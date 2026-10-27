"""stage31: '설정 파일로 보이는 파일이 없음' 판정(no_config_found / config_check). 가짜 루트만 쓴다."""
import json

from pcmigrate import envpaths
from pcmigrate.discover import CONFIG_NONE, CONFIG_UNKNOWN, Candidate, to_dict
from pcmigrate.find_program import find_program, render
from test_stage25_install_dir import Fake, fake, touch  # noqa: F401


def roaming(fake):
    return fake.home / "AppData" / "Roaming"


def test_candidate_config_check_values():
    c = Candidate("%APPDATA%", "X", "p", True)
    assert c.config_check == CONFIG_UNKNOWN and not c.no_config_found  # 계산 전
    c.config_files, c.config_complete = 0, False
    assert c.config_check == CONFIG_UNKNOWN and not c.no_config_found  # 시간 상한: 0개로 단정 못 함
    c.config_complete = True
    assert c.config_check == CONFIG_NONE and c.no_config_found
    c.config_files = 3
    assert c.config_check == "있음 3개" and not c.no_config_found
    c.config_files, c.config_limit_files = 0, 2  # 상한으로 빠진 설정 파일이 있으면 '없음'이 아니다
    assert not c.no_config_found and c.config_check.startswith("있음")
    c.config_limit_files, c.license_only = 0, True
    assert not c.no_config_found and c.config_check == CONFIG_UNKNOWN


def test_find_folder_without_config_files(fake):
    touch(roaming(fake) / "Acme Paint" / "data.bin", b"x" * 2048)
    touch(roaming(fake) / "Acme Paint" / "sub" / "model.dat", b"y" * 100)
    res = find_program(fake.prog(loc=fake.tmp / "nowhere"), [], envpaths.expand_env)
    assert res.candidates and all(c.no_config_found and c.config_check == CONFIG_NONE for c in res.candidates)
    assert res.no_config_found and res.config_check == CONFIG_NONE
    text = render(res)
    assert "설정 파일로 보이는 파일이 없습니다(폴더 용량" in text and "--extra-mode all" in text
    d = json.loads(render(res, "json"))
    assert d["no_config_found"] is True and d["config_check"] == "없음"
    assert all(c["no_config_found"] and c["config_check"] == "없음" for c in d["candidates"])
    assert "설정 파일로 보이는 파일이 없습니다" in d["no_config_message"]
    dd = to_dict(res.as_discovery())
    assert all("no_config_found" in c and "config_check" in c for e in dd["programs_without_rule"] for c in e["candidates"])


def test_find_folder_with_config_file_is_not_flagged(fake):
    touch(roaming(fake) / "Acme Paint" / "settings.ini", "[a]\nx=1\n")
    res = find_program(fake.prog(loc=fake.tmp / "nowhere"), [], envpaths.expand_env)
    assert not res.no_config_found and res.config_check == "있음 1개"
    assert "설정 파일로 보이는 파일이 없습니다" not in render(res)


def test_install_dir_without_config_counts_as_none(fake):
    touch(fake.app / "tool.exe", b"MZ")
    touch(fake.app / "core.dll", b"MZ")
    res = find_program(fake.prog(), [], envpaths.expand_env)
    assert res.install_dir_found and not res.install_files and not res.candidates
    assert res.install_config_check == CONFIG_NONE and res.no_config_found  # 설치 폴더만 훑어도 같은 판정
    text = render(res)
    assert "설치 폴더에서도 설정 형식 파일을 찾지 못했습니다" in text and "설정 파일로 보이는 파일이 없습니다(폴더 용량" in text
    assert json.loads(render(res, "json"))["install_config_check"] == "없음"


def test_install_dir_with_config_is_yes(fake):
    touch(fake.app / "settings.ini", "[a]")
    res = find_program(fake.prog(), [], envpaths.expand_env)
    assert res.install_config_check == "있음 1개" and not res.no_config_found


def test_nothing_found_is_unknown_not_none(fake):
    res = find_program(fake.prog(loc=fake.tmp / "nowhere"), [], envpaths.expand_env)
    assert not res.candidates and not res.no_config_found and res.config_check == CONFIG_UNKNOWN


