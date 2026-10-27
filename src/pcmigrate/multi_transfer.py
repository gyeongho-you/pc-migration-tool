r"""다중 원본 전송: 선택 파일(find-data 가 만든 JSON)의 여러 원본 폴더를 대상 아래에 드라이브/폴더 구조를 보존해 복사한다.

대상 폴더 구조: <대상>\<드라이브 문자>\<원본의 나머지 경로>   예) D:\프로젝트\A  ->  <대상>\D\프로젝트\A
  (드라이브 문자는 'D:' 대신 폴더 이름 'D' 로, 네트워크 \\서버\공유 는 'UNC\서버\공유' 로 바꾼다.)

항목(원본 폴더) 하나는 기존 transfer 를 그대로 한 번 호출한다. 그래서 파일 단위 이어받기/해시 검사/.bak 보존/--strict/--retry-failed 가
항목마다 그대로 적용되고, 상태 파일(.pcmigrate-state.json)도 항목의 대상 폴더 안에 따로 생긴다.
- 선택 정리: 같은 폴더를 두 번 고르거나 부모와 자식을 함께 고르면 자식을 빼서 중복 복사를 막는다(빼낸 내역은 리포트에 남김).
- 대상이 어느 원본 안(또는 같음)이면 아무것도 시작하지 않고 거부한다.
- 용량 사전 점검: 선택 합계(선택 파일의 total_bytes, 없으면 조사) - 대상에 이미 있는 양 + 5% 여유 vs 대상의 여유 공간.
- 항목이 실패해도 나머지는 계속한다(항목별 실패 집계). 취소/드라이브 분리는 기록을 남기고 바로 멈춘다(다시 실행하면 이어받음).
- 대상 폴더의 .pcmigrate-multi.json 에 항목별 진행(pending/done/failed)을 기록한다. skip_done 이면 끝난 항목은 다시 하지 않는다.
- manifest_out 을 주면 항목별 해시 목록을 합쳐 목록 하나(상대 경로 앞에 항목 폴더가 붙음)로 만든다: verify --target <대상> 으로 한 번에 비교.
"""
from __future__ import annotations

import dataclasses
import json
import ntpath
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import fs
from .cancel import Cancelled
from .copier import FAIL_STATUSES, Report
from .find_data import SelectedItem
from .manifest import ManifestReader, ManifestWriter
from .progress import ProgressInfo, fmt_bytes
from .scan import scan_source_info
from .transfer import DriveDisconnected, SPACE_MARGIN, TransferError, _is_within

JOURNAL_NAME = ".pcmigrate-multi.json"
JOURNAL_VERSION = 1
MAX_ITEM_FAILURES_LISTED = 50
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def dest_rel(src: str) -> str:
    """원본 폴더 경로 -> 대상 아래 상대 경로(역슬래시). 드라이브 문자 'D:' 는 'D', UNC 는 'UNC\\서버\\공유'."""
    full = ntpath.abspath(src)
    if full.startswith("\\\\?\\"):
        full = full[4:]
    drive, rest = ntpath.splitdrive(full)
    parts = [p for p in rest.split("\\") if p]
    if drive.startswith("\\\\"):
        head = ["UNC"] + [_BAD.sub("_", p) for p in drive.strip("\\").split("\\") if p]
    else:
        head = [_BAD.sub("_", drive.rstrip(":")) or "_"]
    return "\\".join(head + parts)


def check_dest_rel(name: str) -> str:
    """항목별 대상 폴더 이름(dest_rel) 검증: 단일 폴더 이름만(구분자/../절대경로/ADS/예약 이름/끝 점·공백/도구 기록 이름 거부). 위반이면 ValueError."""
    from .lan_protocol import UnsafePath, validate_rel
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError("대상 폴더 이름이 비었거나 앞뒤에 공백이 있습니다")
    if "/" in name or "\\" in name or ":" in name or name in (".", ".."):
        raise ValueError(f"대상 폴더 이름은 폴더 이름 하나여야 합니다(경로 구분자, .., 드라이브/ADS 표시 불가): {name!r}")
    try:
        validate_rel(name)
    except UnsafePath as e:
        raise ValueError(str(e)) from e
    return name


