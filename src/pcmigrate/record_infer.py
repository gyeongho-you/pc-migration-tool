"""설정 기록 모드: 설치 프로그램 정보(이름, 게시자, 설치 위치, 실행 파일 정보)로 '훑을 폴더'를 추정한다.

사용자가 폴더 이름 낱말을 직접 입력하지 않아도 되게, 아래 근거를 모아 AppData/ProgramData/문서/홈 점 폴더에서 폴더를 찾는다.
  (a) 표시 이름: 버전·연도 숫자와 제조사 낱말을 뗀 이름이 폴더 이름과 같을 때(제조사 낱말은 단독 매칭 금지)
  (b) 게시자(Publisher): 회사 접미사(Inc., Co., Ltd., 주식회사 ...)를 뗀 이름의 '제조사 폴더' 아래에서 프로그램 폴더를 찾는다(제조사 폴더 통째 수집 금지)
  (c) 설치 위치(InstallLocation/DisplayIcon/UninstallString): 마지막 폴더 이름=제품, 그 상위=제조사(Program Files/사용자 폴더 이름은 쓰지 않음)
  (d) 실행 파일 버전 정보(CompanyName/ProductName/FileDescription, 읽지 못하면 무시)
  (e) 제품 이름에서 버전을 뗀 기본 이름과 '이름 2025' 처럼 연도/버전이 붙은 폴더
오탐 방지: 비교는 '정규화한 이름이 같다' 로만 한다(부분 포함 아님). 그래서 'Photoshop' 은 'Adobe Photoshop Elements' 폴더에 걸리지 않는다.
이름의 앞부분만 맞는 폴더와 제조사 폴더 전체는 기본 대상이 아니라 '제외 후보'로만 알리고, 사용자가 직접 추가할 수 있다.
읽기 전용: 폴더 이름만 보고 파일 내용은 열지 않는다. 결과의 경로는 환경변수 표기라 사용자 이름이 없다.
"""
from __future__ import annotations

import ctypes
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Callable

from . import discover_data, errorlog, fs
from .record_snapshot import NOISE_TOP, VENDOR_WORDS, Root, default_roots, standard_token

HIGH, MID, LOW = "높음", "중간", "낮음"
COMPANY_SUFFIXES = frozenset({"inc", "incorporated", "corp", "corporation", "co", "ltd", "limited", "llc", "gmbh", "srl", "sa", "ag", "bv", "plc",
                              "company", "kk", "pte", "oy", "ab", "nv", "sas", "spa", "pty", "the", "주식회사", "유한회사", "유한책임회사"})
GENERIC_DIRS = frozenset({"program files", "program files (x86)", "programs", "programdata", "appdata", "local", "roaming", "windowsapps",
                          "common files", "users", "apps", "bin", "app", "win64", "x64", "x86", "application", "windows", "system32"})
LANG_TOKENS = frozenset({"english", "korean", "한국어", "language", "pack"})
_VERSION_TOKEN = re.compile(r"^(?:v?\d+|x64|x86|64bit|32bit|win64|win32)$")
_TOKEN = re.compile(r"[0-9a-z가-힣]+")
VERSION_KEYS = ("CompanyName", "ProductName", "FileDescription")


@dataclass
class ProgramInfo:
    """폴더 추정에 쓰는 입력. detect.Program 에서 만들거나 이름만으로 만든다(나머지는 선택)."""
    name: str
    version: str = ""
    publisher: str = ""
    install_location: str = ""
    display_icon: str = ""
    uninstall_string: str = ""
    exe_path: str = ""

    @staticmethod
    def from_program(p, exe_path: str = "") -> "ProgramInfo":
        g = lambda n: str(getattr(p, n, "") or "")  # noqa: E731 - 선택 필드가 없는 옛 Program 도 받는다
        return ProgramInfo(g("name"), g("version"), g("publisher"), g("install_location"), g("display_icon"), g("uninstall_string"), exe_path)

    def as_state(self) -> dict:
        """상태 파일에 저장할 모양: 경로는 환경변수 표기로 바꿔 사용자 이름이 남지 않게 한다."""
        tok = lambda s: standard_token(fs.plain_path_text(s)) if s else ""  # noqa: E731
        return {"name": self.name, "version": self.version, "publisher": self.publisher, "install_location": tok(self.install_location),
                "exe_path": tok(self.exe_path or _exe_from(self.display_icon) or "")}

    @staticmethod
    def from_state(d: dict) -> "ProgramInfo":
        from .envpaths import expand_env
        return ProgramInfo(d.get("name", ""), d.get("version", ""), d.get("publisher", ""),
                           expand_env(d.get("install_location", "")), exe_path=expand_env(d.get("exe_path", "")))


