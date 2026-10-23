"""화면에 보이는 한국어 문구를 한 곳에 모은 모듈(비개발자 기준 쉬운 말, 일관된 존댓말).

문구를 고칠 때는 이 파일만 수정하면 된다. 테스트(tests/test_gui_texts.py)가 다음을 지킨다:
- 모든 상수가 비어 있지 않다.
- 개발 용어(dry-run, manifest, SHA-256, robocopy, extra, strict, UNC)가 사용자에게 보이는 문구에 그대로 나오지 않는다
  (필요한 경우 쉬운 말로 풀고, 괄호 안에 .bak 같은 파일 이름만 작게 덧붙인다).
- 뷰/컨트롤러 코드의 한글 문자열도 같은 금지 용어를 쓰지 않는다.
테스트는 가능하면 여기 상수를 참조하므로 문구를 바꿔도 테스트가 깨지지 않는다.
"""
from __future__ import annotations

# 화면 이름(state.py 의 상수와 같은 값)
START, OLD_SCAN, OLD_DISCOVER, OLD_CHECKLIST, OLD_EXPORT = "start", "old_scan", "old_discover", "old_checklist", "old_export"
NEW_PACKAGE, NEW_PREVIEW, NEW_CONFIRM, NEW_RESULT, TRANSFER = "new_package", "new_preview", "new_confirm", "new_result", "transfer"

APP_TITLE = "PC 이전 도우미"
FONT_FAMILIES = ("Malgun Gothic", "맑은 고딕", "Segoe UI")
FONT_SIZE = 10

# --------------------------------------------------------------------------- 공통 버튼/표지
BTN_NEXT = "다음"
BTN_BACK = "이전"
BTN_HOME = "처음으로"
BTN_CANCEL = "취소"
BTN_CLOSE = "닫기"
BTN_OK = "확인"
BTN_BROWSE = "찾아보기..."
BTN_HELP = "?"
HELP_TOOLTIP = "이 화면 도움말(F1)"
BTN_DETAIL_SHOW = "상세 보기 ▸"
BTN_DETAIL_HIDE = "상세 보기 ▾"
NO_DETAIL = "(자세한 정보 없음)"
READY = "준비됨"
CRUMB_HOME = "처음 화면"
ICON_OK, ICON_FAIL, ICON_WARN = "✔", "✖", "⚠"
CHECKED, UNCHECKED, LOCKED = "☑", "☐", "—"
HEADER_ADMIN = {True: "관리자 권한: 예", False: "관리자 권한: 아니오", None: "관리자 권한: 알 수 없음"}

LICENSE_NOTE = "프로그램 라이선스(정품 인증)는 옮겨지지 않습니다. 새 PC 에서 각 프로그램에 다시 로그인하거나 인증해 주세요."
LICENSE_BANNER = f"{ICON_WARN} 라이선스는 복원하지 않습니다. 체크리스트로 안내만 해 드립니다. " + LICENSE_NOTE
PRIVACY_NOTE = ("이 파일에는 이 PC 에 설치된 프로그램 목록과 폴더 경로 같은 개인 정보가 들어 있습니다. "
                "공유 저장소에 올리거나 다른 사람에게 보내기 전에 내용을 확인해 주세요.")
READ_ONLY_NOTE = "원본(기존 파일)은 읽기만 하며 바꾸거나 지우지 않습니다."

# --------------------------------------------------------------------------- 시작 화면
START_HEADING = "무엇을 하시겠습니까?"
START_INTRO = ("새 PC 로 업무 환경을 옮기는 일을 도와드립니다. 아래에서 하려는 일을 하나 고르세요.\n"
               "기존 PC 의 파일은 읽기만 하고 바꾸지 않습니다.\n"
               "실제로 파일을 만들거나 덮어쓰기 전에는 반드시 미리보기와 확인을 거칩니다.")
START_CHOICES = (
    ("구 PC (내보내기)", "설치된 프로그램과 설정을 조사하고, 설정 패키지(.zip)를 만듭니다.", "조사 시작하기", OLD_SCAN),
    ("신 PC (가져오기)", "만들어 둔 설정 패키지를 먼저 미리 확인한 뒤, 새 PC 에 복원합니다.", "복원 시작하기", NEW_PACKAGE),
    ("대용량 복사", "도면·프로젝트 같은 큰 폴더를 원본은 건드리지 않고 복사한 뒤, 정확히 복사됐는지 검사합니다.", "복사 시작하기", TRANSFER),
)
SAFETY_TITLE = "안전 원칙"
SAFETY_LINES = (
    "기존(원본) 파일은 읽기만 하고 수정하거나 지우지 않습니다.",
    "복원이나 복사는 미리보기(실제로 바꾸지 않음)를 확인한 뒤에만 실행됩니다. 덮어쓸 파일은 백업 파일(.bak)로 남깁니다.",
    "규칙에 없는 프로그램은 자동으로 포함되지 않습니다. 직접 고른 것만 포함됩니다.",
    "비밀번호·열쇠 파일처럼 민감한 정보가 들어 있을 수 있는 항목은 이해했다고 직접 표시해야 고를 수 있습니다.",
)

