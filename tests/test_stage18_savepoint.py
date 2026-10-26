"""stage18: 내보내기 저장점 이어받기(.part/.part.journal)와 압축 생략(--compress). 모두 tmp_path 의 가짜 환경만 쓴다."""
import hashlib
import json
import os
import random
import zipfile
from types import SimpleNamespace

import pytest

from pcmigrate import api, cli, package
from pcmigrate.gui import texts as T
from pcmigrate.gui.status import StatusModel
from pcmigrate.package import (PackageError, apply_import, export_package, inspect_savepoint, part_paths,
                               plan_import, read_journal, verify_package)
from pcmigrate.progress import ProgressInfo
from pcmigrate.rules import parse_rule

NOW = lambda: __import__("datetime").datetime(2030, 1, 1, tzinfo=__import__("datetime").timezone.utc)  # noqa: E731
N = 6


def make_rule(version="7", rid="demo"):
    return parse_rule({
        "id": rid, "name": "Demo", "version": version, "detect": {"display_name_patterns": ["Demo"]},
        "paths": [{"path": "%ROAM%\\Vendor\\Demo\\Support", "dest": "demo/support", "kind": "dir"}],
        "manual_warnings": ["재로그인 필요"]})


@pytest.fixture
def env(tmp_path):
    old = tmp_path / "old"
    sup = old / "Vendor" / "Demo" / "Support"
    sup.mkdir(parents=True)
    for i in range(N):
        (sup / f"f{i}.cfg").write_bytes((f"file-{i}-" * 200).encode())
    (sup / "pic.jpg").write_bytes(os.urandom(5000))
    return SimpleNamespace(tmp=tmp_path, old=old, sup=sup, out=tmp_path / "out" / "p.zip",
                           expand=lambda s: s.replace("%ROAM%", str(old)))


def run(env, out=None, rules=None, **kw):
    notes = []
    kw.setdefault("savepoint_bytes", 1)  # 파일마다 저장점
    m = export_package(rules or [make_rule()], out or env.out, [], None, expand=env.expand, now=NOW,
                       notice=notes.append, **kw)
    return m, notes


pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")  # 시험이 일부러 열어 둔 반쯤 쓴 엔트리


def crash_after(monkeypatch, n, partial=False, power_loss=True):
    """n 번째 엔트리를 담을 때 예외를 던진다(partial 이면 zip 에 엔트리를 반쯤 쓰고). 전원 차단 모의면 마지막 저장점도 못 찍는다."""
    real = package._write_entry
    count = {"n": 0}

    def boom(zf, arc, src, stage_dir=None):
        count["n"] += 1
        if count["n"] == n + 1:
            if partial:
                w = zf.open(zipfile.ZipInfo(arc), "w")
                w.write(b"half-written-entry" * 100)
            raise OSError("simulated power loss")
        return real(zf, arc, src, stage_dir)
    monkeypatch.setattr(package, "_write_entry", boom)
    monkeypatch.setattr(package, "_GRACEFUL_SAVEPOINT", not power_loss)
    return count


def snapshot(path):
    with zipfile.ZipFile(path) as zf:
        return zf.namelist(), {n: hashlib.sha256(zf.read(n)).hexdigest() for n in zf.namelist()}


def fresh_reference(env, tmp_name="ref.zip", **kw):
    ref = env.tmp / tmp_name
    run(env, out=ref, **kw)
    return ref


# --------------------------------------------------------------------------- 저장점 이어받기

def test_resume_after_power_loss_matches_fresh_build(env, monkeypatch):
    ref = fresh_reference(env)
    crash_after(monkeypatch, 4, partial=True)
    with pytest.raises(OSError):
        run(env)
    part, jr = part_paths(env.out)
    assert part.exists() and jr.exists() and not env.out.exists()  # 최종 이름은 완성본일 때만 존재
    with open(part, "ab") as f:
        f.write(b"\0" * 777)  # 쓰다 만 뒷부분
    monkeypatch.undo()
    m, notes = run(env)
    assert m["resume"]["resumed"] and m["resume"]["kept_files"] == 4
    assert any("이어서 하는 중" in x and "4개 파일" in x for x in notes)
    assert not part.exists() and not jr.exists()
    assert verify_package(env.out)[1] == []
    assert snapshot(env.out) == snapshot(ref)
    assert env.out.read_bytes() == ref.read_bytes() or snapshot(env.out) == snapshot(ref)
    a, b = zipfile.ZipFile(env.out), zipfile.ZipFile(ref)
    assert a.read("manifest.json") == b.read("manifest.json")  # manifest 도 처음부터 만든 것과 같다


