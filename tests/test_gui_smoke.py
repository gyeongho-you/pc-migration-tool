"""tkinter 스모크 테스트: 창을 숨긴 채 만들고 화면 전환/버튼 상태/도움말/키 조작을 확인한다. tkinter/디스플레이가 없으면 건너뛴다."""
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import messagebox  # noqa: E402

from pcmigrate import api as real_api  # noqa: E402
from pcmigrate.copier import FileResult, Report  # noqa: E402
from pcmigrate.detect import Program  # noqa: E402
from pcmigrate.gui import state as S  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402
from pcmigrate.gui.app import MIN_SIZE, App, ErrorDialog  # noqa: E402
from pcmigrate.gui.controller import Controller, FriendlyError, friendly_error  # noqa: E402
from pcmigrate.gui.runner import Runner  # noqa: E402
from pcmigrate.gui.views.common import TextDialog, sort_tree  # noqa: E402

from test_gui_controller import FakeApi, item, plan  # noqa: E402

ALL_SCREENS = (S.START, S.OLD_SCAN, S.OLD_DISCOVER, S.OLD_CHECKLIST, S.OLD_EXPORT, S.NEW_PACKAGE, S.NEW_PREVIEW,
               S.NEW_CONFIRM, S.NEW_RESULT, S.TRANSFER)


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


def label_texts(widget):
    out = []
    for w in widget.winfo_children():
        try:
            out.append(str(w.cget("text")))
        except tk.TclError:
            pass
        out.extend(label_texts(w))
    return out


# --------------------------------------------------------------------- 화면 전환 / 도움말 / 레이아웃

def test_all_screens_open_and_header_footer_exist(ctx):
    app, c = ctx.app, ctx.c
    for scr in (*ALL_SCREENS, S.START):
        c.go(scr)
        app.update()
        assert app.crumb.cget("text") == (T.CRUMB_HOME if scr == S.START else T.TITLES[scr])
    assert set(app.views) == set(ALL_SCREENS)
    header = " ".join(str(w.cget("text")) for w in app.winfo_children()[0].winfo_children() if hasattr(w, "cget"))
    assert "Test OS" in header and T.HEADER_ADMIN[False] in header
    assert app.minsize() == MIN_SIZE and MIN_SIZE[0] >= 900 and app.resizable() == (True, True)


def test_every_screen_has_help_button_and_popup_opens_and_closes(ctx):
    app, c = ctx.app, ctx.c
    for scr in ALL_SCREENS:
        c.go(scr)
        app.update()
        view = app.views[scr]
        assert view.help_btn.cget("text") == T.BTN_HELP
        view.help_btn.invoke()  # '?' 단추
        app.update()
        dlg = app.help_dialog
        assert isinstance(dlg, TextDialog) and dlg.winfo_exists()
        assert dlg.title() == T.HELP_TITLE.format(screen=T.TITLES.get(scr, T.CRUMB_HOME))
        body = dlg.text.get("1.0", "end")
        assert "무엇을 하는 화면인가요?" in body and "안전한가요?" in body and "다음 단계" in body
        assert body.strip() == T.HELP[scr].strip()
        dlg.close_btn.invoke()
        app.update()
        assert not dlg.winfo_exists()  # 닫기 단추를 누르면 창이 사라진다


def test_start_screen_guides_and_license_banner(ctx):
    app = ctx.app
    v = app.views[S.START]
    texts = label_texts(v)
    assert T.START_INTRO in texts and len(T.START_INTRO.splitlines()) <= 3
    for title, desc, btn, _t in T.START_CHOICES:
        assert desc in texts and btn in texts  # 각 선택지 아래 한 줄 설명
    assert v.license_box.cget("text") == T.LICENSE_BANNER and "라이선스는 복원하지 않습니다" in v.license_box.cget("text")
    assert v.license_box.cget("bg") != "" and v.license_box.cget("font")  # 눈에 띄는 색 상자 + 굵은 글씨
    assert [b.cget("text") for b in v.buttons] == [c[2] for c in T.START_CHOICES]


