"""설정 기록 모드 리뷰 수정(K1~K14) 회귀 시험. 가짜 프로필 + 환경변수 주입만 쓴다.

기본 루트 토큰만 쓰던 기존 시험의 사각지대(--roots 로 앱 폴더/루트 자체를 주는 경로, 작업 폴더가 루트 안에 있는 경우)를 메운다.
"""
import json
import os
from pathlib import Path

import pytest

from pcmigrate import cli, envpaths, errorlog, record_mode as rm, record_snapshot as rs
from test_record_mode import USER, Fake


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    # 환경변수 철자를 한 가지로 고정하고 덮어쓰기까지 걸어 실제 %ProgramData%/AppData 를 훑지 못하게 한다
    envpaths.set_overrides({"USERPROFILE": str(f.home), "HOME": str(f.home), "APPDATA": str(f.app), "LOCALAPPDATA": str(f.loc),
                            "PROGRAMDATA": str(f.pd)})
    yield f
    envpaths.set_overrides(None)


def z(f):
    return f.app / "FakeZephyr"


def names(snap):
    return [k.rsplit("\\", 1)[-1] for k in snap.files]


# ---------------------------------------------------------------- K1
def test_k1_work_dir_inside_default_root_is_excluded(fake):
    fake.work = z(fake) / "_rec"  # 작업 폴더가 훑는 폴더 안에 있다
    ini = fake.put(z(fake) / "s.ini", "1")
    s, _ = fake.start()
    fake.snap(s)  # baseline: 이제 작업 폴더에 snap-*.json, record-state.json 이 있다
    fake.put(ini, "2")
    fake.snap(s)
    for label in ("before", "baseline", "after1"):
        assert names(s.load_snap(label)) == ["s.ini"]
    a = rm.diff(s)
    assert [c.path.rsplit("\\", 1)[-1] for c in a.candidates] == ["s.ini"]


def test_k1_work_dir_inside_custom_root_and_as_root(fake):
    fake.work = z(fake) / "_rec"
    ini = fake.put(z(fake) / "s.ini", "1")
    s, _ = fake.start(roots=[str(z(fake))])
    fake.snap(s)
    fake.put(ini, "2")
    fake.snap(s)
    assert all(names(s.load_snap(x)) == ["s.ini"] for x in ("before", "baseline", "after1"))
    assert [c.path.rsplit("\\", 1)[-1] for c in rm.diff(s).candidates] == ["s.ini"]
    # 작업 폴더 자체를 루트로 줘도 자기 기록 파일을 훑지 않는다
    w2 = fake.work.parent / "_rec2"
    fake.work = w2
    w2.mkdir()
    s2, r2 = fake.start(roots=[str(w2)])
    assert r2.files == 0 and any("작업 폴더" in w for w in r2.warnings + s2.load_snap("before").notes)


# ---------------------------------------------------------------- K2
def _two_rounds(fake, s, files):
    fake.snap(s)
    for v in ("2", "3"):
        for f in files:
            fake.put(f, v)
        fake.snap(s)


def test_k2_root_itself_as_roots_never_groups_into_dir(fake):
    a1, a2 = fake.put(fake.app / "a.ini", "1"), fake.put(fake.app / "b.ini", "1")
    s, _ = fake.start(roots=[str(fake.app)])
    _two_rounds(fake, s, [a1, a2])
    rule = rm.write_draft(s)[1]
    assert all(p["kind"] == "file" for p in rule["paths"])
    assert {p["path"] for p in rule["paths"]} == {"%APPDATA%\\a.ini", "%APPDATA%\\b.ini"}  # 표준 토큰으로 일반화
    assert USER not in json.dumps(rule)


def test_k2_app_folder_and_parent_chain_as_roots(fake):
    f1, f2 = fake.put(z(fake) / "a.ini", "1"), fake.put(z(fake) / "b.ini", "1")
    s, _ = fake.start(roots=[str(z(fake))])  # 앱 폴더 자체가 루트: 그 폴더 통째로 담지 않는다
    _two_rounds(fake, s, [f1, f2])
    rule = rm.write_draft(s)[1]
    assert [p["kind"] for p in rule["paths"]] == ["file", "file"]
    assert all(p["path"].startswith("%APPDATA%\\FakeZephyr\\") for p in rule["paths"])
    # %USERPROFILE%\AppData 를 루트로: Roaming/Local 폴더 자체도 루트 폴더로 취급하고 %APPDATA% 로 일반화
    f3 = fake.put(fake.app / "x.ini", "1")
    f4 = fake.put(fake.app / "y.ini", "1")
    fake.work = fake.work.parent / "work_b"
    s2, _ = fake.start(roots=[str(fake.home / "AppData")])
    _two_rounds(fake, s2, [f3, f4])
    rule2 = rm.write_draft(s2)[1]
    assert all(p["kind"] == "file" and p["path"].startswith("%APPDATA%\\") for p in rule2["paths"])
    assert not any("%USERPROFILE%\\AppData" in p["path"] for p in rule2["paths"])