def test_graceful_failure_keeps_up_to_last_finished_entry(env, monkeypatch):
    crash_after(monkeypatch, 5, partial=True, power_loss=False)
    with pytest.raises(OSError):
        run(env, savepoint_bytes=10 ** 12)  # 간격 안에는 저장점이 없어도
    info = inspect_savepoint(env.out)
    assert info["usable"] and info["files"] == 5  # 실패 때 마지막으로 끝난 엔트리까지 확정
    monkeypatch.undo()
    m, _ = run(env, savepoint_bytes=10 ** 12)
    assert m["resume"]["kept_files"] == 5 and verify_package(env.out)[1] == []


def test_entries_after_last_savepoint_are_not_trusted(env, monkeypatch):
    crash_after(monkeypatch, 4, power_loss=True)
    with pytest.raises(OSError):
        run(env, savepoint_bytes=10 ** 12)  # 저장점이 하나도 없다(첫 줄 head 만)
    monkeypatch.undo()
    m, notes = run(env, savepoint_bytes=10 ** 12)
    assert not m["resume"]["resumed"] and verify_package(env.out)[1] == []


def test_torn_last_journal_line_is_ignored(env, monkeypatch):
    ref = fresh_reference(env)
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    part, jr = part_paths(env.out)
    with open(jr, "ab") as f:
        f.write(b'{"t":"e","arc":"data/demo/su')  # 줄바꿈 없이 끊긴 마지막 줄
    monkeypatch.undo()
    m, _ = run(env)
    assert m["resume"]["kept_files"] == 3 and snapshot(env.out) == snapshot(ref)


def test_corrupt_journal_middle_restarts_with_reason(env, monkeypatch):
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    part, jr = part_paths(env.out)
    lines = jr.read_bytes().split(b"\n")
    lines[2] = b"{garbage"
    jr.write_bytes(b"\n".join(lines))
    monkeypatch.undo()
    m, notes = run(env)
    assert not m["resume"]["resumed"] and "손상" in m["resume"]["reason"] and any("처음부터" in x for x in notes)
    assert verify_package(env.out)[1] == [] and not part.exists() and not jr.exists()


def test_part_without_journal_restarts(env, monkeypatch):
    part, jr = part_paths(env.out)
    env.out.parent.mkdir(parents=True)
    part.write_bytes(b"PK junk from somewhere")
    m, _ = run(env)
    assert not m["resume"]["resumed"] and "기록이 없" in m["resume"]["reason"]
    assert verify_package(env.out)[1] == [] and not part.exists()


def test_journal_without_part_restarts(env, monkeypatch):
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    part, jr = part_paths(env.out)
    part.unlink()
    monkeypatch.undo()
    m, _ = run(env)
    assert not m["resume"]["resumed"] and verify_package(env.out)[1] == []


def test_truncated_part_restarts(env, monkeypatch):
    crash_after(monkeypatch, 4)
    with pytest.raises(OSError):
        run(env)
    part, jr = part_paths(env.out)
    part.write_bytes(part.read_bytes()[:100])  # 저장점 기록보다 짧다
    monkeypatch.undo()
    m, _ = run(env)
    assert not m["resume"]["resumed"] and "맞지 않" in m["resume"]["reason"]
    assert verify_package(env.out)[1] == []


