"""stage35: 항목별 대상 상대 경로(dest_rel) - 설정 묶음이 <대상>\\설정 묶음 으로 가고 구 PC 임시/사용자 경로가 받는 쪽에 남지 않는다.
가짜 사용자/경로만 쓴다. LAN 은 127.0.0.1 루프백."""
import json
import os
from pathlib import Path

import pytest

from pcmigrate import api as real_api
from pcmigrate import data_api, find_data as fd, lan_api, multi_transfer as mt
from pcmigrate.gui import texts as T
from pcmigrate.transfer import TransferError

from test_stage29_lan import _fast_kdf, make_src, rx_factory, send  # noqa: F401
from test_stage34 import env, make_zip, touch  # noqa: F401


def S(path, **kw):
    return fd.SelectedItem(str(path), True, None, None, "", **kw)


def tree(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate-"))


BAD = ["", " ", "a/b", "a\\b", "..", ".", "C:", "C:\\x", "/abs", "\\\\srv\\sh", "x:stream", "CON", "nul.txt", "end.", "end ", " lead",
       "a" * 300, ".pcmigrate-x"]


@pytest.mark.parametrize("bad", BAD)
def test_check_dest_rel_rejects_unsafe_names(bad):
    with pytest.raises(ValueError):
        mt.check_dest_rel(bad)


def test_check_dest_rel_accepts_korean_single_name_and_texts_constant_is_valid():
    assert mt.check_dest_rel("설정 묶음") == "설정 묶음"
    assert mt.check_dest_rel(T.BUNDLE_DEST_DIR) == T.BUNDLE_DEST_DIR == "설정 묶음"


@pytest.mark.parametrize("bad", ["a/b", "..", "CON", "end."])
def test_plan_items_rejects_bad_dest_rel(tmp_path, bad):
    src = make_src(tmp_path / "s", {"a.txt": b"1"})
    with pytest.raises(TransferError):
        mt.plan_items([S(src, dest_rel=bad)], tmp_path / "dst")


def test_plan_items_overlap_rejected(tmp_path):
    a, b = make_src(tmp_path / "a", {"x": b"1"}), make_src(tmp_path / "b", {"y": b"2"})
    with pytest.raises(TransferError):
        mt.plan_items([S(a, dest_rel="묶음"), S(b, dest_rel="묶음")], tmp_path / "dst")
    with pytest.raises(TransferError):
        mt.plan_items([S(a, dest_rel="abc"), S(b, dest_rel="ABC")], tmp_path / "dst")
    head = mt.dest_rel(str(b)).split("\\")[0]  # 지정 이름이 다른 항목의 <드라이브> 구조 맨 앞 이름과 같으면 그 안쪽과 겹침
    with pytest.raises(TransferError):
        mt.plan_items([S(a, dest_rel=head), S(b)], tmp_path / "dst")


def test_plan_items_default_unchanged_and_custom_flagged(tmp_path):
    a, b = make_src(tmp_path / "a", {"x": b"1"}), make_src(tmp_path / "b", {"y": b"2"})
    p = mt.plan_items([S(a), S(b, dest_rel="설정 묶음")], tmp_path / "dst")
    by = {i.src: i for i in p.items}
    assert by[str(a)].rel == mt.dest_rel(str(a)) and not by[str(a)].custom
    assert by[str(b)].rel == "설정 묶음" and by[str(b)].custom


# --------------------------------------------------------------------------- 선택 파일
def test_selection_roundtrip_and_old_format_compat():
    items = [fd.SelectedItem("D:\\x", True, 5, 1, "L", "설정 묶음"), fd.SelectedItem("D:\\y", True)]
    d = fd.selected_items_dict(items)
    assert "dest_rel" not in d["items"][1] and d["items"][0]["dest_rel"] == "설정 묶음"
    assert fd.parse_selection(json.loads(json.dumps(d, ensure_ascii=False))) == items
    old = {"format": fd.SELECTION_FORMAT, "version": fd.SELECTION_VERSION, "items": [{"path": "D:\\x", "include": True}]}
    assert fd.parse_selection(old)[0].dest_rel == ""  # 이전 형식(키 없음)
    for bad in ("a/b", "..", 5):
        old["items"][0]["dest_rel"] = bad
        with pytest.raises(fd.SelectionFileError):
            fd.parse_selection(old)
    old["items"][0]["dest_rel"] = None
    assert fd.parse_selection(old)[0].dest_rel == ""


# --------------------------------------------------------------------------- multi_transfer 로컬 전송
def test_multi_transfer_local_bundle_dir_other_items_keep_structure_verify_and_resume(tmp_path):
    user = tmp_path / "Users" / "oldpcuser" / "AppData" / "Local" / "Temp" / "pcmigrate-bundle-ab12cd34"
    make_src(user, {"pcmigrate-settings.zip": b"zipdata"})
    proj = make_src(tmp_path / "proj", {"a.txt": b"AAA", "sub/b.txt": b"BB"})
    dst, man = tmp_path / "dst", tmp_path / "out" / "m.jsonl"
    man.parent.mkdir()
    items = [S(user, dest_rel="설정 묶음"), S(proj)]
    rep = data_api.run_transfer_selection(items, dst, manifest_out=man, verify_dest=True)
    assert rep.to_dict()["success"], rep.to_dict()
    files = tree(dst)
    assert (dst / "설정 묶음" / "pcmigrate-settings.zip").read_bytes() == b"zipdata"
    pr = mt.dest_rel(str(proj)).replace("\\", "/")
    assert f"{pr}/a.txt" in files and f"{pr}/sub/b.txt" in files
    assert not any("oldpcuser" in f or "pcmigrate-bundle" in f for f in files)
    assert not real_api.run_verify(man, dst).failures  # verify --manifest --target
    (dst / "설정 묶음" / "pcmigrate-settings.zip").write_bytes(b"zipdatX")
    assert real_api.run_verify(man, dst).failures
    (dst / "설정 묶음" / "pcmigrate-settings.zip").write_bytes(b"zipdata")
    rep2 = data_api.run_transfer_selection(items, dst, skip_done=True)  # 이어받기: 같은 구조에서 끝나고 새 폴더가 생기지 않는다
    assert all(i.status in ("ok", "skipped_done") for i in rep2.items)
    assert tree(dst) == files


def test_multi_transfer_bundle_only_goes_to_bundle_dir(tmp_path):
    stage = make_src(tmp_path / "Temp" / "pcmigrate-bundle-1", {"pcmigrate-settings.zip": b"z"})
    dst = tmp_path / "dst"
    rep = data_api.run_transfer_selection([S(stage, dest_rel=T.BUNDLE_DEST_DIR)], dst)
    assert rep.to_dict()["success"]
    assert tree(dst) == ["설정 묶음/pcmigrate-settings.zip"]


# --------------------------------------------------------------------------- LAN 루프백
def test_lan_loopback_bundle_goes_to_bundle_dir_other_items_unchanged(rx_factory, tmp_path):  # noqa: F811
    user = make_src(tmp_path / "Users" / "oldpcuser" / "AppData" / "Local" / "Temp" / "pcmigrate-bundle-ab12cd34",
                    {"pcmigrate-settings.zip": b"zz"})
    proj = make_src(tmp_path / "proj", {"a.txt": b"AAA"})
    rx = rx_factory(tmp_path / "recv")
    send(rx, selection=[S(user, dest_rel="설정 묶음"), S(proj)])
    files = tree(tmp_path / "recv")
    pr = mt.dest_rel(str(proj)).replace("\\", "/")
    assert files == sorted(["설정 묶음/pcmigrate-settings.zip", f"{pr}/a.txt"])
    assert not any("oldpcuser" in f for f in files)


def test_lan_loopback_single_custom_item_in_flat_layout(rx_factory, tmp_path):  # noqa: F811
    stage = make_src(tmp_path / "Temp" / "pcmigrate-bundle-9", {"pcmigrate-settings.zip": b"q"})
    rx = rx_factory(tmp_path / "recv")
    send(rx, selection=[S(stage, dest_rel="설정 묶음")], layout="flat")
    assert tree(tmp_path / "recv") == ["설정 묶음/pcmigrate-settings.zip"]


def test_lan_bad_dest_rel_is_refused_before_connecting(tmp_path):
    src = make_src(tmp_path / "s", {"a": b"1"})
    with pytest.raises(lan_api.LanError):
        lan_api.build_send_plan(selection=[S(src, dest_rel="../x")])


# --------------------------------------------------------------------------- GUI 컨트롤러와 ③ 찾기
def test_controller_bundle_item_has_dest_rel(env):  # noqa: F811
    make_zip(env.desk / "pcmigrate-settings.zip")
    env.c.set_bundle_included(True)
    it = env.c.bundle_item()
    assert it.dest_rel == T.BUNDLE_DEST_DIR and env.c.is_bundle_item(it)


def test_find_packages_finds_new_shallow_and_old_deep_locations(env):  # noqa: F811
    c = env.c
    base = 1_700_000_000
    recv = env.desk / "PC이전_받은파일_20260101-000000"
    new = touch(recv / T.BUNDLE_DEST_DIR / "pcmigrate-settings.zip", base + 100)
    old = touch(recv / "C" / "Users" / "someone" / "AppData" / "Local" / "Temp" / "pcmigrate-bundle-ab12cd34" / "pcmigrate-settings.zip", base + 200)
    assert c.find_packages() == [old, new]  # 최신 순, 둘 다 찾음
    os.utime(new, (base + 900, base + 900))
    assert c.find_packages() == [new, old]


def test_find_packages_new_location_found_even_when_deep_scan_is_exhausted(env, monkeypatch):  # noqa: F811
    from conftest import patch_ctl
    patch_ctl(monkeypatch, "RECEIVED_MAX_DIRS", 3)  # stage43: 이 상수를 쓰는 controller_pkgfind 에도 걸리게
    recv = env.desk / "PC이전_받은파일_20260101-000000"
    for i in range(10):
        (recv / f"d{i}").mkdir(parents=True)
    new = touch(recv / T.BUNDLE_DEST_DIR / "pcmigrate-settings.zip", 1_700_000_000)
    assert new in env.c.find_packages()
