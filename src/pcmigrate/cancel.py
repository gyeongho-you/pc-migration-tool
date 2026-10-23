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
