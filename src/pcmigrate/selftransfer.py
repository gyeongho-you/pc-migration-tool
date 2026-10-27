r"""'자체 이전 필요' 프로그램 목록(JSON)과 매칭. 읽기 전용, 표시/경고 전용이다.

폴더를 복사해서는 옮길 수 없는 프로그램(카카오톡 대화, 금융보안 모듈, 인증서, 백신, 드라이버, Office, Adobe CC 등)을
규칙(rules/)과는 별개로 ``selftransfer/*.json`` 에 둔다. 코드 수정 없이 파일/항목을 추가할 수 있다.
discover/find-program 의 설정 폴더 후보가 이 목록에 해당하면 ``Candidate.self_transfer`` 에 표시를 붙인다(선택을 막지는 않는다).

파일 형식::

    {
      "format": 1,
      "items": [
        {
          "id": "kakaotalk", "name": "카카오톡",
          "category": "chat",                      # chat/finance_security/cert/antivirus/driver/office/adobe/other
          "grade": "A",                            # 근거 등급(A=이름이 설치 목록에 그대로 나옴, B=이름 변형 가능). C 이하는 넣지 않는다
          "display_name_patterns": ["kakaotalk"],  # 설치 프로그램 표시 이름 부분일치(대소문자 무시)
          "publisher_patterns": [],                # 선택. 게시자 이름 부분일치(정규화). 단독으로 쓰기엔 넓으니 신중히
          "exclude_display_name_patterns": [],     # 선택. 이 이름이 함께 들어 있으면 이 항목으로 보지 않음
          "folder_names": ["kakaotalk"],           # 선택. 폴더 이름(정규화 완전 일치)만으로도 표시
          "reason": "...", "todo": "...",          # 이유 한 줄 / 대신 할 일 한 줄
          "checklist_item": "kakao_cert_finance",  # 선택. 해야 할 일 목록의 공통 항목 id(안내를 한 번만 하려고 가리킴)
          "notes": "근거 등급 설명"
        }
      ]
    }

display_name_patterns/publisher_patterns/folder_names 중 하나 이상이 있어야 한다.
"""
from __future__ import annotations

import functools
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .checklist import _norm, _norm_company

CATEGORIES = ("chat", "finance_security", "cert", "antivirus", "driver", "office", "adobe", "other")
CATEGORY_LABELS = {"chat": "대화", "finance_security": "금융보안", "cert": "인증서", "antivirus": "백신", "driver": "드라이버",
                   "office": "Office", "adobe": "Adobe", "other": "기타"}
GRADES = ("A", "B")  # 근거가 약한 항목(C 이하)은 목록에 넣지 않는다
FORMAT = 1


class SelfTransferError(ValueError):
    """자체 이전 목록 파일 스키마 위반."""


@dataclass(frozen=True)
class SelfTransferItem:
    id: str
    name: str
    category: str
    reason: str
    todo: str
    grade: str = "A"
    display_name_patterns: tuple[str, ...] = ()
    publisher_patterns: tuple[str, ...] = ()
    exclude_display_name_patterns: tuple[str, ...] = ()
    folder_names: tuple[str, ...] = ()
    checklist_item: str = ""
    notes: str = ""

    def matches_program(self, name: str, publisher: str = "") -> bool:
        """설치 프로그램 이름(표시 이름)/게시자가 이 항목에 맞는지. 제외 패턴이 이름이나 게시자에 있으면 맞지 않는다(Rule.matches_name 과 같은 방식)."""
        low = (name or "").lower()
        pub_low = (publisher or "").lower()
        if any(x.lower() in low or x.lower() in pub_low for x in self.exclude_display_name_patterns):
            return False
        if any(p.lower() in low for p in self.display_name_patterns):
            return True
        pub = _norm_company(publisher) if publisher else ""
        return bool(pub) and any(_norm_company(p) and _norm_company(p) in pub for p in self.publisher_patterns)

    def matches_folder(self, folder: str) -> bool:
        n = _norm(folder.lstrip("."))
        return bool(n) and any(n == _norm(f) for f in self.folder_names)

    def info(self) -> dict:
        """Candidate.self_transfer 에 들어가는 표시 정보(JSON 으로 그대로 나간다)."""
        return {"id": self.id, "name": self.name, "category": self.category, "reason": self.reason, "todo": self.todo}


def _str_list(obj: dict, key: str, where: str) -> tuple[str, ...]:
    value = obj.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise SelfTransferError(f"{where}: '{key}' 는 비어 있지 않은 문자열 배열이어야 합니다")
    return tuple(value)


