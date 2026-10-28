from conftest import patch_pkg
import json
import zipfile
from pathlib import Path

import pytest

from pcmigrate import cli, package
from pcmigrate.detect import Program, match_rules
from pcmigrate.package import (PackageError, apply_import, collect_items, export_package,
                               plan_import, split_rule_path, verify_package)
from pcmigrate.rules import load_rules, parse_rule

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"


def make_rule(**over):
    data = {
        "id": "demo", "name": "Demo", "version": "7",
        "detect": {"display_name_patterns": ["Demo"]},
        "paths": [
            {"path": "%ROAM%\\Vendor\\Demo *\\R*\\Support", "dest": "demo/support", "kind": "dir"},
            {"path": "%ROAM%\\Vendor\\prefs.json", "dest": "demo/prefs.json", "kind": "file"},
        ],
        "manual_warnings": ["재로그인 필요"],
        "exclude": ["*.tmp"],
    }
    data.update(over)
    return parse_rule(data)


@pytest.fixture
def env(tmp_path):
    """가짜 사용자 폴더(old)와 새 PC 폴더(new). 환경변수 %ROAM% 은 expand 로 주입."""
    old, new = tmp_path / "old", tmp_path / "new"
    v = old / "Vendor"
    (v / "Demo 2024" / "R1" / "Support").mkdir(parents=True)
    (v / "Demo 2025" / "R2" / "Support").mkdir(parents=True)
    (v / "Demo 2024" / "R1" / "Support" / "a.cfg").write_bytes(b"a-2024")
    (v / "Demo 2024" / "R1" / "Support" / "x.lic").write_bytes(b"SECRET-LICENSE")
    (v / "Demo 2024" / "R1" / "Support" / "x.tmp").write_bytes(b"tmp")
    (v / "Demo 2025" / "R2" / "Support" / "b.cfg").write_bytes(b"b-2025")
    (v / "prefs.json").write_bytes(b"{}")
    new.mkdir()

    def expand_for(root):
        return lambda s: s.replace("%ROAM%", str(root))
    return tmp_path, old, new, expand_for


def src_snapshot(root):
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_split_rule_path():
    assert split_rule_path("%A%\\X\\Y *\\R*\\Support") == ("%A%\\X", "Y */R*/Support")
    assert split_rule_path("%A%\\X\\file.json") == ("%A%\\X", "file.json")
    assert split_rule_path("%A%") is None
    assert split_rule_path("*\\x") is None


def test_collect_items_glob_and_excludes(env):
    _, old, _, expand_for = env
    items, skipped = collect_items(make_rule(), expand_for(old))
    arcs = sorted(i.arc for i in items)
    assert arcs == [
        "data/demo/prefs.json",
        "data/demo/support/Demo 2024/R1/Support/a.cfg",
        "data/demo/support/Demo 2025/R2/Support/b.cfg",
    ]
    reasons = " ".join(s["reason"] for s in skipped)
    assert "제외" in reasons  # .lic / .tmp
    assert not any("x.lic" in i.arc for i in items)


def test_export_manifest_and_source_untouched(env):
    tmp_path, old, _, expand_for = env
    before = src_snapshot(old)
    out = tmp_path / "pkg.zip"
    m = export_package([make_rule()], out, [Program("Demo App", "1.0")], '{"Sources": []}',
                       expand=expand_for(old))
    assert src_snapshot(old) == before
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        mf = json.loads(zf.read("manifest.json"))
        assert {"manifest.json", "programs/registry.json", "programs/winget.json"} <= names
        assert not any(n.endswith(".lic") for n in names)
        assert json.loads(zf.read("programs/registry.json"))[0]["name"] == "Demo App"
    assert mf["rules"][0]["version"] == "7" and mf["rules"][0]["name"] == "Demo"
    assert mf["manual_warnings"][0]["items"] == ["재로그인 필요"]
    assert len(mf["files"]) == 3 and all(len(f["sha256"]) == 64 for f in mf["files"])
    assert mf["failed"] == []
    assert not list(tmp_path.glob("*.pcmigrate-part"))


def test_export_without_winget_and_existing_output_backed_up(env):
    tmp_path, old, _, expand_for = env
    out = tmp_path / "pkg.zip"
    out.write_bytes(b"old package")
    m = export_package([make_rule()], out, [], None, expand=expand_for(old))
    assert m["programs"]["winget"] is None
    assert (tmp_path / "pkg.zip.bak").read_bytes() == b"old package"


