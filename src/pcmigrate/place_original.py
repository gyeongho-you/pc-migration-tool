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
--gather: '위치 문제'로 못 놓은 항목(보호 위치, 없는 드라이브, 공간 부족, 지정 이름 폴더, UNC, 받기 폴더 안 등)을 한 폴더로 모은다.
원래 구조(D\프로젝트\A)를 유지해 그 아래에 두고, 방식은 place 와 같다(같은 볼륨 이동 / 복사-검증-삭제 / --keep 복사만).
읽기·복사 오류로 '실패'한 항목은 모으지 않고 받기 폴더에 둔다. 모은 폴더에 `이전 안내.txt`(구조/예상 원위치/사유)를 쓴다.

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
from .multi_transfer import JOURNAL_NAME, JOURNAL_VERSION, dest_rel
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
MAP_CONFLICT = "map_conflict"
GATHER_DIR_NAME = "이전 파일"
GUIDE_NAME = "이전 안내.txt"
GUIDE_FILES_PER_ROW = 500
GUIDE_MAX_BYTES = 1_000_000  # 안내 파일 상한(줄 경계에서 자른다)
_GUIDE_HEADER = "모은 위치(이 폴더 기준) | 예상 원위치 | 사유"
# 모으기 대상: 원래 위치에 놓을 수 없는 '위치 문제'로 건너뛴 사유만(실패/덮어쓰기 거부/링크/미검증/이름 문제는 모으지 않는다)
GATHER_CODES = frozenset({"protected_system", "drive_missing", "no_space", "unknown_location", "unc", "inside_receive",
                         "readonly_drive", "dest_blocked"})
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
    MAP_CONFLICT: ("--map 때문에 받기 폴더의 서로 다른 드라이브가 같은 드라이브로 합쳐져 건너뜀", "--map 을 바꾸거나 받기 폴더의 해당 드라이브를 따로 처리하세요(도구는 두 드라이브를 한 곳에 합치지 않습니다)."),
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


_SHORT83 = re.compile(r"~\d")


