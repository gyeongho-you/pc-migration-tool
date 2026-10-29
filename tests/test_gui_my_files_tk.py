"""stage30a: 내 파일 옮기기 화면들의 tkinter 시험(창을 숨긴 채). 가짜 data_api / lan_api 만 쓴다(실제 스캔·전송·네트워크 방송 없음).
tkinter/디스플레이가 없으면 건너뛴다."""
import os
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
from pcmigrate.lan_protocol import LanError  # noqa: E402
from pcmigrate.progress import ProgressInfo  # noqa: E402

from test_gui_controller import FakeApi  # noqa: E402
from test_gui_my_files_controller import FakeData, FakeLan, folder  # noqa: E402


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    answers = {"askyesno": True, "askokcancel": True}
    shown = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: answers["askyesno"])
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: answers["askokcancel"])
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: shown.append(("warn", a)))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(("info", a)))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(("error", a)))
    data, lan = FakeData(), FakeLan(tmp_path)
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = data, lan
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
    yield SimpleNamespace(app=app, c=c, data=data, lan=lan, answers=answers, shown=shown, tmp=tmp_path)
    app.destroy()


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


def find_and_pick(ctx, n=1):
    """찾기를 실행하고 앞의 n 개 폴더를 체크한다(표 클릭과 같은 경로)."""
    v = go(ctx, S.FIND)
    v.find_btn.invoke()
    ctx.app.update()
    iids = v.tree.get_children()
    for iid in iids[:n]:
        v._toggle(iid)
    ctx.app.update()
    return v


# =========================================================================== 시작 화면 / 단계 표시줄
def test_start_card_two_opens_find_screen(ctx):  # stage34: ② 카드
    v = ctx.app.views[S.START]
    assert len(v.cards) == 3
    texts = " ".join(labels_of(v.cards[1]))
    assert "② 내 파일 옮기기" in texts and "자동으로 찾아서" in texts and "대용량" not in texts
    v.buttons[1].invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.FIND and ctx.app.current == S.FIND
    assert ctx.app.crumb.cget("text") == T.TITLES[S.FIND]
    assert ctx.app.overall_label.cget("text") == T.OVERALL_FMT.format(n=1, total=4)
    assert [ch.cget("text") for ch in ctx.app.overall_chips][:4] == ["1 찾기", "2 방법", "3 연결·복사", "4 결과"]
    assert ctx.app.overall_flow.cget("text") == T.OVERALL_FLOWS["files"]


def test_all_new_screens_have_help_titles_and_alt_keys(ctx):
    for scr in (S.FIND, S.METHOD, S.LAN_RECV, S.LAN_SEND):
        v = go(ctx, scr)
        assert ctx.app.crumb.cget("text") == T.TITLES[scr]
        dlg = ctx.app.show_help(scr)
        assert dlg.title() == T.HELP_TITLE.format(screen=T.TITLES[scr])
        dlg.destroy()
        assert v.home_btn is not None and v.back_btn is not None
        assert v.title_label.cget("text") == T.TITLES[scr]


# =========================================================================== 1/4 찾기
def test_find_screen_empty_state_then_search_fills_table_without_selecting(ctx):
    v = go(ctx, S.FIND)
    assert v.find_btn.cget("text") == T.BTN_FD_FIND and T.FD_EMPTY in labels_of(v)
    assert v.total.cget("text") == T.FD_TOTAL_NONE and "disabled" in v.next_btn.state()
    assert not v.junk_var.get()  # 정크 제외는 기본 꺼짐
    v.find_btn.invoke()
    ctx.app.update()
    assert ctx.data.calls and ctx.c.status.task == T.TASK_FIND_DATA
    rows = [v.tree.item(i)["values"] for i in v.tree.get_children()]
    assert len(rows) == 3 and all(str(r[0]) == T.UNCHECKED for r in rows)  # 자동 선택 없음
    assert v.find_btn.cget("text") == T.BTN_FD_AGAIN
    assert [str(x) for x in rows[0][2:6]] == ["5", "1", "0", "0"] and str(rows[0][6]).endswith("GB") and str(rows[0][7]) == "2025-03-04"
    assert T.FD_SUMMARY.format(n=3, files="12,345", sec="7") == v.summary.cget("text")


