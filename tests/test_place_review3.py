"""place 재검토(stage48 R1~R14) 회귀 시험: 긴 경로, 읽기 전용 원본, 중단 머리말, 분할 조각, 안내 파일 이름 충돌, 속성/ADS,
미리보기 덮어쓰기 목록, 리포트 저장, 보호 위치, 사유 문구, CLI 사전 점검, 성능(realpath 호출 수).

임시 폴더와 주입만 쓴다(홈/바탕화면/실제 드라이브 금지).
"""
import ctypes
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from pcmigrate import cli, copier, errorlog, failure, place_original as po
from pcmigrate.cancel import Cancelled
from pcmigrate.fs import long_path
from test_place_original import BIG, Env, contents, put, tree

NOPROT = lambda p, **k: None  # noqa: E731


def lput(path: Path, data: bytes = b"x") -> Path:
    os.makedirs(long_path(path.parent), exist_ok=True)
    with open(long_path(path), "wb") as h:
        h.write(data)
    return path


def lexists_file(path: Path) -> bool:
    return os.path.isfile(long_path(path))


def set_attrs(path: Path, flags: int) -> None:
    assert ctypes.windll.kernel32.SetFileAttributesW(long_path(path), flags)


def get_attrs(path: Path) -> int:
    return os.stat(long_path(path)).st_file_attributes


DEEP = "/".join(["d" * 60] * 5)  # 5 * 61 = 305 글자


# --------------------------------------------------------------------------- R1 긴 경로
def test_long_path_files_under_missing_drive_are_reported(tmp_path):
    e = Env(tmp_path, letters="D")
    lput(e.recv / "X" / DEEP / "f.txt", b"data")  # X 드라이브는 이 PC 에 없다
    lput(e.recv / "D" / "p" / DEEP / "g.txt", b"data2")
    assert po._count_tree(e.recv / "X")[0] == 1  # 접두 없는 조회였다면 0 이 되던 경우
    pre = e.run()
    row = next(r for r in pre.skipped if r.code == po.DRIVE_MISSING)
    assert row.files == 1 and not pre.to_dict()["success"]
    rep = e.run(apply=True)
    d = rep.to_dict()
    assert (e.drives / "D" / "p").is_dir() and lexists_file(e.drives / "D" / "p" / DEEP / "g.txt")
    assert d["success"] is False and d["needs_manual"] is True and d["remaining_in_receive"]["files"] == 1
    assert [r["reason_code"] for r in d["skipped"]] == [po.DRIVE_MISSING]


def test_unit_folder_itself_over_260_chars_is_placed(tmp_path):
    e = Env(tmp_path, letters="D")
    e.recv = tmp_path / "deep2" / ("r" * 80)
    a, b = "a" * 70, "b" * 70
    lput(e.recv / "D" / a / b / "q" / "a.txt", b"data")
    os.makedirs(long_path(e.recv), exist_ok=True)
    with open(long_path(e.recv / ".pcmigrate-multi.json"), "w", encoding="utf-8") as h:
        h.write(json.dumps({"version": 1, "items": {"k": {"src": f"D:\\{a}\\{b}", "rel": f"D\\{a}\\{b}", "status": "done"}}}))
    assert len(str(e.recv / "D" / a / b)) > 260
    for apply in (False, True):
        rep = e.run(apply=apply)
        assert rep.meta.get("nothing_to_do") is None and rep.total_files == 1
    assert lexists_file(e.drives / "D" / a / b / "q" / "a.txt")
    assert not lexists_file(e.recv / "D" / a / b / "q" / "a.txt")


def test_gather_collects_over_260_char_folder(tmp_path):
    e = Env(tmp_path, letters="D")
    lput(e.recv / "Z" / DEEP / "z.txt", b"z")
    g = tmp_path / "g"
    rep = e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert len(rep.gathered) == 1 and rep.gathered[0]["files"] == 1
    assert lexists_file(g / "Z" / DEEP / "z.txt") and not lexists_file(e.recv / "Z" / DEEP / "z.txt")


