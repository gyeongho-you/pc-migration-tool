"""stage28b: 허용 방식(설정 파일만) 내보내기, 프로그램별 복원 선택(only/skip), 프로그램별 되돌리기(undo-import).
모두 tmp_path 의 가짜 환경만 쓴다(이 PC 의 실제 사용자/프로그램 정보는 쓰지 않는다)."""
from conftest import patch_pkg, set_pkg
import hashlib
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import api, cli, coverage, envpaths, import_record, package, settings_filter
from pcmigrate.api import ApiError, Deps, run_export, run_import, run_undo_import
from pcmigrate.detect import Program
from pcmigrate.discover import Candidate, ExtraItem
from pcmigrate.find_program import config_line, fill_config_estimate, find_program, render
from pcmigrate.package import (EXTRA_ID, NOT_SELECTED, PackageError, apply_import, collect_extra_items, export_package,
                               plan_import, program_key, program_matches, retry_failed_export, verify_package)
from pcmigrate.rules import parse_rule
from pcmigrate.settings_filter import (ALLOWED_CONFIG_EXTS, ConfigGate, ConfigLimits, EXTRA_MODE_ALL, EXTRA_MODE_CONFIG)
from test_export_locked_files import ByteLock

winonly = pytest.mark.skipif(os.name != "nt", reason="바이트 범위 잠금은 Windows 전용 시나리오")
NOW = lambda: __import__("datetime").datetime(2030, 1, 1, tzinfo=__import__("datetime").timezone.utc)  # noqa: E731


