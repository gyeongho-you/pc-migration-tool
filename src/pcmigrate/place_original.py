r"""받기 폴더 -> 원래 위치 배치(2단계): 모드 B 다중 원본 전송이 <받기폴더>\<드라이브>\<경로> 로 모아 둔 것을 같은 드라이브·같은 경로로 놓는다.

원래 경로는 받기 폴더의 저널(.pcmigrate-multi.json)의 원본 경로로 복원하고, 저널이 없으면(LAN 받기 등) <드라이브 문자>\... 구조에서 유추한다.
'설정 묶음' 같이 이름을 지정해 받은 폴더, \\서버\공유(UNC) 원본, 구조를 모르는 폴더/파일은 놓지 않고 사유와 함께 보고한다.

동작 방식(파일 단위, 기본은 미리보기이고 apply 로만 실제 변경):
- move        받기 폴더와 대상이 같은 볼륨: 이름 바꾸기(os.replace)로 이동한다(용량이 늘지 않음). 같은 볼륨 이동은 데이터를 다시 쓰지 않는 메타데이터 작업이라
              해시를 다시 계산하지 않고 이동 뒤 '크기와 존재'만 확인한다(받기 때의 검증은 이미 끝났다는 전제). 덮어쓸 기존 파일은 이동 전에 .bak 으로 옮긴다.
- copy-delete 다른 볼륨: copier.copy_verified(임시 파일 -> 크기/SHA-256 검증 -> 교체, 기존 파일은 .bak)로 복사하고, 검증을 통과한 그 파일만 받기 폴더에서 지운다.
              검증/복사 실패 파일은 받기 폴더에 그대로 둔다. 파일마다 '원래 자리 또는 새 자리' 한쪽에는 항상 온전한 파일이 있다(지우기는 교체가 끝난 뒤).
- keep        복사만 한다(받기 폴더는 읽기만, 이동/삭제 없음).
이미 같은 내용이 대상에 있으면(해시 비교) 복사하지 않고 받기 폴더 쪽 사본만 정리한다(keep 이면 그대로 둠) — 중간에 멈춘 뒤 다시 실행해도 같은 결과가 된다(멱등).
빈 폴더는 지우지 않는다(남은 항목만 보고). 링크/reparse 지점은 따라가지 않고 건너뛴다.

놓을 수 없는 것(시스템 폴더, 대상 드라이브 없음, 잠긴 파일, 덮어쓰기 거부, 대상이 받기 폴더 안, 링크 등)은 예외로 멈추지 않고 건너뛰어
'예상 위치 / 현재 위치 / 사유' 표로 모은다. 취소와 드라이브 분리만 바로 멈춘다(다시 실행하면 이어서 한다).
한계: 파일 내용 안의 경로는 고쳐 쓰지 않는다(--map 으로 드라이브가 달라지면 프로젝트 파일 안의 절대경로가 어긋날 수 있다).
"""
from __future__ import annotations

import errno
import json
import ntpath
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import copier, envpaths, errorlog, failure, fs
from .cancel import Cancelled
from .copier import FAIL_STATUSES, PART_SUFFIX
from .discover_data import RESTORE_DENY_SUBSTRINGS
from .fs import long_path
from .multi_transfer import JOURNAL_NAME, _read_journal, dest_rel
from .package_base import (DENY_DIR_NAMES, DENY_FILE_NAMES, HOME_DENY_FILES, HOME_DENY_SUBFILES, SSH_DENY_FILES,
                           unsafe_name)
from .progress import ProgressInfo, ProgressTracker, categorize, fmt_bytes
from .transfer_base import DISCONNECTED_MESSAGE, SPACE_MARGIN, DriveDisconnected, TransferError, _is_within

class DiskFull(TransferError):
    """대상 드라이브 공간 부족: 계속하면 모든 파일이 실패하므로 지금까지의 결과를 저장하고 멈춘다(다시 실행하면 이어서 한다)."""


MOVE, COPY_DELETE, KEEP = "move", "copy-delete", "keep"
OWN_PREFIX = ".pcmigrate-"
MAX_LISTED = 2000  # 보고서에 파일별로 싣는 성공/건너뜀 목록 상한(나머지는 개수만)
_DRIVE = re.compile(r"^[A-Za-z]$")

# 사유 코드 -> (짧은 사유, 안내). 파일 복사/이동 실패는 failure.py 의 코드를 그대로 쓴다.
PROTECTED, DRIVE_MISSING, UNSAFE_NAME, LINK, INSIDE_RECEIVE, SAME_LOCATION = (
    "protected_system", "drive_missing", "unsafe_name", "link", "inside_receive", "same_location")
NO_SPACE, NO_OVERWRITE, BLOCKED, UNKNOWN_LOC, UNC, UNVERIFIED, READONLY_DRIVE, BAD_JOURNAL, UNREADABLE = (
    "no_space", "no_overwrite", "dest_blocked", "unknown_location", "unc", "unverified", "readonly_drive",
    "bad_journal", "receive_unreadable")
