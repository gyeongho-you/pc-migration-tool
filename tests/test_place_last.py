"""place 마지막 검증(stage48 T1~T8) 회귀 시험. 임시 폴더와 주입만 쓴다."""
import json
import os
import stat
from pathlib import Path

import pytest

from pcmigrate import cli, place_original as po
from test_place_original import BIG, Env, put, tree

NOPROT = lambda p, **k: None  # noqa: E731
WIN = pytest.mark.skipif(os.name != "nt", reason="Windows 전용")


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    e = Env(tmp_path, letters="D")
    monkeypatch.setitem(po.place.__kwdefaults__, "drive_root_fn", e.root)
    monkeypatch.setitem(po.place.__kwdefaults__, "dest_info_fn", lambda p: BIG)
    monkeypatch.setitem(po.place.__kwdefaults__, "protected_fn", NOPROT)
    return e


# --------------------------------------------------------------------------- T1 PowerShell 프로필(한글 Windows/문서 폴더 리디렉션)
@pytest.mark.parametrize("path", [
    "C:\\Users\\x\\OneDrive\\문서\\WindowsPowerShell\\Microsoft.PowerShell_profile.ps1",
    "C:\\Users\\x\\OneDrive - 회사\\문서\\PowerShell\\profile.ps1",
    "D:\\Documents\\WindowsPowerShell\\profile.ps1",  # 문서 폴더를 다른 드라이브로 리디렉션
    "C:\\Users\\x\\내 문서\\WindowsPowerShell\\Microsoft.VSCode_profile.ps1",
    "D:\\My Documents\\PowerShell\\profile.ps1",
    "c:\\USERS\\X\\DOCUMENTS\\WINDOWSPOWERSHELL\\PROFILE.PS1",
])
def test_powershell_profiles_are_protected_on_localized_windows(path, monkeypatch):
    monkeypatch.setattr(po, "known_documents_fn", lambda: "")
    assert po.protected_reason(path, is_dir=False)


@pytest.mark.parametrize("path", [
    "D:\\bk\\WindowsPowerShell\\profile.ps1",  # 임의 경로의 백업 복사본
    "D:\\proj\\profile.ps1",
    "C:\\Users\\x\\Config\\a.txt",
    "C:\\Users\\x\\Documents\\WindowsPowerShell\\Modules\\M\\profile.ps1",
    "C:\\Users\\x\\Documents\\WindowsPowerShell\\profile.ps1.bak",
    "C:\\Users\\x\\Documents\\WindowsPowerShell\\profile.psm1",
    "C:\\Users\\x\\Documents\\WindowsPowerShell\\notes.txt",
    "D:\\WindowsPowerShell\\profile.ps1",
])
def test_powershell_rule_still_lets_ordinary_files_through(path, monkeypatch):
    monkeypatch.setattr(po, "known_documents_fn", lambda: "")
    assert po.protected_reason(path, is_dir=False) is None


def test_powershell_profile_under_known_documents_folder_and_8dot3(monkeypatch):
    monkeypatch.setattr(po, "known_documents_fn", lambda: "E:\\Redirected")  # 알려진 폴더 조회 주입
    assert po.protected_reason("E:\\Redirected\\WindowsPowerShell\\profile.ps1", is_dir=False)
    assert po.protected_reason("E:\\Other\\WindowsPowerShell\\profile.ps1", is_dir=False) is None
    monkeypatch.setattr(po, "expand_short_names", lambda p: p.replace("MYDOCU~1", "My Documents"))
    assert po.protected_reason("D:\\MYDOCU~1\\PowerShell\\profile.ps1", is_dir=False)


# --------------------------------------------------------------------------- T2 사용자 .bak 은 보관본이 아니다
def test_user_bak_files_are_origin_rows_not_backups(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "Q" / "draw.dwg", b"drawing")
    put(e.recv / "Q" / "draw.dwg.bak", b"user's own backup")
    g = tmp_path / "g"
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    text = (g / po.GUIDE_NAME).read_text(encoding="utf-8-sig")
    rows = [x.split(" | ")[0] for x in text.splitlines() if " | " in x and not x.startswith("모은 위치")]
    assert rows == ["Q\\draw.dwg", "Q\\draw.dwg.bak"] and po._BAK_SECTION not in text


