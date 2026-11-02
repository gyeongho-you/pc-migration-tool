"""stage63 확인: 목록에 담은 뒤(또는 폴더를 체크한 뒤) 원래 위치에서 지우거나 옮기거나 잠가도 오류로 터지지 않고
'찾을 수 없다'로만 기록하고 나머지는 계속 보내는지. 공유 폴더/외장(MultiReport)·LAN·화면 컨트롤러 모두. 가짜 환경만 쓴다."""
import json
import os
import shutil

import pytest

from pcmigrate import drop_stage as ds, errorlog, lan_api, multi_transfer as mt
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SelectedItem
from pcmigrate.transfer import TransferError

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_lan_transfer import _fast_kdf, make_src, rx_factory, user_tree  # noqa: F401


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


def logged(tmp_path):
    from pcmigrate import errorlog as el
    d = el.log_dir()
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in d.glob("errors-*.log")) if d.exists() else ""


def tree3(tmp_path):
    base = tmp_path / "pc" / "Work"
    put(base / "A" / "a1.txt", b"1" * 10)
    put(base / "B" / "b1.txt", b"2" * 20)
    put(base / "keep.txt", b"3" * 5)
    put(base / "gone.txt", b"4" * 6)
    put(base / "moved.txt", b"5" * 7)
    return base


def run(items, extras, dst, **kw):
    return run_transfer_selection(items, dst, deps=deps_with(FakeRobo()), keep_awake=False, extra_files=extras, **kw)


# ---------------------------------------------------------------------------- ① 직접 고른 파일
def test_dropped_files_deleted_or_moved_after_listing_are_not_found_only(tmp_path):
    base = tree3(tmp_path)
    entries = [ds.describe(p) for p in (base / "keep.txt", base / "gone.txt", base / "moved.txt")]
    os.remove(base / "gone.txt")
    shutil.move(str(base / "moved.txt"), str(tmp_path / "elsewhere.txt"))
    rep = run([SelectedItem(str(base / "A"), True)], entries, tmp_path / "out")
    d = rep.to_dict()
    assert d["failed_count"] == 2 and not d["success"] and d["items_ok"] == 2  # 폴더 A + 남은 파일 1개는 계속 보냄
    reasons = [f["reason"] for f in d["drop_stage"]["failed"]]
    assert all("찾을 수 없습니다" in r and "Error" not in r and "WinError" not in r for r in reasons)
    assert (tmp_path / "out" / mt.dest_rel(str(base)) / "keep.txt").is_file()
    assert ds.staging_root().exists() is False or list(ds.staging_root().iterdir()) == []
    assert "gone.txt" in logged(tmp_path)  # 오류 로그에도 남는다


@pytest.mark.skipif(os.name != "nt", reason="Windows 파일 잠금")
def test_locked_dropped_file_is_unreadable_only_and_others_continue(tmp_path):
    import msvcrt
    base = tree3(tmp_path)
    entries = [ds.describe(base / "keep.txt"), ds.describe(base / "gone.txt")]
    plan = ds.plan_drops(entries, [])
    stage = ds.new_stage_dir()
    with open(base / "gone.txt", "r+b") as f:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 3)  # 앞 3바이트를 다른 핸들이 못 읽게 잠금
        try:
            ds.stage_files(plan, stage, no_link=True)
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 3)
    assert [os.path.basename(x[0]) for x in plan.files] == ["keep.txt"]
    assert len(plan.failed) == 1 and "읽을 수 없습니다" in plan.failed[0]["reason"] and "Error" not in plan.failed[0]["reason"]
    ds.remove_stage_dir(stage)


def test_all_dropped_files_vanished_and_no_folder_gives_notice_and_report(tmp_path):
    base = tree3(tmp_path)
    entries = [ds.describe(base / "gone.txt")]
    os.remove(base / "gone.txt")
    rpt = tmp_path / "r.json"
    with pytest.raises(TransferError, match="선택되지 않았|보낼"):
        run([], entries, tmp_path / "out", abort_report=rpt)
    data = json.loads(rpt.read_text(encoding="utf-8"))
    assert data["aborted"] and not data["success"] and data["drop_stage"]["failed"]


def test_lan_dropped_file_vanished_sends_the_rest(rx_factory, tmp_path):
    base = tree3(tmp_path)
    entries = [ds.describe(base / "keep.txt"), ds.describe(base / "gone.txt")]
    os.remove(base / "gone.txt")
    rx = rx_factory()
    rep = lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, selection=[SelectedItem(str(base / "A"), True)], options={"chunk": 4096}, extra_files=entries)
    assert rep["failed_count"] == 1 and not rep["success"]
    got = user_tree(tmp_path / "dst")
    r = mt.dest_rel(str(base)).replace("\\", "/")
    assert f"{r}/keep.txt" in got and any(k.endswith("A/a1.txt") for k in got)
    assert "찾을 수 없습니다" in rep["failed"][0]["error"]


