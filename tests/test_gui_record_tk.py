"""바꿔 보며 설정 찾기 화면의 tkinter 스모크 시험(창을 숨긴 채, stage60). 가짜 AppData(환경변수 주입)와 주입한 설치 목록만 쓴다.
① 화면의 '들어가는 길' 추가가 기존 화면의 문구/동작을 바꾸지 않았는지도 여기서 본다. tkinter/디스플레이가 없으면 건너뛴다."""
import json
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import filedialog, messagebox  # noqa: E402

from conftest import make_rules_dir  # noqa: E402
from pcmigrate import api as real_api  # noqa: E402
from pcmigrate import envpaths  # noqa: E402
from pcmigrate.detect import Program  # noqa: E402
from pcmigrate.gui import record_flow as rf  # noqa: E402
from pcmigrate.gui import state as S  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402
from pcmigrate.gui.app import App  # noqa: E402
from pcmigrate.gui.controller import Controller  # noqa: E402
from pcmigrate.gui.runner import Runner  # noqa: E402

from test_gui_collapse_empty_tk import occupies  # noqa: E402
from test_record_mode import USER, Fake  # noqa: E402


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    answers = {"askyesno": True}
    asked, shown = [], []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: (asked.append(a), answers["askyesno"])[1])
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: shown.append(("warn", a)))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(("info", a)))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(("error", a)))
    picked = {"dir": "", "save": ""}
    monkeypatch.setattr(filedialog, "askdirectory", lambda *a, **k: picked["dir"])
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda *a, **k: picked["save"])
    f = Fake(tmp_path, monkeypatch)
    envpaths.set_overrides({"USERPROFILE": str(f.home), "HOME": str(f.home), "APPDATA": str(f.app), "LOCALAPPDATA": str(f.loc),
                            "PROGRAMDATA": str(f.pd)})
    rules = make_rules_dir(tmp_path / "rules")
    deps = real_api.Deps(registry=lambda: [Program("Foo Edit", "2025", "Acme Inc.", install_location="C:\\Program Files\\Acme\\Foo Edit 2025"),
                                         Program("Zzyzx Tool", "1", "Nobody")])
    c = Controller(api_mod=real_api, deps=deps, runner=Runner(sync=True), rules_dir=rules)
    c.record_documents_fn = lambda: str(f.docs)
    c.record_process_list_fn = lambda: []
    c.record_version_info_fn = lambda p: {}
    c.record_work_dir_fn = lambda name: str(f.work)
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
    yield SimpleNamespace(app=app, c=c, f=f, answers=answers, asked=asked, shown=shown, picked=picked, tmp=tmp_path)
    app.destroy()
    envpaths.set_overrides(None)


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


def shown(ctx, w):
    return occupies(w, ctx.app)


def tree_texts(tree):
    return [tree.item(i, "values") for i in tree.get_children()]


def pick_first(ctx, v):
    iid = v.prog_tree.get_children()[0]
    v.prog_tree.selection_set(iid)
    ctx.app.update()


# ------------------------------------------------------------------ 화면 만들기
def test_screen_builds_without_overall_bar_and_starts_at_pick_room(ctx):
    v = go(ctx, S.RECORD)
    assert v.title_label.cget("text") == T.REC_HEADING == T.TITLES[S.RECORD]
    assert ctx.app.crumb.cget("text") == T.TITLES[S.RECORD]
    assert not ctx.app.overall.winfo_manager()  # 맨 위 '전체 단계 n/4' 는 표시하지 않는다
    assert shown(ctx, v.rooms[rf.ROOM_PICK]) and not any(shown(ctx, v.rooms[r]) for r in v.rooms if r != rf.ROOM_PICK)
    assert not shown(ctx, v.steps_strip) and not shown(ctx, v.reset_btn)  # 기록을 시작하기 전에는 단계 표시줄과 '처음부터 다시' 가 없다
    assert [r[0] for r in tree_texts(v.prog_tree)] == ["Foo Edit 2025", "Zzyzx Tool 1"]
    assert str(v.main_btn.cget("text")) == T.BTN_REC_NEXT_FOLDERS and "disabled" in v.main_btn.state()
    assert v.reason_label.cget("text") == T.G_REC_PICK
    dlg = ctx.app.show_help(S.RECORD)
    assert dlg.title() == T.HELP_TITLE.format(screen=T.TITLES[S.RECORD]) and T.REC_HELP.split("\n")[0] in dlg.text.get("1.0", "end")
    dlg.destroy()


