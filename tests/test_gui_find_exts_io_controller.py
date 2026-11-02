"""stage64: '찾을 파일 형식 설정' 창의 내보내기·가져오기·'기타' 컨트롤러 시험(화면 없음).

임시 설정 폴더(conftest)와 가짜 환경변수(사용자 이름 oldpcuser)만 쓴다. 실제 파일 대화상자는 쓰지 않는다(경로를 직접 넘긴다).
"""
import json
from pathlib import Path

import pytest

from pcmigrate import find_exts as fx
from pcmigrate import find_exts_io as fio
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller
from pcmigrate.gui.controller_exts import friendly_line
from pcmigrate.gui.files_flow import DataRow, row_visible
from pcmigrate.gui.runner import Runner

from test_find_exts_io import BAD_FILES
from test_gui_controller import FakeApi

# 화면에 보이는 글에 나오면 안 되는 내부 단어
JARGON = ("JSON", "misc", "added", "removed", "format", "version", "tool_version", "{ }", "[ ]", "NaN", "Infinity", "--yes", ".bak", "dry")


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("USERNAME", "oldpcuser")
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\oldpcuser")


@pytest.fixture
def c(tmp_path):
    ctl = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    ctl.events = []
    ctl.listener = lambda kind, payload=None: ctl.events.append(kind)
    return ctl


def good(**kw):
    base = {"format": fio.FORMAT, "version": 1, "added": {".zep": "misc"}, "removed": [".svg"]}
    base.update(kw)
    return base


def wfile(tmp_path, obj, name="in.json"):
    p = tmp_path / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def assert_clean(text):
    for w in JARGON:
        assert w not in text, (w, text)


# ------------------------------------------------------------------ 거부 문구는 화면 전에 한글로
@pytest.mark.parametrize("content,needle", BAD_FILES)
def test_rejection_text_has_no_internal_words_and_keeps_settings(c, tmp_path, content, needle):
    c.exts_begin()
    c.exts_add(".keep", "doc")
    c.exts_save()
    before = fx.config_path().read_bytes()
    chk = c.exts_import_check(wfile(tmp_path, content))
    assert not chk.ok and not chk.same
    assert chk.text.startswith(T.FX_IMPORT_REJECT_HEAD) and chk.text.endswith(T.FX_IMPORT_REJECT_TAIL) and "· " in chk.text
    assert_clean(chk.text)
    assert_clean(chk.message)
    assert fx.config_path().read_bytes() == before


def test_friendly_line_samples():
    assert friendly_line("7번째 줄 added: '.x': 이유") == "7번째 줄 추가 목록: '.x': 이유"
    assert friendly_line("version: 더 새로운 버전(2)의 파일입니다.").startswith("버전:")
    assert "misc" not in friendly_line("분류 (design, photo, video, doc, archive, misc 중 하나. 기타는 misc 로 적어 주세요)")
    assert friendly_line("그대로인 문장입니다.") == "그대로인 문장입니다."


def test_missing_file_and_directory_reported(c, tmp_path):
    chk = c.exts_import_check(str(tmp_path / "nope.json"))
    assert not chk.ok and "찾을 수 없습니다" in chk.text
    assert not c.exts_import_check(str(tmp_path)).ok


# ------------------------------------------------------------------ 가져오기: 통과·같은 내용·적용
def test_check_ok_builds_confirm_text_with_numbers_and_warnings(c, tmp_path):
    c.exts_begin()
    chk = c.exts_import_check(wfile(tmp_path, good(added={".zep": "misc", ".exe": "doc"})))
    assert chk.ok and not chk.same
    assert chk.text.startswith(T.FX_IMPORT_CONFIRM_HEAD) and chk.text.endswith(T.FX_IMPORT_CONFIRM_TAIL)
    assert T.FX_IMPORT_CONFIRM_SIZE.format(a=2, r=1) in chk.text and T.FX_IMPORT_CONFIRM_DIFF.format(a=2, r=1) in chk.text
    assert T.FX_IMPORT_CONFIRM_KEEP in chk.text and "프로그램 파일" in chk.text and ".exe" in chk.text
    assert_clean(chk.text)
    assert not fx.config_path().exists()  # 검사만: 아무것도 바뀌지 않는다


def test_check_same_as_current(c, tmp_path):
    fx.save(fx.ExtConfig({".zep": "misc"}, [".svg"]))
    c.exts_begin()
    chk = c.exts_import_check(wfile(tmp_path, good()))
    assert chk.ok and chk.same and "같아" in chk.message


def test_apply_replaces_resets_edit_base_backs_up_and_notifies(c, tmp_path):
    c.exts_begin()
    c.exts_add(".old", "doc")
    c.exts_save()
    before = fx.config_path().read_text(encoding="utf-8")
    c.exts_begin()
    c.exts_add(".unsaved", "doc")  # 저장 안 한 편집은 가져오기로 사라진다
    res = c.exts_import_apply(wfile(tmp_path, good()))
    assert res.ok and res.changed and "가져왔습니다" in res.message
    assert_clean(res.message)
    assert c.exts.cfg.added == {".zep": "misc"} and c.exts.cfg.removed == [".svg"] and not c.exts_dirty()
    assert fx.load().added == {".zep": "misc"}
    assert any(p.read_text(encoding="utf-8") == before for p in fx.settings_dir().glob(fx.FILE_NAME + ".bak*"))
    assert "files_changed" in c.events and c.exts_has_misc()
    assert c.exts_describe()["categories"][-1]["category"] == "misc" and c.exts_describe()["categories"][-1]["count"] == 1


