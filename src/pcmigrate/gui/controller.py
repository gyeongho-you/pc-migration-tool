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
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import api as real_api
from .. import errorlog
from ..cancel import CancelToken
from ..cancel import bind as bind_cancel
from ..checklist import write_output
from .. import coverage, fs
from ..discover import fmt_size, items_from_selection
from ..install_check import INSTALLED, WINGET, import_command, newly_installed, safe_winget_ids, selected_commands
from ..detect import valid_winget_id
from ..install_dir import install_items_from_selection
from ..manifest import DEFAULT_NAME as MANIFEST_NAME
from ..manifest import ManifestError
from ..package import inspect_savepoint
from ..progress import ProgressInfo, fmt_bytes, format_info
from ..rules import default_rules_dir
from ..scan import skipped_links_view
from ..transfer import DriveDisconnected, TransferError
from . import state as S
from . import texts as T
from .files_controller import FilesMixin  # stage30a: 내 파일 옮기기(찾기/방법/직접 연결) 부분은 별도 파일
from .net_controller import NetMixin  # stage39: 이더넷 주소 자동 맞추기
from .runner import Runner
from .status import StatusModel
from .controller_common import (  # noqa: F401  # stage43: 값 객체·오류 변환·경로 보조는 별도 파일, 여기서 다시 내보낸다(기존 import 경로 유지)
    _home_aliases,
    anonymize_paths,
    DriveEntry,
    GuardError,
    FriendlyError,
    _WIN_PATH_MISSING,
    _WIN_PERMISSION,
    _WIN_DISK_FULL,
    _WIN_DISCONNECTED,
    _WIN_IN_USE,
    _os_error_texts,
    _api_how,
    _export_error_texts,
    friendly_error,
    RECEIVED_PREFIX,
    RECEIVED_DEPTH,
    RECEIVED_MAX_DIRS,
    norm_path,
    _supported,
    _accepts,
    _meta_value,
    _count_of,
    _final_status,
    CandRow,
    PathCheck,
    AutoStep,
    AutoRun,
    AUTO_FLOWS,
    _AUTO_KINDS,
)
from .controller_pkgfind import PackageFindMixin  # stage43
from .controller_scan import ScanMixin  # stage43
from .controller_export import ExportMixin  # stage43
from .controller_install import InstallMixin  # stage43
from .controller_import import ImportMixin  # stage43
from .controller_transfer import TransferMixin  # stage43
from .controller_auto import AutoMixin  # stage43
from .controller_config import ConfigMixin  # stage43
from .controller_undo import UndoMixin  # stage43
from .controller_place import PlaceMixin  # stage50: 받은 파일 원래 위치에 놓기
from .place_flow import PlaceState  # stage50

_WRITING_TASKS = (T.TASK_EXPORT, T.TASK_EXPORT_RETRY, T.TASK_TRANSFER, T.TASK_APPLY, T.TASK_RETRY)  # 파일을 쓰는 작업
FORCE_STOP_AFTER = 15.0  # '중지' 요청 후 이 시간(초)이 지나도 못 멈추면 force_stop_ready() 가 True


