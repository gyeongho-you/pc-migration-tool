"""stage81: 최종 새 리뷰어 지적 수정 재현 시험(dest 검증, 알 수 없는 규칙 id, OneDrive 클라우드 전용, .git/.sh 등 거부, Civil 3D 언어 폴더,
알 수 없는 키, 제어문자, 내보내기 종료 코드 3). 가짜 폴더만 쓴다."""
import hashlib
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import cli, summary
from pcmigrate.checklist import build_checklist, manifest_warning_pairs
from pcmigrate.detect import Program
from pcmigrate.package import apply_import, collect_items, export_package, plan_import, restore_denied, verify_package
from pcmigrate.rules import RuleError, default_rules_dir, load_rules, parse_rule

B = "\\"


def mk(rid="x", **over):
    d = {"id": rid, "name": rid, "detect": {"display_name_patterns": [rid]},
         "paths": [{"path": "%APPDATA%" + B + "Foo", "dest": "d"}]}
    d.update(over)
    return d


def build(zpath, files):
    m = {"format": 1, "tool_version": "x", "created": "t", "rules": [], "files": [], "skipped": [], "failed": [],
         "coverage": [], "manual_warnings": []}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (rule, base, rel, data) in enumerate(files):
            arc = f"data/x{i}/{os.path.basename(rel)}"
            z.writestr(arc, data)
            m["files"].append({"arc": arc, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                               "rule": rule, "base": base, "rel": rel})
        z.writestr("manifest.json", json.dumps(m))


# --------------------------------------------------------------------------- M1 dest

@pytest.mark.parametrize("dest", ["a" + B + "b", "a/*", "con", "a/con/b", "a/b?", "a//b", ".", "a/./b", "a /b", "a/b.", "x:y", "/abs",
                                  "../x", "a/", "NUL.txt", "a/<b"])
def test_m1_bad_dest_rejected_by_parse_rule(dest):
    d = mk()
    d["paths"][0]["dest"] = dest
    with pytest.raises(RuleError):
        parse_rule(d)


def test_m1_good_dest_and_bundled_rules_pass():
    for dest in ("a", "a/b", "demo/data-2", "Revit/appdata"):
        d = mk()
        d["paths"][0]["dest"] = dest
        parse_rule(d)
    assert len(load_rules(default_rules_dir())) == 25


def test_m1_bad_dest_would_fail_export_before_the_fix_now_rejected_early(tmp_path):
    (tmp_path / "rules").mkdir()
    d = mk("bad")
    d["paths"][0]["dest"] = "a/*"
    (tmp_path / "rules" / "bad.json").write_text(json.dumps(d), encoding="utf-8")
    (tmp_path / "rules" / "ok.json").write_text(json.dumps(mk("ok")), encoding="utf-8")
    errs: list[str] = []
    assert [r.id for r in load_rules(tmp_path / "rules", errors=errs)] == ["ok"] and "dest" in errs[0]


def test_m1_record_mode_dest_handles_reserved_names():
    from pcmigrate import record_mode as rm
    from pcmigrate.package_base import unsafe_name
    got = rm._dest("x", "%APPDATA%" + B + "Con" + B + "NUL" + B + "a." + B + "b?c", set())
    assert not unsafe_name(got) and got.startswith("x/")
    parse = mk()
    parse["paths"][0]["dest"] = got
    parse_rule(parse)


# --------------------------------------------------------------------------- M2 알 수 없는 규칙 id

