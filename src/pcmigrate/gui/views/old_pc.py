"""구 PC 흐름: 스캔 -> 규칙 없는 프로그램 확인 -> 체크리스트 -> 설정 패키지 내보내기."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .common import (LICENSE_NOTE, OK_FG, PRIVACY_NOTE, SECRET_BG, SECRET_FG, WARN_FG, BaseView, clear,
                     make_text, make_tree, open_location, set_text)


class ScanView(BaseView):
    name = S.OLD_SCAN

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.nav = self.navbar(back=lambda: self.c.go(S.START), next_=lambda: self.c.go(S.OLD_DISCOVER))
        self.nav["next"].state(["disabled"])
        self.heading("1. 스캔", "이 PC 에 설치된 프로그램을 읽고(읽기 전용), 알려진 프로그램의 규칙과 대조합니다. 아무것도 쓰지 않습니다.")
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.btn = ttk.Button(top, text="스캔 시작", command=lambda: self.guarded(self.c.start_scan))
        self.btn.pack(side="left")
        self.summary = ttk.Label(top, text="아직 스캔하지 않았습니다.")
        self.summary.pack(side="left", padx=12)
        ttk.Label(self, text="규칙과 일치한 프로그램(정밀 처리 대상)과 근거").pack(anchor="w", pady=(10, 2))
        frame, self.tree = make_tree(self, [("rule", "프로그램(규칙)", 260), ("evidence", "근거", 560)], height=12)
        frame.pack(fill="both", expand=True)

    def on_show(self) -> None:
        self.nav["next"].state(["!disabled"] if self.c.state.scan else ["disabled"])

    def on_event(self, kind, payload) -> None:
        if kind != "scan_done":
            return
        r = payload
        clear(self.tree)
        for info in r.match_info:
            self.tree.insert("", "end", values=(info.rule.name, info.evidence))
        extra = f", 규칙 없는 설치 프로그램 {r.discovery[0]}개" if r.discovery else ""
        self.summary.configure(text=f"설치 프로그램 {r.program_count}개 확인, 규칙 일치 {len(r.matched)}개{extra}")
        self.nav["next"].state(["!disabled"])


class DiscoverView(BaseView):
    name = S.OLD_DISCOVER

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.nav = self.navbar(back=lambda: self.c.go(S.OLD_SCAN), next_=lambda: self.c.go(S.OLD_CHECKLIST))
        self._bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.heading("2. 규칙 없는 프로그램 확인",
                     "규칙이 없는 프로그램의 설정 폴더 후보입니다. 아무것도 자동으로 포함되지 않습니다. "
                     "패키지에 넣을 항목만 직접 선택하세요(행을 클릭하면 선택/해제).")
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="탐지 시작", command=lambda: self.guarded(self.c.start_discover)).pack(side="left")
        self.info = ttk.Label(top, text="읽기 전용입니다. 폴더 크기를 계산하느라 1분 안팎 걸릴 수 있습니다(취소 가능).")
        self.info.pack(side="left", padx=12)

        cols = [("sel", "선택", 50), ("warn", "경고", 60), ("prog", "프로그램", 200), ("path", "위치", 330),
                ("size", "크기", 80), ("grade", "연결 근거", 200)]
        frame, self.tree = make_tree(self, cols, height=11)
        frame.pack(fill="both", expand=True, pady=6)
        self.tree.tag_configure("secret", background=SECRET_BG, foreground=SECRET_FG)
        self.tree.tag_configure("weak", foreground=WARN_FG)
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)

        self.detail = ttk.Label(self, text="", wraplength=860, justify="left")
        self.detail.pack(anchor="w")
        self.banner = tk.Label(self, text="", anchor="w", justify="left", wraplength=860)
        self.banner.pack(fill="x", pady=4)
        self.ack_var = tk.BooleanVar(value=False)
        self.ack = ttk.Checkbutton(self, variable=self.ack_var, command=self._ack_changed,
                                   text="⚠ 비밀 정보(키, 비밀번호, 쿠키 등)가 포함될 수 있음을 이해했습니다 "
                                        "(체크해야 경고 항목을 선택할 수 있습니다)")
        self.ack.pack(anchor="w")
        self.count = ttk.Label(self, text="선택한 항목: 0개")
        self.count.pack(anchor="w", pady=(4, 0))
        self.rows: dict[str, object] = {}

    def on_show(self) -> None:
        self._refresh()

    def on_event(self, kind, payload) -> None:
        if kind == "discover_done":
            d = payload
            self.info.configure(text=f"규칙 없는 설치 프로그램 {d.program_count}개, 설정 폴더 후보 {d.candidate_count}개"
                                     f"(비밀 정보 가능성 {d.secret_count}개)")
            self.ack_var.set(False)
            self._refresh()
        elif kind == "selection_changed":
            self._refresh()

    def _refresh(self) -> None:
        clear(self.tree)
        self.rows = {}
        rows = self.c.candidate_rows()
        for r in rows:
            checked = r.key in self.c.state.selected
            mark = "☑" if checked else ("—" if not r.selectable else "☐")
            warn = "⚠ 비밀" if r.secret else ("약함" if r.weak else "")
            tags = ("secret",) if r.secret else (("weak",) if r.weak else ())
            iid = self.tree.insert("", "end", values=(mark, warn, r.programs, r.path, r.size, f"{r.grade}"), tags=tags)
            self.rows[iid] = r
        n_secret = sum(1 for r in rows if r.secret)
        if n_secret:
            self.banner.configure(
                text=f"⚠ 비밀 정보 가능성 {n_secret}개 항목은 붉게 표시됩니다. 키/비밀번호/쿠키/토큰이 들어 있을 수 있어 기본적으로 선택할 수 "
                     "없습니다. 정말 필요할 때만 아래 동의에 체크한 뒤 선택하세요.", bg=SECRET_BG, fg=SECRET_FG)
        else:
            self.banner.configure(text="", bg=self._bg, fg="black")
        sel = self.c.selected_rows()
        big = sum(1 for r in sel if r.size.endswith("GB"))
        self.count.configure(text=f"선택한 항목: {len(sel)}개" + (f" (1GB 이상 {big}개 — 큰 폴더는 대용량 복사를 권장)" if big else ""))
        self.ack_var.set(self.c.state.secrets_ack)

    def _click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.rows:
            return
        r = self.rows[iid]
        on = r.key not in self.c.state.selected
        self.guarded(self.c.set_selected, r.key, on)

    def _show_detail(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel or sel[0] not in self.rows:
            return
        r = self.rows[sel[0]]
        text = f"{r.kind}: {r.path}\n연결 근거: {r.grade}" + (f" — {r.reason}" if r.reason else "")
        if r.secret:
            text += "\n⚠ 비밀 정보 가능성: " + ", ".join(r.secret)
        self.detail.configure(text=text)

    def _ack_changed(self) -> None:
        on = self.ack_var.get()
        if on and not messagebox.askyesno(
                "비밀 정보 포함 확인", "비밀 정보(SSH 키, 클라우드 자격 증명, 브라우저 쿠키/저장된 비밀번호 등)가 들어 있을 수 있는 "
                "항목을 선택할 수 있게 합니다.\n패키지 파일을 안전하게 보관할 수 있습니까?", parent=self):
            self.ack_var.set(False)
            return
        self.c.set_secrets_ack(on)


class ChecklistView(BaseView):
    name = S.OLD_CHECKLIST

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.OLD_DISCOVER), next_=lambda: self.c.go(S.OLD_EXPORT))
        self.heading("3. 체크리스트", "신 PC 에서 직접 해야 할 일 목록입니다. 저장해 두고 이전 작업 때 확인하세요.")
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="체크리스트 만들기", command=lambda: self.guarded(self.c.start_checklist)).pack(side="left")
        self.save_btn = ttk.Button(top, text="파일로 저장...", command=self._save)
        self.save_btn.pack(side="left", padx=8)
        self.save_btn.state(["disabled"])
        ttk.Label(top, text="※ 저장 파일에는 개인 정보가 들어 있습니다.", foreground=WARN_FG).pack(side="left", padx=8)
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, pady=8)
        self.texts: dict[str, tk.Text] = {}
        for tab in ("요약", "1. 먼저 설치", "2. 로그인·인증", "3. 데이터·기타", "참고"):
            frame, text = make_text(self.nb)
            self.nb.add(frame, text=tab)
            self.texts[tab] = text

    def on_event(self, kind, payload) -> None:
        if kind != "checklist_done":
            return
        tabs = self.c.checklist_tabs()
        for name, text in self.texts.items():
            lines = tabs.get(name, [])
            plain = 2 if name == "1. 먼저 설치" else (len(lines) if name in ("요약", "참고") else 0)  # 안내문은 체크박스 없이
            body = "\n".join(("" if i < plain else "☐ ") + l for i, l in enumerate(lines)) if lines else "(해당 없음)"
            set_text(text, body)
        self.save_btn.state(["!disabled"])

    def _save(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="체크리스트 저장", defaultextension=".md",
                                            filetypes=[("Markdown", "*.md"), ("텍스트", "*.txt")])
        if not path:
            return
        if not messagebox.askokcancel("저장 확인", f"다음 위치에 저장합니다:\n{path}\n\n개인 정보 주의: "
                                      f"이 파일에는 이 PC 의 설치 프로그램 목록 등이 들어 있습니다. 저장소에 커밋하거나 남에게 공유하지 마세요.\n"
                                      "(같은 이름의 파일이 있으면 .bak 으로 보존합니다)", parent=self):
            return
        try:
            text = self.c.checklist_text("txt" if path.lower().endswith(".txt") else "md")
            self.c.save_text(path, text)
            messagebox.showinfo("저장 완료", f"저장했습니다:\n{path}", parent=self)
        except Exception as e:  # noqa: BLE001 - 한국어 메시지로 변환
            from ..controller import friendly_error
            fe = friendly_error(e)
            messagebox.showerror(fe.title, fe.message, parent=self)


class ExportView(BaseView):
    name = S.OLD_EXPORT

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.open_btn = ttk.Button(self, text="결과 위치 열기", command=self._open)
        self.open_btn.pack(side="bottom", anchor="e")
        self.open_btn.state(["disabled"])
        self.navbar(back=lambda: self.c.go(S.OLD_CHECKLIST))
        self.heading("4. 설정 패키지 내보내기",
                     "규칙에 따라 설정/템플릿/단축키와 프로그램 목록을 하나의 .zip 으로 만듭니다. 원본은 읽기만 하고 수정하지 않습니다.")
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="저장할 파일:").pack(side="left")
        self.path_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.path_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="찾아보기...", command=self._browse).pack(side="left")
        self.plan = ttk.Label(self, text="", wraplength=860, justify="left")
        self.plan.pack(anchor="w", pady=8)
        self.go_btn = ttk.Button(self, text="패키지 만들기...", command=self._export)
        self.go_btn.pack(anchor="w")
        ttk.Label(self, text=LICENSE_NOTE, foreground=WARN_FG).pack(anchor="w", pady=(8, 0))
        ttk.Label(self, text=PRIVACY_NOTE, foreground=WARN_FG, wraplength=860, justify="left").pack(anchor="w")
        frame, self.result = make_text(self, height=10)
        frame.pack(fill="both", expand=True, pady=8)

    def on_show(self) -> None:
        sel = self.c.selected_rows()
        secrets = sum(1 for r in sel if r.secret)
        scan = self.c.state.scan
        rules = len(scan.matched) if scan else 0
        text = f"포함될 내용: 규칙 일치 프로그램 {rules}개의 설정 + 프로그램 목록 + 직접 선택한 규칙 없는 항목 {len(sel)}개."
        if secrets:
            text += f"\n⚠ 선택 항목 중 비밀 정보 가능성이 있는 것이 {secrets}개 포함됩니다. 패키지 파일을 안전하게 보관하세요."
        if not sel:
            text += "\n(규칙 없는 항목은 선택하지 않았으므로 포함되지 않습니다.)"
        self.plan.configure(text=text)

    def _browse(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title="설정 패키지 저장 위치", defaultextension=".zip",
                                         filetypes=[("설정 패키지", "*.zip")], initialfile="pcmigrate-settings.zip")
        if p:
            self.path_var.set(p)

    def _export(self) -> None:
        out = self.path_var.get().strip()
        if not out:
            messagebox.showwarning("저장 위치", "저장할 파일 위치를 먼저 선택하세요.", parent=self)
            return
        exists = "\n※ 같은 이름의 파일이 있으면 .bak 으로 보존합니다." if os.path.exists(out) else ""
        if not messagebox.askokcancel("패키지 만들기 확인", f"다음 위치에 설정 패키지를 만듭니다:\n{out}{exists}\n\n"
                                      "원본 파일은 읽기만 하고 수정하지 않습니다. 계속할까요?", parent=self):
            return
        self.guarded(self.c.start_export, out, write_confirmed=True)

    def on_event(self, kind, payload) -> None:
        if kind != "export_done":
            return
        m = payload.manifest
        lines = [f"패키지를 만들었습니다: {self.c.state.export_output}", "",
                 f"포함된 규칙: {', '.join(r['id'] for r in m['rules']) or '(없음)'}",
                 f"파일 {len(m['files'])}개, 건너뜀 {len(m['skipped'])}개, 실패 {len(m['failed'])}개"]
        if m["failed"]:
            lines.append("⚠ 일부 파일을 읽지 못했습니다(상세는 아래).")
            lines += [f"  - {f['src']}: {f['error']}" for f in m["failed"][:20]]
        lines += ["", "신 PC 에서 직접 해야 할 일(요약):"]
        for w in m.get("manual_warnings", []):
            for item in w.get("items", [])[:3]:
                lines.append(f"  - {w.get('name')}: {item}")
        lines += ["", LICENSE_NOTE]
        set_text(self.result, lines)
        self.open_btn.state(["!disabled"])

    def _open(self) -> None:
        open_location(self.c.state.export_output)
