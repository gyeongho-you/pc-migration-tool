"""stage39: 랜선 직결용 이더넷 주소 임시 맞추기(net_setup / net_setup_cli / lan_api). 전부 가짜 runner(net_fakes.FakePC) — 실제 네트워크 설정은 건드리지 않는다."""
import argparse
import json
import subprocess

import pytest

from pcmigrate import cli, lan_api
from pcmigrate import net_setup as ns
from pcmigrate import net_setup_cli

from net_fakes import FakePC, adapter


@pytest.fixture
def pc(tmp_path):
    return FakePC(tmp_path)


def yes(_text):
    return True


# --------------------------------------------------------------------------- 어댑터 필터
def test_adapter_filter_keeps_only_wired_up_without_gateway(tmp_path):
    pc = FakePC(tmp_path, [
        adapter("Ethernet A", 7, addresses=[("169.254.1.5", 16)]),
        adapter("Wi-Fi", 8, media="Native 802.11", desc="Intel Wireless-AC"),
        adapter("vEthernet (Default Switch)", 9, media="Unspecified", desc="Hyper-V Virtual Ethernet Adapter", hardware=False),
        adapter("Ethernet VPN", 10, desc="TAP-Windows Adapter V9"),
        adapter("Bluetooth Network", 11, media="Bluetooth", desc="Bluetooth Device (PAN)"),
        adapter("Ethernet Loop", 12, media="802.3", desc="Microsoft KM-TEST Loopback Adapter"),
        adapter("Ethernet Net", 13, addresses=[("192.168.0.5", 24)], gateways=["192.168.0.1"]),
        adapter("Ethernet Down", 14, status="Disconnected"),
    ])
    assert [a.name for a in ns.list_adapters(pc.env())] == ["Ethernet A"]
    assert [a.name for a in ns.list_adapters(pc.env(), direct_only=False)] == ["Ethernet A", "Ethernet Net"]
    assert all(c[0] == "powershell.exe" for c in pc.calls)  # 조회만 했다


def test_zero_gateway_value_counts_as_no_gateway(tmp_path):
    pc = FakePC(tmp_path, [adapter(gateways=["0.0.0.0"])])
    assert len(ns.list_adapters(pc.env())) == 1


def test_query_failure_is_easy_error(tmp_path):
    env = ns.NetEnv(runner=lambda a, timeout=0, env=None: ns.RunResult(1, "", "boom"), state_path=lambda: tmp_path / "x.json")
    with pytest.raises(ns.NetSetupError) as e:
        ns.list_adapters(env)
    assert e.value.code == "query_failed"
    env2 = ns.NetEnv(runner=lambda a, timeout=0, env=None: ns.RunResult(0, "not json"), state_path=lambda: tmp_path / "x.json")
    with pytest.raises(ns.NetSetupError):
        ns.list_adapters(env2)


# --------------------------------------------------------------------------- 계획: 대역/역할/peer
def test_default_band_first_and_roles(pc):
    s = ns.plan_ip_setup("sender", env=pc.env())
    r = ns.plan_ip_setup("receiver", env=pc.env())
    assert ns.CANDIDATE_BANDS[0] == "10.254.250.0/24"
    assert (s.band, s.ip, s.prefix) == ("10.254.250.0/24", "10.254.250.1", 24)
    assert (r.band, r.ip) == ("10.254.250.0/24", "10.254.250.2")
    assert "10.254.250.1" in s.describe() and "되돌립" in s.describe() and "인터넷" in s.describe()


def test_overlap_moves_to_next_band(tmp_path):
    other = adapter("Ethernet Other", 20, addresses=[("10.254.250.77", 24)], gateways=["10.254.250.1"])
    pc = FakePC(tmp_path, [adapter(), other])
    assert ns.plan_ip_setup("receiver", env=pc.env()).band == "172.30.250.0/24"
    pc2 = FakePC(tmp_path, [adapter()], routes=[{"index": 99, "prefix": "172.30.0.0/16"}, {"index": 99, "prefix": "10.254.250.0/24"}])
    assert ns.plan_ip_setup("receiver", env=pc2.env()).ip == "192.168.250.2"
    pc3 = FakePC(tmp_path, [adapter()], routes=[{"index": 99, "prefix": p} for p in ns.CANDIDATE_BANDS])
    with pytest.raises(ns.NetSetupError) as e:
        ns.plan_ip_setup("sender", env=pc3.env())
    assert e.value.code == "no_band"


