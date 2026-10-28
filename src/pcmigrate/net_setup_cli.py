"""pcmigrate lan-setup 명령(랜선 직결용 이더넷 주소 임시 맞추기) + receive/send 의 --auto-ip. 동작은 net_setup 에 있다.

기본은 계획만 보여 주는 미리보기이며 --yes 가 있어야 적용한다. 시험은 ENV_FACTORY 를 가짜 runner 가 든 NetEnv 로 바꿔 끼운다.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys

from . import net_setup as ns

ENV_FACTORY = ns.NetEnv  # 시험에서 가짜 runner/관리자 판정/기록 위치를 가진 NetEnv 를 돌려주는 함수로 바꾼다


def _err(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr, flush=True)


def add_parsers(sub) -> None:
    p = sub.add_parser("lan-setup", help="랜선 직결용: 이 PC 이더넷 주소를 같은 대역 고정 IP 로 임시로 맞추고(보내는 쪽 .1 / 받는 쪽 .2), 끝나면 원래대로 되돌린다")
    p.add_argument("--role", choices=ns.ROLES, help="sender(보내는 쪽 .1) 또는 receiver(받는 쪽 .2)")
    p.add_argument("--adapter", help="바꿀 이더넷 어댑터 이름(생략하면 직결 후보가 하나일 때 자동). 이름은 --status 로 확인")
    p.add_argument("--peer", help="상대(받는 쪽) 주소. 상대가 고른 대역에 맞춘다(예: 10.254.250.2)")
    p.add_argument("--dry-run", action="store_true", help="계획만 보여 준다(기본 동작. --yes 가 없으면 항상 계획만 보여 줌)")
    p.add_argument("--yes", action="store_true", help="확인하고 실제로 적용(또는 --restore 와 함께 되돌리기)")
    p.add_argument("--restore", action="store_true", help="적용 전 원래 설정으로 되돌린다(복구 기록이 있을 때)")
    p.add_argument("--status", action="store_true", help="직결 후보 어댑터와 복구 기록을 보여 준다(읽기 전용)")
    p.add_argument("--allow-internet-adapter", action="store_true", help="인터넷(게이트웨이)에 연결된 어댑터도 바꾸는 것을 명시적으로 허용(인터넷이 끊길 수 있음)")
    p.add_argument("--json", action="store_true", help="--status 를 JSON 으로 출력")
    # 관리자 권한으로 승격된 보조 프로세스 전용(사용자가 직접 쓰지 않는다)
    p.add_argument("--apply-elevated", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--action", choices=("apply", "restore"), help=argparse.SUPPRESS)
    p.add_argument("--adapter-index", type=int, help=argparse.SUPPRESS)
    p.add_argument("--state-file", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_lan_setup)
    for name in ("receive", "send"):
        sp = sub.choices.get(name)
        if sp is not None:
            sp.add_argument("--auto-ip", action="store_true",
                            help="시작 전에 이 PC 이더넷 주소를 임시로 맞추고(계획을 보여 주고 확인) 끝나면 원래대로 되돌린다(관리자 권한 확인창이 뜰 수 있음)")


def cmd_lan_setup(args: argparse.Namespace) -> int:
    env = ENV_FACTORY()
    if args.apply_elevated:
        if not (args.action and args.role and args.adapter_index and args.state_file):
            return 2
        return ns.elevated_main(args.action, args.role, args.adapter_index, args.state_file, env)
    try:
        if args.status:
            return _status(env, args.json)
        if args.restore:
            return _restore(env, args.yes)
        if not args.role:
            _err("--role sender|receiver 가 필요합니다(상태만 보려면 --status, 되돌리려면 --restore).")
            return 2
        plan = ns.plan_ip_setup(args.role, args.adapter, args.peer, allow_internet=args.allow_internet_adapter, env=env)
        print(plan.describe())
        print(f"복구 방법: {plan.restore_howto}")
        if args.dry_run or not args.yes:
            print("[미리보기] 아직 아무것도 바꾸지 않았습니다. 적용하려면 같은 명령에 --yes 를 붙이세요.")
            return 0
        res = ns.apply_ip_setup(plan, lambda _text: True, env)
        print(f"[적용됨] {res['adapter']} = {res['ip']}/{res['prefix']}. 끝나면 `pcmigrate lan-setup --restore --yes` 로 되돌리세요.")
        return 0
    except ns.NetSetupError as e:
        _err(str(e))
        return 2 if e.code in ("bad_role", "ambiguous", "unknown_adapter", "bad_peer", "peer_not_in_range", "internet_adapter") else 1


def _status(env: ns.NetEnv, as_json: bool) -> int:
    st = ns.current_status(env)
    if as_json:
        print(json.dumps(st, ensure_ascii=False))
        return 0
    pend = st.get("pending")
    print("[복구 기록] " + (pend["message"] if pend else "없음(원래 설정 그대로)"))
    if st.get("error"):
        _err(st["error"])
        return 1
    print("유선(케이블 연결됨) 어댑터:")
    if not st["adapters"]:
        print("  (없음)")
    for a in st["adapters"]:
        mode = "자동(DHCP)" if a["dhcp"] else "고정"
        addr = ", ".join(f"{x['ip']}/{x['prefix']}" for x in a["addresses"]) or "주소 없음"
        tag = "직결 후보" if a["direct_candidate"] else "인터넷 연결 어댑터(기본 제외)"
        print(f"  - {a['name']}: {mode} {addr} [{tag}]")
    return 0


def _restore(env: ns.NetEnv, yes: bool) -> int:
    pend = ns.pending_restore(env)
    if pend is None:
        print("되돌릴 기록이 없습니다. 이더넷 설정은 원래 그대로입니다.")
        return 0
    if pend.get("bad"):
        _err(pend["message"])
        return 1
    print(pend["message"])
    if not yes:
        print("[미리보기] 되돌리려면 `pcmigrate lan-setup --restore --yes` 를 실행하세요.")
        return 0
    res = ns.restore_ip_setup(env)
    print(f"[되돌림] 어댑터 {res.get('adapter', '')} 를 원래 설정으로 되돌렸습니다." if res.get("restored") else "되돌릴 기록이 없습니다.")
    return 0


def notify_pending() -> None:
    """아무 명령이나 실행할 때: 복구 기록이 남아 있으면 안내만 한다(자동으로 바꾸지 않는다). 어떤 오류도 명령을 막지 않는다."""
    try:
        pend = ns.pending_restore(ENV_FACTORY())
    except Exception:  # noqa: BLE001
        return
    if pend:
        _err("[안내] " + pend["message"])


@contextlib.contextmanager
def auto_ip_scope(args: argparse.Namespace, role: str, peer: str | None = None):
    """--auto-ip: 계획을 보여 주고 확인받아 적용하고, 블록이 끝나면(오류/Ctrl+C 포함) 원래대로 되돌린다. 실패하면 yield 값이 종료 코드(int), 아니면 None."""
    if not getattr(args, "auto_ip", False):
        yield None
        return
    env = ENV_FACTORY()
    applied = False
    try:
        try:
            plan = ns.plan_ip_setup(role, None, peer, env=env)

            def confirm(text: str) -> bool:
                print(text)
                if not (sys.stdin and sys.stdin.isatty()):
                    _err("확인할 수 없어(터미널이 아님) 이더넷 설정을 바꾸지 않았습니다. 먼저 `pcmigrate lan-setup --role ... --yes` 를 쓰세요.")
                    return False
                try:
                    return input("이대로 적용할까요? (y/N): ").strip().lower() in ("y", "yes", "예", "ㅇ")
                except EOFError:
                    return False

            ns.apply_ip_setup(plan, confirm, env)
            applied = True
            print(f"[주소 맞춤] {plan.adapter.name} = {plan.ip}/{plan.prefix} (끝나면 자동으로 원래대로 되돌립니다)", flush=True)
        except ns.NetSetupError as e:
            _err(str(e))
            yield 1
            return
        yield None
    finally:
        if applied:
            try:
                ns.restore_ip_setup(env)
                print("[주소 복구] 이더넷 설정을 원래대로 되돌렸습니다.", flush=True)
            except ns.NetSetupError as e:
                _err(str(e), "직접 되돌리려면: pcmigrate lan-setup --restore --yes")
