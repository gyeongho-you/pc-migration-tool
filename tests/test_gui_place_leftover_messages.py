"""받은 파일 놓기 화면: 받은 폴더에 남은 것 안내(지워도 되는지), 요약 줄, 따옴표 경로, 미리보기 중 옵션 변경, 오류 리포트 개인정보(stage55).

모두 실제 place_original.place 와 연결하고(same_volume_fn/drive_root_fn/dest_info_fn 주입) 임시 폴더의 가짜 드라이브만 쓴다.
"""
import json
import os
from pathlib import Path

import pytest

from pcmigrate import copier
from pcmigrate import place_original as po
from pcmigrate.fs import DestInfo
from pcmigrate.gui import place_flow as pf
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi
from test_gui_place_controller import BIG, put


class Api(FakeApi):
    def __init__(self, drives: Path, same: bool, info=BIG, cancel_when=None):
        super().__init__()
        self.drives, self.same, self.info, self.cancel_when, self.calls = drives, same, info, cancel_when, 0

    def run_place(self, recv, **kw):
        self.calls += 1
        if self.cancel_when is not None and kw["apply"]:
            kw["cancel"] = self.cancel_when
        return po.place(recv, drive_root_fn=lambda letter: (self.drives / letter) if (self.drives / letter).is_dir() else None,
                        dest_info_fn=lambda p: self.info, same_volume_fn=lambda a, b: self.same, **kw)


def make(tmp_path, same=True, files=3, **opts):
    drives = tmp_path / "drives"
    (drives / "D").mkdir(parents=True)
    recv = tmp_path / "recv"
    for i in range(files):
        put(recv / "D" / "p" / f"f{i}.dwg", b"new%d" % i)
    c = Controller(api_mod=Api(drives, same), runner=Runner(sync=True), rules_dir=tmp_path)
    c.default_desktop = lambda: str(tmp_path / "Desktop")
    c.listener = lambda *a: None
    c.place_protected_fn = lambda path, is_dir=True: None
    c.place_set(recv=str(recv), report_path=str(tmp_path / "out" / "r.json"), **opts)
    return c, drives, recv


def run(c, ack=True):
    c.start_place(apply=False)
    try:
        c.start_place(apply=True, write_confirmed=True, ack=ack)
    except Exception:
        pass
    return c.place_result()


def alltext(r):
    return "\n".join([r["head"], r["line"], *r["notes"], *r["todo"]])


# --------------------------------------------------------------------------- Q1: 남은 파일을 지우라는 말을 하지 않는다
def test_refused_overwrite_says_do_not_delete_and_new_data(tmp_path):
    c, drives, recv = make(tmp_path, no_overwrite=True)
    put(drives / "D" / "p" / "f0.dwg", b"old")
    r = run(c)
    t = alltext(r)
    assert r["sm"]["refused_n"] == 1 and r["sm"]["remaining_files"] == 1
    assert T.PL_REMAINING_REFUSED.format(n=1) in r["notes"] and "새 데이터일 수 있" in t and "지우지 마세요" in t
    assert "직접 지워 주세요" not in t and "정리가 끝났습니다" not in t
    assert T.PL_TODO_NOOVERWRITE in r["todo"] and T.PL_TODO_UNPLACED not in r["todo"] and T.PL_TODO_LEFTOVER not in r["todo"]
    assert (drives / "D" / "p" / "f0.dwg").read_bytes() == b"old" and (recv / "D" / "p" / "f0.dwg").read_bytes() == b"new0"


