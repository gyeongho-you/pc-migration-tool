r"""끌어다 놓은(직접 고른) 파일/폴더를 모드 B 전송에 함께 싣는다.

흐름(전송을 시작하는 시점에 한 번):
 1) 겹침/중복 제거: 체크된 폴더 안에 있는 것(경로 정규화, 대소문자 무시, 원본 경로 기준)과 같은 파일이 두 번 들어온 것을 뺀다.
    OneDrive 클라우드 전용(placeholder) 파일은 내려받지 않고 제외한다. 없어졌거나 읽을 수 없는 파일은 그 항목만 실패로 기록하고 계속한다.
 2) 남은 파일만 임시 폴더 %LOCALAPPDATA%\pcmigrate\staging\<고유이름> 에 원래 경로 구조(D\프로젝트\A\파일)로 담는다.
    같은 볼륨(NTFS)이면 하드 링크(용량 0, 원본은 읽기만), 안 되면 복사 + SHA-256 검증. 쓰기 금지 속성 파일은 링크하지 않고 복사한다
    (정리할 때 링크의 속성을 바꾸면 원본 속성이 바뀌기 때문). 복사 전에 임시 폴더/대상의 여유 공간을 확인한다.
 3) 담은 폴더를 기존 다중 폴더 전송(multi_transfer: 이어받기/해시 검증/4GB 분할)에 항목으로 더해 함께 보낸다. 기록과 표시, 대상 구조는 원래 경로 기준.
 4) 끝나면(취소/예외 포함) 임시 폴더를 지운다. 표식 파일(.pcmigrate-staging.json)이 있는 폴더만 지우고, 비정상 종료 잔여물은 다음 실행 때 표식으로 정리한다.
 5) 결과는 MultiReport.meta["drop_stage"] 로 리포트에 들어간다. 중단돼도 abort_report 가 있으면 리포트를 저장한다.
원본은 읽기만 한다(링크 생성/읽기 복사 외에는 건드리지 않는다).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat as _stat
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import errorlog, fs
from .cancel import Cancelled, checkpoint
from .copier import sha256_file
from .envpaths import expand_env
from .find_data import SelectedItem
from .multi_transfer import MultiReport, dest_rel
from .progress import fmt_bytes
from .scan import FILE_ATTRIBUTE_REPARSE_POINT, is_cloud_only
from .transfer import SPACE_MARGIN, TransferError

MARKER_NAME = ".pcmigrate-staging.json"
STAGING_DIR = r"%LOCALAPPDATA%\pcmigrate\staging"  # 시험은 이 값을 임시 폴더로 바꾼다
STALE_SECONDS = 24 * 3600  # 표식 파일이 이보다 오래 갱신되지 않은 폴더는 비정상 종료 잔여물로 본다(전송 중에는 표식을 계속 갱신)
MAX_DROP_FILES = 20000  # 직접 고른 파일 수 상한(폴더를 통째로 놓아 파일이 늘어나는 경우 포함). 넘으면 시작 전에 오류로 멈춘다
HEARTBEAT_SECONDS = 300  # 전송하는 동안 표식을 이 간격으로 갱신한다(오래 걸리는 전송 중에 다른 실행이 잔여물로 오인해 지우지 않게)
MAX_LISTED = 200  # 리포트에 적는 항목 수 상한(개수는 따로 센다)


class DropSpaceError(TransferError):
    """임시 폴더 또는 대상의 여유 공간 부족(kind: "temp" | "dest"). 화면이 쉬운 문구로 바꿔 보여 준다."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class StagedItem(SelectedItem):
    """임시 폴더에서 보내는 항목: path 는 임시 폴더, orig_dir 은 원래 폴더(대상 구조/기록 기준)."""
    orig_dir: str = ""


@dataclass
class DropEntry:
    """끌어다 놓은 항목 하나: 경로 + 용량 + 수정 시각만 기억한다(복사하지 않음)."""
    path: str
    is_dir: bool = False
    size: int = 0
    mtime_ns: int = 0
    error: str = ""  # 담을 때 이미 읽을 수 없었던 이유
    files: int = 1  # 폴더면 안의 일반 파일 수(상한 MAX_DROP_FILES+1 에서 세기를 멈춘다)


