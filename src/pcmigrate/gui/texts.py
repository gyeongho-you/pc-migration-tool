"""화면에 보이는 한국어 문구를 한 곳에 모은 모듈(비개발자 기준 쉬운 말, 일관된 존댓말).

문구를 고칠 때는 이 파일만 수정하면 된다. 테스트(tests/test_gui_texts.py)가 다음을 지킨다:
- 모든 상수가 비어 있지 않다.
- 개발 용어(dry-run, manifest, SHA-256, robocopy, extra, strict, UNC)가 사용자에게 보이는 문구에 그대로 나오지 않는다
  (필요한 경우 쉬운 말로 풀고, 괄호 안에 .bak 같은 파일 이름만 작게 덧붙인다).
- 뷰/컨트롤러 코드의 한글 문자열도 같은 금지 용어를 쓰지 않는다.
테스트는 가능하면 여기 상수를 참조하므로 문구를 바꿔도 테스트가 깨지지 않는다.
"""
from __future__ import annotations

import os

from . import icons as _icons

# 화면 이름(state.py 의 상수와 같은 값)
START, OLD_SCAN, OLD_DISCOVER, OLD_CHECKLIST, OLD_EXPORT = "start", "old_scan", "old_discover", "old_checklist", "old_export"
NEW_PACKAGE, NEW_PREVIEW, NEW_CONFIRM, NEW_RESULT, TRANSFER = "new_package", "new_preview", "new_confirm", "new_result", "transfer"
NEW_INSTALL = "new_install"

# --------------------------------------------------------------------------- 용어집
# 화면 문구는 아래 말만 쓴다(전문 용어/영어/내부 이름을 그대로 보이지 않는다). 새 문구도 이 표를 따른다.
#   해시/검증값            -> 내용 확인(복사본이 원본과 같은지 비교)
#   규칙/규칙 매칭         -> 자동 목록(이 도구가 알고 있는 프로그램 목록)
#   엄격 검사              -> 처음부터 모두 다시 확인(오래 걸림)
#   패키지/package         -> 설정 묶음 파일
#   manifest/.jsonl 파일   -> 확인용 목록 파일('자동 찾기' 버튼으로 찾는다)
#   dry-run                -> 미리보기(실제로 바꾸지 않음)
#   .bak                   -> 백업 파일(.bak)
TERM_CHECK, TERM_CHECK_DESC = "내용 확인", "복사본이 원본과 같은지 비교"
TERM_CHECK_FULL = f"{TERM_CHECK}({TERM_CHECK_DESC})"
TERM_AUTOLIST = "자동 목록"
TERM_FULL_RECHECK = "처음부터 모두 다시 확인(오래 걸림)"
TERM_BUNDLE = "설정 묶음 파일"
TERM_LISTFILE = "확인용 목록 파일"
TERM_PREVIEW = "미리보기(실제로 바꾸지 않음)"
TERM_BACKUP = "백업 파일(.bak)"

APP_TITLE = "PC 이전 도우미"
FONT_FAMILIES = ("Malgun Gothic", "맑은 고딕", "Segoe UI")
FONT_SIZE = 11  # 본문 11pt(제목 16, 소제목 12 는 views/common.py)

# --------------------------------------------------------------------------- 공통 버튼/표지
BTN_NEXT = "다음(N)"
BTN_BACK = "◀ 이전(B)"
BTN_DONE = "완료"
BTN_HOME = "처음 화면으로(H)"
BTN_CANCEL = "취소"
BTN_CLOSE = "닫기"
BTN_OK = "확인"
BTN_BROWSE = "찾아보기..."
BTN_AUTOFIND = "자동 찾기"
BTN_MAKE_ON_DESKTOP = "바탕화면에 만들기"
BTN_MORE = "자세히 ▸"
BTN_LESS = "자세히 ▾"
BTN_FONT_UP, BTN_FONT_DOWN = "A+", "A-"
TIP_FONT_UP, TIP_FONT_DOWN = "글자 크기 키우기", "글자 크기 줄이기"
MORE_BELOW = "▼ 아래에 더 있습니다(마우스 휠로 내려 보세요)"
SPACE_HINT = "목록의 줄을 선택한 뒤 Space 키(또는 클릭)로 선택/해제합니다."
BTN_HELP = "?"
HELP_TOOLTIP = "이 화면 도움말(F1)"
BTN_DETAIL_SHOW = "상세 보기 ▸"
BTN_DETAIL_HIDE = "상세 보기 ▾"
NO_DETAIL = "(자세한 정보 없음)"
READY = "준비됨"
CRUMB_HOME = "처음 화면"
# 글꼴에 ✔ ✖ ⚠ ☑ 가 없어 네모로 보이면 글자 표시([OK] [X] [!])로 바꾼다. 창을 열 때 gui/icons.py 가 글꼴을 검사해 자동으로 판단하고
# (app.main), 환경변수 PCMIGRATE_TEXT_ICONS 는 강제 옵션이다(1 = 항상 글자 표시, 0 = 항상 그림 글자).
TEXT_ICONS = os.environ.get("PCMIGRATE_TEXT_ICONS") == "1" or bool(_icons.auto_result()) and os.environ.get("PCMIGRATE_TEXT_ICONS") != "0"
ICON_OK, ICON_FAIL, ICON_WARN = ("[OK]", "[X]", "[!]") if TEXT_ICONS else ("✔", "✖", "⚠")
CHECKED, UNCHECKED, LOCKED = ("[v]", "[ ]", "-") if TEXT_ICONS else ("☑", "☐", "—")
HEADER_ADMIN = {True: "관리자 권한: 예", False: "관리자 권한: 아니오", None: "관리자 권한: 알 수 없음"}

LICENSE_NOTE = "프로그램 라이선스(정품 인증)는 옮겨지지 않습니다. 새 PC에서 각 프로그램에 다시 로그인하거나 인증해 주세요."
LICENSE_LINK = f"{ICON_WARN} 라이선스 안내 ▸"
LICENSE_LINK_TITLE = "라이선스 안내"
LICENSE_BANNER = f"{ICON_WARN} 라이선스는 복원하지 않습니다. 체크리스트로 안내만 해 드립니다. " + LICENSE_NOTE
PRIVACY_NOTE = ("이 파일에는 이 PC에 설치된 프로그램 목록과 폴더 경로 같은 개인 정보가 들어 있습니다. "
                "공유 저장소에 올리거나 다른 사람에게 보내기 전에 내용을 확인해 주세요.")
READ_ONLY_NOTE = "원본(기존 파일)은 읽기만 하며 바꾸거나 지우지 않습니다."

# --------------------------------------------------------------------------- 시작 화면
START_HEADING = "무엇을 하시겠습니까?"
START_INTRO = ("새 PC로 업무 환경을 옮기는 일을 도와드립니다. 지금 하실 일을 아래에서 하나 골라 주세요.\n"
               "쓰던 PC의 파일은 읽기만 하며 바꾸지 않습니다.")
START_ORDER_NOTE = "처음이시면 ① → ② → ③ 순서로 진행해 주세요. (① 쓰던 PC → ② 파일 옮기기 → ③ 새 PC)"
# 카드 아래의 작은 안내: 어느 PC 앞에서 하는 일인지(카드 순서와 같다)
START_WHERE = ("구 PC에서", "양쪽 PC에서: 보내는 쪽은 구 PC, 받는 쪽은 새 PC", "새 PC에서")
START_CHOICES = (
    ("① 쓰던 PC에서 준비하기\n(구 PC)", "지금 쓰던 PC 앞에 계신가요? 설치된 프로그램과 설정을 조사해서, 새 PC로 옮길 설정 묶음 파일(.zip)을 만듭니다.",
     "준비 시작하기", OLD_SCAN),
    ("② 큰 파일(도면·사진)\n옮기기(대용량 복사)", "도면·사진·프로젝트 같은 큰 폴더를 외장 드라이브나 랜선으로 옮기고, 정확히 복사됐는지 확인합니다.",
     "큰 파일 옮기기 시작", TRANSFER),
    ("③ 새 PC에서 설치하고\n가져오기(신 PC)", "새 PC 앞에 계신가요? 만들어 둔 설정 묶음 파일을 먼저 미리 확인한 뒤, 이 PC에 가져옵니다.",
     "가져오기 시작하기", NEW_PACKAGE),
)
BTN_ROADMAP_BIG = "전체 이전 순서 보기 (처음부터 끝까지 한눈에)"
SAFETY_TITLE = "안전 원칙 ▸"
SAFETY_TITLE_OPEN = "안전 원칙 ▾"
SAFETY_LINES = (
    "기존(원본) 파일은 읽기만 하고 수정하거나 지우지 않습니다.",
    f"복원이나 복사는 {TERM_PREVIEW}를 확인한 뒤에만 실행됩니다. 덮어쓸 파일은 {TERM_BACKUP}로 남깁니다.",
    "자동 목록에 없는 프로그램은 자동으로 포함되지 않습니다. 직접 고른 것만 포함됩니다.",
    "비밀번호·열쇠 파일처럼 민감한 정보가 들어 있을 수 있는 항목은 이해했다고 직접 표시해야 고를 수 있습니다.",
)

# --------------------------------------------------------------------------- 화면 제목
TITLES = {
    START: "시작", OLD_SCAN: "1단계. 프로그램 조사", OLD_DISCOVER: "2단계. 자동 목록에 없는 프로그램 확인",
    OLD_CHECKLIST: "3단계. 해야 할 일 목록", OLD_EXPORT: "4단계. 설정 묶음 파일 만들기",
    NEW_PACKAGE: "1단계. 설정 묶음 파일 선택", NEW_INSTALL: "2단계. 프로그램 설치 확인", NEW_PREVIEW: "3단계. 검사와 미리보기",
    NEW_CONFIRM: "4단계. 복원 확인", NEW_RESULT: "5단계. 결과", TRANSFER: "내 파일 옮기기 · 복사하기", "auto_run": "추천대로 한 번에",
}

# --------------------------------------------------------------------------- 진행 표시(작업 이름, 예상 시간, 메시지)
TASK_SCAN, TASK_DISCOVER, TASK_CHECKLIST = "프로그램 조사", "자동 목록에 없는 프로그램 찾기", "해야 할 일 목록 만들기"
TASK_EXPORT, TASK_PREVIEW, TASK_APPLY = "설정 묶음 파일 만들기", "설정 묶음 파일 검사와 미리보기", "복원"
TASK_INSTALL_CHECK = "프로그램 설치 확인"
TASK_FIND_PROGRAM = "이 프로그램 설정 찾기"
TASK_TRANSFER, TASK_TRANSFER_PREVIEW = "파일 복사", "복사 미리보기(실제로 바꾸지 않음)"
TASK_VERIFY, TASK_VERIFY_AGAIN, TASK_RETRY = "복사본 검사", "복사본 다시 검사", "실패한 파일만 다시 복사"
PROGRESS = {
    TASK_SCAN: "설치된 프로그램을 조사하는 중… 보통 10~60초 걸립니다.",
    TASK_DISCOVER: "자동 목록에 없는 프로그램의 설정 폴더를 찾는 중… 폴더 크기를 재느라 보통 30~90초 걸립니다.",
    TASK_CHECKLIST: "해야 할 일 목록을 만드는 중… 보통 10~60초 걸립니다.",
    TASK_FIND_PROGRAM: "고른 프로그램과 이름이 맞는 설정 폴더만 찾는 중… 보통 몇 초에서 30초 걸립니다. 읽기만 하며 아래 '중지'로 멈출 수 있습니다.",
    TASK_EXPORT: "설정 묶음 파일을 만드는 중… 파일 수에 따라 수십 초에서 몇 분 걸립니다. 원본은 읽기만 합니다.",
    TASK_PREVIEW: "설정 묶음 파일을 검사하고 미리보기를 만드는 중… 보통 몇 초에서 1분 걸립니다. 아무것도 쓰지 않습니다.",
    TASK_INSTALL_CHECK: "이 PC에 설치된 프로그램을 읽어 설정 묶음 파일의 목록과 비교하는 중… 보통 10~60초 걸립니다. 아무것도 설치하거나 바꾸지 않습니다.",
    TASK_APPLY: "복원하는 중… 파일 수에 따라 수십 초에서 몇 분 걸립니다. 덮어쓰는 파일은 백업 파일(.bak)로 남깁니다.",
    TASK_TRANSFER: "복사하고 검사하는 중… 용량에 따라 몇 분에서 몇 시간 걸릴 수 있습니다. 중지해도 나중에 이어서 할 수 있습니다.",
    TASK_TRANSFER_PREVIEW: "무엇이 복사될지 확인하는 중… 아무것도 쓰지 않습니다.",
    TASK_VERIFY: "목록 파일과 이 PC의 복사본을 하나씩 비교하는 중… 용량에 따라 몇 분에서 몇 시간 걸립니다. 읽기만 하며 아무것도 쓰지 않습니다.",
    TASK_VERIFY_AGAIN: "원본과 복사본을 다시 비교하는 중… 복사는 하지 않고 읽기만 합니다. 중지해도 됩니다.",
    TASK_RETRY: "검사가 끝나지 않았거나 실패한 파일만 다시 복사하고 검사하는 중입니다. 이미 정확한 파일은 건드리지 않습니다.",
}
STAGE_LABEL = "지금 하는 일"
BUSY_NOTE = "화면이 멈춘 것처럼 보여도 작업은 진행 중입니다. 끝날 때까지 기다려 주세요."
CANCELLING = "중지하는 중입니다…"
CANCELLED = "{task}을(를) 중지했습니다. 진행 기록은 남아 있어 같은 설정으로 다시 실행하면 이어서 합니다."
ERROR_STATUS = "문제가 생겨 작업을 마치지 못했습니다."
SCAN_PROGRESS = PROGRESS[TASK_SCAN]
CHECKLIST_PROGRESS = PROGRESS[TASK_CHECKLIST]
EXPORT_PROGRESS = PROGRESS[TASK_EXPORT]
PREVIEW_PROGRESS = PROGRESS[TASK_PREVIEW]
APPLY_PROGRESS = PROGRESS[TASK_APPLY]
GO_HOME_BUSY = "작업이 끝난 뒤에 처음 화면으로 돌아갈 수 있습니다."
CLOSE_TITLE = "종료 확인"
CLOSE_BUSY_NO_CANCEL = "설정 묶음 파일을 만들거나 복원하는 중입니다. 지금 종료하면 작업이 도중에 끊기고 되돌릴 수 없습니다(복원 중이면 일부 파일만 바뀔 수 있습니다).\n\n끝날 때까지 기다리는 것을 권장합니다. 그래도 종료하시겠습니까?"
CLOSE_BUSY = "작업이 진행 중입니다. 지금 종료하면 작업이 중단됩니다.\n(복사는 나중에 같은 설정으로 다시 실행하면 이어서 할 수 있습니다)\n\n종료하시겠습니까?"

# --------------------------------------------------------------------------- 안전 가드 메시지(컨트롤러가 사용)
GUARD_TITLE = "안전을 위해 막았습니다"
G_BUSY = "다른 작업이 진행 중입니다. 끝난 뒤에 다시 시도해 주세요."
G_NOT_IN_LIST = "목록에 없는 항목입니다."
G_SECRET_SELECT = ("비밀번호·열쇠 파일·로그인 정보 등 민감한 정보가 들어 있을 수 있는 항목입니다. "
                   "먼저 '민감한 정보가 포함될 수 있음을 이해했습니다'에 체크해야 고를 수 있습니다.")
G_SECRET_NO_ACK = "민감한 정보가 들어 있을 수 있는 항목이 선택되어 있지만, 이해했다는 표시가 없습니다."
G_NEED_CHECKLIST_FIRST = "먼저 해야 할 일 목록을 만들어 주세요."
G_NEED_SAVE_PATH = "저장할 위치를 선택해 주세요."
G_NEED_EXPORT_CONFIRM = "설정 묶음 파일을 저장할 위치를 확인하는 단계가 필요합니다."
G_NEED_PACKAGE = "복원할 설정 묶음 파일(.zip) 파일을 먼저 선택해 주세요."
G_NEED_PREVIEW = "먼저 미리보기를 만들어 주세요(설정 묶음 파일을 바꾸면 미리보기를 다시 만들어야 합니다)."
G_PACKAGE_MISSING = "설정 묶음 파일을 찾을 수 없습니다: {path}\n파일 이름과 위치를 다시 확인해 주세요."
G_OUTPUT_IS_DIR = "저장할 위치가 폴더입니다. 파일 이름까지 지정해 주세요(예: D:\\백업\\pc-package.zip). 기존 폴더는 건드리지 않았습니다."
G_OUTPUT_UNEXPANDED = "저장 위치에 %TEMP% 같은 환경변수 표시가 그대로 들어 있습니다. 실제 폴더 경로를 입력하거나 '찾아보기'로 골라 주세요."
G_PACKAGE_BLOCKED = "설정 묶음 파일 검사에 실패했습니다. 문제를 해결하기 전에는 복원할 수 없습니다."
G_NEED_CHECK_PREVIEW = "'미리보기에서 덮어쓸 파일을 확인했습니다'에 체크해 주세요."
G_NEED_BAK_UNDERSTAND = "'백업 파일(.bak)이 남는다는 것을 이해했습니다'에 체크해 주세요."
G_NEED_EXTRA_CONSENT = "직접 고른 추가 항목이 들어 있습니다. 이 항목들을 복원하겠다는 별도 동의가 필요합니다."
G_NEED_FINAL_YES = "확인 창에서 '예'를 눌러 주세요."
G_NEED_SOURCE = "원본 폴더를 선택해 주세요."
G_NEED_DEST = "대상 폴더를 선택해 주세요."
G_SOURCE_MISSING = "원본 폴더를 찾을 수 없습니다: {path}"
G_SOURCE_OK = "원본 폴더가 있습니다. 원본은 읽기만 하며 바꾸거나 지우지 않습니다."
G_DEST_INSIDE = "대상 폴더가 원본 폴더와 같거나 그 안에 있습니다. 다른 위치를 골라 주세요."
G_DEST_OK = "대상 폴더가 원본 폴더 밖에 있습니다."
G_TRANSFER_CONFIRM = "대상 폴더에 파일을 쓰기 전에 확인이 필요합니다."
G_TRANSFER_OPTIONS_CHANGED = "옵션(클라우드 파일 포함/처음부터 모두 다시 확인/조각으로 나눠 복사)이 미리보기 때와 달라졌습니다. 바뀐 옵션으로 '미리보기 실행'을 다시 해서 확인해 주세요."
G_TRANSFER_NEED_PREVIEW = "먼저 '미리보기 실행'으로 무엇이 복사되는지 확인해 주세요."
G_NOTHING_TO_SAVE = "저장할 결과가 없습니다."

