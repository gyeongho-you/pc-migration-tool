"""transfer 분해 모듈: 4GB 분할(split_large) 파일의 조각 복사/검사 및 분할 백업 안내.

공개 이름은 transfer.py 파사드가 그대로 다시 내보낸다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import failure, split
from .cancel import Cancelled
from .copier import FAILED, FileResult, IoStats, MISMATCH, MISSING, PENDING_VERIFY, Report, VERIFIED, sha256_file
from .fs import long_path
from .transfer_base import MAX_PRESERVED_LISTED, MTIME_TOLERANCE_NS, _join
from .transfer_state import State

def _state_conflict(state: State, rel: str, size: int, mtime: int, sha256: str) -> bool:
    """상태 파일이 '검사 완료'로 기록한 해시가 지금 원본 해시와 다른데 크기/수정시각은 같은 경우."""
    e = state.files.get(rel)
    return bool(e and e[3] and e[2] != sha256 and e[0] == size and e[1] == mtime)


# --------------------------------------------------------------------------- 4GB 분할 파일(--split-large)

@dataclass
class SplitCtx:
    """이번 실행의 분할 정보. entries 는 조각으로 나눌 (상대 경로, 크기, 수정시각) 목록."""
    psz: int
    entries: list = field(default_factory=list)
    bytes: int = 0
    parts_total: int = 0
    copy_errors: dict[str, FileResult] = field(default_factory=dict)
    parts: dict[str, list[dict]] = field(default_factory=dict)  # rel -> [{name,size,sha256}]
    backups: list[tuple[str, str]] = field(default_factory=list)
    written: int = 0
    kept: int = 0


def _split_copy_entry(entry, src_root: str, dst, state: State, sctx: SplitCtx, *, strict: bool, chunk: int | None, cancel,
                      stats: IoStats, on_bytes, repair: bool) -> split.SplitCopy:
    """분할 파일 하나를 조각으로 복사한다(원본은 읽기만). 끝난 조각마다 상태 파일에 '복사 끝, 검사 전'으로 기록한다.

    repair=False(기본): 상태 파일이 '검사 끝'으로 적은 조각, 또는 크기+수정시각이 같은 조각은 건너뛴다(내용 검사는 검사 단계에서).
    repair=True/strict: 대상 조각을 읽어 해시를 원본과 비교하고 다른 조각만 다시 쓴다(옛 조각은 .bak)."""
    rel, size, mtime = entry
    s_path, d_path = _join(src_root, rel), _join(str(dst), rel)
    deep = strict or repair

    def probe(idx: int, plen: int, target: str):
        if deep:
            try:
                if os.stat(long_path(target)).st_size != plen:
                    return None, False
                return sha256_file(Path(target), chunk, cancel, lambda n: stats.add("dst", n)), False
            except Cancelled:
                raise
            except OSError:
                return None, False
        h = state.is_verified(split.state_key(rel, idx), plen, mtime, target)
        if h:
            return h, False
        try:
            st = os.stat(long_path(target))
        except OSError:
            return None, False
        return None, st.st_size == plen and abs(st.st_mtime_ns - mtime) <= MTIME_TOLERANCE_NS

    def on_part(idx: int, plen: int, digest: str, written: bool) -> None:
        state.record(split.state_key(rel, idx), plen, mtime, digest, False, "pending")

    try:
        os.makedirs(long_path(os.path.dirname(d_path)), exist_ok=True)
    except OSError as e:
        f = failure.classify(e, d_path)
        r = split.SplitCopy(error=f"{f.text} ({os.path.dirname(d_path)})", reason=f.code)
    else:
        r = split.copy_split(s_path, d_path, size, mtime, psz=sctx.psz, chunk=chunk, cancel=cancel, on_bytes=on_bytes,
                             stats=stats, probe=probe, on_part=on_part)
    if r.ok:
        sctx.parts[rel] = r.parts
        sctx.copy_errors.pop(rel, None)
        state.record(rel, size, mtime, r.sha256, False, "pending")
        sctx.written += r.written
        sctx.kept += r.kept
    else:
        sctx.copy_errors[rel] = FileResult(s_path, d_path, FAILED, size, reason=r.reason, error=r.error)
    sctx.backups += r.backups
    return r


def _verify_split_entry(entry, src_root: str, dst_root: str, state: State, sctx: SplitCtx, stats: IoStats, *, strict: bool,
                        retry: bool, verify_only: bool, check_dest: bool, chunk: int | None, cancel, reuse_hash, count_src,
                        count_dst) -> tuple[FileResult, bool]:
    """분할 파일 하나의 검사: 대상 조각이 모두 있고(크기), 조각별 해시와(이어 읽은) 전체 해시가 원본과 같은지 본다.
    원본 해시는 상태 파일/해시 목록/방금 복사하며 구한 값을 쓰고, 없으면 원본을 한 번 읽어 구한다."""
    rel, size, mtime = entry
    s_path, d_path = _join(src_root, rel), _join(dst_root, rel)
    err = sctx.copy_errors.get(rel)
    if err is not None:
        return err, False
    sizes = split.part_sizes(size, sctx.psz)
    names = [(d_path + split.part_suffix(i), n) for i, n in enumerate(sizes, 1)]
    keys = [split.state_key(rel, i) for i in range(1, len(sizes) + 1)]
    prior = state.files.get(rel)
    res = FileResult(s_path, d_path, FAILED, size)

    def parts_from(per: list[str]) -> list[dict]:
        return [{"name": os.path.basename(p), "size": n, "sha256": h} for (p, n), h in zip(names, per)]

    if not strict and not verify_only and prior and prior[3] and prior[0] == size and prior[1] == mtime:
        hs = [state.is_verified(k, n, mtime, p) for k, (p, n) in zip(keys, names)]
        if all(hs):  # 이전 실행에서 검사가 끝난 파일: 대상은 크기/수정시각만 가볍게 확인
            sctx.parts[rel] = parts_from(hs)
            res.status, res.sha256 = VERIFIED, prior[2]
            return res, True
    if retry and not (prior and (prior[3] or prior[4] == "pending")):
        r = _split_copy_entry(entry, src_root, dst_root, state, sctx, strict=strict, chunk=chunk, cancel=cancel, stats=stats,
                              on_bytes=count_src, repair=True)
        if not r.ok:
            return sctx.copy_errors[rel], False

    def expected() -> tuple[str, list[str]] | FileResult:
        whole = reuse_hash(rel, size, mtime)
        recs = [state.files.get(k) for k in keys]
        if whole and not strict and all(r and r[0] == n and r[1] == mtime for r, n in zip(recs, sizes)):
            return whole, [r[2] for r in recs]
        try:
            out = split.hash_ranges(s_path, sizes, chunk=chunk, cancel=cancel, on_bytes=count_src)
            stats.file_read("src")
            return out
        except Cancelled:
            raise
        except split.SourceChangedError:
            res.reason, res.error = "source_changed", "원본 파일의 크기가 조사한 뒤 바뀌었습니다. 원본을 쓰는 프로그램을 닫고 다시 실행하세요"
            return res
        except OSError as e:
            f = failure.classify(e, s_path)
            res.reason, res.error = f.code, f"{f.text} ({s_path})"
            return res

    def check(whole: str, per: list[str]) -> FileResult:
        out = FileResult(s_path, d_path, FAILED, size, whole)
        where = d_path
        try:
            for i, (p, n) in enumerate(names, 1):
                where = p
                try:
                    st = os.stat(long_path(p))
                except FileNotFoundError:
                    out.status, out.reason = MISSING, failure.MISSING_DEST
                    out.error = (f"조각 {i:03d}/{len(names):03d}({os.path.basename(p)})이 대상에 없습니다. "
                                 "같은 명령을 다시 실행하면 이어받습니다(--retry-failed 도 가능)")
                    return out
                if not os.path.isfile(long_path(p)) or st.st_size != n:
                    out.status, out.reason = MISMATCH, failure.SIZE_MISMATCH
                    out.error = f"조각 {i:03d}({os.path.basename(p)})의 크기가 원본과 다릅니다(원본 {n}바이트, 여기 {st.st_size}바이트)"
                    return out
            if not check_dest:
                out.status = PENDING_VERIFY
                return out
            whole_d, per_d = split.hash_parts([p for p, _n in names], chunk=chunk, cancel=cancel, on_bytes=count_dst)
            stats.file_read("dst")
            for i, (h_d, h_s) in enumerate(zip(per_d, per), 1):
                if h_d != h_s:
                    out.status, out.reason = MISMATCH, failure.MISMATCH
                    out.error = (f"조각 {i:03d}({os.path.basename(names[i - 1][0])})의 내용이 원본과 다릅니다(SHA-256 불일치). "
                                 "--strict 로 다시 실행하면 어긋난 조각만 원본에서 다시 복사합니다")
                    return out
            if whole_d != whole:
                out.status, out.reason = MISMATCH, failure.MISMATCH
                out.error = failure.REASONS[failure.MISMATCH].text + " (조각을 이어 붙인 전체 SHA-256 불일치)"
                return out
            out.status = VERIFIED
        except Cancelled:
            raise
        except OSError as e:
            bad = getattr(e, "filename", None) or where
            f = failure.classify(e, bad)
            out.status, out.reason, out.error = FAILED, f.code, f"{f.text} ({bad})"
        return out

    got = expected()
    if isinstance(got, FileResult):
        return got, False
    whole, per = got
    res = check(whole, per)
    if res.status in (MISMATCH, MISSING) and ((strict and res.status == MISMATCH) or retry):
        r = _split_copy_entry(entry, src_root, dst_root, state, sctx, strict=strict, chunk=chunk, cancel=cancel, stats=stats,
                              on_bytes=count_src, repair=True)
        if not r.ok:
            return sctx.copy_errors[rel], False
        whole, per = r.sha256, [p["sha256"] for p in r.parts]
        res = check(whole, per)
        if res.status == VERIFIED:
            res.backup = r.backups[0][1] if r.backups else ""
    sctx.parts[rel] = parts_from(per)
    for k, (_p, n), h in zip(keys, names, per):
        if res.status == VERIFIED:
            state.record(k, n, mtime, h, True)
        elif res.status == PENDING_VERIFY:
            state.record(k, n, mtime, h, False, "pending")
    return res, False


def _note_split_backups(report: Report, sctx: SplitCtx, warn: Callable[[str], None]) -> None:
    """조각 덮어쓰기 전 .bak 보존과 조각 개수/크기 요약을 리포트 meta 에 남긴다."""
    if sctx.backups:
        n = report.meta.get("preserved_existing_count", 0) + len(sctx.backups)
        listed = list(report.meta.get("preserved_existing", []))
        for target, bak in sctx.backups:
            if len(listed) < MAX_PRESERVED_LISTED:
                listed.append({"file": target, "backup": bak})
        report.meta.update(preserved_existing_count=n, preserved_existing=listed)
        warn(f"대상에 있던 같은 이름의 다른 조각 {len(sctx.backups)}개를 .bak 으로 보존했습니다(덮어쓰지 않았습니다).")
    report.meta.update(split_written_parts=sctx.written, split_kept_parts=sctx.kept)
