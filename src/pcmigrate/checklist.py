"""수동 작업 체크리스트: 규칙의 manual_warnings + 공통 '자동 복원 불가' 항목 + winget 설치 분류.

구성: 요약 / 1. 새 PC에서 먼저 설치 / 2. 로그인·인증 / 3. 데이터 복사·기타 / 참고(설치 불필요 추정).
이 모듈은 안내문만 만든다. winget import 등은 절대 자동 실행하지 않는다.
데이터 표(공통 경고, 번들 패턴, 대표 앱)는 checklist_data.py 에 있다.
"""
from __future__ import annotations

import errno
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .checklist_data import (common_warning_pairs, BUNDLE_PATTERNS, BUNDLE_REGEX, COMMON_ITEMS, KNOWN_APPS,
                             LOGIN_KEYWORDS, CommonItem)
from .copier import _backup_path
from .fs import long_path
from .detect import DEFAULT_LOCALE, Program, parse_winget_export, safe_install_command
from .rules import Rule

WINGET_IMPORT_GUIDE: tuple[str, ...] = (
    "신 PC에서 먼저 패키지(.zip) 안의 winget 목록을 꺼내세요(폴더는 아무 곳이나 됩니다. 예: 바탕 화면의 새 폴더):",
    "pcmigrate winget-extract 패키지.zip --output 폴더경로",
    "그런 다음 꺼낸 winget.json 으로 다음 명령을 직접 실행하세요:",
    "winget import -i 폴더경로\\winget.json --ignore-unavailable --accept-package-agreements --accept-source-agreements",
    "--ignore-unavailable 은 신 PC 의 winget 에서 찾을 수 없는 패키지(구 PC 의 그 버전이 저장소에 없는 경우 포함)를 건너뜁니다. "
    "이 도구는 winget import 를 자동 실행하지 않습니다. "
    "실행한 뒤에는 pcmigrate install-check 패키지.zip 으로 설치 결과를 다시 확인할 수 있습니다.",
    "구 PC 의 버전이 목록에 들어 있으면 import 는 기본으로 그 버전을 설치합니다. 저장소에서 그 버전을 찾지 못한 항목은 건너뛰거나 오류가 날 수 있으니(winget 동작) "
    "설치 확인에서 다시 확인하세요. 최신판을 받아도 괜찮다면 명령 끝에 --ignore-versions 를 붙여 다시 실행하세요. "
    "이전 버전 도구로 만든 묶음은 버전 정보가 없어 항상 최신판이 설치됩니다.",
    "※ winget import(일괄 설치)는 언어(--locale)를 지정할 수 없어 패키지의 기본 언어판이 설치될 수 있습니다. 한글판이 중요한 프로그램은 일괄 설치 대신 "
    "개별 설치 명령(winget install --id <id> --exact --locale ko-KR [--version 버전])을 쓰세요. --locale 은 그 패키지가 해당 언어를 제공할 때만 적용되며, "
    "제공하지 않으면 winget 이 기본값을 쓸 수 있고, 설치 프로그램이 자체 언어 선택 화면을 쓰면 바뀌지 않을 수 있습니다. 설치한 뒤 실제 언어를 직접 확인하세요.",
)

OLD_VERSION_GUIDE = (
    "같은 버전을 받아 설치하세요. 구 PC 의 제어판 > 프로그램 및 기능에서 확인한 값입니다. "
    "언어판은 구 PC 에서 쓰던 것과 같은 언어판을 고르세요.")
NO_VERSION_TEXT = "버전 정보 없음 — 구 PC 의 제어판에서 확인"


_CTRL = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]|[\x00-\x1f\x7f-\x9f]")


def clean_text(s: str) -> str:
    """패키지의 프로그램 목록(registry.json)에서 온 문자열의 ANSI 이스케이프/제어 문자를 지운다(터미널 화면 조작 방지, 78)."""
    return _CTRL.sub("", str(s))


