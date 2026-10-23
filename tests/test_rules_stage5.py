from pathlib import Path

import pytest

from pcmigrate.detect import Program, match_rules
from pcmigrate.package import collect_items, export_package, plan_import
from pcmigrate.rules import load_rules

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"
NEW_RULES = ["rhino", "sketchup", "archicad", "lumion", "enscape", "bluebeam"]
WARNING_ONLY = ["vray", "navisworks", "browser"]
ALL_IDS = {"revit", "autocad", "vscode", *NEW_RULES, *WARNING_ONLY}


@pytest.fixture(scope="module")
def rules():
    return {r.id: r for r in load_rules(REPO_RULES)}


def detected(rules, name, **kw):
    matched = match_rules(list(rules.values()), [Program(name)], path_exists=lambda p: False,
                          key_exists=lambda k: False, **kw)
    return {r.id for r in matched}


# --------------------------------------------------------------------- 스키마/내용

def test_all_rules_load_and_expected_ids(rules):
    assert set(rules) == ALL_IDS
    assert all(r.manual_warnings for r in rules.values())
    assert all("*.lic" in r.exclude for r in rules.values())


@pytest.mark.parametrize("rid", ["rhino", "sketchup", "archicad", "lumion", "enscape", "bluebeam",
                                 "vray"])
def test_license_reactivation_warning_present(rules, rid):
    assert any("라이선스" in w for w in rules[rid].manual_warnings)


@pytest.mark.parametrize("rid", ["archicad", "bluebeam", *WARNING_ONLY])
def test_pathless_rules_say_confirm_needed(rules, rid):
    assert rules[rid].paths == ()
    assert "확인 필요" in rules[rid].notes


@pytest.mark.parametrize("rid", NEW_RULES + WARNING_ONLY)
def test_notes_state_evidence_grade(rules, rid):
    assert "근거 등급" in rules[rid].notes


def test_common_warning_items_covered(rules):
    text = lambda rid: " ".join(rules[rid].manual_warnings)
    for rid in ("lumion", "enscape", "rhino", "archicad", "vray"):
        assert "GPU" in text(rid)
    assert "동글" in text("archicad")
    assert "DPAPI" in text("browser")
    assert "Autodesk" in text("navisworks")
    assert "종료" in text("lumion") and "OneDrive" in text("lumion")
    assert "RUI" in text("rhino") and "Rhino 5" in text("rhino") and "OptionsExport" in text("rhino")
    assert ".bpx" in text("bluebeam") and "Tool Chest" in text("bluebeam")
    assert ".dat" in text("sketchup")


def test_no_forbidden_dest_and_unique_dests(rules):
    for r in rules.values():
        dests = [p.dest for p in r.paths]
        assert len(dests) == len(set(dests)), r.id


# --------------------------------------------------------------------- 감지 패턴 오탐

@pytest.mark.parametrize("name,expected", [
    ("Rhinoceros 8", {"rhino"}),
    ("V-Ray 6 for Rhino 7", {"vray"}),
    ("V-Ray 6 for SketchUp", {"vray"}),
    ("Enscape", {"enscape"}),
    ("Enscape 3D", {"enscape"}),
    ("SketchUp Pro 2024", {"sketchup"}),
    ("SketchUp 2017", {"sketchup"}),
    ("Lumion LiveSync for SketchUp", {"lumion"}),
    ("Lumion 2024", {"lumion"}),
    ("ARCHICAD 26", {"archicad"}),
    ("Bluebeam Revu 21", {"bluebeam"}),
    ("Autodesk Navisworks Manage 2024", {"navisworks"}),
    ("Autodesk Revit 2024", {"revit"}),
    ("AutoCAD 2024 - English", {"autocad"}),
    ("Google Chrome", {"browser"}),
    ("Microsoft Visual Studio Code", {"vscode"}),
    ("Rhino Inside Revit", set()),
    ("7-Zip 23.01 (x64)", set()),
])
def test_detect_patterns_do_not_cross_match(rules, name, expected):
    assert detected(rules, name) == expected


def test_install_path_and_registry_detection_for_rhino(rules):
    matched = match_rules(list(rules.values()), [], path_exists=lambda p: p.endswith("Rhino 8"),
                          expand=lambda p: p, key_exists=lambda k: False)
    assert [r.id for r in matched] == ["rhino"]
    matched = match_rules(list(rules.values()), [], path_exists=lambda p: False,
                          key_exists=lambda k: k == "HKLM\\SOFTWARE\\McNeel\\Rhinoceros")
    assert [r.id for r in matched] == ["rhino"]


# --------------------------------------------------------------------- glob 수집

def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def arcs(items):
    return sorted(i.arc for i in items)


def test_rhino_collects_every_version_settings_dir(rules, tmp_path):
    ro = tmp_path / "McNeel" / "Rhinoceros"
    touch(ro / "7.0" / "settings" / "a.xml")
    touch(ro / "8.0" / "settings" / "Scheme" / "b.xml")
    touch(ro / "8.0" / "other" / "c.txt")  # settings 밖은 제외
    touch(ro / "8.0" / "settings" / "license.lic")
    items, skipped = collect_items(rules["rhino"], lambda s: s.replace("%APPDATA%", str(tmp_path)))
    assert arcs(items) == ["data/rhino/settings/7.0/settings/a.xml",
                           "data/rhino/settings/8.0/settings/Scheme/b.xml"]
    assert any("제외" in s["reason"] for s in skipped)  # *.lic