def test_nav_bar_is_last_in_tab_order_and_buttons_are_verbs(ctx):
    app = ctx.app
    for scr in ALL_SCREENS:
        v = app.views[scr]
        if v._nav is not None:
            assert v.winfo_children()[-1] is v._nav, scr  # 본문 -> 이동 단추 순서로 Tab 이동
    assert app.views[S.OLD_SCAN].next_btn.cget("text") == T.BTN_NEXT
    assert app.views[S.OLD_SCAN].back_btn.cget("text") == T.BTN_BACK
    assert app.views[S.NEW_PACKAGE].next_btn.cget("text") == T.BTN_PREVIEW_NEXT
    assert app.views[S.NEW_CONFIRM].primary.cget("text") == T.BTN_APPLY


def test_window_resize_down_to_minimum_does_not_break_layout(ctx):
    app = ctx.app
    app.geometry("1400x900")
    app.update()
    for scr in ALL_SCREENS:
        ctx.c.go(scr)
        app.update()
    app.geometry(f"{MIN_SIZE[0]}x{MIN_SIZE[1]}")
    app.update()
    for scr in ALL_SCREENS:
        ctx.c.go(scr)
        app.update()
        v = app.views[scr]
        assert v.winfo_width() <= app.winfo_width() + 2
        if v._nav is not None:  # 이동 막대가 창 밖으로 잘리지 않음
            assert v._nav.winfo_y() + v._nav.winfo_height() <= v.winfo_height() + 2, scr


# --------------------------------------------------------------------- 키 조작

def test_enter_runs_primary_button_and_escape_goes_back_or_cancels(ctx):
    app, c, api = ctx.app, ctx.c, ctx.api
    c.go(S.OLD_SCAN)
    app.update()
    app.focus_get = lambda: app.crumb  # 버튼이 아닌 위젯에 포커스가 있을 때
    assert app._on_return(None) == "break" and [x for x in api.calls if x[0] == "scan"] == [("scan",)]
    app.focus_get = lambda: app.views[S.OLD_SCAN].btn  # 버튼에 포커스가 있으면 그 버튼을 누른다
    app._on_return(None)
    assert len([x for x in api.calls if x[0] == "scan"]) == 2
    assert app._on_escape(None) == "break" and c.state.screen == S.START  # Esc = 이전 화면
    assert app._on_escape(None) is None  # 처음 화면에는 이전이 없음
    app.focus_get = lambda: tk.Text(app)  # 여러 줄 입력창에서는 Enter 가 줄바꿈이므로 가로채지 않음
    assert app._on_return(None) is None
    c.state.busy, c.can_cancel = True, True
    c.cancel_token = real_api.CancelToken()
    assert app._on_escape(None) == "break" and c.cancel_token.is_set()  # 작업 중 Esc = 취소


def test_dialog_closes_with_enter_and_escape(ctx):
    app = ctx.app
    app.on_event("error", FriendlyError("제목", "무엇이", "어떻게", "TB"))
    app.update()
    dlg = [w for w in app.winfo_children() if isinstance(w, ErrorDialog)][0]
    assert dlg.bind("<Return>") and dlg.bind("<Escape>")  # 키가 연결되어 있고
    assert dlg.close_event() == "break"  # 눌렀을 때 닫히며 다른 화면의 Enter/Esc 와 겹치지 않는다
    app.update()
    assert not [w for w in app.winfo_children() if isinstance(w, ErrorDialog)]
    app.show_help(S.START)
    help_dlg = app.help_dialog
    assert help_dlg.bind("<Return>") and help_dlg.bind("<Escape>")
    assert help_dlg.close_event() == "break"


# --------------------------------------------------------------------- 구 PC 화면

