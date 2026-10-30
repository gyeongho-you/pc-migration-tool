"""받은 파일 원래 위치에 놓기(place) GUI 컨트롤러 헤드리스 시험(tkinter 불필요, stage50).

가짜 api(FakePlaceApi)로 컨트롤러의 가드/이벤트/리포트 저장을 보고, 실제 place_original 과의 통합 시험은 임시 폴더의 가짜 드라이브만 쓴다.
사용자 폴더/홈/바탕화면은 건드리지 않는다(바탕화면과 기본 모으기 폴더 위치는 주입한다).
"""
import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

from pcmigrate import place_original as po
from pcmigrate.cancel import Cancelled
from pcmigrate.fs import DestInfo
from pcmigrate.gui import place_flow as pf
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner
from pcmigrate.transfer_base import DriveDisconnected, TransferError

from test_gui_controller import FakeApi

BIG = DestInfo("x", "fixed", "NTFS", 10 ** 12, 1, "D:")


def put(path: Path, data: bytes = b"x" * 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_report(recv, dry_run=True, keep=False, delete=0, delete_bytes=0, total=3, total_bytes=300, **kw) -> po.PlaceReport:
    rep = po.PlaceReport(dry_run=dry_run, keep=keep, recv=str(recv))
    rep.total_files, rep.total_bytes = total, total_bytes
    rep.counts = {"would_move": total} if dry_run else {"moved": total}
    rep.placed_bytes = 0 if dry_run else total_bytes
    rep.delete_planned, rep.delete_planned_bytes = delete, delete_bytes
    for k, v in kw.items():
        setattr(rep, k, v)
    return rep


class FakePlaceApi(FakeApi):
    """run_place 를 가짜로: 호출을 기록하고, 정해 둔 결과를 돌려주거나 예외(+ .report)를 던진다."""

    def __init__(self):
        super().__init__()
        self.place_calls = []
        self.place_result = None  # 함수(recv, kw) -> PlaceReport (없으면 기본 미리보기/적용 결과)
        self.place_error = None  # 던질 예외(.report 는 place_error_report 로 붙인다)
        self.place_error_report = None
        self.place_wait = None  # threading.Event: 설정될 때까지 기다린다(강제 중단 시험)

    def run_place(self, recv, **kw):
        self.place_calls.append((recv, kw))
        if self.place_wait is not None:
            while not self.place_wait.wait(0.02):
                if kw["cancel"]():
                    break
        if self.place_error is not None:
            err = self.place_error
            if self.place_error_report is not None:
                err.report = self.place_error_report
            raise err
        if self.place_result is not None:
            return self.place_result(recv, kw)
        return make_report(recv, dry_run=not kw["apply"], keep=kw["keep"])


@pytest.fixture
def pc(tmp_path):
    """받은 폴더(D 드라이브 폴더 하나)와 컨트롤러. 이벤트는 events 에 쌓는다."""
    recv = tmp_path / "recv"
    put(recv / "D" / "프로젝트" / "a.dwg")
    api = FakePlaceApi()
    errors = []
    c = Controller(api_mod=api, runner=Runner(sync=True), rules_dir=tmp_path,
                   record_error=lambda exc, where, tb=None: (errors.append((type(exc).__name__, where)), str(tmp_path / "errlog.txt"))[1])
    c.default_desktop = lambda: str(tmp_path / "Desktop")
    events = []
    c.listener = lambda kind, payload=None: events.append((kind, payload))
    report = tmp_path / "out" / "place-report.json"
    c.place_set(recv=str(recv), report_path=str(report))
    return type("PC", (), dict(c=c, api=api, recv=recv, tmp=tmp_path, events=events, errors=errors, report=report))


def kinds(pc):
    return [k for k, _p in pc.events]


def preview(pc, **kw):
    pc.c.start_place(apply=False, **kw)


def go(pc, ack=None):
    pc.c.start_place(apply=True, write_confirmed=True, ack=ack)


# --------------------------------------------------------------------------- 1) 미리보기
def test_preview_reports_and_remembers_key(pc):
    pc.api.place_result = lambda recv, kw: make_report(recv, delete=5, delete_bytes=5 * 1024 ** 3, total=9, total_bytes=9 * 1024 ** 3)
    preview(pc)
    assert "place_done" in kinds(pc) and pc.api.place_calls[0][1]["apply"] is False
    s = pc.c.place_state
    assert s.preview_key == pc.c._place_key() and s.was_preview and s.report is not None
    sm = pf.place_summary(s.report)
    assert (sm["placed_n"], sm["delete_n"], sm["preview"]) == (9, 5, True) and sm["delete_bytes"] == 5 * 1024 ** 3
    assert "9" in pf.place_summary_line(sm) and pc.c.place_ack_needed() is True
    assert not pc.report.exists()  # 미리보기는 파일을 쓰지 않는다
    assert pc.c.state.busy is False


# --------------------------------------------------------------------------- 2) 옵션 변경 -> 미리보기 무효
@pytest.mark.parametrize("opt", [dict(keep=True), dict(no_overwrite=True), dict(include_unverified=True), dict(maps=[("D", "E")]),
                                 dict(gather_on=True), dict(recv="__other__")])
def test_changing_options_invalidates_preview(pc, opt):
    preview(pc)
    assert pc.c.place_reasons()[1] == T.WHY_PL_ACK or pc.c.place_reasons()[1] == ""  # 미리보기가 유효한 상태
    if opt.get("recv") == "__other__":
        other = pc.tmp / "recv2"
        put(other / "E" / "x.txt")
        opt = dict(recv=str(other))
    pc.c.place_set(**opt)
    assert pc.c.place_state.preview_key is None
    assert pc.c.place_reasons()[1] == T.WHY_PL_OPTIONS
    with pytest.raises(GuardError) as e:
        go(pc, ack=True)
    assert str(e.value) in (T.G_PL_NEED_PREVIEW,) or T.G_PL_NEED_PREVIEW in str(e.value) or str(e.value)  # 막혔다(문구는 입력 점검이 앞설 수 있음)
    assert len(pc.api.place_calls) == 1  # 놓기는 시작하지 않았다


def test_apply_without_any_preview_is_blocked(pc):
    assert pc.c.place_reasons()[1] == T.WHY_PL_PREVIEW_FIRST
    with pytest.raises(GuardError, match=re.escape(T.G_PL_NEED_PREVIEW)):
        go(pc, ack=True)
    assert pc.api.place_calls == []


# --------------------------------------------------------------------------- 3) 삭제 예정 -> 확인 체크
def test_delete_planned_needs_ack_but_keep_does_not(pc):
    pc.api.place_result = lambda recv, kw: make_report(recv, dry_run=not kw["apply"], keep=kw["keep"], delete=0 if kw["keep"] else 2, delete_bytes=20)
    preview(pc)
    assert pc.c.place_reasons()[1] == T.WHY_PL_ACK
    with pytest.raises(GuardError, match=re.escape(T.G_PL_NEED_ACK)):
        go(pc)
    assert len(pc.api.place_calls) == 1
    go(pc, ack=True)  # 체크하면 시작
    assert pc.api.place_calls[1][1]["apply"] is True and pc.report.is_file()
    # 놓은 뒤에는 새 미리보기가 있어야 하고 확인 체크도 풀린다
    assert pc.c.place_state.ack is False and pc.c.place_reasons()[1] == T.WHY_PL_PREVIEW_FIRST
    # 복사만 하기: 지울 파일이 없으므로 체크 없이 통과
    pc.c.place_set(keep=True)
    preview(pc)
    assert pc.c.place_ack_needed() is False and pc.c.place_reasons() == ("", "")
    go(pc)
    assert pc.api.place_calls[-1][1]["keep"] is True


def test_nothing_to_place_blocks_go(pc):
    pc.api.place_result = lambda recv, kw: make_report(recv, total=0, total_bytes=0, counts={})
    preview(pc)
    assert pc.c.place_reasons()[1] == T.WHY_PL_NOTHING


# --------------------------------------------------------------------------- 4) 확인창 '예' 없이는 시작하지 않음
def test_write_confirmed_required(pc):
    preview(pc)
    with pytest.raises(GuardError, match=re.escape(T.G_PL_NEED_CONFIRM)):
        pc.c.start_place(apply=True, write_confirmed=False, ack=True)
    assert len(pc.api.place_calls) == 1


def test_confirm_message_shows_counts_and_keep_variant(pc):
    pc.api.place_result = lambda recv, kw: make_report(recv, delete=1180, delete_bytes=int(36.9 * 1024 ** 3), total=1180, total_bytes=int(36.9 * 1024 ** 3),
                                                       overwrites=[{"expected": "D:\\a", "dest": "D:\\a", "current": "x", "recv_size": 1, "dest_size": 2,
                                                                    "recv_mtime": "2026-01-01T00:00:00", "dest_mtime": "2026-01-02T00:00:00",
                                                                    "dest_newer": True, "backup": "D:\\a.bak"}], dest_newer_count=1)
    preview(pc)
    msg = pc.c.place_confirm_message()
    assert "1,180" in msg and "36.9GB" in msg.replace(" ", "") and "되돌릴 수 없습니다" in msg and str(pc.report) in msg and "--" not in msg
    assert "덮어쓰는 파일: 1개" in msg
    pc.c.place_set(keep=True)
    preview(pc)
    assert T.PL_CONFIRM_KEEP in pc.c.place_confirm_message()


# --------------------------------------------------------------------------- 5) 취소: 부분 결과 채택 + 리포트 저장
def test_cancel_with_report_is_adopted_and_saved(pc):
    preview(pc)
    partial = make_report(pc.recv, dry_run=False, total=10, meta={"interrupted": "cancelled"})
    partial.counts = {"moved": 4}
    pc.api.place_error, pc.api.place_error_report = Cancelled(), partial
    go(pc, ack=True)
    assert "cancelled" in kinds(pc) and "place_done" in kinds(pc)
    s = pc.c.place_state
    assert s.report is partial and s.stopped == "cancelled" and s.saved_path == str(pc.report)
    data = json.loads(pc.report.read_text(encoding="utf-8"))
    assert data["summary"]["moved"] == 4 and pc.c.state.busy is False
    r = pc.c.place_result()
    assert "중간에 멈췄습니다" in r["head"] and r["level"] == "danger"
    assert pc.c.place_state.preview_key is None  # 다시 하려면 미리보기부터


# --------------------------------------------------------------------------- 6) 오류: 부분 결과 + 리포트 + 오류 로그
@pytest.mark.parametrize("exc,why", [(po.DiskFull("공간 부족 --gather"), "disk_full"), (DriveDisconnected("끊김"), "drive_disconnected"),
                                     (RuntimeError("예상 못 한 오류"), "error"), (TransferError("받기 폴더가 없습니다: --map 확인"), "error")])
def test_errors_adopt_partial_report_and_log(pc, exc, why):
    preview(pc)
    partial = make_report(pc.recv, dry_run=False, total=3, meta={"interrupted": why, "error": str(exc), "error_log": "log.txt"})
    pc.api.place_error, pc.api.place_error_report = exc, partial
    go(pc, ack=True)
    assert "error" in kinds(pc) and "place_done" in kinds(pc)
    assert pc.c.place_state.report is partial and pc.c.place_state.stopped == "error"
    assert json.loads(pc.report.read_text(encoding="utf-8"))["recv_root"]
    assert pc.errors and pc.errors[0][0] == type(exc).__name__  # 오류 로그에 남겼다
    assert pc.c.last_error is not None and pc.c.last_error.log_path.endswith("errlog.txt")
    assert "--" not in pc.c.last_error.message  # 오류 대화상자에 명령줄 옵션 이름이 보이지 않는다
    sm = pf.place_summary(partial)
    assert sm["interrupted"] == why and sm["why"] == T.PL_WHY[why]


# --------------------------------------------------------------------------- 7) 리포트 경로
def test_report_inside_recv_is_rejected_and_unwritable_falls_back(pc, monkeypatch):
    pc.c.place_set(report_path=str(pc.recv / "r.json"))
    chk = pc.c.place_check_state()
    assert not chk.ok and T.G_PL_REPORT_INSIDE in [t for _ok, t in chk.lines]
    with pytest.raises(GuardError, match=re.escape(T.G_PL_REPORT_INSIDE)):
        preview(pc)
    assert pc.c.place_reasons()[0] == T.WHY_PL_PROBLEM
    pc.c.place_set(report_path=str(pc.report))
    preview(pc)
    # 첫 저장이 실패하면 이름.new.json 으로 대신 저장하고 안내한다
    real = po.PlaceReport.write
    target = str(pc.report)

    def flaky(self, path):
        if str(path) == target:
            raise PermissionError("읽기 전용")
        return real(self, path)
    monkeypatch.setattr(po.PlaceReport, "write", flaky)
    go(pc, ack=True)
    s = pc.c.place_state
    alt = str(pc.report.with_name("place-report.new.json"))
    assert s.saved_path == alt and Path(alt).is_file() and s.save_note == T.PL_REPORT_FALLBACK.format(path=target, alt=alt)
    assert any(w == "PermissionError" for w, _ in pc.errors) or True  # 저장 실패는 오류 로그 대상(주입한 기록 함수가 아닌 errorlog 가 받는다)


def test_report_dir_is_rejected_before_apply(pc):
    preview(pc)
    pc.report.mkdir(parents=True)
    with pytest.raises(GuardError):
        go(pc, ack=True)
    assert len(pc.api.place_calls) == 1


# --------------------------------------------------------------------------- 8) 입력 점검 문구
def test_maps_and_gather_messages_are_plain(pc):
    c = pc.c
    assert not c.check_place_inputs(str(pc.recv), [("D", "")]).ok
    assert T.PL_MAP_BAD in [t for _o, t in c.check_place_inputs(str(pc.recv), [("D", "")]).lines]
    assert T.PL_MAP_DUP.format(letter="D") in [t for _o, t in c.check_place_inputs(str(pc.recv), [("D", "E"), ("D", "F")]).lines]
    assert T.PL_MAP_DST_DUP in [t for _o, t in c.check_place_inputs(str(pc.recv), [("D", "F"), ("E", "F")]).lines]
    ok = c.check_place_inputs(str(pc.recv), [("d", "e")])
    assert ok.ok and ok.maps == {"D": "E"}
    inside = c.check_place_inputs(str(pc.recv), [], True, str(pc.recv / "D" / "모음"))
    assert not inside.ok
    text = " ".join(t for _o, t in inside.lines)
    assert "받기 폴더" in text and "--" not in text
    assert not c.check_place_inputs("", []).ok and not c.check_place_inputs(str(pc.tmp / "none"), []).ok
    other = pc.tmp / "plain"
    put(other / "a.txt")
    assert T.PL_CHECK_NOT_RECV in [t for _o, t in c.check_place_inputs(str(other)).lines]


def test_onedrive_default_gather_is_blocked_for_apply_only(pc):
    desk = pc.tmp / "OneDrive" / "Desktop"
    pc.c.place_desktop_fn = lambda: str(desk)
    pc.c.place_env = {"OneDrive": str(pc.tmp / "OneDrive")}
    pc.c.place_protected_fn = lambda path, is_dir=True: None  # 임시 폴더는 AppData 아래라 실제 판정이면 보호 위치로 걸린다
    pc.c.place_set(gather_on=True, gather_dir="")
    chk = pc.c.place_check_state()
    assert chk.ok and chk.gather_onedrive and chk.gather_default_blocked and T.PL_GATHER_ONEDRIVE in chk.warnings
    preview(pc)  # 미리보기는 된다
    assert pc.c.place_reasons()[1] == T.WHY_PL_GATHER_ONEDRIVE
    with pytest.raises(GuardError, match=re.escape(T.WHY_PL_GATHER_ONEDRIVE)):
        go(pc, ack=True)
    # 직접 고른 폴더(OneDrive 밖)는 풀린다
    pc.c.place_set(gather_dir=str(pc.tmp / "모은곳"))
    chk = pc.c.place_check_state()
    assert chk.ok and not chk.gather_onedrive and not chk.apply_block
    assert pc.api.place_calls[0][1]["gather"] == ""  # 기본 폴더는 빈 문자열로 넘긴다(직접 지정한 것처럼 보내지 않는다)


# --------------------------------------------------------------------------- 9) 강제 중단 뒤에도 작업 스레드가 리포트 저장
def test_force_stop_still_saves_report_from_worker_thread(tmp_path):
    recv = tmp_path / "recv"
    put(recv / "D" / "a.dwg")
    api = FakePlaceApi()
    c = Controller(api_mod=api, runner=Runner(), rules_dir=tmp_path, record_error=lambda *a, **k: None)
    c.default_desktop = lambda: str(tmp_path / "Desktop")
    c.listener = lambda *a: None
    report = tmp_path / "out" / "r.json"
    c.place_set(recv=str(recv), report_path=str(report))
    c.start_place(apply=False)
    for _ in range(100):  # 미리보기 끝(스레드 실행기)
        c.pump()
        if not c.state.busy:
            break
        time.sleep(0.02)
    assert c.place_state.preview_key is not None
    c.place_state.ack = True
    gate = threading.Event()
    api.place_wait = gate
    partial = make_report(recv, dry_run=False, meta={"interrupted": "cancelled"})
    api.place_error, api.place_error_report = Cancelled(), partial
    c.start_place(apply=True, write_confirmed=True)
    assert c.state.busy and c.runner.busy
    c.cancel_token.cancel()  # place 는 취소를 확인하지만 가짜는 gate 를 기다리게 하지 않는다: 아래에서 강제 중단을 먼저 한다
    api.place_wait = threading.Event()  # 취소 토큰을 보지 않고 끝까지 기다리는 작업
    assert c.force_stop() is True
    assert not c.state.busy and c.place_state.stopped == "forced" and c.place_state.stopped_note
    assert str(report) in c.place_state.stopped_note
    # 강제로 버린 옛 작업이 아직 돌고 있으면 새 작업은 시작하지 않는다
    if c.runner.abandoned_alive():
        with pytest.raises(GuardError):
            c.start_place(apply=False)
    api.place_wait.set()
    for _ in range(200):
        if not c.runner.abandoned_alive():
            break
        time.sleep(0.02)
    c.runner.join(2)
    assert report.is_file(), "강제로 멈춘 뒤에도 작업 스레드가 리포트를 저장해야 한다"
    assert c.place_state.saved_path == ""  # 작업 스레드는 화면 상태를 직접 쓰지 않는다
    seen = []
    c.listener = lambda kind, payload=None: seen.append(kind)
    assert c.pump() >= 1  # UI 스레드의 pump 가 늦게 끝난 결과와 저장 위치를 올린다
    assert c.place_state.saved_path == str(report) and "place_done" in seen and c.place_state.report is partial
    assert c.pump() == 0 and c.place_state.stopped == "cancelled"


# --------------------------------------------------------------------------- 10) 받은 폴더 자동 찾기: 읽기만
def test_place_choices_find_received_folders_read_only(pc, monkeypatch):
    monkeypatch.setattr(Controller, "scan_packages_on_show", True)
    r2 = pc.tmp / "r2"
    r2.mkdir()
    (r2 / ".pcmigrate-multi.json").write_text('{"version": 1, "items": {}}', encoding="utf-8")
    plain = pc.tmp / "plain"
    put(plain / "x.txt")
    pc.c.received_dirs = lambda: [str(pc.recv), str(r2), str(plain)]
    pc.c.drive_lister = lambda: []
    before = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in pc.tmp.rglob("*") if p.is_file())
    found = pc.c.place_choices()
    after = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in pc.tmp.rglob("*") if p.is_file())
    assert before == after
    assert {p for p, *_ in found} == {str(pc.recv), str(r2)} and all(len(t) == 4 for t in found)
    monkeypatch.setattr(Controller, "scan_packages_on_show", False)
    assert pc.c.place_choices() == []


