"""CLI 의 기본 출력: 사람이 읽는 한국어 요약(핵심 숫자 + 다음에 할 일 한 줄). JSON 은 --json 일 때만 나온다.

여기 함수는 문자열 목록만 만든다(출력은 cli.py). 결과 객체(api)와 리포트 dict 를 읽기만 한다.
"""
from __future__ import annotations

from . import coverage, fs
from .junk import summary_line as junk_summary_line
from .progress import fmt_bytes

_IMPORT_LABELS = (("would_copy", "새로 만듦"), ("would_overwrite", "덮어씀(.bak 으로 백업)"),
                  ("skipped_identical", "이미 같음(건너뜀)"), ("copied", "복원함"), ("denied", "복원 안 함(보안상 거부 형식)"),
                  ("rejected", "복원 안 함(링크를 거치는 위치)"), ("not_selected", "선택에서 제외됨(복원 안 함)"),
                  ("failed", "실패"))


def _q(path: str) -> str:
    return f'"{fs.plain_path_text(str(path))}"'


def _counts_line(counts: dict, labels) -> str:
    parts = [f"{label} {counts[k]}개" for k, label in labels if counts.get(k)]
    return ", ".join(parts) if parts else "해당 파일 없음"


def export_lines(r, output: str) -> list[str]:
    m = r.manifest
    rules = [x["id"] for x in m["rules"]]
    denied = len(r.denied)
    lines = [f"설정 패키지를 만들었습니다: {fs.plain_path_text(str(output))}",
             f"- 담은 파일 {len(m['files'])}개 (프로그램 규칙: {', '.join(rules) if rules else '없음'})",
             f"- 제외된 항목 {len(m['skipped'])}개(없는 경로, 라이선스, 링크 등)"
             + (f" — 이 중 보안상 복원하지 않는 형식 {denied}개" if denied else "")]
    cl = coverage.cache_line(m)
    if cl:
        lines.append(f"- {cl}")
    lines += coverage.config_lines(m, "- ")
    lines += coverage.lines(m)
    if r.unreadable:
        lines.append(f"- 사용 중이라 읽지 못한 파일 {len(r.unreadable)}개(해당 프로그램을 닫고 다시 실행하면 담깁니다)")
    if r.hard_failures:
        lines.append(f"- 실패 {len(r.hard_failures)}개(아래 '실패' 줄 참고)")
    lines.append(f"다음 할 일: 새 PC 로 옮긴 뒤 pcmigrate import {_q(output)} --dry-run 으로 미리 확인하고, 이상 없으면 --yes 로 복원하세요.")
    return lines


def program_table_lines(programs: list[dict]) -> list[str]:
    """프로그램별 복원 표: 프로그램 / 파일 수 / 새로 만듦 / 덮어씀(.bak) / 이미 같음 / 선택 여부."""
    if not programs:
        return []
    out = ["프로그램별(프로그램 id / 파일 수 / 새로 만듦 / 덮어씀 / 이미 같음):"]
    for p in programs:
        tail = "" if p["selected"] else " — 선택에서 제외됨(복원 안 함)"
        extra = (f", 복원 안 함(보안) {p['denied']}" if p["denied"] else "") + (f", 링크 거부 {p['rejected']}" if p["rejected"] else "")
        out.append(f"  {p['id']} ({p['name']}) / {p['files']}개 / 새로 {p['new']} / 덮어씀 {p['overwrite']} / 같음 {p['same']}{extra}{tail}")
    return out


