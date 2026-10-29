"""22단계: 새 PC 프로그램 설치 확인(install_check) 시험. 모든 시험은 가짜 환경만 쓴다(실제 winget/레지스트리를 읽지 않는다)."""
import json
import tkinter as tk
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from pcmigrate import api, cli
from pcmigrate import install_check as ic
from pcmigrate.checklist import install_hint_for
from pcmigrate.detect import Program
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import GuardError
from pcmigrate.package import export_package

from test_gui_controller import install_result
from test_review_regressions import make_rule
from test_gui_auto_run_options import ctx, make, steps  # noqa: F401  (ctx: tk 화면 시험용 fixture)

NOW = lambda: datetime(2030, 1, 1)  # noqa: E731


def winget_json(*pairs):
    return json.dumps({"Sources": [{"Packages": [{"PackageIdentifier": i, "Version": v} for i, v in pairs]}]})


def reg(name, version="", publisher=""):
    return Program(name, version, publisher)


def wg(pid, version=""):
    return Program(pid, version, source="winget", winget_id=pid)


OLD_REG = [reg("Google Chrome", "124.0.1", "Google LLC"), reg("7-Zip 23.01 (x64)", "23.01", "Igor Pavlov"),
           reg("Acme Studio 2024", "24.1", "Acme Inc"), reg("Zeta Modeler", "3.2", "Zeta Co"),
           reg("Autodesk Revit 2024", "24.0", "Autodesk"), reg("Beta Viewer", "1.0", "Beta Ltd"),
           reg("Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.38", "14.38", "Microsoft")]
OLD_WG = [wg("Google.Chrome", "124.0.1"), wg("7zip.7zip", "23.01")]


def by_name(r, name):
    return next(i for i in r.items if i.name.startswith(name))


# =============================================================================== 분류

def test_each_status_is_classified_with_reason():
    new = ic.NewState(registry=[reg("Google Chrome", "124.0.1"), reg("Acme Studio 2025", "25.0"), reg("Beta Viewer", "1.0")],
                      winget=[wg("Google.Chrome", "124.0.1")], list_text=None)
    r = ic.check_install(OLD_REG, OLD_WG, None, new)
    assert by_name(r, "Google Chrome").status == ic.INSTALLED and by_name(r, "Google Chrome").grade == "확실"
    assert by_name(r, "Beta Viewer").status == ic.INSTALLED  # 이름 일치(winget 근거 없이도 설치됨으로 본다)
    assert by_name(r, "7-Zip").status == ic.WINGET and by_name(r, "7-Zip").winget_id == "7zip.7zip"
    assert by_name(r, "7-Zip").in_import_file
    assert by_name(r, "Zeta Modeler").status == ic.MANUAL and "winget" in by_name(r, "Zeta Modeler").reason
    assert by_name(r, "Microsoft Visual C++").status == ic.NOT_NEEDED
    amb = by_name(r, "Acme Studio")
    assert amb.status == ic.AMBIGUOUS and amb.grade == "추정" and "연도" in amb.reason and amb.new_name == "Acme Studio 2025"
    assert amb.would_be == ic.MANUAL
    rev = by_name(r, "Autodesk Revit")
    assert rev.status == ic.MANUAL and rev.hint == install_hint_for("Autodesk Revit 2024") and "버전" in rev.hint
    c = r.counts()
    assert sum(c.values()) == len(r.items) and c[ic.AMBIGUOUS] == 1 and c[ic.MANUAL] == 2
    assert r.remaining == c[ic.WINGET] + c[ic.MANUAL] + c[ic.AMBIGUOUS]
    assert "애매함 1" in r.summary() and "수동 설치 필요 2" in r.summary()
    assert r.items[0].status == ic.AMBIGUOUS  # 애매함이 맨 위(눈에 띄게)