@pytest.mark.parametrize("what,expect", [("rules", "선택한 프로그램"), ("version", "규칙의 버전"), ("compress", "압축 설정")])
def test_fingerprint_mismatch_restarts_and_says_why(env, monkeypatch, what, expect):
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    monkeypatch.undo()
    kw = {}
    rules = None
    if what == "rules":
        rules = [make_rule(), make_rule(rid="other")]
        # 같은 경로를 두 번 담지 않도록 다른 규칙은 경로 없음
        rules[1] = parse_rule({"id": "other", "name": "O", "detect": {"display_name_patterns": ["O"]},
                               "paths": [], "manual_warnings": []})
    elif what == "version":
        rules = [make_rule(version="8")]
    else:
        kw["compress"] = "never"
    m, notes = run(env, rules=rules, **kw)
    assert not m["resume"]["resumed"] and expect in m["resume"]["reason"]
    assert any(expect in x for x in notes)
    assert verify_package(env.out)[1] == []


def test_changed_source_file_is_re_added_only(env, monkeypatch):
    crash_after(monkeypatch, 5)
    with pytest.raises(OSError):
        run(env)
    # 이미 담긴 파일(f1)의 원본이 바뀐다 -> 그 파일만 다시 담고, 나머지는 보존(가운데 구멍이 있어도 안전하게 이어받음)
    (env.sup / "f1.cfg").write_bytes(b"changed content now")
    monkeypatch.undo()
    m, _ = run(env)
    assert m["resume"]["resumed"] and m["resume"]["dropped"] == 1 and m["resume"]["kept_files"] == 4
    assert verify_package(env.out)[1] == []
    ref = fresh_reference(env, "ref2.zip")
    (n1, h1), (n2, h2) = snapshot(env.out), snapshot(ref)
    assert sorted(n1) == sorted(n2) and h1 == h2  # 다시 담은 파일은 zip 끝에 붙으므로 순서만 다를 수 있다
    assert zipfile.ZipFile(env.out).read("manifest.json") == zipfile.ZipFile(ref).read("manifest.json")
    with zipfile.ZipFile(env.out) as zf:
        assert zf.read("data/demo/support/Support/f1.cfg") == b"changed content now"


def test_deleted_source_file_is_dropped(env, monkeypatch):
    crash_after(monkeypatch, 4)
    with pytest.raises(OSError):
        run(env)
    (env.sup / "f0.cfg").unlink()
    monkeypatch.undo()
    m, _ = run(env)
    assert m["resume"]["dropped"] == 1 and "data/demo/support/Support/f0.cfg" not in zipfile.ZipFile(env.out).namelist()
    assert verify_package(env.out)[1] == []


def test_unreadable_file_is_retried_on_next_attempt(env, monkeypatch):
    real = package._write_entry
    state = {"locked": True, "n": 0}

    def flaky(zf, arc, src, stage_dir=None):
        state["n"] += 1
        if state["locked"] and src.name == "f1.cfg":
            raise package.SourceUnreadable(src, PermissionError(33, "lock"), during_read=True)
        if state["locked"] and state["n"] == 5:
            raise OSError("crash")
        return real(zf, arc, src, stage_dir)
    monkeypatch.setattr(package, "_write_entry", flaky)
    with pytest.raises(OSError):
        run(env)
    jr = part_paths(env.out)[1]
    assert any(json.loads(x)["t"] == "f" for x in jr.read_text(encoding="utf-8").splitlines())  # journal 에도 기록
    state["locked"] = False  # 잠근 프로그램(Chrome 등)을 닫은 뒤 다시 시도
    m, _ = run(env)
    assert m["resume"]["resumed"] and m["failed"] == []
    assert "data/demo/support/Support/f1.cfg" in zipfile.ZipFile(env.out).namelist()


def test_existing_final_file_untouched_until_complete_then_bak(env, monkeypatch):
    env.out.parent.mkdir(parents=True)
    env.out.write_bytes(b"old complete package")
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    assert env.out.read_bytes() == b"old complete package"  # 실패해도 완성본은 그대로
    monkeypatch.undo()
    run(env)
    assert env.out.with_name("p.zip.bak").read_bytes() == b"old complete package"
    assert verify_package(env.out)[1] == []


