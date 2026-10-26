"""현황 카드 위젯. 값 계산은 gui/status.py(StatusModel)가 하고, 여기서는 그 결과를 그리기만 한다.

구성(위에서 아래로): ① 지금 하는 일(큰 글씨 한 줄) ② 단계 표시줄 ③ 진행 막대와 지표(파일/용량/속도/경과/남은 시간)
④ 지금 처리 중인 폴더/파일 ⑤ 카운터 칩(성공/건너뜀/읽지 못함/실패, 누르면 목록) ⑥ '문제가 있나요?' 한 줄 ⑦ 마지막 메시지.
색만으로 구분하지 않도록 칩과 문제 줄에는 아이콘과 문구를 함께 쓴다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import status as ST
from .. import texts as T
from .common import (INFO_BG, MUTED_FG, OK_BG, OK_FG, SECRET_BG, SECRET_FG, BIG_FONT, STAGE_FONT, Tooltip, TextDialog,
                     autowrap)

CHIP_COLORS = {  # level -> (배경, 글자)
    "ok": (OK_BG, OK_FG), "info": ("#e3ecfb", "#0b5394"), "warn": ("#fff4cc", "#7a5200"),
    "fail": (SECRET_BG, SECRET_FG), "muted": ("#efefef", MUTED_FG),
}
STEP_COLORS = {ST.STEP_DONE: (OK_FG, "normal"), ST.STEP_CURRENT: ("#0b5394", "bold"), ST.STEP_TODO: (MUTED_FG, "normal")}


class StatusCard(tk.Frame):
    """작업 현황 한눈에 보기. tasks 에 있는 작업이 진행/종료 상태일 때만 보인다."""

    def __init__(self, parent: tk.Misc, controller, tasks: set[str], slot: tk.Misc | None = None) -> None:
        super().__init__(slot or parent, bg=INFO_BG, highlightbackground="#9db7e0", highlightthickness=1, padx=10, pady=8)
        self.c, self.tasks = controller, set(tasks)
        self.shown = False
        self.dialogs: list[TextDialog] = []

        top = tk.Frame(self, bg=INFO_BG)
        top.pack(fill="x")
        tk.Label(top, text=T.CARD_TITLE, bg=INFO_BG, fg=MUTED_FG).pack(side="left")
        self.headline = tk.Label(self, text="", bg=INFO_BG, font=BIG_FONT, anchor="w", justify="left")
        self.headline.pack(fill="x", pady=(2, 4))
        autowrap(self.headline, 20)
        self.steps_row = tk.Frame(self, bg=INFO_BG)
        self.steps_row.pack(fill="x")
        self.step_labels: list[tk.Label] = []
        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(6, 4))
        self.metrics_row = tk.Frame(self, bg=INFO_BG)
        self.metrics_row.pack(fill="x")
        self.metric_values: dict[str, tk.Label] = {}
        for i, key in enumerate((T.CARD_METRIC_FILES, T.CARD_METRIC_BYTES, T.CARD_METRIC_SPEED, T.CARD_METRIC_ELAPSED,
                                 T.CARD_METRIC_ETA)):
            cell = tk.Frame(self.metrics_row, bg=INFO_BG)
            cell.grid(row=0, column=i, sticky="w", padx=(0, 18))
            tk.Label(cell, text=key, bg=INFO_BG, fg=MUTED_FG).pack(anchor="w")
            lbl = tk.Label(cell, text=T.CARD_METRIC_NONE, bg=INFO_BG, font=STAGE_FONT)
            lbl.pack(anchor="w")
            self.metric_values[key] = lbl
        cur = tk.Frame(self, bg=INFO_BG)
        cur.pack(fill="x", pady=(6, 0))
        tk.Label(cur, text=T.CARD_CURRENT + ":", bg=INFO_BG, fg=MUTED_FG).pack(side="left")
        self.current = tk.Label(cur, text=T.CARD_CURRENT_NONE, bg=INFO_BG, anchor="w")
        self.current.pack(side="left", padx=(6, 0))
        self.current_tip = Tooltip(self.current, "")
        self.chips_row = tk.Frame(self, bg=INFO_BG)
        self.chips_row.pack(fill="x", pady=(8, 0))
        self.chips: dict[str, tk.Label] = {}
        self.chip_tips: dict[str, Tooltip] = {}
        for kind in ST.KINDS:
            chip = tk.Label(self.chips_row, text="", padx=10, pady=4, relief="groove", borderwidth=1, font=STAGE_FONT)
            chip.pack(side="left", padx=(0, 8))
            chip.bind("<Button-1>", lambda _e, k=kind: self.open_list(k))
            self.chips[kind] = chip
            self.chip_tips[kind] = Tooltip(chip, "")
        self.resumed = tk.Label(self, text="", bg=INFO_BG, fg=OK_FG, anchor="w", justify="left")
        self.resumed.pack(fill="x", pady=(6, 0))
        self.problem = tk.Label(self, text="", anchor="w", justify="left", padx=8, pady=4)
        self.problem.pack(fill="x", pady=(8, 0))
        autowrap(self.problem, 30)
        self.last = tk.Label(self, text="", bg=INFO_BG, fg=MUTED_FG, anchor="w", justify="left")
        self.last.pack(fill="x", pady=(4, 0))
        autowrap(self.last, 20)

    # ------------------------------------------------------------------ 표시/갱신
    def wants(self) -> bool:
        st = self.c.status
        return st.active and st.task in self.tasks

    def refresh(self) -> None:
        """컨트롤러의 현재 상태로 다시 그린다(작업 중에는 앱이 0.1초마다 부른다). 이 화면의 작업이 아니면 숨긴다."""
        if not self.wants():
            if self.shown:
                self.pack_forget()
                self.shown = False
            return
        if not self.shown:
            self.pack(fill="x", pady=(0, 8))
            self.shown = True
        snap = self.c.status.snapshot()
        self.headline.configure(text=snap.headline, fg=SECRET_FG if snap.state == ST.FAILED else "black")
        self._draw_steps(snap.steps)
        if snap.fraction is None:
            if snap.state == ST.RUNNING:
                if str(self.bar.cget("mode")) != "indeterminate":
                    self.bar.configure(mode="indeterminate")
                    self.bar.start(12)
            else:
                self.bar.stop()
                self.bar.configure(mode="determinate", value=0)
        else:
            if str(self.bar.cget("mode")) != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
            self.bar.configure(value=snap.fraction * 100)
        for name, value in snap.metrics:
            self.metric_values[name].configure(text=value)
        self.current.configure(text=snap.current_short)
        self.current_tip.text = snap.current
        for chip in snap.chips:
            bg, fg = CHIP_COLORS[chip.level]
            lbl = self.chips[chip.key]
            lbl.configure(text=f"{chip.icon} {chip.label} {chip.count:,}", bg=bg, fg=fg,
                          cursor="hand2" if chip.clickable else "arrow")
            self.chip_tips[chip.key].text = chip.tip
        self.resumed.configure(text=snap.resumed_line)
        bg, fg = (SECRET_BG, SECRET_FG) if snap.has_problem else (OK_BG, OK_FG)
        self.problem.configure(text=f"{T.CARD_PROBLEM_LABEL} {snap.problem_line}", bg=bg, fg=fg)
        self.last.configure(text=f"{T.CARD_LAST}: {snap.last_message}")

    def _draw_steps(self, steps: tuple) -> None:
        if len(self.step_labels) != len(steps):
            for w in self.steps_row.winfo_children():
                w.destroy()
            self.step_labels = []
            for i in range(len(steps)):
                lbl = tk.Label(self.steps_row, text="", bg=INFO_BG, padx=6, pady=2)
                lbl.pack(side="left")
                self.step_labels.append(lbl)
                if i < len(steps) - 1:
                    tk.Label(self.steps_row, text="→", bg=INFO_BG, fg=MUTED_FG).pack(side="left")
        for lbl, step in zip(self.step_labels, steps):
            fg, weight = STEP_COLORS[step.state]
            mark = T.ICON_OK + " " if step.state == ST.STEP_DONE else ""
            lbl.configure(text=f"{mark}{step.no} {step.label}", fg=fg, font=("TkDefaultFont", 10, weight),
                          bg="#d6e4fa" if step.state == ST.STEP_CURRENT else INFO_BG)

    # ------------------------------------------------------------------ 칩을 누르면 목록
    def open_list(self, kind: str) -> TextDialog | None:
        chip = next((c for c in self.c.status.snapshot().chips if c.key == kind), None)
        if chip is None or not chip.clickable:
            return None
        title, body = ST.list_dialog_text(self.c.status, kind)
        dlg = TextDialog(self, title, body, 90, 20)
        self.dialogs.append(dlg)
        return dlg
