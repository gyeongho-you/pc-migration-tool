"""v0.1.1 수정 2(오류 로그/최상위 오류 처리/입력 검증)와 수정 4(안전 가드 3종)."""
import json
import os
import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pcmigrate import cli, envpaths, errorlog
from pcmigrate.manifest import ManifestError, ManifestReader
from pcmigrate.package import PackageError, plan_import, verify_package

from test_package import env, make_rule  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _reset_overrides(monkeypatch):
    # 이 파일의 테스트는 %LOCALAPPDATA%(--env 로 tmp 를 가리킴)를 해석하는 기본 동작을 검사한다
    monkeypatch.setattr(errorlog, "PRIMARY_DIR", errorlog.DEFAULT_PRIMARY_DIR)
    yield
    envpaths.set_overrides(None)


def run(argv, capsys, logs=None):
    """cli.main 실행 -> (종료 코드, stdout, stderr). logs 가 있으면 오류 로그를 그 폴더로 보낸다."""
    if logs is not None:
        argv = ["--env", f"LOCALAPPDATA={logs}", *argv]
    try:
        code = cli.main(argv)
    except SystemExit as e:  # argparse 오류
        code = e.code
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# ----------------------------------------------------------------------------- errorlog

def test_record_writes_full_block(tmp_path):
    try:
        raise ValueError("깨진 값")
    except ValueError as e:
        path = errorlog.record(e, "pcmigrate export", directory=tmp_path, now=datetime(2026, 10, 26, 14, 3, 11))
    assert path == tmp_path / "errors-20261026.log"
    text = path.read_text(encoding="utf-8")
    assert "2026-10-26 14:03:11" in text and "pcmigrate export" in text
    assert "예외: ValueError: 깨진 값" in text and "Traceback (most recent call last)" in text
    from pcmigrate import __version__
    assert f"pcmigrate {__version__}" in text


def test_record_appends_and_last_error_returns_latest(tmp_path):
    for msg in ("첫째", "둘째"):
        try:
            raise RuntimeError(msg)
        except RuntimeError as e:
            errorlog.record(e, "x", directory=tmp_path)
    path, block = errorlog.last_error(tmp_path)
    assert "둘째" in block and "첫째" not in block
    assert any("RuntimeError: 둘째" in l for l in errorlog.summarize_block(block))


def test_log_dir_uses_localappdata_via_envpaths(tmp_path):
    envpaths.set_overrides({"LOCALAPPDATA": str(tmp_path)})
    assert errorlog.log_dir() == tmp_path / "pcmigrate" / "logs"
    assert (tmp_path / "pcmigrate" / "logs").is_dir()


