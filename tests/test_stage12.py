"""12단계: 절전 방지, 진행률/ETA, 검증 모드, 사전 검사, 긴 경로, 대량 상태 파일, 실패 분류, 해시 목록(manifest)."""
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from pcmigrate import cli, fs, manifest as mf, transfer as tr
from pcmigrate.copier import FAILED, MISMATCH, MISSING, PENDING_VERIFY, VERIFIED, EXTRA, sha256_file
from pcmigrate.failure import (NET_CONFLICT, NET_LOGON, NET_NOT_FOUND, REASONS, classify, network_failure,
                               robocopy_failures)
from pcmigrate.fs import DestInfo, long_path
from pcmigrate.power import ES_CONTINUOUS, KEEP_AWAKE_FLAGS, KeepAwake
from pcmigrate.progress import (NETWORK_SLOW_HINT, SLOW_HINT, ProgressTracker, fmt_bytes, fmt_duration, slow_policy)
from pcmigrate.transfer import DriveDisconnected, State, TransferError, read_state_entries, transfer


def make_src(tmp_path, n=3):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    for i in range(n):
        (src / "sub" / f"f{i}.dwg").write_bytes(os.urandom(500) + bytes([i]))
    (src / "top.txt").write_bytes(b"top")
    return src


class FakeRobocopy:
    def __init__(self, code=1, skip=()):
        self.calls, self.code, self.skip = [], code, set(skip)

    def __call__(self, args):
        self.calls.append(list(args))
        src, dst = Path(args[1]), Path(args[2])
        if "/L" in args:
            return self.code, ""
        for root, _d, files in os.walk(src):
            rel = Path(root).relative_to(src)
            (dst / rel).mkdir(parents=True, exist_ok=True)
            for n in files:
                if n not in self.skip:
                    shutil.copy2(Path(root) / n, dst / rel / n)
        return self.code, ""


def info(free=10 ** 12, fs_name="NTFS", kind="fixed", serial=1):
    return lambda p: DestInfo(p, kind, fs_name, free, serial, "D:")


def run(src, dst, **kw):
    kw.setdefault("runner", FakeRobocopy())
    kw.setdefault("dest_info_fn", info())
    kw.setdefault("keep_awake", False)
    return transfer(src, dst, **kw)


# ------------------------------------------------------------------ 절전 방지

def test_keep_awake_sets_and_restores_flags():
    calls = []
    with KeepAwake(True, lambda f: calls.append(f) or 1) as k:
        assert k.status == "on"
    assert calls == [KEEP_AWAKE_FLAGS, ES_CONTINUOUS]
    assert KEEP_AWAKE_FLAGS == 0x80000001  # 연속 + 시스템 필요만(AWAYMODE 0x40, 디스플레이 0x2 없음)
    assert k.status == "restored"


def test_keep_awake_restores_on_exception():
    calls = []
    with pytest.raises(RuntimeError):
        with KeepAwake(True, lambda f: calls.append(f) or 1):
            raise RuntimeError("x")
    assert calls[-1] == ES_CONTINUOUS


def test_keep_awake_failure_only_warns():
    warns = []

    def boom(_f):
        raise OSError("no")
    with KeepAwake(True, boom, warns.append) as k:
        assert k.status == "failed"
    assert len(warns) == 1

    warns.clear()
    with KeepAwake(True, lambda f: 0, warns.append) as k:
        assert k.status == "failed"
    assert warns


def test_keep_awake_disabled_and_unsupported():
    calls = []
    with KeepAwake(False, lambda f: calls.append(f) or 1) as k:
        assert k.status == "off"
    assert calls == []
    with KeepAwake(True, lambda f: None) as k:
        assert k.status == "unsupported"


def test_transfer_uses_keep_awake_and_reports(tmp_path):
    src, calls = make_src(tmp_path), []
    rep = run(src, tmp_path / "dst", keep_awake=True, power_set_state=lambda f: calls.append(f) or 1)
    assert calls == [KEEP_AWAKE_FLAGS, ES_CONTINUOUS]
    assert rep.to_dict()["keep_awake"] == "restored"


