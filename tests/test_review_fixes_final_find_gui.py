"""stage80: 최종 새 리뷰어(파일 찾기·GUI) 지적 수정의 재현 시험. 수정 전에는 실패하도록 지적된 상황을 그대로 재현한다.

임시 폴더와 가짜 환경만 쓴다(CLI 시험은 --env 로 모든 환경변수를 임시 폴더로 돌리고 경로를 확인한다. 실제 AppData 는 건드리지 않는다).
"""
import json
import os
import threading
from pathlib import Path

import pytest

from pcmigrate import cli, data_api, errorlog, find_data as fd, find_exts as fx
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi
from test_gui_my_files_controller import FakeData, FakeLan


def put(p: Path, data: bytes = b"x" * 10) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def cli_env(tmp_path):
    """모든 환경변수를 임시 폴더로 돌리는 --env 인자(결과 경로가 임시 폴더 아래인지 확인한다)."""
    home = tmp_path / "fakehome"
    args = []
    for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP", "PROGRAMDATA"):
        d = home / name
        d.mkdir(parents=True, exist_ok=True)
        assert str(d).startswith(str(tmp_path))
        args += ["--env", f"{name}={d}"]
    return args


# ------------------------------------------------------------------ M1 비정상 mtime
def weird_mtime_tree(tmp_path):
    root = tmp_path / "pc"
    good = put(root / "Proj" / "a.dwg")
    bad = put(root / "Proj" / "b.dwg")
    try:
        os.utime(bad, (4e11, 4e11))
        os.stat(bad).st_mtime
    except (OverflowError, OSError, ValueError):
        pytest.skip("이 파일 시스템은 아주 먼 미래 시각을 저장하지 않는다")
    return root


def test_m1_abnormal_mtime_does_not_abort_find(tmp_path):
    root = weird_mtime_tree(tmp_path)
    res = fd.find_data([root], user_folders={}, exclude_paths=[], clock=lambda: 0.0)  # 수정 전: OSError 로 찾기 전체 중단
    assert [Path(p.path).name for p in res.projects] == ["Proj"] and res.projects[0].total_files == 2
    assert res.projects[0].latest_mtime == "" and res.error_count >= 1
    assert any(e["path"].endswith("Proj") for e in res.errors)  # 어느 폴더의 시각이 문제였는지 기록
    assert res.complete


def test_m1_cli_reports_scan_result_not_write_failure(tmp_path, capsys):
    root = weird_mtime_tree(tmp_path)
    env = cli_env(tmp_path)
    code = cli.main([*env, "find-data", "--roots", str(root), "--json"])
    cap = capsys.readouterr()
    assert code == 0 and json.loads(cap.out)["projects"] and "쓰기" not in cap.err


def test_m1_selection_write_failure_is_still_reported_as_write_failure(tmp_path, capsys):
    root = tmp_path / "pc"
    put(root / "A" / "x.dwg")
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder")
    out = blocker / "sel.json"  # 부모가 파일이라 쓸 수 없다
    code = cli.main([*cli_env(tmp_path), "find-data", "--roots", str(root), "--output-selection", str(out)])
    err = capsys.readouterr().err
    assert code == 1 and err.strip() and "예상하지 못한" not in err


def test_write_selection_cleans_part_file_on_failure(tmp_path, monkeypatch):
    res = fd.FindResult()
    out = tmp_path / "sel.json"
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("denied")))
        with pytest.raises(PermissionError):
            fd.write_selection(res, out)
    assert not list(tmp_path.glob("*.part")) and not out.exists()  # 수정 전: sel.json.part 가 남음


