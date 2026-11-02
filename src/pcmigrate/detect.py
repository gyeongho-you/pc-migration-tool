"""설치 프로그램 감지: 레지스트리 Uninstall 키 + winget export.

외부 호출(레지스트리 읽기, winget 실행, 경로 존재 확인)은 모두 주입 가능하다.
읽기 전용 동작만 수행한다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .cancel import checkpoint
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
    # 설정 기록 모드의 폴더 추정용(선택). 사용자 이름이 든 경로일 수 있어 설정 묶음(registry.json)에는 담지 않는다(package_export 가 뺌).
    display_icon: str = ""
    uninstall_string: str = ""
    install_date: str = ""

    @property
    def display(self) -> str:
        """사람에게 보여 줄 이름. 표시 이름에 이미 버전(연도/점 숫자)이 있으면 버전을 또 붙이지 않는다(R2-11)."""
        return display_with_version(self.name, self.version)


_NAME_HAS_VERSION = re.compile(r"\d+(?:\.\d+)+|\b(?:19|20)\d\d\b|\bv\d+\b", re.I)


def display_with_version(name: str, version: str) -> str:
    """'이름 버전'. 이름에 같은 버전이 들어 있거나 이름이 이미 버전처럼 보이는 숫자(2024, 1.2.3, v5)를 가지면 이름만 쓴다."""
    if not version or version.lower() in name.lower() or _NAME_HAS_VERSION.search(name):
        return name
    return f"{name} {version}"


# reader() -> Program 목록. 기본 구현은 winreg 사용(Windows 전용).
RegistryReader = Callable[[], list[Program]]
# runner(args) -> (returncode, stdout). 기본 구현은 subprocess.
Runner = Callable[[list[str]], tuple[int, str]]


_UPDATE_TRAILING_KB = re.compile(r"\(KB\d{6,8}\)\s*$", re.IGNORECASE)
_UPDATE_RELEASE_TYPES = frozenset({"security update", "update", "hotfix", "update rollup", "critical update"})


def is_update_noise(name: str, release_type: str = "") -> bool:
    """레지스트리 설치 목록의 Windows/Office 업데이트·핫픽스 항목(프로그램이 아니다)인지. 확실한 패턴만 본다:
    ReleaseType 이 Security Update/Update/Hotfix/Update Rollup/Critical Update 이거나, 이름이 '(KB1234567)' 로 끝날 때.
    ('Microsoft Update Health Tools' 처럼 KB 번호가 없는 일반 프로그램은 건드리지 않는다.)"""
    return release_type.strip().lower() in _UPDATE_RELEASE_TYPES or bool(_UPDATE_TRAILING_KB.search(name or ""))


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
                        if not name or val("SystemComponent") == "1" or is_update_noise(name, val("ReleaseType")):
                            continue
                        p = Program(name, val("DisplayVersion"), val("Publisher"),
                                    install_location=val("InstallLocation"), display_icon=val("DisplayIcon"),
                                    uninstall_string=val("UninstallString"), install_date=val("InstallDate"))
                        found[(p.name, p.version)] = p
                except OSError:
                    continue
    return list(found.values())


# 창 모드 exe(--noconsole)에서 winget/robocopy 같은 콘솔 프로그램을 부를 때 검은 창이 번쩍이지 않게 하고, 없는 표준 입력 핸들 오류를 피한다.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _subprocess_runner(args: list[str], timeout: float = 300.0, poll: float = 0.2) -> tuple[int, str]:
    """하위 프로세스를 실행하고 기다린다. 기다리는 동안 0.2초마다 취소를 확인해, 취소되면 프로세스를 종료하고 Cancelled 를 던진다."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                out, _err = proc.communicate(timeout=poll)
                return proc.returncode, out
            except subprocess.TimeoutExpired:
                checkpoint()
                if time.monotonic() > deadline:
                    raise
    except BaseException:  # 취소/시간 초과/예상 밖 오류: 프로세스를 남기지 않는다
        _kill(proc)
        raise


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.communicate(timeout=5)
    except (subprocess.SubprocessError, OSError, ValueError):
        pass


WINGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")  # 실제 id 의 '+'(Notepad++.Notepad++)까지만 더 허용. 공백/따옴표/셸 문자는 거부