def undo_lines(r, record_note: str = "") -> list[str]:
    """undo-import 결과 요약(미리보기/확인/적용)."""
    from . import import_record as ir
    p = r.plan
    s = p.summary()
    head = {"dry-run": "되돌리기 미리보기(아무것도 바꾸지 않았습니다)", "needs-confirm": "되돌리기 확인(아무것도 바꾸지 않았습니다)",
            "applied": "되돌리기를 마쳤습니다", "nothing": "되돌릴 파일이 없습니다"}[r.mode]
    lines = [f"{head}: {', '.join(p.programs) or '-'}", f"- 복원 기록: {fs.plain_path_text(p.record_path)}"]
    if r.written:
        done = sum(1 for i in p.items if i.result == ir.RESTORED)
        moved = sum(1 for i in p.items if i.result == ir.MOVED)
        bad = sum(1 for i in p.items if i.result == ir.FAILED_UNDO)
        lines.append(f"- 원래 이름으로 되돌림(.bak 복구) {done}개" + (f", 새 파일을 .removed 로 옮김 {moved}개" if moved else "")
                     + (f", 실패 {bad}개" if bad else ""))
    else:
        lines.append(f"- 되돌릴 파일(.bak 을 원래 이름으로) {s['restore']}개" + (f", .removed 로 옮길 새 파일 {s['new_moved']}개" if s["new_moved"] else ""))
    if s["new_kept"]:
        lines.append(f"- 새 파일 {s['new_kept']}개는 지우지 않았습니다(복원 때 새로 만든 파일이라 사용자 데이터 보호를 위해 그대로 둡니다). "
                     "필요하면 --move-new 로 '<이름>.removed' 로 옮길 수 있습니다.")
    if s["already_undone"]:
        lines.append(f"- 이미 되돌렸거나 제자리에 있어 건너뜀 {s['already_undone']}개")
    for i in p.problems:
        lines.append(f"- 되돌리지 않음: {fs.plain_path_text(i.target)} — {i.detail}")
    for i in p.items:
        if i.result == ir.FAILED_UNDO:
            lines.append(f"- 실패: {fs.plain_path_text(i.target)} — {i.detail}")
    if r.mode == "dry-run" and s["restore"] + s["new_moved"]:
        lines.append("다음 할 일: 이상 없으면 같은 명령에서 --dry-run 을 빼고 --yes 를 붙여 실행하세요.")
    elif r.mode == "needs-confirm":
        lines.append("다음 할 일: 실제로 되돌리려면 --yes 를 붙여 다시 실행하세요.")
    return lines


def import_lines(r, package: str, report=None) -> list[str]:
    plan = r.plan
    pkg = fs.plain_path_text(str(package))
    if r.mode == "blocked":
        lines = [f"패키지 검증 실패 — 복원하지 않았습니다: {pkg}"]
        lines += [f"- {p}" for p in plan.problems[:10]]
        if len(plan.problems) > 10:
            lines.append(f"- … 외 {len(plan.problems) - 10}건(전체는 --json 으로 확인)")
        lines.append("다음 할 일: 패키지를 다시 만들거나 파일이 손상되지 않았는지 확인하세요.")
        return lines
    denied = [it for it in plan.items if it["status"] == "denied"]
    if report is not None:
        counts: dict[str, int] = {}
        for x in report.results:
            counts[x.status] = counts.get(x.status, 0) + 1
        lines = [f"복원을 마쳤습니다: {pkg}", f"- {_counts_line(counts, _IMPORT_LABELS)}"]
    else:
        lines = [f"{'미리보기' if r.mode == 'dry-run' else '복원 전 확인'}(아무것도 쓰지 않았습니다): {pkg}",
                 f"- {_counts_line(r.summary(), _IMPORT_LABELS)}"]
    if denied:
        lines.append(f"- 보안상 복원하지 않는 형식 {len(denied)}개(나머지는 정상 복원됩니다):")
        lines += [f"    · {fs.plain_path_text(it['target'])}" for it in denied[:5]]
        if len(denied) > 5:
            lines.append(f"    … 외 {len(denied) - 5}개")
    lines += program_table_lines(plan.programs)
    lines += coverage.lines(plan.manifest)
    lines += install_lines(r, report)
    if r.git_config_warning:
        lines.append(f"- [주의] {r.git_config_warning}")
    if report is not None:
        bad = report.failures
        if bad:
            lines.append(f"- 문제가 있는 파일 {len(bad)}개(아래 '실패' 줄 참고)")
            lines.append("다음 할 일: 원인을 해결한 뒤 같은 명령을 다시 실행하세요(이미 복원된 파일은 건너뜁니다).")
        else:
            lines.append("다음 할 일: pcmigrate checklist 로 수동 작업(라이선스 재활성화, 재로그인 등)을 확인하세요.")
    elif r.mode == "needs-allow-extra":
        lines.append("다음 할 일: 내용을 확인한 뒤 --allow-extra 를 붙여 다시 실행하세요.")
    elif r.mode == "dry-run":
        lines.append(f"다음 할 일: 이상 없으면 pcmigrate import {_q(package)} --yes 로 실제 복원하세요.")
    else:
        lines.append(f"다음 할 일: 실제 복원하려면 --yes 를 붙여 다시 실행하세요(pcmigrate import {_q(package)} --yes).")
    return lines


