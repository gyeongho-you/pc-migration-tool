"""✔ ✖ ⚠ 같은 그림 글자를 현재 글꼴이 그릴 수 있는지 확인한다(tkinter 만 사용, texts.py 보다 먼저 쓸 수 있도록 다른 모듈에 의존하지 않는다).

그릴 수 없으면(네모로 보이는 환경) texts.py 가 [OK] [X] [!] 같은 글자 표시로 바꾼다.
환경변수 PCMIGRATE_TEXT_ICONS 는 자동 판단보다 우선하는 강제 옵션이다: 1 = 항상 글자 표시, 0 = 항상 그림 글자.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

SAMPLE = "✔✖⚠☑☐"  # 화면 문구에 쓰는 그림 글자
_MISSING_PROBES = ("\uffff", "\ufffe")  # 어떤 글꼴에도 없는 문자: 이 문자와 같은 폭이면 '없는 글자 네모'로 본다
_auto: bool | None = None  # 마지막 자동 판단 결과(None = 아직 판단 안 함)


def missing_glyphs(measure: Callable[[str], int], chars: str = SAMPLE) -> list[str]:
    """measure(문자) = 글꼴에서 그 문자의 폭(픽셀). 폭이 0 이하이거나 '없는 글자'의 폭과 같으면 그릴 수 없는 글자로 본다."""
    probes = {measure(p) for p in _MISSING_PROBES}
    out = []
    for ch in chars:
        w = measure(ch)
        if w <= 0 or w in probes:
            out.append(ch)
    return out


def glyphs_supported(font: tkfont.Font, chars: str = SAMPLE) -> bool:
    try:
        return not missing_glyphs(font.measure, chars)
    except tk.TclError:
        return True  # 판단하지 못하면 그림 글자를 그대로 쓴다


def forced() -> bool | None:
    """환경변수로 강제한 값: True = 글자 표시, False = 그림 글자, None = 강제 안 함."""
    v = os.environ.get("PCMIGRATE_TEXT_ICONS", "").strip()
    return True if v == "1" else False if v == "0" else None


def want_text_icons(font: tkfont.Font | None) -> bool:
    """글자 표시([OK] [X] [!])를 써야 하는가: 강제 옵션이 있으면 그것, 없으면 글꼴 검사 결과."""
    global _auto
    f = forced()
    if f is not None:
        return f
    _auto = False if font is None else not glyphs_supported(font)
    return _auto


def auto_result() -> bool | None:
    return _auto