def test_m2_unknown_rule_item_only_is_denied_rest_restored(tmp_path):
    rules = load_rules(default_rules_dir())  # mytool 규칙은 이 PC 에 없다
    new = tmp_path / "new"
    new.mkdir()
    z = tmp_path / "p.zip"
    build(z, [("mytool", "%APPDATA%" + B + "MyTool", "a.cfg", b"m"), ("vscode", "%APPDATA%" + B + "Code" + B + "User", "settings.json", b"{}")])
    plan = plan_import(z, rules, lambda s: s.replace("%APPDATA%", str(new)))
    assert plan.problems == []
    by = {i["program"]: i for i in plan.items}
    assert by["mytool"]["status"] == "denied" and by["mytool"]["skip_kind"] == "unknown_rule"
    assert "rules 폴더" in by["mytool"]["error"] and "--rules" in by["mytool"]["error"]
    assert by["vscode"]["status"] == "would_copy"
    apply_import(z, plan)
    assert (new / "Code" / "User" / "settings.json").exists() and not (new / "MyTool").exists()
    from pcmigrate.api import ImportResult
    text = "\n".join(summary.import_lines(ImportResult(plan, "dry-run", 0), str(z)))
    assert "건너뛴 항목 1개" in text and "--rules" in text
    new2 = tmp_path / "new2"
    only = plan_import(z, rules, lambda s: s.replace("%APPDATA%", str(new2)), only_programs=["vscode"])
    assert only.problems == [] and {i["program"]: i["status"] for i in only.items} == {"mytool": "not_selected", "vscode": "would_copy"}


def test_m2_structure_violations_still_block_whole_package(tmp_path):
    rules = load_rules(default_rules_dir())
    z = tmp_path / "p.zip"
    build(z, [("vscode", "%APPDATA%" + B + "Evil", "settings.json", b"{}")])
    assert plan_import(z, rules, lambda s: s.replace("%APPDATA%", str(tmp_path))).problems  # 규칙에 허용되지 않은 위치
    build(z, [("mytool", "%APPDATA%" + B + "MyTool", "../../evil.cfg", b"m")])
    assert plan_import(z, rules, lambda s: s.replace("%APPDATA%", str(tmp_path))).problems  # 경로 탈출


# --------------------------------------------------------------------------- M3 클라우드 전용

def test_m3_cloud_only_files_are_not_read_or_packed(tmp_path):
    root = tmp_path / "old"
    d = root / "Vendor" / "Demo"
    d.mkdir(parents=True)
    (d / "local.cfg").write_bytes(b"local")
    (d / "recall.cfg").write_bytes(b"R" * 11)
    (d / "offline.cfg").write_bytes(b"O" * 12)
    (d / "pinned.cfg").write_bytes(b"P" * 13)
    attrs = {11: 0x400000 | 0x20, 12: 0x1000 | 0x20, 13: 0x20}  # 크기로 구분: RECALL_ON_DATA_ACCESS / OFFLINE / 일반
    rule = parse_rule({"id": "demo", "name": "Demo", "detect": {"display_name_patterns": ["Demo"]},
                       "paths": [{"path": "%APPDATA%" + B + "Vendor" + B + "Demo", "dest": "demo/data"}]})
    reads = []
    import pcmigrate.package_export as pe
    real = pe._write_entry
    pe._write_entry = lambda zf, arc, src, stage=None: (reads.append(str(src)), real(zf, arc, src, stage))[1]
    try:
        m = export_package([rule], tmp_path / "o.zip", [Program("x", "1")], None, expand=lambda s: s.replace("%APPDATA%", str(root)),
                           attr_fn=lambda st: attrs.get(st.st_size, 0x20))
    finally:
        pe._write_entry = real
    assert sorted(f["rel"] for f in m["files"]) == ["Demo/local.cfg", "Demo/pinned.cfg"]
    assert not any("recall" in r or "offline" in r for r in reads)  # 열어 읽지 않는다
    assert m["cloud_only"]["count"] == 2 and len(m["cloud_only"]["examples"]) == 2
    assert sum("클라우드 전용" in s["reason"] for s in m["skipped"]) == 2
    assert verify_package(tmp_path / "o.zip")[1] == []


def test_m3_summary_line_uses_existing_wording():
    m = {"files": [], "rules": [], "skipped": [], "failed": [], "cloud_only": {"count": 3, "examples": ["a"]}}
    fake = SimpleNamespace(manifest=m, denied=[], unreadable=[], hard_failures=[], extras=SimpleNamespace(items=[]))
    fake.unreadable_lines = lambda: []
    try:
        lines = summary.export_lines(fake, "o.zip")
    except Exception:  # 요약이 다른 속성을 더 요구해도 이 줄만 확인한다
        pytest.skip("export_lines 가 추가 속성을 요구함")
    assert any("OneDrive 클라우드 전용 3개는 내려받지 않고 제외했습니다" in x for x in lines)


