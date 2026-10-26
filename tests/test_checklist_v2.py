from conftest import make_rules_dir
import json
import re
from pathlib import Path

import pytest

from pcmigrate import cli
from pcmigrate.checklist import (build_checklist, bundle_reason, classify_programs, known_app,
                                 render)
from pcmigrate.checklist_data import (BUNDLE_PATTERNS, COMMON_ITEMS, KNOWN_APPS)
from pcmigrate.detect import Program

from test_stage4 import WINGET, patch_detection, wg


def P(name, version="", publisher=""):
    return Program(name, version, publisher)


# --------------------------------------------------------------------- B. 번들 패턴 보호

BUNDLES = [
    "AhnLab V3 Office Security", "AhnLab Office Security Agent 1.8", "LG On Screen Display 3",
    "LG Quick Guide", "LG Security Guard", "LG Smart Assistant 2.0", "LG Update",
    "Microsoft Update Health Tools", "Microsoft Visual Studio Installer",
    "Microsoft Windows Application Compatibility Fix Database",
    "Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.42.34438",
    "Microsoft Edge WebView2 Runtime", "Microsoft .NET Runtime - 8.0.0 (x64)",
    "Microsoft Windows Desktop Runtime - 8.0.0 (x64)", "Update for  (KB2504637)",
    "Security Update for Windows (KB5000000)", "Office 16 Click-to-Run Licensing Component",
]
BUSINESS = [
    "Autodesk Revit 2024", "AutoCAD 2024 - English", "Rhinoceros 8", "Lumion 2024", "Enscape",
    "SketchUp Pro 2024", "ARCHICAD 26", "Bluebeam Revu 21", "V-Ray 6 for Rhino",
    "Microsoft Visual Studio Community 2022", "Microsoft Visual Studio Code",
    "Microsoft Office Standard 2016", "Adobe Photoshop 2024", "Adobe Creative Cloud",
    "AhnLab Safe Transaction", "Java(TM) SE Development Kit 21.0.7 (64-bit)", "Windows SDK AddOn",
    "Node.js", "Git", "Google Chrome", "Python 3.12.1 (64-bit)", "Redis on Windows", "TM-calc",
    "Laplink PCmover Professional", "KakaoTalk", "Slack", "Zoom Workplace",
]


@pytest.mark.parametrize("name", BUNDLES)
def test_bundles_detected(name):
    assert bundle_reason(name)


@pytest.mark.parametrize("name", BUSINESS)
def test_business_apps_never_classified_as_bundle(name):
    assert bundle_reason(name) is None


def test_bundle_patterns_are_lowercase_and_specific():
    for pat, why in BUNDLE_PATTERNS:
        assert pat == pat.lower() and why and len(pat) >= 5, pat  # 한두 글자 패턴 금지


# --------------------------------------------------------------------- C. 대표 앱 표

ID_RE = re.compile(r"^[A-Za-z0-9+\-]+(\.[A-Za-z0-9+\-]+)+$")


def test_known_apps_table_is_well_formed():
    ids = [a.winget_id for a in KNOWN_APPS]
    assert len(ids) == len(set(ids))
    assert all(ID_RE.match(i) for i in ids)
    assert {"Google.Chrome", "Microsoft.Edge", "Mozilla.Firefox", "Microsoft.VisualStudioCode", "7zip.7zip",
            "Notepad++.Notepad++", "Zoom.Zoom", "SlackTechnologies.Slack", "Git.Git"} <= set(ids)
    names = [n for a in KNOWN_APPS for n in a.names]
    assert len(names) == len(set(n.lower() for n in names))