# 버튼 옆에 보여 줄 "지금 누를 수 없는 이유"
WHY_SCAN_FIRST = "먼저 '조사 시작'을 눌러 주세요."
WHY_PREVIEW_FIRST = "미리보기를 먼저 실행해 주세요."
WHY_PATH_PROBLEM = "원본과 대상 폴더를 올바르게 선택해 주세요."
WHY_NEED_OUTPUT = "저장할 파일 위치를 먼저 선택해 주세요."
WHY_BUSY = "다른 작업이 끝날 때까지 기다려 주세요."
WHY_PACKAGE_FIRST = "복원할 설정 묶음 파일을 먼저 선택해 주세요."
WHY_CHECKLIST_FIRST = "먼저 '목록 만들기'를 눌러 주세요."

# --------------------------------------------------------------------------- 구 PC 1) 조사
SCAN_HEADING = TITLES[OLD_SCAN]
SCAN_SUB = "이 PC에 설치된 프로그램을 읽고, 알려진 프로그램의 자동 목록과 맞춰 봅니다. 읽기만 하며 아무것도 쓰지 않습니다."
BTN_SCAN = "조사 시작"
SCAN_IDLE = "아직 조사하지 않았습니다."
SCAN_RESULT = "설치 프로그램 {programs}개를 확인했습니다. 자동 목록의 프로그램 {rules}개{more}"
SCAN_RESULT_EXTRA = ", 자동 목록에 없는 프로그램 {n}개"
SCAN_TABLE_TITLE = "자동 목록의 프로그램(설정을 자동으로 모을 수 있는 프로그램)과 맞춘 근거"
COL_PROGRAM_RULE, COL_EVIDENCE = "프로그램", "근거"

# --------------------------------------------------------------------------- 구 PC 2) 자동 목록에 없는 프로그램
DISC_HEADING = TITLES[OLD_DISCOVER]
DISC_SUB = "자동 목록에 없는 프로그램의 설정 폴더 후보입니다. 설정 묶음 파일에 넣을 항목만 직접 골라 주세요."
BTN_DISCOVER = "찾기 시작"
DISC_HINT = "읽기만 합니다. 폴더 크기를 재느라 1분 안팎 걸릴 수 있으며, 아래 '중지'로 멈출 수 있습니다."
DISC_SUMMARY = "자동 목록에 없는 프로그램 {n}개, 설정 폴더 후보 {m}개 (민감한 정보 가능성 {k}개)"
DISC_COLS = ("선택", "주의", "프로그램", "위치", "크기(캐시 제외)", "파일 수", "연결 근거")
DISC_COL_SIZE_ALL = "크기(캐시 포함)"
DISC_WARN_SECRET = f"{ICON_WARN} 민감"
DISC_WARN_WEAK = "근거 약함"
DISC_BANNER = ("{icon} 민감한 정보가 들어 있을 수 있는 항목 {n}개는 붉게 표시됩니다(열쇠 파일, 비밀번호, 로그인 정보 등). "
               "정말 필요할 때만 아래 항목에 체크한 뒤 골라 주세요.")
DISC_ACK = f"{ICON_WARN} 민감한 정보(열쇠 파일, 비밀번호 등)가 포함될 수 있음을 이해했습니다"
DISC_ACK_DIALOG_TITLE = "민감한 정보 포함 확인"
DISC_ACK_DIALOG = ("열쇠 파일, 클라우드 로그인 정보, 브라우저에 저장된 비밀번호 같은 민감한 정보가 들어 있을 수 있는 항목을 "
                   "고를 수 있게 합니다.\n설정 묶음 파일을 안전하게 보관하실 수 있습니까?")
DISC_SELECTED = "선택됨 {n}개 / 전체 {m}개"
DISC_SELECT_ALL = "보이는 항목 전체 선택(A)"
DISC_CLEAR_ALL = "보이는 항목 전체 해제(R)"
DISC_BULK_SCOPE = "두 단추는 지금 표에 보이는 항목만 바꿉니다. 표를 걸러서 보고 있다면 보이는 줄만 대상입니다."
DISC_BULK_SKIPPED_SECRET = "비밀 정보가 들어 있을 수 있는 {k}개는 건드리지 않았습니다. 직접 골라 주세요."
DISC_BULK_SELECTED = "{n}개를 선택했습니다."
DISC_BULK_CLEARED = "보이는 항목 {n}개의 선택을 모두 해제했습니다."
DISC_SELECTED_BIG = " (1GB 이상 {n}개 — 큰 폴더는 '② 내 파일 옮기기'를 권장합니다)"
DISC_DETAIL = "{kind}: {path}\n연결 근거: {grade}{reason}"
DISC_DETAIL_SECRET = "\n" + ICON_WARN + " 민감한 정보 가능성: {names}"
DISC_TOTAL = "선택한 {n}개: 약 {size}, 파일 {files}개{note}"
DISC_TOTAL_NONE = "선택한 항목이 없습니다."
DISC_TOTAL_CACHE = " (캐시 제외 후 — 임시 저장물 약 {size}, 파일 {files}개는 빠집니다)"
DISC_TOTAL_CACHE_IN = " (캐시 폴더도 포함하도록 설정됨)"
DISC_TOTAL_APPROX = " ※ 시간 안에 다 재지 못한 {k}개는 지금까지 센 만큼만 더했습니다(실제는 더 큽니다)"
DISC_TOTAL_UNKNOWN = " ※ 크기를 알 수 없는 {k}개는 합계에 넣지 못했습니다"
DISC_DETAIL_CACHE = "\n전체 크기 {total}(파일 {files}개) 중 임시 저장물(Cache, node_modules 등) 약 {cache}(파일 {cfiles}개)는 내보낼 때 기본으로 빠집니다."
# 이 프로그램 설정 찾기(규칙 없는 프로그램 하나의 설정 폴더만 국소적으로 찾는다)
FIND_LABEL = "프로그램 하나만 자세히 찾기:"
FIND_BTN = "이 프로그램 설정 찾기"
FIND_HINT = ("목록에서 프로그램을 고른 뒤 누르면, 그 프로그램의 이름·제조사·설치 위치와 이름이 맞는 폴더만 찾아 표에 더합니다. "
             "더해진 항목은 자동으로 체크되지 않습니다.")
FIND_PICK = "(프로그램 선택)"
FIND_RESULT = "'{name}' 설정 찾기: 새 후보 {new}개(이미 목록에 있던 {dup}개). 검색에 쓴 단어: {tokens}"
FIND_RESULT_RULE = "'{name}' 은(는) 이미 자동 목록에 있는 프로그램이라 설정을 자동으로 옮깁니다. 따로 찾을 필요가 없습니다."
FIND_RESULT_NONE = "'{name}' 와 이름이 맞는 설정 폴더를 찾지 못했습니다. 프로그램의 '설정 내보내기' 기능이나 설치 폴더를 직접 확인해 주세요."
FIND_INSTALL_FILES = "설치 폴더 안의 설정 형식 파일 {n}개(참고용, 자동으로 옮기지 않습니다): {names}"
FIND_BUDGET = " 시간 상한에 걸려 일부 크기는 '이상'으로 표시됩니다."
G_FIND_NEED_DISCOVERY = "먼저 '찾기 시작'으로 자동 목록에 없는 프로그램을 찾아 주세요."
G_INCLUDE_CACHES_UNSUPPORTED = "이 버전은 캐시 폴더 포함 옵션을 지원하지 않습니다."
G_LICENSE_SELECT = "라이선스는 이 도구가 옮기지 않습니다. 새 PC 에서 재활성화/재로그인해 주세요."
FIND_SCOPE_NOTE = ("캐시/로그/임시 폴더도 포함해 찾았습니다. 배지가 붙은 항목은 직접 확인 후 체크해 주세요(자동 선택 안 함). "
                   "라이선스는 발견만 표시하며 이 도구는 옮기지 않습니다.")
G_FIND_NEED_PROGRAM = "어느 프로그램의 설정을 찾을지 목록에서 먼저 골라 주세요."
KIND_FOLDER, KIND_FILE, NO_PROGRAM = "폴더", "파일", "(연결된 프로그램 없음)"

# --------------------------------------------------------------------------- 구 PC 3) 해야 할 일 목록
CL_HEADING = TITLES[OLD_CHECKLIST]
CL_SUB = "새 PC에서 직접 해야 할 일입니다. 체크하면서 진행하고, 파일로 저장해 두면 나중에도 볼 수 있습니다."
BTN_CL_MAKE = "목록 만들기"
BTN_CL_SAVE = "파일로 저장..."
CL_PRIVACY = f"{ICON_WARN} 저장한 파일에는 개인 정보가 들어 있습니다."
CL_TABS = ("요약", "1. 먼저 설치", "2. 로그인·인증", "3. 데이터·기타", "참고")
CL_SHOW_ALL = "이 PC에 관련 없는 경고도 모두 보기"
CL_HIDDEN = "이 PC에 관련 없는 경고 {n}건을 숨겼습니다."
CL_FILTER = "검색:"
CL_EMPTY = "(해당 없음)"
CL_PROGRESS_MARK = "체크 {done}/{total}"
CL_SAVE_TITLE = "해야 할 일 목록 저장"
CL_SAVE_CONFIRM = "다음 위치에 저장합니다:\n{path}\n\n개인 정보 주의: " + PRIVACY_NOTE + "\n(같은 이름의 파일이 있으면 .bak 파일로 보관합니다)"
SAVED_TITLE = "저장 완료"
SAVED_BODY = "저장했습니다:\n{path}"
CL_GUIDE = (
    "Windows 앱 설치 도구(winget)로 한 번에 설치할 수 있는 프로그램이 있습니다. 먼저 신 PC의 명령 프롬프트에서 아래 명령으로 "
    "설정 묶음 파일 안의 프로그램 목록(winget.json)을 꺼내세요(폴더는 아무 곳이나 됩니다):",
    "pcmigrate winget-extract 설정묶음.zip --output 폴더경로",
    "그런 다음 꺼낸 winget.json 으로 아래 명령을 직접 실행해 주세요(이 도구가 대신 실행하지는 않습니다):",
    "winget import -i 폴더경로\\winget.json --ignore-unavailable --accept-package-agreements --accept-source-agreements",
)
CL_SUMMARY_INSTALL = "1. 새 PC에서 먼저 설치: 직접 설치 {a}건, 설치 도구로 개별 설치 {b}건 (설치 도구로 한 번에 설치 {c}건은 별도)"
CL_SUMMARY_LOGIN = "2. 로그인·인증: {n}건"
CL_SUMMARY_DATA = "3. 데이터 복사·기타: {n}건"
CL_SUMMARY_REF = "참고(따로 설치하지 않아도 되는 것으로 보임): {n}건"
CL_SUMMARY_OMITTED = "이 PC에 관련 없는 경고 {n}건은 숨겼습니다('모두 보기'로 확인할 수 있습니다)."
CL_SUMMARY_DISC = "자동 목록에 없는 프로그램 {n}개{m} — '자동 목록에 없는 프로그램 확인' 화면에서 직접 고른 것만 설정 묶음 파일에 들어갑니다."
CL_SUMMARY_DISC_M = ", 설정 폴더 후보 {m}개"
CL_SUMMARY_LICENSE = "라이선스는 복원되지 않습니다. 모든 항목은 직접 확인해 주세요."
CL_HIDDEN_MARK = "(이 PC에서 감지되지 않음)"  # checklist.py 가 붙이는 표시와 같아야 한다(테스트로 확인)

# --------------------------------------------------------------------------- 구 PC 4) 설정 묶음 파일 만들기
EXP_HEADING = TITLES[OLD_EXPORT]
EXP_SUB = "알려진 프로그램의 설정·템플릿·단축키와 프로그램 목록을 하나의 .zip 파일로 만듭니다. " + READ_ONLY_NOTE
EXP_PATH_LABEL = "저장할 파일:"
EXP_DEFAULT_NAME = "pcmigrate-settings.zip"
EXP_PATH_HINT = "기본은 바탕화면입니다. 탐색기 주소창의 경로를 복사해 붙여넣어도 됩니다(따옴표는 자동으로 지웁니다)."
BTN_EXP_DESKTOP = BTN_MAKE_ON_DESKTOP
DRIVE_PICK = "드라이브 선택"
DRIVE_PICK_PLACEHOLDER = "드라이브 선택 ▾"
DRIVE_KINDS = {"removable": "외장·이동식", "fixed": "내장 또는 USB 외장 디스크", "network": "네트워크", "cdrom": "CD/DVD",
               "ramdisk": "램디스크", "unknown": "종류 모름"}
DRIVE_ITEM = "{root}  ({kind}, 여유 {free})"
DRIVE_NONE = "(연결된 드라이브를 찾지 못했습니다)"
DRIVE_FOLDER_DEFAULT = "이전자료"
BTN_EXPORT = "설정 묶음 파일 만들기"
EXP_SAVE_TITLE = "설정 묶음 파일 저장 위치"
EXP_PLAN = "포함될 내용: 자동 목록의 프로그램 {rules}개의 설정 + 프로그램 목록 + 직접 고른 항목 {n}개."
EXP_PLAN_SECRET = "\n" + ICON_WARN + " 고른 항목 중 민감한 정보 가능성이 있는 것이 {k}개 들어갑니다. 설정 묶음 파일을 안전하게 보관해 주세요."
EXP_PLAN_NONE = "\n(자동 목록에 없는 항목은 고르지 않았으므로 들어가지 않습니다.)"
EXP_NEED_PATH_TITLE = "저장 위치"
EXP_NEED_PATH = "저장할 파일 위치를 먼저 선택해 주세요."
EXP_CONFIRM_TITLE = "설정 묶음 파일 만들기 확인"
EXP_CONFIRM = ("다음 위치에 설정 묶음 파일(.zip)을 만듭니다:\n{path}{exists}\n\n"
               "기존(원본) 파일은 읽기만 하고 바꾸지 않습니다. 계속하시겠습니까?")
EXP_EXISTS = "\n※ 같은 이름의 파일이 있으면 백업 파일(.bak)로 보관합니다."
BTN_OPEN_LOCATION = "결과 위치 열기"
EXP_DONE = "설정 묶음 파일을 만들었습니다: {path}"
EXP_RULES = "포함된 자동 목록: {rules}"
EXP_NO_RULES = "(없음)"
EXP_COUNTS = "파일 {files}개, 건너뜀(옮기지 않음) {skipped}개, 읽지 못함(실패) {failed}개"
EXP_FAILED_HEAD = f"{ICON_WARN} 읽지 못해 제외된 파일이 있습니다(아래 예시). Chrome 같은 프로그램이 실행 중이면 닫고 다시 만들어 보세요."
EXP_RESUME_HINT = ("이전에 중단된 같은 저장 위치의 내보내기가 있습니다(저장점까지 파일 {n:,}개, {size}). "
                   "같은 선택이면 이어서 합니다.")
EXP_RESUME_BROKEN = "이전에 중단된 임시 파일이 있지만 기록을 쓸 수 없어({why}) 처음부터 만듭니다."
EXP_RESTART_CHECK = "처음부터 다시 만들기(이전에 만들던 내용을 버립니다)"
EXP_RESTART_TITLE = "처음부터 다시 만들기"
EXP_RESTART_CONFIRM = ("이전에 중단된 임시 파일(.part)과 진행 기록을 지우고 처음부터 다시 만듭니다.\n\n"
                       "완성된 설정 묶음 파일과 원본 파일은 건드리지 않습니다. 계속하시겠습니까?")
