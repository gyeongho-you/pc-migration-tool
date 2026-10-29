"""받기 폴더 -> 원래 위치 배치(place_original, `pcmigrate place`) 시험.

가짜 환경만 쓴다: 임시 폴더의 받기 폴더(recv)와 가짜 드라이브 폴더(drives/D, drives/E ...). 실제 드라이브/시스템 폴더는 건드리지 않는다.
볼륨 판정(같은 볼륨/다른 볼륨)과 드라이브 위치는 주입해 바꾼다.
"""
import errno
import json
import os
import shutil
from pathlib import Path

import pytest

from pcmigrate import cli, copier, errorlog, failure, place_original as po
from pcmigrate.cancel import Cancelled
from pcmigrate.fs import DestInfo
from pcmigrate.transfer_base import DriveDisconnected, TransferError

BIG = DestInfo("x", "fixed", "NTFS", 10 ** 12, 1, "D:")


def put(path: Path, data: bytes = b"x" * 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) if p.is_file() else None
            for p in sorted(root.rglob("*"))}


def contents(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class Env:
    def __init__(self, tmp: Path, letters="DE"):
        self.recv = tmp / "recv"
        self.recv.mkdir()
        self.drives = tmp / "drives"
        for letter in letters:
            (self.drives / letter).mkdir(parents=True)
        self.tmp = tmp

    def root(self, letter):
        p = self.drives / letter
        return p if p.is_dir() else None

    def journal(self, items: dict):
        (self.recv / ".pcmigrate-multi.json").write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False), encoding="utf-8")

    def kw(self, **extra):
        d = dict(drive_root_fn=self.root, dest_info_fn=lambda p: BIG)
        d.update(extra)
        return d

    def run(self, **extra):
        return po.place(self.recv, **self.kw(**extra))


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    put(e.recv / "D" / "프로젝트" / "A" / "plan.dwg", b"plan")
    put(e.recv / "D" / "프로젝트" / "A" / "sub" / "spec.pdf", b"spec")
    put(e.recv / "D" / "프로젝트" / "B" / "site.jpg", b"site")
    e.journal({"d\\프로젝트\\a": {"src": "D:\\프로젝트\\A", "rel": "D\\프로젝트\\A", "status": "done"},
               "d\\프로젝트\\b": {"src": "D:\\프로젝트\\B", "rel": "D\\프로젝트\\B", "status": "done"}})
    return e


def _no_vol(a, b):
    return False


# --------------------------------------------------------------------------- 기본 배치
def test_same_volume_moves_into_original_paths(env):
    rep = env.run(apply=True)
    d = env.drives / "D" / "프로젝트"
    assert contents(d) == {"A\\plan.dwg": b"plan", "A\\sub\\spec.pdf": b"spec", "B\\site.jpg": b"site"}
    assert not (env.recv / "D" / "프로젝트" / "A" / "plan.dwg").exists()
    r = rep.to_dict()
    assert r["mode"] == "move" and r["summary"]["moved"] == 3 and r["failed_count"] == 0 and r["manual_count"] == 0
    assert r["success"] is True and r["removed_sources"] == 0  # 이동은 삭제가 아니다
    # 빈 폴더는 지우지 않고 남은 항목으로만 보고
    assert (env.recv / "D" / "프로젝트" / "A").is_dir()
    assert r["remaining_in_receive"]["files"] == 0 and r["remaining_in_receive"]["empty_folders"] >= 1
    assert [i["status"] for i in r["items"]] == ["placed", "placed"]


def test_infers_from_drive_folder_without_journal(tmp_path):
    e = Env(tmp_path)
    put(e.recv / "D" / "Work" / "a.txt", b"a")
    warns = []
    rep = e.run(apply=True, warn=warns.append)
    assert (e.drives / "D" / "Work" / "a.txt").read_bytes() == b"a"
    assert rep.to_dict()["journal"] is False and any("저널이 없어" in w for w in warns)


def test_map_drive(env):
    rep = env.run(apply=True, maps={"D": "E"})
    assert (env.drives / "E" / "프로젝트" / "A" / "plan.dwg").read_bytes() == b"plan"
    assert not any((env.drives / "D").iterdir())
    assert rep.items[0]["expected"].startswith("E:\\")


