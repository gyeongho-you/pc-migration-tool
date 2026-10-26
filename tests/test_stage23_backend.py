"""stage23: 캐시류 하위 폴더 제외, 설정 확인표(coverage), '이 프로그램 설정 찾기'. 가짜 환경(tmp_path)만 쓴다."""
import json
import os
import zipfile
from pathlib import Path

import pytest

from pcmigrate import cli, coverage
from pcmigrate.api import ApiError, Deps, run_export, run_find_program
from pcmigrate.detect import Program
from pcmigrate.discover import Candidate, ExtraItem, scan_candidate, scan_tree
from pcmigrate.discover_data import REGENERABLE_DIRS, REGENERABLE_SUBPATHS, is_regenerable
from pcmigrate.find_program import find_program, program_tokens, render
from pcmigrate.package import (EXTRA_ID, collect_extra_items, collect_items, export_package, plan_import,
                               verify_package)
from pcmigrate.rules import load_rules, parse_rule

REPO_RULES = Path(__file__).resolve().parents[1] / "rules"


def touch(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class Env:
    def __init__(self, tmp):
        self.home = tmp / "home"
        self.appdata = self.home / "AppData" / "Roaming"
        self.local = self.home / "AppData" / "Local"
        self.pd = tmp / "ProgramData"
        for p in (self.appdata, self.local, self.pd, self.home / "Documents"):
            p.mkdir(parents=True, exist_ok=True)

    def expand(self, s):
        return (s.replace("%APPDATA%", str(self.appdata)).replace("%LOCALAPPDATA%", str(self.local))
                 .replace("%PROGRAMDATA%", str(self.pd)).replace("%USERPROFILE%", str(self.home)))


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def extra(env, rel, root="%APPDATA%"):
    return ExtraItem(root, rel, Path(env.expand(root)) / rel, True)


def app_tree(env):
    base = env.appdata / "Acme"
    touch(base / "config.ini", b"cfg")
    touch(base / "Cache" / "a.bin", b"c" * 100)
    touch(base / "Cache" / "deep" / "b.bin", b"c" * 50)
    touch(base / "Code Cache" / "js" / "x.bin")
    touch(base / "GPUCache" / "g.bin")
    touch(base / "Service Worker" / "CacheStorage" / "s.bin")
    touch(base / "Service Worker" / "Database" / "keep.db", b"db")
    touch(base / "node_modules" / "m" / "index.js")
    touch(base / "logs" / "a.log")
    touch(base / "CacheTool" / "user.cfg", b"user")  # 이름에 cache 가 들어가도 정확 일치가 아니면 담긴다
    touch(base / "profile" / "Logs" / "x.log")
    return base


# --------------------------------------------------------------------------- 1) 캐시류 제외

def test_is_regenerable_exact_names_only():
    assert is_regenerable(("Cache",)) and is_regenerable(("x", "GPUCache")) and is_regenerable(("a", "Code Cache"))
    assert is_regenerable(("a", "Service Worker", "CacheStorage"))
    assert not is_regenerable(("CacheTool",)) and not is_regenerable(("MyCache",)) and not is_regenerable(("CacheStorage",))
    assert not is_regenerable(()) and not is_regenerable(("Service Worker",))
    assert set(REGENERABLE_DIRS.values()) <= {"A", "B"} and all(v == "A" for v in REGENERABLE_SUBPATHS.values())


def test_collect_extra_skips_cache_dirs_and_records(env):
    app_tree(env)
    items, skipped = collect_extra_items([extra(env, "Acme")], env.expand)
    rels = {i.rel for i in items}
    assert rels == {"Acme/config.ini", "Acme/Service Worker/Database/keep.db", "Acme/CacheTool/user.cfg"}
    cache = [s for s in skipped if s.get("cache")]
    assert {Path(s["path"]).name for s in cache} == {"Cache", "Code Cache", "GPUCache", "CacheStorage", "node_modules", "logs", "Logs"}
    assert all(s["reason"] == "재생성되는 임시 저장물" and s["rule"] == EXTRA_ID for s in cache)
    c = next(s for s in cache if s["name"] == "Cache")
    assert c["files"] == 2 and c["bytes"] == 150 and c["complete"] is True


def test_collect_extra_include_caches_keeps_everything(env):
    app_tree(env)
    items, skipped = collect_extra_items([extra(env, "Acme")], env.expand, include_caches=True)
    assert len(items) == 10  # node_modules/index.js 는 복원 거부 형식(.js)이라 제외
    assert not [s for s in skipped if s.get("cache")]


def test_selected_top_folder_named_cache_is_not_pruned(env):
    touch(env.appdata / "Cache" / "a.bin")
    items, skipped = collect_extra_items([extra(env, "Cache")], env.expand)
    assert [i.rel for i in items] == ["Cache/a.bin"] and not skipped


def test_rule_paths_untouched_by_cache_filter(env):
    touch(env.appdata / "Vendor" / "Cache" / "keep.cfg", b"k")
    rule = parse_rule({"id": "r", "name": "R", "detect": {"display_name_patterns": ["R"]},
                       "paths": [{"path": "%APPDATA%\\Vendor", "dest": "r/v", "kind": "dir", "description": "설정"}]})
    items, skipped = collect_items(rule, env.expand)
    assert [i.rel for i in items] == ["Vendor/Cache/keep.cfg"] and not [s for s in skipped if s.get("cache")]


def _export(env, tmp_path, extras, rules=(), **kw):
    out = tmp_path / "o" / "pkg.zip"
    m = export_package(list(rules), out, [], None, env.expand, extras=extras, **kw)
    return out, m


def test_export_manifest_records_cache_and_coverage(env, tmp_path):
    app_tree(env)
    out, m = _export(env, tmp_path, [extra(env, "Acme")])
    assert not any("Cache/" in f["rel"] for f in m["files"])
    n, files, size = coverage.cache_totals(m)
    assert n == 7 and files >= 7 and size > 0
    row = [r for r in coverage.rows(m) if r["rule"] == "extra"][0]
    assert row["status"] == coverage.ST_OK and row["files"] == 3 and "임시 저장물" in row["note"]
    assert verify_package(out)[1] == []


def test_export_include_caches_and_fingerprint_resume(env, tmp_path):
    app_tree(env)
    out, m = _export(env, tmp_path, [extra(env, "Acme")], include_caches=True)
    assert len(m["files"]) == 10 and coverage.cache_totals(m)[0] == 0
    from pcmigrate.package import _fingerprint
    a, _ = _fingerprint([], [extra(env, "Acme")], "auto", False)
    b, _ = _fingerprint([], [extra(env, "Acme")], "auto", True)
    assert a != b


def test_run_export_include_caches_passthrough(env, tmp_path):
    app_tree(env)
    deps = Deps(registry=lambda: [], winget_export=lambda: None, winget_list=lambda: None)
    deps.export = lambda *a, **k: export_package(*a, expand=env.expand, **k)
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "x.json").write_text(json.dumps({"id": "x", "name": "X", "detect": {"display_name_patterns": ["zzz"]}}), encoding="utf-8")
    ex = [extra(env, "Acme")]
    r1 = run_export(rd, tmp_path / "a.zip", extra_items=ex, deps=deps)
    r2 = run_export(rd, tmp_path / "b.zip", extra_items=ex, deps=deps, include_caches=True)
    assert len(r1.manifest["files"]) == 3 and len(r2.manifest["files"]) == 10
    assert r1.summary_dict("a.zip")["cache_dirs_skipped"] == 7 and "cache_dirs_skipped" not in r2.summary_dict("b.zip")


