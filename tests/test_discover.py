from conftest import make_rules_dir
import json
import re
import zipfile
from pathlib import Path

import pytest

from pcmigrate import cli
from pcmigrate.checklist import build_checklist, render as render_checklist
from pcmigrate.detect import Program
from pcmigrate.discover import (CERTAIN, PROBABLE, SelectionError, build_selection, discover,
                                link_candidate, load_selection, render, summarize)
from pcmigrate.discover_data import (DOT_HINTS, EXTRA_ROOTS, RESTORE_DENY_SUBSTRINGS, ROOTS, SECRET_PATTERNS,
                                     SKIP_NAMES)
from pcmigrate.package import EXTRA_ID, export_package, plan_import, verify_package
from pcmigrate.rules import load_rules

from test_stage4 import WINGET, patch_detection

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"


def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class Env:
    """가짜 사용자 환경(tmp_path 안). expand 로 주입하거나 monkeypatch.setenv 로 쓴다."""

    def __init__(self, tmp):
        self.home = tmp / "home"
        self.appdata = self.home / "AppData" / "Roaming"
        self.local = self.home / "AppData" / "Local"
        self.pd = tmp / "ProgramData"
        for p in (self.appdata, self.local, self.pd, self.home / "Documents"):
            p.mkdir(parents=True, exist_ok=True)

    def expand(self, s):
        return (s.replace("%APPDATA%", str(self.appdata)).replace("%LOCALAPPDATA%", str(self.local))
                 .replace("%PROGRAMDATA%", str(self.pd)).replace("%USERPROFILE%", str(self.home)))

    def setenv(self, monkeypatch):
        monkeypatch.setenv("APPDATA", str(self.appdata))
        monkeypatch.setenv("LOCALAPPDATA", str(self.local))
        monkeypatch.setenv("PROGRAMDATA", str(self.pd))
        monkeypatch.setenv("USERPROFILE", str(self.home))
        monkeypatch.delenv("OneDrive", raising=False)
        monkeypatch.delenv("OneDriveCommercial", raising=False)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    touch(e.appdata / "JetBrains" / "IntelliJIdea2025.1" / "options" / "ide.xml")
    touch(e.appdata / "JetBrains" / "IntelliJIdea2025.1" / "c.kdbx", b"secret-db")
    touch(e.appdata / "Code" / "User" / "settings.json", b"{}")
    touch(e.appdata / "Acme" / "config.ini", b"a" * 100)
    touch(e.appdata / "Acme" / "sub" / "credentials.json", b"{}")
    touch(e.appdata / "Foo Bar" / "data.db", b"b" * 2048)
    touch(e.appdata / "Contoso" / "x.cfg", b"c" * 10)
    touch(e.appdata / "Located" / "l.cfg")
    touch(e.appdata / "Unknown Vendor" / "u.cfg")
    touch(e.appdata / "Microsoft" / "system.dat")
    touch(e.local / "Temp" / "t.tmp")
    touch(e.local / "SomeAppCache" / "c.bin")
    touch(e.local / "LG Update" / "lg.dat")
    touch(e.pd / "Acme" / "common.dat", b"p" * 50)
    touch(e.home / ".gitconfig", b"[user]")
    touch(e.home / ".ssh" / "id_rsa", b"KEY")
    touch(e.home / ".vscode" / "extensions" / "e.json")
    touch(e.home / ".cache" / "c")
    return e


PROGRAMS = [
    Program("Acme Tool", "1.0", "Acme Inc"),
    Program("Foo Bar Pro", "2.0", "Foo"),
    Program("Widget Studio", "1.0", "Contoso Ltd"),
    Program("Located App", "1.0", "", install_location=""),
    Program("Git", "2.4", "The Git Development Community"),
    Program("Microsoft Visual Studio Code", "1.9", "Microsoft Corporation"),
    Program("IntelliJ IDEA Community Edition 2025.1", "2025.1", "JetBrains s.r.o."),
    Program("LG Update", "1.0", "LG Electronics"),
    Program("Microsoft Visual C++ 2015-2022 Redistributable (x64)", "14", "Microsoft"),
]


