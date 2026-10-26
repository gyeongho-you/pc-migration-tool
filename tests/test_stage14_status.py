"""v0.1.1 수정 3: 현황 카드(상태 계산은 tkinter 없이, 화면은 스모크), 전체 단계 표시, 엔진 이벤트의 새 필드."""
from conftest import make_rules_dir
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import api
from pcmigrate.copier import FileResult, Report
from pcmigrate.gui import state as S
from pcmigrate.gui import status as ST
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner
from pcmigrate.progress import (FAILED_KIND, OK, SKIPPED, UNREADABLE, ProgressInfo, ProgressTracker, categorize,
                                format_info)

from test_gui_controller import FakeApi
from test_package import env as pkg_env, make_rule  # noqa: F401  (fixture)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def info(phase, done_files=0, total_files=0, **kw):
    return ProgressInfo(phase, kw.pop("done_bytes", 0), kw.pop("total_bytes", 0), done_files, total_files, **kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def model(clock):
    return ST.StatusModel(clock)


# ----------------------------------------------------------------------------- 순수 상태 계산

def test_idle_snapshot_is_inactive(model):
    snap = model.snapshot()
    assert snap.state == ST.IDLE and snap.headline == T.CARD_IDLE and snap.steps == () and not model.active


def test_headline_matches_spec_example(model):
    model.begin(T.TASK_TRANSFER)
    model.info(info("scan", 5))
    model.info(info("copy", 12431, 50003, done_bytes=1, total_bytes=100, speed_bps=1000.0, eta_seconds=60.0))
    assert model.snapshot().headline == "② 복사 중 — 12,431 / 50,003개 파일 (24%)"


def test_steps_bar_marks_done_current_todo_and_final_step(model):
    model.begin(T.TASK_TRANSFER)
    snap = model.snapshot()
    assert [s.label for s in snap.steps] == ["조사", "복사", "검사", T.CARD_STEP_DONE]
    assert [s.state for s in snap.steps] == [ST.STEP_CURRENT, ST.STEP_TODO, ST.STEP_TODO, ST.STEP_TODO]
    assert [s.no for s in snap.steps] == ["①", "②", "③", "④"]
    model.info(info("copy", 1, 10))
    assert [s.state for s in model.snapshot().steps] == [ST.STEP_DONE, ST.STEP_CURRENT, ST.STEP_TODO, ST.STEP_TODO]
    model.info(info("scan", 3))  # 단계는 뒤로 가지 않는다
    assert model.snapshot().stage_text == "② 복사"
    model.info(info("verify", 1, 10))
    assert model.snapshot().headline.startswith("③ 검사 중")
    model.finish()
    snap = model.snapshot()
    assert all(s.state == ST.STEP_DONE for s in snap.steps) and snap.headline.startswith("④ 완료")


def test_non_applicable_task_uses_its_own_steps(model):
    model.begin(T.TASK_VERIFY)
    assert [s.label for s in model.snapshot().steps] == ["검사", T.CARD_STEP_DONE]
    model.begin(T.TASK_SCAN)
    assert [s.label for s in model.snapshot().steps] == ["목록 읽기", "규칙 맞추기", T.CARD_STEP_DONE]
    model.begin("알 수 없는 작업")
    assert [s.label for s in model.snapshot().steps] == ["알 수 없는 작업", T.CARD_STEP_DONE]


def test_every_task_has_steps_and_valid_phase_map():
    for task, steps in T.TASK_STEPS.items():
        for idx in ST.PHASE_MAP.get(task, {}).values():
            assert 0 <= idx < len(steps), task
    for task in (T.TASK_SCAN, T.TASK_DISCOVER, T.TASK_CHECKLIST, T.TASK_EXPORT, T.TASK_PREVIEW, T.TASK_APPLY,
                 T.TASK_TRANSFER, T.TASK_TRANSFER_PREVIEW, T.TASK_VERIFY, T.TASK_VERIFY_AGAIN, T.TASK_RETRY):
        assert task in T.TASK_STEPS


def test_metrics_files_bytes_speed_elapsed_and_eta(model, clock):
    model.begin(T.TASK_TRANSFER)
    clock.t += 125
    model.info(info("copy", 10, 40, done_bytes=2 * 1024 ** 3, total_bytes=8 * 1024 ** 3, speed_bps=100 * 1024 ** 2, eta_seconds=3600.0))
    m = dict(model.snapshot().metrics)
    assert m[T.CARD_METRIC_FILES] == "10 / 40개"
    assert m[T.CARD_METRIC_BYTES] == "2.0GB / 8.0GB"
    assert m[T.CARD_METRIC_SPEED] == "100.0MB/s"
    assert m[T.CARD_METRIC_ELAPSED] == "2분"
    assert m[T.CARD_METRIC_ETA] == T.CARD_ETA_ESTIMATE.format(eta="1시간")


def test_eta_unknown_says_computing_and_no_total_files_shows_count_only(model):
    model.begin(T.TASK_TRANSFER)
    model.info(info("copy", 4, 0, done_bytes=10, total_bytes=100, eta_seconds=None))
    snap = model.snapshot()
    assert dict(snap.metrics)[T.CARD_METRIC_ETA] == T.CARD_ETA_COMPUTING
    assert "파일 4개 확인함" in snap.headline
    model.begin(T.TASK_SCAN)
    assert dict(model.snapshot().metrics)[T.CARD_METRIC_ETA] == T.CARD_ETA_COMPUTING  # 이벤트가 아직 없어도 '계산 중'


def test_elapsed_freezes_after_finish(model, clock):
    model.begin(T.TASK_SCAN)
    clock.t += 30
    model.finish()
    clock.t += 500
    assert dict(model.snapshot().metrics)[T.CARD_METRIC_ELAPSED] == "30초"


def test_current_item_is_ellipsized_with_full_path_kept(model):
    long = "D:\\" + "폴더" * 40 + "\\파일.dwg"
    model.begin(T.TASK_TRANSFER)
    model.info(info("copy", 1, 2, current=long))
    snap = model.snapshot()
    assert snap.current == long and len(snap.current_short) <= ST.CURRENT_SHORT_LIMIT and "…" in snap.current_short
    assert snap.current_short.endswith("파일.dwg")


def test_chips_zero_vs_nonzero_highlight_with_icon_and_text(model):
    model.begin(T.TASK_TRANSFER)
    model.info(info("verify", 5, 10, ok=5))
    chips = {c.key: c for c in model.snapshot().chips}
    assert [c.key for c in model.snapshot().chips] == [OK, SKIPPED, UNREADABLE, FAILED_KIND]
    assert chips[OK].level == "ok" and chips[SKIPPED].level == chips[UNREADABLE].level == chips[FAILED_KIND].level == "muted"
    assert not any(c.clickable for c in chips.values())
    model.info(info("verify", 6, 10, ok=5, skipped=2, unreadable=1, failed=3))
    chips = {c.key: c for c in model.snapshot().chips}
    assert chips[SKIPPED].level == "info" and chips[UNREADABLE].level == "warn" and chips[FAILED_KIND].level == "fail"
    assert chips[UNREADABLE].clickable and chips[FAILED_KIND].clickable and not chips[OK].clickable
    for c in chips.values():  # 색만으로 구분하지 않는다: 아이콘과 문구가 항상 있다
        assert c.icon and c.label
    assert len({c.icon for c in chips.values()}) == 4
    assert chips[UNREADABLE].label == "읽지 못함(사용 중)"


def test_problem_line_none_and_some(model):
    model.begin(T.TASK_TRANSFER)
    model.info(info("verify", 1, 10, ok=1))
    snap = model.snapshot()
    assert snap.problem_line == T.CARD_PROBLEM_NONE and "지금까지 문제 없음" in snap.problem_line and not snap.has_problem
    model.info(info("verify", 2, 10, ok=1, unreadable=2, failed=1))
    snap = model.snapshot()
    assert snap.has_problem and "읽지 못함(사용 중) 2개" in snap.problem_line and "실패 1개" in snap.problem_line
    assert T.ICON_FAIL in snap.problem_line


def test_counters_accumulate_problems_across_phases_but_ok_is_current_phase(model):
    model.begin(T.TASK_TRANSFER)
    model.info(info("scan", 100, skipped=3, failed=1))
    model.info(info("copy", 50, 100, ok=50))
    model.info(info("verify", 10, 100, ok=10, failed=2))
    c = {x.key: x.count for x in model.snapshot().chips}
    assert c == {OK: 10, SKIPPED: 3, UNREADABLE: 0, FAILED_KIND: 3}


def test_finish_counts_override_and_problem_lists(model):
    model.begin(T.TASK_EXPORT)
    model.info(info("pack", 1, 5, ok=1))
    model.finish({"ok": 4, "skipped": 1, "unreadable": 1, "failed": 0}, {"unreadable": ["C:\\a\\LOCK"], "skipped": ["x"]}, "파일 4개")
    c = {x.key: x.count for x in model.snapshot().chips}
    assert c == {OK: 4, SKIPPED: 1, UNREADABLE: 1, FAILED_KIND: 0}
    assert model.problems(UNREADABLE) == ["C:\\a\\LOCK"]
    assert model.snapshot().headline.endswith("파일 4개") and model.snapshot().fraction == 1.0
    title, body = ST.list_dialog_text(model, UNREADABLE)
    assert "읽지 못함" in title and "C:\\a\\LOCK" in body and "Chrome" in body
    assert T.CARD_LIST_NONE in ST.list_dialog_text(model, FAILED_KIND)[1]


def test_problem_items_from_events_are_kept_and_capped(model):
    model.begin(T.TASK_TRANSFER)
    model.info(info("verify", 1, 9, failed=1, problems=((FAILED_KIND, "D:\\x\\a.bin"),)))
    assert model.problems(FAILED_KIND) == ["D:\\x\\a.bin"]
    many = tuple((FAILED_KIND, f"p{i}") for i in range(ST.MAX_KEPT_PER_KIND + 50))
    model.info(info("verify", 2, 9, failed=ST.MAX_KEPT_PER_KIND + 51, problems=many))
    assert len(model.problems(FAILED_KIND)) == ST.MAX_KEPT_PER_KIND
    assert model.problem_overflow(FAILED_KIND) == 51
    assert "51" in ST.list_dialog_text(model, FAILED_KIND)[1]


def test_cancelled_failed_and_events_after_end_are_ignored(model):
    model.begin(T.TASK_TRANSFER)
    model.info(info("copy", 1, 10))
    model.cancelled()
    snap = model.snapshot()
    assert snap.state == ST.CANCELLED and snap.headline == T.CARD_HEAD_CANCELLED and snap.fraction is None
    model.info(info("verify", 9, 10))
    assert model.snapshot().headline == T.CARD_HEAD_CANCELLED
    model.begin(T.TASK_SCAN)
    model.failed("읽을 수 없습니다")
    snap = model.snapshot()
    assert snap.state == ST.FAILED and snap.headline == T.CARD_HEAD_FAILED and snap.last_message == "읽을 수 없습니다"


def test_last_message_follows_latest_text(model):
    model.begin(T.TASK_DISCOVER)
    assert model.snapshot().last_message == T.CARD_LAST_NONE
    model.message("후보 폴더를 찾는 중")
    model.info(info("scan", 3, 9, message="크기를 재는 중"))
    assert model.snapshot().last_message == "크기를 재는 중"


def test_fraction_prefers_bytes_then_files(model):
    model.begin(T.TASK_TRANSFER)
    model.info(info("copy", 1, 10, done_bytes=50, total_bytes=100))
    assert model.snapshot().fraction == 0.5
    model.info(info("verify", 3, 10))
    assert model.snapshot().fraction == 0.3
    model.info(info("scan", 3, 0))
    assert model.snapshot().fraction is None


# ----------------------------------------------------------------------------- 진행 이벤트 필드

def test_tracker_counts_current_and_new_problems_once():
    t = ProgressTracker("verify", 100, 4)
    t.set_current("D:\\a")
    t.note(OK)
    t.note(FAILED_KIND, "D:\\bad")
    t.note(UNREADABLE, "D:\\locked")
    s1 = t.snapshot()
    assert (s1.ok, s1.failed, s1.unreadable, s1.skipped) == (1, 1, 1, 0) and s1.current == "D:\\a"
    assert s1.problems == ((FAILED_KIND, "D:\\bad"), (UNREADABLE, "D:\\locked"))
    assert t.snapshot().problems == ()  # 새 항목만 한 번씩


def test_cli_progress_text_is_unchanged_by_new_fields():
    base = info("verify", 10, 100, done_bytes=5, total_bytes=50, speed_bps=2.0, eta_seconds=3.0)
    rich = info("verify", 10, 100, done_bytes=5, total_bytes=50, speed_bps=2.0, eta_seconds=3.0, current="x", ok=9, failed=1,
                problems=((FAILED_KIND, "x"),))
    assert format_info(base) == format_info(rich)


def test_categorize():
    assert categorize("verified") == categorize("copied") == categorize("pending_verify") == OK
    assert categorize("skipped_identical") == categorize("cloud_only") == SKIPPED
    assert categorize("failed", "in_use") == UNREADABLE
    assert categorize("failed", "permission") == categorize("mismatch") == categorize("missing") == FAILED_KIND


def test_verify_against_manifest_emits_counters_current_and_problem_paths(tmp_path):
    from pcmigrate.manifest import build_manifest, verify_against_manifest
    src, dst = tmp_path / "src", tmp_path / "dst"
    for d in (src, dst):
        (d / "sub").mkdir(parents=True)
    for name in ("a.txt", "b.txt", "c.txt"):
        (src / "sub" / name).write_text(name * 10)
    (dst / "sub" / "a.txt").write_text("a.txt" * 10)
    (dst / "sub" / "b.txt").write_text("CORRUPT!!!")  # 크기는 같고 내용이 다름... 아래에서 실제 크기에 맞춤
    (dst / "sub" / "b.txt").write_text("x" * len("b.txt" * 10))
    # c.txt 는 대상에 없음
    m = tmp_path / "m.jsonl"
    build_manifest(src, m)
    events = []
    rep = verify_against_manifest(m, dst, progress_info=events.append, min_interval=0)
    last = events[-1]
    assert (last.ok, last.failed, last.unreadable) == (1, 2, 0) and last.done_files == 3
    paths = [p for e in events for _k, p in e.problems]
    assert any(p.endswith("b.txt") for p in paths) and any(p.endswith("c.txt") for p in paths)
    assert last.current.endswith(".txt")
    assert len(rep.failures) == 2


@pytest.mark.skipif(os.name != "nt", reason="바이트 범위 잠금은 Windows 전용 시나리오")
def test_manifest_build_counts_locked_file_as_unreadable(tmp_path):
    import msvcrt
    from pcmigrate.manifest import build_manifest
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.txt").write_text("ok")
    locked = src / "LOCKED.dat"
    locked.write_bytes(b"x" * 100)
    f = open(locked, "r+b")
    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 10)
    events = []
    try:
        res = build_manifest(src, tmp_path / "out.jsonl", progress_info=events.append, min_interval=0)
    finally:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 10)
        f.close()
    assert len(res.errors) == 1
    last = events[-1]
    assert last.unreadable == 1 and last.ok == 1
    assert any(k == UNREADABLE and p.endswith("LOCKED.dat") for e in events for k, p in e.problems)