_INSTALL_STATUS_LABELS = (("would_copy", "새로 만듦"), ("would_overwrite", "덮어씀(.bak 으로 백업)"), ("skipped_identical", "이미 같음(건너뜀)"),
                          ("denied", "복원 안 함(설정 형식이 아니거나 허용되지 않은 위치)"), ("rejected", "복원 안 함(링크를 거치는 위치)"),
                          ("missing_dir", "복원 안 함(프로그램 폴더 없음)"), ("missing_program", "복원 안 함(프로그램이 설치되어 있지 않음)"))


def install_lines(r, report=None) -> list[str]:
    """복원 요약의 설치 폴더 설정 줄. 패키지에 설치 폴더 설정이 없으면 빈 목록."""
    from . import install_dir
    if not getattr(r, "n_install", 0):
        return []
    counts = r.install_counts()
    head = f"- 설치 폴더 설정 {r.n_install}개: " + _counts_line(counts, _INSTALL_STATUS_LABELS)
    if not r.install_allowed:
        return [head, "  기본은 복원 안 함입니다. 복원하려면 --allow-install-dir 를 붙이고 관리자 권한으로 실행하세요."]
    lines = [head]
    for p in getattr(r.plan, "install_programs", []):
        old, new = p.get("old_version") or "?", p.get("new_version") or "?"
        if p.get("old_version") and p.get("new_version") and old != new:
            lines.append(f"  [주의] {p.get('program') or p.get('root')}: 구 PC 버전 {old} / 이 PC 버전 {new} — 버전이 달라 오작동할 수 있습니다")
    missing = [v for v in r.install_view if v["status"] in (install_dir.MISSING_DIR, install_dir.MISSING_PROGRAM)]
    if missing:
        lines.append(f"  - {install_dir.INSTALL_FIRST_NOTE}: " + ", ".join(sorted({v.get('program') or v['entry'] for v in missing})[:5]))
    return lines


def install_notes(r) -> list[str]:
    """import 의 stderr 안내(설치 폴더 설정). 없으면 빈 목록."""
    from . import install_dir
    if not getattr(r, "n_install", 0):
        return []
    out = [f"[주의] {install_dir.VERSION_WARNING}."]
    if not r.install_allowed:
        out.append(f"[안내] 설치 폴더 설정 {r.n_install}개는 복원하지 않습니다. {install_dir.OFF_NOTE}.")
    else:
        out.append("[안내] 설치 폴더 설정은 설정 형식 파일만 쓰고, 기존 파일은 .bak 으로 보존하며, 프로그램 폴더가 없으면 건너뜁니다.")
    if any(v["status"] in (install_dir.MISSING_DIR, install_dir.MISSING_PROGRAM) for v in r.install_view):
        out.append(f"[안내] 대상 프로그램 폴더가 없는 항목이 있습니다. {install_dir.INSTALL_FIRST_NOTE}.")
    return out


