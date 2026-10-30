"""'받은 파일 원래 위치에 놓기'(place) 화면의 문구(stage50). texts.py 가 불러와 자기 이름공간에 넣는다(texts_files.py 와 같은 방식).

규칙은 texts.py 와 같다: 비개발자 기준 쉬운 말, 존댓말, 금지 용어(해시/규칙/패키지/엄격 검사/저널/볼륨 등)와 명령줄 옵션 이름(--yes 등) 없음.
상수는 모두 texts(T) 의 이름으로 보인다(T.PL_*, T.BTN_PL_*, T.WHY_PL_*, T.G_PL_* 등).
"""
from __future__ import annotations


def install(ns: dict) -> None:
    ICON_OK, ICON_FAIL, ICON_WARN = ns["ICON_OK"], ns["ICON_FAIL"], ns["ICON_WARN"]
    PLACE = "place_files"

    # ---- 화면 이름/제목/단계
    TITLES = ns["TITLES"]
    TITLES[PLACE] = "② 내 파일 옮기기 · 받은 파일 원래 위치에 놓기"
    PL_HEADING = TITLES[PLACE]
    PL_SUB = ("받은 폴더의 파일을 이 PC의 원래 위치(같은 드라이브, 같은 경로)로 놓습니다. 먼저 미리보기로 무엇이 어디로 가는지 확인합니다. "
              "미리보기에서는 아무것도 바꾸지 않습니다.")
    PL_STEP_NAMES = ("받은 폴더", "미리보기", "놓기", "결과")
    PL_STEPS_LABEL = "진행 순서"

    # ---- 들어가는 길(기존 화면에 붙는 문구)
    BTN_PL_ENTER = "받은 파일을 원래 위치에 놓기 ▶"
    PL_ENTER_NOTE = ("다음 단계: 받은 파일은 아직 받은 폴더 안에 있습니다. 원래 위치(예: D:\\프로젝트)로 놓아야 도면의 참조 경로가 이어집니다.")
    BTN_PL_ENTER_LINK = "이미 받은 폴더를 원래 위치에 놓기"

    # ---- 입력
    PL_RECV_LABEL = "받은 폴더:"
    PL_RECV_HINT = "받기를 마친 폴더를 골라 주세요. 안에 D, E 같은 드라이브 이름의 폴더가 들어 있습니다."
    PL_FOUND_LABEL = "이 PC에서 찾은 받은 폴더:"
    PL_FOUND_ITEM = "{when}  {where}  {folder}"
    PL_PICK_TITLE = "받은 폴더 선택"
    PL_CHECK_NEED = "받은 폴더를 먼저 골라 주세요."
    PL_CHECK_OK = "받은 폴더로 보입니다(드라이브 폴더 {n}개: {drives})."
    PL_CHECK_OK_JOURNAL = "받기 기록이 있는 폴더입니다."
    PL_CHECK_NO_FOLDER = "받은 폴더가 없습니다: {path}"
    PL_CHECK_NOT_RECV = "이 폴더에서 받은 파일의 구조(D\\…, E\\… 같은 드라이브 폴더)를 찾지 못했습니다. 받기를 마친 폴더가 맞는지 확인해 주세요."
    PL_CHECK_UNREADABLE = "받은 폴더를 읽지 못했습니다: {path}"
    PL_CHECK_REPORT_DIR = "결과 리포트 위치가 폴더입니다. 파일 이름까지 적어 주세요: {path}"

    # ---- 고급 옵션
    PL_OPTIONS_TOGGLE, PL_OPTIONS_TOGGLE_OPEN = "고급 옵션 ▸", "고급 옵션 ▾"
    PL_MAP_TITLE = "드라이브 바꾸기"
    PL_MAP_LINE = "받은 폴더의"
    PL_MAP_TO = "를 이 PC의"
    PL_MAP_END = "드라이브에 놓기"
    BTN_PL_MAP_ADD, BTN_PL_MAP_DEL = "+ 줄 추가", "줄 지우기"
    PL_MAP_NOTE = "기본은 원래와 같은 드라이브 문자에 놓습니다. 새 PC에서 드라이브 문자가 다를 때만 써 주세요."
    PL_MAP_BAD = "드라이브 바꾸기 형식이 올바르지 않습니다. 예: D 를 E 로"
    PL_MAP_DUP = "같은 드라이브를 두 번 바꿀 수 없습니다: {letter}"
    PL_MAP_DST_DUP = "서로 다른 드라이브를 같은 드라이브에 놓을 수 없습니다."
    PL_KEEP = "복사만 하기(받은 폴더의 파일은 그대로 둠)"
    PL_KEEP_NOTE = "체크하면 받은 폴더에서 아무것도 지우거나 옮기지 않습니다. 복사본만큼 저장 공간이 더 필요합니다."
    PL_NO_OVERWRITE = "이미 있는 다른 내용의 파일은 덮어쓰지 않기"
    PL_NO_OVERWRITE_NOTE = "체크하면 건너뛰고 '못 놓은 것'에 올립니다. 체크하지 않으면 덮어쓰고, 기존 파일은 백업 파일(.bak)로 남깁니다."
    PL_UNVERIFIED = "받기가 끝나지 않은 폴더도 놓기(권장하지 않음)"
    PL_UNVERIFIED_NOTE = f"{ICON_WARN} 받기가 중간에 멈췄거나 정확한지 확인하지 않은 파일일 수 있습니다. 먼저 받기를 끝내고 검사하는 것이 안전합니다."
    PL_GATHER = "원래 위치에 놓을 수 없는 것을 한 곳에 모으기"
    PL_GATHER_NOTE = ("시스템 폴더, 없는 드라이브, 공간 부족 같은 이유로 놓지 못한 것을 한 폴더에 모읍니다. 모은 폴더에 '이전 안내.txt'(어디로 옮겨야 하는지)도 만듭니다.")
    PL_GATHER_LABEL = "모을 폴더:"
    PL_GATHER_DEFAULT_HINT = "비워 두면 이 폴더에 모읍니다: {path}"
    PL_GATHER_ONEDRIVE = (f"{ICON_WARN} 이 폴더는 OneDrive 와 연결되어 있어, 모은 파일이 인터넷으로 올라갈 수 있습니다. "
                          "OneDrive 밖의 폴더(예: D:\\이전 파일)를 권합니다.")
    BTN_PL_GATHER_OTHER = "다른 폴더 고르기"
    PL_GATHER_PICK_TITLE = "모을 폴더 선택"
    PL_REPORT_LABEL = "결과 리포트 저장 위치:"
    PL_REPORT_HINT = "놓은 뒤(멈췄거나 문제가 있어도) 항상 저장합니다. 미리보기에서는 저장하지 않습니다. 받은 폴더 안은 고를 수 없습니다."
    PL_REPORT_PICK_TITLE = "결과 리포트 저장"

    # ---- 버튼/이유
    BTN_PL_PREVIEW = "미리보기(P)"
    BTN_PL_GO = "놓기"
    BTN_PL_OPEN_RECV = "받은 폴더 열기"
    BTN_PL_OPEN_REPORT = "결과 리포트 열기"
    WHY_PL_RECV = "받은 폴더를 먼저 골라 주세요."
    WHY_PL_PREVIEW_FIRST = "미리보기를 먼저 해 주세요."
    WHY_PL_OPTIONS = "입력이나 옵션을 바꿨습니다. 미리보기를 다시 해 주세요."
    WHY_PL_NOTHING = "놓을 파일이 없습니다."
    WHY_PL_ACK = "확인 체크를 먼저 해 주세요."
    WHY_PL_GATHER_ONEDRIVE = "기본 모으기 폴더는 OneDrive 와 연결되어 있어 쓸 수 없습니다. 다른 폴더를 직접 골라 주세요."
    WHY_PL_PROBLEM = "입력을 확인해 주세요."

    # ---- 작업 이름/진행 문구
    TASK_PLACE_PREVIEW = "받은 파일 놓기 미리보기(실제로 바꾸지 않음)"
    TASK_PLACE = "받은 파일 원래 위치에 놓기"
    ns["PROGRESS"][TASK_PLACE_PREVIEW] = "받은 폴더를 읽어 어디에 놓을지 확인하는 중… 아무것도 바꾸지 않습니다. 아래 '중지'로 멈출 수 있습니다."
    ns["PROGRESS"][TASK_PLACE] = ("파일을 원래 위치로 놓는 중… 용량에 따라 몇 분에서 몇 시간 걸릴 수 있습니다. "
                                  "중지해도 같은 설정으로 다시 시작하면 이어서 합니다.")
    ns["TASK_STEPS"][TASK_PLACE_PREVIEW] = ("조사", "비교")
    ns["TASK_STEPS"][TASK_PLACE] = ("조사", "놓기", "정리")
    STOP_DONE_PLACE = ("중지했습니다. 이미 놓은 파일은 그대로이고 아직 놓지 않은 파일은 받은 폴더에 남아 있습니다. 같은 설정으로 미리보기부터 다시 하면 이어서 합니다.")
    STOP_DONE_PLACE_PREVIEW = "중지했습니다. 미리보기는 아무것도 바꾸지 않았으니 처음부터 다시 해도 안전합니다."

    # ---- 결과 요약
    PL_RESULT_EMPTY = "아직 미리보기를 하지 않았습니다. 아래 '미리보기'를 누르면 무엇이 어디로 가는지 여기에 나옵니다."
    PL_PREVIEW_HEAD = "미리보기입니다(아무것도 바꾸지 않았습니다)"
    PL_DONE_HEAD = "받은 파일을 원래 위치에 놓았습니다"
    PL_PREVIEW_STOPPED_HEAD = f"{ICON_FAIL} 미리보기가 중간에 멈췄습니다(일부만 확인했습니다 — {{why}})"
    PL_STOPPED_HEAD = f"{ICON_FAIL} 중간에 멈췄습니다(일부만 처리했습니다 — {{why}})"
    PL_WHY = {"cancelled": "사용자가 멈춤", "drive_disconnected": "드라이브 연결이 끊김", "disk_full": "저장 공간 부족", "error": "오류"}
    PL_STOPPED_NO_REPORT = ("강제로 멈췄습니다. 작업이 완전히 끝나는 대로 결과 리포트를 저장합니다. 이미 놓은 파일은 그대로이고 "
                            "나머지는 받은 폴더에 남아 있습니다. 저장 위치: {path}")
    PL_STOPPED_ERROR_LOG = "오류 내용은 오류 로그에 남겼습니다."
    PL_SUM_PLACE = f"{ICON_OK} 놓을 파일 {{n:,}}개 ({{size}})"
    PL_SUM_PLACED = f"{ICON_OK} 놓은 파일 {{n:,}}개 ({{size}})"
    PL_SUM_SAME_PREVIEW = "그중 이미 같은 파일이 있어 건너뛸 것 {n:,}개"
    PL_SUM_SAME = "이미 같은 파일이 있어 건너뜀 {n:,}개"
    PL_SUM_MANUAL = f"{ICON_WARN} 직접 옮길 것 {{n}}건"
    PL_SUM_FAILED = f"{ICON_FAIL} 실패 {{n}}개"
    PL_SUM_NOTHING = "원래 위치에 놓을 파일이 없습니다."
    PL_DELETE_TITLE = "삭제 예정(되돌릴 수 없음)"
    PL_DELETE_BIG = "받은 폴더에서 지울 파일 {n:,}개 / {size}"
    PL_DELETE_NOTE = "복사와 검사가 끝난 파일만 지웁니다. 지우지 않으려면 '복사만 하기'를 켜세요."
    PL_DELETE_NONE = "받은 폴더에서 지우는 파일은 없습니다."
    PL_REMOVED = "받은 폴더에서 정리한 파일 {n:,}개"
    PL_KEEP_NOTE_RESULT = "복사만 하기를 켰으므로 받은 폴더의 파일은 그대로 있습니다."
    PL_SAME_VOLUME_NOTE = "같은 드라이브에서는 이동하므로 저장 공간이 늘지 않습니다."
    PL_DEST_NEWER = (f"{ICON_WARN} 이미 있는 파일이 받은 파일보다 더 새로운 것이 {{n}}개 있습니다. "
                     "새 PC에서 이미 작업한 파일일 수 있으니 덮어쓰기 전에 확인해 주세요.")
    PL_DEST_NEWER_DONE = f"{ICON_WARN} 덮어쓰기 전에 기존 파일이 더 새로웠던 것이 {{n}}개 있습니다. 기존 파일은 백업 파일(.bak)로 남아 있습니다."
    PL_REMAINING = "받은 폴더에 남은 것: 파일 {files:,}개 / {size}. 빈 폴더는 지우지 않았습니다. 확인한 뒤 직접 지워 주세요."
    PL_UNREAD = f"{ICON_WARN} 끝까지 읽지 못한 곳이 {{n}}곳 있어 남은 파일을 정확히 알 수 없습니다."
    PL_RETRY_NOTE = ("실패한 파일은 받은 폴더에 그대로 있습니다. 원인을 해결한 뒤 '미리보기'를 다시 하고 '놓기'를 누르면 "
                     "실패한 파일부터 이어서 처리합니다.")
    PL_GATHER_RESULT = "모은 폴더: {folder} — 모은 항목 {n}건(모은 폴더 안의 '이전 안내.txt'에 어디로 옮길지 적혀 있습니다)"
    PL_GATHER_BLOCKED = f"{ICON_WARN} 모으는 폴더의 드라이브 여유 공간이 부족해 모으지 않았습니다."
    PL_GATHER_GUIDE_ERROR = f"{ICON_WARN} '이전 안내.txt'를 쓰지 못했습니다. 아래 '모은 항목' 표를 보고 직접 확인해 주세요."
    PL_SAVED = "결과 리포트: {path}"

    # ---- 표/접이식
    PL_FOLD_OVERWRITE = "덮어쓸 파일 {n}개 ▸"
    PL_FOLD_OVERWRITE_OPEN = "덮어쓸 파일 {n}개 ▾"
    PL_FOLD_OVERWRITTEN = "덮어쓴 파일 {n}개 ▸"
    PL_FOLD_OVERWRITTEN_OPEN = "덮어쓴 파일 {n}개 ▾"
    PL_FOLD_MANUAL = "못 놓은 것 {n}건 ▸"
    PL_FOLD_MANUAL_OPEN = "못 놓은 것 {n}건 ▾"
    PL_FOLD_GATHERED = "모은 항목 {n}건 ▸"
    PL_FOLD_GATHERED_OPEN = "모은 항목 {n}건 ▾"
    PL_FOLD_TODO = "직접 해야 할 일 ▸"
    PL_FOLD_TODO_OPEN = "직접 해야 할 일 ▾"
    PL_COLS_OVER = ("위치", "받은 쪽", "기존 쪽", "비고")
    PL_COLS_MANUAL = ("구분", "예상 위치", "현재 위치", "사유")
    PL_COLS_GATHER = ("구분", "원래 놓으려던 위치", "모은 위치", "사유")
    PL_KIND = {"skipped": "건너뜀", "failed": "실패", "leftover": "받은 폴더에서 못 지움", "leftover_preview": "받은 폴더에 남을 것(예상)",
               "file": "파일", "folder": "폴더", "partial": "폴더(일부)", "split": "조각 {n}개", "folder_n": "폴더({n:,}개)"}
    PL_OVER_NEWER = "기존 파일이 더 새로움"
    PL_OVER_BAK = "백업: {path}"
    PL_OVER_SIDE = "{size} · {when}"
    PL_TABLE_MORE = "표에는 앞의 {shown}건만 보입니다. 전체는 결과 리포트 파일에 있습니다."
    PL_ROW_HINT = "줄을 두 번 누르거나 Enter 를 누르면 탐색기로 그 위치를 보여 줍니다(보기만 합니다)."
    BTN_PL_OPEN_ROW = "폴더 보기"
    PL_TODO_SPLIT = ("4GB 넘는 파일은 조각으로 나뉘어 받은 폴더에 남아 있습니다. 조각을 하나로 합친 뒤 원래 위치에 직접 놓아 주세요"
                     "(합치는 기능은 이 화면에 없습니다. 명령 창의 'pcmigrate join' 으로 합칠 수 있으며 README 에 방법이 있습니다).")
    PL_TODO_PROTECTED = "시스템/보호 폴더(Windows, Program Files, 사용자 설정 폴더 등)는 자동으로 놓지 않습니다. 확인하고 직접 옮겨 주세요."
    PL_TODO_DRIVE = "이 PC에 없는 드라이브로 가는 파일은 드라이브를 연결하거나 '드라이브 바꾸기'를 써 주세요."
    PL_TODO_SPACE = "대상 드라이브의 여유 공간이 부족한 폴더는 놓지 않았습니다. 공간을 확보하거나 '드라이브 바꾸기'를 써 주세요."
    PL_TODO_UNKNOWN = "설정 묶음처럼 이름을 지정해 받은 폴더는 원래 위치를 알 수 없어 놓지 않았습니다. 필요하면 직접 옮겨 주세요."
    PL_TODO_NETWORK = "네트워크 공유 폴더가 원래 위치였던 파일은 자동으로 놓지 않습니다. 공유 폴더에 직접 복사해 주세요."
    PL_TODO_LOCKED = "다른 프로그램(CAD, 탐색기 미리보기 등)이 쓰는 파일이 있었습니다. 그 프로그램을 닫은 뒤 미리보기부터 다시 해 주세요."
    PL_TODO_LINK = "바로가기(링크)는 따라가지 않고 건너뛰었습니다. 링크 너머는 따로 확인해 직접 옮겨 주세요."
    PL_TODO_NOOVERWRITE = "덮어쓰지 않기로 해서 건너뛴 파일은 확인한 뒤 직접 옮기거나, 옵션을 끄고 다시 해 주세요(기존 파일은 백업 파일로 남습니다)."
    PL_TODO_NAME = "Windows 에서 쓸 수 없는 이름의 파일은 이름을 바꿔 직접 옮겨 주세요."
    PL_TODO_UNVERIFIED = "받기가 끝나고 확인되지 않은 폴더는 놓지 않았습니다. 먼저 받기를 마치고 검사해 주세요."
    PL_TODO_OTHER = "표의 '사유'를 보고 직접 처리해 주세요."
    PL_TODO_LEFTOVER = "받은 폴더에 남은 파일은 확인한 뒤 직접 지워 주세요. 이 도구는 받은 폴더의 빈 폴더와 기록 파일을 지우지 않습니다."
    PL_TODO_WARNINGS = f"{ICON_WARN} 확인할 경고 {{n}}건(자세한 내용은 결과 리포트의 '경고' 항목):"
    PL_TODO_PATHS = (f"{ICON_WARN} 파일 안에 적힌 경로(도면의 외부 참조 등)는 고쳐 쓰지 않습니다. "
                     "드라이브를 바꿨다면 도면을 열어 참조가 이어지는지 확인해 주세요.")
    PL_NOTES_TITLE = "참고(작업 중 알려 드린 내용)"

    # ---- 확인창/안내
    PL_ACK = "받은 폴더의 파일이 지워진다는 것과, 구 PC의 원본이 그대로 있다는 것을 확인했습니다."
    PL_CONFIRM_TITLE = "받은 파일을 원래 위치에 놓을까요?"
    PL_CONFIRM = ("받은 파일을 원래 위치로 놓습니다.\n\n"
                  "· 놓을 파일: {n:,}개 ({size})\n· 덮어쓰는 파일: {over:,}개 (기존 파일은 백업 파일(.bak)로 보관됩니다)\n{delete}\n"
                  "· 직접 옮겨야 할 것: {manual}건\n· 결과 리포트: {report}\n\n계속할까요?")
    PL_CONFIRM_DELETE = "· 받은 폴더에서 지우는 파일: {n:,}개 / {size}  ← 되돌릴 수 없습니다"
    PL_CONFIRM_DELETE_NONE = "· 받은 폴더에서 지우는 파일: 없음"
    PL_CONFIRM_KEEP = "· 받은 폴더의 파일은 지우지 않습니다(복사만)."
    PL_HELP = ("무엇을 하는 화면인가요?\n받은 폴더에 모인 파일을 이 PC의 원래 위치(같은 드라이브·같은 경로)로 놓습니다.\n\n"
               "안전한가요?\n미리보기는 아무것도 바꾸지 않습니다. 놓을 때 덮어쓰는 파일은 백업 파일(.bak)로 남기고, 복사한 파일은 검사한 뒤에만 받은 폴더에서 지웁니다. "
               "멈췄다가 같은 설정으로 미리보기부터 다시 하면 이어서 합니다.\n\n다음 단계\n'직접 해야 할 일'을 확인하고, 도면의 외부 참조가 이어지는지 열어 봅니다.")
    ns["HELP"][PLACE] = PL_HELP
    G_PL_BUSY_ABANDONED = "강제로 멈춘 작업이 아직 끝나지 않았습니다. 잠시 뒤에 다시 시도해 주세요."
    G_PL_NEED_PREVIEW = "미리보기를 먼저 해 주세요. 입력이나 옵션이 바뀌면 미리보기를 다시 해야 합니다."
    G_PL_NEED_ACK = "받은 폴더의 파일이 지워진다는 확인에 체크해 주세요."
    G_PL_NEED_CONFIRM = "놓기 확인이 필요합니다. 확인창에서 '예'를 눌러 주세요."
    G_PL_REPORT_INSIDE = "결과 리포트는 받은 폴더 밖에 저장해 주세요."
    G_PL_ENTER_FIRST = "받은 폴더를 먼저 골라 주세요."
    PL_REPORT_FALLBACK = "결과 리포트를 {path} 에 저장하지 못해 {alt} 에 저장했습니다."
    PL_REPORT_FAILED = "결과 리포트를 저장하지 못했습니다. 화면의 요약은 그대로 보입니다. 오류 로그를 확인해 주세요."
    PL_ERR_PREFLIGHT = "아무것도 옮기지 않았습니다. {reason}"
    PL_REPORT_PROBE = "결과 리포트를 저장할 수 없습니다: {path}. 다른 위치를 골라 주세요."

    # ---- 못 놓은 이유(place_original.REASONS 의 사유 코드 -> 화면용 문구: 명령줄 옵션 이름 없이)
    PL_REASON_NETWORK = "네트워크 공유 폴더(\\\\서버\\공유)가 원래 위치라 자동으로 놓지 않음. 공유 폴더에 직접 복사해 주세요."  # 코드 'unc'(문구 검사에 걸리지 않게 사전 밖에 둔다)
    PL_REASON = {
        "protected_system": "시스템/보호 폴더라 자동으로 놓지 않음. Windows, Program Files, ProgramData, 사용자 AppData, 시작 프로그램 같은 위치는 직접 확인하고 옮겨 주세요.",
        "drive_missing": "대상 드라이브가 이 PC에 없음. 드라이브를 연결하거나 '드라이브 바꾸기'로 다른 드라이브를 지정해 주세요.",
        "unsafe_name": "Windows 에서 쓸 수 없는 이름(예약된 이름, 끝의 점/공백, 금지 문자, 너무 긴 이름). 이름을 바꿔 직접 옮겨 주세요.",
        "link": "바로가기(링크)라 따라가지 않음. 링크 너머는 따로 확인해 직접 옮겨 주세요.",
        "inside_receive": "놓을 위치가 받은 폴더 안이라 건너뜀. 받은 폴더를 다른 위치로 옮긴 뒤 다시 하거나 직접 옮겨 주세요.",
        "same_location": "이미 원래 위치에 있음(받은 폴더 경로와 같음)",
        "no_space": "대상 드라이브의 여유 공간이 부족함. 필요량은 이 드라이브로 가는 모든 폴더의 합계입니다. 공간을 확보하거나 '드라이브 바꾸기', '한 곳에 모으기'를 써 주세요.",
        "no_overwrite": "대상에 다른 내용의 파일이 있어 덮어쓰지 않음('덮어쓰지 않기' 체크). 확인 후 직접 옮기거나 체크를 끄고 다시 해 주세요(기존 파일은 백업 파일로 남습니다).",
        "dest_blocked": "놓을 위치에 같은 이름의 파일이 폴더 자리를 막고 있음. 그 파일의 이름을 바꾸거나 옮긴 뒤 다시 해 주세요.",
        "unknown_location": "원래 위치를 알 수 없는 폴더/파일(설정 묶음처럼 이름을 지정해 받은 것). 자동으로 놓지 않으니 필요하면 직접 옮겨 주세요.",
        "unverified": "받기가 끝나고 확인된 항목이 아님. 먼저 받기를 마치고 검사해 주세요. 알고 진행하려면 '받기가 끝나지 않은 폴더도 놓기'를 체크해 주세요.",
        "readonly_drive": "쓸 수 없는 드라이브(CD/DVD)",
        "bad_journal": "받기 기록의 항목이 올바르지 않아 놓지 않음",
        "receive_unreadable": "받은 폴더를 읽지 못함",
        "source_not_removed": "복사는 검사까지 끝났지만 받은 폴더의 파일을 지우지 못함. 대상에는 온전히 있습니다. 받은 폴더의 파일이 열려 있거나 권한이 없는지 확인한 뒤 미리보기부터 다시 하거나 직접 지워 주세요.",
        "source_changed": "복사한 뒤에 받은 폴더의 파일이 바뀌었거나 링크가 되어 지우지 않음. 대상의 사본은 복사한 때의 내용입니다. 두 파일을 비교해 남길 쪽을 직접 정해 주세요.",
        "source_readonly": "받은 폴더의 파일이 읽기 전용이고 속성을 풀지 못해 지우지 못함. 대상에는 온전히 있습니다. 읽기 전용 속성을 풀고 다시 하거나 직접 지워 주세요.",
        "source_ads": "받은 폴더의 파일에 대체 데이터 스트림(NTFS 부가 정보)이 있어 대상에 복사되지 않음. 필요 없으면 받은 폴더 쪽만 지우고, 필요하면 대상에 직접 옮겨 주세요.",
        "source_ads_unknown": "받은 폴더 파일의 대체 데이터 스트림(NTFS 부가 정보) 여부를 확인하지 못해 지우지 않음. 대상에는 복사돼 있으니 필요 없는지 확인한 뒤 직접 지워 주세요.",
        "split_parts": "4GB 분할 조각(.pcm###)이라 자동으로 놓지 않음. 하나로 합친 뒤 원래 위치에 직접 놓으세요(조각만 옮기면 합치기가 끊깁니다).",
        "map_conflict": "'드라이브 바꾸기' 때문에 받은 폴더의 서로 다른 드라이브가 같은 드라이브로 합쳐져 건너뜀. 드라이브 바꾸기를 고치거나 해당 드라이브를 따로 처리해 주세요.",
    }

    ns.update({k: v for k, v in locals().items() if k.isupper()})
