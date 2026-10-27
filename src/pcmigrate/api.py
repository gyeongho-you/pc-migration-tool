"""CLI 와 GUI 가 함께 쓰는 실행 로직. 출력(print)은 하지 않고 결과 객체를 돌려준다.

규칙:
- 여기 함수는 stdout/stderr 에 아무것도 쓰지 않는다. 메시지는 결과 객체의 필드(notes 등)나 ApiError 로 전달하고,
  어떻게 보여 줄지는 호출자(cli.py 의 터미널 출력, 이후 GUI)가 정한다.
- 파일 쓰기 실패(OSError)는 그대로 전파한다. 호출자는 format_write_error 로 사용자용 문장을 만든다.
- 외부 호출(레지스트리, winget, 요약, export 등)은 Deps 로 주입할 수 있다(테스트/GUI 에서 교체).
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .cancel import Cancelled, CancelToken  # noqa: F401  (GUI 가 api 에서 가져다 쓴다)
from . import coverage
from .settings_filter import DEFAULT_EXTRA_MODE, EXTRA_MODE_ALL, EXTRA_MODES
from .checklist import (Checklist, build_checklist, bundle_reason, load_package_programs,
                        manifest_warning_pairs, render as render_checklist, rule_warning_pairs)
from .copier import Report, copy_tree
from .detect import (Program, match_rules, parse_winget_export, read_registry_programs,
                     winget_export_raw, winget_list_raw)
from .discover import (Discovery, ExtraItem, SelectionError, build_selection, discover, items_from_selection,
                       load_selection,
                       program_has_rule, secret_names, summarize)
import inspect
from .find_program import ProgramFind, find_program
from .install_check import InstallCheck, NewState, check_install, read_new_state
from . import import_record
from .package import (COMPRESS_MODES, NOT_SELECTED, ImportPlan, PackageError, apply_import, export_package, extract_winget,
                      is_unfinished_part, plan_import, retry_failed_export, verify_package)
from .rules import Rule, RuleError, load_rules
from . import fs
from .manifest import build_manifest, verify_against_manifest
from . import split
from .progress import ProgressInfo
from .transfer import transfer

NEEDS_CONFIRM = 2  # import 에 --yes 가 없어 복원하지 않고 종료

# 셸이 확장하지 못한 환경변수(cmd 의 %VAR%, PowerShell 의 $env:VAR)가 그대로 들어온 경로
UNEXPANDED = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%|\$env:", re.I)
SHELL_HINT = ("힌트: cmd 에서는 %TEMP%, PowerShell 에서는 $env:TEMP 를 쓰세요"
              "(PowerShell 에서 %TEMP% 는, cmd 에서 $env:TEMP 는 확장되지 않습니다. Git Bash 는 $TEMP).")


class ApiError(Exception):
    """사용자에게 보여 줄 수 있는 실패. lines 는 본문 앞에 붙는 상세 줄들."""

    def __init__(self, message: str, *, lines: tuple[str, ...] | list[str] = (), code: int = 1):
        super().__init__(message)
        self.message = message
        self.lines = list(lines)
        self.code = code


@dataclass
class Deps:
    """외부 호출 주입점. 기본값은 실제 구현."""
    registry: Callable[[], list[Program]] = read_registry_programs
    winget_export: Callable[[], str | None] = winget_export_raw
    winget_list: Callable[[], str | None] = winget_list_raw
    summarize: Callable = summarize
    export: Callable = export_package
    plan: Callable = plan_import
    transfer: Callable = transfer
    manifest: Callable = build_manifest
    verify: Callable = verify_against_manifest
    describe_destination: Callable = fs.describe_destination
    retry_export: Callable = retry_failed_export


# --------------------------------------------------------------------------- 공통 보조

def format_write_error(path: str, exc: OSError) -> list[str]:
    """파일 쓰기 실패를 traceback 대신 사용자용 문장(한 줄 + 선택적 힌트)으로."""
    p = exc.filename if isinstance(getattr(exc, "filename", None), str) else path
    if isinstance(exc, IsADirectoryError) or getattr(exc, "errno", None) == errno.EISDIR:
        return [f"출력 경로가 폴더입니다: {p}", "힌트: 파일 이름까지 지정하세요(예: D:/백업/pc-package.zip). 기존 폴더는 건드리지 않았습니다."]
    cause = getattr(exc, "strerror", None) or str(exc)
    lines = [f"출력 경로를 만들 수 없습니다: {p} ({cause})"]
    if UNEXPANDED.search(p):
        lines.append(SHELL_HINT)
    elif isinstance(exc, FileNotFoundError) or getattr(exc, "winerror", None) in (3, 15):
        lines.append("힌트: 드라이브 문자와 폴더 이름이 맞는지 확인하세요(존재하지 않는 드라이브/폴더일 수 있습니다).")
    return lines


def unreadable_lines(items: list[dict], limit: int = 5) -> list[str]:
    """'읽지 못해 제외된 파일 N개' 안내(예시 경로는 limit 개까지). 없으면 빈 목록."""
    if not items:
        return []
    lines = [f"[주의] 읽지 못해 제외된 파일 {len(items)}개 — 사용 중인 파일이라 설정 묶음 파일에 넣지 못했습니다"
             "(예: Chrome 같은 프로그램이 실행 중이면 닫고 다시 하세요)."]
    lines += [f"  - {f['src']}" for f in items[:limit]]
    if len(items) > limit:
        lines.append(f"  … 외 {len(items) - limit}개(전체 목록은 설정 묶음 파일 안의 목록 파일(manifest) 'failed' 항목에 있습니다)")
    return lines


def check_unexpanded(**paths: str | None) -> list[str]:
    """경로 인자 검사: 빈 문자열이거나 환경변수가 확장되지 않았으면 오류 문장들(없으면 빈 목록)."""
    for label, value in paths.items():
        if value is not None and not value.strip():
            return [f"경로가 비어 있습니다: {label} (빈 값은 현재 폴더로 보지 않고 오류로 처리합니다. 경로를 입력하세요)"]
    for label, value in paths.items():
        if value and UNEXPANDED.search(value):
            return [f"출력 경로를 만들 수 없습니다: {value} (환경변수가 확장되지 않았습니다: --{label})", SHELL_HINT]
    return []


def clean_path_arg(value: str) -> str:
    """경로 인자 양끝의 공백과 따옴표(" ')를 지운다(탐색기에서 '경로로 복사' 한 값 등). 빈 값은 그대로 두어 호출자가 거부한다."""
    return value.strip(" \t\r\n\"'")


def check_path_forms(**paths: str | None) -> tuple[list[str], list[str]]:
    """경로 인자의 모양 검사(R2-9). (오류 문장들, 안내 문장들).

    - 드라이브 문자 없이 역슬래시 하나로 시작하는 경로(\\x\\y)는 '현재 드라이브의 루트'로 해석되어 뜻밖의 곳이 되므로 거부한다.
      네트워크 경로는 역슬래시 두 개(\\\\서버\\공유)로 시작해야 한다.
    - 상대 경로는 현재 폴더 기준으로 풀어 보여 준다(안내)."""
    errors: list[str] = []
    notes: list[str] = []
    for label, value in paths.items():
        if not value or UNEXPANDED.search(value):
            continue
        v = fs.plain_path_text(value)
        if v.startswith(("\\", "/")) and not v.startswith(("\\\\", "//")):
            errors.append(f"경로가 드라이브 문자 없이 역슬래시(\\)로 시작합니다: {value} (--{label}). 현재 드라이브의 맨 위 폴더로 해석되어 "
                          "뜻하지 않은 곳이 됩니다. D:\\폴더\\이름 처럼 드라이브 문자부터 쓰세요. 네트워크 경로는 역슬래시 두 개로 "
                          "시작해야 합니다(\\\\서버\\공유\\폴더).")
        elif not os.path.isabs(v) and not v.startswith("\\\\"):
            notes.append(f"[안내] 상대 경로를 현재 폴더 기준으로 풀어서 씁니다: {value} → {os.path.abspath(v)} (--{label})")
    return errors, notes


def load_rules_checked(rules_dir: str | Path) -> list[Rule]:
    try:
        return load_rules(Path(rules_dir))
    except RuleError as e:
        raise ApiError(f"규칙 오류: {e}") from e


def programs_registry(programs: list[Program]) -> list[Program]:
    return [p for p in programs if p.source == "registry"]


def winget_programs(programs: list[Program]) -> list[Program]:
    return [p for p in programs if p.source == "winget"]


def count_rule_less_programs(rules: list[Rule], programs: list[Program]) -> int:
    """규칙도 번들도 아닌 설치 프로그램 수(폴더 후보는 세지 않음)."""
    seen: set[str] = set()
    n = 0
    for p in programs:
        if p.name in seen:
            continue
        seen.add(p.name)
        if not program_has_rule(p, rules) and not bundle_reason(p.name):
            n += 1
    return n


def discovery_summary(rules: list[Rule], programs: list[Program], deps: Deps) -> tuple[int, int] | None:
    """(규칙 없는 설치 프로그램 수, 설정 폴더 후보 수). 크기 계산 없이 개수만. 읽기 실패 시 None."""
    try:
        return deps.summarize(rules, programs)
    except OSError:
        return None


@dataclass
class SystemInfo:
    os_name: str
    is_admin: bool | None  # None = 알 수 없음
    python: str


def system_info() -> SystemInfo:
    """현재 PC 의 OS 와 관리자 권한 여부(GUI 상단 표시용, 읽기 전용)."""
    admin: bool | None = None
    try:
        if sys.platform == "win32":
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        admin = None
    return SystemInfo(f"{platform.system()} {platform.release()} ({platform.version()})", admin,
                      platform.python_version())


@dataclass
class RuleMatchInfo:
    rule: Rule
    programs: list[str]  # 이름 패턴에 걸린 설치 프로그램
    evidence: str  # 사람이 읽는 근거 한 줄


def explain_matches(matched: list[Rule], programs: list[Program]) -> list[RuleMatchInfo]:
    """규칙이 어떤 근거(설치 프로그램 이름 패턴 / 설치 경로 / 레지스트리 키)로 감지됐는지 설명한다."""
    out = []
    for r in matched:
        names = []
        for p in programs:
            if r.matches_name(p.name) and p.name not in names:
                names.append(p.name)
        if names:
            ev = "설치 프로그램 이름 일치: " + ", ".join(names[:3]) + (" 외" if len(names) > 3 else "")
        elif r.install_paths or r.registry_keys:
            ev = "설치 경로 또는 레지스트리 키로 감지"
        else:
            ev = "근거 확인 필요"
        out.append(RuleMatchInfo(r, names, ev))
    return out


# --------------------------------------------------------------------------- scan

@dataclass
class ScanResult:
    program_count: int
    matched: list[Rule]
    checklist: Checklist
    discovery: tuple[int, int] | None
    match_info: list[RuleMatchInfo] = field(default_factory=list)

    def summary_dict(self) -> dict:
        return {"program_count": self.program_count,
                "matched_rules": [{"id": r.id, "name": r.name, "manual_warnings": list(r.manual_warnings),
                                   "notes": r.notes} for r in self.matched]}


def _phase(progress_info: Callable[[ProgressInfo], None] | None, phase: str, message: str, **counts) -> None:
    """단계 전환을 알린다(GUI 현황 카드용). 콜백이 없으면 아무 일도 하지 않는다."""
    if progress_info is not None:
        progress_info(ProgressInfo(phase, 0, 0, 0, 0, message=message, **counts))


def run_scan(rules_dir: str | Path, deps: Deps | None = None,
             progress_info: Callable[[ProgressInfo], None] | None = None) -> ScanResult:
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    _phase(progress_info, "list", "설치된 프로그램 목록을 읽는 중입니다…")
    winget_text = deps.winget_export()
    programs = deps.registry() + (parse_winget_export(winget_text) if winget_text else [])
    _phase(progress_info, "match", f"프로그램 {len(programs)}개를 규칙과 맞춰 보는 중입니다…")
    matched = match_rules(rules, programs)
    cl = build_checklist(rule_warning_pairs(matched), programs_registry(programs), winget_programs(programs),
                         deps.winget_list(), rule_ids=[r.id for r in matched])
    return ScanResult(len(programs), matched, cl, discovery_summary(rules, programs_registry(programs), deps),
                      explain_matches(matched, programs))


# --------------------------------------------------------------------------- copy / transfer

def run_copy(src: str | Path, dst: str | Path, dry_run: bool = False) -> Report:
    return copy_tree(Path(src), Path(dst), dry_run=dry_run)


def run_transfer(src: str | Path, dst: str | Path, *, dry_run: bool = False, include_cloud: bool = False,
                 log_path: str | Path | None = None, strict: bool = False, state_path: str | Path | None = None,
                 warn: Callable[[str], None] = lambda m: None, deps: Deps | None = None,
                 progress: Callable[[str], None] | None = None,
                 cancel: Callable[[], bool] | None = None,
                 progress_info: Callable[[ProgressInfo], None] | None = None,
                 verify_only: bool = False, retry_failed: bool = False, workers: int | None = None,
                 chunk: int | None = None, keep_awake: bool = True, manifest_out: str | Path | None = None,
                 use_manifest: str | Path | None = None, verify_dest: bool | None = None,
                 copy_mode: str = "restartable", copy_threads: int | None = None,
                 split_large: bool = False, exclude_junk: bool = False, junk_backup_copies: bool = False) -> Report:
    """대용량 전송. TransferError/OSError 는 그대로 전파한다. 취소하면 Cancelled(진행 상태는 저장됨).

    기본값이 아닌 인자만 하위 함수로 넘기므로 이전 호출 형태가 그대로 동작한다.
    """
    deps = deps or Deps()
    check_outputs_outside(src, log=log_path, state_file=state_path, manifest_out=manifest_out)
    kw: dict = dict(dry_run=dry_run, include_cloud=include_cloud,
                    log_path=Path(log_path) if log_path else None, warn=warn, strict=strict)
    if state_path:
        kw["state_path"] = Path(state_path)
    if progress:
        kw["progress"] = progress
    if progress_info:
        kw["progress_info"] = progress_info
    if cancel:
        kw["cancel"] = cancel
    extras = dict(verify_only=verify_only, retry_failed=retry_failed, workers=workers, chunk=chunk, manifest_out=manifest_out,
                  use_manifest=use_manifest, verify_dest=verify_dest)
    kw.update({k: v for k, v in extras.items() if v})
    if verify_dest is not None:
        kw["verify_dest"] = verify_dest
    if not keep_awake:
        kw["keep_awake"] = False
    if copy_mode != "restartable":
        kw["copy_mode"] = copy_mode
    if copy_threads:
        kw["copy_threads"] = copy_threads
    if split_large:  # 4GB 넘는 파일을 조각(.pcm001...)으로 나눠 저장(켠 경우에만 넘긴다)
        kw["split_large"] = True
    if junk_backup_copies and not exclude_junk:
        raise ApiError("백업 사본 제외(junk_backup_copies)는 정크 제외(exclude_junk)와 함께만 쓸 수 있습니다")
    if exclude_junk:  # 임시/잠금/캐시 파일 제외(junk.py 의 목록). 꺼져 있으면 아무것도 넘기지 않는다
        from .junk import make_spec
        kw["junk_spec"] = make_spec(include_backup_copies=junk_backup_copies)
    return deps.transfer(Path(src), Path(dst), **kw)


def run_manifest(src: str | Path, out: str | Path, *, workers: int | None = None, chunk: int | None = None,
                 cancel: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
                 progress_info: Callable[[ProgressInfo], None] | None = None, deps: Deps | None = None):
    """구 PC: 원본(로컬)을 읽어 해시 목록 파일을 만든다(원본은 읽기만 함)."""
    deps = deps or Deps()
    check_outputs_outside(src, out=out)
    return deps.manifest(Path(src), Path(out), workers=workers, chunk=chunk, cancel=cancel, progress=progress,
                         progress_info=progress_info)


def run_verify(manifest: str | Path, target: str | Path, *, workers: int | None = None, chunk: int | None = None,
               cancel: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
               progress_info: Callable[[ProgressInfo], None] | None = None, find_extras: bool = True,
               deps: Deps | None = None) -> Report:
    """새 PC: 해시 목록과 로컬 대상 폴더를 비교한다. 대상 폴더는 읽기만 한다."""
    deps = deps or Deps()
    return deps.verify(Path(manifest), Path(target), workers=workers, chunk=chunk, cancel=cancel, progress=progress,
                       progress_info=progress_info, find_extras=find_extras)


def run_join(src: str | Path, output: str | Path, *, manifest: str | Path | None = None, dry_run: bool = False,
             chunk: int | None = None, cancel: Callable[[], bool] | None = None,
             progress: Callable[[str], None] | None = None,
             dest_info_fn: Callable[[str], fs.DestInfo] | None = None) -> Report:
    """분할 저장된 조각(.pcm001 ...)을 output 아래에 원래 파일로 합친다. 조각은 읽기만 하고 지우지 않는다.

    src 는 조각이 있는 폴더(하위 폴더 포함) 또는 첫 조각 파일. SplitError 는 ApiError 로 바뀐다."""
    try:
        return split.join_all(str(src), str(output), manifest=str(manifest) if manifest else None, dry_run=dry_run,
                              chunk=chunk, cancel=cancel, progress=progress, dest_info_fn=dest_info_fn)
    except split.SplitError as e:
        raise ApiError(str(e)) from e


def destination_info(dst: str | Path, deps: Deps | None = None) -> fs.DestInfo:
    """복사 대상의 종류(이동식/고정/네트워크), 파일 시스템, 여유 공간(읽기 전용 조회)."""
    return (deps or Deps()).describe_destination(str(dst))


def report_inside(path: str | Path | None, folder: str | Path) -> bool:
    """path 가 folder 안에 있는지(verify 의 읽기 전용 보장: 리포트를 대상 폴더 안에 쓰지 못하게 할 때 쓴다)."""
    if not path or not str(folder):
        return False
    try:
        Path(os.path.normcase(os.path.realpath(path))).relative_to(os.path.normcase(os.path.realpath(folder)))
        return True
    except ValueError:
        return False


def same_file(a: str | Path | None, b: str | Path | None) -> bool:
    """두 경로가 같은 파일을 가리키는지(대소문자/링크/상대 경로 무시). 하나라도 비어 있으면 False."""
    if not a or not b:
        return False
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def check_outputs_outside(src: str | Path, **outputs: str | Path | None) -> None:
    """원본 폴더 안에 쓰는 출력 경로(리포트/로그/상태 파일/해시 목록)가 있으면 ApiError. 원본은 읽기만 한다(원칙 1)."""
    for label, value in outputs.items():
        if value and report_inside(value, src):
            raise ApiError(f"{label} 경로가 원본 폴더 안입니다: {value}",
                           lines=["원본 폴더에는 아무것도 쓰지 않습니다. 원본 폴더 밖(예: 대상 폴더나 문서 폴더)에 저장하세요."])


# --------------------------------------------------------------------------- checklist

@dataclass
class ChecklistResult:
    text: str
    checklist: Checklist
    discovery: tuple[int, int | None] | None


def run_checklist(rules_dir: str | Path, *, package: str | Path | None = None, show_all: bool = False,
                  fmt: str = "text", deps: Deps | None = None,
                  progress_info: Callable[[ProgressInfo], None] | None = None) -> ChecklistResult:
    deps = deps or Deps()
    _phase(progress_info, "list", "설치된 프로그램 목록을 읽는 중입니다…")
    if package:
        manifest, problems = verify_package(Path(package))
        if manifest is None or problems:
            raise ApiError("패키지 검증 실패: " + "; ".join(problems[:3]))
        registry, winget, list_text = load_package_programs(Path(package))
        pairs = manifest_warning_pairs(manifest)
        rule_ids = [r["id"] for r in manifest.get("rules", [])]
        all_rules = load_rules_checked(rules_dir)  # 규칙 폴더가 없거나 비어 있으면 조용히 넘어가지 않고 오류로 알린다
        disc = (count_rule_less_programs(all_rules, registry), None)
    else:
        rules = load_rules_checked(rules_dir)
        winget_text = deps.winget_export()
        winget = parse_winget_export(winget_text) if winget_text else []
        list_text = deps.winget_list() if winget_text else None
        registry = deps.registry()
        matched = match_rules(rules, registry + winget)
        pairs = rule_warning_pairs(matched)
        rule_ids = [r.id for r in matched]
        disc = deps.summarize(rules, registry)
    _phase(progress_info, "match", "해야 할 일 목록을 정리하는 중입니다…")
    cl = build_checklist(pairs, registry, winget, list_text, rule_ids=rule_ids, show_all=show_all,
                         discovery=disc)
    return ChecklistResult(render_checklist(cl, fmt), cl, disc)


# --------------------------------------------------------------------------- install-check

def run_install_check(package: str | Path, *, deps: Deps | None = None,
                      progress_info: Callable[[ProgressInfo], None] | None = None,
                      new_state: NewState | None = None) -> InstallCheck:
    """묶음의 구 PC 설치 목록과 이 PC(새 PC)의 현재 설치 상태를 비교한다. 읽기 전용(winget import/install 은 실행하지 않는다).

    new_state 를 주면 이 PC 를 읽지 않고 그 값을 쓴다(시험/--winget-list-file 용). 패키지가 손상되면 ApiError.
    """
    deps = deps or Deps()
    if is_unfinished_part(Path(package)):
        raise ApiError("이 파일(.part)은 아직 완성되지 않은 임시 파일입니다. 완성된 .zip 파일을 선택하세요.")
    manifest, problems = verify_package(Path(package))
    if manifest is None or problems:
        raise ApiError("패키지 검증 실패: " + "; ".join(problems[:3]))
    try:
        registry, winget, list_text = load_package_programs(Path(package))
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, zipfile.BadZipFile, OSError) as e:
        raise ApiError(f"패키지의 프로그램 목록을 읽을 수 없습니다({type(e).__name__}). 구 PC 에서 설정 묶음 파일을 다시 만들어 주세요.") from e
    _phase(progress_info, "list", "이 PC 에 설치된 프로그램 목록을 읽는 중입니다…")
    state = new_state if new_state is not None else read_new_state(deps.registry, deps.winget_export, deps.winget_list)
    _phase(progress_info, "match", "묶음의 프로그램과 비교하는 중입니다…")
    return check_install(registry, winget, list_text, state)


