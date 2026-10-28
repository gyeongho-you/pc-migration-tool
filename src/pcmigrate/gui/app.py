"""Tkinter 마법사 창. 실행: python -m pcmigrate.gui  또는  pcmigrate gui"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import tempfile
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from .. import errorlog
from . import icons
from . import state as S
from . import texts as T
from .controller import Controller, FriendlyError, anonymize_paths
from .views import common as C
from .views.common import (CHIP_DONE_BG, CHIP_TODO_BG, FONT_DELTA_RANGE, INFO_BG, INFO_FG, MUTED_FG, OK_FG, SUB_FONT, TITLE_FONT, WARN_FG,
                           ActionButton, BaseView, TextDialog, add_tooltip, autowrap, install_wheel, open_location, setup_fonts)
from .views.auto import AutoView
from .views.find import FindView
from .views.install import InstallView
from .views.lan_recv import ReceiveView
from .views.lan_send import SendView
from .views.method import MethodView
from .views.new_pc import ConfirmView, PackageView, PreviewView, ResultView
from .views.old_pc import ChecklistView, DiscoverView, ExportView, ScanView
from .views.start import StartView
from .views.transfer import TransferView

LOG_NAME = "pcmigrate-gui.log"
BASE_SIZE = (1040, 760)  # 96dpi(100%) 기준 시작 크기
MIN_SIZE = (900, 640)  # 96dpi 기준 최소 크기: 이 크기에서도 입력칸과 주 버튼이 첫 화면에 보여야 한다
SCREEN_MARGIN = (40, 90)  # 화면(작업 표시줄 포함)에서 남겨 둘 여백


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


def window_sizes(scale: float, screen: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    """(시작 크기, 최소 크기). 배율(scale = 화면 dpi / 96)만큼 키우되 화면 크기를 넘지 않게 자른다."""
    sw, sh = screen
    max_w, max_h = max(640, sw - SCREEN_MARGIN[0]), max(480, sh - SCREEN_MARGIN[1])
    start = (min(round(BASE_SIZE[0] * scale), max_w), min(round(BASE_SIZE[1] * scale), max_h))
    mini = (min(round(MIN_SIZE[0] * scale), max_w, start[0]), min(round(MIN_SIZE[1] * scale), max_h, start[1]))
    return start, mini


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
    """전체 이전 순서: 제목, 진행률 n/N, 번호와 체크박스, '도구가 도와줌 / 직접 하셔야 함' 표시, 하단 '닫기'. 내용은 texts.py 의 ROADMAP_* 에서 온다."""

    def __init__(self, parent: tk.Misc, controller: Controller) -> None:
        super().__init__(parent)
        self.c = controller
        self.title(T.ROADMAP_TITLE)
        self.transient(parent.winfo_toplevel())
        w, h = min(760, max(520, parent.winfo_screenwidth() - 120)), min(660, max(420, parent.winfo_screenheight() - 140))
        self.geometry(f"{w}x{h}")
        top = ttk.Frame(self, padding=(14, 10, 14, 0))
        top.pack(fill="x")
        self.heading = ttk.Label(top, text=T.ROADMAP_HEADING, font=TITLE_FONT)
        self.heading.pack(side="left")
        self.progress_label = ttk.Label(top, text="", font=SUB_FONT, foreground=INFO_FG)
        self.progress_label.pack(side="right")
        bottom = ttk.Frame(self, padding=(14, 8))
        bottom.pack(side="bottom", fill="x")
        self.close_btn = ActionButton(bottom, text=T.BTN_CLOSE, command=self.destroy, kind="primary")
        self.close_btn.pack(side="right")
        body_holder = C.ScrollBody(self)
        body_holder.pack(fill="both", expand=True)
        body = ttk.Frame(body_holder.inner, padding=(14, 6))
        body.pack(fill="both", expand=True)
        intro = ttk.Label(body, text=T.ROADMAP_INTRO, justify="left")
        intro.pack(anchor="w", fill="x")
        autowrap(intro, 30)
        self.vars: dict[str, tk.BooleanVar] = {}
        self.who_labels: dict[str, ttk.Label] = {}
        for heading, items in ((T.ROADMAP_OLD_HEADING, T.ROADMAP_OLD), (T.ROADMAP_NEW_HEADING, T.ROADMAP_NEW)):
            ttk.Label(body, text=heading, font=SUB_FONT).pack(anchor="w", pady=(14, 4))
            for i, (key, title, who, detail) in enumerate(items, 1):
                row = ttk.Frame(body)
                row.pack(fill="x", pady=3)
                var = tk.BooleanVar(value=key in self.c.roadmap_marks)
                self.vars[key] = var
                ttk.Checkbutton(row, text=f"{i}) {title}", variable=var, command=lambda k=key: self._toggle(k)).pack(anchor="w")
                who_label = ttk.Label(row, text=who, foreground=OK_FG if who == T.WHO_TOOL else WARN_FG if who == T.WHO_HUMAN else INFO_FG)
                who_label.pack(anchor="w", padx=(24, 0))
                self.who_labels[key] = who_label
                d = ttk.Label(row, text=detail, justify="left", foreground=MUTED_FG)
                d.pack(anchor="w", fill="x", padx=(24, 0))
                autowrap(d, 40)
        self.update_progress()
        self.bind("<Escape>", lambda e: (self.destroy(), "break")[1])
        self.bind("<Return>", lambda e: (self.destroy(), "break")[1])

    def _toggle(self, key: str) -> None:
        self.c.toggle_roadmap(key)
        self.update_progress()

    def update_progress(self) -> None:
        total = len(self.vars)
        done = sum(1 for k in self.vars if k in self.c.roadmap_marks)
        self.progress_label.configure(text=T.ROADMAP_PROGRESS.format(n=done, total=total))


class ErrorDialog(tk.Toplevel):
    """오류 대화상자: 한국어 한 문장 + '이렇게 해 보세요' + 접힌 '상세 보기'(영문 예외 이름과 traceback) + '문제 보고서 저장'."""

    def __init__(self, parent: tk.Misc, err: FriendlyError) -> None:
        super().__init__(parent)
        self.title(err.title)
        self.transient(parent)
        self.resizable(True, True)
        body = ttk.Frame(self, padding=14)
        body.pack(fill="x")
        self.what_label = ttk.Label(body, text=err.what, wraplength=560, justify="left", font=SUB_FONT)
        self.what_label.pack(anchor="w", fill="x")
        self.how_label = None
        if err.how:
            ttk.Label(body, text=T.ERR_HOW, foreground=INFO_FG, font=C.BOLD_FONT).pack(anchor="w", pady=(10, 0))
            self.how_label = ttk.Label(body, text=err.how, wraplength=560, justify="left")
            self.how_label.pack(anchor="w", fill="x")
        self.log_note = ttk.Label(body, text=T.ERROR_LOG_NOTE if err.log_path else T.ERROR_LOG_FAILED_NOTE,
                                  wraplength=560, justify="left", foreground=MUTED_FG)
        self.log_note.pack(anchor="w", fill="x", pady=(10, 0))
        self.detail = tk.Text(self, height=10, width=80, wrap="none")
        self.detail.insert("1.0", err.detail or T.NO_DETAIL)
        self.detail.configure(state="disabled")
        self.shown = False
        bar = ttk.Frame(self, padding=(14, 0, 14, 14))
        bar.pack(fill="x", side="bottom")
        self.err = err
        self.toggle = ttk.Button(bar, text=T.BTN_DETAIL_SHOW, command=self._toggle)
        self.toggle.pack(side="left")
        self.save_btn = ttk.Button(bar, text=T.BTN_SAVE_REPORT, command=self.save_report)
        self.save_btn.pack(side="left", padx=(8, 0))
        self.log_btn = ttk.Button(bar, text=T.BTN_OPEN_LOG_FOLDER, command=self.open_log_folder)
        self.log_btn.pack(side="left", padx=(8, 0))
        self.copy_btn = ttk.Button(bar, text=T.BTN_COPY_ERROR, command=self.copy_error)
        self.copy_btn.pack(side="left", padx=(8, 0))
        self.ok_btn = ActionButton(bar, text=T.BTN_OK, command=self.destroy, kind="primary")
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

    def report_text(self) -> str:
        """저장/복사할 문제 보고서(개인 폴더 경로는 %USERPROFILE% 등으로 바꾼다)."""
        text = self.err.report or "\n".join(x for x in (self.err.what, self.err.how, self.err.detail) if x)
        return anonymize_paths(text)

    def copy_error(self) -> None:
        """오류 내용(시각, 버전, 실행 화면, 예외, 전체 traceback)을 클립보드에 넣는다."""
        self.clipboard_clear()
        self.clipboard_append(self.report_text())
        self.log_note.configure(text=T.ERROR_COPIED)

    def save_report(self, path: str | None = None) -> str:
        """문제 보고서를 파일로 저장한다(저장 위치는 사용자가 고른다). 저장한 경로를 돌려준다(취소하면 빈 문자열)."""
        if path is None:
            parent = Path(os.path.expanduser("~")) / "Desktop"
            path = filedialog.asksaveasfilename(parent=self, title=T.REPORT_SAVE_TITLE, defaultextension=".txt",
                                                filetypes=[("텍스트", "*.txt")], initialfile=T.REPORT_DEFAULT_NAME,
                                                initialdir=str(parent) if parent.is_dir() else None)
        if not path:
            return ""
        try:
            Path(path).write_text(self.report_text(), encoding="utf-8")
        except OSError as e:
            self.log_note.configure(text=f"{T.ICON_FAIL} {e.strerror or e}")
            return ""
        self.log_note.configure(text=T.ERROR_REPORT_SAVED)
        return path

    def _toggle(self) -> None:
        self.shown = not self.shown
        if self.shown:
            self.detail.pack(fill="both", expand=True, padx=14, pady=(0, 8))
            self.toggle.configure(text=T.BTN_DETAIL_HIDE)
        else:
            self.detail.pack_forget()
            self.toggle.configure(text=T.BTN_DETAIL_SHOW)


VIEW_CLASSES = (StartView, ScanView, DiscoverView, ChecklistView, ExportView, PackageView, InstallView, PreviewView, ConfirmView,
                ResultView, TransferView, AutoView, FindView, MethodView, ReceiveView, SendView)  # stage30a: 뒤의 네 화면


class App(tk.Tk):
    def __init__(self, controller: Controller | None = None, detect_icons: bool = False) -> None:
        super().__init__()
        self.c = controller or Controller()
        self.title(T.APP_TITLE)
        self.ui_scale = self._measure_scale()
        start, mini = window_sizes(self.ui_scale, (self.winfo_screenwidth(), self.winfo_screenheight()))
        self.min_size = mini
        self.geometry(f"{start[0]}x{start[1]}")
        self.minsize(*mini)
        self.current = S.START
        self.font_delta = 0
        self.help_dialog: TextDialog | None = None
        self._setup_style()
        if detect_icons:
            self.detect_icons()  # 화면 문구를 만들기 전에(글꼴이 정해진 뒤) 그림 글자를 그릴 수 있는지 본다
        install_wheel(self)
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
        for name, v in self.views.items():  # 보이는 화면 하나만 배치 대상으로 둔다(숨은 화면까지 매번 다시 배치하지 않게)
            if name != S.START:
                v.grid_remove()
        self.c.listener = self.on_event
        self.bind_all("<Return>", self._on_return)
        self.bind_all("<Escape>", self._on_escape)
        self.bind_all("<F1>", lambda e: (self.show_help(self.current), "break")[1])
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.show(S.START)
        self.after(100, self._tick)
        self.after(1200, self._offer_net_restore)  # stage39: 이전에 바꾼 이더넷 설정이 남아 있으면 되돌리기를 제안한다

    def _offer_net_restore(self) -> None:
        try:
            text = self.c.net_pending_text()
            if text and messagebox.askyesno(T.NET_RESTORE_ASK_TITLE, text + chr(10) * 2 + T.NET_RESTORE_ASK, parent=self):
                self.c.net_restore()
        except Exception:  # noqa: BLE001 - 안내 실패가 프로그램을 막지 않게(화면의 '지금 되돌리기'로도 할 수 있다)
            pass

    # ------------------------------------------------------------------ 구성
    def _measure_scale(self) -> float:
        """화면 배율(dpi/96). 알 수 없으면 1."""
        try:
            return max(1.0, min(3.0, float(self.winfo_fpixels("1i")) / 96.0))
        except (tk.TclError, ValueError):
            return 1.0

    def detect_icons(self) -> bool:
        """✔ ✖ ⚠ 를 현재 글꼴이 그릴 수 없으면 글자 표시([OK] [X] [!])로 바꾼다. 환경변수 PCMIGRATE_TEXT_ICONS 가 있으면 그것이 우선.
        글자 표시가 필요하면 texts 를 다시 불러 문구를 새로 만든다(화면은 이 뒤에 만든다). 글자 표시를 쓰는지 돌려준다."""
        try:
            want = icons.want_text_icons(tkfont.nametofont("TkDefaultFont"))
        except tk.TclError:
            return T.TEXT_ICONS
        if want != T.TEXT_ICONS:
            importlib.reload(T)
        return T.TEXT_ICONS

    def _setup_style(self) -> None:
        """글꼴(맑은 고딕, 본문 11pt)을 통일하고, 표 줄 높이/버튼 여백을 글꼴에 맞춘다."""
        try:
            families = set(tkfont.families())
            fam = next((f for f in T.FONT_FAMILIES if f in families), None)
            setup_fonts(self, T.FONT_SIZE + self.font_delta, fam)
            self._apply_font_metrics()
        except tk.TclError:
            pass

    def _apply_font_metrics(self) -> None:
        style = ttk.Style(self)
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style.configure("Treeview", rowheight=line + 8)
        style.configure("Treeview.Heading", font=C.BOLD_FONT)
        style.configure("TButton", padding=(10, 4))
        style.configure("Mini.TButton", padding=(4, 1))
        style.configure("TCheckbutton", padding=(2, 2))
        style.configure("TRadiobutton", padding=(2, 2))

    def change_font(self, delta: int) -> int:
        """글자 크기 키우기(+1)/줄이기(-1). 허용 범위 안에서만 바뀌며 바뀐 단계(본문 pt 기준)를 돌려준다."""
        lo, hi = FONT_DELTA_RANGE
        self.font_delta = max(lo, min(hi, self.font_delta + delta))
        try:
            setup_fonts(self, T.FONT_SIZE + self.font_delta)
            self._apply_font_metrics()
        except tk.TclError:
            pass
        self.font_up.state(["disabled"] if self.font_delta >= hi else ["!disabled"])
        self.font_down.state(["disabled"] if self.font_delta <= lo else ["!disabled"])
        return T.FONT_SIZE + self.font_delta

    def _build_header(self) -> None:
        bar = ttk.Frame(self, padding=(12, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text=T.APP_TITLE, font=SUB_FONT).pack(side="left")
        ttk.Label(bar, text="›", foreground=INFO_FG, padding=(10, 0, 0, 0)).pack(side="left")
        self.crumb = ttk.Label(bar, text="", foreground=INFO_FG, padding=(4, 0))
        self.crumb.pack(side="left")
        info = self.c.system_info()
        self.env_label = ttk.Label(bar, text=f"{info.os_name} · {T.HEADER_ADMIN[info.is_admin]}", foreground=MUTED_FG)
        self.env_label.pack(side="right")
        self.font_up = ttk.Button(bar, text=T.BTN_FONT_UP, width=3, style="Mini.TButton", command=lambda: self.change_font(1))
        self.font_down = ttk.Button(bar, text=T.BTN_FONT_DOWN, width=3, style="Mini.TButton", command=lambda: self.change_font(-1))
        self.font_up.pack(side="right", padx=(4, 12))
        self.font_down.pack(side="right")
        add_tooltip(self.font_up, T.TIP_FONT_UP)
        add_tooltip(self.font_down, T.TIP_FONT_DOWN)
        self.header_sep = ttk.Separator(self)
        self.header_sep.pack(fill="x")
        self.overall = tk.Frame(self, bg=INFO_BG, padx=12, pady=2)  # 전체 단계 n/N + 단계 이름 (흐름 안에서만 보인다)
        self.overall_label = tk.Label(self.overall, text="", bg=INFO_BG, font=SUB_FONT)
        self.overall_label.pack(side="left")
        self.overall_chips: list[tk.Label] = []
        self.overall_flow = tk.Label(self.overall, text="", bg=INFO_BG, fg=MUTED_FG)
        self.overall_flow.pack(side="right")

    def _build_footer(self) -> None:
        ttk.Separator(self).pack(fill="x", side="bottom")
        bar = ttk.Frame(self, padding=(12, 2))
        bar.pack(fill="x", side="bottom")
        self.footer = bar
        right = ttk.Frame(bar)
        right.pack(side="right", padx=(10, 0))
        self.progress = ttk.Progressbar(right, mode="indeterminate", length=170)  # 작업 중에만 보인다(유휴일 때는 숨김)
        self.progress_shown = False
        self.log_link = ActionButton(right, text=T.BTN_OPEN_ERROR_LOG, kind="link",
                                     command=lambda: open_location(str(errorlog.log_dir())))
        self.log_link.pack(side="bottom")  # 오류가 났을 때 로그를 바로 열 수 있게 항상 보인다(작은 링크)
        left = ttk.Frame(bar)
        left.pack(side="left", fill="x", expand=True)
        self.stage = ttk.Label(left, text=T.READY, font=SUB_FONT)  # 지금 하는 일
        self.stage.pack(anchor="w")
        self.status = ttk.Label(left, text="", justify="left")  # 진행 메시지(현황 카드가 없는 화면에서만 보인다)
        autowrap(self.status, 20)
        self.note = ttk.Label(left, text="", foreground=MUTED_FG)
        self._left = left
        self._build_stop_widgets(right, left)  # stage30b: 중지/강제 중지/중지 뒤 안내

    def _sync_footer(self) -> None:
        """하단 상태줄: 유휴일 때는 '준비됨' 한 줄만. 작업 중이어도 현황 카드가 보이는 화면에서는 같은 말이 겹치지 않게 막대/설명을 숨긴다."""
        busy = bool(self.c.state.busy)
        card = getattr(self.views.get(self.current), "card", None) if hasattr(self, "views") else None
        card_visible = bool(card is not None and card.shown)
        want_bar = busy and not card_visible
        if want_bar and not self.progress_shown:
            self.progress.pack(side="top", before=self.log_link)
            self.progress_shown = True
        elif not want_bar and self.progress_shown:
            self.progress.pack_forget()
            self.progress_shown = False
        want_text = busy and not card_visible
        for w in (self.status, self.note):
            if want_text and not w.winfo_manager():
                w.pack(anchor="w", fill="x")
            elif not want_text and w.winfo_manager():
                w.pack_forget()
        self._sync_stop()

    # ------------------------------------------------------------------ 화면/도움말/키
    def show(self, screen: str) -> None:
        """화면 전환. 모든 화면을 '아래에 가려진 채' 먼저 다 채우고(내용, 상단 단계 표시, 현황 카드, 하단 막대) 마지막에 한 번에 올린다.
        화면이 부분부분 바뀌는 깜빡임(랙처럼 보임)을 막기 위해 보이는 일은 tkraise 하나뿐이다."""
        view = self.views[screen]
        previous = self.views.get(self.current) if self.current != screen else None
        self.current = screen
        self._after_note_text = ""  # 다른 화면으로 가면 지난 중지 안내는 지운다
        view.grid()  # grid_remove 로 숨겨 둔 화면을 다시 배치 대상으로(원래 옵션을 기억한다)
        self.crumb.configure(text=T.CRUMB_HOME if screen == S.START else T.TITLES.get(screen, screen))
        self.update_overall()
        view.on_show()
        view.refresh_card()
        self._sync_footer()
        view.update_idletasks()  # 가려진 채로 배치까지 끝낸다
        view.tkraise()
        if previous is not None and previous is not view:
            previous.grid_remove()

    def update_overall(self) -> None:
        """맨 위의 '전체 단계 n/N' 과 단계 이름 표시(흐름 안의 화면에서만). 단계 칩은 만들어 둔 것을 재사용하고 바뀐 것만 고친다."""
        mode, stage = self.c.flow()
        files = self.c.files_stage(self.current)  # stage30a: 내 파일 옮기기 4단계 흐름이면 1~4, 아니면 0
        step = S.overall_step(self.current, mode, stage, files)
        if step is None:
            if self.overall.winfo_manager():
                self.overall.pack_forget()
            return
        n, total, flow, title = step
        names = S.overall_names(self.current, mode, files)
        if not self.overall.winfo_manager():
            self.overall.pack(fill="x", after=self.header_sep)
        self._set_text(self.overall_label, T.OVERALL_FMT.format(n=n, total=total))
        self._set_text(self.overall_flow, flow)
        while len(self.overall_chips) < total:
            chip = tk.Label(self.overall, text="", padx=8, pady=1)
            self.overall_chips.append(chip)
            chip._look = None  # type: ignore[attr-defined]
        for i, chip in enumerate(self.overall_chips):
            if i >= total:
                if chip.winfo_manager():
                    chip.pack_forget()
                continue
            state = "done" if i + 1 < n else "current" if i + 1 == n else "todo"
            name = names[i] if i < len(names) else ""
            look = ((T.ICON_OK + " " if state == "done" else "") + f"{i + 1} {name}".strip(), state)
            if getattr(chip, "_look", None) != look:  # 바뀐 칩만 다시 그린다
                chip._look = look  # type: ignore[attr-defined]
                chip.configure(text=look[0],
                               bg=INFO_FG if state == "current" else CHIP_DONE_BG if state == "done" else CHIP_TODO_BG,
                               fg="white" if state == "current" else OK_FG if state == "done" else MUTED_FG,
                               font=C.BOLD_FONT if state == "current" else C.BODY_FONT)
        # 칩 순서를 지키려고, 보이는 칩이 달라졌을 때만 다시 쌓는다
        want = [ch for i, ch in enumerate(self.overall_chips) if i < total]
        if [ch for ch in self.overall_chips if ch.winfo_manager()] != want:
            for ch in self.overall_chips:
                if ch.winfo_manager():
                    ch.pack_forget()
            for ch in want:
                ch.pack(side="left", padx=(8, 0), before=self.overall_flow)

    @staticmethod
    def _set_text(label: tk.Label, text: str) -> None:
        if str(label.cget("text")) != text:
            label.configure(text=text)

    def show_error(self, err: FriendlyError) -> ErrorDialog:
        """오류 대화상자(문제 보고서 저장/로그 폴더 열기/오류 내용 복사 버튼 포함)."""
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
        if isinstance(w, (ttk.Button, ActionButton)):
            w.invoke()
            return "break"
        btn = self.views[self.current].primary
        if btn is not None and "disabled" not in btn.state():
            btn.invoke()
            return "break"
        return None

    def _on_escape(self, event):
        """Esc: 대화상자 닫기(각 대화상자가 처리)와 진행 중인 작업 취소에만 쓴다. 화면을 '이전'으로 되돌리지 않는다."""
        w = self.focus_get()
        if w is not None and w.winfo_toplevel() is not self:
            return None
        if self.c.state.busy:
            if self.c.can_cancel:
                self._stop_clicked()
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
                self._after_note_text = ""  # 새 작업을 시작하면 지난 중지 안내는 지운다
            else:
                self.progress.stop()
                self.progress.configure(mode="indeterminate", value=0)
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
            self._after_note_text = self.c.stop_done_note(payload)
        elif kind == "force_stopped":
            self._after_note_text = T.FORCE_STOPPED
        elif kind == "error":
            self.show_error(payload)
        elif kind in ("transfer_flow", "transfer_done", "files_changed", "lan_rx", "lan_send_done"):  # stage30a: 단계 표시줄 새로 고침
            self.update_overall()
        for v in self.views.values():
            v.on_event(kind, payload)
        if kind in ("status", "busy", "screen"):
            self.views[self.current].refresh_card()
        self._sync_footer()

    def _tick(self) -> None:
        try:
            self.c.pump()
            if self.c.status.running:  # 경과 시간/속도가 계속 바뀌도록 작업 중에는 0.1초마다 현황 카드를 다시 그린다
                self.views[self.current].refresh_card()
                self._sync_footer()
            self._sync_stop()  # 중지 요청 뒤 15초가 지났는지 주기적으로 본다(stage30b)
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

    # ====================================================================== stage30b 구획: 작업 중지
    def _build_stop_widgets(self, right: ttk.Frame, left: ttk.Frame) -> None:
        """하단 막대의 빨간 '중지' 단추, '지금 강제로 멈추기' 상자, 중지가 끝난 뒤 안내."""
        self.stop_btn = ActionButton(right, text=T.BTN_STOP, command=self._stop_clicked, kind="danger", padx=12, pady=3)
        add_tooltip(self.stop_btn, T.TIP_STOP)
        self.force_box = ttk.Frame(left)
        self.force_label = ttk.Label(self.force_box, text=T.STOP_STUCK, justify="left", foreground=WARN_FG)
        self.force_btn = ActionButton(self.force_box, text=T.BTN_FORCE_STOP, command=self._force_clicked, kind="danger", padx=12, pady=3)
        self.force_btn.pack(side="right", padx=(10, 0))
        self.force_label.pack(side="left", fill="x", expand=True)
        autowrap(self.force_label, 240)
        self.after_note = ttk.Label(left, text="", justify="left", foreground=INFO_FG)
        autowrap(self.after_note, 20)
        self._after_note_text = ""

    def _stop_clicked(self) -> None:
        """'중지': 바로 '중지하는 중…'으로 바뀌고 다시 누를 수 없다. '추천대로 한 번에' 진행 중이면 그 흐름도 함께 멈춘다."""
        a = self.c.auto
        if a is not None and a.active:
            self.c.auto_stop()
        else:
            self.c.cancel()
        self._sync_stop()

    def _force_clicked(self) -> None:
        self.c.force_stop()
        self._sync_stop()

    def _sync_stop(self) -> None:
        """컨트롤러 상태에서 중지 관련 위젯을 맞춘다: 평상시 숨김 → 작업 중 '중지' → 누르면 '중지하는 중…'(비활성) → 15초 뒤 강제 중지 상자."""
        if not hasattr(self, "stop_btn"):
            return
        c = self.c
        if c.state.busy and c.can_cancel:
            requested = c.cancel_requested_at is not None
            if not self.stop_btn.winfo_manager():
                self.stop_btn.pack(side="top", pady=(4, 0), before=self.log_link)
            text = T.STOPPING if requested else T.BTN_STOP
            if str(self.stop_btn.cget("text")) != text:
                self.stop_btn.configure(text=text)
            self.stop_btn.state(["disabled"] if requested else ["!disabled"])
            want_force = c.force_stop_ready()
        else:
            if self.stop_btn.winfo_manager():
                self.stop_btn.pack_forget()
            want_force = False
        if want_force and not self.force_box.winfo_manager():
            self.force_box.pack(anchor="w", fill="x", after=self.stage)
        elif not want_force and self.force_box.winfo_manager():
            self.force_box.pack_forget()
        text = self._after_note_text if not c.state.busy else ""
        if text:
            if str(self.after_note.cget("text")) != text:
                self.after_note.configure(text=text)
            if not self.after_note.winfo_manager():
                self.after_note.pack(anchor="w", fill="x")
        elif self.after_note.winfo_manager():
            self.after_note.pack_forget()


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
        app = App(detect_icons=True)
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
