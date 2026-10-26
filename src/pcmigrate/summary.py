"""CLI 의 기본 출력: 사람이 읽는 한국어 요약(핵심 숫자 + 다음에 할 일 한 줄). JSON 은 --json 일 때만 나온다.

여기 함수는 문자열 목록만 만든다(출력은 cli.py). 결과 객체(api)와 리포트 dict 를 읽기만 한다.
"""
from __future__ import annotations

from . import coverage, fs
from .progress import fmt_bytes

_IMPORT_LABELS = (("would_copy", "새로 만듦"), ("would_overwrite", "덮어씀(.bak 으로 백업)"),
                  ("skipped_identical", "이미 같음(건너뜀)"), ("copied", "복원함"), ("denied", "복원 안 함(보안상 거부 형식)"),
                  ("rejected", "복원 안 함(링크를 거치는 위치)"), ("failed", "실패"))


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
    lines += coverage.lines(m)
    if r.unreadable:
        lines.append(f"- 사용 중이라 읽지 못한 파일 {len(r.unreadable)}개(해당 프로그램을 닫고 다시 실행하면 담깁니다)")
    if r.hard_failures:
        lines.append(f"- 실패 {len(r.hard_failures)}개(아래 '실패' 줄 참고)")
    lines.append(f"다음 할 일: 새 PC 로 옮긴 뒤 pcmigrate import {_q(output)} --dry-run 으로 미리 확인하고, 이상 없으면 --yes 로 복원하세요.")
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
    lines += coverage.lines(plan.manifest)
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
