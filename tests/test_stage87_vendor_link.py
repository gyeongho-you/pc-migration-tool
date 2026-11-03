"""stage87: 수동 설치 목록의 '제조사 사이트' 줄. 주소는 Uninstall 키의 URLInfoAbout > HelpLink > URLUpdateInfo 첫 유효 값이고, 검증·정화를 통과한
http(s) 주소만 글자로 보여 준다(접속/열기 없음). 실제 레지스트리는 읽지 않고 가짜 값/가짜 winreg 로만 시험한다."""
import json
import sys
import types
import zipfile
from dataclasses import asdict

import pytest

from pcmigrate import cli, detect, install_check as ic
from pcmigrate.checklist import build_checklist, describe_manual, load_package_programs
from pcmigrate.detect import Program, pick_vendor_url, program_from_values, sanitize_vendor_url

ESC = chr(27)


# --------------------------------------------------------------------------- 정화

@pytest.mark.parametrize("raw,expect", [
    ("https://www.autodesk.com/support", "https://www.autodesk.com/support"),
    ("  https://example.com/a?b=1#c  ", "https://example.com/a?b=1#c"),
    ("HTTP://Example.com/Path", "http://Example.com/Path"),  # 스킴만 소문자로
    ("HTTPS://example.com:8443/x", "https://example.com:8443/x"),
    ("https://sub.example.co.kr/%EC%A0%9C", "https://sub.example.co.kr/%EC%A0%9C"),
])
def test_sanitize_accepts_safe_http_urls(raw, expect):
    assert sanitize_vendor_url(raw) == expect


@pytest.mark.parametrize("raw", [
    "", "   ", None, 5, [], "example.com", "//example.com", "javascript:alert(1)", "JaVaScRiPt:alert(1)", "file:///C:/x", "ftp://example.com/x",
    "mailto:a@b.com", "data:text/html,x", "https:example.com", "https://user:pass@example.com/", "https://user@example.com/",
    "https://example.com@evil.com/", "https://example.com/a b", "https://example.com/a\tb", "https://example.com/a\nb",
    'https://example.com/"x"', "https://example.com/'x'", "https://example.com/<a>", "https://example.com/a`b", "https://example.com/a\\b",
    "https://example.com/" + ESC + "[31mred", "https://example.com/" + chr(7), "https://example.com/" + chr(0), "https://example.com/\u202e",
    "https://localhost/", "https://x/", "https://exa mple.com/", "https://한글.com/", "https://example.com/한글", "https://" + "a" * 290 + ".com/b",
    "https://-bad-.com/".replace("-bad-", "a b"), "https://example.com:port/",
])
def test_sanitize_rejects_unsafe_values(raw):
    assert sanitize_vendor_url(raw) == ""


def test_sanitize_length_limit_boundary():
    ok = "https://example.com/" + "a" * (detect.VENDOR_URL_MAX - len("https://example.com/"))
    assert len(ok) == detect.VENDOR_URL_MAX and sanitize_vendor_url(ok) == ok
    assert sanitize_vendor_url(ok + "a") == ""


# --------------------------------------------------------------------------- 레지스트리 값 선택(주입)

def _get(values):
    return lambda name: values.get(name, "")


def test_priority_about_then_help_then_update():
    v = {"URLInfoAbout": "https://about.example.com/", "HelpLink": "https://help.example.com/", "URLUpdateInfo": "https://upd.example.com/"}
    assert pick_vendor_url(_get(v)) == "https://about.example.com/"
    del v["URLInfoAbout"]
    assert pick_vendor_url(_get(v)) == "https://help.example.com/"
    del v["HelpLink"]
    assert pick_vendor_url(_get(v)) == "https://upd.example.com/"
    del v["URLUpdateInfo"]
    assert pick_vendor_url(_get(v)) == ""


def test_invalid_first_value_falls_through_to_next_valid():
    v = {"URLInfoAbout": "javascript:alert(1)", "HelpLink": "https://user:pw@evil.com/", "URLUpdateInfo": "https://ok.example.com/u"}
    assert pick_vendor_url(_get(v)) == "https://ok.example.com/u"
    assert pick_vendor_url(_get({"URLInfoAbout": "file:///x", "HelpLink": ""})) == ""


