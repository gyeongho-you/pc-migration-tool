"""v0.1.1 수정 1: 다른 프로세스가 잠근 파일을 읽지 못해도 패키지에 흔적이 남지 않고 내보내기는 끝까지 간다."""
from conftest import make_rules_dir
import os
import zipfile
from pathlib import Path

import pytest

from pcmigrate import package
from pcmigrate.package import (LOCK_NAMES, SourceUnreadable, _write_entry, export_package, plan_import,
                               verify_package)
from test_package import env, make_rule  # noqa: F401  (fixture)

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"
winonly = pytest.mark.skipif(os.name != "nt", reason="바이트 범위 잠금은 Windows 전용 시나리오")


class ByteLock:
    """msvcrt.locking 으로 파일 앞부분의 바이트 범위를 잠근다(Chrome 이 LOCK 파일에 하는 것과 같은 방식)."""

    def __init__(self, path: Path, nbytes: int = 1) -> None:
        self.path, self.nbytes = path, nbytes

    def __enter__(self):
        import msvcrt
        self.f = open(self.path, "r+b")
        msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, self.nbytes)
        return self

    def __exit__(self, *exc):
        import msvcrt
        self.f.seek(0)
        msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, self.nbytes)
        self.f.close()


@winonly
def test_locked_file_leaves_no_trace_in_zip(tmp_path):
    src = tmp_path / "LOCKED.dat"
    src.write_bytes(b"x" * 4096)
    zpath = tmp_path / "t.zip"
    with ByteLock(src, 100), zipfile.ZipFile(zpath, "w") as zf:
        with pytest.raises(SourceUnreadable) as ei:
            _write_entry(zf, "data/x/LOCKED.dat", src, str(tmp_path))
        assert zf.namelist() == []
    assert ei.value.failure.code == "in_use"


@winonly
def test_export_with_locked_file_succeeds_and_reports(env):
    tmp_path, old, _, expand_for = env
    target = old / "Vendor" / "Demo 2024" / "R1" / "Support" / "a.cfg"
    out = tmp_path / "p.zip"
    with ByteLock(target, 2):
        m = export_package([make_rule()], out, [], None, expand=expand_for(old))
    assert len(m["failed"]) == 1 and m["failed"][0]["reason"] == "in_use"
    assert "사용 중" in m["failed"][0]["error"]
    manifest, problems = verify_package(out)
    assert problems == []
    assert "data/demo/support/a.cfg" not in zipfile.ZipFile(out).namelist()
    assert len(manifest["files"]) == 2
    assert not list(tmp_path.glob(".pcmigrate-stage-*")) and not list(tmp_path.glob("*.pcmigrate-part"))


def test_unreadable_simulated_anywhere_leaves_no_zip_entry(env, monkeypatch):
    """어떤 OS 에서도: 읽는 도중 실패(두 번째 read)해도 zip 에 반쪽 엔트리가 없다."""
    tmp_path, old, _, expand_for = env
    real_open = open

    class Flaky:
        def __init__(self, f):
            self.f, self.n = f, 0

        def read(self, size=-1):
            self.n += 1
            if self.n > 1:
                raise PermissionError(33, "lock violation")
            return self.f.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

    big = old / "Vendor" / "Demo 2024" / "R1" / "Support" / "a.cfg"
    big.write_bytes(b"z" * 10)
    monkeypatch.setattr(package, "CHUNK", 4)

    def fake_open(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        return Flaky(f) if Path(str(path)).name == "a.cfg" and "b" in mode and "r" in mode else f
    monkeypatch.setattr(package, "open", fake_open, raising=False)
    out = tmp_path / "p.zip"
    m = export_package([make_rule()], out, [], None, expand=expand_for(old))
    assert [f["src"] for f in m["failed"]] == [str(big)]
    assert verify_package(out)[1] == []
    assert not any(n.endswith("a.cfg") for n in zipfile.ZipFile(out).namelist())


def test_lock_names_excluded_globally(env):
    tmp_path, old, _, expand_for = env
    d = old / "Vendor" / "Demo 2024" / "R1" / "Support"
    for n in LOCK_NAMES:
        (d / n).write_bytes(b"1")
    items, skipped = package.collect_items(make_rule(), expand_for(old))
    names = {i.src.name for i in items}
    assert names.isdisjoint(LOCK_NAMES) and "a.cfg" in names
    assert {Path(x["path"]).name for x in skipped} >= set(LOCK_NAMES)


def test_extra_items_exclude_lock_names(tmp_path):
    from types import SimpleNamespace
    d = tmp_path / "Google" / "Chrome" / "User Data"
    d.mkdir(parents=True)
    (d / "LOCK").write_bytes(b"")
    (d / "SingletonLock").write_bytes(b"")
    (d / "Preferences").write_bytes(b"{}")
    ex = SimpleNamespace(path=str(tmp_path / "Google"), root="%LOCALAPPDATA%", rel="Google")
    items, _ = package.collect_extra_items([ex])
    assert [i.src.name for i in items] == ["Preferences"]


def test_no_rule_targets_a_lock_name():
    """전역 제외 이름이 규칙이 의도한 파일 이름과 충돌하지 않는다."""
    from pcmigrate.rules import load_rules
    lowered = {n.lower() for n in LOCK_NAMES}
    for r in load_rules(REPO_RULES):
        for pr in r.paths:
            last = pr.path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
            assert last not in lowered, (r.id, pr.path)


def test_old_package_with_lock_entry_still_imports(env):
    """이전 버전이 만든 패키지에 LOCK 엔트리가 있어도 가져오기는 막히지 않고 그 파일만 건너뛴다."""
    tmp_path, old, new, expand_for = env
    d = old / "Vendor" / "Demo 2024" / "R1" / "Support"
    (d / "LOCK").write_bytes(b"1")
    old_out = tmp_path / "old.zip"
    orig = package.GLOBAL_EXCLUDE
    package.GLOBAL_EXCLUDE = ("*.lic", "*.pcmigrate-part")  # 이전 버전 동작을 흉내
    try:
        export_package([make_rule()], old_out, [], None, expand=expand_for(old))
    finally:
        package.GLOBAL_EXCLUDE = orig
    assert any(n.endswith("/LOCK") for n in zipfile.ZipFile(old_out).namelist())
    plan = plan_import(old_out, [make_rule()], expand_for(new))
    assert plan.problems == []
    assert plan.items and all(not it["target"].endswith("LOCK") for it in plan.items)


def test_verify_failure_after_export_leaves_no_temp_and_explains(env, monkeypatch):
    tmp_path, old, _, expand_for = env
    monkeypatch.setattr(package, "verify_package", lambda p: (None, ["manifest 에 없는 엔트리: data/x"]))
    out = tmp_path / "p.zip"
    with pytest.raises(package.PackageError) as ei:
        export_package([make_rule()], out, [], None, expand=expand_for(old))
    assert "저장하지 않았습니다" in str(ei.value) and "data/x" in str(ei.value)
    assert not out.exists() and not list(tmp_path.glob("*.pcmigrate-part"))
    assert not list(tmp_path.glob(".pcmigrate-stage-*"))


def test_large_file_is_staged_on_disk_not_memory(env, monkeypatch):
    tmp_path, old, _, expand_for = env
    seen = {}
    real = package.tempfile.SpooledTemporaryFile

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)
    monkeypatch.setattr(package.tempfile, "SpooledTemporaryFile", spy)
    export_package([make_rule()], tmp_path / "p.zip", [], None, expand=expand_for(old))
    assert seen["max_size"] == package.SPOOL_MAX and Path(seen["dir"]).parent == tmp_path