class TooLongPath(OSError):
    """임시 보관 경로가 Windows 한도(약 32,000자)를 넘음."""


MAX_STAGE_PATH = 32000


def reason_text(e: BaseException) -> str:
    """실패 사유를 사용자 문구로(내부 오류 이름/errno 를 보이지 않는다). 자세한 내용은 오류 로그에만 남는다."""
    if getattr(e, "winerror", None) == 206 or isinstance(e, TooLongPath):
        return "경로가 너무 깁니다(임시 보관에 담을 수 없습니다). 이 파일이 든 폴더를 '직접 폴더 추가'로 넣어 폴더째 보내 주세요"
    if isinstance(e, FileNotFoundError):
        return "찾을 수 없습니다(없어졌거나 다른 곳으로 옮겨졌습니다)"
    if isinstance(e, PermissionError):
        return "읽을 수 없습니다(다른 프로그램이 쓰고 있거나 접근 권한이 없습니다)"
    if isinstance(e, OSError) and e.errno is not None:
        return "읽거나 쓸 수 없습니다(자세한 내용은 오류 로그를 보세요)"
    return str(e)


def _attrs(st: os.stat_result) -> int:
    return getattr(st, "st_file_attributes", 0)


def describe(path: str | os.PathLike) -> DropEntry:
    """항목 하나의 경로/용량/수정시각을 기록한다(읽기만). 폴더는 안의 일반 파일 용량 합. 읽을 수 없으면 error 에 이유."""
    p = os.fspath(path)
    try:
        st = os.lstat(fs.long_path(p))
        if _stat.S_ISDIR(st.st_mode):
            total = count = 0
            for root, dirs, files in os.walk(fs.long_path(p)):
                dirs[:] = [d for d in dirs if not fs.is_reparse(os.path.join(root, d))]
                checkpoint()
                for n in files:
                    count += 1
                    try:
                        total += os.lstat(os.path.join(root, n)).st_size
                    except OSError:
                        pass
                if count > MAX_DROP_FILES:  # 이미 상한을 넘었으니 더 세지 않는다(화면이 멈추지 않게)
                    break
            return DropEntry(p, True, total, st.st_mtime_ns, files=count)
        return DropEntry(p, False, st.st_size, st.st_mtime_ns)
    except OSError as e:
        return DropEntry(p, False, error=reason_text(e))


def _key(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/") or os.path.normcase(p)


def _under(k: str, base: str) -> bool:
    return k == base or k.startswith(base + os.sep) or (base.endswith(os.sep) and k.startswith(base))


# --------------------------------------------------------------------------- 1) 정리
@dataclass
class StagePlan:
    files: list[tuple[str, int, int, str]] = field(default_factory=list)  # (원본, 크기, mtime_ns, 최상위 폴더)
    overlap: list[dict] = field(default_factory=list)  # {"path","covered_by"}: 체크된 폴더에 이미 포함
    duplicates: list[str] = field(default_factory=list)
    cloud_only: list[dict] = field(default_factory=list)  # {"path","size"}
    skipped_links: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)  # {"path","reason"}
    changed: list[dict] = field(default_factory=list)  # 담은 뒤 바뀐 파일(최신 내용으로 보냄)

    @property
    def total_bytes(self) -> int:
        return sum(f[1] for f in self.files)


