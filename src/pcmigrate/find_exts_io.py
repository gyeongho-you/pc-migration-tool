r"""'찾을 파일 형식' 설정의 내보내기/가져오기(find_exts 의 보조 모듈).

내보내기: 현재 설정(기본값과의 차이)을 표식·버전이 있는 JSON 한 파일로 쓴다(원자 저장, 같은 이름 파일은 번호 .bak 으로 보관).
  경로·사용자 이름·컴퓨터 이름은 들어가지 않는다(확장자 글자, 분류 이름, 도구 버전, 만든 시각(UTC)만).
가져오기: 파일을 **먼저 읽어 엄격하게 검증**하고, 이상하면 아무것도 적용하지 않고 거부한다(부분 적용 없음, 기존 설정은 그대로).
  - 크기 1MB 이하, UTF-8(BOM 허용), JSON 이어야 하고 NaN/Infinity·같은 이름의 키 두 번·너무 깊은 중첩은 거부한다.
  - 최상위는 객체이고 format="pcmigrate-find-exts", version=1 이어야 한다(더 높은 버전은 거부: 이 도구가 뜻을 모른다).
  - 알려진 키(format, version, tool_version, created, note, added, removed)만 허용한다. 알 수 없는 키는 **거부**한다
    (오타나 다른 도구의 파일을 조용히 무시하면 사용자가 적용된 줄 알기 때문).
  - 확장자는 이미 정규화된 형태(점으로 시작, 소문자, 영문·숫자·_-, 16자 이내)여야 하고, 분류는 design/photo/video/doc/archive 중 하나,
    항목(added+removed)은 500개까지, removed 는 기본 표에 있는 확장자만, added 는 이미 (삭제되지 않은) 기본 확장자와 겹치면 거부한다.
  - 프로그램/시스템 성격 확장자(.exe 등)는 거부하지 않고 경고 목록으로 보여 준다(직접 추가와 같은 규칙).
  - 문제는 줄 번호와 이유를 사용자 문구로 모아 알린다(앞 20건).
적용: 기본은 '교체'(파일의 내용이 새 설정이 된다)이고 mode="merge" 로 현재 설정에 합칠 수도 있다. 적용 전 현재 설정 파일은 번호 .bak 으로 보관한다.
dry_run 이면 검증과 미리보기만 하고 아무것도 쓰지 않는다. 모든 오류는 오류 로그에 남기고, 결과는 항상 ImportResult 로 돌려준다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, errorlog, find_data as fd, find_exts as fx

FORMAT = "pcmigrate-find-exts"
VERSION = 1
ALLOWED_KEYS = frozenset({"format", "version", "tool_version", "created", "note", "added", "removed"})
MAX_BYTES = fx.MAX_BYTES
MAX_ENTRIES = fx.MAX_ENTRIES
MAX_ISSUES = 20
MAX_TEXT = 200  # tool_version/created/note 같은 안내 글의 길이 상한
MAX_DEPTH_NOTE = "너무 깊게 겹친 구조"


@dataclass
class ExportResult:
    ok: bool
    message: str
    path: str = ""
    backup: str = ""
    notes: list = field(default_factory=list)


@dataclass
class ImportResult:
    ok: bool                      # 검증을 통과하고(미리보기 포함) 문제없이 끝났는가
    message: str
    applied: bool = False         # 설정을 실제로 바꿨는가
    dry_run: bool = False
    mode: str = "replace"
    issues: list = field(default_factory=list)    # 거부 이유(사용자 문구, 줄 번호 포함)
    warnings: list = field(default_factory=list)  # 위험 확장자 등(거부 사유 아님)
    summary: dict = field(default_factory=dict)   # 파일 내용과 현재 설정 대비 변화
    backup: str = ""
    cfg: fx.ExtConfig | None = None


# --------------------------------------------------------------------------- 내보내기
def export_dict(cfg: fx.ExtConfig, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    d = cfg.to_json_dict()
    return {"format": FORMAT, "version": VERSION, "tool_version": __version__,
            "created": now.isoformat(timespec="seconds"), "added": d["added"], "removed": d["removed"]}


def export_to(path: str | os.PathLike, directory: str | os.PathLike | None = None, now: datetime | None = None) -> ExportResult:
    """현재 설정을 path 에 JSON 으로 쓴다. 같은 이름 파일은 번호 .bak 으로 보관한다. 실패해도 예외 없이 ExportResult(ok=False)."""
    p = Path(path)
    notes: list[str] = []
    try:
        if p.is_dir():
            return ExportResult(False, "내보낼 위치가 폴더입니다. 파일 이름까지 적어 주세요(예: 형식설정.json).")
        if _same_file(p, fx.config_path(directory)):
            return ExportResult(False, "설정 파일 자체에는 내보낼 수 없습니다. 다른 위치나 이름을 골라 주세요.")
        cfg = fx.load(directory)
        notes += cfg.notes
        text = json.dumps(export_dict(cfg, now), ensure_ascii=False, indent=2) + "\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            backup = ""
            if p.exists():
                bak = fx._numbered_bak(p)
                bak.write_bytes(p.read_bytes())  # 복사: 교체가 실패해도 원래 파일이 남는다
                backup = errorlog.tokenize_home(str(bak))
            os.replace(tmp, p)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
    except OSError as e:
        errorlog.record(e, "찾을 파일 형식 설정: 내보내기")
        return ExportResult(False, "설정을 내보내지 못했습니다. 저장 위치에 쓸 수 있는지 확인해 주세요(원래 파일은 그대로입니다).", notes=notes)
    msg = f"설정을 내보냈습니다(추가 {len(cfg.added)}개, 삭제 {len(cfg.removed)}개)."
    if backup:
        msg += " 같은 이름의 파일은 보관했습니다."
    return ExportResult(True, msg, errorlog.tokenize_home(str(p)), backup, notes)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


# --------------------------------------------------------------------------- 검증
class _Issues:
    def __init__(self, text: str) -> None:
        self.text, self.items, self.total = text, [], 0

    def line_of(self, needle: str) -> int:
        """needle 이 따옴표로 처음 나오는 줄 번호(없으면 0). 설명용이라 정확한 위치가 아닐 수 있다."""
        if not needle:
            return 0
        key = json.dumps(needle, ensure_ascii=False)
        for i, line in enumerate(self.text.splitlines(), 1):
            if key in line:
                return i
        return 0

    def add(self, where: str, why: str, needle: str = "") -> None:
        self.total += 1
        if len(self.items) < MAX_ISSUES:
            n = self.line_of(needle)
            self.items.append(f"{n}번째 줄 {where}: {why}" if n else f"{where}: {why}")

    def lines(self) -> list[str]:
        out = list(self.items)
        if self.total > len(self.items):
            out.append(f"…그 밖에 {self.total - len(self.items)}건의 문제가 더 있습니다.")
        return out


def _short(v: object) -> str:
    s = v if isinstance(v, str) else repr(v)
    return repr(s[:20] + ("…" if len(s) > 20 else ""))


def _read_text(p: Path, issues: list[str]) -> str | None:
    """크기 상한 안에서 UTF-8 로 읽는다. 못 읽으면 issues 에 이유를 넣고 None."""
    try:
        if not p.exists():
            issues.append("파일을 찾을 수 없습니다. 경로를 확인해 주세요.")
            return None
        if not p.is_file():
            issues.append("파일이 아닙니다(폴더이거나 특수 파일입니다). 내보낸 설정 파일(.json)을 골라 주세요.")
            return None
        size = p.stat().st_size
        if size > MAX_BYTES:
            issues.append(f"파일이 너무 큽니다({size:,}바이트, 최대 {MAX_BYTES:,}바이트). 이 도구가 내보낸 설정 파일이 아닐 수 있습니다.")
            return None
        with open(p, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError as e:
        errorlog.record(e, "찾을 파일 형식 설정: 가져오기(파일 읽기)")
        issues.append("파일을 읽을 수 없습니다. 다른 프로그램이 쓰고 있거나 읽을 권한이 없을 수 있습니다.")
        return None
    if len(data) > MAX_BYTES:
        issues.append("파일이 너무 큽니다. 이 도구가 내보낸 설정 파일이 아닐 수 있습니다.")
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        issues.append("글자 모양(UTF-8)이 올바르지 않습니다. 이 도구가 내보낸 설정 파일이 아닐 수 있습니다.")
        return None


def _parse(text: str, issues: _Issues) -> object:
    dups: list[str] = []

    def hook(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                dups.append(k)
            d[k] = v
        return d

    def bad_const(name):
        raise ValueError(f"허용하지 않는 값({name})")

    try:
        data = json.loads(text, object_pairs_hook=hook, parse_constant=bad_const)
    except RecursionError:
        issues.add("전체", f"{MAX_DEPTH_NOTE}라 읽을 수 없습니다.")
        return None
    except ValueError as e:
        line = getattr(e, "lineno", 0)
        why = str(e) if "허용하지 않는 값" in str(e) else "JSON 형식이 올바르지 않습니다(쉼표·따옴표·괄호를 확인해 주세요)"
        issues.total += 1
        issues.items.append(f"{line}번째 줄: {why}" if line else why)
        return None
    for k in dups:
        issues.add("키", f"{_short(k)} 가 두 번 이상 있습니다(어느 쪽이 맞는지 알 수 없어 거부합니다).", k)
    return data


def validate_text(text: str) -> tuple[fx.ExtConfig | None, dict, list[str]]:
    """JSON 글을 엄격하게 검증한다. (설정, 파일 정보, 문제 목록). 문제가 하나라도 있으면 설정은 None."""
    iss = _Issues(text)
    data = _parse(text, iss)
    meta: dict = {}
    if data is None and iss.total:
        return None, meta, iss.lines()
    if not isinstance(data, dict):
        iss.add("전체", "맨 바깥이 { } 형태가 아닙니다.")
        return None, meta, iss.lines()
    for k in data:
        if k not in ALLOWED_KEYS:
            iss.add("키", f"알 수 없는 항목 {_short(k)} 이(가) 있습니다(허용: {', '.join(sorted(ALLOWED_KEYS))}).", k)
    if data.get("format") != FORMAT:
        iss.add("format", f"이 도구의 형식 설정 파일이 아닙니다(format 이 {FORMAT!r} 이어야 합니다).", "format")
    ver = data.get("version")
    if isinstance(ver, bool) or not isinstance(ver, int):
        iss.add("version", "버전이 숫자가 아닙니다.", "version")
    elif ver > VERSION:
        iss.add("version", f"더 새로운 버전({ver})의 파일입니다. 이 도구는 버전 {VERSION}까지만 읽습니다. 도구를 새로 받아 주세요.", "version")
    elif ver < 1:
        iss.add("version", "버전이 올바르지 않습니다.", "version")
    for k in ("tool_version", "created", "note"):
        if k in data:
            v = data[k]
            if not isinstance(v, str) or len(v) > MAX_TEXT:
                iss.add(k, f"글자가 아니거나 너무 깁니다(최대 {MAX_TEXT}자).", k)
            else:
                meta[k] = v
    r_added, r_removed = data.get("added", {}), data.get("removed", [])
    if not isinstance(r_added, dict):
        iss.add("added", "{ } 형태(확장자: 분류)여야 합니다.", "added")
        r_added = {}
    if not isinstance(r_removed, list):
        iss.add("removed", "[ ] 형태(확장자 목록)여야 합니다.", "removed")
        r_removed = []
    if len(r_added) + len(r_removed) > MAX_ENTRIES:
        iss.add("전체", f"항목이 너무 많습니다({len(r_added) + len(r_removed)}개, 최대 {MAX_ENTRIES}개).")
        return None, meta, iss.lines()
    added: dict[str, str] = {}
    removed: list[str] = []
    for item in r_removed:
        e = _check_ext(item, "removed", iss)
        if e is None:
            continue
        if e not in fd.EXT_TABLE:
            iss.add("removed", f"{e} 은(는) 기본 형식이 아니라 삭제할 수 없습니다.", e)
        elif e in removed:
            iss.add("removed", f"{e} 이(가) 두 번 적혀 있습니다.", e)
        else:
            removed.append(e)
    for k, v in r_added.items():
        e = _check_ext(k, "added", iss)
        if e is None:
            continue
        if not isinstance(v, str) or v not in fx.EXT_CATEGORIES:
            iss.add("added", f"{e} 의 분류 {_short(v)} 은(는) 사용할 수 없습니다(design, photo, video, doc, archive, misc 중 하나{'. 기타는 misc 로 적어 주세요' if v == 'other' else ''}).", e)
            continue
        if e in fd.EXT_TABLE and e not in removed:
            iss.add("added", f"{e} 은(는) 이미 기본 형식입니다(중복).", e)
        elif e in fd.EXT_TABLE and fd.EXT_TABLE[e][0] == v:
            iss.add("added", f"{e} 은(는) 삭제 목록에도 있고 같은 분류로도 추가돼 서로 충돌합니다.", e)
        else:
            added[e] = v
    if iss.total:
        return None, meta, iss.lines()
    return fx.ExtConfig(added, sorted(removed)), meta, []


def _check_ext(value: object, where: str, iss: _Issues) -> str | None:
    if not isinstance(value, str):
        iss.add(where, f"확장자가 글자가 아닙니다({_short(value)}).")
        return None
    try:
        e = fx.normalize_ext(value)
    except ValueError as ex:
        iss.add(where, f"{_short(value)}: {ex}", value)
        return None
    if e != value:
        iss.add(where, f"{_short(value)} 은(는) 점으로 시작하는 소문자로 적어야 합니다(예: {e}).", value)
        return None
    return e


# --------------------------------------------------------------------------- 가져오기
def _merge(cur: fx.ExtConfig, new: fx.ExtConfig) -> fx.ExtConfig | None:
    added = {**cur.added, **new.added}
    removed = sorted(set(cur.removed) | set(new.removed))
    # 합친 결과에서 '삭제 + 같은 분류로 추가' 충돌이나 기본과 같은 추가는 정리한다
    for e in list(added):
        if e in fd.EXT_TABLE and (e not in removed or fd.EXT_TABLE[e][0] == added[e]):
            if e in removed:
                removed.remove(e)
            del added[e]
    if len(added) + len(removed) > MAX_ENTRIES:
        return None
    return fx.ExtConfig(added, removed)


def _summary(cur: fx.ExtConfig, new: fx.ExtConfig, file_cfg: fx.ExtConfig) -> dict:
    return {"file_added": len(file_cfg.added), "file_removed": len(file_cfg.removed),
            "result_added": len(new.added), "result_removed": len(new.removed),
            "new_added": len(set(new.added.items()) - set(cur.added.items())) + len(set(cur.removed) - set(new.removed)),
            "new_removed": len(set(new.removed) - set(cur.removed)) + len(set(cur.added) - set(new.added)),
            "same_as_current": cur.added == new.added and sorted(cur.removed) == sorted(new.removed)}


def import_from(path: str | os.PathLike, dry_run: bool = False, mode: str = "replace",
                directory: str | os.PathLike | None = None) -> ImportResult:
    """JSON 파일을 검증하고(문제가 있으면 아무것도 바꾸지 않고 거부) 적용한다. dry_run 이면 미리보기만. 예외를 던지지 않는다."""
    if mode not in ("replace", "merge"):
        return ImportResult(False, "가져오기 방식은 교체(replace) 또는 합치기(merge)여야 합니다.", mode=mode, dry_run=dry_run)
    try:
        return _import(Path(path), dry_run, mode, directory)
    except Exception as e:  # noqa: BLE001 - 검증 중 예기치 못한 오류도 기존 설정은 그대로 두고 알린다
        errorlog.record(e, "찾을 파일 형식 설정: 가져오기")
        return ImportResult(False, "가져오는 중 예기치 못한 오류가 났습니다. 기존 설정은 그대로입니다. 자세한 내용은 오류 기록에 남았습니다.",
                            mode=mode, dry_run=dry_run)


def _reject(issues: list[str], mode: str, dry_run: bool) -> ImportResult:
    errorlog.record(ValueError("찾을 파일 형식 가져오기 거부: " + " / ".join(issues[:5])), "찾을 파일 형식 설정: 가져오기 검증")
    return ImportResult(False, "이 파일은 가져올 수 없습니다. 아무것도 바꾸지 않았고 기존 설정은 그대로입니다.", False, dry_run, mode, issues)


def _import(p: Path, dry_run: bool, mode: str, directory) -> ImportResult:
    pre: list[str] = []
    text = _read_text(p, pre)
    if text is None:
        return _reject(pre, mode, dry_run)
    file_cfg, _meta, issues = validate_text(text)
    if file_cfg is None:
        return _reject(issues, mode, dry_run)
    cur = fx.load(directory)
    new = file_cfg if mode == "replace" else _merge(cur, file_cfg)
    if new is None:
        return _reject([f"현재 설정과 합치면 항목이 {MAX_ENTRIES}개를 넘습니다. 교체로 가져오거나 먼저 정리해 주세요."], mode, dry_run)
    warns = []
    for e in sorted(file_cfg.added):
        for w in fx.warnings_for(e):
            warns.append(f"{e}: {w}")
    warns = warns[:MAX_ISSUES]
    summ = _summary(cur, new, file_cfg)
    res = ImportResult(True, "", False, dry_run, mode, [], warns, summ, "", new)
    how = "교체" if mode == "replace" else "합치기"
    if summ["same_as_current"]:
        res.message = "파일의 내용이 현재 설정과 같아 바꿀 것이 없습니다."
        return res
    desc = f"{how}: 추가 형식 {summ['result_added']}개, 삭제한 기본 형식 {summ['result_removed']}개가 됩니다(현재 설정 대비 추가 {summ['new_added']}개, 삭제 {summ['new_removed']}개 변화)."
    if dry_run:
        res.message = "검증을 통과했습니다. 미리보기만 했고 아무것도 바꾸지 않았습니다. " + desc
        return res
    backup = ""
    try:
        path = fx.config_path(directory)
        if path.exists():  # 적용 전 현재 설정을 번호 .bak 으로 보관한다
            bak = fx._numbered_bak(path)
            bak.write_bytes(path.read_bytes())
            backup = errorlog.tokenize_home(str(bak))
        fx.save(new, directory)
    except fx.FindExtsError as e:
        return ImportResult(False, f"{e} 설정은 바뀌지 않았습니다.", False, dry_run, mode, [], warns, summ, backup)
    except OSError as e:
        errorlog.record(e, "찾을 파일 형식 설정: 가져오기(보관/저장)")
        return ImportResult(False, "현재 설정을 보관하거나 저장하지 못해 가져오지 않았습니다. 기존 설정은 그대로입니다.", False, dry_run, mode, [], warns, summ, backup)
    res.applied, res.backup = True, backup
    res.message = f"가져왔습니다. {desc}" + (" 이전 설정은 보관했습니다." if backup else "")
    return res
