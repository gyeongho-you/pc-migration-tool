"""'바꿔 보며 설정 찾기'(설정 기록 모드) 화면의 문구(stage60). texts.py 가 불러와 자기 이름공간에 넣는다(texts_place.py 와 같은 방식).

규칙은 texts.py 와 같다: 비개발자 기준 쉬운 말, 존댓말('~해 주세요'), 금지 용어(해시/규칙/패키지/엄격 검사 등)와 명령줄 옵션 이름 없음, 'PC' 는 뒤 글자에 붙여 쓴다.
상수는 모두 texts(T) 의 이름으로 보인다(T.REC_*, T.BTN_REC_*, T.G_REC_*, T.TASK_REC_* 등).
"""
from __future__ import annotations


def install(ns: dict) -> None:
    ICON_OK, ICON_FAIL, ICON_WARN = ns["ICON_OK"], ns["ICON_FAIL"], ns["ICON_WARN"]
    RECORD = "record_settings"

    # ---- 화면 이름/제목/단계
    TITLES = ns["TITLES"]
    TITLES[RECORD] = "① 쓰던 PC에서 준비하기 · 바꿔 보며 설정 찾기"
    REC_HEADING = TITLES[RECORD]
    REC_SUB = ("프로그램에서 설정을 하나 바꾸기 전과 후의 파일 상태를 비교해, 설정이 저장되는 파일을 찾습니다. "
               "읽기만 하며 파일 내용은 저장하지 않습니다.")
    REC_STEP_NAMES = ("프로그램 닫기", "변경 전", "기준선", "설정 바꾸고 변경 후", "후보 확인", "초안 저장")
    REC_STEPS_LABEL = "진행 순서"

    # ---- 들어가는 길(① 화면의 '이 프로그램 설정 찾기' 아래 한 줄)
    REC_ENTER_NOTE = "설정 파일을 못 찾았나요? 프로그램에서 설정을 바꿔 보며 어떤 파일이 바뀌는지 보고 찾을 수 있습니다."
    BTN_REC_ENTER = "바꿔 보며 설정 찾기 ▸"

    # ---- 화면 A: 프로그램 고르기
    REC_PICK_LABEL = "이 PC에 설치된 프로그램 중에서 설정을 찾고 싶은 프로그램을 골라 주세요."
    REC_ONLY_NORULE = "자동 목록이 없는 프로그램만 보기"
    REC_FILTER_LABEL = "이름으로 거르기:"
    REC_COLS_PROGRAM = ("프로그램", "게시자", "설치 위치(일부)")
    REC_PICK_HINT = "목록에 없으면 '자동 목록이 없는 프로그램만 보기'의 체크를 꺼 주세요."
    REC_NO_PROGRAMS = "설치된 프로그램 목록을 읽지 못했거나 비어 있습니다."
    BTN_REC_NEXT_FOLDERS = "다음: 폴더 확인 ▶"
    G_REC_PICK = "프로그램을 먼저 골라 주세요."

    # ---- 화면 B: 이 폴더들을 훑습니다
    REC_FOLDERS_HEAD = "이 폴더들을 훑습니다 · {name}"
    REC_FOLDERS_NOTE = ("프로그램 이름, 게시자, 설치 위치로 찾았습니다. 이 폴더들의 파일 상태(크기·수정 시각·지문)만 기록하고, "
                        "파일 내용은 저장하지 않습니다. 읽기만 하며 아무것도 바꾸지 않습니다.")
    REC_COLS_FOLDER = ("훑기", "확신", "폴더", "이유")
    REC_EXCLUDED_CONF = "제외"
    REC_ADDED_CONF = "추가"
    REC_ADDED_REASON = "제외 후보에서 직접 추가한 폴더"
    BTN_REC_ADD_FOLDER = "폴더 직접 추가…"
    REC_ADD_FOLDER_DIALOG = "훑을 폴더를 골라 주세요"
    REC_NO_FOLDER = ("이 프로그램의 설정 폴더를 찾지 못했습니다. 폴더를 직접 추가해 주세요. "
                     "전체를 훑는 것은 오래 걸려 자동으로 하지 않습니다.")
    REC_RUNNING = (f"{ICON_WARN} {{name}} 이(가) 실행 중일 수 있습니다({{procs}}). 먼저 프로그램을 완전히 닫아 주세요. "
                   "계속할 수는 있지만 결과가 정확하지 않을 수 있습니다.")
    BTN_REC_START = "프로그램을 닫았습니다. 시작 ▶"
    REC_PREVIOUS = "이 프로그램의 이전 기록이 있습니다({done}까지 진행, {when}). 이어서 하거나 처음부터 다시 할 수 있습니다."
    BTN_REC_RESUME = "이어서 하기"
    G_REC_NO_FOLDER = "훑을 폴더가 하나도 켜져 있지 않습니다. 폴더를 체크하거나 직접 추가해 주세요."
    G_REC_HAS_PREVIOUS = "이전 기록이 있습니다. '이어서 하기'나 '처음부터 다시'를 눌러 주세요."
    G_REC_NEED_PLAN = "먼저 폴더 확인 화면에서 시작해 주세요."

    # ---- 화면 C: 기록 단계
    REC_STEP_TITLE = "지금 할 일: {step}"
    REC_TASK_BASELINE = ("프로그램을 열고 아무 설정도 바꾸지 말고 바로 닫아 주세요. 이때 바뀌는 파일은 '원래 바뀌는 파일'(기록, 창 위치 등)이라 후보에서 뺍니다.\n"
                         "완전히 닫았으면 아래 단추를 눌러 주세요.")
    BTN_REC_BASELINE = "프로그램을 닫았습니다. 기준선 찍기"
    REC_TASK_AFTER = ("프로그램을 열고 설정을 딱 하나만 바꾼 뒤 프로그램을 완전히 닫아 주세요. 닫을 때 설정을 저장하는 프로그램이 많습니다.\n"
                      "완전히 닫았으면 아래 단추를 눌러 주세요.")
    BTN_REC_AFTER = "프로그램을 닫았습니다. 변경 후 찍기({n}번째)"
    REC_AFTER_AGAIN = "다른 설정으로 한 번 더 해 보면 후보가 더 정확해집니다(2~3번 권장)."
    BTN_REC_AGAIN = "다른 설정으로 한 번 더"
    BTN_REC_CANDS = "후보 보기 ▶"
    REC_LOG_HEAD = "기록:"
    REC_LOG_LINE = "{icon} {label} · 파일 {n:,}개"
    REC_LABELS = {"before": "변경 전", "baseline": "기준선", "after": "변경 후 {n}번째"}
    REC_SNAP_DONE = "{label}을(를) 저장했습니다: 파일 {n:,}개."
    REC_UNREADABLE = "읽지 못해 건너뛴 파일이 {n}개 있습니다(크기와 수정 시각만 비교합니다). 자세한 내용은 오류 로그에 있습니다."
    REC_CLOUD = "내려받지 않고 건너뛴 클라우드 전용 파일이 {n}개 있습니다."
    REC_TRUNCATED_SNAP = "파일이 너무 많아 일부만 기록했습니다. 폴더를 줄여서 처음부터 다시 해 주세요."
    REC_NEW_FOLDER = "시작한 뒤 새로 생긴 설정 폴더가 있습니다. 이번 기록에는 포함하지 않았습니다. 포함하려면 처음부터 다시 해 주세요."
    REC_RESULT_SAVED_NOTE = "결과 보고서: {path}"

    # ---- 화면 D: 후보 표
    REC_CANDS_HEAD = "설정 파일 후보 · {name} · 변경 후 {rounds}회, 기준선 {base}회"
    REC_CANDS_NOISE = "기준선에서 바뀐 파일 {n}개는 제외했습니다(기록, 창 위치 등)."
    REC_COLS_CAND = ("담기", "확신", "파일", "크기", "이유")
    REC_CONF_REF = "참고"
    REC_SECRET_NOTICE = f"{ICON_WARN} 로그인·라이선스 등 민감한 정보일 수 있는 파일 {{n}}개는 후보에서 뺐습니다. 이 도구는 라이선스와 로그인 정보를 옮기지 않습니다."
    REC_SECRET_FOLD = "뺀 민감 파일 {n}개 보기 ▸"
    REC_SECRET_FOLD_OPEN = "뺀 민감 파일 {n}개 접기 ▾"
    REC_CAND_NOTE = "체크한 파일만 자동 목록 초안에 담습니다. '참고' 줄은 담을 수 없습니다."
    BTN_REC_BACK_AGAIN = "◀ 한 번 더 바꿔 보기"
    BTN_REC_TO_CANDS = "◀ 후보로 돌아가기"
    BTN_REC_DRAFT = "자동 목록 초안 저장 ▶"
    REC_REGISTRY = (f"{ICON_WARN} 바뀐 파일이 없었습니다. 이 프로그램은 설정을 레지스트리(Windows의 설정 저장소)에 저장할 가능성이 높아, "
                    "파일을 옮기는 방식으로는 설정을 옮길 수 없습니다. 다른 설정을 바꿔 한 번 더 해 보거나, 프로그램의 '설정 내보내기' 기능을 써 보세요.")
    REC_BASELINE_ONLY = (f"{ICON_WARN} 바뀐 파일은 있었지만 모두 기준선에서도 바뀌던 파일이라 설정 파일로 확정하지 못했습니다. "
                         "설정을 바꾼 기록을 한 번 더 찍어 보세요. 레지스트리에 저장하는 프로그램일 수도 있습니다.")
    REC_TRUNCATED = (f"{ICON_WARN} 파일이 너무 많아 일부만 기록해서 '바뀐 파일이 없다'고 판단할 수 없습니다. "
                     "폴더를 줄여서 처음부터 다시 해 주세요.")
    REC_OUTSIDE = "바뀐 파일이 이 PC의 사용자 폴더 밖에 있어 자동 목록 초안에 담을 수 없습니다. 그 위치의 파일은 직접 복사해 주세요."
    REC_ONLY_SECRET = "바뀐 파일이 로그인·라이선스 파일뿐이라 자동 목록 초안에 담을 설정 파일이 없습니다."
    REC_ONLY_REMOVED = "바뀐 파일이 삭제뿐이라 자동 목록 초안에 담을 파일이 없습니다."
    REC_NO_CANDS = "설정 파일 후보가 없습니다."
    G_REC_NONE_CHECKED = "담을 후보를 하나 이상 체크해 주세요."
    G_REC_NEED_SESSION = "먼저 기록을 시작해 주세요."
    REC_MORE_ROWS = "표에는 앞의 {shown}개만 보입니다. 전체 목록은 결과 파일에 저장되어 있습니다."

    # ---- 화면 E: 초안 저장
    REC_DRAFT_HEAD = "자동 목록 초안"
    REC_DRAFT_PATH_LABEL = "저장할 곳:"
    REC_DRAFT_PATH_HINT = "같은 이름의 파일이 있으면 지우지 않고 .bak 으로 보관한 뒤 저장합니다."
    REC_DRAFT_SUMMARY = "담길 후보: {n}개를 체크했습니다."
    BTN_REC_SAVE_DRAFT = "초안 저장"
    REC_DRAFT_SAVED = (f"{ICON_OK} 저장했습니다: {{path}}\n담긴 설정: 폴더 {{d}}개, 파일 {{f}}개 · 이름: {{rid}}\n"
                       "이 초안은 '확인 필요' 표시가 붙은 자동 생성본입니다. 확인한 뒤 프로그램의 자동 목록 폴더(rules)에 넣어 주세요.")
    REC_NO_DRAFT = "자동 목록 초안을 만들지 않았습니다. {reason}"
    BTN_REC_OPEN_FOLDER = "폴더 열기"
    REC_SAVE_DIALOG = "자동 목록 초안 저장"

    # ---- 처음부터 다시
    BTN_REC_RESET = "처음부터 다시"
    REC_RESET_TITLE = "처음부터 다시 할까요?"
    REC_RESET_CONFIRM = ("지금까지 찍은 기록(변경 전·기준선·변경 후, 후보, 초안)을 지우지 않고 같은 폴더에 .bak 파일로 보관한 뒤 빈 상태로 돌아갑니다.\n"
                         "보관된 파일은 나중에 직접 확인하거나 지울 수 있습니다.")
    REC_RESET_DONE = "이전 기록 {n}개를 보관했습니다(삭제하지 않음). 프로그램을 고르는 화면으로 돌아갑니다."
    REC_RESET_NONE = "보관할 이전 기록이 없었습니다. 프로그램을 고르는 화면으로 돌아갑니다."
    REC_RESET_KEPT = "기록 도구의 파일이 아니라 그대로 둔 파일: {names}"
    REC_WORK_NOTE = "기록 폴더: {path}"

    # ---- 작업 이름/진행/중지
    TASK_REC_PROGRAMS = "설치된 프로그램 읽기"
    TASK_REC_FOLDERS = "훑을 폴더 찾기"
    TASK_REC_SNAP = "설정 기록(파일 상태 읽기)"
    TASK_REC_DIFF = "바뀐 파일 비교"
    TASK_REC_DRAFT = "자동 목록 초안 저장"
    TASK_REC_RESET = "이전 기록 보관(처음부터 다시)"
    ns["PROGRESS"][TASK_REC_PROGRAMS] = "이 PC에 설치된 프로그램을 읽는 중… 보통 몇 초 걸립니다. 아무것도 바꾸지 않습니다."
    ns["PROGRESS"][TASK_REC_FOLDERS] = "프로그램 정보로 설정 폴더를 찾는 중… 폴더 이름만 보며 보통 몇 초 걸립니다. 아무것도 바꾸지 않습니다."
    ns["PROGRESS"][TASK_REC_SNAP] = ("고른 폴더의 파일 상태(크기·수정 시각·지문)를 읽는 중… 파일 수에 따라 몇 초에서 몇 분 걸립니다. "
                                     "읽기만 하며 아래 '중지'로 멈출 수 있습니다.")
    ns["PROGRESS"][TASK_REC_DIFF] = "기록을 비교해 바뀐 파일을 찾는 중… 보통 몇 초 걸립니다. 아무것도 바꾸지 않습니다."
    ns["PROGRESS"][TASK_REC_DRAFT] = "체크한 후보로 자동 목록 초안을 만드는 중… 보통 몇 초 걸립니다."
    ns["PROGRESS"][TASK_REC_RESET] = "이전 기록 파일을 .bak 으로 옮기는 중… 보통 몇 초 걸립니다. 지우지 않습니다."
    ns["TASK_STEPS"][TASK_REC_PROGRAMS] = ("읽기",)
    ns["TASK_STEPS"][TASK_REC_FOLDERS] = ("폴더 찾기",)
    ns["TASK_STEPS"][TASK_REC_SNAP] = ("파일 읽기", "저장")
    ns["TASK_STEPS"][TASK_REC_DIFF] = ("비교",)
    ns["TASK_STEPS"][TASK_REC_DRAFT] = ("초안 만들기",)
    ns["TASK_STEPS"][TASK_REC_RESET] = ("보관",)
    STOP_DONE_REC = ("중지했습니다. 이 기록은 저장하지 않았습니다. 같은 단계를 다시 할 수 있습니다. "
                     "대상 폴더는 읽기만 해서 바뀌지 않았습니다.")

    REC_HELP = ("무엇을 하는 화면인가요?\n자동 목록이 없는 프로그램에서 설정을 하나 바꾸기 전과 후의 파일 상태를 비교해, 설정이 저장되는 파일을 찾습니다.\n\n"
                "안전한가요?\n프로그램 폴더의 파일은 읽기만 하며 내용은 저장하지 않습니다(크기·수정 시각·지문만 기록). 기록은 이 PC의 기록 폴더에만 쓰고, "
                "'처음부터 다시'도 지우지 않고 .bak 파일로 보관합니다.\n\n"
                "다음 단계\n폴더 확인 → 변경 전 → 기준선 → 설정 바꾸고 변경 후(반복) → 후보 확인 → 초안 저장. 저장한 초안을 확인해 자동 목록에 넣으면 됩니다.")
    ns["HELP"][RECORD] = REC_HELP

    ns.update({k: v for k, v in locals().items() if k.isupper()})
