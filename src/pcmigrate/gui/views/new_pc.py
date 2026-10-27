"""신 PC 흐름: 설정 묶음 파일 선택 -> 검사와 미리보기 -> 복원 확인 -> 결과."""
from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from .common import (BODY_FONT, TEXT_FG, DANGER_BG, DANGER_FG, INFO_BG, INFO_FG, MUTED_FG, OK_BG, OK_FG, SUB_FONT, TITLE_FONT, WARN_FG,
                     ActionButton, BaseView, Collapsible, CoverageBox, Notice, PathField, ResultSummary, ScrollBody, TreeTooltip, autowrap, clean_path,
                     clear, make_text, make_tree, open_location, set_text)


class PackageView(BaseView):
    name = S.NEW_PACKAGE

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.START), next_=self._next, next_text=T.BTN_PREVIEW_NEXT)
        self.header(T.PKG_HEADING, T.PKG_SUB)
        self.add_status_card({T.TASK_PREVIEW})
        self.path_var = tk.StringVar(value=self.c.state.package)
        self.path_field = PathField(self, self.path_var, self._browse, label=T.PKG_LABEL, hint=T.PKG_HINT, drives=self.c.list_drives,
                                    on_drive=lambda root: self._browse(root))
        self.path_field.pack(fill="x")
        self.browse = self.path_field.browse_btn
        # 이 PC 에서 찾은 설정 묶음(받은 폴더·바탕화면·다운로드·외장 드라이브, 최신 순). 찾은 것이 있을 때만 보인다. ③ 은 이 PC 안의 작업이라 연결 기능은 없다
        self.found_row = ttk.Frame(self)
        ttk.Label(self.found_row, text=T.PKG_FOUND_LABEL).pack(side="left")
        self.found_var = tk.StringVar(value="")
        self.found_box = ttk.Combobox(self.found_row, textvariable=self.found_var, state="readonly", width=60, values=[])
        self.found_box.pack(side="left", fill="x", expand=True, padx=6)
        self.found_box.bind("<<ComboboxSelected>>", self._found_picked)
        self.found_entries: list[tuple[str, float, int, str]] = []
        note = ttk.Label(self, text=T.PKG_NOTE, justify="left", foreground=MUTED_FG)
        self.note = note
        note.pack(anchor="w", fill="x", pady=10)
        autowrap(note)
        self.primary = self.next_btn
        self.path_var.trace_add("write", lambda *a: self._update())
        self.finish()

    def _update(self) -> None:
        has = bool(clean_path(self.path_var.get()))
        self.next_btn.state(["!disabled"] if has else ["disabled"])
        self.set_next_reason("" if has else T.WHY_PACKAGE_FIRST)

    def on_show(self) -> None:
        self._update()
        self._scan_found()
        self.browse.focus_set()

    def _scan_found(self) -> None:
        """이 PC 에서 설정 묶음을 찾아 목록에 채운다(읽기만 한다). 없으면 줄 자체를 숨긴다."""
        self.found_entries = self.c.package_choices()
        labels = [T.PKG_FOUND_ITEM.format(name=os.path.basename(p), when=time.strftime("%Y-%m-%d %H:%M", time.localtime(m)), where=where,
                                          folder=T.ellipsize_path(os.path.dirname(p), 40)) for p, m, _size, where in self.found_entries]
        self.found_box.configure(values=labels)
        self.found_var.set("")
        if labels:
            if not self.found_row.winfo_manager():
                self.found_row.pack(fill="x", pady=(8, 0), before=self.note)
        else:
            self.found_row.pack_forget()

    def _found_picked(self, _e=None) -> None:
        idx = self.found_box.current()
        if 0 <= idx < len(self.found_entries):
            self.path_var.set(self.found_entries[idx][0])

    def _browse(self, initialdir: str | None = None) -> None:
        p = filedialog.askopenfilename(parent=self, title=T.PKG_OPEN_TITLE, initialdir=initialdir or None,
                                       filetypes=[("설정 묶음 파일", "*.zip"), ("모든 파일", "*.*")])
        if p:
            self.path_var.set(p.replace("/", "\\"))

    def _next(self) -> None:
        self.path_field.normalize()
        path = clean_path(self.path_var.get())
        if not path:
            messagebox.showwarning(T.PKG_NEED_TITLE, T.PKG_NEED, parent=self)
            return
        self.c.set_package(path)
        self.guarded(self.c.start_preview)

    def on_event(self, kind, payload) -> None:
        if kind == "preview_done" and self.c.state.screen == S.NEW_PACKAGE:
            self.c.go(S.NEW_INSTALL)  # 프로그램 설치 확인(건너뛰기 가능) 다음에 미리보기


