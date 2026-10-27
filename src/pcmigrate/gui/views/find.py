"""내 파일 옮기기 1/4 — 옮길 폴더 고르기: 내 파일 자동 찾기 → 종류별 보기 → 폴더 표(체크) → 합계.

찾기는 읽기만 한다. 아무것도 자동으로 선택하지 않는다. 상태와 규칙은 모두 컨트롤러(files_controller.FilesMixin)에 있고
이 화면은 그 결과를 그리기만 한다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from .. import state as S
from .. import texts as T
from .common import (MUTED_FG, SUB_FONT, WARN_BG, WARN_FG, ActionButton, BaseView, Collapsible, ExplorerLink, ScrollBody, TreeTooltip, add_tooltip,
                     autowrap, bind_alt, clean_path, clear, make_tree)


class FindView(BaseView):
    name = S.FIND

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.START), next_=lambda: self.guarded(self.c.files_go_method), next_text=T.BTN_FD_NEXT)
        self.total_row = ttk.Frame(self)  # 하단 합계(이동 막대 바로 위) + '폴더 보기' 단추(창이 낮아도 항상 보인다)
        self.total_row.pack(side="bottom", fill="x", pady=(4, 0))
        self.total = ttk.Label(self.total_row, text=T.FD_TOTAL_NONE, font=SUB_FONT)
        self.total.pack(side="left")
        self.header(T.FD_HEADING, T.FD_SUB)
        self.add_status_card({T.TASK_FIND_DATA})
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        self.body = body

        # ---- ① 에서 만든 설정 묶음 함께 보내기(맨 위. 묶음이 있을 때만 보이고, 기본은 체크 해제)
        self.bundle_row = ttk.Frame(body)
        self.bundle_var = tk.BooleanVar(value=False)
        self.bundle_cb = ttk.Checkbutton(self.bundle_row, text="", variable=self.bundle_var, command=self._bundle_changed)
        self.bundle_cb.pack(anchor="w")
        self.bundle_note = ttk.Label(self.bundle_row, text=T.FD_BUNDLE_NOTE, justify="left", foreground=MUTED_FG)
        self.bundle_note.pack(anchor="w", fill="x", padx=(24, 0))
        autowrap(self.bundle_note, 40)

        # ---- 찾기 / 직접 추가
        top = ttk.Frame(body)
        self.top_row = top
        top.pack(fill="x", pady=(2, 4))
        self.find_btn = ActionButton(top, text=T.BTN_FD_FIND, command=lambda: self.guarded(self.c.find_data_start))
        self.find_btn.pack(side="left")
        self.add_btn = ActionButton(top, text=T.BTN_FD_ADD, kind="plain", command=self._add_folder)
        self.add_btn.pack(side="left", padx=(8, 0))
        self.primary = self.find_btn
        self.summary = ttk.Label(body, text="", justify="left", foreground=MUTED_FG)
        self.summary.pack(anchor="w", fill="x")
        autowrap(self.summary)
        self.notice = tk.Label(body, text="", anchor="w", justify="left", bg=WARN_BG, fg=WARN_FG, padx=10, pady=4)  # 시간 초과/읽기 오류/오류 안내
        autowrap(self.notice, 40)

        # ---- 종류별 보기
        flt = ttk.Frame(body)
        flt.pack(fill="x", pady=(2, 2))
        self.filter_row = flt
        ttk.Label(flt, text=T.FD_FILTER_LABEL).pack(side="left", padx=(0, 6))
        self.filter_vars: dict[str, tk.BooleanVar] = {}
        self.filter_btns: dict[str, ttk.Checkbutton] = {}
        for cat, label in T.FD_CATS:
            var = tk.BooleanVar(value=cat in self.c.files.filters)
            cb = ttk.Checkbutton(flt, text=label, variable=var, command=lambda c=cat, v=var: self.guarded(self.c.set_data_filter, c, v.get()))
            cb.pack(side="left", padx=(0, 10))
            self.filter_vars[cat], self.filter_btns[cat] = var, cb

        # ---- 폴더 표
        cols = [("sel", T.FD_COLS[0], 52), ("path", T.FD_COLS[1], 330), ("design", T.FD_COLS[2], 50), ("photo", T.FD_COLS[3], 50),
                ("video", T.FD_COLS[4], 50), ("doc", T.FD_COLS[5], 50), ("size", T.FD_COLS[6], 80), ("latest", T.FD_COLS[7], 100)]
        frame, self.tree = make_tree(body, cols, height=7)
        frame.pack(fill="x", pady=(2, 2))
        self.tree_frame = frame
        for cid in ("sel", "design", "photo", "video", "doc"):
            self.tree.column(cid, stretch=False)
        self.tree.column("sel", anchor="center")
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", self._key_toggle)
        self.tree.bind("<<TreeviewSelect>>", self._show_basis)
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.rows[iid].path if col == 1 and iid in self.rows else "")
        self.rows: dict[str, object] = {}
        self.empty = ttk.Label(body, text=T.FD_EMPTY, justify="left", foreground=MUTED_FG)
        self.empty.pack(anchor="w", fill="x")
        autowrap(self.empty)
        self.detail = ttk.Label(body, text=T.FD_ROW_HINT + " " + T.OPEN_HINT, justify="left", foreground=MUTED_FG)
        self.detail.pack(anchor="w", fill="x")
        autowrap(self.detail)

        # ---- 보이는 항목 전체 선택/해제
        bulk = ttk.Frame(body)
        bulk.pack(fill="x", pady=(4, 0))
        self.select_all_btn = ttk.Button(bulk, text=T.BTN_FD_SELECT_ALL, command=self._select_all)
        self.clear_all_btn = ttk.Button(bulk, text=T.BTN_FD_CLEAR_ALL, command=self._clear_all)
        self.select_all_btn.pack(side="left")
        self.clear_all_btn.pack(side="left", padx=6)
        # 탐색기에서 보기(폴더 줄 더블클릭은 체크 칸 제외, Enter, '폴더 보기'): 열거나 실행하지 않고 폴더만 보여 준다
        self.explorer = ExplorerLink(self, self.total_row)
        self.explorer.button.pack(side="right")
        self.explorer.attach(self.tree, lambda iid: (self.rows[iid].path, None) if iid in self.rows else None,
                             click=self._click, undo=self._undo_toggle)
        self._last_toggle: str | None = None
        self.bulk_msg = ttk.Label(bulk, text="", justify="left")
        self.bulk_msg.pack(side="left", padx=6)

        # ---- 정크 파일 제외
        self.junk_var = tk.BooleanVar(value=self.c.files.junk)
        self.junk_cb = ttk.Checkbutton(body, text=T.FD_JUNK, variable=self.junk_var, command=lambda: self.c.set_data_junk(self.junk_var.get()))
        self.junk_cb.pack(anchor="w", pady=(8, 0))
        junk_note = ttk.Label(body, text=T.FD_JUNK_NOTE, justify="left", foreground=MUTED_FG)
        junk_note.pack(anchor="w", fill="x", padx=(24, 0))
        autowrap(junk_note, 40)

        # ---- 제외 N곳 ▸ / 클라우드 전용 파일 ▸
        self.excluded = Collapsible(body, T.FD_EXCLUDED_TITLE.format(n=0), T.FD_EXCLUDED_TITLE_OPEN.format(n=0))
        self.cloud = Collapsible(body, T.FD_CLOUD_TITLE.format(n=0), T.FD_CLOUD_TITLE_OPEN.format(n=0))

        # ---- 기존 방식 / 새 PC 에서 받기
        self.links = ttk.Frame(body)
        self.links.pack(anchor="w", fill="x", pady=(10, 0))
        self.legacy_btn = ActionButton(self.links, text=T.FD_LEGACY_LINK, kind="link", command=lambda: self.guarded(self.c.files_legacy_transfer))
        self.legacy_btn.pack(anchor="w")
        add_tooltip(self.legacy_btn, T.TIP_FD_LEGACY)
        self.receive_btn = ActionButton(self.links, text=T.FD_RECEIVE_LINK, kind="link", command=lambda: self.guarded(self.c.files_receive_only))
        self.receive_btn.pack(anchor="w", pady=(2, 0))
        add_tooltip(self.receive_btn, T.FD_RECEIVE_NOTE)
        for b in (self.find_btn, self.add_btn):
            bind_alt(self.app, b, lambda: self.app.current == self.name)
        self.finish()
        self._rebuild()

    # ------------------------------------------------------------------ 표시
    def on_show(self) -> None:
        self._rebuild()
        self.scroll.scroll_to_top()
        self.find_btn.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "find_data_done":
            self.bulk_msg.configure(text="")
            self._rebuild()
        elif kind in ("files_changed", "cancelled", "busy"):
            self._rebuild()

    def _visible_keys(self) -> list[str]:
        return [r.path for r in self.rows.values()]

    def _rebuild(self) -> None:
        """표/합계/안내를 컨트롤러 상태에서 다시 그린다. 보이는 줄이 같으면 표시만 바꿔 스크롤 위치를 지킨다."""
        c, f = self.c, self.c.files
        rows = c.data_rows()
        same = [r.path for r in rows] == self._visible_keys()
        if same:
            for iid, r in zip(self.tree.get_children(), rows):
                self.rows[iid] = r
                self.tree.set(iid, "sel", T.CHECKED if r.selected else T.UNCHECKED)
        else:
            keep = [self.rows[i].path for i in self.tree.selection() if i in self.rows]
            clear(self.tree)
            self.rows = {}
            for r in rows:
                iid = self.tree.insert("", "end", values=(T.CHECKED if r.selected else T.UNCHECKED, T.ellipsize_path(r.path, 56),
                                                          r.count_text("design"), r.count_text("photo"), r.count_text("video"),
                                                          r.count_text("doc"), r.size_text, r.latest))
                self.rows[iid] = r
                if r.path in keep:
                    self.tree.selection_set(iid)
        res = f.result
        self.summary.configure(text=c.data_summary_text())
        self._notice(rows)
        self.total.configure(text=c.data_total_text())
        self._bundle_sync()
        n_sel = c.data_total()[0] or int(c.bundle_included())  # 설정 묶음만 보내도 된다
        self.next_btn.state(["!disabled"] if n_sel else ["disabled"])
        self.next_btn.emphasize(bool(n_sel))
        self.set_next_reason("" if n_sel else T.WHY_FD_NEED_SELECTION)
        busy = bool(c.state.busy)
        self.find_btn.configure(text=T.BTN_FD_AGAIN if res is not None else T.BTN_FD_FIND)
        self.find_btn.state(["disabled"] if busy else ["!disabled"])
        self.find_btn.emphasize(not n_sel)
        self.add_btn.state(["disabled"] if busy else ["!disabled"])
        for b in (self.select_all_btn, self.clear_all_btn):
            b.state(["disabled"] if busy or not rows else ["!disabled"])
        self.junk_var.set(f.junk)
        for cat, var in self.filter_vars.items():
            var.set(cat in f.filters)
        self._empty_text(rows)
        self._fold_sections()
        self.explorer.refresh()

    def _bundle_sync(self) -> None:
        """① 에서 만든 설정 묶음이 있을 때만 맨 위 체크 줄을 보인다(없으면 줄 자체가 안 보인다). 체크 상태는 사용자가 바꾼 대로만."""
        c = self.c
        label = c.bundle_label()
        if label:
            self.bundle_cb.configure(text=label)
            self.bundle_var.set(c.bundle_included())
            if not self.bundle_row.winfo_manager():
                self.bundle_row.pack(fill="x", pady=(2, 6), before=self.top_row)
            self.bundle_cb.state(["disabled"] if c.state.busy else ["!disabled"])
        elif self.bundle_row.winfo_manager():
            self.bundle_row.pack_forget()
            self.bundle_var.set(False)

    def _bundle_changed(self) -> None:
        self.guarded(self.c.set_bundle_included, self.bundle_var.get())
        self.bundle_var.set(self.c.bundle_included())  # 거부되면 원래대로

    def _empty_text(self, rows) -> None:
        f = self.c.files
        if rows:
            self.empty.pack_forget()
            if not self.tree_frame.winfo_manager():
                self.tree_frame.pack(fill="x", pady=(2, 2), after=self.filter_row)
            return
        self.tree_frame.pack_forget()  # 줄이 없으면 빈 표 대신 안내 글만(낮은 창에서 자리를 아낀다)
        if f.result is None:
            text = T.FD_EMPTY
        elif not (f.result.projects or f.result.user_folders) and not f.extra:
            text = T.FD_NONE_FOUND
        else:
            text = T.FD_FILTER_EMPTY
        self.empty.configure(text=text)
        self.empty.pack(anchor="w", fill="x", before=self.detail)

    def _notice(self, rows) -> None:
        f = self.c.files
        parts = []
        res = f.result
        if f.find_error:
            parts.append(f.find_error)
        elif f.find_cancelled and res is None:
            parts.append(T.FD_CANCELLED)
        if res is not None:
            if not res.complete:
                parts.append(T.FD_PARTIAL)
            if res.error_count:
                parts.append(T.FD_READ_ERRORS.format(n=res.error_count))
        if parts:
            self.notice.configure(text="\n".join(parts))
            self.notice.pack(fill="x", pady=(2, 2), after=self.summary)
        else:
            self.notice.pack_forget()

    def _fold_sections(self) -> None:
        """제외한 곳/클라우드 전용 파일 접이식을 결과에 맞게 채운다(없으면 숨긴다)."""
        c = self.c
        excl = c.data_excluded()
        n_ex = sum(n for _r, n, _e in excl)
        self._fill(self.excluded, n_ex, T.FD_EXCLUDED_TITLE, T.FD_EXCLUDED_TITLE_OPEN, self._excluded_lines(excl) if n_ex else [])
        cloud = c.data_cloud_rows()
        n_cloud = sum(files for _p, files, _b in cloud)
        lines = []
        if cloud:
            from ...progress import fmt_bytes
            lines = [T.FD_CLOUD_NOTE] + [T.FD_CLOUD_LINE.format(path=T.ellipsize_path(p, 60), n=files, size=fmt_bytes(b))
                                         for p, files, b in cloud]
        self._fill(self.cloud, n_cloud, T.FD_CLOUD_TITLE, T.FD_CLOUD_TITLE_OPEN, lines)
        for box in (self.excluded, self.cloud):
            box.pack_forget()
        if n_ex:
            self.excluded.pack(anchor="w", fill="x", pady=(8, 0), before=self.links)
        if n_cloud:
            self.cloud.pack(anchor="w", fill="x", pady=(4, 0), before=self.links)

    @staticmethod
    def _excluded_lines(excl) -> list[str]:
        lines = [T.FD_EXCLUDED_NOTE]
        for reason, n, examples in excl:
            lines.append(T.FD_EXCLUDED_LINE.format(reason=reason, n=n))
            lines += [T.FD_EXCLUDED_EXAMPLE.format(path=T.ellipsize_path(p, 70)) for p in examples]
        return lines

    @staticmethod
    def _fill(box: Collapsible, n: int, title: str, title_open: str, lines: list[str]) -> None:
        box.title, box.title_open = title.format(n=n), title_open.format(n=n)
        box.set_open(box.open)  # 제목 글자만 새로 고친다(펼침 상태는 그대로)
        pool: list[tk.Label] = box.__dict__.setdefault("_lines", [])  # 글줄 위젯은 지우지 않고 재사용(줄바꿈 처리기가 부모에 묶여 있어 지우면 오류가 난다)
        for i, line in enumerate(lines):
            if i >= len(pool):
                pool.append(box.add_text(line))
            elif str(pool[i].cget("text")) != line:
                pool[i].configure(text=line)
            if not pool[i].winfo_manager():
                pool[i].pack(anchor="w", fill="x", pady=2)
        for extra in pool[len(lines):]:
            extra.pack_forget()

    # ------------------------------------------------------------------ 동작
    def _add_folder(self) -> None:
        p = filedialog.askdirectory(parent=self, title=T.FD_ADD_TITLE, mustexist=True)
        if p:
            self.guarded(self.c.add_data_folder, clean_path(p.replace("/", "\\")))

    def _toggle(self, iid: str) -> None:
        r = self.rows.get(iid)
        if r is not None:
            was = r.selected
            self.guarded(self.c.set_data_selected, r.path, not r.selected)
            self._last_toggle = r.path if self.rows.get(iid) is not None and self.rows[iid].selected != was else None

    def _undo_toggle(self, iid: str) -> None:
        """줄 더블클릭으로 탐색기를 열 때, 첫 클릭이 바꾼 체크를 원래대로 되돌린다."""
        r = self.rows.get(iid)
        if r is not None and self._last_toggle == r.path:
            self._last_toggle = None
            self.guarded(self.c.set_data_selected, r.path, not r.selected)

    def _click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    def _key_toggle(self, _e=None):
        sel = self.tree.selection()
        if sel:
            self._toggle(sel[0])
        return "break"

    def _show_basis(self, _e=None) -> None:
        sel = self.tree.selection()
        r = self.rows.get(sel[0]) if sel else None
        self.detail.configure(text=(T.FD_BASIS.format(text=r.basis) if r is not None and r.basis else T.FD_ROW_HINT + " " + T.OPEN_HINT))

    def _select_all(self) -> None:
        n = self.c.select_visible_data(True)
        self.bulk_msg.configure(text=T.FD_BULK_SELECTED.format(n=n))

    def _clear_all(self) -> None:
        n = self.c.select_visible_data(False)
        self.bulk_msg.configure(text=T.FD_BULK_CLEARED.format(n=n))
