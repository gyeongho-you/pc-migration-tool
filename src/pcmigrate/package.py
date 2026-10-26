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
from . import __version__, failure
from .copier import (COPIED, FAILED, SKIPPED_SAME, WOULD_COPY, WOULD_OVERWRITE,
                     FileResult, Report, _backup_path, copy_file, sha256_file)
from .detect import Program
from .discover_data import EXTRA_ROOTS, RESTORE_DENY_SUBSTRINGS, ROOTS
from .progress import FAILED_KIND, OK, SKIPPED, UNREADABLE, ProgressInfo, ProgressTracker, categorize
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


def _safe_rel(rel: str) -> PurePosixPath:
    """패키지/manifest 의 상대 경로 검증. 위반 시 PackageError."""
    if not rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        raise PackageError(f"안전하지 않은 경로: {rel!r}")
    p = PurePosixPath(rel)
    if any(part in ("", ".", "..") for part in p.parts):
        raise PackageError(f"안전하지 않은 경로: {rel!r}")
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
    return any(fnmatch.fnmatch(name.lower(), pat.lower()) for pat in (GLOBAL_EXCLUDE if base is None else base) + extra)


# --------------------------------------------------------------------------- export

@dataclass
class Item:
    arc: str
    src: Path
    rule_id: str
    base: str  # 미확장 base (예: %APPDATA%\Autodesk)
    rel: str   # base 기준 상대 경로(posix)


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
            if m.is_symlink():
                skipped.append({"rule": rule.id, "path": str(m), "reason": "심볼릭 링크/정션은 따라가지 않음"})
                continue
            files: list[Path] = []
            if m.is_file():
                files = [m]
            else:
                for root, dirs, names in os.walk(m, followlinks=False):
                    dirs.sort()
                    for n in sorted(names):
                        files.append(Path(root) / n)
            for f in files:
                if f.is_symlink() or _excluded(f.name, rule):
                    skipped.append({"rule": rule.id, "path": str(f),
                                    "reason": "제외 대상(라이선스/임시/링크)"})
                    continue
                rel = f.relative_to(base_path).as_posix()
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
        if top.is_file():
            files = [top]
        else:
            for root, dirs, names in os.walk(top, followlinks=False):
                dirs.sort()
                files.extend(Path(root) / n for n in sorted(names))
        for f in files:
            if f.is_symlink() or _excluded(f.name, None):
                skipped.append({"rule": EXTRA_ID, "path": str(f), "reason": "제외 대상(라이선스/임시/링크)"})
                continue
            rel = ex.rel if f == top else f"{ex.rel}/{f.relative_to(top).as_posix()}"
            items.append(Item(f"data/{EXTRA_ID}/{keys[ex.root]}/{rel}", f, EXTRA_ID, ex.root, rel))
    return items, skipped


def _write_entry(zf: zipfile.ZipFile, arc: str, src: Path, stage_dir: str | None = None) -> tuple[int, str]:
    """원본을 읽기 전용으로 끝까지 읽어 (크기, sha256)을 구한 뒤에만 zip 엔트리를 만든다.

    다른 프로세스가 바이트 범위를 잠근 파일은 읽는 도중에 PermissionError 가 난다. zip 엔트리를 먼저 열면 반쯤 쓰인
    엔트리가 남아 패키지 검증이 깨지므로, 먼저 임시 저장소(작은 파일은 메모리, 큰 파일은 stage_dir 의 임시 파일)에
    읽어 두고 성공했을 때만 zip 에 쓴다. 읽기 실패는 SourceUnreadable 로, zip/임시 저장소 쓰기 실패는 OSError 로 전파한다.
    """
    h = hashlib.sha256()
    n = 0
    try:
        size = src.stat().st_size
        mtime = _zip_time(src)
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
        info.compress_type = zipfile.ZIP_DEFLATED
        with zf.open(info, "w", force_zip64=max(size, n) > ZIP64_THRESHOLD) as fout:
            shutil.copyfileobj(spool, fout, CHUNK)
    return n, h.hexdigest()


def _zip_time(path: Path) -> tuple:
    t = datetime.fromtimestamp(path.stat().st_mtime)
    return (max(t.year, 1980), t.month, t.day, t.hour, t.minute, t.second)


