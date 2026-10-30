r"""'내 파일 자동 찾기': 고정 드라이브를 읽기 전용으로 훑어 옮길 만한 사용자 데이터(도면/사진/영상/문서/압축)를 폴더 단위로 묶는다.

- 읽기만 한다(파일/폴더를 만들거나 바꾸지 않는다). 심볼릭 링크/정션은 따라가지 않는다.
- OneDrive 클라우드 전용 파일은 파일을 열지 않고 속성만 읽어 '클라우드 전용 N개'로 따로 센다(내려받기를 일으키지 않는다).
- 읽기 오류는 기록하고 계속한다. 시간 상한(time_limit)을 넘으면 그때까지의 결과를 돌려주고 complete=False 로 표시한다.
  취소(cancel)는 Cancelled 예외로 끝낸다(다른 작업과 같은 규칙).
- 결과는 '후보'일 뿐이다. 자동으로 선택하지 않는다(선택 파일의 include 는 모두 false).

후보를 묶는 규칙(project 항목): 드라이브 루트(또는 --roots 폴더) 아래를 내려가다가
  (1) 그 폴더에 설계/사진/영상 파일이 직접 있거나 문서/압축/큰 파일이 직접 3개 이상 있으면 그 폴더를 하나의 후보로 보고(하위 폴더 포함),
  (2) 아니면 하위 폴더로 내려가고, (3) max_depth 에 닿으면 그 폴더를 후보로 한다.
  사용자 기본 폴더(문서/바탕화면/사진/동영상/다운로드/음악)는 후보 규칙과 상관없이 폴더 하나당 항목 하나(user_folder)로 따로 낸다.
"""
from __future__ import annotations

import json
import ntpath
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import failure, fs
from .cancel import Cancelled
from .discover_data import REGENERABLE_DIRS
from .fs import long_path
from .progress import ProgressInfo, fmt_bytes

SELECTION_FORMAT = "pcmigrate-data-selection"
SELECTION_VERSION = 1

# --------------------------------------------------------------------------- 분류 표(한 곳)
# 근거 등급: A = 해당 프로그램의 고유 파일 형식(제조사 문서로 확인되는 확장자), B = 널리 쓰이는 일반 형식(분류가 거의 확실),
#            C = 용도가 다양해 분류가 느슨함(참고용: 문서/압축/큰 기타 파일). 이름만 보고 정하며 내용은 열지 않는다.
DESIGN_EXT: dict[str, str] = {
    ".dwg": "A", ".dxf": "A", ".dwt": "A", ".dws": "A", ".dwf": "A", ".dwfx": "A",   # AutoCAD 계열
    ".rvt": "A", ".rfa": "A", ".rte": "A", ".rft": "A",                                # Revit
    ".ifc": "A", ".ifczip": "A",                                                      # IFC(BIM 교환)
    ".3dm": "A", ".gh": "A", ".ghx": "A",                                              # Rhino/Grasshopper
    ".skp": "A",                                                                      # SketchUp
    ".pln": "A", ".pla": "A", ".tpl": "B",                                            # ArchiCAD(.tpl 은 다른 용도도 있어 B)
    ".nwd": "A", ".nwc": "A", ".nwf": "A",                                            # Navisworks
    ".dgn": "A",                                                                      # MicroStation
    ".vwx": "A",                                                                      # Vectorworks
    ".max": "B", ".3ds": "B", ".fbx": "B", ".stl": "B", ".rcp": "B", ".rcs": "B",      # 3D/포인트클라우드 일반 형식
    ".c4d": "B", ".blend": "B", ".lmv": "B",
    # CAD 교환: 국제/업계 표준 규격으로 확장자가 정해져 있어 A (ISO 10303-21 STEP, ANSI IGES, ACIS SAT)
    ".step": "A", ".stp": "A", ".iges": "A", ".igs": "A", ".sat": "A",
    # 포인트클라우드/스캔: LAS(ASPRS 규격)와 E57(ASTM E2807)은 규격 확장자라 A, .laz 는 LAS 압축 사실상 표준이라 B
    # (.las 는 지구물리 시추 로그(LAS, Log ASCII Standard)와 확장자가 겹쳐 토목/지질 폴더에서 오탐할 수 있다)
    ".las": "A", ".e57": "A", ".laz": "B",
    # 드론/사진측량·GIS 결과물: 다른 용도와 겹침이 적은 것만. .ply(스캔 메시, 3D 프린팅/게임에도 쓰여 B),
    # .osgb(경사사진 3D 타일, OpenSceneGraph 형식이라 B), .kml/.kmz(OGC 규격이나 지도 앱 내보내기도 많아 B), .shp(Esri 문서화 형식, A)
    ".ply": "B", ".osgb": "B", ".kml": "B", ".kmz": "B", ".shp": "A",
}
PHOTO_EXT: dict[str, str] = {
    ".jpg": "B", ".jpeg": "B", ".png": "B", ".tif": "B", ".tiff": "B", ".bmp": "B", ".gif": "B", ".webp": "B",
    ".heic": "B", ".heif": "B",
    ".raw": "A", ".cr2": "A", ".cr3": "A", ".nef": "A", ".arw": "A", ".dng": "A", ".orf": "A", ".rw2": "A",  # 카메라 RAW
    ".psd": "A", ".psb": "A", ".ai": "A", ".indd": "A",                                # Adobe 그래픽
    ".eps": "B",                                                                      # 벡터 그래픽(Adobe 계열이나 LaTeX/인쇄에도 쓰여 B)
    ".svg": "C",                                                                      # 벡터 그래픽(W3C 표준). 웹/개발 폴더의 아이콘으로도 많아 C
}
VIDEO_EXT: dict[str, str] = {
    ".mp4": "B", ".mov": "B", ".avi": "B", ".mkv": "B", ".wmv": "B", ".m4v": "B", ".mts": "B", ".m2ts": "B",
    ".webm": "B", ".flv": "B",
}
DOC_EXT: dict[str, str] = {
    ".pdf": "B", ".doc": "B", ".docx": "B", ".xls": "B", ".xlsx": "B", ".ppt": "B", ".pptx": "B",
    ".hwp": "A", ".hwpx": "A",                                                        # 한글
    ".rtf": "C", ".odt": "C", ".txt": "C", ".csv": "C",                                # 용도가 다양해 C
}
ARCHIVE_EXT: dict[str, str] = {".zip": "B", ".7z": "B", ".rar": "B", ".tar": "C", ".gz": "C", ".tgz": "C"}

