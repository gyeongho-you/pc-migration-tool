"""stage67: 기능 리뷰어 1(데이터 전송) 지적 수정의 재현 시험. 가짜 환경만 쓴다(임시 폴더, 가짜 robocopy, 루프백, 가짜 API)."""
import json
import os
from types import SimpleNamespace

import pytest

from pcmigrate import cli, drop_stage as ds, lan_api, lan_protocol as P, lan_transfer as LT, multi_transfer as mt, scan
from pcmigrate.cancel import Cancelled
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SelectedItem
from pcmigrate.transfer import DriveDisconnected, TransferError, transfer

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_lan_transfer import _fast_kdf, make_src, rx_factory, user_tree  # noqa: F401


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


# ---------------------------------------------------------------- F1 LAN 조사 단계 읽기 실패
def with_scan_error(monkeypatch):
    real = LT.scan_source_info

    def fake(src, **kw):
        sc = real(src, **kw)
        sc.errors.append(("locked_dir", "permission", "접근 권한이 없어 읽을 수 없습니다"))
        return sc
    monkeypatch.setattr(LT, "scan_source_info", fake)


def test_lan_scan_errors_count_as_failures(rx_factory, tmp_path, monkeypatch):
    src = make_src(tmp_path / "src", {"a/f.txt": b"hello"})
    with_scan_error(monkeypatch)
    rx = rx_factory()
    rep = lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, sources=[str(src)], options={"chunk": 4096})
    assert rep["failed_count"] == 1 and rep["success"] is False
    assert rep["failed"][0]["path"].endswith("locked_dir") and "읽을 수 없습니다" in rep["failed"][0]["error"]
    assert user_tree(tmp_path / "dst")  # 읽은 파일은 계속 보냈다


def test_lan_cli_shows_scan_errors_and_nonzero_exit(rx_factory, tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path / "src", {"a/f.txt": b"hello"})
    with_scan_error(monkeypatch)
    rx = rx_factory()
    code = cli.main(["send", f"127.0.0.1:{rx.port}", "--code", rx.code, str(src)])
    out = capsys.readouterr().out
    assert code == 1 and "locked_dir" in out and "읽을 수 없습니다" in out


def test_gui_lan_result_lists_first_failures():
    from pcmigrate.gui.controller import Controller
    from pcmigrate.gui.runner import Runner
    from test_gui_controller import FakeApi
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=".")
    c.files.tx_report = {"success": False, "files": 3, "verification": {"exact": 3, "mismatch": 0, "missing": 0}, "failed_count": 7,
                         "failed": [{"path": f"D/p{i}", "error": "접근 권한이 없습니다"} for i in range(7)]}
    _t, ok, notes = c.tx_result_lines()
    text = "\n".join(notes)
    assert not ok and "D/p0" in text and "D/p4" in text and "D/p5" not in text and "외 2" in text


# ---------------------------------------------------------------- F2 대상이 원본의 부모 + 같은 이름 하위 폴더
def test_dst_parent_of_src_with_same_named_subfolder_is_refused(tmp_path):
    src = tmp_path / "P" / "A"
    put(src / "A" / "f.txt", b"x")
    put(src / "top.txt", b"t")
    before = sorted(str(p.relative_to(src)) for p in src.rglob("*"))
    with pytest.raises(TransferError, match="상위 폴더"):
        transfer(src, tmp_path / "P", keep_awake=False)
    assert before == sorted(str(p.relative_to(src)) for p in src.rglob("*"))  # 원본에 아무것도 생기지 않았다


def test_dst_parent_without_collision_is_still_allowed(tmp_path):
    src = tmp_path / "P" / "A"
    put(src / "top.txt", b"t")
    transfer(src, tmp_path / "P", keep_awake=False, runner=FakeRobo(), dest_info_fn=lambda p: deps_with(FakeRobo()).transfer.keywords["dest_info_fn"](p))


# ---------------------------------------------------------------- F3 중단 리포트
def test_cli_single_transfer_saves_aborted_report_on_cancel_and_errors(tmp_path, monkeypatch):
    src = tmp_path / "s"
    put(src / "a.txt", b"1")
    for exc, cancelled in ((Cancelled(), True), (DriveDisconnected("드라이브가 분리됨"), False), (TransferError("공간 부족"), False)):
        rpt = tmp_path / f"r{type(exc).__name__}.json"

        def boom(*a, **k):
            raise exc
        monkeypatch.setattr(cli, "run_transfer", boom)
        try:
            cli.main(["transfer", str(src), str(tmp_path / "d"), "--report", str(rpt)])
        except (Cancelled, SystemExit, KeyboardInterrupt):
            pass
        d = json.loads(rpt.read_text(encoding="utf-8"))
        assert d["aborted"] is True and d["success"] is False and d["cancelled"] is cancelled