def test_filter_checkbox_and_text_filter(ctx):
    v = go(ctx, S.RECORD)
    v.only_var.set(False)
    v.filter_var.set("zzy")
    ctx.app.update()
    assert [r[0] for r in tree_texts(v.prog_tree)] == ["Zzyzx Tool 1"]
    v.filter_var.set("")
    ctx.app.update()
    assert len(tree_texts(v.prog_tree)) == 2


# ------------------------------------------------------------------ ① 화면 진입 링크(기존 화면 회귀)
def test_old_pc_entry_link_added_and_existing_texts_unchanged(ctx):
    v = go(ctx, S.OLD_DISCOVER)
    # 기존 문구/단추는 그대로
    assert T.FIND_LABEL == "프로그램 하나만 자세히 찾기:" and T.FIND_BTN == "이 프로그램 설정 찾기" and T.FIND_PICK == "(프로그램 선택)"
    assert v.find_btn.cget("text") == T.FIND_BTN and v.find_var.get() == T.FIND_PICK
    assert T.TITLES[S.OLD_DISCOVER] == "2단계. 자동 목록에 없는 프로그램 확인" and T.TASK_FIND_PROGRAM == "이 프로그램 설정 찾기"
    texts = labels_of(v)
    assert T.FIND_LABEL in texts and T.FIND_BTN in texts and T.DISC_HINT_SHORT in " ".join(texts) or T.FIND_LABEL in texts
    # 새로 생긴 것은 안내 한 줄과 링크 하나뿐
    assert v.record_btn.cget("text") == T.BTN_REC_ENTER == "바꿔 보며 설정 찾기 ▸"
    assert T.REC_ENTER_NOTE in texts and shown(ctx, v.record_row)
    assert v.find_row.pack_info()["side"] == v.record_row.pack_info()["side"]
    names = [w for w in v.record_row.winfo_children()]
    assert len(names) == 2  # 안내 줄 + 링크
    v.record_btn.invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.RECORD and ctx.c.record_back_screen() == S.OLD_DISCOVER and ctx.c.record_state.program is None
    # 위 목록에서 프로그램을 고른 상태면 새 화면에 미리 채워진다
    ctx.c.go(S.OLD_DISCOVER)
    ctx.app.update()
    v = ctx.app.views[S.OLD_DISCOVER]
    v.find_var.set("Foo Edit")
    v.record_btn.invoke()
    ctx.app.update()
    assert ctx.c.record_state.program is not None and ctx.c.record_state.program.name == "Foo Edit"
    ctx.app.views[S.RECORD].back_btn.invoke()
    ctx.app.update()
    assert ctx.c.state.screen == S.OLD_DISCOVER  # '이전' 은 들어온 화면으로


def test_start_screen_has_no_record_card(ctx):
    v = ctx.app.views[S.START]
    blob = " ".join(labels_of(v))
    assert T.BTN_REC_ENTER not in blob and T.REC_HEADING not in blob  # 시작 화면에는 새 카드를 만들지 않는다


