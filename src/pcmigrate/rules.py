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
import re
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
    # 이 경로 폴더 안에서 복원을 허용할 '자동 실행될 수 있는' 확장자(예: SketchUp ".rb"). 기본 거부 확장자(package_base.DENY_EXTENSIONS)를 규칙이 명시적으로 풀 때만 쓴다.
    allow_extensions: tuple[str, ...] = ()


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


# 규칙 path 의 첫 요소로 쓸 수 있는 환경변수(대소문자 무시). 그 밖의 변수(%WINDIR%, %SystemRoot%, %ProgramFiles% 등)와 정의되지 않은 변수는 거부한다(M3).
# 기록 모드/가져오기/내보내기가 쓰는 루트(discover_data.ROOTS)와 같은 범위다.
ALLOWED_PATH_ENVS = frozenset({"appdata", "localappdata", "userprofile", "programdata", "onedrive", "onedrivecommercial", "onedriveconsumer"})
# 규칙이 allow_extensions 로 풀 수 없는 확장자: 실행 파일/스크립트/라이브러리/설치 파일. 플러그인 확장자(.rb, .lsp, .addin 등)는 규칙이 명시하면 허용된다.
NEVER_ALLOW_EXTENSIONS = frozenset({".exe", ".scr", ".com", ".pif", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse",
                                    ".wsf", ".wsh", ".hta", ".lnk", ".url", ".reg", ".msi", ".msp", ".cpl", ".dll", ".ocx", ".sys", ".jar",
                                    ".msc", ".inf", ".appref-ms", ".application", ".gadget", ".settingcontent-ms", ".mof", ".sct", ".wsc"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RESERVED_IDS = frozenset({"extra", "install"})  # 패키지 manifest 에서 규칙 없는 항목/설치 폴더 항목이 쓰는 id
_ENV_ELEM = re.compile(r"^%([^%\/:*?\"<>|]+)%$")


def _wildcard_only(part: str) -> bool:
    """'*', '*.*', '?', '**' 처럼 와일드카드(와 점/공백)만 있는 요소."""
    return bool(part) and not part.strip("*?. ")


def _check_rule_path(path: str, where: str) -> None:
    """규칙 path 는 허용된 %환경변수% 로 시작하는 사용자 폴더 기준 경로여야 한다: 상위 폴더(..)/'.' 요소, 드라이브 절대 경로, 네트워크(UNC),
    루트 상대 경로, 환경변수 없는 상대 경로, 허용 목록 밖 환경변수, 요소 1개(루트 전체), 와일드카드만 있는 요소, ':'(대체 데이터 스트림)는 거부(R1-15, M3)."""
    raw = path.replace("/", "\\")
    win = PureWindowsPath(raw)
    parts = raw.split("\\")
    if ".." in parts or "." in parts:
        raise RuleError(f"{where}: path '{path}' 에 상위 폴더(..) 또는 '.' 요소가 있습니다. 규칙 경로는 사용자 폴더 안이어야 합니다")
    if path.startswith(("\\\\", "//")):
        raise RuleError(f"{where}: path '{path}' 는 네트워크(UNC) 경로입니다. 규칙 경로는 %환경변수% 로 시작해야 합니다")
    if win.drive or win.root or path.startswith(("\\", "/")):
        raise RuleError(f"{where}: path '{path}' 는 절대 경로입니다. 규칙 경로는 %환경변수% 로 시작해야 합니다")
    if any(not p for p in parts):
        raise RuleError(f"{where}: path '{path}' 에 빈 경로 요소가 있습니다")
    m = _ENV_ELEM.match(parts[0])
    if not m:
        raise RuleError(f"{where}: path '{path}' 는 %환경변수% 로 시작해야 합니다(환경변수 없는 상대 경로는 현재 폴더로 새어 나갈 수 있습니다)")
    if m.group(1).lower() not in ALLOWED_PATH_ENVS:
        raise RuleError(f"{where}: path '{path}' 의 환경변수 %{m.group(1)}% 는 허용되지 않습니다(허용: " + ", ".join(sorted(ALLOWED_PATH_ENVS)) + ")")
    if len(parts) < 2:
        raise RuleError(f"{where}: path '{path}' 는 루트 폴더 전체입니다. 그 아래 프로그램 폴더/파일까지 적어야 합니다")
    if any(":" in p or "%" in p for p in parts[1:]):
        raise RuleError(f"{where}: path '{path}' 에 ':' 또는 '%' 가 있습니다(대체 데이터 스트림/미확장 변수는 허용하지 않습니다)")
    if _wildcard_only(parts[1]):  # 루트 바로 아래 요소는 프로그램 폴더 이름이어야 한다(안쪽 '*' 폴더는 버전 폴더 등에 쓰므로 허용)
        raise RuleError(f"{where}: path '{path}' 의 루트 바로 아래 요소가 와일드카드뿐입니다. 프로그램 폴더 이름을 적어야 합니다")


def parse_rule(data: object, source: str = "<rule>") -> Rule:
    if not isinstance(data, dict):
        raise RuleError(f"{source}: 최상위는 객체여야 합니다")
    for key in ("id", "name"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise RuleError(f"{source}: '{key}' 문자열이 필요합니다")
    rid = data["id"]
    if not _ID_RE.match(rid) or rid.lower() in RESERVED_IDS:
        raise RuleError(f"{source}: id '{rid}' 형식이 올바르지 않습니다(영문/숫자/_/- 만, 64자 이내, 'extra'/'install' 은 예약어)")
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
    seen_dest: set[str] = set()
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
        _check_rule_path(p["path"], where)
        _check_dest(p["dest"], where)
        allow = _str_list(p, "allow_extensions", where)
        if any(not a.startswith(".") or a != a.lower() or "*" in a or "\\" in a or "/" in a for a in allow):
            raise RuleError(f"{where}: allow_extensions 는 '.rb' 처럼 점으로 시작하는 소문자 확장자여야 합니다")
        if any(a in NEVER_ALLOW_EXTENSIONS for a in allow):
            raise RuleError(f"{where}: allow_extensions 에 실행/라이브러리 확장자({', '.join(a for a in allow if a in NEVER_ALLOW_EXTENSIONS)})는 쓸 수 없습니다")
        dkey = PurePosixPath(p["dest"].replace("\\", "/")).as_posix().casefold().strip("/")
        if dkey in seen_dest:
            raise RuleError(f"{where}: dest '{p['dest']}' 가 같은 규칙의 다른 path 와 겹칩니다(복원 때 서로 덮어씁니다)")
        seen_dest.add(dkey)
        paths.append(PathRule(p["path"], p["dest"], kind, str(p.get("description", "")), allow))

    warnings = _str_list(data, "manual_warnings", source)
    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise RuleError(f"{source}: 'notes' 는 문자열이어야 합니다")
    version = data.get("version", "1")
    if not isinstance(version, str) or not version.strip():
        raise RuleError(f"{source}: 'version' 은 비어 있지 않은 문자열이어야 합니다")
    exclude = _str_list(data, "exclude", source)
    if any(_wildcard_only(x) for x in exclude):
        raise RuleError(f"{source}: exclude 에 '*' 처럼 와일드카드만 있는 패턴은 쓸 수 없습니다(모든 파일이 제외됩니다)")
    return Rule(data["id"], data["name"], patterns, install_paths, reg_keys,
                tuple(paths), warnings, notes, version, exclude,
                exclude_display_name_patterns=exclude_patterns)


def load_rule_file(path: Path) -> Rule:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuleError(f"{path}: JSON 파싱 실패: {e}") from e
    return parse_rule(data, str(path))


def load_rules(rules_dir: Path, errors: list[str] | None = None) -> list[Rule]:
    """디렉터리의 *.json 을 모두 로드한다. id 중복은 오류.

    errors 를 주면(리스트) 규칙 파일 하나가 잘못돼도(스키마 위반, JSON 오류, id 중복) 그 파일만 건너뛰고 사유를 errors 에 덧붙인 채 나머지를 로드한다
    (사용자가 넣은 규칙 하나가 전체를 막지 않게). 주지 않으면 첫 오류에서 RuleError(기존 동작). 폴더가 없거나 읽을 수 있는 규칙이 하나도 없으면 항상 RuleError."""
    rules_dir = Path(rules_dir)
    if not rules_dir.is_dir():
        raise RuleError(f"규칙 폴더를 찾을 수 없습니다: {rules_dir} "
                        "(프로그램 폴더의 rules 폴더가 지워졌거나 --rules 경로가 잘못됐을 수 있습니다. "
                        "프로그램을 다시 내려받아 주세요)")
    rules: list[Rule] = []
    seen: set[str] = set()
    for f in sorted(rules_dir.glob("*.json")):
        try:
            rule = load_rule_file(f)
            if rule.id in seen:
                raise RuleError(f"{f}: 중복된 규칙 id '{rule.id}'")
        except (RuleError, OSError, UnicodeDecodeError) as e:
            if errors is None:
                if isinstance(e, RuleError):
                    raise
                raise RuleError(f"{f}: 읽을 수 없음: {e}") from e
            errors.append(str(e) if isinstance(e, RuleError) else f"{f}: 읽을 수 없음: {e}")
            continue
        seen.add(rule.id)
        rules.append(rule)
    if not rules and errors:  # 읽을 수 있는 규칙이 없고 건너뛴 파일이 있으면 그 오류를 그대로 알린다(원인을 가리지 않는다)
        raise RuleError("; ".join(errors))
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
