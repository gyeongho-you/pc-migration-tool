"""설정 기록 모드(record_mode / record_snapshot / record_cli) 시험. 가짜 AppData 와 환경변수 주입만 쓴다(실제 사용자 폴더 접근 없음)."""
import json
import os
from pathlib import Path

import pytest

from pcmigrate import cli, envpaths, record_mode as rm, record_snapshot as rs
from pcmigrate.cancel import Cancelled
from pcmigrate.rules import load_rules, parse_rule

USER = "alice_secret"
T0 = 1_700_000_000


class Fake:
    """가짜 사용자 프로필. 파일 쓰기마다 mtime 을 올려 서로 다른 시각이 되게 한다."""

    def __init__(self, base: Path, monkeypatch):
        self.home = base / "Users" / USER
        self.app = self.home / "AppData" / "Roaming"
        self.loc = self.home / "AppData" / "Local"
        self.pd = base / "ProgramData"
        self.docs = self.home / "Documents"
        self.work = base / "work"
        for d in (self.app, self.loc, self.pd, self.docs):
            d.mkdir(parents=True)
        for k, v in {"USERPROFILE": self.home, "HOME": self.home, "APPDATA": self.app, "LOCALAPPDATA": self.loc,
                     "PROGRAMDATA": self.pd}.items():
            monkeypatch.setenv(k, str(v))
        envpaths.set_overrides(None)
        monkeypatch.setattr(rs, "_known_documents", lambda: str(self.docs))
        monkeypatch.setattr(rm, "default_process_list", lambda: None)
        self.clock = T0

    def put(self, path, text: str = "x") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.touch(path)
        return path

    def touch(self, path) -> None:
        self.clock += 10
        os.utime(path, (self.clock, self.clock))

    def start(self, program="Fake Zephyr", **kw):
        return rm.start(program, str(self.work), documents_fn=lambda: str(self.docs), **kw)

    def snap(self, s, label=None, **kw):
        return rm.snapshot(s, label, documents_fn=lambda: str(self.docs), **kw)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    yield f
    envpaths.set_overrides(None)


def zdir(f):
    return f.app / "FakeZephyr"


def test_a_one_setting_file_changed(fake):
    z = zdir(fake)
    ini = fake.put(z / "settings.ini", "a=1")
    fake.put(z / "readme.txt", "r")
    s, r = fake.start()
    assert r.files == 2
    fake.snap(s)  # baseline
    fake.put(ini, "a=2")
    fake.snap(s)  # after1
    a = rm.diff(s)
    assert [c.path for c in a.candidates] == ["%APPDATA%\\FakeZephyr\\settings.ini"]
    c = a.candidates[0]
    assert c.change == "수정" and c.ext == ".ini" and c.size == 3 and c.confidence == rm.MID and not a.registry_likely


def test_b_log_noise_is_excluded_by_baseline(fake):
    z = zdir(fake)
    log = fake.put(z / "app.log", "1")
    ini = fake.put(z / "settings.ini", "a=1")
    s, _ = fake.start()
    fake.put(log, "22")
    fake.snap(s)  # baseline: 로그만 바뀜
    fake.put(log, "333")
    fake.put(ini, "a=2")
    fake.snap(s)
    a = rm.diff(s)
    assert [c.path for c in a.candidates] == ["%APPDATA%\\FakeZephyr\\settings.ini"]
    assert a.noise_files == 1
    rule = rm.build_rule(a)
    assert "*.log" in rule["exclude"]
    assert "app.log" not in json.dumps(rule["paths"])


def test_b2_no_baseline_warns(fake):
    ini = fake.put(zdir(fake) / "s.ini", "1")
    s, _ = fake.start()
    fake.put(ini, "2")
    fake.snap(s, "after1")
    a = rm.analyze(s)
    assert any("기준선" in w for w in a.warnings)


def test_c_confidence_accumulates_over_rounds(fake):
    z = zdir(fake)
    always = fake.put(z / "always.ini", "0")
    twice = fake.put(z / "twice.ini", "0")
    once = fake.put(z / "once.dat", "0")
    s, _ = fake.start()
    fake.snap(s)
    for i in (1, 2, 3):
        fake.put(always, str(i))
        if i != 2:
            fake.put(twice, str(i))
        if i == 1:
            fake.put(once, "z")
        fake.snap(s)
    a = rm.diff(s)
    by = {c.path.rsplit("\\", 1)[1]: c for c in a.candidates}
    assert by["always.ini"].confidence == rm.HIGH and by["always.ini"].hits == 3
    assert by["twice.ini"].confidence == rm.MID
    assert by["once.dat"].confidence == rm.LOW
    assert a.candidates[0].path.endswith("always.ini")  # 확신도 순


