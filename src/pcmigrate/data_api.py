"""'내 파일 찾기'(find-data)와 '선택 파일 전송'(transfer --from-selection)의 CLI/GUI 공용 진입점.

api.py 와 같은 규칙: 출력(print)을 하지 않고 결과 객체를 돌려주며, 외부 호출은 Deps 로 주입한다.
(api.py 를 직접 늘리지 않고 새 모듈로 둔 이유: 다른 작업과의 병합 충돌을 줄이기 위해서다. GUI 는 이 모듈을 가져다 쓴다.)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable

from . import find_data as fd, find_exts
from .api import ApiError, Deps, check_outputs_outside, report_inside, run_transfer
from .cancel import Cancelled  # noqa: F401  (GUI 가 가져다 쓴다)
from .copier import Report  # noqa: F401
from .multi_transfer import MultiReport, plan_items, run_multi
from .progress import ProgressInfo


def run_find_data(roots: Iterable[str | os.PathLike] | None = None, *, categories: Iterable[str] = fd.DEFAULT_CATEGORIES,
                  min_size: int = 0, max_depth: int = 4, time_limit: float | None = None,
                  include_removable: bool = False, user_folders: dict[str, str] | None = None,
                  exclude_paths: Iterable[str] | None = None,
                  cancel: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
                  progress_info: Callable[[ProgressInfo], None] | None = None,
                  output_selection: str | Path | None = None, use_user_exts: bool = True,
                  ext_config: find_exts.ExtConfig | None = None, **inject) -> fd.FindResult:
    """내 파일 자동 찾기. 읽기 전용. output_selection 을 주면 선택 파일(전부 include:false)을 쓴다.

    use_user_exts: 사용자가 설정한 '찾을 파일 형식'(find_exts)을 쓴다(False 면 기본 표). 시작할 때 한 번만 읽어 표를 만든다.
    설정이 손상됐으면 기본값으로 계속하고 경고를 결과의 ext_notes 에 싣는다. ext_config: 읽어 둔 설정을 직접 넘김(화면/시험용).

    inject: find_data 의 주입 인자(clock, attr_getter, drives_fn 등, 테스트용).
    """
    root_list = None if roots is None else [os.fspath(r) for r in roots]
    if output_selection:
        for r in root_list or []:
            if os.path.dirname(os.path.abspath(r)) != os.path.abspath(r) and report_inside(output_selection, r):
                raise ApiError(f"선택 파일 경로가 훑을 폴더 안입니다: {output_selection}",
                               lines=["원본 폴더에는 아무것도 쓰지 않습니다. 훑는 폴더 밖(예: 문서 폴더나 외장 드라이브)에 저장하세요."])
    if use_user_exts and "ext_table" not in inject:
        cfg = ext_config if ext_config is not None else find_exts.load()
        notes = list(cfg.notes)
        if not cfg.is_default:
            notes.insert(0, f"찾을 파일 형식 설정을 적용했습니다(추가 {len(cfg.added)}개, 삭제 {len(cfg.removed)}개).")
        inject.update(ext_table=find_exts.effective_table(cfg), user_exts=find_exts.user_exts(cfg), ext_notes=notes)
    try:
        res = fd.find_data(root_list, categories=categories, min_size=min_size, max_depth=max_depth, time_limit=time_limit,
                           include_removable=include_removable, user_folders=user_folders, exclude_paths=exclude_paths,
                           cancel=cancel, progress=progress, progress_info=progress_info, **inject)
    except ValueError as e:
        raise ApiError(str(e)) from e
    if output_selection:
        fd.write_selection(res, output_selection)
    return res


def load_selected(selection: str | Path | Iterable[fd.SelectedItem | str]) -> list[fd.SelectedItem | str]:
    """선택 파일 경로 또는 항목 목록을 항목 목록으로. 형식 오류는 ApiError."""
    if isinstance(selection, (str, Path)):
        try:
            return list(fd.load_selection(selection))
        except fd.SelectionFileError as e:
            raise ApiError(str(e)) from e
    return list(selection)


def run_transfer_selection(selection, dst: str | Path, *, dry_run: bool = False, include_cloud: bool = False,
                           strict: bool = False, warn: Callable[[str], None] = lambda m: None, deps: Deps | None = None,
                           progress: Callable[[str], None] | None = None,
                           progress_info: Callable[[ProgressInfo], None] | None = None,
                           on_item: Callable[[int, int, str, str, Report | None], None] | None = None,
                           cancel: Callable[[], bool] | None = None, verify_only: bool = False, retry_failed: bool = False,
                           workers: int | None = None, chunk: int | None = None, keep_awake: bool = True,
                           manifest_out: str | Path | None = None, verify_dest: bool | None = None,
                           copy_mode: str = "restartable", copy_threads: int | None = None, split_large: bool = False,
                           exclude_junk: bool = False, junk_backup_copies: bool = False, skip_done: bool = False,
                           dest_info_fn=None, estimate_fn=None, extra_files: Iterable[str | os.PathLike] | None = None,
                           abort_report: str | Path | None = None, drop_kwargs: dict | None = None) -> MultiReport:
    """선택 파일(또는 항목 목록)의 여러 원본 폴더를 dst 아래에 <드라이브>\\<경로> 구조로 전송한다.

    include 가 true 인 항목만 옮긴다. 옵션은 run_transfer 와 같다(항목마다 적용). 항목 하나의 실패는 나머지를 막지 않는다.
    TransferError(대상이 원본 안, 용량 부족, 선택 없음 등)는 그대로 전파한다. 취소하면 Cancelled.
    on_item(번호, 전체, 원본, 상태, 리포트): GUI 가 항목별 진행 표시에 쓸 수 있다.
    extra_files: 직접 고른 파일/폴더(drop_stage). 없으면(기본) 동작이 이전과 완전히 같다. 있으면 전송 시작 때 겹침을 빼고 임시 폴더에 담아
    폴더들과 함께 보낸 뒤 임시 폴더를 지운다. 중단/예외 때도 abort_report(있으면)에 리포트를 저장한다. drop_kwargs: 시험용 주입.
    """
    deps = deps or Deps()
    items = load_selected(selection)
    for it in items:  # 해시 목록은 원본 폴더 밖에 쓴다(원본은 읽기만)
        check_outputs_outside(it.path if isinstance(it, fd.SelectedItem) else it, manifest_out=manifest_out, report=abort_report)
    opts = dict(include_cloud=include_cloud, strict=strict, workers=workers, chunk=chunk, keep_awake=keep_awake,
                verify_dest=verify_dest, copy_mode=copy_mode, copy_threads=copy_threads, split_large=split_large,
                exclude_junk=exclude_junk, junk_backup_copies=junk_backup_copies)

    def one(src: Path, d: Path, **kw) -> Report:
        o = opts
        if opts["exclude_junk"]:
            from .drop_stage import under_stage
            if under_stage(src):  # 직접 고른 파일(임시 보관)은 정크 규칙으로 빼지 않는다(사용자가 고른 파일, 겹침 판정과 같은 기준)
                o = {**opts, "exclude_junk": False, "junk_backup_copies": False}
        return run_transfer(src, d, deps=deps, **o, **kw)

    def go(its: list) -> MultiReport:
        return run_multi(its, dst, one, dry_run=dry_run, manifest_out=manifest_out, skip_done=skip_done,
                         retry_failed=retry_failed, verify_only=verify_only, progress=progress, progress_info=progress_info,
                         on_item=on_item, cancel=cancel, warn=warn, dest_info_fn=dest_info_fn, estimate_fn=estimate_fn)

    for x in extra_files or []:  # 직접 고른 항목 안에도 해시 목록/리포트를 쓰지 않는다(원본은 읽기만)
        check_outputs_outside(x.path if hasattr(x, "path") else os.fspath(x), manifest_out=manifest_out, report=abort_report)
    if not extra_files:
        if not abort_report:
            return go(items)
        from . import drop_stage
        try:
            return go(items)
        except BaseException as e:  # noqa: BLE001 - 보낼 것이 없거나 중단돼도 결과 리포트는 저장한다
            drop_stage._write_abort(abort_report, {}, e)
            raise
    from . import drop_stage
    return drop_stage.run_with_extras(items, [os.fspath(x) if not isinstance(x, drop_stage.DropEntry) else x for x in extra_files],
                                      dst, go, dry_run=dry_run, cancel=cancel, warn=warn, progress=progress,
                                      abort_report=abort_report, **{**_junk_kw(exclude_junk, junk_backup_copies), **(drop_kwargs or {})})


def _junk_kw(exclude_junk: bool, backup_copies: bool) -> dict:
    if not exclude_junk:
        return {}
    from .junk import make_spec
    return {"junk_spec": make_spec(include_backup_copies=backup_copies)}


def preview_plan(selection, dst: str | Path):
    """실행 없이 선택 정리 결과만(어느 폴더가 어디로, 무엇이 중복으로 빠지는지). 화면의 '선택 확인' 단계용."""
    return plan_items(load_selected(selection), dst)