@pytest.fixture
def rules():
    return load_rules(REPO_RULES)


def programs_for(env):
    ps = list(PROGRAMS)
    ps[3] = Program("Located App", "1.0", "", install_location=str(env.appdata / "Located"))
    return ps


def run(env, rules, **kw):
    return discover(rules, programs_for(env), env.expand, **kw)


def cand(d, rel):
    for c in d.candidates() + d.unlinked + d.rule_linked:
        if c.rel == rel:
            return c
    raise AssertionError(rel)


# --------------------------------------------------------------------- A. 탐지/연결

def test_ruleless_programs_exclude_rule_programs_and_bundles(env, rules):
    d = run(env, rules)
    names = {e.program.name for e in d.programs}
    assert names == {"Acme Tool", "Foo Bar Pro", "Widget Studio", "Located App", "Git"}
    assert "IntelliJ IDEA Community Edition 2025.1" not in names  # jetbrains 규칙이 처리
    assert "Microsoft Visual Studio Code" not in names  # vscode 규칙, 중복 표시 안 함
    assert {p.name for p, _ in d.bundles} == {"LG Update",
                                              "Microsoft Visual C++ 2015-2022 Redistributable (x64)"}
    assert d.program_count == 5


def test_links_have_grade_and_reason(env, rules):
    d = run(env, rules)
    by = {e.program.name: e for e in d.programs}
    loc = cand(d, "Located")
    assert loc.link.grade == CERTAIN and "설치 위치" in loc.link.reason
    acme = cand(d, "Acme")
    assert acme.link.programs == ["Acme Tool"] and acme.link.grade == PROBABLE
    assert cand(d, "Foo Bar").link.programs == ["Foo Bar Pro"]
    contoso = cand(d, "Contoso")
    assert contoso.link.programs == ["Widget Studio"] and "발행자" in contoso.link.reason
    assert {c.rel for c in by["Acme Tool"].candidates} == {"Acme"}  # AppData 와 ProgramData 의 Acme
    assert len([c for c in d.candidates() if c.rel == "Acme"]) == 2


def test_acme_found_in_both_appdata_and_programdata(env, rules):
    d = run(env, rules)
    roots = sorted(c.root for c in d.candidates() if c.rel == "Acme")
    assert roots == ["%APPDATA%", "%PROGRAMDATA%"]


def test_noise_and_system_folders_skipped(env, rules):
    d = run(env, rules)
    everything = [c.rel for c in d.candidates() + d.unlinked + d.rule_linked]
    for bad in ("Temp", "SomeAppCache", "Microsoft", ".cache"):
        assert bad not in everything
    assert "Unknown Vendor" in [c.rel for c in d.unlinked]  # 연결 못 한 폴더는 숨김 목록
    assert "Unknown Vendor" not in [c.rel for c in d.candidates()]


def test_rule_covered_folders_not_listed_twice(env, rules):
    d = run(env, rules)
    everything = [c.rel for c in d.candidates() + d.unlinked + d.rule_linked]
    assert "JetBrains" not in everything and "Code" not in everything
    assert d.rule_covered >= 2
    vs = cand(d, ".vscode")  # 규칙 있는 프로그램 쪽 폴더(숨김)
    assert vs in d.rule_linked and vs.rule_programs == ["Microsoft Visual Studio Code"]
    assert vs not in d.home


def test_home_dotfiles_and_hints(env, rules):
    d = run(env, rules)
    home = {c.rel: c for c in d.home}
    assert set(home) == {".gitconfig", ".ssh"}
    assert home[".gitconfig"].link.programs == ["Git"] and home[".gitconfig"].link.grade == PROBABLE
    assert home[".ssh"].link.programs == ["Git"]  # 힌트 표: .ssh -> openssh/putty/git
    assert {e.program.name: sorted(c.rel for c in e.candidates) for e in d.programs}["Git"] == [".gitconfig", ".ssh"]
    # 연결 없는 점 항목도 홈 목록에 보여 준다
    d2 = discover(rules, [Program("Unrelated")], env.expand)
    assert {".gitconfig", ".ssh"} <= {c.rel for c in d2.home}


