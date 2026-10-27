"""stage30b: 화면 4~7(자동 목록 밖 프로그램 표, 작업 중지, 프로그램별 복원/되돌리기, 구 PC 마지막 화면). 가짜 api 만 쓴다."""
import threading
from types import SimpleNamespace

import pytest

from pcmigrate import api as real_api
from pcmigrate import import_record, package
from pcmigrate.cancel import CancelToken
from pcmigrate.copier import FileResult, Report
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import AutoRun, AutoStep, Controller, GuardError
from pcmigrate.gui.runner import Runner
from pcmigrate.package import ImportPlan
from test_gui_controller import FakeApi, cand, discovery, kinds, plan

tk = pytest.importorskip("tkinter")
from test_gui_smoke import ctx  # noqa: E402,F401


# --------------------------------------------------------------------------- 가짜 api
def item(program, name, status="would_copy", backup=""):
    return {"entry": f"{program}/{name}", "target": f"C:\\fake\\{program}\\{name}", "status": status, "backup": backup, "size": 10,
            "sha256": "x", "extra": False, "program": program}


BASE_ITEMS = [item("revit", "a.cfg"), item("revit", "b.cfg", "would_overwrite", "C:\\fake\\revit\\b.cfg.bak"),
              item("autocad", "c.cfg", "would_overwrite", "C:\\fake\\autocad\\c.cfg.bak"), item("autocad", "d.cfg", "skipped_identical"),
              item("vscode", "e.json")]


def make_plan(skip=()):
    items = []
    for it in BASE_ITEMS:
        it = dict(it)
        if any(package.program_matches(t, it["program"]) for t in skip):
            it.update(status="not_selected", backup="")
        items.append(it)
    p = ImportPlan({"manual_warnings": []}, items, [])
    package._fill_programs(p, {}, [], list(skip))
    return p


class ApiB(FakeApi):
    """프로그램별 선택(skip_programs)과 되돌리기를 지원하는 가짜 api."""

    def __init__(self, record_path="C:\\fake\\record.json", record_error=""):
        super().__init__()
        self.import_calls = []
        self.undo_calls = []
        self.export_calls = []
        self.record_path, self.record_error = record_path, record_error
        self.undo_restore = 1

    def run_export(self, rules_dir, output, *, extra=None, allow_secrets=False, deps=None, extra_items=None, extra_mode="config"):
        self.export_calls.append(extra_mode)
        return SimpleNamespace(manifest={"rules": [], "files": [{}], "skipped": [], "failed": [], "manual_warnings": []})

    def run_import(self, package_path, rules_dir, *, dry_run=False, yes=False, allow_extra=False, deps=None, progress_info=None,
                   only_programs=None, skip_programs=None):
        skip = tuple(skip_programs or ())
        self.import_calls.append({"dry_run": dry_run, "yes": yes, "skip": skip, "only": only_programs})
        p = make_plan(skip)
        if dry_run or not yes:
            return real_api.ImportResult(p, "dry-run", 0, skip=skip)
        results = []
        for it in p.items:
            if it["status"] == "not_selected":
                results.append(FileResult(it["entry"], it["target"], "not_selected", 10))
            elif it["status"] == "skipped_identical":
                results.append(FileResult(it["entry"], it["target"], "skipped_identical", 10))
            else:
                results.append(FileResult(it["entry"], it["target"], "copied", 10, "x", it["backup"]))
        rep = Report(dry_run=False, results=results)
        return real_api.ImportResult(p, "applied", 0, rep, record_path=self.record_path, record_error=self.record_error, skip=skip)

    def run_undo_import(self, only_programs, *, record=None, record_dir=None, dry_run=False, yes=False, move_new=False):
        self.undo_calls.append({"only": list(only_programs), "record": record, "dry_run": dry_run, "yes": yes})
        pid = only_programs[0]
        items = [import_record.UndoItem(pid, "C:\\fake\\x", "C:\\fake\\x.bak", import_record.RESTORE, index=0)
                 for _ in range(self.undo_restore)]
        items.append(import_record.UndoItem(pid, "C:\\fake\\new", "", import_record.KEEP_NEW, index=9))
        plan_ = import_record.UndoPlan(record or "", [pid], items)
        if dry_run or not yes:
            return real_api.UndoResult(plan_, "dry-run", {})
        for it in items:
            if it.action == import_record.RESTORE:
                it.result = import_record.RESTORED
        return real_api.UndoResult(plan_, "applied", {})


def new_ctl(tmp_path, api=None, sync=True):
    api = api or ApiB()
    c = Controller(api_mod=api, runner=Runner(sync=sync), rules_dir=tmp_path,
                   make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    ev = []
    c.listener = lambda kind, payload=None: ev.append((kind, payload))
    return c, api, ev


def previewed(tmp_path, api=None):
    c, api, ev = new_ctl(tmp_path, api)
    c.set_package("C:\\fake\\pkg.zip")
    c.start_preview()
    return c, api, ev


def applied(tmp_path, api=None):
    c, api, ev = previewed(tmp_path, api)
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    return c, api, ev


# --------------------------------------------------------------------------- 화면 5: 중지(헤드리스)
def test_every_task_is_cancellable_while_running(tmp_path):
    seen = []

    class Spy(ApiB):
        def run_scan(self, rules_dir, deps=None):
            seen.append(("scan", c.can_cancel))
            return super().run_scan(rules_dir, deps)

        def run_import(self, *a, **k):
            seen.append(("import", c.can_cancel))
            return super().run_import(*a, **k)

        def run_export(self, *a, **k):
            seen.append(("export", c.can_cancel))
            return super().run_export(*a, **k)
    c, api, ev = new_ctl(tmp_path, Spy())
    c.start_scan()
    c.set_package("C:\\p.zip")
    c.start_preview()
    c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)
    assert seen == [("scan", True), ("import", True), ("export", True)] and c.can_cancel is False


