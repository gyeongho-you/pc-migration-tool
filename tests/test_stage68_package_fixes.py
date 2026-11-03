"""stage68: 설정 패키지/규칙/설치 계열 독립 리뷰 지적 수정 재현 시험(H1, M1~M7, L1~L6). 가짜 폴더만 쓴다."""
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

import pytest

from conftest import patch_pkg
from pcmigrate import api, cli, envpaths, import_record, summary
from pcmigrate.cancel import Cancelled
from pcmigrate.package import PackageError, apply_import, plan_import, verify_package
from pcmigrate.rules import RuleError, load_rules, parse_rule

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"


def make_rule(**over):
    data = {"id": "demo", "name": "Demo", "version": "1", "detect": {"display_name_patterns": ["Demo"]},
            "paths": [{"path": "%APPDATA%\\Vendor\\Demo", "dest": "demo/data", "kind": "dir"},
                      {"path": "%OneDrive%\\Documents\\Demo", "dest": "demo/od", "kind": "dir"}],
            "manual_warnings": [], "exclude": ["secret.db"]}
    data.update(over)
    return parse_rule(data)


def build(zpath, files, programs=None, manifest_extra=None, mtime=None):
    """files: [(rule, base, rel, bytes)] → 패키지 zip. programs: {entry: bytes}"""
    m = {"format": 1, "tool_version": "x", "created": "t", "rules": [], "files": [], "skipped": [], "failed": [],
         "coverage": [], "manual_warnings": []}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (rule, base, rel, data) in enumerate(files):
            arc = f"data/x{i}/{os.path.basename(rel)}"
            zi = zipfile.ZipInfo(arc, date_time=mtime or (2020, 5, 6, 7, 8, 10))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
            m["files"].append({"arc": arc, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                               "rule": rule, "base": base, "rel": rel})
        for n, d in (programs or {}).items():
            z.writestr(n, d)
        m.update(manifest_extra or {})
        z.writestr("manifest.json", json.dumps(m))
    return m


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "new"
    root.mkdir()

    def expand(s):  # %OneDrive% 는 일부러 확장하지 않는다(OneDrive 없는 신 PC)
        return s.replace("%APPDATA%", str(root))
    return tmp_path, root, expand


# --------------------------------------------------------------------------- H1

def test_h1_env_unexpandable_item_is_denied_not_blocking(env):
    tmp, root, expand = env
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a"), ("demo", "%OneDrive%\\Documents", "Demo/b.cfg", b"b")])
    plan = plan_import(z, [make_rule()], expand)
    assert plan.problems == []  # 패키지 전체가 막히지 않는다
    st = {Path(i["entry"]).name: i for i in plan.items}
    assert st["a.cfg"]["status"] == "would_copy"
    assert st["b.cfg"]["status"] == "denied" and st["b.cfg"]["skip_kind"] == "env" and "--env" in st["b.cfg"]["error"]
    rep = apply_import(z, plan)
    assert (root / "Vendor" / "Demo" / "a.cfg").read_bytes() == b"a"
    assert {r.status for r in rep.results} == {"copied", "denied"}


def test_h1_rule_excluded_file_of_old_package_is_denied(env):
    tmp, root, expand = env
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a"), ("demo", "%APPDATA%\\Vendor", "Demo/secret.db", b"s"),
              ("demo", "%APPDATA%\\Vendor", "Demo/x.lic", b"l")])
    plan = plan_import(z, [make_rule()], expand)
    assert plan.problems == []
    by = {Path(i["entry"]).name: i for i in plan.items}
    assert by["a.cfg"]["status"] == "would_copy"
    assert by["secret.db"]["status"] == by["x.lic"]["status"] == "denied" and by["secret.db"]["skip_kind"] == "excluded"
    apply_import(z, plan)
    assert not (root / "Vendor" / "Demo" / "secret.db").exists() and (root / "Vendor" / "Demo" / "a.cfg").exists()


def test_h1_not_selected_still_works_for_env_item(env):
    tmp, _, expand = env
    z = tmp / "p.zip"
    build(z, [("demo", "%OneDrive%\\Documents", "Demo/b.cfg", b"b")])
    plan = plan_import(z, [make_rule()], expand, skip_programs=["demo"])
    assert plan.problems == [] and plan.items[0]["status"] == "not_selected"


