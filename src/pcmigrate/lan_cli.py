"""pcmigrate receive / send 명령(LAN 직접 전송). 인자 파싱과 터미널 출력만 담당하고, 동작은 lan_api 에 있다."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import failure
from .cancel import Cancelled
from .lan_api import SendOptions, default_receive_dir, resolve_target, run_diagnose, run_send, start_receiver
from .lan_link import KIND_TEXT
from .lan_protocol import CODE_TTL, DEFAULT_CHUNK, DEFAULT_PORT, LanError
from .progress import fmt_bytes, format_info

SEND_USAGE = "pcmigrate send <주소[:포트]|자동> --code 코드 (--from-selection 선택.json | 원본폴더...)"
EVENT_LABEL = {"peer_connected": "연결", "auth_failed": "인증 실패", "disconnected": "끊김", "cancelled": "취소", "error": "오류", "plan": "목록"}
USAGE_CODES = ("bad_code", "bad_args", "bad_target", "no_sources", "bad_sources", "public_address", "multiple_receivers")


def _err(*lines: str) -> None:
    for line in lines:
        print(failure.cli_text(line), file=sys.stderr, flush=True)


def _port_arg(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"포트 번호(0~65535)를 입력하세요(입력: {text!r})") from None
    if not 0 <= v <= 65535:
        raise argparse.ArgumentTypeError(f"포트 번호는 0~65535 입니다(입력: {v})")
    return v


def add_parsers(sub, path_arg) -> None:
    r = sub.add_parser("receive", help="LAN 직접 전송(새 PC): 짧은 코드로 연결해 구 PC 가 보내는 파일을 받는다(SMB/공유 폴더 설정 불필요)")
    r.add_argument("--dest", type=path_arg, help="받을 폴더(기본: 바탕화면 아래 새 폴더). 시작할 때 만든다")
    r.add_argument("--port", type=_port_arg, default=DEFAULT_PORT, help=f"접속 포트(기본 {DEFAULT_PORT}, 0 이면 아무 빈 포트)")
    r.add_argument("--code", help="숫자 6자리 페어링 코드(기본: 자동 생성)")
    r.add_argument("--bind", default="0.0.0.0", help="이 주소로만 대기(기본 0.0.0.0 = 모든 어댑터). 사설 주소를 지정하면 더 안전")
    r.add_argument("--allow-public", action="store_true", help="공인(인터넷) 주소에 대기/접속하는 것을 명시적으로 허용(암호화가 없어 권장하지 않음)")
    r.add_argument("--no-discovery", action="store_true", help="자동 검색(UDP 응답)을 끈다")
    r.set_defaults(func=cmd_receive)

    s = sub.add_parser("send", help="LAN 직접 전송(구 PC): 새 PC 의 receive 에 코드로 접속해 폴더를 보낸다(원본은 읽기만 함)")
    s.add_argument("target", help="새 PC 주소[:포트] 또는 '자동'(같은 네트워크에서 검색)")
    s.add_argument("sources", nargs="*", type=path_arg, help="보낼 원본 폴더(--from-selection 을 쓰면 적지 않음)")
    s.add_argument("--code", help="새 PC 화면에 표시된 숫자 6자리 코드")
    s.add_argument("--from-selection", type=path_arg, metavar="선택파일", help="find-data 가 만든 선택 파일(include:true 폴더들)을 <드라이브>\\<경로> 구조로 보냄")
    s.add_argument("--exclude-junk", action="store_true", help="Thumbs.db, ~$ 잠금, *.tmp 등 다시 만들어지는 파일을 보내지 않음(transfer 와 같은 목록)")
    s.add_argument("--exclude-backup-copies", action="store_true", help="--exclude-junk 와 함께: Revit 백업 사본 등도 제외")
    s.add_argument("--include-cloud", action="store_true", help="OneDrive 클라우드 전용 파일도 보냄(읽는 순간 내려받아 오래 걸림)")
    s.add_argument("--layout", choices=("auto", "flat", "drive"), default="auto",
                   help="받는 쪽 폴더 구조: auto(폴더 하나면 내용 그대로, 여러 개/선택 파일이면 <드라이브>\\<경로>), flat, drive")
    s.add_argument("--chunk-kb", type=int, default=DEFAULT_CHUNK // 1024, help="한 번에 보내는 조각 크기(KB, 기본 1024, 상대와 협상해 작은 쪽 사용)")
    s.add_argument("--allow-public", action="store_true", help="공인(인터넷) 주소로 보내는 것을 명시적으로 허용(암호화가 없어 권장하지 않음)")
    s.add_argument("--diagnose", action="store_true", help="보내지 않고 연결 문제만 진단(포트 열림/서브넷/코드/방화벽 가능성을 한 줄로)")
    s.add_argument("--report", type=path_arg, help="결과 리포트(JSON) 저장 경로")
    s.add_argument("--json", action="store_true", help="요약 대신 JSON 출력")
    s.add_argument("--discover-seconds", type=float, default=2.0, help="'자동' 검색 대기 시간(초)")
    s.set_defaults(func=cmd_send)


# --------------------------------------------------------------------------- receive
def cmd_receive(args: argparse.Namespace) -> int:
    dest = args.dest or default_receive_dir()
    state = {"last": 0.0}

    def on_event(ev: dict) -> None:
        t = ev["type"]
        if t == "progress":
            info = ev.get("info")
            now = time.monotonic()
            if info is not None and now - state["last"] >= 10:
                state["last"] = now
                _err(f"[진행] {format_info(info)}")
        elif t in EVENT_LABEL:
            _err(f"[{EVENT_LABEL[t]}] {ev.get('message', '')}")

    try:
        rx = start_receiver(dest, args.port, on_event, code=args.code, bind=args.bind, allow_public=args.allow_public,
                            discovery=not args.no_discovery)
    except LanError as e:
        _err(str(e))
        return 2 if e.code in USAGE_CODES else 1
    print("[수신 대기] 구 PC 에서 아래 주소와 코드로 접속하세요.", flush=True)
    print(f"받는 폴더: {rx.dest}")
    print("접속 주소(구 PC 의 pcmigrate send 에 입력):")
    for a in rx.addresses:
        print(f"  {a}   ({KIND_TEXT.get(rx.address_kinds.get(a, ''), '')})")
    print(f"페어링 코드: {rx.code}   ({int(CODE_TTL // 60)}분 안에 연결하세요. 코드를 5번 틀리면 잠깁니다)")
    print(f"구 PC 에서: pcmigrate send 자동 --code {rx.code} --from-selection 선택.json   (자동 검색이 안 되면 위 주소를 직접 적습니다)", flush=True)
    for w in rx.warnings:
        _err(f"[안내] {w}")
    _err("[안내] 끝나면 자동으로 멈춥니다. 중간에 멈추려면 Ctrl+C. 끊겨도 같은 코드로 다시 접속하면 이어받습니다.")
    try:
        while not rx.wait(0.5):
            pass
    except KeyboardInterrupt:
        rx.stop()
        _err("수신을 멈췄습니다. 받던 파일은 임시 파일로 남아 있어 다음에 이어받습니다.")
        return 130
    res = rx.result
    if res is None:
        rx.stop()
        return 1
    print(f"[완료] 파일 {res['files']:,}개({fmt_bytes(res['bytes'])}) 받음 · 이미 있던 같은 파일 {res['skipped_existing']}개 건너뜀 · "
          f"덮어써서 .bak 으로 보관 {res['preserved_existing']}개")
    print(f"해시 목록: {res['manifest']}")
    print(f"다시 검사하려면: pcmigrate verify --manifest \"{res['manifest']}\" --target \"{res['dest']}\"", flush=True)
    if res["failed"] or res["sender_failed"] or res["mismatch"]:
        _err(f"[주의] 받지 못했거나 어긋난 파일이 있습니다(받기 실패 {res['failed']}, 내용 다름 {res['mismatch']}, 송신 쪽 읽기 실패 {res['sender_failed']}). "
             "송신 쪽 리포트를 확인하고 다시 실행하세요.")
        return 1
    return 0


# --------------------------------------------------------------------------- send
def _choose(found):
    if not sys.stdin or not sys.stdin.isatty():
        return None
    for n, r in enumerate(found, 1):
        print(f"  {n}. {r.name}  {r.address}")
    try:
        k = int(input("접속할 번호: ").strip())
        return found[k - 1]
    except (ValueError, IndexError, EOFError):
        return None


def cmd_send(args: argparse.Namespace) -> int:
    if args.diagnose:
        target = args.target
        if target.lower() in ("자동", "auto"):
            try:
                host, port = resolve_target("자동", timeout=args.discover_seconds, choose=_choose)
            except LanError as e:
                _err(str(e))
                return 1
            target = f"{host}:{port}"
        d = run_diagnose(target, args.code)
        if args.json:
            print(json.dumps(d.to_dict(), ensure_ascii=False))
        else:
            print(("[정상] " if d.ok else "[문제] ") + d.summary)
            for line in d.details:
                print("  - " + line)
        return 0 if d.ok else 1
    if not args.code:
        if sys.stdin and sys.stdin.isatty():
            try:
                args.code = input("새 PC 화면의 6자리 코드: ").strip()
            except EOFError:
                args.code = ""
        if not args.code:
            _err(f"--code 가 필요합니다: {SEND_USAGE}")
            return 2
    if bool(args.from_selection) == bool(args.sources):
        _err(f"보낼 곳을 하나만 지정하세요(--from-selection 선택 파일 또는 원본 폴더): {SEND_USAGE}")
        return 2
    if args.exclude_backup_copies and not args.exclude_junk:
        _err("--exclude-backup-copies 는 --exclude-junk 와 함께만 쓸 수 있습니다")
        return 2
    opts = SendOptions(exclude_junk=args.exclude_junk, junk_backup_copies=args.exclude_backup_copies, include_cloud=args.include_cloud,
                       chunk=max(1, args.chunk_kb) * 1024, allow_public=args.allow_public, layout=args.layout)
    state = {"last": 0.0}

    def on_progress(info) -> None:
        now = time.monotonic()
        if now - state["last"] >= 10:
            state["last"] = now
            _err(f"[진행] {format_info(info)}")

    def on_event(ev: dict) -> None:
        if ev["type"] == "connected":
            _err(f"[연결] {ev.get('message', '')}")

    _err("[안내] 이 연결은 암호화되지 않습니다. 믿을 수 있는 사설 LAN(같은 공유기/스위치, 랜선 직결)에서만 쓰세요. 원본은 읽기만 합니다.")
    try:
        rep = run_send(args.target, args.code, selection=args.from_selection, sources=args.sources or None, options=opts,
                       progress_info=on_progress, on_event=on_event, choose=_choose, timeout=args.discover_seconds)
    except Cancelled:
        _err("취소했습니다. 원본은 읽기만 했으므로 바뀌지 않았습니다. 같은 명령을 다시 실행하면 이어서 보냅니다.")
        return 130
    except LanError as e:
        _err(str(e))
        if e.code in ("refused", "timeout", "unreachable"):
            _err("원인 확인: pcmigrate send <주소> --diagnose [--code 코드]")
        return 2 if e.code in USAGE_CODES else 1
    if args.report:
        try:
            Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            _err(f"리포트를 저장하지 못했습니다: {e}")
    if args.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        v = rep["verification"]
        print(f"[{'완료' if rep['success'] else '주의'}] 파일 {rep['files']:,}개({fmt_bytes(rep['bytes'])}) · 보냄 {rep['sent_files']}개, "
              f"이미 있어 건너뜀 {rep['skipped_existing']}개, 이어받음 {rep['resumed_files']}개")
        print(f"검증: 정확함 {v['exact']}개 / 내용 다름 {v['mismatch']}개 / 누락 {v['missing']}개 (소요 {rep['seconds']}초)")
        if rep.get("junk_excluded") and rep["junk_excluded"].get("files"):
            print(f"- 정크 제외: {rep['junk_excluded']['files']}개({fmt_bytes(rep['junk_excluded']['bytes'])}) 보내지 않음")
        if rep["cloud_only_skipped"]:
            print(f"- OneDrive 클라우드 전용 파일 {rep['cloud_only_skipped']}개는 보내지 않았습니다(--include-cloud 로 포함)")
        if rep["skipped_links"]:
            print(f"- 바로가기/정션(링크) {rep['skipped_links']}개는 따라가지 않았습니다")
        for f in rep["failed"][:20]:
            print(f"  ! {f['path']}: {f['error']}")
        if rep["failed_count"] > 20:
            print(f"  ... 외 {rep['failed_count'] - 20}개(--report 파일 참고)")
        print(f"새 PC 의 해시 목록: {rep['receiver'].get('manifest', '')}  (새 PC 에서 pcmigrate verify 로 다시 검사할 수 있습니다)")
    return 0 if rep["success"] else 1