@dataclass
class Folder:
    token: str  # 환경변수 표기
    path: str
    reason: str
    confidence: str
    source: str = "inferred"  # inferred | added
    enabled: bool = True

    def as_dict(self) -> dict:
        return {"path": self.token, "reason": self.reason, "confidence": self.confidence, "source": self.source, "enabled": self.enabled}


@dataclass
class Inference:
    info: ProgramInfo
    folders: list = field(default_factory=list)
    excluded: list = field(default_factory=list)  # [(token, 이유)] 기본 대상이 아닌 후보
    notes: list = field(default_factory=list)
    evidence: list = field(default_factory=list)  # 사용한 근거 설명

    def enabled(self) -> list:
        return [f for f in self.folders if f.enabled]

    def as_dict(self) -> dict:
        return {"program": self.info.name, "folders": [f.as_dict() for f in self.folders],
                "excluded": [{"path": t, "reason": r} for t, r in self.excluded], "notes": self.notes, "evidence": self.evidence}


# ----------------------------------------------------------------------------- 이름 정규화
def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def vendor_tokens(text: str) -> list[str]:
    """회사 이름에서 접미사(Inc., Co., Ltd., 주식회사 ...)를 뗀 낱말."""
    return [t for t in tokens(text) if t not in COMPANY_SUFFIXES]


def product_key(text: str, strip: frozenset[str] | set[str]) -> str:
    """버전/연도 숫자, 언어 낱말, 제조사 낱말(strip)을 떼고 이어 붙인 비교 키. 예: 'Adobe Photoshop 2025' -> 'photoshop'."""
    return "".join(t for t in tokens(text) if not _VERSION_TOKEN.match(t) and t not in strip and t not in LANG_TOKENS)


def compact(text: str) -> str:
    return "".join(vendor_tokens(text))


def _exe_from(text: str) -> str:
    """DisplayIcon/UninstallString 같은 값에서 파일 경로만(따옴표, ',0' 아이콘 번호, 인수 제거)."""
    s = (text or "").strip()
    if not s:
        return ""
    if s.startswith('"'):
        s = s[1:].split('"', 1)[0]
    else:
        m = re.match(r"(.+?\.(?:exe|ico|dll))(?=[,\s]|$)", s, re.I)
        s = m.group(1) if m else s
    return re.sub(r",\s*-?\d+$", "", s).strip()


def path_names(path: str) -> tuple[str, str]:
    """설치 위치에서 (제품 폴더 이름, 제조사 폴더 이름). Program Files 같은 일반 이름과 사용자 이름 부분은 쓰지 않는다."""
    parts = [p for p in re.split(r"[\\/]+", fs.plain_path_text(path or "")) if p and not re.fullmatch(r"[A-Za-z]:", p)]
    low = [p.lower() for p in parts]
    if "users" in low:  # C:\Users\<이름>\... : 이름 부분은 버린다
        parts = parts[low.index("users") + 2:]
    elif parts and parts[0].startswith("%"):
        parts = parts[1:]
    parts = [p for p in parts if p.lower() not in GENERIC_DIRS and not re.fullmatch(r"\d+(\.\d+)*", p)]
    if parts and re.search(r"\.[A-Za-z0-9]{2,4}$", parts[-1]):  # 마지막이 파일 이름이면 폴더만
        parts = parts[:-1]
    if not parts:
        return "", ""
    return parts[-1], (parts[-2] if len(parts) >= 2 else "")


