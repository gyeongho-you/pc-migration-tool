"""오류 로그: 예기치 않은 오류의 전체 내용(traceback)을 파일에 남겨, 사용자가 화면을 베끼지 않고 그 파일을 전달할 수 있게 한다.

- 위치: %LOCALAPPDATA%\\pcmigrate\\logs\\errors-YYYYMMDD.log (envpaths.expand_env 로 해석). 쓸 수 없으면 %TEMP%\\pcmigrate-logs.
- 한 번의 오류가 한 블록: 시각, 도구 버전, 명령/화면 이름, 예외 종류와 메시지, 전체 traceback.
- 설치 프로그램 목록이나 파일 내용은 쓰지 않는다(예외 메시지/traceback 에 들어 있는 파일 경로는 포함될 수 있다).
- 회전: 파일 하나는 5MB 까지(넘으면 errors-YYYYMMDD.1.log …), 파일은 최근 10개만 남긴다.
- 로그를 남기지 못해도 예외를 일으키지 않는다(오류 처리 중에 또 오류가 나면 안 된다).
- 한 블록은 메시지 8KB, traceback 64KB 까지만 쓴다(넘으면 줄이고 표시). 사용자 홈 경로는 %USERPROFILE% 로 바꿔 저장한다(이름이 새지 않게).
- 쓸 수 있는지 확인(probe)은 폴더마다 한 번만 하고, 블록은 O_APPEND 로 한 번에 쓴다. 같은 오류가 짧은 시간에 반복되면 한 번만 쓰고
  나머지는 개수만 센다(중복 제거), 1분에 20블록을 넘으면 더 쓰지 않는다(속도 제한).
"""
from __future__ import annotations

import os
import re
import tempfile
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .envpaths import expand_env

DEFAULT_PRIMARY_DIR = "%LOCALAPPDATA%\\pcmigrate\\logs"
PRIMARY_DIR = DEFAULT_PRIMARY_DIR  # 테스트가 실제 로그 폴더를 건드리지 않도록 바꿔 끼울 수 있다
FALLBACK_NAME = "pcmigrate-logs"
MAX_FILES = 10
MAX_BYTES = 5 * 1024 * 1024
BLOCK_MARK = "=== "
PATH_NOTE = "이 로그에는 파일 경로가 들어 있을 수 있습니다. 다른 사람에게 보내기 전에 확인하세요."
MAX_MESSAGE = 8 * 1024
MAX_TRACEBACK = 64 * 1024
DEDUP_SECONDS = 30.0
BURST_MAX = 20
BURST_SECONDS = 60.0
HOME_TOKEN = "%USERPROFILE%"

_WRITABLE: dict[str, bool] = {}  # 폴더 -> 쓸 수 있는지(프로세스 동안 한 번만 확인)
_RECENT: dict[str, list] = {}    # 오류 서명 -> [마지막으로 쓴 시각, 그 뒤 생략한 횟수, 마지막으로 쓴 파일]
_BURST: deque = deque()          # 최근에 쓴 시각들


def reset_throttle() -> None:
    """중복 제거/속도 제한/쓰기 확인 캐시를 비운다(시험용)."""
    _WRITABLE.clear()
    _RECENT.clear()
    _BURST.clear()


def _writable(d: Path) -> bool:
    key = str(d)
    cached = _WRITABLE.get(key)
    if cached is not None:
        return cached
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        ok = True
    except OSError:
        ok = False
    _WRITABLE[key] = ok
    return ok


def _home_forms() -> list[str]:
    homes = {os.path.expanduser("~"), os.environ.get("USERPROFILE", ""), expand_env(HOME_TOKEN)}
    forms = set()
    for h in homes:
        if h and "%" not in h and len(h) > 3:
            forms.update({h, h.replace("\\", "/"), "\\\\?\\" + h})
            # 예외 메시지는 경로를 repr 로 찍어 백슬래시가 두 배로 보인다(PermissionError 의 '\\\\?\\C:\\Users\\이름\\...')
            forms.update({f.replace("\\", "\\\\") for f in (h, "\\\\?\\" + h)})
    return sorted(forms, key=len, reverse=True)


def tokenize_home(text: str) -> str:
    """텍스트 속 사용자 홈 경로(대소문자 무시, / 표기와 긴 경로 접두 포함)를 %USERPROFILE% 로 바꾼다."""
    for h in _home_forms():
        text = re.sub(re.escape(h), lambda _m: HOME_TOKEN, text, flags=re.I)
    return text


def _clip(text: str, limit: int, head: int | None = None) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4 if head is None else head
    cut = len(text) - limit
    return f"{text[:head]}\n… (너무 길어 {cut:,}자 생략) …\n{text[-(limit - head):]}"


def log_dir() -> Path:
    """로그 폴더. %LOCALAPPDATA% 를 해석할 수 없거나 쓸 수 없으면 임시 폴더로 대신한다."""
    primary = expand_env(PRIMARY_DIR)
    if "%" not in primary and os.path.isabs(primary) and _writable(Path(primary)):
        return Path(primary)
    fallback = Path(tempfile.gettempdir()) / FALLBACK_NAME
    _writable(fallback)
    return fallback


