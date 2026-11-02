"""패키지 검증(verify_package)과 manifest 구조 검사.

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import hashlib
import json
import os
import zipfile
import zlib
from pathlib import Path

from .cancel import checkpoint
from .package_base import ALLOWED_COMPRESSION, CHUNK, FORMAT_VERSION, MANIFEST, MAX_ENTRIES, MAX_COMPRESSION_RATIO, MAX_MANIFEST_BYTES, MAX_TOTAL_BYTES, RATIO_CHECK_MIN_BYTES, RATIO_TOTAL_FLOOR_BYTES, PackageError, _safe_rel


def _hash_entry(zf: zipfile.ZipFile, name: str, limit: int | None = None) -> tuple[int, str]:
    """엔트리를 끝까지 읽어 (크기, sha256). limit 보다 많이 나오면 거기서 멈춘다(선언 크기보다 큰 엔트리/압축 폭탄 방어)."""
    h = hashlib.sha256()
    n = 0
    with zf.open(name) as f:
        while chunk := f.read(CHUNK):
            checkpoint()
            h.update(chunk)
            n += len(chunk)
            if limit is not None and n > limit:
                break
    return n, h.hexdigest()


def _manifest_shape_problems(manifest: object) -> list[str]:
    """manifest 구조(타입)를 검사한다. 위조/손상된 manifest 가 어떤 모양이어도 예외 없이 문제 목록만 만든다."""
    if not isinstance(manifest, dict):
        return ["manifest 오류: 최상위가 객체가 아님"]
    if manifest.get("format") != FORMAT_VERSION:
        return ["manifest 오류: 지원하지 않는 형식"]
    if not isinstance(manifest.get("files"), list):
        return ["manifest 오류: files 가 목록이 아님"]
    problems = []
    rules = manifest.get("rules", [])
    if not isinstance(rules, list) or not all(isinstance(r, dict) and isinstance(r.get("id"), str) for r in rules):
        problems.append("manifest 오류: rules 형식이 올바르지 않음")
    warns = manifest.get("manual_warnings", [])
    if not isinstance(warns, list) or not all(
            isinstance(w, dict) and isinstance(w.get("items", []), list)
            and all(isinstance(i, str) for i in w.get("items", [])) for w in warns):
        problems.append("manifest 오류: manual_warnings 형식이 올바르지 않음")
    return problems


PROGRAM_ENTRIES = ("programs/registry.json", "programs/winget.json", "programs/winget-list.txt")
MAX_PROGRAMS_BYTES = 64 * 1024 * 1024  # 프로그램 목록 한 파일의 상한(그보다 크면 거부)


def _programs_problems(zf: zipfile.ZipFile, names: list[str], manifest: dict) -> list[str]:
    """programs/* 의 무결성(M6). manifest 에 programs_sha256 이 있으면 모든 항목의 해시를 맞춰 보고, 없으면(옛 패키지) 기존처럼 읽되
    manifest['_programs_unverified']=True 로 표시한다(저장되는 값이 아니라 읽은 결과에만 붙는 표시)."""
    present = [n for n in PROGRAM_ENTRIES if n in names]
    for n in present:
        if zf.getinfo(n).file_size > MAX_PROGRAMS_BYTES:
            return [f"프로그램 목록이 너무 큽니다(> {MAX_PROGRAMS_BYTES // 1024 // 1024}MB): {n}"]
    declared = manifest.get("programs_sha256")
    if declared is None:
        if present:
            manifest["_programs_unverified"] = True
        return []
    if not isinstance(declared, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in declared.items()):
        return ["manifest 오류: programs_sha256 형식이 올바르지 않음"]
    problems = []
    for n in present:
        if n not in declared:
            problems.append(f"프로그램 목록 해시가 manifest 에 없음(바뀌었을 수 있음): {n}")
            continue
        try:
            size, digest = _hash_entry(zf, n, MAX_PROGRAMS_BYTES)
        except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, OSError):
            problems.append(f"프로그램 목록을 읽을 수 없음(손상): {n}")
            continue
        if digest != declared[n]:
            problems.append(f"프로그램 목록 해시 불일치(바뀌었거나 손상): {n}")
    for n in declared:
        if n not in names:
            problems.append(f"프로그램 목록이 패키지에 없음: {n}")
    return problems


def _file_entry_ok(f: object) -> bool:
    if not isinstance(f, dict):
        return False
    if not all(isinstance(f.get(k), str) for k in ("arc", "sha256", "rule", "base", "rel")):
        return False
    size = f.get("size")
    return isinstance(size, int) and not isinstance(size, bool) and size >= 0


def verify_package(path: Path) -> tuple[dict | None, list[str]]:
    """zip 구조/경로 안전성과 manifest 해시를 검증한다. (manifest, 문제 목록) 반환.

    zip 이나 manifest 가 어떤 모양으로 망가져 있어도 예외를 던지지 않고 문제 목록으로 돌려준다.
    """
    problems: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        return None, [f"zip 을 열 수 없음: {e}"]
    with zf:
        infos = zf.infolist()
        names = [i.filename for i in infos]
        if len(names) > MAX_ENTRIES:
            return None, [f"패키지에 엔트리가 너무 많습니다({len(names):,}개 > {MAX_ENTRIES:,}개). 손상되었거나 안전하지 않은 패키지입니다"]
        for n in names:  # 모든 엔트리가 패키지 루트 밖으로 나가지 않는지
            try:
                _safe_rel(n.rstrip("/") if n.endswith("/") else n)
            except PackageError:
                problems.append(f"경로 탈출/비정상 엔트리: {n!r}")
        if len(set(names)) != len(names):  # 같은 이름이 두 번 있으면 어느 쪽이 검사되고 어느 쪽이 풀리는지 알 수 없다
            problems.append("패키지에 같은 이름의 엔트리가 중복되어 있음")
        for i in infos:
            if i.compress_type not in ALLOWED_COMPRESSION:
                problems.append(f"지원하지 않는 압축 방식(안전을 위해 거부): {i.filename!r}")
        if problems:
            return None, problems
        if MANIFEST not in names:
            return None, [f"{MANIFEST} 없음"]
        if zf.getinfo(MANIFEST).file_size > MAX_MANIFEST_BYTES:
            return None, [f"manifest 가 너무 큽니다(> {MAX_MANIFEST_BYTES // 1024 // 1024}MB). 안전하지 않은 패키지입니다"]
        try:
            with zf.open(MANIFEST) as mf_:
                raw = mf_.read(MAX_MANIFEST_BYTES + 1)  # 헤더가 거짓이어도 상한까지만 읽는다
            if len(raw) > MAX_MANIFEST_BYTES:
                return None, ["manifest 가 너무 큽니다. 안전하지 않은 패키지입니다"]
            manifest = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError, zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError,
                OSError) as e:  # UnicodeDecodeError 는 ValueError, 깊게 중첩된 JSON 은 RecursionError
            return None, [f"manifest 오류: {type(e).__name__}"]
        shape = _manifest_shape_problems(manifest)
        if shape:
            return None, shape
        declared_total = sum(f["size"] for f in manifest["files"] if _file_entry_ok(f))
        if declared_total > MAX_TOTAL_BYTES:  # 선언 크기의 합이 비정상적으로 크면 해시를 계산하느라 시간/디스크를 쓰기 전에 거부한다(L5)
            return None, [f"패키지가 선언한 파일 크기의 합이 너무 큽니다({declared_total:,}바이트 > {MAX_TOTAL_BYTES:,}바이트). 손상되었거나 안전하지 않은 패키지입니다"]
        try:
            zip_size = os.path.getsize(path)
        except OSError:
            zip_size = 0
        if zip_size and declared_total > max(zip_size * MAX_COMPRESSION_RATIO, RATIO_TOTAL_FLOOR_BYTES):
            return None, [f"패키지의 선언 크기 합({declared_total:,}바이트)이 파일 실제 크기({zip_size:,}바이트)에 비해 비정상적으로 큽니다(압축비 {MAX_COMPRESSION_RATIO}배 초과). "
                          "압축 폭탄일 수 있어 거부합니다"]
        listed = set()
        for f in manifest["files"]:
            if not _file_entry_ok(f):
                problems.append(f"manifest 항목 오류: {str(f)[:80]}")
                continue
            try:
                _safe_rel(f["arc"])
                _safe_rel(f["rel"])
            except PackageError:
                problems.append(f"manifest 항목 오류: {str(f)[:80]}")
                continue
            arc, size, digest = f["arc"], f["size"], f["sha256"]
            if arc in listed:
                problems.append(f"manifest 에 같은 패키지 경로가 중복됨: {arc}")
                continue
            listed.add(arc)
            if arc not in names:
                problems.append(f"패키지에 없음: {arc}")
                continue
            zi = zf.getinfo(arc)
            if zi.file_size >= RATIO_CHECK_MIN_BYTES and zi.file_size > zi.compress_size * MAX_COMPRESSION_RATIO:
                problems.append(f"압축비가 비정상적으로 큰 엔트리(압축 폭탄 의심, {MAX_COMPRESSION_RATIO}배 초과): {arc}")
                continue
            if zf.getinfo(arc).file_size != size:  # 선언과 다른 크기는 읽기 전에 거른다(거대 엔트리를 해시하느라 시간/디스크를 쓰지 않게)
                problems.append(f"크기/해시 불일치: {arc}")
                continue
            try:
                n, h = _hash_entry(zf, arc, size)
            except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, OSError):
                problems.append(f"엔트리를 읽을 수 없음(손상): {arc}")
                continue
            if n != size or h != digest:
                problems.append(f"크기/해시 불일치: {arc}")
        problems += _programs_problems(zf, names, manifest)
        extra = set(names) - listed - {MANIFEST, *PROGRAM_ENTRIES}
        for e in sorted(extra):
            problems.append(f"manifest 에 없는 엔트리: {e}")
    return manifest, problems
