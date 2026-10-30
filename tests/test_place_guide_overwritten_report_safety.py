"""place 최종 재검증(stage48 S1~S14) 회귀 시험. 임시 폴더와 주입만 쓴다(홈은 환경변수로 임시 폴더를 가리킨다).
"""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from pcmigrate import cli, copier, errorlog, failure, place_original as po
from pcmigrate.fs import DestInfo, long_path
from test_place_original import BIG, Env, put, tree

NOPROT = lambda p, **k: None  # noqa: E731

WIN = pytest.mark.skipif(os.name != "nt", reason="Windows 전용(속성/ADS)")

@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", e.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    monkeypatch.setitem(po.place.__kwdefaults__, "protected_fn", NOPROT)
    return e


# --------------------------------------------------------------------------- S1 안내 파일 오인
def test_forged_first_line_user_file_is_preserved_and_guide_gets_backup(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "Z" / "z.txt", b"z")
    g = tmp_path / "g"
    put(g / po.GUIDE_NAME, "이전 안내: 내가 직접 쓴 메모\n두 번째 줄은 내 것\n".encode("utf-8"))  # 첫 줄만 우리 것과 비슷하다
    rep = e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert (g / po.GUIDE_NAME).read_text(encoding="utf-8") == "이전 안내: 내가 직접 쓴 메모\n두 번째 줄은 내 것\n"
    gf = Path(rep.gather_info["guide_file"])
    assert gf.name == "이전 안내 (2).txt" and po._is_our_guide(gf) and not po._is_our_guide(g / po.GUIDE_NAME)
    # 우리 안내 파일을 바꿀 때는 .bak 으로 보관한다(내용이 같으면 다시 쓰지 않는다)
    put(e.recv / "Z" / "y.txt", b"y")
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    bak = Path(str(gf) + ".bak")
    assert bak.is_file() and "z.txt" in bak.read_text(encoding="utf-8-sig") and "y.txt" not in bak.read_text(encoding="utf-8-sig")
    before = bak.stat().st_mtime_ns
    e.run(apply=True, gather=str(g), protected_fn=NOPROT, keep=True)  # 새로 모을 것이 없고 내용이 같다
    assert bak.stat().st_mtime_ns == before


# --------------------------------------------------------------------------- S2/S3 같은 폴더에 다시 모으기, 덮어쓴 목록
def test_regather_same_name_keeps_bak_out_of_origin_rows_and_reports_conflicts(tmp_path):
    e = Env(tmp_path, letters="D")
    g = tmp_path / "g"
    put(e.recv / "Z" / "a.txt", b"v1")
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    put(e.recv / "Z" / "a.txt", b"v2")
    rep = e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert (g / "Z" / "a.txt").read_bytes() == b"v2" and (g / "Z" / "a.txt.bak").read_bytes() == b"v1"
    text = (g / po.GUIDE_NAME).read_text(encoding="utf-8-sig")
    assert not any(x.startswith("Z\\a.txt.bak | ") for x in text.splitlines())  # .bak 은 원위치 행이 아니다
    sec = text.split(po._BAK_SECTION)[1]
    assert "Z\\a.txt.bak" in sec and "원위치 목록이 아닙니다" in sec
    d = rep.to_dict()["gather"]
    assert d["overwritten_count"] == 1 and d["overwritten"][0]["backup"].endswith("a.txt.bak")
    assert "덮어쓴 파일 1개" in "\n".join(po.summary_lines(rep))


