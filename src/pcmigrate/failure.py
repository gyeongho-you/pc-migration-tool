"""파일 읽기/쓰기 실패와 네트워크 접속 실패를 원인별로 나누고 쉬운 문구로 바꾼다(리포트의 reason 코드와 error 문구)."""
from __future__ import annotations

import errno
import os
import re
from dataclasses import dataclass

from .fs import long_path

IN_USE, PERMISSION, PATH_TOO_LONG = "in_use", "permission", "path_too_long"
DISK_FULL, DISCONNECTED, NOT_FOUND, OTHER = "disk_full", "disconnected", "not_found", "other"
MISMATCH, MISSING_DEST, SIZE_MISMATCH = "mismatch", "missing_dest", "size_mismatch"
DEST_IS_DIR, LINK_IN_PATH, MANIFEST_ERRORS = "dest_is_dir", "link_in_path", "manifest_errors"
NET_NOT_FOUND, NET_LOGON, NET_CONFLICT, NET_DENIED = "net_not_found", "net_logon", "net_conflict", "net_denied"


class ReadLocked(PermissionError):
    """파일은 열렸는데 읽는 도중 접근 거부: 다른 프로세스가 바이트 범위를 잠근 경우(Python 은 winerror 없이 EACCES 로 알린다)."""


def read_chunk(read, *args):
    """read(*args) 를 부르되, 열린 뒤의 PermissionError 는 '사용 중'(ReadLocked)으로 바꾼다."""
    try:
        return read(*args)
    except ReadLocked:
        raise
    except PermissionError as e:
        raise ReadLocked(e.errno, e.strerror or "locked", e.filename) from e


@dataclass(frozen=True)
class Failure:
    code: str
    label: str
    hint: str

    @property
    def text(self) -> str:
        return f"{self.label}. {self.hint}" if self.hint else self.label


REASONS: dict[str, Failure] = {f.code: f for f in (
    Failure(IN_USE, "다른 프로그램이 파일을 사용 중입니다",
            "해당 프로그램(CAD, 탐색기 미리보기 등)을 닫고 '실패한 것만 다시 시도'를 실행하세요."),
    Failure(PERMISSION, "접근 권한이 없습니다", "파일 권한이나 관리자 권한을 확인한 뒤 다시 시도하세요."),
    Failure(PATH_TOO_LONG, "경로가 너무 깁니다", "폴더 이름을 줄이거나 더 위쪽 폴더로 옮긴 뒤 다시 시도하세요."),
    Failure(DISK_FULL, "저장 공간이 부족합니다", "여유 공간이 있는 드라이브로 바꾸거나 정리한 뒤 다시 시도하세요."),
    Failure(DISCONNECTED, "드라이브나 네트워크 연결이 끊어졌습니다",
            "케이블과 연결 상태를 확인한 뒤 같은 설정으로 다시 실행하면 이어서 합니다."),
    Failure(NOT_FOUND, "파일을 찾을 수 없습니다", "원본이 옮겨지거나 지워지지 않았는지 확인하세요."),
    Failure(OTHER, "파일을 읽는 중 문제가 생겼습니다", "경로와 상태를 확인한 뒤 다시 시도하세요."),
    Failure(MISMATCH, "내용이 원본과 다릅니다", "'실패한 것만 다시 시도'로 다시 복사하세요."),
    Failure(SIZE_MISMATCH, "크기가 원본과 다릅니다", "'실패한 것만 다시 시도'로 다시 복사하세요."),
    Failure(MISSING_DEST, "대상에 파일이 없습니다", "'실패한 것만 다시 시도'로 다시 복사하세요."),
    Failure(DEST_IS_DIR, "대상에 같은 이름의 폴더가 있습니다",
            "그 폴더의 이름을 바꾸거나 옮긴 뒤 '실패한 것만 다시 시도'를 실행하세요(폴더는 도구가 지우지 않습니다)."),
    Failure(LINK_IN_PATH, "복원 위치가 바로가기(정션/심볼릭 링크)를 거칩니다",
            "링크 너머의 폴더는 따라가지 않으므로 복원하지 않았습니다. 링크를 없애거나 실제 폴더로 바꾼 뒤 다시 시도하세요."),
    Failure(MANIFEST_ERRORS, "구 PC 에서 읽지 못해 목록에서 빠진 파일이 있습니다",
            "구 PC 의 해시 목록 만들기/전송 결과에서 실패한 파일을 확인하고, 해당 파일을 닫은 뒤 다시 복사/목록 만들기를 하세요."),
    # 네트워크(공유 폴더) 접속
    Failure(NET_NOT_FOUND, "공유 폴더를 찾을 수 없습니다",
            "새 PC 의 IP 주소와 공유 이름이 맞는지, 두 PC 가 같은 공유기/스위치(또는 랜선으로 직접)에 연결돼 있는지, "
            "새 PC 의 네트워크 프로필이 '개인'인지 확인하세요. 탐색기 주소창에 \\\\IP주소\\공유이름 을 직접 입력해 열리는지 먼저 확인해 보세요."),
    Failure(NET_LOGON, "공유 폴더 로그인에 실패했습니다",
            "새 PC 에 만든 접속용 계정의 이름과 비밀번호가 맞는지 확인하세요. 비밀번호가 없는 계정은 네트워크 접속이 막힙니다."),
    Failure(NET_CONFLICT, "이미 다른 계정으로 그 PC 에 연결돼 있습니다",
            "탐색기에서 해당 PC 의 연결을 끊거나(cmd: net use * /delete) 같은 계정으로 다시 접속하세요."),
    Failure(NET_DENIED, "공유 폴더에 쓸 권한이 없습니다",
            "새 PC 의 공유 설정과 폴더 보안 탭에서 접속 계정에 '쓰기(변경)' 권한을 주었는지 확인하세요."),
)}

