"""stage77: 새 리뷰어 지적(전송·직접 추가 상자·LAN·place)의 재현 시험. 가짜 환경만 쓴다(가짜 attr/태그 주입, 루프백, 임시 폴더)."""
import json
import os
import stat
import time
from types import SimpleNamespace

import pytest

from pcmigrate import cli, drop_stage as ds, fs, lan_api, lan_cli, lan_transfer as LT, multi_transfer as mt, transfer as tr
from pcmigrate import data_api
from pcmigrate.cancel import Cancelled
from pcmigrate.find_data import SelectedItem
from pcmigrate.lan_protocol import LanError
from pcmigrate.transfer import DriveDisconnected
from pcmigrate.transfer_base import mtime_tolerance_ns, rotate_log

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_lan_transfer import _fast_kdf, make_src, rx_factory  # noqa: F401
from test_place_original import Env, contents

NOPROT = lambda p, **k: None  # noqa: E731
BS = "\\"
REPARSE = 0x400
RECALL = 0x00400000
CLOUD_TAG = 0x9000701A
SYMLINK_TAG = 0xA000000C
MOUNT_TAG = 0xA0000003


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


# ---------------------------------------------------------------- reparse: OneDrive 에서 받아 둔 파일을 링크로 오분류하지 않는다
def test_downloaded_onedrive_file_with_reparse_tag_is_staged_not_dropped_as_link(tmp_path):
    f = tmp_path / "keep.dwg"
    put(f, b"12345")
    plan = ds.plan_drops([ds.describe(f)], [], attr_fn=lambda st: REPARSE, tag_fn=lambda st: CLOUD_TAG)
    assert [os.path.basename(x[0]) for x in plan.files] == ["keep.dwg"] and not plan.skipped_links and not plan.failed


def test_recall_placeholder_still_cloud_only_and_real_symlink_is_a_reported_failure(tmp_path):
    f = tmp_path / "x.dwg"
    put(f, b"1")
    plan = ds.plan_drops([ds.describe(f)], [], attr_fn=lambda st: REPARSE | RECALL, tag_fn=lambda st: CLOUD_TAG)
    assert not plan.files and plan.cloud_only and not plan.failed
    plan = ds.plan_drops([ds.describe(f)], [], attr_fn=lambda st: REPARSE, tag_fn=lambda st: SYMLINK_TAG)
    assert not plan.files and plan.skipped_links and plan.failed and "바로가기" in plan.failed[0]["reason"]  # 조용한 누락이 아니라 실패로 센다


def test_file_and_dir_link_policy_helpers():
    st = lambda mode, attrs: SimpleNamespace(st_mode=mode, st_file_attributes=attrs)  # noqa: E731
    reg = stat.S_IFREG | 0o644
    dr = stat.S_IFDIR | 0o755
    assert not fs.file_is_link(st(reg, REPARSE), CLOUD_TAG) and not fs.file_is_link(st(reg, REPARSE), 0x80000017)  # 받아 둔 파일/WOF 압축
    assert fs.file_is_link(st(reg, REPARSE), SYMLINK_TAG) and fs.file_is_link(st(reg, REPARSE), MOUNT_TAG)
    assert not fs.dir_is_link(st(dr, REPARSE), CLOUD_TAG)  # 클라우드 태그 폴더는 따라간다
    assert fs.dir_is_link(st(dr, REPARSE), MOUNT_TAG) and fs.dir_is_link(st(dr, REPARSE), 0)  # 정션/태그 모름은 보수적으로 링크
    assert not fs.dir_is_link(st(dr, 0), 0)


def test_dropped_junction_folder_is_reported_not_silently_dropped(tmp_path):
    d = tmp_path / "j"
    d.mkdir()
    plan = ds.plan_drops([ds.describe(d)], [], attr_fn=lambda st: REPARSE, tag_fn=lambda st: MOUNT_TAG)
    assert plan.failed and plan.skipped_links == [str(d)]
    plan2 = ds.plan_drops([ds.describe(d)], [], attr_fn=lambda st: REPARSE, tag_fn=lambda st: CLOUD_TAG)  # 클라우드 폴더는 따라간다
    assert not plan2.failed and not plan2.skipped_links


