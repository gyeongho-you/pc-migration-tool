"""stage33: '자체 이전 필요' 프로그램 목록(selftransfer/*.json), 후보 표시, CLI/JSON, 내보내기 경고, 번들 포함. 가짜 환경만 쓴다."""
import json
import os
import re
import sys
from pathlib import Path

import pytest

from pcmigrate import checklist_data, cli, coverage, envpaths, selftransfer as st
from pcmigrate.api import ApiError, Deps, load_self_transfer_checked, run_export
from pcmigrate.detect import Program
from pcmigrate.discover import Candidate, build_selection, discover, flag_extra, render, to_dict
from pcmigrate.find_program import find_program, render as render_find
from conftest import make_rules_dir
from test_install_dir import Fake, fake, touch  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ITEMS = st.load_items(ROOT / "selftransfer")


def by_id(i):
    return next(x for x in ITEMS if x.id == i)


def good(**kw):
    d = {"id": "x", "name": "X", "category": "other", "reason": "이유", "todo": "할 일", "display_name_patterns": ["xprog"]}
    d.update(kw)
    return d


# --------------------------------------------------------------------------- 데이터/스키마/로더

def test_shipped_list_loads_with_expected_items_and_grades():
    assert {i.id for i in ITEMS} == {"kakaotalk", "finance_security", "cert", "antivirus", "driver", "office", "adobe_cc"}
    assert {i.category for i in ITEMS} == {"chat", "finance_security", "cert", "antivirus", "driver", "office", "adobe"}
    assert all(i.grade in ("A", "B") and i.reason and i.todo and i.notes for i in ITEMS)  # 근거가 약한 항목(C)은 없다
    assert all(not any(c in i.reason + i.todo for c in "\\") for i in ITEMS)


def test_every_checklist_item_reference_exists():
    ids = {c.id for c in checklist_data.COMMON_ITEMS}
    assert all(i.checklist_item in ids for i in ITEMS if i.checklist_item)


def test_schema_errors():
    bad = [good(id=""), good(category="nope"), good(grade="C"), good(display_name_patterns=[]), good(display_name_patterns="x"),
           good(todo=""), good(display_name_patterns=[""]), "str", good(checklist_item=3)]
    for b in bad:
        with pytest.raises(st.SelfTransferError):
            st.parse_item(b)
    assert st.parse_item(good(display_name_patterns=[], folder_names=["npki"])).folder_names == ("npki",)  # 폴더 이름만으로도 가능