# ----------------------------------------------------------------------------- 실행 파일 정보(ctypes, 주입 가능)
def read_version_info(path: str) -> dict:
    """exe 의 버전 리소스(CompanyName/ProductName/FileDescription). 읽지 못하면 빈 dict(예외를 던지지 않는다)."""
    if os.name != "nt" or not path or not os.path.isfile(fs.long_path(path)):
        return {}
    try:
        from ctypes import wintypes
        ver = ctypes.WinDLL("version", use_last_error=True)
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        ver.VerQueryValueW.restype = wintypes.BOOL
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return {}
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return {}
        lp, n = ctypes.c_void_p(), wintypes.UINT()
        langs = []
        if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(lp), ctypes.byref(n)) and n.value >= 4:
            lang, cp = struct.unpack("<HH", ctypes.string_at(lp.value, 4))
            langs.append(f"{lang:04x}{cp:04x}")
        langs += ["040904b0", "040904e4", "041203b5"]
        out = {}
        for key in VERSION_KEYS:
            for lc in langs:
                if ver.VerQueryValueW(buf, f"\\StringFileInfo\\{lc}\\{key}", ctypes.byref(lp), ctypes.byref(n)) and n.value:
                    val = ctypes.wstring_at(lp.value, n.value).strip("\x00 ").strip()
                    if val:
                        out[key] = val
                        break
        return out
    except Exception as e:  # noqa: BLE001 - 정보를 못 읽어도 다른 근거로 계속한다
        errorlog.record(e, "설정 기록: 실행 파일 정보")
        return {}


# ----------------------------------------------------------------------------- 폴더 추정
@dataclass
class _Hit:
    path: str
    root: Root
    reasons: list = field(default_factory=list)
    strength: int = 0
    sources: set = field(default_factory=set)


def _skip_top(name: str) -> bool:
    low = name.lower()
    return low in NOISE_TOP or any(s in low for s in ("cache", "crashpad", "telemetry"))


def _dirs(path: str) -> list[os.DirEntry]:
    try:
        with os.scandir(fs.long_path(path)) as it:
            return sorted((e for e in it if e.is_dir(follow_symlinks=False) and not e.is_symlink()), key=lambda e: e.name.lower())
    except OSError:
        return []