def old_version_text(p: Program) -> str:
    """구 PC 에서 설치돼 있던 버전(레지스트리 DisplayVersion, 있으면 게시자). 없으면 '버전 정보 없음 — 구 PC 의 제어판에서 확인'."""
    if not p.version:
        return f"구 PC 버전: {NO_VERSION_TEXT}"
    return f"구 PC 버전: {clean_text(p.version)}" + (f"(게시자 {clean_text(p.publisher)})" if p.publisher else "")

# 매칭 근거 등급. '확실' 만 수동 설치 목록에서 제외하고, 나머지는 등급을 표시해 남긴다(과다 안내가 안전).
CERTAIN, PROBABLE, NONE = "확실", "추정", "없음"

_COMPANY_WORDS = re.compile(r"\b(inc|incorporated|corp|corporation|ltd|limited|llc|co|company|gmbh|ag|sa|plc)\b",
                            re.I)
_VERSION = re.compile(r"\bv?\d+(\.\d+)+\b|\b(x64|x86|64-?bit|32-?bit)\b", re.I)
_PARENS = re.compile(r"\([^)]*\)")
_BUNDLE_RE = [(re.compile(p, re.I), why) for p, why in BUNDLE_REGEX]


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def _norm_company(s: str) -> str:
    s = s.replace("(주)", " ").replace("㈜", " ").replace("주식회사", " ")
    return _norm(_COMPANY_WORDS.sub(" ", s))


def _norm_display(name: str) -> str:
    """표시 이름에서 괄호, 버전, 아키텍처 표기를 제거한 정규화 문자열."""
    return _norm(_VERSION.sub(" ", _PARENS.sub(" ", name)))


def winget_matches(program_name: str, winget_id: str) -> bool:
    """(약함) id 를 '.' 로 나눈 모든 조각이 이름에 포함되는지. 마지막 보조 수단."""
    name = _norm(program_name)
    parts = [_norm(p) for p in winget_id.split(".") if _norm(p)]
    return bool(parts) and all(p in name for p in parts)


@dataclass(frozen=True)
class Match:
    grade: str  # CERTAIN | PROBABLE
    winget_id: str
    reason: str
    strength: int = 0  # 같은 등급 안에서의 비교용


@dataclass(frozen=True)
class Link:
    name: str
    version: str
    truncated: bool


def parse_winget_list(text: str | None, known_ids: list[str]) -> dict[str, Link]:
    """`winget list` 원문에서 알려진 PackageIdentifier 토큰이 있는 줄을 찾아 (이름, 버전)을 연결한다.

    열 폭/한글 정렬에 의존하지 않는다: id 토큰 앞쪽 텍스트를 이름, 바로 뒤 토큰을 버전으로 본다.
    """
    links: dict[str, Link] = {}
    if not text or not known_ids:
        return links
    patterns = {i: re.compile(r"(?<!\S)" + re.escape(i) + r"(?!\S)", re.I) for i in known_ids}
    for line in text.splitlines():
        if not line.strip():
            continue
        for wid, pat in patterns.items():
            m = pat.search(line)
            if not m or wid in links:
                continue
            name = line[:m.start()].strip()
            rest = line[m.end():].split()
            truncated = name.endswith("…") or name.endswith("...")
            name = name.rstrip("….").strip()
            if name:
                links[wid] = Link(name, rest[0] if rest else "", truncated)
    return links


def _link_match(p: Program, link: Link) -> str | None:
    """설치 목록 항목과 winget list 이름 연결의 일치 사유('확실' 근거). 없으면 None."""
    mine, theirs = _norm(p.name), _norm(link.name)
    if not theirs:
        return None
    if mine == theirs:
        return "winget list 이름 일치"
    if link.truncated and len(theirs) >= 8 and mine.startswith(theirs):
        return "winget list 이름(잘림) 앞부분 일치"
    disp = _norm_display(p.name)
    if disp and disp == _norm_display(link.name) and p.version and link.version and p.version == link.version:
        return "winget list 이름+버전 일치"
    return None