def test_stop_note_tells_whether_resumable(tmp_path):
    c, api, ev = new_ctl(tmp_path)
    assert c.stop_done_note(T.TASK_EXPORT) == T.STOP_DONE_RESUME and c.stop_done_note(T.TASK_TRANSFER) == T.STOP_DONE_RESUME
    assert c.stop_done_note(T.TASK_APPLY) == T.STOP_DONE_APPLY and c.stop_done_note(T.TASK_SCAN) == T.STOP_DONE_READONLY
    assert ".bak" in T.STOP_DONE_APPLY and "이어서" in T.STOP_DONE_RESUME
    for sentence in (T.FORCE_STOPPED,):
        assert "화면은 정상으로 돌아왔습니다" in sentence and "이어받기가 가능한 상태" in sentence and "잠시 막힐 수 있습니다" in sentence


# --------------------------------------------------------------------------- 화면 4: 설정 파일 열 / 폴더 전체 담기
def set_discovery(c, *cands):
    from pcmigrate.detect import Program
    from pcmigrate.discover import Discovery, ProgramEntry
    d = Discovery()
    d.programs = [ProgramEntry(Program("Acme Tool"), list(cands))]
    c.state.discovery = d


def test_cfg_cells_unknown_calculating_and_known(tmp_path):
    c, api, ev = new_ctl(tmp_path)
    a, b = cand("A"), cand("B")
    b.config_files, b.config_bytes, b.config_excluded_files, b.config_excluded_bytes, b.config_complete = 3, 2048, 5, 4096, True
    set_discovery(c, a, b)
    cells = c.candidate_cfg_cells()
    assert cells[a.key]["cfg"] == T.DISC_CFG_UNKNOWN and T.DISC_CFG_TIP_UNKNOWN == cells[a.key]["tip"]
    assert cells[a.key]["size"] == T.DISC_SIZE_FOLDER.format(size="100B")
    assert cells[b.key]["cfg"] == "3개" and cells[b.key]["size"] == "2.0KB" and cells[b.key]["excl"] == "4.0KB"
    assert "허용 방식" in cells[b.key]["tip"]
    c.state.busy, c.state.busy_label = True, T.TASK_FIND_PROGRAM
    assert c.candidate_cfg_cells()[a.key]["cfg"] == T.DISC_CFG_CALC
    c.state.busy = False
    b.config_complete = False
    assert c.candidate_cfg_cells()[b.key]["cfg"] == "3개 이상"


def test_selection_total_text_config_and_all(tmp_path):
    c, api, ev = new_ctl(tmp_path)
    a, b = cand("A", size=2048), cand("B", size=4096)
    b.config_files, b.config_bytes, b.config_complete = 3, 2048, True
    set_discovery(c, a, b)
    assert c.selection_config_text() == "선택 0개 · 설정 파일 0개 · 약 0B"
    c.set_selected(a.key, True)
    c.set_selected(b.key, True)
    text = c.selection_config_text()
    assert text.startswith("선택 2개 · 설정 파일 3개 · 약 2.0KB") and "확인 안 된 1개" in text
    c.set_extra_mode("all")
    assert c.selection_config_text() == T.DISC_TOTAL_V2_ALL.format(n=2, files=2, size="6.0KB")
    assert c.candidate_cfg_cells()[b.key]["size"] == "4.0KB"
    with pytest.raises(GuardError):
        c.set_extra_mode("bogus")


def test_export_passes_extra_mode_only_when_all_and_guards_old_api(tmp_path):
    c, api, ev = new_ctl(tmp_path)
    out = str(tmp_path / "o.zip")
    c.start_export(out, write_confirmed=True)
    assert api.export_calls == ["config"]  # 기본은 넘기지 않아 api 기본값(설정 파일만)을 쓴다
    c.set_extra_mode("all")
    c.start_export(out, write_confirmed=True)
    assert api.export_calls == ["config", "all"]

    class Old(FakeApi):
        pass
    c2, _api, _ev = new_ctl(tmp_path, Old())
    c2.set_extra_mode("all")
    with pytest.raises(GuardError, match="폴더 전체 담기"):
        c2.start_export(out, write_confirmed=True)  # 조용히 무시하지 않는다
    assert [x for x in _api.calls if x[0] == "export"] == []


# --------------------------------------------------------------------------- 화면 6: 프로그램별 복원 선택(헤드리스)
def test_preview_programs_table_and_default_selection(tmp_path):
    c, api, ev = previewed(tmp_path)
    rows = c.restore_programs()
    assert [r["id"] for r in rows] == ["revit", "autocad", "vscode"] and all(r["checked"] for r in rows)
    assert (rows[0]["new"], rows[0]["overwrite"], rows[0]["same"]) == (1, 1, 0) and rows[1]["same"] == 1
    assert api.import_calls == [{"dry_run": True, "yes": False, "skip": (), "only": None}]
    assert c.restore_selection_reason() == "" and c.restore_selected_count() == (3, 3) and c.restore_item_count() == 5


