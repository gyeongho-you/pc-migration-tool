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
- 옵션 split_large(--split-large): 4GiB-1 바이트를 넘는 파일은 robocopy 에서 빼고 split.py 가 <이름>.pcm001... 조각으로 나눠 쓴다.
  조각 단위로 이어받고(상태 파일에 조각별 기록), 조각 해시/전체 해시는 해시 목록(parts)에 기록된다. 새 PC 에서 pcmigrate join 으로 합친다.
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
import stat as _stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import failure, fs, junk as junk_mod, split
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
              retries: int = 3, wait: int = 5, threads: int = 1, xf_extra: tuple[str, ...] = (),
              xd: tuple[str, ...] = ()) -> list[str]:
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
    if xd:  # 정크 제외(--exclude-junk): 폴더 이름 패턴. /XF 목록 뒤에 이름을 이어 붙이는 코드가 있으므로 /XF 앞에 둔다.
        args += ["/XD", *xd]
    args += ["/XF", OWN_PATTERN, *xf_extra]  # 이 도구의 기록 파일(상태/로그/목록)은 옮기지 않는다. 뒤에 파일 이름을 더 이어 붙일 수 있다.
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
                if head.get("state_version") != STATE_VERSION or not _same_path(head.get("src"), self.src):
                    raise ValueError(f"버전/원본 경로 불일치(이전 원본: {head.get('src')}). 같은 원본이어도 드라이브 문자가 바뀌었으면 안전하게 전체를 다시 검사합니다")
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
    if not _stat.S_ISREG(st.st_mode):  # stat 한 번으로 일반 파일 여부까지 본다(R1-12)
        return False
    return not (st.st_size == size and abs(st.st_mtime_ns - mtime_ns) <= MTIME_TOLERANCE_NS)


def _bak_name(target: str) -> str:
    cand, n = target + ".bak", 1
    while os.path.lexists(long_path(cand)):
        cand = f"{target}.bak.{n}"
        n += 1
    return cand


def _same_path(a, b) -> bool:
    """두 경로가 같은 곳인지(대소문자/상대 경로/링크 무시). 비교할 수 없으면 False(안전한 쪽: 같다고 보지 않는다)."""
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))
    except (OSError, ValueError):
        return False


def _read_journal(path: Path, src: Path) -> dict | None:
    j = _read_journal_raw(path)
    if j is None or not _same_path(j.get("src"), src):
        return None
    return j


