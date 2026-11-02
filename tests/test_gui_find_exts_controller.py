"""stage61: '찾을 파일 형식 설정' 컨트롤러(controller_exts.ExtsMixin) 시험(화면 없음).

설정 폴더는 conftest 가 임시 폴더로 바꾼 find_exts.SETTINGS_DIR 만 쓴다(실제 %LOCALAPPDATA% 는 건드리지 않는다).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import data_api, errorlog, find_data as fd, find_exts as fx
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi


@pytest.fixture
def c(tmp_path):
    ctl = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    ctl.events = []
    ctl.listener = lambda kind, payload=None: ctl.events.append(kind)
    return ctl


def saved():
    return json.loads(fx.config_path().read_text(encoding="utf-8"))


def test_open_default(c):
    st = c.exts_begin()
    d = c.exts_describe()
    assert st.open_note == "" and d["is_default"] and d["total"] == len(fd.EXT_TABLE)
    assert [x["label"] for x in d["categories"]] == ["설계", "사진", "영상", "문서", "압축", "기타"]  # 기타는 비어 있어도 항상
    assert not c.exts_dirty() and c.exts_dirty_text() == T.FX_CLEAN
    assert not fx.config_path().exists()  # 열기만으로는 아무것도 쓰지 않는다


def test_open_blocked_while_busy(c):
    c.state.busy = True
    with pytest.raises(GuardError):
        c.exts_begin()


def test_open_damaged_falls_back_with_notice(c):
    d = fx.settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / fx.FILE_NAME).write_text("{broken", encoding="utf-8")
    st = c.exts_begin()
    assert st.open_note == T.FX_DAMAGED and c.exts_describe()["is_default"]
    assert (d / fx.FILE_NAME).read_text(encoding="utf-8") == "{broken"  # 손상 원본은 그대로


def test_open_with_ignored_entries_shows_backend_note(c):
    d = fx.settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / fx.FILE_NAME).write_text(json.dumps({"version": 1, "added": {"a b": "design", ".ok": "doc"}, "removed": []}), encoding="utf-8")
    st = c.exts_begin()
    assert "잘못된 항목" in st.open_note and c.exts.cfg.added == {".ok": "doc"}


def test_add_valid_duplicate_invalid_and_warning(c):
    c.exts_begin()
    r = c.exts_add("ZEP", "design")
    assert r.ok and r.changed and ".zep" in r.message and c.exts_dirty()
    assert c.exts_dirty_text() == T.FX_DIRTY.format(a=1, r=0)
    dup = c.exts_add(".zep", "design")
    assert dup.ok and not dup.changed and "이미" in dup.message
    bad = c.exts_add("*.zep", "design")
    assert not bad.ok and "와일드카드" in bad.message and c.exts.cfg.added == {".zep": "design"}
    exe = c.exts_add(".exe", "doc")
    assert exe.ok and exe.changed and exe.warnings and "프로그램 파일" in exe.warnings[0]  # 경고만, 막지 않는다
    wide = c.exts_add(".dat", "doc")
    assert wide.ok and wide.warnings == []  # 넓은 확장자는 경고하지 않는다(승인된 결정)
    assert not fx.config_path().exists()  # 저장 전에는 파일이 없다


def test_remove_restore_and_counts(c):
    c.exts_begin()
    r = c.exts_remove("svg")
    assert r.ok and r.changed and c.exts_describe()["removed"][0]["ext"] == ".svg"
    assert c.exts_dirty_text() == T.FX_DIRTY.format(a=0, r=1)
    back = c.exts_restore(".svg")
    assert back.ok and not c.exts_dirty() and c.exts_describe()["is_default"]
    c.exts_add(".zep", "design")
    c.exts_remove(".zep")
    assert not c.exts_dirty()
    assert not c.exts_remove(".zzz").changed and c.exts_remove(".zzz").ok
    assert not c.exts_remove("a/b").ok
    c.exts_remove(".svg")
    c.exts_add(".svg", "design")  # 다른 분류로 다시 넣은 것도 되돌리기가 원래 분류로 복구
    assert c.exts_restore(".svg").ok and c.exts_describe()["is_default"]


def test_save_writes_diff_only_and_clears_dirty_and_notifies(c):
    c.exts_begin()
    assert c.exts_save().closed and c.exts_save().message == T.FX_NOTHING_TO_SAVE and not fx.config_path().exists()
    c.exts_add(".zep", "design")
    c.exts_remove(".svg")
    r = c.exts_save()
    assert r.ok and r.closed and r.message == T.FX_SAVED_NOTICE
    assert saved() == {"version": 1, "added": {".zep": "design"}, "removed": [".svg"]}
    assert not c.exts_dirty() and "files_changed" in c.events
    assert c.exts_notice_lines() == [T.FX_SAVED_NOTICE]
    c.exts_clear_notice()
    assert c.exts_notice_lines() == []
    # 다시 열면 저장한 설정이 편집 시작점
    c.exts_begin()
    assert c.exts.cfg.added == {".zep": "design"} and not c.exts_dirty()


def test_save_failure_keeps_old_settings_and_logs(c, monkeypatch):
    c.exts_begin()
    c.exts_add(".aaa", "doc")
    c.exts_save()
    before = fx.config_path().read_bytes()
    c.exts_clear_notice()
    c.exts_begin()
    c.exts_add(".bbb", "doc")
    import os

    def boom(a, b):
        raise PermissionError("denied")
    with monkeypatch.context() as m:  # undo() 는 conftest 의 설정 폴더 격리까지 풀어 버리므로 쓰지 않는다
        m.setattr(os, "replace", boom)
        r = c.exts_save()
    assert not r.ok and not r.closed and r.message == T.FX_SAVE_FAILED
    assert fx.config_path().read_bytes() == before and c.exts_dirty()  # 편집본은 남아 다시 시도할 수 있다
    assert errorlog.last_error() is not None
    assert c.exts.saved_notice == ""  # 실패했으면 '저장했습니다' 안내를 남기지 않는다


def test_unexpected_exception_is_logged_and_reported(c, monkeypatch):
    logged = []
    c.record_error = lambda e, where, tb=None: logged.append((type(e).__name__, where)) or None
    c.exts_begin()
    c.exts_add(".zep", "design")
    monkeypatch.setattr(fx, "save", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = c.exts_save()
    assert not r.ok and r.message == T.FX_UNEXPECTED and logged and "저장" in logged[0][1]
    monkeypatch.setattr(fx, "reset", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert c.exts_reset().message == T.FX_UNEXPECTED


def test_reset_keeps_numbered_backups_and_always_reports(c):
    c.exts_begin()
    r0 = c.exts_reset()
    assert r0.ok and not r0.changed and "이미 기본값" in r0.message
    c.exts_add(".aaa", "doc")
    c.exts_save()
    c.exts_begin()
    c.exts_add(".unsaved", "doc")  # 저장 안 한 편집도 초기화로 사라진다
    r1 = c.exts_reset()
    assert r1.ok and r1.changed and "보관" in r1.message
    assert not fx.config_path().exists() and c.exts_describe()["is_default"] and not c.exts_dirty()
    c.exts_add(".bbb", "doc")
    c.exts_save()
    assert c.exts_reset().ok
    assert sorted(p.name for p in fx.settings_dir().glob(fx.FILE_NAME + ".bak*")) == [fx.FILE_NAME + ".bak", fx.FILE_NAME + ".bak.1"]


def test_reset_failure_reported(c, monkeypatch):
    c.exts_begin()
    c.exts_add(".aaa", "doc")
    c.exts_save()
    import os
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("denied")))
        r = c.exts_reset()
    assert not r.ok and r.message == T.FX_RESET_FAILED and fx.config_path().exists()


def test_notice_lines_include_ext_notes_from_result(c):
    res = SimpleNamespace(ext_notes=["찾을 파일 형식 설정을 적용했습니다(추가 1개, 삭제 0개)."])
    assert c.exts_notice_lines(res) == res.ext_notes
    assert c.exts_notice_lines(SimpleNamespace()) == [] and c.exts_notice_lines(None) == []


class RealFind:
    """진짜 data_api.run_find_data 를 임시 폴더 하나에만 돌린다(드라이브 전체를 훑지 않게)."""

    def __init__(self, root):
        self.root = root

    def run_find_data(self, roots=None, **kw):
        kw = {k: v for k, v in kw.items() if k in ("cancel", "progress", "progress_info", "time_limit")}
        return data_api.run_find_data([self.root], user_folders={}, exclude_paths=[], **kw)


def test_saved_settings_reach_find_and_notice(c, tmp_path):
    root = tmp_path / "pc"
    (root / "Drone" / "p1").mkdir(parents=True)
    (root / "Drone" / "p1" / "m.zep").write_bytes(b"x")
    c.data_api = RealFind(str(root))
    c.find_data_start()
    assert c.data_rows() == []  # 설정 전: 모르는 형식 1개 폴더는 후보가 아니다
    c.exts_begin()
    c.exts_add(".zep", "design")
    c.exts_save()
    c.exts_clear_notice()
    c.find_data_start()
    assert [Path(r.path).name for r in c.data_rows()] == ["p1"]
    assert any("설정을 적용했습니다" in n for n in c.exts_notice_lines(c.files.result))


def test_find_with_damaged_settings_still_works_and_warns(c, tmp_path):
    root = tmp_path / "pc"
    (root / "W").mkdir(parents=True)
    (root / "W" / "a.dwg").write_bytes(b"x")
    d = fx.settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / fx.FILE_NAME).write_text("{broken", encoding="utf-8")
    c.data_api = RealFind(str(root))
    c.find_data_start()
    assert [Path(r.path).name for r in c.data_rows()] == ["W"]
    assert any("기본값으로" in n for n in c.exts_notice_lines(c.files.result))


def test_texts_are_polite_and_have_no_command_words():
    names = [n for n in dir(T) if n.startswith(("FX_", "BTN_FX_"))]
    assert len(names) >= 25
    assert (T.BTN_FX_RESET, T.BTN_FX_SAVE, T.BTN_FX_CANCEL) == ("기본값으로 초기화", "저장", "취소")
    for n in names:
        for t in ([getattr(T, n)] if isinstance(getattr(T, n), str) else [x for tup in getattr(T, n) for x in tup]):
            for w in ("find-exts", "--category", ".bak", "pcmigrate", "JSON", "명령"):
                assert w not in t, (n, w)