def test_no_resume_ignores_and_deletes_existing_part_and_keeps_final(env, monkeypatch):
    env.out.parent.mkdir(parents=True)
    env.out.write_bytes(b"old complete package")
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    part, jr = part_paths(env.out)
    assert part.exists() and jr.exists()
    monkeypatch.undo()
    m, notes = run(env, resume=False)
    assert not m["resume"]["resumed"] and any("처음부터 다시 만들기" in x for x in notes)
    assert not part.exists() and not jr.exists()
    assert env.out.with_name("p.zip.bak").read_bytes() == b"old complete package"
    assert verify_package(env.out)[1] == []


def test_no_resume_discards_even_when_fresh_attempt_also_fails(env, monkeypatch):
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    part, jr = part_paths(env.out)
    old_journal = jr.read_bytes()
    monkeypatch.undo()
    crash_after(monkeypatch, 1)
    with pytest.raises(OSError):
        run(env, resume=False)
    assert jr.read_bytes() != old_journal  # 이전 기록을 버리고 새로 시작했다
    assert inspect_savepoint(env.out)["files"] <= 1


def test_resume_verify_failure_rebuilds_from_scratch(env, monkeypatch):
    ref = fresh_reference(env)
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    part, jr = part_paths(env.out)
    data = bytearray(part.read_bytes())
    # 보존 구간 안쪽(첫 엔트리 데이터)을 한 바이트 손상: 지역 헤더는 멀쩡하지만 최종 검사에서 걸린다
    first = read_journal(jr)["entries"][0]
    data[first["end"] - 3] ^= 0xFF
    part.write_bytes(bytes(data))
    monkeypatch.undo()
    m, notes = run(env)
    assert not m["resume"]["resumed"] and "검사를 통과하지 못해" in m["resume"]["reason"]
    assert snapshot(env.out) == snapshot(ref)


def test_part_file_cannot_be_imported(env, monkeypatch, tmp_path):
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    part = part_paths(env.out)[0]
    with pytest.raises(PackageError, match="완성되지 않은"):
        plan_import(part, [make_rule()])
    complete_named_part = tmp_path / "x.zip.part"
    complete_named_part.write_bytes(b"")
    with pytest.raises(PackageError):
        plan_import(complete_named_part, [make_rule()])


def test_resumed_package_imports_normally(env, monkeypatch):
    crash_after(monkeypatch, 4, partial=True)
    with pytest.raises(OSError):
        run(env)
    monkeypatch.undo()
    run(env)
    new = env.tmp / "new"
    new.mkdir()
    plan = plan_import(env.out, [make_rule()], expand=lambda s: s.replace("%ROAM%", str(new)))
    assert plan.problems == []
    rep = apply_import(env.out, plan)
    assert all(r.status in ("copied", "verified") for r in rep.results)
    assert (new / "Vendor" / "Demo" / "Support" / "f3.cfg").read_bytes() == (env.sup / "f3.cfg").read_bytes()


def test_time_based_savepoint(env, monkeypatch):
    t = {"v": 0.0}

    def clock():
        t["v"] += 100.0
        return t["v"]
    crash_after(monkeypatch, 4)
    with pytest.raises(OSError):
        export_package([make_rule()], env.out, [], None, expand=env.expand, now=NOW, clock=clock,
                       savepoint_bytes=10 ** 12, savepoint_seconds=60)  # 크기 기준은 못 채우지만 시간 기준으로 저장점이 찍힌다
    assert inspect_savepoint(env.out)["files"] == 4


def test_savepoint_resume_progress_reports_resumed_count(env, monkeypatch):
    crash_after(monkeypatch, 4)
    with pytest.raises(OSError):
        run(env)
    monkeypatch.undo()
    events = []
    export_package([make_rule()], env.out, [], None, expand=env.expand, now=NOW, progress_info=events.append,
                   savepoint_bytes=1)
    assert any(e.resumed == 4 for e in events) and events[-1].resumed == 4
    sm = StatusModel()
    sm.begin(T.TASK_EXPORT)
    for e in events:
        sm.info(e)
    assert "4" in sm.snapshot().resumed_line and "저장점" in sm.snapshot().resumed_line


def test_resume_hint_and_inspect(env, monkeypatch):
    assert inspect_savepoint(env.out) is None
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    info = inspect_savepoint(env.out)
    assert info["usable"] and info["files"] == 3 and info["bytes"] > 0
    part, jr = part_paths(env.out)
    jr.write_bytes(b"not json\n")
    assert inspect_savepoint(env.out)["usable"] is False


