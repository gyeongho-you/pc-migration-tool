"""GUI 컨트롤러 헤드리스 테스트(tkinter 불필요). 가짜 api 를 주입한다."""
import errno
import logging
import threading
import time
from types import SimpleNamespace

import pytest

from pcmigrate import api as real_api
from pcmigrate.cancel import Cancelled
from pcmigrate.copier import FileResult, Report
from pcmigrate.detect import Program
from pcmigrate.discover import Candidate, Discovery, Link, ProgramEntry
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError, friendly_error
from pcmigrate.gui.runner import Runner
from pcmigrate.package import ImportPlan
from pcmigrate.transfer import TransferError


def cand(rel, secret=(), size=100, grade="추정", weak=False, root="%APPDATA%"):
    c = Candidate(root, rel, f"C:\\fake\\{rel}", True, Link(["Acme Tool"], grade, "이유", weak), size=size, files=1)
    c.secret = list(secret)
    return c


def discovery():
    d = Discovery()
    d.programs = [ProgramEntry(Program("Acme Tool"), [cand("Acme"), cand("Creds", secret=["credentials.json"])])]
    return d


def plan(items=(), problems=(), warnings=()):
    return ImportPlan({"manual_warnings": list(warnings)}, list(items), list(problems))


def item(status="would_copy", extra=False, target="C:\\x\\a.txt", backup=""):
    return {"entry": "data/a", "target": target, "status": status, "backup": backup, "size": 1, "sha256": "x", "extra": extra}


