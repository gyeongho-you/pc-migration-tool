"""stage83: 입력 오류 문구 정리(내부 예외 문구 제거, 파일 없음/폴더/권한/0바이트/손상 구분)와 CLI 반복 출력 정리(수동 작업 경고를 한 구역으로,
핵심 요약은 항상 맨 아래). --json 스키마와 종료 코드는 그대로. 가짜 폴더/가짜 환경만 쓴다."""
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from pcmigrate import api, cli, failure, summary
from pcmigrate.package_verify import (PKG_CORRUPT, PKG_DENIED, PKG_EMPTY, PKG_IS_DIR, PKG_NOT_FOUND, verify_package)

LEAKS = ("[Errno", "WinError", "No such file", "Permission denied", "cannot find", "Traceback")


def no_leak(text: str) -> None:
    for x in LEAKS:
        assert x not in text, (x, text)


@pytest.fixture
def iso(tmp_path):
    """모든 환경변수를 임시 폴더로 지정(--env). 결과 경로가 임시 폴더 아래인지 확인한다."""
    env = []
    for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMDATA"):
        d = tmp_path / "env" / k
        d.mkdir(parents=True)
        env += ["--env", f"{k}={d}"]
        assert str(d).startswith(str(tmp_path))
    return env


# --------------------------------------------------------------------------- A. 오류 문구

def test_a_verify_package_distinguishes_open_failures(tmp_path, monkeypatch):
    missing = tmp_path / "missing.zip"
    m, p = verify_package(missing)
    assert m is None and p == [PKG_NOT_FOUND]
    d = tmp_path / "dir.zip"
    d.mkdir()
    assert verify_package(d)[1] == [PKG_IS_DIR]
    empty = tmp_path / "empty.zip"
    empty.write_bytes(b"")
    assert verify_package(empty)[1] == [PKG_EMPTY]
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip file at all" * 10)
    assert verify_package(bad)[1] == [PKG_CORRUPT]
    ok = tmp_path / "ok.zip"
    with zipfile.ZipFile(ok, "w") as z:
        z.writestr("x", "1")
    real_stat = os.stat

    def deny(path, *a, **k):
        if str(path).endswith("ok.zip"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *a, **k)
    monkeypatch.setattr("pcmigrate.package_verify.os.stat", deny)
    assert verify_package(ok)[1] == [PKG_DENIED]
    for text in (PKG_NOT_FOUND, PKG_IS_DIR, PKG_DENIED, PKG_EMPTY, PKG_CORRUPT):
        no_leak(text)


def test_a_cli_import_missing_zip_gives_user_text_and_next_step(tmp_path, iso, capsys):
    from conftest import make_rules_dir
    rd = make_rules_dir(tmp_path / "rules")
    code = cli.main([*iso, "import", str(tmp_path / "nope.zip"), "--dry-run", "--rules", str(rd)])
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert code == 1 and PKG_NOT_FOUND in text
    no_leak(text)
    assert "경로와 읽기 권한을 확인" in text and "다시 만들거나" not in text  # 파일 없음은 '손상' 안내가 아니다


def test_a_cli_import_corrupt_zip_keeps_rebuild_hint(tmp_path, iso, capsys):
    from conftest import make_rules_dir
    rd = make_rules_dir(tmp_path / "rules")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"garbage" * 20)
    code = cli.main([*iso, "import", str(bad), "--dry-run", "--rules", str(rd)])
    text = "".join(capsys.readouterr())
    assert code == 1 and PKG_CORRUPT in text and "다시 만들거나" in text
    no_leak(text)


def test_a_checklist_and_install_check_use_same_text(tmp_path):
    from conftest import make_rules_dir
    rd = make_rules_dir(tmp_path / "rules")
    with pytest.raises(api.ApiError) as e1:
        api.run_checklist(rd, package=tmp_path / "nope.zip")
    with pytest.raises(api.ApiError) as e2:
        api.run_install_check(tmp_path / "nope.zip")
    for e in (e1, e2):
        assert PKG_NOT_FOUND in str(e.value)
        no_leak(str(e.value))


