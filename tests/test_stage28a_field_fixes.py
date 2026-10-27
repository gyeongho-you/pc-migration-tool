"""stage28a: 현장 피드백(화면 제외). 민감 항목 없이 묶음 만들기, 속도/남은 시간 평활화, 모든 장기 작업의 중지와 강제 중단.
가짜 환경만 쓴다(이 PC의 사용자 이름/설치 목록 없음)."""
import json
import os
import random
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import api as real_api
from pcmigrate import cancel as cancel_mod
from pcmigrate import detect, discover, package, transfer
from pcmigrate.cancel import Cancelled, CancelToken, bind, checkpoint
from pcmigrate.copier import sha256_file
from pcmigrate.discover import ExtraItem
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner
from pcmigrate.package import (apply_import, export_package, inspect_savepoint, part_paths, plan_import, verify_package)
from pcmigrate.progress import ProgressTracker
from pcmigrate.rules import parse_rule

from test_gui_controller import FakeApi, cand, discovery, env, kinds  # noqa: F401

MB = 1024 * 1024
NOW = lambda: datetime(2030, 1, 1, tzinfo=timezone.utc)  # noqa: E731


def wait_until(c, pred, seconds=8.0):
    deadline = time.time() + seconds
    while not pred() and time.time() < deadline:
        c.pump()
        time.sleep(0.01)
    c.pump()
    return pred()


# =========================================================================== 2) 민감 항목 없이 묶음 만들기

class RecApi(FakeApi):
    """run_export 에 넘어온 인자를 기록한다(GUI 가 allow_unverified 를 넘기는지 확인)."""

    def run_export(self, rules_dir, output, *, extra=None, allow_secrets=False, deps=None, extra_items=None,
                   allow_unverified=False, progress_info=None, resume=True):
        self.calls.append(("export", output, allow_secrets, extra_items, allow_unverified))
        return SimpleNamespace(manifest={"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []})


@pytest.fixture
def renv(tmp_path):
    api = RecApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path,
                   make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    c.state.discovery = discovery()
    return c, api


def rows_by_name(c):
    return {r.path.split("\\")[-1]: r for r in c.candidate_rows()}


def export_args(api):
    return [x for x in api.calls if x[0] == "export"][-1]


def test_export_without_any_secret_selected_never_needs_ack(renv, tmp_path):
    c, api = renv
    r = rows_by_name(c)
    c.set_selected(r["Acme"].key, True)
    c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)  # 동의 체크 없음 + 민감 항목 없음 = 에러 없이 진행
    call = export_args(api)
    assert call[2] is False and call[4] is True


def test_export_after_ack_then_deselecting_secret_still_works(renv, tmp_path):
    c, api = renv
    r = rows_by_name(c)
    c.set_secrets_ack(True)
    c.set_selected(r["Creds"].key, True)
    c.set_selected(r["Acme"].key, True)
    c.set_selected(r["Creds"].key, False)  # 민감 항목만 빼고(동의는 켜 둔 채)
    c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)
    assert export_args(api)[2] is True  # 동의가 켜져 있으면 다시 검사에서 발견된 이름도 이해한 것으로 본다


def test_export_after_revoking_ack_drops_secret_selection_and_works(renv, tmp_path):
    c, api = renv
    r = rows_by_name(c)
    c.set_secrets_ack(True)
    c.set_selected(r["Creds"].key, True)
    c.set_selected(r["Acme"].key, True)
    c.set_secrets_ack(False)  # 동의를 거두면 민감 항목은 자동으로 선택 해제된다
    assert r["Creds"].key not in c.state.selected and r["Acme"].key in c.state.selected
    c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)
    assert export_args(api)[2] is False