LEFTOVER = "source_not_removed"
REASONS: dict[str, tuple[str, str]] = {
    PROTECTED: ("시스템/보호 폴더라 자동으로 놓지 않음", "Windows, Program Files, ProgramData, 사용자 AppData, 시작 프로그램 같은 위치는 직접 확인하고 옮기세요."),
    DRIVE_MISSING: ("대상 드라이브가 이 PC 에 없음", "드라이브를 연결하거나 --map 으로 다른 드라이브를 지정하세요(예: --map D=E)."),
    UNSAFE_NAME: ("Windows 에서 쓸 수 없는 이름(예약 장치 이름, 끝의 점/공백, 금지 문자, 너무 긴 이름)", "이름을 바꿔 직접 옮기세요."),
    LINK: ("링크(바로가기/정션/심볼릭 링크)라 따라가지 않음", "링크 너머는 따로 확인해 직접 옮기세요."),
    INSIDE_RECEIVE: ("놓을 위치가 받기 폴더 안이라 건너뜀", "받기 폴더를 다른 위치로 옮긴 뒤 다시 실행하거나 직접 옮기세요."),
    SAME_LOCATION: ("이미 원래 위치에 있음(받기 폴더 경로와 같음)", ""),
    NO_SPACE: ("대상 드라이브의 여유 공간이 부족함", "공간을 확보하거나 --map 으로 다른 드라이브를 지정하세요."),
    NO_OVERWRITE: ("대상에 다른 내용의 파일이 있어 덮어쓰기를 거부함(--no-overwrite)", "확인 후 직접 옮기거나 --no-overwrite 없이 다시 실행하세요(.bak 으로 보관됨)."),
    BLOCKED: ("놓을 위치에 같은 이름의 파일이 폴더 자리를 막고 있음", "그 파일의 이름을 바꾸거나 옮긴 뒤 다시 실행하세요."),
    UNKNOWN_LOC: ("원래 위치를 알 수 없는 폴더/파일(설정 묶음 등 이름을 지정해 받은 것)", "이 도구가 자동으로 놓지 않습니다. 필요하면 직접 옮기세요."),
    UNC: ("네트워크 경로(\\\\서버\\공유) 원본이라 자동으로 놓지 않음", "공유 폴더에 직접 복사하세요."),
    UNVERIFIED: ("받기가 끝나고 검증된 항목이 아님(저널 상태가 done 이 아님)", "먼저 받기를 마치고 verify 로 확인하세요. 알고 진행하려면 --include-unverified."),
    READONLY_DRIVE: ("쓸 수 없는 드라이브(CD/DVD)", ""),
    BAD_JOURNAL: ("저널의 항목이 올바르지 않아 놓지 않음", ""),
    UNREADABLE: ("받기 폴더를 읽지 못함", ""),
    LEFTOVER: ("복사는 검증까지 끝났지만 받기 폴더의 파일을 지우지 못함", "대상에 온전히 있으니 받기 폴더의 파일은 직접 지워도 됩니다."),
}


def reason_text(code: str, extra: str = "") -> str:
    if code in REASONS:
        label, hint = REASONS[code]
        text = f"{label}. {hint}" if hint else label
    elif code in failure.REASONS:
        text = failure.REASONS[code].text
    else:
        text = extra or code
    return text


# --------------------------------------------------------------------------- 보호 위치
ROOT_PROTECTED = frozenset({"windows", "windows.old", "program files", "program files (x86)", "programdata", "$recycle.bin",
                            "system volume information", "recovery", "perflogs", "$winreagent", "boot", "efi"})


def _env_roots() -> list[str]:
    out = []
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        v = envpaths.lookup(name)
        if v and ntpath.splitdrive(v)[0]:
            out.append(ntpath.normcase(ntpath.normpath(v)).rstrip("\\"))
    return out


def protected_reason(logical: str, *, is_dir: bool = True) -> str | None:
    """원래 위치(논리 경로, 예 D:\\프로젝트\\A)가 자동으로 놓지 않는 보호 위치면 사유 문구, 아니면 None.

    기존 복원 거부 규칙(discover_data.RESTORE_DENY_SUBSTRINGS, package_base 의 DENY_*)의 '위치' 규칙을 재사용한다.
    확장자 거부(.exe/.dll 등)는 시작 프로그램 등록을 막기 위한 것이라 사용자 자료 폴더에는 적용하지 않는다.
    """
    norm = ntpath.normpath(logical)
    drive, rest = ntpath.splitdrive(norm)
    parts = [p for p in rest.split("\\") if p]
    low = [p.lower() for p in parts]
    if not low:
        return None
    if low[0] in ROOT_PROTECTED:
        return f"{parts[0]} 폴더"
    if low[0] == "users" and len(low) >= 3 and low[2] == "appdata":
        return "사용자 AppData"
    if not is_dir and len(low) == 1 and drive.upper() == (envpaths.lookup("SystemDrive") or "C:").upper():
        return "시스템 드라이브 루트 파일"
    full = ntpath.normcase(norm).rstrip("\\")
    for r in _env_roots():
        if full == r or full.startswith(r + "\\"):
            return "시스템 폴더"
    if any(d in full + "\\" for d in RESTORE_DENY_SUBSTRINGS):
        return "시작 프로그램/authorized_keys 위치"
    if any(x in DENY_DIR_NAMES for x in low):
        return "Excel 시작 폴더(XLSTART)"
    if low[0] == "users" and len(low) >= 3:
        if len(low) == 3 and (low[2] in HOME_DENY_FILES or low[2] in DENY_FILE_NAMES):
            return "홈 설정 파일(명령/토큰이 들어갈 수 있음)"
        if len(low) == 4 and ((low[2], low[3]) in HOME_DENY_SUBFILES or (low[2] == ".ssh" and low[3] in SSH_DENY_FILES)):
            return "홈 설정 파일(명령/토큰이 들어갈 수 있음)"
    return None


def name_problem(name: str) -> bool:
    return unsafe_name(name) or ":" in name or len(name) > 255 or name in (".", "..")


# --------------------------------------------------------------------------- 결과
@dataclass
class Row:
    kind: str  # file | folder
    expected: str  # 예상 위치(놓으려던 원래 경로)
    current: str  # 현재 위치(받기 폴더 안)
    code: str
    detail: str = ""
    files: int = 0
    bytes: int = 0

    @property
    def reason(self) -> str:
        return reason_text(self.code, self.detail) + (f" ({self.detail})" if self.detail and self.code in REASONS and self.code != LEFTOVER else "")

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "expected": fs.plain_path_text(self.expected), "current": fs.plain_path_text(self.current),
             "reason_code": self.code, "reason": fs.plain_path_text(self.reason)}
        if self.kind == "folder":
            d.update(files=self.files, bytes=self.bytes)
        return d