def test_scan_view_fills_and_enables_next_with_reason(ctx):
    v = ctx.app.views[S.OLD_SCAN]
    ctx.c.go(S.OLD_SCAN)
    assert "disabled" in v.next_btn.state() and v.next_reason.cget("text") == T.WHY_SCAN_FIRST  # 막힌 이유 표시
    ctx.api.run_scan = lambda rules_dir, deps=None: SimpleNamespace(
        program_count=5, matched=[1], discovery=(2, 3), checklist=None,
        match_info=[SimpleNamespace(rule=SimpleNamespace(name="Demo App"), evidence="이름 일치: Demo")])
    ctx.c.start_scan()
    ctx.app.update()
    assert "disabled" not in v.next_btn.state() and v.next_reason.cget("text") == ""
    assert [c["values"][0] for c in children(v.tree)] == ["Demo App"]
    assert v.summary.cget("text") == T.SCAN_RESULT.format(programs=5, rules=1, more=T.SCAN_RESULT_EXTRA.format(n=2))


def test_discover_view_secret_rows_highlighted_gated_and_long_paths(ctx):
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
    assert T.ICON_WARN in v.tree.item(iid(True), "values")[1] and "민감" in v.tree.item(iid(True), "values")[1]  # 색 + 아이콘 + 문구
    assert "민감한 정보 가능성" in v.banner.cget("text") or "민감한 정보" in v.banner.cget("text")
    assert v.tree.item(iid(True), "values")[0] == T.LOCKED and v.tree.item(iid(False), "values")[0] == T.UNCHECKED

    click(True)  # 동의 없이 민감 항목 클릭 -> 막힘 + 안내
    assert c.state.selected == set() and ctx.shown[-1][0] == "warn" and ctx.shown[-1][1][1] == T.G_SECRET_SELECT
    click(False)
    assert len(c.state.selected) == 1 and v.tree.item(iid(False), "values")[0] == T.CHECKED
    assert T.DISC_SELECTED.format(n=1) in v.count.cget("text")

    ctx.answers["askyesno"] = False  # 동의 대화상자에서 '아니오'
    v.ack_var.set(True)
    v._ack_changed()
    assert c.state.secrets_ack is False and v.ack_var.get() is False
    ctx.answers["askyesno"] = True
    v.ack_var.set(True)
    v._ack_changed()
    assert c.state.secrets_ack is True and v.tree.item(iid(True), "values")[0] == T.UNCHECKED
    click(True)
    assert len(c.state.selected) == 2 and v.tree.item(iid(True), "values")[0] == T.CHECKED
    v.ack_var.set(False)
    v._ack_changed()  # 동의 철회 -> 민감 항목 선택 해제
    assert len(c.state.selected) == 1 and v.tree.item(iid(True), "values")[0] == T.LOCKED


def test_discover_long_path_is_ellipsized_with_full_path_tooltip_and_sortable(ctx):
    from pcmigrate.discover import Candidate, Discovery, Link, ProgramEntry
    long_path = "C:\\Users\\someone\\AppData\\Roaming\\" + "VeryLongVendorName\\" * 6 + "Product"
    d = Discovery()
    c1 = Candidate("%APPDATA%", "Product", long_path, True, Link(["Zeta Tool"], "추정", "이유"), size=10)
    c2 = Candidate("%APPDATA%", "Alpha", "C:\\a\\Alpha", True, Link(["Alpha Tool"], "추정", "이유"), size=10)
    d.programs = [ProgramEntry(Program("Zeta Tool"), [c1]), ProgramEntry(Program("Alpha Tool"), [c2])]
    ctx.c.state.discovery = d
    v = ctx.app.views[S.OLD_DISCOVER]
    ctx.c.go(S.OLD_DISCOVER)
    ctx.app.update()
    row = next(i for i in v.tree.get_children() if v.rows[i].path == long_path)
    shown = v.tree.item(row, "values")[3]
    assert shown != long_path and "…" in shown and len(shown) <= 60
    assert v.tips.text_at(row, 3) == long_path  # 마우스를 올리면 전체 경로
    v.tips.show_now(row, 3, 100, 100)
    assert v.tips.tip is not None
    v.tips.hide()
    assert v.tips.tip is None
    sort_tree(v.tree, "prog", False)  # 열 제목을 눌러 정렬
    assert [v.tree.item(i, "values")[2] for i in v.tree.get_children()] == ["Alpha Tool", "Zeta Tool"]
    sort_tree(v.tree, "prog", True)
    assert [v.tree.item(i, "values")[2] for i in v.tree.get_children()] == ["Zeta Tool", "Alpha Tool"]
    assert "▼" in v.tree.heading("prog", "text")


