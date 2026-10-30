"""파일 시스템 도우미: 긴 경로(\\\\?\\) 접두, 파일 시스템 종류, 여유 공간. 모두 읽기 전용이다."""
from __future__ import annotations

import ntpath
import re
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x400  # 정션/심볼릭 링크/마운트 지점 등(OneDrive 자리표시 파일도 이 속성을 가진다)
FAT4G = 4 * 1024 ** 3 - 1  # FAT32 가 저장할 수 있는 파일 하나의 최대 크기


def long_path(path: str | os.PathLike, nt: bool | None = None) -> str:
    """Windows 의 260자 경로 제한을 넘는 파일도 열 수 있게 ``\\\\?\\`` 접두를 붙인다(UNC 는 ``\\\\?\\UNC\\``).

    Windows 가 아니면 그대로 돌려준다. nt 로 강제하면(테스트) Windows 규칙으로 계산한다.
    """
    s = os.fspath(path)
    use_nt = (os.name == "nt") if nt is None else nt
    if not use_nt:
        return s
    if s.startswith("\\\\?\\"):
        return s
    s = ntpath.abspath(s)
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def is_unc(path: str | os.PathLike) -> bool:
    """네트워크 공유 경로(\\\\서버\\공유\\...)인지."""
    s = os.fspath(path)
    return s.startswith("\\\\") and not s.startswith("\\\\?\\") or s.startswith("\\\\?\\UNC\\")


def volume_root(path: str | os.PathLike) -> str:
    """경로가 속한 드라이브/공유의 루트(예: ``C:\\``, ``\\\\서버\\공유\\``)."""
    s = ntpath.abspath(os.fspath(path))
    drive, _ = ntpath.splitdrive(s)
    return drive + "\\" if drive else s


def volume_info(path: str | os.PathLike) -> tuple[str, int] | None:
    """(파일 시스템 이름, 볼륨 일련번호). 알 수 없으면 None. 일련번호는 드라이브 문자가 바뀌어도 같은 드라이브를 알아보는 데 쓴다."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(64)
        serial = ctypes.c_uint32(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(volume_root(path)), None, 0, ctypes.byref(serial),
                                                          None, None, buf, 64)
        return (buf.value.upper(), int(serial.value)) if ok else None
    except (AttributeError, OSError):
        return None


def filesystem_type(path: str | os.PathLike) -> str | None:
    """대상이 놓인 볼륨의 파일 시스템 이름(NTFS, FAT32, EXFAT, REFS ...). 알 수 없으면 None."""
    info = volume_info(path)
    return info[0] if info else None


DRIVE_TYPES = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}


def drive_kind(path: str | os.PathLike) -> str:
    """removable(이동식) / fixed(고정, USB 외장 SSD/HDD 도 여기로 보일 수 있음) / network / cdrom / ramdisk / unknown."""
    if is_unc(path):
        return "network"
    if os.name != "nt":
        return "unknown"
    try:
        import ctypes
        return DRIVE_TYPES.get(int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(volume_root(path)))), "unknown")
    except (AttributeError, OSError):
        return "unknown"


@dataclass(frozen=True)
class DestInfo:
    """복사 대상 경로에 대한 사전 정보(읽기 전용 조회). 테스트에서는 이 값을 만들어 주입한다."""
    path: str
    kind: str = "unknown"
    fs: str | None = None
    free: int | None = None
    serial: int | None = None
    letter: str = ""

    @property
    def is_network(self) -> bool:
        return self.kind == "network"


def describe_destination(path: str | os.PathLike) -> DestInfo:
    """대상 드라이브 종류, 파일 시스템, 여유 공간, 볼륨 일련번호, 드라이브 문자를 한 번에 조회한다."""
    vi = volume_info(path)
    drive = ntpath.splitdrive(ntpath.abspath(os.fspath(path)))[0]
    return DestInfo(os.fspath(path), drive_kind(path), vi[0] if vi else None, free_space(path), vi[1] if vi else None,
                    "" if drive.startswith("\\\\") else drive)


def free_space(path: str | os.PathLike) -> int | None:
    """경로(없으면 가장 가까운 상위 폴더)가 있는 볼륨의 여유 바이트. 알 수 없으면 None."""
    p = Path(path)
    try:
        while not p.exists() and p.parent != p:
            p = p.parent
        return shutil.disk_usage(p).free
    except OSError:
        return None


def is_reparse(path: str | os.PathLike) -> bool:
    """경로(링크 자체, 따라가지 않음)가 심볼릭 링크 또는 정션 등 reparse point 인지. 없거나 읽을 수 없으면 False.

    폴더에 쓰는 기준이다. 파일은 OneDrive 클라우드 전용 자리표시 파일도 reparse 속성을 가지므로 파일에는 쓰지 않는다(is_symlink 만 본다).
    """
    try:
        st = os.lstat(long_path(path))
    except OSError:
        return False
    import stat as _stat
    return _stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def reparse_in_chain(base: str | os.PathLike, target: str | os.PathLike) -> str | None:
    """base 아래에서 target 까지(target 포함) 지나는 폴더/파일 중 링크(reparse point)인 첫 경로. 없으면 None.

    base 자체는 검사하지 않는다(사용자가 정한 루트). 이미 있는 구성 요소만 검사한다(없는 것은 링크일 수 없다).
    """
    base_s, target_s = os.path.abspath(os.fspath(base)), os.path.abspath(os.fspath(target))
    try:
        rel = os.path.relpath(target_s, base_s)
    except ValueError:
        return None
    if rel == "." or rel.startswith(".."):
        return None
    cur = base_s
    for part in rel.split(os.sep):
        cur = os.path.join(cur, part)
        lcur = long_path(cur)  # 260자 넘는 경로도 판정한다(접두 없는 조회는 '없음'으로 보아 링크를 놓친다)
        if not os.path.lexists(lcur):
            return None
        if os.path.islink(lcur) or (os.path.isdir(lcur) and is_reparse(cur)):  # 파일은 심볼릭 링크만(클라우드 자리표시 파일 제외)
            return cur
    return None


@dataclass(frozen=True)
class SpaceCheck:
    """대상 드라이브에 쓰기 전 사전 점검 결과(transfer 와 export 가 같이 쓴다)."""
    fat32_blocked: bool   # FAT32 인데 4GB 를 넘는 파일(또는 패키지)을 써야 함
    space_blocked: bool   # 여유 공간이 필요량보다 적음
    free_unknown: bool    # 여유 공간을 조회하지 못함(사전 검사 건너뜀)
    exfat: bool           # exFAT: 큰 파일은 되지만 전원이 꺼지면 손상되기 쉬움
    free: int | None
    required: int


def check_space(dest: DestInfo, required: int, biggest: int) -> SpaceCheck:
    """dest 에 required 바이트를 쓸 수 있는지, 가장 큰 파일(biggest)이 FAT32 의 4GB 한계를 넘는지 본다(읽기 전용)."""
    return SpaceCheck(fat32_blocked=biggest > FAT4G and dest.fs == "FAT32",
                      space_blocked=dest.free is not None and dest.free < required,
                      free_unknown=dest.free is None, exfat=dest.fs == "EXFAT", free=dest.free, required=required)


_LONG_PREFIX = re.compile(r"\\\\\?\\(UNC\\)?")


def plain_path_text(text: str) -> str:
    """문구 안의 긴 경로 접두(역슬래시 2개 + ? + 역슬래시, UNC 는 UNC 포함)를 지워 사용자가 입력한 모양의 경로로 보여 준다(표시 전용)."""
    return _LONG_PREFIX.sub(lambda m: "\\\\" if m.group(1) else "", text)
