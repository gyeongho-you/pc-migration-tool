"""설치 폴더 설정(Program Files 등 프로그램 설치 폴더 안의 설정 형식 파일) 내보내기/복원 규칙.

배경: AutoCAD 의 사용자 설정(.cuix/.pgp)처럼 일부 프로그램은 설정을 설치 폴더에 둔다. 허용 루트(AppData/ProgramData/문서/홈)
밖이라 기본 흐름은 옮기지 않지만, 사용자가 직접 고른 설정 파일은 아래 안전 조건을 모두 지킬 때만 옮긴다.

내보내기(구 PC, 읽기 전용): 설치 폴더의 '설정 형식 확장자(허용 목록)' 파일만. 실행/스크립트/라이브러리(package.DENY_EXTENSIONS)와 라이선스,
링크, 5MB 초과, 개수/총량 상한 초과는 제외하고 사유를 skipped 에 남긴다. 경로는 환경 토큰(%ProgramFiles% 등)으로 바꿔 저장한다(사용자 이름 경로 없음).
복원(신 PC): 기본은 '복원 안 함'. 사용자가 명시적으로 허용했을 때만, 다음을 모두 만족하는 파일만 쓴다.
 (a) 프로그램 폴더가 이미 있음(없으면 먼저 설치하라고 안내), (b) 허용된 설치 루트(%ProgramFiles%, %ProgramFiles(x86)%, 또는 새 PC 의
 설치 목록에서 찾은 같은 이름 프로그램의 InstallLocation) 아래이고 링크/정션/'..'/대체 데이터 스트림/예약 이름이 없음, (c) 확장자가 설정
 허용 목록에 있고 거부 목록에 없음, (d) 기존 파일은 .bak 보존, (e) 쓰기 권한 사전 점검(없으면 부분 쓰기 없이 중단), (f) 버전 경고.
이 모듈은 규칙 판정만 한다. zip 쓰기/읽기는 package.py 가 이 모듈을 호출해 한다(순환 import 를 피하려고 package 는 함수 안에서 가져온다).
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from .detect import Program
from .envpaths import expand_env

Expand = Callable[[str], str]

INSTALL_ID = "install"  # manifest 의 file.rule 값
INSTALL_ARC = "data/install"  # 패키지 안의 별도 엔트리 영역(규칙/extra 의 data/<dest> 와 섞이지 않는다)
PF_TOKENS = ("%ProgramFiles%", "%ProgramFiles(x86)%")
PF_KEYS = {"%ProgramFiles%": "pf", "%ProgramFiles(x86)%": "pfx86"}
LOC_PREFIX = "@InstallLocation:"  # 설치 위치가 Program Files 밖인 프로그램: 새 PC 의 같은 이름 프로그램 InstallLocation 을 쓴다
SELECTION_KEY = "install_dir"  # 선택 파일(discover --output-selection)의 최상위 목록. 이전 버전은 이 키를 모르고 무시한다
MANIFEST_KEY = "install_dir"

# --------------------------------------------------------------------------- 상한(내보내기). 초과분은 제외하고 표시한다.
INSTALL_FILE_MAX_BYTES = 5 * 1024 * 1024      # 파일 하나
INSTALL_PROGRAM_MAX_BYTES = 20 * 1024 * 1024  # 프로그램(폴더) 하나의 합계
INSTALL_TOTAL_MAX_BYTES = 50 * 1024 * 1024    # 패키지 전체 합계
INSTALL_PROGRAM_MAX_FILES = 30                # 프로그램 하나
INSTALL_TOTAL_MAX_FILES = 300                 # 패키지 전체
INSTALL_MAX_SUBDIR_DEPTH = 1                  # 프로그램 폴더 아래로 한 단계 폴더까지(그 폴더 안의 파일)

# --------------------------------------------------------------------------- 허용 확장자(한 곳). 근거 등급:
# A = 사용자 설정을 담는 것으로 널리 알려진 일반 설정 형식(텍스트), B = 특정 프로그램군의 사용자 설정/UI/별칭 파일(.cuix 등 AutoCAD 계열).
# 근거가 불확실한 형식(.dat, .db, .bin 등)과 실행/스크립트/라이브러리는 넣지 않는다(거부 목록은 package.DENY_EXTENSIONS).
INSTALL_SETTINGS_EXTENSIONS: dict[str, str] = {
    ".ini": "A", ".cfg": "A", ".conf": "A", ".config": "A", ".properties": "A", ".json": "A", ".xml": "A",
    ".yaml": "A", ".yml": "A", ".toml": "A",
    ".pgp": "B", ".cui": "B", ".cuix": "B",
}
# 이름의 낱말(공백/기호로 나눔)에 있으면 라이선스/로그/캐시로 보고 제외
SKIP_WORDS = frozenset({"license", "licenses", "licence", "licensing", "eula", "log", "logs", "cache", "caches", "crash", "crashes",
                        "telemetry", "temp", "tmp"})
# 설치 루트 바로 아래에서 절대 복원 대상이 되지 않는 폴더(소문자). Windows 구성요소/공용 파일/스토어 앱.
PROTECTED_DIRS = frozenset({"windowsapps", "modifiablewindowsapps", "windows nt", "windows defender", "windows defender advanced threat protection",
                            "windows mail", "windows media player", "windows photo viewer", "windows portable devices",
                            "windows security", "windowspowershell", "common files", "internet explorer", "microsoft.net",
                            "reference assemblies", "msbuild", "dotnet", "windows kits", "microsoft update health tools",
                            "uninstall information", "installshield installation information"})
# 위험 확장자 거부 목록은 package.DENY_EXTENSIONS(실행/스크립트/라이브러리/바로가기/설치 파일 등)를 그대로 쓴다.

# 비밀 정보 의심: 이름의 낱말(정확 일치) 또는 부분 문자열, 그리고 파일 앞부분의 '이름=값' 형태
SECRET_WORDS = frozenset({"password", "passwd", "pwd", "secret", "secrets", "token", "tokens", "credential", "credentials", "key", "keys",
                          "apikey", "auth", "cookie", "cookies", "session", "oauth", "login", "logins", "license"})
SECRET_SUBSTRINGS = ("password", "passwd", "secret", "token", "credential", "apikey", "api_key", "api-key", "privatekey",
                     "private_key", "id_rsa", "id_ed25519", ".kdbx", "keychain")
_CONTENT_SECRET = re.compile(rb"(?i)(password|passwd|secret|api[_-]?key|access[_-]?key|token|credential)s?[\"']?\s*[:=]|"
                             rb"<\s*(password|secret|token|apikey)\b")
CONTENT_SCAN_BYTES = 256 * 1024
_WORD = re.compile(r"[0-9a-z]+")

# --------------------------------------------------------------------------- 문구(CLI/GUI/요약 공용)
VERSION_WARNING = ("이 설정 파일은 같은 버전의 프로그램에서 가져온 것입니다. 버전이 다르면 프로그램이 오작동할 수 있습니다")
INSTALL_FIRST_NOTE = "먼저 프로그램을 설치한 뒤 다시 복원하세요"
NEED_ADMIN_MESSAGE = ("프로그램 설치 폴더에 쓸 수 있는 권한이 없습니다. 관리자 권한으로 다시 실행해 주세요"
                      "(이 프로그램을 마우스 오른쪽 단추로 눌러 '관리자 권한으로 실행'). 아무것도 복원하지 않았습니다")
OFF_NOTE = "설치 폴더 설정은 기본으로 복원하지 않습니다. 복원하려면 --allow-install-dir (GUI 는 '설치 폴더 설정도 복원')을 켜고 관리자 권한으로 실행하세요"
MANIFEST_NOTE = ("프로그램 설치 폴더 안의 설정 파일입니다. 신 PC 에서 같은 버전의 프로그램을 먼저 설치한 뒤, 관리자 권한으로 "
                 "'설치 폴더 설정 복원'을 켜서 복원하세요. 라이선스/로그인은 직접 처리하세요.")

# 복원 계획의 설치 폴더 항목 상태(plan.install 의 보기 전용 상태. 실제로 쓰는 항목은 copier 의 would_copy/would_overwrite 등)
MISSING_DIR = "missing_dir"          # 프로그램 폴더(또는 대상 하위 폴더)가 새 PC 에 없음: 먼저 설치
MISSING_PROGRAM = "missing_program"  # 새 PC 설치 목록에 같은 프로그램이 없음


# --------------------------------------------------------------------------- 이름 판정

def _words(name: str) -> list[str]:
    return _WORD.findall(name.lower())


def _skip_word_name(name: str) -> bool:
    return any(w in SKIP_WORDS for w in _words(name))


def is_settings_name(name: str) -> bool:
    """설치 폴더에서 옮길 수 있는 설정 형식의 이름인지: 허용 확장자 + 거부 확장자 아님 + 라이선스/로그/캐시/잠금/임시 이름 아님."""
    from .package import DENY_EXTENSIONS, GLOBAL_EXCLUDE, _excluded
    base = name.rstrip(" .")
    ext = os.path.splitext(base)[1].lower()
    if ext not in INSTALL_SETTINGS_EXTENSIONS or ext in DENY_EXTENSIONS:
        return False
    return not _skip_word_name(base) and not _excluded(base, None, GLOBAL_EXCLUDE)


def sub_dir_skipped(rel: str, folder: str) -> bool:
    """프로그램 폴더 아래의 하위 폴더 이름이 라이선스/로그/캐시 낱말이면 True(그 안의 파일은 설정이 아니다)."""
    parts = PurePosixPath(rel).parts
    n = len(PurePosixPath(folder).parts) if folder else 0
    return any(_skip_word_name(x) for x in parts[n:-1])


def name_secret_hints(name: str) -> list[str]:
    """이름만으로 본 비밀 정보 의심(없으면 빈 목록)."""
    low = name.lower()
    hits = [w for w in _words(low) if w in SECRET_WORDS and w != "license"]
    hits += [s for s in SECRET_SUBSTRINGS if s in low and s not in hits]
    return [name] if hits else []


def secret_hints(path: str | Path) -> list[str]:
    """파일 이름 + 앞부분 내용의 비밀 정보 의심. 값은 보여 주지 않는다. 읽기 전용."""
    p = Path(path)
    out = name_secret_hints(p.name)
    try:
        with open(p, "rb") as f:
            head = f.read(CONTENT_SCAN_BYTES)
    except OSError:
        return out + [f"{p.name}(내용을 읽지 못해 비밀 정보 검사를 끝내지 못함)"]
    if _CONTENT_SECRET.search(head):
        out.append(f"{p.name}(내용에 비밀번호/토큰으로 보이는 항목)")
    return out


# --------------------------------------------------------------------------- 루트 토큰
def parse_root(root: str) -> tuple[str, str] | None:
    """('pf', 토큰) / ('loc', 프로그램 이름) / None(허용되지 않은 루트)."""
    if root in PF_TOKENS:
        return "pf", root
    if root.startswith(LOC_PREFIX):
        name = root[len(LOC_PREFIX):]
        if name and len(name) <= 200 and name == name.strip() and not any(ord(c) < 32 for c in name):
            return "loc", name
    return None


def root_key(root: str) -> str:
    """패키지 안 경로(data/install/<key>/...)에 쓰는 짧은 영문 키."""
    if root in PF_KEYS:
        return PF_KEYS[root]
    return "loc-" + hashlib.sha1(root.encode("utf-8")).hexdigest()[:10]


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _under(child: str | Path, parent: str | Path) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def program_by_name(name: str, programs: list[Program] | None) -> Program | None:
    """같은 이름(대소문자 무시) 프로그램. 여러 개면 InstallLocation 이 있는 것을 먼저."""
    if not programs:
        return None
    hits = [p for p in programs if p.name.strip().casefold() == name.strip().casefold()]
    hits.sort(key=lambda p: 0 if p.install_location else 1)
    return hits[0] if hits else None


def _too_broad(path: Path, expand: Expand) -> bool:
    """설치 위치로 쓰기에 너무 넓거나 시스템 영역인 경로(드라이브 루트, Windows 폴더, Program Files 자체 등)."""
    if len(path.parts) <= 1:
        return True
    for var in ("%SystemRoot%", "%windir%", *PF_TOKENS, "%ProgramW6432%", "%ProgramData%", "%USERPROFILE%"):
        v = expand(var)
        if "%" in v:
            continue
        if var in ("%SystemRoot%", "%windir%"):
            if _under(path, v):
                return True
        elif _under(v, path):  # 그 폴더 자신이거나 그것을 품은 상위 폴더
            return True
    return False


def resolve_root(root: str, programs: list[Program] | None, expand: Expand = expand_env) -> Path | None:
    """루트 토큰을 이 PC 의 실제 폴더로. 허용되지 않았거나 해석할 수 없으면 None(존재 여부는 보지 않는다)."""
    parsed = parse_root(root)
    if parsed is None:
        return None
    kind, val = parsed
    if kind == "pf":
        p = Path(expand(val))
    else:
        prog = program_by_name(val, programs)
        if prog is None or not prog.install_location:
            return None
        p = Path(prog.install_location.strip().strip('"'))
    if not p.is_absolute() or "%" in str(p) or ".." in p.parts:
        return None
    if kind == "loc" and _too_broad(p, expand):
        return None
    return p


def tokenize_location(location: str, program_name: str, expand: Expand = expand_env) -> tuple[str, str] | None:
    """절대 설치 위치 -> (루트 토큰, 루트 기준 프로그램 폴더 상대 경로(posix, 위치 밖이면 빈 문자열)). 쓸 수 없는 위치면 None.

    Program Files 아래면 %ProgramFiles% / %ProgramFiles(x86)% 로 바꾼다. 그 밖이면 프로그램 이름 기반 토큰(@InstallLocation:이름).
    경로 전체를 저장하지 않으므로 사용자 이름 같은 개인 경로가 패키지에 남지 않는다."""
    loc = Path(location.strip().strip('"'))
    if not loc.is_absolute() or "%" in str(loc):
        return None
    for tok in PF_TOKENS:
        base = expand(tok)
        if "%" in base:
            continue
        if _under(loc, base) and _norm(loc) != _norm(base):
            rel = os.path.relpath(str(loc), base).replace(os.sep, "/")
            return tok, rel
    if _too_broad(loc, expand):
        return None
    return LOC_PREFIX + program_name, ""


def guess_install_dir(program: Program, expand: Expand = expand_env) -> Path | None:
    """InstallLocation 이 없을 때 Program Files 아래에서 프로그램 이름/토큰과 폴더 이름이 정확히 같은 폴더(첫 번째)."""
    from .find_program import program_tokens
    want = {re.sub(r"[^0-9a-z가-힣]", "", t) for t, _s in program_tokens(program)}
    want.discard("")
    if not want:
        return None
    for tok in PF_TOKENS:
        base = expand(tok)
        if "%" in base or not os.path.isdir(base):
            continue
        try:
            with os.scandir(base) as it:
                names = sorted((e for e in it if e.is_dir(follow_symlinks=False) and not e.is_symlink()), key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in names:
            if e.name.lower() in PROTECTED_DIRS:
                continue
            if re.sub(r"[^0-9a-z가-힣]", "", e.name.lower()) in want:
                return Path(e.path)
    return None


def locate_install_dir(program: Program, expand: Expand = expand_env) -> tuple[Path, bool] | None:
    """(프로그램 설치 폴더, InstallLocation 에서 왔는지). 없으면 None. 폴더가 실제로 있어야 한다."""
    if program.install_location:
        p = Path(program.install_location.strip().strip('"'))
        if p.is_absolute() and p.is_dir() and not p.is_symlink():
            return p, True
    g = guess_install_dir(program, expand)
    return (g, False) if g is not None else None


# --------------------------------------------------------------------------- 내보내기 항목
@dataclass
class InstallItem:
    program: str
    root: str    # 루트 토큰
    folder: str  # 루트 기준 프로그램 폴더(posix, loc 루트면 빈 문자열)
    rel: str     # 루트 기준 파일 상대 경로(posix, folder 로 시작)
    path: Path   # 이 PC 의 실제 경로(표시/읽기용)
    size: int = 0


def build_candidate_entry(program: Program, file_path: str | Path, install_dir: Path, from_location: bool,
                          expand: Expand = expand_env) -> dict | None:
    """프로그램 설치 폴더 안의 파일 하나를 선택 파일 항목 모양(root/folder/rel)으로. 토큰으로 바꿀 수 없으면 None."""
    tk = tokenize_location(str(install_dir), program.name, expand)
    if tk is None:
        return None
    root, folder = tk
    try:
        sub = os.path.relpath(str(file_path), str(install_dir)).replace(os.sep, "/")
    except ValueError:
        return None
    if sub.startswith("..") or sub == ".":
        return None
    rel = f"{folder}/{sub}" if folder else sub
    return {"program": program.name, "root": root, "folder": folder, "rel": rel, "from_location": from_location}


def _safe_rel_parts(rel: str) -> PurePosixPath | None:
    from .package import PackageError, _safe_rel
    try:
        return _safe_rel(rel)
    except PackageError:
        return None


def install_items_from_selection(data: object, expand: Expand = expand_env, programs: list[Program] | None = None) -> list[InstallItem]:
    """선택 데이터에서 install_dir 목록의 include:true 항목만 구조 검증해 돌려준다(이전 버전 선택 파일은 이 키가 없어 빈 목록).

    여기서는 구조(루트 토큰/상대 경로/폴더 접두)만 본다. 확장자/크기/링크 같은 내용 판정은 내보낼 때(collect_install_items) 다시 한다.
    선택 파일의 값은 믿지 않는다: 실제 경로는 루트 토큰 + rel 로만 만든다."""
    from .discover import SelectionError
    if not isinstance(data, dict):
        raise SelectionError("선택 파일 형식이 올바르지 않습니다")
    lst = data.get(SELECTION_KEY)
    if lst is None:
        return []
    if not isinstance(lst, list):
        raise SelectionError(f"{SELECTION_KEY} 가 목록이 아닙니다")
    out: list[InstallItem] = []
    seen: set[tuple[str, str]] = set()
    for i, c in enumerate(lst):
        if not isinstance(c, dict):
            raise SelectionError(f"{SELECTION_KEY}[{i}] 가 객체가 아닙니다")
        if c.get("include") is not True:
            continue
        program, root, folder, rel = c.get("program"), c.get("root"), c.get("folder", ""), c.get("rel")
        if not (isinstance(program, str) and isinstance(root, str) and isinstance(folder, str) and isinstance(rel, str)):
            raise SelectionError(f"{SELECTION_KEY}[{i}] 의 program/root/folder/rel 이 문자열이 아닙니다")
        if parse_root(root) is None:
            raise SelectionError(f"허용되지 않은 설치 폴더 root: {root!r}")
        if _safe_rel_parts(rel) is None:
            raise SelectionError(f"안전하지 않은 설치 폴더 rel: {rel!r}")
        if folder and (_safe_rel_parts(folder) is None or not rel.startswith(folder + "/")):
            raise SelectionError(f"folder 가 rel 의 앞부분이 아닙니다: {folder!r}")
        if root.startswith(LOC_PREFIX) and (folder or root != LOC_PREFIX + program):
            raise SelectionError("설치 위치 기반 root 는 프로그램 이름과 같아야 하고 folder 가 비어 있어야 합니다")
        if root in PF_TOKENS and not folder:
            raise SelectionError("Program Files 바로 아래 파일은 선택할 수 없습니다(프로그램 폴더 안의 파일만)")
        base = resolve_root(root, programs, expand)
        if base is None:
            if root in PF_TOKENS:
                raise SelectionError(f"root 의 환경변수를 확장할 수 없습니다: {root}")
            base_path: Path | None = None  # 위치 기반 root 는 프로그램 목록이 있어야 풀린다(내보내기에서 다시 해석)
        else:
            base_path = base
        if (root, rel) in seen:
            continue
        seen.add((root, rel))
        shown = Path(str(c["path"])) if isinstance(c.get("path"), str) and base_path is None else None
        path = base_path.joinpath(*PurePosixPath(rel).parts) if base_path is not None else (shown or Path(rel))
        out.append(InstallItem(program, root, folder, rel, path))
    return out


def load_install_selection(path: Path, expand: Expand = expand_env, programs: list[Program] | None = None) -> list[InstallItem]:
    import json
    from .discover import SelectionError
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SelectionError(f"선택 파일을 읽을 수 없습니다: {e}") from e
    return install_items_from_selection(data, expand, programs)


def item_secret_hints(it: InstallItem, programs: list[Program] | None = None, expand: Expand = expand_env) -> list[str]:
    """내보내기 전 비밀 정보 점검(이름 + 내용). 파일을 찾지 못하면 이름만 본다."""
    p = _real_path(it, programs, expand)
    if p is not None and p.is_file():
        return secret_hints(p)
    return name_secret_hints(Path(it.rel).name)


def _real_path(it: InstallItem, programs: list[Program] | None, expand: Expand) -> Path | None:
    base = resolve_root(it.root, programs, expand)
    if base is None:
        return None
    return base.joinpath(*PurePosixPath(it.rel).parts)


def collect_install_items(items: list[InstallItem], programs: list[Program] | None, expand: Expand = expand_env, *,
                          avoid: Callable[[Path], bool] | None = None, failed: list[dict] | None = None,
                          cov: list[dict] | None = None, info: dict | None = None):
    """선택한 설치 폴더 설정 파일의 패키지 항목(package.Item)과 건너뛴 항목. 읽기 전용. 모든 조건을 여기서 다시 확인한다.

    제외(skipped, denied=True 는 '복원하지 않는 형식'): 링크/정션, 설정 허용 목록 밖 확장자, 거부 확장자, 라이선스/로그/캐시 이름, 상한 초과.
    info 가 있으면 manifest['install_dir'] 의 프로그램별 요약(program/version/root/folder/files/bytes)을 채운다."""
    from . import coverage, fs
    from .package import DENY_EXTENSIONS, Item, _denied_skip, _usable_base, restore_denied, unsafe_name
    pkg_items: list[Item] = []
    skipped: list[dict] = []
    progs: dict[tuple[str, str], dict] = {}
    total_files = total_bytes = 0
    for it in items:
        key = (it.root, it.folder or it.program)
        pr = progs.setdefault(key, {"program": it.program, "root": it.root, "folder": it.folder, "files": 0, "bytes": 0,
                                    "failed": 0, "excluded": 0, "found": False, "version": ""})
        prog = program_by_name(it.program, programs)
        if prog is not None and not pr["version"]:
            pr["version"] = prog.version

        def skip(why: str, path, denied: bool = False, bad: bool = True) -> None:
            skipped.append(_denied_skip(INSTALL_ID, path, why) if denied else {"rule": INSTALL_ID, "path": str(path), "reason": why})
            if bad:
                pr["excluded"] += 1

        base = resolve_root(it.root, programs, expand)
        if base is None or not _usable_base(base):
            skip("설치 폴더 위치를 이 PC 에서 알 수 없음(프로그램 설치 목록에 없거나 허용되지 않은 위치)", it.rel, bad=False)
            continue
        target = base.joinpath(*PurePosixPath(it.rel).parts)
        src = target
        shown = it.root + chr(92) + it.rel.replace("/", chr(92))  # 패키지 기록(skipped/failed)에는 환경 토큰 경로만 남긴다(사용자 이름 경로 없음)
        if avoid is not None and avoid(src):
            continue
        if not os.path.lexists(src):
            skip("경로가 없음", shown, bad=False)
            continue
        pr["found"] = True
        link = fs.reparse_in_chain(base, src)
        if link:
            skip("심볼릭 링크/정션은 따라가지 않음", shown)
            continue
        if not src.is_file():
            skip("파일이 아님(폴더는 담지 않음)", shown)
            continue
        if unsafe_name(it.rel):
            skip("Windows 에서 쓸 수 없는 이름(예약 이름, 끝의 점/공백 등)", shown)
            continue
        if it.folder and not (src.parent == base.joinpath(*PurePosixPath(it.folder).parts) or
                              _under(src.parent, base.joinpath(*PurePosixPath(it.folder).parts))):
            skip("프로그램 폴더 밖", shown, denied=True)
            continue
        if it.root in PF_TOKENS and (len(PurePosixPath(it.rel).parts) < 2 or PurePosixPath(it.rel).parts[0].lower() in PROTECTED_DIRS):
            skip("Windows 구성요소/공용 폴더이거나 프로그램 폴더 안의 파일이 아님", shown, denied=True)
            continue
        if sub_dir_skipped(it.rel, it.folder):
            skip("라이선스/로그/캐시 이름의 폴더 안 파일", shown, denied=True)
            continue
        depth = len(PurePosixPath(it.rel).parts) - (len(PurePosixPath(it.folder).parts) if it.folder else 0) - 1
        if depth > INSTALL_MAX_SUBDIR_DEPTH:
            skip(f"프로그램 폴더에서 {INSTALL_MAX_SUBDIR_DEPTH}단계보다 깊은 폴더의 파일", shown)
            continue
        ext = os.path.splitext(src.name)[1].lower()
        if ext in DENY_EXTENSIONS:
            skip(f"실행/스크립트/라이브러리 형식({ext})", shown, denied=True)
            continue
        if not is_settings_name(src.name):
            skip(f"설정 형식 허용 목록에 없는 파일({ext or '확장자 없음'}) 또는 라이선스/로그/캐시 이름", shown, denied=True)
            continue
        why = restore_denied(it.rel, target, None, it.root)
        if why:
            skip(why, shown, denied=True)
            continue
        try:
            size = os.stat(fs.long_path(src)).st_size
        except OSError as e:
            if failed is not None:
                from . import failure
                f = failure.classify(e, str(src))
                failed.append({"src": shown, "error": f"읽지 못함: {f.label}", "reason": f.code})
            pr["failed"] += 1
            continue
        if size > INSTALL_FILE_MAX_BYTES:
            skip(f"파일이 너무 큼({size // (1024 * 1024)}MB > {INSTALL_FILE_MAX_BYTES // (1024 * 1024)}MB 상한)", shown)
            continue
        if (pr["files"] >= INSTALL_PROGRAM_MAX_FILES or pr["bytes"] + size > INSTALL_PROGRAM_MAX_BYTES
                or total_files >= INSTALL_TOTAL_MAX_FILES or total_bytes + size > INSTALL_TOTAL_MAX_BYTES):
            skip("파일 수/총 용량 상한 초과(프로그램당 30개·20MB, 전체 300개·50MB)", shown)
            continue
        pr["files"] += 1
        pr["bytes"] += size
        total_files += 1
        total_bytes += size
        pkg_items.append(Item(f"{INSTALL_ARC}/{root_key(it.root)}/{it.rel}", src, INSTALL_ID, it.root, it.rel))
    if info is not None:
        info.update({"note": MANIFEST_NOTE, "version_warning": VERSION_WARNING,
                     "limits": {"file_bytes": INSTALL_FILE_MAX_BYTES, "program_bytes": INSTALL_PROGRAM_MAX_BYTES,
                                "total_bytes": INSTALL_TOTAL_MAX_BYTES, "program_files": INSTALL_PROGRAM_MAX_FILES,
                                "total_files": INSTALL_TOTAL_MAX_FILES},
                     "programs": [{k: v for k, v in p.items() if k in ("program", "version", "root", "folder")} | {"files": p["files"], "bytes": p["bytes"]}
                                  for p in progs.values()]})
    if cov is not None:
        for p in progs.values():
            where = f"{p['root']}\\{p['folder'].replace('/', os.sep)}" if p["folder"] else p["root"]
            cov.append({"rule": INSTALL_ID, "name": p["program"], "path": where, "description": coverage.INSTALL_DIR_WHAT,
                        "files": p["files"], "failed": p["failed"], "excluded": p["excluded"],
                        "status": coverage.classify(found=p["found"], files=p["files"], failed=p["failed"], excluded=p["excluded"])})
    return pkg_items, skipped


# --------------------------------------------------------------------------- 쓰기 권한 사전 점검
def is_admin() -> bool | None:
    """관리자 권한으로 실행 중인지(Windows). 알 수 없으면 None. 시험에서 교체한다."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return None


