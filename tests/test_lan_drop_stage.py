"""stage63: LAN 직접 보내기에서 직접 고른 파일(extra_files). 127.0.0.1 루프백과 임시 폴더만 쓴다(실제 네트워크 설정 변경 없음).

받는 쪽이 만드는 구조는 폴더 전송과 같고(프로토콜 변경 없음), 임시 보관 폴더는 성공·취소·예외 모두에서 지워진다.
"""
import json
import os
import shutil

import pytest

from pcmigrate import cli, drop_stage as ds, lan_api, multi_transfer as mt
from pcmigrate.cancel import Cancelled
from pcmigrate.find_data import SelectedItem

from test_lan_transfer import _fast_kdf, make_src, rx_factory, user_tree, wait_state  # noqa: F401
from test_place_original import Env, contents


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


def rel(p):
    return mt.dest_rel(str(p)).replace("\\", "/")


def pc(tmp_path):
    base = tmp_path / "pc" / "Work"
    make_src(base, {"A/a1.txt": b"1" * 10, "A/sub/a2.txt": b"2" * 20, "B/b1.txt": b"3" * 30, "loose.txt": b"4" * 5, "C/c1.txt": b"5" * 7})
    return base


def leftovers():
    root = ds.staging_root()
    return list(root.iterdir()) if root.exists() else []


def send(rx, items, extras, **kw):
    return lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, selection=items, options={"chunk": 4096}, extra_files=extras, **kw)


def test_folder_plus_dropped_files_same_structure_as_folders_and_cleanup(rx_factory, tmp_path):
    base = pc(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / "pc").rglob("*") if p.is_file()}
    rx = rx_factory()
    warns = []
    rep = send(rx, [SelectedItem(str(base / "A"), True)], [base / "A" / "sub" / "a2.txt", base / "B", base / "loose.txt"], warn=warns.append)
    assert rep["success"] and rep["failed_count"] == 0
    r = rel(base)
    assert user_tree(tmp_path / "dst") == {f"{r}/A/a1.txt": b"1" * 10, f"{r}/A/sub/a2.txt": b"2" * 20, f"{r}/B/b1.txt": b"3" * 30,
                                         f"{r}/loose.txt": b"4" * 5}
    d = rep["drop_stage"]
    assert d["overlap_count"] == 1 and d["staged_files"] == 2 and d["cleanup"]["removed"] and leftovers() == []
    assert any("이미 선택한 폴더에 포함" in w or "포함되어" in w for w in warns)
    assert before == {p: p.stat().st_mtime_ns for p in (tmp_path / "pc").rglob("*") if p.is_file()}


def test_only_dropped_files_no_folder(rx_factory, tmp_path):
    base = pc(tmp_path)
    rx = rx_factory()
    rep = send(rx, [], [base / "loose.txt"])
    assert rep["success"] and user_tree(tmp_path / "dst") == {f"{rel(base)}/loose.txt": b"4" * 5}
    assert leftovers() == []


def test_no_extras_is_unchanged(rx_factory, tmp_path):
    base = pc(tmp_path)
    rx = rx_factory()
    rep = lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, selection=[SelectedItem(str(base / "A"), True)], options={"chunk": 4096})
    assert rep["success"] and "drop_stage" not in rep and leftovers() == []


def test_failed_dropped_file_does_not_stop_others_and_marks_report(rx_factory, tmp_path):
    base = pc(tmp_path)
    rx = rx_factory()
    rep = send(rx, [SelectedItem(str(base / "A"), True)], [base / "gone.bin", base / "loose.txt"])
    assert rep["failed_count"] == 1 and rep["success"] is False
    assert rep["drop_stage"]["failed"][0]["path"].endswith("gone.bin")
    assert any(f["path"].endswith("gone.bin") for f in rep["failed"])
    assert f"{rel(base)}/loose.txt" in user_tree(tmp_path / "dst") and leftovers() == []


