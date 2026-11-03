"""stage80 M2: 작업 중 창 닫기는 취소 후 작업이 끝나기를 짧게 기다렸다 닫는다(숨긴 창 tk 시험). 확인창은 가짜, 작업은 상태 값으로 흉내 낸다."""
import time

import pytest

pytest.importorskip("tkinter")
from pcmigrate import errorlog  # noqa: E402
from pcmigrate.gui import app as app_mod  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402

from test_gui_find_exts_tk import ctx  # noqa: E402,F401


def run_until(app, cond, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not cond():
        app.update()
        time.sleep(0.02)
    return cond()


def setup_busy(ctx, monkeypatch, label):  # noqa: F811
    c, app = ctx.c, ctx.app
    c.state.busy, c.state.busy_label, c.can_cancel = True, label, True
    cancelled, destroyed = [], []
    monkeypatch.setattr(c, "cancel", lambda: cancelled.append(1) or True)
    monkeypatch.setattr(app, "destroy", lambda: destroyed.append(1))
    return cancelled, destroyed


def test_close_waits_for_task_to_finish_then_closes(ctx, monkeypatch):  # noqa: F811
    cancelled, destroyed = setup_busy(ctx, monkeypatch, T.TASK_TRANSFER)
    ctx.answers["askyesno"] = True
    ctx.app._close()
    assert cancelled == [1] and destroyed == []  # 취소만 하고 바로 닫지 않는다(수정 전: 곧바로 destroy)
    for _ in range(5):
        ctx.app.update()
        time.sleep(0.05)
    assert destroyed == []  # 작업이 아직 끝나지 않았다
    ctx.c.state.busy = False  # 작업 스레드가 정리를 마치고 끝났다
    assert run_until(ctx.app, lambda: bool(destroyed)) and destroyed == [1]


def test_close_gives_up_after_timeout_and_logs(ctx, monkeypatch):  # noqa: F811
    cancelled, destroyed = setup_busy(ctx, monkeypatch, T.TASK_TRANSFER)
    monkeypatch.setattr(app_mod, "CLOSE_WAIT_WRITING", 0.3)  # 복사는 쓰기 작업
    ctx.answers["askyesno"] = True
    ctx.app._close()
    assert run_until(ctx.app, lambda: bool(destroyed)) and destroyed == [1]
    last = errorlog.last_error()
    assert last is not None and "창을 닫아 작업을 중단했습니다" in last[1] and T.TASK_TRANSFER in last[1]
    ctx.c.state.busy = False


def test_non_writing_task_uses_shorter_limit(ctx, monkeypatch):  # noqa: F811
    cancelled, destroyed = setup_busy(ctx, monkeypatch, T.TASK_FIND_DATA)
    monkeypatch.setattr(app_mod, "CLOSE_WAIT", 0.2)
    monkeypatch.setattr(app_mod, "CLOSE_WAIT_WRITING", 30.0)
    ctx.answers["askyesno"] = True
    ctx.app._close()
    assert run_until(ctx.app, lambda: bool(destroyed), 3.0)  # 찾기는 쓰기 작업이 아니라 짧은 제한을 쓴다
    ctx.c.state.busy = False


def test_declining_the_confirmation_does_nothing(ctx, monkeypatch):  # noqa: F811
    cancelled, destroyed = setup_busy(ctx, monkeypatch, T.TASK_TRANSFER)
    ctx.answers["askyesno"] = False
    ctx.app._close()
    ctx.app.update()
    assert not cancelled and not destroyed
    ctx.c.state.busy = False


def test_second_close_click_while_waiting_is_ignored(ctx, monkeypatch):  # noqa: F811
    cancelled, destroyed = setup_busy(ctx, monkeypatch, T.TASK_TRANSFER)
    ctx.answers["askyesno"] = True
    ctx.app._close()
    ctx.app._close()
    assert cancelled == [1] and len(ctx.asked) == 1  # 확인창도 취소도 한 번만
    ctx.c.state.busy = False
    run_until(ctx.app, lambda: bool(destroyed))
