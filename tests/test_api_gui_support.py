"""9단계(GUI)를 위해 api/transfer/discover 에 추가한 기능(취소, 진행, 설명, 시스템 정보) 테스트. CLI 동작은 그대로."""
import sys
import time
from pathlib import Path

import pytest

from pcmigrate import api, transfer as tr
from pcmigrate.cancel import Cancelled, CancelToken
from pcmigrate.detect import Program
from pcmigrate.discover import SelectionError, discover, items_from_selection, load_selection
from pcmigrate.rules import load_rules
from pcmigrate.transfer import STATE_NAME, cancellable_runner, transfer

from test_discover import REPO_RULES, Env, touch
from test_stage4 import WINGET
from test_transfer import FakeRobocopy, make_src


def test_cancel_token():
    t = CancelToken()
    assert not t.is_set() and not t()
    t.raise_if_set()
    t.cancel()
    assert t.is_set() and t()
    with pytest.raises(Cancelled):
        t.raise_if_set()


def test_system_info_shape():
    info = api.system_info()
    assert info.os_name and info.python and info.is_admin in (True, False, None)


def test_explain_matches_gives_evidence():
    rules = load_rules(REPO_RULES)
    revit = next(r for r in rules if r.id == "revit")
    rhino = next(r for r in rules if r.id == "rhino")
    out = api.explain_matches([revit, rhino], [Program("Autodesk Revit 2024"), Program("Other")])
    assert out[0].programs == ["Autodesk Revit 2024"] and "이름 일치" in out[0].evidence
    assert out[1].programs == [] and "설치 경로 또는 레지스트리" in out[1].evidence  # rhino 는 이름 없이 감지된 경우


def test_run_scan_includes_match_info(tmp_path):
    d = api.Deps(registry=lambda: [Program("Autodesk Revit 2024")], winget_export=lambda: WINGET,
                 winget_list=lambda: None, summarize=lambda r, p: (0, 0))
    rd = tmp_path / "r"
    rd.mkdir()
    (rd / "demo.json").write_text('{"id":"demo","name":"Demo","detect":{"display_name_patterns":["Revit"]}}', encoding="utf-8")
    res = api.run_scan(rd, d)
    assert [m.rule.id for m in res.match_info] == ["demo"] and res.match_info[0].programs == ["Autodesk Revit 2024"]
    assert res.summary_dict()["matched_rules"][0]["id"] == "demo"  # CLI JSON 은 그대로


def test_discover_cancel(tmp_path):
    env = Env(tmp_path)
    for i in range(30):
        touch(env.appdata / f"Foo Bar {i}" / "d.db")
    calls = []

    def cancel():
        calls.append(1)
        return len(calls) > 3

    with pytest.raises(Cancelled):
        discover(load_rules(REPO_RULES), [Program("Foo Bar Pro 0", "1", "Foo")], env.expand, cancel=cancel)
    assert len(calls) == 4  # 후보 순회 중 즉시 중단
    d = discover(load_rules(REPO_RULES), [Program("Foo Bar Pro 0", "1", "Foo")], env.expand, cancel=lambda: False)
    assert d.candidate_count >= 1


