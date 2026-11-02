"""'찾을 파일 형식' 설정이 내 파일 찾기(find_data/data_api)와 명령줄(find-data, find-exts)에 연결되는지 시험.

가짜 폴더 트리와 임시 설정 폴더만 쓴다(실제 드라이브·실제 %LOCALAPPDATA% 를 건드리지 않는다).
"""
import json
from pathlib import Path

import pytest

from pcmigrate import cli, data_api, find_data as fd, find_exts as fx


def put(path: Path, data: bytes = b"x" * 10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def tree(tmp_path: Path) -> Path:
    r = tmp_path / "pc"
    put(r / "Work" / "A" / "plan.dwg")
    put(r / "Work" / "A" / "render.jpg")
    put(r / "Icons" / "a.svg")
    put(r / "Icons" / "b.svg")
    put(r / "Web" / "x.svg")  # .svg 3개: 기본값에서는 후보(등급 C 3개 규칙)
    put(r / "Web" / "y.svg")
    put(r / "Web" / "z.svg")
    put(r / "Drone" / "proj1" / "model.zep")  # 우리가 모르는 형식 1개
    put(r / "Notes" / "n.txt")
    return r


def run(root, **kw):
    kw.setdefault("user_folders", {})
    kw.setdefault("exclude_paths", [])
    kw.setdefault("clock", lambda: 0.0)
    return data_api.run_find_data([root], **kw)


def names(res, root):
    return sorted(Path(p.path).relative_to(root).as_posix() for p in res.projects)


def test_default_behavior_identical_without_settings(tmp_path):
    r = tree(tmp_path)
    base = fd.find_data([r], user_folders={}, exclude_paths=[], clock=lambda: 0.0)
    via_api = run(r)
    assert json.dumps(via_api.to_dict(), ensure_ascii=False, sort_keys=True) == json.dumps(base.to_dict(), ensure_ascii=False, sort_keys=True)
    assert "ext_notes" not in via_api.to_dict() and fd.summary_lines(via_api) == fd.summary_lines(base)
    assert "Drone/proj1" not in names(base, r) and "Work/A" in names(base, r) and "Web" in names(base, r)
    assert "Icons" not in names(base, r)  # .svg 2개: 등급 C 3개 규칙 유지
    # 기본 표를 명시해도, 사용자 설정 무시 옵션도 같다
    assert names(run(r, use_user_exts=False), r) == names(base, r)


def test_classify_name_default_and_table_argument():
    assert fd.classify_name("a.svg") == ("photo", "C")
    assert fd.classify_name("a.zep") is None
    t = fx.effective_table(fx.ExtConfig(added={".zep": "design"}, removed=[".svg"]))
    assert fd.classify_name("a.zep", 0, fd.LARGE_OTHER_BYTES, t) == ("design", "B")
    assert fd.classify_name("a.svg", 0, fd.LARGE_OTHER_BYTES, t) is None
    assert fd.EXT_TABLE[".svg"] == ("photo", "C") and ".zep" not in fd.EXT_TABLE  # 모듈 표는 그대로


def test_user_added_single_file_folder_becomes_candidate(tmp_path):
    r = tree(tmp_path)
    cfg = fx.add(fx.ExtConfig(), ".zep", "design").cfg
    res = run(r, ext_config=cfg)
    assert "Drone/proj1" in names(res, r)
    item = next(p for p in res.projects if p.path.endswith("proj1"))
    assert item.categories["design"]["files"] == 1 and ".zep" in item.top_extensions
    assert "사용자 추가" in item.basis and ".zep" in item.basis
    lines = "\n".join(fd.summary_lines(res))
    assert "사용자 추가 형식: .zep 1개" in lines and "찾을 파일 형식 설정을 적용했습니다(추가 1개, 삭제 0개)" in lines
    assert res.to_dict()["ext_notes"]
    # 다른 폴더의 근거 문구에는 '사용자 추가'가 없다
    other = next(p for p in res.projects if p.path.endswith("A"))
    assert "사용자 추가" not in other.basis


def test_user_added_doc_category_single_file_also_candidate(tmp_path):
    r = tmp_path / "pc"
    put(r / "Proj" / "data.kdc")
    assert names(run(r), r) == []
    res = run(r, ext_config=fx.add(fx.ExtConfig(), ".kdc", "doc").cfg)
    assert names(res, r) == ["Proj"]  # 문서 분류 기본 규칙(3개 이상)과 달리 사용자 추가는 1개로 후보
    # 분류를 고르지 않으면 후보 조건에 쓰이지 않는다
    assert names(run(r, ext_config=fx.add(fx.ExtConfig(), ".kdc", "doc").cfg, categories=["design"]), r) == []


def test_removed_ext_not_classified(tmp_path):
    r = tree(tmp_path)
    cfg = fx.remove(fx.ExtConfig(), ".svg").cfg
    res = run(r, ext_config=cfg)
    assert "Web" not in names(res, r) and "Icons" not in names(res, r)
    assert "삭제 1개" in "\n".join(fd.summary_lines(res))
    # 삭제한 .dwg: 도면 폴더가 사진(.jpg)만으로 후보가 된다
    cfg2 = fx.remove(fx.ExtConfig(), ".dwg").cfg
    a = next(p for p in run(r, ext_config=cfg2).projects if p.path.endswith("A"))
    assert "design" not in a.categories and a.categories["photo"]["files"] == 1


def test_removed_ext_big_file_rule_unchanged(tmp_path):
    r = tmp_path / "pc"
    put(r / "Big" / "huge.dwg", b"x" * 300)
    cfg = fx.remove(fx.ExtConfig(), ".dwg").cfg
    assert names(run(r, ext_config=cfg), r) == []
    res = run(r, ext_config=cfg, other_large_bytes=100, categories=["design", "other"])
    assert names(res, r) == ["Big"] and res.projects[0].categories == {"other": {"files": 1, "bytes": 300}}


def test_user_added_beats_other_excluded_and_large_rule(tmp_path):
    r = tmp_path / "pc"
    put(r / "Tools" / "x.exe", b"x" * 300)
    assert names(run(r, other_large_bytes=100, categories=["design", "other"]), r) == []  # .exe 는 큰 기타에서 제외
    cfg = fx.add(fx.ExtConfig(), ".exe", "design").cfg  # 명시적으로 추가했으므로 우선
    res = run(r, ext_config=cfg, other_large_bytes=100)
    assert names(res, r) == ["Tools"] and res.projects[0].categories == {"design": {"files": 1, "bytes": 300}}
    assert "사용자 추가" in res.projects[0].basis
    # 추가한 확장자가 100MB 이상이어도 '기타 큰 파일'이 아니라 지정한 분류
    put(r / "Zz" / "big.zep", b"x" * 300)
    res2 = run(r, ext_config=fx.add(fx.ExtConfig(), ".zep", "photo").cfg, other_large_bytes=100)
    z = next(p for p in res2.projects if p.path.endswith("Zz"))
    assert set(z.categories) == {"photo"}


def test_settings_file_is_read_once_at_start_and_consistent(tmp_path, monkeypatch):
    r = tree(tmp_path)
    fx.save(fx.add(fx.ExtConfig(), ".zep", "design").cfg)  # SETTINGS_DIR 은 conftest 의 임시 폴더
    calls = []
    real_load = fx.load

    def counting_load(*a, **k):
        calls.append(1)
        return real_load(*a, **k)
    monkeypatch.setattr(fx, "load", counting_load)
    changed = []
    orig_scan = fd._Finder.scan_dir

    def scan(self, *a, **k):
        if not changed:  # 훑는 도중 설정을 지운다: 이번 결과에는 영향이 없어야 한다
            changed.append(1)
            fx.reset()
        return orig_scan(self, *a, **k)
    monkeypatch.setattr(fd._Finder, "scan_dir", scan)
    res = data_api.run_find_data([r], user_folders={}, exclude_paths=[])
    assert len(calls) == 1 and "Drone/proj1" in names(res, r)
    assert fx.load().is_default  # 도중에 바뀐 설정은 다음 찾기부터 적용


def test_run_find_data_reads_saved_settings_by_default(tmp_path):
    r = tree(tmp_path)
    assert "Drone/proj1" not in names(run(r), r)
    fx.save(fx.add(fx.ExtConfig(), ".zep", "design").cfg)
    assert "Drone/proj1" in names(run(r), r)
    assert "Drone/proj1" not in names(run(r, use_user_exts=False), r)


def test_damaged_settings_find_still_works_with_warning(tmp_path):
    r = tree(tmp_path)
    d = fx.settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / fx.FILE_NAME).write_text("{broken", encoding="utf-8")
    res = run(r)
    base = fd.find_data([r], user_folders={}, exclude_paths=[], clock=lambda: 0.0)
    assert names(res, r) == names(base, r)
    assert any("기본값으로" in n for n in res.ext_notes)
    assert (d / fx.FILE_NAME).read_text(encoding="utf-8") == "{broken"  # 손상 파일은 그대로


