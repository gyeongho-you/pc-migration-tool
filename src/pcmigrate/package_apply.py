"""가져오기 실행(apply_import).

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import hashlib
import shutil
import tempfile
import time
import zipfile
import zlib
from pathlib import Path
from typing import Callable

from . import failure, fs
from .cancel import checkpoint
from .copier import DENIED, FAILED, REJECTED, SKIPPED_SAME, FileResult, Report, copy_file
from .progress import ProgressInfo, ProgressTracker, categorize
from .package_base import CHUNK, NOT_SELECTED, NOT_SELECTED_TEXT, PackageError
from .package_plan import ImportPlan, git_config_targets


def apply_import(path: Path, plan: ImportPlan,
                 progress_info: Callable[[ProgressInfo], None] | None = None) -> Report:
    """검증을 통과한 계획을 실행한다. 덮어쓸 파일은 .bak 백업, 복사 후 해시 재검증.

    progress_info 가 있으면 파일 수/현재 파일/분류별 개수를 알린다(GUI 현황 카드용, CLI 출력과 무관).
    """
    if plan.problems:
        raise PackageError("검증 실패 상태에서는 복원할 수 없습니다")
    if not plan.install_allowed and any(it.get("install") for it in plan.items):  # 계획을 손으로 바꿔도 동의 없이는 설치 폴더에 쓰지 않는다
        raise PackageError("설치 폴더 설정은 사용자가 복원을 허용했을 때만 쓸 수 있습니다")
    report = Report(dry_run=False)
    if plan.git_config_warning:  # 복원은 허용하되 내용을 확인하라고 알린다(GUI 는 이 키를 읽는다)
        report.meta.update(git_config_warning=plan.git_config_warning, git_config_files=git_config_targets(plan.items))
    tracker = ProgressTracker("restore", sum(i["size"] for i in plan.items), len(plan.items))
    last = [0.0]

    def emit(force: bool = False) -> None:
        if progress_info is not None and (force or time.monotonic() - last[0] >= 0.5):
            last[0] = time.monotonic()
            progress_info(tracker.snapshot())

    def done(res: FileResult) -> None:
        report.results.append(res)
        tracker.add(res.size, 1)
        tracker.note(categorize(res.status, res.reason), res.dst or res.src)
        emit()

    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory() as tmp:
        for it in plan.items:
            checkpoint()  # 파일 사이에서만 멈춘다: 이미 바꾼 파일은 .bak 으로 되돌릴 수 있고, 지금 파일은 건드리지 않는다
            tracker.set_current(it["target"])
            if it["status"] == SKIPPED_SAME:
                done(FileResult(it["entry"], it["target"], SKIPPED_SAME, it["size"], it["sha256"]))
                continue
            target = Path(it["target"])
            if it["status"] == NOT_SELECTED:  # 선택에서 제외된 프로그램: 패키지에서 꺼내지도, 대상에 쓰지도 않는다
                done(FileResult(it["entry"], it["target"], NOT_SELECTED, it["size"], it["sha256"], error=NOT_SELECTED_TEXT,
                                reason=NOT_SELECTED))
                continue
            if it["status"] == DENIED:  # 거부 항목은 패키지에서 꺼내지도, 대상에 쓰지도 않는다
                done(FileResult(it["entry"], it["target"], DENIED, it["size"], it["sha256"], error=it["error"], reason=DENIED))
                continue
            link = it["error"] if it["status"] == REJECTED else ""
            if not link and it.get("base_dir"):  # 계획 이후에 링크가 생겼을 수도 있으니 쓰기 직전에 다시 확인한다
                found = fs.reparse_in_chain(it["base_dir"], target)
                link = f"{failure.REASONS[failure.LINK_IN_PATH].text} ({found})" if found else ""
            if link:
                done(FileResult(it["entry"], it["target"], REJECTED, it["size"], it["sha256"], error=link,
                                reason=failure.LINK_IN_PATH))
                continue
            if it.get("install"):  # 설치 폴더: 쓰기 직전 마지막 확인(허용 확장자/대상 폴더 존재). 새 폴더는 만들지 않는다
                from . import install_dir
                why = install_dir.recheck_before_write(it)
                if why:
                    done(FileResult(it["entry"], it["target"], FAILED, it["size"], it["sha256"], error=why, reason=failure.OTHER))
                    continue
            staged = Path(tmp) / "entry"
            try:
                bad = _stage_entry(zf, it, staged, tmp)
                if bad is not None:  # 해시/크기가 다르면 대상은 건드리지 않는다(B-4)
                    res = bad
                else:
                    res = copy_file(staged, target)
                    if res.status != FAILED and res.sha256 != it["sha256"]:
                        res.status, res.error = FAILED, "manifest 해시와 불일치"
                    res.src = it["entry"]
            except OSError as e:
                res = FileResult(it["entry"], it["target"], FAILED, error=f"{type(e).__name__}: {e}",
                                 reason=failure.classify(e, it["target"]).code)
            except (KeyError, zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError) as e:
                res = FileResult(it["entry"], it["target"], FAILED, error=f"패키지에서 읽을 수 없음({type(e).__name__}): manifest 해시와 불일치할 수 있음",
                                 reason=failure.OTHER)
            done(res)
            staged.unlink(missing_ok=True)
    emit(True)
    return report


def _stage_entry(zf: zipfile.ZipFile, it: dict, staged: Path, tmp: str) -> FileResult | None:
    """zip 엔트리를 임시 파일로 풀면서 크기/해시를 계산한다. 선언과 다르면 실패 결과를, 맞으면 None 을 돌려준다.

    선언 크기보다 많이 나오면 거기서 멈춘다. 대상 파일을 바꾸기 전에 확인하므로 검사~적용 사이에 zip 이 바뀌어도 잘못된 파일이 설치되지 않는다.
    """
    size = it["size"]
    free = shutil.disk_usage(tmp).free
    if free < size:
        f = failure.REASONS[failure.DISK_FULL]
        return FileResult(it["entry"], it["target"], FAILED, error=f"{f.text} (임시 폴더 여유 {free}바이트 < 필요 {size}바이트)", reason=f.code)
    h = hashlib.sha256()
    n = 0
    with zf.open(it["entry"]) as fin, open(staged, "wb") as fout:
        while chunk := fin.read(CHUNK):
            checkpoint()
            n += len(chunk)
            if n > size:
                break
            h.update(chunk)
            fout.write(chunk)
    if n != size or h.hexdigest() != it["sha256"]:
        staged.unlink(missing_ok=True)
        return FileResult(it["entry"], it["target"], FAILED, error="manifest 해시와 불일치(패키지가 검사 뒤에 바뀌었을 수 있음, 대상은 바꾸지 않음)",
                          reason=failure.MISMATCH)
    return None


# --------------------------------------------------------------------------- winget-extract
