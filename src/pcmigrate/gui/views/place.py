"""받은 파일 원래 위치에 놓기 화면(stage50): 받은 폴더 입력 -> 미리보기 -> 놓기 -> 결과. 큰 파일 옮기기(TransferView)와 같은 방식이다.

위에서 아래로 '받은 폴더 입력칸 → 고급 옵션(접이식) → 결과'가 보이고, 주 버튼(미리보기/놓기)은 스크롤 밖 하단 고정 막대에 있다.
'놓기'는 같은 입력·옵션으로 미리보기를 마친 뒤에만 켜지고, 받은 폴더에서 지울 파일이 있으면 확인 체크와 확인창을 거친다.
화면의 숫자와 문장은 컨트롤러(place_result 등)와 place_flow 의 순수 함수가 만든 것을 그리기만 한다.
"""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from .common import (BODY_FONT, MUTED_FG, SUB_FONT, TITLE_FONT, WARN_BG, WARN_FG, ActionButton, BaseView, Collapsible, ExplorerLink,
                     FoldLabel, FoldTLabel, Notice, PathField, ScrollBody, StepStrip, TreeTooltip, autowrap, bind_alt, clean_path, clear,
                     make_tree, set_shown, DANGER_FG, OK_FG)
from ..place_flow import MAX_TABLE_ROWS, size_text

__all__ = ["PlaceView"]

_ALL_LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
_TABLES = {  # 표 이름 -> (접이식 제목, 열 이름, 열 너비, 줄 키)
    "overwrite": (T.PL_COLS_OVER, (320, 150, 150, 320), ("expected", "recv", "dest", "note")),
    "manual": (T.PL_COLS_MANUAL, (170, 280, 280, 420), ("kind", "expected", "current", "reason")),
    "gathered": (T.PL_COLS_GATHER, (110, 300, 300, 420), ("kind", "expected", "current", "reason")),
}