def test_unchecking_blocks_apply_until_preview_recomputed_with_skip(tmp_path):
    c, api, ev = previewed(tmp_path)
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    ev.clear()
    c.set_restore_program("autocad", False)
    assert "restore_selection_changed" in kinds(ev) and c.state.preview_checked is False and c.state.bak_understood is False
    assert c.restore_selection_reason() == T.G_RESTORE_DIRTY and c.can_apply() == (False, T.G_RESTORE_DIRTY)
    assert c.apply_reason() == T.G_RESTORE_DIRTY
    c.restart_preview_for_selection()
    assert api.import_calls[-1]["skip"] == ("autocad",) and api.import_calls[-1]["dry_run"]
    rows = {r["id"]: r for r in c.restore_programs()}
    assert rows["autocad"]["checked"] is False and rows["revit"]["checked"] is True
    assert c.restore_selection_reason() == "" and c.restore_selected_count() == (2, 3)
    counts = c.state.preview.summary()  # 체크를 끈 프로그램은 새로/덮어씀 숫자에서 빠진다
    assert counts.get("would_overwrite") == 1 and counts.get("not_selected") == 2
    assert c.restore_item_count() == 3
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    assert api.import_calls[-1] == {"dry_run": False, "yes": True, "skip": ("autocad",), "only": None}


def test_all_unchecked_cannot_apply_and_unknown_program_is_guarded(tmp_path):
    c, api, ev = previewed(tmp_path)
    for pid in ("revit", "autocad", "vscode"):
        c.set_restore_program(pid, False)
    c.restart_preview_for_selection()
    assert c.restore_selection_reason() == T.G_RESTORE_NONE and c.can_apply()[1] == T.G_RESTORE_NONE
    with pytest.raises(GuardError, match=T.G_RESTORE_UNKNOWN_PROGRAM):
        c.set_restore_program("nope", False)
    c.set_restore_program("revit", True)
    assert c.restore_selection_reason() == T.G_RESTORE_DIRTY
    c.state.busy = True
    assert c.restore_selection_reason() == T.G_RESTORE_DIRTY
    c.state.busy = False


def test_new_package_resets_program_checks_but_recompute_keeps_them(tmp_path):
    c, api, ev = previewed(tmp_path)
    c.set_restore_program("vscode", False)
    c.restart_preview_for_selection()
    assert c.state.restore_skip == {"vscode"}
    c.start_preview()  # 새로 고른 묶음(또는 처음부터 다시 미리보기)은 모두 체크로 시작
    assert c.state.restore_skip == set() and api.import_calls[-1]["skip"] == ()


def test_old_api_without_skip_programs_raises_instead_of_ignoring(tmp_path):
    class NoSkip(FakeApi):
        pass
    c, api, ev = new_ctl(tmp_path, NoSkip())
    c.set_package("C:\\p.zip")
    c.start_preview()
    c.state.preview = real_api.ImportResult(make_plan(), "dry-run", 0)
    c.set_restore_program("revit", False)
    with pytest.raises(GuardError, match="프로그램별 복원 선택"):
        c.restart_preview_for_selection()
    n = len(api.calls)
    c.state.preview_skip = ("revit",)  # 가정: 미리보기와 체크가 맞아도 복원은 skip 을 보낼 수 없으면 막는다
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    with pytest.raises(GuardError, match="프로그램별 복원 선택"):
        c.start_apply()
    assert len(api.calls) == n


# --------------------------------------------------------------------------- 화면 6: 결과와 되돌리기(헤드리스)
def test_result_programs_counts_and_undo_reasons(tmp_path):
    c, api, ev = applied(tmp_path)
    rows = {r["id"]: r for r in c.result_programs()}
    assert (rows["revit"]["new"], rows["revit"]["overwrite"]) == (1, 1) and rows["autocad"]["same"] == 1
    assert rows["revit"]["undo"] == "" and rows["autocad"]["undo"] == "" and rows["vscode"]["undo"] == T.UNDO_NOTHING
    assert c.state.import_result.record_path.endswith("record.json")


def test_undo_disabled_without_record_with_reason(tmp_path):
    c, api, ev = applied(tmp_path, ApiB(record_path=""))
    assert {r["undo"] for r in c.result_programs()} == {T.UNDO_NO_RECORD}
    with pytest.raises(GuardError, match=T.UNDO_NO_RECORD):
        c.start_undo_preview("revit")
    c2, api2, ev2 = applied(tmp_path, ApiB(record_path="", record_error="OSError: 쓸 수 없음"))
    assert c2.undo_reason("revit") == T.UNDO_RECORD_FAILED


