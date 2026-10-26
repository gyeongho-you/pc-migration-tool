"""프로그램별 '설정 확인표': 어떤 설정이 패키지에 담겼는지 사람이 읽는 표로 만든다(읽기 전용, 표시 전용).

내보낼 때 package 가 manifest["coverage"] 에 경로별 결과를 기록하고, 이 모듈이 그것을 표(행)와 문장으로 바꾼다.
이전 버전 패키지(coverage 항목 없음)도 읽을 수 있다: 규칙별 담긴 파일 수만 보여 준다.
"""
from __future__ import annotations

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
            if c.get("rule") == "install" and status in (ST_OK, ST_PART):
                note = (note + " / " if note else "") + INSTALL_DIR_NOTE
            out.append({"program": str(c.get("name") or c.get("rule") or "?"), "what": str(c.get("description") or c.get("path") or ""),
                        "status": status, "files": files, "note": note, "rule": str(c.get("rule", "")),
                        "path": str(c.get("path", ""))})
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
    if hidden:
        out.append(f"{indent}{folded_line(len(hidden))}")
        if expand_none:
            out += [f"{indent}{indent}· {r['program']} / {r['what']}" for r in hidden]
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
