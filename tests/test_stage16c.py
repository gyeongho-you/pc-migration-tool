"""16c단계: 화면 표시(미룸/rejected/manifest_errors/건너뛴 링크), build_exe --work 보호, 패키지 데이터, transfer 덮어쓰기 보존.
모든 시험은 tmp 가짜 환경에서만 한다."""
import json
import subprocess
import sys

import pytest

from pcmigrate import api as real_api
from pcmigrate import cli
from pcmigrate.copier import FileResult, Report
from pcmigrate.gui import texts as T

from test_gui_controller import FakeApi, env, item, plan  # noqa: F401  (fixture)


def _run_cli(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# ----------------------------------------------------------------------------- 1(a) 미룸 표시

def _deferred_report(links=()):
    meta = dict(mode="copy", dest_verification="deferred", verification_complete=False, cloud_only_count=0,
                total_files=2, total_bytes=10, manifest_out="m.jsonl", manifest_files=2, skipped_links=len(links),
                skipped_link_paths=list(links))
    return Report(dry_run=False, results=[FileResult("s", "d", "pending_verify", 1, "x")] * 2, meta=meta)


def test_cli_transfer_deferred_is_not_called_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: _deferred_report())
    code, out, err = _run_cli(["transfer", str(tmp_path / "a"), str(tmp_path / "b"), "--json"], capsys)
    j = json.loads(out)
    assert code == 0 and j["success"] is False and j["verification_complete"] is False
    assert "복사 완료" in err and "verify" in err and "새 PC" in err
    assert "성공" not in err and "검사 통과" not in err


def test_cli_transfer_immediate_is_success(tmp_path, monkeypatch, capsys):
    rep = Report(dry_run=False, results=[FileResult("s", "d", "verified", 1, "x")],
                 meta=dict(mode="copy", dest_verification="now", verification_complete=True, cloud_only_count=0))
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: rep)
    code, out, err = _run_cli(["transfer", str(tmp_path / "a"), str(tmp_path / "b"), "--json"], capsys)
    j = json.loads(out)
    assert code == 0 and j["success"] is True and j["verification_complete"] is True
    assert "새 PC 에서 verify" not in err


def test_gui_deferred_result_says_copy_done_verify_needed(env):
    c, api, _ = env
    c.state.transfer_report = _deferred_report()
    c.state.transfer_was_dry_run = False
    sm = c.transfer_summary()
    assert sm["verification_complete"] is False and sm["success"] is False
    assert "복사 완료" in T.TR_RESULT_PENDING and "성공" not in T.TR_RESULT_PENDING and "verify" in T.TR_DEFERRED_NOTE
    assert T.TR_DEFERRED_NOTE in c.flow_notes()


# ----------------------------------------------------------------------------- 1(c) 건너뛴 링크 안내

def test_cli_transfer_lists_skipped_links(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: _deferred_report(["x/jn", "y/lnk"]))
    code, out, err = _run_cli(["transfer", str(tmp_path / "a"), str(tmp_path / "b")], capsys)
    assert "바로가기/정션(링크) 2개" in err and "x/jn" in err and "y/lnk" in err


def test_gui_flow_notes_list_skipped_links(env):
    c, api, _ = env
    c.state.transfer_report = _deferred_report(["x/jn"])
    c.state.transfer_was_dry_run = False
    notes = "\n".join(c.flow_notes())
    assert "x/jn" in notes


def test_gui_preview_notes_include_skipped_links(env):
    c, api, _ = env
    rep = _deferred_report(["x/jn"])
    rep.dry_run = True
    c.state.transfer_report = rep
    assert "x/jn" in "\n".join(c.preview_link_lines())


# ----------------------------------------------------------------------------- 1(b) rejected / manifest_errors

