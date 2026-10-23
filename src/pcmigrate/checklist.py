"""수동 작업 체크리스트: 규칙의 manual_warnings + 공통 '자동 복원 불가' 항목 + winget 수동 설치 목록.

이 모듈은 안내문만 만든다. winget import 등은 절대 자동 실행하지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .copier import _backup_path
from .detect import Program, parse_winget_export
from .rules import Rule

# CLAUDE.md '자동 복원 불가, 수동 작업 필요' 항목(감지 여부와 무관하게 항상 안내)
COMMON_WARNINGS: tuple[str, ...] = (
    "Autodesk 계열: 라이선스는 복원되지 않습니다. 신 PC에서 재로그인하고, 구/신 PC의 버전이 일치하는지 확인하세요. "
    "Revit 로컬 캐시는 복원하지 않으므로 중앙 모델에서 다시 동기화하세요.",
    "동글(하드웨어 락) 프로그램: 동글을 신 PC에 연결하고 드라이버 설치와 인식을 확인하세요.",
    "Rhino/V-Ray/Enscape/Lumion/ArchiCAD: 신 PC에서 재설치 후 라이선스를 재활성화하세요.",
    "GPU/드라이버 의존 설정: 신 PC에 그래픽 드라이버를 설치한 뒤 각 프로그램의 GPU/렌더링 설정을 직접 다시 확인하세요.",
    "브라우저 저장 비밀번호/쿠키: DPAPI 로 암호화되어 복사할 수 없습니다. 브라우저 계정 동기화를 쓰거나 "
    "비밀번호를 내보낸 뒤 신 PC에서 다시 로그인하세요.",
    "카카오톡 대화, 공인인증서, 금융보안 프로그램: 자동 이전되지 않습니다. 각 서비스의 이전/재설치 절차로 수동 처리하세요.",
    "OneDrive 파일 온디맨드: 클라우드 전용 파일은 복사에 실패하거나 대량 다운로드가 발생합니다. "
    "필요한 폴더를 '항상 이 장치에 유지'로 동기화한 뒤 복사하세요.",
    "Office, Adobe CC: 신 PC에서 재설치하고 계정으로 다시 로그인/라이선스 인증하세요.",
)

WINGET_IMPORT_GUIDE: tuple[str, ...] = (
    "신 PC에서 패키지(.zip) 안의 programs/winget.json 을 꺼낸 뒤 다음 명령을 직접 실행하세요:",
    "winget import -i winget.json --ignore-unavailable --accept-package-agreements --accept-source-agreements",
    "--ignore-unavailable 은 신 PC 의 winget 에서 찾을 수 없는 패키지를 건너뜁니다. "
    "이 도구는 winget import 를 자동 실행하지 않습니다.",
)

# 설치 목록에서 수동 설치 비교 대상이 아닌 항목(재배포 패키지, 업데이트 등)
_NOISE = re.compile(r"redistributable|\bupdate for\b|security update|hotfix|\bKB\d{6,}|"
                    r"microsoft edge webview2|windows (sdk|software development kit)", re.I)

# 매칭 근거 등급. '확실' 만 수동 설치 목록에서 제외하고, 나머지는 등급을 표시해 남긴다(과다 안내가 안전).
CERTAIN, PROBABLE, NONE = "확실", "추정", "없음"

_COMPANY_WORDS = re.compile(r"\b(inc|incorporated|corp|corporation|ltd|limited|llc|co|company|gmbh|ag|sa|plc)\b",
                            re.I)
_VERSION = re.compile(r"\bv?\d+(\.\d+)+\b|\b(x64|x86|64-?bit|32-?bit)\b", re.I)
_PARENS = re.compile(r"\([^)]*\)")


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


def find_manual_installs(registry: list[Program], winget: list[Program],
                         winget_list_text: str | None = None) -> list[tuple[Program, Match | None]]:
    """수동 설치 후보를 (프로그램, 매칭 근거)로 반환. '확실' 매칭만 제외한다.

    정렬: 근거 없음 -> 추정(약한 순) -> 이름순.
    """
    ids = [w.winget_id or w.name for w in winget]
    links = parse_winget_list(winget_list_text, ids)
    out: dict[str, tuple[Program, Match | None]] = {}
    for p in registry:
        if _NOISE.search(p.name) or p.name in out:
            continue
        m = match_program(p, ids, links)
        if m is not None and m.grade == CERTAIN:
            continue
        out[p.name] = (p, m)
    return sorted(out.values(), key=lambda t: (t[1] is not None, t[1].strength if t[1] else 0,
                                               t[0].name.lower()))


def describe_manual(p: Program, m: Match | None) -> str:
    head = p.name + (f" {p.version}" if p.version else "")
    if m is None:
        return f"{head} — 매칭 근거: {NONE}"
    return f"{head} — 매칭 근거: {m.grade}({m.reason}: {m.winget_id}) → 실제 설치 여부 확인"


@dataclass
class Checklist:
    program_items: list[str] = field(default_factory=list)
    common_items: list[str] = field(default_factory=list)
    manual_installs: list[str] = field(default_factory=list)
    winget_available: bool = True
    has_winget_list: bool = False

    @property
    def count(self) -> int:
        return len(self.program_items) + len(self.common_items) + len(self.manual_installs)

    @property
    def probable_count(self) -> int:
        return sum(1 for i in self.manual_installs if f"매칭 근거: {PROBABLE}" in i)


def build_checklist(rule_warnings: list[tuple[str, list[str]]], registry: list[Program] | None = None,
                    winget: list[Program] | None = None,
                    winget_list_text: str | None = None) -> Checklist:
    """rule_warnings: [(프로그램 이름, [경고...])]. 같은 문구는 하나로 합치고 출처를 병기한다."""
    cl = Checklist()
    common_norm = {_norm(t) for t in COMMON_WARNINGS}
    merged: dict[str, list[str]] = {}
    for name, items in rule_warnings:
        for text in items:
            if _norm(text) in common_norm:
                continue
            merged.setdefault(text, [])
            if name not in merged[text]:
                merged[text].append(name)
    for text, names in merged.items():
        cl.program_items.append(f"{text} ({', '.join(names)})" if names else text)
    cl.common_items = list(COMMON_WARNINGS)
    cl.winget_available = bool(winget)
    if registry is not None and winget:
        cl.has_winget_list = bool(winget_list_text)
        cl.manual_installs = [describe_manual(p, m)
                              for p, m in find_manual_installs(registry, winget, winget_list_text)]
    return cl


def render(cl: Checklist, fmt: str = "text") -> str:
    md = fmt == "md"
    h = (lambda t: f"## {t}") if md else (lambda t: f"=== {t} ===")
    lines = ["# PC 이전 수동 작업 체크리스트" if md else "PC 이전 수동 작업 체크리스트", ""]

    def section(title: str, items: list[str], empty: str, note: str = "") -> None:
        lines.append(h(title))
        if items:
            lines.extend(f"- [ ] {i}" for i in items)
        else:
            lines.append(empty)
        if note:
            lines.append(note)
        lines.append("")

    section("프로그램별 수동 작업", cl.program_items, "(감지된 프로그램의 추가 경고 없음)")
    section("공통 수동 작업(자동 복원 불가)", cl.common_items, "")
    if cl.winget_available:
        note = "※ 매칭 근거 '추정' 항목은 이미 winget 으로 설치될 수도 있습니다. 직접 판단하세요."
        if not cl.has_winget_list:
            note += " (winget list 결과가 없어 '확실' 판정을 하지 못했습니다.)"
        section("수동 설치 필요(winget 에서 '확실'하게 매칭되지 않은 프로그램)", cl.manual_installs,
                "(없음)", note)
    else:
        lines += [h("수동 설치 필요"),
                  "winget 목록이 없어 비교하지 못했습니다. 설치된 프로그램 목록을 직접 확인하세요.", ""]
    lines.append(h("신 PC 에서 winget 으로 설치"))
    lines += [("    " + g) if g.startswith("winget ") else g for g in WINGET_IMPORT_GUIDE]
    lines.append("")
    lines.append("※ 라이선스는 복원하지 않습니다. 모든 항목은 직접 확인하세요.")
    return "\n".join(lines) + "\n"


def write_output(path: Path, text: str) -> None:
    """기존 파일은 .bak 으로 보존한 뒤 원자적으로 쓴다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".pcmigrate-part")
    tmp.write_text(text, encoding="utf-8")
    if path.exists():
        os.replace(path, _backup_path(path))
    os.replace(tmp, path)


def rule_warning_pairs(rules: list[Rule]) -> list[tuple[str, list[str]]]:
    return [(r.name, list(r.manual_warnings)) for r in rules]


def manifest_warning_pairs(manifest: dict) -> list[tuple[str, list[str]]]:
    return [(w.get("name", ""), list(w.get("items", []))) for w in manifest.get("manual_warnings", [])]


def load_package_programs(path: Path) -> tuple[list[Program], list[Program], str | None]:
    """패키지 zip 의 registry.json / winget.json / winget-list.txt 를 읽는다(없으면 빈 값)."""
    registry: list[Program] = []
    winget: list[Program] = []
    list_text: str | None = None
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "programs/registry.json" in names:
            for d in json.loads(zf.read("programs/registry.json").decode("utf-8")):
                registry.append(Program(d.get("name", ""), d.get("version", ""), d.get("publisher", "")))
        if "programs/winget.json" in names:
            winget = parse_winget_export(zf.read("programs/winget.json").decode("utf-8-sig"))
        if "programs/winget-list.txt" in names:
            list_text = zf.read("programs/winget-list.txt").decode("utf-8", errors="replace")
    return registry, winget, list_text
