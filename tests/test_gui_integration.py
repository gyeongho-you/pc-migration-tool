"""컨트롤러 + 실제 api 를 가짜 환경(tmp_path, 환경변수 치환)에서 끝까지 돌린다(헤드리스)."""
from conftest import make_rules_dir
import json
import zipfile
from pathlib import Path

import pytest

from pcmigrate import api
from pcmigrate.detect import Program
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_discover import Env, touch
from test_stage4 import WINGET


def make_controller(tmp_path, registry):
    rd = make_rules_dir(tmp_path / "rules")
    deps = api.Deps(registry=lambda: registry, winget_export=lambda: WINGET, winget_list=lambda: None,
                    summarize=lambda rules, programs: (0, 0))
    c = Controller(deps=deps, runner=Runner(sync=True), rules_dir=rd)
    events = []
    c.listener = lambda k, p=None: events.append((k, p))
    return c, events


def test_old_pc_to_new_pc_full_flow(tmp_path, monkeypatch):
    old = Env(tmp_path / "old")
    touch(old.appdata / "Foo Bar" / "data.db", b"b" * 2048)
    touch(old.appdata / "Foo Bar" / "license.lic", b"LICENSE")
    touch(old.appdata / "Acme Secrets" / "credentials.json", b"{}")
    touch(old.home / ".gitconfig", b"[user]")
    old.setenv(monkeypatch)
    registry = [Program("Foo Bar Pro", "1", "Foo"), Program("Acme Secrets App", "1", "Acme"), Program("Git", "2", "")]
    c, events = make_controller(tmp_path, registry)

    c.start_scan()
    assert c.state.scan.program_count >= 3
    c.start_discover()
    rows = {r.path.split("\\")[-1]: r for r in c.candidate_rows()}
    assert {"Foo Bar", "Acme Secrets", ".gitconfig"} <= set(rows)
    assert rows["Acme Secrets"].secret and not rows["Acme Secrets"].selectable
    assert c.state.selected == set()  # 자동 선택 없음
    with pytest.raises(GuardError):
        c.set_selected(rows["Acme Secrets"].key, True)
    c.set_selected(rows["Foo Bar"].key, True)  # 사용자가 고른 것만

    out = tmp_path / "out" / "settings.zip"
    with pytest.raises(GuardError):
        c.start_export(str(out))
    c.start_export(str(out), write_confirmed=True)
    assert out.exists() and "export_done" in [k for k, _ in events]
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
    assert any(n.endswith("Foo Bar/data.db") for n in names)
    assert not any(n.endswith(".lic") for n in names)  # 라이선스 제외
    assert not any("credentials" in n or "gitconfig" in n for n in names)  # 선택하지 않은 항목은 자동 포함되지 않음
    assert manifest["extras"] == [{"root": "%APPDATA%", "rel": "Foo Bar"}]

    # ---- 신 PC
    new = Env(tmp_path / "new")
    touch(new.appdata / "Foo Bar" / "data.db", b"OLD-CONTENT")
    new.setenv(monkeypatch)
    c2, events2 = make_controller(tmp_path, [])
    c2.set_package(str(out))
    c2.start_preview()
    rows = c2.preview_rows()
    assert [r["status"] for r in rows] == [T.PREV_STATUS["would_overwrite"]] and rows[0]["extra"] is True
    assert (new.appdata / "Foo Bar" / "data.db").read_bytes() == b"OLD-CONTENT"  # 미리보기는 쓰지 않음
    with pytest.raises(GuardError):
        c2.start_apply()
    c2.set_preview_checked(True)
    c2.set_bak_understood(True)
    c2.confirm_final(True)
    assert not c2.can_apply()[0]  # 규칙 없이 선택 포함된 항목은 별도 동의 필요
    c2.set_extra_consent(True)
    c2.confirm_final(True)
    c2.start_apply()
    assert (new.appdata / "Foo Bar" / "data.db").read_bytes() == b"b" * 2048
    assert (new.appdata / "Foo Bar" / "data.db.bak").read_bytes() == b"OLD-CONTENT"
    sm = c2.import_summary()
    assert sm["counts"] == {"copied": 1} and sm["backups"] and sm["failures"] == []
    assert "import_done" in [k for k, _ in events2]


def test_transfer_flow_with_real_robocopy_or_fake(tmp_path):
    import shutil
    if shutil.which("robocopy") is None:
        pytest.skip("robocopy 없음")
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.dwg").write_bytes(b"a" * 500)
    (src / "sub" / "b.rvt").write_bytes(b"b" * 500)
    c, events = make_controller(tmp_path, [])
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False, write_confirmed=True)  # 미리보기 먼저
    c.start_transfer(dry_run=True)
    assert not (tmp_path / "dst").exists()  # 미리보기는 대상에 아무것도 쓰지 않음
    assert c.transfer_summary()["counts"] == {"would_copy": 2}
    rp = tmp_path / "rep.json"
    c.start_transfer(dry_run=False, write_confirmed=True, report_path=str(rp))
    sm = c.transfer_summary()
    assert sm["verified"] == 2 and sm["failures"] == [] and rp.exists()
    assert (tmp_path / "dst" / "sub" / "b.rvt").read_bytes() == b"b" * 500
    assert (src / "a.dwg").read_bytes() == b"a" * 500  # 원본 그대로


def test_state_screens_are_defined():
    for scr in (*S.OLD_FLOW, *S.NEW_FLOW, S.TRANSFER, S.START):
        assert scr in S.TITLES
