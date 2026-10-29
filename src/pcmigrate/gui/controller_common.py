"""컨트롤러가 쓰는 값 객체·오류 변환·경로 보조 함수(stage43 분리). controller.py 가 이름을 모두 다시 내보낸다."""
from __future__ import annotations

import errno
import inspect
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import api as real_api, errorlog
from ..manifest import ManifestError
from ..transfer import DriveDisconnected, TransferError
from . import texts as T


def _home_aliases() -> list[tuple[str, str]]:
    """(실제 경로, 대신 보여 줄 환경변수 표시). 긴 경로부터 바꿔야 하므로 길이 순으로 정렬한다."""
    pairs: list[tuple[str, str]] = []
    for var in ("LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "USERPROFILE"):
        val = os.environ.get(var)
        if val and len(val) > 3:
            pairs.append((val, f"%{var}%"))
    home = os.path.expanduser("~")
    if home and home != "~" and len(home) > 3:
        pairs.append((home, "%USERPROFILE%"))
    return sorted(set(pairs), key=lambda t: -len(t[0]))


def anonymize_paths(text: str) -> str:
    """오류 보고서에 들어가는 개인 경로(내 사용자 폴더 등)를 %USERPROFILE% 같은 표시로 바꾼다(대소문자 무시, 슬래시 방향 무관)."""
    for real, alias in _home_aliases():
        for variant in {real, real.replace("\\", "/"), real.replace("/", "\\")}:
            if variant:
                text = re.sub(re.escape(variant), lambda _m, a=alias: a, text, flags=re.IGNORECASE)
    return text


@dataclass(frozen=True)
class DriveEntry:
    """드라이브 선택 콤보에 보여 줄 한 줄(읽기 전용 조회 결과)."""
    root: str  # 예: E:\
    kind: str  # removable | fixed | network | cdrom | ramdisk | unknown
    free_text: str = ""


class GuardError(Exception):
    """안전 가드 위반(예: 미리보기 없이 복원, 동의 없이 민감 항목 선택). 메시지는 사용자에게 그대로 보여 준다."""


@dataclass
class FriendlyError:
    title: str
    what: str  # 무엇이 잘못됐는지
    how: str  # 어떻게 하면 되는지
    detail: str = ""  # traceback(접힌 상세 보기에만)
    log_path: str = ""  # 오류 로그 파일(기록하지 못했으면 빈 문자열)
    report: str = ""  # '오류 내용 복사' 로 클립보드에 넣을 전체 텍스트(시각/버전/실행 화면/예외/traceback)

    @property
    def message(self) -> str:
        return f"{self.what}\n{self.how}"


_WIN_PATH_MISSING = {2, 3, 15}
_WIN_PERMISSION = {5}
_WIN_DISK_FULL = {112, 39}
_WIN_DISCONNECTED = {21, 53, 55, 59, 64, 67, 121, 1167, 1231}
_WIN_IN_USE = {32, 33}


def _os_error_texts(exc: OSError) -> tuple[str, str]:
    path = exc.filename if isinstance(getattr(exc, "filename", None), str) else ""
    suffix = f": {path}" if path else ""
    win = getattr(exc, "winerror", None)
    code = exc.errno
    if win in _WIN_IN_USE:
        what, how = T.ERR_IN_USE
    elif isinstance(exc, PermissionError) or win in _WIN_PERMISSION or code in (errno.EACCES, errno.EPERM):
        what, how = T.ERR_PERMISSION
    elif win in _WIN_DISK_FULL or code == errno.ENOSPC:
        what, how = T.ERR_DISK_FULL
    elif win in _WIN_DISCONNECTED or code in (errno.ENODEV, errno.ENXIO, errno.EIO):
        what, how = T.ERR_DISCONNECTED
    elif isinstance(exc, FileNotFoundError) or win in _WIN_PATH_MISSING or code == errno.ENOENT:
        what, how = T.ERR_PATH_MISSING
    else:
        what, how = T.ERR_OS_GENERIC
    return what.format(path=suffix), how


def _api_how(message: str) -> str:
    if "--allow-secrets" in message or "비밀 정보" in message:
        return T.ERR_API_SECRET
    if message.startswith("규칙 오류"):
        return T.ERR_API_RULES
    if message.startswith("패키지 검증 실패") or "해시" in message:
        return T.ERR_API_PACKAGE
    if message.startswith("선택 파일 오류"):
        return T.ERR_API_SELECTION
    return T.ERR_API_HOW


def _export_error_texts(message: str) -> tuple[str, str] | None:
    """내보내기 때 자주 만나는 두 가지(같은 이름으로 이미 실행 중 / 만든 파일 재검사 실패)를 쉬운 말로 바꾼다."""
    if "내보내기가 이미 실행 중" in message:
        what, how = T.ERR_EXPORT_LOCK
        m = re.search(r"같은 출력 파일\((.+?)\)", message)
        return what.format(where=f"({m.group(1)})" if m else ""), how
    if ".part.failed" in message or "다시 검사했더니 문제가 있어" in message:
        what, how = T.ERR_EXPORT_CHECK_FAILED
        m = re.search(r"임시 파일을 (.+?) 로 보존", message)
        return what.format(kept=T.ERR_EXPORT_KEPT.format(path=m.group(1)) if m else ""), how
    return None


def friendly_error(exc: BaseException, tb: str = "") -> FriendlyError:
    """예외를 '무엇이 잘못됐는지 / 어떻게 하면 되는지' 두 줄로. traceback 은 detail 에만 둔다."""
    if isinstance(exc, real_api.ApiError):
        special = _export_error_texts(exc.message)
        if special:
            return FriendlyError(T.ERR_TITLES["api"], special[0], special[1], tb)
        what = T.friendly_detail("\n".join([*exc.lines, exc.message]))
        return FriendlyError(T.ERR_TITLES["api"], what, _api_how(exc.message), tb)
    if isinstance(exc, DriveDisconnected):
        return FriendlyError(T.TR_DISCONNECTED_TITLE, T.TR_DISCONNECTED, T.TR_RECONNECT_NOTE, tb)
    if isinstance(exc, ManifestError):
        return FriendlyError(T.ERR_TITLES["api"], T.friendly_detail(str(exc)), T.ERR_API_HOW, tb)
    if isinstance(exc, TransferError):
        what, how = T.ERR_TRANSFER
        return FriendlyError(T.ERR_TITLES["transfer"], what.format(msg=T.friendly_detail(str(exc))), how, tb)
    if isinstance(exc, OSError):
        what, how = _os_error_texts(exc)
        return FriendlyError(T.ERR_TITLES["os"], what, how, tb)
    if isinstance(exc, GuardError):
        return FriendlyError(T.ERR_TITLES["guard"], str(exc), "", tb)
    what, how = T.ERR_UNEXPECTED
    head = f"{type(exc).__name__}: {exc}"  # 영문 예외 이름은 본문이 아니라 '상세 보기' 안에만 둔다
    return FriendlyError(T.ERR_TITLES["unexpected"], what, how, f"{head}\n{tb}" if tb else head)


RECEIVED_PREFIX = "PC이전_받은파일"  # lan_link.default_receive_dir 가 만드는 폴더 이름의 앞부분
RECEIVED_DEPTH = 9  # 받은 폴더 안에서 설정 묶음을 찾는 깊이(<드라이브>\<경로> 구조가 그대로 들어 있어 깊다)
RECEIVED_MAX_DIRS = 3000  # 받은 폴더 하나에서 훑는 폴더 수 상한


def norm_path(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _supported(fn: Callable, **kw) -> dict:
    """fn 이 받는 키워드 인자만 남긴다(진행 정보 같은 선택 인자를 가짜/예전 api 에도 안전하게 넘기기 위해)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kw
    return {k: v for k, v in kw.items() if k in params}


def _accepts(fn: Callable, name: str) -> bool:
    """fn 이 키워드 인자 name 을 받는가(**kwargs 도 받는 것으로 본다). 새 옵션을 아직 지원하지 않는 api 에 몰래 빠뜨리지 않으려고 쓴다."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _meta_value(res: Any, key: str) -> Any:
    """결과(ImportResult/Report/ImportPlan)와 그 안쪽 리포트의 meta 에서 key 값을 찾는다(없으면 None)."""
    seen = [res, getattr(res, "report", None), getattr(res, "plan", None)]
    for obj in seen:
        if obj is None:
            continue
        for attr in ("meta", "manifest"):
            d = getattr(obj, attr, None)
            if isinstance(d, dict) and d.get(key):
                return d[key]
    return None


def _count_of(value: Any) -> int:
    """개수(정수) 또는 목록/딕셔너리의 길이를 정수로."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except TypeError:
        return 0


def _final_status(res: Any) -> tuple[dict | None, dict | None, str]:
    """작업 결과에서 현황 카드에 넣을 확정 개수/문제 목록/요약을 뽑는다. 알 수 없는 결과면 (None, None, '')."""
    try:
        if getattr(res, "report", None) is not None:  # 복원 결과
            res = res.report
        if hasattr(res, "results") and hasattr(res, "to_dict"):  # copier.Report
            c = res.to_dict()["summary"]
            counts = {"ok": c.get("copied", 0) + c.get("verified", 0) + c.get("pending_verify", 0) + c.get("would_copy", 0)
                      + c.get("would_overwrite", 0),
                      "skipped": c.get("skipped_identical", 0) + c.get("cloud_only", 0), "unreadable": 0, "failed": 0}
            problems: dict[str, list[str]] = {"skipped": [], "unreadable": [], "failed": []}
            for r in res.results:
                if r.status == "cloud_only":
                    problems["skipped"].append(r.src)
                elif r.status in ("failed", "mismatch", "missing", "rejected"):
                    kind = "unreadable" if r.reason == "in_use" else "failed"
                    counts[kind] += 1
                    problems[kind].append(r.src)
            return counts, problems, T.CARD_SUMMARY_FILES.format(n=sum(counts.values()))
        if isinstance(getattr(res, "manifest", None), dict):  # 패키지 내보내기
            m = res.manifest
            un = [f for f in m["failed"] if f.get("reason")]
            hard = [f for f in m["failed"] if not f.get("reason")]
            counts = {"ok": len(m["files"]), "skipped": len(m["skipped"]), "unreadable": len(un), "failed": len(hard)}
            problems = {"skipped": [x.get("path", "") for x in m["skipped"]], "unreadable": [f["src"] for f in un],
                        "failed": [f["src"] for f in hard]}
            return counts, problems, T.CARD_SUMMARY_FILES.format(n=len(m["files"]))
        if hasattr(getattr(res, "plan", None), "items"):  # 복원 미리보기
            st = [it["status"] for it in res.plan.items]
            rej = [it.get("target", "") for it in res.plan.items if it["status"] == "rejected"]
            counts = {"ok": sum(s in ("would_copy", "would_overwrite") for s in st),
                      "skipped": sum(s == "skipped_identical" for s in st), "unreadable": 0,
                      "failed": len(res.plan.problems) + len(rej)}
            return (counts, {"failed": [str(p) for p in res.plan.problems] + rej},
                    T.CARD_SUMMARY_FILES.format(n=len(st)))
        if hasattr(res, "program_count"):
            return None, None, T.CARD_SUMMARY_PROGRAMS.format(n=res.program_count)
    except Exception as e:  # noqa: BLE001 - 요약을 못 만들어도 작업 결과 처리에는 영향이 없어야 한다
        errorlog.record(e, "GUI 요약")  # 삼키되 버그가 숨지 않게 로그에는 남긴다
    return None, None, T.CARD_SUMMARY_SIMPLE


@dataclass
class CandRow:
    key: tuple
    programs: str
    path: str
    kind: str
    size: str
    grade: str
    reason: str
    weak: bool
    secret: list = field(default_factory=list)
    selectable: bool = True
    badge: str = ""  # 프로그램 설정 찾기의 분류 배지(임시 저장물일 수 있음/로그/라이선스)
    license_only: bool = False
    size_eff: str = ""  # 표에 보이는 크기: 내보낼 때 기본으로 빠지는 임시 저장물(캐시)을 뺀 값
    files_eff: str = ""  # 표에 보이는 파일 수(캐시 제외)
    detail_cache: str = ""  # 캐시가 있으면 '전체 X 중 캐시 Y' 안내(상세줄용)
    install: bool = False  # 프로그램 설치 폴더 안의 설정 파일 후보(설치 폴더 설정)
    badge_note: str = ""  # 배지의 전체 설명(툴팁/상세줄용)
    self_transfer: dict | None = None  # 자체 이전 필요 프로그램의 후보면 {id, name, category, reason, todo}: 표는 메인 표 대신 접힌 구역에 보인다(선택은 막지 않는다)


@dataclass
class PathCheck:
    ok: bool
    lines: list  # [(ok, 문장)]
    info: list = field(default_factory=list)  # 대상 드라이브 종류/파일 시스템/여유 공간 안내(문장)
    warnings: list = field(default_factory=list)  # FAT32/exFAT/네트워크 경고(문장)


@dataclass
class AutoStep:
    """'추천대로 한 번에' 진행 화면의 한 줄(단계)."""
    key: str
    title: str
    state: str = "todo"  # todo | doing | waiting | done | skipped | warn | stopped
    note: str = ""


@dataclass
class AutoRun:
    """'추천대로 한 번에' 진행 상태(tkinter 와 무관). status: running | waiting | done | stopped."""
    flow: str  # old | new | transfer
    steps: list = field(default_factory=list)
    status: str = "running"
    gate: str = ""  # 사람의 확인/입력을 기다리는 지점: export_confirm | pick_package | restore | questions | folders | transfer_confirm | verify_confirm
    what: str = ""  # 멈춘 이유(쉬운 말)
    how: str = ""  # 어떻게 하면 되는지
    warning: str = ""  # 끝났지만 확인할 것
    manual: str = ""  # '직접 진행하기'로 갈 화면
    candidates: list = field(default_factory=list)  # 찾은 설정 묶음 파일들
    export_path: str = ""
    phase: str = ""  # 대용량: preview | copy | verify
    stop_requested: bool = False
    by_user: bool = False

    def step(self, key: str) -> AutoStep | None:
        return next((x for x in self.steps if x.key == key), None)

    @property
    def active(self) -> bool:
        return self.status in ("running", "waiting")


AUTO_FLOWS = ("old", "new", "transfer")
_AUTO_KINDS = {"scan_done", "checklist_done", "export_done", "preview_done", "install_done", "import_done", "transfer_done", "error", "cancelled"}
