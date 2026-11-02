"""'찾을 파일 형식' 설정 내보내기/가져오기(find_exts_io, find-exts export/import) 시험.

임시 폴더와 가짜 환경변수(사용자 이름 oldpcuser)만 쓴다. conftest 가 설정 폴더와 오류 로그를 임시 폴더로 바꿔 둔다.
"""
import json
import os
from pathlib import Path

import pytest

from pcmigrate import cli, errorlog, find_data as fd, find_exts as fx, find_exts_io as fio

FAKE_HOME = "C:\\Users\\oldpcuser"


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("USERNAME", "oldpcuser")
    monkeypatch.setenv("USERPROFILE", FAKE_HOME)


@pytest.fixture
def d(tmp_path):
    return tmp_path / "settings"


def write(p: Path, obj) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def good(**kw):
    base = {"format": fio.FORMAT, "version": 1, "added": {".zep": "design"}, "removed": [".svg"]}
    base.update(kw)
    return base


def seed(d, ext=".aaa"):
    fx.save(fx.add(fx.ExtConfig(), ext, "doc").cfg, d)


# ------------------------------------------------------------------ 내보내기
def test_export_roundtrip_and_format(tmp_path, d):
    fx.save(fx.ExtConfig({".zep": "design"}, [".svg"]), d)
    out = tmp_path / "out" / "exts.json"
    r = fio.export_to(out, d)
    assert r.ok and out.exists() and "추가 1개, 삭제 1개" in r.message
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format"] == fio.FORMAT and data["version"] == 1 and data["added"] == {".zep": "design"} and data["removed"] == [".svg"]
    assert set(data) == {"format", "version", "tool_version", "created", "added", "removed"}
    cfg, _meta, issues = fio.validate_text(out.read_text(encoding="utf-8"))  # 내보낸 것은 가져오기 검증을 통과한다
    assert issues == [] and cfg.added == {".zep": "design"} and cfg.removed == [".svg"]
    assert not list(out.parent.glob("*.tmp"))


def test_export_default_settings_ok(tmp_path, d):
    r = fio.export_to(tmp_path / "e.json", d)
    assert r.ok and json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))["added"] == {}


def test_export_no_personal_info(tmp_path, d):
    fx.save(fx.ExtConfig({".zep": "design"}, []), d)
    out = tmp_path / "e.json"
    r = fio.export_to(out, d)
    text = out.read_text(encoding="utf-8")
    for needle in ("oldpcuser", str(tmp_path), "Users", "\\\\", ":/"):
        assert needle not in text
    assert "oldpcuser" not in json.dumps(r.__dict__, ensure_ascii=False)


def test_export_overwrite_keeps_numbered_bak(tmp_path, d):
    out = tmp_path / "e.json"
    out.write_text("mine", encoding="utf-8")
    r1 = fio.export_to(out, d)
    r2 = fio.export_to(out, d)
    assert r1.ok and r2.ok and r1.backup and r2.backup != r1.backup
    assert (tmp_path / "e.json.bak").read_text(encoding="utf-8") == "mine"
    assert json.loads((tmp_path / "e.json.bak.1").read_text(encoding="utf-8"))["format"] == fio.FORMAT


def test_export_refuses_dir_and_settings_file_and_reports_failure(tmp_path, d, monkeypatch):
    assert not fio.export_to(tmp_path, d).ok
    seed(d)
    r = fio.export_to(fx.config_path(d), d)
    assert not r.ok and "설정 파일 자체" in r.message and json.loads(fx.config_path(d).read_text(encoding="utf-8"))["version"] == 1
    out = tmp_path / "e.json"
    out.write_text("keep", encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("denied")))
        r = fio.export_to(out, d)
    assert not r.ok and "내보내지 못했습니다" in r.message and out.read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.glob("*.tmp")) and errorlog.last_error() is not None


