"""설정 기록 모드: 프로그램 정보로 훑을 폴더 추정(record_infer), --from-installed, 초기화(reset) 시험.

가짜 프로필(환경변수 주입)과 주입한 설치 목록/실행 파일 정보만 쓴다(실제 레지스트리·AppData 는 읽지 않는다).
"""
import json
import os
import zipfile
from pathlib import Path

import pytest

from pcmigrate import cli, envpaths, record_infer as ri, record_mode as rm
from pcmigrate.detect import Program
from pcmigrate.package_export import export_package
from test_package import env, make_rule  # noqa: F401  (env 는 fixture)
from test_record_mode import USER, Fake


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    envpaths.set_overrides({"USERPROFILE": str(f.home), "HOME": str(f.home), "APPDATA": str(f.app), "LOCALAPPDATA": str(f.loc),
                            "PROGRAMDATA": str(f.pd)})
    yield f
    envpaths.set_overrides(None)


def plan(fake, info, **kw):
    return ri.infer_folders(info, documents_fn=lambda: str(fake.docs), **kw)


def toks(p):
    return [f.token for f in p.folders]


# ------------------------------------------------------------------ (b) 게시자
def test_publisher_vendor_folder_then_product_child(fake):
    fake.put(fake.app / "Acme" / "Foo Pro 2025" / "a.ini")
    fake.put(fake.app / "Acme" / "Other App" / "b.ini")
    p = plan(fake, ri.ProgramInfo("Foo Pro", publisher="Acme Inc."))
    assert toks(p) == ["%APPDATA%\\Acme\\Foo Pro 2025"]
    assert "게시자(Acme Inc.)" in p.folders[0].reason and p.folders[0].confidence == ri.HIGH
    assert "게시자 이름" in p.evidence


def test_vendor_only_folder_is_never_collected(fake):
    fake.put(fake.app / "Acme" / "Other App" / "b.ini")
    p = plan(fake, ri.ProgramInfo("Foo Pro", publisher="Acme Inc."))
    assert p.folders == []
    assert any(t == "%APPDATA%\\Acme" and "통째" not in w and "제외" in w for t, w in p.excluded)
    assert any("전체 훑기(--all-roots)" in n for n in p.notes)
    with pytest.raises(rm.RecordError) as e:
        rm.start("Foo Pro", str(fake.work), info=ri.ProgramInfo("Foo Pro", publisher="Acme Inc."), infer=True,
                 documents_fn=lambda: str(fake.docs))
    assert "자동으로 하지 않습니다" in str(e.value) and not (fake.work / rm.STATE_FILE).exists()


# ------------------------------------------------------------------ (c) 설치 위치
def test_install_location_gives_vendor_and_product(fake):
    fake.put(fake.app / "ZWSoft" / "ZWCAD" / "a.ini")
    fake.put(fake.app / "ZWSoft" / "ZW3D" / "b.ini")
    info = ri.ProgramInfo("ZWCAD Professional", install_location="C:\\Program Files\\ZWSoft\\ZWCAD 2026")
    p = plan(fake, info)
    assert toks(p) == ["%APPDATA%\\ZWSoft\\ZWCAD"]
    assert "설치 위치" in p.folders[0].reason
    assert ri.path_names("C:\\Program Files\\ZWSoft\\ZWCAD 2026") == ("ZWCAD 2026", "ZWSoft")


def test_install_location_never_uses_user_name(fake):
    home_path = "C:\\Users\\" + USER + "\\AppData\\Local\\Programs\\Foo"
    assert ri.path_names(home_path) == ("Foo", "")
    fake.put(fake.loc / "Foo" / "a.ini")
    info = ri.ProgramInfo("Foo Studio", install_location=str(fake.loc / "Programs" / "Foo"))
    p = plan(fake, info)
    assert toks(p) == ["%LOCALAPPDATA%\\Foo"]
    blob = json.dumps(p.as_dict(), ensure_ascii=False) + json.dumps(info.as_state(), ensure_ascii=False)
    assert USER not in blob
    assert info.as_state()["install_location"].startswith("%LOCALAPPDATA%")


