"""place --gather: 원래 위치에 놓을 수 없는 항목을 한 폴더로 모으기 시험(가짜 환경만).

test_place_original.py 의 Env(가짜 받기 폴더/가짜 드라이브)를 재사용한다. 모으는 폴더는 임시 폴더 안(보호 위치 검사는 주입으로 끈다).
"""
import json
import os
from pathlib import Path

import pytest

from pcmigrate import cli, copier, errorlog, failure, place_original as po
from pcmigrate.cancel import Cancelled
from pcmigrate.transfer_base import TransferError
from test_place_original import BIG, Env, contents, put, tree

NOPROT = lambda p, **k: None  # noqa: E731 - 임시 폴더(AppData 아래)를 보호 위치로 보지 않게


@pytest.fixture
def genv(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "Windows" / "x.dll", b"dll")  # 보호 위치
    put(e.recv / "D" / "work" / "ok.txt", b"ok")  # 정상 배치
    put(e.recv / "Z" / "data" / "z.txt", b"zz")  # 없는 드라이브
    put(e.recv / "설정 묶음" / "pkg.zip", b"pkg")  # 지정 이름 폴더
    put(e.recv / "loose.txt", b"l")
    e.g = tmp_path / "gather"
    return e


def run(e, **kw):
    kw.setdefault("gather", str(e.g))
    kw.setdefault("protected_fn", NOPROT)
    return e.run(**kw)


