"""'추천대로 한 번에' 진행 화면 하나: 전체 단계 목록 + 지금 상태 + 중지 단추.

단계를 이어 붙이는 일은 controller.auto_run 이 하고, 이 화면은 상태(controller.auto)를 보여 주고 사람의 확인/입력이 필요한 지점
(내보내기·복사·복원 시작 직전, 설정 묶음 파일 고르기, 폴더 고르기)에서 기존과 같은 확인 창을 띄운다. 확인 창을 생략하는 길은 없다.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from .install import InstallPanel
from .retry_panel import RetryPanel
from .status_card import StatusCard
from .common import (DANGER_BG, DANGER_FG, INFO_BG, INFO_FG, MUTED_FG, OK_BG, OK_FG, SUB_FONT, WARN_BG, WARN_FG, ActionButton, BaseView,
                     Collapsible, CoverageBox, PathField, ScrollBody, TextDialog, autowrap, clean_path, open_location)

_STATE_COLORS = {"todo": MUTED_FG, "doing": INFO_FG, "waiting": WARN_FG, "done": OK_FG, "skipped": MUTED_FG, "warn": WARN_FG,
                 "stopped": DANGER_FG}
_TASKS = {T.TASK_SCAN, T.TASK_CHECKLIST, T.TASK_EXPORT, T.TASK_PREVIEW, T.TASK_INSTALL_CHECK, T.TASK_APPLY, T.TASK_TRANSFER, T.TASK_TRANSFER_PREVIEW,
          T.TASK_VERIFY}


class _RunningCard(StatusCard):
    def wants(self) -> bool:
        return bool(self.c.state.busy) and super().wants()


class AutoView(BaseView):
    name = S.AUTO

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar()
        self.stop_btn = self.add_action(T.AUTO_STOP_BTN, self._stop, kind="warn")
        self.primary = None  # Enter 키로 '중지'가 눌리지 않게 한다
        self.header(T.AUTO_HEADING, T.AUTO_SUB["old"])
        self.card_slot = ttk.Frame(self)
        self.card_slot.pack(fill="x")
        self.card = _RunningCard(self, self.c, _TASKS, slot=self.card_slot)  # 작업 중일 때만(확인을 기다리는 동안 지난 작업 결과가 남아 있지 않게)
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        self.body = body
        self.head = ttk.Label(body, text="", font=SUB_FONT, justify="left")
        self.head.pack(anchor="w", fill="x", pady=(2, 2))
        autowrap(self.head)
        self.steps_frame = tk.Frame(body, highlightbackground="#9db7e0", highlightthickness=1, padx=10, pady=3)  # 맨 아래에 pack(위쪽이 지금 할 일)
        self.step_rows: list[tuple[tk.Label, tk.Label, tk.Label]] = []
        self._steps_sig: tuple = ()
        self.stop_note = ttk.Label(body, text="", foreground=WARN_FG, justify="left")
        autowrap(self.stop_note)
        self.msg = tk.Label(body, text="", anchor="w", justify="left", padx=8, pady=3)  # 멈춘 이유 / 끝난 결과(이 화면의 안내 상자)
        autowrap(self.msg, 40)
        self.gate = ttk.Frame(body)  # 사람의 확인/입력이 필요할 때만 내용이 있다
        self.gate.pack(fill="x", pady=2)
        self._gate_sig: tuple = ()
        self.out = ttk.Frame(body)  # 끝나거나 멈춘 뒤의 단추들
        # 구 PC 마지막 화면: 일반 4단계와 같은 '읽지 못해 제외된 파일 다시 담기' 상자, 접힌 설정 확인표, 다음에 할 일
        self.retry_panel = RetryPanel(body, self.c, self.guarded)
        self.retry_panel.pack(fill="x", after=self.gate)
        self.out.pack(fill="x", pady=(3, 0))
        self.next_box = tk.Frame(body, bg=INFO_BG, highlightbackground=INFO_FG, highlightthickness=1, padx=10, pady=4)
        tk.Label(self.next_box, text=T.NEXT_TITLE, bg=INFO_BG, fg=INFO_FG, font=SUB_FONT, anchor="w").pack(anchor="w")
        for line in T.EXP_NEXT_STEPS:
            lbl = tk.Label(self.next_box, text=line, bg=INFO_BG, justify="left", anchor="w")
            lbl.pack(anchor="w", fill="x", pady=1)
            autowrap(lbl, 30)
        self.cov_fold = Collapsible(body, T.EXP_CHECK_FOLD, T.EXP_CHECK_FOLD_OPEN)
        self.cov = CoverageBox(self.cov_fold.body, title="")
        self.cov.title_label.pack_forget()
        self.cov.pack(fill="x")
        self._retried = False
        self.steps_frame.pack(fill="x", pady=(5, 3))
        self.restore = None
        self.install_panel = None  # 설치 확인 지점에서만 있다
        self.finish()

    # ------------------------------------------------------------------ 표시
    @property
    def auto(self):
        return self.c.auto

    def on_show(self) -> None:
        self.scroll.scroll_to_top()
        self._gate_sig = ()
        self.refresh()

    def on_event(self, kind, payload) -> None:
        self.retry_panel.on_event(kind, payload)
        if kind == "export_done":
            self._retried = False
            self._show_cov()
        elif kind == "export_retry_done":
            self._retried = True
            self.c.auto_after_retry(payload)  # 모두 담겼으면 '끝났지만 확인 필요' 를 '완료'로 갱신(auto_changed 가 화면을 다시 그린다)
            self._show_cov()
            self.refresh()
        if kind in ("auto_changed", "busy"):
            self.refresh()
        elif kind == "confirm_changed":
            self._update_restore()
        if kind in ("install_done", "install_marks", "install_folder", "busy") and self.install_panel is not None:
            self.install_panel.on_event(kind, payload)

    def refresh(self) -> None:
        a = self.auto
        if a is None:
            return
        self.sub_label.configure(text=T.AUTO_SUB[a.flow])
        self._draw_steps(a)
        head = {"running": T.AUTO_RUNNING_HEAD, "waiting": T.AUTO_WAITING_HEAD, "stopped": T.AUTO_STOPPED_HEAD,
                "done": T.AUTO_DONE_WARN_HEAD if a.warning else T.AUTO_DONE_HEAD}[a.status]
        self.head.configure(text=head)
        if a.stop_requested and a.active:
            self.stop_note.configure(text=T.AUTO_STOP_NOTE)
            self.stop_note.pack(anchor="w", fill="x", after=self.head)
        else:
            self.stop_note.pack_forget()
        self._draw_message(a)
        if a.active and not self.c.state.busy:  # 작업 중에는 아래 막대의 빨간 '중지'를 쓴다(확인을 기다리는 동안만 이 단추)
            self.stop_btn.pack(side="right")
            self.stop_btn.state(["!disabled"] if not a.stop_requested else ["disabled"])
        else:
            self.stop_btn.pack_forget()
        sig = (a.flow, a.status, a.gate, tuple(a.candidates), a.manual)
        if sig != self._gate_sig:
            self._gate_sig = sig
            self._draw_gate(a)
            self._draw_outcome(a)

    def _draw_steps(self, a) -> None:
        sig = tuple(st.key for st in a.steps)
        if sig != self._steps_sig:
            for w in self.steps_frame.winfo_children():
                w.destroy()
            self.step_rows = []
            for i, _st in enumerate(a.steps):
                num = tk.Label(self.steps_frame, text=f"{i + 1}", width=2, anchor="e")
                title = tk.Label(self.steps_frame, text="", anchor="w", font=SUB_FONT)
                state = tk.Label(self.steps_frame, text="", anchor="w")
                note = tk.Label(self.steps_frame, text="", anchor="w", justify="left", fg=MUTED_FG)
                num.grid(row=2 * i, column=0, sticky="ne", padx=(0, 6))
                title.grid(row=2 * i, column=1, sticky="w")
                state.grid(row=2 * i, column=2, sticky="e", padx=(16, 0))
                note.grid(row=2 * i + 1, column=1, columnspan=2, sticky="w", pady=(0, 2))
                self.step_rows.append((title, state, note))
            self.steps_frame.columnconfigure(1, weight=1)
            self._steps_sig = sig
        for (title, state, note), st in zip(self.step_rows, a.steps):
            color = _STATE_COLORS.get(st.state, MUTED_FG)
            title.configure(text=st.title, fg=color if st.state in ("doing", "waiting", "stopped") else "#111111")
            state.configure(text=T.AUTO_STATE_LABELS.get(st.state, st.state), fg=color)
            note.configure(text=st.note)
            if st.note:
                note.grid()
            else:
                note.grid_remove()

    def _draw_message(self, a) -> None:
        text, bg, fg = "", None, None
        if a.status == "stopped":
            text = "\n".join(x for x in (a.what, a.how) if x)
            bg, fg = (INFO_BG, INFO_FG) if a.by_user else (WARN_BG, WARN_FG)
        elif a.status == "done":
            if a.warning:
                text, bg, fg = a.warning, WARN_BG, WARN_FG
            else:
                lines = self.c.auto_result_lines()
                if self._retried:
                    lines = [T.AUTO_DONE_RETRY_OK, *lines]
                text, bg, fg = "\n".join(lines[:6]), OK_BG, OK_FG
        if text:
            self.msg.configure(text=text, bg=bg, fg=fg)
            self.msg.pack(fill="x", pady=2, before=self.gate)
        else:
            self.msg.pack_forget()

    # ------------------------------------------------------------------ 확인/입력 지점
    def _clear(self, frame: tk.Misc) -> None:
        for w in frame.winfo_children():
            w.destroy()

    def _note(self, parent: tk.Misc, text: str, fg: str = "#111111", **kw) -> ttk.Label:
        lbl = ttk.Label(parent, text=text, justify="left", foreground=fg, **kw)
        lbl.pack(anchor="w", fill="x", pady=2)
        autowrap(lbl, 30)
        return lbl

    def _draw_gate(self, a) -> None:
        self._clear(self.gate)
        self.restore = None
        self.install_panel = None
        if a.status != "waiting":
            return
        build = getattr(self, "_gate_" + a.gate, None)
        if build is not None:
            build(a)

    def _gate_export_confirm(self, a) -> None:
        self._note(self.gate, T.AUTO_EXPORT_CONFIRM_HEAD, font=SUB_FONT)
        self._note(self.gate, f"{T.AUTO_EXPORT_WILL}\n{a.export_path}")
        b = ActionButton(self.gate, text=T.AUTO_EXPORT_CONFIRM_BTN, command=self._export_go, kind="primary")
        b.pack(anchor="w", pady=4)
        self.gate_btn = b

    def _export_go(self) -> None:
        a = self.auto
        exists = T.EXP_EXISTS if os.path.exists(a.export_path) else ""
        if not messagebox.askokcancel(T.EXP_CONFIRM_TITLE, T.EXP_CONFIRM.format(path=a.export_path, exists=exists), parent=self):
            self.guarded(self.c.auto_confirm, False)
            return
        self.guarded(self.c.auto_confirm, True)

    def _gate_pick_package(self, a) -> None:
        self._note(self.gate, T.AUTO_PKG_PICK_HEAD.format(n=len(a.candidates)), font=SUB_FONT)
        self.pick_var = tk.StringVar(value=a.candidates[0])
        for path in a.candidates:
            ttk.Radiobutton(self.gate, text=path, value=path, variable=self.pick_var).pack(anchor="w", pady=1)
        b = ActionButton(self.gate, text=T.AUTO_PKG_PICK_BTN, command=lambda: self.guarded(self.c.auto_pick_package, self.pick_var.get()),
                         kind="primary")
        b.pack(anchor="w", pady=6)
        self.gate_btn = b

    # ---- 프로그램 설치 확인(설치 명령은 도구가 실행하지 않는다: 복사해서 직접 실행하고 '다시 확인')
    def _gate_install(self, a) -> None:
        self._note(self.gate, T.AUTO_INSTALL_HEAD, font=SUB_FONT)
        self._note(self.gate, T.AUTO_INSTALL_NOTE)
        row = ttk.Frame(self.gate)  # 진행 단추는 표 위에 둔다(스크롤하지 않아도 보이게)
        row.pack(anchor="w", pady=(2, 6))
        b = ActionButton(row, text=T.AUTO_INSTALL_CONTINUE, command=lambda: self.guarded(self.c.auto_install_continue, False), kind="primary")
        b.pack(side="left")
        ActionButton(row, text=T.INST_BTN_SKIP, command=lambda: self.guarded(self.c.auto_install_continue, True),
                     kind="plain").pack(side="left", padx=8)
        self.gate_btn = b
        self.install_panel = InstallPanel(self.gate, self, height=4)
        self.install_panel.pack(fill="x", pady=4)
        self.install_panel.refresh()

    # ---- 복원 확인(기존 확인 화면과 같은 체크 2개 + 최종 확인창)
    def _gate_restore(self, a) -> None:
        c = self.c
        p = c.state.preview
        counts = p.summary() if p else {}
        new, over = counts.get("would_copy", 0), counts.get("would_overwrite", 0)
        self._note(self.gate, T.AUTO_RESTORE_HEAD, font=SUB_FONT)
        self._note(self.gate, T.AUTO_RESTORE_SUMMARY.format(new=new, over=over, same=counts.get("skipped_identical", 0)))
        extra_lines = [x for x in (c.denied_note(), c.git_config_note()) if x]
        if extra_lines:
            self._note(self.gate, "\n".join(extra_lines), fg=WARN_FG)
        ActionButton(self.gate, text=T.AUTO_BTN_PREVIEW_LIST, kind="link", command=self._show_preview_list).pack(anchor="w")
        self.checked_var, self.bak_var, self.extra_var = tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()
        self.check = ttk.Checkbutton(self.gate, variable=self.checked_var, text=T.CONF_CHECK,
                                     command=lambda: c.set_preview_checked(self.checked_var.get()))
        self.check.pack(anchor="w", pady=3)
        self.check_bak = ttk.Checkbutton(self.gate, variable=self.bak_var, text=T.CONF_CHECK_BAK,
                                         command=lambda: c.set_bak_understood(self.bak_var.get()))
        self.check_bak.pack(anchor="w", pady=3)
        self.extra_check = None
        if p and p.n_extra:
            self._note(self.gate, T.CONF_EXTRA_INFO.format(n=p.n_extra), fg=DANGER_FG)
            self.extra_check = ttk.Checkbutton(self.gate, variable=self.extra_var, text=T.CONF_EXTRA_CHECK,
                                               command=lambda: c.set_extra_consent(self.extra_var.get()))
            self.extra_check.pack(anchor="w", pady=3)
        label = T.BTN_APPLY_N.format(n=over) if over else T.BTN_APPLY_NEW.format(n=new) if new else T.BTN_APPLY
        self.apply_btn = ActionButton(self.gate, text=label, command=self._apply, kind="warn")
        self.apply_btn.pack(anchor="w", pady=(6, 2))
        self.apply_why = ttk.Label(self.gate, text="", foreground=WARN_FG, justify="left")
        self.apply_why.pack(anchor="w", fill="x")
        autowrap(self.apply_why, 30)
        self.restore = True
        self.checked_var.set(c.state.preview_checked)
        self.bak_var.set(c.state.bak_understood)
        self._update_restore()
        self.gate_btn = self.check

    def _update_restore(self) -> None:
        if not getattr(self, "restore", None):
            return
        why = self.c.apply_reason()
        self.apply_btn.state(["!disabled"] if not why and not self.c.state.busy else ["disabled"])
        self.apply_why.configure(text=why)

    def _show_preview_list(self) -> None:
        rows = self.c.preview_rows()
        lines = [f"{r['status']}  {r['target']}" + (f"  (백업: {r['backup']})" if r["backup"] else "") for r in rows[:2000]]
        TextDialog(self, T.AUTO_PREVIEW_LIST_TITLE, "\n".join(lines) or T.CL_EMPTY, 96, 18)

    def _apply(self) -> None:
        p = self.c.state.preview
        n = len(p.plan.items) if p else 0
        if not messagebox.askyesno(T.CONF_DIALOG_TITLE, T.CONF_DIALOG.format(n=n), icon="warning", parent=self):
            self.c.confirm_final(False)
            return
        self.c.confirm_final(True)  # 최종 확인창에서 '예'를 눌렀을 때만 켜진다(체크 2개가 없으면 켜지지 않는다)
        self.guarded(self.c.auto_apply)

    # ---- 대용량: 질문 두 개
    def _gate_questions(self, a) -> None:
        self.q1, self.q2 = tk.StringVar(value=T.MODE_EXTERNAL), tk.IntVar(value=1)
        self._note(self.gate, T.AUTO_Q1, font=SUB_FONT)
        for key, title, desc in T.TR_MODES:
            if key == T.MODE_LOCAL:
                continue
            ttk.Radiobutton(self.gate, text=title, value=key, variable=self.q1).pack(anchor="w", pady=1)
            self._note(self.gate, desc, fg=MUTED_FG, padding=(24, 0, 0, 0))
        self._note(self.gate, T.AUTO_Q2, font=SUB_FONT)
        for n, text in enumerate(T.AUTO_Q2_OPTIONS, 1):
            ttk.Radiobutton(self.gate, text=text, value=n, variable=self.q2).pack(anchor="w", pady=1)
        b = ActionButton(self.gate, text=T.AUTO_Q_NEXT, command=lambda: self.guarded(self.c.auto_answer_questions, self.q1.get(), self.q2.get()),
                         kind="primary")
        b.pack(anchor="w", pady=6)
        self.gate_btn = b

    # ---- 대용량: 폴더 고르기
    def _gate_folders(self, a) -> None:
        c = self.c
        self.f1, self.f2 = tk.StringVar(), tk.StringVar()
        self._note(self.gate, T.AUTO_FOLDERS_HEAD, font=SUB_FONT)
        if c.is_verify_flow():
            self.field1 = PathField(self.gate, self.f1, self._pick_manifest, label=T.TR_MANIFEST_LABEL, hint=T.TR_MANIFEST_HINT,
                                    extra=[(T.BTN_AUTOFIND, self._autofind)])
            self.field2 = PathField(self.gate, self.f2, lambda: self._pick_dir(self.f2), label=T.TR_TARGET_LABEL, hint=T.TR_TARGET_HINT,
                                    drives=c.list_drives, on_drive=lambda root: self.f2.set(root))
            go_text = T.AUTO_FOLDERS_BTN_VERIFY
        else:
            src_text, dst_text = c.flow_labels()
            mode, _stage = c.flow()
            self.field1 = PathField(self.gate, self.f1, lambda: self._pick_dir(self.f1), label=src_text, hint=T.TR_SRC_HINT,
                                    drives=c.list_drives, on_drive=lambda root: self.f1.set(root))
            self.field2 = PathField(self.gate, self.f2, lambda: self._pick_dir(self.f2), label=dst_text, hint=c.flow_dst_hint(),
                                    drives=c.list_drives if mode != T.MODE_NETWORK else None,
                                    on_drive=lambda root: self.f2.set(os.path.join(root, T.DRIVE_FOLDER_DEFAULT)
                                                                      if mode == T.MODE_EXTERNAL else root))
            go_text = T.AUTO_FOLDERS_BTN
        self.field1.pack(fill="x", pady=(2, 0))
        self.field2.pack(fill="x", pady=(6, 0))
        b = ActionButton(self.gate, text=go_text, command=self._folders_go, kind="primary")
        b.pack(anchor="w", pady=8)
        self.gate_btn = b

    def _pick_dir(self, var) -> None:
        p = filedialog.askdirectory(parent=self, title=T.TR_PICK_FOLDER, mustexist=False)
        if p:
            var.set(p.replace("/", "\\"))

    def _pick_manifest(self) -> None:
        p = filedialog.askopenfilename(parent=self, title=T.TR_PICK_MANIFEST,
                                       filetypes=[(T.TR_FILE_KIND, "*.jsonl"), ("모든 파일", "*.*")])
        if p:
            self.f1.set(p.replace("/", "\\"))

    def _autofind(self) -> None:
        target = clean_path(self.f2.get())
        found = self.c.find_manifest(target) if target else ""
        if found:
            self.f1.set(found)
        else:
            messagebox.showinfo(T.TR_AUTOFIND_NEED_FOLDER if not target else T.AUTO_FOLDERS_HEAD,
                                T.TR_AUTOFIND_NEED_FOLDER if not target else T.TR_AUTOFIND_NONE, parent=self)

    def _folders_go(self) -> None:
        self.field1.normalize()
        self.field2.normalize()
        self.guarded(self.c.auto_set_folders, clean_path(self.f1.get()), clean_path(self.f2.get()))

    def _gate_transfer_confirm(self, a) -> None:
        self._note(self.gate, T.AUTO_TR_CONFIRM_HEAD, font=SUB_FONT)
        self._note(self.gate, T.AUTO_TR_WILL)
        b = ActionButton(self.gate, text=T.AUTO_TR_CONFIRM_BTN, command=self._transfer_go, kind="warn")
        b.pack(anchor="w", pady=4)
        self.gate_btn = b

    def _transfer_go(self) -> None:
        s = self.c.state
        flow = T.TR_CONFIRM_FLOW.format(flow=self.c.flow_title(), method=self.c.flow_method())
        if not messagebox.askokcancel(T.TR_CONFIRM_TITLE, T.TR_CONFIRM.format(src=s.transfer_src, dst=s.transfer_dst, cloud="") + flow,
                                      parent=self):
            self.guarded(self.c.auto_confirm, False)
            return
        self.guarded(self.c.auto_confirm, True)

    def _gate_verify_confirm(self, a) -> None:
        self._note(self.gate, T.AUTO_VERIFY_WILL)
        b = ActionButton(self.gate, text=T.AUTO_TR_VERIFY_BTN, command=self._verify_go, kind="primary")
        b.pack(anchor="w", pady=4)
        self.gate_btn = b

    def _verify_go(self) -> None:
        s = self.c.state
        if not messagebox.askokcancel(T.TR_VERIFY_CONFIRM_TITLE, T.TR_VERIFY_CONFIRM.format(listfile=s.verify_manifest, target=s.verify_target),
                                      parent=self):
            self.guarded(self.c.auto_confirm, False)
            return
        self.guarded(self.c.auto_confirm, True)

    # ------------------------------------------------------------------ 끝나거나 멈춘 뒤
    def _show_cov(self) -> None:
        """끝난 구 PC 흐름의 설정 확인표(접힘). 헤드라인('담김 5 · 직접 해야 함 3 · 해당 없음 12')은 접힌 제목에 보인다."""
        a = self.auto
        if a is None or a.flow != "old" or a.status != "done":
            self.cov_fold.pack_forget()
            return
        table = self.c.coverage_table()
        self.cov.show(table)
        head = self.c.coverage_headline(table)
        self.cov_fold.title = f"{T.EXP_CHECK_TITLE}: {head} ▸" if head else T.EXP_CHECK_FOLD
        self.cov_fold.title_open = self.cov_fold.title.replace("▸", "▾")
        self.cov_fold.set_open(self.cov_fold.open)
        self.cov_fold.pack(fill="x", pady=(2, 0), after=self.next_box)

    def _draw_outcome(self, a) -> None:
        self._clear(self.out)
        self.next_box.pack_forget()
        self.cov_fold.pack_forget()
        if a.active:
            return
        if a.status == "stopped":
            label = T.AUTO_BTN_PICK_MANUAL if a.flow == "new" and not a.candidates else T.AUTO_BTN_MANUAL
            if self.c.auto_export_stopped():  # 내보내기가 예외로 멈췄다: 저장점부터 이어서 / 처음부터 / 직접 진행
                ActionButton(self.out, text=T.BTN_EXP_RETRY_SAVEPOINT, command=lambda: self._retry_export(False),
                             kind="primary").pack(side="left", padx=(0, 8))
                ActionButton(self.out, text=T.BTN_EXP_RESTART, command=lambda: self._retry_export(True),
                             kind="plain").pack(side="left", padx=(0, 8))
                label = T.AUTO_BTN_MANUAL
                ActionButton(self.out, text=label, command=self.c.auto_manual, kind="plain").pack(side="left")
                return
            ActionButton(self.out, text=label, command=self.c.auto_manual, kind="primary").pack(side="left")
        elif a.flow == "old":
            ActionButton(self.out, text=T.BTN_OPEN_RESULT, command=lambda: open_location(self.c.state.export_output),
                         kind="plain").pack(side="left", padx=(0, 8))
            ActionButton(self.out, text=T.AUTO_BTN_CHECKLIST, command=lambda: self.c.go(S.OLD_CHECKLIST), kind="plain").pack(side="left")
            self.next_box.pack(fill="x", pady=(4, 0), after=self.out)  # 안의 글은 한 번만 만든다(다시 그릴 때 글 상자를 지우면 줄바꿈 처리기가 남는다)
            self._show_cov()
        elif a.flow == "transfer":
            ActionButton(self.out, text=T.AUTO_BTN_DETAIL, command=lambda: self.c.go(S.TRANSFER), kind="plain").pack(side="left")
        ActionButton(self.out, text=T.AUTO_BTN_HOME, command=lambda: self.c.go(S.START), kind="plain").pack(side="left", padx=8)

    # ------------------------------------------------------------------ 내보내기가 멈춘 뒤 다시 시도
    def _retry_export(self, restart: bool) -> None:
        a = self.auto
        if restart:
            if not messagebox.askokcancel(T.EXP_RESTART_TITLE, T.EXP_RESTART_CONFIRM, parent=self):
                return
        else:
            exists = T.EXP_EXISTS if a is not None and os.path.exists(a.export_path) else ""
            if not messagebox.askokcancel(T.EXP_CONFIRM_TITLE, T.EXP_CONFIRM.format(path=a.export_path if a else "", exists=exists),
                                          parent=self):
                return
        self.guarded(self.c.auto_retry_export, restart)

    # ------------------------------------------------------------------ 중지
    def _stop(self) -> None:
        self.c.auto_stop()
        self.refresh()