EXP_RESUMED_DONE = "저장점에서 이어받음: {n:,}개 파일은 다시 담지 않았습니다."
EXP_RESTART_REASON = "처음부터 만든 이유: {why}"
EXP_TODO = "새 PC에서 직접 해야 할 일(일부):"
EXP_INCLUDE_CACHES = "캐시 폴더도 포함(Cache, node_modules 등 다시 만들어지는 폴더. 용량과 시간이 크게 늘 수 있어 보통은 끕니다)"
EXP_CACHE_SKIPPED = "다시 만들어지는 임시 저장물 폴더 {n}개(파일 {files}개, {size})는 뺐습니다. 필요하면 고급 옵션에서 '캐시 폴더도 포함'을 켜 주세요."
# 설정 확인표(프로그램별로 어떤 설정이 담겼는지)
COV_TITLE = "설정 확인표 — 프로그램별로 어떤 설정이 담겼는지"
COV_COLS = ("프로그램", "옮기는 설정", "상태", "담긴 파일")
COV_FOLD = "해당 없음 {n}곳 ▸"
COV_FOLD_OPEN = "해당 없음 {n}곳 ▾"
COV_FOLD_NOTE = "이 PC에 없는 설정 위치라 건너뛰었습니다. 이 PC에 이 설정이 만들어진 적이 없을 수 있습니다(정상)."
COV_NONE = "(설정 확인표에 보여 줄 항목이 없습니다)"
COV_PREVIEW_TITLE = "설정 확인표 — 묶음 안에 어떤 설정이 담겼는지"
COV_NOEXT_HINT = "설정일 수 있는 확장자 없는 파일이 {n}개 빠졌습니다. 필요하면 폴더 전체 담기를 켜고 다시 내보내 주세요."
COV_EXC_INDENT = "   └ "
COV_EX_INDENT = "        · "
EXP_DONE_BIG = f"{ICON_OK} 설정 묶음 파일을 만들었습니다"
NEXT_TITLE = "다음에 할 일"
EXP_NEXT_STEPS = (
    "다음은 ② 내 파일 옮기기에서 이 설정 묶음을 새 PC로 보내 주세요(두 PC를 바로 연결하거나 외장 드라이브 사용).",
)
BTN_OPEN_RESULT = "결과 위치 열기"

# --------------------------------------------------------------------------- 신 PC
PKG_HEADING = TITLES[NEW_PACKAGE]
PKG_SUB = "구 PC에서 만든 설정 묶음 파일(.zip)을 골라 주세요. 이 단계에서는 아무것도 쓰지 않습니다."
PKG_LABEL = "설정 묶음 파일:"
PKG_OPEN_TITLE = "설정 묶음 파일 선택"
PKG_NOTE = "묶음 안의 모든 파일은 내용이 정확한지 확인하며, 확인에 실패하면 복원할 수 없습니다."
BTN_PREVIEW_NEXT = "검사하고 미리보기"
PKG_NEED_TITLE = "설정 묶음 파일 선택"
PKG_NEED = "설정 묶음 파일(.zip)을 먼저 선택해 주세요."
PKG_HINT = "USB 메모리 등에서 가져온 파일의 경로를 붙여넣거나 '찾아보기'로 골라 주세요."
PKG_PICK_DRIVE_FOLDER = "찾아볼 위치"

PREV_HEADING = TITLES[NEW_PREVIEW]
PREV_SUB = "아래는 복원했을 때 일어날 일입니다. 지금은 아무것도 바꾸지 않았습니다."
BTN_TO_CONFIRM = "복원 확정 단계로"
PREV_NONE = "미리보기가 없습니다. 이전 단계에서 설정 묶음 파일을 선택해 주세요."
PREV_SUMMARY = f"새로 만들 파일 {{new}}개, 덮어쓸 파일 {{over}}개({TERM_BACKUP}으로 남김), 같아서 건너뛸 파일 {{same}}개"
PREV_PROBLEMS = f"{ICON_FAIL} 설정 묶음 파일에 문제가 있어 복원할 수 없습니다:\n{{items}}\n→ 구 PC에서 설정 묶음 파일을 다시 만들어 복사해 주세요."
PREV_OK = f"{ICON_OK} 검사 통과(모든 파일 내용이 정확함)"
PREV_COLS = ("처리", "복원 위치", "백업 파일(.bak) 위치", "구분")
PREV_STATUS = {"would_copy": "새로 만듦", "would_overwrite": "덮어씀(백업함)", "skipped_identical": "같음(건너뜀)",
               "rejected": "복원 안 함(링크)", "denied": "복원 안 함(보안)",
               "missing_dir": "복원 안 함(폴더 없음)", "missing_program": "복원 안 함(프로그램 없음)"}
PREV_DENIED_NOTE = f"{ICON_WARN} 보안상 복원하지 않는 형식 {{n}}개 · 나머지는 복원됩니다"
PREV_REJECTED_NOTE = (f"{ICON_WARN} 복원 위치가 바로가기(정션/링크)를 거치는 파일 {{n}}개는 복원되지 않습니다. "
                      "링크를 없애거나 실제 폴더로 바꾼 뒤 다시 시도해 주세요(링크 너머에는 쓰지 않습니다).")
PREV_KIND_RULE, PREV_KIND_EXTRA = "자동 목록", "직접 고른 항목"
PREV_WARN_TITLE = "새 PC에서 직접 해야 할 일(자동 복원 불가)"

# 프로그램 설치 확인(묶음의 구 PC 프로그램과 이 PC의 설치 상태 비교). 도구는 설치 명령을 실행하지 않는다.
INST_HEADING = TITLES[NEW_INSTALL]
INST_SUB = ("구 PC에 있던 프로그램이 이 PC에 이미 설치됐는지 비교한 결과입니다. 이 도구는 프로그램을 설치하지 않습니다. "
            "필요하면 아래 명령을 직접 실행한 뒤 '다시 확인'을 눌러 주세요. 건너뛰어도 됩니다.")
BTN_INST_NEXT = "다음(미리보기)"
INST_BTN_COPY = "전체 설치 목록 꺼내고 명령 복사"
INST_BTN_PICK = "선택한 것만 목록 만들기"
INST_PICK_COL = "선택"
INST_PICK_COUNT = "선택 {n}개"
INST_PICK_HINT = "설치 도구(winget)로 설치 가능한 줄은 '선택' 칸을 눌러 원하는 것만 고를 수 있습니다."
INST_PICK_NONE = "선택한 프로그램이 없습니다. 표에서 '선택' 칸을 눌러 설치할 프로그램을 고른 뒤 다시 눌러 주세요."
INST_PICK_NOTHING_LEFT = "선택한 프로그램이 이미 설치되었거나 목록에서 사라졌습니다. 표에서 다시 골라 주세요."
INST_PICKED_ONLY_EACH = "선택한 프로그램은 묶음의 목록 파일에 없어서 파일은 만들지 않았습니다. 아래 명령을 한 줄씩 직접 실행해 주세요."
INST_PICK_EACH = "목록 파일 대신 프로그램마다 하나씩 설치하려면 다음 명령을 직접 실행해 주세요:"
INST_PICK_REST = "묶음의 목록 파일에 없는 프로그램은 다음 명령을 한 줄씩 직접 실행해 주세요:"
INST_BTN_RECHECK = "다시 확인"
INST_BTN_FOLDER = "폴더 바꾸기"
INST_BTN_SKIP = "건너뛰기"
INST_FOLDER_DEFAULT_NAME = "PC이전-프로그램목록"
INST_FOLDER_LABEL = "설치 목록 파일(winget.json)을 꺼낼 폴더: {folder}"
INST_FOLDER_PICK = "설치 목록을 꺼낼 폴더 선택"
INST_SUMMARY = ("전체 {total}개 · 설치됨 {installed}개(버전 다름 {diff}개) · 설치 도구(winget)로 설치 가능 {winget}개 · "
                "직접 설치 {manual}개 · 애매함 {ambiguous}개 · 설치 불필요 {not_needed}개")
INST_LEFT = f"{ICON_WARN} 아직 설치하지 않은 것(애매함 포함) {{n}}개가 남아 있습니다. 설치한 뒤 '다시 확인'을 눌러 주세요."
INST_ALL_DONE = f"{ICON_OK} 설치가 필요한 프로그램이 남아 있지 않습니다."
INST_COLS = ("표시", "상태", "프로그램", "버전", "근거")
INST_LABELS = {"installed": f"{ICON_OK} 설치됨", "installed_diff": f"{ICON_WARN} 설치됨(버전 다름)", "winget": "+ 설치 가능(winget)",
               "manual": f"{ICON_WARN} 직접 설치 필요", "ambiguous": "? 애매함", "not_needed": "- 설치 불필요"}
INST_KIND_NAMES = {"installed": "설치됨", "winget": "설치 도구(winget)로 설치 가능", "manual": "직접 설치 필요", "ambiguous": "애매함",
                   "not_needed": "설치 불필요"}
INST_VERSION_DIFF = "구 {old} → 새 {new}"
INST_SPACE_HINT = "'표시' 칸 클릭 또는 Space 키로 '설치 끝냄'을 표시해 둘 수 있습니다(기억용이며 판정에는 쓰이지 않습니다)."
INST_DETAIL = "{name} · {status} · 근거: {reason}{hint}"
INST_DETAIL_HINT = " · 알아 둘 점: {hint}"
INST_AMBIGUOUS_NOTE = "'애매함'은 이름만 비슷해서 같은 프로그램인지 확신할 수 없는 것입니다. 실제로 설치돼 있는지 직접 확인해 주세요."
INST_WINGET_MISSING = f"{ICON_WARN} 이 PC에서 설치 도구(winget)를 읽지 못했습니다. 설치 여부를 프로그램 이름만 보고 판단해서 정확도가 낮습니다."
INST_NO_WINGET_FILE = "설정 묶음 파일에 설치 도구(winget) 목록이 없거나 비어 있습니다. '직접 설치 필요' 항목을 설치해 주세요."
INST_NONE = "설치 확인 결과가 아직 없습니다. 잠시 기다리거나 '다시 확인'을 눌러 주세요."
INST_COPIED = (f"{ICON_OK} 설치 목록을 꺼내고 명령을 복사했습니다. 이 도구는 명령을 대신 실행하지 않습니다.\n"
               "1) 시작 메뉴에서 '명령 프롬프트' 또는 PowerShell 을 엽니다. 설치에 관리자 권한이 필요하면 '관리자 권한으로 실행'으로 열어 주세요.\n"
               "2) 마우스 오른쪽 클릭이나 Ctrl+V 로 붙여넣고 Enter 를 누릅니다.\n"
               "3) 끝나면 이 화면에서 '다시 확인'을 눌러 주세요.")
INST_PICKED = (f"{ICON_OK} 선택한 프로그램 {{n}}개만 담은 설치 목록을 꺼내고 명령을 복사했습니다. 이 도구는 명령을 대신 실행하지 않습니다.\n"
               + INST_COPIED.split("\n", 1)[1])
INST_COPIED_BAK = "같은 이름의 기존 파일은 덮어쓰지 않고 {bak} 로 옮겼습니다."
INST_COPIED_CMD = "복사한 명령(복사가 안 되면 아래를 직접 선택해 복사해 주세요):"
INST_INDIVIDUAL = "묶음의 목록에는 없지만 개별로 설치할 수 있는 프로그램은 다음 명령을 직접 실행해 주세요:"
INST_COPY_NOTHING = "설치 도구(winget)로 한꺼번에 설치할 프로그램이 없습니다. '직접 설치 필요' 항목을 확인해 주세요."
INST_RECHECK_NEW = f"{ICON_OK} 지난 확인보다 새로 설치된 프로그램 {{n}}개: {{names}}"
INST_RECHECK_SAME = "지난 확인과 달라진 것이 없습니다. 설치가 끝난 뒤에 다시 눌러 주세요."
INST_SKIPPED_NOTE = "프로그램 설치 확인은 건너뛰었습니다. 나중에 필요하면 '이전'으로 돌아와 다시 확인할 수 있습니다."
G_INSTALL_NEED_PACKAGE = "먼저 설정 묶음 파일을 선택해 주세요."
G_INSTALL_NO_WINGET = "설정 묶음 파일에 설치 도구(winget) 목록이 없어서 꺼낼 수 없습니다."
G_INSTALL_NOT_YET = "먼저 설치 확인 결과가 나와야 합니다."

CONF_HEADING = TITLES[NEW_CONFIRM]
CONF_SUB = "여기서부터는 실제로 파일이 만들어지거나 덮어써집니다. 아래 두 가지를 확인하면 실행할 수 있습니다."
CONF_INFO = "설정 묶음 파일: {package}\n새로 만들 파일 {new}개"
CONF_CHECK = "미리보기에서 덮어쓸 파일 목록을 확인했습니다"
CONF_BIG = "덮어쓸 파일 {over}개"
CONF_BIG_NOTE = "(미리보기에서 확인함)"
CONF_BIG_NONE = "덮어쓸 파일 없음 — 새로 만들 파일 {new}개"
CONF_CHECK_BAK = f"덮어쓰는 파일은 {TERM_BACKUP}로 남고, 되돌리려면 백업 파일 이름을 직접 원래대로 바꿔야 한다는 것을 이해했습니다"
CONF_EXTRA_TITLE = "직접 고른 추가 항목"
CONF_EXTRA_INFO = ("설정 묶음 파일에 직접 골라 넣은 파일 {n}개가 있습니다. 새 PC의 같은 위치 파일을 덮어쓸 수 있으니 "
                   "미리보기에서 내용을 확인한 뒤 동의해 주세요.")
CONF_EXTRA_CHECK = "이 항목들도 복원하는 데 동의합니다(별도 동의)"
BTN_APPLY = "복원 실행"
BTN_APPLY_N = "복원 실행(파일 {n}개 덮어씀)"
BTN_APPLY_NEW = "복원 실행(새 파일 {n}개 만듦)"
CONF_DIALOG_TITLE = "복원 실행 확인"
CONF_DIALOG = ("파일 {n}개를 복원합니다.\n덮어쓰는 파일은 백업 파일(.bak)로 남깁니다.\n"
               "되돌리려면 백업 파일을 직접 원래 이름으로 바꿔야 합니다.\n\n지금 실행하시겠습니까?")

RES_HEADING = TITLES[NEW_RESULT]
RES_NOT_YET = "아직 복원하지 않았습니다."
RES_COLS = ("구분", "위치", "내용")
RES_KIND_FAIL, RES_KIND_BAK, RES_KIND_COPY = "실패", "백업", "복사"
RES_KIND_REJECTED = "복원 안 함"
RES_BAK_DETAIL = "덮어쓰기 전 파일을 보관함(백업 파일)"
BTN_OPEN_RESTORED = "복원된 폴더 열기"
RES_ALL_OK = f"{ICON_OK} 모든 파일을 복원하고 확인했습니다."
RES_IMPORT_FAIL = (f"{ICON_FAIL} 실패한 파일이 있습니다. 아래 '실패' 줄의 내용을 확인해 주세요. "
                   "파일을 사용 중인 프로그램을 닫거나 권한·저장 공간 문제를 해결한 뒤, 같은 설정 묶음 파일로 다시 복원하면 "
                   "이미 복원된 파일은 건너뛰고 실패한 파일만 다시 시도합니다.")
BTN_OPEN_BAK = "백업 파일(.bak) 찾기 / 폴더 열기"
RES_UNDO_TITLE = "되돌리고 싶을 때"
RES_UNDO_HELP = ("덮어쓴 파일은 같은 폴더에 원래 이름 뒤에 .bak 이 붙은 백업 파일로 남아 있습니다.\n"
                 "되돌리려면 새로 복원된 파일을 지우거나 이름을 바꾼 뒤, 백업 파일의 이름에서 .bak 을 지워 원래 이름으로 바꿔 주세요.\n"
                 "'백업 파일 찾기' 버튼을 누르면 해당 폴더를 탐색기로 열어 줍니다.")
BTN_UNDO_HELP = "되돌리는 방법 ▸"
RES_NEXT_ITEMS = (
    ("new2", "프로그램마다 라이선스를 다시 인증합니다(로그인). 라이선스는 옮겨지지 않았습니다."),
    ("new5", "프로그램을 하나씩 열어 설정·템플릿이 잘 들어왔는지 확인합니다."),
    ("old6", "구 PC는 모든 확인이 끝나기 전에는 지우거나 포맷하지 마세요."),
)

# --------------------------------------------------------------------------- 대용량 복사
TR_HEADING = TITLES[TRANSFER]
TR_SUB = "복사본이 원본과 같은지 하나씩 확인합니다. 원본은 읽기만 하며 바꾸거나 지우지 않습니다."
TR_MORE_TITLE = "자세한 준비 사항 ▸"
TR_MORE_TITLE_OPEN = "자세한 준비 사항 ▾"
TR_DETAIL_NOTES = ("'클라우드 전용 파일 포함'을 켜면 파일을 내려받으므로 이 PC의 저장 상태가 바뀝니다. "
                   f"대상에 같은 이름의 다른 파일이 이미 있으면 덮어쓰기 전에 같은 폴더에 {TERM_BACKUP}로 보관합니다"
                   "(비어 있는 새 폴더를 고르면 가장 깔끔합니다). 중간에 멈추면 같은 설정으로 다시 실행해 이어서 할 수 있습니다.")
