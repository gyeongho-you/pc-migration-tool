"""모드 A: 설정 패키지(.zip) 내보내기 / 가져오기.

패키지 구조::

    manifest.json            파일별 SHA-256/크기, 규칙 이름/버전, 수동 작업 경고
    programs/winget.json     winget export 원문(가능한 경우)
    programs/registry.json   레지스트리 Uninstall 기반 프로그램 목록
    data/<dest>/...          규칙 paths 에 따라 모은 설정 파일

안전 원칙:
- export 는 원본을 읽기만 한다. 라이선스 파일(*.lic)은 포함하지 않고 체크리스트로만 안내한다.
- import 는 manifest 를 신뢰하지 않는다. 규칙 id/base 가 현재 규칙과 일치해야 하고,
  모든 엔트리 경로와 복원 대상이 허용된 루트 밖으로 나가지 않는지(zip 슬립) 검증한다.
- 해시 검증이 하나라도 실패하면 복원하지 않는다.
- 덮어쓸 파일은 copier 와 동일하게 .bak 으로 백업한다.
"""
from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from .envpaths import expand_env
from . import __version__, failure, fs
from .copier import (COPIED, DENIED, FAILED, REJECTED, SKIPPED_SAME, WOULD_COPY, WOULD_OVERWRITE,
                     FileResult, Report, _backup_path, copy_file, sha256_file)
from .detect import Program
from . import coverage, settings_filter
from .discover_data import EXTRA_ROOTS, REGENERABLE_REASON, RESTORE_DENY_SUBSTRINGS, ROOTS, is_regenerable
from .progress import FAILED_KIND, OK, SKIPPED, UNREADABLE, ProgressInfo, ProgressTracker, categorize, fmt_bytes
from .rules import PathRule, Rule

FORMAT_VERSION = 1
EXTRA_ID = "extra"  # 규칙 없는 프로그램 중 사용자가 선택해 포함한 항목(manifest rule id)
NOT_SELECTED = "not_selected"  # 사용자가 프로그램별 복원 선택(only/skip)에서 뺀 항목: 쓰지 않고 '선택에서 제외됨'으로 기록
NOT_SELECTED_TEXT = "선택에서 제외됨"
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


# --------------------------------------------------------------------------- export

@dataclass
class Item:
    arc: str
    src: Path
    rule_id: str
    base: str  # 미확장 base (예: %APPDATA%\Autodesk)
    rel: str   # base 기준 상대 경로(posix)


def _walk_files_no_links(top: Path, files: list[Path], skipped: list[dict], rule_id: str,
                         failed: list[dict] | None = None, pruned: list[Path] | None = None) -> None:
    """top 아래 파일을 모은다. 정션/심볼릭 링크 폴더(reparse point)는 들어가지 않고 건너뛴 기록을 남긴다.

    260자를 넘는 경로도 긴 경로 접두로 순회하고(반환 경로는 접두 없는 사용자 표기), 읽지 못한 폴더는 조용히 넘기지 않고
    failed 에 기록한다(R2-1: 빼먹고 '실패 0' 으로 보고하지 않는다).
    pruned 가 주어지면(내보내기 extra 전용) top 아래에서 이름이 '재생성되는 임시 저장물'(is_regenerable)과 정확히 일치하는
    하위 폴더는 들어가지 않고 pruned 에 모은다(top 자신은 사용자가 고른 것이므로 검사하지 않는다)."""
    top_s = str(top)
    lp = fs.long_path(top_s)

    def on_error(e: OSError) -> None:
        if failed is not None:
            f = failure.classify(e, str(e.filename or top_s))
            where = str(e.filename or top_s)
            if where.startswith("\\\\?\\"):
                where = where[4:]
            failed.append({"src": where, "error": f"폴더를 읽지 못함: {f.label}", "reason": f.code})

    for root, dirs, names in os.walk(lp, followlinks=False, onerror=on_error):
        dirs.sort()
        plain = top_s + root[len(lp):]
        keep = []
        for d in dirs:
            dp = Path(plain) / d
            if fs.is_reparse(dp):
                skipped.append({"rule": rule_id, "path": str(dp), "reason": "심볼릭 링크/정션은 따라가지 않음"})
            elif pruned is not None and is_regenerable(Path(plain, d).relative_to(top).parts):
                pruned.append(dp)
            else:
                keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            files.append(Path(plain) / n)


def _denied_skip(rule_id: str, path: Path | str, why: str) -> dict:
    return {"rule": rule_id, "path": str(path), "denied": True,
            "reason": f"복원하지 않는 형식이라 패키지에 넣지 않음(보안): {why}"}


_BENIGN_SKIPS = ("제외 대상", "경로가 없음", "환경변수 확장 실패")


def _path_coverage(rule: Rule, pr: PathRule, items: list, skipped: list, failed: list | None, snap: tuple[int, int, int],
                   found: bool) -> dict:
    """경로 하나의 결과(설정 확인표용). snap = 이 경로를 처리하기 전의 (items, skipped, failed) 길이."""
    n_files = len(items) - snap[0]
    mine = skipped[snap[1]:]
    n_failed = (len(failed) - snap[2]) if failed is not None else 0
    bad = sum(1 for x in mine if not any(b in x.get("reason", "") for b in _BENIGN_SKIPS)
              and not x.get("cache") and not x.get("not_config"))
    return {"rule": rule.id, "name": rule.name, "path": pr.path, "description": pr.description, "files": n_files,
            "failed": n_failed, "excluded": bad,
            "status": coverage.classify(found=found, files=n_files, failed=n_failed, excluded=bad)}


def collect_items(rule: Rule, expand: Expand = expand_env, *, avoid: Callable[[Path], bool] | None = None,
                  roots: list[Path] | None = None, failed: list[dict] | None = None,
                  cov: list[dict] | None = None) -> tuple[list[Item], list[dict]]:
    """규칙에 따라 백업할 파일 목록과 건너뛴 항목(사유 포함)을 만든다. 읽기 전용.

    복원 때 거부될 파일(실행/스크립트 형식 등)은 내보낼 때도 같은 판정(restore_denied)으로 skipped 에 넣는다(denied=True).
    avoid(path)가 True 인 파일(출력 파일 자신/임시물 등)은 조용히 뺀다. roots 에는 찾은 최상위 경로를 모은다(출력이 그 안인지 검사용).
    cov 가 있으면 경로별 결과(설정 확인표용)를 덧붙인다.
    """
    items: list[Item] = []
    skipped: list[dict] = []
    for pr in rule.paths:
        snap = (len(items), len(skipped), len(failed) if failed is not None else 0)
        found = _collect_one_path(rule, pr, expand, avoid, roots, failed, items, skipped)
        if cov is not None:
            cov.append(_path_coverage(rule, pr, items, skipped, failed, snap, found))
    if cov is not None and not rule.paths:  # 자동으로 옮길 설정이 없는 규칙: 직접 해야 함
        cov.append({"rule": rule.id, "name": rule.name, "path": "", "description": coverage.NO_AUTO_SETTINGS,
                    "files": 0, "failed": 0, "excluded": 0, "status": coverage.ST_MANUAL})
    return items, skipped


def _collect_one_path(rule: Rule, pr: PathRule, expand: Expand, avoid, roots, failed, items: list[Item],
                      skipped: list[dict]) -> bool:
    """규칙 경로 하나를 처리해 items/skipped 에 덧붙인다. 경로가 실제로 있었으면 True."""
    split = split_rule_path(pr.path)
    if split is None:
        skipped.append({"rule": rule.id, "path": pr.path, "reason": "경로 형식이 올바르지 않음"})
        return False
    base, pattern = split
    base_path = Path(expand(base))
    if not _usable_base(base_path):
        skipped.append({"rule": rule.id, "path": pr.path, "reason": "환경변수 확장 실패 또는 상대 경로"})
        return False
    has_glob = bool(WILDCARD & set(pr.path))
    matches = sorted(base_path.glob(pattern)) if base_path.is_dir() else []
    if not matches:
        skipped.append({"rule": rule.id, "path": pr.path, "reason": "경로가 없음"})
        return False
    for m in matches:
        link = fs.reparse_in_chain(base_path, m)  # 일치한 경로 자신과 그 위쪽 폴더(base 아래)가 링크/정션인지
        if link:
            skipped.append({"rule": rule.id, "path": link, "reason": "심볼릭 링크/정션은 따라가지 않음"})
            continue
        if roots is not None:
            roots.append(m)
        files: list[Path] = []
        if m.is_file():
            files = [m]
        else:
            _walk_files_no_links(m, files, skipped, rule.id, failed)
        for f in files:
            if avoid is not None and avoid(f):
                continue
            if os.path.islink(fs.long_path(f)) or _excluded(f.name, rule):
                skipped.append({"rule": rule.id, "path": str(f),
                                "reason": "제외 대상(라이선스/임시/링크)"})
                continue
            rel = f.relative_to(base_path).as_posix()
            if unsafe_name(rel):
                skipped.append({"rule": rule.id, "path": str(f), "reason": "Windows 에서 쓸 수 없는 이름(예약 이름, 끝의 점/공백 등)"})
                continue
            why = restore_denied(rel, base_path / rel, rule, base)
            if why:
                skipped.append(_denied_skip(rule.id, f, why))
                continue
            if pr.kind == "file" and not has_glob:
                arc = f"data/{pr.dest}"
            else:
                arc = f"data/{pr.dest}/{rel}"
            items.append(Item(arc, f, rule.id, base, rel))
    return True


CACHE_COUNT_SECONDS = 8.0  # 제외한 임시 저장물 폴더 하나의 크기/파일 수를 세는 데 쓰는 최대 시간(넘으면 '이상'으로 표시)


def _pruned_skip(path: Path) -> dict:
    """제외한 임시 저장물 폴더의 이름·파일 수·용량을 센다(읽기 전용, 시간 상한). skipped 항목으로 돌려준다."""
    from .discover import scan_tree
    size, nfiles, _newest, _sec, complete = scan_tree(path, time.monotonic() + CACHE_COUNT_SECONDS, time.monotonic)
    return {"rule": EXTRA_ID, "path": str(path), "name": path.name, "reason": REGENERABLE_REASON, "cache": True,
            "files": nfiles, "bytes": size, "complete": bool(complete)}


def _size_or_zero(path: Path) -> int:
    try:
        return os.stat(fs.long_path(path)).st_size
    except OSError:
        return 0


def collect_extra_items(extras: list, expand: Expand = expand_env, *, avoid: Callable[[Path], bool] | None = None,
                        roots: list[Path] | None = None, failed: list[dict] | None = None,
                        include_caches: bool = False, cov: list[dict] | None = None,
                        extra_mode: str = settings_filter.DEFAULT_EXTRA_MODE,
                        limits: settings_filter.ConfigLimits | None = None
                        ) -> tuple[list[Item], list[dict]]:
    """discover 선택 파일로 고른 항목(ExtraItem)의 파일 목록. 읽기 전용, 라이선스/임시/링크 제외, 복원 거부 형식은 skipped(denied).

    include_caches=False(기본)면 폴더 안의 재생성되는 임시 저장물 폴더(Cache, GPUCache, node_modules 등 정확히 일치하는 이름)는
    건너뛰고 skipped(reason='재생성되는 임시 저장물', 이름·파일 수·용량 포함)에 기록한다.

    extra_mode='config'(기본, 허용 방식)면 settings_filter 의 설정 형식 파일만 담고(상한 포함) 나머지는 항목마다 요약 1~2개로 skipped 에
    기록한다(not_config / limit). 'all' 이면 예전 방식(폴더 전체)이다."""
    if extra_mode not in settings_filter.EXTRA_MODES:
        raise ValueError(f"extra_mode 는 {', '.join(settings_filter.EXTRA_MODES)} 중 하나여야 합니다: {extra_mode!r}")
    keys = {r.template: r.key for r in ROOTS}
    items: list[Item] = []
    skipped: list[dict] = []
    for ex in extras:
        snap = (len(items), len(skipped), len(failed) if failed is not None else 0)
        top = Path(ex.path)
        files: list[Path] = []
        pruned: list[Path] = []
        gate = settings_filter.ConfigGate(limits) if extra_mode == settings_filter.EXTRA_MODE_CONFIG else None
        if top.is_symlink() or (top.is_dir() and fs.is_reparse(top)):
            skipped.append({"rule": EXTRA_ID, "path": str(top), "reason": "심볼릭 링크/정션은 따라가지 않음"})
        else:
            if top.is_file():
                files = [top]
            else:
                _walk_files_no_links(top, files, skipped, EXTRA_ID, failed,
                                     None if (include_caches or getattr(ex, "include_caches", False)) else pruned)
            for pd in pruned:
                skipped.append(_pruned_skip(pd))
            if roots is not None:
                roots.append(top)
            for f in files:
                if avoid is not None and avoid(f):
                    continue
                if os.path.islink(fs.long_path(f)) or _excluded(f.name, None):
                    skipped.append({"rule": EXTRA_ID, "path": str(f), "reason": "제외 대상(라이선스/임시/링크)"})
                    continue
                rel = ex.rel if f == top else f"{ex.rel}/{f.relative_to(top).as_posix()}"
                if unsafe_name(rel):
                    skipped.append({"rule": EXTRA_ID, "path": str(f), "reason": "Windows 에서 쓸 수 없는 이름(예약 이름, 끝의 점/공백 등)"})
                    continue
                why = restore_denied(rel, Path(expand(ex.root)) / rel, None, ex.root)
                if why:
                    skipped.append(_denied_skip(EXTRA_ID, f, why))
                    continue
                if gate is not None and gate.judge(f.name, _size_or_zero(f), str(f)) is not None:
                    continue  # 허용 방식: 설정 형식이 아니거나 상한 초과(요약은 아래에서 한 번에 기록)
                items.append(Item(f"data/{EXTRA_ID}/{keys[ex.root]}/{rel}", f, EXTRA_ID, ex.root, rel))
            if gate is not None:
                skipped += gate.skipped_records(EXTRA_ID, str(top), top.name)
        if cov is not None:
            where = f"{ex.root}\\{ex.rel}"
            c = _path_coverage(Rule(EXTRA_ID, coverage.EXTRA_PROGRAM), PathRule(where, EXTRA_ID, "dir", where),
                               items, skipped, failed, snap, top.exists())
            c["name"] = f"{coverage.EXTRA_PROGRAM}: {ex.rel}"
            mine = [x for x in skipped[snap[1]:] if x.get("cache")]
            if mine:
                c.update(cache_dirs=len(mine), cache_files=sum(x["files"] for x in mine), cache_bytes=sum(x["bytes"] for x in mine))
            if gate is not None:
                c.update(gate.coverage_fields())
                if c["files"] == 0 and gate.nc_files and c["status"] == coverage.ST_EMPTY:
                    c["status"] = coverage.ST_EXCL  # 담을 설정 형식 파일이 하나도 없다(폴더 자체는 비어 있지 않음)
            cov.append(c)
    return items, skipped


