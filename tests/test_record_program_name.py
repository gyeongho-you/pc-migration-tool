"""S7: record start --program 의 이름 길이 제한, Windows 예약어 접미사, 일반 오류 핸들러의 홈 경로 치환. 가짜 프로필만 쓴다."""
import pytest

from pcmigrate import cli, envpaths, record_mode as rm
from test_record_mode import USER, Fake


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = Fake(tmp_path, monkeypatch)
    envpaths.set_overrides({"USERPROFILE": str(f.home), "HOME": str(f.home), "APPDATA": str(f.app), "LOCALAPPDATA": str(f.loc),
                            "PROGRAMDATA": str(f.pd)})
    yield f
    envpaths.set_overrides(None)


def test_long_program_name_is_a_friendly_error(fake, capsys):
    assert cli.main(["record", "start", "--program", "A" * 300]) == 1
    err = capsys.readouterr().err
    assert "너무 깁니다" in err and "예상하지 못한" not in err and USER not in err
    assert not (fake.loc / "pcmigrate").exists()  # 아무것도 만들지 않았다


@pytest.mark.parametrize("name", ["CON", "nul", "COM1", "lpt9", "AUX", "prn"])
def test_reserved_names_get_suffix(name):
    s = rm.slug(name)
    assert s.endswith("-record") and s.split("-")[0] not in ("",) and s != name.lower()
    assert rm.default_work_dir(name).endswith(s)


def test_reserved_program_creates_normal_folder(fake):
    s, _ = rm.start("CON", None, documents_fn=lambda: str(fake.docs))
    assert s.work.name == "con-record" and s.work.is_dir()


def test_normal_slugs_unchanged():
    assert rm.slug("Fake Zephyr") == "fake-zephyr"
    assert rm.slug("Console") == "console"


def test_generic_error_handler_tokenizes_home_in_stderr(fake, monkeypatch, capsys):
    def boom(args):
        raise OSError(f"cannot open {fake.home}/x.txt")
    monkeypatch.setattr(rm, "start", lambda *a, **k: boom(None))
    assert cli.main(["record", "start", "--program", "Prog"]) == 1
    err = capsys.readouterr().err
    assert "예상하지 못한" in err and USER not in err and "%USERPROFILE%" in err
