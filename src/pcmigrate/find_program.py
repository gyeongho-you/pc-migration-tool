"""'이 프로그램 설정 찾기': 규칙이 없는 프로그램 하나의 설정 위치만 국소적으로 찾는다. 읽기 전용, 자동 선택 없음.

전역 discover 의 판정(link_candidate / _prep / _folder_key / _list_root / scan_candidate)을 그대로 재사용한다.
여기서 더하는 것은 (1) 프로그램 이름·게시자·설치 경로에서 뽑은 '토큰'과 폴더 이름의 일치, (2) 일치한 토큰을 근거로 보여 주기,
(3) 설치 폴더 안의 사용자 설정 파일 후보(참고용: 설치 폴더 쓰기는 하지 않으므로 자동으로 옮기지 않는다), (4) 시간 상한과 취소.
이 버튼은 전역 discover 와 달리 캐시/로그/임시/라이선스 이름의 폴더도 찾는다(기본 탐색이 놓친 곳에 설정이 있을 수 있다는 가정).
각 후보에 분류 배지('임시 저장물일 수 있음'/'로그'/'라이선스')와 근거를 붙이고, 자동 선택은 하지 않는다. 캐시/로그 이름 후보를 사용자가
직접 고르면 그 후보에는 임시 저장물 자동 제외를 적용하지 않는다(Candidate.include_caches). 라이선스 후보는 발견만 표시하고 선택할 수 없다
(이 도구는 라이선스를 옮기지 않는다). 결과 후보는 사용자가 체크해야 기존 선택 흐름(secrets_ack, 민감 항목 가드)에 들어간다.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .cancel import Cancelled
from .detect import Program
from .discover import (CERTAIN, PROBABLE, Candidate, Discovery, Expand, Link, ProgramEntry, _covered, _folder_key, _list_root,
                       _skip_name, link_candidate, rule_concrete_paths, scan_candidate)
from .discover_data import (FIND_SKIP_WORDS, ROOTS, SETTINGS_EXT_PREFIXES, SETTINGS_EXTENSIONS, TOKEN_STOPWORDS,
                            is_regenerable)
from .envpaths import expand_env
from .progress import ProgressInfo
from .rules import Rule

MAX_INSTALL_FILES = 30
INSTALL_FILE_MAX_BYTES = 5 * 1024 * 1024
_WORDS = re.compile(r"[0-9a-z가-힣]+")
_VERSIONISH = re.compile(r"^(v?\d[\d.]*|x64|x86|64bit|32bit)$")


@dataclass
class ProgramFind:
    program: Program
    tokens: list[tuple[str, str]] = field(default_factory=list)  # (토큰, 출처: 이름/게시자/설치 경로)
    candidates: list[Candidate] = field(default_factory=list)
    install_files: list[dict] = field(default_factory=list)  # 참고용: 설치 폴더의 사용자 설정 파일 후보(자동으로 옮기지 않음)
    install_more: int = 0
    nested: list[dict] = field(default_factory=list)  # 찾은 폴더 안쪽 캐시/로그 폴더에 든 설정 형식 파일(참고용)
    nested_more: int = 0
    budget_hit: bool = False  # 시간 상한에 걸려 일부 크기를 못 쟀다
    has_rule: bool = False  # 이 프로그램은 이미 규칙이 있다(찾을 필요 없음)

    def as_discovery(self) -> Discovery:
        """build_selection/render 같은 기존 도구를 재사용하기 위한 변환."""
        return Discovery(programs=[ProgramEntry(self.program, list(self.candidates))], with_sizes=True)


def _words(text: str) -> list[str]:
    return [w for w in _WORDS.findall(text.lower()) if not _VERSIONISH.match(w)]


def program_tokens(p: Program) -> list[tuple[str, str]]:
    """프로그램 이름/게시자/설치 경로의 마지막 폴더 이름에서 뽑은 토큰(4자 이상, 흔한 낱말 제외, 중복 제거)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(tok: str, src: str) -> None:
        if len(tok) >= 4 and tok not in TOKEN_STOPWORDS and tok not in seen:
            seen.add(tok)
            out.append((tok, src))
    name_words = _words(re.sub(r"\([^)]*\)", " ", p.name))
    for w in name_words:
        add(w, "이름")
    if len(name_words) > 1:
        add("".join(name_words), "이름")  # 붙여 쓴 폴더 이름(예: 'AcmePaint')
    pub_words = _words(p.publisher) if p.publisher else []
    for w in pub_words:
        add(w, "게시자")
    if p.install_location:
        leaf = os.path.basename(os.path.normpath(p.install_location))
        for w in _words(leaf):
            add(w, "설치 경로")
    return out