def test_export_package_emits_find_pack_check_with_counts(pkg_env):
    from pcmigrate.package import export_package
    tmp_path, old, _new, expand_for = pkg_env
    events = []
    export_package([make_rule()], tmp_path / "p.zip", [], None, expand=expand_for(old), progress_info=events.append)
    phases = [e.phase for e in events]
    assert phases[0] == "find" and "pack" in phases and phases[-1] == "check"
    assert phases.index("pack") < phases.index("check")
    pack = [e for e in events if e.phase == "pack"][-1]
    assert pack.done_files == pack.total_files == 3 and pack.ok == 3 and pack.skipped >= 1  # x.lic/x.tmp 제외가 '건너뜀'
    assert events[-1].ok == 3


def test_export_unreadable_goes_to_unreadable_counter_and_problem_path(pkg_env, monkeypatch):
    from pcmigrate import package
    tmp_path, old, _new, expand_for = pkg_env
    real = package._write_entry

    def flaky(zf, arc, src, stage_dir=None):
        if src.name == "a.cfg":
            raise package.SourceUnreadable(src, PermissionError(33, "lock"), during_read=True)
        return real(zf, arc, src, stage_dir)
    monkeypatch.setattr(package, "_write_entry", flaky)
    events = []
    package.export_package([make_rule()], tmp_path / "p.zip", [], None, expand=expand_for(old), progress_info=events.append)
    assert events[-1].unreadable == 1 and events[-1].ok == 2
    assert any(k == UNREADABLE and p.endswith("a.cfg") for e in events for k, p in e.problems)


