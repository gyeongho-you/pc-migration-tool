"""stage65: 독립 리뷰(A) 지적 수정의 재현 시험. 가짜 환경만 쓴다(임시 폴더, 가짜 robocopy, 가짜 Windows API, 루프백)."""
import os
import time
from pathlib import Path

import pytest

from pcmigrate import cli, drop_stage as ds, fs, lan_api, lan_transfer as LT, multi_transfer as mt
from pcmigrate.api import ApiError
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SelectedItem
from pcmigrate.transfer import TransferError

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_lan_transfer import _fast_kdf, make_src, rx_factory, user_tree  # noqa: F401


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


def run(items, extras, dst, **kw):
    return run_transfer_selection(items, dst, deps=deps_with(FakeRobo()), keep_awake=False, extra_files=extras, **kw)


# ---------------------------------------------------------------- ① 저널 키: 일부 파일 항목이 폴더 전체 skip_done 을 점유하지 않는다
def test_partial_staged_run_does_not_make_next_full_folder_run_skip(tmp_path):
    proj = tmp_path / "pc" / "proj"
    put(proj / "x.txt", b"x")
    put(proj / "y.txt", b"yy")
    put(proj / "sub" / "z.txt", b"zzz")
    dst = tmp_path / "out"
    r1 = run([], [proj / "x.txt"], dst)
    assert r1.to_dict()["success"]
    rel = mt.dest_rel(str(proj))
    assert sorted(p.name for p in (dst / rel).rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate")) == ["x.txt"]
    r2 = run_transfer_selection([SelectedItem(str(proj), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False, skip_done=True)
    assert [i.status for i in r2.items] != ["skipped_done"]  # 건너뛰지 않는다
    got = sorted(str(p.relative_to(dst / rel)) for p in (dst / rel).rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate"))
    assert got == ["sub\\z.txt", "x.txt", "y.txt"] or got == ["sub/z.txt", "x.txt", "y.txt"]


def test_full_run_done_still_skips_and_partial_flag_is_recorded(tmp_path):
    import json
    proj = tmp_path / "pc" / "proj"
    put(proj / "x.txt", b"x")
    dst = tmp_path / "out"
    run_transfer_selection([SelectedItem(str(proj), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False)
    r = run_transfer_selection([SelectedItem(str(proj), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False, skip_done=True)
    assert [i.status for i in r.items] == ["skipped_done"]  # 기존 동작 유지
    run([], [proj / "x.txt"], dst)
    j = json.loads((dst / mt.JOURNAL_NAME).read_text(encoding="utf-8"))
    assert all(v.get("partial") for v in j["items"].values())  # 일부 파일 실행이 폴더 완료 기록을 덮어씀(보수적)
    r = run_transfer_selection([SelectedItem(str(proj), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False, skip_done=True)
    assert [i.status for i in r.items] != ["skipped_done"]


# ---------------------------------------------------------------- ② 긴 경로
def deep_file(tmp_path, depth=3):
    d = tmp_path / "src"
    for _ in range(depth):
        d = d / ("d" * 90)
    os.makedirs(fs.long_path(str(d)), exist_ok=True)
    f = d / "x.txt"
    with open(fs.long_path(str(f)), "wb") as fh:
        fh.write(b"hi")
    return f


@pytest.mark.skipif(os.name != "nt", reason="Windows 긴 경로")
@pytest.mark.parametrize("no_link", [False, True])
def test_long_source_path_is_staged(tmp_path, no_link):
    f = deep_file(tmp_path)
    assert len(str(f)) > 260
    plan = ds.plan_drops([ds.describe(str(f))], [])
    assert len(plan.files) == 1 and not plan.failed
    root = tmp_path / "stg"
    root.mkdir()
    st = ds.new_stage_dir(root)
    stats = ds.stage_files(plan, st, no_link=no_link)
    assert not plan.failed and (stats["linked"] + stats["copied"]) == 1 and len(plan.files) == 1
    assert len(st.name) <= 20  # 임시 폴더 이름은 짧게
    assert ds.remove_stage_dir(st, root)["removed"]


def test_too_long_staging_path_gets_exact_reason(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_bytes(b"1")
    monkeypatch.setattr(ds, "MAX_STAGE_PATH", 10)
    plan = ds.plan_drops([ds.describe(str(f))], [])
    st = ds.new_stage_dir(tmp_path / "stg2") if (tmp_path / "stg2").mkdir() is None else None
    ds.stage_files(plan, st)
    assert not plan.files and "경로가 너무 깁니다" in plan.failed[0]["reason"] and "찾을 수 없습니다" not in plan.failed[0]["reason"]
    assert "폴더 추가" in plan.failed[0]["reason"] or "직접 폴더 추가" in plan.failed[0]["reason"]
    ds.remove_stage_dir(st, tmp_path / "stg2")


def test_winerror_206_is_reported_as_too_long():
    e = OSError(3, "x")
    e.winerror = 206
    assert "경로가 너무 깁니다" in ds.reason_text(e)


# ---------------------------------------------------------------- ③ dnd_win
tk = pytest.importorskip("tkinter")


def fake_api(log, fail_on_nth_subclass=None):
    n = {"sub": 0}

    def subclass(hwnd, cb):
        n["sub"] += 1
        if fail_on_nth_subclass and n["sub"] == fail_on_nth_subclass:
            raise OSError("boom")
        log.append(("subclass", hwnd))
        return 5000 + hwnd

    return {"wndproc_type": lambda f: f, "accept": lambda h, on: log.append(("accept", h, on)), "subclass": subclass,
            "restore": lambda h, o: log.append(("restore", h, o)), "query": lambda h: [], "call_orig": lambda o, h, m, w, l: ("orig", o, m)}


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"디스플레이/Tcl 없음: {e}")
    r.withdraw()
    tk.Frame(r).pack()
    tk.Label(r, text="x").pack()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def test_dnd_rollback_turns_off_accept_for_every_window_even_the_failing_last(root):
    from pcmigrate.gui import dnd_win
    log = []
    hook = dnd_win.DropHook(root, lambda p: None, is_admin_fn=lambda: False, api_factory=lambda: fake_api(log, fail_on_nth_subclass=3))
    assert hook.install() is False and hook.reason == dnd_win.REASON_ERROR
    ons = [h for k, h, *r in [x for x in log if x[0] == "accept" and x[2] is True]]
    offs = [x[1] for x in log if x[0] == "accept" and x[2] is False]
    assert ons and set(ons) <= set(offs)  # 켠 모든 핸들(실패한 마지막 포함)을 껐다
    assert any(x[0] == "restore" for x in log)


def test_dnd_destroy_messages_still_reach_original_proc(root):
    from pcmigrate.gui import dnd_win
    log = []
    hook = dnd_win.DropHook(root, lambda p: None, is_admin_fn=lambda: False, api_factory=lambda: fake_api(log))
    assert hook.install()
    hwnd = next(iter(hook._orig))
    hook._on_destroy(type("E", (), {"widget": root})())  # <Destroy> 이벤트 뒤에도
    assert hwnd in hook._orig  # 원래 프로시저를 잃지 않는다
    assert hook._proc(hwnd, 0x0002, 0, 0)[0] == "orig"  # WM_DESTROY 가 원래 프로시저로
    res = hook._proc(hwnd, dnd_win.WM_NCDESTROY, 0, 0)
    assert res[0] == "orig" and hwnd not in hook._orig  # WM_NCDESTROY 를 넘긴 뒤에 기록을 지운다
    assert hook._proc(hwnd, 0x0001, 0, 0) == 0  # 그 뒤 모르는 핸들은 안전하게 0


# ---------------------------------------------------------------- ④ 표식 갱신
def test_marker_is_refreshed_during_long_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "HEARTBEAT_SECONDS", 0.05)
    f = tmp_path / "a.txt"
    f.write_bytes(b"1")
    seen = {}

    def slow(items):
        stage = next(Path(it.path).parent for it in items if isinstance(it, ds.StagedItem))
        while stage.name != Path(items[0].path).parts[Path(items[0].path).parts.index(stage.name)]:
            break
        marker = [p for p in ds.staging_root().iterdir() if p.is_dir()][0] / ds.MARKER_NAME
        old = time.time() - 3 * 24 * 3600
        os.utime(marker, (old, old))
        time.sleep(0.4)
        seen["age"] = time.time() - marker.stat().st_mtime
        return mt.MultiReport(dry_run=False)
    ds.run_with_extras([], [str(f)], tmp_path / "o", slow)
    assert seen["age"] < 60  # 3일 전으로 돌려놓았는데 전송 중 다시 갱신됐다
    assert ds.cleanup_stale() == []


# ---------------------------------------------------------------- ⑤ 대상/출력 검사
def test_destination_inside_dropped_folder_is_refused(tmp_path):
    d = tmp_path / "drop"
    put(d / "a.txt", b"1")
    with pytest.raises(TransferError, match="직접 고른 폴더"):
        run([], [d], d / "out")
    assert not (d / "out").exists()


def test_manifest_or_report_inside_dropped_folder_is_refused(tmp_path):
    d = tmp_path / "drop"
    put(d / "a.txt", b"1")
    other = tmp_path / "other"
    put(other / "b.txt", b"1")
    with pytest.raises(ApiError):
        run([SelectedItem(str(other), True)], [d], tmp_path / "out", manifest_out=d / "m.jsonl")
    with pytest.raises(ApiError):
        run([SelectedItem(str(other), True)], [d], tmp_path / "out2", abort_report=d / "r.json")


def test_lan_scan_error_path_shows_original_not_staging(rx_factory, tmp_path, monkeypatch):
    f = tmp_path / "pc" / "a.txt"
    put(f, b"1")
    real = LT.scan_source_info

    def with_error(src, **kw):
        sc = real(src, **kw)
        sc.errors.append(("bad.txt", "denied", "접근할 수 없음"))
        return sc
    monkeypatch.setattr(LT, "scan_source_info", with_error)
    rx = rx_factory()
    rep = lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, selection=[], options={"chunk": 4096}, extra_files=[f])
    paths = [e["path"] for e in rep.get("scan_errors", [])] if "scan_errors" in rep else []
    plan_errs = paths or []
    assert str(ds.staging_root()).replace("\\", "/") not in " ".join(plan_errs).replace("\\", "/")
    # 계획 객체로도 직접 확인
    st_items = None
    p = LT.build_send_plan(None, [ds.StagedItem(str(tmp_path / "pc"), True, orig_dir=str(tmp_path / "pc"))], LT.SendOptions())
    assert p.scan_errors and str(tmp_path / "pc") in p.scan_errors[0]["path"]


def test_lan_cli_extra_file_with_flat_layout_rejected(tmp_path, capsys):
    f = tmp_path / "a.txt"
    f.write_bytes(b"1")
    assert cli.main(["send", "127.0.0.1:9", "--code", "123456", str(tmp_path), "--layout", "flat", "--extra-file", str(f)]) == 2
    assert "layout flat" in capsys.readouterr().err