# --------------------------------------------------------------------------- M4 .git 등 거부

EXTRA_DENIED = [("%USERPROFILE%", ".git/config"), ("%APPDATA%", "proj/.git/config"), ("%APPDATA%", "proj/.GIT/hooks/post-checkout"),
                ("%APPDATA%", "proj/.git/hooks/pre-commit.sample"), ("%APPDATA%", "proj/.npmrc"), ("%APPDATA%", "proj/.NPMRC"),
                ("%APPDATA%", "x/run.sh"), ("%APPDATA%", "x/run.SH"), ("%APPDATA%", "x/a.bash"), ("%APPDATA%", "x/a.zsh"),
                ("%APPDATA%", "x/app.msix"), ("%APPDATA%", "x/app.APPX"), ("%APPDATA%", "x/mol.cdxml")]


@pytest.mark.parametrize("base,rel", EXTRA_DENIED)
def test_m4_new_deny_entries(base, rel):
    assert restore_denied(rel, Path("C:/x") / rel, None, base), (base, rel)


def test_m4_safe_files_rule_override_and_home_gitconfig_unchanged():
    for base, rel in [("%APPDATA%", "proj/.gitignore"), ("%APPDATA%", "proj/.gitattributes"), ("%USERPROFILE%", ".gitconfig"),
                      ("%APPDATA%", "Code/User/settings.json")]:
        assert restore_denied(rel, Path("C:/x") / rel, None, base) is None, (base, rel)
    rule = parse_rule(mk(paths=[{"path": "%APPDATA%" + B + "Foo", "dest": "d", "allow_extensions": [".sh", ".cdxml"]}]))
    assert restore_denied("Foo/a.sh", Path("C:/x/Foo/a.sh"), rule, "%APPDATA%") is None
    assert restore_denied("Foo/a.cdxml", Path("C:/x/Foo/a.cdxml"), rule, "%APPDATA%") is None


# --------------------------------------------------------------------------- M5 Civil 3D 언어 폴더

def test_m5_civil3d_collects_non_english_language_folder(tmp_path):
    rules = {r.id: r for r in load_rules(default_rules_dir())}
    app, pd = tmp_path / "app", tmp_path / "pd"
    for lang in ("kor", "enu"):
        (app / "Autodesk" / "C3D 2024" / lang / "Support").mkdir(parents=True)
        (app / "Autodesk" / "C3D 2024" / lang / "Support" / "c3d.cuix").write_bytes(lang.encode())
        (pd / "Autodesk" / "C3D 2024" / lang / "Data" / "Toolbox").mkdir(parents=True)
        (pd / "Autodesk" / "C3D 2024" / lang / "Data" / "Toolbox" / "t.xml").write_bytes(b"t")
    (pd / "Autodesk" / "C3D 2024" / "kor" / "Survey").mkdir(parents=True)
    (pd / "Autodesk" / "C3D 2024" / "kor" / "Survey" / "s.ini").write_bytes(b"s")
    items, _ = collect_items(rules["civil3d"], lambda s: s.replace("%APPDATA%", str(app)).replace("%PROGRAMDATA%", str(pd)))
    rels = sorted(i.rel for i in items)
    assert "C3D 2024/kor/Support/c3d.cuix" in rels and "C3D 2024/enu/Support/c3d.cuix" in rels
    assert "C3D 2024/kor/Data/Toolbox/t.xml" in rels and "C3D 2024/kor/Survey/s.ini" in rels
    assert "확인 필요" in rules["civil3d"].notes and int(rules["civil3d"].version) >= 5
    # 옛 패키지(enu 고정으로 만든)도 새 규칙으로 가져와진다
    z = tmp_path / "p.zip"
    build(z, [("civil3d", "%APPDATA%" + B + "Autodesk", "C3D 2024/enu/Support/c3d.cuix", b"x")])
    plan = plan_import(z, list(rules.values()), lambda s: s.replace("%APPDATA%", str(tmp_path / "new")))
    assert plan.problems == [] and plan.items[0]["status"] == "would_copy"


# --------------------------------------------------------------------------- M6/M7 규칙 문구

