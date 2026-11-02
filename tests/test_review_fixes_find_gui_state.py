"""stage79: 새 리뷰어(파일 찾기·GUI 상태 흐름) 지적 수정의 재현 시험. 수정 전에는 실패하도록 지적된 상황을 그대로 재현한다.

임시 폴더와 가짜 시계/실행기만 쓴다(실제 드라이브·사용자 폴더·AppData 는 읽거나 쓰지 않는다).
"""
import json
import os
import threading
import time
from pathlib import Path

import pytest

from pcmigrate import cli, data_api, errorlog, find_data as fd
from pcmigrate.api import ApiError
from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T
from pcmigrate.gui.controller import Controller, GuardError
from pcmigrate.gui.runner import Runner

from test_gui_controller import FakeApi
from test_gui_my_files_controller import FakeData, FakeLan


def put(p: Path, data: bytes = b"x" * 10) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def tick_clock(step=1.0):
    t = {"now": 0.0}

    def clock():
        t["now"] += step
        return t["now"]
    return clock


def names(res, root):
    return sorted(Path(p.path).relative_to(root).as_posix() for p in res.projects)


# ------------------------------------------------------------------ [높음] 시간 상한에서도 사용자 기본 폴더는 훑는다
def big_root_and_docs(tmp_path):
    root = tmp_path / "root"
    for i in range(30):
        put(root / f"P{i:02d}" / "a.dwg")
    docs = tmp_path / "Documents"
    put(docs / "plan.dwg")
    return root, docs


def test_time_limit_still_scans_user_folders_first(tmp_path):
    root, docs = big_root_and_docs(tmp_path)
    res = fd.find_data([root], user_folders={"documents": str(docs)}, exclude_paths=[], time_limit=60, clock=tick_clock())
    assert res.complete is False and res.stop_reason == "time"  # 루트 훑기는 시간 안에 못 끝났다
    assert len(res.user_folders) >= 1 and res.user_folders[0].label == "documents"  # 수정 전: 루트 뒤라서 0개
    assert res.user_folders_incomplete == []


def test_user_folder_not_finished_is_reported_in_notes_and_summary(tmp_path):
    docs = tmp_path / "Documents"
    for i in range(30):
        put(docs / f"d{i:02d}" / "a.dwg")
    res = fd.find_data([], user_folders={"documents": str(docs)}, exclude_paths=[], time_limit=20, clock=tick_clock())
    assert res.complete is False and res.user_folders_incomplete == ["documents"]
    assert any("사용자 기본 폴더(문서)" in n and "시간이 부족" in n for n in res.ext_notes)
    assert any("시간이 부족" in ln for ln in fd.summary_lines(res)) and res.to_dict()["user_folders_incomplete"] == ["documents"]


def test_without_time_limit_order_and_result_unchanged(tmp_path):
    root, docs = big_root_and_docs(tmp_path)
    res = fd.find_data([root], user_folders={"documents": str(docs)}, exclude_paths=[], clock=lambda: 0.0)
    assert res.complete and len(res.projects) == 30 and len(res.user_folders) == 1
    assert "user_folders_incomplete" not in res.to_dict()  # 값이 없으면 결과 모양은 이전과 같다


def test_roots_only_with_time_limit_is_unchanged(tmp_path):
    root, _docs = big_root_and_docs(tmp_path)
    res = fd.find_data([root], user_folders={}, exclude_paths=[], time_limit=60, clock=tick_clock())
    assert res.complete is False and not res.user_folders_incomplete and not res.ext_notes


# ------------------------------------------------------------------ [중간] 작업 중 화면 이동/정크 토글 차단
@pytest.fixture
def env(tmp_path):
    c = Controller(api_mod=FakeApi(), runner=Runner(sync=True), rules_dir=tmp_path)
    c.data_api, c.lan_api = FakeData(), FakeLan(tmp_path)
    c.find_data_start()
    return c