def test_import_preview_and_result_show_rejected(env):
    c, api, _ = env
    it = item("rejected", target="C:/x/r.txt")
    it["error"] = "복원 위치가 바로가기를 거칩니다"
    api.preview = real_api.ImportResult(plan([item(), it]), "dry-run", 0)
    c.state.package = "p.zip"
    c.start_preview()
    rows = c.preview_rows()
    assert any(r["status"] == T.PREV_STATUS["rejected"] for r in rows)
    assert c.preview_rejected_count() == 1
    assert T.PREV_REJECTED_NOTE.format(n=1) in c.preview_lines()


def test_import_counts_treat_rejected_as_failed(env):
    c, api, _ = env
    rep = Report(dry_run=False, results=[FileResult("e", "C:\a", "copied", 1, "x"),
                                         FileResult("e2", "C:\b", "rejected", 1, "x", error="링크", reason="link_in_path")])
    c.state.import_result = real_api.ImportResult(plan([item()]), "applied", 0, rep)
    ok, skipped, failed = c.import_counts()
    assert (ok, failed) == (1, 1)
    assert c.import_summary()["rejected"] == 1


def test_cli_import_apply_reports_rejected(tmp_path, monkeypatch, capsys):
    rep = Report(dry_run=False, results=[FileResult("e2", "C:\b", "rejected", 1, "x", error="링크", reason="link_in_path")])
    res = real_api.ImportResult(plan([item("rejected")]), "applied", 0, rep)
    monkeypatch.setattr(cli, "run_import", lambda *a, **k: res)
    code, out, err = _run_cli(["import", str(tmp_path / "p.zip"), "--yes", "--json"], capsys)
    j = json.loads(out)
    assert code == 1 and j["success"] is False and j["summary"] == {"rejected": 1}
    assert "복원하지 않았습니다" in err and "바로가기" in err


def test_cli_import_preview_warns_rejected(tmp_path, monkeypatch, capsys):
    res = real_api.ImportResult(plan([item(), item("rejected")]), "dry-run", 0)
    monkeypatch.setattr(cli, "run_import", lambda *a, **k: res)
    code, out, err = _run_cli(["import", str(tmp_path / "p.zip"), "--dry-run"], capsys)
    assert code == 0 and "복원되지 않을 파일 1개" in err


def test_verify_manifest_errors_surface_in_cli_and_gui(tmp_path, monkeypatch, capsys, env):
    rep = Report(dry_run=False, results=[FileResult("m", "", "failed", reason="manifest_errors", error="목록에 빠진 파일 3개")],
                 meta=dict(mode="verify", manifest_errors=3, manifest_complete=True, extra_files=0, seconds=1))
    monkeypatch.setattr(cli, "run_verify", lambda *a, **k: rep)
    (tmp_path / "t").mkdir()
    code, out, err = _run_cli(["verify", "--manifest", str(tmp_path / "m"), "--target", str(tmp_path / "t"), "--json"], capsys)
    j = json.loads(out)
    assert code == 1 and j["manifest_errors"] == 3 and j["success"] is False
    assert "구 PC 에서 읽지 못해 목록에서 빠진 파일 3개" in err
    c, api, _ = env
    c.state.transfer_report = rep
    c.state.transfer_was_dry_run = False
    sm = c.transfer_summary()
    assert sm["manifest_errors"] == 3
    assert T.TR_MANIFEST_ERRORS.format(n=3) in c.verify_notes()


