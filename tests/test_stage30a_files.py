"""stage30a: 내 파일 옮기기(찾기 → 방법 → 연결·복사 → 결과) 컨트롤러/상태 시험(화면 없음). 가짜 data_api / lan_api 만 쓴다.

실제 드라이브 스캔, 실제 전송, 네트워크 방송은 하지 않는다(주소는 문서용 예시 주소).
"""
import os
from types import SimpleNamespace

import pytest

from pcmigrate import find_data as fd
from pcmigrate.cancel import Cancelled
from pcmigrate.copier import FileResult, Report
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.files_flow import lan_error_text, signature_of
from pcmigrate.gui.runner import Runner
from pcmigrate.lan_link import Receiver as FoundReceiver
from pcmigrate.lan_protocol import LanError
from pcmigrate.multi_transfer import ItemResult, MultiReport
from pcmigrate.progress import ProgressInfo

from test_gui_controller import FakeApi


def folder(path, kind="project", label="", design=0, photo=0, video=0, doc=0, total=1000, cloud=0, basis="근거 예시"):
    cats = {c: {"files": n, "bytes": n * 10} for c, n in (("design", design), ("photo", photo), ("video", video), ("doc", doc)) if n}
    return fd.FolderItem(path=path, kind=kind, drive="C:", label=label, categories=cats, total_files=sum(c["files"] for c in cats.values()),
                         total_bytes=total, latest_mtime="2025-03-04T01:02:03+00:00", cloud_only_files=cloud, cloud_only_bytes=cloud * 5,
                         basis=basis)


def find_result(complete=True, errors=0):
    res = fd.FindResult()
    res.projects = [folder("C:\\Proj\\Alpha", design=5, photo=1, total=5 * 1024 ** 3), folder("C:\\Proj\\Beta", photo=9, cloud=3, total=2 * 1024 ** 3)]
    res.user_folders = [folder("C:\\Users\\someone\\Documents", kind="user_folder", label="documents", doc=7, total=1024 ** 3)]
    res.excluded = {"시스템": 4, "재생성": 2}
    res.excluded_examples = [{"path": "C:\\Windows", "reason": "시스템"}, {"path": "C:\\p\\node_modules", "reason": "재생성"}]
    res.complete, res.stop_reason = complete, ("" if complete else "time")
    res.error_count = errors
    res.files_seen, res.seconds = 12345, 7.0
    return res


class FakeData:
    def __init__(self, result=None):
        self.result = result if result is not None else find_result()
        self.calls = []
        self.raise_cancel = None

    def run_find_data(self, roots=None, *, categories=None, time_limit=None, cancel=None, progress=None, progress_info=None, **kw):
        self.calls.append(("find", roots, time_limit))
        if progress:
            progress("드라이브 읽는 중")
        if self.raise_cancel:
            self.raise_cancel(cancel)
        return self.result

    def run_transfer_selection(self, selection, dst, *, dry_run=False, include_cloud=False, strict=False, exclude_junk=False,
                               split_large=False, verify_only=False, retry_failed=False, workers=None, manifest_out=None, warn=None,
                               deps=None, progress=None, progress_info=None, cancel=None, **kw):
        self.calls.append(("transfer", [i.path for i in selection], str(dst), dry_run, exclude_junk, bool(manifest_out), verify_only))
        items = []
        for it in selection:
            rep = Report(dry_run=dry_run, results=[FileResult(it.path + "\\a", str(dst) + "\\a", "would_copy" if dry_run else "verified", 1, "x")],
                         meta={"verification_complete": not dry_run})
            items.append(ItemResult(it.path, str(dst) + "\\" + os.path.basename(it.path), "preview" if dry_run else "ok", rep))
        mr = MultiReport(dry_run=dry_run, items=items)
        mr.meta.update(space_blocked=False, required_bytes=100, dest_free_bytes=10 ** 9, estimate_bytes=100)
        return mr