# ------------------------------------------------------------------ (d) 실행 파일 정보
def test_exe_version_info_is_used_and_failure_is_ignored(fake):
    fake.put(fake.app / "Acme" / "Zeta Studio" / "a.ini")
    calls = []

    def vi(path):
        calls.append(path)
        return {"CompanyName": "Acme Corp", "ProductName": "Zeta Studio", "FileDescription": "Zeta"}
    info = ri.ProgramInfo("Totally Different Name", exe_path="C:\\x\\z.exe")
    p = plan(fake, info, version_info_fn=vi)
    assert toks(p) == ["%APPDATA%\\Acme\\Zeta Studio"] and calls == ["C:\\x\\z.exe"]
    assert "실행 파일 정보" in p.folders[0].reason and "실행 파일 버전 정보" in p.evidence
    # 읽기 실패(빈 값/예외)는 무시하고 다른 근거로 계속
    for bad in (lambda path: {}, lambda path: (_ for _ in ()).throw(OSError("x"))):
        p2 = plan(fake, ri.ProgramInfo("Zeta Studio", exe_path="C:\\x\\z.exe"), version_info_fn=bad)
        assert toks(p2) == ["%APPDATA%\\Acme\\Zeta Studio"] or toks(p2) == []  # 이름만으로는 Acme 아래를 못 찾을 수 있다
    assert ri.read_version_info("C:\\no\\such\\file.exe") == {}
    if os.name == "nt":
        import sys
        assert isinstance(ri.read_version_info(sys.executable), dict)


def test_display_icon_is_parsed_for_exe_and_install_dir():
    assert ri._exe_from('"C:\\Program Files\\A B\\app.exe",0') == "C:\\Program Files\\A B\\app.exe"
    assert ri._exe_from("C:\\Program Files\\A\\app.exe,1") == "C:\\Program Files\\A\\app.exe"
    assert ri._exe_from("") == ""


# ------------------------------------------------------------------ (a)(e) 이름·버전 폴백
def test_name_only_fallback_handles_year_and_spaces(fake):
    fake.put(fake.app / "AutoCAD LT 2015" / "a.ini")
    fake.put(fake.app / "AutoCADLT" / "b.ini")
    fake.put(fake.app / "AutoCAD LT" / "c.ini")
    fake.put(fake.app / "AutoCAD Civil 2015" / "d.ini")
    p = plan(fake, ri.ProgramInfo("AutoCAD LT 2015 - English"))
    assert sorted(toks(p)) == ["%APPDATA%\\AutoCAD LT", "%APPDATA%\\AutoCAD LT 2015", "%APPDATA%\\AutoCADLT"]
    assert all("프로그램 이름" in f.reason for f in p.folders)
    assert ri.product_key("AutoCAD LT 2015", set()) == "autocadlt"


def test_empty_info_gives_note_not_crash(fake):
    p = plan(fake, ri.ProgramInfo("2025"))
    assert p.folders == [] and p.notes
    p2 = plan(fake, ri.ProgramInfo("Zzyzx Tool"))  # 폴더가 하나도 없음
    assert p2.folders == [] and any("직접 추가" in n for n in p2.notes)


def test_home_dot_folder_and_documents_are_searched(fake):
    fake.put(fake.home / ".fooedit" / "rc")
    fake.put(fake.docs / "FooEdit" / "t.ini")
    p = plan(fake, ri.ProgramInfo("FooEdit"))
    assert sorted(toks(p)) == ["%USERPROFILE%\\.fooedit", "%USERPROFILE%\\Documents\\FooEdit"]


# ------------------------------------------------------------------ 오탐 방지
def test_photoshop_elements_does_not_match_photoshop(fake):
    fake.put(fake.app / "Adobe" / "Adobe Photoshop 2025" / "a.ini")
    fake.put(fake.app / "Adobe" / "Adobe Photoshop Elements 2024" / "b.ini")
    fake.put(fake.app / "Adobe" / "Common" / "c.ini")
    fake.put(fake.app / "Adobe Photoshop Elements" / "d.ini")
    p = plan(fake, ri.ProgramInfo("Adobe Photoshop 2025", publisher="Adobe Inc."))
    assert toks(p) == ["%APPDATA%\\Adobe\\Adobe Photoshop 2025"]
    pe = plan(fake, ri.ProgramInfo("Adobe Photoshop Elements 2024", publisher="Adobe Inc."))
    assert sorted(toks(pe)) == ["%APPDATA%\\Adobe Photoshop Elements", "%APPDATA%\\Adobe\\Adobe Photoshop Elements 2024"]
    assert not any("Photoshop 2025" in t for t in toks(pe))
    # 이름의 앞부분만 맞는 Photoshop 폴더는 기본 대상이 아니라 제외 후보
    assert any(t.endswith("Adobe Photoshop 2025") and "앞부분만" in w for t, w in pe.excluded)


