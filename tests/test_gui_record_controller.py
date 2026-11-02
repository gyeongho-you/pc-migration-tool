"""바꿔 보며 설정 찾기(설정 기록 모드) GUI 컨트롤러 헤드리스 시험(tkinter 불필요, stage60).

가짜 api(FakeRecordApi)로 컨트롤러의 가드/이벤트/취소/오류 문구를 보고, 실제 record_mode 와의 통합은 가짜 AppData(환경변수 주입)와
주입한 설치 목록만 쓴다(실제 레지스트리·사용자 폴더는 읽지 않는다).
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_rules_dir
from pcmigrate import api as real_api
from pcmigrate import envpaths, record_infer as ri, record_mode as rm
from pcmigrate.cancel import Cancelled
from pcmigrate.detect import Program
from pcmigrate.gui import record_flow as rf
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller
from pcmigrate.gui.runner import Runner
from pcmigrate.rules import parse_rule

from test_gui_controller import FakeApi
from test_record_mode import USER, Fake


# ------------------------------------------------------------------ 가짜 api
def cand(path, conf="높음", size=100, **kw):
    d = dict(path=path, confidence=conf, size=size, ext=".ini", hits=1, rounds=1, change="수정", reason="이유", masked=False,
             noise_like=False, hash_unknown=False, removed=False, promoted=False, secret=False)
    d.update(kw)
    return SimpleNamespace(**d)


def analysis(cands, **kw):
    a = dict(program="Foo", rounds=2, baselines=1, candidates=cands, noise_files=3, mtime_only=0, unreadable=0, warnings=[],
             registry_likely=False, undetermined=False, truncated=False, outside=[], root_tokens=[])
    a.update(kw)
    return SimpleNamespace(**a)


def snap_result(**kw):
    d = dict(label="before", status="ok", files=12, hashed=1, carried=0, unreadable=0, cloud_skipped=0, links_skipped=0, truncated=False,
             running=[], warnings=[], root_notes=[], matched=[], inferred=[], inferred_excluded=[])
    d.update(kw)
    return SimpleNamespace(**d)


class FakeSession:
    def __init__(self, work="C:\\x\\rec"):
        self.work = work
        self.snaps = []

    def add(self, kind, label, n=10):
        self.snaps.append({"label": label, "kind": kind, "file": f"snap-{label}.json", "taken_at": "2026-11-02T10:00:00", "files": n})


class FakeRecordApi(FakeApi):
    def __init__(self):
        super().__init__()
        self.programs = [real_api.RecordProgram(Program("Foo Edit", "1", "Acme Inc.", install_location="C:\\Program Files\\Acme\\Foo Edit"), False),
                         real_api.RecordProgram(Program("Revit 2024", "2024", "Autodesk"), True)]
        self.plan = None
        self.running = []
        self.resume = None
        self.rcalls = []
        self.raises = {}  # 호출 이름 -> 던질 예외
        self.diff_result = analysis([cand("%APPDATA%\\Foo\\a.ini")])
        self.draft_result = None
        self.reset_result = SimpleNamespace(moved=[("a", "a.bak")] * 3, kept_foreign=[], status="ok")

    def _maybe(self, name):
        self.rcalls.append(name)
        if name in self.raises:
            raise self.raises[name]

    def run_record_programs(self, rules_dir, deps=None):
        self._maybe("programs")
        return list(self.programs)

    def run_record_infer(self, program, exe_path="", **kw):
        self._maybe("infer")
        plan = self.plan or ri.Inference(ri.ProgramInfo(program.name), [ri.Folder("%APPDATA%\\Acme\\Foo Edit", "C:\\x", "게시자(Acme Inc.) 폴더 아래의 프로그램 이름과 일치", "높음")],
                                         [("%APPDATA%\\Acme", "제조사 폴더 전체")])
        return plan, list(self.running)

    def record_resume(self, work):
        return self.resume

    def run_record_start(self, program, plan, work=None, **kw):
        self._maybe("start")
        self.rcalls.append(("start_folders", [f.token for f in plan.enabled()]))
        s = FakeSession(work or "C:\\x\\rec")
        s.add("before", "before")
        return s, snap_result()

    def run_record_snapshot(self, session, **kw):
        self._maybe("snapshot")
        kinds = [x["kind"] for x in session.snaps]
        if "after" in kinds or "baseline" in kinds:
            n = sum(1 for k in kinds if k == "after") + 1
            session.add("after", f"after{n}")
        else:
            session.add("baseline", "baseline")
        return snap_result(files=13)

    def run_record_diff(self, session):
        self._maybe("diff")
        return self.diff_result

    def run_record_draft(self, session, output, selected):
        self._maybe("draft")
        self.rcalls.append(("draft_args", output, set(selected)))
        if self.draft_result is not None:
            return self.draft_result
        rule = {"id": "foo-edit", "paths": [{"path": "%APPDATA%\\Foo\\a.ini", "kind": "file"}, {"path": "%APPDATA%\\Bar", "kind": "dir"}]}
        return self.diff_result, rule, Path("C:\\out\\rule.json")

    def run_record_reset(self, work):
        self._maybe("reset")
        return self.reset_result


@pytest.fixture
def renv(tmp_path):
    api = FakeRecordApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    events = []
    c.listener = lambda kind, payload=None: events.append((kind, payload))
    return c, api, events


def to_steps(c, api):
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.record_pick(0)
    c.record_find_folders()
    c.record_start()
    return c.record_state


# ------------------------------------------------------------------ 화면 이름/문구
def test_screen_registration_and_texts_follow_the_rules():
    assert S.RECORD in T.TITLES and T.TITLES[S.RECORD].startswith("① 쓰던 PC에서 준비하기")
    assert S.overall_step(S.RECORD) is None and S.overall_names(S.RECORD) == ()  # 맨 위 '전체 단계 n/4' 는 표시하지 않는다
    for part in ("무엇을 하는 화면인가요?", "안전한가요?", "다음 단계"):
        assert part in T.HELP[S.RECORD]
    assert len(T.HELP[S.RECORD]) < 700 and T.REC_HEADING == T.TITLES[S.RECORD]
    assert len(T.REC_STEP_NAMES) == 6 and T.BTN_REC_ENTER == "바꿔 보며 설정 찾기 ▸" and T.BTN_REC_RESET == "처음부터 다시"
    assert ".bak" in T.REC_RESET_CONFIRM and "지우지 않고" in T.REC_RESET_CONFIRM
    for name in dir(T):
        if name.startswith(("REC_", "BTN_REC_", "G_REC_")):
            val = getattr(T, name)
            blob = " ".join(val.values() if isinstance(val, dict) else val) if isinstance(val, (dict, tuple)) else str(val)
            assert "--" not in blob and "pcmigrate" not in blob, name  # 명령줄 낱말 금지
    assert T.TASK_REC_SNAP in T.PROGRESS and T.TASK_REC_SNAP in T.TASK_STEPS


# ------------------------------------------------------------------ 화면 A
def test_open_sets_back_screen_and_program_list_filters(renv):
    c, api, ev = renv
    c.record_open(S.OLD_DISCOVER, "Foo Edit")
    assert c.state.screen == S.RECORD and c.record_back_screen() == S.OLD_DISCOVER
    c.record_load_programs()
    assert c.record_state.program.name == "Foo Edit"  # ① 화면에서 고른 프로그램이 미리 채워진다
    rows = c.record_program_rows()
    assert [r["name"] for r in rows] == ["Foo Edit 1"] and rows[0]["where"] == "...\\Acme\\Foo Edit"  # 자동 목록이 있는 것은 기본으로 숨김
    c.record_set_filter(only_norule=False)
    assert len(c.record_program_rows()) == 2
    c.record_set_filter(text="revit")
    assert [r["name"] for r in c.record_program_rows()] == ["Revit 2024"]
    c.record_load_programs()
    assert api.rcalls.count("programs") == 1  # 이미 읽었으면 다시 읽지 않는다
    with pytest.raises(Exception) as e:
        c.record_pick(99)
    assert T.G_REC_PICK in str(e.value)
    assert USER not in rows[0]["where"]


# ------------------------------------------------------------------ 화면 B
def test_folders_plan_toggle_add_and_start_guards(renv, tmp_path):
    c, api, ev = renv
    api.running = ["FooEdit.exe"]
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.record_pick(0)
    c.record_find_folders()
    st = c.record_state
    assert st.room == rf.ROOM_FOLDERS and st.running == ["FooEdit.exe"]
    rows = c.record_folder_rows()
    assert [(r["on"], r["conf"], r["candidate"]) for r in rows] == [(True, "높음", False), (False, T.REC_EXCLUDED_CONF, True)]
    assert "게시자(Acme Inc.)" in rows[0]["reason"]
    c.record_toggle_folder("%APPDATA%\\Acme\\Foo Edit", False)
    assert c.record_can_start() == (False, T.G_REC_NO_FOLDER)
    with pytest.raises(Exception) as e:
        c.record_start()
    assert T.G_REC_NO_FOLDER in str(e.value) and "start" not in api.rcalls
    c.record_toggle_folder("%APPDATA%\\Acme", True)  # 제외 후보를 켜면 '추가'한 폴더가 된다
    assert c.record_folder_rows()[-1]["conf"] == T.REC_ADDED_CONF and c.record_can_start() == (True, "")
    extra = tmp_path / "mine"
    extra.mkdir()
    c.record_add_folder(str(extra))
    assert any(r["reason"] == "직접 추가한 폴더" for r in c.record_folder_rows())
    with pytest.raises(Exception) as e:
        c.record_add_folder(str(tmp_path / "nope"))
    assert "찾지 못했습니다" in str(e.value) and "--" not in str(e.value)
    c.record_start()
    assert ("start_folders", ["%APPDATA%\\Acme", c.record_state.plan.folders[-1].token]) in api.rcalls
    assert st.room == rf.ROOM_STEPS and st.session is not None and "12" in st.notes[0]
    with pytest.raises(Exception):
        c.record_toggle_folder("%APPDATA%\\Acme", False)  # 기록을 시작한 뒤에는 폴더를 바꾸지 않는다


def test_no_folder_found_does_not_scan_everything(renv):
    c, api, ev = renv
    api.plan = ri.Inference(ri.ProgramInfo("Foo Edit"), [], [("%APPDATA%\\Acme", "제조사 폴더 전체")], notes=["--all-roots"])
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.record_pick(0)
    c.record_find_folders()
    assert c.record_can_start() == (False, T.G_REC_NO_FOLDER)
    assert "start" not in api.rcalls


def test_previous_record_requires_resume_or_reset(renv):
    c, api, ev = renv
    prev = FakeSession()
    prev.add("before", "before")
    prev.add("baseline", "baseline")
    api.resume = prev
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.record_pick(0)
    c.record_find_folders()
    st = c.record_state
    assert st.previous["done"] == "기준선" and st.previous["when"] == "2026-11-02"
    assert c.record_can_start() == (False, T.G_REC_HAS_PREVIOUS) and c.record_can_reset()
    c.record_resume()
    assert st.session is prev and st.room == rf.ROOM_STEPS and rf.next_action(prev) == "after"
    assert rf.current_step(st) == 4


# ------------------------------------------------------------------ 화면 C/D/E
def test_steps_candidates_and_draft_flow(renv):
    c, api, ev = renv
    st = to_steps(c, api)
    assert rf.current_step(st) == 3 and rf.next_action(st.session) == "baseline"
    with pytest.raises(Exception) as e:
        c.record_show_candidates()  # 변경 후 기록이 없으면 후보를 볼 수 없다
    assert "프로그램을 열고 설정을 딱 하나만" in str(e.value) or str(e.value)
    c.record_snapshot()
    assert rf.next_action(st.session) == "after" and rf.current_step(st) == 4 and "기준선" in st.notes[0]
    c.record_snapshot()
    assert rf.after_count(st.session) == 1 and "변경 후 1번째" in st.notes[0]
    assert rf.log_lines(st.session)[-1].endswith("변경 후 1번째 · 파일 10개")
    api.diff_result = analysis([
        cand("%APPDATA%\\Foo\\a.ini", "높음"), cand("%APPDATA%\\Foo\\b.xml", "중간"), cand("%APPDATA%\\Foo\\c.dat", "낮음", ext=".dat"),
        cand("%APPDATA%\\Foo\\old.ini", "낮음", masked=True), cand("%APPDATA%\\Foo\\gone.ini", "낮음", removed=True),
        cand("%APPDATA%\\Foo\\token.json", "높음", secret=True), cand("%APPDATA%\\Foo\\x.log", "낮음", noise_like=True)])
    c.record_show_candidates()
    assert st.room == rf.ROOM_CANDS
    by = {r["path"].rsplit("\\", 1)[-1]: r for r in st.rows}
    assert set(by) == {"a.ini", "b.xml", "c.dat", "old.ini", "gone.ini", "x.log"}  # 민감 파일은 표에서 빠진다
    assert [r["path"].rsplit("\\", 1)[-1] for r in st.secret_rows] == ["token.json"]
    assert by["a.ini"]["checked"] and by["b.xml"]["checked"] and not by["c.dat"]["checked"] and by["c.dat"]["checkable"]  # 기본은 높음·중간만 체크
    assert not by["old.ini"]["checkable"] and by["old.ini"]["conf"] == T.REC_CONF_REF and not by["gone.ini"]["checkable"] and not by["x.log"]["checkable"]
    assert c.record_toggle_candidate("%APPDATA%\\Foo\\old.ini", True) is False and "%APPDATA%\\Foo\\old.ini" not in st.checked
    assert c.record_toggle_candidate("%APPDATA%\\Foo\\c.dat", True) is True
    assert c.record_toggle_candidate("%APPDATA%\\Foo\\a.ini", False) is True
    assert st.checked == {"%APPDATA%\\Foo\\b.xml", "%APPDATA%\\Foo\\c.dat"}
    c.record_goto_draft()
    assert st.room == rf.ROOM_DRAFT and rf.current_step(st) == 6
    c.record_save_draft("")
    assert ("draft_args", None, {"%APPDATA%\\Foo\\b.xml", "%APPDATA%\\Foo\\c.dat"}) in api.rcalls  # 체크한 후보만 백엔드로
    assert st.draft == {"path": str(Path("C:\\out\\rule.json")), "d": 1, "f": 1, "rid": "foo-edit"}
    text = c.record_draft_text()
    assert "확인 필요" in text and "폴더 1개, 파일 1개" in text and "--" not in text
    c.record_toggle_candidate("%APPDATA%\\Foo\\a.ini", True)
    c.record_toggle_candidate("%APPDATA%\\Foo\\b.xml", False)
    c.record_toggle_candidate("%APPDATA%\\Foo\\c.dat", False)
    c.record_toggle_candidate("%APPDATA%\\Foo\\a.ini", False)
    with pytest.raises(Exception) as e:
        c.record_save_draft("")
    assert T.G_REC_NONE_CHECKED in str(e.value)


def test_new_snapshot_invalidates_old_candidate_table(renv):
    c, api, ev = renv
    st = to_steps(c, api)
    c.record_snapshot()
    c.record_snapshot()
    c.record_show_candidates()
    assert st.analysis is not None
    c.record_goto(rf.ROOM_STEPS)
    c.record_snapshot()  # 한 번 더 바꿔 본 뒤에는 이전 후보 표는 무효
    assert st.analysis is None and st.rows == [] and st.checked == set() and rf.after_count(st.session) == 2


def test_notices_never_show_command_words_and_registry_hint(renv):
    c, api, ev = renv
    st = to_steps(c, api)
    c.record_snapshot()
    c.record_snapshot()
    api.diff_result = analysis([], registry_likely=True, warnings=["--max-files --all-roots"])
    c.record_show_candidates()
    level, text = c.record_candidates_notice()
    assert (level, text) == ("warn", T.REC_REGISTRY) and "레지스트리" in text and "--" not in text
    for kw, want in ((dict(undetermined=True, truncated=True), T.REC_TRUNCATED), (dict(undetermined=True), T.REC_BASELINE_ONLY)):
        api.diff_result = analysis([cand("%APPDATA%\\a.ini", masked=True)], **kw)
        c.record_goto(rf.ROOM_STEPS)
        c.record_show_candidates()
        assert c.record_candidates_notice() == ("warn", want)
    a = analysis([cand("%APPDATA%\\a.ini", secret=True)])
    assert rf.no_draft_text(a) == T.REC_ONLY_SECRET
    assert rf.no_draft_text(analysis([cand("x", removed=True)])) == T.REC_ONLY_REMOVED
    assert rf.no_draft_text(analysis([], outside=["D:\\x"])) == T.REC_OUTSIDE
    st.checked = {"%APPDATA%\\a.ini"}
    api.draft_result = (analysis([], registry_likely=True), None, None)
    st.analysis = analysis([cand("%APPDATA%\\a.ini")])
    c.record_save_draft("")
    assert st.draft is None and c.record_draft_text().startswith("자동 목록 초안을 만들지 않았습니다.")


# ------------------------------------------------------------------ 취소/오류/강제 중단
def test_cancel_keeps_the_same_step_and_reports(renv):
    c, api, ev = renv
    st = to_steps(c, api)
    api.raises["snapshot"] = Cancelled()
    c.record_snapshot()
    assert ("cancelled", T.TASK_REC_SNAP) in ev and st.session.snaps[-1]["kind"] == "before" and st.room == rf.ROOM_STEPS
    assert not c.state.busy and "저장하지 않았습니다" in c.stop_done_note(T.TASK_REC_SNAP)
    assert c.stop_done_note(T.TASK_REC_DIFF) == T.STOP_DONE_REC
    del api.raises["snapshot"]
    c.record_snapshot()  # 같은 단계를 다시 할 수 있다
    assert st.session.snaps[-1]["kind"] == "baseline"


def test_start_cancel_leaves_folders_room(renv):
    c, api, ev = renv
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.record_pick(0)
    c.record_find_folders()
    api.raises["start"] = Cancelled()
    c.record_start()
    assert c.record_state.session is None and c.record_state.room == rf.ROOM_FOLDERS and ("cancelled", T.TASK_REC_SNAP) in ev


def test_backend_user_errors_become_plain_messages_and_unexpected_errors_are_logged(renv):
    c, api, ev = renv
    logged = []
    c.record_error = lambda exc, where, tb=None: (logged.append((type(exc).__name__, where)), None)[1]
    st = to_steps(c, api)
    api.raises["snapshot"] = rm.RecordError("이미 기록이 있습니다. `record snapshot` 또는 --force 를 쓰세요(--all-roots)")
    c.record_snapshot()
    errs = [p for k, p in ev if k == "error"]
    assert errs and "--" not in errs[-1].what and "--" not in errs[-1].how and not c.state.busy
    api.raises["snapshot"] = RuntimeError("boom")
    c.record_snapshot()
    assert ("RuntimeError", "GUI record_settings / " + T.TASK_REC_SNAP) in logged  # 예상 밖 오류는 오류 로그에 남는다
    assert not c.state.busy and st.room == rf.ROOM_STEPS


def test_busy_blocks_second_task_and_record_tasks_are_writing_tasks(renv):
    from pcmigrate.gui import controller as ctl
    assert {T.TASK_REC_SNAP, T.TASK_REC_DRAFT, T.TASK_REC_RESET} <= set(ctl._WRITING_TASKS)
    c, api, ev = renv
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.state.busy = True
    with pytest.raises(Exception) as e:
        c.record_pick(0) or c.record_find_folders()
    assert T.G_BUSY in str(e.value)


# ------------------------------------------------------------------ 처음부터 다시
def test_reset_moves_back_to_pick_and_reports(renv):
    c, api, ev = renv
    st = to_steps(c, api)
    assert c.record_can_reset() and T.REC_RESET_TITLE and ".bak" in c.record_reset_message()
    c.record_reset()
    assert st.session is None and st.room == rf.ROOM_PICK and st.plan is None and st.analysis is None and st.checked == set()
    assert "3개를 보관했습니다(삭제하지 않음)" in st.notes[0] and not c.record_can_reset()
    api.reset_result = SimpleNamespace(moved=[], kept_foreign=["my-notes.txt"], status="nothing")
    st.session = FakeSession()
    c.record_reset()
    assert st.notes[0] == T.REC_RESET_NONE and "my-notes.txt" in st.notes[1]
    api.raises["reset"] = rm.RecordError("작업 폴더 밖을 가리키는 링크라 초기화하지 않습니다")
    st.session = FakeSession()
    c.record_reset()
    assert [p for k, p in ev if k == "error"] and st.session is not None  # 실패하면 상태는 그대로


# ------------------------------------------------------------------ 실제 record_mode 통합(가짜 AppData + 가짜 설치 목록)
@pytest.fixture
def real(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    envpaths.set_overrides({"USERPROFILE": str(f.home), "HOME": str(f.home), "APPDATA": str(f.app), "LOCALAPPDATA": str(f.loc),
                            "PROGRAMDATA": str(f.pd)})
    rules = make_rules_dir(tmp_path / "rules")
    deps = real_api.Deps(registry=lambda: [Program("Foo Edit", "2025", "Acme Inc.",
                                                  install_location="C:\\Program Files\\Acme\\Foo Edit 2025"),
                                         Program("Other Tool", "1", "Other Co.")])
    c = Controller(api_mod=real_api, deps=deps, runner=Runner(sync=True), rules_dir=rules)
    c.record_documents_fn = lambda: str(f.docs)
    c.record_process_list_fn = lambda: []
    c.record_version_info_fn = lambda p: {}
    c.record_work_dir_fn = lambda name: str(f.work)
    events = []
    c.listener = lambda kind, payload=None: events.append((kind, payload))
    yield SimpleNamespace(f=f, c=c, ev=events, tmp=tmp_path)
    envpaths.set_overrides(None)


def test_integration_full_flow_with_real_record_mode(real):
    f, c = real.f, real.c
    z = f.app / "Acme" / "Foo Edit 2025"
    ini = f.put(z / "settings.ini", "a=1")
    f.put(z / "keys.xml", "k=1")
    f.put(z / "app.log", "l1")
    f.put(f.app / "Acme" / "Other App" / "o.ini", "o")
    f.put(z / "token.json", "{}")
    c.record_open(S.OLD_DISCOVER, "Foo Edit")
    c.record_load_programs()
    assert [r["name"] for r in c.record_program_rows()] == ["Foo Edit 2025", "Other Tool 1"]  # 규칙(더미) 은 이 두 프로그램과 맞지 않음
    c.record_pick(c.record_program_rows()[0]["index"])
    c.record_find_folders()
    st = c.record_state
    assert [r["token"] for r in c.record_folder_rows() if r["on"]] == ["%APPDATA%\\Acme\\Foo Edit 2025"]
    c.record_start()
    assert st.room == rf.ROOM_STEPS and st.session.snaps[0]["files"] == 4
    f.put(z / "app.log", "l12")  # 기준선 동안 로그만 바뀜
    c.record_snapshot()
    f.put(z / "app.log", "l22")
    f.put(z / "token.json", "{\"t\": 1}")  # 바뀐 로그인 파일: 표에서 빠지고 따로 알린다
    f.put(ini, "a=2")
    f.put(z / "keys.xml", "k=2")
    c.record_snapshot()
    c.record_show_candidates()
    names = {r["path"].rsplit("\\", 1)[-1]: r for r in st.rows}
    assert {"settings.ini", "keys.xml"} <= set(names) and names["settings.ini"]["checked"] and "app.log" not in names
    assert [r["path"].rsplit("\\", 1)[-1] for r in st.secret_rows] == ["token.json"] and "token.json" not in names
    c.record_goto_draft()
    out = real.tmp / "rules_out" / "foo.json"
    c.record_save_draft(str(out))
    rule = json.loads(out.read_text("utf-8"))
    assert parse_rule(rule) and rule["paths"][0]["path"].startswith("%APPDATA%\\Acme\\Foo Edit 2025") and "token.json" not in json.dumps(rule["paths"])
    assert st.draft["f"] + st.draft["d"] == len(rule["paths"])
    blob = "".join(p.read_text("utf-8") for p in f.work.iterdir() if p.suffix in (".json",)) + out.read_text("utf-8")
    assert USER not in blob and "token.json" not in "".join(r["path"] for r in st.rows)
    rep = json.loads((f.work / rm.REPORT_FILE).read_text("utf-8"))
    assert rep["op"] == "rule-draft" and rep["status"] == "ok"  # 결과 보고서는 항상 남는다
    # 처음부터 다시: 지우지 않고 .bak 으로 보관
    c.record_reset()
    names_after = sorted(p.name for p in f.work.iterdir())
    assert "snap-before.json.bak" in names_after and "record-state.json" not in names_after and st.session is None and st.room == rf.ROOM_PICK
    assert (f.work / "snap-before.json.bak").exists() and ini.read_text("utf-8") == "a=2"  # 기록 대상은 그대로


def test_integration_cancel_midway_and_no_folder_and_errors_logged(real):
    f, c = real.f, real.c
    f.put(f.app / "Acme" / "Foo Edit 2025" / "s.ini", "1")
    c.record_open(S.OLD_DISCOVER)
    c.record_load_programs()
    c.record_pick(0)
    c.record_find_folders()
    # 취소: 백엔드가 스냅샷을 저장하지 않는다
    orig = real_api.run_record_start

    def cancelling(*a, **k):
        k["cancel"] = lambda: True
        return orig(*a, **k)
    real_api.run_record_start = cancelling
    try:
        c.record_start()
    finally:
        real_api.run_record_start = orig
    st = c.record_state
    assert st.session is None and ("cancelled", T.TASK_REC_SNAP) in real.ev
    assert not (f.work / "snap-before.json").exists() and json.loads((f.work / rm.REPORT_FILE).read_text("utf-8"))["status"] == "cancelled"
    c.record_start()  # 다시 시작할 수 있다
    assert st.session is not None
    # 폴더를 못 찾는 프로그램은 전체 훑기를 하지 않는다
    c2 = real.c
    c2.record_state.session = None
    c2.record_state.programs_loaded = True
    c2.record_state.program = Program("Zzyzx Tool")
    c2.record_state.previous = None
    c2.record_find_folders()
    assert c2.record_can_start()[0] is False