@pytest.mark.parametrize("name,wid", [
    ("Google Chrome", "Google.Chrome"), ("Microsoft Edge", "Microsoft.Edge"),
    ("Mozilla Firefox (x64 ko)", "Mozilla.Firefox"), ("Microsoft Visual Studio Code", "Microsoft.VisualStudioCode"),
    ("Microsoft Visual Studio Code (User)", "Microsoft.VisualStudioCode"), ("7-Zip 23.01 (x64)", "7zip.7zip"),
    ("Notepad++ (64-bit x64)", "Notepad++.Notepad++"), ("Zoom Workplace (64-bit)", "Zoom.Zoom"),
    ("Slack", "SlackTechnologies.Slack"), ("Git", "Git.Git"),
])
def test_known_app_matches_display_variants(name, wid):
    assert known_app(name).winget_id == wid


@pytest.mark.parametrize("name", ["Google Chrome Remote Desktop Host", "Chrome 원격 데스크톱", "Notepad",
                                  "Git Extensions", "Zoom Rooms", "Slack Desktop Helper", "Microsoft Edge Update",
                                  "Visual Studio Code Server"])
def test_known_app_does_not_overmatch(name):
    assert known_app(name) is None


def test_chrome_not_manual_install_whether_or_not_in_export():
    reg = [P("Google Chrome", "120", "Google LLC")]
    c1 = classify_programs(reg, wg("Google.Chrome"))
    assert [x[1] for x in c1.winget_auto] == ["Google.Chrome"] and c1.manual == [] and c1.winget_cmd == []
    c2 = classify_programs(reg, wg("Acme.Widget"))  # 내보낸 목록에는 없음 -> 개별 winget install 안내
    assert [x[1] for x in c2.winget_cmd] == ["Google.Chrome"] and c2.manual == []
    out = render(build_checklist([], reg, wg("Acme.Widget")))
    assert "winget install --id Google.Chrome --exact" in out
    assert "수동 설치(" in out and "Google Chrome — 매칭" not in out


def test_edge_goes_to_reference_not_install():
    c = classify_programs([P("Microsoft Edge")], [])
    assert c.manual == [] and c.winget_cmd == [] and "기본 포함" in c.reference[0][1]


def test_classification_buckets_for_mixed_list():
    reg = [P("LG Update"), P("Google Chrome"), P("Rhino 8"), P("Microsoft Office Standard 2016", "16.0"),
           P("Microsoft Visual Studio Community 2022")]
    c = classify_programs(reg, wg("Google.Chrome"))
    assert [p.name for p, _ in c.reference] == ["LG Update"]
    assert [p.name for p, _ in c.manual] == ["Microsoft Office Standard 2016", "Microsoft Visual Studio Community 2022",
                                              "Rhino 8"] or {p.name for p, _ in c.manual} == {
        "Rhino 8", "Microsoft Office Standard 2016", "Microsoft Visual Studio Community 2022"}


# --------------------------------------------------------------------- A. 공통 경고 필터

ALWAYS = {"gpu", "browser", "kakao_cert_finance", "onedrive", "dongle_check"}  # 19a: 동글 확인은 항상 한 줄(R2-12)


def test_common_items_data_is_consistent():
    assert {i.id for i in COMMON_ITEMS if i.always} == ALWAYS
    for i in COMMON_ITEMS:
        assert i.phase in ("login", "data") and i.title and i.text
        assert i.always or i.patterns or i.rule_ids, i.id  # 연관 패턴이 데이터로 정의되어 있음
        assert all(p == p.lower() for p in i.patterns)
    assert len({i.id for i in COMMON_ITEMS}) == len(COMMON_ITEMS)


def texts(cl):
    return " ".join(cl.login + cl.data)


def test_only_always_items_when_nothing_relevant_installed():
    cl = build_checklist([], [P("Google Chrome"), P("Microsoft Visual Studio Code")], wg("a.b"))
    t = texts(cl)
    for kw in ("브라우저", "GPU", "카카오톡", "OneDrive", "동글(하드웨어 락) 확인"):  # 동글 확인 한 줄은 항상(19a R2-12)
        assert kw in t
    for kw in ("Autodesk", "Sentinel", "Rhino", "Office", "Adobe", "Revit"):
        assert kw not in t
    assert len(cl.omitted) == len(COMMON_ITEMS) - len(ALWAYS)


