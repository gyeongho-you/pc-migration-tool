"""뷰 공통 도우미(tkinter 위젯): 표, 툴팁, 도움말 단추, 결과 요약 등."""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .. import texts as T
from ..controller import Controller, GuardError

SECRET_BG = "#fde2e2"
SECRET_FG = "#a40000"
WARN_FG = "#8a4b00"
OK_BG = "#e3f3e6"
OK_FG = "#1b6e2f"
INFO_BG = "#eef3fb"
MUTED_FG = "#555555"
HEADING_FONT = ("TkDefaultFont", 14, "bold")
BIG_FONT = ("TkDefaultFont", 16, "bold")
STAGE_FONT = ("TkDefaultFont", 12, "bold")


def autowrap(label: tk.Misc, pad: int = 24) -> None:
    """부모 폭에 맞춰 줄바꿈 폭을 바꾼다(창 크기를 줄여도 글이 잘리지 않게).

    줄바꿈 폭을 바꾸면 위젯 높이가 바뀌어 Configure 이벤트가 다시 오므로, 폭이 8픽셀 이상 달라질 때만 반영해
    이벤트가 계속 되풀이되는 것을 막는다.
    """
    parent = label.master
    last = {"w": 0}

    def on_configure(e) -> None:
        w = max(240, e.width - pad)
        if abs(w - last["w"]) >= 8:
            last["w"] = w
            label.configure(wraplength=w)

    parent.bind("<Configure>", on_configure, add="+")


# --------------------------------------------------------------------------- 툴팁
class Tooltip:
    """위젯 위에 마우스를 올리면 설명을 띄운다."""

    def __init__(self, widget: tk.Misc, text: str, delay: int = 500) -> None:
        self.widget, self.text, self.delay = widget, text, delay
        self.tip: tk.Toplevel | None = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _e=None) -> None:
        self.hide()
        self._job = self.widget.after(self.delay, self.show)

    def show(self, x: int | None = None, y: int | None = None) -> None:
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12 if x is None else x
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4 if y is None else y
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, background="#ffffe0", relief="solid", borderwidth=1, justify="left",
                 wraplength=420, padx=6, pady=3).pack()

    def hide(self, _e=None) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


def add_tooltip(widget: tk.Misc, text: str) -> Tooltip:
    return Tooltip(widget, text)


class TreeTooltip:
    """표의 칸 위에 마우스를 올리면 그 칸의 전체 내용(긴 경로 등)을 보여 준다."""

    def __init__(self, tree: ttk.Treeview, text_for: Callable[[str, int], str | None], delay: int = 500) -> None:
        self.tree, self.text_for, self.delay = tree, text_for, delay
        self.tip: tk.Toplevel | None = None
        self._job = None
        self._last: tuple[str, str] | None = None
        tree.bind("<Motion>", self._motion, add="+")
        tree.bind("<Leave>", self.hide, add="+")

    def text_at(self, iid: str, col_index: int) -> str:
        return (self.text_for(iid, col_index) or "") if iid else ""

    def _motion(self, e) -> None:
        iid, col = self.tree.identify_row(e.y), self.tree.identify_column(e.x)
        key = (iid, col)
        if key == self._last:
            return
        self.hide()
        self._last = key
        if iid and col:
            self._job = self.tree.after(self.delay, lambda: self.show_now(iid, int(col[1:]) - 1, e.x_root, e.y_root))

    def show_now(self, iid: str, col_index: int, x: int, y: int) -> None:
        text = self.text_at(iid, col_index)
        if not text:
            return
        self.tip = tk.Toplevel(self.tree)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x + 14}+{y + 16}")
        tk.Label(self.tip, text=text, background="#ffffe0", relief="solid", borderwidth=1, justify="left",
                 wraplength=640, padx=6, pady=3).pack()

    def hide(self, _e=None) -> None:
        if self._job is not None:
            try:
                self.tree.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
        self._last = None


# --------------------------------------------------------------------------- 표
def make_tree(parent: tk.Misc, columns: list[tuple[str, str, int]], height: int = 10,
              select: str = "browse") -> tuple[ttk.Frame, ttk.Treeview]:
    """스크롤바가 있는 표. 열 제목을 누르면 정렬된다(다시 누르면 반대로)."""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height, selectmode=select)
    for cid, title, width in columns:
        tree.heading(cid, text=title, command=lambda c=cid: sort_tree(tree, c))
        tree.column(cid, width=width, minwidth=40, anchor="w", stretch=True)
    ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    xs.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    tree._sort_state = {}  # type: ignore[attr-defined]
    return frame, tree