def test_row_click_selects_and_total_and_next_button_follow(ctx):
    v = find_and_pick(ctx, 2)
    marks = [str(v.tree.item(i)["values"][0]) for i in v.tree.get_children()]
    assert marks == [T.CHECKED, T.CHECKED, T.UNCHECKED]
    assert v.total.cget("text") == T.FD_TOTAL.format(n=2, size="7.0GB")
    assert "disabled" not in v.next_btn.state() and v.next_reason.cget("text") == ""
    v.tree.selection_set(v.tree.get_children()[0])
    ctx.app.update()
    assert "근거" in v.detail.cget("text")  # 줄을 고르면 근거 한 줄
    v._toggle(v.tree.get_children()[0])
    v._toggle(v.tree.get_children()[1])
    ctx.app.update()
    assert "disabled" in v.next_btn.state() and v.next_reason.cget("text") == T.WHY_FD_NEED_SELECTION


def test_bulk_select_and_clear_buttons(ctx):
    v = go(ctx, S.FIND)
    v.find_btn.invoke()
    ctx.app.update()
    v.select_all_btn.invoke()
    assert len(ctx.c.data_selection()) == 3 and "3" in v.bulk_msg.cget("text")
    v.clear_all_btn.invoke()
    assert ctx.c.data_selection() == []


def test_filter_checkbutton_changes_visible_rows(ctx):
    v = go(ctx, S.FIND)
    v.find_btn.invoke()
    ctx.app.update()
    assert len(v.tree.get_children()) == 3
    ctx.c.set_data_filter("design", False)
    ctx.c.set_data_filter("photo", False)
    ctx.c.set_data_filter("video", False)
    ctx.app.update()
    assert len(v.tree.get_children()) == 1  # 문서 폴더만
    assert v.filter_vars["doc"].get() is True and v.filter_vars["photo"].get() is False


def test_junk_checkbox_and_excluded_and_cloud_sections(ctx):
    v = go(ctx, S.FIND)
    v.find_btn.invoke()
    ctx.app.update()
    v.junk_cb.invoke()
    assert ctx.c.files.junk is True
    assert v.excluded.winfo_manager() and "6곳" in v.excluded.toggle_btn.cget("text")
    assert not v.excluded.open
    v.excluded.toggle()
    ctx.app.update()
    body = " ".join(labels_of(v.excluded.body))
    assert "시스템: 4곳" in body and "재생성: 2곳" in body
    assert v.cloud.winfo_manager() and "3개" in v.cloud.toggle_btn.cget("text")
    v.cloud.toggle()
    ctx.app.update()
    assert "내려받지 않았습니다" in " ".join(labels_of(v.cloud.body))
    # 펼친 채로 다시 그려도(선택 바꾸기 등) 오류가 나지 않는다(글줄 위젯을 지우지 않고 재사용)
    v._toggle(v.tree.get_children()[0])
    ctx.app.update()
    ctx.c.find_data_start()
    ctx.app.update()
    assert ctx.c.last_error is None and "시스템: 4곳" in " ".join(labels_of(v.excluded.body))


def test_add_folder_button_uses_dialog_and_selects(ctx, monkeypatch):
    mine = ctx.tmp / "추가할폴더"
    mine.mkdir()
    monkeypatch.setattr(filedialog, "askdirectory", lambda **k: str(mine).replace("\\", "/"))
    v = go(ctx, S.FIND)
    v.add_btn.invoke()
    ctx.app.update()
    assert ctx.c.data_selection() == [str(mine)]
    assert str(v.tree.item(v.tree.get_children()[0])["values"][0]) == T.CHECKED and v.total.cget("text").startswith("선택한 1개")
    monkeypatch.setattr(filedialog, "askdirectory", lambda **k: str(ctx.tmp / "없음"))
    v.add_btn.invoke()
    assert ctx.shown and ctx.shown[-1][0] == "warn"


def test_partial_and_empty_results_show_plain_notices(ctx):
    from test_gui_my_files_controller import find_result
    ctx.data.result = find_result(complete=False, errors=3)
    v = go(ctx, S.FIND)
    v.find_btn.invoke()
    ctx.app.update()
    text = v.notice.cget("text")
    assert T.FD_PARTIAL in text and T.FD_READ_ERRORS.format(n=3) in text and v.notice.winfo_manager()
    from pcmigrate import find_data as fd
    ctx.data.result = fd.FindResult()
    v.find_btn.invoke()
    ctx.app.update()
    assert v.empty.cget("text") == T.FD_NONE_FOUND and not v.notice.winfo_manager()


