import json
from pathlib import Path

import pytest
from conftest import patch_xfer

from pcmigrate import cli, transfer as tr
from pcmigrate.checklist_data import COMMON_ITEMS
from pcmigrate.checklist import (CERTAIN, PROBABLE, match_program, parse_winget_list, build_checklist, find_manual_installs,
                                 render, winget_matches)
from pcmigrate.copier import MISMATCH, VERIFIED
from pcmigrate.detect import Program
from pcmigrate.package import plan_import
from pcmigrate.transfer import STATE_NAME, transfer

from test_transfer import FakeRobocopy, make_src


# --------------------------------------------------------------------- A. --strict

def same_size_tamper(path: Path, new: bytes):
    """크기와 mtime 을 그대로 두고 내용만 바꾼다."""
    st = path.stat()
    assert len(new) == st.st_size
    path.write_bytes(new)
    import os
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_default_resume_trusts_state_but_strict_catches_and_repairs_same_size_tamper(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    transfer(src, dst, runner=FakeRobocopy())
    target = dst / "a.dwg"
    original = (src / "a.dwg").read_bytes()
    same_size_tamper(target, b"Z" * target.stat().st_size)

    rep = transfer(src, dst, runner=FakeRobocopy(skip=["a.dwg"]))  # 기본: 상태 신뢰
    assert rep.failures == [] and rep.meta["strict"] is False  # 알려진 한계(문서화), 기본 동작 유지
    assert target.read_bytes() != original

    rep = transfer(src, dst, runner=FakeRobocopy(skip=["a.dwg"]), strict=True)  # robocopy 는 건너뛰어도 strict 가 복구
    st = {Path(r.src).name: r.status for r in rep.results}
    assert st["a.dwg"] == VERIFIED and st["b.rvt"] == VERIFIED and rep.failures == []
    assert rep.meta["strict"] is True and rep.meta["resumed_skipped"] == 0
    assert target.read_bytes() == original and (src / "a.dwg").read_bytes() == original
    assert [Path(r["file"]).name for r in rep.meta["repaired"]] == ["a.dwg"]
    assert (dst / "a.dwg.bak").read_bytes() == b"Z" * len(original)  # 손상된 사본은 .bak 보존
    state = tr.read_state_entries(dst / STATE_NAME)
    assert state["a.dwg"]["verified"] is True
    rep2 = transfer(src, dst, runner=FakeRobocopy(skip=["a.dwg"]), strict=True)  # 재실행은 깨끗
    assert rep2.failures == [] and "repaired" not in rep2.meta


def test_strict_rehashes_every_file(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    transfer(src, dst, runner=FakeRobocopy())
    calls = []
    real = tr.verify_file
    patch_xfer(monkeypatch, "verify_file", lambda s, d, **kw: (calls.append(s), real(s, d, **kw))[1])
    rep = transfer(src, dst, runner=FakeRobocopy(), strict=True)
    assert len(calls) == 3 and rep.failures == []


def test_strict_reports_state_vs_source_conflict(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    transfer(src, dst, runner=FakeRobocopy())
    # 원본과 대상 모두 같은 크기/mtime 으로 내용이 바뀜: 해시는 서로 같지만 상태 파일 기록과 어긋남
    for p in (src / "a.dwg", dst / "a.dwg"):
        same_size_tamper(p, b"Q" * p.stat().st_size)
    rep = transfer(src, dst, runner=FakeRobocopy(skip=["a.dwg"]), strict=True)
    r = next(r for r in rep.results if r.src.endswith("a.dwg"))
    assert r.status == MISMATCH and "상태 파일" in r.error


def test_cli_strict_flag(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    rc = FakeRobocopy()
    seen = {}

    def fake(*a, **k):
        seen.update(k)
        return tr.transfer(*a, runner=rc, **{x: y for x, y in k.items()})
    monkeypatch.setattr(cli, "transfer", fake)
    assert cli.main(["transfer", str(src), str(tmp_path / "d"), "--strict"]) == 0
    assert seen["strict"] is True


# --------------------------------------------------------------------- C. winget 비교

@pytest.mark.parametrize("name,wid,ok", [
    ("Google Chrome", "Google.Chrome", True),
    ("Microsoft Visual Studio Code", "Microsoft.VisualStudioCode", True),
    ("7-Zip 23.01 (x64)", "7zip.7zip", True),
    ("Autodesk Revit 2024", "Google.Chrome", False),
])
def test_winget_matches(name, wid, ok):
    assert winget_matches(name, wid) is ok


def test_find_manual_installs_filters_matches_and_noise():
    registry = [Program("Google Chrome", "1"), Program("Rhino 8", "8.0"), Program("Acme Widget", "1", "Acme Inc"),
                Program("Microsoft Visual C++ 2015-2022 Redistributable (x64)"),
                Program("Security Update for Windows (KB5000000)"), Program("Rhino 8", "8.0")]
    winget = wg("Google.Chrome", "Acme.Widget")
    # Chrome 은 대표 앱 표, 재배포/업데이트는 참고로 빠지고, winget list 없이 Acme 는 '추정'으로 남는다
    res = find_manual_installs(registry, winget)
    assert [(p.name, m.grade if m else None) for p, m in res] == [("Rhino 8", None), ("Acme Widget", PROBABLE)]


# --------------------------------------------------------------------- B. 체크리스트

def test_checklist_dedups_and_merges_sources():
    pairs = [("Revit", ["라이선스 재로그인", "캐시 재동기화"]), ("AutoCAD", ["라이선스 재로그인"])]
    cl = build_checklist(pairs, [Program("Rhino 8")], wg("a.b"))
    assert "라이선스 재로그인 (Revit, AutoCAD)" in cl.login
    assert "캐시 재동기화 (Revit)" in cl.data
    assert cl.install == ["Rhino 8 — 매칭 근거: 없음"]


def test_common_warnings_cover_claude_md_items():
    text = " ".join(i.text for i in COMMON_ITEMS)
    for kw in ["Autodesk", "Revit 로컬 캐시", "동글", "Rhino", "V-Ray", "Enscape", "Lumion", "ArchiCAD",
               "GPU", "DPAPI", "카카오톡", "공인인증서", "금융보안", "OneDrive", "Office", "Adobe"]:
        assert kw in text


@pytest.mark.parametrize("fmt", ["text", "md"])
def test_render_has_checkboxes_sections_and_winget_guide(fmt):
    cl = build_checklist([("Revit", ["w1"])], [Program("Rhino 8")], wg("a.b"))
    out = render(cl, fmt)
    assert "- [ ] w1 (Revit)" in out and "- [ ] Rhino 8 — 매칭 근거: 없음" in out
    assert "1. 새 PC에서 먼저 설치" in out and "--ignore-unavailable" in out
    assert "자동 실행하지 않습니다" in out


def test_render_without_winget_list_says_so():
    out = render(build_checklist([], [Program("Rhino 8")], None))
    assert "winget 목록이 없어" in out and "Rhino 8" in out  # 비교는 못 해도 설치 항목은 남긴다


def patch_detection(monkeypatch, registry, winget_text, list_text=None):
    monkeypatch.setattr(cli, "read_registry_programs", lambda: registry)
    monkeypatch.setattr(cli, "winget_export_raw", lambda: winget_text)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: list_text)  # 실제 winget 호출 금지


RULES_DIR_JSON = {"id": "demo", "name": "Demo", "detect": {"display_name_patterns": ["Demo"]},
                  "manual_warnings": ["데모 재로그인"]}


@pytest.fixture
def rules_dir(tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "demo.json").write_text(json.dumps(RULES_DIR_JSON), encoding="utf-8")
    return d


WINGET = json.dumps({"Sources": [{"Packages": [{"PackageIdentifier": "Google.Chrome"}]}]})


def test_cli_checklist_output_file_with_bak(tmp_path, monkeypatch, rules_dir):
    list_text = "Name           Id            Version\n---\nGoogle Chrome  Google.Chrome  120.0\n"
    patch_detection(monkeypatch, [Program("Demo App"), Program("Google Chrome"), Program("Rhino 8")],
                    WINGET, list_text)
    out = tmp_path / "check.md"
    out.write_text("old", encoding="utf-8")
    assert cli.main(["checklist", "--format", "md", "--output", str(out), "--rules", str(rules_dir)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "- [ ] 데모 재로그인 (Demo)" in text and "- [ ] Rhino 8 — 매칭 근거: 없음" in text
    assert "Google Chrome" not in text
    assert (tmp_path / "check.md.bak").read_text(encoding="utf-8") == "old"


def test_cli_checklist_stdout(tmp_path, monkeypatch, rules_dir, capsys):
    patch_detection(monkeypatch, [Program("Demo App")], None)
    assert cli.main(["checklist", "--rules", str(rules_dir)]) == 0
    out = capsys.readouterr().out
    assert "[ ] 데모 재로그인" in out and "winget 목록이 없어" in out


def test_scan_prints_hint(monkeypatch, rules_dir, capsys):
    patch_detection(monkeypatch, [Program("Demo App")], WINGET)
    assert cli.main(["scan", "--rules", str(rules_dir)]) == 0
    err = capsys.readouterr().err
    assert "수동 작업" in err and "pcmigrate checklist" in err


def test_export_prints_hint_and_import_dry_run_shows_manifest_warnings(tmp_path, monkeypatch, rules_dir, capsys):
    base = tmp_path / "old" / "Vendor"
    base.mkdir(parents=True)
    (base / "prefs.json").write_text("{}", encoding="utf-8")
    rule = {"id": "demo", "name": "Demo", "detect": {"display_name_patterns": ["Demo"]},
            "paths": [{"path": "%ROAM%\\Vendor\\prefs.json", "dest": "demo/prefs.json", "kind": "file"}],
            "manual_warnings": ["데모 재로그인"]}
    (rules_dir / "demo.json").write_text(json.dumps(rule), encoding="utf-8")
    patch_detection(monkeypatch, [Program("Demo App")], WINGET)
    old_expand = lambda s: s.replace("%ROAM%", str(tmp_path / "old"))
    new_expand = lambda s: s.replace("%ROAM%", str(tmp_path / "new"))
    real_export = cli.export_package
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: real_export(*a, expand=old_expand, **k))
    pkg = tmp_path / "pkg.zip"
    assert cli.main(["export", "--output", str(pkg), "--rules", str(rules_dir)]) == 0
    assert "pcmigrate checklist" in capsys.readouterr().err

    monkeypatch.setattr(cli, "plan_import", lambda p, r: plan_import(p, r, new_expand))
    assert cli.main(["import", str(pkg), "--dry-run", "--rules", str(rules_dir)]) == 0
    err = capsys.readouterr().err
    assert "데모 재로그인" in err  # manifest 경고가 dry-run 미리보기에 그대로 표시됨

    # --package 기반 체크리스트(신 PC)
    out = tmp_path / "c.txt"
    assert cli.main(["checklist", "--package", str(pkg), "--output", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "데모 재로그인" in text and "Demo App" in text


def test_checklist_package_rejects_bad_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"nope")
    assert cli.main(["checklist", "--package", str(bad)]) == 1


# --------------------------------------------------------------------- C'. 매칭 근거 등급

LIST_TEXT = """\
Name                         Id                          Version      Source
-------------------------------------------------------------------------------
Google Chrome                Google.Chrome               120.0.1      winget
Microsoft Visual Studio Code Microsoft.VisualStudioCode  1.90.0       winget
7-Zip                        7zip.7zip                   23.01        winget
카카오톡                     Kakao.KakaoTalk             3.5.1        winget
Visual Studio Community 20…  Microsoft.VisualStudio.2022.Community  17.9  winget
"""
IDS = ["Google.Chrome", "Microsoft.VisualStudioCode", "7zip.7zip", "Kakao.KakaoTalk",
       "Microsoft.VisualStudio.2022.Community"]


def wg(*ids):
    return [Program(i, source="winget", winget_id=i) for i in ids]


def section_of(text):
    return text.split("수동 설치(")[1].split("2. 로그인")[0]


def test_parse_winget_list_links_names_without_column_widths():
    links = parse_winget_list(LIST_TEXT, IDS)
    assert links["Google.Chrome"].name == "Google Chrome" and links["Google.Chrome"].version == "120.0.1"
    assert links["Kakao.KakaoTalk"].name == "카카오톡"  # 한글 폭/정렬과 무관
    vs = links["Microsoft.VisualStudio.2022.Community"]
    assert vs.truncated and vs.name == "Visual Studio Community 20"
    assert "7zip.7zip" in links and links["7zip.7zip"].version == "23.01"


def test_certain_matches_via_winget_list_are_excluded():
    registry = [Program("Google Chrome", "120.0.1", "Google LLC"),
                Program("Microsoft Visual Studio Code", "1.90.0", "Microsoft Corporation"),
                Program("7-Zip 23.01 (x64)", "23.01", "Igor Pavlov"),
                Program("Visual Studio Community 2022", "17.9", "Microsoft"),
                Program("Autodesk Revit 2024", "24.0", "Autodesk")]
    res = find_manual_installs(registry, wg(*IDS), LIST_TEXT)
    assert [p.name for p, _ in res] == ["Autodesk Revit 2024"]  # Autodesk 제품은 winget 근거 없음
    assert res[0][1] is None


def test_similar_app_is_not_certain_match():
    # Google.Chrome 이 설치돼 있어도 '이름이 비슷한 다른 앱'을 확실 매칭하면 안 된다
    registry = [Program("Google Chrome", "120.0.1", "Google LLC"),
                Program("Chrome Remote Desktop Host", "154.0", "Google LLC"),
                Program("Google Chrome Remote Desktop", "1.0", "Google LLC")]
    res = {p.name: m for p, m in find_manual_installs(registry, wg("Google.Chrome"), LIST_TEXT)}
    assert "Google Chrome" not in res
    assert res["Chrome Remote Desktop Host"] is None  # 근거 없음
    assert res["Google Chrome Remote Desktop"].strength == 1  # 약한 조각 포함
    assert res["Google Chrome Remote Desktop"].grade == PROBABLE


def test_version_mismatch_is_not_certain():
    text = "Oracle JDK 21   Oracle.JDK.21   21.0.7   winget\n"
    link = parse_winget_list(text, ["Oracle.JDK.21"])["Oracle.JDK.21"]
    jdk24 = Program("Oracle JDK 24 (64-bit)", "24.0.1")
    m = match_program(jdk24, ["Oracle.JDK.21"], {"Oracle.JDK.21": link})
    assert m is None or m.grade != CERTAIN


def test_publisher_product_probable_and_fallback_without_list():
    registry = [Program("Acme Widget", "1", "Acme Inc"), Program("Foo Bar Pro 2", "2", "Foo"),
                Program("Zed Studio", "1", "Zed Corp"), Program("Acme Widget Server", "1", "Acme Inc")]
    ids = ["Acme.Widget", "Foo.BarPro2", "Zed.Studio"]
    res = {p.name: m for p, m in find_manual_installs(registry, wg(*ids), None)}  # winget list 없음
    assert all(m is None or m.grade != CERTAIN for m in res.values())  # 확실 판정 불가
    assert res["Acme Widget"].grade == PROBABLE and res["Acme Widget"].strength == 2
    assert res["Zed Studio"].strength == 2
    assert res["Acme Widget Server"].strength == 1  # 약한 조각 포함


def test_company_suffixes_ignored_in_publisher_compare():
    p = Program("Acme Widget", "1", "ACME Inc.")
    assert match_program(p, ["Acme.Widget"], {}).strength == 2
    p2 = Program("Widget", "1", "Acme Corp")
    assert match_program(p2, ["Acme.Widget"], {}).strength == 2  # 이름이 제품명과 같음


def test_render_shows_grades_and_hint_when_list_missing():
    reg = [Program("Acme Widget", "1", "Acme Inc"), Program("Rhino 8")]
    out = render(build_checklist([], reg, wg("Acme.Widget"), None))
    assert "매칭 근거: 추정" in out and "매칭 근거: 없음" in out
    assert "'확실' 판정을 하지 못했습니다" in out
    out2 = render(build_checklist([], reg, wg("Acme.Widget"), "Acme Widget  Acme.Widget  1  winget\n"))
    assert "Acme Widget" not in section_of(out2)
    assert "판정을 하지 못했습니다" not in out2


def test_winget_list_raw_injected_runner_and_failure():
    from pcmigrate.detect import winget_list_raw
    seen = []

    def ok(args):
        seen.append(args)
        return 0, "Name Id\nX  A.B\n"

    assert winget_list_raw(ok) is not None
    assert "--disable-interactivity" in seen[0] and "--source" in seen[0] and "winget" in seen[0]
    assert winget_list_raw(lambda a: (1, "x")) is None
    assert winget_list_raw(lambda a: (0, "  \n")) is None

    def boom(a):
        raise FileNotFoundError("winget")
    assert winget_list_raw(boom) is None


def test_package_roundtrip_keeps_winget_list_for_checklist(tmp_path):
    from pcmigrate.checklist import load_package_programs
    from pcmigrate.package import export_package
    out = tmp_path / "p.zip"
    export_package([], out, [Program("Google Chrome", "1", "Google LLC")], WINGET,
                   winget_list_text="Google Chrome  Google.Chrome  1  winget\n")
    reg, wgt, text = load_package_programs(out)
    assert text and wgt[0].winget_id == "Google.Chrome"
    assert cli.main(["checklist", "--package", str(out), "--output", str(tmp_path / "c.txt")]) == 0
    assert "Google Chrome" not in section_of((tmp_path / "c.txt").read_text(encoding="utf-8"))
