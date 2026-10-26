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
from .copier import (COPIED, FAILED, REJECTED, SKIPPED_SAME, WOULD_COPY, WOULD_OVERWRITE,
                     FileResult, Report, _backup_path, copy_file, sha256_file)
from .detect import Program
from .discover_data import EXTRA_ROOTS, RESTORE_DENY_SUBSTRINGS, ROOTS
from .progress import FAILED_KIND, OK, SKIPPED, UNREADABLE, ProgressInfo, ProgressTracker, categorize, fmt_bytes
from .rules import Rule

FORMAT_VERSION = 1
EXTRA_ID = "extra"  # 규칙 없는 프로그램 중 사용자가 선택해 포함한 항목(manifest rule id)
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
                             ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".url", ".reg", ".msi", ".msp", ".cpl"})
# 셸/SSH 가 시작할 때 읽어 실행하는 설정(소문자 파일 이름 / 폴더 이름)
DENY_FILE_NAMES = frozenset({".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile", ".zshrc", ".zprofile",
                             ".zshenv", ".zlogin", ".cshrc", ".kshrc", "authorized_keys"})
# .ssh 아래에서는 개인키가 아니라 접속 때 명령을 실행시킬 수 있는 파일(ProxyCommand 등)만 막는다
SSH_DENY_FILES = frozenset({"config", "rc", "environment", "authorized_keys2"})
_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})
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
    return part.split(".", 1)[0].rstrip(" ").upper() in _DEVICE_NAMES


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


def _walk_files_no_links(top: Path, files: list[Path], skipped: list[dict], rule_id: str) -> None:
    """top 아래 파일을 모은다. 정션/심볼릭 링크 폴더(reparse point)는 들어가지 않고 건너뛴 기록을 남긴다."""
    for root, dirs, names in os.walk(top, followlinks=False):
        dirs.sort()
        keep = []
        for d in dirs:
            dp = Path(root) / d
            if fs.is_reparse(dp):
                skipped.append({"rule": rule_id, "path": str(dp), "reason": "심볼릭 링크/정션은 따라가지 않음"})
            else:
                keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            files.append(Path(root) / n)


def collect_items(rule: Rule, expand: Expand = expand_env
                  ) -> tuple[list[Item], list[dict]]:
    """규칙에 따라 백업할 파일 목록과 건너뛴 항목(사유 포함)을 만든다. 읽기 전용."""
    items: list[Item] = []
    skipped: list[dict] = []
    for pr in rule.paths:
        split = split_rule_path(pr.path)
        if split is None:
            skipped.append({"rule": rule.id, "path": pr.path, "reason": "경로 형식이 올바르지 않음"})
            continue
        base, pattern = split
        base_path = Path(expand(base))
        if not _usable_base(base_path):
            skipped.append({"rule": rule.id, "path": pr.path, "reason": "환경변수 확장 실패 또는 상대 경로"})
            continue
        has_glob = bool(WILDCARD & set(pr.path))
        matches = sorted(base_path.glob(pattern)) if base_path.is_dir() else []
        if not matches:
            skipped.append({"rule": rule.id, "path": pr.path, "reason": "경로가 없음"})
            continue
        for m in matches:
            link = fs.reparse_in_chain(base_path, m)  # 일치한 경로 자신과 그 위쪽 폴더(base 아래)가 링크/정션인지
            if link:
                skipped.append({"rule": rule.id, "path": link, "reason": "심볼릭 링크/정션은 따라가지 않음"})
                continue
            files: list[Path] = []
            if m.is_file():
                files = [m]
            else:
                _walk_files_no_links(m, files, skipped, rule.id)
            for f in files:
                if f.is_symlink() or _excluded(f.name, rule):
                    skipped.append({"rule": rule.id, "path": str(f),
                                    "reason": "제외 대상(라이선스/임시/링크)"})
                    continue
                rel = f.relative_to(base_path).as_posix()
                if unsafe_name(rel):
                    skipped.append({"rule": rule.id, "path": str(f), "reason": "Windows 에서 쓸 수 없는 이름(예약 이름, 끝의 점/공백 등)"})
                    continue
                if pr.kind == "file" and not has_glob:
                    arc = f"data/{pr.dest}"
                else:
                    arc = f"data/{pr.dest}/{rel}"
                items.append(Item(arc, f, rule.id, base, rel))
    return items, skipped