# ------------------------------------------------------------------ CLI find-data
def test_cli_find_data_uses_settings_and_default_exts_flag(tmp_path, capsys):
    r = tree(tmp_path)
    fx.save(fx.add(fx.ExtConfig(), ".zep", "design").cfg)
    assert cli.main(["find-data", "--roots", str(r), "--json"]) == 0
    with_cfg = json.loads(capsys.readouterr().out)
    assert any(p["path"].endswith("proj1") for p in with_cfg["projects"]) and with_cfg["ext_notes"]
    assert cli.main(["find-data", "--roots", str(r), "--json", "--default-exts"]) == 0
    default = json.loads(capsys.readouterr().out)
    assert not any(p["path"].endswith("proj1") for p in default["projects"]) and "ext_notes" not in default
    assert cli.main(["find-data", "--roots", str(r)]) == 0
    assert "사용자 추가 형식: .zep" in capsys.readouterr().out


def test_cli_find_data_help_mentions_option(capsys):
    with pytest.raises(SystemExit):
        cli.main(["find-data", "--help"])
    out = capsys.readouterr().out
    assert "--default-exts" in out and "find-exts" in out


# ------------------------------------------------------------------ CLI find-exts
def jmain(capsys, *argv):
    code = cli.main(["find-exts", *argv, "--json"])
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_cli_list_default_and_text(capsys):
    assert cli.main(["find-exts", "list"]) == 0
    out = capsys.readouterr().out
    assert "[설계]" in out and ".dwg" in out and "[압축]" in out and "사용자 추가 0개" in out
    code, data, _ = jmain(capsys, "list")
    assert code == 0 and data["is_default"] and data["total"] == len(fd.EXT_TABLE) and data["path"].endswith("find-exts.json")