def plan_drops(entries: list[DropEntry], checked: list[str], *, cancel: Callable[[], bool] | None = None,
               attr_fn: Callable[[os.stat_result], int] = _attrs, junk_spec=None) -> StagePlan:
    """끌어다 놓은 항목을 정리한다. 원본은 읽기만 한다(stat/목록).

    junk_spec: 정크 제외가 켜져 있으면, 체크한 폴더 안에 있어도 폴더 전송에서 빠질 파일(정크 규칙에 걸림)은 겹침으로 보지 않고
    직접 고른 파일로 보낸다(두 경로에서 다 빠지는 것을 막는다). 직접 고른 파일은 정크 규칙으로 다시 빼지 않는다.
    """
    plan = StagePlan()
    ckeys = sorted({_key(c) for c in checked if os.path.isdir(c)}, key=len)  # 없어진 폴더는 겹침 판정에 쓰지 않는다(폴더 전송이 실패하므로)
    seen: set[str] = set()

    def covered(k: str) -> str | None:
        return next((c for c in ckeys if _under(k, c)), None)

    def junked(k: str, c: str) -> bool:
        if junk_spec is None:
            return False
        rel = k[len(c):].lstrip("\\/").replace("\\", "/")
        return bool(rel) and junk_spec.classify_rel(rel) is not None

    def fail(path: str, e: BaseException | str) -> None:
        reason = e if isinstance(e, str) else reason_text(e)
        plan.failed.append({"path": path, "reason": reason})
        if not isinstance(e, str):
            errorlog.record(e if isinstance(e, BaseException) else OSError(reason), f"drop-stage plan {path}")

    def add_file(path: str, top: str, expect: DropEntry | None) -> None:
        k = _key(path)
        c = covered(k)
        if c is not None and not junked(k, c):
            plan.overlap.append({"path": path, "covered_by": c})
            return
        if k in seen:
            plan.duplicates.append(path)
            return
        try:
            st = os.lstat(fs.long_path(path))
        except OSError as e:  # 없어졌거나 읽을 수 없음: 이 항목만 실패
            fail(path, e)
            return
        a = attr_fn(st)
        if is_cloud_only(a):
            seen.add(k)
            plan.cloud_only.append({"path": path, "size": st.st_size})
            return
        if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISREG(st.st_mode) or a & FILE_ATTRIBUTE_REPARSE_POINT:
            plan.skipped_links.append(path)
            return
        if expect is not None and not expect.is_dir and (st.st_size != expect.size or st.st_mtime_ns != expect.mtime_ns):
            plan.changed.append({"path": path, "old_size": expect.size, "new_size": st.st_size})
        seen.add(k)
        plan.files.append((path, st.st_size, st.st_mtime_ns, top))
        if len(plan.files) > MAX_DROP_FILES:
            raise TransferError(f"직접 고른 파일이 너무 많습니다(최대 {MAX_DROP_FILES:,}개). 폴더째 옮길 것은 '폴더 추가'(선택한 폴더)로 보내고, "
                                "직접 고르는 파일은 그보다 적게 줄여 주세요. 아무것도 보내지 않았고 원본은 그대로입니다.")

    for e in entries:
        checkpoint()
        if cancel is not None and cancel():
            raise Cancelled()
        if e.error:
            fail(e.path, e.error)
            continue
        k = _key(e.path)
        c = covered(k)
        if c is not None and junk_spec is None:  # 정크 제외가 켜져 있으면 파일 단위로 따져 본다(정크 규칙에 걸리는 파일은 폴더 전송에서 빠진다)
            plan.overlap.append({"path": e.path, "covered_by": c})
            continue
        try:
            st = os.lstat(fs.long_path(e.path))
        except OSError as ex:
            fail(e.path, ex)
            continue
        if not _stat.S_ISDIR(st.st_mode):
            add_file(e.path, os.path.dirname(os.path.abspath(e.path)), e)
            continue
        if attr_fn(st) & FILE_ATTRIBUTE_REPARSE_POINT:
            plan.skipped_links.append(e.path)
            continue

        def on_err(ex: OSError, _top=e.path) -> None:
            fail(getattr(ex, "filename", None) or _top, ex)

        for root, dirs, files in os.walk(fs.long_path(e.path), onerror=on_err):
            checkpoint()
            if cancel is not None and cancel():
                raise Cancelled()
            keep = []
            for d in sorted(dirs):
                full = fs.plain_path_text(os.path.join(root, d))
                c = covered(_key(full))
                if c is not None and junk_spec is None:
                    plan.overlap.append({"path": full, "covered_by": c})
                elif fs.is_reparse(full):
                    plan.skipped_links.append(full)
                else:
                    keep.append(d)
            dirs[:] = keep
            for n in sorted(files):
                add_file(fs.plain_path_text(os.path.join(root, n)), e.path, None)
    return plan


def tops_of(plan: StagePlan) -> list[str]:
    """담을 파일들의 최상위 폴더(다른 최상위 안에 든 것은 뺀다): 이 폴더마다 전송 항목 하나."""
    uniq = {_key(f[3]): f[3] for f in plan.files}
    kept: list[str] = []
    for k in sorted(uniq, key=len):
        if not any(_under(k, _key(t)) for t in kept):
            kept.append(uniq[k])
    return kept


