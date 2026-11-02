"""Z5: 수동 설치 목록의 버전 중복 표기, place 결과 머리글(실패가 섞이면 '일부만'), 정리 완료 안내 문구."""
from pcmigrate.checklist import describe_manual
from pcmigrate.detect import Program
from pcmigrate.gui import place_flow as pf
from pcmigrate.gui import texts as T


def test_manual_line_shows_version_once():
    p = Program("Foo 3", "1.0", "Acme Inc")
    assert p.display == "Foo 3 1.0"
    line = describe_manual(p, None)
    assert line.count("1.0") == 1 and "Acme Inc" in line
    assert "Foo 3 1.0" in line and "[구 PC 버전" not in line


def test_manual_line_without_publisher_omits_brackets():
    line = describe_manual(Program("Foo 3", "1.0"), None)
    assert line.startswith("Foo 3 1.0 — ") and line.count("1.0") == 1


def test_manual_line_keeps_bracket_version_when_name_hides_it():
    # 이름에 이미 버전 같은 숫자(2024)가 있으면 이름 옆에는 버전이 안 붙으므로 대괄호에 버전을 보여 준다
    p = Program("Foo 2024", "24.1.0", "Acme")
    assert p.display == "Foo 2024"
    assert "구 PC 버전: 24.1.0" in describe_manual(p, None)


def _sm(**kw):
    base = {"preview": False, "interrupted": "", "why": "", "failed_n": 0}
    base.update(kw)
    return base


def test_place_head_reports_partial_when_failures():
    assert pf.place_head(_sm()) == T.PL_DONE_HEAD
    head = pf.place_head(_sm(failed_n=3))
    assert "일부만 놓았습니다" in head and "실패 3개" in head and head != T.PL_DONE_HEAD
    assert pf.place_head(_sm(preview=True, failed_n=3)) == T.PL_PREVIEW_HEAD  # 미리보기는 그대로


def test_clean_finish_notice_mentions_report_files_removed_with_folder():
    assert "결과 리포트나 기록 파일도" in T.PL_REMAINING_CLEAN and "지워도 됩니다" in T.PL_REMAINING_CLEAN