def test_cloud_only_dropped_file_excluded_and_counted(tmp_path):
    base = pc(tmp_path)
    # attr_fn 주입은 drop_stage 직접 호출로 시험(공개 API 는 실제 속성을 쓴다)
    plan = ds.plan_drops([ds.describe(base / "loose.txt")], [], attr_fn=lambda st: 0x00400000)
    assert not plan.files and plan.cloud_only
    res = ds.attach_info({"success": True, "cloud_only_skipped": 1, "failed_count": 0}, ds._info(plan, []))
    assert res["cloud_only_skipped"] == 2


def test_cancel_cleans_up_and_receiver_keeps_waiting(rx_factory, tmp_path):
    base = pc(tmp_path)
    make_src(base, {f"many/f{i}.bin": os.urandom(30_000) for i in range(12)})
    rx = rx_factory()
    n = {"c": 0}

    def cancel():
        n["c"] += 1
        return n["c"] > 60
    with pytest.raises(Cancelled):
        lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, selection=[], options={"chunk": 4096}, extra_files=[base / "many"], cancel=cancel)
    wait_state(rx, "waiting")
    assert leftovers() == []
    assert len(list((base / "many").iterdir())) == 12  # 원본은 그대로


def test_connection_error_cleans_up_without_error_log_noise(tmp_path):
    from pcmigrate.lan_protocol import LanError
    base = pc(tmp_path)
    with pytest.raises(LanError):
        lan_api.run_send("127.0.0.1:9", "123456", selection=[], options={"chunk": 4096}, extra_files=[base / "loose.txt"])
    assert leftovers() == []


def test_unsafe_name_is_reported_not_fatal(rx_factory, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows 예약 이름 시험")
    base = pc(tmp_path)
    rx = rx_factory()
    rep = send(rx, [SelectedItem(str(base / "A"), True)], [base / "loose.txt"])
    assert rep["success"]  # 정상 이름은 경로 안전 검사(validate_rel)에 걸리지 않는다
    assert all(k.count("..") == 0 for k in user_tree(tmp_path / "dst"))


def test_nested_received_structure_then_place(rx_factory, tmp_path):
    base = pc(tmp_path)
    rx = rx_factory()
    rep = send(rx, [SelectedItem(str(base / "A" / "sub"), True)], [base / "A" / "a1.txt", base / "loose.txt"])
    assert rep["success"]
    dst = tmp_path / "dst"
    env = Env(tmp_path, letters="D")
    real = rel(base).split("/")
    shutil.rmtree(env.recv)
    shutil.move(str(dst), str(env.recv))
    (env.recv / "D").mkdir()
    shutil.move(str(env.recv.joinpath(*real)), str(env.recv / "D" / "Work"))
    shutil.rmtree(env.recv / real[0])
    got = contents(env.recv / "D" / "Work")
    assert {k for k in got if not k.split("\\")[-1].startswith(".pcmigrate-")} == {"A\\a1.txt", "A\\sub\\a2.txt", "loose.txt"}
    out = env.run(apply=True, protected_fn=lambda p, **k: None).to_dict()
    assert out["failed_count"] == 0 and out["skipped_count"] == 0
    placed = contents(env.drives / "D" / "Work")
    assert placed == {"A\\a1.txt": b"1" * 10, "A\\sub\\a2.txt": b"2" * 20, "loose.txt": b"4" * 5}


def test_cli_send_extra_file(rx_factory, tmp_path, capsys):
    base = pc(tmp_path)
    rx = rx_factory()
    code = cli.main(["send", f"127.0.0.1:{rx.port}", "--code", rx.code, str(base / "A"), "--extra-file", str(base / "loose.txt"),
                     "--extra-file", str(base / "A" / "a1.txt"), "--report", str(tmp_path / "r.json")])
    out = capsys.readouterr().out
    assert code == 0 and "직접 고른" in out
    d = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert d["drop_stage"]["overlap_count"] == 1 and leftovers() == []
    assert f"{rel(base)}/loose.txt" in user_tree(tmp_path / "dst")
