"""'찾을 파일 형식' 설정(find_exts) 시험: 정규화, 추가/삭제/초기화, 차이만 저장, 손상·상한, 경고, 원자 저장, 개인정보 없음.

모든 시험은 임시 폴더(directory 주입)만 쓴다. conftest 가 SETTINGS_DIR 도 임시 폴더로 바꿔 둔다.
"""
import json
import os
from pathlib import Path

import pytest

from pcmigrate import errorlog, find_data as fd, find_exts as fx


@pytest.fixture
def d(tmp_path):
    return tmp_path / "settings"


def saved(d):
    return json.loads((d / fx.FILE_NAME).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ normalize
@pytest.mark.parametrize("raw,want", [("zep", ".zep"), (" .ZEP ", ".zep"), (".Dwg", ".dwg"), ("a_b-c", ".a_b-c"), ("3df", ".3df"),
                                      ("." + "a" * 15, "." + "a" * 15)])
def test_normalize_ok(raw, want):
    assert fx.normalize_ext(raw) == want


@pytest.mark.parametrize("raw", ["", "   ", ".", "...", "a b", ". zep", "a/b", "a\\b", "C:\\x.dwg", "*.dwg", ".dw?", ".tar.gz", "한글",
                                 ".a" + "b" * 15, ".zep!", ".z$p", None, 5])
def test_normalize_rejects(raw):
    with pytest.raises(ValueError) as e:
        fx.normalize_ext(raw)
    assert str(e.value) and "Traceback" not in str(e.value)  # 쉬운 문구


def test_normalize_category():
    assert fx.normalize_category("Design") == "design" and fx.normalize_category("설계") == "design"
    assert fx.normalize_category(" 압축 ") == "archive"
    for bad in ("other", "", "기타 큰 파일", None):
        with pytest.raises(ValueError):
            fx.normalize_category(bad)


# ------------------------------------------------------------------ 기본값 / 유효 표
def test_defaults_when_no_file(d):
    cfg = fx.load(d)
    assert cfg.is_default and not cfg.notes and not cfg.damaged
    assert fx.effective_table(cfg) == fd.EXT_TABLE and fx.effective_table() == fd.EXT_TABLE
    assert fx.effective_table(cfg) is not fd.EXT_TABLE  # 복사본
    assert not d.exists()  # 읽기만으로는 아무것도 만들지 않는다


def test_effective_table_added_removed_grade_b():
    cfg = fx.ExtConfig(added={".zep": "design", ".kdc": "doc"}, removed=[".svg"])
    t = fx.effective_table(cfg)
    assert t[".zep"] == ("design", "B") and t[".kdc"] == ("doc", "B") and ".svg" not in t
    assert t[".dwg"] == fd.EXT_TABLE[".dwg"] and len(t) == len(fd.EXT_TABLE) + 2 - 1
    assert fx.user_exts(cfg) == {".zep", ".kdc"}
    assert fd.EXT_TABLE[".svg"] == ("photo", "C")  # 기본 표는 그대로


def test_removed_only_applies_to_default_exts():
    cfg = fx.ExtConfig(removed=[".nothere"])
    assert fx.effective_table(cfg) == fd.EXT_TABLE


# ------------------------------------------------------------------ add / remove
def test_add_new_and_duplicate():
    cfg = fx.ExtConfig()
    ch = fx.add(cfg, "ZEP", "design")
    assert ch.changed and ch.cfg.added == {".zep": "design"} and cfg.added == {}  # 원본 cfg 불변
    dup = fx.add(ch.cfg, ".zep", "design")
    assert not dup.changed and "이미" in dup.message
    dflt = fx.add(cfg, ".dwg", "design")
    assert not dflt.changed and "이미" in dflt.message and "기본" in dflt.message
    other = fx.add(cfg, ".dwg", "photo")
    assert not other.changed and "먼저 삭제" in other.message
    moved = fx.add(ch.cfg, ".zep", "doc")
    assert moved.changed and moved.cfg.added == {".zep": "doc"}
    with pytest.raises(ValueError):
        fx.add(cfg, "*.zep", "design")
    with pytest.raises(ValueError):
        fx.add(cfg, ".zep", "other")


def test_remove_default_and_user_added():
    cfg = fx.ExtConfig()
    r = fx.remove(cfg, "svg")
    assert r.changed and r.cfg.removed == [".svg"] and cfg.removed == []
    again = fx.remove(r.cfg, ".svg")
    assert not again.changed and "이미 삭제" in again.message
    back = fx.add(r.cfg, ".svg", "photo")  # 삭제한 기본 형식을 되살림: 차이가 사라진다
    assert back.changed and back.cfg.is_default
    other_cat = fx.add(r.cfg, ".svg", "design")  # 다른 분류로 다시 넣으면 사용자 추가가 된다
    assert other_cat.cfg.added == {".svg": "design"} and fx.effective_table(other_cat.cfg)[".svg"] == ("design", "B")
    u = fx.add(cfg, ".zep", "design").cfg
    rm = fx.remove(u, ".zep")
    assert rm.changed and rm.cfg.is_default  # 추가한 것을 지우면 added 에서 빠진다(removed 에 들어가지 않는다)
    none = fx.remove(cfg, ".zzz")
    assert not none.changed and "목록에 없습니다" in none.message


def test_add_warnings_do_not_block():
    for ext in (".exe", ".dll", ".msi", ".sys", ".bat", ".cmd", ".ps1", ".vbs", ".lnk"):
        ch = fx.add(fx.ExtConfig(), ext, "doc")
        assert ch.changed and ch.warnings and "프로그램 파일" in ch.warnings[0], ext
        assert ext in ch.cfg.added
    for ext in (".tmp", ".log", ".dmp", ".pdb"):  # find_data.OTHER_EXCLUDED_EXT
        assert fx.warnings_for(ext) and "프로그램 파일" not in fx.warnings_for(ext)[0]
    assert fx.warnings_for(".zep") == [] and fx.warnings_for("  ") == []
    assert all(fx.warnings_for(e) for e in fd.OTHER_EXCLUDED_EXT)


def test_entry_limit_on_add():
    cfg = fx.ExtConfig(added={f".x{i}": "doc" for i in range(fx.MAX_ENTRIES)})
    with pytest.raises(ValueError, match="너무 많"):
        fx.add(cfg, ".more", "doc")
    with pytest.raises(ValueError, match="너무 많"):
        fx.remove(cfg, ".dwg")


# ------------------------------------------------------------------ 저장: 차이만, 원자, .bak
def test_save_stores_only_diff_and_roundtrips(d):
    cfg = fx.add(fx.add(fx.ExtConfig(), ".zep", "design").cfg, ".kdc", "doc").cfg
    cfg = fx.remove(cfg, ".svg").cfg
    p = fx.save(cfg, d)
    data = saved(d)
    assert data == {"version": 1, "added": {".kdc": "doc", ".zep": "design"}, "removed": [".svg"]}
    assert p == d / fx.FILE_NAME and not (d / (fx.FILE_NAME + ".tmp")).exists()
    again = fx.load(d)
    assert again.added == cfg.added and again.removed == cfg.removed and not again.notes
    assert fx.effective_table(again) == fx.effective_table(cfg)


def test_save_keeps_previous_as_single_bak(d):
    fx.save(fx.add(fx.ExtConfig(), ".aaa", "doc").cfg, d)
    first = (d / fx.FILE_NAME).read_text(encoding="utf-8")
    fx.save(fx.add(fx.ExtConfig(), ".bbb", "doc").cfg, d)
    bak = d / (fx.FILE_NAME + ".bak")
    assert bak.read_text(encoding="utf-8") == first
    fx.save(fx.add(fx.ExtConfig(), ".ccc", "doc").cfg, d)
    assert json.loads(bak.read_text(encoding="utf-8"))["added"] == {".bbb": "doc"}  # 한 벌만 유지
    assert not (d / (fx.FILE_NAME + ".bak.1")).exists()


def test_save_atomic_on_replace_failure(d, monkeypatch):
    fx.save(fx.add(fx.ExtConfig(), ".aaa", "doc").cfg, d)
    before = (d / fx.FILE_NAME).read_bytes()

    def boom(a, b):
        raise PermissionError("denied")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(fx.FindExtsError, match="저장하지 못했습니다"):
        fx.save(fx.add(fx.ExtConfig(), ".bbb", "doc").cfg, d)
    monkeypatch.undo()
    assert (d / fx.FILE_NAME).read_bytes() == before  # 원래 설정 그대로
    assert not (d / (fx.FILE_NAME + ".tmp")).exists()  # 임시 파일 정리
    assert errorlog.last_error() is not None  # 오류 로그에 기록


def test_save_failure_when_dir_is_a_file(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("x")
    with pytest.raises(fx.FindExtsError):
        fx.save(fx.ExtConfig(added={".zep": "design"}), blocker)


def test_save_rejects_invalid_cfg(d):
    with pytest.raises(ValueError):
        fx.save(fx.ExtConfig(added={"bad ext": "design"}), d)
    with pytest.raises(ValueError):
        fx.save(fx.ExtConfig(added={".zep": "other"}), d)
    assert not d.exists()


def test_saved_file_has_no_personal_info(d, tmp_path, monkeypatch):
    monkeypatch.setenv("USERNAME", "홍길동")
    cfg = fx.add(fx.ExtConfig(), ".zep", "design").cfg
    fx.save(cfg, d)
    text = (d / fx.FILE_NAME).read_text(encoding="utf-8")
    assert set(json.loads(text)) == {"version", "added", "removed"}
    for needle in ("홍길동", str(tmp_path), "Users", "\\\\", ":/"):
        assert needle not in text


# ------------------------------------------------------------------ reset
def test_reset_backs_up_numbered_and_reports(d):
    first = fx.reset(d)
    assert first.ok and not first.changed and first.message.startswith("이미 기본값")
    fx.save(fx.add(fx.ExtConfig(), ".aaa", "doc").cfg, d)
    r1 = fx.reset(d)
    assert r1.ok and r1.changed and r1.backup and not (d / fx.FILE_NAME).exists()
    assert fx.load(d).is_default
    fx.save(fx.add(fx.ExtConfig(), ".bbb", "doc").cfg, d)
    r2 = fx.reset(d)
    assert r1.backup != r2.backup
    baks = sorted(p.name for p in d.glob(fx.FILE_NAME + ".bak*"))
    assert baks == [fx.FILE_NAME + ".bak", fx.FILE_NAME + ".bak.1"]
    assert json.loads((d / baks[0]).read_text(encoding="utf-8"))["added"] == {".aaa": "doc"}  # 이전 설정이 지워지지 않았다
    assert "보관" in r2.message


def test_reset_failure_reported_and_logged(d, monkeypatch):
    fx.save(fx.add(fx.ExtConfig(), ".aaa", "doc").cfg, d)

    def boom(a, b):
        raise PermissionError("denied")
    monkeypatch.setattr(os, "replace", boom)
    r = fx.reset(d)
    monkeypatch.undo()
    assert not r.ok and "되돌리지 못했습니다" in r.message and (d / fx.FILE_NAME).exists()
    assert errorlog.last_error() is not None


# ------------------------------------------------------------------ 손상 / 상한 / 잘못된 항목
def test_corrupt_json_falls_back_keeps_original_and_bak(d):
    d.mkdir()
    bad = b"{not json"
    (d / fx.FILE_NAME).write_bytes(bad)
    cfg = fx.load(d)
    assert cfg.damaged and cfg.is_default and any("기본값으로" in n for n in cfg.notes)
    assert fx.effective_table(cfg) == fd.EXT_TABLE
    assert (d / fx.FILE_NAME).read_bytes() == bad  # 원본은 건드리지 않는다
    bak = d / (fx.FILE_NAME + ".bak")
    assert bak.read_bytes() == bad
    fx.load(d)
    fx.load(d)
    assert [p.name for p in d.glob(fx.FILE_NAME + ".bak*")] == [bak.name]  # 같은 내용은 다시 보관하지 않는다
    assert errorlog.last_error() is not None


@pytest.mark.parametrize("content", ['[]', '{"version": 2}', '{"added": {}}', '"x"', '\x00\x01', ''])
def test_wrong_shape_or_version_is_damaged(d, content):
    d.mkdir()
    (d / fx.FILE_NAME).write_text(content, encoding="utf-8")
    cfg = fx.load(d)
    assert cfg.damaged and cfg.is_default


def test_binary_garbage_and_bom(d):
    d.mkdir()
    (d / fx.FILE_NAME).write_bytes(b"\xff\xfe\x00bad")
    assert fx.load(d).damaged
    (d / fx.FILE_NAME).write_bytes(b"\xef\xbb\xbf" + json.dumps({"version": 1, "added": {".zep": "design"}, "removed": []}).encode())
    cfg = fx.load(d)
    assert not cfg.damaged and cfg.added == {".zep": "design"}  # 메모장이 BOM 을 붙여도 읽는다


def test_oversized_file_ignored(d):
    d.mkdir()
    (d / fx.FILE_NAME).write_bytes(b" " * (fx.MAX_BYTES + 1))
    cfg = fx.load(d)
    assert cfg.damaged and cfg.is_default and any("너무 큽니다" in n for n in cfg.notes)


def test_entry_cap_and_invalid_entries_ignored_with_warning(d):
    d.mkdir()
    added = {f".ok{i}": "doc" for i in range(fx.MAX_ENTRIES + 20)}
    (d / fx.FILE_NAME).write_text(json.dumps({"version": 1, "added": added, "removed": []}), encoding="utf-8")
    cfg = fx.load(d)
    assert len(cfg.added) == fx.MAX_ENTRIES and any("너무 많아" in n for n in cfg.notes) and not cfg.damaged

    bad = {"version": 1, "added": {"a b": "design", ".good": "design", ".x": "nope", ".dwg": "design", "../etc": "doc", ".fine": 5},
           "removed": [".svg", ".notdefault", 7, "x y", ".svg"]}
    (d / fx.FILE_NAME).write_text(json.dumps(bad), encoding="utf-8")
    cfg = fx.load(d)
    assert cfg.added == {".good": "design"} and cfg.removed == [".svg"]
    assert any("잘못된 항목" in n for n in cfg.notes)
    assert fx.effective_table(cfg)[".good"] == ("design", "B") and ".svg" not in fx.effective_table(cfg)


def test_wrong_container_types(d):
    d.mkdir()
    (d / fx.FILE_NAME).write_text(json.dumps({"version": 1, "added": [1], "removed": {"a": 1}}), encoding="utf-8")
    cfg = fx.load(d)
    assert cfg.is_default and not cfg.damaged and cfg.notes


def test_unreadable_path_is_not_fatal(d):
    d.mkdir()
    (d / fx.FILE_NAME).mkdir()  # 파일 자리에 폴더: 읽을 수 없다
    cfg = fx.load(d)
    assert cfg.damaged and fx.effective_table(cfg) == fd.EXT_TABLE


# ------------------------------------------------------------------ describe
def test_describe_structure():
    cfg = fx.ExtConfig(added={".zep": "design"}, removed=[".svg"])
    desc = fx.describe(cfg)
    assert [c["category"] for c in desc["categories"]] == ["design", "photo", "video", "doc", "archive"]
    design = desc["categories"][0]
    assert design["label"] == "설계" and {"ext": ".zep", "grade": "B", "origin": "user"} in design["exts"]
    assert design["exts"][-1]["ext"] == ".zep" and design["exts"][0]["origin"] == "default"
    photo = desc["categories"][1]
    assert ".svg" not in [e["ext"] for e in photo["exts"]]
    assert desc["removed"] == [{"ext": ".svg", "category": "photo", "label": "사진"}]
    assert desc["added_count"] == 1 and desc["removed_count"] == 1 and desc["total"] == len(fd.EXT_TABLE) and not desc["is_default"]
    assert sum(c["count"] for c in desc["categories"]) == desc["total"]
    json.dumps(desc, ensure_ascii=False)  # 화면/JSON 으로 그대로 내보낼 수 있다
    assert fx.describe(fx.ExtConfig())["is_default"] is True


def test_module_size_limit():
    src = Path(fx.__file__).read_text(encoding="utf-8")
    assert len(src.splitlines()) <= 800
