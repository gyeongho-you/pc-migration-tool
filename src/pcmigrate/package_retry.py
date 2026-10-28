"""읽지 못해 제외된 파일 다시 담기(retry_failed_export).

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import json
import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__, failure, fs
from . import settings_filter
from .cancel import checkpoint
from .copier import _backup_path
from .discover_data import EXTRA_ROOTS
from .envpaths import expand_env
from .progress import FAILED_KIND, OK, UNREADABLE, ProgressInfo, ProgressTracker
from .rules import Rule
from .package_base import CHUNK, EXTRA_ID, Expand, MANIFEST, PackageError, STAGE_PREFIX, SourceUnreadable, ZIP64_THRESHOLD, _within, unreadable_entry, unsafe_name
from .package_collect import Item, collect_extra_items, collect_items
from .package_export import _check_export_space
from .package_savepoint import FAILED_SUFFIX, LOCK_SUFFIX, _OutputLock, is_unfinished_part, part_paths
from .package_verify import verify_package
from .package_zip import COMPRESS_MODES, METHOD_NAMES, _PartZip, _write_entry


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
                checkpoint()
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
        checkpoint()
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