def make_checklist_state(ctx):
    from pcmigrate.checklist import build_checklist
    cl = build_checklist([("Demo", ["라이선스 재로그인"])], [Program("Rhino 8"), Program("Google Chrome")],
                         [Program("a.b", source="winget", winget_id="a.b")], discovery=(1, 2), show_all=True)
    ctx.c.state.checklist = SimpleNamespace(checklist=cl, text="", discovery=(1, 2))
    ctx.c.go(S.OLD_CHECKLIST)
    ctx.app.on_event("checklist_done", None)
    ctx.app.update()


def test_checklist_view_tabs_checkboxes_persist_and_hide_toggle(ctx):
    v = ctx.app.views[S.OLD_CHECKLIST]
    ctx.c.go(S.OLD_CHECKLIST)
    assert "disabled" in v.save_btn.state() and v.reason.cget("text") == T.WHY_CHECKLIST_FIRST
    make_checklist_state(ctx)
    assert "disabled" not in v.save_btn.state() and v.reason.cget("text") == ""
    assert [v.nb.tab(i, "text") for i in range(v.nb.index("end"))] == list(T.CL_TABS)
    hidden = ctx.c.checklist_hidden_count()
    assert hidden >= 1 and v.hidden_label.cget("text") == T.CL_HIDDEN.format(n=hidden)  # '관련 없는 경고 N건 숨김'
    login = v.lists[T.CL_TABS[2]]
    n_default = len(login.vars)
    login.vars[0].set(True)  # 체크
    login.widgets[0][0].invoke()  # 실제 클릭 동작(체크 상태 토글)
    first_text = login.widgets[0][1]
    done_before, total = ctx.c.checklist_progress()
    assert v.progress_label.cget("text") == T.CL_PROGRESS_MARK.format(done=done_before, total=total)
    v.all_var.set(True)  # 모두 보기: --all 과 같은 동작
    v._rebuild()
    assert len(v.lists[T.CL_TABS[2]].vars) > n_default and v.hidden_label.cget("text") == ""
    v.all_var.set(False)
    v._rebuild()
    again = v.lists[T.CL_TABS[2]]
    idx = [t for _w, t in again.widgets].index(first_text)
    assert again.vars[idx].get() == ((T.CL_TABS[2], first_text) in ctx.c.checklist_marks)  # 화면을 다시 만들어도 체크 유지
    v.filter_var.set("Rhino")  # 간단한 검색: 안내문 아래의 항목이 검색어를 포함하는 것만 남는다
    rest = [t for _w, t in v.lists[T.CL_TABS[1]].widgets[len(T.CL_GUIDE):]]
    assert rest and all("rhino" in t.lower() for t in rest)
    v.filter_var.set("")
    guide = v.lists[T.CL_TABS[1]].widgets[:len(T.CL_GUIDE)]
    assert [t for _w, t in guide] == list(T.CL_GUIDE)