def test_scan_tree_cache_split(env):
    base = app_tree(env)
    cache: dict = {}
    size, files, _n, _s, complete = scan_tree(base, 1e18, lambda: 0.0, cache)
    assert complete and cache["files"] == 8 and cache["size"] >= 150
    assert files == 3 and size == len(b"cfg") + len(b"db") + len(b"user")
    c = Candidate("%APPDATA%", "Acme", str(base), True)
    scan_candidate(c, 5, lambda: 0.0, 1e18)
    assert c.files == 11 and c.cache_files == 8 and c.size == size + c.cache_size


# --------------------------------------------------------------------------- 3) 설정 확인표

def _rule():
    return parse_rule({"id": "demo", "name": "Demo", "version": "2", "detect": {"display_name_patterns": ["Demo"]},
                       "paths": [{"path": "%APPDATA%\\Demo\\settings", "dest": "demo/s", "kind": "dir", "description": "사용자 설정"},
                                 {"path": "%APPDATA%\\Demo\\plugins", "dest": "demo/p", "kind": "dir", "description": "플러그인"},
                                 {"path": "%APPDATA%\\Demo\\keys.json", "dest": "demo/k", "kind": "file", "description": "단축키"}],
                       "exclude": ["*.lic"]})


def test_coverage_statuses(env, tmp_path):
    touch(env.appdata / "Demo" / "settings" / "a.ini")
    touch(env.appdata / "Demo" / "settings" / "x.lic")  # 라이선스 제외는 정상: 담김
    (env.appdata / "Demo" / "keys.json").mkdir(parents=True)  # 비어 있는 폴더(파일 경로가 폴더)
    out, m = _export(env, tmp_path, [], rules=[_rule()])
    by = {r["what"]: r for r in coverage.rows(m)}
    assert by["사용자 설정"]["status"] == coverage.ST_OK and by["사용자 설정"]["files"] == 1
    assert by["플러그인"]["status"] == coverage.ST_NONE and coverage.NONE_NOTE in by["플러그인"]["note"]
    assert by["단축키"]["status"] == coverage.ST_EMPTY
    shown, hidden = coverage.fold(coverage.rows(m))
    assert len(hidden) == 1 and "해당 없음 1곳" in coverage.lines(m)[-1]
    assert "  · Demo / 플러그인" in "\n".join(coverage.lines(m, expand_none=True))