def test_log_dir_falls_back_to_temp_when_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # LOCALAPPDATA 가 파일이라 하위 폴더를 만들 수 없음
    envpaths.set_overrides({"LOCALAPPDATA": str(blocker)})
    monkeypatch.setattr(errorlog.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    assert errorlog.log_dir() == tmp_path / "tmp" / "pcmigrate-logs"


def test_log_dir_unresolved_variable_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(errorlog.tempfile, "gettempdir", lambda: str(tmp_path))
    assert errorlog.log_dir() == tmp_path / "pcmigrate-logs"


def test_rotation_keeps_10_files_and_rolls_at_5mb(tmp_path):
    base = datetime(2026, 1, 1)
    for i in range(14):
        try:
            raise OSError("e")
        except OSError as e:
            errorlog.record(e, "x", directory=tmp_path, now=base + timedelta(days=i))
    assert len(list(tmp_path.glob("errors-*.log"))) == errorlog.MAX_FILES
    assert not (tmp_path / "errors-20260101.log").exists() and (tmp_path / "errors-20260114.log").exists()
    big = tmp_path / "errors-20260301.log"
    big.write_bytes(b"x" * errorlog.MAX_BYTES)
    try:
        raise OSError("e")
    except OSError as e:
        p = errorlog.record(e, "x", directory=tmp_path, now=datetime(2026, 3, 1))
    assert p.name == "errors-20260301.1.log"


def test_record_never_raises(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    assert errorlog.record(RuntimeError("x"), "c", directory=f / "sub") is None


# ----------------------------------------------------------------------------- CLI 최상위 처리

def test_unexpected_exception_becomes_friendly_message_and_log(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("내부 오류 시뮬레이션")
    monkeypatch.setattr(cli, "run_scan", boom)
    logs = tmp_path / "L"
    code, out, err = run(["scan"], capsys, logs)
    assert code == 1
    assert "예상하지 못한 오류가 났습니다" in err and "기록했습니다" in err and "Traceback" not in err
    files = list((logs / "pcmigrate" / "logs").glob("errors-*.log"))
    assert len(files) == 1 and str(files[0]) in err
    text = files[0].read_text(encoding="utf-8")
    assert "RuntimeError: 내부 오류 시뮬레이션" in text and "pcmigrate scan" in text and "Traceback" in text


def test_keyboard_interrupt_exit_130(tmp_path, monkeypatch, capsys):
    def stop(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "run_scan", stop)
    code, out, err = run(["scan"], capsys, tmp_path / "L")
    assert code == 130 and "중단했습니다" in err and "Traceback" not in err
    assert not (tmp_path / "L").exists()  # 사용자 중단은 오류 로그를 만들지 않는다


def test_argparse_error_still_exit_2(capsys):
    code, out, err = run(["scan", "--nope"], capsys)
    assert code == 2


@pytest.mark.parametrize("flag,value", [("--chunk-mb", "-1"), ("--chunk-mb", "0"), ("--chunk-mb", "abc"),
                                        ("--chunk-mb", "nan"), ("--chunk-mb", "99999"),
                                        ("--workers", "0"), ("--workers", "-3"), ("--workers", "99"),
                                        ("--copy-threads", "0"), ("--copy-threads", "17")])
def test_numeric_options_rejected_with_exit_2(tmp_path, capsys, flag, value):
    src = tmp_path / "s"
    src.mkdir()
    code, out, err = run(["transfer", str(src), str(tmp_path / "d"), "--dry-run", flag, value], capsys)
    assert code == 2 and "Traceback" not in err
    assert "사이" in err or "숫자" in err or "정수" in err


@pytest.mark.parametrize("cmd", ["manifest", "verify"])
def test_chunk_and_workers_rejected_on_manifest_and_verify(tmp_path, capsys, cmd):
    base = (["manifest", str(tmp_path), "--out", str(tmp_path / "o.jsonl")] if cmd == "manifest"
            else ["verify", "--manifest", str(tmp_path / "m.jsonl"), "--target", str(tmp_path)])
    assert run([*base, "--chunk-mb", "-5"], capsys)[0] == 2
    assert run([*base, "--workers", "0"], capsys)[0] == 2


def test_valid_numeric_options_accepted():
    p = cli.build_parser()
    a = p.parse_args(["transfer", "s", "d", "--workers", "8", "--chunk-mb", "0.25", "--copy-threads", "16"])
    assert (a.workers, a.chunk_mb, a.copy_threads) == (8, 0.25, 16)


def test_logs_subcommand_prints_folder_and_last_error(tmp_path, monkeypatch, capsys):
    logs = tmp_path / "L"
    code, out, err = run(["logs"], capsys, logs)
    assert code == 0 and str(logs / "pcmigrate" / "logs") in out and "기록된 오류가 없습니다" in out
    assert "경로" in out  # 공유 전 확인 안내
    monkeypatch.setattr(cli, "run_scan", lambda *a, **k: (_ for _ in ()).throw(ValueError("마지막 오류")))
    run(["scan"], capsys, logs)
    code, out, err = run(["logs"], capsys, logs)
    assert code == 0 and "ValueError: 마지막 오류" in out and "Traceback" not in out


def test_logs_open_calls_startfile(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    code, out, err = run(["logs", "--open"], capsys, tmp_path / "L")
    assert code == 0 and opened == [str(tmp_path / "L" / "pcmigrate" / "logs")]


# ----------------------------------------------------------------------------- 깨진 manifest

def _jsonl_header(**over):
    h = {"format": "pcmigrate-manifest", "version": 1, "total_files": 0, "total_bytes": 0}
    h.update(over)
    return json.dumps(h)


def _manifest_constants():
    from pcmigrate import manifest as m
    return m.MANIFEST_FORMAT, m.MANIFEST_VERSION


BAD_JSONL = {
    "list": b"[1, 2, 3]\n",
    "null": b"null\n",
    "nested": b"[" * 200000 + b"]" * 200000 + b"\n",
    "non_utf8": b"\xff\xfe\x80\x81 not utf8\n",
    "number": b"123\n",
}


@pytest.mark.parametrize("name", sorted(BAD_JSONL))
def test_manifest_reader_header_problems_are_manifest_errors(tmp_path, name):
    f = tmp_path / "m.jsonl"
    f.write_bytes(BAD_JSONL[name])
    with pytest.raises(ManifestError):
        ManifestReader(f)


def test_manifest_reader_bad_rows_are_manifest_errors(tmp_path):
    fmt, ver = _manifest_constants()
    head = json.dumps({"format": fmt, "version": ver}) + "\n"
    for body in (b"[1,2]\n", b"null\n", b"\xff\xfe\n", b"[" * 200000 + b"]" * 200000 + b"\n"):
        f = tmp_path / "m.jsonl"
        f.write_bytes(head.encode() + body)
        with pytest.raises(ManifestError):
            list(ManifestReader(f).rows())


def test_manifest_header_bad_counts_are_manifest_errors(tmp_path, capsys):
    fmt, ver = _manifest_constants()
    m = tmp_path / "m.jsonl"
    m.write_text(json.dumps({"format": fmt, "version": ver, "total_bytes": "abc"}) + "\n", encoding="utf-8")
    tgt = tmp_path / "t"
    tgt.mkdir()
    code, out, err = run(["verify", "--manifest", str(m), "--target", str(tgt)], capsys, tmp_path / "L")
    assert code == 1 and "해시 목록 오류" in err and "Traceback" not in err


@pytest.mark.parametrize("name", sorted(BAD_JSONL))
def test_cli_verify_corrupt_manifest_friendly(tmp_path, capsys, name):
    m = tmp_path / "m.jsonl"
    m.write_bytes(BAD_JSONL[name])
    tgt = tmp_path / "t"
    tgt.mkdir()
    code, out, err = run(["verify", "--manifest", str(m), "--target", str(tgt)], capsys, tmp_path / "L")
    assert code == 1 and "해시 목록 오류" in err and "Traceback" not in err
    assert not (tmp_path / "L").exists()  # 예상된 오류이므로 '예상 밖 오류 로그'를 만들지 않는다


def _zip_with_manifest(path, manifest_bytes):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest_bytes)
    return path


def _good(**over):
    m = {"format": 1, "files": [], "rules": [], "manual_warnings": []}
    m.update(over)
    return json.dumps(m).encode()


BAD_PACKAGE_MANIFESTS = {
    "list": b"[]",
    "null": b"null",
    "nested": b"[" * 200000 + b"]" * 200000,
    "non_utf8": b"\xff\xfe\x80",
    "files_not_list": _good(files={"a": 1}),
    "file_is_list": _good(files=[["a"]]),
    "file_is_str": _good(files=["x"]),
    "arc_is_list": _good(files=[{"arc": ["a"], "size": 1, "sha256": "x", "rule": "r", "base": "b", "rel": "x"}]),
    "size_is_str": _good(files=[{"arc": "data/a", "size": "1", "sha256": "x", "rule": "r", "base": "b", "rel": "x"}]),
    "rule_is_dict": _good(files=[{"arc": "data/a", "size": 1, "sha256": "x", "rule": {}, "base": "b", "rel": "x"}]),
    "rules_not_list": _good(rules="abc"),
    "rules_items_str": _good(rules=["abc"]),
    "warnings_not_list": _good(manual_warnings=5),
    "warning_items_not_list": _good(manual_warnings=[{"name": "n", "items": 3}]),
    "warning_is_str": _good(manual_warnings=["x"]),
}


@pytest.mark.parametrize("name", sorted(BAD_PACKAGE_MANIFESTS))
def test_verify_and_plan_import_never_raise_on_corrupt_manifest(tmp_path, name):
    z = _zip_with_manifest(tmp_path / "p.zip", BAD_PACKAGE_MANIFESTS[name])
    manifest, problems = verify_package(z)
    assert problems, name
    plan = plan_import(z, [make_rule()])  # 예외 없이 문제 목록
    assert plan.problems and not plan.items


@pytest.mark.parametrize("name", sorted(BAD_PACKAGE_MANIFESTS))
def test_cli_import_corrupt_manifest_friendly(tmp_path, capsys, name):
    z = _zip_with_manifest(tmp_path / "p.zip", BAD_PACKAGE_MANIFESTS[name])
    rules = tmp_path / "rules"
    rules.mkdir()
    code, out, err = run(["import", str(z), "--dry-run", "--rules", str(rules)], capsys, tmp_path / "L")
    assert code == 1 and "Traceback" not in err and "패키지 검증 실패" in err
    assert not (tmp_path / "L").exists()


def test_plan_import_wraps_unexpected_structure_in_package_error(tmp_path, monkeypatch):
    from pcmigrate import package
    monkeypatch.setattr(package, "_plan_import", lambda *a, **k: (_ for _ in ()).throw(KeyError("rule")))
    with pytest.raises(PackageError, match="패키지 검증 실패"):
        plan_import(tmp_path / "x.zip", [])


def test_api_run_import_converts_package_error_to_api_error(tmp_path):
    from pcmigrate import api
    deps = api.Deps(plan=lambda p, r: (_ for _ in ()).throw(PackageError("구조 이상")))
    rules = tmp_path / "rules"
    rules.mkdir()
    with pytest.raises(api.ApiError, match="패키지 검증 실패"):
        api.run_import(tmp_path / "p.zip", rules, deps=deps)


def test_checklist_package_corrupt_manifest_friendly(tmp_path, capsys):
    z = _zip_with_manifest(tmp_path / "p.zip", BAD_PACKAGE_MANIFESTS["rules_items_str"])
    rules = tmp_path / "rules"
    rules.mkdir()
    code, out, err = run(["checklist", "--package", str(z), "--rules", str(rules)], capsys, tmp_path / "L")
    assert code == 1 and "패키지 검증 실패" in err and "Traceback" not in err


# ----------------------------------------------------------------------------- 수정 4(b): 출력 경로가 폴더

@pytest.mark.parametrize("cmd", ["export", "discover", "checklist"])
def test_output_that_is_existing_folder_is_rejected_and_untouched(tmp_path, capsys, cmd):
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "keep.txt").write_text("keep")
    rules = tmp_path / "rules"
    rules.mkdir()
    code, out, err = run([cmd, "--output", str(folder), "--rules", str(rules)], capsys, tmp_path / "L")
    assert code == 1 and "폴더" in err and "Traceback" not in err
    assert folder.is_dir() and (folder / "keep.txt").read_text() == "keep"
    assert not list(tmp_path.glob("out.bak*")) and not (tmp_path / "out").is_file()


def test_discover_output_selection_folder_rejected(tmp_path, capsys):
    folder = tmp_path / "sel"
    folder.mkdir()
    rules = tmp_path / "rules"
    rules.mkdir()
    code, out, err = run(["discover", "--output-selection", str(folder), "--rules", str(rules)], capsys)
    assert code == 1 and "폴더" in err and folder.is_dir() and not list(tmp_path.glob("sel.bak*"))


def test_library_level_folder_guards(tmp_path, env):
    from pcmigrate.checklist import write_output
    from pcmigrate.package import export_package
    d = tmp_path / "dir"
    d.mkdir()
    with pytest.raises(IsADirectoryError):
        write_output(d, "x")
    with pytest.raises(IsADirectoryError):
        export_package([], d, [], None)
    assert d.is_dir() and not list(tmp_path.glob("dir.bak*"))


def test_import_report_equal_to_package_rejected(tmp_path, capsys):
    z = _zip_with_manifest(tmp_path / "p.zip", _good())
    before = z.read_bytes()
    rules = tmp_path / "rules"
    rules.mkdir()
    for rep in (str(z), str(z).upper() if os.name == "nt" else str(z)):
        code, out, err = run(["import", str(z), "--dry-run", "--report", rep, "--rules", str(rules)], capsys)
        assert code == 1 and "패키지 파일 자신" in err
        assert z.read_bytes() == before


# ----------------------------------------------------------------------------- 수정 4(a): 원본 폴더 안에 쓰기 금지

def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.fixture
def srcdir(tmp_path):
    s = tmp_path / "SrcFolder"
    (s / "sub").mkdir(parents=True)
    (s / "sub" / "a.txt").write_text("a")
    return s


@pytest.mark.parametrize("flag", ["--report", "--log", "--state-file", "--manifest-out"])
def test_transfer_outputs_inside_source_rejected(tmp_path, srcdir, capsys, flag):
    before = _tree(srcdir)
    code, out, err = run(["transfer", str(srcdir), str(tmp_path / "dst"), flag, str(srcdir / "sub" / "x.out")], capsys)
    assert code == 1 and "원본 폴더 안" in err and "Traceback" not in err
    assert _tree(srcdir) == before and not (tmp_path / "dst").exists()


def test_transfer_output_inside_source_case_insensitive(tmp_path, srcdir, capsys):
    if os.name != "nt":
        pytest.skip("대소문자 무시는 Windows 전용")
    code, out, err = run(["transfer", str(srcdir), str(tmp_path / "dst"), "--report",
                          str(srcdir).upper() + "\\r.json"], capsys)
    assert code == 1 and "원본 폴더 안" in err


@pytest.mark.skipif(os.name != "nt", reason="정션은 Windows 전용 시나리오")
def test_transfer_output_inside_source_via_junction(tmp_path, srcdir, capsys):
    junction = tmp_path / "J"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(srcdir)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("정션을 만들 수 없음")
    try:
        before = _tree(srcdir)
        code, out, err = run(["transfer", str(srcdir), str(tmp_path / "dst"), "--report",
                              str(junction / "r.json")], capsys)
        assert code == 1 and "원본 폴더 안" in err and _tree(srcdir) == before
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(junction)], capture_output=True)


