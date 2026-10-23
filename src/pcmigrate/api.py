"""CLI 와 GUI 가 함께 쓰는 실행 로직. 출력(print)은 하지 않고 결과 객체를 돌려준다.

규칙:
- 여기 함수는 stdout/stderr 에 아무것도 쓰지 않는다. 메시지는 결과 객체의 필드(notes 등)나 ApiError 로 전달하고,
  어떻게 보여 줄지는 호출자(cli.py 의 터미널 출력, 이후 GUI)가 정한다.
- 파일 쓰기 실패(OSError)는 그대로 전파한다. 호출자는 format_write_error 로 사용자용 문장을 만든다.
- 외부 호출(레지스트리, winget, 요약, export 등)은 Deps 로 주입할 수 있다(테스트/GUI 에서 교체).
"""
from __future__ import annotations

import ctypes
import json
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
from .package import (ImportPlan, PackageError, apply_import, export_package, plan_import,
                      verify_package)
from .rules import Rule, RuleError, load_rules
from .transfer import cancellable_runner, transfer

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


# --------------------------------------------------------------------------- 공통 보조

def format_write_error(path: str, exc: OSError) -> list[str]:
    """파일 쓰기 실패를 traceback 대신 사용자용 문장(한 줄 + 선택적 힌트)으로."""
    p = exc.filename if isinstance(getattr(exc, "filename", None), str) else path
    cause = getattr(exc, "strerror", None) or str(exc)
    lines = [f"출력 경로를 만들 수 없습니다: {p} ({cause})"]
    if UNEXPANDED.search(p):
        lines.append(SHELL_HINT)
    elif isinstance(exc, FileNotFoundError) or getattr(exc, "winerror", None) in (3, 15):
        lines.append("힌트: 드라이브 문자와 폴더 이름이 맞는지 확인하세요(존재하지 않는 드라이브/폴더일 수 있습니다).")
    return lines


def check_unexpanded(**paths: str | None) -> list[str]:
    """환경변수가 확장되지 않은 경로 인자가 있으면 오류 문장들(없으면 빈 목록)."""
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
            low = p.name.lower()
            if any(pat.lower() in low for pat in r.display_name_patterns) and p.name not in names:
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


def run_scan(rules_dir: str | Path, deps: Deps | None = None) -> ScanResult:
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    winget_text = deps.winget_export()
    programs = deps.registry() + (parse_winget_export(winget_text) if winget_text else [])
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
                 cancel: Callable[[], bool] | None = None) -> Report:
    """대용량 전송. TransferError/OSError 는 그대로 전파한다. 취소하면 Cancelled(진행 상태는 저장됨)."""
    deps = deps or Deps()
    kw = dict(dry_run=dry_run, include_cloud=include_cloud,
              log_path=Path(log_path) if log_path else None, warn=warn, strict=strict)
    if progress:
        kw["progress"] = progress
    if cancel:
        kw["cancel"] = cancel
        kw["runner"] = cancellable_runner(cancel)
    if state_path:
        kw["state_path"] = Path(state_path)
    return deps.transfer(Path(src), Path(dst), **kw)


# --------------------------------------------------------------------------- checklist

@dataclass
class ChecklistResult:
    text: str
    checklist: Checklist
    discovery: tuple[int, int | None] | None


def run_checklist(rules_dir: str | Path, *, package: str | Path | None = None, show_all: bool = False,
                  fmt: str = "text", deps: Deps | None = None) -> ChecklistResult:
    deps = deps or Deps()
    if package:
        manifest, problems = verify_package(Path(package))
        if manifest is None or problems:
            raise ApiError("패키지 검증 실패: " + "; ".join(problems[:3]))
        registry, winget, list_text = load_package_programs(Path(package))
        pairs = manifest_warning_pairs(manifest)
        rule_ids = [r["id"] for r in manifest.get("rules", [])]
        try:
            all_rules = load_rules(Path(rules_dir))
        except RuleError:
            all_rules = None
        disc = (count_rule_less_programs(all_rules, registry), None) if all_rules is not None else None
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
    cl = build_checklist(pairs, registry, winget, list_text, rule_ids=rule_ids, show_all=show_all,
                         discovery=disc)
    return ChecklistResult(render_checklist(cl, fmt), cl, disc)


# --------------------------------------------------------------------------- discover

def run_discover(rules_dir: str | Path, *, min_size_kb: int = 0, show_all: bool = False,
                 progress: Callable[[str], None] | None = None, deps: Deps | None = None,
                 cancel: Callable[[], bool] | None = None) -> Discovery:
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    if progress:
        progress("설치된 프로그램 목록을 읽는 중...")
    return discover(rules, deps.registry(), min_size_kb=min_size_kb, show_all=show_all, progress=progress,
                    cancel=cancel)


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

    def summary_dict(self, output: str) -> dict:
        m = self.manifest
        return {"output": output, "rules": [r["id"] for r in m["rules"]], "files": len(m["files"]),
                "skipped": len(m["skipped"]), "failed": len(m["failed"])}


def run_export(rules_dir: str | Path, output: str | Path, *, extra: str | Path | None = None,
               allow_secrets: bool = False, deps: Deps | None = None,
               extra_items: list[ExtraItem] | None = None) -> ExportResult:
    """설정 패키지 내보내기. OSError(출력 경로 문제)는 전파, 나머지 실패는 ApiError."""
    deps = deps or Deps()
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
        manifest = deps.export(matched, Path(output), registry, winget_text, winget_list_text=list_text,
                               extras=extras.items)
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
               allow_extra: bool = False, deps: Deps | None = None) -> ImportResult:
    deps = deps or Deps()
    rules = load_rules_checked(rules_dir)
    plan = deps.plan(Path(package), rules)
    n_extra = sum(1 for it in plan.items if it.get("extra"))
    if plan.problems:
        return ImportResult(plan, "blocked", n_extra)
    if dry_run or not yes:  # --dry-run 이 --yes 보다 우선
        return ImportResult(plan, "dry-run" if dry_run else "needs-confirm", n_extra)
    if n_extra and not allow_extra:
        return ImportResult(plan, "needs-allow-extra", n_extra)
    return ImportResult(plan, "applied", n_extra, apply_import(Path(package), plan))
