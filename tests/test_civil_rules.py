"""11단계: 토목 설계 규칙(Civil 3D, QGIS, Rhino 8 보강, AutoCAD 보강, 경고 전용 규칙)과 공통 경고 테스트."""
from pathlib import Path

import pytest

from pcmigrate import api
from pcmigrate.checklist import build_checklist
from pcmigrate.checklist_data import COMMON_ITEMS
from pcmigrate.detect import Program, match_rules
from pcmigrate.discover import program_has_rule
from pcmigrate.package import collect_items, export_package, plan_import
from pcmigrate.rules import RuleError, load_rules, parse_rule

from test_discover import Env, touch

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"
WARNING_ONLY = ["midas", "ezload", "plaxis", "geostudio", "tbc", "microstation", "arcgispro"]
CHANGED = ["rhino", "autocad", "civil3d", "qgis", *WARNING_ONLY]


@pytest.fixture(scope="module")
def rules():
    return {r.id: r for r in load_rules(REPO_RULES)}


def detected(rules, name):
    matched = match_rules(list(rules.values()), [Program(name)], path_exists=lambda p: False, key_exists=lambda k: False)
    return {r.id for r in matched}


# --------------------------------------------------------------------- 스키마 / 로드

def test_all_civil_rules_load_with_expected_ids(rules):
    assert {"civil3d", "qgis", *WARNING_ONLY} <= set(rules)
    assert all(r.manual_warnings for r in rules.values())
    assert all("*.lic" in r.exclude for r in rules.values())  # 라이선스 제외 유지


def test_exclude_display_name_patterns_schema():
    base = {"id": "x", "name": "X", "detect": {"display_name_patterns": ["Foo"], "exclude_display_name_patterns": ["Bar"]}}
    r = parse_rule(base)
    assert r.exclude_display_name_patterns == ("Bar",)
    assert r.matches_name("Foo 1") and not r.matches_name("Foo Bar 1") and not r.matches_name("other")
    assert r.matches_name("FOO") and not r.matches_name("foo bar")  # 대소문자 무시
    for bad in ("Bar", [1], [""]):
        data = {"id": "x", "name": "X", "detect": {"display_name_patterns": ["Foo"], "exclude_display_name_patterns": bad}}
        with pytest.raises(RuleError):
            parse_rule(data)
    assert parse_rule({"id": "x", "name": "X", "detect": {"display_name_patterns": ["Foo"]}}).exclude_display_name_patterns == ()


# --------------------------------------------------------------------- 감지 패턴 오탐 방지

@pytest.mark.parametrize("name,expected", [
    ("AutoCAD Civil 3D 2024 - English", {"civil3d"}),  # AutoCAD 규칙과 이중 감지되지 않음
    ("Autodesk AutoCAD Civil 3D 2025 Language Pack - Korean", {"civil3d"}),
    ("Autodesk Civil 3D 2024 Object Enabler", {"civil3d"}),
    ("AutoCAD 2024 - English", {"autocad"}),
    ("AutoCAD LT 2024", {"autocad"}),
    ("AutoCAD Map 3D 2024", {"autocad"}),
    ("Autodesk Revit 2024", {"revit"}),
    ("QGIS 3.34.5 'Prizren'", {"qgis"}),
    ("QGIS Desktop 3.28.1", {"qgis"}),
    ("MicroStation CONNECT Edition", {"microstation"}),
    ("OpenRoads Designer CONNECT Edition", {"microstation"}),
    ("OpenRoads Navigator", set()),
    ("MIDAS Civil 2023", {"midas"}),
    ("midas Gen 2024", {"midas"}),
    ("MIDAS GTS NX 2023", {"midas"}),
    ("Midas Touch Media Player", set()),
    ("PLAXIS 2D CONNECT Edition", {"plaxis"}),
    ("GeoStudio 2023", {"geostudio"}),
    ("Trimble Business Center", {"tbc"}),
    ("Trimble Connect", set()),
    ("ArcGIS Pro", {"arcgispro"}),
    ("ArcGIS Desktop 10.8", set()),
    ("이지로드 2024", {"ezload"}),
    ("Rhinoceros 8", {"rhino"}),
    ("V-Ray 6 for Rhino 7", {"vray"}),
    ("Microsoft Visual Studio Code", {"vscode"}),
])
def test_detect_patterns_do_not_double_match(rules, name, expected):
    assert detected(rules, name) == expected