def touch(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class PC:
    """가짜 PC 한 대(AppData 등)."""

    def __init__(self, tmp: Path, name: str):
        self.home = tmp / name
        self.appdata = self.home / "AppData" / "Roaming"
        self.local = self.home / "AppData" / "Local"
        self.pd = self.home / "ProgramData"
        for p in (self.appdata, self.local, self.pd, self.home / "Documents"):
            p.mkdir(parents=True, exist_ok=True)

    def expand(self, s):
        return (s.replace("%APPDATA%", str(self.appdata)).replace("%LOCALAPPDATA%", str(self.local))
                 .replace("%PROGRAMDATA%", str(self.pd)).replace("%USERPROFILE%", str(self.home)))


def rule_data(rid, folder):
    return {"id": rid, "name": rid.title(), "version": "1", "detect": {"display_name_patterns": [rid]},
            "paths": [{"path": f"%APPDATA%\\{folder}", "dest": rid, "kind": "dir"}], "exclude": [], "manual_warnings": []}


@pytest.fixture
def pcs(tmp_path):
    old, new = PC(tmp_path, "old"), PC(tmp_path, "new")
    return SimpleNamespace(tmp=tmp_path, old=old, new=new, out=tmp_path / "out" / "p.zip")


def acme_tree(pc: PC):
    """가짜 프로그램 폴더: 설정 형식 + 정크(dat/bin/cache/log/exe/lic/txt)가 섞여 있다."""
    b = pc.appdata / "AcmePaint"
    touch(b / "settings.json", b"{" + b'"a":1' + b"}")
    touch(b / "config.ini", b"[x]\na=1\n")
    touch(b / "prefs.xml", b"<p/>")
    touch(b / "ui" / "layout.cuix", b"u" * 300)
    touch(b / "data.dat", b"d" * 5000)
    touch(b / "cache.bin", b"b" * 3000)
    touch(b / "debug.log", b"l" * 500)
    touch(b / "notes.txt", b"n" * 100)
    touch(b / "tool.exe", b"MZ")
    touch(b / "license.lic", b"LIC")
    touch(b / "Cache" / "x.json", b"{}")
    return b


def ex(pc: PC, rel="AcmePaint", root="%APPDATA%"):
    return ExtraItem(root, rel, Path(pc.expand(root)) / rel, True)


# --------------------------------------------------------------------------- 1. 허용 방식

def test_allowed_table_and_reg_excluded():
    assert ".reg" not in ALLOWED_CONFIG_EXTS and ".exe" not in ALLOWED_CONFIG_EXTS and ".dll" not in ALLOWED_CONFIG_EXTS
    for e in (".ini", ".json", ".xml", ".cfg", ".conf", ".config", ".yaml", ".yml", ".toml", ".properties", ".pgp", ".cuix",
              ".dwt", ".rte", ".rft", ".ctb", ".stb", ".pc3"):
        assert e in ALLOWED_CONFIG_EXTS
    assert {g for g, _ in ALLOWED_CONFIG_EXTS.values()} <= {"A", "B", "C"}
    # 허용 확장자는 복원 거부 확장자와 겹치지 않는다(허용했는데 복원이 막히는 일이 없게)
    assert not set(ALLOWED_CONFIG_EXTS) & package.DENY_EXTENSIONS


def test_config_vs_all_size_and_count(pcs):
    acme_tree(pcs.old)
    items_c, sk_c = collect_extra_items([ex(pcs.old)], pcs.old.expand, extra_mode="config")
    items_a, sk_a = collect_extra_items([ex(pcs.old)], pcs.old.expand, extra_mode="all")
    names_c = {i.src.name for i in items_c}
    names_a = {i.src.name for i in items_a}
    assert names_c == {"settings.json", "config.ini", "prefs.xml", "layout.cuix"}
    assert names_a == names_c | {"data.dat", "cache.bin", "debug.log", "notes.txt"}
    assert "tool.exe" not in names_a and "license.lic" not in names_a and "x.json" not in names_a  # 보안/라이선스/캐시는 두 방식 모두 제외
    size = lambda its: sum(i.src.stat().st_size for i in its)  # noqa: E731
    assert size(items_c) < size(items_a) and len(items_c) < len(items_a)
    nc = [s for s in sk_c if s.get("not_config")]
    assert len(nc) == 1 and nc[0]["files"] == 4 and nc[0]["bytes"] == 5000 + 3000 + 500 + 100
    assert nc[0]["by_ext"][".dat"] == [1, 5000]
    assert not any(s.get("not_config") for s in sk_a)
    assert default_is_config()


def default_is_config():
    return settings_filter.DEFAULT_EXTRA_MODE == "config"


def test_collect_extra_items_rejects_bad_mode(pcs):
    with pytest.raises(ValueError):
        collect_extra_items([], pcs.old.expand, extra_mode="x")


def test_limits_record_reason_and_keep_order(pcs):
    b = pcs.old.appdata / "Big"
    for i in range(6):
        touch(b / f"f{i}.json", b"x" * 100)
    touch(b / "huge.json", b"y" * 1000)
    e = ex(pcs.old, "Big")
    items, sk = collect_extra_items([e], pcs.old.expand, limits=ConfigLimits(file_bytes=500, folder_bytes=10_000, folder_files=4))
    assert [i.src.name for i in items] == ["f0.json", "f1.json", "f2.json", "f3.json"]  # 정렬 순서대로 상한까지
    lim = [s for s in sk if s.get("limit")]
    assert len(lim) == 1 and lim[0]["files"] == 3 and lim[0]["kinds"] == {"file_size": 1, "folder_files": 2}
    assert "huge.json" in "".join(lim[0]["examples"]) or lim[0]["examples"]
    items2, sk2 = collect_extra_items([e], pcs.old.expand, limits=ConfigLimits(file_bytes=500, folder_bytes=250, folder_files=99))
    assert [i.src.name for i in items2] == ["f0.json", "f1.json"]
    assert [s for s in sk2 if s.get("limit")][0]["kinds"]["folder_bytes"] == 4


def test_gate_judge_names():
    g = ConfigGate()
    assert g.judge("desktop.ini", 1) == settings_filter.NOT_CONFIG
    assert g.judge("My License.json", 1) == settings_filter.NOT_CONFIG
    assert g.judge("Preferences", 1) == settings_filter.NOT_CONFIG  # 확장자 없는 파일은 허용 방식에서 빠진다(--extra-mode all 로 포함)
    assert g.judge(".gitconfig", 1) is None and g.judge("A.JSON", 1) is None
    assert g.ext_counts == {".gitconfig": 1, ".json": 1} or g.ext_counts[".json"] == 1


def test_export_manifest_coverage_and_lines(pcs):
    acme_tree(pcs.old)
    m = export_package([], pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW)
    assert m["extra_mode"] == "config" and m["extra_limits"]["folder_files"] == settings_filter.MAX_FOLDER_FILES
    assert {f["rel"].split("/")[-1] for f in m["files"]} == {"settings.json", "config.ini", "prefs.xml", "layout.cuix"}
    c = m["coverage"][0]
    assert c["config_mode"] and c["allowed_files"] == 4 and c["ext_counts"] == {".cuix": 1, ".ini": 1, ".json": 1, ".xml": 1}
    assert c["not_config_files"] == 4 and c["status"] == coverage.ST_PART  # tool.exe(보안 거부)가 있어서 일부만 담김
    t = coverage.config_totals(m)
    assert t["items"] == 1 and t["not_config_bytes"] == 8600
    text = "\n".join(coverage.config_lines(m))
    assert "허용 방식" in text and ".json 1개" in text and "제외된 파일(설정 형식이 아님) 4개" in text and "--extra-mode all" in text
    row = coverage.rows(m)[0]
    assert row["ext_counts"][".ini"] == 1 and "설정 형식이 아니라 제외 4개" in row["note"]
    # manifest 호환: 기존 키는 그대로, 패키지 검사도 통과
    assert verify_package(pcs.out)[1] == [] and {"program", "what", "status", "files", "note"} <= set(row)
    old_style = {k: v for k, v in m.items() if k not in ("extra_mode", "extra_limits")}
    for cc in old_style["coverage"]:
        for k in ("config_mode", "allowed_files", "ext_counts"):
            cc.pop(k, None)
    assert coverage.config_lines(old_style) == [] and coverage.rows(old_style)[0]["files"] == 4  # 이전 형식 패키지도 읽힌다


def test_status_not_config_alone_is_not_partial(pcs):
    touch(pcs.old.appdata / "OnlyJunk" / "a.dat", b"d" * 10)
    touch(pcs.old.appdata / "Mixed" / "a.dat", b"d" * 10)
    touch(pcs.old.appdata / "Mixed" / "a.ini", b"[x]")
    cov = []
    collect_extra_items([ex(pcs.old, "OnlyJunk"), ex(pcs.old, "Mixed")], pcs.old.expand, cov=cov)
    assert [c["status"] for c in cov] == [coverage.ST_EXCL, coverage.ST_OK]  # 설정 형식 파일이 없으면 '제외됨', 일부 제외는 '담김'
    assert cov[0]["files"] == 0 and cov[0]["not_config_files"] == 1


def test_export_all_mode_matches_old_behaviour(pcs):
    acme_tree(pcs.old)
    m = export_package([], pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW, extra_mode="all")
    assert m["extra_mode"] == "all" and "extra_limits" not in m
    assert {"data.dat", "cache.bin", "debug.log", "notes.txt"} <= {f["rel"].split("/")[-1] for f in m["files"]}
    assert coverage.config_lines(m) == []
    with pytest.raises(ValueError):
        export_package([], pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], extra_mode="nope")