_NAME = re.compile(r"errors-(\d{8})(?:\.(\d+))?\.log$")


def _order(p: Path) -> tuple[str, int]:
    m = _NAME.match(p.name)
    return (m.group(1), int(m.group(2) or 0)) if m else ("", 0)


def _log_files(d: Path) -> list[Path]:
    """오래된 것부터(파일 이름의 날짜, 같은 날이면 회전 번호 순)."""
    try:
        return sorted((p for p in d.glob("errors-*.log") if _NAME.match(p.name)), key=_order)
    except OSError:
        return []


def _current_file(d: Path, now: datetime) -> Path:
    base = f"errors-{now:%Y%m%d}"
    path = d / f"{base}.log"
    n = 0
    while path.exists() and path.stat().st_size >= MAX_BYTES:
        n += 1
        path = d / f"{base}.{n}.log"
    return path


def _prune(d: Path) -> None:
    files = _log_files(d)
    for old in files[:-MAX_FILES]:
        try:
            old.unlink()
        except OSError:
            pass


def format_block(exc: BaseException, context: str, tb: str | None = None, now: datetime | None = None) -> str:
    """로그 한 블록(파일에 쓰는 것과 같은 텍스트: '오류 내용 복사' 에도 쓴다)."""
    now = now or datetime.now()
    if tb is None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    head = f"{BLOCK_MARK}{now:%Y-%m-%d %H:%M:%S} | pcmigrate {__version__} | 실행: {context or '(알 수 없음)'} ==="
    msg = _clip(f"{exc}", MAX_MESSAGE, MAX_MESSAGE // 2)
    return tokenize_home(f"{head}\n예외: {type(exc).__name__}: {msg}\n{_clip(tb.rstrip(), MAX_TRACEBACK)}\n\n")


def _signature(exc: BaseException, context: str) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    last = frames[-1] if frames else None
    where = f"{last.filename}:{last.lineno}" if last else ""
    return f"{type(exc).__name__}|{str(exc)[:200]}|{where}|{context}"


def record(exc: BaseException, context: str = "", tb: str | None = None, directory: Path | None = None,
           now: datetime | None = None) -> Path | None:
    """오류 한 건을 로그에 덧붙이고 로그 파일 경로를 돌려준다. 실패하면 None(예외를 던지지 않는다)."""
    try:
        now = now or datetime.now()
        d = Path(directory) if directory is not None else log_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = _current_file(d, now)
        sig = _signature(exc, context)
        prev = _RECENT.get(sig)
        if prev is not None and timedelta(0) <= now - prev[0] < timedelta(seconds=DEDUP_SECONDS) and prev[2] == path:
            prev[1] += 1  # 같은 오류가 곧바로 또 났다: 블록을 늘리지 않고 개수만 센다
            return path
        while _BURST and now - _BURST[0] >= timedelta(seconds=BURST_SECONDS):
            _BURST.popleft()
        if len(_BURST) >= BURST_MAX:
            return path  # 속도 제한: 오류가 폭주하면 로그 폴더를 채우지 않는다
        block = format_block(exc, context, tb, now)
        if prev is not None and prev[1]:
            block = block.replace("\n", f"\n(직전과 같은 오류 {prev[1]}건은 기록을 생략했습니다)\n", 1)
        data = block.encode("utf-8", errors="replace")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o666)
        try:
            os.write(fd, data)  # 한 번의 write 로 블록 전체를 붙인다(다른 프로세스의 블록과 섞이지 않게)
        finally:
            os.close(fd)
        _RECENT[sig] = [now, 0, path]
        _BURST.append(now)
        _prune(d)
        return path
    except Exception:  # noqa: BLE001 - 로그 실패가 원래 오류를 가리면 안 된다
        return None


def last_error(directory: Path | None = None) -> tuple[Path, str] | None:
    """가장 최근 오류 한 건 (파일, 블록 텍스트). 없으면 None."""
    try:
        d = Path(directory) if directory is not None else log_dir()
        for path in reversed(_log_files(d)):
            text = path.read_text(encoding="utf-8", errors="replace")
            blocks = [b for b in text.split("\n" + BLOCK_MARK) if b.strip()]
            if not blocks:
                continue
            last = blocks[-1]
            if not last.startswith(BLOCK_MARK):
                last = BLOCK_MARK + last
            return path, last.strip()
    except OSError:
        return None
    return None


def summarize_block(block: str) -> list[str]:
    """블록에서 시각/실행/예외 종류와 메시지만 뽑은 요약(traceback 제외)."""
    lines = block.splitlines()
    out = []
    if lines:
        head = lines[0]
        head = head[len(BLOCK_MARK):] if head.startswith(BLOCK_MARK) else head
        out.append(head[:-4] if head.endswith(" ===") else head)
    out += [l for l in lines[1:3] if l.startswith("예외:")]
    return out