def install_export_notes(manifest: dict) -> list[str]:
    """export 의 stderr 안내(설치 폴더 설정을 담았을 때). 없으면 빈 목록."""
    from . import install_dir
    info = manifest.get(install_dir.MANIFEST_KEY)
    if not isinstance(info, dict):
        return []
    n = sum(1 for f in manifest.get("files", []) if isinstance(f, dict) and f.get("rule") == install_dir.INSTALL_ID)
    out = [f"[안내] 설치 폴더 설정 {n}개를 담았습니다. {install_dir.MANIFEST_NOTE}", f"[주의] {install_dir.VERSION_WARNING}."]
    big = [s for s in manifest.get("skipped", []) if isinstance(s, dict) and s.get("rule") == install_dir.INSTALL_ID and not s.get("denied")
           and ("상한" in s.get("reason", "") or "너무 큼" in s.get("reason", ""))]
    if big:
        out.append(f"[주의] 크기/개수 상한으로 제외한 설치 폴더 설정 파일 {len(big)}개가 있습니다(패키지 manifest 'skipped' 참고).")
    return out


def verify_lines(d: dict, rep) -> list[str]:
    counts = d.get("summary", {})
    bad = len(rep.failures)
    head = "검사 결과: 모든 파일이 정확합니다." if not bad and d.get("manifest_complete", True) else \
        ("검사 결과: 문제가 있습니다." if bad else "검사 결과: 검사한 파일은 정확하지만 목록이 완전하지 않습니다.")
    lines = [head,
             f"- 정확함 {counts.get('verified', 0)}개, 내용 다름 {counts.get('mismatch', 0)}개, 없음 {counts.get('missing', 0)}개, "
             f"목록에 없는 추가 파일 {counts.get('extra', 0)}개"]
    if d.get("manifest_errors"):
        lines.append(f"- 구 PC 에서 읽지 못해 목록에서 빠진 파일 {d['manifest_errors']}개(검사되지 않음)")
    lines.append("다음 할 일: 실패 0 을 확인하기 전에는 구 PC 의 원본을 지우지 마세요." if bad or d.get("manifest_errors")
                 else "다음 할 일: 실패 0 입니다. 필요하면 --report 로 결과를 보관하고, 그 뒤에 원본 정리를 판단하세요.")
    return lines


def split_line(d: dict, done: bool) -> list[str]:
    """--split-large 로 나눈(나눌) 파일 요약(조각 개수/크기). 나눈 파일이 없으면 빈 목록."""
    n = d.get("split_files") or 0
    if not n:
        return []
    verb = "나눠 저장했습니다" if done else "나눠 저장합니다"
    line = (f"- 4GB 넘는 파일 {n}개({fmt_bytes(d.get('split_bytes') or 0)})를 조각 {d.get('split_parts', 0)}개"
            f"(조각 하나 최대 {fmt_bytes(d.get('split_part_size') or 0)}, 이름은 <원래 이름>.pcm001 ...)로 {verb}")
    if done and d.get("split_kept_parts"):
        line += f" — 이미 있던 조각 {d['split_kept_parts']}개는 건너뜀"
    return [line, "  새 PC 에서 pcmigrate join <조각 폴더> --output <합칠 폴더> 로 합쳐야 원래 파일이 됩니다(NTFS 드라이브에 합치세요)."]