def valid_winget_id(value: object) -> bool:
    """winget 패키지 id 형식(영문자·숫자와 . - _ + 만, 128자 이내; checklist/install-check/winget-extract 가 모두 이 검사 하나를 쓴다). 명령 문자열에 넣기 전의 주입 방지 검사."""
    return isinstance(value, str) and WINGET_ID_RE.fullmatch(value) is not None


DEFAULT_LOCALE = "ko-KR"
_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")
_VERSION_ARG_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")


def valid_locale(value: object) -> bool:
    """BCP47 로캘 형식(ko-KR, en-US, zh-CN 등). 명령 문자열에 넣기 전의 주입 방지 검사."""
    return isinstance(value, str) and _LOCALE_RE.fullmatch(value) is not None


def usable_version_arg(value: object) -> bool:
    """winget install --version 에 그대로 넣어도 안전하고 쓸 만한 버전 문자열인가('Unknown'·공백·기호 포함 값은 제외)."""
    return (isinstance(value, str) and _VERSION_ARG_RE.fullmatch(value) is not None
            and value.lower() not in ("unknown", "latest"))


def winget_install_command(winget_id: str, version: str = "", locale: str = DEFAULT_LOCALE) -> str:
    """개별 설치 명령 한 줄(실행하지 않는다): winget install --id X --exact [--locale ko-KR] [--version V].

    id/로캘 형식이 틀리면 ValueError. 버전은 쓸 수 없는 값(빈 값, Unknown, 특수문자)이면 조용히 뺀다(구 PC 버전을 모르는 경우)."""
    if not valid_winget_id(winget_id):
        raise ValueError(f"올바르지 않은 winget id: {winget_id!r}")
    parts = [f"winget install --id {winget_id} --exact"]
    if locale:
        if not valid_locale(locale):
            raise ValueError(f"올바르지 않은 로캘(예: ko-KR): {locale!r}")
        parts.append(f"--locale {locale}")
    if usable_version_arg(version):
        parts.append(f"--version {version}")
    return " ".join(parts)


def safe_install_command(winget_id: str, version: str = "", locale: str = DEFAULT_LOCALE) -> str | None:
    """winget_install_command 의 예외 없는 판: id/로캘이 올바르지 않으면 None(그 항목만 건너뛰고 나머지는 계속하는 데 쓴다)."""
    try:
        return winget_install_command(winget_id, version, locale)
    except ValueError:
        return None


def parse_winget_export(text: str) -> list[Program]:
    """winget export JSON → Program 목록. 모양이 이상한 항목(목록이 아님, 객체가 아님, id 가 문자열이 아님)은 건너뛴다(손상/변조 방어)."""
    data = json.loads(text)
    programs: list[Program] = []
    sources = data.get("Sources", []) if isinstance(data, dict) else []
    for source in sources if isinstance(sources, list) else []:
        pkgs = source.get("Packages", []) if isinstance(source, dict) else []
        for pkg in pkgs if isinstance(pkgs, list) else []:
            pid = pkg.get("PackageIdentifier", "") if isinstance(pkg, dict) else ""
            if pid and isinstance(pid, str):
                programs.append(Program(pid, str(pkg.get("Version") or ""),
                                        source="winget", winget_id=pid))
    return programs


def _winget_export_once(runner: Runner, include_versions: bool) -> tuple[str | None, bool]:
    """winget export 1회. (JSON 원문 또는 None, 재시도할 만한 실패인가). 실행 자체가 안 되거나 시간 초과면 재시도하지 않는다."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "winget.json"
        args = ["winget", "export", "-o", str(out), "--accept-source-agreements"]
        if include_versions:
            args.append("--include-versions")
        try:
            code, _ = runner(args)
        except (OSError, subprocess.SubprocessError):
            return None, False
        if code != 0 or not out.exists():
            return None, True
        try:
            text = out.read_text(encoding="utf-8-sig")
            json.loads(text)
        except (json.JSONDecodeError, OSError):
            return None, False
        return text, False


def winget_export_raw(runner: Runner = _subprocess_runner) -> str | None:
    """`winget export --include-versions` 를 임시 파일로 실행해 JSON 원문(패키지마다 구 PC 버전 포함)을 반환한다. 실패 시 None.

    --include-versions 를 모르는 구버전 winget 은 오류 종료하므로, 종료 코드가 0 이 아니면 옵션 없이 한 번 더 시도한다(버전 없는 목록)."""
    text, retry = _winget_export_once(runner, True)
    if text is None and retry:
        text, _ = _winget_export_once(runner, False)
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