# ------------------------------------------------------------------ 가져오기: 정상
def test_import_replace_applies_backs_up_and_reports(tmp_path, d):
    seed(d, ".aaa")
    before = fx.config_path(d).read_text(encoding="utf-8")
    f = write(tmp_path / "in.json", good())
    r = fio.import_from(f, directory=d)
    assert r.ok and r.applied and r.mode == "replace" and "가져왔습니다" in r.message
    cfg = fx.load(d)
    assert cfg.added == {".zep": "design"} and cfg.removed == [".svg"]  # 교체: .aaa 는 사라진다
    assert r.summary["file_added"] == 1 and r.summary["result_removed"] == 1 and r.summary["new_added"] == 1
    baks = [p for p in d.glob(fx.FILE_NAME + ".bak*")]
    assert any(p.read_text(encoding="utf-8") == before for p in baks) and r.backup


def test_import_merge_mode(tmp_path, d):
    seed(d, ".aaa")
    r = fio.import_from(write(tmp_path / "in.json", good()), mode="merge", directory=d)
    assert r.ok and r.applied
    cfg = fx.load(d)
    assert cfg.added == {".aaa": "doc", ".zep": "design"} and cfg.removed == [".svg"]


def test_import_dry_run_changes_nothing(tmp_path, d):
    seed(d)
    before = fx.config_path(d).read_bytes()
    r = fio.import_from(write(tmp_path / "in.json", good()), dry_run=True, directory=d)
    assert r.ok and not r.applied and r.dry_run and "미리보기" in r.message and r.cfg.added == {".zep": "design"}
    assert fx.config_path(d).read_bytes() == before and not list(d.glob("*.bak*"))


def test_import_same_as_current_is_noop(tmp_path, d):
    fx.save(fx.ExtConfig({".zep": "design"}, [".svg"]), d)
    r = fio.import_from(write(tmp_path / "in.json", good()), directory=d)
    assert r.ok and not r.applied and "같아" in r.message


def test_import_into_fresh_settings_no_backup_needed(tmp_path, d):
    r = fio.import_from(write(tmp_path / "in.json", good()), directory=d)
    assert r.ok and r.applied and r.backup == "" and fx.load(d).added == {".zep": "design"}


def test_import_allows_bom_and_optional_keys(tmp_path, d):
    f = tmp_path / "in.json"
    f.write_bytes(b"\xef\xbb\xbf" + json.dumps(good(tool_version="0.3.6", created="2026-11-02T00:00:00+00:00", note="내 메모")).encode("utf-8"))
    assert fio.import_from(f, directory=d).applied


def test_import_redefining_default_ext_in_other_category_ok(tmp_path, d):
    r = fio.import_from(write(tmp_path / "in.json", good(added={".svg": "design"}, removed=[".svg"])), directory=d)
    assert r.ok and fx.effective_table(fx.load(d))[".svg"] == ("design", "B")


def test_import_dangerous_ext_warns_but_applies(tmp_path, d):
    r = fio.import_from(write(tmp_path / "in.json", good(added={".exe": "doc", ".zep": "design"}, removed=[])), directory=d)
    assert r.ok and r.applied and any(".exe" in w and "프로그램 파일" in w for w in r.warnings) and ".zep" not in " ".join(r.warnings)


# ------------------------------------------------------------------ 가져오기: 거부(아무것도 바뀌지 않음)
def reject_case(tmp_path, d, content, needle=""):
    seed(d)
    before = fx.config_path(d).read_bytes()
    baks_before = sorted(p.name for p in d.glob("*"))
    f = write(tmp_path / "bad.json", content) if not isinstance(content, Path) else content
    r = fio.import_from(f, directory=d)
    assert not r.ok and not r.applied and r.issues and "기존 설정은 그대로" in r.message
    assert fx.config_path(d).read_bytes() == before  # 기존 설정 그대로
    assert sorted(p.name for p in d.glob("*")) == baks_before  # 보관본도 만들지 않음(아무 일도 없었다)
    if needle:
        assert any(needle in i for i in r.issues), r.issues
    return r