def test_export_with_secret_selected_still_requires_ack(renv, tmp_path):
    c, api = renv
    r = rows_by_name(c)
    c.set_secrets_ack(True)
    c.set_selected(r["Creds"].key, True)
    c.state.secrets_ack = False  # 어떤 경로로든 동의 없이 민감 항목이 남아 있으면 막는다(안전 가드는 그대로)
    with pytest.raises(GuardError) as e:
        c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)
    assert str(e.value) == T.G_SECRET_NO_ACK


def test_select_all_then_export_skips_secret_items_without_error(renv, tmp_path):
    c, api = renv
    n, skipped = c.select_all_candidates()
    assert (n, skipped) == (1, 1)
    c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)
    assert export_args(api)[2] is False


@pytest.fixture
def extra_dir(tmp_path):
    d = tmp_path / "Acme"
    d.mkdir()
    (d / "settings.ini").write_text("a=1", encoding="utf-8")
    return ExtraItem("%APPDATA%", "Acme", d, True)


def test_rescan_incomplete_without_secret_names_does_not_block_in_gui(extra_dir, monkeypatch):
    """원인 재현: 다시 검사(prepare_extras)가 시간 초과/읽기 거부로 끝나면 비밀 이름이 없어도 '위험'으로 봐 묶음을 막았다."""
    monkeypatch.setattr(real_api, "secret_names", lambda item: ([], False))
    with pytest.raises(real_api.ApiError) as e:  # CLI 기본은 그대로 엄격하다
        real_api.prepare_extras(None, False, items=[extra_dir])
    assert "비밀 정보" in e.value.message
    prep = real_api.prepare_extras(None, False, items=[extra_dir], allow_unverified=True)
    assert prep.items == [extra_dir] and prep.risky == 0


def test_rescan_that_finds_secret_names_still_blocks_without_allow(extra_dir, monkeypatch):
    monkeypatch.setattr(real_api, "secret_names", lambda item: (["token.json"], True))
    with pytest.raises(real_api.ApiError):
        real_api.prepare_extras(None, False, items=[extra_dir], allow_unverified=True)
    assert real_api.prepare_extras(None, True, items=[extra_dir], allow_unverified=True).risky == 1


def test_export_end_to_end_with_incomplete_rescan_through_gui_controller(tmp_path, monkeypatch):
    """GUI 컨트롤러 + 진짜 api: 민감 항목을 하나도 고르지 않고 만들 때 '민감 정보' 오류가 나지 않는다."""
    monkeypatch.setattr(real_api, "secret_names", lambda item: ([], False))
    folder = tmp_path / "Acme"
    folder.mkdir()
    (folder / "a.cfg").write_text("x", encoding="utf-8")
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "z.json").write_text(json.dumps({"id": "z", "name": "Z", "detect": {"display_name_patterns": ["ZZ-none"]},
                                              "paths": [], "manual_warnings": []}), encoding="utf-8")
    deps = real_api.Deps(registry=lambda: [], winget_export=lambda: None, winget_list=lambda: None)
    seen = {}

    def fake_export(matched, out, registry, winget_text, **kw):
        seen["extras"] = kw.get("extras")
        return {"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": [], "coverage": []}
    deps.export = fake_export
    c = Controller(runner=Runner(sync=True), rules_dir=rules, deps=deps,
                   make_items=lambda data: [ExtraItem(x["root"], x["rel"], folder, True) for x in data["candidates"]])
    events = []
    c.listener = lambda k, p=None: events.append((k, p))
    d = discovery()
    d.programs[0].candidates = [cand("Acme"), cand("Creds", secret=["credentials.json"])]
    c.state.discovery = d
    r = rows_by_name(c)
    c.set_selected(r["Acme"].key, True)
    c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)
    assert "error" not in kinds(events) and "export_done" in kinds(events)
    assert len(seen["extras"]) == 1