def test_only_location_problems_are_gathered_keeping_structure(genv):
    rep = run(genv, apply=True)
    assert contents(genv.g) == {"D\\Windows\\x.dll": b"dll", "Z\\data\\z.txt": b"zz", "설정 묶음\\pkg.zip": b"pkg", "loose.txt": b"l",
                                po.GUIDE_NAME: contents(genv.g)[po.GUIDE_NAME]}
    assert (genv.drives / "D" / "work" / "ok.txt").read_bytes() == b"ok"
    assert [p for p in genv.recv.rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate-")] == []
    d = rep.to_dict()
    assert d["skipped_count"] == 0 and len(d["gather"]["gathered"]) == 4 and d["gather"]["mode"] == "move"
    row = next(x for x in d["gather"]["gathered"] if x["expected"] == "D:\\Windows")
    assert row["gathered"].endswith("D\\Windows") and row["reason_code"] == po.PROTECTED and row["files"] == 1
    assert d["success"] is False and d["manual_count"] == 4  # 모았어도 사용자가 직접 놓아야 한다
    guide = (genv.g / po.GUIDE_NAME).read_text(encoding="utf-8-sig")
    assert "D\\Windows\\x.dll | D:\\Windows\\x.dll |" in guide and "Z\\data\\z.txt | Z:\\data\\z.txt" in guide
    assert str(genv.recv) not in guide and str(genv.tmp) not in guide  # 개인 경로 없음
    assert d["gather"]["guide_file"].endswith(po.GUIDE_NAME)
    text = "\n".join(po.summary_lines(rep))
    assert "[모은 항목]" in text and "원래 놓으려던 위치 | 모은 위치 | 사유" in text


def test_failures_and_non_location_skips_are_not_gathered(genv, monkeypatch):
    put(genv.drives / "D" / "work" / "ok.txt", b"OLD")  # --no-overwrite 로 거부될 파일
    put(genv.recv / "D" / "work" / "locked.txt", b"lk")
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        if Path(tmp).name == "locked.txt" and str(genv.g) not in str(dst):
            raise PermissionError(5, "denied", str(tmp))
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    rep = run(genv, apply=True, no_overwrite=True)
    d = rep.to_dict()
    assert (genv.recv / "D" / "work" / "locked.txt").read_bytes() == b"lk"  # 실패 항목은 받기 폴더에
    assert (genv.recv / "D" / "work" / "ok.txt").exists()  # 덮어쓰기 거부도 모으지 않음
    assert not (genv.g / "D" / "work").exists()
    assert [r["reason_code"] for r in d["failed"]] == [failure.PERMISSION]
    assert [r["reason_code"] for r in d["skipped"]] == [po.NO_OVERWRITE]
    assert len(d["gather"]["gathered"]) == 4


def test_cross_volume_copy_verify_delete_and_listing(genv):
    rep = run(genv, apply=True, same_volume_fn=lambda a, b: False)
    g = rep.to_dict()["gather"]
    assert g["mode"] == "copy-delete" and g["removed_sources"] == 4
    assert sorted(x.rsplit("\\", 1)[-1] for x in g["removed_files"]) == ["loose.txt", "pkg.zip", "x.dll", "z.txt"]
    assert (genv.g / "D" / "Windows" / "x.dll").read_bytes() == b"dll"
    assert not (genv.recv / "loose.txt").exists()


def test_keep_copies_only(genv):
    before = tree(genv.recv)
    rep = run(genv, apply=True, keep=True)
    assert tree(genv.recv) == before  # 받기 폴더는 그대로
    assert (genv.g / "loose.txt").read_bytes() == b"l"
    g = rep.to_dict()["gather"]
    assert g["mode"] == "keep" and g["removed_sources"] == 0 and g["removed_files"] == []


def test_dry_run_shows_gather_and_deletions_without_changes(genv):
    before = (tree(genv.recv), tree(genv.drives))
    rep = run(genv, same_volume_fn=lambda a, b: False)
    assert (tree(genv.recv), tree(genv.drives)) == before and not genv.g.exists()
    d = rep.to_dict()["gather"]
    assert d["delete_planned_files"] == 4 and d["delete_planned_bytes"] == 3 + 2 + 3 + 1
    assert {x["status"] for x in d["gathered"]} == {"preview"}
    text = "\n".join(po.summary_lines(rep))
    assert "모을 항목 4건" in text and "삭제 예정" in text


def test_collision_keeps_bak_and_structure(genv):
    put(genv.g / "D" / "Windows" / "x.dll", b"OLD")
    rep = run(genv, apply=True)
    assert (genv.g / "D" / "Windows" / "x.dll").read_bytes() == b"dll"
    assert (genv.g / "D" / "Windows" / "x.dll.bak").read_bytes() == b"OLD"
    assert any(p["backup"].endswith("x.dll.bak") for p in rep.to_dict()["gather"]["placed"])


def test_idempotent_rerun(genv):
    run(genv, apply=True)
    snap = contents(genv.g)
    rep2 = run(genv, apply=True)
    assert contents(genv.g) == snap and rep2.to_dict()["gather"]["gathered_count"] == 0
    # --keep 는 받기 폴더에 남으므로 다시 실행하면 '이미 같음'으로 지나간다
    (genv.tmp / "two").mkdir()
    genv2 = Env(genv.tmp / "two")
    put(genv2.recv / "Z" / "a.txt", b"a")
    genv2.g = genv2.tmp / "g2"
    run(genv2, apply=True, keep=True)
    rep3 = run(genv2, apply=True, keep=True)
    assert rep3.to_dict()["gather"]["summary"] == {"skipped_identical": 1}


# --------------------------------------------------------------------------- 폴더 검증/OneDrive
def test_rejects_gather_inside_or_around_receive(genv):
    before = tree(genv.recv)
    for bad in (genv.recv / "모음", genv.tmp):  # 받기 폴더 안 / 받기 폴더를 포함
        with pytest.raises(TransferError) as ei:
            run(genv, apply=True, gather=str(bad))
        assert ei.value.report.to_dict()["interrupted"] == "error" and "받기 폴더" in ei.value.report.meta["error"]
    assert tree(genv.recv) == before and not any((genv.drives / "D").iterdir())  # 아무것도 놓지 않았다


def test_rejects_protected_gather_folder(genv):
    with pytest.raises(TransferError) as ei:
        genv.run(apply=True, gather="C:\\Windows\\이전 파일")
    assert "보호 위치" in str(ei.value)


def test_onedrive_desktop_default_warns(genv, tmp_path):
    od = tmp_path / "OneDrive"
    desk = od / "Desktop"
    desk.mkdir(parents=True)
    warns = []
    rep = genv.run(gather="", desktop_fn=lambda: str(desk), env={"OneDrive": str(od)}, protected_fn=NOPROT, warn=warns.append)
    gi = rep.gather_info
    assert gi["onedrive_redirected"] is True and gi["folder"] == str(desk / po.GATHER_DIR_NAME) and gi["default_folder"] is True
    assert any("OneDrive" in w and "--yes" in w and "--gather=폴더" in w for w in warns)
    assert "OneDrive 동기화 폴더" in "\n".join(po.summary_lines(rep))
    assert not (desk / po.GATHER_DIR_NAME).exists()  # 미리보기는 만들지 않는다
    warns.clear()
    plain = tmp_path / "Desk"
    plain.mkdir()
    rep2 = genv.run(gather="", desktop_fn=lambda: str(plain), env={}, protected_fn=NOPROT, warn=warns.append)
    assert rep2.gather_info["onedrive_redirected"] is False and not any("OneDrive" in w for w in warns)
    assert po.onedrive_redirected("C:\\Users\\x\\OneDrive - 회사\\Desktop", {}) and not po.onedrive_redirected("D:\\data", {})


def test_default_desktop_uses_known_folder_helper(monkeypatch, tmp_path):
    from pcmigrate import find_data
    monkeypatch.setattr(find_data, "default_user_folders", lambda env=None: {"desktop": str(tmp_path / "Dt")})
    assert po.default_desktop() == str(tmp_path / "Dt")
    assert po.default_gather_folder() == tmp_path / "Dt" / po.GATHER_DIR_NAME


# --------------------------------------------------------------------------- 오류·취소·보고
def test_one_gather_item_error_is_isolated_logged_and_reported(genv, monkeypatch):
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        if Path(tmp).name == "z.txt":
            raise PermissionError(5, "denied", str(tmp))
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    rep = run(genv, apply=True)
    d = rep.to_dict()
    assert (genv.recv / "Z" / "data" / "z.txt").read_bytes() == b"zz"  # 실패한 항목은 받기 폴더에 그대로
    assert (genv.g / "D" / "Windows" / "x.dll").exists() and (genv.g / "loose.txt").exists()
    assert [f["reason_code"] for f in d["failed"]] == [failure.PERMISSION] and d["failed"][0]["expected"].endswith("z.txt")
    assert len(d["gather"]["gathered"]) == 3 and any(s["reason_code"] == po.DRIVE_MISSING for s in d["skipped"])
    assert errorlog.last_error() is not None


def test_cancel_during_gather_keeps_report(genv, monkeypatch):
    real = po._gather_one
    n = {"i": 0}

    def one(*a, **k):
        n["i"] += 1
        if n["i"] > 4 + 1:  # 훑기(4) 뒤 실행 단계 첫 항목만 처리
            raise Cancelled()
        return real(*a, **k)
    monkeypatch.setattr(po, "_gather_one", one)
    with pytest.raises(Cancelled) as ei:
        run(genv, apply=True)
    d = ei.value.report.to_dict()
    assert d["interrupted"] == "cancelled" and d["gather"]["gathered_count"] == 1 and d["gather"]["summary"]["moved"] == 1
    # 다시 실행하면 나머지를 이어서 모은다
    monkeypatch.setattr(po, "_gather_one", real)
    rep = run(genv, apply=True)
    assert rep.to_dict()["gather"]["gathered_count"] == 3
    assert len([p for p in genv.g.rglob("*") if p.is_file() and p.name != po.GUIDE_NAME]) == 4


def test_unexpected_error_mid_gather_reports_and_logs(genv, monkeypatch):
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        if Path(tmp).name == "pkg.zip":
            raise RuntimeError("boom-gather")
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    rep = run(genv, apply=True)
    assert [f.code for f in rep.failed] == [failure.OTHER] and len(rep.gathered) == 3
    assert "boom-gather" in errorlog.last_error()[1]


def test_guide_write_failure_does_not_stop(genv, monkeypatch):
    real = os.replace

    def deny(a, b, *x, **k):  # 안내 파일 교체만 실패시킨다
        if str(b).endswith(po.GUIDE_NAME):
            raise PermissionError(5, "denied", str(b))
        return real(a, b, *x, **k)
    monkeypatch.setattr(os, "replace", deny)
    rep = run(genv, apply=True)
    d = rep.to_dict()
    assert d["gather"]["guide_error"] and "guide_file" not in d["gather"] and d["gather"]["gathered_count"] == 4
    assert errorlog.last_error() is not None and (genv.g / "loose.txt").exists()


def test_gather_blocked_by_space_leaves_items(genv):
    from pcmigrate.fs import DestInfo
    small = DestInfo("x", "fixed", "NTFS", 1, 1, "D:")
    rep = run(genv, apply=True, same_volume_fn=lambda a, b: False, dest_info_fn=lambda p: small if "gather" in str(p) else BIG)
    assert rep.gather_info["blocked"] == "no_space" and rep.gathered == []
    assert (genv.recv / "loose.txt").exists() and not genv.g.exists()


# --------------------------------------------------------------------------- CLI
@pytest.fixture
def cenv(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    put(e.recv / "Z" / "data" / "z.txt", b"zz")
    put(e.recv / "D" / "work" / "ok.txt", b"ok")
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", e.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    monkeypatch.setattr(po, "protected_reason", lambda p, is_dir=True: None)
    return e


def test_cli_gather_folder_json_and_report(cenv, capsys, tmp_path):
    g = tmp_path / "모음"
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(cenv.recv), f"--gather={g}", "--yes", "--report", str(rp), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["gather"]["gathered_count"] == 1 and (g / "Z" / "data" / "z.txt").read_bytes() == b"zz"
    assert json.loads(rp.read_text(encoding="utf-8"))["gather"]["gathered"][0]["expected"] == "Z:\\"
    assert (g / po.GUIDE_NAME).is_file()


def test_cli_default_desktop_onedrive_warning_in_preview(cenv, capsys, tmp_path, monkeypatch):
    od = tmp_path / "OneDrive"
    (od / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("OneDrive", str(od))
    monkeypatch.setattr(po, "default_desktop", lambda: str(od / "Desktop"))
    before = tree(cenv.recv)
    assert cli.main(["place", str(cenv.recv), "--gather"]) == 0
    cap = capsys.readouterr()
    assert "OneDrive" in cap.err and "--gather=폴더" in cap.err and "OneDrive 동기화 폴더" in cap.out
    assert tree(cenv.recv) == before and not (od / "Desktop" / po.GATHER_DIR_NAME).exists()


def test_cli_rejects_gather_inside_receive_and_still_reports(cenv, capsys, tmp_path):
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(cenv.recv), f"--gather={cenv.recv / 'g'}", "--yes", "--report", str(rp)]) == 1
    data = json.loads(rp.read_text(encoding="utf-8"))
    assert data["interrupted"] == "error" and "받기 폴더" in data["error"]
    assert not (cenv.drives / "D" / "work").exists()