# ----------------------------------------------------------------------------- C-6 build_exe --work 보호

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def build_mod():
    spec = importlib.util.spec_from_file_location("build_exe_c6", ROOT / "packaging" / "build_exe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_work_nonempty_without_marker_is_refused_and_untouched(build_mod, tmp_path):
    w = tmp_path / "mydocs"
    w.mkdir()
    (w / "keep.txt").write_text("중요")
    with pytest.raises(build_mod.WorkDirError):
        build_mod.prepare_work(w)
    assert (w / "keep.txt").read_text() == "중요" and not (w / build_mod.WORK_MARKER).exists()


def test_main_refuses_unmarked_work_and_never_deletes(build_mod, tmp_path, monkeypatch):
    w = tmp_path / "mydocs"
    w.mkdir()
    (w / "keep.txt").write_text("x")
    monkeypatch.setattr(build_mod, "build", lambda a: pytest.fail("빌드까지 가면 안 된다"))
    rc = build_mod.main(["--work", str(w), "--out", str(tmp_path / "o")])
    assert rc == 2 and (w / "keep.txt").exists()


def test_empty_or_new_work_gets_marker_and_is_cleaned(build_mod, tmp_path, monkeypatch):
    w = tmp_path / "newwork"
    exe = tmp_path / "o" / "pcmigrate" / "pcmigrate.exe"

    def fake_build(a):
        (w / "venv").mkdir()
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"x")
        return [exe]
    monkeypatch.setattr(build_mod, "build", fake_build)
    assert build_mod.main(["--work", str(w), "--out", str(tmp_path / "o"), "--no-smoke"]) == 0
    assert not w.exists()  # 표식이 있는 폴더만 정리


def test_marked_work_is_reused_and_cleaned(build_mod, tmp_path):
    w = tmp_path / "w"
    build_mod.prepare_work(w)
    (w / "venv").mkdir()
    assert (w / build_mod.WORK_MARKER).exists()
    build_mod.prepare_work(w)  # 두 번째 실행(표식 있음)은 허용
    build_mod.cleanup_work(w)
    assert not w.exists()


@pytest.mark.parametrize("bad", ["root", "home", "repo", "drive"])
def test_dangerous_work_paths_refused_even_if_empty_or_marked(build_mod, tmp_path, bad):
    p = {"root": Path(ROOT.anchor), "home": Path.home(), "repo": ROOT, "drive": Path(ROOT.anchor)}[bad]
    with pytest.raises(build_mod.WorkDirError):
        build_mod.prepare_work(p)


def test_cleanup_without_marker_does_nothing(build_mod, tmp_path):
    w = tmp_path / "x"
    w.mkdir()
    (w / "f").write_text("1")
    build_mod.cleanup_work(w)
    assert (w / "f").exists()


def test_work_inside_output_or_containing_output_refused(build_mod, tmp_path):
    out = tmp_path / "out"
    with pytest.raises(build_mod.WorkDirError):
        build_mod.prepare_work(tmp_path, out=out)  # work 가 out 을 포함


# ----------------------------------------------------------------------------- C-15 스모크에서 번들 규칙 확인

def test_cli_check_rules_loads_bundled_rules(capsys):
    code, out, err = _run_cli(["check-rules"], capsys)
    j = json.loads(out)
    assert code == 0 and j["rules"] >= 20 and Path(j["dir"]).is_dir()


def test_cli_check_rules_fails_on_missing_or_empty(tmp_path, capsys):
    code, out, err = _run_cli(["check-rules", "--rules", str(tmp_path / "none")], capsys)
    assert code == 1 and "규칙 폴더를 찾을 수 없습니다" in err
    (tmp_path / "e").mkdir()
    code, out, err = _run_cli(["check-rules", "--rules", str(tmp_path / "e")], capsys)
    assert code == 1


def test_smoke_runs_version_and_check_rules_and_fails_when_rules_missing(build_mod, tmp_path):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd[1:])
        out = "pcmigrate 0.1.0" if cmd[1:] == ["--version"] else '{"rules": 0, "dir": "x"}'
        return subprocess.CompletedProcess(cmd, 1 if (cmd[1:] == ["check-rules"]) else 0, stdout=out, stderr="")
    ok, msg = build_mod.smoke_test(tmp_path / "pcmigrate.exe", run=fake)
    assert calls == [["--version"], ["check-rules"]] and not ok and "규칙" in msg


def test_smoke_passes_when_rules_load(build_mod, tmp_path):
    def fake(cmd, **kw):
        out = "pcmigrate 0.1.0" if cmd[1:] == ["--version"] else '{"rules": 22, "dir": "x"}'
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    ok, msg = build_mod.smoke_test(tmp_path / "pcmigrate.exe", run=fake)
    assert ok and "22" in msg