TR_SRC_HINT = "경로를 복사해 붙여넣어도 됩니다(따옴표는 자동으로 지웁니다)."
TR_OPTIONS_TOGGLE, TR_OPTIONS_TOGGLE_OPEN = "고급 옵션 ▸", "고급 옵션 ▾"
TR_LAYOUT_MORE_TITLE, TR_LAYOUT_MORE_TITLE_OPEN = "원본의 위치와 참조 경로 주의 ▸", "원본의 위치와 참조 경로 주의 ▾"
TR_STEP_NAMES = ("방식", "폴더", "미리보기", "복사", "결과")
TR_VERIFY_STEP_NAMES = ("방식", "목록·폴더", "검사", "결과")
TR_STEPS_LABEL = "진행 단계"
TR_SRC, TR_DST, TR_REPORT = "원본 폴더:", "대상 폴더:", "결과 파일 저장(선택):"
TR_DST_HINT = "외장 드라이브(E:\\)나 공유 폴더 경로(\\\\컴퓨터이름\\폴더)를 직접 입력해도 됩니다."
TR_REPORT_HINT = "복사 결과를 파일(.json)로 남깁니다."
TR_PICK_FOLDER = "폴더 선택"
TR_REPORT_TITLE = "결과 파일 저장 위치"
TR_OPTIONS = "옵션"
TR_STRICT = TERM_FULL_RECHECK
TR_STRICT_NOTE = ("이어서 복사할 때 이전 확인 기록을 믿지 않고 전부 다시 확인합니다. 내용이 다른 파일은 원본에서 다시 복사합니다"
                  f"(기존 복사본은 {TERM_BACKUP}로 보관). 시간이 더 걸립니다.")
TR_CLOUD = "OneDrive 클라우드 전용 파일도 포함 (기본 꺼짐)"
TR_CLOUD_NOTE = ("클라우드 전용 파일은 이 PC에 내용이 없어서, 복사하려면 인터넷으로 모두 내려받아야 합니다. "
                 "용량과 시간이 크게 늘 수 있어 기본으로 건너뛰고, 결과에 '클라우드 전용'으로 따로 표시합니다.")
BTN_TR_PREVIEW = "① 미리보기 실행"
BTN_TR_GO = "② 복사 시작"
BTN_TR_SAVE = "결과 파일 저장..."
BTN_TR_CLOUD = "클라우드 전용 파일 보기"
TR_CLOUD_WIN = "클라우드 전용 파일(건너뜀)"
TR_CONFIRM_TITLE = "복사 확인"
TR_CONFIRM = ("다음 위치에 복사합니다(원본은 읽기만 합니다):\n\n원본: {src}\n대상: {dst}\n\n"
              f"대상 폴더에 복사한 파일과 작업 기록용 숨김 파일이 만들어집니다. 같은 이름의 다른 파일이 이미 있으면 {TERM_BACKUP}로 보관한 뒤 복사합니다."
              "{cloud}\n\n※ CAD 등 모든 프로그램을 종료한 뒤 시작해 주세요(사용 중인 파일은 복사에 실패합니다).\n"
              "시작하시겠습니까? (진행 중 '중지'로 멈추고 나중에 이어서 할 수 있습니다)")
TR_CONFIRM_CLOUD = "\n⚠ OneDrive 클라우드 전용 파일을 포함하므로 대량으로 내려받을 수 있습니다."
TR_COLS = ("구분", "파일", "내용")
TR_PREVIEW_RESULT = ("미리보기 결과(실제로 바꾸지 않음): 새로 복사 {new}개, 덮어씀 {over}개(기존 파일은 백업 파일로 보관), 이미 같음 {same}개, "
                     "클라우드 전용(건너뜀) {cloud}개. 이상이 없으면 '② 복사 시작'을 눌러 주세요.")
TR_RESULT = "복사 결과: 복사 완료(정확함) {ok}개, 내용 다름(원본과 달라짐) {mismatch}개, 빠짐(복사본에 없음) {missing}개, 실패 {failed}개, 클라우드 전용(건너뜀) {cloud}개"
TR_RESULT_REPAIRED = ", 다시 복사해 바로잡음 {n}개"
TR_FAIL_HINT = (f"{ICON_FAIL} 정확히 복사되지 않은 파일이 있습니다. 아래 목록을 확인하고, 원인(파일을 쓰는 프로그램, 연결 끊김, 저장 공간)을 "
                "해결한 뒤 같은 설정으로 다시 실행해 주세요. 이미 정확히 복사된 파일은 건너뜁니다.")
TR_STATUS_LABELS = {"mismatch": "내용 다름(원본과 달라짐)", "missing": "빠짐(복사본에 없음)", "failed": "실패"}
TR_STATUS_ADDED, TR_STATUS_PENDING = "추가 파일", "검사 대기"

# --------------------------------------------------------------------------- 대용량 복사: 옮기는 방식(외장 / 랜선 / 같은 PC)
TR_MODE_HEADING = "어떻게 옮기시나요?"
MODE_EXTERNAL, MODE_NETWORK, MODE_LOCAL = "external", "network", "local"
TR_MODES = (
    (MODE_EXTERNAL, "외장 드라이브로 옮기기",
     "외장 드라이브에 복사한 뒤, 그 드라이브를 새 PC에 꽂아 다시 복사합니다. 가장 단순합니다."),
    (MODE_NETWORK, "랜선(네트워크)으로 옮기기",
     "구 PC에서 새 PC의 공유 폴더로 바로 복사합니다. 시간은 짧지만 네트워크 준비가 필요합니다."),
    (MODE_LOCAL, "이 PC 안에서 폴더 복사",
     "같은 PC의 다른 드라이브나 폴더로 복사합니다. 미리 정리하거나 백업할 때 씁니다."),
)
TR_STAGES = {
    MODE_EXTERNAL: ("1단계 (구 PC): 원본 → 외장 드라이브", "2단계 (새 PC): 외장 드라이브 → 새 PC"),
    MODE_NETWORK: ("1단계 (구 PC): 새 PC의 공유 폴더로 복사", "2단계 (새 PC): 복사본이 정확한지 검사"),
}
TR_STAGE_HEADING = "지금 어느 단계인가요?"
# 흐름별 입력 칸 이름: (원본 칸, 대상 칸)
TR_FLOW_LABELS = {
    (MODE_EXTERNAL, 1): ("구 PC의 원본 폴더:", "외장 드라이브의 폴더:"),
    (MODE_EXTERNAL, 2): ("외장 드라이브의 폴더(1단계에서 복사한 폴더):", "새 PC의 폴더:"),
    (MODE_NETWORK, 1): ("구 PC의 원본 폴더:", "새 PC의 공유 폴더:"),
    (MODE_LOCAL, 1): ("원본 폴더:", "대상 폴더:"),
}
TR_FLOW_DST_HINT = {
    MODE_EXTERNAL: "예: E:\\이전자료 (외장 드라이브의 빈 폴더)",
    MODE_NETWORK: "예: \\\\192.168.50.2\\이전 (새 PC의 IP 주소와 공유 이름)",
    MODE_LOCAL: "외장 드라이브(E:\\)나 공유 폴더 경로(\\\\컴퓨터이름\\폴더)를 직접 입력해도 됩니다.",
}
# 흐름별로 검사가 어떻게 이루어지는지(화면에 항상 보여 줍니다)
TR_VERIFY_METHOD = {
    (MODE_EXTERNAL, 1): "검사 방법: 외장 드라이브에 복사한 파일을 다시 읽어 원본과 같은지 확인합니다. 끝나면 확인용 목록 파일이 외장 드라이브에 함께 저장됩니다.",
    (MODE_EXTERNAL, 2): "검사 방법: 외장 드라이브의 확인용 목록 파일과 새 PC에 복사된 파일을 비교합니다. 외장 드라이브의 원본 파일을 다시 읽지 않아 빠릅니다.",
    (MODE_NETWORK, 1): "검사 방법: 복사하면서 원본의 내용 확인 값을 계산해 확인용 목록 파일로 저장합니다. 네트워크로 복사본을 다시 읽지 않아 시간을 아낍니다. 정확한지는 새 PC에서 2단계로 확인합니다.",
    (MODE_NETWORK, 2): "검사 방법: 새 PC에 있는 복사본을 확인용 목록 파일과 비교합니다. 구 PC와 네트워크를 거치지 않고, 복사본은 읽기만 합니다.",
    (MODE_LOCAL, 1): "검사 방법: 복사한 파일을 다시 읽어 원본과 같은지 하나씩 확인합니다.",
}
TR_FLOW_STEPS = {
    (MODE_EXTERNAL, 1): ("구 PC의 CAD·도면 프로그램을 모두 종료합니다.", "외장 드라이브(NTFS 로 포맷, USB 3.x 포트)를 꽂습니다.",
                         "미리보기를 실행하고, 이상이 없으면 복사를 시작합니다.", "끝난 뒤 '복사 완료(정확함)'만 있고 실패가 0 인지 확인합니다.",
                         "외장 드라이브를 안전하게 제거해 새 PC로 가져갑니다."),
    (MODE_EXTERNAL, 2): ("새 PC에 외장 드라이브를 꽂습니다.", "원본 칸에는 외장 드라이브의 복사 폴더를, 대상 칸에는 새 PC의 폴더를 고릅니다.",
                         "미리보기를 실행하고, 이상이 없으면 복사를 시작합니다.", "결과에서 실패가 0 인지 확인합니다."),
    (MODE_NETWORK, 1): ("두 PC를 같은 공유기(또는 랜선으로 직접) 연결하고, 새 PC에 공유 폴더를 만듭니다(도움말 참고).",
                        "구 PC의 탐색기에서 새 PC의 공유 폴더가 열리고 파일이 만들어지는지 먼저 확인합니다.",
                        "CAD·도면 프로그램을 모두 종료한 뒤 미리보기 → 복사를 시작합니다.",
                        "끝나면 확인용 목록 파일이 새 PC의 공유 폴더에 함께 저장됩니다. 새 PC에서 2단계 검사를 합니다."),
    (MODE_NETWORK, 2): ("새 PC에서 이 도구를 실행하고 '랜선(네트워크)' → '2단계'를 고릅니다.",
                        "확인용 목록 파일('자동 찾기'를 누르면 찾아 줍니다)과 복사된 폴더를 고르고 '검사 시작'을 누릅니다.",
                        "결과가 '정확함'만 있고 빠짐/내용 다름이 0 이면 완료입니다."),
    (MODE_LOCAL, 1): ("프로그램을 종료합니다.", "미리보기를 실행하고, 이상이 없으면 복사를 시작합니다."),
}
TR_FLOW_TITLES = {
    (MODE_EXTERNAL, 1): "외장 드라이브 1단계 (구 PC)", (MODE_EXTERNAL, 2): "외장 드라이브 2단계 (새 PC)",
    (MODE_NETWORK, 1): "랜선 1단계 (구 PC)", (MODE_NETWORK, 2): "랜선 2단계 (새 PC)", (MODE_LOCAL, 1): "같은 PC 안에서 복사",
}
TR_STEPS_HEADING = "이 단계에서 할 일"
# 복사 전 안내(모든 프로그램 종료, 전원, 외장 드라이브 형식)
TR_PRECHECK_TITLE = "복사를 시작하기 전에 확인해 주세요"
TR_PRECHECK_LINES = (
    "CAD 등 모든 프로그램을 종료해 주세요. 사용 중인 파일은 복사와 검사에 실패합니다.",
    "노트북은 전원을 연결해 주세요. 복사하는 동안 이 도구가 PC가 잠들지 않게 하지만, 전원이 꺼지면 멈춥니다.",
    "외장 드라이브는 NTFS 로 포맷된 것을 쓰고 USB 3.x 포트에 꽂아 주세요.",
    "검사가 끝나 실패가 0 인 것을 확인하기 전에는 구 PC의 원본을 지우거나 PC를 포맷하지 마세요.",
)
TR_CONFIRM_FLOW = "\n\n방식: {flow}\n{method}"
# 대상 드라이브 정보(종류/파일 시스템/여유 공간)
TR_KIND_LABELS = {
    "removable": "이동식 드라이브", "fixed": "고정 드라이브(USB 외장 SSD·HDD 도 이렇게 보일 수 있습니다)",
    "network": "네트워크 공유 폴더", "cdrom": "CD/DVD 드라이브", "ramdisk": "RAM 디스크", "unknown": "종류를 알 수 없음",
}
TR_DEST_INFO = "대상 드라이브: {kind} · 파일 시스템 {fs} · 여유 공간 {free}"
TR_FS_UNKNOWN = "알 수 없음"
TR_FS_UNKNOWN_NOTE = "알 수 없음(네트워크 경로는 조회되지 않을 수 있습니다)"
TR_FAT32_WARN = (f"{ICON_WARN} 대상이 FAT32 입니다. 4GB 보다 큰 파일은 저장할 수 없어서 그런 파일이 있으면 복사를 시작하기 전에 막습니다. "
                 "NTFS 로 포맷하거나 다른 드라이브를 써 주세요.")
TR_EXFAT_WARN = f"{ICON_WARN} 대상이 exFAT 입니다. 4GB 넘는 파일도 저장되지만 전원이 갑자기 꺼지면 손상되기 쉽습니다. 가능하면 NTFS 를 권장합니다."
TR_NETWORK_NOTE = "대상이 네트워크 공유 폴더입니다. 속도가 11MB/s 보다 계속 느리면 연결 속도(100Mbps 여부)를 확인하라는 안내가 나옵니다."
TR_CDROM_BLOCK = "대상이 CD/DVD 드라이브입니다. 쓸 수 있는 드라이브를 골라 주세요."
TR_SPACE_BLOCK = f"{ICON_FAIL} 대상 드라이브의 여유 공간이 부족합니다. 필요 약 {{need}}(원본 {{total}} + 여유 5%), 현재 여유 {{free}}. 복사를 시작할 수 없습니다."
TR_SPACE_OK = f"{ICON_OK} 여유 공간이 충분합니다. 필요 약 {{need}}, 현재 여유 {{free}}."
TR_SPACE_UNKNOWN = f"{ICON_WARN} 대상의 여유 공간을 조회하지 못했습니다. 복사 중에 공간이 부족하면 멈출 수 있으니 직접 확인해 주세요."
TR_FAT32_BLOCK = (f"{ICON_FAIL} 대상이 FAT32 인데 4GB 보다 큰 파일이 있습니다({{big}}개). 복사를 시작할 수 없습니다. "
                  "NTFS 로 포맷하거나 다른 드라이브를 써 주세요.")
WHY_SPACE = "대상 드라이브의 여유 공간이 부족합니다."
WHY_FAT32 = "대상이 FAT32 라서 4GB 보다 큰 파일을 저장할 수 없습니다."
WHY_STAGE2_MANIFEST = "외장 드라이브 폴더에서 목록 파일을 찾지 못해, 원본을 다시 읽어 검사합니다(시간이 더 걸립니다)."
TR_MANIFEST_MISSING_NOTE = f"{ICON_WARN} " + WHY_STAGE2_MANIFEST
TR_MANIFEST_FOUND_NOTE = f"{ICON_OK} 외장 드라이브의 목록 파일을 찾았습니다. 이 목록으로 새 PC의 복사본을 검사합니다."
# 드라이브 분리 / 재연결
TR_DISCONNECTED = "드라이브가 분리되었습니다. 다시 연결하면 이어받습니다."
TR_DISCONNECTED_TITLE = "드라이브 연결이 끊어졌습니다"
TR_RECONNECT_NOTE = "다시 연결한 뒤 같은 설정으로 복사를 다시 시작해 주세요. 이미 정확히 복사된 파일은 건너뜁니다. 드라이브 문자가 바뀌었는지 확인해 주세요."
# 속도 힌트 두 종류는 progress.py 가 만든다(외장: USB 2.0 확인 / 네트워크: 100Mbps 링크 확인)
# 검사 화면(2단계: 목록 파일과 비교)
TR_MANIFEST_LABEL, TR_TARGET_LABEL = "확인용 목록 파일:", "검사할 폴더:"
TR_MANIFEST_HINT = "복사할 때 함께 만들어진 파일입니다('자동 찾기'를 눌러 보세요)."
TR_TARGET_HINT = "새 PC에 복사된 폴더입니다. 이 폴더는 읽기만 하며 아무것도 쓰지 않습니다."
TR_PICK_MANIFEST = "확인용 목록 파일 선택"
TR_AUTOFIND_OK = f"{ICON_OK} 확인용 목록 파일을 찾았습니다."
TR_AUTOFIND_NONE = f"{ICON_WARN} 확인용 목록 파일을 찾지 못했습니다. 구 PC에서 복사할 때 함께 저장한 위치(공유 폴더나 외장 드라이브)를 '찾아보기'로 직접 골라 주세요."
TR_AUTOFIND_NEED_FOLDER = "먼저 검사할 폴더나 복사 대상 폴더를 선택해 주세요."
TR_FILE_KIND = "확인용 목록 파일"
BTN_TR_VERIFY = "검사 시작"
BTN_TR_VERIFY_AGAIN = "검사만 다시"
BTN_TR_RETRY = "실패한 것만 다시 시도"
TR_VERIFY_AGAIN_NOTE = "복사는 하지 않고 원본과 복사본을 다시 읽어 비교합니다(복사본은 항상 다시 읽습니다)."
TR_VERIFY_CONFIRM_TITLE = "검사 확인"
TR_VERIFY_CONFIRM = ("목록 파일과 다음 폴더를 비교합니다:\n\n목록: {listfile}\n폴더: {target}\n\n"
                     "폴더는 읽기만 하며 바꾸거나 지우지 않습니다. 시작하시겠습니까?")