def test_copy_verified_unexpected_exception_leaves_no_part_file(tmp_path):
    """수정 1(e): copier 도 예상 밖 예외(OSError 아님)에서 임시 파일(.pcmigrate-part)을 남기지 않는다."""
    from pcmigrate.copier import PART_SUFFIX, copy_verified
    src, dst = tmp_path / "a.bin", tmp_path / "out" / "a.bin"
    src.write_bytes(b"q" * 100)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("unexpected")
        return False
    with pytest.raises(RuntimeError):
        copy_verified(src, dst, chunk=10, cancel=boom)
    assert not list((tmp_path / "out").glob("*" + PART_SUFFIX)) and not dst.exists()


def test_copy_verified_read_failure_leaves_no_part_file(tmp_path, monkeypatch):
    from pcmigrate import copier
    src, dst = tmp_path / "a.bin", tmp_path / "out" / "a.bin"
    src.write_bytes(b"q" * 100)
    real = copier.sha256_file

    def fail(*a, **k):
        raise PermissionError(33, "locked")
    monkeypatch.setattr(copier, "sha256_file", fail)
    res = copier.copy_verified(src, dst)
    assert res.status == "failed" and not dst.exists()
    assert not list((tmp_path / "out").glob("*" + copier.PART_SUFFIX))


def _fake_manifest(n_unreadable=7, hard=0):
    failed = [{"src": f"D:/data/Chrome/LOCK{i}", "error": "사용 중이라 읽지 못함(다른 프로그램이 파일을 잡고 있습니다)",
               "reason": "in_use"} for i in range(n_unreadable)]
    failed += [{"src": "dup", "error": "중복된 패키지 경로 data/x"} for _ in range(hard)]
    return {"rules": [], "files": [{"arc": "a"}], "skipped": [], "failed": failed, "manual_warnings": []}


def _cli_export(monkeypatch, tmp_path, manifest):
    from pcmigrate import cli
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [])
    monkeypatch.setattr(cli, "winget_export_raw", lambda: None)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: None)
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: manifest)
    rules = make_rules_dir(tmp_path / "rules")
    return cli.main(["export", "--output", str(tmp_path / "o.zip"), "--rules", str(rules)])


def test_cli_export_unreadable_is_prominent_success_with_five_examples(tmp_path, monkeypatch, capsys):
    assert _cli_export(monkeypatch, tmp_path, _fake_manifest(7)) == 0
    cap = capsys.readouterr()
    assert "읽지 못해 제외된 파일 7개" in cap.err and "Chrome" in cap.err and "닫고 다시" in cap.err
    assert cap.err.count("LOCK") == 5 and "외 2개" in cap.err
    assert '"unreadable": 7' in cap.out


def test_cli_export_hard_failure_still_exit_1(tmp_path, monkeypatch, capsys):
    assert _cli_export(monkeypatch, tmp_path, _fake_manifest(0, hard=1)) == 1


def test_write_output_failure_leaves_no_part_file(tmp_path, monkeypatch):
    """수정 1(e): 체크리스트/탐지 결과 저장이 중간에 실패해도 임시 파일이 남지 않고 기존 파일은 그대로다."""
    from pcmigrate import checklist
    target = tmp_path / "c.md"
    target.write_text("old", encoding="utf-8")
    real_replace = os.replace

    def fail(src, dst):
        raise PermissionError("locked")
    monkeypatch.setattr(checklist.os, "replace", fail)
    with pytest.raises(PermissionError):
        checklist.write_output(target, "new")
    monkeypatch.setattr(checklist.os, "replace", real_replace)
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("*.pcmigrate-part"))