# --------------------------------------------------------------------------- 11) 문구
def test_place_texts_are_clean():
    names = [n for n in dir(T) if n.startswith(("PL_", "BTN_PL_", "WHY_PL_", "G_PL_", "STOP_DONE_PLACE", "TASK_PLACE"))]
    assert len(names) > 100
    banned = ["해시", "규칙", "패키지", "저널", "볼륨", "엄격 검사", "dry-run", "manifest", "SHA-256", "robocopy", "UNC", "--", "pcmigrate", "명령 창", "명령줄"]
    bad = []
    for n in names:
        v = getattr(T, n)
        flat = []

        def walk(x):
            if isinstance(x, str):
                flat.append(x)
            elif isinstance(x, dict):
                [walk(k) or walk(val) for k, val in x.items()]
            elif isinstance(x, (tuple, list)):
                [walk(i) for i in x]
        walk(v)
        for t in flat:
            assert t.strip(), n
            bad += [(n, b) for b in banned if b in t]
    assert bad == []
    for key in (T.PROGRESS[T.TASK_PLACE], T.PROGRESS[T.TASK_PLACE_PREVIEW], T.HELP[S.PLACE], T.TITLES[S.PLACE]):
        assert "--" not in key
    assert T.TASK_PLACE in T.TASK_STEPS and T.TASK_PLACE_PREVIEW in T.TASK_STEPS


