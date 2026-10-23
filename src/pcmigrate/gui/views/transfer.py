"""대용량 복사 화면: 원본은 읽기만 하고, 복사 후 파일이 정확한지 검사한다."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from ..controller import friendly_error
from .common import (INFO_BG, MUTED_FG, OK_BG, OK_FG, SECRET_BG, SECRET_FG, WARN_FG, BaseView, ResultSummary, TextDialog,
                     TreeTooltip, add_tooltip, autowrap, clear, make_tree)


class TransferView(BaseView):
    name = S.TRANSFER

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.START))
        self.header(T.TR_HEADING, T.TR_SUB)
        form = ttk.Frame(self)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.src_var, self.dst_var, self.rep_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.src_entry = self._row(form, 0, T.TR_SRC, self.src_var, lambda: self._pick_dir(self.src_var))
        self._row(form, 1, T.TR_DST, self.dst_var, lambda: self._pick_dir(self.dst_var), hint=T.TR_DST_HINT)
        self._row(form, 2, T.TR_REPORT, self.rep_var, self._pick_report, hint=T.TR_REPORT_HINT)
        self.src_var.trace_add("write", lambda *a: self._check())
        self.dst_var.trace_add("write", lambda *a: self._check())

        self.check_label = tk.Label(self, text="", anchor="w", justify="left")
        self.check_label.pack(fill="x", pady=4)
        autowrap(self.check_label, 40)

        opts = ttk.LabelFrame(self, text=T.TR_OPTIONS, padding=8)
        opts.pack(fill="x", pady=4)
        self.strict_var, self.cloud_var = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, variable=self.strict_var, text=T.TR_STRICT).pack(anchor="w")
        n1 = ttk.Label(opts, text=T.TR_STRICT_NOTE, justify="left")
        n1.pack(anchor="w", fill="x")
        autowrap(n1, 40)
        ttk.Checkbutton(opts, variable=self.cloud_var, text=T.TR_CLOUD).pack(anchor="w", pady=(6, 0))
        n2 = ttk.Label(opts, text=T.TR_CLOUD_NOTE, justify="left", foreground=WARN_FG)
        n2.pack(anchor="w", fill="x")
        autowrap(n2, 40)

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=6)
        self.dry_btn = ttk.Button(btns, text=T.BTN_TR_PREVIEW, command=self._dry)
        self.dry_btn.pack(side="left")
        self.primary = self.dry_btn
        self.dry_reason = ttk.Label(btns, text="", foreground=WARN_FG)
        self.dry_reason.pack(side="left", padx=(6, 14))
        self.go_btn = ttk.Button(btns, text=T.BTN_TR_GO, command=self._go)
        self.go_btn.pack(side="left")
        self.go_reason = ttk.Label(btns, text="", foreground=WARN_FG)
        self.go_reason.pack(side="left", padx=6)
        self.save_btn = ttk.Button(btns, text=T.BTN_TR_SAVE, command=self._save_report)
        self.save_btn.pack(side="right")
        self.cloud_btn = ttk.Button(btns, text=T.BTN_TR_CLOUD, command=self._show_cloud)
        self.cloud_btn.pack(side="right", padx=8)
        for b in (self.go_btn, self.save_btn, self.cloud_btn):
            b.state(["disabled"])

        self.result = ResultSummary(self)
        self.result.pack(fill="x")
        self.preview_box = tk.Label(self, text="", anchor="w", justify="left", padx=8, pady=6)
        self.preview_box.pack(fill="x")
        autowrap(self.preview_box, 40)
        cols = [("kind", T.TR_COLS[0], 100), ("path", T.TR_COLS[1], 520), ("detail", T.TR_COLS[2], 260)]
        frame, self.tree = make_tree(self, cols, height=6)
        frame.pack(fill="both", expand=True, pady=4)
        self.tree.tag_configure("fail", background=SECRET_BG, foreground=SECRET_FG)
        self.full: dict[str, str] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, "") if col == 1 else "")
        self.finish()

    def _row(self, form, r, label, var, browse, hint="") -> ttk.Entry:
        ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=2)
        entry = ttk.Entry(form, textvariable=var)
        entry.grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(form, text=T.BTN_BROWSE, command=browse).grid(row=r, column=2)
        if hint:
            ttk.Label(form, text=hint, foreground=MUTED_FG).grid(row=r, column=3, sticky="w", padx=6)
        return entry

    def _pick_dir(self, var) -> None:
        p = filedialog.askdirectory(parent=self, title=T.TR_PICK_FOLDER, mustexist=False)
        if p:
            var.set(p.replace("/", "\\"))

    def _pick_report(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.TR_REPORT_TITLE, defaultextension=".json",
                                         filetypes=[("JSON", "*.json")], initialfile="transfer-report.json")
        if p:
            self.rep_var.set(p)

    def _sync_state(self) -> None:
        self.c.state.transfer_src = self.src_var.get().strip()
        self.c.state.transfer_dst = self.dst_var.get().strip()

    def _check(self):
        self._sync_state()
        res = self.c.check_transfer_paths(self.c.state.transfer_src, self.c.state.transfer_dst)
        if not (self.c.state.transfer_src or self.c.state.transfer_dst):
            self.check_label.configure(text="", bg=self.winfo_toplevel().cget("background"))
        else:
            body = "\n".join((T.ICON_OK if ok else T.ICON_FAIL) + " " + t for ok, t in res.lines)
            self.check_label.configure(text=body, bg=OK_BG if res.ok else SECRET_BG, fg=OK_FG if res.ok else SECRET_FG)
        self._update_buttons()
        return res

    def _update_buttons(self) -> None:
        dry_why, go_why = self.c.transfer_reasons()
        self.dry_btn.state(["disabled"] if dry_why else ["!disabled"])
        self.go_btn.state(["disabled"] if go_why else ["!disabled"])
        self.dry_reason.configure(text=dry_why)
        self.go_reason.configure(text=go_why)

    def on_show(self) -> None:
        src, dst = self.c.state.transfer_src, self.c.state.transfer_dst  # 먼저 읽어 둔다(값을 넣을 때마다 상태가 갱신되므로)
        self.src_var.set(src)
        self.dst_var.set(dst)
        self._check()
        self.src_entry.focus_set()

    def _opts(self) -> dict:
        return {"strict": self.strict_var.get(), "include_cloud": self.cloud_var.get(),
                "report_path": self.rep_var.get().strip()}

    def _dry(self) -> None:
        self._sync_state()
        self.guarded(self.c.start_transfer, dry_run=True, **self._opts())

    def _go(self) -> None:
        self._sync_state()
        s = self.c.state
        cloud = T.TR_CONFIRM_CLOUD if self.cloud_var.get() else ""
        if not messagebox.askokcancel(T.TR_CONFIRM_TITLE, T.TR_CONFIRM.format(src=s.transfer_src, dst=s.transfer_dst, cloud=cloud),
                                      parent=self):
            return
        self.guarded(self.c.start_transfer, dry_run=False, write_confirmed=True, **self._opts())

    def _save_report(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.TR_REPORT_TITLE, defaultextension=".json",
                                         filetypes=[("JSON", "*.json")], initialfile="transfer-report.json")
        if not p:
            return
        try:
            self.c.save_transfer_report(p)
            messagebox.showinfo(T.SAVED_TITLE, T.SAVED_BODY.format(path=p), parent=self)
        except Exception as e:  # noqa: BLE001
            fe = friendly_error(e)
            messagebox.showerror(fe.title, fe.message, parent=self)

    def _show_cloud(self) -> None:
        files = self.c.transfer_summary().get("cloud_files", [])
        TextDialog(self, T.TR_CLOUD_WIN, "\n".join(files[:2000]) or T.CL_EMPTY)

    def on_event(self, kind, payload) -> None:
        if kind in ("busy", "selection_changed"):
            self._update_buttons()
        if kind == "transfer_done":
            self._render_result()
            self._update_buttons()

    def _render_result(self) -> None:
        sm = self.c.transfer_summary()
        clear(self.tree)
        self.full = {}
        if not sm:
            return
        if sm["dry_run"]:
            c = sm["counts"]
            self.result.clear()
            self.preview_box.configure(text=T.TR_PREVIEW_RESULT.format(
                new=c.get("would_copy", 0), over=c.get("would_overwrite", 0), same=c.get("skipped_identical", 0),
                cloud=sm["cloud_only"]), bg=INFO_BG, fg="black")
        else:
            ok, skipped, failed = self.c.transfer_counts()
            extra = T.TR_RESULT_REPAIRED.format(n=len(sm["repaired"])) if sm["repaired"] else ""
            text = T.TR_RESULT.format(ok=sm["verified"], mismatch=sm["mismatch"], missing=sm["missing"], failed=sm["failed"],
                                      cloud=sm["cloud_only"]) + extra
            self.result.show(ok, skipped, failed, text, T.TR_FAIL_HINT)
            self.preview_box.configure(text=text, bg=INFO_BG, fg="black")
        for path, status, err in sm["failures"][:500]:
            iid = self.tree.insert("", "end", tags=("fail",), values=(f"{T.ICON_FAIL} {status}", T.ellipsize_path(path), err))
            self.full[iid] = path
        self.save_btn.state(["!disabled"])
        self.cloud_btn.state(["!disabled"] if sm["cloud_only"] else ["disabled"])
