"""stage25 GUI: 설치 폴더 설정 후보(찾기 결과 표), 내보내기 선택 분리, 신 PC 미리보기의 별도 동의(기본 복원 안 함). 가짜 환경만 쓴다."""
from types import SimpleNamespace

import pytest

from pcmigrate import api as real_api
from pcmigrate.copier import FileResult, Report
from pcmigrate.detect import Program
from pcmigrate.discover import Candidate, Discovery, Link, ProgramEntry
from pcmigrate.find_program import ProgramFind
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi, item, kinds, plan  # noqa: F401
from test_gui_smoke import ctx  # noqa: F401


def inst_cand(name="settings.ini", secret=None, program="Acme Paint"):
    c = Candidate("%ProgramFiles%", f"Acme Paint/{name}", f"C:\\fake\\Program Files\\Acme Paint\\{name}", False,
                  Link([program], "확실", "설치 위치", False), size=10, files=1, scan_complete=True)
    c.badge, c.install_program, c.install_folder = "설치 폴더 설정", program, "Acme Paint"
    c.secret = list(secret or [])
    return c


def plain_cand(rel="Cfg"):
    return Candidate("%APPDATA%", rel, f"C:\\fake\\{rel}", True, Link(["Acme Paint"], "추정", "x", False), size=5, files=1)


def set_discovery(c, *cands):
    d = Discovery()
    d.programs = [ProgramEntry(Program("Acme Paint"), list(cands))]
    c.state.discovery = d


class Api(FakeApi):
    def __init__(self):
        super().__init__()
        self.import_calls = []
        self.export_kw = []

    def run_export(self, rules_dir, output, *, extra=None, allow_secrets=False, deps=None, extra_items=None, install_items=None):
        self.export_kw.append({"extra_items": extra_items, "install_items": install_items, "allow_secrets": allow_secrets})
        return SimpleNamespace(manifest={"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []})

    def run_import(self, package, rules_dir, *, dry_run=False, yes=False, allow_extra=False, deps=None, allow_install_dir=False):
        self.import_calls.append({"dry_run": dry_run, "yes": yes, "allow_install_dir": allow_install_dir})
        if dry_run or not yes:
            return self.preview_for(allow_install_dir)
        rep = Report(dry_run=False, results=[FileResult("e", "C:\\x\\a.txt", "copied", 1, "x")])
        return real_api.ImportResult(self.preview.plan, "applied", 0, rep)

    def preview_for(self, allow):
        p = plan([item()] if not allow else [item(), {**item(status="would_overwrite", target="C:\\pf\\Acme Paint\\s.ini",
                                                          backup="C:\\pf\\Acme Paint\\s.ini.bak"), "install": True}])
        p.install = [{"entry": "e", "target": "C:\\pf\\Acme Paint\\s.ini", "status": "would_overwrite" if allow else "would_overwrite",
                      "backup": "C:\\pf\\Acme Paint\\s.ini.bak", "size": 1, "program": "Acme Paint"},
                     {"entry": "e2", "target": "C:\\pf\\Gone\\g.ini", "status": "missing_dir", "backup": "", "size": 1, "program": "Gone"}]
        p.install_allowed = bool(allow)
        p.install_programs = [{"program": "Acme Paint", "old_version": "1.0", "new_version": "2.0", "files": 1}]
        return real_api.ImportResult(p, "dry-run", 0)


@pytest.fixture
def env(tmp_path):
    api = Api()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path,
                   make_items=lambda data: [("item", x["root"], x["rel"]) for x in data["candidates"]])
    c.make_install_items = lambda data: [("install", x["program"], x["root"], x["folder"], x["rel"]) for x in data["install_dir"]]
    events = []
    c.listener = lambda kind, payload=None: events.append((kind, payload))
    return c, api, events


# --------------------------------------------------------------------- 구 PC: 찾기 결과 표

def test_find_result_adds_install_candidates_unchecked(env):
    c, api, ev = env
    set_discovery(c, plain_cand())
    res = ProgramFind(Program("Acme Paint"), tokens=[("acme", "이름")], candidates=[],
                      install_files=[{"path": "C:\\p\\a.ini", "size": 1, "why": "x"}], install_candidates=[inst_cand("a.ini"), inst_cand("b.cfg")],
                      install_excluded=[{"path": "C:\\p\\big.xml", "size": 9 * 1024 * 1024, "why": "크기"}])
    api.run_find_program = lambda rules_dir, name, *, deps=None, cancel=None, progress_info=None: res
    c.start_find_program("Acme Paint")
    assert c.state.selected == set()  # 자동 선택 없음
    rows = {r.path.rsplit("\\", 1)[-1]: r for r in c.candidate_rows()}
    assert rows["a.ini"].install and rows["a.ini"].badge == T.BADGE_INSTALL_DIR and rows["a.ini"].selectable
    assert not rows["Cfg"].install
    assert "관리자 권한" in rows["a.ini"].badge_note and "같은 버전" in rows["a.ini"].badge_note
    assert "관리자 권한" in rows["a.ini"].detail_cache
    info = [p for k, p in ev if k == "find_done"][0]
    msg = c.find_summary(info)
    assert "설치 폴더 안의 설정 형식 파일 2개를 표에 더했습니다" in msg and "자동 선택은 하지 않습니다" in msg and "5MB" in msg


def test_select_all_keeps_secret_rule_for_install_candidates(env):
    c, api, ev = env
    secret = inst_cand("api_token.json", secret=["api_token.json"])
    set_discovery(c, inst_cand("a.ini"), secret)
    n, skipped = c.select_all_candidates()
    assert (n, skipped) == (1, 1) and secret.key not in c.state.selected
    with pytest.raises(GuardError):
        c.set_selected(secret.key, True)
    c.set_secrets_ack(True)
    c.set_selected(secret.key, True)
    assert secret.key in c.state.selected


def test_export_sends_install_selection_separately(env, tmp_path):
    c, api, ev = env
    a, p = inst_cand("a.ini"), plain_cand()
    set_discovery(c, a, p)
    c.set_selected(a.key, True)
    c.set_selected(p.key, True)
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    kw = api.export_kw[-1]
    assert kw["extra_items"] == [("item", "%APPDATA%", "Cfg")]
    assert kw["install_items"] == [("install", "Acme Paint", "%ProgramFiles%", "Acme Paint", "Acme Paint/a.ini")]


def test_export_with_install_needs_supporting_api(env, tmp_path):
    c, api, ev = env
    a = inst_cand("a.ini")
    set_discovery(c, a)
    c.set_selected(a.key, True)
    old = FakeApi()  # install_items 를 모르는 api
    c.api = old
    with pytest.raises(GuardError):
        c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)