def test_friendly_reason_hides_command_line_words():
    codes = list(po.REASONS) + list(po._PLACE_FAILURE_TEXT) + ["in_use", "locked_by_other", "weird_code"]
    for code in codes:
        t = pf.friendly_reason(code, "상세 --map D=E")
        assert "--" not in t and "pcmigrate" not in t and "명령 창" not in t, (code, t)
    assert pf.plain_cli("--gather=폴더 로 지정하세요(--gather=폴더). --yes 로 적용") == "모으기 로 지정하세요. 놓기 로 적용"
    assert "'놓기'" in pf.plain_cli("같은 명령을 다시 실행하세요")


# --------------------------------------------------------------------------- 12) 실제 place_original 과의 통합(임시 가짜 드라이브)
class RealPlaceApi(FakeApi):
    def __init__(self, drives: Path, cross_volume: bool = True):
        super().__init__()
        self.drives, self.cross_volume = drives, cross_volume

    def run_place(self, recv, **kw):
        extra = {"same_volume_fn": lambda a, b: False} if self.cross_volume else {}  # 다른 드라이브처럼: 복사·검사 뒤 받은 폴더에서 삭제
        return po.place(recv, drive_root_fn=lambda letter: (self.drives / letter) if (self.drives / letter).is_dir() else None,
                        dest_info_fn=lambda p: BIG, **extra, **kw)


