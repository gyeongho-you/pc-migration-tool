import copy
import json
from pathlib import Path

import pytest

from pcmigrate.rules import RuleError, load_rules, parse_rule

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"

VALID = {
    "id": "x",
    "name": "X",
    "detect": {"display_name_patterns": ["X"]},
    "paths": [{"path": "%APPDATA%\\X", "dest": "x/appdata"}],
    "manual_warnings": ["w"],
}


def test_repo_rules_load():
    rules = load_rules(REPO_RULES)
    assert {"revit", "autocad", "vscode"} <= {r.id for r in rules}
    assert all(r.manual_warnings for r in rules)


def test_parse_valid_defaults():
    r = parse_rule(VALID)
    assert r.paths[0].kind == "dir"
    assert r.display_name_patterns == ("X",)


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("id"),
    lambda d: d.update(name=""),
    lambda d: d.pop("detect"),
    lambda d: d.update(detect={}),
    lambda d: d.update(detect={"display_name_patterns": "X"}),
    lambda d: d.update(paths="nope"),
    lambda d: d["paths"][0].update(kind="symlink"),
    lambda d: d["paths"][0].update(dest="../escape"),
    lambda d: d["paths"][0].update(dest="C:\\abs"),
    lambda d: d["paths"][0].update(dest="/abs"),
    lambda d: d.update(manual_warnings=[1]),
    lambda d: d.update(notes=5),
])
def test_parse_invalid(mutate):
    data = copy.deepcopy(VALID)
    mutate(data)
    with pytest.raises(RuleError):
        parse_rule(data)


def test_top_level_not_object():
    with pytest.raises(RuleError):
        parse_rule([])


def test_bad_json_and_duplicate_id(tmp_path):
    (tmp_path / "a.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(RuleError):
        load_rules(tmp_path)
    (tmp_path / "a.json").write_text(json.dumps(VALID), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(VALID), encoding="utf-8")
    with pytest.raises(RuleError, match="중복"):
        load_rules(tmp_path)


def test_missing_or_empty_rules_dir_is_error(tmp_path):
    with pytest.raises(RuleError, match="찾을 수 없"):
        load_rules(tmp_path / "nope")
    with pytest.raises(RuleError, match="하나도 없"):
        load_rules(tmp_path)
