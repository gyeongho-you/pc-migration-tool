"""신 PC 흐름의 '프로그램 설치 확인' 화면과 그 표 부품(InstallPanel).

묶음 파일의 구 PC 프로그램과 이 PC의 설치 상태를 비교한 결과를 보여 준다. 이 화면은 설치를 하지 않는다:
설치 목록(winget.json)을 폴더에 꺼내고 사용자가 명령 창에서 직접 실행할 명령을 복사해 줄 뿐이며, 실행 뒤 '다시 확인'으로 재판정한다.
InstallPanel 은 '추천대로 한 번에'의 확인 지점(views/auto.py)에서도 같이 쓴다.
화면을 바꿀 때 깜빡이지 않도록 fill_hidden=True: app.show 가 보이기 전에 내용을 먼저 채운다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import state as S
from .. import texts as T
from ..controller import GuardError
from .status_card import StatusCard
from .common import (BOLD_FONT, INFO_FG, MUTED_FG, OK_FG, WARN_BG, WARN_FG, ActionButton, BaseView, Notice, TextDialog,
                     TreeTooltip, autowrap, clean_path, clear, make_tree, table_hint)


class _BusyCard(StatusCard):
    """확인하는 동안에만 보이는 현황 카드(끝난 뒤에는 표가 높이를 쓰도록 접는다)."""

    def wants(self) -> bool:
        return bool(self.c.state.busy) and super().wants()


class InstallPanel(ttk.Frame):
    """요약 + 안내 + 단추(명령 복사, 다시 확인) + 표 + 선택한 줄의 설명."""

    def __init__(self, parent: tk.Misc, view: BaseView, height: int = 7) -> None:
        super().__init__(parent)
        self.view, self.c = view, view.c
        self.summary = ttk.Label(self, text=T.INST_NONE, justify="left", font=BOLD_FONT)
        self.summary.pack(anchor="w", fill="x", pady=(0, 4))
        autowrap(self.summary)
        self.notes = Notice(self, "", "info")
        self.notes.pack(fill="x", pady=(0, 4))
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 4))
        self.copy_btn = ActionButton(row, text=T.INST_BTN_COPY, command=self.copy_command, kind="primary")
        self.copy_btn.pack(side="left")
        self.recheck_btn = ActionButton(row, text=T.INST_BTN_RECHECK, command=self.recheck, kind="plain")
        self.recheck_btn.pack(side="left", padx=8)
        self.folder_btn = ActionButton(row, text=T.INST_BTN_FOLDER, command=self.pick_folder, kind="link")
        self.folder_btn.pack(side="right")
        self.folder_label = ttk.Label(row, text="", foreground=MUTED_FG)
        self.folder_label.pack(side="right", padx=8)
        self.copy_note = ttk.Label(self, text="", justify="left", foreground=INFO_FG)  # 명령을 복사한 뒤에만 보인다
        autowrap(self.copy_note)
        # 아래쪽 묶음을 먼저 쌓아 두어야 창이 낮아도 설명 줄이 잘리지 않는다(표가 남는 높이를 쓴다)
        self.detail = ttk.Label(self, text="", justify="left")
        self.detail.pack(side="bottom", anchor="w", fill="x", pady=(4, 0))
        autowrap(self.detail)
        self.hint = table_hint(self)
        self.hint.configure(text=T.INST_SPACE_HINT, justify="left")
        self.hint.pack(side="bottom", anchor="w", fill="x")
        autowrap(self.hint)
        cols = [("mark", T.INST_COLS[0], 48), ("status", T.INST_COLS[1], 160), ("name", T.INST_COLS[2], 230),
                ("version", T.INST_COLS[3], 130), ("reason", T.INST_COLS[4], 420)]
        frame, self.tree = make_tree(self, cols, height=height)
        frame.pack(side="top", fill="both", expand=True, pady=2)
        self.tree.column("mark", anchor="center", stretch=False)
        self.tree.tag_configure("installed", foreground=OK_FG)
        self.tree.tag_configure("diff", foreground=WARN_FG)
        self.tree.tag_configure("winget", foreground=INFO_FG)
        self.tree.tag_configure("manual", foreground=WARN_FG)
        self.tree.tag_configure("ambiguous", background=WARN_BG, foreground=WARN_FG, font=BOLD_FONT)  # 애매함은 따로 강조
        self.tree.tag_configure("not_needed", foreground=MUTED_FG)
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", self._key_toggle)
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.rows: dict[str, dict] = {}
        self.tips = TreeTooltip(self.tree, lambda iid, col: (self.rows.get(iid) or {}).get({2: "name", 4: "reason"}.get(col, ""), ""))

    # ------------------------------------------------------------------ 표시
    def refresh(self) -> None:
        c = self.c
        self.summary.configure(text=c.install_summary_text())
        notes = c.install_notes()
        r = c.state.install_check
        if notes:
            self.notes.set("\n".join(notes), "warn" if r is not None and r.remaining else "ok")
            self.notes.pack(fill="x", pady=(0, 4), before=self.copy_btn.master)
        else:
            self.notes.pack_forget()
        folder = c.state.install_folder or c.default_install_folder()
        self.folder_label.configure(text=T.INST_FOLDER_LABEL.format(folder=T.ellipsize_path(folder, 50)))
        sel = self.tree.selection()
        keep = self.rows.get(sel[0], {}).get("name") if sel else None
        top = self.tree.yview()[0]
        clear(self.tree)
        self.rows = {}
        for row in c.install_rows():
            tag = "diff" if row["diff"] else row["status"]
            mark = (T.CHECKED if row["mark"] else T.UNCHECKED) if row["markable"] else T.LOCKED
            iid = self.tree.insert("", "end", tags=(tag,), values=(mark, row["label"], row["name"], row["version"], row["reason"]))
            self.rows[iid] = row
            if keep is not None and row["name"] == keep:
                self.tree.selection_set(iid)
        self.tree.yview_moveto(top)  # 표시를 바꿔도 보던 위치가 유지되게
        self._update_buttons()

    def _update_buttons(self) -> None:
        busy = bool(self.c.state.busy)
        r = self.c.state.install_check
        self.copy_btn.state(["!disabled"] if r is not None and r.import_needed and not busy else ["disabled"])
        self.recheck_btn.state(["!disabled"] if self.c.state.package and not busy else ["disabled"])

    def on_event(self, kind: str, payload) -> None:
        if kind in ("install_done", "install_marks", "install_folder"):
            self.refresh()
        elif kind == "busy":
            self._update_buttons()

    # ------------------------------------------------------------------ 동작
    def copy_command(self) -> None:
        c = self.c
        try:
            info = c.prepare_install_command()
        except GuardError as e:
            messagebox.showwarning(T.GUARD_TITLE, str(e), parent=self)
            return
        except Exception as e:  # noqa: BLE001 - 쉬운 문구로 바꿔 보여 준다(쓰기 실패 등)
            self.view.app.show_error(c.report_error(e, T.TASK_UI))
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(info["command"])
            self.update()  # 창을 닫아도 클립보드에 남도록 한 번 반영
        except tk.TclError:
            pass  # 복사에 실패해도 아래 창에서 직접 복사할 수 있다
        lines = [T.INST_COPIED]
        if info["backup"]:
            lines.append(T.INST_COPIED_BAK.format(bak=info["backup"]))
        lines += ["", T.INST_COPIED_CMD, info["command"]]
        if info["individual"]:
            lines += ["", T.INST_INDIVIDUAL, *info["individual"]]
        self.copy_note.configure(text=T.INST_COPIED.split("\n", 1)[0])
        self.copy_note.pack(anchor="w", fill="x", after=self.copy_btn.master, pady=(0, 4))
        self.last_dialog = TextDialog(self, T.INST_BTN_COPY, "\n".join(lines), 90, 12)

    def recheck(self) -> None:
        self.view.guarded(self.c.start_install_check)

    def pick_folder(self) -> None:
        p = filedialog.askdirectory(parent=self, title=T.INST_FOLDER_PICK, mustexist=False)
        if p:
            self.c.set_install_folder(clean_path(p.replace("/", "\\")))

    def _toggle(self, iid: str) -> None:
        row = self.rows.get(iid)
        if row is not None and row["markable"]:
            self.c.toggle_install_mark(row["name"])

    def _click(self, event) -> None:
        if self.tree.identify_column(event.x) != "#1":  # 표시 칸을 눌렀을 때만 바꾼다(줄을 고르기만 할 때는 바꾸지 않는다)
            return
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
            self.detail.configure(text="")
            return
        row = self.rows[sel[0]]
        key = "installed" if row["status"] == "installed" else row["status"]
        self.detail.configure(text=T.INST_DETAIL.format(
            name=row["name"], status=T.INST_KIND_NAMES[key], reason=row["reason"],
            hint=T.INST_DETAIL_HINT.format(hint=row["hint"]) if row["hint"] else ""))


class InstallView(BaseView):
    name = S.NEW_INSTALL
    fill_hidden = True  # 숨긴 채로 내용을 먼저 채운 뒤 보이기(화면 전환 깜빡임 방지)

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=lambda: self.c.go(S.NEW_PACKAGE), next_=lambda: self.c.install_next(False), next_text=T.BTN_INST_NEXT)
        self.skip_btn = self.add_action(T.INST_BTN_SKIP, lambda: self.c.install_next(True), kind="plain", padx=10)
        self.header(T.INST_HEADING, T.INST_SUB)
        self.card_slot = ttk.Frame(self)
        self.card_slot.pack(fill="x")
        self.card = _BusyCard(self, self.c, {T.TASK_INSTALL_CHECK}, slot=self.card_slot)
        self.panel = InstallPanel(self, self, height=7)
        self.panel.pack(fill="both", expand=True)
        self.primary = self.next_btn
        self.finish()

    def on_show(self) -> None:
        self.panel.refresh()
        if self.c.install_needs_check():
            self.guarded(self.c.start_install_check)
        self._update()

    def on_event(self, kind, payload) -> None:
        self.panel.on_event(kind, payload)
        if kind in ("busy", "install_done"):
            self._update()

    def _update(self) -> None:
        busy = bool(self.c.state.busy)
        self.next_btn.state(["disabled"] if busy else ["!disabled"])
        self.skip_btn.state(["disabled"] if busy else ["!disabled"])
        self.set_next_reason("")