def join_lines(d: dict, rep, src: str, output: str) -> list[str]:
    """pcmigrate join 의 사람이 읽는 요약."""
    counts = d.get("summary", {})
    bad = len(rep.failures)
    out = fs.plain_path_text(str(output))
    if d.get("dry_run"):
        return [f"미리보기입니다(아무것도 합치지 않았습니다): {fs.plain_path_text(str(src))} → {out}",
                f"- 합칠 파일 {d.get('groups', 0)}개(조각 {d.get('joined_parts') or 0}개, {fmt_bytes(d.get('join_bytes') or 0)}): "
                f"새로 만듦 {counts.get('would_copy', 0)}개, 덮어씀(.bak 으로 백업) {counts.get('would_overwrite', 0)}개"
                + (f", 문제 {bad}개" if bad else ""),
                "다음 할 일: 이상 없으면 같은 명령에서 --dry-run 만 빼고 실행하세요."]
    ok = d.get("joined_files", 0)
    head = "합치기를 마쳤습니다" if not bad else "합치기에서 문제가 있습니다"
    lines = [f"{head}: {fs.plain_path_text(str(src))} → {out}",
             f"- 합친 파일 {ok}개(조각 {d.get('joined_parts', 0)}개, {fmt_bytes(d.get('join_bytes') or 0)}), 문제 {bad}개"
             + (f", 이미 같은 파일이 있어 건너뜀 {counts.get('skipped_identical', 0)}개" if counts.get("skipped_identical") else "")]
    src_kind = {"manifest": "해시 목록(전체/조각 해시)", "state": "전송 상태 파일의 조각 해시", "none": "없음"}.get(d.get("hash_source", "none"), "없음")
    lines.append(f"- 내용 대조 기준: {src_kind}"
                 + (f" — 대조하지 못한 파일 {d['unverified_files']}개(이어 붙인 뒤 다시 읽어 확인만 함)" if d.get("unverified_files") else ""))
    for n in d.get("notes", []):
        lines.append(f"- 안내: {n}")
    if bad:
        lines.append("다음 할 일: 원인을 해결하고(빠진 조각은 다시 복사) 같은 명령을 다시 실행하세요. 조각은 지우지 않았습니다.")
    else:
        lines.append("다음 할 일: 합친 파일을 확인한 뒤 조각 파일(.pcmNNN)은 직접 지워 공간을 확보하세요(도구는 지우지 않습니다). "
                     "목록이 있으면 pcmigrate verify --manifest <목록> --target <합친 폴더> 로 한 번 더 확인할 수 있습니다.")
    return lines


def transfer_lines(d: dict, rep, src: str, dst: str) -> list[str]:
    counts = d.get("summary", {})
    total, tb = d.get("total_files"), d.get("total_bytes")
    size = f" / {fmt_bytes(tb)}" if tb is not None else ""
    if d.get("dry_run"):
        return [f"미리보기입니다(아무것도 복사하지 않았습니다): {fs.plain_path_text(str(src))} → {fs.plain_path_text(str(dst))}",
                f"- 전체 {total if total is not None else '?'}개{size}: 새로 복사 {counts.get('would_copy', 0)}개, "
                f"덮어쓰기 전 .bak 보존 {counts.get('would_overwrite', 0)}개, 이미 같음 {counts.get('skipped_identical', 0)}개",
                *( [f"- OneDrive 클라우드 전용(건너뜀) {d['cloud_only_count']}개"] if d.get("cloud_only_count") else []),
                *split_line(d, False),
                *junk_summary_line(d),
                "다음 할 일: 이상 없으면 같은 명령에서 --dry-run 만 빼고 실행하세요."]
    mode = d.get("mode")
    verb = {"verify-only": "검사를 마쳤습니다", "retry-failed": "실패한 파일을 다시 처리했습니다"}.get(mode, "전송을 마쳤습니다")
    bad = len(rep.failures)
    lines = [f"{verb}: {fs.plain_path_text(str(src))} → {fs.plain_path_text(str(dst))}",
             f"- 파일 {total if total is not None else '?'}개{size}: 정확함 {counts.get('verified', 0)}개"
             + (f", 검사 보류 {counts.get('pending_verify', 0)}개" if counts.get("pending_verify") else "")
             + f", 실패 {bad}개"
             + (f", 클라우드 전용 건너뜀 {counts.get('cloud_only', 0)}개" if counts.get("cloud_only") else "")]
    lines += split_line(d, True)
    lines += junk_summary_line(d)
    trusted = d.get("trusted_state_files")
    if trusted and not d.get("strict"):
        lines.append(f"- 이전 검사 기록을 믿고 다시 읽지 않은 파일 {trusted}개(크기·수정시각이 같음). "
                     "내용까지 모두 다시 확인하려면 --strict 로 실행하세요.")
    if bad:
        lines.append("다음 할 일: 원인을 해결한 뒤 같은 명령에 --retry-failed 를 붙여 실패한 파일만 다시 처리하세요.")
    elif d.get("dest_verification") == "deferred":
        lines.append("다음 할 일: 복사만 끝났습니다. 새 PC 에서 pcmigrate verify 로 검사해야 정확한지 알 수 있습니다(그 전에는 원본을 지우지 마세요).")
    elif d.get("verification_complete"):
        lines.append("다음 할 일: 검사까지 통과했습니다. 대상 폴더의 .pcmigrate-* 기록 파일은 지워도 됩니다"
                     "(--clean-state 로 자동 삭제). 원본 정리는 충분히 확인한 뒤 판단하세요.")
    return lines