def test_parse_maps():
    assert po.parse_maps(["d=e", "F:=G:"]) == {"D": "E", "F": "G"}
    for bad in (["DE"], ["D=E", "D=F"], ["D=E", "F=E"], ["1=E"]):
        with pytest.raises(ValueError):
            po.parse_maps(bad)


# --------------------------------------------------------------------------- 미리보기
def test_dry_run_changes_nothing(env):
    before_recv, before_drv = tree(env.recv), tree(env.drives)
    rep = env.run(same_volume_fn=_no_vol)
    assert tree(env.recv) == before_recv and tree(env.drives) == before_drv
    r = rep.to_dict()
    assert r["dry_run"] is True and r["summary"]["would_copy"] == 3
    assert r["delete_planned_files"] == 3 and r["delete_planned_bytes"] == 12  # 삭제 예정 명시
    text = "\n".join(po.summary_lines(rep))
    assert "삭제 예정" in text and "--yes" in text
    # 같은 볼륨 미리보기는 이동 예정, 삭제 예정 없음
    r2 = env.run(same_volume_fn=lambda a, b: True)
    assert r2.to_dict()["summary"]["would_move"] == 3 and r2.delete_planned == 0
    assert tree(env.recv) == before_recv and tree(env.drives) == before_drv


# --------------------------------------------------------------------------- 놓을 수 없는 항목(중단 없이 모아서 보고)
def test_protected_missing_drive_and_report_table(tmp_path):
    e = Env(tmp_path)
    put(e.recv / "D" / "Windows" / "x.dll", b"1")
    put(e.recv / "D" / "Program Files" / "App" / "a.exe", b"2")
    put(e.recv / "D" / "Users" / "bob" / "AppData" / "Roaming" / "c.ini", b"3")
    put(e.recv / "D" / "Users" / "bob" / "Documents" / "ok.txt", b"ok")
    put(e.recv / "Z" / "data" / "z.txt", b"z")
    put(e.recv / "설정 묶음" / "pkg.zip", b"pkg")
    put(e.recv / "UNC" / "srv" / "share" / "u.txt", b"u")
    put(e.recv / "loose.txt", b"l")
    before = contents(e.recv)
    rep = e.run(apply=True)
    assert (e.drives / "D" / "Users" / "bob" / "Documents" / "ok.txt").read_bytes() == b"ok"
    assert not (e.drives / "D" / "Windows").exists() and not (e.drives / "D" / "Program Files").exists()
    assert not (e.drives / "D" / "Users" / "bob" / "AppData").exists()
    rows = {(r.expected, r.code) for r in rep.skipped}
    assert ("D:\\Windows", po.PROTECTED) in rows and ("D:\\Program Files", po.PROTECTED) in rows
    assert ("D:\\Users\\bob\\AppData", po.PROTECTED) in rows and ("Z:\\", po.DRIVE_MISSING) in rows
    assert ("\\\\srv\\share", po.UNC) in rows
    assert sum(1 for r in rep.skipped if r.code == po.UNKNOWN_LOC) == 2  # 설정 묶음 + 받기 폴더 바로 아래 파일
    # 건너뛴 것은 받기 폴더에 그대로(현재 위치)
    after = contents(e.recv)
    for r in rep.skipped:
        assert os.path.exists(r.current)
    assert after["Z\\data\\z.txt"] == before["Z\\data\\z.txt"] and after["D\\Windows\\x.dll"] == b"1"
    d = rep.to_dict()
    row = next(x for x in d["skipped"] if x["expected"] == "D:\\Windows")
    assert row["current"].endswith("Windows") and "시스템" in row["reason"] and row["kind"] == "folder" and row["files"] == 1
    lines = "\n".join(po.summary_lines(rep))
    assert "예상 위치 | 현재 위치 | 사유" in lines and d["success"] is False