def test_tool_made_bak_is_carried_across_runs_and_wording(tmp_path):
    e = Env(tmp_path, letters="D")
    g = tmp_path / "g"
    put(e.recv / "Z" / "a.txt", b"v1")
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    put(e.recv / "Z" / "a.txt", b"v2")
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    put(e.recv / "Z" / "b.txt", b"b")  # 세 번째 실행: 새 파일만 모은다
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    text = (g / po.GUIDE_NAME).read_text(encoding="utf-8-sig")
    assert not any(x.startswith("Z\\a.txt.bak | ") for x in text.splitlines())
    sec = text.split(po._BAK_SECTION)[1]
    assert "Z\\a.txt.bak" in sec and "내용을 확인한 뒤 정리하세요" in sec and "지워도 됩니다" not in sec


# --------------------------------------------------------------------------- T3 리포트 사전 점검(열린 파일)
@WIN
def test_report_open_in_another_program_is_rejected_nondestructively(cli_env, tmp_path, capsys):
    e = cli_env
    put(e.recv / "D" / "p" / "a.txt", b"a")
    rp = put(tmp_path / "r.json", b'{"keep": "me"}')
    before = tree(e.recv)
    h = open(rp, "rb")  # 공유 삭제 없이 열어 둔 상태(이름 바꾸기/삭제가 막힌다)
    try:
        assert cli.main(["place", str(e.recv), "--yes", "--report", str(rp)]) == 1
        err = capsys.readouterr().err
        assert "교체할 수 없습니다" in err and tree(e.recv) == before and not (e.drives / "D" / "p" / "a.txt").exists()
        assert h.read() == b'{"keep": "me"}'
    finally:
        h.close()
    assert rp.read_bytes() == b'{"keep": "me"}' and not list(tmp_path.glob("*pcmigrate-swap*"))  # 데이터와 이름이 그대로


def test_report_probe_leaves_file_and_names_untouched_on_success(cli_env, tmp_path):
    rp = put(tmp_path / "r.json", b"original")
    assert cli._probe_report(str(rp), create=False) is None
    assert rp.read_bytes() == b"original" and sorted(p.name for p in tmp_path.glob("r.json*")) == ["r.json"]


def test_final_report_save_failure_falls_back_to_alternate_name(cli_env, tmp_path, monkeypatch, capsys):
    e = cli_env
    put(e.recv / "D" / "p" / "a.txt", b"a")
    real = po.PlaceReport.write

    def write(self, path):
        if not str(path).endswith(".new.json"):
            raise PermissionError(5, "denied", str(path))
        return real(self, path)
    monkeypatch.setattr(po.PlaceReport, "write", write)
    rp = tmp_path / "r.json"
    assert cli.main(["place", str(e.recv), "--report", str(rp)]) == 1
    alt = tmp_path / "r.new.json"
    assert alt.is_file() and json.loads(alt.read_text(encoding="utf-8"))["dry_run"] is True
    assert "대신" in capsys.readouterr().err and not rp.exists()


# --------------------------------------------------------------------------- T4 안내 파일 교체의 안전
def _gather_twice_setup(tmp_path):
    e = Env(tmp_path, letters="D")
    g = tmp_path / "g"
    put(e.recv / "Z" / "z.txt", b"z")
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    put(e.recv / "Z" / "y.txt", b"y")
    return e, g


def test_guide_is_restored_if_replace_is_interrupted(tmp_path, monkeypatch):
    e, g = _gather_twice_setup(tmp_path)
    old = (g / po.GUIDE_NAME).read_text(encoding="utf-8-sig")
    real = os.replace

    def replace(a, b, *x, **k):
        if str(a).endswith(po.PART_SUFFIX) and str(b).endswith(po.GUIDE_NAME):
            raise KeyboardInterrupt  # 이름 바꾸기 사이에 취소/중단
        return real(a, b, *x, **k)
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(KeyboardInterrupt):
        e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    monkeypatch.undo()
    assert (g / po.GUIDE_NAME).read_text(encoding="utf-8-sig") == old  # 이전 안내가 제자리로 돌아왔다
    assert not list(g.glob("*" + po.PART_SUFFIX))


