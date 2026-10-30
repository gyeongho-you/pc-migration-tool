"""설정 기록 모드의 스냅샷: 폴더를 읽기 전용으로 훑어 '파일 목록 + 크기 + 수정 시각 + (작은 파일의) SHA-256' 만 기록한다.

원칙
- 파일 내용은 저장하지 않는다(해시만). 대상 폴더는 읽기만 한다(쓰기/삭제/이름 바꾸기 없음).
- 링크/정션은 따라가지 않고, OneDrive 클라우드 전용 파일은 내려받지 않고(속성 확인) 건너뛰고, 읽지 못한 파일은 항목만 격리해 기록한다.
- 키는 환경변수 표기 경로(%APPDATA%\\... 등)라 사용자 이름이 스냅샷 파일에 남지 않는다.

해시 최적화(정확도와 속도의 균형)
- 첫 스냅샷: 모든 파일의 크기/mtime 을 기록하고, hash_limit(기본 20MB) 이하 파일만 해시한다. 변경 '전' 내용 해시는 나중에 얻을 수
  없으므로 처음에 해 둬야 '수정 시각만 바뀌고 내용은 같은' 파일을 후보에서 걸러낼 수 있다.
- 후속 스냅샷: 직전 스냅샷과 크기/mtime 이 같으면 직전 해시를 그대로 가져오고(파일을 읽지 않음), 달라진 파일만 해시한다.
- hash_limit 보다 큰 파일은 해시하지 않고 크기/mtime 만 본다(내용 비교 불가로 표시). hash_limit=0 이면 모두 크기/mtime 만(가장 빠름).
한계: 내용만 바뀌고 크기와 mtime 이 모두 같은 파일(수정 시각을 일부러 되돌리는 도구)은 놓칠 수 있다.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import discover_data, envpaths, errorlog, failure, fs, junk
from .cancel import Cancelled
from .progress import ProgressInfo
from .scan import is_cloud_only, FILE_ATTRIBUTE_REPARSE_POINT

HASH_LIMIT = 20 * 1024 * 1024
MAX_FILES = 400_000
MAX_ERROR_LOGS = 50
MAX_LISTED = 200  # 스냅샷에 목록으로 남기는 읽지 못한/클라우드 항목 수(나머지는 개수만)
CHUNK = 1024 * 1024
HOME_TOKEN = "%USERPROFILE%"
DOCS_TOKEN = "%USERPROFILE%\\Documents"

# 루트 바로 아래의 시스템/설치 캐시 폴더(소문자). discover_data.SKIP_NAMES 중 '앱 설정이 들어갈 수 없는' 것만 고른다.
NOISE_TOP: frozenset[str] = frozenset({
    "temp", "tmp", "packages", "publishers", "crashdumps", "d3dscache", "inetcache", "comms", "connecteddevices", "programs",
    "squirreltemp", "virtualstore", "package cache", "softwaredistribution", "usoshared", "usoprivate", "elevateddiagnostics",
    "placeholdertilelogoplate", "application data", "pcmigrate",  # pcmigrate: 이 도구 자신의 로그/기록
})
# 사용자 지정 루트(--roots)에서는 앱 폴더 안의 'Packages' 같은 이름이 진짜 설정일 수 있어 명백한 임시/캐시 이름만 건너뛴다.
NOISE_TOP_CUSTOM: frozenset[str] = frozenset({"temp", "tmp", "inetcache", "crashdumps", "d3dscache", "squirreltemp", "pcmigrate"})
# 두 단계 (상위, 하위) 이름이 일치하는 잡음 폴더(소문자): Windows 구성요소, 브라우저 프로필 등
NOISE_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("microsoft", "windows"), ("microsoft", "edge"), ("microsoft", "teams"), ("google", "chrome"), ("microsoft", "onedrive"),
    ("microsoft", "windowsapps"), ("microsoft", "crypto"), ("microsoft", "protect"),
})


@dataclass(frozen=True)
class Root:
    token: str  # 환경변수 표기(%APPDATA% 등) 또는 사용자 지정 절대 경로
    path: str
    dotfiles_only: bool = False
    custom: bool = False  # --roots 로 직접 지정(프로그램 이름 좁히기 없이 전체)


def _norm(p: str) -> str:
    """경로 비교용 정규화. scandir 항목 경로에는 긴 경로 접두(역슬래시 2개+물음표)가 붙으므로 접두를 떼고 비교한다(자기 제외가 어긋나지 않게)."""
    return os.path.normcase(os.path.normpath(fs.plain_path_text(p)))


def default_roots(documents_fn: Callable[[], str | None] | None = None) -> tuple[list[Root], list[str]]:
    """기본 루트(%APPDATA%, %LOCALAPPDATA%, %PROGRAMDATA%, 문서 폴더, 홈의 점 폴더/파일). (루트들, 해석 못 한 항목 안내) 를 돌려준다."""
    roots: list[Root] = []
    notes: list[str] = []
    for var in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        v = envpaths.lookup(var)
        if v and os.path.isdir(v):
            roots.append(Root(f"%{var}%", v))
        else:
            notes.append(f"%{var}% 폴더를 찾지 못해 건너뜁니다")
    home = envpaths.lookup("USERPROFILE") or os.path.expanduser("~")
    docs = None
    try:
        docs = documents_fn() if documents_fn else _known_documents()
    except Exception as e:  # noqa: BLE001 - 문서 폴더 조회 실패는 기본 위치로 대신한다
        errorlog.record(e, "설정 기록: 문서 폴더 조회")
    docs = docs or os.path.join(home, "Documents")
    if os.path.isdir(docs):
        roots.append(Root(DOCS_TOKEN, docs))
    else:
        notes.append("문서 폴더를 찾지 못해 건너뜁니다")
    if home and os.path.isdir(home):
        roots.append(Root(HOME_TOKEN, home, dotfiles_only=True))
    return roots, notes


def _known_documents() -> str | None:
    from .find_data import default_user_folders
    return default_user_folders().get("documents")


def custom_root(path: str) -> Root:
    """사용자가 지정한 폴더. 홈 아래면 %USERPROFILE% 표기로, 아니면 그대로(개인 이름이 없는 경로일 때만 의미 있음)."""
    ap = fs.plain_path_text(os.path.abspath(path))
    return Root(standard_token(ap), ap, custom=True)


def standard_token(path: str) -> str:
    """경로를 %APPDATA% / %LOCALAPPDATA% / %PROGRAMDATA% / %USERPROFILE%\\Documents / %USERPROFILE% 표준 토큰 표기로 바꾼다
    (가장 긴 기준 폴더 우선, 같은 폴더를 가리키는 %USERPROFILE%\\AppData\\Roaming 같은 표기도 %APPDATA% 로 통일). 어디에도 안 맞으면 홈 치환만."""
    bases: list[tuple[str, str]] = []
    for var in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        v = envpaths.lookup(var)
        if v:
            bases.append((f"%{var}%", v))
    home = envpaths.lookup("USERPROFILE") or os.path.expanduser("~")
    if home:
        bases.append((DOCS_TOKEN, os.path.join(home, "Documents")))
        bases.append((HOME_TOKEN, home))
    n = _norm(path)
    for tok, base in sorted(bases, key=lambda b: -len(b[1])):
        nb = _norm(base)
        if n == nb or n.startswith(nb + os.sep):
            rest = os.path.normpath(fs.plain_path_text(path))[len(os.path.normpath(base)):]
            return tok + rest
    return errorlog.tokenize_home(path)


_EXTRA_STOP = frozenset({"app", "apps", "pro", "the", "and", "for", "new", "lite"})  # 너무 흔해 엉뚱한 폴더(Application Data 등)가 걸림
# 제조사 낱말: 이것만으로 폴더를 고르면 같은 제조사의 다른 프로그램까지 통째로 훑으므로 단독 매칭에 쓰지 않는다(제조사\프로그램 두 단계에서 프로그램 낱말로만 맞춘다).
VENDOR_WORDS = frozenset({"adobe", "autodesk", "microsoft", "google", "trimble", "bentley", "graphisoft", "nemetschek", "dassault",
                          "jetbrains", "mozilla", "apple", "oracle", "intel", "nvidia", "amd", "corel", "esri", "ptc", "siemens",
                          "mcneel", "robert", "chaos", "chaosgroup", "maxon", "unity", "epic", "valve"})


def match_words(program: str, extra: list[str] | None = None) -> list[str]:
    """프로그램 이름에서 폴더 이름 부분 일치에 쓸 낱말(소문자). 너무 흔한 낱말/두 글자 이하는 뺀다. 공백 없는 전체 이름도 하나로 친다."""
    words = [w for w in re.findall(r"[0-9A-Za-zㄱ-힣]+", program.lower())
             if len(w) >= 3 and w not in discover_data.TOKEN_STOPWORDS and w not in _EXTRA_STOP]
    specific = [w for w in words if w not in VENDOR_WORDS]
    words = specific or words  # 제조사 낱말뿐인 이름이면 그대로 쓴다(사용자가 그것을 원한 것)
    compact = re.sub(r"\s+", "", program.lower())
    if len(compact) >= 3 and compact not in VENDOR_WORDS:
        words.append(compact)
    words += [w.strip().lower() for w in (extra or []) if w.strip()]
    return list(dict.fromkeys(words))


def _name_matches(name: str, words: list[str]) -> bool:
    low = name.lower()
    flat = low.replace(" ", "")
    return any(w in low or w in flat for w in words)


@dataclass
class SnapshotOptions:
    hash_limit: int = HASH_LIMIT
    max_files: int = MAX_FILES
    include_caches: bool = False
    narrow_words: list[str] = field(default_factory=list)  # 비어 있으면 좁히지 않음
    exclude_dirs: list[str] = field(default_factory=list)  # 건드리지 않을 폴더(작업 폴더 등)


@dataclass
class Snapshot:
    label: str
    taken_at: str
    files: dict = field(default_factory=dict)  # 환경변수 표기 경로 -> [size, mtime_ns, sha256 | None]
    unreadable: list = field(default_factory=list)  # [[경로, 사유 코드], ...] (앞 MAX_LISTED 개)
    unreadable_count: int = 0
    cloud_skipped: int = 0
    cloud_examples: list = field(default_factory=list)
    links_skipped: int = 0
    noise_skipped: dict = field(default_factory=dict)  # 사유 -> 폴더/파일 수
    truncated: bool = False
    hashed: int = 0
    carried: int = 0
    hash_limit: int = HASH_LIMIT
    roots: list = field(default_factory=list)  # 훑은 루트의 환경변수 표기
    notes: list = field(default_factory=list)
    matched: list = field(default_factory=list)  # 이름으로 좁혔을 때 훑은 시작 폴더(환경변수 표기)

    def as_dict(self) -> dict:
        return {"version": 1, "label": self.label, "taken_at": self.taken_at, "hash_limit": self.hash_limit,
                "roots": self.roots, "truncated": self.truncated, "unreadable_count": self.unreadable_count,
                "unreadable": self.unreadable, "cloud_skipped": self.cloud_skipped, "cloud_examples": self.cloud_examples,
                "links_skipped": self.links_skipped, "noise_skipped": self.noise_skipped, "hashed": self.hashed,
                "carried": self.carried, "notes": self.notes, "matched": self.matched, "files": self.files}

    @staticmethod
    def from_dict(d: dict) -> "Snapshot":
        s = Snapshot(str(d.get("label", "")), str(d.get("taken_at", "")))
        s.files = {k: list(v) for k, v in (d.get("files") or {}).items()}
        for key in ("unreadable", "cloud_examples", "roots", "notes", "matched"):
            setattr(s, key, list(d.get(key) or []))
        for key in ("unreadable_count", "cloud_skipped", "links_skipped", "hashed", "carried"):
            setattr(s, key, int(d.get(key) or 0))
        s.noise_skipped = dict(d.get("noise_skipped") or {})
        s.truncated = bool(d.get("truncated"))
        s.hash_limit = int(d.get("hash_limit") or 0)
        return s


def sha256_file(path: str, cancel: Callable[[], bool] | None = None) -> str:
    """파일을 읽기 전용으로 열어 SHA-256 을 계산한다. 읽지 못하면 OSError."""
    h = hashlib.sha256()
    with open(fs.long_path(path), "rb") as f:
        while True:
            if cancel is not None and cancel():
                raise Cancelled()
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class _Walker:
    def __init__(self, opts: SnapshotOptions, prev: Snapshot | None, snap: Snapshot, cancel, progress, clock,
                 attr_getter, hash_fn, scrub) -> None:
        self.o, self.prev, self.snap = opts, prev, snap
        self.cancel, self.progress, self.clock = cancel, progress, clock
        self.attr_getter, self.hash_fn, self.scrub = attr_getter, hash_fn, scrub
        self.junk = junk.make_spec()
        self.excl = [_norm(p) for p in opts.exclude_dirs if p]
        self.custom = False  # 지금 훑는 루트가 --roots 로 직접 지정한 것인지
        self.bytes_hashed = 0
        self.logged = 0
        self.last_report = clock()

    # ---- 보고/격리
    def _noise(self, why: str) -> None:
        self.snap.noise_skipped[why] = self.snap.noise_skipped.get(why, 0) + 1

    def _unreadable(self, tok: str, exc: BaseException, path: str) -> None:
        f = failure.classify(exc, path)
        self.snap.unreadable_count += 1
        if len(self.snap.unreadable) < MAX_LISTED:
            self.snap.unreadable.append([tok, f.code])
        if self.logged < MAX_ERROR_LOGS:  # 오류 로그에도 남긴다(전부 쓰지는 않고 상한까지)
            self.logged += 1
            errorlog.record(exc, "설정 기록 스냅샷: " + tok)

    def _tick(self, current: str = "") -> None:
        if self.progress is None:
            return
        now = self.clock()
        if now - self.last_report >= 1.0:
            self.last_report = now
            n = len(self.snap.files)
            self.progress(ProgressInfo("scan", self.bytes_hashed, 0, n, 0, message=f"스냅샷 '{self.snap.label}': 파일 {n:,}개 확인", current=current))

    # ---- 잡음 판정
    def _skip_dir(self, parts: tuple[str, ...]) -> str | None:
        if self.o.include_caches:
            return None
        low = tuple(p.lower() for p in parts)
        if len(low) == 1 and low[0] in (NOISE_TOP_CUSTOM if self.custom else NOISE_TOP):
            return "시스템/설치 폴더"
        if len(low) == 2 and low in NOISE_PAIRS and not self.custom:
            return "Windows/브라우저 구성요소"
        if discover_data.is_regenerable(parts):
            return "캐시/로그 폴더"
        if self.junk.skip_dir(parts[-1]):
            return "정크 폴더"
        if len(low) == 1 and any(s in low[0] for s in ("cache", "crashpad", "crash reports", "telemetry")):
            return "캐시 폴더"
        return None

    def _skip_file(self, name: str) -> str | None:
        if self.o.include_caches:
            return None
        if self.junk.file_group(name):
            return "정크 파일"
        return None

    def _excluded(self, path: str) -> bool:
        n = _norm(path)
        return any(n == e or n.startswith(e + os.sep) for e in self.excl)

    # ---- 파일 하나
    def _record_file(self, abspath: str, tok: str, st: os.stat_result, attrs: int) -> None:
        if len(self.snap.files) >= self.o.max_files:
            self.snap.truncated = True
            return
        if is_cloud_only(attrs):  # 내려받지 않는다(열면 다운로드가 시작됨)
            self.snap.cloud_skipped += 1
            if len(self.snap.cloud_examples) < 20:
                self.snap.cloud_examples.append(tok)
            return
        size, mtime = st.st_size, st.st_mtime_ns
        digest = None
        old = self.prev.files.get(tok) if self.prev else None
        if old is not None and old[0] == size and old[1] == mtime and old[2]:
            digest = old[2]  # 크기/mtime 이 같으면 읽지 않고 직전 해시를 가져온다
            self.snap.carried += 1
        elif 0 < self.o.hash_limit and size <= self.o.hash_limit:
            try:
                digest = self.hash_fn(abspath, self.cancel)
                self.snap.hashed += 1
                self.bytes_hashed += size
            except Cancelled:
                raise
            except OSError as e:
                self._unreadable(tok, e, abspath)
        self.snap.files[tok] = [size, mtime, digest]

    # ---- 폴더 순회
    def walk(self, start: str, tok: str, parts: tuple[str, ...]) -> None:
        stack = [(start, tok, parts)]
        while stack and not self.snap.truncated:
            if self.cancel is not None and self.cancel():
                raise Cancelled()
            path, ptok, pparts = stack.pop()
            try:
                with os.scandir(fs.long_path(path)) as it:
                    items = sorted(it, key=lambda e: e.name.lower())
            except OSError as e:
                self._unreadable(ptok + "\\", e, path)
                continue
            subdirs = []
            for k, e in enumerate(items):
                if k % 1000 == 999 and self.cancel is not None and self.cancel():
                    raise Cancelled()
                if self.snap.truncated:
                    break
                etok = ptok + "\\" + e.name
                try:
                    self._entry(e, path, etok, pparts, subdirs)
                except Cancelled:
                    raise
                except OSError as ex:
                    self._unreadable(etok, ex, os.path.join(path, e.name))
            stack.extend(reversed(subdirs))
            self._tick(ptok)

    def _entry(self, e: os.DirEntry, path: str, etok: str, pparts: tuple[str, ...], subdirs: list) -> None:
        if e.is_symlink():
            self.snap.links_skipped += 1
            return
        st = e.stat(follow_symlinks=False)
        attrs = getattr(st, "st_file_attributes", 0)
        full = os.path.join(path, e.name)
        if e.is_dir(follow_symlinks=False):
            if attrs & FILE_ATTRIBUTE_REPARSE_POINT:  # 정션
                self.snap.links_skipped += 1
                return
            if self._excluded(full):
                return
            parts = pparts + (e.name,)
            why = self._skip_dir(parts)
            if why:
                self._noise(why)
                return
            subdirs.append((full, etok, parts))
            return
        if self.excl and self._excluded(full):
            return
        why = self._skip_file(e.name)
        if why:
            self._noise(why)
            return
        if self.attr_getter is not None:
            attrs = self.attr_getter(full)
        self._record_file(full, etok, st, attrs)

    def start_entry(self, root: Root, e: os.DirEntry, rel_parts: tuple[str, ...], tok: str) -> None:
        """루트(또는 그 아래 한 단계)의 항목 하나에서 시작: 폴더면 순회, 파일이면 기록."""
        subdirs: list = []
        self._entry(e, os.path.dirname(e.path), tok, rel_parts[:-1], subdirs)
        for full, etok, parts in subdirs:
            self.walk(full, etok, parts)


def take_snapshot(label: str, roots: list[Root], opts: SnapshotOptions | None = None, prev: Snapshot | None = None,
                  cancel: Callable[[], bool] | None = None, progress: Callable[[ProgressInfo], None] | None = None,
                  clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = datetime.now,
                  attr_getter: Callable[[str], int] | None = None,
                  hash_fn: Callable[..., str] = sha256_file, scrub: Callable[[str], str] = lambda s: s) -> Snapshot:
    """roots 를 읽기 전용으로 훑어 Snapshot 을 만든다. 취소하면 Cancelled(부분 결과는 저장하지 않는 것이 호출자의 약속)."""
    opts = opts or SnapshotOptions()
    snap = Snapshot(label, now().isoformat(timespec="seconds"), hash_limit=opts.hash_limit, roots=[r.token for r in roots])
    w = _Walker(opts, prev, snap, cancel, progress, clock, attr_getter, hash_fn, scrub)
    seen: set[str] = set()
    for root in roots:
        key = _norm(root.path)
        if key in seen:
            continue
        seen.add(key)
        if cancel is not None and cancel():
            raise Cancelled()
        if w._excluded(root.path):  # 작업 폴더 자체(또는 그 안)를 루트로 준 경우: 자기 기록 파일을 훑지 않는다
            snap.notes.append("작업 폴더 안의 루트는 자기 기록 파일이 섞이지 않게 건너뛰었습니다")
            continue
        w.custom = root.custom
        narrow = bool(opts.narrow_words) and not root.custom
        try:
            with os.scandir(fs.long_path(root.path)) as it:
                top = sorted(it, key=lambda e: e.name.lower())
        except OSError as e:
            w._unreadable(root.token + "\\", e, root.path)
            continue
        for e in top:
            if snap.truncated:
                break
            if cancel is not None and cancel():
                raise Cancelled()
            name_l = e.name.lower()
            if root.dotfiles_only and (not e.name.startswith(".") or name_l in discover_data.DOT_SKIP):
                continue
            tok = root.token + "\\" + e.name
            try:
                if not narrow or _name_matches(e.name, opts.narrow_words):
                    if narrow and len(snap.matched) < MAX_LISTED:
                        snap.matched.append(tok)
                    w.start_entry(root, e, (e.name,), tok)
                    continue
                # 이름이 맞지 않는 폴더는 한 단계 아래(예: Autodesk\Revit)만 이름으로 찾는다
                if e.is_symlink() or not e.is_dir(follow_symlinks=False) or root.dotfiles_only:
                    continue
                if w._skip_dir((e.name,)) or w._excluded(e.path):
                    continue
                with os.scandir(fs.long_path(e.path)) as sub:
                    kids = sorted(sub, key=lambda x: x.name.lower())
                for kid in kids:
                    if snap.truncated:
                        break
                    if _name_matches(kid.name, opts.narrow_words):
                        if len(snap.matched) < MAX_LISTED:
                            snap.matched.append(tok + "\\" + kid.name)
                        w.start_entry(root, kid, (e.name, kid.name), tok + "\\" + kid.name)
            except Cancelled:
                raise
            except OSError as ex:
                w._unreadable(tok, ex, e.path)
    if snap.truncated:
        snap.notes.append(f"파일이 {opts.max_files:,}개를 넘어 여기까지만 기록했습니다(--max-files 로 늘리거나 --program 으로 좁히세요)")
    return snap


def snapshot_changes(a: Snapshot, b: Snapshot) -> tuple[dict[str, str], int, list[str]]:
    """a -> b 에서 바뀐 파일. ({경로: 'added'|'removed'|'modified'}, 시각만 바뀐 파일 수, 내용 비교 불가(해시 없음) 경로들).

    - 크기가 다르면 바뀐 것. 둘 다 해시가 있으면 해시가 다를 때만 바뀐 것(시각만 바뀐 파일은 제외).
    - 한쪽이라도 해시가 없으면(큰 파일/읽기 실패) 크기/mtime 이 다르면 바뀐 것으로 보고 '내용 비교 불가'로 따로 기록한다.
    """
    out: dict[str, str] = {}
    touched = 0
    unknown: list[str] = []
    for k, vb in b.files.items():
        va = a.files.get(k)
        if va is None:
            out[k] = "added"
        elif va[0] != vb[0]:
            out[k] = "modified"
        elif va[2] and vb[2]:
            if va[2] != vb[2]:
                out[k] = "modified"
            elif va[1] != vb[1]:
                touched += 1
        elif va[1] != vb[1]:
            out[k] = "modified"
            unknown.append(k)
    for k in a.files:
        if k not in b.files:
            out[k] = "removed"
    return out, touched, unknown


def glob_match(name: str, pattern: str) -> bool:
    return fnmatch.fnmatch(name.lower(), pattern.lower())
