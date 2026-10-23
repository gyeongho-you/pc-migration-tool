"""파일 단위 병렬 처리(순서 유지). 해시 계산(hashlib 은 큰 버퍼에서 GIL 을 놓는다)을 여러 스레드로 나눠 돌린다."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

from .cancel import Cancelled

DEFAULT_WORKERS = 2  # 보수적 기본값: HDD/USB 에서는 동시에 여러 파일을 읽으면 오히려 느려질 수 있다
MAX_WORKERS = 8


def clamp_workers(n: int | None) -> int:
    return max(1, min(MAX_WORKERS, int(n) if n else DEFAULT_WORKERS))


def run_ordered(items: Iterable[Any], task: Callable[[Any], Any], handle: Callable[[Any, Any], None], *,
                workers: int = 1, window: int | None = None, cancel: Callable[[], bool] | None = None) -> None:
    """items 를 task 로 처리하되 결과 처리(handle)는 항상 호출 스레드에서 입력 순서대로 한다.

    workers<=1 이면 같은 스레드에서 차례로 처리한다. 진행 중인 작업 수는 window(기본 workers*4)로 제한해
    파일이 수백만 개여도 미래 객체를 무한정 쌓지 않는다. 취소하면 Cancelled 를 던진다(작업 안에서 cancel 도 확인해야 큰 파일에서 빨리 멈춘다).
    """
    if workers <= 1:
        for item in items:
            if cancel is not None and cancel():
                raise Cancelled()
            handle(item, task(item))
        return
    window = window or workers * 4
    pending: deque = deque()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pcm-hash") as pool:
        try:
            for item in items:
                if cancel is not None and cancel():
                    raise Cancelled()
                pending.append((item, pool.submit(task, item)))
                while len(pending) >= window:
                    it, fut = pending.popleft()
                    handle(it, fut.result())
            while pending:
                it, fut = pending.popleft()
                handle(it, fut.result())
        except BaseException:
            for _it, fut in pending:
                fut.cancel()
            raise