def test_sketchup_collects_plugins_for_each_year(rules, tmp_path):
    base = tmp_path / "SketchUp"
    touch(base / "SketchUp 2023" / "SketchUp" / "Plugins" / "a.rb")
    touch(base / "SketchUp 2024" / "SketchUp" / "Plugins" / "sub" / "b.rb")
    touch(base / "SketchUp 2024" / "SketchUp" / "Templates" / "t.skp")
    touch(base / "SketchUp 2024" / "SketchUp" / "Plugins" / "x.lic")
    items, _ = collect_items(rules["sketchup"], lambda s: s.replace("%APPDATA%", str(tmp_path)))
    assert arcs(items) == [
        "data/sketchup/plugins/SketchUp 2023/SketchUp/Plugins/a.rb",
        "data/sketchup/plugins/SketchUp 2024/SketchUp/Plugins/sub/b.rb"]


def lumion_env(tmp_path):
    docs = tmp_path / "home" / "Documents"
    od = tmp_path / "od" / "Documents"
    touch(docs / "Lumion 2024" / "Library" / "model.lib")
    touch(docs / "Lumion (2023)" / "Materials" / "m.mat")  # 괄호 있는 폴더명도 수집
    touch(docs / "Lumion 2024" / "settings2024.ini")
    touch(docs / "Lumion 2024" / "Library" / "k.lic")
    touch(docs / "Lumion 2024" / "Scenes" / "big.ls")  # 규칙 밖
    touch(od / "Lumion 2024" / "GroupLibrary" / "g.grp")

    def expand(s):  # %OneDriveCommercial% 은 정의되지 않은 PC 를 흉내냄
        return (s.replace("%USERPROFILE%", str(tmp_path / "home"))
                 .replace("%OneDrive%", str(tmp_path / "od")))
    return expand


def test_lumion_collects_documents_and_onedrive_candidates(rules, tmp_path):
    items, skipped = collect_items(rules["lumion"], lumion_env(tmp_path))
    got = arcs(items)
    assert "data/lumion/documents/Library/Lumion 2024/Library/model.lib" in got
    assert "data/lumion/documents/Materials/Lumion (2023)/Materials/m.mat" in got
    assert "data/lumion/documents/settings/Lumion 2024/settings2024.ini" in got
    assert "data/lumion/onedrive/GroupLibrary/Lumion 2024/GroupLibrary/g.grp" in got
    assert not any("big.ls" in a or a.endswith(".lic") for a in got)
    assert any("환경변수 확장 실패" in s["reason"] for s in skipped)  # %OneDriveCommercial%


def test_enscape_collects_settings_from_documents(rules, tmp_path):
    touch(tmp_path / "home" / "Documents" / "Enscape" / "Settings" / "preset.json")
    touch(tmp_path / "home" / "Documents" / "Enscape" / "Settings" / "x.lic")
    items, _ = collect_items(rules["enscape"], lambda s: s.replace("%USERPROFILE%", str(tmp_path / "home")))
    assert arcs(items) == ["data/enscape/documents/settings/Settings/preset.json"]


@pytest.mark.parametrize("rid", ["archicad", "bluebeam", *WARNING_ONLY])
def test_pathless_rules_collect_nothing(rules, rid, tmp_path):
    items, skipped = collect_items(rules[rid], lambda s: s)
    assert items == [] and skipped == []


# --------------------------------------------------------------------- 환경변수 미확장 방어 + 왕복

def test_roundtrip_lumion_and_unexpanded_var_rejected_on_restore(rules, tmp_path):
    old_expand = lumion_env(tmp_path)
    out = tmp_path / "pkg.zip"
    m = export_package([rules["lumion"]], out, [], None, expand=old_expand)
    assert m["files"] and not any(f["arc"].endswith(".lic") for f in m["files"])

    # 신 PC: %OneDrive% 가 정의되지 않음 -> 확장 실패한 후보는 거부(현재 폴더로 새지 않음)
    new = tmp_path / "new"
    def expand_new(s):
        return s.replace("%USERPROFILE%", str(new))
    plan = plan_import(out, list(rules.values()), expand_new)
    assert any("환경변수" in p for p in plan.problems)
    assert not (tmp_path / "%OneDrive%").exists() and not Path("%OneDrive%").exists()
    assert not new.exists()

    # 모든 후보가 확장되는 PC 에서는 정상 계획
    plan_ok = plan_import(out, list(rules.values()),
                          lambda s: s.replace("%USERPROFILE%", str(new)).replace(
                              "%OneDriveCommercial%", str(new / "odc")).replace("%OneDrive%", str(new / "od")))
    assert plan_ok.problems == []
    assert {Path(i["target"]).name for i in plan_ok.items} >= {"model.lib", "settings2024.ini", "g.grp"}