def test_undo_preview_then_confirmed_undo_uses_record_and_keeps_new_files(tmp_path):
    c, api, ev = applied(tmp_path)
    with pytest.raises(GuardError):
        c.start_undo("revit", confirmed=True)  # 미리보기 없이는 되돌리지 않는다
    c.start_undo_preview("revit")
    assert api.undo_calls == [{"only": ["revit"], "record": "C:\\fake\\record.json", "dry_run": True, "yes": False}]
    prev = [p for k, p in ev if k == "undo_preview_done"][0]
    assert c.undo_preview_counts(prev["result"]) == (1, 1, 0)
    with pytest.raises(GuardError):
        c.start_undo("revit")  # 확인 없이는 안 된다
    assert len(api.undo_calls) == 1
    c.start_undo("revit", confirmed=True)
    assert api.undo_calls[-1]["yes"] is True and api.undo_calls[-1]["dry_run"] is False
    done = [p for k, p in ev if k == "undo_done"][0]
    assert c.undo_counts(done["result"]) == (1, 0)
    assert c.undo_reason("revit") == T.UNDO_DONE_LABEL.format(n=1) and c.undo_reason("autocad") == ""
    assert T.TASK_UNDO in T.PROGRESS and T.TASK_UNDO_PREVIEW in T.TASK_STEPS


def test_undo_unsupported_api_is_guarded(tmp_path):
    class NoUndo(ApiB):
        run_undo_import = None
    c, api, ev = applied(tmp_path, NoUndo())
    with pytest.raises(GuardError, match=T.G_UNDO_UNSUPPORTED):
        c.start_undo_preview("revit")


# --------------------------------------------------------------------------- 화면 7: 한 번에 진행의 내보내기 멈춤/다시 담기(헤드리스)
def auto_old(c, export_path="C:\\x\\o.zip"):
    c.auto = AutoRun("old", [AutoStep("scan", "조사", "done"), AutoStep("export", "묶음", "stopped")], status="stopped",
                     export_path=export_path, what="오류", how="")


def test_auto_retry_export_from_savepoint_or_restart(tmp_path):
    c, api, ev = new_ctl(tmp_path)
    with pytest.raises(GuardError, match=T.G_AUTO_RETRY_NA):
        c.auto_retry_export()
    auto_old(c, str(tmp_path / "o.zip"))
    assert c.auto_export_stopped()
    seen = []
    c.api.run_export = lambda *a, resume=True, **k: (seen.append(resume), SimpleNamespace(manifest={
        "rules": [], "files": [{}], "skipped": [], "failed": [], "manual_warnings": []}))[1]
    c.auto_retry_export(restart=False)
    assert seen == [True] and c.auto.status == "done" and c.auto.what == ""
    auto_old(c, str(tmp_path / "o.zip"))
    c.auto_retry_export(restart=True)
    assert seen == [True, False]


def test_auto_after_retry_clears_warning_only_when_all_recovered(tmp_path):
    c, api, ev = new_ctl(tmp_path)
    c.auto = AutoRun("old", [AutoStep("export", "묶음", "warn")], status="done", warning="읽지 못한 파일이 있습니다")
    locked = {"src": "x", "error": "사용 중", "reason": "in_use"}
    c.auto_after_retry(SimpleNamespace(manifest={"failed": [locked], "files": [], "skipped": []}))
    assert c.auto.warning and c.auto.step("export").state == "warn"
    c.auto_after_retry(SimpleNamespace(manifest={"failed": [], "files": [{}], "skipped": []}))
    assert c.auto.warning == "" and c.auto.step("export").state == "done"
    c.auto.status = "running"
    c.auto.warning = "keep"
    c.auto_after_retry(SimpleNamespace(manifest={"failed": [], "files": [], "skipped": []}))
    assert c.auto.warning == "keep"


# =========================================================================== tkinter
def blocking_ctx(ctx, wait, honor_cancel=True):
    """실제 백그라운드 실행기 + 끝나지 않는 가짜 작업(honor_cancel=False 면 취소 신호를 무시하는 상황)."""
    from pcmigrate.cancel import Cancelled

    class Slow(ApiB):
        def run_scan(self, rules_dir, deps=None):
            tok = ctx.c.cancel_token
            wait.wait(5)
            if honor_cancel and tok is not None and tok.is_set():
                raise Cancelled()
            return super().run_scan(rules_dir, deps)
    ctx.c.api = Slow()
    ctx.c.runner = Runner(sync=False)
    ctx.c.runner.on_callback_error = ctx.c._callback_failed
    return ctx.c


def test_tk_stop_button_states_normal_busy_cancelling_force(ctx):
    app, c = ctx.app, ctx.c
    now = [100.0]
    c.clock = lambda: now[0]
    wait = threading.Event()
    blocking_ctx(ctx, wait, honor_cancel=False)
    app.update()
    assert not app.stop_btn.winfo_manager() and not app.force_box.winfo_manager()  # 평상시는 숨김
    c.start_scan()
    app.update()
    assert c.state.busy and c.can_cancel
    assert app.stop_btn.winfo_manager() == "pack" and app.stop_btn.cget("text") == T.BTN_STOP and "disabled" not in app.stop_btn.state()
    assert app.stop_btn.kind == "danger"
    app.stop_btn.invoke()  # 누르면 즉시 '중지하는 중…' 과 비활성
    app.update()
    assert app.stop_btn.cget("text") == T.STOPPING and "disabled" in app.stop_btn.state() and c.cancel_token.is_set()
    assert not app.force_box.winfo_manager()
    now[0] += 14
    app._sync_stop()
    assert not app.force_box.winfo_manager()
    now[0] += 2  # 15초가 지났는데 아직 busy
    app._sync_stop()
    app.update()
    assert app.force_box.winfo_manager() and T.STOP_STUCK in app.force_label.cget("text") and app.force_btn.cget("text") == T.BTN_FORCE_STOP
    app.force_btn.invoke()
    app.update()
    assert not c.state.busy and not app.stop_btn.winfo_manager() and not app.force_box.winfo_manager()
    assert app.after_note.cget("text") == T.FORCE_STOPPED and app.after_note.winfo_manager()
    assert "새 작업" in T.FORCE_STOPPED
    wait.set()
    c.runner.join(3)
    c.runner.pump()
    assert not c.state.busy


