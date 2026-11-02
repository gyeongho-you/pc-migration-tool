"""stage61: '찾을 파일 형식 설정' 창과 찾기 화면 링크의 tkinter 시험(부모 창은 숨긴 채). 확인창(messagebox)은 가짜로 바꾼다.
설정 폴더는 conftest 가 임시 폴더로 바꾼 find_exts.SETTINGS_DIR 만 쓴다. tkinter/디스플레이가 없으면 건너뛴다."""
import json
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import messagebox  # noqa: E402

from pcmigrate import find_data as fd  # noqa: E402
from pcmigrate import find_exts as fx  # noqa: E402
from pcmigrate.gui import state as S  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402
from pcmigrate.gui.app import App  # noqa: E402
from pcmigrate.gui.controller import Controller  # noqa: E402
from pcmigrate.gui.runner import Runner  # noqa: E402
from pcmigrate.gui.views.find_exts_dialog import ExtsDialog  # noqa: E402

from test_gui_controller import FakeApi  # noqa: E402
from test_gui_my_files_controller import FakeData, FakeLan  # noqa: E402


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    answers = {"askyesno": True}
    shown, asked = [], []

    def askyesno(title, msg, *a, **k):
        asked.append((title, msg))
        return answers["askyesno"]
    monkeypatch.setattr(messagebox, "askyesno", askyesno)
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: shown.append(("warn", a)))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(("info", a)))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(("error", a)))
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = FakeData(), FakeLan(tmp_path)
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
    c.go(S.FIND)
    app.update()
    yield SimpleNamespace(app=app, c=c, view=app.views[S.FIND], answers=answers, shown=shown, asked=asked)
    for w in list(app.winfo_children()):
        if isinstance(w, ExtsDialog):
            w.destroy()
    app.destroy()


def texts_of(widget):
    out = []
    for w in widget.winfo_children():
        for opt in ("text",):
            try:
                t = w.cget(opt)
                if isinstance(t, str) and t:
                    out.append(t)
            except tk.TclError:
                pass
        out += texts_of(w)
    return out


def open_dialog(ctx):
    ctx.view.exts_btn.invoke()
    ctx.app.update()
    dlgs = [w for w in ctx.view.winfo_children() if isinstance(w, ExtsDialog)]
    assert len(dlgs) == 1
    return dlgs[0]


def press_enter(dlg, ctx):
    """Enter: 입력칸에 바인딩이 있는지 확인한 뒤 키 이벤트를 보낸다. 숨긴 창에서는 키 이벤트가 전달되지 않아
    아무 일도 없었으면 바인딩이 부르는 동작(_add)을 직접 실행한다(바인딩 자체는 위에서 확인)."""
    assert dlg.entry.bind("<Return>"), "입력칸에 Enter 바인딩이 있어야 한다"
    dlg.entry.focus_force()
    dlg.update()
    before = dlg.status.cget("text")
    dlg.entry.event_generate("<Return>", when="now")
    ctx.app.update()
    if dlg.status.cget("text") == before and dlg.ext_var.get().strip():
        dlg._add()
        ctx.app.update()


def test_link_added_once_and_existing_links_unchanged(ctx):
    v = ctx.view
    assert v.exts_btn.cget("text") == T.FX_LINK == "찾을 파일 형식 설정(3DF Zephyr 같은 형식 추가) ▸"
    assert v.legacy_btn.cget("text") == T.FD_LEGACY_LINK and v.receive_btn.cget("text") == T.FD_RECEIVE_LINK
    kids = [w for w in v.links.winfo_children()]
    assert kids == [v.exts_btn, v.legacy_btn, v.receive_btn]  # 링크 한 줄만 추가, 순서 유지
    # 기존 찾기 화면의 다른 문구는 그대로
    assert v.find_btn.cget("text") == T.BTN_FD_FIND and not hasattr(v, "add_btn")
    assert v.junk_cb.cget("text") == T.FD_JUNK


def test_dialog_structure_and_buttons(ctx):
    dlg = open_dialog(ctx)
    assert dlg.title() == T.FX_TITLE and dlg.winfo_exists()
    texts = texts_of(dlg)
    d = ctx.c.exts_describe()
    for cat in d["categories"]:
        assert T.FX_CAT_TITLE.format(label=cat["label"], n=cat["count"]) in texts
    assert (dlg.reset_btn.cget("text"), dlg.save_btn.cget("text"), dlg.cancel_btn.cget("text")) == ("기본값으로 초기화", "저장", "취소")
    assert T.FX_CLEAN in texts and T.FX_INTRO in texts
    assert dlg.chip_for(".dwg") is not None and dlg.chip_for(".dwg").label.cget("text") == ".dwg"
    assert str(dlg.cat_box.cget("state")) == "readonly" and dlg.cat_var.get() == "설계"
    dlg.destroy()


