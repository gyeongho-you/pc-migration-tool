"""stage63: 직접 고른 파일·폴더(끌어다 놓기) 컨트롤러 시험(화면 없음). 가짜 data_api 만 쓴다(실제 전송 없음). 가짜 환경변수 고정."""
import json
import os

import pytest

from pcmigrate import drop_stage as ds, find_data as fd
from pcmigrate.gui import state as S, texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner
from pcmigrate.find_data import SelectedItem  # noqa: F401
from pcmigrate.multi_transfer import MultiReport

from test_gui_controller import FakeApi
from test_gui_my_files_controller import FakeData, find_result


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        d = tmp_path / "oldpcuser_env" / k
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(k, str(d))


class DropData(FakeData):
    """전송 호출에 넘어온 extra_files / abort_report 를 기록하고 drop_stage 결과를 흉내 낸다."""
    def __init__(self, result=None, fail=None):
        super().__init__(result)
        self.extra_seen, self.abort_seen, self.fail = [], [], fail

    def run_transfer_selection(self, selection, dst, *, extra_files=None, abort_report=None, dry_run=False, **kw):
        self.extra_seen.append(list(extra_files or []))
        self.abort_seen.append(abort_report)
        if self.fail:
            raise self.fail
        mr = super().run_transfer_selection(selection, dst, dry_run=dry_run, **kw) if selection else MultiReport(dry_run=dry_run)
        if extra_files:
            mr.meta["drop_stage"] = {"entries": len(extra_files), "staged_files": 2, "staged_bytes": 30, "overlap_count": 1,
                                     "duplicate_count": 0, "cloud_only": [{"path": "X:\\c.bin", "size": 1}], "cloud_only_total": 1,
                                     "failed": [{"path": "X:\\gone.txt", "reason": "FileNotFoundError: x"}], "changed": [{"path": "p"}],
                                     "cleanup": {"removed": True}}
        return mr


@pytest.fixture
def env(tmp_path):
    data = DropData()
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api = data
    c.events = []
    c.listener = lambda kind, payload=None: c.events.append((kind, payload))
    work = tmp_path / "pc"
    for rel, n in (("A/a1.txt", 5), ("A/sub/a2.txt", 6), ("B/b1.txt", 7), ("loose.txt", 8)):
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * n)
    c.work = work
    return c


def sel_folder(c, path):
    c.add_data_folder(str(path))


def test_add_files_and_folder_records_path_size_time_only(env):
    msg = env.add_drop_paths([str(env.work / "loose.txt"), str(env.work / "B" / "b1.txt")])
    assert "2개" in msg
    n, size = env.drop_total()
    assert n == 2 and size == 8 + 7
    rows = env.drop_rows()
    assert rows[0][0].endswith("loose.txt") and rows[1][0].endswith("b1.txt") and rows[0][2]
    assert (env.work / "loose.txt").read_bytes() == b"x" * 8  # 복사/수정 없음


def test_duplicates_and_unreadable_are_reported_not_fatal(env):
    env.add_drop_paths([str(env.work / "loose.txt")])
    msg = env.add_drop_paths([str(env.work / "LOOSE.TXT"), str(env.work / "nope.bin"), str(env.work / "B" / "b1.txt")])
    assert T.DP_ALREADY.format(n=1) in msg and T.DP_BAD.format(n=1) in msg and T.DP_ADDED.format(n=1) in msg
    assert env.drop_total()[0] == 2


def test_empty_drop_means_virtual_items(env):
    with pytest.raises(GuardError, match="실제 파일이 없는"):
        env.add_drop_paths([])


def test_limit_blocks_all_and_adds_nothing(env, monkeypatch):
    monkeypatch.setattr(ds, "MAX_DROP_FILES", 2)
    with pytest.raises(GuardError, match="너무 많습니다"):
        env.add_drop_paths([str(env.work / "A" / "a1.txt"), str(env.work / "A" / "sub" / "a2.txt"), str(env.work / "loose.txt")])  # 파일 3개 > 2
    assert env.drop_total() == (0, 0)
    env.add_drop_paths([str(env.work / "loose.txt")])
    assert env.drop_total()[0] == 1


