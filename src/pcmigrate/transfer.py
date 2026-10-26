"""모드 B: 대용량 데이터 전송(robocopy 복사 + SHA-256 검사 + 이어받기).

복사는 Windows 내장 robocopy 로 하고, 끝난 뒤 원본/대상을 읽어 크기 + SHA-256 을 비교한다.
- 원본은 읽기만 한다. 원본을 바꿀 수 있는 옵션(/MIR /PURGE /MOV /MOVE)은 코드로 금지한다.
- 대상 폴더의 .pcmigrate-state.json(줄 단위 JSON)에 파일별 검사 결과를 이어 적어 재실행 시 건너뛴다.
  파일이 수백만 개여도 한 줄씩 덧붙이므로 매번 전체를 다시 쓰지 않는다. 손상/불일치하면 무시하고 전체를 다시 검사한다.
- 검사에서 읽는 횟수(파일 하나당): 복사할 때 원본 1회(robocopy) + 검사 때 원본 1회(해시) + 대상 1회(해시).
  원본 해시는 상태 파일(같은 크기/수정시각) 또는 해시 목록(use_manifest)에 있으면 다시 읽지 않는다. 대상은 항상 다시 읽는다.
- 네트워크(공유 폴더)로 복사할 때는 대상을 네트워크로 다시 읽지 않도록 검사를 미루고(해시 목록 manifest_out),
  새 PC 에서 목록과 비교한다(verify).
- OneDrive 클라우드 전용(온디맨드) 파일은 기본으로 건너뛰고 cloud_only 로 기록한다.
- 전송 중 시스템 절전을 막고(SetThreadExecutionState), 끝나면 반드시 되돌린다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import failure, fs
from .cancel import Cancelled
from .copier import (CLOUD_ONLY, FAILED, MISMATCH, MISSING, PENDING_VERIFY, SKIPPED_SAME, VERIFIED, WOULD_COPY,
                     WOULD_OVERWRITE, FileResult, IoStats, Report, copy_verified, repair_file, sha256_file, verify_file)
from .engine import clamp_workers, run_ordered
from .fs import long_path
from .manifest import ManifestReader, ManifestWriter
from .power import KeepAwake
from .progress import OK, ProgressInfo, ProgressTracker, categorize, fmt_bytes, format_info, slow_policy
from .scan import ScanInfo, is_cloud_only, scan_source_info  # noqa: F401  (is_cloud_only 는 기존 import 경로 유지)

STATE_NAME = ".pcmigrate-state.json"
PRESERVE_NAME = ".pcmigrate-preserve.json"  # 덮어쓰기 전 .bak 보존 기록(이어받기 구분용)
MAX_PRESERVED_LISTED = 200  # 리포트에 이름까지 남기는 보존 파일 수(전체 개수는 따로 센다)
LOG_NAME = ".pcmigrate-robocopy.log"
OWN_PATTERN = ".pcmigrate-*"
STATE_VERSION = 2
STATE_FLUSH_EVERY = 500
STATE_COMPACT_FACTOR = 3
MAX_DETAIL_RESULTS = 50_000  # 이보다 파일이 많으면 정상 파일은 개수만 세고 목록에는 문제 있는 파일만 남긴다(메모리/리포트 크기)
SPACE_MARGIN = 0.05  # 원본 총량의 5% 를 여유로 더 요구
DEFAULT_CHUNK = 4 * 1024 * 1024

# 원본/대상을 삭제·이동·동기화(삭제)할 수 있는 옵션. 접두 일치로 /MOV:, /MOVE 모두 차단.
FORBIDDEN_PREFIXES = ("/MIR", "/PURGE", "/MOV")

# st_file_attributes 비트(기존 이름 유지)
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000

MTIME_TOLERANCE_NS = 2_000_000_000  # FAT 계열 2초 해상도
MAX_XF_CHARS = 6000  # dry-run 목록용 /XF 이름 합계 상한(명령줄 길이 제한 회피)
COPY_MODES = ("restartable", "normal", "unbuffered")
_COPY_LINE = re.compile(r"^\t[^\t]*\t+\s*(\d+)\t(.+?)\s*$")  # robocopy /BYTES /NJH /NJS /NDL 의 파일 줄(언어와 무관)
_ERR_LINE = re.compile(r"(?:ERROR|오류)\s+\d+\s+\(0x", re.I)

# runner(args) -> (returncode, output). 줄 단위 진행을 받는 runner 는 supports_lines=True 이고 runner(args, on_line) 형태.
Runner = Callable[..., tuple[int, str]]
AttrGetter = Callable[[Path], int]


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 창 모드 exe 에서 콘솔 창이 번쩍이지 않게


class TransferError(ValueError):
    """전송을 시작할 수 없는 입력/설정/환경 오류. 메시지는 사용자에게 그대로 보여 줄 수 있다."""


class DriveDisconnected(TransferError):
    """복사/검사 중 대상(또는 원본) 드라이브가 분리되었다. 다시 연결하고 같은 설정으로 실행하면 이어받는다."""


DISCONNECTED_MESSAGE = "드라이브가 분리되었습니다. 다시 연결하면 이어받습니다(같은 폴더/설정으로 다시 실행하세요)."


def default_runner(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        text = proc.stdout.decode("oem", errors="replace")
    except LookupError:
        text = proc.stdout.decode("utf-8", errors="replace")
    return proc.returncode, text


def make_runner(cancel: Callable[[], bool] | None = None, alive: Callable[[], bool] | None = None,
                poll: float = 0.2, alive_every: float = 2.0) -> Runner:
    """robocopy 를 Popen 으로 실행하며 출력 줄을 실시간으로 넘겨준다.

    - cancel 이 True 가 되면 프로세스를 종료하고 Cancelled(/Z 또는 상태 파일 덕분에 다시 실행하면 이어받음).
    - alive 가 False 를 돌려주면(드라이브 분리) 프로세스를 종료하고 DriveDisconnected.
    - 출력은 메모리를 아끼려고 오류 줄과 마지막 줄만 모아 돌려준다.
    """
    base_alive = alive

    def run(args: list[str], on_line: Callable[[str], None] | None = None,
            alive: Callable[[], bool] | None = None) -> tuple[int, str]:
        alive = alive or base_alive
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=NO_WINDOW)
        errors: list[str] = []
        tail: list[str] = []

        def read() -> None:
            for raw in proc.stdout:
                try:
                    line = raw.decode("oem", errors="replace").rstrip("\r\n")
                except LookupError:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if on_line is not None:
                    on_line(line)
                if _ERR_LINE.search(line) and len(errors) < 200:
                    errors.append(line)
                tail.append(line)
                if len(tail) > 200:
                    del tail[:100]

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        last_alive = time.monotonic()
        while proc.poll() is None:
            if cancel is not None and cancel():
                _stop(proc, reader)
                raise Cancelled()
            if alive is not None and time.monotonic() - last_alive >= alive_every:
                last_alive = time.monotonic()
                if not alive():
                    _stop(proc, reader)
                    raise DriveDisconnected(DISCONNECTED_MESSAGE)
            time.sleep(poll)
        reader.join(10)
        return proc.returncode, "\n".join(errors + tail)

    run.supports_lines = True  # type: ignore[attr-defined]
    return run


def _stop(proc: subprocess.Popen, reader: threading.Thread) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    reader.join(1)


def cancellable_runner(cancel: Callable[[], bool], alive: Callable[[], bool] | None = None) -> Runner:
    return make_runner(cancel, alive)


def validate_args(args: list[str]) -> None:
    """원본 보호: 금지 옵션이 하나라도 있으면 실행 전에 거부한다."""
    for a in args[1:]:
        up = a.upper()
        if any(up.startswith(p) for p in FORBIDDEN_PREFIXES):
            raise TransferError(f"금지된 robocopy 옵션: {a}")


DEFAULT_COPY_THREADS = 4  # 작은 파일이 많을 때 /MT:4 가 로컬 SSD 에서 1개일 때보다 2~3배 빨랐다(측정). 느린 HDD/USB 는 1 을 권장


def base_args(src: Path, dst: Path, log: Path | None, dry_run: bool, copy_mode: str = "restartable",
              retries: int = 3, wait: int = 5, threads: int = 1) -> list[str]:
    """robocopy 인자. copy_mode: restartable(/Z, 기본·끊겨도 파일 중간부터 이어받음) / normal(옵션 없음) / unbuffered(/J, 아주 큰 파일에 유리).

    threads 가 2 이상이면 /MT:n(여러 파일을 동시에 복사)을 붙인다. 목록만 보는 미리보기(/L)에는 쓰지 않는다."""
    if copy_mode not in COPY_MODES:
        raise TransferError(f"알 수 없는 복사 방식: {copy_mode}")
    args = ["robocopy", str(src), str(dst), "/E", f"/R:{retries}", f"/W:{wait}",
            "/COPY:DAT", "/DCOPY:DAT", "/NP", "/XJ", "/BYTES", "/NJH", "/NJS", "/NDL"]
    if copy_mode == "restartable":
        args.insert(3, "/Z")
    elif copy_mode == "unbuffered":
        args.insert(3, "/J")
    if threads > 1 and not dry_run:
        args.append(f"/MT:{min(threads, 16)}")
    if dry_run:
        args.append("/L")  # 목록만. 로그 파일도 쓰지 않는다.
    elif log is not None:
        args.append(f"/LOG+:{log}")
    args += ["/XF", OWN_PATTERN]  # 이 도구의 기록 파일(상태/로그/목록)은 옮기지 않는다. 뒤에 파일 이름을 더 이어 붙일 수 있다.
    return args


def interpret_exit(code: int) -> tuple[bool, list[str]]:
    """robocopy 종료코드(비트마스크): 0~7 성공/경고, 8 이상 실패."""
    notes = []
    for bit, text in ((1, "파일 복사됨"), (2, "대상에 추가 파일/폴더 있음(삭제하지 않음)"),
                      (4, "불일치 파일/폴더 있음"), (8, "일부 복사 실패"), (16, "치명적 오류")):
        if code & bit:
            notes.append(text)
    return code < 8, notes


def default_attr_getter(path: Path) -> int:
    return getattr(os.stat(path), "st_file_attributes", 0)


def parse_copy_path(line: str) -> str | None:
    """robocopy 파일 줄에서 경로를 꺼낸다(현황 카드의 '지금 처리 중인 파일' 표시용). 아니면 None."""
    m = _COPY_LINE.match(line)
    return m.group(2) if m else None


def parse_copy_line(line: str) -> int | None:
    """robocopy 파일 줄('\\t라벨\\t\\t크기\\t경로')에서 파일 크기를 꺼낸다. 아니면 None."""
    m = _COPY_LINE.match(line)
    return int(m.group(1)) if m else None


def _is_within(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(child)).relative_to(os.path.realpath(parent))
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- 상태 파일(줄 단위 JSON)

class State:
    """이어받기용 진행 상태. 파일마다 한 줄씩 덧붙이고(크래시에 강함), 가끔 정리해서 다시 쓴다.

    항목: rel -> (size, mtime_ns, sha256(원본 해시), verified, reason). reason 이 'pending' 이면 복사는 끝났고 대상 검사는 미룸.
    첫 줄은 헤더({"state_version":2,"src":...,"dest_path":...,"dest_serial":...}). 마지막 줄이 잘려도 나머지는 쓴다.
    손상/원본 경로 불일치/옛 형식이면 비우고 전체를 다시 검사한다(reset_reason).
    """

    def __init__(self, path: Path, src: Path, dest_path: str = "", dest_serial: int | None = None) -> None:
        self.path = Path(path)
        self.src = str(src)
        self.files: dict[str, tuple[int, int, str, bool, str]] = {}
        self.reset_reason = ""
        self.prev_dest_path = ""
        self.prev_dest_serial: int | None = None
        self.dest_path, self.dest_serial = dest_path, dest_serial
        self._fh = None
        self._appended = 0
        self._since_flush = 0
        self._last_sync = time.monotonic()
        self._rewritten = False
        self._lock = threading.Lock()
        self._load()

    # -- 읽기
    def _reset(self, why: str) -> None:
        self.files = {}
        self.reset_reason = f"상태 파일 무시(전체 재검사): {why}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(long_path(self.path), "r", encoding="utf-8") as f:
                first = f.readline()
                head = json.loads(first)
                if not isinstance(head, dict):
                    raise ValueError("형식 오류")
                if head.get("state_version") != STATE_VERSION or head.get("src") != self.src:
                    raise ValueError("버전/원본 경로 불일치")
                self.prev_dest_path = str(head.get("dest_path") or "")
                self.prev_dest_serial = head.get("dest_serial") if isinstance(head.get("dest_serial"), int) else None
                files: dict[str, tuple] = {}
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        files[e["rel"]] = (int(e["s"]), int(e["m"]), str(e["h"]), bool(e["v"]), str(e.get("r", "")))
                    except (ValueError, KeyError, TypeError):
                        continue  # 마지막 줄이 중간에 끊긴 경우 등: 그 항목만 무시
                self.files = files
        except (OSError, ValueError, KeyError, TypeError) as e:
            self._reset(f"{type(e).__name__}: {e}")

    # -- 조회
    def is_verified(self, rel: str, size: int, mtime_ns: int, dst: str) -> str | None:
        """검사 완료로 건너뛸 수 있으면 원본 해시, 아니면 None. 대상은 크기/수정시각만 가볍게 확인한다."""
        e = self.files.get(rel)
        if not e or not e[3] or e[0] != size or e[1] != mtime_ns:
            return None
        try:
            ds = os.stat(long_path(dst))
        except OSError:
            return None
        if ds.st_size == size and abs(ds.st_mtime_ns - mtime_ns) <= MTIME_TOLERANCE_NS:
            return e[2]
        return None

    def src_hash(self, rel: str, size: int, mtime_ns: int) -> str | None:
        """원본이 그대로(크기/수정시각 동일)이면 이전에 계산해 둔 원본 해시."""
        e = self.files.get(rel)
        return e[2] if e and e[0] == size and e[1] == mtime_ns and e[2] else None

    # -- 쓰기
    def _open(self) -> None:
        if self._fh is not None:
            return
        if not self.path.exists() or (self.reset_reason and not self._rewritten):
            self._write_all()
        self._fh = open(long_path(self.path), "a", encoding="utf-8", newline="\n")

    def _header(self) -> str:
        return json.dumps({"state_version": STATE_VERSION, "src": self.src, "dest_path": self.dest_path,
                           "dest_serial": self.dest_serial, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                          ensure_ascii=False, separators=(",", ":")) + "\n"

    def _write_all(self) -> None:
        tmp = self.path.with_name(self.path.name + ".part")
        with open(long_path(tmp), "w", encoding="utf-8", newline="\n") as f:
            f.write(self._header())
            for rel, (s, m, h, v, r) in self.files.items():
                f.write(self._line(rel, s, m, h, v, r))
            f.flush()
            os.fsync(f.fileno())
        os.replace(long_path(tmp), long_path(self.path))
        self._appended = 0
        self._rewritten = True

    @staticmethod
    def _line(rel: str, s: int, m: int, h: str, v: bool, r: str) -> str:
        return json.dumps({"rel": rel, "s": s, "m": m, "h": h, "v": int(v), "r": r}, ensure_ascii=False,
                          separators=(",", ":")) + "\n"

    def record(self, rel: str, size: int, mtime_ns: int, sha256: str, verified: bool, reason: str = "") -> None:
        with self._lock:
            self.files[rel] = (size, mtime_ns, sha256, verified, reason)
            self._open()
            self._fh.write(self._line(rel, size, mtime_ns, sha256, verified, reason))
            self._appended += 1
            self._since_flush += 1
            now = time.monotonic()
            if self._since_flush >= STATE_FLUSH_EVERY or now - self._last_sync >= 10:
                self._flush(now)

    def _flush(self, now: float | None = None) -> None:
        if self._fh is None:
            return
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass
        self._since_flush = 0
        self._last_sync = now or time.monotonic()

    def save(self) -> None:
        """기록을 확정한다. 덧붙인 줄이 너무 많으면(같은 파일이 여러 번 기록됨) 한 번 정리해서 다시 쓴다."""
        with self._lock:
            if self._fh is not None:
                self._flush()
                self._fh.close()
                self._fh = None
            if not self.path.exists() or (self.reset_reason and not self._rewritten):
                self._write_all()  # 기록한 항목이 없어도 상태 파일(헤더)은 남긴다
            if self._appended > STATE_COMPACT_FACTOR * max(1, len(self.files)) + 1000:
                self._write_all()

    def close(self) -> None:
        self.save()


def read_state_entries(path: Path) -> dict[str, dict]:
    """상태 파일을 읽어 {상대 경로: {size, mtime_ns, sha256, verified, reason}} 로 돌려준다(점검/테스트용)."""
    out: dict[str, dict] = {}
    with open(long_path(path), "r", encoding="utf-8") as f:
        f.readline()
        for line in f:
            try:
                e = json.loads(line)
                out[e["rel"]] = {"size": e["s"], "mtime_ns": e["m"], "sha256": e["h"], "verified": bool(e["v"]),
                                 "reason": e.get("r", "")}
            except (ValueError, KeyError):
                continue
    return out


# --------------------------------------------------------------------------- 보조

def _repair_and_reverify(src: Path, dst: Path, before, report: Report, **kw):
    """strict 에서 불일치로 판정된 파일만 재복사하고 다시 해시 검사한다. 실패하면 원래 불일치 결과를 유지."""
    rep = repair_file(src, dst)
    if rep.status == FAILED:
        before.error = f"{before.error}; 재복사 실패: {rep.error}"
        return before
    after = verify_file(src, dst, **kw)
    after.backup = rep.backup
    if after.status == VERIFIED:
        report.meta.setdefault("repaired", []).append({"file": str(dst), "backup": rep.backup, "reason": before.error})
    return after


def _call_runner(runner: Runner, args: list[str], on_line: Callable[[str], None] | None,
                 alive: Callable[[], bool] | None = None) -> tuple[int, str]:
    validate_args(args)
    if getattr(runner, "supports_lines", False):
        return runner(args, on_line, alive)
    return runner(args)


def _friendly_makedirs(path: Path) -> None:
    try:
        os.makedirs(long_path(path), exist_ok=True)
    except OSError as e:
        win = getattr(e, "winerror", None)
        f = failure.network_failure(win) if win else None
        if f is None:
            raise  # 일반 오류(없는 드라이브, 파일이 폴더 자리에 있음 등)는 호출자가 '출력 경로를 만들 수 없습니다'로 알린다
        raise TransferError(f.text + f" ({path})") from e


def _join(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def _add_result(report: Report, res: FileResult, detail_ok: bool) -> None:
    """정상 결과는 파일이 아주 많으면 개수만 세고(메모리/리포트 크기), 문제 있는 결과는 항상 남긴다."""
    if res.status in (VERIFIED, SKIPPED_SAME, PENDING_VERIFY) and not detail_ok:
        report.counts[res.status] = report.counts.get(res.status, 0) + 1
    else:
        report.results.append(res)


# --------------------------------------------------------------------------- 덮어쓰기 전 보존(.bak)

def _differs(target: str, size: int, mtime_ns: int) -> bool:
    """대상에 같은 이름의 '다른' 파일이 있는가. robocopy 가 같다고 보는 기준(크기 같고 수정시각 2초 이내)과 같다. 폴더는 해당 없음."""
    try:
        st = os.stat(long_path(target))
    except OSError:
        return False
    if not os.path.isfile(long_path(target)):
        return False
    return not (st.st_size == size and abs(st.st_mtime_ns - mtime_ns) <= MTIME_TOLERANCE_NS)


def _bak_name(target: str) -> str:
    cand, n = target + ".bak", 1
    while os.path.lexists(long_path(cand)):
        cand = f"{target}.bak.{n}"
        n += 1
    return cand


def _read_journal(path: Path, src: Path) -> dict | None:
    try:
        with open(long_path(path), "r", encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(j, dict) or j.get("src") != str(src) or j.get("phase") not in ("copying", "done"):
        return None
    return j


def _write_journal(path: Path, src: Path, phase: str, count: int, listed: list[dict]) -> None:
    tmp = path.with_name(path.name + ".part")
    with open(long_path(tmp), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"journal_version": 1, "src": str(src), "phase": phase, "preserved_count": count, "preserved": listed},
                  f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(long_path(tmp), long_path(path))


def _preserve_existing(entries, src: Path, dst: Path, journal: Path, cancel, say) -> tuple[int, list[dict], bool]:
    """복사(robocopy) 시작 전에, 대상에 있는 같은 이름의 다른 파일을 같은 폴더의 .bak 으로 이름만 바꿔 보존한다(데이터를 다시 쓰지 않아 빠름).

    이어받기 구분: 이전 실행이 복사를 시작했다가 끝나지 못한 기록(journal phase=copying)이 있으면, 대상의 다른 파일은
    우리가 쓰다 만 파일이므로 보존하지 않는다(보존하면 이어받기가 깨진다). 이전 실행이 끝났거나(done) 기록이 없으면 새 복사다.
    보존할 수 없으면(사용 중 등) TransferError 로 복사를 시작하지 않는다. 반환: (보존한 개수, 앞쪽 목록, 이어받기 여부)."""
    prev = _read_journal(journal, src)
    if prev is not None and prev["phase"] == "copying":
        return int(prev.get("preserved_count") or 0), list(prev.get("preserved") or []), True
    count, listed = 0, []
    if any(True for _ in _first(dst)):
        say("대상에 있는 같은 이름의 다른 파일을 .bak 으로 보존하는 중입니다…")
        for rel, size, mtime in entries:
            if cancel is not None and cancel():
                raise Cancelled()
            target = _join(str(dst), rel)
            if not _differs(target, size, mtime):
                continue
            bak = _bak_name(target)
            try:
                os.replace(long_path(target), long_path(bak))
            except OSError as e:
                raise TransferError(f"대상에 있는 다른 파일을 .bak 으로 보존하지 못해 복사를 시작하지 않았습니다(덮어쓰지 않음): {target} "
                                    f"({failure.classify(e, target).label}). 파일을 쓰는 프로그램을 닫고 다시 실행하세요.") from e
            count += 1
            if len(listed) < MAX_PRESERVED_LISTED:
                listed.append({"file": target, "backup": bak})
    _write_journal(journal, src, "copying", count, listed)
    return count, listed, False


# --------------------------------------------------------------------------- 전송

def transfer(src: Path, dst: Path, *, dry_run: bool = False, include_cloud: bool = False,
             runner: Runner = default_runner, attr_getter: AttrGetter = default_attr_getter,
             log_path: Path | None = None, warn: Callable[[str], None] = lambda m: None,
             strict: bool = False, state_path: Path | None = None,
             progress: Callable[[str], None] | None = None,
             cancel: Callable[[], bool] | None = None,
             progress_info: Callable[[ProgressInfo], None] | None = None,
             verify_only: bool = False, retry_failed: bool = False,
             workers: int | None = None, chunk: int | None = None, keep_awake: bool = True,
             power_set_state: Callable[[int], int | None] | None = None,
             dest_info_fn: Callable[[str], fs.DestInfo] | None = None,
             manifest_out: Path | str | None = None, use_manifest: Path | str | None = None,
             verify_dest: bool | None = None, copy_mode: str = "restartable",
             retries: int | None = None, wait: int | None = None,
             clock: Callable[[], float] = time.monotonic, alive_fn: Callable[[], bool] | None = None,
             min_report_interval: float = 1.0, copy_threads: int | None = None) -> Report:
    """src 폴더를 dst 로 복사하고 SHA-256 으로 검사한다.

    - strict: 상태 파일을 믿지 않고 모든 파일을 다시 검사하고, 내용이 어긋난 파일은 원본에서 다시 복사(.bak 보존)한다.
    - verify_only: 복사 없이 원본/대상을 비교만 한다(대상은 항상 다시 읽음, 원본 해시는 상태 파일 값을 재사용).
    - retry_failed: 전체 robocopy 를 돌리지 않고 검사가 끝나지 않은/실패한 파일만 다시 복사하고 검사한다.
    - workers/chunk: 파일 단위 병렬 해시 개수(기본 2)와 읽기 청크.
    - manifest_out: 원본 기준 해시 목록을 JSONL 로 저장(새 PC 에서 verify 로 비교). verify_dest 가 None 이면
      대상이 네트워크 경로이고 목록을 만드는 경우 대상 검사를 미룬다(네트워크로 다시 읽지 않음).
    - use_manifest: 원본 해시를 이 목록에서 가져와 원본을 다시 읽지 않는다(외장 드라이브 2단계: 외장에서 새 PC 로 복사).
    - copy_mode: restartable(/Z) | normal | unbuffered(/J).
    """
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise TransferError(f"원본 폴더가 없습니다: {src}")
    if os.path.realpath(src) == os.path.realpath(dst) or _is_within(dst, src):
        raise TransferError("대상은 원본과 같거나 원본 폴더 안에 있을 수 없습니다")
    if verify_only and retry_failed:
        raise TransferError("검사만 다시(verify_only)와 실패한 것만 다시(retry_failed)는 함께 쓸 수 없습니다")
    if dry_run and (verify_only or retry_failed):
        raise TransferError("미리보기에서는 검사만 다시/실패한 것만 다시를 쓸 수 없습니다")
    workers = clamp_workers(workers)
    threads = max(1, min(16, copy_threads or DEFAULT_COPY_THREADS))
    chunk = chunk or DEFAULT_CHUNK
    if runner is default_runner:  # 줄 단위 진행/취소/드라이브 분리 감지가 되는 실행기
        runner = make_runner(cancel)
    emit_lock = threading.Lock()
    last_emit = [0.0]

    def say(msg: str) -> None:
        if progress:
            progress(msg)

    t_start = clock()
    dest = (dest_info_fn or fs.describe_destination)(str(dst))
    if dest.kind == "cdrom":
        raise TransferError("대상이 CD/DVD 드라이브입니다. 쓸 수 있는 하드디스크나 외장 드라이브를 고르세요.")
    network = dest.is_network
    if verify_only and not dst.is_dir():
        raise TransferError(f"검사할 대상 폴더가 없습니다: {dst}")
    defer = bool(manifest_out) and (network if verify_dest is None else not verify_dest) and not verify_only
    check_dest = not defer
    if network and not manifest_out and not dry_run and not verify_only:
        warn("대상이 네트워크 경로입니다. 복사본을 네트워크로 다시 읽어 검사하면 복사만큼 오래 걸립니다. "
             "목록 파일(--manifest-out)을 만들고 새 PC 에서 verify 로 검사하는 방법을 권장합니다.")

    report = Report(dry_run=dry_run)
    mode = "dry-run" if dry_run else "verify-only" if verify_only else "retry-failed" if retry_failed else "copy"
    report.meta.update(mode=mode, strict=strict, dest_kind=dest.kind, dest_fs=dest.fs, workers=workers, copy_threads=threads, chunk_bytes=chunk,
                       copy_mode=copy_mode, dest_verification="deferred" if defer else "now",
                       # 대상 내용 검사가 이 실행에서 끝났는지. 미룬 경우(랜선/네트워크 + 해시 목록) 새 PC 에서 verify 를 해야 끝난다.
                       verification_complete=not defer and not dry_run)

    # ---- 원본 조사(총 용량/파일 수/최상위 구조)
    say("원본 파일을 조사하는 중입니다… 파일이 많으면 몇 분 걸릴 수 있습니다.")
    scan = scan_source_info(src, attr_getter=None if attr_getter is default_attr_getter else attr_getter,
                            progress=progress, progress_info=progress_info, cancel=cancel, clock=clock)
    entries = scan.entries + (scan.cloud if include_cloud else [])
    skipped_cloud = [] if include_cloud else scan.cloud
    total_bytes = scan.total_bytes + (scan.cloud_bytes if include_cloud else 0)
    detail_ok = len(entries) <= MAX_DETAIL_RESULTS
    if scan.cloud:
        warn(f"OneDrive 클라우드 전용 파일 {len(scan.cloud)}개 감지: "
             + ("--include-cloud 로 포함됩니다(대량 다운로드 주의)." if include_cloud else "기본으로 건너뜁니다."))
        for rel, _s, _m in scan.cloud[:20]:
            warn(f"  클라우드 전용: {scan.abs_path(rel)}")
        if len(scan.cloud) > 20:
            warn(f"  ... 외 {len(scan.cloud) - 20}개")
    for rel, _s, _m in skipped_cloud:
        report.results.append(FileResult(scan.abs_path(rel), "", CLOUD_ONLY, error="클라우드 전용 파일(건너뜀)", reason="cloud_only"))
    for rel, code, text in scan.errors:
        report.results.append(FileResult(scan.abs_path(rel), "", FAILED, reason=code, error=f"{text} ({rel})"))
    report.meta.update(cloud_only_count=len(scan.cloud), include_cloud=include_cloud, total_files=len(entries),
                       total_bytes=total_bytes, source_root=scan.root, source_layout=scan.layout(),
                       scan_seconds=round(scan.seconds, 2), skipped_links=scan.skipped_links, skipped_link_paths=list(scan.skipped_link_paths),
                       results_truncated=not detail_ok)
    say(f"원본 조사 완료: 파일 {len(entries):,}개, 총 {fmt_bytes(total_bytes)} (조사 {scan.seconds:.0f}초).")

    # ---- 파일 시스템/여유 공간 사전 검사(복사 전에 막는다)
    if scan.max_size > fs.FAT4G and dest.fs == "FAT32":
        big = sum(1 for _r, s, _m in entries if s > fs.FAT4G)
        report.meta.update(fat32_blocked=True, fat32_big_files=big)
        msg = (f"대상 드라이브가 FAT32 라서 4GB 보다 큰 파일 {big}개({fmt_bytes(scan.max_size)} 포함)를 저장할 수 없습니다. "
               "NTFS 로 포맷하거나 NTFS 드라이브를 쓰세요.")
        if dry_run:
            warn(msg)
        else:
            raise TransferError(msg)
    elif dest.fs == "EXFAT":
        warn("대상 드라이브가 exFAT 입니다. 4GB 이상 파일은 저장되지만 전원이 갑자기 꺼지면 손상되기 쉽습니다. 가능하면 NTFS 를 권장합니다.")
    if not verify_only:
        need = _needed_bytes(entries, dst, total_bytes)
        required = need + int(total_bytes * SPACE_MARGIN)
        report.meta.update(dest_free_bytes=dest.free, required_bytes=required,
                           space_blocked=dest.free is not None and dest.free < required)
        if dest.free is None:
            warn("대상의 여유 공간을 조회하지 못해 사전 검사를 건너뜁니다. 복사 중 공간이 부족하면 멈출 수 있습니다.")
        elif dest.free < required:
            msg = (f"대상 드라이브의 여유 공간이 부족합니다. 필요 약 {fmt_bytes(required)}(원본 {fmt_bytes(total_bytes)} + 여유 5%), "
                   f"현재 여유 {fmt_bytes(dest.free)}. 더 큰 드라이브를 쓰거나 정리한 뒤 다시 시작하세요.")
            if dry_run:
                warn(msg)
            else:
                raise TransferError(msg)

    if dry_run:
        if progress_info:
            progress_info(ProgressInfo("compare", 0, 0, 0, len(entries), message="복사될 파일을 비교하는 중입니다…",
                                       skipped=len(skipped_cloud)))
        return _dry_run(report, entries, skipped_cloud, scan, dst, runner, copy_mode, network, warn)

    # ---- 실제 실행
    _friendly_makedirs(dst)
    log = Path(log_path) if log_path else dst / LOG_NAME
    state = State(Path(state_path) if state_path else dst / STATE_NAME, src, str(dst), dest.serial)
    report.meta["state_file"] = str(state.path)
    if state.reset_reason:
        report.meta["state_reset"] = state.reset_reason
        warn(state.reset_reason)
    if state.prev_dest_path and state.prev_dest_path.lower() != str(dst).lower():
        if dest.serial is not None and state.prev_dest_serial == dest.serial:
            warn(f"드라이브 문자가 바뀐 것 같습니다(이전 {state.prev_dest_path} → 지금 {dst}). 같은 드라이브이므로 이어받습니다.")
        else:
            warn(f"이전 기록의 대상 위치({state.prev_dest_path})와 지금({dst})이 다릅니다. 다른 드라이브가 아닌지 확인하세요.")
    elif state.prev_dest_serial is not None and dest.serial is not None and state.prev_dest_serial != dest.serial:
        warn("같은 경로지만 드라이브 일련번호가 이전과 다릅니다. 다른 드라이브가 같은 문자로 연결됐을 수 있으니 확인하세요(검사가 다시 진행됩니다).")
    alive = alive_fn or _alive_checker(dst, network)

    manifest_map: dict[str, tuple[int, str]] | None = None
    if use_manifest:
        manifest_map = {}
        mreader = ManifestReader(Path(use_manifest))
        report.meta["original_root"] = str(mreader.header.get("source_root", ""))
        for row in mreader.rows():
            manifest_map[row["rel"]] = (row["size"], row["sha256"])
        report.meta["use_manifest"] = str(use_manifest)
    writer = None
    if manifest_out:
        writer = ManifestWriter(manifest_out, scan.root, len(entries), total_bytes, scan.layout())
    stats = IoStats()
    kind_bps, kind_hint = slow_policy("network" if network else dest.kind)
    ok_finish = False
    try:
        with KeepAwake(keep_awake, power_set_state, warn) as awake:
            # ---- 1) 복사 단계(robocopy). 검사만/실패한 것만 다시 모드에서는 건너뛴다.
            copy_seconds = 0.0
            journal = dst / PRESERVE_NAME
            report.meta.update(preserved_existing_count=0, preserved_existing=[], preserve_resumed=False)
            if mode == "copy":
                n_pres, listed, resumed_run = _preserve_existing(entries, src, dst, journal, cancel, say)
                report.meta.update(preserved_existing_count=n_pres, preserved_existing=listed, preserve_resumed=resumed_run)
                if n_pres:
                    warn(f"대상에 있던 같은 이름의 다른 파일 {n_pres}개를 .bak 으로 보존했습니다(덮어쓰지 않았습니다).")
                t0 = clock()
                tracker = ProgressTracker("copy", total_bytes, len(entries), clock=clock, slow_bps=kind_bps, slow_hint=kind_hint)

                def on_line(line: str) -> None:
                    size = parse_copy_line(line)
                    if size is None:
                        return
                    tracker.add(size, 1)
                    tracker.set_current(parse_copy_path(line) or "")
                    tracker.note(OK)
                    now = clock()
                    with emit_lock:
                        if now - last_emit[0] < min_report_interval:
                            return
                        last_emit[0] = now
                    snap = tracker.snapshot()
                    if progress_info:
                        progress_info(snap)
                    say(format_info(snap))

                say("복사 도구로 복사하는 중입니다(파일이 크면 오래 걸립니다)…")
                code, text, runs = _copy_with_robocopy(src, dst, log, skipped_cloud, runner, cancel, copy_mode,
                                                       retries if retries is not None else (10 if network else 3),
                                                       wait if wait is not None else (30 if network else 5), on_line, scan, alive, threads)
                ok, notes = interpret_exit(code)
                report.meta.update(robocopy_exit=code, robocopy_ok=ok, robocopy_notes=notes, robocopy_runs=runs,
                                   robocopy_log=str(log))
                found = failure.robocopy_failures(text)
                report.meta["robocopy_failures"] = [f.code for f in found]
                if not ok:
                    fatal = [f for f in found if f.code.startswith("net_")]
                    if fatal:
                        raise TransferError(fatal[0].text)
                    if not alive():
                        raise DriveDisconnected(DISCONNECTED_MESSAGE)
                    why = " ".join(f.label for f in found)
                    report.results.append(FileResult(str(src), str(dst), FAILED, reason=found[0].code if found else "robocopy",
                                                     error=f"복사 도구 종료 코드 {code}: {', '.join(notes)}" + (f" ({why})" if why else "")))
                copy_seconds = clock() - t0
                if ok:  # 복사가 끝났다: 다음 실행은 새 복사로 보고 바뀐 파일을 .bak 으로 보존한다
                    try:
                        _write_journal(journal, src, "done", report.meta["preserved_existing_count"], report.meta["preserved_existing"])
                    except OSError:
                        try:  # 'done' 을 못 적으면 기록을 지운다(남겨 두면 다음 실행이 이어받기로 보고 .bak 보존을 건너뛰게 된다)
                            os.unlink(long_path(journal))
                        except OSError:
                            pass
            # ---- 2) 검사 단계(파일 단위 병렬)
            t1 = clock()
            _verify_phase(report, entries, scan, dst, state, stats, writer, manifest_map, strict=strict, mode=mode,
                          check_dest=check_dest, workers=workers, chunk=chunk, cancel=cancel, progress=progress,
                          progress_info=progress_info, clock=clock, min_interval=min_report_interval, alive=alive,
                          detail_ok=detail_ok, kind_bps=kind_bps, kind_hint=kind_hint, warn=warn)
            verify_seconds = clock() - t1
        report.meta.update(keep_awake=awake.status, copy_seconds=round(copy_seconds, 1), verify_seconds=round(verify_seconds, 1),
                           total_seconds=round(clock() - t_start, 1), io=stats.as_dict())
        if writer is not None:
            report.meta["manifest_out"] = str(writer.close())
            report.meta["manifest_files"] = writer.files
        ok_finish = True
    finally:
        state.save()
        if writer is not None and not ok_finish:
            writer.abort()
    return report


def _alive_checker(dst: Path, network: bool) -> Callable[[], bool]:
    root = fs.volume_root(dst)

    def alive() -> bool:
        try:
            return os.path.isdir(long_path(root if not network else str(dst)))
        except OSError:
            return False
    return alive


def _needed_bytes(entries, dst: Path, total_bytes: int) -> int:
    """대상에 새로 필요한 용량. 대상이 비어 있으면 원본 총량, 이미 일부 있으면 없거나 크기가 다른 파일만 센다."""
    if not dst.is_dir() or not any(True for _ in _first(dst)):
        return total_bytes
    need = 0
    for rel, size, _m in entries:
        try:
            if os.stat(long_path(_join(str(dst), rel))).st_size == size:
                continue
        except OSError:
            pass
        need += size
    return need


def _first(path: Path):
    try:
        with os.scandir(long_path(path)) as it:
            for e in it:
                if not e.name.startswith(".pcmigrate-"):
                    yield e
                    return
    except OSError:
        return


def _dry_run(report: Report, entries, skipped_cloud, scan: ScanInfo, dst: Path, runner, copy_mode: str, network: bool,
             warn: Callable[[str], None] = lambda m: None) -> Report:
    """robocopy /L 은 목록 확인용, 리포트는 크기 비교로 만든다. 아무것도 쓰지 않는다."""
    args = base_args(Path(scan.root), dst, None, True, copy_mode)
    xf = sorted({os.path.basename(scan.abs_path(r)) for r, _s, _m in skipped_cloud})
    if xf and sum(len(n) + 3 for n in xf) <= MAX_XF_CHARS:
        args += xf  # 너무 길면 생략: 목록 확인용일 뿐 리포트는 별도로 만든다
    code, _ = _call_runner(runner, args, None)
    ok, notes = interpret_exit(code)
    report.meta.update(robocopy_exit=code, robocopy_ok=ok, robocopy_notes=notes)
    detail_ok = len(entries) <= MAX_DETAIL_RESULTS
    over = 0
    for rel, size, mtime in entries:
        target = _join(str(dst), rel)
        status = WOULD_COPY
        try:
            st = os.stat(long_path(target))
            status = SKIPPED_SAME if st.st_size == size and abs(st.st_mtime_ns - mtime) <= MTIME_TOLERANCE_NS else WOULD_OVERWRITE
        except OSError:
            pass
        res = FileResult(scan.abs_path(rel), target, status, size)
        if status == WOULD_OVERWRITE:
            over += 1
            if over <= MAX_DETAIL_RESULTS:
                res.backup = _bak_name(target)  # 실제 복사 때 기존 파일이 옮겨질 이름
        if status in (WOULD_COPY, SKIPPED_SAME) and not detail_ok:
            report.counts[status] = report.counts.get(status, 0) + 1
        else:
            report.results.append(res)
    report.meta["would_overwrite_count"] = over
    if over:
        warn(f"대상에 같은 이름의 다른 파일 {over}개가 이미 있습니다. 실제 복사 때는 덮어쓰기 전에 각 파일을 같은 폴더의 .bak 으로 보존합니다"
             "(이름만 바꾸므로 공간을 더 쓰지 않습니다). 확인하고 진행하세요.")
    return report


def _copy_with_robocopy(src: Path, dst: Path, log: Path, cloud: list, runner: Runner, cancel, copy_mode: str,
                        retries: int, wait: int, on_line, scan: ScanInfo, alive, threads: int = 1) -> tuple[int, str, int]:
    """robocopy 실행. (최악 종료코드, 오류 줄 모음, 실행 횟수). 클라우드 전용 파일은 /XF 로 제외."""
    if not cloud:
        code, text = _call_runner(runner, base_args(src, dst, log, False, copy_mode, retries, wait, threads), on_line, alive)
        return code, text, 1
    # 폴더별 실행: /LEV:1 + 해당 폴더의 클라우드 파일명만 /XF (전체 경로를 한 명령줄에 넣지 않는다)
    skip_by_dir: dict[str, list[str]] = {}
    for rel, _s, _m in cloud:
        d, _, name = rel.rpartition("/")
        skip_by_dir.setdefault(d, []).append(name)
    worst = runs = 0
    texts: list[str] = []
    for root, dirs, _files in os.walk(src, followlinks=False):
        if cancel is not None and cancel():
            raise Cancelled()
        dirs.sort()
        dirs[:] = [d for d in dirs if not fs.is_reparse(os.path.join(root, d))]  # robocopy /XJ 와 같은 기준: 정션/링크는 따라가지 않는다
        relp = os.path.relpath(root, src)
        key = "" if relp == "." else relp.replace(os.sep, "/")
        args = base_args(Path(root), dst / ("" if relp == "." else relp), log, False, copy_mode, retries, wait, threads)
        args[args.index("/E")] = "/LEV:1"
        if key in skip_by_dir:
            args += skip_by_dir[key]
        code, text = _call_runner(runner, args, on_line, alive)
        worst = max(worst, code)
        texts.append(text)
        runs += 1
    return worst, "\n".join(texts), runs


def _verify_phase(report: Report, entries, scan: ScanInfo, dst: Path, state: State, stats: IoStats, writer,
                  manifest_map, *, strict: bool, mode: str, check_dest: bool, workers: int, chunk: int, cancel,
                  progress, progress_info, clock, min_interval: float, alive, detail_ok: bool, kind_bps: float,
                  kind_hint: str, warn) -> None:
    """모든 파일을 검사한다(파일 단위 병렬). 상태 파일/해시 목록의 원본 해시는 다시 읽지 않고 재사용한다."""
    dst_root = str(dst)
    retry = mode == "retry-failed"
    verify_only = mode == "verify-only"
    resumed = [0]
    unmatched_manifest = dict(manifest_map) if manifest_map is not None else None

    def reuse_hash(rel: str, size: int, mtime: int) -> str | None:
        if strict:
            return None
        if manifest_map is not None:
            m = manifest_map.get(rel)
            if m and m[0] == size:
                return m[1]
        return state.src_hash(rel, size, mtime)

    def expected_reads(entry) -> int:
        rel, size, mtime = entry
        reads = 0 if reuse_hash(rel, size, mtime) else 1
        return reads + (1 if check_dest else 0)

    total = sum(e[1] * expected_reads(e) for e in entries)
    tracker = ProgressTracker("verify", total, len(entries), clock=clock, slow_bps=kind_bps, slow_hint=kind_hint)
    last = [0.0]
    lock = threading.Lock()
    on_bytes = tracker.add

    def emit(force: bool = False) -> None:
        now = clock()
        with lock:
            if not force and now - last[0] < min_interval:
                return
            last[0] = now
        snap = tracker.snapshot()
        if progress_info:
            progress_info(snap)
        if progress:
            progress(format_info(snap))

    def task(entry):
        rel, size, mtime = entry
        s_path, d_path = _join(scan.root, rel), _join(dst_root, rel)
        tracker.set_current(d_path)
        if not strict and not verify_only:
            digest = state.is_verified(rel, size, mtime, d_path)
            if digest is not None:
                return FileResult(s_path, d_path, VERIFIED, size, digest), True
        prior = state.files.get(rel)
        if retry and not (prior and (prior[3] or prior[4] == "pending")):
            res = copy_verified(s_path, d_path, chunk=chunk, cancel=cancel, on_bytes=on_bytes, stats=stats)
            if res.status in ("copied", SKIPPED_SAME):  # 이미 같은 내용이면 건너뜀(.bak 을 만들지 않음)
                res.status = VERIFIED
            return res, False
        res = verify_file(s_path, d_path, chunk=chunk, cancel=cancel, on_bytes=on_bytes, stats=stats,
                          src_hash=reuse_hash(rel, size, mtime), check_dest=check_dest)
        if retry and res.status in (MISSING, MISMATCH):  # 검사를 미뤘거나 이전에 통과했어도 대상이 비었거나 다르면 다시 복사한다
            res = copy_verified(s_path, d_path, chunk=chunk, cancel=cancel, on_bytes=on_bytes, stats=stats)
            if res.status in ("copied", SKIPPED_SAME):
                res.status = VERIFIED
            return res, False
        if strict and res.status == MISMATCH:
            res = _repair_and_reverify(Path(s_path), Path(d_path), res, report, chunk=chunk, cancel=cancel, on_bytes=on_bytes,
                                       stats=stats, check_dest=check_dest)
        if writer is not None and not res.sha256 and res.status in (MISMATCH, MISSING):
            try:  # 목록은 원본 기준이므로 대상이 없거나 달라도 원본 해시는 필요하다
                res.sha256 = sha256_file(s_path, chunk, cancel, on_bytes)
                stats.file_read("src")
            except Cancelled:
                raise
            except OSError:
                pass
        return res, False

    def handle(entry, result) -> None:
        rel, size, mtime = entry
        res, was_resumed = result
        if unmatched_manifest is not None:
            unmatched_manifest.pop(rel, None)
        if was_resumed:
            resumed[0] += 1
            tracker.total_bytes = max(0, tracker.total_bytes - size * expected_reads(entry))
        elif strict and res.status == VERIFIED and _state_conflict(state, rel, size, mtime, res.sha256):
            res.status, res.reason = MISMATCH, "state_conflict"
            res.error = "상태 파일의 검사 해시와 원본이 어긋남(크기/수정시각은 동일)"
        if not was_resumed:
            if res.status == PENDING_VERIFY:
                state.record(rel, size, mtime, res.sha256, False, "pending")
            elif res.status == VERIFIED:
                state.record(rel, size, mtime, res.sha256, True)
            else:
                state.record(rel, size, mtime, res.sha256, False, res.reason or res.status)
        if writer is not None:
            if res.sha256:
                writer.write_row(rel, size, mtime, res.sha256)
            else:
                writer.note_error()
        if res.status == FAILED and res.reason in ("disconnected",) and not alive():
            raise DriveDisconnected(DISCONNECTED_MESSAGE)
        tracker.add(0, 1)
        tracker.note(categorize(res.status, res.reason), res.src)
        _add_result(report, res, detail_ok)
        emit()

    try:
        run_ordered(entries, task, handle, workers=workers, cancel=cancel)
    except Cancelled:
        raise
    if unmatched_manifest:
        for rel, (size, sha) in unmatched_manifest.items():
            report.results.append(FileResult(f"[목록] {rel}", _join(dst_root, rel), MISSING, size, sha, reason="missing_in_source",
                                             error="해시 목록에는 있지만 원본(외장 드라이브)에 이 파일이 없습니다. 복사가 덜 됐을 수 있습니다."))
    report.meta["resumed_skipped"] = resumed[0]
    emit(True)


def _state_conflict(state: State, rel: str, size: int, mtime: int, sha256: str) -> bool:
    """상태 파일이 '검사 완료'로 기록한 해시가 지금 원본 해시와 다른데 크기/수정시각은 같은 경우."""
    e = state.files.get(rel)
    return bool(e and e[3] and e[2] != sha256 and e[0] == size and e[1] == mtime)
