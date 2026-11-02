"""stage63: 끌어다 놓은 파일/폴더를 모드 B 전송에 함께 싣는 백엔드(drop_stage) 시험.

가짜 환경만 쓴다: 임시 폴더 트리, 가짜 robocopy, 환경변수 덮어쓰기(oldpcuser). 실제 사용자 폴더/레지스트리/네트워크는 건드리지 않는다.
"""
import json
import os
import stat
import time

import pytest

from pcmigrate import cli, drop_stage as ds, multi_transfer as mt
from pcmigrate.cancel import Cancelled
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SELECTION_FORMAT
from pcmigrate.transfer import TransferError

from test_find_data_multi_transfer import FakeRobo, deps_with, put


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


def tree(tmp_path):
    base = tmp_path / "pc" / "Work"
    put(base / "A" / "a1.dwg", b"1" * 10)
    put(base / "A" / "sub" / "a2.dwg", b"2" * 20)
    put(base / "B" / "b1.jpg", b"3" * 30)
    put(base / "loose.txt", b"4" * 5)
    put(base / "C" / "c1.pdf", b"5" * 7)
    return base


def run(items, extras, dst, **kw):
    kw.setdefault("keep_awake", False)
    return run_transfer_selection(items, dst, deps=deps_with(FakeRobo()), extra_files=extras, **kw)


def leftovers():
    root = ds.staging_root()
    return list(root.iterdir()) if root.exists() else []


def test_overlap_and_duplicates_removed_by_normalized_path(tmp_path):
    base = tree(tmp_path)
    # 대소문자 다르고 .. 이 섞인 같은 경로도 같은 파일로 본다
    weird = str(base / "B" / ".." / "A" / "SUB" / "A2.DWG")
    entries = [ds.describe(base / "A" / "sub" / "a2.dwg"), ds.describe(base / "B" / "b1.jpg"), ds.describe(base / "B"),
               ds.describe(base / "loose.txt"), ds.describe(base / "loose.txt")]
    entries[0].path = weird
    plan = ds.plan_drops(entries, [str(base / "A")])
    assert sorted(os.path.basename(f[0]) for f in plan.files) == ["b1.jpg", "loose.txt"]
    assert len(plan.overlap) == 1 and len(plan.duplicates) == 2  # 폴더 B 안의 b1.jpg, 두 번째 loose.txt
    assert not plan.failed


def test_dropped_dir_inside_checked_is_one_overlap_entry(tmp_path):
    base = tree(tmp_path)
    plan = ds.plan_drops([ds.describe(base / "A" / "sub")], [str(base / "A")])
    assert not plan.files and len(plan.overlap) == 1


def test_dropped_dir_containing_checked_folder_excludes_only_that_part(tmp_path):
    base = tree(tmp_path)
    plan = ds.plan_drops([ds.describe(base)], [str(base / "A")])
    assert sorted(os.path.basename(f[0]) for f in plan.files) == ["b1.jpg", "c1.pdf", "loose.txt"] and len(plan.overlap) == 1


def test_missing_item_fails_only_that_item(tmp_path):
    base = tree(tmp_path)
    e_gone = ds.describe(base / "loose.txt")
    os.remove(base / "loose.txt")
    plan = ds.plan_drops([e_gone, ds.describe(base / "nope.bin"), ds.describe(base / "C" / "c1.pdf")], [])
    assert [os.path.basename(f[0]) for f in plan.files] == ["c1.pdf"]
    assert {os.path.basename(f["path"]) for f in plan.failed} == {"loose.txt", "nope.bin"}


def test_changed_since_drop_uses_current_content_and_notes(tmp_path):
    base = tree(tmp_path)
    e = ds.describe(base / "loose.txt")
    put(base / "loose.txt", b"new content!")
    plan = ds.plan_drops([e], [])
    assert len(plan.files) == 1 and plan.changed and plan.changed[0]["new_size"] == 12


def test_cloud_only_excluded_not_staged(tmp_path):
    base = tree(tmp_path)

    def attr(st):
        return 0x00400000 if st.st_size == 5 else 0
    plan = ds.plan_drops([ds.describe(base / "loose.txt"), ds.describe(base / "C")], [], attr_fn=attr)
    assert [os.path.basename(f[0]) for f in plan.files] == ["c1.pdf"] and plan.cloud_only[0]["size"] == 5


