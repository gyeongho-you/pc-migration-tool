"""내 파일 옮기기 2/4 — 옮기는 방법 고르기: 두 PC를 바로 연결(추천) / 외장 드라이브 / 공유 폴더(고급)를 라디오 방식 상자 셋으로.

'두 PC를 바로 연결'을 고르면 이 PC의 역할(보내는 쪽/받는 쪽)을 묻는다. 이 화면에서는 아무것도 옮기지 않는다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import state as S
from .. import texts as T
from ...progress import fmt_bytes
from .common import BODY_FONT, INFO_BORDER, INFO_FG, MUTED_FG, SUB_FONT, BaseView, ScrollBody, autowrap


class MethodView(BaseView):
    name = S.METHOD

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.FIND), next_=lambda: self.guarded(self.c.files_continue))
        self.primary = self.next_btn
        self.header(T.MV_HEADING, "")
        self.sub = ttk.Label(self, text="", justify="left")
        self.sub.pack(anchor="w", fill="x", pady=(2, 8))
        autowrap(self.sub)
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        self.method_var = tk.StringVar(value="")
        self.role_var = tk.StringVar(value="")
        self.boxes: dict[str, tk.Frame] = {}
        self.radios: dict[str, ttk.Radiobutton] = {}
        self.descs: dict[str, tk.Label] = {}
        for key, title, desc in T.MV_OPTIONS:
            box = tk.Frame(body, highlightbackground=INFO_BORDER, highlightthickness=1, padx=12, pady=8)
            box.pack(fill="x", pady=5)
            rb = ttk.Radiobutton(box, text=title, value=key, variable=self.method_var, command=self._method_changed)
            rb.pack(anchor="w")
            lbl = tk.Label(box, text=desc, justify="left", anchor="w", fg=MUTED_FG, font=BODY_FONT)
            lbl.pack(anchor="w", fill="x", padx=(24, 0), pady=(2, 0))
            autowrap(lbl, 70)
            lbl.bind("<Button-1>", lambda e, k=key: self._pick(k))
            self.boxes[key], self.radios[key], self.descs[key] = box, rb, lbl
        # ---- 두 PC를 바로 연결: 이 PC 의 역할
        direct = self.boxes[T.MV_DIRECT]
        self.role_frame = tk.Frame(direct)
        tk.Label(self.role_frame, text=T.MV_ROLE_TITLE, font=SUB_FONT, anchor="w").pack(anchor="w", pady=(8, 2))
        self.role_radios: dict[str, ttk.Radiobutton] = {}
        for key, title, desc in T.MV_ROLES:
            rb = ttk.Radiobutton(self.role_frame, text=title, value=key, variable=self.role_var, command=self._role_changed)
            rb.pack(anchor="w", padx=(12, 0))
            d = tk.Label(self.role_frame, text=desc, justify="left", anchor="w", fg=MUTED_FG)
            d.pack(anchor="w", fill="x", padx=(36, 0))
            autowrap(d, 90)
            self.role_radios[key] = rb
        note = tk.Label(self.role_frame, text=T.MV_ROLE_NOTE, justify="left", anchor="w", fg=INFO_FG)
        note.pack(anchor="w", fill="x", padx=(12, 0), pady=(6, 0))
        autowrap(note, 90)
        self.finish()
        self._sync()

    def on_show(self) -> None:
        self._sync()
        self.scroll.scroll_to_top()
        (self.radios.get(self.c.files.method) or self.radios[T.MV_DIRECT]).focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "files_changed":
            self._sync()

    def _pick(self, key: str) -> None:
        self.method_var.set(key)
        self._method_changed()

    def _method_changed(self) -> None:
        self.guarded(self.c.set_method, self.method_var.get())

    def _role_changed(self) -> None:
        self.guarded(self.c.set_role, self.role_var.get())

    def _sync(self) -> None:
        c, f = self.c, self.c.files
        self.method_var.set(f.method)
        self.role_var.set(f.role)
        n, size, unknown = c.data_total()
        note = c.bundle_note()  # '설정 묶음 1개 포함'(체크했을 때만)
        text = (T.MV_SUB.format(n=n, size=("약 " + fmt_bytes(size)) + (" 이상" if unknown else "")) if n
                else T.MV_SUB_BUNDLE_ONLY if note else T.MV_SUB_UNKNOWN.format(n=n))
        self.sub.configure(text=text + (f" ({note})" if note and n else ""))
        for key, box in self.boxes.items():  # 고른 상자는 굵은 파란 테두리(배경색은 그대로: 안쪽 라디오 단추와 색이 어긋나지 않게)
            on = key == f.method
            box.configure(highlightbackground=INFO_FG if on else INFO_BORDER, highlightthickness=3 if on else 1)
        if f.method == T.MV_DIRECT:
            self.role_frame.pack(fill="x", padx=(24, 0))
        else:
            self.role_frame.pack_forget()
        why = c.files_method_reason()
        self.next_btn.state(["disabled"] if why else ["!disabled"])
        self.next_btn.emphasize(not why)
        self.set_next_reason(why)
