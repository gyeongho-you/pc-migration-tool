"""exe 빌드 스크립트(packaging/build_exe.py)의 순수 로직 테스트. 실제 PyInstaller 빌드는 하지 않는다."""
import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("build_exe", ROOT / "packaging" / "build_exe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_entry_scripts_exist_and_import_real_mains():
    assert "from pcmigrate.gui.app import main" in (ROOT / "packaging" / "gui_entry.py").read_text(encoding="utf-8")
    assert "from pcmigrate.cli import main" in (ROOT / "packaging" / "cli_entry.py").read_text(encoding="utf-8")


def test_two_exes_gui_is_windowed_cli_is_console(build):
    assert build.ENTRIES["pcmigrate-gui"][1] is False and build.ENTRIES["pcmigrate"][1] is True
    for entry, _console in build.ENTRIES.values():
        assert (ROOT / "packaging" / entry).exists()


def test_pyinstaller_version_is_pinned(build):
    assert build.PYINSTALLER_VERSION.count(".") == 2 and all(p.isdigit() for p in build.PYINSTALLER_VERSION.split("."))


def test_spec_template_bundles_rules_and_both_exes(build):
    text = build.SPEC_TEMPLATE.format(root="R", src="S", rules="RU", gui="G", cli="C")
    compile(text.replace("Analysis", "dict").replace("MERGE", "print").replace("PYZ", "list").replace("EXE", "dict")
            .replace("COLLECT", "dict"), "spec", "exec")  # 문법 확인
    assert '(r"RU", "rules")' in text and 'name="pcmigrate-gui", console=False' in text and 'name="pcmigrate", console=True' in text
    assert "upx=False" in text and 'name="pcmigrate")' in text  # 폴더 하나(dist/pcmigrate)에 exe 두 개


def test_sha256_helper(build, tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc" * 1000)
    assert build.sha256(f) == hashlib.sha256(b"abc" * 1000).hexdigest()


def test_build_outputs_are_gitignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for pattern in ("dist/", "build/", "*.spec", ".venv/"):
        assert pattern in ignore


def test_venv_is_never_the_global_environment(build, tmp_path):
    assert build.venv_python(tmp_path / "venv").parts[-3:-1] != ("Lib",) and str(tmp_path) in str(build.venv_python(tmp_path / "venv"))


def test_help_runs_without_building():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")  # 한글 도움말이 콘솔 코드페이지와 무관하게 읽히도록
    r = subprocess.run([sys.executable, str(ROOT / "packaging" / "build_exe.py"), "--help"], capture_output=True, text=True,
                       encoding="utf-8", env=env)
    assert r.returncode == 0 and "--mode" in r.stdout and "onedir" in r.stdout


def test_frozen_rules_dir_uses_meipass(monkeypatch, tmp_path):
    from pcmigrate import rules
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert rules.default_rules_dir() == tmp_path / "rules"