class PlaceView(BaseView):
    name = S.PLACE

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(self.c.place_back_screen()))
        self._build_action_bar()
        self.header(T.PL_HEADING, T.PL_SUB)
        self.add_status_card({T.TASK_PLACE, T.TASK_PLACE_PREVIEW})
        self.steps_strip = StepStrip(self, T.PL_STEP_NAMES, label=T.PL_STEPS_LABEL)  # 받은 폴더 → 미리보기 → 놓기 → 결과
        self.steps_strip.pack(fill="x", pady=(0, 6))
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        self.scroll = scroll
        self._loading = False  # 화면이 값을 채우는 동안에는 상태로 되돌려 쓰지 않는다
        self._last_drives: list[str] = []  # 받은 폴더 맨 위의 드라이브 폴더(드라이브 바꾸기의 왼쪽 목록)
        self._default_report = ""  # 화면이 결과 리포트 칸에 채운 기본값(이 값 그대로면 상태에는 '비어 있음'으로 알린다)
        self._change_job = None  # 글자 입력 뒤 점검을 미루는 after 작업(키 입력마다 폴더를 읽지 않게)
        self._found_gen = 0  # 받은 폴더 찾기(백그라운드) 요청 번호
        self._shown_stale = False  # 결과 영역이 '이전 결과' 표시로 그려져 있는가

        # ---- 1) 받은 폴더
        self.recv_var, self.rep_var = tk.StringVar(), tk.StringVar()
        self.recv_field = PathField(body, self.recv_var, self._pick_recv, label=T.PL_RECV_LABEL, hint=T.PL_RECV_HINT,
                                    drives=self.c.list_drives, on_drive=lambda root: self.recv_var.set(root))
        self.recv_field.pack(fill="x", pady=(2, 0))
        self.recv_entry = self.recv_field.entry
        self.found_row = ttk.Frame(body)  # ③ 첫 화면의 '이 PC에서 찾은 설정 묶음' 과 같은 방식
        ttk.Label(self.found_row, text=T.PL_FOUND_LABEL).pack(side="left")
        self.found_var = tk.StringVar(value="")
        self.found_box = ttk.Combobox(self.found_row, textvariable=self.found_var, state="readonly", width=60, values=[])
        self.found_box.pack(side="left", fill="x", expand=True, padx=6)
        self.found_box.bind("<<ComboboxSelected>>", self._found_picked)
        self.found_entries: list[tuple[str, float, int, str]] = []
        self.check_label = FoldLabel(body, text="", anchor="w", justify="left", padx=2)  # 입력 확인 결과(✔/✖ 줄)
        self.check_label.pack(fill="x", pady=4)
        autowrap(self.check_label, 40)
        self.warn_label = FoldLabel(body, text="", anchor="w", justify="left", padx=10, pady=6, bg=WARN_BG, fg=WARN_FG)  # 주황 안내(OneDrive 등)
        autowrap(self.warn_label, 40)
        self.recv_var.trace_add("write", lambda *a: self._changed())
        self.rep_var.trace_add("write", lambda *a: self._changed())

        # ---- 2) 고급 옵션(접이식)
        self.opts = Collapsible(body, T.PL_OPTIONS_TOGGLE, T.PL_OPTIONS_TOGGLE_OPEN)
        self.opts.pack(anchor="w", fill="x")
        self.opts_toggle = self.opts.toggle_btn
        ob = self.opts.body
        self.map_box = ttk.LabelFrame(ob, text=T.PL_MAP_TITLE, padding=(8, 4))
        self.map_box.pack(fill="x", pady=(0, 6))
        self.map_rows_frame = ttk.Frame(self.map_box)
        self.map_rows_frame.pack(fill="x")
        self.map_rows: list[dict] = []
        mrow = ttk.Frame(self.map_box)
        mrow.pack(fill="x", pady=(4, 0))
        self.map_add_btn = ttk.Button(mrow, text=T.BTN_PL_MAP_ADD, command=lambda: self._add_map_row())
        self.map_add_btn.pack(side="left")
        mnote = ttk.Label(mrow, text=T.PL_MAP_NOTE, justify="left", foreground=MUTED_FG)
        mnote.pack(side="left", padx=10, fill="x", expand=True)
        autowrap(mnote, 160)
        self.keep_var, self.over_var, self.unv_var, self.gather_var_on = (tk.BooleanVar(value=False) for _ in range(4))
        self.gather_dir_var = tk.StringVar()
        self._check_with_note(ob, self.keep_var, T.PL_KEEP, T.PL_KEEP_NOTE, MUTED_FG)
        self._check_with_note(ob, self.over_var, T.PL_NO_OVERWRITE, T.PL_NO_OVERWRITE_NOTE, MUTED_FG, pady=6)
        self._check_with_note(ob, self.unv_var, T.PL_UNVERIFIED, T.PL_UNVERIFIED_NOTE, WARN_FG, pady=6)
        self._check_with_note(ob, self.gather_var_on, T.PL_GATHER, T.PL_GATHER_NOTE, MUTED_FG, pady=6)
        self.gather_frame = ttk.Frame(ob)  # 모으기를 켰을 때만 보인다
        self.gather_field = PathField(self.gather_frame, self.gather_dir_var, self._pick_gather, label=T.PL_GATHER_LABEL, hint="")
        self.gather_field.pack(fill="x", padx=(24, 0))
        self.gather_frame.pack(fill="x", pady=(2, 0))
        self.gather_other_btn = ActionButton(self.gather_frame, text=T.BTN_PL_GATHER_OTHER, kind="link", command=self._pick_gather)  # OneDrive 일 때만
        self.gather_other_btn.pack(anchor="w", padx=(24, 0))
        self.gather_dir_var.trace_add("write", lambda *a: self._changed())
        self.rep_field = PathField(ob, self.rep_var, self._pick_report, label=T.PL_REPORT_LABEL, hint=T.PL_REPORT_HINT)
        self.rep_field.pack(fill="x", pady=(8, 0))
        for var in (self.keep_var, self.over_var, self.unv_var, self.gather_var_on):  # 옵션을 바꾸면 이전 미리보기는 무효가 된다
            var.trace_add("write", lambda *a: self._options_changed())
        self._add_map_row()

        # ---- 3) 결과 영역
        self.res = ttk.Frame(body)
        self.res.pack(fill="x", pady=(6, 0))
        res = self.res
        self.empty_note = FoldTLabel(res, text=T.PL_RESULT_EMPTY, justify="left", foreground=MUTED_FG)
        self.empty_note.pack(anchor="w", fill="x", pady=2)
        autowrap(self.empty_note, 40)
        self.head_label = FoldTLabel(res, text="", font=SUB_FONT, justify="left")
        self.head_label.pack(anchor="w", fill="x", pady=(2, 2))
        autowrap(self.head_label, 40)
        self.summary = Notice(res, "", "ok")
        self.summary.pack(fill="x", pady=2)
        self.stopped_box = Notice(res, "", "danger")
        self.stopped_box.pack(fill="x", pady=2)
        self.delete_box = tk.Frame(res, bg=WARN_BG, highlightbackground=WARN_FG, highlightthickness=1, padx=12, pady=8)  # 삭제 예정(큰 글씨)
        self.delete_title = tk.Label(self.delete_box, text=T.PL_DELETE_TITLE, bg=WARN_BG, fg=WARN_FG, font=SUB_FONT, anchor="w")
        self.delete_title.pack(anchor="w")
        self.delete_big = tk.Label(self.delete_box, text="", bg=WARN_BG, fg=WARN_FG, font=TITLE_FONT, anchor="w")
        self.delete_big.pack(anchor="w")
        self.delete_note = FoldLabel(self.delete_box, text=T.PL_DELETE_NOTE, bg=WARN_BG, fg=WARN_FG, font=BODY_FONT, anchor="w", justify="left")
        self.delete_note.pack(anchor="w", fill="x")
        autowrap(self.delete_note, 40)
        self.delete_box.pack(fill="x", pady=4)
        self.ack_var = tk.BooleanVar(value=False)
        self.ack_check = ttk.Checkbutton(res, variable=self.ack_var, command=self._ack_changed, text=T.PL_ACK)
        self.ack_check.pack(anchor="w", pady=4)
        self.notes_label = FoldTLabel(res, text="", justify="left")
        self.notes_label.pack(anchor="w", fill="x", pady=2)
        autowrap(self.notes_label, 40)
        self.save_label = FoldTLabel(res, text="", justify="left", foreground=MUTED_FG)
        self.save_label.pack(anchor="w", fill="x", pady=2)
        autowrap(self.save_label, 40)
        self.folds: dict[str, Collapsible] = {}
        self.trees: dict[str, ttk.Treeview] = {}
        self.more_labels: dict[str, FoldTLabel] = {}
        self.paths: dict[str, dict[str, tuple[str, bool]]] = {}
        self.full: dict[str, dict[str, tuple]] = {}
        self.tips: dict[str, TreeTooltip] = {}
        titles = {"overwrite": (T.PL_FOLD_OVERWRITE, T.PL_FOLD_OVERWRITE_OPEN), "manual": (T.PL_FOLD_MANUAL, T.PL_FOLD_MANUAL_OPEN),
                  "gathered": (T.PL_FOLD_GATHERED, T.PL_FOLD_GATHERED_OPEN)}
        self.link_row = ttk.Frame(res)  # 표의 줄을 탐색기로 보기(보기만 한다)
        self.explorer = ExplorerLink(self, self.link_row, text=T.BTN_PL_OPEN_ROW, note_pack={"side": "left", "padx": (8, 0)})
        for key in ("overwrite", "manual", "gathered"):
            cols, widths, _keys = _TABLES[key]
            fold = Collapsible(res, *titles[key])
            fold.pack(anchor="w", fill="x", pady=(4, 0))
            frame, tree = make_tree(fold.body, [(f"c{i}", name, w) for i, (name, w) in enumerate(zip(cols, widths))], height=5)
            frame.pack(fill="x")
            more = FoldTLabel(fold.body, text="", justify="left", foreground=MUTED_FG)
            more.pack(anchor="w", fill="x")
            autowrap(more, 40)
            self.folds[key], self.trees[key], self.more_labels[key] = fold, tree, more
            self.paths[key], self.full[key] = {}, {}
            self.tips[key] = TreeTooltip(tree, lambda iid, col, k=key: (self.full[k].get(iid) or ("",) * 4)[col] if col < 4 else "")
            self.explorer.attach(tree, lambda iid, k=key: self.paths[k].get(iid), check_col=None)
        self.todo_fold = Collapsible(res, T.PL_FOLD_TODO, T.PL_FOLD_TODO_OPEN)
        self.todo_fold.pack(anchor="w", fill="x", pady=(4, 0))
        self.todo_label = FoldTLabel(self.todo_fold.body, text="", justify="left")
        self.todo_label.pack(anchor="w", fill="x")
        autowrap(self.todo_label, 40)
        self.row_hint = FoldTLabel(res, text=T.PL_ROW_HINT, justify="left", foreground=MUTED_FG)
        self.row_hint.pack(anchor="w", fill="x", pady=(4, 0))
        autowrap(self.row_hint, 40)
        self.link_row.pack(anchor="w", fill="x", pady=(2, 0))
        self.explorer.button.pack(side="left")
        actions = ttk.Frame(res)
        actions.pack(anchor="w", pady=(6, 0))
        self.open_recv_btn = ttk.Button(actions, text=T.BTN_PL_OPEN_RECV, command=self._open_recv)
        self.open_recv_btn.pack(side="left")
        self.open_report_btn = ttk.Button(actions, text=T.BTN_PL_OPEN_REPORT, command=self._open_report)
        self.open_report_btn.pack(side="left", padx=8)
        self.finish()
        for w in (self.gather_frame, self.gather_other_btn, self.delete_box, self.ack_check, self.link_row, self.row_hint):
            set_shown(w, False)
        for fold in (*self.folds.values(), self.todo_fold):
            set_shown(fold, False)
        self._update_buttons()

    # ------------------------------------------------------------------ 하단 고정 막대(주 버튼)
    def _build_action_bar(self) -> None:
        br = self.bar_right
        assert br is not None
        self.go_btn = ActionButton(br, text=T.BTN_PL_GO, command=self._go, kind="warn")  # 주의색: 받은 폴더의 파일을 옮기거나 지우는 실행
        self.preview_btn = ActionButton(br, text=T.BTN_PL_PREVIEW, command=self._preview)
        self.reason_col = ttk.Frame(br)  # 지금 누를 수 없는 이유(버튼 왼쪽)
        self.preview_reason = ttk.Label(self.reason_col, text="", foreground=WARN_FG, justify="right", wraplength=300)
        self.go_reason = ttk.Label(self.reason_col, text="", foreground=WARN_FG, justify="right", wraplength=300)
        self.primary = self.preview_btn
        self.go_btn.state(["disabled"])
        self.go_btn.pack(side="right")
        self.preview_btn.pack(side="right", padx=(0, 8))
        self.reason_col.pack(side="right", padx=10)
        bind_alt(self.app, self.preview_btn, lambda: self.app.current == self.name)

    def _pack_reasons(self) -> None:
        for w in (self.preview_reason, self.go_reason):
            w.pack_forget()
        pv, go = self.preview_reason.cget("text"), self.go_reason.cget("text")
        if pv:
            self.preview_reason.pack(anchor="e")
        if go and go != pv:  # 같은 말이면 한 번만
            self.go_reason.pack(anchor="e")

    # ------------------------------------------------------------------ 구성 도우미
    def _check_with_note(self, parent, var, text, note, color, pady=0) -> None:
        ttk.Checkbutton(parent, variable=var, text=text).pack(anchor="w", pady=(pady, 0))
        n = ttk.Label(parent, text=note, justify="left", foreground=color)
        n.pack(anchor="w", fill="x", padx=(24, 0))
        autowrap(n, 40)

    def _pick_recv(self) -> None:
        p = filedialog.askdirectory(parent=self, title=T.PL_PICK_TITLE, mustexist=False)
        if p:
            self.recv_var.set(p.replace("/", "\\"))

    def _pick_gather(self) -> None:
        p = filedialog.askdirectory(parent=self, title=T.PL_GATHER_PICK_TITLE, mustexist=False)
        if p:
            self.gather_dir_var.set(p.replace("/", "\\"))

    def _pick_report(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.PL_REPORT_PICK_TITLE, defaultextension=".json", filetypes=[("JSON", "*.json")],
                                         initialfile=os.path.basename(self.c.place_default_report()))
        if p:
            self.rep_var.set(p.replace("/", "\\"))

    def _found_picked(self, _e=None) -> None:
        idx = self.found_box.current()
        if 0 <= idx < len(self.found_entries):
            self.recv_var.set(self.found_entries[idx][0])

    def _scan_found(self) -> None:
        """받은 폴더 자동 찾기는 백그라운드 스레드에서 읽고, 화면(위젯)은 UI 스레드가 after 로 가져다 그린다."""
        self._found_gen += 1
        gen, box = self._found_gen, {"r": None}

        def work() -> None:
            try:
                box["r"] = self.c.place_choices()
            except Exception:  # noqa: BLE001 - place_choices 가 이미 오류 로그를 남기고 빈 목록을 돌려준다. 만약을 위해 화면만 계속 쓰게 한다
                box["r"] = []
        t = threading.Thread(target=work, daemon=True, name="pcmigrate-place-find")
        t.start()

        def poll() -> None:
            if gen != self._found_gen:  # 더 새 요청이 있다
                return
            if t.is_alive():
                self.after(80, poll)
            else:
                self._apply_found(box["r"] or [])
        self.after(40, poll)

    def _apply_found(self, entries: list) -> None:
        self.found_entries = entries
        labels = [T.PL_FOUND_ITEM.format(when=time.strftime("%Y-%m-%d %H:%M", time.localtime(m)), where=where,
                                         folder=T.ellipsize_path(p, 50)) for p, m, _size, where in self.found_entries]
        self.found_box.configure(values=labels)
        self.found_var.set("")
        if self.found_entries:
            if not self.found_row.winfo_manager():
                self.found_row.pack(fill="x", pady=(8, 0), after=self.recv_field)
        else:
            self.found_row.pack_forget()

    # ------------------------------------------------------------------ 드라이브 바꾸기 줄
    def _src_letters(self) -> list[str]:
        return list(self._last_drives) or _ALL_LETTERS

    def _dst_letters(self) -> list[str]:
        try:
            letters = sorted({d.root[0].upper() for d in self.c.list_drives() if d.root})
        except Exception:  # noqa: BLE001 - 드라이브 조회 실패는 목록만 줄인다
            letters = []
        return letters or _ALL_LETTERS

    def _add_map_row(self, src: str = "", dst: str = "") -> None:
        row = ttk.Frame(self.map_rows_frame)
        row.pack(fill="x", pady=1)
        sv, dv = tk.StringVar(value=src), tk.StringVar(value=dst)
        ttk.Label(row, text=T.PL_MAP_LINE).pack(side="left")
        sb = ttk.Combobox(row, textvariable=sv, state="readonly", width=4, values=self._src_letters(),
                          postcommand=lambda: sb.configure(values=self._src_letters()))
        sb.pack(side="left", padx=4)
        ttk.Label(row, text=T.PL_MAP_TO).pack(side="left")
        db = ttk.Combobox(row, textvariable=dv, state="readonly", width=4, values=self._dst_letters(),
                          postcommand=lambda: db.configure(values=self._dst_letters()))
        db.pack(side="left", padx=4)
        ttk.Label(row, text=T.PL_MAP_END).pack(side="left")
        entry = {"frame": row, "src": sv, "dst": dv}
        ttk.Button(row, text=T.BTN_PL_MAP_DEL, command=lambda e=entry: self._del_map_row(e)).pack(side="left", padx=10)
        sv.trace_add("write", lambda *a: self._changed())
        dv.trace_add("write", lambda *a: self._changed())
        self.map_rows.append(entry)

    def _del_map_row(self, entry: dict) -> None:
        if len(self.map_rows) <= 1:  # 마지막 한 줄은 지우지 않고 비운다
            entry["src"].set("")
            entry["dst"].set("")
            return
        self.map_rows.remove(entry)
        entry["frame"].destroy()
        self._changed()

    def _map_pairs(self) -> list[tuple[str, str]]:
        return [(r["src"].get(), r["dst"].get()) for r in self.map_rows]

    # ------------------------------------------------------------------ 상태 동기화
    def _sync_state(self) -> None:
        if self._loading:
            return
        report = clean_path(self.rep_var.get())
        if report == self._default_report:  # 화면이 채운 기본값은 상태에 저장하지 않는다(놓을 때마다 컨트롤러가 새로 정한다)
            report = ""
        self.c.place_set(recv=clean_path(self.recv_var.get()), maps=self._map_pairs(), keep=self.keep_var.get(), no_overwrite=self.over_var.get(),
                         include_unverified=self.unv_var.get(), gather_on=self.gather_var_on.get(), gather_dir=clean_path(self.gather_dir_var.get()),
                         report_path=report)

    def _options_changed(self) -> None:
        self._changed_now()

    def _changed(self) -> None:
        """글자 입력(받은 폴더, 모을 폴더, 리포트 위치, 드라이브 선택)은 잠깐 모아서 한 번만 점검한다."""
        if self._loading:
            return
        if self._change_job is not None:
            self.after_cancel(self._change_job)
        self._change_job = self.after(250, self._changed_now)

    def _changed_now(self) -> None:
        if self._change_job is not None:
            self.after_cancel(self._change_job)
            self._change_job = None
        if self._loading:
            return
        self._sync_state()
        self._check()

    def _flush(self) -> None:
        """미뤄 둔 입력 반영이 있으면 지금 한다(버튼을 누르기 직전, 시험)."""
        if self._change_job is not None:
            self._changed_now()

    def _refresh_default_report(self) -> None:
        """결과 리포트 칸이 비어 있거나 기본값 그대로면 컨트롤러의 새 기본값으로 바꾼다(놓기를 마치면 새 이름이 된다)."""
        new = self.c.place_default_report()
        if self.rep_var.get() in ("", self._default_report) and new != self._default_report:
            self._default_report = new
            self._loading = True
            try:
                self.rep_var.set(new)
            finally:
                self._loading = False

    def _check(self):
        s = self.c.place_state
        res = self.c.place_check_state()
        self._last_drives = res.drives
        touched = bool(s.recv)
        if not touched:
            self.check_label.configure(text="")
        else:
            body = "\n".join((T.ICON_OK if ok else T.ICON_FAIL) + " " + t for ok, t in res.lines)
            self.check_label.configure(text=body, fg=OK_FG if res.ok else DANGER_FG)
        if res.warnings:
            self.warn_label.configure(text="\n".join(res.warnings))
            self.warn_label.pack(fill="x", pady=2, after=self.check_label)
        else:
            self.warn_label.pack_forget()
        on = bool(self.gather_var_on.get())
        set_shown(self.gather_frame, on)
        if on:
            self.gather_field.set_hint(T.PL_GATHER_DEFAULT_HINT.format(path=self.c.place_gather_default()))
        set_shown(self.gather_other_btn, on and res.gather_onedrive)  # OneDrive 와 연결된 폴더일 때만 '다른 폴더 고르기'
        self._update_buttons()
        return res

    def _update_buttons(self) -> None:
        pv, go = self.c.place_reasons()
        self.preview_btn.state(["disabled"] if pv else ["!disabled"])
        self.go_btn.state(["disabled"] if go else ["!disabled"])
        self.preview_reason.configure(text=pv)
        self.go_reason.configure(text=go)
        self.go_btn.emphasize(not go)  # 미리보기를 마치면 '놓기'가, 그 전에는 '미리보기'가 강조된다
        self.preview_btn.emphasize(bool(go))
        self._pack_reasons()
        need = self.c.place_ack_needed()
        set_shown(self.ack_check, need)
        self.ack_var.set(bool(self.c.place_state.ack and need))
        names, current = self.c.place_step()
        self.steps_strip.set_names(names)
        self.steps_strip.set(current)

    def on_show(self) -> None:
        s = self.c.place_state
        self.c.place_recheck()  # 화면을 열 때는 폴더 상태를 새로 읽는다
        if self._change_job is not None:
            self.after_cancel(self._change_job)
            self._change_job = None
        self._loading = True
        try:
            self.recv_var.set(s.recv)
            self._default_report = self.c.place_default_report()
            self.rep_var.set(s.report_path or self._default_report)
            self.keep_var.set(s.keep)
            self.over_var.set(s.no_overwrite)
            self.unv_var.set(s.include_unverified)
            self.gather_var_on.set(s.gather_on)
            self.gather_dir_var.set(s.gather_dir)
            while len(self.map_rows) > 1:
                self.map_rows.pop()["frame"].destroy()
            pairs = s.maps or [("", "")]
            self.map_rows[0]["src"].set(pairs[0][0])
            self.map_rows[0]["dst"].set(pairs[0][1])
            for a, b in pairs[1:]:
                self._add_map_row(a, b)
        finally:
            self._loading = False
        self._scan_found()
        self._check()
        self._render_result()
        self.after_idle(self.scroll.scroll_to_top)
        self.recv_entry.focus_set()

    # ------------------------------------------------------------------ 실행
    def _preview(self) -> None:
        self._flush()
        self._sync_state()
        self.guarded(self.c.start_place, apply=False)

    def _go(self) -> None:
        self._flush()
        self._sync_state()
        msg = self.c.place_confirm_message()
        if not messagebox.askyesno(T.PL_CONFIRM_TITLE, msg, icon="warning", parent=self):
            self.ack_var.set(False)  # 아니요: 아무 일도 없고 확인 체크는 풀린다
            self.c.place_set_ack(False)
            return
        self.guarded(self.c.start_place, apply=True, write_confirmed=True)

    def _ack_changed(self) -> None:
        self.c.place_set_ack(self.ack_var.get())

    def _open_recv(self) -> None:
        recv = self.c.place_state.recv
        if recv and self.c.can_show_in_explorer(recv, False):
            self.c.show_in_explorer(recv, False)

    def _open_report(self) -> None:
        path = self.c.place_state.saved_path
        if path and self.c.can_show_in_explorer(path, True):
            self.c.show_in_explorer(path, True)

    def on_event(self, kind, payload) -> None:
        if kind in ("busy", "place_changed"):
            self._update_buttons()
            if kind == "place_changed" and self.c.place_state.report_stale != self._shown_stale:
                self._render_result()  # 받은 폴더/옵션을 바꿨다: 화면의 결과를 '이전 결과' 로 다시 그린다
        if kind == "place_done":
            self._refresh_default_report()
            self._render_result()
            self._update_buttons()

    # ------------------------------------------------------------------ 결과 표시
    def _fill(self, key: str, rows: list[dict], preview: bool) -> None:
        tree, fold = self.trees[key], self.folds[key]
        _cols, _w, keys = _TABLES[key]
        clear(tree)
        self.paths[key], self.full[key] = {}, {}
        for r in rows[:MAX_TABLE_ROWS]:
            full = tuple(str(r.get(k, "")) for k in keys)
            iid = tree.insert("", "end", values=tuple(T.ellipsize_path(v, 70) if i in (1, 2) else v for i, v in enumerate(full)))
            self.full[key][iid] = full
            if r.get("path"):
                self.paths[key][iid] = (r["path"], bool(r.get("select")))
        tree.configure(height=max(2, min(8, len(rows))))
        more = len(rows) - MAX_TABLE_ROWS
        more_text = T.PL_TABLE_MORE_PREVIEW if preview else T.PL_TABLE_MORE  # 미리보기는 리포트 파일을 저장하지 않는다
        self.more_labels[key].configure(text=more_text.format(shown=MAX_TABLE_ROWS) if more > 0 else "")
        set_shown(fold, bool(rows))
        if rows:
            names = {"overwrite": (T.PL_FOLD_OVERWRITE, T.PL_FOLD_OVERWRITE_OPEN) if preview else (T.PL_FOLD_OVERWRITTEN, T.PL_FOLD_OVERWRITTEN_OPEN),
                     "manual": (T.PL_FOLD_MANUAL, T.PL_FOLD_MANUAL_OPEN), "gathered": (T.PL_FOLD_GATHERED, T.PL_FOLD_GATHERED_OPEN)}[key]
            fold.title, fold.title_open = (x.format(n=len(rows)) for x in names)
            fold.toggle_btn.configure(text=fold.title_open if fold.open else fold.title)

    def _render_result(self) -> None:
        r = self.c.place_result()
        self._shown_stale = bool(r.get("stale"))
        has = bool(r.get("sm"))
        note = r.get("stopped_note", "")
        self.empty_note.configure(text="" if (has or note) else T.PL_RESULT_EMPTY)
        if not has:  # 결과가 없다(아직 미리보기 전이거나, 결과 없이 강제로 멈춘 경우)
            self.head_label.configure(text="")
            self.summary.clear()
            self.notes_label.configure(text="")
            self.save_label.configure(text="")
            if note:
                self.stopped_box.set(note, "danger")
            else:
                self.stopped_box.clear()
            for key in self.folds:
                self._fill(key, [], True)
            for w in (self.todo_fold, self.delete_box, self.link_row, self.row_hint):
                set_shown(w, False)
            self.open_report_btn.state(["disabled"])
            self.open_recv_btn.state(["!disabled"] if self.c.place_state.recv else ["disabled"])
            return
        sm = r["sm"]
        self.head_label.configure(text=(T.PL_STALE_HEAD + "\n" if r.get("stale") else "") + r["head"])  # 받은 폴더/옵션을 바꾼 뒤에는 '이전 결과' 표시
        self.summary.set(r["line"], r["level"])
        lines = [x for x in (note, T.PL_STOPPED_ERROR_LOG if sm["interrupted"] and sm["error_log"] else "") if x]
        if lines:
            self.stopped_box.set("\n".join(lines), "danger")
        else:
            self.stopped_box.clear()
        if r["preview"] and sm["delete_n"] and not sm["keep"] and not r.get("stale"):  # 큰 숫자 한 줄: 받은 폴더에서 지울 파일 N개 / 용량
            self.delete_big.configure(text=T.PL_DELETE_BIG.format(n=sm["delete_n"], size=size_text(sm["delete_bytes"])))
            set_shown(self.delete_box, True)
        else:
            set_shown(self.delete_box, False)
        self.notes_label.configure(text="\n".join(r["notes"]))
        saved = T.PL_SAVED.format(path=r["saved_path"]) if r["saved_path"] else ""
        self.save_label.configure(text="\n".join(x for x in (saved, r["save_note"]) if x))
        rows = r["rows"]
        for key in ("overwrite", "manual", "gathered"):
            self._fill(key, rows[key], bool(r["preview"]))
        todo = r["todo"]
        self.todo_label.configure(text="\n".join(t if t.startswith("  ") else f"· {t}" for t in todo))
        set_shown(self.todo_fold, bool(todo))
        any_rows = any(rows.values())
        set_shown(self.link_row, any_rows)
        set_shown(self.row_hint, any_rows)
        self.explorer.refresh()
        self.open_report_btn.state(["!disabled"] if r["saved_path"] else ["disabled"])
        self.open_recv_btn.state(["!disabled"] if self.c.place_state.recv else ["disabled"])
        self.after_idle(lambda: self.scroll.scroll_to(self.head_label))
