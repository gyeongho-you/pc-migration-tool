"""규칙 없는 프로그램 탐지(discover). 읽기 전용.

rules/*.json 에 없는 설치 프로그램을 사용자에게 반드시 보여 주기 위한 단계다.
프로그램별로 사용자 데이터 폴더 후보(AppData, Documents, ProgramData, 홈의 점 폴더/파일)를 찾아
근거 등급(확실/추정)과 크기/수정일/파일 수, 비밀 정보 가능성을 보여 준다.
자동으로 export 에 포함하지 않는다. 포함은 사용자가 선택 파일로 명시한 것만(export --extra).
"""
from __future__ import annotations

import functools
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .cancel import Cancelled
from .checklist import _norm, _norm_company, _norm_display, bundle_reason
from . import fs
from .envpaths import expand_env
from .detect import Program
from .discover_data import (BROWSER_VENDOR_DIRS, DOT_HINTS, DOT_SKIP, EXTRA_ROOTS, ROOTS, SECRET_PATTERNS, SKIP_NAMES,
                            SKIP_PREFIXES, SKIP_SUBSTRINGS, Root)
from .progress import ProgressInfo
from .rules import Rule

Expand = Callable[[str], str]
CERTAIN, PROBABLE, NONE = "확실", "추정", "없음"
SELECTION_FORMAT = 1
MAX_LINKED_NAMES = 5
SECRET_SAMPLES = 5


class SelectionError(ValueError):
    """선택 파일이 잘못되었거나 안전하지 않음."""


# --------------------------------------------------------------------------- 데이터 모델

@dataclass
class Link:
    programs: list[str]
    grade: str
    reason: str
    weak: bool = False  # 제조사 폴더 하나가 여러 프로그램에 연결된 경우 등 근거가 약함


@dataclass
class Candidate:
    root: str  # 루트 템플릿(예: %APPDATA%)
    rel: str  # 루트 기준 상대 경로(posix, 최상위 항목 하나)
    path: str  # 확장된 절대 경로(표시용)
    is_dir: bool
    link: Link | None = None
    size: int | None = None  # None = 크기 미확인
    files: int | None = None
    mtime: float | None = None
    secret: list[str] = field(default_factory=list)  # 비밀 정보 가능성이 있는 이름(샘플)
    scan_complete: bool = False
    rule_programs: list[str] = field(default_factory=list)  # 연결된 프로그램 중 규칙이 있는 것(숨김 사유)

    @property
    def key(self) -> tuple[str, str]:
        return (self.root, self.rel)


@dataclass
class ProgramEntry:
    program: Program
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Discovery:
    programs: list[ProgramEntry] = field(default_factory=list)  # 규칙 없는 프로그램(번들 제외)
    bundles: list[tuple[Program, str]] = field(default_factory=list)  # 숨긴 번들/구성요소
    home: list[Candidate] = field(default_factory=list)  # 홈의 점 폴더/파일(규칙 없는 프로그램과 무관하게 표시)
    unlinked: list[Candidate] = field(default_factory=list)  # 프로그램과 연결 못 한 폴더
    rule_linked: list[Candidate] = field(default_factory=list)  # 규칙이 있는 프로그램 쪽 폴더(숨김)
    rule_covered: int = 0  # 규칙 경로와 겹쳐 건너뛴 폴더 수
    with_sizes: bool = True
    hidden_small: int = 0

    @property
    def program_count(self) -> int:
        return len(self.programs)

    def candidates(self) -> list[Candidate]:
        """프로그램에 연결된 후보 + 홈 점 항목(중복 제거)."""
        seen: dict[tuple[str, str], Candidate] = {}
        for e in self.programs:
            for c in e.candidates:
                seen.setdefault(c.key, c)
        for c in self.home:
            seen.setdefault(c.key, c)
        return list(seen.values())

    @property
    def candidate_count(self) -> int:
        return len(self.candidates())

    @property
    def secret_count(self) -> int:
        return sum(1 for c in self.candidates() if c.secret)


