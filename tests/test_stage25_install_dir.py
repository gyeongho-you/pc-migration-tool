"""stage25: 설치 폴더 설정(Program Files 안의 설정 형식 파일) 내보내기/복원. 가짜 환경(tmp_path + 환경변수 덮어쓰기)만 쓴다.

실제 Program Files/Windows 폴더에는 아무것도 쓰지 않는다: %ProgramFiles% 등은 tmp 아래로 바꾼다(envpaths.set_overrides).
"""
import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from pcmigrate import cli, coverage, envpaths, install_dir as idr
from pcmigrate.api import ApiError, Deps, run_export, run_import
from pcmigrate.copier import DENIED, REJECTED, SKIPPED_SAME, WOULD_COPY, WOULD_OVERWRITE
from pcmigrate.detect import Program
from pcmigrate.discover import (Discovery, ProgramEntry, SelectionError, build_selection, items_from_selection)
from pcmigrate.find_program import find_program, render
from pcmigrate.package import (PackageError, apply_import, export_package, plan_import, verify_package)
from conftest import make_rules_dir


def touch(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


class Fake:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.pf = tmp / "Program Files"
        self.pf86 = tmp / "Program Files (x86)"
        self.win = tmp / "Windows"
        self.home = tmp / "someuser-home"
        self.app = self.pf / "Acme Paint"
        for p in (self.pf, self.pf86, self.win, self.home):
            p.mkdir(parents=True, exist_ok=True)
        self.env = {"ProgramFiles": str(self.pf), "ProgramFiles(x86)": str(self.pf86), "ProgramW6432": str(self.pf),
                    "SystemRoot": str(self.win), "windir": str(self.win), "USERPROFILE": str(self.home),
                    "PROGRAMDATA": str(tmp / "PD"), "APPDATA": str(self.home / "AppData" / "Roaming"),
                    "LOCALAPPDATA": str(self.home / "AppData" / "Local")}
        envpaths.set_overrides(self.env)

    def cli(self, *args):
        """cli.main 은 --env 로 덮어쓰기를 다시 정하므로 가짜 루트를 매번 넘긴다(실제 Program Files 를 건드리지 않게)."""
        pre = [x for k, v in self.env.items() for x in ("--env", f"{k}={v}")]
        return cli.main(pre + list(args))

    def acme_tree(self):
        a = self.app
        touch(a / "settings.ini", "[a]\nx=1\n")
        touch(a / "user.cuix", b"cuix")
        touch(a / "acme.pgp", b"pgp")
        touch(a / "options.json", b"{}")
        touch(a / "tool.exe", b"MZ")
        touch(a / "core.dll", b"MZ")
        touch(a / "run.bat", b"echo")
        touch(a / "acme.lic", b"LIC")
        touch(a / "license.xml", b"<l/>")
        touch(a / "api_token.json", b"{}")
        touch(a / "sub" / "ui.cfg", b"cfg")
        touch(a / "sub" / "deep" / "x.ini", b"deep")
        touch(a / "logs" / "a.ini", b"log")
        touch(a / "big.xml", b"x" * (6 * 1024 * 1024))
        return a

    def prog(self, name="Acme Paint", version="1.0", loc=None):
        return Program(name, version, "Acme Corp", install_location=str(self.app if loc is None else loc))


@pytest.fixture
def fake(tmp_path):
    f = Fake(tmp_path)
    yield f
    envpaths.set_overrides(None)


def item(fake, rel, program="Acme Paint", root="%ProgramFiles%", folder="Acme Paint"):
    return idr.InstallItem(program, root, folder, rel, fake.pf.joinpath(*rel.split("/")))


def export(fake, items, programs=None, out=None):
    out = out or fake.tmp / "out" / "pkg.zip"
    m = export_package([], out, programs if programs is not None else [fake.prog()], None, install_items=items)
    return out, m


def sel_names(manifest):
    return sorted(f["rel"] for f in manifest["files"] if f["rule"] == "install")


# --------------------------------------------------------------------------- 확장자/이름 규칙

def test_settings_extension_table_is_one_place_with_grades():
    assert set(idr.INSTALL_SETTINGS_EXTENSIONS.values()) <= {"A", "B"}
    from pcmigrate.package import DENY_EXTENSIONS
    assert not (set(idr.INSTALL_SETTINGS_EXTENSIONS) & DENY_EXTENSIONS)  # 허용 목록과 거부 목록은 겹치지 않는다
    for ext in (".ini", ".json", ".xml", ".cfg", ".config", ".conf", ".pgp", ".cui", ".cuix", ".properties", ".yaml", ".yml", ".toml"):
        assert ext in idr.INSTALL_SETTINGS_EXTENSIONS


@pytest.mark.parametrize("name,ok", [
    ("a.ini", True), ("A.CUIX", True), ("x.config", True), ("tool.exe", False), ("core.dll", False), ("run.bat", False),
    ("s.ps1", False), ("m.js", False), ("a.vbs", False), ("a.lnk", False), ("a.msi", False), ("a.lic", False),
    ("license.xml", False), ("Licenses.json", False), ("eula.ini", False), ("app.log.ini", False), ("cache.json", False),
    ("a.dat", False), ("noext", False), ("a.ini ", True), ("a.ini.exe", False),
])
def test_is_settings_name(name, ok):
    assert idr.is_settings_name(name) is ok


def test_secret_hints_by_name_and_content(tmp_path):
    assert idr.name_secret_hints("settings.ini") == []
    for n in ("password.ini", "api_token.json", "client_secret.json", "key.xml", "credentials.cfg", "my-keys.json"):
        assert idr.name_secret_hints(n), n
    assert idr.name_secret_hints("keybindings.json") == []  # 낱말 단위: keyboard/keybindings 는 걸리지 않는다
    f = touch(tmp_path / "a.ini", "[db]\nPassword=hunter2\n")
    h = idr.secret_hints(f)
    assert h and "hunter2" not in " ".join(h)  # 값은 보여 주지 않는다
    assert idr.secret_hints(touch(tmp_path / "b.ini", "[a]\nx=1\n")) == []


# --------------------------------------------------------------------------- 찾기: 체크 가능한 후보

def test_find_makes_install_candidates_unchecked_with_tokens(fake):
    fake.acme_tree()
    res = find_program(fake.prog(), [], envpaths.expand_env)
    names = sorted(os.path.basename(x["path"]) for x in res.install_files)
    assert names == ["acme.pgp", "api_token.json", "options.json", "settings.ini", "ui.cfg", "user.cuix"]  # exe/dll/bat/lic/license/큰 파일/깊은/logs 제외
    assert [os.path.basename(x["path"]) for x in res.install_excluded] == ["big.xml"]
    cands = res.install_candidates
    assert len(cands) == 6 and all(c.is_install and c.badge == "설치 폴더 설정" for c in cands)
    assert all(c.root == "%ProgramFiles%" and c.install_folder == "Acme Paint" and c.rel.startswith("Acme Paint/") for c in cands)
    assert not any(str(fake.tmp) in c.root + c.rel for c in cands)  # 토큰 경로(실제 경로를 저장하지 않음)
    assert [bool(c.secret) for c in cands if c.rel.endswith("api_token.json")] == [True]
    assert all(not c.secret for c in cands if c.rel.endswith("settings.ini"))
    text = render(res)
    assert "설치 폴더 설정 후보 6개" in text and "--allow-install-dir" in text and "크기 상한으로 제외" in text and "big.xml" in text


def test_find_outside_program_files_uses_install_location_token(fake):
    other = fake.tmp / "D" / "Tools" / "Zed"
    touch(other / "z.ini", "a")
    p = Program("Zed Editor", "2", "Zed", install_location=str(other))
    res = find_program(p, [], envpaths.expand_env)
    assert [c.root for c in res.install_candidates] == ["@InstallLocation:Zed Editor"]
    assert res.install_candidates[0].rel == "z.ini" and res.install_candidates[0].install_folder == ""


def test_find_install_location_too_broad_gives_no_candidate(fake):
    touch(fake.win / "w.ini", "a")
    p = Program("Win Thing", "1", "x", install_location=str(fake.win))
    assert find_program(p, [], envpaths.expand_env).install_candidates == []
    touch(fake.pf / "root.ini", "a")
    p2 = Program("PF Thing", "1", "x", install_location=str(fake.pf))
    assert find_program(p2, [], envpaths.expand_env).install_candidates == []


def test_find_guesses_folder_under_program_files_when_no_install_location(fake):
    touch(fake.pf / "AcmePaint" / "s.ini", "a")
    p = Program("Acme Paint", "1", "Acme", install_location="")
    res = find_program(p, [], envpaths.expand_env)
    assert [c.rel for c in res.install_candidates] == ["AcmePaint/s.ini"]
    assert res.install_candidates[0].link.grade == "추정"


# --------------------------------------------------------------------------- 선택 파일 형식(호환)

def test_selection_has_separate_install_dir_list_and_old_reader_ignores_it(fake):
    fake.acme_tree()
    res = find_program(fake.prog(), [], envpaths.expand_env)
    sel = build_selection(res.as_discovery())
    assert sel["format"] == 1 and all(c.get("kind") == "install_dir" for c in sel["install_dir"])
    assert len(sel["install_dir"]) == 6 and all(c["include"] is False for c in sel["install_dir"])
    assert all(c["root"] != "%ProgramFiles%" for c in sel["candidates"])  # 일반 후보 목록에는 섞이지 않는다
    sel["install_dir"][0]["include"] = True
    assert items_from_selection(sel) == []  # 이전 버전(이 키를 모름)은 설치 폴더 항목을 무시한다
    got = idr.install_items_from_selection(sel)
    assert len(got) == 1 and got[0].root == "%ProgramFiles%"
    # 이전 버전 선택 파일(키 없음)도 읽는다
    assert idr.install_items_from_selection({"format": 1, "candidates": []}) == []


@pytest.mark.parametrize("bad", [
    {"root": "%SystemRoot%", "folder": "x", "rel": "x/a.ini"},
    {"root": "%ProgramFiles%", "folder": "Acme", "rel": "Acme/../../Windows/a.ini"},
    {"root": "%ProgramFiles%", "folder": "Acme", "rel": "Acme/a.ini:evil"},
    {"root": "%ProgramFiles%", "folder": "Acme", "rel": "Acme\\a.ini"},
    {"root": "%ProgramFiles%", "folder": "Acme", "rel": "Acme/CON.ini"},
    {"root": "%ProgramFiles%", "folder": "Acme", "rel": "Other/a.ini"},
    {"root": "%ProgramFiles%", "folder": "", "rel": "a.ini"},
    {"root": "%ProgramData%", "folder": "Acme", "rel": "Acme/a.ini"},
    {"root": "C:\\Windows", "folder": "", "rel": "a.ini"},
])
def test_selection_rejects_unsafe_install_entries(fake, bad):
    ent = {"include": True, "program": "Acme", **bad}
    with pytest.raises(SelectionError):
        idr.install_items_from_selection({"format": 1, "candidates": [], "install_dir": [ent]})


# --------------------------------------------------------------------------- 내보내기

def test_export_collects_only_allowed_settings_and_records_tokens(fake):
    fake.acme_tree()
    rels = ["settings.ini", "user.cuix", "tool.exe", "core.dll", "run.bat", "acme.lic", "license.xml", "big.xml", "sub/deep/x.ini",
            "sub/ui.cfg", "logs/a.ini", "missing.ini"]
    out, m = export(fake, [item(fake, f"Acme Paint/{r}") for r in rels])
    assert sel_names(m) == ["Acme Paint/settings.ini", "Acme Paint/sub/ui.cfg", "Acme Paint/user.cuix"]
    for f in m["files"]:
        assert f["rule"] == "install" and f["base"] == "%ProgramFiles%" and f["arc"].startswith("data/install/pf/")
        assert len(f["sha256"]) == 64 and f["size"] > 0
    skipped = {os.path.basename(s["path"].replace("\\", "/")): s for s in m["skipped"] if s["rule"] == "install"}
    assert skipped["tool.exe"].get("denied") and skipped["core.dll"].get("denied") and skipped["run.bat"].get("denied")
    assert skipped["acme.lic"].get("denied") and skipped["license.xml"].get("denied")
    assert "너무 큼" in skipped["big.xml"]["reason"] and "깊은" in skipped["x.ini"]["reason"]
    assert skipped["missing.ini"]["reason"] == "경로가 없음"
    blob = json.dumps(m, ensure_ascii=False)
    assert str(fake.tmp) not in blob and "someuser" not in blob  # 실제(사용자 이름이 든) 경로가 manifest 에 없다
    info = m["install_dir"]
    assert info["programs"][0]["program"] == "Acme Paint" and info["programs"][0]["version"] == "1.0"
    assert info["programs"][0]["root"] == "%ProgramFiles%" and info["programs"][0]["folder"] == "Acme Paint" and "version_warning" in info
    rows = [r for r in coverage.rows(m) if r["what"] == coverage.INSTALL_DIR_WHAT]
    assert len(rows) == 1 and rows[0]["program"] == "Acme Paint" and rows[0]["files"] == 3
    assert coverage.INSTALL_DIR_NOTE in rows[0]["note"]
    assert any(w["rule"] == "install" for w in m["manual_warnings"])
    mf, problems = verify_package(out)
    assert not problems
    # 원본은 그대로
    assert (fake.app / "settings.ini").read_bytes() == b"[a]\nx=1\n" and (fake.app / "tool.exe").exists()


def test_export_total_limits_exclude_and_report(fake, monkeypatch):
    monkeypatch.setattr(idr, "INSTALL_PROGRAM_MAX_FILES", 2)
    for n in "abc":
        touch(fake.app / f"{n}.ini", "x")
    out, m = export(fake, [item(fake, f"Acme Paint/{n}.ini") for n in "abc"])
    assert len(sel_names(m)) == 2
    assert any("상한" in s["reason"] for s in m["skipped"] if s["rule"] == "install")


def test_export_refuses_link_chain_and_symlinks(fake):
    fake.acme_tree()
    real = fake.tmp / "elsewhere"
    touch(real / "x.ini", "outside")
    link = fake.app / "linked"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True)
        if r.returncode != 0:
            pytest.skip("링크를 만들 수 없는 환경")
    out, m = export(fake, [item(fake, "Acme Paint/linked/x.ini")])
    assert sel_names(m) == []
    assert any("링크" in s["reason"] for s in m["skipped"])