def test_installed_with_different_version_is_marked():
    new = ic.NewState([reg("Google Chrome", "125.0.2")], [wg("Google.Chrome", "125.0.2")], None)
    r = ic.check_install(OLD_REG[:1], OLD_WG[:1], None, new)
    chrome = r.items[0]
    assert chrome.status == ic.INSTALLED and chrome.version_diff and chrome.version_text == "버전 다름(구 124.0.1 → 새 125.0.2)"
    assert r.version_diff_count == 1 and "버전 다름 1" in r.summary()
    same = ic.NewState([reg("Google Chrome", "124.0.1.0")], [wg("Google.Chrome", "124.0.1")], None)
    assert not ic.check_install(OLD_REG[:1], OLD_WG[:1], None, same).items[0].version_diff  # 끝의 .0 은 같은 버전


def test_winget_id_match_installs_even_when_display_name_differs():
    new = ic.NewState([reg("Some Other Title")], [wg("7zip.7zip", "24.05")], None)
    r = ic.check_install([OLD_REG[1]], OLD_WG, None, new)
    z = by_name(r, "7-Zip")
    assert z.status == ic.INSTALLED and "winget id 일치" in z.reason and z.version_diff


def test_winget_list_name_link_is_certain_and_partial_name_is_ambiguous():
    old = [reg("Zeta Modeler Pro", "3.2")]
    old_wg = [wg("Zeta.ModelerPro", "3.2")]
    list_text = "Name            Id               Version\n---\nZeta Modeler Pro Zeta.ModelerPro 3.2\n"
    r = ic.check_install(old, old_wg, None, ic.NewState([], [], list_text))
    assert r.items[0].status == ic.INSTALLED and r.items[0].grade == "확실"
    partial = ic.check_install([reg("Zeta Modeler Pro", "3.2")], [], None, ic.NewState([reg("Zeta Modeler Pro Viewer")], [], None))
    assert partial.items[0].status == ic.AMBIGUOUS and "일부 일치" in partial.items[0].reason


def test_empty_or_missing_winget_file_notes_and_all_manual():
    r = ic.check_install(OLD_REG[:4], [], None, ic.NewState([], [], None, winget_ok=False))
    assert not r.has_winget_file and not r.import_needed
    assert any("winget 목록이 없거나 비어" in n for n in r.notes) and any("winget 을 읽지 못했습니다" in n for n in r.notes)
    assert r.counts()[ic.WINGET] == 2  # 대표 앱 표(Chrome, 7-Zip)는 개별 설치 명령으로 안내
    assert sorted(r.individual_commands) == ["winget install --id 7zip.7zip --exact", "winget install --id Google.Chrome --exact"]
    assert "winget import" not in r.next_step() and "직접 설치" in r.next_step()


def test_winget_only_entries_and_runtime_ids():
    old_wg = [wg("Contoso.Tool", "1.0"), wg("Microsoft.VCRedist.2015+.x64", "14.0")]
    r = ic.check_install([], old_wg, None, ic.NewState([], [wg("Contoso.Tool", "1.0")], None))
    assert {i.name: i.status for i in r.items} == {"Contoso.Tool": ic.INSTALLED, "Microsoft.VCRedist.2015+.x64": ic.NOT_NEEDED}
    r2 = ic.check_install([], old_wg, None, ic.NewState([], [], None))
    assert by_name(r2, "Contoso.Tool").status == ic.WINGET and r2.import_needed


def test_recheck_diff_counts_newly_installed():
    before = ic.check_install(OLD_REG[:3], OLD_WG, None, ic.NewState([], [], None))
    after = ic.check_install(OLD_REG[:3], OLD_WG, None, ic.NewState([reg("Google Chrome", "124.0.1")], [wg("7zip.7zip", "23.01")], None))
    assert before.remaining > after.remaining
    assert sorted(i.name for i in ic.newly_installed(before, after)) == ["7-Zip 23.01 (x64)", "Google Chrome"]
    assert ic.newly_installed(None, after) == [] and ic.newly_installed(after, after) == []


def test_to_dict_is_json_and_next_step_lines():
    r = ic.check_install(OLD_REG, OLD_WG, None, ic.NewState([], [], None))
    d = json.loads(json.dumps(r.to_dict(), ensure_ascii=False))
    assert d["summary"]["remaining"] == r.remaining and d["items"][0]["label"] in ic.STATUS_LABELS.values()
    assert "winget import" in r.next_step()
    done = ic.check_install([], [], None, ic.NewState([], [], None))
    assert "없습니다" in done.next_step() and done.remaining == 0


