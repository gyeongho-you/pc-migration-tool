"""설치 프로그램 감지: 레지스트리 Uninstall 키 + winget export.

외부 호출(레지스트리 읽기, winget 실행, 경로 존재 확인)은 모두 주입 가능하다.
읽기 전용 동작만 수행한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .envpaths import expand_env
from .rules import Rule

UNINSTALL_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"


@dataclass(frozen=True)
class Program:
    name: str
    version: str = ""
    publisher: str = ""
    source: str = "registry"  # "registry" | "winget"
    winget_id: str = ""
    install_location: str = ""  # 레지스트리 InstallLocation(없으면 빈 문자열). discover 의 연결 근거


# reader() -> Program 목록. 기본 구현은 winreg 사용(Windows 전용).
RegistryReader = Callable[[], list[Program]]
# runner(args) -> (returncode, stdout). 기본 구현은 subprocess.
Runner = Callable[[list[str]], tuple[int, str]]


def read_registry_programs() -> list[Program]:
    import winreg  # Windows 전용이라 지연 import

    hives = [
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    ]
    found: dict[tuple[str, str], Program] = {}
    for hive, view in hives:
        try:
            root = winreg.OpenKey(hive, UNINSTALL_SUBKEY, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        def val(n: str) -> str:
                            try:
                                return str(winreg.QueryValueEx(k, n)[0])
                            except OSError:
                                return ""
                        name = val("DisplayName")
                        if not name or val("SystemComponent") == "1":
                            continue
                        p = Program(name, val("DisplayVersion"), val("Publisher"),
                                    install_location=val("InstallLocation"))
                        found[(p.name, p.version)] = p
                except OSError:
                    continue
    return list(found.values())


def _subprocess_runner(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    return proc.returncode, proc.stdout


def parse_winget_export(text: str) -> list[Program]:
    data = json.loads(text)
    programs: list[Program] = []
    for source in data.get("Sources", []):
        for pkg in source.get("Packages", []):
            pid = pkg.get("PackageIdentifier", "")
            if pid:
                programs.append(Program(pid, pkg.get("Version", ""),
                                        source="winget", winget_id=pid))
    return programs


def winget_export_raw(runner: Runner = _subprocess_runner) -> str | None:
    """`winget export` 를 임시 파일로 실행해 JSON 원문을 반환한다. 실패 시 None."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "winget.json"
        try:
            code, _ = runner(["winget", "export", "-o", str(out),
                              "--accept-source-agreements"])
        except (OSError, subprocess.SubprocessError):
            return None
        if code != 0 or not out.exists():
            return None
        try:
            text = out.read_text(encoding="utf-8-sig")
            json.loads(text)
        except (json.JSONDecodeError, OSError):
            return None
        return text


def winget_export(runner: Runner = _subprocess_runner) -> list[Program]:
    """winget 패키지 목록을 반환한다. 실패 시 빈 목록."""
    text = winget_export_raw(runner)
    return parse_winget_export(text) if text is not None else []


def winget_list_raw(runner: Runner = _subprocess_runner) -> str | None:
    """`winget list`(winget 소스로 매칭된 설치 항목) 출력 원문. 실패/빈 출력이면 None.

    표를 열 위치로 자르지 않는다(한글 폭/이름 잘림에 취약). 원문만 돌려주고
    checklist.parse_winget_list 가 알려진 PackageIdentifier 토큰을 찾아 이름을 연결한다.
    """
    try:
        code, out = runner(["winget", "list", "--source", "winget", "--disable-interactivity",
                            "--accept-source-agreements"])
    except (OSError, subprocess.SubprocessError):
        return None
    return out if code == 0 and out.strip() else None


def registry_key_exists(key: str) -> bool:
    r"""``HKLM\SOFTWARE\...`` 형식 키의 존재 여부(읽기 전용, 64/32비트 뷰 모두 확인)."""
    import winreg

    hive_name, _, sub = key.partition("\\")
    hive = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}.get(hive_name.upper())
    if hive is None or not sub:
        return False
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view):
                return True
        except OSError:
            continue
    return False


def detect_programs(registry_reader: RegistryReader = read_registry_programs,
                    runner: Runner = _subprocess_runner) -> list[Program]:
    return registry_reader() + winget_export(runner)


def rule_matches(rule: Rule, programs: Iterable[Program],
                 path_exists: Callable[[str], bool] = os.path.exists,
                 expand: Callable[[str], str] = expand_env,
                 key_exists: Callable[[str], bool] = registry_key_exists) -> bool:
    if any(rule.matches_name(p.name) for p in programs):
        return True
    if any(path_exists(expand(p)) for p in rule.install_paths):
        return True
    return any(key_exists(k) for k in rule.registry_keys)


def match_rules(rules: Iterable[Rule], programs: list[Program],
                path_exists: Callable[[str], bool] = os.path.exists,
                expand: Callable[[str], str] = expand_env,
                key_exists: Callable[[str], bool] = registry_key_exists) -> list[Rule]:
    """감지된 프로그램의 규칙만 반환한다."""
    return [r for r in rules if rule_matches(r, programs, path_exists, expand, key_exists)]
