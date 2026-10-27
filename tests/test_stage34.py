"""stage34: 시작 화면 카드 순서(① 구 PC → ② 내 파일 옮기기 → ③ 새 PC), ① 완료 안내, ② 설정 묶음 함께 보내기, ③ 받은 폴더 자동 찾기,
탐색기에서 보기(열지 않고 보여 주기만) 시험(화면 없음). 가짜 data_api / lan_api / 탐색기 함수만 쓴다. 실제 스캔·전송·탐색기 실행은 하지 않는다."""
import hashlib
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import find_data as fd
from pcmigrate.gui import explorer as X
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi
from test_stage30a_files import FakeData, FakeLan, folder

GUI_DIR = Path(__file__).resolve().parents[1] / "src" / "pcmigrate" / "gui"
ROOT = Path(__file__).resolve().parents[1]


# =========================================================================== 카드 순서와 문구
def test_start_cards_follow_the_real_order_and_keep_screen_ids():
    titles = [" ".join(c[0].split()) for c in T.START_CHOICES]
    assert titles[0].startswith("① 쓰던 PC에서 준비하기") and titles[1].startswith("② 내 파일 옮기기") \
        and titles[2].startswith("③ 새 PC에서 설치하고 가져오기")
    assert [c[3] for c in T.START_CHOICES] == [S.OLD_SCAN, S.FIND, S.NEW_PACKAGE]  # 화면 id 는 그대로, 배치만 바뀜
    assert T.START_WHERE == ("구 PC에서", "양쪽 PC에서: 보내는 쪽은 구 PC, 받는 쪽은 새 PC", "새 PC에서")
    assert T.OVERALL_FLOWS["old"].startswith("①") and T.OVERALL_FLOWS["files"].startswith("②") and T.OVERALL_FLOWS["new"].startswith("③")
    for scr in (S.FIND, S.METHOD, S.LAN_RECV, S.LAN_SEND, S.TRANSFER):
        assert T.TITLES[scr].startswith("② 내 파일 옮기기"), scr


def test_help_roadmap_and_auto_texts_use_the_new_numbers():
    assert "③" in T.HELP[S.OLD_EXPORT] and "② 내 파일 옮기기" in T.HELP[S.OLD_EXPORT]
    start = T.HELP[S.START]
    assert start.index("①") < start.index("②") < start.index("③") and "새 PC에서 설치하고 가져오기는 ③" in start
    blob = T.roadmap_text()
    assert "'② 내 파일 옮기기'" in blob and "'③ 새 PC에서 설치하고 가져오기'" in blob and "'① 쓰던 PC에서 준비하기'" in blob
    assert "연결(보내기·받기)은 ②에서만" in T.ROADMAP_INTRO and "①과 ③은 각 PC 안에서만" in T.ROADMAP_INTRO
    assert T.DISC_SELECTED_BIG.count("②") == 1 and "(③)" not in T.DISC_SELECTED_BIG
    assert "②" in T.AUTO_TR_BLOCKED_HOW and "②" in T.AUTO_TR_FAT32_HOW
    assert "받은 폴더" in T.TIP_AUTO["new"] and "받은 폴더" in T.AUTO_PKG_NONE_WHAT


def test_export_done_next_step_is_one_line_pointing_to_step_two():
    assert len(T.EXP_NEXT_STEPS) == 1
    line = T.EXP_NEXT_STEPS[0]
    assert line == "다음은 ② 내 파일 옮기기에서 이 설정 묶음을 새 PC로 보내 주세요(두 PC를 바로 연결하거나 외장 드라이브 사용)."
    assert line.endswith("주세요(두 PC를 바로 연결하거나 외장 드라이브 사용).")  # 용어집: ~해 주세요


def test_no_stale_old_numbering_left_in_screen_texts():
    stale = ["'내 파일 옮기기'(③)", "(② 새 PC에서 설치하고 가져오기)", "새 PC에서 ②(신 PC)", "③ 큰 파일", "③ 내 파일 옮기기"]
    blob = "\n".join(str(v) for v in vars(T).values() if isinstance(v, (str, tuple, list, dict)))
    for s in stale:
        assert s not in blob, s


