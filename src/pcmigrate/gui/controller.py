"""GUI 컨트롤러: api 함수 호출, 스레드 관리, 안전 가드. tkinter 에 의존하지 않는다(가짜 api 로 테스트 가능).

안전 원칙(CLI 와 같은 보수적 기본값):
- 원본은 읽기 전용, 쓰기 전에는 반드시 미리보기와 확인.
- 규칙 없는 프로그램은 사용자가 고른 것만 포함(자동 포함 없음). 민감한 정보 가능성 항목은 명시 동의 후에만 선택.
- 가드 위반은 GuardError 로 막는다(뷰가 버튼을 막아 두더라도 컨트롤러가 한 번 더 막는다).
- 로그에는 설치 프로그램 목록/경로를 남기지 않고 이벤트 종류와 개수만 남긴다.
화면에 보이는 문구는 texts.py 에 있다.
"""
from __future__ import annotations

import errno
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import api as real_api
from ..cancel import CancelToken
from ..checklist import write_output
from ..discover import fmt_size, items_from_selection
from ..manifest import DEFAULT_NAME as MANIFEST_NAME
from ..manifest import ManifestError
from ..progress import ProgressInfo, fmt_bytes, format_info
from ..rules import default_rules_dir
from ..transfer import DriveDisconnected, TransferError
from . import state as S
from . import texts as T
from .runner import Runner


class GuardError(Exception):
    """안전 가드 위반(예: 미리보기 없이 복원, 동의 없이 민감 항목 선택). 메시지는 사용자에게 그대로 보여 준다."""


@dataclass
class FriendlyError:
    title: str
    what: str  # 무엇이 잘못됐는지
    how: str  # 어떻게 하면 되는지
    detail: str = ""  # traceback(접힌 상세 보기에만)

    @property
    def message(self) -> str:
        return f"{self.what}\n{self.how}"


_WIN_PATH_MISSING = {2, 3, 15}
_WIN_PERMISSION = {5}
_WIN_DISK_FULL = {112, 39}
_WIN_DISCONNECTED = {21, 53, 55, 59, 64, 67, 121, 1167, 1231}
_WIN_IN_USE = {32, 33}


def _os_error_texts(exc: OSError) -> tuple[str, str]:
    path = exc.filename if isinstance(getattr(exc, "filename", None), str) else ""
    suffix = f": {path}" if path else ""
    win = getattr(exc, "winerror", None)
    code = exc.errno
    if win in _WIN_IN_USE:
        what, how = T.ERR_IN_USE
    elif isinstance(exc, PermissionError) or win in _WIN_PERMISSION or code in (errno.EACCES, errno.EPERM):
        what, how = T.ERR_PERMISSION
    elif win in _WIN_DISK_FULL or code == errno.ENOSPC:
        what, how = T.ERR_DISK_FULL
    elif win in _WIN_DISCONNECTED or code in (errno.ENODEV, errno.ENXIO, errno.EIO):
        what, how = T.ERR_DISCONNECTED
    elif isinstance(exc, FileNotFoundError) or win in _WIN_PATH_MISSING or code == errno.ENOENT:
        what, how = T.ERR_PATH_MISSING
    else:
        what, how = T.ERR_OS_GENERIC
    return what.format(path=suffix), how


def _api_how(message: str) -> str:
    if "--allow-secrets" in message or "비밀 정보" in message:
        return T.ERR_API_SECRET
    if message.startswith("규칙 오류"):
        return T.ERR_API_RULES
    if message.startswith("패키지 검증 실패") or "해시" in message:
        return T.ERR_API_PACKAGE
    if message.startswith("선택 파일 오류"):
        return T.ERR_API_SELECTION
    return T.ERR_API_HOW


def friendly_error(exc: BaseException, tb: str = "") -> FriendlyError:
    """예외를 '무엇이 잘못됐는지 / 어떻게 하면 되는지' 두 줄로. traceback 은 detail 에만 둔다."""
    if isinstance(exc, real_api.ApiError):
        what = T.friendly_detail("\n".join([*exc.lines, exc.message]))
        return FriendlyError(T.ERR_TITLES["api"], what, _api_how(exc.message), tb)
    if isinstance(exc, DriveDisconnected):
        return FriendlyError(T.TR_DISCONNECTED_TITLE, T.TR_DISCONNECTED, T.TR_RECONNECT_NOTE, tb)
    if isinstance(exc, ManifestError):
        return FriendlyError(T.ERR_TITLES["api"], T.friendly_detail(str(exc)), T.ERR_API_HOW, tb)
    if isinstance(exc, TransferError):
        what, how = T.ERR_TRANSFER
        return FriendlyError(T.ERR_TITLES["transfer"], what.format(msg=T.friendly_detail(str(exc))), how, tb)
    if isinstance(exc, OSError):
        what, how = _os_error_texts(exc)
        return FriendlyError(T.ERR_TITLES["os"], what, how, tb)
    if isinstance(exc, GuardError):
        return FriendlyError(T.ERR_TITLES["guard"], str(exc), "", tb)
    what, how = T.ERR_UNEXPECTED
    return FriendlyError(T.ERR_TITLES["unexpected"], what.format(name=type(exc).__name__, msg=exc), how, tb)


