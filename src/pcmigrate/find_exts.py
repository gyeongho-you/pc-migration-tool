r"""'내 파일 찾기'가 가져올 파일 형식(확장자)을 사용자가 더하고 빼는 설정.

- 저장 위치: %LOCALAPPDATA%\pcmigrate\settings\find-exts.json (envpaths.expand_env 로 해석, 해석 못 하면 %TEMP%\pcmigrate-settings).
  기본 표(find_data.EXT_TABLE)와의 '차이'만 저장한다: {"version":1,"added":{".zep":"design"},"removed":[".svg"]}.
  확장자 문자열과 분류 이름만 들어 있다(경로·개인정보 없음). 설정 기록 모드나 규칙 복원 허용 확장자(allow_extensions)와는 무관하다.
- 설정이 없거나 손상돼도 찾기는 기본값으로 계속 동작한다. 손상은 오류 로그에 남기고, 원본 파일은 건드리지 않은 채 번호 .bak 으로 복사해 둔다.
- 읽기 보호: 파일 1MB 상한, 항목(추가+삭제) 500개 상한. 상한을 넘거나 잘못된 항목은 무시하고 경고(notes)로 알린다.
- 사용자가 추가한 확장자의 등급은 B(파일 한 개만으로도 후보가 되는 확실한 형식)로 다룬다.
- 화면(GUI)도 같은 함수를 부른다: load/describe/add/remove/reset/save.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import errorlog, find_data as fd
from .envpaths import expand_env

CONFIG_VERSION = 1
SETTINGS_DIR = "%LOCALAPPDATA%\\pcmigrate\\settings"  # 시험이 실제 폴더를 건드리지 않도록 바꿔 끼울 수 있다
FALLBACK_DIR = "pcmigrate-settings"
FILE_NAME = "find-exts.json"
MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 500
MAX_EXT_LEN = 16  # 점을 포함한 길이
USER_GRADE = "B"
USER_LABEL = "사용자 추가"

EXT_CATEGORIES: tuple[str, ...] = ("design", "photo", "video", "doc", "archive")
CATEGORY_LABELS = {c: fd.CATEGORY_LABELS[c] for c in EXT_CATEGORIES}
_LABEL_TO_CATEGORY = {v: k for k, v in CATEGORY_LABELS.items()}

# 프로그램/설치/시스템 성격 확장자(추가하려 하면 경고만 하고 막지는 않는다)
PROGRAM_EXT = frozenset({".exe", ".dll", ".msi", ".msu", ".sys", ".drv", ".ocx", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1",
                         ".vbs", ".vbe", ".wsf", ".lnk", ".reg", ".cpl", ".cab"})
PROGRAM_WARNING = "프로그램 파일이라 옮길 필요가 없을 수 있습니다(새 PC 에서는 프로그램을 다시 설치하는 편이 안전합니다). 막지는 않습니다."
SYSTEM_WARNING = "임시·시스템·로그 성격의 파일이라 옮길 필요가 없을 수 있습니다. 막지는 않습니다."

_EXT_RE = re.compile(r"^\.[a-z0-9_-]+$")


class FindExtsError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 쉬운 오류(입력 오류, 저장 실패 등)."""


# --------------------------------------------------------------------------- 입력 정리
def normalize_ext(text: str) -> str:
    """'ZEP', ' .Zep ', 'zep' -> '.zep'. 쓸 수 없는 값은 FindExtsError(ValueError)."""
    if not isinstance(text, str):
        raise FindExtsError("확장자를 글자로 입력하세요(예: .zep)")
    s = text.strip()
    if not s:
        raise FindExtsError("확장자가 비어 있습니다. 예: .zep")
    if s.strip(".") == "":
        raise FindExtsError("점만으로는 확장자가 아닙니다. 예: .zep")
    if any(ch in s for ch in "\\/:"):
        raise FindExtsError("폴더 경로가 아니라 확장자만 입력하세요. 예: .zep")
    if any(ch in s for ch in "*?"):
        raise FindExtsError("* 나 ? 같은 와일드카드는 쓸 수 없습니다. 확장자만 입력하세요. 예: .zep")
    if any(ch.isspace() for ch in s):
        raise FindExtsError("확장자 안에 공백이 있습니다. 한 번에 하나씩, 공백 없이 입력하세요. 예: .zep")
    body = s[1:] if s.startswith(".") else s
    if "." in body:
        raise FindExtsError("점이 두 개 이상입니다. 마지막 부분만 입력하세요(예: .tar.gz 는 .gz).")
    ext = "." + body.lower()
    if len(ext) > MAX_EXT_LEN:
        raise FindExtsError(f"확장자가 너무 깁니다(점 포함 {MAX_EXT_LEN}자까지).")
    if not _EXT_RE.match(ext):
        raise FindExtsError("확장자에는 영문 소문자·숫자·밑줄(_)·붙임표(-)만 쓸 수 있습니다. 예: .zep")
    return ext


