"""pcmigrate find-exts 명령('내 파일 찾기'가 찾을 파일 형식 설정). 인자 파싱과 출력만 담당하고 동작은 find_exts 에 있다."""
from __future__ import annotations

import argparse
import json
import sys

from . import errorlog, failure, find_exts as fx, find_exts_io as fio


def _err(*lines: str) -> None:
    for line in lines:
        print(failure.cli_text(errorlog.tokenize_home(line)), file=sys.stderr, flush=True)


def _out(obj_or_lines, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj_or_lines, ensure_ascii=False, indent=2))
    else:
        for line in obj_or_lines:
            print(errorlog.tokenize_home(line))


def _list_lines(cfg: fx.ExtConfig) -> list[str]:
    d = fx.describe(cfg, show_empty_misc=True)
    lines = [f"찾을 파일 형식: 모두 {d['total']}개(사용자 추가 {d['added_count']}개, 기본에서 삭제 {d['removed_count']}개)"]
    for c in d["categories"]:
        lines.append(f"[{c['label']}] {c['count']}개: " + " ".join(("+" if e["origin"] == "user" else "") + e["ext"] for e in c["exts"]))
    if d["removed"]:
        lines.append("삭제한 기본 형식: " + " ".join(f"{r['ext']}({r['label']})" for r in d["removed"]))
    lines.append("(+ 는 사용자가 추가한 형식입니다. 추가한 형식은 파일이 한 개만 있어도 후보 폴더가 됩니다.)")
    return lines


def _settings(cfg: fx.ExtConfig) -> dict:
    return {"path": errorlog.tokenize_home(str(fx.config_path())), **fx.describe(cfg, show_empty_misc=True)}


def _cmd_export(args: argparse.Namespace) -> int:
    r = fio.export_to(args.file)
    for n in r.notes:
        _err(f"[경고] {n}")
    if args.json:
        _out({"ok": r.ok, "message": r.message, "path": r.path, "backup": r.backup}, True)
    else:
        _out([r.message] + ([f"파일: {r.path}"] if r.ok else []) + ([f"보관한 이전 파일: {r.backup}"] if r.backup else []), False)
    return 0 if r.ok else 1