BADGE_CACHE, BADGE_LOG, BADGE_LICENSE = "임시 저장물일 수 있음", "로그", "라이선스"
_LICENSE_WORDS = ("licenses", "license", "licences", "licence", "licensing", "eula")
_LOG_WORDS = ("logs", "log")
_CACHE_WORDS = ("caches", "cache", "crashes", "crash", "telemetry", "temp", "tmp")


def _skip_find_name(name: str) -> bool:
    words = set(re.split(r"[^0-9a-z]+", name.lower()))
    return any(w in words for w in FIND_SKIP_WORDS)


def classify_name(name: str) -> tuple[str, str]:
    """(배지, 분류 낱말을 뺀 나머지 이름). 이름 끝의 Cache/Logs/Licenses 같은 낱말을 떼어 낸다('AcmePaint Cache' -> 'acmepaint').
    나머지가 4자 미만이면 프로그램과 이름으로 이을 수 없으므로 빈 문자열."""
    norm = re.sub(r"[^0-9a-z가-힣]", "", name.lower().lstrip("."))
    for words, badge in ((_LICENSE_WORDS, BADGE_LICENSE), (_LOG_WORDS, BADGE_LOG), (_CACHE_WORDS, BADGE_CACHE)):
        for w in words:
            if norm.endswith(w):
                rest = norm[: -len(w)]
                return badge, (rest if len(rest) >= 4 else "")
    return "", norm


def _match(p: Program, name: str, path: str, is_home: bool, tokens: list[tuple[str, str]]) -> Link | None:
    link = link_candidate(name, path, [p], is_home)  # 전역 discover 와 같은 판정
    fk = _folder_key(name)
    hit = [(t, s) for t, s in tokens if fk and t == fk]
    if link is None and not hit:
        return None
    if link is None:
        t, s = hit[0]
        if s == "게시자":  # 제조사 이름만 일치: 같은 제조사의 다른 프로그램 설정도 섞여 있을 수 있다
            return Link([p.name], PROBABLE, f"프로그램 게시자에서 뽑은 토큰 '{t}' 와 폴더 이름이 일치 — 근거 약함: 제조사 폴더라 다른 프로그램의 설정도 섞여 있을 수 있음", True)
        return Link([p.name], PROBABLE, f"프로그램 {s}에서 뽑은 토큰 '{t}' 와 폴더 이름이 일치")
    reason = link.reason
    weak = link.weak
    if link.reason == "발행자 이름과 일치":
        weak = True  # 제조사 폴더는 같은 제조사의 다른 프로그램 설정도 함께 들어 있을 수 있다
        reason += " — 근거 약함: 제조사 폴더라 다른 프로그램의 설정도 섞여 있을 수 있음"
    if hit:
        reason += f" — 일치한 토큰: " + ", ".join(f"'{t}'({s})" for t, s in hit)
    return Link(link.programs, link.grade, reason, weak)


