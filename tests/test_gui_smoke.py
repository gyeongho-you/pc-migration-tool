"""tkinter 스모크 테스트: 창을 숨긴 채 만들고 화면 전환/버튼 상태를 확인한다. tkinter/디스플레이가 없으면 건너뛴다."""
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import messagebox  # noqa: E402

from pcmigrate import api as real_api  # noqa: E402
from pcmigrate.gui import state as S  # noqa: E402
from pcmigrate.gui.app import App, ErrorDialog  # noqa: E402
from pcmigrate.gui.controller import Controller, FriendlyError  # noqa: E402
from pcmigrate.gui.runner import Runner  # noqa: E402

from test_gui_controller import FakeApi, item, plan  # noqa: E402


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    answers = {"askyesno": True, "askokcancel": True}
    shown = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: answers["askyesno"])
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: answers["askokcancel"])
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: shown.append(("warn", a)))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(("info", a)))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(("error", a)))
    api = FakeApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path,
                   make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    app, last = None, None
    for _ in range(4):  # 이 PC 에서 Tcl 초기화가 드물게 일시 실패하는 일이 있어 재시도(그래도 안 되면 건너뜀)
        try:
            app = App(c)
            break
        except tk.TclError as e:
            last = e
            time.sleep(0.5)
    if app is None:
        pytest.skip(f"디스플레이/Tcl 을 사용할 수 없음: {last}")
    app.withdraw()
    app.update()
    yield SimpleNamespace(app=app, c=c, api=api, answers=answers, shown=shown)
    app.destroy()


def children(tree):
    return [tree.item(i) for i in tree.get_children()]


def test_all_screens_open_and_header_footer_exist(ctx):
    app, c = ctx.app, ctx.c
    for scr in (S.START, S.OLD_SCAN, S.OLD_DISCOVER, S.OLD_CHECKLIST, S.OLD_EXPORT, S.NEW_PACKAGE, S.NEW_PREVIEW,
                S.NEW_CONFIRM, S.NEW_RESULT, S.TRANSFER, S.START):
        c.go(scr)
        app.update()
        assert app.crumb.cget("text") == ("처음 화면" if scr == S.START else S.TITLES[scr])
    assert set(app.views) == {S.START, S.OLD_SCAN, S.OLD_DISCOVER, S.OLD_CHECKLIST, S.OLD_EXPORT, S.NEW_PACKAGE,
                              S.NEW_PREVIEW, S.NEW_CONFIRM, S.NEW_RESULT, S.TRANSFER}
    header = " ".join(str(w.cget("text")) for w in app.winfo_children()[0].winfo_children() if hasattr(w, "cget"))
    assert "Test OS" in header and "관리자 권한: 아니오" in header
    assert app.minsize()[0] >= 800  # 창 크기 변경 가능(최소 크기만 지정)
    assert app.resizable() == (True, True)


def test_scan_view_fills_and_enables_next(ctx):
    v = ctx.app.views[S.OLD_SCAN]
    ctx.c.go(S.OLD_SCAN)
    assert "disabled" in v.nav["next"].state()
    ctx.api.run_scan = lambda rules_dir, deps=None: SimpleNamespace(
        program_count=5, matched=[1], discovery=(2, 3), checklist=None,
        match_info=[SimpleNamespace(rule=SimpleNamespace(name="Demo App"), evidence="이름 일치: Demo")])
    ctx.c.start_scan()
    ctx.app.update()
    assert "disabled" not in v.nav["next"].state()
    assert [c["values"][0] for c in children(v.tree)] == ["Demo App"]
    assert "5개" in v.summary.cget("text") and "규칙 없는 설치 프로그램 2개" in v.summary.cget("text")


