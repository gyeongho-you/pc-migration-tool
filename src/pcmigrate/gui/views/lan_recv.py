"""내 파일 옮기기 3/4 — 받는 쪽(새 PC): '받기 대기 중'. 6자리 코드(큰 글씨)와 접속 주소, 진행, 경고, 중지.

받기는 사람이 '받기 시작'을 눌러야 시작한다(연결 통로를 여는 일이므로). 받는 스레드의 소식은 컨트롤러 큐에 쌓이고
이 화면이 0.25초마다 컨트롤러의 lan_poll() 로 가져와 그린다(화면 스레드에서만 화면을 만진다).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from .. import state as S
from .. import texts as T
from ...progress import fmt_bytes, fmt_duration
from .common import (FoldLabel, FoldTLabel, DANGER_FG, INFO_BG, INFO_BORDER, INFO_FG, MUTED_FG, OK_BG, OK_FG, SUB_FONT, TITLE_FONT, WARN_BG, WARN_FG, ActionButton,
                     BaseView, ScrollBody, autowrap, bind_alt, clean_path, open_location)
from .net_section import NetSection

POLL_MS = 250


class ReceiveView(BaseView):
    name = S.LAN_RECV

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.navbar(back=self._back)
        self.home_btn.configure(command=self._home)
        self.stop_btn = ActionButton(self.bar_right, text=T.BTN_RX_STOP, kind="warn", command=self._stop)
        self.start_btn = ActionButton(self.bar_right, text=T.BTN_RX_START, command=self._start)
        self.primary = self.start_btn
        self.header(T.RX_HEADING, T.RX_SUB)
        scroll = ScrollBody(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        body = ttk.Frame(scroll.inner, padding=(0, 0, 8, 0))
        body.pack(fill="both", expand=True)
        self._job = None

        # ---- 저장 위치
        self.top = ttk.Frame(body)  # 결과 상자가 들어갈 맨 위 자리
        self.top.pack(fill="x")
        head = ttk.Frame(body)
        head.pack(fill="x", pady=(4, 0))
        ttk.Label(head, text=T.RX_DEST_LABEL, font=SUB_FONT).pack(side="left")
        self.dest_hint = ttk.Label(head, text=T.RX_DEST_HINT, foreground=MUTED_FG)
        self.dest_hint.pack(side="left", padx=(10, 0))
        row = ttk.Frame(body)
        row.pack(fill="x")
        self.dest_var = tk.StringVar(value="")
        self.dest_entry = ttk.Entry(row, textvariable=self.dest_var, state="readonly")
        self.dest_entry.pack(side="left", fill="x", expand=True)
        self.dest_btn = ttk.Button(row, text=T.BTN_RX_CHANGE, command=self._change_dest)
        self.dest_btn.pack(side="left", padx=(6, 0))

        # ---- 코드 / 주소
        box = tk.Frame(body, highlightbackground=INFO_BORDER, highlightthickness=1, padx=14, pady=8, bg=INFO_BG)
        box.pack(fill="x", pady=(10, 4))
        tk.Label(box, text=T.RX_CODE_LABEL, bg=INFO_BG, fg=MUTED_FG).pack(anchor="w")
        self.code_font = tkfont.nametofont(TITLE_FONT).copy()
        self.code_font.configure(size=30)
        self.code_label = tk.Label(box, text=T.RX_CODE_NONE, bg=INFO_BG, fg=INFO_FG, font=self.code_font, anchor="w")
        self.code_label.pack(anchor="w")
        tk.Label(box, text=T.RX_ADDR_LABEL, bg=INFO_BG, fg=MUTED_FG).pack(anchor="w", pady=(6, 0))
        self.addr_label = tk.Label(box, text=T.RX_ADDR_NONE, bg=INFO_BG, justify="left", anchor="w", font=SUB_FONT)
        self.addr_label.pack(anchor="w", fill="x")
        autowrap(self.addr_label, 40)

        # ---- 상태 / 진행 / 소식
        self.state_label = ttk.Label(body, text=T.RX_STATE["idle"], font=SUB_FONT)
        self.state_label.pack(anchor="w", pady=(8, 2))
        self.bar = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress_label = FoldTLabel(body, text="", justify="left")
        autowrap(self.progress_label)
        self.log = FoldTLabel(body, text="", justify="left", foreground=MUTED_FG)
        self.log.pack(anchor="w", fill="x", pady=(2, 0))
        autowrap(self.log)
        self.error = FoldLabel(body, text="", anchor="w", justify="left", fg=DANGER_FG, padx=2)
        autowrap(self.error, 40)

        # ---- 경고(노란 상자: 진행 표시 아래. 낮은 창에서도 진행이 먼저 보이게)
        self.warn = FoldLabel(body, text="", anchor="w", justify="left", bg=WARN_BG, fg=WARN_FG, padx=10, pady=6)
        self.warn.pack(fill="x", pady=(4, 2))
        autowrap(self.warn, 40)
        # ---- 이 PC 이더넷 주소 자동 맞추기(stage39, 접이식)
        self.net = NetSection(body, self.c, "receiver", self)
        self.net.pack(fill="x", pady=(6, 2))
        self._net_job = None

        # ---- 결과
        self.result = FoldLabel(self.top, text="", anchor="w", justify="left", padx=10, pady=6)
        autowrap(self.result, 40)
        self.result_note = ttk.Label(self.top, text=T.RX_DONE_NOTE, justify="left")
        autowrap(self.result_note)
        self.open_btn = ActionButton(self.top, text=T.RX_OPEN_DEST, kind="link", command=lambda: open_location(self.c.lan_dest()))
        self.place_btn = ActionButton(self.top, text=T.BTN_PL_ENTER, kind="plain", command=self._enter_place)  # stage50: 받기가 정상으로 끝났을 때만
        self.place_note = FoldTLabel(self.top, text=T.PL_ENTER_NOTE, justify="left")
        autowrap(self.place_note)
        bind_alt(self.app, self.start_btn, lambda: self.app.current == self.name)
        self.finish()
        self._render()

    # ------------------------------------------------------------------ 표시
    def on_show(self) -> None:
        self.net.refresh()
        self._render()
        self._schedule()
        self.scroll.scroll_to_top()
        (self.stop_btn if self.c.lan_receive_active() else self.start_btn).focus_set()

    def on_event(self, kind, payload) -> None:
        if kind in ("lan_rx", "files_changed", "net_changed", "busy"):
            self._render()

    def _schedule(self) -> None:
        if self._job is None:
            self._job = self.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        self._job = None
        try:
            self.c.lan_poll()
        finally:
            if self.app.current == self.name or self.c.lan_receive_active():
                self._schedule()

    def _render(self) -> None:
        c, f = self.c, self.c.files
        active = c.lan_receive_active()
        self.dest_var.set(c.lan_dest())
        self.dest_btn.state(["disabled"] if active else ["!disabled"])
        if f.rx_code and f.rx_state != "idle":
            code = f.rx_code
            self.code_label.configure(text=f"{code[:3]} {code[3:]}" if len(code) == 6 else code, fg=INFO_FG if active else MUTED_FG)
        else:
            self.code_label.configure(text=T.RX_CODE_NONE, fg=MUTED_FG)
        lines = [self._addr_line(a) for a in f.rx_addresses]
        applied = c.net_applied()
        if applied and applied["ip"] not in f.rx_addresses:  # 자동으로 맞춘 이 PC 주소도 보인다(구 PC 에서 이 주소로 연결)
            lines.append(T.NET_ADDR_LINE.format(ip=applied["ip"]))
        self.addr_label.configure(text="\n".join(lines) if lines else T.RX_ADDR_NONE)
        warns = [T.RX_WARN_PLAIN]
        if c.rx_public_warning():
            warns.append(T.RX_WARN_PUBLIC)
        if active or f.rx_state == "idle":
            warns.append(T.RX_WARN_FIREWALL)
        self.warn.configure(text="\n".join(warns))
        self.state_label.configure(text=T.RX_STATE.get(f.rx_state, T.RX_STATE["idle"]))
        self._progress()
        self.log.configure(text="\n".join(f.rx_log[-5:]))
        if f.rx_error and f.rx_state in ("error", "idle", "stopped"):
            self.error.configure(text=f.rx_error)
            self.error.pack(anchor="w", fill="x", pady=(4, 0), after=self.log)
        else:
            self.error.pack_forget()
        self._result()
        self.net.refresh()
        if f.rx_state in ("done", "stopped") and c.net_applied() and self._net_job is None:  # 받기가 끝나면 주소를 원래대로 되돌린다
            self._net_job = self.after(100, self._net_auto_restore)
        for b in (self.stop_btn, self.start_btn):
            b.pack_forget()
        if active:
            self.stop_btn.pack(side="right")
        else:
            self.start_btn.pack(side="right")
            self.start_btn.emphasize(True)

    def _net_auto_restore(self) -> None:
        self._net_job = None
        self.c.net_restore_if_applied()

    def _addr_line(self, addr: str) -> str:
        kind = T.RX_ADDR_KINDS.get(self.c.files.rx_kinds.get(addr, ""), "")
        return f"{addr}   ({kind})" if kind else addr

    def _progress(self) -> None:
        f = self.c.files
        info = f.rx_info
        if f.rx_state != "receiving" or info is None:
            self.bar.pack_forget()
            self.progress_label.pack_forget()
            return
        total = info.total_bytes or (f.rx_plan or {}).get("bytes", 0)
        self.bar.configure(value=(min(1.0, info.done_bytes / total) * 100) if total else 0)
        speed = f"{fmt_bytes(info.speed_bps)}/s" if info.speed_bps > 0 else T.RX_SPEED_NONE
        text = (T.RX_PROGRESS.format(done=fmt_bytes(info.done_bytes), total=fmt_bytes(total), speed=speed,
                                     eta=fmt_duration(info.eta_seconds)) if total else T.RX_PROGRESS_SHORT.format(done=fmt_bytes(info.done_bytes)))
        self.progress_label.configure(text=text)
        self.bar.pack(fill="x", pady=(0, 2), after=self.state_label)
        self.progress_label.pack(anchor="w", fill="x", after=self.bar)

    def _result(self) -> None:
        text, ok = self.c.rx_result_text()
        if not text:
            for w in (self.result, self.result_note, self.open_btn, self.place_btn, self.place_note):
                w.pack_forget()
            return
        head = T.RX_RESULT_OK if ok else T.RX_RESULT_BAD
        self.result.configure(text=text + "\n" + head, bg=OK_BG if ok else WARN_BG, fg=OK_FG if ok else WARN_FG)
        self.result.pack(fill="x", pady=(2, 2))
        self.result_note.pack(anchor="w", fill="x")
        self.open_btn.pack(anchor="w", pady=(4, 0))
        if ok:  # 받은 파일을 원래 위치에 놓는 화면으로 들어가는 길(실패/중지로 끝났으면 보이지 않는다)
            self.place_btn.pack(anchor="w", pady=(6, 0))
            self.place_note.pack(anchor="w", fill="x", pady=(2, 0))
        else:
            self.place_btn.pack_forget()
            self.place_note.pack_forget()

    def _enter_place(self) -> None:
        self.guarded(self.c.place_open, S.LAN_RECV, self.c.lan_dest())

    # ------------------------------------------------------------------ 동작
    def _change_dest(self) -> None:
        p = filedialog.askdirectory(parent=self, title=T.RX_PICK_TITLE, mustexist=False)
        if p:
            self.guarded(self.c.lan_set_dest, clean_path(p.replace("/", "\\")))

    def _start(self) -> None:
        if self.guarded(self.c.lan_receive_start):
            self._schedule()

    def _stop(self) -> None:
        self.c.lan_receive_stop()

    def _leave_ok(self) -> bool:
        """받기가 진행 중이면 멈추고 나갈지 묻는다(예이면 멈춘다)."""
        if not self.c.lan_receive_active():
            return True
        if not messagebox.askyesno(T.RX_LEAVE_TITLE, T.RX_LEAVE_CONFIRM, parent=self):
            return False
        self.c.lan_receive_stop()
        return True

    def _back(self) -> None:
        if self._leave_ok():
            self.c.go(S.METHOD if self.c.data_selection() else S.FIND)

    def _home(self) -> None:
        if self._leave_ok():
            self.app.go_home()
