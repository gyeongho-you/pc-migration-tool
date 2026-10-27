"""stage31: 설치 폴더 설정(stage25)을 v0.2.0 위로 옮긴 뒤의 상호작용(프로그램별 선택, 되돌리기, 허용 방식, 이어받기). 가짜 루트만 쓴다."""
import os

import pytest

from pcmigrate import import_record, install_dir as idr, settings_filter
from pcmigrate.api import ApiError, run_import, run_undo_import
from pcmigrate.package import NOT_SELECTED, program_matches, retry_failed_export, verify_package
from conftest import make_rules_dir
from test_stage25_install_dir import deps_for, export, fake, item, pkg_for  # noqa: F401


@pytest.fixture
def writable(monkeypatch):
    monkeypatch.setattr(idr, "dir_writable", lambda d, probe=False, expand=None: True)


def _imp(fake, tmp_path, pkg, **kw):
    return run_import(pkg, make_rules_dir(tmp_path / "rules"), deps=deps_for(fake), record_dir=tmp_path / "rec", **kw)


# (a) 프로그램별 선택
def test_install_key_matching():
    assert program_matches("install", "install:Acme Paint") and program_matches("INSTALL", "install:acme paint")
    assert program_matches("install:Acme Paint", "install:Acme Paint") and not program_matches("install:Other", "install:Acme Paint")
    assert not program_matches("revit", "install:Acme Paint")


def test_skip_install_program_writes_nothing_and_is_not_selected(fake, tmp_path, writable):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/new.ini", "Acme Paint/settings.ini"], data=b"NEW")
    r = _imp(fake, tmp_path, pkg, yes=True, allow_install_dir=True, skip_programs="install")
    assert r.mode == "applied"
    assert {it["status"] for it in r.plan.items} == {NOT_SELECTED}
    assert not (fake.app / "new.ini").exists() and (fake.app / "settings.ini").read_bytes() == b"[a]\nx=1\n"
    row = [p for p in r.plan.programs if p["kind"] == "install"]
    assert row and row[0]["id"] == "install:Acme Paint" and row[0]["selected"] is False


def test_only_install_program_by_name_and_unknown_name(fake, tmp_path, writable):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/new.ini"], data=b"NEW")
    with pytest.raises(ApiError):
        _imp(fake, tmp_path, pkg, dry_run=True, allow_install_dir=True, only_programs="install:Nope")
    r = _imp(fake, tmp_path, pkg, yes=True, allow_install_dir=True, only_programs="install")
    assert (fake.app / "new.ini").read_bytes() == b"NEW"
    assert r.plan.programs[0]["name"].startswith("설치 폴더 설정")


# (b) 되돌리기
def test_undo_import_reverts_install_dir_restore(fake, tmp_path, writable):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/settings.ini", "Acme Paint/new.ini"], data=b"NEW")
    r = _imp(fake, tmp_path, pkg, yes=True, allow_install_dir=True)
    assert r.record_path
    rec = import_record.load_record(r.record_path)
    assert rec["entries"] and all(e["install"] and e["program"] == "install:Acme Paint" for e in rec["entries"])
    u = run_undo_import("install", record=r.record_path, yes=True, record_dir=tmp_path / "rec")
    assert u.mode == "applied"
    assert (fake.app / "settings.ini").read_bytes() == b"[a]\nx=1\n"  # .bak 복구
    assert (fake.app / "new.ini").read_bytes() == b"NEW"  # 새 파일은 지우지 않는다


def test_undo_install_without_permission_gives_easy_admin_message(fake, tmp_path, writable, monkeypatch):
    fake.acme_tree()
    pkg = pkg_for(fake, ["Acme Paint/settings.ini"], data=b"NEW")
    r = _imp(fake, tmp_path, pkg, yes=True, allow_install_dir=True)
    real = os.replace

    def deny(a, b, *x):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(import_record.os, "replace", deny)
    u = run_undo_import("install", record=r.record_path, yes=True, record_dir=tmp_path / "rec")
    monkeypatch.setattr(import_record.os, "replace", real)
    bad = [i for i in u.plan.items if i.result == import_record.FAILED_UNDO]
    assert bad and "관리자 권한" in bad[0].detail
    assert (fake.app / "settings.ini").read_bytes() == b"NEW"  # 아무것도 망가지지 않음


# (c) 허용 방식과 확장자 허용 목록의 일관성
def test_install_extensions_are_subset_of_allowed_mode_extensions():
    assert set(idr.INSTALL_SETTINGS_EXTENSIONS) <= set(settings_filter.ALLOWED_CONFIG_EXTS)


# (e) 이어받기 지문 / 다시 담기
def test_fingerprint_with_extra_mode_and_install_items(fake):
    from pcmigrate.package import _fingerprint
    a = _fingerprint([], None, "auto", False, "all")[0]
    b = _fingerprint([], None, "auto", False, "all", [item(fake, "Acme Paint/a.ini")])[0]
    c = _fingerprint([], None, "auto", False, "config", [item(fake, "Acme Paint/a.ini")])[0]
    assert len({a, b, c}) == 3


def test_retry_failed_export_keeps_install_entries(fake):
    fake.acme_tree()
    out, m = export(fake, [item(fake, "Acme Paint/settings.ini")])
    before = out.read_bytes()
    r = retry_failed_export(out, [])
    assert r["retry"]["unchanged"] and out.read_bytes() == before
    m2, problems = verify_package(out)
    assert not problems and any(f["rule"] == "install" for f in m2["files"])
