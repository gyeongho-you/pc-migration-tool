"""pcmigrate 명령행 진입점. 인자 파싱과 터미널 출력만 담당하고, 실행 로직은 api.py 에 있다."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

from . import __version__, envpaths, errorlog
from .api import (NEEDS_CONFIRM, ApiError, Deps, check_outputs_outside, check_unexpanded, format_write_error, load_rules_checked,
                  report_inside, run_checklist, run_copy, run_discover, run_export, run_import, run_manifest,
                  run_scan, run_transfer, run_verify, same_file, selection_json)
from .checklist import write_output
from .detect import read_registry_programs, winget_export_raw, winget_list_raw
from .manifest import ManifestError, build_manifest, verify_against_manifest
from .progress import ProgressInfo, format_info
from .scan import skipped_links_view
from .discover import render as render_discovery
from .discover import summarize
from .package import export_package, plan_import
from .rules import default_rules_dir
from .transfer import TransferError, transfer


def _deps() -> Deps:
    """이 모듈의 현재 이름들을 주입한다(테스트가 cli.<이름> 을 바꿔치기할 수 있도록 호출 시점에 조회)."""
    return Deps(registry=read_registry_programs, winget_export=winget_export_raw,
                winget_list=winget_list_raw, summarize=summarize, export=export_package,
                plan=plan_import, transfer=transfer, manifest=build_manifest, verify=verify_against_manifest)


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


def _output_is_dir(**paths: str | None) -> bool:
    """출력 파일 경로에 기존 '폴더'를 준 경우(폴더를 .bak 으로 옮기고 같은 이름 파일을 만들게 되므로) 거부한다."""
    for label, value in paths.items():
        if value and os.path.isdir(value):
            _err(f"출력 경로가 폴더입니다: {value} (--{label})",
                 "힌트: 파일 이름까지 지정하세요(예: D:\\백업\\pc-package.zip). 기존 폴더는 건드리지 않았습니다.")
            return True
    return False


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
    try:
        check_outputs_outside(args.src, report=args.report)
    except ApiError as e:
        return _fail(e)
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


PRECHECK_LINES = (
    "시작 전 확인: ① CAD 등 모든 프로그램을 종료하세요(사용 중인 파일은 복사/검사에 실패합니다).",
    "시작 전 확인: ② 노트북은 전원을 연결하세요. 복사 중 PC 가 잠들지 않도록 이 도구가 절전을 막지만 전원이 꺼지면 멈춥니다.",
    "시작 전 확인: ③ 외장 드라이브는 NTFS 로 포맷된 것을 쓰고 USB 3.x 포트에 꽂으세요(FAT32 는 4GB 넘는 파일을 저장할 수 없습니다).",
    "시작 전 확인: ④ 원본 삭제나 PC 포맷은 검사가 끝나 '실패 0' 을 확인하기 전까지 절대 하지 마세요.",
    "시작 전 확인: ⑤ 대상에 같은 이름의 다른 파일이 이미 있으면 덮어쓰기 전에 .bak 으로 보관합니다(미리보기 --dry-run 에서 개수를 알려 줍니다).",
)


def _cli_progress():
    """진행 줄은 10초마다(또는 단계가 바뀔 때) 한 번만 출력한다. 오래 걸리는 작업에서 화면이 넘치지 않게."""
    state = {"last": 0.0, "phase": ""}

    def cb(msg: str) -> None:
        now = time.monotonic()
        phase = msg.split(" ", 1)[0]
        if phase != state["phase"] or now - state["last"] >= 10:
            state["phase"], state["last"] = phase, now
            print(f"[진행] {msg}", file=sys.stderr)
    return cb


def _cli_info_progress(interval: float = 10.0):
    """패키지 내보내기/복원처럼 파일 수가 많은 작업의 진행 줄. 10초마다(또는 단계가 바뀔 때) 한 번만 stderr 로 출력한다."""
    state = {"last": None, "phase": ""}

    def cb(info: ProgressInfo) -> None:
        now = time.monotonic()
        if info.phase != state["phase"] or state["last"] is None or now - state["last"] >= interval:
            state["phase"], state["last"] = info.phase, now
            print(f"[진행] {format_info(info)}", file=sys.stderr)
    return cb


def _cli_hint():
    seen: set[str] = set()

    def cb(info: ProgressInfo) -> None:
        if info.hint and info.hint not in seen:
            seen.add(info.hint)
            print(f"[힌트] {info.hint}", file=sys.stderr)
    return cb


def _chunk_bytes(mb: float | None) -> int | None:
    return int(mb * 1024 * 1024) if mb else None


def _int_range(lo: int, hi: int):
    def conv(text: str) -> int:
        try:
            v = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{lo}~{hi} 사이의 정수를 입력하세요(입력: {text!r})") from None
        if not lo <= v <= hi:
            raise argparse.ArgumentTypeError(
                f"{lo}~{hi} 사이여야 합니다(입력: {v}). 자동으로 바꾸지 않으니 범위 안의 값을 지정하세요")
        return v
    return conv


CHUNK_MB_RANGE = (0.25, 1024.0)


def _chunk_mb(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"숫자(MB)를 입력하세요(입력: {text!r})") from None
    lo, hi = CHUNK_MB_RANGE
    if not math.isfinite(v) or not lo <= v <= hi:
        raise argparse.ArgumentTypeError(
            f"{lo:g}~{hi:g}MB 사이여야 합니다(입력: {text}). 자동으로 바꾸지 않으니 범위 안의 값을 지정하세요")
    return v


def _cmd_transfer(args: argparse.Namespace) -> int:
    def warn(msg: str) -> None:
        print(f"[경고] {msg}", file=sys.stderr)

    if _unexpanded(src=args.src, dst=args.dst, report=args.report, log=args.log, state_file=args.state_file,
                   manifest_out=args.manifest_out, use_manifest=args.use_manifest):
        return 1
    try:
        check_outputs_outside(args.src, report=args.report, log=args.log, state_file=args.state_file,
                              manifest_out=args.manifest_out)
    except ApiError as e:
        return _fail(e)
    if not (args.dry_run or args.verify_only):
        _err(*PRECHECK_LINES)
    try:
        report = run_transfer(args.src, args.dst, dry_run=args.dry_run, include_cloud=args.include_cloud,
                              log_path=args.log, strict=args.strict, state_path=args.state_file, warn=warn,
                              deps=_deps(), progress=_cli_progress(), progress_info=_cli_hint(),
                              verify_only=args.verify_only, retry_failed=args.retry_failed, workers=args.workers,
                              chunk=_chunk_bytes(args.chunk_mb), keep_awake=not args.no_keep_awake,
                              manifest_out=args.manifest_out, use_manifest=args.use_manifest,
                              verify_dest=args.verify_dest, copy_mode=args.copy_mode,
                              copy_threads=args.copy_threads)
    except ApiError as e:
        return _fail(e)
    except TransferError as e:
        print(f"전송 실패: {e}", file=sys.stderr)
        return 1
    except ManifestError as e:
        print(f"해시 목록 오류: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        return _write_failed(args.dst, e)
    if args.report:
        try:
            report.write(Path(args.report))
        except OSError as e:
            return _write_failed(args.report, e)
    d = report.to_dict()
    print(json.dumps({"dry_run": d["dry_run"], "mode": d.get("mode"), "summary": d["summary"],
                      "robocopy_exit": d.get("robocopy_exit"), "cloud_only_count": d["cloud_only_count"],
                      "total_files": d.get("total_files"), "total_bytes": d.get("total_bytes"),
                      "verification_complete": d.get("verification_complete"), "success": d.get("success"),
                      "failed_count": d.get("failed_count"), "skipped_links": d.get("skipped_links", 0)},
                     ensure_ascii=False))
    if d.get("preserved_existing_count"):
        _err(f"[보존] 대상에 있던 같은 이름의 다른 파일 {d['preserved_existing_count']}개를 덮어쓰기 전에 .bak 으로 보관했습니다"
             "(같은 폴더의 <이름>.bak, 이미 있으면 .bak.1). 필요 없으면 확인 후 직접 지우세요.")
    n_links, link_paths, link_more = skipped_links_view(d)
    if n_links:
        _err(f"[안내] 바로가기/정션(링크) {n_links}개는 따라가지 않고 건너뛰었습니다. 링크 너머의 폴더는 따로 복사해야 합니다."
             + (f" (앞 {len(link_paths)}개만 표시, 전체는 --report 파일의 skipped_link_paths)" if link_more else ""))
        for p in link_paths:
            _err(f"  건너뜀: {p}")
    if d.get("dest_verification") == "deferred" and not d["dry_run"] and not report.failures:
        _err("[복사 완료, 검증 전] 복사는 끝났지만 복사본 검사는 아직 하지 않았습니다. 새 PC 에서 verify 로 검사해야 정확한지 알 수 있습니다. "
             "그 전에는 원본을 지우지 마세요.")
    for item in d.get("repaired", []):
        print(f"[복구] 내용이 어긋난 파일을 원본에서 다시 복사했습니다: {item['file']} (이전 사본: {item['backup']})",
              file=sys.stderr)
    if d.get("manifest_out"):
        print(f"[목록] 해시 목록을 저장했습니다: {d['manifest_out']} (파일 {d.get('manifest_files', 0):,}개)", file=sys.stderr)
        if d.get("dest_verification") == "deferred":
            print(f"[다음 단계] 대상 검사는 새 PC 에서 하세요: pcmigrate verify --manifest <목록 파일> --target <새 PC 의 폴더>",
                  file=sys.stderr)
    if d.get("keep_awake") == "failed":
        print("[경고] 절전 방지를 켜지 못했습니다. 전원 설정에서 잠자기를 꺼 두세요.", file=sys.stderr)
    for r in report.failures[:50]:
        print(f"실패: {r.src} -> {r.error}", file=sys.stderr)
    return 1 if report.failures else 0


def _cmd_manifest(args: argparse.Namespace) -> int:
    if _unexpanded(src=args.src, out=args.out):
        return 1
    try:
        check_outputs_outside(args.src, out=args.out)
    except ApiError as e:
        return _fail(e)
    try:
        r = run_manifest(args.src, args.out, workers=args.workers, chunk=_chunk_bytes(args.chunk_mb),
                         progress=_cli_progress(), progress_info=_cli_hint(), deps=_deps())
    except ManifestError as e:
        print(f"해시 목록 오류: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        return _write_failed(args.out, e)
    print(json.dumps({"manifest": str(r.path), "files": r.files, "bytes": r.bytes, "errors": len(r.errors),
                      "seconds": round(r.seconds, 1), "io": r.io}, ensure_ascii=False))
    for e in r.errors[:50]:
        print(f"읽지 못함: {e.src} -> {e.error}", file=sys.stderr)
    return 1 if r.errors else 0


def _cmd_verify(args: argparse.Namespace) -> int:
    if _unexpanded(manifest=args.manifest, target=args.target, report=args.report):
        return 1
    if report_inside(args.report, args.target):
        print("리포트는 검사할 폴더 밖에 저장하세요(검사는 대상 폴더를 읽기만 하고 아무것도 쓰지 않습니다).", file=sys.stderr)
        return 1
    try:
        rep = run_verify(args.manifest, args.target, workers=args.workers, chunk=_chunk_bytes(args.chunk_mb),
                         progress=_cli_progress(), progress_info=_cli_hint(), find_extras=not args.no_extras, deps=_deps())
    except ManifestError as e:
        print(f"해시 목록 오류: {e}", file=sys.stderr)
        return 1
    if args.report:
        try:
            rep.write(Path(args.report))
        except OSError as e:
            return _write_failed(args.report, e)
    d = rep.to_dict()
    print(json.dumps({"summary": d["summary"], "extra_files": d.get("extra_files"),
                      "manifest_complete": d.get("manifest_complete"), "manifest_errors": d.get("manifest_errors", 0),
                      "failed_count": d.get("failed_count"), "success": not rep.failures,
                      "seconds": d.get("seconds")}, ensure_ascii=False))
    if d.get("manifest_errors"):
        _err(f"[주의] 구 PC 에서 읽지 못해 목록에서 빠진 파일 {d['manifest_errors']}개가 있습니다. 이 파일들은 검사되지 않았으므로 "
             "'모두 정확함'이 아닙니다. 구 PC 에서 해당 파일을 닫은 뒤 목록을 다시 만드세요.")
    for r in rep.failures[:50]:
        print(f"실패: {r.src} -> {r.error}", file=sys.stderr)
    return 1 if rep.failures else 0


def _cmd_check_rules(args: argparse.Namespace) -> int:
    """규칙 폴더가 읽히는지만 확인한다(설치 목록/사용자 폴더는 읽지 않는다). exe 빌드 스모크 시험에서 번들 누락을 잡는 용도."""
    try:
        rules = load_rules_checked(args.rules)
    except ApiError as e:
        return _fail(e)
    if not rules:
        _err(f"규칙 파일이 하나도 없습니다: {args.rules}")
        return 1
    print(json.dumps({"rules": len(rules), "dir": str(args.rules)}, ensure_ascii=False))
    return 0


def _cmd_checklist(args: argparse.Namespace) -> int:
    if _unexpanded(output=args.output, package=args.package) or _output_is_dir(output=args.output):
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
    if (_unexpanded(output=args.output, output_selection=args.output_selection)
            or _output_is_dir(output=args.output, **{"output-selection": args.output_selection})):
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
    if _unexpanded(output=args.output, extra=args.extra) or _output_is_dir(output=args.output):
        return 1
    try:
        r = run_export(args.rules, args.output, extra=args.extra, allow_secrets=args.allow_secrets,
                       deps=_deps(), progress_info=_cli_info_progress())
    except ApiError as e:
        return _fail(e)
    except OSError as e:
        return _write_failed(args.output, e)
    if r.extras.risky:
        _err(f"[주의] --allow-secrets: 비밀 정보 가능성이 있는 항목 {r.extras.risky}개를 패키지에 포함합니다. "
             "패키지 파일을 안전하게 보관하세요.")
    _err(*r.notes)
    print(json.dumps(r.summary_dict(args.output), ensure_ascii=False))
    _err(*r.unreadable_lines())
    for f in r.hard_failures:
        _err(f"실패: {f['src']} -> {f['error']}")
    _print_warnings(r.manifest.get("manual_warnings", []))
    _print_checklist_hint(r.checklist)
    if r.discovery:
        _print_hint("(자동으로 포함되지 않습니다).", *r.discovery)
    return 1 if r.hard_failures else 0  # 읽지 못한 파일은 안내만 하고 성공으로 끝낸다(패키지는 검증을 통과했다)


def _cmd_import(args: argparse.Namespace) -> int:
    if _unexpanded(package=args.package, report=args.report):
        return 1
    if same_file(args.report, args.package):
        _err("--report 가 패키지 파일 자신입니다. 패키지를 덮어쓰지 않도록 다른 이름으로 저장하세요.")
        return 1
    if not args.dry_run:
        _err("[진행] 패키지를 검사하는 중입니다. 파일이 많으면 몇 분 걸릴 수 있으며, 진행 상황이 10초마다 표시됩니다.")
    try:
        r = run_import(args.package, args.rules, dry_run=args.dry_run, yes=args.yes,
                       allow_extra=args.allow_extra, deps=_deps(), progress_info=_cli_info_progress())
    except ApiError as e:
        return _fail(e)
    if r.mode == "blocked":
        print(json.dumps(r.preview_dict(), ensure_ascii=False, indent=2))
        _err("패키지 검증 실패: 복원하지 않습니다.")
        return 1
    if r.mode in ("dry-run", "needs-confirm"):
        print(json.dumps(r.preview_dict(), ensure_ascii=False, indent=2))
        n_rej = sum(1 for it in r.plan.items if it.get("status") == "rejected")
        if n_rej:
            _err(f"[주의] 복원 위치가 바로가기(정션/링크)를 거쳐 복원되지 않을 파일 {n_rej}개가 있습니다. "
                 "링크를 없애거나 실제 폴더로 바꾼 뒤 다시 시도하세요(링크 너머에는 쓰지 않습니다).")
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
    rd = report.to_dict()
    print(json.dumps({"dry_run": False, "summary": rd["summary"], "failed_count": rd["failed_count"],
                      "success": not report.failures}, ensure_ascii=False))
    n_rej = sum(1 for f in report.failures if f.status == "rejected")
    if n_rej:
        _err(f"[주의] 복원 위치가 바로가기(정션/링크)를 거치는 파일 {n_rej}개는 복원하지 않았습니다(링크 너머에는 쓰지 않습니다). "
             "링크를 없애거나 실제 폴더로 바꾼 뒤 다시 시도하세요.")
    for f in report.failures:
        print(f"실패: {f.dst} -> {f.error}", file=sys.stderr)
    _print_warnings(r.plan.warnings)
    return 1 if report.failures else 0


def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui.app import main as gui_main
    except ImportError as e:  # tkinter 가 없는 Python
        print(f"그래픽 화면을 쓸 수 없습니다(tkinter 가 설치되지 않았을 수 있습니다): {e}", file=sys.stderr)
        return 1
    return gui_main([])


def _cmd_logs(args: argparse.Namespace) -> int:
    d = errorlog.log_dir()
    print(f"오류 로그 폴더: {d}")
    print(errorlog.PATH_NOTE)
    last = errorlog.last_error(d)
    if last is None:
        print("기록된 오류가 없습니다.")
    else:
        path, block = last
        print(f"가장 최근 오류({path.name}):")
        for line in errorlog.summarize_block(block):
            print(f"  {line}")
    if args.open:
        try:
            os.startfile(str(d))  # type: ignore[attr-defined]
        except (AttributeError, OSError) as e:
            _err(f"탐색기로 열지 못했습니다({type(e).__name__}). 위 경로를 직접 여세요.")
            return 1
    return 0


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

    cr = sub.add_parser("check-rules", help="규칙 폴더가 올바르게 읽히는지만 확인(읽기 전용)")
    cr.add_argument("--rules", default=str(default_rules_dir()), help="규칙 JSON 폴더")
    cr.set_defaults(func=_cmd_check_rules)

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
    t.add_argument("--verify-only", action="store_true", help="복사 없이 원본과 대상을 비교만 하고 리포트를 갱신(대상은 항상 다시 읽음)")
    t.add_argument("--retry-failed", action="store_true", help="전체를 다시 복사하지 않고 검사가 끝나지 않았거나 실패한 파일만 다시 복사/검사")
    t.add_argument("--workers", type=_int_range(1, 8), help="파일 단위 병렬 검사 개수(1~8, 기본 2). 느린 USB/HDD 에서는 1 이 나을 수 있음")
    t.add_argument("--chunk-mb", type=_chunk_mb, help="읽기 청크 크기(MB, 0.25~1024, 기본 4)")
    t.add_argument("--no-keep-awake", action="store_true", help="전송 중 절전 방지를 끈다(기본은 시스템 절전을 막음)")
    t.add_argument("--manifest-out", help="원본 기준 해시 목록(.jsonl)을 저장. 네트워크 대상이면 대상 검사를 미루고 새 PC 에서 verify 로 비교")
    t.add_argument("--use-manifest", help="원본 해시를 이 목록에서 가져와 원본을 다시 읽지 않음(외장 드라이브 2단계: 외장 -> 새 PC)")
    g = t.add_mutually_exclusive_group()
    g.add_argument("--verify-dest", dest="verify_dest", action="store_const", const=True, default=None,
                   help="--manifest-out 과 함께여도 대상을 지금 다시 읽어 검사(네트워크면 오래 걸림)")
    g.add_argument("--no-verify-dest", dest="verify_dest", action="store_const", const=False,
                   help="대상 검사를 미룬다(해시 목록만 만들고 새 PC 에서 verify)")
    t.add_argument("--copy-threads", type=_int_range(1, 16), help="복사 도구가 동시에 복사할 파일 수(기본 4, 1~16). 작은 파일이 많을 때 빠르며, 느린 HDD/USB 에서는 1 을 권장")
    t.add_argument("--copy-mode", choices=("restartable", "normal", "unbuffered"), default="restartable",
                   help="복사 방식: restartable(기본, 끊겨도 이어받음) / normal / unbuffered(아주 큰 파일에 유리)")
    t.set_defaults(func=_cmd_transfer)

    m = sub.add_parser("manifest", help="원본(로컬)을 읽어 해시 목록 파일을 만든다(읽기 전용)")
    m.add_argument("src", help="원본 폴더(읽기만 함)")
    m.add_argument("--out", required=True, help="해시 목록 파일(.jsonl) 저장 경로")
    m.add_argument("--workers", type=_int_range(1, 8), help="파일 단위 병렬 개수(1~8, 기본 2)")
    m.add_argument("--chunk-mb", type=_chunk_mb, help="읽기 청크 크기(MB, 0.25~1024, 기본 4)")
    m.set_defaults(func=_cmd_manifest)

    v = sub.add_parser("verify", help="해시 목록과 로컬 폴더를 비교(누락/내용 다름/추가 파일). 대상은 읽기만 함")
    v.add_argument("--manifest", required=True, help="구 PC 에서 만든 해시 목록(.jsonl)")
    v.add_argument("--target", required=True, help="검사할 로컬 폴더(새 PC)")
    v.add_argument("--report", help="결과 리포트(JSON). 검사할 폴더 밖에 저장해야 함")
    v.add_argument("--workers", type=_int_range(1, 8), help="파일 단위 병렬 개수(1~8, 기본 2)")
    v.add_argument("--chunk-mb", type=_chunk_mb, help="읽기 청크 크기(MB, 0.25~1024, 기본 4)")
    v.add_argument("--no-extras", action="store_true", help="목록에 없는 추가 파일 찾기를 건너뜀")
    v.set_defaults(func=_cmd_verify)

    c = sub.add_parser("copy", help="소규모 폴더/파일을 해시 검증하며 복사(대용량은 transfer)")
    c.add_argument("src")
    c.add_argument("dst")
    c.add_argument("--dry-run", action="store_true", help="변경 없이 계획만 출력")
    c.add_argument("--report", help="결과 리포트(JSON) 저장 경로")
    c.set_defaults(func=_cmd_copy)
    g = sub.add_parser("gui", help="그래픽 마법사(Tkinter)를 연다")
    g.set_defaults(func=_cmd_gui)
    lg = sub.add_parser("logs", help="오류 로그 폴더 경로와 최근 오류 요약을 보여 준다")
    lg.add_argument("--open", action="store_true", help="탐색기로 로그 폴더를 연다")
    lg.set_defaults(func=_cmd_logs)
    return p


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    args = build_parser().parse_args(argv)
    try:
        envpaths.set_overrides(envpaths.parse_assignments(args.env))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _err("중단했습니다. 원본은 읽기만 하므로 바뀌지 않았습니다.")
        return 130
    except Exception as e:  # noqa: BLE001 - traceback 대신 쉬운 문구 + 로그 파일로 안내
        path = errorlog.record(e, f"pcmigrate {args.command}")
        _err("예상하지 못한 오류가 났습니다. " + (f"자세한 내용은 {path} 에 기록했습니다."
                                          if path else "오류 로그도 남기지 못했습니다(로그 폴더에 쓸 수 없음)."),
             f"원인(요약): {type(e).__name__}: {e}")
        return 1
