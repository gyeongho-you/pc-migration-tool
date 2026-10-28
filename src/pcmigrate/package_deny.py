"""복원 거부 규칙(restore_denied, 규칙 경로 일치 검사).

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import fnmatch
import os
from pathlib import Path, PurePosixPath

from .discover_data import RESTORE_DENY_SUBSTRINGS
from .rules import Rule
from .package_base import DENY_DIR_NAMES, DENY_EXTENSIONS, DENY_FILE_NAMES, HOME_DENY_FILES, HOME_DENY_SUBFILES, SSH_DENY_FILES, split_rule_path


def rel_matches_rule(rule: Rule, base: str, rel: str) -> bool:
    """복원하려는 rel(base 기준 posix 경로)이 규칙에 선언된 path 패턴 안에 드는지(B-1).

    규칙 path 는 base + 패턴(요소별 glob). dir 은 패턴에 맞는 폴더 아래의 모든 파일, file 은 패턴에 맞는 파일 자신만 허용한다.
    """
    parts = rel.split("/")
    for pr in rule.paths:
        sp = split_rule_path(pr.path)
        if sp is None or sp[0] != base:
            continue
        pat = sp[1].split("/")
        if len(parts) < len(pat) or (pr.kind == "file" and len(parts) != len(pat)):
            continue
        if all(fnmatch.fnmatch(a.lower(), b.lower()) for a, b in zip(parts, pat)):
            return True
    return False


def _rule_names_extension(rule: Rule | None, base: str, ext: str) -> bool:
    """규칙 path 의 마지막 패턴이 그 확장자를 직접 적어 둔 경우(예: '*.exe')."""
    for pr in (rule.paths if rule is not None else ()):
        sp = split_rule_path(pr.path)
        if sp and sp[0] == base and sp[1].split("/")[-1].lower().endswith(ext):
            return True
    return False


def restore_denied(rel: str, target: Path, rule: Rule | None, base: str) -> str | None:
    """복원이 금지된 파일이면 사유, 아니면 None. 시작 프로그램/쉘 프로필/PowerShell 프로필/실행 파일 확장자 등(규칙/extra 공통)."""
    low = str(target).lower() + "\\"
    if any(d in low for d in RESTORE_DENY_SUBSTRINGS):
        return "복원이 금지된 위치(시작 프로그램/authorized_keys 등)"
    parts = [x.lower() for x in PurePosixPath(rel).parts]
    if any(x in DENY_DIR_NAMES for x in parts[:-1]) or parts[-1] in DENY_DIR_NAMES:
        return "복원이 금지된 위치(Excel 시작 폴더 XLSTART)"
    if base == "%USERPROFILE%" and (parts[0] in HOME_DENY_FILES and len(parts) == 1
                                    or len(parts) == 2 and (parts[0], parts[1]) in HOME_DENY_SUBFILES):
        return "복원이 금지된 설정 파일(도구가 실행할 명령이나 토큰이 들어갈 수 있는 홈 설정)"
    if parts[-1] in DENY_FILE_NAMES or (len(parts) >= 2 and parts[-2] == ".ssh" and parts[-1] in SSH_DENY_FILES):
        return "복원이 금지된 설정 파일(쉘 시작 파일/SSH 설정)"
    ext = os.path.splitext(parts[-1])[1]
    if ext in DENY_EXTENSIONS and not _rule_names_extension(rule, base, ext):
        return f"실행/스크립트 파일({ext})은 복원하지 않음(PowerShell 프로필 등)"
    return None
