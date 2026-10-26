"""pcmigrate-gui.exe(창 모드, 콘솔 없음)의 명령행 인자 처리: --version / --help 는 창을 열지 않고 메시지 상자로 알린 뒤 끝낸다(R2-7).

gui/ 폴더 밖에 둔 이유: GUI 화면 코드와 무관한 진입 처리이고, tkinter 를 불러오지 않고도 시험할 수 있다.
PCMIGRATE_NO_DIALOG=1 이면 메시지 상자 대신 아무것도 띄우지 않고(표준 출력이 있으면 거기에 쓰고) 끝낸다(빌드 스모크 시험용).
"""
from __future__ import annotations

import os
import sys
from typing import Callable

from . import __version__

VERSION_FLAGS = ("--version", "-V")
HELP_FLAGS = ("--help", "-h", "/?")


def _help_text() -> str:
    return (f"PC 이전 도우미 {__version__}\n\n이 프로그램(pcmigrate-gui.exe)은 그래픽 화면입니다. 그냥 실행하면 화면이 열립니다.\n"
            "옵션: --version(버전 확인), --help(이 안내)\n\n명령행(검사/전송/패키지)은 같은 폴더의 pcmigrate.exe 를 쓰세요. "
            "예: pcmigrate.exe --help")


def message_box(text: str, title: str = "PC 이전 도우미") -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # MB_ICONINFORMATION
    except (AttributeError, OSError):
        print(text)


def handle_gui_args(argv: list[str], notify: Callable[[str], None] | None = None, out=None) -> bool:
    """--version/--help 를 처리했으면 True(호출자는 화면을 열지 않고 종료). 아니면 False."""
    if any(a in VERSION_FLAGS for a in argv):
        text = f"PC 이전 도우미 {__version__}"
    elif any(a in HELP_FLAGS for a in argv):
        text = _help_text()
    else:
        return False
    out = out if out is not None else sys.stdout
    if os.environ.get("PCMIGRATE_NO_DIALOG"):
        if out is not None:
            print(text, file=out)
    elif notify is not None:
        notify(text)
    elif out is not None and getattr(out, "isatty", lambda: False)():
        print(text, file=out)
    else:
        message_box(text)
    return True
