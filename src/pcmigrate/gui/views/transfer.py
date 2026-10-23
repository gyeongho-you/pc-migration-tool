"""대용량 데이터 복사 화면(robocopy + SHA-256 검증). 원본은 읽기만 한다."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .common import OK_FG, SECRET_BG, SECRET_FG, WARN_FG, BaseView, clear, make_text, make_tree, set_text


class TransferView(BaseView):
    name = S.TRANSFER

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.nav = self.navbar(back=lambda: self.c.go(S.START))
        self.heading("대용량 데이터 복사",
                     "원본 폴더의 모든 파일을 대상 폴더로 복사하고, 복사본을 하나씩 SHA-256 으로 검증합니다. "
                     "원본은 읽기만 합니다(수정/삭제/이동하지 않음). 중단되면 같은 설정으로 다시 실행해 이어받을 수 있습니다.")
        form = ttk.Frame(self)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.src_var, self.dst_var, self.rep_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._row(form, 0, "원본 폴더:", self.src_var, lambda: self._pick_dir(self.src_var))
        self._row(form, 1, "대상 폴더:", self.dst_var, lambda: self._pick_dir(self.dst_var),
                  hint="외장 드라이브(E:\\) 또는 네트워크 경로(\\\\서버\\공유)를 직접 입력해도 됩니다.")
        self._row(form, 2, "리포트 저장(선택):", self.rep_var, self._pick_report, hint="결과를 JSON 으로 남깁니다.")
        self.src_var.trace_add("write", lambda *a: self._check())
        self.dst_var.trace_add("write", lambda *a: self._check())

        self.check_label = tk.Label(self, text="", anchor="w", justify="left", wraplength=860)
        self.check_label.pack(fill="x", pady=4)

        opts = ttk.LabelFrame(self, text="옵션", padding=8)
        opts.pack(fill="x", pady=4)
        self.strict_var, self.cloud_var = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, variable=self.strict_var, text="엄격 검증(--strict): 모든 파일을 처음부터 다시 해시로 확인").pack(anchor="w")
        ttk.Label(opts, text="   이어받기 때 이전 검증 기록을 믿지 않고 전부 다시 확인합니다. 내용이 어긋난 파일은 원본에서 다시 복사합니다"
                             "(기존 사본은 .bak 으로 보존). 시간이 더 걸립니다.", wraplength=820, justify="left").pack(anchor="w")
        ttk.Checkbutton(opts, variable=self.cloud_var,
                        text="OneDrive 클라우드 전용 파일도 포함 (기본 꺼짐)").pack(anchor="w", pady=(6, 0))
        ttk.Label(opts, text="   클라우드 전용(온디맨드) 파일은 PC 에 내용이 없어, 복사하려면 인터넷으로 모두 내려받아야 합니다. "
                             "용량과 시간이 크게 늘 수 있어 기본은 건너뛰고 결과에 'cloud_only' 로 표시합니다.",
                  wraplength=820, justify="left", foreground=WARN_FG).pack(anchor="w")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=6)
        self.dry_btn = ttk.Button(btns, text="① 미리보기(쓰기 없음)", command=self._dry)
        self.dry_btn.pack(side="left")
        self.go_btn = ttk.Button(btns, text="② 복사 시작...", command=self._go)
        self.go_btn.pack(side="left", padx=8)
        self.save_btn = ttk.Button(btns, text="리포트 저장...", command=self._save_report)
        self.save_btn.pack(side="left")
        self.cloud_btn = ttk.Button(btns, text="클라우드 전용 파일 보기", command=self._show_cloud)
        self.cloud_btn.pack(side="left", padx=8)
        for b in (self.go_btn, self.save_btn, self.cloud_btn):
            b.state(["disabled"])

        self.summary = tk.Label(self, text="", anchor="w", justify="left", wraplength=860)
        self.summary.pack(fill="x")
        cols = [("kind", "구분", 100), ("path", "파일", 520), ("detail", "내용", 260)]
        frame, self.tree = make_tree(self, cols, height=7)
        frame.pack(fill="both", expand=True, pady=4)
        self.tree.tag_configure("fail", background=SECRET_BG, foreground=SECRET_FG)

    def _row(self, form, r, label, var, browse, hint="") -> None:
        ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=var).grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(form, text="찾아보기...", command=browse).grid(row=r, column=2)
        if hint:
            ttk.Label(form, text=hint, foreground="#555555").grid(row=r, column=3, sticky="w", padx=6)

    def _pick_dir(self, var) -> None:
        p = filedialog.askdirectory(parent=self, title="폴더 선택", mustexist=False)
        if p:
            var.set(p.replace("/", "\\"))

    def _pick_report(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title="리포트 저장 위치", defaultextension=".json",
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
            self.check_label.configure(text="", bg="#f0f0f0")
        else:
            body = "\n".join(("✔ " if ok else "✖ ") + t for ok, t in res.lines)
            self.check_label.configure(text=body, bg="#e3f3e6" if res.ok else SECRET_BG, fg=OK_FG if res.ok else SECRET_FG)
        self._update_buttons(res.ok)
        return res

    def _update_buttons(self, ok: bool | None = None) -> None:
        s = self.c.state
        if ok is None:
            ok = self.c.check_transfer_paths(s.transfer_src, s.transfer_dst).ok
        idle = not s.busy
        self.dry_btn.state(["!disabled"] if ok and idle else ["disabled"])
        previewed = s.transfer_preview_key == (s.transfer_src, s.transfer_dst)
        self.go_btn.state(["!disabled"] if ok and idle and previewed else ["disabled"])

    def on_show(self) -> None:
        self.src_var.set(self.c.state.transfer_src)
        self.dst_var.set(self.c.state.transfer_dst)
        self._check()

    def _transfer_opts(self) -> dict:
        return {"strict": self.strict_var.get(), "include_cloud": self.cloud_var.get(),
                "report_path": self.rep_var.get().strip()}

    def _dry(self) -> None:
        self._sync_state()
        self.guarded(self.c.start_transfer, dry_run=True, **self._transfer_opts())

    def _go(self) -> None:
        self._sync_state()
        s = self.c.state
        extra = "\n⚠ OneDrive 클라우드 전용 파일을 포함하므로 대량 다운로드가 발생할 수 있습니다." if self.cloud_var.get() else ""
        if not messagebox.askokcancel(
                "복사 확인", f"다음 위치에 복사합니다(원본은 읽기만 합니다):\n\n원본: {s.transfer_src}\n대상: {s.transfer_dst}\n\n"
                "대상 폴더에 파일과 진행 기록(.pcmigrate-state.json), 로그(.pcmigrate-robocopy.log)가 만들어집니다."
                f"{extra}\n\n시작할까요? (진행 중 '취소'로 멈추고 나중에 이어받을 수 있습니다)", parent=self):
            return
        self.guarded(self.c.start_transfer, dry_run=False, write_confirmed=True, **self._transfer_opts())

    def _save_report(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title="리포트 저장", defaultextension=".json",
                                         filetypes=[("JSON", "*.json")], initialfile="transfer-report.json")
        if not p:
            return
        try:
            self.c.save_transfer_report(p)
            messagebox.showinfo("저장 완료", f"저장했습니다:\n{p}", parent=self)
        except Exception as e:  # noqa: BLE001
            from ..controller import friendly_error
            fe = friendly_error(e)
            messagebox.showerror(fe.title, fe.message, parent=self)

    def _show_cloud(self) -> None:
        files = self.c.transfer_summary().get("cloud_files", [])
        win = tk.Toplevel(self)
        win.title("클라우드 전용 파일(건너뜀)")
        frame, text = make_text(win, height=18)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        set_text(text, files[:2000] or "(없음)")

    def on_event(self, kind, payload) -> None:
        if kind in ("busy", "selection_changed"):
            self._update_buttons()
        if kind == "transfer_done":
            self._render_result()
            self._update_buttons()

    def _render_result(self) -> None:
        sm = self.c.transfer_summary()
        clear(self.tree)
        if not sm:
            return
        head = "미리보기(쓰기 없음) 결과" if sm["dry_run"] else "복사 결과"
        if sm["dry_run"]:
            c = sm["counts"]
            text = (f"{head}: 새로 복사 {c.get('would_copy', 0)}개, 덮어씀 {c.get('would_overwrite', 0)}개, "
                    f"이미 같음 {c.get('skipped_identical', 0)}개, 클라우드 전용(건너뜀) {sm['cloud_only']}개. "
                    "이상 없으면 ② 복사 시작을 누르세요.")
            self.summary.configure(text=text, bg="#eef3fb", fg="black")
        else:
            bad = sm["mismatch"] + sm["missing"] + sm["failed"]
            text = (f"{head}: 검증 성공 {sm['verified']}개, 불일치 {sm['mismatch']}개, 누락 {sm['missing']}개, "
                    f"실패 {sm['failed']}개, 클라우드 전용(건너뜀) {sm['cloud_only']}개")
            if sm["repaired"]:
                text += f", 재복사로 복구 {len(sm['repaired'])}개"
            self.summary.configure(text=text, bg=("#e3f3e6" if not bad else SECRET_BG), fg=(OK_FG if not bad else SECRET_FG))
        for path, status, err in sm["failures"][:500]:
            self.tree.insert("", "end", tags=("fail",), values=(status, path, err))
        self.save_btn.state(["!disabled"])
        self.cloud_btn.state(["!disabled"] if sm["cloud_only"] else ["disabled"])
