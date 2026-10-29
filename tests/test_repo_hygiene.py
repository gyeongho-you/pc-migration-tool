"""v0.1.1 수정 5: 저장소 위생(.gitignore / .gitattributes)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NUL = "\0"
REQUIRED_IGNORES = ("checklist*.md", "checklist*.md.bak", "discover*.md", "*.bak", ".pcmigrate-*", "*.jsonl", "*.egg-info/",
                    "pcmigrate-win.zip")


def lines(name):
    return [l.strip() for l in (ROOT / name).read_text(encoding="utf-8").splitlines()]


def check_ignore(paths):
    """git check-ignore 에 경로를 바이트로 넘겨(윈도우 줄바꿈 변환 방지) 무시되는 경로만 돌려받는다."""
    r = subprocess.run(["git", "check-ignore", "--stdin", "-z"], cwd=ROOT, input=NUL.join(paths).encode("utf-8"),
                       capture_output=True)
    return [p for p in r.stdout.decode("utf-8").split(NUL) if p]


def test_gitignore_has_personal_output_patterns():
    ig = lines(".gitignore")
    for pat in REQUIRED_IGNORES:
        assert pat in ig, pat
    assert ".claude/worktrees/" in ig  # 기존 항목 유지


def test_gitattributes_forces_lf_with_crlf_scripts():
    ga = lines(".gitattributes")
    assert "* text=auto eol=lf" in ga
    assert any(l.startswith("*.bat") and "eol=crlf" in l for l in ga)


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="git 저장소가 아님")
def test_no_tracked_file_is_ignored_and_patterns_work():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True).stdout.decode("utf-8")
    tracked = [t for t in out.split(NUL) if t]
    assert check_ignore(tracked) == [], "추적 중인 파일이 무시 규칙에 걸림"
    probe = ["checklist.md", "checklist-old.md.bak", "discover.md", "x.bak", ".pcmigrate-state.json", "m.jsonl",
             "src/pcmigrate.egg-info/PKG-INFO", "pcmigrate-win.zip"]
    assert sorted(check_ignore(probe)) == sorted(probe)
