"""stage64: '찾을 파일 형식 설정' 창의 내보내기·가져오기·'기타' 화면 시험(부모 창은 숨긴 채, tkinter/디스플레이가 없으면 건너뜀).

파일 대화상자와 확인창은 가짜로 바꾼다(실제 대화상자 없음). 설정 폴더는 conftest 의 임시 폴더, 환경변수는 가짜(oldpcuser)로 고정한다.
"""
import json
from tkinter import filedialog

import pytest

from pcmigrate import find_exts as fx
from pcmigrate import find_exts_io as fio
from pcmigrate.gui import texts as T

from test_gui_find_exts_tk import ctx, open_dialog, texts_of  # noqa: F401  (ctx 는 같은 fixture)

JARGON = ("JSON", "misc", "added", "removed", "format", "version", "NaN")


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("USERNAME", "oldpcuser")
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\oldpcuser")


@pytest.fixture
def pick(monkeypatch):
    """파일 대화상자 대체: pick.save / pick.open 에 돌려줄 경로를 넣는다(빈 글은 '취소'). 호출 기록은 pick.calls."""
    state = type("P", (), {"save": "", "open": "", "calls": []})()
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda *a, **k: state.calls.append(("save", k)) or state.save)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda *a, **k: state.calls.append(("open", k)) or state.open)
    return state


def good(**kw):
    base = {"format": fio.FORMAT, "version": 1, "added": {".zep": "misc"}, "removed": [".svg"]}
    base.update(kw)
    return base


def wfile(tmp_path, obj, name="in.json"):
    p = tmp_path / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def reject_windows(dlg):
    from pcmigrate.gui.views.common import TextDialog
    return [w for w in dlg.winfo_children() if isinstance(w, TextDialog)]


def test_buttons_order_and_names(ctx):  # noqa: F811
    dlg = open_dialog(ctx)
    assert dlg.export_btn.cget("text") == "내보내기" and dlg.import_btn.cget("text") == "가져오기"
    assert (dlg.reset_btn.cget("text"), dlg.save_btn.cget("text"), dlg.cancel_btn.cget("text")) == ("기본값으로 초기화", "저장", "취소")
    bar = dlg.reset_btn.master
    left = [w for w in bar.pack_slaves() if str(w.pack_info()["side"]) == "left"]
    assert left == [dlg.reset_btn, dlg.export_btn, dlg.import_btn]  # 왼쪽: 초기화, 내보내기, 가져오기(Tab 순서)
    dlg.destroy()


def test_misc_group_always_visible_with_hint_and_category_choice(ctx):  # noqa: F811
    dlg = open_dialog(ctx)
    texts = texts_of(dlg)
    assert T.FX_CAT_TITLE.format(label="기타", n=0) in texts and T.FX_CAT_MISC_HINT in texts
    assert "기타" in dlg.cat_box.cget("values")
    dlg.ext_var.set("zep")
    dlg.cat_var.set("기타")
    dlg.add_btn.invoke()
    ctx.app.update()
    chip = dlg.chip_for(".zep")
    assert chip is not None and chip.label.cget("text") == "+ .zep"
    assert T.FX_CAT_TITLE.format(label="기타", n=1) in texts_of(dlg)
    dlg.save_btn.invoke()
    assert json.loads(fx.config_path().read_text(encoding="utf-8"))["added"] == {".zep": "misc"}


def test_export_flow(ctx, pick, tmp_path):  # noqa: F811
    fx.save(fx.ExtConfig({".zep": "misc"}, []))
    dlg = open_dialog(ctx)
    out = tmp_path / "e.json"
    pick.save = str(out)
    dlg.export_btn.invoke()
    ctx.app.update()
    kw = pick.calls[-1][1]
    assert kw["title"] == T.FX_EXPORT_TITLE and kw["initialfile"] == T.FX_EXPORT_DEFAULT_NAME
    assert json.loads(out.read_text(encoding="utf-8"))["added"] == {".zep": "misc"} and ctx.asked == []
    assert "내보냈습니다" in dlg.notice.cget("text") and T.FX_EXPORT_NOTE in dlg.notice.cget("text")
    assert "oldpcuser" not in out.read_text(encoding="utf-8")
    assert dlg.winfo_exists()
    # 파일 고르기를 취소하면 아무 일도 없다
    pick.save = ""
    out.unlink()
    dlg.export_btn.invoke()
    assert not out.exists()
    dlg.destroy()