def test_h1_structure_violations_still_block_whole_package(env):
    tmp, _, expand = env
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a"), ("nope", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a")])
    p0 = plan_import(z, [make_rule()], expand)  # stage81: 알 수 없는 규칙 id 는 그 항목만 denied(unknown_rule), 패키지 전체는 막지 않는다
    assert p0.problems == [] and [i.get("skip_kind") for i in p0.items] == [None, "unknown_rule"]
    build(z, [("demo", "%APPDATA%\\Evil", "Demo/a.cfg", b"a")])
    assert plan_import(z, [make_rule()], expand).problems  # 허용 외 위치
    build(z, [("extra", "%WINDIR%", "x.cfg", b"a")])
    assert plan_import(z, [make_rule()], expand).problems  # 허용 외 루트
    build(z, [("demo", "%APPDATA%\\Vendor", "Other/a.cfg", b"a")])
    assert plan_import(z, [make_rule()], expand).problems  # 규칙 패턴 밖


def test_h1_summary_lists_skipped_items_with_reason(env):
    tmp, _, expand = env
    z = tmp / "p.zip"
    build(z, [("demo", "%OneDrive%\\Documents", "Demo/b.cfg", b"b"), ("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a")])
    plan = plan_import(z, [make_rule()], expand)
    res = api.ImportResult(plan, "dry-run", 0)
    text = "\n".join(summary.import_lines(res, str(z)))
    assert "건너뛴 항목 1개" in text and "환경변수" in text and "보안상 복원하지 않는 형식" not in text


# --------------------------------------------------------------------------- M1

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
    build(z, [("demo", "%APPDATA%\\Vendor", f"Demo/f{i}.cfg", b"x" * (i + 1)) for i in range(3)])
    yield tmp_path, root, rd, z
    envpaths.set_overrides(None)


def _fail_on_call(monkeypatch, n, exc):
    calls = [0]

    def cp():
        calls[0] += 1
        if calls[0] == n:
            raise exc
    monkeypatch.setattr("pcmigrate.package_apply.checkpoint", cp)  # 복원 루프의 checkpoint 만(검증 단계의 호출은 세지 않는다)


def test_m1_cancel_keeps_partial_report_and_undo_record(run_env, monkeypatch):
    tmp, root, rd, z = run_env
    _fail_on_call(monkeypatch, 3, Cancelled())  # 첫 파일(호출 1,2)은 복원하고 둘째 파일 앞에서 취소
    with pytest.raises(Cancelled) as ei:
        api.run_import(z, rd, yes=True, record_dir=tmp / "rec")
    part = ei.value.partial_result
    assert part.mode == "interrupted" and [r.status for r in part.report.results] == ["copied"]
    assert (root / "Vendor" / "Demo" / "f0.cfg").exists()
    rec = import_record.load_record(Path(part.record_path))  # 이미 쓴 파일을 되돌릴 기록이 남는다
    assert len(rec["entries"]) == 1 and part.report.meta["interrupted"] == "Cancelled"
    assert "중간에 멈췄습니다" in "\n".join(summary.import_lines(part, str(z), part.report))


def test_m1_unexpected_exception_also_keeps_record_and_logs(run_env, monkeypatch):
    tmp, root, rd, z = run_env
    _fail_on_call(monkeypatch, 3, RuntimeError("boom"))
    with pytest.raises(RuntimeError) as ei:
        api.run_import(z, rd, yes=True, record_dir=tmp / "rec")
    assert ei.value.partial_result.record_path and Path(ei.value.partial_result.record_path).exists()


def test_m1_cli_writes_report_on_interrupt(run_env, monkeypatch, capsys):
    tmp, root, rd, z = run_env
    _fail_on_call(monkeypatch, 3, RuntimeError("boom"))
    rep = tmp / "report.json"
    code = cli.main(["--env", f"APPDATA={root}", "import", str(z), "--rules", str(rd), "--yes", "--report", str(rep),
                     "--record-dir", str(tmp / "rec")])
    assert code == 1 and rep.exists() and "중단" in capsys.readouterr().err
    assert json.loads(rep.read_text(encoding="utf-8"))["summary"]["copied"] == 1


# --------------------------------------------------------------------------- M5, L4