def test_discover_view_secret_rows_highlighted_and_gated(ctx):
    app, c = ctx.app, ctx.c
    v = app.views[S.OLD_DISCOVER]
    c.go(S.OLD_DISCOVER)
    c.start_discover()
    app.update()

    def iid(secret):  # 선택할 때마다 목록을 다시 그려 iid 가 바뀌므로 매번 찾는다
        return next(i for i in v.tree.get_children() if ("secret" in v.tree.item(i, "tags")) == secret)

    def click(secret):
        target = iid(secret)
        v.tree.identify_row = lambda y, _t=target: _t
        v._click(SimpleNamespace(y=0))

    assert len(children(v.tree)) == 2
    assert "⚠" in v.tree.item(iid(True), "values")[1]
    assert "비밀 정보 가능성" in v.banner.cget("text")
    assert v.tree.item(iid(True), "values")[0] == "—"  # 동의 전에는 선택 불가 표시
    assert v.tree.item(iid(False), "values")[0] == "☐"

    click(True)  # 동의 없이 비밀 항목 클릭 -> 막힘 + 안내
    assert c.state.selected == set() and ctx.shown[-1][0] == "warn"

    click(False)
    assert len(c.state.selected) == 1 and v.tree.item(iid(False), "values")[0] == "☑"
    assert "선택한 항목: 1개" in v.count.cget("text")

    ctx.answers["askyesno"] = False  # 동의 대화상자에서 '아니오'
    v.ack_var.set(True)
    v._ack_changed()
    assert c.state.secrets_ack is False and v.ack_var.get() is False
    ctx.answers["askyesno"] = True
    v.ack_var.set(True)
    v._ack_changed()
    assert c.state.secrets_ack is True and v.tree.item(iid(True), "values")[0] == "☐"
    click(True)
    assert len(c.state.selected) == 2 and v.tree.item(iid(True), "values")[0] == "☑"
    v.ack_var.set(False)
    v._ack_changed()  # 동의 철회 -> 비밀 항목 선택 해제
    assert len(c.state.selected) == 1 and v.tree.item(iid(True), "values")[0] == "—"


def test_checklist_view_tabs(ctx):
    v = ctx.app.views[S.OLD_CHECKLIST]
    ctx.c.go(S.OLD_CHECKLIST)
    assert "disabled" in v.save_btn.state()
    ctx.c.start_checklist()
    ctx.app.update()
    assert "disabled" not in v.save_btn.state()
    body = v.texts["1. 먼저 설치"].get("1.0", "end")
    assert "winget import" in body and "☐ Rhino 8" in body
    assert "규칙 없는 설치 프로그램 1개" in v.texts["요약"].get("1.0", "end")
    assert v.nb.index("end") == 5


def test_export_view_requires_path_and_confirmation(ctx, tmp_path):
    v = ctx.app.views[S.OLD_EXPORT]
    ctx.c.go(S.OLD_EXPORT)
    v._export()
    assert ctx.shown[-1][0] == "warn" and not [x for x in ctx.api.calls if x[0] == "export"]
    v.path_var.set(str(tmp_path / "p.zip"))
    ctx.answers["askokcancel"] = False
    v._export()
    assert not [x for x in ctx.api.calls if x[0] == "export"]  # 확인에서 취소하면 쓰지 않음
    ctx.answers["askokcancel"] = True
    v._export()
    ctx.app.update()
    assert [x for x in ctx.api.calls if x[0] == "export"]
    assert "disabled" not in v.open_btn.state() and "패키지를 만들었습니다" in v.result.get("1.0", "end")


def test_new_pc_flow_preview_confirm_and_result(ctx):
    app, c, api = ctx.app, ctx.c, ctx.api
    api.preview = real_api.ImportResult(plan([item(extra=True), item("would_overwrite", backup="C:\\b.bak")],
                                              warnings=[{"name": "Demo", "items": ["재로그인"]}]), "dry-run", 1)
    pv = app.views[S.NEW_PACKAGE]
    c.go(S.NEW_PACKAGE)
    pv._next()  # 경로 비어 있음
    assert ctx.shown[-1][0] == "warn"
    pv.path_var.set("C:\\pkg.zip")
    pv._next()
    app.update()
    assert c.state.screen == S.NEW_PREVIEW and [x for x in api.calls if x[0] == "import"] == [("import", True, False, False)]
    prev = app.views[S.NEW_PREVIEW]
    rows = children(prev.tree)
    assert len(rows) == 2 and rows[0]["values"][3] == "규칙 없이 선택 포함"
    assert "검증 통과" in prev.problem_box.cget("text") and "재로그인" in prev.warn.get("1.0", "end")

    cf = app.views[S.NEW_CONFIRM]
    c.go(S.NEW_CONFIRM)
    assert "disabled" in cf.run_btn.state()
    cf.checked_var.set(True)
    cf._checked_changed()
    assert "disabled" in cf.run_btn.state()  # 별도 동의가 필요한 항목이 있으면 아직 닫힘
    cf.extra_var.set(True)
    cf._extra_changed()
    assert "disabled" not in cf.run_btn.state()
    ctx.answers["askyesno"] = False
    cf._run()
    assert not [x for x in api.calls if x[0] == "import" and x[2]] and c.state.screen == S.NEW_CONFIRM
    ctx.answers["askyesno"] = True
    cf._run()
    app.update()
    assert [x for x in api.calls if x[0] == "import" and x[2]] == [("import", False, True, True)]
    assert c.state.screen == S.NEW_RESULT
    assert "복사 1개" in app.views[S.NEW_RESULT].summary.cget("text")


