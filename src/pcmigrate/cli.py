"""pcmigrate 명령행 진입점."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__
from .copier import copy_tree
from .checklist import (build_checklist, load_package_programs, manifest_warning_pairs,
                        render, rule_warning_pairs, write_output)
from .discover import (SelectionError, build_selection, discover, load_selection, secret_names,
                       summarize)
from .discover import render as render_discovery
from .detect import (match_rules, read_registry_programs, winget_export_raw, winget_list_raw,
                     parse_winget_export)
from .package import PackageError, apply_import, export_package, plan_import, verify_package
from .transfer import TransferError, transfer
from .rules import RuleError, default_rules_dir, load_rules

NEEDS_CONFIRM = 2  # import 에 --yes 가 없어 복원하지 않고 종료

# 셸이 확장하지 못한 환경변수(cmd 의 %VAR%, PowerShell 의 $env:VAR)가 그대로 들어온 경로
_UNEXPANDED = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%|\$env:", re.I)
_SHELL_HINT = ("힌트: cmd 에서는 %TEMP%, PowerShell 에서는 $env:TEMP 를 쓰세요"
               "(PowerShell 에서 %TEMP% 는, cmd 에서 $env:TEMP 는 확장되지 않습니다. Git Bash 는 $TEMP).")


def _cause(exc: OSError) -> str:
    return getattr(exc, "strerror", None) or str(exc)


def _write_failed(path: str, exc: OSError) -> int:
    """파일 쓰기 실패를 traceback 대신 한 줄로 알린다."""
    path = exc.filename if isinstance(getattr(exc, "filename", None), str) else path
    print(f"출력 경로를 만들 수 없습니다: {path} ({_cause(exc)})", file=sys.stderr)
    if _UNEXPANDED.search(path):
        print(_SHELL_HINT, file=sys.stderr)
    elif isinstance(exc, FileNotFoundError) or getattr(exc, "winerror", None) in (3, 15):
        print("힌트: 드라이브 문자와 폴더 이름이 맞는지 확인하세요(존재하지 않는 드라이브/폴더일 수 있습니다).",
              file=sys.stderr)
    return 1


def _unexpanded(**paths: str | None) -> bool:
    """환경변수가 확장되지 않은 경로 인자가 있으면 알리고 True(그대로 쓰면 '%TEMP%' 폴더가 생길 수 있음)."""
    for label, value in paths.items():
        if value and _UNEXPANDED.search(value):
            print(f"출력 경로를 만들 수 없습니다: {value} (환경변수가 확장되지 않았습니다: --{label})",
                  file=sys.stderr)
            print(_SHELL_HINT, file=sys.stderr)
            return True
    return False


def programs_registry(programs):
    return [p for p in programs if p.source == "registry"]


def winget_programs(programs):
    return [p for p in programs if p.source == "winget"]


def _print_discovery_hint(rules, programs) -> tuple[int, int] | None:
    """규칙 없는 설치 프로그램/설정 폴더 후보 수를 알린다(크기 계산 없이 개수만)."""
    try:
        n, m = summarize(rules, programs)
    except OSError:
        return None
    print(f"규칙 없는 설치 프로그램 {n}개, 설정 폴더 후보 {m}개 — pcmigrate discover 로 확인하세요"
          "(자동으로 포함되지 않습니다).", file=sys.stderr)
    return n, m


def summarize_programs_only(rules, programs) -> int:
    from .discover import program_has_rule
    from .checklist import bundle_reason
    seen: set[str] = set()
    n = 0
    for p in programs:
        if p.name in seen:
            continue
        seen.add(p.name)
        if not program_has_rule(p, rules) and not bundle_reason(p.name):
            n += 1
    return n


def _print_checklist_hint(cl) -> None:
    print(f"수동 작업 {cl.count}건, pcmigrate checklist 로 확인하세요.", file=sys.stderr)


def _detect(winget_text: str | None = None):
    programs = read_registry_programs()
    return programs + (parse_winget_export(winget_text) if winget_text else [])


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        rules = load_rules(Path(args.rules))
    except RuleError as e:
        print(f"규칙 오류: {e}", file=sys.stderr)
        return 1
    programs = _detect(winget_export_raw())
    matched = match_rules(rules, programs)
    out = {
        "program_count": len(programs),
        "matched_rules": [
            {"id": r.id, "name": r.name, "manual_warnings": list(r.manual_warnings),
             "notes": r.notes} for r in matched],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    _print_checklist_hint(build_checklist(rule_warning_pairs(matched), programs_registry(programs),
                                          winget_programs(programs), winget_list_raw(),
                                          rule_ids=[r.id for r in matched]))
    _print_discovery_hint(rules, programs_registry(programs))
    return 0


def _cmd_copy(args: argparse.Namespace) -> int:
    if _unexpanded(src=args.src, dst=args.dst, report=args.report):
        return 1
    report = copy_tree(Path(args.src), Path(args.dst), dry_run=args.dry_run)
    if args.report:
        try:
            report.write(Path(args.report))
        except OSError as e:
            return _write_failed(args.report, e)
    summary = report.to_dict()
    print(json.dumps({"dry_run": summary["dry_run"], "summary": summary["summary"]},
                     ensure_ascii=False))
    for r in report.failures:
        print(f"실패: {r.src} -> {r.error}", file=sys.stderr)
    return 1 if report.failures else 0


def _cmd_transfer(args: argparse.Namespace) -> int:
    def warn(msg: str) -> None:
        print(f"[경고] {msg}", file=sys.stderr)

    if _unexpanded(src=args.src, dst=args.dst, report=args.report, log=args.log):
        return 1
    try:
        report = transfer(Path(args.src), Path(args.dst), dry_run=args.dry_run,
                          include_cloud=args.include_cloud,
                          log_path=Path(args.log) if args.log else None, warn=warn,
                          strict=args.strict)
    except TransferError as e:
        print(f"전송 실패: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        return _write_failed(args.dst, e)
    if args.report:
        try:
            report.write(Path(args.report))
        except OSError as e:
            return _write_failed(args.report, e)
    d = report.to_dict()
    print(json.dumps({"dry_run": d["dry_run"], "summary": d["summary"],
                      "robocopy_exit": d.get("robocopy_exit"),
                      "cloud_only_count": d["cloud_only_count"]}, ensure_ascii=False))
    for r in report.failures[:50]:
        print(f"실패: {r.src} -> {r.error}", file=sys.stderr)
    return 1 if report.failures else 0


def _cmd_checklist(args: argparse.Namespace) -> int:
    if _unexpanded(output=args.output, package=args.package):
        return 1
    rule_ids: list[str] = []
    if args.package:
        manifest, problems = verify_package(Path(args.package))
        if manifest is None or problems:
            print("패키지 검증 실패: " + "; ".join(problems[:3]), file=sys.stderr)
            return 1
        registry, winget, list_text = load_package_programs(Path(args.package))
        pairs = manifest_warning_pairs(manifest)
        rule_ids = [r["id"] for r in manifest.get("rules", [])]
    else:
        try:
            rules = load_rules(Path(args.rules))
        except RuleError as e:
            print(f"규칙 오류: {e}", file=sys.stderr)
            return 1
        winget_text = winget_export_raw()
        winget = parse_winget_export(winget_text) if winget_text else []
        list_text = winget_list_raw() if winget_text else None
        registry = read_registry_programs()
        matched = match_rules(rules, registry + winget)
        pairs = rule_warning_pairs(matched)
        rule_ids = [r.id for r in matched]
    disc = None
    try:
        all_rules = load_rules(Path(args.rules))
    except RuleError:
        all_rules = None
    if all_rules is not None:
        if args.package:  # 구 PC 의 목록 기준: 폴더 후보는 이 PC 에서 알 수 없으므로 프로그램 수만
            disc = (summarize_programs_only(all_rules, registry), None)
        else:
            disc = summarize(all_rules, registry)
    text = render(build_checklist(pairs, registry, winget, list_text, rule_ids=rule_ids,
                                  show_all=args.all, discovery=disc), args.format)
    if args.output:
        try:
            write_output(Path(args.output), text)
        except OSError as e:
            return _write_failed(args.output, e)
        print(f"체크리스트를 저장했습니다: {args.output}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
        print(text, end="")
    if disc is not None:
        n, m = disc
        extra = f", 설정 폴더 후보 {m}개" if m is not None else ""
        print(f"규칙 없는 설치 프로그램 {n}개{extra} — pcmigrate discover 로 확인하세요.", file=sys.stderr)
    return 0


def _load_extras(args: argparse.Namespace):
    """--extra 선택 파일을 검증하고 비밀 정보 가능성을 다시 검사한다. (항목, 오류 코드) 반환."""
    if not args.extra:
        return [], 0
    try:
        items = load_selection(Path(args.extra))
    except SelectionError as e:
        print(f"선택 파일 오류: {e}", file=sys.stderr)
        return None, 1
    risky = []
    for it in items:
        names, complete = secret_names(it)
        if names or not complete:
            risky.append((it, names, complete))
    if risky and not args.allow_secrets:
        for it, names, complete in risky:
            why = ", ".join(names) if names else "비밀 정보 검사를 끝내지 못함"
            print(f"비밀 정보 가능성: {it.path} ({why})", file=sys.stderr)
        print("비밀 정보(키, 토큰, 쿠키, 비밀번호 등)가 들어 있을 수 있는 항목이 선택되었습니다. 패키지는 만들지 않았습니다. "
              "정말 포함하려면 내용을 확인한 뒤 --allow-secrets 를 붙여 다시 실행하세요.", file=sys.stderr)
        return None, 1
    if risky:
        print(f"[주의] --allow-secrets: 비밀 정보 가능성이 있는 항목 {len(risky)}개를 패키지에 포함합니다. "
              "패키지 파일을 안전하게 보관하세요.", file=sys.stderr)
    return items, 0


def _cmd_discover(args: argparse.Namespace) -> int:
    if _unexpanded(output=args.output, output_selection=args.output_selection):
        return 1
    try:
        rules = load_rules(Path(args.rules))
    except RuleError as e:
        print(f"규칙 오류: {e}", file=sys.stderr)
        return 1
    d = discover(rules, read_registry_programs(), min_size_kb=args.min_size_kb, show_all=args.all)
    text = render_discovery(d, args.format, args.all)
    if args.output:
        try:
            write_output(Path(args.output), text)
        except OSError as e:
            return _write_failed(args.output, e)
        print(f"탐지 결과를 저장했습니다: {args.output}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
        print(text, end="")
    if args.output_selection:
        sel = json.dumps(build_selection(d, args.all), ensure_ascii=False, indent=2) + "\n"
        try:
            write_output(Path(args.output_selection), sel)
        except OSError as e:
            return _write_failed(args.output_selection, e)
        print(f"선택 파일 템플릿을 저장했습니다(모두 include:false): {args.output_selection}", file=sys.stderr)
    print(f"규칙 없는 설치 프로그램 {d.program_count}개, 설정 폴더 후보 {d.candidate_count}개"
          f"(비밀 정보 가능성 {d.secret_count}개).", file=sys.stderr)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    if _unexpanded(output=args.output, extra=args.extra):
        return 1
    extras, rc = _load_extras(args)
    if extras is None:
        return rc
    try:
        rules = load_rules(Path(args.rules))
    except RuleError as e:
        print(f"규칙 오류: {e}", file=sys.stderr)
        return 1
    winget_text = winget_export_raw()
    registry = read_registry_programs()
    list_text = winget_list_raw() if winget_text else None
    matched = match_rules(rules, registry + (parse_winget_export(winget_text) if winget_text else []))
    if not matched:
        print("감지된 프로그램 규칙이 없어 프로그램 목록만 내보냅니다.", file=sys.stderr)
    if winget_text is None:
        print("경고: winget export 실패 - 레지스트리 목록만 포함됩니다.", file=sys.stderr)
    try:
        manifest = export_package(matched, Path(args.output), registry, winget_text,
                                  winget_list_text=list_text, extras=extras)
    except PackageError as e:
        print(f"내보내기 실패: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        return _write_failed(args.output, e)
    print(json.dumps({"output": args.output, "rules": [r["id"] for r in manifest["rules"]],
                      "files": len(manifest["files"]), "skipped": len(manifest["skipped"]),
                      "failed": len(manifest["failed"])}, ensure_ascii=False))
    _print_warnings(manifest.get("manual_warnings", []))
    _print_checklist_hint(build_checklist(manifest_warning_pairs(manifest), registry,
                                          parse_winget_export(winget_text) if winget_text else [],
                                          list_text, rule_ids=[r["id"] for r in manifest["rules"]]))
    _print_discovery_hint(rules, registry)
    return 1 if manifest["failed"] else 0


def _print_warnings(warnings: list[dict]) -> None:
    for w in warnings:
        for item in w.get("items", []):
            print(f"[수동 작업 필요] {w.get('name')}: {item}", file=sys.stderr)
    print("[수동 작업 필요] 라이선스는 복원되지 않습니다. 각 프로그램을 재로그인/재활성화하세요.",
          file=sys.stderr)


def _cmd_import(args: argparse.Namespace) -> int:
    if _unexpanded(package=args.package, report=args.report):
        return 1
    try:
        rules = load_rules(Path(args.rules))
    except RuleError as e:
        print(f"규칙 오류: {e}", file=sys.stderr)
        return 1
    plan = plan_import(Path(args.package), rules)
    summary: dict[str, int] = {}
    for it in plan.items:
        summary[it["status"]] = summary.get(it["status"], 0) + 1
    out = {"dry_run": args.dry_run, "summary": summary, "problems": plan.problems,
           "items": plan.items}
    if plan.problems:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print("패키지 검증 실패: 복원하지 않습니다.", file=sys.stderr)
        return 1
    if args.dry_run or not args.yes:  # --dry-run 이 --yes 보다 우선
        print(json.dumps(out, ensure_ascii=False, indent=2))
        _print_warnings(plan.warnings)
        n_extra = sum(1 for it in plan.items if it.get("extra"))
        if n_extra:
            print(f"[주의] 규칙 없이 선택 포함된 파일 {n_extra}개가 있습니다. 실제 복원은 --yes 와 --allow-extra 가 모두 필요합니다.",
                  file=sys.stderr)
        if args.dry_run:
            print("미리보기입니다. 실제 변경은 없었습니다.", file=sys.stderr)
            return 0
        print("미리보기만 출력했고 아무것도 쓰지 않았습니다. "
              "실제 복원하려면 --yes 를 붙여 다시 실행하세요.", file=sys.stderr)
        return NEEDS_CONFIRM
    n_extra = sum(1 for it in plan.items if it.get("extra"))
    if n_extra and not args.allow_extra:
        print(f"패키지에 규칙 없이 선택 포함된 파일 {n_extra}개가 있습니다. 내용을 미리보기로 확인한 뒤 "
              "--allow-extra 를 붙여 다시 실행하세요(복원하지 않았습니다).", file=sys.stderr)
        return 1
    print("실제 복원을 시작합니다. 덮어쓸 파일은 .bak 으로 백업됩니다.", file=sys.stderr)
    report = apply_import(Path(args.package), plan)
    if args.report:
        try:
            report.write(Path(args.report))
        except OSError as e:
            return _write_failed(args.report, e)
    print(json.dumps({"dry_run": False, "summary": report.to_dict()["summary"]}, ensure_ascii=False))
    for r in report.failures:
        print(f"실패: {r.dst} -> {r.error}", file=sys.stderr)
    _print_warnings(plan.warnings)
    return 1 if report.failures else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pcmigrate", description="PC 이전 도우미")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="설치 프로그램 감지 및 적용 규칙 표시")
    s.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    s.set_defaults(func=_cmd_scan)

    k = sub.add_parser("checklist", help="수동 작업/수동 설치 체크리스트 출력")
    k.add_argument("--format", choices=("text", "md"), default="text")
    k.add_argument("--output", help="파일로 저장(기존 파일은 .bak 보존)")
    k.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    k.add_argument("--all", action="store_true",
                   help="이 PC 에 없는 프로그램 관련 경고와 참고(설치 불필요 추정) 목록도 모두 표시")
    k.add_argument("--package", help="export 한 .zip 의 manifest/프로그램 목록 기준으로 작성(신 PC 용)")
    k.set_defaults(func=_cmd_checklist)

    dv = sub.add_parser("discover", help="규칙 없는 설치 프로그램과 설정 폴더 후보 탐지(읽기 전용)")
    dv.add_argument("--format", choices=("text", "md", "json"), default="text")
    dv.add_argument("--output", help="파일로 저장(기존 파일은 .bak 보존)")
    dv.add_argument("--min-size-kb", type=int, default=0, help="이 크기(KB)보다 작은 후보는 숨김(비밀 정보 가능성 제외)")
    dv.add_argument("--all", action="store_true", help="제조사 번들, 규칙이 있는 프로그램 쪽 폴더, 연결 못 한 폴더도 표시")
    dv.add_argument("--output-selection", help="export --extra 용 선택 파일 템플릿 생성(모두 include:false)")
    dv.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    dv.set_defaults(func=_cmd_discover)

    e = sub.add_parser("export", help="설정 패키지(.zip) 내보내기")
    e.add_argument("--output", required=True, help="생성할 .zip 경로")
    e.add_argument("--extra", help="discover --output-selection 으로 만들고 사용자가 고른 선택 파일(규칙 없는 폴더 포함)")
    e.add_argument("--allow-secrets", action="store_true",
                   help="선택 항목에 비밀 정보 가능성이 있어도 포함(내용을 확인한 경우에만)")
    e.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    e.set_defaults(func=_cmd_export)

    i = sub.add_parser("import", help="설정 패키지 가져오기(--dry-run 먼저 권장)")
    i.add_argument("package", help="export 로 만든 .zip")
    i.add_argument("--dry-run", action="store_true", help="변경 없이 미리보기만 출력(--yes 보다 우선)")
    i.add_argument("--yes", action="store_true", help="미리보기 확인 후 실제 복원 수행")
    i.add_argument("--allow-extra", action="store_true", help="규칙 없이 선택 포함된 파일의 복원을 허용")
    i.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    i.add_argument("--report", help="결과 리포트(JSON) 저장 경로")
    i.set_defaults(func=_cmd_import)

    t = sub.add_parser("transfer", help="모드 B: 대용량 폴더를 robocopy 로 전송 + SHA-256 검증")
    t.add_argument("src", help="원본 폴더(읽기만 함)")
    t.add_argument("dst", help="대상(로컬/외장 드라이브/UNC 경로)")
    t.add_argument("--report", help="결과 리포트(JSON) 저장 경로")
    t.add_argument("--log", help="robocopy 로그 경로(기본: 대상 폴더 안)")
    t.add_argument("--dry-run", action="store_true", help="robocopy /L 로 목록만 확인(쓰기 없음)")
    t.add_argument("--strict", action="store_true",
                   help="이어받기 시 상태 파일을 믿지 않고 모든 파일을 SHA-256 으로 다시 검증")
    t.add_argument("--include-cloud", action="store_true",
                   help="OneDrive 클라우드 전용 파일도 포함(대량 다운로드 발생)")
    t.set_defaults(func=_cmd_transfer)

    c = sub.add_parser("copy", help="소규모 폴더/파일을 해시 검증하며 복사(대용량은 transfer)")
    c.add_argument("src")
    c.add_argument("dst")
    c.add_argument("--dry-run", action="store_true", help="변경 없이 계획만 출력")
    c.add_argument("--report", help="결과 리포트(JSON) 저장 경로")
    c.set_defaults(func=_cmd_copy)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