def test_target_adapters_own_address_does_not_count_as_used(tmp_path):
    pc = FakePC(tmp_path, [adapter(dhcp=False, addresses=[("10.254.250.9", 24)])],
                routes=[{"index": 7, "prefix": "10.254.250.0/24"}, {"index": 7, "prefix": "0.0.0.0/0"}])
    assert ns.plan_ip_setup("sender", env=pc.env()).band == "10.254.250.0/24"


def test_peer_matches_band_and_validates(tmp_path):
    pc = FakePC(tmp_path)
    p = ns.plan_ip_setup("sender", peer="172.30.250.2", env=pc.env())
    assert (p.band, p.ip, p.peer_ip) == ("172.30.250.0/24", "172.30.250.1", "172.30.250.2")
    assert ns.plan_ip_setup("sender", peer="172.30.250.2:47830", env=pc.env()).ip == "172.30.250.1"
    for bad, code in (("192.168.0.12", "peer_not_in_range"), ("abc", "bad_peer"), ("10.254.250.1", "band_in_use")):
        with pytest.raises(ns.NetSetupError) as e:
            ns.plan_ip_setup("sender", peer=bad, env=pc.env())
        assert e.value.code == code
    busy = FakePC(tmp_path, [adapter(), adapter("Other", 30, addresses=[("172.30.250.4", 24)], gateways=["172.30.250.254"])])
    with pytest.raises(ns.NetSetupError) as e:
        ns.plan_ip_setup("sender", peer="172.30.250.2", env=busy.env())
    assert e.value.code == "band_in_use"


def test_adapter_selection_rules(tmp_path):
    pc = FakePC(tmp_path, [adapter("Eth 1", 7), adapter("Eth 2", 8), adapter("Eth Net", 9, gateways=["192.168.0.1"], addresses=[("192.168.0.4", 24)])])
    with pytest.raises(ns.NetSetupError) as e:
        ns.plan_ip_setup("sender", env=pc.env())
    assert e.value.code == "ambiguous" and "Eth 1" in str(e.value)
    assert ns.plan_ip_setup("sender", "Eth 2", env=pc.env()).adapter.index == 8
    with pytest.raises(ns.NetSetupError) as e:
        ns.plan_ip_setup("sender", "Eth Net", env=pc.env())
    assert e.value.code == "internet_adapter"
    forced = ns.plan_ip_setup("sender", "Eth Net", allow_internet=True, env=pc.env())
    assert forced.internet_adapter and "끊길 수 있습니다" in forced.describe() and forced.describe().count("[!]") == 1
    with pytest.raises(ns.NetSetupError) as e:
        ns.plan_ip_setup("sender", "없는이름", env=pc.env())
    assert e.value.code == "unknown_adapter"
    with pytest.raises(ns.NetSetupError) as e:
        ns.plan_ip_setup("both", env=pc.env())
    assert e.value.code == "bad_role"
    wifi = FakePC(tmp_path, [adapter("Wi-Fi", 3, media="Native 802.11")])
    with pytest.raises(ns.NetSetupError) as e:
        ns.plan_ip_setup("sender", env=wifi.env())
    assert e.value.code == "no_candidate"
    with pytest.raises(ns.NetSetupError) as e:
        ns.plan_ip_setup("sender", "Wi-Fi", env=wifi.env())
    assert e.value.code == "not_wired"