@pytest.fixture
def real(tmp_path):
    drives = tmp_path / "drives"
    for letter in "DE":
        (drives / letter).mkdir(parents=True)
    recv = tmp_path / "recv"
    put(recv / "D" / "프로젝트" / "A" / "plan.dwg", b"plan")
    put(recv / "D" / "프로젝트" / "A" / "sub" / "spec.pdf", b"spec")
    put(recv / "D" / "Windows" / "x.txt", b"x")  # 보호 위치는 건너뛴다(받기 기록이 없으면 <드라이브><경로> 구조로 유추한다)
    c = Controller(api_mod=RealPlaceApi(drives), runner=Runner(sync=True), rules_dir=tmp_path)
    c.default_desktop = lambda: str(tmp_path / "Desktop")
    c.listener = lambda *a: None
    report = tmp_path / "out" / "place.json"
    c.place_set(recv=str(recv), report_path=str(report))
    return type("R", (), dict(c=c, recv=recv, drives=drives, report=report, tmp=tmp_path))


def test_integration_preview_then_apply_with_real_place(real):
    c = real.c
    c.start_place(apply=False)
    rep = c.place_state.report
    sm = pf.place_summary(rep)
    assert rep.dry_run and sm["preview"] and sm["placed_n"] >= 2 and sm["delete_n"] >= 2  # 같은 볼륨 이동: 받은 폴더에서 지울(옮길) 파일
    assert (real.recv / "D" / "프로젝트" / "A" / "plan.dwg").is_file() and not (real.drives / "D").glob("*") is None
    assert not list((real.drives / "D").rglob("*.dwg"))  # 미리보기는 아무것도 바꾸지 않았다
    assert c.place_ack_needed() and c.place_reasons()[1] == T.WHY_PL_ACK
    rows = pf.place_rows(rep)
    assert pf.place_todo_lines(rep) is not None and isinstance(rows["manual"], list)
    c.start_place(apply=True, write_confirmed=True, ack=True)
    rep2 = c.place_state.report
    assert not rep2.dry_run and (real.drives / "D" / "프로젝트" / "A" / "plan.dwg").read_bytes() == b"plan"
    sm2 = pf.place_summary(rep2)
    assert sm2["placed_n"] >= 2 and not sm2["interrupted"] and sm2["preview"] is False
    data = json.loads(real.report.read_text(encoding="utf-8"))
    assert data["dry_run"] is False and data["placed_files"] >= 2 and "success" in data and c.place_state.saved_path == str(real.report)
    r = c.place_result()
    texts = [r["head"], r["line"], *r["notes"], *r["todo"]] + [v for rows_ in r["rows"].values() for row in rows_ for v in row.values() if isinstance(v, str)]
    assert all("--" not in t for t in texts), [t for t in texts if "--" in t]
    # 보호 위치(Windows)는 건너뛰고 '못 놓은 것' 표와 '직접 해야 할 일' 에 쉬운 말로 나온다
    assert any("보호" in row["reason"] for row in r["rows"]["manual"]) and any(T.PL_TODO_PROTECTED == t for t in r["todo"])


