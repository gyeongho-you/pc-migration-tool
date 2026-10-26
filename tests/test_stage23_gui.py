"""stage23 GUI: 후보 표의 크기/파일 수와 선택 합계, 이 프로그램 설정 찾기, 설정 확인표, 화면 전환(숨긴 채 채우기). 가짜 환경만 쓴다."""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import api as real_api
from pcmigrate import coverage
from pcmigrate.detect import Program
from pcmigrate.discover import Candidate, Discovery, Link, ProgramEntry
from pcmigrate.find_program import ProgramFind
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi, env, kinds, plan  # noqa: F401
from test_gui_smoke import ALL_SCREENS, children, ctx  # noqa: F401

MB = 1024 * 1024


def cache_cand(rel="Big", size=10 * MB, cache=4 * MB, files=100, cache_files=60, **kw):
    c = Candidate("%APPDATA%", rel, f"C:\\fake\\{rel}", True, Link(["Acme Tool"], "추정", "이유", False), size=size, files=files)
    c.cache_size, c.cache_files = cache, cache_files
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def set_discovery(c, *cands):
    d = Discovery()
    d.programs = [ProgramEntry(Program("Acme Tool"), list(cands)), ProgramEntry(Program("Other Tool"), [])]
    c.state.discovery = d


# --------------------------------------------------------------------- 컨트롤러(헤드리스)

def test_rows_show_size_and_files_without_cache(env):
    c, api, ev = env
    set_discovery(c, cache_cand())
    r = c.candidate_rows()[0]
    assert r.size_eff == "6.0MB" and r.files_eff == "40개" and "전체 크기 10.0MB" in r.detail_cache
    c.set_include_caches(True)
    r = c.candidate_rows()[0]
    assert r.size_eff == "10.0MB" and r.files_eff == "100개" and r.detail_cache == ""


def test_selection_total_text_and_cache_note(env):
    c, api, ev = env
    set_discovery(c, cache_cand("A"), cache_cand("B", size=2 * MB, cache=0, files=5, cache_files=0))
    assert c.selection_total_text() == T.DISC_TOTAL_NONE
    for r in c.candidate_rows():
        c.set_selected(r.key, True)
    txt = c.selection_total_text()
    assert "선택한 2개: 약 8.0MB, 파일 45개" in txt
    assert "캐시 제외 후" in txt and "4.0MB" in txt and "60개" in txt
    c.set_include_caches(True)
    t = c.selection_totals()
    assert t["bytes"] == 12 * MB and t["cache_bytes"] == 0 and "캐시 폴더도 포함" in c.selection_total_text()


def test_selection_total_unknown_and_partial_and_big(env):
    c, api, ev = env
    unknown = cache_cand("U", size=None, cache=0, files=None, cache_files=0)
    partial = cache_cand("P", size=None, cache=0, files=7, cache_files=0, partial_size=2 * 1024 ** 3)
    set_discovery(c, unknown, partial)
    rows = {r.path[-1]: r for r in c.candidate_rows()}
    assert rows["U"].size_eff == "크기 미확인" and rows["P"].size_eff == "2.0GB 이상" and rows["P"].files_eff == "7개 이상"
    for r in c.candidate_rows():
        c.set_selected(r.key, True)
    t = c.selection_totals()
    assert t["unknown"] == 1 and t["approx"] == 1 and t["big"] == 1
    txt = c.selection_total_text()
    assert "더 큽니다" in txt and "알 수 없는 1개" in txt


