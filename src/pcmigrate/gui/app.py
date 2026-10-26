"""Tkinter 마법사 창. 실행: python -m pcmigrate.gui  또는  pcmigrate gui"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import tkinter as tk
import traceback
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from .. import errorlog
from . import state as S
from . import texts as T
from .controller import Controller, FriendlyError
from .views.common import (BIG_FONT, INFO_BG, MUTED_FG, OK_FG, STAGE_FONT, BaseView, TextDialog, autowrap, open_location)
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


class RoadmapDialog(tk.Toplevel):
    """전체 이전 순서: 번호, 체크박스, '도구가 도와줌 / 직접 하셔야 함' 표시. 내용은 texts.py 의 ROADMAP_* 에서 온다."""

    def __init__(self, parent: tk.Misc, controller: Controller) -> None:
        super().__init__(parent)
        self.c = controller
        self.title(T.ROADMAP_TITLE)
        self.transient(parent.winfo_toplevel())
        self.geometry("760x620")
        canvas = tk.Canvas(self, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, padding=12)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        intro = ttk.Label(body, text=T.ROADMAP_INTRO, justify="left")
        intro.pack(anchor="w", fill="x")
        autowrap(intro, 30)
        self.vars: dict[str, tk.BooleanVar] = {}
        self.who_labels: dict[str, ttk.Label] = {}
        for heading, items in ((T.ROADMAP_OLD_HEADING, T.ROADMAP_OLD), (T.ROADMAP_NEW_HEADING, T.ROADMAP_NEW)):
            ttk.Label(body, text=heading, font=STAGE_FONT).pack(anchor="w", pady=(14, 4))
            for i, (key, title, who, detail) in enumerate(items, 1):
                row = ttk.Frame(body)
                row.pack(fill="x", pady=3)
                var = tk.BooleanVar(value=key in self.c.roadmap_marks)
                self.vars[key] = var
                ttk.Checkbutton(row, text=f"{i}) {title}", variable=var, command=lambda k=key: self.c.toggle_roadmap(k)).pack(anchor="w")
                who_label = ttk.Label(row, text=who, foreground="#1b6e2f" if who == T.WHO_TOOL else "#8a4b00" if who == T.WHO_HUMAN else "#0b5394")
                who_label.pack(anchor="w", padx=(24, 0))
                self.who_labels[key] = who_label
                d = ttk.Label(row, text=detail, justify="left", foreground=MUTED_FG)
                d.pack(anchor="w", fill="x", padx=(24, 0))
                autowrap(d, 40)
        self.bind("<Escape>", lambda e: (self.destroy(), "break")[1])


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
        self.log_note = ttk.Label(body, text=T.ERROR_LOG_NOTE if err.log_path else T.ERROR_LOG_FAILED_NOTE,
                                  wraplength=540, justify="left", foreground=MUTED_FG)
        self.log_note.pack(anchor="w", fill="x", pady=(8, 0))
        self.detail = tk.Text(self, height=10, width=80, wrap="none")
        self.detail.insert("1.0", err.detail or T.NO_DETAIL)
        self.detail.configure(state="disabled")
        self.shown = False
        bar = ttk.Frame(self, padding=(12, 0, 12, 12))
        bar.pack(fill="x", side="bottom")
        self.err = err
        self.toggle = ttk.Button(bar, text=T.BTN_DETAIL_SHOW, command=self._toggle)
        self.toggle.pack(side="left")
        self.log_btn = ttk.Button(bar, text=T.BTN_OPEN_LOG_FOLDER, command=self.open_log_folder)
        self.log_btn.pack(side="left", padx=(8, 0))
        self.copy_btn = ttk.Button(bar, text=T.BTN_COPY_ERROR, command=self.copy_error)
        self.copy_btn.pack(side="left", padx=(8, 0))
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

    def open_log_folder(self) -> None:
        open_location(str(errorlog.log_dir()))

    def copy_error(self) -> None:
        """오류 내용(시각, 버전, 실행 화면, 예외, 전체 traceback)을 클립보드에 넣는다."""
        text = self.err.report or "\n".join(x for x in (self.err.what, self.err.how, self.err.detail) if x)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log_note.configure(text=T.ERROR_COPIED)

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
        self.overall = tk.Frame(self, bg=INFO_BG, padx=10, pady=4)  # 전체 단계 n/N (흐름 안에서만 보인다)
        self.overall_label = tk.Label(self.overall, text="", bg=INFO_BG, font=BIG_FONT)
        self.overall_label.pack(side="left")
        self.overall_chips: list[tk.Label] = []
        self.overall_flow = tk.Label(self.overall, text="", bg=INFO_BG, fg=MUTED_FG)
        self.overall_flow.pack(side="right")

    def _build_footer(self) -> None:
        ttk.Separator(self).pack(fill="x", side="bottom")
        bar = ttk.Frame(self, padding=(10, 6))
        bar.pack(fill="x", side="bottom")
        right = ttk.Frame(bar)
        right.pack(side="right", padx=(10, 0))
        self.progress = ttk.Progressbar(right, mode="indeterminate", length=170)
        self.progress.pack(side="top")
        self.log_link = ttk.Button(right, text=T.BTN_OPEN_ERROR_LOG, command=lambda: open_location(str(errorlog.log_dir())))
        self.log_link.pack(side="bottom", pady=(4, 0))  # 오류가 났을 때 로그를 바로 열 수 있게 항상 보인다
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
        self.update_overall()
        view.on_show()
        view.refresh_card()

    def update_overall(self) -> None:
        """맨 위의 '전체 단계 n/N' 큰 표시(흐름 안의 화면에서만)."""
        mode, stage = self.c.flow()
        step = S.overall_step(self.current, mode, stage)
        if step is None:
            self.overall.pack_forget()
            return
        n, total, flow, title = step
        self.overall.pack(fill="x", after=self.crumb)
        self.overall_label.configure(text=T.OVERALL_FMT.format(n=n, total=total))
        self.overall_flow.configure(text=f"{flow} · {title}")
        while len(self.overall_chips) < total:
            chip = tk.Label(self.overall, text="", padx=8, pady=1)
            chip.pack(side="left", padx=(8, 0), before=self.overall_flow)
            self.overall_chips.append(chip)
        for i, chip in enumerate(self.overall_chips):
            if i >= total:
                chip.pack_forget()
                continue
            chip.pack(side="left", padx=(8, 0), before=self.overall_flow)
            state = "done" if i + 1 < n else "current" if i + 1 == n else "todo"
            chip.configure(text=(T.ICON_OK + " " if state == "done" else "") + str(i + 1),
                           bg="#0b5394" if state == "current" else "#d6e4fa" if state == "done" else "#efefef",
                           fg="white" if state == "current" else OK_FG if state == "done" else MUTED_FG)

    def show_error(self, err: FriendlyError) -> ErrorDialog:
        """오류 대화상자(로그 폴더 열기/오류 내용 복사 버튼 포함)."""
        self.status.configure(text=T.ERROR_STATUS)
        return ErrorDialog(self, err)

    def report_callback_exception(self, exc, val, tb) -> None:
        """tkinter 콜백 안에서 처리되지 않은 오류도 로그에 남기고 같은 대화상자로 보여 준다(창 모드 exe 에서는 콘솔이 없다)."""
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            self.show_error(self.c.report_error(val, T.TASK_UI, text))
        except Exception:  # noqa: BLE001 - 오류 표시 중 오류가 또 나도 앱이 죽지 않게
            errorlog.record(val, "GUI", text)

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
        self.help_dialog.roadmap_btn = ttk.Button(self.help_dialog, text=T.BTN_ROADMAP, command=self.show_roadmap)  # ? 에서도 전체 순서를 열 수 있게
        self.help_dialog.roadmap_btn.pack(side="left", padx=12, pady=(0, 12))
        return self.help_dialog

    def show_roadmap(self) -> RoadmapDialog:
        """전체 이전 순서(체크 목록)를 연다. 이미 열려 있으면 닫고 새로 만든다(체크 상태는 컨트롤러에 있다)."""
        if getattr(self, "roadmap_dialog", None) is not None:
            try:
                self.roadmap_dialog.destroy()
            except tk.TclError:
                pass
        self.roadmap_dialog = RoadmapDialog(self, self.c)
        return self.roadmap_dialog

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
                self.progress.configure(mode="indeterminate", value=0)
                self.cancel_btn.pack_forget()
                self.stage.configure(text=T.READY)
                self.status.configure(text="")
                self.note.configure(text="")
        elif kind == "progress":
            self.status.configure(text=str(payload))
        elif kind == "progress_info":
            if payload.total_bytes:  # 바이트를 알 수 있으면 비율 막대로 바꿔 보여 준다
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=100, value=payload.fraction * 100)
        elif kind == "cancelled":
            self.status.configure(text=T.CANCELLED.format(task=payload))
        elif kind == "error":
            self.show_error(payload)
        elif kind == "transfer_flow":
            self.update_overall()
        for v in self.views.values():
            v.on_event(kind, payload)
        if kind in ("status", "busy", "screen"):
            self.views[self.current].refresh_card()

    def _tick(self) -> None:
        try:
            self.c.pump()
            if self.c.status.running:  # 경과 시간/속도가 계속 바뀌도록 작업 중에는 0.1초마다 현황 카드를 다시 그린다
                self.views[self.current].refresh_card()
        except Exception as e:  # noqa: BLE001 - 한 번의 오류로 폴링이 멈추면 진행/완료가 영영 화면에 오지 않는다
            self.report_callback_exception(type(e), e, e.__traceback__)
        finally:
            try:
                self.after(100, self._tick)
            except tk.TclError:  # 창이 이미 닫힘
                pass

    def _close(self) -> None:
        if self.c.state.busy:
            msg = T.CLOSE_BUSY if self.c.can_cancel else T.CLOSE_BUSY_NO_CANCEL
            if not messagebox.askyesno(T.CLOSE_TITLE, msg, icon="warning", parent=self):
                return
        self.c.cancel()
        self.destroy()


def _notify_fatal(text: str) -> None:
    """창 모드 exe 는 표준 오류가 없어 시작 실패가 아무 표시 없이 끝난다. Windows 메시지 상자로 알린다(실패해도 조용히 무시)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "PC 이전 도우미", 0x10)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 알림 실패가 종료 코드를 바꾸면 안 된다
        pass


