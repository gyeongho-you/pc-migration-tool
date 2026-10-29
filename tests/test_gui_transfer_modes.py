"""12단계 GUI: 방식 선택(외장/랜선/같은 PC)별 흐름, 사전 점검 차단, 목록 파일 검사, 경고 문구, 드라이브 분리."""
import threading
import time
from types import SimpleNamespace

import pytest

from pcmigrate import fs
from pcmigrate.copier import FileResult, Report
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError, friendly_error
from pcmigrate.gui.runner import Runner
from pcmigrate.manifest import DEFAULT_NAME
from pcmigrate.progress import ProgressInfo
from pcmigrate.transfer import DriveDisconnected

from test_gui_controller import FakeApi


class FlowApi(FakeApi):
    """run_transfer / run_verify 호출 인자를 기록하고 원하는 리포트를 돌려주는 가짜 api."""

    def __init__(self):
        super().__init__()
        self.t_calls, self.v_calls = [], []
        self.t_meta = {}
        self.v_meta = {"source_root": "D:\\Projects", "target": ""}
        self.dest = fs.DestInfo("x", "removable", "NTFS", 10 ** 12, 1, "E:")
        self.report_in = False

    def run_transfer(self, src, dst, *, dry_run=False, include_cloud=False, strict=False, progress=None, cancel=None,
                     deps=None, warn=None, progress_info=None, **kw):
        self.calls.append(("transfer", dry_run, include_cloud, strict))
        self.t_calls.append(dict(kw, dry_run=dry_run, src=src, dst=dst))
        if self.transfer_error:
            raise self.transfer_error
        if progress_info:
            progress_info(ProgressInfo("copy", 50, 100, 1, 2, 10.0, 5.0))
        st = "would_copy" if dry_run else "verified"
        return Report(dry_run=dry_run, results=[FileResult("s\\a", "d\\a", st, 1, "x")], meta=dict(self.t_meta))

    def run_verify(self, manifest, target, *, workers=None, chunk=None, cancel=None, progress=None, progress_info=None,
                   find_extras=True, deps=None):
        self.v_calls.append((manifest, target))
        meta = dict(self.v_meta, target=target, mode="verify", manifest_complete=True)
        return Report(dry_run=False, results=[FileResult("[목록] a", "d", "verified"), FileResult("[목록에 없음] z", "d", "extra")],
                      meta=meta)

    def destination_info(self, dst, deps=None):
        return self.dest

    def report_inside(self, path, folder):
        return self.report_in


@pytest.fixture
def flow(tmp_path):
    api = FlowApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    events = []
    c.listener = lambda kind, payload=None: events.append((kind, payload))
    src = tmp_path / "src"
    src.mkdir()
    return SimpleNamespace(c=c, api=api, events=events, src=src, tmp=tmp_path)


def run_copy(f, dst, **kw):
    f.c.state.transfer_src, f.c.state.transfer_dst = str(f.src), str(dst)
    f.c.start_transfer(dry_run=True)
    f.c.start_transfer(dry_run=False, write_confirmed=True, **kw)


# ------------------------------------------------------------------ 방식/단계 선택

def test_default_flow_is_external_stage1_and_modes_are_three(flow):
    assert flow.c.flow() == (T.MODE_EXTERNAL, 1)
    assert [m[0] for m in T.TR_MODES] == [T.MODE_EXTERNAL, T.MODE_NETWORK, T.MODE_LOCAL]
    assert T.TR_MODE_HEADING == "어떻게 옮기시나요?"


def test_set_flow_resets_preview_and_rejects_unknown(flow):
    flow.c.state.transfer_preview_key = ("a", "b")
    flow.c.set_transfer_flow(T.MODE_NETWORK, 2)
    assert flow.c.flow() == (T.MODE_NETWORK, 2) and flow.c.state.transfer_preview_key is None and flow.c.is_verify_flow()
    flow.c.set_transfer_flow(T.MODE_LOCAL, 2)
    assert flow.c.flow() == (T.MODE_LOCAL, 1)  # 같은 PC 는 단계가 없다
    with pytest.raises(GuardError):
        flow.c.set_transfer_flow("usb-cable")  # USB-USB 케이블 방식은 지원하지 않는다
    flow.c.state.busy = True
    with pytest.raises(GuardError):
        flow.c.set_transfer_flow(T.MODE_EXTERNAL)


