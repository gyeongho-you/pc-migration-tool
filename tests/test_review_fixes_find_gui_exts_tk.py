"""stage69 M2: LAN 받기 중 창 닫기(숨긴 창 tk 시험). 확인창은 가짜, 실제 네트워크 없음."""
import pytest

pytest.importorskip("tkinter")
from pcmigrate.gui import texts as T  # noqa: E402

from test_gui_find_exts_tk import ctx  # noqa: E402,F401  (같은 fixture)


@pytest.mark.parametrize("state", ["waiting", "connected", "receiving"])
def test_m2_closing_while_receiving_asks_and_stops_receive(ctx, monkeypatch, state):  # noqa: F811
    c, app = ctx.c, ctx.app
    c.files.rx_state = state
    stopped, destroyed = [], []
    monkeypatch.setattr(c, "lan_receive_stop", lambda: stopped.append(1))
    monkeypatch.setattr(app, "destroy", lambda: destroyed.append(1))
    ctx.answers["askyesno"] = False
    app._close()
    assert ctx.asked[-1] == (T.CLOSE_TITLE, T.CLOSE_BUSY) and not stopped and not destroyed  # 아니요: 아무 일도 없다
    ctx.answers["askyesno"] = True
    app._close()
    assert stopped == [1] and destroyed == [1]  # 예: 받기를 멈추고 닫는다(수정 전: 확인도 없이 닫혀 받기가 끊김)


def test_m2_closing_when_idle_has_no_confirmation(ctx, monkeypatch):  # noqa: F811
    stopped, destroyed = [], []
    monkeypatch.setattr(ctx.c, "lan_receive_stop", lambda: stopped.append(1))
    monkeypatch.setattr(ctx.app, "destroy", lambda: destroyed.append(1))
    ctx.app._close()
    assert ctx.asked == [] and not stopped and destroyed == [1]  # 받는 중이 아니면 이전과 같다


def test_m2_stop_failure_does_not_block_closing(ctx, monkeypatch):  # noqa: F811
    ctx.c.files.rx_state = "waiting"
    destroyed = []
    monkeypatch.setattr(ctx.c, "lan_receive_stop", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(ctx.app, "destroy", lambda: destroyed.append(1))
    ctx.app._close()
    assert destroyed == [1]