def test_m5_dest_dir_is_failed_in_preview_and_apply(env):
    tmp, root, expand = env
    (root / "Vendor" / "Demo" / "a.cfg").mkdir(parents=True)  # 이름 자리에 폴더
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a"), ("demo", "%APPDATA%\\Vendor", "Demo/b.cfg", b"b")])
    plan = plan_import(z, [make_rule()], expand)
    by = {Path(i["entry"]).name: i for i in plan.items}
    assert by["a.cfg"]["status"] == "failed" and by["a.cfg"]["reason"] == "dest_is_dir"
    rep = apply_import(z, plan)
    assert {Path(r.src).name: r.status for r in rep.results} == {"a.cfg": "failed", "b.cfg": "copied"}
    assert (root / "Vendor" / "Demo" / "a.cfg").is_dir()


def test_l4_restored_file_keeps_original_mtime(env):
    tmp, root, expand = env
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a")], mtime=(2021, 3, 4, 5, 6, 8))
    apply_import(z, plan_import(z, [make_rule()], expand))
    import time
    assert abs(os.stat(root / "Vendor" / "Demo" / "a.cfg").st_mtime - time.mktime((2021, 3, 4, 5, 6, 8, 0, 0, -1))) < 3


# --------------------------------------------------------------------------- M3

B = "\\"


def _rule(paths=None, **over):
    d = {"id": "x", "name": "X", "detect": {"display_name_patterns": ["X"]},
         "paths": paths if paths is not None else [{"path": "%APPDATA%" + B + "A", "dest": "x/a"}]}
    d.update(over)
    return parse_rule(d)


@pytest.mark.parametrize("path", [
    "Foo" + B + "Bar", "%NOPE%" + B + "x", "%WINDIR%" + B + "System32" + B + "drivers", "%SystemRoot%" + B + "x",
    "%ProgramFiles%" + B + "X", "%APPDATA%" + B + "*", "%USERPROFILE%" + B + "*", "%APPDATA%", "%APPDATA%" + B + "Foo:bar",
    "%APPDATA%" + B + "." + B + "Foo", "%APPDATA%" + B + ".." + B + "Foo", "%APPDATA%" + B + "%USERNAME%",
    "C:Foo", "%APPDATA%" + B + "*.*", "%APPDATA%" + B + B + "x"])
def test_m3_bad_rule_paths_rejected(path):
    with pytest.raises(RuleError):
        _rule([{"path": path, "dest": "x/a"}])


def test_m3_dup_dest_bad_allow_ext_bad_exclude_bad_id():
    with pytest.raises(RuleError):
        _rule([{"path": "%APPDATA%" + B + "A", "dest": "x/a"}, {"path": "%APPDATA%" + B + "B", "dest": "X/A"}])
    for ext in (".exe", ".dll", ".bat", ".ps1"):
        with pytest.raises(RuleError):
            _rule([{"path": "%APPDATA%" + B + "A", "dest": "x/a", "allow_extensions": [ext]}])
    for ex in ("*", "*.*", "?"):
        with pytest.raises(RuleError):
            _rule(exclude=[ex])
    for rid in ("../x", "a/b", "a b", "extra", "install", "", ".x", "x" * 65):
        with pytest.raises(RuleError):
            _rule(id=rid)


def test_m3_good_rules_still_accepted():
    r = _rule([{"path": "%AppData%" + B + "Autodesk" + B + "AutoCAD *" + B + "R*" + B + "*" + B + "Support", "dest": "x/a",
                "allow_extensions": [".lsp", ".rb", ".addin"]},
               {"path": "%OneDrive%" + B + "Documents" + B + "X*" + B + "settings*.ini", "dest": "x/b", "kind": "file"}],
              exclude=["*.lic", "*.tmp"])
    assert len(r.paths) == 2
    assert len(load_rules(REPO_RULES)) == 25  # 기존 25개 규칙이 그대로 통과


def test_m3_record_mode_rule_passes_validation(tmp_path, monkeypatch):
    from test_record_mode import Fake
    f = Fake(tmp_path, monkeypatch)
    try:
        ini = f.put(f.app / "Fake Zephyr" / "settings.ini", "a=1")
        s, _ = f.start()
        f.snap(s)
        f.put(ini, "a=2")
        f.snap(s)
        from pcmigrate import record_mode as rm
        rule = rm.build_rule(rm.diff(s))
        parse_rule(rule)
    finally:
        envpaths.set_overrides(None)


