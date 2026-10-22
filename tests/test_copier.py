import json
import os
import stat

from pcmigrate import copier
from pcmigrate.cli import main
from pcmigrate.copier import (COPIED, FAILED, SKIPPED_SAME, WOULD_COPY,
                              WOULD_OVERWRITE, copy_file, copy_tree, sha256_file)


def make_src(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_bytes(b"alpha")
    (src / "sub" / "b.bin").write_bytes(b"\x00\x01" * 1000)
    return src


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_copy_tree_verifies_and_leaves_source_untouched(tmp_path):
    src = make_src(tmp_path)
    before = snapshot(src)
    mtimes = {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}
    rep = copy_tree(src, tmp_path / "dst")
    assert [r.status for r in rep.results] == [COPIED, COPIED]
    assert snapshot(tmp_path / "dst") == before
    assert snapshot(src) == before
    assert mtimes == {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}
    r = rep.results[0]
    assert r.sha256 == sha256_file(tmp_path / "dst" / "a.txt") and r.size == 5
    assert not list((tmp_path / "dst").rglob("*" + copier.PART_SUFFIX))


def test_dry_run_changes_nothing(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "a.txt").write_bytes(b"old")
    rep = copy_tree(src, dst, dry_run=True)
    assert {r.status for r in rep.results} == {WOULD_OVERWRITE, WOULD_COPY}
    assert snapshot(dst) == {"a.txt": b"old"}
    assert not (dst / "sub").exists()


def test_overwrite_creates_bak_and_preserves_old_baks(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "a.txt").write_bytes(b"old1")
    copy_tree(src, dst)
    assert (dst / "a.txt.bak").read_bytes() == b"old1"
    assert (dst / "a.txt").read_bytes() == b"alpha"

    (src / "a.txt").write_bytes(b"alpha2")
    rep = copy_tree(src, dst)
    assert (dst / "a.txt.bak").read_bytes() == b"old1"  # 기존 백업 보존
    assert (dst / "a.txt.bak.1").read_bytes() == b"alpha"
    assert next(r for r in rep.results if r.dst.endswith("a.txt")).backup.endswith(".bak.1")


def test_identical_file_skipped_without_backup(tmp_path):
    src = make_src(tmp_path)
    copy_tree(src, tmp_path / "dst")
    rep = copy_tree(src, tmp_path / "dst")
    assert {r.status for r in rep.results} == {SKIPPED_SAME}
    assert not list((tmp_path / "dst").rglob("*.bak*"))


def test_verification_failure_recorded_and_cleaned(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    real_copy = copier.shutil.copyfile

    def corrupt(s, d, **kw):
        real_copy(s, d, **kw)
        with open(d, "ab") as f:
            f.write(b"x")
    monkeypatch.setattr(copier.shutil, "copyfile", corrupt)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "a.txt").write_bytes(b"keep")
    rep = copy_tree(src, dst)
    assert all(r.status == FAILED and "검증" in r.error for r in rep.results)
    assert (dst / "a.txt").read_bytes() == b"keep"  # 검증 실패 시 기존 파일 유지
    assert not list(dst.rglob("*" + copier.PART_SUFFIX))


def test_one_failure_does_not_stop_others(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    real_stat_hash = copier.sha256_file

    def flaky(p):
        if str(p).endswith("a.txt") and "src" in str(p):
            raise PermissionError("locked")
        return real_stat_hash(p)
    monkeypatch.setattr(copier, "sha256_file", flaky)
    rep = copy_tree(src, tmp_path / "dst")
    statuses = {os.path.basename(r.src): r.status for r in rep.results}
    assert statuses == {"a.txt": FAILED, "b.bin": COPIED}
    assert "PermissionError" in rep.failures[0].error


def test_missing_source_and_dst_inside_src(tmp_path):
    rep = copy_tree(tmp_path / "nope", tmp_path / "dst")
    assert rep.failures and "원본" in rep.failures[0].error
    src = make_src(tmp_path)
    rep = copy_tree(src, src / "inside")
    assert rep.failures and not (src / "inside").exists()


def test_copy_single_file_into_dir(tmp_path):
    f = tmp_path / "one.txt"
    f.write_bytes(b"1")
    (tmp_path / "d").mkdir()
    res = copy_file(f, tmp_path / "d" / "one.txt")
    assert res.status == COPIED


def test_report_json_and_cli(tmp_path, capsys):
    src = make_src(tmp_path)
    report = tmp_path / "report.json"
    assert main(["copy", str(src), str(tmp_path / "dst"), "--dry-run",
                 "--report", str(report)]) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["dry_run"] is True and data["summary"] == {WOULD_COPY: 2}
    assert not (tmp_path / "dst").exists()

    assert main(["copy", str(tmp_path / "missing"), str(tmp_path / "d2"),
                 "--report", str(report)]) == 1
    assert json.loads(report.read_text(encoding="utf-8"))["failed_count"] == 1


def test_cli_unimplemented_subcommands(capsys):
    assert main(["export"]) == 2
    assert main(["import", "--dry-run"]) == 2
    assert "구현되지 않았습니다" in capsys.readouterr().err
