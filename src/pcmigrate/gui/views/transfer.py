"""대용량 복사 화면: 외장 드라이브 / 랜선(네트워크) / 같은 PC 중 방식을 고르고, 원본은 읽기만 하며 복사 후 정확한지 검사한다."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from ...progress import fmt_bytes
from .common import (INFO_BG, MUTED_FG, OK_BG, OK_FG, SECRET_BG, SECRET_FG, WARN_FG, BaseView, ResultSummary, TextDialog,
                     TreeTooltip, add_tooltip, autowrap, clear, make_tree)

YELLOW_BG = "#fff4cc"
YELLOW_FG = "#7a5200"


class ScrollBody(ttk.Frame):
    """세로로 스크롤되는 본문 영역(화면이 길어도 아래 버튼이 잘리지 않게)."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", self._enter, add="+")
            w.bind("<Leave>", self._leave, add="+")

    def _wheel(self, e) -> None:
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def _enter(self, _e) -> None:
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _leave(self, _e) -> None:
        self.canvas.unbind_all("<MouseWheel>")


class TransferView(BaseView):
    name = S.TRANSFER

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.START))
        self.header(T.TR_HEADING, T.TR_SUB)
        self.add_status_card({T.TASK_TRANSFER, T.TASK_TRANSFER_PREVIEW, T.TASK_VERIFY, T.TASK_VERIFY_AGAIN, T.TASK_RETRY})
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        self.scroll = scroll

        # ---- 방식 / 단계 선택
        modes = ttk.LabelFrame(body, text=T.TR_MODE_HEADING, padding=8)
        modes.pack(fill="x", pady=(0, 4))
        self.mode_var, self.stage_var = tk.StringVar(value=self.c.state.transfer_mode), tk.IntVar(value=self.c.state.transfer_stage)
        row = ttk.Frame(modes)
        row.pack(fill="x")
        self.mode_btns: dict[str, ttk.Radiobutton] = {}
        for key, title, _desc in T.TR_MODES:
            rb = ttk.Radiobutton(row, text=title, value=key, variable=self.mode_var, command=self._on_mode)
            rb.pack(side="left", padx=(0, 18))
            self.mode_btns[key] = rb
        self.mode_desc = ttk.Label(modes, text="", justify="left", foreground=MUTED_FG)
        self.mode_desc.pack(anchor="w", fill="x", pady=(4, 0))
        autowrap(self.mode_desc, 40)
        self.stage_frame = ttk.Frame(modes)
        self.stage_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(self.stage_frame, text=T.TR_STAGE_HEADING).pack(side="left", padx=(0, 10))
        self.stage_btns: list[ttk.Radiobutton] = []
        for n in (1, 2):
            rb = ttk.Radiobutton(self.stage_frame, text="", value=n, variable=self.stage_var, command=self._on_stage)
            rb.pack(side="left", padx=(0, 18))
            self.stage_btns.append(rb)
        helps = ttk.Frame(modes)
        helps.pack(fill="x", pady=(6, 0))
        ttk.Button(helps, text=T.TR_HELP_BTN, command=lambda: TextDialog(self, T.TR_HELP_NETWORK_TITLE, T.HELP_NETWORK, 86, 26)).pack(side="left")
        ttk.Button(helps, text=T.TR_COMPARE_BTN, command=lambda: TextDialog(self, T.TR_COMPARE_TITLE, T.HELP_COMPARE, 96, 18)).pack(side="left", padx=8)

        # ---- 이 단계의 검사 방법 / 할 일 / 복사 전 확인
        self.method_label = tk.Label(body, text="", anchor="w", justify="left", padx=8, pady=6, bg=INFO_BG)
        self.method_label.pack(fill="x", pady=2)
        autowrap(self.method_label, 40)
        self.steps_label = ttk.Label(body, text="", justify="left")
        self.steps_label.pack(anchor="w", fill="x", pady=2)
        autowrap(self.steps_label, 20)
        self.precheck_label = tk.Label(body, text="", anchor="w", justify="left", padx=8, pady=6, bg=YELLOW_BG, fg=YELLOW_FG)
        self.precheck_label.pack(fill="x", pady=2)
        autowrap(self.precheck_label, 40)

        # ---- 복사 폼(원본/대상/결과 파일)
        self.copy_form = ttk.Frame(body)
        self.copy_form.pack(fill="x")
        form = self.copy_form
        form.columnconfigure(1, weight=1)
        self.src_var, self.dst_var, self.rep_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.src_label = ttk.Label(form, text=T.TR_SRC)
        self.dst_label = ttk.Label(form, text=T.TR_DST)
        self.src_entry = self._row(form, 0, self.src_label, self.src_var, lambda: self._pick_dir(self.src_var))
        self.dst_hint = self._row(form, 1, self.dst_label, self.dst_var, lambda: self._pick_dir(self.dst_var), hint=T.TR_DST_HINT)
        self._row(form, 2, ttk.Label(form, text=T.TR_REPORT), self.rep_var, self._pick_report, hint=T.TR_REPORT_HINT)
        self.src_var.trace_add("write", lambda *a: self._check())
        self.dst_var.trace_add("write", lambda *a: self._check())

        # ---- 검사 폼(랜선 2단계: 목록 파일과 비교)
        self.verify_form = ttk.Frame(body)
        vf = self.verify_form
        vf.columnconfigure(1, weight=1)
        self.manifest_var, self.target_var, self.vrep_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._row(vf, 0, ttk.Label(vf, text=T.TR_MANIFEST_LABEL), self.manifest_var, self._pick_manifest, hint=T.TR_MANIFEST_HINT)
        self._row(vf, 1, ttk.Label(vf, text=T.TR_TARGET_LABEL), self.target_var, lambda: self._pick_dir(self.target_var), hint=T.TR_TARGET_HINT)
        self._row(vf, 2, ttk.Label(vf, text=T.TR_REPORT), self.vrep_var, self._pick_vreport, hint=T.TR_REPORT_HINT)
        self.manifest_var.trace_add("write", lambda *a: self._check())
        self.target_var.trace_add("write", lambda *a: self._check())

        self.check_label = tk.Label(body, text="", anchor="w", justify="left")
        self.check_label.pack(fill="x", pady=4)
        autowrap(self.check_label, 40)
        self.warn_label = tk.Label(body, text="", anchor="w", justify="left", padx=8, pady=4, bg=YELLOW_BG, fg=YELLOW_FG)
        autowrap(self.warn_label, 40)

        # ---- 옵션(펼치기)
        self.opts_open = False
        self.opts_toggle = ttk.Button(body, text="옵션 ▸", command=self._toggle_opts)
        self.opts_toggle.pack(anchor="w")
        self.opts = ttk.LabelFrame(body, text=T.TR_OPTIONS, padding=8)
        self.strict_var, self.cloud_var = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        for var in (self.strict_var, self.cloud_var):  # 옵션을 바꾸면 이전 미리보기는 무효가 된다
            var.trace_add("write", lambda *a: self._options_changed())
        ttk.Checkbutton(self.opts, variable=self.strict_var, text=T.TR_STRICT).pack(anchor="w")
        n1 = ttk.Label(self.opts, text=T.TR_STRICT_NOTE, justify="left")
        n1.pack(anchor="w", fill="x")
        autowrap(n1, 40)
        ttk.Checkbutton(self.opts, variable=self.cloud_var, text=T.TR_CLOUD).pack(anchor="w", pady=(6, 0))
        n2 = ttk.Label(self.opts, text=T.TR_CLOUD_NOTE, justify="left", foreground=WARN_FG)
        n2.pack(anchor="w", fill="x")
        autowrap(n2, 40)

        # ---- 버튼(복사 흐름)
        self.btns = ttk.Frame(body)
        self.btns.pack(fill="x", pady=6)
        btns = self.btns
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
        self.retry_btn = ttk.Button(btns, text=T.BTN_TR_RETRY, command=self._retry)
        self.retry_btn.pack(side="right", padx=8)
        self.again_btn = ttk.Button(btns, text=T.BTN_TR_VERIFY_AGAIN, command=self._verify_again)
        self.again_btn.pack(side="right", padx=8)
        add_tooltip(self.again_btn, T.TR_VERIFY_AGAIN_NOTE)
        for b in (self.go_btn, self.save_btn, self.cloud_btn, self.retry_btn, self.again_btn):
            b.state(["disabled"])

        # ---- 버튼(검사 흐름)
        self.vbtns = ttk.Frame(body)
        self.verify_btn = ttk.Button(self.vbtns, text=T.BTN_TR_VERIFY, command=self._verify)
        self.verify_btn.pack(side="left")
        self.verify_reason = ttk.Label(self.vbtns, text="", foreground=WARN_FG)
        self.verify_reason.pack(side="left", padx=8)
        self.vsave_btn = ttk.Button(self.vbtns, text=T.BTN_TR_SAVE, command=self._save_report)
        self.vsave_btn.pack(side="right")
        self.vsave_btn.state(["disabled"])

        # ---- 결과
        self.result = ResultSummary(body)
        self.result.pack(fill="x")
        self.preview_box = tk.Label(body, text="", anchor="w", justify="left", padx=8, pady=6)
        self.preview_box.pack(fill="x")
        autowrap(self.preview_box, 40)
        self.loc_box = tk.Label(body, text="", anchor="w", justify="left", padx=8, pady=6, bg=YELLOW_BG, fg=YELLOW_FG)
        autowrap(self.loc_box, 40)
        self.notes_box = ttk.Label(body, text="", justify="left")
        autowrap(self.notes_box, 20)
        self.layout_box = ttk.Label(body, text="", justify="left", foreground=MUTED_FG)
        autowrap(self.layout_box, 20)
        cols = [("kind", T.TR_COLS[0], 100), ("path", T.TR_COLS[1], 520), ("detail", T.TR_COLS[2], 260)]
        frame, self.tree = make_tree(body, cols, height=5)
        frame.pack(fill="both", expand=True, pady=4)
        self.tree.tag_configure("fail", background=SECRET_BG, foreground=SECRET_FG)
        self.full: dict[str, str] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, "") if col == 1 else "")
        self.finish()
        self._apply_flow()

    # ------------------------------------------------------------------ 구성 도우미
    def _row(self, form, r, label, var, browse, hint="") -> ttk.Entry:
        label.grid(row=r, column=0, sticky="w", pady=2)
        entry = ttk.Entry(form, textvariable=var)
        entry.grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(form, text=T.BTN_BROWSE, command=browse).grid(row=r, column=2)
        hint_label = ttk.Label(form, text=hint, foreground=MUTED_FG)
        hint_label.grid(row=r, column=3, sticky="w", padx=6)
        return hint_label if r == 1 and var is self.dst_var else entry

    def _toggle_opts(self) -> None:
        self.opts_open = not self.opts_open
        if self.opts_open:
            self.opts.pack(fill="x", pady=4, after=self.opts_toggle)
        else:
            self.opts.pack_forget()
        self.opts_toggle.configure(text="옵션 ▾" if self.opts_open else "옵션 ▸")

    def _pick_dir(self, var) -> None:
        p = filedialog.askdirectory(parent=self, title=T.TR_PICK_FOLDER, mustexist=False)
        if p:
            var.set(p.replace("/", "\\"))

    def _pick_manifest(self) -> None:
        p = filedialog.askopenfilename(parent=self, title=T.TR_PICK_MANIFEST, filetypes=[("목록 파일", "*.jsonl"), ("모든 파일", "*.*")])
        if p:
            self.manifest_var.set(p.replace("/", "\\"))

    def _pick_report(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.TR_REPORT_TITLE, defaultextension=".json",
                                         filetypes=[("JSON", "*.json")], initialfile="transfer-report.json")
        if p:
            self.rep_var.set(p)

    def _pick_vreport(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.TR_REPORT_TITLE, defaultextension=".json",
                                         filetypes=[("JSON", "*.json")], initialfile="verify-report.json")
        if p:
            self.vrep_var.set(p)

    # ------------------------------------------------------------------ 방식/단계 바꾸기
    def _on_mode(self) -> None:
        self.guarded(self.c.set_transfer_flow, self.mode_var.get(), 1 if self.mode_var.get() == T.MODE_LOCAL else self.stage_var.get())
        self._sync_radios()

    def _on_stage(self) -> None:
        self.guarded(self.c.set_transfer_flow, self.mode_var.get(), self.stage_var.get())
        self._sync_radios()

    def _sync_radios(self) -> None:
        mode, stage = self.c.flow()
        self.mode_var.set(mode)
        self.stage_var.set(stage)
        self._apply_flow()

    def _apply_flow(self) -> None:
        """방식/단계에 맞게 안내, 입력 칸 이름, 버튼을 바꾼다."""
        mode, stage = self.c.flow()
        self.mode_var.set(mode)
        self.stage_var.set(stage)
        desc = {k: d for k, _t, d in T.TR_MODES}
        self.mode_desc.configure(text=desc.get(mode, ""))
        if mode == T.MODE_LOCAL:
            self.stage_frame.pack_forget()
        else:
            self.stage_frame.pack(fill="x", pady=(6, 0), after=self.mode_desc)
            for rb, text in zip(self.stage_btns, T.TR_STAGES[mode]):
                rb.configure(text=text)
        self.method_label.configure(text=self.c.flow_method())
        steps = self.c.flow_steps()
        self.steps_label.configure(text=T.TR_STEPS_HEADING + "\n" + "\n".join(f"  {i}. {t}" for i, t in enumerate(steps, 1)))
        verify = self.c.is_verify_flow()
        if verify:
            self.precheck_label.pack_forget()
            self.copy_form.pack_forget()
            self.btns.pack_forget()
            self.verify_form.pack(fill="x", after=self.steps_label)
            self.vbtns.pack(fill="x", pady=6, before=self.result)
            self.opts_toggle.pack_forget()
            if self.opts_open:
                self._toggle_opts()
            self.primary = self.verify_btn
        else:
            self.verify_form.pack_forget()
            self.vbtns.pack_forget()
            self.precheck_label.configure(text=T.TR_PRECHECK_TITLE + "\n" + "\n".join(f"  · {t}" for t in T.TR_PRECHECK_LINES))
            self.precheck_label.pack(fill="x", pady=2, after=self.steps_label)
            self.copy_form.pack(fill="x", after=self.precheck_label)
            self.btns.pack(fill="x", pady=6, before=self.result)
            self.opts_toggle.pack(anchor="w", before=self.btns)
            src_text, dst_text = self.c.flow_labels()
            self.src_label.configure(text=src_text)
            self.dst_label.configure(text=dst_text)
            self.dst_hint.configure(text=self.c.flow_dst_hint())
            self.primary = self.dry_btn
        self._render_result()
        self._check()

    # ------------------------------------------------------------------ 상태 동기화
    def _sync_state(self) -> None:
        st = self.c.state
        st.transfer_src, st.transfer_dst = self.src_var.get().strip(), self.dst_var.get().strip()
        st.verify_manifest, st.verify_target = self.manifest_var.get().strip(), self.target_var.get().strip()

    def _show_check(self, res) -> None:
        body = "\n".join((T.ICON_OK if ok else T.ICON_FAIL) + " " + t for ok, t in res.lines)
        if res.info:
            body += "\n" + "\n".join(res.info)
        self.check_label.configure(text=body, bg=OK_BG if res.ok else SECRET_BG, fg=OK_FG if res.ok else SECRET_FG)
        if res.warnings:
            self.warn_label.configure(text="\n".join(res.warnings))
            self.warn_label.pack(fill="x", pady=2, after=self.check_label)
        else:
            self.warn_label.pack_forget()

    def _check(self):
        self._sync_state()
        st = self.c.state
        if self.c.is_verify_flow():
            res = self.c.check_verify_inputs(st.verify_manifest, st.verify_target)
            touched = st.verify_manifest or st.verify_target
        else:
            res = self.c.check_transfer_paths(st.transfer_src, st.transfer_dst)
            touched = st.transfer_src or st.transfer_dst
        if not touched:
            self.check_label.configure(text="", bg=self.winfo_toplevel().cget("background"))
            self.warn_label.pack_forget()
        else:
            self._show_check(res)
        self._update_buttons()
        return res

    def _update_buttons(self) -> None:
        dry_why, go_why = self.c.transfer_reasons()
        if self.c.is_verify_flow():
            self.verify_btn.state(["disabled"] if dry_why else ["!disabled"])
            self.verify_reason.configure(text=dry_why)
            return
        self.dry_btn.state(["disabled"] if dry_why else ["!disabled"])
        self.go_btn.state(["disabled"] if go_why else ["!disabled"])
        self.dry_reason.configure(text=dry_why)
        self.go_reason.configure(text=go_why)
        again_ok, _why = self.c.can_verify_again()
        self.again_btn.state(["!disabled"] if again_ok else ["disabled"])
        self.retry_btn.state(["!disabled"] if self.c.has_retryable() and not self.c.state.busy else ["disabled"])

    def on_show(self) -> None:
        st = self.c.state
        vals = (st.transfer_src, st.transfer_dst, st.verify_manifest, st.verify_target)  # 먼저 읽어 둔다(값을 넣을 때마다 상태가 갱신되므로)
        self.src_var.set(vals[0])
        self.dst_var.set(vals[1])
        self.manifest_var.set(vals[2])
        self.target_var.set(vals[3])
        self._apply_flow()
        self.src_entry.focus_set()

    # ------------------------------------------------------------------ 실행
    def _options_changed(self) -> None:
        self.c.set_transfer_options(self.cloud_var.get(), self.strict_var.get())
        self._update_buttons()

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
        flow = T.TR_CONFIRM_FLOW.format(flow=self.c.flow_title(), method=self.c.flow_method())
        if not messagebox.askokcancel(T.TR_CONFIRM_TITLE,
                                      T.TR_CONFIRM.format(src=s.transfer_src, dst=s.transfer_dst, cloud=cloud) + flow, parent=self):
            return
        self.guarded(self.c.start_transfer, dry_run=False, write_confirmed=True, **self._opts())

    def _retry(self) -> None:
        self._sync_state()
        if not messagebox.askokcancel(T.TR_CONFIRM_TITLE, T.TR_CONFIRM.format(src=self.c.state.transfer_src, dst=self.c.state.transfer_dst,
                                                                              cloud="") , parent=self):
            return
        self.guarded(self.c.start_transfer, dry_run=False, write_confirmed=True, retry_failed=True, **self._opts())

    def _verify_again(self) -> None:
        self._sync_state()
        self.guarded(self.c.start_transfer, dry_run=False, verify_only=True, **self._opts())

    def _verify(self) -> None:
        self._sync_state()
        s = self.c.state
        if not messagebox.askokcancel(T.TR_VERIFY_CONFIRM_TITLE, T.TR_VERIFY_CONFIRM.format(listfile=s.verify_manifest, target=s.verify_target),
                                      parent=self):
            return
        self.guarded(self.c.start_verify, report_path=self.vrep_var.get().strip())

    def _save_report(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.TR_REPORT_TITLE, defaultextension=".json",
                                         filetypes=[("JSON", "*.json")], initialfile="transfer-report.json")
        if not p:
            return
        try:
            self.c.save_transfer_report(p)
            messagebox.showinfo(T.SAVED_TITLE, T.SAVED_BODY.format(path=p), parent=self)
        except Exception as e:  # noqa: BLE001
            self.app.show_error(self.c.report_error(e, T.TASK_UI))

    def _show_cloud(self) -> None:
        files = self.c.transfer_summary().get("cloud_files", [])
        TextDialog(self, T.TR_CLOUD_WIN, "\n".join(files[:2000]) or T.CL_EMPTY)

    def on_event(self, kind, payload) -> None:
        if kind in ("busy", "selection_changed"):
            self._update_buttons()
        if kind == "transfer_flow":
            self._apply_flow()
        if kind == "transfer_done":
            self._render_result()
            self._update_buttons()

    # ------------------------------------------------------------------ 결과 표시
    def _hide_extras(self) -> None:
        for w in (self.loc_box, self.notes_box, self.layout_box):
            w.pack_forget()

    def _show_extras(self) -> None:
        loc = self.c.location_warning()
        if loc:
            self.loc_box.configure(text=loc)
            self.loc_box.pack(fill="x", pady=2, after=self.preview_box)
        else:
            self.loc_box.pack_forget()
        notes = [n for n in self.c.flow_notes() if n != loc]
        self.notes_box.configure(text="\n".join(notes))
        self.notes_box.pack(anchor="w", fill="x", pady=2, after=self.loc_box if loc else self.preview_box)
        lay = self.c.layout_lines()
        if lay:
            self.layout_box.configure(text=T.TR_LAYOUT_TITLE + "\n" + "\n".join(lay))
            self.layout_box.pack(anchor="w", fill="x", pady=2, after=self.notes_box)
        else:
            self.layout_box.pack_forget()

    def _render_result(self) -> None:
        sm = self.c.transfer_summary()
        clear(self.tree)
        self.full = {}
        self._hide_extras()
        if not sm:
            self.result.clear()
            self.preview_box.configure(text="", bg=self.winfo_toplevel().cget("background"))
            self.save_btn.state(["disabled"])
            self.vsave_btn.state(["disabled"])
            return
        if sm["dry_run"]:
            c = sm["counts"]
            self.result.clear()
            self.preview_box.configure(text=T.TR_PREVIEW_RESULT.format(
                new=c.get("would_copy", 0), over=c.get("would_overwrite", 0), same=c.get("skipped_identical", 0),
                cloud=sm["cloud_only"]), bg=INFO_BG, fg="black")
            self._preview_notes()
        elif self.c.is_verify_flow() or sm["mode"] == "verify":
            ok, skipped, failed = self.c.transfer_counts()
            text = T.TR_VERIFY_RESULT.format(ok=sm["verified"], mismatch=sm["mismatch"], missing=sm["missing"], failed=sm["failed"],
                                             added=sm["added"])
            ok_msg = T.TR_VERIFY_OK + ("\n" + T.TR_VERIFY_EXTRA_NOTE if sm["added"] else "")
            self.result.show(ok, 0, failed, ok_msg if not failed else text, T.TR_FAIL_HINT if sm["manifest_complete"] else T.TR_VERIFY_INCOMPLETE)
            self.preview_box.configure(text=text, bg=INFO_BG, fg="black")
            self._show_extras()
        else:
            ok, skipped, failed = self.c.transfer_counts()
            extra = T.TR_RESULT_REPAIRED.format(n=len(sm["repaired"])) if sm["repaired"] else ""
            if sm["pending"]:
                text = T.TR_RESULT_PENDING.format(pending=sm["pending"], failed=sm["mismatch"] + sm["missing"] + sm["failed"],
                                                  cloud=sm["cloud_only"]) + extra
            else:
                text = T.TR_RESULT.format(ok=sm["verified"], mismatch=sm["mismatch"], missing=sm["missing"], failed=sm["failed"],
                                          cloud=sm["cloud_only"]) + extra
            io = sm.get("io") or {}
            if io:
                text += "\n" + T.TR_IO_LINE.format(src_files=io.get("source_files_read", 0), src_bytes=fmt_bytes(io.get("source_read_bytes", 0)),
                                                   dst_files=io.get("dest_files_read", 0), dst_bytes=fmt_bytes(io.get("dest_read_bytes", 0)))
            self.result.show(ok, skipped, failed, text, T.TR_FAIL_HINT)
            self.preview_box.configure(text=text, bg=INFO_BG, fg="black")
            self._show_extras()
        for path, status, err in sm["failures"][:500]:
            iid = self.tree.insert("", "end", tags=("fail",), values=(f"{T.ICON_FAIL} {status}", T.ellipsize_path(path), err))
            self.full[iid] = path
        self.save_btn.state(["!disabled"])
        self.vsave_btn.state(["!disabled"])
        self.cloud_btn.state(["!disabled"] if sm["cloud_only"] else ["disabled"])

    def _preview_notes(self) -> None:
        """미리보기 뒤: 여유 공간/FAT32 사전 검사 결과를 보여 준다."""
        rep = self.c.state.transfer_report
        meta = getattr(rep, "meta", {}) or {}
        lines = []
        need, free = meta.get("required_bytes"), meta.get("dest_free_bytes")
        if need is not None and free is not None:
            if meta.get("space_blocked"):
                lines.append(T.TR_SPACE_BLOCK.format(need=fmt_bytes(need), total=fmt_bytes(meta.get("total_bytes", 0)), free=fmt_bytes(free)))
            else:
                lines.append(T.TR_SPACE_OK.format(need=fmt_bytes(need), free=fmt_bytes(free)))
        elif need is not None:
            lines.append(T.TR_SPACE_UNKNOWN)
        if meta.get("fat32_blocked"):
            lines.append(T.TR_FAT32_BLOCK.format(big=meta.get("fat32_big_files", 0)))
        if lines:
            self.notes_box.configure(text="\n".join(lines))
            self.notes_box.pack(anchor="w", fill="x", pady=2, after=self.preview_box)