class FakeApi:
    ApiError = real_api.ApiError

    def __init__(self):
        self.calls = []
        self.preview = real_api.ImportResult(plan([item()]), "dry-run", 0)
        self.transfer_error = None
        self.transfer_wait = None

    def system_info(self):
        return SimpleNamespace(os_name="Test OS", is_admin=False, python="3")

    def run_scan(self, rules_dir, deps=None):
        self.calls.append(("scan",))
        return SimpleNamespace(program_count=3, matched=[], discovery=(1, 2), match_info=[], checklist=None)

    def run_discover(self, rules_dir, *, min_size_kb=0, show_all=False, progress=None, deps=None, cancel=None):
        self.calls.append(("discover",))
        if progress:
            progress("탐지 중")
        return discovery()

    def run_checklist(self, rules_dir, **kw):
        from pcmigrate.checklist import build_checklist, render
        cl = build_checklist([("Demo", ["라이선스 재로그인"])], [Program("Rhino 8")], [Program("a.b", source="winget", winget_id="a.b")],
                             discovery=(1, 2))
        return SimpleNamespace(text=render(cl, "md"), checklist=cl, discovery=(1, 2))

    def run_export(self, rules_dir, output, *, extra=None, allow_secrets=False, deps=None, extra_items=None):
        self.calls.append(("export", output, allow_secrets, extra_items))
        return SimpleNamespace(manifest={"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []})

    def run_import(self, package, rules_dir, *, dry_run=False, yes=False, allow_extra=False, deps=None):
        self.calls.append(("import", dry_run, yes, allow_extra))
        if dry_run or not yes:
            return self.preview
        rep = Report(dry_run=False, results=[FileResult("e", "C:\\x\\a.txt", "copied", 1, "x")])
        return real_api.ImportResult(self.preview.plan, "applied", self.preview.n_extra, rep)

    def run_transfer(self, src, dst, *, dry_run=False, include_cloud=False, strict=False, progress=None, cancel=None,
                     deps=None, warn=None, **kw):
        self.calls.append(("transfer", dry_run, include_cloud, strict))
        if self.transfer_error:
            raise self.transfer_error
        if self.transfer_wait:
            while not cancel():
                if self.transfer_wait.wait(0.01):
                    break
            if cancel():
                raise Cancelled()
        rep = Report(dry_run=dry_run, results=[FileResult("s\\a", "d\\a", "would_copy" if dry_run else "verified", 1, "x")])
        return rep


@pytest.fixture
def env(tmp_path):
    api = FakeApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path,
                   make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    events = []
    c.listener = lambda kind, payload=None: events.append((kind, payload))
    return c, api, events


def kinds(events):
    return [k for k, _ in events]


# --------------------------------------------------------------------- 실행기

def test_runner_threaded_progress_done_error_cancel():
    r = Runner()
    got = []
    r.submit(lambda progress: (progress("a"), progress("b"), 42)[2], on_done=got.append,
             on_error=lambda e, tb: got.append(("err", e)), on_cancel=lambda: got.append("c"),
             on_progress=lambda m: got.append(m))
    assert r.busy and threading.current_thread().name == "MainThread"
    deadline = time.time() + 5
    while r.busy and time.time() < deadline:
        r.pump()
        time.sleep(0.01)
    assert got == ["a", "b", 42] and not r.busy

    r.submit(lambda p: 1 / 0, on_done=got.append, on_error=lambda e, tb: got.append(("err", type(e).__name__, "ZeroDivision" in tb)),
             on_cancel=lambda: None)
    while r.busy:
        r.pump()
        time.sleep(0.01)
    assert got[-1] == ("err", "ZeroDivisionError", True)

    def cancelled(p):
        raise Cancelled()
    r.submit(cancelled, on_done=got.append, on_error=lambda e, tb: None, on_cancel=lambda: got.append("cancelled"))
    while r.busy:
        r.pump()
        time.sleep(0.01)
    assert got[-1] == "cancelled"


def test_runner_callbacks_run_on_pump_thread_and_single_task():
    r = Runner()
    release = threading.Event()
    seen = []
    r.submit(lambda p: release.wait(5), on_done=lambda v: seen.append(threading.current_thread().name),
             on_error=lambda e, tb: None, on_cancel=lambda: None)
    with pytest.raises(RuntimeError):
        r.submit(lambda p: 1, on_done=lambda v: None, on_error=lambda e, tb: None, on_cancel=lambda: None)
    release.set()
    deadline = time.time() + 5
    while r.busy and time.time() < deadline:
        r.pump()
        time.sleep(0.01)
    assert seen == [threading.current_thread().name]  # 콜백은 pump 를 부른(UI) 스레드에서


# --------------------------------------------------------------------- 구 PC 흐름

def test_scan_and_discover_events_and_no_auto_selection(env):
    c, api, ev = env
    c.start_scan()
    assert c.state.scan.program_count == 3 and "scan_done" in kinds(ev)
    assert kinds(ev)[:2] == ["busy", "progress"] and ("busy", None) in ev
    c.start_discover()
    rows = c.candidate_rows()
    assert [r.path.split("\\")[-1] for r in rows] == ["Acme", "Creds"]
    assert c.state.selected == set() and c.state.secrets_ack is False  # 자동 선택/자동 동의 없음
    by = {r.path.split("\\")[-1]: r for r in rows}
    assert by["Creds"].secret and not by["Creds"].selectable and by["Acme"].selectable


def test_secret_items_need_explicit_acknowledgement(env):
    c, api, ev = env
    c.start_discover()
    acme, creds = [r.key for r in c.candidate_rows()]
    c.set_selected(acme, True)
    with pytest.raises(GuardError) as e:
        c.set_selected(creds, True)
    assert "이해했습니다" in str(e.value) and creds not in c.state.selected
    c.set_secrets_ack(True)
    assert next(r for r in c.candidate_rows() if r.key == creds).selectable
    c.set_selected(creds, True)
    assert {acme, creds} == c.state.selected
    c.set_secrets_ack(False)  # 동의를 거두면 비밀 항목만 해제
    assert c.state.selected == {acme}
    with pytest.raises(GuardError):
        c.set_selected(("%APPDATA%", "nope"), True)


def test_export_requires_confirmation_and_passes_only_selected(env, tmp_path):
    c, api, ev = env
    c.start_discover()
    acme, creds = [r.key for r in c.candidate_rows()]
    with pytest.raises(GuardError):
        c.start_export(str(tmp_path / "p.zip"))  # 위치 확인 없이는 불가
    with pytest.raises(GuardError):
        c.start_export("", write_confirmed=True)
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)  # 선택 없음 -> extra 없음(자동 포함 없음)
    assert api.calls[-1][0] == "export" and api.calls[-1][2] is False and api.calls[-1][3] is None
    c.set_selected(acme, True)
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    assert api.calls[-1][3] == [("item", "%APPDATA%", "Acme")] and api.calls[-1][2] is False
    c.set_secrets_ack(True)
    c.set_selected(creds, True)
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    assert api.calls[-1][2] is True  # 비밀 항목을 동의하고 선택했을 때만 allow_secrets
    assert len(api.calls[-1][3]) == 2 and "export_done" in kinds(ev)


def test_checklist_tabs_and_save_text(env, tmp_path):
    c, api, ev = env
    assert c.checklist_tabs() == {}
    with pytest.raises(GuardError):
        c.checklist_text()
    c.start_checklist()
    tabs = c.checklist_tabs()
    assert list(tabs) == list(T.CL_TABS)
    assert any("자동 목록에 없는 프로그램 1개" in l for l in tabs[T.CL_TABS[0]])
    assert any("Rhino 8" in l for l in tabs[T.CL_TABS[1]]) and any("winget import" in l for l in tabs[T.CL_TABS[1]])
    out = tmp_path / "sub" / "c.md"
    c.save_text(str(out), c.checklist_text("md"))
    assert "PC 이전 체크리스트" in out.read_text(encoding="utf-8")
    with pytest.raises(GuardError):
        c.save_text("", "x")
    blocker = tmp_path / "f.txt"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(real_api.ApiError) as e:
        c.save_text(str(blocker / "x.md"), "x")
    assert "출력 경로를 만들 수 없습니다" in str(e.value)


# --------------------------------------------------------------------- 신 PC: 확정 전 쓰기 금지 가드

def apply_calls(api):
    return [x for x in api.calls if x[0] == "import" and x[2]]


def test_no_write_before_preview_checkbox_and_final_confirmation(env):
    c, api, ev = env
    with pytest.raises(GuardError):
        c.start_preview()  # 패키지 없음
    c.set_package("C:\\p.zip")
    with pytest.raises(GuardError):
        c.start_apply()  # 미리보기 전
    assert apply_calls(api) == []
    c.start_preview()
    assert api.calls[-1] == ("import", True, False, False)  # 미리보기는 dry_run
    for why, act in (("확인했습니다", c.set_preview_checked), ("이해했습니다", c.set_bak_understood), ("예", None)):
        ok, reason = c.can_apply()
        assert not ok and why in reason  # 미리보기 확인 -> 백업 파일 이해 -> 확인 창 '예' 순서로 하나씩 요구한다
        with pytest.raises(GuardError):
            c.start_apply()
        if act:
            act(True)
    assert apply_calls(api) == []
    c.confirm_final(True)
    assert c.can_apply() == (True, "")
    c.start_apply()
    assert apply_calls(api) == [("import", False, True, False)]  # --yes 와 같은 호출, allow_extra 없음
    assert "import_done" in kinds(ev)
    assert c.can_apply()[0] is False  # 확정은 한 번만 유효: 다시 쓰려면 새 미리보기 필요
    with pytest.raises(GuardError):
        c.start_apply()
    assert len(apply_calls(api)) == 1


def test_final_confirmation_requires_checkbox_and_unchecking_revokes(env):
    c, api, ev = env
    c.set_package("p.zip")
    c.start_preview()
    c.confirm_final(True)  # 체크박스 없이 대화상자만 눌러도 무효
    assert c.state.final_confirmed is False
    c.set_preview_checked(True)
    c.confirm_final(True)  # 두 번째 확인(백업 파일 이해) 없이는 확정도 무효
    assert c.state.final_confirmed is False
    c.set_bak_understood(True)
    c.confirm_final(True)
    assert c.can_apply()[0]
    c.set_bak_understood(False)  # 두 번째 체크를 풀어도 확정이 취소된다
    assert c.can_apply()[0] is False and c.state.final_confirmed is False
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.set_preview_checked(False)  # 체크를 풀면 확정도 취소
    assert c.can_apply()[0] is False and c.state.final_confirmed is False


def test_changing_package_invalidates_preview_and_blocked_package_cannot_apply(env):
    c, api, ev = env
    c.set_package("a.zip")
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.set_package("b.zip")
    assert c.can_apply()[0] is False and c.state.preview is None and not c.state.preview_checked
    api.preview = real_api.ImportResult(plan([item()], problems=["해시 불일치"]), "blocked", 0)
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    ok, why = c.can_apply()
    assert not ok and why == T.G_PACKAGE_BLOCKED
    with pytest.raises(GuardError):
        c.start_apply()
    assert apply_calls(api) == []


def test_extra_items_need_separate_consent_and_allow_extra_follows_it(env):
    c, api, ev = env
    api.preview = real_api.ImportResult(plan([item(extra=True), item("would_overwrite", target="C:\\y", backup="C:\\y.bak")]),
                                        "dry-run", 1)
    c.set_package("p.zip")
    c.start_preview()
    rows = c.preview_rows()
    assert [r["extra"] for r in rows] == [True, False] and rows[1]["status"].startswith("덮어씀")
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    ok, why = c.can_apply()
    assert not ok and "별도 동의" in why
    c.set_extra_consent(True)
    c.confirm_final(True)
    assert c.can_apply()[0]
    c.start_apply()
    assert apply_calls(api) == [("import", False, True, True)]
    sm = c.import_summary()
    assert sm["counts"] == {"copied": 1} and sm["failures"] == []


def test_import_summary_lists_failures_and_backups(env):
    c, api, ev = env
    c.set_package("p.zip")
    c.start_preview()
    rep = Report(dry_run=False, results=[FileResult("e", "C:\\a", "failed", error="잠김"),
                                         FileResult("e", "C:\\b", "copied", backup="C:\\b.bak")])
    c.state.import_result = real_api.ImportResult(plan(warnings=[{"name": "X", "items": ["w1"]}]), "applied", 0, rep)
    sm = c.import_summary()
    assert sm["failures"] == [("C:\\a", "잠김")] and sm["backups"] == ["C:\\b.bak"] and sm["warnings"] == [("X", "w1")]


# --------------------------------------------------------------------- 대용량 복사

def test_transfer_path_checks(env, tmp_path):
    c, api, ev = env
    src = tmp_path / "src"
    src.mkdir()
    assert not c.check_transfer_paths("", "").ok
    assert not c.check_transfer_paths(str(tmp_path / "none"), str(tmp_path / "d")).ok
    inside = c.check_transfer_paths(str(src), str(src / "inside"))
    assert not inside.ok and inside.lines[-1][1] == T.G_DEST_INSIDE
    assert not c.check_transfer_paths(str(src), str(src)).ok
    good = c.check_transfer_paths(str(src), str(tmp_path / "dst"))
    assert good.ok and good.lines[0][1] == T.G_SOURCE_OK
    unc = c.check_transfer_paths(str(src), "\\\\server\\share\\dst")
    assert unc.ok  # UNC 직접 입력 허용


def test_transfer_requires_preview_then_confirmation(env, tmp_path):
    c, api, ev = env
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    with pytest.raises(GuardError) as e:
        c.start_transfer(dry_run=False, write_confirmed=True)
    assert "미리보기" in str(e.value)
    c.start_transfer(dry_run=True)
    assert api.calls[-1] == ("transfer", True, False, False)
    assert c.transfer_summary()["dry_run"] is True
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False)  # 쓰기 확인 없이는 불가
    c.state.transfer_dst = str(tmp_path / "dst2")  # 대상을 바꾸면 미리보기가 무효
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False, write_confirmed=True)
    c.state.transfer_dst = str(tmp_path / "dst")
    with pytest.raises(GuardError, match="옵션"):  # C-8: 미리보기 때와 옵션이 다르면 시작 불가
        c.start_transfer(dry_run=False, write_confirmed=True, strict=True)
    with pytest.raises(GuardError, match="옵션"):
        c.start_transfer(dry_run=False, write_confirmed=True, include_cloud=True)
    c.start_transfer(dry_run=True, strict=True)  # 바뀐 옵션으로 미리보기를 다시 하면 가능
    c.start_transfer(dry_run=False, write_confirmed=True, strict=True)
    assert api.calls[-1] == ("transfer", False, False, True)  # 클라우드 전용 포함은 기본 꺼짐
    sm = c.transfer_summary()
    assert sm["dry_run"] is False and sm["verified"] == 1 and sm["failures"] == []


