"""stage32: 허용 방식에서 빠진 파일의 '큰 것 예시 + 사유 분류' 기록/표시. 가짜 폴더만 쓴다(이 PC 의 실제 정보 없음)."""
import json
import time

import pytest

from pcmigrate import cli, coverage, envpaths, package, settings_filter as sf
from pcmigrate.package import collect_extra_items, export_package, plan_import, verify_package
from test_per_program_restore_undo import NOW, PC, ex, touch


@pytest.fixture
def pc(tmp_path):
    return PC(tmp_path, "old")


def big_tree(pc, n_db=12, n_noext=5, n_img=7):
    b = pc.appdata / "BigApp"
    touch(b / "settings.json", b"{}")
    for i in range(n_db):
        touch(b / "db" / f"d{i}.db", b"d" * (100 + i))
    for i in range(n_noext):
        touch(b / f"state{i}", b"s" * (50 + i))
    for i in range(n_img):
        touch(b / "img" / f"p{i}.png", b"p" * (1000 + i))
    touch(b / "model.rvt", b"m" * 9000)
    touch(b / "a.ttf", b"f" * 40)
    touch(b / "readme.txt", b"t" * 30)
    touch(b / "run.log", b"l" * 20)
    touch(b / "license.dat", b"L" * 10)
    touch(b / "x.weird", b"w" * 5)
    return b


# --------------------------------------------------------------------------- 사유 분류
def test_why_of_table():
    w = sf
    cases = {"a.db": w.WHY_DB, "THUMBS.DB": w.WHY_DB, "state": w.WHY_NOEXT, ".hidden": w.WHY_NOEXT, "m.RVT": w.WHY_WORK,
             "x.dwg": w.WHY_WORK, "f.ttf": w.WHY_FONT, "p.png": w.WHY_MEDIA, "n.txt": w.WHY_DOC, "a.log": w.WHY_LOG,
             "my-license.dat": w.WHY_LICENSE, "k.lic": w.WHY_LICENSE, "t.dll": w.WHY_CODE, "s.lsp": w.WHY_CODE, "z.weird": w.WHY_OTHER}
    for name, why in cases.items():
        assert w.why_of(name) == why, name
    assert w.WHY_SIZE in w.WHY_ORDER and set(w._EXT_WHY.values()) <= set(w.WHY_ORDER)
    assert not set(w._EXT_WHY) & set(w.ALLOWED_CONFIG_EXTS)  # 허용 확장자는 '빠진 사유' 표에 없다


def test_topn_keeps_largest_and_earliest_on_ties():
    t = sf._TopN(3)
    for i, s in enumerate([5, 1, 9, 9, 3, 7, 9, 2]):
        t.add(f"f{i}", s, "w")
    assert [(s, n) for s, n, _ in t.items()] == [(9, "f2"), (9, "f3"), (9, "f6")]
    t2 = sf._TopN(2)
    for i, s in enumerate([4, 4, 4]):
        t2.add(f"g{i}", s, "w")
    assert [n for _, n, _ in t2.items()] == ["g0", "g1"]  # 같은 크기면 먼저 본 것이 남는다(결정적)


def test_gate_examples_and_by_reason(pc):
    big_tree(pc)
    cov = []
    items, sk = collect_extra_items([ex(pc, "BigApp")], pc.expand, cov=cov)
    assert [i.src.name for i in items] == ["settings.json"]
    nc = [s for s in sk if s.get("not_config")][0]
    assert nc["files"] == 12 + 5 + 7 + 6
    by = nc["by_reason"]
    assert by[sf.WHY_DB] == 12 and by[sf.WHY_NOEXT] == 5 and by[sf.WHY_MEDIA] == 7  # license.dat 은 라이선스로 분류
    assert by[sf.WHY_LICENSE] == 1 and by[sf.WHY_WORK] == 1 and by[sf.WHY_FONT] == 1
    assert by[sf.WHY_DOC] == 1 and by[sf.WHY_LOG] == 1 and by[sf.WHY_OTHER] == 1
    assert list(by)[0] == sf.WHY_DB  # 개수 많은 순
    exs = nc["examples"]
    assert len(exs) == 5 and [e["size"] for e in exs] == sorted([e["size"] for e in exs], reverse=True)
    assert exs[0] == {"name": "model.rvt", "size": 9000, "why": sf.WHY_WORK}
    assert exs[1]["name"] == "img/p6.png" and exs[1]["why"] == sf.WHY_MEDIA  # 항목 폴더 기준 상대 이름(슬래시)
    c = cov[0]
    assert c["excluded_examples"] == exs and c["excluded_by_reason"] == by
    # 사용자 폴더가 들어간 절대 경로는 예시에 없다
    assert all(":" not in e["name"] and "\\" not in e["name"] and str(pc.home) not in e["name"] for e in exs)


