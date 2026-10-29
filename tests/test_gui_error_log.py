"""v0.1.1 수정 2(GUI): 모든 오류가 같은 로그에 기록되고, 오류 대화상자와 메인 창에서 로그를 열거나 복사할 수 있다."""
import pytest

tk = pytest.importorskip("tkinter")

from pathlib import Path  # noqa: E402

from pcmigrate import errorlog  # noqa: E402
from pcmigrate.gui import app as app_mod  # noqa: E402
from pcmigrate.gui import state as S  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402
from pcmigrate.gui.app import ErrorDialog  # noqa: E402
from pcmigrate.gui.controller import FriendlyError  # noqa: E402

from test_gui_smoke import ctx  # noqa: E402,F401


def dialogs(app):
    return [w for w in app.winfo_children() if isinstance(w, ErrorDialog)]


def buttons(dlg):
    out = {}
    stack = [dlg]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        if w.winfo_class() in ("TButton", "Button"):  # ttk 단추와 강조 단추(tk)
            out[w.cget("text")] = w
    return out


def trigger_runner_error(ctx):
    ctx.c.go(S.TRANSFER)
    s = ctx.c.state
    src = Path(s.rules_dir) / "src"
    src.mkdir(exist_ok=True)
    s.transfer_src, s.transfer_dst = str(src), str(Path(s.rules_dir) / "dst")
    ctx.api.transfer_error = RuntimeError("시뮬레이션 오류")
    ctx.c.start_transfer(dry_run=True)
    ctx.app.update()


def test_runner_exception_is_logged_and_dialog_has_buttons(ctx):
    trigger_runner_error(ctx)
    (dlg,) = dialogs(ctx.app)
    log_files = list(errorlog.log_dir().glob("errors-*.log"))
    assert len(log_files) == 1
    text = log_files[0].read_text(encoding="utf-8")
    assert "RuntimeError: 시뮬레이션 오류" in text and "Traceback" in text and "GUI transfer" in text
    assert dlg.err.log_path == str(log_files[0])
    btns = buttons(dlg)
    assert T.BTN_OPEN_LOG_FOLDER in btns and T.BTN_COPY_ERROR in btns and T.BTN_DETAIL_SHOW in btns and T.BTN_OK in btns
    assert T.ERROR_LOG_NOTE in dlg.log_note.cget("text") and "파일 경로" in dlg.log_note.cget("text")
    dlg.destroy()


def test_dialog_open_log_folder_and_copy(ctx, monkeypatch):
    trigger_runner_error(ctx)
    (dlg,) = dialogs(ctx.app)
    opened = []
    monkeypatch.setattr(app_mod, "open_location", lambda p: opened.append(p))
    buttons(dlg)[T.BTN_OPEN_LOG_FOLDER].invoke()
    assert opened == [str(errorlog.log_dir())]
    buttons(dlg)[T.BTN_COPY_ERROR].invoke()
    clip = ctx.app.clipboard_get()
    assert "RuntimeError: 시뮬레이션 오류" in clip and "Traceback" in clip and "pcmigrate" in clip
    assert dlg.log_note.cget("text") == T.ERROR_COPIED
    dlg.destroy()


def test_dialog_without_log_file_says_so_and_copy_still_works(ctx):
    fe = FriendlyError("제목", "무엇이", "어떻게", "TB-DETAIL")  # 로그를 남기지 못한 오류
    ctx.app.on_event("error", fe)
    (dlg,) = dialogs(ctx.app)
    assert dlg.log_note.cget("text") == T.ERROR_LOG_FAILED_NOTE
    buttons(dlg)[T.BTN_COPY_ERROR].invoke()
    assert "TB-DETAIL" in ctx.app.clipboard_get()
    dlg.destroy()


def test_main_window_always_has_error_log_button(ctx, monkeypatch):
    opened = []
    monkeypatch.setattr(app_mod, "open_location", lambda p: opened.append(p))
    for screen in (S.START, S.OLD_SCAN, S.TRANSFER):
        ctx.c.go(screen)
        ctx.app.update()
        assert ctx.app.log_link.winfo_manager() == "pack" and ctx.app.log_link.cget("text") == T.BTN_OPEN_ERROR_LOG
    ctx.app.log_link.invoke()
    assert opened == [str(errorlog.log_dir())]


def test_uncaught_callback_exception_is_logged_and_shown(ctx):
    try:
        raise ValueError("콜백 안의 오류")
    except ValueError as e:
        ctx.app.report_callback_exception(type(e), e, e.__traceback__)
    assert dialogs(ctx.app)
    text = "".join(p.read_text(encoding="utf-8") for p in errorlog.log_dir().glob("errors-*.log"))
    assert "ValueError: 콜백 안의 오류" in text and T.TASK_UI in text
    dialogs(ctx.app)[0].destroy()


def test_view_level_errors_go_through_log_too(ctx, monkeypatch, tmp_path):
    """결과 파일 저장 실패처럼 화면에서 직접 잡는 오류도 같은 대화상자/로그를 쓴다."""
    ctx.c.state.transfer_report = None
    v = ctx.app.views[S.TRANSFER]
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda **k: str(tmp_path / "r.json"))
    v._save_report()  # 저장할 결과가 없음 -> GuardError -> 오류 대화상자
    ctx.app.update()
    assert dialogs(ctx.app)
    dialogs(ctx.app)[0].destroy()
    assert list(errorlog.log_dir().glob("errors-*.log"))