def test_transfer_invalid_paths_blocked_even_for_dry_run(env, tmp_path):
    c, api, ev = env
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(src / "in")
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=True)
    assert api.calls == []


def test_transfer_report_saved_on_request(env, tmp_path):
    c, api, ev = env
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    rp = tmp_path / "r.json"
    c.start_transfer(dry_run=True, report_path=str(rp))
    assert rp.exists()
    with pytest.raises(GuardError):
        Controller(api_mod=api, runner=Runner(sync=True)).save_transfer_report(str(tmp_path / "x.json"))
    c.save_transfer_report(str(tmp_path / "again.json"))
    assert (tmp_path / "again.json").exists()


def test_transfer_cancel_and_error_events(tmp_path):
    api = FakeApi()
    api.transfer_wait = threading.Event()
    c = Controller(api_mod=api, runner=Runner(), rules_dir=tmp_path)
    events = []
    c.listener = lambda k, p=None: events.append((k, p))
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    c.state.transfer_preview_key = (c.state.transfer_src, c.state.transfer_dst)
    c.start_transfer(dry_run=False, write_confirmed=True)
    assert c.state.busy and c.can_cancel
    with pytest.raises(GuardError):
        c.start_scan()  # 작업 중에는 새 작업 시작 불가
    assert c.cancel() is True
    deadline = time.time() + 5
    while c.state.busy and time.time() < deadline:
        c.pump()
        time.sleep(0.01)
    assert not c.state.busy and ("cancelled", T.TASK_TRANSFER) in events and "transfer_done" not in kinds(events)
    assert c.cancel() is False  # 작업이 없으면 취소할 것도 없음

    api.transfer_wait = None
    api.transfer_error = TransferError("대상은 원본과 같거나 원본 폴더 안에 있을 수 없습니다")
    c.runner = Runner(sync=True)
    c.start_transfer(dry_run=False, write_confirmed=True)
    err = [p for k, p in events if k == "error"][-1]
    assert err.title == T.ERR_TITLES["transfer"] and "원본 폴더 안" in err.what and "Traceback" not in err.message
    assert err.how == T.ERR_TRANSFER[1]
    assert not c.state.busy


