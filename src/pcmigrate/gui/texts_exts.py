"""'찾을 파일 형식 설정' 창의 문구(stage61). texts.py 가 불러와 자기 이름공간에 넣는다(texts_place.py 와 같은 방식).

규칙은 texts.py 와 같다: 비개발자 기준 쉬운 말, 존댓말, 금지 용어와 명령줄 단어(옵션 이름, 보관 파일 확장자 등) 없음.
상수는 모두 texts(T) 의 이름으로 보인다(T.FX_*). 형식을 더하고 빼는 문구 중 일부(추가했습니다/이미 있습니다/경고)는 백엔드(find_exts)가 돌려준다.
"""
from __future__ import annotations


def install(ns: dict) -> None:
    ICON_WARN = ns["ICON_WARN"]

    # ---- 찾기 화면의 들어가는 길
    FX_LINK = "찾을 파일 형식 설정(3DF Zephyr 같은 형식 추가) ▸"
    FX_TIP = "내 파일 찾기가 어떤 파일을 도면·사진·영상·문서·압축으로 볼지 직접 정합니다. 읽기만 하며 파일은 바뀌지 않습니다."
    FX_SAVED_NOTICE = "설정을 저장했습니다. 다음에 '다시 찾기'를 누르면 반영됩니다."

    # ---- 창
    FX_TITLE = "찾을 파일 형식 설정"
    FX_INTRO = ("내 파일 찾기는 아래 형식의 파일이 있는 폴더를 옮길 후보로 보여 드립니다. 우리가 모르는 프로젝트 형식은 직접 넣어 주세요. "
                "직접 추가한 형식은 파일이 한 개만 있어도 그 폴더를 후보로 보여 드립니다.")
    FX_CATS = (("design", "설계"), ("photo", "사진"), ("video", "영상"), ("doc", "문서"), ("archive", "압축"), ("misc", "기타"))
    FX_CAT_MISC_HINT = "위에 맞지 않는 형식을 넣어 두는 곳입니다."
    FX_FILTER_MISC = "기타"  # 찾기 화면 종류별 보기 칩(직접 추가한 기타 형식이 설정에 있을 때만 보인다)
    FX_CAT_TITLE = "{label} ({n}개)"
    FX_MORE, FX_MORE_OPEN = "더 보기 ▸ ({n}개)", "접기 ▾"
    FX_USER_TIP = "직접 추가한 형식입니다. ×를 누르면 목록에서 뺍니다."
    FX_DEFAULT_TIP = "기본 형식입니다. ×를 누르면 찾지 않습니다(기본값으로 초기화하면 돌아옵니다)."
    FX_REMOVE_LABEL = "×"
    FX_REMOVED = "찾지 않도록 뺀 기본 형식:"
    FX_RESTORE = "되돌리기"
    FX_ADD_LABEL, FX_CAT_LABEL = "형식", "분류"
    FX_ADD_HINT = "점은 안 쳐도 됩니다(예: zep). 한 번에 하나씩 넣어 주세요."
    BTN_FX_ADD, BTN_FX_RESET, BTN_FX_SAVE, BTN_FX_CANCEL = "추가", "기본값으로 초기화", "저장", "취소"
    FX_DIRTY = "바뀐 것: 추가 {a}개, 삭제 {r}개 (아직 저장하지 않았습니다)"
    FX_CLEAN = "바뀐 것이 없습니다."
    FX_RESET_TITLE = "기본값으로 되돌릴까요?"
    FX_RESET_ASK = "지금까지 추가·삭제한 형식이 모두 사라지고 기본 목록으로 돌아갑니다. 이전 설정은 지우지 않고 보관해 둡니다."
    FX_CLOSE_TITLE = "저장하지 않고 닫을까요?"
    FX_CLOSE_ASK = "바꾼 내용이 아직 저장되지 않았습니다. 닫으면 바꾼 것이 사라집니다."
    FX_NOTHING_TO_SAVE = "저장할 것이 없습니다."
    FX_SAVE_FAILED = ("설정을 저장하지 못했습니다. 저장 위치에 쓸 수 있는지 확인해 주세요(원래 설정은 그대로입니다). "
                      "자세한 내용은 오류 기록에 남았습니다.")
    FX_RESET_FAILED = "기본값으로 되돌리지 못했습니다(설정은 그대로입니다). 자세한 내용은 오류 기록에 남았습니다."
    FX_DAMAGED = ("저장된 설정을 읽지 못해 기본 목록으로 보여 드립니다. 손상된 파일은 그대로 두고 복사본을 보관했습니다. "
                  "저장하면 새 설정으로 바뀝니다.")
    # ---- 내보내기 / 가져오기
    BTN_FX_EXPORT, BTN_FX_IMPORT = "내보내기", "가져오기"
    FX_EXPORT_TITLE = "찾을 파일 형식 설정 저장"
    FX_EXPORT_DEFAULT_NAME = "찾을파일형식설정.json"
    FX_IMPORT_TITLE = "가져올 설정 파일 고르기"
    FX_FILE_TYPES = "형식 설정 파일"
    FX_UNSAVED_TITLE = "저장하지 않은 변경이 있습니다"
    FX_EXPORT_UNSAVED = "아직 저장하지 않은 변경이 있습니다. 내보내기는 지금 저장된 설정을 파일로 만듭니다. 계속할까요?"
    FX_EXPORT_NOTE = "이 파일에는 확장자 목록만 들어 있고 내 PC의 경로나 이름은 들어 있지 않습니다."
    FX_IMPORT_UNSAVED = "저장하지 않은 변경이 사라집니다. 가져오기를 계속할까요?"
    FX_IMPORT_REJECT_TITLE = "이 파일은 가져올 수 없습니다"
    FX_IMPORT_REJECT_HEAD = "이 파일은 가져올 수 없습니다. 아무것도 바꾸지 않았고 기존 설정은 그대로입니다."
    FX_IMPORT_REJECT_TAIL = "이 도구의 '내보내기'로 만든 파일인지 확인해 주세요."
    FX_IMPORT_REJECT_SHORT = "가져올 수 없는 파일입니다. 기존 설정은 그대로입니다(자세한 이유는 열린 창을 확인해 주세요)."
    FX_IMPORT_CONFIRM_TITLE = "이 설정으로 바꿀까요?"
    FX_IMPORT_CONFIRM_HEAD = "파일에서 읽은 설정으로 바꿉니다(교체)."
    FX_IMPORT_CONFIRM_SIZE = "· 직접 추가한 형식 {a}개, 찾지 않도록 뺀 기본 형식 {r}개가 됩니다."
    FX_IMPORT_CONFIRM_DIFF = "· 지금 설정 대비 추가 {a}개, 삭제 {r}개가 바뀝니다."
    FX_IMPORT_CONFIRM_KEEP = "· 지금 설정은 지우지 않고 보관해 둡니다."
    FX_IMPORT_CONFIRM_TAIL = "바꿀까요?"
    FX_IMPORT_FAILED = "설정을 가져오지 못했습니다. 기존 설정은 그대로입니다."
    FX_NOTES_PREFIX = f"{ICON_WARN} "
    FX_UNEXPECTED = "설정을 처리하는 중 예기치 못한 오류가 났습니다. 자세한 내용은 오류 기록에 남았습니다."

    ns.update({k: v for k, v in locals().items() if k.startswith(("FX_", "BTN_FX_"))})