# ------------------------------------------------------------------ 진행률 / ETA / 느림 힌트

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_progress_eta_and_speed():
    c = Clock()
    tr_ = ProgressTracker("copy", 1000 * 1024 * 1024, 10, clock=c)
    c.t = 10
    tr_.add(500 * 1024 * 1024, 5)
    snap = tr_.snapshot()
    assert snap.fraction == pytest.approx(0.5)
    assert snap.speed_bps == pytest.approx(50 * 1024 * 1024, rel=0.01)
    assert snap.eta_seconds == pytest.approx(10, rel=0.05)
    assert snap.hint == ""


def test_slow_hint_usb_and_network_differ():
    for kind, hint, speed in (("removable", SLOW_HINT, 20), ("network", NETWORK_SLOW_HINT, 5)):
        bps, h = slow_policy(kind)
        c = Clock()
        t = ProgressTracker("copy", 10 ** 12, 1000, clock=c, slow_bps=bps, slow_hint=h)
        got = ""
        for _ in range(8):
            c.t += 10
            t.add(speed * 1024 * 1024 * 10)
            got = t.snapshot().hint or got
        assert got == hint
    assert SLOW_HINT != NETWORK_SLOW_HINT and "100Mbps" in NETWORK_SLOW_HINT and "USB 2.0" in SLOW_HINT


def test_no_hint_when_fast():
    c = Clock()
    t = ProgressTracker("copy", 10 ** 12, 1000, clock=c)
    for _ in range(10):
        c.t += 10
        t.add(200 * 1024 * 1024 * 10)
        assert t.snapshot().hint == ""


def test_formatters():
    assert fmt_bytes(1536) == "1.5KB"
    assert fmt_duration(3720) == "1시간 2분"
    assert fmt_duration(None) == "계산 중"


def test_transfer_emits_progress_info(tmp_path):
    src, got = make_src(tmp_path), []
    run(src, tmp_path / "dst", progress_info=got.append, min_report_interval=0)
    assert got and all(i.total_bytes >= 0 for i in got)
    assert any(i.phase == "verify" for i in got)


# ------------------------------------------------------------------ 사전 검사

def test_free_space_precheck_blocks_with_5_percent_margin(tmp_path):
    src = make_src(tmp_path)
    total = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
    with pytest.raises(TransferError, match="여유 공간"):
        run(src, tmp_path / "dst", dest_info_fn=info(free=total))  # 딱 총량이면 5% 여유가 없어 차단
    rep = run(src, tmp_path / "dst", dest_info_fn=info(free=total + int(total * 0.06) + 10))
    assert not rep.failures


def test_free_space_unknown_only_warns(tmp_path):
    src, warns = make_src(tmp_path), []
    run(src, tmp_path / "dst", dest_info_fn=info(free=None), warn=warns.append)
    assert any("여유 공간" in w for w in warns)


def test_dry_run_free_space_only_warns(tmp_path):
    src, warns = make_src(tmp_path), []
    rep = run(src, tmp_path / "dst", dry_run=True, dest_info_fn=info(free=1), warn=warns.append)
    assert any("여유 공간" in w for w in warns) and rep.dry_run


