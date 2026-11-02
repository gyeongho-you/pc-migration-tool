"""가져오기 계획(plan_import).

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from . import coverage
from .checklist_data import common_warning_pairs
from . import failure, fs
from .copier import DENIED, FAILED, REJECTED, SKIPPED_SAME, WOULD_COPY, WOULD_OVERWRITE, _backup_path, sha256_file
from .detect import Program
from .discover_data import EXTRA_ROOTS, ROOTS
from .envpaths import expand_env
from .rules import Rule
from .package_base import EXTRA_ID, Expand, INSTALL_ID, LOCK_NAMES, NOT_SELECTED, NOT_SELECTED_TEXT, PackageError, RESTORE_EXCLUDE, _excluded, _usable_base, _within, split_rule_path
from .package_deny import rel_matches_rule, restore_denied
from .package_savepoint import is_unfinished_part
from .package_verify import verify_package


GIT_CONFIG_WARNING = ("Git 설정 파일은 명령 실행 설정(core.sshCommand, alias 의 !명령 등)이 들어 있을 수 있어 "
                      "복원 전에 내용을 확인하세요")


def is_git_config_name(name: str) -> bool:
    """.gitconfig 와 .gitconfig 로 시작하는 이름(.gitconfig-work 등). Git 이 읽어 명령을 실행할 수 있는 설정 파일이다.
    (.config/git/config 는 복원 거부 목록이라 이 경고 대상이 되기 전에 거부되지만, 이름 판정에는 포함한다.)"""
    low = name.casefold().replace("\\", "/")
    return low.rsplit("/", 1)[-1].startswith(".gitconfig") or low.endswith(".config/git/config")


def git_config_targets(items: list[dict]) -> list[str]:
    """복원 계획에서 실제로 복원될(거부/링크 거부가 아닌) Git 설정 파일의 대상 경로."""
    return [it["target"] for it in items if it.get("status") not in (DENIED, REJECTED, NOT_SELECTED) and is_git_config_name(str(it["target"]))]


INSTALL_KEY_PREFIX = INSTALL_ID + ":"


def install_program_key(program_name: str) -> str:
    """설치 폴더 설정 항목의 프로그램 단위 식별자('install:<프로그램 이름>'). 프로그램 단위로 선택/제외할 수 있다."""
    return INSTALL_KEY_PREFIX + (program_name or "?")


def program_key(f: dict) -> str:
    """manifest 파일 항목의 프로그램 단위 식별자. 규칙은 규칙 id, 규칙 없는 항목(extra)은 'extra:<루트키>/<최상위 이름>'."""
    if f.get("rule") != EXTRA_ID:
        return str(f.get("rule"))
    keys = {r.template: r.key for r in ROOTS}
    top = str(f.get("rel", "")).split("/")[0]
    return f"{EXTRA_ID}:{keys.get(f.get('base'), '?')}/{top}"


def program_matches(token: str, key: str) -> bool:
    """--only/--skip 의 항목 하나(token)가 프로그램 식별자(key)에 해당하는지. 대소문자 무시.
    규칙 id 는 정확히 일치. extra 는 'extra'(전부), 'extra:<루트키>/<이름>'(정확히), 'extra:<이름>'(루트 무관)으로 고를 수 있다."""
    t, k = token.strip().casefold(), key.casefold()
    if t == k:
        return True
    if k.startswith(EXTRA_ID + ":"):
        return t == EXTRA_ID or t == f"{EXTRA_ID}:{k.split('/', 1)[1]}"
    if k.startswith(INSTALL_KEY_PREFIX):  # 설치 폴더 설정: 'install'(전부) 또는 'install:<프로그램 이름>'
        return t == INSTALL_ID
    return False


def _norm_tokens(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    return [t.strip() for t in v if isinstance(t, str) and t.strip()]


@dataclass
class ImportPlan:
    manifest: dict
    items: list[dict] = field(default_factory=list)  # {entry, target, status, backup, program}
    problems: list[str] = field(default_factory=list)
    # 프로그램별 요약(화면의 프로그램별 체크용): {id, name, kind, selected, files, bytes, new, overwrite, same, denied, rejected, not_selected}
    programs: list[dict] = field(default_factory=list)
    unknown_programs: list[str] = field(default_factory=list)  # only/skip 에 적었지만 묶음에 없는 항목
    # 설치 폴더 설정(rule='install'): install 은 보기용 전체 목록(상태: would_copy/…/denied/rejected/missing_dir/missing_program).
    # 실제로 쓰는 항목은 install_allowed=True 일 때만 items 에 들어간다(기본은 복원 안 함).
    install: list[dict] = field(default_factory=list)
    install_allowed: bool = False
    install_programs: list[dict] = field(default_factory=list)  # {program, root, folder, old_version, new_version, files}

    @property
    def warnings(self) -> list[dict]:
        w = list(self.manifest.get("manual_warnings", []))
        for name, items in common_warning_pairs(self.manifest):  # 규칙 공통 한 줄(구 PC 경로 안내 등)
            w.append({"rule": "common", "name": name, "items": items})
        return w

    @property
    def git_config_warning(self) -> str:
        """복원할 항목에 Git 설정 파일(.gitconfig)이 있으면 한 줄 경고, 없으면 빈 문자열."""
        return GIT_CONFIG_WARNING if git_config_targets(self.items) else ""


def plan_import(path: Path, rules: list[Rule], expand: Expand = expand_env, *,
                only_programs: list[str] | str | None = None, skip_programs: list[str] | str | None = None,
                allow_install_dir: bool = False, programs: list[Program] | Callable[[], list[Program]] | None = None,
                write_check: Callable[[Path], bool] | None = None) -> ImportPlan:
    """검증 후 복원 계획을 만든다. 파일 시스템은 읽기만 한다.

    allow_install_dir: 설치 폴더 설정(rule='install')도 복원할지. 기본(False)은 복원 안 함이며 plan.install 에 보기용으로만 남는다.
    programs: 이 PC(신 PC)의 설치 프로그램 목록(없으면 None: 프로그램 확인을 건너뛰고 폴더 존재만 본다).
    write_check(dir): 설치 폴더에 쓸 수 있는지(권한 사전 점검). 쓸 수 없으면 plan.problems 에 안내를 넣어 부분 쓰기 없이 중단한다.

    검증 단계가 놓친 구조 오류로 예외가 나더라도 traceback 대신 PackageError('패키지 검증 실패: …')로 알린다.

    only_programs/skip_programs: 프로그램(규칙 id 또는 extra 항목) 단위 복원 선택. only 가 있으면 그것만, skip 은 그것만 뺀다(둘 다면 only 후 skip).
    선택에서 빠진 프로그램의 파일은 대상을 읽지도 쓰지도 않고 status='not_selected' 로 기록한다. 묶음에 없는 이름은 plan.unknown_programs 에 남긴다.
    검증/보안 판정(허용 범위, 해시, 링크)은 선택과 무관하게 모든 항목에 똑같이 적용한다.
    """
    try:
        return _plan_import(path, rules, expand, _norm_tokens(only_programs), _norm_tokens(skip_programs),
                            allow_install_dir, programs, write_check)
    except PackageError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError) as e:
        raise PackageError(f"패키지 검증 실패: manifest 구조가 올바르지 않습니다({type(e).__name__})") from e


def _rejected_item(f: dict, target: Path, link: str, is_extra: bool, base_path: Path) -> dict:
    return {"entry": f["arc"], "target": str(target), "status": REJECTED, "backup": "", "size": f["size"],
            "base_dir": str(base_path),
            "sha256": f["sha256"], "extra": is_extra, "reason": failure.LINK_IN_PATH,
            "error": f"{failure.REASONS[failure.LINK_IN_PATH].text} ({link})"}


def _denied_item(f: dict, target: Path, why: str, is_extra: bool, base_path: Path, kind: str = "") -> dict:
    d = {"entry": f["arc"], "target": str(target), "status": DENIED, "backup": "", "size": f["size"],
         "base_dir": str(base_path), "sha256": f["sha256"], "extra": is_extra, "reason": DENIED,
         "error": f"복원 안 함: {why}"}
    if kind:  # 보안 거부(빈 값)와 구분: 'env'(복원 위치를 알 수 없음) / 'excluded'(규칙 제외 대상). 패키지 전체는 막지 않는다(H1)
        d["skip_kind"] = kind
    return d


ENV_UNKNOWN_HINT = "pcmigrate --env 이름=값 import … 처럼 환경변수를 지정하면 복원할 수 있습니다"


def _skipped_item(f: dict, target: Path, why: str, kind: str, is_extra: bool, base_path: Path, pkey: str, selected: bool) -> dict:
    """패키지 전체를 막지 않고 이 항목만 '복원 안 함'으로 남긴다(선택에서 뺀 프로그램이면 '선택 제외'로 남긴다)."""
    if not selected:
        return _not_selected_item(f, target, is_extra, base_path, pkey)
    return _denied_item(f, target, why, is_extra, base_path, kind) | {"program": pkey}


def _not_selected_item(f: dict, target: Path, is_extra: bool, base_path: Path, pkey: str) -> dict:
    return {"entry": f["arc"], "target": str(target), "status": NOT_SELECTED, "backup": "", "size": f["size"],
            "base_dir": str(base_path), "sha256": f["sha256"], "extra": is_extra, "program": pkey, "reason": NOT_SELECTED,
            "error": NOT_SELECTED_TEXT}


def _fill_programs(plan: ImportPlan, by_id: dict, only: list[str], skip: list[str]) -> None:
    """plan.items 에서 프로그램별 표(plan.programs)를 만들고, 묶음에 없는 only/skip 항목을 plan.unknown_programs 에 모은다."""
    order: list[str] = []
    rows: dict[str, dict] = {}
    for it in plan.items:
        k = it["program"]
        if k not in rows:
            order.append(k)
            if k.startswith(EXTRA_ID + ":"):
                name, kind = f"{coverage.EXTRA_PROGRAM}: {k.split('/', 1)[1]}", "extra"
            elif k.startswith(INSTALL_KEY_PREFIX):
                name, kind = f"설치 폴더 설정: {k[len(INSTALL_KEY_PREFIX):]}", "install"
            else:
                name, kind = (by_id[k].name if k in by_id else k), "rule"
            rows[k] = {"id": k, "name": name, "kind": kind, "selected": True, "files": 0, "bytes": 0, "new": 0, "overwrite": 0,
                       "same": 0, "denied": 0, "rejected": 0, "not_selected": 0}
        r = rows[k]
        r["files"] += 1
        r["bytes"] += it["size"]
        st = it["status"]
        key = {WOULD_COPY: "new", WOULD_OVERWRITE: "overwrite", SKIPPED_SAME: "same", DENIED: "denied", REJECTED: "rejected",
               NOT_SELECTED: "not_selected"}.get(st)
        if key:
            r[key] += 1
        if st == NOT_SELECTED:
            r["selected"] = False
    plan.programs = [rows[k] for k in order]
    ids = list(rows)
    plan.unknown_programs = [t for t in only + skip if not any(program_matches(t, k) for k in ids)]


def _plan_import(path: Path, rules: list[Rule], expand: Expand, only: list[str] | None = None,
                 skip: list[str] | None = None, allow_install_dir: bool = False,
                 programs: list[Program] | Callable[[], list[Program]] | None = None,
                 write_check: Callable[[Path], bool] | None = None) -> ImportPlan:
    only, skip = only or [], skip or []
    return _plan_import_inner(path, rules, expand, only, skip, allow_install_dir, programs, write_check)


def _plan_import_inner(path: Path, rules: list[Rule], expand: Expand, only: list[str], skip: list[str],
                       allow_install_dir: bool = False, programs: list[Program] | Callable[[], list[Program]] | None = None,
                       write_check: Callable[[Path], bool] | None = None) -> ImportPlan:
    if is_unfinished_part(path):  # 만드는 중/중단된 임시 파일: 완성본이 아니다
        raise PackageError("이 파일(.part)은 아직 완성되지 않은 임시 파일입니다. 내보내기를 같은 저장 위치로 다시 실행해 "
                           "끝내거나(이어서 합니다), 완성된 .zip 파일을 선택하세요.")
    manifest, problems = verify_package(path)
    plan = ImportPlan(manifest or {}, problems=list(problems))
    if manifest is None or problems:
        return plan
    by_id = {r.id: r for r in rules}
    seen_targets: set[str] = set()
    allowed = {rid: {split_rule_path(p.path)[0] for p in r.paths if split_rule_path(p.path)}
               for rid, r in by_id.items()}
    install_files: list[dict] = []
    for f in manifest["files"]:
        rid, base = f["rule"], f["base"]
        is_extra = rid == EXTRA_ID
        if rid == INSTALL_ID:  # 설치 폴더 설정은 규칙/extra 의 허용 루트와 섞지 않고 install_dir 의 허용 목록 판정으로만 처리한다
            install_files.append(f)
            continue
        if is_extra:
            if base not in EXTRA_ROOTS:
                plan.problems.append(f"허용되지 않은 복원 루트(extra): {base}")
                continue
            if base == "%USERPROFILE%" and not f["rel"].startswith("."):
                plan.problems.append(f"홈 루트는 점으로 시작하는 항목만 복원할 수 있음: {f['rel']}")
                continue
        elif rid not in by_id:
            plan.problems.append(f"알 수 없는 규칙 id: {rid}")
            continue
        elif base not in allowed[rid]:
            plan.problems.append(f"규칙 {rid} 에 허용되지 않은 복원 위치: {base}")
            continue
        elif not rel_matches_rule(by_id[rid], base, f["rel"]):
            plan.problems.append(f"규칙 {rid} 에 선언된 경로 패턴 밖의 파일(복원 거부): {f['rel']}")
            continue
        base_path = Path(expand(base))
        pkey = program_key(f)
        selected = (not only or any(program_matches(t, pkey) for t in only)) and not any(program_matches(t, pkey) for t in skip)
        target = base_path.joinpath(*PurePosixPath(f["rel"]).parts)
        if not _usable_base(base_path):  # 이 PC 에 없는 환경변수(예: OneDrive): 그 항목만 복원 안 함, 나머지는 진행(H1). 구조 위반이 아니다
            plan.items.append(_skipped_item(f, target, f"이 PC 에서 복원 위치({base})를 알 수 없음(환경변수가 없음). {ENV_UNKNOWN_HINT}",
                                            "env", is_extra, base_path, pkey, selected))
            continue
        link = fs.reparse_in_chain(base_path, target)  # 복원 위치가 링크/정션을 거치면 링크 너머에 쓰게 되므로 따라가지 않는다(F-5)
        if link:
            plan.items.append(_rejected_item(f, target, link, is_extra, base_path) | {"program": pkey} if selected
                              else _not_selected_item(f, target, is_extra, base_path, pkey))
            continue
        if not _within(target, base_path):
            plan.problems.append(f"복원 대상이 허용 루트 밖: {target}")
            continue
        if LOCK_NAMES and _excluded(target.name, None, LOCK_NAMES):
            continue  # 이전 버전이 만든 패키지에 든 잠금 파일: 복원하지 않고 조용히 건너뜀
        if _excluded(target.name, None if is_extra else by_id[rid], RESTORE_EXCLUDE):  # 옛 도구가 담은 라이선스/비밀번호 저장소 등: 그 파일만 복원 안 함(H1)
            plan.items.append(_skipped_item(f, target, "복원 제외 대상(라이선스·비밀번호 저장소 등 규칙이 제외한 파일)",
                                            "excluded", is_extra, base_path, pkey, selected))
            continue
        key = os.path.normcase(str(target))
        if key in seen_targets:  # 대소문자만 다른 경로 등 같은 파일로 가는 두 항목: 뒤 항목이 앞 항목을 .bak 으로 밀어낸다(B-6)
            plan.problems.append(f"같은 대상으로 복원되는 중복 항목: {target}")
            continue
        seen_targets.add(key)
        if not selected:  # 선택에서 뺀 프로그램: 대상 파일은 읽지도 않는다
            plan.items.append(_not_selected_item(f, target, is_extra, base_path, pkey))
            continue
        why = restore_denied(f["rel"], target, None if is_extra else by_id[rid], base)
        if why:  # 패키지 전체를 막지 않고 이 파일만 '복원 안 함'(쓰지 않는다). 나머지는 정상 복원(R1-1)
            plan.items.append(_denied_item(f, target, why, is_extra, base_path) | {"program": pkey})
            continue
        backup = ""
        if target.is_file() and target.stat().st_size == f["size"] and sha256_file(target) == f["sha256"]:
            status = SKIPPED_SAME
        elif target.is_dir():  # 같은 이름의 폴더: 실제 복원도 실패하므로 미리보기도 같은 결과를 보인다(M5). 폴더는 지우지 않는다
            plan.items.append({"entry": f["arc"], "target": str(target), "status": FAILED, "backup": "", "size": f["size"],
                               "sha256": f["sha256"], "extra": is_extra, "base_dir": str(base_path), "program": pkey,
                               "reason": failure.DEST_IS_DIR, "error": failure.REASONS[failure.DEST_IS_DIR].text})
            continue
        elif target.exists():
            status = WOULD_OVERWRITE
            backup = str(_backup_path(target))
        else:
            status = WOULD_COPY
        plan.items.append({"entry": f["arc"], "target": str(target), "status": status,
                           "backup": backup, "size": f["size"], "sha256": f["sha256"],
                           "extra": is_extra, "base_dir": str(base_path), "program": pkey})
    if install_files:
        from . import install_dir
        if callable(programs):  # 신 PC 의 프로그램 목록은 설치 폴더 항목이 있을 때만 읽는다
            try:
                programs = programs()
            except Exception as e:  # 목록을 못 읽으면 프로그램 확인 없이 폴더 존재만 본다(폴더 존재/허용 확장자 확인은 그대로)
                from . import errorlog
                errorlog.record(e, "복원 미리보기: 설치 프로그램 목록 읽기")
                programs = None
        items, view, progs, probs = install_dir.plan_install(
            manifest, install_files, expand, allow=allow_install_dir, programs=programs, write_check=write_check,
            sha256_file=sha256_file)
        plan.install, plan.install_programs, plan.install_allowed = view, progs, bool(allow_install_dir)
        plan.problems += probs
        if not probs:
            for it in items:  # 설치 폴더 항목도 프로그램 단위 선택(only/skip)을 따른다: 'install:<프로그램>' 또는 'install'(전부)
                pk = install_program_key(it.get("program", ""))
                it["program"] = pk
                if (only and not any(program_matches(t, pk) for t in only)) or any(program_matches(t, pk) for t in skip):
                    it.update({"status": NOT_SELECTED, "backup": "", "reason": NOT_SELECTED, "error": NOT_SELECTED_TEXT})
            plan.items += items
    _fill_programs(plan, by_id, only, skip)
    return plan
