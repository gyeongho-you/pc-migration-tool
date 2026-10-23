import pytest

from pcmigrate import cli


@pytest.fixture(autouse=True)
def _no_real_discovery(monkeypatch):
    """scan/export/checklist 의 '규칙 없는 프로그램' 안내가 실제 AppData 를 읽지 않게 막는다(테스트는 mock/tmp_path 만)."""
    monkeypatch.setattr(cli, "summarize", lambda rules, programs, *a, **k: (0, 0))