def test_find_cancel_message(ctx):
    from pcmigrate.cancel import Cancelled

    def cancel_now(token):
        ctx.c.cancel()
        raise Cancelled()
    ctx.data.raise_cancel = cancel_now
    v = go(ctx, S.FIND)
    v.find_btn.invoke()
    ctx.app.update()
    assert T.FD_CANCELLED in v.notice.cget("text")


def test_legacy_and_receive_links_on_find_screen(ctx):
    v = go(ctx, S.FIND)
    v.legacy_btn.invoke()
    ctx.app.update()
    assert ctx.app.current == S.TRANSFER and not ctx.c.has_selection() and ctx.app.views[S.TRANSFER].src_field.winfo_manager()
    v = go(ctx, S.FIND)
    v.receive_btn.invoke()
    ctx.app.update()
    assert ctx.app.current == S.LAN_RECV


# =========================================================================== 2/4 방법
def test_method_screen_radio_boxes_and_role(ctx):
    find_and_pick(ctx)
    ctx.app.views[S.FIND].next_btn.invoke()
    ctx.app.update()
    m = ctx.app.views[S.METHOD]
    assert ctx.app.current == S.METHOD and ctx.app.overall_label.cget("text") == T.OVERALL_FMT.format(n=2, total=4)
    assert list(m.boxes) == [T.MV_DIRECT, T.MV_EXTERNAL, T.MV_SHARE]
    assert "(추천)" in m.radios[T.MV_DIRECT].cget("text") and "고급" in m.radios[T.MV_SHARE].cget("text")
    assert "disabled" in m.next_btn.state() and m.next_reason.cget("text") == T.WHY_MV_NEED_METHOD
    assert not m.role_frame.winfo_manager()
    m.radios[T.MV_DIRECT].invoke()
    ctx.app.update()
    assert m.role_frame.winfo_manager() and m.next_reason.cget("text") == T.WHY_MV_NEED_ROLE
    m.role_radios[T.MV_ROLE_SEND].invoke()
    ctx.app.update()
    assert "disabled" not in m.next_btn.state()
    m.radios[T.MV_EXTERNAL].invoke()
    ctx.app.update()
    assert not m.role_frame.winfo_manager() and "disabled" not in m.next_btn.state()
    m.back_btn.invoke()
    ctx.app.update()
    assert ctx.app.current == S.FIND


def test_method_external_enters_existing_copy_screen_with_the_list(ctx):
    real = ctx.tmp / "원본폴더"
    real.mkdir()
    v = go(ctx, S.FIND)
    v.find_btn.invoke()
    ctx.c.files.result.projects = [folder(str(real), design=3, total=4096)]
    ctx.c.files.selected = set()
    ctx.c.set_data_selected(str(real), True)
    ctx.c.go(S.METHOD)
    ctx.app.update()
    m = ctx.app.views[S.METHOD]
    m.radios[T.MV_EXTERNAL].invoke()
    m.next_btn.invoke()
    ctx.app.update()
    t = ctx.app.views[S.TRANSFER]
    assert ctx.app.current == S.TRANSFER and ctx.c.has_selection() and ctx.app.overall_label.cget("text") == T.OVERALL_FMT.format(n=3, total=4)
    assert t.sel_box.winfo_manager() and not t.src_field.winfo_manager()
    assert "1곳" in t.sel_box.cget("text") and "원본폴더" in t.sel_box.cget("text")
    t.dst_var.set(str(ctx.tmp / "외장"))
    ctx.app.update()
    assert "disabled" not in t.dry_btn.state() and "disabled" in t.go_btn.state()
    t.dry_btn.invoke()
    ctx.app.update()
    assert ctx.data.calls[-1][0] == "transfer" and ctx.data.calls[-1][3] is True
    assert "disabled" not in t.go_btn.state()
    t.go_btn.invoke()  # 확인 창(가짜 '예') 뒤에 복사
    ctx.app.update()
    assert ctx.data.calls[-1][3] is False and ctx.app.overall_label.cget("text") == T.OVERALL_FMT.format(n=4, total=4)
    t.back_btn.invoke()
    ctx.app.update()
    assert ctx.app.current == S.METHOD
    # 확인 창에서 '취소' 하면 복사하지 않는다
    ctx.c.enter_selection_transfer(T.MODE_EXTERNAL)
    ctx.app.update()


