"""57단계: 구 PC 버전·언어(--locale) 안내. winget export --include-versions, 개별 설치 명령의 --locale/--version, 수동 설치 목록의 구 PC 버전.
가짜 runner/가짜 환경만 쓴다(실제 winget 실행 없음)."""
import json
import subprocess
from pathlib import Path

import pytest

from pcmigrate import cli
from pcmigrate import install_check as ic
from pcmigrate.checklist import OLD_VERSION_GUIDE, WINGET_IMPORT_GUIDE, build_checklist, render
from pcmigrate.detect import (Program, parse_winget_export, usable_version_arg, valid_locale, winget_export,
                              winget_export_raw, winget_install_command)
from pcmigrate.gui import texts as T

from test_install_check import by_name, make_pkg, reg, winget_json, wg
from test_winget_select_export import setup_controller  # noqa: F401


def runner_writing(payload, reject_include=False, fail_code=1):
    """가짜 winget: --include-versions 를 거부하는 구버전(reject_include)도 흉내 낸다."""
    calls = []

    def run(args):
        calls.append(list(args))
        if reject_include and "--include-versions" in args:
            return fail_code, ""
        Path(args[args.index("-o") + 1]).write_text(json.dumps(payload), encoding="utf-8-sig")
        return 0, ""
    run.calls = calls
    return run


DOC = {"Sources": [{"Packages": [{"PackageIdentifier": "Google.Chrome", "Version": "124.0.1"}]}]}


# =============================================================================== W1 export 에 버전
def test_export_asks_for_versions_and_keeps_them():
    run = runner_writing(DOC)
    text = winget_export_raw(run)
    assert len(run.calls) == 1 and "--include-versions" in run.calls[0] and "--accept-source-agreements" in run.calls[0]
    assert [(p.winget_id, p.version) for p in parse_winget_export(text)] == [("Google.Chrome", "124.0.1")]
    assert [p.version for p in winget_export(runner_writing(DOC))] == ["124.0.1"]


def test_export_falls_back_without_option_for_old_winget():
    run = runner_writing({"Sources": [{"Packages": [{"PackageIdentifier": "A.B"}]}]}, reject_include=True)
    text = winget_export_raw(run)
    assert text is not None and len(run.calls) == 2
    assert "--include-versions" in run.calls[0] and "--include-versions" not in run.calls[1]
    assert parse_winget_export(text)[0].version == ""  # 버전 없는 목록도 오류 없이 읽힌다


def test_export_does_not_retry_when_winget_missing_or_times_out():
    for exc in (FileNotFoundError("winget"), subprocess.TimeoutExpired("winget", 1)):
        calls = []

        def boom(args, exc=exc, calls=calls):
            calls.append(args)
            raise exc
        assert winget_export_raw(boom) is None and len(calls) == 1
    both = []

    def fail_both(args):
        both.append(args)
        return 1, ""
    assert winget_export_raw(fail_both) is None and len(both) == 2  # 옵션 문제인지 알 수 없어 옵션 없이 한 번 더 시도


def test_parse_tolerates_non_string_and_missing_versions():
    progs = parse_winget_export(json.dumps({"Sources": [{"Packages": [
        {"PackageIdentifier": "A.B", "Version": 5}, {"PackageIdentifier": "C.D", "Version": None}, {"PackageIdentifier": "E.F"}]}]}))
    assert [p.version for p in progs] == ["5", "", ""]


# =============================================================================== W2 로캘·버전 명령
def test_install_command_has_default_locale_and_optional_version():
    assert ic.install_command("Google.Chrome") == "winget install --id Google.Chrome --exact --locale ko-KR"
    assert ic.install_command("Google.Chrome", "124.0.1") == "winget install --id Google.Chrome --exact --locale ko-KR --version 124.0.1"
    assert ic.install_command("Google.Chrome", "124.0.1", "en-US").endswith("--locale en-US --version 124.0.1")
    assert ic.install_command("Google.Chrome", "9", "") == "winget install --id Google.Chrome --exact --version 9"  # 빈 로캘 = 생략
    for bad in ("Unknown", "1.0 & calc", "a b", '"x"', "", "latest", "-5"):
        assert "--version" not in winget_install_command("A.b", bad)  # 쓸 수 없는 버전은 조용히 뺀다
    assert usable_version_arg("1.2.3-beta+4") and not usable_version_arg("x;y")
    for bad in ("ko_KR", "ko-KR; calc", "한국어", "k", "ko-"):
        assert not valid_locale(bad)
        with pytest.raises(ValueError):
            ic.install_command("A.b", "", bad)
    with pytest.raises(ValueError):
        ic.install_command("A b & calc")