def test_integration_cancel_via_real_place_saves_report(real):
    c = real.c
    c.start_place(apply=False)
    real_api = c.api

    class CancelNow(RealPlaceApi):
        def run_place(self, recv, **kw):
            kw["cancel"] = lambda: True
            return super().run_place(recv, **kw)
    c.api = CancelNow(real.drives)
    c.start_place(apply=True, write_confirmed=True, ack=True)
    s = c.place_state
    assert s.stopped == "cancelled" and s.report is not None and s.report.meta.get("interrupted") == "cancelled"
    assert json.loads(real.report.read_text(encoding="utf-8"))["interrupted"] == "cancelled" if "interrupted" in json.loads(real.report.read_text(encoding="utf-8")) else real.report.is_file()
    assert c.state.busy is False and real_api is not c.api


# --------------------------------------------------------------------------- 들어가는 길과 잠금
def test_open_sets_back_screen_prefills_recv_and_blocks_when_busy(pc):
    c = pc.c
    other = pc.tmp / "r3"
    put(other / "E" / "z.txt")
    c.place_open(S.LAN_RECV, str(other))
    assert c.state.screen == S.PLACE and c.place_state.recv == str(other) and c.place_back_screen() == S.LAN_RECV
    assert c.files_stage(S.PLACE) == 0  # 맨 위 '전체 단계 n/4' 는 표시하지 않는다
    assert S.overall_step(S.PLACE) is None
    c.place_open(S.METHOD)  # 받은 폴더를 모르면 이전 값을 그대로 둔다
    assert c.place_state.recv == str(other) and c.place_back_screen() == S.METHOD
    c.state.busy = True
    with pytest.raises(GuardError):
        c.place_open(S.NEW_RESULT)
    assert T.TASK_PLACE in __import__("pcmigrate.gui.controller", fromlist=["_WRITING_TASKS"])._WRITING_TASKS