# ------------------------------------------------------------------ M3 가려진 선택
@pytest.fixture
def env(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = FakeData(), FakeLan(tmp_path)
    c.find_data_start()
    return c


ALPHA, BETA = "C:\\Proj\\Alpha", "C:\\Proj\\Beta"


def test_m3_hidden_selection_is_shown_in_total_and_kept(env):
    c = env
    c.set_data_selected(ALPHA, True)
    c.set_data_selected(BETA, True)
    assert c.hidden_selected_count() == 0 and "가려진" not in c.data_total_text()
    c.set_data_filter("design", True)
    for cat in ("photo", "video", "doc"):
        c.set_data_filter(cat, False)  # 설계만 보기: Beta(사진만) 줄이 사라진다
    assert [r.path for r in c.data_rows()] == [ALPHA]
    assert c.hidden_selected_count() == 1
    assert T.FD_TOTAL_HIDDEN.format(n=1) in c.data_total_text()  # 수정 전: 아무 표시 없이 Beta 도 전송됨
    assert set(c.data_selection()) == {ALPHA, BETA} and {i.path for i in c.data_selected_items()} == {ALPHA, BETA}  # 선택은 임의로 해제하지 않는다
    assert c.data_total()[0] == 2
    c.set_data_filter("photo", True)
    assert c.hidden_selected_count() == 0 and "가려진" not in c.data_total_text()


def test_m3_existing_totals_unchanged_without_hiding(env):
    c = env
    assert "가려진" not in c.data_total_text()
    c.set_data_selected(ALPHA, True)
    n, size, unknown = c.data_total()
    from pcmigrate.progress import fmt_bytes
    assert n == 1 and c.data_total_text().startswith(T.FD_TOTAL.format(n=1, size=fmt_bytes(size)))


# ------------------------------------------------------------------ 낮음
def test_errorlog_record_holds_lock(tmp_path, monkeypatch):
    seen = []
    real = errorlog._record_locked

    def spy(*a, **k):
        seen.append(errorlog._LOCK._is_owned())
        return real(*a, **k)
    monkeypatch.setattr(errorlog, "_record_locked", spy)
    errorlog.reset_throttle()
    errorlog.record(ValueError("x"), "ctx", tb="tb", directory=tmp_path / "logs")
    assert seen == [True]
    # 여러 스레드가 동시에 기록해도 예외 없이 상한(BURST_MAX)을 넘지 않는다
    monkeypatch.setattr(errorlog, "_record_locked", real)
    errorlog.reset_throttle()
    errs = []

    def worker(n):
        try:
            for i in range(30):
                errorlog.record(ValueError(f"e{n}-{i}"), f"c{n}", tb="tb", directory=tmp_path / "logs2")
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    text = next((tmp_path / "logs2").glob("errors-*.log")).read_text(encoding="utf-8")
    assert not errs and text.count("예외:") == errorlog.BURST_MAX


def test_damaged_settings_copies_are_capped(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    for i in range(fx.MAX_DAMAGED_COPIES):
        (d / (fx.FILE_NAME + (".bak" if i == 0 else f".bak.{i}"))).write_text(f"old{i}", encoding="utf-8")
    (d / fx.FILE_NAME).write_text("{broken-new", encoding="utf-8")
    cfg = fx.load(d)
    assert cfg.damaged and any("이미 많아" in n for n in cfg.notes)
    assert len(list(d.glob(fx.FILE_NAME + ".bak*"))) == fx.MAX_DAMAGED_COPIES  # 수정 전: 61번째 복사본이 생김
    assert (d / fx.FILE_NAME).read_text(encoding="utf-8") == "{broken-new"


def test_cli_conflicting_add_exits_1_and_duplicate_exits_0(capsys):
    assert cli.main(["find-exts", "add", ".dwg", "--category", "photo"]) == 1  # 기본으로 설계에 있어 적용되지 않음
    assert "먼저 삭제" in capsys.readouterr().out
    assert cli.main(["find-exts", "add", ".dwg", "--category", "design"]) == 0  # 이미 있음(중복): 안내만
    capsys.readouterr()
    assert cli.main(["find-exts", "add", ".zep", "--category", "design"]) == 0
    assert cli.main(["find-exts", "add", ".zep", "--category", "photo"]) == 0  # 사용자 추가 형식의 분류 변경은 적용됨
    capsys.readouterr()


def test_busy_guards_run_before_clearing_previous_state(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = FakeData(), FakeLan(tmp_path)
    c.state.package = str(tmp_path / "p.zip")
    c.state.restore_skip = {"revit"}
    c.state.busy = True
    with pytest.raises(GuardError):
        c.start_preview()
    assert c.state.restore_skip == {"revit"}  # 수정 전: 먼저 비워짐
    c.state.install_consent = False
    with pytest.raises(GuardError):
        c.set_install_consent(True)
    assert c.state.install_consent is False
    c.files.tx_report, c.files.tx_error = "old-report", "old-error"
    with pytest.raises(GuardError):
        c.lan_send_start("192.0.2.10:47830", "123456", confirmed=True)
    assert c.files.tx_report == "old-report" and c.files.tx_error == "old-error"