BAD_FILES = [
    ("{broken", "JSON"),
    ("", "JSON"),
    ("[]", "{ }"),
    ("null", "{ }"),
    ('"x"', "{ }"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {}, "removed": [], "extra": 1}', "알 수 없는 항목"),
    ('{"version": 1, "added": {}, "removed": []}', "형식 설정 파일이 아닙니다"),
    ('{"format": "other", "version": 1}', "형식 설정 파일이 아닙니다"),
    ('{"format": "pcmigrate-find-exts", "version": 2}', "더 새로운 버전"),
    ('{"format": "pcmigrate-find-exts", "version": "1"}', "숫자가 아닙니다"),
    ('{"format": "pcmigrate-find-exts", "version": true}', "숫자가 아닙니다"),
    ('{"format": "pcmigrate-find-exts"}', "숫자가 아닙니다"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": [], "removed": []}', "added"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {}, "removed": {}}', "removed"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {"zep": "design"}}', "점으로 시작"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {".ZEP": "design"}}', "소문자"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {".a b": "design"}}', "공백"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {"../x": "design"}}', "경로"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {"*.dwg": "design"}}', "와일드카드"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {".zep": "other"}}', "분류"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {".zep": 5}}', "분류"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {".zep": "design", ".zep": "doc"}}', "두 번 이상"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {".dwg": "design"}}', "이미 기본 형식"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "removed": [".notdefault"]}', "기본 형식이 아니라"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "removed": [".svg", ".svg"]}', "두 번"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "removed": [5]}', "글자가 아닙니다"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {".svg": "photo"}, "removed": [".svg"]}', "충돌"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {}, "removed": [], "note": 5}', "note"),
    ('{"format": "pcmigrate-find-exts", "version": 1, "added": {}, "removed": [], "note": "' + "x" * 300 + '"}', "note"),
    ('{"format": "pcmigrate-find-exts", "version": NaN, "added": {}, "removed": []}', "NaN"),
    ('{"format": "pcmigrate-find-exts", "version": Infinity}', "Infinity"),
]


@pytest.mark.parametrize("content,needle", BAD_FILES)
def test_import_rejects_bad_files(tmp_path, d, content, needle):
    reject_case(tmp_path, d, content, needle)


def test_reject_reports_line_number_and_reason(tmp_path, d):
    text = json.dumps(good(added={".zep": "design", ".bad one": "doc"}), ensure_ascii=False, indent=2)
    r = reject_case(tmp_path, d, text)
    line = next(i for i in text.splitlines() if ".bad one" in i)
    n = text.splitlines().index(line) + 1
    assert any(i.startswith(f"{n}번째 줄") and "공백" in i for i in r.issues), r.issues


def test_reject_too_many_entries(tmp_path, d):
    added = {f".x{i}": "doc" for i in range(fio.MAX_ENTRIES + 1)}
    r = reject_case(tmp_path, d, good(added=added, removed=[]), "항목이 너무 많습니다")
    assert len(r.issues) == 1


def test_reject_many_problems_lists_first_20_only(tmp_path, d):
    added = {f"bad{i}": "doc" for i in range(60)}
    r = reject_case(tmp_path, d, good(added=added, removed=[]))
    assert len(r.issues) == fio.MAX_ISSUES + 1 and "그 밖에 40건" in r.issues[-1]


def test_reject_oversized_binary_and_missing_and_dir(tmp_path, d):
    seed(d)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (fio.MAX_BYTES + 1))
    assert "너무 큽니다" in reject_case(tmp_path, d, big).issues[0]
    bin_ = tmp_path / "bin.json"
    bin_.write_bytes(b"\xff\xfe\x00\x01garbage")
    assert "UTF-8" in reject_case(tmp_path, d, bin_).issues[0]
    r = fio.import_from(tmp_path / "nope.json", directory=d)
    assert not r.ok and "찾을 수 없습니다" in r.issues[0]
    r = fio.import_from(tmp_path, directory=d)
    assert not r.ok and "파일이 아닙니다" in r.issues[0]


def test_reject_deep_nesting_and_huge_string(tmp_path, d):
    deep = "[" * 100000 + "]" * 100000
    r = reject_case(tmp_path, d, '{"format": "pcmigrate-find-exts", "version": 1, "added": ' + deep + "}")
    assert r.issues
    huge = '{"format": "pcmigrate-find-exts", "version": 1, "added": {"' + "a" * 500000 + '": "doc"}}'
    reject_case(tmp_path, d, huge, "너무 깁니다")


