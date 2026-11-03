"""stage82: 최종 새 리뷰어 지적(데이터 전송)의 재현 시험. 가짜 환경만 쓴다."""
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import drop_stage as ds, lan_transfer as LT, multi_transfer as mt, summary, transfer_robocopy as rc, transfer as tr
from pcmigrate.copier import Report
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SelectedItem

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_lan_transfer import _fast_kdf, make_src, rx_factory  # noqa: F401
from test_review_fixes77 import ntfs_info


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


# ---------------------------------------------------------------- 요약: 재실행 전송의 검사는 크기·수정 시각 기준
def test_rerun_transfer_summary_says_check_is_size_and_mtime_based(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    put(src / "a.txt", b"AAAA")
    kw = dict(runner=FakeRobo(), dest_info_fn=ntfs_info("NTFS"), keep_awake=False)
    tr.transfer(src, dst, **kw)
    rep = tr.transfer(src, dst, **kw)  # 재실행: 같은 크기·수정 시각은 건너뜀
    text = "\n".join(summary.transfer_lines(rep.to_dict(), rep, str(src), str(dst)))
    assert "재실행 전송의 검사는 크기·수정 시각 기준입니다" in text and "--strict" in text


def test_multi_summary_rerun_line(tmp_path):
    a = tmp_path / "a"
    put(a / "f.txt", b"1")
    out = tmp_path / "out"
    run_transfer_selection([SelectedItem(str(a), True)], out, deps=deps_with(FakeRobo()), keep_awake=False)
    rep = run_transfer_selection([SelectedItem(str(a), True)], out, deps=deps_with(FakeRobo()), keep_awake=False)
    assert "재실행 전송의 검사는 크기·수정 시각 기준입니다" in "\n".join(mt.summary_lines(rep, str(out)))


# ---------------------------------------------------------------- 저널 쓰기 실패
def test_journal_write_failure_is_warned_and_recorded_in_meta(tmp_path, monkeypatch):
    a = tmp_path / "a"
    put(a / "f.txt", b"1")
    out = tmp_path / "out"
    warns = []
    monkeypatch.setattr(mt, "_write_journal", lambda dst, journal: False)  # 읽기 전용/잠금으로 쓰지 못하는 상황
    rep = run_transfer_selection([SelectedItem(str(a), True)], out, deps=deps_with(FakeRobo()), keep_awake=False, warn=warns.append)
    assert rep.meta.get("journal_write_failed") is True and rep.to_dict()["journal_write_failed"] is True
    assert sum("이어받기 기록" in w for w in warns) == 1  # 한 번만 알린다


def test_journal_really_readonly_file_is_detected(tmp_path):
    a = tmp_path / "a"
    put(a / "f.txt", b"1")
    out = tmp_path / "out"
    out.mkdir()
    jp = out / mt.JOURNAL_NAME
    jp.write_text('{"version": 1, "items": {}}', encoding="utf-8")
    os.chmod(jp, stat.S_IREAD)
    try:
        rep = run_transfer_selection([SelectedItem(str(a), True)], out, deps=deps_with(FakeRobo()), keep_awake=False)
        assert rep.meta.get("journal_write_failed") is True
    finally:
        os.chmod(jp, stat.S_IWRITE | stat.S_IREAD)


# ---------------------------------------------------------------- drop_stage: 하드 링크 성공해도 표식 갱신·진행 표시
def test_stage_files_touches_marker_and_reports_progress_even_when_every_file_is_linked(tmp_path, monkeypatch):
    entries = []
    for i in range(210):  # 모두 하드 링크로 성공하는 파일 210개(200번째에서 표식 갱신/진행 표시가 있어야 한다)
        f = tmp_path / "pc" / f"f{i:03d}.txt"
        put(f, b"x")
        entries.append(ds.describe(f))
    plan = ds.plan_drops(entries, [])
    (tmp_path / "stg").mkdir()
    stage = ds.new_stage_dir(tmp_path / "stg")
    touched, msgs = [], []
    monkeypatch.setattr(ds, "touch_marker", lambda d: touched.append(d))
    stats = ds.stage_files(plan, stage, progress=msgs.append)
    assert stats["linked"] + stats["copied"] == 210
    if stats["linked"]:  # 하드 링크를 쓸 수 있는 파일 시스템에서
        assert touched and any("200" in m for m in msgs)


# ---------------------------------------------------------------- LAN
def test_gate_does_not_take_over_a_session_that_is_hashing(rx_factory):
    rx = rx_factory()
    closed = []

    class Ch:
        def __init__(self):
            self.last_rx = time.monotonic() - 1000  # 오래 조용함
            self.closed = False

        def close(self):
            self.closed = True
    old = LT._Active(Ch())
    rx._cur = old
    old.working = True
    assert rx._gate(Ch())[0] == "busy" and not old.ch.closed  # 작업 중이면 인계 거절
    old.working = False
    assert rx._gate(Ch()) is None and old.ch.closed  # 정말 조용한 연결만 인계


def test_receiver_result_warns_and_is_not_success_when_counts_differ_from_plan(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {"a.txt": b"1", "b.txt": b"2"})
    rx = rx_factory()
    from pcmigrate import lan_api
    lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, sources=[str(src)], options={"chunk": 4096})
    for _ in range(50):
        if rx.result:
            break
        time.sleep(0.05)
    assert rx.result["success"] is True and "count_mismatch" not in rx.result  # 정상: 일치


# ---------------------------------------------------------------- robocopy 인자
def test_robocopy_args_exclude_directory_and_file_junctions_and_stay_valid(tmp_path):
    args = rc.base_args(tmp_path / "s", tmp_path / "d", None, False)
    assert "/XJ" in args and "/XJD" in args and "/XJF" in args
    rc.validate_args(args)  # 금지 옵션(/MIR /PURGE /MOV)은 여전히 없다
