"""탐색기로 '보기만' 하기(stage34). 파일을 열거나 실행하지 않는다.

- 폴더: 그 폴더를 탐색기로 연다.  [explorer, <폴더>]
- 파일: 탐색기에서 선택된 상태로 부모 폴더를 연다.  [explorer, /select,<파일>]
- 호출은 항상 인자 목록(shell=False)으로만 한다. 경로를 셸 문자열로 합치지 않는다(명령 주입 방지). os.startfile 은 쓰지 않는다.
- 이 PC 의 '존재하는 로컬 디스크(고정/이동식) 경로'만 받는다. 공유 폴더(UNC)·장치 경로(\\\\?\\ 등)·상대 경로·명령 구분자가 든 경로는 거절한다.

탐색기 호출은 explorer_open() 한 곳으로 모았고, 화면/컨트롤러는 이 함수를 바꿔 끼울 수 있게 쓴다(시험에서는 가짜).
"""
from __future__ import annotations

import ntpath
import os
import re
import subprocess
import sys
from typing import Callable

EXPLORER = "explorer.exe"
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_BAD_CHARS = re.compile(r"[\x00-\x1f\"<>|?*&;^%`]")  # 명령 구분자·리다이렉션·환경변수·와일드카드·제어 문자
_MAX_LEN = 1000
_DRIVE_FIXED, _DRIVE_REMOVABLE = 3, 2


def clean_local_path(path: object) -> str | None:
    """안전한 절대 로컬 경로면 정규화한 경로(역슬래시)를, 아니면 None. 파일 시스템은 보지 않는다."""
    if not isinstance(path, str) or not path or path != path.strip() or len(path) > _MAX_LEN:
        return None
    if path.startswith(("\\\\", "//")):  # 공유 폴더(UNC)와 \\?\ \\.\ 장치 경로
        return None
    if not _DRIVE.match(path) or _BAD_CHARS.search(path) or ":" in path[2:]:  # 상대 경로, 명령 구분자, 대체 데이터 스트림
        return None
    if any(part == ".." for part in re.split(r"[\\/]+", path)):
        return None
    return ntpath.normpath(path)


def _windows_drive_type(root: str) -> int:
    try:
        import ctypes
        return int(ctypes.windll.kernel32.GetDriveTypeW(root))  # type: ignore[attr-defined]
    except (AttributeError, OSError, ImportError):
        return _DRIVE_FIXED if sys.platform != "win32" else 0


def is_local_disk(path: str, drive_type_fn: Callable[[str], int] | None = None) -> bool:
    """고정/이동식 디스크 위의 경로인가(네트워크 드라이브·CD·알 수 없는 드라이브는 거절)."""
    root = ntpath.splitdrive(path)[0] + "\\"
    return (drive_type_fn or _windows_drive_type)(root) in (_DRIVE_FIXED, _DRIVE_REMOVABLE)


def openable(path: object, select: bool = False, *, exists: Callable[[str], bool] = os.path.exists,
             isdir: Callable[[str], bool] = os.path.isdir, drive_type_fn: Callable[[str], int] | None = None) -> bool:
    """이 경로를 탐색기로 보여 줄 수 있는가. select=False 는 존재하는 폴더만(파일은 열지 않는다), select=True 는 존재하는 파일/폴더."""
    p = clean_local_path(path)
    if p is None or not is_local_disk(p, drive_type_fn):
        return False
    try:
        return exists(p) if select else isdir(p)
    except OSError:
        return False


def explorer_open(path: object, select: bool = False, *, popen: Callable = subprocess.Popen, exists: Callable[[str], bool] = os.path.exists,
                  isdir: Callable[[str], bool] = os.path.isdir, drive_type_fn: Callable[[str], int] | None = None) -> bool:
    """탐색기로 보여 준다. 보여 줬으면 True(탐색기를 시작했다는 뜻), 경로가 안전하지 않거나 없으면 False.

    select=False: 폴더를 연다(파일 경로면 거절 — 파일은 절대 열지 않는다). select=True: 탐색기에서 선택된 상태로 부모 폴더를 연다.
    """
    if not openable(path, select, exists=exists, isdir=isdir, drive_type_fn=drive_type_fn):
        return False
    p = clean_local_path(path)
    assert p is not None
    args = [EXPLORER, "/select," + p] if select else [EXPLORER, p]
    try:
        popen(args, shell=False)  # 인자 목록으로만: 경로가 셸을 거치지 않는다
    except OSError:
        return False
    return True