def test_program_from_values_sets_vendor_url_and_skips_system_components():
    base = {"DisplayName": "Acme Studio", "DisplayVersion": "1.0", "Publisher": "Acme", "HelpLink": "https://acme.example.com/help"}
    p = program_from_values(_get(base))
    assert p.name == "Acme Studio" and p.vendor_url == "https://acme.example.com/help"
    assert program_from_values(_get({**base, "SystemComponent": "1"})) is None
    assert program_from_values(_get({"HelpLink": "https://x.example.com"})) is None
    assert program_from_values(_get({"DisplayName": "Bare"})).vendor_url == ""  # 값이 없으면 빈 문자열(지어내지 않음)


def test_read_registry_programs_with_fake_winreg(monkeypatch):
    """read_registry_programs 의 값 읽기 경로를 가짜 winreg 로 확인한다(실제 레지스트리는 읽지 않는다)."""
    keys = {"A": {"DisplayName": "App A", "DisplayVersion": "1", "URLInfoAbout": "https://a.example.com/", "HelpLink": "https://h.example.com/"},
            "B": {"DisplayName": "App B", "HelpLink": "javascript:alert(1)", "URLUpdateInfo": "https://u.example.com/x"},
            "C": {"DisplayName": "App C"}}

    class Key:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    fake = types.ModuleType("winreg")
    fake.HKEY_LOCAL_MACHINE, fake.HKEY_CURRENT_USER = "HKLM", "HKCU"
    fake.KEY_READ = fake.KEY_WOW64_64KEY = fake.KEY_WOW64_32KEY = 0

    def open_key(parent, sub, reserved=0, access=0):
        if isinstance(parent, Key):
            return Key(sub)
        if parent == "HKLM" and access == 0 and sub == detect.UNINSTALL_SUBKEY:
            return Key("root")
        raise OSError(2, "x")
    fake.OpenKey = open_key
    names = list(keys)

    def enum_key(root, i):
        if i >= len(names):
            raise OSError(259, "no more")
        return names[i]
    fake.EnumKey = enum_key

    def query(k, n):
        if n in keys[k.name]:
            return (keys[k.name][n], 1)
        raise OSError(2, "x")
    fake.QueryValueEx = query
    monkeypatch.setitem(sys.modules, "winreg", fake)
    got = {p.name: p.vendor_url for p in detect.read_registry_programs()}
    assert got == {"App A": "https://a.example.com/", "App B": "https://u.example.com/x", "App C": ""}


# --------------------------------------------------------------------------- 패키지 왕복

def _registry_zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("programs/registry.json", json.dumps(entries))
        z.writestr("manifest.json", json.dumps({"format": 1, "files": [], "rules": [], "manual_warnings": []}))


def test_export_writes_vendor_url_and_new_pc_reads_it(tmp_path):
    from pcmigrate.package import export_package, verify_package
    out = tmp_path / "o.zip"
    export_package([], out, [Program("Acme Studio", "1.0", "Acme", vendor_url="https://acme.example.com/about"), Program("NoUrl", "2")], None)
    m, probs = verify_package(out)
    assert probs == [] and "programs/registry.json" in m["programs_sha256"]  # 해시로 보호되는 파일에 담긴다
    with zipfile.ZipFile(out) as z:
        rows = json.loads(z.read("programs/registry.json"))
    assert {r["name"]: r.get("vendor_url") for r in rows} == {"Acme Studio": "https://acme.example.com/about", "NoUrl": ""}
    registry, _w, _t = load_package_programs(out)
    assert {p.name: p.vendor_url for p in registry} == {"Acme Studio": "https://acme.example.com/about", "NoUrl": ""}


def test_old_package_without_field_still_reads(tmp_path):
    z = tmp_path / "old.zip"
    _registry_zip(z, [{"name": "Old App", "version": "1", "publisher": "P"}])
    registry, _w, _t = load_package_programs(z)
    assert [(p.name, p.vendor_url) for p in registry] == [("Old App", "")]


def test_tampered_vendor_url_in_package_is_sanitized_again(tmp_path):
    z = tmp_path / "t.zip"
    _registry_zip(z, [{"name": "A", "vendor_url": "javascript:alert(1)"}, {"name": "B", "vendor_url": "https://u:p@evil.com/"},
                      {"name": "C", "vendor_url": "https://ok.example.com/" + ESC + "[2J"}, {"name": "D", "vendor_url": 5},
                      {"name": "E", "vendor_url": "HTTPS://ok.example.com/x"}, {"name": "F", "vendor_url": "https://x.example.com/" + "a" * 400}])
    registry, _w, _t = load_package_programs(z)
    assert {p.name: p.vendor_url for p in registry} == {"A": "", "B": "", "C": "", "D": "", "E": "https://ok.example.com/x", "F": ""}


