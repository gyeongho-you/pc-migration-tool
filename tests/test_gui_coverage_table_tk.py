"""stage86: 확인표(CoverageBox)의 새 열·상태·직접 해야 할 것·기타 한 줄·범례를 창을 숨긴 채 시험한다(가짜 표만, 가짜 환경변수)."""
import time

import pytest

tk = pytest.importorskip("tkinter")

from pcmigrate import coverage  # noqa: E402
from pcmigrate.gui import coverage_view as cv, texts as T  # noqa: E402
from pcmigrate.gui.views.common import CoverageBox  # noqa: E402


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


@pytest.fixture
def root():
    r, last = None, None
    for _ in range(4):
        try:
            r = tk.Tk()
            break
        except tk.TclError as e:
            last = e
            time.sleep(0.5)
    if r is None:
        pytest.skip(f"디스플레이/Tcl 을 사용할 수 없음: {last}")
    r.withdraw()
    yield r
    r.destroy()


def entry(name, status="담김", files=3, failed=0, excluded=0, **extra):
    d = {"rule": name.lower(), "name": name, "path": "%APPDATA%\\" + name, "description": "설정·템플릿", "files": files, "failed": failed,
         "excluded": excluded, "status": status}
    d.update(extra)
    return d


def shown(root, entries, warnings=()):
    table = coverage.rows({"coverage": entries})
    table, other = cv.attach_manual(table, list(warnings))
    box = CoverageBox(root)
    box.pack(fill="x")
    box.show(table, other)
    root.update()
    return box


def tops(box):
    return [box.tree.item(i, "values") for i in box.tree.get_children("")]


def test_columns_title_legend_and_status_texts(root):
    box = shown(root, [entry("Revit"), entry("AutoCAD", excluded=2), entry("Chrome", failed=3), entry("Hand", status="직접 해야 함", files=0),
                       entry("Gone", status="이 PC에 없음", files=0)])
    assert tuple(box.tree.heading(c)["text"] for c in box.tree["columns"]) == T.COV2_COLS
    assert box.title_label.cget("text") == T.COV2_TITLE and T.COV2_LEGEND in [box.legend.cget("text")]
    states = [v[3] for v in tops(box)]
    assert states == [T.COV2_ST_OK, T.COV2_ST_PART, T.COV2_ST_FAIL, T.COV2_ST_MANUAL]
    assert "해당 없음 1곳" in box.fold.toggle_btn.cget("text")  # 해당 없음은 기존처럼 접힘


def test_part_and_fail_rows_have_note_lines_and_files_move_to_tooltip(root):
    box = shown(root, [entry("AutoCAD", excluded=2), entry("Chrome", failed=3)])
    part_iid, fail_iid = box.tree.get_children("")
    (pn,) = box.tree.get_children(part_iid)
    (fn,) = box.tree.get_children(fail_iid)
    assert "의도적으로 뺀 파일 2개" in box.tree.item(pn, "values")[1]
    assert "읽지 못한 파일 3개" in box.tree.item(fn, "values")[1] and "다시 담기" in box.tree.item(fn, "values")[1]
    assert T.COV2_FILES_TIP.format(n=3) in box.full[part_iid]  # 담긴 파일 수는 툴팁


def test_manual_column_summary_expands_to_full_list_and_other_is_one_line(root):
    box = shown(root, [entry("Revit"), entry("AutoCAD")],
                [{"name": "Revit", "items": ["다시 로그인", "라이선스", "템플릿 위치"]}, {"name": "Nothing Matches", "items": ["기타 직접 설정"]}])
    rev, cad = tops(box)
    assert rev[2] == "다시 로그인 외 2건 ▸" and cad[2] == T.COV2_MANUAL_NONE
    first = box.tree.get_children("")[0]
    (line,) = box.tree.get_children(first)
    assert T.COV2_MANUAL_LINE.format(n=3) in box.tree.item(line, "values")[1] and not box.tree.item(line, "open")
    assert len(box.tree.get_children(line)) == 3
    box._toggle_line(line)
    assert box.tree.item(line, "open") and "라이선스" in box.tree.item(box.tree.get_children(line)[1], "values")[1]
    assert box._misc_shown and box.misc.toggle_btn.cget("text") == T.COV2_MISC.format(n=1)
    assert "기타 직접 설정" in box.misc_list.cget("text")


def test_no_other_line_when_every_warning_matches_and_clear_resets(root):
    box = shown(root, [entry("Revit")], [{"name": "Revit", "items": ["라이선스"]}])
    assert not box._misc_shown
    box.clear()
    assert not box._misc_shown and box.tree.get_children("") == ()


def test_old_package_rows_without_manual_items_still_draw(root):
    box = CoverageBox(root)
    box.pack(fill="x")
    box.show(coverage.rows({"rules": [{"id": "a", "name": "Old"}], "files": [{"rule": "a"}]}))  # 컨트롤러를 거치지 않은 행(manual_items 없음)
    root.update()
    assert tops(box)[0][0] == "Old" and tops(box)[0][2] == ""