def test_manifest_out_inside_source_rejected(tmp_path, srcdir, capsys):
    before = _tree(srcdir)
    code, out, err = run(["manifest", str(srcdir), "--out", str(srcdir / "m.jsonl")], capsys)
    assert code == 1 and "원본 폴더 안" in err and _tree(srcdir) == before


def test_copy_report_inside_source_rejected(tmp_path, srcdir, capsys):
    before = _tree(srcdir)
    code, out, err = run(["copy", str(srcdir), str(tmp_path / "dst"), "--report", str(srcdir / "r.json")], capsys)
    assert code == 1 and "원본 폴더 안" in err and _tree(srcdir) == before and not (tmp_path / "dst").exists()


def test_api_run_transfer_itself_refuses_outputs_inside_source(tmp_path, srcdir):
    from pcmigrate import api
    called = []
    deps = api.Deps(transfer=lambda *a, **k: called.append(1))
    with pytest.raises(api.ApiError, match="원본 폴더 안"):
        api.run_transfer(srcdir, tmp_path / "d", state_path=srcdir / ".s.json", deps=deps)
    assert not called


def test_outputs_outside_source_still_allowed(tmp_path, srcdir, capsys):
    out_manifest = tmp_path / "ok.jsonl"
    code, out, err = run(["manifest", str(srcdir), "--out", str(out_manifest)], capsys)
    assert code == 0 and out_manifest.exists()
    assert "a.txt" in out_manifest.read_text(encoding="utf-8")