def test_confirm_blocked_when_package_has_problems(ctx):
    app, c, api = ctx.app, ctx.c, ctx.api
    api.preview = real_api.ImportResult(plan([item()], problems=["해시 불일치"]), "blocked", 0)
    c.set_package("p.zip")
    c.start_preview()
    c.go(S.NEW_PREVIEW)
    prev = app.views[S.NEW_PREVIEW]
    assert "복원할 수 없습니다" in prev.problem_box.cget("text") and "disabled" in prev.nav["next"].state()
    c.go(S.NEW_CONFIRM)
    cf = app.views[S.NEW_CONFIRM]
    cf.checked_var.set(True)
    cf._checked_changed()
    assert "disabled" in cf.run_btn.state()


def test_transfer_view_path_check_and_button_gating(ctx, tmp_path):
    app, c, api = ctx.app, ctx.c, ctx.api
    v = app.views[S.TRANSFER]
    c.go(S.TRANSFER)
    src = tmp_path / "src"
    src.mkdir()
    v.src_var.set(str(src))
    v.dst_var.set(str(src / "inside"))
    app.update()
    assert "✖" in v.check_label.cget("text") and "disabled" in v.dry_btn.state()
    v.dst_var.set(str(tmp_path / "dst"))
    app.update()
    assert "✔" in v.check_label.cget("text") and "읽기만" in v.check_label.cget("text")
    assert "disabled" not in v.dry_btn.state() and "disabled" in v.go_btn.state()  # 미리보기 전에는 복사 불가
    assert v.cloud_var.get() is False and v.strict_var.get() is False  # 위험한 기본값 꺼짐
    v._dry()
    app.update()
    assert [x for x in api.calls if x[0] == "transfer"] == [("transfer", True, False, False)]
    assert "disabled" not in v.go_btn.state() and "미리보기" in v.summary.cget("text")
    v.dst_var.set(str(tmp_path / "other"))  # 대상을 바꾸면 다시 미리보기 필요
    app.update()
    assert "disabled" in v.go_btn.state()
    v.dst_var.set(str(tmp_path / "dst"))
    app.update()
    ctx.answers["askokcancel"] = False
    v._go()
    assert len([x for x in api.calls if x[0] == "transfer"]) == 1
    ctx.answers["askokcancel"] = True
    v.strict_var.set(True)
    v._go()
    app.update()
    assert api.calls[-1] == ("transfer", False, False, True)
    assert "검증 성공 1개" in v.summary.cget("text")


def test_error_dialog_hides_traceback_until_expanded(ctx):
    app = ctx.app
    app.on_event("error", FriendlyError("제목", "한국어 설명", "Traceback (most recent call last):\n  boom"))
    app.update()
    dlg = [w for w in app.winfo_children() if isinstance(w, ErrorDialog)][0]
    assert dlg.title() == "제목" and not dlg.shown and not dlg.detail.winfo_ismapped()
    dlg._toggle()
    dlg.update()
    assert dlg.shown and "Traceback" in dlg.detail.get("1.0", "end")
    dlg.destroy()


def test_busy_state_shows_progress_and_cancel_for_cancellable_tasks(ctx):
    app, c = ctx.app, ctx.c
    c.can_cancel = True
    app.on_event("busy", "대용량 복사")
    app.update()
    assert "대용량 복사" in app.status.cget("text") and app.cancel_btn.winfo_manager() == "pack"
    app.on_event("progress", "검증 중 25/100")
    assert app.status.cget("text") == "검증 중 25/100"
    app.on_event("busy", None)
    assert app.cancel_btn.winfo_manager() == "" and app.status.cget("text") == "준비됨"
    c.go(S.TRANSFER)
    c.state.busy = True
    app.go_home()
    assert ctx.shown[-1][0] == "info" and c.state.screen == S.TRANSFER  # 작업 중에는 처음으로 돌아가지 않음
    c.state.busy = False
    app.go_home()
    assert c.state.screen == S.START


def test_tick_pumps_runner_events(ctx):
    called = []
    ctx.c.runner.pump = lambda: called.append(1) or 0
    ctx.app._tick()
    assert called == [1]


def test_cli_gui_subcommand_registered_and_handles_missing_tkinter(monkeypatch, capsys):
    import sys
    from pcmigrate import cli
    assert "gui" in cli.build_parser()._subparsers._group_actions[0].choices
    monkeypatch.setitem(sys.modules, "pcmigrate.gui.app", None)  # import 실패를 흉내
    assert cli.main(["gui"]) == 1
    assert "tkinter" in capsys.readouterr().err
