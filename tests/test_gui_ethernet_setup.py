"""stage39: 이더넷 주소 자동 맞추기의 컨트롤러(비시각)와 화면(tkinter, 창을 숨긴 채) 시험. 가짜 lan_api + 가짜 runner(net_fakes.FakePC)만 쓴다."""
import time
from types import SimpleNamespace

import pytest

from pcmigrate import lan_api
from pcmigrate import net_setup as ns
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import GuardError

from net_fakes import FakePC, adapter
from test_gui_my_files_controller import FakeLan, env  # noqa: F401  (env 는 시험 모듈의 fixture)


class NetLan(FakeLan):
    """FakeLan + 이더넷 주소 맞추기 함수(진짜 lan_api 함수를 그대로 쓰되 env 로 가짜 PC 가 들어온다)."""
    plan_ip_setup = staticmethod(lan_api.plan_ip_setup)
    apply_ip_setup = staticmethod(lan_api.apply_ip_setup)
    restore_ip_setup = staticmethod(lan_api.restore_ip_setup)
    pending_restore = staticmethod(lan_api.pending_restore)
    list_ip_adapters = staticmethod(lan_api.list_ip_adapters)


@pytest.fixture
def netpc(tmp_path):
    return FakePC(tmp_path)


def wire(c, tmp_path, netpc):
    c.lan_api = NetLan(tmp_path)
    c.net_env = netpc.env()
    return c


# =========================================================================== 컨트롤러
def test_controller_plan_apply_restore(env, netpc):
    c = wire(env.c, env.tmp, netpc)
    assert c.net_refresh_adapters() == ["Ethernet A"]
    assert c.net_status_text() == T.NET_NOT_APPLIED
    text = c.net_plan("receiver", "Ethernet A")
    assert "10.254.250.2" in text and "Ethernet A" in text
    with pytest.raises(GuardError):
        c.net_apply(False)  # 확인 없이는 적용하지 않는다
    assert netpc.netsh_count == 0
    c.net_apply(True)
    assert c.net_applied()["ip"] == "10.254.250.2" and "10.254.250.2" in c.net_status_text()
    assert "net_changed" in [k for k, _ in env.ev]
    c.net_restore()
    assert c.net_applied() is None and c.net_status_text() == T.NET_RESTORED and netpc.find(7)["dhcp"] == "Enabled"
    assert not netpc.state.exists()


def test_controller_apply_without_plan_and_plan_errors(env, netpc):
    c = wire(env.c, env.tmp, netpc)
    with pytest.raises(GuardError):
        c.net_apply(True)
    netpc.adapters[:] = [adapter("Wi-Fi", 3, media="Native 802.11")]
    with pytest.raises(GuardError) as e:
        c.net_plan("sender")
    assert "랜선" in str(e.value)
    assert c.net_refresh_adapters() == [] and c.net_status_text() == T.NET_NO_ADAPTER


def test_controller_sender_matches_peer_from_target(env, netpc):
    c = wire(env.c, env.tmp, netpc)
    c.lan_set_target("172.30.250.2:47830", "123456")
    assert "172.30.250.1" in c.net_plan("sender")
    c.lan_set_target("192.168.0.12", "123456")  # 자동 맞추기 대역이 아니면 상대 주소를 무시하고 기본 대역
    assert "10.254.250.1" in c.net_plan("sender")
    c.net_apply(True)
    assert "10.254.250.2" in c.net_status_text()  # 보내는 쪽에는 받는 쪽 주소 예시가 보인다
    assert netpc.find(7)["addresses"][0]["ip"] == "10.254.250.1"


def test_controller_apply_error_is_shown_not_raised(env, netpc):
    c = wire(env.c, env.tmp, netpc)
    netpc.admin, netpc.uac_denied = False, True
    c.net_plan("receiver")
    c.net_apply(True)
    assert c.net_applied() is None and "관리자" in c.net_status_text() and netpc.netsh_count == 0


def test_controller_pending_after_interruption_and_restore(env, netpc):
    c = wire(env.c, env.tmp, netpc)
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=netpc.env()), lambda t: True, netpc.env())  # 이전 실행이 중간에 꺼짐
    assert "10.254.250.1" in c.net_pending_text()
    c.net_restore()
    assert c.net_pending_text() == "" and not netpc.state.exists() and netpc.find(7)["dhcp"] == "Enabled"