CATEGORIES: tuple[str, ...] = ("design", "photo", "video", "doc", "archive", "other")
CATEGORY_LABELS = {"design": "설계", "photo": "사진", "video": "영상", "doc": "문서", "archive": "압축", "other": "기타 큰 파일"}
DEFAULT_CATEGORIES: tuple[str, ...] = ("design", "photo", "video", "doc", "archive")  # 'other' 는 고를 때만 후보 조건에 쓴다
EXT_TABLE: dict[str, tuple[str, str]] = {}  # 확장자 -> (분류, 등급)
for _cat, _tbl in (("design", DESIGN_EXT), ("photo", PHOTO_EXT), ("video", VIDEO_EXT), ("doc", DOC_EXT), ("archive", ARCHIVE_EXT)):
    for _e, _g in _tbl.items():
        EXT_TABLE[_e] = (_cat, _g)
# 기타 큰 파일(등급 C): 위 표에 없는 확장자 중 이 크기 이상. 실행/설치/시스템 성격 확장자는 제외한다.
LARGE_OTHER_BYTES = 100 * 1024 * 1024
OTHER_EXCLUDED_EXT = frozenset({".exe", ".dll", ".msi", ".msu", ".sys", ".cab", ".tmp", ".log", ".etl", ".dmp", ".pf", ".lnk",
                                ".vhd", ".vhdx", ".wim", ".esd", ".mui", ".cat", ".pdb", ".obj", ".lib"})

# --------------------------------------------------------------------------- 자동 제외 표(폴더 이름, 소문자 완전 일치)
# 드라이브 루트(또는 --roots 로 준 폴더) 바로 아래에서만 적용: 이름만으로는 사용자 폴더일 수도 있는 것은 루트 직속에서만 뺀다.
ROOT_LEVEL_SKIP: dict[str, str] = {
    "windows": "시스템", "program files": "시스템", "program files (x86)": "시스템", "programdata": "시스템",
    "$windows.~bt": "시스템", "$windows.~ws": "시스템", "$sysreset": "시스템", "windows.old": "시스템", "recovery": "시스템",
    "msocache": "시스템", "perflogs": "시스템", "config.msi": "시스템", "found.000": "시스템",
    "temp": "임시", "tmp": "임시",
}
# 어느 깊이에서든 적용. $recycle.bin, system volume information 은 A, 나머지는 B(도구가 다시 만드는 폴더).
ANY_LEVEL_SKIP: dict[str, str] = {
    "$recycle.bin": "휴지통", "system volume information": "시스템", "appdata": "시스템(사용자 AppData)",
    "application data": "시스템", "local settings": "시스템",
    "node_modules": "재생성", ".git": "버전 이력(.git 은 통계에서 뺀다. 폴더 단위 전송 때는 함께 복사된다)",
    "__pycache__": "재생성", ".cache": "재생성",
}
for _n in REGENERABLE_DIRS:
    ANY_LEVEL_SKIP.setdefault(_n, "재생성")
USERS_LEVEL_SKIP = frozenset({"default", "default user", "all users", "defaultuser0"})  # <루트>\Users 바로 아래

