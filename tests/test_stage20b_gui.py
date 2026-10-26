"""20b GUI: 그림 글자 자동 감지, 4GB 조각/임시 파일 위치/Git 설정 안내 연결, '추천대로 한 번에'(auto_run) 시험.

가짜 api(FakeApi)와 임시 폴더만 쓴다. 실제 스캔/내보내기/복원은 하지 않는다.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import api as real_api
from pcmigrate.cancel import Cancelled
from pcmigrate.copier import FileResult, Report
from pcmigrate.gui import icons
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, DriveEntry, GuardError
from pcmigrate.gui.runner import Runner
from pcmigrate.transfer import TransferError

from test_gui_controller import FakeApi, item, plan


# =============================================================================== 그림 글자 자동 감지

def test_missing_glyphs_by_width():
    widths = {"✔": 16, "✖": 16, "⚠": 21, "☑": 16, "☐": 16, "\uffff": 15, "\ufffe": 15}
    assert icons.missing_glyphs(lambda ch: widths[ch]) == []
    widths["⚠"] = 15  # '없는 글자' 네모와 같은 폭이면 그릴 수 없는 것으로 본다
    assert icons.missing_glyphs(lambda ch: widths[ch]) == ["⚠"]
    widths["✖"] = 0
    assert icons.missing_glyphs(lambda ch: widths[ch]) == ["✖", "⚠"]


def test_want_text_icons_auto_and_forced(monkeypatch):
    class Font:
        def __init__(self, missing):
            self.missing = missing

        def measure(self, ch):
            return 15 if ch in "\uffff\ufffe" or ch in self.missing else 17

    monkeypatch.delenv("PCMIGRATE_TEXT_ICONS", raising=False)
    assert icons.want_text_icons(Font("")) is False and icons.auto_result() is False
    assert icons.want_text_icons(Font("⚠")) is True and icons.auto_result() is True  # 자동: 하나라도 못 그리면 글자 표시
    monkeypatch.setenv("PCMIGRATE_TEXT_ICONS", "1")
    assert icons.want_text_icons(Font("")) is True  # 강제 옵션이 우선
    monkeypatch.setenv("PCMIGRATE_TEXT_ICONS", "0")
    assert icons.want_text_icons(Font("⚠")) is False
    monkeypatch.delenv("PCMIGRATE_TEXT_ICONS")
    assert icons.want_text_icons(None) is False


def test_texts_follow_auto_result_and_env_override():
    code = ("from pcmigrate.gui import icons; icons._auto = True\n"
            "from pcmigrate.gui import texts as T; print(T.ICON_OK, T.ICON_FAIL, T.ICON_WARN, T.CHECKED)")
    base = {**os.environ, "PYTHONPATH": str(Path(T.__file__).parents[2]), "PYTHONIOENCODING": "utf-8"}

    def run(**env):
        e = {k: v for k, v in base.items() if k != "PCMIGRATE_TEXT_ICONS"}
        e.update(env)
        return subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, encoding="utf-8",
                              check=True).stdout.split()
    assert run() == ["[OK]", "[X]", "[!]", "[v]"]  # 자동 판단이 '못 그림'이면 글자 표시
    assert run(PCMIGRATE_TEXT_ICONS="0") == ["✔", "✖", "⚠", "☑"]  # 강제 옵션 0 이 자동 판단보다 우선
    assert run(PCMIGRATE_TEXT_ICONS="1") == ["[OK]", "[X]", "[!]", "[v]"]


# =============================================================================== 컨트롤러 도우미

class Api(FakeApi):
    """호출 인자를 모두 기록하는 가짜 api(새 옵션 split_large / stage_dir 를 받는다)."""

    def __init__(self):
        super().__init__()
        self.kw = {}
        self.export_kw = {}
        self.transfer_meta = {}
        self.export_failed = []
        self.import_meta = {}
        self.fail_scan = None
        self.cancel_transfer = False

    def run_scan(self, rules_dir, deps=None, **kw):
        if self.fail_scan:
            raise self.fail_scan
        return super().run_scan(rules_dir, deps)

    def run_export(self, rules_dir, output, **kw):
        self.export_kw = kw
        self.calls.append(("export", output, kw.get("allow_secrets"), kw.get("extra_items")))
        return SimpleNamespace(manifest={"rules": [], "files": ["a"], "skipped": [], "failed": list(self.export_failed),
                                         "manual_warnings": []})

    def run_transfer(self, src, dst, *, dry_run=False, cancel=None, **kw):
        self.kw = dict(kw, dry_run=dry_run)
        self.calls.append(("transfer", dry_run))
        if self.cancel_transfer:
            raise Cancelled()
        if self.transfer_error:
            raise self.transfer_error
        status = "would_copy" if dry_run else "verified"
        return Report(dry_run=dry_run, results=[FileResult("s\\a", "d\\a", status, 1, "x")], meta=dict(self.transfer_meta))

    def run_verify(self, manifest, target, **kw):
        self.calls.append(("verify",))
        return Report(dry_run=False, results=[FileResult("s\\a", "d\\a", "verified", 1, "x")], meta={"mode": "verify"})

    def run_import(self, package, rules_dir, *, dry_run=False, yes=False, allow_extra=False, deps=None, **kw):
        res = super().run_import(package, rules_dir, dry_run=dry_run, yes=yes, allow_extra=allow_extra)
        if self.import_meta and res.report is not None:
            res.report.meta.update(self.import_meta)
        return res


def make(tmp_path, api=None):
    api = api or Api()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path, record_error=lambda e, w, tb=None: None,
                   make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    events = []
    c.listener = lambda kind, payload=None: events.append((kind, payload))
    desk = tmp_path / "Desktop"
    desk.mkdir(exist_ok=True)
    c.default_desktop = lambda: str(desk)
    c.default_downloads = lambda: str(tmp_path / "Downloads")
    c.drive_lister = lambda: []
    return c, api, events


@pytest.fixture
def env(tmp_path):
    return make(tmp_path)


def steps(c):
    return {s.key: s.state for s in c.auto.steps}


# =============================================================================== 4GB 조각 / 임시 파일 위치 / Git 안내

def _folders(c, tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(exist_ok=True)
    dst.mkdir(exist_ok=True)
    c.state.transfer_src, c.state.transfer_dst = str(src), str(dst)
    return src, dst


def test_split_large_is_passed_and_changing_it_invalidates_the_preview(env, tmp_path):
    c, api, ev = env
    _folders(c, tmp_path)
    c.set_transfer_options(False, False, True)
    c.start_transfer(dry_run=True, split_large=True)
    assert api.kw["split_large"] is True
    c.set_transfer_options(False, False, False)  # 미리보기 때와 달라지면 무효
    assert c.state.transfer_preview_key is None
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False, write_confirmed=True, split_large=False)
    c.start_transfer(dry_run=True)
    assert "split_large" not in api.kw
    with pytest.raises(GuardError):  # 미리보기 때는 끄고 실행 때만 켜는 것도 막는다
        c.start_transfer(dry_run=False, write_confirmed=True, split_large=True)
    c.start_transfer(dry_run=False, write_confirmed=True)
    assert api.calls[-1] == ("transfer", False)


def test_split_large_is_refused_when_api_does_not_support_it(tmp_path):
    class Old:  # split_large 를 모르는 api(조용히 무시하면 큰 파일이 그대로 복사된다)
        ApiError = real_api.ApiError

        def system_info(self):
            return SimpleNamespace(os_name="x", is_admin=False)

        def run_transfer(self, src, dst, *, dry_run=False, include_cloud=False, strict=False, progress=None, cancel=None,
                         deps=None, warn=None, progress_info=None):
            raise AssertionError("호출되면 안 된다")
    c, _api, _ev = make(tmp_path, Old())
    _folders(c, tmp_path)
    with pytest.raises(GuardError) as e:
        c.start_transfer(dry_run=True, split_large=True)
    assert e.value.args[0] == T.G_SPLIT_UNSUPPORTED


def test_split_result_note_and_summary(env, tmp_path):
    c, api, ev = env
    _folders(c, tmp_path)
    api.transfer_meta = {"split_files": ["a.bin", "b.bin"]}
    c.start_transfer(dry_run=True, split_large=True)
    assert c.split_count() == 2 and c.transfer_summary()["split"] == 2
    c.start_transfer(dry_run=False, write_confirmed=True, split_large=True)
    note = T.TR_SPLIT_RESULT.format(n=2)
    assert note in c.flow_notes() and "조각으로 나눈 파일 2개" in note and "pcmigrate join" in note
    api.transfer_meta = {"split_count": 3}  # 개수만 있는 형태도 받는다
    c.start_transfer(dry_run=False, verify_only=True)
    assert c.split_count() == 3
    api.transfer_meta = {}
    c.start_transfer(dry_run=True)
    assert c.split_count() == 0 and not any("조각" in n for n in c.flow_notes())


def test_export_stage_dir(env, tmp_path):
    c, api, ev = env
    out = str(tmp_path / "p.zip")
    c.start_export(out, write_confirmed=True)
    assert "stage_dir" not in api.export_kw  # 비워 두면(기본) 인자를 넘기지 않는다
    stage = tmp_path / "tmp-stage"
    with pytest.raises(GuardError) as e:
        c.start_export(out, write_confirmed=True, stage_dir=str(stage))  # 없는 폴더
    assert str(stage) in str(e.value)
    stage.mkdir()
    c.start_export(out, write_confirmed=True, stage_dir=str(stage))
    assert api.export_kw["stage_dir"] == str(stage)


def test_export_stage_dir_refused_when_api_does_not_support_it(tmp_path):
    class Old(FakeApi):
        def run_export(self, rules_dir, output, *, extra=None, allow_secrets=False, deps=None, extra_items=None):
            raise AssertionError("호출되면 안 된다")
    c, _api, _ev = make(tmp_path, Old())
    with pytest.raises(GuardError) as e:
        c.start_export(str(tmp_path / "p.zip"), write_confirmed=True, stage_dir=str(tmp_path))
    assert e.value.args[0] == T.G_STAGE_UNSUPPORTED


def test_git_config_warning_note_in_preview_and_result(env):
    c, api, ev = env
    c.set_package("p.zip")
    c.start_preview()
    assert c.git_config_note() == ""
    c.state.preview.plan.manifest["git_config_warning"] = True  # 리포트 meta 의 키 이름
    assert c.git_config_note() == T.PREV_GIT_NOTE and "Git 설정 파일" in T.PREV_GIT_NOTE
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    api.import_meta = {"git_config_warning": True}
    c.start_apply()
    assert c.git_config_note(applied=True) == T.RES_GIT_NOTE
    api.import_meta = {}
    api.preview.plan.manifest.pop("git_config_warning")  # 같은 미리보기 객체를 다시 쓰므로 표시를 지운다
    c.set_package("p.zip")
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    assert c.git_config_note(applied=True) == ""


# =============================================================================== 설정 묶음 파일 찾기

def test_find_packages_searches_desktop_downloads_and_external_drives(tmp_path, monkeypatch):
    c, api, ev = make(tmp_path)
    dl, usb, sys_drive = tmp_path / "Downloads", tmp_path / "USB", tmp_path / "SYS"
    for d in (dl, usb / "backup", sys_drive):
        d.mkdir(parents=True)
    a = Path(c.default_desktop()) / "pcmigrate-settings.zip"
    b = dl / "pcmigrate-settings (1).zip"
    e = usb / "backup" / "PCMIGRATE-SETTINGS-old.zip"  # 외장 드라이브의 바로 아래 폴더, 대소문자 무시
    for f, t in ((a, 3000), (b, 2000), (e, 1000)):
        f.write_text("x")
        os.utime(f, (t, t))
    (dl / "other.zip").write_text("x")  # 이름이 다르면 제외
    (usb / "backup" / "deep").mkdir()
    (usb / "backup" / "deep" / "pcmigrate-settings.zip").write_text("x")  # 두 단계 아래는 찾지 않는다
    c.drive_lister = lambda: [DriveEntry(str(usb), "removable"), DriveEntry(str(sys_drive), "fixed")]
    monkeypatch.setenv("SystemDrive", str(sys_drive))  # 시스템 드라이브(내장)는 따로 찾지 않는다(바탕화면/다운로드가 이미 있다)
    assert c.find_packages() == [str(a), str(b), str(e)]  # 최근 것이 먼저
    for f in (a, b, e):
        f.unlink()
    assert c.find_packages() == []


# =============================================================================== 추천대로 한 번에: 구 PC

def test_auto_old_runs_steps_in_order_and_waits_for_export_confirmation(env, tmp_path):
    c, api, ev = env
    c.state.selected = {("%APPDATA%", "Old")}  # 이전에 직접 고른 항목이 있어도 가져오지 않는다
    c.state.secrets_ack = True
    c.auto_run("old")
    assert [x[0] for x in api.calls] == ["scan"]  # 조사 -> 해야 할 일 목록(run_checklist 는 calls 에 안 남음)
    assert steps(c) == {"scan": "done", "discover": "skipped", "checklist": "done", "export": "waiting"}
    assert c.auto.status == "waiting" and c.auto.gate == "export_confirm"
    assert c.state.screen == S.AUTO and "discover" not in [x[0] for x in api.calls]  # 규칙에 없는 프로그램은 찾지도 고르지도 않는다
    assert c.state.selected == set() and c.state.secrets_ack is False and c.state.discovery is None
    assert not any(x[0] == "export" for x in api.calls)  # 사람이 확인하기 전에는 내보내지 않는다
    out = c.auto.export_path
    assert out == os.path.join(c.default_desktop(), T.EXP_DEFAULT_NAME)  # 바탕화면 기본 경로
    c.auto_confirm(True)
    assert api.calls[-1] == ("export", out, False, None)  # 선택 항목 없음 -> 추가 항목 없음
    assert c.auto.status == "done" and steps(c)["export"] == "done" and c.auto.warning == ""
    assert c.auto_result_lines()[0] == T.EXP_DONE.format(path=out)
    kinds = [k for k, _ in ev]
    assert kinds.index("scan_done") < kinds.index("checklist_done") < kinds.index("export_done")


def test_auto_old_decline_stops_without_writing(env):
    c, api, ev = env
    c.auto_run("old")
    c.auto_confirm(False)
    assert c.auto.status == "stopped" and c.auto.what == T.AUTO_EXPORT_DECLINED
    assert not any(x[0] == "export" for x in api.calls)
    with pytest.raises(GuardError):
        c.auto_confirm(True)  # 멈춘 뒤에는 시작되지 않는다


def test_auto_old_cannot_skip_the_confirmation(env):
    c, api, ev = env
    c.auto_run("old")
    for bad in (lambda: c.auto_apply(), lambda: c.auto_pick_package("x"), lambda: c.auto_answer_questions("external", 1),
                lambda: c.auto_set_folders("a", "b")):
        with pytest.raises(GuardError):
            bad()
    assert not any(x[0] == "export" for x in api.calls) and c.auto.gate == "export_confirm"


def test_auto_old_stops_on_error_and_shows_easy_reason(tmp_path):
    api = Api()
    api.fail_scan = PermissionError(5, "denied", "x")
    c, _api, ev = make(tmp_path, api)
    c.auto_run("old")
    assert c.auto.status == "stopped" and steps(c)["scan"] == "stopped" and steps(c)["checklist"] == "todo"
    assert c.auto.what and "Permission" not in c.auto.what and c.auto.how  # 영문 예외 이름 없이 쉬운 문구
    assert ("error", c.last_error) in ev


def test_auto_old_unreadable_files_finish_with_warning(env):
    c, api, ev = env
    api.export_failed = [{"src": "C:\\x\\a.dat", "error": "in use", "reason": "in_use"}, {"src": "C:\\x\\b", "error": "boom"}]
    c.auto_run("old")
    c.auto_confirm(True)
    assert c.auto.status == "done" and steps(c)["export"] == "warn"
    assert T.AUTO_UNREADABLE_WARN.format(n=1) in c.auto.warning and T.AUTO_FAILED_WARN.format(n=1) in c.auto.warning


def test_auto_user_stop_while_waiting_and_leaving_the_screen(env):
    c, api, ev = env
    c.auto_run("old")
    assert c.auto_stop() is True
    assert c.auto.status == "stopped" and c.auto.by_user and c.auto.what == T.AUTO_STOPPED_USER
    assert not any(x[0] == "export" for x in api.calls)
    assert c.auto_stop() is False  # 이미 멈춤
    c.auto_manual()
    assert c.state.screen == S.OLD_SCAN and c.auto is None  # 직접 진행하기: 기존 화면으로, 한 번에 진행은 끝
    c.auto_run("old")
    c.go(S.START)
    assert c.auto is None


def test_auto_stop_during_a_running_task_stops_before_the_next_step(tmp_path):
    """작업 중 '중지': 취소할 수 없는 단계는 끝난 뒤 다음 단계를 시작하지 않고 멈춘다."""
    c, api, ev = make(tmp_path)
    orig = api.run_scan

    def scan(rules_dir, deps=None, **kw):
        c.auto_stop()  # 조사 도중에 사용자가 '중지'를 누른 것과 같다
        return orig(rules_dir, deps)
    api.run_scan = scan
    c.auto_run("old")
    assert c.auto.status == "stopped" and c.auto.by_user and c.auto.what == T.AUTO_STOPPED_USER
    assert steps(c)["scan"] == "done" and steps(c)["checklist"] == "todo"  # 조사는 마쳤고 다음 단계는 시작하지 않았다


def test_auto_run_refuses_while_busy_or_unknown_flow(env):
    c, api, ev = env
    with pytest.raises(GuardError):
        c.auto_run("nope")
    c.state.busy = True
    with pytest.raises(GuardError):
        c.auto_run("old")


# =============================================================================== 추천대로 한 번에: 새 PC

def test_auto_new_without_any_package_explains_and_offers_manual_pick(env):
    c, api, ev = env
    c.auto_run("new")
    a = c.auto
    assert a.status == "stopped" and a.what == T.AUTO_PKG_NONE_WHAT and a.how == T.AUTO_PKG_NONE_HOW
    assert "바탕화면" in a.what and "다운로드" in a.what and "외장 드라이브" in a.what
    assert not any(x[0] == "import" for x in api.calls)
    c.auto_manual()
    assert c.state.screen == S.NEW_PACKAGE


def test_auto_new_single_package_goes_to_preview_then_waits_for_both_checks_and_final_yes(env, tmp_path):
    c, api, ev = env
    pkg = Path(c.default_desktop()) / "pcmigrate-settings.zip"
    pkg.write_text("x")
    c.auto_run("new")
    assert api.calls == [("import", True, False, False)]  # 미리보기만(쓰지 않음)
    assert c.state.package == str(pkg) and steps(c) == {"find": "done", "preview": "done", "confirm": "waiting", "apply": "todo"}
    assert c.auto.gate == "restore"
    for attempt in (lambda: c.auto_apply(),):  # 체크도 확인창도 없이는 복원하지 않는다
        with pytest.raises(GuardError) as e:
            attempt()
        assert e.value.args[0] == T.G_NEED_CHECK_PREVIEW
    c.set_preview_checked(True)
    with pytest.raises(GuardError) as e:
        c.auto_apply()
    assert e.value.args[0] == T.G_NEED_BAK_UNDERSTAND
    c.set_bak_understood(True)
    with pytest.raises(GuardError) as e:
        c.auto_apply()  # 최종 확인창의 '예'가 없다
    assert e.value.args[0] == T.G_NEED_FINAL_YES
    c.confirm_final(False)
    with pytest.raises(GuardError):
        c.auto_apply()
    assert api.calls == [("import", True, False, False)]
    c.confirm_final(True)
    c.auto_apply()
    assert api.calls[-1] == ("import", False, True, False)
    assert c.auto.status == "done" and steps(c)["apply"] == "done" and c.auto.warning == ""


def test_auto_new_multiple_packages_need_a_choice_from_the_found_list(env):
    c, api, ev = env
    d = Path(c.default_desktop())
    paths = []
    for name, t in (("pcmigrate-settings.zip", 1000), ("pcmigrate-settings-2.zip", 2000)):
        (d / name).write_text("x")
        os.utime(d / name, (t, t))
        paths.append(str(d / name))
    c.auto_run("new")
    assert c.auto.gate == "pick_package" and c.auto.candidates == [paths[1], paths[0]]
    assert api.calls == []
    with pytest.raises(GuardError):
        c.auto_pick_package(str(d / "evil.zip"))  # 찾은 목록에 없는 경로는 받지 않는다
    c.auto_pick_package(paths[0])
    assert c.state.package == paths[0] and c.auto.gate == "restore"


def test_auto_new_stops_on_rejected_and_on_package_problems(env):
    c, api, ev = env
    (Path(c.default_desktop()) / "pcmigrate-settings.zip").write_text("x")
    api.preview = real_api.ImportResult(plan([item(), item("rejected")]), "dry-run", 0)
    c.auto_run("new")
    assert c.auto.status == "stopped" and c.auto.what == T.AUTO_REJECTED_WHAT.format(n=1) and c.auto.manual == S.NEW_PREVIEW
    c.auto_manual()
    assert c.state.screen == S.NEW_PREVIEW and c.state.preview is not None  # 미리보기 화면에서 직접 확인
    api.preview = real_api.ImportResult(plan([], problems=["해시 불일치"]), "blocked", 0)
    c.auto_run("new")
    assert c.auto.status == "stopped" and "설정 묶음 파일 검사" in c.auto.what and c.auto.how == T.AUTO_PROBLEMS_HOW
    assert not any(x[1:] == (False, True, False) for x in api.calls if x[0] == "import")


def test_auto_new_apply_failure_stops_with_reason(env):
    c, api, ev = env
    (Path(c.default_desktop()) / "pcmigrate-settings.zip").write_text("x")
    c.auto_run("new")
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    api.run_import = lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space"))
    c.auto_apply()
    assert c.auto.status == "stopped" and steps(c)["apply"] == "stopped" and c.auto.what
    assert c.state.final_confirmed is False  # 실패 뒤 확인 표시는 지워진다(기존 규칙)


# =============================================================================== 추천대로 한 번에: 대용량

def test_auto_transfer_questions_then_preview_then_confirmation_before_copy(env, tmp_path):
    c, api, ev = env
    c.auto_run("transfer")
    assert c.auto.gate == "questions" and api.calls == []
    with pytest.raises(GuardError):
        c.auto_answer_questions("local", 1)  # 질문 두 개로는 외장/랜선만
    c.auto_answer_questions("external", 1)
    assert c.flow() == ("external", 1) and c.auto.gate == "folders"
    assert [s.key for s in c.auto.steps] == ["questions", "folders", "preview", "copy", "result"]
    src, dst = tmp_path / "src", tmp_path / "dst"
    with pytest.raises(GuardError):
        c.auto_set_folders(str(src), str(dst))  # 점검에 통과하지 못하면 이어가지 않는다
    assert c.auto.gate == "folders"
    src.mkdir()
    dst.mkdir()
    c.auto_set_folders(str(src), str(dst))
    assert api.calls == [("transfer", True)]  # 미리보기만(쓰지 않음)
    assert api.kw["dry_run"] is True and "split_large" not in api.kw
    assert c.auto.gate == "transfer_confirm" and steps(c)["preview"] == "done" and steps(c)["copy"] == "waiting"
    with pytest.raises(GuardError):
        c.auto_apply()
    assert api.calls == [("transfer", True)]  # 사람의 확인 없이는 복사하지 않는다
    c.auto_confirm(True)
    assert api.calls[-1] == ("transfer", False) and api.kw["include_cloud"] is False and api.kw.get("strict") is False
    assert c.auto.status == "done" and steps(c)["result"] == "done"
    assert c.auto_result_lines()[0].startswith(T.RESULT_OK) or T.RESULT_OK in c.auto_result_lines()[0]


def test_auto_transfer_decline_and_blocked_preview_stop(env, tmp_path):
    c, api, ev = env
    _src, _dst = (tmp_path / "s"), (tmp_path / "d")
    _src.mkdir()
    _dst.mkdir()
    c.auto_run("transfer")
    c.auto_answer_questions("external", 1)
    c.auto_set_folders(str(_src), str(_dst))
    c.auto_confirm(False)
    assert c.auto.status == "stopped" and c.auto.what == T.AUTO_TR_PREVIEW_DECLINED and api.calls == [("transfer", True)]
    api.transfer_meta = {"fat32_blocked": True}
    c.auto_run("transfer")
    c.auto_answer_questions("external", 1)
    c.auto_set_folders(str(_src), str(_dst))
    assert c.auto.status == "stopped" and c.auto.what == T.WHY_FAT32 and c.auto.how == T.AUTO_TR_FAT32_HOW
    assert c.auto.manual == S.TRANSFER and api.calls == [("transfer", True)] * 2  # 복사는 시작하지 않았다
    api.transfer_meta = {"space_blocked": True}
    c.auto_run("transfer")
    c.auto_answer_questions("network", 1)
    c.auto_set_folders(str(_src), "\\\\newpc\\share")
    assert c.auto.status == "stopped" and c.auto.what == T.WHY_SPACE


def test_auto_transfer_cancel_and_failure_stop(env, tmp_path):
    c, api, ev = env
    s, d = tmp_path / "s", tmp_path / "d"
    s.mkdir()
    d.mkdir()
    api.cancel_transfer = True
    c.auto_run("transfer")
    c.auto_answer_questions("external", 1)
    c.auto_set_folders(str(s), str(d))
    assert c.auto.status == "stopped" and c.auto.what == T.AUTO_CANCELLED_WHAT and not c.auto.by_user
    api.cancel_transfer = False
    api.transfer_error = TransferError("boom")
    c.auto_run("transfer")
    c.auto_answer_questions("external", 1)
    c.auto_set_folders(str(s), str(d))
    assert c.auto.status == "stopped" and steps(c)["preview"] == "stopped" and c.auto.what


def test_auto_transfer_copy_failures_end_with_warning(env, tmp_path):
    c, api, ev = env
    s, d = tmp_path / "s", tmp_path / "d"
    s.mkdir()
    d.mkdir()
    c.auto_run("transfer")
    c.auto_answer_questions("external", 1)
    c.auto_set_folders(str(s), str(d))
    api.run_transfer = lambda *a, dry_run=False, **k: Report(dry_run=dry_run, results=[
        FileResult("s\\a", "d\\a", "would_copy" if dry_run else "mismatch", 1, "x", error="다름")])
    c.auto_confirm(True)
    assert c.auto.status == "done" and steps(c)["copy"] == "warn" and T.AUTO_TR_FAIL_WHAT.format(n=1) in c.auto.warning


def test_auto_transfer_network_new_pc_runs_check_only_after_confirmation(env, tmp_path):
    c, api, ev = env
    listing, target = tmp_path / "pcmigrate-manifest.jsonl", tmp_path / "copy"
    listing.write_text("{}")
    target.mkdir()
    c.auto_run("transfer")
    c.auto_answer_questions("network", 2)
    assert c.is_verify_flow() and [s.key for s in c.auto.steps] == ["questions", "folders", "check", "result"]
    with pytest.raises(GuardError):
        c.auto_set_folders(str(tmp_path / "missing.jsonl"), str(target))
    c.auto_set_folders(str(listing), str(target))
    assert api.calls == [] and c.auto.gate == "verify_confirm"  # 검사 전에도 확인
    c.auto_confirm(True)
    assert api.calls == [("verify",)] and c.auto.status == "done"


# =============================================================================== tk 화면

tk = pytest.importorskip("tkinter")
from tkinter import messagebox  # noqa: E402

from pcmigrate.gui.app import App  # noqa: E402


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    import time
    answers = {"askyesno": True, "askokcancel": True}
    shown = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: answers["askyesno"])
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: answers["askokcancel"])
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: shown.append(("warn", a)))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(("info", a)))
    c, api, _ev = make(tmp_path)
    app, last = None, None
    for _ in range(4):
        try:
            app = App(c)
            break
        except tk.TclError as e:
            last = e
            time.sleep(0.5)
    if app is None:
        pytest.skip(f"디스플레이/Tcl 을 사용할 수 없음: {last}")
    app.geometry("900x640+0+0")
    app.update()
    yield SimpleNamespace(app=app, c=c, api=api, answers=answers, shown=shown, tmp=tmp_path)
    app.destroy()


def test_start_screen_has_an_auto_button_per_card_and_two_line_titles(ctx):
    app, c = ctx.app, ctx.c
    v = app.views[S.START]
    app.show(S.START)
    app.update()
    assert [b.cget("text") for b in v.auto_buttons] == [T.BTN_AUTO] * 3
    texts = [w.cget("text") for card in v.cards for w in card.winfo_children() if isinstance(w, tk.Label)]
    for title, *_ in T.START_CHOICES:
        assert title in texts and "\n" in title
    v.auto_buttons[0].invoke()
    assert c.auto is not None and c.auto.flow == "old" and app.current == S.AUTO
    app.update()
    assert c.auto.gate == "export_confirm" and not any(x[0] == "export" for x in ctx.api.calls)


def test_auto_view_export_gate_asks_the_normal_confirmation(ctx):
    app, c = ctx.app, ctx.c
    c.auto_run("old")
    app.update()
    v = app.views[S.AUTO]
    assert app.current == S.AUTO and [s.key for s in c.auto.steps] == ["scan", "discover", "checklist", "export"]
    assert v.gate_btn.cget("text") == T.AUTO_EXPORT_CONFIRM_BTN and v.stop_btn.winfo_ismapped()
    ctx.answers["askokcancel"] = False  # 확인 창에서 '취소' -> 멈춤, 아무것도 쓰지 않음
    v.gate_btn.invoke()
    assert c.auto.status == "stopped" and not any(x[0] == "export" for x in ctx.api.calls)
    assert not v.stop_btn.winfo_ismapped() and v.msg.cget("text").startswith(T.AUTO_EXPORT_DECLINED)
    c.auto_run("old")
    app.update()
    ctx.answers["askokcancel"] = True
    v.gate_btn.invoke()
    app.update()
    assert c.auto.status == "done" and ctx.api.calls[-1][0] == "export"
    assert T.AUTO_DONE_HEAD == v.head.cget("text") and v.out.winfo_children()


def test_auto_view_restore_gate_enforces_checks_and_final_dialog(ctx):
    app, c = ctx.app, ctx.c
    (Path(c.default_desktop()) / "pcmigrate-settings.zip").write_text("x")
    c.auto_run("new")
    app.update()
    v = app.views[S.AUTO]
    assert c.auto.gate == "restore" and "disabled" in v.apply_btn.state()
    assert v.apply_why.cget("text") == T.G_NEED_CHECK_PREVIEW
    v.check.invoke()
    assert "disabled" in v.apply_btn.state() and v.apply_why.cget("text") == T.G_NEED_BAK_UNDERSTAND
    v.check_bak.invoke()
    assert "disabled" not in v.apply_btn.state()
    ctx.answers["askyesno"] = False  # 최종 확인창에서 '아니오'
    v.apply_btn.invoke()
    assert c.state.final_confirmed is False and ctx.api.calls[-1][1:] == (True, False, False)
    ctx.answers["askyesno"] = True
    v.apply_btn.invoke()
    assert ctx.api.calls[-1][1:] == (False, True, False)  # 둘 다 체크 + '예' 일 때만 복원


def test_auto_view_no_package_found_shows_easy_reason_and_manual_button(ctx):
    app, c = ctx.app, ctx.c
    c.auto_run("new")
    app.update()
    v = app.views[S.AUTO]
    assert v.head.cget("text") == T.AUTO_STOPPED_HEAD and T.AUTO_PKG_NONE_WHAT in v.msg.cget("text")
    labels = [w.cget("text") for w in v.out.winfo_children()]
    assert T.AUTO_BTN_PICK_MANUAL in labels
    v.out.winfo_children()[0].invoke()
    assert app.current == S.NEW_PACKAGE and c.auto is None


def test_auto_view_transfer_questions_and_folders(ctx):
    app, c = ctx.app, ctx.c
    c.auto_run("transfer")
    app.update()
    v = app.views[S.AUTO]
    assert v.gate_btn.cget("text") == T.AUTO_Q_NEXT and c.auto.gate == "questions"
    v.q1.set(T.MODE_EXTERNAL)
    v.q2.set(2)
    v.gate_btn.invoke()
    app.update()
    assert c.flow() == (T.MODE_EXTERNAL, 2) and c.auto.gate == "folders"
    assert v.gate_btn.cget("text") == T.AUTO_FOLDERS_BTN
    v.gate_btn.invoke()  # 폴더를 안 골랐으면 안내 창만 뜨고 진행하지 않는다
    assert ctx.shown and ctx.shown[-1][0] == "warn" and c.auto.gate == "folders"
    src, dst = ctx.tmp / "usb", ctx.tmp / "newpc"
    src.mkdir()
    dst.mkdir()
    v.f1.set(f'"{src}"')  # 따옴표 붙여넣기도 정리된다
    v.f2.set(str(dst))
    v.gate_btn.invoke()
    app.update()
    assert c.auto.gate == "transfer_confirm" and ctx.api.calls[-1] == ("transfer", True)
    ctx.answers["askokcancel"] = False
    v.gate_btn.invoke()
    assert c.auto.status == "stopped" and ctx.api.calls[-1] == ("transfer", True)


def test_path_field_entry_is_on_its_own_full_width_row(ctx):
    app = ctx.app
    app.show(S.OLD_EXPORT)
    app.update()
    f = app.views[S.OLD_EXPORT].path_field
    assert f.entry.grid_info()["row"] < f.buttons.grid_info()["row"]  # 입력칸 아래에 단추 줄
    assert f.entry.winfo_width() >= f.winfo_width() - 4  # 한 줄 전체
    assert f.browse_btn.winfo_y() + f.buttons.winfo_y() > f.entry.winfo_y()
    app.show(S.TRANSFER)
    app.update()
    t = app.views[S.TRANSFER]
    for field in (t.src_field, t.dst_field):
        assert field.entry.winfo_width() >= field.winfo_width() - 4
    assert t.dst_field.drive_box.master is t.dst_field.buttons


def test_transfer_advanced_options_has_split_checkbox_and_passes_it(ctx):
    app, c = ctx.app, ctx.c
    v = app.views[S.TRANSFER]
    app.show(S.TRANSFER)
    assert v.opts.toggle_btn.cget("text") == T.TR_OPTIONS_TOGGLE and "고급 옵션" in T.TR_OPTIONS_TOGGLE
    v.opts.set_open(True)
    app.update()
    src, dst = ctx.tmp / "s", ctx.tmp / "d"
    src.mkdir()
    dst.mkdir()
    v.src_var.set(str(src))
    v.dst_var.set(str(dst))
    texts = []

    def walk(w):
        for ch in w.winfo_children():
            if "text" in ch.keys():
                texts.append(str(ch.cget("text")))
            walk(ch)
    walk(v.copy_opts)
    assert T.TR_SPLIT in texts
    v.split_var.set(True)
    v._dry()
    assert ctx.api.kw["split_large"] is True
    v.split_var.set(False)  # 옵션을 바꾸면 미리보기가 무효가 되어 복사 시작이 막힌다
    assert c.state.transfer_preview_key is None


def test_export_view_stage_dir_option(ctx):
    app, c = ctx.app, ctx.c
    v = app.views[S.OLD_EXPORT]
    app.show(S.OLD_EXPORT)
    assert v.adv.toggle_btn.cget("text") == T.EXP_ADV_TOGGLE and v.stage_var.get() == ""
    v.adv.set_open(True)
    assert v.stage_field.label.cget("text") == T.EXP_STAGE_LABEL
    stage = ctx.tmp / "stage"
    stage.mkdir()
    v.stage_var.set(f'"{stage}"')
    v._export()
    assert ctx.api.export_kw["stage_dir"] == str(stage)
    v.stage_var.set("")
    v._export()
    assert "stage_dir" not in ctx.api.export_kw


def test_git_note_is_one_more_line_not_a_second_box(ctx):
    app, c = ctx.app, ctx.c
    c.set_package("p.zip")
    c.start_preview()
    c.state.preview.plan.manifest["git_config_warning"] = True
    app.show(S.NEW_PREVIEW)
    v = app.views[S.NEW_PREVIEW]
    assert T.PREV_GIT_NOTE in v.summary.cget("text") and T.PREV_GIT_NOTE not in v.problem_box.cget("text")


def test_app_detect_icons_keeps_current_texts_when_font_is_fine(ctx, monkeypatch):
    from tkinter import font as tkfont
    assert isinstance(icons.glyphs_supported(tkfont.nametofont("TkDefaultFont")), bool)
    monkeypatch.setattr(icons, "want_text_icons", lambda font: T.TEXT_ICONS)
    assert ctx.app.detect_icons() is T.TEXT_ICONS


def test_app_switches_whole_screen_to_text_icons_when_font_cannot_draw_them():
    code = """
import tkinter as tk
from pcmigrate.gui import icons, texts as T
def fake(font):  # 글꼴이 그림 글자를 못 그리는 환경을 흉내 낸다(진짜 want_text_icons 처럼 자동 판단 결과를 남긴다)
    icons._auto = True
    return True
icons.want_text_icons = fake
from pcmigrate.gui.app import App
try:
    app = App(detect_icons=True)
except tk.TclError:
    print("SKIP"); raise SystemExit(0)
app.withdraw()
print(T.ICON_OK, T.ICON_WARN, app.views['start'].license_box.cget('text').split()[0])
app.destroy()
"""
    env = {**os.environ, "PYTHONPATH": str(Path(T.__file__).parents[2]), "PYTHONIOENCODING": "utf-8"}
    env.pop("PCMIGRATE_TEXT_ICONS", None)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
    if out == ["SKIP"]:
        pytest.skip("디스플레이를 사용할 수 없음")
    assert out == ["[OK]", "[!]", "[!]"]  # 이미 만든 문구가 아니라 화면을 만들기 전에 바뀐 문구가 쓰인다
