"""stage37: 결과/안내/요약 영역은 내용이 없으면 자리를 차지하지 않는다(높이 0 으로 접힘). 내용이 생기면 원래 자리에 다시 나타난다.
창을 숨긴 채 위젯의 배치 상태(winfo_manager)와 요청 높이(winfo_reqheight)로 확인한다. 가짜 컨트롤러/가짜 폴더만 쓴다.
디스플레이/tkinter 가 없으면 건너뛴다."""
import tkinter as tk

import pytest

pytest.importorskip("tkinter")

from pcmigrate.gui import state as S  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402
from pcmigrate.gui.views import common as C  # noqa: E402

from test_gui_my_files_tk import ctx, go, labels_of  # noqa: E402,F401  (ctx 는 시험 모듈의 fixture)
from test_gui_start_cards_tk import real  # noqa: E402,F401


def occupies(w, root) -> bool:
    """위젯이 화면 공간을 차지하는가: 자기와 위쪽 부모가 모두 배치돼 있고 요청 높이가 2 이하가 아니다."""
    x = w
    while x is not None and x is not root:
        if not x.winfo_manager():
            return False
        x = x.master
    return w.winfo_reqheight() > 2


def pick(view, dotted: str):
    obj = view
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


# 화면 id -> 처음(결과 전)에 내용이 없어 접혀 있어야 하는 위젯 이름
EMPTY_AT_START = {
    S.OLD_SCAN: ["tree_title", "tree_frame"],
    S.OLD_DISCOVER: ["banner", "detail", "find_msg", "find_full"],
    S.OLD_EXPORT: ["summary", "cov", "cache_note", "stop_box", "next_box", "cov_fold", "more"],
    S.NEW_INSTALL: ["panel.detail", "panel.copy_note"],
    S.NEW_PREVIEW: ["problem_box", "files_frame", "cov_fold", "more", "dirty"],
    S.NEW_RESULT: ["summary", "cov", "tree_frame"],
    S.TRANSFER: ["check_label", "preview_box", "warn_label", "loc_box", "notes_box", "result", "tree_frame"],
    S.FIND: ["bulk_msg", "notice"],
    S.LAN_SEND: ["found_msg", "diag", "error", "note", "result", "result_notes"],
    S.LAN_RECV: ["progress_label", "error", "result"],
    S.AUTO: ["msg", "stop_note", "gate", "out"],
}


@pytest.mark.parametrize("screen", sorted(EMPTY_AT_START))
def test_result_areas_are_collapsed_before_any_result(ctx, screen):
    v = go(ctx, screen)
    for name in EMPTY_AT_START[screen]:
        w = pick(v, name)
        assert not occupies(w, ctx.app), f"{screen}.{name} 이 빈 채로 자리를 차지한다(manager={w.winfo_manager()!r}, h={w.winfo_reqheight()})"


def test_no_empty_label_takes_space_on_any_screen(ctx):
    """글이 빈 tk/ttk 라벨이 세로로 쌓이는 자리에서 높이를 차지하지 않는다(이동 막대/한 줄 안의 라벨 제외)."""
    skip_names = {"next_reason", "reason", "why", "hidden_label", "progress_label", "big_note"}
    bad = []
    for name, v in ctx.app.views.items():
        go(ctx, name)

        def walk(w):
            for ch in w.winfo_children():
                yield ch
                yield from walk(ch)

        for w in walk(v):
            if isinstance(w, tk.Toplevel) or w.winfo_class() not in ("Label", "TLabel"):
                continue
            if str(w.cget("text")) == "" and occupies(w, ctx.app):
                owner = next((k for k, x in vars(v).items() if x is w), "?")
                if owner not in skip_names:
                    bad.append((name, owner))
    assert not bad, bad


def test_idle_footer_and_status_card_take_no_space(ctx):
    app = ctx.app
    go(ctx, S.TRANSFER)
    for w in (app.status, app.note, app.after_note, app.force_box, app.progress, app.stop_btn):
        assert not w.winfo_manager()
    for name in (S.TRANSFER, S.NEW_RESULT, S.OLD_EXPORT, S.AUTO, S.FIND):
        v = go(ctx, name)
        card = getattr(v, "card", None)
        if card is not None:
            assert not card.shown and not card.winfo_manager()
            assert v.card_slot.winfo_reqheight() <= 2


