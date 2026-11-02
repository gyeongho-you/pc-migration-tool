"""stage71: 재점검 지적(N1~N6, M4, R1, R3)의 재현 시험. 가짜 환경만 쓴다(임시 폴더, 가짜 robocopy, 가짜 API)."""
import json
import os
import time

import pytest

from pcmigrate import cli, drop_stage as ds, multi_transfer as mt
from pcmigrate.copier import Report
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SelectedItem
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.files_flow import norm, unsafe_data_folder
from pcmigrate.gui.runner import Runner

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_gui_controller import FakeApi
from test_gui_drop_controller import DropData


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


@pytest.fixture
def ctl(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api = DropData()
    c.events = []
    c.listener = lambda k, p=None: c.events.append((k, p))
    return c


# ---------------------------------------------------------------- N1 성능
def test_drop_notes_and_rows_are_fast_with_500_selected_folders(ctl, tmp_path):
    f = ctl.files
    base = tmp_path / "many"
    for i in range(500):
        p = str(base / f"f{i:03d}")
        f.extra.append(p)
        f.selected.add(norm(p))
    for name in ("d1", "d2", "d3"):
        f.drops.append(ds.DropEntry(str(base / name), True, 0, 0, files=0, unknown=True))
    f.drops.append(ds.DropEntry(str(base / "f007" / "x.txt"), False, 5, 1))
    t0 = time.perf_counter()
    for _ in range(5):  # 화면 갱신(files_changed)마다 부르는 경로
        notes = ctl.drop_notes()
        rows = ctl.drop_rows()
    dt = (time.perf_counter() - t0) / 5
    assert dt < 0.2, dt
    assert notes == [T.DP_OVERLAP.format(n=1)] and rows[-1][4]  # 파일 하나가 체크 폴더 안


def test_folder_cover_skips_comparison_without_manual_folders(ctl, tmp_path):
    for i in range(50):
        p = str(tmp_path / f"a{i}")
        ctl.files.extra.append(p)
        ctl.files.selected.add(norm(p))
    assert ctl.folder_cover() == {} and ctl.drop_notes() == []


# ---------------------------------------------------------------- N2/N3 리포트 쓰기 조건
def single_flow(ctl, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    ctl.state.transfer_src, ctl.state.transfer_dst = str(src), str(src / "out")  # 대상이 원본 안(점검 실패)
    return src


def test_n2_report_inside_source_is_never_written_single_and_selection(ctl, tmp_path):
    src = single_flow(ctl, tmp_path)
    rpt = src / "r.json"
    with pytest.raises(GuardError):
        ctl.start_transfer(dry_run=True, report_path=str(rpt))
    assert not rpt.exists()
    # 선택 전송: 대상이 선택 폴더 안 + 리포트도 그 안
    a = tmp_path / "a"
    a.mkdir()
    ctl.add_data_folder(str(a))
    ctl.set_method(T.MV_EXTERNAL)
    ctl.enter_selection_transfer(T.MODE_EXTERNAL)
    ctl.state.transfer_dst = str(a / "out")
    rpt2 = a / "r2.json"
    with pytest.raises(GuardError):
        ctl.start_transfer(dry_run=True, report_path=str(rpt2))
    assert not rpt2.exists()
    # 직접 추가한 폴더 안도 같다
    ctl.clear_drops()
    d = tmp_path / "dd"
    d.mkdir()
    ctl.add_drop_paths([str(d)])
    ctl.set_data_selected(str(a), False)
    ctl.enter_selection_transfer(T.MODE_EXTERNAL)
    ctl.state.transfer_dst = str(d / "out")
    rpt3 = d / "r3.json"
    with pytest.raises(GuardError):
        ctl.start_transfer(dry_run=True, report_path=str(rpt3))
    assert not rpt3.exists()


def test_n3_plain_confirmation_guards_keep_the_previous_report(ctl, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    ctl.state.transfer_src, ctl.state.transfer_dst = str(src), str(tmp_path / "dst")
    rpt = tmp_path / "keep.json"
    rpt.write_text('{"success": true, "mine": 1}', encoding="utf-8")
    for kw in (dict(write_confirmed=False), dict(write_confirmed=True)):  # 확인 없음 / 미리보기 필요
        with pytest.raises(GuardError):
            ctl.start_transfer(dry_run=False, report_path=str(rpt), **kw)
        assert json.loads(rpt.read_text(encoding="utf-8")) == {"success": True, "mine": 1}
    ctl.state.transfer_src = str(tmp_path / "없는 폴더")  # 실제로 시작하지 못한 경우만 중단 리포트(기존 것은 번호 .bak 으로 보관)
    with pytest.raises(GuardError):
        ctl.start_transfer(dry_run=True, report_path=str(rpt))
    assert json.loads(rpt.read_text(encoding="utf-8"))["aborted"] is True
    assert any(p.name.startswith("keep.json.bak") for p in tmp_path.iterdir())


# ---------------------------------------------------------------- N5, N6
def test_n5_drop_change_ends_selection_mode_completely(ctl, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    put(a / "f.txt", b"1")
    ctl.add_data_folder(str(a))
    ctl.set_method(T.MV_EXTERNAL)
    ctl.enter_selection_transfer(T.MODE_EXTERNAL)
    assert ctl.state.transfer_src == ctl.files.signature and ctl.files.use_selection
    ctl.state.transfer_report = object()
    ctl.add_drop_paths([str(a / "f.txt")])
    assert not ctl.files.use_selection and ctl.state.transfer_src == "" and ctl.state.transfer_report is None


def test_n6_same_folder_is_not_counted_twice(ctl, tmp_path):
    d = tmp_path / "dd"
    d.mkdir()
    ctl.files.extra.append(str(d))  # 옛 API 로 넣은 직접 추가 폴더
    ctl.files.selected.add(norm(str(d)))
    msg = ctl.add_drop_paths([str(d)])
    assert T.DP_ALREADY.format(n=1) in msg and ctl.drop_total()[0] == 0
    ctl2_dir = tmp_path / "e"
    ctl2_dir.mkdir()
    ctl.add_drop_paths([str(ctl2_dir)])
    with pytest.raises(GuardError, match="이미 목록"):
        ctl.add_data_folder(str(ctl2_dir))


def test_n6_totals_do_not_double_count_auto_folder_inside_manual_folder(ctl, tmp_path):
    outer, inner = tmp_path / "outer", tmp_path / "outer" / "in"
    inner.mkdir(parents=True)
    ctl.files.extra.append(str(inner))
    ctl.files.selected.add(norm(str(inner)))
    before = ctl.data_total_text()
    ctl.add_drop_paths([str(outer)])
    text = ctl.data_total_text()
    assert "선택한 폴더 없이 직접 추가한 1개" in text and before != text  # 안쪽 자동 폴더는 합계에서 빠진다


# ---------------------------------------------------------------- M4 잔여
def test_m4_users_folder_other_homes_and_appdata_root_are_refused():
    env = {"USERPROFILE": "C:\\Users\\oldpcuser"}
    assert unsafe_data_folder("C:\\Users", env)
    assert unsafe_data_folder("C:\\Users\\other", env)
    assert unsafe_data_folder("C:\\Users\\oldpcuser", env)
    assert unsafe_data_folder("C:\\Users\\oldpcuser\\AppData", env)
    assert unsafe_data_folder("C:\\Users\\other\\AppData", env)
    assert not unsafe_data_folder("C:\\Users\\oldpcuser\\AppData\\Roaming", env)
    assert not unsafe_data_folder("C:\\Users\\oldpcuser\\Documents", env)
    assert not unsafe_data_folder("D:\\Work\\Projects", env)


# ---------------------------------------------------------------- R1 임시 보관 항목의 저널/상태 분리
def rescue_run(tmp_path, skip_done=False):
    src = tmp_path / "pc" / "Proj"
    put(src / "a.txt", b"A")
    put(src / "keep.tmp", b"wanted")
    stage_root = tmp_path / "stage"
    stage_root.mkdir(exist_ok=True)
    return src, run_transfer_selection([SelectedItem(str(src), True)], tmp_path / "dst", deps=deps_with(FakeRobo()), keep_awake=False,
                                       exclude_junk=True, extra_files=[str(src / "keep.tmp")], skip_done=skip_done,
                                       drop_kwargs={"root": stage_root})


def test_r1_staged_item_has_its_own_journal_key_and_state_file(tmp_path):
    src, rep = rescue_run(tmp_path)
    assert rep.to_dict()["success"]
    rel = mt.dest_rel(str(src))
    j = json.loads((tmp_path / "dst" / mt.JOURNAL_NAME).read_text(encoding="utf-8"))["items"]
    assert rel.lower() in j and rel.lower() + mt.STAGED_SUFFIX in j  # 키가 분리됨
    assert not j[rel.lower()].get("partial") and j[rel.lower() + mt.STAGED_SUFFIX]["partial"] is True
    d = tmp_path / "dst" / rel
    assert (d / ".pcmigrate-state.json").is_file() and (d / mt.STAGED_STATE_NAME).is_file()  # 상태 파일도 분리
    first = lambda p: json.loads((d / p).read_text(encoding="utf-8").splitlines()[0])  # noqa: E731
    assert first(".pcmigrate-state.json") != first(mt.STAGED_STATE_NAME)
    assert (d / "keep.tmp").read_bytes() == b"wanted" and (d / "a.txt").is_file()
    # 2회차(skip_done): 실제 항목은 건너뛰고(끝난 기록이 있으므로) 직접 고른 파일은 다시 확인하며 둘 다 누락 없음
    _s, rep2 = rescue_run(tmp_path, skip_done=True)
    assert (d / "keep.tmp").is_file() and (d / "a.txt").is_file()
    assert any(i.status == "skipped_done" for i in rep2.items)  # 실제 폴더 항목은 끝난 기록이 있어 건너뜀


def test_r1_real_failed_item_is_not_marked_done_by_staged_success(tmp_path):
    a = tmp_path / "a"
    put(a / "f.txt", b"1")
    staged = tmp_path / "stg" / "x"
    staged.mkdir(parents=True)

    def fake(src, dst, **kw):
        from pcmigrate.copier import FileResult
        if src == a:  # 실제 폴더 항목은 실패
            return Report(dry_run=False, results=[FileResult("s", "d", "failed", error="x")], meta={"verification_complete": False})
        return Report(dry_run=False, results=[], meta={"verification_complete": True})
    items = [SelectedItem(str(a), True), ds.StagedItem(str(staged), True, orig_dir=str(a))]
    mt.run_multi(items, tmp_path / "out", fake, dest_info_fn=lambda p: type("D", (), {"kind": "fixed", "fs": "NTFS", "free": 10 ** 12})())
    j = json.loads((tmp_path / "out" / mt.JOURNAL_NAME).read_text(encoding="utf-8"))["items"]
    rel = mt.dest_rel(str(a)).lower()
    assert j[rel]["status"] == "failed" and j[rel + mt.STAGED_SUFFIX]["status"] == "done"  # 실제 폴더의 실패가 완료로 덮이지 않는다


def test_r1_place_reads_both_keys_and_places_once(tmp_path):
    from test_place_original import Env, contents
    env = Env(tmp_path, letters="D")
    put(env.recv / "D" / "Work" / "a.txt", b"a")
    put(env.recv / "D" / "Work" / "keep.tmp", b"k")
    j = {"d\\work": {"src": "D:\\Work", "rel": "D\\Work", "status": "failed"},
         "d\\work" + mt.STAGED_SUFFIX: {"src": "D:\\Work", "rel": "D\\Work", "status": "done", "partial": True}}
    env.journal(j)
    r = env.run(apply=True, protected_fn=lambda p, **k: None).to_dict()
    assert contents(env.drives / "D") == {} and r["skipped_count"] == 1  # 실제 항목이 failed 면 임시 보관 항목이 done 이어도 놓지 않는다
    j["d\\work"]["status"] = "done"
    env.journal(j)
    r = env.run(apply=True, protected_fn=lambda p, **k: None).to_dict()
    assert contents(env.drives / "D" / "Work") == {"a.txt": b"a", "keep.tmp": b"k"} and r["total_files"] == 2 and r["skipped_count"] == 0


# ---------------------------------------------------------------- R3 CLI: 기존 정상 리포트는 번호 .bak 으로 보관
def test_r3_cli_abort_report_keeps_previous_report_as_numbered_bak(tmp_path):
    rpt = tmp_path / "r.json"
    rpt.write_text('{"success": true, "old": 1}', encoding="utf-8")
    cli.main(["transfer", str(tmp_path / "없는원본"), str(tmp_path / "dst"), "--report", str(rpt)])
    assert json.loads(rpt.read_text(encoding="utf-8"))["aborted"] is True
    baks = [p for p in tmp_path.iterdir() if p.name.startswith("r.json.bak")]
    assert baks and json.loads(baks[0].read_text(encoding="utf-8")) == {"success": True, "old": 1}
