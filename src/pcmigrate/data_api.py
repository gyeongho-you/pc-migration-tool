"""'내 파일 찾기'(find-data)와 '선택 파일 전송'(transfer --from-selection)의 CLI/GUI 공용 진입점.

api.py 와 같은 규칙: 출력(print)을 하지 않고 결과 객체를 돌려주며, 외부 호출은 Deps 로 주입한다.
(api.py 를 직접 늘리지 않고 새 모듈로 둔 이유: 다른 작업과의 병합 충돌을 줄이기 위해서다. GUI 는 이 모듈을 가져다 쓴다.)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable

from . import find_data as fd
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
                  output_selection: str | Path | None = None, **inject) -> fd.FindResult:
    """내 파일 자동 찾기. 읽기 전용. output_selection 을 주면 선택 파일(전부 include:false)을 쓴다.

    inject: find_data 의 주입 인자(clock, attr_getter, drives_fn 등, 테스트용).
    """
    root_list = None if roots is None else [os.fspath(r) for r in roots]
    if output_selection:
        for r in root_list or []:
            if os.path.dirname(os.path.abspath(r)) != os.path.abspath(r) and report_inside(output_selection, r):
                raise ApiError(f"선택 파일 경로가 훑을 폴더 안입니다: {output_selection}",
                               lines=["원본 폴더에는 아무것도 쓰지 않습니다. 훑는 폴더 밖(예: 문서 폴더나 외장 드라이브)에 저장하세요."])
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
                           dest_info_fn=None, estimate_fn=None) -> MultiReport:
    """선택 파일(또는 항목 목록)의 여러 원본 폴더를 dst 아래에 <드라이브>\\<경로> 구조로 전송한다.

    include 가 true 인 항목만 옮긴다. 옵션은 run_transfer 와 같다(항목마다 적용). 항목 하나의 실패는 나머지를 막지 않는다.
    TransferError(대상이 원본 안, 용량 부족, 선택 없음 등)는 그대로 전파한다. 취소하면 Cancelled.
    on_item(번호, 전체, 원본, 상태, 리포트): GUI 가 항목별 진행 표시에 쓸 수 있다.
    """
    deps = deps or Deps()
    items = load_selected(selection)
    for it in items:  # 해시 목록은 원본 폴더 밖에 쓴다(원본은 읽기만)
        check_outputs_outside(it.path if isinstance(it, fd.SelectedItem) else it, manifest_out=manifest_out)
    opts = dict(include_cloud=include_cloud, strict=strict, workers=workers, chunk=chunk, keep_awake=keep_awake,
                verify_dest=verify_dest, copy_mode=copy_mode, copy_threads=copy_threads, split_large=split_large,
                exclude_junk=exclude_junk, junk_backup_copies=junk_backup_copies)

    def one(src: Path, d: Path, **kw) -> Report:
        return run_transfer(src, d, deps=deps, **opts, **kw)

    return run_multi(items, dst, one, dry_run=dry_run, manifest_out=manifest_out, skip_done=skip_done,
                     retry_failed=retry_failed, verify_only=verify_only, progress=progress, progress_info=progress_info,
                     on_item=on_item, cancel=cancel, warn=warn, dest_info_fn=dest_info_fn, estimate_fn=estimate_fn)


def preview_plan(selection, dst: str | Path):
    """실행 없이 선택 정리 결과만(어느 폴더가 어디로, 무엇이 중복으로 빠지는지). 화면의 '선택 확인' 단계용."""
    return plan_items(load_selected(selection), dst)
