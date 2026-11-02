"""stage69: 기능 리뷰어 3(파일 찾기·형식 설정·GUI 흐름) 지적 수정의 재현 시험.

각 시험은 수정 전에는 실패하도록 지적된 상황을 그대로 재현한다(H1 낡은 선택 목록, M1 기록 모드 busy, M2 받기 중 창 닫기,
M3 복사 오류 리포트, M4 시스템 폴더 직접 추가, L1~L5). 임시 폴더/가짜 환경만 쓴다(실제 드라이브·AppData 는 읽거나 쓰지 않는다).
"""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_rules_dir  # noqa: F401
from pcmigrate import cli, errorlog, find_data as fd, find_exts as fx
from pcmigrate.cancel import Cancelled
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui import files_flow
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi
from test_gui_my_files_controller import FakeData, FakeLan

ALPHA, BETA = "C:\\Proj\\Alpha", "C:\\Proj\\Beta"


@pytest.fixture
def env(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = FakeData(), FakeLan(tmp_path)
    c.find_data_start()
    return c


def send_paths(c):
    return [i.path for i in c.lan_send_items()]


# ------------------------------------------------------------------ H1 낡은 선택 목록
def go_copy_screen_then_back(c):
    c.set_data_selected(ALPHA, True)
    c.enter_selection_transfer(T.MODE_EXTERNAL)  # 외장 복사 화면으로(고른 목록 스냅샷)
    assert c.files.use_selection and [i.path for i in c.files.transfer_items] == [ALPHA]
    c.go(S.FIND)  # 찾기 화면으로 돌아옴(선택 모드는 유지되던 곳)


def test_h1_checkbox_change_after_copy_screen_sends_current_checks(env):
    c = env
    go_copy_screen_then_back(c)
    c.set_data_selected(ALPHA, False)
    c.set_data_selected(BETA, True)
    assert send_paths(c) == [BETA]  # 화면 체크(Beta)와 같은 폴더가 가야 한다(수정 전: Alpha)
    assert not c.files.use_selection  # 선택 모드는 끝났다


def test_h1_select_all_visible_and_clear(env):
    c = env
    go_copy_screen_then_back(c)
    c.select_visible_data(True)
    assert set(send_paths(c)) == {ALPHA, BETA, "C:\\Users\\someone\\Documents"}
    c.select_visible_data(False)
    assert send_paths(c) == []


def test_h1_add_folder_junk_toggle_and_new_find_end_selection_mode(env, tmp_path):
    c = env
    go_copy_screen_then_back(c)
    extra = tmp_path / "Gamma"
    extra.mkdir()
    c.add_data_folder(str(extra))
    assert not c.files.use_selection and str(extra) in send_paths(c) and ALPHA in send_paths(c)
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    c.go(S.FIND)
    c.set_data_junk(True)
    assert not c.files.use_selection  # 정크 제외 토글도 보낼 내용을 바꾼다
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    c.go(S.FIND)
    c.find_data_start()  # 다시 찾으면 체크가 재계산된다
    assert not c.files.use_selection


def test_h1_unchanged_checks_keep_the_snapshot(env):
    c = env
    go_copy_screen_then_back(c)
    c.set_data_selected(ALPHA, True)  # 이미 켜진 것을 또 켬: 바뀐 것이 없다
    assert c.files.use_selection and send_paths(c) == [ALPHA]


def test_h1_lan_send_items_never_prefers_stale_snapshot(env):
    c = env
    go_copy_screen_then_back(c)
    c.files.selected = {os.path.normcase(os.path.abspath(BETA)).rstrip("\\/")}  # 어떤 경로로든 체크가 바뀌었는데 모드가 남아 있는 경우
    assert c.files.use_selection  # (일부러 모드를 끝내지 않은 상태)
    assert send_paths(c) == [BETA]


# ------------------------------------------------------------------ M1 기록 모드 busy / 낡은 결과
def make_record():
    from test_gui_record_controller import FakeRecordApi
    return FakeRecordApi()


def test_m1_record_guards_while_busy(tmp_path):
    api = make_record()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.record_pick(0)
    c.record_find_folders()
    tok = c.record_folder_rows()[0]["token"]
    c.state.busy = True
    with pytest.raises(GuardError) as e:
        c.record_pick(1)
    assert T.G_BUSY in str(e.value)
    with pytest.raises(GuardError):
        c.record_toggle_folder(tok, False)
    with pytest.raises(GuardError):
        c.record_add_folder(str(tmp_path))
    assert c.record_state.program.name == "Foo Edit" and c.record_folder_rows()[0]["on"]
    c.state.busy = False
    c.record_toggle_folder(tok, False)  # 작업이 끝나면 다시 된다
    assert not c.record_folder_rows()[0]["on"]


def test_m1_stale_infer_result_is_not_applied_after_program_changed(tmp_path):
    api = make_record()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.record_pick(0)
    orig = api.run_record_infer

    def infer_then_switch(program, exe_path="", **kw):
        res = orig(program, exe_path, **kw)
        c.record_state.program = c.record_state.programs[1].program  # 작업 도중 프로그램이 바뀐 상황
        return res
    api.run_record_infer = infer_then_switch
    c.record_find_folders()
    assert c.record_state.plan is None  # 낡은 계획이 다른 프로그램에 붙지 않는다


# ------------------------------------------------------------------ M3 복사 오류/가드 실패 리포트
def prepared_single_transfer(tmp_path):
    api = FakeApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    return c, api


def test_m3_error_during_copy_still_saves_report(tmp_path):
    c, api = prepared_single_transfer(tmp_path)
    c.start_transfer(dry_run=True)
    rep = tmp_path / "out" / "report.json"
    rep.parent.mkdir()
    api.transfer_error = OSError("disk exploded at C:\\Users\\oldpcuser\\x")
    c.start_transfer(dry_run=False, write_confirmed=True, report_path=str(rep))
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["aborted"] is True and data["success"] is False and "OSError" in data["reason"]


def test_m3_cancelled_copy_saves_report(tmp_path):
    c, api = prepared_single_transfer(tmp_path)
    c.start_transfer(dry_run=True)
    rep = tmp_path / "report.json"
    api.transfer_error = Cancelled()
    c.start_transfer(dry_run=False, write_confirmed=True, report_path=str(rep))
    assert json.loads(rep.read_text(encoding="utf-8"))["reason"] == "취소했습니다"


def test_m3_guard_failure_saves_report_and_keeps_old_one(tmp_path):
    c, api = prepared_single_transfer(tmp_path)
    rep = tmp_path / "report.json"
    rep.write_text("previous result", encoding="utf-8")
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False, write_confirmed=True, report_path=str(rep))  # 미리보기 없이 시작: 가드 실패
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["aborted"] and "미리보기" in data["reason"]
    assert (tmp_path / "report.json.bak").read_text(encoding="utf-8") == "previous result"  # 이전 리포트는 보관


