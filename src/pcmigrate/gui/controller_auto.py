"""컨트롤러의 '추천대로 한 번에(자동 진행)' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

import traceback
from typing import Any, Callable

from . import state as S, texts as T
from .controller_common import AUTO_FLOWS, AutoRun, AutoStep, FriendlyError, GuardError


class AutoMixin:
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

    # ---- 화면 7: '추천대로 한 번에' 구 PC 마지막 화면
    def auto_export_stopped(self) -> bool:
        """내보내기 단계에서 멈췄는가(다시 시도/처음부터 다시 만들기 단추를 보일 때)."""
        a = self.auto
        st = a.step("export") if a is not None else None
        return bool(a is not None and a.flow == "old" and a.status == "stopped" and st is not None and st.state == "stopped")

    def auto_retry_export(self, restart: bool = False) -> None:
        """멈춘 내보내기를 다시 시도한다(restart=False: 저장점부터 이어서, True: 처음부터)."""
        a = self.auto
        if not self.auto_export_stopped() or not a.export_path:
            raise GuardError(T.G_AUTO_RETRY_NA)
        a.stop_requested, a.what, a.how, a.by_user, a.warning = False, "", "", False, ""
        self._auto_start("export", self.start_export, a.export_path, write_confirmed=True, resume=not restart)

    def auto_after_retry(self, res: Any) -> None:
        """끝난 '한 번에' 진행에서 '읽지 못한 파일 다시 담기'가 끝났을 때 '끝났지만 확인 필요' 를 새 결과로 갱신한다."""
        a = self.auto
        m = getattr(res, "manifest", None)
        if a is None or a.flow != "old" or a.status != "done" or not isinstance(m, dict):
            return
        unreadable = sum(1 for f in m["failed"] if f.get("reason"))
        hard = len(m["failed"]) - unreadable
        a.warning = " ".join(x for x in (T.AUTO_UNREADABLE_WARN.format(n=unreadable) if unreadable else "",
                                         T.AUTO_FAILED_WARN.format(n=hard) if hard else "") if x)
        self._auto_mark("export", "warn" if a.warning else "done")
        self._auto_notify()
