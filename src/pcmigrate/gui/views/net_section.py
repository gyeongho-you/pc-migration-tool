"""'이 PC 이더넷 주소 자동 맞추기' 접이식 섹션(stage39). 받기 화면과 보내기 화면이 같이 쓴다(병합 충돌을 줄이려고 별도 파일).

흐름: [어댑터 고르기(직결 후보만)] -> [계획 보기] -> [적용](확인창) -> 상태 표시 -> 끝나면 자동 복구 / [원래대로 되돌리기].
복구 기록이 남아 있으면 맨 위에 한 줄 안내와 [지금 되돌리기]. 이 PC 의 설정을 바꾸는 일이라 적용 전에 반드시 확인창을 거친다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import texts as T
from .common import MUTED_FG, WARN_BG, WARN_FG, ActionButton, Collapsible, autowrap


class NetSection(ttk.Frame):
    def __init__(self, parent: tk.Misc, controller, role: str, owner: tk.Misc) -> None:
        super().__init__(parent)
        self.c, self.role, self.owner = controller, role, owner
        self.loaded = False
        # 복구 기록 안내(맨 위)
        self.pending_row = tk.Frame(self, bg=WARN_BG)
        self.pending_label = tk.Label(self.pending_row, text="", anchor="w", justify="left", bg=WARN_BG, fg=WARN_FG, padx=8, pady=4)
        self.pending_label.pack(side="left", fill="x", expand=True)
        autowrap(self.pending_label, 120)
        self.pending_btn = ActionButton(self.pending_row, text=T.BTN_NET_RESTORE_NOW, kind="warn", command=self._restore_pending)
        self.pending_btn.pack(side="right", padx=6, pady=2)
        # 연결 진단이 '서브넷 불일치'일 때 펼치라는 안내
        self.hint = tk.Label(self, text=T.NET_SUBNET_HINT, anchor="w", justify="left", bg=WARN_BG, fg=WARN_FG, padx=8, pady=4)
        autowrap(self.hint, 40)
        self.col = Collapsible(self, title=T.NET_TITLE, title_open=T.NET_TITLE_OPEN)
        self.col.pack(fill="x")
        self.col.toggle_btn.configure(command=self._toggle)
        body = self.col.body
        self.col.add_text(T.NET_INTRO)
        row = tk.Frame(body, bg=self.col.bg)
        row.pack(fill="x", pady=(4, 2))
        tk.Label(row, text=T.NET_ADAPTER_LABEL, bg=self.col.bg).pack(side="left")
        self.adapter_var = tk.StringVar(value="")
        self.adapter_box = ttk.Combobox(row, textvariable=self.adapter_var, state="readonly", width=34, values=[])
        self.adapter_box.pack(side="left", padx=(8, 6))
        self.refresh_btn = ActionButton(row, text=T.BTN_NET_REFRESH, kind="link", command=self._load_adapters)
        self.refresh_btn.pack(side="left")
        self.plan_label = tk.Label(body, text="", anchor="w", justify="left", bg=self.col.bg)
        self.plan_label.pack(anchor="w", fill="x", pady=(2, 2))
        autowrap(self.plan_label, 40)
        self.status_label = tk.Label(body, text="", anchor="w", justify="left", bg=self.col.bg)
        self.status_label.pack(anchor="w", fill="x", pady=(2, 2))
        autowrap(self.status_label, 40)
        btns = tk.Frame(body, bg=self.col.bg)
        btns.pack(anchor="w", pady=(2, 4))
        self.plan_btn = ActionButton(btns, text=T.BTN_NET_PLAN, kind="plain", command=self._plan)
        self.plan_btn.pack(side="left")
        self.apply_btn = ActionButton(btns, text=T.BTN_NET_APPLY, command=self._apply)
        self.apply_btn.pack(side="left", padx=(8, 0))
        self.restore_btn = ActionButton(btns, text=T.BTN_NET_RESTORE, kind="plain", command=self._restore)
        self.restore_btn.pack(side="left", padx=(8, 0))
        self.refresh()

    # ------------------------------------------------------------------ 표시
    def refresh(self) -> None:
        c = self.c
        pend = c.net_pending_text()
        if pend:
            self.pending_label.configure(text=pend)
            self.pending_row.pack(fill="x", pady=(0, 4), before=self.col)
        else:
            self.pending_row.pack_forget()
        self.status_label.configure(text=c.net_status_text())
        self.plan_label.configure(text=c.net_plan_text() if not c.net_applied() else "")
        busy = bool(c.state.busy)
        applied = bool(c.net_applied())
        self.plan_btn.state(["disabled"] if busy or applied else ["!disabled"])
        self.apply_btn.state(["disabled"] if busy or applied or not c.net_plan_text() else ["!disabled"])
        self.restore_btn.state(["!disabled"] if applied and not busy else ["disabled"])
        self.pending_btn.state(["disabled"] if busy else ["!disabled"])
        self.adapter_box.state(["disabled"] if busy or applied else ["!disabled"])

    def show_subnet_hint(self, on: bool) -> None:
        """연결 진단이 서브넷 불일치였을 때: 안내 문구를 보이고 섹션을 펼친다."""
        if on:
            self.hint.pack(fill="x", pady=(0, 4), before=self.col)
            if not self.col.open:
                self._toggle()
        else:
            self.hint.pack_forget()

    # ------------------------------------------------------------------ 동작
    def _toggle(self) -> None:
        self.col.toggle()
        if self.col.open and not self.loaded:
            self._load_adapters()

    def _load_adapters(self) -> None:
        names = self.c.net_refresh_adapters()
        self.loaded = True
        self.adapter_box.configure(values=names)
        if names and self.adapter_var.get() not in names:
            self.adapter_var.set(names[0])
        if not names:
            self.adapter_var.set("")
        self.refresh()

    def _plan(self) -> None:
        try:
            self.c.net_plan(self.role, self.adapter_var.get())
        except Exception as e:  # noqa: BLE001 - GuardError 는 쉬운 문구이므로 그대로 보인다
            messagebox.showwarning(T.GUARD_TITLE, str(e), parent=self.owner)
        self.refresh()

    def _apply(self) -> None:
        text = self.c.net_plan_text()
        if not text:
            messagebox.showwarning(T.GUARD_TITLE, T.NET_PLAN_FIRST, parent=self.owner)
            return
        if not messagebox.askokcancel(T.NET_CONFIRM_TITLE, text, parent=self.owner):
            return
        self._guarded(self.c.net_apply, True)

    def _restore(self) -> None:
        self._guarded(self.c.net_restore)

    def _restore_pending(self) -> None:
        if messagebox.askyesno(T.NET_RESTORE_ASK_TITLE, T.NET_RESTORE_ASK, parent=self.owner):
            self._guarded(self.c.net_restore)

    def _guarded(self, fn, *a) -> None:
        try:
            fn(*a)
        except Exception as e:  # noqa: BLE001
            messagebox.showwarning(T.GUARD_TITLE, str(e), parent=self.owner)
        self.refresh()