def test_end_to_end_layout_report_and_cleanup(tmp_path):
    base = tree(tmp_path)
    snap = lambda: {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in (tmp_path / "pc").rglob("*") if p.is_file()}  # noqa: E731
    before = snap()
    dst = tmp_path / "out"
    rep = run([str(base / "A")], [base / "A" / "sub" / "a2.dwg", base / "B", base / "loose.txt"], dst)
    d = rep.to_dict()
    assert d["success"] and d["drop_stage"]["overlap_count"] == 1 and d["drop_stage"]["staged_files"] == 2
    # 원래 경로 구조 재현
    assert (dst / mt.dest_rel(str(base / "B")) / "b1.jpg").read_bytes() == b"3" * 30
    assert (dst / mt.dest_rel(str(base)) / "loose.txt").read_bytes() == b"4" * 5
    assert (dst / mt.dest_rel(str(base / "A")) / "sub" / "a2.dwg").is_file()
    journal = json.loads((dst / mt.JOURNAL_NAME).read_text(encoding="utf-8"))
    assert all(str(ds.staging_root()) not in v["src"] for v in journal["items"].values())  # 기록은 원래 경로 기준
    assert d["drop_stage"]["cleanup"]["removed"] and leftovers() == []
    assert before == snap()
    assert "이미 선택한 폴더에 포함되어 빼고 보냅니다" in "\n".join(mt.summary_lines(rep, str(dst)))


def test_no_extras_is_identical_to_before(tmp_path):
    base = tree(tmp_path)
    r1 = run_transfer_selection([str(base / "A")], tmp_path / "o1", deps=deps_with(FakeRobo()), keep_awake=False)
    r2 = run([str(base / "A")], None, tmp_path / "o2")
    assert "drop_stage" not in r1.meta and "drop_stage" not in r2.meta and not leftovers()


def test_only_extras_no_checked_folder(tmp_path):
    base = tree(tmp_path)
    rep = run([], [base / "loose.txt"], tmp_path / "o")
    assert rep.to_dict()["success"] and (tmp_path / "o" / mt.dest_rel(str(base)) / "loose.txt").is_file()


def test_copy_fallback_is_verified_and_readonly_never_linked(tmp_path):
    base = tree(tmp_path)
    ro = base / "C" / "c1.pdf"
    os.chmod(ro, stat.S_IREAD)
    try:
        stage = ds.new_stage_dir()
        plan = ds.plan_drops([ds.describe(ro), ds.describe(base / "loose.txt")], [])
        st = ds.stage_files(plan, stage, no_link=True)
        assert st["copied"] == 2 and st["linked"] == 0
        out = stage / mt.dest_rel(str(ro))
        assert out.read_bytes() == b"5" * 7 and out.stat().st_mtime_ns == ro.stat().st_mtime_ns
        stage2 = ds.new_stage_dir()  # 링크를 허용해도 읽기 전용 파일은 복사로 처리
        assert ds.stage_files(ds.plan_drops([ds.describe(ro)], []), stage2)["copied"] == 1
        assert ds.remove_stage_dir(stage)["removed"] and ds.remove_stage_dir(stage2)["removed"]
        assert ro.read_bytes() == b"5" * 7 and not ro.stat().st_mode & stat.S_IWRITE  # 원본 속성 그대로
    finally:
        os.chmod(ro, stat.S_IWRITE | stat.S_IREAD)


def test_hardlink_used_when_possible_and_cleanup_keeps_original(tmp_path):
    base = tree(tmp_path)
    f = base / "loose.txt"
    stage = ds.new_stage_dir()
    st = ds.stage_files(ds.plan_drops([ds.describe(f)], []), stage)
    if st["linked"]:
        assert os.stat(f).st_nlink == 2
    assert ds.remove_stage_dir(stage)["removed"]
    assert f.read_bytes() == b"4" * 5 and os.stat(f).st_nlink == 1


def test_corrupt_temp_copy_is_recorded_as_failure_and_continues(tmp_path, monkeypatch):
    base = tree(tmp_path)
    monkeypatch.setattr(ds, "sha256_file", lambda p, **k: "0" * 64)
    plan = ds.plan_drops([ds.describe(base / "loose.txt")], [])
    stage = ds.new_stage_dir()
    ds.stage_files(plan, stage, no_link=True)
    assert plan.failed and "SHA-256" in plan.failed[0]["reason"] and not plan.files
    assert not (stage / mt.dest_rel(str(base / "loose.txt"))).exists()
    ds.remove_stage_dir(stage)


def test_temp_space_shortage_blocks_before_copy(tmp_path):
    base = tree(tmp_path)
    plan = ds.plan_drops([ds.describe(base / "loose.txt")], [])
    stage = ds.new_stage_dir()
    with pytest.raises(TransferError, match="임시 폴더"):
        ds.stage_files(plan, stage, no_link=True, free_fn=lambda p: 1)
    assert not list(stage.rglob("loose.txt"))
    ds.remove_stage_dir(stage)


def test_dest_space_shortage_blocks_and_report_saved(tmp_path):
    base = tree(tmp_path)
    rpt = tmp_path / "abort.json"
    with pytest.raises(TransferError, match="여유 공간"):
        run([], [base / "loose.txt"], tmp_path / "o", abort_report=rpt, drop_kwargs={"free_fn": lambda p: 1})
    data = json.loads(rpt.read_text(encoding="utf-8"))
    assert data["aborted"] and not data["success"] and leftovers() == []