# --------------------------------------------------------------------------- 공통 도우미
def test_helper_collapses_and_returns_to_the_same_place(ctx):
    host = tk.Frame(ctx.app)
    host.pack()
    a = tk.Label(host, text="a")
    b = C.FoldLabel(host, text="b")
    c = tk.Label(host, text="c")
    for w in (a, b, c):
        w.pack(fill="x", pady=5)
    ctx.app.update_idletasks()
    full = host.winfo_reqheight()
    C.set_text_or_hide(b, "")
    ctx.app.update_idletasks()
    assert not b.winfo_manager() and host.winfo_reqheight() < full - 10  # 접히면 자기 높이와 여백이 모두 사라진다
    assert [s for s in host.pack_slaves() if isinstance(s, tk.Label)] == [a, c]
    C.set_text_or_hide(b, "b")
    ctx.app.update_idletasks()
    assert b.winfo_manager() == "pack" and [s for s in host.pack_slaves() if isinstance(s, tk.Label)] == [a, b, c]
    assert abs(host.winfo_reqheight() - full) <= 2  # 자리표시 틀이 1픽셀을 요청할 수 있다
    assert b.pack_info()["pady"] == a.pack_info()["pady"]  # 원래 옵션 그대로
    host.destroy()


def test_order_kept_when_neighbours_fold_and_unfold_in_any_order(ctx):
    host = tk.Frame(ctx.app)
    host.pack()
    ws = [C.FoldLabel(host, text=str(i)) for i in range(4)]
    for w in ws:
        w.pack(anchor="w")
    for order in ((1, 2), (2, 1)):
        for i in order:
            ws[i].configure(text="")
        for i in order:
            ws[i].configure(text=f"x{i}")
        assert [s for s in host.pack_slaves() if isinstance(s, tk.Label)] == ws
    host.destroy()


def test_empty_label_packed_empty_is_collapsed_and_shown_later(ctx):
    host = tk.Frame(ctx.app)
    host.pack()
    lbl = C.FoldTLabel(host, text="")
    lbl.pack(fill="x", pady=6)
    assert not lbl.winfo_manager()
    lbl.configure(text="내용")
    assert lbl.winfo_manager() == "pack" and lbl.pack_info()["pady"] in (6, "6")
    host.destroy()


def test_pack_before_or_after_a_folded_widget_works(ctx):
    host = tk.Frame(ctx.app)
    host.pack()
    a, hid, z = tk.Label(host, text="a"), C.FoldLabel(host, text="h"), tk.Label(host, text="z")
    for w in (a, hid, z):
        w.pack()
    hid.configure(text="")
    new1, new2 = tk.Label(host, text="n1"), tk.Label(host, text="n2")
    new1.pack(before=hid)
    new2.pack(after=hid)
    hid.configure(text="h")
    assert [s for s in host.pack_slaves() if isinstance(s, tk.Label)] == [a, new1, hid, new2, z]
    host.destroy()


def test_direct_pack_forget_by_caller_is_respected(ctx):
    host = tk.Frame(ctx.app)
    host.pack()
    lbl = C.FoldLabel(host, text="x")
    lbl.pack()
    lbl.configure(text="")
    lbl.pack_forget()  # 호출한 쪽이 직접 숨긴 위젯은 글이 생겨도 멋대로 나타나지 않는다
    lbl.configure(text="y")
    assert not lbl.winfo_manager()
    host.destroy()


def test_notice_clear_collapses_and_set_shows_again(ctx):
    host = tk.Frame(ctx.app)
    host.pack()
    n = C.Notice(host, "안내", "warn")
    n.pack(fill="x", pady=4)
    assert n.winfo_manager()
    n.clear()
    ctx.app.update_idletasks()
    assert not n.winfo_manager() and host.winfo_reqheight() <= 2
    n.set("다시", "danger")
    assert n.winfo_manager() == "pack" and n.cget("text") == "다시"
    host.destroy()


def test_tab_order_keeps_nav_bar_last_after_folding(ctx):
    """접힘 때 남기는 자리표시 틀이 이동 막대 뒤로 밀려나 Tab 순서가 바뀌지 않는다."""
    for name in (S.TRANSFER, S.FIND, S.NEW_PREVIEW, S.OLD_EXPORT, S.NEW_RESULT):
        v = go(ctx, name)
        for w in v.winfo_children():
            pass
        # 일부러 접었다 펴 본다
        for attr in ("check_label", "summary", "notice", "problem_box"):
            w = getattr(v, attr, None)
            if isinstance(w, (C.FoldLabel, C.FoldTLabel)):
                keep = str(w.cget("text"))
                w.configure(text="")
                w.configure(text=keep)
        assert v.winfo_children()[-1] is v._nav, name