# --------------------------------------------------------------------------- 명령 인자 검증
def test_command_builders_reject_injection():
    assert ns.cmd_set_static(7, "10.254.250.1", 24) == ["netsh", "interface", "ip", "set", "address", "name=7", "source=static",
                                                        "address=10.254.250.1", "mask=255.255.255.0"]
    for bad_idx in ("7", "7 & calc", 0, -1, True, None, 10 ** 9):
        with pytest.raises(ns.NetSetupError):
            ns.cmd_set_static(bad_idx, "10.254.250.1", 24)
    for bad_ip in ("10.254.250.1; calc", "10.254.250.1 mask=1.1.1.1", "010.254.250.1", "1.2.3", "::1", "", None, "10.254.250.1\n"):
        with pytest.raises(ns.NetSetupError):
            ns.cmd_set_static(7, bad_ip, 24)
    for bad_p in (0, 33, "24", True):
        with pytest.raises(ns.NetSetupError):
            ns.cmd_set_static(7, "10.254.250.1", bad_p)
    with pytest.raises(ns.NetSetupError):
        ns.cmd_set_static(7, "10.254.250.1", 24, gateway="1.1.1.1 & calc")


def test_apply_only_allows_candidate_dot1_dot2():
    rec = {"role": "sender", "adapter": {"index": 7}, "applied": {"ip": "10.254.250.1", "prefix": 24}}
    assert ns.apply_commands(rec)
    for ip, role in (("10.254.250.2", "sender"), ("10.254.250.3", "sender"), ("192.168.0.1", "sender"), ("10.254.251.1", "sender"),
                     ("172.30.250.2", "sender"), ("10.254.250.1", "receiver")):
        with pytest.raises(ns.NetSetupError):
            ns.apply_commands({**rec, "applied": {"ip": ip, "prefix": 24}, "role": role})
    with pytest.raises(ns.NetSetupError):  # 접두 길이도 24 만
        ns.validate_record({"version": 1, **rec, "applied": {"ip": "10.254.250.1", "prefix": 16}, "original": {"dhcp": True}})


def test_default_runner_uses_list_and_no_shell(monkeypatch):
    seen = {}

    def fake_run(args, **kw):
        seen.update(args=args, kw=kw)
        return subprocess.CompletedProcess(args, 0, b"ok", b"")
    monkeypatch.setattr(subprocess, "run", fake_run)
    r = ns.default_runner(["netsh", "interface", "ip", "set", "address", "name=7", "source=dhcp"])
    assert r.returncode == 0 and isinstance(seen["args"], list) and seen["kw"]["shell"] is False


def test_every_call_made_by_apply_and_restore_is_a_list_of_known_commands(pc):
    plan = ns.plan_ip_setup("receiver", env=pc.env())
    ns.apply_ip_setup(plan, yes, pc.env())
    ns.restore_ip_setup(pc.env())
    assert any(c[0] == "netsh" for c in pc.calls)
    for c in pc.calls:
        assert isinstance(c, list)
        assert c[0] in ("powershell.exe", "netsh")
        if c[0] == "netsh":
            assert c[1:4] in (["interface", "ip", "set"], ["interface", "ip", "add"])
            assert all(a.split("=")[0] in ("name", "source", "address", "mask", "gateway") for a in c[5:])
            assert not any(("&" in a or ";" in a or "|" in a) for a in c)


# --------------------------------------------------------------------------- 적용 / 복구
def test_record_saved_before_change_and_round_trip_dhcp(pc):
    before = json.dumps(pc.adapters, sort_keys=True)
    res = ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), yes, pc.env())
    assert res["ip"] == "10.254.250.1" and pc.state_seen_before_change and all(pc.state_seen_before_change)  # 첫 변경 전에 기록이 이미 있었다
    a = pc.find(7)
    assert a["dhcp"] == "Disabled" and a["addresses"] == [{"ip": "10.254.250.1", "prefix": 24}] and not a["gateways"] and not a["dns"]
    rec = json.loads(pc.state.read_text(encoding="utf-8"))
    assert rec["original"]["dhcp"] is True and rec["adapter"]["index"] == 7 and rec["applied"]["ip"] == "10.254.250.1" and rec["status"] == "applied"
    assert ns.pending_restore(pc.env())["ip"] == "10.254.250.1"
    assert ns.restore_ip_setup(pc.env())["restored"] and not pc.state.exists()
    assert pc.find(7)["dhcp"] == "Enabled" and json.dumps(pc.adapters, sort_keys=True) == before
    assert ns.restore_ip_setup(pc.env()) == {"ok": True, "restored": False}


