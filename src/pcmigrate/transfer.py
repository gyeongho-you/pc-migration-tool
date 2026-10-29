"""모드 B: 대용량 데이터 전송(robocopy 복사 + SHA-256 검사 + 이어받기).

복사는 Windows 내장 robocopy 로 하고, 끝난 뒤 원본/대상을 읽어 크기 + SHA-256 을 비교한다.
- 원본은 읽기만 한다. 원본을 바꿀 수 있는 옵션(/MIR /PURGE /MOV /MOVE)은 코드로 금지한다.
- 대상 폴더의 .pcmigrate-state.json(줄 단위 JSON)에 파일별 검사 결과를 이어 적어 재실행 시 건너뛴다.
  파일이 수백만 개여도 한 줄씩 덧붙이므로 매번 전체를 다시 쓰지 않는다. 손상/불일치하면 무시하고 전체를 다시 검사한다.
- 검사에서 읽는 횟수(파일 하나당): 복사할 때 원본 1회(robocopy) + 검사 때 원본 1회(해시) + 대상 1회(해시).
  원본 해시는 상태 파일(같은 크기/수정시각) 또는 해시 목록(use_manifest)에 있으면 다시 읽지 않는다. 대상은 항상 다시 읽는다.
- 네트워크(공유 폴더)로 복사할 때는 대상을 네트워크로 다시 읽지 않도록 검사를 미루고(해시 목록 manifest_out),
  새 PC 에서 목록과 비교한다(verify).
- OneDrive 클라우드 전용(온디맨드) 파일은 기본으로 건너뛰고 cloud_only 로 기록한다.
- 옵션 split_large(--split-large): 4GiB-1 바이트를 넘는 파일은 robocopy 에서 빼고 split.py 가 <이름>.pcm001... 조각으로 나눠 쓴다.
  조각 단위로 이어받고(상태 파일에 조각별 기록), 조각 해시/전체 해시는 해시 목록(parts)에 기록된다. 새 PC 에서 pcmigrate join 으로 합친다.
- 전송 중 시스템 절전을 막고(SetThreadExecutionState), 끝나면 반드시 되돌린다.
"""
from __future__ import annotations

# 기존 모듈 레벨 이름(표준 라이브러리/하위 모듈 포함)을 그대로 유지한다: `transfer.os`, `transfer.subprocess` 등.
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
import stat as _stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import failure, fs, junk as junk_mod, split  # noqa: F401
from .cancel import Cancelled, checkpoint
from .copier import (CLOUD_ONLY, FAILED, MISMATCH, MISSING, PENDING_VERIFY, SKIPPED_SAME, VERIFIED, WOULD_COPY,
                     WOULD_OVERWRITE, FileResult, IoStats, Report, copy_verified, repair_file, sha256_file, verify_file)
from .engine import clamp_workers, run_ordered
from .fs import long_path
from .manifest import ManifestReader, ManifestWriter
from .power import KeepAwake
from .progress import OK, ProgressInfo, ProgressTracker, categorize, fmt_bytes, format_info, slow_policy
from .scan import ScanInfo, is_cloud_only, scan_source_info  # noqa: F401  (is_cloud_only 는 기존 import 경로 유지)

from .transfer_base import (  # noqa: F401
    AttrGetter,
    _bak_name,
    DEFAULT_CHUNK,
    _differs,
    DISCONNECTED_MESSAGE,
    DriveDisconnected,
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    FILE_ATTRIBUTE_RECALL_ON_OPEN,
    _first,
    _friendly_makedirs,
    _is_within,
    _join,
    LOG_NAME,
    MAX_DETAIL_RESULTS,
    MAX_PRESERVED_LISTED,
    MAX_XF_CHARS,
    MTIME_TOLERANCE_NS,
    OWN_PATTERN,
    PRESERVE_NAME,
    Runner,
    _same_path,
    SPACE_MARGIN,
    STATE_COMPACT_FACTOR,
    STATE_FLUSH_EVERY,
    STATE_NAME,
    STATE_VERSION,
    TransferError,
)
from .transfer_robocopy import (  # noqa: F401
    base_args,
    _call_runner,
    cancellable_runner,
    _COPY_LINE,
    COPY_MODES,
    default_attr_getter,
    DEFAULT_COPY_THREADS,
    default_runner,
    _ERR_LINE,
    FORBIDDEN_PREFIXES,
    interpret_exit,
    make_runner,
    NO_WINDOW,
    parse_copy_line,
    parse_copy_path,
    _stop,
    validate_args,
)
from .transfer_state import (  # noqa: F401
    read_state_entries,
    State,
)
from .transfer_preserve import (  # noqa: F401
    DestScan,
    _is_partial_prefix,
    _needed_bytes,
    _preserve_existing,
    _PreserveFailed,
    _read_journal,
    _read_journal_raw,
    _rollback,
    _scan_dest,
    _write_journal,
)
from .transfer_split import (  # noqa: F401
    _note_split_backups,
    _split_copy_entry,
    SplitCtx,
    _state_conflict,
    _verify_split_entry,
)
from .transfer_verify import (  # noqa: F401
    _add_result,
    _repair_and_reverify,
    _verify_phase,
)
from .transfer_copy import (  # noqa: F401
    _alive_checker,
    _copy_with_robocopy,
    _dry_run,
)
from .transfer_run import (  # noqa: F401
    transfer,
)
