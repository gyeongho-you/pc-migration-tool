r"""프로그램별 백업/복원 규칙(JSON) 스키마와 로더.

규칙 파일 형식::

    {
      "id": "revit",
      "name": "Autodesk Revit",
      "detect": {
        "display_name_patterns": ["Autodesk Revit"],  # Uninstall DisplayName 부분일치(대소문자 무시)
        "install_paths": ["%ProgramFiles%\\Autodesk"],  # 존재하면 감지
        "registry_keys": [],                          # 키가 존재하면 감지
        "exclude_display_name_patterns": []           # 선택. 이 이름도 포함한 프로그램은 이 규칙으로 감지하지 않음
      },
      "paths": [
        {"path": "%APPDATA%\\Autodesk\\Revit", "dest": "revit/appdata",
         "kind": "dir", "description": "..."}
      ],
      "manual_warnings": ["..."],
      "version": "1",                   # 선택. 규칙 파일 버전
      "exclude": ["*.lic"],             # 선택. 제외할 파일명 glob
      "notes": "확인 필요 항목 등"
    }

path 의 폴더 이름에는 glob(``*``, ``?``)을 쓸 수 있다(예: ``%APPDATA%\Autodesk\AutoCAD *``).
registry_keys 는 ``HKLM\SOFTWARE\...`` 형식이며 키가 존재하면 감지로 본다.

detect 조건은 하나 이상 있어야 하며, paths 는 백업과 복원에 공통으로 쓰인다
(path = 원본/복원 위치, dest = 패키지 내부 상대 경로).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


class RuleError(ValueError):
    """규칙 파일 스키마 위반."""


@dataclass(frozen=True)
class PathRule:
    path: str
    dest: str
    kind: str = "dir"  # "dir" | "file"
    description: str = ""


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    display_name_patterns: tuple[str, ...] = ()
    install_paths: tuple[str, ...] = ()
    registry_keys: tuple[str, ...] = ()
    paths: tuple[PathRule, ...] = ()
    manual_warnings: tuple[str, ...] = ()
    notes: str = ""
    version: str = "1"  # 규칙 파일 버전(패키지 manifest 에 기록)
    exclude: tuple[str, ...] = ()  # 백업에서 제외할 파일명 glob(예: "*.lic")
    exclude_display_name_patterns: tuple[str, ...] = ()  # 이 이름이 함께 들어 있으면 이 규칙으로 감지하지 않음

    def matches_name(self, name: str) -> bool:
        """설치 프로그램 표시 이름이 이 규칙의 이름 패턴에 맞고 제외 패턴에는 걸리지 않는지(대소문자 무시)."""
        low = name.lower()
        if any(x.lower() in low for x in self.exclude_display_name_patterns):
            return False
        return any(pat.lower() in low for pat in self.display_name_patterns)


def _str_list(obj: dict, key: str, where: str) -> tuple[str, ...]:
    value = obj.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise RuleError(f"{where}: '{key}' 는 비어 있지 않은 문자열 배열이어야 합니다")
    return tuple(value)


def _check_dest(dest: str, where: str) -> None:
    # 패키지 밖으로 나가는 경로(.., 절대경로)는 허용하지 않는다.
    win = PureWindowsPath(dest)
    posix = PurePosixPath(dest)
    if (win.is_absolute() or posix.is_absolute() or win.drive
            or ".." in win.parts or ".." in posix.parts):
        raise RuleError(f"{where}: dest '{dest}' 는 안전한 상대 경로여야 합니다")


def parse_rule(data: object, source: str = "<rule>") -> Rule:
    if not isinstance(data, dict):
        raise RuleError(f"{source}: 최상위는 객체여야 합니다")
    for key in ("id", "name"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise RuleError(f"{source}: '{key}' 문자열이 필요합니다")
    detect = data.get("detect")
    if not isinstance(detect, dict):
        raise RuleError(f"{source}: 'detect' 객체가 필요합니다")
    patterns = _str_list(detect, "display_name_patterns", source)
    install_paths = _str_list(detect, "install_paths", source)
    reg_keys = _str_list(detect, "registry_keys", source)
    exclude_patterns = _str_list(detect, "exclude_display_name_patterns", source)
    if not (patterns or install_paths or reg_keys):
        raise RuleError(f"{source}: detect 에 감지 조건이 하나 이상 필요합니다")

    raw_paths = data.get("paths", [])
    if not isinstance(raw_paths, list):
        raise RuleError(f"{source}: 'paths' 는 배열이어야 합니다")
    paths = []
    for i, p in enumerate(raw_paths):
        where = f"{source}: paths[{i}]"
        if not isinstance(p, dict):
            raise RuleError(f"{where} 는 객체여야 합니다")
        for key in ("path", "dest"):
            if not isinstance(p.get(key), str) or not p[key].strip():
                raise RuleError(f"{where}: '{key}' 문자열이 필요합니다")
        kind = p.get("kind", "dir")
        if kind not in ("dir", "file"):
            raise RuleError(f"{where}: kind 는 'dir' 또는 'file' 이어야 합니다")
        _check_dest(p["dest"], where)
        paths.append(PathRule(p["path"], p["dest"], kind, str(p.get("description", ""))))

    warnings = _str_list(data, "manual_warnings", source)
    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise RuleError(f"{source}: 'notes' 는 문자열이어야 합니다")
    version = data.get("version", "1")
    if not isinstance(version, str) or not version.strip():
        raise RuleError(f"{source}: 'version' 은 비어 있지 않은 문자열이어야 합니다")
    exclude = _str_list(data, "exclude", source)
    return Rule(data["id"], data["name"], patterns, install_paths, reg_keys,
                tuple(paths), warnings, notes, version, exclude,
                exclude_display_name_patterns=exclude_patterns)


def load_rule_file(path: Path) -> Rule:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuleError(f"{path}: JSON 파싱 실패: {e}") from e
    return parse_rule(data, str(path))


def load_rules(rules_dir: Path) -> list[Rule]:
    """디렉터리의 *.json 을 모두 로드한다. id 중복은 오류."""
    rules_dir = Path(rules_dir)
    if not rules_dir.is_dir():
        raise RuleError(f"규칙 폴더를 찾을 수 없습니다: {rules_dir} "
                        "(프로그램 폴더의 rules 폴더가 지워졌거나 --rules 경로가 잘못됐을 수 있습니다. "
                        "프로그램을 다시 내려받아 주세요)")
    rules: list[Rule] = []
    seen: set[str] = set()
    for f in sorted(rules_dir.glob("*.json")):
        rule = load_rule_file(f)
        if rule.id in seen:
            raise RuleError(f"{f}: 중복된 규칙 id '{rule.id}'")
        seen.add(rule.id)
        rules.append(rule)
    if not rules:
        raise RuleError(f"규칙 폴더에 규칙 파일(*.json)이 하나도 없습니다: {rules_dir} "
                        "(규칙이 없으면 어떤 프로그램도 감지하지 못합니다. 프로그램을 다시 내려받아 주세요)")
    return rules


def default_rules_dir() -> Path:
    """저장소의 rules/ (소스 트리 실행 기준). PyInstaller exe 에서는 번들된 rules/ (--add-data "rules;rules"),
    pip 일반 설치에서는 패키지 안의 rules_data/ (pyproject 의 package-dir 로 저장소 rules/ 를 담는다)."""
    bundled = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundled:
        return Path(bundled) / "rules"
    repo = Path(__file__).resolve().parents[2] / "rules"
    if repo.is_dir() and any(repo.glob("*.json")):
        return repo  # 소스 트리/편집 설치(pip install -e)
    return Path(__file__).resolve().parent / "rules_data"  # pip 일반 설치: pyproject 의 package-data 로 들어온 규칙