def test_transfer_cancel_keeps_state_and_resumes(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    rc = FakeRobocopy()
    infos = []

    def cancel():  # 복사가 끝나고 파일 1개를 검사한 뒤에 취소
        return any(i.phase == "verify" and i.done_files >= 1 for i in infos)

    msgs = []
    with pytest.raises(Cancelled):
        transfer(src, dst, runner=rc, cancel=cancel, progress=msgs.append, progress_info=infos.append, workers=1,
                 min_report_interval=0)
    assert (dst / STATE_NAME).exists() and any("복사" in m for m in msgs)
    rep = transfer(src, dst, runner=FakeRobocopy())  # 같은 설정으로 다시 실행하면 이어받는다
    assert rep.failures == [] and rep.meta["resumed_skipped"] >= 1


def test_transfer_progress_reports_verification(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(60):
        (src / f"f{i}.txt").write_bytes(b"x" * 100)
    msgs, infos = [], []
    transfer(src, tmp_path / "dst", runner=FakeRobocopy(), progress=msgs.append, progress_info=infos.append,
             min_report_interval=0)
    assert any(m.startswith("검사 ") and "파일" in m and "%" in m for m in msgs)
    phases = {i.phase for i in infos}
    assert {"verify"} <= phases and infos[-1].phase == "verify" and infos[-1].done_files == 60
    assert infos[-1].total_bytes == 60 * 100 * 2  # 원본 1회 + 대상 1회 읽기(원본 해시 재사용 없음)


def test_run_transfer_passes_cancel_and_progress_only_when_given(tmp_path):
    seen = {}
    d = api.Deps(transfer=lambda *a, **k: seen.update(k) or "ok")
    api.run_transfer("s", "d", deps=d)
    assert "cancel" not in seen and "progress" not in seen and "runner" not in seen  # CLI 호출 형태 그대로
    tok = CancelToken()
    api.run_transfer("s", "d", deps=d, cancel=tok, progress=lambda m: None)
    assert seen["cancel"] is tok and callable(seen["progress"]) and "runner" not in seen  # 실행기는 transfer 가 만든다


def test_cancellable_runner_runs_and_cancels_real_process():
    py = sys.executable
    code, out = cancellable_runner(lambda: False)([py, "-c", "print('hello')"])
    assert code == 0 and "hello" in out
    t0 = time.time()
    with pytest.raises(Cancelled):
        cancellable_runner(lambda: time.time() - t0 > 0.4)([py, "-c", "import time; time.sleep(30)"])
    assert time.time() - t0 < 10  # 프로세스를 종료하고 빠르게 돌아옴


def test_items_from_selection_same_validation_as_file(tmp_path):
    env = Env(tmp_path)
    touch(env.appdata / "Foo Bar" / "d.db")
    items = items_from_selection({"format": 1, "candidates": [{"include": True, "root": "%APPDATA%", "rel": "Foo Bar"}]},
                                 env.expand)
    assert [(i.root, i.rel) for i in items] == [("%APPDATA%", "Foo Bar")]
    for bad in ({"root": "C:\\Windows", "rel": "x"}, {"root": "%APPDATA%", "rel": "..\\x"}, {"root": "%APPDATA%", "rel": "nope"}):
        with pytest.raises(SelectionError):
            items_from_selection({"format": 1, "candidates": [dict(bad, include=True)]}, env.expand)
    with pytest.raises(SelectionError):
        items_from_selection([], env.expand)


def test_run_export_with_extra_items_rechecks_secrets(tmp_path, monkeypatch):
    env = Env(tmp_path)
    touch(env.appdata / "Sec" / "credentials.json")
    env.setenv(monkeypatch)
    items = items_from_selection({"format": 1, "candidates": [{"include": True, "root": "%APPDATA%", "rel": "Sec"}]})
    rd = tmp_path / "rules"
    rd.mkdir()
    d = api.Deps(registry=lambda: [], winget_export=lambda: WINGET, winget_list=lambda: None,
                 summarize=lambda r, p: (0, 0))
    with pytest.raises(api.ApiError) as e:  # GUI 가 동의 없이 넘겨도 api 가 다시 막는다
        api.run_export(rd, tmp_path / "p.zip", extra_items=items, deps=d)
    assert "--allow-secrets" in e.value.message
    ok = api.run_export(rd, tmp_path / "p.zip", extra_items=items, allow_secrets=True, deps=d)
    assert ok.extras.risky == 1 and (tmp_path / "p.zip").exists()


def test_default_rules_dir_source_and_frozen(monkeypatch, tmp_path):
    from pcmigrate.rules import default_rules_dir
    assert default_rules_dir().name == "rules" and (default_rules_dir() / "revit.json").exists()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert default_rules_dir() == tmp_path / "rules"  # PyInstaller 번들 위치


def test_readme_documents_gui():
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for needle in ("python -m pcmigrate.gui", "python -m pcmigrate gui", "pcmigrate-gui.bat", "pythonw",
                   "구 PC(내보내기)", "신 PC(가져오기)", "대용량 데이터 복사", "pcmigrate-gui.log", "비밀 정보가 포함될 수 있음을 이해했습니다",
                   "--add-data"):
        assert needle in text, needle