def test_reject_does_not_overwrite_damaged_or_missing_current(tmp_path, d):
    d.mkdir()
    (d / fx.FILE_NAME).write_text("{broken", encoding="utf-8")
    r = fio.import_from(write(tmp_path / "bad.json", "{x"), directory=d)
    assert not r.ok and (d / fx.FILE_NAME).read_text(encoding="utf-8") == "{broken"


def test_import_own_settings_file_is_rejected_as_wrong_format(tmp_path, d):
    seed(d)
    r = fio.import_from(fx.config_path(d), directory=d)
    assert not r.ok and any("형식 설정 파일이 아닙니다" in i for i in r.issues)


def test_import_save_failure_keeps_old_settings(tmp_path, d, monkeypatch):
    seed(d)
    before = fx.config_path(d).read_bytes()
    f = write(tmp_path / "in.json", good())
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("denied")))
        r = fio.import_from(f, directory=d)
    assert not r.ok and not r.applied and "바뀌지 않았습니다" in r.message and fx.config_path(d).read_bytes() == before
    assert errorlog.last_error() is not None


def test_rejection_is_logged(tmp_path, d):
    reject_case(tmp_path, d, "{broken")
    assert errorlog.last_error() is not None


def test_bad_mode_and_unexpected_exception_reported(tmp_path, d, monkeypatch):
    r = fio.import_from(write(tmp_path / "in.json", good()), mode="zzz", directory=d)
    assert not r.ok
    monkeypatch.setattr(fio, "_import", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = fio.import_from(write(tmp_path / "in.json", good()), directory=d)
    assert not r.ok and "기존 설정은 그대로" in r.message and not r.applied


def test_merge_over_limit_rejected(tmp_path, d):
    fx.save(fx.ExtConfig({f".y{i}": "doc" for i in range(fx.MAX_ENTRIES - 1)}, []), d)
    r = fio.import_from(write(tmp_path / "in.json", good(added={".a1": "doc", ".a2": "doc"}, removed=[])), mode="merge", directory=d)
    assert not r.ok and "넘습니다" in r.issues[0] and len(fx.load(d).added) == fx.MAX_ENTRIES - 1


# ------------------------------------------------------------------ 적용 후 찾기에 반영
def test_imported_settings_reach_find(tmp_path, d):
    from pcmigrate import data_api
    root = tmp_path / "pc" / "Drone"
    root.mkdir(parents=True)
    (root / "m.zep").write_bytes(b"x")
    cfg = fx.load(d)
    assert not data_api.run_find_data([tmp_path / "pc"], user_folders={}, exclude_paths=[], ext_config=cfg).projects
    fio.import_from(write(tmp_path / "in.json", good(removed=[])), directory=d)
    res = data_api.run_find_data([tmp_path / "pc"], user_folders={}, exclude_paths=[], ext_config=fx.load(d))
    assert [Path(p.path).name for p in res.projects] == ["Drone"]


# ------------------------------------------------------------------ CLI
def run(capsys, *argv):
    code = cli.main(["find-exts", *argv])
    o = capsys.readouterr()
    return code, o.out, o.err


def test_cli_export_then_import_roundtrip(tmp_path, capsys):
    assert run(capsys, "add", ".zep", "--category", "design")[0] == 0
    assert run(capsys, "remove", ".svg")[0] == 0
    out = tmp_path / "exts.json"
    code, so, _ = run(capsys, "export", str(out))
    assert code == 0 and "내보냈습니다" in so and out.exists()
    assert run(capsys, "reset")[0] == 0
    code, so, _ = run(capsys, "import", str(out), "--dry-run")
    assert code == 0 and "미리보기" in so and fx.load().is_default
    code, so, _ = run(capsys, "import", str(out))  # 터미널이 아니면 미리보기만
    assert code == 0 and "--yes" in so and fx.load().is_default
    code, so, _ = run(capsys, "import", str(out), "--yes")
    assert code == 0 and "가져왔습니다" in so
    cfg = fx.load()
    assert cfg.added == {".zep": "design"} and cfg.removed == [".svg"]
    code, so, _ = run(capsys, "import", str(out), "--yes")  # 같은 내용
    assert code == 0 and "같아" in so


def test_cli_import_rejects_with_exit_1_and_lines(tmp_path, capsys):
    run(capsys, "add", ".keep", "--category", "doc")
    before = fx.config_path().read_bytes()
    bad = write(tmp_path / "bad.json", good(added={".ok": "design", "bad ext": "doc"}))
    code, so, err = run(capsys, "import", str(bad), "--yes")
    assert code == 1 and "[문제]" in err and "번째 줄" in err and "가져올 수 없습니다" in so
    assert fx.config_path().read_bytes() == before


def test_cli_import_json_output_and_merge(tmp_path, capsys):
    run(capsys, "add", ".keep", "--category", "doc")
    f = write(tmp_path / "in.json", good(added={".exe": "doc", ".zep": "design"}, removed=[]))
    code, so, _ = run(capsys, "import", str(f), "--merge", "--yes", "--json")
    data = json.loads(so)
    assert code == 0 and data["ok"] and data["applied"] and data["mode"] == "merge" and data["warnings"]
    assert fx.load().added == {".keep": "doc", ".exe": "doc", ".zep": "design"}
    code, so, _ = run(capsys, "import", str(f), "--json")  # --json 은 질문하지 않는다
    assert json.loads(so)["ok"]
    bad = write(tmp_path / "bad.json", "{x")
    code, so, _ = run(capsys, "import", str(bad), "--json")
    data = json.loads(so)
    assert code == 1 and not data["ok"] and data["issues"] and not data["applied"]


def test_cli_export_failure_and_help(tmp_path, capsys):
    code, so, _ = run(capsys, "export", str(tmp_path))
    assert code == 1 and "폴더" in so
    with pytest.raises(SystemExit):
        cli.main(["find-exts", "import", "--help"])
    help_text = capsys.readouterr().out
    assert "--dry-run" in help_text and "--yes" in help_text and "--merge" in help_text
    with pytest.raises(SystemExit) as e:
        cli.main(["find-exts", "import"])
    assert e.value.code == 2
    capsys.readouterr()


def test_cli_output_has_no_fake_user_name(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(fx, "SETTINGS_DIR", FAKE_HOME + "\\AppData\\Local\\pcmigrate\\settings-nonexistent")
    code, so, err = run(capsys, "export", str(tmp_path / "e.json"))
    assert code in (0, 1) and "oldpcuser" not in so + err
    assert "oldpcuser" not in (tmp_path / "e.json").read_text(encoding="utf-8") if (tmp_path / "e.json").exists() else True


def test_module_size_limit():
    for mod in (fx, fio):
        assert len(Path(mod.__file__).read_text(encoding="utf-8").splitlines()) <= 800


# ------------------------------------------------------------------ '기타'(misc)
def test_export_import_misc_roundtrip(tmp_path, d):
    fx.save(fx.ExtConfig({".zep": "misc", ".dwx": "design"}, []), d)
    out = tmp_path / "e.json"
    assert fio.export_to(out, d).ok and json.loads(out.read_text(encoding="utf-8"))["added"][".zep"] == "misc"
    d2 = tmp_path / "other-pc"
    r = fio.import_from(out, directory=d2)
    assert r.ok and r.applied and fx.load(d2).added == {".zep": "misc", ".dwx": "design"}


def test_import_old_file_without_misc_still_valid_and_other_key_rejected(tmp_path, d):
    assert fio.import_from(write(tmp_path / "old.json", good()), directory=d).applied  # 기타 없는 예전 내보내기 파일
    r = reject_case(tmp_path, d, good(added={".zep": "other"}, removed=[]), "misc 로 적어")
    assert any("분류" in i for i in r.issues)
    r2 = fio.import_from(write(tmp_path / "ok.json", good(added={".zep": "misc"}, removed=[])), mode="merge", directory=d)
    assert r2.ok and fx.load(d).added[".zep"] == "misc"