def test_cli_selection_saves_aborted_report_without_extra_file(tmp_path, monkeypatch):
    from pcmigrate import data_api
    a = tmp_path / "a"
    put(a / "f.txt", b"1")
    sel = tmp_path / "s.json"
    from pcmigrate.find_data import SELECTION_FORMAT
    sel.write_text(json.dumps({"format": SELECTION_FORMAT, "version": 1, "items": [{"path": str(a), "include": True}]}), encoding="utf-8")

    def boom(*a, **k):
        raise DriveDisconnected("드라이브가 분리됨")
    monkeypatch.setattr(data_api, "run_transfer", boom)
    rpt = tmp_path / "r.json"
    cli.main(["transfer", "--from-selection", str(sel), str(tmp_path / "out"), "--report", str(rpt)])
    d = json.loads(rpt.read_text(encoding="utf-8"))
    assert d["aborted"] is True and d["success"] is False


def test_gui_single_transfer_saves_report_on_error_and_on_blocked_start(tmp_path):
    from pcmigrate.gui.controller import Controller, GuardError
    from pcmigrate.gui.runner import Runner
    from test_gui_controller import FakeApi
    api = FakeApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    events = []
    c.listener = lambda k, p=None: events.append(k)
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    api.transfer_error = DriveDisconnected("드라이브가 분리됨")
    rpt = tmp_path / "r.json"
    c.start_transfer(dry_run=True, report_path=str(rpt))
    assert "error" in events and json.loads(rpt.read_text(encoding="utf-8"))["aborted"] is True
    c.state.transfer_src = str(tmp_path / "없는 폴더")  # 시작 전 점검 실패(가드)도 리포트를 남긴다
    rpt2 = tmp_path / "r2.json"
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=True, report_path=str(rpt2))
    assert json.loads(rpt2.read_text(encoding="utf-8"))["aborted"] is True


# ---------------------------------------------------------------- F4 예상 밖 예외 한 건이 나머지를 막지 않는다
def test_unexpected_exception_in_one_item_does_not_stop_the_rest(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a / "f.txt", b"a")
    put(b / "f.txt", b"b")
    calls = []

    def fake(src, dst, **kw):
        calls.append(src.name)
        if src.name == "a":
            raise KeyError("내부 오류")
        from pcmigrate.copier import Report
        return Report(dry_run=False, results=[], meta={"verification_complete": True})
    rep = mt.run_multi([str(a), str(b)], tmp_path / "out", fake, dest_info_fn=lambda p: SimpleNamespace(kind="fixed", fs="NTFS", free=10 ** 12))
    assert calls == ["a", "b"] and [i.status for i in rep.items] == ["error", "ok"]
    assert rep.to_dict()["failed_count"] == 1


# ---------------------------------------------------------------- L1 건너뜀 안내와 정리 안내가 모순되지 않는다
def test_summary_does_not_say_clean_up_when_items_were_skipped():
    from pcmigrate.copier import Report
    rep = mt.MultiReport(dry_run=False)
    r = Report(dry_run=False, results=[], meta={"verification_complete": True, "cloud_only_count": 2, "skipped_links": 1})
    r.results.append(__import__("pcmigrate.copier", fromlist=["FileResult"]).FileResult("s", "", "cloud_only", error="x", reason="cloud_only"))
    rep.items.append(mt.ItemResult("D:\\a", "o\\a", "ok", r))
    text = "\n".join(mt.summary_lines(rep, "o"))
    assert "건너뛴 항목" in text and "구 PC 에 그대로 두세요" in text
    clean = mt.MultiReport(dry_run=False)
    clean.items.append(mt.ItemResult("D:\\a", "o\\a", "ok", Report(dry_run=False, results=[], meta={"verification_complete": True})))
    assert "건너뛴 항목" not in "\n".join(mt.summary_lines(clean, "o"))


# ---------------------------------------------------------------- L2 공간 추정: 남이 만든 폴더의 파일은 '이미 있는 양'으로 빼지 않는다
def test_space_precheck_does_not_subtract_foreign_existing_files(tmp_path):
    src = tmp_path / "src"
    put(src / "big.bin", b"x" * 1000)
    out = tmp_path / "out"
    rel = mt.dest_rel(str(src))
    put(out / rel / "big.bin", b"y" * 1000)  # 같은 크기, 다른 내용(이 도구가 담은 것이 아님: 저널 없음)
    plan = mt.plan_items([str(src)], out)
    sp = mt.precheck_space(plan, out, dest_info_fn=lambda p: SimpleNamespace(kind="fixed", fs="NTFS", free=10 ** 12))
    assert sp["existing_bytes"] == 0
    (out / mt.JOURNAL_NAME).write_text(json.dumps({"version": 1, "items": {rel.lower(): {"src": str(src), "rel": rel, "status": "failed"}}}), encoding="utf-8")
    sp = mt.precheck_space(mt.plan_items([str(src)], out), out, dest_info_fn=lambda p: SimpleNamespace(kind="fixed", fs="NTFS", free=10 ** 12))
    assert sp["existing_bytes"] == 1000  # 이 도구가 이전에 담은 폴더(이어받기)는 뺀다


