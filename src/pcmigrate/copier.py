"""복사 + 검증 코어.

- 원본은 읽기만 한다(쓰기 모드로 열지 않는다).
- 복사는 임시 파일(.pcmigrate-part)에 먼저 하고, 크기 + SHA-256 검증 후 교체한다.
- 덮어쓸 대상이 이미 있고 내용이 다르면 .bak 으로 백업한다(기존 .bak 은 보존, 번호 증가).
- dry-run 은 파일 시스템을 전혀 바꾸지 않고 계획만 기록한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import failure
from .cancel import Cancelled
from .fs import long_path

CHUNK = 1024 * 1024  # 읽기 청크(바이트). transfer 에서 벤치마크로 정한 값을 chunk 인자로 넘긴다
PART_SUFFIX = ".pcmigrate-part"

# status 값
COPIED = "copied"
SKIPPED_SAME = "skipped_identical"
WOULD_COPY = "would_copy"
WOULD_OVERWRITE = "would_overwrite"
FAILED = "failed"
# transfer(모드 B) 검증 결과
VERIFIED = "verified"
MISMATCH = "mismatch"
MISSING = "missing"
CLOUD_ONLY = "cloud_only"  # OneDrive 온디맨드 파일: 기본 건너뜀(실패 아님)
PENDING_VERIFY = "pending_verify"  # 복사는 끝났고 대상 검사는 새 PC 에서 verify 로 하기로 미룸(실패 아님)
EXTRA = "extra"  # verify: 목록에 없는 추가 파일(실패 아님, 알림)
FAIL_STATUSES = (FAILED, MISMATCH, MISSING)


class IoStats:
    """검사 중 실제로 읽은 바이트를 센다(스레드 안전). 원본/대상 읽기 횟수 보고용."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.src_bytes = self.dst_bytes = 0
        self.src_files = self.dst_files = self.src_hash_reused = 0

    def add(self, side: str, n: int) -> None:
        with self._lock:
            if side == "src":
                self.src_bytes += n
            else:
                self.dst_bytes += n

    def file_read(self, side: str) -> None:
        with self._lock:
            if side == "src":
                self.src_files += 1
            else:
                self.dst_files += 1

    def reused(self) -> None:
        with self._lock:
            self.src_hash_reused += 1

    def as_dict(self) -> dict:
        return {"source_read_bytes": self.src_bytes, "dest_read_bytes": self.dst_bytes,
                "source_files_read": self.src_files, "dest_files_read": self.dst_files,
                "source_hash_reused_files": self.src_hash_reused}


_tls = threading.local()


def _thread_buffer(size: int) -> memoryview:
    """스레드별로 재사용하는 읽기 버퍼(큰 파일용)."""
    cached = getattr(_tls, "buf", None)
    if cached is None or len(cached) != size:
        cached = _tls.buf = memoryview(bytearray(size))
    return cached


def sha256_file(path: Path, chunk: int | None = None, cancel: Callable[[], bool] | None = None,
                on_bytes: Callable[[int], None] | None = None) -> str:
    """파일을 청크 단위로 읽어 SHA-256 을 계산한다(읽기 전용). 260자 넘는 긴 경로도 읽고, 취소/진행 콜백을 지원한다."""
    h = hashlib.sha256()
    size = chunk or CHUNK
    with open(long_path(path), "rb", buffering=0) as f:
        # 작은 파일마다 4MiB 버퍼를 새로 만들면 파일당 약 1ms 가 버퍼 초기화에 쓰인다(5만 개면 1분). 크기에 맞춰 줄이고,
        # 큰 파일은 스레드마다 버퍼를 재사용한다.
        try:
            fsize = os.fstat(f.fileno()).st_size
        except OSError:
            fsize = size
        if fsize + 1 < size:
            view = memoryview(bytearray(max(fsize + 1, 4096)))
        else:
            view = _thread_buffer(size)
        while True:
            if cancel is not None and cancel():
                raise Cancelled()
            n = f.readinto(view)
            if not n:
                break
            h.update(view[:n])
            if on_bytes is not None:
                on_bytes(n)
    return h.hexdigest()


@dataclass
class FileResult:
    src: str
    dst: str
    status: str
    size: int = 0
    sha256: str = ""
    backup: str = ""
    error: str = ""
    reason: str = ""  # 실패 원인 분류 코드(failure.py: in_use, permission, path_too_long, disk_full ...)


@dataclass
class Report:
    dry_run: bool
    results: list[FileResult] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # 리포트 최상위에 병합되는 부가 정보
    counts: dict = field(default_factory=dict)  # 파일이 아주 많을 때 목록에 넣지 않고 개수만 센 정상 결과(상태별)

    @property
    def failures(self) -> list[FileResult]:
        return [r for r in self.results if r.status in FAIL_STATUSES]

    def to_dict(self) -> dict:
        counts: dict[str, int] = dict(self.counts)
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {"dry_run": self.dry_run, "summary": counts,
                "failed_count": len(self.failures), **self.meta,
                "results": [asdict(r) for r in self.results]}

    def write(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                              encoding="utf-8")