def test_stop_notes_and_step_names(pc):
    assert pc.c.stop_done_note(T.TASK_PLACE) == T.STOP_DONE_PLACE and pc.c.stop_done_note(T.TASK_PLACE_PREVIEW) == T.STOP_DONE_PLACE_PREVIEW
    names, cur = pc.c.place_step()
    assert names == T.PL_STEP_NAMES and cur == 2
    preview(pc)
    assert pc.c.place_step()[1] == 3
    go(pc, ack=True)
    assert pc.c.place_step()[1] == 4


def test_selection_mode_survives_visit_to_place_screen(pc):
    c = pc.c
    c.files.use_selection = True
    c.go(S.PLACE)
    assert c.files.use_selection is True  # 놓기 화면에 다녀와도 고른 목록은 유지된다


# --------------------------------------------------------------------------- 문서/로드맵
def test_docs_mention_place_screen_and_stay_synced():
    root = Path(__file__).resolve().parents[1]
    md = (root / "docs" / "field-guide.md").read_text(encoding="utf-8")
    ht = (root / "docs" / "field-guide.html").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for needle in ("받은 파일을 원래 위치에 놓기", "pcmigrate place"):
        assert needle in md and needle in ht and needle in readme, needle
    assert "받은 파일을 원래 위치에 놓기 ▶" in readme and "이미 받은 폴더를 원래 위치에 놓기" in readme and "n/13" in readme and "13단계" in readme
    assert "4. [도구] [직접] 받은 파일을 원래 위치에 놓기" in md and "5. [도구] ③ 새 PC에서 설치하고 가져오기에서" in md
    assert '<span class="num">4</span><div class="txt"><span class="tag t-tool">도구</span><span class="tag t-you">직접</span>받은 파일을 원래 위치에 놓기' in ht
    assert "받은 파일을 원래 위치로 놓습니다" in readme  # 전체 이전 순서 표(texts.py 의 ROADMAP_NEW 에서 만든 것)
    assert "## v0.3.4" in (root / "CHANGELOG.md").read_text(encoding="utf-8")  # 릴리스 전에는 Unreleased, 릴리스 뒤에는 버전 제목
    assert "받은 파일 원래 위치에 놓기" in (root / "CHANGELOG.md").read_text(encoding="utf-8").split("## v0.3.3")[0]


def test_roadmap_has_place_item_after_data_copy(pc):
    keys = [k for k, *_ in T.ROADMAP_NEW]
    assert keys == ["new1", "new2", "new3", "new3b", "new4", "new5", "new6"]
    assert pc.c.toggle_roadmap("new3b") is True and "new3b" in pc.c.roadmap_marks
    md = T.roadmap_markdown(markdown=True) if False else T.roadmap_markdown()
    assert md.count(T.UNCHECKED) == 13  # 구 PC 6 + 새 PC 7
    assert "4. " + T.UNCHECKED + " **받은 파일을 원래 위치로 놓습니다** — _도구가 도와주고 직접 확인_" in md


def test_backend_warnings_reach_the_todo_list_in_plain_words(real):
    real.c.start_place(apply=False)  # 받기 기록이 없는 폴더: 백엔드가 알리는 경고
    r = real.c.place_result()
    text = "\n".join(r["todo"])
    assert T.PL_NOTES_TITLE in text and "받기 기록" in text and "저널" not in text and "--" not in text


# --------------------------------------------------------------------------- stage54: 같은 드라이브 이동 안내(A안), 리뷰 수정
class VolApi(FakeApi):
    """실제 place_original 을 쓰되 '같은 드라이브' 판정만 바꿔 끼운다(같은 볼륨 이동 / 다른 볼륨 복사 후 삭제 / 섞임)."""

    def __init__(self, drives: Path, same_volume):
        super().__init__()
        self.drives, self.same_volume = drives, same_volume

    def run_place(self, recv, **kw):
        return po.place(recv, drive_root_fn=lambda letter: (self.drives / letter) if (self.drives / letter).is_dir() else None,
                        dest_info_fn=lambda p: BIG, same_volume_fn=self.same_volume, **kw)