class PreviewView(BaseView):
    name = S.NEW_PREVIEW

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.NEW_INSTALL), next_=lambda: self.c.go(S.NEW_CONFIRM), next_text=T.BTN_TO_CONFIRM)
        self.header(T.PREV_HEADING, T.PREV_SUB)
        self.summary = ttk.Label(self, text="", justify="left", font=SUB_FONT)
        self.summary.pack(anchor="w", fill="x")
        autowrap(self.summary)
        # 설치 폴더 설정(있을 때만 보임): 기본은 복원 안 함. 체크하면 미리보기를 다시 만든다
        self.install_var = tk.BooleanVar(value=False)
        self.install_check = ttk.Checkbutton(self, variable=self.install_var, command=self._install_changed, text=T.PREV_INSTALL_CHECK)
        self.problem_box = tk.Label(self, text="", anchor="w", justify="left", padx=10, pady=6)  # 이 화면의 유일한 안내 상자
        self.problem_box.pack(fill="x", pady=4)
        self._empty_bg = self.problem_box.cget("bg")
        autowrap(self.problem_box, 40)
        # 요약과 검사 결과 아래는 스크롤된다(창이 낮아도 프로그램별 표와 파일 목록이 줄어 사라지지 않게)
        self.scroll = ScrollBody(self)
        self.scroll.pack(fill="both", expand=True)
        body = ttk.Frame(self.scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        # 프로그램별 복원 선택표: 체크를 끈 프로그램은 읽지도 쓰지도 않는다. 체크를 바꾸면 미리보기를 다시 계산한다
        self.prog_box = ttk.Frame(body)
        ttk.Label(self.prog_box, text=T.PREV_PROG_TITLE, font=SUB_FONT).pack(anchor="w")
        pcols = [("chk", T.PREV_PROG_COLS[0], 60), ("name", T.PREV_PROG_COLS[1], 380), ("new", T.PREV_PROG_COLS[2], 80),
                 ("over", T.PREV_PROG_COLS[3], 80), ("same", T.PREV_PROG_COLS[4], 80)]
        self.prog_frame, self.prog_tree = make_tree(self.prog_box, pcols, height=3)
        self.prog_frame.pack(fill="x", pady=(2, 0))
        self.prog_tree.tag_configure("off", foreground=MUTED_FG)
        self.prog_tree.bind("<Button-1>", self._prog_click)
        self.prog_tree.bind("<space>", self._prog_key)
        self.prog_ids: dict[str, str] = {}
        prog_note_row = ttk.Frame(self.prog_box)
        prog_note_row.pack(fill="x", pady=(2, 0))
        self.prog_note = ttk.Label(prog_note_row, text=T.PREV_PROG_NOTE, foreground=MUTED_FG, justify="left")
        self.prog_note.pack(side="left")
        self.recalc_btn = ttk.Button(prog_note_row, text=T.BTN_PREV_RECALC, command=self._recompute_now)
        self.dirty = ttk.Label(self.prog_box, text="", foreground=WARN_FG, justify="left")
        autowrap(self.dirty, 30)
        self._recalc_timer = None
        cols = [("status", T.PREV_COLS[0], 130), ("target", T.PREV_COLS[1], 460), ("backup", T.PREV_COLS[2], 250),
                ("extra", T.PREV_COLS[3], 110)]
        frame, self.tree = make_tree(body, cols, height=6)
        self.files_frame = frame
        frame.pack(fill="x", pady=4)
        self.cov_fold = Collapsible(body, f"{T.COV_PREVIEW_TITLE} ▸", f"{T.COV_PREVIEW_TITLE} ▾")  # 설정 확인표도 접어 둔다
        self.cov_fold.pack(fill="x")
        self.cov = CoverageBox(self.cov_fold.body, title="")
        self.cov.title_label.pack_forget()
        self.cov.pack(fill="x")
        self.more = Collapsible(body, f"{T.PREV_WARN_TITLE} ▸", f"{T.PREV_WARN_TITLE} ▾")  # 직접 해야 할 일은 접어 둔다
        self.more.pack(fill="x")
        self.lic_link = self.license_link(body)
        self.lic_link.pack(anchor="w")
        self.tree.tag_configure("over", foreground=WARN_FG)
        self.tree.tag_configure("extra", background=DANGER_BG, foreground=DANGER_FG)
        self.full: dict[str, tuple[str, str]] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, ("", ""))[0 if col == 1 else 1] if col in (1, 2) else "")
        tf, self.warn = make_text(self.more.body, height=5)
        tf.pack(fill="x")
        self.finish()

    def on_show(self) -> None:
        self._render()
        self.scroll.scroll_to_top()
        self.next_btn.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "preview_done":
            self._render()
        elif kind == "restore_selection_changed":
            self._render_programs()
            self._update_gate()
            self._schedule_recompute()
        elif kind == "busy" and self.c.state.preview is not None:
            self._update_gate()

    # ---- 프로그램별 복원 선택
    def _render_programs(self) -> None:
        rows = self.c.restore_programs()
        clear(self.prog_tree)
        self.prog_ids = {}
        for r in rows:
            on = r["checked"]
            na = T.PREV_PROG_NA
            iid = self.prog_tree.insert("", "end", tags=() if on else ("off",), values=(
                T.CHECKED if on else T.UNCHECKED, r["name"], r["new"] if on else na, r["overwrite"] if on else na,
                r["same"] if on else na))
            self.prog_ids[iid] = r["id"]
        self.prog_tree.configure(height=max(2, min(4, len(rows))))
        if rows and not self.prog_box.winfo_manager():
            self.prog_box.pack(fill="x", pady=(2, 0), before=self.files_frame)
        elif not rows and self.prog_box.winfo_manager():
            self.prog_box.pack_forget()

    def _prog_toggle(self, iid: str) -> None:
        pid = self.prog_ids.get(iid)
        if pid is None:
            return
        on = pid in self.c.state.restore_skip  # 지금 꺼져 있으면 켠다
        self.guarded(self.c.set_restore_program, pid, on)

    def _prog_click(self, event) -> None:
        iid = self.prog_tree.identify_row(event.y)
        if iid:
            self._prog_toggle(iid)

    def _prog_key(self, _e=None):
        sel = self.prog_tree.selection()
        if sel:
            self._prog_toggle(sel[0])
        return "break"

    def _schedule_recompute(self) -> None:
        """체크를 바꾼 뒤 잠시(연달아 누르는 동안은 기다렸다가) 미리보기를 선택대로 다시 계산한다."""
        if self._recalc_timer is not None:
            try:
                self.after_cancel(self._recalc_timer)
            except tk.TclError:
                pass
        self._recalc_timer = self.after(400, self._recompute_now)

    def _recompute_now(self) -> None:
        self._recalc_timer = None
        if self.c.restore_selection_reason() != T.G_RESTORE_DIRTY:
            return
        if self.c.state.busy:  # 다른 작업이 끝나면 다시 시도한다
            self._recalc_timer = self.after(500, self._recompute_now)
            return
        self.guarded(self.c.restart_preview_for_selection)

    def _update_gate(self) -> None:
        """'복원 확인으로' 단추와 안내: 검사 문제나 체크 상태가 미리보기와 다르면 막는다."""
        p = self.c.state.preview
        if p is None:
            return
        why = self.c.restore_selection_reason()
        dirty = why == T.G_RESTORE_DIRTY
        if dirty:
            self.dirty.configure(text=T.PREV_PROG_DIRTY, foreground=WARN_FG)
        elif why:
            self.dirty.configure(text=T.PREV_PROG_NONE_SELECTED, foreground=DANGER_FG)
        if why and not self.dirty.winfo_manager():
            self.dirty.pack(anchor="w", fill="x")
        elif not why and self.dirty.winfo_manager():
            self.dirty.pack_forget()
        if dirty and not self.recalc_btn.winfo_manager():
            self.recalc_btn.pack(side="left", padx=8)
        elif not dirty and self.recalc_btn.winfo_manager():
            self.recalc_btn.pack_forget()
        if self.c.preview_problems():
            return
        if why:
            self.next_btn.state(["disabled"])
            self.set_next_reason(why)
        else:
            self.next_btn.state(["!disabled"])
            self.set_next_reason("")

    def _install_changed(self) -> None:
        self.guarded(self.c.set_install_consent, self.install_var.get())
        self.install_var.set(self.c.state.install_consent)  # 거부되면 체크도 되돌린다

    def _render(self) -> None:
        p = self.c.state.preview
        clear(self.tree)
        self.full = {}
        if p is None:
            self.summary.configure(text=T.PREV_NONE)
            self.problem_box.configure(text="", bg=self._empty_bg)  # 이전 미리보기의 '검사 통과' 표시가 남지 않게
            self.next_btn.state(["disabled"])
            self.set_next_reason(T.WHY_PREVIEW_FIRST)
            self.prog_box.pack_forget()
            return
        counts = p.summary()
        text = T.PREV_SUMMARY.format(new=counts.get("would_copy", 0), over=counts.get("would_overwrite", 0),
                                     same=counts.get("skipped_identical", 0))
        notes = [x for x in (self.c.denied_note(), self.c.git_config_note(), *self.c.install_note_lines()) if x]
        if self.c.install_view():
            self.install_var.set(self.c.state.install_consent)
            self.install_check.pack(anchor="w", pady=(2, 0), before=self.problem_box)
        else:
            self.install_check.pack_forget()
        self.summary.configure(text=text + "".join(f"\n{x}" for x in notes))  # 별도 상자 없이 요약 아래 한 줄씩
        problems = self.c.preview_problems()
        if problems:
            self.problem_box.configure(text=T.PREV_PROBLEMS.format(items="\n".join("• " + x for x in problems[:8])),
                                       bg=DANGER_BG, fg=DANGER_FG)
            self.next_btn.state(["disabled"])
            self.set_next_reason(T.G_PACKAGE_BLOCKED)
        else:
            self.problem_box.configure(text=T.PREV_OK, bg=OK_BG, fg=OK_FG)
            self.next_btn.state(["!disabled"])
            self.set_next_reason("")
        for r in self.c.preview_rows():
            tags = ("extra",) if r["extra"] else (("over",) if r["backup"] else ())
            kind = T.PREV_KIND_INSTALL if r.get("install") else (T.PREV_KIND_EXTRA if r["extra"] else T.PREV_KIND_RULE)
            iid = self.tree.insert("", "end", tags=tags, values=(
                r["status"], T.ellipsize_path(r["target"]), T.ellipsize_path(r["backup"]), kind))
            self.full[iid] = (r["target"], r["backup"])
        lines = (self.c.preview_lines()
                 + [f"{w.get('name', '')}: {i}" for w in p.plan.warnings for i in w.get("items", [])])
        set_text(self.warn, lines or [T.CL_EMPTY])
        self._render_programs()
        self._update_gate()
        table = self.c.coverage_table(p)
        self.cov.show(table)
        head = self.c.coverage_headline(table)
        self.cov_fold.title = f"{T.COV_PREVIEW_TITLE}: {head} ▸" if head else f"{T.COV_PREVIEW_TITLE} ▸"
        self.cov_fold.title_open = self.cov_fold.title.replace("▸", "▾")
        self.cov_fold.set_open(self.cov_fold.open)