def _install_files(p: Program, deadline: float, clock: Callable[[], float]) -> tuple[list[dict], int]:
    """설치 폴더(와 바로 아래 폴더)의 사용자 설정일 수 있는 파일(확장자 허용 목록). 참고용 목록이며 상한 개수까지만."""
    root = p.install_location
    if not root or not os.path.isdir(root):
        return [], 0
    found: list[dict] = []
    more = 0
    stack = [(root, 0)]
    while stack:
        cur, depth = stack.pop(0)
        if clock() > deadline:
            break
        try:
            with os.scandir(cur) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if depth < 1 and not _skip_name(e.name) and not _skip_find_name(e.name):
                        stack.append((e.path, depth + 1))
                    continue
                ext = os.path.splitext(e.name)[1].lower()
                if not (ext in SETTINGS_EXTENSIONS or ext.startswith(SETTINGS_EXT_PREFIXES)) or _skip_find_name(e.name):
                    continue
                size = e.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            if size > INSTALL_FILE_MAX_BYTES:
                continue
            if len(found) < MAX_INSTALL_FILES:
                found.append({"path": e.path, "size": size, "why": f"설치 폴더의 설정 형식 파일({ext})"})
            else:
                more += 1
    return found, more


def find_program(program: Program, rules: list[Rule], expand: Expand = expand_env, *, per_limit: float = 3.0,
                 total_limit: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 cancel: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
                 progress_info: Callable[[ProgressInfo], None] | None = None) -> ProgramFind:
    """program 하나와 관련된 위치만 찾는다. 취소하면 Cancelled. total_limit 초가 지나면 남은 크기 계산은 건너뛴다."""
    def say(msg: str, phase: str = "find", done: int = 0, total: int = 0, current: str = "") -> None:
        if progress is not None:
            progress(msg)
        if progress_info is not None:
            progress_info(ProgressInfo(phase, 0, 0, done, total, message=msg, current=current))

    def check() -> None:
        if cancel is not None and cancel():
            raise Cancelled()

    # 캐시/로그/라이선스 이름도 찾는다(배지로 구분): 이 버튼은 기본 탐색이 놓친 곳을 보기 위한 것이다
    res = ProgramFind(program, program_tokens(program), has_rule=any(r.matches_name(program.name) for r in rules))
    if res.has_rule:
        return res
    start = clock()
    budget_end = start + total_limit
    say(f"'{program.name}' 와 관련된 폴더 이름을 찾는 중입니다…")
    concrete = rule_concrete_paths(rules, expand)
    cands: list[Candidate] = []
    for root in ROOTS:
        check()
        for r, base, e in _list_root(root, expand, skip_noise=False):
            path = os.path.join(str(base), e.name)
            if _covered(path, concrete):
                continue
            badge, rest = classify_name(e.name)
            match_name = rest if badge else e.name
            if badge and not rest:
                continue
            link = _match(program, match_name, path, r.dotfiles_only, res.tokens)
            if link is None:
                continue
            c = Candidate(r.template, e.name, path, e.is_dir(follow_symlinks=False))
            if badge:
                c.badge = badge
                if badge == BADGE_LICENSE:
                    c.license_only = True
                    note = "발견됨 — 이 도구는 라이선스를 옮기지 않으므로 선택할 수 없습니다(새 PC 에서 재활성화)"
                else:
                    c.include_caches = True  # 사용자가 직접 고르면 그대로 담는다(자동 제외 없음)
                    note = ("임시 저장물일 수 있음 — 다시 만들어지는 폴더일 수 있으나 설정이 들어 있을 가능성을 보고 찾음" if badge == BADGE_CACHE
                            else "로그 폴더 — 설정이 아닌 기록일 가능성이 큼(직접 확인 후 선택)")
                link = Link(link.programs, link.grade, f"[{badge}] {link.reason} — {note}", True if badge != BADGE_LICENSE else link.weak)
            c.link = link
            cands.append(c)
    for i, c in enumerate(cands, 1):
        check()
        say(f"찾은 폴더의 크기를 재는 중입니다({i}/{len(cands)})", "scan", i - 1, len(cands), c.path)
        scan_candidate(c, per_limit, clock, budget_end)
        if not c.scan_complete and c.is_dir:
            res.budget_hit = True
    for c in cands:  # 허용 방식(설정 파일만)으로 내보내면 담길 파일 수/용량(후보 표용, 시간 상한 안에서)
        check()
        if not c.license_only:
            fill_config_estimate(c, expand, min(budget_end, clock() + per_limit), clock)
            if not c.config_complete:
                res.budget_hit = True
    cands.sort(key=lambda c: (0 if c.link and c.link.grade == CERTAIN else 1, 1 if c.link and c.link.weak else 0, c.path.lower()))
    res.candidates = cands
    check()
    say("설치 폴더의 설정 형식 파일을 찾는 중입니다…", "find")
    res.install_files, res.install_more = _install_files(program, min(budget_end, clock() + per_limit), clock)
    res.nested, res.nested_more = _nested_cache_settings([c for c in cands if c.is_dir and not c.badge],
                                                         min(budget_end, clock() + per_limit), clock)
    return res