def test_protected_reason_rules():
    assert po.protected_reason("C:\\Windows\\System32") and po.protected_reason("D:\\ProgramData")
    assert po.protected_reason("C:\\Users\\x\\AppData\\Local\\y") and po.protected_reason("C:\\Users\\x\\.ssh\\config")
    assert po.protected_reason("C:\\Users\\x\\AppData") and po.protected_reason("C:\\Users\\x\\Start Menu\\Programs\\Startup\\a.lnk")
    assert po.protected_reason("D:\\Work\\XLSTART\\a.xlsx") and po.protected_reason("C:\\boot.ini", is_dir=False)
    assert po.protected_reason("D:\\프로젝트\\A") is None and po.protected_reason("C:\\Users\\x\\Documents\\a.exe", is_dir=False) is None
    assert po.name_problem("CON") and po.name_problem("a.") and po.name_problem("a|b") and not po.name_problem("정상 이름.dwg")


def test_dest_inside_receive_is_skipped(tmp_path):
    e = Env(tmp_path)
    e.recv = e.drives / "D" / "recv"  # 받기 폴더가 D 드라이브 안에 있음
    e.recv.mkdir()
    put(e.recv / "D" / "recv" / "sub" / "f.txt", b"f")
    put(e.recv / "D" / "keep" / "g.txt", b"g")
    rep = e.run(apply=True)
    assert any(r.code == po.INSIDE_RECEIVE and r.expected == "D:\\recv" for r in rep.skipped)
    assert (e.drives / "D" / "keep" / "g.txt").read_bytes() == b"g"
    assert (e.recv / "D" / "recv" / "sub" / "f.txt").read_bytes() == b"f"


