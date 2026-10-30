"""현황 카드의 상태 계산. tkinter 와 무관한 순수 로직이라 화면 없이 테스트할 수 있다.

데이터 출처(모두 기존 진행 이벤트):
- controller 가 작업을 시작하면 begin(작업 이름), 진행 문자열이 오면 message(), ProgressInfo 가 오면 info() 를 부른다.
- ProgressInfo 의 phase/파일 수/바이트/속도/남은 시간/현재 경로/분류별 개수/새 문제 항목이 카드의 각 칸이 된다.
- 작업이 끝나면 finish(counts, problems) 로 결과의 확정된 개수와 문제 목록을 넣는다(진행 이벤트보다 정확한 값).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..progress import KINDS, OK, ProgressInfo, fmt_bytes, fmt_duration
from . import texts as T

IDLE, RUNNING, DONE, CANCELLED, FAILED = "idle", "running", "done", "cancelled", "failed"
STEP_TODO, STEP_CURRENT, STEP_DONE = "todo", "current", "done"
# 작업 이름 -> {진행 이벤트(ProgressInfo.phase) -> 몇 번째 단계}. 단계 이름은 texts.TASK_STEPS.
PHASE_MAP = {
    T.TASK_SCAN: {"list": 0, "match": 1},
    T.TASK_DISCOVER: {"list": 0, "find": 1, "scan": 2},
    T.TASK_CHECKLIST: {"list": 0, "match": 1},
    T.TASK_INSTALL_CHECK: {"list": 0, "match": 1},
    T.TASK_EXPORT: {"find": 0, "pack": 1, "check": 2},
    T.TASK_APPLY: {"restore": 1},
    T.TASK_TRANSFER: {"scan": 0, "copy": 1, "verify": 2, "manifest": 2},
    T.TASK_TRANSFER_PREVIEW: {"scan": 0, "compare": 1},
    T.TASK_VERIFY: {"verify": 0},
    T.TASK_VERIFY_AGAIN: {"scan": 0, "verify": 1},
    T.TASK_RETRY: {"scan": 0, "verify": 1, "copy": 1},
}
PHASE_MAP[T.TASK_PLACE_PREVIEW] = {"move": 1, "copy": 1}  # stage50: 받은 파일 놓기(place) 의 진행 단계 이름은 move/copy
PHASE_MAP[T.TASK_PLACE] = {"move": 1, "copy": 1}
PHASE_MAP[T.TASK_EXPORT_RETRY] = PHASE_MAP[T.TASK_EXPORT]  # 읽지 못한 파일 다시 담기: 같은 단계(찾기/담기/검사)
MAX_KEPT_PER_KIND = 2000  # 분류별로 창에 보관하는 문제 항목 상한(메모리 보호)
CURRENT_SHORT_LIMIT = 70


@dataclass(frozen=True)
class Step:
    label: str
    state: str  # todo | current | done
    no: str  # ①②③…


@dataclass(frozen=True)
class Chip:
    key: str
    label: str
    icon: str
    count: int
    level: str  # ok | info | warn | fail (개수가 0 이 아니면 색과 아이콘으로 강조), 0 이면 muted
    clickable: bool
    tip: str


@dataclass(frozen=True)
class Snapshot:
    state: str
    task: str
    headline: str
    steps: tuple
    metrics: tuple  # ((이름, 값), ...)
    current: str  # 전체 경로(툴팁)
    current_short: str
    chips: tuple
    problem_line: str
    has_problem: bool
    last_message: str
    fraction: float | None  # 0~1, 알 수 없으면 None(막대는 '작업 중' 표시)
    stage_text: str  # 예: '② 복사'
    resumed_line: str = ""  # 예: '저장점에서 이어받음 120개 파일'(이어받지 않았으면 빈 문자열)


def _circled(i: int) -> str:
    return T.CARD_CIRCLED[i] if i < len(T.CARD_CIRCLED) else f"{i + 1}."


class StatusModel:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.reset()

    # ------------------------------------------------------------------ 상태 바꾸기
    def reset(self) -> None:
        self.state = IDLE
        self.task = ""
        self.steps: tuple[str, ...] = ()
        self.phase_map: dict[str, int] = {}
        self.step_index = 0
        self.started = self.ended = 0.0
        self.info_last: ProgressInfo | None = None
        self.resumed = 0
        self.last_message = ""
        self.phase_counts: dict[str, dict[str, int]] = {}
        self.final_counts: dict[str, int] | None = None
        self.summary = ""
        self._problems: dict[str, list[tuple[str, str]]] = {k: [] for k in KINDS}
        self._problem_total: dict[str, int] = {k: 0 for k in KINDS}

    def begin(self, task: str) -> None:
        self.reset()
        self.steps = T.TASK_STEPS.get(task, (task,))
        self.phase_map = PHASE_MAP.get(task, {})
        self.task, self.state = task, RUNNING
        self.started = self.clock()

    def message(self, text: str) -> None:
        if self.state == RUNNING and text:
            self.last_message = text

    def info(self, info: ProgressInfo) -> None:
        if self.state != RUNNING:
            return
        self.info_last = info
        self.resumed = max(self.resumed, info.resumed)
        idx = self.phase_map.get(info.phase)
        if idx is not None:
            self.step_index = max(self.step_index, min(idx, len(self.steps) - 1))
        self.phase_counts[info.phase] = {OK: info.ok, "skipped": info.skipped, "unreadable": info.unreadable,
                                         "failed": info.failed}
        for kind, path in info.problems:
            self._keep(kind, path)
        if info.message:
            self.last_message = info.message

    def _keep(self, kind: str, path: str) -> None:
        self._problem_total[kind] = self._problem_total.get(kind, 0) + 1
        if len(self._problems[kind]) < MAX_KEPT_PER_KIND:
            self._problems[kind].append((kind, path))

    def finish(self, counts: dict[str, int] | None = None, problems: dict[str, list[str]] | None = None,
               summary: str = "") -> None:
        """정상 종료. counts/problems 는 결과에서 확정된 값(있으면 진행 이벤트 값보다 우선)."""
        if self.state != RUNNING:
            return
        self.state, self.ended = DONE, self.clock()
        self.step_index = len(self.steps)
        self.summary = summary
        if counts is not None:
            self.final_counts = {k: int(counts.get(k, 0)) for k in KINDS}
        if problems is not None:
            self._problems = {k: [] for k in KINDS}
            self._problem_total = {k: 0 for k in KINDS}
            for kind, paths in problems.items():
                for path in paths:
                    self._keep(kind, path)

    def cancelled(self) -> None:
        if self.state == RUNNING:
            self.state, self.ended = CANCELLED, self.clock()

    def failed(self, message: str = "") -> None:
        if self.state == RUNNING:
            self.state, self.ended = FAILED, self.clock()
            if message:
                self.last_message = message

    # ------------------------------------------------------------------ 읽기
    @property
    def active(self) -> bool:
        return self.state != IDLE

    @property
    def running(self) -> bool:
        return self.state == RUNNING

    def counts(self) -> dict[str, int]:
        if self.final_counts is not None:
            return dict(self.final_counts)
        out = {k: 0 for k in KINDS}
        if not self.phase_counts:
            return out
        for counts in self.phase_counts.values():
            for k in KINDS:
                if k != OK:
                    out[k] += counts.get(k, 0)
        out[OK] = self.phase_counts[self.info_last.phase if self.info_last else next(reversed(self.phase_counts))].get(OK, 0)
        return out

    def problems(self, kind: str) -> list[str]:
        return [path for _k, path in self._problems.get(kind, [])]

    def problem_overflow(self, kind: str) -> int:
        """창에 보관하지 못한 항목 수(개수 > 보관 상한)."""
        return max(0, max(self.counts().get(kind, 0), self._problem_total.get(kind, 0)) - len(self._problems.get(kind, [])))

    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return (self.ended or self.clock()) - self.started

    def _steps_view(self) -> tuple[Step, ...]:
        labels = (*self.steps, T.CARD_STEP_DONE)
        out = []
        for i, label in enumerate(labels):
            if self.state == DONE or i < self.step_index:
                st = STEP_DONE
            elif i == self.step_index and self.state in (RUNNING, FAILED, CANCELLED):
                st = STEP_CURRENT if self.state == RUNNING else STEP_TODO
            else:
                st = STEP_TODO
            out.append(Step(label, st, _circled(i)))
        return tuple(out)

    def _headline(self) -> str:
        if self.state == IDLE:
            return T.CARD_IDLE
        if self.state == CANCELLED:
            return T.CARD_HEAD_CANCELLED
        if self.state == FAILED:
            return T.CARD_HEAD_FAILED
        if self.state == DONE:
            head = T.CARD_HEAD_DONE.format(no=_circled(len(self.steps)))
            return head + (T.CARD_HEAD_DONE_SUMMARY.format(summary=self.summary) if self.summary else "")
        label = self.steps[min(self.step_index, len(self.steps) - 1)]
        head = T.CARD_HEAD_RUNNING.format(no=_circled(self.step_index), step=label)
        i = self.info_last
        if i is None:
            return head + T.CARD_NO_NUMBERS
        if i.total_files:
            pct = int(min(1.0, i.done_files / i.total_files) * 100)
            return head + T.CARD_HEAD_FILES.format(done=i.done_files, total=i.total_files, pct=pct)
        if i.done_files:
            return head + T.CARD_HEAD_COUNT.format(done=i.done_files)
        return head + T.CARD_NO_NUMBERS

    def _metrics(self) -> tuple:
        i = self.info_last
        none = T.CARD_METRIC_NONE
        files = bytes_ = speed = none
        eta = none
        if i is not None:
            if i.total_files:
                files = T.CARD_METRIC_FILES_VALUE.format(done=i.done_files, total=i.total_files)
            elif i.done_files:
                files = T.CARD_METRIC_FILES_NOTOTAL.format(done=i.done_files)
            if i.total_bytes:
                bytes_ = f"{fmt_bytes(i.done_bytes)} / {fmt_bytes(i.total_bytes)}"
            if i.speed_bps > 0:
                speed = f"{fmt_bytes(i.speed_bps)}/s"
        if self.state == RUNNING and i is not None and i.total_bytes:
            eta = (T.CARD_ETA_ESTIMATE.format(eta=fmt_duration(i.eta_seconds)) if i.eta_seconds is not None
                   else T.CARD_ETA_COMPUTING)
        elif self.state == DONE:
            eta = fmt_duration(0)
        elif self.state == RUNNING:
            eta = T.CARD_ETA_COMPUTING
        return ((T.CARD_METRIC_FILES, files), (T.CARD_METRIC_BYTES, bytes_), (T.CARD_METRIC_SPEED, speed),
                (T.CARD_METRIC_ELAPSED, fmt_duration(self.elapsed()) if self.started else none),
                (T.CARD_METRIC_ETA, eta))

    def _chips(self) -> tuple[Chip, ...]:
        counts = self.counts()
        out = []
        for kind in KINDS:
            n = counts[kind]
            label = T.CARD_CHIP_LABELS[kind]
            if kind == OK:
                level, tip, clickable = ("ok" if n else "muted"), T.CARD_CHIP_TIP_OK, False
            else:
                level = ("fail" if kind == "failed" else "warn" if kind == "unreadable" else "info") if n else "muted"
                clickable = n > 0
                tip = T.CARD_CHIP_TIP_OPEN.format(label=label) if n else T.CARD_CHIP_TIP_NONE.format(label=label)
            out.append(Chip(kind, label, T.CARD_CHIP_ICONS[kind], n, level, clickable, tip))
        return tuple(out)

    def _problem_line(self, chips: tuple) -> tuple[str, bool]:
        parts = [T.CARD_PROBLEM_PART.format(label=c.label, n=c.count) for c in chips if c.key in ("unreadable", "failed") and c.count]
        if not parts:
            return T.CARD_PROBLEM_NONE, False
        icon = T.ICON_FAIL if any(c.key == "failed" and c.count for c in chips) else T.ICON_WARN
        return T.CARD_PROBLEM_SOME.format(icon=icon, parts=", ".join(parts)), True

    def _fraction(self) -> float | None:
        if self.state == DONE:
            return 1.0
        i = self.info_last
        if self.state != RUNNING or i is None:
            return None
        if i.total_bytes and i.done_bytes:
            return min(1.0, i.fraction)
        if i.total_files:
            return min(1.0, i.done_files / i.total_files)
        return None

    def snapshot(self) -> Snapshot:
        chips = self._chips()
        problem, has_problem = self._problem_line(chips)
        current = (self.info_last.current if self.info_last else "") if self.state == RUNNING else ""
        stage = ""
        if self.state == RUNNING and self.steps:
            stage = f"{_circled(self.step_index)} {self.steps[min(self.step_index, len(self.steps) - 1)]}"
        return Snapshot(self.state, self.task, self._headline(), self._steps_view() if self.state != IDLE else (),
                        self._metrics() if self.state != IDLE else (), current, T.ellipsize_path(current, CURRENT_SHORT_LIMIT) if current else T.CARD_CURRENT_NONE,
                        chips, problem, has_problem, self.last_message or T.CARD_LAST_NONE, self._fraction(), stage,
                        T.CARD_RESUMED.format(n=self.resumed) if self.resumed else "")


def list_dialog_text(model: StatusModel, kind: str) -> tuple[str, str]:
    """칩을 눌렀을 때 보여 줄 목록 창의 (제목, 본문)."""
    label = T.CARD_CHIP_LABELS[kind]
    paths = model.problems(kind)
    lines: list[str] = []
    if kind == "unreadable":
        lines += [T.CARD_LIST_UNREADABLE_NOTE, ""]
    lines += paths or [T.CARD_LIST_NONE]
    over = model.problem_overflow(kind) if paths else 0
    if over:
        lines += ["", T.CARD_LIST_MORE.format(n=over)]
    return T.CARD_LIST_TITLE.format(label=label), "\n".join(lines)
