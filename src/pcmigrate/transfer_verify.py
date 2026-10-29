"""transfer 분해 모듈: 복사 후 검사 단계(해시/크기 비교, 재복사 후 재검사, 결과 집계).

공개 이름은 transfer.py 파사드가 그대로 다시 내보낸다.
"""
from __future__ import annotations

import threading
from pathlib import Path

from .cancel import Cancelled
from .copier import FAILED, FileResult, IoStats, MISMATCH, MISSING, PENDING_VERIFY, Report, SKIPPED_SAME, VERIFIED, copy_verified, repair_file, sha256_file, verify_file
from .engine import run_ordered
from .progress import ProgressTracker, categorize, format_info
from .scan import ScanInfo
from .transfer_base import DISCONNECTED_MESSAGE, DriveDisconnected, _join
from .transfer_split import _state_conflict, _verify_split_entry
from .transfer_state import State

# --------------------------------------------------------------------------- 보조

def _repair_and_reverify(src: Path, dst: Path, before, report: Report, **kw):
    """strict 에서 불일치로 판정된 파일만 재복사하고 다시 해시 검사한다. 실패하면 원래 불일치 결과를 유지."""
    rep = repair_file(src, dst)
    if rep.status == FAILED:
        before.error = f"{before.error}; 재복사 실패: {rep.error}"
        return before
    after = verify_file(src, dst, **kw)
    after.backup = rep.backup
    if after.status == VERIFIED:
        report.meta.setdefault("repaired", []).append({"file": str(dst), "backup": rep.backup, "reason": before.error})
    return after


def _add_result(report: Report, res: FileResult, detail_ok: bool) -> None:
    """정상 결과는 파일이 아주 많으면 개수만 세고(메모리/리포트 크기), 문제 있는 결과는 항상 남긴다."""
    if res.status in (VERIFIED, SKIPPED_SAME, PENDING_VERIFY) and not detail_ok:
        report.counts[res.status] = report.counts.get(res.status, 0) + 1
    else:
        report.results.append(res)