USER_FOLDER_KEYS: tuple[str, ...] = ("documents", "desktop", "pictures", "videos", "downloads", "music")
USER_FOLDER_LABELS = {"documents": "문서", "desktop": "바탕화면", "pictures": "사진", "videos": "동영상", "downloads": "다운로드", "music": "음악"}

MAX_ERRORS_LISTED = 200
MAX_EXCLUDED_EXAMPLES = 30
MAX_RECURSION = 120  # 이보다 깊은 폴더는 따라 내려가지 않는다(파이썬 재귀 한계 보호)
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CLOUD_ATTR_MASK = 0x00040000 | 0x00400000


def classify_name(name: str, size: int = 0, other_large: int = LARGE_OTHER_BYTES) -> tuple[str, str] | None:
    """파일 이름(+크기)으로 (분류, 근거 등급). 해당 없으면 None. 내용은 보지 않는다."""
    ext = os.path.splitext(name)[1].lower()
    hit = EXT_TABLE.get(ext)
    if hit:
        return hit
    if size >= other_large and ext not in OTHER_EXCLUDED_EXT:
        return "other", "C"
    return None


# --------------------------------------------------------------------------- 결과 형식
class _Agg:
    __slots__ = ("cats", "files", "bytes", "latest", "exts", "cloud_n", "cloud_b", "errors", "weak")

    def __init__(self) -> None:
        self.cats: dict[str, list[int]] = {}
        self.files = self.bytes = 0
        self.latest = 0.0
        self.exts: Counter = Counter()
        self.cloud_n = self.cloud_b = self.errors = 0
        self.weak: dict[str, int] = {}  # 등급 C 라서 파일 1개로는 폴더를 후보로 만들지 않는 분류(설계/사진/영상)별 개수

    def add_file(self, size: int, mtime: float, cat: str | None, ext: str, weak: bool = False) -> None:
        self.files += 1
        self.bytes += size
        if cat:
            if weak and cat in ("design", "photo", "video"):
                self.weak[cat] = self.weak.get(cat, 0) + 1
            c = self.cats.setdefault(cat, [0, 0])
            c[0] += 1
            c[1] += size
            if cat != "other":
                self.exts[ext] += 1
            if mtime > self.latest:
                self.latest = mtime

    def merge(self, o: "_Agg") -> None:
        self.files += o.files
        self.bytes += o.bytes
        self.latest = max(self.latest, o.latest)
        self.exts.update(o.exts)
        self.cloud_n += o.cloud_n
        self.cloud_b += o.cloud_b
        self.errors += o.errors
        for k, v in o.weak.items():
            self.weak[k] = self.weak.get(k, 0) + v
        for k, v in o.cats.items():
            c = self.cats.setdefault(k, [0, 0])
            c[0] += v[0]
            c[1] += v[1]


@dataclass
class FolderItem:
    path: str
    kind: str  # "project" | "user_folder"
    drive: str
    label: str = ""  # user_folder 일 때 documents/desktop/...
    categories: dict = field(default_factory=dict)  # 분류 -> {"files": n, "bytes": b}
    total_files: int = 0  # 폴더 전체(분류 안 된 작은 파일 포함): 폴더 단위로 옮기면 복사되는 양
    total_bytes: int = 0
    latest_mtime: str = ""  # ISO(UTC), 분류된 파일 중 가장 최근 수정
    top_extensions: list = field(default_factory=list)
    cloud_only_files: int = 0
    cloud_only_bytes: int = 0
    read_errors: int = 0
    basis: str = ""  # 이 폴더를 후보로 본 근거(한국어 한 줄)

    def selected_bytes(self, categories: Iterable[str]) -> int:
        return sum(self.categories.get(c, {}).get("bytes", 0) for c in categories)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FindResult:
    projects: list[FolderItem] = field(default_factory=list)
    user_folders: list[FolderItem] = field(default_factory=list)
    drives: list[dict] = field(default_factory=list)  # {"drive","root","files","bytes","categories","cloud_only_files","cloud_only_bytes"}
    roots: list[str] = field(default_factory=list)
    categories: tuple = DEFAULT_CATEGORIES
    min_size: int = 0
    max_depth: int = 4
    complete: bool = True
    stop_reason: str = ""  # "" | "time"
    seconds: float = 0.0
    files_seen: int = 0
    dirs_seen: int = 0
    skipped_links: int = 0
    cloud_only_files: int = 0
    cloud_only_bytes: int = 0
    excluded: dict = field(default_factory=dict)  # 이유 -> 폴더 수
    excluded_examples: list = field(default_factory=list)  # [{"path","reason"}]
    errors: list = field(default_factory=list)  # [{"path","code","text"}] 앞 MAX_ERRORS_LISTED 개
    error_count: int = 0

    def to_dict(self) -> dict:
        return {"complete": self.complete, "stop_reason": self.stop_reason, "seconds": round(self.seconds, 2),
                "roots": self.roots, "categories": list(self.categories), "min_size": self.min_size, "max_depth": self.max_depth,
                "files_seen": self.files_seen, "dirs_seen": self.dirs_seen, "skipped_links": self.skipped_links,
                "cloud_only_files": self.cloud_only_files, "cloud_only_bytes": self.cloud_only_bytes,
                "excluded": self.excluded, "excluded_examples": self.excluded_examples,
                "drives": self.drives, "projects": [p.to_dict() for p in self.projects],
                "user_folders": [p.to_dict() for p in self.user_folders],
                "error_count": self.error_count, "errors": self.errors}