# ---------------------------------------------------------------- LAN send 중단 리포트
def test_lan_send_cli_saves_aborted_report_with_numbered_bak_and_home_replaced(tmp_path):
    src = make_src(tmp_path / "src", {"a.txt": b"1"})
    rpt = tmp_path / "r.json"
    rpt.write_text('{"success": true, "old": 1}', encoding="utf-8")
    cli.main(["send", "127.0.0.1:9", "--code", "123456", str(src), "--report", str(rpt)])  # 연결 거부(LanError)
    d = json.loads(rpt.read_text(encoding="utf-8"))
    assert d["aborted"] is True and d["success"] is False and d["cancelled"] is False and d["sent_files"] == 0
    baks = [p for p in tmp_path.iterdir() if p.name.startswith("r.json.bak")]
    assert baks and json.loads(baks[0].read_text(encoding="utf-8")) == {"success": True, "old": 1}
    home = os.environ["USERPROFILE"]
    lan_cli._save_send_abort(str(tmp_path / "r2.json"), LanError(f"열 수 없음: {home}{BS}x", "refused"))
    text = (tmp_path / "r2.json").read_text(encoding="utf-8")
    assert home.replace(BS, BS * 2) not in text and "%USERPROFILE%" in text


def test_lan_send_cancel_carries_partial_counts_and_cli_saves_them(rx_factory, tmp_path, monkeypatch):
    src = make_src(tmp_path / "src", {f"f{i}.bin": os.urandom(30_000) for i in range(12)})
    rx = rx_factory()
    n = {"c": 0}

    def cancel():
        n["c"] += 1
        return n["c"] > 80
    with pytest.raises(Cancelled) as ei:
        lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, sources=[str(src)], options={"chunk": 4096}, cancel=cancel)
    part = ei.value.partial
    assert part["total_files"] == 12 and "sent_files" in part and part["last_file"]
    lan_cli._save_send_abort(str(tmp_path / "c.json"), ei.value)
    d = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert d["cancelled"] is True and d["total_files"] == 12 and d["last_file"]


def test_lan_api_passes_abort_report_to_the_extra_files_path(tmp_path):
    f = tmp_path / "x.txt"
    put(f, b"1")
    rpt = tmp_path / "a.json"
    with pytest.raises(LanError):
        lan_api.run_send("127.0.0.1:9", "123456", selection=[], options={"chunk": 4096}, extra_files=[f], abort_report=str(rpt))
    assert json.loads(rpt.read_text(encoding="utf-8"))["aborted"] is True


# ---------------------------------------------------------------- place: 미검증 자식 폴더는 부모 walk 가 옮기지 않는다
def test_place_unverified_child_is_not_moved_by_done_parent(tmp_path):
    env = Env(tmp_path, letters="D")
    put(env.recv / "D" / "proj" / "a.txt", b"a")
    put(env.recv / "D" / "proj" / "sub" / "b.txt", b"b")
    env.journal({"d" + BS + "proj": {"src": "D:" + BS + "proj", "rel": "D" + BS + "proj", "status": "done"},
                 "d" + BS + "proj" + BS + "sub": {"src": "D:" + BS + "proj" + BS + "sub", "rel": "D" + BS + "proj" + BS + "sub", "status": "failed"}})
    r = env.run(apply=True, protected_fn=NOPROT).to_dict()
    assert contents(env.drives / "D") == {"proj\\a.txt": b"a"}  # 부모의 파일만 놓임
    assert (env.recv / "D" / "proj" / "sub" / "b.txt").read_bytes() == b"b" and r["skipped_count"] == 1  # 미검증으로 보고, 파일은 그대로


# ---------------------------------------------------------------- _differs: NTFS 는 허용오차 0
def ntfs_info(fs_name):
    from pcmigrate.fs import DestInfo
    return lambda p: DestInfo("x", "fixed", fs_name, 10 ** 12, 1, "C:")


