"""place --gather 경로 처리·안내 파일·부분 모으기·재실행 수렴·OneDrive 거부(stage48 G1~G7)와 find_data 약한 형식(G8) 시험.

가능한 한 실제 파일 시스템(임시 폴더)을 쓰고, 보호 위치 판정은 전역 무력화 대신 place() 의 protected_fn 주입으로만 끈다.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from pcmigrate import cli, copier, errorlog, find_data as fd, place_original as po
from pcmigrate.cancel import Cancelled
from pcmigrate.fs import DestInfo
from pcmigrate.transfer_base import TransferError
from test_place_original import BIG, Env, contents, put, tree

NOPROT = lambda p, **k: None  # noqa: E731


@pytest.fixture
def genv(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "Windows" / "x.dll", b"dll")
    put(e.recv / "D" / "work" / "ok.txt", b"ok")
    put(e.recv / "Z" / "data" / "z.txt", b"zz")
    put(e.recv / "설정 묶음" / "pkg.zip", b"pkg")
    put(e.recv / "loose.txt", b"l")
    e.g = tmp_path / "gather"
    return e


def run(e, **kw):
    kw.setdefault("gather", str(getattr(e, "g", e.tmp / "gather")))
    kw.setdefault("protected_fn", NOPROT)
    return e.run(**kw)


@pytest.fixture
def cenv(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    put(e.recv / "Z" / "data" / "z.txt", b"zz")
    put(e.recv / "D" / "work" / "ok.txt", b"ok")
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", e.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    monkeypatch.setitem(po.place.__kwdefaults__, "protected_fn", NOPROT)  # 주입(전역 무력화 아님)
    return e


# --------------------------------------------------------------------------- G1 경로 처리
def test_cli_gather_unexpanded_var_rejected_without_creating_anything(cenv, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = tree(cenv.recv)
    assert cli.main(["place", str(cenv.recv), "--gather", "%NOPE_VAR_XYZ%\\g", "--yes"]) == 1
    assert "확장되지 않았습니다" in capsys.readouterr().err
    assert tree(cenv.recv) == before and not [p for p in tmp_path.glob("%*")]


def test_cli_gather_trailing_quote_rejected(cenv, capsys, tmp_path):
    with pytest.raises(SystemExit) as ei:
        cli.main(["place", str(cenv.recv), "--gather", str(tmp_path / "g") + '"', "--yes"])
    assert ei.value.code == 2 and "따옴표" in capsys.readouterr().err
    assert (cenv.recv / "Z" / "data" / "z.txt").exists()


def test_cli_gather_drive_root_relative_form_rejected(cenv, capsys):
    assert cli.main(["place", str(cenv.recv), "--gather", "\\x\\y", "--yes"]) == 1
    assert "역슬래시" in capsys.readouterr().err and (cenv.recv / "Z" / "data" / "z.txt").exists()


def test_cli_gather_tilde_expands_to_home(cenv, capsys):
    rc = cli.main(["place", str(cenv.recv), "--gather", "~/__pcm_gather_test__", "--json"])  # 미리보기: 아무것도 만들지 않는다
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["gather"]["folder"] == os.path.join(os.path.expanduser("~"), "__pcm_gather_test__")
    assert not os.path.exists(out["gather"]["folder"])


def test_cli_gather_empty_value_announces_default(cenv, capsys, tmp_path, monkeypatch):
    desk = tmp_path / "Desk"
    desk.mkdir()
    monkeypatch.setattr(po, "default_desktop", lambda: str(desk))
    assert cli.main(["place", str(cenv.recv), "--gather="]) == 0
    assert "기본 폴더" in capsys.readouterr().err and not (desk / po.GATHER_DIR_NAME).exists()


def _missing_drive():
    for letter in "QXYZWVUTS":
        if not os.path.isdir(f"{letter}:\\"):
            return letter
    pytest.skip("없는 드라이브 문자를 찾을 수 없음")


def test_gather_on_missing_drive_rejected_before_any_move(genv):
    q = _missing_drive()
    before = (tree(genv.recv), tree(genv.drives))
    with pytest.raises(TransferError) as ei:
        run(genv, apply=True, gather=f"{q}:\\모음")
    assert "드라이브" in str(ei.value) and "아무것도 옮기지 않았" in str(ei.value) and "원본" not in str(ei.value).replace("원본과는", "")
    assert (tree(genv.recv), tree(genv.drives)) == before  # 정상 배치 대상(D)도 건드리지 않았다
    assert ei.value.report.meta["interrupted"] == "error"


def test_cli_gather_missing_drive_writes_report_and_one_line_error(cenv, capsys, tmp_path):
    q = _missing_drive()
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(cenv.recv), f"--gather={q}:\\모음", "--yes", "--report", str(rp)]) == 1
    cap = capsys.readouterr()
    assert json.loads(rp.read_text(encoding="utf-8"))["interrupted"] == "error"
    assert "배치 실패" in cap.err and "이어서" not in cap.err and "배치를 마쳤습니다" not in cap.out  # G5: 요약 없이 한 줄


def test_gather_unwritable_or_readonly_rejected_as_gather_problem(genv, monkeypatch):
    cd = DestInfo("x", "cdrom", None, 0, None, "")
    with pytest.raises(TransferError) as ei:
        run(genv, apply=True, dest_info_fn=lambda p: cd)
    assert "CD/DVD" in str(ei.value)

    def deny(folder):
        raise PermissionError(5, "denied", str(folder))
    monkeypatch.setattr(po, "_probe_writable", deny)
    before = tree(genv.recv)
    with pytest.raises(TransferError) as ei2:
        run(genv, apply=True)
    assert "모으는 폴더/드라이브 문제" in str(ei2.value) and "원본과는 무관" in str(ei2.value) and tree(genv.recv) == before


def test_gather_junction_into_receive_rejected_by_realpath(genv, tmp_path):
    (genv.recv / "sub").mkdir()
    link = tmp_path / "gj"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(genv.recv / "sub")], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("정션을 만들 수 없는 환경")
    with pytest.raises(TransferError) as ei:
        run(genv, apply=True, gather=str(link))
    assert "받기 폴더" in str(ei.value)


def test_gather_overlapping_a_unit_destination_rejected(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "P" / "a.txt", b"a")
    put(e.recv / "Z" / "z.txt", b"z")
    e.journal({"d\\p": {"src": "D:\\P", "rel": "D\\P", "status": "done"}})
    with pytest.raises(TransferError) as ei:
        run(e, apply=True, gather=str(e.drives / "D" / "P" / "모음"))
    assert "겹칩니다" in str(ei.value) and (e.recv / "D" / "P" / "a.txt").exists()


# --------------------------------------------------------------------------- G2 OneDrive
def test_onedrive_default_gather_cannot_be_applied_even_with_yes(genv, tmp_path, capsys):
    od = tmp_path / "OneDrive"
    desk = od / "Desktop"
    desk.mkdir(parents=True)
    kw = dict(gather="", desktop_fn=lambda: str(desk), env={"OneDrive": str(od)}, protected_fn=NOPROT)
    before = (tree(genv.recv), tree(genv.drives))
    with pytest.raises(TransferError) as ei:
        genv.run(apply=True, **kw)
    msg = str(ei.value)
    assert "--gather 폴더" in msg and "--yes 로도 적용할 수 없습니다" in msg and "후보" in msg
    assert (tree(genv.recv), tree(genv.drives)) == before and not (desk / po.GATHER_DIR_NAME).exists()
    warns = []
    rep = genv.run(warn=warns.append, **kw)  # 미리보기는 가능(경고와 대안 안내)
    assert rep.gather_info["onedrive_blocked"] is True and any("--gather 폴더" in w for w in warns)


def test_onedrive_explicit_folder_warns_but_proceeds(genv, tmp_path):
    od = tmp_path / "OneDrive"
    od.mkdir()
    warns = []
    rep = genv.run(apply=True, gather=str(od / "모음"), env={"OneDrive": str(od)}, protected_fn=NOPROT, warn=warns.append)
    assert (od / "모음" / "Z" / "data" / "z.txt").exists() and rep.gather_info["onedrive_redirected"]
    assert any("OneDrive" in w for w in warns) and not any("--yes" in w for w in warns)  # 이미 --yes(적용)이면 문구 제거
    warns2 = []
    genv.run(gather=str(od / "모음2"), env={"OneDrive": str(od)}, protected_fn=NOPROT, warn=warns2.append)
    assert any("--yes" in w for w in warns2)


def test_onedrive_detection_precision():
    assert po.onedrive_redirected("C:\\Users\\u\\OneDrive\\Desktop", {})
    assert po.onedrive_redirected("C:\\Users\\u\\OneDrive - 회사\\Desktop", {})
    assert not po.onedrive_redirected("D:\\OneDriveBackup\\x", {}) and not po.onedrive_redirected("D:\\data\\x", {})
    assert po.onedrive_redirected("E:\\custom\\sync\\Desktop", {"OneDriveCommercial": "E:\\custom\\sync"})  # 환경변수 우선


# --------------------------------------------------------------------------- G3 안내 파일(중단/재개)
def test_guide_written_on_cancel_and_resume_keeps_earlier_entries(genv, monkeypatch):
    real = po._gather_one
    n = {"i": 0}

    def one(*a, **k):
        n["i"] += 1
        if n["i"] > 4 + 1:
            raise Cancelled()
        return real(*a, **k)
    monkeypatch.setattr(po, "_gather_one", one)
    with pytest.raises(Cancelled) as ei:
        run(genv, apply=True)
    guide = genv.g / po.GUIDE_NAME
    assert guide.is_file() and ei.value.report.gather_info["guide_file"]
    first = guide.read_text(encoding="utf-8-sig")
    assert "|" in first and first.count("\n") > 4
    monkeypatch.setattr(po, "_gather_one", real)
    run(genv, apply=True)
    final = guide.read_text(encoding="utf-8-sig")
    for line in first.splitlines()[4:]:
        if line.strip():
            assert line in final  # 앞 실행에서 모은 것도 안내에 남아 있다
    assert "x.dll" in final and "z.txt" in final and "pkg.zip" in final and "loose.txt" in final


def test_guide_written_on_disk_full(genv, monkeypatch):
    import errno
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        if Path(tmp).name == "z.txt":
            raise OSError(errno.ENOSPC, "full", str(tmp))
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    with pytest.raises(po.DiskFull):
        run(genv, apply=True)
    assert (genv.g / po.GUIDE_NAME).is_file()


# --------------------------------------------------------------------------- G4 부분 모으기
def test_partial_gather_updates_rows_counts_and_expected_paths(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "Windows" / "a.dll", b"a")
    put(e.recv / "D" / "Windows" / "b.dll", b"bb")
    e.g = tmp_path / "gather"
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        if Path(tmp).name == "b.dll":
            raise PermissionError(5, "denied", str(tmp))
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    rep = run(e, apply=True)
    d = rep.to_dict()
    assert (e.g / "D" / "Windows" / "a.dll").exists() and (e.recv / "D" / "Windows" / "b.dll").exists()
    g = d["gather"]["gathered"][0]
    assert g["status"] == "partial" and g["expected"] == "D:\\Windows" and g["current"].endswith("D\\Windows") and g["files"] == 1
    row = d["skipped"][0]
    assert row["files"] == 1 and row["current"].endswith("D\\Windows")  # 받기 폴더에 남은 만큼으로 갱신
    assert d["failed"][0]["expected"] == "D:\\Windows\\b.dll"  # 실패 행도 원래 놓으려던 경로
    assert d["manual_count"] == 2  # 건너뜀 1(남은 것) + 모은 1, 실패 행은 이중 집계하지 않음
    assert any(i["origin"] == "gather" and i["status"] == "partial" for i in d["items"])
    assert "'현재 위치' 열" in "\n".join(po.summary_lines(rep)) and "그대로 있습니다" not in "\n".join(po.summary_lines(rep))


# --------------------------------------------------------------------------- G5/G6/G7
def test_cli_missing_receive_folder_saves_report(tmp_path, capsys):
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(tmp_path / "nope"), "--report", str(rp)]) == 1
    cap = capsys.readouterr()
    assert json.loads(rp.read_text(encoding="utf-8"))["interrupted"] == "error" and "배치 실패" in cap.err and "이어서" not in cap.err


def test_rerun_converges_without_skeleton_rows(genv):
    run(genv, apply=True)
    rep2 = run(genv, apply=True)
    d = rep2.to_dict()
    assert d["skipped"] == [] and d["gather"]["gathered_count"] == 0 and d["success"] is True and d["manual_count"] == 0


def test_guide_dedupes_on_repeat_and_caps_per_folder(tmp_path):
    e = Env(tmp_path, letters="D")
    for i in range(520):
        put(e.recv / "Z" / "many" / f"f{i:04d}.txt", b"x")
    e.g = tmp_path / "gather"
    run(e, apply=True, keep=True)
    run(e, apply=True, keep=True)  # 같은 내용으로 다시 실행해도 줄이 늘지 않는다
    text = (e.g / po.GUIDE_NAME).read_text(encoding="utf-8-sig")
    lines = [x for x in text.splitlines() if " | " in x and not x.startswith("모은 위치")]
    assert len(lines) == po.GUIDE_FILES_PER_ROW + 1 and sum("파일이 많아" in x for x in lines) == 1 and text.count("이전 안내:") == 1
    assert len(lines) == len(set(lines))


def test_guide_size_cap_cuts_on_line_boundary(genv, monkeypatch):
    for i in range(60):
        put(genv.recv / "Z" / "many" / f"file_{i:03d}.txt", b"x")
    monkeypatch.setattr(po, "GUIDE_MAX_BYTES", 1500)
    run(genv, apply=True)
    raw = (genv.g / po.GUIDE_NAME).read_bytes()
    text = raw.decode("utf-8-sig")
    assert len(raw) < 1500 + 200 and "용량 한도로 생략" in text and text.endswith("\n")
    body = [x for x in text.splitlines() if " | " in x and not x.startswith("모은 위치")]
    assert all(len(x.split(" | ")) >= 3 for x in body)  # 잘린 줄 없음


def test_gather_only_summary_has_no_meaningless_values(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "Z" / "z.txt", b"z")
    rep = run(e)
    text = "\n".join(po.summary_lines(rep))
    assert "방식: -" not in text and "이동 0개" not in text and "복사 0개" not in text and "모을 항목 1건" in text


# --------------------------------------------------------------------------- G8 find_data: 약한(등급 C) 형식
def _find(root, names):
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
    return fd.find_data([str(root)], user_folders={}, exclude_paths=[], max_depth=4)


def test_single_svg_folder_is_not_a_candidate_but_three_are(tmp_path):
    r = _find(tmp_path / "one", ["web/icons/logo.svg", "web/src/index.js"])
    assert [p.path for p in r.projects] == []
    r3 = _find(tmp_path / "three", ["art/a.svg", "art/b.svg", "art/c.svg"])
    assert [os.path.basename(p.path) for p in r3.projects] == ["art"]
    r2 = _find(tmp_path / "two", ["art/a.svg", "art/b.svg"])
    assert r2.projects == []


def test_png_and_gif_behaviour_unchanged(tmp_path):
    r = _find(tmp_path / "p", ["pics/a.png"])
    assert [os.path.basename(p.path) for p in r.projects] == ["pics"]
    r = _find(tmp_path / "g", ["anim/a.gif"])
    assert [os.path.basename(p.path) for p in r.projects] == ["anim"]
    r = _find(tmp_path / "mix", ["m/a.svg", "m/b.jpg"])  # 강한 형식이 하나라도 있으면 후보
    assert [os.path.basename(p.path) for p in r.projects] == ["m"]