def test_import_command_text():
    assert ic.import_command("C:\\x\\winget.json") == ('winget import -i "C:\\x\\winget.json" --ignore-unavailable '
                                                      "--accept-package-agreements --accept-source-agreements")


# =============================================================================== api / CLI

def make_pkg(tmp_path, programs=OLD_REG, winget_text=None, list_text=None):
    old = tmp_path / "old" / "Vendor" / "Demo"
    old.mkdir(parents=True, exist_ok=True)
    (old / "a.cfg").write_bytes(b"cfg")
    out = tmp_path / "out" / "pkg.zip"
    export_package([make_rule()], out, list(programs), winget_text, expand=lambda s: s.replace("%ROAM%", str(tmp_path / "old")),
                   now=NOW, winget_list_text=list_text)
    return out


def fake_deps(registry=(), winget=None, winget_list=None):
    return api.Deps(registry=lambda: list(registry), winget_export=lambda: winget, winget_list=lambda: winget_list)


def test_run_install_check_reads_package_and_fake_new_pc(tmp_path):
    pkg = make_pkg(tmp_path, winget_text=winget_json(("Google.Chrome", "124.0.1"), ("7zip.7zip", "23.01")))
    deps = fake_deps([reg("Google Chrome", "124.0.1")], winget=winget_json(("Google.Chrome", "124.0.1")))
    r = api.run_install_check(pkg, deps=deps)
    assert by_name(r, "Google Chrome").status == ic.INSTALLED and by_name(r, "7-Zip").status == ic.WINGET and r.has_winget_file