def test_apply_import_emits_restore_counts(pkg_env):
    from pcmigrate.package import apply_import, export_package, plan_import
    tmp_path, old, new, expand_for = pkg_env
    out = tmp_path / "p.zip"
    export_package([make_rule()], out, [], None, expand=expand_for(old))
    plan = plan_import(out, [make_rule()], expand_for(new))
    events = []
    apply_import(out, plan, events.append)
    assert events[-1].phase == "restore" and events[-1].ok == 3 and events[-1].done_files == events[-1].total_files == 3
    events.clear()
    plan2 = plan_import(out, [make_rule()], expand_for(new))  # 이미 복원됨 -> 같은 파일은 건너뜀
    apply_import(out, plan2, events.append)
    assert events[-1].skipped == 3 and events[-1].ok == 0


def test_run_scan_and_discover_emit_phases(tmp_path):
    rules = make_rules_dir(tmp_path / "rules")
    deps = api.Deps(registry=lambda: [], winget_export=lambda: None, winget_list=lambda: None,
                    summarize=lambda *a, **k: (0, 0))
    events = []
    api.run_scan(rules, deps, progress_info=events.append)
    assert [e.phase for e in events] == ["list", "match"]
    events.clear()
    api.run_discover(rules, deps=deps, progress_info=events.append)
    assert events[0].phase == "list" and "find" in [e.phase for e in events]


