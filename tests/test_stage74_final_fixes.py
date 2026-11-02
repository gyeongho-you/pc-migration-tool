"""stage74: 3차 재점검 수정 재현 시험(N5 규칙 건너뜀 경고·내장 규칙 우선, N6 중단된 복원의 JSON, N7 GUI 예외 중단의 부분 결과)."""
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from pcmigrate import api, cli, envpaths
from pcmigrate.rules import RuleError, default_rules_dir, load_rules


def _pkg(path, n=3):
    m = {"format": 1, "tool_version": "x", "created": "t", "rules": [], "files": [], "skipped": [], "failed": [],
         "coverage": [], "manual_warnings": []}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(n):
            data = b"x" * (i + 1)
            arc = f"data/x{i}/f{i}.cfg"
            z.writestr(arc, data)
            m["files"].append({"arc": arc, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "rule": "demo",
                               "base": "%APPDATA%\\Vendor", "rel": f"Demo/f{i}.cfg"})
        z.writestr("manifest.json", json.dumps(m))


@pytest.fixture
def run_env(tmp_path):
    root = tmp_path / "new"
    root.mkdir()
    envpaths.set_overrides({"APPDATA": str(root)})
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "demo.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "detect": {"display_name_patterns": ["Demo"]},
        "paths": [{"path": "%APPDATA%\\Vendor\\Demo", "dest": "demo/data", "kind": "dir"}], "manual_warnings": []}), encoding="utf-8")
    z = tmp_path / "p.zip"
    _pkg(z)
    yield tmp_path, root, rd, z
    envpaths.set_overrides(None)


def _bad_rule(rd):
    (rd / "zbad.json").write_text(json.dumps({"id": "my.rule", "name": "B", "detect": {"display_name_patterns": ["B"]},
                                              "paths": [{"path": "%APPDATA%\\x\\y", "dest": "b"}]}), encoding="utf-8")


# --------------------------------------------------------------------------- N5 (a): 건너뛴 규칙 파일 안내가 기존 경고 목록으로

def test_n5a_skipped_rule_line_reaches_import_warnings_and_export_notes_and_checklist(run_env):
    tmp, root, rd, z = run_env
    _bad_rule(rd)
    res = api.run_import(z, rd, dry_run=True)
    lines = [i for w in res.plan.warnings for i in w["items"]]
    assert any("읽을 수 없는 규칙 파일 1개를 건너뛰었습니다" in x for x in lines)
    cl = api.run_checklist(rd, package=z)
    assert any("읽을 수 없는 규칙 파일 1개를 건너뛰었습니다" in x for x in cl.checklist.data)
    # 문제가 없으면 줄도 없다
    (rd / "zbad.json").unlink()
    res = api.run_import(z, rd, dry_run=True)
    assert not any("읽을 수 없는 규칙 파일" in i for w in res.plan.warnings for i in w["items"])
    assert api.rule_skip_line() == ""


def test_n5a_export_notes_carry_the_line(run_env, monkeypatch):
    tmp, root, rd, z = run_env
    _bad_rule(rd)
    from pcmigrate.api import Deps
    d = Deps()
    d.winget_export = lambda: None
    d.registry = lambda: []
    d.winget_list = lambda: None
    d.export = lambda matched, out, registry, winget_text, **kw: {"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []}
    d.summarize = lambda rules, registry: (0, 0)
    res = api.run_export(rd, tmp / "o.zip", deps=d)
    assert any("읽을 수 없는 규칙 파일 1개를 건너뛰었습니다" in n for n in res.notes)


# --------------------------------------------------------------------------- N5 (b): 내장 규칙 id 우선

def test_n5b_user_file_cannot_shadow_bundled_rule(tmp_path):
    shutil.copy(default_rules_dir() / "vscode.json", tmp_path / "vscode.json")
    shadow = json.loads((tmp_path / "vscode.json").read_text(encoding="utf-8"))
    shadow["name"] = "SHADOW"
    (tmp_path / "a_shadow.json").write_text(json.dumps(shadow), encoding="utf-8")  # 이름순으로 내장 파일보다 앞선다
    errs: list[str] = []
    rules = load_rules(tmp_path, errors=errs)
    got = [r for r in rules if r.id == "vscode"]
    assert len(got) == 1 and got[0].name != "SHADOW"
    assert len(errs) == 1 and "내장 규칙 우선" in errs[0]
    with pytest.raises(RuleError):  # 엄격 모드는 기존처럼 중복 id 오류
        load_rules(tmp_path)


def test_n5b_user_user_duplicates_and_normal_dirs_unchanged(tmp_path):
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps({"id": "mine", "name": name, "detect": {"display_name_patterns": ["m"]},
                                                 "paths": [{"path": "%APPDATA%\\V\\m", "dest": "m"}]}), encoding="utf-8")
    errs: list[str] = []
    rules = load_rules(tmp_path, errors=errs)
    assert [r.name for r in rules] == ["a.json"] and "중복된 규칙 id" in errs[0]
    errs2: list[str] = []
    bundled = load_rules(default_rules_dir(), errors=errs2)
    assert len(bundled) == 25 and errs2 == []