def test_civil3d_only_install_does_not_trigger_autocad_even_with_shared_registry_key(rules):
    # Civil 3D 설치에도 HKLM\SOFTWARE\Autodesk\AutoCAD 키가 있을 수 있으므로 AutoCAD 규칙은 레지스트리 감지를 쓰지 않는다
    assert rules["autocad"].registry_keys == ()
    matched = match_rules(list(rules.values()), [Program("AutoCAD Civil 3D 2024 - English")], path_exists=lambda p: False,
                          key_exists=lambda k: k == "HKLM\\SOFTWARE\\Autodesk\\AutoCAD")
    assert [r.id for r in matched] == ["civil3d"]
    both = match_rules(list(rules.values()), [Program("AutoCAD Civil 3D 2024"), Program("AutoCAD 2024 - English")],
                       path_exists=lambda p: False, key_exists=lambda k: False)
    assert sorted(r.id for r in both) == ["autocad", "civil3d"]  # 둘 다 설치돼 있으면 각각 감지


def test_discover_and_scan_explanations_respect_exclusion(rules):
    civil = [Program("AutoCAD Civil 3D 2024")]
    assert program_has_rule(civil[0], [rules["civil3d"]]) and not program_has_rule(civil[0], [rules["autocad"]])
    info = api.explain_matches([rules["civil3d"]], civil)
    assert info[0].programs == ["AutoCAD Civil 3D 2024"]
    assert api.explain_matches([rules["autocad"]], civil)[0].programs == []  # 이름으로는 AutoCAD 규칙에 걸리지 않는다


# --------------------------------------------------------------------- glob 수집(가짜 폴더 구조)

def arcs(items):
    return sorted(i.arc for i in items)


def test_civil3d_collects_versioned_programdata_and_roaming_but_not_pipe_catalog(rules, tmp_path):
    env = Env(tmp_path)
    for ver in ("2024", "2025"):
        touch(env.pd / "Autodesk" / f"C3D {ver}" / "enu" / "Data" / "Toolbox" / f"t{ver}.xml")
        touch(env.pd / "Autodesk" / f"C3D {ver}" / "enu" / "Survey" / f"s{ver}.db")
    touch(env.pd / "Autodesk" / "C3D 2024" / "enu" / "Pipes Catalog" / "pipe.xml")  # Pipe Catalog 는 가져오지 않는다
    touch(env.pd / "Autodesk" / "C3D 2024" / "enu" / "Data" / "Toolbox" / "RepGenSettings.xml")
    touch(env.pd / "Autodesk" / "C3D 2024" / "enu" / "Data" / "Toolbox" / "x.lic")
    touch(env.appdata / "Autodesk" / "C3D 2024" / "enu" / "Support" / "Profiles" / "p.arg")
    touch(env.appdata / "Autodesk" / "C3D 2024" / "enu" / "Support" / "c3d.cuix")
    touch(env.appdata / "Autodesk" / "C3D 2024" / "enu" / "Support" / "ToolPalette" / "t.atc")
    touch(env.appdata / "Autodesk" / "AutoCAD 2024" / "R24.3" / "enu" / "Support" / "acad.pgp")  # AutoCAD 것은 civil3d 규칙 대상 아님
    items, skipped = collect_items(rules["civil3d"], env.expand)
    got = arcs(items)
    assert got == [
        "data/civil3d/programdata/survey/C3D 2024/enu/Survey/s2024.db",
        "data/civil3d/programdata/survey/C3D 2025/enu/Survey/s2025.db",
        "data/civil3d/programdata/toolbox/C3D 2024/enu/Data/Toolbox/t2024.xml",
        "data/civil3d/programdata/toolbox/C3D 2025/enu/Data/Toolbox/t2025.xml",
        "data/civil3d/support/C3D 2024/enu/Support/Profiles/p.arg",
        "data/civil3d/support/C3D 2024/enu/Support/ToolPalette/t.atc",
        "data/civil3d/support/C3D 2024/enu/Support/c3d.cuix",
    ]
    assert not any("Pipes Catalog" in a or "RepGenSettings" in a or a.endswith(".lic") for a in got)
    assert any("제외" in s["reason"] for s in skipped)