def normalize_category(text: str) -> str:
    """'design' 또는 '설계' -> 'design'. 설계/사진/영상/문서/압축 중 하나여야 한다."""
    t = (text or "").strip().lower() if isinstance(text, str) else ""
    if t in EXT_CATEGORIES:
        return t
    if t in _LABEL_TO_CATEGORY:
        return _LABEL_TO_CATEGORY[t]
    raise FindExtsError("분류는 설계(design), 사진(photo), 영상(video), 문서(doc), 압축(archive) 중에서 고르세요.")


def warnings_for(ext: str) -> list[str]:
    """추가하려는 확장자가 실행/설치/시스템 성격이면 경고 문구 목록(막지는 않는다). 형식이 틀리면 빈 목록."""
    try:
        e = normalize_ext(ext)
    except ValueError:
        return []
    out = []
    if e in PROGRAM_EXT:
        out.append(PROGRAM_WARNING)
    elif e in fd.OTHER_EXCLUDED_EXT:
        out.append(SYSTEM_WARNING)
    return out


# --------------------------------------------------------------------------- 설정 객체
@dataclass
class ExtConfig:
    added: dict[str, str] = field(default_factory=dict)   # 확장자 -> 분류(사용자가 추가)
    removed: list[str] = field(default_factory=list)       # 삭제한 기본 확장자
    notes: list[str] = field(default_factory=list)         # 읽을 때 생긴 경고(저장하지 않음)
    damaged: bool = False                                  # 설정 파일을 못 읽어 기본값으로 동작 중

    def copy(self) -> "ExtConfig":
        return ExtConfig(dict(self.added), list(self.removed), list(self.notes), self.damaged)

    @property
    def is_default(self) -> bool:
        return not self.added and not self.removed

    def to_json_dict(self) -> dict:
        return {"version": CONFIG_VERSION, "added": dict(sorted(self.added.items())), "removed": sorted(self.removed)}


@dataclass
class Change:
    cfg: ExtConfig
    changed: bool
    message: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ResetResult:
    ok: bool
    changed: bool
    message: str
    backup: str = ""


# --------------------------------------------------------------------------- 경로
def settings_dir(directory: str | os.PathLike | None = None) -> Path:
    if directory is not None:
        return Path(directory)
    primary = expand_env(SETTINGS_DIR)
    if "%" not in primary and os.path.isabs(primary):
        return Path(primary)
    return Path(tempfile.gettempdir()) / FALLBACK_DIR


def config_path(directory: str | os.PathLike | None = None) -> Path:
    return settings_dir(directory) / FILE_NAME


def _numbered_bak(path: Path) -> Path:
    cand = path.with_name(path.name + ".bak")
    n = 1
    while os.path.lexists(cand):
        cand = path.with_name(f"{path.name}.bak.{n}")
        n += 1
    return cand


def _log(msg: str, context: str, exc: BaseException | None = None) -> None:
    errorlog.record(exc if exc is not None else ValueError(msg), f"찾을 파일 형식 설정: {context}")


def _same_as_existing_bak(path: Path, data: bytes) -> bool:
    for i in range(0, 60):
        b = path.with_name(path.name + (".bak" if i == 0 else f".bak.{i}"))
        try:
            if b.is_file() and b.stat().st_size == len(data) and b.read_bytes() == data:
                return True
        except OSError:
            continue
    return False