def test_export_with_unsaved_edits_asks_and_can_stop(ctx, pick, tmp_path):  # noqa: F811
    dlg = open_dialog(ctx)
    dlg.ext_var.set("zep")
    dlg.add_btn.invoke()
    pick.save = str(tmp_path / "e.json")
    ctx.answers["askyesno"] = False
    dlg.export_btn.invoke()
    assert ctx.asked[-1] == (T.FX_UNSAVED_TITLE, T.FX_EXPORT_UNSAVED) and not (tmp_path / "e.json").exists()
    ctx.answers["askyesno"] = True
    dlg.export_btn.invoke()
    data = json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))
    assert data["added"] == {}  # 저장된 설정 기준: 저장 안 한 편집은 들어가지 않는다
    dlg.destroy()


def test_import_rejected_shows_lines_and_changes_nothing(ctx, pick, tmp_path):  # noqa: F811
    fx.save(fx.ExtConfig({".keep": "doc"}, []))
    before = fx.config_path().read_bytes()
    dlg = open_dialog(ctx)
    pick.open = wfile(tmp_path, good(added={".ok": "design", "bad ext": "doc", ".zep": "other"}), "bad.json")
    dlg.import_btn.invoke()
    ctx.app.update()
    wins = reject_windows(dlg)
    assert len(wins) == 1 and wins[0].title() == T.FX_IMPORT_REJECT_TITLE
    body = wins[0].text.get("1.0", "end")
    assert T.FX_IMPORT_REJECT_HEAD in body and T.FX_IMPORT_REJECT_TAIL in body and "번째 줄" in body and "공백" in body
    assert not any(w in body for w in JARGON) and not any(w in dlg.notice.cget("text") for w in JARGON)
    assert dlg.notice.cget("text") == T.FX_IMPORT_REJECT_SHORT
    assert ctx.asked == []  # 거부에는 확인창 없음
    assert fx.config_path().read_bytes() == before and dlg.chip_for(".keep") is not None and dlg.chip_for(".ok") is None
    wins[0].destroy()
    dlg.destroy()


def test_import_ok_confirm_yes_applies_and_redraws(ctx, pick, tmp_path):  # noqa: F811
    fx.save(fx.ExtConfig({".old": "doc"}, []))
    dlg = open_dialog(ctx)
    pick.open = wfile(tmp_path, good(added={".zep": "misc", ".exe": "doc"}))
    dlg.import_btn.invoke()
    ctx.app.update()
    title, msg = ctx.asked[-1]
    assert title == T.FX_IMPORT_CONFIRM_TITLE and msg.startswith(T.FX_IMPORT_CONFIRM_HEAD) and T.FX_IMPORT_CONFIRM_KEEP in msg and "프로그램 파일" in msg
    assert not any(w in msg for w in JARGON)
    assert dlg.chip_for(".old") is None and dlg.chip_for(".zep") is not None  # 적용 뒤 목록을 다시 그림
    assert dlg.status.cget("text") == T.FX_CLEAN  # 편집 시작점도 새 설정
    assert "가져왔습니다" in dlg.notice.cget("text")
    assert fx.load().added == {".zep": "misc", ".exe": "doc"} and fx.load().removed == [".svg"]
    assert list(fx.settings_dir().glob(fx.FILE_NAME + ".bak*"))  # 이전 설정 보관
    assert T.FX_CAT_TITLE.format(label="기타", n=1) in texts_of(dlg)
    dlg.destroy()


def test_import_ok_confirm_no_changes_nothing(ctx, pick, tmp_path):  # noqa: F811
    fx.save(fx.ExtConfig({".old": "doc"}, []))
    before = fx.config_path().read_bytes()
    dlg = open_dialog(ctx)
    pick.open = wfile(tmp_path, good())
    ctx.answers["askyesno"] = False
    dlg.import_btn.invoke()
    assert ctx.asked[-1][0] == T.FX_IMPORT_CONFIRM_TITLE and fx.config_path().read_bytes() == before and dlg.chip_for(".old") is not None
    dlg.destroy()