class FakeRx:
    code = "123456"

    def __init__(self, dest, on_event):
        self.dest, self.on_event = dest, on_event
        self.addresses = ["192.0.2.10:47830", "198.51.100.7:47830"]
        self.address_kinds = {"192.0.2.10:47830": "private", "198.51.100.7:47830": "public"}
        self.warnings = ["이 연결은 암호화되지 않습니다."]
        self.state = "waiting"
        self.stopped = False

    def stop(self):
        self.stopped = True
        self.state = "stopped"


class FakeLan:
    def __init__(self, tmp):
        self.tmp = tmp
        self.calls = []
        self.found = [FoundReceiver("새PC", "192.0.2.20", 47830)]
        self.start_error = None
        self.send_error = None
        self.report = {"success": True, "files": 10, "bytes": 100, "verification": {"exact": 10, "mismatch": 0, "missing": 0},
                       "failed_count": 0, "cloud_only_skipped": 2, "junk_excluded": 0}
        self.rx = None

    def default_receive_dir(self):
        return str(self.tmp / "받은파일")

    def start_receiver(self, dest=None, on_event=None, **kw):
        self.calls.append(("start_receiver", str(dest)))
        if self.start_error:
            raise self.start_error
        self.rx = FakeRx(dest, on_event)
        return self.rx

    def discover_receivers(self, timeout=2.0):
        self.calls.append(("discover",))
        return list(self.found)

    def run_diagnose(self, target, code=None):
        self.calls.append(("diagnose", target, code))
        return SimpleNamespace(summary="연결할 수 있습니다")

    def run_send(self, target, code, selection=None, options=None, progress_info=None, cancel=None, **kw):
        self.calls.append(("send", target, code, [i.path for i in selection], dict(options or {})))
        if progress_info:
            progress_info(ProgressInfo("copy", 10, 100, 1, 10, speed_bps=5.0, eta_seconds=3.0))
        if self.send_error:
            raise self.send_error
        return self.report


@pytest.fixture
def env(tmp_path):
    data, lan = FakeData(), FakeLan(tmp_path)
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = data, lan
    ev = []
    c.listener = lambda kind, payload=None: ev.append((kind, payload))
    return SimpleNamespace(c=c, data=data, lan=lan, ev=ev, tmp=tmp_path)


def kinds(ev):
    return [k for k, _ in ev]


# =========================================================================== 1/4 찾기
def test_find_runs_read_only_search_and_selects_nothing(env):
    c = env.c
    c.find_data_start()
    assert env.data.calls == [("find", None, 300.0)]  # 시간 상한이 있다
    assert "find_data_done" in kinds(env.ev) and c.status.task == T.TASK_FIND_DATA
    rows = c.data_rows()
    assert [r.path for r in rows][0].endswith("Alpha")  # 용량 큰 순서
    assert len(rows) == 3 and not any(r.selected for r in rows)  # 자동 선택 금지
    assert c.data_selection() == [] and c.data_total_text() == T.FD_TOTAL_NONE
    assert c.data_summary_text() == T.FD_SUMMARY.format(n=3, files="12,345", sec="7")


def test_partial_result_after_time_limit_is_kept(env):
    env.data.result = find_result(complete=False, errors=2)
    env.c.find_data_start()
    res = env.c.files.result
    assert res.complete is False and res.stop_reason == "time" and len(env.c.data_rows()) == 3  # 지금까지 찾은 결과를 보여 준다
    assert env.c.files.find_error == ""


def test_cancel_is_not_reported_as_failure(env):
    def cancel_now(token):
        env.c.cancel()
        raise Cancelled()
    env.data.raise_cancel = cancel_now
    env.c.find_data_start()
    f = env.c.files
    assert f.find_cancelled is True and f.find_error == "" and f.result is None and not env.c.state.busy


def test_find_error_gives_plain_message_and_keeps_manual_folders(env, tmp_path):
    def boom(token):
        raise OSError("디스크 오류")
    env.data.raise_cancel = boom
    mine = tmp_path / "내폴더"
    mine.mkdir()
    env.c.add_data_folder(str(mine))
    env.c.find_data_start()
    assert env.c.files.find_error == T.FD_FAILED and not env.c.files.find_cancelled
    assert "error" in kinds(env.ev) and env.c.data_selection() == [str(mine)]  # 직접 추가한 폴더는 그대로