def test_symlink_in_receive_not_followed(env, tmp_path):
    outside = tmp_path / "outside"
    put(outside / "secret.txt", b"s")
    try:
        os.symlink(outside, env.recv / "D" / "프로젝트" / "A" / "lnk", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("심볼릭 링크를 만들 수 없는 환경")
    rep = env.run(apply=True)
    assert any(r.code == po.LINK for r in rep.skipped)
    assert not (env.drives / "D" / "프로젝트" / "A" / "lnk").exists()
    assert (outside / "secret.txt").read_bytes() == b"s"  # 링크 너머는 건드리지 않음
    assert (env.drives / "D" / "프로젝트" / "A" / "plan.dwg").exists()


def test_unverified_journal_items_skipped_unless_allowed(env):
    env.journal({"d\\프로젝트\\a": {"src": "D:\\프로젝트\\A", "rel": "D\\프로젝트\\A", "status": "pending"},
                 "d\\프로젝트\\b": {"src": "D:\\프로젝트\\B", "rel": "D\\프로젝트\\B", "status": "done"}})
    rep = env.run(apply=True)
    assert any(r.code == po.UNVERIFIED for r in rep.skipped) and (env.recv / "D" / "프로젝트" / "A" / "plan.dwg").exists()
    assert (env.drives / "D" / "프로젝트" / "B" / "site.jpg").exists()
    rep2 = env.run(apply=True, include_unverified=True)
    assert (env.drives / "D" / "프로젝트" / "A" / "plan.dwg").exists() and rep2.to_dict()["manual_count"] == 0


def test_space_precheck_skips_drive_without_writing(env):
    small = DestInfo("x", "fixed", "NTFS", 5, 1, "D:")
    before = tree(env.recv)
    rep = env.run(apply=True, same_volume_fn=_no_vol, dest_info_fn=lambda p: small)
    assert [r.code for r in rep.skipped] == [po.NO_SPACE, po.NO_SPACE] and tree(env.recv) == before
    assert not any((env.drives / "D").iterdir())
    # 같은 볼륨 이동은 용량이 늘지 않아 공간이 작아도 막지 않는다
    rep2 = env.run(apply=True, dest_info_fn=lambda p: small)
    assert rep2.to_dict()["summary"]["moved"] == 3


# --------------------------------------------------------------------------- 덮어쓰기/.bak
@pytest.mark.parametrize("vol", [True, False])
def test_overwrite_keeps_bak(env, vol):
    put(env.drives / "D" / "프로젝트" / "A" / "plan.dwg", b"OLD")
    rep = env.run(apply=True, same_volume_fn=lambda a, b: vol)
    dst = env.drives / "D" / "프로젝트" / "A"
    assert (dst / "plan.dwg").read_bytes() == b"plan" and (dst / "plan.dwg.bak").read_bytes() == b"OLD"
    assert any(p["backup"].endswith("plan.dwg.bak") for p in rep.placed)


def test_no_overwrite_refuses_and_reports(env):
    put(env.drives / "D" / "프로젝트" / "A" / "plan.dwg", b"OLD")
    rep = env.run(apply=True, no_overwrite=True)
    assert (env.drives / "D" / "프로젝트" / "A" / "plan.dwg").read_bytes() == b"OLD"
    assert (env.recv / "D" / "프로젝트" / "A" / "plan.dwg").read_bytes() == b"plan"
    assert [r.code for r in rep.skipped] == [po.NO_OVERWRITE] and rep.skipped[0].expected.endswith("plan.dwg")
    assert not list((env.drives / "D").rglob("*.bak"))


def test_dir_in_file_spot_fails_only_that_file(env):
    (env.drives / "D" / "프로젝트" / "A" / "plan.dwg").mkdir(parents=True)
    rep = env.run(apply=True)
    assert [r.code for r in rep.failed] == [failure.DEST_IS_DIR]
    assert (env.drives / "D" / "프로젝트" / "B" / "site.jpg").exists() and (env.recv / "D" / "프로젝트" / "A" / "plan.dwg").exists()


# --------------------------------------------------------------------------- 복사-삭제(다른 볼륨)
def test_cross_volume_copy_verify_then_delete(env):
    rep = env.run(apply=True, same_volume_fn=_no_vol)
    r = rep.to_dict()
    assert r["mode"] == "copy-delete" and r["summary"]["copied"] == 3 and r["removed_sources"] == 3
    assert contents(env.drives / "D" / "프로젝트") == {"A\\plan.dwg": b"plan", "A\\sub\\spec.pdf": b"spec", "B\\site.jpg": b"site"}
    assert contents(env.recv) == {".pcmigrate-multi.json": (env.recv / ".pcmigrate-multi.json").read_bytes()}
    assert sorted(x.rsplit("\\", 1)[-1] for x in r["removed_files"]) == ["plan.dwg", "site.jpg", "spec.pdf"]  # 지운 파일 목록
    assert r["remaining_in_receive"]["files"] == 0 and r["success"] is True


def test_verification_failure_keeps_source_and_continues(env, monkeypatch):
    real = copier.copy_verified

    def fake(src, dst, **kw):
        if Path(src).name == "plan.dwg":  # 검증 실패 흉내: 대상에 아무것도 놓지 않고 실패
            return copier.FileResult(str(src), str(dst), copier.FAILED, reason=failure.MISMATCH, error="복사 후 내용 검사 실패")
        return real(src, dst, **kw)
    monkeypatch.setattr(copier, "copy_verified", fake)
    rep = env.run(apply=True, same_volume_fn=_no_vol)
    assert (env.recv / "D" / "프로젝트" / "A" / "plan.dwg").read_bytes() == b"plan"  # 받기 폴더에 그대로
    assert not (env.drives / "D" / "프로젝트" / "A" / "plan.dwg").exists()
    assert [(r.code, r.expected.rsplit("\\", 1)[-1]) for r in rep.failed] == [(failure.MISMATCH, "plan.dwg")]
    assert rep.to_dict()["removed_sources"] == 2 and not (env.recv / "D" / "프로젝트" / "B" / "site.jpg").exists()


def test_mismatched_copy_is_never_deleted_from_receive(env, monkeypatch):
    """복사 도중 내용이 어긋나면(copy_verified 의 내부 검증이 실패) 임시 파일만 지우고 받기 폴더 파일은 남는다."""
    real_sha = copier.sha256_file
    calls = {"n": 0}

    def bad_sha(path, *a, **k):
        h = real_sha(path, *a, **k)
        if str(path).endswith(copier.PART_SUFFIX):
            calls["n"] += 1
            return "0" * 64
        return h
    monkeypatch.setattr(copier, "sha256_file", bad_sha)
    rep = env.run(apply=True, same_volume_fn=_no_vol)
    assert calls["n"] == 3 and rep.failed_count == 3 and rep.removed_sources == 0
    assert len(contents(env.recv)) == 4 and not [p for p in env.drives.rglob("*") if p.is_file()]  # 대상엔 폴더만(파일/임시 파일 없음)
    assert not list(env.drives.rglob("*.pcmigrate-part"))


def test_source_delete_failure_is_reported_as_leftover(env, monkeypatch):
    real = os.unlink

    def unlink(p, *a, **k):
        if str(p).endswith("site.jpg") and "recv" in str(p):
            raise PermissionError(5, "denied", str(p))
        return real(p, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink)
    rep = env.run(apply=True, same_volume_fn=_no_vol)
    assert [r.code for r in rep.leftover] == [po.LEFTOVER] and rep.failed_count == 0
    assert (env.drives / "D" / "프로젝트" / "B" / "site.jpg").exists() and rep.to_dict()["success"] is False


def test_exdev_move_falls_back_to_copy_delete(env, monkeypatch):
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        if str(env.recv) in str(tmp) and not str(tmp).endswith(copier.PART_SUFFIX):
            raise OSError(errno.EXDEV, "cross-device")
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    rep = env.run(apply=True, same_volume_fn=lambda a, b: True)
    assert rep.to_dict()["summary"]["copied"] == 3 and rep.removed_sources == 3
    assert (env.drives / "D" / "프로젝트" / "A" / "plan.dwg").read_bytes() == b"plan"


# --------------------------------------------------------------------------- --keep / 불변 / 멱등
def test_keep_leaves_receive_unchanged(env):
    before = tree(env.recv)
    rep = env.run(apply=True, keep=True)
    assert tree(env.recv) == before  # 파일/폴더/크기/mtime 모두 그대로
    assert contents(env.drives / "D" / "프로젝트")["A\\plan.dwg"] == b"plan"
    r = rep.to_dict()
    assert r["mode"] == "keep" and r["summary"]["copied"] == 3 and r["removed_sources"] == 0 and r["removed_files"] == []


def test_keep_then_default_run_is_idempotent_and_cleans_receive(env):
    env.run(apply=True, keep=True)
    rep = env.run(apply=True, same_volume_fn=_no_vol)  # 이미 같은 내용 -> 복사 없이 받기 폴더 쪽만 정리
    r = rep.to_dict()
    assert r["summary"] == {"skipped_identical": 3} and r["removed_sources"] == 3 and r["failed_count"] == 0
    rep2 = env.run(apply=True, same_volume_fn=_no_vol)  # 한 번 더: 할 일이 없다
    assert rep2.to_dict()["total_files"] == 0 and rep2.to_dict()["summary"] == {}
    assert len(contents(env.drives / "D" / "프로젝트")) == 3


def test_rerun_after_interruption_resumes(env, monkeypatch):
    """두 번째 파일에서 드라이브가 분리된 뒤 다시 실행하면 남은 것만 이어서 놓는다. 첫 실행에서 처리된 것은 보존된다."""
    real = copier.copy_verified
    n = {"i": 0}

    def fake(src, dst, **kw):
        n["i"] += 1
        if n["i"] == 2:
            raise DriveDisconnected("x")
        return real(src, dst, **kw)
    monkeypatch.setattr(copier, "copy_verified", fake)
    with pytest.raises(DriveDisconnected) as ei:
        env.run(apply=True, same_volume_fn=_no_vol)
    rep = ei.value.report
    assert rep.to_dict()["interrupted"] == "drive_disconnected" and rep.counts["copied"] == 1
    monkeypatch.setattr(copier, "copy_verified", real)
    rep2 = env.run(apply=True, same_volume_fn=_no_vol)
    assert len(contents(env.drives / "D" / "프로젝트")) == 3 and rep2.failed_count == 0
    assert len([p for p in env.recv.rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate-")]) == 0
    assert not list(env.drives.rglob("*.pcmigrate-part"))


# --------------------------------------------------------------------------- 항목 단위 격리, 중단과 보고
def test_one_item_oserror_does_not_stop_others(env, monkeypatch):
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        if Path(tmp).name == "spec.pdf":
            raise PermissionError(5, "denied", str(tmp))
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    rep = env.run(apply=True)
    r = rep.to_dict()
    assert r["summary"] == {"moved": 2, "failed": 1} and r["failed_count"] == 1
    assert (env.drives / "D" / "프로젝트" / "A" / "plan.dwg").exists() and (env.drives / "D" / "프로젝트" / "B" / "site.jpg").exists()
    assert r["failed"][0]["reason_code"] == failure.PERMISSION and r["failed"][0]["expected"].endswith("spec.pdf")
    assert (env.recv / "D" / "프로젝트" / "A" / "sub" / "spec.pdf").read_bytes() == b"spec"
    assert errorlog.last_error() is not None  # 오류 로그에 남음
    assert r["success"] is False and rep.to_dict()["items"][0]["status"] == "partial"


def test_unexpected_exception_in_one_file_is_isolated_and_logged(env, monkeypatch):
    real = copier.copy_verified

    def fake(src, dst, **kw):
        if Path(src).name == "site.jpg":
            raise RuntimeError("boom")
        return real(src, dst, **kw)
    monkeypatch.setattr(copier, "copy_verified", fake)
    rep = env.run(apply=True, same_volume_fn=_no_vol)
    assert [r.code for r in rep.failed] == [failure.OTHER] and rep.counts["copied"] == 2
    _, text = errorlog.last_error()
    assert "boom" in text


def test_locked_file_fails_only_itself(env, monkeypatch):
    real = copier.copy_verified

    def fake(src, dst, **kw):
        if Path(src).name == "plan.dwg":
            return copier.FileResult(str(src), str(dst), copier.FAILED, reason=failure.IN_USE, error="사용 중")
        return real(src, dst, **kw)
    monkeypatch.setattr(copier, "copy_verified", fake)
    rep = env.run(apply=True, same_volume_fn=_no_vol)
    assert [r.code for r in rep.failed] == [failure.IN_USE] and (env.recv / "D" / "프로젝트" / "A" / "plan.dwg").exists()
    assert rep.to_dict()["summary"] == {"copied": 2, "failed": 1}


def test_disk_full_stops_with_report(env, monkeypatch):
    real = copier.copy_verified
    n = {"i": 0}

    def fake(src, dst, **kw):
        n["i"] += 1
        if n["i"] == 2:
            return copier.FileResult(str(src), str(dst), copier.FAILED, reason=failure.DISK_FULL, error="full")
        return real(src, dst, **kw)
    monkeypatch.setattr(copier, "copy_verified", fake)
    with pytest.raises(po.DiskFull) as ei:
        env.run(apply=True, same_volume_fn=_no_vol)
    d = ei.value.report.to_dict()
    assert d["interrupted"] == "disk_full" and d["summary"] == {"copied": 1, "failed": 1} and n["i"] == 2
    assert errorlog.last_error() is not None


def test_cancel_attaches_report(env):
    n = {"i": 0}

    def cancel():
        n["i"] += 1
        return n["i"] > 6
    with pytest.raises(Cancelled) as ei:
        env.run(apply=True, cancel=cancel, same_volume_fn=_no_vol)
    rep = ei.value.report
    assert rep.to_dict()["interrupted"] == "cancelled"
    done = rep.counts.get("copied", 0)
    assert done < 3 and len(contents(env.drives / "D")) == done
    assert rep.remaining["files"] == 3 - rep.removed_sources  # 받기 폴더에 남은 항목 보고


def test_report_totals_match_reality(tmp_path):
    e = Env(tmp_path)
    for i in range(5):
        put(e.recv / "D" / "w" / f"f{i}.txt", b"x" * (i + 1))
    put(e.recv / "D" / "Windows" / "n.txt", b"n")
    put(e.recv / "D" / "w" / "bad.txt", b"bad")
    put(e.drives / "D" / "w" / "f0.txt", b"DIFFERENT")  # 덮어쓰기 1개
    rep = e.run(apply=True, no_overwrite=False)
    r = rep.to_dict()
    actual_dst = contents(e.drives / "D")
    placed_files = [k for k in actual_dst if not k.endswith(".bak")]
    assert r["placed_files"] == len(placed_files) == 6
    assert r["placed_bytes"] == sum(len(actual_dst[k]) for k in placed_files)
    left = [p for p in e.recv.rglob("*") if p.is_file()]
    assert r["remaining_in_receive"]["files"] == len(left) == 1 and r["skipped_count"] == 1
    assert r["total_files"] == 6 and sum(v for k, v in r["summary"].items() if k != "failed") == 6
    assert len(r["placed"]) == 6


# --------------------------------------------------------------------------- CLI
@pytest.fixture
def cli_env(env, monkeypatch):
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", env.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    return env


def test_cli_default_is_preview_and_json(cli_env, capsys, tmp_path):
    before = tree(cli_env.recv)
    assert cli.main(["place", str(cli_env.recv), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["summary"]["would_move"] == 3 and tree(cli_env.recv) == before
    assert not any((cli_env.drives / "D").iterdir())


def test_cli_apply_writes_report(cli_env, capsys, tmp_path):
    rp = tmp_path / "out" / "r.json"
    rp.parent.mkdir()
    assert cli.main(["place", str(cli_env.recv), "--yes", "--report", str(rp)]) == 0
    data = json.loads(rp.read_text(encoding="utf-8"))
    assert data["summary"]["moved"] == 3 and data["mode"] == "move"
    assert "배치를 마쳤습니다" in capsys.readouterr().out


def test_cli_rejects_bad_map_and_report_inside_receive(cli_env, capsys):
    assert cli.main(["place", str(cli_env.recv), "--map", "DE"]) == 2
    assert cli.main(["place", str(cli_env.recv), "--report", str(cli_env.recv / "r.json")]) != 0
    assert not (cli_env.recv / "r.json").exists()


def test_cli_midway_drive_disconnect_still_writes_report_and_log(cli_env, capsys, tmp_path, monkeypatch):
    real = copier.copy_verified
    n = {"i": 0}

    def fake(src, dst, **kw):
        n["i"] += 1
        if n["i"] == 2:
            shutil.rmtree(cli_env.drives / "D")  # 드라이브가 사라짐
            return copier.FileResult(str(src), str(dst), copier.FAILED, reason=failure.DISCONNECTED, error="gone")
        return real(src, dst, **kw)
    monkeypatch.setattr(copier, "copy_verified", fake)
    monkeypatch.setattr(po, "_same_volume", _no_vol)
    monkeypatch.setitem(po.place.__kwdefaults__, "same_volume_fn", _no_vol)
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(cli_env.recv), "--yes", "--report", str(rp)]) == 1
    data = json.loads(rp.read_text(encoding="utf-8"))
    assert data["interrupted"] == "drive_disconnected" and data["summary"]["copied"] == 1
    assert "중간에 멈췄습니다" in capsys.readouterr().err and errorlog.last_error() is not None


def test_cli_unexpected_toplevel_exception_reports(cli_env, capsys, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(po, "discover", boom)
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(cli_env.recv), "--yes", "--report", str(rp)]) == 1
    data = json.loads(rp.read_text(encoding="utf-8"))
    assert data["interrupted"] == "error" and "kaboom" in data["error"] and data["success"] is False
    assert "kaboom" in errorlog.last_error()[1]


def test_cli_keyboard_interrupt_reports(cli_env, capsys, tmp_path, monkeypatch):
    real = copier.copy_verified

    def fake(src, dst, **kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(copier, "copy_verified", fake)
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(cli_env.recv), "--yes", "--keep", "--report", str(rp)]) == 130
    assert json.loads(rp.read_text(encoding="utf-8"))["interrupted"] == "cancelled"
    assert len(contents(cli_env.recv)) == 3 + 1  # 받기 폴더 그대로(3 파일 + 저널)


def test_cli_missing_receive_folder(capsys, tmp_path):
    assert cli.main(["place", str(tmp_path / "nope")]) == 1
    assert "배치 실패" in capsys.readouterr().err