# --------------------------------------------------------------------------- N6

def _fail_on_call(monkeypatch, n, exc):
    calls = [0]

    def cp():
        calls[0] += 1
        if calls[0] == n:
            raise exc
    monkeypatch.setattr("pcmigrate.package_apply.checkpoint", cp)


def test_n6_interrupted_result_is_written_and_not_dry_run(run_env, monkeypatch):
    tmp, root, rd, z = run_env
    _fail_on_call(monkeypatch, 3, RuntimeError("boom"))
    with pytest.raises(RuntimeError) as ei:
        api.run_import(z, rd, yes=True, record_dir=tmp / "rec")
    part = ei.value.partial_result
    assert part.mode == "interrupted" and part.written is True
    d = part.preview_dict()
    assert d["dry_run"] is False and d["written"] is True and d["mode"] == "interrupted"
    assert part.report.meta["interrupted_error"] == "boom"


def test_n6_cli_json_for_interrupted_restore(run_env, monkeypatch, capsys):
    tmp, root, rd, z = run_env
    _fail_on_call(monkeypatch, 3, RuntimeError("boom"))
    code = cli.main(["--env", f"APPDATA={root}", "import", str(z), "--rules", str(rd), "--yes", "--json",
                     "--record-dir", str(tmp / "rec")])
    out = capsys.readouterr()
    assert code == 1
    data = json.loads(out.out)
    assert data["dry_run"] is False and data["mode"] == "interrupted" and data["interrupted"] == "RuntimeError"
    assert data["import_record"] and Path(data["import_record"]).exists()
    assert len(data["written_files"]) == 1 and data["written_files"][0].endswith("f0.cfg") and data["success"] is False


# --------------------------------------------------------------------------- N7

def _controller(tmp_path, run_import):
    import test_gui_controller as tg
    api_ = tg.FakeApi()
    c = tg.Controller(api_mod=api_, runner=tg.Runner(sync=True), rules_dir=tmp_path,
                      make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    ev = []
    c.listener = lambda kind, payload=None: ev.append((kind, payload))
    api_.run_import = lambda *a, **k: api_.preview if (k.get("dry_run") or not k.get("yes")) else run_import(api_, *a, **k)
    c.set_package(r"C:\p.zip")
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    return c, ev


def _partial(api_, exc):
    from pcmigrate.copier import FileResult, Report
    rep = Report(dry_run=False, results=[FileResult("e", r"C:\x\a.txt", "copied", 1, "x")])
    rep.meta["interrupted"] = type(exc).__name__
    if not type(exc).__name__ == "Cancelled":
        rep.meta["interrupted_error"] = str(exc)
    exc.partial_result = api.ImportResult(api_.preview.plan, "interrupted", api_.preview.n_extra, rep)
    return exc


def test_n7_gui_non_cancel_exception_with_partial_reaches_result_screen(tmp_path):
    def run_import(api_, *a, **k):
        raise _partial(api_, OSError(28, "No space left on device"))
    c, ev = _controller(tmp_path, run_import)
    c.start_apply()
    kinds = [k for k, _ in ev]
    assert "import_done" in kinds and "error" not in kinds and "cancelled" not in kinds
    assert c.state.import_result.mode == "interrupted"
    note = c.interrupted_note()
    assert note.splitlines()[0].startswith("중지했습니다") and len(note.splitlines()) == 2 and "space" in note.splitlines()[1].lower()
    assert c.import_counts()[0] == 1 and c.state.busy is False


def test_n7_gui_exception_without_partial_keeps_error_path(tmp_path):
    def run_import(api_, *a, **k):
        raise OSError(28, "No space left on device")
    c, ev = _controller(tmp_path, run_import)
    c.start_apply()
    kinds = [k for k, _ in ev]
    assert "error" in kinds and "import_done" not in kinds


def test_n7_gui_cancel_with_partial_still_works(tmp_path):
    from pcmigrate.cancel import Cancelled

    def run_import(api_, *a, **k):
        raise _partial(api_, Cancelled())
    c, ev = _controller(tmp_path, run_import)
    c.start_apply()
    assert "import_done" in [k for k, _ in ev]
    assert c.interrupted_note() == c.interrupted_note().splitlines()[0]  # 취소는 중단 이유 줄이 없다