TR_VERIFY_RESULT = ("검사 결과: 정확함 {ok}개, 내용 다름(원본과 달라짐) {mismatch}개, 빠짐(복사본에 없음) {missing}개, "
                    "읽지 못함(다른 프로그램이 사용 중 등) {failed}개, 확인용 목록에 없는 추가 파일 {added}개")
TR_VERIFY_OK = f"{ICON_OK} 목록에 있는 모든 파일이 정확히 복사되어 있습니다."
TR_VERIFY_EXTRA_NOTE = "확인용 목록에 없는 추가 파일은 실패가 아닙니다. 새 PC에서 따로 만든 파일일 수 있으니 확인만 해 주세요."
TR_VERIFY_INCOMPLETE = f"{ICON_FAIL} 확인용 목록 파일이 끝까지 만들어지지 않았습니다. 구 PC에서 복사를 다시 실행해 목록을 완성한 뒤 비교해 주세요."
G_NEED_MANIFEST = "확인용 목록 파일을 선택해 주세요."
G_MANIFEST_MISSING = "확인용 목록 파일을 찾을 수 없습니다: {path}"
G_NEED_TARGET = "검사할 폴더를 선택해 주세요."
G_TARGET_MISSING = "검사할 폴더를 찾을 수 없습니다: {path}"
G_REPORT_INSIDE_SOURCE = "결과 파일은 원본 폴더 밖에 저장해 주세요. 원본 폴더에는 아무것도 쓰지 않습니다."
G_REPORT_INSIDE = "결과 파일은 검사할 폴더 밖에 저장해 주세요. 검사는 폴더를 읽기만 하고 아무것도 쓰지 않습니다."
G_VERIFY_OK = "확인용 목록 파일과 검사할 폴더가 있습니다. 검사할 폴더는 읽기만 합니다."
G_NEED_COPY_FIRST = "먼저 복사를 한 번 실행해 주세요."
WHY_NEED_MANIFEST = "확인용 목록 파일과 검사할 폴더를 먼저 선택해 주세요."
# 결과 화면: 원본의 위치와 구조, 경고
TR_LAYOUT_TITLE = "원본의 위치와 구조(복원할 때 같은 위치에 두시길 권장합니다)"
TR_LAYOUT_ROOT = "원본 위치: {root}"
TR_LAYOUT_LINE = "  {name} — 파일 {files:,}개, {size}"
TR_LAYOUT_MORE = "  … 외 {n}개 폴더"
TR_LAYOUT_FILES = "(폴더 밖의 파일)"
LOC_WARNING = (f"{ICON_WARN} 복사한 위치가 원래 경로와 다릅니다. 원래: {{orig}} → 지금: {{now}}. "
               "도면의 외부 참조(xref)나 이미지·데이터 링크가 절대 경로로 저장돼 있으면 열 때 끊어질 수 있습니다. "
               "같은 드라이브 문자와 폴더 이름으로 맞추거나, 도면을 연 뒤 경로를 다시 지정해 주세요.")
XREF_NOTE = (f"{ICON_WARN} 외부 참조(xref)·이미지·데이터 단축은 절대 경로(예: D:\\프로젝트\\...)로 저장된 것이 많습니다. "
             "새 PC의 드라이브 문자와 폴더 구조를 구 PC와 같게 맞추면 가장 안전합니다. 열었을 때 참조가 비어 있으면 경로를 다시 지정해 주세요.")
TR_KEEP_AWAKE_FAILED = f"{ICON_WARN} 절전 방지를 켜지 못했습니다. 전원 설정에서 잠자기를 꺼 두어 주세요."
TR_PRESERVED_NOTE = (f"{ICON_OK} 대상에 있던 같은 이름의 다른 파일 {{n}}개는 덮어쓰기 전에 같은 폴더에 {TERM_BACKUP}로 보관했습니다. "
                     "필요 없으면 확인 후 직접 지워 주세요.")
TR_DEFERRED_NOTE = (f"{ICON_WARN} 복사는 끝났지만 복사본의 내용 확인은 아직 하지 않았습니다. "
                    "새 PC에서 '2단계'로 확인용 목록 파일과 비교해야 정확한지 알 수 있습니다.")
TR_MANIFEST_ERRORS = (f"{ICON_FAIL} 구 PC에서 읽지 못해 확인용 목록에서 빠진 파일이 {{n}}개 있습니다. 이 파일들은 검사되지 않았으므로 "
                      "'모두 정확함'이 아닙니다. 구 PC에서 해당 파일을 닫은 뒤 목록을 다시 만들어 주세요.")
TR_SKIPPED_LINKS = (f"{ICON_WARN} 바로가기/정션(링크) {{n}}개는 따라가지 않고 건너뛰었습니다. 링크 너머의 폴더는 따로 복사해야 합니다."
                    "{more}\n{paths}")
TR_SKIPPED_LINKS_MORE = " (아래는 앞 {shown}개만 표시합니다. 전체 목록은 '결과 파일 저장'으로 저장한 파일에 있습니다)"
TR_NEXT_STEP_EXT1 = "다음: 외장 드라이브를 안전하게 제거해 새 PC로 가져가 2단계를 진행해 주세요. 검사가 끝나기 전에는 원본을 지우지 마세요."
TR_NEXT_STEP_NET1 = "다음: 새 PC에서 이 도구를 실행해 '랜선(네트워크)' → '2단계'로 복사본을 검사해 주세요. 검사가 끝나기 전에는 원본을 지우지 마세요."
TR_DONE_REMINDER = "원본은 검사 결과에서 실패가 0 인 것을 확인한 뒤에만 정리해 주세요. 그 전에는 지우거나 포맷하지 마세요."
TR_RESULT_PENDING = "복사 완료 {pending}개(아직 내용 확인 전 — 새 PC에서 2단계 확인 필요), 실패 {failed}개, 클라우드 전용(건너뜀) {cloud}개"
TR_PENDING_LABEL = "복사 완료(내용 확인 전)"
TR_IO_LINE = "읽은 양: 원본 {src_files:,}개 {src_bytes}, 복사본 {dst_files:,}개 {dst_bytes}"
TR_SHOW_RESULT_DETAIL = "자세히 보기 ▸"
TR_NEXT_TITLE = "다음에 할 일"
TR_HELP_BTN, TR_COMPARE_BTN = "네트워크 준비 도움말", "두 방식 비교"
TR_HELP_NETWORK_TITLE, TR_COMPARE_TITLE = "랜선(네트워크)로 옮길 준비", "외장 드라이브와 랜선 비교"
HELP_NETWORK = """랜선(네트워크)으로 옮길 준비 — 환경에 따라 다를 수 있습니다. 아래는 일반적인 예시입니다.

a. 연결 방법(둘 중 하나)
  · 같은 공유기/스위치에 두 PC를 모두 랜선(유선)으로 연결합니다. 무선은 느리고 끊기기 쉽습니다.
  · 또는 두 PC를 랜선 하나로 직접 연결합니다. LAN 포트가 없으면 USB 랜 어댑터(기가비트)를 씁니다.

b. 랜선 직결일 때 고정 IP (예시)
  · 구 PC: IP 192.168.50.1, 서브넷 마스크 255.255.255.0, 기본 게이트웨이는 비워 둡니다.
  · 새 PC: IP 192.168.50.2, 서브넷 마스크 255.255.255.0, 기본 게이트웨이는 비워 둡니다.
  · 설정 위치: 설정 > 네트워크 및 인터넷 > 이더넷 > IP 할당 > 편집 > 수동.
  · 두 PC 모두 네트워크 프로필을 '개인'으로 바꿉니다(공용이면 접속이 막힙니다).

c. 새 PC에서 공유 폴더 만들기
  · 비밀번호가 있는 로컬 계정을 하나 만듭니다(비밀번호가 없으면 네트워크 접속이 막힙니다).
  · 복사받을 폴더(예: D:\\이전)를 만들고, 속성 > 공유 > 고급 공유에서 공유하며 그 계정에 '변경(쓰기)' 권한을 줍니다.

d. 접속 테스트(구 PC에서)
  · 명령 프롬프트: ping 192.168.50.2
  · 탐색기 주소창: \\\\192.168.50.2\\이전 을 입력하고 계정/비밀번호를 입력합니다.
  · 폴더 안에 새 텍스트 파일을 만들었다 지워서 쓰기가 되는지 확인합니다.
  · 접속이 꼬였으면: net use * /delete 로 기존 연결을 끊고 다시 시도합니다.

e. 링크 속도 확인
  · 설정 > 네트워크 > 이더넷 > 속성의 '링크 속도'가 1000/1000(Mbps)인지 확인합니다. 100 이면 케이블이나 어댑터를 바꿔 보세요.
  · 기가비트에서 이론상 약 110MB/s 이지만 실제 속도는 환경에 따라 크게 다릅니다.

f. 복사하는 동안
  · 두 PC 모두 전원을 연결합니다. 이 도구가 복사 중 잠자기를 막습니다(구 PC 기준).
  · 새 PC 에서도 잠자기를 꺼 두면 공유 폴더가 끊기지 않습니다.
  · 복사가 끊겨도 같은 설정으로 다시 실행하면 이어받습니다."""
HELP_COMPARE = """외장 드라이브와 랜선(네트워크) 비교 — 측정값과 이론값은 구분해서 봐 주세요.

                외장 드라이브                      랜선(네트워크)
복사 횟수       2번(구 PC → 외장 → 새 PC)          1번(구 PC → 새 PC)
준비물          NTFS 포맷 외장 드라이브            랜선, 필요하면 USB 랜 어댑터, 새 PC 접속 계정
속도 한계       USB 3.x 에서 대개 100MB/s 이상     기가비트 이론 약 110MB/s, 100Mbps 면 약 11MB/s
검사            2단계에서 외장 목록과 새 PC 비교   새 PC에서 목록 파일과 비교(네트워크 재읽기 없음)
막히기 쉬운 곳  FAT32 형식, USB 2.0 포트           공유 설정, 계정/비밀번호, 네트워크 프로필

랜선이 충분한 경우: 기가비트 연결이 확인되고 새 PC에 공유 폴더를 만들 수 있을 때, 복사 횟수가 한 번이라 전체 시간이 짧습니다.
외장 드라이브가 나은 경우: 네트워크 설정이 어렵거나 연결이 불안정할 때, 두 PC를 한 곳에 둘 수 없을 때, 복사 도중 끊겨도 단순하게 이어받고 싶을 때.

문제가 생기면 확인할 곳: 외장은 드라이브 문자와 형식(NTFS)·포트, 랜선은 ping 과 공유 폴더 접속·링크 속도·절전 설정입니다."""

# --------------------------------------------------------------------------- 결과 요약(성공/건너뜀/실패)
RESULT_OK, RESULT_SKIPPED, RESULT_FAILED = "성공", "건너뜀", "실패"
RESULT_LINE = "{ok_label} {ok}  /  {skip_label} {skipped}  /  {fail_label} {failed}"

# --------------------------------------------------------------------------- 현황 카드(오래 걸리는 작업 화면 맨 위)
ICON_SKIP = "↷"
CARD_TITLE = "작업 현황"
CARD_IDLE = "지금 진행 중인 작업이 없습니다."
CARD_STEP_DONE = "완료"
CARD_CIRCLED = "①②③④⑤⑥⑦⑧⑨"
CARD_HEAD_RUNNING = "{step} 중"
CARD_HEAD_FILES = " — {done:,} / {total:,}개 파일 ({pct}%)"
CARD_HEAD_COUNT = " — 파일 {done:,}개 확인함"
CARD_HEAD_DONE = "완료"
CARD_HEAD_DONE_SUMMARY = " — {summary}"
CARD_HEAD_CANCELLED = "중단했습니다 — 같은 설정으로 다시 시작하면 이어서 합니다"
CARD_HEAD_FAILED = f"{ICON_FAIL} 문제가 생겨 멈췄습니다"
CARD_NO_NUMBERS = " — 잠시만 기다려 주세요"
CARD_METRIC_FILES, CARD_METRIC_BYTES, CARD_METRIC_SPEED = "파일", "용량", "속도"
CARD_METRIC_ELAPSED, CARD_METRIC_ETA = "경과 시간", "남은 시간"
CARD_METRIC_FILES_VALUE = "{done:,} / {total:,}개"
CARD_METRIC_FILES_NOTOTAL = "{done:,}개"
CARD_METRIC_NONE = "—"
CARD_ETA_ESTIMATE = "약 {eta}"
CARD_ETA_COMPUTING = "계산 중"
CARD_CURRENT = "지금 처리 중"
CARD_CURRENT_NONE = "—"
CARD_CHIP_LABELS = {"ok": "성공", "skipped": "건너뜀", "unreadable": "읽지 못함(사용 중)", "failed": "실패"}
CARD_CHIP_ICONS = {"ok": ICON_OK, "skipped": ICON_SKIP, "unreadable": ICON_WARN, "failed": ICON_FAIL}
CARD_CHIP_TIP_OPEN = "누르면 {label} 목록을 볼 수 있습니다."
CARD_CHIP_TIP_NONE = "{label} 항목이 없습니다."
CARD_CHIP_TIP_OK = "성공한 파일은 개수만 셉니다."
CARD_PROBLEM_LABEL = "문제가 있나요?"
CARD_PROBLEM_NONE = f"{ICON_OK} 지금까지 문제 없음"
CARD_PROBLEM_SOME = f"{{icon}} {{parts}} — 위 칸을 누르면 목록을 볼 수 있습니다"
CARD_PROBLEM_PART = "{label} {n:,}개"
CARD_LAST = "마지막 메시지"
CARD_ETA_LINE = "남은 시간 {eta}"
CARD_DETAILS_SHOW, CARD_DETAILS_HIDE = "자세히 ▸", "자세히 ▾"
CARD_RESUMED = "저장점에서 이어받음: {n:,}개 파일은 다시 담지 않았습니다"
CARD_LAST_NONE = "—"
CARD_LIST_TITLE = "{label} 목록"
CARD_LIST_NONE = "해당하는 항목이 없습니다."
CARD_LIST_MORE = "… 외 {n:,}개는 이 창에 보관하지 않았습니다(작업이 끝난 뒤 결과 화면이나 결과 파일에서 확인해 주세요)."
CARD_LIST_UNREADABLE_NOTE = "다른 프로그램(예: Chrome)이 파일을 사용 중이라 읽지 못한 파일입니다. 그 프로그램을 닫고 다시 시도해 주세요."
CARD_SUMMARY_FILES = "파일 {n:,}개를 처리했습니다"
CARD_SUMMARY_PROGRAMS = "프로그램 {n:,}개를 확인했습니다"
CARD_SUMMARY_SIMPLE = "작업을 마쳤습니다"
# 작업별 단계 이름(마지막에 '완료' 가 자동으로 붙는다). 진행 단계 이름과의 대응은 status.py 의 PHASE_MAP 에 있다.
TASK_STEPS = {
    TASK_SCAN: ("목록 읽기", "목록 맞추기"),
    TASK_DISCOVER: ("목록 읽기", "폴더 찾기", "크기 재기"),
    TASK_FIND_PROGRAM: ("폴더 찾기", "크기 재기"),
    TASK_CHECKLIST: ("목록 읽기", "정리"),
    TASK_EXPORT: ("파일 찾기", "담기", "검사"),
    TASK_PREVIEW: ("설정 묶음 파일 검사",),
    TASK_INSTALL_CHECK: ("목록 읽기", "비교"),
    TASK_APPLY: ("준비", "복원"),
    TASK_TRANSFER: ("조사", "복사", "검사"),
    TASK_TRANSFER_PREVIEW: ("조사", "비교"),
    TASK_VERIFY: ("검사",),
    TASK_VERIFY_AGAIN: ("조사", "검사"),
    TASK_RETRY: ("조사", "복사·검사"),
}
# 마법사 맨 위의 전체 단계 표시
OVERALL_FMT = "전체 단계 {n}/{total}"
OVERALL_FLOWS = {"old": "① 쓰던 PC에서 준비", "new": "③ 새 PC에서 설치하고 가져오기", "transfer": "② 내 파일 옮기기"}
OVERALL_STEP_NAMES = {"old": ("조사", "목록 밖 프로그램", "할 일", "묶음 만들기"), "new": ("파일 선택", "설치 확인", "미리보기", "복원 확인", "결과"),
                      "transfer": ("구 PC에서 보내기", "새 PC에서 받아 검사"), "local": ("복사",)}
OVERALL_TRANSFER_STAGES = {1: "1단계(구 PC에서 보내기)", 2: "2단계(새 PC에서 받아 검사하기)"}

# --------------------------------------------------------------------------- 오류 대화상자
ERR_WHAT, ERR_HOW = "무엇이 잘못됐나요?", "이렇게 해 보세요"
ERR_PATH_MISSING = ("지정한 폴더나 파일을 찾을 수 없습니다{path}.",
                    "경로를 다시 확인하고, 외장 드라이브나 공유 폴더가 연결돼 있는지 확인해 주세요.")
