"""새 PC 프로그램 설치 확인: 설정 묶음 파일의 구 PC 설치 목록과 새 PC 의 현재 설치 상태를 비교해 항목별로 판정한다.

읽기 전용이다. winget import / winget install 은 절대 실행하지 않는다(사용자가 직접 실행하고 '다시 확인'으로 재판정).
분류 규칙은 checklist.classify_programs / match_program / bundle_reason 을 그대로 재사용하고, 새 PC 쪽 존재 여부 판정만 더한다.

판정(상태):
- installed   설치됨: 새 PC 에서 winget id 가 같거나, 이름이 같거나, 대표 앱 표/winget list 이름 연결이 같은 앱('확실').
              구 PC 와 새 PC 버전이 다르면 version_diff=True 로 '버전 다름(구 x → 새 y)' 을 함께 표시한다.
- winget      설치 안 됨 - winget 으로 설치 가능: 묶음의 winget 목록에 있거나 대표 앱 표에 id 가 있다(새 PC 의 winget 에 실제로 있는지는 import 가 판단).
- manual      설치 안 됨 - 수동 설치 필요: winget 으로 확실하게 연결되지 않는다.
- ambiguous   애매함: 이름만 비슷한 추정 일치(근거 등급 '추정' 과 사유 표시). 설치돼 있지 않으면 would_be 방식으로 설치해야 한다.
- not_needed  설치 불필요: 제조사 번들/드라이버/런타임/Windows 기본 포함.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from .checklist import (CERTAIN, PROBABLE, _norm_display, bundle_reason, classify_programs,
                        install_hint_for, known_app, match_program, parse_winget_list)
from .detect import Program, parse_winget_export

INSTALLED, WINGET, MANUAL, AMBIGUOUS, NOT_NEEDED = "installed", "winget", "manual", "ambiguous", "not_needed"
STATUSES = (INSTALLED, WINGET, MANUAL, AMBIGUOUS, NOT_NEEDED)
STATUS_LABELS = {INSTALLED: "설치됨", WINGET: "winget 으로 설치 가능", MANUAL: "수동 설치 필요", AMBIGUOUS: "애매함",
                 NOT_NEEDED: "설치 불필요"}

# 묶음의 winget 목록에만 있는 항목 중 런타임/시스템 구성요소로 보는 id 접두어(설치 불필요로 접는다)
_RUNTIME_ID_PREFIXES = ("microsoft.vcredist", "microsoft.dotnet", "microsoft.vclibs", "microsoft.ui.xaml",
                        "microsoft.windowsappruntime", "microsoft.appinstaller", "microsoft.edgewebview2runtime")
_YEAR = re.compile(r"\b(?:19|20)\d\d\b")

IMPORT_FLAGS = "--ignore-unavailable --accept-package-agreements --accept-source-agreements"


def import_command(json_path: str) -> str:
    """사용자가 직접 실행할 winget import 명령 한 줄(이 도구는 실행하지 않는다)."""
    return f'winget import -i "{json_path}" {IMPORT_FLAGS}'


def install_command(winget_id: str) -> str:
    return f"winget install --id {winget_id} --exact"


@dataclass(frozen=True)
class Item:
    name: str
    status: str
    reason: str
    old_version: str = ""
    new_version: str = ""
    new_name: str = ""  # 새 PC 에서 찾은 항목 이름(설치됨/애매함)
    winget_id: str = ""
    grade: str = ""  # 근거 등급: 확실 | 추정 | ""
    version_diff: bool = False
    in_import_file: bool = False  # winget import 로 설치되는 항목(묶음의 winget 목록에 있음)
    would_be: str = ""  # 애매함 항목이 설치돼 있지 않다면 필요한 방식(winget | manual)
    hint: str = ""  # 수동 설치 시 알아 둘 점(체크리스트 문구 재사용)

    @property
    def version_text(self) -> str:
        if self.version_diff:
            return f"버전 다름(구 {self.old_version} → 새 {self.new_version})"
        return self.new_version or self.old_version

    @property
    def needs_action(self) -> bool:
        return self.status in (WINGET, MANUAL, AMBIGUOUS)


@dataclass
class NewState:
    """새 PC 의 현재 설치 상태(읽기 전용 수집 결과)."""
    registry: list[Program] = field(default_factory=list)
    winget: list[Program] = field(default_factory=list)
    list_text: str | None = None
    winget_ok: bool = True  # winget export 또는 list 를 읽을 수 있었는가


@dataclass
class InstallCheck:
    items: list[Item] = field(default_factory=list)
    has_winget_file: bool = False  # 묶음에 비어 있지 않은 winget 목록이 있는가
    winget_ok: bool = True  # 새 PC 에서 winget 을 읽을 수 있었는가
    notes: list[str] = field(default_factory=list)

    def by_status(self, status: str) -> list[Item]:
        return [i for i in self.items if i.status == status]

    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in STATUSES}
        for i in self.items:
            c[i.status] += 1
        return c

    @property
    def version_diff_count(self) -> int:
        return sum(1 for i in self.items if i.status == INSTALLED and i.version_diff)

    @property
    def remaining(self) -> int:
        """아직 할 일이 남은 항목 수(설치 필요 + 애매함)."""
        return sum(1 for i in self.items if i.needs_action)

    @property
    def import_needed(self) -> bool:
        return any(i.status == WINGET and i.in_import_file for i in self.items)

    @property
    def individual_commands(self) -> list[str]:
        """묶음의 winget 목록에는 없지만 대표 앱 표에 id 가 있어 개별로 설치할 수 있는 항목의 명령."""
        return [install_command(i.winget_id) for i in self.items
                if i.status == WINGET and i.winget_id and not i.in_import_file]

    def summary(self) -> str:
        c = self.counts()
        diff = f"(그중 버전 다름 {self.version_diff_count})" if self.version_diff_count else ""
        return (f"설치됨 {c[INSTALLED]}{diff} · winget 으로 설치 가능 {c[WINGET]} · 수동 설치 필요 {c[MANUAL]} · "
                f"애매함 {c[AMBIGUOUS]} · 설치 불필요 {c[NOT_NEEDED]}")

    def next_step(self) -> str:
        """다음 할 일 한 줄."""
        c = self.counts()
        if self.import_needed:
            return ("다음 할 일: winget 목록을 꺼내(pcmigrate winget-extract) 명령 창에서 winget import 를 직접 실행한 뒤 "
                    "이 명령을 다시 실행해 결과를 확인하세요.")
        if c[WINGET] or c[MANUAL]:
            return f"다음 할 일: 설치 안 된 프로그램 {c[WINGET] + c[MANUAL]}개를 직접 설치한 뒤 이 명령을 다시 실행해 확인하세요."
        if c[AMBIGUOUS]:
            return f"다음 할 일: 애매함 {c[AMBIGUOUS]}개는 실제로 설치돼 있는지 직접 확인하세요."
        return "다음 할 일: 없습니다. 묶음의 프로그램이 모두 확인되었습니다."

    def to_dict(self) -> dict:
        return {"summary": self.counts() | {"version_diff": self.version_diff_count, "remaining": self.remaining},
                "winget_file": self.has_winget_file, "winget_readable_on_this_pc": self.winget_ok,
                "notes": list(self.notes), "next": self.next_step(),
                "items": [asdict(i) | {"label": STATUS_LABELS[i.status]} for i in self.items]}


def newly_installed(prev: InstallCheck | None, cur: InstallCheck) -> list[Item]:
    """이전 확인에서는 설치가 필요했거나 애매했는데 이번에 '설치됨' 이 된 항목(다시 확인 결과 표시용)."""
    if prev is None:
        return []
    before = {i.name for i in prev.items if i.needs_action}
    return [i for i in cur.items if i.status == INSTALLED and i.name in before]


# --------------------------------------------------------------------------- 새 PC 쪽 판정

def _ver_key(v: str) -> tuple:
    parts = [p for p in re.split(r"[.\-_+]", v.strip().lower().lstrip("v")) if p != ""]
    while parts and parts[-1] == "0":
        parts.pop()
    return tuple(parts)


def _same_version(a: str, b: str) -> bool:
    return not a or not b or _ver_key(a) == _ver_key(b)


def _year_free(name: str) -> str:
    return _norm_display(_YEAR.sub(" ", name))


@dataclass(frozen=True)
class _Found:
    grade: str
    name: str
    version: str
    reason: str
    strength: int


class _NewPc:
    def __init__(self, state: NewState, extra_ids: list[str]) -> None:
        self.registry = [p for p in state.registry if p.name]
        self.versions: dict[str, str] = {}  # winget id(소문자) -> 새 PC 버전
        for w in state.winget:
            if w.winget_id:
                self.versions[w.winget_id.lower()] = w.version
        known = list(dict.fromkeys([w.winget_id for w in state.winget if w.winget_id] + extra_ids))
        self.links = parse_winget_list(state.list_text, known)
        for wid in self.links:  # winget list 에서 확인된 id 도 설치된 것으로 본다(export 가 없을 때)
            self.versions.setdefault(wid.lower(), self.links[wid].version)
        self.ids = list(dict.fromkeys(list(self.versions) + [w for w in self.links]))
        self._norm = [(_norm_display(q.name), _year_free(q.name), q) for q in self.registry]

    def has_id(self, wid: str) -> tuple[str, str] | None:
        """(새 PC 이름, 버전) 또는 None."""
        key = wid.lower()
        if key not in self.versions:
            return None
        link = next((l for i, l in self.links.items() if i.lower() == key), None)
        return (link.name if link else wid, self.versions[key])

    def find(self, p: Program, wid: str, probable_wid: str = "") -> _Found | None:
        best: _Found | None = None

        def offer(f: _Found) -> None:
            nonlocal best
            if best is None or (f.grade == CERTAIN, f.strength) > (best.grade == CERTAIN, best.strength):
                best = f

        if wid:
            hit = self.has_id(wid)
            if hit:
                offer(_Found(CERTAIN, hit[0], hit[1], f"winget id 일치({wid})", 5))
        if probable_wid:
            hit = self.has_id(probable_wid)
            if hit:
                offer(_Found(PROBABLE, hit[0], hit[1], f"구 PC 의 winget 추정 항목과 같은 id({probable_wid})가 있음", 3))
        nd, ny = _norm_display(p.name), _year_free(p.name)
        app = known_app(p.name)
        for qd, qy, q in self._norm:
            if nd and qd == nd:
                offer(_Found(CERTAIN, q.name, q.version, "이름 일치", 4))
            elif app is not None and (kq := known_app(q.name)) is not None and kq.winget_id == app.winget_id:
                offer(_Found(CERTAIN, q.name, q.version, "대표 앱 표 일치", 4))
            elif ny and qy == ny:
                offer(_Found(PROBABLE, q.name, q.version, "같은 제품의 다른 연도/버전(추정)", 2))
            elif nd and qd and min(len(nd), len(qd)) >= 6 and (nd in qd or qd in nd):
                offer(_Found(PROBABLE, q.name, q.version, "이름 일부 일치(추정)", 1))
        m = match_program(p, self.ids, self.links)
        if m is not None:
            hit = self.has_id(m.winget_id)
            nm, ver = hit if hit else (m.winget_id, "")
            offer(_Found(m.grade, nm, ver, f"winget {m.reason}({m.winget_id})", 3 if m.grade == PROBABLE else 5))
        return best


# --------------------------------------------------------------------------- 판정 본체

def check_install(old_registry: list[Program], old_winget: list[Program], old_list_text: str | None,
                  new: NewState) -> InstallCheck:
    """구 PC 목록(묶음)과 새 PC 상태를 비교한다. 순수 함수(외부 호출 없음)."""
    cls = classify_programs(old_registry, old_winget, old_list_text)
    old_ids = [w.winget_id or w.name for w in old_winget]
    newpc = _NewPc(new, old_ids + [w for _p, w in cls.winget_cmd] + [w for _p, w, _y in cls.winget_auto])
    out = InstallCheck(has_winget_file=bool(old_winget), winget_ok=new.winget_ok)
    old_ver = {w.winget_id.lower(): w.version for w in old_winget if w.winget_id}
    in_file = {i.lower() for i in old_ids}

    def evaluate(p: Program, wid: str, route: str, route_reason: str, probable_wid: str = "") -> Item:
        hint = install_hint_for(p.name)
        found = newpc.find(p, wid, probable_wid)
        ov = p.version or old_ver.get(wid.lower(), "") if wid else p.version
        if found is not None and found.grade == CERTAIN:
            diff = bool(ov and found.version and not _same_version(ov, found.version))
            return Item(p.name, INSTALLED, f"{found.reason}: {found.name}", ov, found.version, found.name, wid, CERTAIN,
                        diff, hint=hint)
        if found is not None:
            return Item(p.name, AMBIGUOUS, f"{found.reason}: {found.name}", ov, found.version, found.name, wid, PROBABLE,
                        in_import_file=route == WINGET and wid.lower() in in_file, would_be=route, hint=hint)
        return Item(p.name, route, route_reason, ov, "", "", wid, "", in_import_file=route == WINGET and wid.lower() in in_file,
                    hint=hint if route == MANUAL else "")

    claimed: set[str] = set()
    for p, why in cls.reference:
        out.items.append(Item(p.name, NOT_NEEDED, why, p.version))
    for p, wid, why in cls.winget_auto:
        claimed.add(wid.lower())
        out.items.append(evaluate(p, wid, WINGET, "묶음의 winget 목록에 있음(새 PC 의 winget 에서 찾을 수 있는지는 import 가 판단)"))
    for p, wid in cls.winget_cmd:
        claimed.add(wid.lower())
        out.items.append(evaluate(p, wid, WINGET, f"묶음의 winget 목록에는 없지만 개별 설치 가능: {install_command(wid)}"))
    for p, m in cls.manual:
        pw = m.winget_id if m is not None else ""
        if pw:
            claimed.add(pw.lower())
        reason = (f"winget 근거가 약함(추정: {m.winget_id}). 실제 설치 여부 확인" if m is not None
                  else "winget 에서 찾지 못함. 설치 파일/계정이 필요합니다")
        out.items.append(evaluate(p, "", MANUAL, reason, probable_wid=pw))
    for w in old_winget:  # 설치 목록에는 없고 winget 목록에만 있는 항목
        wid = w.winget_id or w.name
        if not wid or wid.lower() in claimed:
            continue
        claimed.add(wid.lower())
        if wid.lower().startswith(_RUNTIME_ID_PREFIXES) or bundle_reason(wid):
            out.items.append(Item(wid, NOT_NEEDED, "런타임/시스템 구성요소(winget 목록에만 있음)", w.version, winget_id=wid))
            continue
        item = evaluate(Program(wid, w.version, source="winget", winget_id=wid), wid, WINGET, "winget 목록에만 있는 항목")
        out.items.append(item)
    if not old_winget:
        out.notes.append("설정 묶음 파일에 winget 목록이 없거나 비어 있습니다. winget import 로 설치할 수 있는 항목이 없습니다.")
    if not new.winget_ok:
        out.notes.append("이 PC 에서 winget 을 읽지 못했습니다. 설치 여부는 프로그램 이름으로만 판정해 정확도가 낮습니다.")
    order = {s: n for n, s in enumerate((AMBIGUOUS, MANUAL, WINGET, INSTALLED, NOT_NEEDED))}
    out.items.sort(key=lambda i: (order[i.status], i.name.lower()))
    return out


def read_new_state(registry: Callable[[], list[Program]], winget_export: Callable[[], str | None],
                   winget_list: Callable[[], str | None]) -> NewState:
    """새 PC 의 설치 목록을 읽는다(레지스트리 + winget export + winget list). 모두 주입 가능, 읽기 전용."""
    reg = [p for p in registry() if p.source == "registry"]
    text = winget_export()
    winget: list[Program] = []
    if text:
        try:
            winget = parse_winget_export(text)
        except (ValueError, AttributeError, TypeError):
            winget = []
    list_text = winget_list()
    return NewState(reg, winget, list_text, winget_ok=bool(text or list_text))
