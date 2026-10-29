"""stage33(화면): 구 PC 2단계 표의 '자체 이전이 필요한 프로그램' 접힌 구역. 창을 숨긴 채 가짜 데이터만 쓴다."""
import pytest

from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from test_gui_controller import cand
from test_gui_screens_4_7 import new_ctl, set_discovery

tk = pytest.importorskip("tkinter")
from test_gui_smoke import ctx  # noqa: E402,F401

KAKAO = {"id": "kakaotalk", "name": "카카오톡", "category": "chat",
         "reason": "카카오톡 대화 내용은 이 폴더를 복사해도 새 PC에서 열리지 않습니다.", "todo": "카카오톡 자체의 대화 백업 기능으로 옮기세요."}


def st_cand(rel="KakaoTalk", **kw):
    c = cand(rel, **kw)
    c.link.programs = ["KakaoTalk"]
    c.self_transfer = dict(KAKAO)
    return c


def open_view(ctx, *cands):
    set_discovery(ctx.c, *cands)
    ctx.c.go(S.OLD_DISCOVER)
    ctx.app.update()
    return ctx.app.views[S.OLD_DISCOVER]


def test_controller_rows_carry_self_transfer(tmp_path):
    c, api, ev = new_ctl(tmp_path)
    set_discovery(c, cand("A"), st_cand())
    rows = {r.path: r for r in c.candidate_rows()}
    assert rows["C:\\fake\\KakaoTalk"].self_transfer["id"] == "kakaotalk" and rows["C:\\fake\\A"].self_transfer is None
    k = next(r for r in c.candidate_rows() if r.self_transfer)
    c.set_selected(k.key, True)  # 막지 않는다
    assert k.key in c.state.selected and c.selection_config_text().startswith("선택 1개")


def test_section_hidden_when_no_self_transfer(ctx):
    v = open_view(ctx, cand("A"))
    assert not v.st_box.winfo_manager() and len(v.tree.get_children()) == 1 and not v.st_tree.get_children()


def test_section_collapsed_by_default_with_count_and_badge_row(ctx):
    v = open_view(ctx, cand("A"), st_cand(), st_cand("KakaoCache2"))
    assert v.st_box.winfo_manager() and not v.st_box.open
    assert v.st_box.toggle_btn.cget("text") == T.DISC_SELF_TOGGLE.format(n=2) and "2개 ▸" in v.st_box.toggle_btn.cget("text")
    assert [v.rows[i].path for i in v.tree.get_children()] == ["C:\\fake\\A"]  # 메인 표에서는 빠진다
    v.st_box.toggle()
    ctx.app.update()
    assert v.st_box.open and v.st_box.toggle_btn.cget("text").endswith("▾") and v.st_box.body.winfo_manager()
    vals = v.st_tree.item(v.st_tree.get_children()[0], "values")
    assert vals[1] == T.DISC_SELF_BADGE and vals[2] == "KakaoTalk" and vals[3] == KAKAO["todo"] and vals[0] == T.UNCHECKED
    v.st_box.toggle()
    assert not v.st_box.open and v.st_box.toggle_btn.cget("text").endswith("▸")


def test_selecting_row_shows_detail_line(ctx):
    v = open_view(ctx, cand("A"), st_cand())
    v.st_box.toggle()
    ctx.app.update()
    iid = v.st_tree.get_children()[0]
    v.st_tree.selection_set(iid)
    v._show_st_detail()
    assert v.detail.cget("text") == T.DISC_SELF_DETAIL.format(reason=KAKAO["reason"], todo=KAKAO["todo"])
    assert v.detail.cget("text").startswith("이 프로그램은 폴더를 복사해서 옮길 수 없습니다. ")


def test_checking_row_selects_without_blocking_and_shows_dialog(ctx):
    c = ctx.c
    k = st_cand()
    v = open_view(ctx, cand("A"), k)
    v.st_box.toggle()
    ctx.app.update()
    v._toggle_st(v.st_tree.get_children()[0])
    assert k.key in c.state.selected
    assert any(kind == "info" and T.DISC_SELF_TITLE in args and KAKAO["todo"] in " ".join(map(str, args)) for kind, args in ctx.shown)
    assert v.st_tree.item(v.st_tree.get_children()[0], "values")[0] == T.CHECKED
    ctx.shown.clear()
    v._toggle_st(v.st_tree.get_children()[0])  # 해제할 때는 안내가 없다
    assert k.key not in c.state.selected and not ctx.shown


def test_select_all_skips_section_and_totals_count_selected(ctx):
    c = ctx.c
    a, k = cand("A"), st_cand()
    v = open_view(ctx, a, k)
    v._select_all()
    assert a.key in c.state.selected and k.key not in c.state.selected
    assert "1" in v.bulk_msg.cget("text")
    c.set_selected(k.key, True)  # 직접 체크한 구역 항목은 합계에 들어간다
    ctx.app.update()
    assert c.selection_config_text().startswith("선택 2개")
    v._clear_all()  # 해제는 구역 항목도 함께
    assert not c.state.selected


def test_section_rows_follow_secret_rule_and_main_table_height_rules_unchanged(ctx):
    v = open_view(ctx, cand("A"), st_cand(secret=["token.json"]))
    k = next(r for r in v.st_rows.values())
    assert k.secret and not k.selectable
    v._toggle_st(v.st_tree.get_children()[0])  # 민감 동의 전에는 기존대로 막힌다(안내 대화상자도 없다)
    assert not ctx.c.state.selected and not [s for s in ctx.shown if s[0] == "info"]
    assert v.table_rows() >= 8 and v.set_table_rows(3) == 8  # 구역이 있어도 표 최소 8줄은 그대로
    assert v.sash.winfo_manager() and v.st_slot.winfo_manager()


def test_new_discovery_rebuilds_section_and_hides_when_empty(ctx):
    v = open_view(ctx, cand("A"), st_cand())
    assert v.st_box.winfo_manager()
    set_discovery(ctx.c, cand("A"))
    v._refresh()
    assert not v.st_box.winfo_manager()


def test_texts_are_clean_and_polite():
    for t in (T.DISC_SELF_TOGGLE, T.DISC_SELF_HINT, T.DISC_SELF_DETAIL, T.DISC_SELF_CHECK, T.DISC_SELF_BADGE, *T.DISC_SELF_COLS):
        assert t.strip()
    assert "해 주세요" in T.DISC_SELF_CHECK