def test_controller_pending_check_never_breaks_with_fake_lan(env):
    assert env.c.net_pending() is None  # 가짜 lan_api 에 함수가 없어도 화면이 막히지 않는다


def test_controller_auto_restore_only_when_applied(env, netpc):
    c = wire(env.c, env.tmp, netpc)
    assert c.net_restore_if_applied() is False
    c.net_plan("sender")
    c.net_apply(True)
    assert c.net_restore_if_applied() is True and c.net_applied() is None


def test_diag_code_recorded(env, netpc):
    c = wire(env.c, env.tmp, netpc)
    c.lan_api.run_diagnose = lambda target, code=None: SimpleNamespace(summary="서브넷이 다릅니다", code="subnet")
    c.lan_diagnose_start("10.0.0.5:47830", "")
    assert c.files.diag_code == "subnet"


# =========================================================================== 화면(tk)
tk = pytest.importorskip("tkinter")
from tkinter import messagebox  # noqa: E402

from test_gui_my_files_tk import ctx, go, labels_of  # noqa: E402,F401


def pump(ctx, cond, secs=2.0):
    end = time.time() + secs
    while time.time() < end:
        ctx.app.update()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture
def nctx(ctx, netpc):
    wire(ctx.c, ctx.tmp, netpc)
    ctx.pc = netpc
    ctx.asked = []
    orig = messagebox.askokcancel
    return ctx


def test_receive_section_collapsed_then_plan_apply_restore(nctx, monkeypatch):
    v = go(nctx, S.LAN_RECV)
    sec = v.net
    assert sec.col.open is False and not sec.pending_row.winfo_ismapped() and not sec.hint.winfo_ismapped()
    assert T.NET_TITLE in labels_of(sec)
    sec.col.toggle_btn.invoke()
    nctx.app.update()
    assert sec.col.open and list(sec.adapter_box.cget("values")) == ["Ethernet A"] and sec.adapter_var.get() == "Ethernet A"
    assert str(sec.apply_btn.cget("state")) == "disabled"  # 계획을 먼저 본다
    sec.plan_btn.invoke()
    nctx.app.update()
    assert "10.254.250.2" in sec.plan_label.cget("text") and str(sec.apply_btn.cget("state")) == "normal"
    seen = []
    monkeypatch.setattr(messagebox, "askokcancel", lambda title, text, **k: seen.append((title, text)) or False)
    sec.apply_btn.invoke()
    nctx.app.update()
    assert seen[0][0] == T.NET_CONFIRM_TITLE and "Ethernet A" in seen[0][1] and "10.254.250.2" in seen[0][1] and "되돌립니다" in seen[0][1]
    assert nctx.pc.netsh_count == 0 and nctx.c.net_applied() is None  # 취소하면 아무것도 바꾸지 않는다
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
    sec.apply_btn.invoke()
    nctx.app.update()
    assert "적용됨: 10.254.250.2" in sec.status_label.cget("text") and str(sec.restore_btn.cget("state")) == "normal"
    assert "10.254.250.2" in v.addr_label.cget("text")  # 받는 쪽 화면의 주소 표시에 이 PC 주소가 보인다
    sec.restore_btn.invoke()
    nctx.app.update()
    assert nctx.c.net_applied() is None and T.NET_RESTORED in sec.status_label.cget("text") and nctx.pc.find(7)["dhcp"] == "Enabled"
    assert str(sec.restore_btn.cget("state")) == "disabled"


def test_receive_done_restores_automatically(nctx, monkeypatch):
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
    v = go(nctx, S.LAN_RECV)
    v.net.col.toggle_btn.invoke()
    v.net.plan_btn.invoke()
    v.net.apply_btn.invoke()
    nctx.app.update()
    assert nctx.c.net_applied()
    nctx.c.files.rx_state = "done"
    v._render()
    assert pump(nctx, lambda: nctx.c.net_applied() is None)
    assert nctx.pc.find(7)["dhcp"] == "Enabled" and not nctx.pc.state.exists()


