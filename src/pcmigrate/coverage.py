"""프로그램별 '설정 확인표': 어떤 설정이 패키지에 담겼는지 사람이 읽는 표로 만든다(읽기 전용, 표시 전용).

내보낼 때 package 가 manifest["coverage"] 에 경로별 결과를 기록하고, 이 모듈이 그것을 표(행)와 문장으로 바꾼다.
이전 버전 패키지(coverage 항목 없음)도 읽을 수 있다: 규칙별 담긴 파일 수만 보여 준다.
"""
from __future__ import annotations

from . import settings_filter
from .progress import fmt_bytes

ST_OK, ST_PART, ST_NONE = "담김", "일부만 담김", "이 PC에 없음"
ST_UNREAD, ST_MANUAL, ST_EXCL, ST_EMPTY = "읽지 못함", "직접 해야 함", "제외됨", "비어 있음"
NONE_NOTE = "이 PC에 이 설정이 만들어진 적이 없을 수 있습니다"
EXTRA_PROGRAM = "목록 밖 프로그램(직접 고른 폴더)"
INSTALL_DIR_WHAT = "설치 폴더 설정"
INSTALL_DIR_NOTE = "복원하려면 관리자 권한과 같은 버전의 프로그램이 필요합니다"
NO_AUTO_SETTINGS = "자동으로 옮기는 설정이 없습니다(아래 직접 할 일 참고)"
OLD_PACKAGE_WHAT = "설정 파일(이전 버전 패키지라 설정별 내역은 없음)"
STATUS_ORDER = (ST_OK, ST_PART, ST_UNREAD, ST_EXCL, ST_EMPTY, ST_MANUAL, ST_NONE)


def classify(*, found: bool, files: int, failed: int, excluded: int) -> str:
    """경로 하나의 상태. found=경로가 실제로 있었는지, excluded=보안/링크 등 '정상이 아닌' 제외 수(라이선스·임시 제외는 세지 않음)."""
    if not found:
        return ST_NONE
    if failed and not files:
        return ST_UNREAD
    if files and (failed or excluded):
        return ST_PART
    if files:
        return ST_OK
    if excluded:
        return ST_EXCL
    return ST_EMPTY


def rows(manifest: dict) -> list[dict]:
    """manifest 에서 표의 행을 만든다: {program, what, status, files, note, rule, path}. 이전 버전 패키지도 허용."""
    cov = manifest.get("coverage")
    out: list[dict] = []
    if isinstance(cov, list):
        for c in cov:
            if not isinstance(c, dict):
                continue
            files = c.get("files") if isinstance(c.get("files"), int) else 0
            status = c.get("status") if isinstance(c.get("status"), str) else ST_NONE
            note = ""
            if status == ST_NONE:
                note = NONE_NOTE
            elif c.get("cache_dirs"):
                note = (f"임시 저장물 폴더 {c['cache_dirs']}개(파일 {c.get('cache_files', 0)}개, "
                        f"{fmt_bytes(c.get('cache_bytes', 0))})는 다시 만들어지므로 뺐습니다")
            elif status == ST_MANUAL:
                note = "자동으로 옮기지 않습니다"
            extra_note = _config_note(c)
            if extra_note:
                note = f"{note}; {extra_note}" if note else extra_note
            if c.get("rule") == "install" and status in (ST_OK, ST_PART):
                note = (note + " / " if note else "") + INSTALL_DIR_NOTE
            row = {"program": str(c.get("name") or c.get("rule") or "?"), "what": str(c.get("description") or c.get("path") or ""),
                   "status": status, "files": files, "note": note, "rule": str(c.get("rule", "")),
                   "path": str(c.get("path", ""))}
            if c.get("config_mode"):  # 허용 방식(설정 파일만)으로 담은 항목: 화면/표가 더 보여 줄 수 있는 세부(없으면 키 자체가 없다)
                row.update({k: c.get(k) for k in ("ext_counts", "allowed_files", "allowed_bytes", "not_config_files",
                                                   "not_config_bytes", "limit_files", "limit_bytes")})
                row.update(excluded_fields(c))
            out.append(row)
        return out
    counts: dict[str, int] = {}
    for f in manifest.get("files", []):
        if isinstance(f, dict):
            counts[str(f.get("rule", ""))] = counts.get(str(f.get("rule", "")), 0) + 1
    for r in manifest.get("rules", []):
        if not isinstance(r, dict):
            continue
        n = counts.get(str(r.get("id", "")), 0)
        out.append({"program": str(r.get("name") or r.get("id") or "?"), "what": OLD_PACKAGE_WHAT,
                    "status": ST_OK if n else ST_NONE, "files": n, "note": "" if n else NONE_NOTE,
                    "rule": str(r.get("id", "")), "path": ""})
    n_extra = counts.get("extra", 0)
    if n_extra:
        out.append({"program": EXTRA_PROGRAM, "what": "직접 고른 폴더", "status": ST_OK, "files": n_extra, "note": "",
                    "rule": "extra", "path": ""})
    return out