@dataclass
class PlanItem:
    src: str
    rel: str  # 대상 아래 상대 경로(역슬래시)
    estimate: int | None = None  # 선택 파일에 적힌 용량(없으면 None)
    custom: bool = False  # dest_rel 로 지정한 이름이면 True(LAN 전송에서 레이아웃과 무관하게 이 이름을 쓴다)


@dataclass
class Plan:
    items: list[PlanItem] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)  # {"path","reason","covered_by"}


def _key(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/") or os.path.normcase(p)


def plan_items(selected: Iterable[SelectedItem | str], dst: str | os.PathLike, *, only_included: bool = True) -> Plan:
    """선택 항목을 정리한다: include 가 true 인 것만, 중복/겹침 제거, 원본 존재 확인, 대상이 원본 안이면 거부(TransferError)."""
    plan = Plan()
    rows: list[SelectedItem] = []
    for s in selected:
        it = s if isinstance(s, SelectedItem) else SelectedItem(str(s), True)
        if only_included and not it.include:
            continue
        rows.append(it)
    if not rows:
        raise TransferError("옮길 폴더가 선택되지 않았습니다(선택 파일에서 옮길 항목의 include 를 true 로 바꾸세요)")
    seen: dict[str, str] = {}
    kept: list[SelectedItem] = []
    for it in sorted(rows, key=lambda r: len(_key(r.path))):  # 짧은(상위) 경로부터
        k = _key(it.path)
        if k in seen:
            plan.dropped.append({"path": it.path, "reason": "같은 폴더가 중복 선택됨", "covered_by": seen[k]})
            continue
        parent = next((kp for kp in seen if k.startswith(kp + os.sep) or kp.endswith(os.sep) and k.startswith(kp)), None)
        if parent is not None:
            plan.dropped.append({"path": it.path, "reason": "선택한 상위 폴더에 이미 포함됨(중복 복사 방지)", "covered_by": seen[parent]})
            continue
        seen[k] = it.path
        kept.append(it)
    missing = [it.path for it in kept if not os.path.isdir(it.path)]
    if missing:
        raise TransferError("원본 폴더가 없습니다: " + ", ".join(missing[:5]) + (f" 외 {len(missing) - 5}개" if len(missing) > 5 else ""))
    d_real = os.path.realpath(dst)
    for it in kept:
        s_real = os.path.realpath(it.path)
        if os.path.normcase(s_real) == os.path.normcase(d_real) or _is_within(Path(d_real), Path(s_real)):
            raise TransferError(f"대상은 원본과 같거나 원본 폴더 안에 있을 수 없습니다: 원본 {it.path} / 대상 {dst}")
    rels: dict[str, str] = {}
    custom_keys: set[str] = set()
    for it in kept:
        custom = bool(getattr(it, "dest_rel", ""))
        if custom:
            try:
                rel = check_dest_rel(it.dest_rel)
            except ValueError as e:
                raise TransferError(f"항목의 대상 폴더 이름이 올바르지 않습니다({it.path}): {e}") from e
            custom_keys.add(rel.lower())
        else:
            rel = dest_rel(it.path)
        if rel.lower() in rels:  # 이름 변환이 겹치는 경우(실제로는 거의 없음): 조용히 합치지 않고 거부
            raise TransferError(f"대상 폴더 이름이 겹칩니다: {it.path} 와 {rels[rel.lower()]}")
        rels[rel.lower()] = it.path
        plan.items.append(PlanItem(it.path, rel, it.total_bytes, custom))
    if custom_keys:  # 지정한 이름이 다른 항목의 <드라이브>\... 구조 안쪽/바깥쪽과 겹치는 경우도 거부
        for ck in custom_keys:
            for other in rels:
                if other != ck and (other.startswith(ck + "\\") or ck.startswith(other + "\\")):
                    raise TransferError(f"대상 폴더 이름이 겹칩니다: {rels[ck]} 와 {rels[other]}")
    return plan


# --------------------------------------------------------------------------- 결과
@dataclass
class ItemResult:
    src: str
    dest: str
    status: str  # ok | failed | error | skipped_done | preview
    report: Report | None = None
    error: str = ""

    def to_dict(self) -> dict:
        d = {"src": fs.plain_path_text(self.src), "dest": fs.plain_path_text(self.dest), "status": self.status, "error": self.error}
        if self.report is not None:
            r = self.report.to_dict()
            d.update(summary=r["summary"], failed_count=r["failed_count"], total_files=r.get("total_files"),
                     total_bytes=r.get("total_bytes"), cloud_only_count=r.get("cloud_only_count", 0),
                     verification_complete=r.get("verification_complete"), dest_verification=r.get("dest_verification"),
                     robocopy_exit=r.get("robocopy_exit"), junk_excluded=r.get("junk_excluded"),
                     skipped_links=r.get("skipped_links", 0), preserved_existing_count=r.get("preserved_existing_count", 0),
                     failures=[x for x in r["results"] if x["status"] in FAIL_STATUSES][:MAX_ITEM_FAILURES_LISTED])
        return d


@dataclass
class MultiReport:
    dry_run: bool
    items: list[ItemResult] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def failures(self) -> list:
        """(항목, 실패 FileResult) 쌍과, 항목 자체가 실패한 경우 FileResult 비슷한 객체 없이 항목 오류는 item_errors 로 센다."""
        out = []
        for it in self.items:
            if it.report is not None:
                out += [(it, f) for f in it.report.failures]
        return out

    @property
    def item_errors(self) -> list[ItemResult]:
        return [it for it in self.items if it.status == "error"]

    @property
    def failed_total(self) -> int:
        return len(self.failures) + len(self.item_errors)

    def to_dict(self) -> dict:
        counts: dict[str, int] = {}
        files = size = cloud = junk_f = junk_b = 0
        complete = True
        for it in self.items:
            if it.report is None:
                complete = False
                continue
            r = it.report.to_dict()
            for k, v in r["summary"].items():
                counts[k] = counts.get(k, 0) + v
            files += r.get("total_files") or 0
            size += r.get("total_bytes") or 0
            cloud += r.get("cloud_only_count", 0)
            complete = complete and bool(r.get("verification_complete"))
            j = r.get("junk_excluded") or {}
            junk_f += j.get("files", 0)
            junk_b += j.get("bytes", 0)
        d = {"dry_run": self.dry_run, "mode": "multi", "summary": counts, "total_items": len(self.items),
             "items_ok": sum(1 for i in self.items if i.status in ("ok", "skipped_done", "preview")),
             "items_failed": sum(1 for i in self.items if i.status in ("failed", "error")),
             "failed_count": self.failed_total, "total_files": files, "total_bytes": size, "cloud_only_count": cloud,
             "verification_complete": complete and not self.dry_run, **self.meta,
             "dropped": self.dropped, "items": [i.to_dict() for i in self.items]}
        if junk_f or any((i.report and i.report.meta.get("junk_excluded") is not None) for i in self.items):
            d["junk_excluded"] = {"files": junk_f, "bytes": junk_b}
        d["success"] = (not self.failed_total and bool(self.items)) and (self.dry_run or d["verification_complete"])
        return d

    def write(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def summary_lines(rep: MultiReport, dst: str) -> list[str]:
    d = rep.to_dict()
    c = d["summary"]
    dstp = fs.plain_path_text(str(dst))
    if rep.dry_run:
        lines = [f"미리보기입니다(아무것도 복사하지 않았습니다): 폴더 {d['total_items']}개 → {dstp}",
                 f"- 전체 {d['total_files']:,}개 / {fmt_bytes(d['total_bytes'])}: 새로 복사 {c.get('would_copy', 0)}개, "
                 f"덮어쓰기 전 .bak 보존 {c.get('would_overwrite', 0)}개, 이미 같음 {c.get('skipped_identical', 0)}개"]
    else:
        lines = [f"전송을 마쳤습니다: 폴더 {d['total_items']}개 → {dstp}",
                 f"- 파일 {d['total_files']:,}개 / {fmt_bytes(d['total_bytes'])}: 정확함 {c.get('verified', 0)}개"
                 + (f", 검사 보류 {c.get('pending_verify', 0)}개" if c.get("pending_verify") else "")
                 + f", 실패 {d['failed_count']}개"
                 + (f", 클라우드 전용 건너뜀 {c.get('cloud_only', 0)}개" if c.get("cloud_only") else "")]
    lines.append(f"- 폴더별: 성공 {d['items_ok']}개, 실패 {d['items_failed']}개")
    for it in rep.items:
        mark = {"ok": "OK ", "preview": "미리보기", "skipped_done": "건너뜀(이미 끝남)"}.get(it.status, "실패")
        extra = ""
        if it.report is not None:
            r = it.report.to_dict()
            extra = f" {r.get('total_files', 0):,}개 / {fmt_bytes(r.get('total_bytes') or 0)}" + (
                f", 실패 {r['failed_count']}개" if r["failed_count"] else "")
        lines.append(f"  [{mark}] {fs.plain_path_text(it.src)} → {fs.plain_path_text(it.dest)}{extra}" + (f"  ({it.error})" if it.error else ""))
    if rep.dropped:
        lines.append(f"- 선택에서 뺀 폴더 {len(rep.dropped)}개(중복/부모 폴더에 포함):")
        lines += [f"    · {x['path']} — {x['reason']}" for x in rep.dropped[:10]]
    from .junk import summary_line
    lines += summary_line({"junk_excluded": {"files": d["junk_excluded"]["files"], "bytes": d["junk_excluded"]["bytes"],
                                              "by_group": _sum_groups(rep)}} if d.get("junk_excluded") else {})
    if rep.meta.get("required_bytes") is not None:
        lines.append(f"- 용량 점검: 필요 약 {fmt_bytes(rep.meta['required_bytes'])}, 대상 여유 "
                     f"{fmt_bytes(rep.meta['dest_free_bytes']) if rep.meta.get('dest_free_bytes') is not None else '알 수 없음'}")
    if rep.dry_run:
        lines.append("다음 할 일: 이상 없으면 같은 명령에서 --dry-run 만 빼고 실행하세요.")
    elif d["failed_count"] or d["items_failed"]:
        lines.append("다음 할 일: 원인을 해결한 뒤 같은 명령에 --retry-failed 를 붙여 실패한 파일만 다시 처리하세요(끝난 폴더는 빠르게 지나갑니다).")
    elif d["verification_complete"]:
        lines.append("다음 할 일: 검사까지 통과했습니다. 원본 정리는 충분히 확인한 뒤 판단하세요.")
    else:
        lines.append("다음 할 일: 복사만 끝났습니다. 새 PC 에서 pcmigrate verify 로 검사해야 정확한지 알 수 있습니다(그 전에는 원본을 지우지 마세요).")
    return lines


def _sum_groups(rep: MultiReport) -> dict:
    out: dict = {}
    for it in rep.items:
        j = (it.report.meta.get("junk_excluded") if it.report is not None else None) or {}
        for g, v in j.get("by_group", {}).items():
            o = out.setdefault(g, {"files": 0, "bytes": 0})
            o["files"] += v["files"]
            o["bytes"] += v["bytes"]
    return out


# --------------------------------------------------------------------------- 기록(항목별 진행)
def _journal_path(dst: Path) -> Path:
    return dst / JOURNAL_NAME


def _read_journal(dst: Path) -> dict:
    try:
        data = json.loads(_journal_path(dst).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == JOURNAL_VERSION and isinstance(data.get("items"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": JOURNAL_VERSION, "items": {}}


def _write_journal(dst: Path, journal: dict) -> None:
    p = _journal_path(dst)
    tmp = p.with_name(p.name + ".part")
    try:
        tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass  # 기록을 못 써도 전송 자체는 계속한다(항목 단위 건너뛰기만 못 함)


def _existing_bytes(folder: Path, cancel) -> int:
    total = 0
    for root, _d, files in os.walk(folder):
        if cancel is not None and cancel():
            raise Cancelled()
        for n in files:
            if n.startswith(".pcmigrate-"):
                continue
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def precheck_space(plan: Plan, dst: Path, *, dest_info_fn=None, cancel=None, estimate_fn=None) -> dict:
    """선택 합계 vs 대상 여유. 반환: {"estimate","existing","required","dest_free_bytes","blocked","fs"}."""
    dest = (dest_info_fn or fs.describe_destination)(str(dst))
    if dest.kind == "cdrom":
        raise TransferError("대상이 CD/DVD 드라이브입니다. 쓸 수 있는 하드디스크나 외장 드라이브를 고르세요.")
    est = existing = 0
    for it in plan.items:
        if it.estimate is None:
            it.estimate = (estimate_fn or _scan_bytes)(it.src, cancel)
        est += it.estimate
        existing += min(it.estimate, _existing_bytes(dst / it.rel, cancel))
    required = int(max(0, est - existing) * (1 + SPACE_MARGIN))
    blocked = dest.free is not None and dest.free < required
    return {"estimate_bytes": est, "existing_bytes": existing, "required_bytes": required, "dest_free_bytes": dest.free,
            "space_blocked": blocked, "dest_fs": dest.fs, "dest_kind": dest.kind}


def _scan_bytes(src: str, cancel) -> int:
    return scan_source_info(src, cancel=cancel).total_bytes


# --------------------------------------------------------------------------- 실행
TransferFn = Callable[..., Report]


def run_multi(selected: Iterable[SelectedItem | str], dst: str | os.PathLike, transfer_fn: TransferFn, *,
              dry_run: bool = False, item_kwargs: dict | None = None, manifest_out: str | os.PathLike | None = None,
              skip_done: bool = False, retry_failed: bool = False, verify_only: bool = False,
              progress: Callable[[str], None] | None = None,
              progress_info: Callable[[ProgressInfo], None] | None = None,
              on_item: Callable[[int, int, str, str, Report | None], None] | None = None,
              cancel: Callable[[], bool] | None = None, warn: Callable[[str], None] = lambda m: None,
              dest_info_fn=None, estimate_fn=None) -> MultiReport:
    """여러 원본 폴더를 dst 아래로 전송한다.

    transfer_fn(src: Path, dst: Path, **item_kwargs_and_per_item) -> Report. 항목별로 progress/progress_info/cancel/warn/manifest_out/
    dry_run/retry_failed/verify_only 를 덧붙여 부른다(그 외 옵션은 item_kwargs 로 그대로 전달).
    on_item(번호(1부터), 전체, 원본, 상태, 리포트): 항목 시작(status="start")과 끝(ok/failed/error/skipped_done/preview)마다.
    """
    dst = Path(dst)
    plan = plan_items(selected, dst)
    mr = MultiReport(dry_run=dry_run, dropped=plan.dropped)
    for d in plan.dropped:
        warn(f"선택에서 뺐습니다: {d['path']} ({d['reason']})")
    space = precheck_space(plan, dst, dest_info_fn=dest_info_fn, cancel=cancel, estimate_fn=estimate_fn)
    mr.meta.update(dest_free_bytes=space["dest_free_bytes"], required_bytes=space["required_bytes"],
                   estimate_bytes=space["estimate_bytes"], space_blocked=space["space_blocked"], dest_fs=space["dest_fs"],
                   items_planned=len(plan.items), dest_root=str(dst))
    if space["space_blocked"]:
        msg = (f"대상 드라이브의 여유 공간이 부족합니다. 선택한 폴더 {len(plan.items)}개에 필요 약 {fmt_bytes(space['required_bytes'])}"
               f"(선택 합계 {fmt_bytes(space['estimate_bytes'])} - 이미 대상에 있는 {fmt_bytes(space['existing_bytes'])} + 여유 5%), "
               f"현재 여유 {fmt_bytes(space['dest_free_bytes'] or 0)}. 폴더를 줄이거나 더 큰 드라이브를 쓰세요.")
        if dry_run:
            warn(msg)
        else:
            raise TransferError(msg)
    elif space["dest_free_bytes"] is None:
        warn("대상의 여유 공간을 조회하지 못해 전체 용량 점검을 건너뜁니다.")
    if space["dest_fs"] == "FAT32":
        warn("대상이 FAT32 입니다. 4GB 보다 큰 파일이 든 폴더는 그 폴더만 실패로 기록되고 나머지는 계속됩니다(--split-large 로 해결 가능).")
    journal = {"version": JOURNAL_VERSION, "items": {}} if dry_run else _read_journal(dst)
    prev_keys = set(journal["items"])
    new_keys = {it.rel.lower() for it in plan.items}
    if prev_keys and prev_keys != new_keys and not dry_run:
        warn("이전 실행과 선택한 폴더 구성이 다릅니다. 같은 대상에 이어서 담습니다(이전에 담은 폴더는 지우지 않습니다).")
    if not dry_run:
        os.makedirs(dst, exist_ok=True)
    item_dir = None
    final_manifest = Path(manifest_out) if manifest_out else None
    if final_manifest is not None:
        item_dir = final_manifest.with_name(final_manifest.name + ".items")
    total = len(plan.items)
    t0 = time.monotonic()
    for n, it in enumerate(plan.items, 1):
        if cancel is not None and cancel():
            raise Cancelled()
        item_dst = dst / it.rel
        label = f"[{n}/{total}] {it.src}"
        key = it.rel.lower()
        prior = journal["items"].get(key, {})
        if skip_done and not dry_run and not retry_failed and prior.get("status") == "done":
            mr.items.append(ItemResult(it.src, str(item_dst), "skipped_done"))
            if on_item:
                on_item(n, total, it.src, "skipped_done", None)
            continue
        if on_item:
            on_item(n, total, it.src, "start", None)
        kw = dict(item_kwargs or {})
        kw.update(dry_run=dry_run, cancel=cancel, warn=(lambda m, _l=label: warn(f"{_l}: {m}")))
        if retry_failed:
            kw["retry_failed"] = True
        if verify_only:
            kw["verify_only"] = True
        if progress:
            kw["progress"] = lambda m, _l=label: progress(f"{_l} {m}")
        if progress_info:
            kw["progress_info"] = lambda info, _l=label: progress_info(dataclasses.replace(info, message=f"{_l} {info.message}".strip()))
        if item_dir is not None and not dry_run:
            kw["manifest_out"] = item_dir / f"{n:04d}.jsonl"
        if not dry_run:
            journal["items"][key] = {"src": it.src, "rel": it.rel, "status": "pending"}
            _write_journal(dst, journal)
        try:
            rep = transfer_fn(Path(it.src), item_dst, **kw)
        except Cancelled:
            raise
        except DriveDisconnected:
            raise
        except (TransferError, OSError, ValueError) as e:
            res = ItemResult(it.src, str(item_dst), "error", error=str(e))
            if not dry_run:
                journal["items"][key] = {"src": it.src, "rel": it.rel, "status": "failed", "error": str(e)}
                _write_journal(dst, journal)
            mr.items.append(res)
            warn(f"{label}: 이 폴더는 처리하지 못했습니다 — {e}")
            if on_item:
                on_item(n, total, it.src, "error", None)
            continue
        bad = bool(rep.failures)
        status = "preview" if dry_run else ("failed" if bad else "ok")
        mr.items.append(ItemResult(it.src, str(item_dst), status, rep))
        if not dry_run:
            r = rep.to_dict()
            done = not bad and bool(r.get("verification_complete"))
            journal["items"][key] = {"src": it.src, "rel": it.rel, "status": "done" if done else "failed" if bad else "copied",
                                     "files": r.get("total_files"), "bytes": r.get("total_bytes"), "failed": len(rep.failures)}
            _write_journal(dst, journal)
        if on_item:
            on_item(n, total, it.src, status, rep)
    mr.meta["seconds"] = round(time.monotonic() - t0, 1)
    if final_manifest is not None and not dry_run:
        _merge_manifests(mr, plan, final_manifest, item_dir)
    return mr


def _merge_manifests(mr: MultiReport, plan: Plan, final: Path, item_dir: Path) -> None:
    """항목별 해시 목록을 목록 하나로 합친다. 상대 경로 앞에 항목의 대상 폴더(posix)를 붙여 verify --target <대상> 으로 비교한다."""
    parts_done = [(i + 1, plan.items[i], r) for i, r in enumerate(mr.items) if r.report is not None]
    total_files = total_bytes = 0
    rows_src = []
    for n, pi, r in parts_done:
        p = item_dir / f"{n:04d}.jsonl"
        if not p.is_file():
            continue
        rd = ManifestReader(p)
        rows_src.append((pi, rd))
        total_files += int(rd.header.get("total_files", 0))
        total_bytes += int(rd.header.get("total_bytes", 0))
    layout = {"items": [{"src": pi.src, "rel": pi.rel.replace("\\", "/")} for pi, _ in rows_src]}
    w = ManifestWriter(final, "(여러 원본)", total_files, total_bytes, layout)
    ok = False
    try:
        for pi, rd in rows_src:
            prefix = pi.rel.replace("\\", "/") + "/"
            for row in rd.rows():
                w.write_row(prefix + row["rel"], row["size"], row.get("mtime_ns", 0), row["sha256"], row.get("parts"))
        ok = True
    finally:
        if not ok:
            w.abort()
    mr.meta["manifest_out"] = str(w.close())
    mr.meta["manifest_files"] = w.files
    mr.meta["manifest_complete"] = len(rows_src) == len(plan.items) and not mr.failed_total
    try:
        for f in item_dir.glob("*.jsonl"):
            f.unlink()
        item_dir.rmdir()
    except OSError:
        pass