def test_cli_discover_program_prints_no_config_line(fake, capsys, tmp_path):
    from conftest import make_rules_dir
    touch(roaming(fake) / "Acme Paint" / "data.bin", b"x" * 10)
    rules = make_rules_dir(tmp_path / "rules")
    from pcmigrate import cli
    from pcmigrate.api import Deps
    deps = Deps(registry=lambda: [fake.prog(loc=fake.tmp / "nowhere")], winget_export=lambda: None, winget_list=lambda: None)
    import pcmigrate.cli as c
    orig = c._deps
    c._deps = lambda: deps
    try:
        assert fake.cli("discover", "--rules", str(rules), "--program", "Acme Paint") == 0
    finally:
        c._deps = orig
    cap = capsys.readouterr()
    assert "설정 파일로 보이는 파일이 없습니다" in cap.out and "[안내] 설정 파일로 보이는 파일이 없습니다" in cap.err


# --------------------------------------------------------------------------- 화면(승인된 범위): 구 PC 2단계 표 / 설정 찾기 결과
import pytest

from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from test_gui_controller import cand
from test_stage30b import new_ctl, set_discovery

tk = pytest.importorskip("tkinter")
from test_gui_smoke import ctx  # noqa: E402,F401


def _none_cand(name="A", size=5000):
    c = cand(name, size=size)
    c.config_files, c.config_bytes, c.config_complete = 0, 0, True
    return c


def test_cells_show_none_with_detail_and_total_counts_zero(tmp_path):
    c, api, ev = new_ctl(tmp_path)
    a = _none_cand()
    set_discovery(c, a)
    cell = c.candidate_cfg_cells()[a.key]
    assert cell["cfg"] == T.DISC_CFG_NONE and cell["none"] and "설정 파일로 보이는 파일이 없습니다" in cell["tip"] and "4.9KB" in cell["tip"]
    c.set_selected(a.key, True)
    assert c.selection_config_text().startswith("선택 1개 · 설정 파일 0개")
    assert c.is_no_config(a.key)
    c.set_extra_mode("all")  # 폴더 전체 담기: 담을 수 있으므로 '없음'이 아니다
    assert not c.is_no_config(a.key) and c.candidate_cfg_cells()[a.key]["cfg"] != T.DISC_CFG_NONE


def test_find_summary_mentions_no_config(tmp_path, fake):
    c, api, ev = new_ctl(tmp_path)
    touch(roaming(fake) / "Acme Paint" / "data.bin", b"x" * 10)
    fake.app.mkdir(parents=True, exist_ok=True)
    res = find_program(fake.prog(), [], envpaths.expand_env)
    text = c.find_summary({"result": res, "name": "Acme Paint", "new": 1, "dup": 0})
    assert "설정 파일로 보이는 파일이 없습니다" in text and "설치 폴더 안에도" in text


def test_tk_discover_none_is_gray_and_check_shows_notice(ctx):
    app, c = ctx.app, ctx.c
    a = _none_cand()
    set_discovery(c, a, cand("B"))
    c.go(S.OLD_DISCOVER)
    app.update()
    v = app.views[S.OLD_DISCOVER]
    iids = list(v.tree.get_children())
    first = next(i for i in iids if v.rows[i].key == a.key)
    assert v.tree.item(first)["values"][4] == T.DISC_CFG_NONE and "noconfig" in v.tree.item(first)["tags"]
    v.tree.selection_set(first)
    v._show_detail()
    assert "설정 파일로 보이는 파일이 없습니다" in v.detail.cget("text")
    v._toggle(first)  # 체크는 되고, 안내만 나온다
    assert a.key in c.state.selected and any(k == "info" and T.DISC_NO_CONFIG_CHECK in args for k, args in ctx.shown)
    c.set_selected(a.key, False)  # 표가 다시 그려져 줄 번호가 바뀌므로 직접 해제
    ctx.shown.clear()
    c.set_extra_mode("all")
    app.update()
    v._toggle(next(i for i in v.tree.get_children() if v.rows[i].key == a.key))
    assert a.key in c.state.selected and not ctx.shown
