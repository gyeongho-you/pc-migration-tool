"""place 리뷰 수정(stage48) 회귀 시험: 링크/정션 방어, 긴 경로 .bak, --map 충돌, 저널 상태, 용량 점검, 8.3 이름, 이동/삭제 보고.

가짜 환경만 쓴다(임시 폴더). 정션은 `mklink /J`(권한 불필요)로 만들고 못 만들면 건너뛴다.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pcmigrate import cli, copier, errorlog, failure, place_original as po
from pcmigrate.cancel import Cancelled
from pcmigrate.fs import DestInfo, long_path
from test_place_original import BIG, Env, contents, put, tree


def junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0 or not link.exists():
        pytest.skip("정션을 만들 수 없는 환경")


def jr(e, **items):
    e.journal({k: {"src": v[0], "rel": v[1], "status": v[2] if len(v) > 2 else "done"} for k, v in items.items()})


# --------------------------------------------------------------------------- A1 링크/정션
@pytest.mark.parametrize("vol", [True, False])
def test_junction_as_journal_unit_root_is_not_followed(tmp_path, vol):
    e = Env(tmp_path, letters="D")
    outside = tmp_path / "outside_precious"
    put(outside / "keep_me.txt", b"precious")
    (e.recv / "D" / "P").mkdir(parents=True)
    junction(e.recv / "D" / "P" / "A", outside)
    jr(e, a=("D:\\P\\A", "D\\P\\A"))
    rep = e.run(apply=True, same_volume_fn=lambda a, b: vol)
    assert (outside / "keep_me.txt").read_bytes() == b"precious"  # 받기 폴더 밖 파일은 이동/삭제되지 않는다
    assert not [p for p in e.drives.rglob("*") if p.is_file()]
    assert [r.code for r in rep.skipped] == [po.LINK] and rep.skipped[0].expected == "D:\\P\\A"


def test_junction_as_drive_folder_is_not_followed(tmp_path):
    e = Env(tmp_path, letters="D")
    outside = tmp_path / "outside2"
    put(outside / "P" / "A" / "keep2.txt", b"precious2")
    junction(e.recv / "D", outside)
    jr(e, a=("D:\\P\\A", "D\\P\\A"))
    rep = e.run(apply=True, same_volume_fn=lambda a, b: False)
    assert (outside / "P" / "A" / "keep2.txt").exists() and not [p for p in e.drives.rglob("*") if p.is_file()]
    assert rep.skipped and all(r.code == po.LINK for r in rep.skipped)


def test_junction_inside_unit_is_skipped_and_gather_ignores_it(tmp_path):
    e = Env(tmp_path, letters="D")
    outside = tmp_path / "out"
    put(outside / "o.txt", b"o")
    put(e.recv / "D" / "P" / "a.txt", b"a")
    junction(e.recv / "D" / "P" / "sub", outside)
    rep = e.run(apply=True, same_volume_fn=lambda a, b: False)
    assert (outside / "o.txt").exists() and (e.drives / "D" / "P" / "a.txt").exists()
    assert any(r.code == po.LINK for r in rep.skipped)


def test_gather_does_not_follow_junction_unit(tmp_path):
    e = Env(tmp_path, letters="D")
    outside = tmp_path / "out"
    put(outside / "o.txt", b"o")
    (e.recv / "설정 묶음").mkdir()
    put(e.recv / "Z" / "z.txt", b"z")
    junction(e.recv / "Z" / "lnk", outside)
    rep = e.run(apply=True, gather=str(tmp_path / "g"), protected_fn=lambda p, **k: None)
    assert (outside / "o.txt").exists() and (tmp_path / "g" / "Z" / "z.txt").exists()
    assert not (tmp_path / "g" / "Z" / "lnk").exists()
    assert any(r.code == po.LINK for r in rep.skipped)


def test_source_safety_net_blocks_file_ops_through_a_link(tmp_path, monkeypatch):
    """방어선: 폴더 단위 링크 검사를 일부러 무력화해도 파일 작업 직전의 realpath 확인이 받기 폴더 밖 파일을 지키는지."""
    e = Env(tmp_path, letters="D")
    outside = tmp_path / "out"
    put(outside / "o.txt", b"o")
    put(e.recv / "D" / "P" / "a.txt", b"a")
    junction(e.recv / "D" / "P" / "sub", outside)
    from pcmigrate import fs
    monkeypatch.setattr(fs, "is_reparse", lambda p: False)
    monkeypatch.setattr(fs, "reparse_in_chain", lambda b, t: None)
    rep = e.run(apply=True, same_volume_fn=lambda a, b: True)
    assert (outside / "o.txt").read_bytes() == b"o"
    assert any(r.code == po.LINK and "밖" in r.detail for r in rep.skipped)


# --------------------------------------------------------------------------- A2 긴 경로 .bak
def _long_file(base: Path, total: int, name="f.txt") -> Path:
    d = base
    while len(str(d / name)) + 4 < total - 41:
        d = d / ("d" * 40)
    pad = total - 4 - len(str(d)) - 1  # '.bak' 을 뺀 파일 경로 길이
    f = d / (name.split(".")[0].ljust(max(len(name), pad), "f") if pad > len(name) else name)
    os.makedirs(long_path(f.parent), exist_ok=True)
    return f


@pytest.mark.parametrize("total", [255, 286])
def test_backup_path_counts_existing_bak_on_long_paths(tmp_path, total):
    f = _long_file(tmp_path / "lp", total)
    with open(long_path(f), "w") as h:
        h.write("cur")
    with open(long_path(str(f) + ".bak"), "w") as h:
        h.write("OLD BACKUP")
    assert len(str(f)) + 4 in range(total - 2, total + 3)
    assert copier._backup_path(f).name == f.name + ".bak.1"  # 기존 .bak 을 덮어쓰지 않는다


def test_place_overwrite_on_286_char_path_keeps_both_backups(tmp_path):
    e = Env(tmp_path, letters="D")
    d = e.drives / "D"
    parts = []
    while len(str(d.joinpath(*parts))) < 250:
        parts.append("d" * 30)
    dst_dir = d.joinpath(*parts)
    os.makedirs(long_path(dst_dir), exist_ok=True)
    dst = dst_dir / "data.bin"
    open(long_path(dst), "w").write("CURRENT-1")
    open(long_path(str(dst) + ".bak"), "w").write("PREVIOUS BACKUP")
    src_dir = e.recv.joinpath("D", *parts)
    os.makedirs(long_path(src_dir), exist_ok=True)
    open(long_path(src_dir / "data.bin"), "w").write("received-new")
    rep = e.run(apply=True)
    assert len(str(dst)) + 4 > 260 and rep.failed_count == 0
    assert open(long_path(str(dst) + ".bak")).read() == "PREVIOUS BACKUP"  # 이전 백업 보존
    assert open(long_path(str(dst) + ".bak.1")).read() == "CURRENT-1"
    assert open(long_path(dst)).read() == "received-new"


# --------------------------------------------------------------------------- A3 --map 충돌
def test_map_collision_skips_both_drives(tmp_path):
    e = Env(tmp_path, letters="DE")
    put(e.recv / "D" / "a.txt", b"a")
    put(e.recv / "E" / "b.txt", b"b")
    before = tree(e.recv)
    rep = e.run(apply=True, maps={"D": "E"})
    assert sorted(r.code for r in rep.skipped) == [po.MAP_CONFLICT, po.MAP_CONFLICT] and tree(e.recv) == before
    assert not [p for p in e.drives.rglob("*") if p.is_file()]
    assert "D → E" in rep.skipped[0].detail or "E" in rep.skipped[0].detail
    rep2 = e.run(apply=True, maps={"D": "E", "E": "D"})  # 서로 바꾸기는 합쳐지지 않으므로 허용
    assert (e.drives / "E" / "a.txt").exists() and (e.drives / "D" / "b.txt").exists() and not rep2.skipped


# --------------------------------------------------------------------------- A4/A5 저널
@pytest.mark.parametrize("text", ["{not json", json.dumps({"version": 2, "items": {"d\\p": {"src": "D:\\P", "rel": "D\\P", "status": "copying"}}})])
def test_unreadable_or_mismatched_journal_requires_include_unverified(tmp_path, text):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "P" / "half.bin", b"partial")
    (e.recv / ".pcmigrate-multi.json").write_text(text, encoding="utf-8")
    warns = []
    rep = e.run(apply=True, warn=warns.append)
    assert [r.code for r in rep.skipped] == [po.UNVERIFIED] and (e.recv / "D" / "P" / "half.bin").exists()
    assert not [p for p in e.drives.rglob("*") if p.is_file()] and any("저널" in w for w in warns)
    rep2 = e.run(apply=True, include_unverified=True)
    assert (e.drives / "D" / "P" / "half.bin").read_bytes() == b"partial" and rep2.meta["journal_state"] == "bad"


def test_bad_or_non_dict_journal_items_exclude_their_drive_from_inference(tmp_path):
    e = Env(tmp_path, letters="DE")
    put(e.recv / "D" / "P" / "a.txt", b"a")
    put(e.recv / "E" / "b.txt", b"b")
    e.journal({"d\\p": "not-a-dict", "e\\x": {"src": "E:\\x", "rel": "..\\x", "status": "done"}})
    rep = e.run(apply=True)
    assert [r.code for r in rep.skipped] == [po.BAD_JOURNAL, po.BAD_JOURNAL]
    assert not [p for p in e.drives.rglob("*") if p.is_file()] and (e.recv / "D" / "P" / "a.txt").exists()


def test_partial_journal_reports_uncovered_siblings_and_not_success(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "P" / "A" / "a.txt", b"a")
    put(e.recv / "D" / "P" / "loose.txt", b"l")
    put(e.recv / "D" / "Other" / "o.txt", b"o")
    jr(e, a=("D:\\P\\A", "D\\P\\A"))
    rep = e.run(apply=True)
    got = {(r.expected, r.code, r.kind) for r in rep.skipped}
    assert got == {("D:\\Other", po.UNKNOWN_LOC, "folder"), ("D:\\P\\loose.txt", po.UNKNOWN_LOC, "file")}
    d = rep.to_dict()
    assert (e.drives / "D" / "P" / "A" / "a.txt").exists() and d["success"] is False and d["remaining_in_receive"]["files"] == 2


def test_success_false_when_files_remain_in_receive(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "a.txt", b"a")
    rep = e.run(apply=True, keep=False)
    assert rep.to_dict()["success"] is True
    put(e.recv / "D" / "b.txt", b"b")
    rep.remaining = {"files": 1}  # 처리되지 않은 파일이 남은 경우
    assert rep.to_dict()["success"] is False


# --------------------------------------------------------------------------- A6 용량
def test_space_precheck_counts_same_size_overwrite(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "P" / "x.bin", b"A" * 1000)
    put(e.drives / "D" / "P" / "x.bin", b"B" * 1000)  # 크기는 같고 내용은 다르다
    old = os.stat(e.drives / "D" / "P" / "x.bin").st_mtime_ns - 60_000_000_000
    os.utime(e.drives / "D" / "P" / "x.bin", ns=(old, old))  # 수정 시각도 다르다(FAT 해상도 2초를 넘는 차이)
    small = DestInfo("x", "fixed", "NTFS", 500, 1, "D:")
    rep = e.run(same_volume_fn=lambda a, b: False, dest_info_fn=lambda p: small)
    assert [r.code for r in rep.skipped] == [po.NO_SPACE] and rep.meta["space"]["D"]["required_bytes"] >= 1000
    # 복사본(수정 시각까지 같음)이면 이미 같은 것으로 보고 공간을 요구하지 않는다
    shutil.copy2(e.recv / "D" / "P" / "x.bin", e.drives / "D" / "P" / "x.bin")
    rep2 = e.run(same_volume_fn=lambda a, b: False, dest_info_fn=lambda p: small)
    assert not rep2.skipped and rep2.meta["space"]["D"]["required_bytes"] == 0


# --------------------------------------------------------------------------- A7 8.3 이름
def test_short_name_protected_paths(monkeypatch):
    monkeypatch.setattr(po, "expand_short_names", lambda p: None)
    assert po.protected_reason("C:\\PROGRA~1\\Vendor") and "8.3" in po.protected_reason("C:\\PROGRA~1\\Vendor")  # 풀 수 없으면 보호로
    monkeypatch.setattr(po, "expand_short_names", lambda p: "C:\\Program Files\\Vendor")
    assert "Program Files" in po.protected_reason("C:\\PROGRA~1\\Vendor")  # 긴 이름으로 풀어 보호 판정
    monkeypatch.setattr(po, "expand_short_names", lambda p: "D:\\Work\\Big Folder")
    assert po.protected_reason("D:\\BIGFOL~1") is None  # 풀린 결과가 보호 위치가 아니면 허용
    monkeypatch.setattr(po, "expand_short_names", lambda p: pytest.fail("~숫자가 없으면 부르지 않는다"))
    assert po.protected_reason("D:\\a~b\\c") is None and po.protected_reason("C:\\Windows\\x")


def test_short_name_dir_in_place_is_skipped(tmp_path):
    e = Env(tmp_path, letters="C")
    put(e.recv / "C" / "PROGRA~1" / "v" / "a.exe", b"x")
    rep = e.run(apply=True)
    assert [r.code for r in rep.skipped] == [po.PROTECTED] and (e.recv / "C" / "PROGRA~1" / "v" / "a.exe").exists()


# --------------------------------------------------------------------------- B1~B7
@pytest.fixture
def env2(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "P" / "a.txt", b"aaa")
    put(e.recv / "D" / "P" / "b.txt", b"bbb")
    jr(e, p=("D:\\P", "D\\P"))
    return e


def test_move_check_mismatch_is_warning_not_failure(env2, monkeypatch):
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        real(tmp, dst, res, lp)
        if Path(tmp).name == "a.txt":
            put(Path(tmp), b"reappeared")  # 이동 뒤에도 받기 폴더에 같은 이름이 보이는 경우
    monkeypatch.setattr(copier, "_install", inst)
    rep = env2.run(apply=True)
    d = rep.to_dict()
    assert d["failed_count"] == 0 and d["summary"]["moved"] == 2 and len(d["warnings"]) == 1 and d["success"] is False
    assert (env2.drives / "D" / "P" / "a.txt").read_bytes() == b"aaa"


def test_delete_source_rechecks_mtime(env2, monkeypatch):
    real = copier.copy_verified

    def fake(src, dst, **kw):
        r = real(src, dst, **kw)
        if Path(src).name == "a.txt":
            st = os.stat(src)
            os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))  # 복사 뒤 원본이 바뀐 것처럼
        return r
    monkeypatch.setattr(copier, "copy_verified", fake)
    rep = env2.run(apply=True, same_volume_fn=lambda a, b: False)
    assert (env2.recv / "D" / "P" / "a.txt").exists() and [r.code for r in rep.leftover] == [po.SRC_CHANGED]
    assert not (env2.recv / "D" / "P" / "b.txt").exists()


def test_dry_run_errors_are_reason_rows_only(env2, monkeypatch):
    put(env2.drives / "D" / "P" / "a.txt", b"AAA")  # 같은 크기: 해시 비교가 필요
    real = copier.sha256_file

    def sha(path, *a, **k):
        if Path(path).name == "a.txt":
            raise PermissionError(5, "locked", str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(copier, "sha256_file", sha)
    rep = env2.run(same_volume_fn=lambda a, b: False)
    d = rep.to_dict()
    assert d["failed_count"] == 0 and d["summary"].get("failed", 0) == 0
    assert [r["reason_code"] for r in d["skipped"]] == [failure.PERMISSION]
    assert errorlog.last_error() is None  # 미리보기는 오류 로그를 남기지 않는다


def test_item_status_partial_when_leftover(env2, monkeypatch):
    real = os.unlink

    def unlink(p, *a, **k):
        if str(p).endswith("a.txt") and "recv" in str(p):
            raise PermissionError(5, "denied", str(p))
        return real(p, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink)
    rep = env2.run(apply=True, same_volume_fn=lambda a, b: False)
    assert rep.items[0]["status"] == "partial" and rep.items[0]["leftover_count"] == 1


def test_finish_error_does_not_mask_original_exception(env2, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("finish-boom")
    monkeypatch.setattr(po, "_remaining", boom)
    n = {"i": 0}

    def cancel():
        n["i"] += 1
        return n["i"] > 3
    with pytest.raises(Cancelled) as ei:
        env2.run(apply=True, cancel=cancel)
    assert ei.value.report.meta["finish_error"].startswith("RuntimeError") and errorlog.last_error() is not None


def test_cli_manual_work_message_and_exit_code(tmp_path, monkeypatch, capsys):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "a.txt", b"a")
    put(e.recv / "loose.txt", b"l")
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", e.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    assert cli.main(["place", str(e.recv), "--yes", "--json"]) == 0  # 종료 코드는 실패일 때만 1
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["needs_manual"] is True and out["success"] is False and "[수동 작업 필요]" in cap.err