# --------------------------------------------------------------------------- discover

def run_discover(rules_dir: str | Path, *, min_size_kb: int = 0, show_all: bool = False,
                 progress: Callable[[str], None] | None = None, deps: Deps | None = None,
                 cancel: Callable[[], bool] | None = None,
                 progress_info: Callable[[ProgressInfo], None] | None = None) -> Discovery:
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    if progress:
        progress("설치된 프로그램 목록을 읽는 중...")
    _phase(progress_info, "list", "설치된 프로그램 목록을 읽는 중입니다…")
    kw = {"progress_info": progress_info} if progress_info else {}
    return discover(rules, deps.registry(), min_size_kb=min_size_kb, show_all=show_all, progress=progress,
                    cancel=cancel, **kw)


def find_program_in(rules_dir: str | Path, name: str, deps: Deps | None = None):
    """등록된 설치 프로그램 중 name 과 같은(대소문자 무시) 것 하나. 정확히 일치가 없으면 이름에 name 이 들어 있는 것이 하나일 때만 허용한다."""
    deps = deps or Deps()
    reg = programs_registry(deps.registry())
    low = name.strip().lower()
    exact = [p for p in reg if p.name.lower() == low]
    hits = exact or [p for p in reg if low and low in p.name.lower()]
    names = sorted({p.name for p in hits})
    if not hits:
        raise ApiError(f"설치된 프로그램에서 '{name}' 을(를) 찾지 못했습니다.",
                       lines=["힌트: pcmigrate discover 로 규칙 없는 프로그램 목록을 보고 이름을 그대로 쓰세요."])
    if len(names) > 1:
        raise ApiError(f"'{name}' 과(와) 일치하는 프로그램이 {len(names)}개입니다. 더 정확한 이름을 쓰세요.",
                       lines=[f"  - {n}" for n in names[:10]])
    return hits[0]


