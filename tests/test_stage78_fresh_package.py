"""stage78: 새 리뷰어 지적 수정 재현 시험(복원 거부 목록 누락, 규칙 간 중복 파일, 긴 경로, Civil 3D/SketchUp 경고, path 끝 *.exe,
describe_manual 제어문자, 기록 모드 폴더 묶기 한계, 이 도구가 만든 .bak 재수집). 가짜 폴더만 쓴다."""
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from pcmigrate import cli, envpaths, fs
from pcmigrate.checklist import describe_manual
from pcmigrate.copier import copy_file
from pcmigrate.detect import Program
from pcmigrate.package import apply_import, collect_items, export_package, plan_import, restore_denied, verify_package
from pcmigrate.rules import RuleError, default_rules_dir, find_overlaps, load_rules, parse_rule

B = "\\"


def build(zpath, files, mtime=None):
    m = {"format": 1, "tool_version": "x", "created": "t", "rules": [], "files": [], "skipped": [], "failed": [],
         "coverage": [], "manual_warnings": []}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (rule, base, rel, data) in enumerate(files):
            arc = f"data/x{i}/{os.path.basename(rel)}"
            z.writestr(arc, data)
            m["files"].append({"arc": arc, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                               "rule": rule, "base": base, "rel": rel})
        z.writestr("manifest.json", json.dumps(m))
    return m


def mk_rule(rid="demo", **over):
    d = {"id": rid, "name": rid, "detect": {"display_name_patterns": [rid]},
         "paths": [{"path": "%APPDATA%" + B + "Vendor" + B + "Demo", "dest": rid + "/data"}]}
    d.update(over)
    return parse_rule(d)


# --------------------------------------------------------------------------- 거부 목록

DENIED_EXTRA = [
    ("%APPDATA%", "Python/Python311/site-packages/evil.pth"), ("%APPDATA%", "Python/Python311/site-packages/evil.PTH"),
    ("%APPDATA%", "Python/Python311/site-packages/x.pyd"), ("%APPDATA%", "Python/Python311/site-packages/x.pyc"),
    ("%APPDATA%", "Python/Python311/site-packages/readme.txt"), ("%APPDATA%", "Foo/x.pyz"), ("%APPDATA%", "Foo/x.egg"),
    ("%APPDATA%", "Foo/x.whl"), ("%USERPROFILE%", ".ipython/profile_default/startup/a.ipy"),
    ("%USERPROFILE%", ".cargo/config.toml"), ("%USERPROFILE%", ".CARGO/Config.TOML"), ("%USERPROFILE%", ".cargo/config"),
    ("%USERPROFILE%", ".aws/config"), ("%USERPROFILE%", ".aws/credentials"), ("%USERPROFILE%", ".gradle/gradle.properties"),
    ("%USERPROFILE%", ".terraformrc"), ("%APPDATA%", "terraform.rc"), ("%APPDATA%", "npm/etc/npmrc"), ("%APPDATA%", "NPM/ETC/NPMRC"),
    ("%APPDATA%", "pip/pip.ini"), ("%APPDATA%", "Microsoft/Outlook/VbaProject.OTM"), ("%APPDATA%", "Microsoft/AddIns/x.xla"),
    ("%APPDATA%", "Microsoft/AddIns/x.PPAM"), ("%APPDATA%", "Notepad++/shortcuts.xml"), ("%APPDATA%", "Foo/x.psc1"),
    ("%APPDATA%", "Adobe/x.jsx"), ("%APPDATA%", "Adobe/x.jsxbin"), ("%APPDATA%", "Foo/x.ppa")]


@pytest.mark.parametrize("base,rel", DENIED_EXTRA)
def test_restore_denied_new_entries(base, rel):
    assert restore_denied(rel, Path("C:/x") / rel, None, base), (base, rel)


@pytest.mark.parametrize("base,rel", DENIED_EXTRA)
def test_extra_items_are_denied_in_plan_and_not_written(tmp_path, base, rel):
    root = tmp_path / "new"
    root.mkdir()
    z = tmp_path / "p.zip"
    build(z, [("extra", base, rel, b"x")])

    def expand(s):
        return s.replace("%APPDATA%", str(root / "ap")).replace("%USERPROFILE%", str(root / "home"))
    plan = plan_import(z, [], expand)
    assert plan.problems == [] and [i["status"] for i in plan.items] == ["denied"]
    apply_import(z, plan)
    assert not any(p.is_file() for p in root.rglob("*"))