def test_m3_no_report_for_dry_run_busy_or_inside_source(tmp_path):
    c, api = prepared_single_transfer(tmp_path)
    rep = tmp_path / "report.json"
    c.start_transfer(dry_run=True, report_path=str(rep))  # 미리보기는 리포트를 쓰지 않는다(기존 동작)
    assert not rep.exists() or json.loads(rep.read_text(encoding="utf-8")).get("aborted") is not True
    c.state.busy = True
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False, write_confirmed=True, report_path=str(tmp_path / "busy.json"))
    assert not (tmp_path / "busy.json").exists()
    c.state.busy = False
    inside = Path(c.state.transfer_src) / "r.json"
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False, write_confirmed=True, report_path=str(inside))
    assert not inside.exists()  # 원본 폴더 안에는 쓰지 않는다


# ------------------------------------------------------------------ M4 시스템 폴더 직접 추가
@pytest.fixture
def fake_sys_env(monkeypatch):
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\oldpcuser")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
    monkeypatch.setenv("ProgramData", "C:\\ProgramData")


@pytest.mark.parametrize("path", ["C:\\", "D:\\", "C:\\Windows", "C:\\Windows\\System32", "C:\\Program Files", "C:\\Program Files\\App",
                                  "C:\\ProgramData", "D:\\Windows", "D:\\Program Files (x86)", "C:\\Users\\oldpcuser", "c:\\users\\OLDPCUSER\\"])
def test_m4_unsafe_folders_detected(path, fake_sys_env):
    assert files_flow.unsafe_data_folder(path)


@pytest.mark.parametrize("path", ["C:\\Proj\\Alpha", "D:\\Projects", "C:\\Users\\oldpcuser\\Documents", "C:\\Users\\oldpcuser\\Desktop\\도면", "E:\\프로젝트\\Windows앱"])
def test_m4_normal_folders_allowed(path, fake_sys_env):
    assert not files_flow.unsafe_data_folder(path)


def test_m4_add_data_folder_refuses_unsafe_and_drop_does_not_count_it(env, tmp_path, monkeypatch, fake_sys_env):
    c = env
    monkeypatch.setattr("pcmigrate.gui.files_controller.unsafe_data_folder", lambda p: os.path.abspath(p) == os.path.abspath(str(tmp_path / "SystemLike")))
    sysdir, okdir = tmp_path / "SystemLike", tmp_path / "Mine"
    sysdir.mkdir()
    okdir.mkdir()
    with pytest.raises(GuardError) as e:
        c.add_data_folder(str(sysdir))
    assert str(e.value) == T.G_FD_SYSTEM_FOLDER and str(sysdir) not in c.files.extra and not c.files.selected
    msg = c.add_drop_paths([str(sysdir), str(okdir)])
    assert "폴더 1곳" in msg and "읽을 수 없는 항목 1개" in msg  # 시스템 폴더는 '옮겼습니다' 로 세지 않는다
    assert c.files.extra == [str(okdir)]


def test_m4_real_drive_root_refused(env):
    with pytest.raises(GuardError):
        env.add_data_folder(os.path.abspath(os.sep))