# --------------------------------------------------------------------- 오류 메시지 / 로그

def test_friendly_errors_have_what_how_and_traceback_only_in_detail():
    e1 = friendly_error(real_api.ApiError("규칙 오류: x", lines=["a"]))
    assert e1.what == "a\n자동 목록 오류: x" and e1.how == T.ERR_API_RULES and e1.detail == ""
    e2 = friendly_error(FileNotFoundError(3, "경로 없음", "Z:\\a"), "TB-TEXT")
    assert e2.what == T.ERR_PATH_MISSING[0].format(path=": Z:\\a") and e2.how == T.ERR_PATH_MISSING[1]
    assert e2.detail == "TB-TEXT" and "TB-TEXT" not in e2.message
    e3 = friendly_error(KeyError("boom"), "Traceback (most recent call last)...")
    assert e3.title == T.ERR_TITLES["unexpected"] and "KeyError" not in e3.what and "KeyError" in e3.detail and "Traceback" not in e3.message  # 영문 예외 이름은 상세 보기에만
    assert e3.how == T.ERR_UNEXPECTED[1] and "Traceback" in e3.detail
    g = friendly_error(GuardError("막음"))
    assert g.what == "막음" and g.title == T.ERR_TITLES["guard"]


@pytest.mark.parametrize("exc,texts", [
    (FileNotFoundError(2, "no", "C:\\x"), T.ERR_PATH_MISSING),
    (PermissionError(13, "denied", "C:\\x"), T.ERR_PERMISSION),
    (OSError(errno.ENOSPC, "No space left on device", "C:\\x"), T.ERR_DISK_FULL),
    (OSError(errno.EIO, "I/O error", "E:\\x"), T.ERR_DISCONNECTED),
    (OSError(0, "network", "\\\\srv\\share\\x", 53), T.ERR_DISCONNECTED),  # winerror 53: 네트워크 경로를 찾을 수 없음
    (OSError(0, "sharing violation", "C:\\a.dwg", 32), T.ERR_IN_USE),  # winerror 32: 다른 프로그램이 사용 중
    (OSError(0, "disk full", "C:\\x", 112), T.ERR_DISK_FULL),
    (OSError(0, "access denied", "C:\\x", 5), T.ERR_PERMISSION),
    (OSError(999, "other", "C:\\x"), T.ERR_OS_GENERIC),
])
def test_common_os_errors_map_to_easy_messages(exc, texts):
    e = friendly_error(exc)
    assert e.title == T.ERR_TITLES["os"] and e.how == texts[1]
    assert e.what == texts[0].format(path=f": {exc.filename}")
    assert "Errno" not in e.message and "WinError" not in e.message  # 시스템 오류 문구가 그대로 나오지 않음