def test_next_button_condition_folders_or_drops(env):
    assert not env.data_has_send() and env.files_method_reason() != ""
    env.add_drop_paths([str(env.work / "loose.txt")])
    assert env.data_has_send()
    env.set_method(T.MV_EXTERNAL)
    assert env.files_method_reason() == ""
    env.clear_drops()
    assert not env.data_has_send() and env.files_method_reason() == T.WHY_FD_NEED_SELECTION
    with pytest.raises(GuardError):
        env.files_go_method()


def test_overlap_marking_and_note(env):
    sel_folder(env, env.work / "A")
    env.add_drop_paths([str(env.work / "A" / "sub" / "a2.txt"), str(env.work / "B" / "b1.txt")])
    rows = env.drop_rows()
    assert rows[0][4] and rows[0][3] == T.DP_TAG_COVERED and not rows[1][4]
    assert env.drop_notes() == [T.DP_OVERLAP.format(n=1)]


def test_lan_direct_send_with_drops_is_allowed(env):
    env.add_drop_paths([str(env.work / "loose.txt")])
    env.set_method(T.MV_DIRECT)
    env.set_role(T.MV_ROLE_SEND)
    assert env.files_method_reason() == ""
    assert env.lan_send_problem("192.0.2.1", "123456") == ""
    assert "직접 추가한 1개" in env.tx_selection_text() and "직접 추가한 1개" in env.tx_confirm_text("192.0.2.1")
    env.clear_drops()
    assert env.lan_send_problem("192.0.2.1", "123456") == T.G_TX_NEED_SELECTION


def test_totals_text_includes_drops(env):
    sel_folder(env, env.work / "A")
    env.add_drop_paths([str(env.work / "loose.txt")])
    assert "직접 추가한 1개" in env.data_total_text()
    env.set_data_selected(str(env.work / "A"), False)
    assert env.data_total_text().startswith("선택한 폴더 없이 직접 추가한 1개")


def start_flow(env, tmp_path, mode=T.MODE_EXTERNAL):
    env.set_method(T.MV_EXTERNAL)
    env.enter_selection_transfer(mode)
    env.state.transfer_dst = str(tmp_path / "out")
    return str(tmp_path / "out")


def test_drops_only_transfer_preview_then_copy_passes_extra_files_and_result_lines(env, tmp_path):
    env.add_drop_paths([str(env.work / "loose.txt"), str(env.work / "B" / "b1.txt")])
    start_flow(env, tmp_path)
    assert env.has_selection() and env.files_stage(S.TRANSFER) == 3
    rpt = str(tmp_path / "rep.json")
    env.start_transfer(dry_run=True)
    data = env.data_api
    assert len(data.extra_seen[-1]) == 2 and data.abort_seen[-1] is None
    sm = env.transfer_summary()
    assert sm["dry_run"] and sm["counts"]["would_copy"] == 2 and sm["drop_lines"]
    env.start_transfer(dry_run=False, write_confirmed=True, report_path=rpt)
    assert len(data.extra_seen[-1]) == 2 and data.abort_seen[-1] == rpt  # 중단돼도 리포트를 저장하도록 경로를 넘긴다
    assert not env.transfer_summary()["dry_run"] and os.path.isfile(rpt)


def test_result_summary_counts_drop_failures_cloud_and_lines(env, tmp_path):
    sel_folder(env, env.work / "A")
    env.add_drop_paths([str(env.work / "loose.txt")])
    start_flow(env, tmp_path)
    mr = env.data_api.run_transfer_selection(env.files.transfer_items, tmp_path / "out", extra_files=list(env.files.drops))
    env.state.transfer_report, env.state.transfer_was_dry_run = mr, False
    sm = env.transfer_summary()
    assert sm["failed"] == 1 and sm["cloud_only"] == 1 and ("X:\\c.bin" in sm["cloud_files"])
    assert any(f[0] == "X:\\gone.txt" for f in sm["failures"])
    lines = sm["drop_lines"]
    assert T.DP_RESULT.format(n=1, ok=2, skip=2, bad=1) == lines[0]
    assert T.DP_CLEANED in lines and T.DP_CHANGED.format(n=1) in lines and T.DP_GONE.format(n=1) in lines and T.DP_CLOUD.format(n=1) in lines
    assert mr.to_dict()["success"] is False  # 직접 고른 파일의 실패가 전체 성공 여부에 반영된다