def test_safe_extra_files_still_allowed_and_rule_explicit_extension_allowed(tmp_path):
    for base, rel in [("%APPDATA%", "Code/User/settings.json"), ("%USERPROFILE%", ".gitconfig"), ("%APPDATA%", "Foo/app.ini")]:
        assert restore_denied(rel, Path("C:/x") / rel, None, base) is None
    rule = mk_rule(paths=[{"path": "%APPDATA%" + B + "Vendor" + B + "Demo", "dest": "d", "allow_extensions": [".pth"]}])
    assert restore_denied("Demo/a.pth", Path("C:/x/Demo/a.pth"), rule, "%APPDATA%" + B + "Vendor") is None
    rule2 = mk_rule(paths=[{"path": "%APPDATA%" + B + "Vendor" + B + "Demo" + B + "*.pth", "dest": "d", "kind": "file"}])
    assert restore_denied("a.pth", Path("C:/x/Demo/a.pth"), rule2, "%APPDATA%" + B + "Vendor" + B + "Demo") is None


# --------------------------------------------------------------------------- 규칙 두 개가 같은 파일

def test_export_includes_a_shared_file_once_and_import_is_not_blocked(tmp_path):
    root = tmp_path / "old"
    (root / "Vendor" / "Demo").mkdir(parents=True)
    (root / "Vendor" / "Demo" / "a.cfg").write_bytes(b"a")

    def expand_old(s):
        return s.replace("%APPDATA%", str(root))
    r1, r2 = mk_rule("one"), mk_rule("two", paths=[{"path": "%APPDATA%" + B + "Vendor" + B + "Demo", "dest": "two/data"}])
    out = tmp_path / "o.zip"
    m = export_package([r1, r2], out, [Program("x", "1")], None, expand=expand_old)
    assert len(m["files"]) == 1 and m["files"][0]["rule"] == "one"
    assert any("이미 같은 파일을 담아" in s["reason"] for s in m["skipped"])
    new = tmp_path / "new"
    new.mkdir()
    plan = plan_import(out, [r1, r2], lambda s: s.replace("%APPDATA%", str(new)))
    assert plan.problems == [] and [i["status"] for i in plan.items] == ["would_copy"]


def test_import_duplicate_target_is_item_level_not_whole_package_block(tmp_path):
    new = tmp_path / "new"
    new.mkdir()
    z = tmp_path / "p.zip"
    r1, r2 = mk_rule("one"), mk_rule("two", paths=[{"path": "%APPDATA%" + B + "Vendor" + B + "Demo", "dest": "two/data"}])
    build(z, [("one", "%APPDATA%" + B + "Vendor", "Demo/a.cfg", b"first"), ("two", "%APPDATA%" + B + "Vendor", "Demo/a.cfg", b"second")])
    plan = plan_import(z, [r1, r2], lambda s: s.replace("%APPDATA%", str(new)))
    assert plan.problems == []
    assert [i["status"] for i in plan.items] == ["would_copy", "denied"] and plan.items[1]["skip_kind"] == "duplicate"
    apply_import(z, plan)
    assert (new / "Vendor" / "Demo" / "a.cfg").read_bytes() == b"first"
    assert not list((new / "Vendor" / "Demo").glob("*.bak*"))  # 앞 항목을 .bak 으로 밀어내지 않는다


def test_check_rules_warns_about_overlapping_rules(tmp_path, capsys):
    for rid in ("one", "two"):
        (tmp_path / f"{rid}.json").write_text(json.dumps({
            "id": rid, "name": rid, "detect": {"display_name_patterns": [rid]},
            "paths": [{"path": "%APPDATA%" + B + "Vendor" + B + "Demo", "dest": rid + "/d"}]}), encoding="utf-8")
    rules = load_rules(tmp_path)
    assert len(find_overlaps(rules)) == 1
    assert cli.main(["check-rules", "--rules", str(tmp_path)]) == 0
    assert "경로가 겹치는 규칙" in capsys.readouterr().err
    assert find_overlaps(load_rules(default_rules_dir())) == []  # 번들 규칙 25개는 겹침 없음


# --------------------------------------------------------------------------- 긴 경로

def _deep(base: Path, n=26) -> Path:
    d = base
    for i in range(n):
        d = d / ("d%02d_" % i + "x" * 10)
    return d


def test_copy_file_handles_paths_over_260_chars(tmp_path):
    src = tmp_path / "s.cfg"
    src.write_bytes(b"hello")
    dst = _deep(tmp_path / "dest") / "f.cfg"
    assert len(str(dst)) > 300
    res = copy_file(src, dst)
    assert res.status == "copied", res.error
    assert os.path.isfile(fs.long_path(dst)) and Path(fs.long_path(dst)).read_bytes() == b"hello"
    again = copy_file(src, dst)  # 이미 같음(긴 경로도 존재를 알아본다)
    assert again.status == "skipped_identical"
    src.write_bytes(b"world!")
    over = copy_file(src, dst)
    assert over.status == "copied" and over.backup and os.path.isfile(fs.long_path(over.backup))


