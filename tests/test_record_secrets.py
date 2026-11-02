"""설정 기록 모드 규칙 초안이 로그인/토큰/쿠키/라이선스 파일을 걸러내는지(S1) 시험. 가짜 프로필 + 환경변수 주입만 쓴다."""
import json

import pytest

from pcmigrate import envpaths, record_mode as rm
from test_record_mode import Fake


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    envpaths.set_overrides({"USERPROFILE": str(f.home), "HOME": str(f.home), "APPDATA": str(f.app), "LOCALAPPDATA": str(f.loc),
                            "PROGRAMDATA": str(f.pd)})
    yield f
    envpaths.set_overrides(None)


def _rounds(fake, files):
    s, _ = fake.start("Acme")
    fake.snap(s)
    for v in ("2", "3"):
        for f in files:
            fake.put(f, v)
        fake.snap(s)
    return s


def test_secret_reason_names():
    assert rm.secret_reason("%APPDATA%\\Acme\\token.json")
    assert rm.secret_reason("%APPDATA%\\Acme\\Cookies.sqlite")
    assert rm.secret_reason("%APPDATA%\\Acme\\Auth\\x.dat")
    assert rm.secret_reason("%APPDATA%\\Acme\\license\\lic.dat")
    assert rm.secret_reason("%APPDATA%\\Acme\\Login Data")
    assert rm.secret_reason("%APPDATA%\\Acme\\a.lic")
    assert rm.secret_reason("%APPDATA%\\Acme\\settings.ini") is None


def test_dir_is_not_bundled_when_secret_inside_and_secrets_not_in_paths(fake):
    d = fake.app / "Acme"
    files = [fake.put(d / "settings.ini", "1"), fake.put(d / "keys.ini", "1"), fake.put(d / "token.json", "1"),
             fake.put(d / "cookies.sqlite", "1")]
    s = _rounds(fake, files)
    a, rule, _ = rm.write_draft(s)
    assert rule is not None
    assert all(p["kind"] == "file" for p in rule["paths"])
    got = {p["path"].rsplit("\\", 1)[-1] for p in rule["paths"]}
    assert got == {"settings.ini", "keys.ini"}
    assert any("로그인/라이선스" in w for w in a.warnings)
    assert "token" in json.dumps(rule["exclude"]).lower() and "cookie" in json.dumps(rule["exclude"]).lower()
    secret = [c for c in a.candidates if c.secret]
    assert {c.path.rsplit("\\", 1)[-1] for c in secret} == {"token.json", "cookies.sqlite"}


def test_nested_auth_and_license_folders_never_in_paths(fake):
    d = fake.app / "Acme"
    files = [fake.put(d / "Auth" / "token.json", "1"), fake.put(d / "license" / "lic.dat", "1"),
             fake.put(d / "ui.ini", "1"), fake.put(d / "keys.ini", "1")]
    s = _rounds(fake, files)
    rule = rm.write_draft(s, min_conf=rm.LOW)[1]
    flat = json.dumps(rule["paths"]).lower()
    assert "auth" not in flat and "license" not in flat and "token" not in flat and "lic.dat" not in flat
    assert all(p["kind"] == "file" for p in rule["paths"])


def test_only_secrets_gives_no_draft_with_reason(fake):
    d = fake.app / "Acme"
    s = _rounds(fake, [fake.put(d / "token.json", "1")])
    a, rule, _ = rm.write_draft(s)
    assert rule is None
    assert "로그인/라이선스" in rm.no_draft_message(a)


def test_dir_still_bundled_without_secrets(fake):
    d = fake.app / "Acme"
    s = _rounds(fake, [fake.put(d / "a.ini", "1"), fake.put(d / "b.ini", "1")])
    rule = rm.write_draft(s)[1]
    assert [p["kind"] for p in rule["paths"]] == ["dir"]