def test_autodesk_items_only_when_autodesk_installed_or_rule_detected():
    cl = build_checklist([], [P("Autodesk Revit 2024")], wg("a.b"))
    assert "Autodesk 계정/버전" not in cl.omitted and "Revit 로컬 캐시" not in cl.omitted
    assert "Autodesk 계열" in texts(cl) and "Revit 로컬 캐시" in texts(cl)
    cl2 = build_checklist([], [P("Something")], wg("a.b"), rule_ids=["navisworks"])
    assert "Autodesk 계열" in texts(cl2) and "Revit 로컬 캐시" not in texts(cl2)
    cl3 = build_checklist([], [P("Something")], wg("a.b"), rule_ids=["rhino"])
    assert "Rhino / V-Ray" in texts(cl3) and "Autodesk" not in texts(cl3)


def test_dongle_detected_by_name_pattern():
    cl = build_checklist([], [P("Sentinel HASP Run-time Environment")], wg("a.b"))
    assert "동글" in texts(cl)


def test_omitted_summary_line_and_all_option():
    reg = [P("Google Chrome")]
    out = render(build_checklist([], reg, wg("a.b")))
    n = len(COMMON_ITEMS) - len(ALWAYS)
    assert f"이 PC 에 없는 프로그램 관련 경고 {n}건 생략(--all 로 모두 표시)" in out
    assert "Autodesk 계열" not in out
    cl_all = build_checklist([], reg, wg("a.b"), show_all=True)
    out_all = render(cl_all)
    assert "Autodesk 계열" in out_all and "(이 PC 에서 감지되지 않음)" in out_all
    assert "생략" not in out_all
    assert cl_all.omitted == []


def test_rule_warning_covered_by_common_item_is_not_duplicated():
    pairs = [("Autodesk Revit", ["Autodesk 계정 재로그인 및 라이선스 재활성화가 필요합니다.",
                                 "Revit 로컬 캐시는 복원하지 않습니다.", "다른 경고 라이선스 확인"])]
    cl = build_checklist(pairs, [P("Autodesk Revit 2024")], wg("a.b"), rule_ids=["revit"])
    joined = " ".join(cl.login + cl.data)
    assert joined.count("재로그인") == 1  # 공통 항목 쪽에만
    assert "다른 경고 라이선스 확인 (Autodesk Revit)" in cl.login


def test_rule_warnings_are_split_by_phase_keywords():
    cl = build_checklist([("X", ["라이선스를 재활성화하세요", "캐시를 다시 동기화하세요"])], [], wg("a.b"))
    assert any("라이선스를 재활성화" in i for i in cl.login)
    assert any("캐시를 다시" in i for i in cl.data)


# --------------------------------------------------------------------- D. 구성 / 항목별 안내

def test_sections_order_summary_and_counts():
    reg = [P("Google Chrome"), P("Rhino 8"), P("LG Update"), P("Microsoft Office Standard 2016", "16.0")]
    cl = build_checklist([], reg, wg("Acme.Widget"))
    out = render(cl)
    idx = [out.index(h) for h in ("요약", "1. 새 PC 에서 먼저 설치", "2. 로그인·인증", "3. 데이터 복사/기타", "참고")]
    assert idx == sorted(idx)
    assert f"수동 설치 {len(cl.install)}건" in out and "winget 개별 설치 1건" in out
    assert f"2. 로그인·인증: {len(cl.login)}건" in out and "참고(설치 불필요 추정): 1건" in out
    assert len(cl.install) == 2  # Rhino 8, Office