def test_size_files_mtime_and_unknown_size(env, rules):
    d = run(env, rules)
    acme = next(c for c in d.candidates() if c.rel == "Acme" and c.root == "%APPDATA%")
    assert acme.size == 102 and acme.files == 2
    assert acme.mtime and acme.scan_complete
    foo = cand(d, "Foo Bar")
    assert foo.size == 2048 and foo.files == 1

    ticks = iter(range(0, 10_000_000, 1000))  # 시간이 매 호출마다 크게 흐르는 가짜 시계 -> 시간 상한 초과
    d2 = discover(rules, programs_for(env), env.expand, per_limit=0.5, clock=lambda: float(next(ticks)))
    assert all(c.size is None for c in d2.candidates() if c.is_dir)  # 시간 상한 초과 -> 크기 미확인
    assert any(c.is_dir for c in d2.candidates()) and "크기 미확인" in render(d2)

    d3 = run(env, rules, with_sizes=False)
    assert all(c.size is None for c in d3.candidates() if c.is_dir)


def test_summarize_counts_without_sizes(env, rules):
    n, m = summarize(rules, programs_for(env), env.expand)
    d = run(env, rules, with_sizes=False)
    assert (n, m) == (d.program_count, d.candidate_count) and n == 5 and m >= 5


def test_min_size_filter_keeps_secret_items(env, rules):
    d = run(env, rules, min_size_kb=1)  # 1KB 미만 숨김: Acme(102B) 는 비밀 항목이라 유지, Contoso(10B) 는 숨김
    rels = {c.rel for c in d.candidates()}
    assert "Contoso" not in rels and "Acme" in rels and "Foo Bar" in rels
    assert d.hidden_small >= 1


# --------------------------------------------------------------------- B. 비밀 정보 가능성

def test_secret_warning_nested_and_by_name(env, rules):
    d = run(env, rules)
    acme = next(c for c in d.candidates() if c.rel == "Acme" and c.root == "%APPDATA%")
    assert "credentials.json" in acme.secret
    ssh = next(c for c in d.home if c.rel == ".ssh")
    assert ".ssh" in ssh.secret
    assert not cand(d, "Foo Bar").secret
    out = render(d)
    assert "비밀 정보 가능성(credentials.json): 기본 제외" in out
    assert d.secret_count >= 2


def test_secret_pattern_table_has_required_names():
    for need in ("credentials", "token", "secret", "keychain", ".ssh", ".aws", ".kube", "login data", "cookies"):
        assert need in SECRET_PATTERNS
    assert all(p == p.lower() for p in SECRET_PATTERNS)


# --------------------------------------------------------------------- D. 번들 숨김 / --all / 형식

def test_bundles_hidden_by_default_and_listed_with_all(env, rules):
    d = run(env, rules)
    text = render(d)
    assert "LG Update" not in text.split("숨김:")[0] and "숨김: 제조사 번들/시스템 구성요소 2개" in text
    full = render(d, "text", show_all=True)
    assert "LG Update — LG 제조사 유틸리티" in full and "Unknown Vendor" in full
    assert "Redistributable" in full


def test_render_formats(env, rules):
    d = run(env, rules)
    md = render(d, "md")
    assert md.startswith("# 규칙 없는 프로그램 탐지") and "- **Acme Tool**" in md
    j = json.loads(render(d, "json", True))
    assert j["counts"]["programs"] == 5 and j["programs_without_rule"][0]["name"]
    assert j["counts"]["secret"] >= 2 and "bundles" in j
    assert "읽기 전용" in render(d) and "자동으로 export 에 포함되지 않습니다" in render(d)


def test_data_tables_sane():
    assert {r.template for r in ROOTS} == set(EXTRA_ROOTS)
    assert all(n == n.lower() for n in SKIP_NAMES)
    assert ".gitconfig" in DOT_HINTS and ".ssh" in DOT_HINTS
    assert "\\startup\\" in RESTORE_DENY_SUBSTRINGS