def sort_tree(tree: ttk.Treeview, col: str, descending: bool | None = None) -> None:
    state = tree._sort_state  # type: ignore[attr-defined]
    if descending is None:
        descending = not state.get(col, True)
    state.clear()
    state[col] = descending
    items = [(tree.set(i, col), i) for i in tree.get_children("")]
    items.sort(key=lambda t: t[0].lower(), reverse=descending)
    for pos, (_v, iid) in enumerate(items):
        tree.move(iid, "", pos)
    for cid in tree["columns"]:
        base = tree.heading(cid, "text").rstrip(" ▲▼")
        tree.heading(cid, text=base + ((" ▼" if descending else " ▲") if cid == col else ""))


def clear(tree: ttk.Treeview) -> None:
    tree.delete(*tree.get_children())


def make_text(parent: tk.Misc, height: int = 8) -> tuple[ttk.Frame, tk.Text]:
    frame = ttk.Frame(parent)
    text = tk.Text(frame, height=height, wrap="word", state="disabled", relief="flat", background="#f6f6f6")
    ys = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=ys.set)
    text.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, text


def set_text(text: tk.Text, lines: list[str] | str) -> None:
    body = lines if isinstance(lines, str) else "\n".join(lines)
    text.configure(state="normal")
    text.delete("1.0", "end")
    text.insert("1.0", body)
    text.configure(state="disabled")


def open_location(path: str) -> None:
    """결과 위치를 탐색기로 연다(읽기 전용 동작). 열지 못하면 조용히 무시."""
    try:
        target = path if os.path.isdir(path) else os.path.dirname(path) or "."
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except OSError:
        pass


