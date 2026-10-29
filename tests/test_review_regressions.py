"""19a단계: 정밀 리뷰(R1)와 종단 시험(R2)에서 나온 문제의 재현 시험. 모든 시험은 tmp 가짜 환경에서만 한다."""
import json
import os
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_rules_dir, patch_pkg, patch_xfer
from pcmigrate import api, cli, errorlog, fs, package, rules as rules_mod, transfer as tr
from pcmigrate.copier import Report
from pcmigrate.detect import Program, display_with_version
from pcmigrate.fs import DestInfo
from pcmigrate.package import (PackageError, apply_import, collect_items, export_package, extract_winget, plan_import,
                               restore_denied, unsafe_name, verify_package)
from pcmigrate.rules import RuleError, load_rules, parse_rule

from test_transfer_safety_manifest import FakeRobocopy, info, make_src
from test_transfer_safety_manifest import run as run_tr

nt_only = pytest.mark.skipif(os.name != "nt", reason="Windows 전용")
REPO_RULES = Path(__file__).resolve().parents[1] / "rules"
NOW = lambda: datetime(2030, 1, 1)  # noqa: E731


def make_rule(**over):
    data = {"id": "demo", "name": "Demo", "version": "7", "detect": {"display_name_patterns": ["Demo"]},
            "paths": [{"path": "%ROAM%\\Vendor\\Demo", "dest": "demo/cfg", "kind": "dir"}],
            "manual_warnings": ["재로그인 필요"]}
    data.update(over)
    return parse_rule(data)


@pytest.fixture
def env(tmp_path):
    old = tmp_path / "old"
    demo = old / "Vendor" / "Demo"
    demo.mkdir(parents=True)
    (demo / "a.cfg").write_bytes(b"a-config")
    (demo / "b.cfg").write_bytes(b"b-config")
    new = tmp_path / "new"
    new.mkdir()
    return SimpleNamespace(tmp=tmp_path, old=old, demo=demo, new=new, out=tmp_path / "out" / "pkg.zip",
                           expand=lambda s: s.replace("%ROAM%", str(old)),
                           expand_new=lambda s: s.replace("%ROAM%", str(new)))


def export(env, rules=None, out=None, **kw):
    return export_package(rules or [make_rule()], out or env.out, [], None, expand=env.expand, now=NOW, **kw)


# =============================================================================== R1-1 거부 형식은 항목별 '복원 안 함'

def test_denied_files_are_skipped_on_export_not_blocking(env):
    for name in ("run.exe", "helper.js", "x.lnk", "plugin.dll", "tool.py"):
        (env.demo / name).write_bytes(b"x")
    m = export(env)
    assert sorted(f["rel"] for f in m["files"]) == ["Demo/a.cfg", "Demo/b.cfg"]
    denied = [s for s in m["skipped"] if s.get("denied")]
    assert len(denied) == 5 and all("복원하지 않는 형식" in s["reason"] for s in denied)
    plan = plan_import(env.out, [make_rule()], env.expand_new)
    assert plan.problems == [] and [i["status"] for i in plan.items] == ["would_copy", "would_copy"]


def forge(path, entries, rules=None):
    """entries: (rel, bytes). manifest 를 직접 만들어 거부 형식이 든 '변조/옛 버전' 패키지를 흉내 낸다."""
    import hashlib
    files = []
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in entries:
            arc = f"data/demo/cfg/{rel}"
            zf.writestr(arc, data)
            files.append({"arc": arc, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "rule": "demo",
                          "base": "%ROAM%\\Vendor", "rel": f"Demo/{rel}"})
        zf.writestr("manifest.json", json.dumps({"format": 1, "rules": [{"id": "demo"}], "files": files, "manual_warnings": []}))
    return path


def test_package_with_denied_entries_restores_rest_and_never_writes_denied(env, tmp_path):
    pkg = forge(tmp_path / "f.zip", [("a.cfg", b"A"), ("run.exe", b"MZ"), ("sub/helper.js", b"js"), ("tool.dll", b"dll"),
                                     ("b.cfg", b"B")])
    plan = plan_import(pkg, [make_rule()], env.expand_new)
    assert plan.problems == []
    st = {Path(i["target"]).name: i["status"] for i in plan.items}
    assert st == {"a.cfg": "would_copy", "run.exe": "denied", "helper.js": "denied", "tool.dll": "denied", "b.cfg": "would_copy"}
    report = apply_import(pkg, plan)
    base = env.new / "Vendor" / "Demo"
    assert (base / "a.cfg").read_bytes() == b"A" and (base / "b.cfg").read_bytes() == b"B"
    assert not (base / "run.exe").exists() and not (base / "sub").exists() and not (base / "tool.dll").exists()
    assert not report.failures  # 거부는 실패가 아니라 항목별 '복원 안 함'
    d = report.to_dict()
    assert d["summary"]["denied"] == 3 and d["summary"]["copied"] == 2
    assert {r["reason"] for r in d["results"] if r["status"] == "denied"} == {"denied"}


def test_rule_that_names_extension_still_restores_it(env, tmp_path):
    rule = make_rule(paths=[{"path": "%ROAM%\\Vendor\\Demo\\*.dll", "dest": "demo/dll", "kind": "file"}])
    (env.demo / "ok.dll").write_bytes(b"dll")
    (env.demo / "x.exe").write_bytes(b"MZ")
    m = export(env, rules=[rule])
    assert [f["rel"] for f in m["files"]] == ["ok.dll"]
    plan = plan_import(env.out, [rule], env.expand_new)
    assert plan.problems == [] and [i["status"] for i in plan.items] == ["would_copy"]