def _publisher_product(p: Program, wid: str) -> bool:
    """'발행자.제품' 형태 id 와 정규화 비교(공백/기호/대소문자/Inc·Corp·Ltd 제거)."""
    parts = [x for x in wid.split(".") if x]
    if len(parts) < 2:
        return False
    pub, product = _norm(parts[0]), _norm("".join(parts[1:]))
    nd = _norm_display(p.name)
    if not product or not nd:
        return False
    if nd == product:
        return True
    pc = _norm_company(p.publisher)
    pub_ok = bool(pc) and bool(pub) and (pc == pub or (min(len(pc), len(pub)) >= 3
                                                       and (pc.startswith(pub) or pub.startswith(pc))))
    rest = nd[len(pub):] if pub and nd.startswith(pub) else None
    return pub_ok and rest == product


def match_program(p: Program, ids: list[str], links: dict[str, Link]) -> Match | None:
    """가장 강한 근거를 반환: 확실(winget list 연결) > 추정(발행자/제품 정규화) > 추정(조각, 약함)."""
    best: Match | None = None

    def rank(m: Match) -> tuple[bool, int]:
        return (m.grade == CERTAIN, m.strength)

    for wid in ids:
        cand: Match | None = None
        link = links.get(wid)
        why = _link_match(p, link) if link else None
        if why:
            cand = Match(CERTAIN, wid, why, 3)
        elif _publisher_product(p, wid):
            cand = Match(PROBABLE, wid, "발행자/제품명 정규화 일치", 2)
        elif winget_matches(p.name, wid):
            cand = Match(PROBABLE, wid, "이름 조각 포함(약함)", 1)
        if cand is not None and (best is None or rank(cand) > rank(best)):
            best = cand
    return best


# --------------------------------------------------------------------------- 설치 목록 분류

def bundle_reason(name: str) -> str | None:
    """제조사 번들/드라이버/시스템 구성요소/런타임이면 사유, 아니면 None(데이터 표 기준, 보수적)."""
    low = name.lower()
    for pat, why in BUNDLE_PATTERNS:
        if pat in low:
            return why
    for rx, why in _BUNDLE_RE:
        if rx.search(name):
            return why
    return None


def _norm_known(s: str) -> str:
    """대표 앱 표 비교용 정규화: '+' 는 유지('Notepad++' 와 'Notepad' 를 구분)."""
    return re.sub(r"[^0-9a-z가-힣+]", "", s.lower())


def known_app(name: str):
    """대표 앱 표(괄호/버전/아키텍처를 뺀 표시 이름의 정규화 일치)에서 찾는다. 없으면 None."""
    nd = _norm_known(_VERSION.sub(" ", _PARENS.sub(" ", name)))
    if not nd:
        return None
    for app in KNOWN_APPS:
        if nd in {_norm_known(n) for n in app.names}:
            return app
    return None


@dataclass
class Classified:
    manual: list[tuple[Program, Match | None]] = field(default_factory=list)  # 수동 설치 필요
    winget_auto: list[tuple[Program, str, str]] = field(default_factory=list)  # (프로그램, id, 근거)
    winget_cmd: list[tuple[Program, str]] = field(default_factory=list)  # 대표 앱 표 기준, 목록에 없어 개별 설치
    reference: list[tuple[Program, str]] = field(default_factory=list)  # 설치 불필요 추정


