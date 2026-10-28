import pytest

from pcmigrate import cli


@pytest.fixture(autouse=True)
def _no_real_discovery(monkeypatch):
    """scan/export/checklist 의 '규칙 없는 프로그램' 안내가 실제 AppData 를 읽지 않게 막는다(테스트는 mock/tmp_path 만)."""
    monkeypatch.setattr(cli, "summarize", lambda rules, programs, *a, **k: (0, 0))


@pytest.fixture(autouse=True)
def _isolated_error_log(tmp_path_factory, monkeypatch):
    """오류 로그가 사용자의 실제 %LOCALAPPDATA% 폴더에 쌓이지 않게 한다(테스트가 일부러 일으킨 오류도 기록되므로)."""
    from pcmigrate import errorlog
    monkeypatch.setattr(errorlog, "PRIMARY_DIR", str(tmp_path_factory.mktemp("errlog")))
    errorlog.reset_throttle()  # 중복 제거/속도 제한/쓰기 확인 캐시는 시험끼리 공유하지 않는다


@pytest.fixture(autouse=True)
def _isolated_import_records(tmp_path_factory, monkeypatch):
    """복원(import --yes)이 남기는 복원 기록이 사용자의 실제 %LOCALAPPDATA% 폴더에 쌓이지 않게 한다."""
    from pcmigrate import import_record
    monkeypatch.setattr(import_record, "RECORD_DIR", str(tmp_path_factory.mktemp("import-records")))


@pytest.fixture(autouse=True)
def _no_real_package_scan(monkeypatch):
    """③ 첫 화면이 열릴 때의 '설정 묶음 자동 찾기'가 실제 바탕화면/드라이브를 훑지 않게 끈다(필요한 시험이 직접 켜고 가짜 위치를 넣는다)."""
    from pcmigrate.gui.controller import Controller
    monkeypatch.setattr(Controller, "scan_packages_on_show", False)


def make_rules_dir(path):
    """흐름 시험용 규칙 폴더: 규칙 파일이 하나는 있어야 하고(비어 있으면 오류), 시험 프로그램과는 맞지 않는다."""
    import json
    from pathlib import Path
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "zz-dummy.json").write_text(json.dumps({
        "id": "zz-dummy", "name": "Dummy", "detect": {"display_name_patterns": ["ZZ-no-such-program-ZZ"]},
        "paths": [], "manual_warnings": []}), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolated_net_state(tmp_path_factory, monkeypatch):
    """이더넷 주소 복구 기록(%LOCALAPPDATA%/pcmigrate/net-restore.json)을 실제 위치가 아닌 임시 폴더로 돌린다(stage39). 시험은 가짜 runner 만 쓴다."""
    from pcmigrate import net_setup
    monkeypatch.setattr(net_setup, "STATE_DIR", str(tmp_path_factory.mktemp("net-state")))