def dir_writable(path: Path, probe: bool = False, expand: Expand = expand_env) -> bool:
    """폴더에 파일을 만들 수 있는지.

    probe=True 면 아주 작은 임시 파일을 만들었다 바로 지운다(실제 복원 직전의 사전 점검). probe=False(미리보기)는 아무것도 쓰지 않고
    os.access 로 보며, 이 PC 의 실제 Program Files 아래에서 관리자 권한이 아니면 쓸 수 없다고 본다(표준 사용자는 쓸 수 없는 곳)."""
    p = Path(path)
    if not p.is_dir():
        return False
    if probe:
        probe_path = p / f".pcmigrate-write-test-{os.getpid()}-{os.urandom(4).hex()}"
        try:
            fd = os.open(str(probe_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return False
        try:
            os.close(fd)
        finally:
            try:
                os.unlink(str(probe_path))
            except OSError:
                pass
        return True
    if not os.access(str(p), os.W_OK):
        return False
    real = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemRoot")]
    if any(r and _under(p, r) for r in real) and is_admin() is False:
        return False
    return True


# --------------------------------------------------------------------------- 복원 계획
def _declared(manifest: dict) -> list[dict]:
    info = manifest.get(MANIFEST_KEY)
    lst = info.get("programs") if isinstance(info, dict) else None
    return [d for d in lst if isinstance(d, dict) and isinstance(d.get("program"), str) and isinstance(d.get("root"), str)
            and isinstance(d.get("folder", ""), str)] if isinstance(lst, list) else []


def _folder_matches_program(folder_leaf: str, prog: Program) -> bool:
    from .find_program import program_tokens
    leaf = re.sub(r"[^0-9a-z가-힣]", "", folder_leaf.lower())
    if not leaf:
        return False
    toks = {re.sub(r"[^0-9a-z가-힣]", "", t) for t, _s in program_tokens(prog)}
    toks.add(re.sub(r"[^0-9a-z가-힣]", "", prog.name.lower()))
    toks.discard("")
    return leaf in toks or any(len(t) >= 4 and t in leaf for t in toks)


def plan_install(manifest: dict, files: list[dict], expand: Expand, *, allow: bool, programs: list[Program] | None,
                 write_check: Callable[[Path], bool] | None, sha256_file: Callable[[Path], str]):
    """manifest 의 rule='install' 파일들의 복원 계획. (계획 항목 목록, 보기용 전체 목록, 프로그램 요약, 문제 목록).

    구조 위반(허용되지 않은 루트 등)은 problems(패키지 전체를 막음). 내용 위반(확장자, 선언 안 된 폴더, 다른 프로그램 폴더 등)은 항목별 denied.
    프로그램 폴더가 없으면 missing_dir(쓰지 않음). allow=False 면 계획 항목(items)은 비어 있고 보기용 목록만 채운다."""
    from . import failure, fs
    from .copier import DENIED, REJECTED, SKIPPED_SAME, WOULD_COPY, WOULD_OVERWRITE, _backup_path
    from .package import DENY_EXTENSIONS, _excluded, _usable_base, _within, restore_denied, RESTORE_EXCLUDE
    problems: list[str] = []
    view: list[dict] = []
    declared = _declared(manifest)
    seen: set[str] = set()
    by_prog: dict[str, dict] = {}

    def entry_for(f: dict, base_dir: str, target: Path, status: str, **kw) -> dict:
        d = {"entry": f["arc"], "target": str(target), "status": status, "backup": "", "size": f["size"], "sha256": f["sha256"],
             "extra": False, "install": True, "base_dir": base_dir, "program": kw.pop("program", ""), "note": ""}
        d.update(kw)
        if status in (DENIED, REJECTED):
            d.setdefault("reason", status)
        return d

    for f in files:
        root, rel = f["base"], f["rel"]
        parsed = parse_root(root)
        if parsed is None:
            problems.append(f"허용되지 않은 복원 루트(설치 폴더): {root}")
            continue
        parts = PurePosixPath(rel).parts
        if not parts or any(p in ("", ".", "..") for p in parts):  # verify_package 가 이미 걸렀지만 방어
            problems.append(f"설치 폴더 항목의 경로가 올바르지 않음: {rel}")
            continue
        decl = next((d for d in declared if d["root"] == root and (rel.startswith(d.get("folder", "") + "/") if d.get("folder") else True)), None)
        pname = (decl or {}).get("program") or (parsed[1] if parsed[0] == "loc" else "")
        prog = program_by_name(pname, programs) if pname else None
        base = resolve_root(root, programs, expand)
        shown_root = base if base is not None else Path(root)
        if base is None or not _usable_base(base):
            st = MISSING_PROGRAM if parsed[0] == "loc" else MISSING_DIR
            if parsed[0] == "pf":
                problems.append(f"설치 폴더 루트의 환경변수를 확장할 수 없음: {root}")
                continue
            view.append(entry_for(f, "", Path(rel), st, program=pname, note="이 PC 의 프로그램 목록에서 같은 이름의 프로그램과 설치 위치를 찾지 못함. " + INSTALL_FIRST_NOTE))
            continue
        target = base.joinpath(*parts)
        pr = by_prog.setdefault(pname or root, {"program": pname, "root": root, "folder": (decl or {}).get("folder", ""),
                                                "old_version": "", "new_version": prog.version if prog else "", "files": 0})
        if decl is not None and isinstance(decl.get("version"), str):
            pr["old_version"] = decl["version"]
        pr["files"] += 1

        def deny(why: str, program: str = pname) -> None:
            view.append(entry_for(f, str(base), target, DENIED, program=program, error=f"복원 안 함: {why}", note=why))

        if decl is None:
            deny("패키지의 설치 폴더 설정 목록에 없는 프로그램 폴더")
            continue
        if parsed[0] == "pf":
            first = parts[0].lower()
            if len(parts) < 2 or first in PROTECTED_DIRS:
                deny("Windows 구성요소/공용 폴더이거나 프로그램 폴더 안의 파일이 아님")
                continue
            if not decl.get("folder") or not rel.startswith(decl["folder"] + "/"):
                deny("선언된 프로그램 폴더 밖의 파일")
                continue
            if PurePosixPath(decl["folder"]).parts[0].lower() in PROTECTED_DIRS:
                deny("Windows 구성요소/공용 폴더")
                continue
        ext = os.path.splitext(parts[-1])[1].lower()
        if ext in DENY_EXTENSIONS:
            deny(f"실행/스크립트/라이브러리 형식({ext})은 설치 폴더에 복원하지 않음")
            continue
        if not is_settings_name(parts[-1]):
            deny(f"설정 형식 허용 목록에 없는 파일({ext or '확장자 없음'}) 또는 라이선스/로그/캐시 이름")
            continue
        if _excluded(target.name, None, RESTORE_EXCLUDE):
            deny("라이선스 파일은 복원하지 않음")
            continue
        why = restore_denied(rel, target, None, root)
        if why:
            deny(why)
            continue
        if sub_dir_skipped(rel, decl.get("folder", "")):
            deny("라이선스/로그/캐시 이름의 폴더 안 파일")
            continue
        depth = len(parts) - (len(PurePosixPath(decl["folder"]).parts) if decl.get("folder") else 0) - 1
        if depth > INSTALL_MAX_SUBDIR_DEPTH:
            deny("프로그램 폴더 안쪽으로 너무 깊은 위치")
            continue
        link = fs.reparse_in_chain(base, target)  # 링크/정션을 거치면 링크 너머에 쓰게 되므로 따라가지 않는다
        if link:
            view.append(entry_for(f, str(base), target, REJECTED, program=pname, reason=failure.LINK_IN_PATH,
                                  error=f"{failure.REASONS[failure.LINK_IN_PATH].text} ({link})"))
            continue
        if not _within(target, base):
            problems.append(f"복원 대상이 허용 루트 밖: {target}")
            continue
        key = os.path.normcase(str(target))
        if key in seen:
            problems.append(f"같은 대상으로 복원되는 중복 항목: {target}")
            continue
        seen.add(key)
        prog_dir = base.joinpath(*PurePosixPath(decl["folder"]).parts) if decl.get("folder") else base
        if programs is not None:  # 새 PC 에 같은 프로그램이 설치돼 있고, 이 폴더가 그 프로그램의 폴더여야 한다
            if prog is None:
                view.append(entry_for(f, str(base), target, MISSING_PROGRAM, program=pname,
                                      note="이 PC 의 프로그램 목록에 같은 이름의 프로그램이 없음. " + INSTALL_FIRST_NOTE))
                continue
            if parsed[0] == "pf":
                ok = _norm(prog_dir) == _norm(prog.install_location.strip().strip('"')) if prog.install_location \
                    else _folder_matches_program(prog_dir.name, prog)
                if not ok:
                    deny("이 폴더는 같은 이름 프로그램의 설치 폴더가 아님(다른 프로그램의 폴더일 수 있음)")
                    continue
        if not prog_dir.is_dir() or not target.parent.is_dir():
            view.append(entry_for(f, str(base), target, MISSING_DIR, program=pname,
                                  note=f"대상 폴더가 없음({fs.plain_path_text(str(target.parent))}). " + INSTALL_FIRST_NOTE))
            continue
        if target.is_dir():
            deny("같은 이름의 폴더가 있어 쓸 수 없음")
            continue
        backup = ""
        if target.is_file() and target.stat().st_size == f["size"] and sha256_file(target) == f["sha256"]:
            status = SKIPPED_SAME
        elif target.exists():
            status, backup = WOULD_OVERWRITE, str(_backup_path(target))
        else:
            status = WOULD_COPY
        view.append(entry_for(f, str(base), target, status, program=pname, backup=backup))
    items: list[dict] = []
    if allow:
        actionable = [v for v in view if v["status"] in (WOULD_COPY, WOULD_OVERWRITE)]
        if actionable and write_check is not None:
            dirs = {os.path.normcase(str(Path(v["target"]).parent)): Path(v["target"]).parent for v in actionable}
            if any(not write_check(d) for d in dirs.values()):
                problems.append(NEED_ADMIN_MESSAGE)
        if not problems:
            items = [v for v in view if v["status"] in (WOULD_COPY, WOULD_OVERWRITE, SKIPPED_SAME, DENIED, REJECTED)]
    return items, view, list(by_prog.values()), problems


def recheck_before_write(it: dict) -> str:
    """쓰기 직전 마지막 확인(계획 이후에 바뀌었을 수 있다). 문제가 있으면 사유, 없으면 빈 문자열."""
    from .package import DENY_EXTENSIONS
    target = Path(it["target"])
    ext = target.suffix.lower()
    if ext in DENY_EXTENSIONS or not is_settings_name(target.name):
        return "설치 폴더에는 설정 형식 파일만 복원합니다"
    if not target.parent.is_dir():
        return "대상 폴더가 없습니다. " + INSTALL_FIRST_NOTE
    if target.is_dir():
        return "같은 이름의 폴더가 있습니다"
    return ""