def test_l6_bracket_in_name_not_a_candidate(tmp_path, monkeypatch):
    from test_record_mode import Fake
    from pcmigrate import record_mode as rm
    f = Fake(tmp_path, monkeypatch)
    try:
        z = f.app / "FakeZephyr"
        a = f.put(z / "a[1].ini", "1")
        b = f.put(z / "ok.ini", "1")
        s, _ = f.start()
        f.snap(s)
        f.put(a, "2")
        f.put(b, "2")
        f.snap(s)
        rule = rm.build_rule(rm.diff(s))
        text = json.dumps(rule["paths"])
        assert "ok.ini" in text and "a[1]" not in text
        parse_rule(rule)
    finally:
        envpaths.set_overrides(None)


# --------------------------------------------------------------------------- M2 / L1 / L2 / L7 규칙

def test_m2_qgis_rule_excludes_password_file_and_warns():
    rules = {r.id: r for r in load_rules(REPO_RULES)}
    q = rules["qgis"]
    assert "QGIS3.ini" in q.exclude and int(q.version) >= 4
    text = " ".join(q.manual_warnings)
    assert "QGIS3.ini" in text and "비밀번호" in text and "python\\plugins" in text and "확인 필요" in q.notes


def test_l2_l7_rule_warnings():
    rules = {r.id: r for r in load_rules(REPO_RULES)}
    for rid in ("revit", "sketchup"):
        assert any("DLL" in w and "따로 설치" in w for w in rules[rid].manual_warnings), rid
    assert all("DPAPI" not in w or "Firefox" in w for w in rules["browser"].manual_warnings)
    for rid in ("archicad", "sketchup", "rhino"):
        assert "install_paths" in rules[rid].notes and "한계" in rules[rid].notes


@pytest.mark.parametrize("name", ["a.rbe", "a.rbs", "a.dvb", "a.lua", "a.vim", "a.vsto", "a.docm", "a.xlsm", "a.theme", "a.rdp",
                                  "a.scf", "a.library-ms", "a.so", "desktop.ini", "Desktop.INI"])
def test_l1_new_deny_extensions_denied(env, name):
    tmp, _, expand = env
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/" + name, b"a")])
    assert plan_import(z, [make_rule()], expand).items[0]["status"] == "denied"


def test_l1_rule_allowed_extension_still_restored(env):
    tmp, _, expand = env
    rule = make_rule(paths=[{"path": "%APPDATA%\\Vendor\\Demo", "dest": "demo/data", "allow_extensions": [".dvb"]}])
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.dvb", b"a")])
    assert plan_import(z, [rule], expand).items[0]["status"] == "would_copy"
    rule2 = make_rule(paths=[{"path": "%APPDATA%\\Vendor\\Demo\\*.rdp", "dest": "demo/data", "kind": "file"}])
    build(z, [("demo", "%APPDATA%\\Vendor\\Demo", "a.rdp", b"a")])
    assert plan_import(z, [rule2], expand).items[0]["status"] == "would_copy"


# --------------------------------------------------------------------------- M6

import contextlib
_nullctx = contextlib.nullcontext


def _pkg_with_programs(tmp, programs, extra=None):
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a")], programs=programs, manifest_extra=extra)
    return z


def test_m6_corrupt_registry_json_gives_apierror_not_traceback(tmp_path, run_env):
    _, _, rd, _ = run_env
    for bad in (b"{not json", b'{"a": 1}'):
        z = _pkg_with_programs(tmp_path, {"programs/registry.json": bad})
        with pytest.raises(api.ApiError, match="프로그램 목록"):
            api.run_checklist(rd, package=z)
        with pytest.raises(api.ApiError, match="프로그램 목록"):
            api.run_install_check(z, new_state=api.NewState())
    for odd in (b'[1, "x", null]', b'[{"name": 5, "version": 3}]'):  # 항목 모양이 이상해도 죽지 않는다
        z = _pkg_with_programs(tmp_path, {"programs/registry.json": odd})
        api.run_install_check(z, new_state=api.NewState())


