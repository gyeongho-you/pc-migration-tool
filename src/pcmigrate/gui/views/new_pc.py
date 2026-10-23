"""신 PC 흐름: 패키지 선택 -> 검사와 미리보기 -> 복원 확정 -> 결과."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from .common import (INFO_BG, OK_BG, OK_FG, SECRET_BG, SECRET_FG, WARN_FG, BaseView, ResultSummary, TreeTooltip, autowrap,
                     clear, make_text, make_tree, open_location, set_text)


class PackageView(BaseView):
    name = S.NEW_PACKAGE

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.START), next_=self._next, next_text=T.BTN_PREVIEW_NEXT)
        self.header(T.PKG_HEADING, T.PKG_SUB)
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text=T.PKG_LABEL).pack(side="left")
        self.path_var = tk.StringVar(value=self.c.state.package)
        entry = ttk.Entry(row, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        self.browse = ttk.Button(row, text=T.BTN_BROWSE, command=self._browse)
        self.browse.pack(side="left")
        note = ttk.Label(self, text=T.PKG_NOTE, justify="left")
        note.pack(anchor="w", fill="x", pady=10)
        autowrap(note)
        self.primary = self.next_btn
        self.path_var.trace_add("write", lambda *a: self._update())
        self.finish()

    def _update(self) -> None:
        has = bool(self.path_var.get().strip())
        self.next_btn.state(["!disabled"] if has else ["disabled"])
        self.set_next_reason("" if has else T.WHY_PACKAGE_FIRST)

    def on_show(self) -> None:
        self._update()
        self.browse.focus_set()

    def _browse(self) -> None:
        p = filedialog.askopenfilename(parent=self, title=T.PKG_OPEN_TITLE,
                                       filetypes=[("설정 패키지", "*.zip"), ("모든 파일", "*.*")])
        if p:
            self.path_var.set(p)

    def _next(self) -> None:
        path = self.path_var.get().strip()
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
        self.summary = ttk.Label(self, text="", justify="left")
        self.summary.pack(anchor="w", fill="x")
        autowrap(self.summary)
        self.problem_box = tk.Label(self, text="", anchor="w", justify="left")
        self.problem_box.pack(fill="x", pady=4)
        autowrap(self.problem_box, 40)
        cols = [("status", T.PREV_COLS[0], 130), ("target", T.PREV_COLS[1], 460), ("backup", T.PREV_COLS[2], 250),
                ("extra", T.PREV_COLS[3], 110)]
        frame, self.tree = make_tree(self, cols, height=9)
        frame.pack(fill="both", expand=True, pady=4)
        self.tree.tag_configure("over", foreground=WARN_FG)
        self.tree.tag_configure("extra", background=SECRET_BG, foreground=SECRET_FG)
        self.full: dict[str, tuple[str, str]] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, ("", ""))[0 if col == 1 else 1] if col in (1, 2) else "")
        ttk.Label(self, text=T.PREV_WARN_TITLE).pack(anchor="w")
        tf, self.warn = make_text(self, height=5)
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
            self.next_btn.state(["disabled"])
            self.set_next_reason(T.WHY_PREVIEW_FIRST)
            return
        counts = p.summary()
        self.summary.configure(text=T.PREV_SUMMARY.format(new=counts.get("would_copy", 0), over=counts.get("would_overwrite", 0),
                                                           same=counts.get("skipped_identical", 0)))
        problems = self.c.preview_problems()
        if problems:
            self.problem_box.configure(text=T.PREV_PROBLEMS.format(items="\n".join("• " + x for x in problems[:8])),
                                       bg=SECRET_BG, fg=SECRET_FG)
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
        lines = [f"{w.get('name', '')}: {i}" for w in p.plan.warnings for i in w.get("items", [])] + [T.LICENSE_NOTE]
        set_text(self.warn, lines)


class ConfirmView(BaseView):
    name = S.NEW_CONFIRM

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.NEW_PREVIEW))
        self.header(T.CONF_HEADING, T.CONF_SUB)
        self.info = ttk.Label(self, text="", justify="left")
        self.info.pack(anchor="w", fill="x", pady=4)
        autowrap(self.info)
        self.checked_var = tk.BooleanVar(value=False)
        self.check = ttk.Checkbutton(self, variable=self.checked_var, command=self._checked_changed, text=T.CONF_CHECK)
        self.check.pack(anchor="w", pady=4)
        self.extra_frame = ttk.LabelFrame(self, text=T.CONF_EXTRA_TITLE, padding=8)
        self.extra_var = tk.BooleanVar(value=False)
        self.extra_label = ttk.Label(self.extra_frame, text="", justify="left", foreground=SECRET_FG)
        self.extra_label.pack(anchor="w", fill="x")
        autowrap(self.extra_label, 24)
        self.extra_check = ttk.Checkbutton(self.extra_frame, variable=self.extra_var, command=self._extra_changed,
                                           text=T.CONF_EXTRA_CHECK)
        self.extra_check.pack(anchor="w")
        act = ttk.Frame(self)
        act.pack(fill="x", pady=10)
        self.run_btn = ttk.Button(act, text=T.BTN_APPLY, command=self._run)
        self.run_btn.pack(side="left")
        self.primary = self.run_btn
        self.run_btn.state(["disabled"])
        self.why = ttk.Label(act, text="", foreground=WARN_FG, justify="left")
        self.why.pack(side="left", padx=10, fill="x", expand=True)
        autowrap(self.why, 160)
        lic = ttk.Label(self, text=T.LICENSE_BANNER, foreground=WARN_FG, justify="left")
        lic.pack(anchor="w", fill="x", pady=8)
        autowrap(lic)
        self.finish()

    def on_show(self) -> None:
        p = self.c.state.preview
        n_extra = p.n_extra if p else 0
        self.checked_var.set(self.c.state.preview_checked)
        self.extra_var.set(self.c.state.extra_consent)
        counts = p.summary() if p else {}
        self.info.configure(text=T.CONF_INFO.format(package=self.c.state.package, new=counts.get("would_copy", 0),
                                                    over=counts.get("would_overwrite", 0)))
        if n_extra:
            self.extra_label.configure(text=T.CONF_EXTRA_INFO.format(n=n_extra))
            self.extra_frame.pack(fill="x", pady=4, before=self.run_btn.master)
        else:
            self.extra_frame.pack_forget()
        self._update()
        self.check.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "confirm_changed":
            self._update()

    def _checked_changed(self) -> None:
        self.c.set_preview_checked(self.checked_var.get())

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
        self.navbar()
        self.header(T.RES_HEADING)
        self.summary = ResultSummary(self)
        self.summary.pack(fill="x")
        self.note = ttk.Label(self, text="", justify="left")
        self.note.pack(anchor="w", fill="x")
        cols = [("kind", T.RES_COLS[0], 90), ("path", T.RES_COLS[1], 520), ("detail", T.RES_COLS[2], 260)]
        frame, self.tree = make_tree(self, cols, height=10)
        frame.pack(fill="both", expand=True, pady=6)
        self.tree.tag_configure("fail", background=SECRET_BG, foreground=SECRET_FG)
        self.full: dict[str, str] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, "") if col == 1 else "")
        lic = ttk.Label(self, text=T.LICENSE_BANNER, foreground=WARN_FG, justify="left")
        lic.pack(anchor="w", fill="x", pady=4)
        autowrap(lic)
        self.open_btn = ttk.Button(self, text=T.BTN_OPEN_RESTORED, command=self._open)
        self.open_btn.pack(anchor="w")
        self.first_target = ""
        self.finish()

    def on_event(self, kind, payload) -> None:
        if kind == "import_done":
            self.c.go(S.NEW_RESULT)

    def on_show(self) -> None:
        r = self.c.state.import_result
        clear(self.tree)
        self.full = {}
        if r is None or r.report is None:
            self.summary.clear()
            self.note.configure(text=T.RES_NOT_YET)
            self.open_btn.state(["disabled"])
            return
        self.note.configure(text="")
        ok, skipped, failed = self.c.import_counts()
        self.summary.show(ok, skipped, failed, T.RES_ALL_OK, T.RES_IMPORT_FAIL)

        def add(kind, path, detail, tags=()):
            iid = self.tree.insert("", "end", tags=tags, values=(kind, T.ellipsize_path(path), detail))
            self.full[iid] = path

        for res in r.report.results:
            if res.status == "failed":
                add(f"{T.ICON_FAIL} {T.RES_KIND_FAIL}", res.dst, T.friendly_detail(res.error), ("fail",))
            elif res.backup:
                add(T.RES_KIND_BAK, res.backup, T.RES_BAK_DETAIL)
        for res in r.report.results[:200]:
            if res.status == "copied" and not res.backup:
                add(T.RES_KIND_COPY, res.dst, "")
                self.first_target = self.first_target or res.dst
        self.open_btn.state(["!disabled"] if self.first_target else ["disabled"])

    def _open(self) -> None:
        if self.first_target:
            open_location(self.first_target)
