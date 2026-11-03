"""stage86: '프로그램별로 옮겨지는 것과 직접 해야 할 것' 확인표의 순수 함수 시험(화면 없음). 가짜 manifest 만 쓴다."""
import json

from pcmigrate import coverage
from pcmigrate.gui import coverage_view as cv, texts as T


def entry(name, status="담김", files=2, failed=0, excluded=0, **extra):
    d = {"rule": name.lower(), "name": name, "path": "%APPDATA%\\" + name, "description": "설정·템플릿", "files": files, "failed": failed,
         "excluded": excluded, "status": status}
    d.update(extra)
    return d


def test_rows_carry_failed_and_excluded_integers_but_json_and_cli_do_not_change():
    m = {"coverage": [entry("Revit", failed=3, excluded=2), entry("Old")], "manual_warnings": []}
    rows = coverage.rows(m)
    assert rows[0]["failed"] == 3 and rows[0]["excluded"] == 2 and rows[1]["failed"] == 0
    assert set(coverage.json_rows(m)[0]) == {"program", "what", "status", "files"}  # JSON 키는 그대로
    assert "failed" not in "\n".join(coverage.lines(m))  # CLI 표도 그대로
    old = coverage.rows({"rules": [{"id": "a", "name": "A"}], "files": [{"rule": "a"}]})  # 이전 버전 묶음
    assert old[0]["failed"] == 0 and old[0]["excluded"] == 0


def row(**kw):
    base = {"program": "P", "what": "w", "status": coverage.ST_OK, "files": 1, "failed": 0, "excluded": 0}
    base.update(kw)
    return base


def test_display_state_mapping_separates_intentional_exclusion_from_read_failure():
    assert cv.display_state(row()) == cv.OK and cv.status_text(row()) == T.COV2_ST_OK
    assert cv.display_state(row(excluded_files=4)) == cv.PART and cv.status_text(row(excluded_files=4)) == T.COV2_ST_PART
    assert cv.display_state(row(excluded=2)) == cv.PART  # 보안·링크 제외
    assert cv.display_state(row(status=coverage.ST_PART, excluded_files=1)) == cv.PART
    assert cv.display_state(row(failed=3)) == cv.FAIL and cv.status_text(row(failed=3)) == T.COV2_ST_FAIL
    assert cv.display_state(row(failed=1, excluded_files=5)) == cv.FAIL  # 읽지 못한 것이 있으면 의도된 제외와 섞지 않고 먼저 알린다
    assert cv.display_state(row(status=coverage.ST_UNREAD, files=0)) == cv.FAIL
    assert cv.display_state(row(status=coverage.ST_MANUAL, files=0)) == cv.MANUAL and cv.status_text(row(status=coverage.ST_MANUAL)) == T.COV2_ST_MANUAL
    assert cv.display_state(row(status=coverage.ST_EMPTY, files=0)) == cv.OTHER and cv.status_text(row(status=coverage.ST_EMPTY)) == coverage.ST_EMPTY


def test_notes_use_existing_reason_text_and_counts():
    r = row(excluded_files=3, excluded=1, excluded_by_reason={"데이터베이스/데이터": 4})
    assert "의도적으로 뺀 파일 4개(데이터베이스/데이터 4개)" in cv.part_note(r)
    assert "설정이 아닌 파일이거나 안전을 위해 옮기지 않는 것입니다" in cv.part_note(r)
    assert T.COV2_WHY_DEFAULT in cv.part_note(row(excluded=2))
    assert "읽지 못한 파일 3개" in cv.fail_note(row(failed=3)) and "다시 담기" in cv.fail_note(row(failed=3))
    assert "다시 담기" in cv.fail_note(row(status=coverage.ST_UNREAD, failed=0))


def test_manual_summary_forms():
    assert cv.manual_summary([]) == "없음"
    assert cv.manual_summary(["다시 로그인"]) == "다시 로그인 ▸"
    assert cv.manual_summary(["다시 로그인", "라이선스", "템플릿"]) == "다시 로그인 외 2건 ▸"


def test_attach_manual_matches_by_name_first_row_only_and_collects_the_rest():
    table = coverage.rows({"coverage": [entry("Revit"), entry("Revit", description="플러그인"), entry("AutoCAD", files=0, status="직접 해야 함")]})
    warns = [{"name": "revit", "items": ["다시 로그인", "라이선스"]},
             {"name": "AutoCAD LT", "items": ["라이선스"]},  # 이름 일부가 겹치면 같은 프로그램으로 본다
             {"name": "Unknown Tool", "items": ["직접 설정", ""]},
             {"name": "Revit", "items": ["다시 로그인", "템플릿 위치"]},  # 같은 항목은 한 번만
             "깨진 항목"]
    table, other = cv.attach_manual(table, warns)
    assert table[0]["manual_items"] == ["다시 로그인", "라이선스", "템플릿 위치"] and table[1]["manual_items"] == []
    assert table[2]["manual_items"] == ["라이선스"]
    assert other == ["Unknown Tool: 직접 설정"]
    assert cv.attach_manual([], None) == ([], [])


def test_new_texts_pass_the_same_rules_as_other_texts():
    from test_gui_texts import banned_in, flatten
    names = [n for n in dir(T) if n.startswith("COV2_")]
    assert len(names) >= 18
    for n in names:
        for t in flatten(getattr(T, n)):
            assert banned_in(t) == [] and t.strip(), n
    assert T.COV2_COLS == ("프로그램", "옮겨진 설정", "직접 해야 할 것", "상태")
    assert T.COV2_TITLE == "프로그램별로 옮겨지는 것과 직접 해야 할 것" and T.COV2_PREVIEW_TITLE == "이 묶음으로 이만큼 복원됩니다"
