"""place 재검증 후속(stage48 N2~N6): 모으는 중 중단 시 부분 항목 반영, mtime 해상도, TOCTOU, `~` 확장, 8.3 문구.

임시 폴더와 주입만 쓴다(홈/바탕화면/실제 드라이브를 만들거나 바꾸지 않는다).
"""
import errno
import os
import subprocess
from pathlib import Path

import pytest

from pcmigrate import cli, copier, place_original as po
from pcmigrate.cancel import Cancelled
from pcmigrate.fs import DestInfo
from test_place_original import Env, contents, put

NOPROT = lambda p, **k: None  # noqa: E731


def guide_lines(g: Path) -> list[str]:
    text = (g / po.GUIDE_NAME).read_text(encoding="utf-8-sig")
    return [x for x in text.splitlines() if " | " in x and not x.startswith("모은 위치")]


@pytest.fixture
def wenv(tmp_path):
    e = Env(tmp_path, letters="D")
    for i in range(5):
        put(e.recv / "D" / "Windows" / f"f{i}.dll", b"x%d" % i)
    put(e.recv / "Z" / "z.txt", b"z")
    e.g = tmp_path / "gather"
    return e


def run(e, **kw):
    kw.setdefault("gather", str(e.g))
    kw.setdefault("protected_fn", NOPROT)
    return e.run(**kw)


# --------------------------------------------------------------------------- N2
def test_cancel_mid_folder_reflects_partial_row_in_every_report_part(wenv):
    g = wenv.g

    def cancel():
        w = g / "D" / "Windows"
        return w.exists() and len(list(w.glob("*"))) >= 2
    with pytest.raises(Cancelled) as ei:
        run(wenv, apply=True, cancel=cancel)
    d = ei.value.report.to_dict()
    moved = sorted(p.name for p in (g / "D" / "Windows").glob("*"))
    left = sorted(p.name for p in (wenv.recv / "D" / "Windows").glob("*"))
    assert len(moved) >= 2 and len(moved) + len(left) == 5
    part = [x for x in d["gather"]["gathered"] if x["expected"] == "D:\\Windows"]
    assert len(part) == 1 and part[0]["status"] == "partial" and part[0]["files"] == len(moved)  # 모은 항목 표
    lines = guide_lines(g)
    everything = sorted(p.name for p in g.rglob("*") if p.is_file() and p.name != po.GUIDE_NAME)  # Z 항목은 먼저 끝났다
    assert sorted(x.split(" | ")[0].rsplit("\\", 1)[-1] for x in lines) == everything == sorted(moved + ["z.txt"])  # 안내 = 실제 옮긴 파일
    row = next(x for x in d["skipped"] if x["expected"] == "D:\\Windows")
    assert row["files"] == len(left)  # 건너뜀 표는 받기 폴더에 남은 만큼
    assert not any(x["expected"] == "Z:\\" for x in d["skipped"]) and d["interrupted"] == "cancelled"  # Z 는 이미 모았다
    assert any(i["origin"] == "gather" and i["status"] == "partial" for i in d["items"])
    # 재개: 이미 모은 것도 안내에 남고 나머지가 이어서 모인다
    first = set(lines)
    run(wenv, apply=True)
    final = guide_lines(g)
    assert first <= set(final) and len(final) == 6 and len(contents(g)) == 7  # 파일 6개 + 안내 파일


def test_disk_full_mid_folder_reflects_partial_row(wenv, monkeypatch):
    real = copier._install
    n = {"i": 0}

    def inst(tmp, dst, res, lp=os.fspath):
        if str(wenv.g) in str(dst):
            n["i"] += 1
            if n["i"] == 4:  # z.txt(1), f0(2), f1(3) 다음 네 번째에서 공간 부족
                raise OSError(errno.ENOSPC, "full", str(dst))
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    with pytest.raises(po.DiskFull) as ei:
        run(wenv, apply=True)
    d = ei.value.report.to_dict()
    part = [x for x in d["gather"]["gathered"] if x["expected"] == "D:\\Windows"]
    assert part and part[0]["status"] == "partial" and part[0]["files"] == 2 and len(guide_lines(wenv.g)) == 3
    assert next(x for x in d["skipped"] if x["expected"] == "D:\\Windows")["files"] == 3