def test_filter_shows_only_matching_kinds_and_bulk_select_is_simple(env):
    c = env.c
    c.find_data_start()
    for cat in ("design", "video", "doc"):
        c.set_data_filter(cat, False)
    assert [os.path.basename(r.path) for r in c.data_rows()] == ["Alpha", "Beta"]  # 사진이 있는 폴더만
    assert c.select_visible_data(True) == 2 and len(c.data_selection()) == 2
    assert c.select_visible_data(True) == 0  # 이미 선택됨
    c.set_data_filter("photo", False)
    c.set_data_filter("doc", True)
    assert [os.path.basename(r.path) for r in c.data_rows()] == ["Documents"]
    assert c.select_visible_data(True) == 1 and c.select_visible_data(False) == 1
    assert len(c.data_selection()) == 2  # 안 보이던 선택은 그대로
    with pytest.raises(GuardError):
        c.set_data_filter("zip", True)


def test_selection_totals_text_and_unknown_sizes(env, tmp_path):
    c = env.c
    c.find_data_start()
    r = c.data_rows()
    c.set_data_selected(r[0].path, True)
    c.set_data_selected(r[1].path, True)
    assert c.data_total() == (2, 7 * 1024 ** 3, 0)
    assert c.data_total_text() == T.FD_TOTAL.format(n=2, size="7.0GB")
    mine = tmp_path / "x"
    mine.mkdir()
    c.add_data_folder(f'  "{mine}"  ')  # 붙여넣은 따옴표/공백은 정리
    assert c.data_total()[0] == 3 and c.data_total()[2] == 1 and T.FD_TOTAL_UNKNOWN.format(k=1) in c.data_total_text()
    with pytest.raises(GuardError, match="폴더가 아니"):
        c.add_data_folder(str(tmp_path / "없는폴더"))
    with pytest.raises(GuardError, match="이미"):
        c.add_data_folder(str(mine))
    with pytest.raises(GuardError):
        c.set_data_selected("C:\\없는\\경로", True)
    c.set_data_selected(r[0].path, False)
    assert len(c.data_selection()) == 2


def test_adding_a_found_folder_by_hand_just_selects_it(env):
    c = env.c
    c.find_data_start()
    # 찾은 폴더를 '직접 추가'로 다시 넣어도 중복 줄이 생기지 않는다(실제 폴더가 아니면 막히므로 존재하는 폴더를 결과에 넣어 둔다)
    here = os.getcwd()
    c.files.result.projects.append(folder(here, total=5))
    n = len(c.data_rows())
    assert c.add_data_folder(here) == here
    assert len(c.data_rows()) == n and c.data_selection() == [here]


def test_excluded_and_cloud_views(env):
    c = env.c
    c.find_data_start()
    assert c.data_excluded_total() == 6
    assert c.data_excluded()[0] == ("시스템", 4, ["C:\\Windows"])
    assert c.data_cloud_rows() == [("C:\\Proj\\Beta", 3, 15)]  # '내려받지 않았습니다' 로 따로 보여 줄 자료
    assert "내려받지 않았습니다" in T.FD_CLOUD_LINE and T.FD_JUNK.startswith("정크 파일 제외")


def test_junk_flag_defaults_off_and_is_passed_to_transfer(env, tmp_path):
    c = env.c
    assert c.files.junk is False
    c.find_data_start()
    real = tmp_path / "원본"
    real.mkdir()
    c.files.result.projects = [folder(str(real), design=1, total=10)]
    c.set_data_selected(str(real), True)
    dst = tmp_path / "out"
    c.set_data_junk(True)
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    c.state.transfer_dst = str(dst)
    c.start_transfer(dry_run=True)
    assert env.data.calls[-1][4] is True  # exclude_junk


