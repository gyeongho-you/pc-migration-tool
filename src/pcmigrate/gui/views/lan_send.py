"""내 파일 옮기기 3/4 — 보내는 쪽(구 PC): '보낼 곳 찾기'. 새 PC 찾기 → (직접 주소) → 코드 → 연결하고 보내기, 연결 진단.

보내기는 새 PC 에 쓰는 일이라 시작 직전에 확인 창을 띄운다. 원본은 읽기만 한다. 진행은 현황 카드(속도·남은 시간은 기존 평활화 값),
연결/코드 문제는 오류 대화상자 대신 이 화면에 쉬운 한국어로 보인다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import state as S
from .. import texts as T
from .common import (FoldLabel, FoldTLabel, DANGER_BG, DANGER_FG, MUTED_FG, OK_BG, OK_FG, SUB_FONT, WARN_BG, WARN_FG, ActionButton, BaseView, ScrollBody,
                     autowrap, bind_alt, clean_path)


class SendView(BaseView):
    name = S.LAN_SEND

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.METHOD if self.c.data_selection() else S.FIND))
        self.connect_btn = ActionButton(self.bar_right, text=T.BTN_TX_CONNECT, command=self._connect)
        self.connect_btn.pack(side="right")
        self.diag_btn = ActionButton(self.bar_right, text=T.BTN_TX_DIAG, kind="plain", command=self._diagnose)
        self.diag_btn.pack(side="right", padx=(0, 8))
        self.primary = self.connect_btn
        self.header(T.TX_HEADING, T.TX_SUB)
        self.add_status_card({T.TASK_LAN_SEND})
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)

        self.selection = FoldTLabel(body, text="", font=SUB_FONT)
        self.selection.pack(anchor="w", pady=(4, 6))

        # ---- 새 PC 찾기
        row = ttk.Frame(body)
        row.pack(fill="x")
        self.find_btn = ActionButton(row, text=T.BTN_TX_FIND, kind="plain", command=lambda: self.guarded(self.c.lan_find_start))
        self.find_btn.pack(side="left")
        ttk.Label(row, text=T.TX_FOUND_LABEL).pack(side="left", padx=(12, 6))
        self.found_var = tk.StringVar(value="")
        self.found_box = ttk.Combobox(row, textvariable=self.found_var, state="readonly", width=44, values=[])
        self.found_box.pack(side="left", fill="x", expand=True)
        self.found_box.bind("<<ComboboxSelected>>", self._found_picked)
        self.found_msg = FoldTLabel(body, text="", justify="left", foreground=MUTED_FG)
        self.found_msg.pack(anchor="w", fill="x", pady=(2, 0))
        autowrap(self.found_msg)

        # ---- 직접 주소 / 코드
        ttk.Label(body, text=T.TX_ADDR_LABEL, font=SUB_FONT).pack(anchor="w", pady=(10, 0))
        hint = ttk.Label(body, text=T.TX_ADDR_HINT, foreground=MUTED_FG, justify="left")
        hint.pack(anchor="w", fill="x")
        autowrap(hint)
        self.target_var = tk.StringVar(value="")
        self.target_entry = ttk.Entry(body, textvariable=self.target_var)
        self.target_entry.pack(fill="x", pady=(2, 0))
        ttk.Label(body, text=T.TX_CODE_LABEL, font=SUB_FONT).pack(anchor="w", pady=(10, 0))
        hint2 = ttk.Label(body, text=T.TX_CODE_HINT, foreground=MUTED_FG, justify="left")
        hint2.pack(anchor="w", fill="x")
        autowrap(hint2)
        self.code_var = tk.StringVar(value="")
        self.code_entry = ttk.Entry(body, textvariable=self.code_var, width=14)
        self.code_entry.pack(anchor="w", pady=(2, 0))
        for var in (self.target_var, self.code_var):
            var.trace_add("write", lambda *a: self._remember())

        # ---- 진단 / 오류 / 결과
        self.diag = FoldTLabel(body, text="", justify="left")
        autowrap(self.diag)
        self.error = FoldLabel(body, text="", anchor="w", justify="left", bg=DANGER_BG, fg=DANGER_FG, padx=10, pady=6)
        autowrap(self.error, 40)
        self.note = FoldLabel(body, text="", anchor="w", justify="left", bg=WARN_BG, fg=WARN_FG, padx=10, pady=6)
        autowrap(self.note, 40)
        self.result = FoldLabel(body, text="", anchor="w", justify="left", padx=10, pady=6)
        autowrap(self.result, 40)
        self.result_notes = FoldTLabel(body, text="", justify="left")
        autowrap(self.result_notes)
        for b in (self.connect_btn, self.diag_btn, self.find_btn):
            bind_alt(self.app, b, lambda: self.app.current == self.name)
        self.finish()
        self._render()

    # ------------------------------------------------------------------ 표시
    def on_show(self) -> None:
        f = self.c.files
        self.target_var.set(f.target)
        self.code_var.set(f.code)
        self._render()
        self.scroll.scroll_to_top()
        self.target_entry.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "files_changed":
            if self.target_var.get() != self.c.files.target and self.c.files.target:
                self.target_var.set(self.c.files.target)
            self._render()
        elif kind in ("lan_found", "lan_diag", "lan_send_done", "cancelled", "busy"):
            self._render()

    def _remember(self) -> None:
        self.c.lan_set_target(self.target_var.get(), self.code_var.get())

    def _render(self) -> None:
        c, f = self.c, self.c.files
        items = c.lan_send_items()
        self.selection.configure(text=c.tx_selection_text(items))
        labels = [T.TX_FOUND_ITEM.format(name=r.name, addr=f"{r.ip}:{r.port}") for r in f.found]
        self.found_box.configure(values=labels)
        if not labels:
            self.found_var.set("")
        elif f.target and any(f.target == f"{r.ip}:{r.port}" for r in f.found):
            idx = next(i for i, r in enumerate(f.found) if f.target == f"{r.ip}:{r.port}")
            self.found_var.set(labels[idx])
        self.found_msg.configure(text=f.found_message)
        self._show(self.diag, f.diag, after=self.code_entry)
        err_after = self.selection  # 결과/오류/안내는 맨 위(보낼 폴더 줄 바로 아래)에 보여 첫 화면에서 바로 보이게 한다
        if f.tx_error:
            self.error.configure(text=f"{T.ICON_FAIL} {f.tx_error}")
            self.error.pack(fill="x", pady=(8, 0), after=err_after)
        else:
            self.error.pack_forget()
        if f.tx_cancelled and f.tx_report is None and not f.tx_error:
            self.note.configure(text=T.TX_CANCELLED)
            self.note.pack(fill="x", pady=(8, 0), after=err_after)
        else:
            self.note.pack_forget()
        text, ok, lines = c.tx_result_lines()
        if text:
            self.result.configure(text=text + "\n" + (T.TX_RESULT_OK if ok else T.TX_RESULT_BAD), bg=OK_BG if ok else WARN_BG,
                                  fg=OK_FG if ok else WARN_FG)
            self.result.pack(fill="x", pady=(8, 0), after=err_after)
            self.result_notes.configure(text="\n".join(lines))
            self.result_notes.pack(anchor="w", fill="x", pady=(4, 0), after=self.result)
        else:
            self.result.pack_forget()
            self.result_notes.pack_forget()
        busy = bool(c.state.busy)
        for b in (self.connect_btn, self.diag_btn, self.find_btn):
            b.state(["disabled"] if busy else ["!disabled"])
        for w in (self.target_entry, self.code_entry):
            w.state(["disabled"] if busy else ["!disabled"])
        self.connect_btn.emphasize(f.tx_report is None)

    @staticmethod
    def _show(widget, text: str, after) -> None:
        if text:
            widget.configure(text=text)
            widget.pack(anchor="w", fill="x", pady=(8, 0), after=after)
        else:
            widget.pack_forget()

    # ------------------------------------------------------------------ 동작
    def _found_picked(self, _e=None) -> None:
        idx = self.found_box.current()
        if idx >= 0:
            self.guarded(self.c.lan_pick_found, idx)

    def _inputs(self) -> tuple[str, str]:
        return clean_path(self.target_var.get()), self.code_var.get().strip()

    def _diagnose(self) -> None:
        target, code = self._inputs()
        self.guarded(self.c.lan_diagnose_start, target, code)

    def _connect(self) -> None:
        target, code = self._inputs()
        why = self.c.lan_send_problem(target, code)
        if why:
            messagebox.showwarning(T.GUARD_TITLE, why, parent=self)
            return
        if not messagebox.askokcancel(T.TX_CONFIRM_TITLE, self.c.tx_confirm_text(target), parent=self):
            return
        self.guarded(self.c.lan_send_start, target, code, True)
