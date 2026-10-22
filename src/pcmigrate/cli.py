"""pcmigrate 명령행 진입점."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .copier import copy_tree
from .detect import (match_rules, read_registry_programs, winget_export_raw,
                     parse_winget_export)
from .package import PackageError, apply_import, export_package, plan_import
from .rules import RuleError, default_rules_dir, load_rules

NEEDS_CONFIRM = 2  # import 에 --yes 가 없어 복원하지 않고 종료


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
    return 0


def _cmd_copy(args: argparse.Namespace) -> int:
    report = copy_tree(Path(args.src), Path(args.dst), dry_run=args.dry_run)
    if args.report:
        report.write(Path(args.report))
    summary = report.to_dict()
    print(json.dumps({"dry_run": summary["dry_run"], "summary": summary["summary"]},
                     ensure_ascii=False))
    for r in report.failures:
        print(f"실패: {r.src} -> {r.error}", file=sys.stderr)
    return 1 if report.failures else 0


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        rules = load_rules(Path(args.rules))
    except RuleError as e:
        print(f"규칙 오류: {e}", file=sys.stderr)
        return 1
    winget_text = winget_export_raw()
    registry = read_registry_programs()
    matched = match_rules(rules, registry + (parse_winget_export(winget_text) if winget_text else []))
    if not matched:
        print("감지된 프로그램 규칙이 없어 프로그램 목록만 내보냅니다.", file=sys.stderr)
    if winget_text is None:
        print("경고: winget export 실패 - 레지스트리 목록만 포함됩니다.", file=sys.stderr)
    try:
        manifest = export_package(matched, Path(args.output), registry, winget_text)
    except (PackageError, OSError) as e:
        print(f"내보내기 실패: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"output": args.output, "rules": [r["id"] for r in manifest["rules"]],
                      "files": len(manifest["files"]), "skipped": len(manifest["skipped"]),
                      "failed": len(manifest["failed"])}, ensure_ascii=False))
    _print_warnings(manifest.get("manual_warnings", []))
    return 1 if manifest["failed"] else 0


def _print_warnings(warnings: list[dict]) -> None:
    for w in warnings:
        for item in w.get("items", []):
            print(f"[수동 작업 필요] {w.get('name')}: {item}", file=sys.stderr)
    print("[수동 작업 필요] 라이선스는 복원되지 않습니다. 각 프로그램을 재로그인/재활성화하세요.",
          file=sys.stderr)


def _cmd_import(args: argparse.Namespace) -> int:
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
        if args.dry_run:
            print("미리보기입니다. 실제 변경은 없었습니다.", file=sys.stderr)
            return 0
        print("미리보기만 출력했고 아무것도 쓰지 않았습니다. "
              "실제 복원하려면 --yes 를 붙여 다시 실행하세요.", file=sys.stderr)
        return NEEDS_CONFIRM
    print("실제 복원을 시작합니다. 덮어쓸 파일은 .bak 으로 백업됩니다.", file=sys.stderr)
    report = apply_import(Path(args.package), plan)
    if args.report:
        report.write(Path(args.report))
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

    e = sub.add_parser("export", help="설정 패키지(.zip) 내보내기")
    e.add_argument("--output", required=True, help="생성할 .zip 경로")
    e.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    e.set_defaults(func=_cmd_export)

    i = sub.add_parser("import", help="설정 패키지 가져오기(--dry-run 먼저 권장)")
    i.add_argument("package", help="export 로 만든 .zip")
    i.add_argument("--dry-run", action="store_true", help="변경 없이 미리보기만 출력(--yes 보다 우선)")
    i.add_argument("--yes", action="store_true", help="미리보기 확인 후 실제 복원 수행")
    i.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    i.add_argument("--report", help="결과 리포트(JSON) 저장 경로")
    i.set_defaults(func=_cmd_import)

    c = sub.add_parser("copy", help="폴더/파일을 해시 검증하며 복사")
    c.add_argument("src")
    c.add_argument("dst")
    c.add_argument("--dry-run", action="store_true", help="변경 없이 계획만 출력")
    c.add_argument("--report", help="결과 리포트(JSON) 저장 경로")
    c.set_defaults(func=_cmd_copy)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