def test_export_loc_root_resolves_by_program_name(fake):
    other = fake.tmp / "D" / "Zed"
    touch(other / "z.ini", "z")
    prog = Program("Zed Editor", "2.5", "Zed", install_location=str(other))
    it = idr.InstallItem("Zed Editor", "@InstallLocation:Zed Editor", "", "z.ini", Path("z.ini"))
    out, m = export(fake, [it], [prog])
    assert sel_names(m) == ["z.ini"] and m["files"][0]["base"] == "@InstallLocation:Zed Editor"
    assert m["files"][0]["arc"].startswith("data/install/loc-")
    assert str(fake.tmp) not in json.dumps(m, ensure_ascii=False)
    # 프로그램 목록에 없으면 담지 않는다
    out2, m2 = export(fake, [it], [], out=fake.tmp / "out" / "p2.zip")
    assert sel_names(m2) == []


def test_export_prepare_requires_allow_secrets_for_secret_names_and_content(fake, tmp_path):
    fake.acme_tree()
    touch(fake.app / "db.ini", "[x]\npassword = abc\n")
    rules = make_rules_dir(tmp_path / "rules")
    base_kw = dict(deps=Deps(registry=lambda: [fake.prog()], winget_export=lambda: None, winget_list=lambda: None))
    for rel in ("api_token.json", "db.ini"):
        with pytest.raises(ApiError, match="비밀 정보"):
            run_export(rules, tmp_path / "o1.zip", install_items=[item(fake, f"Acme Paint/{rel}")], **base_kw)
    r = run_export(rules, tmp_path / "o2.zip", install_items=[item(fake, "Acme Paint/api_token.json")], allow_secrets=True, **base_kw)
    assert r.extras.risky == 1 and sel_names(r.manifest) == ["Acme Paint/api_token.json"]
    ok = run_export(rules, tmp_path / "o3.zip", install_items=[item(fake, "Acme Paint/settings.ini")], **base_kw)
    assert ok.extras.risky == 0 and any(r["what"] == coverage.INSTALL_DIR_WHAT for r in coverage.rows(ok.manifest))