def test_vendor_word_alone_never_matches(fake):
    fake.put(fake.app / "Microsoft" / "Windows" / "w.ini")
    fake.put(fake.app / "Microsoft" / "Office" / "o.ini")
    fake.put(fake.app / "Microsoft" / "Teams" / "t.ini")
    assert plan(fake, ri.ProgramInfo("Microsoft", publisher="Microsoft Corporation")).folders == []
    p = plan(fake, ri.ProgramInfo("Microsoft Office", publisher="Microsoft Corporation"))
    assert toks(p) == ["%APPDATA%\\Microsoft\\Office"]
    # 게시자 정보가 없어도 알려진 제조사 폴더 아래의 같은 이름 폴더는 찾되 제조사 폴더 통째로는 담지 않는다
    p2 = plan(fake, ri.ProgramInfo("Office"))
    assert toks(p2) == ["%APPDATA%\\Microsoft\\Office"]
    assert "%APPDATA%\\Microsoft" not in toks(p2)


def test_noise_top_folders_are_skipped(fake):
    fake.put(fake.app / "Temp" / "x.ini")
    fake.put(fake.app / "FooCache" / "x.ini")
    assert plan(fake, ri.ProgramInfo("Temp")).folders == []
    assert plan(fake, ri.ProgramInfo("FooCache")).folders == []


# ------------------------------------------------------------------ start 와 연결
def test_start_with_infer_scans_only_inferred_folders_and_saves_state(fake):
    z = fake.app / "Acme" / "Foo Pro 2025"
    fake.put(z / "s.ini", "1")
    fake.put(fake.app / "Acme" / "Other App" / "o.ini", "1")
    fake.put(fake.app / "Unrelated" / "u.ini", "1")
    info = ri.ProgramInfo("Foo Pro", publisher="Acme Inc.", install_location="C:\\Program Files\\Acme\\Foo Pro 2025")
    s, r = rm.start("Foo Pro", str(fake.work), info=info, infer=True, documents_fn=lambda: str(fake.docs))
    assert sorted(f.rsplit("\\", 1)[-1] for f in s.load_snap("before").files) == ["s.ini"]
    assert [f["path"] for f in r.inferred] == ["%APPDATA%\\Acme\\Foo Pro 2025"]
    saved = json.loads((fake.work / rm.STATE_FILE).read_text("utf-8"))
    assert saved["scope"]["infer"]["folders"][0]["reason"] and saved["scope"]["infer"]["info"]["publisher"] == "Acme Inc."
    assert USER not in (fake.work / rm.STATE_FILE).read_text("utf-8")
    fake.snap(s)
    fake.put(z / "s.ini", "2")
    fake.snap(s)
    assert [c.path.rsplit("\\", 1)[-1] for c in rm.diff(s).candidates] == ["s.ini"]
    assert "추정한 폴더" in rm.render_status(s)[1]


def test_new_inferred_folder_after_start_is_reported_not_scanned(fake):
    fake.put(fake.app / "Acme" / "Foo Pro 2025" / "s.ini")
    info = ri.ProgramInfo("Foo Pro", publisher="Acme Inc.")
    s, _ = rm.start("Foo Pro", str(fake.work), info=info, infer=True, documents_fn=lambda: str(fake.docs))
    fake.put(fake.app / "Acme" / "Foo Pro 2026" / "new.ini")
    r = fake.snap(s)
    assert any("새로 생긴 설정 폴더" in w for w in r.warnings)
    assert [k.rsplit("\\", 1)[-1] for k in s.load_snap("baseline").files] == ["s.ini"]


def test_add_and_exclude_roots_before_start(fake):
    fake.put(fake.app / "Foo" / "a.ini")
    fake.put(fake.loc / "FooData" / "b.ini")
    extra = fake.pd / "Shared" / "Foo"
    fake.put(extra / "c.ini")
    p = plan(fake, ri.ProgramInfo("Foo"))
    assert toks(p) == ["%APPDATA%\\Foo"]
    ri.add_roots(p, [str(extra)])
    assert "%PROGRAMDATA%\\Shared\\Foo" in toks(p) and p.folders[-1].source == "added"
    with pytest.raises(ValueError):
        ri.add_roots(p, [str(fake.pd / "nope")])
    ri.exclude_roots(p, ["%APPDATA%\\Foo"])
    s, r = rm.start("Foo", str(fake.work), plan=p, documents_fn=lambda: str(fake.docs))
    assert [k.rsplit("\\", 1)[-1] for k in s.load_snap("before").files] == ["c.ini"]
    assert any(f["enabled"] is False for f in r.inferred)