def test_civil3d_programdata_roundtrip_is_restricted_to_rule_bases(rules, tmp_path):
    env = Env(tmp_path / "old")
    touch(env.pd / "Autodesk" / "C3D 2024" / "enu" / "Survey" / "s.db", b"db")
    out = tmp_path / "pkg.zip"
    m = export_package([rules["civil3d"]], out, [], None, expand=env.expand)
    assert [f["base"] for f in m["files"]] == ["%PROGRAMDATA%\\Autodesk"]
    new = Env(tmp_path / "new")
    plan = plan_import(out, list(rules.values()), new.expand)
    assert plan.problems == [] and Path(plan.items[0]["target"]) == new.pd / "Autodesk" / "C3D 2024" / "enu" / "Survey" / "s.db"
    # civil3d 규칙이 허용하지 않는 위치로 위조된 manifest 는 거부
    import json
    import zipfile
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(bad, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "manifest.json":
                mm = json.loads(data)
                mm["files"][0]["base"] = "%PROGRAMDATA%"
                data = json.dumps(mm).encode()
            zout.writestr(info.filename, data)
    assert any("허용되지 않은" in p for p in plan_import(bad, list(rules.values()), new.expand).problems)


def test_autocad_collects_support_and_plotters_with_plot_styles(rules, tmp_path):
    env = Env(tmp_path)
    base = env.appdata / "Autodesk" / "AutoCAD 2024" / "R24.3" / "enu"
    touch(base / "Support" / "acad.pgp")
    touch(base / "Support" / "custom.shx")
    touch(base / "Plotters" / "my.pc3")
    touch(base / "Plotters" / "Plot Styles" / "office.ctb")
    touch(env.local / "Autodesk" / "AutoCAD 2024" / "R24.3" / "enu" / "UserDataCache" / "Support" / "acad.cuix")  # 캐시
    items, _ = collect_items(rules["autocad"], env.expand)
    got = arcs(items)
    assert any(a.endswith("Support/acad.pgp") for a in got) and any(a.endswith("Support/custom.shx") for a in got)
    assert any(a.endswith("Plotters/my.pc3") for a in got) and any(a.endswith("Plotters/Plot Styles/office.ctb") for a in got)
    assert not any("UserDataCache" in a for a in got)  # 캐시 사본은 가져오지 않는다
    assert len(got) == len(set(got)) == 4


def test_rhino8_paths_are_not_duplicated_and_collect_plugin_settings(rules, tmp_path):
    r = rules["rhino"]
    assert [p.path for p in r.paths] == [
        "%APPDATA%\\McNeel\\Rhinoceros\\*\\settings",
        "%APPDATA%\\McNeel\\Rhinoceros\\8.0\\Localization",
        "%APPDATA%\\McNeel\\Rhinoceros\\8.0\\Plug-ins\\*\\settings"]
    assert not any(p.path.endswith("settings-Scheme__Default.xml") for p in r.paths)  # settings 폴더에 이미 포함됨
    env = Env(tmp_path)
    ro = env.appdata / "McNeel" / "Rhinoceros"
    touch(ro / "8.0" / "settings" / "settings-Scheme__Default.xml")
    touch(ro / "8.0" / "Localization" / "ko.xml")
    touch(ro / "8.0" / "Plug-ins" / "Foo Plugin (1234-ABCD)" / "settings" / "s.xml")
    touch(ro / "8.0" / "Plug-ins" / "Foo Plugin (1234-ABCD)" / "other" / "o.txt")  # settings 밖
    touch(ro / "7.0" / "settings" / "a.xml")
    items, _ = collect_items(r, env.expand)
    got = arcs(items)
    assert sorted(got) == sorted([
        "data/rhino/settings/8.0/settings/settings-Scheme__Default.xml", "data/rhino/settings/7.0/settings/a.xml",
        "data/rhino/localization/Localization/ko.xml",
        "data/rhino/plugin-settings/Foo Plugin (1234-ABCD)/settings/s.xml"])
    assert len(got) == len(set(got)) and sum("settings-Scheme__Default.xml" in a for a in got) == 1


def test_qgis_collects_default_profile_only_and_excludes_cache_and_auth_db(rules, tmp_path):
    env = Env(tmp_path)
    prof = env.appdata / "QGIS" / "QGIS3" / "profiles"
    touch(prof / "default" / "QGIS" / "QGIS3.ini")
    touch(prof / "default" / "python" / "plugins" / "myplugin" / "__init__.py")
    touch(prof / "default" / "python" / "plugins" / "myplugin" / "__init__.pyc")
    touch(prof / "default" / "qgis-auth.db")
    touch(prof / "default" / "work.tmp")
    touch(prof / "default" / "x.cache")
    touch(prof / "other" / "QGIS" / "QGIS3.ini")
    items, skipped = collect_items(rules["qgis"], env.expand)
    got = arcs(items)
    # 19a: .py 는 복원 때 거부되는 형식이라 내보낼 때도 같은 판정으로 빼고(denied) 경고한다(플러그인은 QGIS 에서 다시 설치)
    assert got == ["data/qgis/profile-default/default/QGIS/QGIS3.ini"]
    assert sum("제외" in s["reason"] for s in skipped) >= 4  # auth.db, tmp, cache, pyc
    assert any(s.get("denied") and s["path"].endswith("__init__.py") for s in skipped)
    text = " ".join(rules["qgis"].manual_warnings)
    assert "플러그인" in text and "용량" in text and "qgis-auth.db" in text


# --------------------------------------------------------------------- 경고 전용 규칙 / 근거 등급 표기

@pytest.mark.parametrize("rid", WARNING_ONLY)
def test_warning_only_rules_have_no_paths_and_say_confirm_needed(rules, rid):
    r = rules[rid]
    assert r.paths == ()
    assert "확인 필요" in r.notes
    items, skipped = collect_items(r, lambda s: s)
    assert items == [] and skipped == []


@pytest.mark.parametrize("rid", CHANGED)
def test_every_new_or_changed_rule_states_evidence_grade_and_source(rules, rid):
    notes = rules[rid].notes
    assert "근거 등급" in notes, rid
    assert any(g in notes for g in ("원문", "요약", "미확인")), rid
    assert "출처" in notes, rid  # URL 이 없으면 '출처 URL: 없음' 으로 명시


def test_source_urls_given_by_researcher_are_recorded(rules):
    assert "https://docs.mcneel.com/rhino/8/help/en-us/popup_moreinformation/supportfilelocation.htm" in rules["rhino"].notes
    assert "https://www.bentley.com/en/support/subscription-entitlement-service/" in rules["microstation"].notes


def test_summary_grade_paths_are_labelled_for_reverification(rules):
    for rid in ("rhino", "autocad", "civil3d", "qgis"):
        assert "공식 원문 재확인 필요" in rules[rid].notes or "공식 문서 대조 필요" in rules[rid].notes, rid


def test_civil3d_rule_content(rules):
    r = rules["civil3d"]
    assert r.display_name_patterns == ("AutoCAD Civil 3D", "Civil 3D")
    assert [p.dest for p in r.paths] == ["civil3d/programdata/toolbox", "civil3d/programdata/survey", "civil3d/support"]
    assert "RepGenSettings.xml" in r.exclude and "*.lic" in r.exclude
    w = " ".join(r.manual_warnings)
    for kw in ("PIPECATALOGREGEN", ".ATC", "PNG", "Database Migration Utility", "2019", "CUI", "불안정", "Subassembly", "Vault",
               "Autodesk 계정", "RepGenSettings.xml", "Template Settings", "OPTIONS > Files", "Export Settings", "Migrate Custom Settings",
               "관리자 권한"):
        assert kw in w, kw
    assert "라이선스는 복원하지 않음" in w
    assert "enu" in r.notes and "확인 필요" in r.notes


def test_rhino_keeps_official_migration_guidance(rules):
    w = " ".join(rules["rhino"].manual_warnings)
    for kw in ("OptionsExport", "OptionsImport", "RUI", ".rhw", "별칭", "Rhino 5"):
        assert kw in w, kw


def test_autocad_new_warnings(rules):
    w = " ".join(rules["autocad"].manual_warnings)
    for kw in (".arg", "OPTIONS > Profiles", "SHX", "TTF", "PC3", ".ctb"):
        assert kw in w, kw
    assert "Civil 3D" in rules["autocad"].exclude_display_name_patterns


# --------------------------------------------------------------------- 공통 경고(감지된 경우에만)

def shown_ids(cl_text):
    return [i.id for i in COMMON_ITEMS if i.text in cl_text]


def checklist(registry, rule_ids=()):
    cl = build_checklist([], [Program(n) for n in registry], [Program("a.b", source="winget", winget_id="a.b")],
                         rule_ids=list(rule_ids))
    return " ".join(cl.login + cl.data)


def test_new_common_items_defined_with_patterns():
    ids = {i.id: i for i in COMMON_ITEMS}
    for cid in ("office_templates", "cad_fonts_plotters", "hwp"):
        assert cid in ids and not ids[cid].always and (ids[cid].patterns or ids[cid].rule_ids)
    assert "midas" in ids["dongle"].rule_ids and "civil3d" in ids["autodesk"].rule_ids
    assert "civil3d" in ids["cad_fonts_plotters"].rule_ids


def test_cad_font_plotter_warning_only_when_autocad_family_detected():
    assert "PC3" not in checklist(["Google Chrome"])
    for name in ("AutoCAD 2024 - English", "AutoCAD Civil 3D 2024 - English"):
        text = checklist([name])
        assert "TrueType(TTF)" in text and "PC3" in text
    assert "PC3" in checklist(["Something"], rule_ids=["civil3d"])


def test_office_template_hwp_dongle_warnings_only_when_relevant():
    assert "Normal.dotm" not in checklist(["Google Chrome"]) and "한글(HWP)" not in checklist(["Google Chrome"])
    office = checklist(["Microsoft Office Standard 2016"])
    assert "Normal.dotm" in office and "%appdata%" in office and "*.qat" in office and "Word 를 완전히 닫은 뒤" in office
    assert "한글(HWP)" in checklist(["한컴오피스 한글 2022"]) and "한글(HWP)" in checklist(["Hancom Office 2020"])
    assert "확인하지 못했습니다" in checklist(["Hancom Office 2020"])  # 위치 미확인 안내
    assert "Sentinel" not in checklist(["Google Chrome"])
    assert "Sentinel" in checklist(["Sentinel HASP Run-time"]) and "Sentinel" in checklist(["x"], rule_ids=["midas"])


def test_onedrive_redirect_warning_is_always_shown():
    text = checklist(["Google Chrome"])
    assert "리디렉션" in text and "OneDrive" in text


def test_civil3d_rule_warning_about_autodesk_account_is_not_duplicated(rules):
    pairs = [("Autodesk AutoCAD Civil 3D", list(rules["civil3d"].manual_warnings))]
    cl = build_checklist(pairs, [Program("AutoCAD Civil 3D 2024")], [Program("a.b", source="winget", winget_id="a.b")],
                         rule_ids=["civil3d"])
    joined = " ".join(cl.login + cl.data)
    assert joined.count("Autodesk 계정") == 1  # 공통 항목에만(규칙 쪽 문구는 중복이라 생략)
    assert "PIPECATALOGREGEN" in joined


def test_no_rules_for_korean_programs_without_evidence(rules):
    """지오웍스 계열/하수·배수 설계/구조해석 한국 프로그램은 근거가 없어 규칙을 만들지 않는다(discover 가 처리)."""
    for rid, r in rules.items():
        text = (r.name + " " + " ".join(r.display_name_patterns)).lower()
        for banned in ("geoworks", "지오웍스", "하수", "배수", "구조해석"):
            assert banned not in text, (rid, banned)
    assert {"ezload", "midas"} <= set(rules)  # 존재가 확인된 한국 프로그램만