def test_go_and_junk_blocked_while_busy_keep_selection(env):
    c = env
    c.set_data_selected("C:\\Proj\\Alpha", True)
    c.enter_selection_transfer(T.MODE_EXTERNAL)
    sig, src = c.files.signature, c.state.transfer_src
    c.state.busy = True
    for screen in (S.METHOD, S.FIND, S.START):
        with pytest.raises(GuardError) as e:
            c.go(screen)
        assert T.G_BUSY in str(e.value)
    with pytest.raises(GuardError):
        c.set_data_junk(True)
    assert c.files.use_selection and c.files.signature == sig and c.state.transfer_src == src and not c.files.junk
    assert c.state.screen == S.TRANSFER
    c.state.busy = False
    c.go(S.METHOD)  # 끝나면 이동할 수 있다
    assert c.state.screen == S.METHOD


def test_go_same_screen_while_busy_is_allowed(env):
    env.state.busy = True
    env.go(env.state.screen)  # 같은 화면으로의 '이동'(다시 그리기)은 막지 않는다


def test_async_runner_midway_blocks_navigation(tmp_path):
    api = FakeApi()
    api.transfer_wait = threading.Event()
    c = Controller(api_mod=api, runner=Runner(), rules_dir=tmp_path)  # 비동기 실행기
    src = tmp_path / "src"
    src.mkdir()
    c.state.transfer_src, c.state.transfer_dst = str(src), str(tmp_path / "dst")
    c.go(S.TRANSFER)
    c.start_transfer(dry_run=True)  # 백그라운드로 도는 중
    try:
        assert c.state.busy
        with pytest.raises(GuardError):
            c.go(S.FIND)
        with pytest.raises(GuardError):
            c.set_data_junk(True)
        assert c.state.screen == S.TRANSFER and c.state.transfer_src == str(src)
    finally:
        api.transfer_wait.set()
        for _ in range(200):
            c.runner.pump()
            if not c.state.busy:
                break
            time.sleep(0.02)
    assert not c.state.busy
    c.go(S.FIND)


def test_app_level_guard_dialog_text_reuses_existing_message():
    assert T.G_BUSY  # 화면은 기존 안내 대화상자(GUARD_TITLE)로 이 문구를 보여 준다(새 문구 없음)


# ------------------------------------------------------------------ [중간] 스레드를 시작하지 못해도 busy 가 풀린다
def test_thread_start_failure_releases_busy_and_next_task_runs(tmp_path, monkeypatch):
    c = Controller(api_mod=FakeApi(), runner=Runner(), rules_dir=tmp_path)
    c.data_api = FakeData()
    events = []
    c.listener = lambda kind, payload=None: events.append(kind)
    real_start = threading.Thread.start

    def boom(self):
        raise RuntimeError("can't start new thread")
    with monkeypatch.context() as m:
        m.setattr(threading.Thread, "start", boom)
        c.find_data_start()
    assert not c.state.busy and not c.runner.busy and "error" in events  # 수정 전: 둘 다 True 로 영구히 남음
    assert c.last_error is not None
    c.find_data_start()  # 다음 작업이 시작된다
    for _ in range(300):
        c.runner.pump()
        if not c.state.busy:
            break
        time.sleep(0.02)
    assert not c.state.busy and c.files.result is not None


# ------------------------------------------------------------------ [중간] Logs/Cache 이름의 자료 폴더
def test_logs_folder_with_data_files_is_kept_and_pure_logs_excluded(tmp_path):
    root = tmp_path / "pc"
    put(root / "Survey" / "Logs" / "scan1.las")
    put(root / "Survey" / "Logs" / "run.log")
    put(root / "Plain" / "Logs" / "a.log")
    put(root / "Plain" / "Logs" / "b.tmp")
    put(root / "Text" / "Logs" / "notes.txt")  # 등급 C(txt)만 있는 로그 폴더는 계속 제외
    put(root / "Web" / "Cache" / "thumb.dwg")
    res = fd.find_data([root], user_folders={}, exclude_paths=[], clock=lambda: 0.0)
    found = names(res, root)
    assert "Survey/Logs" in found and "Web/Cache" in found  # 수정 전: 둘 다 이름만으로 빠짐
    assert not any(n.startswith(("Plain", "Text")) for n in found)
    reasons = {Path(e["path"]).relative_to(root).as_posix(): e["reason"] for e in res.excluded_examples}
    assert reasons.get("Plain/Logs") == "재생성" and reasons.get("Text/Logs") == "재생성"  # 제외 목록에는 이유가 남는다