# ------------------------------------------------------------------ 전체 흐름(위젯으로)
def test_full_flow_through_widgets(ctx):
    f, app, c = ctx.f, ctx.app, ctx.c
    z = f.app / "Acme" / "Foo Edit 2025"
    ini = f.put(z / "settings.ini", "a=1")
    f.put(z / "keys.xml", "k=1")
    f.put(f.app / "Acme" / "Other" / "o.ini", "o")
    v = go(ctx, S.RECORD)
    pick_first(ctx, v)
    assert c.record_state.program.name == "Foo Edit" and "disabled" not in v.main_btn.state()
    v.main_btn.invoke()
    app.update()
    # 화면 B: 이 폴더들을 훑습니다
    assert c.record_state.room == rf.ROOM_FOLDERS and shown(ctx, v.rooms[rf.ROOM_FOLDERS]) and not shown(ctx, v.rooms[rf.ROOM_PICK])
    assert v.folders_head.cget("text") == T.REC_FOLDERS_HEAD.format(name="Foo Edit")
    row = tree_texts(v.folder_tree)[0]
    assert row[0] == T.CHECKED and row[1] == "높음" and "게시자(Acme Inc.)" in row[3] and row[2].startswith("%APPDATA%\\Acme")
    assert shown(ctx, v.steps_strip) and v.steps_strip.current == 1 and str(v.main_btn.cget("text")) == T.BTN_REC_START
    iid = v.folder_tree.get_children()[0]
    v._folder_toggle(iid)  # 체크 해제 = 제외
    app.update()
    assert tree_texts(v.folder_tree)[0][0] == T.UNCHECKED and "disabled" in v.main_btn.state() and v.reason_label.cget("text") == T.G_REC_NO_FOLDER
    v._folder_toggle(v.folder_tree.get_children()[0])
    ctx.picked["dir"] = str(f.pd)
    v.add_folder_btn.invoke()  # 폴더 직접 추가
    app.update()
    assert any(r[1] == T.REC_ADDED_CONF for r in tree_texts(v.folder_tree))
    c.record_toggle_folder(c.record_state.plan.folders[-1].token, False)  # 추가한 폴더는 다시 뺀다
    app.update()
    v.main_btn.invoke()  # 시작: 변경 전
    app.update()
    # 화면 C
    assert c.record_state.room == rf.ROOM_STEPS and v.steps_strip.current == 3 and shown(ctx, v.reset_btn)
    assert v.step_title.cget("text") == T.REC_STEP_TITLE.format(step=T.REC_STEP_NAMES[2]) and v.step_text.cget("text") == T.REC_TASK_BASELINE
    assert str(v.main_btn.cget("text")) == T.BTN_REC_BASELINE and "변경 전" in v.log_label.cget("text") and not v.alt_btn.winfo_manager()
    v.main_btn.invoke()
    app.update()
    assert v.steps_strip.current == 4 and v.step_text.cget("text") == T.REC_TASK_AFTER and str(v.main_btn.cget("text")) == T.BTN_REC_AFTER.format(n=1)
    f.put(ini, "a=2")
    f.put(z / "keys.xml", "k=2")
    v.main_btn.invoke()
    app.update()
    assert str(v.main_btn.cget("text")) == T.BTN_REC_CANDS and str(v.alt_btn.cget("text")) == T.BTN_REC_AGAIN and "변경 후 1번째" in v.log_label.cget("text")
    v.main_btn.invoke()  # 후보 보기
    app.update()
    # 화면 D
    assert c.record_state.room == rf.ROOM_CANDS and v.steps_strip.current == 5
    rows = tree_texts(v.cand_tree)
    assert {r[2].rsplit("\\", 1)[-1] for r in rows} == {"settings.ini", "keys.xml"} and all(r[0] == T.CHECKED for r in rows)
    assert v.cands_head.cget("text") == T.REC_CANDS_HEAD.format(name="Foo Edit", rounds=1, base=1) and shown(ctx, v.cand_hint)
    assert str(v.main_btn.cget("text")) == T.BTN_REC_DRAFT and "disabled" not in v.main_btn.state()
    # 모두 해제(표는 체크할 때마다 다시 그려지므로 줄 번호를 미리 잡아 두지 않고 컨트롤러로 해제한다)
    v._cand_toggle(v.cand_tree.get_children()[0])
    app.update()
    for p in list(ctx.c.record_state.checked):
        ctx.c.record_toggle_candidate(p, False)
    app.update()
    assert "disabled" in v.main_btn.state() and v.reason_label.cget("text") == T.G_REC_NONE_CHECKED
    v._cand_toggle(v.cand_tree.get_children()[0])
    app.update()
    assert "disabled" not in v.main_btn.state()
    v.main_btn.invoke()
    app.update()
    # 화면 E
    assert c.record_state.room == rf.ROOM_DRAFT and v.steps_strip.current == 6 and v.out_var.get().endswith("rule-draft.json")
    out = ctx.tmp / "outrules" / "foo.json"
    v.out_var.set(str(out))
    v.main_btn.invoke()
    app.update()
    assert out.exists() and "저장했습니다" in v.draft_result.cget("text") and shown(ctx, v.open_dir_btn) and "확인 필요" in v.draft_result.cget("text")
    rule = json.loads(out.read_text("utf-8"))
    assert USER not in out.read_text("utf-8") and rule["paths"]
    v.alt_btn.invoke()  # 후보로 돌아가기
    app.update()
    assert c.record_state.room == rf.ROOM_CANDS