def test_unreadable_folder_is_reported_not_swallowed(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "a.txt", b"a")
    real = os.walk

    def walk(top, topdown=True, onerror=None, followlinks=False):
        if onerror is not None and "recv" in str(top):
            onerror(PermissionError(5, "denied", str(top) + "\\locked"))
        return real(top, topdown, onerror, followlinks)
    monkeypatch.setattr(os, "walk", walk)
    assert po._has_files(e.recv / "D" / "none") is True  # 읽지 못하면 '없다'고 단정하지 않는다
    rep = e.run(apply=True)
    d = rep.to_dict()
    assert d["remaining_in_receive"]["unreadable"] >= 1 and d["needs_manual"] is True and d["success"] is False
    assert errorlog.last_error() is not None


# --------------------------------------------------------------------------- R2 읽기 전용 원본
def test_read_only_source_is_deleted_after_verified_copy(tmp_path):
    e = Env(tmp_path, letters="D")
    f = put(e.recv / "D" / "p" / "ro.txt", b"readonly-data")
    os.chmod(f, stat.S_IREAD)
    rep = e.run(apply=True, same_volume_fn=lambda a, b: False)
    d = rep.to_dict()
    assert (e.drives / "D" / "p" / "ro.txt").read_bytes() == b"readonly-data" and not f.exists()
    assert d["removed_sources"] == 1 and d["leftover"] == []
    assert get_attrs(e.drives / "D" / "p" / "ro.txt") & stat.FILE_ATTRIBUTE_READONLY  # 속성은 대상에 옮겨진다(R6)


