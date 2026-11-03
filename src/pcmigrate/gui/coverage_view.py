"""설정 확인표(CoverageBox)의 표시용 매핑(stage86). tkinter 와 무관한 순수 함수라 화면 없이 시험할 수 있다.

coverage.py 의 상태 문구는 그대로 두고(CLI/JSON 출력도 그대로) 화면에 보일 말만 여기서 정한다:
- 모두 옮김(ok) / 일부 뺌(part: 의도적으로 뺀 것만) / 일부 못 읽음(fail: 읽지 못한 파일이 있음) / 직접 해야 함(manual) / 그 밖(other: 원래 상태 문구).
- 직접 해야 할 것: manifest 의 manual_warnings(이름으로 프로그램과 연결)를 재사용한다. 못 찾은 경고는 '기타 직접 할 일'로 모은다.
"""
from __future__ import annotations

from .. import coverage
from . import texts as T

OK, PART, FAIL, MANUAL, OTHER = "ok", "part", "fail", "manual", "other"


def _n(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def excluded_count(row: dict) -> int:
    """의도적으로 뺀 파일 수: 설정 형식이 아님+상한 초과(excluded_files)와 보안·링크 제외(excluded)."""
    return _n(row.get("excluded_files")) + _n(row.get("excluded"))


def display_state(row: dict) -> str:
    st = row.get("status")
    if _n(row.get("failed")) or st == coverage.ST_UNREAD:
        return FAIL  # 읽지 못한 파일이 하나라도 있으면 먼저 알린다(일부만 뺀 것과 섞지 않는다)
    if st == coverage.ST_MANUAL:
        return MANUAL
    if st in (coverage.ST_OK, coverage.ST_PART, coverage.ST_EXCL):
        return PART if (st != coverage.ST_OK or excluded_count(row)) else OK
    return OTHER


def status_text(row: dict) -> str:
    s = display_state(row)
    return {OK: T.COV2_ST_OK, PART: T.COV2_ST_PART, FAIL: T.COV2_ST_FAIL, MANUAL: T.COV2_ST_MANUAL}.get(s, str(row.get("status", "")))


def part_note(row: dict) -> str:
    why = coverage.reasons_text(row.get("excluded_by_reason") or {}) if row.get("excluded_by_reason") else ""
    return T.COV2_PART_NOTE.format(n=excluded_count(row), why=why or T.COV2_WHY_DEFAULT)


def fail_note(row: dict) -> str:
    n = _n(row.get("failed"))
    return T.COV2_FAIL_NOTE.format(n=n) if n else T.COV2_FAIL_NOTE_NONE


def manual_summary(items: list[str]) -> str:
    if not items:
        return T.COV2_MANUAL_NONE
    first = str(items[0])
    return T.COV2_MANUAL_SINGLE.format(first=first) if len(items) == 1 else T.COV2_MANUAL_ONE.format(first=first, n=len(items) - 1)


def _norm(name: object) -> str:
    return str(name or "").strip().casefold()


def _same_program(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def attach_manual(table: list[dict], warnings: object) -> tuple[list[dict], list[str]]:
    """행마다 manual_items(직접 해야 할 것 목록)를 붙이고, 어느 행과도 이름이 맞지 않은 경고 항목들(기타)을 돌려준다.
    같은 프로그램의 여러 행에는 첫 행에만 붙인다(같은 목록을 되풀이하지 않는다)."""
    ws = [w for w in (warnings if isinstance(warnings, list) else []) if isinstance(w, dict)]
    first_row: dict[str, dict] = {}
    for r in table:
        r["manual_items"] = []
        r.setdefault("_prog_key", _norm(r.get("program")))
        first_row.setdefault(r["_prog_key"], r)
    other: list[str] = []
    for w in ws:
        items = [str(i) for i in (w.get("items") or []) if str(i).strip()]
        if not items:
            continue
        key = _norm(w.get("name"))
        target = first_row.get(key) or next((row for k, row in first_row.items() if _same_program(k, key)), None)
        if target is None:
            other += [f"{w.get('name', '')}: {i}" if w.get("name") else i for i in items]
        else:
            for i in items:
                if i not in target["manual_items"]:
                    target["manual_items"].append(i)
    return table, other