def test_roots_option_adds_to_inference_and_all_roots_is_explicit_only(fake):
    fake.put(fake.app / "Foo" / "a.ini")
    extra = fake.pd / "Foo2"
    fake.put(extra / "c.ini")
    s, _ = rm.start("Foo", str(fake.work), roots=[str(extra)], infer=True, documents_fn=lambda: str(fake.docs))
    assert sorted(k.rsplit("\\", 1)[-1] for k in s.load_snap("before").files) == ["a.ini", "c.ini"]
    # 아무것도 못 찾으면 전체 훑기를 자동으로 하지 않는다. --all-roots 를 명시해야만 한다
    with pytest.raises(rm.RecordError):
        rm.start("Zzyzx", str(fake.work.parent / "w2"), infer=True, documents_fn=lambda: str(fake.docs))
    s3, r3 = rm.start("Zzyzx", str(fake.work.parent / "w3"), infer=True, all_roots=True, documents_fn=lambda: str(fake.docs))
    assert r3.status == "ok" and "infer" not in s3.state["scope"]


# ------------------------------------------------------------------ CLI --from-installed
def test_cli_from_installed(fake, monkeypatch, capsys):
    fake.put(fake.app / "Acme" / "Foo Pro 2025" / "s.ini")
    progs = [Program("Foo Pro", "2025", "Acme Inc.", install_location="C:\\Program Files\\Acme\\Foo Pro 2025"),
             Program("Other Tool", "1", "Other"), Program("Foo Pro Viewer", "1", "Acme Inc.")]
    monkeypatch.setattr(cli, "read_registry_programs", lambda: progs)
    w = ["--work", str(fake.work)]
    assert cli.main(["record", "start", "--from-installed", "foo pro", *w]) == 0
    out = capsys.readouterr()
    assert "게시자(Acme Inc.)" in out.out and "%APPDATA%\\Acme\\Foo Pro 2025" in out.out and USER not in out.out + out.err
    assert json.loads((fake.work / rm.STATE_FILE).read_text("utf-8"))["program"] == "Foo Pro"
    assert cli.main(["record", "start", "--from-installed", "foo", "--work", str(fake.work.parent / "w2")]) == 1  # 여러 개가 맞음
    assert "더 정확히" in capsys.readouterr().err
    assert cli.main(["record", "start", "--from-installed", "nothing here", "--work", str(fake.work.parent / "w3")]) == 1
    assert cli.main(["record", "start", "--work", str(fake.work.parent / "w4")]) == 1  # 이름 없음
    capsys.readouterr()


def test_cli_infer_default_and_no_infer(fake, capsys):
    fake.put(fake.app / "Foo Edit" / "a.ini")
    fake.put(fake.app / "FooBar" / "b.ini")
    assert cli.main(["record", "start", "--program", "Foo Edit", "--work", str(fake.work)]) == 0
    assert "[훑음]" in capsys.readouterr().out
    s = rm.load_session(fake.work)
    assert [k.rsplit("\\", 1)[-1] for k in s.load_snap("before").files] == ["a.ini"]
    # --no-infer: 예전 방식(낱말 부분 일치)
    w2 = fake.work.parent / "w2"
    assert cli.main(["record", "start", "--program", "Foo Edit", "--no-infer", "--match", "foobar", "--work", str(w2)]) == 0
    assert sorted(k.rsplit("\\", 1)[-1] for k in rm.load_session(w2).load_snap("before").files) == ["a.ini", "b.ini"]
    capsys.readouterr()
    assert cli.main(["record", "start", "--program", "Zzyzx", "--work", str(fake.work.parent / "w3")]) == 1
    assert "자동으로 하지 않습니다" in capsys.readouterr().err


# ------------------------------------------------------------------ Program 새 필드와 묶음
def test_program_new_fields_optional_and_not_in_package(env):
    p = Program("Demo App", "1.0", "Pub", display_icon="C:\\Users\\" + USER + "\\x.exe,0", uninstall_string="x", install_date="20260101")
    assert Program("A").display_icon == "" and ri.ProgramInfo.from_program(Program("A", "1", "P")).publisher == "P"
    tmp_path, old, _, expand_for = env
    out = tmp_path / "pkg.zip"
    export_package([make_rule()], out, [p], '{"Sources": []}', expand=expand_for(old))
    with zipfile.ZipFile(out) as zf:
        row = json.loads(zf.read("programs/registry.json"))[0]
    assert row["name"] == "Demo App" and "display_icon" not in row and "uninstall_string" not in row and USER not in json.dumps(row)


# ------------------------------------------------------------------ reset
def _session(fake, rounds=1):
    ini = fake.put(fake.app / "Foo" / "a.ini", "1")
    s, _ = rm.start("Foo", str(fake.work), infer=True, documents_fn=lambda: str(fake.docs))
    fake.snap(s)
    for i in range(rounds):
        fake.put(ini, str(i + 2))
        fake.snap(s)
    rm.write_draft(s)
    return s