def test_d_nothing_changed_registry_notice(fake):
    fake.put(zdir(fake) / "s.ini", "1")
    s, _ = fake.start()
    fake.snap(s)
    fake.snap(s)
    a, rule, out = rm.write_draft(s)
    assert a.registry_likely and rule is None and out is None
    assert not (fake.work / rm.DRAFT_FILE).exists()
    assert "레지스트리" in "\n".join(a.warnings)
    assert json.loads((fake.work / rm.REPORT_FILE).read_text("utf-8"))["status"] == "no_draft"


def test_e_symlink_locked_cloud_and_long_path(fake):
    z = zdir(fake)
    fake.put(z / "ok.ini", "1")
    fake.put(z / "locked.ini", "1")
    fake.put(z / "cloud.ini", "1")
    outside = fake.home / "outside"
    fake.put(outside / "secret.ini", "1")
    linked = False
    try:
        os.symlink(outside, z / "link", target_is_directory=True)
        linked = True
    except (OSError, NotImplementedError):
        pass
    deep = z
    for i in range(6):
        deep = deep / ("d" * 50 + str(i))
    long_ok = False
    try:
        lp = Path("\\\\?\\" + str(deep / "deep.ini")) if os.name == "nt" else deep / "deep.ini"
        fake.put(lp, "1")
        long_ok = True
    except OSError:
        pass

    def hash_fn(path, cancel=None):
        if path.endswith("locked.ini"):
            raise PermissionError(13, "denied", path)
        return rs.sha256_file(path, cancel)

    def attrs(p):
        return 0x400000 if p.endswith("cloud.ini") else 0  # 클라우드 전용(내려받지 않음)

    s, r = fake.start(hash_fn=hash_fn, attr_getter=attrs)
    assert r.unreadable == 1 and r.cloud_skipped == 1
    snap = s.load_snap("before")
    keys = " ".join(snap.files)
    assert "secret.ini" not in keys and "cloud.ini" not in keys
    assert snap.files["%APPDATA%\\FakeZephyr\\locked.ini"][2] is None  # 격리: 크기/시각만 기록
    if linked:
        assert r.links_skipped == 1
    if long_ok:
        assert any(k.endswith("deep.ini") and len(fake.app.parts) and len(k) > 260 - len(str(fake.app)) for k in snap.files)
    rep = json.loads((fake.work / rm.REPORT_FILE).read_text("utf-8"))
    assert rep["unreadable"] == 1 and rep["cloud_skipped"] == 1 and rep["status"] == "ok"


def test_f_privacy_no_user_name_anywhere(fake, capsys):
    z = zdir(fake)
    ini = fake.put(z / "s.ini", "1")
    fake.put(fake.docs / "FakeZephyr" / "tpl.ini", "1")
    fake.put(fake.home / ".fakezephyr" / "rc", "1")
    s, _ = fake.start()
    fake.snap(s)
    fake.put(ini, "2")
    fake.snap(s)
    rm.write_draft(s)
    blob = "".join(p.read_text("utf-8") for p in fake.work.iterdir())
    assert USER not in blob
    snap = s.load_snap("before")
    assert any(k.startswith("%USERPROFILE%\\Documents\\") for k in snap.files)
    assert any(k.startswith("%USERPROFILE%\\.fakezephyr\\") for k in snap.files)
    rc = cli.main(["record", "status", "--work", str(fake.work)])
    cap = capsys.readouterr()
    assert rc == 0 and USER not in cap.out + cap.err


