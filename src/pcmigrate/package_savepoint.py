"""이어받기 저장점(.part/.journal), 출력 잠금, 임시 파일 정리.

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, fs
from . import settings_filter
from .rules import Rule
from .package_base import CHUNK, FORMAT_VERSION, PackageError, STAGE_PREFIX
from .package_collect import Item
from .package_zip import _zi_load


PART_SUFFIX = ".part"
JOURNAL_SUFFIX = ".part.journal"
JOURNAL_VERSION = 1
SAVEPOINT_BYTES = 256 * 1024 * 1024
SAVEPOINT_SECONDS = 60.0
_GRACEFUL_SAVEPOINT = True  # 오류/취소로 끝날 때 마지막으로 완성된 엔트리까지 저장점을 한 번 더 찍는다(전원 차단 모의 시험은 끈다)


def part_paths(out: Path) -> tuple[Path, Path]:
    """(임시 zip, 진행 기록) 경로. 둘 다 이 도구가 만드는 임시물이다."""
    out = Path(out)
    return out.with_name(out.name + PART_SUFFIX), out.with_name(out.name + JOURNAL_SUFFIX)


def is_unfinished_part(path: Path | str) -> bool:
    return str(path).lower().endswith(PART_SUFFIX)


def _fingerprint(rules: list[Rule], extras: list | None, compress: str, include_caches: bool = False,
                 extra_mode: str = settings_filter.DEFAULT_EXTRA_MODE,
                 install_items: list | None = None) -> tuple[str, dict]:
    lim = settings_filter.default_limits()
    info = {"tool": __version__, "format": FORMAT_VERSION, "compress": compress, "include_caches": bool(include_caches),
            "extra_mode": extra_mode,
            "extra_limits": [lim.file_bytes, lim.folder_bytes, lim.folder_files] if extra_mode == settings_filter.EXTRA_MODE_CONFIG else None,
            "rules": [[r.id, str(r.version)] for r in rules],
            "extras": sorted([e.root, e.rel] + (["caches"] if getattr(e, "include_caches", False) else [])
                             for e in (extras or []))}
    if install_items:  # 설치 폴더 설정을 고른 경우에만 키를 넣는다(고르지 않은 내보내기의 지문/이어받기는 이전과 같다)
        info["install"] = sorted([i.root, i.rel] for i in install_items)
    raw = json.dumps(info, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), info


def _fp_reason(old: dict, new: dict) -> str:
    if old.get("rules") is not None and [r[0] for r in old["rules"]] != [r[0] for r in new["rules"]]:
        return "선택한 프로그램(규칙)이 달라졌습니다"
    if old.get("rules") != new["rules"]:
        return "프로그램 규칙의 버전이 달라졌습니다"
    if old.get("extras") != new["extras"]:
        return "직접 고른 추가 항목이 달라졌습니다"
    if old.get("extra_mode") != new["extra_mode"] or old.get("extra_limits") != new["extra_limits"]:
        return "목록 밖 항목의 담을 범위(허용 방식/전체) 설정이 달라졌습니다"
    if old.get("install") != new.get("install"):
        return "고른 설치 폴더 설정 파일이 달라졌습니다"
    if old.get("compress") != new["compress"]:
        return "압축 설정이 달라졌습니다"
    if bool(old.get("include_caches")) != bool(new.get("include_caches")):
        return "캐시 폴더 포함 설정이 달라졌습니다"
    if old.get("tool") != new["tool"]:
        return "도구 버전이 달라졌습니다"
    return "내보내기 설정이 달라졌습니다"


class _Journal:
    """진행 기록(JSON lines). 줄마다 쓰고, sync() 때만 디스크에 확정(fsync)한다."""

    def __init__(self, path: Path, mode: str) -> None:
        self.f = open(path, mode)

    def write(self, obj: dict) -> None:
        self.f.write((json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))

    def sync(self) -> None:
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass


_ENTRY_INT = ("ssize", "mtime_ns", "off", "end", "size")
_ENTRY_STR = ("arc", "src", "sha256", "rule", "base", "rel", "method")
_ZI_KEYS = ("fn", "dt", "ct", "ex", "cm", "cs", "cv", "xv", "fl", "ia", "ea", "ho", "crc", "csz", "fsz")


def _entry_shape_ok(e: dict) -> bool:
    if not all(isinstance(e.get(k), str) for k in _ENTRY_STR):
        return False
    if not all(isinstance(e.get(k), int) and not isinstance(e.get(k), bool) and e[k] >= 0 for k in _ENTRY_INT):
        return False
    zi = e.get("zi")
    if not isinstance(zi, dict) or not all(k in zi for k in _ZI_KEYS):
        return False
    try:
        _zi_load(zi)
    except (TypeError, ValueError, KeyError):
        return False
    return e["end"] > e["off"] and zi["fn"] == e["arc"]


def read_journal(path: Path) -> dict:
    """진행 기록을 읽는다. 저장점 이후의 줄과 마지막의 끊긴 줄은 버린다.

    반환: {"head": dict|None, "entries": [저장점까지 확정된 엔트리], "zip_size": int, "error": str}
    error 가 비어 있지 않으면 기록이 손상된 것이다(이어받지 않는다).
    """
    out = {"head": None, "entries": [], "zip_size": 0, "error": ""}
    try:
        data = path.read_bytes()
    except OSError as e:
        out["error"] = f"저장점 기록을 읽을 수 없습니다({type(e).__name__})"
        return out
    lines = data.split(b"\n")
    lines.pop()  # 마지막 조각: 줄바꿈으로 끝났으면 빈 값, 아니면 쓰다 끊긴 줄(믿지 않는다)
    pending: list[dict] = []
    seen: set[str] = set()
    try:
        for i, raw in enumerate(lines):
            obj = json.loads(raw.decode("utf-8"))
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            kind = obj.get("t")
            if i == 0:
                if kind != "head" or obj.get("v") != JOURNAL_VERSION or not isinstance(obj.get("fp"), str) \
                        or not isinstance(obj.get("info"), dict):
                    raise ValueError("bad head")
                out["head"] = obj
            elif kind == "e":
                if not _entry_shape_ok(obj) or obj["arc"] in seen:
                    raise ValueError("bad entry")
                seen.add(obj["arc"])
                pending.append(obj)
            elif kind == "sp":
                z = obj.get("zip")
                if not isinstance(z, int) or isinstance(z, bool) or z < out["zip_size"]:
                    raise ValueError("bad savepoint")
                out["entries"] += pending
                pending = []
                out["zip_size"] = z
            elif kind != "f":
                raise ValueError("unknown line")
    except (ValueError, UnicodeDecodeError, RecursionError):
        out.update(head=None, entries=[], zip_size=0, error="저장점 기록이 손상되었습니다")
        return out
    if out["head"] is None:
        out["error"] = "저장점 기록이 비어 있습니다"
    return out


def inspect_savepoint(out: Path | str) -> dict | None:
    """같은 이름으로 다시 내보낼 때 이어받을 수 있는 임시 파일이 있는지 본다(읽기 전용, GUI 안내용).

    없으면 None, 있으면 {"files": 저장점까지 파일 수, "bytes": 바이트, "usable": 기록이 온전한지, "reason": 사유}.
    규칙/선택이 같은지는 실제로 내보낼 때 확인한다.
    """
    part, jpath = part_paths(Path(out))
    try:
        if not part.is_file() or not jpath.is_file():
            return None
        j = read_journal(jpath)
        if j["error"] or part.stat().st_size < j["zip_size"]:
            return {"files": 0, "bytes": 0, "usable": False, "reason": j["error"] or "임시 파일이 기록과 맞지 않습니다"}
        return {"files": len(j["entries"]), "bytes": sum(e["size"] for e in j["entries"]), "usable": True, "reason": ""}
    except OSError:
        return None


LOCK_SUFFIX = ".part.lock"
FAILED_SUFFIX = ".part.failed"  # 최종 검사에서 문제가 난 임시 패키지(진단용으로 보존)


def _is_ours(path: Path, kind: str) -> bool:
    """지워도 되는, 이 도구가 만든 임시물인지 '머리'로 확인한다(이름만 같은 다른 파일을 지우지 않는다).

    zip 류(.part/.part.new/.part.failed)는 빈 파일이거나 zip 지역 헤더(PK\x03\x04)로 시작해야 하고,
    journal 류는 첫 줄이 {"t":"head",...} JSON 이어야 한다."""
    try:
        with open(fs.long_path(path), "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    if kind == "zip":
        return head == b"" or head[:4] == b"PK" + bytes((3, 4))
    if not head:
        return True
    first = head.split(bytes((10,)), 1)[0]
    try:
        obj = json.loads(first.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(obj, dict) and obj.get("t") == "head"


def _partial_files(out: Path) -> list[tuple[Path, str]]:
    part, jpath = part_paths(out)
    return [(part, "zip"), (jpath, "journal"), (part.with_name(part.name + ".new"), "zip"),
            (jpath.with_name(jpath.name + ".new"), "journal"), (out.with_name(out.name + FAILED_SUFFIX), "zip")]


def discard_partial(out: Path | str, *, keep_failed: bool = False) -> None:
    """이 도구가 만든 임시물(.part, .part.journal, 정리 중 남은 .new, 보존해 둔 .part.failed)만 지운다. 완성본은 건드리지 않는다.

    지우기 전에 파일 머리를 확인한다. 이 도구가 만든 것이 아니거나 확인할 수 없으면 아무것도 지우지 않고
    PackageError('다른 출력 이름을 쓰세요')로 멈춘다(R1-5)."""
    out = Path(out)
    todo = [(p, k) for p, k in _partial_files(out)
            if (p.is_file() or p.is_symlink()) and not (keep_failed and p.name.endswith(FAILED_SUFFIX))]
    for p, kind in todo:
        if p.is_symlink() or not _is_ours(p, kind):
            raise PackageError(f"출력 이름과 겹치는 파일이 이 도구가 만든 임시 파일이 아닌 것 같아 지우지 않았습니다: {p}. "
                               "다른 출력 이름(또는 다른 폴더)을 쓰세요.")
    for p, _ in todo:
        p.unlink()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ctypes.get_last_error() == 5 or k32.GetLastError() == 5  # 접근 거부 = 살아 있음
            try:
                code = ctypes.c_ulong(0)
                ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
                return bool(ok) and code.value == 259  # STILL_ACTIVE
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
        except (AttributeError, OSError):
            return True  # 판단할 수 없으면 살아 있다고 보고 막는다(안전 쪽)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class _OutputLock:
    """<out>.part.lock: 같은 출력 이름으로 동시에 내보내기를 못 하게 하는 배타 락(PID 기록). 끝나면 지운다."""

    def __init__(self, out: Path) -> None:
        self.path = out.with_name(out.name + LOCK_SUFFIX)
        self.held = False
        self.token = f"{os.getpid()}-{id(self)}-{time.time_ns()}"  # 이 락의 주인 표식(강제 중단된 옛 스레드가 새 락을 지우지 않게)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pid = self._owner()
                if pid and _pid_alive(pid) and pid != os.getpid():
                    raise PackageError(f"같은 출력 파일({self.path.name[:-len(LOCK_SUFFIX)]})로 내보내기가 이미 실행 중입니다(PID {pid}). "
                                       "끝나길 기다리거나 다른 출력 이름을 쓰세요.") from None
                try:  # 주인이 없는(비정상 종료로 남은) 락은 치우고 다시 시도한다
                    os.unlink(self.path)
                except OSError:
                    raise PackageError(f"이전 실행이 남긴 잠금 파일을 지우지 못했습니다: {self.path}. 다른 출력 이름을 쓰세요.") from None
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps({"pid": os.getpid(), "token": self.token,
                                    "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
            self.held = True
            return
        raise PackageError(f"잠금 파일을 만들지 못했습니다: {self.path}. 다른 출력 이름을 쓰세요.")

    def _owner(self) -> int:
        try:
            return int(json.loads(self.path.read_text(encoding="utf-8")).get("pid") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            return 0

    def release(self) -> None:
        if self.held:
            try:  # 같은 PID 의 다른(새) 실행이 잡은 락이면 지우지 않는다
                cur = json.loads(self.path.read_text(encoding="utf-8")).get("token")
            except (OSError, ValueError, AttributeError):
                cur = self.token
            if cur in (None, self.token):
                try:
                    os.unlink(self.path)
                except OSError:
                    pass
            self.held = False


def _cleanup_stale_stages(folder: Path) -> int:
    """이전 실행이 남긴 .pcmigrate-stage-* 임시 폴더 중 주인(PID)이 살아 있지 않은 것을 지운다(R1-7). 지운 개수 반환."""
    n = 0
    try:
        entries = [e for e in os.scandir(folder) if e.name.startswith(STAGE_PREFIX) and e.is_dir(follow_symlinks=False)]
    except OSError:
        return 0
    for e in entries:
        rest = e.name[len(STAGE_PREFIX):]
        head = rest.split("-", 1)[0]
        try:
            if head.isdigit() and "-" in rest:
                alive = _pid_alive(int(head))
            else:  # 옛 이름(PID 없음): 하루 넘게 손대지 않은 것만 정리
                alive = time.time() - e.stat().st_mtime < 24 * 3600
        except OSError:
            alive = True
        if not alive:
            shutil.rmtree(e.path, ignore_errors=True)
            n += 0 if os.path.exists(e.path) else 1
    return n


def _headers_ok(part: Path, entries: list[dict]) -> bool:
    """저장점까지의 각 엔트리 위치에 올바른 지역 헤더(PK\\x03\\x04 + 이름)가 있는지 본다."""
    try:
        with open(part, "rb") as f:
            for e in entries:
                f.seek(e["off"])
                head = f.read(30)
                if len(head) < 30 or head[:4] != b"PK\x03\x04":
                    return False
                nlen = int.from_bytes(head[26:28], "little")
                name = f.read(nlen)
                enc = "utf-8" if e["zi"]["fl"] & 0x800 else "cp437"
                if name.decode(enc, "replace") != e["arc"]:
                    return False
    except OSError:
        return False
    return True


def _compact(part: Path, keep: list[dict]) -> int:
    """바뀐/없어진 엔트리를 빼고 남길 엔트리의 바이트 구간만 새 파일로 옮긴다(압축을 다시 하지 않는다). 새 크기 반환."""
    new = part.with_name(part.name + ".new")
    try:
        with open(part, "rb") as src, open(new, "wb") as dst:
            for e in keep:
                src.seek(e["off"])
                pos = dst.tell()
                left = e["end"] - e["off"]
                while left > 0:
                    chunk = src.read(min(CHUNK, left))
                    if not chunk:
                        raise OSError("임시 파일이 기록보다 짧습니다")
                    dst.write(chunk)
                    left -= len(chunk)
                e["off"], e["end"] = pos, dst.tell()
                e["zi"]["ho"] = pos
            dst.flush()
            os.fsync(dst.fileno())
            size = dst.tell()
        os.replace(new, part)
        return size
    finally:
        new.unlink(missing_ok=True)


def _write_journal(jpath: Path, head: dict, entries: list[dict], zip_size: int) -> _Journal:
    """깨끗한 진행 기록을 새로 쓰고(원자적 교체) 이어쓰기용으로 연다."""
    tmp = jpath.with_name(jpath.name + ".new")
    j = _Journal(tmp, "wb")
    try:
        j.write(head)
        for e in entries:
            j.write(e)
        j.write({"t": "sp", "zip": zip_size, "n": len(entries)})
        j.sync()
    finally:
        j.close()
    os.replace(tmp, jpath)
    return _Journal(jpath, "ab")


@dataclass
class _Resumed:
    kept: dict = field(default_factory=dict)   # arc -> 확정된 엔트리 기록
    dropped: int = 0                           # 원본이 바뀌어 다시 담을 엔트리 수
    reason: str = ""                           # 이어받지 않고 처음부터 하는 이유
    zip_size: int = 0
    journal: object = None


def _source_unchanged(e: dict, item: "Item | None") -> bool:
    if item is None or str(item.src) != e["src"] or item.rule_id != e["rule"] or item.base != e["base"] or item.rel != e["rel"]:
        return False
    try:
        st = os.stat(fs.long_path(item.src))
    except OSError:
        return False
    return st.st_size == e["ssize"] and st.st_mtime_ns == e["mtime_ns"]


def _prepare_part(part: Path, jpath: Path, head: dict, info: dict, items: dict, resume: bool) -> _Resumed:
    """이어받을 수 있으면 .part 를 저장점까지 되돌려 두고 보존할 엔트리를 돌려준다. 아니면 임시물을 지우고 처음부터."""
    existed = part.exists() or jpath.exists()
    r = _Resumed()
    if not resume:
        if existed:
            r.reason = "'처음부터 다시 만들기'를 골라 이전 임시 파일을 지우고 새로 만듭니다"
    elif part.is_file() and not jpath.is_file():
        r.reason = "저장점 기록이 없어 처음부터 만듭니다"
    elif jpath.is_file() and not part.is_file():
        r.reason = "임시 패키지 파일이 없어 처음부터 만듭니다"
    elif part.is_file() and jpath.is_file():
        j = read_journal(jpath)
        if j["error"]:
            r.reason = j["error"] + "(처음부터 만듭니다)"
        elif j["head"]["fp"] != head["fp"]:
            r.reason = _fp_reason(j["head"]["info"], info) + "(처음부터 만듭니다)"
        elif part.stat().st_size < j["zip_size"] or any(e["end"] > j["zip_size"] for e in j["entries"]) \
                or not _headers_ok(part, j["entries"]):
            r.reason = "임시 패키지 파일이 기록과 맞지 않아 처음부터 만듭니다"
        elif j["entries"]:
            keep = [e for e in j["entries"] if _source_unchanged(e, items.get(e["arc"]))]
            r.dropped = len(j["entries"]) - len(keep)
            size = j["zip_size"]
            if r.dropped:
                size = _compact(part, keep)
            elif part.stat().st_size > size:
                with open(part, "r+b") as f:  # 저장점 뒤에 쓰던 부분은 믿지 않고 잘라낸다
                    f.truncate(size)
                    f.flush()
                    os.fsync(f.fileno())
            if keep:
                r.kept = {e["arc"]: e for e in keep}
                r.zip_size = size
                r.journal = _write_journal(jpath, j["head"], keep, size)
                return r
    discard_partial(part.with_name(part.name[:-len(PART_SUFFIX)]))
    return r


class _ResumeVerifyFailed(Exception):
    """이어받은 내용을 포함한 .part 가 최종 검사를 통과하지 못함. 임시물을 지우고 처음부터 다시 만든다."""


# --------------------------------------------------------------------------- export
