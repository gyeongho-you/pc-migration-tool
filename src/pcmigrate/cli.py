"""pcmigrate 명령행 진입점. 인자 파싱과 터미널 출력만 담당하고, 실행 로직은 api.py 에 있다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, envpaths
from .api import (NEEDS_CONFIRM, ApiError, Deps, check_unexpanded, format_write_error, run_checklist,
                  run_copy, run_discover, run_export, run_import, run_scan, run_transfer, selection_json)
from .checklist import write_output
from .detect import read_registry_programs, winget_export_raw, winget_list_raw
from .discover import render as render_discovery
from .discover import summarize
from .package import export_package, plan_import
from .rules import default_rules_dir
from .transfer import TransferError, transfer


def _deps() -> Deps:
    """이 모듈의 현재 이름들을 주입한다(테스트가 cli.<이름> 을 바꿔치기할 수 있도록 호출 시점에 조회)."""
    return Deps(registry=read_registry_programs, winget_export=winget_export_raw,
                winget_list=winget_list_raw, summarize=summarize, export=export_package,
                plan=plan_import, transfer=transfer)


def _err(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr)


def _configure_output() -> None:
    """stdout/stderr(그리고 argparse 도움말)가 같은 인코딩이 되게 한다.

    - 콘솔(tty)에 연결된 스트림은 그대로 둔다: Python 은 Windows 콘솔을 유니코드 API 로 쓰므로 코드페이지와 무관하게 깨지지 않는다.
    - 파이프/리다이렉트된 스트림은 UTF-8(errors=replace)로 통일한다: 기본 로케일(cp949)로 두면 stdout 과 stderr 가
      섞여 읽기 어렵고, 파일 출력(--output)이 UTF-8 이라 서로 어긋난다. 변환 불가 문자도 예외 없이 대체된다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                continue
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _write_failed(path: str, exc: OSError) -> int:
    _err(*format_write_error(path, exc))
    return 1


def _unexpanded(**paths: str | None) -> bool:
    lines = check_unexpanded(**paths)
    _err(*lines)
    return bool(lines)


def _fail(e: ApiError) -> int:
    _err(*e.lines, e.message)
    return e.code


def _print_hint(label: str, n: int, m: int | None) -> None:
    extra = f", 설정 폴더 후보 {m}개" if m is not None else ""
    print(f"규칙 없는 설치 프로그램 {n}개{extra} — pcmigrate discover 로 확인하세요{label}", file=sys.stderr)


def _print_checklist_hint(cl) -> None:
    print(f"수동 작업 {cl.count}건, pcmigrate checklist 로 확인하세요.", file=sys.stderr)


def _print_warnings(warnings: list[dict]) -> None:
    for w in warnings:
        for item in w.get("items", []):
            print(f"[수동 작업 필요] {w.get('name')}: {item}", file=sys.stderr)
    print("[수동 작업 필요] 라이선스는 복원되지 않습니다. 각 프로그램을 재로그인/재활성화하세요.",
          file=sys.stderr)


# --------------------------------------------------------------------------- 서브커맨드(출력만)

def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        r = run_scan(args.rules, _deps())
    except ApiError as e:
        return _fail(e)
    print(json.dumps(r.summary_dict(), ensure_ascii=False, indent=2))
    _print_checklist_hint(r.checklist)
    if r.discovery:
        _print_hint("(자동으로 포함되지 않습니다).", *r.discovery)
    return 0


def _cmd_copy(args: argparse.Namespace) -> int:
    if _unexpanded(src=args.src, dst=args.dst, report=args.report):
        return 1
    report = run_copy(args.src, args.dst, dry_run=args.dry_run)
    if args.report:
        try:
            report.write(Path(args.report))
        except OSError as e:
            return _write_failed(args.report, e)
    summary = report.to_dict()
    print(json.dumps({"dry_run": summary["dry_run"], "summary": summary["summary"]}, ensure_ascii=False))
    for r in report.failures:
        print(f"실패: {r.src} -> {r.error}", file=sys.stderr)
    return 1 if report.failures else 0


def _cmd_transfer(args: argparse.Namespace) -> int:
    def warn(msg: str) -> None:
        print(f"[경고] {msg}", file=sys.stderr)

    if _unexpanded(src=args.src, dst=args.dst, report=args.report, log=args.log, state_file=args.state_file):
        return 1
    try:
        report = run_transfer(args.src, args.dst, dry_run=args.dry_run, include_cloud=args.include_cloud,
                              log_path=args.log, strict=args.strict, state_path=args.state_file, warn=warn,
                              deps=_deps())
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
    print(json.dumps({"dry_run": d["dry_run"], "summary": d["summary"], "robocopy_exit": d.get("robocopy_exit"),
                      "cloud_only_count": d["cloud_only_count"]}, ensure_ascii=False))
    for item in d.get("repaired", []):
        print(f"[복구] 내용이 어긋난 파일을 원본에서 다시 복사했습니다: {item['file']} (이전 사본: {item['backup']})",
              file=sys.stderr)
    for r in report.failures[:50]:
        print(f"실패: {r.src} -> {r.error}", file=sys.stderr)
    return 1 if report.failures else 0