def test_every_flow_has_labels_method_steps_and_title(flow):
    for mode, stage in ((T.MODE_EXTERNAL, 1), (T.MODE_EXTERNAL, 2), (T.MODE_NETWORK, 1), (T.MODE_NETWORK, 2), (T.MODE_LOCAL, 1)):
        flow.c.set_transfer_flow(mode, stage)
        assert flow.c.flow_method() and flow.c.flow_steps() and flow.c.flow_title()
        if (mode, stage) != (T.MODE_NETWORK, 2):
            assert all(flow.c.flow_labels())


# ------------------------------------------------------------------ 외장 1단계/2단계

def test_external_stage1_writes_manifest_into_destination(flow):
    ext = flow.tmp / "ext"
    run_copy(flow, ext)
    kw = flow.api.t_calls[-1]
    assert kw["manifest_out"] == str(ext / DEFAULT_NAME)  # 목록 파일은 외장 드라이브(대상) 안에 저장
    assert "manifest_out" not in flow.api.t_calls[0]  # 미리보기는 아무것도 쓰지 않음
    assert "use_manifest" not in kw


def test_external_stage2_uses_manifest_found_on_external(flow):
    (flow.src / DEFAULT_NAME).write_text("{}", encoding="utf-8")
    flow.c.set_transfer_flow(T.MODE_EXTERNAL, 2)
    run_copy(flow, flow.tmp / "newpc")
    kw = flow.api.t_calls[-1]
    assert kw["use_manifest"] == str(flow.src / DEFAULT_NAME) and "manifest_out" not in kw  # 외장 원본을 다시 읽지 않음


def test_external_stage2_without_manifest_falls_back_to_normal_check(flow):
    flow.c.set_transfer_flow(T.MODE_EXTERNAL, 2)
    assert flow.c.stage2_manifest() == ""
    run_copy(flow, flow.tmp / "newpc")
    assert "use_manifest" not in flow.api.t_calls[-1]


def test_location_warning_when_restored_elsewhere(flow):
    flow.c.set_transfer_flow(T.MODE_EXTERNAL, 2)
    flow.api.t_meta = {"original_root": "D:\\Projects"}
    run_copy(flow, flow.tmp / "newpc")
    warn = flow.c.location_warning()
    assert warn.startswith(T.ICON_WARN) and "D:\\Projects" in warn and "xref" in warn
    assert warn in flow.c.flow_notes()
    flow.c.state.transfer_dst = "d:/projects"  # 같은 위치(대소문자/슬래시 차이는 무시)
    assert flow.c.location_warning() == ""


def test_notes_always_include_xref_and_do_not_delete_original_reminder(flow):
    run_copy(flow, flow.tmp / "ext")
    notes = flow.c.flow_notes()
    assert T.XREF_NOTE in notes and T.TR_DONE_REMINDER in notes and T.TR_NEXT_STEP_EXT1 in notes
    assert flow.c.flow_notes() and "지우거나 포맷하지" in T.TR_DONE_REMINDER


def test_layout_lines_show_absolute_root_and_top_folders(flow):
    flow.api.t_meta = {"source_layout": {"root": "D:\\Projects", "drive": "D:", "top_level_count": 2,
                                         "top_level": [{"name": "도면", "files": 1200, "bytes": 3 * 1024 ** 3},
                                                       {"name": "(root)", "files": 3, "bytes": 100}]}}
    run_copy(flow, flow.tmp / "ext")
    lines = flow.c.layout_lines()
    assert lines[0] == T.TR_LAYOUT_ROOT.format(root="D:\\Projects") and "도면" in lines[1] and "1,200" in lines[1]