# ----------------------------------------------------------------------------- C-5 pip 비편집 설치본의 rules

def test_default_rules_dir_falls_back_to_installed_package_data(monkeypatch, tmp_path):
    from pcmigrate import rules
    site = tmp_path / "site" / "pcmigrate"
    (site / "rules_data").mkdir(parents=True)
    (site / "rules_data" / "x.json").write_text("{}")
    monkeypatch.setattr(rules, "__file__", str(site / "rules.py"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert rules.default_rules_dir() == site / "rules_data"


def test_default_rules_dir_prefers_repo_rules_in_source_tree():
    from pcmigrate import rules
    d = rules.default_rules_dir()
    assert d == ROOT / "rules" and (d / "revit.json").exists()


def test_pyproject_ships_rules_as_package_data():
    import tomllib
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]
    assert cfg["package-dir"]["pcmigrate.rules_data"] == "rules"
    pkgs = set(cfg["packages"])
    assert {"pcmigrate", "pcmigrate.gui", "pcmigrate.gui.views", "pcmigrate.rules_data"} <= pkgs
    on_disk = {p.parent.relative_to(ROOT / "src").as_posix().replace("/", ".") for p in (ROOT / "src").rglob("__init__.py")}
    assert on_disk <= pkgs  # 새 하위 패키지를 만들고 목록에 빠뜨리면 설치본에서 import 가 깨진다
    assert "*.json" in cfg["package-data"]["pcmigrate.rules_data"]


# ----------------------------------------------------------------------------- 4. transfer: 덮어쓰기 전 .bak 보존

import os
import time

from pcmigrate import transfer as tr
from pcmigrate.cancel import Cancelled
from test_stage12 import FakeRobocopy, info, make_src
from test_stage12 import run as run_tr


def _bak_files(root):
    return sorted(p.name for p in Path(root).rglob("*") if ".bak" in p.name)


def test_transfer_preserves_different_existing_file_as_bak(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    (dst / "sub" / "f0.dwg").write_bytes("사용자의 기존 파일".encode())
    rep = run_tr(src, dst)
    assert (dst / "sub" / "f0.dwg").read_bytes() == (src / "sub" / "f0.dwg").read_bytes()
    assert (dst / "sub" / "f0.dwg.bak").read_bytes() == "사용자의 기존 파일".encode()
    d = rep.to_dict()
    assert d["success"] is True and d["preserved_existing_count"] == 1
    assert d["preserved_existing"][0]["backup"].endswith("f0.dwg.bak")
    assert _bak_files(dst) == ["f0.dwg.bak"]


def test_transfer_identical_existing_file_is_not_backed_up(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    shutil_copy = __import__("shutil").copytree
    shutil_copy(src, dst)  # 같은 크기/수정시각
    rep = run_tr(src, dst)
    assert _bak_files(dst) == [] and rep.meta["preserved_existing_count"] == 0


def test_transfer_bak_name_does_not_clobber_existing_bak(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    (dst / "sub" / "f0.dwg").write_bytes(b"new-old")
    (dst / "sub" / "f0.dwg.bak").write_bytes(b"older")
    run_tr(src, dst)
    assert (dst / "sub" / "f0.dwg.bak").read_bytes() == b"older"
    assert (dst / "sub" / "f0.dwg.bak.1").read_bytes() == b"new-old"


def test_transfer_dry_run_warns_counts_and_writes_nothing(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    (dst / "sub" / "f0.dwg").write_bytes(b"old")
    warns = []
    rep = run_tr(src, dst, dry_run=True, warn=warns.append)
    assert (dst / "sub" / "f0.dwg").read_bytes() == b"old" and _bak_files(dst) == []
    assert rep.meta["would_overwrite_count"] == 1
    assert any(".bak" in w and "1개" in w for w in warns)
    over = [r for r in rep.results if r.status == "would_overwrite"]
    assert over and over[0].backup.endswith("f0.dwg.bak")


class _PartialThenCancel(FakeRobocopy):
    """첫 실행: 한 파일을 중간까지만 쓰고 취소(이어받기 상황)."""

    def __call__(self, args):
        self.calls.append(list(args))
        dst = Path(args[2])
        (dst / "sub").mkdir(parents=True, exist_ok=True)
        # 실제 robocopy /Z 는 원본의 앞부분을 그대로 쓰다 끊긴다(19a: 앞부분이 아닌 다른 내용은 이어받기로 보지 않는다)
        (dst / "sub" / "f0.dwg").write_bytes((Path(args[1]) / "sub" / "f0.dwg").read_bytes()[:3])
        raise Cancelled()


def test_resume_does_not_back_up_partial_file_from_interrupted_run(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    with pytest.raises(Cancelled):
        run_tr(src, dst, runner=_PartialThenCancel())
    rep = run_tr(src, dst)  # 같은 설정으로 다시 실행(이어받기)
    assert _bak_files(dst) == [] and rep.meta["preserved_existing_count"] == 0
    assert rep.to_dict()["success"] is True


def test_rerun_after_finished_backs_up_changed_files(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    run_tr(src, dst)
    old = (src / "sub" / "f1.dwg").read_bytes()
    (src / "sub" / "f1.dwg").write_bytes(b"edited later")
    t = time.time() + 100
    os.utime(src / "sub" / "f1.dwg", (t, t))
    rep = run_tr(src, dst)
    assert (dst / "sub" / "f1.dwg.bak").read_bytes() == old and rep.meta["preserved_existing_count"] == 1


def test_preserve_failure_stops_before_copy_and_touches_nothing(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    (dst / "sub" / "f0.dwg").write_bytes(b"mine")
    rb = FakeRobocopy()
    real_replace = os.replace

    def boom(a, b, *x):
        if str(a).endswith("f0.dwg"):
            raise PermissionError(13, "in use")
        return real_replace(a, b, *x)
    monkeypatch.setattr(tr.os, "replace", boom)
    with pytest.raises(tr.TransferError) as e:
        run_tr(src, dst, runner=rb)
    assert "f0.dwg" in str(e.value) and rb.calls == []  # robocopy 는 시작하지도 않는다
    assert (dst / "sub" / "f0.dwg").read_bytes() == b"mine"


def test_empty_destination_is_not_scanned_and_has_no_preserved(tmp_path):
    rep = run_tr(make_src(tmp_path), tmp_path / "dst")
    assert rep.meta["preserved_existing_count"] == 0 and rep.meta["preserved_existing"] == []


def test_cli_transfer_announces_preserved_files(tmp_path, monkeypatch, capsys):
    rep = _deferred_report()
    rep.meta.update(preserved_existing_count=3, preserved_existing=[{"file": "a", "backup": "a.bak"}])
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: rep)
    code, out, err = _run_cli(["transfer", str(tmp_path / "a"), str(tmp_path / "b")], capsys)
    assert "3개" in err and ".bak" in err and "대상에 있던" in err


def test_gui_flow_notes_announce_preserved_files(env):
    c, api, _ = env
    rep = _deferred_report()
    rep.meta.update(preserved_existing_count=2, preserved_existing=[{"file": "a", "backup": "a.bak"}])
    c.state.transfer_report = rep
    c.state.transfer_was_dry_run = False
    assert T.TR_PRESERVED_NOTE.format(n=2) in c.flow_notes()


# ----------------------------------------------------------------------------- C-13 창 모드 exe 시작 실패 안내

def test_gui_start_failure_is_logged_and_shown_when_no_stderr(monkeypatch, tmp_path):
    pytest.importorskip("tkinter")
    from pcmigrate import errorlog
    from pcmigrate.gui import app as app_mod
    shown = []
    monkeypatch.setattr(app_mod, "_notify_fatal", lambda text: shown.append(text))
    monkeypatch.setattr(app_mod, "setup_logging", lambda: None)
    monkeypatch.setattr(app_mod, "enable_high_dpi", lambda: None)
    monkeypatch.setattr(errorlog, "PRIMARY_DIR", str(tmp_path / "L"))

    def boom(*a, **k):
        raise RuntimeError("시작 중 예외")
    monkeypatch.setattr(app_mod, "App", boom)
    monkeypatch.setattr(sys, "stderr", None)
    assert app_mod.main([]) == 1
    assert shown and "RuntimeError" in shown[0] and "기록" in shown[0]
    logs = list(Path(errorlog.log_dir()).glob("errors-*.log"))
    assert logs and "RuntimeError" in logs[0].read_text(encoding="utf-8")


def test_gui_tcl_failure_is_also_logged(monkeypatch, tmp_path):
    tk = pytest.importorskip("tkinter")
    from pcmigrate import errorlog
    from pcmigrate.gui import app as app_mod
    shown = []
    monkeypatch.setattr(app_mod, "_notify_fatal", lambda text: shown.append(text))
    monkeypatch.setattr(app_mod, "setup_logging", lambda: None)
    monkeypatch.setattr(app_mod, "enable_high_dpi", lambda: None)
    monkeypatch.setattr(errorlog, "PRIMARY_DIR", str(tmp_path / "L"))
    monkeypatch.setattr(app_mod, "App", lambda: (_ for _ in ()).throw(tk.TclError("no display")))
    monkeypatch.setattr(sys, "stderr", None)
    assert app_mod.main([]) == 1
    assert list(Path(errorlog.log_dir()).glob("errors-*.log"))


# ----------------------------------------------------------------------------- D-9 현장 안내 md/html 동기화

import html as _html
import re as _re


def _norm(text: str) -> str:
    return _re.sub(r"\s+", " ", _html.unescape(text)).strip()


def _guide():
    md = (ROOT / "docs" / "field-guide.md").read_text(encoding="utf-8")
    ht = (ROOT / "docs" / "field-guide.html").read_text(encoding="utf-8")
    return md, ht


def test_field_guide_headings_match_between_md_and_html():
    md, ht = _guide()
    md_heads = [_norm(_re.sub(r"[\[\]`*]", "", m.group(2))) for m in _re.finditer(r"^(#{2,3}) (.+)$", md, _re.M)]
    ht_heads = [_norm(_re.sub(r"<[^>]+>", "", m.group(2))) for m in _re.finditer(r"<h([23])[^>]*>(.*?)</h\1>", ht, _re.S)]
    squash = lambda xs: [_re.sub(r"\s+", "", x) for x in xs]  # noqa: E731  ([가정] 뒤 공백 같은 서식 차이는 무시)
    assert squash(md_heads) == squash(ht_heads)


def test_field_guide_code_blocks_match_between_md_and_html():
    md, ht = _guide()
    md_code = [_norm(b) for b in _re.findall(r"^```[^\n]*\n(.*?)^```", md, _re.M | _re.S)]
    ht_code = [_norm(_re.sub(r"<[^>]+>", "", b)) for b in _re.findall(r"<pre[^>]*>(.*?)</pre>", ht, _re.S)]
    assert md_code == ht_code


def test_field_guide_urls_and_troubleshooting_rows_match():
    md, ht = _guide()
    url = _re.compile(r"https?://[^\s<>)\"'`]+")
    assert sorted(set(u.rstrip(".,") for u in url.findall(md))) == sorted(set(u.rstrip(".,") for u in url.findall(ht)))
    row = _re.compile(r"^\| (.+?) \|", _re.M)
    md_syms = [_norm(_re.sub(r"[`*]", "", r)) for r in row.findall(md.split("## 7. 막혔을 때")[1].split("## 8.")[0])][1:]
    ht_part = ht.split("7. 막혔을 때")[1].split("8. 구 PC")[0]
    ht_syms = [_norm(_re.sub(r"<[^>]+>", "", c)) for c in _re.findall(r"<tr><td>(.*?)</td>", ht_part, _re.S)]
    assert md_syms == ht_syms