# --------------------------------------------------------------------------- 결과 요약 / 안내 상자
class ResultSummary(ttk.Frame):
    """'성공 N / 건너뜀 N / 실패 N' 큰 요약 + 안내 상자(실패면 빨간 상자와 해야 할 일 한 줄)."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.ok_label = ttk.Label(row, text="", font=BIG_FONT, foreground=OK_FG)
        self.skip_label = ttk.Label(row, text="", font=BIG_FONT, foreground=MUTED_FG)
        self.fail_label = ttk.Label(row, text="", font=BIG_FONT)
        for i, w in enumerate((self.ok_label, self.skip_label, self.fail_label)):
            w.pack(side="left", padx=(0, 28))
        self.box = tk.Label(self, text="", anchor="w", justify="left", padx=8, pady=6)
        self.box.pack(fill="x", pady=(6, 0))
        autowrap(self.box, 40)
        self.clear()

    def clear(self) -> None:
        for w in (self.ok_label, self.skip_label, self.fail_label):
            w.configure(text="")
        self.box.configure(text="", bg=self.winfo_toplevel().cget("background"))

    def show(self, ok: int, skipped: int, failed: int, ok_message: str, fail_message: str, pending: bool = False) -> None:
        """pending: 복사는 끝났지만 검사를 미룬 상태 — '성공'이라고 하지 않고 '복사 완료(검증 전)'로 표시한다."""
        if pending:
            self.ok_label.configure(text=f"{T.ICON_WARN} {T.TR_PENDING_LABEL} {ok}", foreground=WARN_FG)
        else:
            self.ok_label.configure(text=f"{T.ICON_OK} {T.RESULT_OK} {ok}", foreground=OK_FG)
        self.skip_label.configure(text=f"{T.RESULT_SKIPPED} {skipped}")
        self.fail_label.configure(text=f"{T.ICON_FAIL if failed else ''} {T.RESULT_FAILED} {failed}".strip(),
                                  foreground=SECRET_FG if failed else MUTED_FG)
        if failed:
            self.box.configure(text=fail_message, bg=SECRET_BG, fg=SECRET_FG)
        elif pending:
            self.box.configure(text=ok_message, bg=INFO_BG, fg=WARN_FG)
        else:
            self.box.configure(text=ok_message, bg=OK_BG, fg=OK_FG)


# --------------------------------------------------------------------------- 도움말 / 대화상자
class TextDialog(tk.Toplevel):
    """짧은 글을 보여 주는 작은 창. Enter/Esc 로 닫는다."""

    def __init__(self, parent: tk.Misc, title: str, body: str, width: int = 70, height: int = 14) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        frame, self.text = make_text(self, height=height)
        self.text.configure(width=width)
        set_text(self.text, body)
        frame.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self.close_btn = ttk.Button(self, text=T.BTN_CLOSE, command=self.destroy)
        self.close_btn.pack(side="right", padx=12, pady=(0, 12))
        self.bind("<Return>", self.close_event)
        self.bind("<Escape>", self.close_event)
        self.close_btn.focus_set()

    def close_event(self, _e=None) -> str:
        """Enter/Esc 로 닫기(다른 화면의 Enter/Esc 동작이 겹치지 않게 'break')."""
        self.destroy()
        return "break"


class BaseView(ttk.Frame):
    """모든 화면의 부모. 컨트롤러 이벤트를 받고, 제목줄(?)과 하단 이동 막대를 제공한다."""
    name = ""

    def __init__(self, app, controller: Controller) -> None:
        super().__init__(app.body, padding=12)
        self.app, self.c = app, controller
        self.primary: ttk.Button | None = None  # Enter 키로 실행되는 기본 버튼
        self.back_btn: ttk.Button | None = None  # Esc 키로 실행되는 '이전' 버튼
        self.next_btn: ttk.Button | None = None
        self._nav: ttk.Frame | None = None

    def on_show(self) -> None:
        """화면이 표시될 때 호출(상태에 맞게 갱신)."""

    def on_event(self, kind: str, payload) -> None:
        """컨트롤러 이벤트(예: scan_done)를 받는다."""

    def refresh_card(self) -> None:
        card = getattr(self, "card", None)
        if card is not None:
            card.refresh()

    def guarded(self, fn: Callable, *args, **kw) -> bool:
        """GuardError 는 안전 안내 대화상자로 보여 준다."""
        try:
            fn(*args, **kw)
            return True
        except GuardError as e:
            messagebox.showwarning(T.GUARD_TITLE, str(e), parent=self)
            return False

    def add_status_card(self, tasks: set[str]):
        """제목 바로 아래에 현황 카드를 둔다. 이 화면의 작업(tasks)이 진행 중이거나 막 끝났을 때만 보인다."""
        from .status_card import StatusCard
        self.card_slot = ttk.Frame(self)
        self.card_slot.pack(fill="x")
        self.card = StatusCard(self, self.c, tasks, slot=self.card_slot)
        return self.card

    def header(self, title: str, sub: str = "") -> None:
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text=title, font=HEADING_FONT).pack(side="left")
        self.help_btn = ttk.Button(row, text=T.BTN_HELP, width=3, command=lambda: self.app.show_help(self.name))
        self.help_btn.pack(side="right")
        add_tooltip(self.help_btn, T.HELP_TOOLTIP)
        self.roadmap_btn = ttk.Button(row, text=T.BTN_ROADMAP, command=lambda: self.app.show_roadmap())
        self.roadmap_btn.pack(side="right", padx=6)
        if sub:
            lbl = ttk.Label(self, text=sub, justify="left")
            lbl.pack(anchor="w", fill="x", pady=(2, 8))
            autowrap(lbl)

    def navbar(self, back: Callable | None = None, back_text: str = T.BTN_BACK,
               next_: Callable | None = None, next_text: str = T.BTN_NEXT) -> dict[str, ttk.Button]:
        """하단 이동 막대(맨 아래). 만든 직후 pack 해 두고 finish() 에서 Tab 순서를 맨 뒤로 보낸다."""
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        self._nav = bar
        btns: dict[str, ttk.Button] = {}
        ttk.Button(bar, text=T.BTN_HOME, command=lambda: self.app.go_home()).pack(side="left")
        if back:
            self.back_btn = btns["back"] = ttk.Button(bar, text=back_text, command=back)
            btns["back"].pack(side="left", padx=6)
        self.next_reason = ttk.Label(bar, text="", foreground=WARN_FG)
        if next_:
            self.next_btn = btns["next"] = ttk.Button(bar, text=next_text, command=next_)
            btns["next"].pack(side="right")
            self.next_reason.pack(side="right", padx=8)  # '다음'이 막혀 있을 때 그 이유
        return btns

    def set_next_reason(self, text: str) -> None:
        self.next_reason.configure(text=text)

    def finish(self) -> None:
        """화면 구성이 끝난 뒤 호출: 이동 막대를 Tab 순서 맨 뒤로(본문 → 이동 단추 순서로 이동)."""
        if self._nav is not None:
            self._nav.lift()