def test_no_drops_behaves_exactly_as_before(env, tmp_path):
    sel_folder(env, env.work / "A")
    start_flow(env, tmp_path)
    env.start_transfer(dry_run=True)
    assert env.data_api.extra_seen[-1] == [] and env.data_api.abort_seen[-1] is None
    assert env.transfer_summary()["drop_lines"] == []
    assert env.drop_notes() == [] and env.drop_rows() == []


def test_drops_change_invalidates_selection_and_busy_blocks_edits(env, tmp_path):
    env.add_drop_paths([str(env.work / "loose.txt")])
    start_flow(env, tmp_path)
    assert env.files.use_selection
    env.add_drop_paths([str(env.work / "B" / "b1.txt")])
    assert not env.files.use_selection  # 목록이 달라지면 이전 미리보기/선택은 무효
    env.state.busy = True
    for fn, arg in ((env.add_drop_paths, [str(env.work / "A")]), (env.remove_drop_paths, ["x"])):
        with pytest.raises(GuardError):
            fn(arg)
    with pytest.raises(GuardError):
        env.clear_drops()
    env.state.busy = False


def test_remove_and_clear(env):
    env.add_drop_paths([str(env.work / "loose.txt"), str(env.work / "B" / "b1.txt")])
    assert env.remove_drop_paths([str(env.work / "LOOSE.txt")]) == 1
    assert [os.path.basename(e.path) for e in env.drop_entries()] == ["b1.txt"]
    env.clear_drops()
    assert env.drop_entries() == []


def test_space_shortage_is_translated_to_plain_message(env, tmp_path):
    env.add_drop_paths([str(env.work / "loose.txt")])
    start_flow(env, tmp_path)
    env.data_api.fail = ds.DropSpaceError("임시 폴더가 있는 드라이브의 여유 공간이 부족합니다. 약 1GB 필요", "temp")
    env.start_transfer(dry_run=True)
    errs = [p for k, p in env.events if k == "error"]
    assert errs and T.DP_TEMP_LOW in (errs[-1].what + errs[-1].how + errs[-1].detail) and env.state.busy is False
    env.data_api.fail = ds.DropSpaceError("대상 드라이브의 여유 공간이 부족합니다", "dest")
    env.start_transfer(dry_run=True)
    errs = [p for k, p in env.events if k == "error"]
    assert T.DP_DEST_LOW in (errs[-1].what + errs[-1].how + errs[-1].detail)


class FakeLanSend:
    def __init__(self, report):
        self.report, self.kw, self.sel = report, None, None

    def run_send(self, target, code, selection=None, options=None, progress_info=None, cancel=None, **kw):
        self.kw, self.sel = kw, selection
        return self.report


def test_lan_send_passes_extra_files_and_shows_drop_lines(env):
    sel_folder(env, env.work / "A")
    env.add_drop_paths([str(env.work / "loose.txt")])
    drop = {"entries": 1, "staged_files": 1, "overlap_count": 0, "duplicate_count": 0, "failed": [], "cleanup": {"removed": True}}
    lan = FakeLanSend({"success": True, "files": 2, "verification": {"exact": 2, "mismatch": 0, "missing": 0}, "drop_stage": drop})
    env.lan_api = lan
    env.lan_send_start("192.0.2.1", "123456", confirmed=True)
    assert len(lan.kw["extra_files"]) == 1 and [i.path for i in lan.sel] == [str(env.work / "A")]
    _text, ok, notes = env.tx_result_lines()
    assert ok and T.DP_RESULT.format(n=1, ok=1, skip=0, bad=0) in notes and T.DP_CLEANED in notes