# --------------------------------------------------------------------------- 드라이브/사용자 폴더/규칙 경로(주입 가능)
def list_drives(include_removable: bool = False, mask_fn: Callable[[], int] | None = None,
                kind_fn: Callable[[str], str] = fs.drive_kind) -> list[tuple[str, str]]:
    """(루트, 종류) 목록. 기본은 고정 드라이브만(네트워크/CD/RAM 디스크/알 수 없음 제외). include_removable 이면 이동식도 포함한다.

    USB 외장 SSD/HDD 는 고정으로 보일 수 있다: 옮길 대상(외장)을 찾아 훑지 않으려면 --roots 로 지정한다."""
    if mask_fn is None:
        def mask_fn() -> int:
            import ctypes
            return int(ctypes.windll.kernel32.GetLogicalDrives())
    try:
        mask = mask_fn()
    except (AttributeError, OSError):
        return []
    out = []
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(65 + i)}:\\"
            kind = kind_fn(root)
            if kind == "fixed" or (include_removable and kind == "removable"):
                out.append((root, kind))
    return out


def default_user_folders(env: dict | None = None) -> dict[str, str]:
    """사용자 기본 폴더 경로(알려진 폴더 API 로 리디렉션/OneDrive 위치까지 반영). 알 수 없으면 홈 아래 기본 이름."""
    out: dict[str, str] = {}
    csidl = {"documents": 5, "desktop": 0, "pictures": 39, "videos": 14, "music": 13}
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(520)
            for key, cid in csidl.items():
                if ctypes.windll.shell32.SHGetFolderPathW(None, cid, None, 0, buf) == 0 and buf.value:
                    out[key] = buf.value
        except (AttributeError, OSError):
            pass
    home = (env or os.environ).get("USERPROFILE") or os.path.expanduser("~")
    for key, name in (("documents", "Documents"), ("desktop", "Desktop"), ("pictures", "Pictures"),
                      ("videos", "Videos"), ("music", "Music"), ("downloads", "Downloads")):
        out.setdefault(key, os.path.join(home, name))
    return {k: v for k, v in out.items() if os.path.isdir(v)}


def rule_covered_paths(rules=None) -> list[str]:
    """설정 규칙(rules/*.json)이 담당하는 폴더(환경변수를 풀고 glob 을 펼친 실제 경로). 파일 규칙은 제외. 규칙을 못 읽으면 빈 목록."""
    import glob as _glob
    from . import envpaths
    if rules is None:
        try:
            from .rules import default_rules_dir, load_rules
            rules = load_rules(default_rules_dir())
        except Exception as e:  # noqa: BLE001 - 규칙 폴더가 없어도 찾기는 계속한다(규칙 제외만 못 함)
            from . import errorlog
            errorlog.record(e, "내 파일 찾기: 규칙 읽기")
            return []
    out: list[str] = []
    for rule in rules:
        for pr in rule.paths:
            if pr.kind != "dir":
                continue
            text = envpaths.expand_env(pr.path)
            if "%" in text:
                continue
            for hit in _glob.glob(text):
                if os.path.isdir(hit):
                    out.append(hit)
    return out


# --------------------------------------------------------------------------- 훑기
def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/") or os.path.normcase(p)


def _drive_of(path: str) -> str:
    d = ntpath.splitdrive(os.path.abspath(path))[0]
    return d.upper() if d else path


def _qualifies_direct(a: _Agg, cats: Iterable[str] = DEFAULT_CATEGORIES) -> bool:
    """이 폴더에 선택한 분류의 데이터가 직접 있어 폴더 하나를 후보로 볼 만한지(설계/사진/영상/큰 기타 1개, 문서/압축 3개 이상)."""
    c, cats = a.cats, tuple(cats)
    if "other" in c and "other" in cats:
        return True
    for k in ("design", "photo", "video"):  # 등급 C 형식(.svg 등)만 있으면 파일 1개로는 후보가 아니다(문서/압축과 같은 3개 이상 기준)
        if k in c and k in cats and c[k][0] > a.weak.get(k, 0):
            return True
    weak = sum(a.weak.get(k, 0) for k in ("design", "photo", "video") if k in cats)
    return sum(c.get(k, [0])[0] for k in ("doc", "archive") if k in cats) + weak >= 3