@dataclass
class CandRow:
    key: tuple
    programs: str
    path: str
    kind: str
    size: str
    grade: str
    reason: str
    weak: bool
    secret: list = field(default_factory=list)
    selectable: bool = True


@dataclass
class PathCheck:
    ok: bool
    lines: list  # [(ok, 문장)]
    info: list = field(default_factory=list)  # 대상 드라이브 종류/파일 시스템/여유 공간 안내(문장)
    warnings: list = field(default_factory=list)  # FAT32/exFAT/네트워크 경고(문장)


class Controller:
    """화면과 api 사이의 중간층. 이벤트는 listener(kind, payload) 로 전달된다."""

    def __init__(self, api_mod: Any = None, deps: Any = None, runner: Runner | None = None,
                 rules_dir: str | Path | None = None, logger: logging.Logger | None = None,
                 make_items: Callable | None = None) -> None:
        self.api = api_mod or real_api
        self.deps = deps
        self.runner = runner or Runner()
        self.state = S.WizardState(rules_dir=str(rules_dir or default_rules_dir()))
        self.log = logger or logging.getLogger("pcmigrate.gui")
        self.make_items = make_items or items_from_selection
        self.listener: Callable[[str, Any], None] | None = None
        self.cancel_token: CancelToken | None = None
        self.can_cancel = False
        self.checklist_marks: set[tuple[str, str]] = set()  # 체크한 항목(탭, 문구): 화면을 오가도 유지
        self._dest_cache: dict[str, Any] = {}
        self.roadmap_marks: set[str] = set()  # 전체 이전 순서에서 체크한 항목(창을 닫았다 열어도 유지)

    # ------------------------------------------------------------------ 이벤트/실행
    def emit(self, kind: str, payload: Any = None) -> None:
        self.log.info("event %s", kind)  # 내용(목록/경로)은 로그에 남기지 않는다
        if self.listener:
            self.listener(kind, payload)

    def pump(self) -> int:
        return self.runner.pump()

    def go(self, screen: str) -> None:
        self.state.screen = screen
        self.emit("screen", screen)

    def system_info(self):
        return self.api.system_info()

    def _run(self, label: str, fn: Callable[[Callable[[str], None], Any], Any], done: Callable[[Any], None],
             cancellable: bool = False) -> None:
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        token = CancelToken() if cancellable else None
        self.cancel_token, self.can_cancel = token, cancellable
        self.state.busy, self.state.busy_label = True, label
        self.log.info("task start: %s", label)
        self.emit("busy", label)
        if label in T.PROGRESS:
            self.emit("progress", T.PROGRESS[label])  # 작업 이름과 예상 시간을 바로 보여 준다

        def finish() -> None:
            self.state.busy, self.state.busy_label = False, ""
            self.cancel_token, self.can_cancel = None, False

        def on_done(res: Any) -> None:
            finish()
            self.emit("busy", None)
            done(res)

        def on_error(exc: BaseException, tb: str) -> None:
            finish()
            self.log.error("task failed: %s (%s)", label, type(exc).__name__)
            self.emit("busy", None)
            self.emit("error", friendly_error(exc, tb))

        def on_cancel() -> None:
            finish()
            self.log.info("task cancelled: %s", label)
            self.emit("busy", None)
            self.emit("cancelled", label)

        def on_progress(m: Any) -> None:
            if isinstance(m, ProgressInfo):  # 바이트/속도/남은 시간(진행 막대용)
                self.state.transfer_progress = m
                self.emit("progress_info", m)
                text = T.friendly_detail(format_info(m))
                self.emit("progress", text + (f"\n{T.ICON_WARN} {m.hint}" if m.hint else ""))
            else:
                self.emit("progress", T.friendly_detail(m))

        self.runner.submit(lambda progress: fn(progress, token), on_done=on_done, on_error=on_error,
                           on_cancel=on_cancel, on_progress=on_progress)

    def toggle_roadmap(self, key: str) -> bool:
        """전체 이전 순서의 항목 체크를 뒤집고 새 상태를 돌려준다."""
        valid = {k for k, *_ in (*T.ROADMAP_OLD, *T.ROADMAP_NEW)}
        if key not in valid:
            raise GuardError(T.G_NOT_IN_LIST)
        if key in self.roadmap_marks:
            self.roadmap_marks.discard(key)
            return False
        self.roadmap_marks.add(key)
        return True

    def cancel(self) -> bool:
        """취소 가능한 작업(규칙에 없는 프로그램 찾기, 대용량 복사)에만 효과가 있다."""
        if self.cancel_token is not None:
            self.cancel_token.cancel()
            self.emit("progress", T.CANCELLING)
            return True
        return False

    # ------------------------------------------------------------------ 구 PC 1) 조사
    def start_scan(self) -> None:
        def work(progress, token):
            return self.api.run_scan(self.state.rules_dir, self.deps)

        def done(res):
            self.state.scan = res
            self.emit("scan_done", res)
        self._run(T.TASK_SCAN, work, done)

    # ------------------------------------------------------------------ 구 PC 2) 규칙에 없는 프로그램
    def start_discover(self, min_size_kb: int = 0) -> None:
        def work(progress, token):
            return self.api.run_discover(self.state.rules_dir, min_size_kb=min_size_kb, progress=progress,
                                         deps=self.deps, cancel=token)

        def done(res):
            self.state.discovery = res
            self.state.selected = set()  # 자동 선택 없음
            self.state.secrets_ack = False
            self.emit("discover_done", res)
        self._run(T.TASK_DISCOVER, work, done, cancellable=True)

    def candidate_rows(self) -> list[CandRow]:
        d = self.state.discovery
        if d is None:
            return []
        rows = []
        for c in d.candidates():
            link = c.link
            rows.append(CandRow(
                key=c.key, programs=", ".join(link.programs) if link else T.NO_PROGRAM, path=c.path,
                kind=T.KIND_FOLDER if c.is_dir else T.KIND_FILE, size=fmt_size(c), grade=link.grade if link else "없음",
                reason=link.reason if link else "", weak=bool(link and link.weak), secret=list(c.secret),
                selectable=(not c.secret) or self.state.secrets_ack))
        return rows

    def _row(self, key: tuple) -> CandRow:
        for r in self.candidate_rows():
            if r.key == key:
                return r
        raise GuardError(T.G_NOT_IN_LIST)

    def set_secrets_ack(self, on: bool) -> None:
        self.state.secrets_ack = bool(on)
        if not on:  # 동의를 거두면 민감한 정보 가능성 항목은 모두 선택 해제
            for r in self.candidate_rows():
                if r.secret:
                    self.state.selected.discard(r.key)
        self.emit("selection_changed")

    def set_selected(self, key: tuple, on: bool) -> None:
        row = self._row(key)
        if on and row.secret and not self.state.secrets_ack:
            raise GuardError(T.G_SECRET_SELECT)
        (self.state.selected.add if on else self.state.selected.discard)(key)
        self.emit("selection_changed")

    def selected_rows(self) -> list[CandRow]:
        return [r for r in self.candidate_rows() if r.key in self.state.selected]

    # ------------------------------------------------------------------ 구 PC 3) 해야 할 일 목록
    def start_checklist(self) -> None:
        """항상 '모두 보기' 로 만들고, 화면에서 관련 없는 경고를 숨기거나 보인다(조사를 다시 하지 않는다)."""
        def work(progress, token):
            return self.api.run_checklist(self.state.rules_dir, show_all=True, fmt="md", deps=self.deps)

        def done(res):
            self.state.checklist = res
            self.checklist_marks = set()
            self.emit("checklist_done", res)
        self._run(T.TASK_CHECKLIST, work, done)

    def checklist_hidden_count(self) -> int:
        r = self.state.checklist
        if r is None:
            return 0
        cl = r.checklist
        return sum(1 for line in [*cl.login, *cl.data] if line.endswith(T.CL_HIDDEN_MARK))

    def checklist_tabs(self, show_all: bool = False, query: str = "") -> dict[str, list[str]]:
        r = self.state.checklist
        if r is None:
            return {}
        cl = r.checklist

        def visible(lines: list[str]) -> list[str]:
            out = [l for l in lines if show_all or not l.endswith(T.CL_HIDDEN_MARK)]
            q = query.strip().lower()
            return [l for l in out if q in l.lower()] if q else out

        hidden = 0 if show_all else self.checklist_hidden_count()
        summary = [
            T.CL_SUMMARY_INSTALL.format(a=len(cl.install), b=len(cl.winget_cmd), c=len(cl.winget_auto)),
            T.CL_SUMMARY_LOGIN.format(n=len(visible(cl.login))), T.CL_SUMMARY_DATA.format(n=len(visible(cl.data))),
            T.CL_SUMMARY_REF.format(n=len(cl.reference))]
        if hidden:
            summary.append(T.CL_SUMMARY_OMITTED.format(n=hidden))
        if cl.discovery is not None:
            n, m = cl.discovery
            summary.append(T.CL_SUMMARY_DISC.format(n=n, m=T.CL_SUMMARY_DISC_M.format(m=m) if m is not None else ""))
        summary.append(T.CL_SUMMARY_LICENSE)
        tabs = {T.CL_TABS[0]: summary,
                T.CL_TABS[1]: [*T.CL_GUIDE, *visible(cl.winget_cmd), *visible(cl.install)],
                T.CL_TABS[2]: visible(cl.login), T.CL_TABS[3]: visible(cl.data), T.CL_TABS[4]: visible(cl.reference)}
        return tabs

    CHECKLIST_PLAIN = {T.CL_TABS[0]: "all", T.CL_TABS[4]: "all", T.CL_TABS[1]: len(T.CL_GUIDE)}  # 체크박스 없는 줄 수

    def checklist_items(self, tab: str, show_all: bool = False, query: str = "") -> list[tuple[str, bool, bool]]:
        """(문구, 체크박스가 있는 항목인지, 체크됨) 목록."""
        lines = self.checklist_tabs(show_all, query).get(tab, [])
        plain = self.CHECKLIST_PLAIN.get(tab, 0)
        out = []
        for i, line in enumerate(lines):
            has_box = not (plain == "all" or (isinstance(plain, int) and i < plain))
            out.append((line, has_box, has_box and (tab, line) in self.checklist_marks))
        return out

    def toggle_check(self, tab: str, text: str) -> bool:
        key = (tab, text)
        if key in self.checklist_marks:
            self.checklist_marks.discard(key)
        else:
            self.checklist_marks.add(key)
        self.emit("checklist_marks")
        return key in self.checklist_marks

    def checklist_progress(self, show_all: bool = False) -> tuple[int, int]:
        total = done = 0
        for tab in T.CL_TABS:
            for _text, has_box, checked in self.checklist_items(tab, show_all):
                total += has_box
                done += checked
        return done, total

    def checklist_text(self, fmt: str = "md", show_all: bool = False) -> str:
        """저장용 텍스트. 체크한 항목은 [x] 로 남는다."""
        if self.state.checklist is None:
            raise GuardError(T.G_NEED_CHECKLIST_FIRST)
        md = fmt == "md"
        lines = ["# PC 이전 체크리스트" if md else "PC 이전 체크리스트", ""]
        for tab in T.CL_TABS:
            lines.append(f"## {tab}" if md else f"=== {tab} ===")
            items = self.checklist_items(tab, show_all)
            for text, has_box, checked in items or [(T.CL_EMPTY, False, False)]:
                lines.append(f"- [{'x' if checked else ' '}] {text}" if has_box else text)
            lines.append("")
        lines.append(T.CL_SUMMARY_LICENSE)
        return "\n".join(lines) + "\n"

    def save_text(self, path: str, text: str) -> str:
        """텍스트 저장(기존 파일은 .bak 보존). 호출자는 저장 위치 확인 대화상자를 먼저 보여 줘야 한다."""
        if not path:
            raise GuardError(T.G_NEED_SAVE_PATH)
        try:
            write_output(Path(path), text)
        except OSError as e:
            raise real_api.ApiError("\n".join(real_api.format_write_error(path, e))) from e
        self.log.info("saved text file")
        return path

    # ------------------------------------------------------------------ 구 PC 4) 설정 패키지 만들기
    def export_reason(self, output: str) -> str:
        """패키지 만들기 버튼이 막혀 있는 이유(없으면 빈 문자열)."""
        if self.state.busy:
            return T.WHY_BUSY
        return "" if output.strip() else T.WHY_NEED_OUTPUT

    def start_export(self, output: str, write_confirmed: bool = False) -> None:
        if not write_confirmed:
            raise GuardError(T.G_NEED_EXPORT_CONFIRM)
        if not output:
            raise GuardError(T.G_NEED_SAVE_PATH)
        sel = self.selected_rows()
        has_secret = any(r.secret for r in sel)
        if has_secret and not self.state.secrets_ack:
            raise GuardError(T.G_SECRET_NO_ACK)
        items = self.make_items({"format": 1, "candidates": [
            {"include": True, "root": k[0], "rel": k[1]} for k in sorted(self.state.selected)]}) if sel else []

        def work(progress, token):
            return self.api.run_export(self.state.rules_dir, output, extra_items=items or None,
                                       allow_secrets=has_secret, deps=self.deps)

        def done(res):
            self.state.export_result, self.state.export_output = res, output
            self.emit("export_done", res)
        self._run(T.TASK_EXPORT, work, done)

    # ------------------------------------------------------------------ 신 PC
    def set_package(self, path: str) -> None:
        self.state.package = path
        self._reset_confirms()
        self.state.preview = None
        self.emit("package_changed", path)

    def _reset_confirms(self) -> None:
        s = self.state
        s.preview_checked = s.final_confirmed = s.extra_consent = False

    def start_preview(self) -> None:
        if not self.state.package:
            raise GuardError(T.G_NEED_PACKAGE)
        package = self.state.package

        def work(progress, token):
            return self.api.run_import(package, self.state.rules_dir, dry_run=True, deps=self.deps)

        def done(res):
            self.state.preview, self.state.preview_package = res, package
            self._reset_confirms()
            self.emit("preview_done", res)
        self._run(T.TASK_PREVIEW, work, done)

    def preview_rows(self) -> list[dict]:
        p = self.state.preview
        if p is None:
            return []
        return [{"status": T.PREV_STATUS.get(it["status"], it["status"]), "target": it["target"],
                 "backup": it.get("backup", ""), "extra": bool(it.get("extra")), "size": it.get("size", 0)}
                for it in p.plan.items]

    def preview_problems(self) -> list[str]:
        p = self.state.preview
        return [T.friendly_detail(x) for x in p.plan.problems] if p else []

    def set_preview_checked(self, on: bool) -> None:
        self.state.preview_checked = bool(on)
        if not on:
            self.state.final_confirmed = False
        self.emit("confirm_changed")

    def set_extra_consent(self, on: bool) -> None:
        self.state.extra_consent = bool(on)
        self.emit("confirm_changed")

    def confirm_final(self, yes: bool) -> None:
        """확인 대화상자에서 '예'를 눌렀을 때만 True 로 호출한다."""
        self.state.final_confirmed = bool(yes) and self.state.preview_checked
        self.emit("confirm_changed")

    def can_apply(self) -> tuple[bool, str]:
        s, p = self.state, self.state.preview
        if p is None or s.preview_package != s.package:
            return False, T.G_NEED_PREVIEW
        if p.mode == "blocked" or p.plan.problems:
            return False, T.G_PACKAGE_BLOCKED
        if not s.preview_checked:
            return False, T.G_NEED_CHECK_PREVIEW
        if p.n_extra and not s.extra_consent:
            return False, T.G_NEED_EXTRA_CONSENT
        if not s.final_confirmed:
            return False, T.G_NEED_FINAL_YES
        return True, ""

    def apply_reason(self) -> str:
        """복원 실행 버튼(확인 창 전)이 막혀 있는 이유. 마지막 '확인 창' 단계는 이유로 치지 않는다."""
        ok, why = self.can_apply()
        return "" if ok or why == T.G_NEED_FINAL_YES else why

    def start_apply(self) -> None:
        ok, why = self.can_apply()
        if not ok:
            raise GuardError(why)
        package, allow_extra = self.state.package, self.state.extra_consent

        def work(progress, token):
            return self.api.run_import(package, self.state.rules_dir, yes=True, allow_extra=allow_extra,
                                       deps=self.deps)

        def done(res):
            self.state.import_result = res
            self._reset_confirms()  # 확정은 한 번만 유효
            self.emit("import_done", res)
        self._run(T.TASK_APPLY, work, done)

    def import_summary(self) -> dict:
        r = self.state.import_result
        if r is None or r.report is None:
            return {}
        rep = r.report
        counts = rep.to_dict()["summary"]
        return {"counts": counts, "failures": [(f.dst, T.friendly_detail(f.error)) for f in rep.failures],
                "backups": [x.backup for x in rep.results if x.backup],
                "warnings": [(w.get("name", ""), i) for w in r.plan.warnings for i in w.get("items", [])]}

    def import_counts(self) -> tuple[int, int, int]:
        """(성공, 건너뜀, 실패)"""
        c = self.import_summary().get("counts", {})
        return c.get("copied", 0), c.get("skipped_identical", 0), c.get("failed", 0)

    # ------------------------------------------------------------------ 대용량 복사: 방식/단계
    def set_transfer_flow(self, mode: str, stage: int = 1) -> None:
        """'어떻게 옮기시나요?' 선택(외장 / 랜선 / 같은 PC)과 단계. 바뀌면 이전 미리보기와 결과는 무효가 된다."""
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        if mode not in (T.MODE_EXTERNAL, T.MODE_NETWORK, T.MODE_LOCAL):
            raise GuardError(T.G_NOT_IN_LIST)
        stage = 1 if mode == T.MODE_LOCAL or stage not in (1, 2) else stage
        s = self.state
        if (s.transfer_mode, s.transfer_stage) != (mode, stage):
            s.transfer_mode, s.transfer_stage = mode, stage
            s.transfer_report, s.transfer_preview_key, s.transfer_blocked = None, None, ""
            s.transfer_was_dry_run = True
        self.emit("transfer_flow", (mode, stage))

    def flow(self) -> tuple[str, int]:
        return self.state.transfer_mode, self.state.transfer_stage

    def is_verify_flow(self) -> bool:
        """랜선 2단계: 복사 없이 새 PC 의 복사본을 목록 파일과 비교만 한다."""
        return self.flow() == (T.MODE_NETWORK, 2)

    def flow_labels(self) -> tuple[str, str]:
        return T.TR_FLOW_LABELS.get(self.flow(), T.TR_FLOW_LABELS[(T.MODE_LOCAL, 1)])

    def flow_dst_hint(self) -> str:
        return T.TR_FLOW_DST_HINT.get(self.state.transfer_mode, "")

    def flow_method(self) -> str:
        return T.TR_VERIFY_METHOD.get(self.flow(), "")

    def flow_steps(self) -> tuple[str, ...]:
        return T.TR_FLOW_STEPS.get(self.flow(), ())

    def flow_title(self) -> str:
        return T.TR_FLOW_TITLES.get(self.flow(), T.TR_HEADING)

    def manifest_out_path(self) -> str:
        """1단계(외장/랜선)에서 확인용 목록 파일을 저장할 위치: 대상(외장 드라이브 또는 새 PC 의 공유 폴더) 안."""
        s = self.state
        if s.transfer_mode in (T.MODE_EXTERNAL, T.MODE_NETWORK) and s.transfer_stage == 1 and s.transfer_dst:
            return os.path.join(s.transfer_dst, MANIFEST_NAME)
        return ""

    def stage2_manifest(self) -> str:
        """외장 2단계: 외장 드라이브 폴더 안의 목록 파일. 없으면 빈 문자열(원본을 다시 읽어 검사)."""
        s = self.state
        if (s.transfer_mode, s.transfer_stage) == (T.MODE_EXTERNAL, 2) and s.transfer_src:
            cand = os.path.join(s.transfer_src, MANIFEST_NAME)
            return cand if os.path.isfile(cand) else ""
        return ""

    # ------------------------------------------------------------------ 대용량 복사: 점검
    def describe_dest(self, dst: str):
        """대상 드라이브 종류/파일 시스템/여유 공간(읽기 전용 조회, 같은 경로는 캐시). 네트워크 경로는 느릴 수 있어 조회하지 않는다."""
        fn = getattr(self.api, "destination_info", None)
        if not dst or fn is None:
            return None
        if dst.startswith("\\\\"):
            from ..fs import DestInfo
            return DestInfo(dst, "network")
        if dst not in self._dest_cache:
            try:
                self._dest_cache[dst] = fn(dst)
            except Exception:  # noqa: BLE001 - 조회 실패는 안내만 생략
                self._dest_cache[dst] = None
        return self._dest_cache[dst]

    def forget_dest_cache(self) -> None:
        self._dest_cache.clear()

    def check_transfer_paths(self, src: str, dst: str) -> PathCheck:
        lines: list[tuple[bool, str]] = []
        if not src:
            lines.append((False, T.G_NEED_SOURCE))
        elif not os.path.isdir(src):
            lines.append((False, T.G_SOURCE_MISSING.format(path=src)))
        else:
            lines.append((True, T.G_SOURCE_OK))
        if not dst:
            lines.append((False, T.G_NEED_DEST))
        elif src and os.path.isdir(src):
            rs, rd = os.path.realpath(src), os.path.realpath(dst)
            try:
                inside = os.path.commonpath([rs, rd]) == rs
            except ValueError:  # 다른 드라이브
                inside = False
            lines.append((False, T.G_DEST_INSIDE) if inside else (True, T.G_DEST_OK))
        info, warnings = [], []
        d = self.describe_dest(dst) if dst else None
        if d is not None:
            fs_name = d.fs or (T.TR_FS_UNKNOWN_NOTE if d.kind == "network" else T.TR_FS_UNKNOWN)
            free = fmt_bytes(d.free) if d.free is not None else T.TR_FS_UNKNOWN
            info.append(T.TR_DEST_INFO.format(kind=T.TR_KIND_LABELS.get(d.kind, T.TR_KIND_LABELS["unknown"]), fs=fs_name, free=free))
            if d.fs == "FAT32":
                warnings.append(T.TR_FAT32_WARN)
            elif d.fs == "EXFAT":
                warnings.append(T.TR_EXFAT_WARN)
            if d.kind == "network":
                warnings.append(T.TR_NETWORK_NOTE)
            if d.kind == "cdrom":
                lines.append((False, T.TR_CDROM_BLOCK))
        return PathCheck(all(ok for ok, _ in lines) and bool(src) and bool(dst), lines, info, warnings)

    def check_verify_inputs(self, manifest: str, target: str) -> PathCheck:
        lines: list[tuple[bool, str]] = []
        if not manifest:
            lines.append((False, T.G_NEED_MANIFEST))
        elif not os.path.isfile(manifest):
            lines.append((False, T.G_MANIFEST_MISSING.format(path=manifest)))
        else:
            lines.append((True, T.G_VERIFY_OK))
        if not target:
            lines.append((False, T.G_NEED_TARGET))
        elif not os.path.isdir(target):
            lines.append((False, T.G_TARGET_MISSING.format(path=target)))
        return PathCheck(all(ok for ok, _ in lines) and bool(manifest) and bool(target), lines)

    def transfer_reasons(self) -> tuple[str, str]:
        """(미리보기 버튼이 막힌 이유, 복사 시작 버튼이 막힌 이유). 빈 문자열이면 눌러도 됨."""
        s = self.state
        if s.busy:
            return T.WHY_BUSY, T.WHY_BUSY
        if self.is_verify_flow():
            ok = self.check_verify_inputs(s.verify_manifest, s.verify_target).ok
            return ("", "") if ok else (T.WHY_NEED_MANIFEST, T.WHY_NEED_MANIFEST)
        check = self.check_transfer_paths(s.transfer_src, s.transfer_dst)
        if not check.ok:
            return T.WHY_PATH_PROBLEM, T.WHY_PATH_PROBLEM
        if s.transfer_preview_key != (s.transfer_src, s.transfer_dst):
            return "", T.WHY_PREVIEW_FIRST
        if s.transfer_blocked:
            return "", s.transfer_blocked
        return "", ""

    def can_verify_again(self) -> tuple[bool, str]:
        """'검사만 다시': 복사를 이미 한 번 한 폴더 쌍에서만. 복사는 하지 않고 읽기만 한다."""
        s = self.state
        if s.busy:
            return False, T.WHY_BUSY
        if self.is_verify_flow():
            return False, ""
        if not self.check_transfer_paths(s.transfer_src, s.transfer_dst).ok:
            return False, T.WHY_PATH_PROBLEM
        if not os.path.isdir(s.transfer_dst):
            return False, T.G_NEED_COPY_FIRST
        return True, ""

    def has_retryable(self) -> bool:
        rep = self.state.transfer_report
        return bool(rep is not None and not self.state.transfer_was_dry_run and rep.failures and not self.is_verify_flow())

    # ------------------------------------------------------------------ 대용량 복사: 실행
    def start_transfer(self, dry_run: bool = True, strict: bool = False, include_cloud: bool = False,
                       report_path: str = "", write_confirmed: bool = False, verify_only: bool = False,
                       retry_failed: bool = False, workers: int | None = None) -> None:
        s = self.state
        if self.is_verify_flow():
            raise GuardError(T.G_NOT_IN_LIST)
        check = self.check_transfer_paths(s.transfer_src, s.transfer_dst)
        if not check.ok:
            raise GuardError(" ".join(t for ok, t in check.lines if not ok))
        key = (s.transfer_src, s.transfer_dst)
        if verify_only:
            if not os.path.isdir(s.transfer_dst):
                raise GuardError(T.G_NEED_COPY_FIRST)
        elif not dry_run:
            if not write_confirmed:
                raise GuardError(T.G_TRANSFER_CONFIRM)
            if s.transfer_preview_key != key:
                raise GuardError(T.G_TRANSFER_NEED_PREVIEW)
            if s.transfer_blocked:
                raise GuardError(s.transfer_blocked)
        extra: dict = {}
        if not dry_run and not verify_only:
            out = self.manifest_out_path()
            if out:
                extra["manifest_out"] = out
            use = self.stage2_manifest()
            if use:
                extra["use_manifest"] = use
        if verify_only:
            extra["verify_only"] = True
        if retry_failed:
            extra["retry_failed"] = True
        if workers:
            extra["workers"] = workers

        def work(progress, token):
            return self.api.run_transfer(s.transfer_src, s.transfer_dst, dry_run=dry_run, include_cloud=include_cloud,
                                         strict=strict, progress=progress, cancel=token, deps=self.deps,
                                         warn=progress, progress_info=progress, **extra)

        def done(rep):
            s.transfer_report, s.transfer_was_dry_run = rep, dry_run
            if dry_run:
                s.transfer_preview_key = key
                meta = getattr(rep, "meta", {}) or {}
                s.transfer_blocked = (T.WHY_SPACE if meta.get("space_blocked") else T.WHY_FAT32 if meta.get("fat32_blocked") else "")
            else:
                s.transfer_blocked = ""
            self.emit("transfer_done", rep)
            if report_path:
                try:
                    rep.write(Path(report_path))
                except OSError as e:
                    self.emit("error", friendly_error(e))
        label = (T.TASK_TRANSFER_PREVIEW if dry_run else T.TASK_VERIFY_AGAIN if verify_only
                 else T.TASK_RETRY if retry_failed else T.TASK_TRANSFER)
        self._run(label, work, done, cancellable=True)

    def start_verify(self, report_path: str = "") -> None:
        """랜선 2단계(새 PC): 목록 파일과 로컬 폴더를 비교한다. 폴더는 읽기만 한다(결과 파일도 그 폴더 밖에만 저장)."""
        s = self.state
        check = self.check_verify_inputs(s.verify_manifest, s.verify_target)
        if not check.ok:
            raise GuardError(" ".join(t for ok, t in check.lines if not ok))
        if report_path and self.api.report_inside(report_path, s.verify_target):
            raise GuardError(T.G_REPORT_INSIDE)

        def work(progress, token):
            return self.api.run_verify(s.verify_manifest, s.verify_target, progress=progress, progress_info=progress,
                                       cancel=token, deps=self.deps)

        def done(rep):
            s.transfer_report, s.transfer_was_dry_run = rep, False
            self.emit("transfer_done", rep)
            if report_path:
                try:
                    rep.write(Path(report_path))
                except OSError as e:
                    self.emit("error", friendly_error(e))
        self._run(T.TASK_VERIFY, work, done, cancellable=True)

    # ------------------------------------------------------------------ 대용량 복사: 결과
    def location_warning(self) -> str:
        """복사/검사한 위치가 원래 경로와 다르면 노란 경고 문구(같으면 빈 문자열)."""
        rep, s = self.state.transfer_report, self.state
        if rep is None or s.transfer_was_dry_run:
            return ""
        meta = getattr(rep, "meta", {}) or {}
        if self.is_verify_flow():
            orig, now = meta.get("source_root", ""), meta.get("target", "") or s.verify_target
        elif (s.transfer_mode, s.transfer_stage) == (T.MODE_EXTERNAL, 2):
            orig, now = meta.get("original_root", ""), s.transfer_dst
        else:
            return ""
        if orig and now and os.path.normcase(os.path.normpath(orig)) != os.path.normcase(os.path.normpath(now)):
            return T.LOC_WARNING.format(orig=orig, now=now)
        return ""

    def layout_lines(self) -> list[str]:
        """원본 최상위 구조(절대 경로, 드라이브 포함). 결과 화면에서 xref/데이터 단축 경로 안내와 함께 보여 준다."""
        rep = self.state.transfer_report
        meta = getattr(rep, "meta", {}) or {}
        layout = meta.get("source_layout") or {}
        if not layout:
            return []
        lines = [T.TR_LAYOUT_ROOT.format(root=layout.get("root", ""))]
        for t in layout.get("top_level", [])[:12]:
            name = t["name"] if t["name"] != "(root)" else T.TR_LAYOUT_FILES
            lines.append(T.TR_LAYOUT_LINE.format(name=name, files=t["files"], size=fmt_bytes(t["bytes"])))
        more = layout.get("top_level_count", 0) - min(12, len(layout.get("top_level", [])))
        if more > 0:
            lines.append(T.TR_LAYOUT_MORE.format(n=more))
        return lines

    def flow_notes(self) -> list[str]:
        """결과 화면 아래에 붙는 안내(검사 미룸, 다음 단계, 절전 방지 실패, xref, 원본 보존)."""
        rep, s = self.state.transfer_report, self.state
        if rep is None or s.transfer_was_dry_run:
            return []
        meta = getattr(rep, "meta", {}) or {}
        notes: list[str] = []
        if meta.get("dest_verification") == "deferred":
            notes.append(T.TR_DEFERRED_NOTE)
        if meta.get("keep_awake") == "failed":
            notes.append(T.TR_KEEP_AWAKE_FAILED)
        if s.transfer_mode == T.MODE_EXTERNAL and s.transfer_stage == 1 and not rep.failures:
            notes.append(T.TR_NEXT_STEP_EXT1)
        if s.transfer_mode == T.MODE_NETWORK and s.transfer_stage == 1 and not rep.failures:
            notes.append(T.TR_NEXT_STEP_NET1)
        loc = self.location_warning()
        if loc:
            notes.append(loc)
        notes.append(T.XREF_NOTE)
        notes.append(T.TR_DONE_REMINDER)
        return notes

    def transfer_summary(self) -> dict:
        rep = self.state.transfer_report
        if rep is None:
            return {}
        counts = rep.to_dict()["summary"]
        return {"dry_run": self.state.transfer_was_dry_run, "counts": counts,
                "verified": counts.get("verified", 0), "mismatch": counts.get("mismatch", 0),
                "missing": counts.get("missing", 0), "cloud_only": counts.get("cloud_only", 0),
                "failed": counts.get("failed", 0),
                "failures": [(f.src, T.TR_STATUS_LABELS.get(f.status, f.status), T.friendly_detail(f.error))
                             for f in rep.failures],
                "cloud_files": [r.src for r in rep.results if r.status == "cloud_only"],
                "repaired": [x["file"] for x in rep.meta.get("repaired", [])],
                "pending": counts.get("pending_verify", 0), "added": counts.get("extra", 0),
                "mode": rep.meta.get("mode", ""), "io": rep.meta.get("io") or {},
                "manifest_complete": rep.meta.get("manifest_complete", True)}

    def transfer_counts(self) -> tuple[int, int, int]:
        """(성공, 건너뜀, 실패). 건너뜀은 클라우드 전용 파일."""
        sm = self.transfer_summary()
        if not sm:
            return 0, 0, 0
        return sm["verified"] + sm["pending"], sm["cloud_only"], sm["mismatch"] + sm["missing"] + sm["failed"]

    def save_transfer_report(self, path: str) -> None:
        rep = self.state.transfer_report
        if rep is None:
            raise GuardError(T.G_NOTHING_TO_SAVE)
        try:
            rep.write(Path(path))
        except OSError as e:
            raise real_api.ApiError("\n".join(real_api.format_write_error(path, e))) from e