# =========================================================================== 2/4 방법
def test_method_needs_selection_and_role(env):
    c = env.c
    with pytest.raises(GuardError, match="하나 이상"):
        c.files_go_method()
    c.find_data_start()
    c.set_data_selected(c.data_rows()[0].path, True)
    c.files_go_method()
    assert c.state.screen == S.METHOD
    assert c.files_method_reason() == T.WHY_MV_NEED_METHOD
    with pytest.raises(GuardError):
        c.files_continue()
    c.set_method(T.MV_DIRECT)
    assert c.files_method_reason() == T.WHY_MV_NEED_ROLE
    c.set_role(T.MV_ROLE_SEND)
    assert c.files_method_reason() == ""
    c.files_continue()
    assert c.state.screen == S.LAN_SEND
    c.set_role(T.MV_ROLE_RECV)
    c.files_continue()
    assert c.state.screen == S.LAN_RECV
    with pytest.raises(GuardError):
        c.set_method("usb-cable")  # USB-USB 케이블 방식은 없다
    with pytest.raises(GuardError):
        c.set_role("both")


def test_external_and_share_enter_existing_copy_flow_with_the_list(env, tmp_path):
    c = env.c
    c.find_data_start()
    r = c.data_rows()
    c.set_data_selected(r[0].path, True)
    c.set_data_selected(r[1].path, True)
    c.set_method(T.MV_EXTERNAL)
    c.files_continue()
    assert c.state.screen == S.TRANSFER and c.flow() == (T.MODE_EXTERNAL, 1) and c.has_selection()
    assert c.transfer_back_screen() == S.METHOD and c.files_stage(S.TRANSFER) == 3
    assert "2" in c.selection_box_text().splitlines()[0] and "Alpha" in c.selection_box_text()
    c.set_method(T.MV_SHARE)
    c.files_continue()
    assert c.flow() == (T.MODE_NETWORK, 1)


def test_selection_copy_requires_preview_and_confirmation_like_before(env, tmp_path):
    c, s = env.c, env.c.state
    c.find_data_start()
    r = c.data_rows()
    for x in r[:2]:
        os.makedirs(os.path.join(str(tmp_path), "src", os.path.basename(x.path)), exist_ok=True)
    c.set_data_selected(r[0].path, True)
    c.set_method(T.MV_EXTERNAL)
    # 존재하지 않는 원본이면 막힌다
    c.files_continue()
    s.transfer_dst = str(tmp_path / "out")
    assert c.check_transfer_paths(s.transfer_src, s.transfer_dst).ok is False
    assert c.transfer_reasons() == (T.WHY_PATH_PROBLEM, T.WHY_PATH_PROBLEM)
    # 실제로 있는 폴더를 고르면 통과
    real = tmp_path / "원본"
    real.mkdir()
    c.files.result.projects = [folder(str(real), design=2, total=2048)]
    c.files.selected = set()
    c.set_data_selected(str(real), True)
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    s.transfer_dst = str(tmp_path / "out")
    assert c.check_transfer_paths(s.transfer_src, s.transfer_dst).ok
    assert c.transfer_reasons() == ("", T.WHY_PREVIEW_FIRST)  # 미리보기 전에는 복사 시작 불가
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=False, write_confirmed=True)
    c.start_transfer(dry_run=True)
    assert env.data.calls[-1][:4] == ("transfer", [str(real)], str(tmp_path / "out"), True)
    assert c.transfer_reasons() == ("", "")
    sm = c.transfer_summary()
    assert sm["dry_run"] and sm["counts"]["would_copy"] == 1 and c.transfer_step()[1] == 4
    with pytest.raises(GuardError):  # 확인 창의 '예' 없이는 실제 복사가 안 된다
        c.start_transfer(dry_run=False)
    c.start_transfer(dry_run=False, write_confirmed=True)
    assert env.data.calls[-1][3] is False and env.data.calls[-1][5] is True  # 목록 파일(manifest_out)도 대상에 남긴다
    sm = c.transfer_summary()
    assert not sm["dry_run"] and sm["verified"] == 1 and sm["failed"] == 0 and sm["success"] is True
    assert c.transfer_counts() == (1, 0, 0) and c.files_stage(S.TRANSFER) == 4
    assert "transfer_done" in kinds(env.ev)
    os.makedirs(s.transfer_dst, exist_ok=True)  # 가짜 복사는 대상 폴더를 만들지 않는다
    assert c.can_verify_again() == (True, "")