def _verify_phase(report: Report, entries, scan: ScanInfo, dst: Path, state: State, stats: IoStats, writer,
                  manifest_map, *, strict: bool, mode: str, check_dest: bool, workers: int, chunk: int, cancel,
                  progress, progress_info, clock, min_interval: float, alive, detail_ok: bool, kind_bps: float,
                  kind_hint: str, warn, sctx: "SplitCtx | None" = None) -> None:
    """모든 파일을 검사한다(파일 단위 병렬). 상태 파일/해시 목록의 원본 해시는 다시 읽지 않고 재사용한다."""
    dst_root = str(dst)
    retry = mode == "retry-failed"
    verify_only = mode == "verify-only"
    resumed = [0]
    unmatched_manifest = dict(manifest_map) if manifest_map is not None else None
    split_rels = {e[0] for e in sctx.entries} if sctx is not None and sctx.entries else frozenset()

    def reuse_hash(rel: str, size: int, mtime: int) -> str | None:
        if strict:
            return None
        if manifest_map is not None:
            m = manifest_map.get(rel)
            if m and m[0] == size:
                return m[1]
        return state.src_hash(rel, size, mtime)

    def expected_reads(entry) -> int:
        rel, size, mtime = entry
        reads = 0 if reuse_hash(rel, size, mtime) else 1
        return reads + (1 if check_dest else 0)

    total = sum(e[1] * expected_reads(e) for e in entries)
    tracker = ProgressTracker("verify", total, len(entries), clock=clock, slow_bps=kind_bps, slow_hint=kind_hint)
    last = [0.0]
    lock = threading.Lock()
    on_bytes = tracker.add

    def emit(force: bool = False) -> None:
        now = clock()
        with lock:
            if not force and now - last[0] < min_interval:
                return
            last[0] = now
        snap = tracker.snapshot()
        if progress_info:
            progress_info(snap)
        if progress:
            progress(format_info(snap))

    def count_src(n: int) -> None:
        stats.add("src", n)
        on_bytes(n)

    def count_dst(n: int) -> None:
        stats.add("dst", n)
        on_bytes(n)

    def task(entry):
        rel, size, mtime = entry
        s_path, d_path = _join(scan.root, rel), _join(dst_root, rel)
        tracker.set_current(d_path)
        if rel in split_rels:
            return _verify_split_entry(entry, scan.root, dst_root, state, sctx, stats, strict=strict, retry=retry,
                                       verify_only=verify_only, check_dest=check_dest, chunk=chunk, cancel=cancel,
                                       reuse_hash=reuse_hash, count_src=count_src, count_dst=count_dst)
        if not strict and not verify_only:
            digest = state.is_verified(rel, size, mtime, d_path)
            if digest is not None:
                return FileResult(s_path, d_path, VERIFIED, size, digest), True
        prior = state.files.get(rel)
        if retry and not (prior and (prior[3] or prior[4] == "pending")):
            res = copy_verified(s_path, d_path, chunk=chunk, cancel=cancel, on_bytes=on_bytes, stats=stats)
            if res.status in ("copied", SKIPPED_SAME):  # 이미 같은 내용이면 건너뜀(.bak 을 만들지 않음)
                res.status = VERIFIED
            return res, False
        res = verify_file(s_path, d_path, chunk=chunk, cancel=cancel, on_bytes=on_bytes, stats=stats,
                          src_hash=reuse_hash(rel, size, mtime), check_dest=check_dest)
        if retry and res.status in (MISSING, MISMATCH):  # 검사를 미뤘거나 이전에 통과했어도 대상이 비었거나 다르면 다시 복사한다
            res = copy_verified(s_path, d_path, chunk=chunk, cancel=cancel, on_bytes=on_bytes, stats=stats)
            if res.status in ("copied", SKIPPED_SAME):
                res.status = VERIFIED
            return res, False
        if strict and res.status == MISMATCH:
            res = _repair_and_reverify(Path(s_path), Path(d_path), res, report, chunk=chunk, cancel=cancel, on_bytes=on_bytes,
                                       stats=stats, check_dest=check_dest)
        if writer is not None and not res.sha256 and res.status in (MISMATCH, MISSING):
            try:  # 목록은 원본 기준이므로 대상이 없거나 달라도 원본 해시는 필요하다
                res.sha256 = sha256_file(s_path, chunk, cancel, on_bytes)
                stats.file_read("src")
            except Cancelled:
                raise
            except OSError:
                pass
        return res, False

    def handle(entry, result) -> None:
        rel, size, mtime = entry
        res, was_resumed = result
        if unmatched_manifest is not None:
            unmatched_manifest.pop(rel, None)
        if was_resumed:
            resumed[0] += 1
            tracker.total_bytes = max(0, tracker.total_bytes - size * expected_reads(entry))
        elif strict and res.status == VERIFIED and _state_conflict(state, rel, size, mtime, res.sha256):
            res.status, res.reason = MISMATCH, "state_conflict"
            res.error = "상태 파일의 검사 기록과 원본이 어긋남(크기/수정시각은 동일)"
        if not was_resumed:
            if res.status == PENDING_VERIFY:
                state.record(rel, size, mtime, res.sha256, False, "pending")
            elif res.status == VERIFIED:
                state.record(rel, size, mtime, res.sha256, True)
            else:
                state.record(rel, size, mtime, res.sha256, False, res.reason or res.status)
        if writer is not None:
            if res.sha256:
                parts = sctx.parts.get(rel) if sctx is not None and res.status in (VERIFIED, PENDING_VERIFY) else None
                writer.write_row(rel, size, mtime, res.sha256, parts)
            else:
                writer.note_error()
        if res.status == FAILED and res.reason in ("disconnected",) and not alive():
            raise DriveDisconnected(DISCONNECTED_MESSAGE)
        tracker.add(0, 1)
        tracker.note(categorize(res.status, res.reason), res.src)
        _add_result(report, res, detail_ok)
        emit()

    try:
        run_ordered(entries, task, handle, workers=workers, cancel=cancel)
    except Cancelled:
        raise
    if unmatched_manifest:
        for rel, (size, sha) in unmatched_manifest.items():
            report.results.append(FileResult(f"[목록] {rel}", _join(dst_root, rel), MISSING, size, sha, reason="missing_in_source",
                                             error="검사 목록에는 있지만 원본(외장 드라이브)에 이 파일이 없습니다. 복사가 덜 됐을 수 있습니다."))
    report.meta["resumed_skipped"] = resumed[0]
    report.meta["trusted_state_files"] = resumed[0]  # 상태 파일의 이전 검사 결과를 믿고 다시 읽지 않은 파일 수(내용까지 다시 보려면 --strict)
    emit(True)