class ConfirmView(BaseView):
    name = S.NEW_CONFIRM

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.NEW_PREVIEW))
        self.run_btn = self.add_action(T.BTN_APPLY, self._run, kind="warn")  # 주의색 단추: 파일을 덮어쓰는 실행
        self.primary = self.run_btn
        self.run_btn.state(["disabled"])
        self.why = ttk.Label(self.bar_right, text="", foreground=WARN_FG, justify="right", wraplength=300)
        self.why.pack(side="right", padx=10)
        self.header(T.CONF_HEADING, T.CONF_SUB)
        self.add_status_card({T.TASK_APPLY})
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        self.info = ttk.Label(body, text="", justify="left")
        self.info.pack(anchor="w", fill="x", pady=(2, 4))
        autowrap(self.info)
        big = ttk.Frame(body)
        big.pack(anchor="w", fill="x", pady=(2, 8))
        self.big_label = tk.Label(big, text="", font=TITLE_FONT, fg=WARN_FG, anchor="w")  # 덮어쓸 파일 N개(큰 숫자)
        self.big_label.pack(side="left")
        self.big_note = tk.Label(big, text="", fg=MUTED_FG, font=BODY_FONT, anchor="w")
        self.big_note.pack(side="left", padx=(10, 0))
        self.checked_var = tk.BooleanVar(value=False)
        self.check = ttk.Checkbutton(body, variable=self.checked_var, command=self._checked_changed, text=T.CONF_CHECK)
        self.check.pack(anchor="w", pady=4)
        self.bak_var = tk.BooleanVar(value=False)
        self.check_bak = ttk.Checkbutton(body, variable=self.bak_var, command=self._bak_changed, text=T.CONF_CHECK_BAK)
        self.check_bak.pack(anchor="w", pady=4)
        self.extra_frame = ttk.LabelFrame(body, text=T.CONF_EXTRA_TITLE, padding=8)
        self.extra_var = tk.BooleanVar(value=False)
        self.extra_label = ttk.Label(self.extra_frame, text="", justify="left", foreground=DANGER_FG)
        self.extra_label.pack(anchor="w", fill="x")
        autowrap(self.extra_label, 24)
        self.extra_check = ttk.Checkbutton(self.extra_frame, variable=self.extra_var, command=self._extra_changed,
                                           text=T.CONF_EXTRA_CHECK)
        self.extra_check.pack(anchor="w")
        self._extra_anchor = ttk.Frame(body)
        self._extra_anchor.pack(fill="x")
        self.lic_link = self.license_link(body)
        self.lic_link.pack(anchor="w", pady=(8, 0))
        self.finish()

    def on_show(self) -> None:
        p = self.c.state.preview
        n_extra = p.n_extra if p else 0
        self.checked_var.set(self.c.state.preview_checked)
        self.bak_var.set(self.c.state.bak_understood)
        self.extra_var.set(self.c.state.extra_consent)
        counts = p.summary() if p else {}
        new, over = counts.get("would_copy", 0), counts.get("would_overwrite", 0)
        info = T.CONF_INFO.format(package=self.c.state.package, new=new)
        picked, total = self.c.restore_selected_count()
        if total and picked < total:  # 프로그램별로 일부만 복원하는 경우: 숫자는 선택을 반영한 미리보기 값이다
            info += "\n" + T.CONF_SELECTED_NOTE.format(n=picked, m=total, k=total - picked)
        n_inst = self.c.install_count()
        if n_inst:
            info += "\n" + T.CONF_INSTALL_INFO.format(n=n_inst)
        self.info.configure(text=info)
        if over:
            self.big_label.configure(text=T.CONF_BIG.format(over=over), fg=WARN_FG)
            self.big_note.configure(text=T.CONF_BIG_NOTE)
            self.run_btn.configure(text=T.BTN_APPLY_N.format(n=over))
        else:
            self.big_label.configure(text=T.CONF_BIG_NONE.format(new=new), fg=OK_FG)
            self.big_note.configure(text="")
            self.run_btn.configure(text=T.BTN_APPLY_NEW.format(n=new) if new else T.BTN_APPLY)
        if n_extra:
            self.extra_label.configure(text=T.CONF_EXTRA_INFO.format(n=n_extra))
            self.extra_frame.pack(fill="x", pady=4, before=self._extra_anchor)
        else:
            self.extra_frame.pack_forget()
        self._update()
        self.scroll.scroll_to_top()
        self.check.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind in ("confirm_changed", "busy", "restore_selection_changed"):
            self._update()

    def _checked_changed(self) -> None:
        self.c.set_preview_checked(self.checked_var.get())

    def _bak_changed(self) -> None:
        self.c.set_bak_understood(self.bak_var.get())

    def _extra_changed(self) -> None:
        self.c.set_extra_consent(self.extra_var.get())

    def _update(self) -> None:
        s = self.c.state
        why = self.c.apply_reason()
        ready = not why
        self.run_btn.state(["!disabled"] if ready and not s.busy else ["disabled"])
        self.why.configure(text="" if ready else why)

    def _run(self) -> None:
        n = self.c.restore_item_count()
        if not messagebox.askyesno(T.CONF_DIALOG_TITLE, T.CONF_DIALOG.format(n=n), icon="warning", parent=self):
            self.c.confirm_final(False)
            return
        self.c.confirm_final(True)
        self.guarded(self.c.start_apply)