def test_selection_copy_blocks_destination_inside_source_and_report_inside_source(env, tmp_path):
    c, s = env.c, env.c.state
    src = tmp_path / "원본"
    (src / "하위").mkdir(parents=True)
    c.files.result = find_result()
    c.files.result.projects = [folder(str(src), design=1, total=10)]
    c.set_data_selected(str(src), True)
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    s.transfer_dst = str(src / "하위")
    chk = c.check_transfer_paths(s.transfer_src, s.transfer_dst)
    assert not chk.ok and any(T.G_DEST_INSIDE == t for ok, t in chk.lines if not ok)
    s.transfer_dst = str(tmp_path / "out")
    with pytest.raises(GuardError):
        c.start_transfer(dry_run=True, report_path=str(src / "r.json"))
    assert c.source_folders() == [str(src)]


def test_legacy_single_folder_flow_still_uses_run_transfer(env, tmp_path):
    c, s = env.c, env.c.state
    c.find_data_start()
    c.set_data_selected(c.data_rows()[0].path, True)
    c.set_method(T.MV_EXTERNAL)
    c.files_continue()
    assert c.has_selection()
    c.files_legacy_transfer()  # 기존 방식 링크
    assert c.state.screen == S.TRANSFER and not c.has_selection() and s.transfer_src == "" and c.transfer_back_screen() == S.START
    src, dst = tmp_path / "a", tmp_path / "b"
    src.mkdir()
    s.transfer_src, s.transfer_dst = str(src), str(dst)
    c.start_transfer(dry_run=True)
    assert c.api.calls[-1][0] == "transfer" and not any(x[0] == "transfer" for x in env.data.calls)


def test_leaving_to_other_flows_ends_selection_mode(env):
    c = env.c
    c.find_data_start()
    c.set_data_selected(c.data_rows()[0].path, True)
    c.set_method(T.MV_SHARE)
    c.files_continue()
    assert c.has_selection()
    c.go(S.START)
    assert not c.files.use_selection and c.state.transfer_src == ""
    c.go(S.TRANSFER)  # '추천대로 한 번에' 등이 예전 화면으로 들어가도 선택 목록을 쓰지 않는다
    assert not c.has_selection()


def test_stage2_of_selection_flow_goes_back_to_folder_input(env):
    c, s = env.c, env.c.state
    c.find_data_start()
    c.set_data_selected(c.data_rows()[0].path, True)
    c.set_method(T.MV_EXTERNAL)
    c.files_continue()
    sig = s.transfer_src
    assert sig.startswith("선택목록:") and sig == c.files.signature
    c.set_transfer_flow(T.MODE_EXTERNAL, 2)  # 새 PC 에서 외장 → 새 PC: 원본은 외장 폴더
    c.sync_selection_src()
    assert not c.has_selection() and s.transfer_src == ""
    c.set_transfer_flow(T.MODE_EXTERNAL, 1)
    c.sync_selection_src()
    assert c.has_selection() and s.transfer_src == sig


def test_signature_changes_with_selection_and_junk():
    a = signature_of(["C:\\a", "C:\\b"], False)
    assert a == signature_of(["c:\\B", "C:\\A"], False)  # 순서/대소문자 무관
    assert a != signature_of(["C:\\a"], False) and a != signature_of(["C:\\a", "C:\\b"], True)


def test_files_receive_only_link_goes_straight_to_receive(env):
    env.c.files_receive_only()
    assert env.c.state.screen == S.LAN_RECV and env.c.files_stage(S.LAN_RECV) == 3