# ------------------------------------------------------------------ 랜선 1단계/2단계

def test_network_stage1_and_deferred_note(flow):
    flow.c.set_transfer_flow(T.MODE_NETWORK, 1)
    flow.api.t_meta = {"dest_verification": "deferred"}
    run_copy(flow, "\\\\192.168.50.2\\이전")
    assert flow.api.t_calls[-1]["manifest_out"].endswith(DEFAULT_NAME)
    notes = flow.c.flow_notes()
    assert T.TR_DEFERRED_NOTE in notes and T.TR_NEXT_STEP_NET1 in notes


def test_network_stage2_verify_flow(flow):
    m = flow.tmp / "m.jsonl"
    m.write_text("{}", encoding="utf-8")
    target = flow.tmp / "newpc"
    target.mkdir()
    flow.c.set_transfer_flow(T.MODE_NETWORK, 2)
    assert flow.c.transfer_reasons() == (T.WHY_NEED_MANIFEST, T.WHY_NEED_MANIFEST)
    flow.c.state.verify_manifest, flow.c.state.verify_target = str(m), str(target)
    assert flow.c.transfer_reasons() == ("", "")
    flow.c.start_verify()
    assert flow.api.v_calls == [(str(m), str(target))]
    sm = flow.c.transfer_summary()
    assert sm["verified"] == 1 and sm["added"] == 1 and sm["failed"] == 0 and sm["failures"] == []  # 추가 파일은 실패가 아님
    assert flow.c.location_warning().startswith(T.ICON_WARN)  # 원래 D:\Projects, 지금 새 PC 폴더
    assert any(k == "transfer_done" for k, _ in flow.events)


def test_verify_requires_inputs_and_blocks_report_inside_target(flow):
    flow.c.set_transfer_flow(T.MODE_NETWORK, 2)
    with pytest.raises(GuardError, match=T.G_NEED_MANIFEST):
        flow.c.start_verify()
    m = flow.tmp / "m.jsonl"
    m.write_text("{}", encoding="utf-8")
    flow.c.state.verify_manifest, flow.c.state.verify_target = str(m), str(flow.tmp / "none")
    with pytest.raises(GuardError, match="검사할 폴더를 찾을 수 없습니다"):
        flow.c.start_verify()
    flow.c.state.verify_target = str(flow.src)
    flow.api.report_in = True
    with pytest.raises(GuardError, match=T.G_REPORT_INSIDE):
        flow.c.start_verify(report_path=str(flow.src / "r.json"))
    assert flow.api.v_calls == []  # 막힌 경우 아무것도 실행하지 않음


def test_start_transfer_is_refused_in_verify_flow(flow):
    flow.c.set_transfer_flow(T.MODE_NETWORK, 2)
    with pytest.raises(GuardError):
        flow.c.start_transfer(dry_run=True)


# ------------------------------------------------------------------ 사전 점검

def test_dest_info_lines_and_warnings(flow):
    flow.api.dest = fs.DestInfo("E:\\x", "removable", "FAT32", 5 * 1024 ** 3, 1, "E:")
    chk = flow.c.check_transfer_paths(str(flow.src), "E:\\x")
    assert "FAT32" in chk.info[0] and "이동식" in chk.info[0] and chk.warnings == [T.TR_FAT32_WARN]
    flow.c.forget_dest_cache()
    flow.api.dest = fs.DestInfo("E:\\x", "fixed", "EXFAT", 5, 1, "E:")
    assert flow.c.check_transfer_paths(str(flow.src), "E:\\y").warnings == [T.TR_EXFAT_WARN]
    flow.api.dest = fs.DestInfo("E:\\x", "cdrom", "CDFS", 0, 1, "E:")
    flow.c.forget_dest_cache()
    assert not flow.c.check_transfer_paths(str(flow.src), "E:\\z").ok


def test_unc_destination_is_not_queried(flow):
    flow.api.destination_info = lambda *a, **k: (_ for _ in ()).throw(AssertionError("네트워크 경로는 조회하지 않음"))
    chk = flow.c.check_transfer_paths(str(flow.src), "\\\\192.168.50.2\\share")
    assert T.TR_NETWORK_NOTE in chk.warnings and "네트워크" in chk.info[0]