def test_export_view_reason_requires_path_and_confirmation_and_shows_summary(ctx, tmp_path):
    v = ctx.app.views[S.OLD_EXPORT]
    ctx.c.go(S.OLD_EXPORT)
    assert "disabled" in v.go_btn.state() and v.reason.cget("text") == T.WHY_NEED_OUTPUT
    v._export()
    assert ctx.shown[-1][0] == "warn" and not [x for x in ctx.api.calls if x[0] == "export"]
    v.path_var.set(str(tmp_path / "p.zip"))
    assert "disabled" not in v.go_btn.state() and v.reason.cget("text") == ""
    ctx.answers["askokcancel"] = False
    v._export()
    assert not [x for x in ctx.api.calls if x[0] == "export"]  # 확인에서 취소하면 쓰지 않음
    ctx.answers["askokcancel"] = True
    v._export()
    ctx.app.update()
    assert [x for x in ctx.api.calls if x[0] == "export"]
    assert "disabled" not in v.open_btn.state() and T.EXP_DONE.format(path=str(tmp_path / "p.zip")) in v.result.get("1.0", "end")
    assert v.summary.ok_label.cget("text") == f"{T.ICON_OK} {T.RESULT_OK} 0"


# --------------------------------------------------------------------- 신 PC 화면

def test_new_pc_flow_preview_confirm_and_result_summary(ctx):
    app, c, api = ctx.app, ctx.c, ctx.api
    api.preview = real_api.ImportResult(plan([item(extra=True), item("would_overwrite", backup="C:\\b.bak")],
                                              warnings=[{"name": "Demo", "items": ["재로그인"]}]), "dry-run", 1)
    pv = app.views[S.NEW_PACKAGE]
    c.go(S.NEW_PACKAGE)
    assert "disabled" in pv.next_btn.state() and pv.next_reason.cget("text") == T.WHY_PACKAGE_FIRST
    pv._next()  # 경로 비어 있음
    assert ctx.shown[-1][0] == "warn"
    pv.path_var.set("C:\\pkg.zip")
    assert "disabled" not in pv.next_btn.state()
    pv._next()
    app.update()
    assert c.state.screen == S.NEW_PREVIEW and [x for x in api.calls if x[0] == "import"] == [("import", True, False, False)]
    prev = app.views[S.NEW_PREVIEW]
    rows = children(prev.tree)
    assert len(rows) == 2 and rows[0]["values"][3] == T.PREV_KIND_EXTRA and rows[1]["values"][0] == T.PREV_STATUS["would_overwrite"]
    assert prev.problem_box.cget("text") == T.PREV_OK and "재로그인" in prev.warn.get("1.0", "end")
    assert prev.summary.cget("text") == T.PREV_SUMMARY.format(new=1, over=1, same=0)

    cf = app.views[S.NEW_CONFIRM]
    c.go(S.NEW_CONFIRM)
    assert "disabled" in cf.run_btn.state() and cf.why.cget("text") == T.G_NEED_CHECK_PREVIEW  # 이유 표시
    cf.checked_var.set(True)
    cf._checked_changed()
    assert "disabled" in cf.run_btn.state() and cf.why.cget("text") == T.G_NEED_EXTRA_CONSENT  # 별도 동의 필요
    cf.extra_var.set(True)
    cf._extra_changed()
    assert "disabled" not in cf.run_btn.state() and cf.why.cget("text") == ""
    ctx.answers["askyesno"] = False
    cf._run()
    assert not [x for x in api.calls if x[0] == "import" and x[2]] and c.state.screen == S.NEW_CONFIRM
    ctx.answers["askyesno"] = True
    cf._run()
    app.update()
    assert [x for x in api.calls if x[0] == "import" and x[2]] == [("import", False, True, True)]
    assert c.state.screen == S.NEW_RESULT
    res = app.views[S.NEW_RESULT]
    assert res.summary.ok_label.cget("text") == f"{T.ICON_OK} {T.RESULT_OK} 1"
    assert res.summary.skip_label.cget("text") == f"{T.RESULT_SKIPPED} 0" and res.summary.box.cget("text") == T.RES_ALL_OK


