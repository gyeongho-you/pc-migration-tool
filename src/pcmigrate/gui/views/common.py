"""뷰 공통 도우미(tkinter 위젯)."""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from ..controller import Controller, GuardError

SECRET_BG = "#fde2e2"
SECRET_FG = "#a40000"
WARN_FG = "#9a5b00"
OK_FG = "#1b6e2f"
PRIVACY_NOTE = "이 파일에는 이 PC 에 설치된 프로그램 목록과 사용자 폴더 경로 등 개인 정보가 들어 있습니다. 저장소에 커밋하거나 남에게 공유하지 마세요."
LICENSE_NOTE = "라이선스는 복원되지 않습니다. 각 프로그램을 신 PC 에서 직접 재로그인/재활성화하세요."


def make_tree(parent: tk.Misc, columns: list[tuple[str, str, int]], height: int = 10,
              select: str = "browse") -> tuple[ttk.Frame, ttk.Treeview]:
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height, selectmode=select)
    for cid, title, width in columns:
        tree.heading(cid, text=title)
        tree.column(cid, width=width, anchor="w", stretch=True)
    ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    xs.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return frame, tree


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
    """결과 위치를 탐색기로 연다(읽기 전용 동작). Windows 가 아니면 조용히 무시."""
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


class BaseView(ttk.Frame):
    """모든 화면의 부모. 컨트롤러 이벤트를 받는다."""
    name = ""

    def __init__(self, app, controller: Controller) -> None:
        super().__init__(app.body, padding=12)
        self.app, self.c = app, controller

    def on_show(self) -> None:
        """화면이 표시될 때 호출(상태에 맞게 갱신)."""

    def on_event(self, kind: str, payload) -> None:
        """컨트롤러 이벤트(예: scan_done)를 받는다."""

    def guarded(self, fn: Callable, *args, **kw) -> bool:
        """GuardError 는 안전 안내 대화상자로 보여 준다."""
        try:
            fn(*args, **kw)
            return True
        except GuardError as e:
            messagebox.showwarning("안전을 위해 막았습니다", str(e), parent=self)
            return False

    def heading(self, text: str, sub: str = "") -> None:
        ttk.Label(self, text=text, font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        if sub:
            ttk.Label(self, text=sub, wraplength=860, justify="left").pack(anchor="w", pady=(2, 8))

    def navbar(self, back: Callable | None = None, back_text: str = "← 이전",
               next_: Callable | None = None, next_text: str = "다음 →") -> dict[str, ttk.Button]:
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        btns: dict[str, ttk.Button] = {}
        ttk.Button(bar, text="처음으로", command=lambda: self.app.go_home()).pack(side="left")
        if back:
            btns["back"] = ttk.Button(bar, text=back_text, command=back)
            btns["back"].pack(side="left", padx=6)
        if next_:
            btns["next"] = ttk.Button(bar, text=next_text, command=next_)
            btns["next"].pack(side="right")
        return btns
