"""구 PC 흐름: 조사 -> 자동 목록에 없는 프로그램 확인 -> 해야 할 일 목록 -> 설정 묶음 파일 만들기."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from ...api import unreadable_lines
from .common import (BOLD_FONT, DANGER_BG, DANGER_FG, INFO_BG, INFO_BORDER, INFO_FG, LIST_BG, MUTED_FG, OK_FG, SUB_FONT, WARN_FG,
                     ActionButton, BaseView, ExplorerLink, add_tooltip, bind_alt, CoverageBox,
                     Collapsible, PathField, ResultSummary, ScrollBody, TreeTooltip, autowrap, clear, clean_path, make_text,
                     make_tree, open_location, set_text, sort_tree, table_hint)
from .retry_panel import RetryPanel


class ScanView(BaseView):
    name = S.OLD_SCAN

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.START), next_=lambda: self.c.go(S.OLD_DISCOVER))
        self.btn = self.add_action(T.BTN_SCAN, lambda: self.guarded(self.c.start_scan), padx=10)  # 하단 고정 막대(스크롤 밖)
        self.primary = self.btn
        self.header(T.SCAN_HEADING, T.SCAN_SUB)
        self.add_status_card({T.TASK_SCAN})
        self.summary = ttk.Label(self, text=T.SCAN_IDLE, font=SUB_FONT)
        self.summary.pack(anchor="w", pady=(0, 4))
        ttk.Label(self, text=T.SCAN_TABLE_TITLE).pack(anchor="w", pady=(10, 2))
        frame, self.tree = make_tree(self, [("rule", T.COL_PROGRAM_RULE, 260), ("evidence", T.COL_EVIDENCE, 560)], height=12)
        frame.pack(fill="both", expand=True)
        self.tips = TreeTooltip(self.tree, lambda iid, col: self.tree.set(iid, self.tree["columns"][col]))
        self.set_next_reason(T.WHY_SCAN_FIRST)
        self.next_btn.state(["disabled"])
        self.next_btn.emphasize(False)
        self.finish()

    def on_show(self) -> None:
        done = bool(self.c.state.scan)
        self.next_btn.state(["!disabled"] if done else ["disabled"])
        self.set_next_reason("" if done else T.WHY_SCAN_FIRST)
        self.btn.emphasize(not done)
        self.next_btn.emphasize(done)
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
        self.btn.emphasize(False)
        self.next_btn.emphasize(True)


MIN_TABLE_ROWS = 8  # 자동 목록에 없는 프로그램 표의 최소 줄 수(창이 낮아도 줄지 않는다)
MAX_TABLE_ROWS = 40


class DiscoverView(BaseView):
    name = S.OLD_DISCOVER
    min_table_rows = MIN_TABLE_ROWS

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.OLD_SCAN), next_=lambda: self.c.go(S.OLD_CHECKLIST))
        self.start_btn = self.add_action(T.BTN_DISCOVER, lambda: self.guarded(self.c.start_discover), padx=10)
        self.primary = self.start_btn
        self._bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.header(T.DISC_HEADING, T.DISC_SUB)
        self.add_status_card({T.TASK_DISCOVER})
        # 아래에 고정되는 줄(스크롤 밖): 선택 개수/합계와 '이해했습니다' 체크. 먼저 pack(side=bottom) 해야 창이 낮아도 잘리지 않는다
        self.ack_var = tk.BooleanVar(value=False)
        self.ack = ttk.Checkbutton(self, variable=self.ack_var, command=self._ack_changed, text=T.DISC_ACK)
        self.ack.pack(side="bottom", anchor="w")
        self.sumrow = ttk.Frame(self)
        self.sumrow.pack(side="bottom", fill="x", pady=(4, 0))
        self.count = ttk.Label(self.sumrow, text=T.DISC_SELECTED.format(n=0, m=0))
        self.count.pack(side="left")
        self.total = ttk.Label(self.sumrow, text=T.DISC_TOTAL_V2.format(n=0, files=0, size="0B"), justify="left", font=BOLD_FONT)  # 선택 합계
        self.total.pack(side="left", padx=(14, 0), fill="x", expand=True)
        autowrap(self.total, 330)
        # 가운데(창이 낮으면 이 부분이 스크롤된다. 표는 줄지 않는다): 한 줄 요약 + '자세히' + 설정 찾기 + 표 + 경계 + 아래 영역
        self.scroll = ScrollBody(self)
        self.scroll.pack(fill="both", expand=True)
        body = ttk.Frame(self.scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        self.more = Collapsible(body, T.DISC_MORE_TOGGLE, T.DISC_MORE_TOGGLE_OPEN, lead=T.DISC_HINT_SHORT)  # 한 줄 요약 + 자세히 ▸
        self.more.pack(anchor="w", fill="x")
        self.info = self.more.lead_label
        self.more.add_text(T.DISC_HINT)
        self.more.add_text(T.FIND_HINT)
        self.more.add_text(T.DISC_BULK_SCOPE)
        self.more.add_text(T.OPEN_HINT)
        self.hint_label = table_hint(self.more.body)  # Space 로 선택
        self.hint_label.pack(anchor="w")
        self.find_full = self.more.add_text("")  # 설정 찾기 결과의 전체 안내(위 한 줄 요약의 나머지)
        self.find_row = ttk.Frame(body)
        ttk.Label(self.find_row, text=T.FIND_LABEL).pack(side="left")
        self.find_var = tk.StringVar(value=T.FIND_PICK)
        self.find_box = ttk.Combobox(self.find_row, textvariable=self.find_var, state="readonly", width=34, values=[T.FIND_PICK])
        self.find_box.pack(side="left", padx=6)
        self.find_btn = ttk.Button(self.find_row, text=T.FIND_BTN, command=self._find)
        self.find_btn.pack(side="left")
        self.find_row.pack(anchor="w", fill="x", pady=(2, 0))
        self.find_msg = ttk.Label(body, text="", justify="left")  # 찾기 결과 한 줄 요약(있을 때만 보인다)
        autowrap(self.find_msg)

        cols = [("sel", T.DISC_COLS_V2[0], 56), ("warn", T.DISC_COLS_V2[1], 110), ("prog", T.DISC_COLS_V2[2], 170),
                ("path", T.DISC_COLS_V2[3], 230), ("cfg", T.DISC_COLS_V2[4], 100), ("size", T.DISC_COLS_V2[5], 110),
                ("excl", T.DISC_COLS_V2[6], 80)]
        frame, self.tree = make_tree(body, cols, height=MIN_TABLE_ROWS)
        frame.pack(fill="x", pady=(4, 0))
        self.tree.tag_configure("secret", background=DANGER_BG, foreground=DANGER_FG)
        self.tree.tag_configure("weak", foreground=WARN_FG)
        self.tree.tag_configure("noconfig", foreground="#767676")  # 설정 파일 없음: 회색
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", self._key_toggle)
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.tips = TreeTooltip(self.tree, self._tip_text)
        # 표와 아래 영역 사이의 경계: 끌어서 표 높이를 바꾼다(최소 8줄). 창이 낮으면 표는 그대로 두고 아래 영역이 줄어 스크롤된다
        self.sash = tk.Frame(body, height=9, bg=INFO_BORDER, cursor="sb_v_double_arrow", takefocus=1, highlightthickness=1,
                             highlightbackground=INFO_BORDER, highlightcolor=INFO_FG)
        self.sash.pack(fill="x", pady=(0, 3))
        add_tooltip(self.sash, T.DISC_GRIP_TIP)
        self.sash.bind("<ButtonPress-1>", self._sash_press)
        self.sash.bind("<B1-Motion>", self._sash_motion)
        self.sash.bind("<Up>", lambda e: (self.set_table_rows(self.table_rows() - 1), "break")[1])
        self.sash.bind("<Down>", lambda e: (self.set_table_rows(self.table_rows() + 1), "break")[1])
        self._drag = (0, MIN_TABLE_ROWS)
        # 아래 영역: 전체 선택/해제, 민감 안내, 고른 줄 상세, 고급 옵션
        low = ttk.Frame(body)
        low.pack(fill="x")
        self.bulk = ttk.Frame(low)
        self.select_all_btn = ttk.Button(self.bulk, text=T.DISC_SELECT_ALL, command=self._select_all)
        self.clear_all_btn = ttk.Button(self.bulk, text=T.DISC_CLEAR_ALL, command=self._clear_all)
        self.select_all_btn.pack(side="left")
        self.clear_all_btn.pack(side="left", padx=6)
        # 탐색기에서 보기(열거나 실행하지 않고 폴더만 보여 준다): '폴더 보기' 단추(창이 낮아도 보이는 아래 고정 줄), 줄 더블클릭(체크 칸 제외), Enter
        self.explorer = ExplorerLink(self, self.sumrow)
        self.explorer.button.pack(side="right")
        self._last_toggle: tuple | None = None  # 방금 첫 클릭이 바꾼 항목(더블클릭으로 열 때 되돌린다)
        self.bulk_msg = ttk.Label(self.bulk, text="", justify="left")
        self.bulk_msg.pack(side="left", padx=6)
        for b in (self.select_all_btn, self.clear_all_btn):
            bind_alt(self.app, b, lambda: self.app.current == self.name)
        self.bulk.pack(anchor="w", fill="x")
        self.banner = tk.Label(low, text="", anchor="w", justify="left")
        self.banner.pack(fill="x", pady=2)
        autowrap(self.banner, 40)
        # 자체 이전이 필요한 프로그램(폴더를 복사해서 옮길 수 없음): 메인 표에서 빼고 표 아래의 접힌 구역에 모은다(없으면 구역 자체가 안 보인다)
        self.st_slot = ttk.Frame(low)
        self.st_slot.pack(fill="x")
        self.st_box = Collapsible(self.st_slot, T.DISC_SELF_TOGGLE.format(n=0), T.DISC_SELF_TOGGLE_OPEN.format(n=0))
        self.st_box.add_text(T.DISC_SELF_HINT)
        st_cols = [("sel", T.DISC_SELF_COLS[0], 56), ("badge", T.DISC_SELF_COLS[1], 110), ("prog", T.DISC_SELF_COLS[2], 200),
                   ("todo", T.DISC_SELF_COLS[3], 420)]
        st_frame, self.st_tree = make_tree(self.st_box.body, st_cols, height=3)
        st_frame.pack(fill="x")
        self.st_tree.tag_configure("selftransfer", foreground=WARN_FG)
        self.st_tree.bind("<Button-1>", self._st_click)
        self.st_tree.bind("<space>", self._st_key_toggle)
        self.st_tree.bind("<<TreeviewSelect>>", self._show_st_detail)
        self.st_tips = TreeTooltip(self.st_tree, self._st_tip_text)
        self.st_rows: dict[str, object] = {}
        self.explorer.attach(self.tree, lambda iid: (self.rows[iid].path, None) if iid in self.rows else None,
                             click=self._click, undo=self._undo_toggle)
        self.explorer.attach(self.st_tree, lambda iid: (self.st_rows[iid].path, None) if iid in self.st_rows else None,
                             click=self._st_click, undo=self._undo_toggle_st)
        self.detail = ttk.Label(low, text="", justify="left")  # 줄을 고르면 그때만 보인다
        autowrap(self.detail)
        self.adv = Collapsible(low, T.DISC_ADV_TOGGLE, T.DISC_ADV_TOGGLE_OPEN)  # 고급 옵션(접힘)
        self.adv.pack(anchor="w", fill="x")
        self.all_var = tk.BooleanVar(value=False)
        self.all_check = ttk.Checkbutton(self.adv.body, variable=self.all_var, command=self._all_changed, text=T.DISC_ALL_MODE)
        self.all_check.pack(anchor="w")
        self.adv.add_text(T.DISC_ALL_MODE_NOTE)
        self.rows: dict[str, object] = {}
        self.cells: dict[str, dict] = {}
        self.finish()

    # ------------------------------------------------------------------ 표 높이(최소 8줄, 끌어서 조절)
    def table_rows(self) -> int:
        return int(self.tree.cget("height"))

    def set_table_rows(self, n: int) -> int:
        n = max(MIN_TABLE_ROWS, min(MAX_TABLE_ROWS, int(n)))
        self.tree.configure(height=n)
        return n

    def _row_px(self) -> int:
        try:
            return max(16, int(ttk.Style().lookup("Treeview", "rowheight") or 24))
        except (tk.TclError, ValueError):
            return 24

    def _sash_press(self, event) -> None:
        self._drag = (event.y_root, self.table_rows())
        self.sash.focus_set()

    def _sash_motion(self, event) -> None:
        y0, rows0 = self._drag
        self.set_table_rows(rows0 + round((event.y_root - y0) / self._row_px()))

    def on_show(self) -> None:
        self.scroll.scroll_to_top()
        self.all_var.set(self.c.state.extra_mode == "all")
        self._refresh()
        done = bool(self.c.state.discovery)
        self.start_btn.emphasize(not done)
        self.next_btn.emphasize(done)
        self.start_btn.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "discover_done":
            d = payload
            self.info.configure(text=T.DISC_SUMMARY.format(n=d.program_count, m=d.candidate_count, k=d.secret_count))
            self.start_btn.emphasize(False)
            self.next_btn.emphasize(True)
            self.ack_var.set(False)
            self._set_find_msg("")
            self.find_full.configure(text="")
            self._refresh()
        elif kind == "find_done":
            full = self.c.find_summary(payload)
            lines = full.split("\n")
            self._set_find_msg(lines[0])
            self.find_full.configure(text="\n".join(lines[1:]))
            self._refresh()
        elif kind in ("selection_changed", "busy"):
            self._refresh()

    def _set_find_msg(self, text: str) -> None:
        self.find_msg.configure(text=text)
        if text and not self.find_msg.winfo_manager():
            self.find_msg.pack(anchor="w", fill="x", after=self.find_row)
        elif not text and self.find_msg.winfo_manager():
            self.find_msg.pack_forget()

    def _tip_text(self, iid: str, col: int) -> str:
        r = self.rows.get(iid)
        if r is None:
            return ""
        cell = self.cells.get(iid, {})
        return {1: ", ".join(r.secret) or r.badge_note, 2: f"{r.programs} ({r.grade})", 3: r.path, 4: cell.get("tip", ""),
                6: T.DISC_EXCLUDED_TIP}.get(col, "")

    def _refresh(self) -> None:
        sort_state = dict(getattr(self.tree, "_sort_state", {}))
        clear(self.tree)
        self.rows = {}
        self.cells = {}
        rows = self.c.candidate_rows()
        cells = self.c.candidate_cfg_cells()
        for r in rows:
            if r.self_transfer:
                continue  # 자체 이전 필요: 메인 표 대신 아래 접힌 구역에 보인다
            checked = r.key in self.c.state.selected
            mark = T.CHECKED if checked else (T.LOCKED if not r.selectable else T.UNCHECKED)
            warn = T.DISC_WARN_SECRET if r.secret else (r.badge or (T.DISC_WARN_WEAK if r.weak else ""))
            tags = ("secret",) if r.secret else (("weak",) if r.weak else ())
            cell = cells.get(r.key, {"cfg": T.DISC_CFG_UNKNOWN, "size": "-", "excl": "-", "tip": ""})
            if cell.get("none") and not r.secret:
                tags = ("noconfig",)
            iid = self.tree.insert("", "end", values=(mark, warn, r.programs, T.ellipsize_path(r.path), cell["cfg"], cell["size"],
                                                      cell["excl"]), tags=tags)
            self.rows[iid] = r
            self.cells[iid] = cell
        for col, desc in sort_state.items():
            sort_tree(self.tree, col, desc)
        self._refresh_self_transfer([r for r in rows if r.self_transfer])
        n_secret = sum(1 for r in rows if r.secret)
        if n_secret:
            self.banner.configure(text=T.DISC_BANNER.format(icon=T.ICON_WARN, n=n_secret), bg=DANGER_BG, fg=DANGER_FG)
        else:
            self.banner.configure(text="", bg=self._bg)
        all_mode = self.c.state.extra_mode == "all"
        self.tree.heading("cfg", text=T.DISC_COLS_V2[4] if not all_mode else T.DISC_COL_FILES_ALL)
        self.tree.heading("size", text=T.DISC_COLS_V2[5] if not all_mode else T.DISC_COL_SIZE_FULL)
        names = self.c.ruleless_program_names()
        self.find_box.configure(values=[T.FIND_PICK] + names)
        if self.find_var.get() not in names:
            self.find_var.set(T.FIND_PICK)
        sel = self.c.selected_rows()
        big = self.c.selection_totals()["big"]
        self.count.configure(text=T.DISC_SELECTED.format(n=len(sel), m=len(rows)) + (T.DISC_SELECTED_BIG.format(n=big) if big else ""))
        self.total.configure(text=self.c.selection_config_text())
        self.ack_var.set(self.c.state.secrets_ack)
        self.all_var.set(all_mode)
        self.explorer.refresh()  # 표를 다시 그렸으니 '폴더 보기' 단추 상태를 맞춘다

    def _visible_keys(self) -> list:
        return [r.key for r in self.rows.values()]  # 메인 표만: 전체 선택은 '자체 이전 필요' 구역의 항목을 선택하지 않는다

    # ------------------------------------------------------------------ 자체 이전이 필요한 프로그램 구역
    def _refresh_self_transfer(self, st_rows: list) -> None:
        clear(self.st_tree)
        self.st_rows = {}
        n = len(st_rows)
        self.st_box.title = T.DISC_SELF_TOGGLE.format(n=n)
        self.st_box.title_open = T.DISC_SELF_TOGGLE_OPEN.format(n=n)
        self.st_box.toggle_btn.configure(text=self.st_box.title_open if self.st_box.open else self.st_box.title)
        for r in st_rows:
            mark = T.CHECKED if r.key in self.c.state.selected else (T.LOCKED if not r.selectable else T.UNCHECKED)
            tags = ("secret",) if r.secret else ("selftransfer",)
            iid = self.st_tree.insert("", "end", values=(mark, T.DISC_SELF_BADGE, r.programs, r.self_transfer["todo"]), tags=tags)
            self.st_rows[iid] = r
        self.st_tree.configure(height=max(2, min(6, n)))
        if n and not self.st_box.winfo_manager():
            self.st_box.pack(anchor="w", fill="x", pady=(2, 0))
        elif not n and self.st_box.winfo_manager():
            self.st_box.pack_forget()

    def _st_tip_text(self, iid: str, col: int) -> str:
        r = self.st_rows.get(iid)
        if r is None:
            return ""
        return {2: f"{r.programs} / {r.path}", 3: r.self_transfer["todo"]}.get(col, "")

    def _st_text(self, r) -> str:
        return T.DISC_SELF_DETAIL.format(reason=r.self_transfer["reason"], todo=r.self_transfer["todo"])

    def _toggle_st(self, iid: str) -> None:
        r = self.st_rows.get(iid)
        if r is not None:
            turning_on = r.key not in self.c.state.selected
            self.guarded(self.c.set_selected, r.key, turning_on)  # 막지 않는다: 안내만 띄운다
            self._last_toggle = ("st", r.key) if (r.key in self.c.state.selected) == turning_on else None
            if turning_on and r.key in self.c.state.selected:
                messagebox.showinfo(T.DISC_SELF_TITLE, T.DISC_SELF_CHECK.format(reason=r.self_transfer["reason"], todo=r.self_transfer["todo"]),
                                    parent=self)

    def _st_click(self, event) -> None:
        iid = self.st_tree.identify_row(event.y)
        if iid:
            self._toggle_st(iid)

    def _st_key_toggle(self, _e=None):
        sel = self.st_tree.selection()
        if sel:
            self._toggle_st(sel[0])
        return "break"

    def _show_st_detail(self, _e=None) -> None:
        sel = self.st_tree.selection()
        if not sel or sel[0] not in self.st_rows:
            return
        if not self.detail.winfo_manager():
            self.detail.pack(fill="x", pady=2, after=self.st_slot)
        self.detail.configure(text=self._st_text(self.st_rows[sel[0]]))

    def _select_all(self) -> None:
        n, k = self.c.select_all_candidates(self._visible_keys())
        msg = T.DISC_BULK_SELECTED.format(n=n)
        if k:
            msg += " " + T.DISC_BULK_SKIPPED_SECRET.format(k=k)
        self.bulk_msg.configure(text=msg)

    def _clear_all(self) -> None:
        n = self.c.clear_all_candidates(self._visible_keys() + [r.key for r in self.st_rows.values()])  # 해제는 구역 항목도 함께(안전한 쪽)
        self.bulk_msg.configure(text=T.DISC_BULK_CLEARED.format(n=n))

    def _undo_toggle(self, iid: str) -> None:
        """줄 더블클릭으로 탐색기를 열 때, 첫 클릭이 바꾼 체크를 원래대로 되돌린다."""
        r = self.rows.get(iid)
        if r is not None and self._last_toggle == ("main", r.key):  # 표를 다시 그리면 줄 번호가 바뀌므로 항목 키로 비교한다
            self._last_toggle = None
            self.guarded(self.c.set_selected, r.key, r.key not in self.c.state.selected)

    def _undo_toggle_st(self, iid: str) -> None:
        r = self.st_rows.get(iid)
        if r is not None and self._last_toggle == ("st", r.key):
            self._last_toggle = None
            self.guarded(self.c.set_selected, r.key, r.key not in self.c.state.selected)

    def _toggle(self, iid: str) -> None:
        r = self.rows.get(iid)
        if r is not None:
            turning_on = r.key not in self.c.state.selected
            self.guarded(self.c.set_selected, r.key, turning_on)
            self._last_toggle = ("main", r.key) if (r.key in self.c.state.selected) == turning_on else None
            if turning_on and r.key in self.c.state.selected and self.c.is_no_config(r.key):
                messagebox.showinfo(T.DISC_NO_CONFIG_TITLE, T.DISC_NO_CONFIG_CHECK, parent=self)  # 안내만(체크는 그대로)

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
        if not self.detail.winfo_manager():
            self.detail.pack(fill="x", pady=2, after=self.st_slot)
        text = T.DISC_DETAIL.format(kind=r.kind, path=r.path, grade=r.grade, reason=f" — {r.reason}" if r.reason else "")
        cell = self.cells.get(sel[0], {})
        if cell.get("tip"):
            text += "\n" + cell["tip"]
        if r.detail_cache:
            text += r.detail_cache
        if r.secret:
            text += T.DISC_DETAIL_SECRET.format(names=", ".join(r.secret))
        self.detail.configure(text=text)
        first = r.programs.split(", ")[0]
        if first in self.c.ruleless_program_names():
            self.find_var.set(first)  # 고른 줄의 프로그램을 '설정 찾기' 목록에 미리 채워 준다

    def _find(self) -> None:
        name = self.find_var.get()
        if name == T.FIND_PICK:
            messagebox.showwarning(T.GUARD_TITLE, T.G_FIND_NEED_PROGRAM, parent=self)
            return
        self.guarded(self.c.start_find_program, name)

    def _ack_changed(self) -> None:
        on = self.ack_var.get()
        if on and not messagebox.askyesno(T.DISC_ACK_DIALOG_TITLE, T.DISC_ACK_DIALOG, parent=self):
            self.ack_var.set(False)
            return
        self.c.set_secrets_ack(on)

    def _all_changed(self) -> None:
        """고급 옵션 '폴더 전체 담기': 켤 때는 한 번 더 확인한다. 기본(끔)은 설정 파일만 담는다."""
        on = self.all_var.get()
        if on and not messagebox.askyesno(T.DISC_ALL_MODE_CONFIRM_TITLE, T.DISC_ALL_MODE_CONFIRM, icon="warning", parent=self):
            self.all_var.set(False)
            return
        self.c.set_extra_mode("all" if on else "config")


class CheckList(ttk.Frame):
    """스크롤되는 체크박스 목록(긴 문장은 창 폭에 맞춰 줄바꿈, Tab 키로 이동, Space 로 체크)."""

    def __init__(self, parent: tk.Misc, on_toggle) -> None:
        super().__init__(parent)
        self.on_toggle = on_toggle
        self.canvas = tk.Canvas(self, highlightthickness=0, background=LIST_BG)
        ys = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=ys.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner = tk.Frame(self.canvas, background=LIST_BG)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        self.widgets: list[tuple[tk.Widget, str]] = []
        self.vars: list[tk.BooleanVar] = []
        self.canvas.pc_wheel = lambda delta: self.canvas.yview_scroll(-1 * (delta // 120), "units")  # 앱 전체 휠 처리기가 호출

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
                                   anchor="w", justify="left", background=LIST_BG, activebackground=INFO_BG,
                                   highlightthickness=2, highlightbackground=LIST_BG, highlightcolor=INFO_FG,
                                   font="TkDefaultFont")
                self.vars.append(var)
            else:
                w = tk.Label(self.inner, text=text, justify="left", anchor="w", foreground=MUTED_FG, background=LIST_BG,
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
        self.make_btn = self.add_action(T.BTN_CL_MAKE, lambda: self.guarded(self.c.start_checklist), padx=10)
        self.primary = self.make_btn
        self.header(T.CL_HEADING, T.CL_SUB)
        self.add_status_card({T.TASK_CHECKLIST})
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.save_btn = ttk.Button(top, text=T.BTN_CL_SAVE, command=self._save)
        self.save_btn.pack(side="left")
        self.save_btn.state(["disabled"])
        self.reason = ttk.Label(top, text=T.WHY_CHECKLIST_FIRST, foreground=WARN_FG)
        self.reason.pack(side="left", padx=10)
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
        self.make_btn.emphasize(self.c.state.checklist is None)
        self.next_btn.emphasize(self.c.state.checklist is not None)
        self.make_btn.focus_set()

    def on_event(self, kind, payload) -> None:
        if kind == "checklist_done":
            self.make_btn.emphasize(False)
            self.next_btn.emphasize(True)
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
        self.navbar(back=lambda: self.c.go(S.OLD_CHECKLIST))
        self.go_btn = self.add_action(T.BTN_EXPORT, self._export)  # 하단 고정 막대의 주 버튼(끝나면 '완료')
        self.primary = self.go_btn
        self.reason = ttk.Label(self.bar_right, text="", foreground=WARN_FG, justify="right")
        self.reason.pack(side="right", padx=10)
        self.header(T.EXP_HEADING, T.EXP_SUB)
        self.add_status_card({T.TASK_EXPORT, T.TASK_EXPORT_RETRY})
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        self.path_var = tk.StringVar()
        self.path_field = PathField(body, self.path_var, self._browse, label=T.EXP_PATH_LABEL, hint=T.EXP_PATH_HINT,
                                    drives=self.c.list_drives,
                                    on_drive=lambda root: self._set_path(os.path.join(root, T.EXP_DEFAULT_NAME)),
                                    extra=[(T.BTN_EXP_DESKTOP, lambda: self._set_path(self.c.default_export_path()))])
        self.path_field.pack(fill="x")
        entry = self.path_field.entry
        self.plan = ttk.Label(body, text="", justify="left")
        self.plan.pack(anchor="w", fill="x", pady=4)
        autowrap(self.plan)
        self.resume_box = ttk.Frame(body)  # 이어받을 수 있을 때만 보인다
        self.resume_lbl = ttk.Label(self.resume_box, text="", justify="left", foreground=OK_FG)
        self.resume_lbl.pack(anchor="w", fill="x")
        autowrap(self.resume_lbl)
        self.restart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.resume_box, text=T.EXP_RESTART_CHECK, variable=self.restart_var).pack(anchor="w")
        self._resume_shown = False
        entry.bind("<FocusOut>", lambda e: self._update_resume(), add="+")
        entry.bind("<Return>", lambda e: self._update_resume(), add="+")
        self.adv = Collapsible(body, T.EXP_ADV_TOGGLE, T.EXP_ADV_TOGGLE_OPEN)  # 고급 옵션(접힘): 임시 파일 위치
        self.adv.pack(anchor="w", fill="x")
        self.stage_var = tk.StringVar()  # 비워 두면 기본 위치
        self.stage_field = PathField(self.adv.body, self.stage_var, self._browse_stage, label=T.EXP_STAGE_LABEL, hint=T.EXP_STAGE_HINT)
        self.stage_field.pack(fill="x")
        self.cache_var = tk.BooleanVar(value=False)  # 고급 옵션: 재생성되는 임시 저장물(Cache, node_modules 등)도 포함
        ttk.Checkbutton(self.adv.body, variable=self.cache_var, text=T.EXP_INCLUDE_CACHES,
                        command=lambda: self.c.set_include_caches(self.cache_var.get())).pack(anchor="w", pady=(4, 0))
        # 끝난 뒤 결과 영역: 상자 사이 여백을 줄여 결과 요약/다음에 할 일/확인표가 한 화면에 들어오게 한다
        self.stop_box = tk.Frame(body, bg=DANGER_BG, highlightbackground=DANGER_FG, highlightthickness=1, padx=10, pady=4)  # 예외로 멈췄을 때만
        tk.Label(self.stop_box, text=T.EXP_STOPPED_HEAD, bg=DANGER_BG, fg=DANGER_FG, font=SUB_FONT, anchor="w").pack(anchor="w")
        self.stop_hint = tk.Label(self.stop_box, text=T.EXP_STOPPED_HINT, bg=DANGER_BG, justify="left", anchor="w")
        self.stop_hint.pack(anchor="w", fill="x")
        autowrap(self.stop_hint, 30)
        stop_row = tk.Frame(self.stop_box, bg=DANGER_BG)
        stop_row.pack(anchor="w", pady=(3, 0))
        self.retry_save_btn = ActionButton(stop_row, text=T.BTN_EXP_RETRY_SAVEPOINT, command=lambda: self._export(restart=False), kind="primary")
        self.retry_save_btn.pack(side="left")
        self.restart_btn = ActionButton(stop_row, text=T.BTN_EXP_RESTART, command=lambda: self._export(restart=True), kind="plain")
        self.restart_btn.pack(side="left", padx=8)
        self._last_task = ""
        self.summary = ResultSummary(body)
        self.summary.pack(fill="x", pady=3)
        self.summary.box.pack_configure(pady=(3, 0))
        self.cov_fold = Collapsible(body, T.EXP_CHECK_FOLD, T.EXP_CHECK_FOLD_OPEN)  # 설정 확인표는 접어 둔다
        self.cov = CoverageBox(self.cov_fold.body, title="",  # 끝난 뒤: 프로그램별 설정 확인표(빠진 파일 예시는 탐색기에서 볼 수 있다: 구 PC 에서만)
                               explorer=(self.c.can_show_in_explorer, self.c.show_in_explorer))
        self.cov.title_label.pack_forget()
        self.cov.pack(fill="x")
        self.cache_note = ttk.Label(body, text="", justify="left", foreground=MUTED_FG)
        autowrap(self.cache_note)
        self.retry_panel = RetryPanel(body, self.c, self.guarded)  # 읽지 못해 제외된 파일이 있을 때만 보이는 '다시 담기' 상자
        self.retry_panel.pack(fill="x")
        # 끝난 뒤: 큰 '다음에 할 일' 상자와 결과 위치 열기
        self.next_box = tk.Frame(body, bg=INFO_BG, highlightbackground=INFO_FG, highlightthickness=1, padx=10, pady=4)
        tk.Label(self.next_box, text=T.NEXT_TITLE, bg=INFO_BG, fg=INFO_FG, font=SUB_FONT, anchor="w").pack(anchor="w")
        self.next_lines: list[tk.Label] = []
        for line in T.EXP_NEXT_STEPS:
            lbl = tk.Label(self.next_box, text=line, bg=INFO_BG, justify="left", anchor="w")
            lbl.pack(anchor="w", fill="x", pady=1)
            autowrap(lbl, 30)
            self.next_lines.append(lbl)
        self.open_btn = ttk.Button(self.next_box, text=T.BTN_OPEN_RESULT, command=self._open)
        self.open_btn.pack(anchor="w", pady=(3, 0))
        self.open_btn.state(["disabled"])
        self.more = Collapsible(body)  # 자세한 결과(포함된 자동 목록, 읽지 못한 파일, 직접 해야 할 일)
        frame, self.result = make_text(self.more.body, height=9)
        frame.pack(fill="both", expand=True)
        self.lic_link = self.license_link(body)
        self.lic_link.pack(anchor="w", pady=(3, 0))
        self._done = False
        self.path_var.trace_add("write", lambda *a: self._update_reason())
        self.finish()

    def _set_path(self, path: str) -> None:
        self.path_var.set(path)
        self._update_resume()

    def _update_resume(self) -> None:
        hint = self.c.export_resume_hint(clean_path(self.path_var.get()))
        if hint:
            self.resume_lbl.configure(text=hint)
            if not self._resume_shown:
                self.resume_box.pack(anchor="w", fill="x", after=self.plan)
                self._resume_shown = True
        elif self._resume_shown:
            self.resume_box.pack_forget()
            self._resume_shown = False
            self.restart_var.set(False)

    def _update_reason(self) -> None:
        why = self.c.export_reason(clean_path(self.path_var.get()))
        self.reason.configure(text=why)
        if self._done:
            return
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
        if self.c.state.extra_mode == "all":
            text += "\n" + T.EXP_MODE_ALL_NOTE
        self.plan.configure(text=text)
        self._reset_done()
        self.stop_box.pack_forget()
        self.cache_var.set(self.c.state.include_caches)
        self.scroll.scroll_to_top()
        if not self.path_var.get().strip():
            self.path_var.set(self.c.default_export_path())  # 기본: 바탕화면의 pcmigrate-settings.zip
        self._update_reason()
        self._update_resume()
        self.go_btn.focus_set()

    def _reset_done(self) -> None:
        """화면에 다시 들어오면 '완료' 상태를 풀고 주 버튼을 '패키지 만들기' 로 되돌린다."""
        if self._done:
            self._done = False
            self.go_btn.configure(text=T.BTN_EXPORT, command=self._export)
            self.next_box.pack_forget()
            self.more.pack_forget()
            self.cov_fold.pack_forget()
            self.cache_note.pack_forget()
            self.summary.clear()
            self.retry_panel.reset()

    def _browse(self) -> None:
        p = filedialog.asksaveasfilename(parent=self, title=T.EXP_SAVE_TITLE, defaultextension=".zip",
                                         filetypes=[("설정 묶음 파일", "*.zip")], initialfile=T.EXP_DEFAULT_NAME)
        if p:
            self.path_var.set(p.replace("/", "\\"))
            self._update_resume()

    def _browse_stage(self) -> None:
        p = filedialog.askdirectory(parent=self, title=T.EXP_STAGE_PICK, mustexist=True)
        if p:
            self.stage_var.set(p.replace("/", "\\"))

    def _export(self, restart: bool | None = None) -> None:
        """restart: None 이면 화면의 '처음부터 다시 만들기' 체크를 따르고, True/False 면 그 방식으로 한다(멈춘 뒤의 두 단추)."""
        self.path_field.normalize()
        self.stage_field.normalize()
        out = clean_path(self.path_var.get())
        if not out:
            messagebox.showwarning(T.EXP_NEED_PATH_TITLE, T.EXP_NEED_PATH, parent=self)
            return
        self._update_resume()
        exists = T.EXP_EXISTS if os.path.exists(out) else ""
        if not messagebox.askokcancel(T.EXP_CONFIRM_TITLE, T.EXP_CONFIRM.format(path=out, exists=exists), parent=self):
            return
        if restart is None:
            restart = bool(self._resume_shown and self.restart_var.get())
        if restart and not messagebox.askokcancel(T.EXP_RESTART_TITLE, T.EXP_RESTART_CONFIRM, parent=self):
            return
        self.guarded(self.c.start_export, out, write_confirmed=True, resume=not restart,
                     **({"stage_dir": stage} if (stage := clean_path(self.stage_var.get())) else {}))

    def on_event(self, kind, payload) -> None:
        self.retry_panel.on_event(kind, payload)
        if kind == "busy" and payload:
            self._last_task = payload
        elif kind == "error" and self._last_task == T.TASK_EXPORT and self.app.current == self.name and not self.stop_box.winfo_manager():
            self.stop_box.pack(fill="x", pady=3, before=self.summary)  # 내보내기가 예외로 멈췄다: 이어서/처음부터 단추
            self._update_resume()
            self.after_idle(lambda: self.scroll.scroll_to(self.stop_box))
        if kind not in ("export_done", "export_retry_done"):
            return
        self.stop_box.pack_forget()
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
        n_denied = sum(1 for s in m["skipped"] if s.get("denied"))
        if n_denied:
            lines.append(f"{T.ICON_WARN} " + T.EXP_DENIED.format(n=n_denied))
        lines += [T.friendly_detail(x) for x in unreadable_lines([f for f in m["failed"] if f.get("reason")], 5)]
        lines += [f"  - {f['src']}: {T.friendly_detail(f['error'])}" for f in m["failed"] if not f.get("reason")][:20]
        lines += ["", T.EXP_TODO]
        for w in m.get("manual_warnings", []):
            for item in w.get("items", [])[:3]:
                lines.append(f"  - {w.get('name')}: {item}")
        set_text(self.result, lines)
        self.next_box.pack(fill="x", pady=(3, 0), before=self.lic_link)
        table = self.c.coverage_table(payload)
        self.cov.show(table)
        head = self.c.coverage_headline(table)
        self.cov_fold.title = f"{T.EXP_CHECK_TITLE}: {head} ▸" if head else T.EXP_CHECK_FOLD
        self.cov_fold.title_open = self.cov_fold.title.replace("▸", "▾")
        self.cov_fold.set_open(self.cov_fold.open)
        self.cov_fold.pack(fill="x", pady=(2, 0), before=self.lic_link)
        note = self.c.cache_skipped_text(payload)
        self.cache_note.configure(text=note)
        if note:
            self.cache_note.pack(anchor="w", fill="x", pady=(1, 0), before=self.lic_link)
        self.more.pack(fill="x", before=self.lic_link, pady=(2, 0))
        self.after_idle(lambda: self.scroll.scroll_to(self.summary))  # 끝나면 결과와 '다음에 할 일' 상자가 바로 보이게
        self.open_btn.state(["!disabled"])
        self._done = True
        self.go_btn.state(["!disabled"])
        self.go_btn.configure(text=T.BTN_DONE, command=lambda: self.c.go(S.START))  # 마지막 화면의 주 버튼은 '완료'

    def _open(self) -> None:
        open_location(self.c.state.export_output)
