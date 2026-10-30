"""화면 문구 상수 모듈(gui/texts.py) 테스트: 비어 있지 않음, 개발 용어 금지, 존댓말, 상수 일관성."""
import ast
import re
from pathlib import Path

import pytest

from pcmigrate.checklist import build_checklist
from pcmigrate.detect import Program
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T

BANNED = ["dry-run", "dry run", "manifest", "SHA-256", "sha256", "robocopy", "extra", "strict", "UNC"]
GUI_DIR = Path(__file__).resolve().parents[1] / "src" / "pcmigrate" / "gui"
SCREENS = (S.START, S.OLD_SCAN, S.OLD_DISCOVER, S.OLD_CHECKLIST, S.OLD_EXPORT, S.NEW_PACKAGE, S.NEW_INSTALL, S.NEW_PREVIEW,
           S.NEW_CONFIRM, S.NEW_RESULT, S.TRANSFER, S.FIND, S.METHOD, S.LAN_RECV, S.LAN_SEND, S.PLACE)  # stage30a: 내 파일 옮기기 화면들, stage50: 받은 파일 놓기


def banned_in(text: str) -> list[str]:
    found = []
    for term in BANNED:
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", text, re.I):
            found.append(term)
    return found


def flatten(value):
    """texts 모듈의 값(문자열/튜플/리스트/딕셔너리)에서 문자열을 모두 꺼낸다."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from flatten(k)
            yield from flatten(v)
    elif isinstance(value, (tuple, list)):
        for v in value:
            yield from flatten(v)


def constants():
    for name in dir(T):
        if name.isupper() or (name.isupper() is False and name.replace("_", "").isupper()):
            yield name, getattr(T, name)


USER_CONSTANTS = {n: v for n, v in constants() if n not in ("DETAIL_REPLACEMENTS", "FONT_FAMILIES", "FONT_SIZE", "TEXT_ICONS")}


def test_there_are_many_constants():
    assert len(USER_CONSTANTS) > 150


def test_every_constant_is_non_empty_text():
    empty = []
    for name, value in USER_CONSTANTS.items():
        texts = list(flatten(value))
        if not texts or (name != "CRUMB_HOME" and not all(t.strip() for t in texts)):
            empty.append(name)
    assert empty == []


def test_no_developer_terms_in_user_visible_texts():
    found = [(name, banned_in(t), t[:50]) for name, value in USER_CONSTANTS.items() for t in flatten(value) if banned_in(t)]
    assert found == []


def test_detail_replacement_table_only_hides_terms():
    # 키는 내부 용어(바꿀 대상)이고, 값(사용자에게 보이는 말)에는 금지 용어가 없다
    for old, new in T.DETAIL_REPLACEMENTS:
        assert banned_in(new) == [], (old, new)
    for raw in ("SHA-256 불일치", "robocopy 종료코드 8: 일부 복사 실패", "manifest 에 없는 엔트리", "허용되지 않은 복원 루트(extra)",
                "크기 불일치(대상 3)", "strict 모드", "UNC 경로", "복사 후 크기/해시 검증 실패"):
        assert banned_in(T.friendly_detail(raw)) == [], raw


def test_helper_outputs_are_clean():
    for s in (T.result_line(1, 2, 3), T.ellipsize_path("C:\\" + "a" * 100 + "\\b.txt"), T.CL_PROGRESS_MARK.format(done=1, total=2)):
        assert banned_in(s) == []
    line = T.result_line(5, 2, 1)
    assert "성공 5" in line and "건너뜀 2" in line and "실패 1" in line


def test_ellipsize_path_keeps_head_and_tail():
    p = "C:\\Users\\someone\\AppData\\Roaming\\Vendor\\Product\\Deep\\Folder\\file.cfg"
    short = T.ellipsize_path(p, 40)
    assert len(short) <= 40 and "…" in short and short.endswith("file.cfg") and short.startswith("C:\\Users")
    assert T.ellipsize_path("C:\\short", 40) == "C:\\short"


def test_code_strings_with_korean_do_not_use_developer_terms():
    """views/app/controller 의 한글 문자열(독스트링 제외)에도 금지 용어가 없다. 문구는 texts.py 에서 가져와 쓴다."""
    offenders = []
    for path in sorted(GUI_DIR.rglob("*.py")):
        if path.name == "texts.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                if re.search("[가-힣]", node.value) and banned_in(node.value):
                    offenders.append((path.name, node.value[:60]))
    assert offenders == []


def test_polite_tone_no_plain_style_sentences():
    """'~한다.' 같은 반말체 문장이 없다(존댓말 '~합니다/하세요' 로 통일)."""
    bad = []
    for name, value in USER_CONSTANTS.items():
        for t in flatten(value):
            for sentence in re.split(r"(?<=[.?!])\s+|\n", t):
                s = sentence.strip().rstrip(")")
                if re.search(r"[가-힣]다\.$", s) and not re.search(r"(니다|습니다)\.$", s):
                    bad.append((name, s))
    assert bad == []


def test_screen_names_titles_and_help_are_complete_and_consistent():
    assert (T.START, T.OLD_SCAN, T.OLD_DISCOVER, T.OLD_CHECKLIST, T.OLD_EXPORT) == (S.START, S.OLD_SCAN, S.OLD_DISCOVER,
                                                                                    S.OLD_CHECKLIST, S.OLD_EXPORT)
    assert (T.NEW_PACKAGE, T.NEW_INSTALL, T.NEW_PREVIEW, T.NEW_CONFIRM, T.NEW_RESULT, T.TRANSFER) == (S.NEW_PACKAGE, S.NEW_INSTALL, S.NEW_PREVIEW,
                                                                                       S.NEW_CONFIRM, S.NEW_RESULT, S.TRANSFER)
    assert S.TITLES is T.TITLES
    for scr in SCREENS:
        assert T.TITLES[scr], scr
        help_text = T.HELP[scr]  # 무엇을 하는 화면인가요 / 안전한가요 / 다음 단계 세 부분
        for part in ("무엇을 하는 화면인가요?", "안전한가요?", "다음 단계"):
            assert part in help_text, (scr, part)
        assert len(help_text) < 700  # 짧게


def test_checklist_hidden_mark_matches_checklist_module():
    cl = build_checklist([], [Program("Google Chrome")], [Program("a.b", source="winget", winget_id="a.b")], show_all=True)
    hidden = [l for l in [*cl.login, *cl.data] if l.endswith(T.CL_HIDDEN_MARK)]
    assert hidden  # checklist.py 가 붙이는 표시와 같아야 GUI 가 숨길 수 있다


def test_jargon_free_replacements_present_in_key_screens():
    assert "미리보기(실제로 바꾸지 않음)" in T.TASK_TRANSFER_PREVIEW and "실제로 바꾸지 않음" in T.PREV_SUB + T.TR_PREVIEW_RESULT
    assert "\\\\컴퓨터이름\\폴더" in T.TR_DST_HINT  # 공유 폴더 경로 안내
    assert ".bak" in T.SAFETY_LINES[1] and "백업 파일" in T.SAFETY_LINES[1]
    assert "같은지" in T.TR_SUB and T.TERM_CHECK_DESC in T.TERM_CHECK_FULL  # 복사본이 원본과 같은지 확인(해시를 쓰지 않는다)
    assert T.TR_STRICT == T.TERM_FULL_RECHECK == "처음부터 모두 다시 확인(오래 걸림)" and "공유 폴더" in T.TR_DST_HINT


def test_button_names_are_clear_verbs():
    for label in (T.BTN_NEXT, T.BTN_BACK, T.BTN_CANCEL, T.BTN_SCAN, T.BTN_DISCOVER, T.BTN_CL_MAKE, T.BTN_EXPORT, T.BTN_APPLY,
                  T.BTN_PREVIEW_NEXT, T.BTN_BROWSE):
        assert 1 < len(label) <= 16
    assert "미리보기 실행" in T.BTN_TR_PREVIEW and "복사 시작" in T.BTN_TR_GO and T.BTN_APPLY == "복원 실행"
    assert T.BTN_NEXT == "다음(N)" and T.BTN_CANCEL == "취소"  # (N) 은 Alt+N 단축키 표시


def test_license_notice_is_prominent():
    assert "라이선스" in T.LICENSE_BANNER and "복원하지 않습니다" in T.LICENSE_BANNER and T.ICON_WARN in T.LICENSE_BANNER
    assert len(T.START_INTRO.splitlines()) <= 3  # 첫 화면 설명은 3줄 이내
    for title, desc, btn, target in T.START_CHOICES:
        assert desc.endswith("니다.") and target in SCREENS


def test_card_head_texts_polite_and_without_circled_numbers():  # stage36
    for name in ("CARD_HEAD_RUNNING", "CARD_HEAD_DONE", "CARD_STEP_DONE"):
        value = getattr(T, name)
        assert not any(0x2460 <= ord(ch) <= 0x2473 for ch in value), name
        assert not banned_in(value), name
    assert T.CARD_HEAD_RUNNING.format(step="복사").endswith("중") and T.CARD_HEAD_DONE == "완료"
