"""stage24: 읽지 못해 제외된 파일만 다시 담기(package.retry_failed_export / api.run_export_retry / CLI --retry-failed).
모두 tmp_path 의 가짜 환경만 쓰고, 잠긴 파일은 msvcrt.locking 으로 흉내 낸다(Windows 전용 시험은 winonly)."""
import hashlib
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import api, cli, package
from pcmigrate.package import PackageError, export_package, part_paths, retry_failed_export, retry_paths, verify_package
from pcmigrate.rules import parse_rule
from test_stage14_export import ByteLock

winonly = pytest.mark.skipif(os.name != "nt", reason="바이트 범위 잠금은 Windows 전용 시나리오")
NOW = lambda: __import__("datetime").datetime(2030, 1, 1, tzinfo=__import__("datetime").timezone.utc)  # noqa: E731


def rule_data(exclude=()):
    return {"id": "demo", "name": "Demo", "version": "7", "detect": {"display_name_patterns": ["Demo"]},
            "paths": [{"path": "%ROAM%\\Vendor\\Demo\\Support", "dest": "demo/support", "kind": "dir"}],
            "exclude": list(exclude), "manual_warnings": []}


@pytest.fixture
def env(tmp_path):
    old = tmp_path / "old"
    sup = old / "Vendor" / "Demo" / "Support"
    sup.mkdir(parents=True)
    for i in range(4):
        (sup / f"f{i}.cfg").write_bytes((f"file-{i}-" * 300).encode())
    (sup / "pic.jpg").write_bytes(os.urandom(3000))
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "demo.json").write_text(json.dumps(rule_data()), encoding="utf-8")
    return SimpleNamespace(tmp=tmp_path, old=old, sup=sup, out=tmp_path / "out" / "p.zip", rules_dir=rules,
                           expand=lambda s: s.replace("%ROAM%", str(old)),
                           rule=parse_rule(rule_data()))


def do_export(env, out=None, **kw):
    return export_package([env.rule], out or env.out, [], None, expand=env.expand, now=NOW, **kw)


def do_retry(env, out=None, rules=None, **kw):
    return retry_failed_export(out or env.out, rules if rules is not None else [env.rule], env.expand, now=NOW, **kw)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def leftovers(env):
    d = env.out.parent
    return [p.name for p in d.iterdir() if p.name.endswith((".retry", ".lock")) or p.name.startswith(".pcmigrate-stage")]


def entry_bytes(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist() if i.filename != "manifest.json"}


def baks(env):
    return sorted(p.name for p in env.out.parent.iterdir() if ".bak" in p.name)


# --------------------------------------------------------------------------- 핵심 동작

