"""16b: App._tick 이 콜백 예외 뒤에도 다시 예약되고 오류를 보여 준다(tkinter 없으면 건너뜀)."""
import pytest

pytest.importorskip("tkinter")

from test_gui_smoke import ctx  # noqa: E402,F401
from pcmigrate.gui import texts as T  # noqa: E402


def test_tick_reschedules_after_pump_exception(ctx, monkeypatch):
    app, c = ctx.app, ctx.c
    scheduled, shown = [], []
    monkeypatch.setattr(app, "after", lambda ms, fn=None, *a: scheduled.append(ms) or "id")
    monkeypatch.setattr(app, "show_error", lambda fe: shown.append(fe))
    monkeypatch.setattr(c, "pump", lambda: (_ for _ in ()).throw(RuntimeError("뷰 오류")))
    app._tick()
    assert scheduled == [100]  # 예외가 나도 다음 폴링이 예약된다
    assert shown and shown[0].what  # 오류 대화상자로 알림


def test_close_while_busy_non_cancellable_uses_stronger_warning(ctx, monkeypatch):
    app, c = ctx.app, ctx.c
    asked = []
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno", lambda title, msg, **k: asked.append(msg) or False)
    c.state.busy, c.can_cancel = True, False
    app._close()
    assert asked == [T.CLOSE_BUSY_NO_CANCEL] and app.winfo_exists()
    c.state.busy = False
