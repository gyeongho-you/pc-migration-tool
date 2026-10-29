"""transfer 분해 모듈: robocopy 실행: runner(실행/취소/드라이브 분리 감지), 인자 조립/검증, 종료 코드 해석, 출력 줄 파싱.

공개 이름은 transfer.py 파사드가 그대로 다시 내보낸다.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .cancel import Cancelled, checkpoint
from .transfer_base import DISCONNECTED_MESSAGE, DriveDisconnected, OWN_PATTERN, Runner, TransferError

# 원본/대상을 삭제·이동·동기화(삭제)할 수 있는 옵션. 접두 일치로 /MOV:, /MOVE 모두 차단.
FORBIDDEN_PREFIXES = ("/MIR", "/PURGE", "/MOV")


COPY_MODES = ("restartable", "normal", "unbuffered")


_COPY_LINE = re.compile(r"^\t[^\t]*\t+\s*(\d+)\t(.+?)\s*$")  # robocopy /BYTES /NJH /NJS /NDL 의 파일 줄(언어와 무관)


_ERR_LINE = re.compile(r"(?:ERROR|오류)\s+\d+\s+\(0x", re.I)


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 창 모드 exe 에서 콘솔 창이 번쩍이지 않게


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
            try:
                checkpoint()
            except Cancelled:
                _stop(proc, reader)
                raise
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
    """robocopy 를 멈춘다: terminate 후 3초 안에 안 끝나면 kill 하고 끝까지 기다린다(좀비/잠긴 파일을 남기지 않는다)."""
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    except OSError:
        pass
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


def _call_runner(runner: Runner, args: list[str], on_line: Callable[[str], None] | None,
                 alive: Callable[[], bool] | None = None) -> tuple[int, str]:
    validate_args(args)
    if getattr(runner, "supports_lines", False):
        return runner(args, on_line, alive)
    return runner(args)