def test_round_trip_static_original_with_two_addresses(tmp_path):
    pc = FakePC(tmp_path, [adapter(dhcp=False, addresses=[("172.16.5.9", 24), ("172.16.5.10", 24)])])
    ns.apply_ip_setup(ns.plan_ip_setup("receiver", env=pc.env()), yes, pc.env())
    assert pc.find(7)["addresses"] == [{"ip": "10.254.250.2", "prefix": 24}]
    ns.restore_ip_setup(pc.env())
    a = pc.find(7)
    assert a["dhcp"] == "Disabled" and [(x["ip"], x["prefix"]) for x in a["addresses"]] == [("172.16.5.9", 24), ("172.16.5.10", 24)]


def test_declined_confirmation_changes_nothing(pc):
    plan = ns.plan_ip_setup("sender", env=pc.env())
    n = len(pc.calls)
    for fn in (lambda t: False, None):
        with pytest.raises(ns.NetSetupError) as e:
            ns.apply_ip_setup(plan, fn, pc.env())
        assert e.value.code == "declined"
    assert len(pc.calls) == n and not pc.state.exists()


def test_confirm_receives_plan_text(pc):
    seen = []
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), lambda t: seen.append(t) or True, pc.env())
    assert "Ethernet A" in seen[0] and "10.254.250.1" in seen[0] and "관리자 권한" in seen[0]


def test_apply_and_rollback_both_failing_keeps_record_for_later_restore(pc):
    plan = ns.plan_ip_setup("sender", env=pc.env())
    pc.netsh_fail = True
    with pytest.raises(ns.NetSetupError) as e:
        ns.apply_ip_setup(plan, yes, pc.env())
    assert e.value.code == "rollback_failed" and pc.state.exists()
    pc.netsh_fail = False
    assert ns.restore_ip_setup(pc.env())["restored"] and not pc.state.exists()


def test_apply_failure_with_successful_rollback(pc):
    plan = ns.plan_ip_setup("sender", env=pc.env())
    orig = pc._netsh
    calls = {"n": 0}

    def flaky(args):
        calls["n"] += 1
        return ns.RunResult(1, "실패") if calls["n"] == 1 else orig(args)
    pc._netsh = flaky
    with pytest.raises(ns.NetSetupError) as e:
        ns.apply_ip_setup(plan, yes, pc.env())
    assert e.value.code == "apply_failed" and not pc.state.exists() and pc.find(7)["dhcp"] == "Enabled"


def test_verify_failure_rolls_back(pc):
    plan = ns.plan_ip_setup("sender", env=pc.env())
    pc.netsh_noop = True
    with pytest.raises(ns.NetSetupError) as e:
        ns.apply_ip_setup(plan, yes, pc.env())
    assert e.value.code == "verify_failed" and not pc.state.exists()


def test_second_apply_while_record_exists_is_refused(pc):
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), yes, pc.env())
    n = pc.netsh_count
    with pytest.raises(ns.NetSetupError) as e:
        ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), yes, pc.env())
    assert e.value.code == "pending_restore" and pc.netsh_count == n


def test_restore_when_adapter_is_gone_keeps_record(pc):
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), yes, pc.env())
    pc.adapters.clear()
    with pytest.raises(ns.NetSetupError) as e:
        ns.restore_ip_setup(pc.env())
    assert e.value.code == "restore_failed"
    assert pc.state.exists()


def test_state_write_failure_changes_nothing(pc, monkeypatch):
    plan = ns.plan_ip_setup("sender", env=pc.env())

    def boom(path, obj):
        raise OSError("read-only")
    monkeypatch.setattr(ns, "_atomic_write", boom)
    with pytest.raises(ns.NetSetupError) as e:
        ns.apply_ip_setup(plan, yes, pc.env())
    assert e.value.code == "state_write" and pc.netsh_count == 0


