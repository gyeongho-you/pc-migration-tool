"""'읽지 못해 제외된 파일 N개 / 이 파일들만 다시 담기' 상자(구 PC 설정 묶음 만들기 화면의 결과 영역에 붙는 독립 위젯).

화면(old_pc.ExportView)은 이 위젯을 만들어 결과 영역에 놓고, 이벤트(export_done / export_retry_done / busy)를 넘겨 주기만 하면 된다.
읽지 못한 파일이 없으면 높이 0 으로 보이지 않는다. 실제 일은 controller.retry_export_failed 가 한다(기존 busy/취소/진행 흐름).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import texts as T
from .common import WARN_BG, WARN_FG, SUB_FONT, autowrap


def retry_lines(res) -> list[str]:
    """다시 담기 결과 안내 줄(화면 문구는 texts.py). res 는 api.RetryResult."""
    info = res.info
    still = res.still_failed
    lines = [T.EXP_RETRY_DONE.format(added=len(res.added), still=len(still)) if not res.unchanged
             else T.EXP_RETRY_NO_CHANGE.format(still=len(still))]
    if still:
        from ...failure import REASONS
        by: dict[str, int] = {}
        for f in still:
            by[f.get("reason", "other")] = by.get(f.get("reason", "other"), 0) + 1
        lines.append(T.EXP_RETRY_REASONS.format(reasons=", ".join(
            f"{REASONS[c].label if c in REASONS else c} {n}개" for c, n in by.items())))
    if info.get("gone"):
        lines.append(T.EXP_RETRY_GONE.format(n=len(info["gone"])))
    if info.get("not_retried"):
        lines.append(T.EXP_RETRY_NOT_RETRIED.format(n=len(info["not_retried"])))
    if res.added:
        lines.append(T.EXP_RETRY_FRESH)
    if info.get("backup"):
        lines.append(T.EXP_RETRY_BACKUP.format(path=info["backup"]))
    return lines


class RetryPanel(ttk.Frame):
    def __init__(self, parent, controller, guarded) -> None:
        """guarded: BaseView.guarded(fn, *args, **kw) (GuardError 를 안내 대화상자로 보여 주는 함수)."""
        super().__init__(parent)
        self.c, self._guarded = controller, guarded
        self.box = tk.Frame(self, bg=WARN_BG, highlightbackground=WARN_FG, highlightthickness=1, padx=12, pady=8)
        self.head = tk.Label(self.box, text="", bg=WARN_BG, fg=WARN_FG, font=SUB_FONT, anchor="w")
        self.head.pack(anchor="w")
        self.hint = tk.Label(self.box, text=T.EXP_RETRY_HINT, bg=WARN_BG, justify="left", anchor="w")
        self.hint.pack(anchor="w", fill="x", pady=2)
        autowrap(self.hint, 30)
        self.result = tk.Label(self.box, text="", bg=WARN_BG, justify="left", anchor="w")
        autowrap(self.result, 30)
        self.btn = ttk.Button(self.box, text=T.BTN_EXP_RETRY, command=self.retry)
        self.btn.pack(anchor="w", pady=(6, 0))
        self._shown = False
        self._result_text = ""

    # ------------------------------------------------------------------ 화면이 부르는 곳
    def on_event(self, kind: str, payload) -> None:
        if kind == "export_done":
            self._result_text = ""
            self.refresh()
        elif kind == "export_retry_done":
            self._result_text = "\n".join(retry_lines(payload))
            self.refresh()
        elif kind == "busy":
            self._update_button()

    def reset(self) -> None:
        """화면에 다시 들어올 때(새로 만들기) 상자를 숨긴다."""
        self._result_text = ""
        self._hide()

    def refresh(self) -> None:
        n = len(self.c.export_unreadable())
        if n == 0 and not self._result_text:
            self._hide()
            return
        if n:
            self.head.configure(text=T.EXP_RETRY_HEAD.format(n=n))
            self.hint.pack(anchor="w", fill="x", pady=2, before=self.btn)
            self.btn.pack(anchor="w", pady=(6, 0))
        else:  # 모두 담겼다: 결과 안내만 남긴다
            self.head.configure(text=T.EXP_RETRY_ALL_DONE)
            self.hint.pack_forget()
            self.btn.pack_forget()
        if self._result_text:
            self.result.configure(text=self._result_text)
            self.result.pack(anchor="w", fill="x", pady=2, **({"before": self.btn} if n else {}))
        else:
            self.result.pack_forget()
        if not self._shown:
            self.box.pack(fill="x", pady=(6, 0))
            self._shown = True
        self._update_button()

    def _hide(self) -> None:
        if self._shown:
            self.box.pack_forget()
            self._shown = False

    def _update_button(self) -> None:
        self.btn.state(["disabled"] if self.c.retry_export_reason() else ["!disabled"])

    def retry(self) -> None:
        n = len(self.c.export_unreadable())
        if not messagebox.askokcancel(T.EXP_RETRY_CONFIRM_TITLE,
                                      T.EXP_RETRY_CONFIRM.format(n=n, path=self.c.state.export_output), parent=self):
            return
        self._guarded(self.c.retry_export_failed, write_confirmed=True)