def test_applied_overwrites_are_listed_with_separate_cap(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    now = 1_800_000_000_000_000_000
    for i in range(3):
        s = put(e.recv / "D" / "p" / f"f{i}.txt", b"new")
        os.utime(s, ns=(now - 10 ** 15,) * 2)
        d = put(e.drives / "D" / "p" / f"f{i}.txt", b"older-dest")
        os.utime(d, ns=(now,) * 2)  # 대상이 더 새롭다
    monkeypatch.setattr(po, "MAX_OVERWRITTEN", 2)
    rep = e.run(apply=True)
    d = rep.to_dict()
    assert d["overwritten_count"] == 3 and len(d["overwritten"]) == 2 and d["overwritten_truncated"] == 1
    assert d["overwritten_dest_newer_count"] == 3 and all(x["dest_newer"] and x["backup"].endswith(".bak") for x in d["overwritten"])
    text = "\n".join(po.summary_lines(rep))
    assert "덮어쓴 파일 3개" in text and "더 새로웠던 것 3개" in text
    assert len(d["placed"]) == 3  # placed 목록과 따로 센다


# --------------------------------------------------------------------------- S4/S5 리포트
def test_existing_read_only_report_is_replaced_and_old_one_kept_as_bak(cli_env, tmp_path, capsys):
    """읽기 전용 속성은 이름 바꾸기가 되므로 막지 않는다: 새 리포트를 저장하고 이전 리포트는 .bak 으로 보관한다."""
    e = cli_env
    put(e.recv / "D" / "p" / "a.txt", b"a")
    rp = put(tmp_path / "r.json", b"{}")
    os.chmod(rp, stat.S_IREAD)
    try:
        assert cli.main(["place", str(e.recv), "--yes", "--report", str(rp)]) == 0
        assert json.loads(rp.read_text(encoding="utf-8"))["placed_files"] == 1
        assert (tmp_path / "r.json.bak").read_bytes() == b"{}"
    finally:
        for f in tmp_path.glob("r.json*"):
            os.chmod(f, stat.S_IWRITE | stat.S_IREAD)


def test_report_rerun_keeps_previous_as_numbered_bak_even_on_long_paths(cli_env, tmp_path):
    e = cli_env
    put(e.recv / "D" / "p" / "a.txt", b"a")
    rp = tmp_path / "out" / "r.json"
    rp.parent.mkdir()
    assert cli.main(["place", str(e.recv), "--report", str(rp)]) == 0
    first = rp.read_text(encoding="utf-8")
    assert cli.main(["place", str(e.recv), "--report", str(rp)]) == 0
    assert (tmp_path / "out" / "r.json.bak").read_text(encoding="utf-8") == first and json.loads(rp.read_text(encoding="utf-8"))
    assert cli.main(["place", str(e.recv), "--report", str(rp)]) == 0
    assert (tmp_path / "out" / "r.json.bak.1").is_file()
    deep = tmp_path.joinpath(*(["d" * 60] * 5))  # 260자 넘는 리포트 경로
    os.makedirs(long_path(deep), exist_ok=True)
    rep = e.run()
    long_rp = deep / "r.json"
    assert len(str(long_rp)) > 260
    rep.write(long_rp)
    rep.write(long_rp)
    assert os.path.isfile(long_path(str(long_rp) + ".bak"))


def test_report_inside_receive_is_rejected_before_probe_and_preview_does_not_create_dirs(cli_env, tmp_path, capsys):
    e = cli_env
    put(e.recv / "D" / "p" / "a.txt", b"a")
    assert cli.main(["place", str(e.recv), "--yes", "--report", str(e.recv / "newdir" / "r.json")]) != 0
    assert not (e.recv / "newdir").exists()  # 프로브가 받기 폴더 안에 폴더를 만들지 않았다
    capsys.readouterr()
    nodir = tmp_path / "nodir" / "r.json"
    assert cli.main(["place", str(e.recv), "--report", str(nodir)]) == 0  # 미리보기: 안내만, 종료 코드 영향 없음
    assert "적용할 때 만듭니다" in capsys.readouterr().err and not nodir.parent.exists()


# --------------------------------------------------------------------------- S6/S7 ADS
@WIN
def test_ads_lookup_failure_is_unknown_not_none(tmp_path):
    f = put(tmp_path / "plain.txt", b"x")
    assert po._alt_streams(f) == []
    assert po._alt_streams(tmp_path / "no_such_file.txt") is None  # 조회가 안 되면 '없음'이 아니라 알 수 없음


def test_unknown_ads_keeps_source(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    f = put(e.recv / "D" / "p" / "a.txt", b"a")
    monkeypatch.setattr(po, "list_streams", lambda p: None)
    rep = e.run(apply=True, same_volume_fn=lambda a, b: False)
    assert [r.code for r in rep.leftover] == [po.SRC_ADS_UNKNOWN] and f.exists() and (e.drives / "D" / "p" / "a.txt").exists()
    assert "확인하지 못해" in rep.leftover[0].reason


@WIN
def test_skipped_identical_copy_gets_attributes_repaired(tmp_path):
    import ctypes
    e = Env(tmp_path, letters="D")
    s = put(e.recv / "D" / "p" / "h.txt", b"same")
    d = put(e.drives / "D" / "p" / "h.txt", b"same")
    ctypes.windll.kernel32.SetFileAttributesW(long_path(s), stat.FILE_ATTRIBUTE_HIDDEN)
    shutil_copystat = os.stat(s)
    os.utime(d, ns=(shutil_copystat.st_atime_ns, shutil_copystat.st_mtime_ns))
    e.run(apply=True, same_volume_fn=lambda a, b: False)
    assert os.stat(d).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN  # 앞 실행이 속성 복사 전에 멈춘 경우를 보정


@WIN
def test_preview_does_not_plan_deleting_ads_sources(tmp_path):
    e = Env(tmp_path, letters="D")
    f = put(e.recv / "D" / "p" / "with_ads.txt", b"main")
    with open(str(f) + ":meta", "w") as h:
        h.write("stream")
    put(e.recv / "D" / "p" / "plain.txt", b"plain")
    rep = e.run(same_volume_fn=lambda a, b: False)
    d = rep.to_dict()
    assert d["delete_planned_files"] == 1 and [x["reason_code"] for x in d["leftover"]] == [po.SRC_ADS]  # ADS 파일은 삭제 예정에서 제외
    text = "\n".join(po.summary_lines(rep))
    assert "받기 폴더에 남을 것(예상)" in text


# --------------------------------------------------------------------------- S8 분할 조각 안내
def test_split_parts_reason_has_join_example_and_counts(tmp_path):
    e = Env(tmp_path, letters="D")
    for n in ("001", "002", "003"):
        put(e.recv / "D" / "p" / f"big.bin.pcm{n}", b"x")
    put(e.recv / "D" / "p" / "ok.txt", b"ok")
    rep = e.run(apply=True)
    d = rep.to_dict()
    row = next(x for x in d["skipped"] if x["reason_code"] == po.SPLIT_PARTS)
    assert row["files"] == 3 and "조각 3개" in row["reason"] and "pcmigrate join" in row["reason"] and "--output" in row["reason"]
    text = "\n".join(po.summary_lines(rep))
    assert "조각 3개 |" in text and "같은 폴더의 일반 파일은 이미 놓였고 조각 파일만" in text


# --------------------------------------------------------------------------- S9 드라이브 루트 모양의 받기 폴더
def test_system_items_at_receive_root_are_ignored(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "ok.txt", b"ok")
    put(e.recv / "$RECYCLE.BIN" / "S-1-5" / "junk", b"j")
    put(e.recv / "System Volume Information" / "tracking.log", b"t")
    put(e.recv / "desktop.ini", b"[.ShellClassInfo]")
    put(e.recv / "Thumbs.db", b"t")
    rep = e.run(apply=True)
    d = rep.to_dict()
    assert d["skipped"] == [] and d["success"] is True and d["needs_manual"] is False
    assert sorted(d["remaining_in_receive"]["system_items_ignored"], key=str.lower) == ["$RECYCLE.BIN", "desktop.ini", "System Volume Information", "Thumbs.db"]
    assert d["remaining_in_receive"]["files"] == 0 and "시스템 항목" in "\n".join(po.summary_lines(rep))
    assert (e.recv / "$RECYCLE.BIN" / "S-1-5" / "junk").exists()  # 건드리지 않는다


# --------------------------------------------------------------------------- S10/S11
def test_gather_to_home_folder_itself_is_rejected(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(po.TransferError) as ei:
        po.resolve_gather("~", tmp_path / "recv", protected_fn=NOPROT)
    assert "홈 폴더 자체" in str(ei.value)
    g, _ = po.resolve_gather("~/sub", tmp_path / "recv", protected_fn=NOPROT)  # 하위 폴더는 허용
    assert g == Path(os.path.abspath(home / "sub"))


def test_powershell_profile_rule_is_limited_to_user_documents():
    prot = lambda p: po.protected_reason(p, is_dir=False)  # noqa: E731
    assert prot("C:\\Users\\x\\Documents\\WindowsPowerShell\\profile.ps1")
    assert prot("C:\\Users\\x\\OneDrive - Co\\Documents\\PowerShell\\Microsoft.PowerShell_profile.ps1")
    assert prot("D:\\bk\\WindowsPowerShell\\profile.ps1") is None  # 백업 복사본
    assert prot("D:\\proj\\profile.ps1") is None and prot("C:\\Users\\x\\Config\\a.txt") is None
    assert prot("C:\\Users\\x\\Documents\\WindowsPowerShell\\sub\\profile.ps1") is None


# --------------------------------------------------------------------------- S12 잠긴 읽기 전용 원본
@WIN
def test_locked_read_only_source_restores_attributes_and_names_the_cause(tmp_path):
    """실제로 열려 있는 파일(공유 삭제 없음)로 재현한다: 속성은 풀 수 있지만 삭제가 막혀 원래 속성을 되돌리고 '사용 중'으로 보고."""
    e = Env(tmp_path, letters="D")
    f = put(e.recv / "D" / "p" / "ro.txt", b"x")
    os.chmod(f, stat.S_IREAD)
    h = open(f, "rb")  # 다른 프로그램이 열어 둔 것과 같다
    try:
        rep = e.run(apply=True, same_volume_fn=lambda a, b: False)
        assert [r.code for r in rep.leftover] == [po.LEFTOVER] and "사용 중" in rep.leftover[0].reason  # 읽기 전용 해제 실패가 아니다
        assert os.stat(f).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY  # 원래 속성으로 되돌렸다
        assert (e.drives / "D" / "p" / "ro.txt").read_bytes() == b"x"  # 대상에는 온전히 있다
    finally:
        h.close()
        os.chmod(f, stat.S_IWRITE | stat.S_IREAD)



# --------------------------------------------------------------------------- S13/S14 문구
def test_failure_summary_names_error_log_and_hides_temp_names(tmp_path, monkeypatch):
    assert "pcmigrate-part" not in po.Row("file", "a", "b", failure.OTHER, "x.pcmigrate-part").reason
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "p" / "a.txt", b"a")

    def inst(tmp, dst, res, lp=os.fspath):
        raise PermissionError(5, "denied", str(tmp))
    monkeypatch.setattr(copier, "_install", inst)
    rep = e.run(apply=True)
    text = "\n".join(po.summary_lines(rep))
    assert "오류 로그:" in text and rep.to_dict()["error_log_dir"]


def test_file_given_as_receive_folder_is_named_a_file(tmp_path):
    f = put(tmp_path / "afile.txt", b"x")
    with pytest.raises(po.TransferError) as ei:
        po.place(f)
    assert "파일입니다" in str(ei.value)


def test_corrupt_journal_warning_is_not_followed_by_contradiction(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "p" / "a.txt", b"a")
    (e.recv / ".pcmigrate-multi.json").write_text("{bad", encoding="utf-8")
    warns = []
    e.run(warn=warns.append)
    assert any("읽을 수 없거나" in w for w in warns) and not any("저널이 없어" in w for w in warns)


def test_summary_wording_cleanups(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "p" / "a.txt", b"a")
    clean = po.summary_lines(e.run(apply=True))
    assert not any("수동으로 옮길 항목" in x for x in clean)  # 할 일이 없으면 이 줄은 없다
    pre = "\n".join(po.summary_lines(e.run()))
    assert "바뀌는 것이 없습니다" in pre  # 놓을 것이 없는 미리보기에는 --yes 안내를 내지 않는다
    assert "다음 할 일: 이상 없으면" not in pre
    put(e.recv / "D" / "q" / "b.txt", b"b")
    put(e.drives / "D" / "q" / "b.txt", b"old")
    text = "\n".join(po.summary_lines(e.run()))
    assert "-  덮어쓸" not in text and "- 덮어쓸 파일 1개" in text


def test_progress_phase_says_move_for_same_volume(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "p" / "a.txt", b"a")
    infos = []
    e.run(apply=True, progress_info=infos.append)
    assert infos and infos[-1].phase == "move"
    put(e.recv / "D" / "p" / "b.txt", b"b")
    infos2 = []
    e.run(apply=True, progress_info=infos2.append, same_volume_fn=lambda a, b: False)
    assert infos2[-1].phase == "copy"


def test_no_space_reason_mentions_drive_total_and_gather():
    text = po.reason_text(po.NO_SPACE)
    assert "합계" in text and "--gather=폴더" in text


def test_gather_value_swallowing_the_receive_folder_gets_a_hint(tmp_path, capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["place", "--gather", str(tmp_path)])
    assert ei.value.code == 2 and "--gather=폴더" in capsys.readouterr().err