@pytest.mark.parametrize("folder,prog,expect", [
    ("Intel", "IntelliJ IDEA Community Edition 2025.1", None),  # 이름 일부일 뿐인 폴더는 연결 안 함
    ("IntelliJIdea2025.1", "IntelliJ IDEA Community Edition 2025.1", PROBABLE),
    ("Code", "Microsoft Visual Studio Code", None),
    ("Google", "Google Chrome", PROBABLE),
    ("Slack", "Slack", CERTAIN),
])
def test_link_name_rules(folder, prog, expect):
    link = link_candidate(folder, "C:\\x\\" + folder, [Program(prog, "1", "Pub")], False)
    assert (link.grade if link else None) == expect


# --------------------------------------------------------------------- B. 선택 파일 / export --extra

def test_selection_template_all_false_and_roundtrip(env, rules, tmp_path, monkeypatch):
    d = run(env, rules)
    sel = build_selection(d)
    assert sel["candidates"] and all(c["include"] is False for c in sel["candidates"])
    path = tmp_path / "sel.json"
    path.write_text(json.dumps(sel), encoding="utf-8")
    assert load_selection(path, env.expand) == []  # 모두 include:false 면 아무것도 선택되지 않음
    for c in sel["candidates"]:
        if c["rel"] == "Foo Bar":
            c["include"] = True
    path.write_text(json.dumps(sel), encoding="utf-8")
    items = load_selection(path, env.expand)
    assert [(i.root, i.rel, i.is_dir) for i in items] == [("%APPDATA%", "Foo Bar", True)]


@pytest.mark.parametrize("patch", [
    {"root": "C:\\Windows"}, {"root": "%APPDATA%", "rel": "..\\evil"}, {"rel": "a/b"}, {"rel": "a\\b"},
    {"rel": ""}, {"rel": ".."}, {"rel": "C:evil"}, {"rel": "does-not-exist"},
    {"root": "%USERPROFILE%", "rel": "Documents"},  # 홈 루트는 점 항목만
])
def test_selection_validation_rejects_unsafe(env, tmp_path, patch):
    item = {"include": True, "root": "%APPDATA%", "rel": "Foo Bar"}
    item.update(patch)
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"format": 1, "candidates": [item]}), encoding="utf-8")
    touch(env.appdata / "Foo Bar" / "d.db")
    with pytest.raises(SelectionError):
        load_selection(f, env.expand)


def test_selection_bad_files(tmp_path, env):
    for text in ("{broken", "[]", json.dumps({"format": 9, "candidates": []})):
        f = tmp_path / "s.json"
        f.write_text(text, encoding="utf-8")
        with pytest.raises(SelectionError):
            load_selection(f, env.expand)
    with pytest.raises(SelectionError):
        load_selection(tmp_path / "missing.json", env.expand)


def select(env, tmp_path, *items):
    f = tmp_path / "sel.json"
    f.write_text(json.dumps({"format": 1, "candidates": [
        {"include": True, "root": r, "rel": rel} for r, rel in items]}), encoding="utf-8")
    return f


def test_export_extra_applies_existing_safeguards(env, rules, tmp_path):
    touch(env.appdata / "Foo Bar" / "license.lic", b"LICENSE")
    touch(env.appdata / "Foo Bar" / "sub" / "keep.txt", b"k")
    items = load_selection(select(env, tmp_path, ("%APPDATA%", "Foo Bar")), env.expand)
    src_before = {p: p.read_bytes() for p in (env.appdata / "Foo Bar").rglob("*") if p.is_file()}
    out = tmp_path / "pkg.zip"
    m = export_package([], out, [], None, expand=env.expand, extras=items)
    assert {f["rule"] for f in m["files"]} == {EXTRA_ID}
    assert not any(f["arc"].endswith(".lic") for f in m["files"])
    assert {f["rel"] for f in m["files"]} == {"Foo Bar/data.db", "Foo Bar/sub/keep.txt"}
    assert all(len(f["sha256"]) == 64 for f in m["files"]) and m["extras"] == [{"root": "%APPDATA%", "rel": "Foo Bar"}]
    assert any(w["rule"] == EXTRA_ID for w in m["manual_warnings"])
    assert {p: p.read_bytes() for p in (env.appdata / "Foo Bar").rglob("*") if p.is_file()} == src_before
    with zipfile.ZipFile(out) as zf:
        assert "data/extra/appdata/Foo Bar/data.db" in zf.namelist()