# --------------------------------------------------------------------------- ResultSummary / CoverageBox
def test_result_summary_collapses_before_and_after_result_and_keeps_its_place(ctx):
    host = tk.Frame(ctx.app)
    host.pack(fill="x")
    top, bottom = tk.Label(host, text="위"), tk.Label(host, text="아래")
    top.pack()
    summary = C.ResultSummary(host)
    summary.pack(fill="x", pady=(6, 0))
    bottom.pack()
    C.collapse_empty(host)
    ctx.app.update_idletasks()
    assert not summary.winfo_manager()  # 결과 전
    summary.show(3, 1, 0, "모두 잘 끝났습니다.", "실패")
    ctx.app.update_idletasks()
    assert summary.winfo_manager() == "pack" and summary.winfo_reqheight() > 20
    assert [s for s in host.pack_slaves() if s in (top, summary, bottom)] == [top, summary, bottom]
    summary.clear()
    ctx.app.update_idletasks()
    assert not summary.winfo_manager() and host.winfo_reqheight() < top.winfo_reqheight() + bottom.winfo_reqheight() + 4
    summary.show(0, 0, 2, "ok", "문제가 있습니다.")
    assert [s for s in host.pack_slaves() if s in (top, summary, bottom)] == [top, summary, bottom]
    host.destroy()


def test_coverage_box_collapses_when_cleared(ctx):
    host = tk.Frame(ctx.app)
    host.pack(fill="x")
    box = C.CoverageBox(host)
    box.pack(fill="x")
    C.collapse_empty(host)
    assert not box.winfo_manager()
    box.show([])  # 표가 비어도 '없음' 안내 한 줄은 보인다
    assert box.winfo_manager() == "pack"
    box.clear()
    assert not box.winfo_manager()
    host.destroy()


# --------------------------------------------------------------------------- 화면별: 내용 있음 -> 보임 -> 사라짐 -> 접힘, 재진입
def test_transfer_result_areas_appear_and_collapse_again(ctx):
    t = go(ctx, S.TRANSFER)
    src, dst = ctx.tmp / "s", ctx.tmp / "d"
    src.mkdir()
    t.src_var.set(str(src))
    t.dst_var.set(str(dst))
    ctx.app.update()
    assert occupies(t.check_label, ctx.app) and not occupies(t.result, ctx.app)  # 입력 확인 줄은 글이 있어 보인다
    t.dry_btn.invoke()
    ctx.app.update()
    assert occupies(t.preview_box, ctx.app) and not occupies(t.result, ctx.app)  # 미리보기: 한 줄 요약만, 큰 요약 상자는 접힘
    t.go_btn.invoke()
    ctx.app.update()
    assert occupies(t.result, ctx.app)  # 복사 결과가 나오면 요약 상자가 원래 자리(요약 줄 위)에 나타난다
    slaves = t.more.master.pack_slaves()
    assert slaves.index(t.result) < slaves.index(t.more) and t.check_label in slaves  # 요약 상자는 아래 접이식보다 위(원래 자리)
    # 다른 화면에 갔다가 돌아와 입력을 지우면 다시 접힌다
    go(ctx, S.START)
    t = go(ctx, S.TRANSFER)
    t.src_var.set("")
    t.dst_var.set("")
    ctx.app.update()
    assert not occupies(t.check_label, ctx.app)


def test_collapsed_state_survives_resize_and_reentry(ctx):
    v = go(ctx, S.NEW_RESULT)
    assert not occupies(v.summary, ctx.app) and not occupies(v.tree_frame, ctx.app)
    ctx.app.geometry("900x640")
    ctx.app.update()
    ctx.app.geometry("700x500")
    ctx.app.update()
    assert not occupies(v.summary, ctx.app) and not occupies(v.tree_frame, ctx.app) and not occupies(v.cov, ctx.app)
    go(ctx, S.START)
    v = go(ctx, S.NEW_RESULT)
    assert not occupies(v.summary, ctx.app) and not occupies(v.cov, ctx.app)
    v.summary.show(1, 0, 0, "ok", "fail")
    ctx.app.update()
    assert occupies(v.summary, ctx.app)
    go(ctx, S.START)
    v = go(ctx, S.NEW_RESULT)  # 결과가 없는 상태로 다시 들어오면 on_show 가 접는다
    assert not occupies(v.summary, ctx.app)


