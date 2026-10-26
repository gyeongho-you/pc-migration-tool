"""19 통합: denied(보안상 복원 안 함) 표시, 체크리스트 안내, 내보내기 오류 문구, 정규식(\\b) 수정."""
import re

import pytest

from pcmigrate import api as real_api
from pcmigrate import checklist, discover
from pcmigrate.copier import FileResult, Report
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, friendly_error
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi, plan, item  # noqa: E402


@pytest.fixture
def c(tmp_path):
    api = FakeApi()
    api.preview = real_api.ImportResult(
        plan([item(), item("denied", target="C:\\x\\run.exe"), item("denied", target="C:\\x\\a.dll")]), "dry-run", 0)
    return Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path, make_items=lambda d: []), api


def test_denied_status_label_note_and_counts_as_skipped(c):
    ctl, api = c
    assert T.PREV_STATUS["denied"] == "복원 안 함(보안)"
    ctl.set_package("p.zip")
    ctl.start_preview()
    assert [r["status"] for r in ctl.preview_rows()].count("복원 안 함(보안)") == 2
    note = ctl.denied_note()
    assert "보안상 복원하지 않는 형식 2개" in note and "나머지는 복원됩니다" in note
    rep = Report(dry_run=False, results=[FileResult("e", "C:\\x\\a.txt", "copied", 1, "x"),
                                         FileResult("e", "C:\\x\\run.exe", "denied", 1, "x", error="복원 안 함: x"),
                                         FileResult("e", "C:\\x\\b.txt", "skipped_identical", 1, "x")])
    ctl.state.preview = None
    ctl.state.import_result = real_api.ImportResult(api.preview.plan, "applied", 0, rep)
    assert ctl.import_counts() == (1, 2, 0)  # denied 는 실패가 아니라 건너뜀
    assert "보안상 복원하지 않는 형식 1개" in ctl.denied_note()


def test_checklist_guide_uses_winget_extract_and_pc_particles():
    blob = "\n".join(T.CL_GUIDE)
    assert "pcmigrate winget-extract" in blob and "winget import -i" in blob and "programs/winget.json" not in blob
    assert T.CL_HIDDEN_MARK == "(이 PC에서 감지되지 않음)"
    cl = checklist.build_checklist([("Demo", ["라이선스 재로그인"])], [], [])
    text = checklist.render(cl, "md")
    assert "PC 에서" not in text and "PC에서" in text


def test_export_lock_and_part_failed_errors_are_friendly():
    lock = real_api.ApiError("내보내기 실패: 같은 출력 파일(a.zip)로 내보내기가 이미 실행 중입니다(PID 12). 끝나길 기다리거나 다른 출력 이름을 쓰세요.")
    fe = friendly_error(lock)
    assert "이미 실행 중" in fe.what and "a.zip" in fe.what and "PID" not in fe.what and "다른 파일 이름" in fe.how
    bad = real_api.ApiError("내보내기 실패: 만든 패키지를 다시 검사했더니 문제가 있어 저장하지 않았습니다(원본은 그대로입니다). "
                            "문제 확인용으로 임시 파일을 C:/out/a.zip.part.failed 로 보존했습니다. 같은 설정으로 다시 내보내면 처음부터 다시 만듭니다. 문제: x")
    fe = friendly_error(bad)
    assert "C:/out/a.zip.part.failed" in fe.what and "원본 파일은 그대로" in fe.what
    assert "패키지" not in fe.what and "해시" not in fe.what


def test_original_messages_avoid_jargon():
    assert "패키지" not in "\n".join(real_api.unreadable_lines([{"src": "C:/a"}]))


def _strip(s):
    return re.sub(r"\s+", " ", discover._VERSION_STRIP.sub(" ", s.lower())).strip()


def test_version_strip_regex_uses_word_boundary_not_backspace():
    assert "\x08" not in discover._VERSION_STRIP.pattern and "\\b" in discover._VERSION_STRIP.pattern
    assert _strip("Tool v1.2.3") == "tool"
    assert _strip("Tool 2024.1") == "tool"
    assert _strip("Tool 64-bit") == "tool" and _strip("Tool x86") == "tool"
    assert _strip("Revit 2024.1 (x64)") == "revit ( )"
    assert _strip("autocad2024") == "autocad2024" and _strip("max64x") == "max64x"  # 단어 안쪽은 건드리지 않음
    assert _strip("7-zip 23.01") == "7-zip"


def test_prep_words_drop_version_and_arch():
    words = discover._prep("Acme Studio 12.0.3 (x64)", "", "")[4]
    assert "acmestudio" in words and not any("12" in w or "x64" in w for w in words)


# --------------------------------------------------------------------------- 화면(tkinter)
tk = pytest.importorskip("tkinter")
from pcmigrate.gui import state as S  # noqa: E402
from test_gui_smoke import ctx  # noqa: E402,F401


def _go(ctx_, screen):
    ctx_.c.go(screen)
    ctx_.app.update()
    return ctx_.app.views[screen]


def test_preview_shows_denied_note_in_summary_and_clears_stale_ok(ctx):
    ctx.api.preview = real_api.ImportResult(plan([item(), item("denied", target="C:/x/a.dll")]), "dry-run", 0)
    ctx.c.set_package("p.zip")
    ctx.c.start_preview()
    v = _go(ctx, S.NEW_PREVIEW)
    assert "보안상 복원하지 않는 형식 1개" in v.summary.cget("text")
    assert v.problem_box.cget("text") == T.PREV_OK  # 안내 상자는 이 화면에 하나뿐이다
    ctx.c.state.preview = None  # 복원을 마친 뒤처럼 미리보기가 없어짐
    v._render()
    assert v.problem_box.cget("text") == ""  # 지난 '검사 통과' 표시가 남아 있으면 안 된다


def test_result_shows_denied_note(ctx):
    ctx.api.preview = real_api.ImportResult(plan([item(), item("denied", target="C:/x/a.dll")]), "dry-run", 0)
    rep = Report(dry_run=False, results=[FileResult("e", "C:/x/a.txt", "copied", 1, "x"),
                                         FileResult("e", "C:/x/a.dll", "denied", 1, "x", error="d")])
    ctx.c.state.import_result = real_api.ImportResult(ctx.api.preview.plan, "applied", 0, rep)
    v = _go(ctx, S.NEW_RESULT)
    assert "보안상 복원하지 않는 형식 1개" in v.note.cget("text")
    assert ctx.c.import_counts() == (1, 1, 0)


def test_export_done_mentions_denied_count(ctx):
    r = type("R", (), {})()
    r.manifest = {"rules": [], "files": [1], "skipped": [{"denied": True, "path": "a.dll"}, {"path": "x"}], "failed": [],
                  "manual_warnings": []}
    r.resume = {}
    v = _go(ctx, S.OLD_EXPORT)
    v.on_event("export_done", r)
    assert "실행·스크립트·라이브러리 등) 1개" in v.result.get("1.0", "end")


def test_status_card_current_path_fits_width_and_keeps_file_name(ctx):
    card = _go(ctx, S.TRANSFER).card
    full = "D:/프로젝트/" + "긴폴더/" * 30 + "a.dwg"
    card.cur_row.winfo_width = lambda: 420
    short = T.ellipsize_path(full, 60)
    fitted = card._fit_path(full, short)
    from tkinter import font as tkfont
    assert fitted.endswith("a.dwg") and "…" in fitted
    assert tkfont.nametofont(str(card.current.cget("font"))).measure(fitted) <= 420 - 130
    card.cur_row.winfo_width = lambda: 1
    assert card._fit_path(full, short) == short