def test_coverage_partial_denied_and_unreadable(env, tmp_path):
    touch(env.appdata / "Demo" / "settings" / "a.ini")
    touch(env.appdata / "Demo" / "settings" / "tool.exe")  # 복원 거부 형식 -> 일부만 담김
    touch(env.appdata / "Demo" / "plugins" / "p.dll")      # 전부 거부 -> 제외됨
    out, m = _export(env, tmp_path, [], rules=[_rule()])
    by = {r["what"]: r["status"] for r in coverage.rows(m)}
    assert by["사용자 설정"] == coverage.ST_PART and by["플러그인"] == coverage.ST_EXCL
    assert coverage.classify(found=True, files=0, failed=2, excluded=0) == coverage.ST_UNREAD
    assert coverage.classify(found=True, files=3, failed=1, excluded=0) == coverage.ST_PART


def test_coverage_rule_without_paths_is_manual(env, tmp_path):
    rules = load_rules(REPO_RULES)
    arch = [r for r in rules if r.id == "archicad"]
    out, m = _export(env, tmp_path, [], rules=arch)
    rows = coverage.rows(m)
    assert rows[0]["status"] == coverage.ST_MANUAL and rows[0]["files"] == 0


def test_coverage_old_manifest_still_readable():
    m = {"rules": [{"id": "a", "name": "A"}], "files": [{"rule": "a"}, {"rule": "a"}, {"rule": "extra"}], "skipped": []}
    rows = coverage.rows(m)
    assert rows[0]["program"] == "A" and rows[0]["files"] == 2 and rows[1]["rule"] == "extra"
    assert coverage.lines({"rules": [], "files": []}) == []


def test_plan_import_manifest_has_coverage_and_import_lines(env, tmp_path):
    touch(env.appdata / "Demo" / "settings" / "a.ini")
    out, m = _export(env, tmp_path, [], rules=[_rule()])
    plan = plan_import(out, [_rule()], env.expand)
    assert [r for r in coverage.rows(plan.manifest) if r["status"] == coverage.ST_OK]


def test_repo_rule_descriptions_are_readable_korean():
    import re
    for r in load_rules(REPO_RULES):
        for p in r.paths:
            assert re.search(r"[가-힣]", p.description) and len(p.description) >= 8, (r.id, p.path, p.description)
            for jargon in ("원문", "요약", "포럼 근거", "등급"):
                assert jargon not in p.description, (r.id, p.description)


# --------------------------------------------------------------------------- 4) 이 프로그램 설정 찾기

def prog(name, publisher="", loc=""):
    return Program(name, "1.0", publisher, "registry", "", loc)


def test_program_tokens_sources_and_stopwords():
    toks = dict(program_tokens(prog("AcmePaint Studio Pro 2024 (x64)", "Acme Software Inc.", r"C:\Tools\AcmePaint")))
    assert toks.get("acmepaint") == "이름" and "studio" not in toks and "software" not in toks
    assert toks.get("acme") == "게시자" and "pro" not in toks and "2024" not in toks