def _backup_path(dst: Path) -> Path:
    cand = dst.with_name(dst.name + ".bak")
    n = 1
    while cand.exists():
        cand = dst.with_name(f"{dst.name}.bak.{n}")
        n += 1
    return cand


def copy_file(src: Path, dst: Path, dry_run: bool = False) -> FileResult:
    src, dst = Path(src), Path(dst)
    res = FileResult(str(src), str(dst), FAILED)
    tmp = dst.with_name(dst.name + PART_SUFFIX)
    try:
        res.size = src.stat().st_size
        res.sha256 = sha256_file(src)
        exists = dst.exists()
        if exists and dst.stat().st_size == res.size and sha256_file(dst) == res.sha256:
            res.status = SKIPPED_SAME
            return res
        if dry_run:
            res.status = WOULD_OVERWRITE if exists else WOULD_COPY
            if exists:
                res.backup = str(_backup_path(dst))
            return res

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, tmp)
        shutil.copystat(src, tmp, follow_symlinks=True)
        if tmp.stat().st_size != res.size or sha256_file(tmp) != res.sha256:
            raise OSError("복사 후 크기/해시 검증 실패")
        if exists:
            bak = _backup_path(dst)
            os.replace(dst, bak)
            res.backup = str(bak)
        os.replace(tmp, dst)
        res.status = COPIED
    except Exception as e:  # 파일 단위로 격리: 하나 실패해도 계속 진행
        res.status = FAILED
        res.error = f"{type(e).__name__}: {e}"
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return res


def verify_file(src: Path, dst: Path, *, chunk: int | None = None, cancel: Callable[[], bool] | None = None,
                on_bytes: Callable[[int], None] | None = None, src_hash: str | None = None,
                stats: IoStats | None = None, check_dest: bool = True) -> FileResult:
    """이미 복사된 dst 를 원본과 크기 + SHA-256 으로 비교한다(양쪽 읽기 전용).

    src_hash 를 주면 원본을 다시 읽지 않고 그 값을 쓴다(원본이 안 바뀐 것을 호출자가 확인한 경우).
    대상은 항상 다시 읽어 비교한다(check_dest=False 는 대상 검사를 새 PC 로 미루는 목록 만들기 전용: 원본 해시만 계산).
    읽기 실패는 원인별로 분류해 reason/error 에 기록한다(사용 중, 권한, 경로 길이, 연결 끊김 등).
    """
    src, dst = Path(src), Path(dst)
    res = FileResult(str(src), str(dst), FAILED)
    where = str(src)
    try:
        res.size = os.stat(long_path(src)).st_size
        if check_dest:
            where = str(dst)
            try:
                dst_size = os.stat(long_path(dst)).st_size
            except FileNotFoundError:
                res.status, res.reason = MISSING, failure.MISSING_DEST
                res.error = failure.REASONS[failure.MISSING_DEST].text
                return res
            if not os.path.isfile(long_path(dst)):
                res.status, res.reason = MISSING, failure.MISSING_DEST
                res.error = failure.REASONS[failure.MISSING_DEST].text
                return res
            if dst_size != res.size:
                res.status, res.reason = MISMATCH, failure.SIZE_MISMATCH
                res.error = f"{failure.REASONS[failure.SIZE_MISMATCH].text} (대상 {dst_size}바이트)"
                return res
        where = str(src)
        if src_hash:
            res.sha256 = src_hash
            if stats is not None:
                stats.reused()
        else:
            res.sha256 = sha256_file(src, chunk, cancel, (lambda n: _count(stats, "src", n, on_bytes)))
            if stats is not None:
                stats.file_read("src")
        if not check_dest:
            res.status = PENDING_VERIFY
            return res
        where = str(dst)
        dst_hash = sha256_file(dst, chunk, cancel, (lambda n: _count(stats, "dst", n, on_bytes)))
        if stats is not None:
            stats.file_read("dst")
        if dst_hash != res.sha256:
            res.status, res.reason = MISMATCH, failure.MISMATCH
            res.error = failure.REASONS[failure.MISMATCH].text + " (SHA-256 불일치)"
            return res
        res.status = VERIFIED
    except Cancelled:
        raise
    except OSError as e:
        f = failure.classify(e, where)
        res.status, res.reason, res.error = FAILED, f.code, f"{f.text} ({where})"
    return res


def _count(stats: IoStats | None, side: str, n: int, on_bytes: Callable[[int], None] | None) -> None:
    if stats is not None:
        stats.add(side, n)
    if on_bytes is not None:
        on_bytes(n)