# =========================================================================== 단계 표시줄
def test_overall_steps_for_the_new_flow_and_old_flows_unchanged(env):
    c = env.c
    names = T.OVERALL_STEP_NAMES["files"]
    assert names == ("찾기", "방법", "연결·복사", "결과")
    assert S.overall_step(S.FIND, "", 1, c.files_stage(S.FIND))[:3] == (1, 4, T.OVERALL_FLOWS["files"])
    assert S.overall_step(S.METHOD, "", 1, 2)[0] == 2
    assert S.overall_step(S.LAN_SEND, "", 1, 3)[0] == 3 and S.overall_step(S.LAN_SEND, "", 1, 4)[0] == 4
    assert S.overall_names(S.FIND, "", 1) == names
    # 기존 흐름
    assert S.overall_step(S.OLD_DISCOVER)[3] == T.TITLES[S.OLD_DISCOVER] and S.overall_step(S.OLD_DISCOVER)[1] == 4
    assert S.overall_step(S.TRANSFER, T.MODE_EXTERNAL, 2)[:2] == (2, 2)
    assert S.overall_step(S.START) is None and c.files_stage(S.START) == 0 and c.files_stage(S.OLD_SCAN) == 0
    assert c.files_stage(S.TRANSFER) == 0  # 선택 목록이 없는 예전 복사 화면은 예전 표시줄


# =========================================================================== 3/4 받는 쪽
def test_receive_start_shows_code_addresses_warnings(env):
    c, f = env.c, env.c.files
    assert c.lan_dest().endswith("받은파일") and not c.lan_receive_active()
    assert c.lan_receive_start() is True
    assert env.lan.calls[0] == ("start_receiver", c.lan_dest())
    assert f.rx_code == "123456" and f.rx_state == "waiting" and len(f.rx_addresses) == 2 and c.lan_receive_active()
    assert c.rx_public_warning() is True  # 공인 주소가 있으면 경고
    assert "암호화되지 않습니다" in T.RX_WARN_PLAIN and "사설 네트워크" in T.RX_WARN_PLAIN
    with pytest.raises(GuardError):
        c.lan_receive_start()  # 이미 시작함
    with pytest.raises(GuardError):
        c.lan_set_dest(str(env.tmp / "다른곳"))  # 받는 중에는 저장 위치를 못 바꾼다


def test_receive_dest_can_be_changed_before_start(env):
    c = env.c
    c.lan_set_dest(f'"{env.tmp / "내 저장소"}"')
    c.lan_receive_start()
    assert env.lan.calls[0] == ("start_receiver", str(env.tmp / "내 저장소"))
    with pytest.raises(GuardError):
        env.c.lan_set_dest("  ")


def test_receive_events_flow_into_state_via_poll_and_done_result(env):
    c, f = env.c, env.c.files
    c.lan_receive_start()
    ev = env.lan.rx.on_event
    ev({"type": "peer_connected", "message": "x"})
    ev({"type": "plan", "files": 10, "bytes": 2048, "message": "x"})
    ev({"type": "progress", "info": ProgressInfo("copy", 1024, 2048, 5, 10, speed_bps=100.0, eta_seconds=10.0), "message": ""})
    assert f.rx_state == "waiting"  # 소식은 poll 로 화면 스레드에서만 처리한다
    assert c.lan_poll() == 3 and f.rx_state == "receiving" and f.rx_info.done_bytes == 1024 and f.rx_plan == {"files": 10, "bytes": 2048}
    ev({"type": "auth_failed", "message": "x"})
    ev({"type": "disconnected", "message": "x"})
    c.lan_poll()
    assert f.rx_state == "waiting" and T.RX_AUTH_FAILED in f.rx_log and T.RX_DISCONNECTED in f.rx_log
    ev({"type": "done", "files": 10, "bytes": 2048, "ok": 10, "mismatch": 0, "failed": 0, "sender_failed": 0, "dest": "x"})
    c.lan_poll()
    assert f.rx_state == "done" and not c.lan_receive_active() and c.files_stage(S.LAN_RECV) == 4
    text, ok = c.rx_result_text()
    assert ok and "10" in text and "lan_rx" in kinds(env.ev)


