"""S2/S3: '다음 실행 때 코드가 돌아가는 파일'을 복원 거부 목록과 place 보호 목록이 같은 기준으로 막는지 시험(순수 함수, 파일 접근 없음)."""
from pathlib import Path

import pytest

from pcmigrate import place_original as po
from pcmigrate.package_deny import restore_denied
from pcmigrate.package_plan import is_git_config_name
from pcmigrate.rules import RuleError, default_rules_dir, load_rules, parse_rule

HOME = "%USERPROFILE%"


def _rule(path, kind="dir", allow=None):
    p = {"path": path, "dest": "x/y", "kind": kind}
    if allow is not None:
        p["allow_extensions"] = allow
    return parse_rule({"id": "t", "name": "T", "detect": {"display_name_patterns": ["T"]}, "paths": [p]}, "<t>")


@pytest.mark.parametrize("ext", [".rb", ".lsp", ".fas", ".vlx", ".arx", ".mnl", ".node", ".gha", ".rhp", ".addin", ".ahk", ".vsix", ".gradle"])
def test_new_extensions_denied_without_rule_naming_them(ext):
    rule = _rule("%APPDATA%\\Acme\\Plugins")
    assert restore_denied(f"Plugins/a{ext}", Path("C:/x/Plugins/a" + ext), rule, "%APPDATA%\\Acme")
    assert restore_denied(f"Plugins/a{ext}", Path("C:/x/Plugins/a" + ext), None, "%APPDATA%\\Acme")  # extra(규칙 없음)도 거부


def test_rule_that_names_extension_still_allows_it():
    rule = _rule("%APPDATA%\\Acme\\Plugins\\*.rb", kind="file")
    assert restore_denied("Plugins/a.rb", Path("C:/x/Plugins/a.rb"), rule, "%APPDATA%\\Acme\\Plugins") is None
    assert restore_denied("Plugins/a.lsp", Path("C:/x/Plugins/a.lsp"), rule, "%APPDATA%\\Acme\\Plugins")  # 다른 확장자는 그대로 거부


def test_allow_extensions_field_allows_only_listed():
    rule = _rule("%APPDATA%\\Acme\\Plugins", allow=[".rb"])
    assert restore_denied("Plugins/a.rb", Path("C:/x/a.rb"), rule, "%APPDATA%\\Acme") is None
    assert restore_denied("Plugins/a.exe", Path("C:/x/a.exe"), rule, "%APPDATA%\\Acme")
    assert restore_denied("Plugins/a.vlx", Path("C:/x/a.vlx"), rule, "%APPDATA%\\Acme")


def test_allow_extensions_validation():
    with pytest.raises(RuleError):
        _rule("%APPDATA%\\Acme\\P", allow=["rb"])
    with pytest.raises(RuleError):
        _rule("%APPDATA%\\Acme\\P", allow=[".*"])


def test_builtin_plugin_rules_keep_restoring_their_plugins():
    """SketchUp/AutoCAD/Revit 규칙은 allow_extensions 로 자기 플러그인 확장자를 명시해 정상 사용자 플러그인 복원이 유지된다."""
    rules = {r.id: r for r in load_rules(default_rules_dir())}
    sk, ac, rv = rules["sketchup"], rules["autocad"], rules["revit"]
    base_sk = "%APPDATA%\\SketchUp"
    assert restore_denied("SketchUp 2024/SketchUp/Plugins/x/a.rb", Path("C:/a.rb"), sk, base_sk) is None
    base_ac = "%APPDATA%\\Autodesk"
    for ext in (".lsp", ".mnl", ".fas", ".vlx", ".arx"):
        assert restore_denied(f"AutoCAD 2024/R24.3/enu/Support/a{ext}", Path("C:/a"), ac, base_ac) is None
    assert restore_denied("Revit/2024/Addins/a.addin", Path("C:/a.addin"), rv, base_ac) is None
    # 허용 목록 밖은 여전히 거부
    assert restore_denied("SketchUp 2024/SketchUp/Plugins/a.exe", Path("C:/a.exe"), sk, base_sk)
    assert restore_denied("Revit/2024/Addins/a.rb", Path("C:/a.rb"), rv, base_ac)
    for r in (sk, ac, rv):
        assert any("신뢰하는" in w for w in r.manual_warnings)


@pytest.mark.parametrize("rel", [".config/git/config", ".config/git/ignore", ".bash_aliases", ".vimrc", ".emacs", ".inputrc",
                                 ".gradle/init.d/a.gradle", ".gradle/init.d/readme.txt", ".m2/settings.xml",
                                 ".vscode/extensions/pub.ext-1/readme.md", ".vscode/extensions/x/y/z.txt", ".emacs.d/init.el"])
def test_home_deny_list(rel):
    assert restore_denied(rel, Path("C:/Users/u/" + rel), None, HOME)


def test_gitconfig_is_still_allowed_with_warning_design():
    assert restore_denied(".gitconfig", Path("C:/Users/u/.gitconfig"), None, HOME) is None
    assert is_git_config_name(".gitconfig") and is_git_config_name(".gitconfig-work")
    assert is_git_config_name("C:\\Users\\u\\.config\\git\\config") and is_git_config_name("C:/Users/u/.config/git/config")
    assert not is_git_config_name("C:\\Users\\u\\config")


def test_home_dirs_only_under_home_base():
    # 다른 기준 폴더의 같은 이름 폴더(예: 앱 데이터 안의 git 폴더)는 홈 전용 거부에 걸리지 않는다
    assert restore_denied("git/config", Path("C:/x/git/config"), None, "%APPDATA%\\Acme") is None


# ----------------------------------------------------------------------------- S3: place 보호 목록
@pytest.mark.parametrize("p,is_dir", [
    ("C:\\Users\\u\\.ssh", True), ("C:\\Users\\u\\.ssh\\known_hosts", False), ("C:\\Users\\u\\.ssh\\keys", True),
    ("C:\\Users\\u\\.vscode\\extensions", True), ("C:\\Users\\u\\.vscode\\extensions\\a\\b", True), ("C:\\Users\\u\\.vscode", True),
    ("C:\\Users\\u\\.config\\git\\config", False), ("C:\\Users\\u\\.config\\git", True), ("C:\\Users\\u\\.config", True),
    ("C:\\Users\\u\\.gradle\\init.d", True), ("C:\\Users\\u\\.gradle\\init.d\\a.gradle", False),
    ("C:\\Users\\u\\.bash_aliases", False), ("C:\\Users\\u\\.vimrc", False), ("C:\\Users\\u\\.emacs", False),
    ("C:\\Users\\u\\.inputrc", False), ("C:\\Users\\u\\.m2\\settings.xml", False),
    ("C:\\Users\\Default\\Desktop", True), ("C:\\Users\\Default", True), ("C:\\Users\\All Users\\x", True),
])
def test_place_protects_same_list_as_restore(p, is_dir):
    assert po.protected_reason(p, is_dir=is_dir), p


@pytest.mark.parametrize("p,is_dir", [
    ("D:\\proj\\.vscode", True), ("D:\\.vscode", True), ("C:\\Users\\u\\proj\\.vscode", True), ("C:\\Users\\u\\proj\\.ssh", True),
    ("C:\\Users\\u\\Documents", True), ("C:\\Users\\u\\.config\\other", True), ("C:\\Users\\u\\.gradle\\caches", True),
    ("C:\\Users\\u\\.gitconfig", False), ("D:\\work\\.vimrc", False),
])
def test_place_does_not_overblock_user_data(p, is_dir):
    assert po.protected_reason(p, is_dir=is_dir) is None, p
