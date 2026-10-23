import json
import os
import shutil
from pathlib import Path

import pytest

from pcmigrate import cli, transfer as tr
from pcmigrate.copier import (CLOUD_ONLY, MISMATCH, MISSING, SKIPPED_SAME, VERIFIED,
                              WOULD_COPY, WOULD_OVERWRITE)
from pcmigrate.transfer import (STATE_NAME, TransferError, base_args, interpret_exit,
                                is_cloud_only, transfer, validate_args)


def make_src(tmp_path):
    src = tmp_path / "src"
    (src / "sub" / "deep").mkdir(parents=True)
    (src / "a.dwg").write_bytes(b"alpha" * 100)
    (src / "sub" / "b.rvt").write_bytes(b"bravo" * 100)
    (src / "sub" / "deep" / "c.txt").write_bytes(b"charlie")
    return src


class FakeRobocopy:
    """robocopy 흉내: 인자를 해석해 shutil 로 복사한다. 호출 기록을 남긴다."""

    def __init__(self, code=1, corrupt=None, skip=()):
        self.calls, self.code, self.corrupt, self.skip = [], code, corrupt, set(skip)

    def __call__(self, args):
        self.calls.append(list(args))
        src, dst = Path(args[1]), Path(args[2])
        if "/L" in args:
            return self.code, "listing"
        xf = set(args[args.index("/XF") + 1:]) if "/XF" in args else set()
        top_only = "/LEV:1" in args
        for root, dirs, files in os.walk(src):
            rel = Path(root).relative_to(src)
            (dst / rel).mkdir(parents=True, exist_ok=True)
            for n in files:
                if n in xf or n in self.skip:
                    continue
                shutil.copy2(Path(root) / n, dst / rel / n)
                if self.corrupt and n == self.corrupt:
                    with open(dst / rel / n, "ab") as f:
                        f.write(b"X")
            if top_only:
                break
        return self.code, ""


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# --------------------------------------------------------------------- 금지 옵션 / 종료코드

@pytest.mark.parametrize("bad", ["/MIR", "/mir", "/PURGE", "/MOV", "/MOVE", "/MOV:x", "/Purge"])
def test_forbidden_options_rejected(bad):
    with pytest.raises(TransferError):
        validate_args(["robocopy", "a", "b", bad])


def test_default_args_have_no_forbidden_option_and_expected_flags(tmp_path):
    args = base_args(tmp_path / "s", tmp_path / "d", tmp_path / "log.txt", dry_run=False)
    validate_args(args)
    for flag in ("/E", "/Z", "/R:3", "/W:5", "/COPY:DAT", "/DCOPY:DAT", "/NP"):
        assert flag in args
    assert f"/LOG+:{tmp_path / 'log.txt'}" in args
    dry = base_args(tmp_path / "s", tmp_path / "d", tmp_path / "log.txt", dry_run=True)
    assert "/L" in dry and not any(a.startswith("/LOG") for a in dry)


def test_runner_never_receives_forbidden_args(tmp_path):
    src = make_src(tmp_path)
    rc = FakeRobocopy()
    transfer(src, tmp_path / "dst", runner=rc)
    for call in rc.calls:
        validate_args(call)