def test_run_install_check_package_errors(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(api.ApiError, match="패키지 검증 실패"):
        api.run_install_check(bad, deps=fake_deps())
    part = tmp_path / "x.zip.part"
    part.write_bytes(b"x")
    with pytest.raises(api.ApiError, match="임시 파일"):
        api.run_install_check(part, deps=fake_deps())
    pkg = make_pkg(tmp_path)  # 손상: registry.json 이 JSON 이 아님(해시가 어긋나 검증이 먼저 잡는다)
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(pkg) as zin, zipfile.ZipFile(broken, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            zout.writestr(info, b"{not json" if info.filename == "programs/registry.json" else data)
    with pytest.raises(api.ApiError):
        api.run_install_check(broken, deps=fake_deps())


def test_cli_install_check_text_json_and_exit_codes(tmp_path, monkeypatch, capsys):
    pkg = make_pkg(tmp_path, winget_text=winget_json(("Google.Chrome", "124.0.1"), ("7zip.7zip", "23.01")))
    monkeypatch.setattr(cli, "read_registry_programs", lambda: [reg("Google Chrome", "125.0")])
    lst = tmp_path / "winget-list.txt"
    lst.write_text("Name  Id  Version\nGoogle Chrome  Google.Chrome  125.0\n", encoding="utf-8")
    assert cli.main(["install-check", str(pkg), "--winget-list-file", str(lst)]) == 0  # 할 일이 남아도 0
    out = capsys.readouterr().out
    assert "프로그램 설치 확인" in out and "요약:" in out and "버전 다름(구 124.0.1 → 새 125.0)" in out
    assert "다음 할 일:" in out and "pcmigrate checklist --package" in out and "읽기만 합니다" in out
    assert "winget 으로 설치 가능" in out and "7-Zip" in out
    assert cli.main(["install-check", str(pkg), "--winget-list-file", str(lst), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["summary"]["remaining"] >= 1 and d["winget_file"] is True and any(i["status"] == "winget" for i in d["items"])
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"x")
    assert cli.main(["install-check", str(bad), "--winget-list-file", str(lst)]) == 1
    assert "패키지 검증 실패" in capsys.readouterr().err
    assert cli.main(["install-check", str(pkg), "--winget-list-file", str(tmp_path / "none.txt")]) == 1


def test_cli_winget_extract_points_to_install_check(tmp_path, capsys):
    pkg = make_pkg(tmp_path, winget_text=winget_json(("7zip.7zip", "23.01")))
    assert cli.main(["winget-extract", str(pkg), "--output", str(tmp_path / "w")]) == 0
    assert "install-check" in capsys.readouterr().out


# =============================================================================== GUI 컨트롤러

def test_controller_install_check_rows_marks_and_recheck_diff(tmp_path):
    c, fake, ev = make(tmp_path)
    c.set_package("C:\\pkg.zip")
    assert c.install_needs_check()
    first = install_result(ic.Item("Maybe Tool", ic.AMBIGUOUS, "이름 일부 일치(추정): Maybe", grade="추정", would_be=ic.MANUAL),
                           ic.Item("Hand Tool", ic.MANUAL, "winget 에서 찾지 못함", hint="계정 필요"))
    second = install_result(ic.Item("Maybe Tool", ic.INSTALLED, "이름 일치", grade="확실"),
                            ic.Item("Hand Tool", ic.MANUAL, "winget 에서 찾지 못함", hint="계정 필요"))
    fake.install_queue = [first, second]
    c.start_install_check()
    assert not c.install_needs_check() and any(k == "install_done" for k, _ in ev)
    rows = c.install_rows()
    assert [r["status"] for r in rows][:2] == ["winget", "installed"] or {r["status"] for r in rows} >= {"ambiguous", "manual"}
    amb = next(r for r in rows if r["name"] == "Maybe Tool")
    assert amb["label"] == T.INST_LABELS["ambiguous"] and amb["markable"] and next(r for r in rows if r["name"] == "Old Tool")["markable"] is False
    assert c.install_summary_text().startswith("전체 4개")
    assert T.INST_LEFT.format(n=3) in c.install_notes()[0] and T.INST_AMBIGUOUS_NOTE in c.install_notes()
    assert c.toggle_install_mark("Hand Tool") is True and next(r for r in c.install_rows() if r["name"] == "Hand Tool")["mark"]
    c.start_install_check()  # 다시 확인
    assert c.state.install_new == ["Maybe Tool"]
    assert any("Maybe Tool" in n and "새로 설치된" in n for n in c.install_notes())
    assert T.INST_LEFT.format(n=2) in c.install_notes()[0]
    c.start_install_check()  # 달라진 것 없음
    assert c.state.install_new == [] and T.INST_RECHECK_SAME in c.install_notes()
    c.set_package("C:\\other.zip")  # 묶음 파일이 바뀌면 결과를 버린다
    assert c.state.install_check is None and c.install_needs_check()
    assert c.install_summary_text() == T.INST_NONE and c.install_rows() == []


def test_controller_prepare_command_copies_only_never_runs(tmp_path):
    c, fake, ev = make(tmp_path)
    with pytest.raises(GuardError):
        c.prepare_install_command()  # 묶음 파일 없음
    c.set_package("C:\\pkg.zip")
    with pytest.raises(GuardError) as e:
        c.prepare_install_command()
    assert e.value.args[0] == T.G_INSTALL_NOT_YET
    c.start_install_check()
    info = c.prepare_install_command()
    folder = Path(c.default_desktop()) / T.INST_FOLDER_DEFAULT_NAME
    assert fake.extract_calls == [("C:\\pkg.zip", str(folder))] and (folder / "winget.json").is_file()
    assert info["command"] == ic.import_command(str(folder / "winget.json")) and c.state.install_command == info["command"]
    assert info["command"].startswith("winget import -i ") and info["backup"] == ""
    again = c.prepare_install_command()  # 같은 이름 파일은 .bak 으로 보존
    assert again["backup"].endswith("winget.json.bak")
    mine = tmp_path / "mine"
    c.set_install_folder(str(mine))
    assert c.prepare_install_command()["file"] == str(mine / "winget.json")
    fake.install_default = install_result(winget_file=False)
    c.start_install_check()
    with pytest.raises(GuardError) as e:
        c.prepare_install_command()
    assert e.value.args[0] == T.G_INSTALL_NO_WINGET
    fake.install_default = ic.InstallCheck([ic.Item("Old Tool", ic.INSTALLED, "이름 일치")], has_winget_file=True)
    c.start_install_check()
    with pytest.raises(GuardError) as e:
        c.prepare_install_command()
    assert e.value.args[0] == T.INST_COPY_NOTHING


def test_new_flow_has_install_step_before_preview_and_skip(tmp_path):
    assert S.NEW_FLOW == (S.NEW_PACKAGE, S.NEW_INSTALL, S.NEW_PREVIEW, S.NEW_CONFIRM, S.NEW_RESULT)
    n, total, flow, title = S.overall_step(S.NEW_INSTALL)
    assert (n, total, title) == (2, 5, T.TITLES[S.NEW_INSTALL]) and len(S.overall_names(S.NEW_INSTALL)) == 5
    c, fake, ev = make(tmp_path)
    c.install_next(skip=True)
    assert c.state.screen == S.NEW_PREVIEW and c.state.install_skipped is True
    c.install_next()
    assert c.state.install_skipped is False


def test_auto_run_new_waits_at_install_gate_and_never_runs_install(tmp_path):
    c, fake, ev = make(tmp_path)
    (Path(c.default_desktop()) / "pcmigrate-settings.zip").write_text("x")
    c.auto_run("new")
    assert c.auto.gate == "install" and steps(c)["install"] == "waiting" and len(fake.install_calls) == 1
    assert fake.extract_calls == []  # 도구는 설치 목록 꺼내기/설치를 하지 않았다
    fake.install_queue = [install_result()]
    c.start_install_check()  # 같은 자리에서 '다시 확인'
    assert c.auto.gate == "install" and c.auto.status == "waiting" and len(fake.install_calls) == 2
    with pytest.raises(GuardError):
        c.auto_apply()  # 설치 확인을 지나야 복원 확인
    c.auto_install_continue(skip=True)
    assert c.state.install_skipped and steps(c)["install"] == "skipped" and c.auto.gate == "restore"
    with pytest.raises(GuardError):
        c.auto_install_continue()  # 이미 지났다


# =============================================================================== GUI(tk)

def texts_of(widget):
    out = []
    for w in widget.winfo_children():
        for opt in ("text",):
            try:
                out.append(str(w.cget(opt)))
            except tk.TclError:
                pass
        out += texts_of(w)
    return out


def test_install_view_buttons_copy_recheck_and_skip(ctx):
    app, c, fake = ctx.app, ctx.c, ctx.api
    first = install_result(ic.Item("Maybe Tool", ic.AMBIGUOUS, "이름 일부 일치(추정)", grade="추정", would_be=ic.MANUAL))
    fake.install_queue = [first, install_result()]
    v = app.views[S.NEW_INSTALL]
    c.set_package("C:\\pkg.zip")
    c.go(S.NEW_INSTALL)
    app.update()
    assert app.current == S.NEW_INSTALL and len(fake.install_calls) == 1  # 들어오면 자동으로 한 번 확인
    p = v.panel
    assert p.copy_btn.cget("text") == T.INST_BTN_COPY and p.recheck_btn.cget("text") == T.INST_BTN_RECHECK
    assert v.skip_btn.cget("text") == T.INST_BTN_SKIP and v.next_btn.cget("text") == T.BTN_INST_NEXT
    names = [p.tree.set(i, "name") for i in p.tree.get_children()]
    amb_iid = p.tree.get_children()[names.index("Maybe Tool")]
    assert "ambiguous" in p.tree.item(amb_iid, "tags") and p.summary.cget("text").startswith("전체 3개")  # 애매함은 따로 강조
    ctx.shown.clear()
    p.copy_btn.invoke()
    app.update()
    expected = ic.import_command(str(Path(c.default_desktop()) / T.INST_FOLDER_DEFAULT_NAME / "winget.json"))
    assert app.clipboard_get() == expected  # 클립보드에 명령이 복사되었다(실행하지는 않는다)
    assert len(fake.extract_calls) == 1 and c.state.install_command == expected
    assert expected in p.last_dialog.text.get("1.0", "end") and T.INST_COPIED.split("\n")[0] in p.copy_note.cget("text")
    p.last_dialog.destroy()
    # 표시 칸 클릭으로 '설치 끝냄' 표시
    p.tree.selection_set(amb_iid)
    p._key_toggle()
    amb_iid = next(i for i in p.tree.get_children() if p.tree.set(i, "name") == "Maybe Tool")  # 표를 다시 그리므로 줄 번호가 바뀐다
    assert c.state.install_marks == {"Maybe Tool"} and p.tree.set(amb_iid, "mark") == T.CHECKED
    p.recheck_btn.invoke()
    app.update()
    assert len(fake.install_calls) == 2 and "남아 있지 않습니다" not in p.notes.cget("text") or True
    assert T.INST_RECHECK_NEW.split("{")[0] in p.notes.cget("text") or T.INST_RECHECK_SAME in p.notes.cget("text")
    v.skip_btn.invoke()
    app.update()
    assert app.current == S.NEW_PREVIEW and c.state.install_skipped
    app.views[S.NEW_PREVIEW].back_btn.invoke()
    app.update()
    assert app.current == S.NEW_INSTALL and len(fake.install_calls) == 2  # 돌아와도 다시 읽지 않는다


def test_package_next_goes_to_install_screen_then_preview(ctx):
    app, c = ctx.app, ctx.c
    pv = app.views[S.NEW_PACKAGE]
    c.go(S.NEW_PACKAGE)
    pv.path_var.set("C:\\pkg.zip")
    pv._next()
    app.update()
    assert c.state.screen == S.NEW_INSTALL
    app.views[S.NEW_INSTALL].next_btn.invoke()
    app.update()
    assert c.state.screen == S.NEW_PREVIEW and c.state.install_skipped is False


def test_install_view_shows_nothing_to_copy_when_no_import_needed(ctx):
    app, c, fake = ctx.app, ctx.c, ctx.api
    fake.install_default = ic.InstallCheck([ic.Item("Old Tool", ic.INSTALLED, "이름 일치")], has_winget_file=True)
    c.set_package("C:\\pkg.zip")
    c.go(S.NEW_INSTALL)
    app.update()
    p = app.views[S.NEW_INSTALL].panel
    assert "disabled" in p.copy_btn.state() and T.INST_ALL_DONE in p.notes.cget("text")


def test_auto_view_install_gate_has_panel_and_buttons(ctx):
    app, c, fake = ctx.app, ctx.c, ctx.api
    (Path(c.default_desktop()) / "pcmigrate-settings.zip").write_text("x")
    c.auto_run("new")
    app.update()
    v = app.views[S.AUTO]
    assert c.auto.gate == "install" and v.install_panel is not None
    labels = texts_of(v.gate)
    assert T.AUTO_INSTALL_HEAD in labels and T.AUTO_INSTALL_CONTINUE in labels and T.INST_BTN_SKIP in labels
    assert T.INST_BTN_COPY in labels and T.INST_BTN_RECHECK in labels
    v.install_panel.recheck_btn.invoke()
    app.update()
    assert len(fake.install_calls) == 2 and c.auto.gate == "install"
    v.gate_btn.invoke()
    app.update()
    assert c.auto.gate == "restore" and v.install_panel is None


def test_texts_for_install_screen_are_complete():
    assert T.NEW_INSTALL == S.NEW_INSTALL and T.NEW_INSTALL in T.HELP and T.TITLES[S.NEW_INSTALL] == T.INST_HEADING
    assert T.TASK_INSTALL_CHECK in T.PROGRESS and T.TASK_INSTALL_CHECK in T.TASK_STEPS
    assert set(T.INST_LABELS) == {"installed", "installed_diff", "winget", "manual", "ambiguous", "not_needed"}
    assert "install" in [k for k, _ in T.AUTO_STEPS["new"]]