def test_cli_add_remove_reset_flow(capsys):
    code, data, err = jmain(capsys, "add", ".ZEP", "--category", "design")
    assert code == 0 and data["changed"] and data["ok"] and fx.config_path().exists()
    assert json.loads(fx.config_path().read_text(encoding="utf-8"))["added"] == {".zep": "design"}
    code, data, _ = jmain(capsys, "add", "zep", "--category", "설계")  # 중복: 안내만(성공)
    assert code == 0 and not data["changed"] and "이미" in data["message"]
    code, data, _ = jmain(capsys, "remove", ".svg")
    assert code == 0 and data["changed"] and fx.load().removed == [".svg"]
    code, data, _ = jmain(capsys, "remove", ".zzz")  # 없는 형식 삭제는 실패 코드
    assert code == 1 and not data["ok"] and "없습니다" in data["message"]
    assert cli.main(["find-exts", "list"]) == 0
    text = capsys.readouterr().out
    assert "+.zep" in text and "삭제한 기본 형식: .svg(사진)" in text
    code, data, _ = jmain(capsys, "remove", ".zep")
    assert code == 0 and fx.load().added == {}
    code, data, _ = jmain(capsys, "reset")
    assert code == 0 and data["changed"] and data["backup"] and data["settings"]["is_default"]
    assert not fx.config_path().exists()
    code, data, _ = jmain(capsys, "reset")
    assert code == 0 and not data["changed"] and "이미 기본값" in data["message"]
    assert list(fx.settings_dir().glob("find-exts.json.bak*"))  # 이전 설정은 보관됨


def test_cli_invalid_input_exit_2_and_no_file(capsys):
    for argv in (["add", "*.dwg", "--category", "design"], ["add", ".a b", "--category", "design"], ["add", ".zep", "--category", "zzz"],
                 ["add", "", "--category", "design"], ["remove", "a/b"]):
        assert cli.main(["find-exts", *argv]) == 2, argv
        assert capsys.readouterr().err.strip()
    assert not fx.config_path().exists()


def test_cli_dangerous_ext_warns_but_adds(capsys):
    assert cli.main(["find-exts", "add", ".exe", "--category", "doc"]) == 0
    cap = capsys.readouterr()
    assert "프로그램 파일" in cap.err and ".exe" in fx.load().added
    code, data, _ = jmain(capsys, "add", ".dll", "--category", "doc")
    assert code == 0 and data["warnings"] and ".dll" in fx.load().added


def test_cli_missing_category_and_action_are_usage_errors(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["find-exts", "add", ".zep"])
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        cli.main(["find-exts"])
    capsys.readouterr()