def test_fat32_blocks_files_over_4gb(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    from pcmigrate import scan as sc
    orig = sc.scan_source_info

    def fat_scan(*a, **k):
        r = orig(*a, **k)
        r.max_size = fs.FAT4G + 1  # 4GB 초과 파일이 있다고 가정(실제 큰 파일을 만들지 않음)
        return r
    monkeypatch.setattr(tr, "scan_source_info", fat_scan)
    with pytest.raises(TransferError, match="FAT32"):
        run(src, tmp_path / "dst", dest_info_fn=info(fs_name="FAT32"))
    assert not (tmp_path / "dst").exists()  # 쓰기 전에 막힘
    run(src, tmp_path / "dst2", dest_info_fn=info(fs_name="NTFS"))  # NTFS 는 통과


def test_exfat_warns(tmp_path):
    warns = []
    run(make_src(tmp_path), tmp_path / "dst", dest_info_fn=info(fs_name="EXFAT"), warn=warns.append)
    assert any("exFAT" in w for w in warns)


def test_cdrom_destination_rejected(tmp_path):
    with pytest.raises(TransferError, match="CD"):
        run(make_src(tmp_path), tmp_path / "dst", dest_info_fn=info(kind="cdrom"))


# ------------------------------------------------------------------ 긴 경로

def test_long_path_prefix_rules():
    assert long_path("C:\\a\\b", nt=True) == "\\\\?\\C:\\a\\b"
    assert long_path("\\\\srv\\share\\x", nt=True) == "\\\\?\\UNC\\srv\\share\\x"
    assert long_path("\\\\?\\C:\\a", nt=True) == "\\\\?\\C:\\a"  # 이중 접두 없음
    assert long_path("/tmp/a", nt=False) == "/tmp/a"
    assert fs.is_unc("\\\\srv\\share") and not fs.is_unc("C:\\x")


@pytest.mark.skipif(os.name != "nt", reason="Windows 긴 경로")
def test_long_path_hash_over_260(tmp_path):
    d = tmp_path
    for _ in range(6):
        d = d / ("d" * 50)
    d_l = Path(long_path(d))
    os.makedirs(d_l, exist_ok=True)
    f = d_l / ("f" * 40 + ".txt")
    f.write_bytes(b"deep")
    assert len(str(d / f.name)) > 260
    assert sha256_file(d / f.name) == hashlib.sha256(b"deep").hexdigest()


# ------------------------------------------------------------------ 실패 분류

def test_classify_exceptions():
    def e(winerror=None, errno_=None, cls=OSError):
        x = cls(errno_ or 0, "x")
        if winerror is not None:
            x.winerror = winerror
        return x
    assert classify(e(32)).code == "in_use"
    assert classify(e(5, cls=PermissionError)).code == "permission"
    assert classify(e(206)).code == "path_too_long"
    assert classify(e(112)).code == "disk_full"
    assert classify(e(121)).code == "disconnected"
    assert classify(FileNotFoundError(2, "x")).code == "not_found"
    assert classify(FileNotFoundError(2, "x"), "C:\\" + "a" * 270).code == "path_too_long"
    assert classify(ValueError("x")).code == "other"
    assert all(f.hint for f in REASONS.values() if f.code not in ("other",))


def test_network_error_mapping():
    assert network_failure(53).code == NET_NOT_FOUND
    assert network_failure(67).code == NET_NOT_FOUND
    assert network_failure(1326).code == NET_LOGON
    assert network_failure(1219).code == NET_CONFLICT
    assert network_failure(999) is None


def test_robocopy_output_parsing_english_and_korean():
    out_en = "2025/01/01 ERROR 53 (0x00000035) Accessing Destination Directory \\\\10.0.0.2\\share\\\n"
    out_ko = "2025/01/01 오류 1326 (0x0000052E) 대상 디렉터리 액세스 중 \\\\10.0.0.2\\share\\\n"
    assert [f.code for f in robocopy_failures(out_en)] == [NET_NOT_FOUND]
    assert [f.code for f in robocopy_failures(out_ko)] == [NET_LOGON]
    assert robocopy_failures("파일 3개 복사") == []
    # 여러 번 나와도 한 번만
    assert len(robocopy_failures(out_en * 3)) == 1


def test_unc_connection_failure_is_friendly(tmp_path):
    """실제 네트워크는 쓸 수 없어 robocopy 출력을 흉내 낸다(한계: 실제 UNC 접속은 검증하지 못함)."""
    src = make_src(tmp_path)
    bad = lambda args: (16, "ERROR 53 (0x00000035) Accessing Destination Directory \\\\10.0.0.2\\share\\\n")
    with pytest.raises(TransferError) as ei:
        run(src, tmp_path / "dst", runner=bad, dest_info_fn=info(kind="network"))
    assert "공유 폴더" in str(ei.value)


# ------------------------------------------------------------------ 상태 파일(JSONL) / 대량

def test_state_file_is_jsonl_and_survives_truncated_tail(tmp_path):
    src = make_src(tmp_path)
    run(src, tmp_path / "dst")
    sp = tmp_path / "dst" / tr.STATE_NAME
    lines = sp.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2 and all(json.loads(l) for l in lines)
    with open(sp, "a", encoding="utf-8") as f:
        f.write('{"rel": "broken", "si')  # 전원 차단으로 잘린 마지막 줄
    entries = read_state_entries(sp)
    assert "broken" not in entries and len(entries) == 4


def test_state_many_files_roundtrip(tmp_path):
    n = 30000
    st = State(tmp_path / "s.jsonl", tmp_path / "src")
    for i in range(n):
        st.record(f"d{i % 100}/f{i}.bin", i, i * 7, "ab" * 32, True)
    st.close()
    again = State(tmp_path / "s.jsonl", tmp_path / "src")
    assert len(again.files) == n
    assert again.files["d5/f105.bin"][0] == 105
    again.close()


# ------------------------------------------------------------------ verify-only / retry-failed

def test_verify_only_does_not_copy_and_rereads_dest(tmp_path):
    src = make_src(tmp_path)
    run(src, tmp_path / "dst")
    rob = FakeRobocopy()
    rep = run(src, tmp_path / "dst", verify_only=True, runner=rob)
    assert rob.calls == []  # 복사 도구를 실행하지 않음
    d = rep.to_dict()
    assert d["summary"] == {VERIFIED: 4} and d["mode"] == "verify-only"
    assert d["io"]["dest_files_read"] == 4  # 대상은 반드시 다시 읽음


def test_verify_only_detects_later_corruption(tmp_path):
    src = make_src(tmp_path)
    run(src, tmp_path / "dst")
    victim = tmp_path / "dst" / "sub" / "f1.dwg"
    data = bytearray(victim.read_bytes())
    data[0] ^= 0xFF
    victim.write_bytes(bytes(data))  # 같은 크기, 내용만 변조
    rep = run(src, tmp_path / "dst", verify_only=True)
    assert [r.status for r in rep.failures] == [MISMATCH]


def test_verify_only_does_not_modify_destination(tmp_path):
    src = make_src(tmp_path)
    run(src, tmp_path / "dst")
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / "dst").rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate")}
    run(src, tmp_path / "dst", verify_only=True)
    after = {p: p.stat().st_mtime_ns for p in (tmp_path / "dst").rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate")}
    assert before == after


def test_verify_only_and_retry_are_exclusive(tmp_path):
    with pytest.raises(TransferError):
        run(make_src(tmp_path), tmp_path / "dst", verify_only=True, retry_failed=True)


def test_retry_failed_only_recopies_failed(tmp_path):
    src = make_src(tmp_path)
    run(src, tmp_path / "dst", runner=FakeRobocopy(skip={"f1.dwg"}))  # f1 누락
    rob = FakeRobocopy()
    rep = run(src, tmp_path / "dst", retry_failed=True, runner=rob)
    assert not rep.failures and (tmp_path / "dst" / "sub" / "f1.dwg").exists()
    assert not any("/E" in c for c in rob.calls)  # 전체 복사는 다시 하지 않음


# ------------------------------------------------------------------ 읽기 횟수 / 해시 재사용

def test_io_counts_first_run_reads_source_once_dest_once(tmp_path):
    src = make_src(tmp_path)
    rep = run(src, tmp_path / "dst")
    io = rep.to_dict()["io"]
    assert io["source_files_read"] == 4 and io["dest_files_read"] == 4


def test_resume_reuses_state_hash_no_source_reread(tmp_path):
    src = make_src(tmp_path)
    run(src, tmp_path / "dst")
    rep = run(src, tmp_path / "dst", runner=FakeRobocopy())
    io = rep.to_dict()["io"]
    assert io["source_files_read"] == 0 and io["dest_files_read"] == 0  # 이미 검사 끝난 파일은 건드리지 않음


# ------------------------------------------------------------------ manifest 생성/검증

def test_manifest_build_and_header(tmp_path):
    src = make_src(tmp_path)
    r = mf.build_manifest(src, tmp_path / "m.jsonl")
    assert r.files == 4 and not r.errors
    lines = (tmp_path / "m.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(l) for l in lines]
    assert len(rows) == 6  # 헤더 + 4행 + 끝줄
    rel = [x for x in rows if x.get("rel") == "sub/f0.dwg"][0]
    assert rel["sha256"] == hashlib.sha256((src / "sub" / "f0.dwg").read_bytes()).hexdigest()
    assert rel["size"] == 501 and "mtime_ns" in rel


def test_manifest_does_not_modify_source(tmp_path):
    src = make_src(tmp_path)
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in src.rglob("*") if p.is_file()}
    mf.build_manifest(src, tmp_path / "m.jsonl")
    after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in src.rglob("*") if p.is_file()}
    assert before == after and sorted(p.name for p in src.rglob("*") if p.is_file()) == sorted(p.name for p in before)


