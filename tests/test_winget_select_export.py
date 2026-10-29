"""38단계: 설치 확인에서 원하는 프로그램만 골라 winget.json 으로 꺼내기. 가짜 환경만 쓴다(실제 winget 실행 없음)."""
import json
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import api, cli
from pcmigrate import install_check as ic
from pcmigrate.detect import valid_winget_id
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import GuardError
from pcmigrate.package import PackageError, export_package, extract_winget, list_winget_packages

from test_gui_controller import install_result
from test_review_regressions import NOW, env, make_rule  # noqa: F401  (env: 가짜 환경 fixture)
from test_gui_auto_run_options import ctx, make  # noqa: F401

DOC = {"$schema": "https://aka.ms/winget-packages.schema.json", "CreationDate": "2030-01-01", "WinGetVersion": "1.9",
       "Sources": [
           {"Packages": [{"PackageIdentifier": "Google.Chrome", "Version": "124.0"}, {"PackageIdentifier": "7zip.7zip"},
                         {"PackageIdentifier": "Acme.Studio", "Version": "2.0"}],
            "SourceDetails": {"Name": "winget", "Identifier": "Microsoft.Winget.Source_8wekyb3d8bbwe"}},
           {"Packages": [{"PackageIdentifier": "9NBLGGH4NNS1"}], "SourceDetails": {"Name": "msstore"}}]}


def pkg_with(env, doc=DOC):
    export_package([make_rule()], env.out, [], json.dumps(doc), expand=env.expand, now=NOW, winget_list_text=None)
    return env.out


