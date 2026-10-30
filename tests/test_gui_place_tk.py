"""받은 파일 원래 위치에 놓기(place) 화면의 tkinter 스모크 시험(창을 숨긴 채, stage50). 가짜 api 만 쓰고 임시 폴더만 만든다.
기존 화면 3곳의 '들어가는 길' 회귀(받기가 정상일 때만 보임, 기존 문구 불변)도 여기서 본다. tkinter/디스플레이가 없으면 건너뛴다."""
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import filedialog, messagebox  # noqa: E402

from pcmigrate.gui import state as S  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402
from pcmigrate.gui.app import App  # noqa: E402
from pcmigrate.gui.controller import Controller  # noqa: E402
from pcmigrate.gui.runner import Runner  # noqa: E402

from test_gui_controller import FakeApi  # noqa: E402
from test_gui_my_files_controller import FakeData, FakeLan  # noqa: E402
from test_gui_place_controller import FakePlaceApi, make_report, put  # noqa: E402
from test_gui_collapse_empty_tk import occupies  # noqa: E402


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    answers = {"askyesno": True}
    shown, asked = [], []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: (asked.append(a), answers["askyesno"])[1])
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: shown.append(("warn", a)))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(("info", a)))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(("error", a)))
    api = FakePlaceApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = FakeData(), FakeLan(tmp_path)
    c.default_desktop = lambda: str(tmp_path / "Desktop")
    c.drive_lister = lambda: []
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
    app.withdraw()
    app.update()
    recv = tmp_path / "recv"
    put(recv / "D" / "프로젝트" / "a.dwg")
    yield SimpleNamespace(app=app, c=c, api=api, answers=answers, shown=shown, asked=asked, tmp=tmp_path, recv=recv)
    app.destroy()


def shown(ctx, w):
    """숨긴(withdraw) 창에서는 winfo_ismapped 가 거짓이므로, 자기와 위쪽 부모가 모두 배치돼 있고 높이가 있는가로 본다."""
    return occupies(w, ctx.app)


def go(ctx, screen):
    ctx.c.go(screen)
    ctx.app.update()
    return ctx.app.views[screen]


def labels_of(widget):
    out = []
    for w in widget.winfo_children():
        try:
            out.append(str(w.cget("text")))
        except tk.TclError:
            pass
        out.extend(labels_of(w))
    return out


def run_preview(ctx, v):
    v.recv_var.set(str(ctx.recv))
    v.rep_var.set(str(ctx.tmp / "out" / "r.json"))
    ctx.app.update()
    v.preview_btn.invoke()
    ctx.app.update()


# =========================================================================== 화면 만들기 / 처음 상태
def test_place_screen_builds_without_overall_bar_and_blocks_go_at_start(ctx):
    app = ctx.app
    app.geometry(f"{app.min_size[0]}x{app.min_size[1]}")
    v = go(ctx, S.PLACE)
    assert isinstance(v.title_label, tk.Widget) and v.title_label.cget("text") == T.PL_HEADING == T.TITLES[S.PLACE]
    assert app.crumb.cget("text") == T.TITLES[S.PLACE]
    assert not app.overall.winfo_manager()  # '전체 단계 n/4' 는 표시하지 않는다
    assert v.steps_strip.names == T.PL_STEP_NAMES and v.steps_strip.current == 1
    assert "disabled" in v.go_btn.state() and "disabled" in v.preview_btn.state()
    assert v.preview_reason.cget("text") == T.WHY_PL_RECV and v.go_reason.cget("text") == T.WHY_PL_RECV
    assert v.empty_note.cget("text") == T.PL_RESULT_EMPTY
    assert not v.found_row.winfo_manager()  # 찾은 것이 없으면 그 줄을 숨긴다
    assert v.go_btn.kind == "warn" and v.preview_btn.master is v.bar_right and v.go_btn.master is v.bar_right
    # 입력칸은 스크롤 영역의 맨 위쪽에, 주 버튼은 스크롤 밖 하단 고정 막대에 있다(숨긴 창에서는 실제 화면 좌표 대신 배치 관계로 본다)
    assert shown(ctx, v.recv_entry) and 0 <= v.recv_entry.winfo_rooty() - v.scroll.canvas.winfo_rooty() < 160
    assert shown(ctx, v.preview_btn) and shown(ctx, v.go_btn) and v.bar_right.master.master is v._nav
    # 고급 옵션은 접혀 있다가 펼쳐진다
    assert v.opts.open is False and v.opts_toggle.cget("text") == T.PL_OPTIONS_TOGGLE and not v.opts.body.winfo_manager()
    v.opts.toggle()
    app.update()
    assert v.opts.body.winfo_manager() == "pack" and v.opts_toggle.cget("text") == T.PL_OPTIONS_TOGGLE_OPEN
    assert not shown(ctx, v.gather_frame)  # 모으기를 켜기 전에는 숨김
    assert v.keep_var.get() is False  # '복사만 하기' 기본 꺼짐
    dlg = app.show_help(S.PLACE)
    assert dlg.title() == T.HELP_TITLE.format(screen=T.TITLES[S.PLACE]) and T.PL_HELP.split("\n")[0] in dlg.text.get("1.0", "end")
    dlg.destroy()
    assert v.roadmap_btn.cget("text") == T.BTN_ROADMAP