@winonly
def test_retry_adds_unlocked_file_and_keeps_everything_else(env):
    target = env.sup / "f1.cfg"
    with ByteLock(target, 2):
        m0 = do_export(env)
    assert [f["reason"] for f in m0["failed"]] == ["in_use"] and len(m0["files"]) == 4
    before_bytes = entry_bytes(env.out)
    before_infos = {i.filename: (i.compress_type, i.CRC, i.compress_size) for i in zipfile.ZipFile(env.out).infolist()}
    orig_hash = sha(env.out)
    m = do_retry(env)
    manifest, problems = verify_package(env.out)
    assert problems == []
    assert len(manifest["files"]) == 5 and manifest["failed"] == []
    rec = next(f for f in manifest["files"] if f["rel"].endswith("f1.cfg"))
    assert rec["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest() and rec["rule"] == "demo"
    assert m["retry"]["added"][0]["arc"] == rec["arc"] and not m["retry"]["unchanged"]
    # 기존 엔트리는 재압축 없이 그대로(내용, 압축 방식, CRC, 압축 크기)
    after_infos = {i.filename: (i.compress_type, i.CRC, i.compress_size) for i in zipfile.ZipFile(env.out).infolist()}
    for name, v in before_infos.items():
        if name != "manifest.json":
            assert after_infos[name] == v
    assert all(entry_bytes(env.out)[n] == b for n, b in before_bytes.items())
    # .bak 은 원래 묶음 그대로
    assert baks(env) == ["p.zip.bak"] and sha(env.out.with_name("p.zip.bak")) == orig_hash
    assert manifest["retries"][-1]["added"] == 1
    assert leftovers(env) == []
    # 복원 쪽도 그대로 읽는다
    plan = package.plan_import(env.out, [env.rule], env.expand)
    assert plan.problems == [] and len(plan.items) == 5


@winonly
def test_retry_still_locked_keeps_failed_and_package_untouched(env):
    with ByteLock(env.sup / "f2.cfg", 2):
        do_export(env)
        h = sha(env.out)
        m = do_retry(env)
        assert sha(env.out) == h and baks(env) == [] and leftovers(env) == []
    assert m["retry"]["unchanged"] and m["retry"]["added"] == []
    assert [f["reason"] for f in m["failed"]] == ["in_use"]


@winonly
def test_partial_progress_some_added_some_still_locked(env):
    a, b = env.sup / "f0.cfg", env.sup / "f3.cfg"
    with ByteLock(a, 2), ByteLock(b, 2):
        do_export(env)
    # a 만 풀고 b 는 다시 잠근 채로 다시 담기
    with ByteLock(b, 2):
        m = do_retry(env)
    manifest, problems = verify_package(env.out)
    assert problems == [] and len(manifest["files"]) == 4
    assert [f["src"] for f in manifest["failed"]] == [str(b)] and manifest["failed"][0]["reason"] == "in_use"
    assert [x["src"] for x in m["retry"]["added"]] == [str(a)] and len(m["retry"]["still_failed"]) == 1
    # 다시 풀고 한 번 더: 모두 담긴다
    m2 = do_retry(env)
    assert verify_package(env.out)[1] == [] and len(m2["files"]) == 5 and m2["failed"] == []
    assert len(m2["retries"]) == 2


@winonly
def test_source_changed_since_export_current_state_is_packed(env):
    target = env.sup / "f1.cfg"
    with ByteLock(target, 2):
        do_export(env)
    target.write_bytes(b"NEW-CONTENT" * 50)
    m = do_retry(env)
    rec = next(f for f in m["files"] if f["rel"].endswith("f1.cfg"))
    assert rec["sha256"] == hashlib.sha256(b"NEW-CONTENT" * 50).hexdigest() and rec["size"] == 550
    assert zipfile.ZipFile(env.out).read(rec["arc"]) == b"NEW-CONTENT" * 50


@winonly
def test_source_is_never_modified(env):
    snap = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in env.old.rglob("*") if p.is_file()}
    with ByteLock(env.sup / "f1.cfg", 2):
        do_export(env)
    snap = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in env.old.rglob("*") if p.is_file()}
    do_retry(env)
    assert snap == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in env.old.rglob("*") if p.is_file()}


@winonly
def test_deleted_source_is_dropped_from_failed_and_reported_gone(env):
    target = env.sup / "f1.cfg"
    with ByteLock(target, 2):
        do_export(env)
    target.unlink()
    m = do_retry(env)
    assert m["failed"] == [] and m["retry"]["gone"] == [str(target)] and len(m["files"]) == 4
    assert verify_package(env.out)[1] == []


@winonly
def test_file_outside_rules_or_excluded_now_is_not_added(env):
    target = env.sup / "f1.cfg"
    with ByteLock(target, 2):
        do_export(env)
    h = sha(env.out)
    narrowed = parse_rule(rule_data(exclude=["f1.cfg"]))  # 규칙이 바뀌어 이제 제외 대상
    m = do_retry(env, rules=[narrowed])
    assert sha(env.out) == h and len(m["failed"]) == 1
    assert "다시 담지 않았습니다" in m["retry"]["not_retried"][0]["note"]


@winonly
def test_rule_missing_on_this_pc_keeps_failed(env):
    with ByteLock(env.sup / "f1.cfg", 2):
        do_export(env)
    h = sha(env.out)
    m = do_retry(env, rules=[])
    assert sha(env.out) == h and m["retry"]["missing_rules"] == ["demo"] and len(m["failed"]) == 1