def test_copy_confirmation_can_be_declined_in_selection_flow(ctx):
    real = ctx.tmp / "원본폴더"
    real.mkdir()
    v = go(ctx, S.FIND)
    v.find_btn.invoke()
    ctx.c.files.result.projects = [folder(str(real), design=3, total=4096)]
    ctx.c.set_data_selected(str(real), True)
    ctx.c.enter_selection_transfer(T.MODE_NETWORK)
    ctx.app.update()
    t = ctx.app.views[S.TRANSFER]
    t.dst_var.set(str(ctx.tmp / "공유"))
    t.dry_btn.invoke()
    ctx.app.update()
    n = len(ctx.data.calls)
    ctx.answers["askokcancel"] = False
    t.go_btn.invoke()
    assert len(ctx.data.calls) == n


def test_legacy_transfer_screen_unchanged(ctx):
    t = go(ctx, S.TRANSFER)
    assert t.src_field.winfo_manager() and not t.sel_box.winfo_manager()
    src, dst = ctx.tmp / "s", ctx.tmp / "d"
    src.mkdir()
    t.src_var.set(str(src))
    t.dst_var.set(str(dst))
    ctx.app.update()
    t.dry_btn.invoke()
    ctx.app.update()
    assert ctx.c.api.calls[-1][0] == "transfer" and not ctx.data.calls
    assert ctx.app.overall_label.cget("text") == T.OVERALL_FMT.format(n=1, total=2) and t.back_btn is not None
    t.back_btn.invoke()
    ctx.app.update()
    assert ctx.app.current == S.START


# =========================================================================== 3/4 받는 쪽
def test_receive_screen_shows_code_addresses_and_warnings(ctx):
    r = go(ctx, S.LAN_RECV)
    assert r.dest_var.get().endswith("받은파일") and r.code_label.cget("text") == T.RX_CODE_NONE
    assert T.RX_WARN_PLAIN in r.warn.cget("text") and r.start_btn.winfo_manager() and not r.stop_btn.winfo_manager()
    r.start_btn.invoke()
    ctx.app.update()
    assert r.code_label.cget("text") == "123 456" and "192.0.2.10:47830" in r.addr_label.cget("text")
    assert "사설 네트워크" in r.addr_label.cget("text") and T.RX_WARN_PUBLIC in r.warn.cget("text") and T.RX_WARN_FIREWALL in r.warn.cget("text")
    assert r.stop_btn.winfo_manager() and not r.start_btn.winfo_manager() and r.state_label.cget("text") == T.RX_STATE["waiting"]
    assert "disabled" in r.dest_btn.state()
    assert float(r.code_font.cget("size")) >= 30 or int(r.code_font.cget("size")) >= 30


def test_receive_progress_and_done_are_drawn_from_events(ctx):
    r = go(ctx, S.LAN_RECV)
    r.start_btn.invoke()
    ev = ctx.lan.rx.on_event
    ev({"type": "peer_connected"})
    ev({"type": "plan", "files": 10, "bytes": 2048})
    ev({"type": "progress", "info": ProgressInfo("copy", 1024, 2048, 5, 10, speed_bps=2048.0, eta_seconds=4.0)})
    r._poll()
    ctx.app.update()
    assert r.state_label.cget("text") == T.RX_STATE["receiving"]
    assert "1.0KB" in r.progress_label.cget("text") and "2.0KB" in r.progress_label.cget("text") and float(r.bar.cget("value")) == 50.0
    ev({"type": "done", "files": 10, "bytes": 2048, "ok": 10, "mismatch": 0, "failed": 0, "sender_failed": 0, "dest": "x"})
    r._poll()
    ctx.app.update()
    assert r.state_label.cget("text") == T.RX_STATE["done"] and T.RX_RESULT_OK in r.result.cget("text") and r.result.winfo_manager()
    assert ctx.app.overall_label.cget("text") == T.OVERALL_FMT.format(n=4, total=4)
    assert r.start_btn.winfo_manager()


def test_receive_stop_button_and_leave_confirmation(ctx):
    r = go(ctx, S.LAN_RECV)
    r.start_btn.invoke()
    ctx.answers["askyesno"] = False
    r.back_btn.invoke()  # 받는 중에 나가려 하면 확인
    ctx.app.update()
    assert ctx.app.current == S.LAN_RECV and ctx.c.lan_receive_active()
    ctx.answers["askyesno"] = True
    r.back_btn.invoke()
    ctx.app.update()
    assert ctx.lan.rx.stopped and ctx.app.current == S.FIND  # 고른 폴더가 없으니 처음 화면 흐름의 1단계로
    r = go(ctx, S.LAN_RECV)
    r.start_btn.invoke()
    r.stop_btn.invoke()
    ctx.app.update()
    assert r.state_label.cget("text") == T.RX_STATE["stopped"] and r.start_btn.winfo_manager()


