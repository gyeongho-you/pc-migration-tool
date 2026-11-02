"""stage63 후속: 직접 고른 파일이 선택 폴더와 중첩되어 도착할 때 받는 쪽 place(--gather 포함)가 올바른지 시험(가짜 환경만)."""
import json
import shutil

import pytest

from pcmigrate import multi_transfer as mt
from pcmigrate.data_api import run_transfer_selection

from test_find_data_multi_transfer import FakeRobo, deps_with, put
from test_place_original import Env, contents

NOPROT = lambda p, **k: None  # noqa: E731
BS = "\\"


def make_received(tmp_path):
    """실제 전송 경로(가짜 robocopy)로 받기 폴더를 만든 뒤, 임시 폴더(보호 위치인 AppData 아래) 이름만 D 드라이브의 Work 로 바꿔 놓는다.

    체크 폴더 Work/A/sub + 직접 고른 Work/A/x.dwg, Work/y.txt. 전송은 경로 내용과 무관하므로 구조/기록은 D 드라이브의 Work 에서 보낸 것과 같다.
    """
    base = tmp_path / "pc" / "Work"
    put(base / "A" / "sub" / "s.dwg", b"s" * 11)
    put(base / "A" / "x.dwg", b"x" * 12)
    put(base / "y.txt", b"y" * 13)
    env = Env(tmp_path, letters="D")
    rep = run_transfer_selection([str(base / "A" / "sub")], env.recv, deps=deps_with(FakeRobo()), keep_awake=False,
                                 extra_files=[base / "A" / "x.dwg", base / "y.txt"])
    assert rep.to_dict()["success"]
    real = mt.dest_rel(str(base))
    (env.recv / "D").mkdir()
    shutil.move(str(env.recv.joinpath(*real.split(BS))), str(env.recv / "D" / "Work"))
    shutil.rmtree(env.recv / real.split(BS)[0])
    jp = env.recv / mt.JOURNAL_NAME
    items = {}
    for v in json.loads(jp.read_text(encoding="utf-8"))["items"].values():
        src = "D:" + BS + "Work" + v["src"][len(str(base)):]
        rel = mt.dest_rel(src)
        items[rel.lower()] = dict(v, src=src, rel=rel)
    jp.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False), encoding="utf-8")
    return env


def want(sep=BS):
    return {"A" + sep + "sub" + sep + "s.dwg": b"s" * 11, "A" + sep + "x.dwg": b"x" * 12, "y.txt": b"y" * 13}


def test_nested_layout_after_transfer(tmp_path):
    env = make_received(tmp_path)
    got = contents(env.recv)
    for k, v in want().items():
        assert got["D" + BS + "Work" + BS + k] == v
    j = json.loads((env.recv / mt.JOURNAL_NAME).read_text(encoding="utf-8"))
    assert {v["src"] for v in j["items"].values()} == {"D:" + BS + "Work" + BS + "A" + BS + "sub", "D:" + BS + "Work"}


@pytest.mark.parametrize("keep", [False, True])
def test_place_puts_nested_items_correctly(tmp_path, keep):
    env = make_received(tmp_path)
    assert env.run(keep=keep, protected_fn=NOPROT).dry_run  # 미리보기
    d = env.run(apply=True, keep=keep, protected_fn=NOPROT).to_dict()
    assert contents(env.drives / "D" / "Work") == want()
    assert d["failed_count"] == 0 and d["skipped_count"] == 0 and d["success"], d
    assert d["total_files"] == 3
    if not keep:
        assert [p for p in env.recv.rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate-")] == []


def test_place_with_gather_nothing_to_gather(tmp_path):
    env = make_received(tmp_path)
    rep = env.run(apply=True, gather=str(tmp_path / "gather"), protected_fn=NOPROT)
    assert contents(env.drives / "D" / "Work") == want() and rep.to_dict()["success"]


def test_place_with_gather_when_drive_missing_keeps_structure(tmp_path):
    env = make_received(tmp_path)
    shutil.rmtree(env.drives)
    g = tmp_path / "gather"
    rep = env.run(apply=True, gather=str(g), protected_fn=NOPROT, drive_root_fn=lambda c: None)
    got = contents(g)
    for k, v in want().items():
        assert got["D" + BS + "Work" + BS + k] == v
    assert rep.to_dict()["failed_count"] == 0
