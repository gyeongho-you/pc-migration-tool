"""Tkinter 마법사 창. 실행: python -m pcmigrate.gui  또는  pcmigrate gui"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import state as S
from . import texts as T
from .controller import Controller, FriendlyError
from .views.common import (MUTED_FG, STAGE_FONT, BaseView, TextDialog, autowrap)
from .views.new_pc import ConfirmView, PackageView, PreviewView, ResultView
from .views.old_pc import ChecklistView, DiscoverView, ExportView, ScanView
from .views.start import StartView
from .views.transfer import TransferView

LOG_NAME = "pcmigrate-gui.log"
MIN_SIZE = (900, 640)


def enable_high_dpi() -> None:
    """Windows 고DPI 에서 글자가 흐려지지 않게 한다(실패해도 무시)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def setup_logging(path: str | None = None) -> str:
    """%TEMP%\\pcmigrate-gui.log 에 이벤트 종류만 기록한다(설치 목록/경로 내용은 남기지 않는다)."""
    path = path or os.path.join(tempfile.gettempdir(), LOG_NAME)
    log = logging.getLogger("pcmigrate.gui")
    if not any(isinstance(h, logging.FileHandler) for h in log.handlers):
        h = logging.FileHandler(path, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return path


class ErrorDialog(tk.Toplevel):
    """오류 대화상자: '무엇이 잘못됐나요 / 어떻게 하면 되나요' 두 줄 + 접힌 '상세 보기'(traceback)."""

    def __init__(self, parent: tk.Misc, err: FriendlyError) -> None:
        super().__init__(parent)
        self.title(err.title)
        self.transient(parent)
        self.resizable(True, True)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="x")
        self.what_label = ttk.Label(body, text=err.what, wraplength=540, justify="left", font=STAGE_FONT)
        self.what_label.pack(anchor="w", fill="x")
        self.how_label = None
        if err.how:
            ttk.Label(body, text=T.ERR_HOW, foreground=MUTED_FG).pack(anchor="w", pady=(8, 0))
            self.how_label = ttk.Label(body, text=err.how, wraplength=540, justify="left")
            self.how_label.pack(anchor="w", fill="x")
        self.detail = tk.Text(self, height=10, width=80, wrap="none")
        self.detail.insert("1.0", err.detail or T.NO_DETAIL)
        self.detail.configure(state="disabled")
        self.shown = False
        bar = ttk.Frame(self, padding=(12, 0, 12, 12))
        bar.pack(fill="x", side="bottom")
        self.toggle = ttk.Button(bar, text=T.BTN_DETAIL_SHOW, command=self._toggle)
        self.toggle.pack(side="left")
        self.ok_btn = ttk.Button(bar, text=T.BTN_OK, command=self.destroy)
        self.ok_btn.pack(side="right")
        self.bind("<Return>", self.close_event)
        self.bind("<Escape>", self.close_event)
        self.ok_btn.focus_set()
        self.grab_set()

    def close_event(self, _e=None) -> str:
        """Enter/Esc 로 닫기(다른 화면의 Enter/Esc 동작이 겹치지 않게 'break')."""
        self.destroy()
        return "break"

    def _toggle(self) -> None:
        self.shown = not self.shown
        if self.shown:
            self.detail.pack(fill="both", expand=True, padx=12, pady=(0, 8))
            self.toggle.configure(text=T.BTN_DETAIL_HIDE)
        else:
            self.detail.pack_forget()
            self.toggle.configure(text=T.BTN_DETAIL_SHOW)


VIEW_CLASSES = (StartView, ScanView, DiscoverView, ChecklistView, ExportView, PackageView, PreviewView, ConfirmView,
                ResultView, TransferView)


