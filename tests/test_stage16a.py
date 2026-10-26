"""16a단계: 복원/전송 안전성(리뷰 A-1/A-4/A-5/B-1~B-7/E-2/F-1/F-5/F-9). 모든 시험은 tmp 가짜 환경에서만 한다."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from pcmigrate import copier, fs, manifest as mf, package, transfer as tr
from pcmigrate.copier import (FAILED, MISMATCH, MISSING, PENDING_VERIFY, SKIPPED_SAME, VERIFIED, copy_file,
                              copy_verified, repair_file)
from pcmigrate.detect import Program
from pcmigrate.package import (PackageError, apply_import, collect_extra_items, collect_items, export_package,
                               plan_import, verify_package)
from pcmigrate.rules import parse_rule

from test_package import env, make_rule  # noqa: F401  (fixture)
from test_stage12 import FakeRobocopy, info, make_src, run

nt_only = pytest.mark.skipif(os.name != "nt", reason="Windows 전용(정션/잠금)")


# ----------------------------------------------------------------------------- 도우미

def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def forge_pkg(path, files, rules=None, extra_names=None, compress=zipfile.ZIP_DEFLATED, manifest_edit=None):
    """files: [(rule, base, rel, data)] 로 manifest 와 해시가 모두 맞는 패키지를 만든다(변조 패키지 흉내)."""
    rules = rules or [make_rule()]
    m = {"format": 1, "rules": [{"id": r.id, "name": r.name, "version": r.version} for r in rules],
         "files": [], "skipped": [], "failed": [], "manual_warnings": []}
    with zipfile.ZipFile(path, "w", compression=compress) as z:
        for rid, base, rel, data in files:
            arc = f"data/{rid}/{rel}"
            m["files"].append({"arc": arc, "size": len(data), "sha256": sha(data), "rule": rid, "base": base, "rel": rel})
            z.writestr(arc, data)
        for n, d in (extra_names or []):
            z.writestr(n, d)
        if manifest_edit:
            manifest_edit(m)
        z.writestr("manifest.json", json.dumps(m))
    return Path(path)


def junction(link: Path, target: Path) -> bool:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return r.returncode == 0


class Lock:
    """파일을 공유 없이 열어 다른 프로세스(이 도구)가 읽지 못하게 한다(Windows)."""

    def __init__(self, path):
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.CreateFileW.restype = wintypes.HANDLE
        self.k = k
        self.h = k.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
        assert self.h not in (None, wintypes.HANDLE(-1).value, 0xFFFFFFFF, -1)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.k.CloseHandle(self.h)


# ----------------------------------------------------------------------------- A-1

def test_retry_failed_without_state_does_not_bak_identical_files(tmp_path):
    src = make_src(tmp_path)
    run(src, tmp_path / "dst")
    (tmp_path / "dst" / tr.STATE_NAME).unlink()  # 상태 파일이 없는 상황(취소/삭제)
    rep = run(src, tmp_path / "dst", retry_failed=True, runner=FakeRobocopy())
    assert not rep.failures
    assert not list((tmp_path / "dst").rglob("*.bak*"))


def test_copy_verified_skips_identical_destination(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    res = copy_verified(a, b)
    assert res.status == SKIPPED_SAME and not res.backup
    assert not list(tmp_path.glob("*.bak*")) and not list(tmp_path.glob("*.pcmigrate-part"))


def test_copy_verified_still_backs_up_different_destination(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"new")
    b.write_bytes(b"old")
    res = copy_verified(a, b)
    assert res.status == copier.COPIED and Path(res.backup).read_bytes() == b"old" and b.read_bytes() == b"new"


# ----------------------------------------------------------------------------- A-4

def test_deferred_copy_failure_is_not_reported_as_pending(tmp_path):
    src = make_src(tmp_path)
    out = tmp_path / "m.jsonl"
    rep = run(src, tmp_path / "dst", runner=FakeRobocopy(skip={"f1.dwg"}), manifest_out=out, verify_dest=False)
    assert [r.status for r in rep.failures] == [MISSING]  # 대상에 없으면 '검사 미룸'이 아니라 실패
    assert rep.meta["verification_complete"] is False


def test_deferred_success_is_flagged_incomplete(tmp_path):
    src = make_src(tmp_path)
    rep = run(src, tmp_path / "dst", manifest_out=tmp_path / "m.jsonl", verify_dest=False)
    d = rep.to_dict()
    assert not rep.failures and d["summary"] == {PENDING_VERIFY: 4}
    assert d["verification_complete"] is False and d["success"] is False  # 새 PC 에서 verify 를 해야 끝난다


def test_immediate_verification_is_complete_and_success(tmp_path):
    rep = run(make_src(tmp_path), tmp_path / "dst")
    d = rep.to_dict()
    assert d["verification_complete"] is True and d["success"] is True


def test_deferred_retry_recopies_missing_pending_file(tmp_path):
    src = make_src(tmp_path)
    out = tmp_path / "m.jsonl"
    run(src, tmp_path / "dst", runner=FakeRobocopy(skip={"f1.dwg"}), manifest_out=out, verify_dest=False)
    rep = run(src, tmp_path / "dst", retry_failed=True, manifest_out=out, verify_dest=False, runner=FakeRobocopy())
    assert not rep.failures and (tmp_path / "dst" / "sub" / "f1.dwg").exists()


# ----------------------------------------------------------------------------- A-5

def test_verify_fails_when_manifest_footer_has_errors(tmp_path):
    src = make_src(tmp_path)
    out = tmp_path / "m.jsonl"
    r = mf.build_manifest(src, out)
    assert not r.errors
    lines = out.read_text(encoding="utf-8").splitlines()
    footer = json.loads(lines[-1])
    footer["errors"] = 2  # 구 PC 에서 읽지 못해 목록에서 빠진 파일이 2개
    lines[-1] = json.dumps(footer)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    target = tmp_path / "dst"
    shutil.copytree(src, target)
    rep = mf.verify_against_manifest(out, target)
    assert [f.reason for f in rep.failures] == ["manifest_errors"]
    assert rep.meta["manifest_errors"] == 2


def test_verify_ok_when_footer_has_no_errors(tmp_path):
    src = make_src(tmp_path)
    out = tmp_path / "m.jsonl"
    mf.build_manifest(src, out)
    target = tmp_path / "dst"
    shutil.copytree(src, target)
    assert not mf.verify_against_manifest(out, target).failures


# ----------------------------------------------------------------------------- B-1 / B-7 / F-1

def build(env_, rules=None):
    tmp_path, old, new, expand_for = env_
    rules = rules or [make_rule()]
    out = tmp_path / "pkg.zip"
    export_package(rules, out, [], None, expand=expand_for(old))
    return out, rules, new, expand_for


def test_restore_outside_rule_pattern_rejected(env, tmp_path):
    _, old, new, expand_for = env
    base = "%ROAM%\\Vendor"
    bad = forge_pkg(tmp_path / "bad.zip", [("demo", base, "WindowsPowerShell/anything.txt", b"x")])
    plan = plan_import(bad, [make_rule()], expand_for(new))
    assert plan.items == [] and any("패턴" in p for p in plan.problems)
    with pytest.raises(PackageError):
        apply_import(bad, plan)
    assert not (new / "Vendor").exists()


def test_restore_inside_rule_pattern_allowed(env, tmp_path):
    _, old, new, expand_for = env
    ok = forge_pkg(tmp_path / "ok.zip", [("demo", "%ROAM%\\Vendor", "Demo 2024/R1/Support/sub/a.cfg", b"x"),
                                          ("demo", "%ROAM%\\Vendor", "prefs.json", b"{}")])
    plan = plan_import(ok, [make_rule()], expand_for(new))
    assert plan.problems == [] and len(plan.items) == 2


def test_file_rule_requires_exact_name(env, tmp_path):
    _, old, new, expand_for = env
    bad = forge_pkg(tmp_path / "bad.zip", [("demo", "%ROAM%\\Vendor", "prefs.json/evil.txt", b"x")])
    assert plan_import(bad, [make_rule()], expand_for(new)).problems


def test_dangerous_names_rejected_even_inside_pattern(env, tmp_path):
    _, old, new, expand_for = env
    rule = make_rule(paths=[{"path": "%ROAM%\\Docs\\*", "dest": "demo/docs", "kind": "dir"}])
    for rel in ("WindowsPowerShell/profile.ps1", "PowerShell/Microsoft.PowerShell_profile.ps1", "x/run.bat",
                "x/a.lnk", "x/a.exe", "Start Menu/Programs/Startup/a.txt", ".ssh/config", "x/.bashrc"):
        bad = forge_pkg(tmp_path / "bad.zip", [("demo", "%ROAM%\\Docs", rel, b"x")], rules=[rule])
        plan = plan_import(bad, [rule], expand_for(new))
        # 19a: 패키지 전체를 막지 않고 그 파일만 '복원 안 함'(denied). 대상에는 쓰지 않는다
        assert plan.problems == [] and [i["status"] for i in plan.items] == ["denied"], rel
        report = apply_import(bad, plan)
        assert [r.status for r in report.results] == ["denied"] and not report.failures
        assert not (new / "Docs").exists(), rel


def test_rule_that_names_extension_explicitly_may_restore_it(env, tmp_path):
    _, old, new, expand_for = env
    rule = make_rule(paths=[{"path": "%ROAM%\\Tools\\*.exe", "dest": "demo/tools", "kind": "file"}])
    ok = forge_pkg(tmp_path / "ok.zip", [("demo", "%ROAM%\\Tools", "helper.exe", b"MZ")], rules=[rule])
    assert plan_import(ok, [rule], expand_for(new)).problems == []


def test_extra_items_use_same_danger_list(env, tmp_path):
    _, old, new, expand_for = env
    bad = forge_pkg(tmp_path / "bad.zip", [("extra", "%USERPROFILE%", ".ssh/config", b"x"),
                                            ("extra", "%APPDATA%", "Tool/run.cmd", b"x")], rules=[make_rule()])
    new_env = lambda s: s.replace("%USERPROFILE%", str(new)).replace("%APPDATA%", str(new))  # noqa: E731
    plan = plan_import(bad, [make_rule()], new_env)
    assert plan.problems == [] and [i["status"] for i in plan.items] == ["denied", "denied"]


def test_shipped_rules_roundtrip_paths_match_their_patterns(tmp_path):
    """모든 규칙 파일의 path 가 자기 자신이 만드는 파일을 허용하는지(패턴 매칭 규칙이 규칙 JSON 과 어긋나지 않는지)."""
    from pcmigrate.rules import load_rules
    for rule in load_rules(Path(__file__).resolve().parents[1] / "rules"):
        for pr in rule.paths:
            split = package.split_rule_path(pr.path)
            assert split is not None
            base, pattern = split
            parts = pattern.split("/")
            sample = [p.replace("*", "x").replace("?", "x") for p in parts]
            rel = "/".join(sample if pr.kind == "file" else sample + ["sub", "f.txt"])
            assert package.rel_matches_rule(rule, base, rel), (rule.id, pr.path)


# ----------------------------------------------------------------------------- B-2 / F-5 / A-3

@nt_only
def test_collect_items_does_not_follow_junction(env, tmp_path):
    _, old, new, expand_for = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    sup = old / "Vendor" / "Demo 2024" / "R1" / "Support"
    if not junction(sup / "jn", outside):
        pytest.skip("정션을 만들 수 없음")
    items, skipped = collect_items(make_rule(), expand_for(old))
    assert not any("secret" in i.arc or "jn" in i.arc for i in items)
    assert any("jn" in s["path"] and "링크" in s["reason"] for s in skipped)


@nt_only
def test_collect_items_skips_junction_as_matched_root(env, tmp_path):
    _, old, new, expand_for = env
    outside = tmp_path / "outside" / "Support"
    outside.mkdir(parents=True)
    (outside / "secret.txt").write_text("s")
    r3 = old / "Vendor" / "Demo 2026" / "R3"
    r3.mkdir(parents=True)
    if not junction(r3 / "Support", outside):
        pytest.skip("정션을 만들 수 없음")
    items, skipped = collect_items(make_rule(), expand_for(old))
    assert not any("secret" in i.arc for i in items)
    assert any("Demo 2026" in s["path"] for s in skipped)


@nt_only
def test_collect_extra_items_does_not_follow_junction(tmp_path):
    from pcmigrate.discover import ExtraItem
    root = tmp_path / "appdata"
    (root / "Tool").mkdir(parents=True)
    (root / "Tool" / "a.cfg").write_text("a")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    if not junction(root / "Tool" / "jn", outside):
        pytest.skip("정션을 만들 수 없음")
    ex = ExtraItem(root="%APPDATA%", rel="Tool", path=root / "Tool", is_dir=True)
    items, skipped = collect_extra_items([ex])
    assert [i.rel for i in items] == ["Tool/a.cfg"]
    assert any("jn" in s["path"] for s in skipped)


@nt_only
def test_scan_source_records_skipped_junction_paths(tmp_path):
    from pcmigrate.scan import scan_source_info
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 100)
    if not junction(src / "jn", outside):
        pytest.skip("정션을 만들 수 없음")
    s = scan_source_info(src)
    assert [e[0] for e in s.entries] == ["a.txt"] and s.skipped_links == 1
    assert s.skipped_link_paths == ["jn"]


@nt_only
def test_transfer_report_lists_skipped_links(tmp_path):
    src = make_src(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 100)
    if not junction(src / "jn", outside):
        pytest.skip("정션을 만들 수 없음")
    rep = run(src, tmp_path / "dst")
    assert rep.meta["skipped_links"] == 1 and rep.meta["skipped_link_paths"] == ["jn"]


@nt_only
def test_copy_tree_does_not_follow_junction(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    if not junction(src / "jn", outside):
        pytest.skip("정션을 만들 수 없음")
    rep = copier.copy_tree(src, tmp_path / "dst")
    assert not (tmp_path / "dst" / "jn").exists()
    assert (tmp_path / "dst" / "a.txt").exists() and not rep.failures


@nt_only
def test_restore_does_not_write_through_junction(env, tmp_path):
    out, rules, new, expand_for = build(env)
    outside = tmp_path / "outside"
    outside.mkdir()
    v = new / "Vendor"
    (v / "Demo 2024" / "R1").mkdir(parents=True)
    if not junction(v / "Demo 2024" / "R1" / "Support", outside):
        pytest.skip("정션을 만들 수 없음")
    plan = plan_import(out, rules, expand_for(new))
    rep = apply_import(out, plan)
    assert list(outside.iterdir()) == []  # 링크 너머(규칙 밖)에는 아무것도 쓰지 않는다
    bad = [r for r in rep.results if r.status == copier.REJECTED]
    assert len(bad) == 1 and bad[0].reason == "link_in_path"
    assert (v / "prefs.json").read_bytes() == b"{}"  # 나머지 항목은 계속 복원


# ----------------------------------------------------------------------------- B-3

def test_copy_file_restores_original_when_second_replace_fails(tmp_path, monkeypatch):
    s, d = tmp_path / "s.txt", tmp_path / "d.txt"
    s.write_bytes(b"new")
    d.write_bytes(b"old")
    real = os.replace
    calls = []

    def flaky(a, b, *x, **k):
        calls.append((str(a), str(b)))
        if len(calls) == 2:
            raise PermissionError(13, "denied")
        return real(a, b, *x, **k)
    monkeypatch.setattr(copier.os, "replace", flaky)
    res = copy_file(s, d)
    monkeypatch.undo()
    assert res.status == FAILED
    assert d.read_bytes() == b"old"  # 원래 이름이 비지 않는다
    assert not list(tmp_path.glob("*.bak*")) and not list(tmp_path.glob("*.pcmigrate-part"))


def test_copy_file_refuses_directory_destination(tmp_path):
    s, d = tmp_path / "s.txt", tmp_path / "d.txt"
    s.write_bytes(b"x")
    d.mkdir()
    (d / "inner.txt").write_text("keep")
    res = copy_file(s, d)
    assert res.status == FAILED and res.reason == "dest_is_dir"
    assert (d / "inner.txt").read_text() == "keep" and not list(tmp_path.glob("*.bak*"))


def test_copy_verified_and_repair_restore_original_on_replace_failure(tmp_path, monkeypatch):
    for fn in (copy_verified, repair_file):
        s, d = tmp_path / "s.txt", tmp_path / "d.txt"
        s.write_bytes(b"new")
        d.write_bytes(b"old")
        real = os.replace
        calls = []

        def flaky(a, b, *x, **k):
            calls.append(1)
            if len(calls) == 2:
                raise PermissionError(13, "denied")
            return real(a, b, *x, **k)
        monkeypatch.setattr(copier.os, "replace", flaky)
        try:
            res = fn(s, d)
        finally:
            monkeypatch.undo()
        assert res.status == FAILED and d.read_bytes() == b"old", fn.__name__
        assert not list(tmp_path.glob("*.bak*"))


def test_copy_verified_refuses_directory_destination(tmp_path):
    s, d = tmp_path / "s.txt", tmp_path / "d.txt"
    s.write_bytes(b"x")
    d.mkdir()
    res = copy_verified(s, d)
    assert res.status == FAILED and res.reason == "dest_is_dir" and d.is_dir()


# ----------------------------------------------------------------------------- B-4

def test_swapped_zip_never_replaces_destination(env, tmp_path):
    out, rules, new, expand_for = build(env)
    v = new / "Vendor"
    v.mkdir()
    (v / "prefs.json").write_bytes(b"precious")
    plan = plan_import(out, rules, expand_for(new))
    assert plan.problems == []
    # 검사와 적용 사이에 zip 내용을 바꿔치기한다(manifest 는 그대로 두고 prefs.json 내용만 변조)
    swapped = tmp_path / "swapped.zip"
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(swapped, "w") as zout:
        for i in zin.infolist():
            data = zin.read(i.filename)
            zout.writestr(i.filename, b"EVIL!" if i.filename == "data/demo/prefs.json" else data)
    rep = apply_import(swapped, plan)
    assert (v / "prefs.json").read_bytes() == b"precious"  # 해시가 안 맞는 파일은 설치되지 않는다
    assert not list(v.glob("prefs.json.bak*"))
    bad = [r for r in rep.failures if r.dst.endswith("prefs.json")]
    assert len(bad) == 1 and "해시" in bad[0].error


# ----------------------------------------------------------------------------- B-5

def test_oversized_manifest_rejected(env, tmp_path, monkeypatch):
    out, rules, new, expand_for = build(env)
    monkeypatch.setattr(package, "MAX_MANIFEST_BYTES", 100)
    manifest, problems = verify_package(out)
    assert manifest is None and any("너무 큽니다" in p for p in problems)


def test_too_many_entries_rejected(env, tmp_path, monkeypatch):
    out, rules, new, expand_for = build(env)
    monkeypatch.setattr(package, "MAX_ENTRIES", 2)
    assert any("엔트리가 너무 많" in p for p in verify_package(out)[1])


def test_unsupported_compression_rejected(tmp_path):
    bad = forge_pkg(tmp_path / "bz.zip", [("demo", "%ROAM%\\Vendor", "prefs.json", b"x" * 1000)],
                    compress=zipfile.ZIP_BZIP2)
    assert any("압축 방식" in p for p in verify_package(bad)[1])


def test_entry_larger_than_declared_stops_early(tmp_path, monkeypatch):
    big = b"A" * 5000
    bad = forge_pkg(tmp_path / "lie.zip", [("demo", "%ROAM%\\Vendor", "prefs.json", big)],
                    manifest_edit=lambda m: m["files"][0].update(size=10))
    read = []
    orig = package._hash_entry

    def spy(zf, name, limit=None):
        n, h = orig(zf, name, limit)
        read.append(n)
        return n, h
    monkeypatch.setattr(package, "_hash_entry", spy)
    problems = verify_package(bad)[1]
    assert problems and max(read or [0]) <= 11


def test_duplicate_zip_entries_rejected(tmp_path):
    import warnings
    p = tmp_path / "dup.zip"
    m = {"format": 1, "rules": [{"id": "demo"}], "files": [], "manual_warnings": []}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("data/demo/a.txt", b"1")
            z.writestr("data/demo/a.txt", b"2")
            z.writestr("manifest.json", json.dumps(m))
    assert any("중복" in x for x in verify_package(p)[1])


# ----------------------------------------------------------------------------- B-6 / F-9

@pytest.mark.parametrize("rel", ["Demo 2024/NUL", "Demo 2024/nul.txt", "Demo 2024/CON", "Demo 2024/com1.cfg",
                                 "Demo 2024/a. ", "Demo 2024/a.", "Demo 2024/a ", "Demo 2024/a\x1bb",
                                 "Demo 2024/a*b", "Demo 2024/a?b", "Demo 2024/a<b", "Demo 2024/a\"b",
                                 "Demo 2024/a|b", "Demo 2024/LPT3"])
def test_windows_unsafe_names_rejected(env, tmp_path, rel):
    _, old, new, expand_for = env
    bad = forge_pkg(tmp_path / "bad.zip", [("demo", "%ROAM%\\Vendor", rel, b"x")])
    manifest, problems = verify_package(bad)
    assert problems, rel
    with pytest.raises(PackageError):
        package._safe_rel(rel)


def test_export_skips_files_with_windows_unsafe_names(env):
    tmp_path, old, new, expand_for = env
    sup = old / "Vendor" / "Demo 2024" / "R1" / "Support"
    if os.name == "nt":
        name = "\\\\?\\" + str(sup) + "\\trail."
        try:
            with open(name, "wb") as f:
                f.write(b"x")
        except OSError:
            pytest.skip("끝 점 파일을 만들 수 없음")
    else:
        (sup / "trail.").write_bytes(b"x")
    items, skipped = collect_items(make_rule(), expand_for(old))
    assert not any(i.arc.endswith("trail.") for i in items)
    assert any("이름" in s["reason"] for s in skipped)
    out = tmp_path / "pkg.zip"
    export_package([make_rule()], out, [], None, expand=expand_for(old))  # 내보내기가 자기 검사에서 죽지 않는다


def test_case_only_duplicate_targets_rejected(env, tmp_path):
    _, old, new, expand_for = env
    bad = forge_pkg(tmp_path / "dup.zip", [("demo", "%ROAM%\\Vendor", "Demo 2024/R1/Support/F.txt", b"1"),
                                            ("demo", "%ROAM%\\Vendor", "demo 2024/r1/support/f.TXT", b"2")])
    plan = plan_import(bad, [make_rule()], expand_for(new))
    assert any("같은 대상" in p for p in plan.problems)
    with pytest.raises(PackageError):
        apply_import(bad, plan)


@pytest.mark.parametrize("name", ["k.lic ", "k.lic.", "k.LIC", "k.lic. ."])
def test_license_exclusion_cannot_be_bypassed(env, tmp_path, name):
    _, old, new, expand_for = env
    bad = forge_pkg(tmp_path / "lic.zip", [("demo", "%ROAM%\\Vendor", f"Demo 2024/R1/Support/{name}", b"LICENSE")])
    plan = plan_import(bad, [make_rule()], expand_for(new))
    assert plan.items == [] and plan.problems
    assert not list((new / "Vendor").rglob("*")) if (new / "Vendor").exists() else True


def test_export_excludes_lic_with_trailing_dot_or_space_names(env):
    _, old, new, expand_for = env
    assert package._excluded("x.lic ", None) and package._excluded("x.lic.", None)


# ----------------------------------------------------------------------------- E-2

@nt_only
def test_locked_source_is_classified_in_use_not_missing_dest(tmp_path):
    src = make_src(tmp_path)
    victim = src / "sub" / "f1.dwg"
    with Lock(victim):
        rep = run(src, tmp_path / "dst", runner=FakeRobocopy(skip={"f1.dwg"}))
    fails = [r for r in rep.failures if r.src.endswith("f1.dwg")]
    assert len(fails) == 1 and fails[0].reason == "in_use", [(f.status, f.reason) for f in fails]
    assert "사용 중" in fails[0].error


def test_unreadable_source_reason_comes_from_actual_error(tmp_path, monkeypatch):
    s, d = tmp_path / "s.bin", tmp_path / "missing.bin"
    s.write_bytes(b"x")
    monkeypatch.setattr(copier, "_probe_source", lambda p: PermissionError(13, "denied"))
    res = copier.verify_file(s, d)
    assert res.status == FAILED and res.reason == "permission"


def test_missing_dest_with_readable_source_stays_missing_dest(tmp_path):
    s = tmp_path / "s.bin"
    s.write_bytes(b"x")
    res = copier.verify_file(s, tmp_path / "nope.bin")
    assert res.status == MISSING and res.reason == "missing_dest"


def test_name_conflict_with_folder_is_classified(tmp_path):
    s, d = tmp_path / "s.bin", tmp_path / "d.bin"
    s.write_bytes(b"x")
    d.mkdir()
    res = copier.verify_file(s, d)
    assert res.status == MISSING and res.reason == "dest_is_dir" and "폴더" in res.error


def test_manifest_verify_classifies_folder_conflict(tmp_path):
    src = make_src(tmp_path)
    out = tmp_path / "m.jsonl"
    mf.build_manifest(src, out)
    target = tmp_path / "t"
    shutil.copytree(src, target)
    (target / "top.txt").unlink()
    (target / "top.txt").mkdir()
    rep = mf.verify_against_manifest(out, target)
    assert [f.reason for f in rep.failures] == ["dest_is_dir"]


@nt_only
def test_discover_scan_tree_does_not_count_through_junction(tmp_path):
    from pcmigrate.discover import scan_tree
    import time
    root = tmp_path / "App"
    root.mkdir()
    (root / "a.cfg").write_bytes(b"12345")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 1000)
    if not junction(root / "jn", outside):
        pytest.skip("정션을 만들 수 없음")
    size, files, _n, _s, complete = scan_tree(root, time.monotonic() + 30, time.monotonic)
    assert (size, files, complete) == (5, 1, True)