def test_cli_export_warns_denied_and_import_summary_lists_them(env, tmp_path, monkeypatch, capsys):
    (env.demo / "run.exe").write_bytes(b"MZ")
    rd = make_rules_dir(tmp_path / "rules")
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [Program("Demo App", "1")])
    monkeypatch.setattr(cli, "winget_export_raw", lambda: None)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: None)
    (rd / "demo.json").write_text(json.dumps({"id": "demo", "name": "Demo", "detect": {"display_name_patterns": ["Demo"]},
                                              "paths": [{"path": "%ROAM%\\Vendor\\Demo", "dest": "demo/cfg"}]}), encoding="utf-8")
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: export_package(*a, **{**k, "expand": env.expand}))
    assert cli.main(["export", "--output", str(env.out), "--rules", str(rd)]) == 0
    cap = capsys.readouterr()
    assert "보안상 복원하지 않는 형식" in cap.err and "run.exe" in cap.err and "보안상" in cap.out


# =============================================================================== R1-9 거부 목록 보강

@pytest.mark.parametrize("rel", ["a.dll", "b.ocx", "c.sys", "d.jar", "e.py", "f.pyw", "g.cjs", "h.mjs", "i.xll", "j.sct", "k.wsc",
                                 "l.msc", "m.gadget", "n.appref-ms", "o.application", "p.mof", "q.inf", "r.chm",
                                 "s.settingcontent-ms", "t.dotm", "u.xlam", "v.wll", "XLSTART/book.xlsx", "x/XLStart/a.txt"])
def test_new_deny_list_entries(rel):
    assert restore_denied(rel, Path("C:/x/" + rel), None, "%APPDATA%")


@pytest.mark.parametrize("rel", [".npmrc", ".wslconfig", ".condarc", ".kube/config", ".docker/config.json", ".yarnrc", ".netrc"])
def test_home_dotfiles_that_run_commands_are_denied(rel):
    assert restore_denied(rel, Path("C:/u/" + rel), None, "%USERPROFILE%")
    assert restore_denied(rel, Path("C:/u/" + rel), None, "%APPDATA%") is None  # 홈 바로 아래에서만


def test_home_gitconfig_and_ordinary_files_still_allowed():
    assert restore_denied(".gitconfig", Path("C:/u/.gitconfig"), None, "%USERPROFILE%") is None
    assert restore_denied(".docker/contexts/x.json", Path("C:/u/.docker/contexts/x.json"), None, "%USERPROFILE%") is None


@pytest.mark.parametrize("name", ["COM0", "LPT0", "COM\u00b9", "COM\u00b2.txt", "LPT\u00b3", "CONIN$", "CONOUT$", "conin$.log", "NUL.txt"])
def test_reserved_device_names_incl_superscripts_rejected(name):
    assert unsafe_name(f"dir/{name}")


def test_all_shipped_rules_roundtrip_without_problems_or_denied(tmp_path):
    """규칙 22개 경로를 모두 가짜 환경에 만들어 내보내고 복원 계획을 세워도 문제/거부가 없어야 한다(정상 라운드트립)."""
    rules = load_rules(REPO_RULES)
    old, new = tmp_path / "old", tmp_path / "new"

    def expander(root):
        import re
        return lambda s: re.sub(r"%([A-Za-z_]+)%", lambda m: str(root / m.group(1)), s)

    n_paths = 0
    for r in rules:
        for pr in r.paths:
            n_paths += 1
            concrete = expander(old)(pr.path).replace("*", "x").replace("?", "x")
            p = Path(concrete)
            if pr.kind == "file":
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"data")
            else:
                (p / "sub").mkdir(parents=True, exist_ok=True)
                (p / "sub" / "f.txt").write_bytes(b"data")
    assert n_paths >= 22
    out = tmp_path / "all.zip"
    m = export_package(rules, out, [], None, expand=expander(old), now=NOW)
    assert not [s for s in m["skipped"] if s.get("denied")], "정상 설정 파일이 거부 형식으로 걸리면 안 된다"
    assert len(m["files"]) >= 22 and m["failed"] == []
    plan = plan_import(out, rules, expander(new))
    assert plan.problems == [] and all(i["status"] == "would_copy" for i in plan.items)
    report = apply_import(out, plan)
    assert not report.failures and len(report.results) == len(m["files"])


# =============================================================================== R1-8 / R1-6 / R1-7 / R1-5 / R1-10 / R2-1 export 견고성

def test_output_inside_backup_folder_is_blocked(env):
    with pytest.raises(PackageError, match="백업할 폴더 안"):
        export(env, out=env.demo / "pkg.zip")
    assert not (env.demo / "pkg.zip").exists() and not list(env.demo.glob("*.part*")) and not list(env.demo.glob(".pcmigrate-*"))
    assert [p.name for p in env.demo.iterdir()] == ["a.cfg", "b.cfg"]


def test_collect_avoid_skips_own_files(env):
    (env.demo / "pkg.zip.part").write_bytes(b"x")
    items, _ = collect_items(make_rule(), env.expand, avoid=lambda p: p.name.endswith(".part"))
    assert sorted(i.rel for i in items) == ["Demo/a.cfg", "Demo/b.cfg"]


def _alive_foreign_pid():
    return os.getppid()


def test_concurrent_export_with_same_output_is_refused(env):
    lock = env.out.with_name(env.out.name + ".part.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": _alive_foreign_pid()}), encoding="utf-8")
    with pytest.raises(PackageError, match="이미 실행 중"):
        export(env)
    assert lock.exists() and not env.out.exists()  # 남의 락은 건드리지 않는다


def test_stale_lock_is_taken_over_and_released(env):
    lock = env.out.with_name(env.out.name + ".part.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": 2 ** 30}), encoding="utf-8")
    export(env)
    assert env.out.exists() and not lock.exists()


