"""16b: 규칙 폴더 오류(C-5/F-8), CLI import 진행 표시(F-13)."""
import json
import zipfile

import pytest

from pcmigrate import cli
from conftest import make_rules_dir


@pytest.mark.parametrize("cmd", [["scan"], ["discover"], ["checklist"], ["export", "--output", "X"],
                                 ["import", "nope.zip"]])
def test_missing_rules_dir_is_friendly_nonzero(cmd, tmp_path, capsys):
    cmd = [str(tmp_path / "o.zip") if c == "X" else c for c in cmd]
    rc = cli.main(cmd + ["--rules", str(tmp_path / "no-such-rules")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "규칙 폴더를 찾을 수 없습니다" in err and "Traceback" not in err
    assert not (tmp_path / "o.zip").exists()


def test_empty_rules_dir_is_error_not_empty_package(tmp_path, capsys):
    rd = tmp_path / "rules"
    rd.mkdir()
    out = tmp_path / "o.zip"
    assert cli.main(["export", "--output", str(out), "--rules", str(rd)]) == 1
    assert "하나도 없습니다" in capsys.readouterr().err
    assert not out.exists()


def test_checklist_package_mode_also_checks_rules(tmp_path, capsys):
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": 1, "files": [], "rules": [], "manual_warnings": []}))
    rc = cli.main(["checklist", "--package", str(z), "--rules", str(tmp_path / "nope")])
    assert rc == 1


def test_cli_info_progress_is_throttled(monkeypatch, capsys):
    from pcmigrate.progress import ProgressInfo
    now = [100.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: now[0])
    cb = cli._cli_info_progress()
    mk = lambda phase, n: ProgressInfo(phase, n, 100, n, 100)  # noqa: E731
    cb(mk("restore", 1))
    now[0] += 3
    cb(mk("restore", 2))          # 10초 안 지남: 출력 안 함
    now[0] += 8
    cb(mk("restore", 3))          # 11초 경과: 출력
    cb(mk("check", 3))            # 단계 변경: 출력
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("[진행]")]
    assert len(lines) == 3 and "복원" in lines[0]


def test_cli_import_and_export_pass_progress_callback(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_import(*a, **k):
        seen["import"] = k.get("progress_info")
        from pcmigrate import api
        raise api.ApiError("stop")

    monkeypatch.setattr(cli, "run_import", fake_import)
    cli.main(["import", str(tmp_path / "p.zip"), "--yes", "--rules", str(make_rules_dir(tmp_path / "r"))])
    assert callable(seen["import"])
    assert "패키지를 검사하는 중" in capsys.readouterr().err
    monkeypatch.setattr(cli, "run_export", lambda *a, **k: (seen.__setitem__("export", k.get("progress_info")), (_ for _ in ()).throw(OSError("x")))[1])
    cli.main(["export", "--output", str(tmp_path / "o.zip"), "--rules", str(make_rules_dir(tmp_path / "r"))])
    assert callable(seen["export"])


# --------------------------------------------------------------------- F-10 탐지 패턴 보정

def _matched(*names):
    from pathlib import Path
    from pcmigrate.detect import Program, match_rules
    from pcmigrate.rules import load_rules
    rules = load_rules(Path(__file__).resolve().parents[1] / "rules")
    progs = [Program(n, "1", "") for n in names]
    return {r.id for r in match_rules(rules, progs, path_exists=lambda p: False, key_exists=lambda k: False)}


@pytest.mark.parametrize("name,expect,absent", [
    ("Rhino 8", {"rhino"}, set()),                                   # 미탐: Rhino 8 / 7 / 6 이름
    ("Rhino 7", {"rhino"}, set()),
    ("Rhinoceros 7", {"rhino"}, set()),
    ("Autodesk AutoCAD Performance Feedback Tool", set(), {"autocad"}),   # 오탐: 부속 도구
    ("Revit Interoperability for AutoCAD 2024", set(), {"autocad"}),
    ("Navisworks Exporter for AutoCAD 2024", {"navisworks"}, {"autocad"}),
    ("Microsoft Edge WebView2 Runtime", set(), {"browser"}),
    ("Microsoft Edge Update", set(), {"browser"}),
    ("V-Ray 6 for Rhinoceros 7", {"vray"}, {"rhino"}),
    ("Autodesk AutoCAD 2024 - 한국어", {"autocad"}, set()),             # 정상 감지는 유지
    ("Microsoft Edge", {"browser"}, set()),
    ("Google Chrome", {"browser"}, set()),
])
def test_detection_patterns(name, expect, absent):
    got = _matched(name)
    assert expect <= got and not (absent & got), got
