"""stage72: 재점검 지적 수정 재현 시험(N1~N4, L5, L7, L8, Rhino 경고, M2 잔여, GUI 부분 결과). 가짜 폴더만 쓴다."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from pcmigrate import api, checklist, detect, envpaths, install_check as ic
from pcmigrate.checklist import build_checklist
from pcmigrate.detect import Program, valid_winget_id
from pcmigrate.package import verify_package
from pcmigrate.rules import RuleError, load_rules, parse_rule

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"


# --------------------------------------------------------------------------- N1

def test_n1_notepadpp_known_app_checklist_does_not_crash():
    cl = build_checklist([], [Program("Notepad++ (64-bit x64)", "8.6", "Notepad++ Team")], [])
    text = "\n".join(cl.winget_cmd)
    assert "winget install --id Notepad++.Notepad++ --exact" in text


def test_n1_id_check_allows_plus_but_rejects_injection_everywhere():
    assert valid_winget_id("Notepad++.Notepad++") and valid_winget_id("Google.Chrome")
    for bad in ("a b", "a;b", "a&b", "a|b", '"a"', "a\nb", "a`b", "$(calc)", "-rf", ".x", "+x", "a" * 200, "a+;b", "a+ b", "a+&b"):
        assert not valid_winget_id(bad), bad
    import pcmigrate.package_winget as pw
    assert not hasattr(pw, "_ID_OK")  # winget-extract 도 같은 검사 하나만 쓴다
    assert ic.safe_winget_ids(["Notepad++.Notepad++"]) == ["Notepad++.Notepad++"]
    with pytest.raises(ValueError):
        ic.safe_winget_ids(["Notepad++.Notepad++; calc"])
    assert detect.winget_install_command("Notepad++.Notepad++").startswith("winget install --id Notepad++.Notepad++ --exact")


def test_n1_one_bad_item_only_replaces_its_line(monkeypatch):
    progs = [Program("Notepad++ (64-bit x64)", "8.6", "Notepad++ Team"), Program("7-Zip 23.01 (x64)", "23.01", "Igor Pavlov")]
    real = detect.safe_install_command
    monkeypatch.setattr(checklist, "safe_install_command",
                        lambda wid, v="", loc=detect.DEFAULT_LOCALE: None if wid.startswith("Notepad") else real(wid, v, loc))
    cl = build_checklist([], progs, [])
    joined = "\n".join(cl.winget_cmd)
    assert "형식이 올바르지 않아 설치 명령을 만들지 못했습니다" in joined and "winget install --id 7zip.7zip" in joined


def test_n1_install_check_survives_tampered_winget_id():
    old_reg = [Program("Foo App", "1.0", "Foo"), Program("Notepad++ (64-bit x64)", "8.6", "Notepad++ Team")]
    old_wg = [Program("Bad.Id; calc", "1", source="winget", winget_id="Bad.Id; calc"),
              Program("Notepad++.Notepad++", "8", source="winget", winget_id="Notepad++.Notepad++")]
    r = ic.check_install(old_reg, old_wg, None, ic.NewState())
    cmds = r.individual_commands  # 예외 없이 계산된다
    assert all("calc" not in c for c in cmds)
    assert r.summary()


# --------------------------------------------------------------------------- N3

def _rule_json(rid, **over):
    d = {"id": rid, "name": rid, "detect": {"display_name_patterns": [rid]}, "paths": [{"path": "%APPDATA%\\V\\" + rid, "dest": rid}]}
    d.update(over)
    return json.dumps(d)


def test_n3_bad_rule_file_is_skipped_not_fatal(tmp_path, capsys):
    (tmp_path / "good.json").write_text(_rule_json("good"), encoding="utf-8")
    (tmp_path / "dotted.json").write_text(_rule_json("my.rule"), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "dup.json").write_text(_rule_json("good"), encoding="utf-8")
    with pytest.raises(RuleError):
        load_rules(tmp_path)  # 기본(엄격) 동작은 그대로
    errs: list[str] = []
    rules = load_rules(tmp_path, errors=errs)
    assert [r.id for r in rules] == ["good"] and len(errs) == 3
    errs2: list[str] = []
    assert [r.id for r in api.load_rules_checked(tmp_path, errors=errs2)] == ["good"] and len(errs2) == 3
    assert [r.id for r in api.load_rules_checked(tmp_path)] == ["good"]
    assert "건너뛰고" in capsys.readouterr().err


def test_n3_all_bad_still_error_and_check_rules_reports(tmp_path, capsys):
    from pcmigrate import cli
    (tmp_path / "x.json").write_text(_rule_json("a.b"), encoding="utf-8")
    with pytest.raises(api.ApiError):
        api.load_rules_checked(tmp_path)
    (tmp_path / "ok.json").write_text(_rule_json("ok"), encoding="utf-8")
    assert cli.main(["check-rules", "--rules", str(tmp_path)]) == 1
    assert "오류가 있습니다" in capsys.readouterr().err


def test_n3_bundled_rules_unaffected():
    errs: list[str] = []
    assert len(load_rules(REPO_RULES, errors=errs)) == 25 and errs == []


# --------------------------------------------------------------------------- N4

def test_n4_percent_in_name_is_excluded_with_reason(tmp_path, monkeypatch):
    from test_record_mode import Fake
    from pcmigrate import record_mode as rm
    f = Fake(tmp_path, monkeypatch)
    try:
        z = f.app / "FakeZephyr"
        a = f.put(z / "100%.ini", "1")
        b = f.put(z / "ok.ini", "1")
        s, _ = f.start()
        f.snap(s)
        f.put(a, "2")
        f.put(b, "2")
        f.snap(s)
        an = rm.diff(s)
        pct = [c for c in an.candidates if "100%" in c.path][0]
        assert pct.nonconfig and "제외" in pct.reason
        rule = rm.build_rule(an)
        assert "ok.ini" in json.dumps(rule["paths"]) and "100%" not in json.dumps(rule["paths"])
        parse_rule(rule)  # 새 검증을 통과하고 OUTSIDE_MSG 로 원인이 가려지지 않는다
    finally:
        envpaths.set_overrides(None)


# --------------------------------------------------------------------------- L5

def _zip_with(path, name, data, comp=zipfile.ZIP_DEFLATED):
    m = {"format": 1, "tool_version": "x", "created": "t", "rules": [], "files": [{
        "arc": "data/x/" + name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "rule": "extra",
        "base": "%APPDATA%", "rel": "X/" + name}], "skipped": [], "failed": [], "coverage": [], "manual_warnings": []}
    with zipfile.ZipFile(path, "w", comp) as z:
        z.writestr("data/x/" + name, data)
        z.writestr("manifest.json", json.dumps(m))


def test_l5_zero_filled_bomb_rejected_but_normal_large_text_passes(tmp_path):
    bomb = tmp_path / "bomb.zip"
    _zip_with(bomb, "z.ini", b"\0" * (40 * 1024 * 1024))
    m, probs = verify_package(bomb)
    assert any("압축" in p for p in probs)
    # 정상: 큰 텍스트 설정(압축비 수십 배)은 통과
    ok = tmp_path / "ok.zip"
    text = ("".join(f"key{i}=value-{i * 7919 % 1000}\n" for i in range(600_000))).encode()
    _zip_with(ok, "big.ini", text)
    assert len(text) > 8 * 1024 * 1024
    m, probs = verify_package(ok)
    assert m is not None and probs == []
    # 16 MiB 미만의 거의 0 데이터는 면제(실제 설정 파일의 패딩 등)
    small = tmp_path / "small.zip"
    _zip_with(small, "pad.ini", b"\0" * (4 * 1024 * 1024))
    assert verify_package(small)[1] == []


def test_l5_total_declared_cap(tmp_path, monkeypatch):
    from pcmigrate.package_base import MAX_TOTAL_BYTES
    assert MAX_TOTAL_BYTES == 256 * 1024 ** 3
    z = tmp_path / "t.zip"
    _zip_with(z, "a.ini", b"a=1\n" * 10)
    import pcmigrate.package_verify as pv
    monkeypatch.setattr(pv, "MAX_TOTAL_BYTES", 5)
    assert any("합이 너무 큽니다" in p for p in verify_package(z)[1])


# --------------------------------------------------------------------------- L7

@pytest.mark.parametrize("name,rt,expect", [
    ("Security Update for Windows (KB5012345)", "", True), ("Update for Microsoft Office (KB4011234)", "", True),
    ("Hotfix for X", "Hotfix", True), ("Windows Security", "Security Update", True), ("Foo", "Update Rollup", True),
    ("Microsoft Update Health Tools", "", False), ("Python 3.12.1 (64-bit)", "", False), ("Autodesk Revit 2024", "", False),
    ("Update Manager Pro", "", False), ("KB Viewer (KB)", "", False), ("Visual Studio Code", "Application", False)])
def test_l7_update_noise_filter(name, rt, expect):
    assert detect.is_update_noise(name, rt) is expect


# --------------------------------------------------------------------------- L8

def test_l8_exe_config_denied_for_rules_and_install_dir():
    from pcmigrate import install_dir
    assert not install_dir.is_settings_name("app.exe.config") and not install_dir.is_settings_name("X.DLL.config")
    assert install_dir.is_settings_name("settings.config") and install_dir.is_settings_name("a.json")
    rule = parse_rule({"id": "demo", "name": "D", "detect": {"display_name_patterns": ["D"]},
                       "paths": [{"path": "%APPDATA%\\V\\D", "dest": "d"}]})
    from pcmigrate.package_deny import restore_denied
    assert restore_denied("D/app.exe.config", Path("C:/x/D/app.exe.config"), rule, "%APPDATA%\\V")
    assert restore_denied("D/x.dll.config", Path("C:/x/D/x.dll.config"), None, "%APPDATA%")
    assert restore_denied("D/settings.config", Path("C:/x/D/settings.config"), rule, "%APPDATA%\\V") is None
    allowed = parse_rule({"id": "demo", "name": "D", "detect": {"display_name_patterns": ["D"]},
                          "paths": [{"path": "%APPDATA%\\V\\D", "dest": "d", "allow_extensions": [".exe.config"]}]})
    assert restore_denied("D/app.exe.config", Path("C:/x/D/app.exe.config"), allowed, "%APPDATA%\\V") is None


# --------------------------------------------------------------------------- Rhino / QGIS 규칙

def test_rhino_has_version_check_warning_and_qgis_note():
    rules = {r.id: r for r in load_rules(REPO_RULES)}
    assert any("설치된 버전을 확인하지 않습니다" in w for w in rules["rhino"].manual_warnings)
    assert "python\\plugins" in rules["qgis"].notes and "나열" in rules["qgis"].notes and int(rules["qgis"].version) >= 5


# --------------------------------------------------------------------------- GUI 부분 결과(N2)

class _Report:
    results: list = []
    failures: list = []

    @staticmethod
    def to_dict():
        return {"summary": {"copied": 1}}


class _Plan:
    warnings: list = []


def test_n2_gui_summary_and_note_for_interrupted_result():
    from pcmigrate.gui.controller_import import ImportMixin
    c = ImportMixin()
    c.state = type("S", (), {"import_result": type("R", (), {"mode": "interrupted", "report": _Report, "plan": _Plan})()})()
    s = c.import_summary()
    assert s["interrupted"] is True and any("중지했습니다" in w[1] for w in s["warnings"])
    assert "중지했습니다" in c.interrupted_note()
    c.state.import_result = type("R", (), {"mode": "applied", "report": _Report, "plan": _Plan})()
    assert c.interrupted_note() == "" and c.import_summary()["interrupted"] is False


def test_n2_gui_cancel_during_restore_reaches_result_screen(tmp_path):
    """복원 중 취소: 취소 화면으로 끝나지 않고 부분 결과(mode='interrupted')가 결과 화면 경로(import_done)로 전달된다."""
    import test_gui_controller as tg
    from pcmigrate.cancel import Cancelled
    from pcmigrate.copier import FileResult, Report
    api_ = tg.FakeApi()
    c = tg.Controller(api_mod=api_, runner=tg.Runner(sync=True), rules_dir=tmp_path,
                      make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    ev = []
    c.listener = lambda kind, payload=None: ev.append((kind, payload))

    def run_import(package, rules_dir, *, dry_run=False, yes=False, allow_extra=False, deps=None):
        if dry_run or not yes:
            return api_.preview
        rep = Report(dry_run=False, results=[FileResult("e", r"C:\x\a.txt", "copied", 1, "x")])
        exc = Cancelled()
        exc.partial_result = api.ImportResult(api_.preview.plan, "interrupted", api_.preview.n_extra, rep)
        raise exc
    api_.run_import = run_import
    c.set_package(r"C:\p.zip")
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    kinds_ = [k for k, _ in ev]
    assert "import_done" in kinds_ and "cancelled" not in kinds_
    assert c.state.import_result.mode == "interrupted"
    assert c.import_summary()["interrupted"] is True and c.import_counts()[0] == 1
    assert c.state.busy is False


def test_n2_gui_cancel_without_partial_is_still_cancelled(tmp_path):
    import test_gui_controller as tg
    from pcmigrate.cancel import Cancelled
    api_ = tg.FakeApi()
    c = tg.Controller(api_mod=api_, runner=tg.Runner(sync=True), rules_dir=tmp_path,
                      make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    ev = []
    c.listener = lambda kind, payload=None: ev.append((kind, payload))

    def run_import(package, rules_dir, *, dry_run=False, yes=False, allow_extra=False, deps=None):
        if dry_run or not yes:
            return api_.preview
        raise Cancelled()
    api_.run_import = run_import
    c.set_package(r"C:\p.zip")
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    assert "cancelled" in [k for k, _ in ev] and "import_done" not in [k for k, _ in ev]