ERR_PERMISSION = ("권한이 없어서 접근할 수 없습니다{path}.",
                  "다른 위치(예: 문서 폴더)를 고르거나, 프로그램을 '관리자 권한으로 실행'해 보세요.")
ERR_DISK_FULL = ("저장 공간이 부족합니다{path}.",
                 "여유 공간이 있는 드라이브를 고르거나 불필요한 파일을 정리한 뒤 다시 시도해 주세요.")
ERR_DISCONNECTED = ("드라이브나 네트워크 연결이 끊어졌습니다{path}.",
                    "케이블이나 공유 폴더 연결을 확인한 뒤 같은 설정으로 다시 실행해 주세요. 복사는 이어서 합니다.")
ERR_IN_USE = ("다른 프로그램이 이 파일을 사용하고 있습니다{path}.",
              "CAD 등 해당 프로그램을 닫은 뒤 다시 시도해 주세요.")
ERR_OS_GENERIC = ("파일을 읽거나 쓰는 중 문제가 생겼습니다{path}.", "경로와 권한, 저장 공간을 확인한 뒤 다시 시도해 주세요.")
ERR_TRANSFER = ("복사를 시작할 수 없습니다: {msg}", "원본과 대상 폴더를 다시 선택해 주세요.")
ERR_API_HOW = "메시지를 확인한 뒤 다시 시도해 주세요. 더 자세한 내용은 '상세 보기'에 있습니다."
ERR_API_RULES = "프로그램 설치 폴더 안의 자동 목록 폴더(rules)가 손상됐을 수 있습니다. 설치 폴더를 다시 복사해 주세요."
ERR_API_PACKAGE = "설정 묶음 파일이 손상됐거나 중간에 바뀐 것 같습니다. 구 PC에서 다시 만들어 복사해 주세요."
ERR_API_SECRET = "민감한 정보가 포함될 수 있는 항목의 선택을 해제하거나, 내용을 확인한 뒤 이해했다는 표시를 하고 다시 시도해 주세요."
ERR_EXPORT_LOCK = ("같은 이름의 설정 묶음 파일을 만드는 작업이 이미 실행 중입니다{where}.",
                   "그 작업이 끝나길 기다리거나, 다른 파일 이름으로 저장해 주세요. 실행 중인 것이 없다면 이 프로그램을 모두 닫고 다시 실행해 주세요.")
ERR_EXPORT_CHECK_FAILED = ("만든 설정 묶음 파일을 다시 검사했더니 문제가 있어 저장하지 않았습니다. 이 PC의 원본 파일은 그대로입니다.{kept}",
                           "같은 설정으로 다시 만들면 처음부터 다시 만듭니다. 같은 문제가 반복되면 저장할 드라이브 상태를 확인하고, "
                           "'문제 보고서 저장'으로 파일을 저장해 담당자에게 보내 주세요.")
ERR_EXPORT_KEPT = "\n문제를 확인할 수 있도록 임시 파일을 남겨 두었습니다(확인이 끝나면 지워도 됩니다): {path}"
EXP_DENIED = "보안상 복원하지 않는 형식(실행·스크립트·라이브러리 등) {n}개는 설정 묶음 파일에 넣지 않았습니다. 나머지는 정상으로 들어갔습니다."
ERR_API_SELECTION = "고른 항목이 이 PC에 없거나 바뀌었습니다. 자동 목록에 없는 프로그램 확인 화면에서 다시 골라 주세요."
BTN_OPEN_LOG_FOLDER, BTN_COPY_ERROR = "로그 폴더 열기", "오류 내용 복사"
BTN_OPEN_ERROR_LOG = "오류 로그 열기"
ERROR_LOG_NOTE = "오류 내용은 로그 파일에도 기록했습니다. 로그에는 파일 경로가 들어 있으니 다른 사람에게 보내기 전에 확인해 주세요."
ERROR_LOG_FAILED_NOTE = "오류 로그 파일은 만들지 못했습니다(로그 폴더에 쓸 수 없음). '오류 내용 복사'로 내용을 복사해 전달해 주세요."
ERROR_COPIED = "오류 내용을 복사했습니다. 메모장 등에 붙여 넣어 전달해 주세요."
BTN_SAVE_REPORT = "문제 보고서 저장"
REPORT_SAVE_TITLE = "문제 보고서 저장 위치"
REPORT_DEFAULT_NAME = "pcmigrate-problem-report.txt"
ERROR_REPORT_SAVED = "문제 보고서를 저장했습니다. 내 폴더 경로는 %USERPROFILE% 로 바꿔 두었으니 그대로 담당자에게 보내셔도 됩니다."
ERROR_DETAIL_HEADING = "예외 이름과 자세한 내용(담당자에게 전달하는 정보)"
TASK_UI = "화면 동작"
ERR_UNEXPECTED = ("예상하지 못한 문제가 생겨 작업을 마치지 못했습니다.",
                  "프로그램을 다시 실행해 보시고, 같은 문제가 반복되면 '문제 보고서 저장'으로 파일을 저장해 담당자에게 보내 주세요.")
ERR_TITLES = {"os": "파일을 읽거나 쓸 수 없습니다", "api": "작업을 할 수 없습니다", "transfer": "복사를 시작할 수 없습니다",
              "guard": GUARD_TITLE, "unexpected": "예상하지 못한 문제가 발생했습니다"}

# 결과 표의 '내용' 칸에 나오는 내부 메시지를 쉬운 말로 바꾸는 표(앞에서부터 차례로 적용)
DETAIL_REPLACEMENTS = (
    ("SHA-256 불일치", "내용이 원본과 다름"),
    ("크기/해시 불일치", "내용이 원본과 다름"),
    ("복사 후 크기/해시 검증 실패", "복사한 파일이 정확하지 않음"),
    ("재복사 후 크기/해시 검증 실패", "다시 복사했지만 파일이 정확하지 않음"),
    ("크기 불일치", "크기가 원본과 다름"),
    ("해시", "내용 확인 값"),
    ("robocopy 종료코드", "복사 도구 종료 코드"),
    ("robocopy", "복사 도구"),
    ("manifest", "목록 파일"),
    ("(extra)", "(직접 고른 항목)"),
    ("extra", "직접 고른 항목"),
    ("strict", "엄격 검사"),
    ("UNC", "공유 폴더"),
    ("zip", "설정 묶음 파일"),
    ("패키지", "설정 묶음 파일"),
    ("규칙", "자동 목록"),
)

# --------------------------------------------------------------------------- 도움말(?) — 무엇을 하는 화면 / 안전한가 / 다음 단계
HELP_TITLE = "도움말 — {screen}"
HELP = {
    START: ("무엇을 하는 화면인가요?\n새 PC로 옮기는 일 중 하나를 고르는 첫 화면입니다.\n\n"
            "안전한가요?\n이 화면에서는 아무것도 읽거나 쓰지 않습니다.\n\n다음 단계\n쓰던 PC에서 ①, 두 PC 사이에서 파일 옮기기는 ②, "
            "새 PC에서 설치하고 가져오기는 ③ 순서로 골라 주세요."),
    OLD_SCAN: ("무엇을 하는 화면인가요?\n이 PC에 설치된 프로그램을 조사하고, 설정을 자동으로 모을 수 있는 프로그램을 찾습니다.\n\n"
               "안전한가요?\n네. 읽기만 하며 아무것도 쓰지 않습니다.\n\n다음 단계\n조사가 끝나면 '다음'으로 자동 목록에 없는 프로그램을 확인합니다."),
    OLD_DISCOVER: ("무엇을 하는 화면인가요?\n자동 목록에 없어서 자동으로 모으지 못하는 프로그램의 설정 폴더 후보를 보여 줍니다. 필요한 것만 직접 고릅니다.\n\n"
                   "안전한가요?\n찾는 동안은 읽기만 합니다. 고른 항목만 설정 묶음 파일에 들어가고, 민감한 정보가 있을 수 있는 항목(붉게 표시)은 "
                   "이해했다는 표시를 해야 고를 수 있습니다.\n\n다음 단계\n'다음'으로 해야 할 일 목록을 만듭니다."),
    OLD_CHECKLIST: ("무엇을 하는 화면인가요?\n새 PC에서 직접 해야 할 일(프로그램 설치, 로그인·인증, 데이터 복사 등)을 정리해 줍니다.\n\n"
                    "안전한가요?\n목록을 만들 때는 읽기만 합니다. 파일로 저장하면 개인 정보가 들어 있으니 공유에 주의해 주세요.\n\n"
                    "다음 단계\n저장해 둔 뒤 '다음'으로 설정 묶음 파일을 만듭니다."),
    OLD_EXPORT: ("무엇을 하는 화면인가요?\n설정과 프로그램 목록을 하나의 .zip 파일(설정 묶음 파일)로 만듭니다.\n\n"
                 "안전한가요?\n원본은 읽기만 합니다. 쓰는 것은 지정한 .zip 파일뿐이며, 쓰기 전에 위치를 확인합니다. 라이선스는 들어가지 않습니다.\n\n"
                 "다음 단계\n만든 .zip 을 ② 내 파일 옮기기로 새 PC에 보낸 뒤, 새 PC에서 ③을 골라 주세요."),
    NEW_PACKAGE: ("무엇을 하는 화면인가요?\n구 PC에서 만든 설정 묶음 파일(.zip)을 고르는 화면입니다.\n\n"
                  "안전한가요?\n네. 이 단계에서는 아무것도 쓰지 않습니다.\n\n다음 단계\n'검사하고 미리보기'를 눌러 주세요."),
    NEW_INSTALL: ("무엇을 하는 화면인가요?\n구 PC에 있던 프로그램이 이 PC에 이미 설치됐는지 비교해, 설치된 것과 아직 설치하지 않은 것을 나눠 보여 줍니다. "
                  "이름이 비슷해 확신할 수 없는 것은 '애매함'으로 따로 표시합니다.\n\n"
                  "안전한가요?\n네. 이 도구는 프로그램을 설치하지 않습니다. 설치 명령은 복사만 해 드리니 명령 창에서 직접 실행해 주세요.\n\n"
                  "다음 단계\n설치한 뒤 '다시 확인'을 누르면 다시 판단합니다. 건너뛰어도 되며, '다음(미리보기)'으로 이어갑니다."),
    NEW_PREVIEW: ("무엇을 하는 화면인가요?\n설정 묶음 파일이 정확한지 검사하고, 복원하면 어떤 파일이 새로 만들어지거나 덮어써지는지 미리 보여 줍니다.\n\n"
                  "안전한가요?\n네. 지금은 실제로 바꾸지 않았습니다.\n\n다음 단계\n문제가 없으면 '복원 확인 단계로' 이동합니다."),
    NEW_CONFIRM: ("무엇을 하는 화면인가요?\n실제로 복원하기 전의 마지막 확인 화면입니다.\n\n"
                  "안전한가요?\n체크 두 개와 확인 창을 모두 거쳐야 실행됩니다. 덮어쓰는 파일은 백업 파일(.bak)로 남습니다.\n\n"
                  "다음 단계\n체크하고 '복원 실행'을 누르면 결과 화면으로 이동합니다."),
    NEW_RESULT: ("무엇을 하는 화면인가요?\n복원 결과(성공/건너뜀/실패)를 보여 줍니다.\n\n안전한가요?\n이 화면은 보기만 합니다.\n\n"
                 "다음 단계\n실패가 있으면 원인을 해결하고 같은 설정 묶음 파일로 다시 복원해 주세요. 라이선스는 직접 인증해야 합니다."),
    TRANSFER: ("무엇을 하는 화면인가요?\n큰 폴더를 외장 드라이브나 랜선(네트워크)으로 옮기고, 정확히 복사됐는지 검사합니다.\n\n"
               "안전한가요?\n원본은 읽기만 합니다. '미리보기 실행'을 해야 '복사 시작'이 열리고, 여유 공간 부족·FAT32 4GB 초과는 시작 전에 막습니다.\n\n"
               "다음 단계\n방식 선택 → 폴더 입력 → 미리보기 → 복사 시작 → 결과 확인. 아래 버튼에서 네트워크 준비 도움말과 방식 비교를 볼 수 있습니다."),
}

# stage30a: '내 파일 옮기기' 화면들의 문구는 texts_files.py (병합 충돌을 줄이려고 별도 파일). 여기서 이름공간에 넣는다.
from .texts_files import install as _install_files_texts  # noqa: E402
_install_files_texts(globals())
START_CHOICES = (START_CHOICES[0], START_CARD3, START_CHOICES[2])  # noqa: F821  (② 카드만 새 문구로)

# --------------------------------------------------------------------------- 전체 이전 순서(? 도움말에서 열림, README 와 같은 원본)
BTN_ROADMAP = "전체 이전 순서 보기"
ROADMAP_TITLE = "전체 이전 순서"
ROADMAP_INTRO = ("순서대로 진행하면서 끝낸 항목에 체크해 주세요(창을 닫았다 열어도 체크가 유지됩니다). 도구가 도와주는 일과 직접 하셔야 하는 일을 구분했습니다.\n"
                 "순서는 ① 쓰던 PC에서 준비 → ② 내 파일 옮기기(양쪽 PC) → ③ 새 PC에서 설치하고 가져오기입니다. 두 PC 연결(보내기·받기)은 ②에서만 하고, ①과 ③은 각 PC 안에서만 작업합니다.")
ROADMAP_HEADING = "전체 이전 순서"
ROADMAP_PROGRESS = "진행률 {n}/{total}"
WHO_TOOL, WHO_HUMAN, WHO_BOTH = "도구가 도와줌", "직접 하셔야 함", "도구가 도와주고 직접 확인"
ROADMAP_OLD_HEADING = "구 PC에서 (떠나기 전)"
ROADMAP_NEW_HEADING = "새 PC에서 (도착해서)"
# (번호 표시용 id, 제목, 누가, 설명)
ROADMAP_OLD = (
    ("old1", "모든 프로그램을 종료하고 전원을 연결합니다", WHO_HUMAN,
     "CAD·BIM 같은 파일을 쓰는 프로그램을 모두 닫아 주세요. 노트북은 전원을 연결해 주세요. 사용 중인 파일은 복사와 검사에 실패합니다."),
    ("old2", "프로그램 조사와 해야 할 일 목록 저장", WHO_TOOL,
     "'① 쓰던 PC에서 준비하기' 1~3단계에서 설치된 프로그램을 조사하고, 새 PC에서 할 일 목록을 파일로 저장해 둡니다."),
    ("old3", "자동 목록에 없는 프로그램 확인·선택", WHO_BOTH,
     "도구가 자동 목록에 없는 프로그램의 설정 폴더 후보를 찾아 줍니다. 설정 묶음 파일에 넣을 항목은 직접 고르셔야 합니다."),
    ("old4", "설정 묶음 파일 내보내기", WHO_TOOL,
     "'① 쓰던 PC에서 준비하기' 4단계에서 설정과 프로그램 목록을 .zip 설정 묶음 파일로 만듭니다. 라이선스는 들어가지 않습니다."),
    ("old5", "데이터 복사 방식 선택(외장/랜선)과 복사", WHO_BOTH,
     "'② 내 파일 옮기기'(예전 이름: 대용량 복사)에서 폴더를 고르고 방법을 정해 미리보기 → 복사를 진행합니다(구 PC는 보내는 쪽이며, ①에서 만든 설정 묶음 파일도 '설정 묶음 함께 보내기'로 같이 보낼 수 있습니다). 외장 드라이브(NTFS)나 랜선·공유 폴더 준비는 직접 하셔야 합니다."),
    ("old6", "모든 검증이 끝나기 전까지 구 PC를 삭제하거나 포맷하지 않습니다", WHO_HUMAN,
     "새 PC에서 검사 결과가 '실패 0'이고 프로그램과 데이터에 문제가 없음을 확인할 때까지 구 PC의 파일을 지우거나 포맷하지 마세요. 이 도구는 원본을 지우지 않습니다."),
)
ROADMAP_NEW = (
    ("new1", "윈도우 업데이트와 장치 드라이버를 먼저 맞춥니다", WHO_HUMAN,
     "Windows 업데이트를 끝내고 그래픽카드·플로터·동글 등 장치 드라이버를 설치해 주세요."),
    ("new2", "필요한 프로그램을 새로 설치하고 라이선스를 인증합니다", WHO_BOTH,
     "도구의 해야 할 일 목록이 설치할 프로그램을 알려 줍니다. 설치, 로그인·인증(동글 드라이버 포함)은 직접 하셔야 하며, 가능하면 각 프로그램을 한 번 실행했다가 종료해 주세요."),
    ("new3", "데이터를 복사하고 검사합니다", WHO_TOOL,
     "'② 내 파일 옮기기'의 받는 쪽(새 PC)에서 데이터를 받고, 랜선이면 2단계 '검사 시작'으로 목록 파일과 비교합니다. 결과가 '실패 0'인지 확인해 주세요."),
    ("new4", "모든 프로그램을 종료하고 설정 묶음 파일을 미리보기로 확인한 뒤 복원합니다", WHO_TOOL,
     "'③ 새 PC에서 설치하고 가져오기'에서 (②로 받은) 설정 묶음 파일을 고르고 미리보기(실제로 바꾸지 않음)를 확인한 뒤 복원합니다. 덮어쓰는 파일은 백업 파일(.bak)로 남습니다."),
    ("new5", "각 프로그램을 열어 설정·템플릿·글꼴·플로터·참조 경로를 점검합니다", WHO_HUMAN,
     "자동으로 옮겨지지 않은 항목이 있을 수 있습니다. 라이선스 상태도 프로그램마다 확인해 주세요."),
    ("new6", "폴더 위치(드라이브 문자)가 구 PC와 같은지 확인합니다", WHO_BOTH,
     "도면의 외부 참조(xref)와 데이터 단축은 절대 경로를 씁니다. 도구가 위치가 다르면 경고해 주지만, 도면을 열어 참조가 이어지는지는 직접 확인하셔야 합니다."),
)