def run_find_program(rules_dir: str | Path, name: str, *, deps: Deps | None = None, total_limit: float = 30.0,
                     progress: Callable[[str], None] | None = None, cancel: Callable[[], bool] | None = None,
                     progress_info: Callable[[ProgressInfo], None] | None = None) -> ProgramFind:
    """'이 프로그램 설정 찾기': 프로그램 하나의 설정 위치만 국소적으로 찾는다(읽기 전용, 자동 선택 없음). name 은 설치 프로그램 이름."""
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    program = find_program_in(rules_dir, name, deps)
    kw = {"progress_info": progress_info} if progress_info else {}
    return find_program(program, rules, total_limit=total_limit, progress=progress, cancel=cancel, **kw)


def selection_json(d: Discovery, show_all: bool = False) -> str:
    return json.dumps(build_selection(d, show_all), ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------- export

@dataclass
class ExtraPrep:
    items: list[ExtraItem]
    risky: int = 0  # --allow-secrets 로 포함하는 비밀 정보 가능성 항목 수


def prepare_extras(extra: str | Path | None, allow_secrets: bool,
                   items: list[ExtraItem] | None = None, *, allow_unverified: bool = False) -> ExtraPrep:
    """선택 파일(또는 이미 검증된 items)의 비밀 정보 가능성을 다시 검사한다. 위험한데 허용 안 했으면 ApiError.

    기본(CLI)은 검사를 끝내지 못한 항목(너무 커서 시간 초과, 읽기 거부)도 '위험'으로 본다. allow_unverified=True(GUI)면
    비밀 이름이 하나도 발견되지 않은 항목은 검사를 못 끝냈어도 막지 않는다: 사용자가 민감 항목을 하나도 고르지 않았는데
    '민감 정보가 있을 수 있다'는 오류로 묶음을 못 만드는 일을 막는다(비밀 이름이 실제로 발견되면 여전히 막는다)."""
    if items is None:
        if not extra:
            return ExtraPrep([])
        try:
            items = load_selection(Path(extra))
        except SelectionError as e:
            raise ApiError(f"선택 파일 오류: {e}") from e
    risky = []
    for it in items:
        names, complete = secret_names(it)
        if names or (not complete and not allow_unverified):
            risky.append((it, names))
    if risky and not allow_secrets:
        lines = [f"비밀 정보 가능성: {it.path} ({', '.join(names) if names else '비밀 정보 검사를 끝내지 못함'})"
                 for it, names in risky]
        raise ApiError("비밀 정보(키, 토큰, 쿠키, 비밀번호 등)가 들어 있을 수 있는 항목이 선택되었습니다. 설정 묶음 파일은 만들지 않았습니다. "
                       "정말 포함하려면 내용을 확인한 뒤 --allow-secrets 를 붙여 다시 실행하세요.", lines=lines)
    return ExtraPrep(items, len(risky))


@dataclass
class ExportResult:
    manifest: dict
    extras: ExtraPrep
    matched: list[Rule]
    notes: list[str]  # 사용자에게 알릴 안내(규칙 없음, winget 실패 등)
    checklist: Checklist
    discovery: tuple[int, int] | None

    @property
    def unreadable(self) -> list[dict]:
        """읽지 못해 제외된 파일(사유 코드가 있는 failed 항목). 패키지에는 흔적 없이 빠졌다."""
        return [f for f in self.manifest["failed"] if f.get("reason")]

    @property
    def denied(self) -> list[dict]:
        """보안상 복원하지 않는 형식이라 패키지에 넣지 않은 파일(skipped 의 denied 항목)."""
        return [s for s in self.manifest["skipped"] if s.get("denied")]

    @property
    def hard_failures(self) -> list[dict]:
        """읽기 실패가 아닌 실패(중복 경로 등). 이것이 있으면 CLI 는 실패 코드로 끝낸다."""
        return [f for f in self.manifest["failed"] if not f.get("reason")]

    @property
    def resume(self) -> dict:
        """저장점 이어받기 결과({resumed, kept_files, kept_bytes, dropped, reason}). 정보가 없으면 빈 dict."""
        return self.manifest.get("resume") or {}

    def summary_dict(self, output: str) -> dict:
        m = self.manifest
        n_cache, f_cache, b_cache = coverage.cache_totals(m)
        d = {"output": output, "rules": [r["id"] for r in m["rules"]], "files": len(m["files"]),
             "skipped": len(m["skipped"]), "failed": len(m["failed"]), "unreadable": len(self.unreadable),
             "denied": len(self.denied)}
        if self.resume.get("resumed"):
            d["resumed_files"] = self.resume["kept_files"]
        if n_cache:
            d["cache_dirs_skipped"], d["cache_files_skipped"], d["cache_bytes_skipped"] = n_cache, f_cache, b_cache
        cfg = coverage.config_totals(m)
        if cfg["items"]:
            d["extra_mode"] = m.get("extra_mode", EXTRA_MODE_ALL)
            d["config_files"], d["config_bytes"] = cfg["allowed_files"], cfg["allowed_bytes"]
            d["config_ext_counts"] = cfg["ext_counts"]
            d["not_config_files"], d["not_config_bytes"] = cfg["not_config_files"], cfg["not_config_bytes"]
            d["limit_files"], d["limit_bytes"] = cfg["limit_files"], cfg["limit_bytes"]
        d["coverage"] = [{k: r[k] for k in ("program", "what", "status", "files")} for r in coverage.rows(m)]
        return d

    def unreadable_lines(self, limit: int = 5) -> list[str]:
        return unreadable_lines(self.unreadable, limit)


def run_export(rules_dir: str | Path, output: str | Path, *, extra: str | Path | None = None,
               allow_secrets: bool = False, deps: Deps | None = None,
               extra_items: list[ExtraItem] | None = None,
               progress_info: Callable[[ProgressInfo], None] | None = None,
               resume: bool = True, compress: str = "auto",
               notice: Callable[[str], None] | None = None,
               stage_dir: str | Path | None = None, include_caches: bool = False,
               allow_unverified: bool = False, extra_mode: str = DEFAULT_EXTRA_MODE) -> ExportResult:
    """설정 패키지 내보내기. OSError(출력 경로 문제)는 전파, 나머지 실패는 ApiError.

    resume=True 면 같은 출력 경로의 이전 저장점(.part)을 이어받고, False 면 이전 임시물을 지우고 처음부터 만든다.
    compress 는 auto/always/never. notice 는 '이어서 하는 중' 같은 안내 문장을 받는다.
    stage_dir 는 64MB 넘는 파일을 담기 전에 임시로 읽어 두는 폴더의 위치(기본: 출력 폴더). 원본 폴더 안은 거부한다.
    include_caches=False(기본)면 직접 고른 폴더 안의 재생성되는 임시 저장물(Cache, node_modules 등)은 담지 않는다(skipped 에 기록).
    extra_mode: 'config'(기본, 허용 방식) 직접 고른 폴더에서 설정 형식 파일만 담고 크기/파일 수 상한을 적용한다. 'all' 은 예전 방식(폴더 전체).
    """
    deps = deps or Deps()
    if extra_mode not in EXTRA_MODES:
        raise ApiError(f"--extra-mode 는 {', '.join(EXTRA_MODES)} 중 하나여야 합니다: {extra_mode}")
    if compress not in COMPRESS_MODES:
        raise ApiError(f"--compress 는 {', '.join(COMPRESS_MODES)} 중 하나여야 합니다: {compress}")
    extras = prepare_extras(extra, allow_secrets, items=extra_items, allow_unverified=allow_unverified)
    rules = load_rules_checked(rules_dir)
    winget_text = deps.winget_export()
    registry = deps.registry()
    list_text = deps.winget_list() if winget_text else None
    winget = parse_winget_export(winget_text) if winget_text else []
    matched = match_rules(rules, registry + winget)
    notes = []
    if not matched:
        notes.append("감지된 프로그램 규칙이 없어 프로그램 목록만 내보냅니다.")
    if winget_text is None:
        notes.append("경고: winget export 실패 - 레지스트리 목록만 포함됩니다.")
    try:
        kw = {"progress_info": progress_info} if progress_info else {}
        # 기본값이 아닌 선택만 넘기고, 받지 못하는(예전/가짜) export 구현에는 넘기지 않는다
        want = {"resume": resume, "compress": compress, "notice": notice, "stage_dir": str(stage_dir) if stage_dir else None,
                "include_caches": include_caches, "extra_mode": extra_mode}
        defaults = {"resume": True, "compress": "auto", "notice": None, "stage_dir": None, "include_caches": False,
                    "extra_mode": DEFAULT_EXTRA_MODE}
        try:
            params = inspect.signature(deps.export).parameters
            varkw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        except (TypeError, ValueError):
            params, varkw = {}, False
        kw.update({k: v for k, v in want.items() if v != defaults[k] and (varkw or k in params)})
        manifest = deps.export(matched, Path(output), registry, winget_text, winget_list_text=list_text,
                               extras=extras.items, **kw)
    except PackageError as e:
        raise ApiError(f"내보내기 실패: {e}") from e
    cl = build_checklist(manifest_warning_pairs(manifest), registry, winget, list_text,
                         rule_ids=[r["id"] for r in manifest["rules"]])
    return ExportResult(manifest, extras, matched, notes, cl, discovery_summary(rules, registry, deps))


# --------------------------------------------------------------------------- export: 읽지 못해 제외된 파일만 다시 담기

@dataclass
class RetryResult:
    """run_export_retry 결과. manifest 는 갱신된 묶음의 목록(ExportResult 와 같은 모양이라 화면/요약이 그대로 쓴다)."""
    manifest: dict
    output: str

    @property
    def info(self) -> dict:
        return self.manifest.get("retry") or {}

    @property
    def added(self) -> list[dict]:
        return self.info.get("added", [])

    @property
    def still_failed(self) -> list[dict]:
        """이번에도 읽지 못했거나 다시 담지 않은 파일(여전히 묶음 목록의 failed 에 있다)."""
        return [f for f in self.manifest["failed"] if f.get("reason")]

    unreadable = still_failed

    @property
    def unchanged(self) -> bool:
        return bool(self.info.get("unchanged", True))

    @property
    def denied(self) -> list[dict]:
        return [s for s in self.manifest["skipped"] if s.get("denied")]

    @property
    def hard_failures(self) -> list[dict]:
        return [f for f in self.manifest["failed"] if not f.get("reason")]

    @property
    def resume(self) -> dict:
        return {}

    def unreadable_lines(self, limit: int = 5) -> list[str]:
        return unreadable_lines(self.still_failed, limit)

    def summary_dict(self) -> dict:
        i, m = self.info, self.manifest
        return {"output": self.output, "retried": i.get("total", 0), "added": len(self.added),
                "still_failed": len(self.still_failed), "gone": len(i.get("gone", [])), "files": len(m["files"]),
                "unchanged": self.unchanged, "backup": i.get("backup", ""),
                "reasons": {code: sum(1 for f in self.still_failed if f.get("reason") == code)
                            for code in sorted({f.get("reason") for f in self.still_failed})},
                "missing_rules": i.get("missing_rules", [])}


def run_export_retry(rules_dir: str | Path, output: str | Path, *, deps: Deps | None = None,
                     progress_info: Callable[[ProgressInfo], None] | None = None,
                     notice: Callable[[str], None] | None = None,
                     stage_dir: str | Path | None = None) -> RetryResult:
    """이미 만든 설정 묶음에서 '읽지 못해 제외된 파일'만 다시 읽어 그 묶음에 추가한다(새로 만들지 않는다).

    묶음이 없거나 끝나지 않은 임시 파일(.part)이거나 검사에 실패하면 ApiError. 기존 묶음은 .bak 으로 보존되고,
    실패/취소 때는 바뀌지 않는다. OSError(저장 위치 문제)는 전파한다.
    """
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    kw: dict = {}
    if progress_info:
        kw["progress_info"] = progress_info
    if notice:
        kw["notice"] = notice
    if stage_dir:
        kw["stage_dir"] = str(stage_dir)
    try:
        manifest = deps.retry_export(Path(output), rules, **kw)
    except PackageError as e:
        raise ApiError(f"다시 담기 실패: {e}") from e
    return RetryResult(manifest, str(output))


# --------------------------------------------------------------------------- import

@dataclass
class ImportResult:
    """mode: blocked(검증 실패) / dry-run / needs-confirm(--yes 없음) / needs-allow-extra / applied"""
    plan: ImportPlan
    mode: str
    n_extra: int
    report: Report | None = None
    record_path: str = ""   # 복원 기록 파일(되돌리기 근거). 실제로 복원했을 때만 있다
    record_error: str = ""  # 기록을 저장하지 못했을 때의 사유(복원 자체는 끝난 뒤이므로 실패로 보지 않고 알린다)
    only: tuple = ()
    skip: tuple = ()

    @property
    def programs(self) -> list[dict]:
        """프로그램별 표(화면의 프로그램별 체크용): id/name/kind/selected/files/bytes/new/overwrite/same/denied/rejected/not_selected."""
        return self.plan.programs

    @property
    def written(self) -> bool:
        return self.mode == "applied"

    @property
    def denied(self) -> list[dict]:
        """보안상 복원하지 않는(항목별 '복원 안 함') 계획 항목."""
        return [it for it in self.plan.items if it.get("status") == "denied"]

    @property
    def git_config_warning(self) -> str:
        """복원 대상에 Git 설정 파일(.gitconfig)이 있을 때의 경고 한 줄(없으면 빈 문자열). 복원은 허용하되 내용 확인을 권한다."""
        return self.plan.git_config_warning

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self.plan.items:
            out[it["status"]] = out.get(it["status"], 0) + 1
        return out

    def preview_dict(self) -> dict:
        """미리보기 JSON. 쓰기가 없었으면 dry_run 은 true(실제 상태)로 표시한다."""
        d = {"dry_run": not self.written, "mode": self.mode, "written": self.written,
             "summary": self.summary(), "problems": self.plan.problems, "items": self.plan.items,
             "programs": self.plan.programs, "selection": {"only": list(self.only), "skip": list(self.skip)}}
        if self.record_path:
            d["import_record"] = self.record_path
        d["coverage"] = [{k: r[k] for k in ("program", "what", "status", "files")} for r in coverage.rows(self.plan.manifest)]
        if self.git_config_warning:
            d["git_config_warning"] = self.git_config_warning
        return d


def run_import(package: str | Path, rules_dir: str | Path, *, dry_run: bool = False, yes: bool = False,
               allow_extra: bool = False, deps: Deps | None = None,
               progress_info: Callable[[ProgressInfo], None] | None = None,
               only_programs: list[str] | str | None = None, skip_programs: list[str] | str | None = None,
               record_dir: str | Path | None = None) -> ImportResult:
    """설정 패키지 복원. only_programs/skip_programs 로 프로그램(규칙 id 또는 extra 항목)별 복원을 고를 수 있다
    (쉼표로 이은 문자열이나 목록). 선택에서 뺀 프로그램은 쓰지 않고 '선택에서 제외됨'으로 기록한다. 묶음에 없는 이름이면 ApiError.
    실제로 복원하면 복원 기록(import_record)을 record_dir(기본: %LOCALAPPDATA%\\pcmigrate\\import-records)에 남긴다(undo-import 의 근거)."""
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    only, skip = tuple(_tokens(only_programs)), tuple(_tokens(skip_programs))
    sel = {}
    if only:
        sel["only_programs"] = list(only)
    if skip:
        sel["skip_programs"] = list(skip)
    try:
        plan = deps.plan(Path(package), rules, **sel)
    except PackageError as e:
        raise ApiError(str(e) if str(e).startswith("패키지 검증 실패") else f"패키지 검증 실패: {e}") from e
    n_extra = sum(1 for it in plan.items if it.get("extra") and it.get("status") != NOT_SELECTED)
    if plan.problems:
        return ImportResult(plan, "blocked", n_extra, only=only, skip=skip)
    if plan.unknown_programs:
        raise ApiError("고른 프로그램이 이 패키지에 없습니다: " + ", ".join(plan.unknown_programs),
                       lines=["패키지에 있는 프로그램(--only/--skip 에 쓸 수 있는 이름):"]
                       + [f"  - {p['id']} ({p['name']})" for p in plan.programs])
    if dry_run or not yes:  # --dry-run 이 --yes 보다 우선
        return ImportResult(plan, "dry-run" if dry_run else "needs-confirm", n_extra, only=only, skip=skip)
    if n_extra and not allow_extra:
        return ImportResult(plan, "needs-allow-extra", n_extra, only=only, skip=skip)
    report = apply_import(Path(package), plan, progress_info) if progress_info else apply_import(Path(package), plan)
    report.meta["programs"] = plan.programs
    res = ImportResult(plan, "applied", n_extra, report, only=only, skip=skip)
    try:  # 되돌리기의 근거: 복원 기록. 저장하지 못해도 이미 끝난 복원은 성공으로 두고 알린다
        rec = import_record.build_record(package, plan, report)
        res.record_path = str(import_record.write_record(rec, record_dir))
        report.meta["import_record"] = res.record_path
    except OSError as e:
        res.record_error = f"{type(e).__name__}: {e.strerror or e}"
    return res


def _tokens(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    return [t.strip() for t in v if isinstance(t, str) and t.strip()]


# --------------------------------------------------------------------------- import: 프로그램별 되돌리기

@dataclass
class UndoResult:
    """mode: dry-run / needs-confirm / applied / nothing(되돌릴 것이 없음)"""
    plan: import_record.UndoPlan
    mode: str
    record: dict

    @property
    def written(self) -> bool:
        return self.mode == "applied"

    def summary_dict(self) -> dict:
        p = self.plan
        d = {"mode": self.mode, "dry_run": not self.written, "written": self.written, "record": p.record_path,
             "programs": p.programs, **p.summary(),
             "items": [{"program": i.program, "target": i.target, "backup": i.backup, "action": i.action,
                        "result": i.result, "detail": i.detail} for i in p.items]}
        if self.written:
            d["restored"] = sum(1 for i in p.items if i.result == import_record.RESTORED)
            d["moved_new"] = sum(1 for i in p.items if i.result == import_record.MOVED)
            d["failed"] = sum(1 for i in p.items if i.result == import_record.FAILED_UNDO)
        return d


def run_undo_import(only_programs: list[str] | str | None, *, record: str | Path | None = None,
                    record_dir: str | Path | None = None, dry_run: bool = False, yes: bool = False,
                    move_new: bool = False) -> UndoResult:
    """복원 기록(기본: 가장 최근)을 근거로 선택한 프로그램의 .bak 을 원래 이름으로 되돌린다.

    복원이 새로 만든 파일은 지우지 않는다(move_new=True 면 '<이름>.removed' 로 옮긴다). 되돌릴 .bak 이 없거나 바뀌었으면 그 파일만
    되돌리지 않고 이유를 알린다. dry_run 이거나 yes 가 아니면 파일을 바꾸지 않는다. 기록이 없거나 프로그램이 기록에 없으면 ApiError."""
    only = _tokens(only_programs)
    if not only:
        raise ApiError("되돌릴 프로그램을 --only 로 지정하세요(예: --only revit,autocad).")
    path = Path(record) if record else import_record.latest_record(record_dir)
    if path is None:
        raise ApiError("복원 기록이 없습니다. 이 PC 에서 import --yes 로 복원한 적이 없거나 기록 폴더가 다릅니다.",
                       lines=["이전 기록 파일 경로를 알면 --record 로 직접 지정하세요."])
    try:
        rec = import_record.load_record(path)
    except import_record.RecordError as e:
        raise ApiError(str(e)) from e
    from .package import program_matches
    known = import_record.record_programs(rec)
    missing = [t for t in only if not any(program_matches(t, k) for k in known)]
    if missing:
        raise ApiError("복원 기록에 없는 프로그램입니다: " + ", ".join(missing),
                       lines=["기록에서 되돌릴 수 있는 프로그램:"] + [f"  - {k}" for k in known] if known
                       else ["이 기록에는 되돌릴 파일(실제로 복원한 파일)이 없습니다."])
    plan = import_record.plan_undo(rec, path, only, move_new=move_new)
    todo = plan.count(import_record.RESTORE, import_record.MOVE_NEW)
    if dry_run or not yes:
        return UndoResult(plan, "dry-run" if dry_run else "needs-confirm", rec)
    if not todo:
        return UndoResult(plan, "nothing", rec)
    import_record.apply_undo(rec, plan)
    return UndoResult(plan, "applied", rec)


# --------------------------------------------------------------------------- winget-extract

def run_winget_extract(package: str | Path, output: str | Path) -> tuple[Path, Path | None]:
    """패키지의 programs/winget.json 을 <output>/winget.json 으로 꺼낸다. (새 파일, .bak 으로 옮긴 이전 파일 또는 None)."""
    try:
        return extract_winget(Path(package), Path(output))
    except PackageError as e:
        raise ApiError(str(e)) from e


# --------------------------------------------------------------------------- 전송 기록 파일 정리

def clean_state_files(dst: str | Path) -> list[str]:
    """전송 성공 + 검사 통과 뒤에 대상 폴더 맨 위의 이 도구 기록 파일(.pcmigrate-state.json, -preserve.json, -robocopy.log)을 지운다.

    복사한 데이터 파일은 건드리지 않는다. 지운 파일 이름 목록을 돌려준다. 지우지 못한 것은 조용히 남긴다."""
    from .transfer import LOG_NAME, PRESERVE_NAME, STATE_NAME
    removed = []
    for name in (STATE_NAME, PRESERVE_NAME, LOG_NAME):
        p = Path(dst) / name
        try:
            if p.is_file():
                p.unlink()
                removed.append(name)
        except OSError:
            pass
    return removed