def test_manifest_streaming_part_file_then_atomic(tmp_path):
    w = mf.ManifestWriter(tmp_path / "m.jsonl", "C:\\src", 2, 2)
    w.write_row("a.txt", 1, 1, "00" * 32)
    assert not (tmp_path / "m.jsonl").exists() and any(p.name.endswith(".part") for p in tmp_path.iterdir())
    w.write_row("b.txt", 1, 1, "11" * 32)
    w.close()
    assert (tmp_path / "m.jsonl").exists() and not any(p.name.endswith(".part") for p in tmp_path.iterdir())
    w2 = mf.ManifestWriter(tmp_path / "n.jsonl", "C:\\src", 1, 1)
    w2.write_row("a", 1, 1, "00" * 32)
    w2.abort()
    assert not (tmp_path / "n.jsonl").exists()


def test_manifest_incomplete_detected(tmp_path):
    src = make_src(tmp_path)
    mf.build_manifest(src, tmp_path / "m.jsonl")
    lines = (tmp_path / "m.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "cut.jsonl").write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")  # 끝줄과 한 행이 없음
    shutil.copytree(src, tmp_path / "copy")
    rep = mf.verify_against_manifest(tmp_path / "cut.jsonl", tmp_path / "copy")
    assert rep.failures and rep.to_dict().get("manifest_complete") is False


