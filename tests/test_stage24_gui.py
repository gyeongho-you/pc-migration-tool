"""stage24 GUI: 읽지 못해 제외된 파일만 다시 담기(컨트롤러 헤드리스 + tkinter 상자). 가짜 api 만 쓴다."""
import os
import threading
from types import SimpleNamespace

import pytest

from pcmigrate import api as real_api
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError, friendly_error
from pcmigrate.gui.runner import Runner
from pcmigrate.progress import ProgressInfo
from test_gui_controller import FakeApi, kinds


def locked(src="C:\\fake\\Chrome\\LOCK"):
    return {"src": src, "error": "사용 중이라 읽지 못함(다른 프로그램이 파일을 잡고 있습니다)", "reason": "in_use"}


def manifest(failed=(), files=3):
    return {"rules": [{"id": "demo"}], "files": [{}] * files, "skipped": [], "failed": list(failed), "manual_warnings": []}


class RetryApi(FakeApi):
    def __init__(self, failed=(locked(),)):
        super().__init__()
        self.failed = list(failed)
        self.retry_calls = []
        self.retry_next = None  # 다음 호출의 결과 manifest(None 이면 모두 담김)
        self.wait = None

    def run_export(self, rules_dir, output, *, extra=None, allow_secrets=False, deps=None, extra_items=None):
        self.calls.append(("export", output, allow_secrets, extra_items))
        return SimpleNamespace(manifest=manifest(self.failed))

    def run_export_retry(self, rules_dir, output, *, deps=None, progress_info=None, notice=None, stage_dir=None):
        self.retry_calls.append(output)
        if progress_info:
            progress_info(ProgressInfo("find", 0, 0, 0, 0, message="찾는 중"))
        if self.wait:
            self.wait.wait(2)
            progress_info(ProgressInfo("pack", 1, 2, 0, 1))
        m = dict(self.retry_next if self.retry_next is not None else manifest([], 4))
        m["retry"] = {"added": [{"src": "x", "arc": "a", "size": 1}] if not m["failed"] else [], "gone": [], "not_retried": [],
                      "unchanged": bool(m["failed"]), "backup": output + ".bak", "total": 1}
        return real_api.RetryResult(m, output)


@pytest.fixture
def env(tmp_path):
    api = RetryApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path,
                   make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    ev = []
    c.listener = lambda kind, payload=None: ev.append((kind, payload))
    out = tmp_path / "p.zip"
    out.write_bytes(b"zip")
    return c, api, ev, str(out)


def exported(env):
    c, api, ev, out = env
    c.start_export(out, write_confirmed=True)
    return c, api, ev, out


def test_retry_reason_and_guards(env):
    c, api, ev, out = env
    assert c.retry_export_reason() == T.G_RETRY_NO_PACKAGE and c.export_unreadable() == []
    with pytest.raises(GuardError, match=T.G_NEED_RETRY_CONFIRM):
        c.retry_export_failed()
    with pytest.raises(GuardError, match=T.G_RETRY_NO_PACKAGE):
        c.retry_export_failed(write_confirmed=True)
    c.start_export(out, write_confirmed=True)
    assert len(c.export_unreadable()) == 1 and c.retry_export_reason() == ""
    c.state.busy = True
    assert c.retry_export_reason() == T.WHY_BUSY
    with pytest.raises(GuardError, match=T.G_BUSY):
        c.retry_export_failed(write_confirmed=True)
    c.state.busy = False
    assert api.retry_calls == []


def test_nothing_to_retry_and_missing_file_guard(env):
    c, api, ev, out = env
    api.failed = []
    c.start_export(out, write_confirmed=True)
    assert c.retry_export_reason() == T.G_RETRY_NOTHING
    with pytest.raises(GuardError, match=T.G_RETRY_NOTHING):
        c.retry_export_failed(write_confirmed=True)
    api.failed = [locked()]
    c.start_export(out, write_confirmed=True)
    os.remove(out)
    with pytest.raises(GuardError, match="찾을 수 없습니다"):
        c.retry_export_failed(write_confirmed=True)


def test_retry_success_updates_result_and_emits_events(env):
    c, api, ev, out = exported(env)
    ev.clear()
    c.retry_export_failed(write_confirmed=True)
    assert api.retry_calls == [out]
    assert c.export_unreadable() == [] and len(c.state.export_result.manifest["files"]) == 4
    k = kinds(ev)
    assert k.index("busy") < k.index("export_retry_done") and ("busy", None) in ev
    assert "progress_info" in k and c.state.busy is False
    assert c.retry_export_reason() == T.G_RETRY_NOTHING


def test_retry_still_failed_keeps_button_state(env):
    c, api, ev, out = exported(env)
    api.retry_next = manifest([locked()], 3)
    c.retry_export_failed(write_confirmed=True)
    assert len(c.export_unreadable()) == 1 and c.retry_export_reason() == ""
    assert ev[-1][0] == "export_retry_done" and ev[-1][1].unchanged


