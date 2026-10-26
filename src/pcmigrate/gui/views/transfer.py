"""큰 파일 옮기기(대용량 복사) 화면: 방식 선택 -> 폴더 입력 -> 미리보기 -> 복사 -> 결과.

위에서 아래로 '방식 선택 → 입력칸'이 먼저 보이고, 주 버튼(미리보기/복사 시작/검사 시작)은 스크롤 밖 하단 고정 막대에 있다.
'이 단계에서 할 일/검사 방법/복사 전 확인'은 아래의 '자세한 준비 사항 ▸' 접이식에 둔다.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from ...progress import fmt_bytes
from .common import (DANGER_BG, DANGER_FG, MUTED_FG, OK_FG, WARN_BG, WARN_FG, ActionButton, BaseView, Collapsible,
                     PathField, ResultSummary, ScrollBody, StepStrip, TextDialog, TreeTooltip, add_tooltip, autowrap, clean_path, clear,
                     make_tree)

__all__ = ["TransferView", "ScrollBody"]


class TransferView(BaseView):
    name = S.TRANSFER

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.START))
        self._build_action_bar()
        self.header(T.TR_HEADING, T.TR_SUB)
        self.add_status_card({T.TASK_TRANSFER, T.TASK_TRANSFER_PREVIEW, T.TASK_VERIFY, T.TASK_VERIFY_AGAIN, T.TASK_RETRY})
        self.steps_strip = StepStrip(self, T.TR_STEP_NAMES, label=T.TR_STEPS_LABEL)  # 방식 → 폴더 → 미리보기 → 복사 → 결과
        self.steps_strip.pack(fill="x", pady=(0, 6))
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        self.scroll = scroll

        # ---- 1) 방식 / 단계 선택
        modes = ttk.LabelFrame(body, text=T.TR_MODE_HEADING, padding=(8, 4))
        modes.pack(fill="x", pady=(0, 4))
        self.modes_frame = modes
        self.mode_var, self.stage_var = tk.StringVar(value=self.c.state.transfer_mode), tk.IntVar(value=self.c.state.transfer_stage)
        row = ttk.Frame(modes)
        row.pack(fill="x")
        self.mode_btns: dict[str, ttk.Radiobutton] = {}
        for key, title, _desc in T.TR_MODES:
            rb = ttk.Radiobutton(row, text=title, value=key, variable=self.mode_var, command=self._on_mode)
            rb.pack(side="left", padx=(0, 18))
            self.mode_btns[key] = rb
        self.mode_desc = ttk.Label(modes, text="", justify="left", foreground=MUTED_FG)
        self.mode_desc.pack(anchor="w", fill="x", pady=(2, 0))
        autowrap(self.mode_desc, 40)
        self.stage_frame = ttk.Frame(modes)
        self.stage_frame.pack(fill="x", pady=(4, 0))
        ttk.Label(self.stage_frame, text=T.TR_STAGE_HEADING).pack(side="left", padx=(0, 10))
        self.stage_btns: list[ttk.Radiobutton] = []
        for n in (1, 2):
            rb = ttk.Radiobutton(self.stage_frame, text="", value=n, variable=self.stage_var, command=self._on_stage)
            rb.pack(side="left", padx=(0, 18))
            self.stage_btns.append(rb)
        helps = ttk.Frame(modes)
        helps.pack(fill="x", pady=(2, 0))
        ActionButton(helps, text=T.TR_HELP_BTN, kind="link",
                     command=lambda: TextDialog(self, T.TR_HELP_NETWORK_TITLE, T.HELP_NETWORK, 86, 26)).pack(side="left")
        ActionButton(helps, text=T.TR_COMPARE_BTN, kind="link",
                     command=lambda: TextDialog(self, T.TR_COMPARE_TITLE, T.HELP_COMPARE, 96, 18)).pack(side="left", padx=12)

        # ---- 2) 입력칸(복사 폼)
        self.copy_form = ttk.Frame(body)
        self.copy_form.pack(fill="x")
        form = self.copy_form
        self.src_var, self.dst_var, self.rep_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.src_field = PathField(form, self.src_var, lambda: self._pick_dir(self.src_var), label=T.TR_SRC, hint=T.TR_SRC_HINT,
                                   drives=self.c.list_drives, on_drive=lambda root: self.src_var.set(root))
        self.src_field.pack(fill="x", pady=(2, 0))
        self.dst_field = PathField(form, self.dst_var, lambda: self._pick_dir(self.dst_var), label=T.TR_DST, hint=T.TR_DST_HINT,
                                   drives=self.c.list_drives, on_drive=self._pick_dst_drive)
        self.dst_field.pack(fill="x", pady=(6, 0))
        self.src_label, self.dst_label = self.src_field.label, self.dst_field.label
        self.src_entry = self.src_field.entry
        self.dst_hint = self.dst_field.hint
        self.src_var.trace_add("write", lambda *a: self._check())
        self.dst_var.trace_add("write", lambda *a: self._check())

        # ---- 입력칸(검사 폼: 랜선 2단계 — 확인용 목록 파일과 비교)
        self.verify_form = ttk.Frame(body)
        vf = self.verify_form
        self.manifest_var, self.target_var, self.vrep_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.manifest_field = PathField(vf, self.manifest_var, self._pick_manifest, label=T.TR_MANIFEST_LABEL, hint=T.TR_MANIFEST_HINT,
                                        extra=[(T.BTN_AUTOFIND, self._autofind)])
        self.manifest_field.pack(fill="x", pady=(2, 0))
        self.find_label = ttk.Label(vf, text="", justify="left")  # '자동 찾기' 결과(글이 있을 때만 보인다)
        autowrap(self.find_label, 40)
        self.target_field = PathField(vf, self.target_var, lambda: self._pick_dir(self.target_var), label=T.TR_TARGET_LABEL,
                                      hint=T.TR_TARGET_HINT, drives=self.c.list_drives, on_drive=lambda root: self.target_var.set(root))
        self.target_field.pack(fill="x", pady=(6, 0))
        self.manifest_var.trace_add("write", lambda *a: self._check())
        self.target_var.trace_add("write", lambda *a: self._check())

        self.check_label = tk.Label(body, text="", anchor="w", justify="left", padx=2)  # 입력 확인 결과(✔/✖ 줄)
        self.check_label.pack(fill="x", pady=4)
        autowrap(self.check_label, 40)
        self.warn_label = tk.Label(body, text="", anchor="w", justify="left", padx=10, pady=6, bg=WARN_BG, fg=WARN_FG)  # 이 화면의 안내 상자
        autowrap(self.warn_label, 40)

        # ---- 옵션(펼치기)
        self.opts = Collapsible(body, T.TR_OPTIONS_TOGGLE, T.TR_OPTIONS_TOGGLE_OPEN)
        self.opts.pack(anchor="w", fill="x")
        self.opts_toggle = self.opts.toggle_btn
        self.strict_var, self.cloud_var = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        for var in (self.strict_var, self.cloud_var):  # 옵션을 바꾸면 이전 미리보기는 무효가 된다
            var.trace_add("write", lambda *a: self._options_changed())
        ob = self.opts.body
        self.copy_opts = ttk.Frame(ob)
        self.copy_opts.pack(fill="x")
        ttk.Checkbutton(self.copy_opts, variable=self.strict_var, text=T.TR_STRICT).pack(anchor="w")
        n1 = ttk.Label(self.copy_opts, text=T.TR_STRICT_NOTE, justify="left", foreground=MUTED_FG)
        n1.pack(anchor="w", fill="x", padx=(24, 0))
        autowrap(n1, 40)
        ttk.Checkbutton(self.copy_opts, variable=self.cloud_var, text=T.TR_CLOUD).pack(anchor="w", pady=(6, 0))
        n2 = ttk.Label(self.copy_opts, text=T.TR_CLOUD_NOTE, justify="left", foreground=WARN_FG)
        n2.pack(anchor="w", fill="x", padx=(24, 0))
        autowrap(n2, 40)
        self.rep_field = PathField(self.copy_opts, self.rep_var, self._pick_report, label=T.TR_REPORT, hint=T.TR_REPORT_HINT)
        self.rep_field.pack(fill="x", pady=(8, 0))
        self.verify_opts = ttk.Frame(ob)
        self.vrep_field = PathField(self.verify_opts, self.vrep_var, self._pick_vreport, label=T.TR_REPORT, hint=T.TR_REPORT_HINT)
        self.vrep_field.pack(fill="x", pady=(4, 0))

        # ---- 결과 영역
        self.result = ResultSummary(body)
        self.result.pack(fill="x", pady=(6, 0))
        self.preview_box = tk.Label(body, text="", anchor="w", justify="left", padx=2, pady=4)  # 결과 한 줄 요약(상자 아님)
        self.preview_box.pack(fill="x")
        autowrap(self.preview_box, 40)
        self.loc_box = tk.Label(body, text="", anchor="w", justify="left", padx=10, pady=6, bg=WARN_BG, fg=WARN_FG)
        autowrap(self.loc_box, 40)
        self.notes_box = ttk.Label(body, text="", justify="left")
        autowrap(self.notes_box, 20)
        self.layout_more = Collapsible(body, T.TR_LAYOUT_MORE_TITLE, T.TR_LAYOUT_MORE_TITLE_OPEN)
        self.layout_box = ttk.Label(self.layout_more.body, text="", justify="left", foreground=MUTED_FG)
        self.layout_box.pack(anchor="w", fill="x")
        autowrap(self.layout_box, 40)
        self.res_actions = ttk.Frame(body)  # 결과가 나온 뒤에만 보이는 보조 단추
        self.save_btn = ttk.Button(self.res_actions, text=T.BTN_TR_SAVE, command=self._save_report)
        self.cloud_btn = ttk.Button(self.res_actions, text=T.BTN_TR_CLOUD, command=self._show_cloud)
        self.retry_btn = ttk.Button(self.res_actions, text=T.BTN_TR_RETRY, command=self._retry)
        self.again_btn = ttk.Button(self.res_actions, text=T.BTN_TR_VERIFY_AGAIN, command=self._verify_again)
        add_tooltip(self.again_btn, T.TR_VERIFY_AGAIN_NOTE)
        self.vsave_btn = self.save_btn  # 검사 흐름에서도 같은 '결과 파일 저장' 단추를 쓴다
        for b in (self.save_btn, self.cloud_btn, self.retry_btn, self.again_btn):
            b.state(["disabled"])
        cols = [("kind", T.TR_COLS[0], 100), ("path", T.TR_COLS[1], 520), ("detail", T.TR_COLS[2], 260)]
        frame, self.tree = make_tree(body, cols, height=5)
        self.tree_frame = frame
        self.tree.tag_configure("fail", background=DANGER_BG, foreground=DANGER_FG)
        self.full: dict[str, str] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.full.get(iid, "") if col == 1 else "")

        # ---- 자세한 준비 사항(접이식): 검사 방법 / 이 단계에서 할 일 / 복사 전 확인
        self.more = Collapsible(body, T.TR_MORE_TITLE, T.TR_MORE_TITLE_OPEN)
        self.more.pack(fill="x", pady=(8, 0))
        mb = self.more.body
        self.method_label = ttk.Label(mb, text="", justify="left")
        self.method_label.pack(anchor="w", fill="x", pady=2)
        autowrap(self.method_label, 40)
        self.steps_label = ttk.Label(mb, text="", justify="left")
        self.steps_label.pack(anchor="w", fill="x", pady=2)
        autowrap(self.steps_label, 20)
        self.precheck_label = ttk.Label(mb, text="", justify="left", foreground=WARN_FG)
        self.precheck_label.pack(anchor="w", fill="x", pady=2)
        autowrap(self.precheck_label, 40)
        self.detail_notes = ttk.Label(mb, text=T.TR_DETAIL_NOTES, justify="left", foreground=MUTED_FG)
        self.detail_notes.pack(anchor="w", fill="x", pady=2)
        autowrap(self.detail_notes, 40)
        self.finish()
        self._apply_flow()

    # ------------------------------------------------------------------ 하단 고정 막대(주 버튼)
    def _build_action_bar(self) -> None:
        br = self.bar_right
        assert br is not None
        self.go_btn = ActionButton(br, text=T.BTN_TR_GO, command=self._go)
        self.dry_btn = ActionButton(br, text=T.BTN_TR_PREVIEW, command=self._dry)
        self.verify_btn = ActionButton(br, text=T.BTN_TR_VERIFY, command=self._verify)
        self.done_btn = ActionButton(br, text=T.BTN_DONE, command=lambda: self.c.go(S.START))  # 결과가 나온 뒤: 마지막 단계의 주 버튼
        self.reason_col = ttk.Frame(br)  # 지금 누를 수 없는 이유(버튼 왼쪽)
        self.dry_reason = ttk.Label(self.reason_col, text="", foreground=WARN_FG, justify="right", wraplength=240)
        self.go_reason = ttk.Label(self.reason_col, text="", foreground=WARN_FG, justify="right", wraplength=240)
        self.verify_reason = ttk.Label(self.reason_col, text="", foreground=WARN_FG, justify="right", wraplength=240)
        self.primary = self.dry_btn
        for b in (self.go_btn,):
            b.state(["disabled"])

    def _final(self) -> bool:
        """복사/검사를 실제로 마친 결과가 있는가(미리보기만 한 것은 제외)."""
        rep = self.c.state.transfer_report
        return rep is not None and not self.c.state.transfer_was_dry_run

    def _layout_bar(self, verify: bool) -> None:
        for w in (self.go_btn, self.dry_btn, self.verify_btn, self.reason_col, self.done_btn):
            w.pack_forget()
        if self._final():
            self.done_btn.pack(side="right", padx=(8, 0))
        if verify:
            self.verify_btn.pack(side="right")
        else:
            self.go_btn.pack(side="right")
            self.dry_btn.pack(side="right", padx=(0, 8))
        self.reason_col.pack(side="right", padx=10)
        self._pack_reasons(verify)

    def _pack_reasons(self, verify: bool) -> None:
        for w in (self.dry_reason, self.go_reason, self.verify_reason):
            w.pack_forget()
        if verify:
            if self.verify_reason.cget("text"):
                self.verify_reason.pack(anchor="e")
            return
        dry, go = self.dry_reason.cget("text"), self.go_reason.cget("text")
        if dry:
            self.dry_reason.pack(anchor="e")
        if go and go != dry:  # 같은 말이면 한 번만
            self.go_reason.pack(anchor="e")

    # ------------------------------------------------------------------ 구성 도우미
    def _pick_dir(self, var) -> None:
        p = filedialog.askdirectory(parent=self, title=T.TR_PICK_FOLDER, mustexist=False)
        if p:
            var.set(p.replace("/", "\\"))

    def _pick_dst_drive(self, root: str) -> None:
        """대상 칸에서 드라이브를 고르면 그 드라이브의 '이전자료' 폴더를 제안한다(바꿔도 된다). 같은 PC 안 복사는 드라이브 루트만 넣는다."""
        mode, _stage = self.c.flow()
        self.dst_var.set(os.path.join(root, T.DRIVE_FOLDER_DEFAULT) if mode == T.MODE_EXTERNAL else root)

    def _pick_manifest(self) -> None:
        p = filedialog.askopenfilename(parent=self, title=T.TR_PICK_MANIFEST,
                                       filetypes=[(T.TR_FILE_KIND, "*.jsonl"), ("모든 파일", "*.*")])
        if p:
            self.manifest_var.set(p.replace("/", "\\"))

    def _autofind(self) -> str:
        """검사할 폴더(와 그 위 폴더)에서 확인용 목록 파일을 찾아 채운다. 찾은 경로를 돌려준다(없으면 빈 문자열)."""
        self._sync_state()
        s = self.c.state
        if not (s.verify_target or s.transfer_dst):
            self._say_find(T.TR_AUTOFIND_NEED_FOLDER, WARN_FG)
            return ""
        found = self.c.find_manifest(s.verify_target, s.transfer_dst, s.transfer_src)
        if found:
            self.manifest_var.set(found)
            self._say_find(T.TR_AUTOFIND_OK, OK_FG)
        else:
            self._say_find(T.TR_AUTOFIND_NONE, WARN_FG)
        return found

    def _say_find(self, text: str, color: str) -> None:
        self.find_label.configure(text=text, foreground=color)
        self.find_label.pack(anchor="w", fill="x", after=self.manifest_field)

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
            self.verify_form.pack(fill="x", after=self.modes_frame)
            self.copy_opts.pack_forget()
            self.verify_opts.pack(fill="x")
            self.primary = self.verify_btn
        else:
            self.verify_form.pack_forget()
            self.verify_opts.pack_forget()
            self.copy_opts.pack(fill="x")
            self.precheck_label.configure(text=T.TR_PRECHECK_TITLE + "\n" + "\n".join(f"  · {t}" for t in T.TR_PRECHECK_LINES))
            self.precheck_label.pack(fill="x", pady=2, after=self.steps_label)
            self.copy_form.pack(fill="x", after=self.modes_frame)
            src_text, dst_text = self.c.flow_labels()
            self.src_label.configure(text=src_text)
            self.dst_label.configure(text=dst_text)
            self.dst_field.set_hint(self.c.flow_dst_hint())
            if self.dst_field.drive_box is not None:  # 랜선(공유 폴더) 대상에는 드라이브 선택이 필요 없다
                if mode == T.MODE_NETWORK:
                    self.dst_field.drive_box.grid_remove()
                else:
                    self.dst_field.drive_box.grid()
            self.primary = self.dry_btn
        self._layout_bar(verify)
        self._render_result()
        self._check()

    # ------------------------------------------------------------------ 상태 동기화
    def _sync_state(self) -> None:
        st = self.c.state
        st.transfer_src, st.transfer_dst = clean_path(self.src_var.get()), clean_path(self.dst_var.get())
        st.verify_manifest, st.verify_target = clean_path(self.manifest_var.get()), clean_path(self.target_var.get())

    def _show_check(self, res) -> None:
        body = "\n".join((T.ICON_OK if ok else T.ICON_FAIL) + " " + t for ok, t in res.lines)
        if res.info:
            body += "\n" + "\n".join(res.info)
        self.check_label.configure(text=body, fg=OK_FG if res.ok else DANGER_FG)
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
            self.check_label.configure(text="")
            self.warn_label.pack_forget()
        else:
            self._show_check(res)
        self._update_buttons()
        return res

    def _update_buttons(self) -> None:
        dry_why, go_why = self.c.transfer_reasons()
        verify = self.c.is_verify_flow()
        if verify:
            self.verify_btn.state(["disabled"] if dry_why else ["!disabled"])
            self.verify_reason.configure(text=dry_why)
            self.verify_btn.emphasize(not self._final())
            self.done_btn.emphasize(self._final())
            self._pack_reasons(True)
            self._update_steps()
            return
        self.dry_btn.state(["disabled"] if dry_why else ["!disabled"])
        self.go_btn.state(["disabled"] if go_why else ["!disabled"])
        self.dry_reason.configure(text=dry_why)
        self.go_reason.configure(text=go_why)
        final = self._final()
        self.go_btn.emphasize(not go_why and not final)  # 미리보기를 마치면 '복사 시작'이, 그 전에는 '미리보기 실행'이 파랑으로 강조된다
        self.dry_btn.emphasize(bool(go_why) and not final)  # 복사까지 끝나면 '완료'만 강조한다
        self.done_btn.emphasize(final)
        self._pack_reasons(False)
        again_ok, _why = self.c.can_verify_again()
        self.again_btn.state(["!disabled"] if again_ok else ["disabled"])
        self.retry_btn.state(["!disabled"] if self.c.has_retryable() and not self.c.state.busy else ["disabled"])
        self._update_steps()

    def _update_steps(self) -> None:
        names, current = self.c.transfer_step()
        self.steps_strip.set_names(names)
        self.steps_strip.set(current)

    def on_show(self) -> None:
        st = self.c.state
        vals = (st.transfer_src, st.transfer_dst, st.verify_manifest, st.verify_target)  # 먼저 읽어 둔다(값을 넣을 때마다 상태가 갱신되므로)
        self.src_var.set(vals[0])
        self.dst_var.set(vals[1])
        self.manifest_var.set(vals[2])
        self.target_var.set(vals[3])
        self._apply_flow()
        self.after_idle(self.scroll.scroll_to_top)  # _render_result 가 예약한 결과 스크롤보다 나중에 실행되어 항상 맨 위(입력칸)에서 시작한다
        self.src_entry.focus_set()

    # ------------------------------------------------------------------ 실행
    def _options_changed(self) -> None:
        self.c.set_transfer_options(self.cloud_var.get(), self.strict_var.get())
        self._update_buttons()

    def _opts(self) -> dict:
        return {"strict": self.strict_var.get(), "include_cloud": self.cloud_var.get(),
                "report_path": clean_path(self.rep_var.get())}

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
                                                                              cloud=""), parent=self):
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
        self.guarded(self.c.start_verify, report_path=clean_path(self.vrep_var.get()))

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
        for w in (self.loc_box, self.notes_box, self.layout_more):
            w.pack_forget()

    def _show_extras(self) -> None:
        loc = self.c.location_warning()
        last = self.preview_box
        if loc:
            self.loc_box.configure(text=loc)
            self.loc_box.pack(fill="x", pady=2, after=last)
            last = self.loc_box
        else:
            self.loc_box.pack_forget()
        notes = [n for n in self.c.flow_notes() if n != loc and n != T.XREF_NOTE]
        self.notes_box.configure(text="\n".join(notes))
        self.notes_box.pack(anchor="w", fill="x", pady=2, after=last)
        lay = self.c.layout_lines()
        self.layout_box.configure(text=(T.TR_LAYOUT_TITLE + "\n" + "\n".join(lay) + "\n\n" if lay else "") + T.XREF_NOTE)
        self.layout_more.pack(anchor="w", fill="x", pady=2, after=self.notes_box)

    def _render_result(self) -> None:
        sm = self.c.transfer_summary()
        clear(self.tree)
        self.full = {}
        self._hide_extras()
        self.tree_frame.pack_forget()
        self.res_actions.pack_forget()
        self.result.pack_forget()  # 복사/검사를 마친 뒤에만 '성공/건너뜀/실패' 요약을 보인다(미리보기 때는 빈 자리를 만들지 않는다)
        if not sm:
            self.result.clear()
            self.preview_box.configure(text="")
            self.save_btn.state(["disabled"])
            return
        if not sm["dry_run"]:
            self.result.pack(fill="x", pady=(6, 0), before=self.preview_box)
        if sm["dry_run"]:
            c = sm["counts"]
            self.result.clear()
            self.preview_box.configure(text=T.TR_PREVIEW_RESULT.format(
                new=c.get("would_copy", 0), over=c.get("would_overwrite", 0), same=c.get("skipped_identical", 0),
                cloud=sm["cloud_only"]))
            self._preview_notes()
            self._focus_result()
        elif self.c.is_verify_flow() or sm["mode"] == "verify":
            ok, skipped, failed = self.c.transfer_counts()
            text = T.TR_VERIFY_RESULT.format(ok=sm["verified"], mismatch=sm["mismatch"], missing=sm["missing"], failed=sm["failed"],
                                             added=sm["added"])
            ok_msg = T.TR_VERIFY_OK + ("\n" + T.TR_VERIFY_EXTRA_NOTE if sm["added"] else "")
            if sm["manifest_errors"]:
                ok_msg = T.TR_MANIFEST_ERRORS.format(n=sm["manifest_errors"])
            self.result.show(ok, 0, failed, ok_msg if not failed else text, T.TR_FAIL_HINT if sm["manifest_complete"] else T.TR_VERIFY_INCOMPLETE)
            self.preview_box.configure(text=text if failed else "")  # 성공이면 위의 요약 상자와 같은 말이라 되풀이하지 않는다
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
            pending = bool(sm["pending"]) and not failed
            self.result.show(ok, skipped, failed, text + ("\n" + T.TR_DEFERRED_NOTE if pending else ""), T.TR_FAIL_HINT,
                             pending=pending)
            self.preview_box.configure(text=text if failed else "")  # 성공이면 위의 요약 상자와 같은 말이라 되풀이하지 않는다
            self._show_extras()
        for path, status, err in sm["failures"][:500]:
            iid = self.tree.insert("", "end", tags=("fail",), values=(f"{T.ICON_FAIL} {status}", T.ellipsize_path(path), err))
            self.full[iid] = path
        if sm["failures"]:
            self.tree_frame.pack(fill="x", pady=4, before=self.more)
        for b in (self.save_btn, self.cloud_btn, self.retry_btn, self.again_btn):
            b.pack_forget()
        self.save_btn.pack(side="left")  # 미리보기에서는 '저장'과 (있다면) '클라우드 전용 파일 보기'만, 복사를 마친 뒤에는 다시 시도/검사만 다시도 보인다
        if sm["cloud_only"]:
            self.cloud_btn.pack(side="left", padx=8)
        if not sm["dry_run"]:
            if sm["failures"] or self.c.has_retryable():
                self.retry_btn.pack(side="left", padx=8)
            self.again_btn.pack(side="left", padx=8)
        self.res_actions.pack(anchor="w", pady=4, before=self.more)
        self._layout_bar(self.c.is_verify_flow())
        self.save_btn.state(["!disabled"])
        self.cloud_btn.state(["!disabled"] if sm["cloud_only"] else ["disabled"])
        self._focus_result()

    def _focus_result(self) -> None:
        """결과(미리보기/복사/검사)가 나오면 입력칸 아래의 결과 영역이 첫 화면에 보이도록 스크롤한다(위로 올리면 입력칸이 있다)."""
        target = self.result if self.result.ok_label.cget("text") else self.preview_box
        self.after_idle(lambda: self.scroll.scroll_to(target))

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
        lines.extend(self.c.preview_link_lines())
        if lines:
            self.notes_box.configure(text="\n".join(lines))
            self.notes_box.pack(anchor="w", fill="x", pady=2, after=self.preview_box)
