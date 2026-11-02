"""stage76: 5차 확인 지적(N7 스탬프, N9 .bak, N6' 낡은 임시 보관 파일)의 재현 시험. 가짜 환경만 쓴다."""
import json
import os
import time

import pytest

from pcmigrate import copier, multi_transfer as mt
from pcmigrate.data_api import run_transfer_selection
from pcmigrate.find_data import SelectedItem

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_place_original import Env, contents

NOPROT = lambda p, **k: None  # noqa: E731
BS = "\\"


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


def sel_run(src, dst, **kw):
    return run_transfer_selection([SelectedItem(str(src), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False, skip_done=True, **kw)


def make_done(tmp_path, with_bak=False):
    src = tmp_path / "pc" / "Proj"
    put(src / "a.txt", b"A" * 50)
    put(src / "b.txt", b"B" * 50)
    if with_bak:
        put(src / "draw.bak", b"CAD backup")
    dst = tmp_path / "dst"
    assert sel_run(src, dst).to_dict()["success"]
    return src, dst, mt.dest_rel(str(src))


# ---------------------------------------------------------------- N7
def test_n7_same_size_content_change_with_new_mtime_is_reprocessed(tmp_path):
    src, dst, rel = make_done(tmp_path)
    p = src / "a.txt"
    p.write_bytes(b"Z" * 50)  # 같은 길이, 다른 내용
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))  # 수정 시각 갱신
    r2 = sel_run(src, dst)
    assert [i.status for i in r2.items] != ["skipped_done"]
    assert (dst / rel / "a.txt").read_bytes() == b"Z" * 50


def test_n7_unchanged_still_skips_and_reads_zero_hashes(tmp_path, monkeypatch):
    src, dst, rel = make_done(tmp_path)
    calls = {"n": 0}
    real = copier.sha256_file

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(copier, "sha256_file", counting)
    r2 = sel_run(src, dst)
    assert [i.status for i in r2.items] == ["skipped_done"] and r2.to_dict()["success"] and calls["n"] == 0


def test_n7_old_format_stamp_is_reprocessed_once(tmp_path):
    src, dst, rel = make_done(tmp_path)
    jp = dst / mt.JOURNAL_NAME
    j = json.loads(jp.read_text(encoding="utf-8"))
    for v in j["items"].values():
        v["src_stamp"] = [2, 100]  # stage75 형식(파일 수, 용량)
    jp.write_text(json.dumps(j), encoding="utf-8")
    assert [i.status for i in sel_run(src, dst).items] != ["skipped_done"]
    assert [i.status for i in sel_run(src, dst).items] == ["skipped_done"]  # 새 형식으로 다시 기록되어 그다음은 건너뜀


# ---------------------------------------------------------------- N9
def test_n9_source_with_bak_file_is_skipped_when_unchanged_and_reprocessed_when_dest_file_missing(tmp_path):
    src, dst, rel = make_done(tmp_path, with_bak=True)
    assert (dst / rel / "draw.bak").is_file()
    assert [i.status for i in sel_run(src, dst).items] == ["skipped_done"]  # 변경 없음: 매번 다시 처리하지 않는다
    (dst / rel / "a.txt").unlink()
    assert [i.status for i in sel_run(src, dst).items] != ["skipped_done"] and (dst / rel / "a.txt").is_file()


# ---------------------------------------------------------------- N6'
def place_stale(tmp_path, staged_files=None, truncated=False, with_file=True):
    env = Env(tmp_path, letters="D")
    put(env.recv / "D" / "Work" / "a.txt", b"a")
    if with_file:
        put(env.recv / "D" / "Work" / "keep.tmp", b"k")
    key = "d" + BS + "work"
    stale = {"src": "D:" + BS + "Work", "rel": "D" + BS + "Work", "status": "stale", "partial": True}
    if staged_files is not None:
        stale["staged_files"] = staged_files
    if truncated:
        stale["staged_files_truncated"] = True
    env.journal({key: {"src": "D:" + BS + "Work", "rel": "D" + BS + "Work", "status": "done"}, key + mt.STAGED_SUFFIX: stale})
    return env


def test_n6_stale_staged_file_still_in_receive_folder_blocks_the_folder_as_unverified(tmp_path):
    env = place_stale(tmp_path, ["keep.tmp"])
    r = env.run(apply=True, protected_fn=NOPROT).to_dict()
    assert r["skipped_count"] == 1 and contents(env.drives / "D") == {}
    assert (env.recv / "D" / "Work" / "keep.tmp").read_bytes() == b"k"  # 받기 폴더에서 지워지지 않았다
    r2 = env.run(apply=True, protected_fn=NOPROT, include_unverified=True).to_dict()  # 사용자가 확인했다고 하면 놓는다
    assert contents(env.drives / "D" / "Work") == {"a.txt": b"a", "keep.tmp": b"k"} and r2["skipped_count"] == 0


def test_n6_stale_without_remaining_file_or_without_list_places_as_before(tmp_path):
    env = place_stale(tmp_path, ["keep.tmp"], with_file=False)  # 목록의 파일이 이미 없다
    assert env.run(apply=True, protected_fn=NOPROT).to_dict()["skipped_count"] == 0 and contents(env.drives / "D" / "Work") == {"a.txt": b"a"}
    (tmp_path / "x").mkdir(exist_ok=True)
    env2 = place_stale(tmp_path / "x", None)  # stage75 형식(목록 없음): 기존 동작
    assert env2.run(apply=True, protected_fn=NOPROT).to_dict()["skipped_count"] == 0


def test_n6_truncated_list_is_conservative(tmp_path):
    env = place_stale(tmp_path, None, truncated=True)
    assert env.run(apply=True, protected_fn=NOPROT).to_dict()["skipped_count"] == 1


def test_n6_run_multi_records_staged_file_list_and_keeps_it_when_stale(tmp_path):
    src = tmp_path / "pc" / "Proj"
    put(src / "a.txt", b"A")
    put(src / "keep.tmp", b"wanted")
    dst = tmp_path / "dst"
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    run_transfer_selection([SelectedItem(str(src), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False, exclude_junk=True,
                           extra_files=[str(src / "keep.tmp")], drop_kwargs={"root": stage_root})
    rel = mt.dest_rel(str(src)).lower()
    jp = dst / mt.JOURNAL_NAME
    j = json.loads(jp.read_text(encoding="utf-8"))
    assert j["items"][rel + mt.STAGED_SUFFIX]["staged_files"] == ["keep.tmp"]
    j["items"][rel + mt.STAGED_SUFFIX]["status"] = "failed"  # 그 실행이 끝나지 못했다고 하자
    jp.write_text(json.dumps(j), encoding="utf-8")
    run_transfer_selection([SelectedItem(str(src), True)], dst, deps=deps_with(FakeRobo()), keep_awake=False)  # 이번에는 파일 없이
    j2 = json.loads(jp.read_text(encoding="utf-8"))["items"][rel + mt.STAGED_SUFFIX]
    assert j2["status"] == "stale" and j2["staged_files"] == ["keep.tmp"]
