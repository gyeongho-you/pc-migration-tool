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

from .rules import Rule

UNINSTALL_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"


@dataclass(frozen=True)
class Program:
    name: str
    version: str = ""
    publisher: str = ""
    source: str = "registry"  # "registry" | "winget"
    winget_id: str = ""


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
                        p = Program(name, val("DisplayVersion"), val("Publisher"))
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


def winget_export(runner: Runner = _subprocess_runner) -> list[Program]:
    """`winget export` 를 임시 파일로 실행해 패키지 목록을 반환한다. 실패 시 빈 목록."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "winget.json"
        try:
            code, _ = runner(["winget", "export", "-o", str(out),
                              "--accept-source-agreements"])
        except (OSError, subprocess.SubprocessError):
            return []
        if code != 0 or not out.exists():
            return []
        try:
            return parse_winget_export(out.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return []


def detect_programs(registry_reader: RegistryReader = read_registry_programs,
                    runner: Runner = _subprocess_runner) -> list[Program]:
    return registry_reader() + winget_export(runner)


def rule_matches(rule: Rule, programs: Iterable[Program],
                 path_exists: Callable[[str], bool] = os.path.exists,
                 expand: Callable[[str], str] = os.path.expandvars) -> bool:
    names = [p.name.lower() for p in programs]
    for pat in rule.display_name_patterns:
        if any(pat.lower() in n for n in names):
            return True
    return any(path_exists(expand(p)) for p in rule.install_paths)


def match_rules(rules: Iterable[Rule], programs: list[Program],
                path_exists: Callable[[str], bool] = os.path.exists,
                expand: Callable[[str], str] = os.path.expandvars) -> list[Rule]:
    """감지된 프로그램의 규칙만 반환한다."""
    return [r for r in rules if rule_matches(r, programs, path_exists, expand)]