@dataclass
class PlaceReport:
    dry_run: bool
    keep: bool
    recv: str
    maps: dict = field(default_factory=dict)
    items: list[dict] = field(default_factory=list)
    skipped: list[Row] = field(default_factory=list)  # 시도하지 않고 건너뜀(수동 작업)
    failed: list[Row] = field(default_factory=list)  # 시도했으나 실패(받기 폴더에 그대로 남음)
    leftover: list[Row] = field(default_factory=list)  # 대상에 놓았지만 받기 폴더 쪽을 지우지 못함
    placed: list[dict] = field(default_factory=list)
    placed_truncated: int = 0
    counts: dict = field(default_factory=dict)
    modes: dict = field(default_factory=dict)  # 모드별 파일 수
    total_files: int = 0
    total_bytes: int = 0
    placed_bytes: int = 0
    delete_planned: int = 0  # (미리보기) 검증 후 받기 폴더에서 지울 파일 수(복사-삭제 방식 + 이미 대상에 같은 내용이 있는 것)
    delete_planned_bytes: int = 0
    removed_sources: int = 0
    removed_list: list[str] = field(default_factory=list)  # 받기 폴더에서 지운 파일(상한 초과분은 개수만)
    removed_truncated: int = 0
    remaining: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @property
    def mode(self) -> str:
        if self.keep:
            return KEEP
        used = [m for m, n in self.modes.items() if n]
        return used[0] if len(used) == 1 else ("mixed" if used else "auto")

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def manual_count(self) -> int:
        return len(self.skipped) + len(self.failed) + len(self.leftover)

    def to_dict(self) -> dict:
        placed_n = sum(self.counts.get(k, 0) for k in ("copied", "moved", "would_copy", "would_move", "would_overwrite"))
        d = {"dry_run": self.dry_run, "mode": self.mode, "keep": self.keep, "recv_root": fs.plain_path_text(self.recv),
             "maps": self.maps, "summary": dict(self.counts), "modes": dict(self.modes), "total_files": self.total_files,
             "total_bytes": self.total_bytes, "placed_files": placed_n, "placed_bytes": self.placed_bytes,
             "removed_sources": self.removed_sources, "removed_files": self.removed_list,
             "removed_truncated": self.removed_truncated, "failed_count": self.failed_count,
             "manual_count": self.manual_count, "items": self.items,
             "skipped": [r.to_dict() for r in self.skipped[:MAX_LISTED]], "skipped_count": len(self.skipped),
             "failed": [r.to_dict() for r in self.failed[:MAX_LISTED]],
             "leftover": [r.to_dict() for r in self.leftover[:MAX_LISTED]],
             "placed": self.placed, "placed_truncated": self.placed_truncated, "remaining_in_receive": self.remaining, **self.meta}
        if self.dry_run:
            d["preview"] = True
            d["delete_planned_files"] = self.delete_planned
            d["delete_planned_bytes"] = self.delete_planned_bytes
        d["success"] = not self.failed and not self.skipped and not self.leftover and not self.meta.get("interrupted")
        return d

    def write(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- 단위 찾기
@dataclass
class Unit:
    current: Path  # 받기 폴더 안의 폴더
    letter: str  # 대상 드라이브 문자(--map 적용 후)
    parts: tuple[str, ...]  # 드라이브 아래 경로 요소
    origin: str  # journal | inferred
    verified: bool | None = None  # 저널 상태가 done 인가(저널 없으면 None)

    @property
    def logical(self) -> str:
        return f"{self.letter}:\\" + "\\".join(self.parts)


def parse_maps(items: list[str] | None) -> dict[str, str]:
    """['D=E', ...] -> {'D': 'E'}. 형식 오류/중복 원본/중복 대상은 ValueError."""
    out: dict[str, str] = {}
    for it in items or []:
        m = re.fullmatch(r"\s*([A-Za-z]):?\s*=\s*([A-Za-z]):?\s*", it)
        if not m:
            raise ValueError(f"--map 은 D=E 형식이어야 합니다(드라이브 문자 하나씩): {it!r}")
        a, b = m.group(1).upper(), m.group(2).upper()
        if a in out:
            raise ValueError(f"--map 에 같은 드라이브가 두 번 있습니다: {a}")
        out[a] = b
    if len(set(out.values())) != len(out):
        raise ValueError("--map 의 대상 드라이브가 겹칩니다(서로 다른 드라이브를 같은 곳에 합치지 않습니다)")
    return out


def default_drive_root(letter: str) -> Path | None:
    p = Path(f"{letter}:\\")
    try:
        return p if p.is_dir() else None
    except OSError:
        return None


def _bad_rel(rel: str) -> bool:
    parts = [p for p in rel.split("\\") if p]
    return not parts or any(p in (".", "..") or name_problem(p) for p in parts)


def discover(recv: Path, maps: dict[str, str], include_unverified: bool, warn: Callable[[str], None]) -> tuple[list[Unit], list[Row], dict]:
    """받기 폴더에서 놓을 단위(폴더)와 처음부터 건너뛸 항목(Row)을 찾는다."""
    units: list[Unit] = []
    rows: list[Row] = []
    meta: dict = {"journal": False, "journal_items": 0}
    jp = recv / JOURNAL_NAME
    journal = _read_journal(recv)
    if jp.is_file():
        meta["journal"] = bool(journal["items"])
        if not journal["items"]:
            warn("저널(.pcmigrate-multi.json)을 읽지 못했거나 비어 있어 폴더 구조로 원래 위치를 유추합니다.")
    covered: set[str] = set()  # 저널이 다루는 최상위 폴더 이름(소문자)
    jrels: list[str] = []
    for key, it in journal["items"].items():
        meta["journal_items"] += 1
        src, rel, status = it.get("src"), it.get("rel"), it.get("status")
        if not isinstance(src, str) or not isinstance(rel, str) or _bad_rel(rel):
            rows.append(Row("folder", "-", str(recv / str(rel or key)), BAD_JOURNAL))
            continue
        covered.add(rel.split("\\")[0].lower())
        jrels.append(rel.lower())
        cur = recv.joinpath(*rel.split("\\"))
        if rel.lower() != dest_rel(src).lower():  # 이름을 지정해 받은 폴더(설정 묶음 등)
            rows.append(Row("folder", "-", str(cur), UNKNOWN_LOC))
            continue
        if not cur.is_dir():
            meta["missing_items"] = meta.get("missing_items", 0) + 1
            continue
        drive, rest = ntpath.splitdrive(ntpath.normpath(src))
        if drive.startswith("\\\\"):
            rows.append(Row("folder", ntpath.normpath(src), str(cur), UNC))
            continue
        letter = drive.rstrip(":")
        if not _DRIVE.match(letter):
            rows.append(Row("folder", src, str(cur), BAD_JOURNAL))
            continue
        parts = tuple(p for p in rest.split("\\") if p)
        if status != "done" and not include_unverified:
            rows.append(Row("folder", f"{maps.get(letter.upper(), letter.upper())}:\\" + "\\".join(parts), str(cur), UNVERIFIED, f"상태: {status}"))
            continue
        units.append(Unit(cur, maps.get(letter.upper(), letter.upper()), parts, "journal", status == "done"))
    try:
        tops = sorted(os.scandir(long_path(recv)), key=lambda e: e.name.lower())
    except OSError as e:
        raise TransferError(f"받기 폴더를 읽을 수 없습니다: {recv} ({e})") from e
    for e in tops:
        name = e.name
        if name.startswith(OWN_PREFIX) or name.endswith(PART_SUFFIX):
            continue
        cur = recv / name
        isdir = e.is_dir(follow_symlinks=False)
        if e.is_symlink() or (isdir and fs.is_reparse(cur)):
            rows.append(Row("folder" if isdir else "file", "-", str(cur), LINK))
            continue
        if not isdir:
            rows.append(Row("file", "-", str(cur), UNKNOWN_LOC))
            continue
        low = name.lower()
        if _DRIVE.match(name):
            if any(r == low or r.startswith(low + "\\") for r in jrels):
                continue  # 저널 항목으로 이미 처리
            letter = name.upper()
            units.append(Unit(cur, maps.get(letter, letter), (), "inferred"))
        elif low == "unc":
            if "unc" in covered:
                continue
            for srv in sorted(p for p in cur.iterdir() if p.is_dir()):
                for share in sorted(p for p in srv.iterdir() if p.is_dir()):
                    rows.append(Row("folder", f"\\\\{srv.name}\\{share.name}", str(share), UNC))
        elif low not in covered:
            rows.append(Row("folder", "-", str(cur), UNKNOWN_LOC))
    return units, rows, meta


# --------------------------------------------------------------------------- 배치 실행
def _same_volume(a: str, b: str) -> bool:
    try:
        return os.stat(long_path(a)).st_dev == os.stat(long_path(b)).st_dev
    except OSError:
        return False


def _nearest_existing(p: Path) -> Path:
    while not os.path.lexists(p) and p.parent != p:
        p = p.parent
    return p


def _is_exdev(e: OSError) -> bool:
    return e.errno == errno.EXDEV or getattr(e, "winerror", None) == 17


@dataclass
class _Totals:
    files: int = 0
    bytes: int = 0
    copy_bytes: int = 0  # 대상 볼륨에 새로 써야 하는 바이트(같은 볼륨 이동은 0)


class _Placer:
    def __init__(self, recv: Path, *, dry_run: bool, keep: bool, no_overwrite: bool,
                 drive_root_fn, same_volume_fn, cancel, progress_info, report: PlaceReport) -> None:
        self.recv, self.dry_run, self.keep, self.no_overwrite = recv, dry_run, keep, no_overwrite
        self.drive_root_fn, self.same_volume_fn, self.cancel = drive_root_fn, same_volume_fn, cancel
        self.progress_info, self.rep = progress_info, report
        self.phase = "scan"
        self.totals: dict[str, _Totals] = {}  # 단위 키별
        self.cur_key = ""
        self.rows: list[Row] = report.skipped  # 건너뜀 표(실행 단계에서만 쌓는다)
        self.tracker: ProgressTracker | None = None
        self._last_emit = 0.0
        self._vol_cache: dict[str, bool] = {}
        self.unit_counts: dict[str, int] = {}

    # ---- 보조
    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel():
            raise Cancelled()

    def _row(self, kind: str, logical: str, cur, code: str, detail: str = "", count_tree: Path | None = None) -> None:
        r = Row(kind, logical, str(cur), code, detail)
        if count_tree is not None:
            r.files, r.bytes = _count_tree(count_tree)
        if self.phase == "apply":
            self.rows.append(r)

    def alive(self, root: Path) -> bool:
        return os.path.isdir(self.recv) and os.path.isdir(root)

    def _same_vol_dir(self, src_dir: Path, phys_dir: Path) -> bool:
        anc = _nearest_existing(phys_dir)
        key = os.path.normcase(str(anc))
        if key not in self._vol_cache:
            self._vol_cache[key] = bool(self.same_volume_fn(str(src_dir), str(anc)))
        return self._vol_cache[key]

    def _mode(self, same_vol: bool) -> str:
        return KEEP if self.keep else (MOVE if same_vol else COPY_DELETE)

    # ---- 폴더
    def walk(self, src_dir: Path, parts: tuple[str, ...], letter: str, root: Path, unit_root: bool = False) -> None:
        self._check_cancel()
        logical = f"{letter}:\\" + "\\".join(parts)
        phys = root.joinpath(*parts)
        problem = self._dir_problem(src_dir, parts, logical, phys, root, unit_root)
        if problem:
            code, detail = problem
            self._row("folder", logical, src_dir, code, detail, count_tree=src_dir)
            return
        same_vol = self._same_vol_dir(src_dir, phys)
        try:
            entries = sorted(os.scandir(long_path(src_dir)), key=lambda e: e.name.lower())
        except OSError as e:
            self._row("folder", logical, src_dir, UNREADABLE, failure.classify(e, str(src_dir)).label)
            return
        files, dirs = [], []
        for e in entries:
            if e.name.startswith(OWN_PREFIX) or e.name.endswith(PART_SUFFIX):
                continue
            child = src_dir / e.name
            clog = logical.rstrip("\\") + "\\" + e.name
            if e.is_symlink() or (e.is_dir(follow_symlinks=False) and fs.is_reparse(child)):
                self._row("folder" if e.is_dir(follow_symlinks=False) else "file", clog, child, LINK)
            elif e.is_dir(follow_symlinks=False):
                dirs.append(e.name)
            elif e.is_file(follow_symlinks=False):
                files.append(e)
            else:
                self._row("file", clog, child, UNKNOWN_LOC, "특수 파일")
        if not files and not dirs and self.phase == "apply" and not self.dry_run:
            try:
                os.makedirs(long_path(phys), exist_ok=True)
            except OSError:
                pass
        for e in files:
            self._check_cancel()
            flog = logical.rstrip("\\") + "\\" + e.name
            try:
                self.file(Path(e.path), flog, phys / e.name, root, same_vol)
            except (Cancelled, DriveDisconnected, DiskFull):
                raise
            except Exception as ex:  # noqa: BLE001 - 파일 하나의 예상 밖 오류는 그 파일만 실패로 기록하고 계속한다
                if self.phase == "apply":
                    self._fail(Path(e.path), flog, failure.OTHER, type(ex).__name__, ex)
        for d in dirs:
            self.walk(src_dir / d, parts + (d,), letter, root)

    def _dir_problem(self, src_dir, parts, logical, phys, root, unit_root) -> tuple[str, str] | None:
        if parts and name_problem(parts[-1]):
            return UNSAFE_NAME, parts[-1]
        why = protected_reason(logical, is_dir=True)
        if why:
            return PROTECTED, why
        if _is_within(phys, self.recv):
            return INSIDE_RECEIVE, ""
        if unit_root:
            link = fs.reparse_in_chain(root, phys)
            if link:
                return LINK, link
            # 위쪽 경로 요소가 파일이면 폴더를 만들 수 없다
            cur = root
            for p in parts[:-1] if parts else ():
                cur = cur / p
                if os.path.lexists(cur) and not os.path.isdir(cur):
                    return BLOCKED, str(cur)
        if os.path.lexists(phys):
            if os.path.islink(phys) or fs.is_reparse(phys):
                return LINK, str(phys)
            if not os.path.isdir(phys):
                return BLOCKED, str(phys)
        return None

    # ---- 파일
    def file(self, src: Path, logical: str, phys: Path, root: Path, same_vol: bool) -> None:
        try:
            size = os.stat(long_path(src)).st_size
        except OSError as e:
            self._row("file", logical, src, UNREADABLE, failure.classify(e, str(src)).label)
            return
        name = src.name
        if name_problem(name):
            self._row("file", logical, src, UNSAFE_NAME, name)
            return
        why = protected_reason(logical, is_dir=False)
        if why:
            self._row("file", logical, src, PROTECTED, why)
            return
        if _is_within(phys, self.recv):
            self._row("file", logical, src, INSIDE_RECEIVE)
            return
        if os.path.lexists(phys) and (os.path.islink(phys) or fs.is_reparse(phys)):
            self._row("file", logical, src, LINK, str(phys))
            return
        mode = self._mode(same_vol)
        if self.phase == "scan":
            t = self.totals.setdefault(self.cur_key, _Totals())
            t.files += 1
            t.bytes += size
            if mode != MOVE:
                try:
                    st = os.stat(long_path(phys))
                    same = st.st_size == size
                except OSError:
                    same = False
                if not same:
                    t.copy_bytes += size
            return
        self._apply_file(src, logical, phys, root, size, mode)

    def _emit(self, src: Path, size: int, status: str, reason: str = "") -> None:
        t = self.tracker
        if t is None:
            return
        t.add(size, 1)
        t.note(categorize(status, reason), str(src))
        t.set_current(str(src))
        now = time.monotonic()
        if self.progress_info is not None and now - self._last_emit >= 0.2:
            self._last_emit = now
            self.progress_info(t.snapshot(f"{src.name}"))

    def _count(self, key: str, n: int = 1) -> None:
        self.rep.counts[key] = self.rep.counts.get(key, 0) + n
        self.unit_counts[key] = self.unit_counts.get(key, 0) + n

    def _record_ok(self, src: Path, phys: Path, status: str, size: int, mode: str, backup: str = "", removed: bool = False) -> None:
        self._count(status)
        self.rep.placed_bytes += size if status not in ("skipped_identical",) else 0
        self.rep.modes[mode] = self.rep.modes.get(mode, 0) + 1
        if removed:
            self.rep.removed_sources += 1
        if len(self.rep.placed) < MAX_LISTED:
            self.rep.placed.append({"src": fs.plain_path_text(str(src)), "dst": fs.plain_path_text(str(phys)), "status": status,
                                    "size": size, "mode": mode, "backup": fs.plain_path_text(backup), "source_removed": removed})
        else:
            self.rep.placed_truncated += 1

    def _fail(self, src: Path, logical: str, code: str, detail: str, exc: BaseException | None = None) -> None:
        """파일 하나의 실패를 기록한다(오류 로그 + 실패 표). 공간 부족은 공통 원인이라 기록 뒤 전체를 멈춘다."""
        self._count("failed")
        self.rep.failed.append(Row("file", logical, str(src), code, detail))
        errorlog.record(exc if exc is not None else OSError(f"{code}: {detail}"), f"pcmigrate place: {src}")
        if code == failure.DISK_FULL:
            raise DiskFull(failure.REASONS[failure.DISK_FULL].text)

    def _delete_source(self, src: Path, size: int, logical: str) -> bool:
        """대상에 온전히 있다는 것이 확인된 뒤에만 부른다. 크기가 달라졌거나 링크면 지우지 않는다."""
        try:
            if os.path.islink(long_path(src)) or os.stat(long_path(src)).st_size != size:
                raise OSError("받기 폴더의 파일이 바뀌었거나 링크라 지우지 않았습니다")
            os.unlink(long_path(src))
            if len(self.rep.removed_list) < MAX_LISTED:
                self.rep.removed_list.append(fs.plain_path_text(str(src)))
            else:
                self.rep.removed_truncated += 1
            return True
        except OSError as e:
            errorlog.record(e, f"pcmigrate place: 받기 폴더 파일 삭제 실패 {src}")
            self.rep.leftover.append(Row("file", logical, str(src), LEFTOVER, failure.classify(e, str(src)).label))
            return False

    def _apply_file(self, src: Path, logical: str, phys: Path, root: Path, size: int, mode: str) -> None:
        try:
            self._apply_file_inner(src, logical, phys, root, size, mode)
        except (Cancelled, DriveDisconnected, DiskFull):
            raise
        except OSError as e:  # 예상 밖 OS 오류도 파일 하나로 격리
            f = failure.classify(e, str(src))
            self._emit(src, size, "failed", f.code)
            self._fail(src, logical, f.code, "", e)
        except Exception as e:  # noqa: BLE001 - 한 파일 때문에 전체를 멈추지 않는다(오류 로그 + 실패 표)
            self._emit(src, size, "failed", failure.OTHER)
            self._fail(src, logical, failure.OTHER, type(e).__name__, e)

    def _apply_file_inner(self, src: Path, logical: str, phys: Path, root: Path, size: int, mode: str) -> None:
        exists = os.path.lexists(long_path(phys))
        if exists and os.path.isdir(long_path(phys)):
            f = failure.REASONS[failure.DEST_IS_DIR]
            self._emit(src, size, "failed", f.code)
            self._fail(src, logical, f.code, str(phys))
            return
        identical = False
        if exists and os.stat(long_path(phys)).st_size == size:
            identical = (copier.sha256_file(src, cancel=self.cancel) == copier.sha256_file(phys, cancel=self.cancel))
        if identical:
            if self.dry_run:
                self._count("skipped_identical")
                if mode != KEEP:
                    self.rep.delete_planned += 1
                    self.rep.delete_planned_bytes += size
            else:
                removed = mode != KEEP and self._delete_source(src, size, logical)
                self._record_ok(src, phys, copier.SKIPPED_SAME, size, mode, removed=removed)
            self._emit(src, size, "skipped_identical")
            return
        if exists and self.no_overwrite:
            self._count("refused")
            self.rep.skipped.append(Row("file", logical, str(src), NO_OVERWRITE))
            self._emit(src, size, "denied")
            return
        if self.dry_run:
            if mode == MOVE:
                self._count("would_overwrite" if exists else "would_move")
            else:
                self._count("would_overwrite" if exists else "would_copy")
                if mode == COPY_DELETE:
                    self.rep.delete_planned += 1
                    self.rep.delete_planned_bytes += size
            self.rep.modes[mode] = self.rep.modes.get(mode, 0) + 1
            self.rep.placed_bytes += size
            self._emit(src, size, "would_copy")
            return
        if mode == MOVE:
            res = copier.FileResult(str(src), str(phys), copier.FAILED, size=size)
            try:
                os.makedirs(long_path(phys.parent), exist_ok=True)
                copier._install(src, phys, res, long_path)  # 기존 파일은 .bak, 실패하면 되돌림
                if os.path.exists(long_path(src)) or os.stat(long_path(phys)).st_size != size:
                    raise OSError("이동 뒤 확인(크기/존재) 실패")
                self._record_ok(src, phys, "moved", size, MOVE, res.backup)
                self._emit(src, size, "moved")
                return
            except OSError as e:
                if not _is_exdev(e):
                    f = failure.classify(e, str(src))
                    if f.code == failure.DISCONNECTED and not self.alive(root):
                        raise DriveDisconnected(DISCONNECTED_MESSAGE) from e
                    self._emit(src, size, "failed", f.code)
                    self._fail(src, logical, f.code, "", e)
                    return
                mode = COPY_DELETE  # 다른 볼륨이었음: 복사-삭제로 대체
        r = copier.copy_verified(src, phys, cancel=self.cancel)
        if r.status in (copier.COPIED, copier.SKIPPED_SAME):
            removed = mode == COPY_DELETE and self._delete_source(src, size, logical)
            self._record_ok(src, phys, r.status, size, mode, r.backup, removed)
            self._emit(src, size, r.status)
        else:
            if r.reason == failure.DISCONNECTED and not self.alive(root):
                raise DriveDisconnected(DISCONNECTED_MESSAGE)
            self._emit(src, size, "failed", r.reason)
            self._fail(src, logical, r.reason or failure.OTHER, "", OSError(r.error))


def _count_tree(p: Path) -> tuple[int, int]:
    n = b = 0
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not fs.is_reparse(os.path.join(root, d))]
        for f in files:
            if f.startswith(OWN_PREFIX) or f.endswith(PART_SUFFIX):
                continue
            n += 1
            try:
                b += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n, b