# --------------------------------------------------------------------------- 화면 제목
TITLES = {
    START: "시작", OLD_SCAN: "1단계. 프로그램 조사", OLD_DISCOVER: "2단계. 규칙에 없는 프로그램 확인",
    OLD_CHECKLIST: "3단계. 해야 할 일 목록", OLD_EXPORT: "4단계. 설정 패키지 만들기",
    NEW_PACKAGE: "1단계. 패키지 선택", NEW_PREVIEW: "2단계. 검사와 미리보기", NEW_CONFIRM: "3단계. 복원 확정",
    NEW_RESULT: "4단계. 결과", TRANSFER: "대용량 복사",
}

# --------------------------------------------------------------------------- 진행 표시(작업 이름, 예상 시간, 메시지)
TASK_SCAN, TASK_DISCOVER, TASK_CHECKLIST = "프로그램 조사", "규칙에 없는 프로그램 찾기", "해야 할 일 목록 만들기"
TASK_EXPORT, TASK_PREVIEW, TASK_APPLY = "설정 패키지 만들기", "패키지 검사와 미리보기", "복원"
TASK_TRANSFER, TASK_TRANSFER_PREVIEW = "대용량 복사", "복사 미리보기(실제로 바꾸지 않음)"
PROGRESS = {
    TASK_SCAN: "설치된 프로그램을 조사하는 중… 보통 10~60초 걸립니다.",
    TASK_DISCOVER: "규칙에 없는 프로그램의 설정 폴더를 찾는 중… 폴더 크기를 재느라 보통 30~90초 걸립니다.",
    TASK_CHECKLIST: "해야 할 일 목록을 만드는 중… 보통 10~60초 걸립니다.",
    TASK_EXPORT: "설정 패키지를 만드는 중… 파일 수에 따라 수십 초에서 몇 분 걸립니다. 원본은 읽기만 합니다.",
    TASK_PREVIEW: "패키지를 검사하고 미리보기를 만드는 중… 보통 몇 초에서 1분 걸립니다. 아무것도 쓰지 않습니다.",
    TASK_APPLY: "복원하는 중… 파일 수에 따라 수십 초에서 몇 분 걸립니다. 덮어쓰는 파일은 백업 파일(.bak)로 남깁니다.",
    TASK_TRANSFER: "복사하고 검사하는 중… 용량에 따라 몇 분에서 몇 시간 걸릴 수 있습니다. 취소해도 나중에 이어서 할 수 있습니다.",
    TASK_TRANSFER_PREVIEW: "무엇이 복사될지 확인하는 중… 아무것도 쓰지 않습니다.",
}
STAGE_LABEL = "지금 하는 일"
BUSY_NOTE = "화면이 멈춘 것처럼 보여도 작업은 진행 중입니다. 끝날 때까지 기다려 주세요."
CANCELLING = "취소하는 중입니다…"
CANCELLED = "{task}을(를) 취소했습니다. 진행 기록은 남아 있어 같은 설정으로 다시 실행하면 이어서 합니다."
ERROR_STATUS = "문제가 생겨 작업을 마치지 못했습니다."
SCAN_PROGRESS = PROGRESS[TASK_SCAN]
CHECKLIST_PROGRESS = PROGRESS[TASK_CHECKLIST]
EXPORT_PROGRESS = PROGRESS[TASK_EXPORT]
PREVIEW_PROGRESS = PROGRESS[TASK_PREVIEW]
APPLY_PROGRESS = PROGRESS[TASK_APPLY]
GO_HOME_BUSY = "작업이 끝난 뒤에 처음으로 돌아갈 수 있습니다."
CLOSE_TITLE = "종료 확인"
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
G_NEED_EXPORT_CONFIRM = "패키지를 저장할 위치를 확인하는 단계가 필요합니다."
G_NEED_PACKAGE = "복원할 패키지(.zip) 파일을 먼저 선택해 주세요."
G_NEED_PREVIEW = "먼저 미리보기를 만들어 주세요(패키지를 바꾸면 미리보기를 다시 만들어야 합니다)."
G_PACKAGE_BLOCKED = "패키지 검사에 실패했습니다. 문제를 해결하기 전에는 복원할 수 없습니다."
G_NEED_CHECK_PREVIEW = "'미리보기를 확인했습니다'에 체크해 주세요."
G_NEED_EXTRA_CONSENT = "직접 고른 추가 항목이 들어 있습니다. 이 항목들을 복원하겠다는 별도 동의가 필요합니다."
G_NEED_FINAL_YES = "확인 창에서 '예'를 눌러야 합니다."
G_NEED_SOURCE = "원본 폴더를 선택해 주세요."
G_NEED_DEST = "대상 폴더를 선택해 주세요."
G_SOURCE_MISSING = "원본 폴더를 찾을 수 없습니다: {path}"
G_SOURCE_OK = "원본 폴더가 있습니다. 원본은 읽기만 하며 바꾸거나 지우지 않습니다."
G_DEST_INSIDE = "대상 폴더가 원본 폴더와 같거나 그 안에 있습니다. 다른 위치를 골라 주세요."
G_DEST_OK = "대상 폴더가 원본 폴더 밖에 있습니다."
G_TRANSFER_CONFIRM = "대상 폴더에 파일을 쓰기 전에 확인이 필요합니다."
G_TRANSFER_NEED_PREVIEW = "먼저 '미리보기 실행'으로 무엇이 복사되는지 확인해 주세요."
G_NOTHING_TO_SAVE = "저장할 결과가 없습니다."

