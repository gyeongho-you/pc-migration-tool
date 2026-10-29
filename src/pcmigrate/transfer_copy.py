"""transfer 분해 모듈: 복사 전 단계: dry-run 미리보기, robocopy 복사 호출, 대상 연결 감시.

공개 이름은 transfer.py 파사드가 그대로 다시 내보낸다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from . import fs, split
from .cancel import Cancelled
from .copier import FileResult, Report, SKIPPED_SAME, WOULD_COPY, WOULD_OVERWRITE
from .fs import long_path
from .progress import fmt_bytes
from .scan import ScanInfo
from .transfer_base import MAX_DETAIL_RESULTS, MAX_XF_CHARS, Runner, _bak_name, _join
from .transfer_preserve import DestScan, _scan_dest
from .transfer_robocopy import _call_runner, base_args, interpret_exit

def _alive_checker(dst: Path, network: bool) -> Callable[[], bool]:
    root = fs.volume_root(dst)

    def alive() -> bool:
        try:
            return os.path.isdir(long_path(root if not network else str(dst)))
        except OSError:
            return False
    return alive


def _dry_run(report: Report, entries, skipped_cloud, scan: ScanInfo, dst: Path, runner, copy_mode: str, network: bool,
             warn: Callable[[str], None] = lambda m: None, dscan: DestScan | None = None,
             sctx: "SplitCtx | None" = None, junk_spec: "junk_mod.JunkSpec | None" = None) -> Report:
    """robocopy /L 은 목록 확인용, 리포트는 크기 비교로 만든다. 아무것도 쓰지 않는다. entries 는 분할 대상을 뺀 파일 목록이다."""
    args = base_args(Path(scan.root), dst, None, True, copy_mode, xf_extra=junk_spec.xf if junk_spec else (), xd=junk_spec.xd if junk_spec else ())
    skipped_names = list(skipped_cloud) + (sctx.entries if sctx else [])
    xf = sorted({os.path.basename(scan.abs_path(r)) for r, _s, _m in skipped_names})
    if xf and sum(len(n) + 3 for n in xf) <= MAX_XF_CHARS:
        args += xf  # 너무 길면 생략: 목록 확인용일 뿐 리포트는 별도로 만든다
    code, _ = _call_runner(runner, args, None)
    ok, notes = interpret_exit(code)
    report.meta.update(robocopy_exit=code, robocopy_ok=ok, robocopy_notes=notes)
    detail_ok = len(entries) <= MAX_DETAIL_RESULTS
    over = 0
    if dscan is None:
        dscan = _scan_dest(entries, dst, sum(e[1] for e in entries))
    for (rel, size, mtime), state in zip(entries, dscan.states):
        target = _join(str(dst), rel)
        status = {"n": WOULD_COPY, "s": SKIPPED_SAME, "d": WOULD_OVERWRITE}[state]
        res = FileResult(scan.abs_path(rel), target, status, size)
        if status == WOULD_OVERWRITE:
            over += 1
            if over <= MAX_DETAIL_RESULTS:
                res.backup = _bak_name(target)  # 실제 복사 때 기존 파일이 옮겨질 이름
        if status in (WOULD_COPY, SKIPPED_SAME) and not detail_ok:
            report.counts[status] = report.counts.get(status, 0) + 1
        else:
            report.results.append(res)
    if sctx is not None:  # 분할 대상: 조각 이름으로 새로 만든다(같은 이름의 조각이 있으면 실제 복사 때 .bak 으로 보존)
        for rel, size, mtime in sctx.entries:
            target = _join(str(dst), rel)
            n_parts = len(split.part_sizes(size, sctx.psz))
            same = sum(1 for i in range(1, n_parts + 1) if os.path.lexists(long_path(target + split.part_suffix(i))))
            res = FileResult(scan.abs_path(rel), target + split.part_suffix(1) + (f" 외 {n_parts - 1}개" if n_parts > 1 else ""),
                             WOULD_OVERWRITE if same else WOULD_COPY, size)
            if same:
                over += 1
                res.backup = f"같은 이름의 조각 {same}개는 덮어쓰기 전에 .bak 으로 보존됩니다(내용이 같으면 건너뜁니다)"
            report.results.append(res)
    report.meta["would_overwrite_count"] = over
    if over:
        warn(f"대상에 같은 이름의 다른 파일 {over}개가 이미 있습니다. 실제 복사 때는 덮어쓰기 전에 각 파일을 같은 폴더의 .bak 으로 보존합니다"
             f"(이름만 바꾸므로 시간은 거의 안 들지만, 기존 파일 {dscan.bak_count}개({fmt_bytes(dscan.bak_bytes)})가 .bak 으로 남아 "
             "공간을 계속 차지하고 새 파일 크기만큼 여유 공간이 더 필요합니다). 확인하고 진행하세요.")
    report.meta["would_preserve_bytes"] = dscan.bak_bytes
    return report


def _copy_with_robocopy(src: Path, dst: Path, log: Path, cloud: list, runner: Runner, cancel, copy_mode: str,
                        retries: int, wait: int, on_line, scan: ScanInfo, alive, threads: int = 1,
                        junk_spec: "junk_mod.JunkSpec | None" = None) -> tuple[int, str, int]:
    """robocopy 실행. (최악 종료코드, 오류 줄 모음, 실행 횟수). 클라우드 전용 파일은 /XF 로 제외."""
    jxf, jxd = (junk_spec.xf, junk_spec.xd) if junk_spec else ((), ())
    if not cloud:
        code, text = _call_runner(runner, base_args(src, dst, log, False, copy_mode, retries, wait, threads, jxf, jxd), on_line, alive)
        return code, text, 1
    # 폴더별 실행: /LEV:1 + 해당 폴더의 클라우드 파일명만 /XF (전체 경로를 한 명령줄에 넣지 않는다)
    skip_by_dir: dict[str, list[str]] = {}
    for rel, _s, _m in cloud:
        d, _, name = rel.rpartition("/")
        skip_by_dir.setdefault(d, []).append(name)
    worst = runs = 0
    texts: list[str] = []
    for root, dirs, _files in os.walk(src, followlinks=False):
        if cancel is not None and cancel():
            raise Cancelled()
        dirs.sort()
        dirs[:] = [d for d in dirs if not fs.is_reparse(os.path.join(root, d))]  # robocopy /XJ 와 같은 기준: 정션/링크는 따라가지 않는다
        if junk_spec is not None:
            dirs[:] = [d for d in dirs if not junk_spec.skip_dir(d)]  # 정크 폴더는 내려가지 않는다(/XD 와 같은 기준)
        relp = os.path.relpath(root, src)
        key = "" if relp == "." else relp.replace(os.sep, "/")
        args = base_args(Path(root), dst / ("" if relp == "." else relp), log, False, copy_mode, retries, wait, threads, jxf, jxd)
        args[args.index("/E")] = "/LEV:1"
        if key in skip_by_dir:
            args += skip_by_dir[key]
        code, text = _call_runner(runner, args, on_line, alive)
        worst = max(worst, code)
        texts.append(text)
        runs += 1
    return worst, "\n".join(texts), runs
