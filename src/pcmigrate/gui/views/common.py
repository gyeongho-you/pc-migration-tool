"""뷰 공통 도우미(tkinter 위젯): 색/글꼴 상수, 단추, 접이식 상자, 안내 상자, 스크롤 본문, 경로 입력칸, 표, 툴팁 등.

색은 네 가지 뜻으로만 쓴다: 안내(파랑), 성공(초록), 주의(주황 + 연노랑), 위험(빨강 + 연빨강). 인라인 색은 만들지 않고 아래 상수를 쓴다.
글꼴은 세 단계: 제목 16 / 소제목 12 / 본문 11(글자 크기 키우기 단추로 모두 같이 커진다).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Callable

from ... import coverage
from .. import texts as T
from ..controller import Controller, GuardError

# --------------------------------------------------------------------------- 색
INFO_FG, INFO_BG, INFO_BORDER = "#0b5394", "#eef3fb", "#9db7e0"  # 안내(파랑)
OK_FG, OK_BG = "#1b6e2f", "#e3f3e6"  # 성공(초록)
WARN_FG, WARN_BG = "#8a4b00", "#fff4d6"  # 주의(주황 글자 + 연노랑 바탕)
DANGER_FG, DANGER_BG = "#a40000", "#fde2e2"  # 위험(빨강 글자 + 연빨강 바탕)
SECRET_FG, SECRET_BG = DANGER_FG, DANGER_BG  # 예전 이름(민감한 정보 표시)
MUTED_FG = "#333333"  # 보조 글자(회색이지만 충분히 진하게)
TEXT_FG = "#111111"
TEXT_BG = "#f6f6f6"  # 읽기 전용 글상자 바탕
LIST_BG = "#ffffff"
TIP_BG = "#ffffe0"
CHIP_TODO_BG = "#efefef"
CHIP_DONE_BG = "#d6e4fa"
WARN_BTN_BG, WARN_BTN_HOVER = "#b45309", "#92400e"  # 주의색 단추(덮어쓰기 같은 되돌리기 어려운 실행)
PRIMARY_HOVER = "#094a82"
BTN_DISABLED_BG, BTN_DISABLED_FG = "#c9d1dd", "#5a6270"
PLAIN_BG, PLAIN_HOVER = "#e6e6e6", "#d6d6d6"
STOP_BTN_BG, STOP_BTN_HOVER = "#b3261e", "#8c1d18"  # 중지 단추(빨강, kind="danger")

# --------------------------------------------------------------------------- 글꼴(이름 있는 글꼴: 글자 크기 키우기에 맞춰 함께 바뀐다)
TITLE_FONT = "PcTitle"  # 16pt 굵게
SUB_FONT = "PcSub"  # 12pt 굵게
BOLD_FONT = "PcBold"  # 본문 크기 굵게
BODY_FONT = "TkDefaultFont"  # 11pt
HEADING_FONT = TITLE_FONT  # 예전 이름
BIG_FONT = TITLE_FONT
STAGE_FONT = SUB_FONT
TITLE_DELTA, SUB_DELTA = 5, 1  # 본문 크기에 더하는 값(11 -> 16, 12)
FONT_DELTA_RANGE = (-2, 6)


def setup_fonts(root: tk.Misc, body_size: int, family: str | None = None) -> None:
    """본문(TkDefaultFont 등)과 제목/소제목/굵은 글꼴을 body_size 기준으로 맞춘다. 여러 번 불러도 된다."""
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkFixedFont"):
        f = tkfont.nametofont(name, root=root)
        f.configure(size=body_size, **({"family": family} if family else {}))
    base = tkfont.nametofont("TkDefaultFont", root=root).actual()
    fam = family or base["family"]
    keep = root.__dict__.setdefault("_pc_fonts", {})  # 글꼴 객체가 사라지면 Tk 의 이름 있는 글꼴도 지워지므로 붙들어 둔다
    for name, size in ((TITLE_FONT, body_size + TITLE_DELTA), (SUB_FONT, body_size + SUB_DELTA), (BOLD_FONT, body_size)):
        if name in keep:
            keep[name].configure(family=fam, size=size, weight="bold")
        else:
            keep[name] = tkfont.Font(root=root, name=name, family=fam, size=size, weight="bold")


# --------------------------------------------------------------------------- 문구/경로 정리
_INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿"))
_QUOTES = "\"'“”‘’`"


def clean_path(text: str) -> str:
    """붙여넣은 경로를 정리한다: 앞뒤 공백/줄바꿈, 바깥 따옴표(복사한 경로에 붙는 "…"), 보이지 않는 방향 표시 문자를 없앤다."""
    s = (text or "").translate(_INVISIBLE).strip()
    while len(s) >= 2 and s[0] in _QUOTES and s[-1] in _QUOTES:
        s = s[1:-1].strip()
    return s


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


# --------------------------------------------------------------------------- 단추
class ActionButton(tk.Button):
    """눈에 띄는 단추(ttk 는 Windows 에서 배경색을 못 바꿔 tk 단추를 쓴다). ttk 단추처럼 state()/instate() 를 쓸 수 있다.

    kind: primary(파랑, 주 동작) / warn(주황, 되돌리기 어려운 실행) / danger(빨강, 중지) / plain(연회색, 보조) / link(밑줄 글자).
    """

    def __init__(self, parent: tk.Misc, text: str = "", command: Callable | None = None, kind: str = "primary", **kw) -> None:
        self.kind = kind
        self._disabled = False
        self._emph = True
        font = kw.pop("font", BOLD_FONT if kind in ("primary", "warn", "danger") else BODY_FONT)
        if kind == "link":
            kw.setdefault("padx", 2)
            kw.setdefault("pady", 1)
        else:
            kw.setdefault("padx", 16 if kind in ("primary", "warn", "danger") else 10)
            kw.setdefault("pady", 6 if kind in ("primary", "warn", "danger") else 4)
        super().__init__(parent, text=text, command=command, font=font, relief="flat", borderwidth=0, cursor="hand2",
                         takefocus=1, highlightthickness=2, **kw)
        self.bind("<Enter>", lambda e: self._paint(hover=True), add="+")
        self.bind("<Leave>", lambda e: self._paint(), add="+")
        self.bind("<FocusIn>", lambda e: self._paint(), add="+")
        self.bind("<FocusOut>", lambda e: self._paint(), add="+")
        self.bind("<Return>", lambda e: (self.invoke(), "break")[1], add="+")
        self.bind("<space>", lambda e: (self.invoke(), "break")[1], add="+")
        self._paint()

    # ---- ttk 호환
    def state(self, specs=None):  # type: ignore[override]
        if specs is None:
            return ("disabled",) if self._disabled else ()
        for s in ([specs] if isinstance(specs, str) else specs):
            if s == "disabled":
                self._disabled = True
            elif s == "!disabled":
                self._disabled = False
        super().configure(state="disabled" if self._disabled else "normal")
        self._paint()
        return ()

    def instate(self, specs, callback=None):
        want = [specs] if isinstance(specs, str) else list(specs)
        ok = all((s == "disabled" and self._disabled) or (s == "!disabled" and not self._disabled) or s not in ("disabled", "!disabled")
                 for s in want)
        return bool(callback() if ok and callback else ok)

    def emphasize(self, on: bool) -> None:
        """여러 단추 중 지금 눌러야 할 하나만 파랑으로 강조한다(나머지는 연회색)."""
        self._emph = bool(on)
        self._paint()

    def _paint(self, hover: bool = False) -> None:
        if self.kind == "link":
            fg = BTN_DISABLED_FG if self._disabled else INFO_FG
            super().configure(bg=self.master_bg(), fg=fg, activebackground=self.master_bg(), activeforeground=PRIMARY_HOVER,
                              disabledforeground=BTN_DISABLED_FG, highlightbackground=self.master_bg(), highlightcolor=INFO_FG,
                              font=self._link_font())
            return
        emph = self.kind in ("primary", "warn", "danger") and self._emph
        if self._disabled:
            bg, fg = (BTN_DISABLED_BG, BTN_DISABLED_FG) if self.kind in ("primary", "warn", "danger") else (PLAIN_BG, BTN_DISABLED_FG)
        elif emph and self.kind == "danger":
            bg, fg = (STOP_BTN_HOVER if hover else STOP_BTN_BG), "white"
        elif emph:
            bg = (WARN_BTN_HOVER if hover else WARN_BTN_BG) if self.kind == "warn" else (PRIMARY_HOVER if hover else INFO_FG)
            fg = "white"
        else:
            bg, fg = (PLAIN_HOVER if hover else PLAIN_BG), TEXT_FG
        super().configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, disabledforeground=fg,
                          highlightbackground=self.master_bg(), highlightcolor=TEXT_FG)

    def master_bg(self) -> str:
        return _bg_of(self.master)

    def _link_font(self):
        f = tkfont.nametofont(BODY_FONT, root=self).copy()
        f.configure(underline=True)
        self._lf = f  # 글꼴 객체가 사라지지 않게 붙들어 둔다
        return f


def bind_alt(root: tk.Misc, button: tk.Misc, active: Callable[[], bool] | None = None) -> None:
    """단추 글자에 '(B)' 처럼 영문자 단축키가 있으면 밑줄을 긋고 Alt+글자로 누를 수 있게 한다(없으면 아무것도 하지 않는다).

    active: 지금 이 단추가 속한 화면이 보이는 중인지(겹쳐 있는 다른 화면의 같은 단축키가 함께 눌리지 않게).
    """
    text = str(button.cget("text"))
    m = re.search(r"\(([A-Za-z])\)\s*$", text)
    if not m:
        return
    try:
        button.configure(underline=text.rindex(m.group(1)))
    except tk.TclError:
        return
    key = m.group(1)

    def fire(_e=None):
        if (active is None or active()) and "disabled" not in button.state():
            button.invoke()
            return "break"
        return None
    for k in (key.lower(), key.upper()):
        root.bind(f"<Alt-{k}>", fire, add="+")


# --------------------------------------------------------------------------- 안내 상자 / 접이식
class Notice(tk.Label):
    """색 상자 하나(안내/성공/주의/위험). 색에만 의존하지 않도록 문구 앞에 아이콘을 직접 넣어 쓴다."""
    KINDS = {"info": (INFO_BG, INFO_FG), "ok": (OK_BG, OK_FG), "warn": (WARN_BG, WARN_FG), "danger": (DANGER_BG, DANGER_FG)}

    def __init__(self, parent: tk.Misc, text: str = "", kind: str = "info", wrap_pad: int = 40, **kw) -> None:
        super().__init__(parent, text=text, anchor="w", justify="left", padx=10, pady=6, **kw)
        autowrap(self, wrap_pad)
        self.set(text, kind)

    def set(self, text: str, kind: str = "info") -> None:
        bg, fg = self.KINDS[kind]
        self.kind = kind
        self.configure(text=text, bg=bg, fg=fg)

    def clear(self) -> None:
        self.configure(text="", bg=self.winfo_toplevel().cget("background"))


def _bg_of(widget: tk.Misc) -> str:
    """위젯 뒤의 배경색(ttk 틀은 배경색 옵션이 없어 스타일에서 찾는다)."""
    try:
        return widget.cget("background")
    except tk.TclError:
        return ttk.Style(widget).lookup("TFrame", "background") or "#f0f0f0"


class Collapsible(tk.Frame):
    """'자세히 ▸' 접이식 영역. 머리글 단추를 누르면 body 가 펼쳐진다(처음에는 접혀 있다)."""

    def __init__(self, parent: tk.Misc, title: str = T.BTN_MORE, title_open: str = T.BTN_LESS, opened: bool = False,
                 bg: str | None = None, lead: str | None = None) -> None:
        """lead: 머리글 한 줄 요약 글(있으면 '요약 글  자세히 ▸' 가 같은 줄에 놓인다. lead_label 로 글을 바꾼다)."""
        self.bg = bg or _bg_of(parent)
        super().__init__(parent, bg=self.bg)
        self.title, self.title_open = title, title_open
        self.open = False
        self.lead_label: tk.Label | None = None
        if lead is None:
            self.toggle_btn = ActionButton(self, text=title, command=self.toggle, kind="link")
            self.toggle_btn.pack(anchor="w")
            self._head: tk.Misc = self.toggle_btn
        else:
            self._head = tk.Frame(self, bg=self.bg)
            self._head.pack(anchor="w", fill="x")
            self.lead_label = tk.Label(self._head, text=lead, bg=self.bg, anchor="w", justify="left")
            self.lead_label.pack(side="left")
            self.toggle_btn = ActionButton(self._head, text=title, command=self.toggle, kind="link")
            self.toggle_btn.pack(side="left", padx=(10, 0))
        self.body = tk.Frame(self, bg=self.bg)
        if opened:
            self.toggle()

    def toggle(self) -> None:
        self.set_open(not self.open)

    def set_open(self, on: bool) -> None:
        self.open = bool(on)
        if self.open:
            self.body.pack(fill="x", pady=(2, 4), after=self._head)
        else:
            self.body.pack_forget()
        self.toggle_btn.configure(text=self.title_open if self.open else self.title)

    def add_text(self, text: str, fg: str = MUTED_FG, pad: int = 24) -> tk.Label:
        lbl = tk.Label(self.body, text=text, justify="left", anchor="w", fg=fg, bg=self.bg)
        lbl.pack(anchor="w", fill="x", pady=2)
        autowrap(lbl, pad)
        return lbl


class CoverageBox(ttk.Frame):
    """프로그램별 설정 확인표: 프로그램 / 옮기는 설정 / 상태 / 담긴 파일 수. '이 PC에 없음' 행은 '해당 없음 N곳' 한 줄로 접고 펼치면 보인다."""

    STATUS_TAGS = {coverage.ST_OK: OK_FG, coverage.ST_PART: WARN_FG, coverage.ST_UNREAD: DANGER_FG,
                   coverage.ST_MANUAL: INFO_FG, coverage.ST_EXCL: MUTED_FG, coverage.ST_EMPTY: MUTED_FG}

    def __init__(self, parent: tk.Misc, title: str = T.COV_TITLE, max_rows: int = 8,
                 explorer: tuple[Callable[[str, bool], bool], Callable[[str, bool], bool]] | None = None) -> None:
        """explorer: (열 수 있는지 검사, 탐색기로 보여 주기) 함수 쌍. 주면 '빠진 파일 예시' 줄에 '탐색기에서 보기'가 생긴다(구 PC 에서만)."""
        super().__init__(parent)
        self.max_rows = max_rows
        self.explorer = explorer
        self.ex_paths: dict[str, tuple[str, bool] | None] = {}  # 예시 줄 -> (폴더 + 예시 이름의 절대 경로, 파일 선택) 또는 열 수 없음(None)
        self.title_label = ttk.Label(self, text=title, font=SUB_FONT)
        self.title_label.pack(anchor="w")
        cols = [("program", T.COV_COLS[0], 190), ("what", T.COV_COLS[1], 380), ("status", T.COV_COLS[2], 100),
                ("files", T.COV_COLS[3], 70)]
        self.frame, self.tree = make_tree(self, cols, height=3)
        self.frame.pack(fill="x", pady=(2, 0))
        self.open_btn: ActionButton | None = None
        self.open_note: tk.Label | None = None
        if explorer is not None:
            self.open_row = ttk.Frame(self)
            self.open_row.pack(fill="x", pady=(2, 0))
            self.open_btn = ActionButton(self.open_row, text=T.BTN_OPEN_EXPLORER, kind="plain", command=self.open_example)
            self.open_btn.pack(side="left")
            self.open_btn.state(["disabled"])
            add_tooltip(self.open_btn, T.TIP_OPEN_EXPLORER)
            self.open_note = tk.Label(self.open_row, text="", anchor="w", justify="left", fg=WARN_FG)
            self.open_note.pack(side="left", padx=(8, 0))
        for st, color in self.STATUS_TAGS.items():
            self.tree.tag_configure(st, foreground=color)
        self.full: dict[str, str] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, "") if col in (1, 2) else "")
        self.tree.tag_configure("exc", foreground=MUTED_FG)
        self.lines: set[str] = set()   # '제외 N개…' 줄(행 아래에 붙는 자식 행). 선택/눌러서 큰 파일 예시를 펼친다
        self.tree.bind("<ButtonRelease-1>", self._click_line, add="+")
        self.tree.bind("<Return>", self._key_line, add="+")
        if explorer is not None:
            self.tree.bind("<<TreeviewSelect>>", lambda e: self._example_selected(), add="+")
            self.tree.bind("<Double-Button-1>", self._double_example, add="+")
        self.noext = Notice(self, "", "warn")  # 확장자 없는 파일이 빠졌을 때만 보이는 노란 안내
        self._noext_shown = False
        self.fold = Collapsible(self, T.COV_FOLD.format(n=0), T.COV_FOLD_OPEN.format(n=0))
        self.fold_note = self.fold.add_text(T.COV_FOLD_NOTE)
        self.fold_list = tk.Label(self.fold.body, text="", justify="left", anchor="w", bg=self.fold.bg)
        self.fold_list.pack(anchor="w", fill="x")
        self._fold_shown = False
        self.rows_shown: list[dict] = []
        self.rows_hidden: list[dict] = []

    def clear(self) -> None:
        clear(self.tree)
        self.full = {}
        self.lines = set()
        self.ex_paths = {}
        self.rows_shown, self.rows_hidden = [], []
        self._set_noext(0)
        self._set_fold(0)

    def _set_noext(self, n: int) -> None:
        if n:
            self.noext.set(f"{T.ICON_WARN} {T.COV_NOEXT_HINT.format(n=n)}", "warn")
            if not self._noext_shown:
                self.noext.pack(fill="x", pady=(3, 0), after=self.open_row if self.open_btn is not None else self.frame)
                self._noext_shown = True
        elif self._noext_shown:
            self.noext.pack_forget()
            self._noext_shown = False

    def _toggle_line(self, iid: str, on: bool | None = None) -> None:
        if iid not in self.lines:
            return
        on = (not self.tree.item(iid, "open")) if on is None else on
        self.tree.item(iid, open=on)
        self.tree.set(iid, "status", T.BTN_LESS if on else T.BTN_MORE)
        self._fit_height()

    def _click_line(self, e) -> None:
        iid = self.tree.identify_row(e.y)
        if iid in self.lines:
            self._toggle_line(iid)

    def _key_line(self, _e=None) -> None:
        for iid in self.tree.selection():
            if iid in self.lines:
                self._toggle_line(iid)
            elif iid in self.ex_paths:
                self.open_example(iid)  # Enter: 예시 줄이면 탐색기에서 보기

    # ---- 빠진 파일 예시를 탐색기로 보기(열거나 실행하지 않는다)
    def _example_target(self, iid: str | None = None) -> tuple[str, bool] | None:
        if iid is None:
            sel = self.tree.selection()
            iid = sel[0] if sel else None
        return self.ex_paths.get(iid) if iid is not None else None

    def _example_selected(self) -> None:
        if self.open_btn is None or self.explorer is None:
            return
        sel = self.tree.selection()
        on_example = bool(sel) and sel[0] in self.ex_paths
        tgt = self._example_target() if on_example else None
        ok = tgt is not None and self.explorer[0](tgt[0], tgt[1])
        self.open_btn.state(["!disabled"] if ok else ["disabled"])
        if self.open_note is not None:
            self.open_note.configure(text=T.OPEN_NOT_HERE if on_example and not ok else "")

    def open_example(self, iid: str | None = None) -> bool:
        if self.explorer is None:
            return False
        tgt = self._example_target(iid)
        if tgt is None or not self.explorer[0](tgt[0], tgt[1]):
            if self.open_note is not None:
                self.open_note.configure(text=T.OPEN_NOT_HERE)
            return False
        ok = self.explorer[1](tgt[0], tgt[1])
        if self.open_note is not None:
            self.open_note.configure(text="" if ok else T.OPEN_FAILED)
        return ok

    def _double_example(self, e) -> str | None:
        iid = self.tree.identify_row(e.y)
        if iid and iid in self.ex_paths:
            self.tree.selection_set(iid)
            self.open_example(iid)
            return "break"
        return None

    @staticmethod
    def example_abs_path(folder: str, name: str) -> tuple[str, bool] | None:
        """설정 폴더 경로와 폴더 기준 예시 이름을 합쳐 절대 경로를 만든다. 안전한 로컬 경로를 만들 수 없으면 None(탐색기에서 볼 수 없음)."""
        import ntpath
        from ..explorer import clean_local_path
        if not folder or not name or ntpath.isabs(name) or ntpath.splitdrive(name)[0]:
            return None
        joined = clean_local_path(ntpath.join(folder, name.replace("/", "\\")))
        return (joined, True) if joined else None

    def _fit_height(self) -> None:
        n = 0
        for top in self.tree.get_children(""):
            n += 1
            for ln in self.tree.get_children(top):
                n += 1
                if self.tree.item(ln, "open"):
                    n += len(self.tree.get_children(ln))
        self.tree.configure(height=min(self.max_rows + 5, max(2, n)))

    def _set_fold(self, n: int) -> None:
        if n and not self._fold_shown:
            self.fold.pack(anchor="w", fill="x")
            self._fold_shown = True
        elif not n and self._fold_shown:
            self.fold.pack_forget()
            self._fold_shown = False
        self.fold.title, self.fold.title_open = T.COV_FOLD.format(n=n), T.COV_FOLD_OPEN.format(n=n)
        self.fold.toggle_btn.configure(text=self.fold.title_open if self.fold.open else self.fold.title)

    def show(self, table: list[dict]) -> None:
        """table: coverage.rows() 의 행들. 비어 있으면 안내 한 줄만."""
        clear(self.tree)
        self.full = {}
        self.lines = set()
        self.ex_paths = {}
        if self.open_btn is not None:
            self.open_btn.state(["disabled"])
            self.open_note.configure(text="")
        shown, hidden = coverage.fold(table)
        self.rows_shown, self.rows_hidden = shown, hidden
        if not table:
            self.tree.insert("", "end", values=("", T.COV_NONE, "", ""))
        for r in shown:
            tail = f" — {r['note']}" if r["note"] else ""
            iid = self.tree.insert("", "end", tags=(r["status"],), values=(r["program"], r["what"], r["status"], f"{r['files']}개"))
            self.full[iid] = f"{r['program']}: {r['what']}{tail}"
            if r.get("excluded_line"):  # 빠진 파일 한 줄 요약(+ 눌러서 펼치는 큰 파일 예시, 읽기 전용)
                ln = self.tree.insert(iid, "end", tags=("exc",), open=False,
                                      values=("", T.COV_EXC_INDENT + r["excluded_line"], T.BTN_MORE, ""))
                self.lines.add(ln)
                self.tree.item(iid, open=True)
                self.full[ln] = r["excluded_line"]
                for e in r.get("excluded_examples", []):
                    ex = self.tree.insert(ln, "end", tags=("exc",), values=("", T.COV_EX_INDENT + coverage.example_text(e), "", ""))
                    self.full[ex] = coverage.example_text(e)
                    if self.explorer is not None:
                        self.ex_paths[ex] = self.example_abs_path(str(r.get("path") or ""), str(e.get("name") or ""))
        self.tree.configure(height=min(self.max_rows, max(2, len(shown) + len(self.lines) or 1)))
        self._set_noext(coverage.noext_total(shown))
        self.fold_list.configure(text="\n".join(f"· {r['program']} — {r['what']}" for r in hidden))
        self._set_fold(len(hidden))


class StepStrip(tk.Frame):
    """단계 표시줄: 번호와 이름이 함께 보인다(끝낸 단계 ✔, 지금 단계는 파랑)."""

    def __init__(self, parent: tk.Misc, names: tuple[str, ...], label: str = "") -> None:
        super().__init__(parent, bg=INFO_BG, padx=8, pady=4)
        self.chips: list[tk.Label] = []
        self.names: tuple[str, ...] = ()
        if label:
            tk.Label(self, text=label, bg=INFO_BG, fg=MUTED_FG).pack(side="left", padx=(0, 8))
        self.holder = tk.Frame(self, bg=INFO_BG)
        self.holder.pack(side="left")
        self.current = 0
        self.set_names(names)

    def set_names(self, names: tuple[str, ...]) -> None:
        if names == self.names:
            return
        for w in self.holder.winfo_children():
            w.destroy()
        self.chips = []
        self.names = names
        for i, name in enumerate(names):
            chip = tk.Label(self.holder, text=f"{i + 1} {name}", padx=8, pady=2)
            chip.pack(side="left", padx=(0, 4))
            self.chips.append(chip)
            if i < len(names) - 1:
                tk.Label(self.holder, text="→", bg=INFO_BG, fg=MUTED_FG).pack(side="left", padx=(0, 4))
        self.set(self.current)

    def set(self, current: int) -> None:
        """current: 지금 단계(1부터). 그보다 앞은 끝낸 단계."""
        self.current = current
        for i, chip in enumerate(self.chips, 1):
            name = self.names[i - 1]
            if i < current:
                chip.configure(text=f"{T.ICON_OK} {i} {name}", bg=CHIP_DONE_BG, fg=OK_FG, font=BODY_FONT)
            elif i == current:
                chip.configure(text=f"{i} {name}", bg=INFO_FG, fg="white", font=BOLD_FONT)
            else:
                chip.configure(text=f"{i} {name}", bg=CHIP_TODO_BG, fg=MUTED_FG, font=BODY_FONT)


# --------------------------------------------------------------------------- 스크롤 본문
class ScrollBody(ttk.Frame):
    """세로로 스크롤되는 본문 영역. 내용이 아래로 더 있으면 '아래에 더 있습니다' 표시가 뜬다. 마우스 휠은 앱 전체 처리기가 맡는다."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._inner_changed)
        self.canvas.bind("<Configure>", self._canvas_changed)
        self.canvas.pc_wheel = self.wheel  # type: ignore[attr-defined]
        self.canvas.pc_see = self.see  # type: ignore[attr-defined]
        self.more = tk.Label(self, text=T.MORE_BELOW, bg=INFO_FG, fg="white", padx=12, pady=3, font=BOLD_FONT)
        self.more_visible = False

    def _inner_changed(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.after_idle(self.update_more)

    def _canvas_changed(self, e) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)
        self.after_idle(self.update_more)

    def _on_scroll(self, lo, hi) -> None:
        self.bar.set(lo, hi)
        self.update_more()

    def can_scroll(self) -> bool:
        return self.inner.winfo_reqheight() > self.canvas.winfo_height() + 1

    def at_bottom(self) -> bool:
        lo, hi = self.canvas.yview()
        return hi >= 0.995

    def update_more(self) -> None:
        """내용이 창보다 길고 맨 아래가 아니면 하단에 안내를 띄운다."""
        show = self.can_scroll() and not self.at_bottom()
        if show and not self.more_visible:
            self.more.place(relx=0.5, rely=1.0, anchor="s", y=-6)
            self.more.lift()
        elif not show and self.more_visible:
            self.more.place_forget()
        self.more_visible = show

    def wheel(self, delta: int) -> None:
        if self.can_scroll():
            self.canvas.yview_scroll(int(-delta / 120) or (-1 if delta > 0 else 1), "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)

    def see(self, widget: tk.Misc) -> None:
        """Tab 키로 포커스가 옮겨진 위젯이 가려져 있으면 보이는 곳까지 스크롤한다(키보드만 쓰는 사람도 아래 입력칸에 닿도록).

        이미 완전히 보이는 위젯은 건드리지 않는다. 화면 좌표로 가려진 정도를 재므로 레이아웃이 아직 바뀌는 중에도 엉뚱하게 움직이지 않는다.
        """
        try:
            self.update_idletasks()
            c_top, c_h = self.canvas.winfo_rooty(), self.canvas.winfo_height()
            w_top, w_h = widget.winfo_rooty(), widget.winfo_height()
            if c_h <= 1 or w_h <= 1 or not self.can_scroll():
                return
            region = self.canvas.bbox("all")
            total = max(1, (region[3] - region[1]) if region else self.inner.winfo_height())
            lo, _hi = self.canvas.yview()
            if w_top < c_top:
                delta = w_top - c_top - 8
            elif w_top + w_h > c_top + c_h:
                delta = (w_top + w_h) - (c_top + c_h) + 8
            else:
                return
            self.canvas.yview_moveto(max(0.0, min(1.0, lo + delta / total)))
        except tk.TclError:
            pass

    def scroll_to(self, widget: tk.Misc) -> None:
        """widget(본문 안의 위젯)의 윗부분이 보이도록 스크롤한다. 결과가 나왔을 때 접힌 아래쪽 결과를 바로 보여 주는 데 쓴다."""
        self.update_idletasks()
        total = max(1, self.inner.winfo_reqheight())
        y = widget.winfo_rooty() - self.inner.winfo_rooty()
        self.canvas.yview_moveto(max(0.0, min(1.0, (y - 6) / total)))
        self.update_more()


def install_wheel(root: tk.Misc) -> None:
    """마우스 휠: 포인터 아래 위젯에서 위로 올라가며 스크롤 영역(pc_wheel)을 찾는다. 표/글상자는 스스로 스크롤하므로 건드리지 않는다."""

    def handler(e):
        w = getattr(e, "widget", None)
        while w is not None:
            if isinstance(w, (tk.Text, ttk.Treeview, tk.Listbox, ttk.Combobox)):
                return None
            fn = getattr(w, "pc_wheel", None)
            if fn is not None:
                fn(e.delta)
                return None
            w = getattr(w, "master", None)
        return None
    root.bind_all("<MouseWheel>", handler, add="+")

    def focus_handler(e):
        w = getattr(e, "widget", None)
        try:
            if w is None or str(root.focus_get()) != str(w):  # 포커스를 실제로 받은 위젯만(위쪽 틀들에도 같은 이벤트가 온다)
                return None
        except (KeyError, tk.TclError):
            return None
        while w is not None:
            fn = getattr(w, "pc_see", None)
            if fn is not None:
                target = e.widget
                target.after_idle(lambda: fn(target))  # 레이아웃이 자리 잡은 뒤에 가려졌는지 본다
                return None
            w = getattr(w, "master", None)
        return None
    root.bind_all("<FocusIn>", focus_handler, add="+")


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
        tk.Label(self.tip, text=self.text, background=TIP_BG, fg=TEXT_FG, relief="solid", borderwidth=1, justify="left",
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


class ExplorerLink:
    """표의 줄을 탐색기로 '보기만' 하는 기능을 붙인다(stage34). 파일을 열거나 실행하지 않는다.

    - '폴더 보기' 단추(self.button), 줄 더블클릭(체크 칸 제외), Enter 키. 체크 칸의 더블클릭은 원래대로 체크를 바꾼다.
    - resolve(iid) -> (경로, 파일을 선택해서 보여 줄지) 또는 None. 경로는 컨트롤러가 검사한다(존재하는 로컬 디스크 경로만).
      열 수 없으면 단추를 끄고 '이 PC에서는 열 수 없습니다' 안내(self.note)를 보인다.
    - 표를 다시 그린 뒤에는 refresh() 를 불러 단추 상태를 맞춘다.
    """

    def __init__(self, view, button_parent: tk.Misc, text: str = "", note_parent: tk.Misc | None = None,
                 note_pack: dict | None = None) -> None:
        self.view, self.c = view, view.c
        self.button = ActionButton(button_parent, text=text or T.BTN_OPEN_FOLDER, kind="plain", command=self.open_selected)
        self.button.state(["disabled"])
        add_tooltip(self.button, T.TIP_OPEN_EXPLORER)
        self.note = tk.Label(note_parent or button_parent, text="", anchor="w", justify="left", fg=WARN_FG)
        add_tooltip(self.note, T.OPEN_NOT_HERE_WHY)
        self.note_pack = note_pack or {"side": "right", "padx": (0, 6)}
        self._trees: dict = {}  # tree -> (resolve, click, undo, check_col)
        self._active = None

    def attach(self, tree: ttk.Treeview, resolve: Callable[[str], tuple[str, bool | None] | None], click: Callable | None = None,
               undo: Callable[[str], None] | None = None, check_col: str | None = "#1") -> None:
        """click: 체크 칸 더블클릭 때 두 번째 클릭을 처리(체크 바꾸기). undo: 첫 클릭이 바꾼 체크를 되돌린다(체크 칸이 아닌 곳을 더블클릭할 때)."""
        self._trees[tree] = (resolve, click, undo, check_col)
        tree.bind("<<TreeviewSelect>>", lambda e, t=tree: self._selected(t), add="+")
        tree.bind("<Double-Button-1>", lambda e, t=tree: self.on_double(t, e), add="+")
        tree.bind("<Return>", lambda e, t=tree: self.on_enter(t), add="+")

    # ---- 대상
    def target(self) -> tuple[str, bool | None] | None:
        tree = self._active
        if tree is None or tree not in self._trees:
            return None
        sel = tree.selection()
        return self._trees[tree][0](sel[0]) if sel else None

    def refresh(self) -> None:
        """고른 줄에 맞춰 단추를 켜거나 끄고, 열 수 없으면 안내를 보인다."""
        tgt = self.target()
        ok = tgt is not None and self.c.can_show_in_explorer(tgt[0], tgt[1])
        self.button.state(["!disabled"] if ok else ["disabled"])
        self._note("" if ok or self._active is None or not self._active.selection() else T.OPEN_NOT_HERE)

    def _note(self, text: str) -> None:
        self.note.configure(text=text)
        if text and not self.note.winfo_manager():
            self.note.pack(**self.note_pack)
        elif not text and self.note.winfo_manager():
            self.note.pack_forget()

    def _selected(self, tree) -> None:
        self._active = tree
        self.refresh()

    # ---- 동작
    def open_selected(self) -> bool:
        return self._open(self.target())

    def _open(self, tgt: tuple[str, bool | None] | None) -> bool:
        if tgt is None:
            return False
        if not self.c.can_show_in_explorer(tgt[0], tgt[1]):
            self.button.state(["disabled"])
            self._note(T.OPEN_NOT_HERE)
            return False
        if not self.c.show_in_explorer(tgt[0], tgt[1]):
            self._note(T.OPEN_FAILED)
            return False
        self._note("")
        return True

    def on_enter(self, tree) -> str:
        self._active = tree
        self.open_selected()
        return "break"

    def on_double(self, tree, event) -> str | None:
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        iid = tree.identify_row(event.y)
        if not iid:
            return None
        resolve, click, undo, check_col = self._trees[tree]
        if check_col is not None and tree.identify_column(event.x) == check_col:
            if click is not None:
                click(event)  # 체크 칸은 더블클릭도 체크 바꾸기(열지 않는다)
            return "break"
        tgt = resolve(iid)  # 체크를 되돌리면 표를 다시 그려 줄 번호가 바뀌므로 먼저 대상을 정해 둔다
        self._active = tree
        if undo is not None:
            undo(iid)  # 첫 클릭이 바꾼 체크를 되돌린다(열기만 하고 선택은 그대로)
        try:
            tree.selection_set(iid)
        except tk.TclError:
            pass
        self._open(tgt)
        return "break"


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
        tk.Label(self.tip, text=text, background=TIP_BG, fg=TEXT_FG, relief="solid", borderwidth=1, justify="left",
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
    text = tk.Text(frame, height=height, wrap="word", state="disabled", relief="flat", background=TEXT_BG, foreground=TEXT_FG)
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


def table_hint(parent: tk.Misc) -> ttk.Label:
    """표 아래 한 줄 안내(Space 로 선택)."""
    lbl = ttk.Label(parent, text=T.SPACE_HINT, foreground=MUTED_FG)
    return lbl


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


# --------------------------------------------------------------------------- 경로 입력칸
class PathField(ttk.Frame):
    """경로 입력칸: 윗줄에 이름표와 짧은 도움말, 가운데 줄에 입력칸(한 줄 전체), 아랫줄에 찾아보기 + (선택) 보조 단추와 드라이브 선택 콤보.

    입력칸을 한 줄 전체로 두어 900px 같은 좁은 창에서도 파일 이름 끝이 잘리지 않는다. 붙여넣은 경로의 따옴표/앞뒤 공백은
    입력칸을 벗어나거나 붙여넣을 때 자동으로 지운다.
    """

    def __init__(self, parent: tk.Misc, var: tk.StringVar, browse: Callable[[], None], label: str = "", hint: str = "",
                 drives: Callable[[], list] | None = None, on_drive: Callable[[str], None] | None = None,
                 extra: list[tuple[str, Callable[[], None]]] | None = None) -> None:
        super().__init__(parent)
        self.var, self.drives, self.on_drive = var, drives, on_drive
        self.columnconfigure(0, weight=1)
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        self.label = ttk.Label(top, text=label, font=SUB_FONT)
        self.label.pack(side="left", anchor="n", padx=(0, 10))
        self.hint = ttk.Label(top, text=hint, foreground=MUTED_FG, justify="left")
        self.hint.pack(side="left", fill="x", expand=True, anchor="n")
        autowrap(self.hint, 280)
        self.entry = ttk.Entry(self, textvariable=var, width=40)
        self.entry.grid(row=1, column=0, sticky="ew", pady=(2, 0))  # 입력칸은 한 줄 전체
        self.buttons = ttk.Frame(self)  # 단추 줄(입력칸 바로 아래)
        self.buttons.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.browse_btn = ttk.Button(self.buttons, text=T.BTN_BROWSE, command=browse)
        self.browse_btn.grid(row=0, column=0)
        col = 1
        self.extra_btns: list[ttk.Button] = []
        for text, cmd in extra or []:
            b = ttk.Button(self.buttons, text=text, command=cmd)
            b.grid(row=0, column=col, padx=(6, 0))
            self.extra_btns.append(b)
            col += 1
        self.drive_box: ttk.Combobox | None = None
        self._drive_map: dict[str, str] = {}
        if drives is not None:
            self.drive_var = tk.StringVar(value=T.DRIVE_PICK_PLACEHOLDER)
            self.drive_box = ttk.Combobox(self.buttons, textvariable=self.drive_var, state="readonly", width=24, postcommand=self._fill_drives)
            self.drive_box.grid(row=0, column=col, padx=(6, 0))
            self.drive_box.bind("<<ComboboxSelected>>", self._picked)
        self.entry.bind("<FocusOut>", self.normalize, add="+")
        self.entry.bind("<Return>", self.normalize, add="+")
        self.entry.bind("<<Paste>>", lambda e: self.after(20, self.normalize), add="+")

    def normalize(self, _e=None) -> None:
        cleaned = clean_path(self.var.get())
        if cleaned != self.var.get():
            self.var.set(cleaned)

    def set_hint(self, text: str) -> None:
        self.hint.configure(text=text)

    def _fill_drives(self) -> None:
        entries = self.drives() if self.drives else []
        self._drive_map = {}
        values = []
        for ent in entries:
            kind = T.DRIVE_KINDS.get(ent.kind, T.DRIVE_KINDS["unknown"])
            free = ent.free_text or T.TR_FS_UNKNOWN
            label = T.DRIVE_ITEM.format(root=ent.root, kind=kind, free=free)
            self._drive_map[label] = ent.root
            values.append(label)
        self.drive_box.configure(values=values or [T.DRIVE_NONE])  # type: ignore[union-attr]

    def _picked(self, _e=None) -> None:
        root = self._drive_map.get(self.drive_var.get())
        self.drive_var.set(T.DRIVE_PICK_PLACEHOLDER)
        if root and self.on_drive:
            self.on_drive(root)


# --------------------------------------------------------------------------- 결과 요약 / 안내 상자
class ResultSummary(ttk.Frame):
    """'성공 N / 건너뜀 N / 실패 N' 큰 요약 + 안내 상자(실패면 빨간 상자와 해야 할 일 한 줄)."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.ok_label = ttk.Label(row, text="", font=TITLE_FONT, foreground=OK_FG)
        self.skip_label = ttk.Label(row, text="", font=TITLE_FONT, foreground=MUTED_FG)
        self.fail_label = ttk.Label(row, text="", font=TITLE_FONT)
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
        """pending: 복사는 끝났지만 검사를 미룬 상태 — '성공'이라고 하지 않고 '복사 완료(내용 확인 전)'로 표시한다."""
        if pending:
            self.ok_label.configure(text=f"{T.ICON_WARN} {T.TR_PENDING_LABEL} {ok}", foreground=WARN_FG)
        else:
            self.ok_label.configure(text=f"{T.ICON_OK} {T.RESULT_OK} {ok}", foreground=OK_FG)
        self.skip_label.configure(text=f"{T.RESULT_SKIPPED} {skipped}")
        self.fail_label.configure(text=f"{T.ICON_FAIL if failed else ''} {T.RESULT_FAILED} {failed}".strip(),
                                  foreground=DANGER_FG if failed else MUTED_FG)
        if failed:
            self.box.configure(text=fail_message, bg=DANGER_BG, fg=DANGER_FG)
        elif pending:
            self.box.configure(text=ok_message, bg=WARN_BG, fg=WARN_FG)
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
    """모든 화면의 부모. 컨트롤러 이벤트를 받고, 제목줄(?)과 맨 아래 고정 이동 막대(처음 화면으로/이전 ... 주 버튼)를 제공한다."""
    name = ""

    def __init__(self, app, controller: Controller) -> None:
        super().__init__(app.body, padding=(14, 10))
        self.app, self.c = app, controller
        self.primary: tk.Misc | None = None  # Enter 키로 실행되는 기본 버튼
        self.back_btn: ttk.Button | None = None  # '이전' 버튼
        self.next_btn: ActionButton | None = None
        self.home_btn: ttk.Button | None = None
        self.bar_right: ttk.Frame | None = None
        self._nav: ttk.Frame | None = None
        self.next_reason: ttk.Label | None = None

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

    def header(self, title: str, sub: str = "", roadmap: bool = True) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.title_label = ttk.Label(row, text=title, font=TITLE_FONT)
        self.title_label.pack(side="left")
        self.help_btn = ttk.Button(row, text=T.BTN_HELP, width=3, command=lambda: self.app.show_help(self.name))
        self.help_btn.pack(side="right")
        add_tooltip(self.help_btn, T.HELP_TOOLTIP)
        self.roadmap_btn = ttk.Button(row, text=T.BTN_ROADMAP, command=lambda: self.app.show_roadmap())
        if roadmap:
            self.roadmap_btn.pack(side="right", padx=6)
        self.sub_label = None
        if sub:
            self.sub_label = ttk.Label(self, text=sub, justify="left")
            self.sub_label.pack(anchor="w", fill="x", pady=(2, 8))
            autowrap(self.sub_label)

    def navbar(self, back: Callable | None = None, back_text: str = T.BTN_BACK,
               next_: Callable | None = None, next_text: str = T.BTN_NEXT, next_kind: str = "primary") -> dict[str, tk.Misc]:
        """맨 아래 고정 이동 막대. 왼쪽: 처음 화면으로 / 이전. 오른쪽 끝: 주 버튼(파랑). 만든 직후 pack 해 두고 finish() 에서 Tab 순서를 맨 뒤로 보낸다."""
        outer = ttk.Frame(self)
        outer.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Separator(outer).pack(fill="x", pady=(0, 8))
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        self._nav = outer
        btns: dict[str, tk.Misc] = {}
        self.home_btn = ttk.Button(bar, text=T.BTN_HOME, command=lambda: self.app.go_home())
        self.home_btn.pack(side="left")
        if back:
            self.back_btn = btns["back"] = ttk.Button(bar, text=back_text, command=back)
            btns["back"].pack(side="left", padx=6)
        self.bar_right = ttk.Frame(bar)
        self.bar_right.pack(side="right")
        if next_:
            self.next_btn = btns["next"] = ActionButton(self.bar_right, text=next_text, command=next_, kind=next_kind)
            btns["next"].pack(side="right")
        self.next_reason = ttk.Label(self.bar_right, text="", foreground=WARN_FG, justify="right")
        if next_:
            self.next_reason.pack(side="right", padx=10)  # '다음'이 막혀 있을 때 그 이유
        for b in (self.home_btn, self.back_btn, self.next_btn):
            if b is not None:
                bind_alt(self.app, b, lambda: self.app.current == self.name)
        return btns

    def add_action(self, text: str, command: Callable, kind: str = "primary", padx: int = 0) -> ActionButton:
        """이동 막대 오른쪽에 주 동작 단추를 더한다(먼저 만든 것이 가장 오른쪽)."""
        assert self.bar_right is not None
        b = ActionButton(self.bar_right, text=text, command=command, kind=kind)
        b.pack(side="right", padx=(padx, 0))
        return b

    def set_next_reason(self, text: str) -> None:
        if self.next_reason is not None:
            self.next_reason.configure(text=text)

    def finish(self) -> None:
        """화면 구성이 끝난 뒤 호출: 이동 막대를 Tab 순서 맨 뒤로(본문 → 이동 단추 순서로 이동)."""
        if self._nav is not None:
            self._nav.lift()

    def license_link(self, parent: tk.Misc) -> ActionButton:
        """라이선스 안내는 한 줄 링크로만 둔다(크게 보이는 곳은 시작 화면과 마지막 결과 화면뿐)."""
        return ActionButton(parent, text=T.LICENSE_LINK, kind="link",
                            command=lambda: TextDialog(self, T.LICENSE_LINK_TITLE, T.LICENSE_NOTE, 60, 4))