def estimate_extra_config(root: str, rel: str, path: Path | str, expand: Expand = expand_env, *,
                          include_caches: bool = False, deadline: float | None = None,
                          clock: Callable[[], float] = time.monotonic,
                          limits: settings_filter.ConfigLimits | None = None) -> settings_filter.ConfigEstimate:
    """직접 고를 항목 하나(root 템플릿 + 최상위 rel)를 허용 방식으로 내보내면 담길 파일 수/용량을 미리 센다(읽기 전용).

    collect_extra_items(extra_mode='config')와 같은 기준(링크 안 따라감, 재생성 폴더 제외, 안전한 이름, 복원 거부 형식 제외,
    설정 형식 확장자, 파일/폴더 상한)을 같은 순서로 적용한다. deadline(clock 기준)을 넘으면 거기서 멈추고 complete=False 로 돌려준다(이상)."""
    top = Path(path)
    gate = settings_filter.ConfigGate(limits)
    est = settings_filter.ConfigEstimate()
    complete = True

    def judge(f: Path, rel_f: str) -> None:
        if unsafe_name(rel_f):
            return
        if restore_denied(rel_f, Path(expand(root)) / rel_f, None, root):
            return
        if os.path.islink(fs.long_path(f)) or _excluded(f.name, None):
            return
        gate.judge(f.name, _size_or_zero(f), str(f))

    try:
        if top.is_symlink() or (top.is_dir() and fs.is_reparse(top)):
            pass
        elif top.is_file():
            judge(top, rel)
        elif top.is_dir():
            lp = fs.long_path(str(top))
            tick = 0
            for cur, dirs, names in os.walk(lp, followlinks=False):
                plain = str(top) + cur[len(lp):]
                dirs.sort()
                keep = []
                for d in dirs:
                    dp = Path(plain) / d
                    if fs.is_reparse(dp):
                        continue
                    if not include_caches and is_regenerable(dp.relative_to(top).parts):
                        continue
                    keep.append(d)
                dirs[:] = keep
                for n in sorted(names):
                    tick += 1
                    if deadline is not None and tick % 100 == 0 and clock() > deadline:
                        complete = False
                        break
                    f = Path(plain) / n
                    judge(f, f"{rel}/{f.relative_to(top).as_posix()}")
                if not complete:
                    break
    except OSError:
        complete = False
    est.files, est.bytes, est.complete = gate.files, gate.bytes, complete
    est.excluded_files, est.excluded_bytes = gate.nc_files, gate.nc_bytes
    est.limit_files, est.limit_bytes = gate.lim_files, gate.lim_bytes
    est.ext_counts = dict(gate.ext_counts)
    return est


# --------------------------------------------------------------------------- 압축 방식

COMPRESS_MODES = ("auto", "always", "never")
# 이미 압축된 형식이라 deflate 를 다시 해도 거의 줄지 않고 시간만 든다(이 확장자는 auto 에서 STORED).
# 사진/영상/음악 코덱, 압축 파일, zip 기반 문서(OOXML/ODF), 내부 스트림이 압축된 PDF 가 근거다.
# 반대로 .rvt/.dwg/.3dm/.skp/.pln 등 CAD/BIM 형식은 목록에 두지 않는다: 압축률이 형식/버전마다 달라 확신할 수 없으므로
# 큰 파일은 아래 표본 검사로 실제 압축 효과를 보고 정한다.
ALREADY_COMPRESSED_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif",
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".webm",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
    ".zip", ".7z", ".rar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab",
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".jar", ".apk", ".vsix", ".nupkg", ".whl",
})
SAMPLE_MIN_SIZE = 8 * 1024 * 1024   # 이보다 큰 '모르는 확장자'는 표본으로 압축 효과를 본다
SAMPLE_BYTES = 256 * 1024
SAMPLE_RATIO = 0.90                 # 표본이 10% 이상 줄지 않으면 STORED