def test_find_program_local_only_and_reasons(env):
    touch(env.appdata / "AcmePaint" / "prefs.ini")
    touch(env.local / "AcmePaint" / "Cache" / "c.bin")
    touch(env.local / "AcmePaint" / "state.json")
    touch(env.pd / "AcmePaint Licenses" / "lic.dat")      # 라이선스 이름: 제외
    touch(env.appdata / "AcmePaint Logs" / "a.log")       # 로그 이름: 제외
    touch(env.appdata / "OtherVendor" / "o.ini")           # 무관
    touch(env.home / ".acmepaint" / "rc")
    touch(env.home / "Documents" / "AcmePaint" / "user.cfg")
    touch(env.appdata / "Acme" / "shared.ini")             # 게시자 폴더: 근거 약함
    p = prog("AcmePaint", "Acme")
    f = find_program(p, [], env.expand)
    paths = {Path(c.path) for c in f.candidates}
    assert env.appdata / "AcmePaint" in paths and env.local / "AcmePaint" in paths and env.home / ".acmepaint" in paths
    assert env.home / "Documents" / "AcmePaint" in paths
    assert not any("OtherVendor" in str(x) for x in paths)
    lic = next(c for c in f.candidates if "Licenses" in c.path)
    assert lic.badge == "라이선스" and lic.license_only and "옮기지 않" in lic.link.reason
    log = next(c for c in f.candidates if c.path.endswith("AcmePaint Logs"))
    assert log.badge == "로그" and log.include_caches and "[로그]" in log.link.reason
    weak = [c for c in f.candidates if c.path.endswith("Roaming" + os.sep + "Acme")]
    assert weak and weak[0].link.weak
    top = next(c for c in f.candidates if c.path == str(env.local / "AcmePaint"))
    assert top.size is not None and top.files == 2 and top.cache_files == 1
    assert "일치" in top.link.reason and "acmepaint" in top.link.reason
    assert all(c.link.grade in ("확실", "추정") for c in f.candidates)


def test_find_program_install_dir_files_are_reference_only(env, tmp_path):
    inst = tmp_path / "Program Files" / "AcmePaint"
    touch(inst / "acme.ini")
    touch(inst / "acad.pgp")
    touch(inst / "ui.cuix")
    touch(inst / "license.xml")
    touch(inst / "AcmePaint.exe")
    touch(inst / "sub" / "x.json")
    f = find_program(prog("AcmePaint", "", str(inst)), [], env.expand)
    names = {Path(x["path"]).name for x in f.install_files}
    assert names == {"acme.ini", "acad.pgp", "ui.cuix", "x.json"}
    assert not f.candidates or all(not c.path.startswith(str(inst)) for c in f.candidates)


def test_find_program_cancel_and_budget(env):
    from pcmigrate.cancel import Cancelled
    touch(env.appdata / "AcmePaint" / "prefs.ini")
    with pytest.raises(Cancelled):
        find_program(prog("AcmePaint"), [], env.expand, cancel=lambda: True)
    t = iter(range(0, 10_000, 100))
    f = find_program(prog("AcmePaint"), [], env.expand, total_limit=0.0, clock=lambda: float(next(t)))
    assert f.candidates and f.candidates[0].size is None and f.budget_hit


def test_find_program_with_rule_is_noop(env):
    rules = load_rules(REPO_RULES)
    f = find_program(prog("Autodesk Revit 2025"), rules, env.expand)
    assert f.has_rule and not f.candidates
    assert "이미 규칙" in render(f)


def test_find_program_secret_flag_and_selection_flow(env):
    touch(env.appdata / "AcmePaint" / "token.json", b"{}")
    f = find_program(prog("AcmePaint"), [], env.expand)
    assert f.candidates[0].secret  # 비밀 정보 가능성은 그대로 표시(자동 선택 없음, 선택은 기존 가드를 거친다)
    from pcmigrate.discover import build_selection
    sel = build_selection(f.as_discovery())
    assert sel["candidates"] and all(c["include"] is False for c in sel["candidates"])


def test_api_find_program_lookup(env, tmp_path):
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "x.json").write_text(json.dumps({"id": "x", "name": "X", "detect": {"display_name_patterns": ["zzz"]}}), encoding="utf-8")
    deps = Deps(registry=lambda: [prog("AcmePaint 3"), prog("AcmePaint Viewer"), prog("Solo Tool")])
    with pytest.raises(ApiError):
        run_find_program(rd, "nothing-here", deps=deps)
    with pytest.raises(ApiError) as e:
        run_find_program(rd, "acmepaint", deps=deps)
    assert "2개" in str(e.value)
    f = run_find_program(rd, "solo tool", deps=deps)  # 대소문자 무시 정확 일치
    assert f.program.name == "Solo Tool"


