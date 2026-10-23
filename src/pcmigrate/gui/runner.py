"""백그라운드 작업 실행기. 작업은 스레드에서 돌고, 결과/진행은 큐로 UI 스레드(pump)에 전달된다.

tkinter 를 import 하지 않는다. UI 는 after() 로 pump() 를 주기적으로 호출하고, 콜백은 pump 를 호출한 스레드(=UI 스레드)에서 실행된다.
sync=True 이면(테스트) 작업을 호출 스레드에서 즉시 실행한다.
"""
from __future__ import annotations

import queue
import threading
import traceback
from typing import Any, Callable

from ..cancel import Cancelled


class Runner:
    def __init__(self, sync: bool = False) -> None:
        self.sync = sync
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._callbacks: dict[int, dict[str, Callable]] = {}
        self._next = 0

    @property
    def busy(self) -> bool:
        return bool(self._callbacks)

    def submit(self, fn: Callable[[Callable[[str], None]], Any], *, on_done: Callable[[Any], None],
               on_error: Callable[[BaseException, str], None], on_cancel: Callable[[], None],
               on_progress: Callable[[str], None] | None = None) -> int:
        """fn(progress) 를 백그라운드에서 실행한다. 이미 실행 중이면 RuntimeError."""
        if self.busy:
            raise RuntimeError("이미 실행 중인 작업이 있습니다")
        self._next += 1
        tid = self._next
        self._callbacks[tid] = {"done": on_done, "error": on_error, "cancel": on_cancel,
                                "progress": on_progress or (lambda m: None)}

        def progress(msg: str) -> None:
            self._q.put((tid, "progress", msg, ""))

        def work() -> None:
            try:
                res = fn(progress)
            except Cancelled:
                self._q.put((tid, "cancel", None, ""))
            except BaseException as e:  # noqa: BLE001 - 스레드 밖(UI)에서 한국어 메시지로 바꿔 보여 준다
                self._q.put((tid, "error", e, traceback.format_exc()))
            else:
                self._q.put((tid, "done", res, ""))

        if self.sync:
            work()
            self.pump()
        else:
            self._thread = threading.Thread(target=work, daemon=True, name=f"pcmigrate-task-{tid}")
            self._thread.start()
        return tid

    def pump(self) -> int:
        """대기 중인 이벤트를 처리한다(UI 스레드에서 호출). 처리한 개수를 돌려준다."""
        n = 0
        while True:
            try:
                tid, kind, payload, tb = self._q.get_nowait()
            except queue.Empty:
                return n
            n += 1
            cb = self._callbacks.get(tid)
            if cb is None:
                continue
            if kind == "progress":
                cb["progress"](payload)
                continue
            del self._callbacks[tid]  # 종료 이벤트: 먼저 busy 해제 후 콜백
            if kind == "done":
                cb["done"](payload)
            elif kind == "cancel":
                cb["cancel"]()
            else:
                cb["error"](payload, tb)

    def join(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