def test_import_failure_shows_red_box_with_next_step(ctx):
    app, c = ctx.app, ctx.c
    rep = Report(dry_run=False, results=[FileResult("e", "C:\\a", "failed", error="SHA-256 불일치"),
                                         FileResult("e", "C:\\b", "copied")])
    c.state.import_result = real_api.ImportResult(plan(), "applied", 0, rep)
    c.go(S.NEW_RESULT)
    app.update()
    res = app.views[S.NEW_RESULT]
    assert res.summary.fail_label.cget("text") == f"{T.ICON_FAIL} {T.RESULT_FAILED} 1"  # 아이콘 + 문구(색에만 의존하지 않음)
    assert res.summary.box.cget("text") == T.RES_IMPORT_FAIL and "다시" in T.RES_IMPORT_FAIL
    fail = [r for r in children(res.tree) if "fail" in r["tags"]]
    assert fail and "내용이 원본과 다름" in fail[0]["values"][2] and "SHA" not in fail[0]["values"][2]


def test_confirm_blocked_when_package_has_problems(ctx):
    app, c, api = ctx.app, ctx.c, ctx.api
    api.preview = real_api.ImportResult(plan([item()], problems=["크기/해시 불일치: data/a"]), "blocked", 0)
    c.set_package("p.zip")
    c.start_preview()
    c.go(S.NEW_PREVIEW)
    prev = app.views[S.NEW_PREVIEW]
    assert "복원할 수 없습니다" in prev.problem_box.cget("text") and "해시" not in prev.problem_box.cget("text")
    assert "disabled" in prev.next_btn.state() and prev.next_reason.cget("text") == T.G_PACKAGE_BLOCKED
    c.go(S.NEW_CONFIRM)
    cf = app.views[S.NEW_CONFIRM]
    cf.checked_var.set(True)
    cf._checked_changed()
    assert "disabled" in cf.run_btn.state() and cf.why.cget("text") == T.G_PACKAGE_BLOCKED


# --------------------------------------------------------------------- 대용량 복사

def test_transfer_view_reasons_gating_and_result_summary(ctx, tmp_path):
    app, c, api = ctx.app, ctx.c, ctx.api
    v = app.views[S.TRANSFER]
    c.go(S.TRANSFER)
    assert v.dry_reason.cget("text") == T.WHY_PATH_PROBLEM and "disabled" in v.dry_btn.state()
    src = tmp_path / "src"
    src.mkdir()
    v.src_var.set(str(src))
    v.dst_var.set(str(src / "inside"))
    app.update()
    assert T.ICON_FAIL in v.check_label.cget("text") and T.G_DEST_INSIDE in v.check_label.cget("text")
    v.dst_var.set(str(tmp_path / "dst"))
    app.update()
    assert T.ICON_OK in v.check_label.cget("text") and T.G_SOURCE_OK in v.check_label.cget("text")
    assert "disabled" not in v.dry_btn.state() and "disabled" in v.go_btn.state()
    assert v.go_reason.cget("text") == T.WHY_PREVIEW_FIRST  # 버튼 옆에 이유
    assert v.cloud_var.get() is False and v.strict_var.get() is False
    v._dry()
    app.update()
    assert [x for x in api.calls if x[0] == "transfer"] == [("transfer", True, False, False)]
    assert "disabled" not in v.go_btn.state() and v.go_reason.cget("text") == ""
    assert v.preview_box.cget("text").startswith("미리보기 결과(실제로 바꾸지 않음)")
    v.dst_var.set(str(tmp_path / "other"))  # 대상을 바꾸면 다시 미리보기 필요
    app.update()
    assert "disabled" in v.go_btn.state() and v.go_reason.cget("text") == T.WHY_PREVIEW_FIRST
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
    assert v.result.ok_label.cget("text") == f"{T.ICON_OK} {T.RESULT_OK} 1" and v.result.box.cget("bg") != "#fde2e2"


