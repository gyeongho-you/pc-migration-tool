"""패키지 공통: 상수, 오류 타입, 경로 안전/이름 검사.

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import fnmatch
import json
import os
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from . import failure
from .rules import Rule


FORMAT_VERSION = 1
EXTRA_ID = "extra"  # 규칙 없는 프로그램 중 사용자가 선택해 포함한 항목(manifest rule id)
NOT_SELECTED = "not_selected"  # 사용자가 프로그램별 복원 선택(only/skip)에서 뺀 항목: 쓰지 않고 '선택에서 제외됨'으로 기록
NOT_SELECTED_TEXT = "선택에서 제외됨"
INSTALL_ID = "install"  # 프로그램 설치 폴더의 설정 파일(install_dir 모듈): 복원은 기본 꺼짐
MANIFEST = "manifest.json"
CHUNK = 1024 * 1024
ZIP64_THRESHOLD = 2 * 1024 ** 3
# 규칙의 exclude 와 별개로 항상 제외: 라이선스는 복원하지 않는다.
RESTORE_EXCLUDE = ("*.lic", "*.pcmigrate-part")
# 브라우저/앱이 실행 중에 잡는 잠금 파일: 복사해도 쓸모가 없고(새 PC 에서 '이미 실행 중' 오해를 부름) 읽기도 실패한다.
LOCK_NAMES = ("LOCK", "lockfile", "parent.lock", "SingletonLock", "SingletonCookie", "SingletonSocket")
GLOBAL_EXCLUDE = RESTORE_EXCLUDE + LOCK_NAMES
SPOOL_MAX = 64 * 1024 * 1024  # 이보다 작은 파일은 메모리에 두고, 큰 파일만 임시 폴더의 파일에 둔다
STAGE_PREFIX = ".pcmigrate-stage-"
WILDCARD = set("*?[")
# 압축 폭탄/거대 패키지 방어(B-5). deflate 는 압축비가 약 1000:1 을 넘을 수 없으므로 압축 방식을 제한하고 선언 크기를 지킨다.
MAX_MANIFEST_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 1_000_000
ALLOWED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
# 복원 거부 목록(B-1/B-7/F-1): 규칙/extra 모두에 적용한다. 다음 로그인/실행 때 코드가 실행될 수 있는 파일들이다.
# 확장자는 규칙 path 의 마지막 패턴이 그 확장자를 직접 적어 둔 경우(예: "*.exe")에만 그 규칙에서 허용한다.
DENY_EXTENSIONS = frozenset({".exe", ".scr", ".com", ".pif", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".ps1xml", ".vbs",
                             ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".url", ".reg", ".msi", ".msp", ".cpl",
                             # 19a: 로드/실행되는 라이브러리·스크립트·도움말·설정 조각(R1-9)
                             ".dll", ".ocx", ".sys", ".jar", ".py", ".pyw", ".cjs", ".mjs", ".xll", ".wll", ".xlam", ".dotm",
                             ".sct", ".wsc", ".msc", ".gadget", ".appref-ms", ".application", ".mof", ".inf", ".chm",
                             ".settingcontent-ms"})
# 폴더 이름(소문자)이 경로 어디에 있어도 막는다: Excel 이 시작할 때 자동으로 여는 폴더
DENY_DIR_NAMES = frozenset({"xlstart"})
# 사용자 홈 바로 아래(%USERPROFILE%)에서 도구가 읽어 명령을 실행하거나 비밀을 담는 점 파일(소문자 이름). .gitconfig 는 흔히 쓰므로 허용한다.
HOME_DENY_FILES = frozenset({".npmrc", ".yarnrc", ".yarnrc.yml", ".wslconfig", ".condarc", ".netrc", ".git-credentials", ".pypirc"})
HOME_DENY_SUBFILES = frozenset({(".kube", "config"), (".docker", "config.json")})
# 셸/SSH 가 시작할 때 읽어 실행하는 설정(소문자 파일 이름 / 폴더 이름)
DENY_FILE_NAMES = frozenset({".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile", ".zshrc", ".zprofile",
                             ".zshenv", ".zlogin", ".cshrc", ".kshrc", "authorized_keys"})
# .ssh 아래에서는 개인키가 아니라 접속 때 명령을 실행시킬 수 있는 파일(ProxyCommand 등)만 막는다
SSH_DENY_FILES = frozenset({"config", "rc", "environment", "authorized_keys2"})
_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                           *(f"COM{i}" for i in range(0, 10)), *(f"LPT{i}" for i in range(0, 10))})
_SUPERSCRIPT = str.maketrans("¹²³", "123")  # COM¹ COM² COM³ LPT¹ ... 도 장치 이름으로 취급된다
_BAD_CHARS = set('<>"|?*')

Expand = Callable[[str], str]


class PackageError(ValueError):
    """패키지가 손상되었거나 안전하지 않음."""


class SourceUnreadable(Exception):
    """원본 파일을 읽지 못함(다른 프로그램이 잠근 파일 등). zip 에는 아무것도 쓰지 않은 상태다."""

    def __init__(self, src: Path | str, cause: BaseException, during_read: bool = False) -> None:
        super().__init__(str(cause))
        self.src, self.cause = str(src), cause
        self.failure = failure.classify(cause, str(src))
        if during_read and self.failure.code == failure.PERMISSION:
            self.failure = failure.REASONS[failure.IN_USE]  # 열린 뒤 읽다가 접근 거부 = 바이트 범위 잠금(failure.ReadLocked 참고)


def unreadable_entry(e: SourceUnreadable) -> dict:
    """manifest['failed'] 항목: 쉬운 사유와 분류 코드(reason)를 함께 남긴다."""
    if e.failure.code == failure.IN_USE:
        text = "사용 중이라 읽지 못함(다른 프로그램이 파일을 잡고 있습니다)"
    else:
        text = f"읽지 못함: {e.failure.label}"
    return {"src": e.src, "error": text, "reason": e.failure.code}


# --------------------------------------------------------------------------- 경로 유틸

def split_rule_path(path: str) -> tuple[str, str] | None:
    """규칙 path(미확장)를 (base, pattern)으로 나눈다.

    base 는 와일드카드가 없는 앞부분, pattern 은 base 아래에서 glob 할 나머지.
    와일드카드가 없으면 마지막 요소만 pattern 이 된다. 요소가 2개 미만이면 None.
    """
    parts = PureWindowsPath(path).parts
    if len(parts) < 2:
        return None
    idx = next((i for i, p in enumerate(parts) if WILDCARD & set(p)), len(parts) - 1)
    if idx == 0:
        return None
    return str(PureWindowsPath(*parts[:idx])), "/".join(parts[idx:])


def _bad_component(part: str) -> bool:
    """Windows 에서 그 이름 그대로 만들 수 없거나 다른 파일로 바뀌는 경로 요소(예약 장치 이름, 끝의 점/공백, 제어/금지 문자)."""
    if part != part.rstrip(" .") or any(ord(c) < 32 or c in _BAD_CHARS for c in part):
        return True
    return part.split(".", 1)[0].rstrip(" ").translate(_SUPERSCRIPT).upper() in _DEVICE_NAMES


def unsafe_name(rel: str) -> bool:
    """상대 경로(posix)의 어느 요소라도 Windows 에서 쓸 수 없는 이름이면 True."""
    return any(_bad_component(p) for p in PurePosixPath(rel).parts)


def _safe_rel(rel: str) -> PurePosixPath:
    """패키지/manifest 의 상대 경로 검증. 위반 시 PackageError."""
    if not rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        raise PackageError(f"안전하지 않은 경로: {rel!r}")
    p = PurePosixPath(rel)
    if any(part in ("", ".", "..") for part in p.parts):
        raise PackageError(f"안전하지 않은 경로: {rel!r}")
    if unsafe_name(rel):
        raise PackageError(f"Windows 에서 쓸 수 없는 이름이 들어 있음: {rel!r}")
    return p


def _within(target: Path, root: Path) -> bool:
    try:
        Path(os.path.realpath(target)).relative_to(os.path.realpath(root))
        return True
    except ValueError:
        return False


def _usable_base(base_path: Path) -> bool:
    """확장되지 않은 %VAR% 가 남았거나 상대 경로면 쓰지 않는다(현재 작업 폴더로 새는 것을 방지)."""
    return base_path.is_absolute() and "%" not in str(base_path)


def _excluded(name: str, rule: Rule | None, base: tuple | None = None) -> bool:
    extra = rule.exclude if rule is not None else ()
    name = name.rstrip(" .")  # Windows 는 이름 끝의 점/공백을 지우므로 "x.lic " 도 x.lic 와 같다(F-9)
    return any(fnmatch.fnmatch(name.lower(), pat.lower()) for pat in (GLOBAL_EXCLUDE if base is None else base) + extra)