def test_space_shortage_found_in_preview_blocks_start(flow):
    flow.api.t_meta = {"space_blocked": True, "required_bytes": 2000, "dest_free_bytes": 100}
    flow.c.state.transfer_src, flow.c.state.transfer_dst = str(flow.src), str(flow.tmp / "d")
    flow.c.start_transfer(dry_run=True)
    assert flow.c.transfer_reasons() == ("", T.WHY_SPACE)
    with pytest.raises(GuardError, match=T.WHY_SPACE):
        flow.c.start_transfer(dry_run=False, write_confirmed=True)
    assert len([c for c in flow.api.t_calls if not c["dry_run"]]) == 0


def test_fat32_big_file_found_in_preview_blocks_start(flow):
    flow.api.t_meta = {"fat32_blocked": True, "fat32_big_files": 2}
    flow.c.state.transfer_src, flow.c.state.transfer_dst = str(flow.src), str(flow.tmp / "d")
    flow.c.start_transfer(dry_run=True)
    assert flow.c.transfer_reasons()[1] == T.WHY_FAT32
    with pytest.raises(GuardError):
        flow.c.start_transfer(dry_run=False, write_confirmed=True)
    flow.api.t_meta = {}  # NTFS 드라이브로 바꿔 다시 미리보기하면 풀림
    flow.c.start_transfer(dry_run=True)
    assert flow.c.transfer_reasons()[1] == ""


# ------------------------------------------------------------------ 검사만 다시 / 실패한 것만 다시

def test_verify_again_needs_existing_destination_and_no_confirm(flow):
    flow.c.state.transfer_src, flow.c.state.transfer_dst = str(flow.src), str(flow.tmp / "ext")
    assert flow.c.can_verify_again() == (False, T.G_NEED_COPY_FIRST)
    (flow.tmp / "ext").mkdir()
    assert flow.c.can_verify_again() == (True, "")
    flow.c.start_transfer(dry_run=False, verify_only=True)  # 읽기만 하므로 미리보기/쓰기 확인 불필요
    assert flow.api.t_calls[-1]["verify_only"] is True and "manifest_out" not in flow.api.t_calls[-1]


def test_retry_failed_requires_confirmation_and_preview(flow):
    flow.c.state.transfer_src, flow.c.state.transfer_dst = str(flow.src), str(flow.tmp / "ext")
    with pytest.raises(GuardError):
        flow.c.start_transfer(dry_run=False, retry_failed=True)
    run_copy(flow, flow.tmp / "ext")
    flow.api.run_transfer = lambda *a, **k: Report(dry_run=False, results=[FileResult("s", "d", "failed", error="x")])
    flow.c.start_transfer(dry_run=False, write_confirmed=True)
    assert flow.c.has_retryable()


# ------------------------------------------------------------------ 진행 정보 / 드라이브 분리

def test_progress_info_reaches_listeners_with_bytes_speed_eta(flow):
    flow.c.state.transfer_src, flow.c.state.transfer_dst = str(flow.src), str(flow.tmp / "d")
    flow.c.start_transfer(dry_run=True)
    infos = [p for k, p in flow.events if k == "progress_info"]
    texts = [p for k, p in flow.events if k == "progress" and "50.0%" in str(p)]
    assert infos and infos[0].fraction == 0.5 and flow.c.state.transfer_progress is infos[0]
    assert texts and "남은 시간" in texts[0]


def test_slow_hint_is_appended_to_status_text(flow):
    flow.c.state.transfer_src, flow.c.state.transfer_dst = str(flow.src), str(flow.tmp / "d")

    def api_run(src, dst, **kw):
        kw["progress_info"](ProgressInfo("copy", 1, 100, 0, 1, 1.0, None, "USB 2.0 확인"))
        return Report(dry_run=True)
    flow.api.run_transfer = api_run
    flow.c.start_transfer(dry_run=True)
    assert any("USB 2.0 확인" in str(p) for k, p in flow.events if k == "progress")