def test_export_from_selection_file_round_trip_via_cli(fake, tmp_path, monkeypatch, capsys):
    fake.acme_tree()
    rules = make_rules_dir(tmp_path / "rules")
    monkeypatch.setattr(cli, "_deps", lambda: Deps(registry=lambda: [fake.prog()], winget_export=lambda: None, winget_list=lambda: None))
    sel_path = tmp_path / "sel.json"
    assert fake.cli("discover", "--program", "Acme Paint", "--rules", str(rules), "--output-selection", str(sel_path)) == 0
    sel = json.loads(sel_path.read_text(encoding="utf-8"))
    assert len(sel["install_dir"]) == 6 and not any(c["include"] for c in sel["install_dir"])
    for c in sel["install_dir"]:
        if c["rel"].endswith(("settings.ini", "user.cuix")):
            c["include"] = True
    sel_path.write_text(json.dumps(sel), encoding="utf-8")
    out = tmp_path / "x.zip"
    assert fake.cli("export", "--rules", str(rules), "--output", str(out), "--extra", str(sel_path)) == 0
    m, problems = verify_package(out)
    assert not problems and sel_names(m) == ["Acme Paint/settings.ini", "Acme Paint/user.cuix"]
    err = capsys.readouterr().err
    assert "설치 폴더 설정 2개를 담았습니다" in err and "같은 버전" in err