@winonly
def test_denied_type_is_never_added_even_if_listed_as_failed(env):
    """묶음 목록(failed)이 위조/오래돼 .dll 경로를 적고 있어도 규칙 판정(restore_denied)을 거치므로 담기지 않는다."""
    dll = env.sup / "evil.dll"
    dll.write_bytes(b"MZ")
    do_export(env)
    assert not any(f["rel"].endswith("evil.dll") for f in verify_package(env.out)[0]["files"])
    _rewrite_manifest(env.out, lambda m: m["failed"].append({"src": str(dll), "error": "x", "reason": "in_use"}))
    m = do_retry(env)
    assert not any(f["rel"].endswith("evil.dll") for f in m["files"]) and len(m["retry"]["not_retried"]) == 1
    assert verify_package(env.out)[1] == []


def _rewrite_manifest(path, fn):
    with zipfile.ZipFile(path) as zf:
        items = [(i, zf.read(i)) for i in zf.infolist()]
    m = json.loads(dict((i.filename, d) for i, d in items)["manifest.json"])
    fn(m)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, d in items:
            zf.writestr(i.filename, json.dumps(m, ensure_ascii=False) if i.filename == "manifest.json" else d)


@winonly
def test_old_manifest_without_extra_fields_still_works(env):
    """retries/extras/resume 같은 나중에 생긴 필드가 없는 예전 묶음도 다시 담을 수 있다."""
    with ByteLock(env.sup / "f1.cfg", 2):
        do_export(env)
    _rewrite_manifest(env.out, lambda m: [m.pop(k, None) for k in ("retries", "extras", "compress")])
    m = do_retry(env)
    assert len(m["files"]) == 5 and verify_package(env.out)[1] == []


@winonly
def test_extras_are_restored_from_manifest(env):
    root = env.tmp / "appdata"
    folder = root / "Tool"
    folder.mkdir(parents=True)
    (folder / "a.ini").write_bytes(b"a" * 100)
    (folder / "b.ini").write_bytes(b"b" * 100)
    ex = SimpleNamespace(root="%APPDATA%", rel="Tool", path=folder, is_dir=True)

    def expand(s):
        return s.replace("%ROAM%", str(env.old)).replace("%APPDATA%", str(root))
    with ByteLock(folder / "b.ini", 2):
        m0 = export_package([env.rule], env.out, [], None, expand=expand, extras=[ex], now=NOW)
    assert len(m0["failed"]) == 1
    m = retry_failed_export(env.out, [env.rule], expand, now=NOW)
    assert m["failed"] == [] and any(f["rule"] == "extra" and f["rel"] == "Tool/b.ini" for f in m["files"])
    assert verify_package(env.out)[1] == [] and m["extras"] == [{"root": "%APPDATA%", "rel": "Tool"}]


def test_nothing_to_retry_leaves_package_alone(env):
    do_export(env)
    h = sha(env.out)
    m = do_retry(env)
    assert m["retry"]["total"] == 0 and m["retry"]["unchanged"] and sha(env.out) == h and baks(env) == []


def test_package_not_at_expected_place_or_unusable(env):
    with pytest.raises(PackageError, match="찾을 수 없"):
        do_retry(env)
    do_export(env)
    with pytest.raises(PackageError, match="찾을 수 없"):
        do_retry(env, out=env.out.parent)
    part, journal = part_paths(env.out)
    part.write_bytes(b"x")
    with pytest.raises(PackageError, match=r"\.part"):
        do_retry(env, out=part)
    assert part.read_bytes() == b"x"


def test_corrupt_package_is_rejected_and_untouched(env):
    do_export(env)
    data = bytearray(env.out.read_bytes())
    pic = next(i for i in zipfile.ZipFile(env.out).infolist() if i.filename.endswith("pic.jpg"))  # 압축 안 한 사진 파일
    data[pic.header_offset + 30 + len(pic.filename) + 100] ^= 0xFF  # 데이터 한 바이트 손상
    env.out.write_bytes(bytes(data))
    h = sha(env.out)
    with pytest.raises(PackageError, match="검사에 실패"):
        do_retry(env)
    assert sha(env.out) == h and baks(env) == [] and leftovers(env) == []
    env.out.write_bytes(b"this is not a zip")
    with pytest.raises(PackageError, match="검사에 실패"):
        do_retry(env)


# --------------------------------------------------------------------------- 실패 주입: 원래 묶음은 변하지 않는다