class App(tk.Tk):
    def __init__(self, controller: Controller | None = None) -> None:
        super().__init__()
        self.c = controller or Controller()
        self.title(T.APP_TITLE)
        self.geometry("1040x760")
        self.minsize(*MIN_SIZE)
        self.current = S.START
        self.help_dialog: TextDialog | None = None
        self._setup_style()
        self._build_header()
        self._build_footer()  # 아래쪽 위젯을 먼저 pack 해야 본문이 늘어나도 잘리지 않는다
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)
        self.body.rowconfigure(0, weight=1)
        self.body.columnconfigure(0, weight=1)
        self.views: dict[str, BaseView] = {}
        for cls in VIEW_CLASSES:
            v = cls(self, self.c)
            v.grid(row=0, column=0, sticky="nsew")
            self.views[cls.name] = v
        self.c.listener = self.on_event
        self.bind_all("<Return>", self._on_return)
        self.bind_all("<Escape>", self._on_escape)
        self.bind_all("<F1>", lambda e: (self.show_help(self.current), "break")[1])
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.show(S.START)
        self.after(100, self._tick)

    # ------------------------------------------------------------------ 구성
    def _setup_style(self) -> None:
        """글꼴(맑은 고딕 10pt)을 통일하고, 표 줄 높이/버튼 여백을 글꼴에 맞춘다."""
        try:
            families = set(tkfont.families())
            fam = next((f for f in T.FONT_FAMILIES if f in families), None)
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkFixedFont"):
                f = tkfont.nametofont(name)
                f.configure(size=T.FONT_SIZE, **({"family": fam} if fam else {}))
            style = ttk.Style(self)
            line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
            style.configure("Treeview", rowheight=line + 8)
            style.configure("TButton", padding=(10, 4))
            style.configure("TCheckbutton", padding=(2, 2))
        except tk.TclError:
            pass

    def _build_header(self) -> None:
        bar = ttk.Frame(self, padding=(10, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text=T.APP_TITLE, font=("TkDefaultFont", 12, "bold")).pack(side="left")
        info = self.c.system_info()
        self.env_label = ttk.Label(bar, text=f"{info.os_name} · {T.HEADER_ADMIN[info.is_admin]}", foreground="#444444")
        self.env_label.pack(side="right")
        ttk.Separator(self).pack(fill="x")
        self.crumb = ttk.Label(self, text="", padding=(10, 4), foreground="#0b5394")
        self.crumb.pack(fill="x")

    def _build_footer(self) -> None:
        ttk.Separator(self).pack(fill="x", side="bottom")
        bar = ttk.Frame(self, padding=(10, 6))
        bar.pack(fill="x", side="bottom")
        right = ttk.Frame(bar)
        right.pack(side="right", padx=(10, 0))
        self.progress = ttk.Progressbar(right, mode="indeterminate", length=170)
        self.progress.pack(side="top")
        self.cancel_btn = ttk.Button(right, text=T.BTN_CANCEL, command=self.c.cancel)
        left = ttk.Frame(bar)
        left.pack(side="left", fill="x", expand=True)
        self.stage = ttk.Label(left, text=T.READY, font=STAGE_FONT)  # 지금 하는 일(크게)
        self.stage.pack(anchor="w")
        self.status = ttk.Label(left, text="", justify="left")  # 진행 메시지와 예상 시간
        self.status.pack(anchor="w", fill="x")
        autowrap(self.status, 20)
        self.note = ttk.Label(left, text="", foreground=MUTED_FG)
        self.note.pack(anchor="w")

    # ------------------------------------------------------------------ 화면/도움말/키
    def show(self, screen: str) -> None:
        view = self.views[screen]
        self.current = screen
        view.tkraise()
        self.crumb.configure(text=T.CRUMB_HOME if screen == S.START else T.TITLES.get(screen, screen))
        view.on_show()

    def go_home(self) -> None:
        if self.c.state.busy:
            messagebox.showinfo(T.STAGE_LABEL, T.GO_HOME_BUSY, parent=self)
            return
        self.c.go(S.START)

    def show_help(self, screen: str) -> TextDialog:
        """이 화면의 도움말(무엇을 하는 화면 / 안전한가 / 다음 단계)을 작은 창으로 보여 준다."""
        if self.help_dialog is not None:
            try:
                self.help_dialog.destroy()
            except tk.TclError:
                pass
        title = T.HELP_TITLE.format(screen=T.TITLES.get(screen, T.CRUMB_HOME))
        self.help_dialog = TextDialog(self, title, T.HELP.get(screen, ""))
        return self.help_dialog

    def _on_return(self, event):
        w = self.focus_get()
        if w is None or w.winfo_toplevel() is not self or isinstance(w, tk.Text):
            return None
        if isinstance(w, ttk.Button):
            w.invoke()
            return "break"
        btn = self.views[self.current].primary
        if btn is not None and "disabled" not in btn.state():
            btn.invoke()
            return "break"
        return None

    def _on_escape(self, event):
        w = self.focus_get()
        if w is not None and w.winfo_toplevel() is not self:
            return None
        if self.c.state.busy:
            if self.c.can_cancel:
                self.c.cancel()
            return "break"
        back = self.views[self.current].back_btn
        if back is not None and "disabled" not in back.state():
            back.invoke()
            return "break"
        return None

    # ------------------------------------------------------------------ 이벤트
    def on_event(self, kind: str, payload) -> None:
        if kind == "screen":
            self.show(payload)
            return
        if kind == "busy":
            if payload:
                self.stage.configure(text=f"{T.STAGE_LABEL}: {payload}")
                self.note.configure(text=T.BUSY_NOTE)
                self.progress.start(12)
                if self.c.can_cancel:
                    self.cancel_btn.pack(side="top", pady=(4, 0))
            else:
                self.progress.stop()
                self.cancel_btn.pack_forget()
                self.stage.configure(text=T.READY)
                self.status.configure(text="")
                self.note.configure(text="")
        elif kind == "progress":
            self.status.configure(text=str(payload))
        elif kind == "cancelled":
            self.status.configure(text=T.CANCELLED.format(task=payload))
        elif kind == "error":
            self.status.configure(text=T.ERROR_STATUS)
            ErrorDialog(self, payload)
        for v in self.views.values():
            v.on_event(kind, payload)

    def _tick(self) -> None:
        self.c.pump()
        self.after(100, self._tick)

    def _close(self) -> None:
        if self.c.state.busy and not messagebox.askyesno(T.CLOSE_TITLE, T.CLOSE_BUSY, parent=self):
            return
        self.c.cancel()
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    enable_high_dpi()
    try:
        setup_logging()
    except OSError:
        pass
    try:
        app = App()
    except tk.TclError as e:
        print(f"화면을 열 수 없습니다(디스플레이가 없는 환경일 수 있습니다): {e}", file=sys.stderr)
        return 1
    app.mainloop()
    return 0