def test_drive_disconnect_message_and_resume(flow):
    flow.c.state.transfer_src, flow.c.state.transfer_dst = str(flow.src), str(flow.tmp / "ext")
    flow.c.start_transfer(dry_run=True)
    flow.api.transfer_error = DriveDisconnected("x")
    flow.c.start_transfer(dry_run=False, write_confirmed=True)
    err = [p for k, p in flow.events if k == "error"][-1]
    assert err.what == T.TR_DISCONNECTED == "드라이브가 분리되었습니다. 다시 연결하면 이어받습니다."
    assert "드라이브 문자" in err.how and not flow.c.state.busy  # 분리 후 다시 시작할 수 있는 상태
    flow.api.transfer_error = None
    flow.c.start_transfer(dry_run=False, write_confirmed=True)  # 재연결 후 같은 설정으로 이어서
    assert flow.c.transfer_summary()["verified"] == 1


def test_friendly_error_for_disconnect_and_manifest_error():
    from pcmigrate.manifest import ManifestError
    assert friendly_error(DriveDisconnected("x")).title == T.TR_DISCONNECTED_TITLE
    assert friendly_error(ManifestError("목록 파일이 비어 있습니다")).what.startswith("목록 파일")


# ------------------------------------------------------------------ 문구 일관성

def test_two_speed_hints_are_different_and_texts_have_no_prices():
    from pcmigrate.progress import NETWORK_SLOW_HINT, SLOW_HINT
    assert "USB 2.0" in SLOW_HINT and "100Mbps" in NETWORK_SLOW_HINT and SLOW_HINT != NETWORK_SLOW_HINT
    blob = T.HELP_COMPARE + T.HELP_NETWORK
    assert "원" not in [w for w in blob.split() if w.endswith("원") and w[:-1].isdigit()] and "$" not in blob
    assert "환경에 따라 다를 수 있습니다" in T.HELP_NETWORK
    for needle in ("192.168.50.1", "192.168.50.2", "255.255.255.0", "개인", "ping", "1000", "net use"):
        assert needle in T.HELP_NETWORK, needle
    assert "랜선이 충분한 경우" in T.HELP_COMPARE and "외장 드라이브가 나은 경우" in T.HELP_COMPARE
    assert "이론" in T.HELP_COMPARE


# ------------------------------------------------------------------ 화면(Tk)

tk = pytest.importorskip("tkinter")


def test_transfer_view_switches_flows(monkeypatch, tmp_path):
    from tkinter import messagebox
    from pcmigrate.gui.app import App
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: None)
    api = FlowApi()
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path)
    app = None
    for _ in range(4):
        try:
            app = App(c)
            break
        except tk.TclError:
            time.sleep(0.5)
    if app is None:
        pytest.skip("디스플레이/Tcl 을 사용할 수 없음")
    try:
        app.withdraw()
        v = app.views[S.TRANSFER]
        c.go(S.TRANSFER)
        app.update()
        assert v.mode_var.get() == T.MODE_EXTERNAL and v.src_label.cget("text") == T.TR_FLOW_LABELS[(T.MODE_EXTERNAL, 1)][0]
        assert T.TR_PRECHECK_LINES[0] in v.precheck_label.cget("text") and v.method_label.cget("text") == T.TR_VERIFY_METHOD[(T.MODE_EXTERNAL, 1)]
        v.mode_var.set(T.MODE_NETWORK)
        v._on_mode()
        v.stage_var.set(2)
        v._on_stage()
        app.update()
        assert c.is_verify_flow() and v.verify_form.winfo_manager() == "pack" and v.copy_form.winfo_manager() == ""
        assert "disabled" in v.verify_btn.state() and v.verify_reason.cget("text") == T.WHY_NEED_MANIFEST
        m = tmp_path / "m.jsonl"
        m.write_text("{}", encoding="utf-8")
        t = tmp_path / "t"
        t.mkdir()
        v.manifest_var.set(str(m))
        v.target_var.set(str(t))
        app.update()
        assert "disabled" not in v.verify_btn.state()
        v._verify()
        app.update()
        assert api.v_calls and T.ICON_OK in v.result.ok_label.cget("text")
        assert v.loc_box.winfo_manager() == "pack" and T.LOC_WARNING.split("{")[0] in v.loc_box.cget("text")  # 노란 경고
        v.mode_var.set(T.MODE_LOCAL)
        v._on_mode()
        app.update()
        assert v.stage_frame.winfo_manager() == "" and v.copy_form.winfo_manager() == "pack"
        # 진행 막대: 바이트를 알면 비율 막대
        app.on_event("busy", T.TASK_TRANSFER)
        app.on_event("progress_info", ProgressInfo("copy", 25, 100, 1, 4))
        assert str(app.progress.cget("mode")) == "determinate" and float(app.progress.cget("value")) == 25.0
        app.on_event("busy", None)
        assert str(app.progress.cget("mode")) == "indeterminate"
    finally:
        app.destroy()


