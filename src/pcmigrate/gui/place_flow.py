"""'받은 파일 원래 위치에 놓기'(place) 화면의 상태와 순수 계산(stage50). tkinter 와 무관해서 화면 없이 시험할 수 있다.

WizardState 에 필드를 더하지 않고 이 모듈의 PlaceState 를 컨트롤러가 self.place_state 로 따로 들고 있다(files_flow.py 와 같은 방식).
화면(views/place.py)은 컨트롤러의 메서드와 이 모듈의 순수 함수만 쓴다.

백엔드(place_original.PlaceReport)의 필드는 이 모듈 한 곳(place_summary/place_rows/place_todo_lines/place_confirm_text)에서만 읽는다.
place_original.summary_lines 는 명령줄 문구(--yes, --keep 등)가 들어 있어 GUI 에 쓰지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import fs
from .. import place_original as po
from ..progress import fmt_bytes
from . import texts as T
from .controller_common import PathCheck

MAX_TABLE_ROWS = 500  # 표에 그리는 줄 상한(나머지는 개수만: 결과 리포트 파일에 전부 있다)
MAX_WARNING_LINES = 10


@dataclass
class PlaceState:
    # ---- 입력/옵션(화면이 바꿀 때마다 컨트롤러에 알린다)
    recv: str = ""
    maps: list = field(default_factory=list)  # [(받은 폴더의 드라이브 문자, 이 PC의 드라이브 문자)]
    keep: bool = False  # 복사만 하기(기본 꺼짐: 같은 드라이브는 이동, 다른 드라이브는 복사·검사 뒤 받은 폴더에서 삭제)
    no_overwrite: bool = False
    include_unverified: bool = False
    gather_on: bool = False
    gather_dir: str = ""  # 비어 있으면 기본 폴더(바탕화면의 '이전 파일')
    report_path: str = ""
    came_from: str = ""  # 들어온 화면(이전 버튼이 돌아갈 곳)
    # ---- 확인/미리보기
    preview_stale: bool = False  # 미리보기를 한 뒤 입력/옵션을 바꿔 무효가 됨(이유 문구를 고르는 데 쓴다)
    ack: bool = False  # '받은 폴더의 파일이 지워진다는 것을 확인했습니다'
    preview_key: tuple | None = None  # 미리보기를 마친 (입력, 옵션). 지금 값과 다르면 놓을 수 없다
    preview_report: Any = None  # 마지막으로 정상 종료한 미리보기 결과(확인창 숫자)
    # ---- 결과(화면에 그릴 것)
    report: Any = None  # place_original.PlaceReport (미리보기/적용/중간에 멈춘 부분 결과)
    was_preview: bool = True
    stopped: str = ""  # "" | cancelled | forced | error  (사용자에게 보일 중단 이유)
    stopped_note: str = ""  # 결과 리포트를 못 얻은 중단(강제 중단 등)의 안내
    warnings: list = field(default_factory=list)  # 작업 중 백엔드가 알려 준 경고(쉬운 말로 바꾼 것)
    saved_path: str = ""  # 결과 리포트를 실제로 저장한 위치
    save_note: str = ""  # 저장 대체/실패 안내
    report_stale: bool = False  # 받은 폴더/옵션을 바꿔서 화면의 결과가 '이전 결과'가 됨
    auto_report: str = ""  # 결과 리포트 기본 저장 위치(비워 둔 칸의 값: 놓을 때마다 새로 정한다. 화면이 채운 기본값은 여기에만 있고 report_path 에 저장하지 않는다)
    run_id: int = 0  # 실행 번호(강제 중단된 옛 작업이 새 작업의 상태를 덮지 않게)


@dataclass
class PlaceCheck(PathCheck):
    """입력 점검 결과: ok/lines/info/warnings 는 PathCheck 와 같고, 아래는 화면이 쓰는 추가 정보."""
    drives: list = field(default_factory=list)  # 받은 폴더 맨 위의 드라이브 폴더 문자
    recv_ok: bool = False  # 받은 폴더 자체는 올바르다(다른 입력 문제와 따로 본다)
    maps: dict | None = None  # 점검을 통과한 {원본 문자: 대상 문자}
    gather_folder: str = ""  # 모을 폴더(모으기를 켰을 때)
    gather_onedrive: bool = False
    gather_default_blocked: bool = False  # 기본 모으기 폴더가 OneDrive 라 '놓기'를 할 수 없다
    apply_block: str = ""  # 미리보기는 되지만 '놓기'는 막는 이유


# --------------------------------------------------------------------------- 문구 정리(명령줄 옵션 이름을 화면용 말로)
_OPTION_WORDS = {"--map": "드라이브 바꾸기", "--no-overwrite": "덮어쓰지 않기", "--gather": "모으기", "--keep": "복사만 하기",
                 "--include-unverified": "받기가 끝나지 않은 폴더도 놓기", "--yes": "놓기", "--report": "결과 리포트", "--json": "결과 파일"}
_AGAIN = (("같은 명령을 다시 실행하면", "미리보기를 다시 한 뒤 '놓기'를 누르면"),
          ("같은 명령을 다시 실행하세요", "미리보기를 다시 한 뒤 '놓기'를 누르세요"),
          ("같은 명령을 다시 실행", "미리보기를 다시 한 뒤 '놓기'를 누르기"))


def plain_cli(text: str) -> str:
    """백엔드 문구에서 명령줄 옵션 이름(--map 등)과 명령 예시를 없애고 화면용 말로 바꾼다(화면에는 '--' 가 남지 않는다)."""
    out = str(text or "")
    for old, new in _AGAIN:
        out = out.replace(old, new)
    out = out.replace("저널", "받기 기록").replace("(verify)", "")
    out = re.sub(r"`[^`]*--[^`]*`", "(README 의 4GB 분할 파일 안내 참고)", out)  # 명령줄 예시(백틱으로 감싼 것)
    out = re.sub(r"\s*\([^()]*--[a-z][^()]*\)", "", out)  # '(--gather=폴더)' 같은 괄호 안내
    for opt, word in _OPTION_WORDS.items():
        out = re.sub(re.escape(opt) + r"(?:=[^\s,.)]*)?", word, out)
    out = re.sub(r"--[A-Za-z][\w-]*", "", out)  # 남은 옵션 이름
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def friendly_reason(code: str, detail: str = "") -> str:
    """사유 코드(place_original.REASONS/실패 코드) -> 화면용 문구. 알 수 없는 코드는 백엔드 문구를 정리해서 쓴다."""
    detail = plain_cli((detail or "").replace(po.PART_SUFFIX, ""))
    text = T.PL_REASON_NETWORK if code == po.UNC else T.PL_REASON.get(code)
    if text:
        return text + (f" ({detail})" if detail else "")
    return plain_cli(po.reason_text(code, detail))


def _size(n: Any) -> str:
    try:
        return fmt_bytes(int(n or 0))
    except (TypeError, ValueError):
        return "0B"


size_text = _size  # 화면이 쓰는 용량 문구(1.2GB 같은)


def _g(rep: Any) -> Any:
    return getattr(rep, "gather_rep", None)


# --------------------------------------------------------------------------- 요약
def place_summary(rep: Any) -> dict:
    """PlaceReport -> 화면 요약 숫자들(순수 함수). 취소/오류로 중간에 멈춘 부분 결과도 같은 모양으로 돌려준다."""
    c = dict(getattr(rep, "counts", {}) or {})
    g = _g(rep)
    preview = bool(rep.dry_run)
    interrupted = str((getattr(rep, "meta", {}) or {}).get("interrupted") or "")
    same = int(c.get("skipped_identical", 0))
    if preview:
        placed_n, placed_bytes = int(rep.total_files), int(rep.total_bytes)
    else:
        placed_n, placed_bytes = int(c.get("moved", 0)) + int(c.get("copied", 0)), int(rep.placed_bytes)
    over_n = (len(rep.overwrites) + int(rep.overwrite_truncated)) if preview else int(rep.overwritten_count)
    dest_newer = int(rep.dest_newer_count) if preview else int(rep.overwritten_newer_count)
    delete_n, delete_bytes = int(rep.delete_planned), int(rep.delete_planned_bytes)
    if g is not None:
        if preview:
            over_n += len(g.overwrites) + int(g.overwrite_truncated)
            dest_newer += int(g.dest_newer_count)
            delete_n += int(g.delete_planned)
            delete_bytes += int(g.delete_planned_bytes)
        else:
            over_n += int(g.overwritten_count)
            dest_newer += int(g.overwritten_newer_count)
    move_n = int((getattr(rep, "modes", {}) or {}).get(po.MOVE, 0)) + (int((g.modes or {}).get(po.MOVE, 0)) if g is not None else 0)  # 같은 드라이브 이동 예정/처리 파일 수
    remaining = dict(rep.remaining or {})
    gi = dict(rep.gather_info or {})
    return {
        "preview": preview, "interrupted": interrupted, "why": T.PL_WHY.get(interrupted, interrupted),
        "keep": bool(rep.keep), "placed_n": placed_n, "placed_bytes": placed_bytes, "same_n": same,
        "manual_n": int(rep.manual_count), "failed_n": int(rep.failed_count), "overwrite_n": over_n, "dest_newer_n": dest_newer,
        "move_n": move_n, "delete_n": delete_n, "delete_bytes": delete_bytes, "removed_n": int(rep.removed_sources) + (int(g.removed_sources) if g is not None else 0),
        "remaining_files": int(remaining.get("files", 0) or 0), "remaining_bytes": int(remaining.get("bytes", 0) or 0),
        "unreadable": int(remaining.get("unreadable", 0) or 0),
        "gather": bool(gi), "gather_folder": str(gi.get("folder", "")), "gather_n": len(rep.gathered),
        "gather_blocked": bool(gi.get("blocked")), "gather_guide_error": bool(gi.get("guide_error")),
        "needs_manual": bool(rep.needs_manual), "total_files": int(rep.total_files),
        "nothing": not rep.total_files and not rep.gathered and not rep.manual_count,
        "error": str((getattr(rep, "meta", {}) or {}).get("error") or ""),
        "error_log": str((getattr(rep, "meta", {}) or {}).get("error_log") or ""),
    }


def place_head(sm: dict) -> str:
    """결과 머리말(미리보기 / 놓았음 / 중간에 멈춤)."""
    if sm["interrupted"]:
        return (T.PL_PREVIEW_STOPPED_HEAD if sm["preview"] else T.PL_STOPPED_HEAD).format(why=sm["why"])
    return T.PL_PREVIEW_HEAD if sm["preview"] else T.PL_DONE_HEAD


def place_summary_line(sm: dict) -> str:
    """한 줄 요약 카드의 글."""
    if sm["nothing"] and not sm["interrupted"]:
        return T.PL_SUM_NOTHING
    parts = [(T.PL_SUM_PLACE if sm["preview"] else T.PL_SUM_PLACED).format(n=sm["placed_n"], size=_size(sm["placed_bytes"]))]
    if sm["same_n"]:
        parts.append((T.PL_SUM_SAME_PREVIEW if sm["preview"] else T.PL_SUM_SAME).format(n=sm["same_n"]))
    if sm["manual_n"]:
        parts.append(T.PL_SUM_MANUAL.format(n=sm["manual_n"]))
    parts.append(T.PL_SUM_FAILED.format(n=sm["failed_n"]) if sm["failed_n"] else "")
    return "    ".join(p for p in parts if p)


def place_level(sm: dict) -> str:
    """요약 카드 색: 실패/중단은 위험, 직접 할 일이 남았으면 주의, 아니면 성공."""
    if sm["interrupted"] or sm["failed_n"]:
        return "danger"
    if sm["manual_n"] or sm["needs_manual"]:
        return "warn"
    return "ok"


# --------------------------------------------------------------------------- 표
def _what(r: Any) -> str:
    if r.code == po.SPLIT_PARTS:
        return T.PL_KIND["split"].format(n=r.files)
    if r.kind == "folder" and r.files:
        return T.PL_KIND["folder_n"].format(n=r.files)
    return T.PL_KIND.get(r.kind, r.kind)


def _side(size: Any, when: Any) -> str:
    return T.PL_OVER_SIDE.format(size=_size(size), when=str(when)[:16].replace("T", " ")) if when else _size(size)


def _over_rows(rep: Any) -> list[dict]:
    out: list[dict] = []
    for r in ([rep] + ([_g(rep)] if _g(rep) is not None else [])):
        if r.dry_run:
            for x in r.overwrites:
                note = [T.PL_OVER_NEWER] if x.get("dest_newer") else []
                if x.get("backup"):
                    note.append(T.PL_OVER_BAK.format(path=x["backup"]))
                out.append({"expected": x.get("expected", ""), "path": x.get("dest", ""), "recv": _side(x.get("recv_size"), x.get("recv_mtime")),
                            "dest": _side(x.get("dest_size"), x.get("dest_mtime")), "note": " · ".join(note), "select": True})
        else:
            for x in r.overwritten:
                note = [T.PL_OVER_NEWER] if x.get("dest_newer") else []
                if x.get("backup"):
                    note.append(T.PL_OVER_BAK.format(path=x["backup"]))
                out.append({"expected": x.get("expected", ""), "path": x.get("dest", ""), "recv": _size(x.get("recv_size")),
                            "dest": _size(x.get("dest_size")), "note": " · ".join(note), "select": True})
    return out


def place_rows(rep: Any) -> dict:
    """결과 표 3종의 줄(순수 함수). 각 줄에는 탐색기로 보여 줄 경로(path)와 파일 선택 여부(select)가 들어 있다."""
    preview = bool(rep.dry_run)
    manual: list[dict] = []
    left_key = "leftover_preview" if preview else "leftover"
    for key, rows in (("skipped", rep.skipped), ("failed", [f for f in rep.failed if not f.via_gather]), (left_key, rep.leftover)):
        for r in rows:
            manual.append({"kind": f"{T.PL_KIND[key]} · {_what(r)}", "expected": fs.plain_path_text(r.expected),
                           "current": fs.plain_path_text(r.current), "reason": friendly_reason(r.code, r.detail),
                           "path": fs.plain_path_text(r.current), "select": r.kind == "file", "code": r.code})
    gathered: list[dict] = []
    for x in rep.gathered:
        what = T.PL_KIND["partial"] if x.get("status") == "partial" else T.PL_KIND.get(x.get("kind", ""), x.get("kind", ""))
        gathered.append({"kind": what, "expected": x.get("expected", ""), "current": x.get("gathered", ""),
                         "reason": friendly_reason(x.get("reason_code", "")), "path": x.get("gathered", ""),
                         "select": x.get("kind") == "file", "code": x.get("reason_code", "")})
    return {"manual": manual, "overwrite": _over_rows(rep), "gathered": gathered}


def place_todo_lines(rep: Any, warnings: list[str] | None = None) -> list[str]:
    """'직접 해야 할 일' 문장들(사유 코드에 따라 고른다. 새 문구를 만들지 않고 PL_TODO_* 만 쓴다). 할 일이 없으면 빈 목록."""
    codes: set[str] = set()
    for rows in (rep.skipped, rep.failed, rep.leftover):
        codes.update(r.code for r in rows)
    codes.update(x.get("reason_code", "") for x in rep.gathered)
    lines: list[str] = []
    table = ((po.SPLIT_PARTS, T.PL_TODO_SPLIT), (po.PROTECTED, T.PL_TODO_PROTECTED), (po.DRIVE_MISSING, T.PL_TODO_DRIVE),
             (po.NO_SPACE, T.PL_TODO_SPACE), (po.UNKNOWN_LOC, T.PL_TODO_UNKNOWN), (po.UNC, T.PL_TODO_NETWORK),
             (po.LINK, T.PL_TODO_LINK), (po.NO_OVERWRITE, T.PL_TODO_NOOVERWRITE), (po.UNSAFE_NAME, T.PL_TODO_NAME),
             (po.UNVERIFIED, T.PL_TODO_UNVERIFIED))
    covered = set()
    for code, text in table:
        if code in codes:
            lines.append(text)
            covered.add(code)
    if any(code in codes for code in ("in_use", "locked")):
        lines.append(T.PL_TODO_LOCKED)
        covered.update(("in_use", "locked"))
    if codes & {po.LEFTOVER, po.SRC_CHANGED, po.SRC_READONLY, po.SRC_ADS, po.SRC_ADS_UNKNOWN} or (not rep.dry_run and not rep.keep and (rep.remaining or {}).get("files")):
        lines.append(T.PL_TODO_LEFTOVER)
        covered.update((po.LEFTOVER, po.SRC_CHANGED, po.SRC_READONLY, po.SRC_ADS, po.SRC_ADS_UNKNOWN))
    if codes - covered - {po.INSIDE_RECEIVE, po.SAME_LOCATION}:
        lines.append(T.PL_TODO_OTHER)
    if rep.warnings:
        lines.append((T.PL_TODO_WARNINGS_PREVIEW if rep.dry_run else T.PL_TODO_WARNINGS).format(n=len(rep.warnings)))
        for w in rep.warnings[:MAX_WARNING_LINES]:
            lines.append("  " + f"{fs.plain_path_text(w.get('expected', ''))} | {plain_cli(w.get('text', ''))}")
    if warnings:  # 작업 중 알려 드린 내용(받기 기록이 없다는 안내 등)
        lines.append(T.PL_NOTES_TITLE + ":")
        lines.extend("  " + w for w in warnings[:MAX_WARNING_LINES])
    if rep.maps:  # 드라이브를 바꿔 놓으면 파일 안의 경로가 어긋날 수 있다
        lines.append(T.PL_TODO_PATHS)
    return lines


def place_notes(rep: Any, sm: dict) -> list[str]:
    """요약 아래 안내 줄들(대상이 더 새로움, 받은 폴더에 남은 것, 읽지 못한 곳, 모으기 결과, 실패 안내)."""
    notes: list[str] = []
    if sm["preview"] and sm["move_n"] and not sm["keep"]:  # 같은 드라이브 이동: 삭제 예정은 아니지만 받은 폴더에서 사라진다(확인 체크는 요구하지 않는다)
        notes.append(T.PL_MOVE_NOTE.format(n=sm["move_n"]))
    if sm["dest_newer_n"]:
        notes.append((T.PL_DEST_NEWER if sm["preview"] else T.PL_DEST_NEWER_DONE).format(n=sm["dest_newer_n"]))
    if not sm["preview"]:
        if sm["removed_n"]:
            notes.append(T.PL_REMOVED.format(n=sm["removed_n"]))
        if sm["keep"]:
            notes.append(T.PL_KEEP_NOTE_RESULT)
        elif sm["remaining_files"]:
            notes.append(T.PL_REMAINING.format(files=sm["remaining_files"], size=_size(sm["remaining_bytes"])))
        if sm["unreadable"]:
            notes.append(T.PL_UNREAD.format(n=sm["unreadable"]))
    if sm["gather"]:
        if sm["gather_blocked"]:
            notes.append(T.PL_GATHER_BLOCKED)
        elif not sm["preview"] and sm["gather_folder"]:
            notes.append(T.PL_GATHER_RESULT.format(folder=sm["gather_folder"], n=sm["gather_n"]))
        if sm["gather_guide_error"]:
            notes.append(T.PL_GATHER_GUIDE_ERROR)
    if sm["failed_n"] and not sm["preview"]:
        notes.append(T.PL_RETRY_NOTE)
    return notes


def place_confirm_text(rep: Any, keep: bool, report_path: str) -> str:
    """'놓기' 확인창 본문(미리보기 결과 기준). 복사만 하기이면 지우는 줄이 바뀐다."""
    sm = place_summary(rep)
    if keep:
        delete = T.PL_CONFIRM_KEEP
    else:
        parts = []
        if sm["move_n"]:  # 이동만이면 '지우는 파일: 없음' 대신 이동 안내, 섞이면 이동과 삭제를 둘 다 보인다
            parts.append(T.PL_CONFIRM_MOVE.format(n=sm["move_n"]))
        if sm["delete_n"]:
            parts.append(T.PL_CONFIRM_DELETE.format(n=sm["delete_n"], size=_size(sm["delete_bytes"])))
        delete = "\n".join(parts) or T.PL_CONFIRM_DELETE_NONE
    return T.PL_CONFIRM.format(n=sm["placed_n"], size=_size(sm["placed_bytes"]), over=sm["overwrite_n"], delete=delete,
                               manual=sm["manual_n"], report=report_path)