# =========================================================================== 4) 속도/남은 시간 평활화

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def bursty_run(seconds=240, seed=7):
    """같은 드라이브에서 작은 파일/큰 파일이 섞여 파일 단위 완료 바이트가 들쭉날쭉한 복사(실제 평균은 약 20MB/s)."""
    rnd = random.Random(seed)
    clock = Clock()
    total = 20 * MB * seconds * 2
    t = ProgressTracker("copy", total, 100000, clock=clock)
    pending = 0.0
    speeds, etas, inst, raw_done = [], [], [], 0
    prev_done = 0
    for _ in range(int(seconds / 0.5)):
        clock.t += 0.5
        pending += 0.5
        while pending > 0:  # 파일 하나가 끝날 때마다 바이트가 한꺼번에 늘어난다
            size = rnd.choice([30 * MB, 1 * MB // 2, 3 * MB, 80 * MB, 100 * 1024])
            cost = size / (20 * MB) + rnd.choice([0.02, 0.4, 0.05])
            if cost > pending + 3:
                break
            pending -= cost
            t.add(size, 1)
        snap = t.snapshot()
        speeds.append(snap.speed_bps)
        etas.append(snap.eta_seconds)
        inst.append((t.done_bytes - prev_done) / 0.5)
        prev_done = t.done_bytes
    return speeds, etas, inst


def test_speed_display_is_smoothed_not_instantaneous():
    speeds, _etas, inst = bursty_run()
    steady = speeds[len(speeds) // 4:]
    inst_steady = inst[len(inst) // 4:]
    assert max(inst_steady) / max(1.0, min(x for x in inst_steady if x > 0)) > 20  # 순간 속도는 크게 흔들린다(재현)
    assert max(steady) / min(steady) < 2.5  # 표시 속도는 안정적이다
    # 연속한 두 표시 사이의 변화도 작다(15 → 1.5 → 25 → 1 같은 널뛰기가 없다)
    jumps = [abs(b - a) / a for a, b in zip(steady, steady[1:]) if a > 0]
    assert max(jumps) < 0.35


def test_eta_counts_down_smoothly_and_does_not_jump():
    speeds, etas, _ = bursty_run()
    steady = [e for e in etas[len(etas) // 4:] if e is not None]
    rel = [abs(b - (a - 0.5)) / a for a, b in zip(steady, steady[1:]) if a > 5]
    assert max(rel) < 0.2  # 직전 값에서 흐른 시간을 뺀 값에서 크게 벗어나지 않는다


def test_eta_converges_to_new_speed_after_a_real_change():
    clock = Clock()
    t = ProgressTracker("copy", 10 ** 12, 1000, clock=clock)
    for _ in range(60):  # 100MB/s 로 30초
        clock.t += 0.5
        t.add(50 * MB)
        t.snapshot()
    for _ in range(240):  # 10MB/s 로 2분: 속도가 정말 바뀌었으니 표시도 따라가야 한다
        clock.t += 0.5
        t.add(5 * MB)
        snap = t.snapshot()
    assert snap.speed_bps == pytest.approx(10 * MB, rel=0.15)


def test_speed_with_huge_files_does_not_saw_tooth():
    """복사 도구는 파일을 시작할 때 그 크기를 한꺼번에 알린다. 1GB 파일이 40초씩 걸려도 표시 속도(약 25MB/s)가 널뛰지 않는다."""
    clock = Clock()
    t = ProgressTracker("copy", 10 ** 13, 1000, clock=clock)
    seen = []
    for i in range(1, 801):  # 0.5초 간격으로 400초
        clock.t += 0.5
        if i % 80 == 1:
            t.add(1024 * MB, 1)
        sp = t.snapshot().speed_bps
        if clock.t > 160:
            seen.append(sp)
    assert min(seen) > 0.55 * 25.6 * MB and max(seen) < 1.5 * 25.6 * MB


# =========================================================================== 5) 중지

def test_checkpoint_only_acts_inside_bind():
    tok = CancelToken()
    tok.cancel()
    checkpoint()  # 묶이지 않았으면 아무 일도 없다
    with bind(tok):
        with pytest.raises(Cancelled):
            checkpoint()
        with bind(None):  # 중첩: 안쪽이 우선
            checkpoint()
        with pytest.raises(Cancelled):
            checkpoint()
    checkpoint()
    assert cancel_mod.current() is None


def test_checkpoint_is_per_thread():
    tok = CancelToken()
    tok.cancel()
    seen = []
    with bind(tok):
        th = threading.Thread(target=lambda: seen.append(cancel_mod.current()))
        th.start()
        th.join()
    assert seen == [None]


def test_subprocess_wait_is_cancellable_and_kills_child():
    """winget 같은 하위 프로세스를 기다리는 동안 중지하면 짧은 시간 안에 멈추고 프로세스를 정리한다."""
    tok = CancelToken()
    threading.Timer(0.6, tok.cancel).start()
    t0 = time.monotonic()
    holder = []
    real_popen = subprocess.Popen

    def spy(*a, **k):
        p = real_popen(*a, **k)
        holder.append(p)
        return p
    with bind(tok), pytest.MonkeyPatch.context() as mp:
        mp.setattr(detect.subprocess, "Popen", spy)
        with pytest.raises(Cancelled):
            detect._subprocess_runner([sys.executable, "-c", "import time; time.sleep(60)"])
    assert time.monotonic() - t0 < 6
    assert holder and holder[0].poll() is not None  # 종료됨


def test_subprocess_runner_still_returns_output_and_code():
    code, out = detect._subprocess_runner([sys.executable, "-c", "print('hello'); import sys; sys.exit(3)"])
    assert code == 3 and "hello" in out


def test_discover_tree_scan_stops_on_cancel(tmp_path):
    for i in range(300):
        d = tmp_path / f"d{i}"
        d.mkdir()
        (d / "f.txt").write_text("x")
    tok = CancelToken()
    tok.cancel()
    with bind(tok), pytest.raises(Cancelled):
        discover.scan_tree(tmp_path, time.monotonic() + 30, time.monotonic)


# ------------------------------------------------------------------ 묶음 만들기/가져오기

def make_rule(rid="demo"):
    return parse_rule({"id": rid, "name": "Demo", "version": "7", "detect": {"display_name_patterns": ["Demo"]},
                       "paths": [{"path": "%ROAM%\\Vendor\\Demo\\Support", "dest": "demo/support", "kind": "dir"}],
                       "manual_warnings": []})


@pytest.fixture
def pk(tmp_path):
    old = tmp_path / "old"
    sup = old / "Vendor" / "Demo" / "Support"
    sup.mkdir(parents=True)
    for i in range(6):
        (sup / f"f{i}.cfg").write_bytes((f"file-{i}-" * 300).encode())
    return SimpleNamespace(tmp=tmp_path, old=old, sup=sup, out=tmp_path / "out" / "p.zip",
                           expand=lambda s: s.replace("%ROAM%", str(old)))


def leftovers(out: Path):
    names = {p.name for p in out.parent.iterdir()}
    return {n for n in names if n.endswith(".lock") or n.startswith(".pcmigrate-stage") or ".pcmigrate" in n and n != out.name}


def test_export_cancel_between_files_keeps_resumable_savepoint_and_cleans_lock(pk):
    tok = CancelToken()
    count = {"n": 0}

    def progress(_info):
        count["n"] += 1
        if count["n"] >= 2:
            tok.cancel()
    with bind(tok), pytest.raises(Cancelled):
        export_package([make_rule()], pk.out, [], None, expand=pk.expand, now=NOW, progress_info=progress,
                       savepoint_bytes=1)
    part, journal = part_paths(pk.out)
    assert not pk.out.exists()  # 완성본은 만들지 않았다
    assert part.exists() and journal.exists()  # 이어받을 수 있는 상태
    info = inspect_savepoint(pk.out)
    assert info and info["usable"]
    assert not [p for p in pk.out.parent.iterdir() if p.name.endswith(".lock") or "stage" in p.name]  # 락/임시 폴더 정리
    assert all((pk.sup / f"f{i}.cfg").exists() for i in range(6))  # 원본은 그대로
    # 다시 내보내면 이어서 끝낸다
    m = export_package([make_rule()], pk.out, [], None, expand=pk.expand, now=NOW, savepoint_bytes=1)
    assert pk.out.exists() and len(m["files"]) == 6 and not part.exists() and not journal.exists()
    assert verify_package(pk.out)[1] == []


def test_export_cancel_inside_big_file_stops_within_chunks(pk, monkeypatch):
    """큰 파일 하나를 읽는 중에도 청크마다 취소를 확인한다(파일이 끝날 때까지 기다리지 않는다)."""
    monkeypatch.setattr(package, "CHUNK", 1024)
    big = pk.sup / "big.bin"
    big.write_bytes(os.urandom(2 * MB))
    reads = {"n": 0}
    tok = CancelToken()
    real_checkpoint = package.checkpoint

    def counting():
        reads["n"] += 1
        if reads["n"] == 5:
            tok.cancel()
        real_checkpoint()
    monkeypatch.setattr(package, "checkpoint", counting)
    with bind(tok), pytest.raises(Cancelled):
        export_package([make_rule()], pk.out, [], None, expand=pk.expand, now=NOW)
    assert reads["n"] < 40  # 2MB/1KB = 2048 청크를 모두 읽지 않고 곧바로 멈춘다
    assert not pk.out.exists() and big.stat().st_size == 2 * MB


def test_export_pre_cancelled_stops_during_file_discovery(pk):
    tok = CancelToken()
    tok.cancel()
    with bind(tok), pytest.raises(Cancelled):
        export_package([make_rule()], pk.out, [], None, expand=pk.expand, now=NOW)
    assert not pk.out.exists()
    assert not [p for p in pk.out.parent.iterdir() if p.name.endswith(".lock") or "stage" in p.name]


def build_package(pk):
    export_package([make_rule()], pk.out, [], None, expand=pk.expand, now=NOW)
    return pk.out


def test_package_check_and_preview_stop_on_cancel(pk):
    out = build_package(pk)
    before = out.read_bytes()
    tok = CancelToken()
    tok.cancel()
    with bind(tok):
        with pytest.raises(Cancelled):
            verify_package(out)  # 가져오기 검사
        with pytest.raises(Cancelled):
            plan_import(out, [make_rule()], expand=lambda s: s.replace("%ROAM%", str(pk.tmp / "new")))  # 미리보기
    assert out.read_bytes() == before


def test_apply_cancel_between_files_leaves_no_half_written_files(pk):
    out = build_package(pk)
    new = pk.tmp / "new"
    exp = lambda s: s.replace("%ROAM%", str(new))  # noqa: E731
    plan = plan_import(out, [make_rule()], expand=exp)
    assert not plan.problems
    tok = CancelToken()
    seen = {"n": 0}

    def progress(info):
        seen["n"] += 1
        tok.cancel()
    with bind(tok), pytest.raises(Cancelled):
        apply_import(out, plan, progress_info=progress)
    target = new / "Vendor" / "Demo" / "Support"
    done = sorted(target.glob("*.cfg")) if target.exists() else []
    assert len(done) < 6  # 중간에 멈췄다
    for f in done:  # 복원된 파일은 원본과 같다
        assert f.read_bytes() == (pk.sup / f.name).read_bytes()
    assert not list(new.rglob("*.pcmigrate-part")) and not list(new.rglob("*.part"))  # 반쪽 임시 파일 없음
    assert all((pk.sup / f"f{i}.cfg").exists() for i in range(6)) and zipfile.is_zipfile(out)
    # 다시 복원하면 나머지를 이어서 한다(이미 같은 파일은 건너뜀)
    plan2 = plan_import(out, [make_rule()], expand=exp)
    apply_import(out, plan2)
    assert len(list(target.glob("*.cfg"))) == 6


def test_retry_export_cancel_removes_retry_temp(pk, monkeypatch):
    out = build_package(pk)
    tok = CancelToken()
    tok.cancel()
    # 읽지 못한 파일이 없으면 곧바로 끝나므로, 검사 단계(verify_package)에서 취소되는지만 확인한다
    with bind(tok), pytest.raises(Cancelled):
        package.retry_failed_export(out, [make_rule()], expand=pk.expand)
    assert not package.retry_paths(out).exists() and out.exists()
    assert not [p for p in out.parent.iterdir() if p.name.endswith(".lock")]


# ------------------------------------------------------------------ 컨트롤러: 취소가 실제로 작업 안까지 닿는다

class SlowApi(FakeApi):
    """취소 신호를 checkpoint() 로만 보는 가짜 느린 작업들(인자로 취소 토큰을 받지 않는 api)."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.ignore_cancel = False

    def _slow(self, name):
        self.calls.append((name,))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            if self.ignore_cancel:
                if self.release.wait(0.01):
                    break
            else:
                checkpoint()
                time.sleep(0.005)
        return SimpleNamespace(program_count=0, matched=[], discovery=(0, 0), match_info=[], checklist=None, text="",
                               manifest={"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []},
                               plan=SimpleNamespace(problems=[], items=[], manifest={}, warnings=[]), mode="dry-run")

    def run_scan(self, rules_dir, deps=None):
        return self._slow("scan")

    def run_checklist(self, rules_dir, **kw):
        return self._slow("checklist")

    def run_export(self, rules_dir, output, **kw):
        return self._slow("export")

    def run_import(self, package, rules_dir, **kw):
        return self._slow("import")

    def run_install_check(self, package, deps=None, **kw):
        return self._slow("install")


@pytest.fixture
def senv(tmp_path):
    api = SlowApi()
    c = Controller(api_mod=api, runner=Runner(), rules_dir=tmp_path,
                   make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    events = []
    c.listener = lambda k, p=None: events.append((k, p))
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(b"x")
    return c, api, events, pkg


def start(c, api, pkg, tmp_path_out, which):
    if which == "scan":
        c.start_scan()
    elif which == "checklist":
        c.state.scan = SimpleNamespace(matched=[], program_count=0)
        c.start_checklist()
    elif which == "export":
        c.start_export(tmp_path_out, write_confirmed=True)
    elif which == "preview":
        c.state.package = str(pkg)
        c.start_preview()
    elif which == "install":
        c.state.package = str(pkg)
        c.start_install_check()


@pytest.mark.parametrize("which", ["scan", "checklist", "export", "preview", "install"])
def test_every_long_task_is_cancellable_and_stops_fast(senv, tmp_path, which):
    c, api, events, pkg = senv
    start(c, api, pkg, str(tmp_path / "o.zip"), which)
    assert c.state.busy and c.can_cancel  # 취소 단추가 항상 보인다
    time.sleep(0.05)
    t0 = time.monotonic()
    assert c.cancel() is True
    assert ("cancelling", None) in events and c.cancel_requested_at is not None
    assert wait_until(c, lambda: not c.state.busy, 5)
    assert time.monotonic() - t0 < 3
    assert any(k == "cancelled" for k, _ in events) and not any(k == "error" for k, _ in events)
    assert c.cancel_token is None and c.cancel() is False


def test_cancel_twice_is_harmless_and_message_appears_once(senv, tmp_path):
    c, api, events, pkg = senv
    c.start_scan()
    c.cancel()
    first = c.cancel_requested_at
    c.cancel()
    assert c.cancel_requested_at == first
    assert [k for k, _ in events].count("cancelling") == 1
    assert wait_until(c, lambda: not c.state.busy)


def test_force_stop_button_appears_after_timeout_and_recovers_ui(senv, tmp_path):
    c, api, events, pkg = senv
    api.ignore_cancel = True  # 취소 신호를 못 보는(멈춰 버린) 작업
    now = {"t": 100.0}
    c.clock = lambda: now["t"]
    c.start_scan()
    assert not c.force_stop_ready()
    c.cancel()
    now["t"] += c.force_stop_after - 1
    assert not c.force_stop_ready()
    now["t"] += 2
    assert c.force_stop_ready()  # 15초가 지나도 못 멈추면 강제로 멈추기를 보인다
    assert c.force_stop() is True
    assert not c.state.busy and ("cancelled", T.TASK_SCAN) in events
    assert ("force_stopped", T.TASK_SCAN) in events
    # 화면이 복구됐으니 곧바로 새 작업을 시작할 수 있고, 늦게 끝난 옛 작업의 결과는 버려진다
    api.release.set()
    time.sleep(0.2)
    c.pump()
    assert "scan_done" not in kinds(events)
    api.ignore_cancel = False
    events.clear()
    c.start_scan()
    assert c.state.busy
    c.cancel()
    assert wait_until(c, lambda: not c.state.busy)
    assert "scan_done" not in kinds(events)


def test_force_stop_without_task_is_noop(senv):
    c, api, events, pkg = senv
    assert c.force_stop() is False and c.force_stop_ready() is False


def test_auto_stop_cancels_running_task_immediately(senv, tmp_path):
    c, api, events, pkg = senv
    c.auto_run("old")  # 첫 단계(조사)가 느린 작업으로 돈다
    assert c.state.busy and c.can_cancel
    assert c.auto_stop() is True and c.cancel_requested_at is not None
    assert wait_until(c, lambda: not c.state.busy, 5)
    assert c.auto.status == "stopped" and c.auto.by_user




# ------------------------------------------------------------------ 추가: 원인 재현, 실제 하위 프로세스/긴 파일 반응 시간

def test_old_behaviour_reproduced_incomplete_rescan_blocked_without_secret_names(tmp_path):
    """원인 재현(진짜 재검사): 시간 초과로 다 못 본 선택 항목은 비밀 이름이 없어도 '비밀 정보' 오류로 묶음을 막았다(기본=CLI 는 지금도 엄격)."""
    d = tmp_path / "Acme"
    d.mkdir()
    (d / "a.cfg").write_text("x", encoding="utf-8")
    item_ = ExtraItem("%APPDATA%", "Acme", d, True)
    real_secret_names = real_api.secret_names
    import pytest as _pt
    with _pt.MonkeyPatch.context() as mp:
        mp.setattr(real_api, "secret_names", lambda it: real_secret_names(it, per_limit=-1.0))  # 데드라인이 이미 지난 상태
        with pytest.raises(real_api.ApiError) as e:
            real_api.prepare_extras(None, False, items=[item_])
        assert "비밀 정보" in e.value.message
        assert real_api.prepare_extras(None, False, items=[item_], allow_unverified=True).risky == 0


def test_cli_still_strict_about_unverified(extra_dir, monkeypatch):
    monkeypatch.setattr(real_api, "secret_names", lambda item: ([], False))
    with pytest.raises(real_api.ApiError):
        real_api.prepare_extras(None, False, items=[extra_dir])


class Child:
    """오래 자는 파이썬 자식 프로세스 스크립트."""
    ARGS = [sys.executable, "-c", "import time; time.sleep(60)"]


def test_transfer_runner_cancel_kills_child_quickly():
    """robocopy 자리에 실제 하위 프로세스(60초 자는 파이썬)를 넣어 취소 후 2초 안에 정리되는지."""
    tok = CancelToken()
    spawned = []
    real_popen = subprocess.Popen

    def spy(*a, **k):
        p = real_popen(*a, **k)
        spawned.append(p)
        return p
    run = transfer.make_runner(tok, poll=0.05)
    threading.Timer(0.5, tok.cancel).start()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transfer.subprocess, "Popen", spy)
        t0 = time.monotonic()
        with pytest.raises(Cancelled):
            run(Child.ARGS)
        waited = time.monotonic() - t0
    assert waited < 0.5 + 2.0
    assert spawned and spawned[0].poll() is not None


def test_transfer_runner_checkpoint_binding_also_cancels():
    tok = CancelToken()
    run = transfer.make_runner(None, poll=0.05)  # cancel 인자 없이도 스레드에 묶인 토큰을 본다
    threading.Timer(0.4, tok.cancel).start()
    t0 = time.monotonic()
    with bind(tok), pytest.raises(Cancelled):
        run(Child.ARGS)
    assert time.monotonic() - t0 < 3


def test_detect_runner_cancel_reaction_under_two_seconds():
    tok = CancelToken()
    threading.Timer(0.3, tok.cancel).start()
    t0 = time.monotonic()
    with bind(tok), pytest.raises(Cancelled):
        detect._subprocess_runner(Child.ARGS)
    assert time.monotonic() - t0 < 2.3


def test_long_file_hash_stops_within_two_seconds_after_cancel(tmp_path):
    big = tmp_path / "big.bin"
    with open(big, "wb") as f:
        for _ in range(64):
            f.write(os.urandom(MB))  # 64MB
    tok = CancelToken()
    seen = {"n": 0}

    def on_bytes(n):
        seen["n"] += n
        if seen["n"] >= 2 * MB:
            tok.cancel()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        sha256_file(big, 64 * 1024, tok, on_bytes)
    assert time.monotonic() - t0 < 2.0 and seen["n"] < 64 * MB
    assert big.stat().st_size == 64 * MB


def test_output_lock_not_removed_by_a_force_stopped_old_run(tmp_path):
    """강제 중단된 옛 스레드가 나중에 release 해도, 같은 PID 의 새 실행이 잡은 락을 지우지 않는다."""
    out = tmp_path / "p.zip"
    old = package._OutputLock(out)
    old.acquire()
    os.unlink(old.path)  # 강제 중단 뒤 새 실행이 낡은 락을 치우고 새로 잡은 상황
    new = package._OutputLock(out)
    new.acquire()
    old.release()
    assert new.path.exists()
    new.release()
    assert not new.path.exists()


def test_force_stop_runs_cleanup_hooks_and_blocks_writing_tasks_until_old_thread_ends(senv, tmp_path):
    c, api, events, pkg = senv
    api.ignore_cancel = True
    hooks = []
    c.cleanup_hooks.append(lambda: hooks.append(1))
    c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)
    c.cancel()
    assert c.force_stop() is True and hooks == [1] and not c.state.busy
    with pytest.raises(GuardError) as e:  # 옛 스레드가 아직 같은 파일을 쓸 수 있으니 쓰기 작업은 막는다
        c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)
    assert str(e.value) == T.G_BUSY
    c.start_scan()  # 읽기 전용 작업은 가능
    c.cancel()
    api.release.set()  # 옛 스레드가 끝난다
    assert wait_until(c, lambda: not c.state.busy, 5)
    time.sleep(0.2)
    api.ignore_cancel = False
    c.start_export(str(tmp_path / "o.zip"), write_confirmed=True)  # 이제 가능
    c.cancel()
    assert wait_until(c, lambda: not c.state.busy, 5)


def test_progress_warmup_first_seconds_are_not_wild():
    clock = Clock()
    t = ProgressTracker("copy", 10 ** 12, 1000, clock=clock)
    seen = []
    for i in range(10):  # 처음 5초: 파일 크기가 들쭉날쭉
        clock.t += 0.5
        t.add([200, 1, 80, 5, 150][i % 5] * MB // 10, 1)
        seen.append(t.snapshot().speed_bps)
    assert max(seen) < 8 * min(seen)