def test_api_error_how_hints_by_kind():
    assert friendly_error(real_api.ApiError("패키지 검증 실패: x")).how == T.ERR_API_PACKAGE
    assert friendly_error(real_api.ApiError("비밀 정보가 들어 있을 수 있는 항목이 선택되었습니다. --allow-secrets")).how == T.ERR_API_SECRET
    assert friendly_error(real_api.ApiError("선택 파일 오류: 존재하지 않는 항목")).how == T.ERR_API_SELECTION
    assert friendly_error(real_api.ApiError("뭔가 다른 오류")).how == T.ERR_API_HOW
    # api 문구 속 개발 용어는 쉬운 말로 바뀐다
    assert "robocopy" not in friendly_error(real_api.ApiError("robocopy 종료코드 8")).what


def test_unexpected_exception_in_task_becomes_error_event(env):
    c, api, ev = env

    def boom(*a, **k):
        raise RuntimeError("내부 오류")
    api.run_scan = boom
    c.start_scan()
    err = [p for k, p in ev if k == "error"][0]
    assert "RuntimeError" not in err.message and "RuntimeError" in err.detail and "Traceback" in err.detail and not c.state.busy  # 영문 예외 이름은 상세 보기 안에만


def test_log_contains_events_but_no_program_names_or_paths(tmp_path):
    api = FakeApi()
    api.run_scan = lambda rules_dir, deps=None: SimpleNamespace(program_count=1, matched=[], discovery=(1, 1), match_info=[],
                                                                  checklist=None, secret_marker="TOP-SECRET-APP-NAME")
    log = logging.getLogger("pcmigrate.gui.test")
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
    log.addHandler(H())
    log.setLevel(logging.INFO)
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path, logger=log)
    c.start_scan()
    c.start_discover()
    text = "\n".join(records)
    assert f"task start: {T.TASK_SCAN}" in text and "event scan_done" in text
    for secret in ("TOP-SECRET-APP-NAME", "Acme Tool", "C:\\fake", "credentials.json"):
        assert secret not in text