_WIN_IN_USE = {32, 33}
_WIN_PERMISSION = {5}
_WIN_TOO_LONG = {206}
_WIN_DISK_FULL = {112, 39}
_WIN_DISCONNECTED = {21, 55, 59, 64, 121, 1167, 1231}
_WIN_NOT_FOUND = {2, 3}
# 네트워크 접속 오류(robocopy 출력의 "ERROR n (0x...)" 와 Python OSError.winerror 가 같은 번호를 쓴다)
_NET_CODES = {53: NET_NOT_FOUND, 67: NET_NOT_FOUND, 1203: NET_NOT_FOUND, 1326: NET_LOGON, 1219: NET_CONFLICT,
              1311: NET_LOGON, 1385: NET_LOGON, 1909: NET_LOGON}


def network_failure(code: int) -> Failure | None:
    """Windows 오류 번호가 공유 폴더 접속 문제면 쉬운 설명, 아니면 None."""
    key = _NET_CODES.get(code)
    return REASONS[key] if key else None


def _sharing_violation(path: str) -> bool:
    """Python 의 open() 은 '다른 프로그램이 파일을 잡고 있음'(공유 위반)도 winerror 없는 EACCES 로 알린다. Windows 에서 읽기 전용으로
    다시 한 번 열어 보며 실제 오류 번호(32/33)를 확인한다. 열리면 False. 파일 내용은 읽지 않고 곧바로 닫는다."""
    if os.name != "nt" or not path:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.HANDLE]
        h = k.CreateFileW(long_path(path), 0x80000000, 7, None, 3, 0, None)
        if h in (None, wintypes.HANDLE(-1).value, 0xFFFFFFFF, -1):
            return ctypes.get_last_error() in _WIN_IN_USE
        k.CloseHandle(h)
    except Exception:  # 확인용일 뿐이므로 어떤 오류도 '모름'으로 둔다
        return False
    return False


def classify(exc: BaseException, path: str = "") -> Failure:
    """예외를 원인 분류로 바꾼다. 경로가 260자 이상인데 '없음'이면 경로 길이 문제로 본다."""
    if not isinstance(exc, OSError):
        return REASONS[OTHER]
    if isinstance(exc, ReadLocked):
        return REASONS[IN_USE]
    win = getattr(exc, "winerror", None)
    code = exc.errno
    shown = path or (exc.filename if isinstance(getattr(exc, "filename", None), str) else "")
    if win in _WIN_IN_USE:
        return REASONS[IN_USE]
    if win is None and isinstance(exc, PermissionError) and _sharing_violation(shown):
        return REASONS[IN_USE]
    if win in _WIN_TOO_LONG or code == errno.ENAMETOOLONG:
        return REASONS[PATH_TOO_LONG]
    if win is not None and network_failure(win):
        return network_failure(win)
    if isinstance(exc, PermissionError) or win in _WIN_PERMISSION or code in (errno.EACCES, errno.EPERM):
        return REASONS[PERMISSION]
    if win in _WIN_DISK_FULL or code == errno.ENOSPC:
        return REASONS[DISK_FULL]
    if win in _WIN_DISCONNECTED or code in (errno.ENODEV, errno.ENXIO, errno.EIO):
        return REASONS[DISCONNECTED]
    if isinstance(exc, FileNotFoundError) or win in _WIN_NOT_FOUND or code == errno.ENOENT:
        if len(shown.replace("\\\\?\\", "")) >= 260:
            return REASONS[PATH_TOO_LONG]
        return REASONS[NOT_FOUND]
    return REASONS[OTHER]


_ROBO_ERROR = re.compile(r"(?:ERROR|오류)\s+(\d+)\s+\(0x[0-9A-Fa-f]+\)")


def robocopy_failures(output: str) -> list[Failure]:
    """robocopy 출력(영어/한국어)에서 'ERROR n (0x...)' 줄의 번호를 찾아 원인 분류(중복 제거, 등장 순서)를 돌려준다."""
    seen: dict[str, Failure] = {}
    for m in _ROBO_ERROR.finditer(output or ""):
        n = int(m.group(1))
        f = network_failure(n)
        if f is None:
            if n in _WIN_IN_USE:
                f = REASONS[IN_USE]
            elif n in _WIN_PERMISSION:
                f = REASONS[NET_DENIED] if "\\\\" in output else REASONS[PERMISSION]
            elif n in _WIN_TOO_LONG:
                f = REASONS[PATH_TOO_LONG]
            elif n in _WIN_DISK_FULL:
                f = REASONS[DISK_FULL]
            elif n in _WIN_DISCONNECTED:
                f = REASONS[DISCONNECTED]
            elif n in _WIN_NOT_FOUND:
                f = REASONS[NOT_FOUND]
        if f is not None:
            seen.setdefault(f.code, f)
    return list(seen.values())
