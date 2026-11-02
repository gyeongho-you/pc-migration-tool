"""컨트롤러의 '바꿔 보며 설정 찾기'(설정 기록 모드) 부분(stage60). Controller 가 이 믹스인을 상속한다.

흐름: 프로그램 고르기 -> 훑을 폴더 확인(이유 표시, 체크로 제외, 직접 추가) -> 시작(변경 전) -> 기준선 -> 설정 바꾸고 변경 후(반복) -> 후보 표 -> 초안 저장.
- 백엔드 호출은 self.api.run_record_* 한 곳(가짜 api 로 바꿔 끼울 수 있다). 대상 폴더는 읽기만 하고, 쓰는 곳은 기록 폴더뿐이다.
- 취소하면 그 스냅샷은 저장되지 않고 같은 단계에 머문다(백엔드가 보장). 어떤 경우에도 결과 보고서(record-last-report.json)는 백엔드가 저장한다.
- 오류는 기존 _run 의 report_error 가 오류 로그에 남긴다. 백엔드의 사용자 오류 문장은 명령줄 낱말을 지워 안내로 보인다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from .. import errorlog
from ..cancel import Cancelled  # noqa: F401  (취소는 _run 이 처리한다)
from . import record_flow as rf
from . import state as S
from . import texts as T
from .controller_common import GuardError
from .place_flow import plain_cli


def _guard_from(e: Exception) -> GuardError:
    return GuardError(plain_cli(str(e)))


class RecordMixin:
    record_documents_fn: Callable[[], str | None] | None = None  # 시험에서 문서 폴더 위치를 바꿔 끼우는 자리
    record_process_list_fn: Callable[[], list | None] | None = None  # 시험에서 실행 중 프로세스 목록을 바꿔 끼우는 자리
    record_version_info_fn: Callable[[str], dict] | None = None  # 시험에서 exe 버전 정보를 바꿔 끼우는 자리
    record_work_dir_fn: Callable[[str], str] | None = None  # 시험에서 기록 폴더 위치를 바꿔 끼우는 자리(기본: 프로그램 이름별 기록 폴더)

    # ------------------------------------------------------------------ 들어가기/나가기
    def record_open(self, from_screen: str, program_name: str = "") -> None:
        """① 화면의 링크에서 들어간다. program_name 이 있으면(이미 고른 프로그램) 목록에서 미리 고른다."""
        st = self.record_state
        st.from_screen = from_screen or S.OLD_DISCOVER
        if st.session is None:  # 이미 기록 중이면 그 화면 그대로 다시 보여 준다
            st.room = rf.ROOM_PICK
            if program_name:
                self._record_preselect(program_name)
        self.go(S.RECORD)

    def record_back_screen(self) -> str:
        return self.record_state.from_screen or S.OLD_DISCOVER

    def _record_preselect(self, name: str) -> None:
        st = self.record_state
        for rp in st.programs:
            if rp.program.name == name:
                st.program = rp.program
                return
        st.wanted = name  # 목록을 읽은 뒤에 고른다

    def record_goto(self, room: str) -> None:
        st = self.record_state
        if room not in (rf.ROOM_PICK, rf.ROOM_FOLDERS, rf.ROOM_STEPS, rf.ROOM_CANDS, rf.ROOM_DRAFT):
            raise GuardError(T.G_NOT_IN_LIST)
        if room in (rf.ROOM_STEPS, rf.ROOM_CANDS, rf.ROOM_DRAFT) and st.session is None:
            raise GuardError(T.G_REC_NEED_SESSION)
        st.room = room
        self.emit("record_changed", room)

    # ------------------------------------------------------------------ 화면 A: 프로그램 고르기
    def record_set_filter(self, only_norule: bool | None = None, text: str | None = None) -> None:
        st = self.record_state
        if only_norule is not None:
            st.only_norule = bool(only_norule)
        if text is not None:
            st.filter_text = text
        self.emit("record_changed", "filter")

    def record_program_rows(self) -> list[dict]:
        return rf.program_rows(self.record_state)

    def record_load_programs(self) -> None:
        """설치된 프로그램 목록을 읽는다(읽기 전용). 이미 읽었으면 다시 읽지 않는다."""
        st = self.record_state
        if st.programs_loaded:
            self.emit("record_changed", "programs")
            return

        def work(progress, token):
            return self.api.run_record_programs(self.state.rules_dir, deps=self.deps)

        def done(res):
            st.programs, st.programs_loaded = list(res), True
            if st.wanted:
                name, st.wanted = st.wanted, ""
                self._record_preselect(name)
            self.emit("record_changed", "programs")
        self._run(T.TASK_REC_PROGRAMS, work, done, cancellable=True)

    def record_pick(self, index: int) -> None:
        st = self.record_state
        if not 0 <= index < len(st.programs):
            raise GuardError(T.G_REC_PICK)
        if st.program is st.programs[index].program:
            return  # 이미 고른 프로그램(표를 다시 그리며 같은 줄을 고르는 이벤트가 되풀이되지 않게)
        if st.session is not None:
            raise GuardError(T.G_REC_HAS_PREVIOUS)  # 기록 중에는 다른 프로그램으로 바꾸지 않는다(처음부터 다시 후에)
        st.program = st.programs[index].program
        st.plan, st.previous = None, None
        self.emit("record_changed", "pick")

    # ------------------------------------------------------------------ 화면 B: 폴더 확인
    def _record_work(self) -> str:
        name = self.record_state.program.name
        if self.record_work_dir_fn:
            return self.record_work_dir_fn(name)
        from .. import record_mode as rm
        return rm.default_work_dir(name)

    def record_find_folders(self) -> None:
        """고른 프로그램의 정보로 훑을 폴더를 추정한다(폴더 이름만 봄). 같은 프로그램의 이전 기록도 확인한다."""
        st = self.record_state
        if st.program is None:
            raise GuardError(T.G_REC_PICK)
        program = st.program

        def work(progress, token):
            plan, running = self.api.run_record_infer(program, documents_fn=self.record_documents_fn,
                                                      process_list_fn=self.record_process_list_fn,
                                                      version_info_fn=self.record_version_info_fn)
            return plan, running, self.api.record_resume(self._record_work())

        def done(res):
            plan, running, prev = res
            st.plan, st.running = plan, list(running)
            st.previous = rf.previous_summary(prev)
            st.previous_session = prev
            st.room = rf.ROOM_FOLDERS
            self.emit("record_changed", rf.ROOM_FOLDERS)
        self._run(T.TASK_REC_FOLDERS, work, done, cancellable=True)

    def record_folder_rows(self) -> list[dict]:
        return rf.folder_rows(self.record_state)

    def record_toggle_folder(self, token: str, on: bool) -> None:
        """폴더 줄의 체크. 추정한 폴더는 켜고 끄고, 제외 후보는 켜면 '추가'한 폴더가 된다."""
        from .. import record_infer as ri
        st = self.record_state
        if st.plan is None:
            raise GuardError(T.G_REC_NEED_PLAN)
        if st.session is not None:
            raise GuardError(T.G_REC_HAS_PREVIOUS)
        if any(f.token == token for f in st.plan.folders):
            for f in st.plan.folders:
                if f.token == token:
                    f.enabled = bool(on)
        elif on:
            for tok, _why in list(st.plan.excluded):
                if tok == token:
                    st.plan.folders.append(ri.Folder(tok, _expand(tok), T.REC_ADDED_REASON, T.REC_ADDED_CONF, source="added"))
                    st.plan.excluded = [x for x in st.plan.excluded if x[0] != tok]
                    break
        self.emit("record_changed", "folders")

    def record_add_folder(self, path: str) -> None:
        from .. import record_infer as ri
        st = self.record_state
        if st.plan is None:
            raise GuardError(T.G_REC_NEED_PLAN)
        if st.session is not None:
            raise GuardError(T.G_REC_HAS_PREVIOUS)
        try:
            ri.add_roots(st.plan, [path])
        except ValueError as e:
            raise _guard_from(e) from e
        self.emit("record_changed", "folders")

    def record_can_start(self) -> tuple[bool, str]:
        st = self.record_state
        if st.plan is None:
            return False, T.G_REC_NEED_PLAN
        if st.session is None and st.previous:
            return False, T.G_REC_HAS_PREVIOUS
        if not st.plan.enabled():
            return False, T.G_REC_NO_FOLDER
        return True, ""

    def record_start(self) -> None:
        st = self.record_state
        ok, why = self.record_can_start()
        if not ok:
            raise GuardError(why)
        program, plan = st.program, st.plan

        def work(progress, token):
            try:
                return self.api.run_record_start(program, plan, self._record_work(), progress_info=progress, cancel=token,
                                                 documents_fn=self.record_documents_fn, process_list_fn=self.record_process_list_fn)
            except Exception as e:  # noqa: BLE001 - 백엔드의 사용자 오류는 안내 문구로 바꿔 알린다(그 밖의 오류는 그대로 오류 창)
                if type(e).__name__ == "RecordError":
                    raise _guard_from(e) from e
                raise

        def done(res):
            session, snap = res
            st.session, st.previous = session, None
            st.analysis, st.rows, st.secret_rows, st.checked, st.draft, st.draft_note = None, [], [], set(), None, ""
            st.notes = rf.snapshot_notes(snap, T.REC_LABELS["before"])
            st.room = rf.ROOM_STEPS
            self.emit("record_changed", rf.ROOM_STEPS)
        self._run(T.TASK_REC_SNAP, work, done, cancellable=True)

    def record_resume(self) -> None:
        """같은 프로그램의 이전 기록을 이어서 한다."""
        st = self.record_state
        prev = st.previous_session
        if prev is None:
            raise GuardError(T.G_REC_NEED_SESSION)
        st.session, st.previous = prev, None
        st.analysis, st.rows, st.secret_rows, st.checked, st.draft, st.draft_note = None, [], [], set(), None, ""
        st.notes = []
        st.room = rf.ROOM_STEPS
        self.emit("record_changed", rf.ROOM_STEPS)

    # ------------------------------------------------------------------ 화면 C: 기준선/변경 후
    def record_snapshot(self) -> None:
        st = self.record_state
        if st.session is None:
            raise GuardError(T.G_REC_NEED_SESSION)
        session = st.session
        kind = rf.next_action(session)
        label = T.REC_LABELS["baseline"] if kind == "baseline" else T.REC_LABELS["after"].format(n=rf.after_count(session) + 1)

        def work(progress, token):
            try:
                return self.api.run_record_snapshot(session, progress_info=progress, cancel=token,
                                                    documents_fn=self.record_documents_fn, process_list_fn=self.record_process_list_fn)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "RecordError":
                    raise _guard_from(e) from e
                raise

        def done(res):
            st.notes = rf.snapshot_notes(res, label)
            st.analysis, st.rows, st.secret_rows, st.checked = None, [], [], set()  # 새 기록이 생기면 이전 후보 표는 무효
            st.room = rf.ROOM_STEPS
            self.emit("record_changed", rf.ROOM_STEPS)
        self._run(T.TASK_REC_SNAP, work, done, cancellable=True)

    def record_can_show_candidates(self) -> tuple[bool, str]:
        st = self.record_state
        if st.session is None:
            return False, T.G_REC_NEED_SESSION
        if rf.after_count(st.session) < 1:
            return False, T.REC_TASK_AFTER.split("\n")[0]
        return True, ""

    # ------------------------------------------------------------------ 화면 D: 후보 표
    def record_show_candidates(self) -> None:
        st = self.record_state
        ok, why = self.record_can_show_candidates()
        if not ok:
            raise GuardError(why)
        session = st.session

        def work(progress, token):
            try:
                return self.api.run_record_diff(session)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "RecordError":
                    raise _guard_from(e) from e
                raise

        def done(a):
            st.analysis = a
            st.rows, st.secret_rows = rf.cand_rows(a)
            st.rows = st.rows[:rf.MAX_CAND_ROWS] if len(st.rows) > rf.MAX_CAND_ROWS else st.rows
            st.checked = {r["path"] for r in st.rows if r["checked"]}
            st.draft, st.draft_note = None, ""
            st.room = rf.ROOM_CANDS
            self.emit("record_changed", rf.ROOM_CANDS)
        self._run(T.TASK_REC_DIFF, work, done, cancellable=True)

    def record_toggle_candidate(self, path: str, on: bool) -> bool:
        """후보 줄 체크. 담을 수 없는 줄(참고 등)은 바뀌지 않고 False."""
        st = self.record_state
        row = next((r for r in st.rows if r["path"] == path), None)
        if row is None or not row["checkable"]:
            return False
        (st.checked.add if on else st.checked.discard)(path)
        self.emit("record_changed", "checked")
        return True

    def record_candidates_notice(self) -> tuple[str, str]:
        a = self.record_state.analysis
        return rf.candidates_notice(a) if a is not None else ("", "")

    # ------------------------------------------------------------------ 화면 E: 초안 저장
    def record_default_draft_path(self) -> str:
        from .. import record_base as rb
        st = self.record_state
        if st.session is None:
            return ""
        return str(Path(st.session.work) / rb.DRAFT_FILE)

    def record_goto_draft(self) -> None:
        st = self.record_state
        if st.analysis is None:
            raise GuardError(T.G_REC_NEED_SESSION)
        if not st.checked:
            raise GuardError(T.G_REC_NONE_CHECKED)
        st.room = rf.ROOM_DRAFT
        self.emit("record_changed", rf.ROOM_DRAFT)

    def record_save_draft(self, output: str = "") -> None:
        st = self.record_state
        if st.session is None or st.analysis is None:
            raise GuardError(T.G_REC_NEED_SESSION)
        if not st.checked:
            raise GuardError(T.G_REC_NONE_CHECKED)
        session, selected = st.session, set(st.checked)
        out = (output or "").strip() or None

        def work(progress, token):
            try:
                return self.api.run_record_draft(session, out, selected)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "RecordError":
                    raise _guard_from(e) from e
                raise

        def done(res):
            a, rule, path = res
            st.analysis = a
            if rule is None:
                st.draft, st.draft_note = None, T.REC_NO_DRAFT.format(reason=rf.no_draft_text(a))
            else:
                paths = rule.get("paths", [])
                st.draft = {"path": str(path), "d": sum(1 for p in paths if p.get("kind") == "dir"),
                            "f": sum(1 for p in paths if p.get("kind") != "dir"), "rid": rule.get("id", "")}
                st.draft_note = ""
            self.emit("record_changed", "draft_saved")
        self._run(T.TASK_REC_DRAFT, work, done)

    def record_draft_text(self) -> str:
        st = self.record_state
        if st.draft:
            return T.REC_DRAFT_SAVED.format(**st.draft)
        return st.draft_note

    # ------------------------------------------------------------------ 처음부터 다시
    def record_can_reset(self) -> bool:
        st = self.record_state
        return st.program is not None and (st.session is not None or bool(st.previous))

    def record_reset_message(self) -> str:
        return T.REC_RESET_CONFIRM

    def record_reset(self) -> None:
        """이전 기록을 지우지 않고 .bak 으로 보관한 뒤 빈 상태로(화면 A 로). 확인창은 화면이 먼저 띄운다."""
        st = self.record_state
        if st.program is None:
            raise GuardError(T.G_REC_PICK)
        work_dir = self._record_work()

        def work(progress, token):
            try:
                return self.api.run_record_reset(work_dir)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "RecordError":
                    raise _guard_from(e) from e
                raise

        def done(res):
            n = len(getattr(res, "moved", []) or [])
            kept = list(getattr(res, "kept_foreign", []) or [])
            st.session, st.plan, st.previous, st.analysis = None, None, None, None
            st.rows, st.secret_rows, st.checked, st.draft, st.draft_note = [], [], set(), None, ""
            st.previous_session = None
            st.notes = [T.REC_RESET_DONE.format(n=n) if n else T.REC_RESET_NONE]
            if kept:
                st.notes.append(T.REC_RESET_KEPT.format(names=", ".join(kept[:5])))
            st.room = rf.ROOM_PICK
            self.emit("record_changed", rf.ROOM_PICK)
        self._run(T.TASK_REC_RESET, work, done)

    def record_work_note(self) -> str:
        st = self.record_state
        if st.session is None:
            return ""
        return T.REC_WORK_NOTE.format(path=errorlog.tokenize_home(str(st.session.work)))


def _expand(token: str) -> str:
    from ..envpaths import expand_env
    return expand_env(token)
