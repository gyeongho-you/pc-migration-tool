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

OLD_FLOW = (OLD_SCAN, OLD_DISCOVER, OLD_CHECKLIST, OLD_EXPORT)
NEW_FLOW = (NEW_PACKAGE, NEW_PREVIEW, NEW_CONFIRM, NEW_RESULT)

TITLES = T.TITLES  # 화면 제목은 texts.py 에서 관리


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
    preview_checked: bool = False  # '미리보기를 확인했습니다'
    final_confirmed: bool = False  # 확인 대화상자에서 '예'
    extra_consent: bool = False  # 규칙 없이 선택 포함된 파일 복원 동의(--allow-extra)
    import_result: Any = None  # api.ImportResult (mode=applied)
    # 대용량 복사
    transfer_src: str = ""
    transfer_dst: str = ""
    transfer_preview_key: tuple | None = None  # 마지막으로 dry-run 한 (src, dst)
    transfer_report: Any = None  # copier.Report
    transfer_was_dry_run: bool = True
    transfer_mode: str = "external"  # external | network | local (texts.MODE_*)
    transfer_stage: int = 1  # 외장/랜선은 1단계(구 PC), 2단계(새 PC)
    transfer_blocked: str = ""  # 미리보기에서 드러난 시작 불가 이유(여유 공간 부족, FAT32 4GB 초과)
    transfer_progress: Any = None  # progress.ProgressInfo
    verify_manifest: str = ""  # 2단계(랜선): 목록 파일
    verify_target: str = ""  # 2단계(랜선): 검사할 폴더