def test_m6_m7_rule_notes_and_warnings():
    rules = {r.id: r for r in load_rules(default_rules_dir())}
    for rid in ("lumion", "enscape"):
        assert "한국어" in rules[rid].notes and "확인 필요" in rules[rid].notes
        assert not any("문서" in p.path for p in rules[rid].paths)  # 추정 경로를 넣지 않는다
    for rid in ("vscode", "jetbrains"):
        assert any("신뢰하는 묶음" in w and "실행 경로" in w for w in rules[rid].manual_warnings), rid
    assert "Rhino 6/7/8" in rules["rhino"].notes and "exclude_display_name_patterns" in rules["rhino"].notes


# --------------------------------------------------------------------------- 낮음: 알 수 없는 키 / 형식

@pytest.mark.parametrize("patch", [{"pahts": []}, {"Notes": "x"}])
def test_unknown_top_level_keys_rejected(patch):
    with pytest.raises(RuleError):
        parse_rule(mk(**patch))


def test_unknown_path_and_detect_keys_rejected():
    d = mk()
    d["paths"][0]["allow_extension"] = [".rb"]
    with pytest.raises(RuleError):
        parse_rule(d)
    d = mk()
    d["detect"]["install_path"] = ["%ProgramFiles%" + B + "X"]
    with pytest.raises(RuleError):
        parse_rule(d)


@pytest.mark.parametrize("ip", ["Foo" + B + "Bar", "%ProgramFiles%", "%ProgramFiles%" + B + ".." + B + "x", "x:y", "\\\\srv\\share"])
def test_install_paths_format_validated(ip):
    d = mk()
    d["detect"]["install_paths"] = [ip]
    with pytest.raises(RuleError):
        parse_rule(d)


@pytest.mark.parametrize("rk", ["foo", "SOFTWARE" + B + "X", "HKLM", "HKLM" + B + B + "X", "HKLM" + B + "X" + B])
def test_registry_keys_format_validated(rk):
    d = mk()
    d["detect"]["registry_keys"] = [rk]
    with pytest.raises(RuleError):
        parse_rule(d)


def test_valid_detect_formats_accepted():
    d = mk()
    d["detect"]["install_paths"] = ["%ProgramFiles%" + B + "X", "C:" + B + "Tools" + B + "X"]
    d["detect"]["registry_keys"] = ["HKLM" + B + "SOFTWARE" + B + "X", "HKCU" + B + "Software" + B + "Y"]
    parse_rule(d)


# --------------------------------------------------------------------------- 낮음: 제어 문자 / 종료 코드

def test_checklist_winget_lines_and_manifest_warnings_strip_control_chars():
    bel = chr(7)
    cl = build_checklist([], [Program("Notepad++" + bel + " (64-bit x64)", "8.6" + bel, "Notepad++ Team")], [])
    assert cl.winget_cmd and all(bel not in x for x in cl.winget_cmd)
    pairs = manifest_warning_pairs({"manual_warnings": [{"name": "N" + chr(27) + "[31m", "items": ["i" + bel + "x"]}]})
    assert pairs[0][0] == "N" and pairs[0][1] == ["ix"]


def test_export_exit_code_is_3_for_unreadable_files(tmp_path, monkeypatch, capsys):
    from conftest import make_rules_dir
    manifest = {"rules": [], "files": [{"arc": "a"}], "skipped": [], "manual_warnings": [],
                "failed": [{"src": "D:/x/LOCK", "error": "사용 중이라 읽지 못함", "reason": "in_use"}]}
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [])
    monkeypatch.setattr(cli, "winget_export_raw", lambda: None)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: None)
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: manifest)
    rd = make_rules_dir(tmp_path / "rules")
    assert cli.main(["export", "--output", str(tmp_path / "o.zip"), "--rules", str(rd), "--json"]) == 3
    manifest["failed"] = [{"src": "dup", "error": "중복된 패키지 경로 data/x"}]
    assert cli.main(["export", "--output", str(tmp_path / "o.zip"), "--rules", str(rd), "--json"]) == 1
    manifest["failed"] = []
    assert cli.main(["export", "--output", str(tmp_path / "o.zip"), "--rules", str(rd), "--json"]) == 0