def test_export_summary_cycle_keeps_stop_box_anchor_working(ctx):
    v = go(ctx, S.OLD_EXPORT)
    assert not occupies(v.summary, ctx.app)
    v.stop_box.pack(fill="x", pady=3, before=v.summary)  # 접힌 요약 앞에 배치해도 오류 없이 놓인다
    body = v.summary.master
    slaves = body.pack_slaves()
    assert slaves.index(v.stop_box) < slaves.index(v.next_box) if v.next_box in slaves else True
    v.stop_box.pack_forget()
    v.summary.show(2, 0, 0, "ok", "fail")
    ctx.app.update()
    assert occupies(v.summary, ctx.app)
    v.summary.clear()
    ctx.app.update()
    assert not occupies(v.summary, ctx.app)


def test_find_notice_and_summary_fold(ctx):
    v = go(ctx, S.FIND)
    assert not occupies(v.notice, ctx.app)
    ctx.c.files.find_error = "읽는 중 문제가 생겼습니다."
    ctx.c.emit("files_changed", None) if hasattr(ctx.c, "emit") else v._rebuild()
    ctx.app.update()
    assert occupies(v.notice, ctx.app)
    ctx.c.files.find_error = ""
    v._rebuild()
    ctx.app.update()
    assert not occupies(v.notice, ctx.app)


def test_lan_send_error_box_appears_and_folds(ctx):
    v = go(ctx, S.LAN_SEND)
    assert not occupies(v.error, ctx.app)
    ctx.c.files.tx_error = "연결하지 못했습니다."
    v._render()
    ctx.app.update()
    assert occupies(v.error, ctx.app)
    ctx.c.files.tx_error = ""
    v._render()
    ctx.app.update()
    assert not occupies(v.error, ctx.app)
    assert not occupies(v.result_notes, ctx.app)  # 결과가 없을 때 글 없이 pack 하던 줄도 접힌다


def test_text_dialog_without_body_has_no_empty_text_box(ctx):
    dlg = C.TextDialog(ctx.app, "제목", "")
    ctx.app.update()
    assert not dlg.text_frame.winfo_manager()
    dlg.destroy()
    dlg = C.TextDialog(ctx.app, "제목", "내용")
    ctx.app.update()
    assert dlg.text_frame.winfo_manager() == "pack"
    dlg.destroy()


def test_help_dialog_for_every_screen_keeps_its_text(ctx):
    for name in ctx.app.views:
        dlg = ctx.app.show_help(name)
        ctx.app.update()
        if T.HELP.get(name):
            assert dlg.text_frame.winfo_manager() == "pack"
        dlg.destroy()
    assert labels_of(ctx.app)  # 화면은 그대로


def test_reconfiguring_pack_options_of_a_folded_widget_keeps_the_others(ctx):
    """접힌 위젯에 pack_configure(pady=...) 만 해도 fill 같은 나머지 옵션이 사라지지 않는다(펴졌을 때 가운데로 쪼그라드는 문제)."""
    host = tk.Frame(ctx.app)
    host.pack()
    lbl = C.FoldLabel(host, text="")
    lbl.pack(fill="x", pady=(6, 0))
    lbl.pack_configure(pady=(3, 0))
    lbl.configure(text="내용")
    info = lbl.pack_info()
    assert info["fill"] == "x" and tuple(str(x) for x in info["pady"]) == ("3", "0")
    host.destroy()
    v = go(ctx, S.OLD_EXPORT)
    v.summary.show(1, 0, 0, "ok", "fail")
    ctx.app.update()
    assert v.summary.box.pack_info()["fill"] == "x" and tuple(str(x) for x in v.summary.box.pack_info()["pady"]) == ("3", "0")


# --------------------------------------------------------------------------- 표 머리글/열 경계선 드래그는 행을 건드리지 않는다
def _find_with_rows(real):
    v = go(real, S.FIND)
    v.find_btn.invoke()
    real.app.deiconify()
    real.app.geometry("900x640")
    real.app.update()
    return v