def test_office_install_item_links_to_common_warning():
    reg = [P("Microsoft Office Standard 2016", "16.0.4266.1001"), P("Rhino 8")]
    cl = build_checklist([], reg, wg("a.b"))
    office = next(i for i in cl.install if "Office" in i)
    assert "설치 미디어와 제품 키" in office and "라이선스는 복원 안 됨" in office
    assert "2단계 'Microsoft Office 인증' 참고" in office
    assert any(i.startswith("Microsoft Office") for i in cl.login)  # 2단계에도 같은 항목이 있음
    assert cl.install[0] == office  # 설치 미디어/키가 필요한 항목이 먼저
    rhino = next(i for i in cl.install if "Rhino 8" in i)
    assert "→" not in rhino


@pytest.mark.parametrize("name,kw", [("Adobe Photoshop 2024", "Adobe 계정"),
                                      ("Autodesk Navisworks Manage 2024", "Autodesk 계정")])
def test_adobe_and_autodesk_install_items_are_linked(name, kw):
    cl = build_checklist([], [P(name)], wg("a.b"))
    assert kw in cl.install[0] and "참고" in cl.install[0]


def test_reference_is_collapsed_in_md_and_text_unless_all():
    cl = build_checklist([], [P("LG Update"), P("AhnLab V3 Office Security")], wg("a.b"))
    md = render(cl, "md")
    assert "<details><summary>참고 — 설치 불필요 추정 2건" in md and "- LG Update — LG 제조사 유틸리티" in md
    text = render(cl, "text")
    assert "LG Update" not in text and "목록은 --all" in text
    full = render(build_checklist([], [P("LG Update")], wg("a.b"), show_all=True), "text")
    assert "· LG Update — LG 제조사 유틸리티" in full


def test_count_is_actionable_items_only():
    cl = build_checklist([], [P("LG Update"), P("Google Chrome")], wg("Google.Chrome"))
    assert cl.count == len(cl.install) + len(cl.winget_cmd) + len(cl.login) + len(cl.data)
    assert len(cl.winget_auto) == 1 and cl.reference


# --------------------------------------------------------------------- CLI 연동

def test_cli_checklist_all_flag_and_default(tmp_path, monkeypatch, capsys):
    patch_detection(monkeypatch, [P("Google Chrome")], WINGET)
    rules_dir = make_rules_dir(tmp_path / "rules")
    out = tmp_path / "c.md"
    assert cli.main(["checklist", "--format", "md", "--output", str(out), "--rules", str(rules_dir)]) == 0
    assert "생략(--all 로 모두 표시)" in out.read_text(encoding="utf-8")
    assert cli.main(["checklist", "--all", "--format", "md", "--output", str(out), "--rules", str(rules_dir)]) == 0
    assert "Autodesk 계열" in out.read_text(encoding="utf-8")


def test_scan_hint_counts_actionable_items(monkeypatch, tmp_path, capsys):
    patch_detection(monkeypatch, [P("Google Chrome")], WINGET)
    rules_dir = make_rules_dir(tmp_path / "rules")
    assert cli.main(["scan", "--rules", str(rules_dir)]) == 0
    err = capsys.readouterr().err
    m = re.search(r"수동 작업 (\d+)건, pcmigrate checklist", err)
    assert m and int(m.group(1)) > 0


# --------------------------------------------------------------------- E. 오류 메시지

def err_of(capsys):
    return capsys.readouterr().err


def test_output_parent_is_a_file_gives_one_line_error_not_traceback(tmp_path, monkeypatch, capsys):
    patch_detection(monkeypatch, [P("Google Chrome")], WINGET)
    blocker = tmp_path / "file.txt"
    blocker.write_text("x", encoding="utf-8")
    rules_dir = make_rules_dir(tmp_path / "rules")
    bad = str(blocker / "sub" / "c.md")
    assert cli.main(["checklist", "--output", bad, "--rules", str(rules_dir)]) == 1
    err = err_of(capsys)
    assert "출력 경로를 만들 수 없습니다:" in err and "Traceback" not in err
    assert err.strip().count("\n") <= 2  # 한 줄(+선택적 힌트)