def fold(table: list[dict]) -> tuple[list[dict], list[dict]]:
    """정상적인 '이 PC에 없음' 행은 따로 접는다. (보여 줄 행, 접은 행)."""
    shown = [r for r in table if r["status"] != ST_NONE]
    hidden = [r for r in table if r["status"] == ST_NONE]
    return shown, hidden


def summary_counts(table: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in table:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def folded_line(n: int) -> str:
    return f"해당 없음 {n}곳(이 PC에 없는 설정 위치라 건너뜀 — {NONE_NOTE})"


def lines(manifest: dict, *, expand_none: bool = False, indent: str = "  ") -> list[str]:
    """CLI/GUI 텍스트용 설정 확인표. 행이 없으면 빈 목록."""
    table = rows(manifest)
    if not table:
        return []
    shown, hidden = fold(table)
    out = ["설정 확인표(프로그램 / 옮기는 설정 / 상태 / 담긴 파일 수):"]
    for r in shown:
        tail = f" — {r['note']}" if r["note"] else ""
        out.append(f"{indent}{r['program']} / {r['what']} / {r['status']} / {r['files']}개{tail}")
        if r.get("excluded_line"):
            out.append(f"{indent}{indent}{r['excluded_line']}")
            out += [f"{indent}{indent}{indent}· {example_text(e)}" for e in r.get("excluded_examples", [])]
    if hidden:
        out.append(f"{indent}{folded_line(len(hidden))}")
        if expand_none:
            out += [f"{indent}{indent}· {r['program']} / {r['what']}" for r in hidden]
    return out


def _num(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def ext_counts_text(counts: object, limit: int = 8) -> str:
    """{'.json': 3, '.ini': 1} -> '.json 3개, .ini 1개'(많은 순, limit 가짓수까지)."""
    if not isinstance(counts, dict):
        return ""
    pairs = [(str(k), _num(v)) for k, v in counts.items() if _num(v)]
    pairs.sort(key=lambda kv: (-kv[1], kv[0]))
    text = ", ".join(f"{k} {n}개" for k, n in pairs[:limit])
    return text + (f" 외 {len(pairs) - limit}가지" if len(pairs) > limit else "")


def _config_note(c: dict) -> str:
    if not c.get("config_mode"):
        return ""
    parts = []
    if _num(c.get("allowed_files")):
        parts.append(f"설정 형식 파일만 담음({ext_counts_text(c.get('ext_counts'))})")
    if _num(c.get("not_config_files")):
        parts.append(f"설정 형식이 아니라 제외 {_num(c['not_config_files'])}개({fmt_bytes(_num(c.get('not_config_bytes')))})")
    if _num(c.get("limit_files")):
        parts.append(f"상한 초과로 제외 {_num(c['limit_files'])}개({fmt_bytes(_num(c.get('limit_bytes')))})")
    return "; ".join(parts)


def config_totals(manifest: dict) -> dict:
    """허용 방식(설정 파일만)으로 담은 항목들의 합계. items=0 이면 허용 방식 항목이 없다(규칙만, 또는 예전 방식/이전 버전 패키지).
    {items, allowed_files, allowed_bytes, ext_counts, not_config_files, not_config_bytes, limit_files, limit_bytes}"""
    out = {"items": 0, "allowed_files": 0, "allowed_bytes": 0, "ext_counts": {}, "not_config_files": 0, "not_config_bytes": 0,
           "limit_files": 0, "limit_bytes": 0, "by_reason": {}}
    cov = manifest.get("coverage")
    for c in cov if isinstance(cov, list) else []:
        if not isinstance(c, dict) or not c.get("config_mode"):
            continue
        out["items"] += 1
        for k in ("allowed_files", "allowed_bytes", "not_config_files", "not_config_bytes", "limit_files", "limit_bytes"):
            out[k] += _num(c.get(k))
        for k, n in _clean_reasons(c.get("excluded_by_reason")).items():
            out["by_reason"][k] = out["by_reason"].get(k, 0) + n
        if isinstance(c.get("ext_counts"), dict):
            for e, n in c["ext_counts"].items():
                out["ext_counts"][str(e)] = out["ext_counts"].get(str(e), 0) + _num(n)
    return out


def config_lines(manifest: dict, indent: str = "") -> list[str]:
    """CLI/GUI 용: 허용 방식 결과 두세 줄(담긴 파일 확장자별 개수, 설정 형식이 아니라 제외한 파일 수/용량, 상한 초과). 해당 없으면 빈 목록."""
    t = config_totals(manifest)
    if not t["items"]:
        return []
    out = [f"{indent}허용 방식(설정 파일만)으로 담은 파일 {t['allowed_files']}개({fmt_bytes(t['allowed_bytes'])})"
           + (f": {ext_counts_text(t['ext_counts'])}" if t["ext_counts"] else "")]
    if t["not_config_files"]:
        out.append(f"{indent}제외된 파일(설정 형식이 아님) {t['not_config_files']}개, 용량 {fmt_bytes(t['not_config_bytes'])}"
                   " — 전부 담으려면 --extra-mode all")
    if t["by_reason"]:
        out.append(f"{indent}빠진 사유: {reasons_text(t['by_reason'], 6)}")
    if t["by_reason"].get(settings_filter.WHY_NOEXT):
        out.append(f"{indent}[주의] " + NOEXT_HINT_CLI.format(n=t["by_reason"][settings_filter.WHY_NOEXT]))
    if t["limit_files"]:
        out.append(f"{indent}상한(파일 크기/폴더 용량/파일 수)을 넘어 제외된 설정 형식 파일 {t['limit_files']}개, 용량 {fmt_bytes(t['limit_bytes'])}")
    return out


def cache_totals(manifest: dict) -> tuple[int, int, int]:
    """(제외한 임시 저장물 폴더 수, 파일 수, 바이트). skipped 의 cache 항목 합계."""
    n = f = b = 0
    for s in manifest.get("skipped", []):
        if isinstance(s, dict) and s.get("cache"):
            n += 1
            f += int(s.get("files") or 0)
            b += int(s.get("bytes") or 0)
    return n, f, b


def cache_line(manifest: dict) -> str:
    n, f, b = cache_totals(manifest)
    if not n:
        return ""
    return f"재생성되는 임시 저장물 폴더 {n}개(파일 {f}개, {fmt_bytes(b)})를 제외했습니다(--include-caches 로 포함 가능)."


# --------------------------------------------------------------------------- 빠진 파일 요약(허용 방식, 표시 전용)
NOEXT_HINT = "설정일 수 있는 확장자 없는 파일이 {n}개 빠졌습니다. 필요하면 폴더 전체 담기를 켜고 다시 내보내 주세요."
NOEXT_HINT_CLI = "설정일 수 있는 확장자 없는 파일이 {n}개 빠졌습니다. 필요하면 --extra-mode all 로 폴더 전체를 담아 다시 내보내 주세요."
REASON_SHOWN = 3  # 한 줄 요약에 적는 사유 가짓수(나머지는 '외 N가지')


def _clean_reasons(v: object) -> dict[str, int]:
    return dict(settings_filter.ordered_reasons(v)) if isinstance(v, dict) else {}


def _clean_examples(v: object) -> list[dict]:
    """manifest 의 예시를 표시용으로 거른다(이전 버전/손상된 값 허용, 최대 EXAMPLES_KEEP 개)."""
    out: list[dict] = []
    for e in v if isinstance(v, list) else []:
        if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"]:
            out.append({"name": e["name"], "size": _num(e.get("size")),
                        "why": e["why"] if isinstance(e.get("why"), str) else settings_filter.WHY_OTHER})
    return out[:settings_filter.EXAMPLES_KEEP]


def reasons_text(by_reason: dict, shown: int = REASON_SHOWN) -> str:
    """{사유: 개수} -> '데이터베이스/데이터 12개, 확장자 없음(설정일 수 있음) 5개, 이미지·영상 340개 외 2가지'(많은 순)."""
    pairs = settings_filter.ordered_reasons(by_reason)
    text = ", ".join(f"{k} {n}개" for k, n in pairs[:shown])
    return text + (f" 외 {len(pairs) - shown}가지" if len(pairs) > shown else "")


def example_text(e: dict) -> str:
    return f"{e['name']} {fmt_bytes(_num(e.get('size')))} — {e.get('why', '')}"


def excluded_line(files: int, size: int, by_reason: dict) -> str:
    """'제외 4개, 3.0GB(데이터베이스/데이터 2개, …)'. 빠진 파일이 없으면 빈 글."""
    if not files:
        return ""
    why = reasons_text(by_reason)
    return f"제외 {files}개, {fmt_bytes(size)}" + (f"({why})" if why else "")


def excluded_fields(c: dict) -> dict:
    """설정 확인표 항목(coverage 의 c) 하나의 '빠진 파일' 세부. 이전 버전 manifest(키 없음)도 허용하며 없으면 개수/용량만 쓴다.
    excluded_files/bytes = 설정 형식이 아님 + 상한 초과. 빠진 것이 없으면 빈 dict."""
    files = _num(c.get("not_config_files")) + _num(c.get("limit_files"))
    if not files:
        return {}
    size = _num(c.get("not_config_bytes")) + _num(c.get("limit_bytes"))
    why = _clean_reasons(c.get("excluded_by_reason"))
    return {"excluded_files": files, "excluded_bytes": size, "excluded_by_reason": why,
            "excluded_examples": _clean_examples(c.get("excluded_examples")),
            "excluded_line": excluded_line(files, size, why),
            "noext_excluded": why.get(settings_filter.WHY_NOEXT, 0)}


def noext_total(table: list[dict]) -> int:
    """표(rows())에서 '확장자 없음'으로 빠진 파일 수의 합."""
    return sum(_num(r.get("noext_excluded")) for r in table)


def json_rows(manifest: dict) -> list[dict]:
    """--json/미리보기 JSON 용 행: program/what/status/files + (있으면) 빠진 파일 요약."""
    out = []
    for r in rows(manifest):
        d = {k: r[k] for k in ("program", "what", "status", "files")}
        if r.get("excluded_files"):
            d.update({k: r[k] for k in ("excluded_files", "excluded_bytes", "excluded_by_reason", "excluded_examples")})
        out.append(d)
    return out