# 버튼 옆에 보여 줄 "지금 누를 수 없는 이유"
WHY_SCAN_FIRST = "먼저 '조사 시작'을 눌러 주세요."
WHY_PREVIEW_FIRST = "미리보기를 먼저 실행해 주세요."
WHY_PATH_PROBLEM = "원본과 대상 폴더를 올바르게 선택해 주세요."
WHY_NEED_OUTPUT = "저장할 파일 위치를 먼저 선택해 주세요."
WHY_BUSY = "다른 작업이 끝날 때까지 기다려 주세요."
WHY_PACKAGE_FIRST = "복원할 패키지 파일을 먼저 선택해 주세요."
WHY_CHECKLIST_FIRST = "먼저 '목록 만들기'를 눌러 주세요."

# --------------------------------------------------------------------------- 구 PC 1) 조사
SCAN_HEADING = TITLES[OLD_SCAN]
SCAN_SUB = "이 PC 에 설치된 프로그램을 읽고, 알려진 프로그램의 규칙과 맞춰 봅니다. 읽기만 하며 아무것도 쓰지 않습니다."
BTN_SCAN = "조사 시작"
SCAN_IDLE = "아직 조사하지 않았습니다."
SCAN_RESULT = "설치 프로그램 {programs}개를 확인했습니다. 규칙과 맞는 프로그램 {rules}개{more}"
SCAN_RESULT_EXTRA = ", 규칙에 없는 프로그램 {n}개"
SCAN_TABLE_TITLE = "규칙과 맞는 프로그램(자동으로 설정을 모을 수 있는 프로그램)과 맞춘 근거"
COL_PROGRAM_RULE, COL_EVIDENCE = "프로그램", "근거"

# --------------------------------------------------------------------------- 구 PC 2) 규칙에 없는 프로그램
DISC_HEADING = TITLES[OLD_DISCOVER]
DISC_SUB = ("규칙이 없는 프로그램의 설정 폴더 후보입니다. 아무것도 자동으로 포함되지 않습니다. "
            "설정 패키지에 넣을 항목만 직접 고르세요(줄을 클릭하면 선택/해제).")
BTN_DISCOVER = "찾기 시작"
DISC_HINT = "읽기만 합니다. 폴더 크기를 재느라 1분 안팎 걸릴 수 있으며, 아래 '취소'로 멈출 수 있습니다."
DISC_SUMMARY = "규칙에 없는 프로그램 {n}개, 설정 폴더 후보 {m}개 (민감한 정보 가능성 {k}개)"
DISC_COLS = ("선택", "주의", "프로그램", "위치", "크기", "연결 근거")
DISC_WARN_SECRET = f"{ICON_WARN} 민감"
DISC_WARN_WEAK = "근거 약함"
DISC_BANNER = ("{icon} 민감한 정보가 들어 있을 수 있는 항목 {n}개는 붉게 표시됩니다(열쇠 파일, 비밀번호, 로그인 정보 등). "
               "정말 필요할 때만 아래 항목에 체크한 뒤 고르세요.")
DISC_ACK = f"{ICON_WARN} 민감한 정보(열쇠 파일, 비밀번호, 로그인 정보 등)가 포함될 수 있음을 이해했습니다 (체크해야 주의 표시 항목을 고를 수 있습니다)"
DISC_ACK_DIALOG_TITLE = "민감한 정보 포함 확인"
DISC_ACK_DIALOG = ("열쇠 파일, 클라우드 로그인 정보, 브라우저에 저장된 비밀번호 같은 민감한 정보가 들어 있을 수 있는 항목을 "
                   "고를 수 있게 합니다.\n설정 패키지 파일을 안전하게 보관할 수 있습니까?")