def test_plan_is_accurate_for_long_paths_and_flags_impossible_ones(tmp_path):
    new = tmp_path / "new"
    deep = _deep(new)
    z = tmp_path / "p.zip"
    build(z, [("extra", "%APPDATA%", "x/f.cfg", b"hello")])
    expand = lambda s: s.replace("%APPDATA%", str(deep))  # noqa: E731
    plan = plan_import(z, [], expand)
    assert plan.items[0]["status"] == "would_copy"
    apply_import(z, plan)
    plan2 = plan_import(z, [], expand)  # 긴 경로에 있는 같은 파일을 '없음'으로 보아 낙관하지 않는다
    assert plan2.items[0]["status"] == "skipped_identical"
    # 이름 하나가 255자를 넘으면 접두를 써도 만들 수 없다: 미리보기부터 실패(path_too_long)
    z2 = tmp_path / "p2.zip"
    build(z2, [("extra", "%APPDATA%", "x/" + "a" * 300 + ".cfg", b"hello")])
    plan3 = plan_import(z2, [], lambda s: s.replace("%APPDATA%", str(new)))
    assert plan3.problems == [] and plan3.items[0]["status"] == "failed" and plan3.items[0]["reason"] == "path_too_long"
    rep = apply_import(z2, plan3)
    assert [r.status for r in rep.results] == ["failed"]


# --------------------------------------------------------------------------- 규칙

def test_civil3d_and_sketchup_warnings():
    rules = {r.id: r for r in load_rules(default_rules_dir())}
    c = " ".join(rules["civil3d"].manual_warnings)
    assert ".ctb" in c and ".pc3" in c and "위치 확인 필요" in c and ".lsp" in c and "allow_extensions" in c
    assert not any("Plot" in p.path or "Plotters" in p.path for p in rules["civil3d"].paths)  # 근거 없는 경로를 지어내지 않는다
    assert int(rules["civil3d"].version) >= 4
    assert any(".rbe" in w and ".rbs" in w and "반쪽" in w for w in rules["sketchup"].manual_warnings)
    assert [p.allow_extensions for p in rules["sketchup"].paths] == [(".rb",)]  # .rbs 허용 여부는 근거가 없어 그대로


@pytest.mark.parametrize("pat", ["*.exe", "*.dll", "*.EXE", "*.bat", "*.ps1"])
def test_rule_path_last_pattern_with_executable_extension_rejected(pat):
    with pytest.raises(RuleError):
        parse_rule({"id": "x1", "name": "x", "detect": {"display_name_patterns": ["x"]},
                    "paths": [{"path": "%APPDATA%" + B + "Foo" + B + pat, "dest": "x/a", "kind": "file"}]})


def test_rule_path_last_pattern_plugin_extension_still_ok_and_bundled_rules_load():
    parse_rule({"id": "x1", "name": "x", "detect": {"display_name_patterns": ["x"]},
                "paths": [{"path": "%APPDATA%" + B + "Foo" + B + "*.rb", "dest": "x/a", "kind": "file"}]})
    assert len(load_rules(default_rules_dir())) == 25


# --------------------------------------------------------------------------- describe_manual / 기록 모드 / .bak

def test_describe_manual_strips_control_characters():
    esc = chr(27)
    p = Program("Evil" + esc + "[2J" + esc + "[31m App" + chr(7), "1.0" + esc + "[0m", "Pub" + chr(13) + "lisher")
    text = describe_manual(p, None)
    assert esc not in text and chr(7) not in text and chr(13) not in text and "Evil" in text and "App" in text


def test_record_rule_notes_state_folder_binding_limit(tmp_path, monkeypatch):
    from test_record_mode import Fake
    from pcmigrate import record_mode as rm
    f = Fake(tmp_path, monkeypatch)
    try:
        z = f.app / "FakeZephyr"
        a = f.put(z / "a.ini", "1")
        b = f.put(z / "b.ini", "1")
        s, _ = f.start()
        f.snap(s)
        f.put(a, "2")
        f.put(b, "2")
        f.snap(s)
        rule = rm.build_rule(rm.diff(s))
        assert any(p["kind"] == "dir" for p in rule["paths"])
        assert "폴더째" in rule["notes"] and "일부 파일만 지정할 수 없음" in rule["notes"]
        parse_rule(rule)
    finally:
        envpaths.set_overrides(None)


def test_tool_made_bak_is_not_collected_again(tmp_path):
    root = tmp_path / "old"
    d = root / "Vendor" / "Demo"
    d.mkdir(parents=True)
    (d / "a.cfg").write_bytes(b"a")
    (d / "a.cfg.bak").write_bytes(b"old")
    (d / "a.cfg.bak.1").write_bytes(b"older")
    (d / "solo.bak").write_bytes(b"s")  # 원래 이름이 없는 .bak 은 도구가 만든 백업으로 보지 않는다
    (d / "b.cfg").write_bytes(b"b")
    items, skipped = collect_items(mk_rule(), lambda s: s.replace("%APPDATA%", str(root)))
    got = sorted(i.rel for i in items)
    assert got == ["Demo/a.cfg", "Demo/b.cfg", "Demo/solo.bak"]
    assert sum("도구가 만든" in s["reason"] or "제외" in s["reason"] for s in skipped) >= 2