def infer_folders(info: ProgramInfo, roots: list[Root] | None = None, documents_fn: Callable | None = None,
                  version_info_fn: Callable[[str], dict] | None = None, extra_words: list[str] | None = None) -> Inference:
    """훑을 폴더를 추정한다(읽기 전용: 폴더 이름만 본다)."""
    inf = Inference(info)
    if roots is None:
        roots, notes = default_roots(documents_fn)
        inf.notes += notes
    vinfo_fn = version_info_fn or read_version_info
    # ---- 근거 모으기: 제품 키(키 -> [(근거, 설명)]) 와 제조사 이름
    exe = info.exe_path or _exe_from(info.display_icon)
    vinfo: dict = {}
    if exe and exe.lower().endswith(".exe"):
        try:
            vinfo = vinfo_fn(exe) or {}
        except Exception as e:  # noqa: BLE001
            errorlog.record(e, "설정 기록: 실행 파일 정보")
    inst = info.install_location or os.path.dirname(exe) or os.path.dirname(_exe_from(info.uninstall_string))
    inst_product, inst_vendor = path_names(inst)
    vendors: dict[str, str] = {}  # 정규화한 제조사 이름 -> 근거 설명
    if info.publisher:
        vendors[compact(info.publisher)] = f"게시자({info.publisher.strip()})"
    if vinfo.get("CompanyName"):
        vendors.setdefault(compact(vinfo["CompanyName"]), f"실행 파일 정보의 회사({vinfo['CompanyName']})")
    if inst_vendor:
        vendors.setdefault(compact(inst_vendor), f"설치 위치의 제조사 폴더({inst_vendor})")
    vendors.pop("", None)
    strip = set(VENDOR_WORDS)
    for v in vendors:
        strip.add(v)
    for src in (info.publisher, vinfo.get("CompanyName", ""), inst_vendor):
        strip.update(vendor_tokens(src))
    strip = {t for t in strip if t}
    keys: dict[str, list[tuple[str, str]]] = {}  # 키 -> [(근거 코드, 문구)] (같은 키를 여러 근거가 가리키면 확신도가 올라간다)
    cands = [(info.name, "name", "프로그램 이름과 일치(버전·연도·제조사 이름 제외)"),
             (inst_product, "install", f"설치 위치의 폴더 이름({inst_product})과 일치"),
             (vinfo.get("ProductName", ""), "exe", "실행 파일 정보(제품 이름)와 일치"),
             (vinfo.get("FileDescription", ""), "exe", "실행 파일 정보(설명)와 일치")]
    for text, code, why in cands:
        k = product_key(text or "", strip)
        if len(k) >= 3 and (code, why) not in keys.get(k, []):
            keys.setdefault(k, []).append((code, why))
    name_compact = "".join(tokens(info.name))
    if info.publisher:
        inf.evidence.append("게시자 이름")
    if inst_product:
        inf.evidence.append("설치 위치 폴더 이름")
    if vinfo:
        inf.evidence.append("실행 파일 버전 정보")
    inf.evidence.insert(0, "프로그램 이름")
    if not keys and not extra_words:
        inf.notes.append("프로그램 이름에서 폴더 이름으로 쓸 만한 낱말을 얻지 못했습니다")
        return inf
    words = [w.strip().lower() for w in (extra_words or []) if w.strip()]
    hits: dict[str, _Hit] = {}
    vendor_dirs: dict[str, tuple[Root, str, str]] = {}  # 토큰 -> (루트, 경로, 근거) 제조사 폴더 중 아직 프로그램 폴더를 못 찾은 것

    def add(root: Root, path: str, name_chain: str, why: str, strength: int, code: str) -> None:
        tok = root.token + "\\" + name_chain
        h = hits.setdefault(tok, _Hit(path, root))
        if why not in h.reasons:
            h.reasons.append(why)
        h.strength = max(h.strength, strength)
        h.sources.add(code)

    for root in roots:
        for e in _dirs(root.path):
            name = e.name
            if root.dotfiles_only:
                if not name.startswith("."):
                    continue
                key_name = name.lstrip(".")
            else:
                key_name = name
            if _skip_top(name):
                continue
            fk = product_key(key_name, strip)
            if fk in keys:
                exact = "".join(tokens(key_name)) == name_compact
                for code, why in keys[fk]:
                    add(root, e.path, name, "프로그램 이름과 폴더 이름이 일치" if code == "name" and exact else why, 3 if code == "name" else 2, code)
                continue
            if words and any(w in name.lower() or w in name.lower().replace(" ", "") for w in words):
                add(root, e.path, name, "직접 지정한 낱말과 일치", 2, "word")
                continue
            fc = compact(key_name)
            evidence_vendor = vendors.get(fc) or (f"제조사 이름({key_name})" if fk == "" and fc in set(tokens(info.name)) else "")
            vwhy = evidence_vendor or (f"제조사 폴더({key_name})" if fk == "" and fc in VENDOR_WORDS else "")
            if vwhy and not root.dotfiles_only:
                found = False
                for kid in _dirs(e.path):
                    kk = product_key(kid.name, strip)
                    if kk in keys:
                        found = True
                        for code, why in keys[kk]:
                            add(root, kid.path, name + "\\" + kid.name, vwhy + " 폴더 아래의 " + why, 3, "vendor-" + code)
                    elif len(product_key(kid.name, strip)) >= 4 and any(k.startswith(product_key(kid.name, strip)) and k != product_key(kid.name, strip) for k in keys):
                        inf.excluded.append((root.token + "\\" + name + "\\" + kid.name, "프로그램 이름의 앞부분만 일치해 제외했습니다(다른 프로그램일 수 있음). 맞으면 직접 추가하세요"))
                    elif words and any(w in kid.name.lower() for w in words):
                        found = True
                        add(root, kid.path, name + "\\" + kid.name, vwhy + " 폴더 아래의 직접 지정한 낱말과 일치", 2, "word")
                if not found and evidence_vendor:
                    vendor_dirs[root.token + "\\" + name] = (root, e.path, vwhy)
                continue
            # 이름의 앞부분만 맞는 폴더: 오탐 위험이라 기본 대상에서 뺀다
            if len(fk) >= 4 and any(k.startswith(fk) and k != fk for k in keys):
                inf.excluded.append((root.token + "\\" + name, "프로그램 이름의 앞부분만 일치해 제외했습니다(다른 프로그램일 수 있음). 맞으면 직접 추가하세요"))
    for tok, (root, path, why) in vendor_dirs.items():
        inf.excluded.append((tok, f"{why} 폴더 전체입니다. 안에 이 프로그램 폴더가 없고 다른 프로그램 설정이 섞일 수 있어 제외했습니다"))
    for tok, h in sorted(hits.items(), key=lambda kv: kv[0].lower()):
        conf = HIGH if (h.strength >= 3 or len(h.sources) >= 2) else MID
        inf.folders.append(Folder(tok, h.path, "; ".join(h.reasons), conf))
    if not inf.folders:
        inf.notes.append("이 프로그램의 설정 폴더를 찾지 못했습니다(제조사/폴더 이름이 프로그램 이름과 전혀 다르면 찾을 수 없습니다). "
                         "폴더를 직접 추가하거나 전체 훑기(--all-roots)를 선택하세요. 전체 훑기는 자동으로 하지 않습니다")
    return inf