# =========================================================================== 탐색기 경로 검증
GOOD = ["C:\\Projects\\Alpha", "D:/Data/사진", "c:\\a b\\c.txt", "E:\\"]
BAD = [
    "", None, 5, " C:\\a", "C:\\a ", "relative\\path", "..\\x", "C:relative", "\\\\server\\share\\x", "//server/share/x",
    "\\\\?\\C:\\a", "\\\\.\\PhysicalDrive0", "\\\\?\\UNC\\s\\x", "C:\\a\\..\\b", "C:\\a&calc", "C:\\a|b", "C:\\a;b", "C:\\a>b", "C:\\a<b",
    "C:\\a^b", "C:\\%TEMP%\\x", "C:\\a\"b", "C:\\a`b", "C:\\a\nb", "C:\\a\x00b", "C:\\a?b", "C:\\a*b", "C:\\a.txt:stream", "http://example.com/x",
    "C:\\" + "a" * 1100,
]


@pytest.mark.parametrize("path", GOOD)
def test_clean_local_path_accepts_plain_absolute_disk_paths(path):
    out = X.clean_local_path(path)
    assert out and out[1] == ":" and "/" not in out


@pytest.mark.parametrize("path", BAD)
def test_clean_local_path_rejects_unc_device_relative_and_command_separators(path):
    assert X.clean_local_path(path) is None


def test_openable_needs_existing_local_disk_and_folder_unless_selecting():
    fixed = lambda root: 3  # noqa: E731
    exists = lambda p: p.endswith(("Alpha", "a.txt"))  # noqa: E731
    isdir = lambda p: p.endswith("Alpha")  # noqa: E731
    kw = dict(exists=exists, isdir=isdir, drive_type_fn=fixed)
    assert X.openable("C:\\x\\Alpha", **kw) is True
    assert X.openable("C:\\x\\a.txt", False, **kw) is False  # 파일은 열지 않는다(폴더만)
    assert X.openable("C:\\x\\a.txt", True, **kw) is True  # 파일은 선택해서 보여 주기만
    assert X.openable("C:\\x\\none", True, **kw) is False  # 없는 경로
    for kind in (0, 1, 4, 5, 6):  # 알 수 없음/루트 없음/네트워크/CD/램디스크
        assert X.openable("C:\\x\\Alpha", exists=exists, isdir=isdir, drive_type_fn=lambda r, k=kind: k) is False, kind
    assert X.openable("C:\\x\\Alpha", exists=exists, isdir=isdir, drive_type_fn=lambda r: 2) is True  # 이동식 드라이브


def test_explorer_open_uses_argument_list_never_shell_and_never_opens_files():
    calls = []
    popen = lambda args, **kw: calls.append((args, kw))  # noqa: E731
    kw = dict(popen=popen, exists=lambda p: True, isdir=lambda p: p.endswith("Dir"), drive_type_fn=lambda r: 3)
    assert X.explorer_open("C:/Data/Dir", False, **kw) is True
    assert calls[-1][0] == ["explorer.exe", "C:\\Data\\Dir"] and calls[-1][1] == {"shell": False}
    assert X.explorer_open("C:/Data/Dir/file one.cfg", True, **kw) is True
    assert calls[-1][0] == ["explorer.exe", "/select,C:\\Data\\Dir\\file one.cfg"] and isinstance(calls[-1][0], list)
    n = len(calls)
    assert X.explorer_open("C:\\Data\\Dir\\file.exe", False, **kw) is False  # 파일을 폴더처럼 열지 않는다
    assert X.explorer_open("C:\\a&calc.exe", True, **kw) is False and X.explorer_open("\\\\srv\\s\\x", False, **kw) is False
    assert len(calls) == n  # 거절된 경로로는 아무것도 호출하지 않는다


def test_explorer_open_failure_to_start_is_reported_not_raised():
    def boom(*a, **k):
        raise OSError("no explorer")
    assert X.explorer_open("C:\\Dir", False, popen=boom, exists=lambda p: True, isdir=lambda p: True, drive_type_fn=lambda r: 3) is False


