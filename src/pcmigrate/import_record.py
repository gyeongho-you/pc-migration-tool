"""복원 기록과 프로그램별 되돌리기(undo-import).

복원(import --yes)이 끝나면 '무엇을 어디에 썼고 무엇을 .bak 으로 밀어냈는지'를 기록 파일 하나로 남긴다. 되돌리기는 이 기록만을 근거로 한다.

기록 파일 형식(JSON, 한 번의 복원마다 파일 하나, version 1):
  {"format": "pcmigrate-import-record", "version": 1, "tool_version": "...", "created": "ISO 시각",
   "package": "패키지 경로(표시용)", "manifest_created": "패키지를 만든 시각",
   "programs": [{"id", "name", "kind", "selected", "files", ...}],        # plan.programs 와 같은 표(선택 여부 포함)
   "entries": [{"program": "규칙 id 또는 extra:<루트키>/<이름>", "entry": "패키지 안 경로", "target": "복원한 위치",
                "base_dir": "허용 루트", "backup": "밀어낸 원래 파일(.bak) 경로, 새 파일이면 빈 문자열",
                "backup_size": N, "backup_sha256": "...", "new_size": N, "new_sha256": "복원한 파일의 해시",
                "undone": false, "undone_at": "", "undo_action": ""}]}
  entries 에는 실제로 복원한 파일(copied)만 넣는다(이미 같음/건너뜀/실패/선택 제외는 되돌릴 것이 없다).

되돌리기 규칙(사용자 데이터 삭제 금지):
- 덮어쓴 파일(backup 있음): .bak 이 기록한 크기/해시 그대로 있고, 대상이 복원한 그대로일 때만 .bak 을 원래 이름으로 되돌린다.
  .bak 이 없거나 바뀌었으면 그 파일은 되돌리지 않고 이유를 알린다. 복원 뒤 사용자가 대상을 고쳤으면 그 수정을 지키려고 되돌리지 않는다.
- 새로 만든 파일(backup 없음): 기본은 지우지 않고 '새 파일 N개는 지우지 않았습니다'로 알린다. move_new=True 면 '<이름>.removed' 로 옮긴다
  (복원한 그대로일 때만, 지우지 않음).
- 기록 파일은 신뢰하지 않는다: 대상이 허용 루트(base_dir) 안인지, 링크를 거치지 않는지, .bak 이 같은 폴더의 '<이름>.bak*' 인지 다시 확인한다.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__, fs
from .copier import COPIED, Report, sha256_file
from .envpaths import expand_env

RECORD_FORMAT = "pcmigrate-import-record"
RECORD_VERSION = 1
RECORD_DIR = "%LOCALAPPDATA%\\pcmigrate\\import-records"
FALLBACK_DIR = "pcmigrate-import-records"
RECORD_RE = re.compile(r"^import-\d{8}-\d{6}(?:-\d+)?\.json$")
REMOVED_SUFFIX = ".removed"

# 되돌리기 항목의 상태
RESTORE, MOVE_NEW, KEEP_NEW, DONE = "restore", "move_new", "keep_new", "already_undone"
BAK_MISSING, BAK_CHANGED, TARGET_CHANGED, UNSAFE = "bak_missing", "bak_changed", "target_changed", "unsafe"
PROBLEMS = (BAK_MISSING, BAK_CHANGED, TARGET_CHANGED, UNSAFE)
RESTORED, MOVED, FAILED_UNDO = "restored", "moved", "failed"
REASON_TEXT = {
    BAK_MISSING: "되돌릴 .bak 파일이 없습니다(지웠거나 옮겼을 수 있습니다)",
    BAK_CHANGED: ".bak 파일의 내용이 복원할 때와 달라졌습니다(그 사이 바뀌었으므로 되돌리지 않습니다)",
    TARGET_CHANGED: "복원한 뒤에 직접 고친 파일이라 되돌리지 않았습니다(고친 내용을 지키기 위해)",
    UNSAFE: "기록이 안전하지 않아 건너뜀(허용 위치 밖이거나 링크를 거침)",
}


class RecordError(ValueError):
    """복원 기록 파일이 없거나 형식이 올바르지 않음."""


def record_dir() -> Path:
    """기록 폴더. %LOCALAPPDATA% 를 해석할 수 없거나 쓸 수 없으면 임시 폴더로 대신한다."""
    primary = expand_env(RECORD_DIR)
    if "%" not in primary and os.path.isabs(primary):
        try:
            Path(primary).mkdir(parents=True, exist_ok=True)
            return Path(primary)
        except OSError:
            pass
    fb = Path(tempfile.gettempdir()) / FALLBACK_DIR
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_record(package: str | Path, plan, report: Report, now: Callable[[], datetime] = _now) -> dict:
    """apply_import 결과(report)와 계획(plan)에서 복원 기록을 만든다. 복사한(copied) 파일만 entries 에 넣는다."""
    by_entry = {it["entry"]: it for it in plan.items}
    entries = []
    for r in report.results:
        if r.status != COPIED:
            continue
        it = by_entry.get(r.src, {})
        e = {"program": it.get("program", ""), "entry": r.src, "target": fs.plain_path_text(r.dst),
             "base_dir": it.get("base_dir", ""), "backup": fs.plain_path_text(r.backup) if r.backup else "",
             "backup_size": 0, "backup_sha256": "", "new_size": r.size, "new_sha256": r.sha256,
             "undone": False, "undone_at": "", "undo_action": ""}
        if r.backup:
            try:
                e["backup_size"] = os.stat(fs.long_path(r.backup)).st_size
                e["backup_sha256"] = sha256_file(Path(r.backup))
            except OSError:
                pass  # 해시를 못 구하면 되돌릴 때 '바뀜'으로 보고 거부한다(빈 해시와 같을 수 없다)
        entries.append(e)
    return {"format": RECORD_FORMAT, "version": RECORD_VERSION, "tool_version": __version__,
            "created": now().isoformat(timespec="seconds"), "package": fs.plain_path_text(str(package)),
            "manifest_created": str(plan.manifest.get("created", "")), "programs": list(plan.programs), "entries": entries}


def _unique_name(directory: Path, now: datetime) -> Path:
    stem = now.strftime("import-%Y%m%d-%H%M%S")
    cand = directory / f"{stem}.json"
    n = 1
    while cand.exists():
        cand = directory / f"{stem}-{n}.json"
        n += 1
    return cand


def _write_json(path: Path, rec: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_record(rec: dict, directory: Path | str | None = None, now: Callable[[], datetime] = _now) -> Path:
    d = Path(directory) if directory else record_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _unique_name(d, now())
    _write_json(path, rec)
    return path


def latest_record(directory: Path | str | None = None) -> Path | None:
    d = Path(directory) if directory else record_dir()
    try:
        files = sorted((p for p in d.iterdir() if RECORD_RE.match(p.name)), key=lambda p: p.name)
    except OSError:
        return None
    return files[-1] if files else None


def load_record(path: Path | str) -> dict:
    p = Path(path)
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RecordError(f"복원 기록 파일을 찾을 수 없습니다: {p}") from None
    except (OSError, ValueError) as e:
        raise RecordError(f"복원 기록 파일을 읽을 수 없습니다: {p} ({type(e).__name__})") from None
    if not isinstance(rec, dict) or rec.get("format") != RECORD_FORMAT or not isinstance(rec.get("entries"), list):
        raise RecordError(f"복원 기록 파일 형식이 올바르지 않습니다: {p}")
    if not isinstance(rec.get("version"), int) or rec["version"] > RECORD_VERSION:
        raise RecordError(f"이 도구보다 새로운 형식의 복원 기록입니다(version {rec.get('version')}): {p}")
    for e in rec["entries"]:
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("program", "target", "backup", "new_sha256")):
            raise RecordError(f"복원 기록 항목 형식이 올바르지 않습니다: {p}")
    return rec


@dataclass
class UndoItem:
    program: str
    target: str
    backup: str
    action: str           # RESTORE / MOVE_NEW / KEEP_NEW / DONE / 문제 코드(PROBLEMS)
    detail: str = ""
    result: str = ""      # 적용 뒤: RESTORED / MOVED / FAILED_UNDO ('' = 아무것도 하지 않음)
    index: int = -1       # record['entries'] 의 위치


@dataclass
class UndoPlan:
    record_path: str
    programs: list[str]               # 이번에 고른 프로그램 id(기록에 있는 것)
    items: list[UndoItem] = field(default_factory=list)

    def count(self, *actions: str) -> int:
        return sum(1 for i in self.items if i.action in actions)

    @property
    def problems(self) -> list[UndoItem]:
        return [i for i in self.items if i.action in PROBLEMS]

    def summary(self) -> dict:
        return {"restore": self.count(RESTORE), "new_kept": self.count(KEEP_NEW), "new_moved": self.count(MOVE_NEW),
                "already_undone": self.count(DONE), "problems": len(self.problems)}


def record_programs(rec: dict) -> list[str]:
    seen: list[str] = []
    for e in rec["entries"]:
        if e["program"] not in seen:
            seen.append(e["program"])
    return seen


def _bak_ok(target: Path, backup: Path) -> bool:
    return backup.parent == target.parent and backup.name.startswith(target.name + ".bak")


def _under(target: Path, base: Path) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(target)), os.path.normcase(os.path.abspath(base))]) == \
            os.path.normcase(os.path.abspath(base))
    except ValueError:
        return False


def _hash_or_none(p: Path) -> str | None:
    try:
        return sha256_file(p) if p.is_file() and not p.is_symlink() else None
    except OSError:
        return None


def plan_undo(rec: dict, record_path: str | Path, only: list[str], *, move_new: bool = False) -> UndoPlan:
    """선택한 프로그램의 되돌리기 계획. 파일 시스템은 읽기만 한다(해시 계산)."""
    from .package import program_matches
    ids = [p for p in record_programs(rec) if any(program_matches(t, p) for t in only)]
    plan = UndoPlan(str(record_path), ids)
    for idx, e in enumerate(rec["entries"]):
        if e["program"] not in ids:
            continue
        target, backup = Path(e["target"]), Path(e["backup"]) if e["backup"] else None
        it = UndoItem(e["program"], e["target"], e["backup"], DONE, index=idx)
        plan.items.append(it)
        if e.get("undone"):
            it.detail = "이미 되돌렸습니다"
            continue
        base = e.get("base_dir") or ""
        if (not base or not _under(target, Path(base)) or fs.reparse_in_chain(base, target)
                or (backup is not None and not _bak_ok(target, backup))):
            it.action, it.detail = UNSAFE, REASON_TEXT[UNSAFE]
            continue
        cur = _hash_or_none(target)
        if backup is None:  # 복원이 새로 만든 파일: 지우지 않는다
            if not move_new:
                it.action = KEEP_NEW
            elif cur is None:
                it.action, it.detail = DONE, "파일이 이미 없습니다"
            elif cur != e["new_sha256"]:
                it.action, it.detail = TARGET_CHANGED, REASON_TEXT[TARGET_CHANGED]
            else:
                it.action = MOVE_NEW
            continue
        bak_hash = _hash_or_none(backup)
        if bak_hash is None:
            if cur is not None and e.get("backup_sha256") and cur == e["backup_sha256"]:
                it.action, it.detail = DONE, "원래 내용이 이미 제자리에 있습니다"  # 이전 되돌리기가 기록 전에 끊긴 경우
            else:
                it.action, it.detail = BAK_MISSING, REASON_TEXT[BAK_MISSING]
            continue
        if not e.get("backup_sha256") or bak_hash != e["backup_sha256"]:
            it.action, it.detail = BAK_CHANGED, REASON_TEXT[BAK_CHANGED]
            continue
        if target.exists() and cur != e["new_sha256"]:  # 복원한 뒤 사용자가 고쳤거나 폴더로 바뀐 경우
            it.action, it.detail = TARGET_CHANGED, REASON_TEXT[TARGET_CHANGED]
            continue
        it.action = RESTORE
    return plan


def apply_undo(rec: dict, plan: UndoPlan, now: Callable[[], datetime] = _now) -> UndoPlan:
    """계획을 실행하고 기록 파일(plan.record_path)에 되돌린 항목을 표시한다. 기록 갱신은 실패/중단해도 마지막에 시도한다."""
    removed_stamp = now().isoformat(timespec="seconds")
    try:
        for it in plan.items:
            e = rec["entries"][it.index]
            if it.action not in (RESTORE, MOVE_NEW):
                continue
            target = Path(it.target)
            try:
                if it.action == RESTORE:
                    os.replace(fs.long_path(it.backup), fs.long_path(target))  # .bak -> 원래 이름(복원된 파일은 패키지에 그대로 있다)
                    it.result = RESTORED
                else:
                    dst = target.with_name(target.name + REMOVED_SUFFIX)
                    n = 1
                    while os.path.lexists(fs.long_path(dst)):
                        dst = target.with_name(f"{target.name}{REMOVED_SUFFIX}.{n}")
                        n += 1
                    os.replace(fs.long_path(target), fs.long_path(dst))  # 지우지 않고 옮긴다
                    it.result, it.detail = MOVED, str(dst)
                e["undone"], e["undone_at"], e["undo_action"] = True, removed_stamp, it.result
            except OSError as ex:
                it.result, it.detail = FAILED_UNDO, f"{type(ex).__name__}: {ex.strerror or ex}"
    finally:
        try:
            _write_json(Path(plan.record_path), rec)
        except OSError:
            pass
    return plan