def test_guide_lines_are_carried_from_our_bak_when_guide_is_missing(tmp_path):
    e, g = _gather_twice_setup(tmp_path)
    os.replace(g / po.GUIDE_NAME, g / (po.GUIDE_NAME + ".bak"))  # 바꾸다 죽어 .bak 만 남은 상태
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    lines = (g / po.GUIDE_NAME).read_text(encoding="utf-8-sig")
    assert "Z\\z.txt | " in lines and "Z\\y.txt | " in lines  # 이전 줄도 이월


def test_foreign_bak_is_not_overwritten_when_guide_is_rewritten(tmp_path):
    e, g = _gather_twice_setup(tmp_path)
    mine = put(g / (po.GUIDE_NAME + ".bak"), b"user's own file named like our backup")
    e.run(apply=True, gather=str(g), protected_fn=NOPROT)
    assert mine.read_bytes() == b"user's own file named like our backup"  # 우리 표식이 없는 .bak 은 덮지 않는다
    assert (g / (po.GUIDE_NAME + ".bak.1")).is_file() and po._is_our_guide(g / (po.GUIDE_NAME + ".bak.1"))


# --------------------------------------------------------------------------- T5 ADS 오류 분류
@pytest.mark.parametrize("err,fst,expected", [
    (38, "NTFS", []), (1, "NTFS", []), (50, "ReFS", []),  # 스트림 없음/미지원 코드
    (87, "FAT32", []), (5, "exFAT", []), (2, "FAT", []),  # NTFS/ReFS 가 아니면 코드와 무관하게 없음
    (87, "NTFS", None), (5, "NTFS", None), (2, "NTFS", None), (5, None, None),  # 그 밖의 오류는 확인 불가
])
def test_stream_error_classification(err, fst, expected):
    assert po._stream_error_result(err, fst) == expected


@WIN
def test_alt_streams_uses_volume_type_on_lookup_failure(tmp_path, monkeypatch):
    missing = tmp_path / "no_such_file.txt"  # 조회가 실패한다(오류 2)
    monkeypatch.setattr(po, "volume_fs_fn", lambda p: "FAT32")
    assert po._alt_streams(missing) == []
    monkeypatch.setattr(po, "volume_fs_fn", lambda p: "NTFS")
    assert po._alt_streams(missing) is None


# --------------------------------------------------------------------------- T6 시스템 이름의 사용자 폴더
def test_user_folders_named_like_system_files_are_normal_units(tmp_path):
    e = Env(tmp_path, letters="D")
    put(e.recv / "Thumbs.db" / "pics" / "a.jpg", b"a")  # 폴더 이름이 Thumbs.db
    put(e.recv / "desktop.ini" / "b.txt", b"b")
    put(e.recv / "desktop.ini.keep", b"x")
    rep = e.run(apply=True)
    codes = sorted((r.current.rsplit("\\", 1)[-1], r.code) for r in rep.skipped)
    assert ("Thumbs.db", po.UNKNOWN_LOC) in codes and ("desktop.ini", po.UNKNOWN_LOC) in codes
    assert not rep.to_dict()["remaining_in_receive"]["system_items_ignored"]
    put(e.recv / "Thumbs.db.file", b"")  # 이름이 비슷해도 무시 대상이 아니다


# --------------------------------------------------------------------------- T7 '다음 할 일'
def test_next_step_line_appears_for_warnings_only_and_for_remaining_files_only():
    r = po.PlaceReport(False, False, "recv")
    r.warnings.append({"kind": "move_check", "expected": "D:\\a", "current": "x", "text": "확인하세요"})
    assert any(x.startswith("다음 할 일: 확인할 경고") for x in po.summary_lines(r))
    r2 = po.PlaceReport(False, False, "recv")
    r2.remaining = {"files": 2, "bytes": 5, "empty_folders": 0, "empty_folder_examples": [], "records": 0, "record_examples": [],
                    "unreadable": 0, "unreadable_examples": [], "system_items_ignored": []}
    assert r2.needs_manual and any("받기 폴더에 남은 파일" in x and x.startswith("다음 할 일") for x in po.summary_lines(r2))
    assert not any(x.startswith("다음 할 일") for x in po.summary_lines(po.PlaceReport(False, False, "recv")))