def test_tk_stop_done_note_and_escape(ctx):
    app, c = ctx.app, ctx.c
    wait = threading.Event()
    blocking_ctx(ctx, wait)
    c.start_scan()
    app.update()
    assert app._on_escape(None) == "break" and c.cancel_requested_at is not None  # Esc 도 같은 중지
    wait.set()
    c.runner.join(3)
    c.runner.pump()
    app.update()
    assert not c.state.busy and app.after_note.cget("text") == T.STOP_DONE_READONLY and app.after_note.winfo_manager()
    c.go(S.START)
    app.update()
    assert not app.after_note.winfo_manager()  # 화면을 옮기면 지난 안내는 지운다


def test_tk_auto_stop_uses_footer_and_flow_stops(ctx):
    app, c = ctx.app, ctx.c
    wait = threading.Event()
    blocking_ctx(ctx, wait)
    c.auto_run("old")
    app.update()
    assert c.state.busy and app.stop_btn.winfo_manager() == "pack"
    v = app.views[S.AUTO]
    assert not v.stop_btn.winfo_manager()  # 작업 중에는 아래 막대의 '중지' 하나만 쓴다
    app.stop_btn.invoke()
    app.update()
    assert c.auto.stop_requested and app.stop_btn.cget("text") == T.STOPPING
    wait.set()
    c.runner.join(3)
    c.runner.pump()
    app.update()
    assert c.auto.status == "stopped" and c.auto.by_user


# --------------------------------------------------------------------------- 화면 4(tk)
def test_tk_discover_table_min_rows_and_sash(ctx):
    app, c = ctx.app, ctx.c
    set_discovery(c, cand("A"), cand("B"))
    c.go(S.OLD_DISCOVER)
    app.update()
    v = app.views[S.OLD_DISCOVER]
    assert v.min_table_rows == 8 and v.table_rows() == 8 and int(v.tree.cget("height")) >= 8
    assert v.set_table_rows(3) == 8  # 최소 8줄 아래로는 줄지 않는다
    ev = lambda y: SimpleNamespace(y_root=y)  # noqa: E731
    v._sash_press(ev(100))
    v._sash_motion(ev(100 + 3 * v._row_px()))
    app.update()
    assert v.table_rows() == 11 and int(v.tree.cget("height")) == 11
    v._sash_motion(ev(100 - 500))  # 위로 많이 끌어도 8줄
    assert v.table_rows() == 8
    v._sash_motion(ev(100 + 5000))
    assert v.table_rows() <= 40
    v.sash.event_generate("<Down>")
    assert v.sash.cget("takefocus") in (1, "1")


def test_tk_discover_table_stays_8_rows_in_low_window_and_lower_area_scrolls(ctx):
    app, c = ctx.app, ctx.c
    set_discovery(c, *[cand(f"P{i}") for i in range(12)])
    app.deiconify()
    app.geometry("900x640")
    c.go(S.OLD_DISCOVER)
    app.update()
    v = app.views[S.OLD_DISCOVER]
    assert v.table_rows() == 8
    assert v.tree.winfo_height() >= 8 * v._row_px() - 8  # 창이 낮아도 표 자체는 줄지 않는다
    assert v.scroll.can_scroll()  # 낮은 창에서는 표를 줄이지 않고 가운데 영역 전체가 스크롤된다
    app.withdraw()


def test_tk_discover_info_fold_columns_and_total(ctx):
    app, c = ctx.app, ctx.c
    a = cand("A")
    a.config_files, a.config_bytes, a.config_complete = 4, 1024, True
    set_discovery(c, a, cand("B"))
    c.go(S.OLD_DISCOVER)
    app.update()
    v = app.views[S.OLD_DISCOVER]
    assert v.info.cget("text") == T.DISC_HINT_SHORT and v.more.toggle_btn.cget("text") == T.DISC_MORE_TOGGLE and not v.more.body.winfo_manager()
    v.more.toggle()
    assert v.more.toggle_btn.cget("text") == T.DISC_MORE_TOGGLE_OPEN
    assert [v.tree.heading(c_)["text"] for c_ in v.tree["columns"]][4:] == list(T.DISC_COLS_V2[4:])
    vals = [v.tree.item(i)["values"] for i in v.tree.get_children()]
    assert vals[0][4] == "4개" and vals[0][5] == "1.0KB" and vals[1][4] == T.DISC_CFG_UNKNOWN
    v._select_all()
    app.update()
    assert v.total.cget("text").startswith("선택 2개 · 설정 파일 4개 · 약 1.0KB")