@pytest.mark.parametrize("fs_name,expect_bak", [("NTFS", True), ("FAT32", False)])
def test_same_size_content_change_within_one_second_makes_bak_on_ntfs_only(tmp_path, fs_name, expect_bak):
    src, dst = tmp_path / "src", tmp_path / "dst"
    put(src / "f.txt", b"AAAAAAAA")
    kw = dict(runner=FakeRobo(), dest_info_fn=ntfs_info(fs_name), keep_awake=False)
    tr.transfer(src, dst, **kw)
    st = (src / "f.txt").stat()
    (src / "f.txt").write_bytes(b"BBBBBBBB")  # 같은 크기, 다른 내용
    os.utime(src / "f.txt", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # 수정 시각 1초 차이
    tr.transfer(src, dst, **kw)
    assert (dst / "f.txt").read_bytes() == b"BBBBBBBB"
    assert (dst / "f.txt.bak").is_file() == expect_bak
    if expect_bak:
        assert (dst / "f.txt.bak").read_bytes() == b"AAAAAAAA"


def test_unchanged_rerun_on_ntfs_creates_no_bak_and_tolerance_helper(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    put(src / "f.txt", b"AAAAAAAA")
    kw = dict(runner=FakeRobo(), dest_info_fn=ntfs_info("NTFS"), keep_awake=False)
    tr.transfer(src, dst, **kw)
    tr.transfer(src, dst, **kw)
    assert not (dst / "f.txt.bak").exists()
    assert mtime_tolerance_ns("NTFS") == 0 and mtime_tolerance_ns(None) == 0 and mtime_tolerance_ns("exFAT") == 2_000_000_000


# ---------------------------------------------------------------- 낮음 항목
def test_abort_report_includes_items_finished_before_the_interruption(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a / "f.txt", b"1")
    put(b / "f.txt", b"2")
    real = data_api.run_transfer

    def fake(src, dst, **kw):
        if os.path.basename(str(src)) == "b":
            raise DriveDisconnected("드라이브가 분리됨")
        return real(src, dst, **kw)
    monkeypatch.setattr(data_api, "run_transfer", fake)
    rpt = tmp_path / "r.json"
    with pytest.raises(DriveDisconnected):
        data_api.run_transfer_selection([SelectedItem(str(a), True), SelectedItem(str(b), True)], tmp_path / "out", deps=deps_with(FakeRobo()),
                                        keep_awake=False, abort_report=str(rpt))
    d = json.loads(rpt.read_text(encoding="utf-8"))
    assert d["aborted"] and [i["status"] for i in d["items"]] == ["ok"] and d["items"][0]["src"].endswith("a")


def test_robocopy_log_rotates_when_too_big(tmp_path):
    log = tmp_path / ".pcmigrate-robocopy.log"
    log.write_bytes(b"x" * 100)
    rotate_log(log, limit=50)
    assert not log.exists() and (tmp_path / ".pcmigrate-robocopy.log.1").is_file()
    log.write_bytes(b"y")
    rotate_log(log, limit=50)
    assert log.exists()


def test_empty_dropped_folder_is_mentioned_in_the_summary(tmp_path):
    d = tmp_path / "d"
    put(d / "a.txt", b"1")
    (d / "empty").mkdir()
    plan = ds.plan_drops([ds.describe(d)], [])
    assert plan.empty_dirs == 1
    info = ds._info(plan, [ds.describe(d)])
    assert any("빈 폴더 1개" in line for line in ds.summary_lines(info))


def test_lan_finish_does_not_double_count_a_file_that_already_failed_during_retry():
    s = object.__new__(LT._Sender)
    e = SimpleNamespace(wire="a/b.txt")
    s.recs = {0: SimpleNamespace(state="bad", reason="mismatch", err="x", e=e)}
    s.counts = {"mismatch": 0}
    s.failed = [{"path": "a/b.txt", "reason": "unreadable", "error": "읽을 수 없음"}]  # 재시도 중 원본을 못 읽어 이미 적음
    s.plan = SimpleNamespace(total_files=1, total_bytes=1)
    s.ch = SimpleNamespace(send_json=lambda *a, **k: None)
    s._wait_for = lambda t: {}
    s._prog = lambda *a, **k: None
    s._finish()
    assert len(s.failed) == 1


def test_receiver_same_content_check_pings_while_hashing(tmp_path):
    import hashlib
    f = tmp_path / "x.bin"
    f.write_bytes(b"data")
    calls = []
    assert LT._same_content(f, 4, hashlib.sha256(b"data").hexdigest(), lambda n: calls.append(n))
    assert calls  # 해시를 읽는 동안 콜백(핑)이 불린다