DISC_SELECTED = "선택한 항목: {n}개"
DISC_SELECTED_BIG = " (1GB 이상 {n}개 — 큰 폴더는 '대용량 복사'를 권장합니다)"
DISC_DETAIL = "{kind}: {path}\n연결 근거: {grade}{reason}"
DISC_DETAIL_SECRET = "\n" + ICON_WARN + " 민감한 정보 가능성: {names}"
KIND_FOLDER, KIND_FILE, NO_PROGRAM = "폴더", "파일", "(연결된 프로그램 없음)"

# --------------------------------------------------------------------------- 구 PC 3) 해야 할 일 목록
CL_HEADING = TITLES[OLD_CHECKLIST]
CL_SUB = "새 PC 에서 직접 해야 할 일입니다. 체크하면서 진행하고, 파일로 저장해 두면 나중에도 볼 수 있습니다."
BTN_CL_MAKE = "목록 만들기"
BTN_CL_SAVE = "파일로 저장..."
CL_PRIVACY = f"{ICON_WARN} 저장한 파일에는 개인 정보가 들어 있습니다."
CL_TABS = ("요약", "1. 먼저 설치", "2. 로그인·인증", "3. 데이터·기타", "참고")
CL_SHOW_ALL = "이 PC 에 관련 없는 경고도 모두 보기"
CL_HIDDEN = "이 PC 에 관련 없는 경고 {n}건을 숨겼습니다."
CL_FILTER = "검색:"
CL_EMPTY = "(해당 없음)"
CL_PROGRESS_MARK = "체크 {done}/{total}"
CL_SAVE_TITLE = "해야 할 일 목록 저장"
CL_SAVE_CONFIRM = "다음 위치에 저장합니다:\n{path}\n\n개인 정보 주의: " + PRIVACY_NOTE + "\n(같은 이름의 파일이 있으면 .bak 파일로 보관합니다)"
SAVED_TITLE = "저장 완료"
SAVED_BODY = "저장했습니다:\n{path}"
CL_GUIDE = (
    "Windows 앱 설치 도구(winget)로 한 번에 설치할 수 있는 프로그램이 있습니다. 구 PC 에서 만든 패키지 안의 programs/winget.json 파일을 "
    "새 PC 로 옮긴 뒤, 명령 프롬프트에서 아래 명령을 직접 실행하세요(이 도구가 대신 실행하지는 않습니다):",
    "winget import -i winget.json --ignore-unavailable --accept-package-agreements --accept-source-agreements",
)
CL_SUMMARY_INSTALL = "1. 새 PC 에서 먼저 설치: 직접 설치 {a}건, 설치 도구로 개별 설치 {b}건 (설치 도구로 한 번에 설치 {c}건은 별도)"
CL_SUMMARY_LOGIN = "2. 로그인·인증: {n}건"
CL_SUMMARY_DATA = "3. 데이터 복사·기타: {n}건"
CL_SUMMARY_REF = "참고(따로 설치하지 않아도 되는 것으로 보임): {n}건"
CL_SUMMARY_OMITTED = "이 PC 에 관련 없는 경고 {n}건은 숨겼습니다('모두 보기'로 확인할 수 있습니다)."
CL_SUMMARY_DISC = "규칙에 없는 프로그램 {n}개{m} — '규칙에 없는 프로그램 확인' 화면에서 직접 고른 것만 패키지에 들어갑니다."
CL_SUMMARY_DISC_M = ", 설정 폴더 후보 {m}개"
CL_SUMMARY_LICENSE = "라이선스는 복원되지 않습니다. 모든 항목은 직접 확인해 주세요."
CL_HIDDEN_MARK = "(이 PC 에서 감지되지 않음)"  # checklist.py 가 붙이는 표시와 같아야 한다(테스트로 확인)