def test_tk_discover_all_mode_checkbox_confirms_and_reaches_export(ctx, tmp_path):
    app, c = ctx.app, ctx.c
    api = ApiB()
    c.api = api
    set_discovery(c, cand("A"))
    c.go(S.OLD_DISCOVER)
    app.update()
    v = app.views[S.OLD_DISCOVER]
    assert v.all_var.get() is False and c.state.extra_mode == "config" and not v.adv.body.winfo_manager()
    v.adv.toggle()
    assert v.all_check.cget("text") == T.DISC_ALL_MODE
    ctx.answers["askyesno"] = False
    v.all_var.set(True)
    v._all_changed()
    assert v.all_var.get() is False and c.state.extra_mode == "config"  # 확인에서 거절하면 켜지지 않는다
    ctx.answers["askyesno"] = True
    v.all_var.set(True)
    v._all_changed()
    app.update()
    assert c.state.extra_mode == "all" and v.tree.heading("cfg")["text"] == T.DISC_COL_FILES_ALL
    c.go(S.OLD_EXPORT)
    app.update()
    ex = app.views[S.OLD_EXPORT]
    assert T.EXP_MODE_ALL_NOTE in ex.plan.cget("text")
    ex.path_var.set(str(tmp_path / "o.zip"))
    ex._export()
    app.update()
    assert api.export_calls == ["all"]


# --------------------------------------------------------------------------- 화면 6(tk)
def goto_preview(ctx, api=None):
    c = ctx.c
    ctx.api = c.api = api or ApiB()
    c.set_package("C:\\fake\\pkg.zip")
    c.start_preview()
    c.go(S.NEW_PREVIEW)
    ctx.app.update()
    return ctx.app.views[S.NEW_PREVIEW]


def test_tk_preview_program_table_check_toggle_recompute(ctx):
    app, c = ctx.app, ctx.c
    v = goto_preview(ctx)
    api = ctx.api
    assert v.prog_box.winfo_manager() and len(v.prog_tree.get_children()) == 3 and T.PREV_PROG_NOTE == v.prog_note.cget("text")
    assert [v.prog_tree.heading(c_)["text"] for c_ in v.prog_tree["columns"]] == list(T.PREV_PROG_COLS)
    first = v.prog_tree.item(v.prog_tree.get_children()[0])["values"]
    assert first[0] == T.CHECKED and str(first[1]) == "revit" and list(map(str, first[2:])) == ["1", "1", "0"]
    assert "disabled" not in v.next_btn.state()
    iid = v.prog_tree.get_children()[1]
    v._prog_toggle(iid)  # autocad 체크를 끈다
    app.update()
    iid = v.prog_tree.get_children()[1]  # 다시 그려졌으므로 줄 번호표를 새로 읽는다
    assert c.state.restore_skip == {"autocad"} and v.prog_tree.item(iid)["values"][0] == T.UNCHECKED
    assert list(map(str, v.prog_tree.item(iid)["values"][2:])) == [T.PREV_PROG_NA] * 3
    assert "disabled" in v.next_btn.state() and v.dirty.cget("text") == T.PREV_PROG_DIRTY and v.recalc_btn.winfo_manager()
    assert v.next_btn.master.winfo_children() and T.G_RESTORE_DIRTY in v.next_reason.cget("text")  # 다시 계산 전에는 다음으로 못 간다
    v._recompute_now()  # 400ms 뒤 자동으로 하는 일을 바로 실행
    app.update()
    assert api.import_calls[-1]["skip"] == ("autocad",) and api.import_calls[-1]["dry_run"]
    assert "disabled" not in v.next_btn.state() and not v.recalc_btn.winfo_manager() and not v.dirty.winfo_manager()
    statuses = [v.tree.item(i)["values"][0] for i in v.tree.get_children()]
    assert T.PREV_STATUS["not_selected"] in statuses
    assert "덮어씀 1" in v.summary.cget("text") or "1" in v.summary.cget("text")


def test_tk_preview_all_unchecked_blocks_next(ctx):
    app, c = ctx.app, ctx.c
    v = goto_preview(ctx)
    for _ in range(3):  # 체크할 때마다 표가 다시 그려지므로 맨 위 줄 하나씩 끈다
        on = [i for i in v.prog_tree.get_children() if v.prog_tree.item(i)["values"][0] == T.CHECKED]
        v._prog_toggle(on[0])
    v._recompute_now()
    app.update()
    assert "disabled" in v.next_btn.state() and v.dirty.cget("text") == T.PREV_PROG_NONE_SELECTED


def test_tk_preview_toggle_when_old_api_shows_guard_dialog(ctx):
    app, c = ctx.app, ctx.c
    v = goto_preview(ctx, FakeApi())
    c.state.preview = real_api.ImportResult(make_plan(), "dry-run", 0)
    v._render()
    app.update()
    v._prog_toggle(v.prog_tree.get_children()[0])
    v._recompute_now()
    assert ctx.shown and ctx.shown[-1][0] == "warn" and T.G_RESTORE_SELECT_UNSUPPORTED in str(ctx.shown[-1][1])
    assert "disabled" in v.next_btn.state()  # 조용히 넘어가지 않는다