# --------------------------------------------------------------------- 10단계: 목록 표시/체크/이유/요약/진행 문구

def checklist_env(env):
    from pcmigrate.checklist import build_checklist
    c, api, ev = env
    cl = build_checklist([("Demo", ["라이선스 재로그인"])], [Program("Rhino 8"), Program("Google Chrome")],
                         [Program("a.b", source="winget", winget_id="a.b")], discovery=(1, 2), show_all=True)
    c.state.checklist = SimpleNamespace(checklist=cl, text="", discovery=(1, 2))
    return c, cl


def test_checklist_hide_unrelated_warnings_toggle_matches_all_option(env):
    c, cl = checklist_env(env)
    hidden = c.checklist_hidden_count()
    assert hidden >= 1 and all(l.endswith(T.CL_HIDDEN_MARK) for l in [*cl.login, *cl.data] if l.endswith(T.CL_HIDDEN_MARK))
    login_all = c.checklist_tabs(show_all=True)[T.CL_TABS[2]]
    login_default = c.checklist_tabs()[T.CL_TABS[2]]
    assert len(login_all) > len(login_default) and not any(l.endswith(T.CL_HIDDEN_MARK) for l in login_default)
    summary = c.checklist_tabs()[T.CL_TABS[0]]
    assert any(T.CL_SUMMARY_OMITTED.format(n=hidden) == l for l in summary)  # 숨긴 개수 안내
    assert not any("숨겼" in l for l in c.checklist_tabs(show_all=True)[T.CL_TABS[0]])


def test_checklist_marks_persist_and_are_saved(env, tmp_path):
    c, cl = checklist_env(env)
    items = c.checklist_items(T.CL_TABS[2])
    text = items[0][0]
    assert items[0][1] is True and items[0][2] is False
    c.toggle_check(T.CL_TABS[2], text)
    assert c.checklist_items(T.CL_TABS[2])[0][2] is True  # 화면을 다시 만들어도 유지
    done, total = c.checklist_progress()
    assert done == 1 and total >= 2
    md = c.checklist_text("md")
    assert f"- [x] {text}" in md and "- [ ] " in md
    c.toggle_check(T.CL_TABS[2], text)
    assert f"- [x] {text}" not in c.checklist_text("md")
    # 안내문(체크박스 없는 줄)과 요약/참고 탭에는 체크박스가 없다
    guide = c.checklist_items(T.CL_TABS[1])[:len(T.CL_GUIDE)]
    assert all(not has_box for _t, has_box, _c in guide) and all(not hb for _t, hb, _c in c.checklist_items(T.CL_TABS[0]))
    assert "winget import" in c.checklist_text("txt")
    c.save_text(str(tmp_path / "c.txt"), c.checklist_text("txt"))