def _keep_damaged_copy(path: Path, data: bytes, cfg: ExtConfig) -> None:
    """손상된 원본은 그대로 두고, 같은 내용의 .bak 이 없을 때만 번호 .bak 으로 복사해 둔다."""
    try:
        if _same_as_existing_bak(path, data):
            return
        bak = _numbered_bak(path)
        bak.write_bytes(data)
        cfg.notes.append(f"손상된 설정 파일은 그대로 두고 복사본을 {errorlog.tokenize_home(str(bak))} 에 보관했습니다.")
    except OSError as e:
        _log(str(e), "손상 파일 보관", e)
        cfg.notes.append("손상된 설정 파일의 복사본을 만들지 못했습니다(원본은 그대로 있습니다).")


def _damaged(cfg: ExtConfig, why: str) -> ExtConfig:
    cfg.damaged = True
    cfg.notes.insert(0, f"찾을 파일 형식 설정을 읽지 못해 기본값으로 찾습니다({why}).")
    return cfg


# --------------------------------------------------------------------------- 읽기
def load(directory: str | os.PathLike | None = None) -> ExtConfig:
    """사용자 설정을 읽는다. 없거나 손상됐으면 기본값 상태를 돌려준다(예외를 던지지 않는다). 경고는 cfg.notes."""
    cfg = ExtConfig()
    path = config_path(directory)
    try:
        if not path.exists():
            return cfg
        size = path.stat().st_size
        if size > MAX_BYTES:
            _log(f"설정 파일이 {size:,}바이트로 상한({MAX_BYTES:,})을 넘음", "읽기")
            return _damaged(cfg, "파일이 너무 큽니다")
        data = path.read_bytes()
    except OSError as e:
        _log(str(e), "읽기", e)
        return _damaged(cfg, "파일을 열 수 없습니다")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError) as e:  # UnicodeDecodeError 는 ValueError
        _log(str(e), "읽기(JSON 오류)", e)
        _damaged(cfg, "JSON 형식이 아닙니다")
        _keep_damaged_copy(path, data, cfg)
        return cfg
    if not isinstance(raw, dict) or raw.get("version") != CONFIG_VERSION:
        _log("형식 또는 버전이 맞지 않음", "읽기")
        _damaged(cfg, "형식이나 버전이 맞지 않습니다")
        _keep_damaged_copy(path, data, cfg)
        return cfg
    _parse_entries(raw, cfg)
    return cfg


def _parse_entries(raw: dict, cfg: ExtConfig) -> None:
    base = fd.EXT_TABLE
    r_added, r_removed = raw.get("added", {}), raw.get("removed", [])
    bad: list[str] = []
    if not isinstance(r_added, dict):
        bad.append("added")
        r_added = {}
    if not isinstance(r_removed, list):
        bad.append("removed")
        r_removed = []
    over = max(0, len(r_added) + len(r_removed) - MAX_ENTRIES)
    budget = MAX_ENTRIES
    removed: list[str] = []
    for item in r_removed[:budget]:
        try:
            e = normalize_ext(item)
        except ValueError:
            bad.append(str(item)[:20])
            continue
        if e not in base:
            bad.append(e)
        elif e not in removed:
            removed.append(e)
    budget -= min(budget, len(r_removed))
    added: dict[str, str] = {}
    for k, v in list(r_added.items())[:budget]:
        try:
            e = normalize_ext(k)
        except ValueError:
            bad.append(str(k)[:20])
            continue
        if v not in EXT_CATEGORIES:
            bad.append(e)
        elif e in base and e not in removed:
            bad.append(e)  # 이미 기본 형식: 중복
        else:
            added[e] = v
    cfg.added, cfg.removed = added, removed
    if over:
        cfg.notes.append(f"설정 항목이 너무 많아(최대 {MAX_ENTRIES}개) 앞쪽만 읽고 {over}개는 무시했습니다.")
        _log(f"항목 {over}개가 상한을 넘어 무시됨", "읽기")
    if bad:
        shown = ", ".join(repr(b) for b in bad[:3])
        msg = f"설정 파일의 잘못된 항목 {len(bad)}개를 무시했습니다(예: {shown})."
        cfg.notes.append(msg)
        _log(msg, "읽기(잘못된 항목)")