# ------------------------------------------------------------------ L1 save 보관본이 초기화 보관본을 덮지 않는다
def test_l1_save_reset_save_save_keeps_original_backup(tmp_path):
    d = tmp_path / "settings"
    fx.save(fx.ExtConfig({".orig": "doc"}, []), d)
    original = (d / fx.FILE_NAME).read_text(encoding="utf-8")
    assert fx.reset(d).ok  # 원래 설정은 번호 .bak 으로
    fx.save(fx.ExtConfig({".a": "doc"}, []), d)
    fx.save(fx.ExtConfig({".b": "doc"}, []), d)
    baks = [p for p in d.glob(fx.FILE_NAME + ".bak*")]
    assert any(p.read_text(encoding="utf-8") == original for p in baks)  # 수정 전: 두 번째 save 가 .bak 을 덮어써 사라짐
    prev = d / (fx.FILE_NAME + fx.PREV_SUFFIX)
    assert json.loads(prev.read_text(encoding="utf-8"))["added"] == {".a": "doc"}  # 직전 설정 한 벌은 따로


# ------------------------------------------------------------------ L2 오류 로그 속도 제한
def test_l2_rate_limited_error_does_not_claim_it_was_logged(tmp_path):
    errorlog.reset_throttle()
    d = tmp_path / "logs"
    paths = []
    for i in range(errorlog.BURST_MAX + 5):
        paths.append(errorlog.record(ValueError(f"e{i}"), f"ctx{i}", tb="tb", directory=d))
    assert all(p is not None for p in paths[:errorlog.BURST_MAX])
    assert all(p is None for p in paths[errorlog.BURST_MAX:])  # 수정 전: 기록되지 않은 오류에도 경로를 돌려줌
    text = next(d.glob("errors-*.log")).read_text(encoding="utf-8")
    assert text.count("예외:") == errorlog.BURST_MAX and text.count("속도 제한") == 1  # 제한 시작 알림은 한 줄만
    # 시간이 지나 다시 기록할 수 있게 되면 '생략 N건' 이 남는다
    later = errorlog.datetime.now() + errorlog.timedelta(seconds=errorlog.BURST_SECONDS + 1)
    assert errorlog.record(ValueError("again"), "after", tb="tb", directory=d, now=later) is not None
    text = next(d.glob("errors-*.log")).read_text(encoding="utf-8")
    assert "오류 5건의 기록을 생략했습니다" in text


# ------------------------------------------------------------------ L3 find-data 종료 코드
def make_tree(tmp_path):
    root = tmp_path / "pc" / "W"
    root.mkdir(parents=True)
    (root / "a.dwg").write_bytes(b"x")
    return tmp_path / "pc"


def test_l3_missing_root_warns_and_exits_1(tmp_path, capsys):
    root = make_tree(tmp_path)
    code = cli.main(["find-data", "--roots", str(root), str(tmp_path / "nope")])
    cap = capsys.readouterr()
    assert code == 1 and "[경고] 훑을 폴더를 찾을 수 없습니다" in cap.err and "W" in cap.out  # 있는 폴더의 결과는 그대로 나온다
    assert cli.main(["find-data", "--roots", str(root)]) == 0
    assert "[경고]" not in capsys.readouterr().err


def test_l3_incomplete_scan_exits_3_with_warning(tmp_path, capsys, monkeypatch):
    root = make_tree(tmp_path)
    from pcmigrate import data_api
    real = data_api.run_find_data

    def partial(*a, **k):
        res = real(*a, **k)
        res.complete, res.stop_reason = False, "time"
        return res
    monkeypatch.setattr(data_api, "run_find_data", partial)
    code = cli.main(["find-data", "--roots", str(root), "--json"])
    cap = capsys.readouterr()
    assert code == 3 and json.loads(cap.out)["complete"] is False and "일부만 훑었습니다" in cap.err


# ------------------------------------------------------------------ L4 큰 단일 폴더 루프
def test_l4_huge_single_folder_checks_cancel_and_time(tmp_path, monkeypatch):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(2500):
        (big / f"f{i:04d}.txt").write_bytes(b"")
    monkeypatch.setattr(fd, "CHECK_EVERY", 100)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3  # 폴더 안에서 몇 번 확인한 뒤 취소
    with pytest.raises(Cancelled):
        fd.find_data([tmp_path], user_folders={}, exclude_paths=[], cancel=cancel)
    assert calls["n"] <= 6  # 2500개를 다 훑기 전에 멈춘다(수정 전에는 폴더 하나를 끝까지 읽고서야 확인)
    t = {"now": 0.0}

    def clock():
        t["now"] += 0.5
        return t["now"]
    res = fd.find_data([tmp_path], user_folders={}, exclude_paths=[], time_limit=3, clock=clock)
    assert res.complete is False and res.stop_reason == "time" and res.files_seen < 2500


# ------------------------------------------------------------------ L5 LAN 보내기는 '쓰는 작업'
def test_l5_lan_send_is_a_writing_task():
    from pcmigrate.gui import controller as ctl
    assert T.TASK_LAN_SEND in ctl._WRITING_TASKS


def test_l5_abandoned_lan_send_blocks_new_one(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.runner.abandoned_alive = lambda: True
    with pytest.raises(GuardError):
        c._run(T.TASK_LAN_SEND, lambda p, t: None, lambda r: None)