def test_m6_integer_names_are_coerced(tmp_path, run_env):
    _, _, rd, _ = run_env
    z = _pkg_with_programs(tmp_path, {"programs/registry.json": b'[{"name": 5, "version": 3}, {"name": "Demo 1"}, 7]'})
    res = api.run_checklist(rd, package=z)  # 죽지 않는다
    assert res.text


def test_m6_winget_schema_validation(tmp_path):
    from pcmigrate.package_winget import extract_winget
    good = json.dumps({"Sources": [{"Packages": [{"PackageIdentifier": "Notepad++.Notepad++"}]}]}).encode()
    z = _pkg_with_programs(tmp_path, {"programs/winget.json": good})
    extract_winget(z, tmp_path / "out")
    for bad in ({"Sources": "x"}, {"Sources": [{"Packages": ["a"]}]}, {"Sources": [{"Packages": [{"PackageIdentifier": "a b; calc"}]}]},
                {"Sources": [{"Packages": [{"PackageIdentifier": 5}]}]}, [1]):
        z = _pkg_with_programs(tmp_path, {"programs/winget.json": json.dumps(bad).encode()})
        with pytest.raises(PackageError):
            extract_winget(z, tmp_path / "out2")
    assert not (tmp_path / "out2" / "winget.json").exists()


def test_m6_programs_hash_detects_tampering_and_legacy_is_flagged(tmp_path):
    reg = b"[]"
    ok = {"programs_sha256": {"programs/registry.json": hashlib.sha256(reg).hexdigest()}}
    z = _pkg_with_programs(tmp_path, {"programs/registry.json": reg}, ok)
    m, probs = verify_package(z)
    assert probs == [] and not m.get("_programs_unverified")
    z = _pkg_with_programs(tmp_path, {"programs/registry.json": b'[{"name": "evil"}]'}, ok)
    assert any("해시 불일치" in p for p in verify_package(z)[1])
    z = _pkg_with_programs(tmp_path, {"programs/registry.json": reg, "programs/winget.json": b"{}"}, ok)
    assert any("해시가 manifest 에 없음" in p for p in verify_package(z)[1])
    z = _pkg_with_programs(tmp_path, {"programs/registry.json": reg})  # 옛 패키지: 기존처럼 읽되 표시
    m, probs = verify_package(z)
    assert probs == [] and m["_programs_unverified"] is True
    from pcmigrate.checklist import manifest_warning_pairs
    assert any("옛 형식" in i for _, items in manifest_warning_pairs(m) for i in items)


def test_m6_export_writes_programs_hash(tmp_path):
    from pcmigrate.detect import Program
    from pcmigrate.package import export_package
    root = tmp_path / "old"
    (root / "Vendor" / "Demo").mkdir(parents=True)
    (root / "Vendor" / "Demo" / "a.cfg").write_bytes(b"a")
    rule = parse_rule({"id": "demo", "name": "Demo", "detect": {"display_name_patterns": ["Demo"]},
                       "paths": [{"path": "%APPDATA%\\Vendor\\Demo", "dest": "demo/data"}]})
    out = tmp_path / "o.zip"
    m = export_package([rule], out, [Program("Demo", "1")], '{"Sources": []}', expand=lambda s: s.replace("%APPDATA%", str(root)))
    assert set(m["programs_sha256"]) == {"programs/registry.json", "programs/winget.json"}
    mm, probs = verify_package(out)
    assert probs == [] and "_programs_unverified" not in mm


# --------------------------------------------------------------------------- M7, L5

def test_m7_common_old_path_warning_in_plan_and_checklist(env):
    tmp, _, expand = env
    z = tmp / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a")])
    plan = plan_import(z, [make_rule()], expand)
    assert any("구 PC 의 경로" in i for w in plan.warnings for i in w["items"])
    from pcmigrate.checklist import manifest_warning_pairs
    assert any("구 PC 의 경로" in i for _, items in manifest_warning_pairs(plan.manifest) for i in items)


def test_l5_declared_total_size_cap(tmp_path, monkeypatch):
    z = tmp_path / "p.zip"
    build(z, [("demo", "%APPDATA%\\Vendor", "Demo/a.cfg", b"a")])
    import pcmigrate.package_verify as pv
    monkeypatch.setattr(pv, "MAX_TOTAL_BYTES", 0)
    m, probs = verify_package(z)
    assert m is None and any("합이 너무 큽니다" in p for p in probs)