def collect_extra_items(extras: list, expand: Expand = expand_env) -> tuple[list[Item], list[dict]]:
    """discover 선택 파일로 고른 항목(ExtraItem)의 파일 목록. 읽기 전용, 라이선스/임시/링크 제외."""
    keys = {r.template: r.key for r in ROOTS}
    items: list[Item] = []
    skipped: list[dict] = []
    for ex in extras:
        top = Path(ex.path)
        files: list[Path] = []
        if top.is_symlink() or (top.is_dir() and fs.is_reparse(top)):
            skipped.append({"rule": EXTRA_ID, "path": str(top), "reason": "심볼릭 링크/정션은 따라가지 않음"})
            continue
        if top.is_file():
            files = [top]
        else:
            _walk_files_no_links(top, files, skipped, EXTRA_ID)
        for f in files:
            if f.is_symlink() or _excluded(f.name, None):
                skipped.append({"rule": EXTRA_ID, "path": str(f), "reason": "제외 대상(라이선스/임시/링크)"})
                continue
            rel = ex.rel if f == top else f"{ex.rel}/{f.relative_to(top).as_posix()}"
            if unsafe_name(rel):
                skipped.append({"rule": EXTRA_ID, "path": str(f), "reason": "Windows 에서 쓸 수 없는 이름(예약 이름, 끝의 점/공백 등)"})
                continue
            items.append(Item(f"data/{EXTRA_ID}/{keys[ex.root]}/{rel}", f, EXTRA_ID, ex.root, rel))
    return items, skipped


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
        st = src.stat()
        size = st.st_size
        mtime = _zip_time_st(st)
        fin = open(src, "rb")
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
    return _zip_time_st(path.stat())


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


def _fingerprint(rules: list[Rule], extras: list | None, compress: str) -> tuple[str, dict]:
    info = {"tool": __version__, "format": FORMAT_VERSION, "compress": compress,
            "rules": [[r.id, str(r.version)] for r in rules],
            "extras": sorted([e.root, e.rel] for e in (extras or []))}
    raw = json.dumps(info, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), info


def _fp_reason(old: dict, new: dict) -> str:
    if old.get("rules") is not None and [r[0] for r in old["rules"]] != [r[0] for r in new["rules"]]:
        return "선택한 프로그램(규칙)이 달라졌습니다"
    if old.get("rules") != new["rules"]:
        return "프로그램 규칙의 버전이 달라졌습니다"
    if old.get("extras") != new["extras"]:
        return "직접 고른 추가 항목이 달라졌습니다"
    if old.get("compress") != new["compress"]:
        return "압축 설정이 달라졌습니다"
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


def discard_partial(out: Path | str) -> None:
    """이 도구가 만든 임시물(.part, .part.journal, 정리 중 남은 .new)만 지운다. 완성본은 건드리지 않는다."""
    part, jpath = part_paths(Path(out))
    for p in (part, jpath, part.with_name(part.name + ".new"), jpath.with_name(jpath.name + ".new")):
        if p.is_file() or p.is_symlink():
            p.unlink()


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
        st = item.src.stat()
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
                   clock: Callable[[], float] = time.monotonic) -> dict:
    """감지된 프로그램의 규칙(rules)으로 설정 패키지를 만든다. manifest dict 반환.

    progress_info 가 있으면 파일 찾기 -> 담기 -> 검사 단계의 진행(파일 수, 현재 파일, 분류별 개수)을 알린다(GUI 현황 카드용).

    만드는 동안에는 최종 이름이 아니라 '<out>.part'(zip)와 '<out>.part.journal'(진행 기록)에 쓰고, 검사를 통과한 뒤에만
    최종 이름으로 바꾼다(out 이 이미 있으면 .bak 으로 보존). 저장점(기본 256MB 또는 60초마다)까지는 실패/종료 뒤에도 남아,
    같은 out 과 같은 설정으로 다시 내보내면(resume=True) 저장점까지는 다시 담지 않는다. 실패/취소 시 .part 와 journal 은
    남기고 성공하면 지운다. resume=False 면 이전 .part/journal 을 지우고 처음부터 만든다.
    compress: auto(이미 압축된 형식은 STORED) / always(모두 deflate) / never(모두 STORED).
    반환 manifest 에는 패키지에 저장되지 않는 "resume" 항목({resumed, kept_files, kept_bytes, dropped, reason})이 추가된다.
    파일 단위 실패는 manifest["failed"] 에 기록(읽지 못한 파일은 다음 시도에서 다시 읽는다).
    """
    if compress not in COMPRESS_MODES:
        raise ValueError(f"compress 는 {', '.join(COMPRESS_MODES)} 중 하나여야 합니다: {compress!r}")
    out = Path(out)
    if out.is_dir():  # 기존 폴더를 .bak 으로 옮기고 같은 이름의 파일을 만들게 되므로 시작하지 않는다
        raise IsADirectoryError(errno.EISDIR, "대상이 폴더입니다", str(out))
    args = (rules, out, programs, winget_text, expand, winget_list_text, extras, now, progress_info)
    kw = dict(compress=compress, savepoint_bytes=savepoint_bytes, savepoint_seconds=savepoint_seconds,
              notice=notice, clock=clock)
    try:
        return _export_once(*args, resume=resume, **kw)
    except _ResumeVerifyFailed:
        if notice:
            notice("이어받은 내용이 최종 검사를 통과하지 못해 처음부터 다시 만듭니다.")
        m = _export_once(*args, resume=False, **kw)
        m["resume"]["reason"] = "이어받은 내용이 최종 검사를 통과하지 못해 처음부터 다시 만들었습니다"
        return m


