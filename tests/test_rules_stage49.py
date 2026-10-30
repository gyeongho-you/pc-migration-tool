"""stage49: AutoCAD LT 감지/경로, ZWCAD·Photoshop·Illustrator 규칙(가짜 레지스트리 이름/가짜 폴더)."""
from pathlib import Path

import pytest

from pcmigrate.detect import Program, match_rules
from pcmigrate.package import collect_items
from pcmigrate.rules import load_rules

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"


@pytest.fixture(scope="module")
def rules():
    return {r.id: r for r in load_rules(REPO_RULES)}


def detected(rules, name):
    matched = match_rules(list(rules.values()), [Program(name)], path_exists=lambda p: False,
                          key_exists=lambda k: False)
    return {r.id for r in matched}


@pytest.mark.parametrize("name,expected", [
    ("AutoCAD LT 2015 - English", {"autocad"}),
    ("AutoCAD LT 2015 - 한국어 (Korean)", {"autocad"}),
    ("AutoCAD 2015 - English", {"autocad"}),
    ("Autodesk AutoCAD Civil 3D 2024", {"civil3d"}),
    ("ZWCAD 2026", {"zwcad"}),
    ("ZWCAD 2026 SP1 (x64)", {"zwcad"}),
    ("Adobe Photoshop 2025", {"photoshop"}),
    ("Adobe Photoshop Elements 2024", set()),
    ("Adobe Photoshop Lightroom Classic", set()),
    ("Adobe Illustrator 2025", {"illustrator"}),
    ("Adobe Creative Cloud", set()),
    ("Adobe Acrobat", set()),
    ("3DF Zephyr", set()),
    ("DJI Terra", set()),
])
def test_detection_no_false_positive(rules, name, expected):
    assert detected(rules, name) == expected


def test_new_rules_schema(rules):
    for rid in ("zwcad", "photoshop", "illustrator"):
        r = rules[rid]
        assert r.manual_warnings and "*.lic" in r.exclude
        assert "https://" in r.notes and ("공식 문서 확인" in r.notes or "일반 지식·확인 필요" in r.notes)
        assert any("라이선스" in w or "로그인" in w for w in r.manual_warnings)
    assert rules["zwcad"].paths == () and "확인 필요" in rules["zwcad"].notes


def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def arcs(items):
    return sorted(i.arc for i in items)


def test_autocad_lt_support_collected(rules, tmp_path):
    base = tmp_path / "Autodesk"
    touch(base / "AutoCAD LT 2015" / "R20.0" / "enu" / "Support" / "acadlt.pgp")
    touch(base / "AutoCAD LT 2015" / "R20.0" / "enu" / "Support" / "acad.lic")
    touch(base / "AutoCAD LT 2015" / "R20.0" / "enu" / "Plotters" / "x.pc3")
    items, _ = collect_items(rules["autocad"], lambda s: s.replace("%APPDATA%", str(tmp_path)))
    got = arcs(items)
    assert any(a.endswith("acadlt.pgp") for a in got) and any(a.endswith("x.pc3") for a in got)
    assert not any(a.endswith(".lic") for a in got)


def test_photoshop_collects_each_version_settings(rules, tmp_path):
    a = tmp_path / "Adobe"
    touch(a / "Adobe Photoshop 2025" / "Adobe Photoshop 2025 Settings" / "Adobe Photoshop Prefs.psp")
    touch(a / "Adobe Photoshop 2025" / "Adobe Photoshop 2025 Settings" / "x.log")
    touch(a / "Adobe Photoshop 2025" / "Other" / "o.txt")  # Settings 밖은 제외
    touch(a / "Adobe Illustrator 29 Settings" / "en_US" / "x64" / "ai.prefs")  # 다른 프로그램
    items, _ = collect_items(rules["photoshop"], lambda s: s.replace("%APPDATA%", str(tmp_path)))
    assert len(items) == 1 and items[0].arc.endswith("Adobe Photoshop Prefs.psp")


def test_illustrator_collects_settings_subfolders(rules, tmp_path):
    a = tmp_path / "Adobe"
    touch(a / "Adobe Illustrator 29 Settings" / "en_US" / "x64" / "Adobe Illustrator Prefs")
    touch(a / "Adobe Illustrator 29 Settings" / "en_US" / "x64" / "t.tmp")
    touch(a / "Adobe Photoshop 2025" / "Adobe Photoshop 2025 Settings" / "p.psp")
    items, _ = collect_items(rules["illustrator"], lambda s: s.replace("%APPDATA%", str(tmp_path)))
    assert len(items) == 1 and items[0].arc.endswith("Adobe Illustrator Prefs")


def test_zwcad_collects_nothing(rules):
    items, skipped = collect_items(rules["zwcad"], lambda s: s)
    assert items == [] and skipped == []