def test_empty_areas_take_no_space_before_a_result(ctx):
    v = go(ctx, S.PLACE)
    for w in (v.summary, v.stopped_box, v.delete_box, v.ack_check, v.notes_label, v.save_label, v.head_label, v.todo_fold,
              *v.folds.values(), v.link_row, v.row_hint):
        assert not occupies(w, ctx.app), w


def test_found_received_folders_combobox_fills_entry(ctx, monkeypatch):
    monkeypatch.setattr(Controller, "scan_packages_on_show", True)
    ctx.c.received_dirs = lambda: [str(ctx.recv)]
    v = go(ctx, S.PLACE)
    assert v.found_row.winfo_manager() and len(v.found_box.cget("values")) == 1
    v.found_box.current(0)
    v.found_box.event_generate("<<ComboboxSelected>>")
    ctx.app.update()
    assert v.recv_var.get() == str(ctx.recv) and ctx.c.place_state.recv == str(ctx.recv)
    assert "✔" in v.check_label.cget("text") or T.ICON_OK in v.check_label.cget("text")


# =========================================================================== 미리보기 -> 놓기
def test_preview_enables_go_and_changing_options_invalidates(ctx):
    v = go(ctx, S.PLACE)
    ctx.api.place_result = lambda recv, kw: make_report(recv, dry_run=not kw["apply"], keep=kw["keep"], delete=0 if kw["keep"] else 4,
                                                        delete_bytes=4096, total=4, total_bytes=4096)
    run_preview(ctx, v)
    assert v.head_label.cget("text") == T.PL_PREVIEW_HEAD and "4" in v.summary.cget("text")
    assert shown(ctx, v.delete_box) and "4개" in v.delete_big.cget("text") and "4.0KB" in v.delete_big.cget("text")
    assert T.PL_DELETE_TITLE == v.delete_title.cget("text")
    assert shown(ctx, v.ack_check) and "disabled" in v.go_btn.state() and v.go_reason.cget("text") == T.WHY_PL_ACK
    assert v.steps_strip.current == 3
    v.ack_check.invoke()  # 확인 체크
    ctx.app.update()
    assert "disabled" not in v.go_btn.state() and v.go_reason.cget("text") == ""
    # 옵션을 바꾸면 미리보기는 무효: '놓기'는 다시 잠기고 이유가 나온다
    v.over_var.set(True)
    ctx.app.update()
    assert "disabled" in v.go_btn.state() and v.go_reason.cget("text") == T.WHY_PL_OPTIONS and not shown(ctx, v.ack_check)
    v.over_var.set(False)
    run_preview(ctx, v)
    v.ack_check.invoke()
    ctx.app.update()
    assert "disabled" not in v.go_btn.state()
    # '복사만 하기' 를 켜면 미리보기 무효 + 다시 하면 삭제 예정 상자와 확인 체크가 사라진다
    v.keep_var.set(True)
    ctx.app.update()
    assert v.go_reason.cget("text") == T.WHY_PL_OPTIONS
    run_preview(ctx, v)
    assert not shown(ctx, v.delete_box) and not shown(ctx, v.ack_check) and "disabled" not in v.go_btn.state()


