"""stage34: 화면 시험(창을 숨긴 채). 시작 화면 카드 순서, ② 설정 묶음 줄, ③ 찾은 설정 묶음 목록, 탐색기에서 보기(가짜 함수 주입).
가짜 data_api / lan_api / 탐색기 함수만 쓴다. 디스플레이/tkinter 가 없으면 건너뛴다."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")

from pcmigrate import find_data as fd  # noqa: E402
from pcmigrate.gui import state as S  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402
from pcmigrate.gui.controller import CandRow, Controller  # noqa: E402
from pcmigrate.gui.runner import Runner  # noqa: E402
from pcmigrate.gui.views.common import CoverageBox  # noqa: E402

from test_gui_controller import FakeApi  # noqa: E402
from test_stage30a_files import FakeLan, folder  # noqa: E402
from test_stage30a_gui_tk import ctx, go, labels_of  # noqa: E402,F401  (ctx 는 시험 모듈의 fixture)
from test_stage34 import StagedData, StagedLan, make_zip, touch  # noqa: E402


@pytest.fixture
def real(ctx):
    """ctx 에 '실제로 있는 시험용 폴더'가 든 찾기 결과, 가짜 바탕화면/임시 위치, 가짜 탐색기 함수를 넣는다."""
    tmp = ctx.tmp
    proj, other = tmp / "Proj" / "Alpha", tmp / "Proj" / "Beta"
    proj.mkdir(parents=True), other.mkdir(parents=True)
    res = fd.FindResult()
    res.projects = [folder(str(proj), design=1, total=5 * 1024 ** 3), folder(str(other), photo=1, total=2 * 1024 ** 3)]
    res.complete, res.files_seen, res.seconds = True, 2, 1.0
    ctx.c.data_api, ctx.c.lan_api = StagedData(res), StagedLan(tmp)
    desk = tmp / "Desktop"
    desk.mkdir()
    ctx.c.default_desktop = lambda: str(desk)
    ctx.c.default_downloads = lambda: str(tmp / "Downloads")
    ctx.c.list_drives = lambda: []
    ctx.c.bundle_temp_root = str(tmp / "Temp")
    (tmp / "Temp").mkdir()
    opened = []
    ctx.c.explorer_fn = lambda path, select: opened.append((path, select)) or True
    ctx.desk, ctx.opened, ctx.proj, ctx.other = desk, opened, proj, other
    return ctx


# =========================================================================== 시작 화면
def test_start_screen_shows_new_order_with_where_labels(ctx):
    v = go(ctx, S.START)
    assert len(v.cards) == 3 and len(v.where_labels) == 3
    heads = [" ".join(labels_of(card)[0].split()) for card in v.cards]
    assert heads[0].startswith("① 쓰던 PC에서 준비하기") and heads[1].startswith("② 내 파일 옮기기") and heads[2].startswith("③ 새 PC에서 설치하고")
    assert [w.cget("text") for w in v.where_labels] == list(T.START_WHERE)
    for card, text in zip(v.cards, T.START_WHERE):
        assert text in labels_of(card)
    assert [b.cget("text") for b in v.buttons] == [c[2] for c in T.START_CHOICES]
    targets = []
    ctx.c.go = lambda screen: targets.append(screen)
    for b in v.buttons:
        b.invoke()
    assert targets == [S.OLD_SCAN, S.FIND, S.NEW_PACKAGE]  # 화면 id 는 그대로, 배치만 바뀜
    assert T.START_ORDER_NOTE in labels_of(v)


def test_start_auto_buttons_keep_their_flows_after_reorder(ctx):
    v = go(ctx, S.START)
    flows = []
    ctx.c.auto_run = lambda flow: flows.append(flow)
    for b in v.auto_buttons:
        b.invoke()
    assert flows == ["old", "transfer", "new"]


def test_roadmap_dialog_shows_new_intro_and_keys_unchanged(ctx):
    dlg = ctx.app.show_roadmap()
    ctx.app.update()
    texts = " ".join(labels_of(dlg))
    assert "연결(보내기·받기)은 ②에서만" in texts and "'② 내 파일 옮기기'" in texts
    assert [k for k, *_ in T.ROADMAP_OLD] == [f"old{i}" for i in range(1, 7)] and [k for k, *_ in T.ROADMAP_NEW] == [f"new{i}" for i in range(1, 7)]
    dlg.destroy()


def test_step_bar_names_follow_new_numbers(ctx):
    go(ctx, S.OLD_SCAN)
    assert ctx.app.overall_flow.cget("text") == "① 쓰던 PC에서 준비"
    go(ctx, S.NEW_PACKAGE)
    assert ctx.app.overall_flow.cget("text") == "③ 새 PC에서 설치하고 가져오기"
    go(ctx, S.FIND)
    assert ctx.app.overall_flow.cget("text") == "② 내 파일 옮기기" and ctx.app.crumb.cget("text").startswith("② 내 파일 옮기기")


# =========================================================================== ② 첫 화면: 설정 묶음 함께 보내기
def test_find_screen_hides_bundle_row_without_zip(real):
    v = go(real, S.FIND)
    assert not v.bundle_row.winfo_manager() and not v.bundle_row.winfo_ismapped()
    assert real.c.files.bundle_include is False


def test_find_screen_bundle_row_default_unchecked_and_sits_above_the_table(real):
    make_zip(real.desk / "pcmigrate-settings.zip", b"x" * 4096)
    v = go(real, S.FIND)
    assert v.bundle_row.winfo_ismapped() and v.bundle_var.get() is False and real.c.bundle_included() is False  # 자동 선택 금지
    assert v.bundle_cb.cget("text") == T.FD_BUNDLE_CHECK.format(name="pcmigrate-settings.zip", size="4.0KB")
    assert v.bundle_note.cget("text") == T.FD_BUNDLE_NOTE
    assert v.bundle_row.winfo_y() < v.top_row.winfo_y()  # 맨 위(찾기 단추 줄보다 위)
    v.find_btn.invoke()
    real.app.update()
    assert v.bundle_row.winfo_y() < v.tree_frame.winfo_y()  # 폴더 표 위
    assert [str(w) for w in v.body.pack_slaves()][0] == str(v.bundle_row)
    assert str(v.next_btn.cget("state")) == "disabled"  # 폴더도 묶음도 고르지 않았다


def test_checking_bundle_adds_it_to_totals_and_enables_next_even_without_folders(real):
    make_zip(real.desk / "pcmigrate-settings.zip", b"x" * 4096)
    v = go(real, S.FIND)
    v.bundle_cb.invoke()
    real.app.update()
    assert real.c.bundle_included() and v.bundle_var.get() is True
    assert T.BUNDLE_INCLUDED in v.total.cget("text")
    assert "disabled" not in v.next_btn.state()  # 설정 묶음만 보내도 다음으로 갈 수 있다
    v.next_btn.invoke()
    real.app.update()
    assert real.c.state.screen == S.METHOD
    sub = real.app.views[S.METHOD].sub.cget("text")
    assert sub == T.MV_SUB_BUNDLE_ONLY
    go(real, S.FIND)
    v.find_btn.invoke()
    real.app.update()
    v._toggle(v.tree.get_children()[0])
    real.app.update()
    assert T.FD_TOTAL.format(n=1, size="5.0GB") in v.total.cget("text") and T.BUNDLE_INCLUDED in v.total.cget("text")
    go(real, S.METHOD)
    assert "(" + T.BUNDLE_INCLUDED + ")" in real.app.views[S.METHOD].sub.cget("text")
    go(real, S.FIND)
    v.bundle_cb.invoke()  # 해제하면 줄에서도 합계에서도 빠진다
    real.app.update()
    assert not real.c.bundle_included() and T.BUNDLE_INCLUDED not in v.total.cget("text")


def test_bundle_row_disappears_when_the_zip_is_gone(real):
    z = make_zip(real.desk / "pcmigrate-settings.zip")
    v = go(real, S.FIND)
    assert v.bundle_row.winfo_manager()
    os.remove(z)
    go(real, S.START)
    v = go(real, S.FIND)
    assert not v.bundle_row.winfo_manager()


def test_direct_send_screen_and_confirm_dialog_mention_the_bundle(real, monkeypatch):
    from tkinter import messagebox
    make_zip(real.desk / "pcmigrate-settings.zip", b"x" * 100)
    asked = []
    monkeypatch.setattr(messagebox, "askokcancel", lambda title, msg, **k: asked.append(msg) or True)
    v = go(real, S.FIND)
    v.find_btn.invoke()
    real.app.update()
    v._toggle(v.tree.get_children()[0])
    v.bundle_cb.invoke()
    real.app.update()
    real.c.set_method(T.MV_DIRECT)
    real.c.set_role(T.MV_ROLE_SEND)
    send = go(real, S.LAN_SEND)
    assert T.BUNDLE_INCLUDED in send.selection.cget("text")
    send.target_var.set("192.0.2.20:47830")
    send.code_var.set("123456")
    send._connect()
    real.app.update()
    assert asked and T.BUNDLE_INCLUDED in asked[0] and "pcmigrate-settings.zip" in asked[0]
    assert T.BUNDLE_INCLUDED in send.result_notes.cget("text")  # 결과에도 '설정 묶음 1개 포함'
    assert os.listdir(real.tmp / "Temp") == []  # 임시 폴더는 정리됨


def test_selection_transfer_screen_shows_bundle_line_and_confirm_and_result(real, monkeypatch):
    from tkinter import messagebox
    make_zip(real.desk / "pcmigrate-settings.zip", b"x" * 100)
    asked = []
    monkeypatch.setattr(messagebox, "askokcancel", lambda title, msg, **k: asked.append(msg) or True)
    v = go(real, S.FIND)
    v.find_btn.invoke()
    real.app.update()
    v._toggle(v.tree.get_children()[0])
    v.bundle_cb.invoke()
    real.c.enter_selection_transfer(T.MODE_EXTERNAL)
    real.app.update()
    tv = real.app.views[S.TRANSFER]
    assert T.BUNDLE_INCLUDED in tv.sel_box.cget("text")
    out = real.tmp / "외장"
    out.mkdir()
    real.c.state.transfer_dst = str(out)
    real.c.start_transfer(True, False, False, "", False)
    real.app.update()
    assert T.BUNDLE_INCLUDED in tv.preview_box.cget("text")  # 미리보기에도 표시
    assert T.BUNDLE_INCLUDED in real.c.src_display()


# =========================================================================== ② 폴더 줄 탐색기에서 보기
def click_event(tree, iid, col_id="#2"):
    """identify_* 를 고정해 더블클릭/클릭 이벤트를 흉내 낸다(실제 마우스 없이)."""
    tree.identify_row = lambda y, _i=iid: _i
    tree.identify_region = lambda x, y: "cell"
    tree.identify_column = lambda x, _c=col_id: _c
    return SimpleNamespace(x=1, y=1)


def test_find_rows_open_in_explorer_via_button_double_click_and_enter(real):
    v = go(real, S.FIND)
    v.find_btn.invoke()
    real.app.update()
    first, second = v.tree.get_children()[:2]
    assert "disabled" in v.explorer.button.state()  # 줄을 고르기 전에는 꺼져 있다
    v.tree.selection_set(first)
    real.app.update()
    assert "disabled" not in v.explorer.button.state() and v.explorer.button.cget("text") == T.BTN_OPEN_FOLDER
    v.explorer.button.invoke()
    assert real.opened == [(str(real.proj), False)]  # 폴더는 폴더를 연다(파일 실행 없음)
    # 더블클릭(체크 칸이 아닌 곳): 열기만 하고 체크는 그대로
    ev = click_event(v.tree, second, "#2")
    v._click(ev)  # 더블클릭의 첫 클릭이 체크를 바꾼다
    real.app.update()
    assert real.c.data_selection() == [str(real.other)]
    assert v.explorer.on_double(v.tree, ev) == "break"
    real.app.update()
    assert real.opened[-1] == (str(real.other), False) and real.c.data_selection() == []  # 첫 클릭의 체크를 되돌렸다
    # Enter
    v.tree.selection_set(first)
    real.app.update()
    assert v.explorer.on_enter(v.tree) == "break" and real.opened[-1] == (str(real.proj), False)


def test_find_check_column_double_click_toggles_and_never_opens(real):
    v = go(real, S.FIND)
    v.find_btn.invoke()
    real.app.update()
    first = v.tree.get_children()[0]
    n = len(real.opened)
    ev = click_event(v.tree, first, "#1")  # 체크 칸
    v._click(ev)  # 첫 클릭: 체크 켜짐
    real.app.update()
    assert real.c.data_selection() == [str(real.proj)]
    v.explorer.on_double(v.tree, ev)  # 둘째 클릭: 체크 칸 더블클릭은 원래대로 체크를 바꾼다(탐색기는 열지 않는다)
    real.app.update()
    assert real.c.data_selection() == [] and len(real.opened) == n


def test_find_row_with_path_not_on_this_pc_disables_button_and_explains(real):
    res = fd.FindResult()
    res.projects = [folder("\\\\server\\share\\Alpha", design=1, total=10), folder(str(real.tmp / "분리된드라이브" / "Beta"), photo=1, total=5)]
    res.complete = True
    real.c.data_api = StagedData(res)
    v = go(real, S.FIND)
    v.find_btn.invoke()
    real.app.update()
    for iid in v.tree.get_children():
        v.tree.selection_set(iid)
        real.app.update()
        assert "disabled" in v.explorer.button.state()
        assert v.explorer.note.cget("text") == T.OPEN_NOT_HERE and v.explorer.note.winfo_manager()  # '이 PC에서는 열 수 없습니다'
        assert v.explorer.open_selected() is False
    assert real.opened == []
    v.tree.selection_set(())
    real.app.update()
    assert not v.explorer.note.winfo_manager()


# =========================================================================== ① 2단계 표 탐색기에서 보기
def test_discover_table_opens_candidate_folders_but_not_from_check_column(ctx, tmp_path, monkeypatch):
    app, c = ctx.app, ctx.c
    ok_dir, gone = tmp_path / "AppData" / "Vendor", tmp_path / "unplugged" / "Vendor"
    ok_dir.mkdir(parents=True)
    rows = [CandRow(key=("a",), programs="Demo", path=str(ok_dir), kind=T.KIND_FOLDER, size="1KB", grade="높음", reason="", weak=False),
            CandRow(key=("b",), programs="Other", path=str(gone), kind=T.KIND_FOLDER, size="1KB", grade="높음", reason="", weak=False),
            CandRow(key=("c",), programs="Self", path=str(ok_dir), kind=T.KIND_FOLDER, size="1KB", grade="높음", reason="", weak=False,
                    self_transfer={"id": "x", "name": "X", "category": "c", "reason": "r", "todo": "t"})]
    monkeypatch.setattr(c, "candidate_rows", lambda: rows)
    monkeypatch.setattr(c, "candidate_cfg_cells", lambda: {})
    opened = []
    c.explorer_fn = lambda path, select: opened.append((path, select)) or True
    v = app.views[S.OLD_DISCOVER]
    c.go(S.OLD_DISCOVER)
    v._refresh()
    app.update()
    a_iid, b_iid = v.tree.get_children()
    assert "disabled" in v.explorer.button.state() and v.explorer.button.cget("text") == T.BTN_OPEN_FOLDER
    v.tree.selection_set(a_iid)
    app.update()
    assert "disabled" not in v.explorer.button.state()
    v.explorer.button.invoke()
    assert opened == [(str(ok_dir), False)]
    # 줄 더블클릭(체크 칸이 아닌 곳): 열고, 첫 클릭이 바꾼 체크는 되돌린다
    ev = click_event(v.tree, a_iid, "#3")
    v._click(ev)
    assert ("a",) in c.state.selected
    ev = click_event(v.tree, v.tree.get_children()[0], "#3")  # 첫 클릭이 표를 다시 그려 줄 번호가 바뀐다
    v.explorer.on_double(v.tree, ev)
    assert opened[-1] == (str(ok_dir), False) and ("a",) not in c.state.selected
    # 체크 칸 더블클릭은 열지 않는다
    n = len(opened)
    ev = click_event(v.tree, v.tree.get_children()[0], "#1")
    v.explorer.on_double(v.tree, ev)
    assert len(opened) == n
    # Enter
    v.tree.selection_set(v.tree.get_children()[0])
    app.update()
    v.explorer.on_enter(v.tree)
    assert len(opened) == n + 1
    # 이 PC 에 없는 경로: 비활성 + 안내
    v.tree.selection_set(v.tree.get_children()[1])
    app.update()
    assert "disabled" in v.explorer.button.state() and v.explorer.note.cget("text") == T.OPEN_NOT_HERE
    # '자체 이전 필요' 구역의 줄도 같은 단추로
    st_iid = v.st_tree.get_children()[0]
    v.st_tree.selection_set(st_iid)
    app.update()
    assert "disabled" not in v.explorer.button.state()
    v.explorer.button.invoke()
    assert opened[-1] == (str(ok_dir), False)
    assert T.OPEN_HINT in labels_of(v.more.body)


# =========================================================================== ① 완료 화면: 빠진 파일 예시
def coverage_table(folder_path):
    return [{"program": "Demo", "what": "설정", "status": "담김", "files": 3, "note": "", "rule": "demo", "path": folder_path,
             "excluded_files": 2, "excluded_bytes": 3000, "excluded_by_reason": {"데이터베이스": 2},
             "excluded_line": "제외 2개, 2.9KB(데이터베이스 2개)",
             "excluded_examples": [{"name": "sub/big.db", "size": 2000, "why": "데이터베이스"}, {"name": "..\\evil.exe", "size": 5, "why": "기타"}]},
            {"program": "Remote", "what": "설정", "status": "담김", "files": 1, "note": "", "rule": "r", "path": "%APPDATA%\\Remote",
             "excluded_files": 1, "excluded_bytes": 10, "excluded_by_reason": {"기타": 1}, "excluded_line": "제외 1개, 10B",
             "excluded_examples": [{"name": "a.db", "size": 10, "why": "기타"}]}]


def test_coverage_examples_open_in_explorer_only_when_path_can_be_built(ctx, tmp_path):
    cfg = tmp_path / "Demo"
    (cfg / "sub").mkdir(parents=True)
    (cfg / "sub" / "big.db").write_text("x")
    opened = []
    c = ctx.c
    c.explorer_fn = lambda path, select: opened.append((path, select)) or True
    box = CoverageBox(ctx.app.views[S.OLD_EXPORT], title="", explorer=(c.can_show_in_explorer, c.show_in_explorer))
    box.pack()
    box.show(coverage_table(str(cfg)))
    ctx.app.update()
    assert box.open_btn.cget("text") == T.BTN_OPEN_EXPLORER and "disabled" in box.open_btn.state()
    paths = list(box.ex_paths.values())
    assert paths == [(str(cfg / "sub" / "big.db"), True), None, None]  # 상대 이름 '..' 와 풀리지 않은 환경변수 폴더는 만들 수 없다
    ids = list(box.ex_paths)
    box.tree.selection_set(ids[0])
    ctx.app.update()
    assert "disabled" not in box.open_btn.state() and box.open_note.cget("text") == ""
    box.open_btn.invoke()
    assert opened == [(str(cfg / "sub" / "big.db"), True)]  # 파일은 선택된 상태로 부모 폴더(열지 않음)
    box._key_line()  # Enter
    assert opened[-1] == (str(cfg / "sub" / "big.db"), True) and len(opened) == 2
    ev = SimpleNamespace(x=1, y=1)
    box.tree.identify_row = lambda y: ids[0]
    assert box._double_example(ev) == "break" and len(opened) == 3
    box.tree.selection_set(ids[1])
    ctx.app.update()
    assert "disabled" in box.open_btn.state() and box.open_note.cget("text") == T.OPEN_NOT_HERE
    assert box.open_example(ids[2]) is False and len(opened) == 3
    box.show([])  # 다시 그리면 단추도 꺼진다
    assert "disabled" in box.open_btn.state() and box.ex_paths == {}


def test_coverage_box_without_explorer_has_no_button_and_new_pc_boxes_stay_plain(ctx):
    box = CoverageBox(ctx.app.views[S.OLD_EXPORT], title="")
    assert box.open_btn is None and box.explorer is None
    assert ctx.app.views[S.NEW_PREVIEW].cov.open_btn is None  # 신 PC 미리보기: 다른 PC 의 경로라 열지 않는다
    assert ctx.app.views[S.OLD_EXPORT].cov.open_btn is not None


# =========================================================================== ③ 이 PC 에서 찾은 설정 묶음
def test_package_screen_lists_found_zips_latest_first_and_fills_the_path(real):
    real.c.scan_packages_on_show = True
    recv = real.desk / "PC이전_받은파일_20260102-000000"
    old = touch(real.desk / "pcmigrate-settings.zip", 1_700_000_000)
    new = touch(recv / "C" / "x" / "pcmigrate-settings.zip", 1_700_000_900)
    v = go(real, S.NEW_PACKAGE)
    assert v.found_row.winfo_manager() and [p for p, *_ in v.found_entries] == [new, old]
    values = list(v.found_box.cget("values"))
    assert len(values) == 2 and T.PKG_WHERE_RECEIVED in values[0] and T.PKG_WHERE_DESKTOP in values[1]
    v.found_box.current(1)
    v.found_box.event_generate("<<ComboboxSelected>>")
    real.app.update()
    assert v.path_var.get() == old and "disabled" not in v.next_btn.state()
    assert not [w for w in labels_of(v) if "보내는 쪽" in w or "받는 쪽" in w]  # ③ 은 이 PC 안의 작업: 연결 기능 없음


def test_package_screen_hides_found_row_when_nothing_found(real):
    real.c.scan_packages_on_show = True
    v = go(real, S.NEW_PACKAGE)
    assert not v.found_row.winfo_manager()