def test_manifest_rejects_unsafe_rel_and_bad_header(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    with pytest.raises(mf.ManifestError):
        mf.ManifestReader(bad).header
    with pytest.raises(mf.ManifestError):
        mf._safe_rel("../evil.txt")
    with pytest.raises(mf.ManifestError):
        mf._safe_rel("C:/windows/x")


def test_verify_missing_mismatch_extra_and_read_only(tmp_path):
    src = make_src(tmp_path)
    mf.build_manifest(src, tmp_path / "m.jsonl")
    dst = tmp_path / "dst"
    shutil.copytree(src, dst)
    (dst / "sub" / "f0.dwg").unlink()                               # 누락
    (dst / "sub" / "f1.dwg").write_bytes(b"Z" * 501)                 # 같은 크기, 다른 내용
    (dst / "new.txt").write_text("extra")                            # 추가
    (dst / ".pcmigrate-state.jsonl").write_text("{}")                # 도구 기록 파일은 제외
    snapshot = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in dst.rglob("*") if p.is_file()}
    rep = mf.verify_against_manifest(tmp_path / "m.jsonl", dst)
    by = {r.src.replace("\\", "/").split("/")[-1].split(" ")[-1]: r.status for r in rep.results}
    assert by["f0.dwg"] == MISSING and by["f1.dwg"] == MISMATCH and by["new.txt"] == EXTRA
    assert by["f2.dwg"] == VERIFIED and ".pcmigrate-state.jsonl" not in by
    assert {r.status for r in rep.failures} == {MISSING, MISMATCH}  # 추가 파일은 실패로 세지 않음
    assert snapshot == {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in dst.rglob("*") if p.is_file()}  # 읽기 전용