def test_transfer_dry_run_emits_scan_then_compare(tmp_path):
    from pcmigrate import fs
    from pcmigrate.transfer import transfer
    src, dst = tmp_path / "s", tmp_path / "d"
    src.mkdir()
    (src / "a.txt").write_text("a")
    events = []
    transfer(src, dst, dry_run=True, progress_info=events.append, runner=lambda *a, **k: (0, ""),
             dest_info_fn=lambda p: fs.DestInfo(p, "fixed", fs="NTFS", free=10 ** 12))
    assert events[-1].phase == "compare" and events[-1].total_files == 1


# ----------------------------------------------------------------------------- 전체 단계 표시

def test_overall_step_for_every_flow_screen():
    for i, screen in enumerate(S.OLD_FLOW, 1):
        assert S.overall_step(screen)[:2] == (i, 4)
    for i, screen in enumerate(S.NEW_FLOW, 1):
        assert S.overall_step(screen)[:2] == (i, 4)
    assert S.overall_step(S.START) is None
    assert S.overall_step(S.TRANSFER, T.MODE_EXTERNAL, 1)[:2] == (1, 2)
    assert S.overall_step(S.TRANSFER, T.MODE_NETWORK, 2)[:2] == (2, 2)
    assert S.overall_step(S.TRANSFER, T.MODE_LOCAL, 1)[:2] == (1, 1)
    assert T.OVERALL_FMT.format(n=2, total=4) == "전체 단계 2/4"
    assert S.overall_step(S.OLD_DISCOVER)[3] == T.TITLES[S.OLD_DISCOVER]


