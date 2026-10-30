"""pcmigrate record 명령(설정 기록 모드). 인자 파싱과 터미널 출력만 담당하고, 동작은 record_mode 에 있다."""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import errorlog, failure, record_mode as rm
from .cancel import Cancelled
from .progress import fmt_bytes, format_info
from .record_snapshot import HASH_LIMIT, MAX_FILES


def _err(*lines: str) -> None:
    for line in lines:
        print(failure.cli_text(errorlog.tokenize_home(line)), file=sys.stderr, flush=True)


def _out(*lines: str) -> None:
    for line in lines:
        print(errorlog.tokenize_home(line))


def _progress():
    state = {"last": None}

    def cb(info) -> None:
        now = time.monotonic()
        if state["last"] is None or now - state["last"] >= 5:
            state["last"] = now
            _err(f"[진행] {format_info(info)}")
    return cb


def _show_snapshot(r: rm.SnapshotResult, work: str) -> None:
    for line in r.root_notes:
        _err(f"[안내] {line}")
    if r.running:
        _err("[경고] 프로그램이 실행 중일 수 있습니다(" + ", ".join(r.running) + "). 먼저 프로그램을 완전히 닫고 다시 찍는 것이 정확합니다(막지는 않습니다).")
    _out(f"스냅샷 '{r.label}' 저장: 파일 {r.files:,}개 (새로 해시 {r.hashed:,}개, 이전 해시 재사용 {r.carried:,}개)")
    if r.unreadable:
        _err(f"[주의] 읽지 못한 {r.unreadable}개는 건너뛰었습니다(크기/시각만 비교). 자세한 내용: pcmigrate logs")
    if r.cloud_skipped:
        _err(f"[주의] OneDrive 클라우드 전용 파일 {r.cloud_skipped}개는 내려받지 않고 건너뛰었습니다.")
    if r.links_skipped:
        _err(f"[안내] 링크/정션 {r.links_skipped}개는 따라가지 않았습니다.")
    for w in r.warnings:
        _err(f"[주의] {w}")
    _out(f"작업 폴더: {work}")


def _run(fn):
    """공통 오류 처리: 사용자 오류는 쉬운 문구, 취소는 130."""
    try:
        return fn()
    except rm.RecordError as e:
        _err(str(e))
        return 1
    except Cancelled:
        _err("취소했습니다. 이 스냅샷은 저장하지 않았습니다(보고서: " + rm.REPORT_FILE + "). 대상 폴더는 읽기만 해서 바뀌지 않았습니다.")
        return 130
    except KeyboardInterrupt:
        _err("중단했습니다. 이 스냅샷은 저장하지 않았습니다. 대상 폴더는 읽기만 해서 바뀌지 않았습니다.")
        return 130


def _cmd_start(args) -> int:
    def go() -> int:
        s, r = rm.start(args.program, args.work, args.roots, args.match, args.all_roots, args.include_caches,
                        int(args.hash_limit_mb * 1024 * 1024), args.max_files, args.force, progress=_progress())
        _show_snapshot(r, errorlog.tokenize_home(str(s.work)))
        _out("다음: 프로그램을 설정 변경 없이 한 번 열었다 닫고 `pcmigrate record snapshot` (기준선)")
        return 0
    return _run(go)


def _cmd_snapshot(args) -> int:
    def go() -> int:
        s = rm.load_session(args.work)
        r = rm.snapshot(s, args.label, progress=_progress())
        _show_snapshot(r, errorlog.tokenize_home(str(s.work)))
        _out(*rm.render_status(s)[-1:])
        return 0
    return _run(go)


def _cmd_diff(args) -> int:
    def go() -> int:
        s = rm.load_session(args.work)
        a = rm.diff(s)
        if args.json:
            print(json.dumps(rm.candidates_dict(a), ensure_ascii=False, indent=1))
        else:
            _out(*rm.render_candidates(a))
        _out(f"후보 저장: {rm.CAND_FILE}")
        return 0
    return _run(go)