def _read_journal_raw(path: Path) -> dict | None:
    try:
        with open(long_path(path), "r", encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(j, dict) or j.get("phase") not in ("copying", "done"):
        return None
    return j


def _write_journal(path: Path, src: Path, phase: str, count: int, listed: list[dict], started_ns: int | None = None) -> None:
    tmp = path.with_name(path.name + ".part")
    with open(long_path(tmp), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"journal_version": 1, "src": str(src), "phase": phase, "preserved_count": count, "preserved": listed,
                   "started_ns": started_ns if started_ns is not None else time.time_ns()}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(long_path(tmp), long_path(path))


@dataclass
class DestScan:
    """대상 폴더를 한 번 훑은 결과(stat 중복 제거, R1-12). states 는 entries 와 같은 순서: n=없음, s=같음, d=같은 이름의 다른 파일."""
    states: list[str] = field(default_factory=list)
    old_sizes: dict[str, int] = field(default_factory=dict)  # 'd' 인 파일의 기존 크기(rel -> 크기). 덮어쓰기 전 .bak 으로 남는 양
    need: int = 0       # 새로 써야 하는 바이트(없거나 다른 파일의 새 크기 합). 다른 파일의 기존 내용은 .bak 으로 남아 공간을 계속 쓴다
    dst_empty: bool = True

    @property
    def bak_count(self) -> int:
        return len(self.old_sizes)

    @property
    def bak_bytes(self) -> int:
        return sum(self.old_sizes.values())


def _scan_dest(entries, dst: Path, total_bytes: int, cancel: Callable[[], bool] | None = None) -> DestScan:
    """_differs 와 같은 기준(크기 + 수정시각 2초)으로 각 파일이 대상에 '없음/같음/다름' 인지 한 번에 본다."""
    out = DestScan(states=["n"] * len(entries), need=total_bytes)
    if not dst.is_dir() or not any(True for _ in _first(dst)):
        return out
    out.dst_empty = False
    need = 0
    for i, (rel, size, mtime) in enumerate(entries):
        if cancel is not None and i % 2000 == 0 and cancel():
            raise Cancelled()
        try:
            st = os.stat(long_path(_join(str(dst), rel)))
        except OSError:
            need += size
            continue
        if not _stat.S_ISREG(st.st_mode):
            need += size  # 폴더 등은 .bak 대상이 아님(복사 단계에서 문제로 드러난다)
            continue
        if st.st_size == size and abs(st.st_mtime_ns - mtime) <= MTIME_TOLERANCE_NS:
            out.states[i] = "s"
        else:
            out.states[i] = "d"
            out.old_sizes[rel] = st.st_size
            need += size
    out.need = need
    return out


def _is_partial_prefix(src_file: str, dst_file: str, dst_size: int, cancel: Callable[[], bool] | None = None) -> bool:
    """대상 파일이 원본의 '앞부분 그대로'(robocopy 가 쓰다 끊긴 이어받기용 사본)인지 바이트로 확인한다.
    원본보다 작고 내용이 같은 앞부분이어야 True. 크기 0 이면 잃을 내용이 없으므로 True."""
    if dst_size == 0:
        return True
    try:
        if os.stat(long_path(src_file)).st_size <= dst_size:
            return False
        left = dst_size
        with open(long_path(src_file), "rb") as fs_, open(long_path(dst_file), "rb") as fd_:
            while left > 0:
                if cancel is not None and cancel():
                    raise Cancelled()
                n = min(DEFAULT_CHUNK, left)
                a, b = fs_.read(n), fd_.read(n)
                if a != b or not a:
                    return False
                left -= len(a)
    except OSError:
        return False
    return True


class _PreserveFailed(Exception):
    def __init__(self, target: str, cause: OSError) -> None:
        super().__init__(str(cause))
        self.target, self.cause = target, cause


def _rollback(moved: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """이미 .bak 으로 옮긴 파일을 거꾸로 원래 이름으로 되돌린다. 되돌리지 못한 (원래 이름, .bak 이름) 목록을 돌려준다."""
    stuck = []
    for target, bak in reversed(moved):
        try:
            if os.path.lexists(long_path(target)):
                stuck.append((target, bak))  # 원래 이름 자리에 이미 다른 것이 있으면 덮어쓰지 않는다
            else:
                os.replace(long_path(bak), long_path(target))
        except OSError:
            stuck.append((target, bak))
    return stuck


def _preserve_existing(entries, src: Path, dst: Path, journal: Path, cancel, say,
                       scan: DestScan | None = None, warn: Callable[[str], None] = lambda m: None
                       ) -> tuple[int, list[dict], bool]:
    """복사(robocopy) 시작 전에, 대상에 있는 같은 이름의 다른 파일을 같은 폴더의 .bak 으로 이름만 바꿔 보존한다(데이터를 다시 쓰지 않아 빠름).

    이어받기 구분: 이전 실행이 복사를 시작했다가 끝나지 못한 기록(journal phase=copying)이 있으면, 대상에서 원본의 '앞부분 그대로'인
    파일(robocopy 가 쓰다 만 사본)은 이어받도록 두고, 그 밖의 다른 파일(그 사이 바뀐 파일 등, R1-13)은 .bak 으로 보존한다.
    원본 경로가 이전 기록과 다르면(드라이브 문자 변경 등) 이어받기로 보지 않는다(R1-4).
    보존 도중 실패하면 이미 옮긴 것을 원래 이름으로 되돌리고(롤백) TransferError 로 복사를 시작하지 않는다(R1-2).
    반환: (보존한 개수, 앞쪽 목록, 이어받기 여부)."""
    raw = _read_journal_raw(journal)
    prev = _read_journal(journal, src)
    if raw is not None and raw["phase"] == "copying" and prev is None:
        warn(f"이전 실행 기록의 원본 경로({raw.get('src')})가 지금({src})과 다릅니다(드라이브 문자가 바뀌었을 수 있습니다). "
             "같은 원본이라도 안전하게 이어받기로 보지 않고, 대상에 있는 다른 파일은 .bak 으로 보존한 뒤 복사합니다.")
    resumed = prev is not None and prev["phase"] == "copying"
    count = int(prev.get("preserved_count") or 0) if resumed else 0
    listed = list(prev.get("preserved") or []) if resumed else []
    started = prev.get("started_ns") if resumed and isinstance(prev.get("started_ns"), int) else None
    if scan is None:
        scan = _scan_dest(entries, dst, sum(e[1] for e in entries), cancel)
    todo = [e for i, e in enumerate(entries) if scan.states[i] == "d"]
    moved: list[tuple[str, str]] = []
    try:
        if todo:
            say("대상에 있는 같은 이름의 다른 파일을 확인하는 중입니다…" if resumed else
                f"대상에 있는 같은 이름의 다른 파일 {len(todo):,}개를 .bak 으로 보존하는 중입니다…")
        last_say = time.monotonic()
        for n, (rel, _size, _mtime) in enumerate(todo, 1):
            if cancel is not None and cancel():
                raise Cancelled()
            target = _join(str(dst), rel)
            if resumed and _is_partial_prefix(_join(str(src), rel), target, scan.old_sizes.get(rel, -1), cancel):
                continue  # 쓰다 끊긴 사본: 이어받는다
            bak = _bak_name(target)
            try:
                os.replace(long_path(target), long_path(bak))
            except FileNotFoundError:
                continue  # 그 사이 사라진 파일은 보존할 것이 없다
            except OSError as e:
                raise _PreserveFailed(target, e) from e
            moved.append((target, bak))
            count += 1
            if len(listed) < MAX_PRESERVED_LISTED:
                listed.append({"file": target, "backup": bak})
            if time.monotonic() - last_say >= 2.0:
                last_say = time.monotonic()
                say(f".bak 으로 보존하는 중입니다… {n:,}/{len(todo):,}개 확인, {len(moved):,}개 옮김")
        _write_journal(journal, src, "copying", count, listed, started)
    except BaseException as e:
        stuck = _rollback(moved)
        if isinstance(e, _PreserveFailed):
            reason = failure.classify(e.cause, e.target).label
            back = (f"이미 .bak 으로 옮긴 파일 {len(moved) - len(stuck)}개는 원래 이름으로 되돌렸습니다."
                    if moved else "아직 옮긴 파일은 없습니다.")
            left = ""
            if stuck:
                left = (f" 되돌리지 못한 {len(stuck)}개는 .bak 이름으로 남아 있습니다(이름만 원래대로 바꾸면 복구됩니다): "
                        + ", ".join(b for _t, b in stuck[:3]) + (" …" if len(stuck) > 3 else ""))
            raise TransferError(f"대상에 있는 다른 파일을 .bak 으로 보존하지 못해 복사를 시작하지 않았습니다(덮어쓰지 않음): {e.target} "
                                f"({reason}). {back}{left} 파일을 쓰는 프로그램을 닫고 다시 실행하세요.") from e.cause
        if stuck:
            warn(f"중단되어 .bak 으로 옮긴 파일 {len(moved)}개를 되돌렸지만 {len(stuck)}개는 되돌리지 못했습니다: "
                 + ", ".join(b for _t, b in stuck[:3]))
        raise
    return count, listed, resumed


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
             min_report_interval: float = 1.0, copy_threads: int | None = None, split_large: bool = False,
             junk_spec: "junk_mod.JunkSpec | None" = None) -> Report:
    """src 폴더를 dst 로 복사하고 SHA-256 으로 검사한다.

    - strict: 상태 파일을 믿지 않고 모든 파일을 다시 검사하고, 내용이 어긋난 파일은 원본에서 다시 복사(.bak 보존)한다.
    - verify_only: 복사 없이 원본/대상을 비교만 한다(대상은 항상 다시 읽음, 원본 해시는 상태 파일 값을 재사용).
    - retry_failed: 전체 robocopy 를 돌리지 않고 검사가 끝나지 않은/실패한 파일만 다시 복사하고 검사한다.
    - workers/chunk: 파일 단위 병렬 해시 개수(기본 2)와 읽기 청크.
    - manifest_out: 원본 기준 해시 목록을 JSONL 로 저장(새 PC 에서 verify 로 비교). verify_dest 가 None 이면
      대상이 네트워크 경로이고 목록을 만드는 경우 대상 검사를 미룬다(네트워크로 다시 읽지 않음).
    - use_manifest: 원본 해시를 이 목록에서 가져와 원본을 다시 읽지 않는다(외장 드라이브 2단계: 외장에서 새 PC 로 복사).
    - copy_mode: restartable(/Z) | normal | unbuffered(/J).
    - split_large: 4GiB-1 바이트를 넘는 파일을 조각(.pcm001...)으로 나눠 저장한다(FAT32 대상 포함). 끄면 동작이 전과 같다.
    - junk_spec: junk.make_spec() 결과. 주면 그 규칙의 파일/폴더를 robocopy /XF /XD 로 빼고 원본 조사에서도 뺀 뒤
      report.meta["junk_excluded"] 에 개수/용량을 남긴다. None(기본)이면 아무것도 제외하지 않는다.
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
    junk_info = junk_mod.apply_to_scan(scan, junk_spec) if junk_spec is not None else None
    entries = scan.entries + (scan.cloud if include_cloud else [])
    skipped_cloud = [] if include_cloud else scan.cloud
    total_bytes = scan.total_bytes + (scan.cloud_bytes if include_cloud else 0)
    detail_ok = len(entries) <= MAX_DETAIL_RESULTS
    sctx = SplitCtx(split.part_size()) if split_large else None
    plain = entries  # robocopy 가 복사하는 파일(분할 대상 제외)
    if sctx is not None:
        big = [e for e in entries if split.needs_split(e[1])]
        if big:
            sctx.entries = big
            plain = [e for e in entries if not split.needs_split(e[1])]
            sctx.bytes = sum(e[1] for e in big)
            sctx.parts_total = sum(len(split.part_sizes(e[1], sctx.psz)) for e in big)
        report.meta.update(split_large=True, split_threshold=split.split_threshold(), split_part_size=sctx.psz,
                           split_files=len(big), split_parts=sctx.parts_total, split_bytes=sctx.bytes)
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
    if junk_info is not None:
        report.meta["junk_excluded"] = junk_info.as_dict(junk_spec)
    say(f"원본 조사 완료: 파일 {len(entries):,}개, 총 {fmt_bytes(total_bytes)} (조사 {scan.seconds:.0f}초).")

    # ---- 파일 시스템/여유 공간 사전 검사(복사 전에 막는다). export 와 같은 fs.check_space 를 쓴다
    biggest = scan.max_size if sctx is None or not sctx.entries else max([e[1] for e in plain] + [sctx.psz])
    sc0 = fs.check_space(dest, 0, biggest)
    if sc0.fat32_blocked:
        big = sum(1 for _r, s, _m in entries if s > fs.FAT4G)
        report.meta.update(fat32_blocked=True, fat32_big_files=big)
        msg = (f"대상 드라이브가 FAT32 라서 4GB 보다 큰 파일 {big}개({fmt_bytes(scan.max_size)} 포함)를 저장할 수 없습니다. "
               "NTFS 로 포맷하거나 NTFS 드라이브를 쓰세요. 포맷이 어려우면 --split-large 옵션으로 큰 파일을 조각(.pcm001 ...)으로 나눠 "
               "저장하고, 새 PC 에서 pcmigrate join 으로 합칠 수도 있습니다.")
        if dry_run:
            warn(msg)
        else:
            raise TransferError(msg)
    elif sc0.exfat:
        warn("대상 드라이브가 exFAT 입니다. 4GB 이상 파일은 저장되지만 전원이 갑자기 꺼지면 손상되기 쉽습니다. 가능하면 NTFS 를 권장합니다.")
    if sctx is not None and sctx.entries:
        warn(f"--split-large: 4GB 넘는 파일 {len(sctx.entries)}개({fmt_bytes(sctx.bytes)})를 조각 {sctx.parts_total}개(조각 하나 최대 "
             f"{fmt_bytes(sctx.psz)})로 나눠 저장합니다. 새 PC 에서 pcmigrate join 으로 합쳐야 원래 파일이 됩니다.")
    dscan: DestScan | None = None
    if not verify_only:
        say("대상에 이미 있는 파일을 확인하는 중입니다…")
        dscan = _scan_dest(plain, dst, total_bytes - (sctx.bytes if sctx else 0), cancel)  # 복사 여부 판단 기준(_differs 와 같음)을 한 번만 훑는다
        if sctx is not None and sctx.entries:  # 조각은 이미 있는(크기가 맞는) 조각만큼 덜 쓴다
            dscan.need += sum(e[1] - split.parts_bytes_present(_join(str(dst), e[0]), e[1], sctx.psz) for e in sctx.entries)
        required = dscan.need + int(total_bytes * SPACE_MARGIN)
        sc = fs.check_space(dest, required, scan.max_size)
        report.meta.update(dest_free_bytes=dest.free, required_bytes=required, space_blocked=sc.space_blocked,
                           would_preserve_count=dscan.bak_count, would_preserve_bytes=dscan.bak_bytes)
        if sc.free_unknown:
            warn("대상의 여유 공간을 조회하지 못해 사전 검사를 건너뜁니다. 복사 중 공간이 부족하면 멈출 수 있습니다.")
        elif sc.space_blocked:
            keep = (f" 이미 있는 다른 파일 {dscan.bak_count}개({fmt_bytes(dscan.bak_bytes)})는 .bak 으로 남아 공간을 계속 차지합니다."
                    if dscan.bak_count else "")
            msg = (f"대상 드라이브의 여유 공간이 부족합니다. 필요 약 {fmt_bytes(required)}(새로 쓸 {fmt_bytes(dscan.need)} + 여유 5%), "
                   f"현재 여유 {fmt_bytes(dest.free or 0)}.{keep} 더 큰 드라이브를 쓰거나 정리한 뒤 다시 시작하세요.")
            if dry_run:
                warn(msg)
            else:
                raise TransferError(msg)

    if dry_run:
        if progress_info:
            progress_info(ProgressInfo("compare", 0, 0, 0, len(entries), message="복사될 파일을 비교하는 중입니다…",
                                       skipped=len(skipped_cloud)))
        return _dry_run(report, plain, skipped_cloud, scan, dst, runner, copy_mode, network, warn, dscan, sctx, junk_spec)

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
            if row.get("parts"):  # 분할 파일(--split-large)은 조각 파일을 각각 일반 파일로 본다(외장 드라이브에는 조각으로 들어 있다)
                folder = row["rel"].rpartition("/")[0]
                for part in row["parts"]:
                    manifest_map[f"{folder}/{part['name']}" if folder else part["name"]] = (part["size"], part["sha256"])
            else:
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
                n_pres, listed, resumed_run = _preserve_existing(plain, src, dst, journal, cancel, say, dscan, warn)
                report.meta.update(preserved_existing_count=n_pres, preserved_existing=listed, preserve_resumed=resumed_run)
                if n_pres:
                    warn(f"대상에 있던 같은 이름의 다른 파일 {n_pres}개를 .bak 으로 보존했습니다(덮어쓰지 않았습니다).")
                t0 = clock()
                tracker = ProgressTracker("copy", total_bytes, len(entries), clock=clock, slow_bps=kind_bps, slow_hint=kind_hint)

                def emit_copy() -> None:
                    now = clock()
                    with emit_lock:
                        if now - last_emit[0] < min_report_interval:
                            return
                        last_emit[0] = now
                    snap = tracker.snapshot()
                    if progress_info:
                        progress_info(snap)
                    say(format_info(snap))

                def on_line(line: str) -> None:
                    size = parse_copy_line(line)
                    if size is None:
                        return
                    tracker.add(size, 1)
                    tracker.set_current(parse_copy_path(line) or "")
                    tracker.note(OK)
                    emit_copy()

                say("복사 도구로 복사하는 중입니다(파일이 크면 오래 걸립니다)…")
                code, text, runs = _copy_with_robocopy(src, dst, log, skipped_cloud + (sctx.entries if sctx else []), runner, cancel, copy_mode,
                                                       retries if retries is not None else (10 if network else 3),
                                                       wait if wait is not None else (30 if network else 5), on_line, scan, alive, threads, junk_spec)
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
                if sctx is not None and sctx.entries:
                    say(f"4GB 넘는 파일 {len(sctx.entries)}개를 조각으로 나눠 복사하는 중입니다(파일이 크면 오래 걸립니다)…")
                    for entry in sctx.entries:
                        tracker.set_current(_join(str(dst), entry[0]))
                        r = _split_copy_entry(entry, scan.root, dst, state, sctx, strict=strict, chunk=chunk, cancel=cancel,
                                              stats=stats, on_bytes=lambda n: (tracker.add(n), emit_copy()), repair=False)
                        if r is not None and not r.ok and r.reason == failure.DISCONNECTED and not alive():
                            raise DriveDisconnected(DISCONNECTED_MESSAGE)
                        tracker.add(0, 1)
                        emit_copy()
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
                          detail_ok=detail_ok, kind_bps=kind_bps, kind_hint=kind_hint, warn=warn, sctx=sctx)
            if sctx is not None:
                _note_split_backups(report, sctx, warn)
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
    """대상에 새로 써야 하는 용량. _differs 와 같은 기준(크기 + 수정시각)으로 없거나 다른 파일의 새 크기만 센다(R1-3).
    다른 파일의 기존 내용은 .bak 으로 남으므로 공간이 돌아오지 않는다."""
    return _scan_dest(entries, dst, total_bytes).need


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
             warn: Callable[[str], None] = lambda m: None, dscan: DestScan | None = None,
             sctx: "SplitCtx | None" = None, junk_spec: "junk_mod.JunkSpec | None" = None) -> Report:
    """robocopy /L 은 목록 확인용, 리포트는 크기 비교로 만든다. 아무것도 쓰지 않는다. entries 는 분할 대상을 뺀 파일 목록이다."""
    args = base_args(Path(scan.root), dst, None, True, copy_mode, xf_extra=junk_spec.xf if junk_spec else (), xd=junk_spec.xd if junk_spec else ())
    skipped_names = list(skipped_cloud) + (sctx.entries if sctx else [])
    xf = sorted({os.path.basename(scan.abs_path(r)) for r, _s, _m in skipped_names})
    if xf and sum(len(n) + 3 for n in xf) <= MAX_XF_CHARS:
        args += xf  # 너무 길면 생략: 목록 확인용일 뿐 리포트는 별도로 만든다
    code, _ = _call_runner(runner, args, None)
    ok, notes = interpret_exit(code)
    report.meta.update(robocopy_exit=code, robocopy_ok=ok, robocopy_notes=notes)
    detail_ok = len(entries) <= MAX_DETAIL_RESULTS
    over = 0
    if dscan is None:
        dscan = _scan_dest(entries, dst, sum(e[1] for e in entries))
    for (rel, size, mtime), state in zip(entries, dscan.states):
        target = _join(str(dst), rel)
        status = {"n": WOULD_COPY, "s": SKIPPED_SAME, "d": WOULD_OVERWRITE}[state]
        res = FileResult(scan.abs_path(rel), target, status, size)
        if status == WOULD_OVERWRITE:
            over += 1
            if over <= MAX_DETAIL_RESULTS:
                res.backup = _bak_name(target)  # 실제 복사 때 기존 파일이 옮겨질 이름
        if status in (WOULD_COPY, SKIPPED_SAME) and not detail_ok:
            report.counts[status] = report.counts.get(status, 0) + 1
        else:
            report.results.append(res)
    if sctx is not None:  # 분할 대상: 조각 이름으로 새로 만든다(같은 이름의 조각이 있으면 실제 복사 때 .bak 으로 보존)
        for rel, size, mtime in sctx.entries:
            target = _join(str(dst), rel)
            n_parts = len(split.part_sizes(size, sctx.psz))
            same = sum(1 for i in range(1, n_parts + 1) if os.path.lexists(long_path(target + split.part_suffix(i))))
            res = FileResult(scan.abs_path(rel), target + split.part_suffix(1) + (f" 외 {n_parts - 1}개" if n_parts > 1 else ""),
                             WOULD_OVERWRITE if same else WOULD_COPY, size)
            if same:
                over += 1
                res.backup = f"같은 이름의 조각 {same}개는 덮어쓰기 전에 .bak 으로 보존됩니다(내용이 같으면 건너뜁니다)"
            report.results.append(res)
    report.meta["would_overwrite_count"] = over
    if over:
        warn(f"대상에 같은 이름의 다른 파일 {over}개가 이미 있습니다. 실제 복사 때는 덮어쓰기 전에 각 파일을 같은 폴더의 .bak 으로 보존합니다"
             f"(이름만 바꾸므로 시간은 거의 안 들지만, 기존 파일 {dscan.bak_count}개({fmt_bytes(dscan.bak_bytes)})가 .bak 으로 남아 "
             "공간을 계속 차지하고 새 파일 크기만큼 여유 공간이 더 필요합니다). 확인하고 진행하세요.")
    report.meta["would_preserve_bytes"] = dscan.bak_bytes
    return report


def _copy_with_robocopy(src: Path, dst: Path, log: Path, cloud: list, runner: Runner, cancel, copy_mode: str,
                        retries: int, wait: int, on_line, scan: ScanInfo, alive, threads: int = 1,
                        junk_spec: "junk_mod.JunkSpec | None" = None) -> tuple[int, str, int]:
    """robocopy 실행. (최악 종료코드, 오류 줄 모음, 실행 횟수). 클라우드 전용 파일은 /XF 로 제외."""
    jxf, jxd = (junk_spec.xf, junk_spec.xd) if junk_spec else ((), ())
    if not cloud:
        code, text = _call_runner(runner, base_args(src, dst, log, False, copy_mode, retries, wait, threads, jxf, jxd), on_line, alive)
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
        if junk_spec is not None:
            dirs[:] = [d for d in dirs if not junk_spec.skip_dir(d)]  # 정크 폴더는 내려가지 않는다(/XD 와 같은 기준)
        relp = os.path.relpath(root, src)
        key = "" if relp == "." else relp.replace(os.sep, "/")
        args = base_args(Path(root), dst / ("" if relp == "." else relp), log, False, copy_mode, retries, wait, threads, jxf, jxd)
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
                  kind_hint: str, warn, sctx: "SplitCtx | None" = None) -> None:
    """모든 파일을 검사한다(파일 단위 병렬). 상태 파일/해시 목록의 원본 해시는 다시 읽지 않고 재사용한다."""
    dst_root = str(dst)
    retry = mode == "retry-failed"
    verify_only = mode == "verify-only"
    resumed = [0]
    unmatched_manifest = dict(manifest_map) if manifest_map is not None else None
    split_rels = {e[0] for e in sctx.entries} if sctx is not None and sctx.entries else frozenset()

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

    def count_src(n: int) -> None:
        stats.add("src", n)
        on_bytes(n)

    def count_dst(n: int) -> None:
        stats.add("dst", n)
        on_bytes(n)

    def task(entry):
        rel, size, mtime = entry
        s_path, d_path = _join(scan.root, rel), _join(dst_root, rel)
        tracker.set_current(d_path)
        if rel in split_rels:
            return _verify_split_entry(entry, scan.root, dst_root, state, sctx, stats, strict=strict, retry=retry,
                                       verify_only=verify_only, check_dest=check_dest, chunk=chunk, cancel=cancel,
                                       reuse_hash=reuse_hash, count_src=count_src, count_dst=count_dst)
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
            res.error = "상태 파일의 검사 기록과 원본이 어긋남(크기/수정시각은 동일)"
        if not was_resumed:
            if res.status == PENDING_VERIFY:
                state.record(rel, size, mtime, res.sha256, False, "pending")
            elif res.status == VERIFIED:
                state.record(rel, size, mtime, res.sha256, True)
            else:
                state.record(rel, size, mtime, res.sha256, False, res.reason or res.status)
        if writer is not None:
            if res.sha256:
                parts = sctx.parts.get(rel) if sctx is not None and res.status in (VERIFIED, PENDING_VERIFY) else None
                writer.write_row(rel, size, mtime, res.sha256, parts)
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
                                             error="검사 목록에는 있지만 원본(외장 드라이브)에 이 파일이 없습니다. 복사가 덜 됐을 수 있습니다."))
    report.meta["resumed_skipped"] = resumed[0]
    report.meta["trusted_state_files"] = resumed[0]  # 상태 파일의 이전 검사 결과를 믿고 다시 읽지 않은 파일 수(내용까지 다시 보려면 --strict)
    emit(True)


def _state_conflict(state: State, rel: str, size: int, mtime: int, sha256: str) -> bool:
    """상태 파일이 '검사 완료'로 기록한 해시가 지금 원본 해시와 다른데 크기/수정시각은 같은 경우."""
    e = state.files.get(rel)
    return bool(e and e[3] and e[2] != sha256 and e[0] == size and e[1] == mtime)


# --------------------------------------------------------------------------- 4GB 분할 파일(--split-large)

@dataclass
class SplitCtx:
    """이번 실행의 분할 정보. entries 는 조각으로 나눌 (상대 경로, 크기, 수정시각) 목록."""
    psz: int
    entries: list = field(default_factory=list)
    bytes: int = 0
    parts_total: int = 0
    copy_errors: dict[str, FileResult] = field(default_factory=dict)
    parts: dict[str, list[dict]] = field(default_factory=dict)  # rel -> [{name,size,sha256}]
    backups: list[tuple[str, str]] = field(default_factory=list)
    written: int = 0
    kept: int = 0


def _split_copy_entry(entry, src_root: str, dst, state: State, sctx: SplitCtx, *, strict: bool, chunk: int | None, cancel,
                      stats: IoStats, on_bytes, repair: bool) -> split.SplitCopy:
    """분할 파일 하나를 조각으로 복사한다(원본은 읽기만). 끝난 조각마다 상태 파일에 '복사 끝, 검사 전'으로 기록한다.

    repair=False(기본): 상태 파일이 '검사 끝'으로 적은 조각, 또는 크기+수정시각이 같은 조각은 건너뛴다(내용 검사는 검사 단계에서).
    repair=True/strict: 대상 조각을 읽어 해시를 원본과 비교하고 다른 조각만 다시 쓴다(옛 조각은 .bak)."""
    rel, size, mtime = entry
    s_path, d_path = _join(src_root, rel), _join(str(dst), rel)
    deep = strict or repair

    def probe(idx: int, plen: int, target: str):
        if deep:
            try:
                if os.stat(long_path(target)).st_size != plen:
                    return None, False
                return sha256_file(Path(target), chunk, cancel, lambda n: stats.add("dst", n)), False
            except Cancelled:
                raise
            except OSError:
                return None, False
        h = state.is_verified(split.state_key(rel, idx), plen, mtime, target)
        if h:
            return h, False
        try:
            st = os.stat(long_path(target))
        except OSError:
            return None, False
        return None, st.st_size == plen and abs(st.st_mtime_ns - mtime) <= MTIME_TOLERANCE_NS

    def on_part(idx: int, plen: int, digest: str, written: bool) -> None:
        state.record(split.state_key(rel, idx), plen, mtime, digest, False, "pending")

    try:
        os.makedirs(long_path(os.path.dirname(d_path)), exist_ok=True)
    except OSError as e:
        f = failure.classify(e, d_path)
        r = split.SplitCopy(error=f"{f.text} ({os.path.dirname(d_path)})", reason=f.code)
    else:
        r = split.copy_split(s_path, d_path, size, mtime, psz=sctx.psz, chunk=chunk, cancel=cancel, on_bytes=on_bytes,
                             stats=stats, probe=probe, on_part=on_part)
    if r.ok:
        sctx.parts[rel] = r.parts
        sctx.copy_errors.pop(rel, None)
        state.record(rel, size, mtime, r.sha256, False, "pending")
        sctx.written += r.written
        sctx.kept += r.kept
    else:
        sctx.copy_errors[rel] = FileResult(s_path, d_path, FAILED, size, reason=r.reason, error=r.error)
    sctx.backups += r.backups
    return r


def _verify_split_entry(entry, src_root: str, dst_root: str, state: State, sctx: SplitCtx, stats: IoStats, *, strict: bool,
                        retry: bool, verify_only: bool, check_dest: bool, chunk: int | None, cancel, reuse_hash, count_src,
                        count_dst) -> tuple[FileResult, bool]:
    """분할 파일 하나의 검사: 대상 조각이 모두 있고(크기), 조각별 해시와(이어 읽은) 전체 해시가 원본과 같은지 본다.
    원본 해시는 상태 파일/해시 목록/방금 복사하며 구한 값을 쓰고, 없으면 원본을 한 번 읽어 구한다."""
    rel, size, mtime = entry
    s_path, d_path = _join(src_root, rel), _join(dst_root, rel)
    err = sctx.copy_errors.get(rel)
    if err is not None:
        return err, False
    sizes = split.part_sizes(size, sctx.psz)
    names = [(d_path + split.part_suffix(i), n) for i, n in enumerate(sizes, 1)]
    keys = [split.state_key(rel, i) for i in range(1, len(sizes) + 1)]
    prior = state.files.get(rel)
    res = FileResult(s_path, d_path, FAILED, size)

    def parts_from(per: list[str]) -> list[dict]:
        return [{"name": os.path.basename(p), "size": n, "sha256": h} for (p, n), h in zip(names, per)]

    if not strict and not verify_only and prior and prior[3] and prior[0] == size and prior[1] == mtime:
        hs = [state.is_verified(k, n, mtime, p) for k, (p, n) in zip(keys, names)]
        if all(hs):  # 이전 실행에서 검사가 끝난 파일: 대상은 크기/수정시각만 가볍게 확인
            sctx.parts[rel] = parts_from(hs)
            res.status, res.sha256 = VERIFIED, prior[2]
            return res, True
    if retry and not (prior and (prior[3] or prior[4] == "pending")):
        r = _split_copy_entry(entry, src_root, dst_root, state, sctx, strict=strict, chunk=chunk, cancel=cancel, stats=stats,
                              on_bytes=count_src, repair=True)
        if not r.ok:
            return sctx.copy_errors[rel], False

    def expected() -> tuple[str, list[str]] | FileResult:
        whole = reuse_hash(rel, size, mtime)
        recs = [state.files.get(k) for k in keys]
        if whole and not strict and all(r and r[0] == n and r[1] == mtime for r, n in zip(recs, sizes)):
            return whole, [r[2] for r in recs]
        try:
            out = split.hash_ranges(s_path, sizes, chunk=chunk, cancel=cancel, on_bytes=count_src)
            stats.file_read("src")
            return out
        except Cancelled:
            raise
        except split.SourceChangedError:
            res.reason, res.error = "source_changed", "원본 파일의 크기가 조사한 뒤 바뀌었습니다. 원본을 쓰는 프로그램을 닫고 다시 실행하세요"
            return res
        except OSError as e:
            f = failure.classify(e, s_path)
            res.reason, res.error = f.code, f"{f.text} ({s_path})"
            return res

    def check(whole: str, per: list[str]) -> FileResult:
        out = FileResult(s_path, d_path, FAILED, size, whole)
        where = d_path
        try:
            for i, (p, n) in enumerate(names, 1):
                where = p
                try:
                    st = os.stat(long_path(p))
                except FileNotFoundError:
                    out.status, out.reason = MISSING, failure.MISSING_DEST
                    out.error = (f"조각 {i:03d}/{len(names):03d}({os.path.basename(p)})이 대상에 없습니다. "
                                 "같은 명령을 다시 실행하면 이어받습니다(--retry-failed 도 가능)")
                    return out
                if not os.path.isfile(long_path(p)) or st.st_size != n:
                    out.status, out.reason = MISMATCH, failure.SIZE_MISMATCH
                    out.error = f"조각 {i:03d}({os.path.basename(p)})의 크기가 원본과 다릅니다(원본 {n}바이트, 여기 {st.st_size}바이트)"
                    return out
            if not check_dest:
                out.status = PENDING_VERIFY
                return out
            whole_d, per_d = split.hash_parts([p for p, _n in names], chunk=chunk, cancel=cancel, on_bytes=count_dst)
            stats.file_read("dst")
            for i, (h_d, h_s) in enumerate(zip(per_d, per), 1):
                if h_d != h_s:
                    out.status, out.reason = MISMATCH, failure.MISMATCH
                    out.error = (f"조각 {i:03d}({os.path.basename(names[i - 1][0])})의 내용이 원본과 다릅니다(SHA-256 불일치). "
                                 "--strict 로 다시 실행하면 어긋난 조각만 원본에서 다시 복사합니다")
                    return out
            if whole_d != whole:
                out.status, out.reason = MISMATCH, failure.MISMATCH
                out.error = failure.REASONS[failure.MISMATCH].text + " (조각을 이어 붙인 전체 SHA-256 불일치)"
                return out
            out.status = VERIFIED
        except Cancelled:
            raise
        except OSError as e:
            bad = getattr(e, "filename", None) or where
            f = failure.classify(e, bad)
            out.status, out.reason, out.error = FAILED, f.code, f"{f.text} ({bad})"
        return out

    got = expected()
    if isinstance(got, FileResult):
        return got, False
    whole, per = got
    res = check(whole, per)
    if res.status in (MISMATCH, MISSING) and ((strict and res.status == MISMATCH) or retry):
        r = _split_copy_entry(entry, src_root, dst_root, state, sctx, strict=strict, chunk=chunk, cancel=cancel, stats=stats,
                              on_bytes=count_src, repair=True)
        if not r.ok:
            return sctx.copy_errors[rel], False
        whole, per = r.sha256, [p["sha256"] for p in r.parts]
        res = check(whole, per)
        if res.status == VERIFIED:
            res.backup = r.backups[0][1] if r.backups else ""
    sctx.parts[rel] = parts_from(per)
    for k, (_p, n), h in zip(keys, names, per):
        if res.status == VERIFIED:
            state.record(k, n, mtime, h, True)
        elif res.status == PENDING_VERIFY:
            state.record(k, n, mtime, h, False, "pending")
    return res, False


def _note_split_backups(report: Report, sctx: SplitCtx, warn: Callable[[str], None]) -> None:
    """조각 덮어쓰기 전 .bak 보존과 조각 개수/크기 요약을 리포트 meta 에 남긴다."""
    if sctx.backups:
        n = report.meta.get("preserved_existing_count", 0) + len(sctx.backups)
        listed = list(report.meta.get("preserved_existing", []))
        for target, bak in sctx.backups:
            if len(listed) < MAX_PRESERVED_LISTED:
                listed.append({"file": target, "backup": bak})
        report.meta.update(preserved_existing_count=n, preserved_existing=listed)
        warn(f"대상에 있던 같은 이름의 다른 조각 {len(sctx.backups)}개를 .bak 으로 보존했습니다(덮어쓰지 않았습니다).")
    report.meta.update(split_written_parts=sctx.written, split_kept_parts=sctx.kept)