def test_receive_start_error_is_plain_text(ctx):
    ctx.lan.start_error = LanError("english", "dest_not_writable")
    r = go(ctx, S.LAN_RECV)
    r.start_btn.invoke()
    ctx.app.update()
    assert T.LAN_ERRORS["dest_not_writable"] in r.error.cget("text") and r.error.winfo_manager()


def test_receive_change_dest_button(ctx, monkeypatch):
    r = go(ctx, S.LAN_RECV)
    monkeypatch.setattr(filedialog, "askdirectory", lambda **k: str(ctx.tmp / "새 위치").replace("\\", "/"))
    r.dest_btn.invoke()
    assert r.dest_var.get() == str(ctx.tmp / "새 위치")
    r.start_btn.invoke()
    assert ctx.lan.calls[0] == ("start_receiver", str(ctx.tmp / "새 위치"))


# =========================================================================== 3/4 보내는 쪽
def to_send(ctx):
    find_and_pick(ctx)
    ctx.c.set_method(T.MV_DIRECT)
    ctx.c.set_role(T.MV_ROLE_SEND)
    ctx.c.files_continue()
    ctx.app.update()
    return ctx.app.views[S.LAN_SEND]


def test_send_screen_find_connect_and_result(ctx):
    s = to_send(ctx)
    assert ctx.app.current == S.LAN_SEND and "1곳" in s.selection.cget("text")
    s.find_btn.invoke()
    ctx.app.update()
    assert list(s.found_box.cget("values")) == [T.TX_FOUND_ITEM.format(name="새PC", addr="192.0.2.20:47830")]
    assert s.target_var.get() == "192.0.2.20:47830" and s.found_msg.cget("text") == T.TX_FOUND_N.format(n=1)
    s.connect_btn.invoke()  # 코드가 없으면 안내만
    assert ctx.shown[-1][0] == "warn" and not any(x[0] == "send" for x in ctx.lan.calls)
    s.code_var.set("123456")
    ctx.answers["askokcancel"] = False
    s.connect_btn.invoke()
    assert not any(x[0] == "send" for x in ctx.lan.calls)  # 확인 창에서 취소
    ctx.answers["askokcancel"] = True
    s.connect_btn.invoke()
    ctx.app.update()
    call = [x for x in ctx.lan.calls if x[0] == "send"][0]
    assert call[1:3] == ("192.0.2.20:47830", "123456")
    assert T.TX_RESULT_OK in s.result.cget("text") and "정확함 10" in s.result.cget("text") and T.TX_VERIFY_NEW_PC in s.result_notes.cget("text")
    assert ctx.app.overall_label.cget("text") == T.OVERALL_FMT.format(n=4, total=4)


def test_send_screen_lan_error_and_diagnose(ctx):
    s = to_send(ctx)
    s.target_var.set("192.0.2.20:47830")
    s.code_var.set("123456")
    s.diag_btn.invoke()
    ctx.app.update()
    assert s.diag.cget("text") == T.TX_DIAG_RESULT.format(summary="연결할 수 있습니다")
    assert ctx.lan.calls[-1] == ("diagnose", "192.0.2.20:47830", "123456")
    ctx.lan.send_error = LanError("english", "auth")
    s.connect_btn.invoke()
    ctx.app.update()
    assert T.LAN_ERRORS["auth"] in s.error.cget("text") and s.error.winfo_manager() and not s.result.winfo_manager()


def test_send_screen_cancel_note_and_back(ctx):
    from pcmigrate.cancel import Cancelled
    s = to_send(ctx)
    s.target_var.set("192.0.2.20")
    s.code_var.set("123456")

    def cancelling(*a, **k):
        ctx.c.cancel()
        raise Cancelled()
    ctx.lan.run_send = cancelling
    s.connect_btn.invoke()
    ctx.app.update()
    assert T.TX_CANCELLED in s.note.cget("text")
    s.back_btn.invoke()
    ctx.app.update()
    assert ctx.app.current == S.METHOD


def test_send_progress_uses_status_card(ctx):
    s = to_send(ctx)
    assert s.card.tasks == {T.TASK_LAN_SEND}
    s.target_var.set("192.0.2.20")
    s.code_var.set("123456")
    s.connect_btn.invoke()
    ctx.app.update()
    assert ctx.c.status.info_last is not None and ctx.c.status.task == T.TASK_LAN_SEND