def fill_config_estimate(c: Candidate, expand: Expand, deadline: float, clock: Callable[[], float] = time.monotonic) -> None:
    """후보에 허용 방식 기준 '담길 파일 수/용량'을 채운다(package.estimate_extra_config 와 같은 기준, 읽기 전용)."""
    from .package import estimate_extra_config
    est = estimate_extra_config(c.root, c.rel, c.path, expand, include_caches=c.include_caches, deadline=deadline, clock=clock)
    c.config_files, c.config_bytes = est.files, est.bytes
    c.config_excluded_files, c.config_excluded_bytes = est.excluded_files, est.excluded_bytes
    c.config_limit_files, c.config_ext_counts, c.config_complete = est.limit_files, dict(est.ext_counts), est.complete


def config_line(c: Candidate) -> str:
    """후보 표/CLI 의 한 줄: 허용 방식으로 담길 파일 수/용량."""
    if c.config_files is None:
        return ""
    from .progress import fmt_bytes
    more = "" if c.config_complete else " 이상(시간 상한)"
    text = f"허용 방식으로 담길 파일 {c.config_files}개{more}, {fmt_bytes(c.config_bytes or 0)}"
    if c.config_excluded_files:
        text += f" / 설정 형식이 아니라 빠질 파일 {c.config_excluded_files}개({fmt_bytes(c.config_excluded_bytes or 0)})"
    if c.config_limit_files:
        text += f" / 상한 초과로 빠질 파일 {c.config_limit_files}개"
    return text


def _nested_cache_settings(dirs: list[Candidate], deadline: float, clock: Callable[[], float]) -> tuple[list[dict], int]:
    """찾은 폴더 안쪽의 캐시/로그 이름 폴더에 든 설정 형식 파일(참고용). 내보낼 때 기본으로 빠지는 곳이라 따로 알려 준다."""
    found: list[dict] = []
    more = 0
    for c in dirs:
        stack = [(c.path, (), False)]
        seen = 0
        while stack and clock() <= deadline and seen < 5000:
            cur, parts, in_c = stack.pop()
            try:
                with os.scandir(cur) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                seen += 1
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        sub = parts + (e.name,)
                        flag = in_c or is_regenerable(sub) or bool(classify_name(e.name)[0] and len(sub) <= 4)
                        if len(sub) <= 4:
                            stack.append((e.path, sub, flag))
                        continue
                    ext = os.path.splitext(e.name)[1].lower()
                    if in_c and (ext in SETTINGS_EXTENSIONS or ext.startswith(SETTINGS_EXT_PREFIXES)) and classify_name(e.name)[0] != BADGE_LICENSE:
                        if len(found) < MAX_INSTALL_FILES:
                            found.append({"path": e.path, "size": e.stat(follow_symlinks=False).st_size,
                                          "why": "캐시/로그 이름 폴더 안의 설정 형식 파일(기본 내보내기에서는 빠짐)"})
                        else:
                            more += 1
                except OSError:
                    continue
    return found, more


