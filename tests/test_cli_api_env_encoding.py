from conftest import make_rules_dir, patch_xfer
import hashlib
import io
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from pcmigrate import api, cli, copier, envpaths, transfer as tr
from pcmigrate.copier import MISMATCH, VERIFIED
from pcmigrate.detect import Program
from pcmigrate.discover import build_selection, discover, load_selection
from pcmigrate.package import export_package, plan_import
from pcmigrate.rules import load_rules
from pcmigrate.transfer import STATE_NAME, transfer

from test_discover import Env, REPO_RULES, cli_env, programs_for, select, touch
from test_checklist_winget import WINGET, patch_detection, same_size_tamper
from test_transfer import FakeRobocopy, make_src


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def snap(root):
    return {str(p): (sha(p), p.stat().st_mtime_ns) for p in Path(root).rglob("*") if p.is_file()}


# --------------------------------------------------------------------- 1. transfer --strict 복구

@pytest.mark.skipif(shutil.which("robocopy") is None, reason="robocopy 없음(Windows 전용)")
def test_strict_repairs_same_size_mtime_corruption_with_real_robocopy(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    assert transfer(src, dst).failures == []
    src_before = snap(src)
    victim = dst / "sub" / "b.rvt"
    data = bytearray(victim.read_bytes())
    data[0] ^= 0xFF  # 바이트 1개만 변경(크기/mtime 유지)
    same_size_tamper(victim, bytes(data))
    assert sha(victim) != sha(src / "sub" / "b.rvt")

    rep = transfer(src, dst)  # 기본 모드: 상태/robocopy 가 같다고 판단 -> 감지하지 못함(알려진 한계, 동작 유지)
    assert rep.failures == [] and sha(victim) != sha(src / "sub" / "b.rvt")

    rep = transfer(src, dst, strict=True)  # strict: 감지 + 해당 파일만 재복사 + 재검증
    assert rep.failures == [] and rep.meta["robocopy_ok"] is True
    assert sha(victim) == sha(src / "sub" / "b.rvt")
    assert [Path(r["file"]).name for r in rep.meta["repaired"]] == ["b.rvt"]
    assert (dst / "sub" / "b.rvt.bak").exists()  # 어긋났던 사본 보존
    assert snap(src) == src_before  # 원본 불변(해시와 mtime)
    st = {Path(r.src).name: r.status for r in rep.results}
    assert set(st.values()) == {VERIFIED}
    rep2 = transfer(src, dst, strict=True)
    assert rep2.failures == [] and "repaired" not in rep2.meta


def test_strict_repair_failure_keeps_mismatch_and_dst_untouched(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    transfer(src, dst, runner=FakeRobocopy())
    victim = dst / "a.dwg"
    same_size_tamper(victim, b"Q" * victim.stat().st_size)
    monkeypatch.setattr(copier.shutil, "copyfile",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    rep = transfer(src, dst, runner=FakeRobocopy(skip=["a.dwg"]), strict=True)
    r = next(r for r in rep.results if r.src.endswith("a.dwg"))
    assert r.status == MISMATCH and "재복사 실패" in r.error and "PermissionError" in r.error
    assert victim.read_bytes() == b"Q" * len(victim.read_bytes())
    assert not (dst / "a.dwg.bak").exists() and not list(dst.glob("*.pcmigrate-part"))
    assert (src / "a.dwg").read_bytes() == b"alpha" * 100


def test_strict_repairs_only_mismatched_files(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    transfer(src, dst, runner=FakeRobocopy())
    victim = dst / "a.dwg"
    same_size_tamper(victim, b"Q" * victim.stat().st_size)
    calls = []
    real = tr.repair_file
    patch_xfer(monkeypatch, "repair_file", lambda s, d: (calls.append(Path(s).name), real(s, d))[1])
    transfer(src, dst, runner=FakeRobocopy(skip=["a.dwg"]), strict=True)
    assert calls == ["a.dwg"]  # b.rvt, c.txt 는 건드리지 않음


def test_transfer_state_file_option_and_cli(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    rc = FakeRobocopy()
    monkeypatch.setattr(cli, "transfer", lambda *a, **k: tr.transfer(*a, runner=rc, **k))
    state = tmp_path / "elsewhere" / "state.json"
    state.parent.mkdir()
    assert cli.main(["transfer", str(src), str(tmp_path / "dst"), "--state-file", str(state)]) == 0
    assert state.exists() and not (tmp_path / "dst" / STATE_NAME).exists()
    rep = transfer(src, tmp_path / "dst", runner=rc, state_path=state)
    assert rep.meta["resumed_skipped"] == 3 and rep.meta["state_file"] == str(state)


def test_cli_transfer_reports_repair(tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path)
    rc = FakeRobocopy()
    monkeypatch.setattr(cli, "transfer", lambda *a, **k: tr.transfer(*a, runner=rc, **k))
    dst = tmp_path / "dst"
    assert cli.main(["transfer", str(src), str(dst)]) == 0
    same_size_tamper(dst / "a.dwg", b"Z" * (dst / "a.dwg").stat().st_size)
    rc.skip = {"a.dwg"}
    assert cli.main(["transfer", str(src), str(dst), "--strict"]) == 0
    assert "[복구]" in capsys.readouterr().err


# --------------------------------------------------------------------- 2. 환경변수 확장 통일

def test_expand_env_override_case_insensitive_and_unknown(monkeypatch):
    monkeypatch.setenv("PCM_TEST_A", "real")
    assert envpaths.expand_env("%PCM_TEST_A%\\x") == "real\\x"
    assert envpaths.expand_env("%pcm_test_a%") == "real"  # 대소문자 무시
    envpaths.set_overrides({"Pcm_Test_A": "over"})
    try:
        assert envpaths.expand_env("%PCM_TEST_A%") == "over"  # 덮어쓰기가 우선
        assert envpaths.expand_env("%NO_SUCH_VAR_X%\\y") == "%NO_SUCH_VAR_X%\\y"  # 모르는 변수는 그대로
    finally:
        envpaths.set_overrides(None)
    assert envpaths.expand_env("%PCM_TEST_A%") == "real"
    assert envpaths.parse_assignments(["A=1", "B=x=y"]) == {"A": "1", "B": "x=y"}
    for bad in (["A"], ["=v"]):
        with pytest.raises(ValueError):
            envpaths.parse_assignments(bad)


def test_no_direct_expandvars_in_package_sources():
    src = Path(__file__).resolve().parents[1] / "src" / "pcmigrate"
    offenders = [p.name for p in src.glob("*.py")
                 if p.name != "envpaths.py" and "expandvars" in p.read_text(encoding="utf-8")]
    assert offenders == []  # 모든 경로 해석은 envpaths.expand_env 하나로


def real_prefixes():
    keys = ("ProgramData", "PROGRAMDATA", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "ALLUSERSPROFILE")
    out = {os.environ[k].lower().rstrip("\\") for k in keys if os.environ.get(k)}
    return {p for p in out if len(p) > 3}


def test_all_locations_resolve_through_env_nothing_real_leaks(tmp_path, monkeypatch):
    real = real_prefixes()
    env = Env(tmp_path / "fake")
    touch(env.appdata / "Foo Bar" / "d.db")
    touch(env.pd / "Foo Bar" / "p.db")
    touch(env.home / "Documents" / "Foo Bar Docs" / "x.txt")
    touch(env.home / ".gitconfig", b"[user]")
    env.setenv(monkeypatch)
    for k in ("ProgramData", "ALLUSERSPROFILE"):  # 실제 값이 남아 있으면 대체(공존 변수 문제 재현 방지)
        monkeypatch.setenv(k, str(env.pd))
    programs = [Program("Foo Bar Pro", "1", "Foo"), Program("Git", "2", "")]
    rules = load_rules(REPO_RULES)
    d = discover(rules, programs, with_sizes=True)  # 기본 expand(= envpaths.expand_env)
    sel = build_selection(d, True)
    sel_file = tmp_path / "sel.json"
    for c in sel["candidates"]:
        c["include"] = c["rel"] == "Foo Bar"
    sel_file.write_text(json.dumps(sel), encoding="utf-8")
    items = load_selection(sel_file)
    out = tmp_path / "pkg.zip"
    manifest = export_package([], out, programs, None, extras=items, extra_mode="all")
    plan = plan_import(out, rules, env.expand)
    blob = (json.dumps(api_dump(d)) + json.dumps(sel) + json.dumps(manifest) + json.dumps(plan.items)).lower()
    for prefix in real:
        assert prefix not in blob, prefix  # 실제 경로가 후보/선택/패키지/계획 어디에도 안 나옴
    assert {c.root for c in d.candidates()} >= {"%APPDATA%", "%PROGRAMDATA%"}
    assert json.dumps(str(env.pd).lower())[1:-1] in blob


def api_dump(d):
    from pcmigrate.discover import to_dict
    return to_dict(d, True)


def test_cli_env_option_overrides_even_with_duplicate_case_variables(tmp_path, monkeypatch, capsys):
    """Git Bash 처럼 대소문자만 다른 PROGRAMDATA 가 공존해 실제 값이 보이는 상황을 --env 로 바로잡는다."""
    env = Env(tmp_path)
    touch(env.pd / "Foo Bar" / "p.db")
    env.setenv(monkeypatch)
    monkeypatch.setenv("ProgramData", str(tmp_path / "REAL_SHOULD_NOT_BE_USED"))
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    patch_detection(monkeypatch, [Program("Foo Bar Pro", "1", "Foo")], WINGET)
    rd = make_rules_dir(tmp_path / "rules")
    assert cli.main(["--env", f"PROGRAMDATA={env.pd}", "discover", "--format", "json", "--rules", str(rd)]) == 0
    data = json.loads(capsys.readouterr().out)
    roots = [c["root"] for e in data["programs_without_rule"] for c in e["candidates"]]
    assert roots == ["%PROGRAMDATA%"]
    assert cli.main(["--env", "BAD", "scan"]) == 2  # 형식 오류
    envpaths.set_overrides(None)


def test_import_allowed_roots_use_same_expand(tmp_path, monkeypatch):
    env = Env(tmp_path / "old")
    touch(env.pd / "Foo Bar" / "p.db")
    sel = select(env, tmp_path, ("%PROGRAMDATA%", "Foo Bar"))
    out = tmp_path / "pkg.zip"
    export_package([], out, [], None, expand=env.expand, extras=load_selection(sel, env.expand), extra_mode="all")
    new = Env(tmp_path / "new")
    new.setenv(monkeypatch)
    monkeypatch.setenv("ProgramData", str(new.pd))
    plan = plan_import(out, [])  # 기본 expand
    assert plan.problems == []
    assert Path(plan.items[0]["target"]) == new.pd / "Foo Bar" / "p.db"


# --------------------------------------------------------------------- 3. 브라우저 프로필 / 약한 연결

def browser_env(tmp_path):
    env = Env(tmp_path)
    touch(env.local / "Google" / "Chrome" / "User Data" / "Default" / "Network" / "Cookies", b"c")
    touch(env.local / "Google" / "CrashReports" / "x")
    touch(env.appdata / "Mozilla" / "Firefox" / "Profiles" / "p.default" / "cookies.sqlite", b"c")
    touch(env.local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Login Data", b"l")
    touch(env.local / "Opera Software" / "empty.txt")
    touch(env.appdata / "Foo Bar" / "d.db")
    return env


PUB_PROGRAMS = [Program("Chrome Remote Desktop Host", "1", "Google LLC"), Program("Google Drive", "1", "Google LLC"),
                Program("Foo Bar Pro", "1", "Foo"), Program("Firefox Extra Tool", "1", "Mozilla Foundation"),
                Program("Brave Helper", "1", "Brave Software"), Program("Opera Plugin", "1", "Opera")]


def test_browser_vendor_folders_flagged_secret_even_without_profile_files(tmp_path, monkeypatch):
    env = browser_env(tmp_path)
    for with_sizes in (True, False):  # 크기 스캔을 안 해도 이름만으로 경고
        d = discover(load_rules(REPO_RULES), PUB_PROGRAMS, env.expand, with_sizes=with_sizes)
        allc = d.candidates() + d.unlinked
        flagged = {c.rel for c in allc if c.secret}
        assert {"Google", "Mozilla", "BraveSoftware", "Opera Software"} <= flagged
        google = next(c for c in allc if c.rel == "Google")
        assert any("브라우저 프로필/쿠키 가능성(Google)" in s for s in google.secret)
        foo = next(c for c in d.candidates() if c.rel == "Foo Bar")
        assert not foo.secret


def test_multi_program_vendor_link_is_marked_weak(tmp_path):
    env = browser_env(tmp_path)
    d = discover(load_rules(REPO_RULES), PUB_PROGRAMS, env.expand)
    google = next(c for c in d.candidates() + d.unlinked if c.rel == "Google")
    assert google.link.weak is True and len(google.link.programs) == 2
    assert "근거 약함" in google.link.reason and "여러 프로그램" in google.link.reason
    from pcmigrate.discover import render
    assert "근거 약함" in render(d)
    single = next(c for c in d.candidates() if c.rel == "Foo Bar")
    assert single.link.weak is False


def test_selection_marks_secret_items_and_export_blocks_them(tmp_path, monkeypatch, capsys):
    env = browser_env(tmp_path)
    d = discover(load_rules(REPO_RULES), PUB_PROGRAMS, env.expand)
    sel = build_selection(d, True)
    g = next(c for c in sel["candidates"] if c["rel"] == "Google")
    assert g["requires_allow_secrets"] is True and g["include"] is False
    foo = next(c for c in sel["candidates"] if c["rel"] == "Foo Bar")
    assert foo["requires_allow_secrets"] is False
    cli_env(env, monkeypatch)
    f = select(env, tmp_path, ("%LOCALAPPDATA%", "Google"))
    rd = make_rules_dir(tmp_path / "rules")
    out = tmp_path / "pkg.zip"
    assert cli.main(["export", "--output", str(out), "--extra", str(f), "--rules", str(rd)]) == 1
    err = capsys.readouterr().err
    assert "브라우저 프로필/쿠키 가능성(Google)" in err and "--allow-secrets" in err and not out.exists()
    assert cli.main(["export", "--output", str(out), "--extra", str(f), "--allow-secrets", "--rules", str(rd)]) == 0
    assert out.exists()


# --------------------------------------------------------------------- 4. 인코딩 일관성

class FakeStream(io.TextIOWrapper):
    def __init__(self, tty=False):
        self.raw_bytes = io.BytesIO()
        super().__init__(self.raw_bytes, encoding="cp949", write_through=True)
        self._tty = tty
        self.reconfigured = False

    def isatty(self):
        return self._tty

    def reconfigure(self, **kw):
        self.reconfigured = True
        super().reconfigure(**kw)


def test_redirected_streams_are_all_utf8_including_argparse_help(monkeypatch):
    out, err = FakeStream(), FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert cli.main(["discover", "--output", "%TEMP%\\x.md"]) == 1
    assert "PC 이전 도우미" in out.raw_bytes.getvalue().decode("utf-8")  # argparse 도움말(stdout)
    assert "출력 경로를 만들 수 없습니다" in err.raw_bytes.getvalue().decode("utf-8")  # stderr


def test_console_streams_are_left_alone(monkeypatch):
    out, err = FakeStream(tty=True), FakeStream(tty=True)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert out.reconfigured is False and err.reconfigured is False


def test_unencodable_characters_never_raise(monkeypatch):
    out = FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    cli._configure_output()
    print("한글 \u2603 ☃ 😀")  # utf-8 로 통일되어 예외 없음
    assert "한글" in out.raw_bytes.getvalue().decode("utf-8")


# --------------------------------------------------------------------- 5. import 필드 / discover 진행

def extras_package(tmp_path):
    env = Env(tmp_path / "old")
    touch(env.appdata / "Foo Bar" / "data.db")
    out = tmp_path / "pkg.zip"
    export_package([], out, [], None, expand=env.expand,
                   extras=load_selection(select(env, tmp_path, ("%APPDATA%", "Foo Bar")), env.expand), extra_mode="all")
    return out


def test_import_json_reflects_real_state(tmp_path, monkeypatch, capsys):
    pkg = extras_package(tmp_path)
    new = Env(tmp_path / "new")
    new.setenv(monkeypatch)
    rd = make_rules_dir(tmp_path / "rules")

    def preview(*extra):
        capsys.readouterr()
        rc = cli.main(["import", str(pkg), "--rules", str(rd), "--json", *extra])
        return rc, json.loads(capsys.readouterr().out)

    rc, j = preview()  # --yes 없음: 아무것도 쓰지 않음
    assert rc == 2 and j["dry_run"] is True and j["written"] is False and j["mode"] == "needs-confirm"
    rc, j = preview("--dry-run")
    assert rc == 0 and j["dry_run"] is True and j["mode"] == "dry-run"
    rc, j = preview("--dry-run", "--yes")  # dry-run 우선
    assert rc == 0 and j["dry_run"] is True and j["written"] is False
    assert not (new.appdata / "Foo Bar").exists()
    capsys.readouterr()
    assert cli.main(["import", str(pkg), "--rules", str(rd), "--yes", "--allow-extra", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["dry_run"] is False
    assert (new.appdata / "Foo Bar" / "data.db").exists()


def test_discover_progress_messages(tmp_path):
    env = Env(tmp_path)
    for i in range(45):
        touch(env.appdata / f"Foo Bar {i}" / "d.db")
    msgs = []
    discover(load_rules(REPO_RULES), [Program("Foo Bar Pro 0", "1", "Foo")], env.expand, progress=msgs.append)
    text = "\n".join(msgs)
    assert "크기를 계산합니다" in text and "완료: 소요" in text and "크기 계산 20/" in text
    msgs2 = []
    discover(load_rules(REPO_RULES), [Program("Foo Bar Pro 0", "1", "Foo")], env.expand, with_sizes=False,
             progress=msgs2.append)
    assert "크기를 계산합니다" not in "\n".join(msgs2)


def test_cli_discover_prints_progress_to_stderr(tmp_path, monkeypatch, capsys):
    env = Env(tmp_path)
    touch(env.appdata / "Foo Bar" / "d.db")
    cli_env(env, monkeypatch, [Program("Foo Bar Pro", "1", "Foo")])
    rd = make_rules_dir(tmp_path / "rules")
    assert cli.main(["discover", "--format", "json", "--rules", str(rd)]) == 0
    cap = capsys.readouterr()
    assert "크기를 계산합니다" in cap.err and "완료: 소요" in cap.err
    json.loads(cap.out)  # stdout 은 순수 JSON 유지


# --------------------------------------------------------------------- 9단계 준비: api 계층(출력 없음)

@pytest.fixture
def deps(tmp_path):
    env = Env(tmp_path)
    touch(env.appdata / "Foo Bar" / "d.db")
    return env, api.Deps(registry=lambda: [Program("Foo Bar Pro", "1", "Foo"), Program("Google Chrome", "1", "")],
                         winget_export=lambda: WINGET, winget_list=lambda: None,
                         summarize=lambda rules, programs: (7, 9))


def test_api_functions_return_results_without_printing(tmp_path, deps, monkeypatch, capsys):
    env, d = deps
    env.setenv(monkeypatch)
    rd = make_rules_dir(tmp_path / "rules")
    scan = api.run_scan(rd, d)
    assert scan.program_count >= 2 and scan.discovery == (7, 9) and scan.checklist.count > 0
    assert scan.summary_dict()["matched_rules"] == []
    cl = api.run_checklist(rd, deps=d)
    assert "PC 이전 체크리스트" in cl.text and cl.discovery == (7, 9)
    disc = api.run_discover(rd, deps=d, progress=lambda m: None)
    assert disc.program_count == 2 or disc.program_count >= 1
    assert json.loads(api.selection_json(disc))["candidates"]
    exp = api.run_export(rd, tmp_path / "pkg.zip", deps=d)
    assert exp.manifest["files"] == [] and exp.discovery == (7, 9) and exp.summary_dict("x")["files"] == 0
    rep = api.run_copy(tmp_path / "rules", tmp_path / "copy_dst", dry_run=True)
    assert rep.dry_run is True
    imp = api.run_import(tmp_path / "pkg.zip", rd, deps=d)
    assert imp.mode == "needs-confirm" and not imp.written
    assert imp.preview_dict()["dry_run"] is True
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""  # api 는 stdout/stderr 에 아무것도 쓰지 않음


def test_api_errors_and_modes(tmp_path, deps, monkeypatch):
    env, d = deps
    env.setenv(monkeypatch)
    bad = tmp_path / "badrules"
    bad.mkdir()
    (bad / "x.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(api.ApiError) as ei:
        api.run_scan(bad, d)
    assert ei.value.message.startswith("규칙 오류") and ei.value.code == 1
    with pytest.raises(api.ApiError):
        api.run_checklist(tmp_path / "rules", package=tmp_path / "missing.zip", deps=d)
    pkg = extras_package(tmp_path)
    rd = make_rules_dir(tmp_path / "rules")
    new = Env(tmp_path / "new")
    new.setenv(monkeypatch)
    assert api.run_import(pkg, rd, dry_run=True, yes=True).mode == "dry-run"
    assert api.run_import(pkg, rd, yes=True).mode == "needs-allow-extra"
    res = api.run_import(pkg, rd, yes=True, allow_extra=True)
    assert res.mode == "applied" and res.written and res.report.failures == []
    # 선택 파일/비밀 정보 검사
    touch(new.appdata / "Sec" / "credentials.json")
    sel = select(new, tmp_path, ("%APPDATA%", "Sec"))
    with pytest.raises(api.ApiError) as es:
        api.run_export(rd, tmp_path / "p2.zip", extra=sel, deps=d)
    assert any("credentials.json" in line for line in es.value.lines) and "--allow-secrets" in es.value.message
    ok = api.run_export(rd, tmp_path / "p2.zip", extra=sel, allow_secrets=True, deps=d)
    assert ok.extras.risky == 1
    with pytest.raises(api.ApiError):
        api.run_export(rd, tmp_path / "p3.zip", extra=tmp_path / "nope.json", deps=d)


def test_api_format_write_error_and_unexpanded():
    lines = api.format_write_error("%TEMP%\\x", FileNotFoundError(3, "경로 없음", "Z:\\a\\x"))
    assert lines[0] == "출력 경로를 만들 수 없습니다: Z:\\a\\x (경로 없음)" and "드라이브" in lines[1]
    assert api.check_unexpanded(output="$env:TEMP\\x")[1] == api.SHELL_HINT
    assert api.check_unexpanded(output="C:\\ok") == []


def test_api_transfer_passes_options(tmp_path):
    src = make_src(tmp_path)
    rc = FakeRobocopy()
    d = api.Deps(transfer=lambda *a, **k: tr.transfer(*a, runner=rc, **k))
    state = tmp_path / "st.json"
    rep = api.run_transfer(src, tmp_path / "dst", strict=True, state_path=state, deps=d)
    assert rep.failures == [] and state.exists() and rep.meta["strict"] is True


def test_cli_commands_are_thin_wrappers():
    """cli.py 는 api 의 run_* 만 호출한다(실행 로직이 cli 에 남아 있지 않음)."""
    text = (Path(__file__).resolve().parents[1] / "src" / "pcmigrate" / "cli.py").read_text(encoding="utf-8")
    for name in ("run_scan", "run_checklist", "run_discover", "run_export", "run_import", "run_transfer", "run_copy"):
        assert f"{name}(" in text
    for forbidden in ("load_rules(", "match_rules(", "build_checklist(", "verify_package(", "apply_import("):
        assert forbidden not in text


# --------------------------------------------------------------------- README

def test_readme_has_required_sections():
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for needle in ("다른 PC(구 PC)에서 실행하기", "Python 3.10", "PYTHONPATH=src", "python -m pip install -e .",
                   ".pcmigrate-state.json", ".pcmigrate-robocopy.log", "--state-file", "--env",
                   "scan", "discover", "checklist --format md --output", "커밋", "인터넷", "PowerShell", "cmd"):
        assert needle in text, needle