def test_atomic_write_leaves_no_temp_files(pc):
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), yes, pc.env())
    assert [p.name for p in pc.state.parent.iterdir()] == ["net-restore.json"]


# --------------------------------------------------------------------------- 승격(UAC)
def test_not_admin_uses_elevated_helper(tmp_path):
    pc = FakePC(tmp_path, admin=False)
    res = ns.apply_ip_setup(ns.plan_ip_setup("receiver", env=pc.env()), yes, pc.env())
    assert res["ip"] == "10.254.250.2" and pc.elevated_runs == 1
    ps = [c for c in pc.calls if c[-1] == ns.ELEVATE_SCRIPT]
    assert len(ps) == 1 and ps[0][0] == "powershell.exe"
    env = next(e for e in pc.envs if e)
    args = env["PCM_ELEV_ARGS"]
    assert "--apply-elevated" in args and "--action apply" in args and "--role receiver" in args
    assert "--adapter-index 7" in args and "--state-file" in args
    ns.restore_ip_setup(pc.env())
    assert pc.elevated_runs == 2 and not pc.state.exists() and pc.find(7)["dhcp"] == "Enabled"


def test_uac_denied_is_easy_error_and_changes_nothing(tmp_path):
    pc = FakePC(tmp_path, admin=False)
    pc.uac_denied = True
    with pytest.raises(ns.NetSetupError) as e:
        ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), yes, pc.env())
    assert e.value.code == "uac_denied" and "관리자" in str(e.value)
    assert pc.netsh_count == 0 and not pc.state.exists() and pc.find(7)["dhcp"] == "Enabled"


def test_uac_denied_on_restore_keeps_record(tmp_path):
    pc = FakePC(tmp_path, admin=True)
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), yes, pc.env())
    pc.admin, pc.uac_denied = False, True
    with pytest.raises(ns.NetSetupError) as e:
        ns.restore_ip_setup(pc.env())
    assert e.value.code == "uac_denied" and pc.state.exists()


def test_elevated_helper_rejects_tampered_record(pc):
    plan = ns.plan_ip_setup("sender", env=pc.env())
    rec = ns.build_record(plan, pc.env())
    rec["applied"]["ip"] = "192.168.0.99"  # 후보 대역이 아님
    pc.state.parent.mkdir(parents=True, exist_ok=True)
    pc.state.write_text(json.dumps(rec), encoding="utf-8")
    assert ns.elevated_main("apply", "sender", 7, str(pc.state), pc.env()) == 1
    assert pc.netsh_count == 0
    assert json.loads(pc.state.read_text(encoding="utf-8"))["result"]["ok"] is False
    # 인자와 기록이 어긋나도 거부(어댑터 번호/역할/동작)
    rec["applied"]["ip"] = "10.254.250.1"
    pc.state.write_text(json.dumps(rec), encoding="utf-8")
    assert ns.elevated_main("apply", "sender", 8, str(pc.state), pc.env()) == 1
    assert ns.elevated_main("apply", "receiver", 7, str(pc.state), pc.env()) == 1
    assert ns.elevated_main("format", "sender", 7, str(pc.state), pc.env()) == 1
    assert pc.netsh_count == 0


def test_elevated_args_text_quotes_and_rejects_bad_values(tmp_path):
    rec = {"role": "sender", "adapter": {"index": 7}}
    txt = ns.elevated_args_text("apply", rec, tmp_path / "a b" / "net-restore.json", ["-m", "pcmigrate"])
    assert txt.startswith('-m pcmigrate lan-setup --apply-elevated --action apply --role sender --adapter-index 7 --state-file "')
    with pytest.raises(ns.NetSetupError):
        ns.elevated_args_text("apply", rec, tmp_path / 'x"y', [])
    with pytest.raises(ns.NetSetupError):
        ns.elevated_args_text("rm", rec, tmp_path / "x", [])


