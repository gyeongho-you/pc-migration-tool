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
        self._threads: dict[int, threading.Thread] = {}  # tid -> 작업 스레드
        self._abandoned: set[int] = set()  # 강제로 버린 작업(스레드가 아직 돌 수 있다)
        self._callbacks: dict[int, dict[str, Callable]] = {}
        self._next = 0
        # 완료/오류/진행 콜백이 예외를 내면 호출된다(UI 스레드). 없으면 큐를 끝까지 비운 뒤 첫 예외를 다시 던진다.
        self.on_callback_error: Callable[[BaseException, str], None] | None = None

    @property
    def busy(self) -> bool:
        return bool(self._callbacks)

    def submit(self, fn: Callable[[Callable[[str], None]], Any], *, on_done: Callable[[Any], None],
               on_error: Callable[[BaseException, str], None], on_cancel: Callable[[], None],
               on_progress: Callable[[str], None] | None = None) -> int:
        """fn(progress) 를 백그라운드에서 실행한다. 이미 실행 중이면 RuntimeError."""
        if self.busy:
            raise RuntimeError("이미 실행 중인 작업이 있습니다")
        self._threads = {t: th for t, th in self._threads.items() if th.is_alive()}
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
            self._threads[tid] = self._thread
            self._thread.start()
        return tid

    def pump(self) -> int:
        """대기 중인 이벤트를 처리한다(UI 스레드에서 호출). 처리한 개수를 돌려준다.

        콜백 하나가 예외를 내도 나머지 이벤트와 다음 pump 는 계속 처리된다(UI 폴링이 멈추지 않게).
        작업 스레드가 결과를 남기지 않고 사라진 경우에도 오류로 마무리해 busy 가 영원히 남지 않게 한다.
        """
        n = 0
        errors: list[tuple[BaseException, str]] = []
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                item = self._dead_thread_event()
                if item is None:
                    break
            tid, kind, payload, tb = item
            n += 1
            cb = self._callbacks.get(tid)
            if cb is None:
                continue
            if kind != "progress":
                del self._callbacks[tid]  # 종료 이벤트: 먼저 busy 해제 후 콜백
            try:
                if kind == "progress":
                    cb["progress"](payload)
                elif kind == "done":
                    cb["done"](payload)
                elif kind == "cancel":
                    cb["cancel"]()
                else:
                    cb["error"](payload, tb)
            except Exception as e:  # noqa: BLE001 - 화면 쪽 콜백 오류가 폴링을 멈추게 두지 않는다
                errors.append((e, traceback.format_exc()))
        for exc, tb in errors:
            hook = self.on_callback_error
            if hook is None:
                raise exc
            try:
                hook(exc, tb)
            except Exception:  # noqa: BLE001 - 오류 보고 중 오류가 또 나도 폴링은 계속
                pass
        return n

    def _dead_thread_event(self):
        """작업 스레드가 끝났는데 결과 이벤트가 없으면 '오류' 이벤트를 만들어 돌려준다(없으면 None)."""
        if self.sync or not self._callbacks:
            return None
        th = self._thread
        if th is None or th.is_alive():
            return None
        try:  # 스레드가 마지막 결과를 넣고 막 끝난 경우를 놓치지 않도록 한 번 더 본다
            return self._q.get_nowait()
        except queue.Empty:
            pass
        tid = max(self._callbacks)
        return (tid, "error", RuntimeError("작업이 결과를 남기지 않고 끝났습니다"), "")

    def abandon(self, tid: int) -> bool:
        """작업 tid 의 결과를 버린다(강제 중단): busy 를 풀고, 그 스레드가 나중에 남기는 이벤트는 무시한다.
        파이썬 스레드는 밖에서 죽일 수 없으므로 스레드는 취소 토큰을 보고 스스로 끝난다(임시물은 그 스레드가 정리한다)."""
        had = self._callbacks.pop(tid, None) is not None
        if had:
            self._abandoned.add(tid)
        return had

    def abandoned_alive(self) -> bool:
        """강제로 버렸지만 아직 끝나지 않은 작업 스레드가 있는가(같은 파일을 쓰는 새 작업을 막는 데 쓴다)."""
        for tid in list(self._abandoned):
            th = self._threads.get(tid)
            if th is None or not th.is_alive():
                self._abandoned.discard(tid)
                self._threads.pop(tid, None)
        return bool(self._abandoned)

    def join(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