def test_fingerprint_unchanged_without_install_and_changes_with_it(fake):
    from pcmigrate.package import _fingerprint
    fp0, i0 = _fingerprint([], None, "auto")
    assert "install" not in i0 and fp0 == _fingerprint([], None, "auto", False, install_items=None)[0]
    fp1, i1 = _fingerprint([], None, "auto", False, install_items=[item(fake, "Acme Paint/a.ini")])
    fp2, _ = _fingerprint([], None, "auto", False, install_items=[item(fake, "Acme Paint/b.ini")])
    assert fp1 != fp0 and fp1 != fp2 and i1["install"] == [["%ProgramFiles%", "Acme Paint/a.ini"]]


# --------------------------------------------------------------------------- 복원

def make_pkg(fake, files, declared=None, name="evil.zip", extra_manifest=None):
    """검증을 통과하는(해시가 맞는) 가짜 패키지. files = [(arc, rule, base, rel, bytes)]."""
    entries, man_files = {}, []
    for arc, rule, base, rel, data in files:
        entries[arc] = data
        man_files.append({"arc": arc, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "rule": rule, "base": base,
                          "rel": rel, "method": "stored"})
    manifest = {"format": 1, "tool_version": "t", "created": "x", "compress": "auto", "rules": [], "files": man_files, "skipped": [],
                "failed": [], "coverage": [], "programs": {}, "manual_warnings": []}
    if declared is not None:
        manifest["install_dir"] = {"programs": declared}
    manifest.update(extra_manifest or {})
    path = fake.tmp / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for arc, data in entries.items():
            zf.writestr(arc, data)
        zf.writestr("manifest.json", json.dumps(manifest))
    return path