def test_extra_restore_roundtrip_and_allowed_roots(env, rules, tmp_path):
    items = load_selection(select(env, tmp_path, ("%APPDATA%", "Foo Bar"), ("%USERPROFILE%", ".gitconfig")),
                           env.expand)
    out = tmp_path / "pkg.zip"
    export_package([], out, [], None, expand=env.expand, extras=items)
    new = Env(tmp_path / "newpc")
    plan = plan_import(out, rules, new.expand)
    assert plan.problems == [] and all(i["extra"] for i in plan.items)
    assert {Path(i["target"]).name for i in plan.items} == {"data.db", ".gitconfig"}
    assert not (new.appdata / "Foo Bar").exists()  # 미리보기만


def forge(out, mutate):
    bad = out.with_name("forged.zip")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(bad, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "manifest.json":
                m = json.loads(data)
                mutate(m)
                data = json.dumps(m).encode()
            zout.writestr(info.filename, data)
    return bad


def test_extra_import_rejects_forged_manifest(env, rules, tmp_path):
    items = load_selection(select(env, tmp_path, ("%APPDATA%", "Foo Bar")), env.expand)
    out = tmp_path / "pkg.zip"
    export_package([], out, [], None, expand=env.expand, extras=items)
    new = Env(tmp_path / "newpc")
    cases = [
        (lambda m: m["files"][0].update(base="C:\\Windows\\System32"), "허용되지 않은"),
        (lambda m: m["files"][0].update(rel="../../evil.txt"), None),
        (lambda m: m["files"][0].update(base="%USERPROFILE%", rel="Documents/x.txt"), "점으로"),
        (lambda m: m["files"][0].update(rel="Foo Bar/license.lic"), "제외"),
    ]
    for mut, word in cases:
        plan = plan_import(forge(out, mut), rules, new.expand)
        assert plan.problems, mut
        if word:
            assert any(word in p for p in plan.problems), (word, plan.problems)
    assert not any(new.appdata.rglob("*.bat"))
    # 시작 프로그램 위치로 위조한 항목은 패키지를 막지 않고 그 항목만 '복원 안 함'(대상에 쓰지 않는다)
    plan = plan_import(forge(out, lambda m: m["files"][0].update(rel="Microsoft/Windows/Start Menu/Programs/Startup/run.bat")),
                       rules, new.expand)
    assert plan.problems == [] and [i["status"] for i in plan.items] == ["denied"]


def test_deny_list_blocks_startup_and_authorized_keys(env, rules, tmp_path):
    touch(env.appdata / "Foo Bar" / "Startup" / "run.bat")
    touch(env.home / ".ssh" / "authorized_keys")
    items = load_selection(select(env, tmp_path, ("%APPDATA%", "Foo Bar"), ("%USERPROFILE%", ".ssh")), env.expand)
    out = tmp_path / "pkg.zip"
    export_package([], out, [], None, expand=env.expand, extras=items)
    plan = plan_import(out, rules, Env(tmp_path / "n2").expand)
    # 19a: 내보내기 단계에서 같은 판정으로 걸러지고(skipped), 가져오기에서도 항목별 '복원 안 함'
    assert plan.problems == []
    m = verify_package(out)[0]
    assert sum(1 for s in m["skipped"] if s.get("denied")) == 2
    assert sum(1 for s in m["files"] if "authorized_keys" in s["rel"] or "run.bat" in s["rel"]) == 0


# --------------------------------------------------------------------- CLI

def cli_env(env, monkeypatch, programs=None):
    env.setenv(monkeypatch)
    patch_detection(monkeypatch, programs or programs_for(env), WINGET)


def test_cli_export_extra_blocks_secrets_without_flag(env, rules, tmp_path, monkeypatch, capsys):
    cli_env(env, monkeypatch)
    sel = select(env, tmp_path, ("%APPDATA%", "Acme"))  # credentials.json 포함
    out = tmp_path / "pkg.zip"
    rd = make_rules_dir(tmp_path / "rules")
    assert cli.main(["export", "--output", str(out), "--extra", str(sel), "--rules", str(rd)]) == 1
    err = capsys.readouterr().err
    assert "비밀 정보 가능성" in err and "--allow-secrets" in err
    assert not out.exists()
    assert cli.main(["export", "--output", str(out), "--extra", str(sel), "--allow-secrets",
                     "--rules", str(rd)]) == 0
    assert out.exists() and "--allow-secrets" in capsys.readouterr().err
    with zipfile.ZipFile(out) as zf:
        assert any("credentials.json" in n for n in zf.namelist())


def test_cli_export_extra_selection_error(env, tmp_path, monkeypatch, capsys):
    cli_env(env, monkeypatch)
    bad = tmp_path / "s.json"
    bad.write_text("{broken", encoding="utf-8")
    rd = make_rules_dir(tmp_path / "rules")
    assert cli.main(["export", "--output", str(tmp_path / "p.zip"), "--extra", str(bad), "--rules", str(rd)]) == 1
    assert "선택 파일 오류" in capsys.readouterr().err
    assert not (tmp_path / "p.zip").exists()


def test_cli_import_extra_needs_allow_extra(env, rules, tmp_path, monkeypatch, capsys):
    items = load_selection(select(env, tmp_path, ("%APPDATA%", "Foo Bar")), env.expand)
    out = tmp_path / "pkg.zip"
    export_package([], out, [], None, expand=env.expand, extras=items)
    new = Env(tmp_path / "newpc")
    new.setenv(monkeypatch)
    rd = make_rules_dir(tmp_path / "rules")
    assert cli.main(["import", str(out), "--dry-run", "--rules", str(rd)]) == 0
    assert "규칙 없이 선택 포함된 파일 1개" in capsys.readouterr().err
    assert cli.main(["import", str(out), "--yes", "--rules", str(rd)]) == 1
    assert "--allow-extra" in capsys.readouterr().err and not (new.appdata / "Foo Bar").exists()
    assert cli.main(["import", str(out), "--yes", "--allow-extra", "--rules", str(rd)]) == 0
    assert (new.appdata / "Foo Bar" / "data.db").exists()


def test_cli_discover_outputs_and_selection(env, tmp_path, monkeypatch, capsys):
    cli_env(env, monkeypatch)
    out, sel = tmp_path / "d.md", tmp_path / "sel.json"
    assert cli.main(["discover", "--format", "md", "--output", str(out), "--output-selection", str(sel)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Acme Tool" in text and "Microsoft Visual Studio Code" not in text
    data = json.loads(sel.read_text(encoding="utf-8"))
    assert data["candidates"] and all(c["include"] is False for c in data["candidates"])
    assert "규칙 없는 설치 프로그램 5개" in capsys.readouterr().err
    assert cli.main(["discover", "--format", "json", "--min-size-kb", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["programs"] == 5


def test_cli_discover_unexpanded_and_write_errors(tmp_path, capsys):
    assert cli.main(["discover", "--output", "%TEMP%\\d.md"]) == 1
    assert "환경변수가 확장되지 않았습니다" in capsys.readouterr().err
    assert cli.main(["discover", "--output-selection", "$env:TEMP\\s.json"]) == 1


# --------------------------------------------------------------------- C. 누락 방지 보고

def test_scan_export_checklist_print_discovery_hint(env, tmp_path, monkeypatch, capsys):
    cli_env(env, monkeypatch)
    monkeypatch.setattr(cli, "summarize", lambda rules, programs, *a, **k: (3, 7))
    rd = make_rules_dir(tmp_path / "rules")
    pat = r"규칙 없는 설치 프로그램 3개, 설정 폴더 후보 7개 — pcmigrate discover 로 확인"
    assert cli.main(["scan", "--rules", str(rd)]) == 0
    assert re.search(pat, capsys.readouterr().err)
    assert cli.main(["export", "--output", str(tmp_path / "p.zip"), "--rules", str(rd)]) == 0
    assert re.search(pat, capsys.readouterr().err)
    out = tmp_path / "c.md"
    assert cli.main(["checklist", "--format", "md", "--output", str(out), "--rules", str(rd)]) == 0
    assert re.search(pat, capsys.readouterr().err)
    assert "## 규칙 없는 프로그램(요약)" in out.read_text(encoding="utf-8")
    assert "규칙 없는 설치 프로그램 3개, 설정 폴더 후보 7개" in out.read_text(encoding="utf-8")


def test_checklist_section_summary_only_and_optional():
    cl = build_checklist([], [Program("a")], [], discovery=(4, 9))
    out = render_checklist(cl)
    assert "규칙 없는 프로그램(요약)" in out and "설정 폴더 후보 9개" in out and "자동으로 백업/복원되지 않으며" in out
    assert "규칙 없는 프로그램(요약)" not in render_checklist(build_checklist([], [Program("a")], []))
    pkg = render_checklist(build_checklist([], [Program("a")], [], discovery=(4, None)))
    assert "구 PC에서 discover 로 확인" in pkg


def test_checklist_package_mode_prints_program_count(env, tmp_path, monkeypatch, capsys):
    cli_env(env, monkeypatch)
    out = tmp_path / "pkg.zip"
    export_package([], out, [Program("Acme Tool"), Program("Microsoft Visual Studio Code"), Program("LG Update")],
                   None, expand=env.expand)
    rd = make_rules_dir(tmp_path / "rules")  # 규칙 폴더가 비어 있으면 모든 비번들 프로그램이 규칙 없음
    assert cli.main(["checklist", "--package", str(out), "--rules", str(rd), "--output", str(tmp_path / "c.txt")]) == 0
    assert "규칙 없는 설치 프로그램 2개" in capsys.readouterr().err


# --------------------------------------------------------------------- E. 알려진 도구 규칙

def test_jetbrains_rule_paths_and_excludes(rules):
    r = next(x for x in rules if x.id == "jetbrains")
    dests = {p.dest for p in r.paths}
    assert {"jetbrains/options", "jetbrains/codestyles"} <= dests
    assert all(p.path.startswith("%APPDATA%\\JetBrains\\*\\") for p in r.paths)  # LOCALAPPDATA(캐시/로그) 제외
    assert not any("LOCALAPPDATA" in p.path for p in r.paths)
    for pat in ("*.key", "*.license", "*.kdbx", "*.pwd", "*.token", "*.lic"):
        assert pat in r.exclude
    assert "Export Settings" in " ".join(r.manual_warnings) and "Settings Sync" in " ".join(r.manual_warnings)
    assert "확인 필요" in r.notes and "근거 등급" in r.notes


def test_jetbrains_collects_only_listed_subfolders(env, rules):
    from pcmigrate.package import collect_items
    r = next(x for x in rules if x.id == "jetbrains")
    base = env.appdata / "JetBrains" / "IntelliJIdea2025.1"
    touch(base / "options" / "other.xml")
    touch(base / "options" / "x.key")
    touch(base / "codestyles" / "s.xml")
    touch(base / "plugins" / "p.jar")
    touch(base / "workspace" / "w.db")
    touch(base / "idea.key")
    items, _ = collect_items(r, env.expand)
    rels = sorted(i.rel for i in items)
    assert rels == ["IntelliJIdea2025.1/codestyles/s.xml", "IntelliJIdea2025.1/options/ide.xml",
                    "IntelliJIdea2025.1/options/other.xml"]


def test_vscode_rule_has_extension_list_guidance(rules):
    r = next(x for x in rules if x.id == "vscode")
    text = " ".join(r.manual_warnings)
    assert "code --list-extensions" in text and "code --install-extension" in text
    assert "discover" in r.notes


def test_intellij_display_names_match_rule_not_other_apps(rules):
    from pcmigrate.detect import match_rules
    def ids(name):
        return {x.id for x in match_rules(rules, [Program(name)], path_exists=lambda p: False,
                                           key_exists=lambda k: False)}
    assert ids("IntelliJ IDEA Community Edition 2025.1") == {"jetbrains"}
    assert ids("PyCharm 2024.3") == {"jetbrains"}
    assert ids("JetBrains Rider 2024.1") == {"jetbrains"}
    assert ids("Provider Console") == set()
    assert ids("Slider Pro") == set()