def test_lan_send_without_drops_passes_nothing_extra(env):
    sel_folder(env, env.work / "A")
    lan = FakeLanSend({"success": True, "files": 1, "verification": {"exact": 1, "mismatch": 0, "missing": 0}})
    env.lan_api = lan
    env.lan_send_start("192.0.2.1", "123456", confirmed=True)
    assert lan.kw == {}


def test_folders_stay_in_the_box_list_and_go_as_selected_folders(env):
    """stage70: 폴더도 한 목록(상자)에 담긴다. 위쪽 자동 목록(폴더 표)에는 들어가지 않고, 전송에서는 폴더째(선택 폴더 항목) 간다."""
    msg = env.add_drop_paths([str(env.work / "B"), str(env.work / "loose.txt")])
    assert T.DP_ADDED.format(n=2) in msg
    assert env.drop_total()[0] == 2 and [e.is_dir for e in env.drop_entries()] == [True, False]
    assert env.data_selection() == [] and not [r for r in env.data_rows(visible_only=False)]  # 위쪽은 자동 목록 전용
    rows = env.drop_rows()
    assert rows[0][3] == T.DP_TAG_FOLDER and rows[0][1] == "-"  # 폴더: 크기는 읽지 않음, '(폴더)' 꼬리표
    items = env.data_selected_items()
    assert [i.path for i in items] == [str(env.work / "B")] and items[0].include
    assert [e.path for e in env.drop_files()] == [str(env.work / "loose.txt")]
    assert env.data_has_send()
    assert "크기를 아직 모릅니다" in env.drop_total_text()


def test_system_folders_are_refused_in_the_box(env):
    from pcmigrate.gui.files_flow import unsafe_data_folder
    root = os.path.abspath(os.sep)
    assert unsafe_data_folder(root)
    with pytest.raises(GuardError, match="드라이브 전체"):
        env.add_drop_paths([root])
    msg = env.add_drop_paths([root, str(env.work / "B")])  # 섞여 있으면 안전한 것만 담고 안내
    assert T.G_FD_SYSTEM_FOLDER in msg and env.drop_total()[0] == 1


def test_folder_overlap_rules_by_path_prefix_only(env):
    """겹침: 직접 추가한 폴더가 체크 폴더 안 -> 그 폴더 뺌 / 체크 폴더를 품음 -> 안쪽 자동 폴더 뺌 / 직접 추가끼리 / 파일."""
    work = env.work
    sub = work / "A" / "sub"
    # (1) 직접 추가한 폴더가 체크한 자동 폴더 안에 있다
    env.add_data_folder(str(work / "A"))
    env.add_drop_paths([str(sub), str(work / "A" / "a1.txt"), str(work / "B")])
    rows = {os.path.basename(r[0]): r for r in env.drop_rows()}
    assert rows["sub"][3] == T.DP_TAG_COVERED and rows["sub"][4] and rows["a1.txt"][4] and not rows["B"][4]
    assert env.drop_notes() == [T.DP_OVERLAP.format(n=2)]
    # (2) 직접 추가한 폴더가 체크한 자동 폴더를 품는다: 안쪽 자동 폴더가 빠진다
    env.clear_drops()
    env.add_drop_paths([str(work)])
    assert env.drop_notes() == [T.DP_OVERLAP.format(n=1)]  # 자동 체크 폴더 A 가 바깥 폴더에 포함
    assert env.folder_cover() == {str(work / "A"): str(work)}
    # (3) 직접 추가한 폴더끼리, 같은 폴더(자동과 직접 추가)
    env.clear_drops()
    msg = env.add_drop_paths([str(work / "A"), str(work / "A" / "sub")])
    assert T.DP_ALREADY.format(n=1) in msg  # stage71: 자동 목록(옛 직접 추가 포함)에 이미 있는 같은 폴더는 다시 담지 않는다(두 번 세지 않음)
    assert env.folder_cover()[str(work / "A" / "sub")] == str(work / "A")