def test_reset_button_confirm_dialog_keeps_files_as_bak(ctx):
    f, app, c = ctx.f, ctx.app, ctx.c
    f.put(f.app / "Acme" / "Foo Edit 2025" / "s.ini", "1")
    v = go(ctx, S.RECORD)
    pick_first(ctx, v)
    v.main_btn.invoke()
    app.update()
    v.main_btn.invoke()
    app.update()
    assert c.record_state.session is not None and (f.work / "snap-before.json").exists()
    ctx.answers["askyesno"] = False
    v.reset_btn.invoke()
    app.update()
    title, msg = ctx.asked[-1][0], ctx.asked[-1][1]
    assert title == T.REC_RESET_TITLE and "지우지 않고" in msg and ".bak" in msg
    assert c.record_state.session is not None and (f.work / "snap-before.json").exists()  # 아니요: 아무 일도 없다
    ctx.answers["askyesno"] = True
    v.reset_btn.invoke()
    app.update()
    assert c.record_state.session is None and c.record_state.room == rf.ROOM_PICK and not shown(ctx, v.reset_btn)
    assert (f.work / "snap-before.json.bak").exists() and not (f.work / "snap-before.json").exists()
    assert "보관했습니다(삭제하지 않음)" in v.pick_note.cget("text")


def test_no_folder_found_shows_notice_and_blocks_start(ctx):
    app = ctx.app
    v = go(ctx, S.RECORD)
    v.only_var.set(True)
    app.update()
    iid = [i for i in v.prog_tree.get_children() if "Zzyzx" in v.prog_tree.item(i, "values")[0]][0]
    v.prog_tree.selection_set(iid)
    app.update()
    v.main_btn.invoke()
    app.update()
    assert shown(ctx, v.no_folder) and v.no_folder.cget("text") == T.REC_NO_FOLDER and "disabled" in v.main_btn.state()
    assert "전체" in T.REC_NO_FOLDER and not hasattr(v, "all_roots_btn")  # 전체 훑기 단추는 없다
    v.alt_btn.invoke()  # 이전: 프로그램 고르기로
    app.update()
    assert ctx.c.record_state.room == rf.ROOM_PICK


def test_busy_disables_buttons_and_cancel_note(ctx):
    v = go(ctx, S.RECORD)
    pick_first(ctx, v)
    ctx.c.state.busy = True
    v._update_buttons()
    assert "disabled" in v.main_btn.state()
    ctx.c.state.busy = False
    v._update_buttons()
    assert "disabled" not in v.main_btn.state()
    ctx.app.on_event("cancelled", T.TASK_REC_SNAP)
    assert ctx.app._after_note_text == T.STOP_DONE_REC
