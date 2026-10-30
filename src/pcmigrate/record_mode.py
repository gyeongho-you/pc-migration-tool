"""설정 기록 모드: 규칙이 없는 프로그램의 설정 파일을 '바꾸기 전/후 파일 상태 비교'로 근거 있게 찾는다.

흐름: start(스냅샷 A=before) -> snapshot(기준선 B=baseline, 설정은 바꾸지 않고 프로그램만 열었다 닫음) -> 설정 하나 바꾸고 snapshot(after1)
      -> 다른 설정으로 반복(after2 ...) -> diff / rule-draft.
판정: 기준선 단계(before->baseline)에서 바뀐 파일은 '원래 바뀌는 파일'(로그/캐시/창 위치)이라 제외하고, 각 after 단계에서 바뀐 파일이 후보다.
여러 라운드에서 반복해서 바뀔수록 확신도가 높다. 결과는 후보 목록, 규칙 JSON 초안, 보고서이며 모두 환경변수 표기 경로만 쓴다.
상태는 작업 폴더의 JSON 파일이다(record-state.json, snap-<라벨>.json, record-candidates.json, record-last-report.json, rule-draft.json).
스냅샷 방식/해시 최적화는 record_snapshot.py 설명 참고. 대상 폴더는 읽기만 하고 작업 폴더에만 쓴다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import copier, envpaths, errorlog, fs, settings_filter
from .cancel import Cancelled
from .progress import ProgressInfo, fmt_bytes
from .record_snapshot import (HASH_LIMIT, MAX_FILES, Root, Snapshot, SnapshotOptions, custom_root, default_roots, match_words,
                              snapshot_changes, standard_token, take_snapshot)

STATE_FILE = "record-state.json"
CAND_FILE = "record-candidates.json"
REPORT_FILE = "record-last-report.json"
DRAFT_FILE = "rule-draft.json"
BEFORE, BASELINE, AFTER = "before", "baseline", "after"
HIGH, MID, LOW = "높음", "중간", "낮음"
RANK = {LOW: 0, MID: 1, HIGH: 2}
RANK_ARG = {"low": LOW, "medium": MID, "high": HIGH}
NOISE_EXTS = frozenset({".log", ".tmp", ".etl", ".dmp", ".old", ".lock", ".cache", ".trace", ".swp", ".bak", ".ldb", ".lck"})
BIG_CANDIDATE = settings_filter.MAX_FILE_BYTES
BIG_FOLDER = settings_filter.MAX_FOLDER_BYTES
REGISTRY_MSG = ("파일이 하나도 바뀌지 않았습니다. 이 프로그램은 설정을 레지스트리에 저장할 가능성이 높습니다(파일 기반 이전 불가). "
                "다른 설정을 바꿔 다시 시도하거나, 프로그램을 완전히 종료해야 저장되는지(종료 시에만 저장) 확인하세요.")
TRUNCATED_MSG = ("파일 수 상한(--max-files)으로 스냅샷이 잘려서 '바뀐 파일이 없다'고 판단할 수 없습니다. "
                 "--max-files 를 늘리거나 --program/--match/--roots 로 범위를 좁혀 다시 시작하세요(레지스트리 저장으로 단정하지 마세요).")
BASELINE_ONLY_MSG = ("바뀐 파일은 있었지만 모두 기준선에서도 바뀌던 파일(또는 비설정 형식)이라 설정 파일로 확정하지 못했습니다. "
                     "기준선을 한 번 더 찍거나(--label baseline2) 설정을 바꾼 라운드를 반복해 보세요. 레지스트리 저장일 수도 있습니다.")
DEFAULT_WARNINGS = (
    "이 규칙은 설정 기록 모드로 자동 만든 초안입니다. 신 PC 에 같은 버전의 프로그램을 설치한 뒤 복원하고 설정이 실제로 반영되는지 확인하세요.",
    "라이선스/로그인 정보는 복원되지 않습니다. 신 PC 에서 다시 활성화하거나 로그인하세요.",
    "레지스트리에 저장되는 설정과 클라우드 계정에 동기화되는 설정은 파일로 옮겨지지 않을 수 있습니다.",
)


class RecordError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 오류(상태 파일 없음, 라벨 오류 등)."""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeError) as e:
        raise RecordError(f"{path.name} 을(를) 읽지 못했습니다({type(e).__name__}). 작업 폴더를 확인하세요") from e


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "recorded-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def default_work_dir(program: str) -> str:
    return envpaths.expand_env("%LOCALAPPDATA%\\pcmigrate\\record\\") + slug(program)


def scrubber(roots: list[Root], work: str = "") -> Callable[[str], str]:
    """출력/보고서용: 알려진 루트 경로와 사용자 홈 경로를 환경변수 표기로 바꾼다(사용자 이름이 새지 않게)."""
    pairs = sorted(((r.path, r.token) for r in roots), key=lambda p: -len(p[0]))

    def fn(text: str) -> str:
        for path, tok in pairs:
            text = re.sub(re.escape(path), lambda _m, t=tok: t, text, flags=re.I)
        return errorlog.tokenize_home(text)
    return fn