def test_overlap_removal_in_transfer_sends_outer_folder_once(tmp_path):
    """백엔드 확인: 바깥(직접 추가) 폴더 + 안쪽(자동) 폴더 + 안쪽 파일 -> 폴더 한 번, 이중 복사 없음, 리포트에 뺀 목록."""
    from pcmigrate import multi_transfer as mt
    from pcmigrate.data_api import run_transfer_selection
    from test_find_data_multi_transfer import FakeRobo, deps_with, put
    base = tmp_path / "pc" / "P"
    put(base / "A" / "a1.txt", b"1")
    put(base / "B" / "b1.txt", b"2")
    put(base / "loose.txt", b"3")
    items = [SelectedItem(str(base / "A"), True), SelectedItem(str(base), True)]
    rep = run_transfer_selection(items, tmp_path / "out", deps=deps_with(FakeRobo()), keep_awake=False, extra_files=[base / "A" / "a1.txt", base / "loose.txt"])
    d = rep.to_dict()
    assert [i["src"] for i in d["items"]] == [str(base)] and d["dropped"][0]["path"] == str(base / "A")
    assert d["drop_stage"]["overlap_count"] == 2 and d["success"]
    rel = mt.dest_rel(str(base))
    assert sorted(p.name for p in (tmp_path / "out" / rel).rglob("*.txt")) == ["a1.txt", "b1.txt", "loose.txt"]


def test_folders_have_no_file_count_limit_but_files_do(env, monkeypatch):
    monkeypatch.setattr(ds, "MAX_DROP_FILES", 1)
    env.add_drop_paths([str(env.work)])  # 폴더째: 안에 파일이 여러 개여도 제한 없음, 안을 읽지도 않는다
    assert env.drop_total()[0] == 1
    with pytest.raises(GuardError, match="너무 많습니다"):
        env.add_drop_paths([str(env.work / "A" / "a1.txt"), str(env.work / "loose.txt")])
    assert env.drop_total()[0] == 1


def test_removing_a_folder_from_the_box_removes_its_selected_item(env):
    env.add_drop_paths([str(env.work / "B"), str(env.work / "loose.txt")])
    assert len(env.data_selected_items()) == 1
    assert env.remove_drop_paths([str(env.work / "B")]) == 1
    assert env.data_selected_items() == [] and [e.path for e in env.drop_files()] == [str(env.work / "loose.txt")]


def test_folder_box_item_is_not_filtered_even_if_auto_list_excluded_it(env, tmp_path):
    """자동 목록의 종류 필터로 숨겨진 폴더도 직접 추가하면 필터 없이 그대로 보낸다."""
    env.set_data_filter("design", False)  # 필터를 줄여도
    env.add_drop_paths([str(env.work / "B")])
    start_flow(env, tmp_path)
    env.start_transfer(dry_run=True)
    assert any(c[0] == "transfer" and str(env.work / "B") in c[1] for c in env.data_api.calls)


def test_dropped_folder_is_sent_as_selected_folder_never_as_extra(env, tmp_path):
    env.add_drop_paths([str(env.work / "B"), str(env.work / "loose.txt")])
    start_flow(env, tmp_path)
    env.start_transfer(dry_run=True)
    d = env.data_api
    assert [os.path.basename(e.path) for e in d.extra_seen[-1]] == ["loose.txt"]  # 폴더는 extra 로 가지 않는다
    assert any(c[0] == "transfer" and c[1] == [str(env.work / "B")] for c in d.calls)  # 폴더째(선택한 폴더)


def test_folder_table_behaviour_unchanged_without_drops(env):
    env.add_data_folder(str(env.work / "A"))
    assert env.data_selection() == [str(env.work / "A")] and env.drop_total() == (0, 0)
    with pytest.raises(GuardError):
        env.add_data_folder(str(env.work / "A"))  # 이미 선택됨 안내(기존 동작)