def test_export_without_install_does_not_send_the_option(env, tmp_path):
    c, api, ev = env
    p = plain_cand()
    set_discovery(c, p)
    c.set_selected(p.key, True)
    c.start_export(str(tmp_path / "p.zip"), write_confirmed=True)
    assert api.export_kw[-1]["install_items"] is None


# --------------------------------------------------------------------- 신 PC: 미리보기/동의

def test_preview_default_is_not_restored_and_shows_notes(env):
    c, api, ev = env
    c.state.package = "x.zip"
    c.start_preview()
    assert api.import_calls[-1]["allow_install_dir"] is False and c.state.install_consent is False
    rows = c.preview_rows()
    inst = [r for r in rows if r["install"]]
    assert len(inst) == 2 and all(r["status"] == T.PREV_INSTALL_NOT_RESTORED for r in inst) and all(r["backup"] == "" for r in inst)
    notes = "\n".join(c.install_note_lines())
    assert "설치 폴더 설정 2개" in notes and "[설치 폴더 설정도 복원]" in notes and "관리자 권한 필요" in notes
    assert "먼저 프로그램을 설치한 뒤 다시 복원해 주세요" in notes and "같은 버전의 프로그램에서 가져온 것입니다" in notes
    assert "버전이 다르면 프로그램이 오작동할 수 있습니다" in notes
    assert c.install_count() == 0


def test_consent_reruns_preview_and_is_required_to_be_current(env):
    c, api, ev = env
    c.state.package = "x.zip"
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.set_install_consent(True)
    assert api.import_calls[-1] == {"dry_run": True, "yes": False, "allow_install_dir": True}  # 미리보기를 다시 만든다
    assert c.state.preview_install_consent is True and c.state.preview_checked is False  # 확인 표시는 지워진다
    assert c.install_count() == 1
    rows = {r["target"]: r for r in c.preview_rows()}
    assert rows["C:\\pf\\Acme Paint\\s.ini"]["backup"].endswith(".bak") and rows["C:\\pf\\Acme Paint\\s.ini"]["status"] == T.PREV_STATUS["would_overwrite"]
    notes = "\n".join(c.install_note_lines())
    assert "복원합니다(관리자 권한 필요)" in notes and "구 PC 버전 1.0, 이 PC 버전 2.0" in notes
    # 체크를 바꾼 뒤 미리보기가 낡았으면 실행할 수 없다
    c.state.install_consent = False
    ok, why = c.can_apply()
    assert not ok and why == T.G_NEED_PREVIEW