def test_logs_folder_with_user_added_ext_is_kept(tmp_path):
    root = tmp_path / "pc"
    put(root / "Drone" / "Logs" / "m.zep")
    from pcmigrate import find_exts as fx
    cfg = fx.add(fx.ExtConfig(), ".zep", "기타").cfg
    res = data_api.run_find_data([root], user_folders={}, exclude_paths=[], ext_config=cfg, clock=lambda: 0.0)
    assert "Drone/Logs" in names(res, root)
    assert not fd.find_data([root], user_folders={}, exclude_paths=[]).projects  # 기본 표에서는 .zep 이 자료가 아니다


def test_node_modules_and_git_stay_hard_excluded(tmp_path):
    root = tmp_path / "pc"
    put(root / "A" / "node_modules" / "x.dwg")
    put(root / "A" / ".git" / "y.dwg")
    put(root / "A" / "keep.dwg")
    res = fd.find_data([root], user_folders={}, exclude_paths=[])
    assert names(res, root) == ["A"] and res.projects[0].categories["design"]["files"] == 1


# ------------------------------------------------------------------ [중간] 겹치는 roots
def test_overlapping_roots_do_not_double_count(tmp_path):
    w = tmp_path / "W"
    put(w / "A" / "B" / "x.dwg")
    put(w / "A" / "B" / "y.dwg")
    put(w / "C" / "z.dwg")
    single = fd.find_data([w], user_folders={}, exclude_paths=[], clock=lambda: 0.0)
    for roots in ([w, w / "A"], [w / "A", w], [w, w], [str(w).upper() + os.sep, str(w / "A").lower()], [w, str(w / "A") + os.sep]):
        res = fd.find_data(roots, user_folders={}, exclude_paths=[], clock=lambda: 0.0)
        assert names(res, w) == names(single, w) and res.files_seen == single.files_seen, roots  # 수정 전: A\B 후보 2번, 합계 어긋남
        assert [d["files"] for d in res.drives] == [d["files"] for d in single.drives]
        assert sum(p.total_files for p in res.projects) == sum(p.total_files for p in single.projects)


def test_non_overlapping_roots_kept(tmp_path):
    a, b = tmp_path / "A", tmp_path / "AB"  # 접두 문자열은 같지만 다른 폴더
    put(a / "x" / "1.dwg")
    put(b / "x" / "2.dwg")
    res = fd.find_data([a, b], user_folders={}, exclude_paths=[])
    assert len(res.projects) == 2


# ------------------------------------------------------------------ [낮음]
def test_dnd_deliver_logs_errors_instead_of_swallowing(tmp_path):
    from pcmigrate.gui import dnd_win

    def boom(paths):
        raise ValueError("handler failed")
    hook = dnd_win.DropHook(None, boom)
    hook.deliver(["C:\\x"])  # 예외는 밖으로 나가지 않는다
    last = errorlog.last_error()
    assert last is not None and "handler failed" in last[1] and "끌어다 놓기" in last[1]  # 수정 전: 아무 기록 없음


def test_summary_drive_label_has_single_colon(tmp_path):
    root = tmp_path / "pc"
    put(root / "A" / "x.dwg")
    res = fd.find_data([root], user_folders={}, exclude_paths=[])
    res.drives[0]["drive"] = "C:"
    lines = fd.summary_lines(res)
    assert any(ln.startswith("- 드라이브 C: 파일") for ln in lines) and not any("C::" in ln for ln in lines)


def test_output_selection_inside_scanned_user_folder_is_refused(tmp_path):
    docs = tmp_path / "Documents"
    put(docs / "a.dwg")
    out = docs / "selection.json"
    with pytest.raises(ApiError) as e:
        data_api.run_find_data(None, user_folders={"documents": str(docs)}, drives_fn=lambda: [], exclude_paths=[], output_selection=out)
    assert "훑을 폴더 안" in e.value.message and not out.exists()
    assert "문서 폴더" not in " ".join(e.value.lines)  # 안내가 '문서 폴더에 저장하세요' 라고 모순되게 말하지 않는다
    ok = tmp_path / "elsewhere" / "selection.json"
    data_api.run_find_data(None, user_folders={"documents": str(docs)}, drives_fn=lambda: [], exclude_paths=[], output_selection=ok)
    assert ok.exists()
