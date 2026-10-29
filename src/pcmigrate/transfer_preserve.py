"""transfer 분해 모듈: 덮어쓰기 전 .bak 보존: 보존 기록(journal), 대상 폴더 스캔, 부분 복사 판정, 롤백.

공개 이름은 transfer.py 파사드가 그대로 다시 내보낸다.
"""
from __future__ import annotations

import json
import os
import stat as _stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import failure
from .cancel import Cancelled
from .fs import long_path
from .transfer_base import DEFAULT_CHUNK, MAX_PRESERVED_LISTED, MTIME_TOLERANCE_NS, TransferError, _bak_name, _first, _join, _same_path

def _read_journal(path: Path, src: Path) -> dict | None:
    j = _read_journal_raw(path)
    if j is None or not _same_path(j.get("src"), src):
        return None
    return j


def _read_journal_raw(path: Path) -> dict | None:
    try:
        with open(long_path(path), "r", encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(j, dict) or j.get("phase") not in ("copying", "done"):
        return None
    return j


def _write_journal(path: Path, src: Path, phase: str, count: int, listed: list[dict], started_ns: int | None = None) -> None:
    tmp = path.with_name(path.name + ".part")
    with open(long_path(tmp), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"journal_version": 1, "src": str(src), "phase": phase, "preserved_count": count, "preserved": listed,
                   "started_ns": started_ns if started_ns is not None else time.time_ns()}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(long_path(tmp), long_path(path))


@dataclass
class DestScan:
    """대상 폴더를 한 번 훑은 결과(stat 중복 제거, R1-12). states 는 entries 와 같은 순서: n=없음, s=같음, d=같은 이름의 다른 파일."""
    states: list[str] = field(default_factory=list)
    old_sizes: dict[str, int] = field(default_factory=dict)  # 'd' 인 파일의 기존 크기(rel -> 크기). 덮어쓰기 전 .bak 으로 남는 양
    need: int = 0       # 새로 써야 하는 바이트(없거나 다른 파일의 새 크기 합). 다른 파일의 기존 내용은 .bak 으로 남아 공간을 계속 쓴다
    dst_empty: bool = True

    @property
    def bak_count(self) -> int:
        return len(self.old_sizes)

    @property
    def bak_bytes(self) -> int:
        return sum(self.old_sizes.values())


def _scan_dest(entries, dst: Path, total_bytes: int, cancel: Callable[[], bool] | None = None) -> DestScan:
    """_differs 와 같은 기준(크기 + 수정시각 2초)으로 각 파일이 대상에 '없음/같음/다름' 인지 한 번에 본다."""
    out = DestScan(states=["n"] * len(entries), need=total_bytes)
    if not dst.is_dir() or not any(True for _ in _first(dst)):
        return out
    out.dst_empty = False
    need = 0
    for i, (rel, size, mtime) in enumerate(entries):
        if cancel is not None and i % 2000 == 0 and cancel():
            raise Cancelled()
        try:
            st = os.stat(long_path(_join(str(dst), rel)))
        except OSError:
            need += size
            continue
        if not _stat.S_ISREG(st.st_mode):
            need += size  # 폴더 등은 .bak 대상이 아님(복사 단계에서 문제로 드러난다)
            continue
        if st.st_size == size and abs(st.st_mtime_ns - mtime) <= MTIME_TOLERANCE_NS:
            out.states[i] = "s"
        else:
            out.states[i] = "d"
            out.old_sizes[rel] = st.st_size
            need += size
    out.need = need
    return out


def _is_partial_prefix(src_file: str, dst_file: str, dst_size: int, cancel: Callable[[], bool] | None = None) -> bool:
    """대상 파일이 원본의 '앞부분 그대로'(robocopy 가 쓰다 끊긴 이어받기용 사본)인지 바이트로 확인한다.
    원본보다 작고 내용이 같은 앞부분이어야 True. 크기 0 이면 잃을 내용이 없으므로 True."""
    if dst_size == 0:
        return True
    try:
        if os.stat(long_path(src_file)).st_size <= dst_size:
            return False
        left = dst_size
        with open(long_path(src_file), "rb") as fs_, open(long_path(dst_file), "rb") as fd_:
            while left > 0:
                if cancel is not None and cancel():
                    raise Cancelled()
                n = min(DEFAULT_CHUNK, left)
                a, b = fs_.read(n), fd_.read(n)
                if a != b or not a:
                    return False
                left -= len(a)
    except OSError:
        return False
    return True


class _PreserveFailed(Exception):
    def __init__(self, target: str, cause: OSError) -> None:
        super().__init__(str(cause))
        self.target, self.cause = target, cause


def _rollback(moved: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """이미 .bak 으로 옮긴 파일을 거꾸로 원래 이름으로 되돌린다. 되돌리지 못한 (원래 이름, .bak 이름) 목록을 돌려준다."""
    stuck = []
    for target, bak in reversed(moved):
        try:
            if os.path.lexists(long_path(target)):
                stuck.append((target, bak))  # 원래 이름 자리에 이미 다른 것이 있으면 덮어쓰지 않는다
            else:
                os.replace(long_path(bak), long_path(target))
        except OSError:
            stuck.append((target, bak))
    return stuck


def _preserve_existing(entries, src: Path, dst: Path, journal: Path, cancel, say,
                       scan: DestScan | None = None, warn: Callable[[str], None] = lambda m: None
                       ) -> tuple[int, list[dict], bool]:
    """복사(robocopy) 시작 전에, 대상에 있는 같은 이름의 다른 파일을 같은 폴더의 .bak 으로 이름만 바꿔 보존한다(데이터를 다시 쓰지 않아 빠름).

    이어받기 구분: 이전 실행이 복사를 시작했다가 끝나지 못한 기록(journal phase=copying)이 있으면, 대상에서 원본의 '앞부분 그대로'인
    파일(robocopy 가 쓰다 만 사본)은 이어받도록 두고, 그 밖의 다른 파일(그 사이 바뀐 파일 등, R1-13)은 .bak 으로 보존한다.
    원본 경로가 이전 기록과 다르면(드라이브 문자 변경 등) 이어받기로 보지 않는다(R1-4).
    보존 도중 실패하면 이미 옮긴 것을 원래 이름으로 되돌리고(롤백) TransferError 로 복사를 시작하지 않는다(R1-2).
    반환: (보존한 개수, 앞쪽 목록, 이어받기 여부)."""
    raw = _read_journal_raw(journal)
    prev = _read_journal(journal, src)
    if raw is not None and raw["phase"] == "copying" and prev is None:
        warn(f"이전 실행 기록의 원본 경로({raw.get('src')})가 지금({src})과 다릅니다(드라이브 문자가 바뀌었을 수 있습니다). "
             "같은 원본이라도 안전하게 이어받기로 보지 않고, 대상에 있는 다른 파일은 .bak 으로 보존한 뒤 복사합니다.")
    resumed = prev is not None and prev["phase"] == "copying"
    count = int(prev.get("preserved_count") or 0) if resumed else 0
    listed = list(prev.get("preserved") or []) if resumed else []
    started = prev.get("started_ns") if resumed and isinstance(prev.get("started_ns"), int) else None
    if scan is None:
        scan = _scan_dest(entries, dst, sum(e[1] for e in entries), cancel)
    todo = [e for i, e in enumerate(entries) if scan.states[i] == "d"]
    moved: list[tuple[str, str]] = []
    try:
        if todo:
            say("대상에 있는 같은 이름의 다른 파일을 확인하는 중입니다…" if resumed else
                f"대상에 있는 같은 이름의 다른 파일 {len(todo):,}개를 .bak 으로 보존하는 중입니다…")
        last_say = time.monotonic()
        for n, (rel, _size, _mtime) in enumerate(todo, 1):
            if cancel is not None and cancel():
                raise Cancelled()
            target = _join(str(dst), rel)
            if resumed and _is_partial_prefix(_join(str(src), rel), target, scan.old_sizes.get(rel, -1), cancel):
                continue  # 쓰다 끊긴 사본: 이어받는다
            bak = _bak_name(target)
            try:
                os.replace(long_path(target), long_path(bak))
            except FileNotFoundError:
                continue  # 그 사이 사라진 파일은 보존할 것이 없다
            except OSError as e:
                raise _PreserveFailed(target, e) from e
            moved.append((target, bak))
            count += 1
            if len(listed) < MAX_PRESERVED_LISTED:
                listed.append({"file": target, "backup": bak})
            if time.monotonic() - last_say >= 2.0:
                last_say = time.monotonic()
                say(f".bak 으로 보존하는 중입니다… {n:,}/{len(todo):,}개 확인, {len(moved):,}개 옮김")
        _write_journal(journal, src, "copying", count, listed, started)
    except BaseException as e:
        stuck = _rollback(moved)
        if isinstance(e, _PreserveFailed):
            reason = failure.classify(e.cause, e.target).label
            back = (f"이미 .bak 으로 옮긴 파일 {len(moved) - len(stuck)}개는 원래 이름으로 되돌렸습니다."
                    if moved else "아직 옮긴 파일은 없습니다.")
            left = ""
            if stuck:
                left = (f" 되돌리지 못한 {len(stuck)}개는 .bak 이름으로 남아 있습니다(이름만 원래대로 바꾸면 복구됩니다): "
                        + ", ".join(b for _t, b in stuck[:3]) + (" …" if len(stuck) > 3 else ""))
            raise TransferError(f"대상에 있는 다른 파일을 .bak 으로 보존하지 못해 복사를 시작하지 않았습니다(덮어쓰지 않음): {e.target} "
                                f"({reason}). {back}{left} 파일을 쓰는 프로그램을 닫고 다시 실행하세요.") from e.cause
        if stuck:
            warn(f"중단되어 .bak 으로 옮긴 파일 {len(moved)}개를 되돌렸지만 {len(stuck)}개는 되돌리지 못했습니다: "
                 + ", ".join(b for _t, b in stuck[:3]))
        raise
    return count, listed, resumed


def _needed_bytes(entries, dst: Path, total_bytes: int) -> int:
    """대상에 새로 써야 하는 용량. _differs 와 같은 기준(크기 + 수정시각)으로 없거나 다른 파일의 새 크기만 센다(R1-3).
    다른 파일의 기존 내용은 .bak 으로 남으므로 공간이 돌아오지 않는다."""
    return _scan_dest(entries, dst, total_bytes).need
