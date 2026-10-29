"""19b: 화면이 처음 쓰는 사람에게 친화적인가 — 감사 보고서 U-1~U-18 항목별 시험(헤드리스 + tkinter).

tkinter/디스플레이가 없으면 tk 시험만 건너뛴다. 가짜 환경만 쓰고 실제 사용자 폴더에는 쓰지 않는다.
"""
import os
from pathlib import Path

import pytest

from pcmigrate.copier import FileResult, Report
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, DriveEntry, anonymize_paths, friendly_error
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi, plan, item  # noqa: E402

tk = pytest.importorskip("tkinter")
from pcmigrate import api as real_api  # noqa: E402
from pcmigrate.gui import app as app_mod  # noqa: E402
from pcmigrate.gui.app import RoadmapDialog, window_sizes  # noqa: E402
from pcmigrate.gui.views import common as C  # noqa: E402

from test_gui_smoke import ctx  # noqa: E402,F401


# =============================================================================== 헤드리스(컨트롤러/문구)

@pytest.fixture
def hc(tmp_path):
    api = FakeApi()
    return Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path,
                      make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])


def test_glossary_terms_are_defined_once_and_used(hc):  # U-5
    assert T.TERM_CHECK == "내용 확인" and "원본과 같은지" in T.TERM_CHECK_FULL
    assert T.TERM_AUTOLIST == "자동 목록" and T.TERM_BUNDLE == "설정 묶음 파일" and T.TERM_FULL_RECHECK.startswith("처음부터 모두 다시 확인")
    assert T.TR_STRICT == T.TERM_FULL_RECHECK
    assert T.TERM_BACKUP in T.SAFETY_LINES[1] and T.TERM_PREVIEW in T.SAFETY_LINES[1]
    assert "설정 묶음 파일" in T.TITLES[S.OLD_EXPORT] and "자동 목록" in T.TITLES[S.OLD_DISCOVER]
    assert T.friendly_detail("규칙 오류: x") == "자동 목록 오류: x" and T.friendly_detail("패키지 검증 실패") == "설정 묶음 파일 검증 실패"


def test_pc_particles_are_attached_and_polite_phrases_unified():  # U-15
    import re
    from pcmigrate.gui.texts import HELP, TITLES
    blob = "\n".join(t for n in dir(T) if n.isupper() for t in _flat(getattr(T, n)))
    assert not re.search(r"PC (에|로|의|를|가|는|도|와|만|까지|은|이)(?![가-힣])", blob)
    assert "하세요" not in blob and "고르세요" not in blob and "누르세요" not in blob
    assert HELP and TITLES


def _flat(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _flat(k)
            yield from _flat(x)
    elif isinstance(v, (tuple, list)):
        for x in v:
            yield from _flat(x)


def test_screen_texts_have_no_internal_names():  # U-5: 화면 문구에 해시/규칙/패키지/verify/내부 키 이름/파일명 확장자가 없다
    banned = ("해시", "규칙", "패키지", "엄격 검사", "verify", "skipped_link_paths", ".jsonl", ".pcmigrate-")
    bad = [(n, b) for n in dir(T) if n.isupper() and n != "DETAIL_REPLACEMENTS"
           for t in _flat(getattr(T, n)) for b in banned if b in t]
    assert bad == []


def test_start_texts_follow_the_three_situations():  # U-3
    titles = [" ".join(c[0].split()) for c in T.START_CHOICES]  # 제목 안의 줄바꿈(어색한 줄바꿈 방지용)은 공백으로 본다
    assert titles[0].startswith("① 쓰던 PC에서 준비하기") and titles[2].startswith("③ 새 PC에서 설치하고 가져오기")  # stage34: 실제 순서
    assert all(len(c[0].split("\n")) == 2 for c in T.START_CHOICES)  # 제목은 의도적으로 두 줄: (구 PC)/(신 PC)가 줄 중간에서 끊기지 않는다
    assert T.START_CHOICES[2][0].split("\n")[1] == "가져오기(신 PC)"
    assert titles[1].startswith("② 내 파일 옮기기") and "자동으로 찾아서" in titles[1]  # stage30a: 이름 변경(예전: 큰 파일 옮기기)
    assert T.START_ORDER_NOTE.startswith("처음이시면 ① → ② → ③ 순서로 진행해 주세요.") and "전체 이전 순서" in T.BTN_ROADMAP_BIG
    assert len(T.START_INTRO.splitlines()) <= 2  # U-6 소개는 2줄 이내


def test_confirm_needs_two_separate_checks_before_apply(hc):  # U-4
    c = hc
    c.set_package("p.zip")
    c.start_preview()
    c.set_preview_checked(True)
    assert c.can_apply() == (False, T.G_NEED_BAK_UNDERSTAND)
    c.confirm_final(True)  # 두 번째 확인이 없으면 확인 창의 '예'도 무효
    assert c.state.final_confirmed is False
    c.set_bak_understood(True)
    c.confirm_final(True)
    assert c.can_apply() == (True, "")
    c.set_package("q.zip")  # 설정 묶음 파일을 바꾸면 두 체크 모두 지워진다
    assert c.state.preview_checked is False and c.state.bak_understood is False


def test_unexpected_error_keeps_english_exception_name_only_in_detail():  # U-14
    fe = friendly_error(RuntimeError("boom"), "Traceback (most recent call last):\n  x")
    assert "RuntimeError" not in fe.what and "RuntimeError" not in fe.how and fe.what.endswith(".")
    assert fe.detail.startswith("RuntimeError: boom") and "Traceback" in fe.detail
    assert T.ERR_HOW == "이렇게 해 보세요" and T.BTN_SAVE_REPORT == "문제 보고서 저장"


def test_anonymize_paths_replaces_home_and_known_folders(monkeypatch, tmp_path):  # U-14
    home = tmp_path / "someone"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    text = f"오류: {home}\\Desktop\\a.zip 와 {str(home).replace(chr(92), '/')}/x 그리고 {home / 'AppData' / 'Local' / 't'}"
    out = anonymize_paths(text)
    assert str(home) not in out and "someone" not in out
    assert "%USERPROFILE%" in out and "%LOCALAPPDATA%" in out


def test_report_error_report_and_detail_are_anonymized(monkeypatch, tmp_path):  # U-14
    home = tmp_path / "me"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path, record_error=lambda e, w, tb=None: None)
    try:
        raise RuntimeError(f"{home}\\x")
    except RuntimeError as e:
        import traceback
        fe = c.report_error(e, "시험", traceback.format_exc())
    assert str(home) not in fe.report and str(home) not in fe.detail and "%USERPROFILE%" in fe.report