def test_k2_dest_tokens_are_consistent():
    used = set()
    assert rm._dest("x", "%APPDATA%\\A\\b.ini", used) == "x/appdata/A/b.ini"
    assert rm._normalize_tok("%USERPROFILE%\\AppData\\Local\\X") == "%LOCALAPPDATA%\\X"
    assert rm._normalize_tok("%USERPROFILE%\\AppData\\LocalLow\\X") == "%USERPROFILE%\\AppData\\LocalLow\\X"


# ---------------------------------------------------------------- K3
def test_k3_folder_total_is_recursive(fake, monkeypatch):
    monkeypatch.setattr(rm, "BIG_FOLDER", 1000)
    d = z(fake)
    f1, f2 = fake.put(d / "a.ini", "1"), fake.put(d / "b.ini", "1")
    fake.put(d / "sub" / "data.bin", "x" * 2000)  # 직접 자식이 아닌 하위 폴더의 큰 파일
    s, _ = fake.start()
    _two_rounds(fake, s, [f1, f2])
    rule = rm.write_draft(s)[1]
    assert [p["kind"] for p in rule["paths"]] == ["file", "file"]


def test_k3_nested_dirs_keep_only_parent(fake):
    d = z(fake)
    top = [fake.put(d / "a.ini", "1"), fake.put(d / "b.ini", "1")]
    sub = [fake.put(d / "S" / "c.ini", "1"), fake.put(d / "S" / "d.ini", "1")]
    s, _ = fake.start()
    _two_rounds(fake, s, top + sub)
    rule = rm.write_draft(s)[1]
    assert [(p["path"], p["kind"]) for p in rule["paths"]] == [("%APPDATA%\\FakeZephyr", "dir")]


# ---------------------------------------------------------------- K4
def test_k4_baseline_created_config_is_promoted(fake):
    fake.put(z(fake) / "keep.txt", "k")
    s, _ = fake.start()
    cfg = fake.put(z(fake) / "settings.ini", "a=1")  # 처음 실행 때 만들어진 설정(기준선에서 추가됨)
    dat = fake.put(z(fake) / "state.dat", "1")
    fake.snap(s)
    fake.put(cfg, "a=2")
    fake.put(dat, "2")
    fake.snap(s)
    a = rm.diff(s)
    by = {c.path.rsplit("\\", 1)[-1]: c for c in a.candidates}
    assert by["settings.ini"].promoted and not by["settings.ini"].masked and by["settings.ini"].confidence == rm.MID
    assert "기준선에서 처음 생성" in by["settings.ini"].reason
    assert by["state.dat"].confidence == rm.LOW and not a.registry_likely


def test_k4_only_baseline_changed_is_not_registry_claim(fake):
    ini = fake.put(z(fake) / "s.ini", "1")
    s, _ = fake.start()
    fake.put(ini, "2")
    fake.snap(s)  # 기준선에서 이미 있던 설정 파일이 바뀜
    fake.put(ini, "3")
    fake.snap(s)
    a = rm.diff(s)
    assert a.undetermined and not a.registry_likely
    text = "\n".join(a.warnings)
    assert "레지스트리 저장으로 단정" not in text and rm.REGISTRY_MSG not in a.warnings and rm.BASELINE_ONLY_MSG in a.warnings
    assert "기준선" in rm.no_draft_message(a) and a.candidates[0].masked


def test_k4_truncated_snapshot_does_not_claim_registry(fake):
    for i in range(5):
        fake.put(z(fake) / f"f{i}.ini", "1")
    s, r = fake.start(max_files=2)
    fake.snap(s)
    fake.snap(s)
    a = rm.diff(s)
    assert a.truncated and a.undetermined and not a.registry_likely
    assert rm.TRUNCATED_MSG in a.warnings and rm.REGISTRY_MSG not in a.warnings
    assert "잘려서" in rm.no_draft_message(a)