# --------------------------------------------------------------------------- 구 PC 4) 설정 패키지 만들기
EXP_HEADING = TITLES[OLD_EXPORT]
EXP_SUB = "알려진 프로그램의 설정·템플릿·단축키와 프로그램 목록을 하나의 .zip 파일로 만듭니다. " + READ_ONLY_NOTE
EXP_PATH_LABEL = "저장할 파일:"
BTN_EXPORT = "패키지 만들기"
EXP_SAVE_TITLE = "설정 패키지 저장 위치"
EXP_PLAN = "포함될 내용: 규칙과 맞는 프로그램 {rules}개의 설정 + 프로그램 목록 + 직접 고른 항목 {n}개."
EXP_PLAN_SECRET = "\n" + ICON_WARN + " 고른 항목 중 민감한 정보 가능성이 있는 것이 {k}개 들어갑니다. 패키지 파일을 안전하게 보관해 주세요."
EXP_PLAN_NONE = "\n(규칙에 없는 항목은 고르지 않았으므로 들어가지 않습니다.)"
EXP_NEED_PATH_TITLE = "저장 위치"
EXP_NEED_PATH = "저장할 파일 위치를 먼저 선택해 주세요."
EXP_CONFIRM_TITLE = "패키지 만들기 확인"
EXP_CONFIRM = "다음 위치에 설정 패키지(.zip)를 만듭니다:\n{path}{exists}\n\n기존(원본) 파일은 읽기만 하고 바꾸지 않습니다. 계속하시겠습니까?"
EXP_EXISTS = "\n※ 같은 이름의 파일이 있으면 .bak 파일로 보관합니다."
BTN_OPEN_LOCATION = "결과 위치 열기"
EXP_DONE = "패키지를 만들었습니다: {path}"
EXP_RULES = "포함된 규칙: {rules}"
EXP_NO_RULES = "(없음)"
EXP_COUNTS = "파일 {files}개, 건너뜀 {skipped}개, 실패 {failed}개"
EXP_FAILED_HEAD = f"{ICON_WARN} 일부 파일을 읽지 못했습니다(아래 목록). 해당 파일을 사용 중인 프로그램을 닫고 다시 만들어 보세요."
EXP_TODO = "새 PC 에서 직접 해야 할 일(일부):"

# --------------------------------------------------------------------------- 신 PC
PKG_HEADING = TITLES[NEW_PACKAGE]
PKG_SUB = "구 PC 에서 만든 설정 패키지(.zip)를 선택하세요. 이 단계에서는 아무것도 쓰지 않습니다."
PKG_LABEL = "패키지 파일:"
PKG_OPEN_TITLE = "설정 패키지 선택"
PKG_NOTE = "패키지 안의 모든 파일은 정확한지 검사하며(해시), 검사에 실패하면 복원할 수 없습니다.\n" + LICENSE_NOTE
BTN_PREVIEW_NEXT = "검사하고 미리보기"
PKG_NEED_TITLE = "패키지 선택"
PKG_NEED = "패키지 파일(.zip)을 먼저 선택해 주세요."

PREV_HEADING = TITLES[NEW_PREVIEW]
PREV_SUB = "아래는 복원했을 때 일어날 일입니다. 지금은 아무것도 바꾸지 않았습니다."
BTN_TO_CONFIRM = "복원 확정 단계로"
PREV_NONE = "미리보기가 없습니다. 이전 단계에서 패키지를 선택해 주세요."
PREV_SUMMARY = "새로 만들 파일 {new}개, 덮어쓸 파일 {over}개(백업 파일 .bak 으로 남김), 같아서 건너뛸 파일 {same}개"
PREV_PROBLEMS = f"{ICON_FAIL} 패키지에 문제가 있어 복원할 수 없습니다:\n{{items}}\n→ 구 PC 에서 패키지를 다시 만들어 복사해 보세요."
PREV_OK = f"{ICON_OK} 패키지 검사 통과(모든 파일 내용이 정확함)"
PREV_COLS = ("처리", "복원 위치", "백업 파일(.bak) 위치", "구분")
PREV_STATUS = {"would_copy": "새로 만듦", "would_overwrite": "덮어씀(백업함)", "skipped_identical": "같음(건너뜀)"}
PREV_KIND_RULE, PREV_KIND_EXTRA = "규칙", "직접 고른 항목"
PREV_WARN_TITLE = "새 PC 에서 직접 해야 할 일(자동 복원 불가)"

CONF_HEADING = TITLES[NEW_CONFIRM]
CONF_SUB = "여기서부터는 실제로 파일이 만들어지거나 덮어써집니다. 기본은 닫혀 있고, 아래를 모두 확인해야 실행할 수 있습니다."
CONF_INFO = "패키지: {package}\n새로 만들 파일 {new}개, 덮어쓸 파일 {over}개"
CONF_CHECK = "미리보기를 확인했습니다 (덮어쓸 파일은 백업 파일(.bak)로 남는다는 것을 이해했습니다)"
CONF_EXTRA_TITLE = "직접 고른 추가 항목"
CONF_EXTRA_INFO = ("패키지에 사용자가 직접 골라 넣은 파일 {n}개가 있습니다. 새 PC 의 같은 위치 파일을 덮어쓸 수 있으니 "
                   "미리보기에서 내용을 확인한 뒤 동의해 주세요.")
CONF_EXTRA_CHECK = "이 항목들도 복원하는 데 동의합니다(별도 동의)"
BTN_APPLY = "복원 실행"
CONF_DIALOG_TITLE = "복원 실행 확인"
CONF_DIALOG = ("파일 {n}개를 복원합니다.\n덮어쓰는 파일은 백업 파일(.bak)로 남깁니다.\n"
               "되돌리려면 백업 파일을 직접 원래 이름으로 바꿔야 합니다.\n\n지금 실행하시겠습니까?")

