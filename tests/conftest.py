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
