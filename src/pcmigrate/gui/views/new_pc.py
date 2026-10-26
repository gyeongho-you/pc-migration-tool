"""신 PC 흐름: 설정 묶음 파일 선택 -> 검사와 미리보기 -> 복원 확인 -> 결과."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from .common import (BODY_FONT, DANGER_BG, DANGER_FG, INFO_BG, INFO_FG, MUTED_FG, OK_BG, OK_FG, SUB_FONT, TITLE_FONT, WARN_FG,
                     BaseView, Collapsible, Notice, PathField, ResultSummary, ScrollBody, TreeTooltip, autowrap, clean_path,
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
        note = ttk.Label(self, text=T.PKG_NOTE, justify="left", foreground=MUTED_FG)
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
        self.browse.focus_set()

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
            self.c.go(S.NEW_PREVIEW)


class PreviewView(BaseView):
    name = S.NEW_PREVIEW

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.NEW_PACKAGE), next_=lambda: self.c.go(S.NEW_CONFIRM), next_text=T.BTN_TO_CONFIRM)
        self.header(T.PREV_HEADING, T.PREV_SUB)
        self.summary = ttk.Label(self, text="", justify="left", font=SUB_FONT)
        self.summary.pack(anchor="w", fill="x")
        autowrap(self.summary)
        self.problem_box = tk.Label(self, text="", anchor="w", justify="left", padx=10, pady=6)  # 이 화면의 유일한 안내 상자
        self.problem_box.pack(fill="x", pady=4)
        self._empty_bg = self.problem_box.cget("bg")
        autowrap(self.problem_box, 40)
        cols = [("status", T.PREV_COLS[0], 130), ("target", T.PREV_COLS[1], 460), ("backup", T.PREV_COLS[2], 250),
                ("extra", T.PREV_COLS[3], 110)]
        frame, self.tree = make_tree(self, cols, height=6)
        self.lic_link = self.license_link(self)
        self.lic_link.pack(side="bottom", anchor="w")
        self.more = Collapsible(self, f"{T.PREV_WARN_TITLE} ▸", f"{T.PREV_WARN_TITLE} ▾")  # 직접 해야 할 일은 접어 둔다
        self.more.pack(side="bottom", fill="x")
        frame.pack(side="top", fill="both", expand=True, pady=4)  # 아래쪽을 먼저 쌓은 뒤 표가 남는 높이를 쓴다
        self.tree.tag_configure("over", foreground=WARN_FG)
        self.tree.tag_configure("extra", background=DANGER_BG, foreground=DANGER_FG)
        self.full: dict[str, tuple[str, str]] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, ("", ""))[0 if col == 1 else 1] if col in (1, 2) else "")
        tf, self.warn = make_text(self.more.body, height=5)
        tf.pack(fill="x")
        self.finish()

    def on_show(self) -> None:
        self._render()
        self.next_btn.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "preview_done":
            self._render()

    def _render(self) -> None:
        p = self.c.state.preview
        clear(self.tree)
        self.full = {}
        if p is None:
            self.summary.configure(text=T.PREV_NONE)
            self.problem_box.configure(text="", bg=self._empty_bg)  # 이전 미리보기의 '검사 통과' 표시가 남지 않게
            self.next_btn.state(["disabled"])
            self.set_next_reason(T.WHY_PREVIEW_FIRST)
            return
        counts = p.summary()
        text = T.PREV_SUMMARY.format(new=counts.get("would_copy", 0), over=counts.get("would_overwrite", 0),
                                     same=counts.get("skipped_identical", 0))
        denied = self.c.denied_note()
        self.summary.configure(text=text + (f"\n{denied}" if denied else ""))  # 별도 상자 없이 요약 아래 한 줄
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
            iid = self.tree.insert("", "end", tags=tags, values=(
                r["status"], T.ellipsize_path(r["target"]), T.ellipsize_path(r["backup"]),
                T.PREV_KIND_EXTRA if r["extra"] else T.PREV_KIND_RULE))
            self.full[iid] = (r["target"], r["backup"])
        lines = (self.c.preview_lines()
                 + [f"{w.get('name', '')}: {i}" for w in p.plan.warnings for i in w.get("items", [])])
        set_text(self.warn, lines or [T.CL_EMPTY])


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
        self.info.configure(text=T.CONF_INFO.format(package=self.c.state.package, new=new))
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
        if kind in ("confirm_changed", "busy"):
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
        p = self.c.state.preview
        n = len(p.plan.items) if p else 0
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

    def on_show(self) -> None:
        r = self.c.state.import_result
        clear(self.tree)
        self.full = {}
        for key, var in self.next_vars.items():
            var.set(key in self.c.roadmap_marks)
        self.scroll.scroll_to_top()
        if r is None or r.report is None:
            self.summary.clear()
            self.note.configure(text=T.RES_NOT_YET)
            self.open_btn.state(["disabled"])
            self.bak_btn.state(["disabled"])
            return
        self.note.configure(text=self.c.denied_note())
        ok, skipped, failed = self.c.import_counts()
        self.summary.show(ok, skipped, failed, T.RES_ALL_OK, T.RES_IMPORT_FAIL)

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