def test_selected_commands_use_versions_and_locale():
    got = ic.selected_commands(None, ["Google.Chrome", "7zip.7zip"], {"google.chrome": "124.0.1"}, "zh-CN")
    assert got["each"][0] == ("winget install --id Google.Chrome --exact --locale zh-CN --version 124.0.1 "
                              "--accept-package-agreements --accept-source-agreements")
    assert "--version" not in got["each"][1] and "--locale zh-CN" in got["each"][1]
    with pytest.raises(ValueError):
        ic.selected_commands(None, ["A.b"], None, "ko KR")
    assert ic.import_command("C:\\x\\winget.json") == ('winget import -i "C:\\x\\winget.json" --ignore-unavailable '
                                                      "--accept-package-agreements --accept-source-agreements")
    assert "--locale" not in ic.import_command("C:\\x\\winget.json")  # winget import 는 --locale 이 없다


def test_check_install_carries_winget_version_into_item():
    r = ic.check_install([reg("Acme Studio", "24.1", "Acme Inc")], [wg("Acme.Studio", "24.1.5")], None, ic.NewState([], [], None))
    it = by_name(r, "Acme")
    assert it.winget_version == "24.1.5" and r.winget_has_versions
    assert "구 PC 버전: 24.1" in it.old_info and "게시자 Acme Inc" in it.old_info and "winget 기록 버전 24.1.5" in it.old_info


def test_individual_commands_carry_locale_and_never_guess_registry_version():
    r = ic.check_install([reg("Google Chrome", "124.0.1")], [], None, ic.NewState([], [], None, winget_ok=False))
    # 묶음 winget 목록에 없는 항목: 레지스트리 버전은 winget 버전 표기와 다를 수 있어 --version 을 붙이지 않는다
    assert r.individual_commands == ["winget install --id Google.Chrome --exact --locale ko-KR"]
    r.locale = "en-US"
    assert r.individual_commands == ["winget install --id Google.Chrome --exact --locale en-US"]
    r.locale = ""
    assert r.individual_commands == ["winget install --id Google.Chrome --exact"]


# =============================================================================== W1 옛 묶음(버전 없음)
def test_old_package_without_versions_still_reads_and_says_so():
    r = ic.check_install([reg("Zeta Modeler", "3.2")], [wg("7zip.7zip"), wg("Foo.Bar")], None, ic.NewState([], [], None))
    assert r.has_winget_file and r.winget_has_versions is False
    assert any("버전 정보가 없습니다" in n and "최신판" in n for n in r.notes)
    assert json.loads(json.dumps(r.to_dict()))["winget_has_versions"] is False
    seven = by_name(r, "7zip.7zip")
    assert seven.winget_version == "" and "버전 정보 없음 — 구 PC 의 제어판에서 확인" in seven.old_info
    assert "--version" not in " ".join(ic.selected_commands(None, ["7zip.7zip"], {"7zip.7zip": ""})["each"])
    r2 = ic.check_install([], [wg("7zip.7zip", "23.01"), wg("Foo.Bar")], None, ic.NewState([], [], None))
    assert r2.winget_has_versions and not any("버전 정보가 없습니다" in n for n in r2.notes)  # 하나라도 있으면 안내하지 않는다


# =============================================================================== W3 수동 설치 목록의 구 PC 버전
def test_manual_item_shows_old_version_publisher_or_missing_text():
    r = ic.check_install([reg("Zeta Modeler", "3.2", "Zeta Co"), reg("Plain Tool")], [], None, ic.NewState([], [], None))
    z, plain = by_name(r, "Zeta Modeler"), by_name(r, "Plain Tool")
    assert z.status == plain.status == ic.MANUAL
    assert z.old_info == "구 PC 버전: 3.2(게시자 Zeta Co)" and z.publisher == "Zeta Co"
    assert plain.old_info == "구 PC 버전: 버전 정보 없음 — 구 PC 의 제어판에서 확인"


def test_checklist_manual_lines_name_old_version_and_guide():
    cl = build_checklist([], [Program("Zeta Modeler", "3.2", "Zeta Co"), Program("Plain Tool")], [])
    lines = "\n".join(cl.install)
    assert "Zeta Modeler 3.2 [구 PC 버전: 3.2(게시자 Zeta Co)]" in lines
    assert "Plain Tool [구 PC 버전: 버전 정보 없음 — 구 PC 의 제어판에서 확인]" in lines
    out = render(cl)
    assert "같은 버전을 받아 설치하세요" in out and "제어판 > 프로그램 및 기능" in out and "같은 언어판" in out
    assert OLD_VERSION_GUIDE in out


def test_checklist_individual_and_auto_lines_show_versions_and_locale():
    cl = build_checklist([], [Program("Google Chrome", "124.0.1"), Program("7-Zip 23.01 (x64)", "23.01")],
                         [Program("7zip.7zip", "23.01", source="winget", winget_id="7zip.7zip")])
    assert any("--locale ko-KR" in l and "Google.Chrome" in l and "구 PC 버전: 124.0.1" in l for l in cl.winget_cmd)
    assert any("구 PC 버전 23.01" in l for l in cl.winget_auto)
    none = build_checklist([], [Program("7-Zip", "")], [Program("7zip.7zip", "", source="winget", winget_id="7zip.7zip")])
    assert none.winget_auto[0].endswith("버전 정보 없음 — 구 PC 의 제어판에서 확인)")


