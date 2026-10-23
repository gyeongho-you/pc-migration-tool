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
from .controller import Controller, FriendlyError
from .views.common import BaseView
from .views.new_pc import ConfirmView, PackageView, PreviewView, ResultView
from .views.old_pc import ChecklistView, DiscoverView, ExportView, ScanView
from .views.start import StartView
from .views.transfer import TransferView

LOG_NAME = "pcmigrate-gui.log"


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
    """오류 대화상자: 한국어 메시지 + 접힌 '상세 보기'(traceback)."""

    def __init__(self, parent: tk.Misc, err: FriendlyError) -> None:
        super().__init__(parent)
        self.title(err.title)
        self.transient(parent)
        self.resizable(True, True)
        ttk.Label(self, text=err.message, wraplength=520, justify="left", padding=12).pack(fill="x")
        self.detail = tk.Text(self, height=10, width=80, wrap="none")
        self.detail.insert("1.0", err.detail or "(상세 정보 없음)")
        self.detail.configure(state="disabled")
        self.shown = False
        bar = ttk.Frame(self, padding=(12, 0, 12, 12))
        bar.pack(fill="x")
        self.toggle = ttk.Button(bar, text="상세 보기 ▸", command=self._toggle)
        self.toggle.pack(side="left")
        ttk.Button(bar, text="확인", command=self.destroy).pack(side="right")
        self.grab_set()

    def _toggle(self) -> None:
        self.shown = not self.shown
        if self.shown:
            self.detail.pack(fill="both", expand=True, padx=12, pady=(0, 8))
            self.toggle.configure(text="상세 보기 ▾")
        else:
            self.detail.pack_forget()
            self.toggle.configure(text="상세 보기 ▸")


VIEW_CLASSES = (StartView, ScanView, DiscoverView, ChecklistView, ExportView, PackageView, PreviewView, ConfirmView,
                ResultView, TransferView)


class App(tk.Tk):
    def __init__(self, controller: Controller | None = None) -> None:
        super().__init__()
        self.c = controller or Controller()
        self.title("PC 이전 도우미")
        self.geometry("1000x720")
        self.minsize(820, 600)
        self._setup_fonts()
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
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.show(S.START)
        self.after(100, self._tick)

    # ------------------------------------------------------------------ 구성
    def _setup_fonts(self) -> None:
        try:
            families = set(tkfont.families())
            fam = next((f for f in ("Malgun Gothic", "맑은 고딕", "Segoe UI") if f in families), None)
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                f = tkfont.nametofont(name)
                f.configure(size=10, **({"family": fam} if fam else {}))
        except tk.TclError:
            pass

    def _build_header(self) -> None:
        bar = ttk.Frame(self, padding=(10, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="PC 이전 도우미", font=("TkDefaultFont", 12, "bold")).pack(side="left")
        info = self.c.system_info()
        admin = {True: "관리자 권한: 예", False: "관리자 권한: 아니오", None: "관리자 권한: 알 수 없음"}[info.is_admin]
        ttk.Label(bar, text=f"{info.os_name} · {admin}", foreground="#444444").pack(side="right")
        ttk.Separator(self).pack(fill="x")
        self.crumb = ttk.Label(self, text="", padding=(10, 4), foreground="#0b5394")
        self.crumb.pack(fill="x")

    def _build_footer(self) -> None:
        ttk.Separator(self).pack(fill="x", side="bottom")
        bar = ttk.Frame(self, padding=(10, 4))
        bar.pack(fill="x", side="bottom")
        self.status = ttk.Label(bar, text="준비됨", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.cancel_btn = ttk.Button(bar, text="취소", command=self.c.cancel)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.pack(side="right", padx=6)

    # ------------------------------------------------------------------ 화면/이벤트
    def show(self, screen: str) -> None:
        view = self.views[screen]
        view.tkraise()
        self.crumb.configure(text=("처음 화면" if screen == S.START else f"{S.TITLES.get(screen, screen)}"))
        view.on_show()

    def go_home(self) -> None:
        if self.c.state.busy:
            messagebox.showinfo("작업 중", "작업이 끝난 뒤에 처음으로 돌아갈 수 있습니다.", parent=self)
            return
        self.c.go(S.START)

    def on_event(self, kind: str, payload) -> None:
        if kind == "screen":
            self.show(payload)
            return
        if kind == "busy":
            if payload:
                self.status.configure(text=f"{payload} 중... (UI 는 멈추지 않습니다)")
                self.progress.start(12)
                if self.c.can_cancel:
                    self.cancel_btn.pack(side="right", padx=4)
            else:
                self.progress.stop()
                self.cancel_btn.pack_forget()
                self.status.configure(text="준비됨")
        elif kind == "progress":
            self.status.configure(text=str(payload))
        elif kind == "cancelled":
            self.status.configure(text=f"{payload}을(를) 취소했습니다. 진행 기록은 남아 있어 나중에 이어받을 수 있습니다.")
        elif kind == "error":
            self.status.configure(text="오류가 발생했습니다.")
            ErrorDialog(self, payload)
        for v in self.views.values():
            v.on_event(kind, payload)

    def _tick(self) -> None:
        self.c.pump()
        self.after(100, self._tick)

    def _close(self) -> None:
        if self.c.state.busy and not messagebox.askyesno(
                "종료 확인", "작업이 진행 중입니다. 지금 종료하면 작업이 중단됩니다.\n(복사는 나중에 다시 실행해 이어받을 수 있습니다)\n\n종료할까요?",
                parent=self):
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