def test_receive_pending_record_shows_notice_and_restore_button(nctx, monkeypatch):
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=nctx.pc.env()), lambda t: True, nctx.pc.env())
    v = go(nctx, S.LAN_RECV)
    assert v.net.pending_row.winfo_ismapped() and "10.254.250.1" in v.net.pending_label.cget("text")
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    v.net.pending_btn.invoke()
    nctx.app.update()
    assert not v.net.pending_row.winfo_ismapped() and not nctx.pc.state.exists() and nctx.pc.find(7)["dhcp"] == "Enabled"


def test_app_offers_restore_at_start_after_confirmation(nctx, monkeypatch):
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=nctx.pc.env()), lambda t: True, nctx.pc.env())
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda title, text, **k: asked.append(text) or False)
    nctx.app._offer_net_restore()
    assert asked and "10.254.250.1" in asked[0] and nctx.pc.state.exists()  # '아니오'면 그대로 둔다
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    nctx.app._offer_net_restore()
    assert not nctx.pc.state.exists()


def test_send_section_uses_peer_and_restores_after_success(nctx, monkeypatch):
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
    v = go(nctx, S.LAN_SEND)
    assert v.net.col.open is False
    nctx.c.lan_set_target("172.30.250.2:47830", "123456")
    v.net.col.toggle_btn.invoke()
    v.net.plan_btn.invoke()
    assert "172.30.250.1" in v.net.plan_label.cget("text")
    v.net.apply_btn.invoke()
    nctx.app.update()
    assert nctx.pc.find(7)["addresses"][0]["ip"] == "172.30.250.1"
    nctx.c.files.tx_report = {"verification": {"exact": 1, "mismatch": 0, "missing": 0}, "files": 1}
    nctx.c.emit("lan_send_done", {"report": nctx.c.files.tx_report})
    assert pump(nctx, lambda: nctx.c.net_applied() is None)
    assert nctx.pc.find(7)["dhcp"] == "Enabled"


def test_send_connection_error_keeps_address_for_retry(nctx, monkeypatch):
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
    v = go(nctx, S.LAN_SEND)
    v.net.col.toggle_btn.invoke()
    v.net.plan_btn.invoke()
    v.net.apply_btn.invoke()
    nctx.app.update()
    nctx.c.files.tx_error, nctx.c.files.tx_error_code = "연결하지 못했습니다", "unreachable"
    nctx.c.emit("lan_send_done", {"lan_error": "unreachable"})
    time.sleep(0.2)
    nctx.app.update()
    assert nctx.c.net_applied() is not None  # 연결 실패면 다시 시도할 수 있게 그대로 둔다
    assert str(v.net.restore_btn.cget("state")) == "normal"


def test_send_subnet_diagnosis_opens_section_with_hint(nctx):
    v = go(nctx, S.LAN_SEND)
    nctx.c.lan_api.run_diagnose = lambda target, code=None: SimpleNamespace(summary="서브넷이 다른 것 같습니다", code="subnet")
    v.target_var.set("192.168.5.20:47830")
    v.code_var.set("123456")
    v.diag_btn.invoke()
    nctx.app.update()
    assert nctx.c.files.diag_code == "subnet"
    assert v.net.hint.winfo_ismapped() and v.net.col.open and T.NET_SUBNET_HINT in labels_of(v.net) + [v.net.hint.cget("text")]
    nctx.c.lan_api.run_diagnose = lambda target, code=None: SimpleNamespace(summary="연결할 수 있습니다", code="ok")
    v.diag_btn.invoke()
    nctx.app.update()
    assert not v.net.hint.winfo_ismapped()


def test_new_texts_use_polite_endings_and_no_banned_terms():
    from test_gui_texts import banned_in, flatten
    for name in dir(T):
        if name.startswith(("NET_", "BTN_NET_", "TASK_NET_")):
            for t in flatten(getattr(T, name)):
                assert banned_in(t) == [], name
    for name in ("NET_INTRO", "NET_SUBNET_HINT", "NET_PLAN_FIRST", "NET_NEED_CONFIRM", "NET_RESTORE_ASK"):
        assert getattr(T, name).rstrip().endswith(("다.", "요.", "요?", "다"))
    assert "해 주세요" in T.NET_PENDING or "주세요" in T.NET_PENDING