def _cmd_draft(args) -> int:
    def go() -> int:
        s = rm.load_session(args.work)
        a, rule, out = rm.write_draft(s, args.output, rm.RANK_ARG[args.min_confidence], args.id)
        _out(*rm.render_candidates(a, 15))
        if rule is None:
            _err("규칙 초안을 만들지 않았습니다. " + ("레지스트리 저장일 가능성이 높음(파일 기반 이전 불가)." if a.registry_likely
                                          else "조건에 맞는 후보가 없습니다(--min-confidence low 로 낮춰 보세요)."))
            return 0
        _out(f"규칙 초안 저장: {errorlog.tokenize_home(str(out))} (paths {len(rule['paths'])}개, exclude {len(rule['exclude'])}개)",
             "확인 후 rules 폴더에 넣고 `pcmigrate check-rules --rules <폴더>` 로 검증하세요. 초안이므로 내용을 꼭 확인하세요.")
        return 0
    return _run(go)


def _cmd_status(args) -> int:
    def go() -> int:
        _out(*rm.render_status(rm.load_session(args.work)))
        return 0
    return _run(go)


def add_parsers(sub, path_arg) -> None:
    r = sub.add_parser("record", help="설정 기록 모드: 규칙 없는 프로그램의 설정을 바꾸기 전/후 파일 변화로 찾아 규칙 초안 만들기(대상은 읽기만 함)")
    rs = r.add_subparsers(dest="record_command", required=True)

    st = rs.add_parser("start", help="기록 시작 + 변경 전 스냅샷(before). 프로그램을 먼저 완전히 닫으세요")
    st.add_argument("--program", required=True, help="프로그램 이름(폴더 이름 부분 일치로 대상을 좁히는 데 씀)")
    st.add_argument("--match", action="append", metavar="낱말", help="폴더 이름에 이 낱말이 들어 있어도 대상에 넣음(여러 번 가능, 예: 제조사 이름)")
    st.add_argument("--roots", nargs="+", type=path_arg, metavar="폴더", help="기본 루트 대신 이 폴더들 전체를 훑음")
    st.add_argument("--all-roots", action="store_true", help="이름으로 좁히지 않고 기본 루트 전체를 훑음(오래 걸릴 수 있음)")
    st.add_argument("--include-caches", action="store_true", help="Temp/Cache/Crashpad/logs 등 알려진 잡음 폴더도 포함")
    st.add_argument("--work", type=path_arg, help="작업 폴더(기본: %%LOCALAPPDATA%%\\pcmigrate\\record\\<이름>)")
    st.add_argument("--hash-limit-mb", type=float, default=HASH_LIMIT / 1024 / 1024, help="이 크기 이하 파일만 해시(기본 20, 0 이면 크기/시각만)")
    st.add_argument("--max-files", type=int, default=MAX_FILES, help=f"스냅샷 파일 수 상한(기본 {MAX_FILES:,})")
    st.add_argument("--force", action="store_true", help="작업 폴더의 기존 기록을 버리고 새로 시작(스냅샷 파일은 덮어씀)")
    st.set_defaults(func=_cmd_start)

    sn = rs.add_parser("snapshot", help="스냅샷 추가: 기준선(baseline) 또는 설정을 바꾼 뒤(after1, after2 ...)")
    sn.add_argument("--label", help="before | baseline | baseline2 | after1 | after2 ... (생략하면 다음 순서를 자동 선택)")
    sn.add_argument("--work", type=path_arg, required=True, help="작업 폴더(start 에서 쓴 것)")
    sn.set_defaults(func=_cmd_snapshot)

    df = rs.add_parser("diff", help="스냅샷을 비교해 설정 파일 후보와 확신도를 보여 줌")
    df.add_argument("--work", type=path_arg, required=True)
    df.add_argument("--json", action="store_true", help="JSON 으로 출력")
    df.set_defaults(func=_cmd_diff)

    dr = rs.add_parser("rule-draft", help="후보로 규칙 JSON 초안 만들기(rules/*.json 스키마)")
    dr.add_argument("--work", type=path_arg, required=True)
    dr.add_argument("--output", type=path_arg, help="초안 저장 경로(기본: 작업 폴더의 rule-draft.json)")
    dr.add_argument("--min-confidence", choices=list(rm.RANK_ARG), default="low", help="paths 에 담을 최소 확신도(기본 low)")
    dr.add_argument("--id", help="규칙 id(기본: 프로그램 이름에서 만듦)")
    dr.set_defaults(func=_cmd_draft)

    ss = rs.add_parser("status", help="진행 상황과 다음 할 일")
    ss.add_argument("--work", type=path_arg, required=True)
    ss.set_defaults(func=_cmd_status)