# --------------------------------------------------------------------------- 중단 후 복구 제안
def test_interrupted_run_is_offered_restore_and_bad_record_is_reported(pc):
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=pc.env()), yes, pc.env())  # 프로그램이 여기서 꺼졌다고 가정
    pend = ns.pending_restore(pc.env())
    assert pend and not pend["bad"] and pend["adapter"] == "Ethernet A" and "되돌" in pend["message"]
    assert ns.current_status(pc.env())["pending"]["ip"] == "10.254.250.1"
    pc.state.write_text("{not json", encoding="utf-8")
    bad = ns.pending_restore(pc.env())
    assert bad["bad"] and "올바르지" in bad["message"]
    with pytest.raises(ns.NetSetupError):
        ns.restore_ip_setup(pc.env())
    pc.state.unlink()
    assert ns.pending_restore(pc.env()) is None


# --------------------------------------------------------------------------- lan_api
def test_lan_api_exposes_net_functions(pc):
    assert lan_api.plan_ip_setup("sender", env=pc.env()).ip == "10.254.250.1"
    assert [a.name for a in lan_api.list_ip_adapters(pc.env())] == ["Ethernet A"]
    plan = lan_api.plan_ip_setup("receiver", env=pc.env())
    assert lan_api.apply_ip_setup(plan, yes, pc.env())["ip"] == "10.254.250.2"
    assert lan_api.pending_restore(pc.env()) is not None
    assert lan_api.restore_ip_setup(pc.env())["restored"] and lan_api.pending_restore(pc.env()) is None


# --------------------------------------------------------------------------- CLI
@pytest.fixture
def clipc(pc, monkeypatch):
    monkeypatch.setattr(net_setup_cli, "ENV_FACTORY", pc.env)
    return pc


def run(capsys, *argv):
    rc = cli.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_preview_is_default_and_changes_nothing(clipc, capsys):
    rc, out, err = run(capsys, "lan-setup", "--role", "sender")
    assert rc == 0 and "10.254.250.1" in out and "복구 방법" in out and "--yes" in out
    assert clipc.netsh_count == 0 and not clipc.state.exists()
    rc, out, _ = run(capsys, "lan-setup", "--role", "receiver", "--dry-run", "--yes")
    assert rc == 0 and "10.254.250.2" in out and clipc.netsh_count == 0


def test_cli_yes_applies_and_restore_flow(clipc, capsys):
    rc, out, _ = run(capsys, "lan-setup", "--role", "receiver", "--yes")
    assert rc == 0 and "[적용됨]" in out and clipc.find(7)["addresses"][0]["ip"] == "10.254.250.2"
    rc, out, err = run(capsys, "lan-setup", "--status")
    assert rc == 0 and "복구 기록" in out and "10.254.250.2" in out
    rc, out, _ = run(capsys, "lan-setup", "--restore")
    assert rc == 0 and "미리보기" in out and clipc.state.exists()
    rc, out, _ = run(capsys, "lan-setup", "--restore", "--yes")
    assert rc == 0 and "[되돌림]" in out and not clipc.state.exists() and clipc.find(7)["dhcp"] == "Enabled"
    rc, out, _ = run(capsys, "lan-setup", "--restore", "--yes")
    assert rc == 0 and "되돌릴 기록이 없습니다" in out


def test_cli_status_json_and_errors(clipc, capsys):
    rc, out, _ = run(capsys, "lan-setup", "--status", "--json")
    d = json.loads(out)
    assert rc == 0 and d["pending"] is None and d["adapters"][0]["direct_candidate"] is True
    rc, _, err = run(capsys, "lan-setup")
    assert rc == 2 and "--role" in err
    rc, _, err = run(capsys, "lan-setup", "--role", "sender", "--adapter", "없는이름")
    assert rc == 2 and "찾지 못" in err
    rc, _, err = run(capsys, "lan-setup", "--role", "sender", "--peer", "192.168.0.12")
    assert rc == 2 and "자동 맞추기 대역" in err


def test_cli_peer_matches_band(clipc, capsys):
    rc, out, _ = run(capsys, "lan-setup", "--role", "sender", "--peer", "172.30.250.2", "--yes")
    assert rc == 0 and clipc.find(7)["addresses"][0]["ip"] == "172.30.250.1"