RES_HEADING = TITLES[NEW_RESULT]
RES_NOT_YET = "아직 복원하지 않았습니다."
RES_COLS = ("구분", "위치", "내용")
RES_KIND_FAIL, RES_KIND_BAK, RES_KIND_COPY = "실패", "백업", "복사"
RES_BAK_DETAIL = "덮어쓰기 전 파일을 보관함(.bak)"
BTN_OPEN_RESTORED = "복원된 폴더 열기"
RES_ALL_OK = f"{ICON_OK} 모든 파일을 복원하고 확인했습니다."
RES_IMPORT_FAIL = (f"{ICON_FAIL} 실패한 파일이 있습니다. 아래 '실패' 줄의 내용을 확인하세요. "
                   "파일을 사용 중인 프로그램을 닫거나 권한·저장 공간 문제를 해결한 뒤, 같은 패키지로 다시 복원하면 "
                   "이미 복원된 파일은 건너뛰고 실패한 파일만 다시 시도합니다.")

# --------------------------------------------------------------------------- 대용량 복사
TR_HEADING = TITLES[TRANSFER]
TR_SUB = ("원본 폴더의 모든 파일을 대상 폴더로 복사하고, 복사된 파일이 정확한지 하나씩 검사합니다(해시). "
          + READ_ONLY_NOTE + " 중간에 멈추면 같은 설정으로 다시 실행해 이어서 할 수 있습니다.")
TR_SRC, TR_DST, TR_REPORT = "원본 폴더:", "대상 폴더:", "결과 파일 저장(선택):"
TR_DST_HINT = "외장 드라이브(E:\\)나 공유 폴더 경로(\\\\컴퓨터이름\\폴더)를 직접 입력해도 됩니다."
TR_REPORT_HINT = "복사 결과를 파일(.json)로 남깁니다."
TR_PICK_FOLDER = "폴더 선택"
TR_REPORT_TITLE = "결과 파일 저장 위치"
TR_OPTIONS = "옵션"
TR_STRICT = "엄격 검사: 모든 파일을 처음부터 다시 검사"
TR_STRICT_NOTE = ("   이어서 복사할 때 이전 검사 기록을 믿지 않고 전부 다시 확인합니다. 내용이 어긋난 파일은 원본에서 다시 복사합니다"
                  "(기존 사본은 .bak 파일로 보관). 시간이 더 걸립니다.")
TR_CLOUD = "OneDrive 클라우드 전용 파일도 포함 (기본 꺼짐)"
TR_CLOUD_NOTE = ("   클라우드 전용 파일은 이 PC 에 내용이 없어서, 복사하려면 인터넷으로 모두 내려받아야 합니다. "
                 "용량과 시간이 크게 늘 수 있어 기본으로 건너뛰고, 결과에 '클라우드 전용'으로 따로 표시합니다.")
BTN_TR_PREVIEW = "① 미리보기 실행"
BTN_TR_GO = "② 복사 시작"
BTN_TR_SAVE = "결과 파일 저장..."
BTN_TR_CLOUD = "클라우드 전용 파일 보기"
TR_CLOUD_WIN = "클라우드 전용 파일(건너뜀)"
TR_CONFIRM_TITLE = "복사 확인"
TR_CONFIRM = ("다음 위치에 복사합니다(원본은 읽기만 합니다):\n\n원본: {src}\n대상: {dst}\n\n"
              "대상 폴더에 복사한 파일과 작업 기록 파일 2개(이름이 .pcmigrate- 로 시작)가 만들어집니다."
              "{cloud}\n\n시작하시겠습니까? (진행 중 '취소'로 멈추고 나중에 이어서 할 수 있습니다)")
TR_CONFIRM_CLOUD = "\n⚠ OneDrive 클라우드 전용 파일을 포함하므로 대량으로 내려받을 수 있습니다."
TR_COLS = ("구분", "파일", "내용")
TR_PREVIEW_RESULT = ("미리보기 결과(실제로 바꾸지 않음): 새로 복사 {new}개, 덮어씀 {over}개, 이미 같음 {same}개, "
                     "클라우드 전용(건너뜀) {cloud}개. 이상이 없으면 '② 복사 시작'을 눌러 주세요.")
TR_RESULT = "복사 결과: 정확히 복사됨 {ok}개, 내용 다름 {mismatch}개, 빠짐 {missing}개, 실패 {failed}개, 클라우드 전용(건너뜀) {cloud}개"
TR_RESULT_REPAIRED = ", 다시 복사해 바로잡음 {n}개"
TR_FAIL_HINT = (f"{ICON_FAIL} 정확히 복사되지 않은 파일이 있습니다. 아래 목록을 확인하고, 원인(파일을 쓰는 프로그램, 연결 끊김, 저장 공간)을 "
                "해결한 뒤 같은 설정으로 다시 실행해 주세요. 이미 정확히 복사된 파일은 건너뜁니다.")