def _long_name(path: str) -> str | None:
    """8.3 짧은 이름이 든 경로를 긴 이름으로 풀어 돌려준다(Windows GetLongPathNameW). 없는 뒷부분은 그대로 붙인다. 풀 수 없으면 None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        parts = path.split("\\")
        for i in range(len(parts), 0, -1):
            head = "\\".join(parts[:i])
            if i == 1:
                head += "\\"
            if ctypes.windll.kernel32.GetLongPathNameW(head, buf, 32768):
                return "\\".join([buf.value.rstrip("\\")] + parts[i:])
    except (AttributeError, OSError):
        return None
    return None


expand_short_names = _long_name  # 시험에서 바꿔 끼운다


def protected_reason(logical: str, *, is_dir: bool = True) -> str | None:
    """원래 위치가 보호 위치인지. 경로에 8.3 짧은 이름(~숫자)이 있으면 긴 이름으로 풀어 판정하고, 풀 수 없으면 보수적으로 보호로 본다."""
    if _SHORT83.search(ntpath.splitdrive(logical)[1]):
        longer = expand_short_names(logical)
        if longer is None:
            return "8.3 짧은 이름 경로(긴 이름을 확인할 수 없어 보호 위치로 취급)"
        logical = longer
    return _protected_reason(logical, is_dir=is_dir)


def _protected_reason(logical: str, *, is_dir: bool = True) -> str | None:
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
    via_gather: bool = False  # 모으는 중 생긴 실패(원본 행과 이중으로 세지 않기 위한 표시)

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
    gather_rep: "PlaceReport | None" = None  # --gather 결과(모은 파일의 성공/실패/삭제 기록)
    gather_info: dict = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)  # 결과는 정상이지만 확인이 필요한 일(이동 뒤 확인 경고 등)
    gathered: list[dict] = field(default_factory=list)  # 모은 항목 표: 원래 놓으려던 위치 / 모은 위치 / 사유

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
        return len(self.skipped) + len([f for f in self.failed if not f.via_gather]) + len(self.leftover) + len(self.gathered)

    def to_dict(self) -> dict:
        placed_n = sum(self.counts.get(k, 0) for k in ("copied", "moved", "would_copy", "would_move", "would_overwrite"))
        d = {"dry_run": self.dry_run, "mode": self.mode, "keep": self.keep, "recv_root": fs.plain_path_text(self.recv),
             "maps": self.maps, "summary": dict(self.counts), "modes": dict(self.modes), "total_files": self.total_files,
             "total_bytes": self.total_bytes, "placed_files": placed_n, "placed_bytes": self.placed_bytes,
             "removed_sources": self.removed_sources, "removed_files": self.removed_list,
             "removed_truncated": self.removed_truncated, "failed_count": self.failed_count,
             "manual_count": self.manual_count, "needs_manual": self.manual_count > 0, "warnings": self.warnings, "items": self.items,
             "skipped": [r.to_dict() for r in self.skipped[:MAX_LISTED]], "skipped_count": len(self.skipped),
             "failed": [r.to_dict() for r in self.failed[:MAX_LISTED]],
             "leftover": [r.to_dict() for r in self.leftover[:MAX_LISTED]],
             "placed": self.placed, "placed_truncated": self.placed_truncated, "remaining_in_receive": self.remaining, **self.meta}
        if self.gather_info:
            g = self.gather_rep
            d["gather"] = {**self.gather_info, "summary": dict(g.counts) if g else {}, "gathered_count": len(self.gathered),
                           "gathered": self.gathered[:MAX_LISTED], "placed": g.placed if g else [],
                           "removed_files": g.removed_list if g else [], "removed_sources": g.removed_sources if g else 0,
                           "delete_planned_files": g.delete_planned if g else 0,
                           "delete_planned_bytes": g.delete_planned_bytes if g else 0, "mode": g.mode if g else "auto"}
        if self.dry_run:
            d["preview"] = True
            d["delete_planned_files"] = self.delete_planned
            d["delete_planned_bytes"] = self.delete_planned_bytes
        d["success"] = (not self.failed and not self.skipped and not self.leftover and not self.gathered
                        and not self.meta.get("interrupted") and not self.warnings
                        and not (not self.dry_run and not self.keep and self.remaining.get("files", 0)))
        return d

    def write(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- 모으기 폴더
_ONEDRIVE_NAME = re.compile(r"^onedrive(?:\s*-.*)?$")


def onedrive_redirected(path: str | os.PathLike, env: dict | None = None) -> bool:
    """경로가 OneDrive 동기화 폴더 안이면 True. 환경변수(OneDrive/OneDriveConsumer/OneDriveCommercial) 경로 아래인지를 우선 보고,
    보조로 상위 폴더 이름이 'OneDrive' 또는 'OneDrive - 이름' 꼴인지 본다(OneDriveBackup 같은 이름은 해당 없음)."""
    env = env if env is not None else os.environ
    norm = ntpath.normcase(ntpath.abspath(os.fspath(path)))
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        base = env.get(var)
        if base:
            b = ntpath.normcase(ntpath.abspath(base)).rstrip("\\")
            if norm == b or norm.startswith(b + "\\"):
                return True
    return any(_ONEDRIVE_NAME.match(part) for part in norm.replace("/", "\\").split("\\"))


def default_desktop() -> str:
    """바탕화면 폴더(Windows 알려진 폴더 조회 = find_data.default_user_folders 재사용, OneDrive 로 옮겨진 위치 포함)."""
    from .find_data import default_user_folders
    d = default_user_folders().get("desktop")
    return d or os.path.join(os.path.expanduser("~"), "Desktop")


def default_gather_folder(desktop_fn: Callable[[], str] | None = None) -> Path:
    return Path((desktop_fn or default_desktop)()) / GATHER_DIR_NAME


def _alternatives(recv: Path, env: dict | None) -> list[str]:
    """OneDrive 밖의 모으기 폴더 후보(문구용): 받기 폴더와 다른 드라이브의 '이전 파일', 사용자 프로필 아래 '이전 파일'."""
    out: list[str] = []
    rd = ntpath.splitdrive(os.path.abspath(recv))[0].upper()
    for i in range(26):
        letter = chr(65 + i)
        if f"{letter}:" != rd and os.path.isdir(f"{letter}:\\"):
            out.append(f"{letter}:\\{GATHER_DIR_NAME}")
            break
    home = os.path.join(os.path.expanduser("~"), GATHER_DIR_NAME)
    if not onedrive_redirected(home, env):
        out.append(home)
    return out


def resolve_gather(arg: str | None, recv: Path, *, desktop_fn=None, env: dict | None = None,
                   protected_fn=None) -> tuple[Path | None, dict]:
    """--gather 값(None=끔, ''=기본 바탕화면의 '이전 파일')을 검증해 (폴더, 정보)를 돌려준다.

    받기 폴더와 겹치거나(링크는 실제 경로 기준), 보호 위치이거나, 모양이 잘못된 경로(끝 따옴표 등)면 TransferError."""
    if arg is None:
        return None, {}
    default = not arg
    if not default:
        if '"' in arg:
            raise TransferError(f"모으는 폴더 경로에 따옴표(\")가 들어 있습니다: {arg!r}. 경로 끝이 역슬래시(\\)인 채 따옴표로 닫으면 cmd 가 따옴표를 경로의 일부로 넘깁니다. "
                                "끝의 역슬래시를 빼고 다시 지정하세요(--gather 폴더).")
        if arg.startswith("~"):
            arg = os.path.expanduser(arg)
    g = Path(os.path.abspath(default_gather_folder(desktop_fn) if default else arg))
    real = Path(os.path.realpath(g))
    pf = protected_fn or protected_reason
    for cand in dict.fromkeys((g, real)):
        if _is_within(cand, recv) or _is_within(recv, cand):
            raise TransferError(f"모으는 폴더가 받기 폴더 안이거나 받기 폴더를 포함합니다: {g}. 받기 폴더와 겹치지 않는 다른 폴더를 지정하세요(--gather 폴더).")
        why = pf(str(cand), is_dir=True)
        if why:
            raise TransferError(f"모으는 폴더가 보호 위치입니다({why}): {cand}. 다른 폴더를 지정하세요(--gather 폴더).")
    if os.path.lexists(g) and not os.path.isdir(g):
        raise TransferError(f"모으는 폴더 자리에 같은 이름의 파일이 있습니다: {g}")
    return g, {"folder": str(g), "default_folder": default, "onedrive_redirected": onedrive_redirected(real, env) or onedrive_redirected(g, env)}


def _probe_writable(folder: Path) -> None:
    """폴더에 임시 파일을 만들었다 지워 쓸 수 있는지 확인한다(실패하면 OSError)."""
    t = folder / f".pcmigrate-write-test-{os.getpid()}"
    with open(long_path(t), "wb") as h:
        h.write(b"x")
    os.unlink(long_path(t))


def preflight_gather(g: Path, apply: bool, info_fn) -> None:
    """모으기 착수 전 점검: 도착 드라이브가 있는지, 쓸 수 있는 드라이브인지, (적용 시) 만들 수 있고 쓸 수 있는지. 실패하면 파일 단위가 아니라 한 번에 거부한다."""
    drive = ntpath.splitdrive(os.path.abspath(g))[0]
    anc = g
    while not os.path.lexists(anc) and anc.parent != anc:
        anc = anc.parent
    if (drive and not drive.startswith("\\\\") and not os.path.isdir(drive + "\\")) or not os.path.isdir(anc):
        raise TransferError(f"모으는 폴더의 드라이브를 찾을 수 없습니다: {g}. 모으는 폴더/드라이브 문제이며 아무것도 옮기지 않았습니다(--gather 로 다른 폴더를 지정하세요).")
    if info_fn(str(anc)).kind == "cdrom":
        raise TransferError(f"모으는 폴더가 쓸 수 없는 드라이브(CD/DVD)에 있습니다: {g}. 아무것도 옮기지 않았습니다(--gather 로 다른 폴더를 지정하세요).")
    if apply:
        try:
            _probe_writable(anc)
        except OSError as e:
            raise TransferError(f"모으는 폴더/드라이브 문제: {failure.classify(e, str(anc)).label} ({anc}). "
                                "받기 폴더의 원본과는 무관하며 아무것도 옮기지 않았습니다(--gather 로 쓸 수 있는 다른 폴더를 지정하세요).") from e


# --------------------------------------------------------------------------- 단위 찾기
@dataclass
class Unit:
    current: Path  # 받기 폴더 안의 폴더
    letter: str  # 대상 드라이브 문자(--map 적용 후)
    parts: tuple[str, ...]  # 드라이브 아래 경로 요소
    origin: str  # journal | inferred
    verified: bool | None = None  # 저널 상태가 done 인가(저널 없으면 None)
    src_letter: str = ""  # 받기 폴더에 있던 원래 드라이브 문자(--map 적용 전)

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


def _journal_state(recv: Path) -> tuple[str, dict]:
    """('none'|'ok'|'bad', items). 파일이 있는데 읽지 못하거나 버전/형식이 다르면 'bad'."""
    jp = recv / JOURNAL_NAME
    if not jp.is_file():
        return "none", {}
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "bad", {}
    if isinstance(data, dict) and data.get("version") == JOURNAL_VERSION and isinstance(data.get("items"), dict):
        return "ok", data["items"]
    return "bad", {}


def discover(recv: Path, maps: dict[str, str], include_unverified: bool, warn: Callable[[str], None]) -> tuple[list[Unit], list[Row], dict]:
    """받기 폴더에서 놓을 단위(폴더)와 처음부터 건너뛸 항목(Row)을 찾는다."""
    units: list[Unit] = []
    rows: list[Row] = []
    state, items = _journal_state(recv)
    meta: dict = {"journal": state == "ok" and bool(items), "journal_state": state, "journal_items": 0}
    journal_bad = state == "bad"
    if journal_bad:
        warn("저널(.pcmigrate-multi.json)이 있지만 읽을 수 없거나 버전이 달라 받기가 끝났는지 알 수 없습니다. "
             "확인 없이 진행하려면 --include-unverified 를 쓰세요(폴더 구조로 원래 위치를 유추).")

    def tgt(letter: str) -> str:
        return maps.get(letter.upper(), letter.upper())

    covered: set[str] = set()  # 저널이 다루는 최상위 폴더 이름(소문자)
    jrels: list[str] = []
    bad_tops: set[str] = set()  # 저널 항목이 깨진 최상위 폴더(유추로 놓지 않는다)
    for key, it in items.items():
        meta["journal_items"] += 1
        key_top = str(key).split("\\")[0].lower()
        if not isinstance(it, dict):
            bad_tops.add(key_top)
            rows.append(Row("folder", "-", str(recv / JOURNAL_NAME), BAD_JOURNAL, f"항목 형식 오류: {str(key)[:60]}"))
            continue
        src, rel, status = it.get("src"), it.get("rel"), it.get("status")
        if not isinstance(src, str) or not isinstance(rel, str) or _bad_rel(rel):
            bad_tops.add(key_top)
            if isinstance(rel, str):
                bad_tops.add(rel.split("\\")[0].lower())
            if isinstance(src, str) and ntpath.splitdrive(src)[0][:1].isalpha():
                bad_tops.add(ntpath.splitdrive(src)[0][:1].lower())
            rows.append(Row("folder", "-", str(recv / JOURNAL_NAME), BAD_JOURNAL, f"항목: {str(key)[:60]}"))
            continue
        covered.add(rel.split("\\")[0].lower())
        jrels.append(rel.lower())
        cur = recv.joinpath(*rel.split("\\"))
        if rel.lower() != dest_rel(src).lower():  # 이름을 지정해 받은 폴더(설정 묶음 등)
            if os.path.isdir(cur) and not _count_tree(cur)[0]:
                continue  # 파일이 없는 빈 폴더 뼈대(앞서 모으거나 옮긴 뒤의 잔여물)는 보고하지 않는다
            rows.append(Row("folder", "-", str(cur), UNKNOWN_LOC))
            continue
        if not os.path.lexists(cur):
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
        expected = f"{tgt(letter)}:\\" + "\\".join(parts)
        link = fs.reparse_in_chain(recv, cur)  # 받기 폴더 안의 링크/정션을 따라가지 않는다
        if link:
            rows.append(Row("folder", expected, str(cur), LINK, link))
            continue
        if not os.path.isdir(cur):
            meta["missing_items"] = meta.get("missing_items", 0) + 1
            continue
        if status != "done" and not include_unverified:
            rows.append(Row("folder", expected, str(cur), UNVERIFIED, f"상태: {status}"))
            continue
        units.append(Unit(cur, tgt(letter), parts, "journal", status == "done", letter.upper()))
    try:
        tops = sorted(os.scandir(long_path(recv)), key=lambda e: e.name.lower())
    except OSError as e:
        raise TransferError(f"받기 폴더를 읽을 수 없습니다: {recv} ({e})") from e

    def uncovered(cur_dir: Path, rel_parts: tuple[str, ...], cov: list[tuple[str, ...]], letter: str) -> None:
        """저널이 덮지 않는 하위 항목을 UNKNOWN_LOC 행으로 보고한다(저널이 덮는 경로는 건너뛴다)."""
        try:
            ents = sorted(os.scandir(long_path(cur_dir)), key=lambda x: x.name.lower())
        except OSError:
            rows.append(Row("folder", "-", str(cur_dir), UNREADABLE))
            return
        for x in ents:
            if x.name.startswith(OWN_PREFIX) or x.name.endswith(PART_SUFFIX):
                continue
            child = rel_parts + (x.name,)
            lc = tuple(p.lower() for p in child)
            if lc in cov:
                continue
            path = cur_dir / x.name
            isdir = x.is_dir(follow_symlinks=False)
            link = x.is_symlink() or (isdir and fs.is_reparse(path))
            if any(c[:len(lc)] == lc for c in cov):  # 저널 항목의 상위 폴더: 안으로 들어가 확인
                if isdir and not link:
                    uncovered(path, child, cov, letter)
                continue
            expected = f"{tgt(letter)}:\\" + "\\".join(child[1:])
            if isdir and not link and not _count_tree(path)[0]:
                continue  # 빈 폴더 뼈대
            rows.append(Row("folder" if isdir else "file", expected, str(path), LINK if link else UNKNOWN_LOC,
                            "" if link else "저널에 없는 항목"))

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
            if low in bad_tops:
                continue  # 저널 항목이 깨진 드라이브는 유추로 놓지 않는다(BAD_JOURNAL 행으로 보고됨)
            letter = name.upper()
            cov = [tuple(r.split("\\")) for r in jrels if r == low or r.startswith(low + "\\")]
            if cov:
                uncovered(cur, (name,), cov, letter)
            elif journal_bad and not include_unverified:
                rows.append(Row("folder", f"{tgt(letter)}:\\", str(cur), UNVERIFIED, "저널을 읽을 수 없음"))
            else:
                units.append(Unit(cur, tgt(letter), (), "inferred", None, letter))
        elif low == "unc":
            if "unc" in covered:
                continue
            for srv in sorted(p for p in cur.iterdir() if p.is_dir()):
                for share in sorted(p for p in srv.iterdir() if p.is_dir()):
                    rows.append(Row("folder", f"\\\\{srv.name}\\{share.name}", str(share), UNC))
        elif low not in covered:
            if _count_tree(cur)[0]:
                rows.append(Row("folder", "-", str(cur), UNKNOWN_LOC))
    by_target: dict[str, set[str]] = {}
    for u in units:
        by_target.setdefault(u.letter, set()).add(u.src_letter)
    clash = {t for t, srcs in by_target.items() if len(srcs) > 1}
    if clash:  # --map 으로 서로 다른 드라이브가 같은 드라이브로 합쳐지면 합치지 않고 모두 건너뛴다
        kept = []
        for u in units:
            if u.letter in clash:
                rows.append(Row("folder", u.logical, str(u.current), MAP_CONFLICT,
                                f"{'/'.join(sorted(by_target[u.letter]))} → {u.letter}"))
            else:
                kept.append(u)
        units = kept
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
        self._real_cache: dict[str, bool] = {}
        self.gather_mode = False  # True 면 대상은 '모은 폴더'(보호 위치 검사 없음, 보고 경로는 실제 경로)

    # ---- 보조
    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel():
            raise Cancelled()

    def _row(self, kind: str, logical: str, cur, code: str, detail: str = "", count_tree: Path | None = None) -> None:
        r = Row(kind, logical, str(cur), code, detail)
        if count_tree is not None:
            r.files, r.bytes = _count_tree(count_tree)
            if kind == "folder" and not r.files:
                return  # 파일이 없는 빈 폴더 뼈대는 사용자가 할 일이 아니므로 행으로 만들지 않는다
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

    def _src_safe(self, src: Path) -> bool:
        """원본(받기 폴더 쪽) 파일이 링크를 거쳐 받기 폴더 밖을 가리키지 않는지(이동/복사/삭제 직전 방어). 폴더 단위로 캐시한다."""
        d = src.parent
        key = os.path.normcase(str(d))
        ok = self._real_cache.get(key)
        if ok is None:
            ok = self._real_cache[key] = _is_within(Path(fs.plain_path_text(str(d))), self.recv)  # scandir 경로의 긴 경로 접두를 뗀다
        return ok and not os.path.islink(long_path(src))

    def _mode(self, same_vol: bool) -> str:
        return KEEP if self.keep else (MOVE if same_vol else COPY_DELETE)

    # ---- 폴더
    def walk(self, src_dir: Path, parts: tuple[str, ...], letter: str, root: Path, unit_root: bool = False) -> None:
        self._check_cancel()
        phys = root.joinpath(*parts)
        logical = str(phys) if self.gather_mode else f"{letter}:\\" + "\\".join(parts)
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
        why = None if self.gather_mode else protected_reason(logical, is_dir=True)
        if why:
            return PROTECTED, why
        if _is_within(phys, self.recv):
            return INSIDE_RECEIVE, ""
        if unit_root:
            slink = fs.reparse_in_chain(self.recv, src_dir)  # 받기 폴더 쪽 경로 중간의 링크/정션
            if slink:
                return LINK, slink
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
            st_src = os.stat(long_path(src))
            size = st_src.st_size
        except OSError as e:
            self._row("file", logical, src, UNREADABLE, failure.classify(e, str(src)).label)
            return
        if not self._src_safe(src):
            self._row("file", logical, src, LINK, "받기 폴더 밖을 가리키는 링크를 거침")
            return
        name = src.name
        if name_problem(name):
            self._row("file", logical, src, UNSAFE_NAME, name)
            return
        why = None if self.gather_mode else protected_reason(logical, is_dir=False)
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
                    # 같은 크기라도 수정 시각이 다르면 다른 파일일 수 있다(덮어쓰면 기존 파일은 .bak 으로 남아 공간이 더 필요)
                    same = st.st_size == size and st.st_mtime_ns == st_src.st_mtime_ns
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
        if self.dry_run:  # 미리보기: 사유 행만 남긴다(실패 집계/오류 로그 없음)
            self.rep.skipped.append(Row("file", logical, str(src), code, detail))
            return
        self._count("failed")
        self.rep.failed.append(Row("file", logical, str(src), code, detail))
        errorlog.record(exc if exc is not None else OSError(f"{code}: {detail}"), f"pcmigrate place: {src}")
        if code == failure.DISK_FULL:
            raise DiskFull(failure.REASONS[failure.DISK_FULL].text)

    def _delete_source(self, src: Path, size: int, logical: str, mtime_ns: int | None = None) -> bool:
        """대상에 온전히 있다는 것이 확인된 뒤에만 부른다. 크기/수정 시각이 달라졌거나 링크이거나 받기 폴더 밖이면 지우지 않는다."""
        try:
            st = os.stat(long_path(src))
            if (os.path.islink(long_path(src)) or st.st_size != size or (mtime_ns is not None and st.st_mtime_ns != mtime_ns)
                    or not self._src_safe(src)):
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

    def _after_move(self, src: Path, logical: str, phys: Path, size: int, backup: str) -> None:
        """이동(교체)이 끝난 뒤의 확인. 실제로 옮겨졌으면 성공으로 기록하고, 확인이 어긋나면 경고로 남긴다(실패로 세지 않는다)."""
        try:
            moved = os.stat(long_path(phys)).st_size == size
            src_left = os.path.exists(long_path(src))
        except OSError:
            moved, src_left = False, False
        if not moved:  # 교체는 끝났는데 대상이 보이지 않는다: 실제 상태를 알 수 없으므로 실패로 보고
            self._emit(src, size, "failed", failure.SIZE_MISMATCH)
            self._fail(src, logical, failure.SIZE_MISMATCH, "이동 뒤 대상 확인 실패", OSError("이동 뒤 확인(크기/존재) 실패"))
            return
        self._record_ok(src, phys, "moved", size, MOVE, backup)
        self._emit(src, size, "moved")
        if src_left:
            self.rep.warnings.append({"kind": "move_check", "expected": fs.plain_path_text(logical), "current": fs.plain_path_text(str(src)),
                                      "text": "이동했지만 받기 폴더에 같은 이름의 파일이 아직 보입니다. 직접 확인하세요."})

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
        mt0 = os.stat(long_path(src)).st_mtime_ns
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
                removed = mode != KEEP and self._delete_source(src, size, logical, mt0)
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
                self._after_move(src, logical, phys, size, res.backup)
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
            removed = mode == COPY_DELETE and self._delete_source(src, size, logical, mt0)
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
          cancel: Callable[[], bool] | None = None, warn: Callable[[str], None] = lambda m: None,
          gather: str | None = None, desktop_fn: Callable[[], str] | None = None, env: dict | None = None,
          protected_fn=None) -> PlaceReport:
    """받기 폴더의 내용을 원래 위치로 놓는다(apply=False 면 미리보기만: 아무것도 바꾸지 않는다).

    어떤 이유로 끝나든 보고서가 남는다: 정상 종료는 PlaceReport 를 돌려주고, 취소/드라이브 분리/공간 부족/예상 못 한 오류는
    예외를 다시 던지되 그 예외의 .report 에 지금까지의 결과(meta["interrupted"] 에 사유)를 붙여 둔다. 호출자는 반드시 이를 저장/출력한다.
    """
    recv = Path(recv)
    rep = PlaceReport(dry_run=not apply, keep=keep, recv=str(recv), maps=dict(maps or {}))
    t0 = time.monotonic()
    gather_opts = (gather, desktop_fn, env, protected_fn)
    try:
        if not recv.is_dir():
            raise TransferError(f"받기 폴더가 없습니다: {recv}")
        _place_impl(rep, recv, apply, keep, no_overwrite, include_unverified, drive_root_fn, same_volume_fn, dest_info_fn,
                    progress_info, cancel, warn, gather_opts)
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
                drive_root_fn, same_volume_fn, dest_info_fn, progress_info, cancel, warn, gather_opts=(None, None, None, None)) -> None:
    maps = rep.maps
    info_fn = dest_info_fn or fs.describe_destination
    groot, ginfo = resolve_gather(gather_opts[0], recv, desktop_fn=gather_opts[1], env=gather_opts[2], protected_fn=gather_opts[3])
    if groot is not None:  # 아무것도 바꾸기 전에 검증/경고
        rep.gather_info = ginfo
        if ginfo["default_folder"]:
            warn(f"--gather 폴더를 지정하지 않아 기본 폴더(바탕화면의 '{GATHER_DIR_NAME}')를 씁니다: {groot}")
        if ginfo["onedrive_redirected"]:
            alts = _alternatives(recv, gather_opts[2])
            hint = ("OneDrive 밖의 후보: " + ", ".join(alts) + ". ") if alts else ""
            if ginfo["default_folder"]:
                msg = ("바탕화면이 OneDrive 로 동기화되는 위치라 기본 모으기 폴더를 쓸 수 없습니다(모은 파일이 대용량이면 OneDrive 로 업로드될 수 있습니다). "
                       f"{hint}--gather 폴더 로 OneDrive 밖의 폴더를 직접 지정하세요(이 기본 폴더는 --yes 로도 적용할 수 없습니다).")
                ginfo["onedrive_blocked"] = True
                if apply:
                    raise TransferError(msg)
            else:
                msg = ("모으는 폴더가 OneDrive 동기화 폴더 안입니다. 모은 파일이 대용량이면 OneDrive 로 업로드(동기화)될 수 있습니다. " + hint
                       + ("" if apply else "그래도 이 폴더를 쓰려면 --yes 로 적용하세요."))
            warn(msg)
        preflight_gather(groot, apply, info_fn)
    units, rows, meta = discover(recv, maps, include_unverified, warn)
    rep.meta.update(meta)
    rep.skipped.extend(rows)
    if not units and not rows:
        if _count_tree(recv)[0]:
            raise TransferError("받기 폴더에서 놓을 폴더를 찾지 못했습니다(<드라이브 문자>\\... 구조나 .pcmigrate-multi.json 저널이 필요합니다)")
        warn("받기 폴더에 옮길 파일이 없습니다(이미 모두 처리됐거나 빈 폴더 뼈대만 남았습니다).")
        rep.meta["nothing_to_do"] = True
    if not meta["journal"]:
        warn("저널이 없어 받기가 끝까지 검증됐는지 알 수 없습니다. 먼저 받기 검사(verify)를 마쳤는지 확인하세요.")
    if groot is not None:  # 모으는 폴더가 이번 배치의 도착 폴더와 겹치면 섞이므로 거부(드라이브 전체 배치와는 경고)
        for u in units:
            root0 = drive_root_fn(u.letter)
            if root0 is None:
                continue
            dest = root0.joinpath(*u.parts)
            if u.parts and (_is_within(groot, dest) or _is_within(dest, groot)):
                raise TransferError(f"모으는 폴더가 이번 배치의 도착 폴더({u.logical})와 겹칩니다: {groot}. 다른 폴더를 지정하세요(--gather 폴더).")
            if not u.parts and _is_within(groot, dest):
                warn(f"모으는 폴더가 드라이브 전체({u.letter}:)를 놓는 위치 안에 있습니다. 같은 이름의 폴더가 있으면 섞일 수 있습니다.")
    p = _Placer(recv, dry_run=not apply, keep=keep, no_overwrite=no_overwrite, drive_root_fn=drive_root_fn,
                same_volume_fn=same_volume_fn, cancel=cancel, progress_info=progress_info, report=rep)
    ready: list[tuple[Unit, Path, str]] = []
    # ---- 1) 사전 점검: 드라이브 존재/쓰기 가능, 수량, 용량
    p.phase = "scan"
    for i, u in enumerate(units):
        root = drive_root_fn(u.letter)
        if root is None:
            n_files, n_bytes = _count_tree(u.current)
            if n_files:  # 파일이 없는 빈 뼈대 폴더는 사용자가 할 일이 아니므로 행으로 만들지 않는다
                rep.skipped.append(Row("folder", u.logical, str(u.current), DRIVE_MISSING, f"{u.letter}:", n_files, n_bytes))
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
            n_left = len(rep.leftover) - n_before[1]
            c = p.unit_counts
            done = sum(c.get(k, 0) for k in ("copied", "moved", "skipped_identical", "would_copy", "would_move", "would_overwrite"))
            status = ("preview" if rep.dry_run else "placed") if not (n_skip or n_fail or n_left) else ("partial" if done else "skipped")
            rep.items.append({"current": fs.plain_path_text(str(u.current)), "expected": u.logical, "origin": u.origin,
                              "status": status, "files": p.totals.get(key, _Totals()).files, "summary": dict(c), "leftover_count": n_left})
    finally:
        rep.meta["items_planned"] = len(ready2)
    if p.progress_info is not None:
        p.progress_info(p.tracker.snapshot("완료"))
    if groot is not None:
        _gather_leftovers(rep, recv, groot, apply, keep, no_overwrite, same_volume_fn, info_fn, progress_info, cancel, warn)


def _gather_one(gp: _Placer, r: Row, rel: tuple[str, ...], groot: Path) -> None:
    cur = Path(r.current)
    phys = groot.joinpath(*rel)
    slink = fs.reparse_in_chain(gp.recv, cur)  # 받기 폴더 안의 링크를 거치거나 그 자체가 링크이면 모으지 않는다
    if slink:
        gp._row("folder" if r.kind == "folder" else "file", str(phys), cur, LINK, slink)
        return
    if r.kind == "folder" and cur.is_dir():
        gp.walk(cur, rel, "G", groot, unit_root=True)
        return
    link = fs.reparse_in_chain(groot, phys.parent)
    if link:
        gp._row("file", str(phys), cur, LINK, link)
        return
    gp.file(cur, str(phys), phys, groot, gp._same_vol_dir(cur.parent, phys.parent))


_PLACED_KEYS = ("copied", "moved", "skipped_identical", "would_copy", "would_move", "would_overwrite")


def _restore_expected(x: Row, base: str, orig: str) -> None:
    """모으는 중 생긴 행의 '예상 위치'(모은 폴더 안 경로)를 원래 놓으려던 논리 경로로 되돌린다."""
    e = x.expected
    if e.lower().startswith(base.lower()):
        suffix = e[len(base):]
        x.expected = (orig.rstrip("\\") + suffix) if orig not in ("-", "") else (suffix.lstrip("\\") or "(원래 위치를 알 수 없음)")


def _gather_leftovers(rep: PlaceReport, recv: Path, groot: Path, apply: bool, keep: bool, no_overwrite: bool, same_volume_fn,
                      info_fn, progress_info, cancel, warn) -> None:
    """위치 문제로 건너뛴 항목을 groot 아래(받기 폴더 안 구조 그대로)로 모은다. 항목(폴더/파일) 단위로 격리하고 결과를 rep 에 바로 반영한다."""
    gat = PlaceReport(dry_run=not apply, keep=keep, recv=str(recv))
    rep.gather_rep = gat
    gp = _Placer(recv, dry_run=not apply, keep=keep, no_overwrite=no_overwrite, drive_root_fn=None,
                 same_volume_fn=same_volume_fn, cancel=cancel, progress_info=progress_info, report=gat)
    gp.gather_mode = True
    rows: list[tuple[Row, tuple[str, ...]]] = []
    for r in list(rep.skipped):
        if r.code not in GATHER_CODES or not os.path.lexists(r.current):
            continue
        if r.kind == "folder" and not _count_tree(Path(r.current))[0]:
            continue  # 파일이 없는 빈 폴더는 모으지 않는다(다시 실행할 때 같은 결과가 되도록)
        try:
            rel = Path(r.current).relative_to(recv).parts
        except ValueError:
            continue
        if rel:
            rows.append((r, rel))
    rep.gather_info["candidates"] = len(rows)
    if not rows:
        return
    gp.phase = "scan"
    for i, (r, rel) in enumerate(rows):
        gp.cur_key = str(i)
        gp.totals.setdefault(gp.cur_key, _Totals())
        _gather_one(gp, r, rel, groot)
    need = sum(t.copy_bytes for t in gp.totals.values())
    required = int(need * (1 + SPACE_MARGIN))
    free = info_fn(str(_nearest_existing(groot))).free
    rep.gather_info.update(required_bytes=required, free_bytes=free)
    if need and free is not None and free < required:
        rep.gather_info["blocked"] = "no_space"
        warn(f"모으는 폴더의 드라이브 여유 공간이 부족해 모으지 않았습니다(필요 약 {fmt_bytes(required)}, 여유 {fmt_bytes(free)}). 항목은 받기 폴더에 그대로 있습니다.")
        return
    total_files = sum(t.files for t in gp.totals.values())
    gp.phase = "apply"
    gp.tracker = ProgressTracker("copy", sum(t.bytes for t in gp.totals.values()), total_files)
    try:
        for i, (r, rel) in enumerate(rows):
            gp.unit_counts = {}
            n0 = (len(gat.failed), len(gat.leftover), len(gat.skipped))
            base = str(groot.joinpath(*rel))
            try:
                _gather_one(gp, r, rel, groot)
            finally:  # 취소/중단으로 빠져나가도 지금까지의 실패/남은 것은 원래 놓으려던 위치 기준으로 보고서에 반영한다
                for grp in (gat.failed[n0[0]:], gat.leftover[n0[1]:], gat.skipped[n0[2]:]):
                    for x in grp:
                        _restore_expected(x, base, r.expected)
                for x in gat.failed[n0[0]:]:
                    x.via_gather = True
                rep.failed.extend(gat.failed[n0[0]:])
                rep.leftover.extend(gat.leftover[n0[1]:])
                rep.skipped.extend(gat.skipped[n0[2]:])
            done = sum(gp.unit_counts.get(k, 0) for k in _PLACED_KEYS)
            bad = (len(gat.failed) - n0[0]) or (len(gat.skipped) - n0[2])
            if not bad:
                rep.skipped.remove(r)
            elif apply:  # 일부만 모았다: 원본 행은 받기 폴더에 남은 만큼으로 갱신(없으면 뺀다)
                left_files, left_bytes = _count_tree(Path(r.current)) if os.path.isdir(r.current) else (1 if os.path.lexists(r.current) else 0, 0)
                if left_files:
                    r.files, r.bytes = left_files, left_bytes
                else:
                    rep.skipped.remove(r)
            if done or not bad:
                status = ("preview" if not apply else "gathered") if not bad else "partial"
                where = fs.plain_path_text(base)
                rep.gathered.append({"kind": r.kind, "expected": fs.plain_path_text(r.expected), "current": where, "gathered": where,
                                     "received_from": fs.plain_path_text(r.current), "reason_code": r.code,
                                     "reason": fs.plain_path_text(r.reason), "files": done, "status": status})
                rep.items.append({"current": fs.plain_path_text(r.current), "expected": fs.plain_path_text(r.expected), "origin": "gather",
                                  "status": status, "files": done, "summary": dict(gp.unit_counts), "leftover_count": len(gat.leftover) - n0[1]})
    finally:
        if gp.progress_info is not None and gp.tracker is not None:
            gp.progress_info(gp.tracker.snapshot("모으기 마침"))
        if apply and rep.gathered:  # 중단/예외/공간 부족으로 끝나도 안내 파일은 남긴다
            _write_guide(rep, groot, warn)


def _guide_entries(rep: PlaceReport, groot: Path) -> dict[str, str]:
    """이번 실행에서 모은 것의 안내 줄(모은 위치 상대 경로 -> 한 줄)."""
    out: dict[str, str] = {}
    for g in rep.gathered:
        if g["status"] == "preview":
            continue
        rel = os.path.relpath(g["gathered"], groot)
        exp = g["expected"] if g["expected"] not in ("-", "") else ""
        reason = g["reason"]
        if g["kind"] == "folder" and os.path.isdir(g["gathered"]):
            n = 0
            for root, dirs, files in os.walk(g["gathered"]):
                dirs.sort()
                for f in sorted(files):
                    n += 1
                    if n > GUIDE_FILES_PER_ROW:
                        break
                    full = os.path.join(root, f)
                    sub = os.path.relpath(full, g["gathered"])
                    out[os.path.relpath(full, groot)] = f"{os.path.relpath(full, groot)} | {(exp.rstrip(chr(92)) + chr(92) + sub) if exp else '(알 수 없음)'} | {reason}"
                if n > GUIDE_FILES_PER_ROW:
                    out[rel] = f"{rel} | {exp or '(알 수 없음)'} | 파일이 많아 {GUIDE_FILES_PER_ROW}개까지만 나열했습니다 ({reason})"
                    break
        else:
            out[rel] = f"{rel} | {exp or '(알 수 없음)'} | {reason}"
    return out


def _write_guide(rep: PlaceReport, groot: Path, warn) -> None:
    """모은 폴더에 이전 안내.txt 를 쓴다(구조/예상 원위치/사유만, 받기 폴더·사용자 경로 없음). 이전 실행의 줄은 지금도 있는 파일이면 합쳐 둔다.
    실패해도 중단하지 않고 보고한다."""
    path = groot / GUIDE_NAME
    try:
        entries = _guide_entries(rep, groot)
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                    parts = line.split(" | ")
                    if len(parts) >= 3 and line != _GUIDE_HEADER and not line.startswith("==="):
                        key = parts[0]
                        if key not in entries and os.path.lexists(groot / key):  # 사용자가 이미 옮긴 파일의 줄은 뺀다
                            entries[key] = line
            except OSError as e:
                errorlog.record(e, f"pcmigrate place: 이전 안내.txt 읽기 {path}")
        head = ["이전 안내: 원래 위치에 놓을 수 없어 이 폴더에 모은 항목입니다. 아래 '예상 원위치'로 직접 옮기세요.",
                "(파일 내용 안의 경로는 고쳐 쓰지 않았습니다.)", "", _GUIDE_HEADER]
        lines, size = list(head), sum(len(x.encode("utf-8")) + 2 for x in head)
        keys = sorted(entries, key=str.lower)
        for i, k in enumerate(keys):
            n = len(entries[k].encode("utf-8")) + 2
            if size + n > GUIDE_MAX_BYTES:
                lines.append(f"… 외 {len(keys) - i}줄은 안내 파일 용량 한도로 생략했습니다")
                break
            lines.append(entries[k])
            size += n
        tmp = path.with_name(path.name + PART_SUFFIX)
        os.makedirs(groot, exist_ok=True)
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        os.replace(tmp, path)
        rep.gather_info["guide_file"] = str(path)
        rep.gather_info["guide_lines"] = len(entries)
    except OSError as e:
        errorlog.record(e, f"pcmigrate place: 이전 안내.txt 쓰기 {path}")
        rep.gather_info["guide_error"] = f"{type(e).__name__}: {failure.classify(e, str(path)).label}"
        warn(f"이전 안내.txt 를 쓰지 못했습니다({rep.gather_info['guide_error']}). 모은 항목 표는 리포트에 있습니다.")


def _finish(rep: PlaceReport, recv: Path, apply: bool, keep: bool, t0: float) -> None:
    """마무리(소요 시간, 받기 폴더에 남은 것 조사). 여기서 오류가 나도 원래 결과/예외를 덮어쓰지 않고 오류 로그와 보고서 메모로만 남긴다."""
    try:
        rep.meta["seconds"] = round(time.monotonic() - t0, 1)
        if apply:
            rep.remaining = _remaining(recv)
    except Exception as e:  # noqa: BLE001
        errorlog.record(e, "pcmigrate place: 마무리")
        rep.meta["finish_error"] = f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- 출력
_COUNT_LABELS = {"would_move": "이동 예정", "would_copy": "복사 예정", "would_overwrite": "덮어쓰기(.bak 보관) 예정", "skipped_identical": "이미 같음",
                 "moved": "이동", "copied": "복사"}


def _count_text(c: dict, keys: tuple[str, ...]) -> str:
    parts = [f"{_COUNT_LABELS[k]} {c[k]}개" for k in keys if c.get(k)]
    return ", ".join(parts) or "없음"


def summary_lines(rep: PlaceReport) -> list[str]:
    d = rep.to_dict()
    c = rep.counts
    mode_text = {MOVE: "이동(같은 볼륨, 용량 증가 없음)", COPY_DELETE: "복사 후 검증된 파일만 받기 폴더에서 삭제",
                 KEEP: "복사만(받기 폴더는 그대로 둠)", "mixed": "이동 + 복사 후 삭제", "auto": ""}[rep.mode]
    if rep.dry_run:
        lines = [f"미리보기입니다(아무것도 바꾸지 않았습니다): 받기 폴더 {rep.recv} → 원래 위치"]
        if rep.mode != "auto":
            lines.append(f"- 방식: {mode_text}")
        if rep.total_files:
            lines.append(f"- 놓을 파일 {rep.total_files:,}개 / {fmt_bytes(rep.total_bytes)}: "
                         + _count_text(c, ("would_move", "would_copy", "would_overwrite", "skipped_identical")))
        else:
            lines.append("- 원래 위치에 놓을 파일이 없습니다")
        if rep.delete_planned:
            lines.append(f"- 삭제 예정: 검증을 통과한 뒤 받기 폴더에서 지울 파일 {rep.delete_planned:,}개 / {fmt_bytes(rep.delete_planned_bytes)} "
                         "(되돌릴 수 없음. 지우지 않으려면 --keep)")
    else:
        lines = [f"배치를 마쳤습니다: 받기 폴더 {rep.recv} → 원래 위치"]
        if rep.mode != "auto":
            lines.append(f"- 방식: {mode_text}")
        lines.append("- " + _count_text(c, ("moved", "copied", "skipped_identical")) + (f", 받기 폴더에서 정리한 파일 {rep.removed_sources}개" if rep.removed_sources else "")
                     + f", 실패 {rep.failed_count}개")
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
    gi = rep.gather_info
    if gi:
        g = rep.gather_rep
        gc = g.counts if g else {}
        lines.append(f"- 모으기(--gather): 폴더 {gi['folder']}" + (" [OneDrive 동기화 폴더 — 업로드 주의]" if gi.get("onedrive_redirected") else ""))
        if gi.get("onedrive_redirected"):
            lines.append("  경고: 모은 파일이 OneDrive 로 동기화(업로드)될 수 있습니다. OneDrive 밖의 폴더를 쓰려면 --gather 폴더 로 지정하세요.")
        if gi.get("blocked"):
            lines.append("  모으는 폴더의 드라이브 여유 공간이 부족해 모으지 않았습니다.")
        elif rep.dry_run:
            lines.append(f"  모을 항목 {len(rep.gathered)}건: " + _count_text(gc, ("would_move", "would_copy", "would_overwrite", "skipped_identical")))
            if g and g.delete_planned:
                lines.append(f"  삭제 예정: 모은 뒤 받기 폴더에서 지울 파일 {g.delete_planned:,}개 / {fmt_bytes(g.delete_planned_bytes)} (되돌릴 수 없음)")
        else:
            lines.append(f"  모은 항목 {len(rep.gathered)}건: " + _count_text(gc, ("moved", "copied", "skipped_identical"))
                         + (f", 받기 폴더에서 정리한 파일 {g.removed_sources}개" if g and g.removed_sources else ""))
            if gi.get("guide_file"):
                lines.append(f"  안내 파일: {GUIDE_NAME}(모은 폴더 안)")
            if gi.get("guide_error"):
                lines.append(f"  안내 파일을 쓰지 못했습니다: {gi['guide_error']}")
        if rep.gathered:
            lines.append("[모은 항목] 파일/폴더 | 원래 놓으려던 위치 | 모은 위치 | 사유")
            for x in rep.gathered[:30]:
                lines.append(f"  {'폴더' if x['kind'] == 'folder' else '파일'}{'(일부)' if x['status'] == 'partial' else ''} | {x['expected']} | {x['gathered']} | {x['reason']}")
            if len(rep.gathered) > 30:
                lines.append(f"  … 외 {len(rep.gathered) - 30}건(전체는 --report 파일)")
    if rep.remaining:
        lines.append(f"- 받기 폴더에 남은 것: 파일 {rep.remaining['files']:,}개 / {fmt_bytes(rep.remaining['bytes'])}, 빈 폴더 {rep.remaining['empty_folders']}개"
                     "(빈 폴더는 지우지 않았습니다. 확인한 뒤 직접 지우세요)")
    if rep.meta.get("interrupted"):
        lines.append(f"중단했습니다({rep.meta['interrupted']}{': ' + rep.meta['error'] if rep.meta.get('error') else ''}). 같은 명령을 다시 실행하면 이어서 합니다(파일마다 원래 자리나 새 자리에 온전히 있습니다).")
    elif rep.dry_run:
        lines.append("다음 할 일: 이상 없으면 같은 명령에 --yes 를 붙여 실행하세요. 파일 내용 안의 경로는 고쳐 쓰지 않습니다.")
    elif rep.manual_count:
        lines.append("다음 할 일: " + ("모은 폴더의 이전 안내.txt 를 보고 모은 항목을 직접 놓고, " if rep.gathered else "")
                     + "표의 항목은 사용자가 직접 옮기세요('현재 위치' 열이 지금 파일이 있는 곳입니다).")
    return lines