def ids_of(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [p["PackageIdentifier"] for s in data["Sources"] for p in s["Packages"]]


# =============================================================================== 백엔드

def test_only_ids_keeps_schema_other_fields_and_ignores_case(env, tmp_path):
    pkg = pkg_with(env)
    target, bak = extract_winget(pkg, tmp_path / "o", only_ids=["google.CHROME", "9nblggh4nns1"])
    data = json.loads(target.read_text(encoding="utf-8"))
    assert bak is None and ids_of(target) == ["Google.Chrome", "9NBLGGH4NNS1"]
    assert data["$schema"] == DOC["$schema"] and data["WinGetVersion"] == "1.9" and data["CreationDate"] == "2030-01-01"
    assert data["Sources"][0]["SourceDetails"] == DOC["Sources"][0]["SourceDetails"]
    assert data["Sources"][0]["Packages"] == [{"PackageIdentifier": "Google.Chrome", "Version": "124.0"}]  # 버전 등 필드 보존
    assert len(data["Sources"]) == 2 and "7zip.7zip" not in target.read_text(encoding="utf-8")


def test_only_ids_drops_empty_source_and_none_means_all(env, tmp_path):
    pkg = pkg_with(env)
    target, _ = extract_winget(pkg, tmp_path / "o", only_ids=["7zip.7zip"])
    assert len(json.loads(target.read_text(encoding="utf-8"))["Sources"]) == 1
    full, bak = extract_winget(pkg, tmp_path / "o")  # 필터 없음 = 기존 동작(전체), 이전 파일은 .bak
    assert ids_of(full) == ["Google.Chrome", "7zip.7zip", "Acme.Studio", "9NBLGGH4NNS1"] and bak is not None


def test_only_ids_rejections_write_nothing(env, tmp_path):
    pkg = pkg_with(env)
    out = tmp_path / "o"
    with pytest.raises(PackageError, match="Nope.App"):
        extract_winget(pkg, out, only_ids=["Google.Chrome", "Nope.App"])  # 없는 id 가 있으면 일부만 쓰지 않는다
    with pytest.raises(PackageError, match="하나도"):
        extract_winget(pkg, out, only_ids=[])
    with pytest.raises(PackageError, match="올바르지 않은"):
        extract_winget(pkg, out, only_ids=["Google.Chrome & calc"])
    assert not out.exists()


def test_only_ids_keeps_old_file_as_bak(env, tmp_path):
    pkg = pkg_with(env)
    out = tmp_path / "o"
    out.mkdir()
    (out / "winget.json").write_text("내 파일", encoding="utf-8")
    target, bak = extract_winget(pkg, out, only_ids=["Acme.Studio"])
    assert bak is not None and bak.read_text(encoding="utf-8") == "내 파일" and ids_of(target) == ["Acme.Studio"]
    with pytest.raises(PackageError):  # 거부되면 기존 파일은 그대로
        extract_winget(pkg, out, only_ids=["Nope.App"])
    assert ids_of(target) == ["Acme.Studio"]


def test_api_run_winget_extract_only_ids_and_list(env, tmp_path):
    pkg = pkg_with(env)
    target, _ = api.run_winget_extract(pkg, tmp_path / "a", only_ids=["7zip.7zip"])
    assert ids_of(target) == ["7zip.7zip"]
    with pytest.raises(api.ApiError):
        api.run_winget_extract(pkg, tmp_path / "b", only_ids=["Nope.App"])
    rows = api.run_winget_list(pkg)
    assert [r["id"] for r in rows] == ["Google.Chrome", "7zip.7zip", "Acme.Studio", "9NBLGGH4NNS1"]
    assert rows[0] == {"id": "Google.Chrome", "version": "124.0", "source": "winget"} and rows[3]["source"] == "msstore"
    assert list_winget_packages(pkg) == rows


def test_id_validation_and_command_injection_rejected():
    for good in ("Google.Chrome", "7zip.7zip", "Microsoft.VisualStudioCode", "a-b_c.d"):
        assert valid_winget_id(good)
    for bad in ("", "a b", "a;b", "a&b", "a|b", '"a"', "a\nb", "a`b", "$(calc)", "-rf", ".x", "a" * 200, None, 5):
        assert not valid_winget_id(bad)
    with pytest.raises(ValueError):
        ic.selected_commands(None, ["Good.Id", "x & calc"])
    with pytest.raises(ValueError):
        ic.selected_commands('C:\\a"b\\winget.json', ["Good.Id"])
    got = ic.selected_commands("C:\\내 폴더\\winget.json", ["Google.Chrome", "google.chrome", "7zip.7zip"])
    assert got["ids"] == ["Google.Chrome", "7zip.7zip"]
    assert got["import"] == ('winget import -i "C:\\내 폴더\\winget.json" --ignore-unavailable '
                             '--accept-package-agreements --accept-source-agreements')
    assert got["each"][0] == "winget install --id Google.Chrome --exact --accept-package-agreements --accept-source-agreements"
    assert ic.selected_commands(None, ["A.b"])["import"] is None


def test_cli_list_and_only(env, tmp_path, capsys):
    pkg = pkg_with(env)
    assert cli.main(["winget-extract", str(pkg), "--list"]) == 0  # --output 없이도 된다
    out = capsys.readouterr().out
    assert "Google.Chrome" in out and "124.0" in out and "msstore" in out and not (tmp_path / "o").exists()
    o = tmp_path / "o"
    assert cli.main(["winget-extract", str(pkg), "--output", str(o), "--only", "google.chrome, 7zip.7zip"]) == 0
    out = capsys.readouterr().out
    assert ids_of(o / "winget.json") == ["Google.Chrome", "7zip.7zip"]
    assert "winget import -i" in out and "winget install --id Google.Chrome --exact" in out and "선택한 2개만" in out
    assert cli.main(["winget-extract", str(pkg), "--output", str(tmp_path / "p"), "--only", "Nope.App"]) == 1
    assert "Nope.App" in capsys.readouterr().err and not (tmp_path / "p").exists()
    assert cli.main(["winget-extract", str(pkg), "--output", str(tmp_path / "q"), "--only", "a;calc"]) == 1
    assert cli.main(["winget-extract", str(pkg), "--output", str(tmp_path / "q"), "--only", " , "]) == 1
    assert cli.main(["winget-extract", str(pkg)]) == 1  # --output 도 --list 도 없음
    assert not (tmp_path / "q").exists()
    assert cli.main(["winget-extract", str(pkg), "--output", str(tmp_path / "all")]) == 0  # 기존 전체 동작 유지
    assert len(ids_of(tmp_path / "all" / "winget.json")) == 4


# =============================================================================== 컨트롤러

def pick_result():
    """winget 으로 설치 가능한 줄 둘 + 개별 설치만 되는 줄 + 형식이 틀린 id 줄 + 설치됨/수동/설치 불필요 줄."""
    return ic.InstallCheck([
        ic.Item("Acme CAD", ic.WINGET, "목록에 있음", "1.0", winget_id="Acme.CAD", in_import_file=True),
        ic.Item("Zip Tool", ic.WINGET, "목록에 있음", "2.0", winget_id="7zip.7zip", in_import_file=True),
        ic.Item("Solo App", ic.WINGET, "개별 설치 가능", "3.0", winget_id="Solo.App", in_import_file=False),
        ic.Item("Old Tool", ic.INSTALLED, "이름 일치", "2.0", "2.0", "Old Tool", grade="확실"),
        ic.Item("Hand Tool", ic.MANUAL, "winget 에서 찾지 못함"),
        ic.Item("VC Runtime", ic.NOT_NEEDED, "런타임"),
        ic.Item("Weird", ic.WINGET, "id 가 이상함", winget_id="bad id; calc", in_import_file=True),
    ], has_winget_file=True, winget_ok=True)


def setup_controller(tmp_path):
    c, fake, ev = make(tmp_path)
    fake.install_default = pick_result()
    seen = []

    def extract(package, output, only_ids=None):
        seen.append((package, output, only_ids))
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "winget.json"
        bak = None
        if target.exists():
            bak = folder / "winget.json.bak"
            target.replace(bak)
        target.write_text(json.dumps({"only": only_ids}), encoding="utf-8")
        return target, bak
    fake.run_winget_extract = extract
    c.set_package("C:\\pkg.zip")
    c.start_install_check()
    return c, fake, ev, seen


def test_rows_mark_which_lines_can_be_picked(tmp_path):
    c, fake, ev, seen = setup_controller(tmp_path)
    rows = {r["name"]: r for r in c.install_rows()}
    assert {n for n, r in rows.items() if r["pickable"]} == {"Acme CAD", "Zip Tool", "Solo App"}
    assert not any(r["pick"] for r in rows.values())  # 기본은 전부 해제
    assert c.install_pick_count() == 0
    assert c.toggle_install_pick("Old Tool") is False and c.toggle_install_pick("Hand Tool") is False
    assert c.toggle_install_pick("Weird") is False and c.toggle_install_pick("없는 이름") is False  # 형식이 틀린 id 는 고를 수 없다
    assert c.toggle_install_pick("Acme CAD") is True and c.install_pick_count() == 1
    assert ("install_picks", "Acme CAD") in ev
    assert c.toggle_install_pick("Acme CAD") is False and c.install_pick_count() == 0


def test_prepare_selected_writes_filtered_file_and_commands(tmp_path):
    c, fake, ev, seen = setup_controller(tmp_path)
    with pytest.raises(GuardError) as e:
        c.prepare_selected_install()
    assert e.value.args[0] == T.INST_PICK_NONE and seen == []
    c.toggle_install_pick("Zip Tool")
    c.toggle_install_pick("Solo App")
    info = c.prepare_selected_install()
    folder = Path(c.default_desktop()) / T.INST_FOLDER_DEFAULT_NAME
    assert seen == [("C:\\pkg.zip", str(folder), ["7zip.7zip"])]  # 목록 파일에 있는 것만 파일로
    assert info["command"] == ic.import_command(str(folder / "winget.json")) and c.state.install_command == info["command"]
    assert info["individual"] == ["winget install --id Solo.App --exact --accept-package-agreements --accept-source-agreements"]
    assert info["count"] == 2 and info["backup"] == ""
    again = c.prepare_selected_install()
    assert again["backup"].endswith("winget.json.bak")


def test_prepare_selected_only_individual_makes_no_file(tmp_path):
    c, fake, ev, seen = setup_controller(tmp_path)
    c.toggle_install_pick("Solo App")
    info = c.prepare_selected_install()
    assert seen == [] and info["file"] == "" and info["command"].startswith("winget install --id Solo.App --exact")


def test_picks_survive_recheck_and_drop_when_installed(tmp_path):
    c, fake, ev, seen = setup_controller(tmp_path)
    c.toggle_install_pick("Acme CAD")
    c.toggle_install_pick("Zip Tool")
    c.start_install_check()  # 다시 확인(같은 결과): 선택 유지
    assert c.install_pick_count() == 2
    done = pick_result()
    done.items[0] = ic.Item("Acme CAD", ic.INSTALLED, "이름 일치", "1.0", "1.0", "Acme CAD", grade="확실")
    fake.install_queue = [done]
    c.start_install_check()  # 설치된 것은 선택에서 자동으로 빠진다
    assert c.install_pick_count() == 1 and [i.name for i in c.picked_items()] == ["Zip Tool"]
    assert next(r for r in c.install_rows() if r["name"] == "Acme CAD")["pick"] is False
    c.set_package("C:\\other.zip")  # 묶음이 바뀌면 선택도 버린다
    assert c.state.install_picks == set()


def test_prepare_selected_when_picks_vanished(tmp_path):
    c, fake, ev, seen = setup_controller(tmp_path)
    c.toggle_install_pick("Acme CAD")
    done = pick_result()
    done.items[0] = ic.Item("Acme CAD", ic.INSTALLED, "이름 일치", grade="확실")
    fake.install_queue = [done]
    c.start_install_check()
    with pytest.raises(GuardError) as e:
        c.prepare_selected_install()
    assert e.value.args[0] == T.INST_PICK_NOTHING_LEFT and seen == []


def test_full_batch_command_is_unchanged(tmp_path):
    c, fake, ev, seen = setup_controller(tmp_path)
    info = c.prepare_install_command()
    assert seen == [("C:\\pkg.zip", str(Path(c.default_desktop()) / T.INST_FOLDER_DEFAULT_NAME), None)]
    assert info["command"].startswith("winget import -i ")


# =============================================================================== GUI(tk)

def open_panel(ctx):
    app, c, fake = ctx.app, ctx.c, ctx.api
    fake.install_default = pick_result()
    seen = []

    def extract(package, output, only_ids=None):
        seen.append(only_ids)
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "winget.json").write_text("{}", encoding="utf-8")
        return folder / "winget.json", None
    fake.run_winget_extract = extract
    c.set_package("C:\\pkg.zip")
    c.go(S.NEW_INSTALL)
    app.update()
    return app.views[S.NEW_INSTALL].panel, c, fake, seen