class _Finder:
    def __init__(self, categories, min_size, max_depth, time_limit, cancel, progress, progress_info, clock, attr_getter,
                 exclude_paths, user_paths, other_large, report_every):
        self.categories, self.min_size, self.max_depth = tuple(categories), min_size, max_depth
        self.cancel, self.progress, self.progress_info, self.clock = cancel, progress, progress_info, clock
        self.attr_getter, self.other_large, self.report_every = attr_getter, other_large, report_every
        self.exclude = {_norm(p) for p in exclude_paths}
        self.user_paths = user_paths  # normpath -> (key, 원래 경로)
        self.start = self.last = clock()
        self.deadline = self.start + time_limit if time_limit else None
        self.stopped = False
        self.res = FindResult(categories=self.categories, min_size=min_size, max_depth=max_depth)
        self.cur = ""

    # -- 제어
    def _check(self) -> bool:
        if self.cancel is not None and self.cancel():
            raise Cancelled()
        if self.deadline is not None and self.clock() >= self.deadline:
            self.stopped = True
            self.res.complete, self.res.stop_reason = False, "time"
        return self.stopped

    def _tick(self, path: str) -> None:
        self.cur = path
        now = self.clock()
        if (self.progress or self.progress_info) and now - self.last >= self.report_every:
            self.last = now
            r = self.res
            msg = f"내 파일을 찾는 중… 파일 {r.files_seen:,}개 확인 (현재: {path})"
            if self.progress:
                self.progress(msg)
            if self.progress_info:
                self.progress_info(ProgressInfo("find", 0, 0, r.files_seen, 0, message=msg, current=path, skipped=r.cloud_only_files,
                                                failed=r.error_count))

    def _err(self, path: str, exc: BaseException) -> None:
        f = failure.classify(exc, path)
        self.res.error_count += 1
        if len(self.res.errors) < MAX_ERRORS_LISTED:
            self.res.errors.append({"path": path, "code": f.code, "text": f.text})

    def _excluded(self, path: str, reason: str) -> None:
        self.res.excluded[reason] = self.res.excluded.get(reason, 0) + 1
        if len(self.res.excluded_examples) < MAX_EXCLUDED_EXAMPLES:
            self.res.excluded_examples.append({"path": path, "reason": reason})

    def _skip_reason(self, name: str, parts: tuple[str, ...], full: str) -> str | None:
        low = name.lower()
        if low in ANY_LEVEL_SKIP:
            return ANY_LEVEL_SKIP[low]
        if len(parts) == 0 and low in ROOT_LEVEL_SKIP:  # parts = 부모까지의 상대 이름들. 비어 있으면 루트 직속
            return ROOT_LEVEL_SKIP[low]
        if len(parts) == 1 and parts[0] == "users" and low in USERS_LEVEL_SKIP:
            return "시스템(사용자 기본 프로필)"
        n = _norm(full)
        if n in self.exclude:
            return "설정 규칙이 담당하는 경로"
        if n in self.user_paths:
            return "사용자 기본 폴더(따로 항목으로 냄)"
        return None

    # -- 한 폴더. 돌려주는 값: (하위 전체 합계, 후보 항목 목록)
    def scan_dir(self, path: str, parts: tuple[str, ...], depth: int, force_unit: bool = False,
                 is_drive_root: bool = False) -> tuple[_Agg, list[FolderItem]]:
        direct, total = _Agg(), _Agg()
        if self._check():
            return total, []
        self.res.dirs_seen += 1
        self._tick(path)
        try:
            with os.scandir(long_path(path)) as it:
                items = sorted(it, key=lambda e: e.name.lower())
        except OSError as e:
            self._err(path, e)
            total.errors += 1
            return total, []
        subdirs: list[str] = []
        for e in items:
            name = e.name
            if name.startswith(".pcmigrate-"):
                continue
            full = os.path.join(path, name)
            try:
                if e.is_symlink():
                    self.res.skipped_links += 1
                    continue
                st = e.stat(follow_symlinks=False)
                attrs = getattr(st, "st_file_attributes", 0)
                if e.is_dir(follow_symlinks=False):
                    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                        self.res.skipped_links += 1
                        continue
                    why = self._skip_reason(name, parts, full)
                    if why:
                        self._excluded(full, why)
                    else:
                        subdirs.append(name)
                    continue
                if self.attr_getter is not None:
                    attrs = self.attr_getter(Path(full))
            except OSError as ex:
                self._err(full, ex)
                direct.errors += 1
                continue
            self.res.files_seen += 1
            if attrs & CLOUD_ATTR_MASK:  # 속성만 읽는다(내려받기 유발 금지). 용량은 이 PC 에 없으므로 합계에 넣지 않는다.
                direct.cloud_n += 1
                direct.cloud_b += st.st_size
                continue
            hit = classify_name(name, st.st_size, self.other_large)
            direct.add_file(st.st_size, st.st_mtime, hit[0] if hit else None, os.path.splitext(name)[1].lower(), bool(hit and hit[1] == "C"))
        total.merge(direct)
        child_units: list[FolderItem] = []
        if depth < MAX_RECURSION and not self.stopped:
            for name in subdirs:
                agg, units = self.scan_dir(os.path.join(path, name), parts + (name.lower(),), depth + 1)
                total.merge(agg)
                child_units += units
                if self.stopped:
                    break
        elif subdirs and depth >= MAX_RECURSION:
            self._err(path, OSError("폴더가 너무 깊습니다"))
        unit = force_unit or (not is_drive_root and (depth >= self.max_depth or _qualifies_direct(direct, self.categories)))
        if unit and self._wanted(total, force_unit):
            why = ("사용자 기본 폴더" if force_unit else
                   "폴더에 데이터 파일이 직접 있음" if _qualifies_direct(direct, self.categories) else f"깊이 {self.max_depth} 한도에서 묶음")
            return total, [self._item(path, total, "user_folder" if force_unit else "project", why)]
        return total, child_units

    def _wanted(self, a: _Agg, force: bool) -> bool:
        if force:
            return a.files > 0 or a.cloud_n > 0
        n = sum(a.cats.get(c, [0])[0] for c in self.categories)
        b = sum(a.cats.get(c, [0, 0])[1] for c in self.categories)
        return n > 0 and b >= self.min_size

    def _item(self, path: str, a: _Agg, kind: str, why: str, label: str = "") -> FolderItem:
        cats = {c: {"files": a.cats[c][0], "bytes": a.cats[c][1]} for c in CATEGORIES if c in a.cats}
        latest = datetime.fromtimestamp(a.latest, timezone.utc).isoformat(timespec="seconds") if a.latest else ""
        bits = [f"{CATEGORY_LABELS[c]} {v['files']}개" for c, v in cats.items()]
        exts = [e for e, _n in a.exts.most_common(5)]
        basis = f"{why}: " + (", ".join(bits) if bits else "분류된 파일 없음") + (f" (대표 {' '.join(exts)})" if exts else "")
        return FolderItem(path=path, kind=kind, drive=_drive_of(path), label=label, categories=cats, total_files=a.files,
                          total_bytes=a.bytes, latest_mtime=latest, top_extensions=exts, cloud_only_files=a.cloud_n,
                          cloud_only_bytes=a.cloud_b, read_errors=a.errors, basis=basis)