def main(argv: list[str] | None = None) -> int:
    enable_high_dpi()
    try:
        setup_logging()
    except OSError:
        pass
    try:
        app = App()
    except tk.TclError as e:
        path = errorlog.record(e, "GUI 시작(화면)")
        msg = f"화면을 열 수 없습니다(디스플레이가 없는 환경일 수 있습니다): {e}"
        if sys.stderr is not None:  # 창 모드 exe 에서는 표준 오류가 없다
            print(msg, file=sys.stderr)
        else:
            _notify_fatal("\n".join([msg, f"오류 종류: {type(e).__name__}"] + ([f"기록: {path}"] if path else [])))
        return 1
    except Exception as e:  # noqa: BLE001 - 창을 만들기 전의 예상 밖 오류도 로그에 남긴다
        path = errorlog.record(e, "GUI 시작")
        where = f"자세한 내용은 {path} 에 기록했습니다." if path else "오류 로그도 남기지 못했습니다(로그 폴더에 쓸 수 없음)."
        if sys.stderr is not None:
            print(f"예상하지 못한 오류가 났습니다. {where}", file=sys.stderr)
        else:
            _notify_fatal("\n".join(["예상하지 못한 오류가 나서 시작하지 못했습니다.", f"오류 종류: {type(e).__name__}: {e}", where]))
        return 1
    app.mainloop()
    return 0