# ---------------------------------------------------------------- K5
def test_k5_error_log_has_no_user_name(fake):
    locked = fake.put(z(fake) / "locked.ini", "1")

    def hash_fn(path, cancel=None):
        raise PermissionError(13, "denied", "\\\\?\\" + str(locked))  # 메시지에 긴 경로 접두 + 사용자 이름 경로

    s, r = fake.start(hash_fn=hash_fn)
    assert r.unreadable == 1
    logs = [p.read_text("utf-8", errors="replace") for p in errorlog.log_dir().glob("errors-*.log")]
    assert logs and USER not in "".join(logs)


def test_k5_tokenize_home_handles_escaped_and_prefixed_forms(fake):
    h = str(fake.home)
    for text in (h + "\\x", "\\\\?\\" + h + "\\x", repr("\\\\?\\" + h + "\\x"), repr(h + "\\x"), h.replace("\\", "/") + "/x"):
        assert USER not in errorlog.tokenize_home(text), text


# ---------------------------------------------------------------- K6 / K7 / K8
def test_k6_roots_outside_home_gives_friendly_error(fake, capsys):
    ext = fake.work.parent / "ext" / "Prog"
    f = fake.put(ext / "a.ini", "1")
    w = ["--work", str(fake.work)]
    assert cli.main(["record", "start", "--program", "Prog", "--roots", str(ext), *w]) == 0
    assert cli.main(["record", "snapshot", *w]) == 0
    fake.put(f, "2")
    assert cli.main(["record", "snapshot", *w]) == 0
    capsys.readouterr()
    assert cli.main(["record", "rule-draft", *w]) == 1
    err = capsys.readouterr().err
    assert "%환경변수%" in err and "RuleError" not in err and "예상하지 못한" not in err
    assert json.loads((fake.work / rm.REPORT_FILE).read_text("utf-8"))["status"] == "failed"
    assert not (fake.work / rm.DRAFT_FILE).exists()


def test_k7_output_overwrite_makes_bak_and_warns_on_id_clash(fake, tmp_path):
    ini = fake.put(z(fake) / "s.ini", "1")
    s, _ = fake.start()
    fake.snap(s)
    fake.put(ini, "2")
    fake.snap(s)
    out = tmp_path / "rules" / "mine.json"
    out.parent.mkdir()
    out.write_text('{"old": true}', encoding="utf-8")
    (out.parent / "other.json").write_text(json.dumps({"id": "fake-zephyr"}), encoding="utf-8")
    a, rule, path = rm.write_draft(s, output=str(out))
    assert json.loads((out.parent / "mine.json.bak").read_text("utf-8")) == {"old": True}
    assert json.loads(out.read_text("utf-8"))["id"] == "fake-zephyr"
    text = "\n".join(a.warnings)
    assert "other.json" in text and "mine.json.bak" in text
    a2, _r, _p = rm.write_draft(s, output=str(out))  # 한 번 더: 번호 .bak 이 쌓이고 기존 .bak 은 보존
    assert (out.parent / "mine.json.bak").exists() and (out.parent / "mine.json.bak.1").exists()


