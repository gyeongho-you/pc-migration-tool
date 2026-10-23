"""시작 화면: 구 PC / 신 PC / 대용량 복사 중 선택."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import state as S
from .. import texts as T
from .common import OK_FG, WARN_FG, BaseView, autowrap


class StartView(BaseView):
    name = S.START

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.header(T.START_HEADING)
        intro = ttk.Label(self, text=T.START_INTRO, justify="left")
        intro.pack(anchor="w", fill="x", pady=(2, 6))
        autowrap(intro)
        # 라이선스 안내는 눈에 띄는 색 상자 + 아이콘 + 문구(색에만 의존하지 않음)
        self.license_box = tk.Label(self, text=T.LICENSE_BANNER, anchor="w", justify="left", bg="#fff4d6", fg=WARN_FG,
                                    padx=10, pady=6, font=("TkDefaultFont", 10, "bold"))
        self.license_box.pack(fill="x", pady=(0, 8))
        autowrap(self.license_box, 40)
        grid = ttk.Frame(self)
        grid.pack(fill="x", pady=6)
        self.buttons: list[ttk.Button] = []
        for i, (title, desc, btn_text, target) in enumerate(T.START_CHOICES):
            box = ttk.LabelFrame(grid, text=title, padding=10)
            box.grid(row=0, column=i, sticky="nsew", padx=6)
            grid.columnconfigure(i, weight=1, uniform="start")
            lbl = ttk.Label(box, text=desc, justify="left")
            lbl.pack(anchor="w", fill="x", pady=(0, 8))
            autowrap(lbl, 16)
            b = ttk.Button(box, text=btn_text, command=lambda t=target: self.c.go(t))
            b.pack(anchor="e")
            self.buttons.append(b)
        self.primary = self.buttons[0]
        notes = ttk.LabelFrame(self, text=T.SAFETY_TITLE, padding=10)
        notes.pack(fill="x", pady=12)
        for line in T.SAFETY_LINES:
            lbl = ttk.Label(notes, text="• " + line, justify="left")
            lbl.pack(anchor="w", fill="x")
            autowrap(lbl, 40)
        self.finish()

    def on_show(self) -> None:
        self.buttons[0].focus_set()