def _remaining(recv: Path, limit: int = 50) -> dict:
    """받기 폴더에 남은 파일(개수/용량)과 빈 폴더(예시 경로). 폴더는 지우지 않는다."""
    files = size = 0
    empty: list[str] = []
    n_empty = 0
    for root, dirs, fl in os.walk(recv):
        dirs[:] = [d for d in dirs if not fs.is_reparse(os.path.join(root, d))]
        real = [f for f in fl if not f.startswith(OWN_PREFIX)]
        for f in real:
            files += 1
            try:
                size += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
        if not real and not dirs and Path(root) != recv:
            n_empty += 1
            if len(empty) < limit:
                empty.append(root)
    return {"files": files, "bytes": size, "empty_folders": n_empty, "empty_folder_examples": empty}


def place(recv: str | os.PathLike, *, apply: bool = False, maps: dict[str, str] | None = None, keep: bool = False,
          no_overwrite: bool = False, include_unverified: bool = False,
          drive_root_fn: Callable[[str], Path | None] = default_drive_root,
          same_volume_fn: Callable[[str, str], bool] = _same_volume,
          dest_info_fn=None, progress_info: Callable[[ProgressInfo], None] | None = None,
          cancel: Callable[[], bool] | None = None, warn: Callable[[str], None] = lambda m: None) -> PlaceReport:
    """받기 폴더의 내용을 원래 위치로 놓는다(apply=False 면 미리보기만: 아무것도 바꾸지 않는다).

    어떤 이유로 끝나든 보고서가 남는다: 정상 종료는 PlaceReport 를 돌려주고, 취소/드라이브 분리/공간 부족/예상 못 한 오류는
    예외를 다시 던지되 그 예외의 .report 에 지금까지의 결과(meta["interrupted"] 에 사유)를 붙여 둔다. 호출자는 반드시 이를 저장/출력한다.
    """
    recv = Path(recv)
    if not recv.is_dir():
        raise TransferError(f"받기 폴더가 없습니다: {recv}")
    rep = PlaceReport(dry_run=not apply, keep=keep, recv=str(recv), maps=dict(maps or {}))
    t0 = time.monotonic()
    try:
        _place_impl(rep, recv, apply, keep, no_overwrite, include_unverified, drive_root_fn, same_volume_fn, dest_info_fn,
                    progress_info, cancel, warn)
    except (Cancelled, KeyboardInterrupt) as e:
        rep.meta["interrupted"] = "cancelled"
        _attach(e, rep, recv, apply, t0)
        raise
    except (DriveDisconnected, DiskFull) as e:
        rep.meta["interrupted"] = "drive_disconnected" if isinstance(e, DriveDisconnected) else "disk_full"
        rep.meta["error"] = str(e)
        rep.meta["error_log"] = str(errorlog.record(e, "pcmigrate place") or "")
        _attach(e, rep, recv, apply, t0)
        raise
    except TransferError as e:  # 입력/환경 오류(놓을 폴더 없음 등): 오류 로그 대상은 아니지만 보고서는 남긴다
        rep.meta["interrupted"] = "error"
        rep.meta["error"] = str(e)
        _attach(e, rep, recv, apply, t0)
        raise
    except Exception as e:  # noqa: BLE001 - 예상 못 한 오류도 보고서와 오류 로그를 남기고 알린다
        rep.meta["interrupted"] = "error"
        rep.meta["error"] = f"{type(e).__name__}: {e}"
        rep.meta["error_log"] = str(errorlog.record(e, "pcmigrate place") or "")
        _attach(e, rep, recv, apply, t0)
        raise
    _finish(rep, recv, apply, keep, t0)
    return rep


