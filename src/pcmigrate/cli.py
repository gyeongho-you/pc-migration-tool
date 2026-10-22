"""pcmigrate 명령행 진입점."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .copier import copy_tree
from .detect import detect_programs, match_rules
from .rules import RuleError, default_rules_dir, load_rules

NOT_IMPLEMENTED = 2


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        rules = load_rules(Path(args.rules))
    except RuleError as e:
        print(f"규칙 오류: {e}", file=sys.stderr)
        return 1
    programs = detect_programs()
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


def _not_implemented(name: str):
    def run(_args: argparse.Namespace) -> int:
        print(f"'{name}' 는 아직 구현되지 않았습니다(다음 단계 예정).", file=sys.stderr)
        return NOT_IMPLEMENTED
    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pcmigrate", description="PC 이전 도우미")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="설치 프로그램 감지 및 적용 규칙 표시")
    s.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    s.set_defaults(func=_cmd_scan)

    e = sub.add_parser("export", help="설정 패키지 내보내기(미구현)")
    e.add_argument("--output")
    e.set_defaults(func=_not_implemented("export"))

    i = sub.add_parser("import", help="설정 패키지 가져오기(미구현)")
    i.add_argument("package", nargs="?")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(func=_not_implemented("import"))

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
