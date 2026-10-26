"""모드 B 옵션 '4GB 분할 파일'(--split-large): FAT32 가 저장하지 못하는 4GiB 넘는 파일을 조각으로 나눠 옮기고, 새 PC 에서 합친다.

- 조각 이름: ``<원래 이름>.pcm001``, ``.pcm002`` ... (같은 폴더, 기본 조각 크기 4000MiB). 번호는 1부터, 999 를 넘으면 자릿수가 늘어난다.
- 원본은 읽기만 한다. 조각은 ``<조각>.pcmigrate-part`` 임시 이름에 쓰고 fsync 한 뒤에만 조각 이름으로 바꾼다
  (쓰다 끊기면 임시 파일만 남으므로 완성 조각 이름에는 반쪽 파일이 없다 -> 이어받기는 조각 단위, 쓰던 조각은 처음부터).
- 같은 이름의 다른 조각이 이미 있으면 덮어쓰기 전에 .bak 으로 보존한다. 이번 실행의 조각 수보다 번호가 큰 옛 조각도 .bak 으로 옮긴다
  (남아 있으면 합칠 때 섞이기 때문).
- 조각별 SHA-256 과 원본 전체 SHA-256 을 같이 구한다(원본을 한 번 읽으며 둘 다 계산). 해시 목록(manifest)에는 ``parts`` 로 기록된다.
- join: 조각을 순서대로 이어 한 파일로 합치고 조각 해시/전체 해시를 대조한다. 조각은 지우지 않는다.

테스트에서는 큰 파일을 만들지 않도록 모듈 상수 SPLIT_THRESHOLD/PART_SIZE 또는 환경변수
PCMIGRATE_SPLIT_THRESHOLD/PCMIGRATE_PART_SIZE(바이트)로 기준을 낮춘다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import failure, fs
from .cancel import Cancelled
from .copier import (COPIED, FAILED, MISMATCH, SKIPPED_SAME, VERIFIED, WOULD_COPY, WOULD_OVERWRITE, FileResult,
                     IoStats, Report, _install, _refuse_dir, _rm, _same_content, sha256_file)
from .fs import long_path
from .progress import fmt_bytes

SPLIT_THRESHOLD = fs.FAT4G  # 이보다 큰 파일(4GiB-1 바이트 초과)을 나눈다(FAT32 한도)
PART_SIZE = 4000 * 1024 * 1024  # 조각 하나의 크기(FAT32 한도보다 작게)
ENV_THRESHOLD = "PCMIGRATE_SPLIT_THRESHOLD"
ENV_PART_SIZE = "PCMIGRATE_PART_SIZE"
PART_TMP = ".pcmigrate-part"
PART_RE = re.compile(r"^(?P<base>.+)\.pcm(?P<n>\d{3,})$", re.I)
STATE_SEP = "\0pcm"  # 상태 파일에서 조각 기록의 키: <상대 경로>\0pcm001 (파일 이름에는 NUL 이 올 수 없어 겹치지 않는다)
MTIME_TOLERANCE_NS = 2_000_000_000
DEFAULT_CHUNK = 4 * 1024 * 1024
JOIN_SPACE_MARGIN = 0.01


class SplitError(ValueError):
    """분할/합치기를 시작할 수 없는 입력/환경 오류. 메시지는 사용자에게 그대로 보여 줄 수 있다."""


def _env_int(name: str) -> int | None:
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return None
    return v if v > 0 else None


def split_threshold() -> int:
    return _env_int(ENV_THRESHOLD) or SPLIT_THRESHOLD


def part_size() -> int:
    return min(_env_int(ENV_PART_SIZE) or PART_SIZE, fs.FAT4G)


def needs_split(size: int) -> bool:
    return size > split_threshold()


def part_sizes(size: int, psz: int) -> list[int]:
    """원본 크기를 조각 크기들로 나눈다(마지막만 작을 수 있음). 크기 0 은 조각 없음."""
    full, rest = divmod(size, psz)
    return [psz] * full + ([rest] if rest else [])


def part_suffix(idx: int) -> str:
    return f".pcm{idx:03d}"


def state_key(rel: str, idx: int) -> str:
    return f"{rel}{STATE_SEP}{idx:03d}"


def bak_name(target: str) -> str:
    cand, n = target + ".bak", 1
    while os.path.lexists(long_path(cand)):
        cand = f"{target}.bak.{n}"
        n += 1
    return cand


def _sha() -> "hashlib._Hash":
    return hashlib.sha256()


# --------------------------------------------------------------------------- 원본 -> 조각 복사

@dataclass
class SplitCopy:
    ok: bool = False
    sha256: str = ""  # 원본 전체 해시
    parts: list[dict] = field(default_factory=list)  # {name,size,sha256} (조각 번호 순서)
    written: int = 0  # 이번에 새로 쓴 조각 수
    kept: int = 0  # 이미 있어 건너뛴 조각 수
    backups: list[tuple[str, str]] = field(default_factory=list)  # (조각 경로, .bak 경로)
    error: str = ""
    reason: str = ""
    cancelled_tmp: str = ""


# probe(idx, 조각 크기, 조각 경로) -> (이미 있는 조각의 해시(알면), 해시를 몰라도 같은 것으로 보고 건너뛸지)
Probe = Callable[[int, int, str], "tuple[str | None, bool]"]


def copy_split(src: str, dst: str, size: int, mtime_ns: int, *, psz: int | None = None, chunk: int | None = None,
               cancel: Callable[[], bool] | None = None, on_bytes: Callable[[int], None] | None = None,
               stats: IoStats | None = None, probe: Probe | None = None,
               on_part: Callable[[int, int, str, bool], None] | None = None) -> SplitCopy:
    """원본(읽기만) 한 파일을 dst(전체 이름)의 조각 dst.pcm001... 로 나눠 쓴다.

    원본은 처음부터 끝까지 한 번만 읽는다(조각 해시와 전체 해시를 함께 계산). probe 가 이미 있는 조각을 알려 주면
    그 조각 구간은 원본을 읽어 해시만 구하고(쓰지 않고) 같으면 건너뛴다. on_part(번호, 크기, 해시, 새로 썼는지)는 조각이 끝날 때마다 불린다.
    쓴 조각은 다시 읽어 검사하지 않는다(호출자가 이어서 하는 검사 단계에서 대상 조각을 읽어 비교한다).
    """
    psz = psz or part_size()
    chunk = chunk or DEFAULT_CHUNK
    out = SplitCopy()
    sizes = part_sizes(size, psz)
    whole = _sha()
    view = memoryview(bytearray(max(16, min(chunk, max(size, 1)))))
    tmp = ""
    where = src

    def count(n: int) -> None:
        if stats is not None:
            stats.add("src", n)
        if on_bytes is not None:
            on_bytes(n)

    def pump(fin, length: int, hashes, fout=None) -> int:
        nonlocal where
        left = length
        while left > 0:
            if cancel is not None and cancel():
                raise Cancelled()
            where = src
            n = failure.read_chunk(fin.readinto, view[:min(len(view), left)])
            if not n:
                break
            for h in hashes:
                h.update(view[:n])
            if fout is not None:
                where = tmp
                fout.write(view[:n])
            count(n)
            left -= n
        return length - left

    try:
        with open(long_path(src), "rb", buffering=0) as fin:
            pos = 0
            for idx, plen in enumerate(sizes, 1):
                target = dst + part_suffix(idx)
                tmp = target + PART_TMP
                pname = os.path.basename(target)
                known, trust = probe(idx, plen, target) if probe else (None, False)
                ph = _sha()
                if known or trust:
                    saved = whole.copy()
                    got = pump(fin, plen, (ph, whole))
                    if got != plen:
                        raise _SourceChanged(size)
                    if (trust and not known) or ph.hexdigest() == known:
                        digest = ph.hexdigest()
                        out.parts.append({"name": pname, "size": plen, "sha256": digest})
                        out.kept += 1
                        if on_part:
                            on_part(idx, plen, digest, False)
                        pos += plen
                        continue
                    whole, ph = saved, _sha()  # 이미 있는 조각이 원본과 다름: 되감아 다시 쓴다
                    fin.seek(pos)
                where = tmp
                with open(long_path(tmp), "wb", buffering=0) as fout:
                    got = pump(fin, plen, (ph, whole), fout)
                    if got != plen:
                        raise _SourceChanged(size)
                    fout.flush()
                    os.fsync(fout.fileno())
                try:
                    os.utime(long_path(tmp), ns=(mtime_ns, mtime_ns))
                except OSError:
                    pass  # 수정 시각은 내용 정확성과 무관(검사는 해시로 한다)
                where = target
                if os.path.lexists(long_path(target)):
                    if os.path.isdir(long_path(target)):
                        raise _DestIsDir(target)
                    bak = bak_name(target)
                    os.replace(long_path(target), long_path(bak))
                    out.backups.append((target, bak))
                    try:
                        os.replace(long_path(tmp), long_path(target))
                    except BaseException:
                        try:
                            os.replace(long_path(bak), long_path(target))  # 되돌린다: 조각 이름이 빈 채로 두지 않는다
                            out.backups.pop()
                        except OSError:
                            pass
                        raise
                else:
                    os.replace(long_path(tmp), long_path(target))
                tmp = ""
                digest = ph.hexdigest()
                out.parts.append({"name": pname, "size": plen, "sha256": digest})
                out.written += 1
                if on_part:
                    on_part(idx, plen, digest, True)
                pos += plen
            if failure.read_chunk(fin.read, 1):  # 복사하는 동안 원본이 커졌다
                raise _SourceChanged(size)
        if stats is not None:
            stats.file_read("src")
        out.sha256 = whole.hexdigest()
        _retire_stale_parts(dst, len(sizes), out)
        out.ok = True
    except Cancelled:
        _cleanup_tmp(tmp)
        raise
    except _SourceChanged as e:
        _cleanup_tmp(tmp)
        out.error = f"원본 파일이 복사하는 동안 바뀐 것 같습니다(처음 크기 {e.size:,}바이트와 읽은 양이 다름). 원본을 쓰는 프로그램을 닫고 다시 실행하세요"
        out.reason = "source_changed"
    except _DestIsDir as e:
        _cleanup_tmp(tmp)
        f = failure.REASONS[failure.DEST_IS_DIR]
        out.error, out.reason = f"{f.text} ({e.path})", f.code
    except OSError as e:
        _cleanup_tmp(tmp)
        f = failure.classify(e, where)
        out.error, out.reason = f"{f.text} ({where})", f.code
    except BaseException:
        _cleanup_tmp(tmp)
        raise
    return out


class _SourceChanged(Exception):
    def __init__(self, size: int) -> None:
        super().__init__(size)
        self.size = size


class _DestIsDir(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def _cleanup_tmp(tmp: str) -> None:
    if tmp:
        _rm(Path(tmp))


def existing_parts(dst: str) -> dict[int, str]:
    """dst(전체 이름) 옆에 있는 조각 파일 {번호: 경로}. 폴더가 없거나 못 읽으면 빈 dict."""
    folder, name = os.path.split(dst)
    out: dict[int, str] = {}
    try:
        with os.scandir(long_path(folder or ".")) as it:
            for e in it:
                m = PART_RE.match(e.name)
                if m and m.group("base").casefold() == name.casefold() and e.is_file(follow_symlinks=False):
                    out[int(m.group("n"))] = os.path.join(folder, e.name)
    except OSError:
        pass
    return out


def _retire_stale_parts(dst: str, count: int, out: SplitCopy) -> None:
    """이번 조각 수보다 번호가 큰 옛 조각은 합칠 때 섞이므로 .bak 으로 옮긴다(지우지 않음)."""
    for idx, path in sorted(existing_parts(dst).items()):
        if idx > count:
            bak = bak_name(path)
            try:
                os.replace(long_path(path), long_path(bak))
                out.backups.append((path, bak))
            except OSError:
                pass


def parts_bytes_present(dst: str, size: int, psz: int) -> int:
    """이미 대상에 있는 '크기가 맞는' 조각의 바이트 합(공간 점검에서 이어받을 분량을 빼기 위한 가벼운 추정, stat 만 한다)."""
    have = existing_parts(dst)
    total = 0
    for idx, plen in enumerate(part_sizes(size, psz), 1):
        p = have.get(idx)
        try:
            if p and os.stat(long_path(p)).st_size == plen:
                total += plen
        except OSError:
            pass
    return total


def part_paths(dst: str, size: int, psz: int) -> list[tuple[str, int]]:
    return [(dst + part_suffix(i), n) for i, n in enumerate(part_sizes(size, psz), 1)]


# --------------------------------------------------------------------------- 조각을 이어 읽기

def hash_parts(paths: list[str], *, chunk: int | None = None, cancel: Callable[[], bool] | None = None,
               on_bytes: Callable[[int], None] | None = None,
               sink: Callable[[memoryview], None] | None = None) -> tuple[str, list[str]]:
    """조각을 순서대로 이어 읽어 (전체 SHA-256, 조각별 SHA-256 목록)을 구한다(읽기 전용). sink 가 있으면 읽은 바이트를 넘겨 준다(합치기용)."""
    chunk = chunk or DEFAULT_CHUNK
    view = memoryview(bytearray(chunk))
    whole = _sha()
    per: list[str] = []
    for p in paths:
        ph = _sha()
        with open(long_path(p), "rb", buffering=0) as f:
            while True:
                if cancel is not None and cancel():
                    raise Cancelled()
                n = failure.read_chunk(f.readinto, view)
                if not n:
                    break
                ph.update(view[:n])
                whole.update(view[:n])
                if sink is not None:
                    sink(view[:n])
                if on_bytes is not None:
                    on_bytes(n)
        per.append(ph.hexdigest())
    return whole.hexdigest(), per


def hash_ranges(path: str, sizes: list[int], *, chunk: int | None = None, cancel: Callable[[], bool] | None = None,
                on_bytes: Callable[[int], None] | None = None) -> tuple[str, list[str]]:
    """원본 한 파일을 sizes 길이대로 잘라 (전체 SHA-256, 조각별 SHA-256 목록)을 구한다(읽기 전용, 원본을 한 번만 읽는다).
    파일이 sizes 의 합보다 짧거나 길면 SourceChangedError."""
    chunk = chunk or DEFAULT_CHUNK
    view = memoryview(bytearray(chunk))
    whole = _sha()
    per: list[str] = []
    with open(long_path(path), "rb", buffering=0) as f:
        for plen in sizes:
            ph = _sha()
            left = plen
            while left > 0:
                if cancel is not None and cancel():
                    raise Cancelled()
                n = failure.read_chunk(f.readinto, view[:min(len(view), left)])
                if not n:
                    raise SourceChangedError(path)
                ph.update(view[:n])
                whole.update(view[:n])
                if on_bytes is not None:
                    on_bytes(n)
                left -= n
            per.append(ph.hexdigest())
        if failure.read_chunk(f.read, 1):
            raise SourceChangedError(path)
    return whole.hexdigest(), per


class SourceChangedError(OSError):
    """조사한 뒤 원본 크기가 바뀌었다."""


# --------------------------------------------------------------------------- join(합치기)

@dataclass
class PartGroup:
    folder: str  # 조각이 있는 폴더(절대 경로)
    base: str  # 원래 파일 이름
    rel: str  # 입력 폴더 기준 상대 경로(posix, 원래 파일 이름 포함)
    parts: dict[int, str] = field(default_factory=dict)  # 번호 -> 조각 경로

    @property
    def count(self) -> int:
        return len(self.parts)

    def missing(self) -> list[int]:
        return [i for i in range(1, (max(self.parts) if self.parts else 0) + 1) if i not in self.parts]


def find_groups(path: str) -> tuple[str, list[PartGroup]]:
    """입력(폴더 또는 첫 조각 파일)에서 조각 묶음을 찾는다. (입력 폴더, 묶음 목록). 링크는 따라가지 않는다."""
    p = os.path.abspath(path)
    if os.path.isfile(long_path(p)):
        m = PART_RE.match(os.path.basename(p))
        if not m:
            raise SplitError(f"조각 파일(이름이 .pcm001 로 끝나는 파일)이 아닙니다: {path}")
        folder = os.path.dirname(p)
        base = m.group("base")
        g = PartGroup(folder, base, base)
        g.parts = existing_parts(os.path.join(folder, base))
        return folder, [g]
    if not os.path.isdir(long_path(p)):
        raise SplitError(f"폴더나 조각 파일이 없습니다: {path}")
    groups: dict[tuple[str, str], PartGroup] = {}
    stack = [(p, "")]
    while stack:
        cur, relp = stack.pop()
        try:
            with os.scandir(long_path(cur)) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append((os.path.join(cur, e.name), f"{relp}/{e.name}" if relp else e.name))
                    continue
            except OSError:
                continue
            m = PART_RE.match(e.name)
            if not m:
                continue
            key = (cur, m.group("base").casefold())
            g = groups.get(key)
            if g is None:
                base = m.group("base")
                g = groups[key] = PartGroup(cur, base, f"{relp}/{base}" if relp else base)
            g.parts[int(m.group("n"))] = os.path.join(cur, e.name)
    return p, sorted(groups.values(), key=lambda g: g.rel.lower())


@dataclass
class Expect:
    """조각 묶음에 대해 알고 있는 기대값(해시 목록 또는 상태 파일에서)."""
    source: str = ""  # manifest / state / ""
    whole: str = ""
    parts: dict[str, dict] = field(default_factory=dict)  # 조각 이름(소문자) -> {size, sha256}
    count: int = 0
    mtime_ns: int | None = None


def load_expectations(root: str, manifest: str | None) -> tuple[dict[str, Expect], list[str], str, str]:
    """(상대 경로 -> 기대값, 안내 문장들, 사용한 출처, 상대 경로의 기준 폴더). 해시 목록(manifest)이 우선이고 없으면 상태 파일의 조각 기록을 쓴다.

    --manifest 를 주지 않으면 입력 폴더에서 위쪽 폴더(최대 8단계)로 올라가며 .pcmigrate-manifest.jsonl, .pcmigrate-state.json 을 찾는다
    (전송 대상 폴더의 하위 폴더나 첫 조각 파일을 지정해도 찾을 수 있게)."""
    from .manifest import DEFAULT_NAME, ManifestError, ManifestReader
    notes: list[str] = []
    if manifest:
        if not os.path.isfile(long_path(manifest)):
            raise SplitError(f"해시 목록 파일이 없습니다: {manifest}")
        try:
            return _rows_with_parts(ManifestReader(Path(manifest)), notes), notes, "manifest", root
        except ManifestError as e:
            raise SplitError(f"해시 목록 오류: {e}") from e
    cur = root
    for _ in range(8):
        cand = os.path.join(cur, DEFAULT_NAME)
        if os.path.isfile(long_path(cand)):
            try:
                found = _rows_with_parts(ManifestReader(Path(cand)), notes)
                if found:
                    return found, notes, "manifest", cur
            except ManifestError as e:
                notes.append(f"폴더 안의 해시 목록을 읽지 못해 쓰지 않습니다({e}).")
        st = _read_state_parts(os.path.join(cur, ".pcmigrate-state.json"))
        if st:
            return st, notes, "state", cur
        up = os.path.dirname(cur)
        if up == cur:
            break
        cur = up
    return {}, notes, "", root


def _rows_with_parts(reader, notes: list[str]) -> dict[str, Expect]:
    found: dict[str, Expect] = {}
    for row in reader.rows():
        if row.get("parts"):
            found[row["rel"]] = Expect("manifest", row["sha256"], {x["name"].casefold(): x for x in row["parts"]}, len(row["parts"]),
                                       row.get("mtime_ns"))
    if reader.footer is None:
        notes.append("해시 목록이 끝까지 만들어지지 않았습니다. 목록에 있는 조각만 대조합니다.")
    return found


def _read_state_parts(path: str) -> dict[str, Expect]:
    """전송 상태 파일(.pcmigrate-state.json)의 조각 기록을 읽는다. 없거나 깨졌으면 빈 dict."""
    out: dict[str, Expect] = {}
    wholes: dict[str, str] = {}
    try:
        with open(long_path(path), "r", encoding="utf-8") as f:
            f.readline()
            for line in f:
                try:
                    e = json.loads(line)
                    rel, h = e["rel"], str(e["h"])
                    if STATE_SEP in rel:
                        base, num = rel.split(STATE_SEP, 1)
                        ex = out.setdefault(base, Expect("state"))
                        ex.parts[f"{os.path.basename(base)}{part_suffix(int(num))}".casefold()] = {"size": int(e["s"]), "sha256": h}
                        ex.count = max(ex.count, int(num))
                        ex.mtime_ns = int(e["m"])
                    else:
                        wholes[rel] = h
                except (ValueError, KeyError, TypeError):
                    continue
    except (OSError, ValueError):
        return {}
    for base, ex in out.items():
        if base in wholes and ex.count == len(ex.parts):
            ex.whole = wholes[base]
    return {k: v for k, v in out.items() if v.parts}


def _expect_for(g: PartGroup, exp_root: str, exp: dict[str, Expect]) -> Expect | None:
    """묶음의 기대값: 기준 폴더에 대한 상대 경로가 같은 항목, 없으면 이름이 같은 항목이 하나뿐일 때만(조각 크기를 따로 검사하므로 안전)."""
    try:
        rel = os.path.relpath(os.path.join(g.folder, g.base), exp_root).replace(os.sep, "/")
    except ValueError:
        rel = g.rel
    if rel in exp:
        return exp[rel]
    hits = [v for k, v in exp.items() if k == g.base or k.endswith("/" + g.base)]
    return hits[0] if len(hits) == 1 else None


def join_all(src: str, output: str, *, manifest: str | None = None, dry_run: bool = False, chunk: int | None = None,
             cancel: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
             dest_info_fn: Callable[[str], fs.DestInfo] | None = None, clock: Callable[[], float] = time.monotonic) -> Report:
    """조각 묶음을 output 아래에 원래 파일로 합친다(입력 폴더의 하위 구조 유지). 조각은 읽기만 한다.

    - 합치기 전에 모든 묶음의 조각 번호가 빠짐없는지/크기가 목록과 같은지 보고, 합칠 폴더의 여유 공간(과 FAT32 한도)을 점검한다.
    - 합치면서 조각 해시와 전체 해시를 계산해 목록(또는 상태 파일)과 대조한 뒤에만 원래 이름으로 바꾼다. 같은 이름의 다른 파일은 .bak 으로 보존한다.
    """
    t0 = clock()
    root, groups = find_groups(src)
    if not groups:
        raise SplitError(f"조각(.pcm001 ...)을 찾지 못했습니다: {src}")
    output = os.path.abspath(output)
    if os.path.isfile(long_path(output)):
        raise SplitError(f"합칠 폴더 자리에 파일이 있습니다: {output}")
    exp, notes, source, exp_root = load_expectations(root, manifest)
    report = Report(dry_run=dry_run)
    report.meta.update(mode="join", source_root=root, output=output, hash_source=source or "none", notes=list(notes),
                       groups=len(groups))
    say = progress or (lambda m: None)

    plans: list[tuple[PartGroup, Expect | None, int, list[str]]] = []
    for g in groups:
        e = _expect_for(g, exp_root, exp)
        problems: list[str] = []
        missing = g.missing()
        if missing:
            problems.append("빠진 조각 번호: " + ", ".join(f"{i:03d}" for i in missing[:10]))
        if e is not None and e.count and e.count != g.count:
            problems.append(f"조각 개수가 목록과 다릅니다(목록 {e.count}개, 폴더 {g.count}개)")
        total = 0
        for idx in sorted(g.parts):
            try:
                n = os.stat(long_path(g.parts[idx])).st_size
            except OSError as ex:
                problems.append(f"조각 {idx:03d} 을 읽을 수 없습니다: {failure.classify(ex, g.parts[idx]).label}")
                continue
            total += n
            want = e.parts.get(os.path.basename(g.parts[idx]).casefold()) if e else None
            if want and want.get("size") != n:
                problems.append(f"조각 {idx:03d} 의 크기가 목록과 다릅니다(목록 {want.get('size')}바이트, 폴더 {n}바이트)")
            elif e and e.parts and want is None:
                problems.append(f"조각 {idx:03d} 이 목록에 없습니다")
        plans.append((g, e, total, problems))

    need = sum(t for _g, _e, t, p in plans if not p)
    dest = (dest_info_fn or fs.describe_destination)(output)
    report.meta.update(join_bytes=need)
    biggest = max([t for _g, _e, t, p in plans if not p] or [0])
    if dest.fs == "FAT32" and biggest > fs.FAT4G:
        raise SplitError(f"합칠 드라이브가 FAT32 라서 4GB 보다 큰 파일({fmt_bytes(biggest)})을 저장할 수 없습니다. NTFS 드라이브에 합치세요.")
    required = need + int(need * JOIN_SPACE_MARGIN)
    if dest.free is not None and dest.free < required:
        raise SplitError(f"합칠 드라이브의 여유 공간이 부족합니다. 필요 약 {fmt_bytes(required)}, 현재 여유 {fmt_bytes(dest.free)}. "
                         "여유가 더 있는 드라이브를 --output 으로 지정하세요(조각은 합친 뒤에도 지우지 않으므로 그 공간은 따로 필요합니다).")
    if dest.free is None:
        report.meta["notes"].append("합칠 위치의 여유 공간을 조회하지 못해 공간 사전 검사를 건너뜁니다.")

    done = 0
    for g, e, total, problems in plans:
        out_path = os.path.join(output, *g.rel.split("/"))
        res = FileResult(f"[조각 {g.count}개] {g.rel}", out_path, FAILED, total)
        if problems:
            res.reason = "bad_parts"
            res.error = "합치지 않았습니다: " + "; ".join(problems)
            report.results.append(res)
            continue
        if dry_run:
            res.status = WOULD_OVERWRITE if os.path.lexists(long_path(out_path)) else WOULD_COPY
            if res.status == WOULD_OVERWRITE:
                res.backup = bak_name(out_path)
            report.results.append(res)
            continue
        say(f"합치는 중입니다({done + 1}/{len(plans)}): {g.rel} ({g.count}개 조각, {fmt_bytes(total)})")
        join_one(g, e, out_path, res, chunk=chunk, cancel=cancel)
        report.results.append(res)
        done += 1
    ok = [r for r in report.results if r.status in (VERIFIED, COPIED, SKIPPED_SAME)]
    report.meta.update(joined_files=len(ok), joined_parts=sum(g.count for g, _e, _t, p in plans if not p),
                       unverified_files=sum(1 for r in ok if r.status == COPIED), seconds=round(clock() - t0, 2),
                       verification_complete=not dry_run and not report.failures and not any(r.status == COPIED for r in ok))
    return report


def join_one(g: PartGroup, e: Expect | None, out_path: str, res: FileResult, *, chunk: int | None = None,
             cancel: Callable[[], bool] | None = None) -> FileResult:
    """묶음 하나를 임시 이름에 합쳐 해시를 대조하고 다시 읽어 확인한 뒤 원래 이름으로 바꾼다."""
    tmp = out_path + PART_TMP
    paths = [g.parts[i] for i in sorted(g.parts)]
    where = out_path
    try:
        os.makedirs(long_path(os.path.dirname(out_path)), exist_ok=True)
        if _refuse_dir(Path(out_path), res, long_path):
            return res
        where = tmp
        with open(long_path(tmp), "wb", buffering=0) as fout:
            where = paths[0]
            whole, per = hash_parts(paths, chunk=chunk, cancel=cancel, sink=lambda b: fout.write(b))
            where = tmp
            fout.flush()
            os.fsync(fout.fileno())
        res.sha256 = whole
        if os.stat(long_path(tmp)).st_size != res.size:
            raise OSError("합친 파일의 크기가 조각 크기의 합과 다릅니다")
        checked = False
        if e is not None:
            for i, (p, h) in enumerate(zip(paths, per), 1):
                want = e.parts.get(os.path.basename(p).casefold())
                if want and want.get("sha256") != h:
                    res.status, res.reason = MISMATCH, failure.MISMATCH
                    res.error = f"조각 {i:03d}({os.path.basename(p)})의 내용이 원본과 다릅니다(SHA-256 불일치). 그 조각을 다시 복사하세요. 합치지 않았습니다"
                    _rm(Path(tmp))
                    return res
                checked = checked or bool(want)
            if e.whole:
                if e.whole != whole:
                    res.status, res.reason = MISMATCH, failure.MISMATCH
                    res.error = "합친 파일의 전체 SHA-256 이 원본과 다릅니다. 합치지 않았습니다"
                    _rm(Path(tmp))
                    return res
                checked = True
        where = tmp
        if sha256_file(Path(tmp), chunk, cancel) != whole:  # 쓴 파일을 다시 읽어 확인
            raise OSError("합친 뒤 내용 검사 실패")
        mt = e.mtime_ns if e and e.mtime_ns else None
        if mt is None:
            try:
                mt = os.stat(long_path(paths[0])).st_mtime_ns
            except OSError:
                mt = None
        if mt:
            try:
                os.utime(long_path(tmp), ns=(mt, mt))
            except OSError:
                pass
        where = out_path
        if _same_content(Path(out_path), res, chunk, cancel):
            _rm(Path(tmp))
            res.status = SKIPPED_SAME
            return res
        _install(Path(tmp), Path(out_path), res, long_path)
        res.status = VERIFIED if checked else COPIED
        if not checked:
            res.error = "해시 목록이 없어 내용 대조는 하지 못했습니다(조각을 이어 붙이고 다시 읽어 확인만 했습니다)"
    except Cancelled:
        _rm(Path(tmp))
        raise
    except OSError as ex:
        f = failure.classify(ex, where)
        res.status, res.reason, res.error = FAILED, f.code, f"{f.text} ({where})"
        _rm(Path(tmp))
    except BaseException:
        _rm(Path(tmp))
        raise
    return res