def test_reset_moves_everything_to_numbered_bak_and_reports(fake):
    s = _session(fake)
    (fake.work / "my-notes.txt").write_text("keep me", encoding="utf-8")
    r = rm.reset(fake.work)
    names = sorted(p.name for p in fake.work.iterdir())
    assert r.status == "ok" and len(r.moved) == 7  # before/baseline/after1 + state + candidates + draft + report
    assert not any(n in names for n in ("record-state.json", "snap-before.json", "rule-draft.json", "record-candidates.json"))
    assert "snap-before.json.bak" in names and "record-state.json.bak" in names and "my-notes.txt" in names
    assert r.kept_foreign == ["my-notes.txt"] and (fake.work / "my-notes.txt").read_text("utf-8") == "keep me"
    rep = json.loads((fake.work / rm.REPORT_FILE).read_text("utf-8"))
    assert rep["op"] == "reset" and rep["status"] == "ok" and len(rep["moved"]) == 7
    with pytest.raises(rm.RecordError):
        rm.load_session(fake.work)  # 빈 상태
    # 다시 시작 후 또 초기화하면 번호가 붙고 이전 .bak 은 그대로
    first_bak = (fake.work / "record-state.json.bak").read_text("utf-8")
    s2, _ = rm.start("Foo", str(fake.work), infer=True, documents_fn=lambda: str(fake.docs))
    rm.reset(fake.work)
    assert (fake.work / "record-state.json.bak").read_text("utf-8") == first_bak and (fake.work / "record-state.json.bak.1").exists()


def test_reset_nothing_missing_dir_and_keep_backup_false(fake):
    fake.work.mkdir()
    r = rm.reset(fake.work)
    assert r.status == "nothing" and not (fake.work / rm.REPORT_FILE).exists()  # 기록이 없는 폴더에는 보고서도 만들지 않는다(Z3)
    with pytest.raises(rm.RecordError):
        rm.reset(fake.work / "nope")
    with pytest.raises(rm.RecordError):
        rm.reset(fake.work, keep_backup=False)


def test_reset_refuses_link_pointing_outside_work_dir(fake, tmp_path):
    fake.work.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        os.symlink(outside, fake.work / rm.STATE_FILE)
    except (OSError, NotImplementedError):
        pytest.skip("심볼릭 링크를 만들 수 없는 환경")
    with pytest.raises(rm.RecordError):
        rm.reset(fake.work)
    assert outside.read_text("utf-8") == "{}" and (fake.work / rm.STATE_FILE).is_symlink()
    assert json.loads((fake.work / rm.REPORT_FILE).read_text("utf-8"))["status"] == "failed"


def test_start_force_shares_reset_and_cli_reset(fake, capsys):
    _session(fake)
    s2, _ = rm.start("Foo", str(fake.work), infer=True, force=True, documents_fn=lambda: str(fake.docs))
    assert (fake.work / "snap-baseline.json.bak").exists() and [i["label"] for i in s2.snaps] == ["before"]
    assert cli.main(["record", "reset", "--work", str(fake.work)]) == 0
    out = capsys.readouterr().out
    assert "삭제하지 않고 보관" in out and not (fake.work / rm.STATE_FILE).exists()
    assert cli.main(["record", "reset", "--work", str(fake.work / "none")]) == 1
    assert cli.main(["record", "reset", "--work", str(fake.work)]) == 0 and "이전 기록이 없었습니다" in capsys.readouterr().out


def test_z3_reset_on_unrelated_folder_creates_nothing(fake, capsys):
    other = fake.home / "Documents" / "mine"
    other.mkdir(parents=True)
    (other / "notes.txt").write_text("x", encoding="utf-8")
    before = sorted(p.name for p in other.iterdir())
    r = rm.reset(other)
    assert r.status == "nothing" and r.reported is False and sorted(p.name for p in other.iterdir()) == before
    assert cli.main(["record", "reset", "--work", str(fake.home / "Documents")]) == 0
    out = capsys.readouterr().out
    assert "아무것도 만들거나 바꾸지 않았습니다" in out and not (fake.home / "Documents" / rm.REPORT_FILE).exists()
    assert cli.main(["record", "reset", "--work", str(fake.home)]) == 0 and not (fake.home / rm.REPORT_FILE).exists()


def test_z3_reset_still_reports_when_tool_files_exist(fake):
    _session(fake)
    r = rm.reset(fake.work)
    assert r.status == "ok" and r.reported and (fake.work / rm.REPORT_FILE).exists()