def test_size_limit_reason_and_merge(pc):
    touch(pc.appdata / "Lim" / "huge.json", b"j" * 500)
    touch(pc.appdata / "Lim" / "a.db", b"d" * 100)
    touch(pc.appdata / "Lim" / "ok.json", b"{}")
    cov = []
    _, sk = collect_extra_items([ex(pc, "Lim")], pc.expand, cov=cov, limits=sf.ConfigLimits(file_bytes=100))
    lim = [s for s in sk if s.get("limit")][0]
    assert lim["top"] == [{"name": "huge.json", "size": 500, "why": sf.WHY_SIZE}] and lim["examples"][0].endswith("huge.json")
    c = cov[0]
    assert c["excluded_by_reason"] == {sf.WHY_DB: 1, sf.WHY_SIZE: 1}
    assert [e["name"] for e in c["excluded_examples"]] == ["huge.json", "a.db"]
    row = coverage.rows({"coverage": cov})[0]
    assert row["excluded_files"] == 2 and row["excluded_bytes"] == 600
    assert row["excluded_line"] == "제외 2개, 600B(데이터베이스/데이터 1개, 크기 상한 1개)"


def test_single_file_item_name(pc):
    touch(pc.appdata / "one.bin", b"b" * 9)
    cov = []
    collect_extra_items([ex(pc, "one.bin")], pc.expand, cov=cov)
    assert cov[0]["excluded_examples"] == [{"name": "one.bin", "size": 9, "why": sf.WHY_DB}]


def test_no_excluded_means_no_keys(pc):
    touch(pc.appdata / "Clean" / "a.json", b"{}")
    cov = []
    _, sk = collect_extra_items([ex(pc, "Clean")], pc.expand, cov=cov)
    assert "excluded_examples" not in cov[0] and not sk
    assert "excluded_line" not in coverage.rows({"coverage": cov})[0]


def test_all_mode_has_no_examples(pc):
    big_tree(pc)
    cov = []
    collect_extra_items([ex(pc, "BigApp")], pc.expand, cov=cov, extra_mode="all")
    assert "excluded_examples" not in cov[0]


# --------------------------------------------------------------------------- 요약 문구 / 이전 manifest 호환
def test_excluded_line_text():
    by = {"이미지·영상": 340, "데이터베이스/데이터": 12, sf.WHY_NOEXT: 5, "로그": 1}
    line = coverage.excluded_line(358, 3 * 1024 ** 3, by)
    assert line == ("제외 358개, 3.0GB(이미지·영상 340개, 데이터베이스/데이터 12개, "
                    "확장자 없음(설정일 수 있음) 5개 외 1가지)")
    assert coverage.excluded_line(0, 0, {}) == ""
    assert coverage.excluded_line(3, 10, {}) == "제외 3개, 10B"  # 이전 manifest(사유 없음)


def test_old_manifest_without_new_keys():
    c = {"rule": "extra", "name": "n", "path": "p", "description": "d", "files": 1, "status": "담김", "config_mode": True,
         "allowed_files": 1, "allowed_bytes": 3, "ext_counts": {".json": 1}, "not_config_files": 4, "not_config_bytes": 100,
         "limit_files": 0, "limit_bytes": 0}
    m = {"coverage": [c]}
    r = coverage.rows(m)[0]
    assert r["excluded_files"] == 4 and r["excluded_examples"] == [] and r["excluded_line"] == "제외 4개, 100B"
    assert r["noext_excluded"] == 0 and coverage.noext_total([r]) == 0
    assert "빠진 사유" not in "\n".join(coverage.config_lines(m))
    assert "제외 4개, 100B" in "\n".join(coverage.lines(m))
    bad = dict(c, excluded_examples=[1, {"name": 3}, {"name": "x", "size": "z"}], excluded_by_reason={"a": "b", "c": -1, "d": 2})
    r2 = coverage.rows({"coverage": [bad]})[0]
    assert r2["excluded_examples"] == [{"name": "x", "size": 0, "why": "기타"}] and r2["excluded_by_reason"] == {"d": 2}
    assert coverage.json_rows({"coverage": [c]})[0]["excluded_files"] == 4


def test_lines_show_summary_and_examples(pc, tmp_path):
    big_tree(pc)
    m = export_package([], tmp_path / "o.zip", [], None, expand=pc.expand, extras=[ex(pc, "BigApp")], now=NOW)
    text = "\n".join(coverage.lines(m))
    assert "제외 30개" in text and "model.rvt 8.8KB — 작업물·모델" in text
    assert text.count("· ") >= 5
    cl = "\n".join(coverage.config_lines(m))
    assert "빠진 사유:" in cl and "확장자 없는 파일이 5개 빠졌습니다" in cl and "--extra-mode all" in cl