DECL = [{"program": "Acme Paint", "version": "1.0", "root": "%ProgramFiles%", "folder": "Acme Paint", "files": 1, "bytes": 1}]


def pkg_for(fake, rels, data=b"new", declared=None, name="p.zip"):
    return make_pkg(fake, [(f"data/install/pf/{r}", "install", "%ProgramFiles%", r, data) for r in rels],
                    declared if declared is not None else DECL, name)


def plan(fake, path, allow=True, programs=None, write_check=lambda d: True):
    progs = [fake.prog()] if programs is None else programs
    return plan_import(path, [], allow_install_dir=allow, programs=progs, write_check=write_check)


def test_import_default_is_off_and_never_writes(fake):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/settings.ini", "Acme Paint/new.ini"])
    before = (fake.app / "settings.ini").read_bytes()
    p = plan(fake, pkg, allow=False)
    assert not p.problems and p.items == [] and p.install_allowed is False
    assert sorted(v["status"] for v in p.install) == [WOULD_COPY, WOULD_OVERWRITE]
    rep = apply_import(pkg, p)
    assert rep.results == [] and (fake.app / "settings.ini").read_bytes() == before and not (fake.app / "new.ini").exists()


def test_import_allowed_writes_with_bak_and_hash_check(fake):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/settings.ini", "Acme Paint/new.ini", "Acme Paint/sub/ui.cfg"], data=b"NEWDATA")
    p = plan(fake, pkg)
    assert not p.problems and p.install_allowed and len(p.items) == 3
    assert all(it["install"] for it in p.items) and {it["status"] for it in p.items} == {WOULD_COPY, WOULD_OVERWRITE}
    ow = [it for it in p.items if it["status"] == WOULD_OVERWRITE]
    assert ow and all(it["backup"] for it in ow)  # 미리보기에 덮어쓸 목록과 .bak 경로
    rep = apply_import(pkg, p)
    assert not rep.failures
    assert (fake.app / "settings.ini").read_bytes() == b"NEWDATA" and (fake.app / "new.ini").read_bytes() == b"NEWDATA"
    baks = [x for x in fake.app.iterdir() if ".bak" in x.name]
    assert baks and baks[0].read_bytes() == b"[a]\nx=1\n"  # 기존 파일은 .bak 으로 보존
    # 같은 내용이면 건너뜀
    again = plan(fake, pkg)
    assert {it["status"] for it in again.items} == {SKIPPED_SAME}


def test_import_missing_program_folder_is_skipped_with_guidance(fake):
    p = plan(fake, pkg_for(fake, ["Acme Paint/a.ini"]))  # 설치 폴더가 아예 없음
    assert not p.problems and p.items == []
    assert p.install[0]["status"] == idr.MISSING_DIR and idr.INSTALL_FIRST_NOTE in p.install[0]["note"]
    assert not (fake.pf / "Acme Paint").exists()  # 폴더를 만들지 않는다
    fake.app.mkdir()
    p2 = plan(fake, pkg_for(fake, ["Acme Paint/newdir/a.ini"]))  # 하위 폴더가 없어도 만들지 않는다
    assert p2.items == [] and p2.install[0]["status"] == idr.MISSING_DIR


def test_import_program_not_installed_on_new_pc(fake):
    fake.app.mkdir()
    p = plan(fake, pkg_for(fake, ["Acme Paint/a.ini"]), programs=[])
    assert p.items == [] and p.install[0]["status"] == idr.MISSING_PROGRAM


def test_import_blocks_when_not_writable_and_writes_nothing(fake):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/settings.ini", "Acme Paint/new.ini"], data=b"NEW")
    p = plan(fake, pkg, write_check=lambda d: False)
    assert idr.NEED_ADMIN_MESSAGE in p.problems and "관리자 권한" in p.problems[0] and p.items == []
    with pytest.raises(PackageError):
        apply_import(pkg, p)
    assert (fake.app / "settings.ini").read_bytes() == b"[a]\nx=1\n" and not (fake.app / "new.ini").exists()
    # 허용하지 않았으면 권한은 점검하지 않는다(쓰지 않으므로)
    assert plan(fake, pkg, allow=False, write_check=lambda d: False).problems == []


def test_dir_writable_probe_and_nonprobe(fake, tmp_path):
    d = tmp_path / "w"
    d.mkdir()
    assert idr.dir_writable(d, probe=True) and idr.dir_writable(d, probe=False)
    assert list(d.iterdir()) == []  # 점검용 임시 파일은 남지 않는다
    assert not idr.dir_writable(tmp_path / "nope", probe=True)