def find_data(roots: Iterable[str | os.PathLike] | None = None, *, user_folders: dict[str, str] | None = None,
              categories: Iterable[str] = DEFAULT_CATEGORIES, min_size: int = 0, max_depth: int = 4,
              time_limit: float | None = None, cancel: Callable[[], bool] | None = None,
              progress: Callable[[str], None] | None = None, progress_info: Callable[[ProgressInfo], None] | None = None,
              clock: Callable[[], float] = time.monotonic, attr_getter: Callable[[Path], int] | None = None,
              exclude_paths: Iterable[str] | None = None, include_removable: bool = False,
              drives_fn: Callable[[], list[tuple[str, str]]] | None = None,
              other_large_bytes: int = LARGE_OTHER_BYTES, report_every: float = 2.0) -> FindResult:
    """roots(없으면 고정 드라이브 전체)를 읽기 전용으로 훑어 FindResult 를 돌려준다. 모든 환경 의존 값은 주입할 수 있다.

    user_folders: {"documents": 경로, ...}. None 이면 roots 가 없을 때만 현재 사용자의 기본 폴더를 조회한다({} 를 주면 사용자 폴더 항목 없음).
    exclude_paths: 설정 규칙이 담당하는 폴더(이 아래는 훑지 않음). None 이면 rules/ 에서 만든다([] 를 주면 제외 없음).
    attr_getter: 파일 속성 주입(클라우드 전용 흉내용). None 이면 scandir 가 준 속성을 쓴다.
    """
    cats = tuple(categories)
    bad = [c for c in cats if c not in CATEGORIES]
    if bad:
        raise ValueError(f"알 수 없는 분류: {', '.join(bad)} (가능: {', '.join(CATEGORIES)})")
    if not cats:
        raise ValueError("분류를 하나 이상 골라야 합니다")
    if roots is None:
        root_list = [r for r, _k in (drives_fn or (lambda: list_drives(include_removable)))()]
    else:
        root_list = [os.fspath(r) for r in roots]
    if user_folders is None:  # 훑을 곳을 직접 지정(--roots)했으면 그곳만 훑는다: 사용자 기본 폴더는 기본(전체 드라이브) 모드에서만 따로 조사
        user_folders = default_user_folders() if roots is None else {}
    ufs = {k: str(v) for k, v in user_folders.items()}
    upaths = {_norm(v): (k, v) for k, v in ufs.items() if os.path.isdir(v)}
    excl = rule_covered_paths() if exclude_paths is None else list(exclude_paths)
    f = _Finder(cats, min_size, max(1, max_depth), time_limit, cancel, progress, progress_info, clock, attr_getter, excl, upaths,
                other_large_bytes, report_every)
    res = f.res
    res.roots = list(root_list)
    drive_aggs: dict[str, list] = {}  # 드라이브 -> [루트 목록, _Agg]
    for root in root_list:
        if f.stopped:
            break
        if not os.path.isdir(long_path(root)):
            f._err(root, FileNotFoundError(root))
            continue
        is_dr = os.path.dirname(os.path.abspath(root)) == os.path.abspath(root)  # 드라이브 루트는 통째로 후보로 만들지 않는다
        agg, units = f.scan_dir(os.path.abspath(root), (), 0, is_drive_root=is_dr)
        res.projects += units
        d = drive_aggs.setdefault(_drive_of(root), [[], _Agg()])
        d[0].append(root)
        d[1].merge(agg)
    for key in USER_FOLDER_KEYS:  # 사용자 기본 폴더: 폴더 하나당 항목 하나
        if f.stopped:
            break
        hit = next(((n, v) for n, v in upaths.items() if v[0] == key), None)
        if hit is None:
            continue
        agg, units = f.scan_dir(hit[1][1], ("~user",), 0, force_unit=True)  # parts 를 비우지 않아 루트 직속 제외 이름(temp 등)이 적용되지 않는다
        for u in units:
            u.label = key
            res.user_folders.append(u)
        d = drive_aggs.setdefault(_drive_of(hit[1][1]), [[], _Agg()])
        d[1].merge(agg)
    res.projects.sort(key=lambda p: -p.total_bytes)
    for drive, (rs, a) in sorted(drive_aggs.items()):
        res.drives.append({"drive": drive, "roots": rs, "files": a.files, "bytes": a.bytes,
                           "categories": {c: {"files": a.cats[c][0], "bytes": a.cats[c][1]} for c in CATEGORIES if c in a.cats},
                           "cloud_only_files": a.cloud_n, "cloud_only_bytes": a.cloud_b})
        res.cloud_only_files += a.cloud_n
        res.cloud_only_bytes += a.cloud_b
    res.seconds = clock() - f.start
    return res


