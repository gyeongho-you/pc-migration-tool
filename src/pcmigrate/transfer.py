"""모드 B: 대용량 데이터 직접 전송(robocopy 래퍼 + SHA-256 검증 + 이어받기).

- 복사는 Windows 내장 robocopy 로 하고, 끝난 뒤 원본/대상을 모두 읽어 크기 + SHA-256 을 비교한다.
- 원본은 읽기만 한다. 원본을 바꿀 수 있는 옵션(/MIR /PURGE /MOV /MOVE)은 코드로 금지한다.
- 대상 폴더의 .pcmigrate-state.json 에 검증 완료 파일을 기록해 재실행 시 재해시를 건너뛴다.
  상태 파일이 손상/불일치하면 무시하고 전체를 다시 검증한다.
- OneDrive 클라우드 전용(온디맨드) 파일은 기본으로 건너뛰고 cloud_only 로 기록한다.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from .copier import (CLOUD_ONLY, FAILED, MISMATCH, SKIPPED_SAME, VERIFIED, WOULD_COPY, repair_file,
                     WOULD_OVERWRITE, FileResult, Report, verify_file)

STATE_NAME = ".pcmigrate-state.json"
LOG_NAME = ".pcmigrate-robocopy.log"
STATE_VERSION = 1
STATE_FLUSH_EVERY = 50

# 원본/대상을 삭제·이동·동기화(삭제)할 수 있는 옵션. 접두 일치로 /MOV:, /MOVE 모두 차단.
FORBIDDEN_PREFIXES = ("/MIR", "/PURGE", "/MOV")

# st_file_attributes 비트
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
CLOUD_ATTR_MASK = FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

MTIME_TOLERANCE_NS = 2_000_000_000  # FAT 계열 2초 해상도
MAX_XF_CHARS = 6000  # dry-run 목록용 /XF 이름 합계 상한(명령줄 길이 제한 회피)

# runner(args) -> (returncode, output)
Runner = Callable[[list[str]], tuple[int, str]]
AttrGetter = Callable[[Path], int]


class TransferError(ValueError):
    """전송을 시작할 수 없는 입력/설정 오류."""


def default_runner(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True)
    try:
        text = proc.stdout.decode("oem", errors="replace")
    except LookupError:
        text = proc.stdout.decode("utf-8", errors="replace")
    return proc.returncode, text


def validate_args(args: list[str]) -> None:
    """원본 보호: 금지 옵션이 하나라도 있으면 실행 전에 거부한다."""
    for a in args[1:]:
        up = a.upper()
        if any(up.startswith(p) for p in FORBIDDEN_PREFIXES):
            raise TransferError(f"금지된 robocopy 옵션: {a}")


def base_args(src: Path, dst: Path, log: Path | None, dry_run: bool) -> list[str]:
    args = ["robocopy", str(src), str(dst), "/E", "/Z", "/R:3", "/W:5",
            "/COPY:DAT", "/DCOPY:DAT", "/NP", "/XJ"]
    if dry_run:
        args.append("/L")  # 목록만. 로그 파일도 쓰지 않는다.
    elif log is not None:
        args.append(f"/LOG+:{log}")
    return args


def interpret_exit(code: int) -> tuple[bool, list[str]]:
    """robocopy 종료코드(비트마스크): 0~7 성공/경고, 8 이상 실패."""
    notes = []
    for bit, text in ((1, "파일 복사됨"), (2, "대상에 추가 파일/폴더 있음(삭제하지 않음)"),
                      (4, "불일치 파일/폴더 있음"), (8, "일부 복사 실패"), (16, "치명적 오류")):
        if code & bit:
            notes.append(text)
    return code < 8, notes


def default_attr_getter(path: Path) -> int:
    return getattr(os.stat(path), "st_file_attributes", 0)


def is_cloud_only(attrs: int) -> bool:
    return bool(attrs & CLOUD_ATTR_MASK)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(child)).relative_to(os.path.realpath(parent))
        return True
    except ValueError:
        return False


def scan_source(src: Path, attr_getter: AttrGetter = default_attr_getter
                ) -> tuple[list[Path], list[Path]]:
    """원본을 읽기만 하며 (일반 파일 목록, 클라우드 전용 파일 목록)을 반환한다."""
    normal: list[Path] = []
    cloud: list[Path] = []
    for root, dirs, files in os.walk(src, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            p = Path(root) / name
            if p.is_symlink():
                continue
            try:
                attrs = attr_getter(p)
            except OSError:
                attrs = 0
            (cloud if is_cloud_only(attrs) else normal).append(p)
    return normal, cloud


# --------------------------------------------------------------------------- 상태 파일

class State:
    """이어받기용 진행 상태. 손상/불일치 시 비어 있는 상태로 시작한다."""

    def __init__(self, path: Path, src: Path):
        self.path = path
        self.src = str(src)
        self.files: dict[str, dict] = {}
        self.reset_reason = ""
        self._dirty = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            files = data["files"]
            if data["version"] != STATE_VERSION or data["src"] != self.src or not isinstance(files, dict):
                raise ValueError("버전/원본 경로 불일치")
            for rel, e in files.items():
                if not (isinstance(rel, str) and isinstance(e["size"], int)
                        and isinstance(e["mtime_ns"], int) and isinstance(e["sha256"], str)
                        and isinstance(e["verified"], bool)):
                    raise ValueError("항목 형식 오류")
            self.files = files
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.files = {}
            self.reset_reason = f"상태 파일 무시(전체 재검증): {type(e).__name__}: {e}"

    def is_verified(self, rel: str, src: Path, dst: Path) -> str | None:
        """검증 완료로 건너뛸 수 있으면 sha256, 아니면 None."""
        e = self.files.get(rel)
        if not e or not e["verified"]:
            return None
        try:
            ss, ds = src.stat(), dst.stat()
        except OSError:
            return None
        if (ss.st_size == e["size"] and ss.st_mtime_ns == e["mtime_ns"]
                and ds.st_size == e["size"]
                and abs(ds.st_mtime_ns - ss.st_mtime_ns) <= MTIME_TOLERANCE_NS):
            return e["sha256"]
        return None

    def record(self, rel: str, src: Path, sha256: str, verified: bool) -> None:
        try:
            ss = src.stat()
        except OSError:
            return
        self.files[rel] = {"size": ss.st_size, "mtime_ns": ss.st_mtime_ns,
                           "sha256": sha256, "verified": verified}
        self._dirty += 1
        if self._dirty >= STATE_FLUSH_EVERY:
            self.save()

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".part")
        data = {"version": STATE_VERSION, "src": self.src, "files": self.files}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = 0


def _repair_and_reverify(src: Path, dst: Path, before, report: Report):
    """strict 에서 불일치로 판정된 파일만 재복사하고 다시 해시 검증한다. 실패하면 원래 불일치 결과를 유지."""
    rep = repair_file(src, dst)
    if rep.status == FAILED:
        before.error = f"{before.error}; 재복사 실패: {rep.error}"
        return before
    after = verify_file(src, dst)
    after.backup = rep.backup
    if after.status == VERIFIED:
        report.meta.setdefault("repaired", []).append({"file": str(dst), "backup": rep.backup,
                                                       "reason": before.error})
    return after


def _state_conflict(state: State, rel: str, src: Path, sha256: str) -> bool:
    """상태 파일이 '검증 완료'로 기록한 해시가 지금 원본 해시와 다른데 크기/mtime 은 같은 경우."""
    e = state.files.get(rel)
    if not e or not e["verified"] or e["sha256"] == sha256:
        return False
    try:
        ss = src.stat()
    except OSError:
        return False
    return ss.st_size == e["size"] and ss.st_mtime_ns == e["mtime_ns"]


# --------------------------------------------------------------------------- 전송

def _run(runner: Runner, args: list[str]) -> int:
    validate_args(args)
    code, _ = runner(args)
    return code


def _copy_with_robocopy(src: Path, dst: Path, log: Path, cloud: list[Path],
                        runner: Runner) -> tuple[int, int]:
    """robocopy 실행. (최악 종료코드, 실행 횟수). 클라우드 전용 파일은 /XF 로 제외."""
    if not cloud:
        return _run(runner, base_args(src, dst, log, False)), 1
    # 폴더별 실행: /LEV:1 + 해당 폴더의 클라우드 파일명만 /XF (전체 경로를 한 명령줄에 넣지 않는다)
    skip_by_dir: dict[Path, list[str]] = {}
    for p in cloud:
        skip_by_dir.setdefault(p.parent, []).append(p.name)
    worst = runs = 0
    for root, dirs, _files in os.walk(src, followlinks=False):
        dirs.sort()
        rel = Path(root).relative_to(src)
        args = base_args(Path(root), dst / rel, log, False)
        args[args.index("/E")] = "/LEV:1"
        if Path(root) in skip_by_dir:
            args += ["/XF", *skip_by_dir[Path(root)]]
        worst = max(worst, _run(runner, args))
        runs += 1
    return worst, runs


def transfer(src: Path, dst: Path, *, dry_run: bool = False, include_cloud: bool = False,
             runner: Runner = default_runner, attr_getter: AttrGetter = default_attr_getter,
             log_path: Path | None = None, warn: Callable[[str], None] = lambda m: None,
             strict: bool = False, state_path: Path | None = None) -> Report:
    """src 폴더를 dst 로 robocopy 복사하고 SHA-256 으로 검증한다.

    strict=True 이면 상태 파일의 크기/mtime 일치를 믿지 않고 모든 파일을 다시 해시 검증하며,
    상태 파일에 기록된 해시와 어긋나면 불일치로 보고한다. 해시가 어긋난 파일은(robocopy 는 크기/시간이 같으면
    건너뛰므로) 원본에서 그 파일만 다시 복사해 교체하고(기존 대상 파일은 .bak 보존) 다시 검증한다.
    state_path 로 진행 상태 파일 위치를 바꿀 수 있다(기본: 대상 폴더 안 .pcmigrate-state.json).
    """
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise TransferError(f"원본 폴더가 없습니다: {src}")
    if os.path.realpath(src) == os.path.realpath(dst) or _is_within(dst, src):
        raise TransferError("대상은 원본과 같거나 원본 폴더 안에 있을 수 없습니다")

    normal, cloud = scan_source(src, attr_getter)
    report = Report(dry_run=dry_run)
    skipped_cloud = [] if include_cloud else cloud
    to_copy = normal + (cloud if include_cloud else [])
    if cloud:
        warn(f"OneDrive 클라우드 전용 파일 {len(cloud)}개 감지: "
             + ("--include-cloud 로 포함됩니다(대량 다운로드 주의)." if include_cloud
                else "기본으로 건너뜁니다."))
        for p in cloud[:20]:
            warn(f"  클라우드 전용: {p}")
        if len(cloud) > 20:
            warn(f"  ... 외 {len(cloud) - 20}개")
    for p in skipped_cloud:
        report.results.append(FileResult(str(p), "", CLOUD_ONLY, error="클라우드 전용 파일(건너뜀)"))
    report.meta["cloud_only_count"] = len(cloud)
    report.meta["include_cloud"] = include_cloud
    report.meta["strict"] = strict

    if dry_run:
        # robocopy /L 은 목록 확인용, 리포트는 크기 비교로 만든다. 아무것도 쓰지 않는다.
        args = base_args(src, dst, None, True)
        xf = sorted({p.name for p in skipped_cloud})
        if xf and sum(len(n) + 3 for n in xf) <= MAX_XF_CHARS:
            args += ["/XF", *xf]  # 너무 길면 생략: 목록 확인용일 뿐 리포트는 별도로 만든다
        code = _run(runner, args)
        ok, notes = interpret_exit(code)
        report.meta.update(robocopy_exit=code, robocopy_ok=ok, robocopy_notes=notes)
        for p in to_copy:
            target = dst / p.relative_to(src)
            status = WOULD_COPY
            if target.is_file():
                status = (SKIPPED_SAME if target.stat().st_size == p.stat().st_size
                          and abs(target.stat().st_mtime_ns - p.stat().st_mtime_ns) <= MTIME_TOLERANCE_NS
                          else WOULD_OVERWRITE)
            report.results.append(FileResult(str(p), str(target), status, p.stat().st_size))
        return report

    dst.mkdir(parents=True, exist_ok=True)
    log = Path(log_path) if log_path else dst / LOG_NAME
    state = State(Path(state_path) if state_path else dst / STATE_NAME, src)
    report.meta["state_file"] = str(state.path)
    if state.reset_reason:
        report.meta["state_reset"] = state.reset_reason
        warn(state.reset_reason)
    try:
        code, runs = _copy_with_robocopy(src, dst, log, skipped_cloud, runner)
        ok, notes = interpret_exit(code)
        report.meta.update(robocopy_exit=code, robocopy_ok=ok, robocopy_notes=notes,
                           robocopy_runs=runs, robocopy_log=str(log))
        if not ok:
            report.results.append(FileResult(str(src), str(dst), FAILED,
                                             error=f"robocopy 종료코드 {code}: {', '.join(notes)}"))
        resumed = 0
        for p in to_copy:
            rel = p.relative_to(src).as_posix()
            target = dst / p.relative_to(src)
            digest = None if strict else state.is_verified(rel, p, target)
            if digest is not None:
                resumed += 1
                report.results.append(FileResult(str(p), str(target), VERIFIED,
                                                 p.stat().st_size, digest))
                continue
            res = verify_file(p, target)
            if strict and res.status == MISMATCH:
                res = _repair_and_reverify(p, target, res, report)
            if strict and res.status == VERIFIED and _state_conflict(state, rel, p, res.sha256):
                res.status = MISMATCH
                res.error = "상태 파일의 검증 해시와 원본이 어긋남(크기/mtime 은 동일)"
            state.record(rel, p, res.sha256, res.status == VERIFIED)
            report.results.append(res)
        report.meta["resumed_skipped"] = resumed
    finally:
        state.save()
    return report