def _export_once(rules, out, programs, winget_text, expand, winget_list_text, extras, now, progress_info, *,
                 resume, compress, savepoint_bytes, savepoint_seconds, notice, clock) -> dict:
    part, jpath = part_paths(out)
    manifest: dict = {
        "format": FORMAT_VERSION,
        "tool_version": __version__,
        "created": now().isoformat(timespec="seconds"),
        "compress": compress,
        "rules": [], "files": [], "skipped": [], "failed": [],
        "programs": {"registry": "programs/registry.json",
                     "winget": "programs/winget.json" if winget_text else None,
                     "winget_list": "programs/winget-list.txt" if winget_list_text else None},
        "manual_warnings": [],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=out.parent)  # 큰 파일을 읽어 두는 전용 임시 폴더(항상 삭제)
    zf: _PartZip | None = None
    fobj = None
    journal: _Journal | None = None
    finished = False
    st = {"end": 0}  # 마지막으로 완전히 쓴 엔트리의 끝 위치(저장점 위치)
    try:
        if progress_info:
            progress_info(ProgressInfo("find", 0, 0, 0, 0, message="패키지에 담을 파일을 찾는 중입니다…"))
        groups: list[list[Item]] = []
        for rule in rules:  # 1) 파일 찾기(읽기 전용)
            items, skipped = collect_items(rule, expand)
            manifest["skipped"] += skipped
            manifest["rules"].append({"id": rule.id, "name": rule.name, "version": rule.version, "notes": rule.notes})
            manifest["manual_warnings"].append({"rule": rule.id, "name": rule.name, "items": list(rule.manual_warnings)})
            groups.append(items)
        if extras:
            items, skipped = collect_extra_items(extras, expand)
            manifest["skipped"] += skipped
            manifest["extras"] = [{"root": e.root, "rel": e.rel} for e in extras]
            manifest["manual_warnings"].append({
                "rule": EXTRA_ID, "name": "규칙 없는 프로그램(선택 포함)",
                "items": ["규칙 없이 사용자가 직접 골라 포함한 폴더입니다. 신 PC 에서 같은 버전의 프로그램을 설치한 뒤 "
                          "복원하고, 라이선스/로그인은 직접 처리하세요."]})
            groups.append(items)
        total_files = sum(len(g) for g in groups)
        total_bytes = 0
        if progress_info:
            for g in groups:
                for it in g:
                    try:
                        total_bytes += it.src.stat().st_size
                    except OSError:
                        pass
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

        fp, fp_info = _fingerprint(rules, extras, compress)
        head = {"t": "head", "v": JOURNAL_VERSION, "fp": fp, "info": fp_info}
        by_arc: dict[str, Item] = {}
        for g in groups:
            for it in g:
                by_arc.setdefault(it.arc, it)
        rs = _prepare_part(part, jpath, head, fp_info, by_arc, resume)
        kept_bytes = sum(e["size"] for e in rs.kept.values())
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
            discard_partial(out)  # 검사를 통과하지 못한 임시물은 다시 쓸 수 없다
            if resume_info["resumed"]:
                raise _ResumeVerifyFailed()
            raise PackageError("만든 패키지를 다시 검사했더니 문제가 있어 저장하지 않았습니다(원본은 그대로입니다). "
                               "문제: " + "; ".join(problems[:3]))
        if out.exists():
            os.replace(out, _backup_path(out))
        os.replace(part, out)
        finished = True
        jpath.unlink(missing_ok=True)
        manifest["resume"] = resume_info
    except BaseException:
        if not finished:
            _abandon(zf, fobj, journal, st["end"])
        raise
    finally:
        if journal is not None:
            journal.close()
        shutil.rmtree(stage, ignore_errors=True)
    return manifest


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
    if parts[-1] in DENY_FILE_NAMES or (len(parts) >= 2 and parts[-2] == ".ssh" and parts[-1] in SSH_DENY_FILES):
        return "복원이 금지된 설정 파일(쉘 시작 파일/SSH 설정)"
    ext = os.path.splitext(parts[-1])[1]
    if ext in DENY_EXTENSIONS and not _rule_names_extension(rule, base, ext):
        return f"실행/스크립트 파일({ext})은 복원하지 않음(PowerShell 프로필 등)"
    return None


