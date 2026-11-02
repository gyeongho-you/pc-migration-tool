"""stage75: 4차 재점검 지적의 재현 시험. 가짜 환경만 쓴다(임시 폴더, 가짜 robocopy, 가짜 API, 가짜 홈 oldpcuser)."""
import json
import os
from types import SimpleNamespace

import pytest

from pcmigrate import api, cli, multi_transfer as mt
from pcmigrate.copier import Report
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SelectedItem
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller
from pcmigrate.gui.runner import Runner

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_gui_controller import FakeApi
from test_gui_drop_controller import DropData
from test_place_original import Env, contents

NOPROT = lambda p, **k: None  # noqa: E731
BS = "\\"


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


def sel_run(src, dst, **kw):
    return run_transfer_selection([SelectedItem(str(src), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False, skip_done=True, **kw)


# ---------------------------------------------------------------- N5 skip_done 대조
def make_done(tmp_path):
    src = tmp_path / "pc" / "Proj"
    put(src / "a.txt", b"A")
    put(src / "sub" / "b.txt", b"BB")
    dst = tmp_path / "dst"
    r1 = sel_run(src, dst)
    assert r1.to_dict()["success"]
    return src, dst, mt.dest_rel(str(src))


def test_n5_dest_file_deleted_is_reprocessed_not_skipped(tmp_path):
    src, dst, rel = make_done(tmp_path)
    (dst / rel / "a.txt").unlink()
    r2 = sel_run(src, dst)
    assert [i.status for i in r2.items] != ["skipped_done"]
    assert (dst / rel / "a.txt").read_bytes() == b"A" and r2.to_dict()["success"]


def test_n5_source_file_added_is_reprocessed_not_skipped(tmp_path):
    src, dst, rel = make_done(tmp_path)
    put(src / "new.txt", b"NEW")
    r2 = sel_run(src, dst)
    assert [i.status for i in r2.items] != ["skipped_done"]
    assert (dst / rel / "new.txt").read_bytes() == b"NEW"


def test_n5_unchanged_rerun_still_skips_and_says_it_trusted_the_old_record(tmp_path):
    src, dst, rel = make_done(tmp_path)
    r2 = sel_run(src, dst)
    d = r2.to_dict()
    assert [i.status for i in r2.items] == ["skipped_done"] and d["success"] and d["verification_complete"]
    text = "\n".join(mt.summary_lines(r2, str(dst)))
    assert "이전 실행의 검사 기록을 믿" in text and "이번에는 검사하지 않" in text
    assert "다음 할 일: 검사까지 통과했습니다" not in text  # 건너뛴 폴더를 이번에 검사했다고 말하지 않는다


def test_n5_dest_removed_entirely_and_old_journal_without_stamp_are_reprocessed(tmp_path):
    import shutil
    src, dst, rel = make_done(tmp_path)
    shutil.rmtree(dst / rel)
    assert [i.status for i in sel_run(src, dst).items] != ["skipped_done"] and (dst / rel / "a.txt").is_file()
    jp = dst / mt.JOURNAL_NAME  # 원본 표식이 없는 옛 기록: 확인할 수 없으니 다시 처리
    j = json.loads(jp.read_text(encoding="utf-8"))
    for v in j["items"].values():
        v.pop("src_stamp", None)
    jp.write_text(json.dumps(j), encoding="utf-8")
    assert [i.status for i in sel_run(src, dst).items] != ["skipped_done"]


# ---------------------------------------------------------------- N12 리포트 위치(직접 추가한 파일)
@pytest.fixture
def ctl(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api = DropData()
    c.listener = lambda k, p=None: None
    return c


def test_n12_dropped_file_blocks_only_its_own_folder_level_not_the_whole_subtree(ctl, tmp_path):
    desk = tmp_path / "Desktop"
    put(desk / "x.txt", b"1")
    (desk / "이전 결과").mkdir()
    ctl.add_drop_paths([str(desk / "x.txt")])
    assert ctl._report_inside_sources(str(desk / "이전 결과" / "r.json")) is False  # 하위 폴더는 허용(저장됨)
    assert ctl._report_inside_sources(str(desk / "r.json")) is True  # 파일 직속은 거부
    ctl._abort_report_for_guard(str(desk / "이전 결과" / "r.json"), "시작할 수 없습니다")
    assert json.loads((desk / "이전 결과" / "r.json").read_text(encoding="utf-8"))["aborted"] is True


def test_n12_folder_sources_still_block_the_whole_subtree(ctl, tmp_path):
    a = tmp_path / "A"
    put(a / "f.txt", b"1")
    ctl.add_drop_paths([str(a)])  # 폴더 원본
    ctl.set_method(T.MV_EXTERNAL)
    ctl.enter_selection_transfer(T.MODE_EXTERNAL)
    assert ctl._report_inside_sources(str(a / "deep" / "er" / "r.json")) is True
    assert ctl._report_inside_sources(str(tmp_path / "elsewhere" / "r.json")) is False


# ---------------------------------------------------------------- N6 낡은 #staged 항목
def test_n6_old_unfinished_staged_entry_becomes_stale_and_place_ignores_it(tmp_path):
    src = tmp_path / "pc" / "Proj"
    put(src / "a.txt", b"A")
    dst = tmp_path / "dst"
    rel = mt.dest_rel(str(src))
    dst.mkdir()
    old = {"src": str(src), "rel": rel, "status": "failed", "partial": True}
    (dst / mt.JOURNAL_NAME).write_text(json.dumps({"version": 1, "items": {rel.lower() + mt.STAGED_SUFFIX: old}}), encoding="utf-8")
    r = run_transfer_selection([SelectedItem(str(src), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False)  # 이번에는 파일 없이 폴더만
    assert r.to_dict()["success"]
    j = json.loads((dst / mt.JOURNAL_NAME).read_text(encoding="utf-8"))["items"]
    assert j[rel.lower() + mt.STAGED_SUFFIX]["status"] == "stale" and j[rel.lower()]["status"] == "done"
    # place: 실제 항목 done + 낡은(stale) 임시 보관 항목 -> 미검증으로 막지 않고 놓는다
    env = Env(tmp_path / "pl", letters="D") if (tmp_path / "pl").mkdir() is None else None
    put(env.recv / "D" / "Work" / "a.txt", b"a")
    key = "d" + BS + "work"
    env.journal({key: {"src": "D:" + BS + "Work", "rel": "D" + BS + "Work", "status": "done"},
                 key + mt.STAGED_SUFFIX: {"src": "D:" + BS + "Work", "rel": "D" + BS + "Work", "status": "stale", "partial": True}})
    out = env.run(apply=True, protected_fn=NOPROT).to_dict()
    assert contents(env.drives / "D" / "Work") == {"a.txt": b"a"} and out["skipped_count"] == 0
    # 대조: 끝나지 못한 임시 보관 항목(failed)은 여전히 막는다(stage73 동작 유지)
    env2 = Env(tmp_path / "pl2", letters="D") if (tmp_path / "pl2").mkdir() is None else None
    put(env2.recv / "D" / "Work" / "a.txt", b"a")
    env2.journal({key: {"src": "D:" + BS + "Work", "rel": "D" + BS + "Work", "status": "done"},
                  key + mt.STAGED_SUFFIX: {"src": "D:" + BS + "Work", "rel": "D" + BS + "Work", "status": "failed", "partial": True}})
    assert env2.run(apply=True, protected_fn=NOPROT).to_dict()["skipped_count"] == 1


# ---------------------------------------------------------------- 홈 경로 치환
def fake_home_path():
    return os.path.join(os.environ["USERPROFILE"], "docs", "x.cfg")


def test_home_path_is_replaced_in_api_cli_and_gui_for_interrupted_import(tmp_path, capsys):
    msg = f"[WinError 5] 액세스가 거부되었습니다: '{fake_home_path()}'"
    e = PermissionError(msg)
    e.partial_report = Report(dry_run=False, results=[])
    api._save_partial_import(e, str(tmp_path / "pkg.zip"), SimpleNamespace(programs=[], items=[], manifest={}), None, None, 0, str(tmp_path / "rec"))
    saved = e.partial_report.meta["interrupted_error"]
    assert "oldpcuser" not in saved and os.environ["USERPROFILE"] not in saved and "%USERPROFILE%" in saved
    # CLI JSON
    args = SimpleNamespace(json=True, report=None)
    part = SimpleNamespace(record_path="", record_error="", report=Report(dry_run=False, results=[]), plan=SimpleNamespace(programs=[]))
    cli._import_interrupted(args, PermissionError(msg), part)
    out = capsys.readouterr().out
    assert os.environ["USERPROFILE"] not in out and "%USERPROFILE%" in json.loads(out)["error"]
    # GUI: 옛 값이 그대로 저장돼 있어도 화면 문구에는 치환
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.state.import_result = SimpleNamespace(mode="interrupted", report=SimpleNamespace(meta={"interrupted_error": msg}))
    note = c.interrupted_note()
    assert os.environ["USERPROFILE"] not in note and "%USERPROFILE%" in note