def test_verify_no_extras_option(tmp_path):
    src = make_src(tmp_path)
    mf.build_manifest(src, tmp_path / "m.jsonl")
    shutil.copytree(src, tmp_path / "dst")
    (tmp_path / "dst" / "new.txt").write_text("x")
    rep = mf.verify_against_manifest(tmp_path / "m.jsonl", tmp_path / "dst", find_extras=False)
    assert EXTRA not in rep.to_dict()["summary"]


def test_manifest_many_rows_streams(tmp_path):
    n = 20000
    w = mf.ManifestWriter(tmp_path / "big.jsonl", "C:\\s", n, n)
    for i in range(n):
        w.write_row(f"d{i % 50}/f{i}", 1, 1, "ab" * 32)
    w.close()
    r = mf.ManifestReader(tmp_path / "big.jsonl")
    assert sum(1 for _ in r.rows()) == n and r.header["total_files"] == n


# ------------------------------------------------------------------ transfer + manifest 흐름(외장 / 랜선)

def test_transfer_manifest_out_then_verify_local(tmp_path):
    """랜선 흐름: 구 PC 에서 복사+목록, 새 PC 에서 목록으로 로컬 검사."""
    src = make_src(tmp_path)
    ext = tmp_path / "ext"
    rep = run(src, tmp_path / "net_dst", manifest_out=ext / "m.jsonl", dest_info_fn=info(kind="network"))
    d = rep.to_dict()
    assert d["dest_verification"] == "deferred" and {r.status for r in rep.results} == {PENDING_VERIFY}
    assert d["io"]["dest_files_read"] == 0  # 네트워크 대상은 다시 읽지 않음
    assert (ext / "m.jsonl").exists()
    v = mf.verify_against_manifest(ext / "m.jsonl", tmp_path / "net_dst")
    assert v.to_dict()["summary"] == {VERIFIED: 4}


def test_external_drive_stage1_writes_manifest_and_stage2_uses_it(tmp_path):
    """외장 흐름: 1단계(구 PC→외장, 로컬이라 바로 검사+목록), 2단계(외장→새 PC, 외장 목록으로 원본 재읽기 없이 검사)."""
    src = make_src(tmp_path)
    ext = tmp_path / "ext"
    r1 = run(src, ext, manifest_out=ext / mf.DEFAULT_NAME)
    assert (ext / mf.DEFAULT_NAME).exists() and not r1.failures
    assert r1.to_dict()["dest_verification"] == "now"
    new_pc = tmp_path / "newpc"
    r2 = run(ext, new_pc, use_manifest=ext / mf.DEFAULT_NAME,
             state_path=tmp_path / "state2.jsonl", log_path=tmp_path / "log2.txt")
    io = r2.to_dict()["io"]
    assert not r2.failures and io["source_read_bytes"] == 0  # 외장을 다시 해시하지 않음(복사 중 읽기 제외)
    assert io["dest_files_read"] >= 4


def test_stage2_detects_file_missing_from_manifest_copy(tmp_path):
    src = make_src(tmp_path)
    ext = tmp_path / "ext"
    run(src, ext, manifest_out=ext / mf.DEFAULT_NAME)
    rep = run(ext, tmp_path / "newpc", use_manifest=ext / mf.DEFAULT_NAME, runner=FakeRobocopy(skip={"f2.dwg"}),
              state_path=tmp_path / "s2.jsonl", log_path=tmp_path / "l2.txt")
    assert any(r.status == MISSING for r in rep.failures)


