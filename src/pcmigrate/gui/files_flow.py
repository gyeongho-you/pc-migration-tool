"""'내 파일 옮기기'(②)의 상태와 순수 계산(stage30a). tkinter 와 무관해서 화면 없이 시험할 수 있다.

WizardState 에 필드를 더하지 않고 이 모듈의 FilesState 를 컨트롤러가 따로 들고 있다(병합 충돌을 줄이기 위해).
화면(views/find.py, method.py, lan_recv.py, lan_send.py)은 컨트롤러의 메서드만 부른다.
"""
from __future__ import annotations

import hashlib
import os
import queue
from dataclasses import dataclass, field
from typing import Any

from .. import find_data as fd
from ..progress import fmt_bytes
from . import texts as T

FIND_CATEGORIES = tuple(c for c, _label in T.FD_CATS)  # ("design", "photo", "video", "doc")
FIND_TIME_LIMIT = 300.0  # 초: 이 시간이 지나면 지금까지 찾은 결과를 보여 준다(찾기는 읽기만 한다)


def norm(path: str) -> str:
    """경로 비교용 키(대소문자·구분자 차이를 같게 본다)."""
    return os.path.normcase(os.path.normpath(path))


@dataclass
class DataRow:
    """폴더 표의 한 줄."""
    path: str
    kind: str  # 화면에 보일 종류(프로젝트 폴더 / 내 문서 폴더 / 직접 추가)
    counts: dict  # 분류 -> 파일 수 (직접 추가한 폴더는 빈 딕셔너리)
    total_bytes: int | None  # None = 아직 모름(직접 추가)
    total_files: int | None
    latest: str  # YYYY-MM-DD 또는 ""
    basis: str
    cloud_files: int
    cloud_bytes: int
    manual: bool
    selected: bool

    @property
    def size_text(self) -> str:
        return fmt_bytes(self.total_bytes) if self.total_bytes is not None else "-"

    def count_text(self, cat: str) -> str:
        if self.manual:
            return "-"
        n = int(self.counts.get(cat, 0) or 0)
        return f"{n:,}" if n else "0"


@dataclass
class FilesState:
    # ---- 1/4 찾기
    result: Any = None  # find_data.FindResult
    find_error: str = ""
    find_cancelled: bool = False
    filters: set = field(default_factory=lambda: set(FIND_CATEGORIES))
    selected: set = field(default_factory=set)  # norm(path)
    extra: list = field(default_factory=list)  # 직접 추가한 폴더(입력한 그대로)
    junk: bool = False  # 정크 파일 제외(기본 꺼짐)
    # ---- 2/4 방법
    method: str = ""  # direct | external | share
    role: str = ""  # send | receive
    use_selection: bool = False  # 복사 화면이 '선택 목록' 흐름인가
    transfer_items: list = field(default_factory=list)  # 복사 화면에 넘긴 SelectedItem 목록
    bundle_include: bool = False  # ② 첫 화면의 '설정 묶음 함께 보내기' 체크(기본 꺼짐, 자동으로 켜지 않는다)
    tx_bundle: bool = False  # 마지막으로 보낸 목록에 설정 묶음이 들어 있었는가(결과 문구용)
    signature: str = ""
    # ---- 3/4 받는 쪽
    dest: str = ""
    rx: Any = None  # lan_transfer.Receiver 핸들
    rx_state: str = "idle"  # idle | waiting | connected | receiving | done | stopped | error
    rx_code: str = ""
    rx_addresses: list = field(default_factory=list)
    rx_kinds: dict = field(default_factory=dict)
    rx_warnings: list = field(default_factory=list)
    rx_log: list = field(default_factory=list)  # 사용자에게 보일 최근 소식(최대 8줄)
    rx_info: Any = None  # 마지막 ProgressInfo
    rx_plan: dict | None = None
    rx_result: dict | None = None
    rx_error: str = ""
    rx_queue: queue.Queue = field(default_factory=queue.Queue)  # 받기 스레드 -> 화면 스레드
    # ---- 3/4 보내는 쪽
    found: list = field(default_factory=list)  # 찾은 PC(lan_link.Receiver)
    found_message: str = ""
    target: str = ""
    code: str = ""
    diag: str = ""
    diag_code: str = ""  # 마지막 연결 진단의 결과 코드(subnet 이면 이더넷 주소 맞추기 안내)
    tx_report: dict | None = None
    tx_error: str = ""
    tx_error_code: str = ""
    tx_cancelled: bool = False


# --------------------------------------------------------------------------- 문구 도우미
def lan_error_text(code: str, message: str = "") -> str:
    """LanError.code -> 쉬운 한국어(알 수 없는 코드는 기본 안내)."""
    return T.LAN_ERRORS.get(code) or T.LAN_ERROR_DEFAULT


def short_date(iso: str) -> str:
    return iso[:10] if iso else ""


def kind_text(item: Any) -> str:
    if getattr(item, "kind", "") == "user_folder":
        return T.FD_KIND_USER.format(label=fd.USER_FOLDER_LABELS.get(item.label, item.label))
    return T.FD_KIND_PROJECT


def row_from_item(item: Any, selected: bool) -> DataRow:
    counts = {c: int((item.categories.get(c) or {}).get("files", 0)) for c in fd.CATEGORIES}
    return DataRow(path=item.path, kind=kind_text(item), counts=counts, total_bytes=int(item.total_bytes or 0),
                   total_files=int(item.total_files or 0), latest=short_date(item.latest_mtime), basis=item.basis or "",
                   cloud_files=int(item.cloud_only_files or 0), cloud_bytes=int(item.cloud_only_bytes or 0), manual=False,
                   selected=selected)


def row_from_manual(path: str, selected: bool) -> DataRow:
    return DataRow(path=path, kind=T.FD_KIND_MANUAL, counts={}, total_bytes=None, total_files=None, latest="",
                   basis=T.FD_BASIS_MANUAL, cloud_files=0, cloud_bytes=0, manual=True, selected=selected)


def row_visible(row: DataRow, filters: set) -> bool:
    """종류별 보기: 네 종류가 모두 켜져 있으면 전부, 일부만 켜면 그 종류의 파일이 있는 폴더만(직접 추가한 폴더는 항상)."""
    if row.manual or set(FIND_CATEGORIES) <= set(filters):
        return True
    return any(row.counts.get(c, 0) for c in filters)


def signature_of(paths: list[str], junk: bool) -> str:
    """선택 목록의 짧은 표식(복사 화면의 미리보기가 같은 선택에 대한 것인지 알아보는 데 쓴다)."""
    raw = "\n".join(sorted(norm(p) for p in paths)) + ("|junk" if junk else "")
    return "선택목록:" + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]
