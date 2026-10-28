"""모드 A: 설정 패키지(.zip) 내보내기 / 가져오기 (하위 호환 파사드).

실제 구현은 package_*.py 모듈로 나뉘어 있고, 이 모듈은 기존 import 경로를 유지하기 위해
이름을 다시 내보내기만 한다. (테스트/모듈 속성 패치는 실제 사용 모듈 기준으로 해야 한다.)

패키지 구조::

    manifest.json            파일별 SHA-256/크기, 규칙 이름/버전, 수동 작업 경고
    programs/winget.json     winget export 원문(가능한 경우)
    programs/registry.json   레지스트리 Uninstall 기반 프로그램 목록
    data/<dest>/...          규칙 paths 에 따라 모은 설정 파일

안전 원칙:
- export 는 원본을 읽기만 한다. 라이선스 파일(*.lic)은 포함하지 않고 체크리스트로만 안내한다.
- import 는 manifest 를 신뢰하지 않는다. 규칙 id/base 가 현재 규칙과 일치해야 하고,
  모든 엔트리 경로와 복원 대상이 허용된 루트 밖으로 나가지 않는지(zip 슬립) 검증한다.
- 해시 검증이 하나라도 실패하면 복원하지 않는다.
- 덮어쓸 파일은 copier 와 동일하게 .bak 으로 백업한다.
"""
from __future__ import annotations

from .package_base import (  # noqa: F401
    ALLOWED_COMPRESSION,
    CHUNK,
    DENY_DIR_NAMES,
    DENY_EXTENSIONS,
    DENY_FILE_NAMES,
    EXTRA_ID,
    Expand,
    FORMAT_VERSION,
    GLOBAL_EXCLUDE,
    HOME_DENY_FILES,
    HOME_DENY_SUBFILES,
    INSTALL_ID,
    LOCK_NAMES,
    MANIFEST,
    MAX_ENTRIES,
    MAX_MANIFEST_BYTES,
    NOT_SELECTED,
    NOT_SELECTED_TEXT,
    PackageError,
    RESTORE_EXCLUDE,
    SPOOL_MAX,
    SSH_DENY_FILES,
    STAGE_PREFIX,
    SourceUnreadable,
    WILDCARD,
    ZIP64_THRESHOLD,
    _BAD_CHARS,
    _DEVICE_NAMES,
    _SUPERSCRIPT,
    _bad_component,
    _excluded,
    _safe_rel,
    _usable_base,
    _within,
    split_rule_path,
    unreadable_entry,
    unsafe_name,
)
from .package_collect import (  # noqa: F401
    CACHE_COUNT_SECONDS,
    Item,
    _BENIGN_SKIPS,
    _collect_one_path,
    _denied_skip,
    _path_coverage,
    _pruned_skip,
    _size_or_zero,
    _walk_files_no_links,
    collect_extra_items,
    collect_items,
    estimate_extra_config,
)
from .package_zip import (  # noqa: F401
    ALREADY_COMPRESSED_EXTS,
    COMPRESS_MODES,
    METHOD_NAMES,
    SAMPLE_BYTES,
    SAMPLE_MIN_SIZE,
    SAMPLE_RATIO,
    _PartZip,
    _looks_incompressible,
    _write_entry,
    _zi_dump,
    _zi_load,
    _zip_time,
    _zip_time_st,
    choose_method,
)
from .package_savepoint import (  # noqa: F401
    FAILED_SUFFIX,
    JOURNAL_SUFFIX,
    JOURNAL_VERSION,
    LOCK_SUFFIX,
    PART_SUFFIX,
    SAVEPOINT_BYTES,
    SAVEPOINT_SECONDS,
    _ENTRY_INT,
    _ENTRY_STR,
    _GRACEFUL_SAVEPOINT,
    _Journal,
    _OutputLock,
    _ResumeVerifyFailed,
    _Resumed,
    _ZI_KEYS,
    _cleanup_stale_stages,
    _compact,
    _entry_shape_ok,
    _fingerprint,
    _fp_reason,
    _headers_ok,
    _is_ours,
    _partial_files,
    _pid_alive,
    _prepare_part,
    _source_unchanged,
    _write_journal,
    discard_partial,
    inspect_savepoint,
    is_unfinished_part,
    part_paths,
    read_journal,
)
from .package_export import (  # noqa: F401
    SPACE_FACTOR_DEFLATE,
    _abandon,
    _check_export_space,
    _export_once,
    _keep_failed_part,
    export_package,
)
from .package_verify import (  # noqa: F401
    _file_entry_ok,
    _hash_entry,
    _manifest_shape_problems,
    verify_package,
)
from .package_deny import (  # noqa: F401
    _rule_names_extension,
    rel_matches_rule,
    restore_denied,
)
from .package_plan import (  # noqa: F401
    GIT_CONFIG_WARNING,
    INSTALL_KEY_PREFIX,
    ImportPlan,
    _denied_item,
    _fill_programs,
    _norm_tokens,
    _not_selected_item,
    _plan_import,
    _plan_import_inner,
    _rejected_item,
    git_config_targets,
    install_program_key,
    is_git_config_name,
    plan_import,
    program_key,
    program_matches,
)
from .package_apply import (  # noqa: F401
    _stage_entry,
    apply_import,
)
from .package_winget import (  # noqa: F401
    MAX_WINGET_BYTES,
    WINGET_ENTRY,
    _read_winget_raw,
    extract_winget,
    filter_winget_json,
    list_winget_packages,
)
from .package_retry import (  # noqa: F401
    RETRY_SUFFIX,
    _ExtraRef,
    _copy_raw_entry,
    _extra_refs,
    _norm_src,
    _retry_once,
    retry_failed_export,
    retry_paths,
)
