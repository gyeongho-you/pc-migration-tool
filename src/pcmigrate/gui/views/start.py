"""시작 화면: 구 PC / 신 PC / 대용량 복사 중 선택."""
from __future__ import annotations

from tkinter import ttk

from .. import state as S
from .common import LICENSE_NOTE, BaseView


class StartView(BaseView):
    name = S.START

    def __init__(self, app, controller) -> None:
        super().__init__(app, controller)
        self.heading("무엇을 하시겠습니까?",
                     "이 도구는 구 PC 의 업무 환경(설정, 프로그램 목록)을 새 PC 로 옮기는 것을 돕습니다. "
                     "원본은 읽기만 하며, 새로 쓰기 전에는 항상 미리보기와 확인을 거칩니다.")
        grid = ttk.Frame(self)
        grid.pack(fill="x", pady=6)
        entries = [
            ("구 PC (내보내기)", "이 PC 의 설치 프로그램과 설정을 확인하고 설정 패키지(.zip)를 만듭니다.", S.OLD_SCAN),
            ("신 PC (가져오기)", "구 PC 에서 만든 설정 패키지(.zip)를 검증하고 미리보기를 본 뒤 복원합니다.", S.NEW_PACKAGE),
            ("대용량 데이터 복사", "사진/도면/프로젝트 폴더 같은 큰 데이터를 외장 드라이브나 네트워크 폴더로 복사하고 검증합니다.", S.TRANSFER),
        ]
        for i, (title, desc, target) in enumerate(entries):
            box = ttk.LabelFrame(grid, text=title, padding=10)
            box.grid(row=0, column=i, sticky="nsew", padx=6)
            grid.columnconfigure(i, weight=1, uniform="start")
            ttk.Label(box, text=desc, wraplength=250, justify="left").pack(anchor="w", pady=(0, 8))
            ttk.Button(box, text="선택", command=lambda t=target: self.c.go(t)).pack(anchor="e")
        notes = ttk.LabelFrame(self, text="안전 원칙", padding=10)
        notes.pack(fill="x", pady=12)
        for line in ("원본(구 PC)의 파일은 읽기만 하고 수정/삭제하지 않습니다.",
                     "복원이나 복사는 미리보기를 확인한 뒤에만 실행됩니다. 덮어쓸 파일은 .bak 으로 백업됩니다.",
                     "규칙 없는 프로그램은 자동으로 포함되지 않습니다. 직접 고른 것만 포함됩니다.",
                     "비밀 정보(키, 비밀번호, 쿠키)가 들어 있을 수 있는 항목은 명시적으로 동의해야 선택할 수 있습니다.",
                     LICENSE_NOTE):
            ttk.Label(notes, text="• " + line, wraplength=860, justify="left").pack(anchor="w")