def test_export_records_read_failure(env, monkeypatch):
    tmp_path, old, _, expand_for = env
    real = package._write_entry

    def flaky(zf, arc, src, stage_dir=None):
        if src.name == "a.cfg":
            raise package.SourceUnreadable(src, PermissionError("locked"))
        return real(zf, arc, src, stage_dir)
    patch_pkg(monkeypatch, "_write_entry", flaky)
    m = export_package([make_rule()], tmp_path / "p.zip", [], None, expand=expand_for(old))
    assert len(m["failed"]) == 1 and "읽지 못함" in m["failed"][0]["error"]
    assert len(m["files"]) == 2


def build(env, rules=None):
    tmp_path, old, new, expand_for = env
    rules = rules or [make_rule()]
    out = tmp_path / "pkg.zip"
    export_package(rules, out, [], None, expand=expand_for(old))
    return out, rules, new, expand_for


def test_import_dry_run_preview_writes_nothing(env):
    out, rules, new, expand_for = build(env)
    (new / "Vendor").mkdir()
    (new / "Vendor" / "prefs.json").write_bytes(b"different")
    plan = plan_import(out, rules, expand_for(new))
    assert plan.problems == []
    status = {Path(i["target"]).name: i["status"] for i in plan.items}
    assert status == {"prefs.json": "would_overwrite", "a.cfg": "would_copy", "b.cfg": "would_copy"}
    over = next(i for i in plan.items if i["status"] == "would_overwrite")
    assert over["backup"].endswith("prefs.json.bak")
    assert plan.warnings[0]["items"] == ["재로그인 필요"]
    assert [p.name for p in (new / "Vendor").iterdir()] == ["prefs.json"]  # 변화 없음
    assert (new / "Vendor" / "prefs.json").read_bytes() == b"different"


def test_import_applies_with_bak_and_is_idempotent(env):
    out, rules, new, expand_for = build(env)
    (new / "Vendor").mkdir()
    (new / "Vendor" / "prefs.json").write_bytes(b"different")
    plan = plan_import(out, rules, expand_for(new))
    rep = apply_import(out, plan)
    assert not rep.failures
    v = new / "Vendor"
    assert (v / "prefs.json").read_bytes() == b"{}"
    assert (v / "prefs.json.bak").read_bytes() == b"different"
    assert (v / "Demo 2024" / "R1" / "Support" / "a.cfg").read_bytes() == b"a-2024"
    assert not list(v.rglob("*.lic"))
    again = plan_import(out, rules, expand_for(new))
    assert {i["status"] for i in again.items} == {"skipped_identical"}


def rewrite_zip(src, dst, edit):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            for name, d in edit(info.filename, data):
                zout.writestr(name, d)


def test_tampered_content_rejected(env):
    out, rules, new, expand_for = build(env)
    bad = out.with_name("bad.zip")
    rewrite_zip(out, bad, lambda n, d: [(n, b"EVIL" if n.endswith("a.cfg") else d)])
    plan = plan_import(bad, rules, expand_for(new))
    assert any("해시" in p for p in plan.problems)
    with pytest.raises(PackageError):
        apply_import(bad, plan)
    assert not (new / "Vendor").exists()


@pytest.mark.parametrize("evil", ["../evil.txt", "data/../../evil.txt", "/abs.txt",
                                  "C:/evil.txt", "data\\..\\evil.txt"])