TR_STATUS_LABELS = {"mismatch": "내용 다름", "missing": "빠짐", "failed": "실패"}

# --------------------------------------------------------------------------- 결과 요약(성공/건너뜀/실패)
RESULT_OK, RESULT_SKIPPED, RESULT_FAILED = "성공", "건너뜀", "실패"
RESULT_LINE = "{ok_label} {ok}  /  {skip_label} {skipped}  /  {fail_label} {failed}"

# --------------------------------------------------------------------------- 오류 대화상자
ERR_WHAT, ERR_HOW = "무엇이 잘못됐나요?", "어떻게 하면 되나요?"
ERR_PATH_MISSING = ("지정한 폴더나 파일을 찾을 수 없습니다{path}.",
                    "경로를 다시 확인하고, 외장 드라이브나 공유 폴더가 연결돼 있는지 확인해 주세요.")
ERR_PERMISSION = ("권한이 없어서 접근할 수 없습니다{path}.",
                  "다른 위치(예: 문서 폴더)를 고르거나, 프로그램을 '관리자 권한으로 실행'해 보세요.")
ERR_DISK_FULL = ("저장 공간이 부족합니다{path}.",
                 "여유 공간이 있는 드라이브를 고르거나 불필요한 파일을 정리한 뒤 다시 시도해 주세요.")
ERR_DISCONNECTED = ("드라이브나 네트워크 연결이 끊어졌습니다{path}.",
                    "케이블이나 공유 폴더 연결을 확인한 뒤 같은 설정으로 다시 실행하세요. 복사는 이어서 합니다.")
ERR_IN_USE = ("다른 프로그램이 이 파일을 사용하고 있습니다{path}.",
              "CAD 등 해당 프로그램을 닫은 뒤 다시 시도해 주세요.")
ERR_OS_GENERIC = ("파일을 읽거나 쓰는 중 문제가 생겼습니다{path}.", "경로와 권한, 저장 공간을 확인한 뒤 다시 시도해 주세요.")
ERR_TRANSFER = ("복사를 시작할 수 없습니다: {msg}", "원본과 대상 폴더를 다시 선택해 주세요.")
ERR_API_HOW = "메시지를 확인한 뒤 다시 시도해 주세요. 자세한 내용은 '상세 보기'에 있습니다."
ERR_API_RULES = "프로그램 설치 폴더 안의 rules 폴더가 손상됐을 수 있습니다. 설치 폴더를 다시 복사해 주세요."
ERR_API_PACKAGE = "패키지가 손상됐거나 중간에 바뀐 것 같습니다. 구 PC 에서 다시 만들어 복사해 주세요."
ERR_API_SECRET = "민감한 정보가 포함될 수 있는 항목의 선택을 해제하거나, 내용을 확인한 뒤 이해했다는 표시를 하고 다시 시도해 주세요."
ERR_API_SELECTION = "고른 항목이 이 PC 에 없거나 바뀌었습니다. 규칙에 없는 프로그램 확인 화면에서 다시 골라 주세요."
ERR_UNEXPECTED = ("예상하지 못한 문제가 발생했습니다: {name}: {msg}",
                  "프로그램을 다시 실행해 보고, 같은 문제가 반복되면 '상세 보기' 내용을 담당자에게 전달해 주세요.")
ERR_TITLES = {"os": "파일을 읽거나 쓸 수 없습니다", "api": "작업을 할 수 없습니다", "transfer": "복사를 시작할 수 없습니다",
              "guard": GUARD_TITLE, "unexpected": "예상하지 못한 문제가 발생했습니다"}

# 결과 표의 '내용' 칸에 나오는 내부 메시지를 쉬운 말로 바꾸는 표(앞에서부터 차례로 적용)
DETAIL_REPLACEMENTS = (
    ("SHA-256 불일치", "내용이 원본과 다름"),
    ("크기/해시 불일치", "내용이 원본과 다름"),
    ("복사 후 크기/해시 검증 실패", "복사한 파일이 정확하지 않음"),
    ("재복사 후 크기/해시 검증 실패", "다시 복사했지만 파일이 정확하지 않음"),
    ("크기 불일치", "크기가 원본과 다름"),
    ("해시", "내용 검사값"),
    ("robocopy 종료코드", "복사 도구 종료 코드"),
    ("robocopy", "복사 도구"),
    ("manifest", "목록 파일"),
    ("(extra)", "(직접 고른 항목)"),
    ("extra", "직접 고른 항목"),
    ("strict", "엄격 검사"),
    ("UNC", "공유 폴더"),
    ("zip", "패키지"),
)