def _attach(e: BaseException, rep: PlaceReport, recv: Path, apply: bool, t0: float) -> None:
    _finish(rep, recv, apply, False, t0)
    try:
        e.report = rep  # type: ignore[attr-defined]
    except AttributeError:
        pass


def _place_impl(rep: PlaceReport, recv: Path, apply: bool, keep: bool, no_overwrite: bool, include_unverified: bool,
                drive_root_fn, same_volume_fn, dest_info_fn, progress_info, cancel, warn) -> None:
    maps = rep.maps
    units, rows, meta = discover(recv, maps, include_unverified, warn)
    rep.meta.update(meta)
    rep.skipped.extend(rows)
    if not units and not rows:
        raise TransferError("받기 폴더에서 놓을 폴더를 찾지 못했습니다(<드라이브 문자>\\... 구조나 .pcmigrate-multi.json 저널이 필요합니다)")
    if not meta["journal"]:
        warn("저널이 없어 받기가 끝까지 검증됐는지 알 수 없습니다. 먼저 받기 검사(verify)를 마쳤는지 확인하세요.")
    p = _Placer(recv, dry_run=not apply, keep=keep, no_overwrite=no_overwrite, drive_root_fn=drive_root_fn,
                same_volume_fn=same_volume_fn, cancel=cancel, progress_info=progress_info, report=rep)
    info_fn = dest_info_fn or fs.describe_destination
    ready: list[tuple[Unit, Path, str]] = []
    # ---- 1) 사전 점검: 드라이브 존재/쓰기 가능, 수량, 용량
    p.phase = "scan"
    for i, u in enumerate(units):
        root = drive_root_fn(u.letter)
        if root is None:
            rep.skipped.append(Row("folder", u.logical, str(u.current), DRIVE_MISSING, f"{u.letter}:",
                                   *_count_tree(u.current)))
            continue
        if info_fn(str(root)).kind == "cdrom":
            rep.skipped.append(Row("folder", u.logical, str(u.current), READONLY_DRIVE, f"{u.letter}:", *_count_tree(u.current)))
            continue
        p.cur_key = str(i)
        p.walk(u.current, u.parts, u.letter, root, unit_root=True)
        ready.append((u, root, str(i)))
    need: dict[str, int] = {}
    for u, root, key in ready:
        need[u.letter] = need.get(u.letter, 0) + p.totals.get(key, _Totals()).copy_bytes
    blocked: set[str] = set()
    space: dict[str, dict] = {}
    for letter, nb in need.items():
        root = drive_root_fn(letter)
        required = int(nb * (1 + SPACE_MARGIN))
        free = info_fn(str(root)).free
        space[letter] = {"required_bytes": required, "free_bytes": free}
        if free is not None and nb and free < required:
            blocked.add(letter)
    rep.meta["space"] = space
    ready2 = []
    for u, root, key in ready:
        if u.letter in blocked:
            t = p.totals.get(key, _Totals())
            rep.skipped.append(Row("folder", u.logical, str(u.current), NO_SPACE,
                                   f"필요 약 {fmt_bytes(space[u.letter]['required_bytes'])}, 여유 {fmt_bytes(space[u.letter]['free_bytes'] or 0)}",
                                   t.files, t.bytes))
        else:
            ready2.append((u, root, key))
    total_files = sum(p.totals.get(k, _Totals()).files for _u, _r, k in ready2)
    total_bytes = sum(p.totals.get(k, _Totals()).bytes for _u, _r, k in ready2)
    rep.total_files, rep.total_bytes = total_files, total_bytes
    # ---- 2) 실행(또는 미리보기)
    p.phase = "apply"
    p.tracker = ProgressTracker("copy", total_bytes, total_files)
    try:
        for u, root, key in ready2:
            p.unit_counts = {}
            n_before = (len(rep.failed), len(rep.leftover), len(rep.skipped))
            p.walk(u.current, u.parts, u.letter, root, unit_root=True)
            n_skip = len(rep.skipped) - n_before[2]
            n_fail = len(rep.failed) - n_before[0]
            c = p.unit_counts
            done = sum(c.get(k, 0) for k in ("copied", "moved", "skipped_identical", "would_copy", "would_move", "would_overwrite"))
            status = ("preview" if rep.dry_run else "placed") if not (n_skip or n_fail) else ("partial" if done else "skipped")
            rep.items.append({"current": fs.plain_path_text(str(u.current)), "expected": u.logical, "origin": u.origin,
                              "status": status, "files": p.totals.get(key, _Totals()).files, "summary": dict(c)})
    finally:
        rep.meta["items_planned"] = len(ready2)
    if p.progress_info is not None:
        p.progress_info(p.tracker.snapshot("완료"))