# ------------------------------------------------------------------ 전체 이전 순서(? 도움말 / README 동기화)

def test_roadmap_covers_required_steps_and_marks_who():
    assert len(T.ROADMAP_OLD) == 6 and len(T.ROADMAP_NEW) == 6
    for key, title, who, detail in (*T.ROADMAP_OLD, *T.ROADMAP_NEW):
        assert who in (T.WHO_TOOL, T.WHO_HUMAN, T.WHO_BOTH) and title and detail
    blob = T.roadmap_text()
    for needle in ("삭제하거나 포맷하지", "동글", "xref", "미리보기", "윈도우 업데이트", "드라이브 문자", "외장/랜선", "라이선스"):
        assert needle in blob, needle
    assert T.roadmap_text({"old1"}).count(T.CHECKED) == 1 and T.UNCHECKED in T.roadmap_text()


def test_readme_roadmap_matches_texts_source():
    from pathlib import Path
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    begin, end = "<!-- ROADMAP:BEGIN -->\n", "\n<!-- ROADMAP:END -->"
    assert begin in readme and end in readme
    assert readme.split(begin, 1)[1].split(end, 1)[0] == T.roadmap_markdown()  # 두 곳이 같은 원본


def test_controller_roadmap_marks_persist_and_validate(flow):
    assert flow.c.toggle_roadmap("old1") is True and flow.c.toggle_roadmap("old1") is False and flow.c.toggle_roadmap("new3") is True
    assert flow.c.roadmap_marks == {"new3"}
    with pytest.raises(GuardError):
        flow.c.toggle_roadmap("nope")


def test_roadmap_reachable_from_every_screen_and_help(tmp_path):
    from pcmigrate.gui.app import App, RoadmapDialog
    c = Controller(api_mod=FlowApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    app = None
    for _ in range(4):
        try:
            app = App(c)
            break
        except tk.TclError:
            time.sleep(0.5)
    if app is None:
        pytest.skip("디스플레이/Tcl 을 사용할 수 없음")
    try:
        app.withdraw()
        for name, view in app.views.items():
            assert view.roadmap_btn.cget("text") == T.BTN_ROADMAP, name  # 모든 화면 오른쪽 위
        c.go(S.START)
        app.update()
        dlg = app.show_help(S.START)
        assert dlg.roadmap_btn.cget("text") == T.BTN_ROADMAP  # ? 도움말에서도
        rd = app.show_roadmap()
        app.update()
        assert isinstance(rd, RoadmapDialog) and len(rd.vars) == 12
        assert {w.cget("text") for w in rd.who_labels.values()} == {T.WHO_TOOL, T.WHO_HUMAN, T.WHO_BOTH}
        c.toggle_roadmap("old2")
        rd2 = app.show_roadmap()  # 다시 열어도 체크 유지
        assert rd2.vars["old2"].get() is True and rd2.vars["old1"].get() is False
    finally:
        app.destroy()