# --------------------------------------------------------------------------- 사람이 읽는 요약
def summary_lines(res: FindResult, limit: int = 30) -> list[str]:
    lines = [f"내 파일 찾기 결과(읽기만 했습니다): 확인한 파일 {res.files_seen:,}개, 폴더 {res.dirs_seen:,}개, {res.seconds:.0f}초"]
    if not res.complete:
        lines.append("- 시간 상한에 닿아 일부만 훑었습니다. 아래 합계는 일부입니다(--time-limit 을 늘려 다시 실행하세요).")
    for d in res.drives:
        lines.append(f"- 드라이브 {d['drive']}: 파일 {d['files']:,}개 / {fmt_bytes(d['bytes'])}"
                     + "".join(f", {CATEGORY_LABELS[c]} {v['files']:,}개" for c, v in d["categories"].items() if c != "other"))
    if res.user_folders:
        lines.append("사용자 기본 폴더:")
        for u in res.user_folders:
            lines.append(f"  [{USER_FOLDER_LABELS.get(u.label, u.label)}] {u.path}  {u.total_files:,}개 / {fmt_bytes(u.total_bytes)}"
                         + (f"  (클라우드 전용 {u.cloud_only_files}개 별도)" if u.cloud_only_files else ""))
    lines.append(f"프로젝트/자료 폴더 후보 {len(res.projects)}개(큰 순서)" + (f", 앞 {limit}개만 표시" if len(res.projects) > limit else "") + ":")
    for p in res.projects[:limit]:
        when = p.latest_mtime[:10] if p.latest_mtime else "?"
        lines.append(f"  {p.path}  {p.total_files:,}개 / {fmt_bytes(p.total_bytes)}  최근 수정 {when}  {' '.join(p.top_extensions[:3])}")
        lines.append(f"      근거: {p.basis}")
    if res.cloud_only_files:
        lines.append(f"- OneDrive 클라우드 전용 {res.cloud_only_files:,}개({fmt_bytes(res.cloud_only_bytes)})는 이 PC 에 내용이 없어 세지 않았고 내려받지 않았습니다.")
    if res.skipped_links:
        lines.append(f"- 바로가기/정션(링크) {res.skipped_links}개는 따라가지 않았습니다.")
    if res.excluded:
        lines.append("- 자동 제외한 폴더: " + ", ".join(f"{k} {v}개" for k, v in sorted(res.excluded.items(), key=lambda kv: -kv[1])))
    if res.error_count:
        lines.append(f"- 읽지 못한 곳 {res.error_count}개(건너뛰고 계속함, --json 의 errors 참고)")
    return lines


# --------------------------------------------------------------------------- 선택 파일(JSON)
class SelectionFileError(ValueError):
    """선택 파일을 읽을 수 없거나 형식이 맞지 않음. 메시지는 사용자에게 그대로 보여 줄 수 있다."""