def test_cancel_says_unplaced_files_remain_do_not_delete(tmp_path):
    c, drives, recv = make(tmp_path)
    c.api.cancel_when = lambda: (drives / "D" / "p" / "f0.dwg").exists() or (drives / "D" / "p" / "f1.dwg").exists()
    r = run(c)
    sm = r["sm"]
    assert c.place_state.stopped == "cancelled" and sm["interrupted"] == "cancelled" and sm["remaining_files"] >= 1
    n = sm["remaining_files"]
    assert T.PL_REMAINING_STOPPED.format(files=n, size=pf.size_text(sm["remaining_bytes"])) in r["notes"]
    t = alltext(r)
    assert "아직 놓지 않은 파일이" in t and "지우지 마세요" in t and "미리보기부터 다시 하면 이어서 놓습니다" in t
    assert "직접 지워 주세요" not in t and "지워도 됩니다" not in t
    assert T.PL_TODO_UNPLACED in r["todo"]
    # Q2: 중단된 요약 줄은 '✔' 로 시작하지 않고 '일부만' 문구를 쓴다
    assert not r["line"].startswith("✔") and "중단됨" in r["line"] and "일부만 처리" in r["line"]
    assert r["level"] == "danger"


def test_cancelled_preview_summary_says_partial_check(tmp_path):
    c, drives, recv = make(tmp_path)
    c.api.cancel_when = None
    real = c.api.run_place

    def cancel_run(recv_, **kw):
        kw["cancel"] = lambda: True
        return real(recv_, **kw)
    c.api.run_place = cancel_run
    c.start_place(apply=False)
    r = c.place_result()
    assert c.place_state.stopped == "cancelled" and not r["line"].startswith("✔") and "일부만 확인" in r["line"]


def test_failure_says_unplaced_remain_and_does_not_repeat_retry_note(tmp_path, monkeypatch):
    c, drives, recv = make(tmp_path)
    real = copier._install

    def inst(tmp, dst, res, lp=os.fspath):
        if Path(tmp).name.startswith("f1.dwg"):
            raise PermissionError(5, "denied", str(tmp))
        return real(tmp, dst, res, lp)
    monkeypatch.setattr(copier, "_install", inst)
    r = run(c)
    sm = r["sm"]
    assert sm["failed_n"] == 1 and sm["remaining_files"] == 1
    t = alltext(r)
    assert any(n.startswith("아직 놓지 않은 파일이 1개") and "지우지 마세요" in n for n in r["notes"])
    assert T.PL_RETRY_NOTE not in r["notes"]  # 같은 화면에서 '이어서 처리' 안내를 한 가지로
    assert "직접 지워 주세요" not in t and "지워도 됩니다" not in t and T.PL_TODO_UNPLACED in r["todo"]
    assert "✖" in r["line"] or "실패 1개" in r["line"]


def test_normal_finish_with_identical_file_says_cleanup_done(tmp_path):
    c, drives, recv = make(tmp_path, same=False)
    put(drives / "D" / "p" / "f0.dwg", b"new0")  # 이미 같은 내용: 받은 폴더의 사본도 정리된다
    r = run(c)
    sm = r["sm"]
    assert sm["same_n"] == 1 and sm["remaining_files"] == 0 and sm["has_remaining"] and not sm["interrupted"] and not sm["failed_n"]
    assert r["notes"].count(T.PL_REMAINING_CLEAN) == 1 and "지워도 됩니다" in alltext(r)
    assert "지우지 마세요" not in alltext(r)
    assert T.PL_TODO_UNPLACED not in r["todo"] and T.PL_TODO_LEFTOVER not in r["todo"]
    assert r["line"].startswith("✔")


def test_source_delete_failure_todo_keeps_check_then_delete_wording(tmp_path, monkeypatch):
    c, drives, recv = make(tmp_path, same=False)
    real = os.unlink

    def unlink(p, *a, **k):
        if str(p).endswith("f1.dwg") and "recv" in str(p):
            raise PermissionError(5, "denied", str(p))
        return real(p, *a, **k)
    monkeypatch.setattr(os, "unlink", unlink)
    r = run(c)
    assert r["sm"]["remaining_files"] == 1 and r["sm"]["manual_n"] == 1
    assert T.PL_TODO_LEFTOVER in r["todo"] and "대상에 온전히 있는지 확인한 뒤" in T.PL_TODO_LEFTOVER and "지우지 마세요" in T.PL_TODO_LEFTOVER
    assert T.PL_TODO_UNPLACED not in r["todo"]  # 복사는 끝났으므로 '아직 놓지 않은 파일' 이 아니다
    assert not any("직접 지워 주세요" in n for n in r["notes"])