def test_go_asks_confirmation_with_counts_and_no_leaves_nothing_started(ctx):
    v = go(ctx, S.PLACE)
    ctx.api.place_result = lambda recv, kw: make_report(recv, dry_run=not kw["apply"], keep=kw["keep"], delete=7, delete_bytes=7 * 1024 ** 2,
                                                        total=7, total_bytes=7 * 1024 ** 2)
    run_preview(ctx, v)
    v.ack_check.invoke()
    ctx.app.update()
    ctx.answers["askyesno"] = False
    v.go_btn.invoke()
    ctx.app.update()
    assert len(ctx.api.place_calls) == 1  # 아니요: 아무 일도 없다
    assert ctx.c.place_state.ack is False and v.ack_var.get() is False and "disabled" in v.go_btn.state()
    title, msg = ctx.asked[-1][0], ctx.asked[-1][1]
    assert title == T.PL_CONFIRM_TITLE and "7개 / 7.0MB" in msg and "되돌릴 수 없습니다" in msg and str(ctx.tmp / "out" / "r.json") in msg
    v.ack_check.invoke()
    ctx.answers["askyesno"] = True
    ctx.app.update()
    v.go_btn.invoke()
    ctx.app.update()
    assert len(ctx.api.place_calls) == 2 and ctx.api.place_calls[1][1]["apply"] is True
    assert v.head_label.cget("text") == T.PL_DONE_HEAD and v.steps_strip.current == 4
    assert str(ctx.tmp / "out" / "r.json") in v.save_label.cget("text") and "disabled" not in v.open_report_btn.state()
    assert "disabled" in v.go_btn.state() and v.go_reason.cget("text") == T.WHY_PL_PREVIEW_FIRST


def test_result_tables_todo_and_row_explorer(ctx):
    v = go(ctx, S.PLACE)
    skipped = po_rows(ctx)
    rep = make_report(ctx.recv, total=5, total_bytes=500, skipped=skipped, dest_newer_count=1,
                      overwrites=[{"expected": "D:\\프로젝트\\a.dwg", "dest": "D:\\프로젝트\\a.dwg", "current": "x", "recv_size": 10, "dest_size": 20,
                                   "recv_mtime": "2026-01-01T10:00:00", "dest_mtime": "2026-01-03T10:00:00", "dest_newer": True,
                                   "backup": "D:\\프로젝트\\a.dwg.bak"}])
    ctx.api.place_result = lambda recv, kw: rep
    run_preview(ctx, v)
    assert shown(ctx, v.folds["manual"]) and v.folds["manual"].toggle_btn.cget("text") == T.PL_FOLD_MANUAL.format(n=2)
    assert v.folds["overwrite"].toggle_btn.cget("text") == T.PL_FOLD_OVERWRITE.format(n=1) and not shown(ctx, v.folds["gathered"])
    assert len(v.trees["manual"].get_children()) == 2 and len(v.trees["overwrite"].get_children()) == 1
    assert T.PL_DEST_NEWER.format(n=1) in v.notes_label.cget("text")
    assert shown(ctx, v.todo_fold) and "--" not in v.todo_label.cget("text")
    v.todo_fold.toggle()
    v.folds["manual"].toggle()
    ctx.app.update()
    first = v.trees["manual"].get_children()[0]
    row = v.trees["manual"].item(first, "values")
    assert "건너뜀" in row[0] and "--" not in " ".join(row)
    # 줄을 고르면 탐색기 보기 버튼이 켜지고(가짜 탐색기), 열 수 없는 경로면 이유가 나온다
    opened = []
    ctx.c.explorer_fn = lambda path, select: (opened.append((path, select)), True)[1]
    ctx.c.can_show_in_explorer = lambda path, select=None: True
    v.trees["manual"].selection_set(first)
    ctx.app.update()
    assert "disabled" not in v.explorer.button.state()
    v.explorer.button.invoke()
    assert opened and opened[0][0].endswith("a.dwg")  # 현재 위치(받은 폴더 안)를 보여 준다
    assert v.row_hint.cget("text") == T.PL_ROW_HINT