def test_tk_confirm_numbers_follow_selection(ctx):
    app, c = ctx.app, ctx.c
    v = goto_preview(ctx)
    c.set_restore_program("autocad", False)
    c.restart_preview_for_selection()
    c.go(S.NEW_CONFIRM)
    app.update()
    cv = app.views[S.NEW_CONFIRM]
    assert T.CONF_BIG.format(over=1) == cv.big_label.cget("text")  # 덮어쓸 파일: revit 1개만
    assert T.CONF_SELECTED_NOTE.format(n=2, m=3, k=1) in cv.info.cget("text")
    asked = []
    import tkinter.messagebox as mb
    orig = mb.askyesno
    mb.askyesno = lambda title, msg, **k: (asked.append(msg), True)[1]
    try:
        c.set_preview_checked(True)
        c.set_bak_understood(True)
        cv._run()
    finally:
        mb.askyesno = orig
    assert asked and T.CONF_DIALOG.format(n=3) == asked[0]  # 선택 안 한 프로그램의 파일은 뺀 개수
    assert c.state.import_result is not None


def goto_result(ctx, api=None):
    c = ctx.c
    ctx.api = c.api = api or ApiB()
    c.set_package("C:\\fake\\pkg.zip")
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    ctx.app.update()
    return ctx.app.views[S.NEW_RESULT]


def test_tk_result_program_table_and_undo_flow(ctx):
    app, c = ctx.app, ctx.c
    v = goto_result(ctx)
    api = ctx.api
    assert c.state.screen == S.NEW_RESULT and v.prog_box.winfo_manager()
    assert set(v.undo_buttons) == {"revit", "autocad", "vscode"}
    assert "disabled" not in v.undo_buttons["revit"].state() and "disabled" in v.undo_buttons["vscode"].state()
    assert v.undo_labels["vscode"].cget("text") == T.UNDO_NOTHING and T.UNDO_NOTE in v.undo_msg.cget("text")
    assert "새로 생긴 파일은 지우지 않" in T.UNDO_NOTE
    ctx.answers["askyesno"] = False
    v.undo_buttons["revit"].invoke()
    app.update()
    assert [x["dry_run"] for x in api.undo_calls] == [True]  # 미리보기는 했고, 확인에서 거절해 실제로는 되돌리지 않았다
    ctx.answers["askyesno"] = True
    v.undo_buttons["revit"].invoke()
    app.update()
    assert [x["dry_run"] for x in api.undo_calls] == [True, True, False]
    assert api.undo_calls[-1]["record"] == "C:\\fake\\record.json"
    assert "disabled" in v.undo_buttons["revit"].state() and v.undo_labels["revit"].cget("text") == T.UNDO_DONE_LABEL.format(n=1)
    assert "새로 생긴 파일은 지우지 않았습니다" in v.undo_msg.cget("text") and v.undo_msg.cget("text").startswith(T.ICON_OK)


def test_tk_result_without_record_disables_undo_with_reason(ctx):
    v = goto_result(ctx, ApiB(record_path=""))
    ctx.app.update()
    assert all("disabled" in b.state() for b in v.undo_buttons.values())
    assert {lbl.cget("text") for lbl in v.undo_labels.values()} == {T.UNDO_NO_RECORD}


def test_tk_result_unchecked_program_shows_dash_and_no_undo(ctx):
    c = ctx.c
    ctx.c.api = ctx.api = ApiB()
    c.set_package("C:\\fake\\pkg.zip")
    c.start_preview()
    c.set_restore_program("autocad", False)
    c.restart_preview_for_selection()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    ctx.app.update()
    v = ctx.app.views[S.NEW_RESULT]
    assert "disabled" in v.undo_buttons["autocad"].state() and v.undo_labels["autocad"].cget("text") == T.UNDO_NOTHING
    assert "disabled" not in v.undo_buttons["revit"].state()


# --------------------------------------------------------------------------- 화면 7(tk)
def first_int(value) -> int:
    import re
    return int(re.findall(r"\d+", str(value))[0])


def export_result(failed=()):
    return SimpleNamespace(manifest={"rules": [{"id": "x"}], "files": [{}] * 3, "skipped": [], "failed": list(failed), "manual_warnings": [],
                                     "coverage": [{"rule": "a", "name": "Demo", "description": "설정", "status": "담김", "files": 3},
                                                  {"rule": "a", "name": "Demo", "description": "플러그인", "status": "이 PC에 없음", "files": 0}]})


def test_tk_export_result_area_is_compact_and_cov_folded(ctx, tmp_path):
    app, c = ctx.app, ctx.c
    c.go(S.OLD_EXPORT)
    app.update()
    v = app.views[S.OLD_EXPORT]
    c.state.export_output = str(tmp_path / "o.zip")
    res = export_result()
    c.state.export_result = res
    v.on_event("export_done", res)
    app.update()
    assert v.cov_fold.winfo_manager() and not v.cov_fold.body.winfo_manager()  # 확인표는 접혀 있다
    assert "담김" in v.cov_fold.toggle_btn.cget("text") and "해당 없음 1" in v.cov_fold.toggle_btn.cget("text")
    assert first_int(v.next_box.pack_info()["pady"]) <= 4 and first_int(v.summary.pack_info()["pady"]) <= 3  # 상자 사이 여백을 절반쯤으로
    v.cov_fold.toggle()
    app.update()
    assert v.cov.winfo_manager() and v.cov_fold.body.winfo_manager()


