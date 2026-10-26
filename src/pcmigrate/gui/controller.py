"""GUI 컨트롤러: api 함수 호출, 스레드 관리, 안전 가드. tkinter 에 의존하지 않는다(가짜 api 로 테스트 가능).

안전 원칙(CLI 와 같은 보수적 기본값):
- 원본은 읽기 전용, 쓰기 전에는 반드시 미리보기와 확인.
- 규칙 없는 프로그램은 사용자가 고른 것만 포함(자동 포함 없음). 민감한 정보 가능성 항목은 명시 동의 후에만 선택.
- 가드 위반은 GuardError 로 막는다(뷰가 버튼을 막아 두더라도 컨트롤러가 한 번 더 막는다).
- 로그에는 설치 프로그램 목록/경로를 남기지 않고 이벤트 종류와 개수만 남긴다.
화면에 보이는 문구는 texts.py 에 있다.
"""
from __future__ import annotations

import errno
import inspect
import logging
import os
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import api as real_api
from .. import errorlog
from ..cancel import CancelToken
from ..checklist import write_output
from .. import fs
from ..discover import fmt_size, items_from_selection
from ..install_check import INSTALLED, import_command, newly_installed
from ..manifest import DEFAULT_NAME as MANIFEST_NAME
from ..manifest import ManifestError
from ..package import inspect_savepoint
from ..progress import ProgressInfo, fmt_bytes, format_info
from ..rules import default_rules_dir
from ..scan import skipped_links_view
from ..transfer import DriveDisconnected, TransferError
from . import state as S
from . import texts as T
from .runner import Runner
from .status import StatusModel


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
    except Exception:  # noqa: BLE001 - 요약을 못 만들어도 작업 결과 처리에는 영향이 없어야 한다
        pass
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