def po_rows(ctx):
    from pcmigrate import place_original as po
    return [po.Row("file", "C:\\Windows\\x.txt", str(ctx.recv / "D" / "프로젝트" / "a.dwg"), po.PROTECTED, "", 0, 0),
            po.Row("folder", "D:\\큰파일", str(ctx.recv / "D"), po.SPLIT_PARTS, "", 3, 1000)]


def test_cancel_and_error_show_partial_result_and_stop_note(ctx):
    from pcmigrate.cancel import Cancelled
    v = go(ctx, S.PLACE)
    ctx.api.place_result = lambda recv, kw: make_report(recv, dry_run=not kw["apply"], delete=0, total=3)
    run_preview(ctx, v)
    partial = make_report(ctx.recv, dry_run=False, total=9, meta={"interrupted": "cancelled"})
    ctx.api.place_error, ctx.api.place_error_report = Cancelled(), partial
    v.go_btn.invoke()
    ctx.app.update()
    assert T.PL_WHY["cancelled"] in v.head_label.cget("text") and v.summary.kind == "danger"
    assert ctx.app._after_note_text == T.STOP_DONE_PLACE and str(ctx.tmp / "out" / "r.json") in v.save_label.cget("text")
    assert "disabled" in v.go_btn.state()  # 다시 하려면 미리보기부터


def test_gather_option_shows_folder_row_and_onedrive_notice(ctx):
    ctx.c.place_desktop_fn = lambda: str(ctx.tmp / "OneDrive" / "Desktop")
    ctx.c.place_env = {"OneDrive": str(ctx.tmp / "OneDrive")}
    ctx.c.place_protected_fn = lambda path, is_dir=True: None
    v = go(ctx, S.PLACE)
    v.recv_var.set(str(ctx.recv))
    v.opts.toggle()
    v.gather_var_on.set(True)
    ctx.app.update()
    assert shown(ctx, v.gather_frame)
    assert T.PL_GATHER_ONEDRIVE in v.warn_label.cget("text") and shown(ctx, v.warn_label)
    assert shown(ctx, v.gather_other_btn) and v.gather_other_btn.cget("text") == T.BTN_PL_GATHER_OTHER
    assert "빈" in v.gather_field.hint.cget("text") or "비워" in v.gather_field.hint.cget("text")
    v.gather_dir_var.set(str(ctx.tmp / "모은곳"))  # 직접 고르면 OneDrive 밖이라 안내가 사라진다
    ctx.app.update()
    assert not shown(ctx, v.warn_label) and not shown(ctx, v.gather_other_btn)
    v.gather_var_on.set(False)
    ctx.app.update()
    assert not shown(ctx, v.gather_frame)


def test_map_rows_feed_the_controller(ctx):
    v = go(ctx, S.PLACE)
    v.recv_var.set(str(ctx.recv))
    v.map_rows[0]["src"].set("D")
    v.map_rows[0]["dst"].set("E")
    v._add_map_row()  # '+ 줄 추가' 는 빈 줄을 더한다(빈 줄은 무시)
    v.map_rows[1]["src"].set("E")
    v.map_rows[1]["dst"].set("F")
    ctx.app.update()
    assert ctx.c.place_state.maps == [("D", "E"), ("E", "F")]
    v._del_map_row(v.map_rows[1])
    assert ctx.c.place_state.maps == [("D", "E")]
    v._del_map_row(v.map_rows[0])  # 마지막 줄은 지우지 않고 비운다
    assert len(v.map_rows) == 1 and ctx.c.place_state.maps == [("", "")]


