"""stage32: 설정 확인표(CoverageBox)의 '제외 N개' 한 줄 요약, 예시 펼침, 노란 안내. 창을 숨긴 채 가짜 표만 쓴다."""
import time

import pytest

tk = pytest.importorskip("tkinter")

from pcmigrate import coverage, settings_filter as sf  # noqa: E402
from pcmigrate.gui import texts as T  # noqa: E402
from pcmigrate.gui.views.common import CoverageBox  # noqa: E402


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


def cov_entry(name, by, examples, files=2, nc=100, nc_bytes=3 * 1024 ** 3):
    return {"rule": "extra", "name": name, "path": "%APPDATA%\\X", "description": "%APPDATA%\\X", "files": files, "failed": 0,
            "excluded": 0, "status": "담김", "config_mode": True, "allowed_files": files, "allowed_bytes": 10,
            "ext_counts": {".json": files}, "not_config_files": nc, "not_config_bytes": nc_bytes, "limit_files": 0, "limit_bytes": 0,
            "excluded_by_reason": by, "excluded_examples": examples}


EXAMPLES = [{"name": f"db/big{i}.db", "size": (9 - i) * 1024 ** 2, "why": sf.WHY_DB} for i in range(5)]


def make(root, entries):
    box = CoverageBox(root)
    box.pack(fill="x")
    box.show(coverage.rows({"coverage": entries}))
    root.update()
    return box


def test_line_summary_under_row_and_examples_collapsed(root):
    box = make(root, [cov_entry("A", {sf.WHY_DB: 12, sf.WHY_MEDIA: 88}, EXAMPLES)])
    tops = box.tree.get_children("")
    assert len(tops) == 1  # 프로그램 행 수는 그대로(요약 줄은 자식 행)
    (line,) = box.tree.get_children(tops[0])
    vals = box.tree.item(line, "values")
    assert "제외 100개, 3.0GB(이미지·영상 88개, 데이터베이스/데이터 12개)" in vals[1] and vals[2] == T.BTN_MORE
    assert not box.tree.item(line, "open") and len(box.tree.get_children(line)) == 5
    assert "big0.db 9.0MB — 데이터베이스/데이터" in box.tree.item(box.tree.get_children(line)[0], "values")[1]


def test_toggle_by_click_and_key_expands_examples(root):
    box = make(root, [cov_entry("A", {sf.WHY_DB: 1}, EXAMPLES)])
    (line,) = box.tree.get_children(box.tree.get_children("")[0])
    h0 = int(box.tree.cget("height"))
    box._toggle_line(line)
    root.update()
    assert box.tree.item(line, "open") and box.tree.set(line, "status") == T.BTN_LESS
    assert int(box.tree.cget("height")) > h0
    box.tree.selection_set(line)
    box._key_line()
    assert not box.tree.item(line, "open") and box.tree.set(line, "status") == T.BTN_MORE
    box._toggle_line(box.tree.get_children("")[0])  # 프로그램 행(줄이 아님)은 무시
    assert not box.tree.item(line, "open")


def test_noext_notice_only_when_present(root):
    with_noext = make(root, [cov_entry("A", {sf.WHY_NOEXT: 5, sf.WHY_DB: 3}, EXAMPLES), cov_entry("B", {sf.WHY_NOEXT: 2}, [])])
    assert with_noext._noext_shown
    assert with_noext.noext.cget("text").endswith(T.COV_NOEXT_HINT.format(n=7))
    assert "필요하면 폴더 전체 담기를 켜고 다시 내보내 주세요" in with_noext.noext.cget("text")
    without = make(root, [cov_entry("A", {sf.WHY_DB: 3}, EXAMPLES)])
    assert not without._noext_shown
    without.show(coverage.rows({"coverage": [cov_entry("A", {sf.WHY_NOEXT: 1}, [])]}))
    assert without._noext_shown
    without.clear()
    assert not without._noext_shown and without.tree.get_children("") == ()


def test_old_manifest_row_has_line_without_examples(root):
    c = cov_entry("A", {}, [])
    c.pop("excluded_by_reason")
    c.pop("excluded_examples")
    box = make(root, [c])
    (line,) = box.tree.get_children(box.tree.get_children("")[0])
    assert box.tree.item(line, "values")[1].endswith("제외 100개, 3.0GB") and box.tree.get_children(line) == ()
    assert not box._noext_shown


def test_rows_without_excluded_have_no_line(root):
    c = cov_entry("A", {}, [], nc=0, nc_bytes=0)
    box = make(root, [c])
    assert box.tree.get_children(box.tree.get_children("")[0]) == () and not box.lines