def _looks_incompressible(spool, size: int) -> bool:
    """spool(이미 읽어 둔 내용)의 앞/가운데/끝 표본을 빠르게 압축해 보고 거의 줄지 않으면 True."""
    raw = packed = 0
    for pos in (0, max(0, size // 2 - SAMPLE_BYTES // 2), max(0, size - SAMPLE_BYTES)):
        spool.seek(pos)
        chunk = spool.read(SAMPLE_BYTES)
        if chunk:
            raw += len(chunk)
            packed += len(zlib.compress(chunk, 1))
    spool.seek(0)
    return raw > 0 and packed / raw >= SAMPLE_RATIO


def choose_method(arc: str, size: int, compress: str, spool=None) -> int:
    """zip 압축 방식(STORED/DEFLATED)을 정한다. never/always 는 그대로, auto 는 확장자와 표본으로 판단."""
    if compress == "never":
        return zipfile.ZIP_STORED
    if compress == "always":
        return zipfile.ZIP_DEFLATED
    if os.path.splitext(arc)[1].lower() in ALREADY_COMPRESSED_EXTS:
        return zipfile.ZIP_STORED
    if spool is not None and size >= SAMPLE_MIN_SIZE and _looks_incompressible(spool, size):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


METHOD_NAMES = {zipfile.ZIP_STORED: "stored", zipfile.ZIP_DEFLATED: "deflated"}


class _PartZip(zipfile.ZipFile):
    """내보내기 중인 .part zip. 압축 옵션과 방금 쓴 엔트리의 기록(meta)을 _write_entry 와 주고받는다."""
    compress = "auto"
    last_meta: dict | None = None


def _zi_dump(zi: zipfile.ZipInfo) -> dict:
    """저장점 기록용: 중앙 디렉터리를 나중에 다시 쓰는 데 필요한 ZipInfo 필드."""
    return {"fn": zi.filename, "dt": list(zi.date_time), "ct": zi.compress_type, "ex": zi.extra.hex(),
            "cm": zi.comment.hex(), "cs": zi.create_system, "cv": zi.create_version, "xv": zi.extract_version,
            "fl": zi.flag_bits, "ia": zi.internal_attr, "ea": zi.external_attr, "ho": zi.header_offset,
            "crc": zi.CRC, "csz": zi.compress_size, "fsz": zi.file_size}


def _zi_load(d: dict) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(d["fn"], tuple(d["dt"]))
    zi.compress_type, zi.extra, zi.comment = d["ct"], bytes.fromhex(d["ex"]), bytes.fromhex(d["cm"])
    zi.create_system, zi.create_version, zi.extract_version = d["cs"], d["cv"], d["xv"]
    zi.flag_bits, zi.internal_attr, zi.external_attr = d["fl"], d["ia"], d["ea"]
    zi.header_offset, zi.CRC, zi.compress_size, zi.file_size = d["ho"], d["crc"], d["csz"], d["fsz"]
    return zi


def _write_entry(zf: zipfile.ZipFile, arc: str, src: Path, stage_dir: str | None = None) -> tuple[int, str]:
    """원본을 읽기 전용으로 끝까지 읽어 (크기, sha256)을 구한 뒤에만 zip 엔트리를 만든다.

    다른 프로세스가 바이트 범위를 잠근 파일은 읽는 도중에 PermissionError 가 난다. zip 엔트리를 먼저 열면 반쯤 쓰인
    엔트리가 남아 패키지 검증이 깨지므로, 먼저 임시 저장소(작은 파일은 메모리, 큰 파일은 stage_dir 의 임시 파일)에
    읽어 두고 성공했을 때만 zip 에 쓴다. 읽기 실패는 SourceUnreadable 로, zip/임시 저장소 쓰기 실패는 OSError 로 전파한다.

    압축 방식은 zf.compress(auto/always/never)로 정한다. zf 가 _PartZip 이면 저장점 기록(zf.last_meta)도 남긴다.
    """
    h = hashlib.sha256()
    n = 0
    compress = getattr(zf, "compress", "auto")
    try:
        st = os.stat(fs.long_path(src))
        size = st.st_size
        mtime = _zip_time_st(st)
        fin = open(fs.long_path(src), "rb")
    except OSError as e:
        raise SourceUnreadable(src, e) from e
    with fin, tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX, dir=stage_dir) as spool:
        while True:
            try:
                chunk = fin.read(CHUNK)
            except OSError as e:
                raise SourceUnreadable(src, e, during_read=True) from e
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
            spool.write(chunk)
        spool.seek(0)
        info = zipfile.ZipInfo(arc, date_time=mtime)
        info.compress_type = choose_method(arc, n, compress, spool)
        with zf.open(info, "w", force_zip64=max(size, n) > ZIP64_THRESHOLD) as fout:
            shutil.copyfileobj(spool, fout, CHUNK)
    if isinstance(zf, _PartZip):
        zf.last_meta = {"ssize": size, "mtime_ns": st.st_mtime_ns, "method": METHOD_NAMES[info.compress_type],
                        "off": info.header_offset, "end": zf.fp.tell(), "zi": _zi_dump(info)}
    return n, h.hexdigest()


def _zip_time_st(st: os.stat_result) -> tuple:
    t = datetime.fromtimestamp(st.st_mtime)
    return (max(t.year, 1980), t.month, t.day, t.hour, t.minute, t.second)


def _zip_time(path: Path) -> tuple:
    return _zip_time_st(os.stat(fs.long_path(path)))


# --------------------------------------------------------------------------- 저장점(이어받기)

PART_SUFFIX = ".part"
JOURNAL_SUFFIX = ".part.journal"
JOURNAL_VERSION = 1
SAVEPOINT_BYTES = 256 * 1024 * 1024
SAVEPOINT_SECONDS = 60.0
_GRACEFUL_SAVEPOINT = True  # 오류/취소로 끝날 때 마지막으로 완성된 엔트리까지 저장점을 한 번 더 찍는다(전원 차단 모의 시험은 끈다)


def part_paths(out: Path) -> tuple[Path, Path]:
    """(임시 zip, 진행 기록) 경로. 둘 다 이 도구가 만드는 임시물이다."""
    out = Path(out)
    return out.with_name(out.name + PART_SUFFIX), out.with_name(out.name + JOURNAL_SUFFIX)


def is_unfinished_part(path: Path | str) -> bool:
    return str(path).lower().endswith(PART_SUFFIX)


def _fingerprint(rules: list[Rule], extras: list | None, compress: str, include_caches: bool = False,
                 extra_mode: str = settings_filter.DEFAULT_EXTRA_MODE) -> tuple[str, dict]:
    lim = settings_filter.default_limits()
    info = {"tool": __version__, "format": FORMAT_VERSION, "compress": compress, "include_caches": bool(include_caches),
            "extra_mode": extra_mode,
            "extra_limits": [lim.file_bytes, lim.folder_bytes, lim.folder_files] if extra_mode == settings_filter.EXTRA_MODE_CONFIG else None,
            "rules": [[r.id, str(r.version)] for r in rules],
            "extras": sorted([e.root, e.rel] + (["caches"] if getattr(e, "include_caches", False) else [])
                             for e in (extras or []))}
    raw = json.dumps(info, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), info


def _fp_reason(old: dict, new: dict) -> str:
    if old.get("rules") is not None and [r[0] for r in old["rules"]] != [r[0] for r in new["rules"]]:
        return "선택한 프로그램(규칙)이 달라졌습니다"
    if old.get("rules") != new["rules"]:
        return "프로그램 규칙의 버전이 달라졌습니다"
    if old.get("extras") != new["extras"]:
        return "직접 고른 추가 항목이 달라졌습니다"
    if old.get("extra_mode") != new["extra_mode"] or old.get("extra_limits") != new["extra_limits"]:
        return "목록 밖 항목의 담을 범위(허용 방식/전체) 설정이 달라졌습니다"
    if old.get("compress") != new["compress"]:
        return "압축 설정이 달라졌습니다"
    if bool(old.get("include_caches")) != bool(new.get("include_caches")):
        return "캐시 폴더 포함 설정이 달라졌습니다"
    if old.get("tool") != new["tool"]:
        return "도구 버전이 달라졌습니다"
    return "내보내기 설정이 달라졌습니다"


class _Journal:
    """진행 기록(JSON lines). 줄마다 쓰고, sync() 때만 디스크에 확정(fsync)한다."""

    def __init__(self, path: Path, mode: str) -> None:
        self.f = open(path, mode)

    def write(self, obj: dict) -> None:
        self.f.write((json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))

    def sync(self) -> None:
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass


_ENTRY_INT = ("ssize", "mtime_ns", "off", "end", "size")
_ENTRY_STR = ("arc", "src", "sha256", "rule", "base", "rel", "method")
_ZI_KEYS = ("fn", "dt", "ct", "ex", "cm", "cs", "cv", "xv", "fl", "ia", "ea", "ho", "crc", "csz", "fsz")


def _entry_shape_ok(e: dict) -> bool:
    if not all(isinstance(e.get(k), str) for k in _ENTRY_STR):
        return False
    if not all(isinstance(e.get(k), int) and not isinstance(e.get(k), bool) and e[k] >= 0 for k in _ENTRY_INT):
        return False
    zi = e.get("zi")
    if not isinstance(zi, dict) or not all(k in zi for k in _ZI_KEYS):
        return False
    try:
        _zi_load(zi)
    except (TypeError, ValueError, KeyError):
        return False
    return e["end"] > e["off"] and zi["fn"] == e["arc"]


def read_journal(path: Path) -> dict:
    """진행 기록을 읽는다. 저장점 이후의 줄과 마지막의 끊긴 줄은 버린다.

    반환: {"head": dict|None, "entries": [저장점까지 확정된 엔트리], "zip_size": int, "error": str}
    error 가 비어 있지 않으면 기록이 손상된 것이다(이어받지 않는다).
    """
    out = {"head": None, "entries": [], "zip_size": 0, "error": ""}
    try:
        data = path.read_bytes()
    except OSError as e:
        out["error"] = f"저장점 기록을 읽을 수 없습니다({type(e).__name__})"
        return out
    lines = data.split(b"\n")
    lines.pop()  # 마지막 조각: 줄바꿈으로 끝났으면 빈 값, 아니면 쓰다 끊긴 줄(믿지 않는다)
    pending: list[dict] = []
    seen: set[str] = set()
    try:
        for i, raw in enumerate(lines):
            obj = json.loads(raw.decode("utf-8"))
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            kind = obj.get("t")
            if i == 0:
                if kind != "head" or obj.get("v") != JOURNAL_VERSION or not isinstance(obj.get("fp"), str) \
                        or not isinstance(obj.get("info"), dict):
                    raise ValueError("bad head")
                out["head"] = obj
            elif kind == "e":
                if not _entry_shape_ok(obj) or obj["arc"] in seen:
                    raise ValueError("bad entry")
                seen.add(obj["arc"])
                pending.append(obj)
            elif kind == "sp":
                z = obj.get("zip")
                if not isinstance(z, int) or isinstance(z, bool) or z < out["zip_size"]:
                    raise ValueError("bad savepoint")
                out["entries"] += pending
                pending = []
                out["zip_size"] = z
            elif kind != "f":
                raise ValueError("unknown line")
    except (ValueError, UnicodeDecodeError, RecursionError):
        out.update(head=None, entries=[], zip_size=0, error="저장점 기록이 손상되었습니다")
        return out
    if out["head"] is None:
        out["error"] = "저장점 기록이 비어 있습니다"
    return out


def inspect_savepoint(out: Path | str) -> dict | None:
    """같은 이름으로 다시 내보낼 때 이어받을 수 있는 임시 파일이 있는지 본다(읽기 전용, GUI 안내용).

    없으면 None, 있으면 {"files": 저장점까지 파일 수, "bytes": 바이트, "usable": 기록이 온전한지, "reason": 사유}.
    규칙/선택이 같은지는 실제로 내보낼 때 확인한다.
    """
    part, jpath = part_paths(Path(out))
    try:
        if not part.is_file() or not jpath.is_file():
            return None
        j = read_journal(jpath)
        if j["error"] or part.stat().st_size < j["zip_size"]:
            return {"files": 0, "bytes": 0, "usable": False, "reason": j["error"] or "임시 파일이 기록과 맞지 않습니다"}
        return {"files": len(j["entries"]), "bytes": sum(e["size"] for e in j["entries"]), "usable": True, "reason": ""}
    except OSError:
        return None


LOCK_SUFFIX = ".part.lock"
FAILED_SUFFIX = ".part.failed"  # 최종 검사에서 문제가 난 임시 패키지(진단용으로 보존)


def _is_ours(path: Path, kind: str) -> bool:
    """지워도 되는, 이 도구가 만든 임시물인지 '머리'로 확인한다(이름만 같은 다른 파일을 지우지 않는다).

    zip 류(.part/.part.new/.part.failed)는 빈 파일이거나 zip 지역 헤더(PK\x03\x04)로 시작해야 하고,
    journal 류는 첫 줄이 {"t":"head",...} JSON 이어야 한다."""
    try:
        with open(fs.long_path(path), "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    if kind == "zip":
        return head == b"" or head[:4] == b"PK" + bytes((3, 4))
    if not head:
        return True
    first = head.split(bytes((10,)), 1)[0]
    try:
        obj = json.loads(first.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(obj, dict) and obj.get("t") == "head"


def _partial_files(out: Path) -> list[tuple[Path, str]]:
    part, jpath = part_paths(out)
    return [(part, "zip"), (jpath, "journal"), (part.with_name(part.name + ".new"), "zip"),
            (jpath.with_name(jpath.name + ".new"), "journal"), (out.with_name(out.name + FAILED_SUFFIX), "zip")]


def discard_partial(out: Path | str, *, keep_failed: bool = False) -> None:
    """이 도구가 만든 임시물(.part, .part.journal, 정리 중 남은 .new, 보존해 둔 .part.failed)만 지운다. 완성본은 건드리지 않는다.

    지우기 전에 파일 머리를 확인한다. 이 도구가 만든 것이 아니거나 확인할 수 없으면 아무것도 지우지 않고
    PackageError('다른 출력 이름을 쓰세요')로 멈춘다(R1-5)."""
    out = Path(out)
    todo = [(p, k) for p, k in _partial_files(out)
            if (p.is_file() or p.is_symlink()) and not (keep_failed and p.name.endswith(FAILED_SUFFIX))]
    for p, kind in todo:
        if p.is_symlink() or not _is_ours(p, kind):
            raise PackageError(f"출력 이름과 겹치는 파일이 이 도구가 만든 임시 파일이 아닌 것 같아 지우지 않았습니다: {p}. "
                               "다른 출력 이름(또는 다른 폴더)을 쓰세요.")
    for p, _ in todo:
        p.unlink()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ctypes.get_last_error() == 5 or k32.GetLastError() == 5  # 접근 거부 = 살아 있음
            try:
                code = ctypes.c_ulong(0)
                ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
                return bool(ok) and code.value == 259  # STILL_ACTIVE
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
        except (AttributeError, OSError):
            return True  # 판단할 수 없으면 살아 있다고 보고 막는다(안전 쪽)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class _OutputLock:
    """<out>.part.lock: 같은 출력 이름으로 동시에 내보내기를 못 하게 하는 배타 락(PID 기록). 끝나면 지운다."""

    def __init__(self, out: Path) -> None:
        self.path = out.with_name(out.name + LOCK_SUFFIX)
        self.held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pid = self._owner()
                if pid and _pid_alive(pid) and pid != os.getpid():
                    raise PackageError(f"같은 출력 파일({self.path.name[:-len(LOCK_SUFFIX)]})로 내보내기가 이미 실행 중입니다(PID {pid}). "
                                       "끝나길 기다리거나 다른 출력 이름을 쓰세요.") from None
                try:  # 주인이 없는(비정상 종료로 남은) 락은 치우고 다시 시도한다
                    os.unlink(self.path)
                except OSError:
                    raise PackageError(f"이전 실행이 남긴 잠금 파일을 지우지 못했습니다: {self.path}. 다른 출력 이름을 쓰세요.") from None
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps({"pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
            self.held = True
            return
        raise PackageError(f"잠금 파일을 만들지 못했습니다: {self.path}. 다른 출력 이름을 쓰세요.")

    def _owner(self) -> int:
        try:
            return int(json.loads(self.path.read_text(encoding="utf-8")).get("pid") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            return 0

    def release(self) -> None:
        if self.held:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.held = False


def _cleanup_stale_stages(folder: Path) -> int:
    """이전 실행이 남긴 .pcmigrate-stage-* 임시 폴더 중 주인(PID)이 살아 있지 않은 것을 지운다(R1-7). 지운 개수 반환."""
    n = 0
    try:
        entries = [e for e in os.scandir(folder) if e.name.startswith(STAGE_PREFIX) and e.is_dir(follow_symlinks=False)]
    except OSError:
        return 0
    for e in entries:
        rest = e.name[len(STAGE_PREFIX):]
        head = rest.split("-", 1)[0]
        try:
            if head.isdigit() and "-" in rest:
                alive = _pid_alive(int(head))
            else:  # 옛 이름(PID 없음): 하루 넘게 손대지 않은 것만 정리
                alive = time.time() - e.stat().st_mtime < 24 * 3600
        except OSError:
            alive = True
        if not alive:
            shutil.rmtree(e.path, ignore_errors=True)
            n += 0 if os.path.exists(e.path) else 1
    return n


def _headers_ok(part: Path, entries: list[dict]) -> bool:
    """저장점까지의 각 엔트리 위치에 올바른 지역 헤더(PK\\x03\\x04 + 이름)가 있는지 본다."""
    try:
        with open(part, "rb") as f:
            for e in entries:
                f.seek(e["off"])
                head = f.read(30)
                if len(head) < 30 or head[:4] != b"PK\x03\x04":
                    return False
                nlen = int.from_bytes(head[26:28], "little")
                name = f.read(nlen)
                enc = "utf-8" if e["zi"]["fl"] & 0x800 else "cp437"
                if name.decode(enc, "replace") != e["arc"]:
                    return False
    except OSError:
        return False
    return True


def _compact(part: Path, keep: list[dict]) -> int:
    """바뀐/없어진 엔트리를 빼고 남길 엔트리의 바이트 구간만 새 파일로 옮긴다(압축을 다시 하지 않는다). 새 크기 반환."""
    new = part.with_name(part.name + ".new")
    try:
        with open(part, "rb") as src, open(new, "wb") as dst:
            for e in keep:
                src.seek(e["off"])
                pos = dst.tell()
                left = e["end"] - e["off"]
                while left > 0:
                    chunk = src.read(min(CHUNK, left))
                    if not chunk:
                        raise OSError("임시 파일이 기록보다 짧습니다")
                    dst.write(chunk)
                    left -= len(chunk)
                e["off"], e["end"] = pos, dst.tell()
                e["zi"]["ho"] = pos
            dst.flush()
            os.fsync(dst.fileno())
            size = dst.tell()
        os.replace(new, part)
        return size
    finally:
        new.unlink(missing_ok=True)


def _write_journal(jpath: Path, head: dict, entries: list[dict], zip_size: int) -> _Journal:
    """깨끗한 진행 기록을 새로 쓰고(원자적 교체) 이어쓰기용으로 연다."""
    tmp = jpath.with_name(jpath.name + ".new")
    j = _Journal(tmp, "wb")
    try:
        j.write(head)
        for e in entries:
            j.write(e)
        j.write({"t": "sp", "zip": zip_size, "n": len(entries)})
        j.sync()
    finally:
        j.close()
    os.replace(tmp, jpath)
    return _Journal(jpath, "ab")


@dataclass
class _Resumed:
    kept: dict = field(default_factory=dict)   # arc -> 확정된 엔트리 기록
    dropped: int = 0                           # 원본이 바뀌어 다시 담을 엔트리 수
    reason: str = ""                           # 이어받지 않고 처음부터 하는 이유
    zip_size: int = 0
    journal: object = None


def _source_unchanged(e: dict, item: "Item | None") -> bool:
    if item is None or str(item.src) != e["src"] or item.rule_id != e["rule"] or item.base != e["base"] or item.rel != e["rel"]:
        return False
    try:
        st = os.stat(fs.long_path(item.src))
    except OSError:
        return False
    return st.st_size == e["ssize"] and st.st_mtime_ns == e["mtime_ns"]


def _prepare_part(part: Path, jpath: Path, head: dict, info: dict, items: dict, resume: bool) -> _Resumed:
    """이어받을 수 있으면 .part 를 저장점까지 되돌려 두고 보존할 엔트리를 돌려준다. 아니면 임시물을 지우고 처음부터."""
    existed = part.exists() or jpath.exists()
    r = _Resumed()
    if not resume:
        if existed:
            r.reason = "'처음부터 다시 만들기'를 골라 이전 임시 파일을 지우고 새로 만듭니다"
    elif part.is_file() and not jpath.is_file():
        r.reason = "저장점 기록이 없어 처음부터 만듭니다"
    elif jpath.is_file() and not part.is_file():
        r.reason = "임시 패키지 파일이 없어 처음부터 만듭니다"
    elif part.is_file() and jpath.is_file():
        j = read_journal(jpath)
        if j["error"]:
            r.reason = j["error"] + "(처음부터 만듭니다)"
        elif j["head"]["fp"] != head["fp"]:
            r.reason = _fp_reason(j["head"]["info"], info) + "(처음부터 만듭니다)"
        elif part.stat().st_size < j["zip_size"] or any(e["end"] > j["zip_size"] for e in j["entries"]) \
                or not _headers_ok(part, j["entries"]):
            r.reason = "임시 패키지 파일이 기록과 맞지 않아 처음부터 만듭니다"
        elif j["entries"]:
            keep = [e for e in j["entries"] if _source_unchanged(e, items.get(e["arc"]))]
            r.dropped = len(j["entries"]) - len(keep)
            size = j["zip_size"]
            if r.dropped:
                size = _compact(part, keep)
            elif part.stat().st_size > size:
                with open(part, "r+b") as f:  # 저장점 뒤에 쓰던 부분은 믿지 않고 잘라낸다
                    f.truncate(size)
                    f.flush()
                    os.fsync(f.fileno())
            if keep:
                r.kept = {e["arc"]: e for e in keep}
                r.zip_size = size
                r.journal = _write_journal(jpath, j["head"], keep, size)
                return r
    discard_partial(part.with_name(part.name[:-len(PART_SUFFIX)]))
    return r


class _ResumeVerifyFailed(Exception):
    """이어받은 내용을 포함한 .part 가 최종 검사를 통과하지 못함. 임시물을 지우고 처음부터 다시 만든다."""


# --------------------------------------------------------------------------- export

def export_package(rules: list[Rule], out: Path, programs: list[Program],
                   winget_text: str | None, expand: Expand = expand_env,
                   winget_list_text: str | None = None, extras: list | None = None,
                   now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                   progress_info: Callable[[ProgressInfo], None] | None = None, *,
                   resume: bool = True, compress: str = "auto",
                   savepoint_bytes: int = SAVEPOINT_BYTES, savepoint_seconds: float = SAVEPOINT_SECONDS,
                   notice: Callable[[str], None] | None = None,
                   clock: Callable[[], float] = time.monotonic,
                   dest_info_fn: Callable[[str], fs.DestInfo] | None = None,
                   stage_dir: Path | str | None = None, include_caches: bool = False,
                   extra_mode: str = settings_filter.DEFAULT_EXTRA_MODE) -> dict:
    """감지된 프로그램의 규칙(rules)으로 설정 패키지를 만든다. manifest dict 반환.

    stage_dir: 64MB 를 넘는 파일을 담기 전에 임시로 읽어 두는 폴더의 위치(기본: 출력 파일이 있는 폴더). 출력 드라이브가 느리거나
    여유가 적을 때 로컬 디스크를 지정한다. 원본 폴더 안은 허용하지 않고, 그 안에 만든 임시 폴더는 끝나면 항상 지운다.

    progress_info 가 있으면 파일 찾기 -> 담기 -> 검사 단계의 진행(파일 수, 현재 파일, 분류별 개수)을 알린다(GUI 현황 카드용).

    만드는 동안에는 최종 이름이 아니라 '<out>.part'(zip)와 '<out>.part.journal'(진행 기록)에 쓰고, 검사를 통과한 뒤에만
    최종 이름으로 바꾼다(out 이 이미 있으면 .bak 으로 보존). 저장점(기본 256MB 또는 60초마다)까지는 실패/종료 뒤에도 남아,
    같은 out 과 같은 설정으로 다시 내보내면(resume=True) 저장점까지는 다시 담지 않는다. 실패/취소 시 .part 와 journal 은
    남기고 성공하면 지운다. resume=False 면 이전 .part/journal 을 지우고 처음부터 만든다.
    compress: auto(이미 압축된 형식은 STORED) / always(모두 deflate) / never(모두 STORED).
    include_caches: 기본 False. 직접 고른 폴더(extras) 안의 재생성되는 임시 저장물 폴더(Cache, GPUCache, node_modules 등)는 담지 않고
    skipped 에 이름·파일 수·용량을 기록한다. True 면 모두 담는다(규칙 경로 안은 이 옵션과 무관, 규칙의 exclude 를 따른다).
    extra_mode: 'config'(기본, 허용 방식) 목록 밖 항목(extras)에서 설정 형식 파일(settings_filter)만 담고 크기/파일 수 상한을 적용한다.
    'all' 은 예전 방식(폴더 전체에서 캐시/라이선스/보안 거부 형식만 제외). 이어받기 지문에 들어가므로 바꾸면 처음부터 다시 만든다.
    반환 manifest 에는 패키지에 저장되지 않는 "resume" 항목({resumed, kept_files, kept_bytes, dropped, reason})이 추가된다.
    파일 단위 실패는 manifest["failed"] 에 기록(읽지 못한 파일은 다음 시도에서 다시 읽는다).
    """
    if compress not in COMPRESS_MODES:
        raise ValueError(f"compress 는 {', '.join(COMPRESS_MODES)} 중 하나여야 합니다: {compress!r}")
    if extra_mode not in settings_filter.EXTRA_MODES:
        raise ValueError(f"extra_mode 는 {', '.join(settings_filter.EXTRA_MODES)} 중 하나여야 합니다: {extra_mode!r}")
    out = Path(out)
    if out.is_dir():  # 기존 폴더를 .bak 으로 옮기고 같은 이름의 파일을 만들게 되므로 시작하지 않는다
        raise IsADirectoryError(errno.EISDIR, "대상이 폴더입니다", str(out))
    args = (rules, out, programs, winget_text, expand, winget_list_text, extras, now, progress_info)
    kw = dict(compress=compress, savepoint_bytes=savepoint_bytes, savepoint_seconds=savepoint_seconds,
              notice=notice, clock=clock, dest_info_fn=dest_info_fn, stage_dir=stage_dir, include_caches=include_caches,
              extra_mode=extra_mode)
    lock = _OutputLock(out)
    lock.acquire()  # 같은 출력 이름으로 동시에 내보내면 .part 를 서로 망가뜨린다(R1-6)
    try:
        try:
            return _export_once(*args, resume=resume, **kw)
        except _ResumeVerifyFailed:
            if notice:
                notice("이어받은 내용이 최종 검사를 통과하지 못해 처음부터 다시 만듭니다.")
            m = _export_once(*args, resume=False, **kw)
            m["resume"]["reason"] = "이어받은 내용이 최종 검사를 통과하지 못해 처음부터 다시 만들었습니다"
            return m
    finally:
        lock.release()


def _export_once(rules, out, programs, winget_text, expand, winget_list_text, extras, now, progress_info, *,
                 resume, compress, savepoint_bytes, savepoint_seconds, notice, clock, dest_info_fn=None, stage_dir=None,
                 include_caches=False, extra_mode=settings_filter.DEFAULT_EXTRA_MODE) -> dict:
    part, jpath = part_paths(out)
    manifest: dict = {
        "format": FORMAT_VERSION,
        "tool_version": __version__,
        "created": now().isoformat(timespec="seconds"),
        "compress": compress,
        "rules": [], "files": [], "skipped": [], "failed": [], "coverage": [],
        "programs": {"registry": "programs/registry.json",
                     "winget": "programs/winget.json" if winget_text else None,
                     "winget_list": "programs/winget-list.txt" if winget_list_text else None},
        "manual_warnings": [],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(stage_dir) if stage_dir else out.parent
    if stage_dir and os.path.lexists(stage_parent) and not stage_parent.is_dir():
        raise PackageError(f"임시 폴더(--stage-dir)가 폴더가 아닙니다: {stage_dir}")
    if not stage_dir:
        _cleanup_stale_stages(out.parent)  # 강제 종료로 남은 이전 임시 폴더 정리(R1-7)
    # 큰 파일을 읽어 두는 전용 임시 폴더(항상 삭제). 원본 폴더 안이 아닌지 확인한 뒤에 만들기 위해 이름만 정해 둔다.
    stage = os.path.join(os.path.abspath(stage_parent), f"{STAGE_PREFIX}{os.getpid()}-{os.urandom(4).hex()}")
    stage_made = False
    zf: _PartZip | None = None
    fobj = None
    journal: _Journal | None = None
    finished = False
    st = {"end": 0}  # 마지막으로 완전히 쓴 엔트리의 끝 위치(저장점 위치)
    try:
        if progress_info:
            progress_info(ProgressInfo("find", 0, 0, 0, 0, message="패키지에 담을 파일을 찾는 중입니다…"))
        groups: list[list[Item]] = []
        mine = {os.path.normcase(os.path.abspath(p)) for p in
                (out, part, jpath, part.with_name(part.name + ".new"), jpath.with_name(jpath.name + ".new"),
                 out.with_name(out.name + FAILED_SUFFIX), out.with_name(out.name + LOCK_SUFFIX))}
        stage_root = os.path.normcase(os.path.abspath(stage))
        out_prefix = os.path.normcase(os.path.abspath(out))

        def avoid(p: Path) -> bool:
            """내보내기 중 이 도구가 만드는 파일(출력, 임시물, .bak, 임시 폴더)은 백업 대상에서 뺀다(R1-8)."""
            q = os.path.normcase(os.path.abspath(p))
            return q in mine or q.startswith(stage_root + os.sep) or (q.startswith(out_prefix + ".bak"))
        roots: list[Path] = []
        for rule in rules:  # 1) 파일 찾기(읽기 전용)
            items, skipped = collect_items(rule, expand, avoid=avoid, roots=roots, failed=manifest["failed"], cov=manifest["coverage"])
            manifest["skipped"] += skipped
            manifest["rules"].append({"id": rule.id, "name": rule.name, "version": rule.version, "notes": rule.notes})
            manifest["manual_warnings"].append({"rule": rule.id, "name": rule.name, "items": list(rule.manual_warnings)})
            groups.append(items)
        if extras:
            items, skipped = collect_extra_items(extras, expand, avoid=avoid, roots=roots, failed=manifest["failed"],
                                                 include_caches=include_caches, cov=manifest["coverage"],
                                                 extra_mode=extra_mode)
            manifest["skipped"] += skipped
            manifest["extras"] = [{"root": e.root, "rel": e.rel} for e in extras]
            manifest["extra_mode"] = extra_mode
            if extra_mode == settings_filter.EXTRA_MODE_CONFIG:
                lim = settings_filter.default_limits()
                manifest["extra_limits"] = {"file_bytes": lim.file_bytes, "folder_bytes": lim.folder_bytes,
                                            "folder_files": lim.folder_files}
            manifest["manual_warnings"].append({
                "rule": EXTRA_ID, "name": "규칙 없는 프로그램(선택 포함)",
                "items": ["규칙 없이 사용자가 직접 골라 포함한 폴더입니다. 신 PC 에서 같은 버전의 프로그램을 설치한 뒤 "
                          "복원하고, 라이선스/로그인은 직접 처리하세요."]})
            groups.append(items)
        for r in roots:  # 출력이 백업 대상 폴더 안이면 자기 자신을 담게 되므로 시작하지 않는다
            if _within(out, r) and Path(os.path.realpath(r)).is_dir():
                raise PackageError(f"출력 파일이 백업할 폴더 안에 있습니다: {out} (폴더: {r}). 백업 대상 밖(예: 다른 드라이브나 문서 폴더)에 저장하세요.")
            if _within(stage_parent, r) and Path(os.path.realpath(r)).is_dir():  # 원본은 읽기만: 원본 폴더 안에는 임시 폴더도 만들지 않는다
                raise PackageError(f"임시 폴더(--stage-dir)가 백업할 폴더 안에 있습니다: {stage_parent} (폴더: {r}). "
                                   "백업 대상 밖(예: 로컬 디스크의 다른 폴더)을 지정하세요.")
        try:  # 원본/출력 위치 점검을 통과한 뒤에야 임시 폴더를 만든다(원본 폴더 안에는 아무것도 만들지 않는다)
            if stage_dir:
                stage_parent.mkdir(parents=True, exist_ok=True)
                _cleanup_stale_stages(stage_parent)
            os.mkdir(stage, 0o700)
        except OSError as e:
            raise PackageError(f"임시 폴더(--stage-dir)를 만들 수 없습니다: {stage_parent} ({e.strerror or e})") from e
        stage_made = True
        total_files = sum(len(g) for g in groups)
        total_bytes = biggest = 0
        for g in groups:
            for it in g:
                try:
                    sz = os.stat(fs.long_path(it.src)).st_size
                except OSError:
                    continue
                total_bytes += sz
                biggest = max(biggest, sz)
        tracker = ProgressTracker("pack", total_bytes, total_files)
        tracker.counts[SKIPPED] = len(manifest["skipped"])
        tracker.resumed = 0
        last_emit = [0.0]

        def emit(force: bool = False) -> None:
            if progress_info is None:
                return
            t = time.monotonic()
            if force or t - last_emit[0] >= 0.5:
                last_emit[0] = t
                progress_info(tracker.snapshot())

        fp, fp_info = _fingerprint(rules, extras, compress, include_caches, extra_mode)
        head = {"t": "head", "v": JOURNAL_VERSION, "fp": fp, "info": fp_info}
        by_arc: dict[str, Item] = {}
        for g in groups:
            for it in g:
                by_arc.setdefault(it.arc, it)
        rs = _prepare_part(part, jpath, head, fp_info, by_arc, resume)
        kept_bytes = sum(e["size"] for e in rs.kept.values())
        try:
            _check_export_space(out, total_bytes - kept_bytes, biggest, compress, dest_info_fn, notice,
                                stage_parent=stage_parent if stage_dir else None)
        except PackageError:
            if rs.journal is not None:
                rs.journal.close()
            raise
        resume_info = {"resumed": bool(rs.kept), "kept_files": len(rs.kept), "kept_bytes": kept_bytes,
                       "dropped": rs.dropped, "reason": rs.reason}
        if rs.kept:
            msg = f"이어서 하는 중: 저장점까지 {len(rs.kept)}개 파일({fmt_bytes(kept_bytes)})을 보존했습니다."
            if rs.dropped:
                msg += f" 원본이 바뀐 {rs.dropped}개 파일은 다시 담습니다."
        else:
            msg = rs.reason
        if msg and notice:
            notice(msg)
        if rs.kept:
            journal = rs.journal
            fobj = open(part, "r+b")
            fobj.seek(0, os.SEEK_END)
            zf = _PartZip(fobj, "w", allowZip64=True)
            for e in rs.kept.values():
                zi = _zi_load(e["zi"])
                zf.filelist.append(zi)
                zf.NameToInfo[zi.filename] = zi
            st["end"] = rs.zip_size
            tracker.resumed = len(rs.kept)
            for e in rs.kept.values():
                tracker.note(OK)
                tracker.add(e["size"], 1)
        else:
            fobj = open(part, "wb")
            zf = _PartZip(fobj, "w", allowZip64=True)
            journal = _Journal(jpath, "wb")
            journal.write(head)
            journal.sync()
        zf.compress = compress
        if msg and progress_info:
            progress_info(ProgressInfo("pack", tracker.done_bytes, total_bytes, tracker.done_files, total_files, message=msg,
                                       ok=tracker.counts[OK], skipped=tracker.counts[SKIPPED], resumed=tracker.resumed))

        last_sp = [clock(), 0]  # 마지막 저장점 시각, 그 뒤 새로 담은 바이트
        done: dict[str, dict] = {}  # 이번에 새로 담은 엔트리
        seen: set[str] = set()

        def savepoint() -> None:
            """zip 을 디스크에 확정(fsync)한 뒤에 '여기까지 확정' 기록을 남기고 fsync 한다. 이 순서가 중요하다."""
            fobj.flush()
            os.fsync(fobj.fileno())
            journal.write({"t": "sp", "zip": st["end"], "n": len(rs.kept) + len(done)})
            journal.sync()
            last_sp[0], last_sp[1] = clock(), 0

        def add_items(items: list[Item]) -> None:
            for it in items:
                tracker.set_current(str(it.src))
                if it.arc in seen:
                    manifest["failed"].append({"src": str(it.src), "error": f"중복된 패키지 경로 {it.arc}"})
                    tracker.note(FAILED_KIND, str(it.src))
                    tracker.add(0, 1)
                    continue
                seen.add(it.arc)
                if it.arc in rs.kept:  # 저장점에서 보존된 파일: 다시 담지 않는다
                    e = rs.kept[it.arc]
                    manifest["files"].append({"arc": it.arc, "size": e["size"], "sha256": e["sha256"], "rule": it.rule_id,
                                              "base": it.base, "rel": it.rel, "method": e["method"]})
                    continue
                zf.last_meta = None
                try:
                    size, digest = _write_entry(zf, it.arc, it.src, stage)  # 2) 담기
                except SourceUnreadable as e:  # zip 에는 아무 흔적도 남지 않았다
                    manifest["failed"].append(unreadable_entry(e))
                    journal.write({"t": "f", "src": str(it.src), "error": manifest["failed"][-1]["error"]})
                    tracker.note(UNREADABLE if e.failure.code == failure.IN_USE else FAILED_KIND, str(it.src))
                    tracker.add(0, 1)
                    emit()
                    continue
                meta = zf.last_meta or {"ssize": size, "mtime_ns": 0, "method": "deflated", "off": st["end"],
                                        "end": fobj.tell(), "zi": _zi_dump(zf.NameToInfo[it.arc])}
                rec = {"arc": it.arc, "size": size, "sha256": digest, "rule": it.rule_id, "base": it.base,
                       "rel": it.rel, "method": meta["method"]}
                manifest["files"].append(rec)
                done[it.arc] = rec
                journal.write({"t": "e", "src": str(it.src), **rec, "ssize": meta["ssize"], "mtime_ns": meta["mtime_ns"],
                               "off": meta["off"], "end": meta["end"], "zi": meta["zi"]})
                st["end"] = meta["end"]
                last_sp[1] += size
                if last_sp[1] >= savepoint_bytes or clock() - last_sp[0] >= savepoint_seconds:
                    savepoint()
                tracker.note(OK)
                tracker.add(size, 1)
                emit()

        for items in groups:
            add_items(items)
        emit(True)
        savepoint()  # 마무리 단계가 실패해도 담은 파일은 다시 담지 않도록
        journal.close()
        journal = None
        zf.writestr("programs/registry.json", json.dumps([asdict(p) for p in programs],
                                                         ensure_ascii=False, indent=2))
        if winget_text:
            zf.writestr("programs/winget.json", winget_text)
        if winget_list_text:
            zf.writestr("programs/winget-list.txt", winget_list_text)
        zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.close()
        fobj.flush()
        os.fsync(fobj.fileno())
        fobj.close()
        zf = fobj = None
        if progress_info:  # 3) 만든 패키지를 다시 읽어 검사
            progress_info(ProgressInfo("check", total_bytes, total_bytes, total_files, total_files,
                                       message="만든 패키지를 다시 읽어 검사하는 중입니다…", ok=tracker.counts[OK],
                                       skipped=tracker.counts[SKIPPED], unreadable=tracker.counts[UNREADABLE],
                                       failed=tracker.counts[FAILED_KIND], resumed=tracker.resumed))
        problems = verify_package(part)[1]
        if problems:
            finished = True
            if resume_info["resumed"]:
                discard_partial(out)  # 이어받은 내용이 의심되므로 처음부터 다시 만든다(자동)
                raise _ResumeVerifyFailed()
            kept = _keep_failed_part(out, part, jpath)
            raise PackageError("만든 패키지를 다시 검사했더니 문제가 있어 저장하지 않았습니다(원본은 그대로입니다). "
                               + (f"문제 확인용으로 임시 파일을 {kept} 로 보존했습니다. " if kept else "")
                               + "같은 설정으로 다시 내보내면 처음부터 다시 만듭니다. 문제: " + "; ".join(problems[:3]))
        if out.exists():
            os.replace(out, _backup_path(out))
        os.replace(part, out)
        finished = True
        jpath.unlink(missing_ok=True)
        out.with_name(out.name + FAILED_SUFFIX).unlink(missing_ok=True)  # 이전에 검사 실패로 보존해 둔 임시 파일은 더 필요 없다
        manifest["resume"] = resume_info
    except BaseException:
        if not finished:
            _abandon(zf, fobj, journal, st["end"])
        raise
    finally:
        if journal is not None:
            journal.close()
        if stage_made:
            shutil.rmtree(stage, ignore_errors=True)
    return manifest


SPACE_FACTOR_DEFLATE = 0.5  # 압축하는 경우 필요한 공간의 하한 추정(원본 총량의 절반 미만이면 막는다). 정확한 값은 알 수 없다


def _check_export_space(out: Path, need_bytes: int, biggest: int, compress: str,
                        dest_info_fn: Callable[[str], fs.DestInfo] | None, notice: Callable[[str], None] | None,
                        stage_parent: Path | None = None) -> None:
    """출력 위치의 여유 공간/FAT32 4GB 한계를 만들기 전에 점검한다(transfer 와 같은 fs.check_space 사용, R1-10).

    zip 으로 얼마나 줄지는 알 수 없으므로, 압축하지 않는 설정이면 원본 총량을, 압축하면 그 절반을 최소 필요량으로 본다.
    큰 파일 하나를 임시 폴더에 읽어 두므로 가장 큰 파일 하나 분량도 더한다."""
    if need_bytes <= 0:
        return
    dest = (dest_info_fn or fs.describe_destination)(str(out.parent))
    factor = 1.0 if compress == "never" else SPACE_FACTOR_DEFLATE
    spool = biggest if biggest > SPOOL_MAX else 0
    other_stage = stage_parent is not None and fs.volume_root(stage_parent).casefold() != fs.volume_root(out.parent).casefold()
    if other_stage and spool:  # 임시 폴더가 다른 드라이브면 가장 큰 파일 하나 분량은 그쪽 여유 공간에서 따로 본다
        sd = (dest_info_fn or fs.describe_destination)(str(stage_parent))
        if sd.free is not None and sd.free < spool:
            raise PackageError(f"임시 폴더(--stage-dir)가 있는 드라이브의 여유 공간이 부족합니다. 가장 큰 파일 하나({fmt_bytes(spool)})를 "
                               f"잠시 읽어 둘 공간이 필요한데 현재 여유는 {fmt_bytes(sd.free)} 입니다. 여유가 있는 드라이브의 폴더를 지정하거나 "
                               "--stage-dir 를 빼세요(출력 폴더를 씁니다).")
    required = int(need_bytes * factor) + (0 if other_stage else spool)
    sc = fs.check_space(dest, required, need_bytes)
    if sc.fat32_blocked:
        raise PackageError(f"저장할 드라이브가 FAT32 라 4GB 가 넘는 패키지를 만들 수 없습니다(담을 파일 약 {fmt_bytes(need_bytes)}). "
                           "NTFS 드라이브를 쓰거나 담을 항목을 줄이세요.")
    if sc.space_blocked:
        raise PackageError(f"저장할 드라이브의 여유 공간이 부족합니다. 최소 약 {fmt_bytes(required)} 필요(담을 파일 {fmt_bytes(need_bytes)}), "
                           f"현재 여유 {fmt_bytes(sc.free or 0)}. 다른 드라이브를 쓰거나 정리한 뒤 다시 하세요(만들던 임시 파일은 보존되어 이어받습니다).")
    if sc.free_unknown and notice:
        notice("저장할 위치의 여유 공간을 조회하지 못해 공간 사전 검사를 건너뜁니다.")


def _keep_failed_part(out: Path, part: Path, jpath: Path) -> str:
    """검사에 실패한 .part 를 <out>.part.failed 로 보존하고(기존 것은 덮어씀) 진행 기록은 지운다. 보존 경로(실패하면 빈 문자열)."""
    failed = out.with_name(out.name + FAILED_SUFFIX)
    try:
        os.replace(part, failed)
    except OSError:
        return ""
    try:
        jpath.unlink(missing_ok=True)
    except OSError:
        pass
    return str(failed)


def _abandon(zf, fobj, journal, end: int) -> None:
    """실패/취소 때: 중앙 디렉터리를 쓰지 않고 닫는다. 가능하면 마지막으로 완성된 엔트리까지 저장점을 한 번 더 찍는다."""
    try:
        if _GRACEFUL_SAVEPOINT and fobj is not None and journal is not None and end:
            fobj.flush()
            os.fsync(fobj.fileno())
            journal.write({"t": "sp", "zip": end, "n": 0})
            journal.sync()
    except (OSError, ValueError):
        pass
    if zf is not None:
        zf.fp = None  # ZipFile.close() 가 .part 끝에 중앙 디렉터리를 쓰지 않게 한다
    if fobj is not None:
        try:
            fobj.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- import

def _hash_entry(zf: zipfile.ZipFile, name: str, limit: int | None = None) -> tuple[int, str]:
    """엔트리를 끝까지 읽어 (크기, sha256). limit 보다 많이 나오면 거기서 멈춘다(선언 크기보다 큰 엔트리/압축 폭탄 방어)."""
    h = hashlib.sha256()
    n = 0
    with zf.open(name) as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            n += len(chunk)
            if limit is not None and n > limit:
                break
    return n, h.hexdigest()


def _manifest_shape_problems(manifest: object) -> list[str]:
    """manifest 구조(타입)를 검사한다. 위조/손상된 manifest 가 어떤 모양이어도 예외 없이 문제 목록만 만든다."""
    if not isinstance(manifest, dict):
        return ["manifest 오류: 최상위가 객체가 아님"]
    if manifest.get("format") != FORMAT_VERSION:
        return ["manifest 오류: 지원하지 않는 형식"]
    if not isinstance(manifest.get("files"), list):
        return ["manifest 오류: files 가 목록이 아님"]
    problems = []
    rules = manifest.get("rules", [])
    if not isinstance(rules, list) or not all(isinstance(r, dict) and isinstance(r.get("id"), str) for r in rules):
        problems.append("manifest 오류: rules 형식이 올바르지 않음")
    warns = manifest.get("manual_warnings", [])
    if not isinstance(warns, list) or not all(
            isinstance(w, dict) and isinstance(w.get("items", []), list)
            and all(isinstance(i, str) for i in w.get("items", [])) for w in warns):
        problems.append("manifest 오류: manual_warnings 형식이 올바르지 않음")
    return problems


def _file_entry_ok(f: object) -> bool:
    if not isinstance(f, dict):
        return False
    if not all(isinstance(f.get(k), str) for k in ("arc", "sha256", "rule", "base", "rel")):
        return False
    size = f.get("size")
    return isinstance(size, int) and not isinstance(size, bool) and size >= 0


def verify_package(path: Path) -> tuple[dict | None, list[str]]:
    """zip 구조/경로 안전성과 manifest 해시를 검증한다. (manifest, 문제 목록) 반환.

    zip 이나 manifest 가 어떤 모양으로 망가져 있어도 예외를 던지지 않고 문제 목록으로 돌려준다.
    """
    problems: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        return None, [f"zip 을 열 수 없음: {e}"]
    with zf:
        infos = zf.infolist()
        names = [i.filename for i in infos]
        if len(names) > MAX_ENTRIES:
            return None, [f"패키지에 엔트리가 너무 많습니다({len(names):,}개 > {MAX_ENTRIES:,}개). 손상되었거나 안전하지 않은 패키지입니다"]
        for n in names:  # 모든 엔트리가 패키지 루트 밖으로 나가지 않는지
            try:
                _safe_rel(n.rstrip("/") if n.endswith("/") else n)
            except PackageError:
                problems.append(f"경로 탈출/비정상 엔트리: {n!r}")
        if len(set(names)) != len(names):  # 같은 이름이 두 번 있으면 어느 쪽이 검사되고 어느 쪽이 풀리는지 알 수 없다
            problems.append("패키지에 같은 이름의 엔트리가 중복되어 있음")
        for i in infos:
            if i.compress_type not in ALLOWED_COMPRESSION:
                problems.append(f"지원하지 않는 압축 방식(안전을 위해 거부): {i.filename!r}")
        if problems:
            return None, problems
        if MANIFEST not in names:
            return None, [f"{MANIFEST} 없음"]
        if zf.getinfo(MANIFEST).file_size > MAX_MANIFEST_BYTES:
            return None, [f"manifest 가 너무 큽니다(> {MAX_MANIFEST_BYTES // 1024 // 1024}MB). 안전하지 않은 패키지입니다"]
        try:
            with zf.open(MANIFEST) as mf_:
                raw = mf_.read(MAX_MANIFEST_BYTES + 1)  # 헤더가 거짓이어도 상한까지만 읽는다
            if len(raw) > MAX_MANIFEST_BYTES:
                return None, ["manifest 가 너무 큽니다. 안전하지 않은 패키지입니다"]
            manifest = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError, zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError,
                OSError) as e:  # UnicodeDecodeError 는 ValueError, 깊게 중첩된 JSON 은 RecursionError
            return None, [f"manifest 오류: {type(e).__name__}"]
        shape = _manifest_shape_problems(manifest)
        if shape:
            return None, shape
        listed = set()
        for f in manifest["files"]:
            if not _file_entry_ok(f):
                problems.append(f"manifest 항목 오류: {str(f)[:80]}")
                continue
            try:
                _safe_rel(f["arc"])
                _safe_rel(f["rel"])
            except PackageError:
                problems.append(f"manifest 항목 오류: {str(f)[:80]}")
                continue
            arc, size, digest = f["arc"], f["size"], f["sha256"]
            if arc in listed:
                problems.append(f"manifest 에 같은 패키지 경로가 중복됨: {arc}")
                continue
            listed.add(arc)
            if arc not in names:
                problems.append(f"패키지에 없음: {arc}")
                continue
            if zf.getinfo(arc).file_size != size:  # 선언과 다른 크기는 읽기 전에 거른다(거대 엔트리를 해시하느라 시간/디스크를 쓰지 않게)
                problems.append(f"크기/해시 불일치: {arc}")
                continue
            try:
                n, h = _hash_entry(zf, arc, size)
            except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, OSError):
                problems.append(f"엔트리를 읽을 수 없음(손상): {arc}")
                continue
            if n != size or h != digest:
                problems.append(f"크기/해시 불일치: {arc}")
        extra = set(names) - listed - {MANIFEST, "programs/registry.json", "programs/winget.json",
                                        "programs/winget-list.txt"}
        for e in sorted(extra):
            problems.append(f"manifest 에 없는 엔트리: {e}")
    return manifest, problems


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


GIT_CONFIG_WARNING = ("Git 설정 파일은 명령 실행 설정(core.sshCommand, alias 의 !명령 등)이 들어 있을 수 있어 "
                      "복원 전에 내용을 확인하세요")


def is_git_config_name(name: str) -> bool:
    """.gitconfig 와 .gitconfig 로 시작하는 이름(.gitconfig-work 등). Git 이 읽어 명령을 실행할 수 있는 설정 파일이다."""
    return name.casefold().startswith(".gitconfig")


def git_config_targets(items: list[dict]) -> list[str]:
    """복원 계획에서 실제로 복원될(거부/링크 거부가 아닌) Git 설정 파일의 대상 경로."""
    return [it["target"] for it in items if it.get("status") not in (DENIED, REJECTED, NOT_SELECTED) and is_git_config_name(Path(it["target"]).name)]


def program_key(f: dict) -> str:
    """manifest 파일 항목의 프로그램 단위 식별자. 규칙은 규칙 id, 규칙 없는 항목(extra)은 'extra:<루트키>/<최상위 이름>'."""
    if f.get("rule") != EXTRA_ID:
        return str(f.get("rule"))
    keys = {r.template: r.key for r in ROOTS}
    top = str(f.get("rel", "")).split("/")[0]
    return f"{EXTRA_ID}:{keys.get(f.get('base'), '?')}/{top}"


def program_matches(token: str, key: str) -> bool:
    """--only/--skip 의 항목 하나(token)가 프로그램 식별자(key)에 해당하는지. 대소문자 무시.
    규칙 id 는 정확히 일치. extra 는 'extra'(전부), 'extra:<루트키>/<이름>'(정확히), 'extra:<이름>'(루트 무관)으로 고를 수 있다."""
    t, k = token.strip().casefold(), key.casefold()
    if t == k:
        return True
    if k.startswith(EXTRA_ID + ":"):
        return t == EXTRA_ID or t == f"{EXTRA_ID}:{k.split('/', 1)[1]}"
    return False


def _norm_tokens(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    return [t.strip() for t in v if isinstance(t, str) and t.strip()]


@dataclass
class ImportPlan:
    manifest: dict
    items: list[dict] = field(default_factory=list)  # {entry, target, status, backup, program}
    problems: list[str] = field(default_factory=list)
    # 프로그램별 요약(화면의 프로그램별 체크용): {id, name, kind, selected, files, bytes, new, overwrite, same, denied, rejected, not_selected}
    programs: list[dict] = field(default_factory=list)
    unknown_programs: list[str] = field(default_factory=list)  # only/skip 에 적었지만 묶음에 없는 항목

    @property
    def warnings(self) -> list[dict]:
        return self.manifest.get("manual_warnings", [])

    @property
    def git_config_warning(self) -> str:
        """복원할 항목에 Git 설정 파일(.gitconfig)이 있으면 한 줄 경고, 없으면 빈 문자열."""
        return GIT_CONFIG_WARNING if git_config_targets(self.items) else ""


def plan_import(path: Path, rules: list[Rule], expand: Expand = expand_env, *,
                only_programs: list[str] | str | None = None, skip_programs: list[str] | str | None = None) -> ImportPlan:
    """검증 후 복원 계획을 만든다. 파일 시스템은 읽기만 한다.

    검증 단계가 놓친 구조 오류로 예외가 나더라도 traceback 대신 PackageError('패키지 검증 실패: …')로 알린다.

    only_programs/skip_programs: 프로그램(규칙 id 또는 extra 항목) 단위 복원 선택. only 가 있으면 그것만, skip 은 그것만 뺀다(둘 다면 only 후 skip).
    선택에서 빠진 프로그램의 파일은 대상을 읽지도 쓰지도 않고 status='not_selected' 로 기록한다. 묶음에 없는 이름은 plan.unknown_programs 에 남긴다.
    검증/보안 판정(허용 범위, 해시, 링크)은 선택과 무관하게 모든 항목에 똑같이 적용한다.
    """
    try:
        return _plan_import(path, rules, expand, _norm_tokens(only_programs), _norm_tokens(skip_programs))
    except PackageError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError) as e:
        raise PackageError(f"패키지 검증 실패: manifest 구조가 올바르지 않습니다({type(e).__name__})") from e


def _rejected_item(f: dict, target: Path, link: str, is_extra: bool, base_path: Path) -> dict:
    return {"entry": f["arc"], "target": str(target), "status": REJECTED, "backup": "", "size": f["size"],
            "base_dir": str(base_path),
            "sha256": f["sha256"], "extra": is_extra, "reason": failure.LINK_IN_PATH,
            "error": f"{failure.REASONS[failure.LINK_IN_PATH].text} ({link})"}


def _denied_item(f: dict, target: Path, why: str, is_extra: bool, base_path: Path) -> dict:
    return {"entry": f["arc"], "target": str(target), "status": DENIED, "backup": "", "size": f["size"],
            "base_dir": str(base_path), "sha256": f["sha256"], "extra": is_extra, "reason": DENIED,
            "error": f"복원 안 함: {why}"}


def _not_selected_item(f: dict, target: Path, is_extra: bool, base_path: Path, pkey: str) -> dict:
    return {"entry": f["arc"], "target": str(target), "status": NOT_SELECTED, "backup": "", "size": f["size"],
            "base_dir": str(base_path), "sha256": f["sha256"], "extra": is_extra, "program": pkey, "reason": NOT_SELECTED,
            "error": NOT_SELECTED_TEXT}


def _fill_programs(plan: ImportPlan, by_id: dict, only: list[str], skip: list[str]) -> None:
    """plan.items 에서 프로그램별 표(plan.programs)를 만들고, 묶음에 없는 only/skip 항목을 plan.unknown_programs 에 모은다."""
    order: list[str] = []
    rows: dict[str, dict] = {}
    for it in plan.items:
        k = it["program"]
        if k not in rows:
            order.append(k)
            if k.startswith(EXTRA_ID + ":"):
                name, kind = f"{coverage.EXTRA_PROGRAM}: {k.split('/', 1)[1]}", "extra"
            else:
                name, kind = (by_id[k].name if k in by_id else k), "rule"
            rows[k] = {"id": k, "name": name, "kind": kind, "selected": True, "files": 0, "bytes": 0, "new": 0, "overwrite": 0,
                       "same": 0, "denied": 0, "rejected": 0, "not_selected": 0}
        r = rows[k]
        r["files"] += 1
        r["bytes"] += it["size"]
        st = it["status"]
        key = {WOULD_COPY: "new", WOULD_OVERWRITE: "overwrite", SKIPPED_SAME: "same", DENIED: "denied", REJECTED: "rejected",
               NOT_SELECTED: "not_selected"}.get(st)
        if key:
            r[key] += 1
        if st == NOT_SELECTED:
            r["selected"] = False
    plan.programs = [rows[k] for k in order]
    ids = list(rows)
    plan.unknown_programs = [t for t in only + skip if not any(program_matches(t, k) for k in ids)]


def _plan_import(path: Path, rules: list[Rule], expand: Expand, only: list[str] | None = None,
                 skip: list[str] | None = None) -> ImportPlan:
    only, skip = only or [], skip or []
    return _plan_import_inner(path, rules, expand, only, skip)


def _plan_import_inner(path: Path, rules: list[Rule], expand: Expand, only: list[str], skip: list[str]) -> ImportPlan:
    if is_unfinished_part(path):  # 만드는 중/중단된 임시 파일: 완성본이 아니다
        raise PackageError("이 파일(.part)은 아직 완성되지 않은 임시 파일입니다. 내보내기를 같은 저장 위치로 다시 실행해 "
                           "끝내거나(이어서 합니다), 완성된 .zip 파일을 선택하세요.")
    manifest, problems = verify_package(path)
    plan = ImportPlan(manifest or {}, problems=list(problems))
    if manifest is None or problems:
        return plan
    by_id = {r.id: r for r in rules}
    seen_targets: set[str] = set()
    allowed = {rid: {split_rule_path(p.path)[0] for p in r.paths if split_rule_path(p.path)}
               for rid, r in by_id.items()}
    for f in manifest["files"]:
        rid, base = f["rule"], f["base"]
        is_extra = rid == EXTRA_ID
        if is_extra:
            if base not in EXTRA_ROOTS:
                plan.problems.append(f"허용되지 않은 복원 루트(extra): {base}")
                continue
            if base == "%USERPROFILE%" and not f["rel"].startswith("."):
                plan.problems.append(f"홈 루트는 점으로 시작하는 항목만 복원할 수 있음: {f['rel']}")
                continue
        elif rid not in by_id:
            plan.problems.append(f"알 수 없는 규칙 id: {rid}")
            continue
        elif base not in allowed[rid]:
            plan.problems.append(f"규칙 {rid} 에 허용되지 않은 복원 위치: {base}")
            continue
        elif not rel_matches_rule(by_id[rid], base, f["rel"]):
            plan.problems.append(f"규칙 {rid} 에 선언된 경로 패턴 밖의 파일(복원 거부): {f['rel']}")
            continue
        base_path = Path(expand(base))
        if not _usable_base(base_path):
            plan.problems.append(f"복원 위치의 환경변수를 확장할 수 없거나 상대 경로임: {base}")
            continue
        target = base_path.joinpath(*PurePosixPath(f["rel"]).parts)
        pkey = program_key(f)
        selected = (not only or any(program_matches(t, pkey) for t in only)) and not any(program_matches(t, pkey) for t in skip)
        link = fs.reparse_in_chain(base_path, target)  # 복원 위치가 링크/정션을 거치면 링크 너머에 쓰게 되므로 따라가지 않는다(F-5)
        if link:
            plan.items.append(_rejected_item(f, target, link, is_extra, base_path) | {"program": pkey} if selected
                              else _not_selected_item(f, target, is_extra, base_path, pkey))
            continue
        if not _within(target, base_path):
            plan.problems.append(f"복원 대상이 허용 루트 밖: {target}")
            continue
        if LOCK_NAMES and _excluded(target.name, None, LOCK_NAMES):
            continue  # 이전 버전이 만든 패키지에 든 잠금 파일: 복원하지 않고 조용히 건너뜀
        if _excluded(target.name, None if is_extra else by_id[rid], RESTORE_EXCLUDE):
            plan.problems.append(f"복원 제외 대상(라이선스 등): {f['rel']}")
            continue
        key = os.path.normcase(str(target))
        if key in seen_targets:  # 대소문자만 다른 경로 등 같은 파일로 가는 두 항목: 뒤 항목이 앞 항목을 .bak 으로 밀어낸다(B-6)
            plan.problems.append(f"같은 대상으로 복원되는 중복 항목: {target}")
            continue
        seen_targets.add(key)
        if not selected:  # 선택에서 뺀 프로그램: 대상 파일은 읽지도 않는다
            plan.items.append(_not_selected_item(f, target, is_extra, base_path, pkey))
            continue
        why = restore_denied(f["rel"], target, None if is_extra else by_id[rid], base)
        if why:  # 패키지 전체를 막지 않고 이 파일만 '복원 안 함'(쓰지 않는다). 나머지는 정상 복원(R1-1)
            plan.items.append(_denied_item(f, target, why, is_extra, base_path) | {"program": pkey})
            continue
        backup = ""
        if target.is_file() and target.stat().st_size == f["size"] and sha256_file(target) == f["sha256"]:
            status = SKIPPED_SAME
        elif target.exists():
            status = WOULD_OVERWRITE
            backup = str(_backup_path(target))
        else:
            status = WOULD_COPY
        plan.items.append({"entry": f["arc"], "target": str(target), "status": status,
                           "backup": backup, "size": f["size"], "sha256": f["sha256"],
                           "extra": is_extra, "base_dir": str(base_path), "program": pkey})
    _fill_programs(plan, by_id, only, skip)
    return plan


def apply_import(path: Path, plan: ImportPlan,
                 progress_info: Callable[[ProgressInfo], None] | None = None) -> Report:
    """검증을 통과한 계획을 실행한다. 덮어쓸 파일은 .bak 백업, 복사 후 해시 재검증.

    progress_info 가 있으면 파일 수/현재 파일/분류별 개수를 알린다(GUI 현황 카드용, CLI 출력과 무관).
    """
    if plan.problems:
        raise PackageError("검증 실패 상태에서는 복원할 수 없습니다")
    report = Report(dry_run=False)
    if plan.git_config_warning:  # 복원은 허용하되 내용을 확인하라고 알린다(GUI 는 이 키를 읽는다)
        report.meta.update(git_config_warning=plan.git_config_warning, git_config_files=git_config_targets(plan.items))
    tracker = ProgressTracker("restore", sum(i["size"] for i in plan.items), len(plan.items))
    last = [0.0]

    def emit(force: bool = False) -> None:
        if progress_info is not None and (force or time.monotonic() - last[0] >= 0.5):
            last[0] = time.monotonic()
            progress_info(tracker.snapshot())

    def done(res: FileResult) -> None:
        report.results.append(res)
        tracker.add(res.size, 1)
        tracker.note(categorize(res.status, res.reason), res.dst or res.src)
        emit()

    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory() as tmp:
        for it in plan.items:
            tracker.set_current(it["target"])
            if it["status"] == SKIPPED_SAME:
                done(FileResult(it["entry"], it["target"], SKIPPED_SAME, it["size"], it["sha256"]))
                continue
            target = Path(it["target"])
            if it["status"] == NOT_SELECTED:  # 선택에서 제외된 프로그램: 패키지에서 꺼내지도, 대상에 쓰지도 않는다
                done(FileResult(it["entry"], it["target"], NOT_SELECTED, it["size"], it["sha256"], error=NOT_SELECTED_TEXT,
                                reason=NOT_SELECTED))
                continue
            if it["status"] == DENIED:  # 거부 항목은 패키지에서 꺼내지도, 대상에 쓰지도 않는다
                done(FileResult(it["entry"], it["target"], DENIED, it["size"], it["sha256"], error=it["error"], reason=DENIED))
                continue
            link = it["error"] if it["status"] == REJECTED else ""
            if not link and it.get("base_dir"):  # 계획 이후에 링크가 생겼을 수도 있으니 쓰기 직전에 다시 확인한다
                found = fs.reparse_in_chain(it["base_dir"], target)
                link = f"{failure.REASONS[failure.LINK_IN_PATH].text} ({found})" if found else ""
            if link:
                done(FileResult(it["entry"], it["target"], REJECTED, it["size"], it["sha256"], error=link,
                                reason=failure.LINK_IN_PATH))
                continue
            staged = Path(tmp) / "entry"
            try:
                bad = _stage_entry(zf, it, staged, tmp)
                if bad is not None:  # 해시/크기가 다르면 대상은 건드리지 않는다(B-4)
                    res = bad
                else:
                    res = copy_file(staged, target)
                    if res.status != FAILED and res.sha256 != it["sha256"]:
                        res.status, res.error = FAILED, "manifest 해시와 불일치"
                    res.src = it["entry"]
            except OSError as e:
                res = FileResult(it["entry"], it["target"], FAILED, error=f"{type(e).__name__}: {e}",
                                 reason=failure.classify(e, it["target"]).code)
            except (KeyError, zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError) as e:
                res = FileResult(it["entry"], it["target"], FAILED, error=f"패키지에서 읽을 수 없음({type(e).__name__}): manifest 해시와 불일치할 수 있음",
                                 reason=failure.OTHER)
            done(res)
            staged.unlink(missing_ok=True)
    emit(True)
    return report


def _stage_entry(zf: zipfile.ZipFile, it: dict, staged: Path, tmp: str) -> FileResult | None:
    """zip 엔트리를 임시 파일로 풀면서 크기/해시를 계산한다. 선언과 다르면 실패 결과를, 맞으면 None 을 돌려준다.

    선언 크기보다 많이 나오면 거기서 멈춘다. 대상 파일을 바꾸기 전에 확인하므로 검사~적용 사이에 zip 이 바뀌어도 잘못된 파일이 설치되지 않는다.
    """
    size = it["size"]
    free = shutil.disk_usage(tmp).free
    if free < size:
        f = failure.REASONS[failure.DISK_FULL]
        return FileResult(it["entry"], it["target"], FAILED, error=f"{f.text} (임시 폴더 여유 {free}바이트 < 필요 {size}바이트)", reason=f.code)
    h = hashlib.sha256()
    n = 0
    with zf.open(it["entry"]) as fin, open(staged, "wb") as fout:
        while chunk := fin.read(CHUNK):
            n += len(chunk)
            if n > size:
                break
            h.update(chunk)
            fout.write(chunk)
    if n != size or h.hexdigest() != it["sha256"]:
        staged.unlink(missing_ok=True)
        return FileResult(it["entry"], it["target"], FAILED, error="manifest 해시와 불일치(패키지가 검사 뒤에 바뀌었을 수 있음, 대상은 바꾸지 않음)",
                          reason=failure.MISMATCH)
    return None


# --------------------------------------------------------------------------- winget-extract

WINGET_ENTRY = "programs/winget.json"
MAX_WINGET_BYTES = 64 * 1024 * 1024


def extract_winget(path: Path, out_dir: Path) -> tuple[Path, Path | None]:
    """패키지(.zip)의 programs/winget.json 을 <out_dir>/winget.json 으로 꺼낸다(신 PC 에서 winget import 에 쓰기 위해).

    패키지를 먼저 검증한다. 같은 이름의 파일이 이미 있으면 덮어쓰지 않고 .bak 으로 옮긴 뒤 새로 쓴다.
    폴더가 같은 이름 자리에 있거나 출력 위치가 파일이면 아무것도 바꾸지 않고 PackageError.
    반환: (새 파일 경로, .bak 으로 옮긴 이전 파일 경로 또는 None)."""
    path, out_dir = Path(path), Path(out_dir)
    if is_unfinished_part(path):
        raise PackageError("이 파일(.part)은 아직 완성되지 않은 임시 파일입니다. 완성된 .zip 파일을 선택하세요.")
    manifest, problems = verify_package(path)
    if manifest is None or problems:
        raise PackageError("패키지 검증 실패: " + "; ".join(problems[:3]))
    with zipfile.ZipFile(path) as zf:
        try:
            info = zf.getinfo(WINGET_ENTRY)
        except KeyError:
            raise PackageError("이 패키지에는 winget 목록이 없습니다(구 PC 에서 winget export 가 실패했거나 winget 이 없었습니다). "
                               "pcmigrate checklist --package 로 수동 설치 목록을 확인하세요.") from None
        if info.file_size > MAX_WINGET_BYTES:
            raise PackageError("패키지의 winget 목록이 비정상적으로 큽니다. 안전하지 않은 패키지일 수 있습니다.")
        raw = zf.read(WINGET_ENTRY)
    try:
        json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        raise PackageError("패키지의 winget 목록이 올바른 JSON 이 아닙니다. 쓰지 않았습니다.") from None
    if out_dir.exists() and not out_dir.is_dir():
        raise PackageError(f"출력 위치가 폴더가 아닙니다: {out_dir}. 폴더 경로를 지정하세요(없으면 만들어 줍니다).")
    target = out_dir / "winget.json"
    if target.is_dir():
        raise PackageError(f"같은 이름의 폴더가 있어 쓰지 않았습니다: {target}")
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".pcmigrate-part")
    tmp.write_bytes(raw)
    bak: Path | None = None
    try:
        if target.exists():
            bak = _backup_path(target)
            os.replace(target, bak)
        try:
            os.replace(tmp, target)
        except OSError:
            if bak is not None:
                os.replace(bak, target)  # 되돌린다
            raise
    finally:
        tmp.unlink(missing_ok=True)
    return target, bak


# --------------------------------------------------------------------------- 읽지 못해 제외된 파일만 다시 담기

RETRY_SUFFIX = ".retry"  # 다시 담는 동안의 임시 묶음(검사를 통과해야만 최종 이름으로 바뀐다)


def retry_paths(out: Path | str) -> Path:
    out = Path(out)
    return out.with_name(out.name + RETRY_SUFFIX)


@dataclass
class _ExtraRef:
    """manifest['extras'] 에서 되살린 직접 고른 항목(collect_extra_items 가 쓰는 root/rel/path 만 가진다)."""
    root: str
    rel: str
    path: Path
    is_dir: bool = False


def _norm_src(p: Path | str) -> str:
    return os.path.normcase(os.path.abspath(fs.plain_path_text(str(p))))


def _extra_refs(manifest: dict, expand: Expand) -> list[_ExtraRef]:
    """manifest 에 적힌 extras 를 다시 만든다. 허용 루트/안전한 이름만 받고 이상한 것은 건너뛴다(묶음 파일은 신뢰하지 않는다)."""
    refs: list[_ExtraRef] = []
    raw = manifest.get("extras")
    if not isinstance(raw, list):
        return refs
    for e in raw:
        if not isinstance(e, dict):
            continue
        root, rel = e.get("root"), e.get("rel")
        if root not in EXTRA_ROOTS or not isinstance(rel, str):
            continue
        if not rel or any(c in rel for c in "\\/:") or rel in (".", "..") or rel.strip() != rel or unsafe_name(rel):
            continue
        base = Path(expand(root))
        if "%" in str(base) or not base.is_absolute():
            continue
        refs.append(_ExtraRef(root, rel, base / rel))
    return refs


def _copy_raw_entry(old: zipfile.ZipFile, zi: zipfile.ZipInfo, fobj, zf: zipfile.ZipFile,
                    on_bytes: Callable[[int], None]) -> None:
    """기존 묶음의 엔트리 하나를 재압축 없이 그대로(로컬 헤더+압축된 데이터) 새 묶음에 옮긴다. 데이터 설명자가 있는 특이한 엔트리만 다시 쓴다."""
    fp = old.fp
    fp.seek(zi.header_offset)
    hdr = fp.read(30)
    if len(hdr) != 30 or hdr[:4] != b"PK\x03\x04" or zi.flag_bits & 0x08:
        info = zipfile.ZipInfo(zi.filename, date_time=zi.date_time)
        info.compress_type = zi.compress_type
        with old.open(zi) as src, zf.open(info, "w", force_zip64=zi.file_size > ZIP64_THRESHOLD) as dst:
            while chunk := src.read(CHUNK):
                dst.write(chunk)
                on_bytes(len(chunk))
        return
    n, m = int.from_bytes(hdr[26:28], "little"), int.from_bytes(hdr[28:30], "little")
    left = n + m + zi.compress_size
    fobj.seek(zf.start_dir)
    new = zipfile.ZipInfo(zi.filename, date_time=zi.date_time)
    for a in ("compress_type", "comment", "extra", "create_system", "create_version", "extract_version", "flag_bits",
              "internal_attr", "external_attr", "CRC", "compress_size", "file_size"):
        setattr(new, a, getattr(zi, a))
    new.header_offset = fobj.tell()
    fobj.write(hdr)
    while left > 0:
        chunk = fp.read(min(CHUNK, left))
        if not chunk:
            raise PackageError(f"기존 묶음을 끝까지 읽지 못했습니다(손상): {zi.filename}")
        fobj.write(chunk)
        left -= len(chunk)
        on_bytes(len(chunk))
    zf.filelist.append(new)
    zf.NameToInfo[new.filename] = new
    zf.start_dir = fobj.tell()


def retry_failed_export(package_path: Path | str, rules: list[Rule], expand: Expand = expand_env, *,
                        progress_info: Callable[[ProgressInfo], None] | None = None,
                        notice: Callable[[str], None] | None = None,
                        dest_info_fn: Callable[[str], fs.DestInfo] | None = None,
                        stage_dir: Path | str | None = None,
                        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """완성된 설정 묶음의 manifest['failed'](읽지 못해 제외된 파일)만 현재 원본에서 다시 읽어 묶음에 추가한다.

    - 원본은 읽기만 한다. 담을 대상은 묶음에 기록된 규칙(rules 중 같은 id)과 직접 고른 항목(extras)으로 다시 찾아 정하므로,
      규칙 밖이거나 보안상 담지 않는 파일(restore_denied 등)은 다시 담지 않는다. 처음 내보낸 뒤 원본이 바뀌었어도 '지금' 상태를 담는다.
    - 기존 묶음 내용은 재압축 없이 '<묶음>.retry' 에 그대로 옮기고 추가분을 담은 뒤, verify_package 를 통과해야만 교체한다.
      기존 묶음은 .bak 으로 보존한다. 어떤 실패/취소에도 기존 묶음은 바뀌지 않는다.
    - 출력 락(<묶음>.part.lock)을 잡아 같은 이름의 내보내기/다시 담기와 동시에 실행되지 않게 한다. 미완성 .part 는 거부한다.
    반환: 새 manifest(+ 저장되지 않는 'retry' 항목: added/still_failed/gone/not_retried/missing_rules/unchanged/backup).
    """
    out = Path(package_path)
    if is_unfinished_part(out):
        raise PackageError("끝나지 않은 임시 파일(.part)입니다. 같은 이름으로 설정 묶음 내보내기를 다시 실행해 먼저 완성하세요.")
    if out.is_dir() or not out.is_file():
        raise PackageError(f"설정 묶음 파일을 찾을 수 없습니다: {out}")
    lock = _OutputLock(out)
    lock.acquire()
    try:
        return _retry_once(out, rules, expand, progress_info, notice, dest_info_fn, stage_dir, now)
    finally:
        lock.release()


def _retry_once(out: Path, rules: list[Rule], expand: Expand, progress_info, notice, dest_info_fn, stage_dir, now) -> dict:
    manifest, problems = verify_package(out)
    if manifest is None or problems:
        raise PackageError("설정 묶음 파일 검사에 실패해 다시 담지 않았습니다(파일은 그대로입니다). 문제: "
                           + "; ".join(problems[:3] or ["알 수 없음"]))
    old_failed = [f for f in manifest.get("failed", []) if isinstance(f, dict)]
    unreadable = [f for f in old_failed if f.get("reason") and isinstance(f.get("src"), str)]
    info: dict = {"added": [], "still_failed": [], "gone": [], "not_retried": [], "missing_rules": [], "unchanged": True,
                  "backup": "", "total": len(unreadable)}
    tmp = retry_paths(out)
    tmp.unlink(missing_ok=True)  # 락을 잡고 있으므로 남은 것은 이전 실행의 찌꺼기다
    if not unreadable:
        manifest["retry"] = info
        return manifest
    if progress_info:
        progress_info(ProgressInfo("find", 0, 0, 0, 0, message="다시 담을 파일을 원본에서 찾는 중입니다…"))

    mine = {_norm_src(p) for p in (out, tmp, out.with_name(out.name + LOCK_SUFFIX), *part_paths(out),
                                    out.with_name(out.name + FAILED_SUFFIX))}
    out_prefix = _norm_src(out)

    def avoid(p: Path) -> bool:
        q = _norm_src(p)
        return q in mine or q.startswith(out_prefix + ".bak")

    existing = {f["arc"] for f in manifest["files"]}
    by_id = {r.id: r for r in rules}
    scan_failed: list[dict] = []
    roots: list[Path] = []
    items: list[Item] = []
    for r in manifest.get("rules", []):
        rule = by_id.get(r.get("id"))
        if rule is None:
            if r.get("id") != EXTRA_ID:
                info["missing_rules"].append(str(r.get("id")))
            continue
        its, _ = collect_items(rule, expand, avoid=avoid, roots=roots, failed=scan_failed)
        items += its
    refs = _extra_refs(manifest, expand)
    if refs:
        saved = manifest.get("extra_mode")  # 이전 버전 묶음(기록 없음)은 예전 방식(전체)으로 담았다
        lim_d = manifest.get("extra_limits") if isinstance(manifest.get("extra_limits"), dict) else {}
        try:
            lim = settings_filter.ConfigLimits(**{k: int(lim_d[k]) for k in ("file_bytes", "folder_bytes", "folder_files")}) if lim_d else None
        except (KeyError, TypeError, ValueError):
            lim = None
        its, _ = collect_extra_items(refs, expand, avoid=avoid, roots=roots, failed=scan_failed,
                                     extra_mode=saved if saved in settings_filter.EXTRA_MODES else settings_filter.EXTRA_MODE_ALL,
                                     limits=lim)
        items += its

    failed_norm = {_norm_src(f["src"]): f for f in unreadable}
    item_norm = {_norm_src(it.src) for it in items}
    scan_norm = {_norm_src(f["src"]): f for f in scan_failed if isinstance(f.get("src"), str)}
    folders = {n for n in failed_norm if n not in item_norm}  # 폴더를 읽지 못해 목록에서 빠진 것들(그 아래 파일이 후보)
    candidates: list[Item] = []
    seen_arc: set[str] = set()
    for it in items:
        if it.arc in existing or it.arc in seen_arc:
            continue
        if _norm_src(it.src) in failed_norm or any(_norm_src(p) in folders for p in it.src.parents):
            candidates.append(it)
            seen_arc.add(it.arc)
    cand_norm = {_norm_src(it.src) for it in candidates}
    keep_failed: list[dict] = []  # 이번에도 담지 못했거나 다시 담지 않는 기존 항목(순서 유지)
    for f in unreadable:
        n = _norm_src(f["src"])
        if n in cand_norm:
            continue  # 다시 시도한다(결과는 아래에서 정함)
        if n in item_norm:  # 이미 같은 패키지 경로가 묶음에 있어 담을 수 없다
            keep_failed.append(f)
            info["not_retried"].append({**f, "note": "같은 경로의 파일이 이미 묶음에 있어 다시 담지 않았습니다"})
        elif n in scan_norm:
            keep_failed.append(scan_norm[n])
            info["still_failed"].append(scan_norm[n])
        elif not os.path.lexists(fs.long_path(f["src"])):
            info["gone"].append(f["src"])  # 원본이 없어졌다: 담을 것이 없다
        elif os.path.isdir(fs.long_path(f["src"])):
            pass  # 폴더를 이제 읽을 수 있다: 그 아래 파일은 후보로 처리됐다
        else:
            keep_failed.append(f)
            info["not_retried"].append({**f, "note": "규칙 밖이거나 보안상 담지 않는 파일이라 다시 담지 않았습니다"})

    if not candidates and len(keep_failed) == len(unreadable):  # 담을 것도, 목록에서 뺄 것도 없다: 묶음을 건드리지 않는다
        manifest["retry"] = info
        return manifest

    stage_parent = Path(stage_dir) if stage_dir else out.parent
    for r in roots:
        if _within(stage_parent, r) and Path(os.path.realpath(r)).is_dir():
            raise PackageError(f"임시 폴더(--stage-dir)가 백업할 폴더 안에 있습니다: {stage_parent} (폴더: {r}). 백업 대상 밖을 지정하세요.")
    total_new = biggest = 0
    for it in candidates:
        try:
            sz = os.stat(fs.long_path(it.src)).st_size
        except OSError:
            continue
        total_new += sz
        biggest = max(biggest, sz)
    old_size = out.stat().st_size
    _check_export_space(out, old_size + total_new, biggest, "never", dest_info_fn, notice,
                        stage_parent=stage_parent if stage_dir else None)

    stage = os.path.join(os.path.abspath(stage_parent), f"{STAGE_PREFIX}{os.getpid()}-{os.urandom(4).hex()}")
    stage_made = False
    new_files: list[dict] = []
    zf: _PartZip | None = None
    nochange = False
    try:
        try:
            if stage_dir:
                stage_parent.mkdir(parents=True, exist_ok=True)
            os.mkdir(stage, 0o700)
        except OSError as e:
            raise PackageError(f"임시 폴더를 만들 수 없습니다: {stage_parent} ({e.strerror or e})") from e
        stage_made = True
        tracker = ProgressTracker("pack", old_size + total_new, len(candidates))
        last = [0.0]

        def emit(force: bool = False) -> None:
            if progress_info is None:
                return
            t = time.monotonic()
            if force or t - last[0] >= 0.5:
                last[0] = t
                progress_info(tracker.snapshot())

        cmode = manifest.get("compress") if manifest.get("compress") in COMPRESS_MODES else "auto"
        with zipfile.ZipFile(out) as old, open(tmp, "wb") as fobj:
            zf = _PartZip(fobj, "w", allowZip64=True)
            zf.compress = cmode
            for it in candidates:
                tracker.set_current(str(it.src))
                zf.last_meta = None
                try:
                    size, digest = _write_entry(zf, it.arc, it.src, stage)
                except SourceUnreadable as e:
                    entry = unreadable_entry(e)
                    keep_failed.append(entry)
                    info["still_failed"].append(entry)
                    tracker.note(UNREADABLE if e.failure.code == failure.IN_USE else FAILED_KIND, str(it.src))
                    tracker.add(0, 1)
                    emit()
                    continue
                new_files.append({"arc": it.arc, "size": size, "sha256": digest, "rule": it.rule_id, "base": it.base,
                                  "rel": it.rel, "method": METHOD_NAMES[zf.getinfo(it.arc).compress_type]})
                info["added"].append({"src": str(it.src), "arc": it.arc, "size": size})
                tracker.note(OK)
                tracker.add(size, 1)
                emit()
            if not new_files and len(keep_failed) == len(unreadable):
                nochange = True  # 하나도 못 담았고 목록에서 뺄 것도 없다: 기존 묶음을 옮겨 적지 않고 그대로 둔다
            else:
                for zi in old.infolist():
                    if zi.filename == MANIFEST:
                        continue
                    _copy_raw_entry(old, zi, fobj, zf, lambda n: (tracker.add(n, 0), emit()))
                new_manifest = dict(manifest)
                new_manifest["files"] = list(manifest["files"]) + new_files
                new_manifest["failed"] = [f for f in old_failed if not f.get("reason")] + keep_failed
                prev = manifest.get("retries") if isinstance(manifest.get("retries"), list) else []
                new_manifest["retries"] = list(prev) + [
                    {"at": now().isoformat(timespec="seconds"), "tool_version": __version__, "added": len(new_files),
                     "still_failed": len(info["still_failed"]), "gone": len(info["gone"])}]
                zf.writestr(MANIFEST, json.dumps(new_manifest, ensure_ascii=False, indent=2))
                zf.close()
                zf = None
                fobj.flush()
                os.fsync(fobj.fileno())
        if nochange:
            manifest["retry"] = info
            return manifest
        if progress_info:
            progress_info(ProgressInfo("check", old_size + total_new, old_size + total_new, len(candidates), len(candidates),
                                       message="새로 만든 묶음을 다시 읽어 검사하는 중입니다…", ok=tracker.counts[OK],
                                       unreadable=tracker.counts[UNREADABLE], failed=tracker.counts[FAILED_KIND]))
        _, problems = verify_package(tmp)
        if problems:
            raise PackageError("다시 담은 묶음이 검사를 통과하지 못해 교체하지 않았습니다(기존 묶음은 그대로입니다). 문제: "
                               + "; ".join(problems[:3]))
        bak = _backup_path(out)
        os.replace(out, bak)
        try:
            os.replace(tmp, out)
        except OSError:
            os.replace(bak, out)  # 교체에 실패하면 기존 묶음을 제자리로 되돌린다
            raise
        info["backup"] = str(bak)
    finally:
        if zf is not None:
            zf.fp = None  # 중단된 임시 묶음에 중앙 디렉터리를 쓰려고 하지 않게 한다
        tmp.unlink(missing_ok=True)
        if stage_made:
            shutil.rmtree(stage, ignore_errors=True)
    info["unchanged"] = False
    new_manifest["retry"] = info
    return new_manifest