def test_explorer_module_never_uses_startfile_or_shell_true():
    src = (GUI_DIR / "explorer.py").read_text(encoding="utf-8")
    assert "startfile" not in src.replace("os.startfile 은 쓰지 않는다", "") and "shell=True" not in src
    assert 'popen(args, shell=False)' in src
    # 화면/컨트롤러가 탐색기를 직접 부르지 않는다: 호출은 explorer_open 한 곳(컨트롤러 show_in_explorer 가 바꿔 끼울 수 있게 감싼다)
    for path in GUI_DIR.rglob("*.py"):
        if path.name in ("explorer.py", "common.py"):
            continue
        text = path.read_text(encoding="utf-8")
        assert "startfile" not in text and "explorer.exe" not in text, path.name


def test_controller_show_in_explorer_is_injectable_and_picks_folder_or_select(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    d = tmp_path / "폴더"
    d.mkdir()
    f = d / "a.cfg"
    f.write_text("x")
    calls = []
    c.explorer_fn = lambda path, select: calls.append((path, select)) or True
    assert c.show_in_explorer(str(d)) is True and calls[-1] == (str(d), False)  # 폴더: 폴더를 연다
    assert c.show_in_explorer(str(f)) is True and calls[-1] == (str(f), True)  # 파일: 선택된 상태로 부모 폴더
    assert c.can_show_in_explorer(str(d)) and c.can_show_in_explorer(str(f))
    assert not c.can_show_in_explorer(str(tmp_path / "없음")) and not c.can_show_in_explorer("\\\\srv\\share\\x")
    c.explorer_fn = lambda path, select: (_ for _ in ()).throw(RuntimeError("x"))
    assert c.show_in_explorer(str(d)) is False  # 오류가 화면을 막지 않는다


def test_coverage_example_abs_path_only_when_folder_and_name_are_safe():
    from pcmigrate.gui.views.common import CoverageBox
    f = CoverageBox.example_abs_path
    assert f("C:\\Users\\x\\App", "sub/a.db") == ("C:\\Users\\x\\App\\sub\\a.db", True)
    assert f("%APPDATA%\\Vendor", "a.db") is None  # 풀리지 않은 환경변수
    assert f("", "a.db") is None and f("C:\\App", "") is None
    assert f("C:\\App", "..\\evil.exe") is None and f("C:\\App", "D:\\other.exe") is None and f("C:\\App", "\\\\srv\\s\\x") is None
    assert f("C:\\App", "a&b.db") is None


# =========================================================================== ② 설정 묶음 함께 보내기(컨트롤러)
class StagedData(FakeData):
    """전송 계층이 폴더 단위라는 점을 흉내 낸다: 호출 시점에 설정 묶음 임시 폴더 안에 zip 복사본이 있는지 기록한다."""

    def __init__(self, result=None):
        super().__init__(result)
        self.seen = []

    def run_transfer_selection(self, selection, dst, **kw):
        for it in selection:
            if getattr(it, "label", "") == T.BUNDLE_LABEL:
                self.seen.append(sorted(os.listdir(it.path)) if os.path.isdir(it.path) else None)
        return super().run_transfer_selection(selection, dst, **kw)


class StagedLan(FakeLan):
    def __init__(self, tmp):
        super().__init__(tmp)
        self.seen = []

    def run_send(self, target, code, selection=None, **kw):
        for it in selection:
            if getattr(it, "label", "") == T.BUNDLE_LABEL:
                self.seen.append(sorted(os.listdir(it.path)) if os.path.isdir(it.path) else None)
        return super().run_send(target, code, selection=selection, **kw)


def make_zip(path: Path, payload: bytes = b"PK-fake-zip") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return str(path)


@pytest.fixture
def env(tmp_path):
    desk, tmpbase, proj = tmp_path / "Desktop", tmp_path / "Temp", tmp_path / "Proj"
    desk.mkdir(), tmpbase.mkdir(), proj.mkdir()
    res = fd.FindResult()
    res.projects = [folder(str(proj), design=1, total=5 * 1024 ** 3)]  # 실제로 있는 시험용 폴더(전송 계층은 가짜라 읽지 않는다)
    res.complete, res.files_seen, res.seconds = True, 1, 1.0
    data, lan = StagedData(res), StagedLan(tmp_path)
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = data, lan
    c.default_desktop = lambda: str(desk)
    c.default_downloads = lambda: str(tmp_path / "Downloads")
    c.list_drives = lambda: []
    c.bundle_temp_root = str(tmpbase)  # %TEMP% 대신 시험용 임시 위치(실제 임시 폴더를 건드리지 않는다)
    ev = []
    c.listener = lambda kind, payload=None: ev.append((kind, payload))
    return SimpleNamespace(c=c, data=data, lan=lan, desk=desk, tmpbase=tmpbase, proj=proj, tmp=tmp_path, ev=ev)


def pick_folder(env):
    env.c.find_data_start()
    p = env.c.data_rows()[0].path
    env.c.set_data_selected(p, True)
    return p


def test_bundle_row_absent_without_zip_and_never_auto_selected(env):
    c = env.c
    assert c.bundle_source() is None and c.bundle_label() == "" and c.bundle_item() is None and not c.bundle_included()
    with pytest.raises(GuardError):
        c.set_bundle_included(True)  # 묶음이 없으면 체크할 수 없다
    assert c.data_selected_items() == [] and c.data_has_send() is False
    make_zip(env.desk / "pcmigrate-settings.zip", b"x" * 2048)
    assert c.bundle_source() == (str(env.desk / "pcmigrate-settings.zip"), 2048)
    assert c.files.bundle_include is False and c.bundle_included() is False and c.bundle_item() is None  # 자동 선택 금지
    assert "pcmigrate-settings.zip" in c.bundle_label() and "함께 보내기" in c.bundle_label() and c.bundle_label().startswith("① 에서 만든 설정 묶음")


def test_bundle_source_prefers_last_export_result_then_default_desktop(env):
    c = env.c
    default = make_zip(env.desk / "pcmigrate-settings.zip")
    last = make_zip(env.tmp / "USB" / "mine.zip", b"y" * 10)
    c.state.export_output = last
    assert c.bundle_source() == (last, 10)
    c.state.export_output = str(env.tmp / "gone.zip")  # 마지막 결과가 지워졌으면 기본 위치
    assert c.bundle_source() == (default, os.path.getsize(default))
    c.state.export_output = str(env.tmp / "notzip.txt")
    (env.tmp / "notzip.txt").write_text("x")
    assert c.bundle_source()[0] == default  # .zip 이 아닌 파일은 묶음으로 보지 않는다


def test_checked_bundle_joins_the_send_list_as_a_folder_item_with_total_note(env):
    c = env.c
    zpath = make_zip(env.desk / "pcmigrate-settings.zip", b"z" * 3000)
    folder = pick_folder(env)
    assert c.data_total_text() == T.FD_TOTAL.format(n=1, size="5.0GB")
    c.set_bundle_included(True)
    items = c.data_selected_items()
    assert [i.path for i in items][0] == folder and len(items) == 2
    b = items[-1]
    assert c.is_bundle_item(b) and b.label == T.BUNDLE_LABEL and b.total_files == 1 and b.total_bytes == 3000
    assert os.path.dirname(b.path) == str(env.tmpbase) and os.path.basename(b.path).startswith("pcmigrate-bundle-")
    assert not os.path.exists(b.path)  # 체크만으로는 아무것도 만들지 않는다
    assert T.BUNDLE_INCLUDED in c.data_total_text() and c.data_total_text().startswith(T.FD_TOTAL.format(n=1, size="5.0GB"))
    c.set_bundle_included(False)
    assert len(c.data_selected_items()) == 1 and T.BUNDLE_INCLUDED not in c.data_total_text()
    assert open(zpath, "rb").read() == b"z" * 3000


def test_bundle_alone_is_enough_to_continue_and_total_says_so(env):
    c = env.c
    make_zip(env.desk / "pcmigrate-settings.zip", b"z" * 1024)
    with pytest.raises(GuardError):
        c.files_go_method()
    c.set_bundle_included(True)
    assert c.data_has_send() and T.BUNDLE_INCLUDED in c.data_total_text() and c.data_total()[0] == 0
    c.files_go_method()
    assert c.state.screen == S.METHOD
    c.set_method(T.MV_EXTERNAL)
    assert c.files_method_reason() == ""


def test_external_drive_and_share_flow_stage_the_zip_during_run_and_clean_up(env):
    c = env.c
    zpath = make_zip(env.desk / "pcmigrate-settings.zip", b"zip-bytes")
    before = (hashlib.sha1(open(zpath, "rb").read()).hexdigest(), os.path.getmtime(zpath))
    folder = pick_folder(env)
    c.set_bundle_included(True)
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    assert c.has_selection() and len(c.files.transfer_items) == 2
    s = c.state
    s.transfer_dst = str(env.tmp / "외장")
    (env.tmp / "외장").mkdir()
    box = c.selection_box_text()
    assert T.BUNDLE_INCLUDED in box and "pcmigrate-settings.zip" in box and T.TR_SELECTION_BOX.format(n=1, size="약 5.0GB") in box
    assert T.BUNDLE_INCLUDED in c.src_display() and c.src_display().startswith(T.TR_SELECTION_SRC.format(n=1))
    c.start_transfer(True, False, False, "", False)  # 미리보기
    stage = c.files.transfer_items[-1].path
    assert [x[1] for x in env.data.calls if x[0] == "transfer"][-1] == [folder, stage]  # 폴더 단위 전송 계층에 폴더 항목으로 들어간다
    assert env.data.seen[-1] == ["pcmigrate-settings.zip"]  # 실행 중에는 임시 폴더에 복사본이 있었다
    assert not os.path.exists(stage) and os.listdir(env.tmpbase) == []  # 끝난 뒤 임시 폴더를 지웠다
    assert c.transfer_summary()["bundle"] == 1
    c.start_transfer(False, False, False, "", True)  # 실제 복사
    assert env.data.seen[-1] == ["pcmigrate-settings.zip"] and not os.path.exists(stage)
    assert c.transfer_summary()["bundle"] == 1 and not c.transfer_summary()["dry_run"]
    assert (hashlib.sha1(open(zpath, "rb").read()).hexdigest(), os.path.getmtime(zpath)) == before  # 원본 zip 은 읽기만


def test_staging_is_cleaned_even_when_the_transfer_fails_or_is_cancelled(env):
    c = env.c
    make_zip(env.desk / "pcmigrate-settings.zip")
    pick_folder(env)
    c.set_bundle_included(True)
    items = c.data_selected_items()
    with pytest.raises(RuntimeError):
        with c.bundle_staging(items):
            assert os.path.isfile(os.path.join(items[-1].path, "pcmigrate-settings.zip"))
            raise RuntimeError("전송 중 오류")
    assert not os.path.exists(items[-1].path)


def test_staging_never_deletes_content_it_did_not_create(env):
    c = env.c
    make_zip(env.desk / "pcmigrate-settings.zip")
    c.set_bundle_included(True)
    items = c.data_selected_items()
    stage = Path(items[-1].path)
    stage.mkdir()
    (stage / "남의파일.txt").write_text("keep")
    with pytest.raises(GuardError):
        with c.bundle_staging(items):
            pass
    assert (stage / "남의파일.txt").read_text() == "keep"  # 우리가 만들지 않은 내용은 건드리지 않는다


def test_staging_stops_when_the_original_zip_disappears(env):
    c = env.c
    z = make_zip(env.desk / "pcmigrate-settings.zip")
    c.set_bundle_included(True)
    items = c.data_selected_items()
    os.remove(z)
    with pytest.raises(GuardError):
        with c.bundle_staging(items):
            pass
    assert c.data_selected_items() == [] and c.bundle_included() is False


def test_direct_send_stages_the_zip_and_confirm_and_result_mention_it(env):
    c = env.c
    zpath = make_zip(env.desk / "pcmigrate-settings.zip", b"q" * 100)
    folder = pick_folder(env)
    c.set_bundle_included(True)
    assert T.BUNDLE_INCLUDED in c.tx_selection_text() and "보낼 폴더: 1곳" in c.tx_selection_text()
    confirm = c.tx_confirm_text("192.0.2.20:47830")
    assert T.BUNDLE_LINE.format(name="pcmigrate-settings.zip", size="100B") in confirm and "선택한 폴더 1곳" in confirm
    c.lan_send_start("192.0.2.20:47830", "123456", confirmed=True)
    sent = [x for x in env.lan.calls if x[0] == "send"][-1]
    assert sent[3][0] == folder and len(sent[3]) == 2
    assert env.lan.seen[-1] == ["pcmigrate-settings.zip"] and os.listdir(env.tmpbase) == []  # 보낸 뒤 정리
    text, ok, notes = c.tx_result_lines()
    assert ok and notes[0] == T.BUNDLE_RESULT_NOTE and T.BUNDLE_INCLUDED in notes[0]
    assert open(zpath, "rb").read() == b"q" * 100


def test_direct_send_of_bundle_only_works_and_without_bundle_nothing_extra_appears(env):
    c = env.c
    make_zip(env.desk / "pcmigrate-settings.zip")
    c.set_bundle_included(True)
    assert T.BUNDLE_INCLUDED in c.tx_selection_text() and c.lan_send_problem("192.0.2.20", "123456") == ""
    c.set_bundle_included(False)
    assert c.lan_send_problem("192.0.2.20", "123456") == T.G_TX_NEED_SELECTION
    pick_folder(env)
    c.lan_send_start("192.0.2.20", "123456", confirmed=True)
    assert T.BUNDLE_INCLUDED not in c.tx_selection_text() and T.BUNDLE_RESULT_NOTE not in c.tx_result_lines()[2]


def test_changing_bundle_choice_resets_prepared_selection_transfer(env):
    c = env.c
    make_zip(env.desk / "pcmigrate-settings.zip")
    pick_folder(env)
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    old = c.files.signature
    c.go(S.FIND)
    c.set_bundle_included(True)
    assert c.files.use_selection is False  # 보낼 목록이 달라졌으니 앞의 미리보기는 쓰지 않는다
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    assert c.files.signature != old and len(c.files.transfer_items) == 2


def test_receiving_side_is_unchanged_default_receive_dir_is_used(env):
    assert env.c.lan_dest() == env.lan.default_receive_dir()


# =========================================================================== ③ 받은 폴더 자동 찾기
def touch(path: Path, mtime: float) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"zip")
    os.utime(path, (mtime, mtime))
    return str(path)


