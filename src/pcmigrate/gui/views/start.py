"""시작 화면: ① 쓰던 PC에서 준비 / ② 내 파일 옮기기 / ③ 새 PC에서 설치하고 가져오기 중 선택. 큰 버튼 3개 + 전체 순서 보기."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import state as S
from .. import texts as T
from .common import (BODY_FONT, INFO_BORDER, INFO_FG, MUTED_FG, SUB_FONT, WARN_BG, WARN_FG, ActionButton, BaseView, Collapsible,
                     ScrollBody, add_tooltip, autowrap)


class StartView(BaseView):
    name = S.START

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.header(T.START_HEADING, roadmap=False)
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        intro = ttk.Label(body, text=T.START_INTRO, justify="left")  # 소개는 2줄 이내
        intro.pack(anchor="w", fill="x", pady=(2, 8))
        autowrap(intro)
        grid = ttk.Frame(body)
        grid.pack(fill="x", pady=4)
        self.buttons: list[ActionButton] = []
        self.auto_buttons: list[ActionButton] = []  # '추천대로 한 번에' 보조 단추(카드마다)
        self.cards: list[tk.Frame] = []
        self.where_labels: list[tk.Label] = []
        for i, (title, desc, btn_text, target) in enumerate(T.START_CHOICES):
            box = tk.Frame(grid, highlightbackground=INFO_BORDER, highlightthickness=1, padx=12, pady=10)
            box.grid(row=0, column=i, sticky="nsew", padx=6)
            grid.columnconfigure(i, weight=1, uniform="start")
            head = tk.Label(box, text=title, font=SUB_FONT, anchor="w", justify="left", fg=INFO_FG)
            head.pack(anchor="w", fill="x")
            autowrap(head, 40)
            lbl = tk.Label(box, text=desc, justify="left", anchor="w", fg=MUTED_FG, font=BODY_FONT)
            lbl.pack(anchor="w", fill="x", pady=(6, 10))
            autowrap(lbl, 40)
            flow = ("old", "transfer", "new")[i]  # 카드 순서: ① 구 PC → ② 내 파일 옮기기 → ③ 새 PC
            where = tk.Label(box, text=T.START_WHERE[i], anchor="w", justify="left", fg=MUTED_FG, font=BODY_FONT)
            where.pack(side="bottom", fill="x", pady=(6, 0))  # 어느 PC 에서 하는 일인지(카드 맨 아래, 작게)
            autowrap(where, 40)
            self.where_labels.append(where)
            auto = ActionButton(box, text=T.BTN_AUTO, command=lambda f=flow: self.guarded(self.c.auto_run, f), kind="plain", pady=3)
            auto.pack(side="bottom", fill="x", pady=(6, 0))  # 보조 단추: 기본 단추 아래에 작게
            add_tooltip(auto, T.TIP_AUTO[flow])
            self.auto_buttons.append(auto)
            b = ActionButton(box, text=btn_text, command=lambda t=target: self.c.go(t), kind="primary", pady=10)
            b.pack(side="bottom", fill="x")
            self.buttons.append(b)
            self.cards.append(box)
        self.primary = self.buttons[0]
        self.order_note = ttk.Label(body, text=T.START_ORDER_NOTE, font=SUB_FONT, foreground=INFO_FG)
        self.order_note.pack(anchor="w", padx=6, pady=(10, 6))
        self.roadmap_big = ActionButton(body, text=T.BTN_ROADMAP_BIG, command=lambda: self.app.show_roadmap(), kind="plain",
                                        font=SUB_FONT, pady=10)
        self.roadmap_big.pack(fill="x", padx=6, pady=(0, 12))
        # 라이선스 안내는 아래쪽에 약하게(시작 화면과 마지막 결과 화면에서만 크게 보인다)
        self.license_box = tk.Label(body, text=T.LICENSE_BANNER, anchor="w", justify="left", bg=WARN_BG, fg=WARN_FG, padx=10, pady=4,
                                    font=BODY_FONT)
        self.license_box.pack(fill="x", padx=6)
        autowrap(self.license_box, 40)
        self.safety = Collapsible(body, T.SAFETY_TITLE, T.SAFETY_TITLE_OPEN)
        self.safety.pack(anchor="w", fill="x", padx=6, pady=(8, 0))
        for line in T.SAFETY_LINES:
            self.safety.add_text("• " + line, pad=40)
        self.finish()

    def on_show(self) -> None:
        self.buttons[0].focus_set()
        self.scroll.scroll_to_top()