def classify_programs(registry: list[Program], winget: list[Program],
                      winget_list_text: str | None = None) -> Classified:
    """설치 목록을 수동 설치 / winget 자동 / winget 개별 설치 / 참고로 나눈다.

    순서: 번들(참고) -> 대표 앱 표(확실) -> winget list 연결(확실) -> 나머지는 수동 설치(근거 등급 표시).
    """
    ids = [w.winget_id or w.name for w in winget]
    id_set = {i.lower() for i in ids}
    links = parse_winget_list(winget_list_text, ids)
    out = Classified()
    seen: set[str] = set()
    manual: list[tuple[Program, Match | None]] = []
    for p in registry:
        if p.name in seen:
            continue
        seen.add(p.name)
        why = bundle_reason(p.name)
        if why:
            out.reference.append((p, why))
            continue
        app = known_app(p.name)
        if app is not None:
            if app.preinstalled:
                out.reference.append((p, "Windows 11 기본 포함(설치 불필요)"))
            elif app.winget_id.lower() in id_set:
                out.winget_auto.append((p, app.winget_id, "대표 앱 표 기준"))
            else:
                out.winget_cmd.append((p, app.winget_id))
            continue
        m = match_program(p, ids, links)
        if m is not None and m.grade == CERTAIN:
            out.winget_auto.append((p, m.winget_id, f"winget list: {m.reason}"))
            continue
        manual.append((p, m))
    out.manual = sorted(manual, key=lambda t: (t[1] is not None, t[1].strength if t[1] else 0,
                                               t[0].name.lower()))
    return out


def find_manual_installs(registry: list[Program], winget: list[Program],
                         winget_list_text: str | None = None) -> list[tuple[Program, Match | None]]:
    """수동 설치 후보를 (프로그램, 매칭 근거)로 반환(분류 결과의 manual)."""
    return classify_programs(registry, winget, winget_list_text).manual


def describe_manual(p: Program, m: Match | None) -> str:
    display = clean_text(p.display)
    if p.version and clean_text(p.version).lower() in display.lower():  # 이름 옆에 구 PC 버전이 이미 보이면 대괄호에 버전을 또 쓰지 않는다
        head = f"{display} [구 PC 게시자 {clean_text(p.publisher)}]" if p.publisher else display
    else:
        head = f"{display} [{old_version_text(p)}]"
    if m is None:
        return f"{head} — 매칭 근거: {NONE}"
    return f"{head} — 매칭 근거: {m.grade}({m.reason}: {m.winget_id}) → 실제 설치 여부 확인"


# --------------------------------------------------------------------------- 체크리스트 구성

def install_hint_for(name: str) -> str:
    """수동 설치 항목 옆에 붙이는 '무엇을/어떻게'(공통 항목의 install_hint). 없으면 빈 문자열(install_check 가 재사용)."""
    low = name.lower()
    for item in COMMON_ITEMS:
        if item.install_hint and any(pat in low for pat in item.patterns):
            return item.install_hint
    return ""


@dataclass
class Checklist:
    winget_auto: list[str] = field(default_factory=list)
    winget_cmd: list[str] = field(default_factory=list)
    install: list[str] = field(default_factory=list)
    login: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    reference: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)  # 이 PC 에 없는 프로그램 관련이라 생략된 공통 경고 제목
    winget_available: bool = True
    has_winget_list: bool = False
    show_all: bool = False
    # (규칙 없는 설치 프로그램 수, 설정 폴더 후보 수 또는 None=알 수 없음). None 이면 섹션 생략
    discovery: tuple[int, int | None] | None = None

    @property
    def count(self) -> int:
        """사용자가 직접 해야 하는 일의 수(설치 + 개별 winget + 인증 + 데이터/기타)."""
        return len(self.install) + len(self.winget_cmd) + len(self.login) + len(self.data)

    @property
    def probable_count(self) -> int:
        return sum(1 for i in self.install if f"매칭 근거: {PROBABLE}" in i)


def _relevant(item: CommonItem, names: list[str], rule_ids: set[str]) -> bool:
    if item.always:
        return True
    if rule_ids & set(item.rule_ids):
        return True
    return any(pat in n for n in names for pat in item.patterns)


def _phase_of(text: str) -> str:
    return "login" if any(k in text for k in LOGIN_KEYWORDS) else "data"


def _install_hint(name: str, shown: list[CommonItem]) -> str:
    low = name.lower()
    for item in shown:
        if item.install_hint and any(pat in low for pat in item.patterns):
            return f"{item.install_hint} → 2단계 '{item.title}' 참고"
    return ""