def test_k8_diff_json_stdout_is_pure_json(fake, capsys):
    ini = fake.put(z(fake) / "s.ini", "1")
    w = ["--work", str(fake.work)]
    cli.main(["record", "start", "--program", "Fake Zephyr", *w])
    cli.main(["record", "snapshot", *w])
    fake.put(ini, "2")
    cli.main(["record", "snapshot", *w])
    capsys.readouterr()
    assert cli.main(["record", "diff", *w, "--json"]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["candidates"] and "후보 저장" in cap.err


# ---------------------------------------------------------------- K9 / K10 / K11
def test_k9_vendor_word_does_not_pull_other_programs(fake):
    fake.put(fake.app / "Adobe" / "Adobe Photoshop 2024" / "a.ini", "1")
    fake.put(fake.app / "Adobe" / "Adobe Illustrator 2024" / "b.ini", "1")
    fake.put(fake.app / "Adobe" / "Common" / "c.ini", "1")
    fake.put(fake.app / "Adobe Reader" / "d.ini", "1")
    s, r = fake.start("Adobe Photoshop")
    assert sorted(names(s.load_snap("before"))) == ["a.ini"]
    assert r.matched == ["%APPDATA%\\Adobe\\Adobe Photoshop 2024"]
    assert rs.match_words("Adobe Photoshop") == ["photoshop", "adobephotoshop"]
    assert rs.match_words("Adobe") == ["adobe"]  # 제조사 낱말뿐이면 그대로(사용자가 원한 것)
    assert "adobe" in rs.match_words("Foo", ["Adobe"])  # --match 로 직접 준 낱말은 그대로


def test_k10_custom_root_keeps_packages_but_skips_temp(fake):
    base = fake.app / "Vendor"
    fake.put(base / "Packages" / "p.ini", "1")
    fake.put(base / "Temp" / "t.ini", "1")
    fake.put(base / "GPUCache" / "g.bin", "1")
    fake.put(base / "keep.ini", "1")
    s, _ = fake.start(roots=[str(base)])
    assert sorted(names(s.load_snap("before"))) == ["keep.ini", "p.ini"]
    fake.put(fake.app / "Packages" / "top.ini", "1")
    s2, _ = rm.start("X", str(fake.work.parent / "w2"), all_roots=True, documents_fn=lambda: str(fake.docs))
    assert "top.ini" not in names(s2.load_snap("before"))  # 기본 루트에서는 기존대로 루트 바로 아래 Packages 를 건너뜀
    s3, _ = rm.start("X", str(fake.work.parent / "w3"), roots=[str(fake.app)])
    assert "top.ini" in names(s3.load_snap("before"))  # 루트를 직접 지정하면 포함


def test_k11_missing_roots_is_an_error(fake, capsys):
    with pytest.raises(rm.RecordError):
        fake.start(roots=[str(fake.app / "nope")])
    assert not (fake.work / rm.STATE_FILE).exists()
    rc = cli.main(["record", "start", "--program", "P", "--roots", str(fake.app / "nope"), "--work", str(fake.work)])
    assert rc == 1 and "찾지 못했습니다" in capsys.readouterr().err


# ---------------------------------------------------------------- K12 / K13 / K14
def test_k12_unreadable_not_summed_across_snapshots(fake):
    fake.put(z(fake) / "locked.ini", "1")
    ini = fake.put(z(fake) / "s.ini", "1")

    def hf(path, cancel=None):
        if path.endswith("locked.ini"):
            raise PermissionError(13, "denied", path)
        return rs.sha256_file(path, cancel)
    s, _ = fake.start(hash_fn=hf)
    fake.snap(s, hash_fn=hf)
    fake.put(ini, "2")
    fake.snap(s, hash_fn=hf)
    assert rm.diff(s).unreadable == 1


def test_k13_removed_files_are_reference_only(fake):
    d = z(fake)
    gone = fake.put(d / "gone.ini", "1")
    fake.put(d / "keep.ini", "1")
    s, _ = fake.start()
    fake.snap(s)
    gone.unlink()
    fake.snap(s)
    a = rm.diff(s)
    c = a.candidates[0]
    assert c.change == "삭제" and c.removed and "참고로만" in c.reason
    a2, rule, out = rm.write_draft(s)
    assert rule is None and "삭제" in rm.no_draft_message(a2)
    # 다른 파일이 바뀌면 그것만 담긴다
    keep = d / "keep.ini"
    fake.put(keep, "2")
    fake.snap(s, "after2")
    rule = rm.write_draft(s)[1]
    assert [p["path"] for p in rule["paths"]] == ["%APPDATA%\\FakeZephyr\\keep.ini"]


def test_k14_force_archives_previous_session_files(fake):
    ini = fake.put(z(fake) / "s.ini", "1")
    s, _ = fake.start()
    fake.snap(s)
    fake.put(ini, "2")
    fake.snap(s)
    rm.write_draft(s)
    s2, _ = fake.start(force=True)
    left = sorted(p.name for p in fake.work.iterdir())
    assert "snap-baseline.json" not in left and "snap-after1.json" not in left
    assert "snap-baseline.json.bak" in left and "snap-after1.json.bak" in left and "rule-draft.json.bak" in left
    assert "record-candidates.json" not in left and "record-candidates.json.bak" in left
    assert [i["label"] for i in s2.snaps] == ["before"]
    fake.snap(s2)
    fake.snap(s2)
    assert rm.diff(s2).registry_likely  # 옛 세션 산출물이 섞이지 않는다