def test_dir_writable_nonprobe_checks_admin_only_for_real_program_files(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(idr, "is_admin", lambda: False)
    d = tmp_path / "w2"
    d.mkdir()
    assert idr.dir_writable(d, probe=False)  # 가짜 루트: 관리자 휴리스틱을 쓰지 않는다
    real = tmp_path / "RealPF"
    (real / "App").mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles", str(real))
    assert not idr.dir_writable(real / "App", probe=False)  # 실제 Program Files 아래 + 관리자 아님 = 쓸 수 없음
    monkeypatch.setattr(idr, "is_admin", lambda: True)
    assert idr.dir_writable(real / "App", probe=False)


# --------------------------------------------------------------------------- 보안: 변조 패키지

def blocked_or_denied(p, rel_entry=None):
    """쓰기 계획이 하나도 없어야 한다(문제로 막히거나 항목별 거부)."""
    return p.problems or all(it["status"] in (DENIED, REJECTED) for it in p.items)


@pytest.mark.parametrize("rel", ["Acme Paint/tool.exe", "Acme Paint/core.dll", "Acme Paint/run.bat", "Acme Paint/s.ps1", "Acme Paint/a.vbs",
                                 "Acme Paint/a.lnk", "Acme Paint/a.msi", "Acme Paint/a.js", "Acme Paint/acme.lic", "Acme Paint/license.xml",
                                 "Acme Paint/a.dat", "Acme Paint/noext", "Acme Paint/a.ini.exe", "Acme Paint/a.INI.DLL"])
def test_tampered_exe_dll_scripts_license_denied(fake, rel):
    fake.acme_tree()
    pkg = pkg_for(fake, [rel, "Acme Paint/ok.ini"], data=b"MZ evil")
    p = plan(fake, pkg)
    by = {os.path.basename(v["target"]): v for v in p.install}
    assert by[os.path.basename(rel)]["status"] == DENIED
    rep = apply_import(pkg, p)
    assert not (fake.app / os.path.basename(rel)).read_bytes() == b"MZ evil" if (fake.app / os.path.basename(rel)).exists() else True
    assert (fake.app / "ok.ini").read_bytes() == b"MZ evil"  # 정상 설정 파일은 복원된다(패키지 전체를 막지 않음)
    assert any(r.status == DENIED for r in rep.results)


def test_tampered_does_not_overwrite_existing_exe(fake):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/tool.exe"], data=b"EVIL")
    apply_import(pkg, plan(fake, pkg))
    assert (fake.app / "tool.exe").read_bytes() == b"MZ"


@pytest.mark.parametrize("rel", [
    "Acme Paint/../Windows/system.ini",   # 상위 경로
    "../Windows/win.ini",
    "Acme Paint/a.ini:evil.exe",          # 대체 데이터 스트림
    "Acme Paint\\..\\x.ini",
    "/Windows/a.ini",
    "Acme Paint/CON.ini", "Acme Paint/aux", "Acme Paint/nul.ini",   # 예약 이름
    "Acme Paint/a.ini.",                  # 끝의 점
])
def test_tampered_paths_blocked_before_any_write(fake, rel):
    fake.acme_tree()
    pkg = make_pkg(fake, [("data/install/pf/x", "install", "%ProgramFiles%", rel, b"E")], DECL)
    mf, problems = verify_package(pkg)
    if problems:  # 검증 단계에서 막힘
        p = plan(fake, pkg)
        assert p.problems and p.items == []
        return
    p = plan(fake, pkg)
    assert blocked_or_denied(p)
    assert not list(fake.win.iterdir())


def test_tampered_other_program_folder_denied(fake):
    fake.acme_tree()
    other = fake.pf / "Other App"
    touch(other / "cfg.ini", "orig")
    decl = [{"program": "Acme Paint", "root": "%ProgramFiles%", "folder": "Other App", "version": "1"}]
    pkg = pkg_for(fake, ["Other App/cfg.ini"], data=b"EVIL", declared=decl)
    p = plan(fake, pkg)
    assert p.items and all(it["status"] == DENIED for it in p.items)  # 새 PC 의 Acme Paint 설치 폴더(InstallLocation)가 아님
    apply_import(pkg, p)
    assert (other / "cfg.ini").read_bytes() == b"orig"


def test_tampered_undeclared_folder_denied(fake):
    fake.acme_tree()
    touch(fake.pf / "Other App" / "cfg.ini", "orig")
    pkg = pkg_for(fake, ["Other App/cfg.ini"], data=b"EVIL", declared=DECL)  # 선언된 폴더는 Acme Paint 뿐
    p = plan(fake, pkg)
    assert all(it["status"] == DENIED for it in p.items)
    apply_import(pkg, p)
    assert (fake.pf / "Other App" / "cfg.ini").read_bytes() == b"orig"


def test_tampered_without_declared_programs_denied(fake):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/x.ini"], data=b"EVIL", declared=[])
    p = plan(fake, pkg)
    assert all(it["status"] == DENIED for it in p.items) and not (fake.app / "x.ini").exists()


@pytest.mark.parametrize("folder,rel", [
    ("Common Files", "Common Files/x.ini"), ("WindowsApps", "WindowsApps/x.ini"), ("Windows Defender", "Windows Defender/x.ini"),
    ("WindowsPowerShell", "WindowsPowerShell/x.ini"), ("Internet Explorer", "Internet Explorer/x.ini"),
])
def test_tampered_protected_windows_folders_denied(fake, folder, rel):
    (fake.pf / folder).mkdir()
    decl = [{"program": "Acme Paint", "root": "%ProgramFiles%", "folder": folder, "version": "1"}]
    pkg = pkg_for(fake, [rel], data=b"EVIL", declared=decl)
    p = plan(fake, pkg, programs=[Program("Acme Paint", "1", "A", install_location=str(fake.pf / folder))])
    assert p.items and all(it["status"] == DENIED for it in p.items)
    apply_import(pkg, p)
    assert not (fake.pf / rel).exists()


def test_tampered_file_directly_in_program_files_root_denied(fake):
    decl = [{"program": "Acme Paint", "root": "%ProgramFiles%", "folder": "Acme Paint", "version": "1"}]
    pkg = pkg_for(fake, ["x.ini"], data=b"EVIL", declared=decl)
    p = plan(fake, pkg)
    assert all(it["status"] == DENIED for it in p.items) and not (fake.pf / "x.ini").exists()


@pytest.mark.parametrize("base", ["%SystemRoot%", "%windir%", "C:\\Windows", "%ProgramData%", "%USERPROFILE%", "%APPDATA%", "%ProgramW6432%",
                                  "@InstallLocation:", "@InstallLocation", "%ProgramFiles%\\Acme Paint", "..", ""])
def test_tampered_unknown_install_root_blocks_package(fake, base):
    pkg = make_pkg(fake, [("data/install/x/a.ini", "install", base, "Acme Paint/a.ini", b"E")], DECL)
    p = plan(fake, pkg)
    assert p.problems and p.items == []
    with pytest.raises(PackageError):
        apply_import(pkg, p)
    assert not list(fake.win.iterdir()) and not (fake.app / "a.ini").exists()


def test_tampered_install_location_root_for_unknown_program_or_broad_dir(fake):
    base = "@InstallLocation:Ghost"
    pkg = make_pkg(fake, [("data/install/loc-x/a.ini", "install", base, "a.ini", b"E")],
                   [{"program": "Ghost", "root": base, "folder": "", "version": "1"}])
    p = plan(fake, pkg, programs=[])
    assert p.items == [] and p.install[0]["status"] == idr.MISSING_PROGRAM
    # 새 PC 의 같은 이름 프로그램 InstallLocation 이 Windows 폴더/드라이브 루트/Program Files 자체이면 쓰지 않는다
    for loc in (fake.win, Path(fake.win.anchor), fake.pf):
        p2 = plan(fake, pkg, programs=[Program("Ghost", "1", "g", install_location=str(loc))])
        assert p2.items == [], loc
    assert not list(fake.win.iterdir())


def test_install_location_root_restores_when_program_present(fake):
    tools = fake.tmp / "D" / "Zed"
    tools.mkdir(parents=True)
    base = "@InstallLocation:Zed"
    pkg = make_pkg(fake, [("data/install/loc-x/z.ini", "install", base, "z.ini", b"Z")],
                   [{"program": "Zed", "root": base, "folder": "", "version": "1"}])
    p = plan(fake, pkg, programs=[Program("Zed", "1", "z", install_location=str(tools))])
    apply_import(pkg, p)
    assert (tools / "z.ini").read_bytes() == b"Z"


def test_junction_in_program_folder_is_rejected(fake):
    fake.acme_tree()
    real = fake.tmp / "elsewhere"
    real.mkdir()
    link = fake.app / "linked"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True)
        if r.returncode != 0:
            pytest.skip("링크를 만들 수 없는 환경")
    pkg = pkg_for(fake, ["Acme Paint/linked/a.ini"], data=b"EVIL")
    p = plan(fake, pkg)
    assert p.items and all(it["status"] == REJECTED for it in p.items)
    rep = apply_import(pkg, p)
    assert not (real / "a.ini").exists() and rep.failures