def test_checklist_filter_and_show_all_text(env):
    c, cl = checklist_env(env)
    only = c.checklist_tabs(query="Rhino")
    assert all("rhino" in l.lower() for l in only[T.CL_TABS[1]][len(T.CL_GUIDE):])
    assert c.checklist_tabs(query="없는검색어zzz")[T.CL_TABS[2]] == []
    assert len(c.checklist_text("md", show_all=True)) > len(c.checklist_text("md", show_all=False))


def test_reasons_for_disabled_buttons(env, tmp_path):
    c, api, ev = env
    assert c.export_reason("") == T.WHY_NEED_OUTPUT and c.export_reason("  ") == T.WHY_NEED_OUTPUT
    assert c.export_reason("C:/x.zip") == ""
    c.state.busy = True
    assert c.export_reason("C:/x.zip") == T.WHY_BUSY and c.transfer_reasons() == (T.WHY_BUSY, T.WHY_BUSY)
    c.state.busy = False
    assert c.transfer_reasons() == (T.WHY_PATH_PROBLEM, T.WHY_PATH_PROBLEM)
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    assert c.transfer_reasons() == ("", T.WHY_PREVIEW_FIRST)  # 미리보기를 먼저 실행해 주세요
    c.start_transfer(dry_run=True)
    assert c.transfer_reasons() == ("", "")
    # 복원 실행 버튼 이유(확인 창 단계는 이유가 아님)
    assert c.apply_reason() == T.G_NEED_PREVIEW
    c.set_package("p.zip")
    c.start_preview()
    assert c.apply_reason() == T.G_NEED_CHECK_PREVIEW
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    assert c.apply_reason() == ""


def test_result_counts_success_skipped_failed(env):
    c, api, ev = env
    rep = Report(dry_run=False, results=[FileResult("e", "a", "copied"), FileResult("e", "b", "copied"),
                                         FileResult("e", "c", "skipped_identical"), FileResult("e", "d", "failed", error="SHA-256 불일치")])
    c.state.import_result = real_api.ImportResult(plan(), "applied", 0, rep)
    assert c.import_counts() == (2, 1, 1)
    assert c.import_summary()["failures"] == [("d", "내용이 원본과 다름")]  # 개발 용어는 쉬운 말로
    trep = Report(dry_run=False, results=[FileResult("s", "d", "verified"), FileResult("s", "", "cloud_only"),
                                          FileResult("s", "d", "mismatch", error="SHA-256 불일치"),
                                          FileResult("s", "d", "missing", error="대상에 파일이 없습니다")])
    c.state.transfer_report, c.state.transfer_was_dry_run = trep, False
    assert c.transfer_counts() == (1, 1, 2)
    fails = c.transfer_summary()["failures"]
    assert [f[1] for f in fails] == [T.TR_STATUS_LABELS["mismatch"], T.TR_STATUS_LABELS["missing"]]
    assert "SHA" not in " ".join(f[2] for f in fails)
    assert Controller(api_mod=api, runner=Runner(sync=True)).transfer_counts() == (0, 0, 0)


def test_task_start_shows_stage_and_expected_time_and_translates_progress(tmp_path):
    api = FakeApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    ev = []
    c.listener = lambda k, p=None: ev.append((k, p))
    c.start_scan()
    assert ("busy", T.TASK_SCAN) in ev and ("progress", T.PROGRESS[T.TASK_SCAN]) in ev
    assert "10~60초" in T.PROGRESS[T.TASK_SCAN]
    api.run_discover = lambda rules_dir, **kw: (kw["progress"]("robocopy 로 복사하는 중"), discovery())[1]
    c.start_discover()
    assert ("progress", "복사 도구 로 복사하는 중") in ev  # api 진행 문구의 개발 용어는 쉬운 말로
    for task in (T.TASK_SCAN, T.TASK_DISCOVER, T.TASK_CHECKLIST, T.TASK_EXPORT, T.TASK_PREVIEW, T.TASK_APPLY, T.TASK_TRANSFER):
        assert "걸립니다" in T.PROGRESS[task] or "걸릴 수" in T.PROGRESS[task]  # 예상 시간 안내
    assert "아무것도 쓰지 않습니다" in T.PROGRESS[T.TASK_TRANSFER_PREVIEW]