def test_cancel_cleans_up_and_writes_report(tmp_path):
    base = tree(tmp_path)
    rpt = tmp_path / "abort.json"
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3
    with pytest.raises(Cancelled):
        run([str(base / "A")], [base / "B", base / "loose.txt"], tmp_path / "o", cancel=cancel, abort_report=rpt)
    assert json.loads(rpt.read_text(encoding="utf-8"))["cancelled"] and leftovers() == []


def test_exception_in_transfer_cleans_up(tmp_path):
    base = tree(tmp_path)

    def boom(its):
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        ds.run_with_extras([], [str(base / "loose.txt")], tmp_path / "o", boom)
    assert leftovers() == []


def test_cleanup_only_marked_dirs_and_stale_removal(tmp_path):
    root = ds.staging_root()
    root.mkdir(parents=True, exist_ok=True)
    foreign = root / "someone-else"
    put(foreign / "keep.txt")
    old = ds.new_stage_dir()
    put(old / "D" / "x.txt")
    fresh = ds.new_stage_dir()
    past = time.time() - 3 * 24 * 3600
    os.utime(old / ds.MARKER_NAME, (past, past))
    out = ds.cleanup_stale()
    assert [o["removed"] for o in out] == [True]
    assert not old.exists() and fresh.exists() and (foreign / "keep.txt").exists()
    assert ds.remove_stage_dir(foreign)["removed"] is False and (foreign / "keep.txt").exists()
    outside = tmp_path / "pc" / "other"
    put(outside / ds.MARKER_NAME, json.dumps({"tool": "pcmigrate", "kind": "drop-staging"}).encode())
    assert ds.remove_stage_dir(outside)["removed"] is False and outside.exists()  # 임시 루트 밖은 지우지 않음
    ds.remove_stage_dir(fresh)


def test_stale_cleanup_runs_at_next_start(tmp_path):
    base = tree(tmp_path)
    left = ds.new_stage_dir()
    put(left / "D" / "x.txt")
    past = time.time() - 5 * 24 * 3600
    os.utime(left / ds.MARKER_NAME, (past, past))
    rep = run([], [base / "loose.txt"], tmp_path / "o")
    assert not left.exists() and rep.drop_stage["stale_cleaned"]


def test_nested_tops_reduced(tmp_path):
    base = tree(tmp_path)
    plan = ds.plan_drops([ds.describe(base / "A"), ds.describe(base / "A" / "sub" / "a2.dwg")], [])
    assert ds.tops_of(plan) == [str(base / "A")]


def test_dry_run_stages_nothing(tmp_path):
    base = tree(tmp_path)
    rep = run([], [base / "loose.txt", base / "B"], tmp_path / "o", dry_run=True)
    assert rep.drop_stage["staged_files"] == 2 and leftovers() == [] and not (tmp_path / "o").exists()


def test_failed_extra_makes_report_not_success(tmp_path):
    base = tree(tmp_path)
    d = run([str(base / "A")], [base / "gone.bin"], tmp_path / "o").to_dict()
    assert d["failed_count"] == 1 and not d["success"] and d["items_ok"] == 1


def test_cli_extra_file(tmp_path, capsys):
    base = tree(tmp_path)
    sel = tmp_path / "s.json"
    sel.write_text(json.dumps({"format": SELECTION_FORMAT, "version": 1, "items": [{"path": str(base / "A"), "include": True}]}), encoding="utf-8")
    assert cli.main(["transfer", str(base / "A"), str(tmp_path / "o"), "--extra-file", str(base / "loose.txt")]) == 2
    capsys.readouterr()
    code = cli.main(["transfer", "--from-selection", str(sel), str(tmp_path / "o"), "--dry-run", "--extra-file", str(base / "B"),
                     "--extra-file", str(base / "A" / "a1.dwg"), "--report", str(tmp_path / "r.json")])
    out = capsys.readouterr().out
    assert code == 0 and "이미 선택한 폴더에 포함되어 빼고 보냅니다" in out and leftovers() == []


def test_too_many_dropped_files_stops_with_clear_error_and_report(tmp_path, monkeypatch):
    base = tree(tmp_path)
    monkeypatch.setattr(ds, "MAX_DROP_FILES", 3)
    for i in range(5):
        put(base / "many" / f"f{i}.txt", b"z")
    rpt = tmp_path / "abort.json"
    with pytest.raises(TransferError, match="너무 많습니다"):
        run([], [base / "many"], tmp_path / "o", abort_report=rpt)
    data = json.loads(rpt.read_text(encoding="utf-8"))
    assert data["aborted"] and not data["success"] and "너무 많습니다" in data["reason"]
    assert leftovers() == [] and not (tmp_path / "o").exists()
    assert len(ds.plan_drops([ds.describe(base / "A")], []).files) == 2  # 상한 이하는 그대로