# --------------------------------------------------------------------------- 추천대로 한 번에(시작 화면의 보조 단추)
AUTO = "auto_run"
BTN_AUTO = "추천대로 한 번에 ▸"
TIP_AUTO = {
    "old": "조사 → 해야 할 일 목록 → 바탕화면에 설정 묶음 파일 만들기를 이어서 합니다. 자동 목록에 없는 프로그램은 아무것도 고르지 않습니다.",
    "new": "받은 폴더·바탕화면·다운로드·연결된 외장 드라이브에서 설정 묶음 파일을 찾아 미리보기까지 이어서 합니다. 복원 확인은 직접 하셔야 합니다.",
    "transfer": "두 가지만 물어본 뒤 미리보기부터 이어서 합니다. 복사를 시작하기 전에는 반드시 한 번 더 확인합니다.",
}
AUTO_HEADING = "추천대로 한 번에"
AUTO_SUB = {
    "old": "쓰던 PC에서 조사부터 설정 묶음 파일 만들기까지 이어서 진행합니다. 쓰던 PC의 파일은 읽기만 합니다.",
    "new": "설정 묶음 파일 찾기부터 복원까지 이어서 진행합니다. 프로그램 설치 확인에서는 설치를 직접 하거나 건너뛸 수 있고, 복원 전에는 확인 표시 두 개와 확인 창을 거칩니다.",
    "transfer": "방식을 정한 뒤 미리보기부터 이어서 진행합니다. 복사를 시작하기 전에는 반드시 확인을 받습니다.",
}
AUTO_STEPS = {
    "old": (("scan", "프로그램 조사"), ("discover", "자동 목록에 없는 프로그램 확인"), ("checklist", "해야 할 일 목록 만들기"),
            ("export", "설정 묶음 파일 만들기")),
    "new": (("find", "설정 묶음 파일 찾기"), ("preview", "검사와 미리보기"), ("install", "프로그램 설치 확인"), ("confirm", "복원 확인"),
            ("apply", "복원")),
    "transfer": (("questions", "방식 정하기"), ("folders", "폴더 고르기"), ("preview", "미리보기"), ("copy", "복사"),
                 ("result", "결과 확인")),
    "receive": (("questions", "방식 정하기"), ("folders", "목록·폴더 고르기"), ("check", "복사본 검사"), ("result", "결과 확인")),
}
AUTO_STATE_LABELS = {"todo": "대기", "doing": "진행 중", "waiting": "확인 기다리는 중", "done": f"{ICON_OK} 끝남",
                     "skipped": "건너뜀", "warn": f"{ICON_WARN} 끝남(확인 필요)", "stopped": f"{ICON_FAIL} 멈춤"}
AUTO_DISCOVER_SKIPPED = "자동 목록에 없는 프로그램은 아무것도 자동으로 고르지 않습니다(필요하면 ①을 직접 진행해 주세요)."
AUTO_SCAN_NOTE = "프로그램 {programs}개 조사, 자동 목록과 일치 {rules}개"
AUTO_STOP_BTN = "중지"
AUTO_STOP_NOTE = "지금 하는 단계는 끝난 뒤에 멈춥니다. 쓰던 PC의 파일은 바뀌지 않습니다."
AUTO_STOP_CANCELLING = "멈추는 중입니다…"
AUTO_BTN_HOME = "처음 화면으로"
AUTO_BTN_DETAIL = "자세한 결과 보기"
AUTO_BTN_CHECKLIST = "해야 할 일 목록 보기"
AUTO_BTN_MANUAL = "직접 진행하기"
AUTO_RUNNING_HEAD = "진행 중입니다. 아래 단계를 순서대로 하고 있습니다."
AUTO_WAITING_HEAD = "확인이 필요합니다"
AUTO_DONE_HEAD = f"{ICON_OK} 모두 끝났습니다"
AUTO_DONE_WARN_HEAD = f"{ICON_WARN} 끝났지만 확인이 필요한 것이 있습니다"
AUTO_STOPPED_HEAD = f"{ICON_WARN} 여기서 멈췄습니다"
AUTO_STOPPED_USER = "중지했습니다. 여기까지 끝난 단계의 결과는 그대로 남아 있습니다."
AUTO_STOPPED_USER_HOW = "처음 화면에서 다시 시작하거나, 직접 진행하기로 이어서 할 수 있습니다."
AUTO_CANCELLED_WHAT = "작업을 취소해서 멈췄습니다."
AUTO_CANCELLED_HOW = "큰 파일 복사는 같은 설정으로 다시 시작하면 이어서 할 수 있습니다."
AUTO_EXPORT_DECLINED = "설정 묶음 파일을 만들기 전에 멈췄습니다. 조사와 해야 할 일 목록은 만들어 두었습니다."
AUTO_EXPORT_CONFIRM_HEAD = "설정 묶음 파일을 만들기 전에 확인해 주세요"
AUTO_EXPORT_CONFIRM_BTN = "설정 묶음 파일 만들기"
AUTO_UNREADABLE_WARN = ("읽지 못해 설정 묶음 파일에 담지 못한 파일이 {n}개 있습니다(다른 프로그램이 쓰는 중일 수 있습니다). "
                        "프로그램을 모두 닫고 다시 만들어 보세요.")
AUTO_FAILED_WARN = "정확히 담지 못한 파일이 {n}개 있습니다. '자세한 결과 보기'에서 내용을 확인해 주세요."
# 새 PC
AUTO_PKG_NONE_WHAT = "설정 묶음 파일을 찾지 못했습니다(받은 폴더, 바탕화면, 다운로드, 연결된 외장 드라이브를 찾아봤습니다)."
AUTO_PKG_NONE_HOW = "파일이 다른 곳에 있으면 '직접 고르기'로 위치를 알려 주세요. 외장 드라이브는 컴퓨터에 연결되어 있는지 확인해 주세요."
AUTO_BTN_PICK_MANUAL = "직접 고르기"
AUTO_PKG_PICK_HEAD = "설정 묶음 파일이 {n}개 있습니다. 사용할 파일을 골라 주세요(가장 최근 것이 맨 위입니다)."
AUTO_PKG_PICK_BTN = "이 파일로 계속"
AUTO_PROBLEMS_WHAT = "설정 묶음 파일 검사에서 문제가 발견되어 멈췄습니다: {items}"
AUTO_PROBLEMS_HOW = "구 PC에서 설정 묶음 파일을 다시 만들어 복사해 주세요."
AUTO_REJECTED_WHAT = "복원 위치가 바로가기(링크)를 거치는 파일 {n}개는 복원되지 않아서 자동 진행을 멈췄습니다."
AUTO_REJECTED_HOW = "'미리보기에서 확인'을 눌러 내용을 직접 보고 진행해 주세요."
AUTO_BTN_MANUAL_PREVIEW = "미리보기에서 확인"
AUTO_RESTORE_HEAD = "복원하기 전에 확인해 주세요"
AUTO_RESTORE_SUMMARY = "새로 만들 파일 {new}개 · 덮어쓸 파일 {over}개 · 같아서 건너뛸 파일 {same}개"
AUTO_INSTALL_HEAD = "이 PC에 프로그램이 설치돼 있는지 확인해 주세요"
AUTO_INSTALL_NOTE = "이 도구는 설치 명령을 실행하지 않습니다. 필요하면 명령을 복사해 직접 실행한 뒤 '다시 확인'을 눌러 주세요."
AUTO_INSTALL_CONTINUE = "확인했습니다. 복원 확인으로"
AUTO_INSTALL_DONE_NOTE = "설치 확인 결과: 남은 것 {n}개"
AUTO_INSTALL_SKIPPED = "건너뜀: 설치 확인 없이 진행했습니다."
AUTO_BTN_PREVIEW_LIST = "미리보기 자세히 보기"
AUTO_PREVIEW_LIST_TITLE = "복원 미리보기"
AUTO_APPLY_FAILED_HOW = "원인을 해결한 뒤 '직접 진행하기'로 미리보기부터 다시 확인해 주세요."
# 대용량 복사
AUTO_Q1 = "어떻게 옮기시나요?"
AUTO_Q2 = "지금 어느 PC 앞에 계신가요?"
AUTO_Q_NEXT = "다음으로"
AUTO_Q2_OPTIONS = ("쓰던 PC(구 PC) 앞입니다. 보내는 쪽입니다", "새 PC 앞입니다. 받는 쪽입니다")
AUTO_EXPORT_WILL = "다음 위치에 설정 묶음 파일을 만듭니다. 원본은 읽기만 합니다."
AUTO_TR_WILL = "미리보기를 마쳤습니다. 아직 아무것도 복사하지 않았습니다. 시작하면 아래 확인 창이 한 번 더 나옵니다."
AUTO_VERIFY_WILL = "복사본을 목록과 비교만 합니다. 폴더는 읽기만 합니다."
AUTO_FOLDERS_HEAD = "폴더를 골라 주세요"
AUTO_FOLDERS_BTN = "미리보기부터 이어서 진행"
AUTO_FOLDERS_BTN_VERIFY = "검사부터 이어서 진행"
AUTO_FOLDERS_BAD = "폴더를 확인해 주세요: {why}"
AUTO_TR_CONFIRM_HEAD = "복사를 시작하기 전에 확인해 주세요"
AUTO_TR_CONFIRM_BTN = "복사 시작"
AUTO_TR_VERIFY_BTN = "검사 시작"
AUTO_TR_PREVIEW_DECLINED = "복사를 시작하기 전에 멈췄습니다. 미리보기만 했고 대상 폴더에는 아무것도 쓰지 않았습니다."
AUTO_TR_BLOCKED_HOW = "문제를 해결하거나 다른 드라이브를 고른 뒤 ②를 다시 시작해 주세요."
AUTO_TR_FAT32_HOW = ("큰 파일이 있는 FAT32 드라이브에는 ②를 직접 시작해서 '고급 옵션'의 '4GB가 넘는 파일을 조각으로 나눠 복사'를 켜 보세요. "
                     "아니면 NTFS 로 포맷된 드라이브를 쓰세요.")
AUTO_TR_PREVIEW_NOTE = "새로 복사 {new}개 · 덮어씀 {over}개 · 이미 같음 {same}개"
AUTO_TR_PREVIEW_PROBLEM = "미리보기에서 문제가 있는 파일 {n}개가 나왔습니다."
AUTO_TR_PREVIEW_PROBLEM_HOW = "'직접 진행하기'로 미리보기 내용을 확인한 뒤 진행해 주세요."
AUTO_TR_FAIL_WHAT = "정확히 복사되지 않은 파일이 {n}개 있습니다."
AUTO_TR_FAIL_HOW = "'자세한 결과 보기'에서 목록을 확인하고, 원인(파일을 쓰는 프로그램, 연결 끊김, 저장 공간)을 해결한 뒤 '실패한 것만 다시 시도'를 눌러 주세요."
AUTO_TR_CLOUD_NOTE = "클라우드 전용 파일 {n}개는 복사하지 않습니다(내려받아야 해서 기본은 제외입니다)."
# 시작 화면 도움말과 시험에서 쓰는 이름
HELP[AUTO] = ("무엇을 하는 화면인가요?\n추천 설정으로 여러 단계를 이어서 진행하고, 지금 어느 단계인지 보여 줍니다.\n\n"
              "안전한가요?\n원본은 읽기만 합니다. 파일을 쓰거나 복원하기 전에는 항상 확인을 받고, 문제가 생기면 그 자리에서 멈춥니다. "
              "'중지'를 누르면 언제든 멈춥니다.\n\n다음 단계\n끝나면 결과와 다음에 할 일이 이 화면에 나옵니다.")

# 4GB 넘는 파일 조각내기(FAT32 외장 디스크용), 임시 파일 위치, Git 설정 안내
TR_SPLIT = "4GB가 넘는 파일을 조각으로 나눠 복사(FAT32 외장 디스크용)"
TR_SPLIT_NOTE = ("FAT32 디스크는 4GB 넘는 파일을 저장하지 못합니다. 켜면 큰 파일을 조각 파일로 나눠 저장합니다. "
                 "새 PC에서 pcmigrate join 으로 합쳐야 열 수 있습니다.")
TR_SPLIT_RESULT = f"{ICON_WARN} 조각으로 나눈 파일 {{n}}개, 새 PC에서 pcmigrate join 으로 합치세요. 합치기 전에는 열 수 없습니다."
TR_SPLIT_PREVIEW = "조각으로 나눌 파일 {n}개(새 PC에서 pcmigrate join 으로 합칩니다)"
G_SPLIT_UNSUPPORTED = "이 버전에서는 큰 파일을 조각으로 나누는 기능을 쓸 수 없습니다. 옵션을 끄고 다시 시도해 주세요."
EXP_ADV_TOGGLE, EXP_ADV_TOGGLE_OPEN = "고급 옵션 ▸", "고급 옵션 ▾"
EXP_STAGE_LABEL = "임시 파일 위치(선택):"
EXP_STAGE_HINT = "큰 파일을 잠시 복제하는 폴더입니다. 비워 두면 기본 위치를 씁니다. 여유 공간이 넉넉한 드라이브의 폴더를 골라 주세요."
EXP_STAGE_PICK = "임시 파일 위치 선택"
G_STAGE_MISSING = "임시 파일 위치 폴더를 찾을 수 없습니다: {path}"
G_STAGE_UNSUPPORTED = "이 버전에서는 임시 파일 위치를 바꿀 수 없습니다. 입력칸을 비우고 다시 시도해 주세요."
PREV_GIT_NOTE = f"{ICON_WARN} Git 설정 파일에 명령 실행 설정이 들어 있을 수 있으니 복원 전에 내용을 확인해 주세요"
RES_GIT_NOTE = f"{ICON_WARN} 복원한 Git 설정 파일에 명령 실행 설정이 들어 있을 수 있으니 내용을 확인해 주세요"

# 설치 폴더 설정(프로그램 설치 폴더 안의 설정 파일): 찾기 결과에서는 체크 가능한 후보, 신 PC 복원은 기본 꺼짐
BADGE_INSTALL_DIR = "설치 폴더 설정"
DISC_INSTALL_BADGE_FULL = "설치 폴더 설정 · 복원 시 관리자 권한과 같은 버전의 프로그램이 필요합니다"
DISC_DETAIL_INSTALL = ("\n프로그램 설치 폴더 안의 설정 파일입니다. 복원할 때 관리자 권한과 같은 버전의 프로그램이 필요하며, "
                       "새 PC에서는 직접 켜기 전까지 복원하지 않습니다.")
FIND_INSTALL_CANDIDATES = "설치 폴더 안의 설정 형식 파일 {n}개를 표에 더했습니다(체크한 것만 담습니다, 자동 선택은 하지 않습니다): {names}"
FIND_INSTALL_MORE = " 개수 상한으로 {k}개는 뺐습니다."
FIND_INSTALL_EXCLUDED = " 크기 상한(5MB)을 넘어 뺀 파일 {k}개."
G_INSTALL_UNSUPPORTED = "이 버전에서는 설치 폴더 설정을 쓸 수 없습니다. 해당 항목의 선택을 해제하고 다시 시도해 주세요."
PREV_KIND_INSTALL = "설치 폴더 설정"
PREV_INSTALL_CHECK = "설치 폴더 설정도 복원(관리자 권한 필요)"
PREV_INSTALL_OFF = ("{icon} 설치 폴더 설정 {n}개 — 복원하려면 아래 [설치 폴더 설정도 복원]을 체크해 주세요(관리자 권한 필요). "
                    "지금은 복원하지 않습니다.")
PREV_INSTALL_ON = "{icon} 설치 폴더 설정 {n}개도 복원합니다(관리자 권한 필요). 덮어쓰는 파일은 백업 파일(.bak)로 남습니다."
PREV_INSTALL_MISSING = "{icon} 대상 프로그램 폴더가 없어 건너뛰는 설치 폴더 설정 {k}개 — 먼저 프로그램을 설치한 뒤 다시 복원해 주세요."
PREV_INSTALL_VERSION = "{icon} 이 설정 파일은 같은 버전의 프로그램에서 가져온 것입니다. 버전이 다르면 프로그램이 오작동할 수 있습니다."
PREV_INSTALL_VERSION_DIFF = "{icon} {name}: 구 PC 버전 {old}, 이 PC 버전 {new} — 버전이 달라 오작동할 수 있습니다."
PREV_INSTALL_NOT_RESTORED = "복원 안 함(설치 폴더)"
CONF_INSTALL_INFO = "설치 폴더 설정 {n}개도 복원합니다(관리자 권한으로 실행 중이어야 합니다)."


