"""모드 A 내보내기(export_package).

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import errno
import json
import os
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__, failure, fs
from . import coverage, settings_filter
from .copier import _backup_path
from .detect import Program
from .envpaths import expand_env
from .progress import FAILED_KIND, OK, SKIPPED, UNREADABLE, ProgressInfo, ProgressTracker, fmt_bytes
from .rules import Rule
from .package_base import EXTRA_ID, Expand, FORMAT_VERSION, MANIFEST, PackageError, SPOOL_MAX, STAGE_PREFIX, SourceUnreadable, _within, unreadable_entry
from .package_collect import Item, collect_extra_items, collect_items
from .package_savepoint import FAILED_SUFFIX, JOURNAL_VERSION, LOCK_SUFFIX, SAVEPOINT_BYTES, SAVEPOINT_SECONDS, _GRACEFUL_SAVEPOINT, _Journal, _OutputLock, _ResumeVerifyFailed, _cleanup_stale_stages, _fingerprint, _prepare_part, discard_partial, part_paths
from .package_verify import verify_package
from .package_zip import COMPRESS_MODES, _PartZip, _write_entry, _zi_dump, _zi_load


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
                   extra_mode: str = settings_filter.DEFAULT_EXTRA_MODE,
                   install_items: list | None = None) -> dict:
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
    install_items: 사용자가 고른 설치 폴더 설정 파일(install_dir.InstallItem). data/install/ 영역에 담고 manifest 의 install_dir/coverage 에 기록한다.
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
              extra_mode=extra_mode, install_items=install_items)
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
                 include_caches=False, extra_mode=settings_filter.DEFAULT_EXTRA_MODE, install_items=None) -> dict:
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
            manifest["extras"] = [{"root": e.root, "rel": e.rel, **({"self_transfer": e.self_transfer["id"]} if getattr(e, "self_transfer", None) else {})}
                                  for e in extras]  # self_transfer 는 덧붙이는 표시(이전 버전은 무시)
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
        if install_items:  # 설치 폴더 설정: 별도 영역(data/install/)에 담고 모든 조건을 여기서 다시 확인한다
            from . import install_dir
            info: dict = {}
            items, skipped = install_dir.collect_install_items(install_items, programs, expand, avoid=avoid, failed=manifest["failed"],
                                                               cov=manifest["coverage"], info=info)
            manifest["skipped"] += skipped
            manifest[install_dir.MANIFEST_KEY] = info
            manifest["manual_warnings"].append({"rule": install_dir.INSTALL_ID, "name": "설치 폴더 설정(선택 포함)",
                                                "items": [install_dir.MANIFEST_NOTE, install_dir.VERSION_WARNING]})
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

        fp, fp_info = _fingerprint(rules, extras, compress, include_caches, extra_mode, install_items)
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