def test_read_only_source_attribute_clear_failure_names_read_only(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    f = put(e.recv / "D" / "p" / "ro.txt", b"x")
    os.chmod(f, stat.S_IREAD)

    def deny(*a, **k):
        raise PermissionError(5, "denied")
    monkeypatch.setattr(os, "chmod", deny)
    rep = e.run(apply=True, same_volume_fn=lambda a, b: False)
    try:
        assert [r.code for r in rep.leftover] == [po.SRC_READONLY] and "읽기 전용" in rep.leftover[0].reason
        assert f.exists() and (e.drives / "D" / "p" / "ro.txt").exists()
    finally:
        monkeypatch.undo()
        os.chmod(f, stat.S_IWRITE | stat.S_IREAD)


# --------------------------------------------------------------------------- R3 중단 머리말
def test_interrupted_summary_header_is_not_completed(tmp_path):
    e = Env(tmp_path, letters="D")
    for i in range(4):
        put(e.recv / "D" / "p" / f"f{i}.txt", b"x")
    n = {"i": 0}

    def cancel():
        n["i"] += 1
        return n["i"] > 8
    with pytest.raises(Cancelled) as ei:
        e.run(apply=True, cancel=cancel, same_volume_fn=lambda a, b: False)
    text = po.summary_lines(ei.value.report)
    assert text[0].startswith("중단됨(일부만 처리했습니다") and "사용자 취소" in text[0] and "처리한 파일" in text[1]
    assert not any("배치를 마쳤습니다" in x for x in text)
    ok = po.summary_lines(e.run(apply=True))
    assert ok[0].startswith("배치를 마쳤습니다")
    pre = po.summary_lines(e.run())
    assert pre[0].startswith("미리보기입니다")


# --------------------------------------------------------------------------- R4 분할 조각/기록 파일
def test_split_parts_are_not_placed_and_records_are_not_empty_folders(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "p" / "big.bin.pcm001", b"1")
    put(e.recv / "D" / "p" / "big.bin.pcm002", b"2")
    put(e.recv / "D" / "p" / "ok.txt", b"ok")
    put(e.recv / "D" / "q" / ".pcmigrate-state.json", b"{}")  # 기록 파일만 있는 폴더
    rep = e.run(apply=True)
    d = rep.to_dict()
    assert (e.drives / "D" / "p" / "ok.txt").exists() and not (e.drives / "D" / "p" / "big.bin.pcm001").exists()
    assert (e.recv / "D" / "p" / "big.bin.pcm001").exists() and (e.recv / "D" / "p" / "big.bin.pcm002").exists()
    row = next(r for r in d["skipped"] if r["reason_code"] == po.SPLIT_PARTS)
    assert row["expected"].endswith("big.bin") and "조각 2개" in row["reason"] and "join" in row["reason"]
    rm = d["remaining_in_receive"]
    assert rm["records"] >= 1 and any(x.endswith(".pcmigrate-state.json") for x in rm["record_examples"])
    assert not any(x.endswith("\\q") for x in rm["empty_folder_examples"])  # 기록 파일만 있는 폴더는 빈 폴더가 아니다
    assert d["needs_manual"] is True


# --------------------------------------------------------------------------- R5 안내 파일 이름 충돌
def test_guide_does_not_overwrite_same_named_user_file(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "Z" / "z.txt", b"z")
    g = tmp_path / "g"
    put(g / po.GUIDE_NAME, b"MY OWN NOTES")
    rep = e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert (g / po.GUIDE_NAME).read_bytes() == b"MY OWN NOTES"  # 사용자 파일은 그대로
    gf = Path(rep.gather_info["guide_file"])
    assert gf.name == "이전 안내 (2).txt" and "z.txt" in gf.read_text(encoding="utf-8-sig")
    rep2 = e.run(apply=True, gather=str(g), protected_fn=NOPROT, keep=True)  # 다시 실행: 같은 안내 파일을 이어서 쓴다
    assert not (g / "이전 안내 (3).txt").exists() and (g / "이전 안내 (2).txt").is_file()


def test_gathered_file_named_like_guide_keeps_guide_separate(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / po.GUIDE_NAME, b"a gathered file named like the guide")
    put(e.recv / "Z" / "z.txt", b"z")
    g = tmp_path / "g"
    rep = e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert (g / po.GUIDE_NAME).read_bytes() == b"a gathered file named like the guide"
    assert Path(rep.gather_info["guide_file"]).name != po.GUIDE_NAME


# --------------------------------------------------------------------------- R6 속성/ADS/폴더 메타
def test_attributes_and_folder_meta_are_copied_across_volumes(tmp_path):
    e = Env(tmp_path, letters="D")
    f = put(e.recv / "D" / "p" / "h.txt", b"hidden-data")
    set_attrs(f, stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM)
    d = f.parent
    old = 1_500_000_000_000_000_000
    os.utime(d, ns=(old, old))
    set_attrs(d, stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_DIRECTORY)
    rep = e.run(apply=True, same_volume_fn=lambda a, b: False)
    dst = e.drives / "D" / "p" / "h.txt"
    a = get_attrs(dst)
    assert a & stat.FILE_ATTRIBUTE_HIDDEN and a & stat.FILE_ATTRIBUTE_SYSTEM
    dd = e.drives / "D" / "p"
    assert get_attrs(dd) & stat.FILE_ATTRIBUTE_HIDDEN and abs(os.stat(dd).st_mtime_ns - old) < 2_000_000_000  # 폴더 속성/시각
    assert rep.to_dict()["warnings"] == []


def test_ads_source_is_not_deleted_and_reported(tmp_path):
    e = Env(tmp_path, letters="D")
    f = put(e.recv / "D" / "p" / "with_ads.txt", b"main")
    with open(str(f) + ":meta", "w") as h:
        h.write("extra stream")
    z = put(e.recv / "D" / "p" / "downloaded.txt", b"dl")
    with open(str(z) + ":Zone.Identifier", "w") as h:
        h.write("[ZoneTransfer]\nZoneId=3\n")
    assert po._alt_streams(f) == [":meta:$DATA"] and po._alt_streams(z) == []  # Zone.Identifier 만 있는 것은 ADS 로 보지 않는다
    rep = e.run(apply=True, same_volume_fn=lambda a, b: False)
    d = rep.to_dict()
    assert (e.drives / "D" / "p" / "with_ads.txt").read_bytes() == b"main" and f.exists()  # 원본은 남는다
    assert [x["reason_code"] for x in d["leftover"]] == [po.SRC_ADS] and "ADS" in d["leftover"][0]["reason"]
    assert not z.exists() and d["removed_sources"] == 1 and d["needs_manual"] is True


# --------------------------------------------------------------------------- R7 미리보기 덮어쓰기 목록
def test_preview_lists_overwrites_with_sizes_times_and_newer_dest(tmp_path):
    e = Env(tmp_path, letters="D")
    now = 1_800_000_000_000_000_000
    a = put(e.recv / "D" / "p" / "a.txt", b"OLDER-FROM-OLD-PC")
    os.utime(a, ns=(now - 10 ** 15,) * 2)
    b = put(e.drives / "D" / "p" / "a.txt", b"NEWER-WORK-ON-NEW-PC")
    os.utime(b, ns=(now,) * 2)
    c = put(e.recv / "D" / "p" / "c.txt", b"new-in-recv")
    put(e.drives / "D" / "p" / "c.txt", b"old-in-dest")
    os.utime(c, ns=(now,) * 2)
    os.utime(e.drives / "D" / "p" / "c.txt", ns=(now - 10 ** 15,) * 2)
    rep = e.run()
    d = rep.to_dict()
    ov = {x["expected"]: x for x in d["overwrites"]}
    x = ov["D:\\p\\a.txt"]
    assert x["recv_size"] == 17 and x["dest_size"] == 20 and x["dest_newer"] is True and x["backup"].endswith("a.txt.bak")
    assert x["recv_mtime"].endswith("Z") and x["dest_mtime"] > x["recv_mtime"]
    assert ov["D:\\p\\c.txt"]["dest_newer"] is False and d["dest_newer_count"] == 1
    text = "\n".join(po.summary_lines(rep))
    assert "덮어쓸 파일 2개" in text and "[대상이 더 새로움]" in text and "--no-overwrite" in text
    assert (e.drives / "D" / "p" / "a.txt").read_bytes() == b"NEWER-WORK-ON-NEW-PC"  # 미리보기는 아무것도 바꾸지 않는다
    real = e.run(apply=True)
    assert (e.drives / "D" / "p" / "a.txt.bak").read_bytes() == b"NEWER-WORK-ON-NEW-PC" and real.to_dict()["overwrites"] == []


# --------------------------------------------------------------------------- R8 리포트 저장
def test_report_write_is_atomic_and_handles_lone_surrogates(tmp_path):
    e = Env(tmp_path, letters="D")
    name = "bad\ud800name.txt"
    try:
        put(e.recv / "D" / "p" / name, b"x")
    except (OSError, UnicodeError):
        pytest.skip("짝 없는 서로게이트 이름을 만들 수 없는 환경")
    rep = e.run(apply=True)
    out = tmp_path / "rep.json"
    rep.write(out)
    data = json.loads(out.read_text(encoding="utf-8"))  # 유효한 JSON(이스케이프됨)
    assert data["placed_files"] == 1 and not list(tmp_path.glob("rep.json.part"))
    out.write_text("PREVIOUS", encoding="utf-8")

    real = os.replace

    def boom(a, b, *x, **k):
        raise PermissionError(5, "denied")
    try:
        os.replace = boom
        with pytest.raises(PermissionError):
            rep.write(out)
    finally:
        os.replace = real
    assert out.read_text(encoding="utf-8") == "PREVIOUS" and not list(tmp_path.glob("rep.json.part"))  # 0바이트/반쪽 파일 없음


def test_cli_prints_summary_and_saves_report_with_surrogate_name(tmp_path, monkeypatch, capsys):
    e = Env(tmp_path, letters="D")
    try:
        put(e.recv / "D" / "p" / "bad\ud800name.txt", b"x")
    except (OSError, UnicodeError):
        pytest.skip("짝 없는 서로게이트 이름을 만들 수 없는 환경")
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", e.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(e.recv), "--yes", "--report", str(rp)]) == 0
    assert json.loads(rp.read_text(encoding="utf-8"))["placed_files"] == 1
    assert "배치를 마쳤습니다" in capsys.readouterr().out


# --------------------------------------------------------------------------- R9 보호 위치
@pytest.mark.parametrize("path,is_dir", [
    ("C:\\Config.Msi", True), ("c:\\CONFIG.MSI\\x", True), ("D:\\$Windows.~BT\\Sources", True), ("E:\\$SysReset", True), ("F:\\MSOCache\\All Users", True),
    ("C:\\Users\\u\\Documents\\WindowsPowerShell\\Microsoft.PowerShell_profile.ps1", False),
    ("C:\\Users\\u\\OneDrive\\Documents\\PowerShell\\profile.ps1", False),
    ("C:\\Users\\u\\Documents\\POWERSHELL\\Microsoft.VSCode_profile.ps1", False),
])
def test_more_protected_locations(path, is_dir):
    assert po.protected_reason(path, is_dir=is_dir)


def test_powershell_profile_rules_are_narrow_and_variants_are_caught(monkeypatch):
    assert po.protected_reason("C:\\Users\\u\\Documents\\WindowsPowerShell\\notes.txt", is_dir=False) is None
    assert po.protected_reason("C:\\Users\\u\\Documents\\WindowsPowerShell", is_dir=True) is None
    assert po.protected_reason("D:\\Work\\profile.ps1", is_dir=False) is None  # PowerShell 프로필 폴더가 아니면 일반 파일
    monkeypatch.setattr(po, "expand_short_names", lambda p: p.replace("WINDOW~1", "Windows").replace("CONFIG~1.MSI", "Config.Msi"))
    assert po.protected_reason("C:\\WINDOW~1\\x") and po.protected_reason("C:\\CONFIG~1.MSI\\x")
    assert po.protected_reason("C:\\users\\U\\.gitconfig", is_dir=False) is None  # .gitconfig 는 기존 설계대로 허용(보고서에 판단 기록)


# --------------------------------------------------------------------------- R10 사유 문구
def test_place_reason_texts_do_not_point_to_gui_buttons():
    for code in (failure.IN_USE, failure.MISMATCH, failure.SIZE_MISMATCH, failure.MISSING_DEST, failure.DEST_IS_DIR, failure.OTHER,
                 failure.PATH_TOO_LONG, failure.PERMISSION, failure.DISK_FULL, failure.DISCONNECTED):
        text = po.reason_text(code)
        assert "실패한 것만 다시 시도" not in text, code
    assert "같은 명령" in po.reason_text(failure.IN_USE) and "읽는 중" not in po.reason_text(failure.OTHER)
    assert "직접 지워도" not in po.reason_text(po.SRC_CHANGED) and "비교" in po.reason_text(po.SRC_CHANGED)
    assert len({po.reason_text(c) for c in (po.LEFTOVER, po.SRC_CHANGED, po.SRC_READONLY, po.SRC_ADS)}) == 4


def test_summary_tells_how_to_resume_when_there_are_failures(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "p" / "a.txt", b"a")
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        raise PermissionError(5, "denied", str(tmp))
    monkeypatch.setattr(copier, "_install", inst)
    rep = e.run(apply=True)
    text = "\n".join(po.summary_lines(rep))
    assert "같은 명령을 다시 실행하면 실패한 파일부터 이어서 처리합니다" in text


# --------------------------------------------------------------------------- R11
def test_cli_report_unwritable_is_rejected_before_anything_moves(tmp_path, monkeypatch, capsys):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "p" / "a.txt", b"a")
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", e.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    blocker = put(tmp_path / "afile", b"x")  # 상위 경로가 파일이라 리포트를 쓸 수 없다
    before = tree(e.recv)
    assert cli.main(["place", str(e.recv), "--yes", "--report", str(blocker / "sub" / "r.json")]) == 1
    assert "리포트를 저장할 수 없습니다" in capsys.readouterr().err
    assert tree(e.recv) == before and not [p for p in e.drives.rglob("*") if p.is_file()]


