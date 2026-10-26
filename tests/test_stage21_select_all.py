"""21: 구 PC 2단계 '보이는 항목 전체 선택/해제'. 민감 항목은 전체 선택에서 건너뛴다."""
import pytest

from pcmigrate.gui.controller import GuardError
from pcmigrate.gui import texts as T
from test_gui_controller import env, kinds  # noqa: F401


def keys_of(c):
    acme, creds = [r.key for r in c.candidate_rows()]
    return acme, creds


def test_select_all_skips_secret_even_with_ack(env):
    c, api, ev = env
    c.start_discover()
    acme, creds = keys_of(c)
    assert c.select_all_candidates() == (1, 1)
    assert c.state.selected == {acme} and "selection_changed" in kinds(ev)
    c.set_secrets_ack(True)  # 동의해도 전체 선택은 민감 항목을 넣지 않는다
    assert c.select_all_candidates() == (0, 1)
    assert c.state.selected == {acme}


def test_select_all_respects_visible_keys_and_ignores_unknown(env):
    c, api, ev = env
    c.start_discover()
    acme, creds = keys_of(c)
    assert c.select_all_candidates(keys=[creds, ("%X%", "nope")]) == (0, 1)
    assert c.state.selected == set()
    assert c.select_all_candidates(keys=[acme]) == (1, 0)
    assert c.state.selected == {acme}
    assert c.select_all_candidates(keys=[]) == (0, 0)


def test_clear_all_includes_secret_and_respects_scope(env):
    c, api, ev = env
    c.start_discover()
    acme, creds = keys_of(c)
    c.set_secrets_ack(True)
    c.set_selected(acme, True)
    c.set_selected(creds, True)
    assert c.clear_all_candidates(keys=[acme]) == 1 and c.state.selected == {creds}
    assert c.clear_all_candidates() == 1 and c.state.selected == set()
    assert c.clear_all_candidates() == 0


def test_no_discovery_is_noop(env):
    c, api, ev = env
    assert c.select_all_candidates() == (0, 0) and c.clear_all_candidates() == 0


def test_guards_still_work_after_select_all(env, tmp_path):
    c, api, ev = env
    c.start_discover()
    acme, creds = keys_of(c)
    c.select_all_candidates()
    with pytest.raises(GuardError):
        c.set_selected(creds, True)  # 동의 없이는 여전히 직접 선택 불가
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    assert api.calls[-1][3] == [("item", "%APPDATA%", "Acme")] and api.calls[-1][2] is False
    c.set_secrets_ack(True)
    c.set_selected(creds, True)
    c.set_secrets_ack(False)
    assert c.state.selected == {acme}


def test_new_discovery_and_reset_start_empty(env):
    c, api, ev = env
    c.start_discover()
    c.select_all_candidates()
    c.start_discover()
    assert c.state.selected == set()


def test_texts():
    assert "{n}" in T.DISC_SELECTED and "{m}" in T.DISC_SELECTED
    assert T.DISC_SELECTED.format(n=2, m=5) == "선택됨 2개 / 전체 5개"
    assert "보이는" in T.DISC_SELECT_ALL and "보이는" in T.DISC_BULK_SCOPE
    assert "직접 골라 주세요" in T.DISC_BULK_SKIPPED_SECRET.format(k=3) and "3개" in T.DISC_BULK_SKIPPED_SECRET.format(k=3)


# ------------------------------------------------------------------ tk
tk = pytest.importorskip("tkinter")
from test_gui_smoke import ctx  # noqa: E402,F401
from pcmigrate.gui import state as S  # noqa: E402


def test_view_buttons_select_clear_and_count(ctx):
    app, c = ctx.app, ctx.c
    v = app.views[S.OLD_DISCOVER]
    c.go(S.OLD_DISCOVER)
    c.start_discover()
    app.update()
    assert v.select_all_btn.cget("text") == T.DISC_SELECT_ALL and v.clear_all_btn.cget("text") == T.DISC_CLEAR_ALL
    assert int(v.select_all_btn.cget("underline")) >= 0 and int(v.clear_all_btn.cget("underline")) >= 0
    assert T.DISC_SELECTED.format(n=0, m=2) in v.count.cget("text")
    v.select_all_btn.invoke()
    assert len(c.state.selected) == 1 and c.state.secrets_ack is False
    assert T.DISC_SELECTED.format(n=1, m=2) in v.count.cget("text")
    assert T.DISC_BULK_SKIPPED_SECRET.format(k=1) in v.bulk_msg.cget("text")
    v.clear_all_btn.invoke()
    assert c.state.selected == set() and T.DISC_SELECTED.format(n=0, m=2) in v.count.cget("text")
    assert str(v.select_all_btn.cget("takefocus")) == "ttk::takefocus"  # 기본값: Tab 순서에 포함


def test_auto_run_still_selects_nothing(ctx):
    ctx.c.start_discover()
    assert ctx.c.state.selected == set()
