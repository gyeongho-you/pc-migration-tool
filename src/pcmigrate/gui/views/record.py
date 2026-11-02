"""바꿔 보며 설정 찾기 화면(stage60): 프로그램 고르기 -> 훑을 폴더 확인 -> 기록 단계(변경 전/기준선/변경 후) -> 후보 표 -> 초안 저장.

한 화면 안의 '방' 5개를 한 번에 하나씩 보여 준다(place 화면과 같은 방식: 위에 단계 표시줄, 가운데 스크롤 본문, 맨 아래 고정 단추 막대).
화면의 숫자와 문장은 컨트롤러(record_* )와 record_flow 의 순수 함수가 만든 것을 그리기만 한다. 대상 폴더는 읽기만 한다.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import record_flow as rf
from .. import state as S
from .. import texts as T
from .common import (BODY_FONT, MUTED_FG, SUB_FONT, ActionButton, BaseView, Collapsible, FoldLabel, Notice, PathField, ScrollBody,
                     StepStrip, TreeTooltip, autowrap, bind_alt, cell_row, clear, make_tree, set_shown)

__all__ = ["RecordView"]

_TASKS = {T.TASK_REC_PROGRAMS, T.TASK_REC_FOLDERS, T.TASK_REC_SNAP, T.TASK_REC_DIFF, T.TASK_REC_DRAFT, T.TASK_REC_RESET}


class RecordView(BaseView):
    name = S.RECORD

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(self.c.record_back_screen()))
        self._build_action_bar()
        self.header(T.REC_HEADING, T.REC_SUB)
        self.add_status_card(_TASKS)
        strip_row = ttk.Frame(self)  # 단계 표시줄(기록을 시작한 뒤에만) + 처음부터 다시
        strip_row.pack(fill="x", pady=(0, 6))
        self.steps_strip = StepStrip(strip_row, T.REC_STEP_NAMES, label=T.REC_STEPS_LABEL)
        self.steps_strip.pack(side="left")
        self.reset_btn = ActionButton(strip_row, text=T.BTN_REC_RESET, kind="plain", command=self._reset)
        self.reset_btn.pack(side="right")
        self.strip_row = strip_row
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = scroll.inner
        self.rooms: dict[str, ttk.Frame] = {}
        self._prog_iids: dict[str, int] = {}
        self._folder_iids: dict[str, str] = {}
        self._cand_iids: dict[str, str] = {}
        self._loading = False
        self._build_pick(body)
        self._build_folders(body)
        self._build_steps(body)
        self._build_cands(body)
        self._build_draft(body)
        for room, frame in self.rooms.items():
            if room != rf.ROOM_PICK:
                set_shown(frame, False)
        self.finish()

    # ------------------------------------------------------------------ 단추 막대
    def _build_action_bar(self) -> None:
        br = self.bar_right
        assert br is not None
        self.main_btn = ActionButton(br, text=T.BTN_REC_NEXT_FOLDERS, command=self._main, kind="primary")
        self.alt_btn = ActionButton(br, text="", command=self._alt, kind="plain")
        self.reason_label = ttk.Label(br, text="", foreground="#9a6700", justify="right", wraplength=320)
        self.primary = self.main_btn
        self.main_btn.pack(side="right")
        self.alt_btn.pack(side="right", padx=(0, 8))
        self.reason_label.pack(side="right", padx=10)
        bind_alt(self.app, self.main_btn, lambda: self.app.current == self.name)

    # ------------------------------------------------------------------ 방 A: 프로그램 고르기
    def _room(self, body, key: str) -> ttk.Frame:
        f = ttk.Frame(body)
        f.pack(fill="x")
        self.rooms[key] = f
        return f

    def _build_pick(self, body) -> None:
        r = self._room(body, rf.ROOM_PICK)
        lab = ttk.Label(r, text=T.REC_PICK_LABEL, justify="left")
        lab.pack(anchor="w", fill="x", pady=(2, 4))
        autowrap(lab, 40)
        row = ttk.Frame(r)
        row.pack(fill="x")
        self.only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text=T.REC_ONLY_NORULE, variable=self.only_var, command=self._filter_changed).pack(side="left")
        self.filter_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.filter_var, width=28).pack(side="right")
        ttk.Label(row, text=T.REC_FILTER_LABEL).pack(side="right", padx=(0, 6))
        self.filter_var.trace_add("write", lambda *a: self._filter_changed())
        cols = [("name", T.REC_COLS_PROGRAM[0], 300), ("pub", T.REC_COLS_PROGRAM[1], 200), ("where", T.REC_COLS_PROGRAM[2], 300)]
        frame, self.prog_tree = make_tree(r, cols, height=11)
        frame.pack(fill="x", pady=(6, 0))
        self.prog_tree.bind("<<TreeviewSelect>>", self._prog_selected)
        self.pick_hint = FoldLabel(r, text=T.REC_PICK_HINT, justify="left", anchor="w", fg=MUTED_FG)
        self.pick_hint.pack(anchor="w", fill="x", pady=(4, 0))
        autowrap(self.pick_hint, 40)
        self.pick_note = Notice(r, "", "info")
        self.pick_note.pack(fill="x", pady=(4, 0))

    # ------------------------------------------------------------------ 방 B: 이 폴더들을 훑습니다
    def _build_folders(self, body) -> None:
        r = self._room(body, rf.ROOM_FOLDERS)
        self.folders_head = ttk.Label(r, text="", font=SUB_FONT, justify="left")
        self.folders_head.pack(anchor="w", fill="x", pady=(2, 2))
        note = ttk.Label(r, text=T.REC_FOLDERS_NOTE, justify="left")
        note.pack(anchor="w", fill="x", pady=(0, 4))
        autowrap(note, 40)
        cols = [("on", T.REC_COLS_FOLDER[0], 56), ("conf", T.REC_COLS_FOLDER[1], 70), ("path", T.REC_COLS_FOLDER[2], 340),
                ("why", T.REC_COLS_FOLDER[3], 420)]
        frame, self.folder_tree = make_tree(r, cols, height=7)
        frame.pack(fill="x")
        self.folder_tree.tag_configure("off", foreground="#767676")
        self.folder_tree.bind("<Button-1>", self._folder_click)
        self.folder_tree.bind("<space>", self._folder_key)
        self.folder_tips = TreeTooltip(self.folder_tree, self._folder_tip)
        self.folder_full: dict[str, tuple[str, str]] = {}
        row = ttk.Frame(r)
        row.pack(fill="x", pady=(6, 0))
        self.add_folder_btn = ttk.Button(row, text=T.BTN_REC_ADD_FOLDER, command=self._add_folder)
        self.add_folder_btn.pack(side="left")
        self.no_folder = Notice(r, "", "warn")
        self.no_folder.pack(fill="x", pady=(6, 0))
        self.running_note = Notice(r, "", "warn")
        self.running_note.pack(fill="x", pady=(6, 0))
        self.previous_note = Notice(r, "", "info")
        self.previous_note.pack(fill="x", pady=(6, 0))
        self.resume_btn = ActionButton(r, text=T.BTN_REC_RESUME, kind="plain", command=lambda: self.guarded(self.c.record_resume))
        self.resume_btn.pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------------ 방 C: 기록 단계
    def _build_steps(self, body) -> None:
        r = self._room(body, rf.ROOM_STEPS)
        box = tk.Frame(r, bg="#eef4fb", highlightbackground="#2b6cb0", highlightthickness=1, padx=12, pady=10)
        box.pack(fill="x", pady=(2, 6))
        self.step_title = tk.Label(box, text="", bg="#eef4fb", font=SUB_FONT, anchor="w", justify="left")
        self.step_title.pack(anchor="w", fill="x")
        self.step_text = tk.Label(box, text="", bg="#eef4fb", font=BODY_FONT, anchor="w", justify="left")
        self.step_text.pack(anchor="w", fill="x", pady=(4, 0))
        autowrap(self.step_text, 60)
        self.again_label = FoldLabel(box, text="", bg="#eef4fb", fg=MUTED_FG, anchor="w", justify="left")
        self.again_label.pack(anchor="w", fill="x", pady=(6, 0))
        autowrap(self.again_label, 60)
        self.step_notes = Notice(r, "", "ok")
        self.step_notes.pack(fill="x", pady=(2, 4))
        self.log_label = FoldLabel(r, text="", anchor="w", justify="left")
        self.log_label.pack(anchor="w", fill="x")
        self.work_label = FoldLabel(r, text="", anchor="w", justify="left", fg=MUTED_FG)
        self.work_label.pack(anchor="w", fill="x", pady=(4, 0))
        autowrap(self.work_label, 40)

    # ------------------------------------------------------------------ 방 D: 후보 표
    def _build_cands(self, body) -> None:
        r = self._room(body, rf.ROOM_CANDS)
        self.cands_head = ttk.Label(r, text="", font=SUB_FONT, justify="left")
        self.cands_head.pack(anchor="w", fill="x", pady=(2, 2))
        autowrap(self.cands_head, 40)
        self.noise_label = FoldLabel(r, text="", anchor="w", justify="left", fg=MUTED_FG)
        self.noise_label.pack(anchor="w", fill="x")
        self.cand_notice = Notice(r, "", "warn")
        self.cand_notice.pack(fill="x", pady=(4, 0))
        cols = [("sel", T.REC_COLS_CAND[0], 56), ("conf", T.REC_COLS_CAND[1], 70), ("path", T.REC_COLS_CAND[2], 360),
                ("size", T.REC_COLS_CAND[3], 80), ("why", T.REC_COLS_CAND[4], 330)]
        frame, self.cand_tree = make_tree(r, cols, height=9)
        self.cand_frame = frame
        frame.pack(fill="x", pady=(4, 0))
        self.cand_tree.tag_configure("ref", foreground="#767676")
        self.cand_tree.bind("<Button-1>", self._cand_click)
        self.cand_tree.bind("<space>", self._cand_key)
        self.cand_tips = TreeTooltip(self.cand_tree, self._cand_tip)
        self.cand_full: dict[str, tuple[str, str]] = {}
        self.cand_hint = FoldLabel(r, text=T.REC_CAND_NOTE, anchor="w", justify="left", fg=MUTED_FG)
        self.cand_hint.pack(anchor="w", fill="x", pady=(4, 0))
        autowrap(self.cand_hint, 40)
        self.secret_note = Notice(r, "", "warn")
        self.secret_note.pack(fill="x", pady=(4, 0))
        self.secret_fold = Collapsible(r, "", "")
        self.secret_fold.pack(anchor="w", fill="x")
        self.secret_text = self.secret_fold.add_text("")
        self.more_label = FoldLabel(r, text="", anchor="w", justify="left", fg=MUTED_FG)
        self.more_label.pack(anchor="w", fill="x")

    # ------------------------------------------------------------------ 방 E: 초안 저장
    def _build_draft(self, body) -> None:
        r = self._room(body, rf.ROOM_DRAFT)
        ttk.Label(r, text=T.REC_DRAFT_HEAD, font=SUB_FONT).pack(anchor="w", pady=(2, 2))
        self.draft_summary = ttk.Label(r, text="", justify="left")
        self.draft_summary.pack(anchor="w", fill="x")
        self.out_var = tk.StringVar()
        self.out_field = PathField(r, self.out_var, self._pick_out, label=T.REC_DRAFT_PATH_LABEL, hint=T.REC_DRAFT_PATH_HINT)
        self.out_field.pack(fill="x", pady=(6, 0))
        self.draft_result = Notice(r, "", "ok")
        self.draft_result.pack(fill="x", pady=(8, 0))
        self.open_dir_btn = ActionButton(r, text=T.BTN_REC_OPEN_FOLDER, kind="link", command=self._open_dir)
        self.open_dir_btn.pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------------ 표시
    def on_show(self) -> None:
        st = self.c.record_state
        self._loading = True
        try:
            self.only_var.set(st.only_norule)
            self.filter_var.set(st.filter_text)
        finally:
            self._loading = False
        if not st.programs_loaded and not self.c.state.busy:
            try:
                self.c.record_load_programs()
            except Exception:  # noqa: BLE001 - 목록을 못 읽어도 화면은 열린다(오류는 컨트롤러가 오류 로그에 남긴다)
                pass
        self._render()
        self.after_idle(self.scroll.scroll_to_top)

    def on_event(self, kind, payload) -> None:
        if kind in ("record_changed", "busy"):
            self._render()

    def _render(self) -> None:
        st = self.c.record_state
        room = st.room
        for key, frame in self.rooms.items():
            set_shown(frame, key == room)
        step = rf.current_step(st)
        if step:
            self.steps_strip.pack(side="left")
            self.steps_strip.set(step)
        else:
            self.steps_strip.pack_forget()
        if self.c.record_can_reset():
            self.reset_btn.pack(side="right")
        else:
            self.reset_btn.pack_forget()
        {rf.ROOM_PICK: self._render_pick, rf.ROOM_FOLDERS: self._render_folders, rf.ROOM_STEPS: self._render_steps,
         rf.ROOM_CANDS: self._render_cands, rf.ROOM_DRAFT: self._render_draft}[room]()
        self._update_buttons()

    def _render_pick(self) -> None:
        st = self.c.record_state
        rows = self.c.record_program_rows()
        clear(self.prog_tree)
        self._prog_iids = {}
        keep = ""
        for r in rows:
            iid = self.prog_tree.insert("", "end", values=(r["name"], r["publisher"], r["where"]))
            self._prog_iids[iid] = r["index"]
            if st.program is not None and st.programs[r["index"]].program is st.program:
                keep = iid
        self._loading = True
        try:
            if keep:
                self.prog_tree.selection_set(keep)
                self.prog_tree.see(keep)
        finally:
            self._loading = False
        loaded_empty = st.programs_loaded and not st.programs
        self.pick_hint.configure(text=T.REC_PICK_HINT if not loaded_empty else "")
        notes = [T.REC_NO_PROGRAMS] if loaded_empty else list(st.notes)
        if notes:
            self.pick_note.set("\n".join(notes), "info")
        else:
            self.pick_note.clear()

    def _render_folders(self) -> None:
        st = self.c.record_state
        name = st.program.name if st.program is not None else ""
        self.folders_head.configure(text=T.REC_FOLDERS_HEAD.format(name=name))
        clear(self.folder_tree)
        self._folder_iids, self.folder_full = {}, {}
        rows = self.c.record_folder_rows()
        for r in rows:
            mark = T.CHECKED if r["on"] else T.UNCHECKED
            iid = self.folder_tree.insert("", "end", values=(mark, r["conf"], T.ellipsize_path(r["token"], 54), r["reason"]),
                                          tags=() if r["on"] else ("off",))
            self._folder_iids[iid] = r["token"]
            self.folder_full[iid] = (r["token"], r["reason"])
        self.folder_tree.configure(height=max(3, min(9, len(rows))))
        plan = st.plan
        self.no_folder.set(T.REC_NO_FOLDER, "warn") if (plan is not None and not plan.enabled() and not plan.folders) else self.no_folder.clear()
        if st.running and st.program is not None:
            self.running_note.set(T.REC_RUNNING.format(name=st.program.name, procs=", ".join(st.running[:5])), "warn")
        else:
            self.running_note.clear()
        if st.previous:
            self.previous_note.set(T.REC_PREVIOUS.format(**st.previous), "info")
            set_shown(self.resume_btn, True)
        else:
            self.previous_note.clear()
            set_shown(self.resume_btn, False)
        self.add_folder_btn.state(["disabled"] if st.previous else ["!disabled"])

    def _render_steps(self) -> None:
        st = self.c.record_state
        kind = rf.next_action(st.session)
        self.step_title.configure(text=T.REC_STEP_TITLE.format(step=T.REC_STEP_NAMES[2] if kind == "baseline" else T.REC_STEP_NAMES[3]))
        self.step_text.configure(text=T.REC_TASK_BASELINE if kind == "baseline" else T.REC_TASK_AFTER)
        self.again_label.configure(text=T.REC_AFTER_AGAIN if kind == "after" else "")
        self.step_notes.set("\n".join(st.notes), "ok") if st.notes else self.step_notes.clear()
        self.log_label.configure(text="\n".join([T.REC_LOG_HEAD, *rf.log_lines(st.session)]) if st.session else "")
        self.work_label.configure(text=self.c.record_work_note())

    def _render_cands(self) -> None:
        st = self.c.record_state
        a = st.analysis
        if a is None:
            return
        name = st.program.name if st.program is not None else ""
        self.cands_head.configure(text=T.REC_CANDS_HEAD.format(name=name, rounds=a.rounds, base=a.baselines))
        self.noise_label.configure(text=T.REC_CANDS_NOISE.format(n=a.noise_files) if a.noise_files else "")
        level, text = self.c.record_candidates_notice()
        self.cand_notice.set(text, level) if text else self.cand_notice.clear()
        clear(self.cand_tree)
        self._cand_iids, self.cand_full = {}, {}
        for r in st.rows:
            if r["checkable"]:
                mark = T.CHECKED if r["path"] in st.checked else T.UNCHECKED
            else:
                mark = T.LOCKED
            iid = self.cand_tree.insert("", "end", values=(mark, r["conf"], T.ellipsize_path(r["path"], 60), r["size"], r["reason"]),
                                        tags=() if r["checkable"] else ("ref",))
            self._cand_iids[iid] = r["path"]
            self.cand_full[iid] = (r["path"], r["reason"])
        self.cand_tree.configure(height=max(3, min(12, len(st.rows))))
        set_shown(self.cand_frame, bool(st.rows))
        set_shown(self.cand_hint, bool(st.rows))
        if st.secret_rows:
            self.secret_note.set(T.REC_SECRET_NOTICE.format(n=len(st.secret_rows)), "warn")
            self.secret_fold.title = T.REC_SECRET_FOLD.format(n=len(st.secret_rows))
            self.secret_fold.title_open = T.REC_SECRET_FOLD_OPEN.format(n=len(st.secret_rows))
            self.secret_fold.toggle_btn.configure(text=self.secret_fold.title_open if self.secret_fold.open else self.secret_fold.title)
            self.secret_text.configure(text="\n".join(r["path"] for r in st.secret_rows[:50]))
            set_shown(self.secret_fold, True)
        else:
            self.secret_note.clear()
            set_shown(self.secret_fold, False)
        total = len(self.c.record_state.analysis.candidates)
        more = total - len(st.rows) - len(st.secret_rows)
        self.more_label.configure(text=T.REC_MORE_ROWS.format(shown=rf.MAX_CAND_ROWS) if more > 0 else "")
        if not st.rows and not st.secret_rows and not text:
            self.cand_notice.set(T.REC_NO_CANDS, "info")

    def _render_draft(self) -> None:
        st = self.c.record_state
        self.draft_summary.configure(text=T.REC_DRAFT_SUMMARY.format(n=len(st.checked)))
        if not self.out_var.get():
            self.out_var.set(self.c.record_default_draft_path())
        text = self.c.record_draft_text()
        if text:
            self.draft_result.set(text, "ok" if st.draft else "warn")
        else:
            self.draft_result.clear()
        set_shown(self.open_dir_btn, bool(st.draft))

    def _update_buttons(self) -> None:
        st = self.c.record_state
        busy = self.c.state.busy
        room = st.room
        reason = ""
        main_text, main_ok = T.BTN_REC_NEXT_FOLDERS, st.program is not None
        alt_text = ""
        if room == rf.ROOM_PICK:
            if not main_ok:
                reason = T.G_REC_PICK
        elif room == rf.ROOM_FOLDERS:
            main_text = T.BTN_REC_START
            main_ok, reason = self.c.record_can_start()
            alt_text = T.BTN_BACK
        elif room == rf.ROOM_STEPS:
            kind = rf.next_action(st.session)
            n_after = rf.after_count(st.session)
            if n_after >= 1:
                main_text, main_ok = T.BTN_REC_CANDS, True
                alt_text = T.BTN_REC_AGAIN
            else:
                main_text = T.BTN_REC_BASELINE if kind == "baseline" else T.BTN_REC_AFTER.format(n=1)
                main_ok = True
        elif room == rf.ROOM_CANDS:
            main_text, main_ok = T.BTN_REC_DRAFT, bool(st.checked)
            alt_text = T.BTN_REC_BACK_AGAIN
            if not main_ok:
                reason = T.G_REC_NONE_CHECKED
        else:
            main_text, main_ok = T.BTN_REC_SAVE_DRAFT, bool(st.checked)
            alt_text = T.BTN_REC_TO_CANDS
        self.main_btn.configure(text=main_text)
        self.main_btn.state(["!disabled"] if (main_ok and not busy) else ["disabled"])
        if alt_text:
            self.alt_btn.configure(text=alt_text)
            self.alt_btn.state(["disabled"] if busy else ["!disabled"])
            if not self.alt_btn.winfo_manager():
                self.alt_btn.pack(side="right", padx=(0, 8), before=self.reason_label)
        elif self.alt_btn.winfo_manager():
            self.alt_btn.pack_forget()
        self.reason_label.configure(text=reason if not busy else "")
        self.reset_btn.state(["disabled"] if busy else ["!disabled"])

    # ------------------------------------------------------------------ 동작
    def _main(self) -> None:
        st = self.c.record_state
        room = st.room
        if room == rf.ROOM_PICK:
            if st.program is None:
                messagebox.showwarning(T.GUARD_TITLE, T.G_REC_PICK, parent=self)
                return
            self.guarded(self.c.record_find_folders)
        elif room == rf.ROOM_FOLDERS:
            self.guarded(self.c.record_start)
        elif room == rf.ROOM_STEPS:
            if rf.after_count(st.session) >= 1:
                self.guarded(self.c.record_show_candidates)
            else:
                self.guarded(self.c.record_snapshot)
        elif room == rf.ROOM_CANDS:
            self.guarded(self.c.record_goto_draft)
        else:
            self.guarded(self.c.record_save_draft, self.out_var.get())

    def _alt(self) -> None:
        room = self.c.record_state.room
        if room == rf.ROOM_FOLDERS:
            self.guarded(self.c.record_goto, rf.ROOM_PICK)
        elif room == rf.ROOM_STEPS:
            self.guarded(self.c.record_snapshot)  # 다른 설정으로 한 번 더(변경 후 기록 추가)
        elif room == rf.ROOM_CANDS:
            self.guarded(self.c.record_goto, rf.ROOM_STEPS)
        else:
            self.guarded(self.c.record_goto, rf.ROOM_CANDS)

    def _reset(self) -> None:
        if not messagebox.askyesno(T.REC_RESET_TITLE, self.c.record_reset_message(), parent=self):
            return
        self.guarded(self.c.record_reset)

    def _filter_changed(self) -> None:
        if self._loading:
            return
        self.c.record_set_filter(self.only_var.get(), self.filter_var.get())

    def _prog_selected(self, _e=None) -> None:
        if self._loading:
            return
        if self.c.state.busy:  # 작업 중에는 고르기를 받지 않는다(안내 상자도 띄우지 않고 표만 원래 선택으로 되돌린다)
            return
        sel = self.prog_tree.selection()
        if sel and sel[0] in self._prog_iids:
            self.guarded(self.c.record_pick, self._prog_iids[sel[0]])

    def _folder_toggle(self, iid: str) -> None:
        tok = self._folder_iids.get(iid)
        if tok is None:
            return
        row = next((r for r in self.c.record_folder_rows() if r["token"] == tok), None)
        if row is not None:
            self.guarded(self.c.record_toggle_folder, tok, not row["on"])

    def _folder_click(self, event) -> None:
        iid = cell_row(self.folder_tree, event)
        if iid:
            self._folder_toggle(iid)

    def _folder_key(self, _e=None):
        sel = self.folder_tree.selection()
        if sel:
            self._folder_toggle(sel[0])
        return "break"

    def _folder_tip(self, iid: str, col: int) -> str | None:
        full = self.folder_full.get(iid)
        return None if full is None else (full[0] if col == 2 else full[1] if col == 3 else None)

    def _add_folder(self) -> None:
        p = filedialog.askdirectory(parent=self, title=T.REC_ADD_FOLDER_DIALOG, mustexist=True)
        if p:
            self.guarded(self.c.record_add_folder, p.replace("/", "\\"))

    def _cand_toggle(self, iid: str) -> None:
        path = self._cand_iids.get(iid)
        if path is not None:
            on = path not in self.c.record_state.checked
            self.c.record_toggle_candidate(path, on)

    def _cand_click(self, event) -> None:
        iid = cell_row(self.cand_tree, event)
        if iid:
            self._cand_toggle(iid)

    def _cand_key(self, _e=None):
        sel = self.cand_tree.selection()
        if sel:
            self._cand_toggle(sel[0])
        return "break"

    def _cand_tip(self, iid: str, col: int) -> str | None:
        full = self.cand_full.get(iid)
        return None if full is None else (full[0] if col == 2 else full[1] if col == 4 else None)

    def _pick_out(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.REC_SAVE_DIALOG, defaultextension=".json", filetypes=[("JSON", "*.json")],
                                         initialfile="rule-draft.json")
        if p:
            self.out_var.set(p.replace("/", "\\"))

    def _open_dir(self) -> None:
        d = self.c.record_state.draft
        if d and self.c.can_show_in_explorer(d["path"], True):
            self.c.show_in_explorer(d["path"], True)
