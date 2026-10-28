"""압축 방식 선택과 zip 엔트리 쓰기.

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import hashlib
import os
import tempfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

from . import fs
from .cancel import checkpoint
from .package_base import CHUNK, SPOOL_MAX, SourceUnreadable, ZIP64_THRESHOLD


COMPRESS_MODES = ("auto", "always", "never")
# 이미 압축된 형식이라 deflate 를 다시 해도 거의 줄지 않고 시간만 든다(이 확장자는 auto 에서 STORED).
# 사진/영상/음악 코덱, 압축 파일, zip 기반 문서(OOXML/ODF), 내부 스트림이 압축된 PDF 가 근거다.
# 반대로 .rvt/.dwg/.3dm/.skp/.pln 등 CAD/BIM 형식은 목록에 두지 않는다: 압축률이 형식/버전마다 달라 확신할 수 없으므로
# 큰 파일은 아래 표본 검사로 실제 압축 효과를 보고 정한다.
ALREADY_COMPRESSED_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif",
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".webm",
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
    ".zip", ".7z", ".rar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab",
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".jar", ".apk", ".vsix", ".nupkg", ".whl",
})
SAMPLE_MIN_SIZE = 8 * 1024 * 1024   # 이보다 큰 '모르는 확장자'는 표본으로 압축 효과를 본다
SAMPLE_BYTES = 256 * 1024
SAMPLE_RATIO = 0.90                 # 표본이 10% 이상 줄지 않으면 STORED


def _looks_incompressible(spool, size: int) -> bool:
    """spool(이미 읽어 둔 내용)의 앞/가운데/끝 표본을 빠르게 압축해 보고 거의 줄지 않으면 True."""
    raw = packed = 0
    for pos in (0, max(0, size // 2 - SAMPLE_BYTES // 2), max(0, size - SAMPLE_BYTES)):
        spool.seek(pos)
        chunk = spool.read(SAMPLE_BYTES)
        if chunk:
            raw += len(chunk)
            packed += len(zlib.compress(chunk, 1))
    spool.seek(0)
    return raw > 0 and packed / raw >= SAMPLE_RATIO


def choose_method(arc: str, size: int, compress: str, spool=None) -> int:
    """zip 압축 방식(STORED/DEFLATED)을 정한다. never/always 는 그대로, auto 는 확장자와 표본으로 판단."""
    if compress == "never":
        return zipfile.ZIP_STORED
    if compress == "always":
        return zipfile.ZIP_DEFLATED
    if os.path.splitext(arc)[1].lower() in ALREADY_COMPRESSED_EXTS:
        return zipfile.ZIP_STORED
    if spool is not None and size >= SAMPLE_MIN_SIZE and _looks_incompressible(spool, size):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


METHOD_NAMES = {zipfile.ZIP_STORED: "stored", zipfile.ZIP_DEFLATED: "deflated"}


class _PartZip(zipfile.ZipFile):
    """내보내기 중인 .part zip. 압축 옵션과 방금 쓴 엔트리의 기록(meta)을 _write_entry 와 주고받는다."""
    compress = "auto"
    last_meta: dict | None = None


def _zi_dump(zi: zipfile.ZipInfo) -> dict:
    """저장점 기록용: 중앙 디렉터리를 나중에 다시 쓰는 데 필요한 ZipInfo 필드."""
    return {"fn": zi.filename, "dt": list(zi.date_time), "ct": zi.compress_type, "ex": zi.extra.hex(),
            "cm": zi.comment.hex(), "cs": zi.create_system, "cv": zi.create_version, "xv": zi.extract_version,
            "fl": zi.flag_bits, "ia": zi.internal_attr, "ea": zi.external_attr, "ho": zi.header_offset,
            "crc": zi.CRC, "csz": zi.compress_size, "fsz": zi.file_size}


def _zi_load(d: dict) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(d["fn"], tuple(d["dt"]))
    zi.compress_type, zi.extra, zi.comment = d["ct"], bytes.fromhex(d["ex"]), bytes.fromhex(d["cm"])
    zi.create_system, zi.create_version, zi.extract_version = d["cs"], d["cv"], d["xv"]
    zi.flag_bits, zi.internal_attr, zi.external_attr = d["fl"], d["ia"], d["ea"]
    zi.header_offset, zi.CRC, zi.compress_size, zi.file_size = d["ho"], d["crc"], d["csz"], d["fsz"]
    return zi


def _write_entry(zf: zipfile.ZipFile, arc: str, src: Path, stage_dir: str | None = None) -> tuple[int, str]:
    """원본을 읽기 전용으로 끝까지 읽어 (크기, sha256)을 구한 뒤에만 zip 엔트리를 만든다.

    다른 프로세스가 바이트 범위를 잠근 파일은 읽는 도중에 PermissionError 가 난다. zip 엔트리를 먼저 열면 반쯤 쓰인
    엔트리가 남아 패키지 검증이 깨지므로, 먼저 임시 저장소(작은 파일은 메모리, 큰 파일은 stage_dir 의 임시 파일)에
    읽어 두고 성공했을 때만 zip 에 쓴다. 읽기 실패는 SourceUnreadable 로, zip/임시 저장소 쓰기 실패는 OSError 로 전파한다.

    압축 방식은 zf.compress(auto/always/never)로 정한다. zf 가 _PartZip 이면 저장점 기록(zf.last_meta)도 남긴다.
    """
    h = hashlib.sha256()
    n = 0
    compress = getattr(zf, "compress", "auto")
    try:
        st = os.stat(fs.long_path(src))
        size = st.st_size
        mtime = _zip_time_st(st)
        fin = open(fs.long_path(src), "rb")
    except OSError as e:
        raise SourceUnreadable(src, e) from e
    with fin, tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX, dir=stage_dir) as spool:
        while True:
            checkpoint()  # 큰 파일을 읽는 중에도 청크마다 취소 확인(zip 에는 아직 아무것도 쓰지 않았다)
            try:
                chunk = fin.read(CHUNK)
            except OSError as e:
                raise SourceUnreadable(src, e, during_read=True) from e
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
            spool.write(chunk)
        spool.seek(0)
        info = zipfile.ZipInfo(arc, date_time=mtime)
        info.compress_type = choose_method(arc, n, compress, spool)
        with zf.open(info, "w", force_zip64=max(size, n) > ZIP64_THRESHOLD) as fout:
            while buf := spool.read(CHUNK):
                checkpoint()  # 압축해서 쓰는 중에도 청크마다 취소 확인(끊겨도 저장점은 직전 엔트리까지)
                fout.write(buf)
    if isinstance(zf, _PartZip):
        zf.last_meta = {"ssize": size, "mtime_ns": st.st_mtime_ns, "method": METHOD_NAMES[info.compress_type],
                        "off": info.header_offset, "end": zf.fp.tell(), "zi": _zi_dump(info)}
    return n, h.hexdigest()


def _zip_time_st(st: os.stat_result) -> tuple:
    t = datetime.fromtimestamp(st.st_mtime)
    return (max(t.year, 1980), t.month, t.day, t.hour, t.minute, t.second)


def _zip_time(path: Path) -> tuple:
    return _zip_time_st(os.stat(fs.long_path(path)))


# --------------------------------------------------------------------------- 저장점(이어받기)