def test_cli_discover_program_and_export_flag(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(env.appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(env.local))
    monkeypatch.setenv("PROGRAMDATA", str(env.pd))
    monkeypatch.setenv("USERPROFILE", str(env.home))
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    touch(env.appdata / "AcmePaint" / "prefs.ini")
    monkeypatch.setattr(cli, "_deps", lambda: Deps(registry=lambda: [prog("AcmePaint")]))
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "x.json").write_text(json.dumps({"id": "x", "name": "X", "detect": {"display_name_patterns": ["zzz"]}}), encoding="utf-8")
    sel = tmp_path / "sel.json"
    rc = cli.main(["discover", "--program", "AcmePaint", "--rules", str(rd), "--output-selection", str(sel)])
    out = capsys.readouterr().out
    assert rc == 0 and "AcmePaint" in out and "근거" in out and "자동으로 선택" in out
    data = json.loads(sel.read_text(encoding="utf-8"))
    assert data["candidates"] and not any(c["include"] for c in data["candidates"])
    assert cli.build_parser().parse_args(["export", "--output", "x.zip", "--include-caches"]).include_caches is True
    assert cli.build_parser().parse_args(["export", "--output", "x.zip"]).include_caches is False


# --------------------------------------------------------------------------- 4-보강) 캐시/로그/라이선스도 찾되 표시만, 자동 선택 없음

def test_find_includes_cache_log_names_but_global_discover_does_not(env):
    from pcmigrate.discover import discover
    touch(env.local / "AcmePaint Cache" / "settings.json", b"{}")
    touch(env.appdata / "AcmePaintLogs" / "a.log")
    p = prog("AcmePaint")
    f = find_program(p, [], env.expand)
    by = {Path(c.path).name: c for c in f.candidates}
    assert by["AcmePaint Cache"].badge == "임시 저장물일 수 있음" and by["AcmePaint Cache"].include_caches
    assert by["AcmePaintLogs"].badge == "로그"
    d = discover([], [p], env.expand, with_sizes=False)
    names = {Path(c.path).name for c in d.candidates()}
    assert "AcmePaint Cache" not in names  # 기본 전역 탐색은 캐시류를 걸러 낸다


def test_find_nested_cache_settings_reference(env):
    touch(env.appdata / "AcmePaint" / "Cache" / "prefs.json", b"{}")
    touch(env.appdata / "AcmePaint" / "Logs" / "x.ini", b"i")
    touch(env.appdata / "AcmePaint" / "main.ini", b"m")
    f = find_program(prog("AcmePaint"), [], env.expand)
    names = {Path(x["path"]).name for x in f.nested}
    assert names == {"prefs.json", "x.ini"}
    assert "캐시/로그/임시 폴더도 포함해 찾았습니다" in render(f)


def test_selected_cache_named_candidate_is_exported_license_is_not(env, tmp_path):
    from pcmigrate.discover import build_selection, items_from_selection
    touch(env.local / "AcmePaint Cache" / "Cache" / "settings.json", b"{}")   # 고른 폴더 안의 Cache 도 그대로 담김
    touch(env.local / "AcmePaint Cache" / "a.ini", b"i")
    touch(env.pd / "AcmePaint Licenses" / "key.lic", b"L")
    f = find_program(prog("AcmePaint"), [], env.expand)
    sel = build_selection(f.as_discovery())
    assert all(c["include"] is False for c in sel["candidates"])  # 자동 선택 없음
    lic = [c for c in sel["candidates"] if "Licenses" in c["rel"]][0]
    assert lic["license_only"] is True
    cache = [c for c in sel["candidates"] if c["rel"] == "AcmePaint Cache"][0]
    assert cache["include_caches"] is True
    cache["include"] = True
    items = items_from_selection(sel, env.expand)
    assert items[0].include_caches is True
    out, m = _export(env, tmp_path, items)
    rels = {x["rel"] for x in m["files"]}
    assert rels == {"AcmePaint Cache/Cache/settings.json", "AcmePaint Cache/a.ini"}
    assert not any(".lic" in r for r in rels)
    # 선택하지 않으면 담기지 않는다
    out2, m2 = _export(env, tmp_path / "x", [])
    assert not m2["files"]


def test_license_files_inside_selected_folder_still_excluded_by_global_rule(env):
    touch(env.appdata / "AcmePaint" / "k.lic", b"L")
    touch(env.appdata / "AcmePaint" / "ok.ini", b"i")
    items, skipped = collect_extra_items([extra(env, "AcmePaint")], env.expand)
    assert [i.rel for i in items] == ["AcmePaint/ok.ini"] and any(s["path"].endswith("k.lic") for s in skipped)