def test_forbidden_arg_from_runner_path_blocked(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    monkeypatch.setattr(tr, "base_args", lambda *a, **k: ["robocopy", "s", "d", "/MIR"])
    rc = FakeRobocopy()
    with pytest.raises(TransferError):
        transfer(src, tmp_path / "dst", runner=rc)
    assert rc.calls == []


@pytest.mark.parametrize("code,ok", [(0, True), (1, True), (3, True), (7, True),
                                     (8, False), (9, False), (16, False)])
def test_interpret_exit(code, ok):
    assert interpret_exit(code)[0] is ok


# --------------------------------------------------------------------- 복사 + 검증

def test_transfer_copies_verifies_and_leaves_source_untouched(tmp_path):
    src = make_src(tmp_path)
    before = tree(src)
    mt = {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}
    rep = transfer(src, tmp_path / "dst", runner=FakeRobocopy())
    assert rep.failures == []
    assert {r.status for r in rep.results} == {VERIFIED}
    assert tree(src) == before
    assert mt == {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}
    assert (tmp_path / "dst" / "sub" / "b.rvt").read_bytes() == before[str(Path("sub/b.rvt"))]
    assert rep.meta["robocopy_ok"] is True and rep.meta["robocopy_runs"] == 1
    assert all(len(r.sha256) == 64 for r in rep.results)


def test_mismatch_and_missing_recorded_in_json_report(tmp_path):
    src = make_src(tmp_path)
    rep = transfer(src, tmp_path / "dst", runner=FakeRobocopy(code=1, corrupt="a.dwg", skip=["c.txt"]))
    st = {Path(r.src).name: r.status for r in rep.results}
    assert st == {"a.dwg": MISMATCH, "b.rvt": VERIFIED, "c.txt": MISSING}
    out = tmp_path / "r.json"
    rep.write(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["failed_count"] == 2 and data["robocopy_exit"] == 1


def test_robocopy_failure_code_reported(tmp_path):
    src = make_src(tmp_path)
    rep = transfer(src, tmp_path / "dst", runner=FakeRobocopy(code=8))
    assert rep.meta["robocopy_ok"] is False
    assert any("종료 코드 8" in r.error for r in rep.failures)


def test_dst_inside_src_or_same_rejected(tmp_path):
    src = make_src(tmp_path)
    for dst in (src, src / "sub", src / "new"):
        with pytest.raises(TransferError):
            transfer(src, dst, runner=FakeRobocopy())
    with pytest.raises(TransferError):
        transfer(tmp_path / "nope", tmp_path / "d", runner=FakeRobocopy())


# --------------------------------------------------------------------- dry-run

def test_dry_run_writes_nothing_and_uses_L(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    rc = FakeRobocopy()
    rep = transfer(src, dst, dry_run=True, runner=rc)
    assert not dst.exists()
    assert len(rc.calls) == 1 and "/L" in rc.calls[0]
    assert {r.status for r in rep.results} == {WOULD_COPY}

    transfer(src, dst, runner=FakeRobocopy())
    (src / "a.dwg").write_bytes(b"changed-and-longer" * 10)
    rep = transfer(src, dst, dry_run=True, runner=FakeRobocopy())
    st = {Path(r.src).name: r.status for r in rep.results}
    assert st["a.dwg"] == WOULD_OVERWRITE and st["b.rvt"] == SKIPPED_SAME
    assert (dst / "a.dwg").read_bytes() == b"alpha" * 100  # 변경 없음


# --------------------------------------------------------------------- 이어받기

def test_resume_skips_rehash_of_verified_files(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    transfer(src, dst, runner=FakeRobocopy())
    state = tr.read_state_entries(dst / STATE_NAME)
    assert len(state) == 3 and all(e["verified"] for e in state.values())

    calls = []
    real = tr.verify_file
    monkeypatch.setattr(tr, "verify_file", lambda s, d, **kw: (calls.append(s), real(s, d, **kw))[1])
    rep = transfer(src, dst, runner=FakeRobocopy())
    assert calls == [] and rep.meta["resumed_skipped"] == 3

    (src / "sub" / "b.rvt").write_bytes(b"new content")  # 원본 변경 -> 재검증
    rep = transfer(src, dst, runner=FakeRobocopy())
    assert [Path(c).name for c in calls] == ["b.rvt"]
    assert rep.failures == []


def test_interrupted_run_keeps_state_for_resume(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"

    class Boom(FakeRobocopy):
        def __call__(self, args):
            super().__call__(args)
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        transfer(src, dst, runner=Boom())
    assert (dst / STATE_NAME).exists()  # 중단돼도 상태 파일은 저장됨
    rep = transfer(src, dst, runner=FakeRobocopy())
    assert rep.failures == []


@pytest.mark.parametrize("content", ["{broken", "[]", json.dumps({"version": 99, "src": "x", "files": {}}),
                                     json.dumps({"version": 1, "src": "OTHER", "files": {}}),
                                     json.dumps({"version": 1, "src": "SRC",
                                                 "files": {"a": {"size": "x"}}})])
def test_corrupt_or_mismatched_state_causes_full_reverify(tmp_path, content, monkeypatch):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / STATE_NAME).write_text(content.replace("SRC", str(src)), encoding="utf-8")
    calls = []
    real = tr.verify_file
    monkeypatch.setattr(tr, "verify_file", lambda s, d, **kw: (calls.append(s), real(s, d, **kw))[1])
    rep = transfer(src, dst, runner=FakeRobocopy())
    assert len(calls) == 3 and rep.failures == []
    if "OTHER" in content or "broken" in content or "[]" == content or "99" in content or '"x"' in content:
        assert "state_reset" in rep.meta
    assert len(tr.read_state_entries(dst / STATE_NAME)) == 3  # 정상 상태로 다시 저장됨


def test_state_not_trusted_when_destination_changed(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    transfer(src, dst, runner=FakeRobocopy())
    (dst / "a.dwg").write_bytes(b"truncated")  # 대상 손상(크기 다름)
    rep = transfer(src, dst, runner=FakeRobocopy(skip=["a.dwg"]))
    assert {Path(r.src).name: r.status for r in rep.results}["a.dwg"] == MISMATCH


# --------------------------------------------------------------------- OneDrive 클라우드 전용

def test_cloud_attribute_detection():
    assert is_cloud_only(tr.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
    assert is_cloud_only(tr.FILE_ATTRIBUTE_RECALL_ON_OPEN | 0x20)
    assert not is_cloud_only(0x20)


def cloud_getter(*names):
    return lambda p: tr.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS if p.name in names else 0x20


def test_cloud_only_skipped_by_default_and_warned(tmp_path):
    src = make_src(tmp_path)
    warnings = []
    rc = FakeRobocopy()
    rep = transfer(src, tmp_path / "dst", runner=rc, attr_getter=cloud_getter("b.rvt"),
                   warn=warnings.append)
    st = {Path(r.src).name: r.status for r in rep.results}
    assert st == {"b.rvt": CLOUD_ONLY, "a.dwg": VERIFIED, "c.txt": VERIFIED}
    assert rep.failures == [] and rep.meta["cloud_only_count"] == 1
    assert not (tmp_path / "dst" / "sub" / "b.rvt").exists()
    assert any("클라우드" in w for w in warnings) and any("b.rvt" in w for w in warnings)
    # 폴더별 실행 + 해당 폴더에서만 /XF
    assert all("/MIR" not in c for c in rc.calls)
    xf_calls = [c for c in rc.calls if c[c.index("/XF") + 1:] != [tr.OWN_PATTERN]]  # 기록 파일 제외 패턴만 있는 호출은 제외
    assert len(xf_calls) == 1 and xf_calls[0][xf_calls[0].index("/XF") + 1:] == [tr.OWN_PATTERN, "b.rvt"]
    assert all("/LEV:1" in c for c in rc.calls)


def test_include_cloud_copies_everything(tmp_path):
    src = make_src(tmp_path)
    rc = FakeRobocopy()
    rep = transfer(src, tmp_path / "dst", runner=rc, attr_getter=cloud_getter("b.rvt"),
                   include_cloud=True)
    assert {r.status for r in rep.results} == {VERIFIED}
    assert len(rc.calls) == 1 and rc.calls[0][rc.calls[0].index("/XF") + 1:] == [tr.OWN_PATTERN]  # 클라우드 파일 이름은 제외하지 않음


# --------------------------------------------------------------------- CLI

def test_cli_transfer_dry_run_and_real(tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path)
    rc = FakeRobocopy()
    monkeypatch.setattr(tr, "default_runner", rc)
    monkeypatch.setattr(cli, "transfer", lambda *a, **k: tr.transfer(*a, runner=rc, **k))
    report = tmp_path / "rep.json"
    assert cli.main(["transfer", str(src), str(tmp_path / "dst"), "--dry-run",
                     "--report", str(report)]) == 0
    assert not (tmp_path / "dst").exists()
    assert json.loads(report.read_text(encoding="utf-8"))["dry_run"] is True
    assert cli.main(["transfer", str(src), str(tmp_path / "dst"), "--report", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["summary"] == {VERIFIED: 3}
    assert cli.main(["transfer", str(src), str(src / "inside")]) == 1


def test_copy_subcommand_still_works(tmp_path):
    src = make_src(tmp_path)
    assert cli.main(["copy", str(src), str(tmp_path / "d")]) == 0


# --------------------------------------------------------------------- 실제 robocopy (tmp_path 안에서만)

@pytest.mark.skipif(shutil.which("robocopy") is None, reason="robocopy 없음(Windows 전용)")
def test_real_robocopy_integration(tmp_path):
    src = make_src(tmp_path)
    before = tree(src)
    dst = tmp_path / "dst"
    rep = transfer(src, dst)
    assert rep.failures == [] and rep.meta["robocopy_ok"] is True
    assert {r.status for r in rep.results} == {VERIFIED}
    assert {k: v for k, v in tree(dst).items() if not k.startswith(".pcmigrate")} == before
    assert tree(src) == before
    rep2 = transfer(src, dst)  # 재실행: 이어받기로 재해시 없음
    assert rep2.meta["resumed_skipped"] == 3