def roadmap_lines(marks=frozenset(), markdown: bool = False) -> list[str]:
    """전체 이전 순서를 줄 목록으로 만든다. 화면 도움말과 README 가 같은 원본(ROADMAP_*)에서 나오게 한다."""
    out: list[str] = []
    for heading, items in ((ROADMAP_OLD_HEADING, ROADMAP_OLD), (ROADMAP_NEW_HEADING, ROADMAP_NEW)):
        out.append(f"**{heading}**" if markdown else heading)
        if markdown:
            out.append("")
        for i, (key, title, who, detail) in enumerate(items, 1):
            box = CHECKED if key in marks else UNCHECKED
            if markdown:
                out.append(f"{i}. {UNCHECKED} **{title}** — _{who}_  ")
                out.append(f"   {detail}")
            else:
                out.append(f"{box} {i}) {title}  [{who}]")
                out.append(f"      {detail}")
        out.append("")
    return out[:-1]


def roadmap_markdown() -> str:
    return "\n".join(roadmap_lines(markdown=True))


def roadmap_text(marks=frozenset()) -> str:
    return "\n".join(roadmap_lines(marks))


# --------------------------------------------------------------------------- 서식 도우미
def friendly_detail(text: str) -> str:
    """결과 표에 보여 줄 내부 메시지를 쉬운 말로 바꾼다."""
    for old, new in DETAIL_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def result_line(ok: int, skipped: int, failed: int) -> str:
    return RESULT_LINE.format(ok_label=RESULT_OK, skip_label=RESULT_SKIPPED, fail_label=RESULT_FAILED,
                              ok=ok, skipped=skipped, failed=failed)


def ellipsize_path(path: str, limit: int = 60) -> str:
    """긴 경로를 가운데 말줄임으로 줄인다(전체 경로는 툴팁으로 보여 준다)."""
    if len(path) <= limit:
        return path
    head = max(10, limit // 3)
    return path[:head] + "…" + path[-(limit - head - 1):]


# --------------------------------------------------------------------------- 읽지 못해 제외된 파일만 다시 담기
TASK_EXPORT_RETRY = "읽지 못한 파일 다시 담기"
PROGRESS[TASK_EXPORT_RETRY] = ("읽지 못해 제외된 파일만 다시 읽어 담는 중… 보통 몇 초에서 몇 분 걸립니다. "
                               "원본은 읽기만 하고, 이전 설정 묶음 파일은 백업 파일(.bak)로 남깁니다.")
TASK_STEPS[TASK_EXPORT_RETRY] = ("파일 찾기", "담기", "검사")
BTN_EXP_RETRY = "이 파일들만 다시 담기"
EXP_RETRY_HEAD = f"{ICON_WARN} 읽지 못해 제외된 파일 {{n}}개"
EXP_RETRY_ALL_DONE = f"{ICON_OK} 읽지 못해 제외된 파일이 이제 없습니다"
EXP_RETRY_HINT = "사용 중인 프로그램(예: Chrome)을 닫고 눌러 주세요. 읽지 못한 파일만 다시 읽어 이미 만든 설정 묶음 파일에 추가합니다."
EXP_RETRY_CONFIRM_TITLE = "읽지 못한 파일 다시 담기"
EXP_RETRY_CONFIRM = ("다음 설정 묶음 파일에서 읽지 못해 제외된 파일 {n}개만 다시 담습니다:\n{path}\n\n"
                     "원본은 읽기만 합니다. 이전 설정 묶음 파일은 백업 파일(.bak)로 남기고, 검사를 통과한 경우에만 바꿉니다. "
                     "다시 담는 파일은 지금 원본의 내용입니다.")
EXP_RETRY_DONE = f"{ICON_OK} 다시 담았습니다: {{added}}개 담김, 아직 못 담음 {{still}}개."
EXP_RETRY_NO_CHANGE = f"{ICON_WARN} 아직 읽지 못했습니다. 설정 묶음 파일은 바꾸지 않았습니다(아직 못 담음 {{still}}개)."
EXP_RETRY_REASONS = "못 담은 이유: {reasons}"
EXP_RETRY_GONE = "원본이 없어져 목록에서 뺀 파일: {n}개"
EXP_RETRY_NOT_RETRIED = "자동 목록 밖이거나 보안상 담지 않는 파일이라 다시 담지 않은 것: {n}개"
EXP_RETRY_FRESH = "다시 담은 파일은 지금 원본의 내용입니다(처음 만든 뒤 바뀌었을 수 있습니다)."
EXP_RETRY_BACKUP = "이전 설정 묶음 파일은 {path} 로 보관했습니다."
EXP_RETRY_AGAIN = "프로그램을 닫은 뒤 '이 파일들만 다시 담기'를 다시 눌러 주세요."
G_NEED_RETRY_CONFIRM = "읽지 못한 파일을 다시 담기 전에 확인하는 단계가 필요합니다."
G_RETRY_NO_PACKAGE = "다시 담을 설정 묶음 파일이 없습니다. 먼저 설정 묶음 파일을 만들어 주세요."
G_RETRY_NOTHING = "다시 담을 파일이 없습니다."


# =========================================================================== stage30b 구획(화면 4~7: 표/중지/프로그램별 복원/마지막 화면)
# ---- 작업 중지(하단 막대)
BTN_STOP = "중지"
STOPPING = "중지하는 중…"
STOP_STUCK = (f"{ICON_WARN} 아직 멈추지 않았습니다. 파일을 읽거나 쓰는 중이면 잠시 더 걸릴 수 있습니다. "
              "더 기다려도 되고, 화면을 먼저 정상으로 돌려놓을 수도 있습니다.")
BTN_FORCE_STOP = "지금 강제로 멈추기"
FORCE_STOPPED = ("화면은 정상으로 돌아왔습니다. 만들던 임시 파일은 이어받기가 가능한 상태로 남겨 두었습니다. "
                 "파일을 쓰는 새 작업은 잠시 막힐 수 있습니다.")
STOP_DONE_RESUME = "중지했습니다. 만들던 임시 파일은 남아 있어, 같은 설정으로 다시 시작하면 이어서 합니다."
STOP_DONE_APPLY = "중지했습니다. 이미 복원한 파일이 있을 수 있으며, 덮어쓴 파일은 백업 파일(.bak)로 남아 있습니다."
STOP_DONE_READONLY = "중지했습니다. 읽기만 하는 작업이라 원본은 그대로이며, 처음부터 다시 해도 안전합니다."
STOP_DONE_UNDO = "중지했습니다. 이미 되돌린 파일이 있을 수 있습니다. 결과 화면에서 다시 확인해 주세요."
TIP_STOP = "지금 하는 작업을 안전한 지점에서 멈춥니다(Esc 키도 같습니다)."

# ---- 화면 4: 자동 목록에 없는 프로그램
DISC_HINT_SHORT = "읽기만 합니다. 1분 안팎 걸릴 수 있고, 아래 '중지'로 멈출 수 있습니다."
DISC_COLS_V2 = ("선택", "주의", "프로그램", "위치", "설정 파일", "용량", "제외")
DISC_CFG_UNKNOWN = "확인 안 됨"
DISC_CFG_CALC = "계산 중"
DISC_CFG_COUNT = "{n}개"
DISC_CFG_COUNT_MORE = "{n}개 이상"
DISC_SIZE_FOLDER = "폴더 {size}"
DISC_CFG_TIP_UNKNOWN = "설정 파일 수는 '이 프로그램 설정 찾기'를 누르면 계산됩니다. 그 전에는 폴더 전체 크기만 보여 줍니다."
DISC_CFG_NONE = "없음"
DISC_NO_CONFIG_TITLE = "안내"
DISC_NO_CONFIG_DETAIL = "설정 파일로 보이는 파일이 없습니다. 폴더 {size} 는 설정이 아닌 파일입니다."
DISC_NO_CONFIG_CHECK = ("담을 설정 파일이 없습니다. 폴더 전체 담기를 켜면 담을 수 있습니다. "
                        "(체크는 되지만 지금 방식으로는 담기는 파일이 0개입니다)")
FIND_NO_CONFIG = "설정 파일로 보이는 파일이 없습니다. 찾은 폴더 {size} 는 설정이 아닌 파일입니다."
FIND_NO_CONFIG_INSTALL = " 설치 폴더 안에도 설정 형식 파일이 없습니다."
DISC_EXCLUDED_TIP = "제외: 설정 형식이 아니거나 캐시라서 담지 않는 파일의 용량입니다."
DISC_TOTAL_V2 = "선택 {n}개 · 설정 파일 {files}개 · 약 {size}"
DISC_TOTAL_V2_ALL = "선택 {n}개 · 폴더 전체 파일 {files}개 · 약 {size}"
DISC_TOTAL_V2_UNKNOWN = " (확인 안 된 {k}개는 빠짐)"
DISC_TOTAL_V2_NONE = "선택 0개 · 설정 파일 0개 · 약 0 MB"
DISC_MORE_TOGGLE, DISC_MORE_TOGGLE_OPEN = "자세히 ▸", "자세히 ▾"
DISC_GRIP_TIP = "이 줄을 위아래로 끌면 표 높이를 바꿀 수 있습니다(↑ ↓ 키도 됩니다). 표는 최소 8줄을 유지합니다."
DISC_ADV_TOGGLE, DISC_ADV_TOGGLE_OPEN = "고급 옵션 ▸", "고급 옵션 ▾"
DISC_ALL_MODE = "폴더 전체 담기(설정 파일이 아닌 파일도 포함)"
DISC_ALL_MODE_NOTE = "기본은 설정 파일만 담습니다. 켜면 고른 폴더의 모든 파일을 담아 용량과 시간이 크게 늘고 민감한 정보가 섞일 수 있습니다."
DISC_ALL_MODE_CONFIRM_TITLE = "폴더 전체 담기"
DISC_ALL_MODE_CONFIRM = ("직접 고른 폴더의 모든 파일(설정 파일이 아닌 것 포함)을 설정 묶음 파일에 담습니다. 용량과 시간이 크게 늘 수 있고 "
                         "민감한 정보가 섞일 수 있습니다. 켤까요?")
EXP_MODE_ALL_NOTE = f"{ICON_WARN} 폴더 전체 담기가 켜져 있습니다. 직접 고른 폴더의 모든 파일을 담습니다."
G_EXTRA_MODE_UNSUPPORTED = "이 버전은 '폴더 전체 담기'를 지원하지 않습니다. 옵션을 끄고 진행해 주세요."

# ---- 화면 6: 신 PC 프로그램별 복원 선택, 되돌리기
PREV_PROG_TITLE = "프로그램별로 복원할지 고르기"
PREV_PROG_COLS = ("복원", "프로그램", "새로", "덮어씀", "같음")
PREV_PROG_NOTE = "체크를 끈 프로그램은 읽지도 쓰지도 않습니다."
PREV_PROG_DIRTY = "체크를 바꿨습니다. 미리보기를 다시 계산하는 중입니다. 계산되지 않으면 '선택대로 다시 계산'을 눌러 주세요."
PREV_PROG_NONE_SELECTED = f"{ICON_WARN} 복원할 프로그램이 하나도 체크되어 있지 않습니다. 하나 이상 체크해 주세요."
PREV_PROG_NA = "—"
BTN_PREV_RECALC = "선택대로 다시 계산"
PREV_STATUS["not_selected"] = "선택 안 함"
G_RESTORE_DIRTY = "프로그램 체크를 바꿨습니다. 미리보기를 선택대로 다시 계산한 뒤 진행해 주세요."
G_RESTORE_NONE = "복원할 프로그램이 하나도 체크되어 있지 않습니다. 하나 이상 체크해 주세요."
G_RESTORE_SELECT_UNSUPPORTED = "이 버전은 프로그램별 복원 선택을 지원하지 않습니다. 모든 프로그램에 체크를 켠 상태로 진행해 주세요."
G_RESTORE_UNKNOWN_PROGRAM = "이 묶음에 없는 프로그램입니다."
CONF_SELECTED_NOTE = "프로그램 {m}개 중 {n}개만 복원합니다(체크를 끈 {k}개는 읽지도 쓰지도 않습니다)."

RES_PROG_TITLE = "프로그램별 결과"
RES_PROG_COLS = ("프로그램", "새로", "덮어씀", "같음", "실패", "되돌리기")
BTN_UNDO = "되돌리기"
UNDO_NO_RECORD = "복원 기록이 없어 되돌릴 수 없습니다."
UNDO_RECORD_FAILED = "복원은 끝났지만 복원 기록을 저장하지 못해 되돌릴 수 없습니다."
UNDO_NOTHING = "덮어쓴 파일이 없어 되돌릴 것이 없습니다."
UNDO_NEW_KEPT = "새로 생긴 파일은 지우지 않았습니다."
UNDO_DONE_LABEL = "되돌림 {n}개"
UNDO_NOTE = "되돌리기는 덮어쓴 파일을 복원 전 백업 파일(.bak)로 되돌립니다. 새로 생긴 파일은 지우지 않습니다."
UNDO_CONFIRM_TITLE = "설정 되돌리기"
UNDO_CONFIRM = ("'{name}' 의 덮어쓴 파일 {n}개를 복원 전 백업 파일(.bak)로 되돌립니다.\n"
                "새로 생긴 파일 {k}개는 지우지 않습니다.{problems}\n\n진행할까요?")
UNDO_PROBLEMS = "\n되돌릴 수 없는 파일 {p}개는 그대로 둡니다(백업이 없거나 그 뒤에 바뀐 파일)."
UNDO_NOTHING_DIALOG = "'{name}' 은(는) 되돌릴 파일이 없습니다. 새로 생긴 파일은 지우지 않습니다."
UNDO_DONE = "{icon} '{name}' 되돌림: {n}개 되돌렸고 못 한 것 {f}개입니다. 새로 생긴 파일은 지우지 않았습니다."
TASK_UNDO_PREVIEW = "되돌리기 미리보기(실제로 바꾸지 않음)"
TASK_UNDO = "프로그램 설정 되돌리기"
PROGRESS[TASK_UNDO_PREVIEW] = "되돌릴 파일을 확인하는 중… 보통 몇 초 걸립니다. 아무것도 바꾸지 않습니다."
PROGRESS[TASK_UNDO] = "덮어쓴 파일을 백업 파일(.bak)로 되돌리는 중… 보통 몇 초 걸립니다. 새로 생긴 파일은 지우지 않습니다."
TASK_STEPS[TASK_UNDO_PREVIEW] = ("확인",)
TASK_STEPS[TASK_UNDO] = ("되돌리기",)
G_UNDO_UNSUPPORTED = "이 버전은 프로그램별 되돌리기를 지원하지 않습니다."
G_UNDO_UNKNOWN = "이 복원에서 되돌릴 수 있는 프로그램이 아닙니다."

# ---- 화면 7: 구 PC 마지막 화면
EXP_CHECK_TITLE = "설정 확인표"
EXP_CHECK_FOLD = "설정 확인표 ▸"
EXP_CHECK_FOLD_OPEN = "설정 확인표 ▾"
EXP_STOPPED_HEAD = f"{ICON_FAIL} 설정 묶음 파일 만들기가 중간에 멈췄습니다"
BTN_EXP_RETRY_SAVEPOINT = "다시 시도(저장점부터 이어서)"
BTN_EXP_RESTART = "처음부터 다시 만들기"
EXP_STOPPED_HINT = "원본은 읽기만 했으므로 바뀐 것이 없습니다. 이어서 하면 이미 담은 부분은 다시 읽지 않습니다."
AUTO_DONE_RETRY_OK = f"{ICON_OK} 읽지 못했던 파일을 모두 담았습니다. 모든 단계가 끝났습니다."
G_AUTO_RETRY_NA = "다시 시도할 수 있는 상태가 아닙니다. 처음 화면에서 다시 시작해 주세요."
DISC_COL_FILES_ALL = "파일 수(폴더 전체)"
DISC_COL_SIZE_FULL = "용량(폴더 전체)"

# 자체 이전이 필요한 프로그램(카카오톡, 금융보안, 백신, 드라이버, Office, Adobe 등): 폴더를 복사해서 옮길 수 없는 프로그램은 표 아래 접힌 구역에 모은다
DISC_SELF_TOGGLE, DISC_SELF_TOGGLE_OPEN = "자체 이전이 필요한 프로그램 {n}개 ▸", "자체 이전이 필요한 프로그램 {n}개 ▾"
DISC_SELF_HINT = ("폴더를 복사해서는 옮길 수 없는 프로그램입니다. 줄을 고르면 아래에 대신 할 일이 나옵니다. "
                  "체크할 수는 있지만 새 PC에서 제대로 동작하지 않을 수 있습니다. 위의 '전체 선택'은 이 구역을 선택하지 않습니다.")
DISC_SELF_COLS = ("선택", "구분", "프로그램", "대신 할 일")
DISC_SELF_BADGE = "자체 이전 필요"
DISC_SELF_DETAIL = "이 프로그램은 폴더를 복사해서 옮길 수 없습니다. {reason} {todo}"
DISC_SELF_TITLE = "안내"
DISC_SELF_CHECK = DISC_SELF_DETAIL + "\n\n체크는 그대로 두었습니다. 필요하지 않다면 체크를 해제해 주세요."
