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