def test_cli_internet_adapter_needs_explicit_flag(tmp_path, monkeypatch, capsys):
    pc = FakePC(tmp_path, [adapter("Eth Net", 9, addresses=[("192.168.0.4", 24)], gateways=["192.168.0.1"], dhcp=False)])
    monkeypatch.setattr(net_setup_cli, "ENV_FACTORY", pc.env)
    rc, _, err = run(capsys, "lan-setup", "--role", "sender", "--adapter", "Eth Net", "--yes")
    assert rc == 2 and "인터넷" in err and pc.netsh_count == 0
    rc, out, _ = run(capsys, "lan-setup", "--role", "sender", "--adapter", "Eth Net", "--allow-internet-adapter")
    assert rc == 0 and "[!]" in out and pc.netsh_count == 0  # 미리보기만


def test_cli_other_commands_only_announce_pending_record(clipc, capsys):
    ns.apply_ip_setup(ns.plan_ip_setup("sender", env=clipc.env()), yes, clipc.env())
    n = clipc.netsh_count
    rc, out, err = run(capsys, "logs")
    assert "[안내]" in err and "lan-setup --restore --yes" in err and clipc.netsh_count == n  # 안내만, 자동 복구 없음


def test_cli_elevated_entry_point(clipc, capsys):
    plan = ns.plan_ip_setup("sender", env=clipc.env())
    rec = ns.build_record(plan, clipc.env())
    clipc.state.parent.mkdir(parents=True, exist_ok=True)
    clipc.state.write_text(json.dumps(rec), encoding="utf-8")
    base = ("lan-setup", "--apply-elevated", "--role", "sender", "--adapter-index", "7", "--state-file", str(clipc.state))
    rc, _, _ = run(capsys, *base, "--action", "apply")
    assert rc == 0 and json.loads(clipc.state.read_text(encoding="utf-8"))["result"]["ok"] is True
    rc, _, _ = run(capsys, *base, "--action", "restore")
    assert rc == 0 and not clipc.state.exists()
    rc, _, _ = run(capsys, "lan-setup", "--apply-elevated", "--action", "apply")
    assert rc == 2


def test_auto_ip_scope_restores_even_on_error(clipc, monkeypatch):
    monkeypatch.setattr("sys.stdin", type("T", (), {"isatty": lambda s: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    args = argparse.Namespace(auto_ip=True)
    with pytest.raises(RuntimeError):
        with net_setup_cli.auto_ip_scope(args, "receiver") as rc:
            assert rc is None and clipc.find(7)["addresses"][0]["ip"] == "10.254.250.2"
            raise RuntimeError("전송 중 오류")
    assert not clipc.state.exists() and clipc.find(7)["dhcp"] == "Enabled"


def test_auto_ip_scope_needs_terminal_confirmation(clipc, monkeypatch):
    monkeypatch.setattr("sys.stdin", type("T", (), {"isatty": lambda s: False})())
    with net_setup_cli.auto_ip_scope(argparse.Namespace(auto_ip=True), "sender") as rc:
        assert rc == 1
    assert clipc.netsh_count == 0
    with net_setup_cli.auto_ip_scope(argparse.Namespace(auto_ip=False), "sender") as rc:
        assert rc is None


def test_receive_and_send_have_auto_ip_option():
    p = cli.build_parser()
    assert p.parse_args(["receive", "--auto-ip"]).auto_ip is True
    assert p.parse_args(["send", "자동", "--auto-ip", "x"]).auto_ip is True
    assert p.parse_args(["receive"]).auto_ip is False


# --------------------------------------------------------------------------- 문서
def test_docs_mention_lan_setup_and_stay_synced():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    md = (root / "docs" / "field-guide.md").read_text(encoding="utf-8")
    ht = (root / "docs" / "field-guide.html").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for needle in ("lan-setup", "--restore", "관리자 권한", "인터넷에 연결된 어댑터", "실제 PC"):
        assert needle in md and needle in ht and needle in readme, needle