def test_g_rule_draft_passes_check_rules(fake, tmp_path, capsys):
    z = zdir(fake)
    a1 = fake.put(z / "a.ini", "1")
    a2 = fake.put(z / "b.xml", "1")
    single = fake.put(fake.app / "OtherDir" / "FakeZephyr.cfg", "1")
    fake.put(z / "x.log", "1")
    s, _ = fake.start()
    fake.snap(s)
    for v in ("2", "3"):
        fake.put(a1, v)
        fake.put(a2, v)
        fake.put(single, v)
        fake.put(z / "x.log", v)
        fake.snap(s)
    a, rule, out = rm.write_draft(s, output=str(tmp_path / "rules" / "z.json"))
    pr = [p["path"] for p in rule["paths"]]
    assert "%APPDATA%\\FakeZephyr" in pr and "%APPDATA%\\OtherDir\\FakeZephyr.cfg" in pr  # 같은 폴더 2개는 폴더로, 단독은 파일로
    assert next(p for p in rule["paths"] if p["path"].endswith("FakeZephyr.cfg"))["kind"] == "file"
    assert "설정 기록 모드로 생성, 확인 필요" in rule["notes"] and "라운드 2회" in rule["notes"]
    assert rule["manual_warnings"] and "*.log" in rule["exclude"]
    rules = load_rules(tmp_path / "rules")
    assert rules[0].id == "fake-zephyr" and parse_rule(json.loads(out.read_text("utf-8")))
    rc = cli.main(["check-rules", "--rules", str(tmp_path / "rules")])
    assert rc == 0 and '"rules": 1' in capsys.readouterr().out


def test_h_cancel_and_error_still_write_report(fake):
    fake.put(zdir(fake) / "s.ini", "1")
    s, _ = fake.start()
    with pytest.raises(Cancelled):
        fake.snap(s, cancel=lambda: True)
    rep = json.loads((fake.work / rm.REPORT_FILE).read_text("utf-8"))
    assert rep["status"] == "cancelled" and "저장되지 않았" in rep["note"]
    assert [i["label"] for i in s.snaps] == ["before"] and not (fake.work / "snap-baseline.json").exists()

    def boom(path, cancel=None):
        raise RuntimeError("boom")
    fake.put(zdir(fake) / "t.ini", "1")
    with pytest.raises(RuntimeError):
        fake.snap(s, hash_fn=boom)
    assert json.loads((fake.work / rm.REPORT_FILE).read_text("utf-8"))["status"] == "failed"
    fake.snap(s)  # 같은 라벨로 다시 찍을 수 있다
    assert [i["label"] for i in s.snaps] == ["before", "baseline"]


def test_h2_cli_cancel_exit_code(fake, monkeypatch, capsys):
    fake.put(zdir(fake) / "s.ini", "1")
    assert cli.main(["record", "start", "--program", "Fake Zephyr", "--work", str(fake.work)]) == 0

    def cancelled(*a, **k):
        raise Cancelled()
    monkeypatch.setattr(rm, "snapshot", cancelled)
    assert cli.main(["record", "snapshot", "--work", str(fake.work)]) == 130
    assert "저장하지 않았" in capsys.readouterr().err


def test_i_no_file_content_in_snapshots(fake):
    marker = "SUPER-SECRET-CONTENT-12345"
    fake.put(zdir(fake) / "s.ini", marker)
    s, _ = fake.start()
    fake.snap(s)
    for p in fake.work.iterdir():
        assert marker not in p.read_text("utf-8")
    snap = json.loads((fake.work / "snap-before.json").read_text("utf-8"))
    row = list(snap["files"].values())[0]
    assert row[0] == len(marker) and len(row[2]) == 64


def test_j_mtime_only_change_is_not_candidate_and_hash_optimized(fake):
    z = zdir(fake)
    touched = fake.put(z / "touched.ini", "same")
    real = fake.put(z / "real.ini", "1")
    calls = []

    def hf(path, cancel=None):
        calls.append(os.path.basename(path))
        return rs.sha256_file(path, cancel)
    s, _ = fake.start(hash_fn=hf)
    assert sorted(calls) == ["real.ini", "touched.ini"]
    calls.clear()
    fake.snap(s, hash_fn=hf)  # 아무것도 안 바뀜: 파일을 읽지 않고 해시를 이어받음
    assert calls == []
    fake.touch(touched)  # 내용은 같고 mtime 만 바뀜
    fake.put(real, "2")
    fake.snap(s, hash_fn=hf)
    assert sorted(calls) == ["real.ini", "touched.ini"]  # 바뀐 것만 해시
    a = rm.diff(s)
    assert [c.path.rsplit("\\", 1)[1] for c in a.candidates] == ["real.ini"] and a.mtime_only == 1