# --------------------------------------------------------------------------- 유효 표 / 화면용 구조
def effective_table(cfg: ExtConfig | None = None) -> dict[str, tuple[str, str]]:
    """기본 표 + 추가 - 삭제: 확장자 -> (분류, 등급). 추가한 확장자의 등급은 B. 항상 새 dict 를 돌려준다."""
    table = dict(fd.EXT_TABLE)
    if cfg is None:
        return table
    for e in cfg.removed:
        if e in fd.EXT_TABLE:
            table.pop(e, None)
    for e, c in cfg.added.items():
        table[e] = (c, USER_GRADE)
    return table


def user_exts(cfg: ExtConfig | None) -> frozenset[str]:
    """유효 표에서 '사용자 추가'로 표시할 확장자."""
    return frozenset(cfg.added) if cfg else frozenset()


def describe(cfg: ExtConfig) -> dict:
    """화면/명령줄 표시용: 분류별 확장자(origin: default|user)와 삭제한 기본 확장자."""
    table = effective_table(cfg)
    users = user_exts(cfg)
    cats = []
    for c in EXT_CATEGORIES:
        exts = [{"ext": e, "grade": g, "origin": "user" if e in users else "default"}
                for e, (cc, g) in table.items() if cc == c and e not in users]
        exts += [{"ext": e, "grade": USER_GRADE, "origin": "user"} for e in sorted(users) if table[e][0] == c]
        cats.append({"category": c, "label": CATEGORY_LABELS[c], "count": len(exts), "exts": exts})
    removed = [{"ext": e, "category": fd.EXT_TABLE[e][0], "label": CATEGORY_LABELS[fd.EXT_TABLE[e][0]]}
               for e in sorted(cfg.removed) if e in fd.EXT_TABLE]
    return {"categories": cats, "removed": removed, "added_count": len(cfg.added), "removed_count": len(removed),
            "total": len(table), "is_default": cfg.is_default, "damaged": cfg.damaged, "notes": list(cfg.notes)}


# --------------------------------------------------------------------------- 편집(메모리에서만, 저장은 save)
def add(cfg: ExtConfig, ext: str, category: str) -> Change:
    """확장자를 더한다. 원래 cfg 는 바꾸지 않고 새 cfg 를 담아 돌려준다. 이미 있으면 changed=False 로 알린다."""
    e, cat = normalize_ext(ext), normalize_category(category)
    new = cfg.copy()
    warns = warnings_for(e)
    table = effective_table(cfg)
    if e in table:
        cur = table[e][0]
        if cur == cat:
            origin = "사용자가 추가한 형식" if e in cfg.added else "기본 형식"
            return Change(cfg, False, f"{e} 은(는) 이미 {CATEGORY_LABELS[cur]} 목록에 있습니다({origin}). 중복이라 추가하지 않았습니다.", [])
        if e in cfg.added:  # 사용자가 추가한 것은 분류만 바꾼다
            new.added[e] = cat
            return Change(new, True, f"{e} 의 분류를 {CATEGORY_LABELS[cur]} → {CATEGORY_LABELS[cat]} 로 바꿨습니다.", warns)
        return Change(cfg, False, f"{e} 은(는) 기본으로 {CATEGORY_LABELS[cur]} 목록에 있습니다. 다른 분류로 옮기려면 먼저 삭제한 뒤 추가하세요.", [])
    if len(new.added) + len(new.removed) >= MAX_ENTRIES:
        raise FindExtsError(f"추가·삭제한 형식이 너무 많습니다(최대 {MAX_ENTRIES}개). 기본값으로 초기화하거나 일부를 정리하세요.")
    if e in fd.EXT_TABLE and e in new.removed and fd.EXT_TABLE[e][0] == cat:  # 삭제했던 기본 형식을 되살림
        new.removed.remove(e)
        return Change(new, True, f"삭제했던 기본 형식 {e} 을(를) {CATEGORY_LABELS[cat]} 목록에 되돌렸습니다.", warns)
    new.added[e] = cat
    return Change(new, True, f"{e} 을(를) {CATEGORY_LABELS[cat]} 목록에 추가했습니다(저장해야 반영됩니다).", warns)