def copy_verified(src: Path, dst: Path, *, chunk: int | None = None, cancel: Callable[[], bool] | None = None,
                  on_bytes: Callable[[int], None] | None = None, stats: IoStats | None = None) -> FileResult:
    """원본을 한 번만 읽으며 임시 이름으로 복사하면서 해시를 계산하고, 복사본을 다시 읽어 해시를 비교한 뒤 교체한다.

    - 원본은 읽기만 한다. 임시 파일(.pcmigrate-part)에서 확인이 끝난 뒤에만 대상 이름으로 바꾼다.
    - 대상에 다른 내용의 파일이 이미 있으면 .bak 으로 보존한다.
    - 읽기 횟수: 원본 1회(복사와 해시 동시), 복사본 1회.
    """
    src, dst = Path(src), Path(dst)
    res = FileResult(str(src), str(dst), FAILED)
    tmp = dst.with_name(dst.name + PART_SUFFIX)
    size = chunk or CHUNK
    where = str(src)
    try:
        res.size = os.stat(long_path(src)).st_size
        os.makedirs(long_path(dst.parent), exist_ok=True)
        h = hashlib.sha256()
        buf = bytearray(size)
        view = memoryview(buf)
        with open(long_path(src), "rb", buffering=0) as fin:
            where = str(tmp)
            with open(long_path(tmp), "wb", buffering=0) as fout:
                while True:
                    if cancel is not None and cancel():
                        raise Cancelled()
                    where = str(src)
                    n = fin.readinto(view)
                    if not n:
                        break
                    h.update(view[:n])
                    where = str(tmp)
                    fout.write(view[:n])
                    _count(stats, "src", n, on_bytes)
                fout.flush()
                os.fsync(fout.fileno())
        if stats is not None:
            stats.file_read("src")
        res.sha256 = h.hexdigest()
        try:
            shutil.copystat(long_path(src), long_path(tmp), follow_symlinks=True)
        except OSError:
            pass  # 시간/속성 복사 실패는 내용 정확성과 무관(다음 검사는 해시로 한다)
        where = str(tmp)
        if sha256_file(tmp, chunk, cancel, (lambda n: _count(stats, "dst", n, None))) != res.sha256:
            raise OSError("복사 후 내용 검사 실패")
        if stats is not None:
            stats.file_read("dst")
        where = str(dst)
        if os.path.exists(long_path(dst)):
            bak = _backup_path(dst)
            os.replace(long_path(dst), long_path(bak))
            res.backup = str(bak)
        os.replace(long_path(tmp), long_path(dst))
        res.status = COPIED
    except Cancelled:
        _rm(tmp)
        raise
    except OSError as e:
        f = failure.classify(e, where)
        res.status, res.reason, res.error = FAILED, f.code, f"{f.text} ({where})"
        _rm(tmp)
    return res


def _rm(path: Path) -> None:
    try:
        os.unlink(long_path(path))
    except OSError:
        pass


def repair_file(src: Path, dst: Path) -> FileResult:
    """내용이 어긋난 dst 를 원본에서 다시 복사한다(원본은 읽기만).

    임시 이름(.pcmigrate-part)에 복사해 크기+SHA-256 을 확인한 뒤, 기존 dst 는 .bak 으로 보존하고 교체한다.
    robocopy 는 크기/시간이 같으면 건너뛰므로 이 경로로만 복구할 수 있다.
    """
    src, dst = Path(src), Path(dst)
    res = FileResult(str(src), str(dst), FAILED)
    tmp = dst.with_name(dst.name + PART_SUFFIX)
    try:
        res.size = os.stat(long_path(src)).st_size
        res.sha256 = sha256_file(src)
        shutil.copyfile(long_path(src), long_path(tmp))
        shutil.copystat(long_path(src), long_path(tmp), follow_symlinks=True)
        if os.stat(long_path(tmp)).st_size != res.size or sha256_file(tmp) != res.sha256:
            raise OSError("재복사 후 크기/해시 검증 실패")
        if os.path.exists(long_path(dst)):
            bak = _backup_path(dst)
            os.replace(long_path(dst), long_path(bak))
            res.backup = str(bak)
        os.replace(long_path(tmp), long_path(dst))
        res.status = COPIED
    except Exception as e:
        res.status = FAILED
        res.error = f"{type(e).__name__}: {e}"
        try:
            os.unlink(long_path(tmp))
        except OSError:
            pass
    return res


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def copy_tree(src: Path, dst: Path, dry_run: bool = False,
              report: Report | None = None) -> Report:
    """src(파일 또는 폴더)를 dst 로 복사한다. 결과는 Report 에 누적."""
    src, dst = Path(src), Path(dst)
    report = report if report is not None else Report(dry_run)
    if not src.exists():
        report.results.append(FileResult(str(src), str(dst), FAILED, error="원본이 없습니다"))
        return report
    if src.is_file():
        target = dst / src.name if dst.is_dir() else dst
        report.results.append(copy_file(src, target, dry_run))
        return report
    if _is_within(dst, src):
        report.results.append(FileResult(str(src), str(dst), FAILED,
                                         error="대상이 원본 폴더 내부에 있습니다"))
        return report

    def on_error(e: OSError) -> None:
        report.results.append(FileResult(str(getattr(e, "filename", src)), "", FAILED,
                                         error=f"{type(e).__name__}: {e}"))

    for root, dirs, files in os.walk(src, onerror=on_error):
        dirs.sort()
        rel = Path(root).relative_to(src)
        for name in sorted(files):
            if name.endswith(PART_SUFFIX):
                continue
            report.results.append(copy_file(Path(root) / name, dst / rel / name, dry_run))
    return report
