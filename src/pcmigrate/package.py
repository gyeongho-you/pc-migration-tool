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

import fnmatch
import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from . import __version__
from .copier import (COPIED, FAILED, SKIPPED_SAME, WOULD_COPY, WOULD_OVERWRITE,
                     FileResult, Report, _backup_path, copy_file, sha256_file)
from .detect import Program
from .rules import Rule

FORMAT_VERSION = 1
MANIFEST = "manifest.json"
CHUNK = 1024 * 1024
ZIP64_THRESHOLD = 2 * 1024 ** 3
# 규칙의 exclude 와 별개로 항상 제외: 라이선스는 복원하지 않는다.
GLOBAL_EXCLUDE = ("*.lic", "*.pcmigrate-part")
WILDCARD = set("*?[")

Expand = Callable[[str], str]


class PackageError(ValueError):
    """패키지가 손상되었거나 안전하지 않음."""


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


def _excluded(name: str, rule: Rule) -> bool:
    return any(fnmatch.fnmatch(name.lower(), pat.lower())
               for pat in GLOBAL_EXCLUDE + rule.exclude)


# --------------------------------------------------------------------------- export

@dataclass
class Item:
    arc: str
    src: Path
    rule_id: str
    base: str  # 미확장 base (예: %APPDATA%\Autodesk)
    rel: str   # base 기준 상대 경로(posix)


def collect_items(rule: Rule, expand: Expand = os.path.expandvars
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


def _write_entry(zf: zipfile.ZipFile, arc: str, src: Path) -> tuple[int, str]:
    """원본을 읽기 전용으로 스트리밍하며 zip 에 쓰고 (크기, sha256)을 반환한다."""
    size = src.stat().st_size
    h = hashlib.sha256()
    n = 0
    info = zipfile.ZipInfo(arc, date_time=_zip_time(src))
    info.compress_type = zipfile.ZIP_DEFLATED
    with open(src, "rb") as fin, zf.open(info, "w", force_zip64=size > ZIP64_THRESHOLD) as fout:
        while chunk := fin.read(CHUNK):
            h.update(chunk)
            n += len(chunk)
            fout.write(chunk)
    return n, h.hexdigest()


def _zip_time(path: Path) -> tuple:
    t = datetime.fromtimestamp(path.stat().st_mtime)
    return (max(t.year, 1980), t.month, t.day, t.hour, t.minute, t.second)


def export_package(rules: list[Rule], out: Path, programs: list[Program],
                   winget_text: str | None, expand: Expand = os.path.expandvars,
                   winget_list_text: str | None = None,
                   now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """감지된 프로그램의 규칙(rules)으로 설정 패키지를 만든다. manifest dict 반환.

    out 이 이미 있으면 .bak 으로 옮겨 보존한다. 파일 단위 실패는 manifest["failed"] 에 기록.
    """
    out = Path(out)
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
    try:
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
            seen: set[str] = set()
            for rule in rules:
                items, skipped = collect_items(rule, expand)
                manifest["skipped"] += skipped
                manifest["rules"].append({"id": rule.id, "name": rule.name, "version": rule.version,
                                          "notes": rule.notes})
                manifest["manual_warnings"].append({"rule": rule.id, "name": rule.name,
                                                    "items": list(rule.manual_warnings)})
                for it in items:
                    if it.arc in seen:
                        manifest["failed"].append({"src": str(it.src), "error": f"중복된 패키지 경로 {it.arc}"})
                        continue
                    seen.add(it.arc)
                    try:
                        size, digest = _write_entry(zf, it.arc, it.src)
                    except OSError as e:
                        manifest["failed"].append({"src": str(it.src), "error": f"{type(e).__name__}: {e}"})
                        continue
                    manifest["files"].append({"arc": it.arc, "size": size, "sha256": digest,
                                              "rule": it.rule_id, "base": it.base, "rel": it.rel})
            zf.writestr("programs/registry.json", json.dumps([asdict(p) for p in programs],
                                                             ensure_ascii=False, indent=2))
            if winget_text:
                zf.writestr("programs/winget.json", winget_text)
            if winget_list_text:
                zf.writestr("programs/winget-list.txt", winget_list_text)
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        problems = verify_package(tmp)[1]
        if problems:
            raise PackageError("생성한 패키지 검증 실패: " + "; ".join(problems[:3]))
        if out.exists():
            os.replace(out, _backup_path(out))
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
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


def verify_package(path: Path) -> tuple[dict | None, list[str]]:
    """zip 구조/경로 안전성과 manifest 해시를 검증한다. (manifest, 문제 목록) 반환."""
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
            if manifest.get("format") != FORMAT_VERSION or not isinstance(manifest.get("files"), list):
                raise ValueError("지원하지 않는 형식")
        except (ValueError, UnicodeDecodeError) as e:
            return None, [f"manifest 오류: {e}"]
        listed = set()
        for f in manifest["files"]:
            try:
                arc, size, digest = f["arc"], f["size"], f["sha256"]
                _safe_rel(arc)
                _safe_rel(f["rel"])
                f["rule"], f["base"]
            except (KeyError, TypeError, PackageError):
                problems.append(f"manifest 항목 오류: {f!r}")
                continue
            listed.add(arc)
            if arc not in names:
                problems.append(f"패키지에 없음: {arc}")
                continue
            n, h = _hash_entry(zf, arc)
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


def plan_import(path: Path, rules: list[Rule], expand: Expand = os.path.expandvars) -> ImportPlan:
    """검증 후 복원 계획을 만든다. 파일 시스템은 읽기만 한다."""
    manifest, problems = verify_package(path)
    plan = ImportPlan(manifest or {}, problems=list(problems))
    if manifest is None or problems:
        return plan
    by_id = {r.id: r for r in rules}
    allowed = {rid: {split_rule_path(p.path)[0] for p in r.paths if split_rule_path(p.path)}
               for rid, r in by_id.items()}
    for f in manifest["files"]:
        rid, base = f["rule"], f["base"]
        if rid not in by_id:
            plan.problems.append(f"알 수 없는 규칙 id: {rid}")
            continue
        if base not in allowed[rid]:
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
        if _excluded(target.name, by_id[rid]):
            plan.problems.append(f"복원 제외 대상(라이선스 등): {f['rel']}")
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
                           "backup": backup, "size": f["size"], "sha256": f["sha256"]})
    return plan


def apply_import(path: Path, plan: ImportPlan) -> Report:
    """검증을 통과한 계획을 실행한다. 덮어쓸 파일은 .bak 백업, 복사 후 해시 재검증."""
    if plan.problems:
        raise PackageError("검증 실패 상태에서는 복원할 수 없습니다")
    report = Report(dry_run=False)
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory() as tmp:
        for it in plan.items:
            if it["status"] == SKIPPED_SAME:
                report.results.append(FileResult(it["entry"], it["target"], SKIPPED_SAME,
                                                 it["size"], it["sha256"]))
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
                res = FileResult(it["entry"], it["target"], FAILED, error=f"{type(e).__name__}: {e}")
            report.results.append(res)
            staged.unlink(missing_ok=True)
    return report