def _finish(rep: PlaceReport, recv: Path, apply: bool, keep: bool, t0: float) -> None:
    rep.meta["seconds"] = round(time.monotonic() - t0, 1)
    if apply:
        rep.remaining = _remaining(recv)


# --------------------------------------------------------------------------- 출력
def summary_lines(rep: PlaceReport) -> list[str]:
    d = rep.to_dict()
    c = rep.counts
    mode_text = {MOVE: "이동(같은 볼륨, 용량 증가 없음)", COPY_DELETE: "복사 후 검증된 파일만 받기 폴더에서 삭제",
                 KEEP: "복사만(받기 폴더는 그대로 둠)", "mixed": "이동 + 복사 후 삭제", "auto": "-"}[rep.mode]
    if rep.dry_run:
        lines = [f"미리보기입니다(아무것도 바꾸지 않았습니다): 받기 폴더 {rep.recv} → 원래 위치",
                 f"- 방식: {mode_text}",
                 f"- 놓을 파일 {rep.total_files:,}개 / {fmt_bytes(rep.total_bytes)}: 이동 {c.get('would_move', 0)}개, 새로 복사 {c.get('would_copy', 0)}개, "
                 f"덮어쓰기 전 .bak 보존 {c.get('would_overwrite', 0)}개, 이미 같음 {c.get('skipped_identical', 0)}개"]
        if rep.delete_planned:
            lines.append(f"- 삭제 예정: 검증을 통과한 뒤 받기 폴더에서 지울 파일 {rep.delete_planned:,}개 / {fmt_bytes(rep.delete_planned_bytes)} "
                         "(되돌릴 수 없음. 지우지 않으려면 --keep)")
    else:
        lines = [f"배치를 마쳤습니다: 받기 폴더 {rep.recv} → 원래 위치", f"- 방식: {mode_text}",
                 f"- 이동 {c.get('moved', 0)}개, 복사 {c.get('copied', 0)}개, 이미 같음 {c.get('skipped_identical', 0)}개, "
                 f"받기 폴더에서 정리한 파일 {rep.removed_sources}개, 실패 {rep.failed_count}개"]
    lines.append(f"- 수동으로 옮길 항목: 건너뜀 {len(rep.skipped)}건, 실패 {len(rep.failed)}건, 받기 폴더 파일을 못 지운 것 {len(rep.leftover)}건")
    for title, rows in (("건너뜀", rep.skipped), ("실패", rep.failed), ("받기 폴더 쪽 삭제 못 함", rep.leftover)):
        if not rows:
            continue
        lines.append(f"[{title}] 파일/폴더 | 예상 위치 | 현재 위치 | 사유")
        for r in rows[:30]:
            what = f"폴더({r.files:,}개)" if r.kind == "folder" and r.files else r.kind.replace("folder", "폴더").replace("file", "파일")
            lines.append(f"  {what} | {fs.plain_path_text(r.expected)} | {fs.plain_path_text(r.current)} | {fs.plain_path_text(r.reason)}")
        if len(rows) > 30:
            lines.append(f"  … 외 {len(rows) - 30}건(전체는 --report 파일)")
    if rep.remaining:
        lines.append(f"- 받기 폴더에 남은 것: 파일 {rep.remaining['files']:,}개 / {fmt_bytes(rep.remaining['bytes'])}, 빈 폴더 {rep.remaining['empty_folders']}개"
                     "(빈 폴더는 지우지 않았습니다. 확인한 뒤 직접 지우세요)")
    if rep.meta.get("interrupted"):
        lines.append(f"중단했습니다({rep.meta['interrupted']}{': ' + rep.meta['error'] if rep.meta.get('error') else ''}). 같은 명령을 다시 실행하면 이어서 합니다(파일마다 원래 자리나 새 자리에 온전히 있습니다).")
    elif rep.dry_run:
        lines.append("다음 할 일: 이상 없으면 같은 명령에 --yes 를 붙여 실행하세요. 파일 내용 안의 경로는 고쳐 쓰지 않습니다.")
    elif rep.manual_count:
        lines.append("다음 할 일: 표의 항목은 사용자가 직접 옮기세요(현재 위치의 파일은 그대로 있습니다).")
    return lines