def test_cli_trailing_quote_on_receive_or_report_is_explained(tmp_path, capsys):
    for argv in (["place", str(tmp_path / "recv") + '"'], ["place", str(tmp_path), "--report", str(tmp_path / "r") + '"']):
        with pytest.raises(SystemExit) as ei:
            cli.main(argv)
        assert ei.value.code == 2
        assert "따옴표" in capsys.readouterr().err


def test_empty_skeleton_is_not_a_map_conflict_or_missing_drive(tmp_path):
    e = Env(tmp_path, letters="DE")
    put(e.recv / "D" / "a.txt", b"a")
    (e.recv / "E" / "sub" / "deeper").mkdir(parents=True)  # 이미 비어 있는 뼈대
    rep = e.run(apply=True, maps={"D": "E"})
    assert [r.code for r in rep.skipped] == [] and (e.drives / "E" / "a.txt").exists()
    (e.recv / "Q" / "x").mkdir(parents=True)  # 없는 드라이브의 빈 뼈대도 오보하지 않는다
    assert e.run(apply=True).skipped == []


def test_file_level_protected_row_is_plain_path_and_gatherable(tmp_path):
    e = Env(tmp_path, letters="D")
    f = put(e.recv / "D" / "Users" / "u" / "Documents" / "WindowsPowerShell" / "profile.ps1", b"x")
    put(e.recv / "D" / "Users" / "u" / "Documents" / "WindowsPowerShell" / "notes.txt", b"n")
    g = tmp_path / "g"
    rep = e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert not any(r.current.startswith("\\\\?\\") for r in rep.skipped)
    assert (g / "D" / "Users" / "u" / "Documents" / "WindowsPowerShell" / "profile.ps1").exists() and not f.exists()
    assert (e.drives / "D" / "Users" / "u" / "Documents" / "WindowsPowerShell" / "notes.txt").exists()