# --------------------------------------------------------------------------- Q2: 놓을 수 있는 파일이 없는 미리보기
def test_no_space_folder_skip_preview_is_not_a_green_zero(tmp_path):
    c, drives, recv = make(tmp_path, same=False)  # 다른 드라이브로 복사해야 공간이 필요하다
    c.api.info = DestInfo("x", "fixed", "NTFS", 1, 1, "D:")  # 여유 공간 1바이트: 폴더 통째로 건너뜀
    c.start_place(apply=False)
    r = c.place_result()
    sm = r["sm"]
    assert sm["placed_n"] == 0 and sm["manual_n"] >= 1 and not sm["interrupted"]
    assert r["line"] == T.PL_SUM_NONE_PREVIEW.format(n=sm["manual_n"]) and "✔" not in r["line"] and r["line"].startswith("⚠")
    assert "놓을 파일 0개" not in r["line"]
    assert r["level"] == "warn"


# --------------------------------------------------------------------------- Q4: 거부 예정/이미 같은 파일은 놓을 파일이 아니다
def test_preview_excludes_refused_and_identical_from_place_count_and_confirm(tmp_path):
    c, drives, recv = make(tmp_path, files=5, no_overwrite=True, same=False)
    put(drives / "D" / "p" / "f0.dwg", b"old")  # 다른 내용: 거부 예정
    put(drives / "D" / "p" / "f1.dwg", b"new1")  # 같은 내용
    c.start_place(apply=False)
    rep = c.place_state.report
    assert rep.total_files == 5  # 백엔드 숫자에는 거부/같은 파일이 들어 있다
    sm = pf.place_summary(rep)
    assert sm["placed_n"] == 3 and sm["refused_n"] == 1 and sm["same_n"] == 1 and not sm["bytes_exact"]
    assert sm["placed_n"] == sum(rep.counts.get(k, 0) for k in ("would_move", "would_copy", "would_overwrite"))
    r = c.place_result()
    assert r["line"].startswith("✔ 놓을 파일 3개") and "건너뛸 것 1개" in r["line"] and "덮어쓰기를 거부해 건너뛸 것 1개" in r["line"]
    msg = c.place_confirm_message()
    assert "· 놓을 파일: 3개 (받은 파일 전체" in msg and "중), 건너뛸 것 2개" in msg
    # 거부가 없고 건너뛸 것도 없으면 예전과 같은 문구
    c2, _d, _r = make(tmp_path / "b", files=2, same=False)
    c2.start_place(apply=False)
    assert "건너뛸 것" not in c2.place_confirm_message() and c2.place_result()["line"].startswith("✔ 놓을 파일 2개 (")


# --------------------------------------------------------------------------- Q3: 따옴표 붙은 경로
@pytest.mark.parametrize("fmt", ['"{}"', "'{}'", '  "{}"  ', '"{}" '])
def test_quoted_paths_are_accepted_for_recv_gather_and_report(tmp_path, fmt):
    c, drives, recv = make(tmp_path)
    g, rep = tmp_path / "모음", tmp_path / "out2" / "x.json"
    c.place_set(recv=fmt.format(recv), gather_on=True, gather_dir=fmt.format(g), report_path=fmt.format(rep))
    s = c.place_state
    assert s.recv == str(recv) and s.gather_dir == str(g) and s.report_path == str(rep)
    chk = c.check_place_inputs(fmt.format(recv), [], True, fmt.format(g), fmt.format(rep))
    assert chk.ok and chk.gather_folder.rstrip("\\") == str(g)
    c.start_place(apply=False)
    assert c.place_state.report is not None


def test_place_open_cleans_quoted_recv(tmp_path):
    c, drives, recv = make(tmp_path)
    c.place_set(recv="")
    c.place_open("start", recv=f'"{recv}"')
    assert c.place_state.recv == str(recv)


