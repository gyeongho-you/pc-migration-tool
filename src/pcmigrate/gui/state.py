"""마법사의 상태. tkinter 와 무관한 순수 데이터."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import texts as T

# 화면 이름
START = "start"
OLD_SCAN, OLD_DISCOVER, OLD_CHECKLIST, OLD_EXPORT = "old_scan", "old_discover", "old_checklist", "old_export"
NEW_PACKAGE, NEW_PREVIEW, NEW_CONFIRM, NEW_RESULT = "new_package", "new_preview", "new_confirm", "new_result"
TRANSFER = "transfer"
AUTO = "auto_run"  # 추천대로 한 번에(진행 화면 하나)

OLD_FLOW = (OLD_SCAN, OLD_DISCOVER, OLD_CHECKLIST, OLD_EXPORT)
NEW_FLOW = (NEW_PACKAGE, NEW_PREVIEW, NEW_CONFIRM, NEW_RESULT)

TITLES = T.TITLES  # 화면 제목은 texts.py 에서 관리


def overall_step(screen: str, transfer_mode: str = "", transfer_stage: int = 1) -> tuple[int, int, str, str] | None:
    """마법사 맨 위의 '전체 단계 n/N' 표시용 (n, N, 흐름 이름, 화면 제목). 시작 화면은 None.

    구 PC / 새 PC 흐름은 4단계, 대용량 복사는 외장/랜선이면 2단계(구 PC에서 보내기 -> 새 PC에서 받아 검사), 같은 PC 면 1단계.
    """
    if screen in OLD_FLOW:
        return OLD_FLOW.index(screen) + 1, len(OLD_FLOW), T.OVERALL_FLOWS["old"], TITLES[screen]
    if screen in NEW_FLOW:
        return NEW_FLOW.index(screen) + 1, len(NEW_FLOW), T.OVERALL_FLOWS["new"], TITLES[screen]
    if screen == TRANSFER:
        if transfer_mode in (T.MODE_EXTERNAL, T.MODE_NETWORK):
            stage = transfer_stage if transfer_stage in (1, 2) else 1
            return stage, 2, T.OVERALL_FLOWS["transfer"], T.OVERALL_TRANSFER_STAGES[stage]
        return 1, 1, T.OVERALL_FLOWS["transfer"], TITLES[screen]
    return None


def overall_names(screen: str, transfer_mode: str = "") -> tuple[str, ...]:
    """맨 위 단계 표시줄에 번호와 함께 보여 줄 단계 이름들(overall_step 의 N 개와 같은 개수)."""
    if screen in OLD_FLOW:
        return T.OVERALL_STEP_NAMES["old"]
    if screen in NEW_FLOW:
        return T.OVERALL_STEP_NAMES["new"]
    if screen == TRANSFER:
        return T.OVERALL_STEP_NAMES["transfer" if transfer_mode in (T.MODE_EXTERNAL, T.MODE_NETWORK) else "local"]
    return ()


@dataclass
class WizardState:
    screen: str = START
    rules_dir: str = ""
    # 공통
    busy: bool = False
    busy_label: str = ""
    # 구 PC 흐름
    scan: Any = None  # api.ScanResult
    discovery: Any = None  # discover.Discovery
    selected: set = field(default_factory=set)  # 선택한 후보 key (root, rel)
    secrets_ack: bool = False  # '비밀 정보가 포함될 수 있음을 이해했습니다'
    checklist: Any = None  # api.ChecklistResult
    export_result: Any = None  # api.ExportResult
    export_output: str = ""
    # 신 PC 흐름
    package: str = ""
    preview: Any = None  # api.ImportResult (mode=dry-run)
    preview_package: str = ""  # 미리보기를 만든 패키지 경로(바뀌면 무효)
    preview_checked: bool = False  # '미리보기에서 덮어쓸 파일을 확인했습니다'
    bak_understood: bool = False  # '백업 파일(.bak)이 남는다는 것을 이해했습니다'(두 번째 확인)
    final_confirmed: bool = False  # 확인 대화상자에서 '예'
    extra_consent: bool = False  # 규칙 없이 선택 포함된 파일 복원 동의(--allow-extra)
    import_result: Any = None  # api.ImportResult (mode=applied)
    # 대용량 복사
    transfer_src: str = ""
    transfer_dst: str = ""
    transfer_preview_key: tuple | None = None  # 마지막으로 dry-run 한 (src, dst)
    transfer_preview_opts: tuple | None = None  # 미리보기를 만들 때의 (클라우드 포함, 엄격 검사). 달라지면 미리보기 무효
    transfer_options: tuple = (False, False)  # 지금 화면의 (클라우드 포함, 엄격 검사)
    transfer_report: Any = None  # copier.Report
    transfer_was_dry_run: bool = True
    transfer_mode: str = "external"  # external | network | local (texts.MODE_*)
    transfer_stage: int = 1  # 외장/랜선은 1단계(구 PC), 2단계(새 PC)
    transfer_split: bool = False  # 지금 화면의 '4GB 넘는 파일을 조각으로 나눠 복사' 체크
    transfer_preview_split: bool | None = None  # 미리보기를 만들 때의 같은 값(달라지면 미리보기 무효)
    transfer_blocked: str = ""  # 미리보기에서 드러난 시작 불가 이유(여유 공간 부족, FAT32 4GB 초과)
    transfer_progress: Any = None  # progress.ProgressInfo
    verify_manifest: str = ""  # 2단계(랜선): 목록 파일
    verify_target: str = ""  # 2단계(랜선): 검사할 폴더