def retry_lines(r, output: str) -> list[str]:
    """'읽지 못해 제외된 파일만 다시 담기' 결과 요약(다시 담김 N개, 아직 못 담음 M개와 이유, 다음 할 일)."""
    from .failure import REASONS
    info = r.info
    out = fs.plain_path_text(str(output))
    if info.get("total", 0) == 0:
        return [f"다시 담을 파일이 없습니다(읽지 못해 제외된 파일이 없습니다): {out}",
                f"다음 할 일: 새 PC 로 옮긴 뒤 pcmigrate import {_q(output)} --dry-run 으로 미리 확인하세요."]
    added, still, gone = len(r.added), r.still_failed, info.get("gone", [])
    lines = [f"설정 묶음 파일에 다시 담았습니다: {out}" if added or gone else f"설정 묶음 파일을 바꾸지 않았습니다: {out}",
             f"- 다시 담김 {added}개, 아직 못 담음 {len(still)}개" + (f", 원본이 없어져 목록에서 뺌 {len(gone)}개" if gone else "")
             + f" (묶음 전체 {len(r.manifest['files'])}개 파일)"]
    if added:
        lines.append("- 다시 담은 파일은 지금 원본의 내용입니다(처음 내보낸 뒤 바뀌었을 수 있습니다).")
    if info.get("backup"):
        lines.append(f"- 이전 묶음은 {fs.plain_path_text(info['backup'])} 로 보관했습니다.")
    if still:
        by: dict[str, int] = {}
        for f in still:
            by[f.get("reason", "other")] = by.get(f.get("reason", "other"), 0) + 1
        lines.append("- 못 담은 이유: " + ", ".join(
            f"{REASONS[c].label if c in REASONS else c} {n}개" for c, n in by.items()))
        lines += [f"    · {f['src']}" for f in still[:5]]
        if len(still) > 5:
            lines.append(f"    … 외 {len(still) - 5}개")
    for f in info.get("not_retried", [])[:5]:
        lines.append(f"- 다시 담지 않음: {f['src']} ({f.get('note', '')})")
    if info.get("missing_rules"):
        lines.append("- 이 PC 의 규칙 폴더에 없는 규칙이라 다시 찾지 못함: " + ", ".join(info["missing_rules"]))
    if still:
        lines.append("다음 할 일: 해당 프로그램(예: Chrome)을 닫은 뒤 같은 명령(--retry-failed)을 다시 실행하세요.")
    else:
        lines.append(f"다음 할 일: 새 PC 로 옮긴 뒤 pcmigrate import {_q(output)} --dry-run 으로 미리 확인하고, 이상 없으면 --yes 로 복원하세요.")
    return lines