def test_zip_slip_entry_rejected(env, evil):
    out, rules, new, expand_for = build(env)
    bad = out.with_name("slip.zip")

    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(bad, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
        zout.writestr(zipfile.ZipInfo(evil), b"x")
    plan = plan_import(bad, rules, expand_for(new))
    assert plan.problems and plan.items == []
    assert not (new.parent / "evil.txt").exists()


def forge_manifest(out, rules, mutate):
    bad = out.with_name("forged.zip")

    def edit(n, d):
        if n == "manifest.json":
            m = json.loads(d)
            mutate(m)
            d = json.dumps(m).encode()
        return [(n, d)]
    rewrite_zip(out, bad, edit)
    return bad


def test_forged_manifest_rel_escape_rejected(env):
    out, rules, new, expand_for = build(env)
    bad = forge_manifest(out, rules, lambda m: m["files"][0].update(rel="../../evil.txt"))
    plan = plan_import(bad, rules, expand_for(new))
    assert plan.problems and plan.items == []


def test_forged_manifest_base_not_in_rule_rejected(env):
    out, rules, new, expand_for = build(env)
    bad = forge_manifest(out, rules, lambda m: m["files"][0].update(base="C:\\Windows\\System32"))
    plan = plan_import(bad, rules, expand_for(new))
    assert any("허용되지 않은" in p for p in plan.problems)
    assert plan.items == [] or all("System32" not in i["target"] for i in plan.items)


def test_forged_manifest_unknown_rule_and_license_rejected(env):
    out, rules, new, expand_for = build(env)
    bad = forge_manifest(out, rules, lambda m: m["files"][0].update(rule="nope"))
    assert any("알 수 없는" in p for p in plan_import(bad, rules, expand_for(new)).problems)
    bad2 = forge_manifest(out, rules, lambda m: m["files"][0].update(rel="Demo 2024/R1/Support/k.lic"))
    assert plan_import(bad2, rules, expand_for(new)).problems


def test_not_a_zip_and_missing_manifest(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"nope")
    assert verify_package(f)[0] is None
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("a.txt", "a")
    manifest, problems = verify_package(f)
    assert manifest is None and problems


def test_cli_import_dry_run_and_apply(env, monkeypatch, capsys):
    out, rules, new, expand_for = build(env)
    rules_dir = out.parent / "rules"
    rules_dir.mkdir()
    (rules_dir / "demo.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "7",
        "detect": {"display_name_patterns": ["Demo"]},
        "paths": [{"path": "%ROAM%\\Vendor\\Demo *\\R*\\Support", "dest": "demo/support"},
                  {"path": "%ROAM%\\Vendor\\prefs.json", "dest": "demo/prefs.json", "kind": "file"}],
        "exclude": ["*.tmp"]}), encoding="utf-8")
    monkeypatch.setattr(cli, "plan_import", lambda p, r: plan_import(p, r, expand_for(new)))
    assert cli.main(["import", str(out), "--dry-run", "--rules", str(rules_dir), "--json"]) == 0
    assert not (new / "Vendor").exists()
    captured = capsys.readouterr()
    assert "would_copy" in captured.out and "수동 작업 필요" in captured.err
    # --yes 없이: 아무것도 쓰지 않고 미리보기 + 안내, 종료 코드 2
    assert cli.main(["import", str(out), "--rules", str(rules_dir), "--json"]) == 2
    captured = capsys.readouterr()
    assert "would_copy" in captured.out and "--yes" in captured.err
    assert not (new / "Vendor").exists()
    # --dry-run 과 --yes 동시 지정: dry-run 우선
    assert cli.main(["import", str(out), "--dry-run", "--yes", "--rules", str(rules_dir)]) == 0
    assert not (new / "Vendor").exists()
    capsys.readouterr()
    # --yes: 실제 복원
    assert cli.main(["import", str(out), "--yes", "--rules", str(rules_dir)]) == 0
    assert (new / "Vendor" / "prefs.json").read_bytes() == b"{}"
    assert (new / "Vendor" / "Demo 2024" / "R1" / "Support" / "a.cfg").exists()


def test_registry_key_detection():
    rules = load_rules(REPO_RULES)
    seen = []

    def key_exists(k):
        seen.append(k)
        return k == "HKLM\\SOFTWARE\\Autodesk\\Revit"
    matched = match_rules(rules, [], path_exists=lambda p: False, key_exists=key_exists)
    assert [r.id for r in matched] == ["revit"]  # AutoCAD 규칙은 Civil 3D 와의 이중 감지를 피하려고 레지스트리 키 감지를 쓰지 않는다
    assert "HKLM\\SOFTWARE\\Autodesk\\Revit" in seen and "HKLM\\SOFTWARE\\Autodesk\\AutoCAD" not in seen


def test_autocad_rule_uses_glob_and_keeps_notes():
    rule = next(r for r in load_rules(REPO_RULES) if r.id == "autocad")
    assert any("*" in p.path for p in rule.paths)
    assert "확인 필요" in rule.notes