# ---------------------------------------------------------------------------- ② 체크한 폴더
def test_checked_folder_deleted_before_transfer_only_that_folder_fails(tmp_path):
    base = tree3(tmp_path)
    items = [SelectedItem(str(base / "A"), True), SelectedItem(str(base / "B"), True)]
    shutil.rmtree(base / "B")
    for dry in (True, False):  # 미리보기 단계와 전송 단계 각각
        rep = run_transfer_selection(items, tmp_path / ("p" if dry else "out"), dry_run=dry, deps=deps_with(FakeRobo()), keep_awake=False)
        d = rep.to_dict()
        assert d["items_ok"] == 1 and d["failed_count"] == 1 and not d["success"]
        assert d["dropped"][0]["missing"] and "찾을 수 없어" in d["dropped"][0]["reason"]
        assert any("B" in x["path"] for x in d["dropped"])
    assert (tmp_path / "out" / mt.dest_rel(str(base / "A")) / "a1.txt").is_file()
    assert "multi-transfer missing folder" in logged(tmp_path)


def test_checked_folder_moved_lan_sends_the_rest(rx_factory, tmp_path):
    base = tree3(tmp_path)
    shutil.move(str(base / "B"), str(tmp_path / "B_moved"))
    rx = rx_factory()
    rep = lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, selection=[SelectedItem(str(base / "A"), True), SelectedItem(str(base / "B"), True)],
                           options={"chunk": 4096})
    assert rep["failed_count"] == 1 and not rep["success"] and "찾을 수 없어" in rep["failed"][0]["error"]
    assert any(k.endswith("A/a1.txt") for k in user_tree(tmp_path / "dst"))


# ---------------------------------------------------------------------------- ③ 전부 없어짐
def test_all_checked_folders_gone_gives_plain_notice_and_report(tmp_path):
    base = tree3(tmp_path)
    items = [SelectedItem(str(base / "A"), True), SelectedItem(str(base / "B"), True)]
    shutil.rmtree(base / "A")
    shutil.rmtree(base / "B")
    rpt = tmp_path / "r.json"
    with pytest.raises(TransferError, match="원본 폴더가 없습니다"):
        run_transfer_selection(items, tmp_path / "out", deps=deps_with(FakeRobo()), keep_awake=False, abort_report=rpt)
    data = json.loads(rpt.read_text(encoding="utf-8"))
    assert data["aborted"] and not data["success"] and "원본 폴더가 없습니다" in data["reason"]
    assert not (tmp_path / "out").exists()


def test_all_gone_lan_gives_error_not_traceback(rx_factory, tmp_path):
    from pcmigrate.lan_protocol import LanError
    base = tree3(tmp_path)
    shutil.rmtree(base / "A")
    rx = rx_factory()
    with pytest.raises(LanError) as ei:
        lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, selection=[SelectedItem(str(base / "A"), True)], options={"chunk": 4096})
    assert "원본 폴더가 없습니다" in str(ei.value)


# ---------------------------------------------------------------------------- 화면 컨트롤러
def test_controller_partial_missing_proceeds_and_all_missing_blocks_with_report(tmp_path):
    from pcmigrate.gui import texts as T
    from pcmigrate.gui.controller import Controller, GuardError
    from pcmigrate.gui.runner import Runner
    from test_gui_controller import FakeApi
    from test_gui_drop_controller import DropData
    base = tree3(tmp_path)
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api = DropData()
    events = []
    c.listener = lambda k, p=None: events.append((k, p))
    c.add_data_folder(str(base / "A"))
    c.add_data_folder(str(base / "B"))
    c.set_method(T.MV_EXTERNAL)
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    c.state.transfer_dst = str(tmp_path / "out")
    shutil.rmtree(base / "B")
    check = c.check_transfer_paths(c.state.transfer_src, c.state.transfer_dst)
    assert check.ok and any("1곳을 찾을 수 없어 제외하고 진행" in t for _ok, t in check.lines)
    c.start_transfer(dry_run=True)  # 예외 없이 미리보기
    assert not [p for k, p in events if k == "error"]
    shutil.rmtree(base / "A")
    rpt = tmp_path / "blocked.json"
    with pytest.raises(GuardError, match="옮길 폴더를 찾을 수 없습니다"):
        c.start_transfer(dry_run=True, report_path=str(rpt))
    d = json.loads(rpt.read_text(encoding="utf-8"))
    assert d["aborted"] and not d["success"] and "찾을 수 없습니다" in d["reason"]