# ----------------------------------------------------------------------------- 컨트롤러 연결

@pytest.fixture
def cenv(tmp_path):
    api_ = FakeApi()
    errors = []

    def rec(exc, where, tb):
        errors.append((type(exc).__name__, where, tb))
        return tmp_path / "errors-x.log"
    c = Controller(api_mod=api_, runner=Runner(sync=True), rules_dir=tmp_path, record_error=rec)
    events = []
    c.listener = lambda k, p=None: events.append((k, p))
    return c, api_, events, errors


def test_controller_drives_status_model_through_a_task(cenv):
    c, api_, events, _ = cenv
    c.start_scan()
    st = c.status
    assert st.state == ST.DONE and st.task == T.TASK_SCAN
    assert "프로그램 3개" in st.snapshot().headline
    assert [k for k, _ in events if k == "status"]  # 시작/끝에 카드 갱신 이벤트


def test_controller_feeds_info_events_and_final_counts_from_report(cenv):
    c, api_, events, _ = cenv

    def run_transfer(src, dst, *, progress=None, **kw):
        progress(info("scan", 5))
        progress(info("copy", 2, 4, done_bytes=1, total_bytes=2, ok=2, current="D:\\a"))
        progress("복사하는 중")
        return Report(dry_run=False, results=[
            FileResult("s\\a", "d\\a", "verified", 1, "x"), FileResult("s\\b", "d\\b", "failed", 1, "", reason="in_use", error="사용 중"),
            FileResult("s\\c", "", "cloud_only"), FileResult("s\\d", "d\\d", "mismatch", 1, "", reason="mismatch")])
    api_.run_transfer = run_transfer
    c.state.transfer_src, c.state.transfer_dst = str(Path(c.state.rules_dir)), str(Path(c.state.rules_dir) / "x")
    api_.destination_info = None
    s = c.state
    s.transfer_src, s.transfer_dst = str(Path(s.rules_dir) / "src"), str(Path(s.rules_dir) / "dst")
    (Path(s.rules_dir) / "src").mkdir()
    s.transfer_preview_key = (s.transfer_src, s.transfer_dst)
    c.start_transfer(dry_run=False, write_confirmed=True)
    counts = {x.key: x.count for x in c.status.snapshot().chips}
    assert counts == {OK: 1, SKIPPED: 1, UNREADABLE: 1, FAILED_KIND: 1}
    assert c.status.problems(UNREADABLE) == ["s\\b"] and c.status.problems(SKIPPED) == ["s\\c"]
    assert c.status.snapshot().last_message == "복사하는 중"