# ----------------------------------------------------------------------------- 사용자 조정(훑기 시작 전)
def add_roots(plan: Inference, paths: list[str]) -> Inference:
    """사용자가 고른 폴더를 계획에 더한다(폴더 전체를 훑는다). 없는 폴더는 RecordError 대신 ValueError."""
    for p in paths:
        ap = fs.plain_path_text(os.path.abspath(p))
        if not os.path.isdir(fs.long_path(ap)):
            raise ValueError(f"폴더를 찾지 못했습니다: {errorlog.tokenize_home(ap)}")
        tok = standard_token(ap)
        if any(f.token.lower() == tok.lower() for f in plan.folders):
            for f in plan.folders:
                if f.token.lower() == tok.lower():
                    f.enabled = True
            continue
        plan.folders.append(Folder(tok, ap, "직접 추가한 폴더", HIGH, source="added"))
        plan.excluded = [x for x in plan.excluded if x[0].lower() != tok.lower()]
    return plan


def exclude_roots(plan: Inference, tokens_: list[str]) -> Inference:
    """계획에서 폴더를 뺀다(환경변수 표기 경로로 지정). 없는 항목은 무시."""
    want = {t.lower() for t in tokens_}
    for f in plan.folders:
        if f.token.lower() in want:
            f.enabled = False
    return plan


def render_inference(inf: Inference) -> list[str]:
    lines = [f"[{inf.info.name}] 훑을 폴더 추정 (근거: {', '.join(inf.evidence)})"]
    if not inf.folders:
        lines.append("  찾은 폴더가 없습니다.")
    for f in inf.folders:
        lines.append(f"  {'[훑음]' if f.enabled else '[제외]'} {f.confidence}  {f.token} — {f.reason}")
    for tok, why in inf.excluded:
        lines.append(f"  [제외 후보] {tok} — {why}")
    lines += [f"  안내: {n}" for n in inf.notes]
    return lines


def installed_match(programs: list, query: str) -> list:
    """설치 프로그램 목록에서 이름으로 고른다: 이름이 정확히 같으면 그것만, 아니면 부분 일치(대소문자 무시) 전부."""
    q = query.strip().lower()
    exact = [p for p in programs if p.name.strip().lower() == q]
    if exact:
        return exact[:1] if len({(p.name, p.version) for p in exact}) == 1 else exact
    return [p for p in programs if q and q in p.name.lower()]
