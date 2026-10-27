"""취소 토큰. 오래 걸리는 작업이 중간에 멈출 수 있게 한다(스레드 안전)."""
from __future__ import annotations

import threading


class Cancelled(Exception):
    """사용자가 작업을 취소함."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    __call__ = is_set

    def raise_if_set(self) -> None:
        if self._event.is_set():
            raise Cancelled()


# --- 현재 스레드에 묶는 취소 토큰: 오래 걸리는 반복문이 인자를 일일이 넘기지 않고도 checkpoint() 로 멈출 수 있게 한다.
# GUI 실행기가 작업 스레드에서 bind(token) 으로 묶는다. 묶여 있지 않으면 checkpoint() 는 아무 일도 하지 않는다(CLI/시험).
_local = threading.local()


class bind:  # noqa: N801 - 컨텍스트 관리자
    """with bind(token): ... 동안 이 스레드의 checkpoint() 가 token 을 본다(중첩 가능)."""

    def __init__(self, token) -> None:
        self.token, self._prev = token, None

    def __enter__(self):
        self._prev = getattr(_local, "token", None)
        _local.token = self.token
        return self.token

    def __exit__(self, *exc) -> None:
        _local.token = self._prev


def current():
    return getattr(_local, "token", None)


def checkpoint() -> None:
    """이 스레드에 묶인 토큰이 취소됐으면 Cancelled. 청크/디렉터리 항목마다 불러도 가볍다."""
    tok = getattr(_local, "token", None)
    if tok is not None and tok.is_set():
        raise Cancelled()