def test_big_file_without_hash_uses_size_mtime(fake):
    big = fake.put(zdir(fake) / "big.cfg", "x" * 100)
    s, _ = fake.start(hash_limit=10)
    assert s.load_snap("before").files["%APPDATA%\\FakeZephyr\\big.cfg"][2] is None
    fake.snap(s)
    fake.put(big, "y" * 100)
    fake.snap(s)
    a = rm.diff(s)
    assert a.candidates and "내용 비교 불가" in a.candidates[0].reason


def test_noise_dirs_default_excluded_and_include_caches(fake):
    z = zdir(fake)
    fake.put(z / "Cache" / "c.bin", "1")
    fake.put(z / "GPUCache" / "g.bin", "1")
    fake.put(z / "keep.ini", "1")
    fake.put(fake.app / "Temp" / "t.ini", "1")
    fake.put(fake.loc / "Microsoft" / "Windows" / "w.ini", "1")
    s, _ = fake.start(all_roots=True)
    assert list(s.load_snap("before").files) == ["%APPDATA%\\FakeZephyr\\keep.ini"]
    s2, _ = rm.start("Fake Zephyr", str(fake.work.parent / "work2"), all_roots=True, include_caches=True,
                     documents_fn=lambda: str(fake.docs))
    assert len(s2.load_snap("before").files) == 5


def test_program_narrowing_by_name(fake):
    fake.put(fake.app / "Vendor" / "FakeZephyr" / "a.ini", "1")  # 두 단계 아래
    fake.put(fake.app / "Unrelated" / "b.ini", "1")
    fake.put(fake.loc / "Zephyr3D" / "c.ini", "1")  # 낱말 일부 일치
    s, _ = fake.start()
    assert sorted(s.load_snap("before").files) == ["%APPDATA%\\Vendor\\FakeZephyr\\a.ini", "%LOCALAPPDATA%\\Zephyr3D\\c.ini"]
    assert rs.match_words("3DF Zephyr") == ["3df", "zephyr", "3dfzephyr"]


def test_running_process_warning_does_not_block(fake):
    fake.put(zdir(fake) / "s.ini", "1")
    s, r = fake.start(process_list_fn=lambda: ["explorer.exe", "FakeZephyr.exe"])
    assert r.running == ["FakeZephyr.exe"] and r.status == "ok"
    assert json.loads((fake.work / rm.REPORT_FILE).read_text("utf-8"))["running_processes"] == ["FakeZephyr.exe"]


def test_label_rules_and_state(fake):
    fake.put(zdir(fake) / "s.ini", "1")
    s, _ = fake.start()
    with pytest.raises(rm.RecordError):
        fake.start()  # 이미 기록 있음
    with pytest.raises(rm.RecordError):
        fake.snap(s, "weird")
    fake.snap(s)
    fake.snap(s)
    assert [i["label"] for i in s.snaps] == ["before", "baseline", "after1"]
    with pytest.raises(rm.RecordError):
        fake.snap(s, "baseline2")  # after 뒤 기준선 금지
    assert "다음 할 일" in rm.render_status(s)[-1]
    assert rm.load_session(fake.work).state["program"] == "Fake Zephyr"


def test_max_files_truncation_is_reported(fake):
    for i in range(5):
        fake.put(zdir(fake) / f"f{i}.ini", "1")
    s, r = fake.start(max_files=3)
    assert r.truncated and r.files == 3 and any("--max-files" in w for w in r.warnings)


def test_cli_full_flow(fake, capsys):
    ini = fake.put(zdir(fake) / "settings.ini", "1")
    w = ["--work", str(fake.work)]
    assert cli.main(["record", "start", "--program", "Fake Zephyr", "--work", str(fake.work)]) == 0
    assert cli.main(["record", "snapshot", *w]) == 0
    fake.put(ini, "2")
    assert cli.main(["record", "snapshot", *w]) == 0
    assert cli.main(["record", "diff", *w, "--json"]) == 0
    assert '"confidence": "중간"' in capsys.readouterr().out
    assert cli.main(["record", "rule-draft", *w]) == 0
    assert (fake.work / rm.DRAFT_FILE).exists()
    assert cli.main(["record", "status", "--work", str(fake.work / "none")]) == 1
    cap = capsys.readouterr()
    assert USER not in cap.out + cap.err