def test_find_packages_includes_received_folders_latest_first(env):
    c = env.c
    base = 1_700_000_000
    default = Path(env.lan.default_receive_dir())  # 아직 없는 기본 받기 폴더(시각이 이름에 들어간다)
    assert not default.exists()
    recv = env.desk / "PC이전_받은파일_20260101-000000"  # 바탕화면 아래 받은 파일 폴더(이전에 받은 것)
    nested = recv / "C" / "Users" / "someone" / "AppData" / "Local" / "Temp" / "pcmigrate-bundle-ab12cd34"  # 보낼 때의 폴더 구조가 그대로 들어 있다
    a = touch(nested / "pcmigrate-settings.zip", base + 300)
    d = touch(env.desk / "pcmigrate-settings.zip", base + 100)
    e = touch(env.tmp / "Downloads" / "pcmigrate-settings (1).zip", base + 200)
    # 기본 받기 폴더가 실제로 있을 때(와 그 한 단계 아래)
    default.mkdir(parents=True)
    f = touch(default / "pcmigrate-settings.zip", base + 50)
    g = touch(default / "하위" / "pcmigrate-settings.zip", base + 400)
    # 마지막으로 받은 폴더(수신 결과의 저장 위치)
    last = env.tmp / "다른곳"
    h = touch(last / "pcmigrate-settings.zip", base + 500)
    c.files.rx_result = {"dest": str(last), "files": 1}
    got = c.find_packages()
    assert got == [h, g, a, e, d, f]  # 최신 순, 같은 파일은 한 번만
    entries = c.find_package_entries()
    wheres = {p: w for p, _m, _s, w in entries}
    assert wheres[a] == T.PKG_WHERE_RECEIVED and wheres[h] == T.PKG_WHERE_RECEIVED and wheres[d] == T.PKG_WHERE_DESKTOP
    assert wheres[e] == T.PKG_WHERE_DOWNLOADS