def test_sibling_folder_with_same_prefix_is_not_inside(tmp_path, srcdir, capsys):
    sibling = tmp_path / "SrcFolder2"
    sibling.mkdir()
    code, out, err = run(["manifest", str(srcdir), "--out", str(sibling / "m.jsonl")], capsys)
    assert code == 0


# ----------------------------------------------------------------------------- 수정 4(c): 빈 문자열 경로

@pytest.mark.parametrize("argv", [
    ["transfer", "", "D:/x"], ["transfer", "D:/x", ""], ["transfer", "D:/x", "D:/y", "--report", ""],
    ["transfer", "D:/x", "D:/y", "--log", " "], ["transfer", "D:/x", "D:/y", "--state-file", ""],
    ["transfer", "D:/x", "D:/y", "--manifest-out", ""], ["transfer", "D:/x", "D:/y", "--use-manifest", ""],
    ["copy", "", "D:/x"], ["copy", "D:/x", ""], ["copy", "D:/x", "D:/y", "--report", ""],
    ["manifest", "", "--out", "D:/m.jsonl"], ["manifest", "D:/x", "--out", ""],
    ["verify", "--manifest", "", "--target", "D:/x"], ["verify", "--manifest", "D:/m", "--target", ""],
    ["verify", "--manifest", "D:/m", "--target", "D:/x", "--report", ""],
    ["export", "--output", ""], ["discover", "--output", ""], ["checklist", "--output", ""],
    ["import", "", "--dry-run"], ["import", "D:/p.zip", "--dry-run", "--report", ""],
])
def test_empty_string_paths_rejected(argv, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 빈 경로가 현재 폴더로 처리되면 여기에 흔적이 남는다
    code, out, err = run(argv, capsys, tmp_path / "L")
    assert code == 1 and "경로가 비어 있습니다" in err and "Traceback" not in err
    assert not list(tmp_path.glob("*.bak*")) and list(tmp_path.iterdir()) == []