# --------------------------------------------------------------------------- 2) 임시 폴더
def staging_root() -> Path:
    """임시 보관 루트. %LOCALAPPDATA% 를 해석할 수 없으면(미정의/상대 경로) 현재 폴더에 '%LOCALAPPDATA%' 폴더가 생기지 않게 시스템 임시 폴더로 대신한다."""
    p = expand_env(STAGING_DIR)
    if "%" in p or not os.path.isabs(p):
        import tempfile
        return Path(tempfile.gettempdir()) / "pcmigrate" / "staging"
    return Path(p)


def under_stage(path) -> bool:
    """path 가 이 도구의 임시 보관 폴더(표식 파일이 있는 폴더) 안인가."""
    p = Path(os.path.abspath(os.fspath(path)))
    for anc in [p, *list(p.parents)[:12]]:
        if (anc / MARKER_NAME).is_file() and _read_marker(anc) is not None:
            return True
    return False


def _read_marker(d: Path) -> dict | None:
    try:
        m = json.loads((d / MARKER_NAME).read_text(encoding="utf-8"))
        return m if isinstance(m, dict) and m.get("tool") == "pcmigrate" and m.get("kind") == "drop-staging" else None
    except (OSError, ValueError):
        return None


def new_stage_dir(root: Path | None = None) -> Path:
    root = Path(root) if root is not None else staging_root()
    d = root / f"{time.strftime('%y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=False)
    (d / MARKER_NAME).write_text(json.dumps({"tool": "pcmigrate", "kind": "drop-staging", "version": 1, "pid": os.getpid(),
                                             "created": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
    return d


def touch_marker(d: Path) -> None:
    try:
        os.utime(d / MARKER_NAME)
    except OSError:
        pass


class _Heartbeat:
    """stage 폴더의 표식 파일을 주기적으로 갱신한다(데몬 스레드, 끝나면 stop)."""

    def __init__(self, stage: Path, interval: float | None = None) -> None:
        self.stage, self.interval = stage, (HEARTBEAT_SECONDS if interval is None else interval)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            touch_marker(self.stage)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a) -> None:
        self._stop.set()
        self._t.join(timeout=5)


def _rm_error(func, path, exc_info) -> None:
    """읽기 전용 '복사본'만 속성을 풀어 지운다. 링크(여러 이름이 같은 파일)의 속성은 원본과 공유되므로 건드리지 않는다."""
    st = os.lstat(path)
    if _stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
        os.chmod(path, _stat.S_IWRITE)
        func(path)
    else:
        raise exc_info[1]


def remove_stage_dir(d: Path, root: Path | None = None) -> dict:
    """표식이 있는 임시 폴더만 지운다(그 밖의 폴더는 절대 지우지 않음). 실패해도 예외 없이 결과를 돌려준다."""
    root = Path(root) if root is not None else staging_root()
    d = Path(d)
    try:
        if os.path.normcase(os.path.abspath(d.parent)) != os.path.normcase(os.path.abspath(root)):
            return {"path": str(d), "removed": False, "error": "임시 폴더 바로 아래의 폴더가 아니어서 지우지 않았습니다"}
        if not d.exists():
            return {"path": str(d), "removed": True}
        if fs.is_reparse(d) or _read_marker(d) is None:
            return {"path": str(d), "removed": False, "error": "도구 표식이 없는 폴더라 지우지 않았습니다"}
        shutil.rmtree(fs.long_path(d), onerror=_rm_error)
        return {"path": str(d), "removed": True}
    except OSError as e:
        errorlog.record(e, f"drop-stage cleanup {d}")
        return {"path": str(d), "removed": False, "error": f"{type(e).__name__}: {e}"}


def cleanup_stale(root: Path | None = None, *, max_age: float = STALE_SECONDS, now: float | None = None) -> list[dict]:
    """비정상 종료로 남은 임시 폴더(표식이 있고 max_age 초 넘게 갱신 안 된 것)를 지운다."""
    root = Path(root) if root is not None else staging_root()
    out: list[dict] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    now = time.time() if now is None else now
    for n in names:
        d = root / n
        try:
            if not d.is_dir() or _read_marker(d) is None:
                continue
            if now - (d / MARKER_NAME).stat().st_mtime < max_age:
                continue
        except OSError:
            continue
        out.append(remove_stage_dir(d, root))
    return out


def _copy_verified(src: str, dst: str, cancel) -> None:
    """읽어서 복사하며 원본 해시를 계산하고, 복사본을 다시 읽은 해시와 비교한다. 복사 중 원본이 바뀌면 실패."""
    s, d = fs.long_path(src), fs.long_path(dst)
    before = os.stat(s)
    h = hashlib.sha256()
    try:
        with open(s, "rb") as fi, open(d, "wb") as fo:
            while True:
                checkpoint()
                if cancel is not None and cancel():
                    raise Cancelled()
                buf = fi.read(4 * 1024 * 1024)
                if not buf:
                    break
                h.update(buf)
                fo.write(buf)
        after = os.stat(s)
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise OSError("복사하는 동안 원본이 바뀌었습니다")
        if sha256_file(Path(dst), cancel=cancel) != h.hexdigest():
            raise OSError("임시 복사본의 SHA-256 이 원본과 다릅니다")
        os.utime(d, ns=(before.st_atime_ns, before.st_mtime_ns))
    except BaseException:
        try:
            os.remove(d)
        except OSError:
            pass
        raise


def stage_files(plan: StagePlan, stage: Path, *, cancel=None, progress: Callable[[str], None] | None = None,
                free_fn: Callable[[str], int | None] = fs.free_space, no_link: bool = False) -> dict:
    """plan.files 를 stage 아래 원래 경로 구조로 담는다. 파일 하나의 실패는 plan.failed 에 적고 계속한다.

    반환: {"linked","copied","copied_bytes"}. 임시 폴더 여유 공간이 복사할 양보다 모자라면 TransferError(담기 전에 막음).
    """
    stats = {"linked": 0, "copied": 0, "copied_bytes": 0}
    todo: list[tuple[str, str, int]] = []
    ok_files = []
    for i, (src, size, _m, top) in enumerate(plan.files, 1):
        checkpoint()
        if cancel is not None and cancel():
            raise Cancelled()
        out = stage / dest_rel(src)
        try:
            if len(str(out)) > MAX_STAGE_PATH:
                raise TooLongPath(f"임시 보관 경로가 너무 깁니다: {len(str(out))}자")
            os.makedirs(fs.long_path(out.parent), exist_ok=True)  # 260자 넘는 원본 경로도 담을 수 있게(원본 읽기와 같은 긴 경로 처리)
            if not no_link and os.stat(fs.long_path(src)).st_mode & _stat.S_IWRITE:
                try:
                    os.link(fs.long_path(src), fs.long_path(out))
                    stats["linked"] += 1
                    ok_files.append((src, size, _m, top))
                    continue
                except OSError:
                    pass  # 다른 볼륨/NTFS 아님/권한: 복사로
            todo.append((src, str(out), size))
            ok_files.append((src, size, _m, top))
        except OSError as e:
            plan.failed.append({"path": src, "reason": reason_text(e)})
            errorlog.record(e, f"drop-stage link {src}")
        if i % 200 == 0:
            touch_marker(stage)
            if progress:
                progress(f"끌어다 놓은 파일 준비 중 {i:,}/{len(plan.files):,}")
    need = sum(t[2] for t in todo)
    free = free_fn(str(stage))
    if todo and free is not None and free < int(need * (1 + SPACE_MARGIN)):
        raise DropSpaceError(f"임시 폴더가 있는 드라이브의 여유 공간이 부족합니다. 끌어다 놓은 파일을 임시로 담는 데 약 "
                            f"{fmt_bytes(int(need * (1 + SPACE_MARGIN)))} 필요, 현재 여유 {fmt_bytes(free)}. "
                            "파일 수를 줄이거나 폴더로 직접 선택하세요.", "temp")
    for n, (src, out, size) in enumerate(todo, 1):
        if cancel is not None and cancel():
            raise Cancelled()
        try:
            _copy_verified(src, out, cancel)
            stats["copied"] += 1
            stats["copied_bytes"] += size
        except Cancelled:
            raise
        except (OSError, ValueError) as e:
            plan.failed.append({"path": src, "reason": reason_text(e)})
            errorlog.record(e, f"drop-stage copy {src}")
            ok_files = [f for f in ok_files if f[0] != src]
        if n % 20 == 0:
            touch_marker(stage)
            if progress:
                progress(f"끌어다 놓은 파일 임시 복사 중 {n:,}/{len(todo):,}")
    plan.files = ok_files
    return stats


# --------------------------------------------------------------------------- 3~5) 전체 흐름과 리포트
def _cap(rows: list) -> list:
    return rows[:MAX_LISTED]


def _info(plan: StagePlan, entries: list[DropEntry]) -> dict:
    return {"entries": len(entries), "staged_files": len(plan.files), "staged_bytes": plan.total_bytes,
            "overlap_count": len(plan.overlap), "overlap": _cap(plan.overlap), "duplicate_count": len(plan.duplicates),
            "duplicates": _cap(plan.duplicates), "cloud_only": _cap(plan.cloud_only), "cloud_only_total": len(plan.cloud_only),
            "skipped_links": _cap(plan.skipped_links), "skipped_links_total": len(plan.skipped_links),
            "failed": plan.failed, "changed": _cap(plan.changed)}


def summary_lines(ds: dict) -> list[str]:
    lines = [f"- 직접 고른 파일/폴더 {ds.get('entries', 0)}개: 전송 {ds.get('staged_files', 0):,}개 / {fmt_bytes(ds.get('staged_bytes', 0))}"
             + (f" (임시 링크 {ds.get('linked', 0):,}개, 임시 복사 {ds.get('copied', 0):,}개)" if "linked" in ds else "")]
    if ds.get("overlap_count"):
        lines.append(f"    · {ds['overlap_count']}개는 이미 선택한 폴더에 포함되어 빼고 보냅니다")
    if ds.get("duplicate_count"):
        lines.append(f"    · 같은 파일이 두 번 들어와 {ds['duplicate_count']}개는 한 번만 보냅니다")
    if ds.get("cloud_only_total"):
        lines.append(f"    · OneDrive 클라우드 전용 {ds['cloud_only_total']}개는 내려받지 않고 제외했습니다(파일을 이 PC에 내려받은 뒤 다시 하세요)")
    if ds.get("skipped_links_total"):
        lines.append(f"    · 바로가기/연결 {ds['skipped_links_total']}개는 따라가지 않고 제외했습니다")
    if ds.get("changed"):
        lines.append(f"    · {len(ds['changed'])}개는 끌어다 놓은 뒤 바뀌어 지금 내용으로 보냅니다")
    for f in ds.get("failed", [])[:10]:
        lines.append(f"    · 실패: {f['path']} — {f['reason']}")
    if ds.get("cleanup"):
        c = ds["cleanup"]
        lines.append("    · 임시 폴더를 정리했습니다" if c.get("removed") else f"    · 임시 폴더를 지우지 못했습니다(다음 실행 때 정리): {c.get('error', '')}")
    return lines


def _write_abort(path, ds: dict, err: BaseException) -> None:
    try:
        data = {"aborted": True, "cancelled": isinstance(err, Cancelled), "reason": f"{type(err).__name__}: {err}",
                "success": False, "drop_stage": ds}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        errorlog.record(e, f"drop-stage abort report {path}")


def run_with_extras(items: list, extras, dst, run_fn: Callable[[list], "object"], *, dry_run: bool = False,
                    cancel: Callable[[], bool] | None = None, warn: Callable[[str], None] = lambda m: None,
                    progress: Callable[[str], None] | None = None, abort_report=None, root: Path | None = None,
                    free_fn: Callable[[str], int | None] = fs.free_space, attr_fn=_attrs, no_link: bool = False, junk_spec=None):
    """items(선택 파일의 폴더들) + extras(경로 문자열 또는 DropEntry) 를 함께 전송한다. run_fn(항목 목록) -> MultiReport.

    예외(취소 포함)가 나도 임시 폴더는 지우고, abort_report 가 있으면 중단 리포트를 저장한 뒤 예외를 다시 던진다.
    """
    root = Path(root) if root is not None else staging_root()
    stale = cleanup_stale(root)
    entries = [e if isinstance(e, DropEntry) else describe(e) for e in extras]
    checked = [it.path for it in items if isinstance(it, SelectedItem) and it.include] + [it for it in items if isinstance(it, str)]
    plan = StagePlan()
    info: dict = {"entries": len(entries)}
    if dst is not None:  # 받는 곳이 직접 고른 폴더 안이면(원본에 쓰게 되므로) 시작하지 않는다
        from .transfer import _is_within
        d_real = os.path.realpath(dst)
        for e in entries:
            if e.is_dir and not e.error:
                s_real = os.path.realpath(e.path)
                if os.path.normcase(s_real) == os.path.normcase(d_real) or _is_within(Path(d_real), Path(s_real)):
                    raise TransferError(f"대상은 직접 고른 폴더와 같거나 그 안에 있을 수 없습니다: 원본 {e.path} / 대상 {dst}")
    stage: Path | None = None
    result = err = None
    try:
        if progress:
            progress("직접 고른 파일 확인 중…")
        plan = plan_drops(entries, checked, cancel=cancel, attr_fn=attr_fn, junk_spec=junk_spec)
        info = _info(plan, entries)
        if stale:
            info["stale_cleaned"] = stale
        extra_items: list[StagedItem] = []
        if plan.files and not dry_run:
            free = free_fn(os.fspath(dst)) if dst is not None else None  # LAN 보내기는 로컬 대상이 없다(받는 PC 가 공간을 확인)
            if free is not None and plan.total_bytes > free:
                raise DropSpaceError(f"대상 드라이브의 여유 공간이 부족합니다. 직접 고른 파일만 {fmt_bytes(plan.total_bytes)}, 현재 여유 {fmt_bytes(free)}.", "dest")
            stage = new_stage_dir(root)
            if progress:
                progress("직접 고른 파일을 임시 보관 중…")
            info.update(stage_files(plan, stage, cancel=cancel, progress=progress, free_fn=free_fn, no_link=no_link))
            info.update(_info(plan, entries))
            for top in tops_of(plan):
                extra_items.append(StagedItem(str(stage / dest_rel(top)), True, orig_dir=top))
        for w in summary_lines(info)[1:]:
            warn(w.strip(" ·-"))
        if dry_run and not checked:  # 미리보기에 폴더가 하나도 없으면 직접 고른 파일의 정리 결과만 보여 준다
            result = MultiReport(dry_run=True)
        elif stage is not None:
            with _Heartbeat(stage):  # 오래 걸리는 전송 중에도 표식을 갱신한다
                result = run_fn(list(items) + extra_items)
        else:
            result = run_fn(list(items) + extra_items)
    except BaseException as e:  # noqa: BLE001 - 정리 후 그대로 다시 던진다
        err = e
        if not isinstance(e, Cancelled) and type(e).__name__ != "LanError":  # 연결/코드 문제는 화면이 쉬운 문구로 안내한다(오류 로그 대상 아님)
            errorlog.record(e, "drop-stage run")
    if stage is not None:
        if progress:
            progress("임시 보관 폴더 정리 중…")
        info["cleanup"] = remove_stage_dir(stage, root)
    if err is not None:
        if abort_report:
            _write_abort(abort_report, info, err)
        raise err
    return attach_info(result, info)


def attach_info(result, info: dict):
    """결과에 drop_stage 정보를 붙인다. MultiReport 는 meta 에, LAN 리포트(dict)는 키로 넣고 실패/클라우드 제외 개수를 합친다."""
    if isinstance(result, dict):
        result["drop_stage"] = info
        nfail = len(info.get("failed", []))
        if nfail:
            result["failed_count"] = int(result.get("failed_count", 0)) + nfail
            result.setdefault("failed", []).extend({"path": x["path"], "reason": "drop_stage", "error": x["reason"]} for x in info["failed"])
            result["success"] = False
        cloud = result.get("cloud_only_skipped")
        if isinstance(cloud, int):
            result["cloud_only_skipped"] = cloud + int(info.get("cloud_only_total", 0))
        return result
    result.meta["drop_stage"] = info
    return result