def test_find_packages_ignores_missing_received_folder_and_other_zips(env):
    c = env.c
    c.files.rx_result = {"dest": str(env.tmp / "없는폴더")}
    touch(env.desk / "PC이전_받은파일_x" / "other.zip", 1_700_000_000)  # 이름이 다른 zip 은 찾지 않는다
    assert c.find_packages() == []
    c.lan_api = SimpleNamespace(default_receive_dir=lambda: (_ for _ in ()).throw(OSError("x")))  # 기본 위치를 모르면 마지막 받은 폴더만
    assert c.find_packages() == []


def test_find_packages_scan_is_bounded_and_reads_only(env):
    c = env.c
    recv = env.desk / "PC이전_받은파일_20260102-000000"
    deep = recv
    for i in range(12):  # 깊이 상한(9)보다 깊은 곳은 훑지 않는다
        deep = deep / f"d{i}"
    touch(deep / "pcmigrate-settings.zip", 1_700_000_000)
    assert c.find_packages() == []
    shallow = touch(recv / "a" / "b" / "pcmigrate-settings.zip", 1_700_000_100)
    assert c.find_packages() == [shallow]


def test_auto_new_flow_uses_received_folder_zip(env):
    c = env.c
    recv = env.desk / "PC이전_받은파일_20260102-000000"
    z = touch(recv / "C" / "x" / "pcmigrate-settings.zip", 1_700_000_100)
    c.auto_run("new")
    assert c.state.package == z  # 받은 폴더의 설정 묶음 한 개를 그대로 골랐다