# --------------------------------------------------------------------------- 표시

def test_checklist_manual_line_gets_vendor_site_only_when_present():
    with_url = Program("Zeta Modeler 5", "5.1", "Zeta Ltd", vendor_url="https://zeta.example.com/about")
    without = Program("Yotta Draw 3", "3.0", "Yotta Ltd")
    cl = build_checklist([], [with_url, without], [])
    lines = {("Zeta" if "Zeta" in x else "Yotta"): x for x in cl.install if "Zeta" in x or "Yotta" in x}
    assert "제조사 사이트: https://zeta.example.com/about" in lines["Zeta"]
    assert "제조사 사이트" not in lines["Yotta"]
    assert "제조사 사이트" not in describe_manual(without, None)


def test_describe_manual_rejects_unsafe_url_even_if_program_carries_it():
    p = Program("Zeta Modeler 5", "5.1", "Zeta Ltd", vendor_url="https://zeta.example.com/" + ESC + "[31m")
    text = describe_manual(p, None)
    assert "제조사 사이트" not in text and ESC not in text
    p2 = Program("Zeta Modeler 5", "5.1", "Zeta Ltd", vendor_url="javascript:alert(1)")
    assert "제조사 사이트" not in describe_manual(p2, None)


def test_install_check_item_cli_text_and_json_carry_vendor_url():
    old = [Program("Zeta Modeler 5", "5.1", "Zeta Ltd", vendor_url="https://zeta.example.com/about"), Program("Yotta Draw 3", "3.0", "Yotta")]
    r = ic.check_install(old, [], None, ic.NewState())
    items = {i.name: i for i in r.items}
    assert items["Zeta Modeler 5"].vendor_url == "https://zeta.example.com/about" and items["Yotta Draw 3"].vendor_url == ""
    text = cli._install_check_text(r, "p.zip")
    assert text.count("제조사 사이트: https://zeta.example.com/about") == 1 and text.count("제조사 사이트") == 1
    row = [x for x in r.summary_dict()["items"] if x["name"] == "Zeta Modeler 5"][0] if hasattr(r, "summary_dict") else asdict(items["Zeta Modeler 5"])
    assert row["vendor_url"] == "https://zeta.example.com/about" and "reason" in row and "status" in row  # 기존 필드는 그대로


def test_gui_rows_and_detail_text_use_existing_detail_line():
    from pcmigrate.gui import texts as T
    from pcmigrate.gui.controller_install import InstallMixin
    old = [Program("Zeta Modeler 5", "5.1", "Zeta Ltd", vendor_url="https://zeta.example.com/about"), Program("Yotta Draw 3", "3.0", "Yotta")]
    mixin = InstallMixin()
    mixin.state = types.SimpleNamespace(install_check=ic.check_install(old, [], None, ic.NewState()), install_marks=set(), install_picks=set())
    rows = {r["name"]: r for r in mixin.install_rows()}
    assert rows["Zeta Modeler 5"]["vendor_url"] == "https://zeta.example.com/about" and rows["Yotta Draw 3"]["vendor_url"] == ""
    assert T.INST_DETAIL_VENDOR.format(url="https://zeta.example.com/about") == " · 제조사 사이트: https://zeta.example.com/about"
    text = T.INST_DETAIL.format(name="n", status="s", reason="r", hint="", old="", vendor=T.INST_DETAIL_VENDOR.format(url="https://z.example.com/"))
    assert "제조사 사이트: https://z.example.com/" in text


def test_tool_never_opens_the_url():
    """주소는 글자로만 다룬다: 정화·표시 코드는 네트워크/브라우저 모듈을 쓰지 않는다."""
    import inspect
    src = inspect.getsource(detect.sanitize_vendor_url) + inspect.getsource(detect.pick_vendor_url) + inspect.getsource(describe_manual)
    for word in ("urllib", "webbrowser", "requests", "socket", "os.startfile", "subprocess"):
        assert word not in src
