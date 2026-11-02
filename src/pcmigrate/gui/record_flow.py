"""'바꿔 보며 설정 찾기' 화면의 순수 함수와 상태(stage60, tkinter 와 무관). 화면과 컨트롤러가 같이 쓴다.

여기서 만든 문구는 화면용이다: 백엔드(record_mode)의 안내 문장에는 명령줄 낱말이 섞여 있어 화면에 그대로 보이지 않고,
분석 결과의 플래그(registry_likely/undetermined/truncated/outside/secret/removed)로 texts 의 쉬운 문구를 고른다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import record_infer as ri
from ..progress import fmt_bytes
from . import state as S
from . import texts as T

ROOM_PICK, ROOM_FOLDERS, ROOM_STEPS, ROOM_CANDS, ROOM_DRAFT = "pick", "folders", "steps", "cands", "draft"
MAX_PROGRAM_ROWS = 600
MAX_CAND_ROWS = 300
CHECKABLE_DEFAULT = ("높음", "중간")  # 기본으로 체크하는 확신도


@dataclass
class RecordState:
    from_screen: str = S.OLD_DISCOVER
    programs: list = field(default_factory=list)  # api.RecordProgram 목록(설치된 프로그램)
    programs_loaded: bool = False
    only_norule: bool = True
    filter_text: str = ""
    program: Any = None  # 고른 detect.Program
    plan: Any = None  # record_infer.Inference
    running: list = field(default_factory=list)
    previous: dict | None = None  # 같은 프로그램의 이전 기록 요약({"done","when"})
    session: Any = None  # record_mode.Session
    room: str = ROOM_PICK
    analysis: Any = None
    rows: list = field(default_factory=list)  # 후보 표 줄(dict)
    secret_rows: list = field(default_factory=list)
    checked: set = field(default_factory=set)  # 체크한 후보 경로(환경변수 표기)
    draft: dict | None = None  # 저장한 초안 {path, d, f, rid}
    draft_note: str = ""  # 초안을 만들지 않은 이유(화면용 문구)
    notes: list = field(default_factory=list)  # 마지막 작업 안내(화면용 문구)
    wanted: str = ""  # ① 화면에서 이미 고른 프로그램 이름(목록을 읽은 뒤 미리 고른다)
    previous_session: Any = None  # 같은 프로그램의 이전 기록(이어서 하기용)


# ------------------------------------------------------------------ 프로그램 목록(화면 A)
def short_location(path: str) -> str:
    """설치 위치를 '...\\제조사\\제품' 처럼 뒤 두 단계만(사용자 이름 등이 보이지 않게)."""
    product, vendor = ri.path_names(path or "")
    if not product:
        return ""
    return "...\\" + (f"{vendor}\\" if vendor else "") + product


def program_rows(st: RecordState) -> list[dict]:
    low = st.filter_text.strip().lower()
    rows = []
    for i, rp in enumerate(st.programs):
        p = rp.program
        if st.only_norule and rp.has_rule:
            continue
        if low and low not in p.name.lower() and low not in (p.publisher or "").lower():
            continue
        rows.append({"index": i, "name": p.display if hasattr(p, "display") else p.name, "publisher": p.publisher or "",
                     "where": short_location(p.install_location)})
        if len(rows) >= MAX_PROGRAM_ROWS:
            break
    return rows


# ------------------------------------------------------------------ 폴더 확인(화면 B)
def folder_rows(st: RecordState) -> list[dict]:
    """훑을 폴더(켜짐/꺼짐)와 제외 후보를 한 목록으로. 제외 후보는 체크하면 켜지는 '추가' 줄이 된다."""
    plan = st.plan
    if plan is None:
        return []
    rows = []
    for f in plan.folders:
        conf = T.REC_ADDED_CONF if f.source == "added" else f.confidence
        rows.append({"token": f.token, "on": f.enabled, "conf": conf, "reason": f.reason, "candidate": False})
    for tok, why in plan.excluded:
        rows.append({"token": tok, "on": False, "conf": T.REC_EXCLUDED_CONF, "reason": why, "candidate": True})
    return rows


# ------------------------------------------------------------------ 기록 단계(화면 C)
def label_of(info: dict) -> str:
    kind, label = info.get("kind"), str(info.get("label", ""))
    if kind == "before":
        return T.REC_LABELS["before"]
    digits = "".join(ch for ch in label if ch.isdigit())
    if kind == "baseline":
        return T.REC_LABELS["baseline"] + (f" {digits}번째" if digits and digits != "1" else "")
    return T.REC_LABELS["after"].format(n=digits or "1")


def after_count(session) -> int:
    return sum(1 for s in (session.snaps if session else []) if s["kind"] == "after")


def next_action(session) -> str:
    """지금 할 일: 'baseline'(아직 기준선 전) 또는 'after'(설정 바꾸고 변경 후)."""
    kinds = [s["kind"] for s in (session.snaps if session else [])]
    return "after" if ("baseline" in kinds or "after" in kinds) else "baseline"


def current_step(st: RecordState) -> int:
    """단계 표시줄의 지금 단계(1부터, 0 이면 아직 시작 전이라 표시줄을 숨김)."""
    if st.room == ROOM_PICK:
        return 0
    if st.room == ROOM_FOLDERS:
        return 1
    if st.room == ROOM_STEPS:
        return 3 if next_action(st.session) == "baseline" else 4
    return 5 if st.room == ROOM_CANDS else 6


def log_lines(session) -> list[str]:
    if session is None:
        return []
    return [T.REC_LOG_LINE.format(icon=T.ICON_OK, label=label_of(s), n=s["files"]) for s in session.snaps]


def snapshot_notes(res: Any, label: str) -> list[str]:
    """스냅샷 결과(record_mode.SnapshotResult)에서 화면용 안내 줄들."""
    out = [T.REC_SNAP_DONE.format(label=label, n=res.files)]
    if res.unreadable:
        out.append(T.REC_UNREADABLE.format(n=res.unreadable))
    if res.cloud_skipped:
        out.append(T.REC_CLOUD.format(n=res.cloud_skipped))
    if res.truncated:
        out.append(T.REC_TRUNCATED_SNAP)
    if any("새로 생긴 설정 폴더" in w for w in res.warnings):
        out.append(T.REC_NEW_FOLDER)
    return out


def previous_summary(session) -> dict | None:
    if session is None or not session.snaps:
        return None
    last = session.snaps[-1]
    return {"done": label_of(last), "when": str(last.get("taken_at", ""))[:10]}


# ------------------------------------------------------------------ 후보 표(화면 D)
def size_text(n: int) -> str:
    return fmt_bytes(n)


def cand_rows(analysis: Any) -> tuple[list[dict], list[dict]]:
    """(표에 보일 후보 줄들, 민감해서 뺀 줄들). 담을 수 있는 줄은 높음·중간만 기본 체크."""
    rows, secret = [], []
    for c in analysis.candidates:
        conf = T.REC_CONF_REF if (c.masked or c.removed) else c.confidence
        row = {"path": c.path, "conf": conf, "size": size_text(c.size), "reason": c.reason, "change": c.change}
        if c.secret:
            secret.append(row)
            continue
        row["checkable"] = not (c.masked or c.noise_like or c.removed or c.size > _big())
        row["checked"] = row["checkable"] and c.confidence in CHECKABLE_DEFAULT
        rows.append(row)
    return rows, secret


def _big() -> int:
    from .. import record_mode as rm
    return rm.BIG_CANDIDATE


def candidates_notice(a: Any) -> tuple[str, str]:
    """(수준, 문구): 'warn' 안내 상자에 보여 줄 말. 문제 없으면 ('', '')."""
    if a.registry_likely:
        return "warn", T.REC_REGISTRY
    if a.undetermined and a.truncated:
        return "warn", T.REC_TRUNCATED
    if a.undetermined:
        return "warn", T.REC_BASELINE_ONLY
    return "", ""


def no_draft_text(a: Any) -> str:
    """초안을 만들지 못한 이유(화면용). 백엔드 문장에는 명령줄 낱말이 있어 플래그로 고른다."""
    if a.registry_likely:
        return T.REC_REGISTRY
    if a.undetermined:
        return T.REC_TRUNCATED if a.truncated else T.REC_BASELINE_ONLY
    if a.outside:
        return T.REC_OUTSIDE
    real = [c for c in a.candidates if not c.masked]
    if real and all(c.secret or c.removed for c in real) and any(c.secret for c in real):
        return T.REC_ONLY_SECRET
    if real and all(c.removed for c in real):
        return T.REC_ONLY_REMOVED
    return T.G_REC_NONE_CHECKED
