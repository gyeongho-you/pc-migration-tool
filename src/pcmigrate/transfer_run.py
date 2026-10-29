"""transfer 분해 모듈: transfer() 본체: 전송 전체 흐름(준비 → 복사 → 검사 → 리포트).

공개 이름은 transfer.py 파사드가 그대로 다시 내보낸다.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import failure, fs, junk as junk_mod, split
from .copier import CLOUD_ONLY, FAILED, FileResult, IoStats, Report
from .engine import clamp_workers
from .fs import long_path
from .manifest import ManifestReader, ManifestWriter
from .power import KeepAwake
from .progress import OK, ProgressInfo, ProgressTracker, fmt_bytes, format_info, slow_policy
from .scan import scan_source_info
from .transfer_base import AttrGetter, DEFAULT_CHUNK, DISCONNECTED_MESSAGE, DriveDisconnected, LOG_NAME, MAX_DETAIL_RESULTS, PRESERVE_NAME, Runner, SPACE_MARGIN, STATE_NAME, TransferError, _friendly_makedirs, _is_within, _join
from .transfer_copy import _alive_checker, _copy_with_robocopy, _dry_run
from .transfer_preserve import DestScan, _preserve_existing, _scan_dest, _write_journal
from .transfer_robocopy import DEFAULT_COPY_THREADS, default_attr_getter, default_runner, interpret_exit, make_runner, parse_copy_line, parse_copy_path
from .transfer_split import SplitCtx, _note_split_backups, _split_copy_entry
from .transfer_state import State
from .transfer_verify import _verify_phase

# --------------------------------------------------------------------------- 전송

def transfer(src: Path, dst: Path, *, dry_run: bool = False, include_cloud: bool = False,
             runner: Runner = default_runner, attr_getter: AttrGetter = default_attr_getter,
             log_path: Path | None = None, warn: Callable[[str], None] = lambda m: None,
             strict: bool = False, state_path: Path | None = None,
             progress: Callable[[str], None] | None = None,
             cancel: Callable[[], bool] | None = None,
             progress_info: Callable[[ProgressInfo], None] | None = None,
             verify_only: bool = False, retry_failed: bool = False,
             workers: int | None = None, chunk: int | None = None, keep_awake: bool = True,
             power_set_state: Callable[[int], int | None] | None = None,
             dest_info_fn: Callable[[str], fs.DestInfo] | None = None,
             manifest_out: Path | str | None = None, use_manifest: Path | str | None = None,
             verify_dest: bool | None = None, copy_mode: str = "restartable",
             retries: int | None = None, wait: int | None = None,
             clock: Callable[[], float] = time.monotonic, alive_fn: Callable[[], bool] | None = None,
             min_report_interval: float = 1.0, copy_threads: int | None = None, split_large: bool = False,
             junk_spec: "junk_mod.JunkSpec | None" = None) -> Report:
    """src 폴더를 dst 로 복사하고 SHA-256 으로 검사한다.

    - strict: 상태 파일을 믿지 않고 모든 파일을 다시 검사하고, 내용이 어긋난 파일은 원본에서 다시 복사(.bak 보존)한다.
    - verify_only: 복사 없이 원본/대상을 비교만 한다(대상은 항상 다시 읽음, 원본 해시는 상태 파일 값을 재사용).
    - retry_failed: 전체 robocopy 를 돌리지 않고 검사가 끝나지 않은/실패한 파일만 다시 복사하고 검사한다.
    - workers/chunk: 파일 단위 병렬 해시 개수(기본 2)와 읽기 청크.
    - manifest_out: 원본 기준 해시 목록을 JSONL 로 저장(새 PC 에서 verify 로 비교). verify_dest 가 None 이면
      대상이 네트워크 경로이고 목록을 만드는 경우 대상 검사를 미룬다(네트워크로 다시 읽지 않음).
    - use_manifest: 원본 해시를 이 목록에서 가져와 원본을 다시 읽지 않는다(외장 드라이브 2단계: 외장에서 새 PC 로 복사).
    - copy_mode: restartable(/Z) | normal | unbuffered(/J).
    - split_large: 4GiB-1 바이트를 넘는 파일을 조각(.pcm001...)으로 나눠 저장한다(FAT32 대상 포함). 끄면 동작이 전과 같다.
    - junk_spec: junk.make_spec() 결과. 주면 그 규칙의 파일/폴더를 robocopy /XF /XD 로 빼고 원본 조사에서도 뺀 뒤
      report.meta["junk_excluded"] 에 개수/용량을 남긴다. None(기본)이면 아무것도 제외하지 않는다.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise TransferError(f"원본 폴더가 없습니다: {src}")
    if os.path.realpath(src) == os.path.realpath(dst) or _is_within(dst, src):
        raise TransferError("대상은 원본과 같거나 원본 폴더 안에 있을 수 없습니다")
    if verify_only and retry_failed:
        raise TransferError("검사만 다시(verify_only)와 실패한 것만 다시(retry_failed)는 함께 쓸 수 없습니다")
    if dry_run and (verify_only or retry_failed):
        raise TransferError("미리보기에서는 검사만 다시/실패한 것만 다시를 쓸 수 없습니다")
    workers = clamp_workers(workers)
    threads = max(1, min(16, copy_threads or DEFAULT_COPY_THREADS))
    chunk = chunk or DEFAULT_CHUNK
    if runner is default_runner:  # 줄 단위 진행/취소/드라이브 분리 감지가 되는 실행기
        runner = make_runner(cancel)
    emit_lock = threading.Lock()
    last_emit = [0.0]

    def say(msg: str) -> None:
        if progress:
            progress(msg)

    t_start = clock()
    dest = (dest_info_fn or fs.describe_destination)(str(dst))
    if dest.kind == "cdrom":
        raise TransferError("대상이 CD/DVD 드라이브입니다. 쓸 수 있는 하드디스크나 외장 드라이브를 고르세요.")
    network = dest.is_network
    if verify_only and not dst.is_dir():
        raise TransferError(f"검사할 대상 폴더가 없습니다: {dst}")
    defer = bool(manifest_out) and (network if verify_dest is None else not verify_dest) and not verify_only
    check_dest = not defer
    if network and not manifest_out and not dry_run and not verify_only:
        warn("대상이 네트워크 경로입니다. 복사본을 네트워크로 다시 읽어 검사하면 복사만큼 오래 걸립니다. "
             "목록 파일(--manifest-out)을 만들고 새 PC 에서 verify 로 검사하는 방법을 권장합니다.")

    report = Report(dry_run=dry_run)
    mode = "dry-run" if dry_run else "verify-only" if verify_only else "retry-failed" if retry_failed else "copy"
    report.meta.update(mode=mode, strict=strict, dest_kind=dest.kind, dest_fs=dest.fs, workers=workers, copy_threads=threads, chunk_bytes=chunk,
                       copy_mode=copy_mode, dest_verification="deferred" if defer else "now",
                       # 대상 내용 검사가 이 실행에서 끝났는지. 미룬 경우(랜선/네트워크 + 해시 목록) 새 PC 에서 verify 를 해야 끝난다.
                       verification_complete=not defer and not dry_run)

    # ---- 원본 조사(총 용량/파일 수/최상위 구조)
    say("원본 파일을 조사하는 중입니다… 파일이 많으면 몇 분 걸릴 수 있습니다.")
    scan = scan_source_info(src, attr_getter=None if attr_getter is default_attr_getter else attr_getter,
                            progress=progress, progress_info=progress_info, cancel=cancel, clock=clock)
    junk_info = junk_mod.apply_to_scan(scan, junk_spec) if junk_spec is not None else None
    entries = scan.entries + (scan.cloud if include_cloud else [])
    skipped_cloud = [] if include_cloud else scan.cloud
    total_bytes = scan.total_bytes + (scan.cloud_bytes if include_cloud else 0)
    detail_ok = len(entries) <= MAX_DETAIL_RESULTS
    sctx = SplitCtx(split.part_size()) if split_large else None
    plain = entries  # robocopy 가 복사하는 파일(분할 대상 제외)
    if sctx is not None:
        big = [e for e in entries if split.needs_split(e[1])]
        if big:
            sctx.entries = big
            plain = [e for e in entries if not split.needs_split(e[1])]
            sctx.bytes = sum(e[1] for e in big)
            sctx.parts_total = sum(len(split.part_sizes(e[1], sctx.psz)) for e in big)
        report.meta.update(split_large=True, split_threshold=split.split_threshold(), split_part_size=sctx.psz,
                           split_files=len(big), split_parts=sctx.parts_total, split_bytes=sctx.bytes)
    if scan.cloud:
        warn(f"OneDrive 클라우드 전용 파일 {len(scan.cloud)}개 감지: "
             + ("--include-cloud 로 포함됩니다(대량 다운로드 주의)." if include_cloud else "기본으로 건너뜁니다."))
        for rel, _s, _m in scan.cloud[:20]:
            warn(f"  클라우드 전용: {scan.abs_path(rel)}")
        if len(scan.cloud) > 20:
            warn(f"  ... 외 {len(scan.cloud) - 20}개")
    for rel, _s, _m in skipped_cloud:
        report.results.append(FileResult(scan.abs_path(rel), "", CLOUD_ONLY, error="클라우드 전용 파일(건너뜀)", reason="cloud_only"))
    for rel, code, text in scan.errors:
        report.results.append(FileResult(scan.abs_path(rel), "", FAILED, reason=code, error=f"{text} ({rel})"))
    report.meta.update(cloud_only_count=len(scan.cloud), include_cloud=include_cloud, total_files=len(entries),
                       total_bytes=total_bytes, source_root=scan.root, source_layout=scan.layout(),
                       scan_seconds=round(scan.seconds, 2), skipped_links=scan.skipped_links, skipped_link_paths=list(scan.skipped_link_paths),
                       results_truncated=not detail_ok)
    if junk_info is not None:
        report.meta["junk_excluded"] = junk_info.as_dict(junk_spec)
    say(f"원본 조사 완료: 파일 {len(entries):,}개, 총 {fmt_bytes(total_bytes)} (조사 {scan.seconds:.0f}초).")

    # ---- 파일 시스템/여유 공간 사전 검사(복사 전에 막는다). export 와 같은 fs.check_space 를 쓴다
    biggest = scan.max_size if sctx is None or not sctx.entries else max([e[1] for e in plain] + [sctx.psz])
    sc0 = fs.check_space(dest, 0, biggest)
    if sc0.fat32_blocked:
        big = sum(1 for _r, s, _m in entries if s > fs.FAT4G)
        report.meta.update(fat32_blocked=True, fat32_big_files=big)
        msg = (f"대상 드라이브가 FAT32 라서 4GB 보다 큰 파일 {big}개({fmt_bytes(scan.max_size)} 포함)를 저장할 수 없습니다. "
               "NTFS 로 포맷하거나 NTFS 드라이브를 쓰세요. 포맷이 어려우면 --split-large 옵션으로 큰 파일을 조각(.pcm001 ...)으로 나눠 "
               "저장하고, 새 PC 에서 pcmigrate join 으로 합칠 수도 있습니다.")
        if dry_run:
            warn(msg)
        else:
            raise TransferError(msg)
    elif sc0.exfat:
        warn("대상 드라이브가 exFAT 입니다. 4GB 이상 파일은 저장되지만 전원이 갑자기 꺼지면 손상되기 쉽습니다. 가능하면 NTFS 를 권장합니다.")
    if sctx is not None and sctx.entries:
        warn(f"--split-large: 4GB 넘는 파일 {len(sctx.entries)}개({fmt_bytes(sctx.bytes)})를 조각 {sctx.parts_total}개(조각 하나 최대 "
             f"{fmt_bytes(sctx.psz)})로 나눠 저장합니다. 새 PC 에서 pcmigrate join 으로 합쳐야 원래 파일이 됩니다.")
    dscan: DestScan | None = None
    if not verify_only:
        say("대상에 이미 있는 파일을 확인하는 중입니다…")
        dscan = _scan_dest(plain, dst, total_bytes - (sctx.bytes if sctx else 0), cancel)  # 복사 여부 판단 기준(_differs 와 같음)을 한 번만 훑는다
        if sctx is not None and sctx.entries:  # 조각은 이미 있는(크기가 맞는) 조각만큼 덜 쓴다
            dscan.need += sum(e[1] - split.parts_bytes_present(_join(str(dst), e[0]), e[1], sctx.psz) for e in sctx.entries)
        required = dscan.need + int(total_bytes * SPACE_MARGIN)
        sc = fs.check_space(dest, required, scan.max_size)
        report.meta.update(dest_free_bytes=dest.free, required_bytes=required, space_blocked=sc.space_blocked,
                           would_preserve_count=dscan.bak_count, would_preserve_bytes=dscan.bak_bytes)
        if sc.free_unknown:
            warn("대상의 여유 공간을 조회하지 못해 사전 검사를 건너뜁니다. 복사 중 공간이 부족하면 멈출 수 있습니다.")
        elif sc.space_blocked:
            keep = (f" 이미 있는 다른 파일 {dscan.bak_count}개({fmt_bytes(dscan.bak_bytes)})는 .bak 으로 남아 공간을 계속 차지합니다."
                    if dscan.bak_count else "")
            msg = (f"대상 드라이브의 여유 공간이 부족합니다. 필요 약 {fmt_bytes(required)}(새로 쓸 {fmt_bytes(dscan.need)} + 여유 5%), "
                   f"현재 여유 {fmt_bytes(dest.free or 0)}.{keep} 더 큰 드라이브를 쓰거나 정리한 뒤 다시 시작하세요.")
            if dry_run:
                warn(msg)
            else:
                raise TransferError(msg)

    if dry_run:
        if progress_info:
            progress_info(ProgressInfo("compare", 0, 0, 0, len(entries), message="복사될 파일을 비교하는 중입니다…",
                                       skipped=len(skipped_cloud)))
        return _dry_run(report, plain, skipped_cloud, scan, dst, runner, copy_mode, network, warn, dscan, sctx, junk_spec)

    # ---- 실제 실행
    _friendly_makedirs(dst)
    log = Path(log_path) if log_path else dst / LOG_NAME
    state = State(Path(state_path) if state_path else dst / STATE_NAME, src, str(dst), dest.serial)
    report.meta["state_file"] = str(state.path)
    if state.reset_reason:
        report.meta["state_reset"] = state.reset_reason
        warn(state.reset_reason)
    if state.prev_dest_path and state.prev_dest_path.lower() != str(dst).lower():
        if dest.serial is not None and state.prev_dest_serial == dest.serial:
            warn(f"드라이브 문자가 바뀐 것 같습니다(이전 {state.prev_dest_path} → 지금 {dst}). 같은 드라이브이므로 이어받습니다.")
        else:
            warn(f"이전 기록의 대상 위치({state.prev_dest_path})와 지금({dst})이 다릅니다. 다른 드라이브가 아닌지 확인하세요.")
    elif state.prev_dest_serial is not None and dest.serial is not None and state.prev_dest_serial != dest.serial:
        warn("같은 경로지만 드라이브 일련번호가 이전과 다릅니다. 다른 드라이브가 같은 문자로 연결됐을 수 있으니 확인하세요(검사가 다시 진행됩니다).")
    alive = alive_fn or _alive_checker(dst, network)

    manifest_map: dict[str, tuple[int, str]] | None = None
    if use_manifest:
        manifest_map = {}
        mreader = ManifestReader(Path(use_manifest))
        report.meta["original_root"] = str(mreader.header.get("source_root", ""))
        for row in mreader.rows():
            if row.get("parts"):  # 분할 파일(--split-large)은 조각 파일을 각각 일반 파일로 본다(외장 드라이브에는 조각으로 들어 있다)
                folder = row["rel"].rpartition("/")[0]
                for part in row["parts"]:
                    manifest_map[f"{folder}/{part['name']}" if folder else part["name"]] = (part["size"], part["sha256"])
            else:
                manifest_map[row["rel"]] = (row["size"], row["sha256"])
        report.meta["use_manifest"] = str(use_manifest)
    writer = None
    if manifest_out:
        writer = ManifestWriter(manifest_out, scan.root, len(entries), total_bytes, scan.layout())
    stats = IoStats()
    kind_bps, kind_hint = slow_policy("network" if network else dest.kind)
    ok_finish = False
    try:
        with KeepAwake(keep_awake, power_set_state, warn) as awake:
            # ---- 1) 복사 단계(robocopy). 검사만/실패한 것만 다시 모드에서는 건너뛴다.
            copy_seconds = 0.0
            journal = dst / PRESERVE_NAME
            report.meta.update(preserved_existing_count=0, preserved_existing=[], preserve_resumed=False)
            if mode == "copy":
                n_pres, listed, resumed_run = _preserve_existing(plain, src, dst, journal, cancel, say, dscan, warn)
                report.meta.update(preserved_existing_count=n_pres, preserved_existing=listed, preserve_resumed=resumed_run)
                if n_pres:
                    warn(f"대상에 있던 같은 이름의 다른 파일 {n_pres}개를 .bak 으로 보존했습니다(덮어쓰지 않았습니다).")
                t0 = clock()
                tracker = ProgressTracker("copy", total_bytes, len(entries), clock=clock, slow_bps=kind_bps, slow_hint=kind_hint)

                def emit_copy() -> None:
                    now = clock()
                    with emit_lock:
                        if now - last_emit[0] < min_report_interval:
                            return
                        last_emit[0] = now
                    snap = tracker.snapshot()
                    if progress_info:
                        progress_info(snap)
                    say(format_info(snap))

                def on_line(line: str) -> None:
                    size = parse_copy_line(line)
                    if size is None:
                        return
                    tracker.add(size, 1)
                    tracker.set_current(parse_copy_path(line) or "")
                    tracker.note(OK)
                    emit_copy()

                say("복사 도구로 복사하는 중입니다(파일이 크면 오래 걸립니다)…")
                code, text, runs = _copy_with_robocopy(src, dst, log, skipped_cloud + (sctx.entries if sctx else []), runner, cancel, copy_mode,
                                                       retries if retries is not None else (10 if network else 3),
                                                       wait if wait is not None else (30 if network else 5), on_line, scan, alive, threads, junk_spec)
                ok, notes = interpret_exit(code)
                report.meta.update(robocopy_exit=code, robocopy_ok=ok, robocopy_notes=notes, robocopy_runs=runs,
                                   robocopy_log=str(log))
                found = failure.robocopy_failures(text)
                report.meta["robocopy_failures"] = [f.code for f in found]
                if not ok:
                    fatal = [f for f in found if f.code.startswith("net_")]
                    if fatal:
                        raise TransferError(fatal[0].text)
                    if not alive():
                        raise DriveDisconnected(DISCONNECTED_MESSAGE)
                    why = " ".join(f.label for f in found)
                    report.results.append(FileResult(str(src), str(dst), FAILED, reason=found[0].code if found else "robocopy",
                                                     error=f"복사 도구 종료 코드 {code}: {', '.join(notes)}" + (f" ({why})" if why else "")))
                if sctx is not None and sctx.entries:
                    say(f"4GB 넘는 파일 {len(sctx.entries)}개를 조각으로 나눠 복사하는 중입니다(파일이 크면 오래 걸립니다)…")
                    for entry in sctx.entries:
                        tracker.set_current(_join(str(dst), entry[0]))
                        r = _split_copy_entry(entry, scan.root, dst, state, sctx, strict=strict, chunk=chunk, cancel=cancel,
                                              stats=stats, on_bytes=lambda n: (tracker.add(n), emit_copy()), repair=False)
                        if r is not None and not r.ok and r.reason == failure.DISCONNECTED and not alive():
                            raise DriveDisconnected(DISCONNECTED_MESSAGE)
                        tracker.add(0, 1)
                        emit_copy()
                copy_seconds = clock() - t0
                if ok:  # 복사가 끝났다: 다음 실행은 새 복사로 보고 바뀐 파일을 .bak 으로 보존한다
                    try:
                        _write_journal(journal, src, "done", report.meta["preserved_existing_count"], report.meta["preserved_existing"])
                    except OSError:
                        try:  # 'done' 을 못 적으면 기록을 지운다(남겨 두면 다음 실행이 이어받기로 보고 .bak 보존을 건너뛰게 된다)
                            os.unlink(long_path(journal))
                        except OSError:
                            pass
            # ---- 2) 검사 단계(파일 단위 병렬)
            t1 = clock()
            _verify_phase(report, entries, scan, dst, state, stats, writer, manifest_map, strict=strict, mode=mode,
                          check_dest=check_dest, workers=workers, chunk=chunk, cancel=cancel, progress=progress,
                          progress_info=progress_info, clock=clock, min_interval=min_report_interval, alive=alive,
                          detail_ok=detail_ok, kind_bps=kind_bps, kind_hint=kind_hint, warn=warn, sctx=sctx)
            if sctx is not None:
                _note_split_backups(report, sctx, warn)
            verify_seconds = clock() - t1
        report.meta.update(keep_awake=awake.status, copy_seconds=round(copy_seconds, 1), verify_seconds=round(verify_seconds, 1),
                           total_seconds=round(clock() - t_start, 1), io=stats.as_dict())
        if writer is not None:
            report.meta["manifest_out"] = str(writer.close())
            report.meta["manifest_files"] = writer.files
        ok_finish = True
    finally:
        state.save()
        if writer is not None and not ok_finish:
            writer.abort()
    return report