def export_package(rules: list[Rule], out: Path, programs: list[Program],
                   winget_text: str | None, expand: Expand = expand_env,
                   winget_list_text: str | None = None, extras: list | None = None,
                   now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                   progress_info: Callable[[ProgressInfo], None] | None = None) -> dict:
    """감지된 프로그램의 규칙(rules)으로 설정 패키지를 만든다. manifest dict 반환.

    progress_info 가 있으면 파일 찾기 -> 담기 -> 검사 단계의 진행(파일 수, 현재 파일, 분류별 개수)을 알린다(GUI 현황 카드용).

    out 이 이미 있으면 .bak 으로 옮겨 보존한다. 파일 단위 실패는 manifest["failed"] 에 기록.
    """
    out = Path(out)
    if out.is_dir():  # 기존 폴더를 .bak 으로 옮기고 같은 이름의 파일을 만들게 되므로 시작하지 않는다
        raise IsADirectoryError(errno.EISDIR, "대상이 폴더입니다", str(out))
    tmp = out.with_name(out.name + ".pcmigrate-part")
    manifest: dict = {
        "format": FORMAT_VERSION,
        "tool_version": __version__,
        "created": now().isoformat(timespec="seconds"),
        "rules": [], "files": [], "skipped": [], "failed": [],
        "programs": {"registry": "programs/registry.json",
                     "winget": "programs/winget.json" if winget_text else None,
                     "winget_list": "programs/winget-list.txt" if winget_list_text else None},
        "manual_warnings": [],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=out.parent)  # 큰 파일을 읽어 두는 전용 임시 폴더(항상 삭제)
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
        last_emit = [0.0]

        def emit(force: bool = False) -> None:
            if progress_info is None:
                return
            now = time.monotonic()
            if force or now - last_emit[0] >= 0.5:
                last_emit[0] = now
                progress_info(tracker.snapshot())

        with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
            seen: set[str] = set()

            def add_items(items: list[Item]) -> None:
                for it in items:
                    tracker.set_current(str(it.src))
                    if it.arc in seen:
                        manifest["failed"].append({"src": str(it.src), "error": f"중복된 패키지 경로 {it.arc}"})
                        tracker.note(FAILED_KIND, str(it.src))
                        tracker.add(0, 1)
                        continue
                    seen.add(it.arc)
                    try:
                        size, digest = _write_entry(zf, it.arc, it.src, stage)  # 2) 담기
                    except SourceUnreadable as e:  # zip 에는 아무 흔적도 남지 않았다
                        manifest["failed"].append(unreadable_entry(e))
                        tracker.note(UNREADABLE if e.failure.code == failure.IN_USE else FAILED_KIND, str(it.src))
                        tracker.add(0, 1)
                        emit()
                        continue
                    manifest["files"].append({"arc": it.arc, "size": size, "sha256": digest,
                                              "rule": it.rule_id, "base": it.base, "rel": it.rel})
                    tracker.note(OK)
                    tracker.add(size, 1)
                    emit()

            for items in groups:
                add_items(items)
            emit(True)
            zf.writestr("programs/registry.json", json.dumps([asdict(p) for p in programs],
                                                             ensure_ascii=False, indent=2))
            if winget_text:
                zf.writestr("programs/winget.json", winget_text)
            if winget_list_text:
                zf.writestr("programs/winget-list.txt", winget_list_text)
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        if progress_info:  # 3) 만든 패키지를 다시 읽어 검사
            progress_info(ProgressInfo("check", total_bytes, total_bytes, total_files, total_files,
                                       message="만든 패키지를 다시 읽어 검사하는 중입니다…", ok=tracker.counts[OK],
                                       skipped=tracker.counts[SKIPPED], unreadable=tracker.counts[UNREADABLE],
                                       failed=tracker.counts[FAILED_KIND]))
        problems = verify_package(tmp)[1]
        if problems:
            raise PackageError("만든 패키지를 다시 검사했더니 문제가 있어 저장하지 않았습니다(원본은 그대로입니다). "
                               "문제: " + "; ".join(problems[:3]))
        if out.exists():
            os.replace(out, _backup_path(out))
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)
    return manifest


# --------------------------------------------------------------------------- import

def _hash_entry(zf: zipfile.ZipFile, name: str) -> tuple[int, str]:
    h = hashlib.sha256()
    n = 0
    with zf.open(name) as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            n += len(chunk)
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
        names = [i.filename for i in zf.infolist()]
        for n in names:  # 모든 엔트리가 패키지 루트 밖으로 나가지 않는지
            try:
                _safe_rel(n.rstrip("/") if n.endswith("/") else n)
            except PackageError:
                problems.append(f"경로 탈출/비정상 엔트리: {n!r}")
        if problems:
            return None, problems
        if MANIFEST not in names:
            return None, [f"{MANIFEST} 없음"]
        try:
            manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
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
            listed.add(arc)
            if arc not in names:
                problems.append(f"패키지에 없음: {arc}")
                continue
            try:
                n, h = _hash_entry(zf, arc)
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


def _plan_import(path: Path, rules: list[Rule], expand: Expand) -> ImportPlan:
    manifest, problems = verify_package(path)
    plan = ImportPlan(manifest or {}, problems=list(problems))
    if manifest is None or problems:
        return plan
    by_id = {r.id: r for r in rules}
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
        base_path = Path(expand(base))
        if not _usable_base(base_path):
            plan.problems.append(f"복원 위치의 환경변수를 확장할 수 없거나 상대 경로임: {base}")
            continue
        target = base_path.joinpath(*PurePosixPath(f["rel"]).parts)
        if not _within(target, base_path):
            plan.problems.append(f"복원 대상이 허용 루트 밖: {target}")
            continue
        if LOCK_NAMES and _excluded(target.name, None, LOCK_NAMES):
            continue  # 이전 버전이 만든 패키지에 든 잠금 파일: 복원하지 않고 조용히 건너뜀
        if _excluded(target.name, None if is_extra else by_id[rid], RESTORE_EXCLUDE):
            plan.problems.append(f"복원 제외 대상(라이선스 등): {f['rel']}")
            continue
        if is_extra and any(d in str(target).lower() + "\\" for d in RESTORE_DENY_SUBSTRINGS):
            plan.problems.append(f"복원이 금지된 위치(시작 프로그램/authorized_keys 등): {target}")
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
                           "extra": is_extra})
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
            staged = Path(tmp) / "entry"
            try:
                with zf.open(it["entry"]) as fin, open(staged, "wb") as fout:
                    while chunk := fin.read(CHUNK):
                        fout.write(chunk)
                res = copy_file(staged, Path(it["target"]))
                if res.status != FAILED and res.sha256 != it["sha256"]:
                    res.status, res.error = FAILED, "manifest 해시와 불일치"
                res.src = it["entry"]
            except OSError as e:
                res = FileResult(it["entry"], it["target"], FAILED, error=f"{type(e).__name__}: {e}",
                                 reason=failure.classify(e, it["target"]).code)
            done(res)
            staged.unlink(missing_ok=True)
    emit(True)
    return report
