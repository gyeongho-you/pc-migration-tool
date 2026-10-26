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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .cancel import Cancelled, CancelToken  # noqa: F401  (GUI 가 api 에서 가져다 쓴다)
from .checklist import (Checklist, build_checklist, bundle_reason, load_package_programs,
                        manifest_warning_pairs, render as render_checklist, rule_warning_pairs)
from .copier import Report, copy_tree
from .detect import (Program, match_rules, parse_winget_export, read_registry_programs,
                     winget_export_raw, winget_list_raw)
from .discover import (Discovery, ExtraItem, SelectionError, build_selection, discover, items_from_selection,
                       load_selection,
                       program_has_rule, secret_names, summarize)
import inspect
from .package import (COMPRESS_MODES, ImportPlan, PackageError, apply_import, export_package, plan_import,
                      verify_package)
from .rules import Rule, RuleError, load_rules
from . import fs
from .manifest import build_manifest, verify_against_manifest
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
    lines = [f"[주의] 읽지 못해 제외된 파일 {len(items)}개 — 사용 중인 파일이라 패키지에 넣지 못했습니다"
             "(예: Chrome 같은 프로그램이 실행 중이면 닫고 다시 하세요)."]
    lines += [f"  - {f['src']}" for f in items[:limit]]
    if len(items) > limit:
        lines.append(f"  … 외 {len(items) - limit}개(전체 목록은 패키지의 manifest 'failed' 항목에 있습니다)")
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
                 copy_mode: str = "restartable", copy_threads: int | None = None) -> Report:
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


def selection_json(d: Discovery, show_all: bool = False) -> str:
    return json.dumps(build_selection(d, show_all), ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------- export

@dataclass
class ExtraPrep:
    items: list[ExtraItem]
    risky: int = 0  # --allow-secrets 로 포함하는 비밀 정보 가능성 항목 수


def prepare_extras(extra: str | Path | None, allow_secrets: bool,
                   items: list[ExtraItem] | None = None) -> ExtraPrep:
    """선택 파일(또는 이미 검증된 items)의 비밀 정보 가능성을 다시 검사한다. 위험한데 허용 안 했으면 ApiError."""
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
        if names or not complete:
            risky.append((it, names))
    if risky and not allow_secrets:
        lines = [f"비밀 정보 가능성: {it.path} ({', '.join(names) if names else '비밀 정보 검사를 끝내지 못함'})"
                 for it, names in risky]
        raise ApiError("비밀 정보(키, 토큰, 쿠키, 비밀번호 등)가 들어 있을 수 있는 항목이 선택되었습니다. 패키지는 만들지 않았습니다. "
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
    def hard_failures(self) -> list[dict]:
        """읽기 실패가 아닌 실패(중복 경로 등). 이것이 있으면 CLI 는 실패 코드로 끝낸다."""
        return [f for f in self.manifest["failed"] if not f.get("reason")]

    @property
    def resume(self) -> dict:
        """저장점 이어받기 결과({resumed, kept_files, kept_bytes, dropped, reason}). 정보가 없으면 빈 dict."""
        return self.manifest.get("resume") or {}

    def summary_dict(self, output: str) -> dict:
        m = self.manifest
        d = {"output": output, "rules": [r["id"] for r in m["rules"]], "files": len(m["files"]),
             "skipped": len(m["skipped"]), "failed": len(m["failed"]), "unreadable": len(self.unreadable)}
        if self.resume.get("resumed"):
            d["resumed_files"] = self.resume["kept_files"]
        return d

    def unreadable_lines(self, limit: int = 5) -> list[str]:
        return unreadable_lines(self.unreadable, limit)


def run_export(rules_dir: str | Path, output: str | Path, *, extra: str | Path | None = None,
               allow_secrets: bool = False, deps: Deps | None = None,
               extra_items: list[ExtraItem] | None = None,
               progress_info: Callable[[ProgressInfo], None] | None = None,
               resume: bool = True, compress: str = "auto",
               notice: Callable[[str], None] | None = None) -> ExportResult:
    """설정 패키지 내보내기. OSError(출력 경로 문제)는 전파, 나머지 실패는 ApiError.

    resume=True 면 같은 출력 경로의 이전 저장점(.part)을 이어받고, False 면 이전 임시물을 지우고 처음부터 만든다.
    compress 는 auto/always/never. notice 는 '이어서 하는 중' 같은 안내 문장을 받는다.
    """
    deps = deps or Deps()
    if compress not in COMPRESS_MODES:
        raise ApiError(f"--compress 는 {', '.join(COMPRESS_MODES)} 중 하나여야 합니다: {compress}")
    extras = prepare_extras(extra, allow_secrets, items=extra_items)
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
        want = {"resume": resume, "compress": compress, "notice": notice}
        defaults = {"resume": True, "compress": "auto", "notice": None}
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


# --------------------------------------------------------------------------- import

@dataclass
class ImportResult:
    """mode: blocked(검증 실패) / dry-run / needs-confirm(--yes 없음) / needs-allow-extra / applied"""
    plan: ImportPlan
    mode: str
    n_extra: int
    report: Report | None = None

    @property
    def written(self) -> bool:
        return self.mode == "applied"

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self.plan.items:
            out[it["status"]] = out.get(it["status"], 0) + 1
        return out

    def preview_dict(self) -> dict:
        """미리보기 JSON. 쓰기가 없었으면 dry_run 은 true(실제 상태)로 표시한다."""
        return {"dry_run": not self.written, "mode": self.mode, "written": self.written,
                "summary": self.summary(), "problems": self.plan.problems, "items": self.plan.items}


def run_import(package: str | Path, rules_dir: str | Path, *, dry_run: bool = False, yes: bool = False,
               allow_extra: bool = False, deps: Deps | None = None,
               progress_info: Callable[[ProgressInfo], None] | None = None) -> ImportResult:
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    try:
        plan = deps.plan(Path(package), rules)
    except PackageError as e:
        raise ApiError(str(e) if str(e).startswith("패키지 검증 실패") else f"패키지 검증 실패: {e}") from e
    n_extra = sum(1 for it in plan.items if it.get("extra"))
    if plan.problems:
        return ImportResult(plan, "blocked", n_extra)
    if dry_run or not yes:  # --dry-run 이 --yes 보다 우선
        return ImportResult(plan, "dry-run" if dry_run else "needs-confirm", n_extra)
    if n_extra and not allow_extra:
        return ImportResult(plan, "needs-allow-extra", n_extra)
    report = apply_import(Path(package), plan, progress_info) if progress_info else apply_import(Path(package), plan)
    return ImportResult(plan, "applied", n_extra, report)