def test_import_same_content_only_notice(ctx, pick, tmp_path):  # noqa: F811
    fx.save(fx.ExtConfig({".zep": "misc"}, [".svg"]))
    dlg = open_dialog(ctx)
    pick.open = wfile(tmp_path, good())
    dlg.import_btn.invoke()
    assert ctx.asked == [] and "같아" in dlg.notice.cget("text")
    dlg.destroy()


def test_import_with_unsaved_edits_asks_before_file_dialog(ctx, pick, tmp_path):  # noqa: F811
    dlg = open_dialog(ctx)
    dlg.ext_var.set("zep")
    dlg.add_btn.invoke()
    pick.open = wfile(tmp_path, good())
    ctx.answers["askyesno"] = False
    dlg.import_btn.invoke()
    assert ctx.asked[-1] == (T.FX_UNSAVED_TITLE, T.FX_IMPORT_UNSAVED) and pick.calls == []  # 파일 대화상자도 열지 않는다
    ctx.answers["askyesno"] = True
    dlg.import_btn.invoke()
    assert [c[0] for c in pick.calls] == ["open"] and ctx.asked[-1][0] == T.FX_IMPORT_CONFIRM_TITLE
    assert fx.load().added == {".zep": "misc"}
    dlg.destroy()


def test_import_cancel_file_choice(ctx, pick):  # noqa: F811
    dlg = open_dialog(ctx)
    pick.open = ""
    dlg.import_btn.invoke()
    assert ctx.asked == [] and not reject_windows(dlg) and not fx.config_path().exists()
    dlg.destroy()


def test_import_apply_failure_shows_message_and_keeps_settings(ctx, pick, tmp_path, monkeypatch):  # noqa: F811
    import os
    fx.save(fx.ExtConfig({".old": "doc"}, []))
    before = fx.config_path().read_bytes()
    dlg = open_dialog(ctx)
    pick.open = wfile(tmp_path, good())
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("denied")))
        dlg.import_btn.invoke()
    ctx.app.update()
    assert T.FX_IMPORT_FAILED in dlg.notice.cget("text") and fx.config_path().read_bytes() == before and dlg.chip_for(".old") is not None
    dlg.destroy()


# ------------------------------------------------------------------ 찾기 화면의 '기타' 칩
def test_find_screen_misc_chip_hidden_without_misc_ext(ctx):  # noqa: F811
    v = ctx.view
    ctx.c.go(ctx.c.state and "find_data")
    ctx.app.update()
    flt_children = [w for w in v.filter_row.winfo_children()]
    assert [w.cget("text") for w in flt_children if w.winfo_class() == "TCheckbutton" and w.winfo_manager()] == [lbl for _c, lbl in T.FD_CATS]
    assert not v.misc_cb.winfo_manager()  # 기본 화면은 이전과 같다(칩 4개)


def test_find_screen_misc_chip_appears_when_saved_and_filters(ctx, tmp_path):  # noqa: F811
    from pcmigrate import find_data as fd
    v, c = ctx.view, ctx.c
    item = fd.FolderItem(path=str(tmp_path / "Drone"), kind="project", drive="C:", categories={"misc": {"files": 1, "bytes": 10}},
                         total_files=1, total_bytes=10)
    res = c.data_api.result
    res.projects, res.user_folders = [item], []
    c.find_data_start()
    ctx.app.update()
    assert not v.misc_cb.winfo_manager() and len(v.tree.get_children()) == 1  # 전체 보기에는 기타만 있는 폴더도 나온다
    fx.save(fx.ExtConfig({".zep": "misc"}, []))
    c.go("find_data")
    v.on_show()
    ctx.app.update()
    assert v.misc_cb.winfo_manager() and v.misc_cb.cget("text") == T.FX_FILTER_MISC
    v.filter_btns["design"].invoke()  # 설계를 끄면: 기타 칩이 켜져 있어 기타 폴더는 계속 보인다
    ctx.app.update()
    assert len(v.tree.get_children()) == 1
    v.misc_cb.invoke()  # 기타 칩을 끄면 가려진다
    ctx.app.update()
    assert len(v.tree.get_children()) == 0
    v.misc_cb.invoke()
    ctx.app.update()
    assert len(v.tree.get_children()) == 1
    fx.reset()  # 기타 형식이 없어지면 칩이 다시 사라진다
    v.on_show()
    ctx.app.update()
    assert not v.misc_cb.winfo_manager()