def test_install_items_cannot_be_applied_without_consent_even_if_plan_edited(fake):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/new.ini"])
    p = plan(fake, pkg)
    p.install_allowed = False  # 계획을 손으로 바꿔도
    with pytest.raises(PackageError):
        apply_import(pkg, p)
    assert not (fake.app / "new.ini").exists()


def test_recheck_before_write_blocks_swapped_extension_and_missing_dir(fake):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/new.ini"])
    p = plan(fake, pkg)
    p.items[0]["target"] = str(fake.app / "evil.exe")  # 계획이 쓰기 직전에 바뀐 경우
    rep = apply_import(pkg, p)
    assert rep.failures and not (fake.app / "evil.exe").exists()
    p = plan(fake, pkg)
    p.items[0]["target"] = str(fake.app / "nodir" / "new.ini")
    rep = apply_import(pkg, p)
    assert rep.failures and not (fake.app / "nodir").exists()


def test_tampered_manifest_shape_does_not_crash(fake):
    for decl in ("x", [1, 2], [{"program": 5}], {"programs": "x"}):
        pkg = make_pkg(fake, [("data/install/pf/a", "install", "%ProgramFiles%", "Acme Paint/a.ini", b"E")], decl)
        p = plan(fake, pkg)
        assert not p.items or all(it["status"] == DENIED for it in p.items)