def test_cli_damaged_settings_warns_and_still_lists(capsys):
    d = fx.settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / fx.FILE_NAME).write_text("{broken", encoding="utf-8")
    assert cli.main(["find-exts", "list"]) == 0
    cap = capsys.readouterr()
    assert "[경고]" in cap.err and "기본값" in cap.err and "[설계]" in cap.out
    # 손상 상태에서 add 하면 새 설정으로 저장되고, 손상 파일은 .bak 으로 남는다
    assert cli.main(["find-exts", "add", ".zep", "--category", "design"]) == 0
    capsys.readouterr()
    assert fx.load().added == {".zep": "design"}
    assert any(p.read_text(encoding="utf-8") == "{broken" for p in d.glob("find-exts.json.bak*"))


def test_cli_save_failure_reported(capsys, monkeypatch):
    def fail(*a, **k):
        raise fx.FindExtsError("설정을 저장하지 못했습니다.")
    monkeypatch.setattr(fx, "save", fail)
    assert cli.main(["find-exts", "add", ".zep", "--category", "design"]) == 1
    assert "저장하지 못했습니다" in capsys.readouterr().err


def test_cli_output_has_no_home_path(capsys, monkeypatch, tmp_path):
    home = str(Path.home())
    monkeypatch.setattr(fx, "SETTINGS_DIR", str(Path(home) / "pcm-test-settings-nonexistent"))
    assert cli.main(["find-exts", "list", "--json"]) == 0
    out = capsys.readouterr().out
    assert home not in out and "%USERPROFILE%" in out  # 경로를 보여 줄 때 사용자 이름이 새지 않는다


# ------------------------------------------------------------------ '기타'(misc) 분류와 찾기
def test_misc_single_file_folder_is_candidate_and_shown(tmp_path):
    r = tree(tmp_path)
    cfg = fx.add(fx.ExtConfig(), ".zep", "기타").cfg
    res = run(r, ext_config=cfg)
    assert "Drone/proj1" in names(res, r)
    item = next(p for p in res.projects if p.path.endswith("proj1"))
    assert item.categories == {"misc": {"files": 1, "bytes": 10}} and ".zep" in item.top_extensions
    assert "기타(직접 추가) 1개" in item.basis and "사용자 추가 형식: .zep 1개" in item.basis
    assert list(res.categories) == list(fd.DEFAULT_CATEGORIES)  # 결과가 되울리는 선택 분류는 그대로
    assert res.drives[0]["categories"]["misc"]["files"] == 1
    # 찾기 화면 행: 모든 종류가 켜진 기본 보기에서는 보이고, 설계만 켜면 빠진다(기타 칩은 기타 형식이 설정에 있을 때 화면에 나온다)
    from pcmigrate.gui.files_flow import row_from_item, row_visible
    row = row_from_item(item, False)
    assert row_visible(row, {"design", "photo", "video", "doc"}) and row_visible(row, {"design"})  # 기타 칩이 켜져 있으면 보임
    assert not row_visible(row, {"design"}, False)  # 기타 칩을 끄면 가려진다


def test_misc_beats_excluded_list_and_large_other_rule(tmp_path):
    r = tmp_path / "pc"
    put(r / "Tools" / "x.exe", b"x" * 300)
    res = run(r, ext_config=fx.add(fx.ExtConfig(), ".exe", "기타").cfg, other_large_bytes=100)
    assert names(res, r) == ["Tools"] and list(res.projects[0].categories) == ["misc"]  # 큰 기타("other")가 아니라 misc


def test_without_misc_results_are_unchanged(tmp_path):
    r = tree(tmp_path)
    base = fd.find_data([r], user_folders={}, exclude_paths=[], clock=lambda: 0.0)
    assert '"misc"' not in json.dumps(base.to_dict())
    assert json.dumps(run(r).to_dict(), sort_keys=True) == json.dumps(base.to_dict(), sort_keys=True)


def test_cli_misc_input_both_languages_and_list(capsys):
    assert cli.main(["find-exts", "add", ".zep", "--category", "기타"]) == 0
    assert cli.main(["find-exts", "add", ".kdc", "--category", "other"]) == 0
    capsys.readouterr()
    assert fx.load().added == {".zep": "misc", ".kdc": "misc"}
    assert cli.main(["find-exts", "list"]) == 0
    assert "[기타] 2개: +.kdc +.zep" in capsys.readouterr().out or "[기타] 2개: +.zep +.kdc" in capsys.readouterr().out
    assert cli.main(["find-exts", "list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["categories"][-1]["category"] == "misc" and data["categories"][-1]["count"] == 2
    fx.reset()
    assert cli.main(["find-exts", "list"]) == 0
    assert "[기타] 0개" in capsys.readouterr().out