def iid_of(p, name):
    return next(i for i in p.tree.get_children() if p.tree.set(i, "name") == name)


def click_cell(p, iid, col_id):
    p.tree.see(iid)
    p.tree.update()
    col = p.tree["displaycolumns"]
    col = list(p.tree["columns"]) if col == ("#all",) else list(col)
    x0 = sum(int(p.tree.column(c, "width")) for c in col[:col.index(col_id)])
    bbox = p.tree.bbox(iid, col_id)
    return SimpleNamespace(x=bbox[0] + bbox[2] // 2 if bbox else x0 + 3, y=bbox[1] + 3 if bbox else 5)


def test_pick_column_marks_and_toggles_only_in_cells(ctx):
    p, c, fake, seen = open_panel(ctx)
    assert p.pick_btn.cget("text") == T.INST_BTN_PICK and p.copy_btn.cget("text") == T.INST_BTN_COPY
    assert T.INST_BTN_COPY != T.INST_BTN_PICK and "전체" in T.INST_BTN_COPY
    assert p.pick_count.cget("text") == T.INST_PICK_COUNT.format(n=0)
    acme, old, hand = iid_of(p, "Acme CAD"), iid_of(p, "Old Tool"), iid_of(p, "Hand Tool")
    assert p.tree.set(acme, "pick") == T.UNCHECKED and p.tree.set(old, "pick") == T.LOCKED and p.tree.set(hand, "pick") == T.LOCKED
    p._click(click_cell(p, acme, "pick"))
    ctx.app.update()
    acme = iid_of(p, "Acme CAD")  # 표를 다시 그린다
    assert p.tree.set(acme, "pick") == T.CHECKED and p.pick_count.cget("text") == T.INST_PICK_COUNT.format(n=1)
    assert c.state.install_marks == set()  # '표시' 칸과 섞이지 않는다
    p._click(click_cell(p, iid_of(p, "Old Tool"), "pick"))  # 설치됨 줄은 눌러도 바뀌지 않는다
    assert c.install_pick_count() == 1
    p._click(SimpleNamespace(x=5, y=5))  # 머리글(y=5 는 머리글 영역) 클릭은 무시
    assert c.install_pick_count() == 1 and c.state.install_marks == set()
    p._click(click_cell(p, iid_of(p, "Hand Tool"), "mark"))  # 표시 칸은 기존대로 동작
    assert c.state.install_marks == {"Hand Tool"}


def test_pick_button_none_warns_then_writes_selected_only(ctx, monkeypatch):
    from tkinter import messagebox
    warned = []
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: warned.append(a))
    p, c, fake, seen = open_panel(ctx)
    p.pick_btn.invoke()
    assert warned and warned[-1][1] == T.INST_PICK_NONE and seen == []  # 선택이 없으면 안내만
    c.toggle_install_pick("Zip Tool")
    ctx.app.update()
    assert p.pick_count.cget("text") == T.INST_PICK_COUNT.format(n=1)
    p.pick_btn.invoke()
    ctx.app.update()
    folder = Path(c.default_desktop()) / T.INST_FOLDER_DEFAULT_NAME
    expected = ic.import_command(str(folder / "winget.json"))
    assert seen == [["7zip.7zip"]] and ctx.app.clipboard_get() == expected
    assert expected in p.last_dialog.text.get("1.0", "end") and "선택한 프로그램 1개" in p.last_dialog.text.get("1.0", "end")
    p.last_dialog.destroy()
    # 전체 일괄은 그대로: 필터 없이 꺼낸다
    p.copy_btn.invoke()
    ctx.app.update()
    assert seen == [["7zip.7zip"], None]
    p.last_dialog.destroy()
    p.recheck_btn.invoke()
    ctx.app.update()
    assert c.install_pick_count() == 1 and p.pick_count.cget("text") == T.INST_PICK_COUNT.format(n=1)  # 다시 확인 뒤에도 유지


def test_pick_texts_follow_glossary():
    for s in (T.INST_BTN_PICK, T.INST_PICK_NONE, T.INST_PICK_HINT, T.INST_PICK_NOTHING_LEFT, T.INST_PICKED,
              T.INST_PICK_EACH, T.INST_PICK_REST, T.INST_PICKED_ONLY_EACH):
        assert isinstance(s, str) and s


def test_docs_mention_pick_flow_and_stay_synced():
    root = Path(__file__).resolve().parents[1]
    md = (root / "docs" / "field-guide.md").read_text(encoding="utf-8")
    ht = (root / "docs" / "field-guide.html").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for needle in ("--only", "선택한 것만 목록 만들기"):
        assert needle in md and needle in ht and needle in readme
    assert "--list" in readme