def test_package_choices_respects_scan_switch(env):
    c = env.c
    touch(env.desk / "pcmigrate-settings.zip", 1_700_000_000)
    assert c.package_choices() == []  # 시험에서는 꺼져 있다(실제 폴더를 훑지 않게)
    c.scan_packages_on_show = True
    assert [p for p, *_ in c.package_choices()] == [str(env.desk / "pcmigrate-settings.zip")]


def test_step_three_gets_no_connection_features():
    src = (GUI_DIR / "views" / "new_pc.py").read_text(encoding="utf-8")
    for word in ("lan_", "LAN_", "files_receive_only", "files_go_method", "run_send", "start_receiver"):
        assert word not in src, word


# =========================================================================== 문서/릴리스 문구
def test_readme_and_docs_follow_the_new_order():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "| ② 내 파일 옮기기" in readme and "| ③ 새 PC에서 설치하고 가져오기(신 PC)" in readme
    assert readme.index("| ① 쓰던 PC") < readme.index("| ② 내 파일 옮기기") < readme.index("| ③ 새 PC에서 설치하고")
    assert "양쪽 PC에서: 보내는 쪽은 구 PC, 받는 쪽은 새 PC" in readme and "설정 묶음 함께 보내기" in readme and "폴더 보기" in readme
    md = (ROOT / "docs" / "field-guide.md").read_text(encoding="utf-8")
    ht = (ROOT / "docs" / "field-guide.html").read_text(encoding="utf-8")
    for doc in (md, ht):
        assert "② 내 파일 옮기기" in doc and "③ 새 PC에서 설치하고 가져오기" in doc and "①에서 만든 설정 묶음" in doc
        assert "GUI 의 ③" not in doc
    assert re.search(r"GUI 의 ② (\*\*|<strong>)내 파일 옮기기", md + ht)