@winonly
@pytest.mark.parametrize("where", ["write_entry", "verify", "second_replace", "progress_cancel"])
def test_failure_midway_leaves_original_untouched(env, monkeypatch, where):
    with ByteLock(env.sup / "f1.cfg", 2):
        do_export(env)
    h = sha(env.out)
    kw = {}
    if where == "write_entry":
        monkeypatch.setattr(package, "_write_entry", lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space left")))
        exc = OSError
    elif where == "verify":
        real = package.verify_package
        monkeypatch.setattr(package, "verify_package",
                            lambda p: real(p) if Path(p) == env.out else (None, ["일부러 만든 문제"]))
        exc = PackageError
    elif where == "second_replace":
        real = os.replace

        def rep(a, b):
            if str(a).endswith(".retry"):
                raise OSError(5, "denied")
            return real(a, b)
        monkeypatch.setattr(package.os, "replace", rep)
        exc = OSError
    else:
        from pcmigrate.cancel import Cancelled

        def cancel(info):
            if info.phase == "pack":
                raise Cancelled()
        kw["progress_info"] = cancel
        monkeypatch.setattr(package.time, "monotonic", lambda: 10 ** 9)  # 첫 진행 보고부터 바로 나가게
        exc = Cancelled
    with pytest.raises(exc):
        do_retry(env, **kw)
    assert sha(env.out) == h and baks(env) == [] and leftovers(env) == []
    monkeypatch.undo()
    assert len(do_retry(env)["files"]) == 5  # 실패 뒤 다시 하면 정상 동작


# --------------------------------------------------------------------------- 락/이어받기와의 관계

@winonly
def test_resume_part_is_not_touched_and_no_conflict(env):
    with ByteLock(env.sup / "f1.cfg", 2):
        do_export(env)
    part, journal = part_paths(env.out)
    part.write_bytes(b"PART-DATA")
    journal.write_text('{"t": "head"}\n', encoding="utf-8")
    do_retry(env)
    assert part.read_bytes() == b"PART-DATA" and journal.read_text(encoding="utf-8") == '{"t": "head"}\n'
    assert verify_package(env.out)[1] == []


def test_refuses_while_another_export_holds_the_lock(env):
    do_export(env)
    lock = env.out.with_name(env.out.name + package.LOCK_SUFFIX)
    lock.write_text(json.dumps({"pid": os.getppid()}), encoding="utf-8")  # 살아 있는 다른 프로세스
    h = sha(env.out)
    with pytest.raises(PackageError, match="이미 실행 중"):
        do_retry(env)
    assert sha(env.out) == h and lock.exists()


def test_stale_lock_and_stale_retry_file_are_cleaned(env):
    do_export(env)
    lock = env.out.with_name(env.out.name + package.LOCK_SUFFIX)
    lock.write_text(json.dumps({"pid": 2 ** 22 - 3}), encoding="utf-8")
    retry_paths(env.out).write_bytes(b"junk")
    do_retry(env)
    assert leftovers(env) == []


def test_backup_names_do_not_overwrite_older_backups(env, monkeypatch):
    do_export(env)
    env.out.with_name("p.zip.bak").write_bytes(b"older backup")
    if os.name == "nt":
        with ByteLock(env.sup / "f1.cfg", 2):
            do_export(env)  # 두 번째 내보내기: 완성본이 .bak.1 로
            assert baks(env) == ["p.zip.bak", "p.zip.bak.1"]
        do_retry(env)
        assert env.out.with_name("p.zip.bak").read_bytes() == b"older backup" and "p.zip.bak.2" in baks(env)


def raw_records(path):
    """엔트리별 로컬 헤더+데이터 바이트(재압축 없이 옮겨졌는지 바이트 단위로 비교)."""
    data = Path(path).read_bytes()
    out = {}
    with zipfile.ZipFile(path) as zf:
        for i in zf.infolist():
            n = int.from_bytes(data[i.header_offset + 26:i.header_offset + 28], "little")
            m = int.from_bytes(data[i.header_offset + 28:i.header_offset + 30], "little")
            out[i.filename] = data[i.header_offset:i.header_offset + 30 + n + m + i.compress_size]
    return out


@winonly
def test_zip64_entries_are_copied_raw(env, monkeypatch):
    """force_zip64 경로(로컬 헤더에 zip64 extra, ZIP64_THRESHOLD 를 낮춰 흉내)의 엔트리도 바이트 그대로 옮겨진다."""
    monkeypatch.setattr(package, "ZIP64_THRESHOLD", 100)
    with ByteLock(env.sup / "f1.cfg", 2):
        do_export(env)
    before = {k: v for k, v in raw_records(env.out).items() if k != "manifest.json"}
    assert any(int.from_bytes(v[28:30], "little") > 0 for k, v in before.items() if k.startswith("data/"))
    m = do_retry(env)
    assert verify_package(env.out)[1] == [] and len(m["files"]) == 5
    after = raw_records(env.out)
    assert all(after[k] == v for k, v in before.items())


# --------------------------------------------------------------------------- API / CLI

@winonly
def test_api_run_export_retry_and_summary(env, monkeypatch):
    with ByteLock(env.sup / "f1.cfg", 2):
        do_export(env)
        deps = api.Deps(retry_export=lambda out, rules, **kw: retry_failed_export(out, rules, env.expand, **kw))
        r = api.run_export_retry(env.rules_dir, env.out, deps=deps)
        assert r.unchanged and len(r.still_failed) == 1
        d = r.summary_dict()
        assert d["added"] == 0 and d["still_failed"] == 1 and d["reasons"] == {"in_use": 1}
    r = api.run_export_retry(env.rules_dir, env.out, deps=deps)
    assert len(r.added) == 1 and r.still_failed == [] and r.summary_dict()["files"] == 5
    with pytest.raises(api.ApiError, match="다시 담기 실패"):
        api.run_export_retry(env.rules_dir, env.out.with_name("none.zip"), deps=deps)


def _cli_deps(env, monkeypatch):
    monkeypatch.setattr(cli, "retry_failed_export", lambda out, rules, **kw: retry_failed_export(out, rules, env.expand, **kw))


@winonly
def test_cli_retry_failed_human_and_json(env, monkeypatch, capsys):
    _cli_deps(env, monkeypatch)
    with ByteLock(env.sup / "f1.cfg", 2):
        do_export(env)
        assert cli.main(["export", "--output", str(env.out), "--retry-failed", "--rules", str(env.rules_dir)]) == 0
        out = capsys.readouterr().out
        assert "바꾸지 않았습니다" in out and "다시 담김 0개, 아직 못 담음 1개" in out
        assert "다른 프로그램이 파일을 사용 중" in out and "--retry-failed" in out and "Chrome" in out
        assert cli.main(["export", "--output", str(env.out), "--retry-failed", "--json", "--rules", str(env.rules_dir)]) == 0
        j = json.loads(capsys.readouterr().out)
        assert j["still_failed"] == 1 and j["reasons"] == {"in_use": 1} and j["unchanged"] is True
    assert cli.main(["export", "--output", str(env.out), "--retry-failed", "--rules", str(env.rules_dir)]) == 0
    out = capsys.readouterr().out
    assert "다시 담김 1개, 아직 못 담음 0개" in out and "지금 원본의 내용" in out and "p.zip.bak" in out and "import" in out
    assert verify_package(env.out)[1] == []


def test_cli_retry_failed_missing_package_and_conflicts(env, monkeypatch, capsys):
    _cli_deps(env, monkeypatch)
    rc = cli.main(["export", "--output", str(env.out), "--retry-failed", "--rules", str(env.rules_dir)])
    cap = capsys.readouterr()
    assert rc == 1 and "찾을 수 없" in cap.err and "Traceback" not in cap.err
    assert not env.out.exists()  # 새로 만들지 않는다
    for extra in (["--extra", "x.json"], ["--allow-secrets"], ["--restart"], ["--no-resume"], ["--compress", "never"]):
        assert cli.main(["export", "--output", str(env.out), "--retry-failed", *extra]) == 1
        assert "함께 쓸 수 없습니다" in capsys.readouterr().err
    assert not env.out.exists()


def test_cli_retry_failed_nothing_to_do(env, monkeypatch, capsys):
    _cli_deps(env, monkeypatch)
    do_export(env)
    assert cli.main(["export", "--output", str(env.out), "--retry-failed", "--rules", str(env.rules_dir)]) == 0
    assert "다시 담을 파일이 없습니다" in capsys.readouterr().out