# =========================================================================== 들어가는 길 3곳(회귀 포함)
def _rx_done(ctx, ok=True):
    f = ctx.c.files
    f.rx_state = "done"
    f.dest = str(ctx.recv)  # 받은 폴더(받기 저장 위치)
    f.rx_result = {"files": 3, "ok": 3 if ok else 1, "mismatch": 0 if ok else 2, "failed": 0, "sender_failed": 0, "dest": str(ctx.recv)}
    ctx.c.emit("lan_rx")
    ctx.app.update()


def test_receive_result_shows_enter_button_only_when_ok(ctx):
    r = go(ctx, S.LAN_RECV)
    assert not r.place_btn.winfo_manager() and not r.place_note.winfo_manager()  # 받기 전에는 없다
    _rx_done(ctx, ok=False)
    assert r.result.winfo_manager() and not r.place_btn.winfo_manager()  # 확인이 필요하면 숨긴다
    _rx_done(ctx, ok=True)
    assert r.place_btn.winfo_manager() and r.place_btn.cget("text") == T.BTN_PL_ENTER
    assert r.place_note.cget("text") == T.PL_ENTER_NOTE
    # 기존 화면의 문구는 그대로
    assert T.RX_RESULT_OK in r.result.cget("text") and r.result_note.cget("text") == T.RX_DONE_NOTE and r.open_btn.cget("text") == T.RX_OPEN_DEST
    r.place_btn.invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.PLACE and ctx.c.place_state.recv == str(ctx.recv) and ctx.app.views[S.PLACE].recv_var.get() == str(ctx.recv)
    ctx.app.views[S.PLACE].back_btn.invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.LAN_RECV


def test_method_screen_link_and_unchanged_texts(ctx):
    v = go(ctx, S.METHOD)
    links = [w for w in _walk(v) if isinstance(w, tk.Button) and str(w.cget("text")) == T.BTN_PL_ENTER_LINK]
    assert len(links) == 1 and links[0].kind == "link"
    assert [k for k, _t, _d in T.MV_OPTIONS] and v.next_btn.cget("text") == T.BTN_NEXT
    links[0].invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.PLACE and ctx.c.place_back_screen() == S.METHOD
    ctx.app.views[S.PLACE].back_btn.invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.METHOD


def test_new_pc_result_next_box_has_enter_line_and_keeps_old_items(ctx):
    v = go(ctx, S.NEW_RESULT)
    assert [cb.cget("text") for cb in v.box_checks] == [t for _k, t in T.RES_NEXT_ITEMS]  # 기존 체크 항목 불변
    assert v.place_note.cget("text") == T.PL_ENTER_NOTE and v.place_btn.cget("text") == T.BTN_PL_ENTER
    assert v.place_btn.master is v.next_box and v.place_note.master is v.next_box
    v.place_btn.invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.PLACE and ctx.c.place_back_screen() == S.NEW_RESULT


def _walk(w):
    for ch in w.winfo_children():
        yield ch
        yield from _walk(ch)


def test_enter_buttons_are_blocked_while_busy(ctx):
    r = go(ctx, S.LAN_RECV)
    _rx_done(ctx, ok=True)
    ctx.c.state.busy = True
    r.place_btn.invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.LAN_RECV and ctx.shown and ctx.shown[-1][0] == "warn"
    ctx.c.state.busy = False


def test_roadmap_has_new_place_item(ctx):
    dlg = ctx.app.show_roadmap()
    ctx.app.update()
    assert "new3b" in dlg.vars and len(dlg.vars) == 13
    assert dlg.progress_label.cget("text") == T.ROADMAP_PROGRESS.format(n=0, total=13)
    assert T.ROADMAP_NEW[3][0] == "new3b" and any("받은 파일을 원래 위치로 놓습니다" in t for t in labels_of(dlg))
    dlg.destroy()