# --------------------------------------------------------------------------- 도움말(?) — 무엇을 하는 화면 / 안전한가 / 다음 단계
HELP_TITLE = "도움말 — {screen}"
HELP = {
    START: ("무엇을 하는 화면인가요?\n새 PC 로 옮기는 일 중 하나를 고르는 첫 화면입니다.\n\n"
            "안전한가요?\n이 화면에서는 아무것도 읽거나 쓰지 않습니다.\n\n다음 단계\n구 PC 에서는 '구 PC', 새 PC 에서는 '신 PC', "
            "도면·사진 같은 큰 폴더는 '대용량 복사'를 고르세요."),
    OLD_SCAN: ("무엇을 하는 화면인가요?\n이 PC 에 설치된 프로그램을 조사하고, 설정을 자동으로 모을 수 있는 프로그램을 찾습니다.\n\n"
               "안전한가요?\n네. 읽기만 하며 아무것도 쓰지 않습니다.\n\n다음 단계\n조사가 끝나면 '다음'으로 규칙에 없는 프로그램을 확인합니다."),
    OLD_DISCOVER: ("무엇을 하는 화면인가요?\n규칙이 없어서 자동으로 모으지 못하는 프로그램의 설정 폴더 후보를 보여 줍니다. 필요한 것만 직접 고릅니다.\n\n"
                   "안전한가요?\n찾는 동안은 읽기만 합니다. 고른 항목만 패키지에 들어가고, 민감한 정보가 있을 수 있는 항목(붉게 표시)은 "
                   "이해했다는 표시를 해야 고를 수 있습니다.\n\n다음 단계\n'다음'으로 해야 할 일 목록을 만듭니다."),
    OLD_CHECKLIST: ("무엇을 하는 화면인가요?\n새 PC 에서 직접 해야 할 일(프로그램 설치, 로그인·인증, 데이터 복사 등)을 정리해 줍니다.\n\n"
                    "안전한가요?\n목록을 만들 때는 읽기만 합니다. 파일로 저장하면 개인 정보가 들어 있으니 공유에 주의하세요.\n\n"
                    "다음 단계\n저장해 둔 뒤 '다음'으로 설정 패키지를 만듭니다."),
    OLD_EXPORT: ("무엇을 하는 화면인가요?\n설정과 프로그램 목록을 하나의 .zip 패키지 파일로 만듭니다.\n\n"
                 "안전한가요?\n원본은 읽기만 합니다. 쓰는 것은 지정한 .zip 파일뿐이며, 쓰기 전에 위치를 확인합니다. 라이선스는 들어가지 않습니다.\n\n"
                 "다음 단계\n만든 .zip 을 새 PC 로 옮긴 뒤, 새 PC 에서 '신 PC (가져오기)'를 고르세요."),
    NEW_PACKAGE: ("무엇을 하는 화면인가요?\n구 PC 에서 만든 설정 패키지(.zip)를 고르는 화면입니다.\n\n"
                  "안전한가요?\n네. 이 단계에서는 아무것도 쓰지 않습니다.\n\n다음 단계\n'검사하고 미리보기'를 누르세요."),
    NEW_PREVIEW: ("무엇을 하는 화면인가요?\n패키지가 정확한지 검사하고, 복원하면 어떤 파일이 새로 만들어지거나 덮어써지는지 미리 보여 줍니다.\n\n"
                  "안전한가요?\n네. 지금은 실제로 바꾸지 않았습니다.\n\n다음 단계\n문제가 없으면 '복원 확정 단계로' 이동합니다."),
    NEW_CONFIRM: ("무엇을 하는 화면인가요?\n실제로 복원하기 전의 마지막 확인 화면입니다.\n\n"
                  "안전한가요?\n체크와 확인 창을 모두 거쳐야 실행됩니다. 덮어쓰는 파일은 백업 파일(.bak)로 남습니다.\n\n"
                  "다음 단계\n체크하고 '복원 실행'을 누르면 결과 화면으로 이동합니다."),
    NEW_RESULT: ("무엇을 하는 화면인가요?\n복원 결과(성공/건너뜀/실패)를 보여 줍니다.\n\n안전한가요?\n이 화면은 보기만 합니다.\n\n"
                 "다음 단계\n실패가 있으면 원인을 해결하고 같은 패키지로 다시 복원하세요. 라이선스는 직접 인증해야 합니다."),
    TRANSFER: ("무엇을 하는 화면인가요?\n도면·프로젝트 같은 큰 폴더를 다른 드라이브나 공유 폴더로 복사하고, 정확히 복사됐는지 검사합니다.\n\n"
               "안전한가요?\n원본은 읽기만 합니다. 먼저 '미리보기 실행'(실제로 바꾸지 않음)을 해야 '복사 시작'이 열립니다. 대상이 원본 안이면 막습니다.\n\n"
               "다음 단계\n미리보기 → 복사 시작 → 결과 확인. 중간에 멈춰도 같은 설정으로 다시 실행하면 이어서 합니다."),
}


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