class Controller(FilesMixin, NetMixin, PackageFindMixin, ScanMixin, ExportMixin, InstallMixin, ImportMixin, TransferMixin, AutoMixin, ConfigMixin, UndoMixin, PlaceMixin):
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
        self.make_install_items = install_items_from_selection
        self.record_error = record_error or errorlog.record  # (예외, 실행 화면/작업 이름, traceback) -> 로그 파일 경로 | None
        self.status = StatusModel()  # 현황 카드의 상태(tkinter 와 무관)
        self.last_error: FriendlyError | None = None
        self.listener: Callable[[str, Any], None] | None = None
        self.cancel_token: CancelToken | None = None
        self.can_cancel = False
        self.clock: Callable[[], float] = time.monotonic  # 시험에서 바꿔 끼운다
        self.cancel_requested_at: float | None = None  # '중지'를 요청한 시각(없으면 None)
        self.force_stop_after = FORCE_STOP_AFTER
        self._abort: Callable[[], None] | None = None  # 지금 작업을 강제로 버리는 함수
        self.cleanup_hooks: list[Callable[[], None]] = []  # 강제 중단 때 부르는 정리 함수(락/임시물)
        self.checklist_marks: set[tuple[str, str]] = set()  # 체크한 항목(탭, 문구): 화면을 오가도 유지
        self._dest_cache: dict[str, Any] = {}
        self.roadmap_marks: set[str] = set()  # 전체 이전 순서에서 체크한 항목(창을 닫았다 열어도 유지)
        self._in_listener_error = False
        self.auto: AutoRun | None = None  # '추천대로 한 번에' 진행 상태(없으면 None)
        self.place_state = PlaceState()  # stage50: 받은 파일 원래 위치에 놓기 화면의 상태
        self.drive_lister: Callable[[], list] | None = None  # 시험에서 가짜 드라이브 목록을 넣는 자리
        if hasattr(self.runner, "on_callback_error"):
            self.runner.on_callback_error = self._callback_failed

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
        self._files_on_go(screen)  # stage30a
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
        if label in _WRITING_TASKS and self.runner.abandoned_alive():  # 강제로 버린 옛 작업이 아직 같은 파일을 쓰고 있을 수 있다
            raise GuardError(T.G_BUSY)
        token = CancelToken()  # 모든 작업은 중지할 수 있다(오래 걸리는 반복문은 checkpoint 로 이 토큰을 본다)
        self.cancel_token, self.can_cancel = token, True
        self.cancel_requested_at, self._abort = None, None
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
            self.cancel_requested_at, self._abort = None, None

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

        def on_cancel(forced: bool = False) -> None:
            finish()
            self.log.info("task cancelled: %s", label)
            if on_fail:
                on_fail()
            self.status.cancelled()
            self.emit("busy", None)
            self.emit("status", self.status)
            self.emit("cancelled", label)
            if forced:
                self.emit("force_stopped", label)

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

        def work(progress):
            with bind_cancel(token):  # 이 스레드의 모든 반복문(읽기/해시/압축/폴더 순회/하위 프로세스 대기)이 취소를 본다
                return fn(progress, token)

        tid = self.runner.submit(work, on_done=on_done, on_error=on_error, on_cancel=on_cancel, on_progress=on_progress)
        if self.state.busy and self.state.busy_label == label:  # sync 실행기에서는 이미 끝났을 수 있다
            def abort() -> None:
                self.runner.abandon(tid)  # 늦게 도착하는 결과는 버린다
                self.log.warning("task force-stopped: %s", label)
                for hook in list(self.cleanup_hooks):
                    try:
                        hook()
                    except Exception:  # noqa: BLE001 - 정리 실패가 강제 중단을 막지 않는다
                        self.log.exception("cleanup hook failed")
                on_cancel(forced=True)
            self._abort = abort

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
        """진행 중인 작업을 중지한다(모든 장기 작업). 이미 눌렀으면 아무 일도 하지 않고 True. 작업 없으면 False."""
        if self.cancel_token is not None:
            if self.cancel_requested_at is None:
                self.cancel_requested_at = self.clock()
                self.cancel_token.cancel()
                self.emit("progress", T.CANCELLING)
                self.emit("cancelling", None)
            return True
        return False

    def force_stop_ready(self) -> bool:
        """'중지'를 요청한 지 force_stop_after 초가 지나도 작업이 안 끝났으면 True(나중에 화면이 '강제 중단' 단추를 보일 때 쓴다)."""
        return bool(self.state.busy and self.cancel_requested_at is not None
                    and self.clock() - self.cancel_requested_at >= self.force_stop_after)

    def force_stop(self) -> bool:
        """작업 스레드의 결과를 버리고 busy 를 푼다(이벤트: cancelled, force_stopped). 스레드는 취소 토큰을 보고 스스로 끝나며
        임시물/락은 그 스레드가 정리한다. 반환: 강제 중단을 했는가. 원본은 읽기만 하므로 건드리지 않는다."""
        if self._abort is None or not self.state.busy:
            return False
        if self.cancel_token is not None:
            self.cancel_token.cancel()
        abort = self._abort
        abort()
        return True

    # ====================================================================== stage30b 구획(화면 4~7)
    # ---- 작업 중지 안내(하단 막대가 쓴다)
    def stop_done_note(self, task: str) -> str:
        """중지가 끝난 뒤 보여 줄 안내: 이어받기가 되는 작업인지/되돌릴 수 있는지/읽기만 하는 작업인지."""
        if task in (T.TASK_EXPORT, T.TASK_EXPORT_RETRY, T.TASK_TRANSFER, T.TASK_RETRY):
            return T.STOP_DONE_RESUME
        if task == T.TASK_APPLY:
            return T.STOP_DONE_APPLY
        if task == T.TASK_UNDO:
            return T.STOP_DONE_UNDO
        if task == T.TASK_PLACE:  # stage50
            return T.STOP_DONE_PLACE
        if task == T.TASK_PLACE_PREVIEW:
            return T.STOP_DONE_PLACE_PREVIEW
        return T.STOP_DONE_READONLY


# stage30b: '되돌리기'는 파일을 쓰는 작업이라 강제로 버린 옛 쓰기 작업이 남아 있으면 시작하지 않는다
_WRITING_TASKS = _WRITING_TASKS + (T.TASK_UNDO,)
# stage50: 받은 파일 놓기는 받은 폴더와 원래 위치에 쓰는 작업이다(미리보기도 강제로 버린 옛 놓기가 아직 파일을 옮기는 중이면 어긋난 결과를 내므로 함께 막는다)
_WRITING_TASKS = _WRITING_TASKS + (T.TASK_PLACE, T.TASK_PLACE_PREVIEW)