class Controller:
    """화면과 api 사이의 중간층. 이벤트는 listener(kind, payload) 로 전달된다."""

    def __init__(self, api_mod: Any = None, deps: Any = None, runner: Runner | None = None,
                 rules_dir: str | Path | None = None, logger: logging.Logger | None = None,
                 make_items: Callable | None = None, record_error: Callable | None = None) -> None:
        self.api = api_mod or real_api
        self.deps = deps
        self.runner = runner or Runner()
        self.state = S.WizardState(rules_dir=str(rules_dir or default_rules_dir()))
        self.log = logger or logging.getLogger("pcmigrate.gui")
        self.make_items = make_items or items_from_selection
        self.record_error = record_error or errorlog.record  # (예외, 실행 화면/작업 이름, traceback) -> 로그 파일 경로 | None
        self.status = StatusModel()  # 현황 카드의 상태(tkinter 와 무관)
        self.last_error: FriendlyError | None = None
        self.listener: Callable[[str, Any], None] | None = None
        self.cancel_token: CancelToken | None = None
        self.can_cancel = False
        self.checklist_marks: set[tuple[str, str]] = set()  # 체크한 항목(탭, 문구): 화면을 오가도 유지
        self._dest_cache: dict[str, Any] = {}
        self.roadmap_marks: set[str] = set()  # 전체 이전 순서에서 체크한 항목(창을 닫았다 열어도 유지)
        self._in_listener_error = False
        self.auto: AutoRun | None = None  # '추천대로 한 번에' 진행 상태(없으면 None)
        self.drive_lister: Callable[[], list] | None = None  # 시험에서 가짜 드라이브 목록을 넣는 자리
        if hasattr(self.runner, "on_callback_error"):
            self.runner.on_callback_error = self._callback_failed

    # ------------------------------------------------------------------ 경로 편의(읽기 전용 조회)
    def list_drives(self) -> list[DriveEntry]:
        """연결된 드라이브 목록(외장/이동식 먼저). Windows 가 아니거나 조회에 실패하면 빈 목록."""
        if self.drive_lister is not None:
            return list(self.drive_lister())
        if os.name != "nt":
            return []
        out: list[DriveEntry] = []
        try:
            import ctypes
            from .. import fs
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if mask & (1 << i):
                    root = f"{chr(65 + i)}:\\"
                    kind = fs.drive_kind(root)
                    if kind == "cdrom":
                        continue
                    free = fs.free_space(root) if kind != "network" else None
                    out.append(DriveEntry(root, kind, fmt_bytes(free) if free is not None else ""))
        except (AttributeError, OSError):
            return out
        out.sort(key=lambda d: (d.kind != "removable", d.root))
        return out

    def default_desktop(self) -> str:
        """바탕화면 폴더(OneDrive 로 옮겨진 경우 포함). 찾지 못하면 사용자 폴더 아래 Desktop."""
        home = os.path.expanduser("~")
        cands = []
        for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            base = os.environ.get(var)
            if base:
                cands.append(os.path.join(base, "Desktop"))
        cands.append(os.path.join(home, "Desktop"))
        for c in cands:
            if os.path.isdir(c):
                return c
        return cands[-1]

    def default_downloads(self) -> str:
        """다운로드 폴더(OneDrive 로 옮겨진 경우 포함). 찾지 못하면 사용자 폴더 아래 Downloads."""
        home = os.path.expanduser("~")
        cands = []
        for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            base = os.environ.get(var)
            if base:
                cands.append(os.path.join(base, "Downloads"))
        cands.append(os.path.join(home, "Downloads"))
        for c in cands:
            if os.path.isdir(c):
                return c
        return cands[-1]

    def find_packages(self, limit: int = 30) -> list[str]:
        """바탕화면·다운로드·연결된 외장 드라이브(드라이브 맨 위와 그 바로 아래 폴더)에서 pcmigrate-settings*.zip 을 찾는다(읽기만).
        가장 최근 파일이 먼저. 같은 파일은 한 번만."""
        pat = re.compile(r"^" + re.escape(T.EXP_DEFAULT_NAME.rsplit(".", 1)[0]) + r".*\.zip$", re.IGNORECASE)
        dirs: list[tuple[str, bool]] = [(self.default_desktop(), False), (self.default_downloads(), False)]
        system = os.path.normcase(os.environ.get("SystemDrive") or "C:").rstrip("\\/")
        try:
            for d in self.list_drives():
                is_system = d.kind == "fixed" and os.path.normcase(d.root).rstrip("\\/") == system
                if d.kind in ("removable", "fixed", "unknown") and not is_system:  # 시스템 드라이브는 바탕화면/다운로드로 이미 찾는다
                    dirs.append((d.root, True))
        except Exception:  # noqa: BLE001 - 드라이브 조회 실패는 찾기만 줄인다
            pass
        found: dict[str, tuple[float, str]] = {}

        def scan(folder: str, deeper: bool) -> None:
            try:
                with os.scandir(folder) as it:
                    entries = list(it)
            except OSError:
                return
            for e in entries:
                try:
                    if e.is_file() and pat.match(e.name):
                        key = os.path.normcase(os.path.realpath(e.path))
                        found.setdefault(key, (e.stat().st_mtime, e.path))
                    elif deeper and e.is_dir(follow_symlinks=False) and not e.name.startswith(("$", ".")):
                        scan(e.path, False)
                except OSError:
                    continue

        for folder, deeper in dirs:
            if folder and os.path.isdir(folder):
                scan(folder, deeper)
        ordered = sorted(found.values(), key=lambda t: -t[0])
        return [path for _m, path in ordered[:limit]]

    def default_export_path(self) -> str:
        """구 PC 의 설정 묶음 파일 저장 기본 위치: 바탕화면의 pcmigrate-settings.zip."""
        return os.path.join(self.default_desktop(), T.EXP_DEFAULT_NAME)

    def find_manifest(self, *folders: str) -> str:
        """확인용 목록 파일을 주어진 폴더(와 그 바로 위 폴더)에서 찾는다. 없으면 빈 문자열(읽기만 한다)."""
        seen: list[str] = []
        for f in folders:
            if f and os.path.isdir(f):
                seen += [f, os.path.dirname(os.path.normpath(f))]
        for folder in dict.fromkeys(seen):
            cand = os.path.join(folder, MANIFEST_NAME)
            if os.path.isfile(cand):
                return cand
        return ""

    # ------------------------------------------------------------------ 이벤트/실행
    def emit(self, kind: str, payload: Any = None) -> None:
        self.log.info("event %s", kind)  # 내용(목록/경로)은 로그에 남기지 않는다
        if self.listener:
            try:
                self.listener(kind, payload)
            except Exception as e:  # noqa: BLE001 - 화면 갱신 오류가 작업 상태 정리를 막지 않게(이후 이벤트는 계속 전달)
                self._callback_failed(e, traceback.format_exc(), kind)
        if self.auto is not None and kind in _AUTO_KINDS:
            self._auto_event(kind, payload)

    def _callback_failed(self, exc: BaseException, tb: str = "", kind: str = "") -> None:
        """화면/콜백에서 난 예외를 로그에 남기고 오류 대화상자로 알린다(재귀 방지)."""
        self.log.error("callback failed: %s (%s)", kind or "task", type(exc).__name__)
        if self._in_listener_error or not self.listener:
            self.report_error(exc, kind or T.TASK_UI, tb)
            return
        self._in_listener_error = True
        try:
            fe = self.report_error(exc, kind or T.TASK_UI, tb)
            try:
                self.listener("error", fe)
            except Exception:  # noqa: BLE001 - 대화상자마저 실패해도 앱은 계속
                pass
        finally:
            self._in_listener_error = False

    def pump(self) -> int:
        return self.runner.pump()

    def report_error(self, exc: BaseException, context: str = "", tb: str = "") -> FriendlyError:
        """오류를 로그 파일에 기록하고 오류 대화상자용 FriendlyError 를 만든다(로그에 설치 목록/파일 내용은 쓰지 않는다)."""
        fe = friendly_error(exc, tb)
        where = f"GUI {self.state.screen}" + (f" / {context}" if context else "")
        path = None
        try:
            path = self.record_error(exc, where, tb or None)
        except Exception:  # noqa: BLE001 - 로그 실패가 오류 표시를 막으면 안 된다
            path = None
        fe.log_path = str(path) if path else ""
        fe.report = anonymize_paths(errorlog.format_block(exc, where, tb or None))
        fe.detail = anonymize_paths(fe.detail)
        self.last_error = fe
        return fe

    def go(self, screen: str) -> None:
        if screen != S.AUTO:
            self.auto = None  # 진행 화면을 떠나면 '한 번에' 진행은 끝난 것으로 본다(이어서 하려면 직접 진행)
        self.state.screen = screen
        self.emit("screen", screen)

    def system_info(self):
        return self.api.system_info()

    def _run(self, label: str, fn: Callable[[Callable[[str], None], Any], Any], done: Callable[[Any], None],
             cancellable: bool = False, on_fail: Callable[[], None] | None = None) -> None:
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        token = CancelToken() if cancellable else None
        self.cancel_token, self.can_cancel = token, cancellable
        self.state.busy, self.state.busy_label = True, label
        self.log.info("task start: %s", label)
        self.status.begin(label)
        self.emit("busy", label)
        if label in T.PROGRESS:
            self.emit("progress", T.PROGRESS[label])  # 작업 이름과 예상 시간을 바로 보여 준다
        self.emit("status", self.status)

        def finish() -> None:
            self.state.busy, self.state.busy_label = False, ""
            self.cancel_token, self.can_cancel = None, False

        def on_done(res: Any) -> None:
            finish()
            counts, problems, summary = _final_status(res)
            self.status.finish(counts, problems, summary)
            self.emit("busy", None)
            self.emit("status", self.status)
            done(res)

        def on_error(exc: BaseException, tb: str) -> None:
            finish()
            self.log.error("task failed: %s (%s)", label, type(exc).__name__)
            fe = self.report_error(exc, label, tb)
            if on_fail:
                on_fail()
            self.status.failed(fe.what)
            self.emit("busy", None)
            self.emit("status", self.status)
            self.emit("error", fe)

        def on_cancel() -> None:
            finish()
            self.log.info("task cancelled: %s", label)
            if on_fail:
                on_fail()
            self.status.cancelled()
            self.emit("busy", None)
            self.emit("status", self.status)
            self.emit("cancelled", label)

        def on_progress(m: Any) -> None:
            if isinstance(m, ProgressInfo):  # 바이트/속도/남은 시간(진행 막대용)
                self.state.transfer_progress = m
                self.status.info(m)
                self.emit("progress_info", m)
                text = T.friendly_detail(format_info(m))
                self.emit("progress", text + (f"\n{T.ICON_WARN} {m.hint}" if m.hint else ""))
            else:
                self.status.message(T.friendly_detail(m))
                self.emit("progress", T.friendly_detail(m))

        self.runner.submit(lambda progress: fn(progress, token), on_done=on_done, on_error=on_error,
                           on_cancel=on_cancel, on_progress=on_progress)

    def toggle_roadmap(self, key: str) -> bool:
        """전체 이전 순서의 항목 체크를 뒤집고 새 상태를 돌려준다."""
        valid = {k for k, *_ in (*T.ROADMAP_OLD, *T.ROADMAP_NEW)}
        if key not in valid:
            raise GuardError(T.G_NOT_IN_LIST)
        if key in self.roadmap_marks:
            self.roadmap_marks.discard(key)
            return False
        self.roadmap_marks.add(key)
        return True

    def cancel(self) -> bool:
        """취소 가능한 작업(규칙에 없는 프로그램 찾기, 대용량 복사)에만 효과가 있다."""
        if self.cancel_token is not None:
            self.cancel_token.cancel()
            self.emit("progress", T.CANCELLING)
            return True
        return False

    # ------------------------------------------------------------------ 구 PC 1) 조사
    def start_scan(self) -> None:
        def work(progress, token):
            return self.api.run_scan(self.state.rules_dir, self.deps,
                                     **_supported(self.api.run_scan, progress_info=progress))

        def done(res):
            self.state.scan = res
            self.emit("scan_done", res)
        self._run(T.TASK_SCAN, work, done)

    # ------------------------------------------------------------------ 구 PC 2) 규칙에 없는 프로그램
    def start_discover(self, min_size_kb: int = 0) -> None:
        def work(progress, token):
            return self.api.run_discover(self.state.rules_dir, min_size_kb=min_size_kb, progress=progress,
                                         deps=self.deps, cancel=token,
                                         **_supported(self.api.run_discover, progress_info=progress))

        def done(res):
            self.state.discovery = res
            self.state.selected = set()  # 자동 선택 없음
            self.state.secrets_ack = False
            self.emit("discover_done", res)
        self._run(T.TASK_DISCOVER, work, done, cancellable=True)

    def candidate_rows(self) -> list[CandRow]:
        d = self.state.discovery
        if d is None:
            return []
        rows = []
        for c in d.candidates():
            link = c.link
            rows.append(CandRow(
                key=c.key, programs=", ".join(link.programs) if link else T.NO_PROGRAM, path=c.path,
                kind=T.KIND_FOLDER if c.is_dir else T.KIND_FILE, size=fmt_size(c), grade=link.grade if link else "없음",
                reason=link.reason if link else "", weak=bool(link and link.weak), secret=list(c.secret),
                selectable=(not c.secret) or self.state.secrets_ack))
        return rows

    def _row(self, key: tuple) -> CandRow:
        for r in self.candidate_rows():
            if r.key == key:
                return r
        raise GuardError(T.G_NOT_IN_LIST)

    def set_secrets_ack(self, on: bool) -> None:
        self.state.secrets_ack = bool(on)
        if not on:  # 동의를 거두면 민감한 정보 가능성 항목은 모두 선택 해제
            for r in self.candidate_rows():
                if r.secret:
                    self.state.selected.discard(r.key)
        self.emit("selection_changed")

    def set_selected(self, key: tuple, on: bool) -> None:
        row = self._row(key)
        if on and row.secret and not self.state.secrets_ack:
            raise GuardError(T.G_SECRET_SELECT)
        (self.state.selected.add if on else self.state.selected.discard)(key)
        self.emit("selection_changed")

    def selected_rows(self) -> list[CandRow]:
        return [r for r in self.candidate_rows() if r.key in self.state.selected]

    def _target_rows(self, keys) -> list[CandRow]:
        """일괄 선택/해제 대상: 화면에 보이는 키(keys). None 이면 후보 전체. 목록에 없는 키는 무시한다."""
        rows = self.candidate_rows()
        if keys is None:
            return rows
        wanted = set(keys)
        return [r for r in rows if r.key in wanted]

    def select_all_candidates(self, keys=None) -> tuple[int, int]:
        """보이는 후보를 모두 선택한다. 민감한 정보 가능성 항목은 동의 여부와 상관없이 건너뛴다(직접 골라야 함).
        반환: (새로 선택된 개수, 건너뛴 민감 항목 개수)."""
        added = skipped = 0
        for r in self._target_rows(keys):
            if r.secret:
                skipped += 1
                continue
            if r.key not in self.state.selected:
                self.state.selected.add(r.key)
                added += 1
        self.emit("selection_changed")
        return added, skipped

    def clear_all_candidates(self, keys=None) -> int:
        """보이는 후보의 선택을 모두 해제한다(민감 항목 포함). 반환: 해제된 개수."""
        n = 0
        for r in self._target_rows(keys):
            if r.key in self.state.selected:
                self.state.selected.discard(r.key)
                n += 1
        self.emit("selection_changed")
        return n

    # ------------------------------------------------------------------ 구 PC 3) 해야 할 일 목록
    def start_checklist(self) -> None:
        """항상 '모두 보기' 로 만들고, 화면에서 관련 없는 경고를 숨기거나 보인다(조사를 다시 하지 않는다)."""
        def work(progress, token):
            return self.api.run_checklist(self.state.rules_dir, show_all=True, fmt="md", deps=self.deps,
                                          **_supported(self.api.run_checklist, progress_info=progress))

        def done(res):
            self.state.checklist = res
            self.checklist_marks = set()
            self.emit("checklist_done", res)
        self._run(T.TASK_CHECKLIST, work, done)

    def checklist_hidden_count(self) -> int:
        r = self.state.checklist
        if r is None:
            return 0
        cl = r.checklist
        return sum(1 for line in [*cl.login, *cl.data] if line.endswith(T.CL_HIDDEN_MARK))

    def checklist_tabs(self, show_all: bool = False, query: str = "") -> dict[str, list[str]]:
        r = self.state.checklist
        if r is None:
            return {}
        cl = r.checklist

        def visible(lines: list[str]) -> list[str]:
            out = [l for l in lines if show_all or not l.endswith(T.CL_HIDDEN_MARK)]
            q = query.strip().lower()
            return [l for l in out if q in l.lower()] if q else out

        hidden = 0 if show_all else self.checklist_hidden_count()
        summary = [
            T.CL_SUMMARY_INSTALL.format(a=len(cl.install), b=len(cl.winget_cmd), c=len(cl.winget_auto)),
            T.CL_SUMMARY_LOGIN.format(n=len(visible(cl.login))), T.CL_SUMMARY_DATA.format(n=len(visible(cl.data))),
            T.CL_SUMMARY_REF.format(n=len(cl.reference))]
        if hidden:
            summary.append(T.CL_SUMMARY_OMITTED.format(n=hidden))
        if cl.discovery is not None:
            n, m = cl.discovery
            summary.append(T.CL_SUMMARY_DISC.format(n=n, m=T.CL_SUMMARY_DISC_M.format(m=m) if m is not None else ""))
        summary.append(T.CL_SUMMARY_LICENSE)
        tabs = {T.CL_TABS[0]: summary,
                T.CL_TABS[1]: [*T.CL_GUIDE, *visible(cl.winget_cmd), *visible(cl.install)],
                T.CL_TABS[2]: visible(cl.login), T.CL_TABS[3]: visible(cl.data), T.CL_TABS[4]: visible(cl.reference)}
        return tabs

    CHECKLIST_PLAIN = {T.CL_TABS[0]: "all", T.CL_TABS[4]: "all", T.CL_TABS[1]: len(T.CL_GUIDE)}  # 체크박스 없는 줄 수

    def checklist_items(self, tab: str, show_all: bool = False, query: str = "") -> list[tuple[str, bool, bool]]:
        """(문구, 체크박스가 있는 항목인지, 체크됨) 목록."""
        lines = self.checklist_tabs(show_all, query).get(tab, [])
        plain = self.CHECKLIST_PLAIN.get(tab, 0)
        out = []
        for i, line in enumerate(lines):
            has_box = not (plain == "all" or (isinstance(plain, int) and i < plain))
            out.append((line, has_box, has_box and (tab, line) in self.checklist_marks))
        return out

    def toggle_check(self, tab: str, text: str) -> bool:
        key = (tab, text)
        if key in self.checklist_marks:
            self.checklist_marks.discard(key)
        else:
            self.checklist_marks.add(key)
        self.emit("checklist_marks")
        return key in self.checklist_marks

    def checklist_progress(self, show_all: bool = False) -> tuple[int, int]:
        total = done = 0
        for tab in T.CL_TABS:
            for _text, has_box, checked in self.checklist_items(tab, show_all):
                total += has_box
                done += checked
        return done, total

    def checklist_text(self, fmt: str = "md", show_all: bool = False) -> str:
        """저장용 텍스트. 체크한 항목은 [x] 로 남는다."""
        if self.state.checklist is None:
            raise GuardError(T.G_NEED_CHECKLIST_FIRST)
        md = fmt == "md"
        lines = ["# PC 이전 체크리스트" if md else "PC 이전 체크리스트", ""]
        for tab in T.CL_TABS:
            lines.append(f"## {tab}" if md else f"=== {tab} ===")
            items = self.checklist_items(tab, show_all)
            for text, has_box, checked in items or [(T.CL_EMPTY, False, False)]:
                lines.append(f"- [{'x' if checked else ' '}] {text}" if has_box else text)
            lines.append("")
        lines.append(T.CL_SUMMARY_LICENSE)
        return "\n".join(lines) + "\n"

    def save_text(self, path: str, text: str) -> str:
        """텍스트 저장(기존 파일은 .bak 보존). 호출자는 저장 위치 확인 대화상자를 먼저 보여 줘야 한다."""
        if not path:
            raise GuardError(T.G_NEED_SAVE_PATH)
        try:
            write_output(Path(path), text)
        except OSError as e:
            raise real_api.ApiError("\n".join(real_api.format_write_error(path, e))) from e
        self.log.info("saved text file")
        return path

    # ------------------------------------------------------------------ 구 PC 4) 설정 패키지 만들기
    def export_reason(self, output: str) -> str:
        """패키지 만들기 버튼이 막혀 있는 이유(없으면 빈 문자열)."""
        if self.state.busy:
            return T.WHY_BUSY
        return "" if output.strip() else T.WHY_NEED_OUTPUT

    def export_resume_hint(self, output: str) -> str:
        """같은 저장 위치로 이어받을 수 있는 임시 파일이 있으면 안내 한 줄(없으면 빈 문자열). 읽기 전용."""
        if not output.strip():
            return ""
        try:
            info = inspect_savepoint(output.strip())
        except (OSError, ValueError):
            return ""
        if info is None:
            return ""
        if not info["usable"]:
            return T.EXP_RESUME_BROKEN.format(why=info["reason"])
        if not info["files"]:
            return ""
        return T.EXP_RESUME_HINT.format(n=info["files"], size=fmt_bytes(info["bytes"]))

    def start_export(self, output: str, write_confirmed: bool = False, resume: bool = True, stage_dir: str = "") -> None:
        if not write_confirmed:
            raise GuardError(T.G_NEED_EXPORT_CONFIRM)
        stage_dir = (stage_dir or "").strip()
        if stage_dir:
            if not os.path.isdir(stage_dir):
                raise GuardError(T.G_STAGE_MISSING.format(path=stage_dir))
            if not _accepts(self.api.run_export, "stage_dir"):  # 지원하지 않는 버전에서 조용히 무시하면 사용자가 모르게 다른 위치를 쓰게 된다
                raise GuardError(T.G_STAGE_UNSUPPORTED)
        if not output:
            raise GuardError(T.G_NEED_SAVE_PATH)
        if real_api.check_unexpanded(output=output):
            raise GuardError(T.G_OUTPUT_UNEXPANDED)
        if os.path.isdir(output):
            raise GuardError(T.G_OUTPUT_IS_DIR)
        sel = self.selected_rows()
        has_secret = any(r.secret for r in sel)
        if has_secret and not self.state.secrets_ack:
            raise GuardError(T.G_SECRET_NO_ACK)
        items = self.make_items({"format": 1, "candidates": [
            {"include": True, "root": k[0], "rel": k[1]} for k in sorted(self.state.selected)]}) if sel else []

        stage_kw = {"stage_dir": stage_dir} if stage_dir else {}

        def work(progress, token):
            return self.api.run_export(self.state.rules_dir, output, extra_items=items or None,
                                       allow_secrets=has_secret, deps=self.deps,
                                       **_supported(self.api.run_export, progress_info=progress, resume=resume), **stage_kw)

        def done(res):
            self.state.export_result, self.state.export_output = res, output
            self.emit("export_done", res)
        self._run(T.TASK_EXPORT, work, done)

    # ------------------------------------------------------------------ 신 PC
    def set_package(self, path: str) -> None:
        self.state.package = path
        self._reset_confirms()
        self.state.preview = None
        self._reset_install()
        self.emit("package_changed", path)

    # ------------------------------------------------------------------ 신 PC: 프로그램 설치 확인(읽기 전용 비교, 설치 명령은 실행하지 않음)
    def _reset_install(self) -> None:
        s = self.state
        s.install_check = s.install_prev = None
        s.install_package, s.install_command = "", ""
        s.install_skipped = False
        s.install_marks, s.install_new = set(), []

    def start_install_check(self) -> None:
        """묶음의 구 PC 프로그램과 이 PC의 설치 상태를 비교한다('다시 확인'도 같은 동작: 직전 결과와 비교해 새로 설치된 것을 센다)."""
        s = self.state
        if not s.package:
            raise GuardError(T.G_INSTALL_NEED_PACKAGE)
        package = s.package

        def work(progress, token):
            return self.api.run_install_check(package, deps=self.deps,
                                              **_supported(self.api.run_install_check, progress_info=progress))

        def done(res):
            prev = s.install_check if s.install_package == package else None
            s.install_prev = prev
            s.install_new = [i.name for i in newly_installed(prev, res)]
            s.install_check, s.install_package = res, package
            self.emit("install_done", res)
        self._run(T.TASK_INSTALL_CHECK, work, done)

    def install_needs_check(self) -> bool:
        """화면에 들어왔을 때 자동으로 확인을 시작해야 하는가(이 묶음 파일로 확인한 적이 없다)."""
        s = self.state
        return bool(s.package) and not s.busy and (s.install_check is None or s.install_package != s.package)

    def install_rows(self) -> list[dict]:
        r = self.state.install_check
        if r is None:
            return []
        rows = []
        for it in r.items:
            key = "installed_diff" if it.status == INSTALLED and it.version_diff else it.status
            version = T.INST_VERSION_DIFF.format(old=it.old_version, new=it.new_version) if it.version_diff else (
                (it.new_version if it.status == INSTALLED else "") or it.old_version)  # 애매함/설치 안 됨은 구 PC 버전
            rows.append({"name": it.name, "status": it.status, "label": T.INST_LABELS[key], "version": version,
                         "reason": it.reason, "hint": it.hint, "markable": it.needs_action, "diff": it.version_diff,
                         "mark": it.name in self.state.install_marks})
        return rows

    def install_summary_text(self) -> str:
        r = self.state.install_check
        if r is None:
            return T.INST_NONE
        c = r.counts()
        return T.INST_SUMMARY.format(total=len(r.items), installed=c["installed"], diff=r.version_diff_count, winget=c["winget"],
                                     manual=c["manual"], ambiguous=c["ambiguous"], not_needed=c["not_needed"])

    def install_notes(self) -> list[str]:
        """요약 아래에 보여 줄 안내 문장들(남은 개수, 다시 확인 결과, 설치 도구 상태, 애매함 설명)."""
        s = self.state
        r = s.install_check
        if r is None:
            return []
        out = [T.INST_LEFT.format(n=r.remaining) if r.remaining else T.INST_ALL_DONE]
        if s.install_prev is not None:
            out.append(T.INST_RECHECK_NEW.format(n=len(s.install_new), names=", ".join(s.install_new[:5])
                                                 + (" …" if len(s.install_new) > 5 else "")) if s.install_new
                       else T.INST_RECHECK_SAME)
        if not r.winget_ok:
            out.append(T.INST_WINGET_MISSING)
        if not r.has_winget_file:
            out.append(T.INST_NO_WINGET_FILE)
        if r.by_status("ambiguous"):
            out.append(T.INST_AMBIGUOUS_NOTE)
        return out

    def toggle_install_mark(self, name: str) -> bool:
        """'설치 끝냄' 표시(기억용). 설치 여부 판정에는 쓰지 않는다."""
        marks = self.state.install_marks
        marks.symmetric_difference_update({name})
        self.emit("install_marks", name)
        return name in marks

    def default_install_folder(self) -> str:
        return os.path.join(self.default_desktop(), T.INST_FOLDER_DEFAULT_NAME)

    def set_install_folder(self, path: str) -> None:
        self.state.install_folder = path
        self.emit("install_folder", path)

    def prepare_install_command(self) -> dict:
        """묶음의 winget.json 을 폴더로 꺼내고(같은 이름 파일은 .bak 으로 보존) 사용자가 직접 실행할 명령을 만든다. 명령은 실행하지 않는다."""
        s = self.state
        r = s.install_check
        if not s.package:
            raise GuardError(T.G_INSTALL_NEED_PACKAGE)
        if r is None:
            raise GuardError(T.G_INSTALL_NOT_YET)
        if not r.has_winget_file:
            raise GuardError(T.G_INSTALL_NO_WINGET)
        if not r.import_needed:
            raise GuardError(T.INST_COPY_NOTHING)
        folder = s.install_folder or self.default_install_folder()
        target, bak = self.api.run_winget_extract(s.package, folder)
        cmd = import_command(fs.plain_path_text(str(target)))
        s.install_command = cmd
        return {"command": cmd, "file": str(target), "backup": str(bak) if bak else "", "individual": list(r.individual_commands)}

    def install_next(self, skip: bool = False) -> None:
        """미리보기 화면으로. skip=True 는 '건너뛰기'(설치 확인 없이 진행했다는 표시만 남는다)."""
        self.state.install_skipped = bool(skip)
        self.go(S.NEW_PREVIEW)

    def _reset_confirms(self) -> None:
        s = self.state
        s.preview_checked = s.bak_understood = s.final_confirmed = s.extra_consent = False

    def start_preview(self) -> None:
        if not self.state.package:
            raise GuardError(T.G_NEED_PACKAGE)
        package = self.state.package

        def work(progress, token):
            return self.api.run_import(package, self.state.rules_dir, dry_run=True, deps=self.deps)

        def done(res):
            self.state.preview, self.state.preview_package = res, package
            self._reset_confirms()
            self.emit("preview_done", res)
        self._run(T.TASK_PREVIEW, work, done)

    def preview_rows(self) -> list[dict]:
        p = self.state.preview
        if p is None:
            return []
        return [{"status": T.PREV_STATUS.get(it["status"], it["status"]), "target": it["target"],
                 "backup": it.get("backup", ""), "extra": bool(it.get("extra")), "size": it.get("size", 0)}
                for it in p.plan.items]

    def preview_rejected_count(self) -> int:
        p = self.state.preview
        return sum(1 for it in p.plan.items if it.get("status") == "rejected") if p else 0

    def preview_denied_count(self) -> int:
        p = self.state.preview
        return len(p.denied) if p else 0

    def preview_lines(self) -> list[str]:
        """미리보기 화면 위쪽 안내(복원되지 않을 항목)."""
        n = self.preview_rejected_count()
        return [T.PREV_REJECTED_NOTE.format(n=n)] if n else []

    def denied_note(self) -> str:
        """보안상 복원하지 않는 형식이 있으면 한 줄 안내(미리보기 요약/결과 화면 공용). 없으면 빈 문자열."""
        n = self.preview_denied_count() if self.state.preview else self.import_summary().get("denied", 0)
        return T.PREV_DENIED_NOTE.format(n=n) if n else ""

    def git_config_note(self, applied: bool = False) -> str:
        """리포트 meta 의 git_config_warning 이 켜져 있으면 한 줄 안내(미리보기/결과 화면 공용). 없으면 빈 문자열."""
        res = self.state.import_result if applied else self.state.preview
        if res is None or not _meta_value(res, "git_config_warning"):
            return ""
        return T.RES_GIT_NOTE if applied else T.PREV_GIT_NOTE

    def preview_problems(self) -> list[str]:
        p = self.state.preview
        return [T.friendly_detail(x) for x in p.plan.problems] if p else []

    def set_preview_checked(self, on: bool) -> None:
        self.state.preview_checked = bool(on)
        if not on:
            self.state.final_confirmed = False
        self.emit("confirm_changed")

    def set_bak_understood(self, on: bool) -> None:
        """'덮어쓰는 파일은 백업 파일(.bak)로 남는다는 것을 이해했습니다'(미리보기 확인과 따로 받는 두 번째 확인)."""
        self.state.bak_understood = bool(on)
        if not on:
            self.state.final_confirmed = False
        self.emit("confirm_changed")

    def set_extra_consent(self, on: bool) -> None:
        self.state.extra_consent = bool(on)
        self.emit("confirm_changed")

    def confirm_final(self, yes: bool) -> None:
        """확인 대화상자에서 '예'를 눌렀을 때만 True 로 호출한다."""
        self.state.final_confirmed = bool(yes) and self.state.preview_checked and self.state.bak_understood
        self.emit("confirm_changed")

    def can_apply(self) -> tuple[bool, str]:
        s, p = self.state, self.state.preview
        if p is None or s.preview_package != s.package:
            return False, T.G_NEED_PREVIEW
        if p.mode == "blocked" or p.plan.problems:
            return False, T.G_PACKAGE_BLOCKED
        if not s.preview_checked:
            return False, T.G_NEED_CHECK_PREVIEW
        if not s.bak_understood:
            return False, T.G_NEED_BAK_UNDERSTAND
        if p.n_extra and not s.extra_consent:
            return False, T.G_NEED_EXTRA_CONSENT
        if not s.final_confirmed:
            return False, T.G_NEED_FINAL_YES
        return True, ""

    def apply_reason(self) -> str:
        """복원 실행 버튼(확인 창 전)이 막혀 있는 이유. 마지막 '확인 창' 단계는 이유로 치지 않는다."""
        ok, why = self.can_apply()
        return "" if ok or why == T.G_NEED_FINAL_YES else why

    def start_apply(self) -> None:
        ok, why = self.can_apply()
        if not ok:
            raise GuardError(why)
        package, allow_extra = self.state.package, self.state.extra_consent

        def work(progress, token):
            return self.api.run_import(package, self.state.rules_dir, yes=True, allow_extra=allow_extra,
                                       deps=self.deps, **_supported(self.api.run_import, progress_info=progress))

        def done(res):
            self.state.import_result = res
            self.state.preview = None  # 확정은 한 번만 유효: 다시 복원하려면 미리보기부터
            self._reset_confirms()
            self.emit("import_done", res)
        self._run(T.TASK_APPLY, work, done, on_fail=self._reset_confirms)  # 실패/취소 뒤에도 확인 표시를 지운다

    def import_summary(self) -> dict:
        r = self.state.import_result
        if r is None or r.report is None:
            return {}
        rep = r.report
        counts = rep.to_dict()["summary"]
        return {"counts": counts, "rejected": counts.get("rejected", 0), "denied": counts.get("denied", 0),
                "failures": [(f.dst, T.friendly_detail(f.error)) for f in rep.failures],
                "backups": [x.backup for x in rep.results if x.backup],
                "warnings": [(w.get("name", ""), i) for w in r.plan.warnings for i in w.get("items", [])]}

    def backup_folder(self) -> str:
        """복원 때 만든 백업 파일(.bak)이 있는 폴더(여러 곳이면 첫 번째). 없으면 빈 문자열."""
        backups = self.import_summary().get("backups", [])
        return os.path.dirname(backups[0]) if backups else ""

    def import_counts(self) -> tuple[int, int, int]:
        """(성공, 건너뜀, 실패)"""
        c = self.import_summary().get("counts", {})
        return (c.get("copied", 0), c.get("skipped_identical", 0) + c.get("denied", 0),
                c.get("failed", 0) + c.get("rejected", 0))

    # ------------------------------------------------------------------ 대용량 복사: 방식/단계
    def set_transfer_flow(self, mode: str, stage: int = 1) -> None:
        """'어떻게 옮기시나요?' 선택(외장 / 랜선 / 같은 PC)과 단계. 바뀌면 이전 미리보기와 결과는 무효가 된다."""
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        if mode not in (T.MODE_EXTERNAL, T.MODE_NETWORK, T.MODE_LOCAL):
            raise GuardError(T.G_NOT_IN_LIST)
        stage = 1 if mode == T.MODE_LOCAL or stage not in (1, 2) else stage
        s = self.state
        if (s.transfer_mode, s.transfer_stage) != (mode, stage):
            s.transfer_mode, s.transfer_stage = mode, stage
            s.transfer_report, s.transfer_preview_key, s.transfer_blocked = None, None, ""
            s.transfer_preview_opts = s.transfer_preview_split = None
            s.transfer_was_dry_run = True
        self.emit("transfer_flow", (mode, stage))

    def set_transfer_options(self, include_cloud: bool, strict: bool, split_large: bool = False) -> None:
        """옵션 체크 상태를 알린다. 미리보기 때와 달라지면 미리보기를 무효로 돌려, 보지 않은 내용으로 복사하지 못하게 한다."""
        s = self.state
        s.transfer_options = (bool(include_cloud), bool(strict))
        s.transfer_split = bool(split_large)
        changed = s.transfer_preview_opts is not None and s.transfer_preview_opts != s.transfer_options
        changed = changed or (s.transfer_preview_split is not None and s.transfer_preview_split != s.transfer_split)
        if changed:
            s.transfer_preview_key, s.transfer_preview_opts, s.transfer_blocked = None, None, ""
            s.transfer_preview_split = None
            self.emit("selection_changed")  # 버튼 상태 다시 계산

    def flow(self) -> tuple[str, int]:
        return self.state.transfer_mode, self.state.transfer_stage

    def is_verify_flow(self) -> bool:
        """랜선 2단계: 복사 없이 새 PC의 복사본을 목록 파일과 비교만 한다."""
        return self.flow() == (T.MODE_NETWORK, 2)

    def flow_labels(self) -> tuple[str, str]:
        return T.TR_FLOW_LABELS.get(self.flow(), T.TR_FLOW_LABELS[(T.MODE_LOCAL, 1)])

    def flow_dst_hint(self) -> str:
        return T.TR_FLOW_DST_HINT.get(self.state.transfer_mode, "")

    def flow_method(self) -> str:
        return T.TR_VERIFY_METHOD.get(self.flow(), "")

    def flow_steps(self) -> tuple[str, ...]:
        return T.TR_FLOW_STEPS.get(self.flow(), ())

    def transfer_step(self) -> tuple[tuple[str, ...], int]:
        """단계 표시줄용 (단계 이름들, 지금 단계 1부터). 방식 → 폴더 → 미리보기 → 복사 → 결과(랜선 2단계는 방식 → 목록·폴더 → 검사 → 결과)."""
        s, rep = self.state, self.state.transfer_report
        done = rep is not None and not s.transfer_was_dry_run
        if self.is_verify_flow():
            if done:
                return T.TR_VERIFY_STEP_NAMES, 4
            ok = self.check_verify_inputs(s.verify_manifest, s.verify_target).ok
            return T.TR_VERIFY_STEP_NAMES, 3 if (s.busy or ok) else 2
        if done:
            return T.TR_STEP_NAMES, 5
        if s.busy:
            return T.TR_STEP_NAMES, 3 if s.busy_label == T.TASK_TRANSFER_PREVIEW else 4
        if s.transfer_preview_key == (s.transfer_src, s.transfer_dst) and not s.transfer_blocked:
            return T.TR_STEP_NAMES, 4
        return T.TR_STEP_NAMES, 3 if self.check_transfer_paths(s.transfer_src, s.transfer_dst).ok else 2

    def flow_title(self) -> str:
        return T.TR_FLOW_TITLES.get(self.flow(), T.TR_HEADING)

    def manifest_out_path(self) -> str:
        """1단계(외장/랜선)에서 확인용 목록 파일을 저장할 위치: 대상(외장 드라이브 또는 새 PC의 공유 폴더) 안."""
        s = self.state
        if s.transfer_mode in (T.MODE_EXTERNAL, T.MODE_NETWORK) and s.transfer_stage == 1 and s.transfer_dst:
            return os.path.join(s.transfer_dst, MANIFEST_NAME)
        return ""

    def stage2_manifest(self) -> str:
        """외장 2단계: 외장 드라이브 폴더 안의 목록 파일. 없으면 빈 문자열(원본을 다시 읽어 검사)."""
        s = self.state
        if (s.transfer_mode, s.transfer_stage) == (T.MODE_EXTERNAL, 2) and s.transfer_src:
            cand = os.path.join(s.transfer_src, MANIFEST_NAME)
            return cand if os.path.isfile(cand) else ""
        return ""

    # ------------------------------------------------------------------ 대용량 복사: 점검
    def describe_dest(self, dst: str):
        """대상 드라이브 종류/파일 시스템/여유 공간(읽기 전용 조회, 같은 경로는 캐시). 네트워크 경로는 느릴 수 있어 조회하지 않는다."""
        fn = getattr(self.api, "destination_info", None)
        if not dst or fn is None:
            return None
        if dst.startswith("\\\\"):
            from ..fs import DestInfo
            return DestInfo(dst, "network")
        if dst not in self._dest_cache:
            try:
                self._dest_cache[dst] = fn(dst)
            except Exception:  # noqa: BLE001 - 조회 실패는 안내만 생략
                self._dest_cache[dst] = None
        return self._dest_cache[dst]

    def forget_dest_cache(self) -> None:
        self._dest_cache.clear()

    def check_transfer_paths(self, src: str, dst: str) -> PathCheck:
        lines: list[tuple[bool, str]] = []
        if not src:
            lines.append((False, T.G_NEED_SOURCE))
        elif not os.path.isdir(src):
            lines.append((False, T.G_SOURCE_MISSING.format(path=src)))
        else:
            lines.append((True, T.G_SOURCE_OK))
        if not dst:
            lines.append((False, T.G_NEED_DEST))
        elif src and os.path.isdir(src):
            rs, rd = os.path.realpath(src), os.path.realpath(dst)
            try:
                inside = os.path.commonpath([rs, rd]) == rs
            except ValueError:  # 다른 드라이브
                inside = False
            lines.append((False, T.G_DEST_INSIDE) if inside else (True, T.G_DEST_OK))
        info, warnings = [], []
        d = self.describe_dest(dst) if dst else None
        if d is not None:
            fs_name = d.fs or (T.TR_FS_UNKNOWN_NOTE if d.kind == "network" else T.TR_FS_UNKNOWN)
            free = fmt_bytes(d.free) if d.free is not None else T.TR_FS_UNKNOWN
            info.append(T.TR_DEST_INFO.format(kind=T.TR_KIND_LABELS.get(d.kind, T.TR_KIND_LABELS["unknown"]), fs=fs_name, free=free))
            if d.fs == "FAT32":
                warnings.append(T.TR_FAT32_WARN)
            elif d.fs == "EXFAT":
                warnings.append(T.TR_EXFAT_WARN)
            if d.kind == "network":
                warnings.append(T.TR_NETWORK_NOTE)
            if d.kind == "cdrom":
                lines.append((False, T.TR_CDROM_BLOCK))
        return PathCheck(all(ok for ok, _ in lines) and bool(src) and bool(dst), lines, info, warnings)

    def check_verify_inputs(self, manifest: str, target: str) -> PathCheck:
        lines: list[tuple[bool, str]] = []
        if not manifest:
            lines.append((False, T.G_NEED_MANIFEST))
        elif not os.path.isfile(manifest):
            lines.append((False, T.G_MANIFEST_MISSING.format(path=manifest)))
        else:
            lines.append((True, T.G_VERIFY_OK))
        if not target:
            lines.append((False, T.G_NEED_TARGET))
        elif not os.path.isdir(target):
            lines.append((False, T.G_TARGET_MISSING.format(path=target)))
        return PathCheck(all(ok for ok, _ in lines) and bool(manifest) and bool(target), lines)

    def transfer_reasons(self) -> tuple[str, str]:
        """(미리보기 버튼이 막힌 이유, 복사 시작 버튼이 막힌 이유). 빈 문자열이면 눌러도 됨."""
        s = self.state
        if s.busy:
            return T.WHY_BUSY, T.WHY_BUSY
        if self.is_verify_flow():
            ok = self.check_verify_inputs(s.verify_manifest, s.verify_target).ok
            return ("", "") if ok else (T.WHY_NEED_MANIFEST, T.WHY_NEED_MANIFEST)
        check = self.check_transfer_paths(s.transfer_src, s.transfer_dst)
        if not check.ok:
            return T.WHY_PATH_PROBLEM, T.WHY_PATH_PROBLEM
        if s.transfer_preview_key != (s.transfer_src, s.transfer_dst):
            return "", T.WHY_PREVIEW_FIRST
        if s.transfer_blocked:
            return "", s.transfer_blocked
        return "", ""

    def can_verify_again(self) -> tuple[bool, str]:
        """'검사만 다시': 복사를 이미 한 번 한 폴더 쌍에서만. 복사는 하지 않고 읽기만 한다."""
        s = self.state
        if s.busy:
            return False, T.WHY_BUSY
        if self.is_verify_flow():
            return False, ""
        if not self.check_transfer_paths(s.transfer_src, s.transfer_dst).ok:
            return False, T.WHY_PATH_PROBLEM
        if not os.path.isdir(s.transfer_dst):
            return False, T.G_NEED_COPY_FIRST
        return True, ""

    def has_retryable(self) -> bool:
        rep = self.state.transfer_report
        return bool(rep is not None and not self.state.transfer_was_dry_run and rep.failures and not self.is_verify_flow())

    # ------------------------------------------------------------------ 대용량 복사: 실행
    def start_transfer(self, dry_run: bool = True, strict: bool = False, include_cloud: bool = False,
                       report_path: str = "", write_confirmed: bool = False, verify_only: bool = False,
                       retry_failed: bool = False, workers: int | None = None, split_large: bool = False) -> None:
        s = self.state
        if self.is_verify_flow():
            raise GuardError(T.G_NOT_IN_LIST)
        check = self.check_transfer_paths(s.transfer_src, s.transfer_dst)
        if not check.ok:
            raise GuardError(" ".join(t for ok, t in check.lines if not ok))
        if report_path and real_api.report_inside(report_path, s.transfer_src):
            raise GuardError(T.G_REPORT_INSIDE_SOURCE)
        key = (s.transfer_src, s.transfer_dst)
        if verify_only:
            if not os.path.isdir(s.transfer_dst):
                raise GuardError(T.G_NEED_COPY_FIRST)
        elif not dry_run:
            if not write_confirmed:
                raise GuardError(T.G_TRANSFER_CONFIRM)
            if s.transfer_preview_key != key:
                raise GuardError(T.G_TRANSFER_NEED_PREVIEW)
            if s.transfer_preview_opts is not None and s.transfer_preview_opts != (bool(include_cloud), bool(strict)):
                raise GuardError(T.G_TRANSFER_OPTIONS_CHANGED)  # 미리보기에 없던 내용(예: 클라우드 파일 내려받기)이 생길 수 있다
            if s.transfer_preview_split is not None and s.transfer_preview_split != bool(split_large):
                raise GuardError(T.G_TRANSFER_OPTIONS_CHANGED)
            if s.transfer_blocked:
                raise GuardError(s.transfer_blocked)
        extra: dict = {}
        if not dry_run and not verify_only:
            out = self.manifest_out_path()
            if out:
                extra["manifest_out"] = out
            use = self.stage2_manifest()
            if use:
                extra["use_manifest"] = use
        if verify_only:
            extra["verify_only"] = True
        if retry_failed:
            extra["retry_failed"] = True
        if workers:
            extra["workers"] = workers
        if split_large:
            if not _accepts(self.api.run_transfer, "split_large"):  # 지원하지 않는 버전이 옵션을 무시하고 큰 파일을 그대로 복사하면 안 된다
                raise GuardError(T.G_SPLIT_UNSUPPORTED)
            extra["split_large"] = True

        def work(progress, token):
            return self.api.run_transfer(s.transfer_src, s.transfer_dst, dry_run=dry_run, include_cloud=include_cloud,
                                         strict=strict, progress=progress, cancel=token, deps=self.deps,
                                         warn=progress, progress_info=progress, **extra)

        def done(rep):
            s.transfer_report, s.transfer_was_dry_run = rep, dry_run
            if dry_run:
                s.transfer_preview_key = key
                s.transfer_preview_opts = (bool(include_cloud), bool(strict))
                s.transfer_preview_split = bool(split_large)
                meta = getattr(rep, "meta", {}) or {}
                s.transfer_blocked = (T.WHY_SPACE if meta.get("space_blocked") else T.WHY_FAT32 if meta.get("fat32_blocked") else "")
            else:
                s.transfer_blocked = ""
            self.emit("transfer_done", rep)
            if report_path:
                try:
                    rep.write(Path(report_path))
                except OSError as e:
                    self.emit("error", self.report_error(e, label))
        label = (T.TASK_TRANSFER_PREVIEW if dry_run else T.TASK_VERIFY_AGAIN if verify_only
                 else T.TASK_RETRY if retry_failed else T.TASK_TRANSFER)
        self._run(label, work, done, cancellable=True)

    def start_verify(self, report_path: str = "") -> None:
        """랜선 2단계(새 PC): 목록 파일과 로컬 폴더를 비교한다. 폴더는 읽기만 한다(결과 파일도 그 폴더 밖에만 저장)."""
        s = self.state
        check = self.check_verify_inputs(s.verify_manifest, s.verify_target)
        if not check.ok:
            raise GuardError(" ".join(t for ok, t in check.lines if not ok))
        if report_path and self.api.report_inside(report_path, s.verify_target):
            raise GuardError(T.G_REPORT_INSIDE)

        def work(progress, token):
            return self.api.run_verify(s.verify_manifest, s.verify_target, progress=progress, progress_info=progress,
                                       cancel=token, deps=self.deps)

        def done(rep):
            s.transfer_report, s.transfer_was_dry_run = rep, False
            self.emit("transfer_done", rep)
            if report_path:
                try:
                    rep.write(Path(report_path))
                except OSError as e:
                    self.emit("error", self.report_error(e, T.TASK_VERIFY))
        self._run(T.TASK_VERIFY, work, done, cancellable=True)

    # ------------------------------------------------------------------ 대용량 복사: 결과
    def location_warning(self) -> str:
        """복사/검사한 위치가 원래 경로와 다르면 노란 경고 문구(같으면 빈 문자열)."""
        rep, s = self.state.transfer_report, self.state
        if rep is None or s.transfer_was_dry_run:
            return ""
        meta = getattr(rep, "meta", {}) or {}
        if self.is_verify_flow():
            orig, now = meta.get("source_root", ""), meta.get("target", "") or s.verify_target
        elif (s.transfer_mode, s.transfer_stage) == (T.MODE_EXTERNAL, 2):
            orig, now = meta.get("original_root", ""), s.transfer_dst
        else:
            return ""
        if orig and now and os.path.normcase(os.path.normpath(orig)) != os.path.normcase(os.path.normpath(now)):
            return T.LOC_WARNING.format(orig=orig, now=now)
        return ""

    def layout_lines(self) -> list[str]:
        """원본 최상위 구조(절대 경로, 드라이브 포함). 결과 화면에서 xref/데이터 단축 경로 안내와 함께 보여 준다."""
        rep = self.state.transfer_report
        meta = getattr(rep, "meta", {}) or {}
        layout = meta.get("source_layout") or {}
        if not layout:
            return []
        lines = [T.TR_LAYOUT_ROOT.format(root=layout.get("root", ""))]
        for t in layout.get("top_level", [])[:12]:
            name = t["name"] if t["name"] != "(root)" else T.TR_LAYOUT_FILES
            lines.append(T.TR_LAYOUT_LINE.format(name=name, files=t["files"], size=fmt_bytes(t["bytes"])))
        more = layout.get("top_level_count", 0) - min(12, len(layout.get("top_level", [])))
        if more > 0:
            lines.append(T.TR_LAYOUT_MORE.format(n=more))
        return lines

    def preview_link_lines(self) -> list[str]:
        """건너뛴 바로가기/정션 안내(미리보기와 결과 화면 공통). 없으면 빈 목록."""
        rep = self.state.transfer_report
        n, paths, more = skipped_links_view(getattr(rep, "meta", {}) or {})
        if not n:
            return []
        return [T.TR_SKIPPED_LINKS.format(n=n, more=T.TR_SKIPPED_LINKS_MORE.format(shown=len(paths)) if more else "",
                                          paths="\n".join("  " + p for p in paths))]

    def split_count(self) -> int:
        """조각으로 나눈(나눌) 파일 개수. 리포트 meta 의 split_files(개수 또는 목록) 또는 split_count."""
        meta = getattr(self.state.transfer_report, "meta", {}) or {}
        return _count_of(meta.get("split_files")) or _count_of(meta.get("split_count"))

    def verify_notes(self) -> list[str]:
        """검사(verify) 결과 안내: 목록에서 빠진 파일이 있으면 '모두 정확함'이 아님."""
        rep = self.state.transfer_report
        n = int((getattr(rep, "meta", {}) or {}).get("manifest_errors") or 0)
        return [T.TR_MANIFEST_ERRORS.format(n=n)] if n else []

    def flow_notes(self) -> list[str]:
        """결과 화면 아래에 붙는 안내(검사 미룸, 다음 단계, 절전 방지 실패, xref, 원본 보존)."""
        rep, s = self.state.transfer_report, self.state
        if rep is None or s.transfer_was_dry_run:
            return []
        meta = getattr(rep, "meta", {}) or {}
        notes: list[str] = []
        if meta.get("dest_verification") == "deferred":
            notes.append(T.TR_DEFERRED_NOTE)
        if meta.get("keep_awake") == "failed":
            notes.append(T.TR_KEEP_AWAKE_FAILED)
        if meta.get("preserved_existing_count"):
            notes.append(T.TR_PRESERVED_NOTE.format(n=meta["preserved_existing_count"]))
        n_split = self.split_count()
        if n_split:
            notes.append(T.TR_SPLIT_RESULT.format(n=n_split))
        notes.extend(self.preview_link_lines())
        notes.extend(self.verify_notes())
        if s.transfer_mode == T.MODE_EXTERNAL and s.transfer_stage == 1 and not rep.failures:
            notes.append(T.TR_NEXT_STEP_EXT1)
        if s.transfer_mode == T.MODE_NETWORK and s.transfer_stage == 1 and not rep.failures:
            notes.append(T.TR_NEXT_STEP_NET1)
        loc = self.location_warning()
        if loc:
            notes.append(loc)
        notes.append(T.XREF_NOTE)
        notes.append(T.TR_DONE_REMINDER)
        return notes

    def transfer_summary(self) -> dict:
        rep = self.state.transfer_report
        if rep is None:
            return {}
        counts = rep.to_dict()["summary"]
        return {"dry_run": self.state.transfer_was_dry_run, "counts": counts,
                "verified": counts.get("verified", 0), "mismatch": counts.get("mismatch", 0),
                "missing": counts.get("missing", 0), "cloud_only": counts.get("cloud_only", 0),
                "failed": counts.get("failed", 0),
                "failures": [(f.src, T.TR_STATUS_LABELS.get(f.status, f.status), T.friendly_detail(f.error))
                             for f in rep.failures],
                "cloud_files": [r.src for r in rep.results if r.status == "cloud_only"],
                "repaired": [x["file"] for x in rep.meta.get("repaired", [])],
                "pending": counts.get("pending_verify", 0), "added": counts.get("extra", 0),
                "mode": rep.meta.get("mode", ""), "io": rep.meta.get("io") or {},
                "split": self.split_count(),
                "manifest_complete": rep.meta.get("manifest_complete", True),
                "manifest_errors": int(rep.meta.get("manifest_errors") or 0),
                "verification_complete": rep.meta.get("verification_complete"),
                "success": rep.to_dict().get("success")}

    def transfer_counts(self) -> tuple[int, int, int]:
        """(성공, 건너뜀, 실패). 건너뜀은 클라우드 전용 파일."""
        sm = self.transfer_summary()
        if not sm:
            return 0, 0, 0
        return sm["verified"] + sm["pending"], sm["cloud_only"], sm["mismatch"] + sm["missing"] + sm["failed"]

    def save_transfer_report(self, path: str) -> None:
        rep = self.state.transfer_report
        if rep is None:
            raise GuardError(T.G_NOTHING_TO_SAVE)
        s = self.state
        for folder in (s.verify_target if self.is_verify_flow() else s.transfer_src,):
            if folder and real_api.report_inside(path, folder):
                raise GuardError(T.G_REPORT_INSIDE_SOURCE)
        try:
            rep.write(Path(path))
        except OSError as e:
            raise real_api.ApiError("\n".join(real_api.format_write_error(path, e))) from e

    # ------------------------------------------------------------------ 추천대로 한 번에(auto_run)
    # 기존 start_* 와 완료 이벤트(*_done)를 순서대로 이어 붙이기만 한다. 새 권한을 만들지 않는다:
    # 쓰기 전 확인(내보내기/복사/복원)은 반드시 사람의 응답(auto_confirm/auto_apply)을 기다리고,
    # 복원은 기존 can_apply() 가드(체크 2개 + 최종 확인)를 그대로 지난다.
    _AUTO_MANUAL = {"old": S.OLD_SCAN, "new": S.NEW_PACKAGE, "transfer": S.TRANSFER}

    def auto_run(self, flow: str) -> None:
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        if flow not in AUTO_FLOWS:
            raise GuardError(T.G_NOT_IN_LIST)
        self.auto = AutoRun(flow, [AutoStep(k, t) for k, t in T.AUTO_STEPS[flow]], manual=self._AUTO_MANUAL[flow])
        self.go(S.AUTO)
        try:
            getattr(self, "_auto_begin_" + flow)()
        except GuardError as e:
            self._auto_stop(str(e))

    def _auto_notify(self) -> None:
        self.emit("auto_changed", self.auto)

    def _auto_mark(self, key: str, state: str, note: str = "") -> None:
        st = self.auto.step(key) if self.auto else None
        if st is not None:
            st.state = state
            if note:
                st.note = note

    def _auto_stop(self, what: str, how: str = "", manual: str = "", by_user: bool = False) -> None:
        a = self.auto
        if a is None or not a.active:
            return
        for st in a.steps:
            if st.state in ("doing", "waiting"):
                st.state = "stopped"
        a.status, a.gate, a.what, a.how, a.by_user = "stopped", "", what, how, by_user
        a.manual = manual or a.manual
        self.log.info("auto stopped: %s", a.flow)
        self._auto_notify()

    def _auto_finish(self, warning: str = "") -> None:
        a = self.auto
        if a is None or not a.active:
            return
        a.status, a.gate, a.warning = "done", "", warning
        self._auto_notify()

    def _auto_user_stop(self) -> None:
        self._auto_stop(T.AUTO_STOPPED_USER, T.AUTO_STOPPED_USER_HOW, by_user=True)

    def _auto_wait(self, key: str, gate: str, note: str = "") -> None:
        a = self.auto
        if a is None:
            return
        if a.stop_requested:
            self._auto_user_stop()
            return
        self._auto_mark(key, "waiting", note)
        a.status, a.gate = "waiting", gate
        self._auto_notify()

    def _auto_start(self, key: str, fn: Callable, *args, **kw) -> None:
        """다음 단계 하나를 시작한다(중지 요청이 있었으면 시작하지 않고 멈춘다)."""
        a = self.auto
        if a is None:
            return
        if a.stop_requested:
            self._auto_user_stop()
            return
        a.status, a.gate = "running", ""
        self._auto_mark(key, "doing")
        self._auto_notify()
        try:
            fn(*args, **kw)
        except GuardError as e:
            self._auto_stop(str(e))
        except Exception as e:  # noqa: BLE001 - 예상 밖 오류도 멈춤으로 알린다
            fe = self.report_error(e, T.TASK_UI, traceback.format_exc())
            self._auto_stop(fe.what, fe.how)
            self.emit("error", fe)

    def auto_stop(self) -> bool:
        """'중지': 취소할 수 있는 작업은 바로 취소하고, 취소할 수 없는 작업은 그 단계가 끝난 뒤 멈춘다. 확인을 기다리는 중이면 바로 멈춘다."""
        a = self.auto
        if a is None or not a.active:
            return False
        if self.state.busy:
            a.stop_requested = True
            if self.can_cancel:
                self.cancel()
            self._auto_notify()
            return True
        self._auto_user_stop()
        return True

    def auto_manual(self) -> None:
        """'직접 진행하기': 멈춘 곳과 가까운 기존 화면으로 가서 거기서부터 직접 진행한다(한 번에 진행은 끝난다)."""
        a = self.auto
        self.go(a.manual if a is not None and a.manual else S.START)

    def _auto_event(self, kind: str, payload: Any) -> None:
        a = self.auto
        if a is None or not a.active:
            return
        if kind == "error":
            if isinstance(payload, FriendlyError):
                self._auto_stop(payload.what, payload.how)
            return
        if kind == "cancelled":
            if a.stop_requested:
                self._auto_user_stop()
            else:
                self._auto_stop(T.AUTO_CANCELLED_WHAT, T.AUTO_CANCELLED_HOW)
            return
        try:
            getattr(self, "_auto_on_" + kind)(payload)
        except GuardError as e:
            self._auto_stop(str(e))

    # ---- 구 PC: 조사 -> (자동 목록에 없는 프로그램은 고르지 않음) -> 해야 할 일 목록 -> 바탕화면에 설정 묶음 파일
    def _auto_begin_old(self) -> None:
        s = self.state
        s.discovery, s.selected, s.secrets_ack = None, set(), False  # 자동 선택 없음: 예전에 직접 고른 항목도 가져오지 않는다
        self._auto_mark("discover", "skipped", T.AUTO_DISCOVER_SKIPPED)
        self._auto_start("scan", self.start_scan)

    def _auto_on_scan_done(self, res: Any) -> None:
        if self.auto.flow != "old":
            return
        self._auto_mark("scan", "done", T.AUTO_SCAN_NOTE.format(programs=getattr(res, "program_count", 0),
                                                                rules=len(getattr(res, "matched", None) or [])))
        self._auto_start("checklist", self.start_checklist)

    def _auto_on_checklist_done(self, res: Any) -> None:
        a = self.auto
        if a.flow != "old":
            return
        self._auto_mark("checklist", "done")
        a.export_path = self.default_export_path()
        self._auto_wait("export", "export_confirm", a.export_path)

    def _auto_on_export_done(self, res: Any) -> None:
        a = self.auto
        if a.flow != "old":
            return
        m = res.manifest
        unreadable = sum(1 for f in m["failed"] if f.get("reason"))
        hard = len(m["failed"]) - unreadable
        warn = " ".join(x for x in (T.AUTO_UNREADABLE_WARN.format(n=unreadable) if unreadable else "",
                                    T.AUTO_FAILED_WARN.format(n=hard) if hard else "") if x)
        self._auto_mark("export", "warn" if warn else "done", a.export_path)
        self._auto_finish(warn)

    # ---- 새 PC: 설정 묶음 파일 찾기 -> 미리보기 -> (복원 확인: 체크 2개 + 최종 확인창) -> 복원
    def _auto_begin_new(self) -> None:
        a = self.auto
        self._auto_mark("find", "doing")
        a.candidates = self.find_packages()
        if not a.candidates:
            self._auto_stop(T.AUTO_PKG_NONE_WHAT, T.AUTO_PKG_NONE_HOW, manual=S.NEW_PACKAGE)
        elif len(a.candidates) == 1:
            self._auto_use_package(a.candidates[0])
        else:
            self._auto_wait("find", "pick_package", T.AUTO_PKG_PICK_HEAD.format(n=len(a.candidates)))

    def _auto_use_package(self, path: str) -> None:
        self.set_package(path)
        self._auto_mark("find", "done", path)
        self._auto_start("preview", self.start_preview)

    def auto_pick_package(self, path: str) -> None:
        a = self.auto
        if a is None or a.gate != "pick_package" or path not in a.candidates:  # 찾은 목록에 없는 경로는 받지 않는다
            raise GuardError(T.G_NOT_IN_LIST)
        a.status, a.gate = "running", ""
        self._auto_use_package(path)

    def _auto_on_preview_done(self, res: Any) -> None:
        if self.auto.flow != "new":
            return
        problems = self.preview_problems()
        if res.mode == "blocked" or problems:
            self._auto_stop(T.AUTO_PROBLEMS_WHAT.format(items=" / ".join(problems[:3]) or T.G_PACKAGE_BLOCKED),
                            T.AUTO_PROBLEMS_HOW, manual=S.NEW_PREVIEW)
            return
        n = self.preview_rejected_count()
        if n:
            self._auto_stop(T.AUTO_REJECTED_WHAT.format(n=n), T.AUTO_REJECTED_HOW, manual=S.NEW_PREVIEW)
            return
        counts = res.summary()
        self._auto_mark("preview", "done", T.AUTO_RESTORE_SUMMARY.format(
            new=counts.get("would_copy", 0), over=counts.get("would_overwrite", 0), same=counts.get("skipped_identical", 0)))
        self._auto_start("install", self.start_install_check)

    def _auto_on_install_done(self, res: Any) -> None:
        a = self.auto
        if a.flow != "new":
            return
        note = T.AUTO_INSTALL_DONE_NOTE.format(n=res.remaining)
        if a.status == "waiting" and a.gate == "install":  # 같은 자리에서 '다시 확인'을 누른 경우
            self._auto_mark("install", "waiting", note)
            self._auto_notify()
            return
        self._auto_wait("install", "install", note)

    def auto_install_continue(self, skip: bool = False) -> None:
        """설치 확인 지점에서 '복원 확인으로'(skip=True 면 건너뛰기). 설치 명령은 실행하지 않는다."""
        a = self.auto
        if a is None or a.gate != "install" or a.status != "waiting":
            raise GuardError(T.G_NOT_IN_LIST)
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        self.state.install_skipped = bool(skip)
        r = self.state.install_check
        self._auto_mark("install", "skipped" if skip else "done",
                        T.AUTO_INSTALL_SKIPPED if skip else T.AUTO_INSTALL_DONE_NOTE.format(n=r.remaining if r else 0))
        a.status, a.gate = "running", ""
        self._auto_wait("confirm", "restore")

    def auto_apply(self) -> None:
        """복원 확인 지점에서 '복원 실행': 기존 can_apply() 가 모두 통과해야 한다(체크 2개 + 최종 확인창의 '예')."""
        a = self.auto
        if a is None or a.gate != "restore":
            raise GuardError(T.G_NOT_IN_LIST)
        ok, why = self.can_apply()
        if not ok:
            raise GuardError(why)
        self._auto_mark("confirm", "done")
        self._auto_start("apply", self.start_apply)

    def _auto_on_import_done(self, res: Any) -> None:
        if self.auto.flow != "new":
            return
        _ok, _skipped, failed = self.import_counts()
        self._auto_mark("apply", "warn" if failed else "done")
        self._auto_finish(T.AUTO_FAILED_WARN.format(n=failed) if failed else "")

    # ---- 대용량: 질문 두 개 -> 폴더 -> 미리보기 -> (복사 시작 직전 확인) -> 복사 / 랜선 새 PC 쪽은 목록·폴더 -> 검사
    def _auto_begin_transfer(self) -> None:
        self._auto_wait("questions", "questions")

    def auto_answer_questions(self, mode: str, stage: int) -> None:
        a = self.auto
        if a is None or a.gate != "questions":
            raise GuardError(T.G_NOT_IN_LIST)
        if mode not in (T.MODE_EXTERNAL, T.MODE_NETWORK) or stage not in (1, 2):
            raise GuardError(T.G_NOT_IN_LIST)
        self.set_transfer_flow(mode, stage)
        self.set_transfer_options(False, False, False)  # 추천 설정: 클라우드 전용 파일 제외, 처음부터 다시 확인 안 함, 조각내기 안 함
        a.steps = [AutoStep(k, t) for k, t in T.AUTO_STEPS["receive" if self.is_verify_flow() else "transfer"]]
        self._auto_mark("questions", "done", self.flow_title())
        self._auto_wait("folders", "folders")

    def auto_set_folders(self, first: str, second: str) -> None:
        """복사: (원본, 대상) / 랜선 새 PC 쪽: (확인용 목록 파일, 검사할 폴더). 점검에 통과해야 이어간다."""
        a = self.auto
        if a is None or a.gate != "folders":
            raise GuardError(T.G_NOT_IN_LIST)
        s = self.state
        if self.is_verify_flow():
            s.verify_manifest, s.verify_target = first, second
            check = self.check_verify_inputs(first, second)
        else:
            s.transfer_src, s.transfer_dst = first, second
            check = self.check_transfer_paths(first, second)
        if not check.ok:
            raise GuardError(" ".join(t for ok, t in check.lines if not ok))
        self._auto_mark("folders", "done")
        if self.is_verify_flow():
            a.phase = "verify"
            self._auto_wait("check", "verify_confirm")
        else:
            a.phase = "preview"
            self._auto_start("preview", self.start_transfer, dry_run=True)

    def auto_confirm(self, yes: bool) -> None:
        """내보내기/복사/검사를 시작하기 직전의 확인. 사람이 '예'라고 답해야만 시작한다."""
        a = self.auto
        if a is None or a.status != "waiting" or a.gate not in ("export_confirm", "transfer_confirm", "verify_confirm"):
            raise GuardError(T.G_NOT_IN_LIST)
        gate = a.gate
        if not yes:
            what = (T.AUTO_EXPORT_DECLINED if gate == "export_confirm"
                    else T.AUTO_TR_PREVIEW_DECLINED if gate == "transfer_confirm" else T.AUTO_STOPPED_USER)
            self._auto_stop(what, T.AUTO_STOPPED_USER_HOW, by_user=True)
            return
        if gate == "export_confirm":
            self._auto_start("export", self.start_export, a.export_path, write_confirmed=True, resume=True)
        elif gate == "transfer_confirm":
            a.phase = "copy"
            self._auto_start("copy", self.start_transfer, dry_run=False, write_confirmed=True, strict=False, include_cloud=False,
                             split_large=False)
        else:
            a.phase = "verify"
            self._auto_start("check", self.start_verify)

    def _auto_on_transfer_done(self, rep: Any) -> None:
        a = self.auto
        if a.flow != "transfer":
            return
        s, sm = self.state, self.transfer_summary()
        bad = sm.get("mismatch", 0) + sm.get("missing", 0) + sm.get("failed", 0)
        if a.phase == "preview":
            if s.transfer_blocked:
                how = T.AUTO_TR_FAT32_HOW if s.transfer_blocked == T.WHY_FAT32 else T.AUTO_TR_BLOCKED_HOW
                self._auto_stop(s.transfer_blocked, how, manual=S.TRANSFER)
                return
            if bad:
                self._auto_stop(T.AUTO_TR_PREVIEW_PROBLEM.format(n=bad), T.AUTO_TR_PREVIEW_PROBLEM_HOW, manual=S.TRANSFER)
                return
            c = sm["counts"]
            note = T.AUTO_TR_PREVIEW_NOTE.format(new=c.get("would_copy", 0), over=c.get("would_overwrite", 0),
                                                 same=c.get("skipped_identical", 0))
            if sm["cloud_only"]:
                note += " · " + T.AUTO_TR_CLOUD_NOTE.format(n=sm["cloud_only"])
            self._auto_mark("preview", "done", note)
            self._auto_wait("copy", "transfer_confirm")
            return
        key = "copy" if a.phase == "copy" else "check"
        warn = f"{T.AUTO_TR_FAIL_WHAT.format(n=bad)} {T.AUTO_TR_FAIL_HOW}" if bad else ""
        self._auto_mark(key, "warn" if bad else "done")
        self._auto_mark("result", "warn" if bad else "done")
        self._auto_finish(warn)

    def auto_result_lines(self) -> list[str]:
        """끝난 뒤 진행 화면에 보여 줄 결과 줄(요약 + 안내)."""
        a = self.auto
        if a is None or a.status != "done":
            return []
        if a.flow == "old":
            m = getattr(self.state.export_result, "manifest", None)
            if not isinstance(m, dict):
                return []
            return [T.EXP_DONE.format(path=self.state.export_output), T.result_line(len(m["files"]), len(m["skipped"]), len(m["failed"]))]
        if a.flow == "transfer" and self.state.transfer_report is not None:
            ok, skipped, failed = self.transfer_counts()
            return [T.result_line(ok, skipped, failed), *self.flow_notes()]
        return []
