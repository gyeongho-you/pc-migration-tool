"""전송 진행률/속도/남은 시간 계산. 시계는 주입할 수 있다(테스트)."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

SLOW_BYTES_PER_SEC = 40 * 1024 * 1024  # 이보다 느린 상태가 오래 이어지면 USB 2.0 포트(또는 느린 링크) 가능성을 안내
SLOW_AFTER_SECONDS = 60.0
SPEED_WINDOW = 30.0

PHASE_LABELS = {"scan": "원본 조사", "copy": "복사", "verify": "검사", "manifest": "목록 만들기", "done": "완료",
                "list": "목록 읽기", "match": "규칙 맞추기", "find": "파일 찾기", "pack": "담기", "check": "검사",
                "restore": "복원", "compare": "비교"}
# 현황 카드의 카운터 분류(GUI 가 색/아이콘으로 구분한다). 기존 CLI 출력에는 쓰이지 않는다.
OK, SKIPPED, UNREADABLE, FAILED_KIND = "ok", "skipped", "unreadable", "failed"
KINDS = (OK, SKIPPED, UNREADABLE, FAILED_KIND)
MAX_PROBLEM_DELTA = 200  # 한 번의 진행 이벤트에 실어 보내는 문제 항목 상한(나머지는 개수만 센다)


def categorize(status: str, reason: str = "") -> str:
    """파일 결과(copier 의 status/reason)를 카운터 분류로 바꾼다. 읽기 실패 중 '사용 중'은 따로 센다."""
    if status in ("copied", "verified", "pending_verify"):
        return OK
    if status in ("skipped_identical", "cloud_only", "would_copy", "would_overwrite", "extra", "denied"):
        return SKIPPED if status in ("skipped_identical", "cloud_only", "denied") else OK
    return UNREADABLE if reason == "in_use" else FAILED_KIND
NETWORK_SLOW_BYTES_PER_SEC = 11 * 1024 * 1024  # 100Mbps 급 링크의 실제 한계(약 11MB/s)
SLOW_HINT = ("속도가 계속 느립니다(40MB/s 미만). 외장 드라이브가 USB 2.0 포트에 연결돼 있을 수 있습니다. "
             "USB 3.x 포트(보통 파란색)에 꽂았는지 확인하세요.")
NETWORK_SLOW_HINT = ("네트워크 속도가 계속 느립니다(11MB/s 미만). 연결 속도가 100Mbps 이하일 수 있습니다. 어댑터 속성에서 링크 속도가 "
                     "1000Mbps(기가비트)인지, 무선이 아닌 유선인지, 케이블이 CAT5e 이상인지 확인하세요.")


def slow_policy(kind: str) -> tuple[float, str]:
    """대상 종류(network/removable/fixed/unknown)에 맞는 (느림 기준 바이트/초, 힌트 문구)."""
    if kind == "network":
        return NETWORK_SLOW_BYTES_PER_SEC, NETWORK_SLOW_HINT
    return SLOW_BYTES_PER_SEC, SLOW_HINT


@dataclass(frozen=True)
class ProgressInfo:
    phase: str  # scan | copy | verify | manifest
    done_bytes: int
    total_bytes: int
    done_files: int
    total_files: int
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    hint: str = ""
    message: str = ""
    # 현황 카드용(기본값이면 CLI 출력은 그대로): 지금 처리 중인 경로, 분류별 개수, 이번 이벤트에서 새로 생긴 문제 항목
    current: str = ""
    ok: int = 0
    skipped: int = 0
    unreadable: int = 0
    failed: int = 0
    problems: tuple = ()  # ((분류, 경로), ...)
    resumed: int = 0  # 내보내기: 저장점에서 이어받아 다시 담지 않은 파일 수

    @property
    def fraction(self) -> float:
        return min(1.0, self.done_bytes / self.total_bytes) if self.total_bytes else 0.0


def fmt_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds < 0:
        return "계산 중"
    s = int(round(seconds))
    if s < 60:
        return f"{s}초"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}분"
    h, m = divmod(m, 60)
    return f"{h}시간 {m}분" if m else f"{h}시간"


def format_info(info: ProgressInfo) -> str:
    """사람이 읽는 한 줄. 예: '복사 12.3GB / 1.0TB (1.2%) · 파일 1,000/9,000개 · 250.0MB/s · 남은 시간 약 1시간 10분'"""
    if info.message and not info.total_bytes:
        return info.message
    label = PHASE_LABELS.get(info.phase, info.phase)
    line = f"{label} {fmt_bytes(info.done_bytes)} / {fmt_bytes(info.total_bytes)} ({info.fraction * 100:.1f}%)"
    if info.total_files:
        line += f" · 파일 {info.done_files:,}/{info.total_files:,}개"
    if info.speed_bps > 0:
        line += f" · {fmt_bytes(info.speed_bps)}/s"
    if info.eta_seconds is not None and info.phase in ("copy", "verify", "manifest"):
        line += f" · 남은 시간 약 {fmt_duration(info.eta_seconds)}"
    return line


class ProgressTracker:
    """처리한 바이트/파일을 누적하고 최근 SPEED_WINDOW 초의 속도로 남은 시간을 추정한다(스레드 안전)."""

    def __init__(self, phase: str, total_bytes: int, total_files: int,
                 clock: Callable[[], float] = time.monotonic, slow_bps: float = SLOW_BYTES_PER_SEC,
                 slow_after: float = SLOW_AFTER_SECONDS, window: float = SPEED_WINDOW,
                 slow_hint: str = SLOW_HINT) -> None:
        self.slow_hint = slow_hint
        self.phase, self.total_bytes, self.total_files = phase, total_bytes, total_files
        self.clock, self.slow_bps, self.slow_after, self.window = clock, slow_bps, slow_after, window
        self.done_bytes = self.done_files = 0
        self._lock = threading.Lock()
        self._start = clock()
        self._samples: deque[tuple[float, int]] = deque([(self._start, 0)])
        self._slow_since: float | None = None
        self.current = ""
        self.resumed = 0
        self.counts = {k: 0 for k in KINDS}
        self._new_problems: list[tuple[str, str]] = []

    def note(self, kind: str, path: str = "") -> None:
        """파일 하나의 결과를 분류별로 센다. 성공이 아닌 것은 경로를 다음 진행 이벤트에 실어 보낸다."""
        with self._lock:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            if kind != OK and path and len(self._new_problems) < MAX_PROBLEM_DELTA:
                self._new_problems.append((kind, path))

    def set_current(self, path: str) -> None:
        self.current = path

    def add(self, nbytes: int = 0, nfiles: int = 0) -> None:
        with self._lock:
            self.done_bytes += nbytes
            self.done_files += nfiles

    def speed(self) -> float:
        """최근 구간의 바이트/초. 구간이 너무 짧으면 시작 이후 평균."""
        with self._lock:
            now, done = self.clock(), self.done_bytes
            self._samples.append((now, done))
            while len(self._samples) > 2 and now - self._samples[0][0] > self.window:
                self._samples.popleft()
            t0, b0 = self._samples[0]
            span = now - t0
            if span < 1.0:
                span, b0 = now - self._start, 0
            return (done - b0) / span if span > 0 else 0.0

    def snapshot(self, message: str = "") -> ProgressInfo:
        speed = self.speed()
        with self._lock:
            done, nfiles = self.done_bytes, self.done_files
            counts, problems = dict(self.counts), tuple(self._new_problems)
            self._new_problems.clear()
        remaining = max(0, self.total_bytes - done)
        eta = (remaining / speed) if speed > 0 else (0.0 if remaining == 0 else None)
        now = self.clock()
        hint = ""
        if self.phase in ("copy", "verify", "manifest") and self.total_bytes and remaining > 0 and done > 0:
            if speed < self.slow_bps:
                if self._slow_since is None:
                    self._slow_since = now
                if now - self._slow_since >= self.slow_after:
                    hint = self.slow_hint
            else:
                self._slow_since = None
        return ProgressInfo(self.phase, done, self.total_bytes, nfiles, self.total_files, speed, eta, hint, message,
                            self.current, counts[OK], counts[SKIPPED], counts[UNREADABLE], counts[FAILED_KIND], problems, self.resumed)