# ---------------------------------------------------------------- L3 manifest 덮어쓰기
def test_second_lan_receive_keeps_previous_manifest(rx_factory, tmp_path):
    a = make_src(tmp_path / "a", {"x.txt": b"x"})
    b = make_src(tmp_path / "b", {"y.txt": b"yy"})
    rx = rx_factory()
    lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, sources=[str(a)], options={"chunk": 4096})
    rx2 = rx_factory(tmp_path / "dst")
    lan_api.run_send(f"127.0.0.1:{rx2.port}", rx2.code, sources=[str(b)], options={"chunk": 4096})
    names = sorted(p.name for p in (tmp_path / "dst").iterdir() if p.name.startswith(".pcmigrate-manifest"))
    assert ".pcmigrate-manifest.jsonl" in names and any(n.startswith(".pcmigrate-manifest.1") for n in names)


# ---------------------------------------------------------------- L4 같은 내용이면 .bak 으로 옮기지 않는다
def test_same_content_existing_file_is_not_moved_to_bak(tmp_path):
    import hashlib
    f = tmp_path / "x.txt"
    f.write_bytes(b"same")
    assert LT._same_content(f, 4, hashlib.sha256(b"same").hexdigest())
    assert not LT._same_content(f, 4, hashlib.sha256(b"diff").hexdigest())
    assert not LT._same_content(tmp_path / "none", 4, "0" * 64)


# ---------------------------------------------------------------- L5 하위 경로의 .pcmigrate-*
@pytest.mark.parametrize("bad", ["d/.pcmigrate-state.json", "a/b/.pcmigrate-x/y", "a/.PCMIGRATE-multi.json"])
def test_validate_rel_rejects_own_names_in_subfolders(bad):
    with pytest.raises(P.UnsafePath):
        P.validate_rel(bad)


# ---------------------------------------------------------------- L6 LOCALAPPDATA 미정의
def test_staging_root_without_localappdata_never_creates_percent_folder(tmp_path, monkeypatch):
    monkeypatch.undo()  # conftest 의 STAGING_DIR 대체를 풀고 실제 기본값으로 시험(환경변수만 가짜)
    monkeypatch.setattr(ds, "STAGING_DIR", r"%LOCALAPPDATA%\pcmigrate\staging")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    from pcmigrate import envpaths
    monkeypatch.setattr(envpaths, "lookup", lambda name: None)
    monkeypatch.chdir(tmp_path)
    root = ds.staging_root()
    assert root.is_absolute() and "%" not in str(root)
    ds.new_stage_dir(tmp_path / "x") if False else None
    assert not any("%" in p.name for p in tmp_path.iterdir())


# ---------------------------------------------------------------- L7 정크 제외 + 겹침으로 두 경로에서 다 빠지는 경우
def test_junk_excluded_file_inside_checked_folder_is_still_sent_when_dropped(tmp_path):
    base = tmp_path / "pc" / "A"
    put(base / "keep.txt", b"k")
    put(base / "Thumbs.db", b"junk")
    out = tmp_path / "out"
    rep = run_transfer_selection([SelectedItem(str(base), True)], out, deps=deps_with(FakeRobo()), keep_awake=False, exclude_junk=True,
                                 extra_files=[base / "Thumbs.db", base / "keep.txt"])
    d = rep.to_dict()
    rel = mt.dest_rel(str(base))
    assert (out / rel / "Thumbs.db").read_bytes() == b"junk"  # 고른 정크 파일은 보낸다(폴더 전송과 겹침 모두에서 빠지지 않는다)
    assert d["drop_stage"]["overlap_count"] == 1  # keep.txt 는 폴더에 포함되어 빠짐
    assert d["success"]


def test_overlap_ignores_checked_folder_that_vanished(tmp_path):
    base = tmp_path / "A"
    put(base / "f.txt", b"1")
    gone = tmp_path / "gone"
    plan = ds.plan_drops([ds.describe(base / "f.txt")], [str(gone), str(base)])
    assert not plan.files  # 존재하는 폴더에는 포함
    plan = ds.plan_drops([ds.describe(base / "f.txt")], [str(base / "none")])
    assert [os.path.basename(f[0]) for f in plan.files] == ["f.txt"]  # 없어진 폴더는 겹침 판정에 쓰지 않는다