def test_drive_list_comes_from_controller_and_is_injectable(hc):  # U-8
    hc.drive_lister = lambda: [DriveEntry("E:\\", "removable", "100 GB"), DriveEntry("C:\\", "fixed", "10 GB")]
    drives = hc.list_drives()
    assert [d.root for d in drives] == ["E:\\", "C:\\"] and drives[0].kind == "removable"
    assert "외장" in T.DRIVE_KINDS["removable"]


def test_default_export_path_is_desktop_pcmigrate_settings_zip(hc, monkeypatch, tmp_path):  # U-8
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    assert hc.default_desktop() == str(home / "Desktop")
    assert hc.default_export_path() == str(home / "Desktop" / "pcmigrate-settings.zip")
    od = tmp_path / "od"
    (od / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("OneDrive", str(od))
    assert hc.default_desktop() == str(od / "Desktop")  # 바탕화면이 OneDrive 로 옮겨진 PC


def test_find_manifest_looks_in_folder_and_its_parent(hc, tmp_path):  # U-5(자동 찾기)
    from pcmigrate.manifest import DEFAULT_NAME
    share = tmp_path / "share"
    (share / "data").mkdir(parents=True)
    assert hc.find_manifest(str(share / "data")) == ""
    (share / DEFAULT_NAME).write_text("{}", encoding="utf-8")
    assert hc.find_manifest(str(share / "data")) == str(share / DEFAULT_NAME)  # 위 폴더에 있어도 찾는다
    assert hc.find_manifest("", str(tmp_path / "none")) == ""


def test_transfer_step_names_follow_state(hc, tmp_path):  # U-1 단계 표시줄
    c = hc
    src, dst = tmp_path / "s", tmp_path / "d"
    src.mkdir()
    names, cur = c.transfer_step()
    assert names == T.TR_STEP_NAMES == ("방식", "폴더", "미리보기", "복사", "결과") and cur == 2
    c.state.transfer_src, c.state.transfer_dst = str(src), str(dst)
    assert c.transfer_step()[1] == 3  # 폴더가 맞으면 미리보기 차례
    c.start_transfer(dry_run=True)
    assert c.transfer_step()[1] == 4  # 미리보기를 마치면 복사 차례
    c.start_transfer(dry_run=False, write_confirmed=True)
    assert c.transfer_step()[1] == 5
    c.set_transfer_flow(T.MODE_NETWORK, 2)
    names, cur = c.transfer_step()
    assert names == T.TR_VERIFY_STEP_NAMES and cur == 2


def test_overall_names_match_step_counts():  # U-1
    for scr, mode in ((S.OLD_SCAN, ""), (S.NEW_PREVIEW, ""), (S.TRANSFER, T.MODE_EXTERNAL), (S.TRANSFER, T.MODE_LOCAL)):
        n, total, _flow, _title = S.overall_step(scr, mode, 1)
        assert len(S.overall_names(scr, mode)) == total, (scr, mode)


def test_next_step_items_reuse_roadmap_keys(hc):  # U-13
    keys = {k for k, *_ in (*T.ROADMAP_OLD, *T.ROADMAP_NEW)}
    assert [k for k, _t in T.RES_NEXT_ITEMS] == ["new2", "new5", "old6"] and set(k for k, _ in T.RES_NEXT_ITEMS) <= keys
    assert hc.toggle_roadmap("new2") is True and "new2" in hc.roadmap_marks
    assert len(T.EXP_NEXT_STEPS) == 1 and T.EXP_NEXT_STEPS[0].startswith("다음은 ② 내 파일 옮기기에서 이 설정 묶음을 새 PC로 보내 주세요")  # stage34


def test_backup_folder_comes_from_applied_report(hc):  # U-4
    rep = Report(dry_run=False, results=[FileResult("e", "C:\\x\\a.txt", "copied", 1, "x", backup="C:\\x\\a.txt.bak")])
    hc.state.import_result = real_api.ImportResult(plan(), "applied", 0, rep)
    assert hc.backup_folder() == "C:\\x"
    hc.state.import_result = None
    assert hc.backup_folder() == ""


def test_window_sizes_scale_and_clamp_to_screen():  # U-11
    start, mini = window_sizes(1.0, (1920, 1080))
    assert start == app_mod.BASE_SIZE and mini == app_mod.MIN_SIZE
    start, mini = window_sizes(1.5, (1920, 1080))
    assert start[0] > app_mod.BASE_SIZE[0] and start[1] <= 1080 - app_mod.SCREEN_MARGIN[1]  # 화면 높이를 넘지 않는다
    assert mini[0] <= start[0] and mini[1] <= start[1]
    start, mini = window_sizes(1.0, (1366, 768))  # 작은 화면
    assert start[1] <= 768 - app_mod.SCREEN_MARGIN[1] and mini[1] <= start[1] and mini[0] <= start[0]


def test_clean_path_strips_quotes_spaces_and_invisible_marks():  # U-8
    assert C.clean_path('  "C:\\Users\\a b\\x"  ') == "C:\\Users\\a b\\x"
    assert C.clean_path("'D:\\자료'") == "D:\\자료" and C.clean_path("“E:\\x”") == "E:\\x"
    assert C.clean_path("\u202aC:\\x\u200e ") == "C:\\x"  # 속성 창에서 복사하면 붙는 방향 표시 문자
    assert C.clean_path("") == "" and C.clean_path('"') == '"'


def test_color_and_font_constants_are_central():  # 색 4가지, 글꼴 3단계
    assert {C.INFO_FG, C.OK_FG, C.WARN_FG, C.DANGER_FG} == {"#0b5394", "#1b6e2f", "#8a4b00", "#a40000"}
    assert C.WARN_BG != C.DANGER_BG and C.SECRET_BG == C.DANGER_BG and C.MUTED_FG == "#333333"
    assert (C.TITLE_DELTA, C.SUB_DELTA) == (5, 1) and T.FONT_SIZE + C.TITLE_DELTA == 16 and T.FONT_SIZE + C.SUB_DELTA == 12 and T.FONT_SIZE == 11


def test_text_icons_fallback_switch():  # U-16: 글꼴에 ✔✖⚠ 가 없을 때 환경변수로 글자 표시로 바꾼다
    import subprocess
    import sys
    code = "from pcmigrate.gui import texts as T; print(T.ICON_OK, T.ICON_FAIL, T.ICON_WARN, T.CHECKED, T.LICENSE_LINK)"
    env = {**os.environ, "PCMIGRATE_TEXT_ICONS": "1", "PYTHONPATH": str(Path(T.__file__).parents[2]), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert out.split()[:4] == ["[OK]", "[X]", "[!]", "[v]"] and out.split()[4] == "[!]"
    assert (T.ICON_OK, T.ICON_FAIL, T.ICON_WARN) == ("✔", "✖", "⚠")  # 기본은 그림 글자


# =============================================================================== tk

@pytest.fixture(autouse=True)
def _deiconified(request):
    """tk 시험은 창을 잠깐 화면에 올려 위치/크기를 정확히 잰다(끝나면 다시 숨긴다)."""
    if "ctx" not in request.fixturenames:
        yield
        return
    c = request.getfixturevalue("ctx")
    c.app.deiconify()
    c.app.update()
    yield
    try:
        c.app.withdraw()
    except tk.TclError:
        pass


def go(ctx_, screen):
    ctx_.c.go(screen)
    ctx_.app.update()
    return ctx_.app.views[screen]


def visible_inside(widget, container):
    """위젯이 container 창 영역 안에 완전히 들어와 있는가(화면 좌표 기준)."""
    widget.update_idletasks()
    return (widget.winfo_ismapped() and widget.winfo_rooty() >= container.winfo_rooty() - 1
            and widget.winfo_rooty() + widget.winfo_height() <= container.winfo_rooty() + container.winfo_height() + 1)


def test_start_screen_has_three_big_buttons_order_note_and_roadmap_button(ctx):  # U-3
    v = go(ctx, S.START)
    assert [b.cget("text") for b in v.buttons] == [c[2] for c in T.START_CHOICES] and len(v.buttons) == 3
    assert all(isinstance(b, C.ActionButton) and b.kind == "primary" for b in v.buttons)
    assert v.order_note.cget("text") == T.START_ORDER_NOTE
    assert v.roadmap_big.cget("text") == T.BTN_ROADMAP_BIG
    v.roadmap_big.invoke()
    assert isinstance(ctx.app.roadmap_dialog, RoadmapDialog)
    ctx.app.roadmap_dialog.destroy()
    # 라이선스 띠는 큰 버튼과 순서 안내 아래로 내려가고, 안전 원칙은 접혀 있다(화면당 상자는 하나)
    assert v.license_box.winfo_y() > v.buttons[0].winfo_y() and v.license_box.winfo_y() > v.roadmap_big.winfo_y()
    assert v.safety.open is False and not v.safety.body.winfo_manager()
    v.safety.toggle()
    ctx.app.update()
    assert v.safety.open and v.safety.body.winfo_manager() == "pack"
    assert not hasattr(v, "roadmap_btn") or not v.roadmap_btn.winfo_ismapped()  # 시작 화면은 큰 버튼 하나만 둔다


def test_transfer_inputs_and_main_button_are_visible_at_minimum_size(ctx, tmp_path):  # U-1
    app = ctx.app
    app.geometry(f"{app.min_size[0]}x{app.min_size[1]}")
    v = go(ctx, S.TRANSFER)
    (tmp_path / "s").mkdir(exist_ok=True)
    v.src_var.set(str(tmp_path / "s"))
    v.dst_var.set(str(tmp_path / "d"))
    app.update()
    canvas = v.scroll.canvas
    assert visible_inside(v.src_field.entry, canvas), "원본 입력칸이 첫 화면에 보여야 한다"
    assert visible_inside(v.dst_field.entry, canvas), "대상 입력칸이 첫 화면에 보여야 한다"
    assert visible_inside(v.dry_btn, app) and visible_inside(v.go_btn, app)  # 주 버튼은 스크롤 밖 하단 고정 막대
    assert v.dry_btn.master is v.bar_right and v._nav.winfo_rooty() > canvas.winfo_rooty()
    # '검사 방법/할 일/복사 전 확인'은 접이식에 있고, 처음에는 접혀 있다
    assert v.more.open is False and not v.more.body.winfo_manager()
    assert v.method_label.master is v.more.body and v.precheck_label.master is v.more.body and v.steps_label.master is v.more.body
    v.more.toggle()
    app.update()
    assert v.method_label.winfo_ismapped() and T.TR_PRECHECK_LINES[0] in v.precheck_label.cget("text")


def test_transfer_step_strip_shows_names_and_follows_progress(ctx, tmp_path):  # U-1
    v = go(ctx, S.TRANSFER)
    assert [c.cget("text").split()[-1] for c in v.steps_strip.chips] == list(T.TR_STEP_NAMES)
    assert v.steps_strip.current == 2  # 폴더를 아직 안 골랐다
    s, d = tmp_path / "s", tmp_path / "d"
    s.mkdir()
    v.src_var.set(str(s))
    v.dst_var.set(str(d))
    ctx.app.update()
    assert v.steps_strip.current == 3
    v._dry()
    ctx.app.update()
    assert v.steps_strip.current == 4 and v.go_btn.state() == ()  # 미리보기 뒤에는 복사가 차례
    assert v.go_btn._emph and not v.dry_btn._emph  # 지금 눌러야 할 단추만 파랑
    ctx.c.set_transfer_flow(T.MODE_NETWORK, 2)
    ctx.app.update()
    assert [c.cget("text").split()[-1] for c in v.steps_strip.chips] == list(T.TR_VERIFY_STEP_NAMES)


def test_verify_form_entries_are_wide_with_hint_on_its_own_line(ctx):  # U-2
    app = ctx.app
    app.geometry(f"{app.min_size[0]}x{app.min_size[1]}")
    ctx.c.set_transfer_flow(T.MODE_NETWORK, 2)
    v = go(ctx, S.TRANSFER)
    for field in (v.manifest_field, v.target_field):
        field.update_idletasks()
        assert field.entry.winfo_width() >= 300, field.entry.winfo_width()  # 예전에는 15px 로 찌그러졌다
        assert int(field.entry.cget("width")) >= 40
        assert field.hint.winfo_y() < field.entry.winfo_y() or field.hint.winfo_rooty() < field.entry.winfo_rooty()  # 힌트는 입력칸 같은 줄이 아니다
        assert field.hint.cget("wraplength") != 0
    assert not v.copy_form.winfo_ismapped() and v.verify_form.winfo_ismapped()


def test_manifest_autofind_button_fills_path_without_showing_file_names(ctx, tmp_path):  # U-5
    from pcmigrate.manifest import DEFAULT_NAME
    ctx.c.set_transfer_flow(T.MODE_NETWORK, 2)
    v = go(ctx, S.TRANSFER)
    share = tmp_path / "share"
    share.mkdir()
    v.target_var.set(str(share))
    assert [b.cget("text") for b in v.manifest_field.extra_btns] == [T.BTN_AUTOFIND]
    v.manifest_field.extra_btns[0].invoke()
    assert v.manifest_var.get() == "" and v.find_label.cget("text") == T.TR_AUTOFIND_NONE
    (share / DEFAULT_NAME).write_text("{}", encoding="utf-8")
    v.manifest_field.extra_btns[0].invoke()
    assert v.manifest_var.get() == str(share / DEFAULT_NAME) and v.find_label.cget("text") == T.TR_AUTOFIND_OK
    shown = "\n".join(C_text for C_text in _widget_texts(v))
    assert ".jsonl" not in shown and ".pcmigrate-" not in shown and "manifest" not in shown


def _widget_texts(widget):
    out = []
    for w in widget.winfo_children():
        try:
            out.append(str(w.cget("text")))
        except tk.TclError:
            pass
        out.extend(_widget_texts(w))
    return out


def test_pasted_paths_lose_quotes_and_spaces(ctx, tmp_path):  # U-8
    v = go(ctx, S.TRANSFER)
    src = tmp_path / "자료 폴더"
    src.mkdir()
    v.src_var.set(f'  "{src}"  ')
    v.src_field.normalize()
    assert v.src_var.get() == str(src)
    v.dst_var.set(f"'{tmp_path / 'out'}'")  # 포커스가 안 빠져도 상태에는 정리된 값이 들어간다
    v._sync_state()
    assert ctx.c.state.transfer_dst == str(tmp_path / "out")
    ctx.c.go(S.NEW_PACKAGE)
    pv = ctx.app.views[S.NEW_PACKAGE]
    pv.path_var.set('"C:\\pkg dir\\a.zip"')
    pv._next()
    assert ctx.c.state.package == "C:\\pkg dir\\a.zip"


def test_drive_combo_lists_removable_drives_and_fills_folder(ctx):  # U-8
    ctx.c.drive_lister = lambda: [DriveEntry("E:\\", "removable", "100.0 GB"), DriveEntry("D:\\", "fixed", "50.0 GB")]
    v = go(ctx, S.TRANSFER)
    box = v.dst_field
    box._fill_drives()
    values = list(box.drive_box.cget("values"))
    assert any("E:\\" in x and "외장" in x for x in values) and any("D:\\" in x for x in values)
    box.drive_var.set(values[0])
    box._picked()
    assert v.dst_var.get() == os.path.join("E:\\", T.DRIVE_FOLDER_DEFAULT)  # 외장 드라이브의 '이전자료' 폴더를 제안
    ctx.c.set_transfer_flow(T.MODE_NETWORK, 1)
    ctx.app.update()
    assert not v.dst_field.drive_box.winfo_ismapped()  # 공유 폴더 대상에는 드라이브 선택이 없다


def test_export_defaults_to_desktop_zip_and_has_desktop_button(ctx, monkeypatch, tmp_path):  # U-8
    home = tmp_path / "h"
    (home / "Desktop").mkdir(parents=True)
    for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    v = ctx.app.views[S.OLD_EXPORT]
    v.path_var.set("")
    go(ctx, S.OLD_EXPORT)
    assert v.path_var.get() == str(home / "Desktop" / "pcmigrate-settings.zip")
    v.path_var.set("D:\\x.zip")
    assert [b.cget("text") for b in v.path_field.extra_btns] == [T.BTN_MAKE_ON_DESKTOP]
    v.path_field.extra_btns[0].invoke()
    assert v.path_var.get() == str(home / "Desktop" / "pcmigrate-settings.zip")
    assert "탐색기 주소창" in v.path_field.hint.cget("text")  # 주소창 경로 복사 안내


def test_export_done_shows_big_next_steps_and_done_button(ctx, tmp_path):  # U-13, U-18
    v = ctx.app.views[S.OLD_EXPORT]
    go(ctx, S.OLD_EXPORT)
    assert not v.next_box.winfo_ismapped() and v.go_btn.cget("text") == T.BTN_EXPORT
    v.path_var.set(str(tmp_path / "p.zip"))
    v._export()
    ctx.app.update()
    assert v.next_box.winfo_ismapped() and v.go_btn.cget("text") == T.BTN_DONE  # 마지막 화면의 주 버튼은 '완료'
    shown = " ".join(l.cget("text") for l in v.next_lines)
    assert all(line in shown for line in T.EXP_NEXT_STEPS) and "새 PC" in shown
    assert "disabled" not in v.open_btn.state() and v.open_btn.cget("text") == T.BTN_OPEN_RESULT
    assert v.more.open is False  # 자세한 결과는 접혀 있다
    v.go_btn.invoke()
    assert ctx.c.state.screen == S.START
    go(ctx, S.OLD_EXPORT)  # 다시 들어오면 '완료'가 풀린다
    assert v.go_btn.cget("text") == T.BTN_EXPORT and not v.next_box.winfo_ismapped()


def test_confirm_screen_big_number_two_checks_and_warning_button(ctx):  # U-4
    c = ctx.c
    ctx.api.preview = real_api.ImportResult(plan([item(), item("would_overwrite", target="C:\\y", backup="C:\\y.bak")]), "dry-run", 0)
    c.set_package("C:\\pkg.zip")
    c.start_preview()
    cf = go(ctx, S.NEW_CONFIRM)
    assert cf.big_label.cget("text") == T.CONF_BIG.format(over=1) and cf.big_note.cget("text") == T.CONF_BIG_NOTE
    assert cf.run_btn.cget("text") == T.BTN_APPLY_N.format(n=1) and cf.run_btn.kind == "warn"
    assert cf.check.cget("text") == T.CONF_CHECK and cf.check_bak.cget("text") == T.CONF_CHECK_BAK  # 체크 둘로 분리
    assert "disabled" in cf.run_btn.state() and cf.why.cget("text") == T.G_NEED_CHECK_PREVIEW
    cf.checked_var.set(True)
    cf._checked_changed()
    assert "disabled" in cf.run_btn.state() and cf.why.cget("text") == T.G_NEED_BAK_UNDERSTAND
    cf.bak_var.set(True)
    cf._bak_changed()
    assert "disabled" not in cf.run_btn.state()
    assert cf.run_btn.cget("bg") == C.WARN_BTN_BG and cf.run_btn.cget("fg") == "white"  # 켜지면 주의색(주황) 단추
    assert cf.lic_link.cget("text") == T.LICENSE_LINK  # 라이선스는 한 줄 링크


def test_result_screen_next_checklist_backup_button_license_and_done(ctx, monkeypatch):  # U-4, U-7, U-13, U-18
    c = ctx.c
    rep = Report(dry_run=False, results=[FileResult("e", "C:\\x\\a.txt", "copied", 1, "x", backup="C:\\x\\a.txt.bak"),
                                         FileResult("e", "C:\\x\\b.txt", "copied", 1, "x")])
    c.state.import_result = real_api.ImportResult(plan(), "applied", 0, rep)
    v = go(ctx, S.NEW_RESULT)
    assert v.license_box.cget("text") == T.LICENSE_BANNER and v.next_btn.cget("text") == T.BTN_DONE  # 마지막 결과 화면의 큰 라이선스 안내 + '완료'
    assert set(v.next_vars) == {k for k, _t in T.RES_NEXT_ITEMS}
    assert not any(v.next_vars[k].get() for k in v.next_vars)
    box = v.box_checks[0]
    box.invoke()
    assert "new2" in c.roadmap_marks  # 전체 이전 순서의 체크와 같은 표시를 쓴다
    go(ctx, S.NEW_RESULT)
    assert v.next_vars["new2"].get() is True
    assert v.bak_btn.cget("text") == T.BTN_OPEN_BAK and "disabled" not in v.bak_btn.state()
    opened = []
    monkeypatch.setattr("pcmigrate.gui.views.new_pc.open_location", lambda p: opened.append(p))
    v.bak_btn.invoke()
    assert opened == ["C:\\x"]
    assert v.undo.open is False
    v.undo.toggle()
    assert T.RES_UNDO_HELP.split("\n")[0] in " ".join(_widget_texts(v.undo.body))
    v.next_btn.invoke()
    assert c.state.screen == S.START


def test_final_transfer_result_replaces_main_button_with_done(ctx, tmp_path):  # U-18
    v = go(ctx, S.TRANSFER)
    s, d = tmp_path / "s", tmp_path / "d"
    s.mkdir()
    v.src_var.set(str(s))
    v.dst_var.set(str(d))
    v._dry()
    assert not v.done_btn.winfo_ismapped()
    v._go()
    ctx.app.update()
    assert v.done_btn.winfo_ismapped() and v.done_btn.cget("text") == T.BTN_DONE and v.done_btn._emph
    assert not v.go_btn._emph and not v.dry_btn._emph
    v.done_btn.invoke()
    assert ctx.c.state.screen == S.START


def test_progress_bar_hidden_when_idle_and_when_card_is_shown(ctx):  # U-10, U-9
    app, c = ctx.app, ctx.c
    app.update()
    assert app.progress.winfo_manager() == "" and not app.progress_shown  # 유휴일 때는 하단 막대가 없다
    c.go(S.START)
    c.state.busy = True
    c.emit("busy", "시험 작업")
    app.update()
    assert app.progress.winfo_manager() == "pack" and app.status.winfo_manager() == "pack"  # 카드가 없는 화면에서는 하단에 보인다
    c.state.busy = False
    c.emit("busy", "")
    app.update()
    assert app.progress.winfo_manager() == "" and app.status.winfo_manager() == "" and app.stage.cget("text") == T.READY
    go(ctx, S.OLD_SCAN)
    c.status.begin(T.TASK_SCAN)
    c.state.busy = True
    c.emit("busy", T.TASK_SCAN)
    app.update()
    assert app.views[S.OLD_SCAN].card.shown and app.progress.winfo_manager() == ""  # 카드의 막대와 겹치지 않는다
    c.state.busy = False
    c.emit("busy", "")


def test_status_card_default_is_headline_bar_and_eta_with_details_folded(ctx):  # U-9
    v = go(ctx, S.OLD_SCAN)
    card = v.card
    ctx.c.status.begin(T.TASK_SCAN)
    card.refresh()
    ctx.app.update()
    assert card.shown and card.details.open is False and not card.details.body.winfo_manager()
    assert card.headline.winfo_ismapped() and card.bar.winfo_ismapped() and card.eta_label.winfo_ismapped()
    assert not card.step_labels[0].winfo_ismapped() and not card.problem.winfo_ismapped()  # 나머지는 '자세히 ▸' 안에 있다
    assert card.details.toggle_btn.cget("text") == T.CARD_DETAILS_SHOW
    card.details.toggle()
    ctx.app.update()
    assert card.details.toggle_btn.cget("text") == T.CARD_DETAILS_HIDE and card.step_labels[0].winfo_ismapped()


def test_escape_never_goes_back_but_closes_dialogs_and_cancels(ctx):  # U-12
    app, c = ctx.app, ctx.c
    c.go(S.OLD_DISCOVER)
    app.update()
    app.focus_get = lambda: None
    assert app._on_escape(None) is None and c.state.screen == S.OLD_DISCOVER
    dlg = app.show_help(S.OLD_DISCOVER)
    dlg.update()
    assert dlg.bind("<Escape>") and dlg.close_event() == "break"
    assert not dlg.winfo_exists()  # 대화상자는 Esc 로 닫힌다
    c.state.busy, c.can_cancel = True, True
    c.cancel_token = real_api.CancelToken()
    assert app._on_escape(None) == "break" and c.cancel_token.is_set()  # 작업 취소
    c.state.busy = False


def test_font_size_buttons_resize_and_respect_limits(ctx):  # U-11
    from tkinter import font as tkfont
    app = ctx.app
    base = tkfont.nametofont("TkDefaultFont").cget("size")
    assert base == T.FONT_SIZE == 11 and tkfont.nametofont(C.TITLE_FONT).cget("size") == 16 and tkfont.nametofont(C.SUB_FONT).cget("size") == 12
    assert app.change_font(1) == 12
    assert tkfont.nametofont("TkDefaultFont").cget("size") == 12 and tkfont.nametofont(C.TITLE_FONT).cget("size") == 17
    for _ in range(20):
        app.change_font(1)
    assert app.font_delta == C.FONT_DELTA_RANGE[1] and "disabled" in app.font_up.state()
    for _ in range(30):
        app.change_font(-1)
    assert app.font_delta == C.FONT_DELTA_RANGE[0] and "disabled" in app.font_down.state()
    app.change_font(app.font_delta * -1)
    assert tkfont.nametofont("TkDefaultFont").cget("size") == 11
    app.update()
    assert (app.font_up.cget("text"), app.font_down.cget("text")) == ("A+", "A-")


def test_scroll_body_shows_more_below_indicator(ctx):  # U-11
    app = ctx.app
    holder = tk.Frame(app, width=300, height=120)
    holder.pack_propagate(False)
    sb = C.ScrollBody(holder)
    sb.pack(fill="both", expand=True)
    for i in range(30):
        tk.Label(sb.inner, text=f"줄 {i}").pack(anchor="w")
    holder.place(x=0, y=0)
    app.update()
    app.update()
    assert sb.can_scroll() and sb.more_visible and sb.more.cget("text") == T.MORE_BELOW
    sb.canvas.yview_moveto(1.0)
    app.update()
    assert sb.at_bottom() and not sb.more_visible
    holder.destroy()


def test_tab_focus_scrolls_hidden_widget_into_view(ctx):  # U-11, U-16: 키보드만 쓰는 사람도 아래 입력칸에 닿는다
    app = ctx.app
    holder = tk.Frame(app, width=300, height=120)
    holder.pack_propagate(False)
    sb = C.ScrollBody(holder)
    sb.pack(fill="both", expand=True)
    entries = []
    for i in range(30):
        e = tk.Entry(sb.inner)
        e.pack(anchor="w")
        entries.append(e)
    holder.place(x=0, y=0)
    app.update()
    app.update()
    last = entries[-1]
    assert last.winfo_rooty() > sb.canvas.winfo_rooty() + sb.canvas.winfo_height()  # 처음에는 가려져 있다
    last.focus_force()
    app.update()
    assert last.winfo_rooty() + last.winfo_height() <= sb.canvas.winfo_rooty() + sb.canvas.winfo_height() + 2
    entries[0].focus_force()
    app.update()
    assert entries[0].winfo_rooty() >= sb.canvas.winfo_rooty() - 1  # 다시 위로 돌아온다
    holder.destroy()


def test_roadmap_dialog_has_title_progress_and_close_button(ctx):  # U-17
    app, c = ctx.app, ctx.c
    dlg = app.show_roadmap()
    dlg.update()
    total = len(T.ROADMAP_OLD) + len(T.ROADMAP_NEW)
    assert total == 12 and dlg.heading.cget("text") == T.ROADMAP_HEADING
    assert dlg.progress_label.cget("text") == T.ROADMAP_PROGRESS.format(n=0, total=12) == "진행률 0/12"
    dlg.vars["old1"].set(True)
    dlg._toggle("old1")
    assert dlg.progress_label.cget("text") == "진행률 1/12" and "old1" in c.roadmap_marks
    assert dlg.close_btn.cget("text") == T.BTN_CLOSE
    dlg.close_btn.invoke()
    assert not dlg.winfo_exists()


def test_error_dialog_body_has_no_exception_name_and_can_save_report(ctx, tmp_path, monkeypatch):  # U-14
    home = tmp_path / "kim"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    try:
        raise RuntimeError(f"실패 {home}\\a")
    except RuntimeError as e:
        import traceback
        fe = ctx.c.report_error(e, "시험", traceback.format_exc())
    dlg = ctx.app.show_error(fe)
    dlg.update()
    assert "RuntimeError" not in dlg.what_label.cget("text") and "RuntimeError" in dlg.detail.get("1.0", "end")
    assert dlg.how_label.cget("text") == fe.how and dlg.save_btn.cget("text") == T.BTN_SAVE_REPORT
    assert dlg.detail.winfo_manager() == ""  # 상세 보기는 접혀 있다
    out = tmp_path / "report.txt"
    assert dlg.save_report(str(out)) == str(out)
    text = out.read_text(encoding="utf-8")
    assert "%USERPROFILE%" in text and str(home) not in text and "RuntimeError" in text  # 개인 경로는 치환
    assert dlg.log_note.cget("text") == T.ERROR_REPORT_SAVED
    dlg.copy_error()
    assert str(home) not in ctx.app.clipboard_get()
    dlg.destroy()


def test_nav_buttons_have_alt_shortcuts_and_are_distinct(ctx):  # U-16, U-18
    app = ctx.app
    v = go(ctx, S.OLD_DISCOVER)
    assert v.home_btn.cget("text") == T.BTN_HOME and v.back_btn.cget("text") == T.BTN_BACK and v.home_btn.cget("text") != v.back_btn.cget("text")
    for b, key in ((v.home_btn, "h"), (v.back_btn, "b"), (v.next_btn, "n")):
        assert int(b.cget("underline")) >= 0 and b.cget("text")[int(b.cget("underline"))].lower() == key
        assert app.bind(f"<Alt-{key}>")
    assert isinstance(v.next_btn, C.ActionButton) and v.next_btn.pack_info()["side"] == "right"  # 주 버튼은 오른쪽 끝
    v.next_btn.invoke()
    assert ctx.c.state.screen == S.OLD_CHECKLIST
    app.show(S.OLD_DISCOVER)


def test_discover_table_has_space_hint_and_accessible_muted_color(ctx):  # U-16
    v = go(ctx, S.OLD_DISCOVER)
    texts = _widget_texts(v)
    assert T.SPACE_HINT in texts
    assert C.MUTED_FG == "#333333" and "Space" in T.SPACE_HINT


def test_license_is_big_only_on_start_and_result_otherwise_a_link(ctx):  # U-7
    big = []
    for scr in (S.START, S.OLD_SCAN, S.OLD_DISCOVER, S.OLD_CHECKLIST, S.OLD_EXPORT, S.NEW_PACKAGE, S.NEW_PREVIEW, S.NEW_CONFIRM,
                S.NEW_RESULT, S.TRANSFER):
        v = go(ctx, scr)
        if any(T.LICENSE_NOTE in t for t in _widget_texts(v)):
            big.append(scr)
    assert set(big) == {S.START, S.NEW_RESULT}
    for scr in (S.OLD_EXPORT, S.NEW_PREVIEW, S.NEW_CONFIRM):
        assert T.LICENSE_LINK in _widget_texts(ctx.app.views[scr])


def test_preview_warnings_are_folded_and_one_box_per_screen(ctx):  # U-6
    ctx.c.set_package("p.zip")
    ctx.c.start_preview()
    v = go(ctx, S.NEW_PREVIEW)
    assert v.more.open is False and not v.more.body.winfo_manager()
    assert v.problem_box.cget("text") == T.PREV_OK


def test_view_modules_use_central_colors_and_fonts_not_inline_values():  # 인라인 색/글꼴 정리
    import re
    root = Path(app_mod.__file__).parent
    offenders = []
    for path in [*root.glob("*.py"), *(root / "views").glob("*.py")]:
        if path.name in ("common.py", "texts.py"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"#[0-9a-fA-F]{6}\b", code) or re.search(r'font=\("TkDefaultFont"', code):
                offenders.append((path.name, n, line.strip()[:70]))
    assert offenders == []