class ResultView(BaseView):
    name = S.NEW_RESULT

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(next_=lambda: self.c.go(S.START), next_text=T.BTN_DONE)  # 마지막 화면의 주 버튼은 '완료'
        self.header(T.RES_HEADING)
        self.add_status_card({T.TASK_APPLY})
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        self.summary = ResultSummary(body)
        self.summary.pack(fill="x")
        self.note = ttk.Label(body, text="", justify="left")
        self.note.pack(anchor="w", fill="x")
        # 프로그램별 결과 표와 프로그램별 [되돌리기]
        self.prog_box = ttk.Frame(body)
        self.prog_title = ttk.Label(self.prog_box, text=T.RES_PROG_TITLE, font=SUB_FONT)
        self.prog_title.pack(anchor="w")
        self.prog_grid = ttk.Frame(self.prog_box)
        self.prog_grid.pack(fill="x", pady=(2, 0))
        self.prog_grid.columnconfigure(0, weight=1)
        self.undo_msg = ttk.Label(self.prog_box, text=T.UNDO_NOTE, foreground=MUTED_FG, justify="left")
        self.undo_msg.pack(anchor="w", fill="x", pady=(2, 0))
        autowrap(self.undo_msg, 30)
        self.undo_buttons: dict[str, ttk.Button] = {}
        self.undo_labels: dict[str, ttk.Label] = {}
        self.prog_box.pack(fill="x", pady=(3, 0))
        self.cov = CoverageBox(body, title=T.COV_PREVIEW_TITLE)  # 묶음 안에 어떤 설정이 담겼는지(프로그램별)
        self.cov.pack(fill="x", pady=(4, 0))
        cols = [("kind", T.RES_COLS[0], 90), ("path", T.RES_COLS[1], 520), ("detail", T.RES_COLS[2], 260)]
        frame, self.tree = make_tree(body, cols, height=6)
        frame.pack(fill="x", pady=6)
        self.tree.tag_configure("fail", background=DANGER_BG, foreground=DANGER_FG)
        self.full: dict[str, str] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, "") if col == 1 else "")
        # 마지막 결과 화면: 라이선스 안내를 크게(이 화면과 시작 화면에서만)
        self.license_box = Notice(body, T.LICENSE_BANNER, "warn")
        self.license_box.pack(fill="x", pady=(2, 6))
        # 다음에 할 일 체크 목록(전체 이전 순서의 체크 표시를 함께 쓴다)
        self.next_box = tk.Frame(body, bg=INFO_BG, highlightbackground=INFO_FG, highlightthickness=1, padx=12, pady=8)
        self.next_box.pack(fill="x", pady=4)
        tk.Label(self.next_box, text=T.NEXT_TITLE, bg=INFO_BG, fg=INFO_FG, font=SUB_FONT, anchor="w").pack(anchor="w")
        self.next_vars: dict[str, tk.BooleanVar] = {}
        self.box_checks: list[tk.Checkbutton] = []
        for key, text in T.RES_NEXT_ITEMS:
            var = tk.BooleanVar(value=key in self.c.roadmap_marks)
            self.next_vars[key] = var
            cb = tk.Checkbutton(self.next_box, text=text, variable=var, bg=INFO_BG, activebackground=INFO_BG, anchor="w",
                                justify="left", command=lambda k=key: self.c.toggle_roadmap(k), wraplength=700, font=BODY_FONT,
                                highlightthickness=2, highlightbackground=INFO_BG, highlightcolor=INFO_FG)
            cb.pack(anchor="w", fill="x", pady=2)
            self.box_checks.append(cb)
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(6, 0))
        self.open_btn = ttk.Button(row, text=T.BTN_OPEN_RESTORED, command=self._open)
        self.open_btn.pack(side="left")
        self.bak_btn = ttk.Button(row, text=T.BTN_OPEN_BAK, command=self._open_bak)
        self.bak_btn.pack(side="left", padx=8)
        self.undo = Collapsible(body, T.BTN_UNDO_HELP, T.BTN_UNDO_HELP.replace("▸", "▾"))
        self.undo.pack(fill="x", pady=(6, 0))
        self.undo.add_text(f"{T.RES_UNDO_TITLE}\n{T.RES_UNDO_HELP}", fg=MUTED_FG, pad=40)
        self.first_target = ""
        self.finish()

    def on_event(self, kind, payload) -> None:
        if kind == "import_done":
            self.c.go(S.NEW_RESULT)
        elif kind == "undo_preview_done":
            self.after_idle(lambda: self._confirm_undo(payload["id"], payload["result"]))
        elif kind == "undo_done":
            self._render_programs()
            name = self._name_of(payload["id"])
            n, failed = self.c.undo_counts(payload["result"])
            self.undo_msg.configure(text=T.UNDO_DONE.format(icon=T.ICON_FAIL if failed else T.ICON_OK, name=name, n=n, f=failed),
                                    foreground=DANGER_FG if failed else OK_FG)
        elif kind == "busy":
            self._sync_undo_buttons()

    # ---- 프로그램별 결과 표 / 되돌리기
    def _name_of(self, pid: str) -> str:
        return next((x["name"] for x in self.c.result_programs() if x["id"] == pid), pid)

    def _render_programs(self) -> None:
        for w in self.prog_grid.winfo_children():
            w.destroy()
        self.undo_buttons, self.undo_labels = {}, {}
        rows = self.c.result_programs()
        if not rows:
            self.prog_box.pack_forget()
            return
        if not self.prog_box.winfo_manager():
            self.prog_box.pack(fill="x", pady=(3, 0), before=self.cov)
        for col, head in enumerate(T.RES_PROG_COLS):
            ttk.Label(self.prog_grid, text=head, font=BODY_FONT, foreground=MUTED_FG).grid(
                row=0, column=col, sticky="w", padx=(0, 14))
        na = T.PREV_PROG_NA
        for i, r in enumerate(rows, 1):
            on = r["selected"]
            ttk.Label(self.prog_grid, text=r["name"]).grid(row=i, column=0, sticky="w", padx=(0, 14))
            for col, key in enumerate(("new", "overwrite", "same", "failed"), 1):
                ttk.Label(self.prog_grid, text=str(r[key]) if on else na,
                          foreground=DANGER_FG if key == "failed" and r[key] else TEXT_FG).grid(
                    row=i, column=col, sticky="w", padx=(0, 14))
            cell = ttk.Frame(self.prog_grid)
            cell.grid(row=i, column=5, sticky="w")
            btn = ttk.Button(cell, text=T.BTN_UNDO, style="Mini.TButton", command=lambda pid=r["id"]: self._undo_click(pid))
            btn.pack(side="left")
            lbl = ttk.Label(cell, text="", foreground=MUTED_FG)
            lbl.pack(side="left", padx=(6, 0))
            self.undo_buttons[r["id"]], self.undo_labels[r["id"]] = btn, lbl
        self._sync_undo_buttons()

    def _sync_undo_buttons(self) -> None:
        for r in self.c.result_programs():
            btn = self.undo_buttons.get(r["id"])
            if btn is None:
                continue
            why = r["undo"]
            btn.state(["disabled"] if why else ["!disabled"])
            self.undo_labels[r["id"]].configure(text=why)

    def _undo_click(self, pid: str) -> None:
        self.guarded(self.c.start_undo_preview, pid)

    def _confirm_undo(self, pid: str, res) -> None:
        """되돌리기 미리보기를 본 뒤 확인창: 예 를 눌러야만 실제로 되돌린다(새로 생긴 파일은 지우지 않는다)."""
        name = self._name_of(pid)
        n, kept, problems = self.c.undo_preview_counts(res)
        if not n:
            messagebox.showinfo(T.UNDO_CONFIRM_TITLE, T.UNDO_NOTHING_DIALOG.format(name=name), parent=self)
            return
        extra = T.UNDO_PROBLEMS.format(p=problems) if problems else ""
        if messagebox.askyesno(T.UNDO_CONFIRM_TITLE, T.UNDO_CONFIRM.format(name=name, n=n, k=kept, problems=extra),
                               icon="warning", parent=self):
            self.guarded(self.c.start_undo, pid, confirmed=True)

    def on_show(self) -> None:
        r = self.c.state.import_result
        clear(self.tree)
        self.full = {}
        self.undo_msg.configure(text=T.UNDO_NOTE, foreground=MUTED_FG)
        for key, var in self.next_vars.items():
            var.set(key in self.c.roadmap_marks)
        self.scroll.scroll_to_top()
        if r is None or r.report is None:
            self._render_programs()
            self.cov.clear()
            self.summary.clear()
            self.note.configure(text=T.RES_NOT_YET)
            self.open_btn.state(["disabled"])
            self.bak_btn.state(["disabled"])
            return
        self.note.configure(text="\n".join(x for x in (self.c.denied_note(), self.c.git_config_note(applied=True)) if x))
        ok, skipped, failed = self.c.import_counts()
        self.summary.show(ok, skipped, failed, T.RES_ALL_OK, T.RES_IMPORT_FAIL)
        self._render_programs()
        self.cov.show(self.c.coverage_table(r))

        def add(kind, path, detail, tags=()):
            iid = self.tree.insert("", "end", tags=tags, values=(kind, T.ellipsize_path(path), detail))
            self.full[iid] = path

        for res in r.report.results:
            if res.status in ("failed", "rejected"):
                kind = T.RES_KIND_REJECTED if res.status == "rejected" else T.RES_KIND_FAIL
                add(f"{T.ICON_FAIL} {kind}", res.dst, T.friendly_detail(res.error), ("fail",))
            elif res.backup:
                add(T.RES_KIND_BAK, res.backup, T.RES_BAK_DETAIL)
        self.first_target = ""
        for res in r.report.results[:200]:
            if res.status == "copied" and not res.backup:
                add(T.RES_KIND_COPY, res.dst, "")
                self.first_target = self.first_target or res.dst
        self.open_btn.state(["!disabled"] if self.first_target else ["disabled"])
        self.bak_btn.state(["!disabled"] if self.c.backup_folder() else ["disabled"])

    def _open(self) -> None:
        if self.first_target:
            open_location(self.first_target)

    def _open_bak(self) -> None:
        folder = self.c.backup_folder()
        if folder:
            open_location(folder)
