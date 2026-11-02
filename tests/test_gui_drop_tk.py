"""stage63: 직접 고른 파일·폴더 상자(1/4 화면)와 끌어다 놓기 연결의 tkinter 시험(창을 숨긴 채, 실제 창에 후킹을 걸지 않음).

끌어다 놓기는 경로 수신 함수(_on_drop / DropHook.deliver)를 직접 불러 시험하고, 후킹 설치는 가짜 Windows API 로만 시험한다.
설치 실패/관리자 권한이면 조용히 꺼지고 버튼은 계속 동작해야 한다. 디스플레이가 없으면 건너뛴다.
"""
import os
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import filedialog, messagebox  # noqa: E402

from pcmigrate.gui import dnd_win, state as S, texts as T  # noqa: E402
from pcmigrate.gui.app import App  # noqa: E402
from pcmigrate.gui.controller import Controller  # noqa: E402
from pcmigrate.gui.runner import Runner  # noqa: E402

from test_gui_controller import FakeApi  # noqa: E402
from test_gui_drop_controller import DropData  # noqa: E402


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api = DropData()
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
    work = tmp_path / "pc"
    for rel, n in (("A/a1.txt", 5), ("B/b1.txt", 7), ("loose.txt", 8)):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(b"x" * n)
    yield SimpleNamespace(app=app, c=c, work=work, tmp=tmp_path)
    app.destroy()


def find_view(ctx):
    ctx.c.go(S.FIND)
    ctx.app.update()
    return ctx.app.views[S.FIND]


def texts_in(widget):
    out = []
    for w in widget.winfo_children():
        try:
            out.append(str(w.cget("text")))
        except tk.TclError:
            pass
        out += texts_in(w)
    return out


def tree_rows(tree):
    return [tree.item(i, "values") for i in tree.get_children()]


def test_box_exists_with_buttons_and_hint_and_next_disabled_when_empty(ctx):
    v = find_view(ctx)
    assert v.drop_box.cget("text") == T.DP_TITLE
    assert str(v.drop_files_btn.cget("text")) == T.BTN_DP_FILES and str(v.drop_folder_btn.cget("text")) == T.BTN_DP_FOLDER
    assert v.drop_box.cget("text") == "직접 추가한 파일·폴더" and not hasattr(v, "add_btn")  # 위쪽 '직접 폴더 추가' 버튼은 없다
    assert T.DP_HINT in texts_in(v.drop_box) and "직접 추가한 파일·폴더는 확장자 필터링을 거치지 않습니다." in texts_in(v.drop_box)
    assert not v.drop_tree_frame.winfo_manager()
    top = [str(w.cget("text")) for w in v.top_row.winfo_children()]
    assert top == [T.BTN_FD_FIND]  # 위쪽은 '내 파일 찾기' 자동 목록 전용
    assert "disabled" in v.next_btn.state()


def test_drop_receive_function_adds_rows_enables_next_and_shows_total(ctx):
    v = find_view(ctx)
    v._on_drop([str(ctx.work / "loose.txt"), str(ctx.work / "B" / "b1.txt")])
    ctx.app.update()
    rows = tree_rows(v.drop_tree)
    assert len(rows) == 2 and rows[1][0].endswith("b1.txt")
    assert "disabled" not in v.next_btn.state()
    assert "직접 추가한 2개" in v.total.cget("text")
    assert T.DP_ADDED.format(n=2) in v.drop_msg.cget("text")


def test_drop_with_no_paths_explains_virtual_items(ctx):
    v = find_view(ctx)
    v._on_drop([])
    assert T.DP_VIRTUAL in v.drop_msg.cget("text") and ctx.c.drop_total()[0] == 0


def test_drop_ignored_when_screen_not_shown(ctx):
    v = find_view(ctx)
    ctx.c.go(S.START)
    ctx.app.update()
    v._on_drop([str(ctx.work / "loose.txt")])
    assert ctx.c.drop_total()[0] == 0


def test_overlap_note_shown_for_files_inside_checked_folder(ctx):
    v = find_view(ctx)
    ctx.c.add_data_folder(str(ctx.work / "A"))
    v._on_drop([str(ctx.work / "A" / "a1.txt")])
    ctx.app.update()
    assert T.DP_OVERLAP.format(n=1) in v.drop_msg.cget("text") and T.DP_TAG_COVERED in tree_rows(v.drop_tree)[0][0]