# --------------------------------------------------------------------------- api/CLI 흐름

def deps_for(fake):
    return Deps(registry=lambda: [fake.prog()], winget_export=lambda: None, winget_list=lambda: None)


def test_run_import_default_no_restore_and_allow_flag(fake, tmp_path, monkeypatch):
    fake.acme_tree()
    rules = make_rules_dir(tmp_path / "rules")
    monkeypatch.setattr(idr, "dir_writable", lambda d, probe=False, expand=None: True)
    pkg = pkg_for(fake, ["Acme Paint/new.ini", "Acme Paint/settings.ini"], data=b"NEW")
    r0 = run_import(pkg, rules, yes=True, deps=deps_for(fake))
    assert r0.mode == "applied" and r0.n_install == 2 and not r0.install_allowed and not (fake.app / "new.ini").exists()
    assert r0.preview_dict()["install_dir"]["allowed"] is False
    pd = run_import(pkg, rules, dry_run=True, allow_install_dir=True, deps=deps_for(fake)).preview_dict()
    assert pd["install_dir"]["allowed"] and pd["install_dir"]["counts"] == {"would_copy": 1, "would_overwrite": 1}
    assert "버전이 다르면" in pd["install_dir"]["version_warning"]
    assert not (fake.app / "new.ini").exists()  # dry-run 은 쓰지 않는다
    r1 = run_import(pkg, rules, yes=True, allow_install_dir=True, deps=deps_for(fake))
    assert r1.mode == "applied" and (fake.app / "new.ini").read_bytes() == b"NEW"


def test_run_import_not_writable_is_blocked_with_admin_message(fake, tmp_path, monkeypatch):
    fake.acme_tree()
    rules = make_rules_dir(tmp_path / "rules")
    monkeypatch.setattr(idr, "dir_writable", lambda d, probe=False, expand=None: False)
    pkg = pkg_for(fake, ["Acme Paint/new.ini"], data=b"NEW")
    r = run_import(pkg, rules, yes=True, allow_install_dir=True, deps=deps_for(fake))
    assert r.mode == "blocked" and "관리자 권한으로 다시 실행해 주세요" in " ".join(r.plan.problems)
    assert r.report is None and not (fake.app / "new.ini").exists()


def test_cli_import_flag_and_messages(fake, tmp_path, monkeypatch, capsys):
    fake.acme_tree()
    rules = make_rules_dir(tmp_path / "rules")
    monkeypatch.setattr(cli, "_deps", lambda: deps_for(fake))
    monkeypatch.setattr(idr, "dir_writable", lambda d, probe=False, expand=None: True)
    pkg = pkg_for(fake, ["Acme Paint/new.ini"], data=b"NEW")
    assert fake.cli("import", str(pkg), "--rules", str(rules), "--dry-run") == 0
    cap = capsys.readouterr()
    assert "설치 폴더 설정 1개" in cap.out and "--allow-install-dir" in cap.out
    assert "같은 버전의 프로그램에서 가져온 것입니다" in cap.err and "복원하지 않습니다" in cap.err
    assert fake.cli("import", str(pkg), "--rules", str(rules), "--yes") == 0
    assert not (fake.app / "new.ini").exists()
    capsys.readouterr()
    assert fake.cli("import", str(pkg), "--rules", str(rules), "--yes", "--allow-install-dir") == 0
    assert (fake.app / "new.ini").read_bytes() == b"NEW"
    monkeypatch.setattr(idr, "dir_writable", lambda d, probe=False, expand=None: False)
    (fake.app / "new.ini").unlink()
    assert fake.cli("import", str(pkg), "--rules", str(rules), "--yes", "--allow-install-dir") == 1
    cap = capsys.readouterr()
    assert "관리자 권한으로 다시 실행해 주세요" in cap.out + cap.err and not (fake.app / "new.ini").exists()


def test_old_package_without_install_entries_unchanged(fake, tmp_path):
    rules = make_rules_dir(tmp_path / "rules")
    pkg = make_pkg(fake, [], None)
    r = run_import(pkg, rules, dry_run=True, deps=deps_for(fake))
    assert r.n_install == 0 and "install_dir" not in r.preview_dict()


def test_export_twice_same_output_and_original_untouched(fake):
    fake.acme_tree()
    its = [item(fake, "Acme Paint/settings.ini"), item(fake, "Acme Paint/user.cuix")]
    out, m1 = export(fake, its)
    out2, m2 = export(fake, its, out=out)  # 같은 출력 이름: 기존은 .bak 으로 보존
    assert sel_names(m1) == sel_names(m2) and Path(str(out) + ".bak").exists()
    assert (fake.app / "settings.ini").read_bytes() == b"[a]\nx=1\n"
