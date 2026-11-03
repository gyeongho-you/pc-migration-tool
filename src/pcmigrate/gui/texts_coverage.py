"""'프로그램별로 옮겨지는 것과 직접 해야 할 것' 확인표(stage86, v0.3.7 안 1)의 문구. texts.py 가 불러와 자기 이름공간에 넣는다.

다른 문구 파일과 따로 둔 이유: 같은 시기에 다른 작업이 texts.py 를 고치므로 병합 충돌을 줄이려는 것이다.
규칙은 texts.py 와 같다: 비개발자 기준 쉬운 말, 존댓말, 금지 용어 없음.
"""
from __future__ import annotations


def install(ns: dict) -> None:
    ICON_OK, ICON_WARN, ICON_FAIL = ns["ICON_OK"], ns["ICON_WARN"], ns["ICON_FAIL"]
    ICON_PART = "[~]" if ns.get("TEXT_ICONS") else "△"  # 글꼴에 없으면 글자 아이콘

    COV2_TITLE = "프로그램별로 옮겨지는 것과 직접 해야 할 것"
    COV2_PREVIEW_TITLE = "이 묶음으로 이만큼 복원됩니다"
    COV2_COLS = ("프로그램", "옮겨진 설정", "직접 해야 할 것", "상태")
    COV2_ST_OK = f"{ICON_OK} 모두 옮김"
    COV2_ST_PART = f"{ICON_PART} 일부 뺌"
    COV2_ST_FAIL = f"{ICON_FAIL} 일부 못 읽음"
    COV2_ST_MANUAL = "직접 해야 함"
    COV2_PART_NOTE = "의도적으로 뺀 파일 {n}개({why}). 설정이 아닌 파일이거나 안전을 위해 옮기지 않는 것입니다."
    COV2_WHY_DEFAULT = "설정 형식이 아니거나 안전을 위해 뺌"
    COV2_FAIL_NOTE = "읽지 못한 파일 {n}개. 프로그램을 닫고 '읽지 못한 파일 다시 담기'를 눌러 주세요."
    COV2_FAIL_NOTE_NONE = "읽지 못한 파일이 있습니다. 프로그램을 닫고 '읽지 못한 파일 다시 담기'를 눌러 주세요."
    COV2_MANUAL_ONE = "{first} 외 {n}건 ▸"
    COV2_MANUAL_SINGLE = "{first} ▸"
    COV2_MANUAL_NONE = "없음"
    COV2_MANUAL_LINE = "직접 해야 할 것 {n}건"
    COV2_MISC = "기타 직접 할 일 {n}건 ▸"
    COV2_MISC_OPEN = "기타 직접 할 일 {n}건 ▾"
    COV2_FILES_TIP = "담긴 파일 {n}개"
    COV2_LEGEND = "✔ 모두 옮김   △ 일부만 옮김(뺀 이유가 적혀 있습니다)   ✖ 못 옮긴 것이 있음   해당 없음 = 이 PC에 없는 프로그램"

    ns.update({k: v for k, v in locals().items() if k.startswith("COV2_")})
