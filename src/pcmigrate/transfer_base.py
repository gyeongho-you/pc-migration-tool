"""transfer 분해 모듈: 공용 상수/예외/타입/작은 경로 보조 함수(다른 transfer_* 모듈이 모두 가져다 쓰는 맨 아래 층).

공개 이름은 transfer.py 파사드가 그대로 다시 내보낸다.
"""
from __future__ import annotations

import os
import stat as _stat
from pathlib import Path
from typing import Callable

from . import failure
from .fs import long_path

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


# st_file_attributes 비트(기존 이름 유지)
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000


FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000


MTIME_TOLERANCE_NS = 2_000_000_000  # FAT 계열 2초 해상도


MAX_XF_CHARS = 6000  # dry-run 목록용 /XF 이름 합계 상한(명령줄 길이 제한 회피)


# runner(args) -> (returncode, output). 줄 단위 진행을 받는 runner 는 supports_lines=True 이고 runner(args, on_line) 형태.
Runner = Callable[..., tuple[int, str]]


AttrGetter = Callable[[Path], int]


class TransferError(ValueError):
    """전송을 시작할 수 없는 입력/설정/환경 오류. 메시지는 사용자에게 그대로 보여 줄 수 있다."""


class DriveDisconnected(TransferError):
    """복사/검사 중 대상(또는 원본) 드라이브가 분리되었다. 다시 연결하고 같은 설정으로 실행하면 이어받는다."""


DISCONNECTED_MESSAGE = "드라이브가 분리되었습니다. 다시 연결하면 이어받습니다(같은 폴더/설정으로 다시 실행하세요)."


def _is_within(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(child)).relative_to(os.path.realpath(parent))
        return True
    except ValueError:
        return False


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


def _first(path: Path):
    try:
        with os.scandir(long_path(path)) as it:
            for e in it:
                if not e.name.startswith(".pcmigrate-"):
                    yield e
                    return
    except OSError:
        return