def test_rules_unaffected_by_extra_mode(pcs):
    touch(pcs.old.appdata / "Demo" / "x.dat", b"d" * 10)
    rule = parse_rule(rule_data("demo", "Demo"))
    m = export_package([rule], pcs.out, [], None, expand=pcs.old.expand, now=NOW)  # 규칙 경로는 허용 방식과 무관: .dat 도 담는다
    assert [f["rel"] for f in m["files"]] == ["Demo/x.dat"]


def test_fingerprint_includes_mode():
    fp_c, info_c = package._fingerprint([], [], "auto", False, "config")
    fp_a, info_a = package._fingerprint([], [], "auto", False, "all")
    fp_c2, _ = package._fingerprint([], [], "auto", False)
    assert fp_c != fp_a and fp_c == fp_c2
    assert "허용 방식" in package._fp_reason(info_c, info_a)


def test_resume_with_other_mode_restarts(pcs, monkeypatch):
    acme_tree(pcs.old)
    part, jpath = package.part_paths(pcs.out)
    pcs.out.parent.mkdir(parents=True)
    # 같은 설정으로 중단된 저장점이 있어도 모드가 다르면 이어받지 않는다
    orig = package._write_entry
    calls = {"n": 0}

    def boom(zf, arc, src, stage=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return orig(zf, arc, src, stage)
    patch_pkg(monkeypatch, "_write_entry", boom)
    with pytest.raises(KeyboardInterrupt):
        export_package([], pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW,
                       savepoint_bytes=1)
    assert part.exists()
    patch_pkg(monkeypatch, "_write_entry", orig)
    m = export_package([], pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW, extra_mode="all")
    assert not m["resume"]["resumed"] and "허용 방식" in m["resume"]["reason"]
    # 같은 모드로 이어받기는 그대로 된다
    pcs.out.unlink()
    patch_pkg(monkeypatch, "_write_entry", boom)
    calls["n"] = 0
    with pytest.raises(KeyboardInterrupt):
        export_package([], pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW, savepoint_bytes=1)
    patch_pkg(monkeypatch, "_write_entry", orig)
    m2 = export_package([], pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW)
    assert m2["resume"]["resumed"] and verify_package(pcs.out)[1] == []


@winonly
def test_retry_failed_config_mode_and_all_mode(pcs):
    acme_tree(pcs.old)
    target = pcs.old.appdata / "AcmePaint" / "settings.json"
    with ByteLock(target, 2):
        m0 = export_package([], pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW)
    assert [f["reason"] for f in m0["failed"]] == ["in_use"]
    m = retry_failed_export(pcs.out, [], pcs.old.expand, now=NOW)
    assert [a["src"] for a in m["retry"]["added"]] == [str(target)]
    names = {f["rel"].split("/")[-1] for f in m["files"]}
    assert names == {"settings.json", "config.ini", "prefs.xml", "layout.cuix"} and m["extra_mode"] == "config"  # 정크는 다시 담기에서도 안 들어온다
    assert verify_package(pcs.out)[1] == []
    # 전체 방식 묶음: 잠긴 .dat 도 다시 담긴다
    out2 = pcs.tmp / "out" / "all.zip"
    dat = pcs.old.appdata / "AcmePaint" / "data.dat"
    with ByteLock(dat, 2):
        export_package([], out2, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW, extra_mode="all")
    m2 = retry_failed_export(out2, [], pcs.old.expand, now=NOW)
    assert [a["src"] for a in m2["retry"]["added"]] == [str(dat)]


def test_retry_old_package_without_mode_uses_all(pcs):
    acme_tree(pcs.old)
    out = pcs.out
    export_package([], out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW, extra_mode="all")
    # extra_mode 기록이 없는 이전 버전 묶음 흉내: 기록을 지우고 해시/목록은 그대로
    with zipfile.ZipFile(out) as zf:
        data = {i.filename: zf.read(i) for i in zf.infolist()}
    man = json.loads(data["manifest.json"])
    man.pop("extra_mode")
    data["manifest.json"] = json.dumps(man).encode()
    with zipfile.ZipFile(out, "w") as zf:
        for k, v in data.items():
            zf.writestr(k, v)
    got = {}
    orig = package.collect_extra_items

    def spy(*a, **kw):
        got.update(kw)
        return orig(*a, **kw)
    set_pkg("collect_extra_items", spy)
    try:
        # 읽지 못한 파일이 없으니 다시 담을 것이 없다: 바로 돌려준다(예외 없이)
        retry_failed_export(out, [], pcs.old.expand, now=NOW)
    finally:
        set_pkg("collect_extra_items", orig)


# --------------------------------------------------------------------------- 1b. 설정 찾기 후보

def test_estimate_matches_collect(pcs):
    acme_tree(pcs.old)
    est = package.estimate_extra_config("%APPDATA%", "AcmePaint", pcs.old.appdata / "AcmePaint", pcs.old.expand)
    items, sk = collect_extra_items([ex(pcs.old)], pcs.old.expand)
    assert est.files == len(items) == 4 and est.bytes == sum(i.src.stat().st_size for i in items)
    nc = next(s for s in sk if s.get("not_config"))
    assert est.excluded_files == nc["files"] and est.excluded_bytes == nc["bytes"] and est.complete
    assert est.ext_counts == {".json": 1, ".ini": 1, ".xml": 1, ".cuix": 1}
    est2 = package.estimate_extra_config("%APPDATA%", "AcmePaint", pcs.old.appdata / "AcmePaint", pcs.old.expand,
                                         limits=ConfigLimits(folder_files=2))
    assert est2.files == 2 and est2.limit_files == 2


def test_estimate_deadline_marks_incomplete(pcs):
    b = pcs.old.appdata / "Many"
    for i in range(250):
        touch(b / f"f{i:03}.json", b"{}")
    est = package.estimate_extra_config("%APPDATA%", "Many", b, pcs.old.expand, deadline=0.0, clock=lambda: 1.0)
    assert not est.complete and est.files < 250


def test_find_program_candidates_get_config_numbers(pcs):
    acme_tree(pcs.old)
    f = find_program(Program("AcmePaint", "1.0", "Acme Inc"), [], pcs.old.expand)
    c = next(c for c in f.candidates if c.path.endswith("AcmePaint"))
    assert c.config_files == 4 and c.config_excluded_files == 4 and c.config_complete
    assert c.config_ext_counts[".json"] == 1 and c.config_bytes < (c.size or 10 ** 9)
    line = config_line(c)
    assert "허용 방식으로 담길 파일 4개" in line and "빠질 파일 4개" in line
    assert "허용 방식으로 담길 파일 4개" in render(f)
    assert json.loads(render(f, "json"))["candidates"][0]["config_files"] == 4


def test_fill_config_estimate_unknown_dir_is_empty(pcs):
    c = Candidate("%APPDATA%", "Nope", str(pcs.old.appdata / "Nope"), True)
    fill_config_estimate(c, pcs.old.expand, 10 ** 12)
    assert c.config_files == 0 and config_line(c).startswith("허용 방식으로 담길 파일 0개")


# --------------------------------------------------------------------------- 2. 프로그램별 복원

@pytest.fixture
def two_rules(pcs):
    """규칙 2개 + 규칙 없는 항목 1개를 담은 패키지(구 PC)와, 신 PC 에 이미 있는 파일."""
    touch(pcs.old.appdata / "Alpha" / "a.cfg", b"alpha-new")
    touch(pcs.old.appdata / "Alpha" / "b.cfg", b"alpha-b")
    touch(pcs.old.appdata / "Beta" / "c.cfg", b"beta-new")
    acme_tree(pcs.old)
    rules = [parse_rule(rule_data("alpha", "Alpha")), parse_rule(rule_data("beta", "Beta"))]
    export_package(rules, pcs.out, [], None, expand=pcs.old.expand, extras=[ex(pcs.old)], now=NOW)
    touch(pcs.new.appdata / "Alpha" / "a.cfg", b"alpha-OLD")     # 덮어쓸 파일
    touch(pcs.new.appdata / "Beta" / "c.cfg", b"beta-OLD")
    touch(pcs.new.appdata / "AcmePaint" / "settings.json", b"acme-OLD")
    return SimpleNamespace(rules=rules, pcs=pcs)


def test_program_key_and_matches():
    assert program_key({"rule": "alpha"}) == "alpha"
    assert program_key({"rule": "extra", "base": "%APPDATA%", "rel": "AcmePaint/ui/x.json"}) == "extra:appdata/AcmePaint"
    assert program_matches("ALPHA", "alpha") and not program_matches("alp", "alpha")
    assert program_matches("extra", "extra:appdata/AcmePaint") and program_matches("extra:AcmePaint", "extra:appdata/AcmePaint")
    assert program_matches("extra:appdata/acmepaint", "extra:appdata/AcmePaint") and not program_matches("extra:Other", "extra:appdata/AcmePaint")
    assert not program_matches("extra", "alpha")


def test_plan_programs_table(two_rules):
    p = two_rules.pcs
    plan = plan_import(p.out, two_rules.rules, p.new.expand)
    assert plan.problems == []
    rows = {r["id"]: r for r in plan.programs}
    assert set(rows) == {"alpha", "beta", "extra:appdata/AcmePaint"}
    assert rows["alpha"]["files"] == 2 and rows["alpha"]["overwrite"] == 1 and rows["alpha"]["new"] == 1 and rows["alpha"]["selected"]
    assert rows["extra:appdata/AcmePaint"]["files"] == 4 and rows["extra:appdata/AcmePaint"]["overwrite"] == 1
    assert rows["alpha"]["name"] == "Alpha" and rows["extra:appdata/AcmePaint"]["kind"] == "extra"
    assert all(it["program"] for it in plan.items)


def test_only_and_skip_do_not_write_skipped(two_rules):
    p = two_rules.pcs
    plan = plan_import(p.out, two_rules.rules, p.new.expand, only_programs="alpha")
    st = {}
    for it in plan.items:
        st.setdefault(it["program"], set()).add(it["status"])
    assert st["alpha"] == {"would_overwrite", "would_copy"} and st["beta"] == {NOT_SELECTED}
    rep = apply_import(p.out, plan)
    counts = rep.to_dict()["summary"]
    assert counts["copied"] == 2 and counts[NOT_SELECTED] == 5 and rep.failures == []
    assert (p.new.appdata / "Alpha" / "a.cfg").read_bytes() == b"alpha-new"
    assert (p.new.appdata / "Beta" / "c.cfg").read_bytes() == b"beta-OLD"           # 선택에서 뺀 프로그램은 그대로
    assert not (p.new.appdata / "Beta" / "c.cfg.bak").exists()
    assert (p.new.appdata / "AcmePaint" / "settings.json").read_bytes() == b"acme-OLD"
    assert not (p.new.appdata / "AcmePaint" / "config.ini").exists()
    assert next(r for r in plan.programs if r["id"] == "beta")["selected"] is False
    # skip: 나머지는 복원, 건너뛴 것만 빠진다
    p.new.appdata.joinpath("Alpha", "a.cfg").write_bytes(b"alpha-OLD")
    plan2 = plan_import(p.out, two_rules.rules, p.new.expand, skip_programs=["beta", "extra"])
    rep2 = apply_import(p.out, plan2)
    assert rep2.to_dict()["summary"].get("copied", 0) == 1 and (p.new.appdata / "Beta" / "c.cfg").read_bytes() == b"beta-OLD"
    # 둘 다: only 후 skip
    plan3 = plan_import(p.out, two_rules.rules, p.new.expand, only_programs="alpha,beta", skip_programs="alpha")
    assert {r["id"] for r in plan3.programs if r["selected"]} == {"beta"}


def test_unknown_program_names_and_api(two_rules):
    p = two_rules.pcs
    deps = Deps(plan=lambda path, rules, **kw: plan_import(path, rules, p.new.expand, **kw))
    rd = p.tmp / "rules"
    rd.mkdir()
    for r in ("alpha", "beta"):
        (rd / f"{r}.json").write_text(json.dumps(rule_data(r, r.title())), encoding="utf-8")
    with pytest.raises(ApiError) as ei:
        run_import(p.out, rd, dry_run=True, deps=deps, only_programs="gamma")
    assert "gamma" in ei.value.message and any("alpha" in x for x in ei.value.lines)
    r = run_import(p.out, rd, dry_run=True, deps=deps, only_programs="alpha")
    d = r.preview_dict()
    assert d["selection"] == {"only": ["alpha"], "skip": []} and d["summary"][NOT_SELECTED] == 5
    assert {x["id"] for x in d["programs"]} == {"alpha", "beta", "extra:appdata/AcmePaint"} and r.programs == d["programs"]
    # 선택에서 뺀 extra 는 --allow-extra 를 요구하지 않는다
    r2 = run_import(p.out, rd, yes=True, deps=deps, only_programs="alpha", record_dir=p.tmp / "rec")
    assert r2.mode == "applied" and r2.n_extra == 0
    r3 = run_import(p.out, rd, yes=True, deps=deps, record_dir=p.tmp / "rec")
    assert r3.mode == "needs-allow-extra"


def test_cli_import_only_table_and_unknown(two_rules, capsys, tmp_path):
    p = two_rules.pcs
    rd = tmp_path / "rules"
    rd.mkdir()
    for r in ("alpha", "beta"):
        (rd / f"{r}.json").write_text(json.dumps(rule_data(r, r.title())), encoding="utf-8")
    try:
        base = ["--env", f"APPDATA={p.new.appdata}", "--env", f"LOCALAPPDATA={p.new.local}", "--env", f"USERPROFILE={p.new.home}",
                "--env", f"PROGRAMDATA={p.new.pd}"]
        assert cli.main(base + ["import", str(p.out), "--dry-run", "--only", "alpha", "--rules", str(rd)]) == 0
        out = capsys.readouterr().out
        assert "프로그램별" in out and "alpha (Alpha) / 2개 / 새로 1 / 덮어씀 1" in out and "beta (Beta) / 1개" in out
        assert "선택에서 제외됨" in out
        assert cli.main(base + ["import", str(p.out), "--dry-run", "--only", "alpha", "--json", "--rules", str(rd)]) == 0
        j = json.loads(capsys.readouterr().out)
        assert j["programs"][0]["id"] == "alpha" and j["summary"]["not_selected"] == 5
        assert cli.main(base + ["import", str(p.out), "--dry-run", "--skip", "nonesuch", "--rules", str(rd)]) == 1
        assert "nonesuch" in capsys.readouterr().err
    finally:
        envpaths.set_overrides(None)


# --------------------------------------------------------------------------- 3. 되돌리기

def deps_new(p):
    return Deps(plan=lambda path, rules, **kw: plan_import(path, rules, p.new.expand, **kw))


@pytest.fixture
def restored(two_rules, tmp_path):
    p = two_rules.pcs
    rd = tmp_path / "rules"
    rd.mkdir()
    for r in ("alpha", "beta"):
        (rd / f"{r}.json").write_text(json.dumps(rule_data(r, r.title())), encoding="utf-8")
    recdir = tmp_path / "records"
    res = run_import(p.out, rd, yes=True, allow_extra=True, deps=deps_new(p), record_dir=recdir)
    assert res.mode == "applied" and res.record_path and not res.record_error
    return SimpleNamespace(p=p, rd=rd, recdir=recdir, res=res, path=Path(res.record_path))


def test_record_format(restored):
    rec = json.loads(restored.path.read_text(encoding="utf-8"))
    assert rec["format"] == "pcmigrate-import-record" and rec["version"] == 1 and rec["created"]
    assert {e["program"] for e in rec["entries"]} == {"alpha", "beta", "extra:appdata/AcmePaint"}
    a = next(e for e in rec["entries"] if e["target"].endswith("a.cfg"))
    b = next(e for e in rec["entries"] if e["target"].endswith("b.cfg"))
    assert a["backup"].endswith("a.cfg.bak") and a["backup_sha256"] == hashlib.sha256(b"alpha-OLD").hexdigest()
    assert a["new_sha256"] == hashlib.sha256(b"alpha-new").hexdigest() and a["undone"] is False
    assert b["backup"] == "" and rec["programs"] and restored.res.report.meta["import_record"] == str(restored.path)
    assert import_record.load_record(restored.path)["entries"]
    assert import_record.latest_record(restored.recdir) == restored.path


def test_undo_dry_run_changes_nothing_and_needs_yes(restored):
    p = restored.p
    before = (p.new.appdata / "Alpha" / "a.cfg").read_bytes()
    r = run_undo_import("alpha", record_dir=restored.recdir, dry_run=True)
    assert r.mode == "dry-run" and r.plan.summary()["restore"] == 1 and r.plan.summary()["new_kept"] == 1
    r = run_undo_import("alpha", record_dir=restored.recdir)
    assert r.mode == "needs-confirm"
    assert (p.new.appdata / "Alpha" / "a.cfg").read_bytes() == before and (p.new.appdata / "Alpha" / "a.cfg.bak").exists()
    assert json.loads(restored.path.read_text(encoding="utf-8"))["entries"][0]["undone"] is False


def test_undo_restores_bak_keeps_new_files(restored):
    p = restored.p
    r = run_undo_import("alpha", record_dir=restored.recdir, yes=True)
    assert r.mode == "applied" and r.summary_dict()["restored"] == 1
    assert (p.new.appdata / "Alpha" / "a.cfg").read_bytes() == b"alpha-OLD"
    assert not (p.new.appdata / "Alpha" / "a.cfg.bak").exists()
    assert (p.new.appdata / "Alpha" / "b.cfg").read_bytes() == b"alpha-b"          # 새로 만든 파일은 지우지 않는다
    assert (p.new.appdata / "Beta" / "c.cfg").read_bytes() == b"beta-new"          # 다른 프로그램은 그대로
    again = run_undo_import("alpha", record_dir=restored.recdir, yes=True)           # 두 번째: 이미 되돌림
    assert again.mode == "nothing" and again.plan.summary()["already_undone"] == 1
    assert (p.new.appdata / "Alpha" / "a.cfg").read_bytes() == b"alpha-OLD"


def test_undo_move_new_does_not_delete(restored):
    p = restored.p
    r = run_undo_import("alpha", record_dir=restored.recdir, yes=True, move_new=True)
    assert r.summary_dict()["moved_new"] == 1
    assert not (p.new.appdata / "Alpha" / "b.cfg").exists()
    assert (p.new.appdata / "Alpha" / "b.cfg.removed").read_bytes() == b"alpha-b"


def test_undo_refuses_changed_or_missing_bak(restored):
    p = restored.p
    bak = p.new.appdata / "Alpha" / "a.cfg.bak"
    bak.write_bytes(b"someone edited me")
    r = run_undo_import("alpha", record_dir=restored.recdir, yes=True)
    assert r.mode == "nothing" and [i.action for i in r.plan.problems] == ["bak_changed"]
    assert (p.new.appdata / "Alpha" / "a.cfg").read_bytes() == b"alpha-new" and bak.read_bytes() == b"someone edited me"
    bak.unlink()
    r = run_undo_import("alpha", record_dir=restored.recdir, yes=True)
    assert [i.action for i in r.plan.problems] == ["bak_missing"] and "없습니다" in r.plan.problems[0].detail
    assert (p.new.appdata / "Alpha" / "a.cfg").read_bytes() == b"alpha-new"


def test_undo_refuses_when_target_edited_after_restore(restored):
    p = restored.p
    (p.new.appdata / "Beta" / "c.cfg").write_bytes(b"user edit after restore")
    r = run_undo_import("beta", record_dir=restored.recdir, yes=True)
    assert [i.action for i in r.plan.problems] == ["target_changed"]
    assert (p.new.appdata / "Beta" / "c.cfg").read_bytes() == b"user edit after restore"
    assert (p.new.appdata / "Beta" / "c.cfg.bak").read_bytes() == b"beta-OLD"


def test_undo_rejects_tampered_record(restored, tmp_path):
    rec = json.loads(restored.path.read_text(encoding="utf-8"))
    victim = tmp_path / "elsewhere" / "v.cfg"
    touch(victim, b"keep")
    for e in rec["entries"]:
        if e["program"] == "alpha" and e["backup"]:
            e["target"] = str(victim)           # 허용 루트 밖으로 바꾼 기록
    restored.path.write_text(json.dumps(rec), encoding="utf-8")
    r = run_undo_import("alpha", record=restored.path, yes=True)
    assert any(i.action == "unsafe" for i in r.plan.items) and victim.read_bytes() == b"keep"


def test_undo_errors(restored, tmp_path):
    with pytest.raises(ApiError, match="--only"):
        run_undo_import(None, record_dir=restored.recdir)
    with pytest.raises(ApiError, match="복원 기록이 없습니다"):
        run_undo_import("alpha", record_dir=tmp_path / "empty")
    with pytest.raises(ApiError, match="복원 기록에 없는 프로그램") as ei:
        run_undo_import("gamma", record_dir=restored.recdir)
    assert any("alpha" in x for x in ei.value.lines)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ApiError, match="읽을 수 없습니다"):
        run_undo_import("alpha", record=bad)
    bad.write_text(json.dumps({"format": "other"}), encoding="utf-8")
    with pytest.raises(ApiError, match="형식"):
        run_undo_import("alpha", record=bad)


