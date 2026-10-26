"""현황 카드 위젯. 값 계산은 gui/status.py(StatusModel)가 하고, 여기서는 그 결과를 그리기만 한다.

기본(항상 보임): ① 지금 하는 일(큰 문장 한 줄) ② 진행 막대 ③ '남은 시간 약 N분'. 문제가 생기면 ③ 줄 옆에 한 줄 표시가 붙는다.
'자세히 ▸'(접힘): 단계 표시줄, 지표(파일/용량/속도/경과 시간), 지금 처리 중인 폴더/파일, 카운터 칩(성공/건너뜀/읽지 못함/실패, 누르면 목록),
'문제가 있나요?' 한 줄, 마지막 메시지. 색만으로 구분하지 않도록 칩과 문제 줄에는 아이콘과 문구를 함께 쓴다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import status as ST
from .. import texts as T
from .common import (BODY_FONT, BOLD_FONT, CHIP_DONE_BG, CHIP_TODO_BG, DANGER_BG, DANGER_FG, INFO_BG, INFO_BORDER, INFO_FG, MUTED_FG,
                     OK_BG, OK_FG, SUB_FONT, TITLE_FONT, WARN_BG, WARN_FG, Collapsible, TextDialog, Tooltip, autowrap)

CHIP_COLORS = {  # level -> (배경, 글자)
    "ok": (OK_BG, OK_FG), "info": (INFO_BG, INFO_FG), "warn": (WARN_BG, WARN_FG),
    "fail": (DANGER_BG, DANGER_FG), "muted": (CHIP_TODO_BG, MUTED_FG),
}
STEP_COLORS = {ST.STEP_DONE: (OK_FG, BODY_FONT), ST.STEP_CURRENT: (INFO_FG, BOLD_FONT), ST.STEP_TODO: (MUTED_FG, BODY_FONT)}


class StatusCard(tk.Frame):
    """작업 현황 한눈에 보기. tasks 에 있는 작업이 진행/종료 상태일 때만 보인다."""

    def __init__(self, parent: tk.Misc, controller, tasks: set[str], slot: tk.Misc | None = None) -> None:
        super().__init__(slot or parent, bg=INFO_BG, highlightbackground=INFO_BORDER, highlightthickness=1, padx=10, pady=6)
        self.c, self.tasks = controller, set(tasks)
        self.shown = False
        self.dialogs: list[TextDialog] = []

        # ---- 기본 3가지: 큰 문장 / 진행 막대 / 남은 시간
        self.headline = tk.Label(self, text="", bg=INFO_BG, font=TITLE_FONT, anchor="w", justify="left")
        self.headline.pack(fill="x", pady=(0, 4))
        autowrap(self.headline, 20)
        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(0, 4))
        eta_row = tk.Frame(self, bg=INFO_BG)
        self.eta_row = eta_row
        self.eta_label = tk.Label(eta_row, text="", bg=INFO_BG, font=SUB_FONT, anchor="w")
        self.eta_label.pack(side="left")
        self.problem_flag = tk.Label(eta_row, text="", bg=INFO_BG, fg=DANGER_FG, font=BOLD_FONT, anchor="e")  # 문제가 있을 때만 글자가 생긴다
        self.problem_flag.pack(side="right")

        # ---- 자세히 ▸ (접힘)
        self.details = Collapsible(self, T.CARD_DETAILS_SHOW, T.CARD_DETAILS_HIDE, bg=INFO_BG)
        self.details.pack(fill="x", pady=(2, 0))
        self._eta_shown = False
        self._bar_shown = True
        box = tk.Frame(self.details.body, bg=INFO_BG)
        box.pack(fill="x")
        top = tk.Frame(box, bg=INFO_BG)
        top.pack(fill="x")
        tk.Label(top, text=T.CARD_TITLE, bg=INFO_BG, fg=MUTED_FG).pack(side="left")
        self.steps_row = tk.Frame(box, bg=INFO_BG)
        self.steps_row.pack(fill="x")
        self.step_labels: list[tk.Label] = []
        self.metrics_row = tk.Frame(box, bg=INFO_BG)
        self.metrics_row.pack(fill="x", pady=(4, 0))
        self.metric_values: dict[str, tk.Label] = {}
        for i, key in enumerate((T.CARD_METRIC_FILES, T.CARD_METRIC_BYTES, T.CARD_METRIC_SPEED, T.CARD_METRIC_ELAPSED,
                                 T.CARD_METRIC_ETA)):
            cell = tk.Frame(self.metrics_row, bg=INFO_BG)
            cell.grid(row=0, column=i, sticky="w", padx=(0, 18))
            tk.Label(cell, text=key, bg=INFO_BG, fg=MUTED_FG).pack(anchor="w")
            lbl = tk.Label(cell, text=T.CARD_METRIC_NONE, bg=INFO_BG, font=SUB_FONT)
            lbl.pack(anchor="w")
            self.metric_values[key] = lbl
        cur = tk.Frame(box, bg=INFO_BG)
        cur.pack(fill="x", pady=(6, 0))
        tk.Label(cur, text=T.CARD_CURRENT + ":", bg=INFO_BG, fg=MUTED_FG).pack(side="left")
        self.current = tk.Label(cur, text=T.CARD_CURRENT_NONE, bg=INFO_BG, anchor="w")
        self.current.pack(side="left", padx=(6, 0))
        self.current_tip = Tooltip(self.current, "")
        self.chips_row = tk.Frame(box, bg=INFO_BG)
        self.chips_row.pack(fill="x", pady=(8, 0))
        self.chips: dict[str, tk.Label] = {}
        self.chip_tips: dict[str, Tooltip] = {}
        for kind in ST.KINDS:
            chip = tk.Label(self.chips_row, text="", padx=10, pady=4, relief="groove", borderwidth=1, font=SUB_FONT)
            chip.pack(side="left", padx=(0, 8))
            chip.bind("<Button-1>", lambda _e, k=kind: self.open_list(k))
            self.chips[kind] = chip
            self.chip_tips[kind] = Tooltip(chip, "")
        self.resumed = tk.Label(box, text="", bg=INFO_BG, fg=OK_FG, anchor="w", justify="left")
        self.resumed.pack(fill="x", pady=(6, 0))
        self.problem = tk.Label(box, text="", anchor="w", justify="left", padx=8, pady=4)
        self.problem.pack(fill="x", pady=(8, 0))
        autowrap(self.problem, 30)
        self.last = tk.Label(box, text="", bg=INFO_BG, fg=MUTED_FG, anchor="w", justify="left")
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
        self.headline.configure(text=snap.headline, fg=DANGER_FG if snap.state == ST.FAILED else "black",
                                font=TITLE_FONT if snap.state == ST.RUNNING else SUB_FONT)  # 끝난 뒤에는 한 단계 작게(카드가 낮아진다)
        eta = dict(snap.metrics).get(T.CARD_METRIC_ETA, "")
        running = snap.state == ST.RUNNING
        eta_text = T.CARD_ETA_LINE.format(eta=eta if eta and eta != T.CARD_METRIC_NONE else T.CARD_ETA_COMPUTING) if running else ""
        self.eta_label.configure(text=eta_text)
        # 끝났거나 멈춘 뒤에는 진행 막대와 빈 줄을 숨겨 카드를 낮게 둔다(아래 입력칸이 밀려나지 않게)
        want_bar = snap.state == ST.RUNNING
        if want_bar and not self._bar_shown:
            self.bar.pack(fill="x", pady=(0, 4), after=self.headline)
            self._bar_shown = True
        elif not want_bar and self._bar_shown:
            self.bar.pack_forget()
            self._bar_shown = False
        want_eta = bool(eta_text) or bool(snap.has_problem)
        if want_eta and not self._eta_shown:
            self.eta_row.pack(fill="x", before=self.details)
            self._eta_shown = True
        elif not want_eta and self._eta_shown:
            self.eta_row.pack_forget()
            self._eta_shown = False
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
        bg, fg = (DANGER_BG, DANGER_FG) if snap.has_problem else (OK_BG, OK_FG)
        self.problem.configure(text=f"{T.CARD_PROBLEM_LABEL} {snap.problem_line}", bg=bg, fg=fg)
        self.problem_flag.configure(text=snap.problem_line if snap.has_problem else "")  # 문제가 있으면 기본 줄에도 한 줄로 알린다
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
            lbl.configure(text=f"{mark}{step.no} {step.label}", fg=fg, font=weight,
                          bg=CHIP_DONE_BG if step.state == ST.STEP_CURRENT else INFO_BG)

    # ------------------------------------------------------------------ 칩을 누르면 목록
    def open_list(self, kind: str) -> TextDialog | None:
        chip = next((c for c in self.c.status.snapshot().chips if c.key == kind), None)
        if chip is None or not chip.clickable:
            return None
        title, body = ST.list_dialog_text(self.c.status, kind)
        dlg = TextDialog(self, title, body, 90, 20)
        self.dialogs.append(dlg)
        return dlg