def build_checklist(rule_warnings: list[tuple[str, list[str]]], registry: list[Program] | None = None,
                    winget: list[Program] | None = None,
                    winget_list_text: str | None = None,
                    rule_ids: list[str] | tuple[str, ...] = (), show_all: bool = False,
                    discovery: tuple[int, int | None] | None = None,
                    locale: str = DEFAULT_LOCALE) -> Checklist:
    """rule_warnings: [(프로그램 이름, [경고...])]. 같은 문구는 하나로 합치고 출처를 병기한다.

    공통 경고는 설치 목록 이름/감지된 규칙에 관련된 것만(또는 항상 해당하는 것) 보여 주고,
    나머지는 생략 건수로 요약한다(show_all=True 이면 모두 표시).
    """
    cl = Checklist(show_all=show_all, discovery=discovery)
    names = [p.name.lower() for p in (registry or [])]
    rids = set(rule_ids)
    shown: list[CommonItem] = []
    for item in COMMON_ITEMS:
        if _relevant(item, names, rids):
            shown.append(item)
        elif show_all:
            shown.append(item)
        else:
            cl.omitted.append(item.title)
    detected = {it.id for it in COMMON_ITEMS if _relevant(it, names, rids)}

    # 프로그램별 규칙 경고(중복 문구 병합, 공통 항목이 이미 다루는 문구는 생략)
    covers = [c for it in shown for c in it.covers]
    merged: dict[str, list[str]] = {}
    for name, items in rule_warnings:
        for text in items:
            if any(c in text.lower() for c in covers):
                continue
            merged.setdefault(text, [])
            if name not in merged[text]:
                merged[text].append(name)
    for text, srcs in merged.items():
        line = f"{text} ({', '.join(srcs)})" if srcs else text
        (cl.login if _phase_of(text) == "login" else cl.data).append(line)

    for item in shown:
        line = item.text
        if item.id not in detected:
            line += " (이 PC에서 감지되지 않음)"
        (cl.login if item.phase == "login" else cl.data).append(line)

    cl.winget_available = bool(winget)
    if registry is not None:
        cl.has_winget_list = bool(winget_list_text)
        cls = classify_programs(registry, winget or [], winget_list_text)
        wver = {w.winget_id.lower(): w.version for w in (winget or []) if w.winget_id}
        for p, wid, why in cls.winget_auto:
            ver = wver.get(wid.lower()) or p.version
            cl.winget_auto.append(f"{p.name} — winget import 로 자동 설치({wid}, {why}; "
                                  + (f"구 PC 버전 {ver}" if ver else NO_VERSION_TEXT) + ")")
        for p, wid in cls.winget_cmd:  # 이 항목은 묶음 winget 목록에 없어 winget 이 기록한 버전이 없다(레지스트리 표기는 winget 버전과 다를 수 있어 --version 은 붙이지 않는다)
            cmd = safe_install_command(wid, '', locale)
            if cmd is None:  # 한 항목의 id/로캘 문제로 체크리스트 전체를 멈추지 않는다
                cl.winget_cmd.append(f"{p.name} — winget id({wid!r}) 또는 로캘 형식이 올바르지 않아 설치 명령을 만들지 못했습니다(수동으로 설치하세요)")
                continue
            cl.winget_cmd.append(f"{p.name} — 내보낸 winget 목록에 없음. 신 PC에서 "
                                 f"`{cmd}` 로 설치(대표 앱 표 기준, 한글판 등 언어는 --locale 로 지정, "
                                 f"{old_version_text(p)} — 같은 버전이 필요하면 `winget show --id {wid} --versions` 로 있는지 확인 후 --version 추가)")
        for p, why in cls.reference:
            cl.reference.append(f"{p.name} — {why}")
        installs = []
        for p, m in cls.manual:
            text = describe_manual(p, m)
            hint = _install_hint(p.name, shown)
            installs.append((0 if hint else 1, text + (f" — {hint}" if hint else "")))
        # 설치 미디어/키가 필요한(공통 항목과 연결된) 항목을 먼저
        cl.install = [t for _, t in sorted(installs, key=lambda x: x[0])]
    return cl


# --------------------------------------------------------------------------- 출력

