"""원본 폴더 조사(읽기 전용): 파일 목록, 크기, 총량, 최상위 폴더 구조, 클라우드 전용 파일 구분.

os.scandir 로 한 번 훑으며 크기/수정시각/속성을 함께 얻어 파일마다 stat 을 다시 하지 않는다.
경로는 상대 경로(posix) 문자열로만 들고 있어 파일 수가 수십만~수백만이어도 메모리를 적게 쓴다.
260자 넘는 긴 경로도 ``\\\\?\\`` 접두로 읽는다.
"""
from __future__ import annotations

import ntpath
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import failure
from .cancel import Cancelled
from .fs import long_path
from .progress import FAILED_KIND, MAX_PROBLEM_DELTA, UNREADABLE, ProgressInfo, fmt_bytes

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
CLOUD_ATTR_MASK = FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
ROOT_FILES_LABEL = "(최상위 파일)"
MAX_LAYOUT_ENTRIES = 300
MAX_LINK_PATHS = 200

Entry = tuple[str, int, int]  # (상대 경로 posix, 크기, mtime_ns)


def is_cloud_only(attrs: int) -> bool:
    return bool(attrs & CLOUD_ATTR_MASK)


@dataclass
class ScanInfo:
    root: str
    entries: list[Entry] = field(default_factory=list)  # 복사/검사 대상(클라우드 전용 제외)
    cloud: list[Entry] = field(default_factory=list)  # OneDrive 클라우드 전용(내용이 이 PC 에 없음)
    total_bytes: int = 0  # entries 의 크기 합
    cloud_bytes: int = 0
    max_size: int = 0
    skipped_links: int = 0
    skipped_link_paths: list[str] = field(default_factory=list)  # 건너뛴 링크/정션의 상대 경로(앞 MAX_LINK_PATHS 개)
    errors: list[tuple[str, str, str]] = field(default_factory=list)  # (상대 경로, 원인 코드, 쉬운 문구)
    seconds: float = 0.0
    _layout: dict[str, list[int]] = field(default_factory=dict)

    @property
    def total_files(self) -> int:
        return len(self.entries)

    def layout(self) -> dict:
        """리포트용: 원본 절대 경로(드라이브 포함)와 최상위 폴더별 파일 수/용량."""
        drive = ntpath.splitdrive(self.root)[0]
        top = [{"name": n, "files": v[0], "bytes": v[1]}
               for n, v in sorted(self._layout.items(), key=lambda kv: -kv[1][1])[:MAX_LAYOUT_ENTRIES]]
        return {"root": self.root, "drive": drive, "top_level": top, "top_level_count": len(self._layout)}

    def abs_path(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))


def skipped_links_view(meta: dict, limit: int = 20) -> tuple[int, list[str], bool]:
    """리포트 meta 에서 (건너뛴 링크 총 개수, 화면에 보일 앞 limit 개 경로, 더 있는지). 화면/CLI 안내용."""
    n = int(meta.get("skipped_links") or 0)
    paths = [str(p) for p in (meta.get("skipped_link_paths") or [])]
    n = max(n, len(paths))
    return n, paths[:limit], n > min(limit, len(paths))


def _note_link(info: "ScanInfo", rel: str) -> None:
    info.skipped_links += 1
    if len(info.skipped_link_paths) < MAX_LINK_PATHS:
        info.skipped_link_paths.append(rel)


def scan_source_info(src: Path | str, attr_getter: Callable[[Path], int] | None = None,
                     progress: Callable[[str], None] | None = None,
                     progress_info: Callable[[ProgressInfo], None] | None = None,
                     cancel: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic,
                     report_every: float = 2.0) -> ScanInfo:
    """원본을 한 번 훑는다. attr_getter 가 None 이면 scandir 가 준 속성을 쓴다(테스트에서 가짜 속성을 주입할 수 있다)."""
    root = os.path.abspath(str(src))
    info = ScanInfo(root=root)
    start = last = clock()
    sent_errors = 0  # 진행 이벤트로 이미 알린 오류 수(새 항목만 문제 목록으로 보낸다)
    stack: list[tuple[str, str]] = [(root, "")]  # (절대 경로, 상대 경로)
    while stack:
        if cancel is not None and cancel():
            raise Cancelled()
        path, relp = stack.pop()
        try:
            with os.scandir(long_path(path)) as it:
                items = sorted(it, key=lambda e: e.name.lower())
        except OSError as e:
            f = failure.classify(e, path)
            info.errors.append((relp or ".", f.code, f.text))
            continue
        subdirs: list[tuple[str, str]] = []
        for k, e in enumerate(items):
            if k % 1000 == 999 and cancel is not None and cancel():
                raise Cancelled()  # 파일이 아주 많은 폴더 안에서도 멈춘다
            rel = f"{relp}/{e.name}" if relp else e.name
            if e.name.startswith(".pcmigrate-"):  # 이 도구의 기록 파일은 이전 대상이 아니다
                continue
            try:
                if e.is_symlink():
                    _note_link(info, rel)
                    continue
                st = e.stat(follow_symlinks=False)
                attrs = getattr(st, "st_file_attributes", 0)
                if e.is_dir(follow_symlinks=False):
                    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:  # 정션(robocopy /XJ 와 같은 기준)
                        _note_link(info, rel)
                    else:
                        subdirs.append((os.path.join(path, e.name), rel))
                    continue
                if attr_getter is not None:
                    attrs = attr_getter(Path(path) / e.name)
            except OSError as ex:
                f = failure.classify(ex, rel)
                info.errors.append((rel, f.code, f.text))
                continue
            entry: Entry = (rel, st.st_size, st.st_mtime_ns)
            if is_cloud_only(attrs):
                info.cloud.append(entry)
                info.cloud_bytes += st.st_size
                continue
            info.entries.append(entry)
            info.total_bytes += st.st_size
            if st.st_size > info.max_size:
                info.max_size = st.st_size
            top = rel.split("/", 1)[0] if "/" in rel else ROOT_FILES_LABEL
            agg = info._layout.setdefault(top, [0, 0])
            agg[0] += 1
            agg[1] += st.st_size
        stack.extend(reversed(subdirs))
        now = clock()
        if (progress or progress_info) and now - last >= report_every:
            last = now
            msg = f"원본 파일 {len(info.entries):,}개({fmt_bytes(info.total_bytes)})를 확인하는 중… 파일이 많으면 몇 분 걸릴 수 있습니다."
            if progress:
                progress(msg)
            if progress_info:
                new = tuple((UNREADABLE if code == "in_use" else FAILED_KIND, rel_) for rel_, code, _t in info.errors[sent_errors:sent_errors + MAX_PROBLEM_DELTA])
                sent_errors = len(info.errors)
                unread = sum(1 for _r, code, _t in info.errors if code == "in_use")
                progress_info(ProgressInfo("scan", info.total_bytes, 0, len(info.entries), 0, message=msg, current=relp or ".",
                                           skipped=len(info.cloud), unreadable=unread, failed=len(info.errors) - unread,
                                           problems=new))
    info.seconds = clock() - start
    return info
