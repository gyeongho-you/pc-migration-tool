"""전송 중 시스템 절전/잠자기 방지(SetThreadExecutionState). 화면 보호기/디스플레이 끄기는 건드리지 않는다."""
from __future__ import annotations

import sys
from typing import Callable

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
# ES_AWAYMODE_REQUIRED(0x40) 와 ES_DISPLAY_REQUIRED(0x2) 는 일부러 쓰지 않는다.
KEEP_AWAKE_FLAGS = ES_CONTINUOUS | ES_SYSTEM_REQUIRED


def default_set_state(flags: int) -> int | None:
    """kernel32.SetThreadExecutionState 호출. Windows 가 아니면 None(지원 안 함). 실패하면 0."""
    if sys.platform != "win32":
        return None
    import ctypes
    fn = ctypes.windll.kernel32.SetThreadExecutionState
    fn.argtypes = [ctypes.c_uint32]
    fn.restype = ctypes.c_uint32
    return int(fn(flags))


class KeepAwake:
    """with 블록 동안 시스템이 절전/잠자기로 들어가지 않게 한다. 나갈 때 반드시 원래대로 되돌린다.

    SetThreadExecutionState 는 호출한 스레드에 적용되므로, 전송을 실행하는 스레드 안에서 with 를 써야 한다.
    실패해도 예외를 내지 않고 경고만 한다(전송은 계속).
    """

    def __init__(self, enabled: bool = True, set_state: Callable[[int], int | None] | None = None,
                 warn: Callable[[str], None] | None = None) -> None:
        self.enabled = enabled
        self.set_state = set_state or default_set_state
        self.warn = warn or (lambda m: None)
        self.status = "off"  # off | on | restored | failed | unsupported

    def __enter__(self) -> "KeepAwake":
        if not self.enabled:
            return self
        try:
            prev = self.set_state(KEEP_AWAKE_FLAGS)
        except Exception as e:  # noqa: BLE001 - 절전 방지 실패가 전송을 막으면 안 된다
            self.status = "failed"
            self.warn(f"절전 방지를 켜지 못했습니다({type(e).__name__}). 전송 중 PC 가 잠들지 않게 전원 설정을 확인하세요.")
            return self
        if prev is None:
            self.status = "unsupported"
        elif prev == 0:
            self.status = "failed"
            self.warn("절전 방지를 켜지 못했습니다. 전송 중 PC 가 잠들지 않게 전원 설정을 확인하세요.")
        else:
            self.status = "on"
        return self

    def __exit__(self, *exc) -> None:
        if self.status != "on":
            return
        try:
            self.set_state(ES_CONTINUOUS)  # 연속 상태 해제 = 원래 절전 정책으로 복원
            self.status = "restored"
        except Exception:  # noqa: BLE001
            self.status = "failed"
