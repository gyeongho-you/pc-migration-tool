"""신 PC 흐름: 패키지 선택 -> 검증과 미리보기 -> 복원 확정 -> 결과."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .common import (LICENSE_NOTE, OK_FG, SECRET_BG, SECRET_FG, WARN_FG, BaseView, clear, make_text, make_tree,
                     open_location, set_text)


class PackageView(BaseView):
    name = S.NEW_PACKAGE

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.nav = self.navbar(back=lambda: self.c.go(S.START), next_=self._next, next_text="검증하고 미리보기 →")
        self.heading("1. 패키지 선택", "구 PC 에서 만든 설정 패키지(.zip)를 선택하세요. 이 단계에서는 아무것도 쓰지 않습니다.")
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="패키지 파일:").pack(side="left")
        self.path_var = tk.StringVar(value=self.c.state.package)
        ttk.Entry(row, textvariable=self.path_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="찾아보기...", command=self._browse).pack(side="left")
        ttk.Label(self, text="패키지의 모든 파일은 해시로 검증되며, 검증에 실패하면 복원할 수 없습니다.\n" + LICENSE_NOTE,
                  wraplength=860, justify="left").pack(anchor="w", pady=10)

    def _browse(self) -> None:
        p = filedialog.askopenfilename(parent=self, title="설정 패키지 선택", filetypes=[("설정 패키지", "*.zip"), ("모든 파일", "*.*")])
        if p:
            self.path_var.set(p)

    def _next(self) -> None:
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("패키지 선택", "패키지 파일(.zip)을 먼저 선택하세요.", parent=self)
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
        self.nav = self.navbar(back=lambda: self.c.go(S.NEW_PACKAGE), next_=lambda: self.c.go(S.NEW_CONFIRM),
                               next_text="복원 확정 단계로 →")
        self.heading("2. 검증과 미리보기", "아래는 복원했을 때 일어날 일입니다. 지금은 아무것도 쓰지 않았습니다.")
        self.summary = ttk.Label(self, text="", wraplength=860, justify="left")
        self.summary.pack(anchor="w")
        self.problem_box = tk.Label(self, text="", anchor="w", justify="left", wraplength=860)
        self.problem_box.pack(fill="x", pady=4)
        cols = [("status", "처리", 130), ("target", "복원 위치", 480), ("backup", ".bak 백업 위치", 250), ("extra", "구분", 90)]
        frame, self.tree = make_tree(self, cols, height=9)
        frame.pack(fill="both", expand=True, pady=4)
        self.tree.tag_configure("over", foreground=WARN_FG)
        self.tree.tag_configure("extra", background=SECRET_BG, foreground=SECRET_FG)
        ttk.Label(self, text="수동 작업 경고(자동 복원 불가)").pack(anchor="w")
        tf, self.warn = make_text(self, height=5)
        tf.pack(fill="x")

    def on_show(self) -> None:
        self._render()

    def on_event(self, kind, payload) -> None:
        if kind == "preview_done":
            self._render()

    def _render(self) -> None:
        p = self.c.state.preview
        clear(self.tree)
        if p is None:
            self.summary.configure(text="미리보기가 없습니다. 이전 단계에서 패키지를 선택하세요.")
            self.nav["next"].state(["disabled"])
            return
        counts = p.summary()
        self.summary.configure(
            text=f"새로 만들 파일 {counts.get('would_copy', 0)}개, 덮어쓸 파일 {counts.get('would_overwrite', 0)}개(.bak 백업), "
                 f"동일해서 건너뜀 {counts.get('skipped_identical', 0)}개")
        if p.plan.problems:
            self.problem_box.configure(text="⚠ 패키지에 문제가 있어 복원할 수 없습니다:\n" + "\n".join("• " + x for x in p.plan.problems[:8]),
                                       bg=SECRET_BG, fg=SECRET_FG)
            self.nav["next"].state(["disabled"])
        else:
            self.problem_box.configure(text="✔ 패키지 검증 통과(모든 파일 해시 일치)", bg="#e3f3e6", fg=OK_FG)
            self.nav["next"].state(["!disabled"])
        for r in self.c.preview_rows():
            tags = ("extra",) if r["extra"] else (("over",) if "덮어씀" in r["status"] else ())
            self.tree.insert("", "end", tags=tags, values=(r["status"], r["target"], r["backup"],
                                                           "규칙 없이 선택 포함" if r["extra"] else "규칙"))
        lines = [f"{w.get('name', '')}: {i}" for w in p.plan.warnings for i in w.get("items", [])] + [LICENSE_NOTE]
        set_text(self.warn, lines)


class ConfirmView(BaseView):
    name = S.NEW_CONFIRM

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.nav = self.navbar(back=lambda: self.c.go(S.NEW_PREVIEW))
        self.heading("3. 복원 확정", "여기서부터는 실제로 파일이 만들어지거나 덮어써집니다. 기본은 닫혀 있고, 아래를 모두 확인해야 실행할 수 있습니다.")
        self.info = ttk.Label(self, text="", wraplength=860, justify="left")
        self.info.pack(anchor="w", pady=4)
        self.checked_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, variable=self.checked_var, command=self._checked_changed,
                        text="미리보기를 확인했습니다 (덮어쓸 파일은 .bak 으로 백업되는 것을 이해했습니다)").pack(anchor="w", pady=4)
        self.extra_frame = ttk.LabelFrame(self, text="규칙 없이 선택 포함된 항목", padding=8)
        self.extra_var = tk.BooleanVar(value=False)
        self.extra_label = ttk.Label(self.extra_frame, text="", wraplength=840, justify="left", foreground=SECRET_FG)
        self.extra_label.pack(anchor="w")
        ttk.Checkbutton(self.extra_frame, variable=self.extra_var, command=self._extra_changed,
                        text="이 항목들도 복원하는 데 동의합니다(별도 동의)").pack(anchor="w")
        self.run_btn = ttk.Button(self, text="복원 실행...", command=self._run)
        self.run_btn.pack(anchor="w", pady=10)
        self.run_btn.state(["disabled"])
        self.why = ttk.Label(self, text="", foreground=WARN_FG, wraplength=860, justify="left")
        self.why.pack(anchor="w")
        ttk.Label(self, text=LICENSE_NOTE, foreground=WARN_FG).pack(anchor="w", pady=8)

    def on_show(self) -> None:
        p = self.c.state.preview
        n_extra = p.n_extra if p else 0
        self.checked_var.set(self.c.state.preview_checked)
        self.extra_var.set(self.c.state.extra_consent)
        counts = p.summary() if p else {}
        self.info.configure(text=f"패키지: {self.c.state.package}\n새로 만들 파일 {counts.get('would_copy', 0)}개, "
                                 f"덮어쓸 파일 {counts.get('would_overwrite', 0)}개")
        if n_extra:
            self.extra_label.configure(text=f"패키지에 규칙 없이 사용자가 선택해 포함한 파일 {n_extra}개가 있습니다. "
                                            "신 PC 의 해당 위치를 덮어쓸 수 있으므로 내용을 미리보기에서 확인한 뒤 동의하세요.")
            self.extra_frame.pack(fill="x", pady=4, before=self.run_btn)
        else:
            self.extra_frame.pack_forget()
        self._update()

    def on_event(self, kind, payload) -> None:
        if kind == "confirm_changed":
            self._update()

    def _checked_changed(self) -> None:
        self.c.set_preview_checked(self.checked_var.get())

    def _extra_changed(self) -> None:
        self.c.set_extra_consent(self.extra_var.get())

    def _update(self) -> None:
        s, p = self.c.state, self.c.state.preview
        ready = bool(p) and not p.plan.problems and s.preview_checked and (not p.n_extra or s.extra_consent)
        self.run_btn.state(["!disabled"] if ready and not s.busy else ["disabled"])
        if not ready:
            ok, why = self.c.can_apply()
            self.why.configure(text="" if ok else why.replace("확인 대화상자에서 '예'를 눌러야 합니다.", ""))

    def _run(self) -> None:
        p = self.c.state.preview
        n = len(p.plan.items) if p else 0
        if not messagebox.askyesno("복원 실행 확인", f"파일 {n}개를 복원합니다.\n덮어쓸 파일은 .bak 으로 백업됩니다.\n"
                                   "이 작업은 되돌리려면 .bak 파일을 직접 복구해야 합니다.\n\n지금 실행할까요?", icon="warning", parent=self):
            self.c.confirm_final(False)
            return
        self.c.confirm_final(True)
        self.guarded(self.c.start_apply)


class ResultView(BaseView):
    name = S.NEW_RESULT

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.nav = self.navbar()
        self.heading("4. 결과")
        self.summary = ttk.Label(self, text="", wraplength=860, justify="left")
        self.summary.pack(anchor="w")
        cols = [("kind", "구분", 90), ("path", "위치", 520), ("detail", "내용", 260)]
        frame, self.tree = make_tree(self, cols, height=10)
        frame.pack(fill="both", expand=True, pady=6)
        self.tree.tag_configure("fail", background=SECRET_BG, foreground=SECRET_FG)
        self.box = tk.Label(self, text="", anchor="w", justify="left", wraplength=860)
        self.box.pack(fill="x")
        ttk.Label(self, text=LICENSE_NOTE, foreground=WARN_FG).pack(anchor="w", pady=4)
        ttk.Button(self, text="복원 위치 폴더 열기", command=self._open).pack(anchor="w")
        self.first_target = ""

    def on_event(self, kind, payload) -> None:
        if kind == "import_done":
            self.c.go(S.NEW_RESULT)

    def on_show(self) -> None:
        r = self.c.state.import_result
        clear(self.tree)
        if r is None or r.report is None:
            self.summary.configure(text="아직 복원하지 않았습니다.")
            return
        sm = self.c.import_summary()
        counts = sm["counts"]
        self.summary.configure(text=f"복사 {counts.get('copied', 0)}개, 동일해서 건너뜀 {counts.get('skipped_identical', 0)}개, "
                                    f"실패 {counts.get('failed', 0)}개")
        for res in r.report.results:
            if res.status == "failed":
                self.tree.insert("", "end", tags=("fail",), values=("실패", res.dst, res.error))
            elif res.backup:
                self.tree.insert("", "end", values=("백업", res.backup, "덮어쓰기 전 원본 보존(.bak)"))
        for res in r.report.results[:200]:
            if res.status == "copied" and not res.backup:
                self.tree.insert("", "end", values=("복사", res.dst, ""))
                self.first_target = self.first_target or res.dst
        if sm["failures"]:
            self.box.configure(text="⚠ 실패한 파일이 있습니다. 위 목록의 '실패' 항목을 확인하세요.", bg=SECRET_BG, fg=SECRET_FG)
        else:
            self.box.configure(text="✔ 모든 파일을 복원하고 검증했습니다.", bg="#e3f3e6", fg=OK_FG)

    def _open(self) -> None:
        if self.first_target:
            open_location(self.first_target)
