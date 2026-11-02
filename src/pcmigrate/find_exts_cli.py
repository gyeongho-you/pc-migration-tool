"""pcmigrate find-exts 명령('내 파일 찾기'가 찾을 파일 형식 설정). 인자 파싱과 출력만 담당하고 동작은 find_exts 에 있다."""
from __future__ import annotations

import argparse
import json
import sys

from . import errorlog, failure, find_exts as fx


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
    d = fx.describe(cfg)
    lines = [f"찾을 파일 형식: 모두 {d['total']}개(사용자 추가 {d['added_count']}개, 기본에서 삭제 {d['removed_count']}개)"]
    for c in d["categories"]:
        lines.append(f"[{c['label']}] {c['count']}개: " + " ".join(("+" if e["origin"] == "user" else "") + e["ext"] for e in c["exts"]))
    if d["removed"]:
        lines.append("삭제한 기본 형식: " + " ".join(f"{r['ext']}({r['label']})" for r in d["removed"]))
    lines.append("(+ 는 사용자가 추가한 형식입니다. 추가한 형식은 파일이 한 개만 있어도 후보 폴더가 됩니다.)")
    return lines


def _settings(cfg: fx.ExtConfig) -> dict:
    return {"path": errorlog.tokenize_home(str(fx.config_path())), **fx.describe(cfg)}


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
    ok = ch.changed or act == "add"
    if args.json:
        _out({"ok": ok, "changed": ch.changed, "message": ch.message, "warnings": warns, "saved_to": saved,
              "settings": _settings(ch.cfg)}, True)
    else:
        _out([ch.message] + ([f"설정 저장: {saved}"] if saved else []), False)
    return 0 if ok else 1


def add_parsers(sub) -> None:
    p = sub.add_parser("find-exts", help="내 파일 찾기가 찾을 파일 형식(확장자)을 보고, 추가하고, 삭제하고, 기본값으로 되돌린다",
                       epilog="기본값은 코드의 확장자 표(설계/사진/영상/문서/압축)입니다. 설정은 기본값과의 차이만 "
                              "%LOCALAPPDATA%\\pcmigrate\\settings\\find-exts.json 에 저장합니다. "
                              "종료 코드: 0 = 성공(이미 있는 형식 추가 안내 포함), 1 = 없는 형식 삭제/저장·초기화 실패, 2 = 잘못된 입력.")
    s = p.add_subparsers(dest="action", required=True)
    for name, text in (("list", "현재 찾을 형식 목록(사용자 추가/삭제 표시)"), ("reset", "기본값으로 되돌림(이전 설정은 번호 .bak 으로 보관)")):
        q = s.add_parser(name, help=text)
        q.add_argument("--json", action="store_true", help="JSON 으로 출력")
        q.set_defaults(func=_cmd)
    a = s.add_parser("add", help="형식 추가(예: add .zep --category design). 바로 저장합니다")
    a.add_argument("ext", help="확장자(예: .zep 또는 zep)")
    a.add_argument("--category", required=True, metavar="분류", help="design(설계), photo(사진), video(영상), doc(문서), archive(압축)")
    a.add_argument("--json", action="store_true", help="JSON 으로 출력")
    a.set_defaults(func=_cmd)
    r = s.add_parser("remove", help="형식 삭제(기본 형식은 삭제 목록에, 추가한 형식은 추가 목록에서 뺌). 바로 저장합니다")
    r.add_argument("ext", help="확장자(예: .svg)")
    r.add_argument("--json", action="store_true", help="JSON 으로 출력")
    r.set_defaults(func=_cmd)