# --------------------------------------------------------------------------- 압축 생략

def test_compress_modes_and_manifest_record(env):
    methods = {}
    for mode in ("auto", "always", "never"):
        out = env.tmp / f"c-{mode}.zip"
        m, _ = run(env, out=out, compress=mode)
        assert m["compress"] == mode and verify_package(out)[1] == []  # STORED/DEFLATED 만 허용하는 검사와 호환
        with zipfile.ZipFile(out) as zf:
            methods[mode] = {i.filename.rsplit("/", 1)[-1]: i.compress_type for i in zf.infolist()}
        by = {f["arc"].rsplit("/", 1)[-1]: f["method"] for f in m["files"]}
        assert by["pic.jpg"] == ("deflated" if mode == "always" else "stored")
        assert by["f0.cfg"] == ("stored" if mode == "never" else "deflated")
    assert methods["auto"]["pic.jpg"] == zipfile.ZIP_STORED and methods["auto"]["f0.cfg"] == zipfile.ZIP_DEFLATED
    assert set(methods["never"].values()) == {zipfile.ZIP_STORED}
    assert methods["always"]["pic.jpg"] == zipfile.ZIP_DEFLATED


@pytest.mark.parametrize("mode", ["auto", "never", "always"])
def test_import_handles_both_methods(env, mode):
    out = env.tmp / "c.zip"
    run(env, out=out, compress=mode)
    new = env.tmp / "new"
    new.mkdir()
    plan = plan_import(out, [make_rule()], expand=lambda s: s.replace("%ROAM%", str(new)))
    assert plan.problems == []
    rep = apply_import(out, plan)
    assert all(r.status in ("copied", "verified") for r in rep.results)
    assert (new / "Vendor" / "Demo" / "Support" / "pic.jpg").read_bytes() == (env.sup / "pic.jpg").read_bytes()


def test_choose_method_extension_and_sampling(tmp_path):
    import io
    cm = package.choose_method
    assert cm("data/x/a.JPG", 10, "auto") == zipfile.ZIP_STORED
    assert cm("data/x/a.docx", 10, "auto") == zipfile.ZIP_STORED
    assert cm("data/x/a.rvt", 10, "auto") == zipfile.ZIP_DEFLATED  # CAD/BIM 은 목록에 없다
    assert cm("data/x/a.dwg", 10, "auto") == zipfile.ZIP_DEFLATED
    big = package.SAMPLE_MIN_SIZE + 1
    rnd = io.BytesIO(random.Random(1).randbytes(big))
    zeros = io.BytesIO(bytes(big))
    assert cm("data/x/a.bin", big, "auto", rnd) == zipfile.ZIP_STORED  # 큰 파일이 표본상 안 줄면 생략
    assert cm("data/x/a.bin", big, "auto", zeros) == zipfile.ZIP_DEFLATED
    assert cm("data/x/a.bin", 1000, "auto", io.BytesIO(os.urandom(1000))) == zipfile.ZIP_DEFLATED  # 작은 파일은 표본 검사 안 함
    with pytest.raises(ValueError):
        export_package([], tmp_path / "z.zip", [], None, compress="zip9")


def test_resume_keeps_compress_method_identical(env, monkeypatch):
    crash_after(monkeypatch, 5)
    with pytest.raises(OSError):
        run(env, compress="auto")
    monkeypatch.undo()
    run(env, compress="auto")
    ref = fresh_reference(env, compress="auto")
    a, b = zipfile.ZipFile(env.out), zipfile.ZipFile(ref)
    assert [(i.filename, i.compress_type, i.CRC) for i in a.infolist()] == [(i.filename, i.compress_type, i.CRC) for i in b.infolist()]


# --------------------------------------------------------------------------- api / cli / gui

def _deps(env):
    from pcmigrate.detect import Program
    return api.Deps(registry=lambda: [Program("Demo", "1", "V")], winget_export=lambda: None, winget_list=lambda: None,
                    summarize=lambda rules, programs: (0, 0))


