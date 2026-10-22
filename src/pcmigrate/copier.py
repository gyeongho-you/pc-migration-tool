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
from dataclasses import asdict, dataclass, field
from pathlib import Path

CHUNK = 1024 * 1024
PART_SUFFIX = ".pcmigrate-part"

# status 값
COPIED = "copied"
SKIPPED_SAME = "skipped_identical"
WOULD_COPY = "would_copy"
WOULD_OVERWRITE = "would_overwrite"
FAILED = "failed"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
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


@dataclass
class Report:
    dry_run: bool
    results: list[FileResult] = field(default_factory=list)

    @property
    def failures(self) -> list[FileResult]:
        return [r for r in self.results if r.status == FAILED]

    def to_dict(self) -> dict:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {"dry_run": self.dry_run, "summary": counts,
                "failed_count": len(self.failures),
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