def test_stale_part_files_make_manual_work_visible(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "p" / "a.txt", b"a")
    put(e.recv / "D" / "p" / "big.bin.pcmigrate-part", b"partial")
    d = e.run(apply=True).to_dict()
    assert d["success"] is False and d["needs_manual"] is True and d["remaining_in_receive"]["files"] == 1


def test_manual_totals_match_between_summary_and_cli_message(tmp_path, monkeypatch, capsys):
    e = Env(tmp_path, letters="D")
    put(e.recv / "Z" / "z.txt", b"z")
    put(e.recv / "D" / "ok.txt", b"ok")
    put(e.recv / "loose.txt", b"l")
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", e.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    monkeypatch.setitem(po.place.__kwdefaults__, "protected_fn", NOPROT)
    g = tmp_path / "g"
    assert cli.main(["place", str(e.recv), f"--gather={g}", "--yes"]) == 0
    cap = capsys.readouterr()
    assert "수동으로 옮길 항목 2건" in cap.out and "직접 처리할 항목이 2건" in cap.err


# --------------------------------------------------------------------------- R14 성능(realpath 호출 수)
def test_realpath_calls_scale_with_folders_not_files(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    for d in range(4):
        for i in range(50):
            put(e.recv / "D" / "p" / f"s{d}" / f"f{i}.txt", b"x")
    calls = {"n": 0}
    real = os.path.realpath

    def rp(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(os.path, "realpath", rp)
    rep = e.run(apply=True)
    assert rep.to_dict()["summary"] == {"moved": 200}
    assert calls["n"] < 100, calls["n"]  # 파일 200개에 realpath 가 폴더 단위로만(예전에는 파일당 여러 번)


# --------------------------------------------------------------------------- R15 파일 단위 건너뜀도 모은다
def test_file_level_protected_rows_are_gathered_even_with_keep(tmp_path):
    e = Env(tmp_path, letters="C")
    put(e.recv / "C" / "x.txt", b"root-file")  # 시스템 드라이브 루트 파일
    put(e.recv / "C" / "Users" / "u" / ".ssh" / "config", b"Host x")  # 홈 보호 파일
    put(e.recv / "C" / "Users" / "u" / "Documents" / "ok.txt", b"ok")
    g = tmp_path / "gat"
    rep = e.run(apply=True, keep=True, gather=str(g), protected_fn=NOPROT)
    assert rep.gather_info["candidates"] == 2 and len(rep.gathered) == 2
    assert (g / "C" / "x.txt").read_bytes() == b"root-file" and (g / "C" / "Users" / "u" / ".ssh" / "config").exists()
    assert (e.recv / "C" / "x.txt").exists()  # --keep: 받기 폴더는 그대로
    assert (e.drives / "C" / "Users" / "u" / "Documents" / "ok.txt").exists()


def test_inside_receive_rows_are_gathered(tmp_path):
    e = Env(tmp_path, letters="D")
    e.recv = e.drives / "D" / "recv"
    e.recv.mkdir()
    put(e.recv / "D" / "recv" / "x.txt", b"x")
    g = tmp_path / "gat"
    rep = e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert [x["reason_code"] for x in rep.to_dict()["gather"]["gathered"]] == [po.INSIDE_RECEIVE] and (g / "D" / "recv" / "x.txt").exists()


# --------------------------------------------------------------------------- R16 사유에 detail 이 나온다
def test_row_reason_keeps_detail():
    mk = lambda code, detail: po.Row("file", "D:/a", "r/a", code, detail)  # noqa: E731
    assert "RuntimeError" in mk(failure.OTHER, "RuntimeError").reason
    assert "target/a.txt" in mk(failure.DEST_IS_DIR, "target/a.txt").reason
    assert "접근 권한" in mk(po.LEFTOVER, "접근 권한이 없습니다").reason
    assert "상세사유" in mk(failure.PERMISSION, "상세사유").reason
    assert "RuntimeError" in mk(failure.OTHER, "RuntimeError").to_dict()["reason"]


# --------------------------------------------------------------------------- R17/R18 잔여 정리
def test_find_data_selection_backup_numbering_and_weak_merge(tmp_path):
    from pcmigrate import find_data as fd
    res = fd.FindResult()
    out = tmp_path / "sel.json"
    for _ in range(3):
        fd.write_selection(res, out)
    assert (tmp_path / "sel.json.bak").exists() and (tmp_path / "sel.json.bak.1").exists()
    a, b = fd._Agg(), fd._Agg()
    b.add_file(10, 0.0, "photo", ".svg", weak=True)
    b.add_file(10, 0.0, "design", ".svg", weak=True)
    a.merge(b)
    assert a.weak == {"photo": 1, "design": 1}  # 합산된다(현재 후보 판정은 direct 집계만 보지만 값은 보존)


def test_set_pkg_fails_loudly_for_unknown_names():
    from conftest import set_pkg
    with pytest.raises(AssertionError):
        set_pkg("no_such_name_for_set_pkg_xyz", 1)


# --------------------------------------------------------------------------- R19 모을 항목이 없으면 쓰기 시험 파일을 만들지 않는다
def test_write_probe_only_when_something_to_gather(tmp_path, monkeypatch):
    calls = []
    real = po._probe_writable

    def probe(folder):
        calls.append(str(folder))
        return real(folder)
    monkeypatch.setattr(po, "_probe_writable", probe)
    e = Env(tmp_path, letters="D")
    put(e.recv / "D" / "ok.txt", b"ok")
    g = tmp_path / "gat"
    g.mkdir()
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert calls == [] and not list(g.iterdir())  # 모을 항목이 없다: 시험 파일도 만들지 않는다
    put(e.recv / "Z" / "z.txt", b"z")  # 이제 모을 항목이 있다
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert len(calls) == 1 and (g / "Z" / "z.txt").exists()
    assert not list(g.glob(".pcmigrate-write-test-*"))
    # 옮기는 중에야 드러나는 모을 항목(파일 단위 보호)도 모으기 직전에 한 번 점검한다
    calls.clear()
    put(e.recv / "D" / "Users" / "u" / ".ssh" / "config", b"c")
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert len(calls) == 1
