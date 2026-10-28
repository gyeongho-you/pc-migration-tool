"""LAN 직접 전송의 화면/CLI 공용 API(시각 요소 없음). 화면은 이 함수들과 이벤트만 쓰면 된다.

- start_receiver(dest, port, on_event) -> Receiver 핸들: .addresses(접속 주소 목록) .code(6자리) .warnings .stop() .wait() .status() .result
  on_event(dict) 로 이벤트가 온다: listening / peer_connected / plan / progress(info=ProgressInfo) / auth_failed / disconnected /
  cancelled / error / done / stopped. 모든 이벤트에 "type", "message"(쉬운 문구)가 있다.
- discover_receivers(timeout) -> [Receiver 정보(name, ip, port)]  (UDP 브로드캐스트, 못 찾으면 빈 목록 + NO_DISCOVERY_HELP 안내)
- run_send(target, code, selection|sources, options, progress_info, cancel) -> 리포트 dict. 취소하면 Cancelled, 연결/인증 문제는 LanError(.code).
- run_diagnose(target, code) -> Diagnosis(한 줄 요약 .summary)
"""
from __future__ import annotations

import os
from typing import Callable

from .lan_link import (NO_DISCOVERY_HELP, Diagnosis, Receiver as FoundReceiver, default_receive_dir, diagnose, discover, local_addresses,
                       parse_target, public_warning, classify_ip, PUBLIC)
from .lan_protocol import DEFAULT_PORT, DISCOVERY_PORT, LanError, normalize_code
from .lan_transfer import Receiver, SendOptions, SendPlan, build_send_plan, run_send as _run_send
from .progress import ProgressInfo

__all__ = ["start_receiver", "discover_receivers", "resolve_target", "run_send", "run_diagnose", "SendOptions", "Receiver", "LanError",
           "default_receive_dir", "local_addresses", "NO_DISCOVERY_HELP", "build_send_plan",
           "list_ip_adapters", "plan_ip_setup", "apply_ip_setup", "restore_ip_setup", "pending_restore", "NetSetupError"]

AUTO_WORDS = ("자동", "auto")


def start_receiver(dest: str | os.PathLike | None = None, port: int = DEFAULT_PORT, on_event: Callable[[dict], None] | None = None, *,
                   code: str | None = None, bind: str = "0.0.0.0", allow_public: bool = False, discovery: bool = True,
                   discovery_port: int = DISCOVERY_PORT, **kwargs) -> Receiver:
    """수신 대기를 시작하고 핸들을 돌려준다(스레드로 동작). dest 가 None 이면 바탕화면 아래 새 폴더."""
    rx = Receiver(dest or default_receive_dir(), port=port, bind=bind, code=code, allow_public=allow_public, discovery=discovery,
                  discovery_port=discovery_port, on_event=on_event, **kwargs)
    return rx.start()


def discover_receivers(timeout: float = 2.0, port: int = DISCOVERY_PORT, targets: list[str] | None = None) -> list[FoundReceiver]:
    return discover(timeout, port=port, targets=targets)


def resolve_target(target: str, *, timeout: float = 2.0, discovery_port: int = DISCOVERY_PORT, targets: list[str] | None = None,
                   choose: Callable[[list[FoundReceiver]], FoundReceiver | None] | None = None) -> tuple[str, int]:
    """'주소[:포트]' 또는 '자동' -> (주소, 포트). 자동은 검색 결과가 하나면 그것, 여럿이면 choose 콜백(없으면 오류), 없으면 수동 주소 안내."""
    if target.strip().lower() in AUTO_WORDS:
        found = discover(timeout, port=discovery_port, targets=targets)
        if not found:
            raise LanError(NO_DISCOVERY_HELP, "no_receiver")
        if len(found) == 1:
            return found[0].ip, found[0].port
        pick = choose(found) if choose else None
        if pick is None:
            err = LanError("수신 PC 가 여러 대 보입니다. 접속할 주소를 직접 지정하세요: " + ", ".join(f"{r.name}({r.address})" for r in found), "multiple_receivers")
            err.receivers = found  # type: ignore[attr-defined]
            raise err
        return pick.ip, pick.port
    return parse_target(target)


def run_send(target: str, code: str, selection=None, sources: list[str] | None = None, options: SendOptions | dict | None = None,
             progress_info: Callable[[ProgressInfo], None] | None = None, cancel: Callable[[], bool] | None = None,
             on_event: Callable[[dict], None] | None = None, *, plan: SendPlan | None = None, **resolve_kw) -> dict:
    """구 PC 에서 새 PC 로 보낸다. target 은 '주소[:포트]' 또는 '자동'. 리포트 dict(verification: exact/mismatch/missing 등)를 돌려준다."""
    opts = options if isinstance(options, SendOptions) else SendOptions(**(options or {}))
    code = normalize_code(code)
    host, port = resolve_target(target, **resolve_kw)
    if plan is None:
        plan = build_send_plan(sources, selection, opts, cancel=cancel, progress_info=progress_info)
    return _run_send(host, port, code, plan, opts, progress_info, cancel, on_event)


def run_diagnose(target: str, code: str | None = None, **kw) -> Diagnosis:
    return diagnose(target, code, **kw)


# --------------------------------------------------------------------------- stage39: 랜선 직결용 이더넷 주소 임시 맞추기(net_setup 의 얇은 창구)
# env 는 시험에서 가짜 runner 가 든 net_setup.NetEnv 를 넣는 자리(None 이면 실제 PC).
from . import net_setup as _ns  # noqa: E402
from .net_setup import NetSetupError  # noqa: E402,F401


def list_ip_adapters(env=None) -> list:
    """직결 후보 어댑터 목록(읽기 전용)."""
    return _ns.list_adapters(env)


def plan_ip_setup(role: str, adapter: str | None = None, peer: str | None = None, *, allow_internet: bool = False, env=None):
    """계획만 만든다(읽기 전용). role: 'sender'(.1) / 'receiver'(.2). peer: 받는 쪽 주소(있으면 그 대역에 맞춘다)."""
    return _ns.plan_ip_setup(role, adapter, peer, allow_internet=allow_internet, env=env)


def apply_ip_setup(plan, confirm_fn: Callable[[str], bool], env=None) -> dict:
    """confirm_fn(계획 설명) 이 True 일 때만 원래 설정을 저장하고 적용한다. 실패하면 롤백한다."""
    return _ns.apply_ip_setup(plan, confirm_fn, env)


def restore_ip_setup(env=None) -> dict:
    return _ns.restore_ip_setup(env)


def pending_restore(env=None) -> dict | None:
    """프로그램이 중간에 꺼져 되돌리지 못한 변경이 있으면 요약 dict, 없으면 None."""
    return _ns.pending_restore(env)