def _rules_dir(env):
    d = env.tmp / "rules"
    d.mkdir()
    (d / "demo.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "7", "detect": {"display_name_patterns": ["Demo"]},
        "paths": [{"path": "%ROAM%\\Vendor\\Demo\\Support", "dest": "demo/support", "kind": "dir"}],
        "manual_warnings": []}), encoding="utf-8")
    return d


def test_api_run_export_resume_flag_and_compress_validation(env, monkeypatch):
    monkeypatch.setenv("ROAM", str(env.old))
    from pcmigrate import envpaths
    monkeypatch.setattr(envpaths, "expand_env", lambda s: s.replace("%ROAM%", str(env.old)))
    monkeypatch.setattr(package, "expand_env", envpaths.expand_env)
    seen = {}

    def fake_export(matched, out, registry, winget_text, **kw):
        seen.update(kw)
        return {"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []}
    d = _deps(env)
    d.export = fake_export
    rd = _rules_dir(env)
    api.run_export(rd, env.out, deps=d)
    assert "resume" not in seen and "compress" not in seen  # 기본값이면 넘기지 않는다(예전 구현과 호환)
    api.run_export(rd, env.out, deps=d, resume=False, compress="never")
    assert seen["resume"] is False and seen["compress"] == "never"
    with pytest.raises(api.ApiError):
        api.run_export(rd, env.out, deps=d, compress="zip9")


def test_cli_flags_parse_and_pass_through(env, monkeypatch, capsys):
    p = cli.build_parser()
    a = p.parse_args(["export", "--output", "x.zip"])
    assert a.resume is True and a.restart is False and a.compress == "auto"
    assert p.parse_args(["export", "--output", "x.zip", "--no-resume"]).resume is False
    assert p.parse_args(["export", "--output", "x.zip", "--restart"]).restart is True
    assert p.parse_args(["export", "--output", "x.zip", "--compress", "never"]).compress == "never"
    seen = {}

    def fake_run_export(rules, output, **kw):
        seen.update(kw)
        kw["notice"]("이어서 하는 중: 저장점까지 3개 파일을 보존했습니다.")
        manifest = {"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": [],
                    "resume": {"resumed": True, "kept_files": 3}}
        return api.ExportResult(manifest, api.ExtraPrep([], 0), [], [], api.build_checklist([], [], [], None), None)
    monkeypatch.setattr(cli, "run_export", fake_run_export)
    assert cli.main(["export", "--output", str(env.out), "--no-resume", "--compress", "always"]) == 0
    cap = capsys.readouterr()
    assert seen["resume"] is False and seen["compress"] == "always"
    assert "[이어받기] 이어서 하는 중" in cap.err and '"resumed_files": 3' in cap.out
    cli.main(["export", "--output", str(env.out), "--restart"])
    assert seen["resume"] is False
    cli.main(["export", "--output", str(env.out)])
    assert seen["resume"] is True


def test_gui_controller_resume_hint_and_restart_flag(env, monkeypatch, tmp_path):
    from pcmigrate.gui.controller import Controller
    from pcmigrate.gui.runner import Runner
    calls = []

    class Fake:
        ApiError = api.ApiError

        def run_export(self, rules_dir, output, *, extra=None, allow_secrets=False, deps=None, extra_items=None,
                       progress_info=None, resume=True):
            calls.append(resume)
            return SimpleNamespace(manifest={"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []},
                                   resume={})
    c = Controller(api_mod=Fake(), runner=Runner(sync=True), rules_dir=tmp_path, make_items=lambda d: [])
    assert c.export_resume_hint(str(env.out)) == ""
    crash_after(monkeypatch, 3)
    with pytest.raises(OSError):
        run(env)
    monkeypatch.undo()
    hint = c.export_resume_hint(str(env.out))
    assert "이어서" in hint and "3" in hint
    c.start_export(str(env.out), write_confirmed=True)
    c.start_export(str(env.out), write_confirmed=True, resume=False)
    assert calls == [True, False]
    jr = part_paths(env.out)[1]
    jr.write_bytes(b"xx\n")
    assert "처음부터" in c.export_resume_hint(str(env.out))
