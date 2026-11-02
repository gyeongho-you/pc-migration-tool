"""stage73: 3차 재점검 지적의 재현 시험. 가짜 환경만 쓴다(임시 폴더, 가짜 robocopy, 가짜 API)."""
import json
import os

import pytest

from pcmigrate import api, drop_stage as ds, multi_transfer as mt
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SelectedItem
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.files_flow import norm, unsafe_data_folder
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


@pytest.fixture
def ctl(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api = DropData()
    c.listener = lambda k, p=None: None
    return c


# ---------------------------------------------------------------- place: 임시 보관 항목 상태 4조합
def place_env(tmp_path, real_status, part_status, suffix=True):
    env = Env(tmp_path, letters="D")
    put(env.recv / "D" / "Work" / "a.txt", b"a")
    put(env.recv / "D" / "Work" / "keep.tmp", b"k")
    key = "d" + BS + "work"
    real = {"src": "D:" + BS + "Work", "rel": "D" + BS + "Work", "status": real_status}
    part = {"src": "D:" + BS + "Work", "rel": "D" + BS + "Work", "status": part_status, "partial": True}
    items = {key: real, key + mt.STAGED_SUFFIX: part} if real_status else {key: part}
    env.journal(items)
    return env


@pytest.mark.parametrize("part_status", ["failed", "pending", "copied"])
def test_real_done_but_staged_not_done_is_unverified_and_nothing_is_removed(tmp_path, part_status):
    env = place_env(tmp_path, "done", part_status)
    r = env.run(apply=True, protected_fn=NOPROT).to_dict()
    assert r["skipped_count"] == 1 and contents(env.drives / "D") == {}  # 놓지 않음
    assert (env.recv / "D" / "Work" / "keep.tmp").read_bytes() == b"k"  # 받기 폴더의 파일은 그대로(지워지지 않음)
    r2 = env.run(apply=True, protected_fn=NOPROT, include_unverified=True).to_dict()  # 사용자가 확인했다고 하면 놓는다
    assert contents(env.drives / "D" / "Work") == {"a.txt": b"a", "keep.tmp": b"k"} and r2["skipped_count"] == 0


def test_both_done_places_once_and_counts_once(tmp_path):
    env = place_env(tmp_path, "done", "done")
    r = env.run(apply=True, protected_fn=NOPROT).to_dict()
    assert contents(env.drives / "D" / "Work") == {"a.txt": b"a", "keep.tmp": b"k"} and r["total_files"] == 2 and r["skipped_count"] == 0


def test_real_failed_and_staged_done_stays_unverified(tmp_path):
    env = place_env(tmp_path, "failed", "done")
    r = env.run(apply=True, protected_fn=NOPROT).to_dict()
    assert r["skipped_count"] == 1 and contents(env.drives / "D") == {}


def test_old_format_single_partial_entry_still_places(tmp_path):
    env = place_env(tmp_path, None, "done")  # 이전 형식: 접미 없는 partial 하나뿐
    r = env.run(apply=True, protected_fn=NOPROT).to_dict()
    assert contents(env.drives / "D" / "Work") == {"a.txt": b"a", "keep.tmp": b"k"} and r["skipped_count"] == 0


# ---------------------------------------------------------------- 홈이 드라이브 루트 아래
def test_home_under_drive_root_does_not_reject_sibling_folders():
    env = {"USERPROFILE": "D:" + BS + "oldpcuser"}
    assert not unsafe_data_folder("D:" + BS + "Projects", env)
    assert not unsafe_data_folder("D:" + BS + "Work" + BS + "A", env)
    assert unsafe_data_folder("D:" + BS + "oldpcuser", env)  # 홈 자체는 거부
    assert unsafe_data_folder("D:" + BS + "oldpcuser" + BS + "AppData", env)
    assert not unsafe_data_folder("D:" + BS + "oldpcuser" + BS + "Documents", env)
    assert unsafe_data_folder("D:" + BS, env)  # 드라이브 루트
    u = {"USERPROFILE": "C:" + BS + "Users" + BS + "oldpcuser"}  # stage71 동작 유지
    assert unsafe_data_folder("C:" + BS + "Users", u) and unsafe_data_folder("C:" + BS + "Users" + BS + "other", u)
    assert not unsafe_data_folder("C:" + BS + "Projects", u)


def test_home_under_drive_root_both_entry_points(ctl, tmp_path, monkeypatch):
    home = tmp_path / "oldpcuser"  # 부모(tmp_path)는 'users' 가 아니다: 형제 폴더 오탐 없음
    proj = tmp_path / "Projects"
    home.mkdir()
    proj.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    assert ctl.add_data_folder(str(proj)) == str(proj)  # 자동 찾기 쪽 '직접 폴더 추가'(옛 API)와
    ctl.files.extra.clear()
    ctl.files.selected.clear()
    assert "목록에 담았습니다" in ctl.add_drop_paths([str(proj)])  # 상자 양쪽
    with pytest.raises(GuardError):
        ctl.add_drop_paths([str(home)])


# ---------------------------------------------------------------- N3 skip_done 정상 재실행
def test_skip_done_rerun_is_still_a_complete_success(tmp_path):
    a = tmp_path / "a"
    put(a / "f.txt", b"1")
    out = tmp_path / "out"
    r1 = run_transfer_selection([SelectedItem(str(a), True)], out, deps=deps_with(FakeRobo()), keep_awake=False)
    assert r1.to_dict()["success"]
    r2 = run_transfer_selection([SelectedItem(str(a), True)], out, deps=deps_with(FakeRobo()), keep_awake=False, skip_done=True)
    d = r2.to_dict()
    assert [i.status for i in r2.items] == ["skipped_done"] and d["verification_complete"] is True and d["success"] is True


# ---------------------------------------------------------------- N4 기록 파일 정리
def test_clean_state_files_also_removes_staged_record_files(tmp_path):
    for n in (".pcmigrate-state.json", ".pcmigrate-preserve.json", mt.STAGED_STATE_NAME, mt.STAGED_PRESERVE_NAME, "data.txt"):
        put(tmp_path / n, b"x")
    removed = api.clean_state_files(tmp_path)
    assert mt.STAGED_STATE_NAME in removed and mt.STAGED_PRESERVE_NAME in removed
    assert (tmp_path / "data.txt").is_file() and not (tmp_path / mt.STAGED_STATE_NAME).exists()


# ---------------------------------------------------------------- N10 직접 추가한 파일의 상위 폴더
def test_report_next_to_a_dropped_file_is_not_written(ctl, tmp_path):
    d = tmp_path / "d1"
    put(d / "x.txt", b"1")
    ctl.add_drop_paths([str(d / "x.txt")])
    assert ctl._report_inside_sources(str(d / "r.json")) is True
    assert ctl._report_inside_sources(str(tmp_path / "elsewhere" / "r.json")) is False
    ctl._abort_report_for_guard(str(d / "r.json"), "시작할 수 없습니다")
    assert not (d / "r.json").exists()


# ---------------------------------------------------------------- N11 합계 이중 집계
def test_total_does_not_count_manual_items_already_inside_an_auto_folder(ctl, tmp_path):
    a = tmp_path / "A"
    (a / "sub").mkdir(parents=True)
    put(a / "f.txt", b"12345")
    ctl.files.extra.append(str(a))
    ctl.files.selected.add(norm(str(a)))
    base = ctl.data_total_text()
    ctl.add_drop_paths([str(a / "sub"), str(a / "f.txt")])  # 둘 다 이미 자동 폴더 안
    assert ctl.data_total_text() == base
    other = tmp_path / "B"
    put(other / "g.txt", b"1")
    ctl.add_drop_paths([str(other / "g.txt")])  # 밖에 있는 것은 센다
    assert " + 직접 추가한 1개" in ctl.data_total_text()