def test_apply_passes_consent_once_then_resets(env):
    c, api, ev = env
    c.state.package = "x.zip"
    c.set_install_consent(True)
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    assert api.import_calls[-1] == {"dry_run": False, "yes": True, "allow_install_dir": True}
    assert c.state.install_consent is False and c.state.preview_install_consent is False  # 다음 복원은 다시 '복원 안 함'


def test_apply_default_does_not_send_consent(env):
    c, api, ev = env
    c.state.package = "x.zip"
    c.start_preview()
    c.set_preview_checked(True)
    c.set_bak_understood(True)
    c.confirm_final(True)
    c.start_apply()
    assert api.import_calls[-1]["allow_install_dir"] is False


def test_new_package_resets_consent(env):
    c, api, ev = env
    c.state.package = "x.zip"
    c.set_install_consent(True)
    c.set_package("y.zip")
    assert c.state.install_consent is False


def test_consent_with_unsupported_api_is_guarded(env):
    c, api, ev = env
    c.api = FakeApi()
    c.state.package = "x.zip"
    with pytest.raises(GuardError):
        c.set_install_consent(True)


def test_blocked_preview_shows_admin_message_in_problems(env):
    c, api, ev = env
    c.state.package = "x.zip"
    p = plan([], problems=["프로그램 설치 폴더에 쓸 수 있는 권한이 없습니다. 관리자 권한으로 다시 실행해 주세요."])
    c.state.preview, c.state.preview_package = real_api.ImportResult(p, "blocked", 0), "x.zip"
    assert "관리자 권한으로 다시 실행해 주세요" in c.preview_problems()[0]
    assert c.can_apply()[0] is False


# --------------------------------------------------------------------- tkinter

def test_preview_view_shows_install_checkbox_only_when_present(ctx):
    app, c = ctx.app, ctx.c
    c.api = Api()
    c.state.package = "x.zip"
    c.state.preview = c.api.preview_for(False)
    c.state.preview_package = "x.zip"
    c.go(S.NEW_PREVIEW)
    app.update()
    v = app.views[S.NEW_PREVIEW]
    assert v.install_check.winfo_manager() and v.install_check.cget("text") == T.PREV_INSTALL_CHECK
    assert v.install_var.get() is False and "복원하려면" in v.summary.cget("text")
    assert any(T.PREV_INSTALL_NOT_RESTORED in str(r["values"]) for r in children(v.tree))
    v.install_var.set(True)
    v._install_changed()
    app.update()
    assert c.state.install_consent is True and c.api.import_calls[-1]["allow_install_dir"] is True
    assert "복원합니다" in v.summary.cget("text")
    # 설치 폴더 설정이 없는 묶음에서는 체크가 보이지 않는다
    c.state.preview = real_api.ImportResult(plan([item()]), "dry-run", 0)
    v._render()
    assert not v.install_check.winfo_manager()


def children(tree):
    return [tree.item(i) for i in tree.get_children()]


def test_confirm_view_mentions_install_count(ctx):
    app, c = ctx.app, ctx.c
    c.api = Api()
    c.state.package = "x.zip"
    c.set_install_consent(True)
    c.go(S.NEW_CONFIRM)
    app.update()
    assert "설치 폴더 설정 1개도 복원합니다" in app.views[S.NEW_CONFIRM].info.cget("text")


def test_discover_view_badge_and_tooltip_text(ctx):
    app, c = ctx.app, ctx.c
    a = inst_cand("a.ini")
    set_discovery(c, a, plain_cand())
    c.go(S.OLD_DISCOVER)
    app.update()
    v = app.views[S.OLD_DISCOVER]
    rows = children(v.tree)
    badge = [r for r in rows if r["values"][1] == T.BADGE_INSTALL_DIR]
    assert len(badge) == 1 and badge[0]["values"][0] == T.UNCHECKED  # 기본 체크 해제
    iid = [i for i, r in v.rows.items() if r.install][0]
    assert "관리자 권한" in v._tip_text(iid, 1)