def test_retry_error_is_friendly_and_busy_released(env):
    c, api, ev, out = exported(env)

    def boom(*a, **k):
        raise real_api.ApiError("다시 담기 실패: 같은 출력 파일(p.zip)로 내보내기가 이미 실행 중입니다(PID 5).")
    api.run_export_retry = boom
    c.retry_export_failed(write_confirmed=True)
    assert c.state.busy is False and kinds(ev)[-1] == "error"
    assert "실행 중" in ev[-1][1].what
    assert friendly_error(real_api.ApiError("다시 담기 실패: x")).what


def test_retry_is_cancellable_through_existing_flow(tmp_path):
    api = RetryApi()
    api.wait = threading.Event()
    c = Controller(api_mod=api, runner=Runner(sync=False), rules_dir=tmp_path, make_items=lambda d: [])
    ev = []
    c.listener = lambda kind, payload=None: ev.append((kind, payload))
    out = tmp_path / "p.zip"
    out.write_bytes(b"z")
    c.state.export_result, c.state.export_output = SimpleNamespace(manifest=manifest([locked()])), str(out)
    c.retry_export_failed(write_confirmed=True)
    assert c.state.busy and c.can_cancel
    c.cancel()
    api.wait.set()
    c.runner.join(3)
    c.runner.pump()
    assert "cancelled" in kinds(ev) and c.state.busy is False and "export_retry_done" not in kinds(ev)
    assert c.export_unreadable()  # 취소하면 결과는 그대로


def test_task_label_is_registered_everywhere():
    from pcmigrate.gui.status import PHASE_MAP
    assert T.TASK_EXPORT_RETRY in T.PROGRESS and T.TASK_EXPORT_RETRY in T.TASK_STEPS and T.TASK_EXPORT_RETRY in PHASE_MAP
    assert "Chrome" in T.EXP_RETRY_HINT and "닫고 눌러 주세요" in T.EXP_RETRY_HINT


# --------------------------------------------------------------------- tkinter

tk = pytest.importorskip("tkinter")
from test_gui_smoke import ctx  # noqa: E402,F401


@pytest.fixture
def tkenv(ctx):
    api = RetryApi()
    ctx.c.api = api
    return ctx, api


def test_tk_panel_hidden_until_unreadable_then_retry_updates(tkenv, tmp_path):
    ctx, api = tkenv
    v = ctx.app.views[S.OLD_EXPORT]
    ctx.c.go(S.OLD_EXPORT)
    p = v.retry_panel
    ctx.app.update()
    assert not p._shown
    out = tmp_path / "p.zip"
    out.write_bytes(b"zip")
    v.path_var.set(str(out))
    v._export()
    ctx.app.update()
    assert p._shown and T.EXP_RETRY_HEAD.format(n=1) == p.head.cget("text") and "disabled" not in p.btn.state()
    assert "Chrome" in p.hint.cget("text")
    ctx.answers["askokcancel"] = False
    p.retry()
    assert api.retry_calls == []  # 확인에서 취소하면 아무것도 하지 않는다
    ctx.answers["askokcancel"] = True
    api.retry_next = manifest([locked()], 3)
    p.retry()
    ctx.app.update()
    assert api.retry_calls == [str(out)] and p._shown
    assert T.EXP_RETRY_NO_CHANGE.format(still=1) in p.result.cget("text") and "disabled" not in p.btn.state()
    assert "읽지 못함(실패) 1개" in v.result.get("1.0", "end")
    api.retry_next = None
    p.retry()
    ctx.app.update()
    assert p.head.cget("text") == T.EXP_RETRY_ALL_DONE and not p.btn.winfo_ismapped()
    assert T.EXP_RETRY_DONE.format(added=1, still=0) in p.result.cget("text")
    assert "읽지 못함(실패) 0개" in v.result.get("1.0", "end")
    # 화면을 벗어났다가 다시 들어오면 숨는다
    ctx.c.go(S.START)
    ctx.c.go(S.OLD_EXPORT)
    ctx.app.update()
    assert not p._shown


def test_tk_button_disabled_while_busy(tkenv, tmp_path):
    ctx, api = tkenv
    v = ctx.app.views[S.OLD_EXPORT]
    ctx.c.go(S.OLD_EXPORT)
    out = tmp_path / "p.zip"
    out.write_bytes(b"zip")
    ctx.c.start_export(str(out), write_confirmed=True)
    ctx.app.update()
    p = v.retry_panel
    assert "disabled" not in p.btn.state()
    ctx.c.state.busy = True
    ctx.c.emit("busy", "x")
    assert "disabled" in p.btn.state()
    ctx.c.state.busy = False
    ctx.c.emit("busy", None)
    assert "disabled" not in p.btn.state()