def _summary_lines(cl: Checklist) -> list[str]:
    inst = len(cl.install) + len(cl.winget_cmd)
    return [
        f"- 1. 새 PC에서 먼저 설치: 수동 설치 {len(cl.install)}건, winget 개별 설치 {len(cl.winget_cmd)}건 "
        f"(winget import 로 자동 설치 {len(cl.winget_auto)}건 별도)" if inst or cl.winget_auto
        else "- 1. 새 PC에서 먼저 설치: 0건",
        f"- 2. 로그인·인증: {len(cl.login)}건",
        f"- 3. 데이터 복사/기타: {len(cl.data)}건",
        f"- 참고(설치 불필요 추정): {len(cl.reference)}건",
    ]


def render(cl: Checklist, fmt: str = "text") -> str:
    md = fmt == "md"
    h1 = (lambda t: f"## {t}") if md else (lambda t: f"=== {t} ===")
    lines = ["# PC 이전 체크리스트" if md else "PC 이전 체크리스트", ""]
    lines.append(h1("요약"))
    lines += _summary_lines(cl)
    if cl.omitted:
        lines.append(f"- 이 PC 에 없는 프로그램 관련 경고 {len(cl.omitted)}건 생략(--all 로 모두 표시)")
    lines.append("")

    def checks(items: list[str]) -> None:
        lines.extend(f"- [ ] {i}" for i in items)

    lines.append(h1("1. 새 PC에서 먼저 설치"))
    lines += [("    " + g) if g.startswith(("winget ", "pcmigrate ")) else g for g in WINGET_IMPORT_GUIDE]
    if cl.winget_auto:
        lines.append(f"→ winget import 로 자동 설치되는 앱 {len(cl.winget_auto)}건"
                     + ("" if cl.show_all else " (목록은 --all)"))
        if cl.show_all:
            lines.extend(f"  · {i}" for i in cl.winget_auto)
    if cl.winget_cmd:
        lines.append("개별 winget 설치:")
        checks(cl.winget_cmd)
    lines.append("수동 설치(winget 에서 '확실'하게 확인되지 않은 프로그램):")
    if cl.install:
        checks(cl.install)
        note = "※ 매칭 근거 '추정' 항목은 이미 winget 으로 설치될 수도 있습니다. 직접 판단하세요."
        if not cl.has_winget_list:
            note += " (winget list 결과가 없어 '확실' 판정을 하지 못했습니다.)"
        lines.append(note)
        lines.append("※ " + OLD_VERSION_GUIDE)
    else:
        lines.append("(없음)")
    if not cl.winget_available:
        lines.append("※ winget 목록이 없어 winget 매칭은 대표 앱 표 기준으로만 했습니다. 설치된 프로그램을 직접 확인하세요.")
    lines.append("")

    lines.append(h1("2. 로그인·인증"))
    if cl.login:
        checks(cl.login)
    else:
        lines.append("(없음)")
    lines.append("")

    lines.append(h1("3. 데이터 복사/기타"))
    if cl.data:
        checks(cl.data)
    else:
        lines.append("(없음)")
    lines.append("")

    if cl.reference:
        if md:
            lines += [f"<details><summary>참고 — 설치 불필요 추정 {len(cl.reference)}건 (펼치기)</summary>", ""]
            lines += [f"- {i}" for i in cl.reference]
            lines += ["", "</details>"]
        else:
            lines.append(f"참고 — 설치 불필요 추정 {len(cl.reference)}건"
                         + ("" if cl.show_all else " (제조사 번들/드라이버/런타임 등, 목록은 --all)"))
            if cl.show_all:
                lines += [f"  · {i}" for i in cl.reference]
        lines.append("")
    if cl.omitted and cl.show_all is False:
        lines.append(f"이 PC 에 없는 프로그램 관련 경고 {len(cl.omitted)}건 생략됨: " + ", ".join(cl.omitted)
                     + " (--all 로 모두 표시)")
        lines.append("")
    if cl.discovery is not None:
        n, m = cl.discovery
        lines.append(h1("규칙 없는 프로그램(요약)"))
        extra = f", 설정 폴더 후보 {m}개" if m is not None else " (폴더 후보는 구 PC에서 discover 로 확인)"
        lines.append(f"규칙 없는 설치 프로그램 {n}개{extra} — pcmigrate discover 로 확인하세요.")
        lines.append("규칙이 없는 프로그램의 설정은 자동으로 백업/복원되지 않으며 export 에 자동 포함되지도 않습니다. "
                     "필요한 것만 discover --output-selection 으로 골라 export --extra 로 포함하세요.")
        lines.append("")
    lines.append("※ 라이선스는 복원하지 않습니다. 모든 항목은 직접 확인하세요.")
    return "\n".join(lines) + "\n"