def test_import_guide_is_honest_about_locale_and_versions():
    text = "\n".join(WINGET_IMPORT_GUIDE)
    assert "--ignore-unavailable" in text and "--ignore-versions" in text and "--locale" in text
    assert "지정할 수 없어" in text and "제공할 때만" in text and "자체 언어 선택" in text and "직접 확인" in text
    gui = "\n".join(T.CL_GUIDE)
    assert "--ignore-versions" in gui and "지정할 수 없어" in gui and "제공할 때만" in gui and "자체 언어 선택" in gui
    for note in (T.INST_LOCALE_NOTE, ic.LOCALE_NOTE):
        assert "지정할 수 없어" in note and "제공할 때만" in note and "자체" in note
    assert "--locale" not in ic.IMPORT_FLAGS and "--ignore-versions" not in ic.IMPORT_FLAGS and "--ignore-unavailable" in ic.IMPORT_FLAGS


# =============================================================================== CLI
def test_cli_install_check_shows_old_versions_locale_and_notes(tmp_path, monkeypatch, capsys):
    pkg = make_pkg(tmp_path, winget_text=winget_json(("7zip.7zip", "23.01")))
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [])
    lst = tmp_path / "wl.txt"
    lst.write_text("Name  Id  Version\n", encoding="utf-8")
    assert cli.main(["install-check", str(pkg), "--winget-list-file", str(lst)]) == 0
    out = capsys.readouterr().out
    assert "구 PC 버전:" in out and "같은 버전을 받아 설치하세요" in out and "지정할 수 없어" in out
    assert "--locale ko-KR" in out
    assert cli.main(["install-check", str(pkg), "--winget-list-file", str(lst), "--locale", "en-US"]) == 0
    assert "--locale en-US" in capsys.readouterr().out


def test_cli_locale_option_rejects_bad_value(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["install-check", str(tmp_path / "x.zip"), "--locale", "ko KR; calc"])
    assert e.value.code == 2 and "로캘 형식" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["winget-extract", str(tmp_path / "x.zip"), "--list", "--locale", "!!"])


def test_cli_winget_extract_only_prints_version_locale_commands(tmp_path, capsys):
    pkg = make_pkg(tmp_path, winget_text=winget_json(("Google.Chrome", "124.0.1"), ("7zip.7zip", "")))
    o = tmp_path / "o"
    assert cli.main(["winget-extract", str(pkg), "--output", str(o), "--only", "google.chrome,7zip.7zip", "--locale", "en-US"]) == 0
    out = capsys.readouterr().out
    assert "winget install --id Google.Chrome --exact --locale en-US --version 124.0.1 " in out
    assert "winget install --id 7zip.7zip --exact --locale en-US --accept" in out  # 버전 없는 항목은 --version 없음
    assert "지정할 수 없어" in out and "--ignore-versions" in out
    assert cli.main(["winget-extract", str(pkg), "--output", str(tmp_path / "o2"), "--only", "7zip.7zip"]) == 0
    out2 = capsys.readouterr().out
    assert "--locale ko-KR" in out2 and "구 PC 버전 정보가 없습니다" in out2  # 고른 항목에 버전이 없는 목록


# =============================================================================== GUI 컨트롤러(문구/명령만, 화면 구조 변경 없음)
def test_gui_selected_install_each_has_version_and_locale_and_rows_show_old_info(tmp_path):
    c, fake, ev, seen = setup_controller(tmp_path)
    old_reg = [reg("Zeta Modeler", "3.2", "Zeta Co")]
    c.state.install_check = ic.check_install(old_reg, [wg("Acme.Studio", "2.0"), wg("Foo.Bar")], None, ic.NewState([], [], None))
    rows = {r["name"]: r for r in c.install_rows()}
    assert rows["Zeta Modeler"]["old_info"] == "구 PC 버전: 3.2(게시자 Zeta Co)" and rows["Zeta Modeler"]["version"] == "3.2"
    assert rows["Foo.Bar"]["version"] == T.INST_NO_VERSION_COL
    c.toggle_install_pick("Acme.Studio")
    c.toggle_install_pick("Foo.Bar")
    info = c.prepare_selected_install()
    each = dict(zip(info["file_ids"], info["each"]))
    assert "--locale ko-KR --version 2.0 " in each["Acme.Studio"]
    assert "--locale ko-KR" in each["Foo.Bar"] and "--version" not in each["Foo.Bar"]
    assert info["command"].startswith("winget import -i") and "--locale" not in info["command"]
    notes = "\n".join(c.install_notes())
    assert T.INST_OLD_VERSION_GUIDE in notes and "같은 버전" in notes and "같은 언어판" in notes


def test_gui_notes_warn_when_package_has_no_winget_versions(tmp_path):
    c, fake, ev, seen = setup_controller(tmp_path)
    c.state.install_check = ic.check_install([], [wg("Foo.Bar")], None, ic.NewState([], [], None))
    assert T.INST_NO_WINGET_VERSIONS in c.install_notes()
    c.state.install_check = ic.check_install([], [wg("Foo.Bar", "1.0")], None, ic.NewState([], [], None))
    assert T.INST_NO_WINGET_VERSIONS not in c.install_notes()