def test_a_user_reason_has_no_internal_text():
    cases = [FileNotFoundError(2, "No such file or directory", "C:/x"), PermissionError(13, "Permission denied", "C:/x"),
             IsADirectoryError(21, "Is a directory", "C:/x"), NotADirectoryError(20, "Not a directory", "C:/x"),
             OSError(22, "Invalid argument", "C:/x"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
             json.JSONDecodeError("Expecting value", "", 0), ValueError("weird internal")]
    for exc in cases:
        text = failure.user_reason(exc, "C:/x")
        no_leak(text)
        assert not any(ord(c) < 32 for c in text) and "invalid" not in text.lower() and "Expecting" not in text
    assert "찾을 수 없습니다" in failure.user_reason(FileNotFoundError(2, "x", "C:/x"), "C:/x")
    assert "폴더" in failure.user_reason(IsADirectoryError(21, "x"), "")
    assert "번째 줄" in failure.user_reason(json.JSONDecodeError("m", "a\nb", 3))


def test_a_selection_file_manifest_and_write_errors_have_no_internal_text(tmp_path):
    from pcmigrate import discover, find_data, install_dir, manifest as mf
    from pcmigrate.api import format_write_error
    missing = tmp_path / "sel.json"
    for fn, exc in ((discover.load_selection, discover.SelectionError), (find_data.load_selection, find_data.SelectionFileError)):
        with pytest.raises(exc) as ei:
            fn(missing)
        no_leak(str(ei.value))
        assert "찾을 수 없습니다" in str(ei.value)
    with pytest.raises(discover.SelectionError) as ei:
        install_dir.load_install_selection(missing)
    no_leak(str(ei.value))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(find_data.SelectionFileError) as ei:
        find_data.load_selection(bad)
    no_leak(str(ei.value))
    assert "번째 줄" in str(ei.value)
    with pytest.raises(mf.ManifestError) as ei:
        mf.ManifestReader(tmp_path / "nope.jsonl")
    no_leak(str(ei.value))
    lines = format_write_error(str(tmp_path / "x" / "y.zip"), FileNotFoundError(2, "The system cannot find the path specified", str(tmp_path / "x" / "y.zip")))
    no_leak("\n".join(lines))


def test_a_cli_winget_list_file_missing_has_no_internal_text(tmp_path, iso, capsys):
    from conftest import make_rules_dir
    z = tmp_path / "p.zip"
    _pkg(z)
    code = cli.main([*iso, "install-check", str(z), "--winget-list-file", str(tmp_path / "nope.txt")])
    text = "".join(capsys.readouterr())
    assert code == 1 and "읽을 수 없습니다" in text
    no_leak(text)


# --------------------------------------------------------------------------- B. 반복 출력 정리

def _warn(n, same=0):
    out = []
    for i in range(n):
        out.append({"rule": f"r{i}", "name": f"Program {i}", "items": [
            "Autodesk 계정 재로그인이 필요합니다.", f"고유 안내 {i} 번째 프로그램에서 직접 확인하세요.", "GPU/드라이버 의존: 그래픽 드라이버를 먼저 설치하세요.",
            "라이선스는 복원되지 않습니다. 각 프로그램을 재로그인/재활성화하세요."]})
    return out


def test_b_manual_work_lines_dedupe_group_and_cap():
    lines = summary.manual_work_lines(_warn(13))
    text = "\n".join(lines)
    assert text.count("Autodesk 계정 재로그인이 필요합니다.") == 1  # 같은 경고는 한 번만(13개 프로그램이 똑같이 내도)
    assert "수동으로 해야 할 일 15건" in lines[0]  # 같은 문장은 한 번씩만 센다(공통 2 + 고유 13)
    assert text.count("라이선스는 복원되지 않습니다") == 1 and lines[-1].startswith("[수동 작업 필요]")
    assert lines[0].startswith("수동으로 해야 할 일 ") and "pcmigrate checklist" in lines[0]
    assert len(lines) <= 1 + summary.MANUAL_MAX_PROGRAMS + 1 + 1  # 머리 + 프로그램(최대 6) + '그 밖' 줄 + 라이선스
    assert any("그 밖의 프로그램" in x and "pcmigrate checklist" in x for x in lines)
    assert any("(같은 안내:" in x for x in lines) and any(" 외 " in x and "건" in x for x in lines)
    assert summary.manual_work_lines([])[-1].startswith("[수동 작업 필요]")  # 경고가 없어도 라이선스 안내는 남는다


def _record(monkeypatch):
    rec = []
    monkeypatch.setattr(cli, "_err", lambda *ls: rec.extend(("err", x) for x in ls))
    monkeypatch.setattr(cli, "_print_lines", lambda ls: rec.extend(("out", x) for x in ls))
    return rec


def _pkg(path, warnings=None):
    data = b"x"
    m = {"format": 1, "tool_version": "x", "created": "t", "rules": [], "files": [{
        "arc": "data/x/a.cfg", "size": 1, "sha256": hashlib.sha256(data).hexdigest(), "rule": "extra", "base": "%APPDATA%", "rel": "X/a.cfg"}],
        "skipped": [], "failed": [], "coverage": [], "manual_warnings": warnings or []}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data/x/a.cfg", data)
        z.writestr("manifest.json", json.dumps(m))


def test_b_import_dry_run_summary_is_last_and_warnings_once(tmp_path, iso, monkeypatch):
    from conftest import make_rules_dir
    rd = make_rules_dir(tmp_path / "rules")
    z = tmp_path / "p.zip"
    _pkg(z, _warn(13))
    rec = _record(monkeypatch)
    code = cli.main([*iso, "import", str(z), "--dry-run", "--rules", str(rd)])
    assert code == 0
    kinds = [k for k, _ in rec]
    last_err = max(i for i, k in enumerate(kinds) if k == "err")
    first_out = min(i for i, k in enumerate(kinds) if k == "out")
    assert first_out > last_err or rec[-1][0] == "out"  # 요약(stdout)이 마지막
    assert rec[-1][0] == "out" and "다음 할 일" in rec[-1][1] and "--yes" in rec[-1][1]
    text = "\n".join(t for _, t in rec)
    assert text.count("라이선스는 복원되지 않습니다") == 1 and text.count("Autodesk 계정 재로그인이 필요합니다.") == 1
    assert sum(1 for _, t in rec if t.startswith("[수동 작업 필요]")) == 1
    assert "수동으로 해야 할 일" in text


def test_b_import_needs_confirm_exit_code_and_json_unchanged(tmp_path, iso, capsys):
    from conftest import make_rules_dir
    rd = make_rules_dir(tmp_path / "rules")
    z = tmp_path / "p.zip"
    _pkg(z, _warn(3))
    assert cli.main([*iso, "import", str(z), "--rules", str(rd)]) == api.NEEDS_CONFIRM  # --yes 없음: 종료 코드 그대로
    capsys.readouterr()
    assert cli.main([*iso, "import", str(z), "--dry-run", "--json", "--rules", str(rd)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {"dry_run", "mode", "written", "summary", "problems", "items", "programs", "selection", "coverage"} <= set(data)


def test_b_export_summary_last_json_and_exit_codes(tmp_path, iso, monkeypatch, capsys):
    from conftest import make_rules_dir
    manifest = {"rules": [], "files": [{"arc": "a"}], "skipped": [], "failed": [], "manual_warnings": _warn(13)}
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [])
    monkeypatch.setattr(cli, "winget_export_raw", lambda: None)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: None)
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: manifest)
    rd = make_rules_dir(tmp_path / "rules")
    rec = _record(monkeypatch)
    assert cli.main([*iso, "export", "--output", str(tmp_path / "o.zip"), "--rules", str(rd)]) == 0
    assert rec[-1][0] == "out" and "다음 할 일" in rec[-1][1]
    text = "\n".join(t for _, t in rec)
    assert text.count("라이선스는 복원되지 않습니다") == 1 and text.count("Autodesk 계정 재로그인이 필요합니다.") == 1
    monkeypatch.undo()
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [])
    monkeypatch.setattr(cli, "winget_export_raw", lambda: None)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: None)
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: manifest)
    capsys.readouterr()
    assert cli.main([*iso, "export", "--output", str(tmp_path / "o2.zip"), "--rules", str(rd), "--json"]) == 0
    out = capsys.readouterr()
    data = json.loads(out.out)  # stdout 은 JSON 하나뿐
    assert "unreadable" in data and "수동으로 해야 할 일" in out.err