def _cmd_checklist(args: argparse.Namespace) -> int:
    if _unexpanded(output=args.output, package=args.package):
        return 1
    try:
        r = run_checklist(args.rules, package=args.package, show_all=args.all, fmt=args.format, deps=_deps())
    except ApiError as e:
        return _fail(e)
    if args.output:
        try:
            write_output(Path(args.output), r.text)
        except OSError as e:
            return _write_failed(args.output, e)
        print(f"체크리스트를 저장했습니다: {args.output}", file=sys.stderr)
    else:
        print(r.text, end="")
    if r.discovery is not None:
        _print_hint(".", *r.discovery)
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    if _unexpanded(output=args.output, output_selection=args.output_selection):
        return 1
    try:
        d = run_discover(args.rules, min_size_kb=args.min_size_kb, show_all=args.all,
                         progress=lambda m: print(m, file=sys.stderr), deps=_deps())
    except ApiError as e:
        return _fail(e)
    text = render_discovery(d, args.format, args.all)
    if args.output:
        try:
            write_output(Path(args.output), text)
        except OSError as e:
            return _write_failed(args.output, e)
        print(f"탐지 결과를 저장했습니다: {args.output}", file=sys.stderr)
    else:
        print(text, end="")
    if args.output_selection:
        try:
            write_output(Path(args.output_selection), selection_json(d, args.all))
        except OSError as e:
            return _write_failed(args.output_selection, e)
        print(f"선택 파일 템플릿을 저장했습니다(모두 include:false): {args.output_selection}", file=sys.stderr)
    print(f"규칙 없는 설치 프로그램 {d.program_count}개, 설정 폴더 후보 {d.candidate_count}개"
          f"(비밀 정보 가능성 {d.secret_count}개).", file=sys.stderr)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    if _unexpanded(output=args.output, extra=args.extra):
        return 1
    try:
        r = run_export(args.rules, args.output, extra=args.extra, allow_secrets=args.allow_secrets,
                       deps=_deps())
    except ApiError as e:
        return _fail(e)
    except OSError as e:
        return _write_failed(args.output, e)
    if r.extras.risky:
        _err(f"[주의] --allow-secrets: 비밀 정보 가능성이 있는 항목 {r.extras.risky}개를 패키지에 포함합니다. "
             "패키지 파일을 안전하게 보관하세요.")
    _err(*r.notes)
    print(json.dumps(r.summary_dict(args.output), ensure_ascii=False))
    _print_warnings(r.manifest.get("manual_warnings", []))
    _print_checklist_hint(r.checklist)
    if r.discovery:
        _print_hint("(자동으로 포함되지 않습니다).", *r.discovery)
    return 1 if r.manifest["failed"] else 0


def _cmd_import(args: argparse.Namespace) -> int:
    if _unexpanded(package=args.package, report=args.report):
        return 1
    try:
        r = run_import(args.package, args.rules, dry_run=args.dry_run, yes=args.yes,
                       allow_extra=args.allow_extra, deps=_deps())
    except ApiError as e:
        return _fail(e)
    if r.mode == "blocked":
        print(json.dumps(r.preview_dict(), ensure_ascii=False, indent=2))
        _err("패키지 검증 실패: 복원하지 않습니다.")
        return 1
    if r.mode in ("dry-run", "needs-confirm"):
        print(json.dumps(r.preview_dict(), ensure_ascii=False, indent=2))
        _print_warnings(r.plan.warnings)
        if r.n_extra:
            _err(f"[주의] 규칙 없이 선택 포함된 파일 {r.n_extra}개가 있습니다. "
                 "실제 복원은 --yes 와 --allow-extra 가 모두 필요합니다.")
        if r.mode == "dry-run":
            _err("미리보기입니다. 실제 변경은 없었습니다.")
            return 0
        _err("미리보기만 출력했고 아무것도 쓰지 않았습니다. 실제 복원하려면 --yes 를 붙여 다시 실행하세요.")
        return NEEDS_CONFIRM
    if r.mode == "needs-allow-extra":
        _err(f"패키지에 규칙 없이 선택 포함된 파일 {r.n_extra}개가 있습니다. 내용을 미리보기로 확인한 뒤 "
             "--allow-extra 를 붙여 다시 실행하세요(복원하지 않았습니다).")
        return 1
    report = r.report
    _err("실제 복원을 시작합니다. 덮어쓸 파일은 .bak 으로 백업됩니다.")
    if args.report:
        try:
            report.write(Path(args.report))
        except OSError as e:
            return _write_failed(args.report, e)
    print(json.dumps({"dry_run": False, "summary": report.to_dict()["summary"]}, ensure_ascii=False))
    for f in report.failures:
        print(f"실패: {f.dst} -> {f.error}", file=sys.stderr)
    _print_warnings(r.plan.warnings)
    return 1 if report.failures else 0


# --------------------------------------------------------------------------- 파서

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pcmigrate", description="PC 이전 도우미")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--env", action="append", metavar="NAME=VALUE",
                   help="경로 해석용 환경변수 덮어쓰기(예: --env PROGRAMDATA=D:\\pd). 여러 번 지정 가능")
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
    t.add_argument("--log", help="robocopy 로그 경로(기본: 대상 폴더 안 .pcmigrate-robocopy.log)")
    t.add_argument("--state-file", help="이어받기 진행 상태 파일 경로(기본: 대상 폴더 안 .pcmigrate-state.json)")
    t.add_argument("--dry-run", action="store_true", help="robocopy /L 로 목록만 확인(쓰기 없음)")
    t.add_argument("--strict", action="store_true",
                   help="이어받기 시 상태 파일을 믿지 않고 모든 파일을 SHA-256 으로 다시 검증하고, "
                        "내용이 어긋난 파일은 원본에서 다시 복사(기존 사본은 .bak 보존)")
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
    _configure_output()
    args = build_parser().parse_args(argv)
    try:
        envpaths.set_overrides(envpaths.parse_assignments(args.env))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return args.func(args)
