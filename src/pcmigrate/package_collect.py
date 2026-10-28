"""내보내기 대상 수집(규칙 경로/추가 설정 폴더 걷기).

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import coverage, settings_filter
from . import failure, fs
from .cancel import checkpoint
from .discover_data import REGENERABLE_REASON, ROOTS, is_regenerable
from .envpaths import expand_env
from .rules import PathRule, Rule
from .package_base import EXTRA_ID, Expand, WILDCARD, _excluded, _usable_base, split_rule_path, unsafe_name
from .package_deny import restore_denied


# --------------------------------------------------------------------------- export

@dataclass
class Item:
    arc: str
    src: Path
    rule_id: str
    base: str  # 미확장 base (예: %APPDATA%\Autodesk)
    rel: str   # base 기준 상대 경로(posix)


def _walk_files_no_links(top: Path, files: list[Path], skipped: list[dict], rule_id: str,
                         failed: list[dict] | None = None, pruned: list[Path] | None = None) -> None:
    """top 아래 파일을 모은다. 정션/심볼릭 링크 폴더(reparse point)는 들어가지 않고 건너뛴 기록을 남긴다.

    260자를 넘는 경로도 긴 경로 접두로 순회하고(반환 경로는 접두 없는 사용자 표기), 읽지 못한 폴더는 조용히 넘기지 않고
    failed 에 기록한다(R2-1: 빼먹고 '실패 0' 으로 보고하지 않는다).
    pruned 가 주어지면(내보내기 extra 전용) top 아래에서 이름이 '재생성되는 임시 저장물'(is_regenerable)과 정확히 일치하는
    하위 폴더는 들어가지 않고 pruned 에 모은다(top 자신은 사용자가 고른 것이므로 검사하지 않는다)."""
    top_s = str(top)
    lp = fs.long_path(top_s)

    def on_error(e: OSError) -> None:
        if failed is not None:
            f = failure.classify(e, str(e.filename or top_s))
            where = str(e.filename or top_s)
            if where.startswith("\\\\?\\"):
                where = where[4:]
            failed.append({"src": where, "error": f"폴더를 읽지 못함: {f.label}", "reason": f.code})

    for root, dirs, names in os.walk(lp, followlinks=False, onerror=on_error):
        checkpoint()  # 폴더마다 취소 확인(파일이 많은 폴더 탐색 중 중지)
        dirs.sort()
        plain = top_s + root[len(lp):]
        keep = []
        for d in dirs:
            dp = Path(plain) / d
            if fs.is_reparse(dp):
                skipped.append({"rule": rule_id, "path": str(dp), "reason": "심볼릭 링크/정션은 따라가지 않음"})
            elif pruned is not None and is_regenerable(Path(plain, d).relative_to(top).parts):
                pruned.append(dp)
            else:
                keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            files.append(Path(plain) / n)


def _denied_skip(rule_id: str, path: Path | str, why: str) -> dict:
    return {"rule": rule_id, "path": str(path), "denied": True,
            "reason": f"복원하지 않는 형식이라 패키지에 넣지 않음(보안): {why}"}


_BENIGN_SKIPS = ("제외 대상", "경로가 없음", "환경변수 확장 실패")


def _path_coverage(rule: Rule, pr: PathRule, items: list, skipped: list, failed: list | None, snap: tuple[int, int, int],
                   found: bool) -> dict:
    """경로 하나의 결과(설정 확인표용). snap = 이 경로를 처리하기 전의 (items, skipped, failed) 길이."""
    n_files = len(items) - snap[0]
    mine = skipped[snap[1]:]
    n_failed = (len(failed) - snap[2]) if failed is not None else 0
    bad = sum(1 for x in mine if not any(b in x.get("reason", "") for b in _BENIGN_SKIPS)
              and not x.get("cache") and not x.get("not_config"))
    return {"rule": rule.id, "name": rule.name, "path": pr.path, "description": pr.description, "files": n_files,
            "failed": n_failed, "excluded": bad,
            "status": coverage.classify(found=found, files=n_files, failed=n_failed, excluded=bad)}


def collect_items(rule: Rule, expand: Expand = expand_env, *, avoid: Callable[[Path], bool] | None = None,
                  roots: list[Path] | None = None, failed: list[dict] | None = None,
                  cov: list[dict] | None = None) -> tuple[list[Item], list[dict]]:
    """규칙에 따라 백업할 파일 목록과 건너뛴 항목(사유 포함)을 만든다. 읽기 전용.

    복원 때 거부될 파일(실행/스크립트 형식 등)은 내보낼 때도 같은 판정(restore_denied)으로 skipped 에 넣는다(denied=True).
    avoid(path)가 True 인 파일(출력 파일 자신/임시물 등)은 조용히 뺀다. roots 에는 찾은 최상위 경로를 모은다(출력이 그 안인지 검사용).
    cov 가 있으면 경로별 결과(설정 확인표용)를 덧붙인다.
    """
    items: list[Item] = []
    skipped: list[dict] = []
    for pr in rule.paths:
        snap = (len(items), len(skipped), len(failed) if failed is not None else 0)
        found = _collect_one_path(rule, pr, expand, avoid, roots, failed, items, skipped)
        if cov is not None:
            cov.append(_path_coverage(rule, pr, items, skipped, failed, snap, found))
    if cov is not None and not rule.paths:  # 자동으로 옮길 설정이 없는 규칙: 직접 해야 함
        cov.append({"rule": rule.id, "name": rule.name, "path": "", "description": coverage.NO_AUTO_SETTINGS,
                    "files": 0, "failed": 0, "excluded": 0, "status": coverage.ST_MANUAL})
    return items, skipped


def _collect_one_path(rule: Rule, pr: PathRule, expand: Expand, avoid, roots, failed, items: list[Item],
                      skipped: list[dict]) -> bool:
    """규칙 경로 하나를 처리해 items/skipped 에 덧붙인다. 경로가 실제로 있었으면 True."""
    split = split_rule_path(pr.path)
    if split is None:
        skipped.append({"rule": rule.id, "path": pr.path, "reason": "경로 형식이 올바르지 않음"})
        return False
    base, pattern = split
    base_path = Path(expand(base))
    if not _usable_base(base_path):
        skipped.append({"rule": rule.id, "path": pr.path, "reason": "환경변수 확장 실패 또는 상대 경로"})
        return False
    has_glob = bool(WILDCARD & set(pr.path))
    matches = sorted(base_path.glob(pattern)) if base_path.is_dir() else []
    if not matches:
        skipped.append({"rule": rule.id, "path": pr.path, "reason": "경로가 없음"})
        return False
    for m in matches:
        link = fs.reparse_in_chain(base_path, m)  # 일치한 경로 자신과 그 위쪽 폴더(base 아래)가 링크/정션인지
        if link:
            skipped.append({"rule": rule.id, "path": link, "reason": "심볼릭 링크/정션은 따라가지 않음"})
            continue
        if roots is not None:
            roots.append(m)
        files: list[Path] = []
        if m.is_file():
            files = [m]
        else:
            _walk_files_no_links(m, files, skipped, rule.id, failed)
        for f in files:
            if avoid is not None and avoid(f):
                continue
            if os.path.islink(fs.long_path(f)) or _excluded(f.name, rule):
                skipped.append({"rule": rule.id, "path": str(f),
                                "reason": "제외 대상(라이선스/임시/링크)"})
                continue
            rel = f.relative_to(base_path).as_posix()
            if unsafe_name(rel):
                skipped.append({"rule": rule.id, "path": str(f), "reason": "Windows 에서 쓸 수 없는 이름(예약 이름, 끝의 점/공백 등)"})
                continue
            why = restore_denied(rel, base_path / rel, rule, base)
            if why:
                skipped.append(_denied_skip(rule.id, f, why))
                continue
            if pr.kind == "file" and not has_glob:
                arc = f"data/{pr.dest}"
            else:
                arc = f"data/{pr.dest}/{rel}"
            items.append(Item(arc, f, rule.id, base, rel))
    return True


CACHE_COUNT_SECONDS = 8.0  # 제외한 임시 저장물 폴더 하나의 크기/파일 수를 세는 데 쓰는 최대 시간(넘으면 '이상'으로 표시)


def _pruned_skip(path: Path) -> dict:
    """제외한 임시 저장물 폴더의 이름·파일 수·용량을 센다(읽기 전용, 시간 상한). skipped 항목으로 돌려준다."""
    from .discover import scan_tree
    size, nfiles, _newest, _sec, complete = scan_tree(path, time.monotonic() + CACHE_COUNT_SECONDS, time.monotonic)
    return {"rule": EXTRA_ID, "path": str(path), "name": path.name, "reason": REGENERABLE_REASON, "cache": True,
            "files": nfiles, "bytes": size, "complete": bool(complete)}


def _size_or_zero(path: Path) -> int:
    try:
        return os.stat(fs.long_path(path)).st_size
    except OSError:
        return 0


def collect_extra_items(extras: list, expand: Expand = expand_env, *, avoid: Callable[[Path], bool] | None = None,
                        roots: list[Path] | None = None, failed: list[dict] | None = None,
                        include_caches: bool = False, cov: list[dict] | None = None,
                        extra_mode: str = settings_filter.DEFAULT_EXTRA_MODE,
                        limits: settings_filter.ConfigLimits | None = None
                        ) -> tuple[list[Item], list[dict]]:
    """discover 선택 파일로 고른 항목(ExtraItem)의 파일 목록. 읽기 전용, 라이선스/임시/링크 제외, 복원 거부 형식은 skipped(denied).

    include_caches=False(기본)면 폴더 안의 재생성되는 임시 저장물 폴더(Cache, GPUCache, node_modules 등 정확히 일치하는 이름)는
    건너뛰고 skipped(reason='재생성되는 임시 저장물', 이름·파일 수·용량 포함)에 기록한다.

    extra_mode='config'(기본, 허용 방식)면 settings_filter 의 설정 형식 파일만 담고(상한 포함) 나머지는 항목마다 요약 1~2개로 skipped 에
    기록한다(not_config / limit). 'all' 이면 예전 방식(폴더 전체)이다."""
    if extra_mode not in settings_filter.EXTRA_MODES:
        raise ValueError(f"extra_mode 는 {', '.join(settings_filter.EXTRA_MODES)} 중 하나여야 합니다: {extra_mode!r}")
    keys = {r.template: r.key for r in ROOTS}
    items: list[Item] = []
    skipped: list[dict] = []
    for ex in extras:
        snap = (len(items), len(skipped), len(failed) if failed is not None else 0)
        top = Path(ex.path)
        files: list[Path] = []
        pruned: list[Path] = []
        gate = settings_filter.ConfigGate(limits) if extra_mode == settings_filter.EXTRA_MODE_CONFIG else None
        if top.is_symlink() or (top.is_dir() and fs.is_reparse(top)):
            skipped.append({"rule": EXTRA_ID, "path": str(top), "reason": "심볼릭 링크/정션은 따라가지 않음"})
        else:
            if top.is_file():
                files = [top]
            else:
                _walk_files_no_links(top, files, skipped, EXTRA_ID, failed,
                                     None if (include_caches or getattr(ex, "include_caches", False)) else pruned)
            for pd in pruned:
                skipped.append(_pruned_skip(pd))
            if roots is not None:
                roots.append(top)
            for f in files:
                if avoid is not None and avoid(f):
                    continue
                if os.path.islink(fs.long_path(f)) or _excluded(f.name, None):
                    skipped.append({"rule": EXTRA_ID, "path": str(f), "reason": "제외 대상(라이선스/임시/링크)"})
                    continue
                rel = ex.rel if f == top else f"{ex.rel}/{f.relative_to(top).as_posix()}"
                if unsafe_name(rel):
                    skipped.append({"rule": EXTRA_ID, "path": str(f), "reason": "Windows 에서 쓸 수 없는 이름(예약 이름, 끝의 점/공백 등)"})
                    continue
                why = restore_denied(rel, Path(expand(ex.root)) / rel, None, ex.root)
                if why:
                    skipped.append(_denied_skip(EXTRA_ID, f, why))
                    continue
                if gate is not None and gate.judge(f.name, _size_or_zero(f), str(f), f.name if f == top else rel[len(ex.rel) + 1:]) is not None:
                    continue  # 허용 방식: 설정 형식이 아니거나 상한 초과(요약은 아래에서 한 번에 기록)
                items.append(Item(f"data/{EXTRA_ID}/{keys[ex.root]}/{rel}", f, EXTRA_ID, ex.root, rel))
            if gate is not None:
                skipped += gate.skipped_records(EXTRA_ID, str(top), top.name)
        if cov is not None:
            where = f"{ex.root}\\{ex.rel}"
            c = _path_coverage(Rule(EXTRA_ID, coverage.EXTRA_PROGRAM), PathRule(where, EXTRA_ID, "dir", where),
                               items, skipped, failed, snap, top.exists())
            c["name"] = f"{coverage.EXTRA_PROGRAM}: {ex.rel}"
            if getattr(ex, "self_transfer", None):
                c["self_transfer"] = dict(ex.self_transfer)  # 확인표에 '자체 이전 필요' 를 덧붙인다
            mine = [x for x in skipped[snap[1]:] if x.get("cache")]
            if mine:
                c.update(cache_dirs=len(mine), cache_files=sum(x["files"] for x in mine), cache_bytes=sum(x["bytes"] for x in mine))
            if gate is not None:
                c.update(gate.coverage_fields())
                if c["files"] == 0 and gate.nc_files and c["status"] == coverage.ST_EMPTY:
                    c["status"] = coverage.ST_EXCL  # 담을 설정 형식 파일이 하나도 없다(폴더 자체는 비어 있지 않음)
            cov.append(c)
    return items, skipped


def estimate_extra_config(root: str, rel: str, path: Path | str, expand: Expand = expand_env, *,
                          include_caches: bool = False, deadline: float | None = None,
                          clock: Callable[[], float] = time.monotonic,
                          limits: settings_filter.ConfigLimits | None = None) -> settings_filter.ConfigEstimate:
    """직접 고를 항목 하나(root 템플릿 + 최상위 rel)를 허용 방식으로 내보내면 담길 파일 수/용량을 미리 센다(읽기 전용).

    collect_extra_items(extra_mode='config')와 같은 기준(링크 안 따라감, 재생성 폴더 제외, 안전한 이름, 복원 거부 형식 제외,
    설정 형식 확장자, 파일/폴더 상한)을 같은 순서로 적용한다. deadline(clock 기준)을 넘으면 거기서 멈추고 complete=False 로 돌려준다(이상)."""
    top = Path(path)
    gate = settings_filter.ConfigGate(limits)
    est = settings_filter.ConfigEstimate()
    complete = True

    def judge(f: Path, rel_f: str) -> None:
        if unsafe_name(rel_f):
            return
        if restore_denied(rel_f, Path(expand(root)) / rel_f, None, root):
            return
        if os.path.islink(fs.long_path(f)) or _excluded(f.name, None):
            return
        gate.judge(f.name, _size_or_zero(f), str(f))

    try:
        if top.is_symlink() or (top.is_dir() and fs.is_reparse(top)):
            pass
        elif top.is_file():
            judge(top, rel)
        elif top.is_dir():
            lp = fs.long_path(str(top))
            tick = 0
            for cur, dirs, names in os.walk(lp, followlinks=False):
                plain = str(top) + cur[len(lp):]
                dirs.sort()
                keep = []
                for d in dirs:
                    dp = Path(plain) / d
                    if fs.is_reparse(dp):
                        continue
                    if not include_caches and is_regenerable(dp.relative_to(top).parts):
                        continue
                    keep.append(d)
                dirs[:] = keep
                for n in sorted(names):
                    tick += 1
                    if deadline is not None and tick % 100 == 0 and clock() > deadline:
                        complete = False
                        break
                    f = Path(plain) / n
                    judge(f, f"{rel}/{f.relative_to(top).as_posix()}")
                if not complete:
                    break
    except OSError:
        complete = False
    est.files, est.bytes, est.complete = gate.files, gate.bytes, complete
    est.excluded_files, est.excluded_bytes = gate.nc_files, gate.nc_bytes
    est.limit_files, est.limit_bytes = gate.lim_files, gate.lim_bytes
    est.ext_counts = dict(gate.ext_counts)
    return est


# --------------------------------------------------------------------------- 압축 방식