def test_dialog_blocked_while_busy_shows_guard_message(ctx):
    ctx.c.state.busy = True
    ctx.view.exts_btn.invoke()
    ctx.app.update()
    assert ctx.shown and ctx.shown[0][0] == "warn"
    assert not [w for w in ctx.view.winfo_children() if isinstance(w, ExtsDialog)]
    ctx.c.state.busy = False


def test_add_enter_user_chip_and_save(ctx):
    dlg = open_dialog(ctx)
    dlg.ext_var.set("ZEP")
    press_enter(dlg, ctx)
    chip = dlg.chip_for(".zep")
    assert chip is not None and chip.label.cget("text") == "+ .zep"  # 사용자 추가 표시
    assert chip.cget("bg") != dlg.chip_for(".dwg").cget("bg")
    assert dlg.ext_var.get() == "" and ".zep" in dlg.notice.cget("text")
    assert T.FX_DIRTY.format(a=1, r=0) == dlg.status.cget("text")
    assert not fx.config_path().exists()  # 저장 전
    dlg.save_btn.invoke()
    ctx.app.update()
    assert not dlg.winfo_exists()
    assert json.loads(fx.config_path().read_text(encoding="utf-8"))["added"] == {".zep": "design"}
    assert T.FX_SAVED_NOTICE in ctx.view.notice.cget("text")  # 찾기 화면의 안내 한 줄
    assert ctx.asked == []  # 저장은 확인창 없이


def test_enter_with_empty_input_does_nothing_and_does_not_save(ctx):
    dlg = open_dialog(ctx)
    press_enter(dlg, ctx)
    assert dlg.winfo_exists() and not fx.config_path().exists()
    dlg.destroy()


def test_invalid_and_dangerous_input_messages(ctx):
    dlg = open_dialog(ctx)
    dlg.ext_var.set("*.dwg")
    dlg.add_btn.invoke()
    assert "와일드카드" in dlg.notice.cget("text") and dlg.ext_var.get() == "*.dwg" and not ctx.c.exts_dirty()
    dlg.ext_var.set(".exe")
    dlg.cat_var.set("문서")
    dlg.add_btn.invoke()
    assert "프로그램 파일" in dlg.notice.cget("text") and dlg.chip_for(".exe") is not None  # 경고만, 추가는 됨
    dlg.ext_var.set(".dat")
    dlg.add_btn.invoke()
    assert "프로그램 파일" not in dlg.notice.cget("text")  # 넓은 확장자는 경고하지 않는다
    dlg.destroy()


def test_remove_chip_restore_and_removed_row(ctx):
    dlg = open_dialog(ctx)
    dlg.chip_for(".svg").remove_btn.invoke()
    ctx.app.update()
    assert dlg.chip_for(".svg") is None and ".svg" in dlg.restore_btns
    assert any(".svg(사진)" in t for t in texts_of(dlg)) and T.FX_REMOVED in texts_of(dlg)
    assert T.FX_CAT_TITLE.format(label="사진", n=23) in texts_of(dlg)
    dlg.restore_btns[".svg"].invoke()
    ctx.app.update()
    assert dlg.chip_for(".svg") is not None and dlg.status.cget("text") == T.FX_CLEAN
    dlg.destroy()


def test_more_collapsible_for_long_category(ctx):
    dlg = open_dialog(ctx)
    n_design = len([e for e, (cat, _g) in fd.EXT_TABLE.items() if cat == "design"])
    assert n_design > 20
    assert any(t == T.FX_MORE.format(n=n_design - 20) for t in texts_of(dlg))
    assert dlg.chip_for(".shp") is not None  # 접힌 안에도 칩은 만들어져 있다
    dlg.destroy()