def test_drive_disconnect_stops_and_resumes(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "ext"

    def gone(args):
        raise DriveDisconnected(tr.DISCONNECTED_MESSAGE)
    with pytest.raises(DriveDisconnected) as ei:
        run(src, dst, runner=gone)
    assert "다시 연결하면 이어받습니다" in str(ei.value)
    rep = run(src, dst)  # 재연결 후 같은 설정으로 다시 실행
    assert not rep.failures and rep.to_dict()["summary"] == {VERIFIED: 4}


def test_drive_letter_change_warning(tmp_path):
    src, warns = make_src(tmp_path), []
    run(src, tmp_path / "dst", dest_info_fn=info(serial=77))
    # 같은 폴더를 다른 일련번호로 다시 연결한 것처럼
    run(src, tmp_path / "dst", dest_info_fn=info(serial=99), warn=warns.append)
    assert any("일련번호" in w or "드라이브" in w for w in warns)


# ------------------------------------------------------------------ CLI

def test_cli_manifest_and_verify_roundtrip(tmp_path, capsys):
    src = make_src(tmp_path)
    assert cli.main(["manifest", str(src), "--out", str(tmp_path / "m.jsonl")]) == 0
    shutil.copytree(src, tmp_path / "dst")
    assert cli.main(["verify", "--manifest", str(tmp_path / "m.jsonl"), "--target", str(tmp_path / "dst"),
                     "--report", str(tmp_path / "r.json")]) == 0
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["summary"] == {VERIFIED: 4}
    (tmp_path / "dst" / "top.txt").write_bytes(b"tampered!!")
    assert cli.main(["verify", "--manifest", str(tmp_path / "m.jsonl"), "--target", str(tmp_path / "dst")]) == 1


def test_cli_verify_refuses_report_inside_target(tmp_path, capsys):
    src = make_src(tmp_path)
    cli.main(["manifest", str(src), "--out", str(tmp_path / "m.jsonl")])
    shutil.copytree(src, tmp_path / "dst")
    rc = cli.main(["verify", "--manifest", str(tmp_path / "m.jsonl"), "--target", str(tmp_path / "dst"),
                   "--report", str(tmp_path / "dst" / "r.json")])
    assert rc == 1 and not (tmp_path / "dst" / "r.json").exists()


def test_cli_unexpanded_env_rejected(tmp_path):
    assert cli.main(["verify", "--manifest", "%NOPE%\\m.jsonl", "--target", str(tmp_path)]) == 1


def test_cli_transfer_options_parse(tmp_path):
    p = cli.build_parser()
    a = p.parse_args(["transfer", "a", "b", "--verify-only", "--workers", "3", "--chunk-mb", "8", "--no-keep-awake",
                      "--manifest-out", "m", "--no-verify-dest", "--copy-mode", "normal"])
    assert a.verify_only and a.workers == 3 and a.chunk_mb == 8 and a.no_keep_awake
    assert a.verify_dest is False and a.copy_mode == "normal"
    assert p.parse_args(["transfer", "a", "b"]).verify_dest is None


def test_copy_mode_args():
    base = lambda m: tr.base_args("s", "d", "log", dry_run=False, copy_mode=m)
    assert "/Z" in base("restartable") and "/J" not in base("restartable")
    assert "/J" in base("unbuffered") and "/Z" not in base("unbuffered")
    assert "/Z" not in base("normal") and "/J" not in base("normal")
    for m in ("restartable", "normal", "unbuffered"):
        tr.validate_args(base(m))


# ------------------------------------------------------------------ 복사 스레드(/MT) / 해시 버퍼 / 실제 robocopy 통합

def test_copy_threads_flag_only_for_real_runs():
    a = tr.base_args("s", "d", "log", dry_run=False, threads=4)
    assert "/MT:4" in a
    tr.validate_args(a)
    assert not any(x.startswith("/MT") for x in tr.base_args("s", "d", "log", dry_run=False))  # 기본 호출은 1개
    assert not any(x.startswith("/MT") for x in tr.base_args("s", "d", None, dry_run=True, threads=4))  # 미리보기(/L)에는 쓰지 않음
    assert "/MT:16" in tr.base_args("s", "d", "log", dry_run=False, threads=99)


def test_transfer_default_threads_and_option(tmp_path):
    src = make_src(tmp_path)
    rob = FakeRobocopy()
    run(src, tmp_path / "d1", runner=rob)
    assert f"/MT:{tr.DEFAULT_COPY_THREADS}" in rob.calls[0]
    rob2 = FakeRobocopy()
    rep = run(src, tmp_path / "d2", runner=rob2, copy_threads=1)
    assert not any(x.startswith("/MT") for x in rob2.calls[0]) and rep.to_dict()["copy_threads"] == 1


@pytest.mark.parametrize("size", [0, 1, 4095, 4096, 4097, 1024 * 1024 - 1, 1024 * 1024, 1024 * 1024 + 1, 3 * 1024 * 1024 + 7])
def test_sha256_file_matches_hashlib_for_all_buffer_paths(tmp_path, size):
    data = os.urandom(size)
    f = tmp_path / "x.bin"
    f.write_bytes(data)
    expect = hashlib.sha256(data).hexdigest()
    assert sha256_file(f) == expect
    assert sha256_file(f, chunk=1024 * 1024) == expect  # 작은 청크: 큰 파일 경로(스레드 버퍼 재사용)
    assert sha256_file(f, chunk=1024 * 1024) == expect  # 같은 스레드에서 두 번째 호출(버퍼 재사용)


def test_sha256_file_buffer_reuse_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    files = []
    for i in range(8):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(os.urandom(300_000 + i))
        files.append(f)
    expect = [hashlib.sha256(f.read_bytes()).hexdigest() for f in files]
    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(lambda f: sha256_file(f, chunk=64 * 1024), files * 3))
    assert got == expect * 3