def test_controller_error_marks_status_failed_and_logs(cenv):
    c, api_, events, errors = cenv
    api_.transfer_error = RuntimeError("boom")
    s = c.state
    (Path(s.rules_dir) / "src").mkdir()
    s.transfer_src, s.transfer_dst = str(Path(s.rules_dir) / "src"), str(Path(s.rules_dir) / "dst")
    c.start_transfer(dry_run=True)
    assert c.status.state == ST.FAILED
    err = [p for k, p in events if k == "error"][-1]
    assert errors and errors[-1][0] == "RuntimeError" and "GUI" in errors[-1][1] and T.TASK_TRANSFER_PREVIEW in errors[-1][1]
    assert err.log_path.endswith("errors-x.log") and "RuntimeError: boom" in err.report and "Traceback" in err.report


def test_report_error_survives_logging_failure(tmp_path):
    def broken(*a, **k):
        raise OSError("disk full")
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path, record_error=broken)
    fe = c.report_error(ValueError("x"), "작업")
    assert fe.log_path == "" and "ValueError: x" in fe.report


def test_transfer_report_path_inside_source_is_refused(cenv, tmp_path):
    c, api_, events, _ = cenv
    s = c.state
    src = tmp_path / "SRC"
    (src / "sub").mkdir(parents=True)
    s.transfer_src, s.transfer_dst = str(src), str(tmp_path / "dst")
    with pytest.raises(GuardError, match="원본 폴더 밖"):
        c.start_transfer(dry_run=True, report_path=str(src / "sub" / "r.json"))
    assert not [x for x in api_.calls if x[0] == "transfer"]
    c.start_transfer(dry_run=True, report_path=str(tmp_path / "r.json"))  # 밖이면 된다
    assert [x for x in api_.calls if x[0] == "transfer"]
    c.state.transfer_report = Report(dry_run=True)
    with pytest.raises(GuardError):
        c.save_transfer_report(str(src / "r.json"))
    assert not (src / "r.json").exists()