def test_lock_released_when_export_fails(env, monkeypatch):
    patch_pkg(monkeypatch, "_write_entry", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        export(env)
    assert not env.out.with_name(env.out.name + ".part.lock").exists()


def test_stale_stage_folders_are_cleaned_but_live_ones_kept(env):
    env.out.parent.mkdir(parents=True)
    dead = env.out.parent / f"{package.STAGE_PREFIX}{2 ** 30}-abc"
    dead.mkdir()
    (dead / "big.tmp").write_bytes(b"x" * 100)
    live = env.out.parent / f"{package.STAGE_PREFIX}{_alive_foreign_pid()}-xyz"
    live.mkdir()
    old_style = env.out.parent / f"{package.STAGE_PREFIX}oldstyle"
    old_style.mkdir()
    os.utime(old_style, (time.time() - 3 * 86400,) * 2)
    export(env)
    assert not dead.exists() and not old_style.exists() and live.exists()
    assert not [p for p in env.out.parent.iterdir() if p.name.startswith(package.STAGE_PREFIX) and p != live]


def test_stage_folder_name_carries_pid(env, monkeypatch):
    seen = []
    real = package._write_entry
    patch_pkg(monkeypatch, "_write_entry", lambda zf, arc, src, stage=None: (seen.append(stage), real(zf, arc, src, stage))[1])
    export(env)
    assert seen and os.path.basename(seen[0]).startswith(f"{package.STAGE_PREFIX}{os.getpid()}-")


def test_discard_partial_refuses_files_that_are_not_ours(env):
    part, jr = package.part_paths(env.out)
    part.parent.mkdir(parents=True)
    part.write_bytes(b"this is somebody's notes, not a zip")
    with pytest.raises(PackageError, match="다른 출력 이름"):
        export(env)
    assert part.read_bytes() == b"this is somebody's notes, not a zip"  # 지우지 않았다
    with pytest.raises(PackageError, match="다른 출력 이름"):
        export(env, resume=False)
    assert part.exists() and not env.out.exists()


def test_discard_partial_refuses_foreign_journal_and_deletes_nothing(env):
    part, jr = package.part_paths(env.out)
    part.parent.mkdir(parents=True)
    part.write_bytes(bytes([80, 75, 3, 4]) + b"ours")
    jr.write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(PackageError):
        package.discard_partial(env.out)
    assert part.exists() and jr.exists()


def test_discard_partial_deletes_ours(env):
    part, jr = package.part_paths(env.out)
    part.parent.mkdir(parents=True)
    part.write_bytes(bytes([80, 75, 3, 4]) + b"ours")
    jr.write_text(json.dumps({"t": "head", "v": 1, "fp": "x", "info": {}}) + "\n", encoding="utf-8")
    package.discard_partial(env.out)
    assert not part.exists() and not jr.exists()


def test_failed_final_check_keeps_part_as_failed_with_retry_hint(env, monkeypatch):
    real = package.verify_package
    calls = []

    def fake(path):
        calls.append(str(path))
        return (None, ["엔트리 손상"]) if str(path).endswith(".part") else real(path)
    patch_pkg(monkeypatch, "verify_package", fake)
    with pytest.raises(PackageError) as ei:
        export(env)
    failed = env.out.with_name(env.out.name + ".part.failed")
    assert failed.is_file() and not env.out.exists()
    assert ".part.failed" in str(ei.value) and "다시 내보내면 처음부터" in str(ei.value)
    assert not env.out.with_name(env.out.name + ".part").exists()
    patch_pkg(monkeypatch, "verify_package", real)
    export(env)
    assert env.out.exists() and not failed.exists()  # 성공하면 보존해 둔 임시 파일을 치운다


def test_export_blocks_when_free_space_is_too_small(env):
    d = lambda p: DestInfo(p, "fixed", "NTFS", 1, 1, "D:")  # noqa: E731
    with pytest.raises(PackageError, match="여유 공간이 부족"):
        export(env, dest_info_fn=d, compress="never")
    assert not env.out.exists()


def test_export_blocks_fat32_over_4gb(env, monkeypatch):
    monkeypatch.setattr(fs, "FAT4G", 5)  # 파일 8바이트 > 5
    d = lambda p: DestInfo(p, "removable", "FAT32", 10 ** 12, 1, "E:")  # noqa: E731
    with pytest.raises(PackageError, match="FAT32"):
        export(env, dest_info_fn=d)


def test_export_space_check_passes_with_enough_space(env):
    d = lambda p: DestInfo(p, "fixed", "NTFS", 10 ** 12, 1, "D:")  # noqa: E731
    assert export(env, dest_info_fn=d)["files"]


def test_unreadable_folder_is_recorded_as_failed(env, monkeypatch):
    real_walk = os.walk

    def walk(top, topdown=True, onerror=None, followlinks=False):
        if onerror and str(top).rstrip("\\/").endswith("Demo"):
            err = PermissionError(13, "Access is denied")
            err.filename = str(top) + "\\sub"
            onerror(err)
        return real_walk(top, topdown, onerror, followlinks)
    monkeypatch.setattr(os, "walk", walk)
    m = export(env)
    assert any(f.get("reason") == "permission" and "폴더를 읽지 못함" in f["error"] for f in m["failed"])


@nt_only
def test_export_includes_files_over_260_chars(env):
    deep = env.demo
    for i in range(6):
        deep = deep / ("d" * 50 + str(i))
    os.makedirs(fs.long_path(deep))
    f = deep / "deep.cfg"
    assert len(str(f)) > 260
    with open(fs.long_path(f), "wb") as h:
        h.write(b"deep-data")
    m = export(env)
    assert any(x["rel"].endswith("deep.cfg") for x in m["files"]) and m["failed"] == []
    with zipfile.ZipFile(env.out) as zf:
        assert any(n.endswith("deep.cfg") and zf.read(n) == b"deep-data" for n in zf.namelist())


# =============================================================================== R1-2 / R1-3 / R1-4 / R1-12 / R1-13 / R2-2 transfer

def _dst_with_different_files(tmp_path, src):
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    for i in range(3):
        (dst / "sub" / f"f{i}.dwg").write_bytes(f"기존 {i}".encode())
    return dst


def test_preserve_failure_rolls_back_already_moved_files(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    dst = _dst_with_different_files(tmp_path, src)
    real = os.replace

    def flaky(a, b):
        if str(a).endswith("f2.dwg"):
            raise PermissionError(5, "Access is denied")
        return real(a, b)
    monkeypatch.setattr(tr.os, "replace", flaky)
    with pytest.raises(tr.TransferError) as ei:
        run_tr(src, dst)
    msg = str(ei.value)
    assert "되돌렸습니다" in msg and "덮어쓰지 않음" in msg
    for i in range(3):
        assert (dst / "sub" / f"f{i}.dwg").read_bytes() == f"기존 {i}".encode()
    assert not [p for p in dst.rglob("*") if ".bak" in p.name]
    assert not (dst / tr.PRESERVE_NAME).exists()


def test_preserve_rollback_that_fails_is_reported_truthfully(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    dst = _dst_with_different_files(tmp_path, src)
    real = os.replace

    def flaky(a, b):
        a, b = str(a), str(b)
        if a.endswith("f2.dwg"):
            raise PermissionError(5, "Access is denied")
        if a.endswith("f0.dwg.bak"):  # 되돌리기 시도 하나가 실패
            raise PermissionError(5, "Access is denied")
        return real(a, b)
    monkeypatch.setattr(tr.os, "replace", flaky)
    with pytest.raises(tr.TransferError) as ei:
        run_tr(src, dst)
    assert "되돌리지 못한 1개" in str(ei.value) and "f0.dwg.bak" in str(ei.value)
    assert (dst / "sub" / "f1.dwg").read_bytes() == "기존 1".encode()


def test_cancel_during_preserve_rolls_back(tmp_path, monkeypatch):
    from pcmigrate.cancel import Cancelled
    src = make_src(tmp_path)
    dst = _dst_with_different_files(tmp_path, src)
    real, n = tr._bak_name, {"c": 0}

    def third_time_cancel(target):
        n["c"] += 1
        if n["c"] == 3:
            raise Cancelled()
        return real(target)
    patch_xfer(monkeypatch, "_bak_name", third_time_cancel)
    with pytest.raises(Cancelled):
        run_tr(src, dst)
    assert [(dst / "sub" / f"f{i}.dwg").read_bytes() for i in range(3)] == [f"기존 {i}".encode() for i in range(3)]
    assert not [p for p in dst.rglob("*") if ".bak" in p.name]


def test_needed_bytes_uses_same_criterion_as_differs(tmp_path):
    dst = tmp_path / "dst"
    dst.mkdir()
    f = dst / "a.bin"
    f.write_bytes(b"x" * 100)
    old_mtime = time.time() - 86400
    os.utime(f, (old_mtime, old_mtime))
    entries = [("a.bin", 100, time.time_ns()), ("new.bin", 50, time.time_ns())]  # 크기는 같지만 수정시각이 다른 파일 + 없는 파일
    assert tr._differs(str(f), 100, entries[0][2])
    assert tr._needed_bytes(entries, dst, 150) == 150  # 예전(크기만)에는 50 으로 세어 공간을 모자라게 봤다
    same = [("a.bin", 100, f.stat().st_mtime_ns)]
    assert tr._needed_bytes(same, dst, 100) == 0


def test_space_check_counts_bak_preserved_files_and_reports_bytes(tmp_path):
    src = make_src(tmp_path)
    dst = _dst_with_different_files(tmp_path, src)
    total = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
    with pytest.raises(tr.TransferError) as ei:
        run_tr(src, dst, dest_info_fn=info(free=total // 2))
    assert ".bak" in str(ei.value) and "공간을 계속 차지" in str(ei.value)
    rep = run_tr(src, dst, dry_run=True, dest_info_fn=info(free=total // 2))
    assert rep.meta["would_preserve_count"] == 3 and rep.meta["would_preserve_bytes"] > 0 and rep.meta["space_blocked"] is True


def test_dry_run_warning_no_longer_claims_no_extra_space(tmp_path):
    src = make_src(tmp_path)
    dst = _dst_with_different_files(tmp_path, src)
    warns = []
    run_tr(src, dst, dry_run=True, warn=warns.append)
    text = " ".join(warns)
    assert "공간을 더 쓰지 않습니다" not in text and "계속 차지" in text and "여유 공간이 더 필요" in text


@nt_only
def test_journal_source_path_compared_normalized(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    journal = dst / tr.PRESERVE_NAME
    tr._write_journal(journal, Path(str(src).upper()), "copying", 0, [])  # 대소문자만 다른 같은 원본
    (dst / "sub" / "f0.dwg").write_bytes((src / "sub" / "f0.dwg").read_bytes()[:10])
    entries = [("sub/f0.dwg", (src / "sub" / "f0.dwg").stat().st_size, (src / "sub" / "f0.dwg").stat().st_mtime_ns)]
    n, listed, resumed = tr._preserve_existing(entries, src, dst, journal, None, lambda m: None)
    assert resumed is True and n == 0 and not list(dst.rglob("*.bak"))


def test_journal_from_other_source_is_not_resumed_and_user_is_told(tmp_path):
    src = make_src(tmp_path)
    other = tmp_path / "other"
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    journal = dst / tr.PRESERVE_NAME
    tr._write_journal(journal, other, "copying", 0, [])
    (dst / "sub" / "f0.dwg").write_bytes(b"x")
    st = (src / "sub" / "f0.dwg").stat()
    warns = []
    n, listed, resumed = tr._preserve_existing([("sub/f0.dwg", st.st_size, st.st_mtime_ns)], src, dst, journal, None,
                                               lambda m: None, warn=warns.append)
    assert resumed is False and n == 1 and (dst / "sub" / "f0.dwg.bak").exists()
    assert warns and "드라이브 문자" in warns[0]


def test_resume_keeps_true_partial_but_preserves_modified_file(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    (dst / "sub").mkdir(parents=True)
    journal = dst / tr.PRESERVE_NAME
    tr._write_journal(journal, src, "copying", 0, [])
    data0 = (src / "sub" / "f0.dwg").read_bytes()
    (dst / "sub" / "f0.dwg").write_bytes(data0[:100])           # robocopy 가 쓰다 끊긴 앞부분
    (dst / "sub" / "f1.dwg").write_bytes(b"user edited after crash")  # 그 사이 사용자가 바꾼 파일
    entries = []
    for i in range(2):
        p = src / "sub" / f"f{i}.dwg"
        entries.append((f"sub/f{i}.dwg", p.stat().st_size, p.stat().st_mtime_ns))
    n, listed, resumed = tr._preserve_existing(entries, src, dst, journal, None, lambda m: None)
    assert resumed is True and n == 1
    assert (dst / "sub" / "f0.dwg").read_bytes() == data0[:100]
    assert (dst / "sub" / "f1.dwg.bak").read_bytes() == b"user edited after crash" and not (dst / "sub" / "f1.dwg").exists()
    assert json.loads(journal.read_text(encoding="utf-8"))["preserved_count"] == 1


def test_state_file_from_other_source_spelling_resets(tmp_path):
    state_path = tmp_path / "s.json"
    st = tr.State(state_path, tmp_path / "srcA", "D:\\dst", 1)
    st.record("a", 1, 1, "h", True)
    st.save()
    again = tr.State(state_path, tmp_path / "srcB", "D:\\dst", 1)
    assert "원본 경로 불일치" in again.reset_reason and "이전 원본" in again.reset_reason


def test_second_run_reports_how_many_files_were_trusted_from_state(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    run_tr(src, dst)
    rep = run_tr(src, dst)
    assert rep.meta["trusted_state_files"] == 4 and rep.meta["strict"] is False
    rep2 = run_tr(src, dst, strict=True)
    assert rep2.meta["trusted_state_files"] == 0


# =============================================================================== R2-3 경로 인자 / R2-9 경로 모양

@pytest.mark.parametrize("raw,clean", [('"D:\\a\\b.zip"', "D:\\a\\b.zip"), ("'D:\\a'", "D:\\a"), ("  D:\\a  ", "D:\\a"),
                                       ('"D:\\a\\"', "D:\\a\\"), (' "D:\\x y" ', "D:\\x y"), ("", "")])
def test_path_args_are_cleaned(raw, clean):
    assert api.clean_path_arg(raw) == clean
    p = cli.build_parser().parse_args(["export", "--output", raw])
    assert p.output == clean


def test_empty_path_after_cleaning_is_still_rejected(tmp_path, capsys):
    assert cli.main(["transfer", '""', str(tmp_path / "d")]) == 1
    assert "경로가 비어 있습니다" in capsys.readouterr().err


def test_quoted_path_works_end_to_end_with_notice(tmp_path, capsys):
    src = tmp_path / "s"
    src.mkdir()
    assert cli.main(["manifest", f'"{src}"', "--out", str(tmp_path / "m.jsonl")]) == 0
    assert "따옴표" in capsys.readouterr().err
    assert (tmp_path / "m.jsonl").exists()


def test_root_relative_path_is_rejected_and_unc_hint_given(tmp_path, capsys):
    assert cli.main(["transfer", str(tmp_path), "\\backup\\x"]) == 1
    err = capsys.readouterr().err
    assert "드라이브 문자 없이" in err and "역슬래시 두 개" in err


def test_relative_path_is_shown_resolved(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "s").mkdir()
    cli.main(["manifest", "s", "--out", "m.jsonl"])
    err = capsys.readouterr().err
    assert "상대 경로를 현재 폴더 기준으로 풀어서" in err and str(tmp_path) in err


def test_check_path_forms_unit():
    errors, notes = api.check_path_forms(dst="\\x\\y", out="rel\\file", good="C:\\ok\\x", unc="\\\\srv\\share\\d", empty="")
    assert len(errors) == 1 and "--dst" in errors[0]
    assert len(notes) == 1 and "--out" in notes[0]


# =============================================================================== R2-4 사람이 읽는 요약 / R2-5 확장자 / R2-6 winget-extract

def _cli_patch_detection(monkeypatch, winget=None):
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [Program("Demo App", "1")])
    monkeypatch.setattr(cli, "winget_export_raw", lambda: winget)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: None)


def _demo_rules_dir(tmp_path):
    rd = make_rules_dir(tmp_path / "rules")
    (rd / "demo.json").write_text(json.dumps({"id": "demo", "name": "Demo", "detect": {"display_name_patterns": ["Demo"]},
                                              "paths": [{"path": "%ROAM%\\Vendor\\Demo", "dest": "demo/cfg"}]}), encoding="utf-8")
    return rd


def test_export_default_output_is_human_readable_and_json_flag_gives_json(env, tmp_path, monkeypatch, capsys):
    _cli_patch_detection(monkeypatch)
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: export_package(*a, **{**k, "expand": env.expand}))
    rd = _demo_rules_dir(tmp_path)
    assert cli.main(["export", "--output", str(env.out), "--rules", str(rd)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("설정 패키지를 만들었습니다") and "담은 파일 2개" in out and "다음 할 일" in out
    with pytest.raises(ValueError):
        json.loads(out)
    assert cli.main(["export", "--output", str(env.out), "--rules", str(rd), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == 2


def test_import_default_output_is_human_readable(env, tmp_path, monkeypatch, capsys):
    export(env)
    rd = _demo_rules_dir(tmp_path)
    monkeypatch.setattr(cli, "plan_import", lambda p, r: plan_import(p, r, env.expand_new))
    assert cli.main(["import", str(env.out), "--dry-run", "--rules", str(rd)]) == 0
    out = capsys.readouterr().out
    assert "미리보기" in out and "새로 만듦 2개" in out and "--yes" in out
    assert not (env.new / "Vendor").exists()
    assert cli.main(["import", str(env.out), "--yes", "--rules", str(rd)]) == 0
    out = capsys.readouterr().out
    assert "복원을 마쳤습니다" in out and "복원함 2개" in out and "checklist" in out


def test_import_blocked_output_lists_problems_in_korean(env, tmp_path, monkeypatch, capsys):
    export(env)
    rd = make_rules_dir(tmp_path / "rules")  # 규칙 id 가 달라 '알 수 없는 규칙'
    assert cli.main(["import", str(env.out), "--dry-run", "--rules", str(rd)]) == 1
    cap = capsys.readouterr()
    assert "패키지 검증 실패" in cap.out and "알 수 없는 규칙" in cap.out


def test_verify_and_transfer_default_output_human_readable(tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    patch_xfer(monkeypatch, "default_runner", FakeRobocopy())
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: run_tr(src, dst, **{x: v for x, v in k.items()
                                                                                 if x in ("dry_run", "strict", "verify_only")}))
    assert cli.main(["transfer", str(src), str(dst), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "미리보기입니다" in out and "새로 복사" in out and "--dry-run 만 빼고" in out
    assert cli.main(["transfer", str(src), str(dst)]) == 0
    out = capsys.readouterr().out
    assert "전송을 마쳤습니다" in out and "정확함 4개" in out and ".pcmigrate-" in out
    assert cli.main(["transfer", str(src), str(dst), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["success"] is True


def test_transfer_dry_run_preview_is_not_reported_as_failure(tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    rep = run_tr(src, dst, dry_run=True)
    d = rep.to_dict()
    assert d["success"] is True and d["preview"] is True and d["verification_complete"] is False
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: rep)
    assert cli.main(["transfer", str(src), str(dst), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["success"] is True


def test_export_output_without_extension_gets_zip(env, tmp_path, monkeypatch, capsys):
    _cli_patch_detection(monkeypatch)
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: export_package(*a, **{**k, "expand": env.expand}))
    rd = _demo_rules_dir(tmp_path)
    target = tmp_path / "pc-package"
    assert cli.main(["export", "--output", str(target), "--rules", str(rd)]) == 0
    cap = capsys.readouterr()
    assert (tmp_path / "pc-package.zip").is_file() and not target.exists() and ".zip 을 붙입니다" in cap.err


def test_export_other_extension_warns_but_keeps_name(env, tmp_path, monkeypatch, capsys):
    _cli_patch_detection(monkeypatch)
    monkeypatch.setattr(cli, "export_package", lambda *a, **k: export_package(*a, **{**k, "expand": env.expand}))
    rd = _demo_rules_dir(tmp_path)
    target = tmp_path / "pc.7z"
    assert cli.main(["export", "--output", str(target), "--rules", str(rd)]) == 0
    assert target.is_file() and "확장자가 .7z" in capsys.readouterr().err


def test_export_existing_folder_without_extension_still_rejected(tmp_path, capsys):
    folder = tmp_path / "pkgdir"
    folder.mkdir()
    rd = make_rules_dir(tmp_path / "rules")
    assert cli.main(["export", "--output", str(folder), "--rules", str(rd)]) == 1
    assert "폴더" in capsys.readouterr().err and folder.is_dir() and not (tmp_path / "pkgdir.zip").exists()


def _pkg_with_winget(env, text='{"Sources": [{"Packages": []}]}'):
    export_package([make_rule()], env.out, [], text, expand=env.expand, now=NOW, winget_list_text=None)
    return env.out


def test_winget_extract_writes_file_and_keeps_old_as_bak(env, tmp_path, capsys):
    pkg = _pkg_with_winget(env)
    out_dir = tmp_path / "w"
    assert cli.main(["winget-extract", str(pkg), "--output", str(out_dir)]) == 0
    text = capsys.readouterr().out
    assert (out_dir / "winget.json").read_text(encoding="utf-8") == '{"Sources": [{"Packages": []}]}'
    assert "winget import -i" in text and "winget.json" in text
    (out_dir / "winget.json").write_text("내 파일", encoding="utf-8")
    assert cli.main(["winget-extract", str(pkg), "--output", str(out_dir)]) == 0
    assert "winget.json.bak" in capsys.readouterr().out
    assert (out_dir / "winget.json.bak").read_text(encoding="utf-8") == "내 파일"


def test_winget_extract_errors(env, tmp_path, capsys):
    export(env)  # winget 없음
    assert cli.main(["winget-extract", str(env.out), "--output", str(tmp_path / "w")]) == 1
    assert "winget 목록이 없습니다" in capsys.readouterr().err and not (tmp_path / "w" / "winget.json").exists()
    pkg = _pkg_with_winget(env)
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    assert cli.main(["winget-extract", str(pkg), "--output", str(blocker)]) == 1
    (tmp_path / "w2" / "winget.json").mkdir(parents=True)
    with pytest.raises(PackageError, match="같은 이름의 폴더"):
        extract_winget(pkg, tmp_path / "w2")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    assert cli.main(["winget-extract", str(bad), "--output", str(tmp_path / "w3")]) == 1
    assert not (tmp_path / "w3").exists()


def test_checklist_points_to_winget_extract():
    from pcmigrate.checklist import WINGET_IMPORT_GUIDE
    text = "\n".join(WINGET_IMPORT_GUIDE)
    assert "pcmigrate winget-extract" in text and "winget import -i" in text and "자동 실행하지 않습니다" in text


# =============================================================================== R2-10 기록 파일 정리

def test_clean_state_removes_only_state_files_after_verified_success(tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: run_tr(src, dst))
    assert cli.main(["transfer", str(src), str(dst), "--clean-state"]) == 0
    err = capsys.readouterr().err
    assert "기록 파일" in err and not list(dst.glob(".pcmigrate-*"))
    assert (dst / "top.txt").read_bytes() == b"top" and (dst / "sub" / "f0.dwg").exists()


def test_clean_state_is_skipped_when_there_are_failures(tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: run_tr(src, dst, runner=FakeRobocopy(skip=["f0.dwg"])))
    assert cli.main(["transfer", str(src), str(dst), "--clean-state"]) == 1
    assert "지우지 않았습니다" in capsys.readouterr().err and (dst / tr.STATE_NAME).exists()


# =============================================================================== R2-8 오류 문구

def test_plain_path_text_and_cli_text():
    assert fs.plain_path_text("파일 \\\\?\\C:\\a\\b 와 \\\\?\\UNC\\srv\\sh\\x") == "파일 C:\\a\\b 와 \\\\srv\\sh\\x"
    from pcmigrate import failure
    t = failure.cli_text(failure.REASONS[failure.MISMATCH].text)
    assert "--retry-failed" in t and "실패한 것만 다시 시도'" not in t.replace("--retry-failed(실패한 것만 다시 시도)", "")


def test_report_dict_strips_long_prefix_in_errors():
    from pcmigrate.copier import FileResult
    r = Report(dry_run=False, results=[FileResult("\\\\?\\C:\\a", "\\\\?\\C:\\b", "failed", error="PermissionError: \\\\?\\C:\\b")])
    d = r.to_dict()["results"][0]
    assert "\\\\?\\" not in json.dumps(d, ensure_ascii=True).replace("\\\\\\\\", "\\")
    assert d["src"] == "C:\\a" and d["error"] == "PermissionError: C:\\b"


def test_cli_failure_lines_use_retry_failed_wording(tmp_path, monkeypatch, capsys):
    from pcmigrate import failure
    from pcmigrate.copier import FileResult
    rep = Report(dry_run=False, results=[FileResult("s", "d", "failed", error=failure.REASONS[failure.IN_USE].text, reason="in_use")],
                 meta=dict(mode="copy", dest_verification="now", verification_complete=True, cloud_only_count=0))
    monkeypatch.setattr(cli, "run_transfer", lambda *a, **k: rep)
    assert cli.main(["transfer", str(tmp_path), str(tmp_path / "x")]) == 1
    err = capsys.readouterr().err
    assert "--retry-failed" in err and "'실패한 것만 다시 시도'" not in err


# =============================================================================== R1-14 errorlog

def _raise(msg="e", n=0):
    try:
        raise ValueError(msg)
    except ValueError as e:
        return e


def test_errorlog_blocks_are_length_limited(tmp_path):
    big = "가" * 50_000
    e = _raise(big)
    block = errorlog.format_block(e, "x", tb="T" * 200_000)
    assert len(block) < errorlog.MAX_MESSAGE + errorlog.MAX_TRACEBACK + 2_000
    assert "생략" in block
    path = errorlog.record(e, "x", tb="T" * 200_000, directory=tmp_path)
    assert path.stat().st_size < 200_000


def test_errorlog_replaces_user_home_with_token(tmp_path):
    home = os.path.expanduser("~")
    e = _raise(f"cannot open {home}\\Documents\\a.txt and {home.replace(chr(92), '/')}/b.txt")
    path = errorlog.record(e, f"cmd {home}", directory=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert home not in text and home.replace("\\", "/") not in text
    assert "%USERPROFILE%\\Documents\\a.txt" in text and "%USERPROFILE%/b.txt" in text


def test_errorlog_dedupes_repeats_and_counts_them(tmp_path):
    base = datetime(2026, 1, 1, 12, 0, 0)
    for i in range(5):
        e = _raise("같은 오류")
        errorlog.record(e, "x", directory=tmp_path, now=base.replace(second=i))
    text = next(tmp_path.glob("errors-*.log")).read_text(encoding="utf-8")
    assert text.count("예외: ValueError: 같은 오류") == 1
    e = _raise("같은 오류")
    errorlog.record(e, "x", directory=tmp_path, now=base.replace(minute=5))  # 시간이 지난 뒤에는 다시 쓰고 생략 개수를 알린다
    text = next(tmp_path.glob("errors-*.log")).read_text(encoding="utf-8")
    assert text.count("예외: ValueError: 같은 오류") == 2 and "같은 오류 4건은 기록을 생략" in text


def test_errorlog_rate_limits_bursts(tmp_path):
    base = datetime(2026, 1, 1, 12, 0, 0)
    for i in range(errorlog.BURST_MAX + 15):
        try:
            raise ValueError(f"서로 다른 오류 {i}")
        except ValueError as e:
            errorlog.record(e, "x", directory=tmp_path, now=base.replace(microsecond=i))
    text = next(tmp_path.glob("errors-*.log")).read_text(encoding="utf-8")
    assert text.count("예외:") == errorlog.BURST_MAX


def test_errorlog_writes_block_with_single_append_write(tmp_path, monkeypatch):
    writes = []
    real = os.write
    monkeypatch.setattr(errorlog.os, "write", lambda fd, data: (writes.append(len(data)), real(fd, data))[1])
    errorlog.record(_raise("한 번에"), "x", directory=tmp_path)
    assert len(writes) == 1


def test_errorlog_writable_probe_is_cached(tmp_path):
    errorlog.reset_throttle()
    d = tmp_path / "logs"
    assert errorlog._writable(d) and (d / ".write-test").exists() is False
    d.rmdir()
    assert errorlog._writable(d) is True and not d.exists()  # 두 번째는 폴더에 다시 쓰지 않는다(캐시)


# =============================================================================== R1-15 규칙 로더 / 감지 제외

@pytest.mark.parametrize("path", ["%APPDATA%\\..\\Other", "C:\\Users\\x\\f", "\\\\server\\share\\f", "\\x\\y", "/etc/passwd",
                                  "%APPDATA%/../x", "D:rel"])
def test_rule_loader_rejects_unsafe_paths(path):
    with pytest.raises(RuleError):
        make_rule(paths=[{"path": path, "dest": "d/x"}])


def test_rule_loader_accepts_env_based_paths():
    assert make_rule(paths=[{"path": "%APPDATA%\\Vendor\\*\\x", "dest": "d/x"}]).paths


def test_check_rules_command_reports_unsafe_rule(tmp_path, capsys):
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "bad.json").write_text(json.dumps({"id": "bad", "name": "Bad", "detect": {"display_name_patterns": ["Bad"]},
                                             "paths": [{"path": "\\\\srv\\share\\x", "dest": "d"}]}), encoding="utf-8")
    assert cli.main(["check-rules", "--rules", str(rd)]) == 1
    assert "UNC" in capsys.readouterr().err


@pytest.mark.parametrize("rule_id,name,expect", [
    ("autocad", "Autodesk AutoCAD 2024 - English", True), ("autocad", "Autodesk DWG TrueView 2024", False),
    ("autocad", "AutoCAD 2024 Object Enabler", False), ("autocad", "Autodesk Viewer for AutoCAD", False),
    ("autocad", "AutoCAD Mocks Library", False),
    ("vscode", "Microsoft Visual Studio Code", True), ("vscode", "Microsoft Visual Studio Code Insiders", False),
    ("browser", "Google Chrome", True), ("browser", "Chrome Remote Desktop Host", False),
    ("revit", "Autodesk Revit 2024", True), ("revit", "Autodesk Revit Viewer 2024", False),
    ("sketchup", "SketchUp Pro 2024", True), ("sketchup", "SketchUp Pro 2024 Viewer", False),
    ("rhino", "Rhinoceros 8", True)])
def test_detection_exclusions(rule_id, name, expect):
    rule = {r.id: r for r in load_rules(REPO_RULES)}[rule_id]
    assert rule.matches_name(name) is expect


def test_modified_rules_have_bumped_versions():
    got = {r.id: r.version for r in load_rules(REPO_RULES)}
    for rid, minimum in {"autocad": 5, "revit": 3, "vscode": 4, "browser": 3, "rhino": 4, "sketchup": 2, "qgis": 2}.items():
        assert int(got[rid]) >= minimum, rid


# =============================================================================== R2-7 gui exe 인자

def test_gui_args_version_and_help_do_not_open_window():
    from pcmigrate.gui_args import handle_gui_args
    from pcmigrate import __version__
    shown = []
    assert handle_gui_args(["--version"], notify=shown.append) is True and __version__ in shown[0]
    assert handle_gui_args(["-h"], notify=shown.append) is True and "pcmigrate.exe" in shown[1]
    assert handle_gui_args([], notify=shown.append) is False and handle_gui_args(["--other"], notify=shown.append) is False
    assert len(shown) == 2


def test_gui_args_no_dialog_env_prints_instead(monkeypatch, capsys):
    from pcmigrate.gui_args import handle_gui_args
    monkeypatch.setenv("PCMIGRATE_NO_DIALOG", "1")
    assert handle_gui_args(["--version"]) is True
    assert "PC 이전 도우미" in capsys.readouterr().out


def test_gui_entry_uses_gui_args_and_smoke_test_exists():
    root = Path(__file__).resolve().parents[1]
    assert "handle_gui_args" in (root / "packaging" / "gui_entry.py").read_text(encoding="utf-8")
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_exe_19a", root / "packaging" / "build_exe.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_exe_19a"] = mod
    spec.loader.exec_module(mod)
    seen = {}

    def fake(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    ok, msg = mod.smoke_test_gui(Path("x/pcmigrate-gui.exe"), run=fake)
    assert ok and seen["cmd"][-1] == "--version" and seen["env"]["PCMIGRATE_NO_DIALOG"] == "1"


# =============================================================================== R2-11 / R2-12

def test_version_is_not_repeated_when_name_already_has_one():
    assert display_with_version("Foo 2024", "2024") == "Foo 2024"
    assert display_with_version("Autodesk Revit 2024", "24.0.4.419") == "Autodesk Revit 2024"
    assert display_with_version("Git", "2.40.0.1") == "Git 2.40.0.1"
    assert display_with_version("Foo", "") == "Foo"
    assert Program("Foo", "1.5").display == "Foo 1.5"


def test_checklist_always_has_one_dongle_confirmation_line():
    from pcmigrate.checklist import build_checklist
    cl = build_checklist([], [Program("Google Chrome", "1")], [])
    text = " ".join(cl.login + cl.data)
    assert text.count("동글(하드웨어 락) 확인") == 1 and "Sentinel" not in text
    cl2 = build_checklist([], [Program("Sentinel HASP Run-time", "1")], [])
    assert "동글(하드웨어 락) 확인" in " ".join(cl2.login) and "Sentinel" in " ".join(cl2.login)