# --------------------------------------------------------------------------- 출력

def render(f: ProgramFind, fmt: str = "text") -> str:
    import json
    from .discover import _cand_line
    if fmt == "json":
        from dataclasses import asdict
        d = {"program": {"name": f.program.name, "version": f.program.version, "publisher": f.program.publisher},
             "has_rule": f.has_rule, "tokens": [{"token": t, "source": s} for t, s in f.tokens], "budget_hit": f.budget_hit,
             "candidates": [dict(asdict(c), secret_risk=bool(c.secret)) for c in f.candidates],
             "install_files": f.install_files, "install_files_more": f.install_more}
        return json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    lines = [f"프로그램 설정 찾기: {f.program.display}" + (f" — {f.program.publisher}" if f.program.publisher else "")]
    if f.has_rule:
        lines.append("이 프로그램은 이미 규칙이 있어 설정을 자동으로 옮깁니다(찾을 필요가 없습니다). pcmigrate scan 으로 확인하세요.")
        return "\n".join(lines) + "\n"
    lines.append("※ 읽기 전용 탐지입니다. 아무것도 자동으로 선택/포함되지 않습니다. 포함하려면 --output-selection 으로 선택 파일을 만들고 "
                 "include 를 true 로 바꾼 뒤 export --extra 로 지정하세요.")
    lines.append("검색에 쓴 토큰: " + (", ".join(f"{t}({s})" for t, s in f.tokens) or "없음(이름이 너무 짧거나 흔한 낱말뿐)"))
    lines.append("")
    lines.append(f"=== 설정 폴더 후보 {len(f.candidates)}개 ===")
    if not f.candidates:
        lines.append("(없음) 이 프로그램의 설정은 AppData/Documents/ProgramData/홈의 점 폴더에서 이름으로 찾지 못했습니다. "
                     "프로그램 안의 '설정 내보내기' 기능이나 설치 폴더를 직접 확인하세요.")
    for c in f.candidates:
        lines.append("  - " + _cand_line(c, True))
        cl = config_line(c)
        if cl:
            lines.append("      · " + cl)
    if f.budget_hit:
        lines.append("※ 시간 상한에 걸려 일부 크기는 '이상' 또는 '크기 미확인'으로 표시됩니다.")
    lines.append("※ 캐시/로그/임시 폴더도 포함해 찾았습니다. 배지([임시 저장물일 수 있음]/[로그])가 붙은 후보는 직접 확인하고 고르세요(기본은 선택 안 함). "
                 "라이선스 폴더는 발견만 표시하며 이 도구는 라이선스를 옮기지 않습니다.")
    if f.nested:
        lines.append(f"=== 찾은 폴더 안쪽 캐시/로그 폴더의 설정 형식 파일(참고용) {len(f.nested)}개" + (f" 외 {f.nested_more}개" if f.nested_more else "") + " ===")
        lines.append("이 파일들은 폴더 단위 후보 안에 있지만 내보낼 때 임시 저장물로 기본 제외됩니다(export --include-caches 로 포함 가능).")
        lines += [f"  - {x['path']} ({x['size']}B)" for x in f.nested]
    lines.append("")
    lines.append(f"=== 설치 폴더의 설정 형식 파일(참고용) {len(f.install_files)}개" + (f" 외 {f.install_more}개" if f.install_more else "") + " ===")
    if f.install_files:
        lines.append("설치 폴더(Program Files 등) 안의 파일은 자동으로 옮기지 않습니다. 사용자가 바꾼 설정인지 확인한 뒤 직접 복사하세요.")
        lines += [f"  - {x['path']} ({x['size']}B)" for x in f.install_files]
    else:
        lines.append("(없음)")
    lines.append("⚠ '비밀 정보 가능성' 항목은 기본 제외입니다. 선택 파일에 넣어도 export 시 --allow-secrets 가 필요합니다.")
    return "\n".join(lines) + "\n"