def test_buttons_work_via_dialogs_and_remove_and_clear(ctx, monkeypatch):
    v = find_view(ctx)
    monkeypatch.setattr(filedialog, "askopenfilenames", lambda **k: (str(ctx.work / "loose.txt").replace("\\", "/"),))
    v.drop_files_btn.invoke()
    monkeypatch.setattr(filedialog, "askdirectory", lambda **k: str(ctx.work / "B").replace("\\", "/"))
    v.drop_folder_btn.invoke()
    ctx.app.update()
    rows = tree_rows(v.drop_tree)
    assert len(rows) == 2 and T.DP_TAG_FOLDER in rows[1][0] and rows[1][1] == "-"  # 파일과 폴더가 한 목록에('(폴더)' 꼬리표)
    assert ctx.c.data_selection() == []  # 위쪽 자동 목록에는 들어가지 않는다
    v.drop_tree.selection_set(v.drop_tree.get_children()[0])
    v.drop_remove_btn.invoke()
    ctx.app.update()
    assert len(tree_rows(v.drop_tree)) == 1
    v.drop_clear_btn.invoke()
    ctx.app.update()
    assert not tree_rows(v.drop_tree) and not v.drop_tree_frame.winfo_manager()


def test_too_many_shows_message_and_adds_nothing(ctx, monkeypatch):
    from pcmigrate import drop_stage as ds
    monkeypatch.setattr(ds, "MAX_DROP_FILES", 1)
    v = find_view(ctx)
    v._on_drop([str(ctx.work / "loose.txt"), str(ctx.work / "B" / "b1.txt")])
    assert "너무 많습니다" in v.drop_msg.cget("text") and ctx.c.drop_total()[0] == 0


def test_method_screen_subtitle_with_drops_only(ctx):
    v = find_view(ctx)
    v._on_drop([str(ctx.work / "loose.txt")])
    ctx.c.go(S.METHOD)
    ctx.app.update()
    assert "직접 추가한 1개" in ctx.app.views[S.METHOD].sub.cget("text")


# ----------------------------------------------------------------------------- 끌어다 놓기 연결(가짜 API)
def fake_api(log, fail_at=None):
    def accept(hwnd, on):
        log.append(("accept", on))

    def subclass(hwnd, cb):
        if fail_at == "subclass":
            raise OSError("boom")
        log.append(("subclass", hwnd))
        return 1000 + len(log)

    return {"wndproc_type": lambda f: f, "accept": accept, "subclass": subclass, "restore": lambda h, o: log.append(("restore", o)),
            "query": lambda h: ["C:\\a.txt"], "call_orig": lambda *a: 7}


def test_hook_installs_with_fake_api_delivers_and_passes_other_messages(ctx):
    got, log = [], []
    hook = dnd_win.DropHook(ctx.app, got.append, is_admin_fn=lambda: False, api_factory=lambda: fake_api(log))
    assert hook.install() and hook.installed and hook.reason == ""
    assert any(k == "subclass" for k, _ in log)
    hwnd = next(iter(hook._orig))
    assert hook._proc(hwnd, 0x0001, 0, 0) == 7  # 다른 메시지는 원래 프로시저로
    assert hook._proc(hwnd, dnd_win.WM_DROPFILES, 5, 0) == 0
    ctx.app.update()
    time.sleep(0.05)
    ctx.app.update()
    assert got == [["C:\\a.txt"]]
    hook.uninstall()
    assert not hook.installed and any(k == "restore" for k, _ in log)


def test_hook_failure_is_silent_and_rolls_back(ctx):
    log = []
    hook = dnd_win.DropHook(ctx.app, lambda p: None, is_admin_fn=lambda: False, api_factory=lambda: fake_api(log, fail_at="subclass"))
    assert hook.install() is False and not hook.installed and hook.reason == dnd_win.REASON_ERROR


def test_hook_api_load_error_is_silent(ctx):
    def boom():
        raise OSError("no api")
    hook = dnd_win.DropHook(ctx.app, lambda p: None, is_admin_fn=lambda: False, api_factory=boom)
    assert hook.install() is False and hook.reason == dnd_win.REASON_ERROR


