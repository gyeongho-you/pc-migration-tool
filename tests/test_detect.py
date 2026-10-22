import json
from pathlib import Path

from pcmigrate.detect import (Program, detect_programs, match_rules,
                              parse_winget_export, winget_export)
from pcmigrate.rules import load_rules

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"

WINGET_JSON = {"Sources": [{"Packages": [
    {"PackageIdentifier": "Microsoft.VisualStudioCode", "Version": "1.90"},
    {"PackageIdentifier": ""},
]}]}


def fake_runner(payload=WINGET_JSON, code=0):
    calls = []

    def run(args):
        calls.append(args)
        if code == 0:
            out = Path(args[args.index("-o") + 1])
            out.write_text(json.dumps(payload), encoding="utf-8-sig")
        return code, ""
    run.calls = calls
    return run


def test_parse_winget_export():
    progs = parse_winget_export(json.dumps(WINGET_JSON))
    assert [p.winget_id for p in progs] == ["Microsoft.VisualStudioCode"]


def test_winget_export_uses_runner():
    runner = fake_runner()
    progs = winget_export(runner)
    assert runner.calls[0][:2] == ["winget", "export"]
    assert len(progs) == 1


def test_winget_export_failures_return_empty():
    assert winget_export(fake_runner(code=1)) == []

    def boom(args):
        raise FileNotFoundError("winget")
    assert winget_export(boom) == []

    def garbage(args):
        Path(args[args.index("-o") + 1]).write_text("not json", encoding="utf-8")
        return 0, ""
    assert winget_export(garbage) == []


def test_detect_programs_combines_sources():
    reg = lambda: [Program("Autodesk Revit 2024", "24.0")]
    progs = detect_programs(reg, fake_runner())
    assert {p.source for p in progs} == {"registry", "winget"}


def test_match_only_detected_rules():
    rules = load_rules(REPO_RULES)
    programs = [Program("Autodesk Revit 2024"), Program("7-Zip")]
    matched = match_rules(rules, programs, path_exists=lambda p: False)
    assert [r.id for r in matched] == ["revit"]


def test_match_by_install_path_with_injected_expand():
    rules = load_rules(REPO_RULES)
    seen = []

    def exists(p):
        seen.append(p)
        return p.startswith("/fake")

    matched = match_rules(rules, [], path_exists=exists,
                          expand=lambda p: p.replace("%LOCALAPPDATA%", "/fake"))
    assert [r.id for r in matched] == ["vscode"]


def test_match_case_insensitive():
    rules = load_rules(REPO_RULES)
    matched = match_rules(rules, [Program("autocad 2023 - english")],
                          path_exists=lambda p: False)
    assert [r.id for r in matched] == ["autocad"]