# --------------------------------------------------------------------------- N3
def test_space_check_treats_mtime_within_fat_resolution_as_same(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "P" / "x.bin", b"A" * 1000)
    put(e.drives / "D" / "P" / "x.bin", b"B" * 1000)
    small = DestInfo("x", "fixed", "NTFS", 500, 1, "D:")
    src_ns = os.stat(e.recv / "D" / "P" / "x.bin").st_mtime_ns
    dst = e.drives / "D" / "P" / "x.bin"
    os.utime(dst, ns=(src_ns + 1_500_000_000,) * 2)  # 1.5초 차이: FAT 해상도 안 -> 같은 것으로 본다
    rep = e.run(same_volume_fn=lambda a, b: False, dest_info_fn=lambda p: small)
    assert rep.meta["space"]["D"]["required_bytes"] == 0
    os.utime(dst, ns=(src_ns + 5_000_000_000,) * 2)  # 5초 차이 -> 다른 파일로 보고 필요량에 더한다
    rep2 = e.run(same_volume_fn=lambda a, b: False, dest_info_fn=lambda p: small)
    assert rep2.meta["space"]["D"]["required_bytes"] >= 1000 and [r.code for r in rep2.skipped] == [po.NO_SPACE]


# --------------------------------------------------------------------------- N4
def _junction(link: Path, target: Path) -> None:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("정션을 만들 수 없는 환경")


@pytest.mark.parametrize("vol", [True, False])
def test_folder_swapped_for_junction_after_scan_is_not_touched(tmp_path, monkeypatch, vol):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "P" / "a.txt", b"mine")
    outside = tmp_path / "outside"
    put(outside / "a.txt", b"OUTSIDE")
    hold = tmp_path / "hold"
    real = po._Placer._apply_file_inner
    done = {"swapped": False}

    def swap_then_apply(self, *a, **k):
        if not done["swapped"]:  # 폴더 검사(_src_safe, 캐시)가 끝난 뒤 폴더를 정션으로 바꾼다
            done["swapped"] = True
            os.rename(e.recv / "D" / "P", hold)
            _junction(e.recv / "D" / "P", outside)
        return real(self, *a, **k)
    monkeypatch.setattr(po._Placer, "_apply_file_inner", swap_then_apply)
    rep = e.run(apply=True, same_volume_fn=lambda a, b: vol)
    assert (outside / "a.txt").read_bytes() == b"OUTSIDE"  # 받기 폴더 밖 파일은 그대로
    assert not (e.drives / "D" / "P" / "a.txt").exists()
    assert [r.code for r in rep.failed] == [po.LINK]


def test_chain_ok_is_fresh_while_cache_is_stale(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "P" / "a.txt", b"a")
    p = po._Placer(e.recv, dry_run=False, keep=False, no_overwrite=False, drive_root_fn=None, same_volume_fn=lambda a, b: True,
                   cancel=None, progress_info=None, report=po.PlaceReport(False, False, str(e.recv)))
    f = e.recv / "D" / "P" / "a.txt"
    assert p._src_safe(f) and p._chain_ok(f)
    os.rename(e.recv / "D" / "P", tmp_path / "hold")
    outside = tmp_path / "outside"
    put(outside / "a.txt", b"o")
    _junction(e.recv / "D" / "P", outside)
    assert p._src_safe(f) is True  # 캐시는 낡았다
    assert p._chain_ok(f) is False  # 직전 확인은 잡는다
    p._real_cache.clear()  # 단계가 바뀔 때 비우는 동작
    assert p._src_safe(f) is False


# --------------------------------------------------------------------------- N5
def test_gather_arg_tilde_rules(monkeypatch, tmp_path):
    home = str(tmp_path / "home")
    monkeypatch.setenv("USERPROFILE", home)
    monkeypatch.setenv("HOME", home)
    assert os.path.expanduser("~") == home  # 홈 주입 확인(실제 홈을 건드리지 않는다)
    assert cli._gather_arg("~") == os.path.normpath(home)
    assert cli._gather_arg("~/x/y") == os.path.normpath(os.path.join(home, "x", "y"))
    assert cli._gather_arg("~\\x") == os.path.normpath(os.path.join(home, "x"))
    assert cli._gather_arg("~backup") == "~backup"  # 사용자 이름 확장으로 보지 않는다
    assert cli._gather_arg("D:/a/b\\c") == os.path.normpath("D:\\a\\b\\c")  # 슬래시 혼합 정규화
    assert cli._gather_arg("") == ""


def test_resolve_gather_does_not_expand_tilde_names(tmp_path):
    g, info = po.resolve_gather("~backup", tmp_path / "recv_x", protected_fn=NOPROT)
    assert g == Path(os.path.abspath("~backup")) and info["default_folder"] is False


# --------------------------------------------------------------------------- N6
def test_short_name_wording_and_missing_drive_order(monkeypatch):
    monkeypatch.setattr(po, "expand_short_names", lambda p: None)
    why = po.protected_reason("C:\\PROGRA~1\\x")
    assert why and "8.3" in why and "건너뜀" in why
    missing = next((c for c in "QXYZWVUTS" if not os.path.isdir(f"{c}:\\")), None)
    if missing:
        assert po.protected_reason(f"{missing}:\\PROGRA~1\\x") is None  # 없는 드라이브는 '대상 드라이브 없음'으로 먼저 보고된다