def remove(cfg: ExtConfig, ext: str) -> Change:
    """확장자를 뺀다. 기본 형식은 삭제 목록에, 사용자가 추가한 형식은 추가 목록에서 제거한다."""
    e = normalize_ext(ext)
    new = cfg.copy()
    if e in new.added:
        cat = new.added.pop(e)
        return Change(new, True, f"사용자가 추가했던 {e}({CATEGORY_LABELS[cat]}) 을(를) 목록에서 뺐습니다(저장해야 반영됩니다).")
    if e in fd.EXT_TABLE and e not in new.removed:
        if len(new.added) + len(new.removed) >= MAX_ENTRIES:
            raise FindExtsError(f"추가·삭제한 형식이 너무 많습니다(최대 {MAX_ENTRIES}개). 기본값으로 초기화하거나 일부를 정리하세요.")
        new.removed.append(e)
        return Change(new, True, f"기본 형식 {e}({CATEGORY_LABELS[fd.EXT_TABLE[e][0]]}) 을(를) 삭제했습니다(저장해야 반영됩니다).")
    if e in new.removed:
        return Change(cfg, False, f"{e} 은(는) 이미 삭제한 기본 형식입니다. 다시 쓰려면 add 로 추가하세요.")
    return Change(cfg, False, f"{e} 은(는) 목록에 없습니다.")


# --------------------------------------------------------------------------- 저장 / 초기화
def save(cfg: ExtConfig, directory: str | os.PathLike | None = None) -> Path:
    """원자 저장(임시 파일에 쓴 뒤 교체). 이전 설정은 .bak 한 벌로 보관한다. 실패하면 FindExtsError(원래 파일은 그대로)."""
    # 저장 전에 한 번 더 검증(잘못된 값이 파일에 들어가지 않게)
    added = {normalize_ext(k): normalize_category(v) for k, v in cfg.added.items()}
    removed = sorted({normalize_ext(e) for e in cfg.removed if e in fd.EXT_TABLE})
    if len(added) + len(removed) > MAX_ENTRIES:
        raise FindExtsError(f"추가·삭제한 형식이 너무 많습니다(최대 {MAX_ENTRIES}개).")
    payload = ExtConfig(added, removed).to_json_dict()
    path = config_path(directory)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            bak = path.with_name(path.name + ".bak")
            bak.write_bytes(path.read_bytes())  # 복사: 교체가 실패해도 원래 파일이 남는다
        os.replace(tmp, path)
    except OSError as e:
        _log(str(e), "저장", e)
        try:
            tmp.unlink()
        except OSError:
            pass
        raise FindExtsError("설정을 저장하지 못했습니다. 저장 위치에 쓸 수 있는지 확인하세요(원래 설정은 그대로입니다).") from e
    return path


def reset(directory: str | os.PathLike | None = None) -> ResetResult:
    """기본값으로 되돌린다: 설정 파일을 번호 .bak 으로 옮겨 보관하고 파일을 없앤다(없으면 기본값). 결과를 항상 돌려준다."""
    path = config_path(directory)
    try:
        if not path.exists():
            return ResetResult(True, False, "이미 기본값입니다(저장된 설정이 없습니다).")
        bak = _numbered_bak(path)
        os.replace(path, bak)
    except OSError as e:
        _log(str(e), "초기화", e)
        return ResetResult(False, False, "기본값으로 되돌리지 못했습니다(설정 파일을 옮길 수 없음). 설정은 그대로입니다.")
    return ResetResult(True, True, "기본값으로 되돌렸습니다. 이전 설정은 보관했습니다.", errorlog.tokenize_home(str(bak)))