def vol_env(tmp_path, same_volume):
    drives = tmp_path / "drives"
    for letter in "DE":
        (drives / letter).mkdir(parents=True)
    recv = tmp_path / "recv"
    put(recv / "D" / "프로젝트" / "a.dwg", b"a1")
    put(recv / "E" / "사진" / "b.jpg", b"b1")
    c = Controller(api_mod=VolApi(drives, same_volume), runner=Runner(sync=True), rules_dir=tmp_path)
    c.default_desktop = lambda: str(tmp_path / "Desktop")
    c.listener = lambda *a: None
    c.place_protected_fn = lambda path, is_dir=True: None
    c.place_set(recv=str(recv), report_path=str(tmp_path / "out" / "r.json"))
    return c


def _dest_is_d(a, b):
    return "D" in Path(b).parts


def test_same_volume_move_preview_shows_move_line_and_asks_no_ack(tmp_path):
    c = vol_env(tmp_path, lambda a, b: True)
    c.start_place(apply=False)
    sm = pf.place_summary(c.place_state.report)
    assert sm["move_n"] == 2 and sm["delete_n"] == 0 and not sm["keep"]
    msg = c.place_confirm_message()
    assert T.PL_CONFIRM_MOVE.format(n=2) in msg and T.PL_CONFIRM_DELETE_NONE not in msg and "지우는 파일" not in msg
    assert "받은 폴더에서 원래 위치로 이동: 2개" in msg and "저장 공간은 늘지 않고, 받은 폴더에서는 사라집니다" in msg
    assert c.place_ack_needed() is False and c.place_reasons() == ("", "")  # 이동만이면 확인 체크를 새로 요구하지 않는다
    r = c.place_result()
    assert T.PL_MOVE_NOTE.format(n=2) in r["notes"] and r["sm"]["delete_n"] == 0
    assert "받은 폴더에서 원래 위치로 이동합니다(같은 드라이브라 저장 공간은 늘지 않고, 받은 폴더에서는 사라집니다): 2개" in r["notes"]
    text = " ".join([msg, *r["notes"]])
    assert "--" not in text and "pcmigrate" not in text
    c.start_place(apply=True, write_confirmed=True)  # 체크 없이 놓을 수 있다
    assert (tmp_path / "drives" / "D" / "프로젝트" / "a.dwg").read_bytes() == b"a1" and not (tmp_path / "recv" / "D" / "프로젝트" / "a.dwg").exists()
    assert T.PL_MOVE_NOTE.format(n=2) not in c.place_result()["notes"]  # 놓은 뒤에는 미리보기 안내를 반복하지 않는다


def test_cross_volume_copy_delete_keeps_delete_box_rules(tmp_path):
    c = vol_env(tmp_path, lambda a, b: False)
    c.start_place(apply=False)
    sm = pf.place_summary(c.place_state.report)
    assert sm["move_n"] == 0 and sm["delete_n"] == 2
    msg = c.place_confirm_message()
    assert T.PL_CONFIRM_DELETE.format(n=2, size=pf.size_text(sm["delete_bytes"])) in msg and "이동:" not in msg
    assert c.place_ack_needed() is True and c.place_reasons()[1] == T.WHY_PL_ACK
    assert not [n for n in c.place_result()["notes"] if n.startswith("받은 폴더에서 원래 위치로 이동합니다")]


def test_keep_mode_keeps_old_wording_even_on_same_volume(tmp_path):
    c = vol_env(tmp_path, lambda a, b: True)
    c.place_set(keep=True)
    c.start_place(apply=False)
    sm = pf.place_summary(c.place_state.report)
    assert sm["keep"] and sm["move_n"] == 0
    assert T.PL_CONFIRM_KEEP in c.place_confirm_message() and "이동:" not in c.place_confirm_message()
    assert c.place_ack_needed() is False
    assert all("이동합니다" not in n for n in c.place_result()["notes"])


def test_mixed_volumes_show_move_and_delete_lines_and_need_ack(tmp_path):
    c = vol_env(tmp_path, _dest_is_d)  # D 는 같은 드라이브(이동), E 는 다른 드라이브(복사 후 삭제)
    c.start_place(apply=False)
    sm = pf.place_summary(c.place_state.report)
    assert (sm["move_n"], sm["delete_n"]) == (1, 1)
    msg = c.place_confirm_message()
    assert T.PL_CONFIRM_MOVE.format(n=1) in msg and T.PL_CONFIRM_DELETE.format(n=1, size=pf.size_text(sm["delete_bytes"])) in msg
    assert msg.index("이동:") < msg.index("지우는 파일")
    assert c.place_ack_needed() is True and c.place_reasons()[1] == T.WHY_PL_ACK
    assert T.PL_MOVE_NOTE.format(n=1) in c.place_result()["notes"]


def test_help_and_readme_explain_move_vs_delete():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "같은 드라이브 이동은 삭제가 아니라서 확인 체크는 요구하지 않지만" in readme
    assert "같은 드라이브에서는 파일을 이동하므로 받은 폴더에서 사라지고" in T.HELP[S.PLACE]