# --------------------------------------------------------------------------- 후보 수집

def _nc(p: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _skip_name(name: str) -> bool:
    low = name.lower()
    return (low in SKIP_NAMES or any(low.startswith(p) for p in SKIP_PREFIXES)
            or any(s in low for s in SKIP_SUBSTRINGS))


def rule_concrete_paths(rules: list[Rule], expand: Expand) -> list[str]:
    """규칙 paths 가 실제로 가리키는(존재하는) 경로들. 후보가 이와 겹치면 규칙이 다루는 폴더로 본다."""
    from .package import WILDCARD, _usable_base, split_rule_path

    out: list[str] = []
    for r in rules:
        for pr in r.paths:
            split = split_rule_path(pr.path)
            if split is None:
                continue
            base_path = Path(expand(split[0]))
            if not _usable_base(base_path) or not base_path.is_dir():
                continue
            try:
                for m in base_path.glob(split[1]):
                    out.append(_nc(m))
            except (OSError, ValueError):
                continue
    return out


def _covered(cand_path: str, concrete: list[str]) -> bool:
    c = _nc(cand_path)
    sep = os.sep
    for r in concrete:
        if r == c or r.startswith(c + sep) or c.startswith(r + sep):
            return True
    return False


def _list_root(root: Root, expand: Expand) -> list[tuple[Root, Path, os.DirEntry]]:
    base = expand(root.template)
    if "%" in base or not os.path.isabs(base) or not os.path.isdir(base):
        return []
    out = []
    try:
        with os.scandir(base) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
    except OSError:
        return []
    for e in entries:
        name = e.name
        try:
            is_dir = e.is_dir(follow_symlinks=False)
            is_link = e.is_symlink()
        except OSError:
            continue
        if is_link:
            continue
        if root.dotfiles_only:
            if not name.startswith(".") or name.lower() in DOT_SKIP:
                continue
        else:
            if not is_dir or _skip_name(name):
                continue
        out.append((root, Path(base), e))
    return out


# --------------------------------------------------------------------------- 프로그램 연결

_TRAILING = re.compile(r"[\d.\-_ ]+$")
_VERSION_STRIP = re.compile(r"\bv?\d+(\.\d+)+\b|\b(x64|x86|64-?bit|32-?bit)\b")
_PAREN_STRIP = re.compile(r"\([^)]*\)")


def _folder_key(name: str) -> str:
    return _norm(_TRAILING.sub("", name)) or _norm(name)


@functools.lru_cache(maxsize=4096)
def _prep(name: str, publisher: str, install_location: str):
    """프로그램 이름/발행자/설치 위치의 정규화 결과(캐시). 마지막은 '앞쪽 단어들을 이어 붙인' 이름 집합."""
    loc = _nc(install_location) if install_location else ""
    words = re.findall(r"[0-9a-z가-힣]+", _VERSION_STRIP.sub(" ", _PAREN_STRIP.sub(" ", name.lower())))
    prefixes = frozenset("".join(words[:k]) for k in range(1, len(words) + 1))
    return (_norm(name), _norm_display(name), loc, _norm_company(publisher) if publisher else "",
            prefixes)


def link_candidate(name: str, path: str, programs: list[Program], is_home: bool) -> Link | None:
    """폴더 이름 정규화, 발행자 토큰, 설치 위치로 프로그램을 연결한다. 가장 강한 근거 등급의 프로그램들.

    등급: 확실 = 설치 위치가 폴더 안에 있거나 폴더 이름이 프로그램 이름과 같음.
    추정 = 폴더 이름이 프로그램 이름에 포함, 발행자 이름과 일치, 점 폴더 힌트 표.
    """
    fk, raw = _folder_key(name), _norm(name)
    cpath = _nc(path)
    best: list[tuple[int, str, str]] = []  # (rank, program name, reason)
    hints = DOT_HINTS.get(name.lower(), ()) if is_home else ()
    for p in programs:
        pn, pdisp, loc, pc, prefixes = _prep(p.name, p.publisher, p.install_location)
        cand: tuple[int, str] | None = None
        if is_home:
            if hints and any(h in p.name.lower() for h in hints):
                cand = (1, "점 폴더/파일 힌트 표 기준")
        elif loc and (loc == cpath or loc.startswith(cpath + os.sep)):
            cand = (3, "설치 위치가 이 폴더 안에 있음")
        elif (fk and fk in (pdisp, pn)) or (raw and raw in (pdisp, pn)):
            cand = (3, "폴더 이름이 프로그램 이름과 일치")
        elif len(fk) >= 4 and fk in prefixes:
            cand = (2, "폴더 이름이 프로그램 이름의 앞쪽 단어와 일치")
        elif len(fk) >= 5 and len(pc) >= 5 and (pc == fk or pc.startswith(fk) or fk.startswith(pc)):
            cand = (2, "발행자 이름과 일치")
        if cand:
            best.append((cand[0], p.name, cand[1]))
    if not best:
        return None
    top = max(b[0] for b in best)
    chosen = [b for b in best if b[0] == top]
    names: list[str] = []
    for _, n, _r in chosen:
        if n not in names:
            names.append(n)
    weak = top < 3 and len(names) > 1
    reason = chosen[0][2] + (f" — 근거 약함: 여러 프로그램({len(names)}개)에 연결됨" if weak else "")
    return Link(names[:MAX_LINKED_NAMES], CERTAIN if top == 3 else PROBABLE, reason, weak)


def program_has_rule(p: Program, rules: list[Rule]) -> bool:
    return any(r.matches_name(p.name) for r in rules)


# --------------------------------------------------------------------------- 크기/비밀 스캔

def scan_tree(path: Path, deadline: float, clock: Callable[[], float]) -> tuple[int, int, float | None, list[str], bool]:
    """(크기, 파일 수, 최신 수정 시각, 비밀 이름 샘플, 완료 여부). 심볼릭 링크는 따라가지 않는다. 읽기 전용."""
    size = files = 0
    newest: float | None = None
    secrets: list[str] = []
    stack = [str(path)]
    tick = 0
    complete = True
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    tick += 1
                    if tick % 200 == 0 and clock() > deadline:
                        return size, files, newest, secrets, False
                    low = e.name.lower()
                    if len(secrets) < SECRET_SAMPLES and any(s in low for s in SECRET_PATTERNS):
                        secrets.append(e.name)
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & fs.FILE_ATTRIBUTE_REPARSE_POINT:
                                continue  # 정션(reparse point)도 따라가지 않는다: 링크 너머의 크기를 세지 않는다
                            stack.append(e.path)
                        else:
                            st = e.stat(follow_symlinks=False)
                            size += st.st_size
                            files += 1
                            if newest is None or st.st_mtime > newest:
                                newest = st.st_mtime
                    except OSError:
                        complete = False
        except OSError:
            complete = False
        if clock() > deadline:
            return size, files, newest, secrets, False
    return size, files, newest, secrets, complete


def _flag_name(c: Candidate) -> None:
    """폴더/파일 이름만으로 판단하는 비밀 정보 가능성(크기 스캔과 무관하게 항상 적용)."""
    base = os.path.basename(c.path)
    low = base.lower()
    if any(s in low for s in SECRET_PATTERNS) and base not in c.secret:
        c.secret.append(base)
    if c.is_dir and low in BROWSER_VENDOR_DIRS:
        tag = f"브라우저 프로필/쿠키 가능성({base})"
        if tag not in c.secret:
            c.secret.append(tag)


def scan_candidate(c: Candidate, per_limit: float, clock: Callable[[], float], budget_end: float) -> None:
    _flag_name(c)
    if not c.is_dir:
        try:
            st = os.stat(c.path)
            c.size, c.files, c.mtime, c.scan_complete = st.st_size, 1, st.st_mtime, True
        except OSError:
            pass
        return
    now = clock()
    if now >= budget_end:
        return  # 전체 시간 상한 소진: 크기 미확인
    size, files, newest, secrets, complete = scan_tree(Path(c.path), min(now + per_limit, budget_end), clock)
    for s in secrets:
        if s not in c.secret and len(c.secret) < SECRET_SAMPLES:
            c.secret.append(s)
    c.scan_complete = complete
    if complete:
        c.size, c.files, c.mtime = size, files, newest
    else:
        c.size, c.files, c.mtime = None, files, newest  # 크기 미확인(시간 초과/접근 거부)


# --------------------------------------------------------------------------- 탐지 본체

def discover(rules: list[Rule], programs: list[Program], expand: Expand = expand_env,
             with_sizes: bool = True, per_limit: float = 3.0, total_limit: float = 60.0,
             clock: Callable[[], float] = time.monotonic, min_size_kb: int = 0,
             show_all: bool = False, progress: Callable[[str], None] | None = None,
             cancel: Callable[[], bool] | None = None,
             progress_info: Callable[[ProgressInfo], None] | None = None) -> Discovery:
    """규칙 없는 설치 프로그램과 사용자 데이터 폴더 후보를 찾는다.

    progress 가 있으면 진행 메시지(문자열)를 넘긴다. 출력 방식은 호출자(UI)가 정한다.
    """
    def say(msg: str) -> None:
        if progress is not None:
            progress(msg)

    def info(phase: str, msg: str, done: int = 0, total: int = 0, current: str = "") -> None:
        if progress_info is not None:  # GUI 현황 카드용(단계, 후보 폴더 처리 수, 현재 폴더)
            progress_info(ProgressInfo(phase, 0, 0, done, total, message=msg, current=current))

    t0 = clock()
    info("list", "설치 프로그램 목록과 규칙을 대조하는 중입니다…")
    say("설치 프로그램 목록과 규칙을 대조하는 중...")
    d = Discovery(with_sizes=with_sizes)
    reg = [p for p in programs if p.source == "registry"] or list(programs)
    seen_names: set[str] = set()
    ruleless: list[Program] = []
    rule_programs: list[Program] = []
    for p in reg:
        if p.name in seen_names:
            continue
        seen_names.add(p.name)
        if program_has_rule(p, rules):
            rule_programs.append(p)
            continue
        why = bundle_reason(p.name)
        if why:
            d.bundles.append((p, why))
            continue
        ruleless.append(p)
    ruleless.sort(key=lambda p: p.name.lower())

    say(f"규칙 없는 설치 프로그램 {len(ruleless)}개. 설정 폴더 후보를 찾는 중...")
    info("find", f"규칙 없는 설치 프로그램 {len(ruleless)}개. 설정 폴더 후보를 찾는 중입니다…")
    concrete = rule_concrete_paths(rules, expand)
    all_programs = ruleless + rule_programs + [bp for bp, _ in d.bundles]
    entries = {p.name: ProgramEntry(p) for p in ruleless}
    rule_names = {p.name for p in rule_programs}
    bundle_names = {bp.name for bp, _ in d.bundles}

    cands: list[Candidate] = []
    for root in ROOTS:
        for r, base, e in _list_root(root, expand):
            path = os.path.join(str(base), e.name)
            if _covered(path, concrete):
                d.rule_covered += 1
                continue
            c = Candidate(r.template, e.name, path, e.is_dir(follow_symlinks=False))
            c.link = link_candidate(e.name, path, all_programs, r.dotfiles_only)
            cands.append(c)

    start = clock()
    budget_end = start + total_limit
    if with_sizes:
        say(f"후보 폴더 {len(cands)}개의 크기를 계산합니다(읽기 전용, 폴더당 최대 {per_limit:g}초, "
            f"전체 최대 약 {total_limit:g}초)...")
    for i, c in enumerate(cands, 1):
        if cancel is not None and cancel():
            raise Cancelled()
        if with_sizes:
            info("scan", f"후보 폴더 크기를 재는 중입니다({i}/{len(cands)})", i - 1, len(cands), c.path)
            scan_candidate(c, per_limit, clock, budget_end)
            if i % 20 == 0 and i < len(cands):
                say(f"  크기 계산 {i}/{len(cands)}")
        else:
            _flag_name(c)
    if with_sizes:
        say(f"완료: 소요 {clock() - t0:.1f}초")

    def too_small(c: Candidate) -> bool:
        return (min_size_kb > 0 and c.size is not None and c.size < min_size_kb * 1024 and not c.secret)

    for c in cands:
        is_home = c.root == "%USERPROFILE%"
        if c.link is None:
            if is_home:
                if too_small(c):
                    d.hidden_small += 1
                else:
                    d.home.append(c)  # 점 파일/폴더는 연결이 없어도 보여 준다(.gitconfig 등)
            else:
                d.unlinked.append(c)
            continue
        names = c.link.programs
        c.rule_programs = [n for n in names if n in rule_names]
        live = [n for n in names if n not in rule_names and n not in bundle_names]
        if live:
            if too_small(c):
                d.hidden_small += 1
                continue
            if is_home:
                d.home.append(c)
            for n in live:
                if n in entries:
                    entries[n].candidates.append(c)
        elif c.rule_programs:
            d.rule_linked.append(c)
        else:  # 번들에만 연결
            d.unlinked.append(c)
    d.programs = list(entries.values())
    return d


def summarize(rules: list[Rule], programs: list[Program], expand: Expand = expand_env) -> tuple[int, int]:
    """(규칙 없는 설치 프로그램 수, 설정 폴더 후보 수). 크기 계산 없이 개수만(빠름)."""
    d = discover(rules, programs, expand, with_sizes=False)
    return d.program_count, d.candidate_count


# --------------------------------------------------------------------------- 출력

def fmt_size(c: Candidate) -> str:
    if c.size is None:
        return "크기 미확인"
    n = float(c.size)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return "크기 미확인"


def _meta(c: Candidate, with_sizes: bool) -> str:
    if not with_sizes:
        return ""
    when = datetime.fromtimestamp(c.mtime).strftime("%Y-%m-%d") if c.mtime else "-"
    files = f"{c.files}개" if c.files is not None else "-"
    return f" [{fmt_size(c)}, 파일 {files}, 수정 {when}]"


def _cand_line(c: Candidate, with_sizes: bool, note_rule: bool = False) -> str:
    kind = "폴더" if c.is_dir else "파일"
    line = f"{c.path} ({kind}){_meta(c, with_sizes)}"
    if c.link:
        line += f" — 근거 {c.link.grade}: {c.link.reason}"
    else:
        line += " — 프로그램 연결 없음"
    if note_rule and c.rule_programs:
        line += f" (규칙 있음: {', '.join(c.rule_programs)})"
    if c.secret:
        line += f" ⚠ 비밀 정보 가능성({', '.join(c.secret)}): 기본 제외"
    return line


def to_dict(d: Discovery, show_all: bool = False) -> dict:
    def cdict(c: Candidate) -> dict:
        x = asdict(c)
        x["secret_risk"] = bool(c.secret)
        return x
    out = {
        "programs_without_rule": [{"name": e.program.name, "version": e.program.version,
                                   "publisher": e.program.publisher,
                                   "candidates": [cdict(c) for c in e.candidates]} for e in d.programs],
        "home_dotfiles": [cdict(c) for c in d.home],
        "counts": {"programs": d.program_count, "candidates": d.candidate_count, "secret": d.secret_count,
                   "bundles_hidden": len(d.bundles), "unlinked": len(d.unlinked),
                   "rule_linked_hidden": len(d.rule_linked), "rule_covered": d.rule_covered,
                   "small_hidden": d.hidden_small},
    }
    if show_all:
        out["bundles"] = [{"name": p.name, "reason": w} for p, w in d.bundles]
        out["unlinked"] = [cdict(c) for c in d.unlinked]
        out["rule_linked"] = [cdict(c) for c in d.rule_linked]
    return out


def render(d: Discovery, fmt: str = "text", show_all: bool = False) -> str:
    if fmt == "json":
        return json.dumps(to_dict(d, show_all), ensure_ascii=False, indent=2) + "\n"
    md = fmt == "md"
    H = (lambda t: f"## {t}") if md else (lambda t: f"=== {t} ===")
    bullet = "- " if md else "  - "
    lines = ["# 규칙 없는 프로그램 탐지(discover)" if md else "규칙 없는 프로그램 탐지(discover)", ""]
    lines.append(f"규칙 없는 설치 프로그램 {d.program_count}개, 설정 폴더 후보 {d.candidate_count}개"
                 f" (비밀 정보 가능성 {d.secret_count}개)")
    lines.append("※ 읽기 전용 탐지입니다. 아무것도 자동으로 export 에 포함되지 않습니다. "
                 "포함하려면 --output-selection 으로 선택 파일을 만들고 include 를 true 로 바꾼 뒤 export --extra 로 지정하세요.")
    lines.append("")
    lines.append(H("규칙 없는 프로그램"))
    if not d.programs:
        lines.append("(없음)")
    for e in d.programs:
        p = e.program
        shown = p.display
        lines.append(f"{'- **' + p.name + '**' if md else '* ' + p.name}"
                     + shown[len(p.name):] + (f" — {p.publisher}" if p.publisher else ""))
        if not e.candidates:
            lines.append(f"{bullet}설정 폴더 후보 없음(자동으로 찾지 못함). 이 프로그램의 설정은 직접 확인하세요.")
        for c in e.candidates:
            lines.append(bullet + _cand_line(c, d.with_sizes))
    lines.append("")
    lines.append(H("홈 폴더의 점 폴더/파일"))
    if d.home:
        lines += [bullet + _cand_line(c, d.with_sizes) for c in d.home]
    else:
        lines.append("(없음)")
    lines.append("")
    if show_all:
        lines.append(H("숨김 항목(--all)"))
        lines.append(f"제조사 번들/시스템 구성요소 {len(d.bundles)}개:")
        lines += [f"{bullet}{p.name} — {w}" for p, w in d.bundles]
        lines.append(f"규칙이 있는 프로그램 쪽 추가 폴더 {len(d.rule_linked)}개(규칙이 일부만 백업함):")
        lines += [bullet + _cand_line(c, d.with_sizes, note_rule=True) for c in d.rule_linked]
        lines.append(f"프로그램과 연결하지 못한 폴더 {len(d.unlinked)}개:")
        lines += [bullet + _cand_line(c, d.with_sizes) for c in d.unlinked]
        lines.append("")
    else:
        lines.append(f"숨김: 제조사 번들/시스템 구성요소 {len(d.bundles)}개, 규칙이 있는 프로그램 쪽 폴더 {len(d.rule_linked)}개, "
                     f"프로그램과 연결 못 한 폴더 {len(d.unlinked)}개(--all 로 표시)"
                     + (f", 작은 폴더 {d.hidden_small}개(--min-size-kb)" if d.hidden_small else ""))
        lines.append("")
    if d.rule_covered:
        lines.append(f"규칙 경로와 겹쳐 제외한 폴더 {d.rule_covered}개(규칙이 처리함)")
    lines.append("⚠ '비밀 정보 가능성' 항목은 기본 제외입니다. 선택 파일에 넣어도 export 시 --allow-secrets 가 필요합니다.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- 선택 파일

def build_selection(d: Discovery, show_all: bool = False) -> dict:
    """후보 템플릿(모두 include:false). 사용자가 포함할 항목만 true 로 바꾼다."""
    cands = d.candidates()
    if show_all:
        seen = {c.key for c in cands}
        cands += [c for c in d.unlinked + d.rule_linked if c.key not in seen]
    return {
        "format": SELECTION_FORMAT,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "포함할 항목의 include 를 true 로 바꾸세요. secret 이 있는 항목은 export --allow-secrets 가 필요합니다. "
                "path 는 참고용이며 export 는 root + rel 만 사용합니다.",
        "candidates": [{
            "include": False, "root": c.root, "rel": c.rel, "path": c.path,
            "programs": c.link.programs if c.link else [], "grade": c.link.grade if c.link else NONE,
            "secret": c.secret, "requires_allow_secrets": bool(c.secret),
        } for c in cands],
    }


@dataclass
class ExtraItem:
    root: str
    rel: str
    path: Path  # 확장된 절대 경로
    is_dir: bool


def _safe_top_rel(rel: str) -> None:
    if (not rel or "\\" in rel or "/" in rel or ":" in rel or rel in (".", "..") or rel.strip() != rel):
        raise SelectionError(f"안전하지 않은 rel(루트 바로 아래 항목 이름 하나여야 함): {rel!r}")


def load_selection(path: Path, expand: Expand = expand_env) -> list[ExtraItem]:
    """선택 파일에서 include:true 항목만 검증해 반환한다. root 는 허용 목록, rel 은 최상위 이름 하나."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SelectionError(f"선택 파일을 읽을 수 없습니다: {e}") from e
    return items_from_selection(data, expand)


def items_from_selection(data: object, expand: Expand = expand_env) -> list[ExtraItem]:
    """선택 데이터(dict)에서 include:true 항목만 검증해 반환한다(GUI 도 같은 검증을 쓴다)."""
    if not isinstance(data, dict) or data.get("format") != SELECTION_FORMAT or not isinstance(
            data.get("candidates"), list):
        raise SelectionError("선택 파일 형식이 올바르지 않습니다")
    items: list[ExtraItem] = []
    seen: set[tuple[str, str]] = set()
    for i, c in enumerate(data["candidates"]):
        if not isinstance(c, dict):
            raise SelectionError(f"candidates[{i}] 가 객체가 아닙니다")
        if c.get("include") is not True:
            continue
        root, rel = c.get("root"), c.get("rel")
        if root not in EXTRA_ROOTS:
            raise SelectionError(f"허용되지 않은 root: {root!r}")
        if not isinstance(rel, str):
            raise SelectionError(f"candidates[{i}].rel 이 문자열이 아닙니다")
        _safe_top_rel(rel)
        base = Path(expand(root))
        if "%" in str(base) or not base.is_absolute():
            raise SelectionError(f"root 의 환경변수를 확장할 수 없습니다: {root}")
        target = base / rel
        if not _contained(target, base):
            raise SelectionError(f"루트 밖으로 나가는 경로: {rel!r}")
        if root == "%USERPROFILE%" and not rel.startswith("."):
            raise SelectionError(f"홈 루트는 점으로 시작하는 항목만 허용: {rel!r}")
        if not target.exists():
            raise SelectionError(f"존재하지 않는 항목: {target}")
        if target.is_symlink():
            raise SelectionError(f"심볼릭 링크는 포함할 수 없습니다: {target}")
        if (root, rel) in seen:
            continue
        seen.add((root, rel))
        items.append(ExtraItem(root, rel, target, target.is_dir()))
    return items


def _contained(target: Path, base: Path) -> bool:
    try:
        Path(os.path.realpath(target)).relative_to(os.path.realpath(base))
        return True
    except ValueError:
        return False


def secret_names(item: ExtraItem, per_limit: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> tuple[list[str], bool]:
    """선택 항목의 비밀 정보 가능성 이름과 검사 완료 여부(export 시 다시 검사; 선택 파일의 값은 믿지 않는다)."""
    c = Candidate(item.root, item.rel, str(item.path), item.is_dir)
    scan_candidate(c, per_limit, clock, clock() + per_limit)
    return c.secret, c.scan_complete