# ----------------------------------------------------------------------------- tkinter 스모크

tk = pytest.importorskip("tkinter")
from test_gui_smoke import ctx  # noqa: E402,F401


def test_overall_step_label_changes_with_screen(ctx):
    app, c = ctx.app, ctx.c
    assert not app.overall.winfo_ismapped() or app.overall_label.cget("text") == ""
    c.go(S.OLD_DISCOVER)
    app.update()
    assert app.overall_label.cget("text") == "전체 단계 2/4" and len(app.overall_chips) >= 4
    c.go(S.NEW_CONFIRM)
    app.update()
    assert app.overall_label.cget("text") == "전체 단계 3/4"
    c.go(S.START)
    app.update()
    assert not app.overall.winfo_ismapped()
    c.set_transfer_flow(T.MODE_EXTERNAL, 2)
    c.go(S.TRANSFER)
    app.update()
    assert app.overall_label.cget("text") == "전체 단계 2/2"


def test_status_card_shows_on_scan_screen_and_stays_after_done(ctx):
    app, c = ctx.app, ctx.c
    c.go(S.OLD_SCAN)
    view = app.views[S.OLD_SCAN]
    assert not view.card.shown
    c.start_scan()  # 동기 실행기: 끝난 뒤 상태
    app.update()
    assert view.card.shown and "완료" in view.card.headline.cget("text")
    assert view.card.problem.cget("text").endswith(T.CARD_PROBLEM_NONE)
    # 다른 작업의 화면에는 보이지 않는다
    assert not app.views[S.OLD_EXPORT].card.shown and not app.views[S.TRANSFER].card.shown


def test_status_card_widget_reflects_progress_chips_and_list_dialog(ctx):
    app, c = ctx.app, ctx.c
    c.go(S.TRANSFER)
    card = app.views[S.TRANSFER].card
    c.status.begin(T.TASK_TRANSFER)
    c.status.info(info("scan", 5))
    c.status.info(info("copy", 12431, 50003, done_bytes=10 * 1024 ** 2, total_bytes=40 * 1024 ** 2, speed_bps=2 * 1024 ** 2,
                       eta_seconds=15.0, current="D:\\프로젝트\\" + "긴폴더\\" * 30 + "a.dwg", ok=12000, skipped=2,
                       unreadable=1, failed=3, problems=((UNREADABLE, "D:\\x\\LOCK"), (FAILED_KIND, "D:\\x\\bad.bin"))))
    card.refresh()
    app.update()
    assert card.shown
    assert card.headline.cget("text") == "② 복사 중 — 12,431 / 50,003개 파일 (24%)"
    texts = [l.cget("text") for l in card.step_labels]
    assert texts[0].startswith(T.ICON_OK) and "② 복사" in texts[1] and T.ICON_OK not in texts[1]
    assert "12,431 / 50,003개" in card.metric_values[T.CARD_METRIC_FILES].cget("text")
    assert "…" in card.current.cget("text") and card.current_tip.text.endswith("a.dwg")
    chip_texts = {k: l.cget("text") for k, l in card.chips.items()}
    assert chip_texts[FAILED_KIND] == f"{T.ICON_FAIL} 실패 3" and chip_texts[UNREADABLE].startswith(T.ICON_WARN)
    assert chip_texts[OK].startswith(T.ICON_OK) and "12,000" in chip_texts[OK]
    assert "읽지 못함(사용 중) 1개" in card.problem.cget("text") and card.problem.cget("bg") != card.chips[OK].cget("bg")
    dlg = card.open_list(UNREADABLE)  # 칩을 누르면 목록
    assert dlg is not None and "D:\\x\\LOCK" in dlg.text.get("1.0", "end")
    dlg.destroy()
    assert card.open_list(OK) is None  # 성공 칩은 목록 없음
    c.status.finish()
    card.refresh()
    assert "완료" in card.headline.cget("text")