# --------------------------------------------------------------------------- 상태
def _kind(label: str) -> str:
    if label == BEFORE:
        return BEFORE
    if re.fullmatch(r"baseline\d*", label):
        return BASELINE
    if re.fullmatch(r"after\d+", label):
        return AFTER
    raise RecordError(f"라벨은 before, baseline(baseline2 ...), after1(after2 ...) 중 하나여야 합니다(입력: {label!r})")


@dataclass
class Session:
    work: Path
    state: dict

    @property
    def snaps(self) -> list[dict]:
        return self.state["snapshots"]

    def next_label(self) -> str:
        kinds = [s["kind"] for s in self.snaps]
        if AFTER in kinds:
            return f"after{kinds.count(AFTER) + 1}"
        return "after1" if BASELINE in kinds else "baseline"

    def roots(self, documents_fn=None) -> tuple[list[Root], list[str]]:
        sc = self.state["scope"]
        if sc["custom_roots"]:
            return [custom_root(envpaths.expand_env(p)) for p in sc["custom_roots"]], []
        return default_roots(documents_fn)

    def options(self) -> SnapshotOptions:
        sc = self.state["scope"]
        return SnapshotOptions(sc["hash_limit"], sc["max_files"], sc["include_caches"],
                               [] if sc["all_roots"] or sc["custom_roots"] else match_words(self.state["program"], sc["match"]),
                               [str(self.work)])

    def load_snap(self, label: str) -> Snapshot:
        d = _read_json(self.work / f"snap-{label}.json")
        if d is None:
            raise RecordError(f"스냅샷 파일 snap-{label}.json 이 없습니다. start 부터 다시 하세요")
        return Snapshot.from_dict(d)


def load_session(work: str | Path) -> Session:
    work = Path(envpaths.expand_env(str(work)))
    st = _read_json(work / STATE_FILE)
    if not st:
        raise RecordError("기록 상태를 찾지 못했습니다. 먼저 `pcmigrate record start --program 이름` 으로 시작하세요"
                          f"(작업 폴더: {errorlog.tokenize_home(str(work))})")
    return Session(work, st)