def test_receive_result_with_problems_is_not_called_exact(env):
    c = env.c
    c.lan_receive_start()
    env.lan.rx.on_event({"type": "done", "files": 10, "bytes": 1, "ok": 8, "mismatch": 2, "failed": 0, "sender_failed": 0})
    c.lan_poll()
    text, ok = c.rx_result_text()
    assert ok is False and "내용이 다름 2" in text


def test_receive_stop_and_start_errors(env):
    c, f = env.c, env.c.files
    c.lan_receive_start()
    c.lan_receive_stop()
    assert env.lan.rx.stopped and f.rx_state == "stopped" and not c.lan_receive_active()
    env.lan.start_error = LanError("raw english message", "port_in_use")
    assert c.lan_receive_start() is False
    assert f.rx_state == "error" and f.rx_error == T.LAN_ERRORS["port_in_use"] and "raw english" not in f.rx_error


def test_receive_error_event_uses_plain_text(env):
    c, f = env.c, env.c.files
    c.lan_receive_start()
    env.lan.rx.on_event({"type": "error", "code": "space", "message": "disk full"})
    c.lan_poll()
    assert f.rx_error == T.LAN_ERRORS["space"] and f.rx_state == "waiting"


# =========================================================================== 3/4 보내는 쪽
def ready_to_send(env):
    c = env.c
    c.find_data_start()
    c.set_data_selected(c.data_rows()[0].path, True)
    c.set_data_junk(True)
    return c


def test_find_receiver_fills_target_when_one_found(env):
    c, f = env.c, env.c.files
    c.lan_find_start()
    assert env.lan.calls == [("discover",)] and f.target == "192.0.2.20:47830" and f.found_message == T.TX_FOUND_N.format(n=1)
    assert "lan_found" in kinds(env.ev) and c.status.task == T.TASK_LAN_FIND
    env.lan.found = [FoundReceiver("A", "192.0.2.21", 47830), FoundReceiver("B", "192.0.2.22", 47831)]
    f.target = ""
    c.lan_find_start()
    assert f.target == "" and f.found_message == T.TX_FOUND_N.format(n=2)
    assert c.lan_pick_found(1) == "192.0.2.22:47831" and f.target == "192.0.2.22:47831"
    with pytest.raises(GuardError):
        c.lan_pick_found(5)


def test_find_receiver_none_found_gives_help_text(env):
    env.lan.found = []
    env.c.lan_find_start()
    assert env.c.files.found_message == T.TX_FOUND_NONE and env.c.files.found == []


def test_diagnose_gives_one_line_and_sends_code_only_when_valid(env):
    c = env.c
    c.lan_diagnose_start("192.0.2.20:47830", "12 34 56")
    assert env.lan.calls[-1] == ("diagnose", "192.0.2.20:47830", "123456")
    assert c.files.diag == T.TX_DIAG_RESULT.format(summary="연결할 수 있습니다") and "lan_diag" in kinds(env.ev)
    c.lan_diagnose_start("192.0.2.20", "12")
    assert env.lan.calls[-1][2] is None
    with pytest.raises(GuardError):
        c.lan_diagnose_start("  ", "")


def test_send_needs_input_and_confirmation_then_reports(env):
    c, f = ready_to_send(env), env.c.files
    assert c.lan_send_problem("", "123456") == T.G_TX_NEED_ADDR and c.lan_send_problem("192.0.2.20", " ") == T.G_TX_NEED_CODE
    with pytest.raises(GuardError):
        c.lan_send_start("192.0.2.20:47830", "123456")  # 확인 없이는 보내지 않는다
    assert not any(x[0] == "send" for x in env.lan.calls)
    c.lan_send_start("192.0.2.20:47830", "123456", confirmed=True)
    call = env.lan.calls[-1]
    assert call[:3] == ("send", "192.0.2.20:47830", "123456") and call[3] == [c.data_selection()[0]] and call[4] == {"exclude_junk": True}
    assert f.tx_report is not None and f.tx_error == "" and c.files_stage(S.LAN_SEND) == 4
    assert c.status.task == T.TASK_LAN_SEND and c.status.info_last is not None and c.status.info_last.speed_bps == 5.0  # 현황 카드에 진행이 간다
    text, ok, notes = c.tx_result_lines()
    assert ok and "정확함 10" in text and T.TX_VERIFY_NEW_PC in notes and any("클라우드" in n for n in notes)