def test_transfer_failure_shows_red_box_and_translated_reasons(ctx, tmp_path):
    app, c, api = ctx.app, ctx.c, ctx.api
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    c.state.transfer_preview_key = (c.state.transfer_src, c.state.transfer_dst)
    rep = Report(dry_run=False, results=[FileResult("s\\a.dwg", "d", "mismatch", error="SHA-256 불일치"),
                                         FileResult("s\\b", "d", "verified")])
    api.run_transfer = lambda *a, **k: rep
    c.go(S.TRANSFER)
    c.start_transfer(dry_run=False, write_confirmed=True)
    app.update()
    v = app.views[S.TRANSFER]
    assert v.result.fail_label.cget("text").startswith(T.ICON_FAIL) and v.result.box.cget("text") == T.TR_FAIL_HINT
    row = children(v.tree)[0]
    assert T.TR_STATUS_LABELS["mismatch"] in row["values"][0] and "SHA" not in row["values"][2]


# --------------------------------------------------------------------- 오류 / 진행 표시

def test_error_dialog_shows_what_how_and_hides_traceback_until_expanded(ctx):
    app = ctx.app
    err = friendly_error(PermissionError(13, "denied", "C:\\x"), "Traceback (most recent call last):\n  boom")
    app.on_event("error", err)
    app.update()
    dlg = [w for w in app.winfo_children() if isinstance(w, ErrorDialog)][0]
    assert dlg.title() == T.ERR_TITLES["os"]
    assert dlg.what_label.cget("text") == T.ERR_PERMISSION[0].format(path=": C:\\x")
    assert dlg.how_label.cget("text") == T.ERR_PERMISSION[1] and "Traceback" not in dlg.what_label.cget("text")
    assert not dlg.shown and not dlg.detail.winfo_ismapped()
    dlg._toggle()
    dlg.update()
    assert dlg.shown and "Traceback" in dlg.detail.get("1.0", "end") and dlg.toggle.cget("text") == T.BTN_DETAIL_HIDE
    dlg.destroy()


def test_busy_status_card_shows_stage_message_note_and_cancel(ctx):
    app, c = ctx.app, ctx.c
    assert app.stage.cget("text") == T.READY
    c.can_cancel = True
    app.on_event("busy", T.TASK_TRANSFER)
    app.on_event("progress", T.PROGRESS[T.TASK_TRANSFER])
    app.update()
    assert app.stage.cget("text") == f"{T.STAGE_LABEL}: {T.TASK_TRANSFER}"  # 지금 하는 일을 크게
    assert app.status.cget("text") == T.PROGRESS[T.TASK_TRANSFER] and app.note.cget("text") == T.BUSY_NOTE
    assert app.cancel_btn.winfo_manager() == "pack"
    app.on_event("progress", "복사본 검증 중 25/100")
    assert app.status.cget("text") == "복사본 검증 중 25/100"
    app.on_event("busy", None)
    assert app.cancel_btn.winfo_manager() == "" and app.stage.cget("text") == T.READY and app.note.cget("text") == ""
    app.on_event("cancelled", T.TASK_DISCOVER)
    assert app.status.cget("text") == T.CANCELLED.format(task=T.TASK_DISCOVER)
    c.state.busy = True
    c.go(S.TRANSFER)
    app.go_home()
    assert ctx.shown[-1][0] == "info" and c.state.screen == S.TRANSFER  # 작업 중에는 처음으로 돌아가지 않음
    c.state.busy = False
    app.go_home()
    assert c.state.screen == S.START


def test_scan_start_updates_status_card_through_controller(ctx):
    ctx.c.go(S.OLD_SCAN)
    seen = []
    orig = ctx.app.on_event

    def spy(kind, payload):
        seen.append((kind, payload))
        orig(kind, payload)
    ctx.c.listener = spy
    ctx.app.views[S.OLD_SCAN].btn.invoke()
    ctx.app.update()
    assert ("busy", T.TASK_SCAN) in seen and ("progress", T.PROGRESS[T.TASK_SCAN]) in seen
    assert ctx.app.stage.cget("text") == T.READY  # 동기 실행이라 끝난 뒤 상태


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
