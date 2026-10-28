"""'이 PC 이더넷 주소 자동 맞추기'(stage39) 화면 문구. texts.py 가 불러와 자기 이름공간에 넣는다(병합 충돌을 줄이려고 별도 파일).

규칙은 texts.py 와 같다: 비개발자 기준 쉬운 말, 존댓말, 금지 용어 없음.
"""
from __future__ import annotations


def install(ns: dict) -> None:
    ICON_OK, ICON_WARN = ns["ICON_OK"], ns["ICON_WARN"]
    TASK_NET_APPLY, TASK_NET_RESTORE = "이더넷 주소 맞추기", "이더넷 설정 되돌리기"
    ns["PROGRESS"].update({
        TASK_NET_APPLY: "이 PC의 이더넷 주소를 바꾸는 중입니다… 관리자 권한 확인창이 뜨면 '예'를 눌러 주세요. 보통 몇 초 걸립니다.",
        TASK_NET_RESTORE: "이더넷 설정을 원래대로 되돌리는 중입니다… 관리자 권한 확인창이 뜨면 '예'를 눌러 주세요. 보통 몇 초 걸립니다.",
    })
    ns["TASK_STEPS"].update({TASK_NET_APPLY: ("적용",), TASK_NET_RESTORE: ("복구",)})
    NET_TITLE, NET_TITLE_OPEN = "이 PC 이더넷 주소 자동 맞추기 ▸", "이 PC 이더넷 주소 자동 맞추기 ▾"
    NET_INTRO = ("두 PC를 랜선으로 바로 연결했는데 연결이 안 될 때 씁니다. 이 PC의 이더넷 주소를 같은 대역으로 임시로 맞추고, "
                 "끝나면 원래대로 되돌립니다. 두 PC 모두에서 하며, 인터넷에 연결된 어댑터는 고를 수 없습니다.")
    NET_ADAPTER_LABEL = "바꿀 어댑터"
    NET_NO_ADAPTER = "랜선이 연결된 이더넷 어댑터가 없습니다. 랜선 연결을 확인해 주세요."
    BTN_NET_REFRESH = "어댑터 다시 찾기"
    BTN_NET_PLAN = "계획 보기"
    BTN_NET_APPLY = "적용"
    BTN_NET_RESTORE = "원래대로 되돌리기"
    BTN_NET_RESTORE_NOW = "지금 되돌리기"
    NET_CONFIRM_TITLE = "이더넷 주소 바꾸기"
    NET_NEED_CONFIRM = "이더넷 설정을 바꾸기 전에 확인이 필요합니다."
    NET_PLAN_FIRST = "먼저 '계획 보기'를 눌러 바꿀 내용을 확인해 주세요."
    NET_ADDR_LINE = "{ip}   (이 PC 주소를 자동으로 맞춤)"
    NET_APPLIED = f"{ICON_OK} 적용됨: {{ip}} (어댑터 {{adapter}})"
    NET_APPLIED_RECEIVER = "구 PC에서는 이 주소로 연결합니다."
    NET_APPLIED_SENDER = "새 PC 주소 칸에는 {peer} 처럼 같은 대역의 주소를 입력합니다(새 PC가 알려 준 주소가 우선입니다)."
    NET_NOT_APPLIED = "아직 바꾸지 않았습니다."
    NET_RESTORED = f"{ICON_OK} 이더넷 설정을 원래대로 되돌렸습니다."
    NET_AUTO_RESTORE_NOTE = "전송이 끝나면 자동으로 원래대로 되돌립니다."
    NET_PENDING = (f"{ICON_WARN} 이전에 바꾼 이더넷 설정이 아직 원래대로 돌아가지 않았습니다"
                   "(어댑터 {adapter}, 임시 주소 {ip}). 지금 되돌려 주세요.")
    NET_PENDING_BAD = f"{ICON_WARN} 이더넷 설정을 되돌릴 기록이 올바르지 않습니다. Windows 네트워크 설정에서 이더넷을 '자동'으로 바꿔 주세요."
    NET_SUBNET_HINT = "두 PC의 주소 대역이 달라서 연결이 안 되는 것 같습니다. 아래 '이 PC 이더넷 주소 자동 맞추기'를 열어 양쪽 PC에서 맞춰 보세요."
    NET_RESTORE_ASK_TITLE = "이더넷 설정 되돌리기"
    NET_RESTORE_ASK = "이전에 바꾼 이더넷 설정을 지금 원래대로 되돌릴까요?\n관리자 권한 확인창이 뜰 수 있습니다."

    ns.update({k: v for k, v in locals().items() if k.isupper()})