def parse_item(data: object, source: str = "<item>") -> SelfTransferItem:
    if not isinstance(data, dict):
        raise SelfTransferError(f"{source}: 항목은 객체여야 합니다")
    for key in ("id", "name", "reason", "todo"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise SelfTransferError(f"{source}: '{key}' 문자열이 필요합니다")
    category = data.get("category")
    if category not in CATEGORIES:
        raise SelfTransferError(f"{source}: category 는 {', '.join(CATEGORIES)} 중 하나여야 합니다: {category!r}")
    grade = data.get("grade", "A")
    if grade not in GRADES:
        raise SelfTransferError(f"{source}: grade 는 {', '.join(GRADES)} 중 하나여야 합니다(근거가 약한 항목은 넣지 않습니다): {grade!r}")
    names = _str_list(data, "display_name_patterns", source)
    pubs = _str_list(data, "publisher_patterns", source)
    folders = _str_list(data, "folder_names", source)
    excl = _str_list(data, "exclude_display_name_patterns", source)
    if not (names or pubs or folders):
        raise SelfTransferError(f"{source}: display_name_patterns/publisher_patterns/folder_names 중 하나 이상이 필요합니다")
    for k in ("checklist_item", "notes"):
        if not isinstance(data.get(k, ""), str):
            raise SelfTransferError(f"{source}: '{k}' 는 문자열이어야 합니다")
    return SelfTransferItem(data["id"], data["name"], category, data["reason"], data["todo"], grade, names, pubs, excl, folders,
                            data.get("checklist_item", ""), data.get("notes", ""))


def load_file(path: Path) -> list[SelfTransferItem]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SelfTransferError(f"{path}: 읽을 수 없습니다: {e}") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("items"), list):
        raise SelfTransferError(f"{path}: 형식이 올바르지 않습니다(format {FORMAT} 와 items 배열이 필요합니다)")
    return [parse_item(it, f"{path}: items[{i}]") for i, it in enumerate(data["items"])]


def load_items(folder: Path) -> list[SelfTransferItem]:
    """폴더의 *.json 을 모두 읽는다. id 중복은 오류. 폴더가 없으면 오류(항목이 없는 빈 폴더는 허용)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise SelfTransferError(f"자체 이전 목록 폴더를 찾을 수 없습니다: {folder} "
                                "(프로그램 폴더의 selftransfer 폴더가 지워졌을 수 있습니다. 프로그램을 다시 내려받아 주세요)")
    items: list[SelfTransferItem] = []
    seen: set[str] = set()
    for f in sorted(folder.glob("*.json")):
        for it in load_file(f):
            if it.id in seen:
                raise SelfTransferError(f"{f}: 중복된 항목 id '{it.id}'")
            seen.add(it.id)
            items.append(it)
    return items


def default_dir() -> Path:
    """저장소의 selftransfer/(소스 트리), PyInstaller exe 의 번들 selftransfer/, pip 일반 설치의 pcmigrate/selftransfer_data/ 순서."""
    bundled = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundled:
        return Path(bundled) / "selftransfer"
    repo = Path(__file__).resolve().parents[2] / "selftransfer"
    if repo.is_dir() and any(repo.glob("*.json")):
        return repo
    return Path(__file__).resolve().parent / "selftransfer_data"


@functools.lru_cache(maxsize=4)
def _cached(folder: str) -> tuple[SelfTransferItem, ...]:
    return tuple(load_items(Path(folder)))


def default_items(strict: bool = False) -> list[SelfTransferItem]:
    """기본 목록. strict=False 면 읽을 수 없을 때 빈 목록(표시만 빠지고 탐지는 계속된다), True 면 SelfTransferError."""
    try:
        return list(_cached(str(default_dir())))
    except SelfTransferError:
        if strict:
            raise
        return []


# --------------------------------------------------------------------------- 매칭

def match_program(items: list[SelfTransferItem], name: str, publisher: str = "") -> SelfTransferItem | None:
    for it in items:
        if it.matches_program(name, publisher):
            return it
    return None


def program_info(items: list[SelfTransferItem], name: str, publisher: str = "") -> dict | None:
    m = match_program(items, name, publisher)
    return m.info() if m else None


def flag_candidate(items: list[SelfTransferItem], folder: str, linked: list[tuple[str, str]], weak: bool = False) -> dict | None:
    """후보 폴더 하나의 자체 이전 표시. linked = 이 폴더에 연결된 프로그램들의 (이름, 게시자).

    - 연결된 프로그램이 항목에 맞으면 표시한다. 단 연결 근거가 약한(여러 프로그램에 걸친 제조사 폴더 등) 경우에는 연결된 프로그램이
      모두 같은 항목에 맞을 때만 표시한다(다른 프로그램의 설정이 든 폴더를 잘못 접지 않기 위해).
    - 항목의 folder_names 와 폴더 이름이 정확히 같으면 연결이 없어도 표시한다(예: NPKI)."""
    for it in items:
        hits = [it.matches_program(n, p) for n, p in linked]
        if hits and any(hits) and (not weak or all(hits)):
            return it.info()
        if it.matches_folder(folder):
            return it.info()
    return None


def summary_line(info: dict | None) -> str:
    """CLI/상세 한 줄: '자체 이전 필요: 이유 / 대신 할 일'."""
    return f"자체 이전 필요: {info['reason']} / 대신 할 일: {info['todo']}" if info else ""