def test_apply_failure_keeps_old_settings(c, tmp_path, monkeypatch):
    import os
    c.exts_begin()
    c.exts_add(".old", "doc")
    c.exts_save()
    before = fx.config_path().read_bytes()
    path = wfile(tmp_path, good())
    c.exts_begin()
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("denied")))
        res = c.exts_import_apply(path)
    assert not res.ok and T.FX_IMPORT_FAILED in res.message and fx.config_path().read_bytes() == before
    assert c.exts.cfg.added == {".old": "doc"}  # 편집본도 그대로


def test_apply_rejected_file_changes_nothing(c, tmp_path):
    c.exts_begin()
    c.exts_add(".old", "doc")
    c.exts_save()
    before = fx.config_path().read_bytes()
    res = c.exts_import_apply(wfile(tmp_path, "{broken"))
    assert not res.ok and fx.config_path().read_bytes() == before and not any(p.name.endswith(".bak.1") for p in fx.settings_dir().glob("*"))
    assert_clean(res.message)


def test_unexpected_exception_in_io_is_reported(c, tmp_path, monkeypatch):
    logged = []
    c.record_error = lambda e, where, tb=None: logged.append(where) or None
    monkeypatch.setattr(fio, "import_from", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(fio, "export_to", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert c.exts_import_check("x").text == T.FX_UNEXPECTED and not c.exts_import_check("x").ok
    assert c.exts_import_apply("x").message == T.FX_UNEXPECTED
    assert c.exts_export("x").message == T.FX_UNEXPECTED and len(logged) >= 4


# ------------------------------------------------------------------ 내보내기
def test_export_saved_settings_not_unsaved_edits(c, tmp_path):
    c.exts_begin()
    c.exts_add(".zep", "기타")
    c.exts_save()
    c.exts_add(".unsaved", "doc")  # 저장하지 않은 편집
    out = tmp_path / "out" / "e.json"
    res = c.exts_export(str(out))
    assert res.ok and "내보냈습니다" in res.message and T.FX_EXPORT_NOTE in res.message
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["added"] == {".zep": "misc"} and data["format"] == fio.FORMAT  # 저장된 설정 기준
    text = out.read_text(encoding="utf-8")
    assert "oldpcuser" not in text and str(tmp_path) not in text
    assert_clean(res.message)


def test_export_failure_reported(c, tmp_path):
    res = c.exts_export(str(tmp_path))  # 폴더 지정
    assert not res.ok and "폴더" in res.message and T.FX_EXPORT_NOTE not in res.message


# ------------------------------------------------------------------ '기타' 칩/보기
def test_misc_chip_state_follows_saved_settings(c):
    assert c.exts_refresh_misc() is False and not c.exts_has_misc()
    c.exts_begin()
    c.exts_add(".zep", "design")
    c.exts_save()
    assert not c.exts_has_misc()  # 설계로 넣었으면 기타 칩은 필요 없다
    c.exts_add(".kdc", "기타")
    c.exts_save()
    assert c.exts_has_misc()
    c.exts_reset()
    assert not c.exts_has_misc()


def test_row_visible_rules_with_misc():
    def row(**counts):
        return DataRow(path="p", kind="", counts=counts, total_bytes=1, total_files=1, latest="", basis="", cloud_files=0, cloud_bytes=0,
                       manual=False, selected=False)
    four = {"design", "photo", "video", "doc"}
    only_misc = row(misc=1)
    assert row_visible(only_misc, four) and row_visible(only_misc, four, True)  # 전체 보기에는 나온다
    assert row_visible(only_misc, {"design"}, True) and row_visible(only_misc, set(), True)  # 기타 칩이 켜져 있으면 기타 폴더도 보인다
    assert not row_visible(only_misc, {"design"}, False) and not row_visible(only_misc, four, False)  # 기타 칩을 끄면 가려진다
    assert row_visible(row(design=1), {"design"}, False) and not row_visible(row(photo=1), {"design"})
    assert row_visible(row(), four)  # 기타 형식이 없는 기본 상태: 네 칩이 모두 켜지면 전부(이전과 같음)


def test_set_data_filter_misc_and_data_rows(c):
    from pcmigrate import find_data as fd
    item = fd.FolderItem(path="C:\\p", kind="project", drive="C:", categories={"misc": {"files": 1, "bytes": 10}}, total_files=1, total_bytes=10)
    c.files.result = type("R", (), {"projects": [item], "user_folders": []})()
    assert len(c.data_rows()) == 1
    c.set_data_filter("misc", False)
    assert c.files.misc_on is False and c.data_rows() == []
    c.set_data_filter("misc", True)
    assert len(c.data_rows()) == 1
    with pytest.raises(Exception):
        c.set_data_filter("zzz", True)


def test_texts_new_constants_polite_and_clean():
    names = [n for n in dir(T) if n.startswith(("FX_", "BTN_FX_"))]
    for n in names:
        v = getattr(T, n)
        vals = [v] if isinstance(v, str) else [tup[1] for tup in v]  # FX_CATS 는 (내부 이름, 화면 글자) 쌍: 화면 글자만 본다
        for t in vals:
            for w in ("find-exts", "--", ".bak", "JSON", "pcmigrate", "misc", "manifest"):
                assert w not in t.replace(".json", ""), (n, w)
    assert (T.BTN_FX_EXPORT, T.BTN_FX_IMPORT) == ("내보내기", "가져오기") and ("misc", "기타") in T.FX_CATS