def test_manifest_roundtrip_verify_and_plan(pc, tmp_path):
    big_tree(pc)
    out = tmp_path / "p.zip"
    m = export_package([], out, [], None, expand=pc.expand, extras=[ex(pc, "BigApp")], now=NOW)
    assert verify_package(out)[1] == []
    sk = [s for s in m["skipped"] if s.get("not_config")][0]
    assert "examples" in sk and "by_reason" in sk
    for e in m["coverage"][0]["excluded_examples"]:
        assert str(pc.home) not in e["name"]
    plan = plan_import(out, [], pc.expand)
    assert coverage.rows(plan.manifest)[0]["excluded_line"].startswith("제외 30개")


def test_fingerprint_signature_unchanged():
    a, _ = package._fingerprint([], [], "auto", False, "config")
    b, _ = package._fingerprint([], [], "auto", False)
    assert a == b  # 예시는 지문(입력)에 들어가지 않는다


def test_retry_failed_export_still_works(pc, tmp_path):
    big_tree(pc)
    out = tmp_path / "r.zip"
    export_package([], out, [], None, expand=pc.expand, extras=[ex(pc, "BigApp")], now=NOW)
    m = package.retry_failed_export(out, [], pc.expand)
    assert verify_package(out)[1] == [] and "retry" in m
    assert any(s.get("examples") for s in m["skipped"] if s.get("not_config"))


# --------------------------------------------------------------------------- CLI / JSON
def test_cli_export_json_has_excluded(pc, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [])
    monkeypatch.setattr(cli, "winget_export_raw", lambda: None)
    monkeypatch.setattr(cli, "winget_list_raw", lambda: None)
    big_tree(pc)
    sel = tmp_path / "sel.json"
    sel.write_text(json.dumps({"format": 1, "candidates": [{"include": True, "root": "%APPDATA%", "rel": "BigApp"}]}), encoding="utf-8")
    from conftest import make_rules_dir
    rd = tmp_path / "rules0"
    make_rules_dir(rd)
    try:
        base = ["--env", f"APPDATA={pc.appdata}", "--env", f"LOCALAPPDATA={pc.local}", "--env", f"USERPROFILE={pc.home}",
                "--env", f"PROGRAMDATA={pc.pd}"]
        assert cli.main(base + ["export", "--output", str(tmp_path / "c.zip"), "--extra", str(sel), "--rules", str(rd), "--json"]) == 0
        j = json.loads(capsys.readouterr().out)
        row = [r for r in j["coverage"] if "BigApp" in r["program"]][0]
        assert row["excluded_files"] == 30 and len(row["excluded_examples"]) == 5
        assert row["excluded_by_reason"][sf.WHY_NOEXT] == 5 and j["excluded_by_reason"][sf.WHY_NOEXT] == 5
        assert cli.main(base + ["export", "--output", str(tmp_path / "d.zip"), "--extra", str(sel), "--rules", str(rd)]) == 0
        txt = capsys.readouterr().out
        assert "제외 30개" in txt and "확장자 없는 파일이 5개" in txt
    finally:
        envpaths.set_overrides(None)


# --------------------------------------------------------------------------- 성능
def test_perf_20k_files_small_overhead(tmp_path):
    """파일 2만 개(대부분 제외 대상) 허용 방식 수집 시간이 예시 기록 때문에 크게 늘지 않는다(기록을 끈 같은 폴더와 비교)."""
    base = PC(tmp_path, "perf")
    root = base.appdata / "Perf"
    for d in range(20):
        (root / f"d{d}").mkdir(parents=True)
        for i in range(1000):
            (root / f"d{d}" / (f"f{i}.dat" if i % 2 else f"f{i}")).write_bytes(b"x" * (i % 97))
    (root / "keep.json").write_text("{}")

    def run():
        t = time.perf_counter()
        _, sk = collect_extra_items([ex(base, "Perf")], base.expand)
        return time.perf_counter() - t, sk

    run()  # 캐시 데우기
    t_new = min(run()[0] for _ in range(3))
    orig = sf._TopN.add
    sf._TopN.add = lambda self, *a, **k: None
    try:
        t_old = min(run()[0] for _ in range(3))
    finally:
        sf._TopN.add = orig
    _, sk = run()
    nc = [s for s in sk if s.get("not_config")][0]
    assert nc["files"] == 20000 and len(nc["examples"]) == 5
    assert t_new < t_old * 1.5 + 0.3, (t_new, t_old)
    print(f"PERF 20000 files: with-examples {t_new:.2f}s, without {t_old:.2f}s")


def test_gate_judge_micro_cost():
    g = sf.ConfigGate()
    t = time.perf_counter()
    for i in range(200000):
        g.judge(f"f{i}.dat", i % 1000, "", f"d/f{i}.dat")
    dt = time.perf_counter() - t
    assert dt < 3.0 and len(g.nc_top.heap) == 5
    print(f"PERF judge x200000: {dt:.2f}s")
