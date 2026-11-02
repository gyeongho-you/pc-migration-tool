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


# ----------------------------------------------------------------------------- Z2: 이름 기반 판정의 빈틈(확장자/낱말/설정 형식 허용 목록)
Z2_FILES = ["server.pem", "id.pfx", "x.p12", "private.key", ".env", "api_key.txt", "apikey.json", "session.dat", "refresh.txt",
            "bearer.cfg", "sso.ini", "key.json", "wallet.dat", "cert.crt", "user.cer", "profile.ovpn", "serial.txt", "activation.xml",
            "hasp.dat", "machine.lic2", "Local State"]


def test_z2_listed_secret_like_files_never_in_paths(fake):
    d = fake.app / "Acme"
    files = [fake.put(d / "settings.ini", "1"), fake.put(d / "author.ini", "1")] + [fake.put(d / n, "1") for n in Z2_FILES]
    s = _rounds(fake, files)
    a, rule, _ = rm.write_draft(s, min_conf=rm.LOW)
    got = {p["path"].rsplit("\\", 1)[-1] for p in rule["paths"]}
    assert got == {"settings.ini", "author.ini"}  # author 는 auth 낱말이 아니므로 제외하지 않는다
    assert all(p["kind"] == "file" for p in rule["paths"])  # 비밀이 든 폴더는 폴더째 담지 않는다
    byname = {c.path.rsplit("\\", 1)[-1]: c for c in a.candidates}
    for n in Z2_FILES:
        assert byname[n].secret or byname[n].nonconfig, n  # 후보 표에는 제외 사유와 함께 남는다
    for n in ("server.pem", "id.pfx", "api_key.txt", "sso.ini", "key.json", "activation.xml", "bearer.cfg", "Local State"):
        assert byname[n].secret, n
    assert "형식" in byname["wallet.dat"].reason or "로그인" in byname["wallet.dat"].reason
    ex = [e.lower() for e in rule["exclude"]]
    for pat in ("*.pem", "*.pfx", "*apikey*", "*session*", "*bearer*", "*refresh*", "local state", "*.ovpn", ".env", "hasp*"):
        assert pat in ex, pat


PFX = "%APPDATA%" + chr(92) + "Acme" + chr(92)


def test_z2_secret_word_logic():
    for n in ("auth.json", "oauth.json", "auth_x.ini", "x_auth.ini", "my-auth.ini", "sso.ini", "key.json"):
        assert rm.secret_reason(PFX + n), n
    for n in ("author.ini", "authors.xml", "keys.ini", "keybindings.json", "lesson.ini", "associations.json", "settings.ini"):
        assert rm.secret_reason(PFX + n) is None, n


def test_z2_non_config_formats_are_excluded_but_listed(fake):
    d = fake.app / "Acme"
    files = [fake.put(d / "a.ini", "1"), fake.put(d / "blob.dat", "1"), fake.put(d / "noext", "1"), fake.put(d / "m.db", "1")]
    s = _rounds(fake, files)
    a, rule, _ = rm.write_draft(s, min_conf=rm.LOW)
    assert {p["path"].rsplit("\\", 1)[-1] for p in rule["paths"]} == {"a.ini"}
    nc = {c.path.rsplit("\\", 1)[-1] for c in a.candidates if c.nonconfig}
    assert nc == {"blob.dat", "noext", "m.db"}
    assert any("설정 형식이 아니라" in c.reason for c in a.candidates if c.nonconfig)


def test_z2_only_non_config_gives_no_draft_with_reason(fake):
    d = fake.app / "Acme"
    s = _rounds(fake, [fake.put(d / "blob.dat", "1")])
    a, rule, _ = rm.write_draft(s, min_conf=rm.LOW)
    assert rule is None and "설정 형식" in rm.no_draft_message(a)


def test_z2_dir_not_bundled_when_clue_extension_inside(fake):
    d = fake.app / "Acme"
    s = _rounds(fake, [fake.put(d / "a.ini", "1"), fake.put(d / "b.ini", "1"), fake.put(d / "server.pem", "1")])
    rule = rm.write_draft(s)[1]
    assert all(p["kind"] == "file" for p in rule["paths"])


LOGIN_NAMES = ["LoginState.xml", "login.json", "account.json", "userinfo.xml", "identity.json", "signin.xml", "authdata.json",
               "passphrase.ini", "SingleSignOn.xml", "AdskIdentityManager.xml", "sign-in.xml", "loginstate.xml", "MyAccount.json", "oauth_state.json"]
NORMAL_NAMES = ["settings.ini", "accounting.xml", "author.ini", "authors.json", "Accountability.xml", "accountant.xml", "singleton.xml",
                "design.json", "signature.xml", "logging.xml", "catalog.xml", "dialog.ini", "preferences.json", "keys.xml"]


BASE = "%APPDATA%" + chr(92) + "Acme" + chr(92)


def test_login_and_account_state_names_are_secret():
    for n in LOGIN_NAMES:
        assert rm.secret_reason(BASE + n), n
    assert rm.secret_reason(BASE + "Identity" + chr(92) + "a.json")  # 폴더 이름 단서도 같다
    assert rm.secret_reason(BASE + "SignIn" + chr(92) + "a.xml")


def test_accidental_matches_are_not_secret():
    for n in NORMAL_NAMES:
        assert rm.secret_reason(BASE + n) is None, n


def test_login_names_never_reach_draft_and_are_listed_as_secret(fake):
    d = fake.app / "Acme"
    ok = fake.put(d / "settings.ini", "1")
    bad = [fake.put(d / n, "1") for n in ("LoginState.xml", "account.json", "SingleSignOn.xml", "AdskIdentityManager.xml")]
    s = _rounds(fake, [ok, *bad])
    a, rule, _out = rm.write_draft(s)
    by = {c.path.rsplit("\\", 1)[-1]: c for c in a.candidates}
    assert all(by[b.name].secret and rm.SECRET_REASON in by[b.name].reason for b in bad)
    assert not by["settings.ini"].secret
    blob = json.dumps(rule["paths"])
    assert "settings.ini" in blob and not any(b.name in blob for b in bad)
    assert not any(c.path.endswith(b.name) for c in a.candidates if not c.secret for b in bad)


def test_login_file_prevents_folder_bundling_and_exclude_globs_cover_it(fake):
    import fnmatch
    d = fake.app / "Acme"
    files = [fake.put(d / n, "1") for n in ("a.ini", "b.xml", "LoginState.xml")]
    s = _rounds(fake, files)
    rule = rm.write_draft(s)[1]
    assert all(p["kind"] == "file" for p in rule["paths"]) and "LoginState.xml" not in json.dumps(rule["paths"])
    for n in ("LoginState.xml", "login.json", "signin.xml", "userinfo.xml", "identity.json", "passphrase.ini", "SingleSignOn.xml",
              "account.json", "AdskIdentityManager.xml"):
        assert any(fnmatch.fnmatch(n.lower(), g.lower()) for g in rm.SECRET_EXCLUDES), n
    assert not any(fnmatch.fnmatch("settings.ini", g.lower()) or fnmatch.fnmatch("accounting.xml", g.lower()) for g in rm.SECRET_EXCLUDES)