def write_output(path: Path, text: str) -> None:
    """기존 파일은 .bak 으로 보존한 뒤 원자적으로 쓴다."""
    path = Path(path)
    if path.is_dir():  # 폴더를 .bak 으로 옮기고 같은 이름의 파일을 만들게 되므로 쓰지 않는다
        raise IsADirectoryError(errno.EISDIR, "대상이 폴더입니다", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".pcmigrate-part")
    try:
        tmp.write_text(text, encoding="utf-8")
        if path.exists():
            os.replace(long_path(path), long_path(_backup_path(path)))
        os.replace(long_path(tmp), long_path(path))
    except BaseException:  # 쓰기 실패/중단 시 반쪽 임시 파일을 남기지 않는다
        tmp.unlink(missing_ok=True)
        raise


def rule_warning_pairs(rules: list[Rule]) -> list[tuple[str, list[str]]]:
    return [(r.name, list(r.manual_warnings)) for r in rules]


def manifest_warning_pairs(manifest: dict) -> list[tuple[str, list[str]]]:
    return [(w.get("name", ""), list(w.get("items", []))) for w in manifest.get("manual_warnings", [])] + common_warning_pairs(manifest)


class PackageProgramsError(ValueError):
    """패키지의 프로그램 목록(programs/*)이 손상/변조되어 읽을 수 없음(M6). 메시지는 사용자에게 그대로 보여도 된다."""


def _str_field(d: dict, key: str) -> str:
    v = d.get(key, "")
    return v if isinstance(v, str) else ("" if v is None else str(v))


def load_package_programs(path: Path) -> tuple[list[Program], list[Program], str | None]:
    """패키지 zip 의 registry.json / winget.json / winget-list.txt 를 읽는다(없으면 빈 값).

    모양이 올바르지 않으면 PackageProgramsError. 항목이 객체가 아니거나 이름이 비어 있으면 건너뛰고, 이름/버전/발행자가 문자열이 아니면 문자열로 바꾼다."""
    registry: list[Program] = []
    winget: list[Program] = []
    list_text: str | None = None
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "programs/registry.json" in names:
                data = json.loads(zf.read("programs/registry.json").decode("utf-8"))
                if not isinstance(data, list):
                    raise PackageProgramsError("패키지의 프로그램 목록(registry.json)이 올바른 형식이 아닙니다. 구 PC 에서 설정 묶음 파일을 다시 만들어 주세요.")
                for d in data:
                    if not isinstance(d, dict):
                        continue
                    name = _str_field(d, "name")
                    if name:
                        registry.append(Program(name, _str_field(d, "version"), _str_field(d, "publisher")))
            if "programs/winget.json" in names:
                winget = parse_winget_export(zf.read("programs/winget.json").decode("utf-8-sig"))
            if "programs/winget-list.txt" in names:
                list_text = zf.read("programs/winget-list.txt").decode("utf-8", errors="replace")
    except PackageProgramsError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, zipfile.BadZipFile, OSError) as e:
        raise PackageProgramsError(f"패키지의 프로그램 목록을 읽을 수 없습니다({type(e).__name__}). 구 PC 에서 설정 묶음 파일을 다시 만들어 주세요.") from e
    return registry, winget, list_text