def test_reset_confirm_yes_and_no(ctx):
    ctx.c.exts_begin()
    ctx.c.exts_add(".aaa", "doc")
    ctx.c.exts_save()
    dlg = open_dialog(ctx)
    assert dlg.chip_for(".aaa") is not None
    ctx.answers["askyesno"] = False
    dlg.reset_btn.invoke()
    assert ctx.asked[-1] == (T.FX_RESET_TITLE, T.FX_RESET_ASK) and fx.config_path().exists() and dlg.chip_for(".aaa") is not None
    ctx.answers["askyesno"] = True
    dlg.reset_btn.invoke()
    ctx.app.update()
    assert not fx.config_path().exists() and dlg.chip_for(".aaa") is None and dlg.winfo_exists()  # 창은 열어 둔다
    assert "보관" in dlg.notice.cget("text")
    assert list(fx.settings_dir().glob(fx.FILE_NAME + ".bak*"))
    dlg.destroy()


def test_cancel_with_and_without_changes(ctx):
    dlg = open_dialog(ctx)
    dlg.cancel_btn.invoke()
    assert not dlg.winfo_exists() and ctx.asked == []  # 바뀐 것이 없으면 확인 없이 닫힘
    dlg = open_dialog(ctx)
    dlg.ext_var.set("zep")
    dlg.add_btn.invoke()
    ctx.answers["askyesno"] = False
    dlg.cancel_btn.invoke()
    assert dlg.winfo_exists() and ctx.asked[-1] == (T.FX_CLOSE_TITLE, T.FX_CLOSE_ASK)
    assert dlg.bind("<Escape>")  # Esc = 취소(같은 확인)
    dlg.event_generate("<Escape>", when="now")
    ctx.app.update()
    if len(ctx.asked) < 2:  # 숨긴 창에서는 키 이벤트가 전달되지 않을 수 있다: 바인딩이 부르는 동작을 직접 실행
        dlg._cancel()
    assert dlg.winfo_exists() and len(ctx.asked) == 2
    ctx.answers["askyesno"] = True
    dlg.cancel_btn.invoke()
    ctx.app.update()
    assert not dlg.winfo_exists() and not fx.config_path().exists()  # 저장하지 않고 닫음
    assert T.FX_SAVED_NOTICE not in ctx.view.notice.cget("text")


def test_window_close_button_same_as_cancel(ctx):
    dlg = open_dialog(ctx)
    dlg.ext_var.set("zep")
    dlg.add_btn.invoke()
    ctx.answers["askyesno"] = False
    dlg.protocol("WM_DELETE_WINDOW") and dlg.tk.call(dlg.protocol("WM_DELETE_WINDOW"))
    assert dlg.winfo_exists() and ctx.asked[-1][0] == T.FX_CLOSE_TITLE
    dlg.destroy()


def test_save_failure_keeps_window_and_shows_message(ctx, monkeypatch):
    import os
    dlg = open_dialog(ctx)
    dlg.ext_var.set("zep")
    dlg.add_btn.invoke()
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("denied")))
        dlg.save_btn.invoke()
    ctx.app.update()
    assert dlg.winfo_exists() and T.FX_SAVE_FAILED in dlg.notice.cget("text") and not fx.config_path().exists()
    dlg.destroy()


def test_damaged_settings_shows_notice_and_defaults(ctx):
    d = fx.settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / fx.FILE_NAME).write_text("{broken", encoding="utf-8")
    dlg = open_dialog(ctx)
    assert dlg.notice.cget("text") == T.FX_DAMAGED and dlg.chip_for(".dwg") is not None
    dlg.destroy()


def test_find_notice_shows_ext_notes_and_clears_after_new_find(ctx):
    v, c = ctx.view, ctx.c
    ctx.data = c.data_api
    res = c.data_api.result
    res.ext_notes = ["찾을 파일 형식 설정을 적용했습니다(추가 1개, 삭제 0개)."]
    c.find_data_start()
    ctx.app.update()
    assert "설정을 적용했습니다" in v.notice.cget("text")
    c.exts_begin()
    c.exts_add(".zep", "design")
    c.exts_save()
    ctx.app.update()
    assert T.FX_SAVED_NOTICE in v.notice.cget("text")
    c.find_data_start()  # 다시 찾으면 '다음에 반영' 안내는 사라진다
    ctx.app.update()
    assert T.FX_SAVED_NOTICE not in v.notice.cget("text") and "설정을 적용했습니다" in v.notice.cget("text")
    res.ext_notes = []
    c.find_data_start()
    ctx.app.update()
    assert v.notice.winfo_manager() == ""  # 형식 설정 안내가 없으면 안내 상자는 기존처럼 보이지 않는다