def selection_dict(res: FindResult) -> dict:
    """선택 파일 내용. 모든 항목은 include:false(자동 선택 금지) — 사용자가 true 로 바꾼 것만 옮긴다."""
    def row(p: FolderItem) -> dict:
        return {"path": p.path, "include": False, "kind": p.kind, "label": p.label, "drive": p.drive,
                "total_files": p.total_files, "total_bytes": p.total_bytes, "latest_mtime": p.latest_mtime,
                "categories": p.categories, "cloud_only_files": p.cloud_only_files, "basis": p.basis}
    return {"format": SELECTION_FORMAT, "version": SELECTION_VERSION,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "옮길 폴더의 include 를 true 로 바꾸세요. 모두 false 로 시작합니다(자동 선택 안 함). "
                    "pcmigrate transfer --from-selection <이 파일> <대상> 으로 옮깁니다.",
            "items": [row(p) for p in res.user_folders + res.projects]}


def write_selection(res: FindResult, path: str | os.PathLike) -> Path:
    """선택 파일을 쓴다(임시 파일에 쓴 뒤 이름 변경). 같은 이름 파일이 있으면 .bak 으로 보존한다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(selection_dict(res), ensure_ascii=False, indent=2), encoding="utf-8")
    if p.exists():
        from .copier import _backup_path  # 번호 붙은 백업 규칙을 한 곳(긴 경로 대응)에서 쓴다
        os.replace(p, _backup_path(p))
    os.replace(tmp, p)
    return p


@dataclass(frozen=True)
class SelectedItem:
    path: str
    include: bool
    total_bytes: int | None = None
    total_files: int | None = None
    label: str = ""
    dest_rel: str = ""  # 비어 있으면 <드라이브>\<경로> 구조, 값이 있으면 '대상 폴더 바로 아래의 이 이름 폴더'로 보낸다(단일 폴더 이름만)


def selected_items_dict(items: list["SelectedItem"]) -> dict:
    """SelectedItem 목록 -> 선택 파일 dict(parse_selection 의 역). dest_rel 은 있을 때만 키를 넣는다(이전 버전은 모르는 키를 무시)."""
    rows = []
    for it in items:
        row: dict = {"path": it.path, "include": it.include}
        if it.total_bytes is not None:
            row["total_bytes"] = it.total_bytes
        if it.total_files is not None:
            row["total_files"] = it.total_files
        if it.label:
            row["label"] = it.label
        if it.dest_rel:
            row["dest_rel"] = it.dest_rel
        rows.append(row)
    return {"format": SELECTION_FORMAT, "version": SELECTION_VERSION, "items": rows}


def load_selection(path: str | os.PathLike) -> list[SelectedItem]:
    """선택 파일을 읽어 검증한다(include 가 true 가 아닌 항목도 돌려주며 호출자가 거른다). 형식이 틀리면 SelectionFileError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise SelectionFileError(f"선택 파일을 읽을 수 없습니다: {e}") from e
    except (ValueError, RecursionError) as e:
        raise SelectionFileError(f"선택 파일이 올바른 JSON 이 아닙니다: {e}") from e
    return parse_selection(data)


def parse_selection(data: object) -> list[SelectedItem]:
    if not isinstance(data, dict) or data.get("format") != SELECTION_FORMAT:
        raise SelectionFileError("pcmigrate find-data 가 만든 선택 파일이 아닙니다(format 불일치)")
    if data.get("version") != SELECTION_VERSION:
        raise SelectionFileError(f"지원하지 않는 선택 파일 버전입니다: {data.get('version')!r}")
    items = data.get("items")
    if not isinstance(items, list):
        raise SelectionFileError("선택 파일에 items 목록이 없습니다")
    out: list[SelectedItem] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not isinstance(it.get("path"), str) or not it["path"].strip():
            raise SelectionFileError(f"items[{i}] 에 path 문자열이 없습니다")
        inc = it.get("include", False)
        if not isinstance(inc, bool):
            raise SelectionFileError(f"items[{i}].include 는 true/false 여야 합니다(입력: {inc!r})")
        tb, tf = it.get("total_bytes"), it.get("total_files")
        dr = it.get("dest_rel", "")
        if dr is None:
            dr = ""
        if not isinstance(dr, str):
            raise SelectionFileError(f"items[{i}].dest_rel 은 폴더 이름 문자열이어야 합니다(입력: {dr!r})")
        if dr:
            from .multi_transfer import check_dest_rel
            try:
                check_dest_rel(dr)
            except ValueError as e:
                raise SelectionFileError(f"items[{i}].dest_rel 이 올바르지 않습니다: {e}") from e
        out.append(SelectedItem(it["path"], inc, tb if isinstance(tb, int) and tb >= 0 else None,
                                tf if isinstance(tf, int) and tf >= 0 else None, str(it.get("label", "")), dr))
    return out