def test_status_card_for_cancelled_and_failed(ctx):
    app, c = ctx.app, ctx.c
    c.go(S.OLD_EXPORT)
    card = app.views[S.OLD_EXPORT].card
    c.status.begin(T.TASK_EXPORT)
    c.status.cancelled()
    card.refresh()
    assert card.headline.cget("text") == T.CARD_HEAD_CANCELLED
    c.status.begin(T.TASK_EXPORT)
    c.status.failed("실패 사유")
    card.refresh()
    assert card.headline.cget("text") == T.CARD_HEAD_FAILED and "실패 사유" in card.last.cget("text")


def test_every_long_running_screen_has_a_status_card(ctx):
    expected = {S.OLD_SCAN: T.TASK_SCAN, S.OLD_DISCOVER: T.TASK_DISCOVER, S.OLD_CHECKLIST: T.TASK_CHECKLIST,
                S.OLD_EXPORT: T.TASK_EXPORT, S.NEW_PACKAGE: T.TASK_PREVIEW, S.NEW_CONFIRM: T.TASK_APPLY,
                S.NEW_RESULT: T.TASK_APPLY, S.TRANSFER: T.TASK_TRANSFER}
    for screen, task in expected.items():
        card = ctx.app.views[screen].card
        assert task in card.tasks, screen
    assert {T.TASK_TRANSFER_PREVIEW, T.TASK_VERIFY, T.TASK_VERIFY_AGAIN, T.TASK_RETRY} <= ctx.app.views[S.TRANSFER].card.tasks


def test_unreadable_files_shown_after_export_in_gui(ctx):
    app, c = ctx.app, ctx.c
    manifest = {"rules": [], "files": [{"arc": "a"}], "skipped": [],
                "failed": [{"src": f"D:/p/LOCK{i}", "error": "사용 중이라 읽지 못함", "reason": "in_use"} for i in range(8)],
                "manual_warnings": []}
    ctx.api.run_export = lambda *a, **k: SimpleNamespace(manifest=manifest)
    c.go(S.OLD_EXPORT)
    v = app.views[S.OLD_EXPORT]
    c.start_export(str(Path(c.state.rules_dir) / "p.zip"), write_confirmed=True)
    app.update()
    body = v.result.get("1.0", "end")
    assert "읽지 못해 제외된 파일 8개" in body and "Chrome" in body and body.count("LOCK") == 5 and "외 3개" in body
    assert "Chrome" in T.EXP_FAILED_HEAD
    chips = {k: l.cget("text") for k, l in v.card.chips.items()}
    assert "8" in chips[UNREADABLE]


@pytest.mark.skipif(os.name != "nt", reason="바이트 범위 잠금은 Windows 전용 시나리오")
def test_locked_source_is_classified_in_use_not_permission(tmp_path):
    """Chrome 등이 잠근 파일을 해시/복사하다 실패하면 '권한 없음' 이 아니라 '사용 중' 으로 분류한다."""
    import msvcrt
    from pcmigrate.copier import copy_verified, verify_file
    src, dst = tmp_path / "s.dat", tmp_path / "d.dat"
    src.write_bytes(b"x" * 100)
    dst.write_bytes(b"x" * 100)
    f = open(src, "r+b")
    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 10)
    try:
        r1 = verify_file(src, dst)
        r2 = copy_verified(src, tmp_path / "out" / "c.dat")
    finally:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 10)
        f.close()
    assert r1.status == "failed" and r1.reason == "in_use" and "사용 중" in r1.error
    assert r2.status == "failed" and r2.reason == "in_use"
    assert not list((tmp_path / "out").glob("*.pcmigrate-part"))