def _ev(tree, x, y):
    from types import SimpleNamespace
    return SimpleNamespace(x=x, y=y)


def test_header_click_and_separator_drag_do_not_toggle_rows(real):
    v = _find_with_rows(real)
    tree = v.tree
    iids = tree.get_children()
    assert iids
    before = [real.c.files.selected.copy()]
    row_y = tree.bbox(iids[0])[1] + 4
    head_y = max(1, tree.bbox(iids[0])[1] // 2)
    assert tree.identify_region(30, head_y) == "heading" and C.cell_row(tree, _ev(tree, 30, head_y)) == ""
    # 머리글 누름 / 열 경계선 위 누름 / 빈 영역
    sep_x = tree.column("#1", "width")
    for x, y in ((30, head_y), (sep_x, head_y), (5, 2000)):
        v._click(_ev(tree, x, y))
    assert [real.c.files.selected.copy()] == before
    # 실제 이벤트: 경계선을 눌러 아래 행 위까지 끌어 놓아도 체크가 바뀌지 않는다
    tree.focus_force()
    tree.event_generate("<Button-1>", x=sep_x, y=head_y)
    tree.event_generate("<B1-Motion>", x=sep_x + 20, y=row_y)
    tree.event_generate("<ButtonRelease-1>", x=sep_x + 20, y=row_y)
    real.app.update()
    assert real.c.files.selected == before[0]
    # 일반 행 클릭은 기존대로 체크가 바뀐다
    v._click(_ev(tree, 30, row_y))
    assert real.c.files.selected != before[0]


def test_coverage_line_toggle_needs_press_and_release_in_the_same_cell_row(ctx):
    host = tk.Frame(ctx.app)
    host.pack(fill="both")
    ctx.app.deiconify()
    ctx.app.geometry("900x640")
    box = C.CoverageBox(host)
    box.pack(fill="x")
    box.show([])
    ctx.app.update()
    tree = box.tree
    iid = tree.get_children()[0]
    box.lines.add(iid)
    seen = []
    box._toggle_line = lambda i, on=None: seen.append(i)
    row_y = tree.bbox(iid)[1] + 4
    head_y = max(1, tree.bbox(iid)[1] // 2)
    box._press_line(_ev(tree, 30, head_y))  # 머리글에서 눌러 행 위에서 놓음
    box._click_line(_ev(tree, 30, row_y))
    assert seen == []
    box._press_line(_ev(tree, 30, row_y))
    box._click_line(_ev(tree, 30, row_y))
    assert seen == [iid]
    host.destroy()


# --------------------------------------------------------------------------- 현황 카드: 성공으로 끝나면 한 줄 요약
def test_finished_status_card_shrinks_to_one_line_and_expands_on_click(ctx):
    v = go(ctx, S.OLD_SCAN)
    card = v.card
    ctx.c.status.begin(T.TASK_SCAN)
    card.refresh()
    ctx.app.update()
    running_h = card.winfo_reqheight()
    ctx.c.status.finish()
    card.refresh()
    ctx.app.update()
    assert card.shown and card._compact
    assert card.winfo_reqheight() <= 40 and card.winfo_reqheight() < running_h  # 큰 상자가 아니라 한 줄
    assert card.headline.cget("text").startswith(T.CARD_HEAD_DONE) and card.headline.cget("text").endswith("▸")
    assert not card.details.toggle_btn.winfo_manager() and not card.details.body.winfo_manager()
    card._headline_click()  # 요약 줄을 누르면 기존 자세히가 펼쳐진다
    ctx.app.update()
    assert card.details.open and card.details.body.winfo_manager() and card.headline.cget("text").endswith("▾")
    card._headline_click()
    ctx.app.update()
    assert not card.details.open and card.winfo_reqheight() <= 40


def test_failed_or_cancelled_status_card_stays_prominent(ctx):
    v = go(ctx, S.OLD_SCAN)
    card = v.card
    ctx.c.status.begin(T.TASK_SCAN)
    ctx.c.status.failed("문제")
    card.refresh()
    ctx.app.update()
    assert not card._compact and card.details.toggle_btn.winfo_manager()
    ctx.c.status.begin(T.TASK_SCAN)
    card.refresh()
    ctx.app.update()
    assert not card._compact and card.bar.winfo_manager()  # 새 작업이 시작되면 다시 큰 카드