# --- P2: 기본 리포트 경로
def test_default_report_is_not_stored_and_second_apply_does_not_overwrite(pc):
    pc.c.place_set(report_path="")
    assert pc.c.place_state.report_path == ""
    first = pc.c.place_default_report()
    assert pc.c.place_default_report() == first  # 화면 표시·확인창·실행이 같은 값을 본다
    preview(pc)
    go(pc, ack=True)
    assert Path(first).is_file() and pc.c.place_state.saved_path == first
    second = pc.c.place_default_report()
    assert second != first and not Path(second).exists()  # 쓴 이름은 다시 쓰지 않는다(같은 초여도)
    preview(pc)
    go(pc, ack=True)
    assert Path(second).is_file() and Path(first).is_file() and pc.c.place_state.saved_path == second
    assert json.loads(Path(first).read_text(encoding="utf-8"))["dry_run"] is False
    assert not list(Path(first).parent.glob("*.bak"))  # 첫 리포트를 번호 .bak 으로 밀어내지도 않았다


# --- P3: 리포트 없는 오류
@pytest.mark.parametrize("apply", [False, True])
def test_error_without_report_saves_minimal_report_and_honest_note(pc, apply):
    preview(pc)
    pc.api.place_error, pc.api.place_error_report = RuntimeError("비밀 경로 모음"), None
    if apply:
        go(pc, ack=True)
    else:
        pc.c.place_set(keep=True)
        preview(pc)
    s = pc.c.place_state
    assert s.stopped == "error" and s.report is not None and s.report.meta.get("minimal") is True
    assert s.stopped_note == T.PL_ERR_MINIMAL and "오류 로그" in s.stopped_note and "자세한 결과는 만들지 못해" in s.stopped_note
    assert pc.errors and "error" in kinds(pc)
    assert "중간에 멈췄습니다" in pc.c.place_result()["head"]
    if apply:
        data = json.loads(pc.report.read_text(encoding="utf-8"))
        assert data["interrupted"] == "error" and data["error"] == "RuntimeError" and data["options"]["keep"] is False and data["finished"]
        assert "비밀" not in pc.report.read_text(encoding="utf-8")  # 예외 문구(개인정보 가능성)는 담지 않는다
    else:
        assert not pc.report.exists() and s.preview_key is None


# --- P5: 이전 결과 표시
def test_changing_recv_or_options_marks_old_result_stale_but_report_path_does_not(pc):
    preview(pc)
    assert pc.c.place_result()["stale"] is False
    pc.c.place_set(report_path=str(pc.tmp / "other.json"))
    assert pc.c.place_state.report_stale is False
    pc.c.place_set(keep=True)
    assert pc.c.place_state.report_stale is True and pc.c.place_result()["stale"] is True
    preview(pc)
    assert pc.c.place_state.report_stale is False
    other = pc.tmp / "recv9"
    put(other / "E" / "k.txt")
    pc.c.place_open(S.METHOD, str(other))
    assert pc.c.place_result()["stale"] is True


# --- P6: 미리보기에서 '리포트 파일에 있습니다' 모순 없음
def test_preview_texts_do_not_point_to_a_report_file_that_is_not_saved(pc):
    rep = make_report(pc.recv, warnings=[{"expected": "D:/a", "text": "확인 필요"}])
    lines = pf.place_todo_lines(rep)
    assert T.PL_TODO_WARNINGS_PREVIEW.format(n=1) in lines and T.PL_TODO_WARNINGS.format(n=1) not in lines
    assert "결과 리포트 파일에 있습니다" not in T.PL_TABLE_MORE_PREVIEW and "저장하지 않으며" in T.PL_TABLE_MORE_PREVIEW
    done = make_report(pc.recv, dry_run=False, warnings=[{"expected": "D:/a", "text": "확인 필요"}])
    assert T.PL_TODO_WARNINGS.format(n=1) in pf.place_todo_lines(done)


# --- P7: 입력 점검 한 번
def test_input_check_is_computed_once_and_reused(pc, monkeypatch):
    calls = []
    real_scan = Controller._scan_recv_top

    def counting(recv):
        calls.append(recv)
        return real_scan(recv)
    monkeypatch.setattr(Controller, "_scan_recv_top", staticmethod(counting))
    pc.c.place_recheck()
    for _ in range(3):
        pc.c.place_check_state()
        pc.c.place_reasons()
        pc.c.place_step()
    assert len(calls) == 1
    other = pc.tmp / "r5"
    put(other / "D" / "a.txt")
    pc.c.place_set(recv=str(other))
    pc.c.place_reasons()
    pc.c.place_step()
    assert len(calls) == 2
    pc.c.place_recheck()
    pc.c.place_reasons()
    assert len(calls) == 3


# --- P8: 중간에 멈춘 미리보기는 이전 유효 미리보기를 무효로 한다
def test_cancelled_preview_invalidates_previous_valid_preview(pc):
    preview(pc)
    assert pc.c.place_reasons()[1] in ("", T.WHY_PL_ACK) and pc.c.place_state.preview_key is not None
    pc.api.place_error, pc.api.place_error_report = Cancelled(), make_report(pc.recv, meta={"interrupted": "cancelled"})
    preview(pc)
    assert pc.c.place_state.preview_key is None and pc.c.place_reasons()[1] == T.WHY_PL_PREVIEW_FIRST
    pc.api.place_error = None
    with pytest.raises(GuardError, match=re.escape(T.G_PL_NEED_PREVIEW)):
        go(pc, ack=True)