def test_send_mismatch_is_not_called_success(env):
    c = ready_to_send(env)
    env.lan.report = dict(env.lan.report, success=False, verification={"exact": 8, "mismatch": 1, "missing": 1}, failed_count=2)
    c.lan_send_start("192.0.2.20", "123456", confirmed=True)
    text, ok, notes = c.tx_result_lines()
    assert not ok and "내용이 다름 1" in text and "누락 1" in text


@pytest.mark.parametrize("code", ["bad_code", "auth", "locked", "expired", "closed", "busy", "version", "unreachable", "disconnected", "no_receiver"])
def test_send_lan_errors_become_plain_text_not_dialogs(env, code):
    c, f = ready_to_send(env), env.c.files
    env.lan.send_error = LanError("raw english message", code)
    c.lan_send_start("192.0.2.20", "123456", confirmed=True)
    assert f.tx_error == T.LAN_ERRORS[code] and "raw english" not in f.tx_error and f.tx_report is None
    assert "error" not in kinds(env.ev) and not c.state.busy  # 오류 대화상자가 아니라 화면 안내


def test_send_cancel_marks_cancelled_and_keeps_state(env):
    c, f = ready_to_send(env), env.c.files

    def cancelling(*a, progress_info=None, cancel=None, **kw):
        env.c.cancel()
        raise Cancelled()
    env.lan.run_send = cancelling
    c.lan_send_start("192.0.2.20", "123456", confirmed=True)
    assert f.tx_cancelled is True and f.tx_report is None and "cancelled" in kinds(env.ev) and not c.state.busy


def test_send_unexpected_error_is_not_marked_cancelled(env):
    c, f = ready_to_send(env), env.c.files
    env.lan.run_send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    c.lan_send_start("192.0.2.20", "123456", confirmed=True)
    assert f.tx_cancelled is False and "error" in kinds(env.ev)


def test_send_without_selection_is_blocked(env):
    with pytest.raises(GuardError):
        env.c.lan_send_start("192.0.2.20", "123456", confirmed=True)


# =========================================================================== 문구
def test_every_lan_error_code_has_plain_text_and_unknown_falls_back():
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "pcmigrate"
    codes = set()
    for name in ("lan_protocol.py", "lan_link.py", "lan_transfer.py", "lan_api.py"):
        text = (src / name).read_text(encoding="utf-8")
        codes |= set(re.findall(r'LanError\([^)]*?,\s*"([a-z_]+)"\)', text))
        codes |= set(re.findall(r'AuthError\([^)]*?,\s*"([a-z_]+)"', text))
    codes -= {"error", "bad_args"}
    missing = sorted(c for c in codes if c not in T.LAN_ERRORS)
    assert missing == []
    assert lan_error_text("weird_code") == T.LAN_ERROR_DEFAULT


def test_start_card_two_is_my_files_and_old_name_only_in_help():  # stage34: 카드 순서 ① 구 PC → ② 내 파일 옮기기 → ③ 새 PC
    title, desc, btn, target = T.START_CHOICES[1]
    assert title.startswith("② 내 파일 옮기기") and "자동으로 찾아서" in title and target == S.FIND
    assert "대용량 복사" not in title + desc + btn
    assert "대용량 복사" in T.HELP[S.FIND] and "대용량 복사" in T.HELP[S.TRANSFER]
    assert T.TITLES[S.FIND].startswith("② 내 파일 옮기기 · 1/4")
    assert T.TITLES[S.METHOD].startswith("② 내 파일 옮기기 · 2/4")


def test_receiver_plain_warning_text_is_exact():
    assert T.RX_WARN_PLAIN == "내용은 암호화되지 않습니다. 사무실·집 같은 사설 네트워크에서만 사용해 주세요."  # 용어집: ~해 주세요