def test_include_caches_reaches_api_and_unsupported_is_guarded(env, tmp_path):
    c, api, ev = env
    set_discovery(c, cache_cand())
    c.set_include_caches(True)
    with pytest.raises(GuardError):  # 이 옵션을 모르는 api 에는 몰래 빠뜨리지 않는다
        c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    seen = {}

    def run_export(rules_dir, output, *, extra=None, allow_secrets=False, deps=None, extra_items=None, include_caches=False):
        seen["ic"] = include_caches
        return SimpleNamespace(manifest={"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []})
    api.run_export = run_export
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    assert seen["ic"] is True


def test_find_program_merges_candidates_without_auto_select(env):
    c, api, ev = env
    set_discovery(c, cache_cand("Have"))
    new = cache_cand("Found")
    dup = cache_cand("Have")
    res = ProgramFind(Program("Acme Tool"), tokens=[("acme", "이름")], candidates=[new, dup],
                      install_files=[{"path": "C:\\p\\a.ini", "size": 1, "why": "x"}])
    api.run_find_program = lambda rules_dir, name, *, deps=None, cancel=None, progress_info=None: res
    with pytest.raises(GuardError):
        c.start_find_program("Nope")
    c.start_find_program("Acme Tool")
    assert "find_done" in kinds(ev) and c.state.selected == set()
    assert [r.path[-5:] for r in c.candidate_rows()] == ["\\Have", "Found"] or len(c.candidate_rows()) == 2
    info = [p for k, p in ev if k == "find_done"][0]
    assert (info["new"], info["dup"]) == (1, 1)
    msg = c.find_summary(info)
    assert "새 후보 1개" in msg and "a.ini" in msg and "캐시/로그/임시 폴더도 포함" in msg and "라이선스" in msg


def test_find_needs_discovery_first(env):
    c, api, ev = env
    with pytest.raises(GuardError):
        c.start_find_program("Acme Tool")


def test_license_candidate_cannot_be_selected_or_exported(env, tmp_path):
    c, api, ev = env
    lic = cache_cand("Lic", badge="라이선스", license_only=True)
    cch = cache_cand("Logs", badge="로그", include_caches=True)
    set_discovery(c, lic, cch)
    rows = {r.path[-3:]: r for r in c.candidate_rows()}
    assert rows["Lic"].selectable is False and rows["Lic"].badge == "라이선스" and rows["ogs"].badge == "로그"
    with pytest.raises(GuardError):
        c.set_selected(lic.key, True)
    n, k = c.select_all_candidates()
    assert n == 1 and lic.key not in c.state.selected  # 전체 선택도 라이선스는 건너뜀
    c.state.selected.add(lic.key)  # 어떤 경로로 들어와도 내보내기에서 뺀다
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    sent = api.calls[-1][3]
    assert [x[2] for x in sent] == ["Logs"]


def test_candidate_level_include_caches_is_sent_to_selection(env, tmp_path):
    c, api, ev = env
    cch = cache_cand("AcmeCache", badge="임시 저장물일 수 있음", include_caches=True)
    plain = cache_cand("Plain")
    set_discovery(c, cch, plain)
    got = {}
    c.make_items = lambda data: got.setdefault("d", data) and []
    c.select_all_candidates()
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    flags = {x["rel"]: x.get("include_caches", False) for x in got["d"]["candidates"]}
    assert flags == {"AcmeCache": True, "Plain": False}


def test_coverage_table_and_headline(env):
    c, api, ev = env
    m = {"coverage": [{"rule": "a", "name": "Demo", "description": "사용자 설정", "status": "담김", "files": 3},
                      {"rule": "a", "name": "Demo", "description": "플러그인", "status": "이 PC에 없음", "files": 0},
                      {"rule": "b", "name": "Hand", "description": "x", "status": "직접 해야 함", "files": 0}],
         "skipped": [{"cache": True, "files": 5, "bytes": 2048}]}
    res = SimpleNamespace(manifest=m)
    t = c.coverage_table(res)
    assert [r["status"] for r in t] == ["담김", "이 PC에 없음", "직접 해야 함"]
    assert c.coverage_headline(t) == "담김 1 · 직접 해야 함 1 · 해당 없음 1"
    assert "1개" in c.cache_skipped_text(res) and "2.0KB" in c.cache_skipped_text(res)
    assert c.coverage_table(SimpleNamespace(plan=SimpleNamespace(manifest=m)))[0]["program"] == "Demo"
    assert c.coverage_table(SimpleNamespace()) == []


# --------------------------------------------------------------------- tkinter

def test_discover_view_columns_total_and_find_row(ctx):
    app, c = ctx.app, ctx.c
    set_discovery(c, cache_cand("Big"))
    ctx.api.run_find_program = lambda *a, **k: ProgramFind(Program("Acme Tool"), tokens=[("acme", "이름")], candidates=[
        cache_cand("AcmeLogs", badge="로그", include_caches=True), cache_cand("AcmeLic", badge="라이선스", license_only=True)])
    c.go(S.OLD_DISCOVER)
    app.update()
    v = app.views[S.OLD_DISCOVER]
    assert len(v.tree["columns"]) == 7
    rows = children(v.tree)
    assert rows[0]["values"][4] == "6.0MB" and rows[0]["values"][5] == "40개"
    assert T.DISC_TOTAL_NONE in v.total.cget("text")
    v._select_all()
    app.update()
    assert "선택한 1개" in v.total.cget("text") and "캐시 제외 후" in v.total.cget("text")
    assert "Acme Tool" in v.find_box.cget("values") and v.find_var.get() == T.FIND_PICK
    v._find()  # 프로그램을 고르지 않음: 안내 대화상자(가짜)만
    assert ctx.shown and ctx.shown[-1][0] == "warn"
    v.find_var.set("Acme Tool")
    v._find()
    app.update()
    shown = children(v.tree)
    marks = {r["values"][3][-8:]: (r["values"][0], r["values"][1]) for r in shown}
    assert len(shown) == 3 and v.find_msg.cget("text")
    lic = [r for r in shown if r["values"][1] == "라이선스"][0]
    assert lic["values"][0] == T.LOCKED
    log = [r for r in shown if r["values"][1] == "로그"][0]
    assert log["values"][0] == T.UNCHECKED and c.state.selected != set()  # 기존 선택은 유지, 새 항목은 자동 선택되지 않음
    assert len(c.state.selected) == 1


def test_export_view_include_caches_checkbox_and_coverage_box(ctx, tmp_path):
    app, c = ctx.app, ctx.c
    set_discovery(c, cache_cand("Big"))
    c.go(S.OLD_EXPORT)
    app.update()
    v = app.views[S.OLD_EXPORT]
    assert v.cache_var.get() is False
    v.cache_var.set(True)
    c.set_include_caches(True)
    assert c.state.include_caches is True
    c.set_include_caches(False)
    m = {"rules": [], "files": [], "skipped": [{"cache": True, "files": 3, "bytes": 1024}], "failed": [], "manual_warnings": [],
         "coverage": [{"rule": "a", "name": "Demo", "description": "사용자 설정", "status": "담김", "files": 3},
                      {"rule": "a", "name": "Demo", "description": "플러그인", "status": "이 PC에 없음", "files": 0}]}
    c.state.export_output = str(tmp_path / "x.zip")
    v.on_event("export_done", SimpleNamespace(manifest=m))
    app.update()
    assert [r["values"][2] for r in children(v.cov.tree)] == ["담김"]
    assert "해당 없음 1곳" in v.cov.fold.toggle_btn.cget("text") and v.cov.winfo_manager()
    assert "임시 저장물 폴더 1개" in v.cache_note.cget("text")
    v.cov.fold.toggle()
    app.update()
    assert "플러그인" in v.cov.fold_list.cget("text")
    v._reset_done()
    assert not v.cov.winfo_manager()


def test_preview_and_result_show_coverage(ctx):
    app, c = ctx.app, ctx.c
    m = {"manual_warnings": [], "coverage": [{"rule": "a", "name": "Demo", "description": "사용자 설정", "status": "담김", "files": 3},
                                              {"rule": "b", "name": "Hand", "description": "x", "status": "직접 해야 함", "files": 0}]}
    p = real_api.ImportResult(plan([], warnings=()), "dry-run", 0)
    p.plan.manifest.update(m)
    c.state.preview = p
    c.go(S.NEW_PREVIEW)
    app.update()
    v = app.views[S.NEW_PREVIEW]
    assert [r["values"][2] for r in children(v.cov.tree)] == ["담김", "직접 해야 함"]
    assert "담김 1" in v.cov_fold.toggle_btn.cget("text")
    # 오래된 패키지(coverage 없음)도 표가 깨지지 않는다
    p2 = real_api.ImportResult(plan([]), "dry-run", 0)
    p2.plan.manifest.update({"rules": [{"id": "a", "name": "Old"}], "files": [{"rule": "a"}]})
    c.state.preview = p2
    v._render()
    assert children(v.cov.tree)[0]["values"][0] == "Old"


# --------------------------------------------------------------------- 화면 전환

def test_show_fills_hidden_then_raises_and_only_current_view_is_laid_out(ctx):
    app, c = ctx.app, ctx.c
    order = []
    for scr in ALL_SCREENS:
        c.go(scr)
        app.update()
        assert app.views[scr].winfo_manager() == "grid" and app.current == scr
        others = [n for n, v in app.views.items() if n != scr and v.winfo_manager()]
        assert others == [], (scr, others)
    v = app.views[S.OLD_EXPORT]
    orig_show, orig_raise = v.on_show, v.tkraise
    v.on_show = lambda: (order.append("fill"), orig_show())[1]
    v.tkraise = lambda: (order.append("raise"), orig_raise())[1]
    c.go(S.OLD_EXPORT)
    assert order == ["fill", "raise"]  # 모든 화면: 먼저 채우고 마지막에 올린다


def test_step_chips_are_reused_not_recreated(ctx):
    app, c = ctx.app, ctx.c
    c.go(S.OLD_SCAN)
    app.update()
    chips = list(app.overall_chips)
    assert chips
    for scr in (S.OLD_DISCOVER, S.OLD_CHECKLIST, S.START, S.OLD_EXPORT, S.NEW_PACKAGE):
        c.go(scr)
        app.update()
    assert app.overall_chips[: len(chips)] == chips and all(a is b for a, b in zip(chips, app.overall_chips))
    c.go(S.OLD_SCAN)
    app.update()
    assert [ch.winfo_manager() for ch in app.overall_chips[:4]] == ["pack"] * 4
    c.go(S.START)
    app.update()
    assert not app.overall.winfo_manager()


def test_screen_switch_is_fast_with_fake_controller(ctx):
    app, c = ctx.app, ctx.c
    app.deiconify()
    app.update()
    t = time.perf_counter()
    n = 0
    for _ in range(2):
        for scr in ALL_SCREENS[:10]:
            c.go(scr)
            app.update()
            n += 1
    avg = (time.perf_counter() - t) / n
    assert avg < 0.6  # 가짜 환경에서 화면 하나를 바꾸는 데 평균 0.6초 미만(느슨한 상한: 회귀 방지용)