def test_tk_export_exception_shows_retry_restart_buttons(ctx, tmp_path):
    app, c = ctx.app, ctx.c
    api = ApiB()
    c.api = api
    seen = []

    def boom(*a, resume=True, **k):
        seen.append(resume)
        raise real_api.ApiError("설정 묶음 파일 만들기 실패")
    api.run_export = boom
    c.go(S.OLD_EXPORT)
    app.update()
    v = app.views[S.OLD_EXPORT]
    v.path_var.set(str(tmp_path / "o.zip"))
    v._export()
    app.update()
    assert seen == [True] and v.stop_box.winfo_manager()
    assert v.retry_save_btn.cget("text") == T.BTN_EXP_RETRY_SAVEPOINT and v.restart_btn.cget("text") == T.BTN_EXP_RESTART
    v.retry_save_btn.invoke()
    v.restart_btn.invoke()
    app.update()
    assert seen == [True, True, False]  # 이어서(저장점부터) / 처음부터
    api.run_export = lambda *a, **k: export_result()
    v.retry_save_btn.invoke()
    app.update()
    assert not v.stop_box.winfo_manager()


def test_tk_auto_last_screen_has_retry_panel_and_updates_to_done(ctx, tmp_path):
    app, c = ctx.app, ctx.c
    locked = {"src": "C:\\x\\LOCK", "error": "사용 중", "reason": "in_use"}
    out = tmp_path / "o.zip"
    out.write_bytes(b"z")
    c.auto = AutoRun("old", [AutoStep("scan", "조사", "done"), AutoStep("export", "묶음", "warn")], status="done",
                     export_path=str(out), warning=T.AUTO_UNREADABLE_WARN.format(n=1))
    res = export_result([locked])
    c.state.export_result, c.state.export_output = res, str(out)
    c.go(S.AUTO)
    app.update()
    v = app.views[S.AUTO]
    app.on_event("export_done", res)
    v._gate_sig = ()
    v.refresh()
    app.update()
    assert v.head.cget("text") == T.AUTO_DONE_WARN_HEAD
    assert v.retry_panel._shown and T.EXP_RETRY_HEAD.format(n=1) == v.retry_panel.head.cget("text")
    assert v.cov_fold.winfo_manager() and v.next_box.winfo_manager() and "disabled" not in v.retry_panel.btn.state()

    def retry_ok(rules_dir, output, **k):
        m = export_result()
        m.manifest["retry"] = {"added": [{"src": "x"}], "gone": [], "not_retried": [], "unchanged": False, "backup": str(out) + ".bak", "total": 1}
        return real_api.RetryResult(m.manifest, output)
    c.api.run_export_retry = retry_ok
    v.retry_panel.retry()  # 확인창은 가짜(예)
    app.update()
    assert c.auto.warning == "" and v.head.cget("text") == T.AUTO_DONE_HEAD  # '끝났지만 확인 필요' -> '완료'
    assert T.AUTO_DONE_RETRY_OK in v.msg.cget("text") and v.retry_panel.head.cget("text") == T.EXP_RETRY_ALL_DONE


def test_tk_auto_stopped_export_has_three_buttons(ctx, tmp_path):
    app, c = ctx.app, ctx.c
    auto_old(c, str(tmp_path / "o.zip"))
    c.auto.manual = S.OLD_EXPORT
    c.go(S.AUTO)
    app.update()
    v = app.views[S.AUTO]
    v._gate_sig = ()
    v.refresh()
    app.update()
    labels = [w.cget("text") for w in v.out.winfo_children()]
    assert labels == [T.BTN_EXP_RETRY_SAVEPOINT, T.BTN_EXP_RESTART, T.AUTO_BTN_MANUAL]
    seen = []
    c.api.run_export = lambda *a, resume=True, **k: (seen.append(resume), export_result())[1]
    v.out.winfo_children()[1].invoke()  # 처음부터 다시 만들기(확인창은 가짜 '예')
    app.update()
    assert seen == [False] and c.auto.status == "done"


# --------------------------------------------------------------------------- 문서/문구
def test_docs_mention_stage30b_and_stay_synced():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    md = (root / "docs" / "field-guide.md").read_text(encoding="utf-8")
    ht = (root / "docs" / "field-guide.html").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for needle in ("지금 강제로 멈추기", "프로그램별 되돌리기", "폴더 전체 담기"):
        assert needle in md and needle in ht and needle in readme, needle


def test_stage30b_texts_are_clean_and_polite():
    import re
    names = [n for n in dir(T) if n.startswith(("BTN_STOP", "STOP", "FORCE", "DISC_", "PREV_PROG", "UNDO", "RES_PROG", "EXP_STOPPED", "AUTO_DONE_RETRY"))]
    assert "BTN_STOP" in names and "UNDO_CONFIRM" in names and "FORCE_STOPPED" in names
    for n in names:
        v = getattr(T, n)
        for t in ([v] if isinstance(v, str) else [x for x in v if isinstance(x, str)] if isinstance(v, tuple) else []):
            for sentence in re.split(r"(?<=[.?!])\s+|\n", t):
                s = sentence.strip().rstrip(")")
                assert not (re.search(r"[가-힣]다\.$", s) and not re.search(r"(니다|습니다)\.$", s)), (n, s)
