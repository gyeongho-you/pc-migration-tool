"""탐색기에서 파일을 끌어다 놓기(표준 라이브러리 ctypes 만 사용, 외부 패키지 없음). stage63.

방법: 받을 위젯(과 그 안의 위젯들)의 창 핸들에 DragAcceptFiles 를 켜고, 창 프로시저를 바꿔 끼워 WM_DROPFILES 만 처리한다
(나머지 메시지는 원래 프로시저로 그대로 넘긴다). 경로 목록은 DragQueryFileW 로 읽고 DragFinish 로 마무리한다.

알려진 한계: 설치한 뒤에 새로 만든 위젯(창 핸들)에는 끌어다 놓기가 걸리지 않는다. 화면이 상자를 다시 그릴 때는 install() 을 다시 부른다.

안전 원칙: 어떤 이유로든 설치에 실패하면(Windows 가 아님, 관리자 권한으로 실행 중, ctypes 오류 등) **끌어다 놓기만 조용히 끄고**
reason 에 이유를 남긴다. 화면의 '파일 추가'/'폴더 추가' 버튼은 항상 동작한다. 관리자 권한으로 실행 중이면 Windows(UIPI)가 일반 권한 탐색기의
끌어놓기를 막으므로 설치하지 않는다(우회용 메시지 필터는 시도하지 않는다).

실제 끌어놓기는 자동 시험으로 재현할 수 없어, 시험은 deliver()(경로 목록을 받는 함수)를 직접 부른다.
"""
from __future__ import annotations

import os
from typing import Callable

from .. import errorlog

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

WM_NCDESTROY = 0x0082
REASON_NO_WINDOWS, REASON_ADMIN, REASON_ERROR = "no_windows", "admin", "error"


