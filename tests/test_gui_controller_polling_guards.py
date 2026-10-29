"""16b: GUI 폴링/상태 복구(C-2), 가드(C-8, 출력 경로, 복원 확인 초기화)."""
import threading
import time

import pytest

from pcmigrate import api as real_api
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi, env, item, plan  # noqa: F401  (env 픽스처 재사용)


# --------------------------------------------------------------------- C-2: Runner/컨트롤러가 콜백 예외에도 계속 동작

def test_runner_callback_exception_does_not_stop_pump_or_leave_busy():
    r = Runner(sync=True)
    errors = []
    r.on_callback_error = lambda e, tb: errors.append(type(e).__name__)
    got = []

    def boom(_res):
        raise RuntimeError("화면 오류")

    r.submit(lambda p: 1, on_done=boom, on_error=lambda e, tb: None, on_cancel=lambda: None)
    assert errors == ["RuntimeError"] and not r.busy
    r.submit(lambda p: 2, on_done=got.append, on_error=lambda e, tb: None, on_cancel=lambda: None)
    assert got == [2]  # 다음 작업이 정상 실행


def test_runner_without_hook_still_drains_queue_then_raises():
    r = Runner()
    got = []
    r._callbacks = {1: {"progress": lambda m: (_ for _ in ()).throw(ValueError("x")), "done": got.append,
                        "error": None, "cancel": None}}
    r._q.put((1, "progress", "a", ""))
    r._q.put((1, "done", 7, ""))
    with pytest.raises(ValueError):
        r.pump()
    assert got == [7] and not r.busy  # 예외가 있어도 완료 이벤트까지 처리됨


def test_runner_thread_that_vanishes_without_result_becomes_error():
    r = Runner()
    res = []
    r._callbacks = {1: {"progress": lambda m: None, "done": res.append, "cancel": lambda: None,
                        "error": lambda e, tb: res.append(("error", str(e)))}}
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    r._thread = t
    assert r.busy
    r.pump()
    assert not r.busy and res and res[0][0] == "error"


def test_listener_exception_does_not_leave_controller_busy(env):
    c, api, ev = env
    seen = []

    def listener(kind, payload=None):
        seen.append(kind)
        if kind == "busy" and payload is None:
            raise RuntimeError("뷰 갱신 실패")
        if kind == "error":
            seen.append(("fe", payload.what))

    c.listener = listener
    c.start_scan()
    assert not c.state.busy and c.state.scan is not None  # done 핸들러까지 끝까지 실행됨
    assert "scan_done" in seen and any(isinstance(x, tuple) and x[0] == "fe" for x in seen)  # 오류 대화상자로 알림
    c.start_scan()  # 다시 실행 가능
    assert not c.state.busy


def test_listener_exception_inside_error_dialog_is_swallowed(env):
    c, api, ev = env
    c.listener = lambda kind, payload=None: (_ for _ in ()).throw(RuntimeError("항상 실패"))
    c.start_scan()  # 예외 전파/무한 재귀 없이 끝난다
    assert not c.state.busy


def test_worker_exception_restores_state_and_reports(env):
    c, api, ev = env
    api.transfer_error = OSError("disk")
    c.state.transfer_src = str(env[0].state.rules_dir)
    c.state.transfer_dst = str(env[0].state.rules_dir) + "x"
    c.start_transfer(dry_run=True)
    assert not c.state.busy and ("error", None) != ev[-1] and any(k == "error" for k, _ in ev)


# --------------------------------------------------------------------- C-8: 옵션이 바뀌면 미리보기 무효

def _ready(c, tmp_path):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")


def test_option_change_after_preview_invalidates_it(env, tmp_path):
    c, api, ev = env
    _ready(c, tmp_path)
    c.start_transfer(dry_run=True, include_cloud=False, strict=False)
    assert c.transfer_reasons()[1] == ""
    c.set_transfer_options(True, False)  # 클라우드 포함으로 변경
    assert c.transfer_reasons()[1] == T.WHY_PREVIEW_FIRST
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False, write_confirmed=True, include_cloud=True)
    c.start_transfer(dry_run=True, include_cloud=True)
    assert c.transfer_reasons()[1] == ""
    c.start_transfer(dry_run=False, write_confirmed=True, include_cloud=True)
    assert api.calls[-1] == ("transfer", False, True, False)


def test_start_without_view_toggle_but_different_arg_is_refused(env, tmp_path):
    c, api, ev = env
    _ready(c, tmp_path)
    c.start_transfer(dry_run=True)
    with pytest.raises(GuardError, match="옵션"):
        c.start_transfer(dry_run=False, write_confirmed=True, include_cloud=True)


# --------------------------------------------------------------------- 출력 경로 가드 / 복원 확인 초기화

def test_export_output_path_guards(env, tmp_path):
    c, api, ev = env
    with pytest.raises(GuardError, match="폴더"):
        c.start_export(str(tmp_path), write_confirmed=True)
    with pytest.raises(GuardError, match="환경변수"):
        c.start_export("%TEMP%/x.zip", write_confirmed=True)
    assert not [x for x in api.calls if x[0] == "export"]


def _arm_apply(c, tmp_path):
    c.set_package(str(tmp_path / "p.zip"))
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    assert c.can_apply()[0]


def test_failed_apply_clears_confirmations(env, tmp_path):
    c, api, ev = env
    _arm_apply(c, tmp_path)
    orig = api.run_import

    def fail(package, rules_dir, *, dry_run=False, yes=False, **kw):
        if yes:
            raise OSError("쓰기 실패")
        return orig(package, rules_dir, dry_run=dry_run, yes=yes, **kw)

    api.run_import = fail
    c.start_apply()
    s = c.state
    assert not s.busy and not s.preview_checked and not s.final_confirmed and not c.can_apply()[0]


def test_successful_apply_requires_new_preview_to_apply_again(env, tmp_path):
    c, api, ev = env
    _arm_apply(c, tmp_path)
    c.start_apply()
    assert c.state.import_result is not None and c.state.preview is None
    assert not c.can_apply()[0]