def test_undo_extra_item_by_short_name(restored):
    p = restored.p
    r = run_undo_import("extra:AcmePaint", record_dir=restored.recdir, yes=True)
    assert (p.new.appdata / "AcmePaint" / "settings.json").read_bytes() == b"acme-OLD"
    assert r.summary_dict()["restored"] == 1 and r.plan.summary()["new_kept"] == 3


def test_cli_undo_import(restored, capsys):
    p = restored.p
    base = ["undo-import", "--only", "alpha", "--record-dir", str(restored.recdir)]
    assert cli.main(base + ["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "미리보기" in out and "새 파일 1개는 지우지 않았습니다" in out
    assert cli.main(base) == api.NEEDS_CONFIRM
    assert "--yes" in capsys.readouterr().out
    assert (p.new.appdata / "Alpha" / "a.cfg").read_bytes() == b"alpha-new"
    assert cli.main(base + ["--yes", "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["restored"] == 1 and j["new_kept"] == 1 and (p.new.appdata / "Alpha" / "a.cfg").read_bytes() == b"alpha-OLD"
    (p.new.appdata / "Beta" / "c.cfg.bak").write_bytes(b"tampered")
    assert cli.main(["undo-import", "--only", "beta", "--record-dir", str(restored.recdir), "--yes"]) == 1
    assert "달라졌습니다" in capsys.readouterr().out
    assert cli.main(["undo-import", "--only", "zzz", "--record-dir", str(restored.recdir)]) == 1
    with pytest.raises(SystemExit):  # --only 필수(argparse)
        cli.main(["undo-import", "--record-dir", str(restored.recdir)])


def test_cli_export_extra_mode_option(pcs, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [])  # 이 PC 의 설치 목록/winget 을 읽지 않는다
    monkeypatch.setattr(cli, "winget_export_raw", lambda: None)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: None)
    acme_tree(pcs.old)
    sel = tmp_path / "sel.json"
    sel.write_text(json.dumps({"format": 1, "candidates": [{"include": True, "root": "%APPDATA%", "rel": "AcmePaint"}]}), encoding="utf-8")
    rd = tmp_path / "rules0"
    from conftest import make_rules_dir
    make_rules_dir(rd)
    try:
        base = ["--env", f"APPDATA={pcs.old.appdata}", "--env", f"LOCALAPPDATA={pcs.old.local}", "--env", f"USERPROFILE={pcs.old.home}",
                "--env", f"PROGRAMDATA={pcs.old.pd}"]
        out1, out2 = tmp_path / "c.zip", tmp_path / "a.zip"
        for out, mode in ((out1, []), (out2, ["--extra-mode", "all"])):
            monkey_rc = cli.main(base + ["export", "--output", str(out), "--extra", str(sel), "--rules", str(rd), "--json", *mode])
            assert monkey_rc == 0, capsys.readouterr()
            j = json.loads(capsys.readouterr().out)
            assert j["files"] == (4 if not mode else 8)
        assert len(zipfile.ZipFile(out1).namelist()) < len(zipfile.ZipFile(out2).namelist())  # manifest 예시 때문에 작은 묶음은 크기 비교 불가
    finally:
        envpaths.set_overrides(None)


def test_api_extra_mode_validation_and_passthrough(tmp_path):
    from conftest import make_rules_dir
    rd = make_rules_dir(tmp_path / "r")
    with pytest.raises(ApiError, match="--extra-mode"):
        run_export(rd, tmp_path / "o.zip", deps=Deps(registry=lambda: [], winget_export=lambda: None), extra_mode="bad")
    seen = {}

    def fake(rules, out, reg, winget, **kw):
        seen.update(kw)
        return {"rules": [], "files": [], "skipped": [], "failed": [], "coverage": [], "manual_warnings": []}
    deps = Deps(registry=lambda: [], winget_export=lambda: None, export=fake)
    run_export(rd, tmp_path / "o.zip", deps=deps, extra_mode="all")
    assert seen["extra_mode"] == "all"
    seen.clear()
    run_export(rd, tmp_path / "o.zip", deps=deps)
    assert "extra_mode" not in seen  # 기본값은 넘기지 않는다(받지 못하는 구현과 호환)