@pytest.mark.skipif(os.name != "nt" or shutil.which("robocopy") is None, reason="실제 robocopy 는 Windows 에서만")
def test_real_robocopy_end_to_end_in_tmp_path(tmp_path):
    """tmp_path 안에서만: 실제 robocopy(/Z /MT) 복사 + 해시 검사 + 목록 파일 + 목록으로 다시 검사."""
    src = tmp_path / "원본 폴더"  # 한글/공백 경로
    for d in range(5):
        (src / f"도면{d}" / "하위").mkdir(parents=True)
        for i in range(20):
            (src / f"도면{d}" / "하위" / f"f{i}.dwg").write_bytes(os.urandom(100 + i * 37))
    (src / "큰파일.bin").write_bytes(os.urandom(3 * 1024 * 1024 + 11))
    dst = tmp_path / "대상"
    got = []
    rep = transfer(src, dst, keep_awake=False, manifest_out=tmp_path / "m.jsonl", progress_info=got.append, min_report_interval=0)
    d = rep.to_dict()
    assert not rep.failures and d["summary"] == {VERIFIED: 101} and d["robocopy_exit"] in (1, 3)
    assert (dst / "큰파일.bin").read_bytes() == (src / "큰파일.bin").read_bytes()
    assert not (dst / ".pcmigrate-manifest.jsonl").exists()  # 기록 파일은 복사하지 않음
    assert any(i.phase == "copy" for i in got) or any(i.phase == "verify" for i in got)
    v = mf.verify_against_manifest(tmp_path / "m.jsonl", dst)
    assert v.to_dict()["summary"] == {VERIFIED: 101}
    # 변조 후 다시 검사하면 잡아낸다
    victim = dst / "도면2" / "하위" / "f3.dwg"
    b = bytearray(victim.read_bytes())
    b[0] ^= 1
    victim.write_bytes(bytes(b))
    assert [r.status for r in mf.verify_against_manifest(tmp_path / "m.jsonl", dst).failures] == [MISMATCH]
    # 원본은 그대로
    assert sum(1 for _ in src.rglob("*") if _.is_file()) == 101
