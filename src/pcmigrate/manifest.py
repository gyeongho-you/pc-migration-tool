"""해시 목록(manifest): 양쪽 PC 가 각자 자기 로컬 디스크를 읽어 만든 해시 목록을 주고받아 비교한다.

네트워크(공유 폴더)로 복사할 때 복사본을 네트워크로 다시 읽지 않기 위한 방식이다.
- 구 PC: 원본(로컬)을 읽어 파일별 상대 경로/크기/수정 시각/SHA-256 을 JSONL(줄 단위 JSON)로 스트리밍해 쓴다.
- 새 PC: 목록과 로컬 대상 폴더를 비교한다(누락/내용 다름/추가 파일). 대상 폴더는 읽기만 하고 아무것도 쓰지 않는다.

파일 형식(.jsonl, UTF-8): 첫 줄 헤더 {"format","version","created","source_root","total_files","total_bytes","layout"},
파일마다 한 줄 {"rel","size","mtime_ns","sha256"}, 마지막 줄 {"end":true,"files","bytes","errors"}.
버전 2 부터 4GB 분할 파일(--split-large)은 줄에 "parts":[{"name","size","sha256"}] 가 더 붙는다. sha256 은 원본 전체의 해시이고,
parts 는 같은 폴더에 있는 조각 파일(<이름>.pcm001 ...)의 이름/크기/해시다. 버전 1 목록도 읽을 수 있다.
마지막 줄이 없으면(중간에 끊김) verify 가 불완전한 목록으로 알려 준다. 쓰는 동안은 .part 이름이고 끝나면 이름을 바꾼다.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

from . import __version__, failure, split
from .cancel import Cancelled
from .copier import (EXTRA, FAILED, MISMATCH, MISSING, VERIFIED, FileResult, IoStats, Report, missing_dest, sha256_file)
from .engine import clamp_workers, run_ordered
from .fs import long_path
from .progress import OK, ProgressInfo, ProgressTracker, categorize, format_info
from .scan import ScanInfo, scan_source_info

MANIFEST_FORMAT = "pcmigrate-manifest"
MANIFEST_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)  # 1: 분할 파일 없음. 2: 줄에 parts 가 있을 수 있음
DEFAULT_NAME = ".pcmigrate-manifest.jsonl"
OWN_PREFIX = ".pcmigrate-"  # 이 도구가 대상 폴더에 만드는 기록 파일(상태, 로그, 목록)은 '추가 파일'로 세지 않는다
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """목록 파일이 잘못되었거나 안전하지 않음."""


NOT_UTF8 = "목록 파일을 읽을 수 없습니다(UTF-8 텍스트가 아닙니다). 이 도구가 만든 .jsonl 파일이 맞는지 확인하세요."


def _count_field(header: dict, key: str) -> int:
    """헤더의 개수 필드. 숫자가 아니면 목록 파일 오류."""
    v = header.get(key) or 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ManifestError(f"목록 파일의 {key} 값이 올바르지 않습니다")
    return v


def _safe_rel(rel: object) -> str:
    if not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        raise ManifestError(f"안전하지 않은 경로: {rel!r}")
    if any(part in ("", ".", "..") for part in PurePosixPath(rel).parts):
        raise ManifestError(f"안전하지 않은 경로: {rel!r}")
    return rel


def _check_parts(row: dict, n: int) -> None:
    """분할 파일 줄의 parts 검사: 조각 이름은 원래 파일 이름 + .pcmNNN(경로 구분자 없음), 크기 합 == 원본 크기, 해시는 64자리 16진수."""
    parts = row["parts"]
    bad = ManifestError(f"{n}번째 줄의 조각 목록(parts)이 올바르지 않습니다")
    if not isinstance(parts, list) or not parts:
        raise bad
    base = row["rel"].rsplit("/", 1)[-1]
    total = 0
    for i, p in enumerate(parts, 1):
        if not isinstance(p, dict):
            raise bad
        name, size, sha = p.get("name"), p.get("size"), p.get("sha256")
        if (not isinstance(name, str) or name != f"{base}.pcm{i:03d}" or isinstance(size, bool) or not isinstance(size, int)
                or size < 0 or not isinstance(sha, str) or not _HEX64.match(sha)):
            raise bad
        total += size
    if total != row["size"]:
        raise bad


class ManifestWriter:
    """JSONL 스트리밍 쓰기. close() 가 끝줄을 쓰고 이름을 바꾼다. 중간에 끊기면 .part 만 남아 불완전 목록이 완성본으로 오해되지 않는다."""

    def __init__(self, path: Path | str, source_root: str, total_files: int, total_bytes: int, layout: dict | None = None,
                 flush_every: int = 500) -> None:
        self.path = Path(path)
        self.tmp = self.path.with_name(self.path.name + ".part")
        self.files = self.bytes = self.errors = 0
        self.flush_every = flush_every
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.tmp, "w", encoding="utf-8", newline="\n")
        header = {"format": MANIFEST_FORMAT, "version": MANIFEST_VERSION, "tool_version": __version__,
                  "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source_root": source_root,
                  "total_files": total_files, "total_bytes": total_bytes, "layout": layout or {}}
        self._f.write(json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "\n")

    def write_row(self, rel: str, size: int, mtime_ns: int, sha256: str, parts: list[dict] | None = None) -> None:
        row = {"rel": rel, "size": size, "mtime_ns": mtime_ns, "sha256": sha256}
        if parts:
            row["parts"] = parts
        self._f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.files += 1
        self.bytes += size
        if self.files % self.flush_every == 0:
            self._f.flush()

    def note_error(self) -> None:
        self.errors += 1

    def close(self) -> Path:
        self._f.write(json.dumps({"end": True, "files": self.files, "bytes": self.bytes, "errors": self.errors},
                                 separators=(",", ":")) + "\n")
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()
        os.replace(self.tmp, self.path)
        return self.path

    def abort(self) -> None:
        try:
            self._f.close()
        except OSError:
            pass


@dataclass
class ManifestReader:
    """JSONL 목록을 줄 단위로 읽는다(메모리에 통째로 올리지 않는다). 순회가 끝나면 footer 를 알 수 있다."""
    path: Path
    header: dict = field(default_factory=dict)
    footer: dict | None = None

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        try:
            with open(long_path(self.path), "r", encoding="utf-8") as f:
                first = f.readline()
        except OSError as e:
            raise ManifestError(f"목록 파일을 읽을 수 없습니다: {e}") from e
        except UnicodeDecodeError as e:
            raise ManifestError(NOT_UTF8) from e
        try:
            self.header = json.loads(first)
        except (ValueError, RecursionError) as e:  # 너무 깊게 중첩된 JSON 은 RecursionError
            raise ManifestError("목록 파일의 첫 줄이 올바르지 않습니다") from e
        if not isinstance(self.header, dict):
            raise ManifestError("목록 파일의 첫 줄이 올바르지 않습니다(목록 파일이 아닌 JSON 입니다)")
        if self.header.get("format") != MANIFEST_FORMAT or self.header.get("version") not in SUPPORTED_VERSIONS:
            raise ManifestError("지원하지 않는 목록 파일입니다(형식 또는 버전이 다름)")

    def rows(self) -> Iterator[dict]:
        try:
            yield from self._rows()
        except UnicodeDecodeError as e:
            raise ManifestError(NOT_UTF8) from e

    def _rows(self) -> Iterator[dict]:
        self.footer = None
        with open(long_path(self.path), "r", encoding="utf-8") as f:
            f.readline()  # 헤더
            for n, line in enumerate(f, 2):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # 끝이 잘린 줄은 무시하고, footer 가 없으므로 불완전 목록으로 보고된다
                except RecursionError as e:
                    raise ManifestError(f"{n}번째 줄의 항목이 올바르지 않습니다") from e
                if not isinstance(row, dict):
                    raise ManifestError(f"{n}번째 줄의 항목이 올바르지 않습니다")
                if row.get("end") is True:
                    self.footer = row
                    continue
                _safe_rel(row.get("rel"))
                if not (isinstance(row.get("size"), int) and isinstance(row.get("sha256"), str) and _HEX64.match(row["sha256"])):
                    raise ManifestError(f"{n}번째 줄의 항목이 올바르지 않습니다")
                if "parts" in row:
                    _check_parts(row, n)
                yield row


@dataclass
class ManifestResult:
    path: Path
    files: int
    bytes: int
    errors: list[FileResult]
    seconds: float
    io: dict
    scan: ScanInfo


def build_manifest(src: Path | str, out: Path | str, *, workers: int | None = None, chunk: int | None = None,
                   cancel: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
                   progress_info: Callable[[ProgressInfo], None] | None = None,
                   cached_hash: Callable[[str, int, int], str | None] | None = None,
                   scan: ScanInfo | None = None, clock: Callable[[], float] = time.monotonic,
                   min_interval: float = 1.0) -> ManifestResult:
    """원본(로컬)을 읽어 해시 목록 파일을 만든다. 원본은 읽기만 한다.

    cached_hash(rel, size, mtime_ns) 가 해시를 돌려주면(상태 파일에 같은 크기/수정시각으로 기록돼 있을 때) 다시 읽지 않는다.
    읽지 못한 파일은 목록에 넣지 않고 errors 로 돌려주며 목록 끝줄의 errors 개수에도 기록한다.
    """
    src = Path(src)
    if not src.is_dir():
        raise ManifestError(f"원본 폴더가 없습니다: {src}")
    t0 = clock()
    info = scan or scan_source_info(src, progress=progress, progress_info=progress_info, cancel=cancel)
    writer = ManifestWriter(out, info.root, info.total_files, info.total_bytes, info.layout())
    stats = IoStats()
    tracker = ProgressTracker("manifest", info.total_bytes, info.total_files, clock=clock)
    errors: list[FileResult] = [FileResult(info.abs_path(rel), "", FAILED, reason=code, error=text)
                                for rel, code, text in info.errors]
    for _ in errors:
        writer.note_error()
    last = [clock()]

    def task(entry):
        rel, size, mtime = entry
        tracker.set_current(info.abs_path(rel))
        cached = cached_hash(rel, size, mtime) if cached_hash else None
        if cached:
            stats.reused()
            return cached, None
        try:
            digest = sha256_file(info.abs_path(rel), chunk, cancel, lambda n: (stats.add("src", n), tracker.add(n)))
            stats.file_read("src")
            return digest, None
        except Cancelled:
            raise
        except OSError as e:
            f = failure.classify(e, rel)
            return None, FileResult(info.abs_path(rel), "", FAILED, size, reason=f.code, error=f"{f.text} ({rel})")

    def handle(entry, result):
        rel, size, mtime = entry
        digest, err = result
        if digest:
            writer.write_row(rel, size, mtime, digest)
            tracker.note(OK)
        else:
            errors.append(err)
            writer.note_error()
            tracker.note(categorize(err.status, err.reason), err.src)
        tracker.add(0, 1)
        now = clock()
        if (progress or progress_info) and now - last[0] >= min_interval:
            last[0] = now
            snap = tracker.snapshot()
            if progress_info:
                progress_info(snap)
            if progress:
                progress(format_info(snap))

    try:
        run_ordered(info.entries, task, handle, workers=clamp_workers(workers), cancel=cancel)
    except BaseException:
        writer.abort()
        raise
    path = writer.close()
    return ManifestResult(path, writer.files, writer.bytes, errors, clock() - t0, stats.as_dict(), info)


def _check_split_row(row: dict, dst: str, res: FileResult, chunk: int | None, cancel, on_bytes, stats: IoStats) -> FileResult | None:
    """분할 파일 줄: 대상에 조각(<이름>.pcm001 ...)이 있으면 조각을 순서대로 이어 읽어 조각별/전체 해시를 목록과 비교한다.
    첫 조각이 없으면 None(합쳐진 파일로 보고 일반 검사로 넘어간다)."""
    folder = os.path.dirname(dst)
    paths = [os.path.join(folder, p["name"]) for p in row["parts"]]
    if not os.path.lexists(long_path(paths[0])):
        return None
    where = paths[0]
    try:
        for i, (p, path) in enumerate(zip(row["parts"], paths), 1):
            where = path
            try:
                size = os.stat(long_path(path)).st_size
            except FileNotFoundError:
                res.status, res.reason = MISSING, failure.MISSING_DEST
                res.error = f"조각 {i:03d}/{len(paths):03d}({p['name']})이 없습니다. 복사가 덜 됐을 수 있습니다(같은 명령을 다시 실행하면 이어받습니다)"
                return res
            if not os.path.isfile(long_path(path)) or size != p["size"]:
                res.status, res.reason = MISMATCH, failure.SIZE_MISMATCH
                res.error = f"조각 {i:03d}({p['name']})의 크기가 원본과 다릅니다(원본 {p['size']}바이트, 여기 {size}바이트)"
                return res
        whole, per = split.hash_parts(paths, chunk=chunk, cancel=cancel, on_bytes=lambda n: (stats.add("dst", n), on_bytes(n)))
        stats.file_read("dst")
        for i, (p, h) in enumerate(zip(row["parts"], per), 1):
            if h != p["sha256"]:
                res.status, res.reason = MISMATCH, failure.MISMATCH
                res.error = f"조각 {i:03d}({p['name']})의 내용이 원본과 다릅니다(SHA-256 불일치). 그 조각만 다시 복사하세요"
                return res
        if whole != row["sha256"]:
            res.status, res.reason = MISMATCH, failure.MISMATCH
            res.error = failure.REASONS[failure.MISMATCH].text + " (조각을 이어 붙인 전체 SHA-256 불일치)"
            return res
        res.status = VERIFIED
    except Cancelled:
        raise
    except OSError as e:
        f = failure.classify(e, where)
        res.status, res.reason, res.error = FAILED, f.code, f"{f.text} ({where})"
    return res


def _check_row(row: dict, target: str, chunk: int | None, cancel, on_bytes, stats: IoStats) -> FileResult:
    rel = row["rel"]
    dst = os.path.join(target, *rel.split("/"))
    res = FileResult(f"[목록] {rel}", dst, FAILED, row["size"], row["sha256"])
    where = dst
    if row.get("parts"):
        done = _check_split_row(row, dst, res, chunk, cancel, on_bytes, stats)
        if done is not None:
            return done
    try:
        try:
            size = os.stat(long_path(dst)).st_size
        except FileNotFoundError:
            return missing_dest(res, None, Path(dst), "새 PC 로 복사가 덜 됐을 수 있습니다.")
        if not os.path.isfile(long_path(dst)):
            return missing_dest(res, None, Path(dst))
        if size != row["size"]:
            res.status, res.reason = MISMATCH, failure.SIZE_MISMATCH
            res.error = f"{failure.REASONS[failure.SIZE_MISMATCH].text} (원본 {row['size']}바이트, 여기 {size}바이트)"
            return res
        digest = sha256_file(dst, chunk, cancel, lambda n: (stats.add("dst", n), on_bytes(n)))
        stats.file_read("dst")
        if digest != row["sha256"]:
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


def verify_against_manifest(manifest: Path | str, target: Path | str, *, workers: int | None = None, chunk: int | None = None,
                            cancel: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
                            progress_info: Callable[[ProgressInfo], None] | None = None,
                            clock: Callable[[], float] = time.monotonic, min_interval: float = 1.0,
                            find_extras: bool = True) -> Report:
    """새 PC 에서: 해시 목록과 로컬 대상 폴더를 비교한다. 대상 폴더는 읽기만 한다(아무것도 쓰지 않는다).

    - 목록에 있는데 없으면 missing, 크기/내용이 다르면 mismatch, 읽지 못하면 failed(원인 분류), 맞으면 verified.
    - 목록에 없는 파일은 extra(추가 파일)로 알린다(실패로 세지 않음). 이 도구의 기록 파일(.pcmigrate-*)은 제외한다.
    - 목록이 중간에 끊겼으면 실패로 알린다(비교할 수 없는 파일이 있을 수 있음).
    """
    target = os.path.abspath(str(target))
    if not os.path.isdir(long_path(target)):
        raise ManifestError(f"대상 폴더가 없습니다: {target}")
    reader = ManifestReader(Path(manifest))
    header = reader.header
    total_bytes, total_files = _count_field(header, "total_bytes"), _count_field(header, "total_files")
    report = Report(dry_run=False)
    stats = IoStats()
    tracker = ProgressTracker("verify", total_bytes, total_files, clock=clock)
    expected: set[str] = set()
    last = [clock()]
    t0 = clock()

    def emit(force: bool = False) -> None:
        now = clock()
        if (progress or progress_info) and (force or now - last[0] >= min_interval):
            last[0] = now
            snap = tracker.snapshot()
            if progress_info:
                progress_info(snap)
            if progress:
                progress(format_info(snap))

    def task(row):
        tracker.set_current(os.path.join(target, *row["rel"].split("/")))
        return _check_row(row, target, chunk, cancel, lambda n: tracker.add(n), stats)

    def handle(row, res):
        expected.add(row["rel"].casefold())
        for p in row.get("parts") or ():  # 조각 파일도 '목록에 있는 파일'이다(추가 파일로 세지 않는다)
            expected.add((row["rel"].rpartition("/")[0] + "/" + p["name"] if "/" in row["rel"] else p["name"]).casefold())
        report.results.append(res)
        tracker.add(0, 1)
        tracker.note(categorize(res.status, res.reason), res.dst or res.src)
        emit()

    run_ordered(reader.rows(), task, handle, workers=clamp_workers(workers), cancel=cancel)
    complete = reader.footer is not None
    extras = 0
    if find_extras:
        for rel in _walk_files(target, cancel):
            if rel.casefold() in expected or os.path.basename(rel).startswith(OWN_PREFIX):
                continue
            extras += 1
            report.results.append(FileResult(f"[목록에 없음] {rel}", os.path.join(target, *rel.split("/")), EXTRA,
                                             error="목록에 없는 추가 파일입니다(실패는 아닙니다)."))
    if not complete:
        report.results.append(FileResult(str(manifest), "", FAILED, reason="manifest_incomplete",
                                         error="목록 파일이 끝까지 만들어지지 않았습니다. 구 PC 에서 목록을 다시 만든 뒤 비교하세요."))
    # 구 PC 에서 읽지 못해 목록에서 빠진 파일(footer.errors)이 있으면 '실패 0'으로 보이면 안 된다(A-5).
    footer_errors = reader.footer.get("errors") if reader.footer else 0
    if isinstance(footer_errors, int) and not isinstance(footer_errors, bool) and footer_errors > 0:
        f = failure.REASONS[failure.MANIFEST_ERRORS]
        report.results.append(FileResult(str(manifest), "", FAILED, reason=f.code,
                                         error=f"{f.text} (목록에 빠진 파일 {footer_errors}개)"))
    else:
        footer_errors = 0
    emit(True)
    report.meta.update(mode="verify", manifest_errors=footer_errors, manifest=str(manifest), target=target, manifest_complete=complete,
                       manifest_created=header.get("created", ""), source_root=header.get("source_root", ""),
                       source_layout=header.get("layout", {}), expected_files=len(expected), extra_files=extras,
                       seconds=round(clock() - t0, 2), io=stats.as_dict(), read_only=True)
    return report


def _walk_files(root: str, cancel: Callable[[], bool] | None) -> Iterator[str]:
    """대상 폴더의 파일을 상대 경로(posix)로 돌려준다(링크는 따라가지 않음, 읽기 전용)."""
    stack = [(root, "")]
    while stack:
        if cancel is not None and cancel():
            raise Cancelled()
        path, relp = stack.pop()
        try:
            with os.scandir(long_path(path)) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        subdirs = []
        for e in entries:
            rel = f"{relp}/{e.name}" if relp else e.name
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    subdirs.append((os.path.join(path, e.name), rel))
                else:
                    yield rel
            except OSError:
                continue
        stack.extend(reversed(subdirs))