@dataclass
class ImportPlan:
    manifest: dict
    items: list[dict] = field(default_factory=list)  # {entry, target, status, backup}
    problems: list[str] = field(default_factory=list)

    @property
    def warnings(self) -> list[dict]:
        return self.manifest.get("manual_warnings", [])


def plan_import(path: Path, rules: list[Rule], expand: Expand = expand_env) -> ImportPlan:
    """검증 후 복원 계획을 만든다. 파일 시스템은 읽기만 한다.

    검증 단계가 놓친 구조 오류로 예외가 나더라도 traceback 대신 PackageError('패키지 검증 실패: …')로 알린다.
    """
    try:
        return _plan_import(path, rules, expand)
    except PackageError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError) as e:
        raise PackageError(f"패키지 검증 실패: manifest 구조가 올바르지 않습니다({type(e).__name__})") from e


def _rejected_item(f: dict, target: Path, link: str, is_extra: bool, base_path: Path) -> dict:
    return {"entry": f["arc"], "target": str(target), "status": REJECTED, "backup": "", "size": f["size"],
            "base_dir": str(base_path),
            "sha256": f["sha256"], "extra": is_extra, "reason": failure.LINK_IN_PATH,
            "error": f"{failure.REASONS[failure.LINK_IN_PATH].text} ({link})"}


def _plan_import(path: Path, rules: list[Rule], expand: Expand) -> ImportPlan:
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
        link = fs.reparse_in_chain(base_path, target)  # 복원 위치가 링크/정션을 거치면 링크 너머에 쓰게 되므로 따라가지 않는다(F-5)
        if link:
            plan.items.append(_rejected_item(f, target, link, is_extra, base_path))
            continue
        if not _within(target, base_path):
            plan.problems.append(f"복원 대상이 허용 루트 밖: {target}")
            continue
        if LOCK_NAMES and _excluded(target.name, None, LOCK_NAMES):
            continue  # 이전 버전이 만든 패키지에 든 잠금 파일: 복원하지 않고 조용히 건너뜀
        if _excluded(target.name, None if is_extra else by_id[rid], RESTORE_EXCLUDE):
            plan.problems.append(f"복원 제외 대상(라이선스 등): {f['rel']}")
            continue
        why = restore_denied(f["rel"], target, None if is_extra else by_id[rid], base)
        if why:
            plan.problems.append(f"{why}: {target}")
            continue
        key = os.path.normcase(str(target))
        if key in seen_targets:  # 대소문자만 다른 경로 등 같은 파일로 가는 두 항목: 뒤 항목이 앞 항목을 .bak 으로 밀어낸다(B-6)
            plan.problems.append(f"같은 대상으로 복원되는 중복 항목: {target}")
            continue
        seen_targets.add(key)
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
                           "extra": is_extra, "base_dir": str(base_path)})
    return plan


def apply_import(path: Path, plan: ImportPlan,
                 progress_info: Callable[[ProgressInfo], None] | None = None) -> Report:
    """검증을 통과한 계획을 실행한다. 덮어쓸 파일은 .bak 백업, 복사 후 해시 재검증.

    progress_info 가 있으면 파일 수/현재 파일/분류별 개수를 알린다(GUI 현황 카드용, CLI 출력과 무관).
    """
    if plan.problems:
        raise PackageError("검증 실패 상태에서는 복원할 수 없습니다")
    report = Report(dry_run=False)
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
