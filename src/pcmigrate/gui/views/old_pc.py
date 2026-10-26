"""구 PC 흐름: 조사 -> 규칙에 없는 프로그램 확인 -> 해야 할 일 목록 -> 설정 패키지 만들기."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from ...api import unreadable_lines
from .common import (INFO_BG, MUTED_FG, OK_FG, SECRET_BG, SECRET_FG, WARN_FG, BaseView, ResultSummary, TreeTooltip, add_tooltip,
                     autowrap, clear, make_text, make_tree, open_location, set_text, sort_tree)


class ScanView(BaseView):
    name = S.OLD_SCAN

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.START), next_=lambda: self.c.go(S.OLD_DISCOVER))
        self.header(T.SCAN_HEADING, T.SCAN_SUB)
        self.add_status_card({T.TASK_SCAN})
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.btn = ttk.Button(top, text=T.BTN_SCAN, command=lambda: self.guarded(self.c.start_scan))
        self.btn.pack(side="left")
        self.primary = self.btn
        self.summary = ttk.Label(top, text=T.SCAN_IDLE)
        self.summary.pack(side="left", padx=12)
        ttk.Label(self, text=T.SCAN_TABLE_TITLE).pack(anchor="w", pady=(10, 2))
        frame, self.tree = make_tree(self, [("rule", T.COL_PROGRAM_RULE, 260), ("evidence", T.COL_EVIDENCE, 560)], height=12)
        frame.pack(fill="both", expand=True)
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.tree.set(iid, self.tree["columns"][col]))
        self.set_next_reason(T.WHY_SCAN_FIRST)
        self.next_btn.state(["disabled"])
        self.finish()

    def on_show(self) -> None:
        done = bool(self.c.state.scan)
        self.next_btn.state(["!disabled"] if done else ["disabled"])
        self.set_next_reason("" if done else T.WHY_SCAN_FIRST)
        self.btn.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind != "scan_done":
            return
        r = payload
        clear(self.tree)
        for info in r.match_info:
            self.tree.insert("", "end", values=(info.rule.name, info.evidence))
        extra = T.SCAN_RESULT_EXTRA.format(n=r.discovery[0]) if r.discovery else ""
        self.summary.configure(text=T.SCAN_RESULT.format(programs=r.program_count, rules=len(r.matched), more=extra))
        self.next_btn.state(["!disabled"])
        self.set_next_reason("")


class DiscoverView(BaseView):
    name = S.OLD_DISCOVER

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.OLD_SCAN), next_=lambda: self.c.go(S.OLD_CHECKLIST))
        self._bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.header(T.DISC_HEADING, T.DISC_SUB)
        self.add_status_card({T.TASK_DISCOVER})
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.start_btn = ttk.Button(top, text=T.BTN_DISCOVER, command=lambda: self.guarded(self.c.start_discover))
        self.start_btn.pack(side="left")
        self.primary = self.start_btn
        self.info = ttk.Label(top, text=T.DISC_HINT)
        self.info.pack(side="left", padx=12)
        autowrap(self.info, 160)

        cols = [("sel", T.DISC_COLS[0], 56), ("warn", T.DISC_COLS[1], 96), ("prog", T.DISC_COLS[2], 190),
                ("path", T.DISC_COLS[3], 320), ("size", T.DISC_COLS[4], 80), ("grade", T.DISC_COLS[5], 90)]
        frame, self.tree = make_tree(self, cols, height=11)
        frame.pack(fill="both", expand=True, pady=6)
        self.tree.tag_configure("secret", background=SECRET_BG, foreground=SECRET_FG)
        self.tree.tag_configure("weak", foreground=WARN_FG)
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", self._key_toggle)
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.tips = TreeTooltip(self.tree, self._tip_text)

        self.detail = ttk.Label(self, text="", justify="left")
        self.detail.pack(anchor="w", fill="x")
        autowrap(self.detail)
        self.banner = tk.Label(self, text="", anchor="w", justify="left")
        self.banner.pack(fill="x", pady=4)
        autowrap(self.banner, 40)
        self.ack_var = tk.BooleanVar(value=False)
        self.ack = ttk.Checkbutton(self, variable=self.ack_var, command=self._ack_changed, text=T.DISC_ACK)
        self.ack.pack(anchor="w")
        self.count = ttk.Label(self, text=T.DISC_SELECTED.format(n=0))
        self.count.pack(anchor="w", pady=(4, 0))
        self.rows: dict[str, object] = {}
        self.finish()

    def on_show(self) -> None:
        self._refresh()
        self.start_btn.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "discover_done":
            d = payload
            self.info.configure(text=T.DISC_SUMMARY.format(n=d.program_count, m=d.candidate_count, k=d.secret_count))
            self.ack_var.set(False)
            self._refresh()
        elif kind == "selection_changed":
            self._refresh()

    def _tip_text(self, iid: str, col: int) -> str:
        r = self.rows.get(iid)
        if r is None:
            return ""
        return {1: ", ".join(r.secret), 2: r.programs, 3: r.path}.get(col, "")

    def _refresh(self) -> None:
        sort_state = dict(getattr(self.tree, "_sort_state", {}))
        clear(self.tree)
        self.rows = {}
        rows = self.c.candidate_rows()
        for r in rows:
            checked = r.key in self.c.state.selected
            mark = T.CHECKED if checked else (T.LOCKED if not r.selectable else T.UNCHECKED)
            warn = T.DISC_WARN_SECRET if r.secret else (T.DISC_WARN_WEAK if r.weak else "")
            tags = ("secret",) if r.secret else (("weak",) if r.weak else ())
            iid = self.tree.insert("", "end", values=(mark, warn, r.programs, T.ellipsize_path(r.path), r.size, r.grade),
                                   tags=tags)
            self.rows[iid] = r
        for col, desc in sort_state.items():
            sort_tree(self.tree, col, desc)
        n_secret = sum(1 for r in rows if r.secret)
        if n_secret:
            self.banner.configure(text=T.DISC_BANNER.format(icon=T.ICON_WARN, n=n_secret), bg=SECRET_BG, fg=SECRET_FG)
        else:
            self.banner.configure(text="", bg=self._bg, fg="black")
        sel = self.c.selected_rows()
        big = sum(1 for r in sel if r.size.endswith("GB"))
        self.count.configure(text=T.DISC_SELECTED.format(n=len(sel)) + (T.DISC_SELECTED_BIG.format(n=big) if big else ""))
        self.ack_var.set(self.c.state.secrets_ack)

    def _toggle(self, iid: str) -> None:
        r = self.rows.get(iid)
        if r is not None:
            self.guarded(self.c.set_selected, r.key, r.key not in self.c.state.selected)

    def _click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    def _key_toggle(self, _e=None):
        sel = self.tree.selection()
        if sel:
            self._toggle(sel[0])
        return "break"

    def _show_detail(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel or sel[0] not in self.rows:
            return
        r = self.rows[sel[0]]
        text = T.DISC_DETAIL.format(kind=r.kind, path=r.path, grade=r.grade, reason=f" — {r.reason}" if r.reason else "")
        if r.secret:
            text += T.DISC_DETAIL_SECRET.format(names=", ".join(r.secret))
        self.detail.configure(text=text)

    def _ack_changed(self) -> None:
        on = self.ack_var.get()
        if on and not messagebox.askyesno(T.DISC_ACK_DIALOG_TITLE, T.DISC_ACK_DIALOG, parent=self):
            self.ack_var.set(False)
            return
        self.c.set_secrets_ack(on)


class CheckList(ttk.Frame):
    """스크롤되는 체크박스 목록(긴 문장은 창 폭에 맞춰 줄바꿈, Tab 키로 이동, Space 로 체크)."""

    def __init__(self, parent: tk.Misc, on_toggle) -> None:
        super().__init__(parent)
        self.on_toggle = on_toggle
        self.canvas = tk.Canvas(self, highlightthickness=0, background="#ffffff")
        ys = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=ys.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner = tk.Frame(self.canvas, background="#ffffff")
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        self.widgets: list[tuple[tk.Widget, str]] = []
        self.vars: list[tk.BooleanVar] = []
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 * (e.delta // 120), "units"))

    def _resize(self, e) -> None:
        self.canvas.itemconfigure(self.win, width=e.width)
        for w, _t in self.widgets:
            w.configure(wraplength=max(240, e.width - 50))

    def set_items(self, items: list[tuple[str, bool, bool]]) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self.widgets, self.vars = [], []
        width = max(240, self.canvas.winfo_width() - 50)
        for text, has_box, checked in items:
            if has_box:
                var = tk.BooleanVar(value=checked)
                # ttk.Checkbutton 은 줄바꿈(wraplength)을 지원하지 않아 tk.Checkbutton 을 쓴다(포커스 테두리 표시)
                w = tk.Checkbutton(self.inner, text=text, variable=var, command=lambda t=text: self.on_toggle(t),
                                   anchor="w", justify="left", background="#ffffff", activebackground="#eef3fb",
                                   highlightthickness=2, highlightbackground="#ffffff", highlightcolor="#0b5394",
                                   font="TkDefaultFont")
                self.vars.append(var)
            else:
                w = tk.Label(self.inner, text=text, justify="left", anchor="w", foreground=MUTED_FG, background="#ffffff",
                             font="TkDefaultFont")
            w.configure(wraplength=width)
            w.pack(anchor="w", fill="x", pady=2, padx=4)
            self.widgets.append((w, text))
        self.canvas.yview_moveto(0)


class ChecklistView(BaseView):
    name = S.OLD_CHECKLIST

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.OLD_DISCOVER), next_=lambda: self.c.go(S.OLD_EXPORT))
        self.header(T.CL_HEADING, T.CL_SUB)
        self.add_status_card({T.TASK_CHECKLIST})
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.make_btn = ttk.Button(top, text=T.BTN_CL_MAKE, command=lambda: self.guarded(self.c.start_checklist))
        self.make_btn.pack(side="left")
        self.primary = self.make_btn
        self.save_btn = ttk.Button(top, text=T.BTN_CL_SAVE, command=self._save)
        self.save_btn.pack(side="left", padx=8)
        self.save_btn.state(["disabled"])
        self.reason = ttk.Label(top, text=T.WHY_CHECKLIST_FIRST, foreground=WARN_FG)
        self.reason.pack(side="left", padx=4)
        ttk.Label(top, text=T.CL_PRIVACY, foreground=WARN_FG).pack(side="right")

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(6, 0))
        self.all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text=T.CL_SHOW_ALL, variable=self.all_var, command=self._rebuild).pack(side="left")
        self.hidden_label = ttk.Label(opts, text="", foreground=MUTED_FG)
        self.hidden_label.pack(side="left", padx=10)
        self.progress_label = ttk.Label(opts, text="")
        self.progress_label.pack(side="right")
        ttk.Label(opts, text=T.CL_FILTER).pack(side="right", padx=(10, 2))
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(opts, textvariable=self.filter_var, width=18)
        entry.pack(side="right")
        self.filter_var.trace_add("write", lambda *a: self._rebuild())

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, pady=8)
        self.lists: dict[str, CheckList] = {}
        for tab in T.CL_TABS:
            cl = CheckList(self.nb, lambda text, t=tab: self._toggle(t, text))
            self.nb.add(cl, text=tab)
            self.lists[tab] = cl
        self.finish()

    def on_show(self) -> None:
        self.make_btn.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "checklist_done":
            self.save_btn.state(["!disabled"])
            self.reason.configure(text="")
            self._rebuild()
        elif kind == "checklist_marks":
            self._update_progress()

    def _rebuild(self) -> None:
        if self.c.state.checklist is None:
            return
        show_all, query = self.all_var.get(), self.filter_var.get()
        for tab, widget in self.lists.items():
            items = self.c.checklist_items(tab, show_all, query)
            widget.set_items(items or [(T.CL_EMPTY, False, False)])
        hidden = 0 if show_all else self.c.checklist_hidden_count()
        self.hidden_label.configure(text=T.CL_HIDDEN.format(n=hidden) if hidden else "")
        self._update_progress()

    def _toggle(self, tab: str, text: str) -> None:
        self.c.toggle_check(tab, text)

    def _update_progress(self) -> None:
        done, total = self.c.checklist_progress(self.all_var.get())
        self.progress_label.configure(text=T.CL_PROGRESS_MARK.format(done=done, total=total))

    def _save(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title=T.CL_SAVE_TITLE, defaultextension=".md",
                                            filetypes=[("Markdown", "*.md"), ("텍스트", "*.txt")])
        if not path:
            return
        if not messagebox.askokcancel(T.CL_SAVE_TITLE, T.CL_SAVE_CONFIRM.format(path=path), parent=self):
            return
        try:
            text = self.c.checklist_text("txt" if path.lower().endswith(".txt") else "md", self.all_var.get())
            self.c.save_text(path, text)
            messagebox.showinfo(T.SAVED_TITLE, T.SAVED_BODY.format(path=path), parent=self)
        except Exception as e:  # noqa: BLE001 - 쉬운 문구로 바꿔 보여 준다
            self.app.show_error(self.c.report_error(e, T.TASK_UI))


class ExportView(BaseView):
    name = S.OLD_EXPORT

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.open_btn = ttk.Button(self, text=T.BTN_OPEN_LOCATION, command=self._open)
        self.open_btn.pack(side="bottom", anchor="e")
        self.open_btn.state(["disabled"])
        self.navbar(back=lambda: self.c.go(S.OLD_CHECKLIST))
        self.header(T.EXP_HEADING, T.EXP_SUB)
        self.add_status_card({T.TASK_EXPORT})
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text=T.EXP_PATH_LABEL).pack(side="left")
        self.path_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text=T.BTN_BROWSE, command=self._browse).pack(side="left")
        self.plan = ttk.Label(self, text="", justify="left")
        self.plan.pack(anchor="w", fill="x", pady=8)
        autowrap(self.plan)
        self.resume_box = ttk.Frame(self)  # 이어받을 수 있을 때만 보인다
        self.resume_lbl = ttk.Label(self.resume_box, text="", justify="left", foreground=OK_FG)
        self.resume_lbl.pack(anchor="w", fill="x")
        autowrap(self.resume_lbl)
        self.restart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.resume_box, text=T.EXP_RESTART_CHECK, variable=self.restart_var).pack(anchor="w")
        self._resume_shown = False
        entry.bind("<FocusOut>", lambda e: self._update_resume())
        entry.bind("<Return>", lambda e: self._update_resume())
        act = ttk.Frame(self)
        act.pack(fill="x")
        self.go_btn = ttk.Button(act, text=T.BTN_EXPORT, command=self._export)
        self.go_btn.pack(side="left")
        self.primary = self.go_btn
        self.reason = ttk.Label(act, text="", foreground=WARN_FG)
        self.reason.pack(side="left", padx=8)
        lic = ttk.Label(self, text=T.LICENSE_BANNER, foreground=WARN_FG, justify="left")
        lic.pack(anchor="w", fill="x", pady=(8, 0))
        autowrap(lic)
        self.summary = ResultSummary(self)
        self.summary.pack(fill="x", pady=6)
        frame, self.result = make_text(self, height=9)
        frame.pack(fill="both", expand=True, pady=4)
        self.path_var.trace_add("write", lambda *a: self._update_reason())
        self.finish()

    def _update_resume(self) -> None:
        hint = self.c.export_resume_hint(self.path_var.get())
        if hint:
            self.resume_lbl.configure(text=hint)
            if not self._resume_shown:
                self.resume_box.pack(anchor="w", fill="x", before=self.go_btn.master)
                self._resume_shown = True
        elif self._resume_shown:
            self.resume_box.pack_forget()
            self._resume_shown = False
            self.restart_var.set(False)

    def _update_reason(self) -> None:
        why = self.c.export_reason(self.path_var.get())
        self.reason.configure(text=why)
        self.go_btn.state(["disabled"] if why == T.WHY_NEED_OUTPUT else ["!disabled"])

    def on_show(self) -> None:
        sel = self.c.selected_rows()
        secrets = sum(1 for r in sel if r.secret)
        scan = self.c.state.scan
        text = T.EXP_PLAN.format(rules=len(scan.matched) if scan else 0, n=len(sel))
        if secrets:
            text += T.EXP_PLAN_SECRET.format(k=secrets)
        if not sel:
            text += T.EXP_PLAN_NONE
        self.plan.configure(text=text)
        self._update_reason()
        self._update_resume()
        self.go_btn.focus_set()

    def _browse(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.EXP_SAVE_TITLE, defaultextension=".zip",
                                         filetypes=[("설정 패키지", "*.zip")], initialfile="pcmigrate-settings.zip")
        if p:
            self.path_var.set(p)
            self._update_resume()

    def _export(self) -> None:
        out = self.path_var.get().strip()
        if not out:
            messagebox.showwarning(T.EXP_NEED_PATH_TITLE, T.EXP_NEED_PATH, parent=self)
            return
        self._update_resume()
        exists = T.EXP_EXISTS if os.path.exists(out) else ""
        if not messagebox.askokcancel(T.EXP_CONFIRM_TITLE, T.EXP_CONFIRM.format(path=out, exists=exists), parent=self):
            return
        restart = bool(self._resume_shown and self.restart_var.get())
        if restart and not messagebox.askokcancel(T.EXP_RESTART_TITLE, T.EXP_RESTART_CONFIRM, parent=self):
            return
        self.guarded(self.c.start_export, out, write_confirmed=True, resume=not restart)

    def on_event(self, kind, payload) -> None:
        if kind != "export_done":
            return
        m = payload.manifest
        failed = len(m["failed"])
        self.summary.show(len(m["files"]), len(m["skipped"]), failed, T.EXP_DONE.format(path=self.c.state.export_output),
                          T.EXP_FAILED_HEAD)
        rs = getattr(payload, "resume", None) or {}
        lines = [T.EXP_DONE.format(path=self.c.state.export_output), ""]
        if rs.get("resumed"):
            lines += [T.EXP_RESUMED_DONE.format(n=rs["kept_files"]), ""]
        elif rs.get("reason"):
            lines += [T.EXP_RESTART_REASON.format(why=rs["reason"]), ""]
        lines += [T.EXP_RULES.format(rules=", ".join(r["id"] for r in m["rules"]) or T.EXP_NO_RULES),
                  T.EXP_COUNTS.format(files=len(m["files"]), skipped=len(m["skipped"]), failed=failed)]
        lines += [T.friendly_detail(x) for x in unreadable_lines([f for f in m["failed"] if f.get("reason")], 5)]
        lines += [f"  - {f['src']}: {T.friendly_detail(f['error'])}" for f in m["failed"] if not f.get("reason")][:20]
        lines += ["", T.EXP_TODO]
        for w in m.get("manual_warnings", []):
            for item in w.get("items", [])[:3]:
                lines.append(f"  - {w.get('name')}: {item}")
        lines += ["", T.LICENSE_NOTE]
        set_text(self.result, lines)
        self.open_btn.state(["!disabled"])

    def _open(self) -> None:
        open_location(self.c.state.export_output)
