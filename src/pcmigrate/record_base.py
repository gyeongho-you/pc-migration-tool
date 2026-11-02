"""설정 기록 모드의 공통 부품: 파일 이름, 오류 종류, 원자적 저장, 보고서 저장, 이전 기록 보관/초기화(reset)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import copier, envpaths, errorlog

STATE_FILE = "record-state.json"
CAND_FILE = "record-candidates.json"
REPORT_FILE = "record-last-report.json"
DRAFT_FILE = "rule-draft.json"


class RecordError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 오류(상태 파일 없음, 라벨 오류 등)."""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_report(work: Path, rep: dict) -> None:
    try:
        work.mkdir(parents=True, exist_ok=True)
        _atomic_write(work / REPORT_FILE, json.dumps(rep, ensure_ascii=False, indent=1))
    except OSError as e:
        errorlog.record(e, "설정 기록: 보고서 저장")


TOOL_FILE_NAMES = (STATE_FILE, CAND_FILE, REPORT_FILE, DRAFT_FILE)


@dataclass
class ResetResult:
    work: str
    status: str = "failed"  # ok | nothing | failed
    moved: list = field(default_factory=list)  # [(원래 이름, 보관 이름)]
    kept_foreign: list = field(default_factory=list)  # 이 도구의 파일이 아니라 건드리지 않은 파일 이름
    reported: bool = True  # 결과 보고서(record-last-report.json)를 썼는지(기록이 없는 폴더에는 쓰지 않음)


def _is_tool_file(name: str) -> bool:
    return name in TOOL_FILE_NAMES or bool(re.fullmatch(r"snap-(?:before|baseline\d*|after\d+)\.json", name))


def _archive_previous(wd: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """이전 기록 산출물(스냅샷, 상태, 후보, 보고서, 초안)을 지우지 않고 번호 .bak 으로 옮긴다(start --force 와 reset 이 함께 쓴다).

    안전: 작업 폴더 바로 아래의 일반 파일만 옮기고(링크이거나 실제 위치가 작업 폴더 밖이면 거부), 이 도구 이름이 아닌 파일과 이미 있는
    .bak/.tmp 는 건드리지 않는다. 돌려주는 값: (옮긴 [(원래 이름, 보관 이름)], 건드리지 않은 다른 파일 이름들).
    """
    wd_real = os.path.normcase(os.path.realpath(wd))
    moved: list[tuple[str, str]] = []
    foreign: list[str] = []
    try:
        entries = sorted(wd.iterdir(), key=lambda p: p.name)
    except OSError as e:
        raise RecordError(f"작업 폴더를 읽지 못했습니다({type(e).__name__})") from e
    tool_names = [f.name for f in entries if (f.is_file() or f.is_symlink()) and _is_tool_file(f.name)]
    if tool_names == [REPORT_FILE]:  # 이전 보고서만 남았으면(초기화 직후 등) 보관할 기록이 없는 것이다
        entries = [f for f in entries if f.name != REPORT_FILE]
    for f in entries:
        if not f.is_file() and not f.is_symlink():
            continue
        if not _is_tool_file(f.name):
            if not re.search(r"\.(bak(\.\d+)?|tmp)$", f.name):
                foreign.append(f.name)
            continue
        if f.is_symlink() or os.path.normcase(os.path.realpath(f.parent)) != wd_real:
            raise RecordError(f"{f.name} 은(는) 작업 폴더 밖을 가리키는 링크라 초기화하지 않습니다. 작업 폴더를 확인하세요")
        try:
            bak = copier._backup_path(f)
            os.replace(f, bak)
            moved.append((f.name, bak.name))
        except OSError as e:
            errorlog.record(e, "설정 기록: 이전 산출물 보관")
            raise RecordError(f"이전 기록 파일 {f.name} 을(를) 보관하지 못했습니다({type(e).__name__}). 파일을 닫고 다시 시도하세요") from e
    return moved, foreign


def reset(work: str | Path, keep_backup: bool = True, now: Callable[[], datetime] = datetime.now) -> ResetResult:
    """기록 초기화: 이전 스냅샷·상태·후보·초안·보고서를 번호 .bak 으로 옮기고 빈 상태로 되돌린다(어떤 파일도 지우지 않음).
    결과는 항상 보고서(record-last-report.json)에 남긴다. 작업 폴더 안에 이 도구 것이 아닌 파일이 있으면 그대로 둔다."""
    if not keep_backup:
        raise RecordError("이전 기록을 보관하지 않고 지우는 초기화는 지원하지 않습니다(원본 보존 원칙). 필요 없으면 보관본(.bak)을 직접 지우세요")
    wd = Path(envpaths.expand_env(str(work)))
    if wd.parent == wd:
        raise RecordError("드라이브 루트는 작업 폴더로 쓸 수 없어 초기화하지 않습니다")
    if not wd.is_dir():
        raise RecordError(f"작업 폴더를 찾지 못했습니다: {errorlog.tokenize_home(str(wd))}")
    res = ResetResult(errorlog.tokenize_home(str(wd)))
    try:
        has_tool = any((f.is_file() or f.is_symlink()) and _is_tool_file(f.name) for f in wd.iterdir())
    except OSError as e:
        raise RecordError(f"작업 폴더를 읽지 못했습니다({type(e).__name__})") from e
    if not has_tool:  # 이 도구의 기록이 하나도 없는 폴더(홈, 문서 폴더 등 엉뚱한 폴더 포함)에는 보고서도 새로 만들지 않는다
        res.status, res.reported = "nothing", False
        return res
    try:
        res.moved, res.kept_foreign = _archive_previous(wd)
        res.status = "ok" if res.moved else "nothing"
        return res
    except BaseException as e:
        if not isinstance(e, RecordError):
            errorlog.record(e, "설정 기록 초기화")
        raise
    finally:
        _write_report(wd, {"op": "reset", "status": res.status, "at": now().isoformat(timespec="seconds"),
                           "moved": [list(m) for m in res.moved], "kept_foreign": res.kept_foreign,
                           "note": "이전 기록은 .bak 으로 보관했습니다(삭제하지 않음)" if res.moved else "보관할 이전 기록이 없었습니다"})