def test_missing_drive_error_has_drive_hint(tmp_path, monkeypatch, capsys):
    patch_detection(monkeypatch, [P("Google Chrome")], WINGET)
    rules_dir = make_rules_dir(tmp_path / "rules")

    def boom(path, text):
        raise FileNotFoundError(3, "지정된 경로를 찾을 수 없습니다", "Z:\\nope\\c.md")
    monkeypatch.setattr(cli, "write_output", boom)
    assert cli.main(["checklist", "--output", "Z:\\nope\\c.md", "--rules", str(rules_dir)]) == 1
    err = err_of(capsys)
    assert "출력 경로를 만들 수 없습니다: Z:\\nope\\c.md (지정된 경로를 찾을 수 없습니다)" in err
    assert "드라이브" in err


@pytest.mark.parametrize("argv", [
    ["checklist", "--output", "%TEMP%\\x.md"],
    ["checklist", "--output", "$env:TEMP\\x.md"],
    ["export", "--output", "%TEMP%\\x.zip"],
    ["import", "x.zip", "--report", "$env:TEMP\\r.json"],
    ["copy", "a", "b", "--report", "%TEMP%\\r.json"],
    ["transfer", "a", "b", "--report", "%TEMP%\\r.json"],
    ["transfer", "a", "%TEMP%\\dst"],
])
def test_unexpanded_env_vars_rejected_with_shell_hint(argv, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(argv) == 1
    err = err_of(capsys)
    assert "출력 경로를 만들 수 없습니다:" in err and "환경변수가 확장되지 않았습니다" in err
    assert "cmd 에서는 %TEMP%" in err and "$env:TEMP" in err
    assert list(tmp_path.iterdir()) == []  # '%TEMP%' 같은 이름의 폴더/파일이 생기지 않음


def test_expanded_env_path_is_fine(tmp_path, monkeypatch):
    patch_detection(monkeypatch, [P("Google Chrome")], WINGET)
    rules_dir = make_rules_dir(tmp_path / "rules")
    out = tmp_path / "ok" / "c.md"
    assert cli.main(["checklist", "--output", str(out), "--rules", str(rules_dir)]) == 0
    assert out.exists()


def test_copy_report_write_failure_is_one_line(tmp_path, capsys):
    src = tmp_path / "s"
    src.mkdir()
    (src / "a.txt").write_text("a", encoding="utf-8")
    blocker = tmp_path / "f.txt"
    blocker.write_text("x", encoding="utf-8")
    assert cli.main(["copy", str(src), str(tmp_path / "d"), "--report", str(blocker / "r.json")]) == 1
    err = err_of(capsys)
    assert "출력 경로를 만들 수 없습니다:" in err and "Traceback" not in err


def test_transfer_dst_under_a_file_gives_error_line(tmp_path, capsys):
    src = tmp_path / "s"
    src.mkdir()
    (src / "a.txt").write_text("a", encoding="utf-8")
    blocker = tmp_path / "f.txt"
    blocker.write_text("x", encoding="utf-8")
    assert cli.main(["transfer", str(src), str(blocker / "dst")]) == 1
    err = err_of(capsys)
    assert "출력 경로를 만들 수 없습니다:" in err and "Traceback" not in err


def test_export_output_failure_is_one_line(tmp_path, monkeypatch, capsys):
    patch_detection(monkeypatch, [P("Google Chrome")], WINGET)
    blocker = tmp_path / "f.txt"
    blocker.write_text("x", encoding="utf-8")
    rules_dir = make_rules_dir(tmp_path / "rules")
    assert cli.main(["export", "--output", str(blocker / "x.zip"), "--rules", str(rules_dir)]) == 1
    err = err_of(capsys)
    assert "출력 경로를 만들 수 없습니다:" in err and "Traceback" not in err