class DropHook:
    """widget(과 모든 하위 위젯)에 파일 끌어다 놓기를 연결한다. on_drop(paths: list[str]) 은 Tk 주 스레드에서 불린다."""

    def __init__(self, widget, on_drop: Callable[[list], None], *, is_admin_fn: Callable[[], bool] | None = None,
                 api_factory: Callable[[], dict] | None = None) -> None:
        self.widget, self.on_drop = widget, on_drop
        self.is_admin_fn, self.api_factory = is_admin_fn, api_factory
        self.installed = False
        self.reason = ""  # 설치하지 못한 이유("" 이면 문제 없음 또는 아직 설치 전)
        self._orig: dict[int, int] = {}
        self._accepted: list[int] = []  # DragAcceptFiles 를 켠 핸들(롤백 때 모두 끈다)
        self._cb = None  # 콜백 객체를 계속 붙들고 있어야 한다(가비지 수집되면 충돌)
        self._api: dict = {}

    # ------------------------------------------------------------------ 설치/해제
    def install(self) -> bool:
        if self.installed:
            return True
        try:
            if os.name != "nt" and self.api_factory is None:
                self.reason = REASON_NO_WINDOWS
                return False
            if (self.is_admin_fn or _is_admin)():
                self.reason = REASON_ADMIN
                return False
            self._api = (self.api_factory or _load_api)()
            self.widget.update_idletasks()
            self._cb = self._api["wndproc_type"](self._proc)
            for w in _walk(self.widget):
                hwnd = int(w.winfo_id())
                self._api["accept"](hwnd, True)
                self._accepted.append(hwnd)
                self._orig[hwnd] = self._api["subclass"](hwnd, self._cb)
            self.installed = True
            self.reason = ""
            self.widget.bind("<Destroy>", self._on_destroy, add="+")
            return True
        except Exception:  # noqa: BLE001 - 어떤 실패든 끌어다 놓기만 끈다
            self._rollback()
            self.reason = REASON_ERROR
            self.installed = False
            return False

    def _rollback(self) -> None:
        """바꿔 끼운 프로시저를 모두 되돌리고 DragAcceptFiles 를 모두 끈다(한 핸들이 실패해도 나머지는 계속)."""
        for hwnd, orig in list(self._orig.items()):
            try:
                self._api["restore"](hwnd, orig)
            except Exception:  # noqa: BLE001
                pass
        for hwnd in list(self._accepted):
            try:
                self._api["accept"](hwnd, False)
            except Exception:  # noqa: BLE001
                pass
        self._orig.clear()
        self._accepted.clear()

    def uninstall(self) -> None:
        if self.installed:
            self._rollback()
        self.installed = False

    def _on_destroy(self, event) -> None:
        # 창이 사라지는 중에도 원래 프로시저는 WM_DESTROY/WM_NCDESTROY 처리를 위해 유지한다(여기서 지우면 파괴 메시지를 삼킨다).
        # 핸들별 정리는 _proc 이 WM_NCDESTROY 를 원래 프로시저에 넘긴 직후에 한다.
        if event.widget is self.widget:
            self.installed = False

    # ------------------------------------------------------------------ 메시지 처리
    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_DROPFILES:
                paths = self._api["query"](wparam)
                self.widget.after(0, self.deliver, paths)  # 창 프로시저 안에서는 목록만 읽고, 처리는 Tk 이벤트 루프로 넘긴다
                return 0
        except Exception as e:  # noqa: BLE001 - 창 프로시저 밖으로 예외를 내보내지 않지만 삼키지도 않는다(오류 로그에 남긴다)
            errorlog.record(e, "끌어다 놓기: 창 프로시저")
            return 0
        orig = self._orig.get(int(hwnd), 0)
        if not orig:
            return 0
        result = self._api["call_orig"](orig, hwnd, msg, wparam, lparam)
        if msg == WM_NCDESTROY:  # 이 창의 마지막 메시지: 원래 프로시저가 처리한 뒤에 우리 기록을 지운다
            self._orig.pop(int(hwnd), None)
            if int(hwnd) in self._accepted:
                self._accepted.remove(int(hwnd))
        return result

    def deliver(self, paths: list) -> None:
        """받은 경로 목록을 화면에 전달한다(시험은 이 함수를 직접 부른다). 빈 목록(실제 파일이 없는 항목)도 전달한다."""
        try:
            self.on_drop([str(p) for p in paths])
        except Exception as e:  # noqa: BLE001 - 화면 쪽 오류가 창 프로시저를 깨뜨리지 않게(오류 로그에는 남긴다)
            errorlog.record(e, "끌어다 놓기: 받은 경로 처리")


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _is_admin() -> bool:
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _load_api() -> dict:
    """Windows API 묶음. 인자/반환 형식을 모두 지정한다(64비트에서 포인터가 잘리지 않게)."""
    import ctypes
    from ctypes import wintypes as wt
    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    lresult = ctypes.c_ssize_t
    wndproc = ctypes.WINFUNCTYPE(lresult, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    set_ptr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    set_ptr.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
    set_ptr.restype = ctypes.c_void_p
    call = user32.CallWindowProcW
    call.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    call.restype = lresult
    shell32.DragAcceptFiles.argtypes = [wt.HWND, wt.BOOL]
    shell32.DragAcceptFiles.restype = None
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wt.UINT, wt.LPWSTR, wt.UINT]
    shell32.DragQueryFileW.restype = wt.UINT
    shell32.DragFinish.argtypes = [ctypes.c_void_p]
    shell32.DragFinish.restype = None

    def accept(hwnd: int, on: bool) -> None:
        shell32.DragAcceptFiles(hwnd, bool(on))

    def subclass(hwnd: int, cb) -> int:
        old = set_ptr(hwnd, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
        if not old:
            raise OSError("창 프로시저를 바꾸지 못했습니다")
        return int(old)

    def restore(hwnd: int, orig: int) -> None:
        set_ptr(hwnd, GWLP_WNDPROC, ctypes.c_void_p(orig))

    def query(hdrop) -> list:
        try:
            n = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            out = []
            for i in range(int(n)):
                size = shell32.DragQueryFileW(hdrop, i, None, 0)
                buf = ctypes.create_unicode_buffer(int(size) + 1)
                shell32.DragQueryFileW(hdrop, i, buf, int(size) + 1)
                if buf.value:
                    out.append(buf.value)
            return out
        finally:
            shell32.DragFinish(hdrop)

    def call_orig(orig, hwnd, msg, wparam, lparam):
        return call(orig, hwnd, msg, wparam, lparam)

    return {"wndproc_type": wndproc, "accept": accept, "subclass": subclass, "restore": restore, "query": query, "call_orig": call_orig}