def test_loader_adds_items_without_code_change_and_rejects_duplicates(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"format": 1, "items": [good(id="a")]}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps({"format": 1, "items": [good(id="b", display_name_patterns=["bprog"])]}), encoding="utf-8")
    assert [i.id for i in st.load_items(tmp_path)] == ["a", "b"]
    (tmp_path / "c.json").write_text(json.dumps({"format": 1, "items": [good(id="a")]}), encoding="utf-8")
    with pytest.raises(st.SelfTransferError, match="중복"):
        st.load_items(tmp_path)
    (tmp_path / "c.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(st.SelfTransferError):
        st.load_items(tmp_path)
    (tmp_path / "c.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    with pytest.raises(st.SelfTransferError, match="format"):
        st.load_items(tmp_path)
    with pytest.raises(st.SelfTransferError, match="찾을 수 없"):
        st.load_items(tmp_path / "none")


def test_api_checked_loader_and_default_non_strict(tmp_path, monkeypatch):
    assert len(load_self_transfer_checked()) == len(ITEMS)
    with pytest.raises(ApiError, match="자체 이전 목록 오류"):
        load_self_transfer_checked(tmp_path / "missing")
    monkeypatch.setattr(st, "default_dir", lambda: tmp_path / "missing")
    st._cached.cache_clear()
    try:
        assert st.default_items() == []  # 표시 전용: 읽지 못해도 탐지는 계속된다
        with pytest.raises(st.SelfTransferError):
            st.default_items(strict=True)
    finally:
        st._cached.cache_clear()


def test_check_rules_reports_count(capsys):
    assert cli.main(["check-rules"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["self_transfer"] == len(ITEMS) and out["rules"] >= 1
    assert cli.main(["check-rules", "--self-transfer", str(ROOT / "nonexistent")]) == 1


def test_default_dir_source_frozen_and_package_data(monkeypatch, tmp_path):
    assert st.default_dir() == ROOT / "selftransfer"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert st.default_dir() == tmp_path / "selftransfer"


# --------------------------------------------------------------------------- 이름 매칭(이름 변형, 오탐 방지)

@pytest.mark.parametrize("name,publisher,expected", [
    ("KakaoTalk", "Kakao Corp.", "kakaotalk"), ("카카오톡", "", "kakaotalk"), ("Kakao Talk 3.5", "", "kakaotalk"),
    ("Kakao Games Launcher", "Kakao Corp.", None), ("KakaoMap Viewer", "Kakao", None),  # 카카오 이름만으로는 판정하지 않는다
    ("TouchEn nxKey", "RaonSecure", "finance_security"), ("nProtect Online Security V1", "INCA Internet", "finance_security"),
    ("AhnLab Safe Transaction", "AhnLab", "finance_security"), ("Veraport G3 x64", "Wizvera", "finance_security"),
    ("IPinside LWS Agent", "", "finance_security"), ("Some Tool", "RaonSecure Co.", "finance_security"),
    ("nProtect GameGuard", "INCA Internet", None),  # 게임 보안은 금융 보안 모듈이 아니다
    ("yessign Client", "", "cert"), ("공동인증서 도구", "", "cert"),
    ("AhnLab V3 Lite", "AhnLab, Inc.", "antivirus"), ("알약", "ESTsoft", "antivirus"), ("Kaspersky Free", "", "antivirus"),
    ("NVIDIA Graphics Driver 551.23", "NVIDIA", "driver"), ("AMD Software: Adrenalin Edition", "", "driver"),
    ("Intel(R) Graphics Command Center", "", "driver"), ("Realtek Audio Driver", "", "driver"),
    ("NVIDIA CUDA Toolkit 12", "NVIDIA", None),  # 개발 도구는 드라이버가 아니다
    ("Microsoft 365 Apps for enterprise - ko-kr", "", "office"), ("Microsoft Office Professional Plus 2019", "", "office"),
    ("Microsoft Visual Studio Tools for Office Runtime", "", None),
    ("Adobe Creative Cloud", "Adobe Inc.", "adobe_cc"), ("Adobe Photoshop 2024", "", "adobe_cc"), ("Adobe Acrobat Reader", "Adobe", None),
    ("Autodesk Revit 2024", "Autodesk", None), ("Google Chrome", "Google LLC", None),
])
def test_name_matching(name, publisher, expected):
    m = st.match_program(ITEMS, name, publisher)
    assert (m.id if m else None) == expected


def test_folder_name_flag_without_link():
    assert st.flag_candidate(ITEMS, "NPKI", [])["id"] == "cert"
    assert st.flag_candidate(ITEMS, "Kakao", []) is None
    assert st.flag_candidate(ITEMS, ".npki", [])["id"] == "cert"


def test_weak_link_flags_only_when_all_linked_programs_match():
    linked = [("KakaoTalk", "Kakao Corp."), ("Kakao Games Launcher", "Kakao Corp.")]
    assert st.flag_candidate(ITEMS, "Kakao", linked, weak=False)["id"] == "kakaotalk"
    assert st.flag_candidate(ITEMS, "Kakao", linked, weak=True) is None  # 제조사 폴더가 여러 프로그램에 걸침: 접지 않는다
    assert st.flag_candidate(ITEMS, "Kakao", linked[:1], weak=True)["id"] == "kakaotalk"


# --------------------------------------------------------------------------- discover/find: 후보 표시

def roaming(fake):
    return fake.home / "AppData" / "Roaming"


def run_discover_fake(fake, programs, items=None):
    return discover([], programs, envpaths.expand_env, with_sizes=False, self_transfer=items)


def test_discover_flags_candidate_and_program_entry(fake):
    touch(roaming(fake) / "KakaoTalk" / "chat.db", b"x")
    touch(roaming(fake) / "Acme Paint" / "a.ini", b"[a]")
    progs = [Program("KakaoTalk", "3.5", "Kakao Corp.", install_location=str(fake.tmp / "nowhere")),
             Program("Acme Paint", "1.0", "Acme Corp", install_location=str(fake.tmp / "nowhere2"))]
    d = run_discover_fake(fake, progs, ITEMS)
    ent = {e.program.name: e for e in d.programs}
    assert ent["KakaoTalk"].self_transfer["id"] == "kakaotalk" and ent["Acme Paint"].self_transfer is None
    kakao = next(c for c in d.candidates() if c.rel == "KakaoTalk")
    acme = next(c for c in d.candidates() if c.rel == "Acme Paint")
    assert kakao.self_transfer["id"] == "kakaotalk" and set(kakao.self_transfer) == {"id", "name", "category", "reason", "todo"}
    assert acme.self_transfer is None
    assert run_discover_fake(fake, progs, []).candidates()[0].self_transfer is None  # 목록이 비어 있으면 표시 없음


def test_unrelated_kakao_program_is_not_flagged_without_evidence(fake):
    touch(roaming(fake) / "KakaoGamesLauncher" / "a.ini", b"[a]")
    d = run_discover_fake(fake, [Program("Kakao Games Launcher", "1", "Kakao Corp.", install_location=str(fake.tmp / "n"))], ITEMS)
    assert d.candidates() and all(c.self_transfer is None for c in d.candidates()) and d.programs[0].self_transfer is None


def test_discover_text_json_and_selection(fake):
    touch(roaming(fake) / "KakaoTalk" / "chat.db", b"x")
    d = run_discover_fake(fake, [Program("KakaoTalk", "3.5", "Kakao Corp.", install_location=str(fake.tmp / "n"))], ITEMS)
    text = render(d)
    assert "자체 이전 필요: " in text and "대신 할 일: 카카오톡 자체의 대화 백업" in text and "[자체 이전 필요]" in text
    assert text.count("대신 할 일:") == 1  # 프로그램 줄에 한 번만(후보 줄에 같은 안내를 되풀이하지 않는다)
    j = json.loads(render(d, "json"))
    e = j["programs_without_rule"][0]
    assert e["self_transfer"]["id"] == "kakaotalk" and e["candidates"][0]["self_transfer"]["todo"]
    assert j["counts"]["self_transfer"] == 1
    sel = build_selection(d)
    assert sel["candidates"][0]["self_transfer"] == "kakaotalk" and sel["candidates"][0]["include"] is False
    d2 = run_discover_fake(fake, [Program("Acme", "1", "A")], [])
    assert "self_transfer" not in json.dumps(build_selection(d2)["candidates"])  # 해당 없으면 키 자체가 없다(이전 형식 그대로)


def test_find_program_flags_candidates_and_renders(fake):
    touch(roaming(fake) / "Acme Paint" / "a.ini", b"[a]")
    p = fake.prog()
    res = find_program(p, [], envpaths.expand_env, self_transfer=[st.parse_item(good(id="acme", display_name_patterns=["acme paint"]))])
    assert res.self_transfer["id"] == "acme" and res.candidates and all(c.self_transfer for c in res.candidates)
    assert "자체 이전 필요: 이유 / 대신 할 일: 할 일" in render_find(res) and "선택은 막지 않습니다" in render_find(res)
    j = json.loads(render_find(res, "json"))
    assert j["self_transfer"]["id"] == "acme" and all(c["self_transfer"] for c in j["candidates"])
    assert all(e.self_transfer for e in [res.as_discovery().programs[0]])
    plain = find_program(p, [], envpaths.expand_env, self_transfer=[])
    assert plain.self_transfer is None and "자체 이전 필요" not in render_find(plain)
    assert json.loads(render_find(plain, "json"))["self_transfer"] is None


def test_cli_discover_json_and_text(fake, capsys, tmp_path, monkeypatch):
    touch(roaming(fake) / "KakaoTalk" / "chat.db", b"x")
    rules = make_rules_dir(tmp_path / "rules")
    deps = Deps(registry=lambda: [Program("KakaoTalk", "3.5", "Kakao Corp.", install_location=str(fake.tmp / "n"))],
                winget_export=lambda: None, winget_list=lambda: None)
    monkeypatch.setattr(cli, "_deps", lambda: deps)
    assert fake.cli("discover", "--rules", str(rules), "--format", "json") == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["programs_without_rule"][0]["self_transfer"]["id"] == "kakaotalk" and "자체 이전 필요 1개" in out.err
    assert fake.cli("discover", "--rules", str(rules)) == 0
    assert "자체 이전 필요: " in capsys.readouterr().out
    assert fake.cli("discover", "--rules", str(rules), "--program", "KakaoTalk", "--format", "json") == 0
    assert json.loads(capsys.readouterr().out)["self_transfer"]["id"] == "kakaotalk"


# --------------------------------------------------------------------------- 내보내기: 경고만, 막지 않음

def test_flag_extra_uses_link_and_folder_names(fake):
    p = [Program("KakaoTalk", "3.5", "Kakao Corp.", install_location=str(fake.tmp / "n"))]
    assert flag_extra(ITEMS, "%APPDATA%", "KakaoTalk", str(roaming(fake) / "KakaoTalk"), p)["id"] == "kakaotalk"
    assert flag_extra(ITEMS, "%APPDATA%", "Other", str(roaming(fake) / "Other"), p) is None
    assert flag_extra(ITEMS, "%APPDATA%", "NPKI", str(roaming(fake) / "NPKI"), [])["id"] == "cert"


def test_export_warns_but_does_not_block_and_marks_manifest_and_table(fake, tmp_path):
    touch(roaming(fake) / "KakaoTalk" / "settings.ini", "[a]\nx=1\n")
    touch(roaming(fake) / "Acme Paint" / "settings.ini", "[a]\nx=1\n")
    from pcmigrate.discover import ExtraItem
    items = [ExtraItem("%APPDATA%", "KakaoTalk", roaming(fake) / "KakaoTalk", True),
             ExtraItem("%APPDATA%", "Acme Paint", roaming(fake) / "Acme Paint", True)]
    rules = make_rules_dir(tmp_path / "rules")
    progs = [Program("KakaoTalk", "3.5", "Kakao Corp.", install_location=str(fake.tmp / "n")), fake.prog(loc=fake.tmp / "n2")]
    r = run_export(rules, tmp_path / "o.zip", extra_items=items, deps=Deps(registry=lambda: progs, winget_export=lambda: None,
                                                                          winget_list=lambda: None))
    warn = [n for n in r.notes if "자체 이전 필요" in n]
    assert len(warn) == 1 and "카카오톡" in warn[0] and "막지 않고 담았습니다" in warn[0] and "카카오톡/공인인증서/금융보안" in warn[0]
    assert items[0].self_transfer["id"] == "kakaotalk" and items[1].self_transfer is None
    assert any("KakaoTalk" in str(f) for f in r.manifest["files"])  # 막지 않고 담았다
    ex = {e["rel"]: e for e in r.manifest["extras"]}
    assert ex["KakaoTalk"]["self_transfer"] == "kakaotalk" and "self_transfer" not in ex["Acme Paint"]  # 덧붙이기만
    rows = {x["program"]: x for x in coverage.rows(r.manifest)}
    kk = rows[f"{coverage.EXTRA_PROGRAM}: KakaoTalk"]
    assert "자체 이전 필요" in kk["note"] and kk["self_transfer"] == "kakaotalk"
    assert "자체 이전 필요" not in rows[f"{coverage.EXTRA_PROGRAM}: Acme Paint"]["note"]
    assert r.summary_dict("o.zip")["self_transfer"] == ["kakaotalk"]
    assert any("자체 이전 필요" in line for line in coverage.lines(r.manifest))
    from pcmigrate.package import verify_package
    m, problems = verify_package(tmp_path / "o.zip")
    assert m is not None and problems == []  # 이전 검증 규칙을 그대로 통과(덧붙인 키는 무해)


def test_export_without_flagged_items_is_unchanged(fake, tmp_path):
    touch(roaming(fake) / "Acme Paint" / "settings.ini", "[a]\nx=1\n")
    from pcmigrate.discover import ExtraItem
    rules = make_rules_dir(tmp_path / "rules")
    r = run_export(rules, tmp_path / "o.zip", extra_items=[ExtraItem("%APPDATA%", "Acme Paint", roaming(fake) / "Acme Paint", True)],
                   deps=Deps(registry=lambda: [fake.prog(loc=fake.tmp / "n")], winget_export=lambda: None, winget_list=lambda: None))
    assert not [n for n in r.notes if "자체 이전" in n] and "self_transfer" not in r.summary_dict("o.zip")
    assert all("self_transfer" not in e for e in r.manifest["extras"])


def test_checklist_not_duplicated_by_self_transfer(fake, tmp_path):
    """체크리스트의 공통 항목은 그대로 한 번만 나오고(자체 이전 정보는 manual_warnings 에 넣지 않는다), 경고는 공통 항목을 가리키기만 한다."""
    touch(roaming(fake) / "KakaoTalk" / "settings.ini", "[a]\nx=1\n")
    from pcmigrate.discover import ExtraItem
    rules = make_rules_dir(tmp_path / "rules")
    r = run_export(rules, tmp_path / "o.zip", extra_items=[ExtraItem("%APPDATA%", "KakaoTalk", roaming(fake) / "KakaoTalk", True)],
                   deps=Deps(registry=lambda: [Program("KakaoTalk", "3.5", "Kakao Corp.", install_location=str(fake.tmp / "n"))],
                             winget_export=lambda: None, winget_list=lambda: None))
    text = json.dumps(r.manifest["manual_warnings"], ensure_ascii=False)
    assert "카카오톡 자체의 대화 백업" not in text
    flat = "\n".join([*r.checklist.login, *r.checklist.data])
    assert flat.count("카카오톡 대화") == 1


# --------------------------------------------------------------------------- 번들 포함(pyproject / spec / 빌드 스모크)

def test_pyproject_packages_selftransfer_data():
    import tomllib
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]
    assert cfg["package-dir"]["pcmigrate.selftransfer_data"] == "selftransfer"
    assert "pcmigrate.selftransfer_data" in cfg["packages"]
    pkg_data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
    assert "*.json" in pkg_data["pcmigrate.selftransfer_data"]


def test_build_script_bundles_selftransfer(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_exe", ROOT / "packaging" / "build_exe.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    text = build.SPEC_TEMPLATE.format(root="R", src="S", rules="RU", selftransfer="ST", gui="G", cli="C")
    assert text.count('(r"ST", "selftransfer")') == 2
    assert "'selftransfer'" in (ROOT / "packaging" / "build_exe.py").read_text(encoding="utf-8")  # onefile 의 --add-data

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = '{"rules": 22, "dir": "x", "self_transfer": 7}\n' if cmd[-1] == "check-rules" else "pcmigrate 0.2.2\n"
        return R()
    ok, msg = build.smoke_test(Path("pcmigrate.exe"), run=fake_run)
    assert ok and "자체 이전 목록 7개" in msg

    def missing(cmd, **kw):
        class R:
            returncode = 0
            stdout = '{"rules": 22, "dir": "x", "self_transfer": 0}\n' if cmd[-1] == "check-rules" else "v\n"
        return R()
    ok, msg = build.smoke_test(Path("pcmigrate.exe"), run=missing)
    assert not ok and "selftransfer" in msg


def test_gitignore_does_not_hide_selftransfer():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert not any(p.strip("/") in ("selftransfer", "*.json") for p in ignore)