def _ask_apply() -> bool:
    try:
        return input("이 내용으로 설정을 바꿀까요? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _cmd_import(args: argparse.Namespace) -> int:
    mode = "merge" if args.merge else "replace"
    r = fio.import_from(args.file, dry_run=True, mode=mode)  # 먼저 검증과 미리보기(아무것도 바꾸지 않음)
    applying = r.ok and not args.dry_run and not r.summary.get("same_as_current", False)
    asked = ""
    if applying and not args.yes:
        interactive = sys.stdin is not None and sys.stdin.isatty() and not args.json
        if not interactive:
            applying, asked = False, "적용하려면 --yes 를 붙여 다시 실행하세요(지금은 미리보기만 했습니다)."
        else:
            _out([r.message] + [f"[경고] {w}" for w in r.warnings], False)
            if not _ask_apply():
                applying, asked = False, "적용하지 않았습니다."
    if applying:
        r = fio.import_from(args.file, dry_run=False, mode=mode)
    if args.json:
        _out({"ok": r.ok, "applied": r.applied, "dry_run": not r.applied, "mode": r.mode, "message": r.message, "issues": r.issues,
              "warnings": r.warnings, "summary": r.summary, "backup": r.backup, "note": asked}, True)
    else:
        for i in r.issues:
            _err(f"[문제] {i}")
        for w in r.warnings:
            _err(f"[경고] {w}")
        _out([r.message] + ([asked] if asked else []) + ([f"보관한 이전 설정: {r.backup}"] if r.backup else []), False)
    return 0 if r.ok else 1


def _cmd(args: argparse.Namespace) -> int:
    act = args.action
    cfg = fx.load()
    for n in cfg.notes:
        _err(f"[경고] {n}")
    if act == "list":
        _out(_settings(cfg) if args.json else _list_lines(cfg), args.json)
        return 0
    if act == "reset":
        r = fx.reset()
        if args.json:
            _out({"ok": r.ok, "changed": r.changed, "message": r.message, "backup": r.backup,
                  "settings": _settings(fx.load())}, True)
        else:
            _out([r.message] + ([f"보관한 이전 설정: {r.backup}"] if r.backup else []), False)
        return 0 if r.ok else 1
    try:
        ch = fx.add(cfg, args.ext, args.category) if act == "add" else fx.remove(cfg, args.ext)
    except fx.FindExtsError as e:
        _err(str(e))
        return 2
    warns = list(ch.warnings)
    saved = ""
    if ch.changed:
        try:
            fx.save(ch.cfg)
        except fx.FindExtsError as e:
            _err(str(e))
            return 1
        saved = errorlog.tokenize_home(str(fx.config_path()))
    for w in warns:
        _err(f"[경고] {w}")
    # 없는 확장자 삭제는 실패(1), 이미 있는 확장자 추가는 안내(0)
    ok = ch.changed or (act == "add" and not ch.conflict)  # 이미 같은 분류로 있으면 안내(0), 다른 분류로 이미 있어 적용되지 않았으면 실패(1)
    if args.json:
        _out({"ok": ok, "changed": ch.changed, "message": ch.message, "warnings": warns, "saved_to": saved,
              "settings": _settings(ch.cfg)}, True)
    else:
        _out([ch.message] + ([f"설정 저장: {saved}"] if saved else []), False)
    return 0 if ok else 1


def add_parsers(sub, path_arg=None) -> None:
    p = sub.add_parser("find-exts", help="내 파일 찾기가 찾을 파일 형식(확장자)을 보고, 추가하고, 삭제하고, 기본값으로 되돌린다",
                       epilog="기본값은 코드의 확장자 표(설계/사진/영상/문서/압축)입니다. 설정은 기본값과의 차이만 "
                              "%LOCALAPPDATA%\\pcmigrate\\settings\\find-exts.json 에 저장합니다. "
                              "종료 코드: 0 = 성공(이미 있는 형식 추가 안내 포함), 1 = 없는 형식 삭제·다른 분류로 이미 있는 형식 추가(적용 안 됨)/저장·초기화 실패/가져오기 거부, 2 = 잘못된 입력.")
    s = p.add_subparsers(dest="action", required=True)
    for name, text in (("list", "현재 찾을 형식 목록(사용자 추가/삭제 표시)"), ("reset", "기본값으로 되돌림(이전 설정은 번호 .bak 으로 보관)")):
        q = s.add_parser(name, help=text)
        q.add_argument("--json", action="store_true", help="JSON 으로 출력")
        q.set_defaults(func=_cmd)
    a = s.add_parser("add", help="형식 추가(예: add .zep --category design). 바로 저장합니다")
    a.add_argument("ext", help="확장자(예: .zep 또는 zep)")
    a.add_argument("--category", required=True, metavar="분류", help="design(설계), photo(사진), video(영상), doc(문서), archive(압축), other(기타: 위에 해당하지 않는 형식). 한글도 됩니다")
    a.add_argument("--json", action="store_true", help="JSON 으로 출력")
    a.set_defaults(func=_cmd)
    e = s.add_parser("export", help="현재 설정을 JSON 파일로 내보냄(같은 이름 파일은 번호 보관본으로 남김)")
    e.add_argument("file", type=path_arg or str, help="저장할 파일(예: 형식설정.json)")
    e.add_argument("--json", action="store_true", help="JSON 으로 출력")
    e.set_defaults(func=_cmd_export)
    im = s.add_parser("import", help="내보낸 JSON 파일을 먼저 검증한 뒤 적용(이상하면 거부하고 기존 설정은 그대로)",
                      epilog="기본은 교체(파일의 내용이 새 설정)이고 --merge 는 현재 설정에 합칩니다. 적용 전 현재 설정은 번호 보관본으로 남깁니다. "
                             "알 수 없는 항목·미래 버전·500개 초과·잘못된 확장자/분류가 하나라도 있으면 아무것도 적용하지 않습니다. "
                             "종료 코드: 0 = 성공(미리보기·바꿀 것 없음 포함), 1 = 거부/실패, 2 = 잘못된 인자.")
    im.add_argument("file", type=path_arg or str, help="가져올 JSON 파일(find-exts export 가 만든 것)")
    im.add_argument("--dry-run", action="store_true", help="검증과 미리보기만 하고 아무것도 바꾸지 않음")
    im.add_argument("--yes", action="store_true", help="확인 질문 없이 적용(없으면 터미널에서는 물어보고, 터미널이 아니면 미리보기만 함)")
    im.add_argument("--merge", action="store_true", help="교체 대신 현재 설정에 합침(같은 확장자는 파일 쪽이 이김)")
    im.add_argument("--json", action="store_true", help="JSON 으로 출력(질문하지 않으므로 적용하려면 --yes)")
    im.set_defaults(func=_cmd_import)
    r = s.add_parser("remove", help="형식 삭제(기본 형식은 삭제 목록에, 추가한 형식은 추가 목록에서 뺌). 바로 저장합니다")
    r.add_argument("ext", help="확장자(예: .svg)")
    r.add_argument("--json", action="store_true", help="JSON 으로 출력")
    r.set_defaults(func=_cmd)