# --------------------------------------------------------------------------- Q5: 미리보기 도중 옵션을 바꾸면 '이전 결과'
def test_option_changed_during_preview_marks_result_stale_and_blocks_place(tmp_path):
    c, drives, recv = make(tmp_path)
    real = c.api.run_place

    def change_midway(recv_, **kw):
        c.place_set(no_overwrite=True)  # 실행 도중(작업 스레드 대신 여기서) 사용자가 옵션을 바꾼 것과 같다
        return real(recv_, **kw)
    c.api.run_place = change_midway
    c.start_place(apply=False)
    s = c.place_state
    assert s.report is not None and s.report_stale is True and c.place_result()["stale"] is True
    assert c.place_reasons()[1] == T.WHY_PL_OPTIONS  # 놓기는 막혀 있고 이유도 '옵션이 바뀜'
    with pytest.raises(GuardError):
        c.start_place(apply=True, write_confirmed=True, ack=True)


def test_option_changed_during_apply_marks_stopped_result_stale(tmp_path):
    c, drives, recv = make(tmp_path)
    c.start_place(apply=False)
    real = c.api.run_place

    def change_midway(recv_, **kw):
        c.place_set(keep=True)
        return real(recv_, **kw)
    c.api.run_place = change_midway
    c.start_place(apply=True, write_confirmed=True, ack=True)
    assert c.place_state.report_stale is True


def test_unchanged_options_are_not_stale(tmp_path):
    c, drives, recv = make(tmp_path, same=False)
    c.start_place(apply=False)
    assert c.place_state.report_stale is False and c.place_reasons()[1] == T.WHY_PL_ACK


# --------------------------------------------------------------------------- Q6: 놓을 파일이 없으면 직접 호출도 막는다
def test_apply_called_directly_is_guarded_when_nothing_to_place(tmp_path):
    c, drives, recv = make(tmp_path, files=1)
    (recv / "D" / "p" / "f0.dwg").unlink()  # 받은 폴더에 드라이브 폴더만 있고 파일이 없다
    c.start_place(apply=False)
    sm = pf.place_summary(c.place_state.report)
    assert sm["total_files"] == 0 and sm["gather_n"] == 0 and c.place_reasons()[1] == T.WHY_PL_NOTHING
    calls = c.api.calls
    with pytest.raises(GuardError) as ei:
        c.start_place(apply=True, write_confirmed=True, ack=True)
    assert str(ei.value) == T.WHY_PL_NOTHING and c.api.calls == calls


# --------------------------------------------------------------------------- Q7: 오류 리포트에 사용자 이름이 남지 않는다
def test_error_report_does_not_keep_user_home_in_meta_error(tmp_path, monkeypatch):
    fake_home = "C:\\Users\\가짜사용자"
    monkeypatch.setenv("USERPROFILE", fake_home)
    monkeypatch.setenv("HOME", fake_home)
    monkeypatch.setattr(os.path, "expanduser", lambda p: fake_home if p == "~" else p)
    c, drives, recv = make(tmp_path)
    c.start_place(apply=False)

    def boom(a, b):
        raise RuntimeError(f"예상 못 한 오류: {fake_home}\\Documents\\비밀.dwg 를 읽지 못함")
    c.api.same = True
    real = c.api.run_place

    def run_with_boom(recv_, **kw):
        return po.place(recv_, drive_root_fn=lambda letter: drives / letter, dest_info_fn=lambda p: BIG, same_volume_fn=boom, **kw)
    c.api.run_place = run_with_boom
    c.record_error = lambda exc, where, tb=None: str(tmp_path / "errlog.txt")
    try:
        c.start_place(apply=True, write_confirmed=True, ack=True)
    except Exception:
        pass
    data = json.loads((tmp_path / "out" / "r.json").read_text(encoding="utf-8"))
    assert data["interrupted"] == "error" and "RuntimeError" in data["error"]
    raw = (tmp_path / "out" / "r.json").read_text(encoding="utf-8")
    assert "가짜사용자" not in raw and "%USERPROFILE%" in data["error"]
    assert "가짜사용자" not in c.place_state.report.meta["error"]
    _ = real
