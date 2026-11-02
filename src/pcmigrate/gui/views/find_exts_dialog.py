"""'찾을 파일 형식 설정' 창(stage61): 설계/사진/영상/문서/압축별 확장자 칩, 추가 줄, [기본값으로 초기화] [저장] [취소].

규칙은 컨트롤러(controller_exts.ExtsMixin) → 백엔드(find_exts)에 있고, 이 창은 그 결과를 그리기만 한다.
모달 창이다(열려 있는 동안 부모 창은 조작할 수 없다). 저장해야 반영되고, 저장하지 않고 닫으려 하면 확인한다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import texts as T
from .common import (BODY_FONT, BOLD_FONT, CHIP_DONE_BG, CHIP_TODO_BG, MUTED_FG, ActionButton, Collapsible, Notice, ScrollBody, add_tooltip,
                     autowrap)

CHIP_LIMIT = 20  # 분류마다 처음 보이는 칩 수(나머지는 '더 보기')


class ChipFlow(tk.Frame):
    """칩을 가로로 늘어놓다가 폭이 모자라면 다음 줄로 넘기는 틀."""

    def __init__(self, parent: tk.Misc, bg: str) -> None:
        super().__init__(parent, bg=bg)
        self.chips: list[tk.Misc] = []
        self.bind("<Configure>", lambda e: self.reflow(e.width))

    def add(self, chip: tk.Misc) -> None:
        self.chips.append(chip)

    def reflow(self, width: int | None = None) -> None:
        width = width or self.winfo_width()
        x = y = row_h = 0
        total_h = 0
        for chip in self.chips:
            chip.update_idletasks()
            w, h = chip.winfo_reqwidth(), chip.winfo_reqheight()
            if x and width > 1 and x + w > width:
                x, y, row_h = 0, y + row_h + 4, 0
            chip.place(x=x, y=y)
            x += w + 6
            row_h = max(row_h, h)
            total_h = y + row_h
        self.configure(height=max(total_h, 1))


class ExtsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, controller, ask_yesno=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.ask_yesno = ask_yesno or (lambda title, msg: messagebox.askyesno(title, msg, icon="warning", parent=self))
        self.closed = False
        self.withdraw()
        try:
            self.st = self.c.exts_begin()  # 설정을 읽고 편집본을 만든다(작업 중이면 GuardError: 창을 만들지 않고 닫는다)
        except BaseException:
            self.destroy()
            raise
        self.title(T.FX_TITLE)
        self.transient(parent.winfo_toplevel())
        self.geometry("660x560")
        self.minsize(520, 420)
        self.cat_var = tk.StringVar(value=T.FX_CATS[0][1])
        self.ext_var = tk.StringVar()

        # ---- 아래: 단추 줄(항상 보이게 먼저 pack)
        bar = ttk.Frame(self, padding=(12, 8))
        bar.pack(side="bottom", fill="x")
        self.reset_btn = ActionButton(bar, text=T.BTN_FX_RESET, kind="plain", command=self._reset)
        self.reset_btn.pack(side="left")
        self.save_btn = ActionButton(bar, text=T.BTN_FX_SAVE, kind="primary", command=self._save)
        self.save_btn.pack(side="right")
        self.cancel_btn = ActionButton(bar, text=T.BTN_FX_CANCEL, kind="plain", command=self._cancel)
        self.cancel_btn.pack(side="right", padx=(0, 8))
        self.status = ttk.Label(self, text="", foreground=MUTED_FG, padding=(12, 0))
        self.status.pack(side="bottom", fill="x")

        # ---- 추가 줄
        add = ttk.Frame(self, padding=(12, 0, 12, 4))
        add.pack(side="bottom", fill="x")
        ttk.Label(add, text=T.FX_ADD_LABEL).pack(side="left")
        self.entry = ttk.Entry(add, textvariable=self.ext_var, width=16)
        self.entry.pack(side="left", padx=(6, 12))
        ttk.Label(add, text=T.FX_CAT_LABEL).pack(side="left")
        self.cat_box = ttk.Combobox(add, textvariable=self.cat_var, values=[lbl for _k, lbl in T.FX_CATS], state="readonly", width=8)
        self.cat_box.pack(side="left", padx=(6, 12))
        self.add_btn = ActionButton(add, text=T.BTN_FX_ADD, kind="plain", command=self._add)
        self.add_btn.pack(side="left")
        self.hint = ttk.Label(self, text=T.FX_ADD_HINT, foreground=MUTED_FG, padding=(12, 0))
        self.hint.pack(side="bottom", fill="x")

        # ---- 위: 안내
        top = ttk.Frame(self, padding=(12, 10, 12, 0))
        top.pack(fill="x")
        intro = ttk.Label(top, text=T.FX_INTRO, justify="left", foreground=MUTED_FG)
        intro.pack(anchor="w", fill="x")
        autowrap(intro, 24)
        self.notice = Notice(top, "", "warn", wrap_pad=24)  # 손상/잘못된 항목 안내, 입력 오류·경고·결과 한 줄
        self.notice.pack(fill="x", pady=(6, 0))
        self.notice.clear()

        # ---- 가운데: 분류별 칩(스크롤)
        self.scroll = ScrollBody(self)
        self.scroll.pack(fill="both", expand=True, padx=12, pady=6)
        self.body = ttk.Frame(self.scroll.inner, padding=(0, 0, 8, 0))
        self.body.pack(fill="both", expand=True)
        self.flows: list[ChipFlow] = []

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: (self._cancel(), "break")[1])
        self.entry.bind("<Return>", lambda e: (self._add(), "break")[1])  # Enter 로 저장되지는 않는다(실수 방지)
        self._rebuild()
        if self.st.open_note:
            self.notice.set(self.st.open_note, "warn")
        self.deiconify()
        try:
            self.grab_set()  # 모달(보이지 않는 창에서는 실패할 수 있어 건너뜀)
        except tk.TclError:
            pass
        self.entry.focus_set()

    # ------------------------------------------------------------------ 그리기
    def _chip(self, flow: ChipFlow, item: dict) -> None:
        user = item["origin"] == "user"
        bg = CHIP_DONE_BG if user else CHIP_TODO_BG
        chip = tk.Frame(flow, bg=bg, padx=6, pady=2)
        text = ("+ " if user else "") + item["ext"]
        lbl = tk.Label(chip, text=text, bg=bg, fg="#111111", font=BOLD_FONT if user else BODY_FONT)
        lbl.pack(side="left")
        x = tk.Button(chip, text=T.FX_REMOVE_LABEL, relief="flat", borderwidth=0, bg=bg, activebackground=bg, padx=3, pady=0,
                      cursor="hand2", takefocus=1, command=lambda e=item["ext"]: self._remove(e))
        x.pack(side="left", padx=(4, 0))
        tip = T.FX_USER_TIP if user else T.FX_DEFAULT_TIP
        add_tooltip(lbl, tip)
        add_tooltip(x, tip)
        chip.ext, chip.label, chip.remove_btn = item["ext"], lbl, x  # type: ignore[attr-defined]
        flow.add(chip)

    def _rebuild(self) -> None:
        d = self.c.exts_describe()
        for w in self.body.winfo_children():
            w.destroy()
        bg = ttk.Style().lookup("TFrame", "background") or self.cget("background")
        self.flows = []
        self.chips: list[tk.Misc] = []
        for cat in d["categories"]:
            ttk.Label(self.body, text=T.FX_CAT_TITLE.format(label=cat["label"], n=cat["count"]), font=BOLD_FONT).pack(anchor="w", pady=(8, 2))
            first, rest = cat["exts"][:CHIP_LIMIT], cat["exts"][CHIP_LIMIT:]
            flow = ChipFlow(self.body, bg)
            flow.pack(fill="x")
            self.flows.append(flow)
            for item in first:
                self._chip(flow, item)
            if rest:
                more = Collapsible(self.body, T.FX_MORE.format(n=len(rest)), T.FX_MORE_OPEN)
                more.pack(anchor="w", fill="x")
                flow2 = ChipFlow(more.body, more.bg)
                flow2.pack(fill="x")
                self.flows.append(flow2)
                for item in rest:
                    self._chip(flow2, item)
        self.chips = [c for f in self.flows for c in f.chips]
        self.removed_row = ttk.Frame(self.body)
        self.restore_btns: dict[str, ActionButton] = {}
        if d["removed"]:
            self.removed_row.pack(fill="x", pady=(10, 0))
            ttk.Label(self.removed_row, text=T.FX_REMOVED, font=BOLD_FONT).pack(anchor="w")
            for r in d["removed"]:
                row = ttk.Frame(self.removed_row)
                row.pack(anchor="w")
                ttk.Label(row, text=f"{r['ext']}({r['label']})").pack(side="left")
                btn = ActionButton(row, text=T.FX_RESTORE, kind="link", command=lambda e=r["ext"]: self._restore(e))
                btn.pack(side="left", padx=8)
                self.restore_btns[r["ext"]] = btn
        self.status.configure(text=self.c.exts_dirty_text())
        self.update_idletasks()
        for f in self.flows:
            f.reflow()

    def chip_for(self, ext: str):
        """시험/접근성용: 그 확장자의 칩 위젯(없으면 None)."""
        return next((c for c in self.chips if c.ext == ext), None)

    def _show(self, res, ok_kind: str = "info") -> None:
        """결과 한 줄(+경고)을 안내 상자에 보인다. 실패/경고는 주황, 그 밖의 결과는 파랑."""
        lines = ([res.message] if res.message else []) + list(res.warnings)
        if not lines:
            self.notice.clear()
        else:
            self.notice.set("\n".join(lines), "warn" if (not res.ok or res.warnings) else ok_kind)

    # ------------------------------------------------------------------ 동작(모두 컨트롤러를 거친다)
    def _add(self) -> None:
        text = self.ext_var.get()
        if not text.strip():
            return  # 비어 있으면 아무 일도 하지 않는다(Enter 로 저장되지 않는다)
        cat = next((k for k, lbl in T.FX_CATS if lbl == self.cat_var.get()), T.FX_CATS[0][0])
        res = self.c.exts_add(text, cat)
        if res.ok:
            self.ext_var.set("")
        self._show(res)
        self._rebuild()

    def _remove(self, ext: str) -> None:
        self._show(self.c.exts_remove(ext))
        self._rebuild()

    def _restore(self, ext: str) -> None:
        self._show(self.c.exts_restore(ext))
        self._rebuild()

    def _reset(self) -> None:
        if not self.ask_yesno(T.FX_RESET_TITLE, T.FX_RESET_ASK):
            return
        self._show(self.c.exts_reset(), "ok")
        self._rebuild()

    def _save(self) -> None:
        res = self.c.exts_save()
        if res.ok and res.closed:
            self._close()
            return
        self._show(res)

    def _cancel(self) -> None:
        if self.c.exts_dirty() and not self.ask_yesno(T.FX_CLOSE_TITLE, T.FX_CLOSE_ASK):
            return
        self._close()

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