def test_hook_not_installed_when_admin(ctx):
    log = []
    hook = dnd_win.DropHook(ctx.app, lambda p: None, is_admin_fn=lambda: True, api_factory=lambda: fake_api(log))
    assert hook.install() is False and hook.reason == dnd_win.REASON_ADMIN and log == []


def test_hook_deliver_swallows_handler_errors(ctx):
    def bad(paths):
        raise RuntimeError("x")
    dnd_win.DropHook(ctx.app, bad).deliver(["C:\\a"])  # 예외가 새지 않는다


def test_view_shows_admin_and_nodrop_hints_but_buttons_still_work(ctx, monkeypatch):
    v = find_view(ctx)
    v.hook.reason = dnd_win.REASON_ADMIN
    v._drop_sync()
    assert T.DP_HINT_ADMIN in texts_in(v.drop_box)
    v.hook.reason = dnd_win.REASON_ERROR
    v._drop_sync()
    assert T.DP_HINT_NO_DROP in texts_in(v.drop_box)
    assert "disabled" not in v.drop_files_btn.state()
    monkeypatch.setattr(filedialog, "askopenfilenames", lambda **k: (str(ctx.work / "loose.txt"),))
    v.drop_files_btn.invoke()
    assert ctx.c.drop_total()[0] == 1


def test_view_hook_install_failure_leaves_screen_working(ctx, monkeypatch):
    from pcmigrate.gui.views.find import FindView
    monkeypatch.setattr(FindView, "install_drop_hook", True)
    monkeypatch.setattr(dnd_win, "_is_admin", lambda: False)

    def boom():
        raise OSError("no api")
    monkeypatch.setattr(dnd_win, "_load_api", boom)
    v = find_view(ctx)
    v._install_hook()
    assert v.hook.reason == dnd_win.REASON_ERROR and T.DP_HINT_NO_DROP in texts_in(v.drop_box)
    v._on_drop([str(ctx.work / "loose.txt")])
    assert ctx.c.drop_total()[0] == 1


def test_transfer_screen_shows_drop_result_lines_after_copy(ctx):
    v = find_view(ctx)
    v._on_drop([str(ctx.work / "loose.txt"), str(ctx.work / "B" / "b1.txt")])
    ctx.c.set_method(T.MV_EXTERNAL)
    ctx.c.files_continue()
    ctx.app.update()
    tv = ctx.app.views[S.TRANSFER]
    assert "직접 추가한 2개" in tv.sel_box.cget("text") or "직접 추가한 2개" in " ".join(texts_in(tv))
    ctx.c.state.transfer_dst = str(ctx.tmp / "out")
    ctx.c.start_transfer(dry_run=True)
    ctx.app.update()
    assert T.DP_RESULT.format(n=2, ok=2, skip=2, bad=1) in tv.preview_box.cget("text")


def test_dropping_folder_adds_folder_row_to_the_box_list(ctx):
    v = find_view(ctx)
    v._on_drop([str(ctx.work / "B"), str(ctx.work / "loose.txt")])
    ctx.app.update()
    rows = tree_rows(v.drop_tree)
    assert len(rows) == 2 and T.DP_TAG_FOLDER in rows[0][0]  # 끌어다 놓은 폴더도 상자 목록에 '(폴더)' 줄로
    assert v.rows == {} and ctx.c.data_selection() == []  # 위쪽 자동 폴더 표는 건드리지 않는다
    assert [i.path for i in ctx.c.data_selected_items()] == [str(ctx.work / "B")]  # 전송에서는 폴더째


def test_overlap_tag_and_note_shown_for_folder_inside_checked_auto_folder(ctx):
    from test_gui_my_files_controller import find_result  # noqa: F401
    v = find_view(ctx)
    ctx.c.add_data_folder(str(ctx.work / "A"))  # 체크된 자동 폴더(옛 API 로 체크 상태를 만든다)
    (ctx.work / "A" / "sub").mkdir(exist_ok=True)
    v._on_drop([str(ctx.work / "A" / "sub"), str(ctx.work / "A" / "a1.txt")])
    ctx.app.update()
    rows = tree_rows(v.drop_tree)
    assert all(T.DP_TAG_COVERED in r[0] for r in rows)
    assert T.DP_OVERLAP.format(n=2) in v.drop_msg.cget("text")