def _save_state(s: Session) -> None:
    _atomic_write(s.work / STATE_FILE, json.dumps(s.state, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------- 프로세스 확인(막지 않고 경고만)
def default_process_list() -> list[str] | None:
    """실행 중인 프로세스 이름 목록(tasklist, 읽기 전용). 조회할 수 없으면 None."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return [line.split('","')[0].strip('"') for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return None


def running_processes(program: str, extra: list[str], process_list_fn: Callable[[], list[str] | None] | None) -> list[str]:
    try:
        names = (process_list_fn or default_process_list)() or []
    except Exception as e:  # noqa: BLE001 - 프로세스 조회 실패는 경고를 못 할 뿐 작업을 막지 않는다
        errorlog.record(e, "설정 기록: 프로세스 조회")
        return []
    words = [w for w in match_words(program, extra) if len(w) >= 4]  # 짧은 낱말은 엉뚱한 프로세스(예: unsecapp)와 겹쳐 경고가 오탐이 된다
    hit = [n for n in names if any(w in n.lower().replace(" ", "") for w in words)]
    return sorted(set(hit))[:10]


# --------------------------------------------------------------------------- 보고서(어떤 경우에도 저장)
def _write_report(work: Path, rep: dict) -> None:
    try:
        work.mkdir(parents=True, exist_ok=True)
        _atomic_write(work / REPORT_FILE, json.dumps(rep, ensure_ascii=False, indent=1))
    except OSError as e:
        errorlog.record(e, "설정 기록: 보고서 저장")


@dataclass
class SnapshotResult:
    label: str
    status: str  # ok | cancelled | failed
    files: int = 0
    hashed: int = 0
    carried: int = 0
    unreadable: int = 0
    cloud_skipped: int = 0
    links_skipped: int = 0
    truncated: bool = False
    running: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    root_notes: list = field(default_factory=list)
    matched: list = field(default_factory=list)  # 이름으로 좁혀 훑은 시작 폴더(환경변수 표기)


def _archive_previous(wd: Path) -> list[str]:
    """start --force: 이전 세션 산출물(스냅샷, 상태, 후보, 보고서, 초안)을 지우지 않고 .bak 으로 옮겨 새 기록과 섞이지 않게 한다."""
    moved: list[str] = []
    for f in sorted(wd.glob("snap-*.json")) + [wd / n for n in (STATE_FILE, CAND_FILE, REPORT_FILE, DRAFT_FILE)]:
        if f.is_file():
            try:
                os.replace(f, copier._backup_path(f))
                moved.append(f.name)
            except OSError as e:
                errorlog.record(e, "설정 기록: 이전 산출물 보관")
                raise RecordError(f"이전 기록 파일 {f.name} 을(를) 보관하지 못했습니다({type(e).__name__}). 파일을 닫고 다시 시도하세요") from e
    return moved


def start(program: str, work: str | None = None, roots: list[str] | None = None, match: list[str] | None = None,
          all_roots: bool = False, include_caches: bool = False, hash_limit: int = HASH_LIMIT, max_files: int = MAX_FILES,
          force: bool = False, **kw) -> tuple[Session, SnapshotResult]:
    """기록을 시작하고 변경 전 스냅샷(before)을 찍는다. 작업 폴더에 이미 기록이 있으면 force 가 필요하다."""
    program = program.strip()
    if not program:
        raise RecordError("--program 에 프로그램 이름이 필요합니다")
    wd = Path(envpaths.expand_env(work or default_work_dir(program)))
    missing = [r for r in (roots or []) if not os.path.isdir(fs.long_path(os.path.abspath(r)))]
    if missing:
        raise RecordError("--roots 폴더를 찾지 못했습니다: " + ", ".join(errorlog.tokenize_home(m) for m in missing)
                          + " (경로 철자와 폴더 존재 여부를 확인하세요)")
    if (wd / STATE_FILE).exists() and not force:
        raise RecordError("이 작업 폴더에는 이미 기록이 있습니다. 이어서 하려면 `record snapshot`, 처음부터 다시 하려면 --force 를 쓰세요")
    wd.mkdir(parents=True, exist_ok=True)
    if force:
        _archive_previous(wd)
    state = {"version": 1, "program": program, "created": (kw.get("now") or datetime.now)().strftime("%Y-%m-%d"), "snapshots": [],
             "scope": {"custom_roots": [standard_token(fs.plain_path_text(os.path.abspath(r))) for r in (roots or [])], "match": list(match or []),
                       "all_roots": all_roots, "include_caches": include_caches, "hash_limit": hash_limit, "max_files": max_files}}
    s = Session(wd, state)
    return s, snapshot(s, BEFORE, **kw)


def snapshot(s: Session, label: str | None = None, cancel=None, progress=None, process_list_fn=None, documents_fn=None,
             clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = datetime.now,
             attr_getter=None, hash_fn=None) -> SnapshotResult:
    """스냅샷 하나를 찍어 저장한다. 취소/오류가 나도 보고서는 저장하고, 미완성 스냅샷은 등록하지 않는다."""
    label = label or (BEFORE if not s.snaps else s.next_label())
    res = SnapshotResult(label, "failed")
    roots: list[Root] = []
    try:
        kind = _kind(label)
        if any(x["label"] == label for x in s.snaps):
            raise RecordError(f"라벨 '{label}' 은(는) 이미 있습니다. 다른 라벨을 쓰거나 start --force 로 다시 시작하세요")
        if kind != BEFORE and not s.snaps:
            raise RecordError("먼저 변경 전 스냅샷(before)이 필요합니다(start)")
        if kind == BASELINE and any(x["kind"] == AFTER for x in s.snaps):
            raise RecordError("기준선(baseline)은 설정을 바꾸기 전에 찍어야 합니다(after 스냅샷 뒤에는 찍을 수 없습니다)")
        res.running = running_processes(s.state["program"], s.state["scope"]["match"], process_list_fn)
        roots, res.root_notes = s.roots(documents_fn)
        if not roots:
            raise RecordError("훑을 폴더가 없습니다(--roots 를 지정하거나 환경변수를 확인하세요)")
        prev = s.load_snap(s.snaps[-1]["label"]) if s.snaps else None
        kw = {} if hash_fn is None else {"hash_fn": hash_fn}
        snap = take_snapshot(label, roots, s.options(), prev, cancel, progress, clock, now, attr_getter, **kw)
        _atomic_write(s.work / f"snap-{label}.json", json.dumps(snap.as_dict(), ensure_ascii=False, separators=(",", ":")))
        s.snaps.append({"label": label, "kind": kind, "file": f"snap-{label}.json", "taken_at": snap.taken_at, "files": len(snap.files)})
        _save_state(s)
        res.status = "ok"
        res.files, res.hashed, res.carried = len(snap.files), snap.hashed, snap.carried
        res.unreadable, res.cloud_skipped, res.links_skipped = snap.unreadable_count, snap.cloud_skipped, snap.links_skipped
        res.truncated = snap.truncated
        res.warnings = list(snap.notes)
        res.matched = list(snap.matched)
        if not snap.files:
            res.warnings.append("일치하는 파일이 하나도 없습니다. 프로그램을 한 번 실행한 적이 없거나 이름이 폴더와 다를 수 있습니다"
                                "(--match 낱말 추가, --all-roots, --roots 폴더 지정)")
        return res
    except Cancelled:
        res.status = "cancelled"
        raise
    except BaseException as e:
        if not isinstance(e, RecordError):
            errorlog.record(e, "설정 기록 스냅샷")
        raise
    finally:
        sc = scrubber(roots)
        _write_report(s.work, {"op": "snapshot", "label": label, "status": res.status, "program": s.state["program"],
                               "at": now().isoformat(timespec="seconds"), "files": res.files, "hashed": res.hashed, "carried": res.carried,
                               "unreadable": res.unreadable, "cloud_skipped": res.cloud_skipped, "links_skipped": res.links_skipped,
                               "truncated": res.truncated, "running_processes": [sc(x) for x in res.running],
                               "note": "" if res.status == "ok" else "이 스냅샷은 저장되지 않았습니다(같은 라벨로 다시 찍을 수 있습니다)"})


# --------------------------------------------------------------------------- 분석
@dataclass
class Candidate:
    path: str
    confidence: str
    size: int
    ext: str
    hits: int
    rounds: int
    change: str
    reason: str
    masked: bool = False  # 기준선에서도 바뀌었으나 설정 형식이라 참고로만 표시
    noise_like: bool = False
    hash_unknown: bool = False
    removed: bool = False  # 마지막 변화가 삭제(참고로만 표시)
    promoted: bool = False  # 기준선에서 처음 생성됐지만 이후 라운드에서 또 바뀌어 후보로 올림

    def as_dict(self) -> dict:
        return {"path": self.path, "confidence": self.confidence, "size": self.size, "ext": self.ext,
                "changed_rounds": f"{self.hits}/{self.rounds}", "change": self.change, "reason": self.reason,
                "baseline_masked": self.masked, "promoted_from_baseline": self.promoted}


@dataclass
class Analysis:
    program: str
    rounds: int = 0
    baselines: int = 0
    candidates: list = field(default_factory=list)
    noise_files: int = 0
    mtime_only: int = 0
    unreadable: int = 0
    warnings: list = field(default_factory=list)
    registry_likely: bool = False
    noise_paths: list = field(default_factory=list)
    folder_bytes: dict = field(default_factory=dict)  # 마지막 스냅샷 기준 폴더별 합계(규칙 초안의 폴더 묶기 판단용)
    last_files: dict = field(default_factory=dict)
    root_tokens: list = field(default_factory=list)  # 훑은 루트(환경변수 표기): 폴더 묶기 금지 판단용
    truncated: bool = False
    undetermined: bool = False  # 바뀐 파일은 있었지만 설정 파일로 확정 못 함(레지스트리 단정 아님)
    outside: list = field(default_factory=list)  # 규칙 경로가 될 수 없는 위치(다른 드라이브 등)의 후보 경로


CHANGE_TEXT = {"added": "추가", "removed": "삭제", "modified": "수정"}


def _confidence(hits: int, rounds: int, config_like: bool, noise_like: bool, big: bool, no_baseline: bool) -> tuple[str, str]:
    if rounds <= 1:
        c, why = (MID, "한 라운드에서 바뀜(교차 확인 불가)") if config_like else (LOW, "한 라운드에서 바뀜")
    elif hits == rounds:
        c, why = HIGH, f"모든 라운드({rounds}회)에서 바뀜"
    elif hits >= 2 and hits * 2 >= rounds:
        c, why = MID, f"{rounds}회 중 {hits}회 바뀜"
    else:
        c, why = LOW, f"{rounds}회 중 {hits}회만 바뀜"
    if noise_like and c != LOW:
        c, why = LOW, why + "; 로그/임시 형식이라 낮춤"
    if big and c != LOW:
        c, why = LOW, why + f"; {fmt_bytes(BIG_CANDIDATE)} 초과 큰 파일이라 낮춤"
    if no_baseline and c == HIGH:
        c, why = MID, why + "; 기준선이 없어 낮춤"
    return c, why


def _normalize_tok(p: str) -> str:
    """같은 폴더를 가리키는 %USERPROFILE%/AppData/Roaming|Local 표기를 %APPDATA%/%LOCALAPPDATA% 표준 토큰으로 통일한다."""
    low = p.lower()
    for prefix, tok in (("%userprofile%\\appdata\\roaming", "%APPDATA%"), ("%userprofile%\\appdata\\local", "%LOCALAPPDATA%")):
        if low == prefix or low.startswith(prefix + "\\"):
            return tok + p[len(prefix):]
    return p


def analyze(s: Session, scrub: Callable[[str], str] | None = None) -> Analysis:
    a = Analysis(s.state["program"])
    infos = s.snaps
    if len(infos) < 2:
        raise RecordError("비교할 스냅샷이 부족합니다. before 다음에 baseline/after 스냅샷을 찍으세요(`record status` 로 확인)")
    def load(label: str) -> Snapshot:
        sn = s.load_snap(label)
        if any(k.lower().startswith("%userprofile%\\appdata\\") for k in sn.files):  # 옛 표기 통일
            sn.files = {_normalize_tok(k): v for k, v in sn.files.items()}
        return sn

    prev = load(infos[0]["label"])
    noise: dict[str, set] = {}  # 기준선 단계에서 바뀐 파일 -> 바뀐 방식들
    rounds: list[tuple[str, dict]] = []
    for info in infos[1:]:
        cur = load(info["label"])
        ch, touched, unknown = snapshot_changes(prev, cur)
        a.mtime_only += touched
        a.unreadable = cur.unreadable_count  # 스냅샷마다 같은 항목을 또 못 읽으므로 합산하지 않고 마지막 스냅샷 값을 쓴다
        if cur.truncated or prev.truncated:
            a.truncated = True
            a.warnings.append(f"스냅샷 '{info['label']}' 은(는) 파일 수 상한으로 잘렸습니다. 추가/삭제로 보이는 항목은 신뢰할 수 없습니다")
        if info["kind"] == BASELINE:
            for k, v in ch.items():
                noise.setdefault(k, set()).add(v)
            a.baselines += 1
        else:
            rounds.append((info["label"], ch))
            for k in unknown:
                ch[k] = "modified?"  # 내용 비교 불가 표시
        prev = cur
    a.last_files, a.rounds = prev.files, len(rounds)
    a.root_tokens = [_normalize_tok(r) for r in prev.roots]
    a.noise_files, a.noise_paths = len(noise), sorted(noise)
    if not rounds:
        raise RecordError("설정을 바꾼 뒤의 스냅샷(after1 ...)이 없습니다. 기준선만으로는 후보를 만들 수 없습니다")
    if a.baselines == 0:
        a.warnings.append("기준선(baseline)이 없어 원래 바뀌는 파일(로그/캐시)을 걸러내지 못했습니다. 후보에 잡음이 섞일 수 있습니다")
    hits: dict[str, list[str]] = {}
    for _lbl, ch in rounds:
        for k, v in ch.items():
            hits.setdefault(k, []).append(v)
    for k, kinds in hits.items():
        in_noise = k in noise
        # 기준선에서 처음 생성된(added) 파일이 이후 라운드에서 내용이 또 바뀌었으면 '원래 바뀌는 파일'이 아니라 설정일 수 있어 승격한다
        promoted = in_noise and "added" in noise[k]
        masked = in_noise and not promoted
        name = k.rsplit("\\", 1)[-1]
        ext = os.path.splitext(name)[1].lower()
        config_like = settings_filter.is_config_name(name)
        if masked and not config_like:
            continue  # 기준선에서 바뀐 비설정 파일 = 원래 바뀌는 파일
        size = (a.last_files.get(k) or [0])[0]
        noise_like = ext in NOISE_EXTS
        conf, why = _confidence(len(kinds), len(rounds), config_like, noise_like, size > BIG_CANDIDATE, a.baselines == 0)
        if config_like:
            why += "; 설정 형식 확장자"
        if promoted:
            cap = MID if config_like else LOW
            if RANK[conf] > RANK[cap]:
                conf = cap
            why = "기준선에서 처음 생성된 파일이 이후 라운드에서 내용이 또 바뀌어 후보로 올림(근거: 기준선 추가 + 이후 변경); " + why
        if masked:
            conf, why = LOW, "기준선에서도 바뀌었지만 설정 형식이라 참고로 표시(기준선에서 이미 있던 파일이라 설정 변경 때문인지 확정 못 함)"
        unk = any(x == "modified?" for x in kinds)
        if unk:
            why += "; 큰 파일이라 내용 비교 불가(크기/시각만 비교)"
        last = kinds[-1].rstrip("?")
        if last == "removed":
            why += "; 삭제된 파일은 참고로만 표시(규칙 초안에는 넣지 않음)"
        a.candidates.append(Candidate(k, conf, size, ext, len(kinds), len(rounds), CHANGE_TEXT.get(last, last), why, masked, noise_like, unk,
                                      removed=last == "removed", promoted=promoted))
    a.candidates.sort(key=lambda c: (-RANK[c.confidence], c.masked, c.path.lower()))
    if any(not c.masked for c in a.candidates):
        pass
    elif not hits:
        if a.truncated:  # 잘린 스냅샷으로는 '바뀐 것이 없다'고 말할 수 없다
            a.undetermined = True
            a.warnings.append(TRUNCATED_MSG)
        else:
            a.registry_likely = True
            a.warnings.append(REGISTRY_MSG)
    else:
        a.undetermined = True
        a.warnings.append(BASELINE_ONLY_MSG)
    for k, v in a.last_files.items():  # 폴더별 재귀 합계: 조상 폴더 전부에 더한다(하위 폴더의 큰 파일도 상위 폴더 용량에 포함)
        d = k
        while "\\" in d:
            d = d.rsplit("\\", 1)[0]
            a.folder_bytes[d] = a.folder_bytes.get(d, 0) + v[0]
    return a


def candidates_dict(a: Analysis, now: Callable[[], datetime] = datetime.now) -> dict:
    return {"program": a.program, "generated": now().isoformat(timespec="seconds"), "rounds": a.rounds, "baselines": a.baselines,
            "registry_likely": a.registry_likely, "undetermined": a.undetermined, "truncated": a.truncated, "noise_files": a.noise_files, "mtime_only_files": a.mtime_only,
            "unreadable": a.unreadable, "warnings": a.warnings, "candidates": [c.as_dict() for c in a.candidates]}


def diff(s: Session, now: Callable[[], datetime] = datetime.now) -> Analysis:
    """후보 목록을 계산해 record-candidates.json 과 보고서로 저장한다(오류가 나도 보고서는 남긴다)."""
    status, a = "failed", None
    try:
        a = analyze(s)
        _atomic_write(s.work / CAND_FILE, json.dumps(candidates_dict(a, now), ensure_ascii=False, indent=1))
        status = "ok"
        return a
    except BaseException as e:
        if not isinstance(e, RecordError):
            errorlog.record(e, "설정 기록 비교")
        raise
    finally:
        _write_report(s.work, {"op": "diff", "status": status, "program": s.state["program"], "at": now().isoformat(timespec="seconds"),
                               "candidates": len(a.candidates) if a else 0, "registry_likely": bool(a and a.registry_likely),
                               "warnings": a.warnings if a else []})


# --------------------------------------------------------------------------- 규칙 초안
def _parent(p: str) -> str:
    return p.rsplit("\\", 1)[0]


STD_ROOT_DIRS = frozenset({"%appdata%", "%localappdata%", "%programdata%", "%userprofile%", "%userprofile%\\documents", "%userprofile%\\appdata",
                           "%userprofile%\\appdata\\roaming", "%userprofile%\\appdata\\local", "%userprofile%\\appdata\\locallow"})


def _is_root_dir(d: str, roots: list[str] | None = None) -> bool:
    """폴더 묶기를 하면 안 되는 폴더인지: 표준 루트/그 상위 폴더이거나, 이번에 훑은 루트와 같거나 그 상위(루트를 통째로 담게 되므로)."""
    dl = d.lower()
    if dl in STD_ROOT_DIRS:
        return True
    return any(r.lower() == dl or r.lower().startswith(dl + "\\") for r in (roots or []))


def _dest(slug_: str, path: str, used: set[str]) -> str:
    parts = [x for x in path.replace("%", "").split("\\") if x and x not in (".", "..")]
    parts = [re.sub(r'[<>:"|?*]', "_", x).lower() if i == 0 else re.sub(r'[<>:"|?*]', "_", x) for i, x in enumerate(parts)]
    base = slug_ + "/" + "/".join(parts)
    d, n = base, 2
    while d.lower() in used:
        d, n = f"{base}-{n}", n + 1
    used.add(d.lower())
    return d


OUTSIDE_MSG = ("규칙 경로는 %환경변수%(%APPDATA%, %LOCALAPPDATA%, %PROGRAMDATA%, %USERPROFILE% 아래)로 시작해야 합니다. "
               "홈/ProgramData 밖 폴더(예: 다른 드라이브의 설치 폴더)는 규칙 초안에 담을 수 없습니다. "
               "바뀐 파일은 record-candidates.json 에 있으니, 그 위치의 파일은 모드 B(데이터 직접 전송)나 직접 복사로 옮기세요")


def no_draft_message(a: Analysis) -> str:
    """초안을 만들지 않은 이유(사용자에게 보여 줄 한 문장)."""
    if a.registry_likely:
        return REGISTRY_MSG
    if a.truncated and a.undetermined:
        return TRUNCATED_MSG
    if a.undetermined:
        return BASELINE_ONLY_MSG
    if a.outside:
        return OUTSIDE_MSG
    if a.candidates and all(c.removed for c in a.candidates if not c.masked):
        return "바뀐 파일이 삭제뿐이라 규칙 초안에 담을 파일이 없습니다(삭제는 참고로만 표시합니다)."
    return "조건에 맞는 후보가 없습니다(--min-confidence low 로 낮춰 보세요)."


def build_rule(a: Analysis, min_conf: str = LOW, rule_id: str | None = None, today: str | None = None) -> dict | None:
    """분석 결과로 규칙 JSON 초안(dict). 후보가 없으면 None. 규칙 스키마(rules.parse_rule)를 따른다."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    ok = [c for c in a.candidates if not c.masked and not c.noise_like and not c.removed and c.size <= BIG_CANDIDATE
          and RANK[c.confidence] >= RANK[min_conf] and "*" not in c.path and "?" not in c.path]
    a.outside = [c.path for c in ok if not c.path.startswith("%")]  # 규칙 경로가 될 수 없는 위치(다른 드라이브 등)
    pick = [c for c in ok if c.path.startswith("%")]
    left = [c for c in a.candidates if c not in pick]
    if not pick:
        return None
    sid = rule_id or slug(a.program)
    by_dir: dict[str, list[Candidate]] = {}
    for c in pick:
        by_dir.setdefault(_parent(c.path), []).append(c)
    entries: list[tuple[str, str, str]] = []  # (경로, kind, 설명)
    for d, cs in sorted(by_dir.items()):
        # 폴더 묶기: 2개 이상, 루트(또는 그 상위)가 아니고, 폴더의 재귀 합계(하위 폴더 포함)가 상한 이하일 때만
        folder_ok = len(cs) >= 2 and not _is_root_dir(d, a.root_tokens) and a.folder_bytes.get(d, 0) <= BIG_FOLDER
        if folder_ok:
            entries.append((d, "dir", f"바뀐 설정 파일 {len(cs)}개가 있는 폴더(설정 기록 모드)"))
        else:
            for c in cs:
                entries.append((c.path, "file", "바뀐 설정 파일(설정 기록 모드)"))
    dir_set = {e[0].lower() for e in entries if e[1] == "dir"}
    # 상위 폴더가 이미 담는 파일과 하위 폴더는 뺀다(중첩 dir 은 상위만 남김)
    entries = [e for e in entries if not any(e[0].lower() != d and e[0].lower().startswith(d + "\\") for d in dir_set)]
    dirs = [e[0].lower() + "\\" for e in entries if e[1] == "dir"]
    used: set[str] = set()
    paths = [{"path": p, "dest": _dest(sid, p, used), "kind": k, "description": desc} for p, k, desc in entries]
    cand_names = {c.path.rsplit("\\", 1)[-1].lower() for c in pick}
    excl = ["*.lic", "*.license"]
    for p in a.noise_paths:  # 기준선에서 바뀐 파일: 로그/임시 형식은 확장자 패턴으로, 나머지는 폴더 안에 있을 때만 이름으로
        name = p.rsplit("\\", 1)[-1]
        ext = os.path.splitext(name)[1].lower()
        if ext in NOISE_EXTS:
            pat = f"*{ext}"
        elif any(p.lower().startswith(x) for x in dirs) and not settings_filter.is_config_name(name):
            pat = name
        else:
            continue
        if pat.lower() not in {e.lower() for e in excl} and name.lower() not in cand_names:
            excl.append(pat)
    for c in left:  # 로그/임시 형식으로 바뀐 후보도 제외 패턴으로(설정이 아니라 잡음일 가능성)
        if c.noise_like and not c.masked and f"*{c.ext}" not in excl:
            excl.append(f"*{c.ext}")
    notes = (f"설정 기록 모드로 생성, 확인 필요. 라운드 {a.rounds}회(기준선 {a.baselines}회), 생성일 {today}. "
             f"후보 {len(pick)}개를 paths 에 담았고 기준선에서 바뀐 파일 {a.noise_files}개는 제외했습니다.")
    skipped = [c for c in left if not c.masked and not c.removed]
    if skipped:
        notes += f" 확신도/형식/크기 때문에 담지 않은 후보 {len(skipped)}개는 record-candidates.json 에서 확인하세요."
    if any(c.masked for c in left):
        notes += " 기준선에서도 바뀐 설정 형식 파일은 참고로만 표시했습니다."
    if any(c.removed for c in left):
        notes += " 삭제된 파일은 담지 않았습니다."
    if a.outside:
        notes += f" 홈/ProgramData 밖 위치의 후보 {len(a.outside)}개는 규칙 경로로 쓸 수 없어 담지 않았습니다."
    if any(c.promoted for c in pick):
        notes += " 기준선에서 처음 생성된 뒤 다시 바뀐 파일이 포함돼 있습니다(근거는 record-candidates.json)."
    if any(c.hash_unknown for c in pick):
        notes += " 큰 파일은 내용 비교 없이 크기/시각으로만 판단했습니다."
    notes += " 폴더 안 캐시/임시 폴더는 복원 전에 직접 확인하세요."
    return {"id": sid, "name": a.program,
            "detect": {"display_name_patterns": [a.program], "install_paths": [], "registry_keys": []},
            "paths": paths, "manual_warnings": list(DEFAULT_WARNINGS), "version": "1", "exclude": excl, "notes": notes}


def _id_conflicts(rule_id: str, out: Path) -> list[str]:
    """같은 id 의 기존 규칙 파일 이름들(프로그램 rules 폴더와 초안을 저장할 폴더). 초안 자신(덮어쓸 파일)은 제외."""
    dirs: list[Path] = []
    try:
        from .rules import default_rules_dir
        dirs.append(default_rules_dir())
    except Exception:  # noqa: BLE001 - 규칙 폴더를 못 찾아도 경고만 못 할 뿐
        pass
    dirs.append(out.parent)
    found: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            files = sorted(Path(d).glob("*.json"))
        except OSError:
            continue
        for f in files:
            key = os.path.normcase(str(f.resolve()))
            if key in seen or key == os.path.normcase(str(out.resolve())):
                continue
            seen.add(key)
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("id") == rule_id:
                found.append(f.name)
    return found


def write_draft(s: Session, output: str | None = None, min_conf: str = LOW, rule_id: str | None = None,
                now: Callable[[], datetime] = datetime.now) -> tuple[Analysis, dict | None, Path | None]:
    """후보를 계산하고 규칙 초안을 저장한다. 파일이 바뀌지 않았으면 초안은 만들지 않고 레지스트리 안내를 낸다. 보고서는 항상 저장.
    --output 이 이미 있으면 번호 .bak 으로 보관한 뒤 쓴다. 같은 id 의 기존 규칙이 있으면 경고한다."""
    status, a, rule, out = "failed", None, None, None
    try:
        a = diff(s, now)
        rule = build_rule(a, min_conf, rule_id, now().strftime("%Y-%m-%d"))
        if rule is None and a.outside:
            raise RecordError(OUTSIDE_MSG)
        if rule is not None:
            from .rules import RuleError, parse_rule  # 만든 초안이 스키마 검증을 통과하는지 저장 전에 확인
            try:
                parse_rule(rule, "<초안>")
            except RuleError as e:
                raise RecordError(f"만든 초안이 규칙 형식에 맞지 않습니다: {e}. {OUTSIDE_MSG}") from e
            out = Path(envpaths.expand_env(output)) if output else s.work / DRAFT_FILE
            out.parent.mkdir(parents=True, exist_ok=True)
            dup = _id_conflicts(rule["id"], out)
            if dup:
                a.warnings.append(f"규칙 id '{rule['id']}' 가 기존 규칙 파일({', '.join(dup)})과 같습니다. 같은 폴더에 두면 규칙 중복 오류가 납니다. --id 로 바꾸세요")
            if out.exists():
                bak = copier._backup_path(out)
                os.replace(out, bak)
                a.warnings.append(f"기존 {out.name} 은(는) {bak.name} 으로 보관하고 새 초안을 저장했습니다")
            _atomic_write(out, json.dumps(rule, ensure_ascii=False, indent=2) + "\n")
        status = "ok" if rule else "no_draft"
        return a, rule, out
    except BaseException as e:
        if not isinstance(e, RecordError):
            errorlog.record(e, "설정 기록 규칙 초안")
        raise
    finally:
        _write_report(s.work, {"op": "rule-draft", "status": status, "program": s.state["program"],
                               "at": now().isoformat(timespec="seconds"), "draft": str(out.name) if out and status == "ok" else "",
                               "paths": len(rule["paths"]) if rule else 0,
                               "registry_likely": bool(a and a.registry_likely), "warnings": a.warnings if a else [],
                               "note": "" if status != "failed" else "초안을 만들지 못했습니다(사유는 화면 메시지/오류 로그 참고)"})


# --------------------------------------------------------------------------- 표시
def render_status(s: Session) -> list[str]:
    st = s.state
    sc = st["scope"]
    lines = [f"프로그램: {st['program']} (시작 {st['created']})",
             "범위: " + ("지정 폴더 " + ", ".join(sc["custom_roots"]) if sc["custom_roots"] else
                       ("기본 루트 전체" if sc["all_roots"] else "기본 루트 중 이름이 맞는 폴더(" + ", ".join(match_words(st['program'], sc['match'])) + ")"))
             + (", 캐시 포함" if sc["include_caches"] else "") + f", 해시 상한 {fmt_bytes(sc['hash_limit'])}"]
    for i in s.snaps:
        lines.append(f"  - {i['label']}: 파일 {i['files']:,}개 ({i['taken_at']})")
    kinds = [i["kind"] for i in s.snaps]
    if len(kinds) == 1:
        nxt = "프로그램을 설정 변경 없이 열었다 닫은 뒤 `record snapshot`(기준선)"
    elif AFTER not in kinds:
        nxt = "설정 하나를 바꾸고 프로그램을 닫은 뒤 `record snapshot`(after1)"
    else:
        nxt = "다른 설정으로 반복하거나 `record rule-draft`"
    lines.append("다음 할 일: " + nxt)
    return lines


def render_candidates(a: Analysis, limit: int = 40) -> list[str]:
    lines = [f"[{a.program}] 라운드 {a.rounds}회, 기준선 {a.baselines}회 — 기준선에서 바뀌어 제외한 파일 {a.noise_files}개, 시각만 바뀐 파일(내용 같음) {a.mtime_only}개"]
    if a.unreadable:
        lines.append(f"읽지 못한 항목 {a.unreadable}개(오류 로그 확인: pcmigrate logs)")
    if not a.candidates:
        lines.append("설정 파일 후보가 없습니다.")
    for c in a.candidates[:limit]:
        tag = " [참고]" if c.masked else ""
        lines.append(f"  {c.confidence}{tag}  {c.path}  ({fmt_bytes(c.size)}, {c.ext or '확장자 없음'}, {c.change}, {c.hits}/{c.rounds}회) — {c.reason}")
    if len(a.candidates) > limit:
        lines.append(f"  ... 외 {len(a.candidates) - limit}개(record-candidates.json 참고)")
    lines += [f"주의: {w}" for w in a.warnings]
    return lines
