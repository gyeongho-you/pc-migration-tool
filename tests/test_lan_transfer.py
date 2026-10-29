"""stage29: LAN 직접 전송(receive/send). 모든 시험은 127.0.0.1 루프백과 임시 폴더만 쓴다(실제 LAN 방송/방화벽 변경 없음)."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pcmigrate import cli, fs, lan_api, lan_link, lan_protocol as P, lan_transfer as LT, multi_transfer
from pcmigrate.cancel import CancelToken, Cancelled
from pcmigrate.lan_protocol import (Channel, LanError, PairingCode, RemoteError, client_handshake, server_handshake, validate_rel)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fast_kdf(monkeypatch):
    monkeypatch.setattr(P, "KDF_ITERATIONS", 1000)


@pytest.fixture
def rx_factory(tmp_path):
    made = []

    def make(dest=None, **kw):
        kw.setdefault("fail_delay", 0)
        kw.setdefault("discovery", False)
        kw.setdefault("addresses", [])
        rx = lan_api.start_receiver(dest or tmp_path / "dst", 0, kw.pop("on_event", None), **kw)
        made.append(rx)
        return rx

    yield make
    for r in made:
        r.stop()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_src(root: Path, files: dict[str, bytes], dirs=()):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


def send(rx, sources=None, selection=None, **opts):
    opts.setdefault("chunk", 4096)
    return lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, selection=selection, sources=sources, options=opts)


def raw_client(rx, code=None) -> Channel:
    s = socket.create_connection(("127.0.0.1", rx.port), timeout=10)
    ch = Channel(s)
    ch.sock.settimeout(10)
    client_handshake(ch, code or rx.code)
    return ch


def raw_plan(ch, files, nbytes, dirs=0, max_size=0):
    ch.send_json(P.T_PLAN, {"files": files, "dirs": dirs, "bytes": nbytes, "max_size": max_size, "chunk": 4096, "name": "t"})
    return ch.recv_json(P.T_PLAN_OK)[1]


def raw_list(ch, entries):
    ch.send_json(P.T_LIST, {"entries": entries, "last": True})
    return ch.recv_json(P.T_LIST_ACK)[1]


def wait_state(rx, state, timeout=5.0):
    end = time.monotonic() + timeout
    while rx.state != state and time.monotonic() < end:
        time.sleep(0.02)
    assert rx.state == state


def tree(root: Path) -> dict[str, bytes]:
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def user_tree(root: Path) -> dict[str, bytes]:
    return {k: v for k, v in tree(root).items() if not k.split("/")[-1].startswith(".pcmigrate-")}


class Proxy:
    """클라이언트->서버 방향 바이트를 세어 일정 위치에서 연결을 끊거나(cut_after) 한 비트를 뒤집는(flip_at) TCP 중계."""

    def __init__(self, port, cut_after=None, flip_at=None):
        self.port, self.cut_after, self.flip_at = port, cut_after, flip_at
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.listen_port = self.srv.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            c, _ = self.srv.accept()
        except OSError:
            return
        up = socket.create_connection(("127.0.0.1", self.port))
        threading.Thread(target=self._pump, args=(up, c, False), daemon=True).start()
        self._pump(c, up, True)

    def _pump(self, a, b, c2s):
        total = 0
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                if c2s:
                    if self.flip_at is not None and total <= self.flip_at < total + len(data):
                        i = self.flip_at - total
                        data = data[:i] + bytes([data[i] ^ 0x01]) + data[i + 1:]
                    if self.cut_after is not None and total + len(data) >= self.cut_after:
                        b.sendall(data[: self.cut_after - total])
                        break
                    total += len(data)
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                s.close()

    def close(self):
        self.srv.close()


# --------------------------------------------------------------------------- 핸드셰이크
def _pair():
    a, b = socket.socketpair()
    a.settimeout(10)
    b.settimeout(10)
    return Channel(a), Channel(b)


def _run_server(sc, pairing, **kw):
    box = {}

    def run():
        try:
            box["hello"] = server_handshake(sc, pairing, sleep=lambda s: box.setdefault("slept", []).append(s), **kw)
        except Exception as e:  # noqa: BLE001
            box["err"] = e
    t = threading.Thread(target=run)
    t.start()
    return t, box


def test_handshake_success_and_session_keys_work():
    cc, sc = _pair()
    t, box = _run_server(sc, PairingCode("123456", fail_delay=0))
    info = client_handshake(cc, "123456")
    t.join(5)
    assert "err" not in box and info["ver"] == P.PROTO_VERSION
    cc.send_json(P.T_PING if False else P.T_PLAN, {"x": 1})
    assert sc.recv_json()[1] == {"x": 1}
    sc.send_json(P.T_PLAN_OK, {"y": 2})
    assert cc.recv_json()[1] == {"y": 2}


def test_handshake_wrong_code_rejected_with_delay_and_code_never_sent():
    cc, sc = _pair()
    sent = []

    class Rec:
        def __init__(self, s):
            self.s = s

        def sendall(self, d):
            sent.append(bytes(d))
            self.s.sendall(d)

        def __getattr__(self, n):
            return getattr(self.s, n)
    cc.sock = Rec(cc.sock)
    pc = PairingCode("123456", fail_delay=0.5)
    t, box = _run_server(sc, pc)
    with pytest.raises(RemoteError) as ei:
        client_handshake(cc, "654321")
    t.join(5)
    assert ei.value.code == "auth" and ei.value.payload["remaining"] == P.MAX_FAILURES - 1
    assert box["slept"] == [0.5]
    assert pc.failures == 1
    blob = b"".join(sent)
    assert b"654321" not in blob and b"123456" not in blob  # 코드 자체는 전송하지 않는다


def test_retry_limit_locks_code_even_for_correct_code(rx_factory, tmp_path):
    rx = rx_factory(max_failures=3)
    events = []
    for n in range(3):
        with pytest.raises(RemoteError) as ei:
            raw_client(rx, "000000" if rx.code != "000000" else "111111")
        assert ei.value.code in ("auth", "locked")
    with pytest.raises(RemoteError) as ei:
        raw_client(rx)  # 맞는 코드도 잠김
    assert ei.value.code == "locked"
    assert rx.pairing.failures >= 3


def test_failure_delay_grows(monkeypatch):
    pc = PairingCode("123456", fail_delay=0.25)
    assert [pc.record_failure()[1] for _ in range(4)] == [0.25, 0.5, 1.0, 2.0]


def test_code_expires_after_ttl(rx_factory):
    rx = rx_factory(ttl=0.05)
    time.sleep(0.15)
    with pytest.raises(RemoteError) as ei:
        raw_client(rx)
    assert ei.value.code == "expired"


def test_paired_code_valid_for_reconnect_but_closed_after_done(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {"a.txt": b"x"})
    rx = rx_factory()
    send(rx, [str(src)])
    assert rx.wait(5) and rx.state == "done"
    with pytest.raises((LanError, OSError)):
        send(rx, [str(src)])  # 끝난 뒤에는 접속할 수 없다(리스너가 닫힘)


def test_pairing_code_unit_clock():
    now = [0.0]
    pc = PairingCode("123456", clock=lambda: now[0])
    pc.check_open()
    now[0] = 601
    with pytest.raises(lan_protocol_AuthError()):
        pc.check_open()
    pc2 = PairingCode("123456", clock=lambda: now[0])
    pc2.record_success()
    now[0] += 1799
    pc2.check_open()
    now[0] += 2
    with pytest.raises(lan_protocol_AuthError()):
        pc2.check_open()


def lan_protocol_AuthError():
    return P.AuthError


def test_frame_bitflip_rejected():
    a, b = socket.socketpair()
    sender, receiver = Channel(a), Channel(b)
    key = b"k" * 32
    sender.set_keys(key, key, 1 << 20)
    receiver.set_keys(key, key, 1 << 20)
    sender.send_json(P.T_PLAN, {"files": 1})
    raw = bytearray()
    b.settimeout(2)
    raw += b.recv(4096)
    raw[P.HDR.size + 2] ^= 0x01  # 페이로드 한 비트 뒤집기
    c, d = socket.socketpair()
    c.sendall(bytes(raw))
    rc = Channel(d)
    rc.set_keys(key, key, 1 << 20)
    with pytest.raises(P.ProtocolError):
        rc.recv()
    # 순번 변조(재전송)도 거부
    sender2, recv2 = _pair()
    sender2.set_keys(key, key, 1 << 20)
    recv2.set_keys(key, key, 1 << 20)
    sender2.send(P.T_PING)
    sender2.send(P.T_PING)
    recv2.sock.settimeout(2)
    first = recv2.recv()
    assert first[0] == P.T_PING
    sender2._send_seq = 0
    sender2.send(P.T_PING)  # 순번 0 을 다시 보냄
    with pytest.raises(P.ProtocolError):
        recv2.recv() and recv2.recv()


def test_version_mismatch_from_old_client(rx_factory):
    rx = rx_factory()
    s = socket.create_connection(("127.0.0.1", rx.port), timeout=5)
    ch = Channel(s)
    ch.send_json(P.T_HELLO, {"ver": 99, "role": "send", "nonce": os.urandom(16).hex()})
    with pytest.raises(RemoteError) as ei:
        ch.recv_json()
    assert ei.value.code == "version"


def test_version_mismatch_detected_by_client():
    cc, sc = _pair()

    def fake():
        sc.recv_json(P.T_HELLO)
        sc.send_json(P.T_CHALLENGE, {"ver": 2, "nonce": "00" * 16, "salt": "00" * 16, "iter": 10})
    threading.Thread(target=fake).start()
    with pytest.raises(P.VersionMismatch):
        client_handshake(cc, "123456")


def test_client_rejects_server_that_does_not_know_code():
    cc, sc = _pair()

    def fake():
        hello = sc.recv_json(P.T_HELLO)[1]
        sc.send_json(P.T_CHALLENGE, {"ver": 1, "nonce": "11" * 16, "salt": "22" * 16, "iter": 10})
        sc.recv_json(P.T_AUTH)
        sc.send_json(P.T_AUTH_OK, {"proof": "00" * 32, "name": "fake", "ver": 1})
    threading.Thread(target=fake).start()
    with pytest.raises(P.AuthError):
        client_handshake(cc, "123456")


def test_non_pcmigrate_peer_and_oversize_frame_rejected(rx_factory):
    rx = rx_factory()
    s = socket.create_connection(("127.0.0.1", rx.port), timeout=5)
    s.sendall(b"GET / HTTP/1.1\r\n\r\n" + b"x" * 40)
    s.settimeout(5)
    try:
        assert s.recv(10) == b""  # 연결을 닫는다
    except ConnectionResetError:
        pass
    s2 = socket.create_connection(("127.0.0.1", rx.port), timeout=5)
    s2.sendall(P.HDR.pack(P.MAGIC, P.PROTO_VERSION, P.T_HELLO, 10 ** 8, 0))  # 인증 전 거대한 프레임
    s2.settimeout(5)
    assert s2.recv(100) == b""
    assert rx.state == "waiting"  # 서버는 계속 산다


def test_bad_code_text_and_normalization():
    assert P.normalize_code("123-456") == "123456"
    for bad in ("12345", "1234567", "abcdef", ""):
        with pytest.raises(LanError):
            P.normalize_code(bad)
    assert len(P.new_code()) == 6 and P.new_code().isdigit()


# --------------------------------------------------------------------------- 전송 기본
def test_transfer_basic_names_empty_dirs_zero_bytes_and_verify(rx_factory, tmp_path, capsys):
    files = {"a.txt": b"hello", "sub/한글 파일 (1).bin": os.urandom(30000), "sub/deep/x y.dat": b"y" * 10, "zero.bin": b""}
    src = make_src(tmp_path / "src", files, dirs=["empty", "sub/empty2"])
    before = {p: os.stat(p).st_mtime_ns for p in (src / k for k in files)}
    rx = rx_factory()
    rep = send(rx, [str(src)])
    assert rep["success"] and rep["verification"] == {"exact": 4, "mismatch": 0, "missing": 0}
    assert rx.wait(5) and rx.result["success"]
    dst = tmp_path / "dst"
    assert user_tree(dst) == files
    assert (dst / "empty").is_dir() and (dst / "sub" / "empty2").is_dir()
    # 원본은 그대로(읽기 전용), 기록 파일이 생기지 않음
    assert {p: os.stat(p).st_mtime_ns for p in before} == before
    assert not any(p.name.startswith(".pcmigrate") for p in src.rglob("*"))
    # 수정 시각 보존
    assert os.stat(dst / "a.txt").st_mtime_ns == os.stat(src / "a.txt").st_mtime_ns
    # 기존 verify 로 다시 검사
    m = dst / ".pcmigrate-manifest.jsonl"
    assert cli.main(["verify", "--manifest", str(m), "--target", str(dst)]) == 0
    capsys.readouterr()
    assert not list(dst.rglob("*.pcmigrate-part"))


def test_long_path_and_big_mock_with_small_chunks(rx_factory, tmp_path):
    deep = "/".join(["d" * 60] * 4)
    big = os.urandom(300_000)
    files = {f"{deep}/long.bin": b"L" * 5000, "big.bin": big}
    for i in range(300):
        files[f"many/f{i:03}.txt"] = b"%d" % i
    src = tmp_path / "src"
    for rel, data in files.items():
        p = src.joinpath(*rel.split("/"))
        os.makedirs(fs.long_path(p.parent), exist_ok=True)
        with open(fs.long_path(p), "wb") as f:
            f.write(data)
    rx = rx_factory()
    rep = send(rx, [str(src)], chunk=1024)
    assert rep["success"] and rep["verification"]["exact"] == 302 and rep["verification"]["missing"] == 0
    with open(fs.long_path(tmp_path / "dst" / deep / "long.bin"), "rb") as f:
        assert f.read() == b"L" * 5000


def test_rerun_skips_complete_files_without_bak(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {"a.txt": b"A" * 100, "b/c.txt": b"C" * 50})
    rx = rx_factory()
    # 먼저 한 번 끝까지 받은 상태를 만든 뒤 새 수신기로 같은 폴더에 다시 받는다
    send(rx, [str(src)])
    rx.wait(5)
    rx2 = rx_factory(tmp_path / "dst")
    rep = send(rx2, [str(src)])
    assert rep["skipped_existing"] == 2 and rep["sent_files"] == 0 and rep["success"]
    assert not list((tmp_path / "dst").rglob("*.bak*"))


def test_existing_different_file_kept_as_bak(rx_factory, tmp_path):
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "a.txt").write_bytes(b"OLD-CONTENT-DIFFERENT-SIZE")
    (dst / "b.txt").write_bytes(b"1234")  # 같은 크기, 다른 내용
    src = make_src(tmp_path / "src", {"a.txt": b"new a", "b.txt": b"abcd"})
    rx = rx_factory(dst)
    rep = send(rx, [str(src)])
    assert rep["success"] and rep["preserved_existing"] == 2
    assert (dst / "a.txt").read_bytes() == b"new a" and (dst / "a.txt.bak").read_bytes() == b"OLD-CONTENT-DIFFERENT-SIZE"
    assert (dst / "b.txt").read_bytes() == b"abcd" and (dst / "b.txt.bak").read_bytes() == b"1234"


def test_multi_source_uses_drive_structure_like_multi_transfer(rx_factory, tmp_path):
    a = make_src(tmp_path / "projA", {"x.txt": b"x", "s/y.txt": b"yy"})
    b = make_src(tmp_path / "projB", {"z.txt": b"zzz"})
    rx = rx_factory()
    rep = send(rx, [str(a), str(b)])
    assert rep["success"] and rep["files"] == 3
    dst = tmp_path / "dst"
    ra, rb = multi_transfer.dest_rel(str(a)).replace("\\", "/"), multi_transfer.dest_rel(str(b)).replace("\\", "/")
    assert user_tree(dst) == {f"{ra}/x.txt": b"x", f"{ra}/s/y.txt": b"yy", f"{rb}/z.txt": b"zzz"}


def test_selection_file_and_single_selection_structure(rx_factory, tmp_path):
    from pcmigrate import find_data as fd
    a = make_src(tmp_path / "projA", {"x.txt": b"x"})
    sel = tmp_path / "sel.json"
    sel.write_text(json.dumps({"format": fd.SELECTION_FORMAT, "version": 1, "items": [{"path": str(a), "include": True}, {"path": str(tmp_path / "other"), "include": False}]}), encoding="utf-8")
    rx = rx_factory()
    rep = send(rx, selection=str(sel))
    assert rep["success"]
    assert list(user_tree(tmp_path / "dst")) == [multi_transfer.dest_rel(str(a)).replace("\\", "/") + "/x.txt"]


def test_exclude_junk_and_options(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {"keep.dwg": b"d", "Thumbs.db": b"t", "~$lock.docx": b"l", "sub/x.tmp": b"t"})
    rx = rx_factory()
    rep = send(rx, [str(src)], exclude_junk=True)
    assert rep["success"] and rep["junk_excluded"]["files"] == 3
    assert list(user_tree(tmp_path / "dst")) == ["keep.dwg"]


def test_progress_info_and_events(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {"a.bin": os.urandom(200_000), "b.bin": b"b"})
    events, infos = [], []
    rx = rx_factory(on_event=events.append)
    lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, sources=[str(src)], options={"chunk": 4096}, progress_info=infos.append)
    rx.wait(5)
    types = [e["type"] for e in events]
    assert types[0] == "listening" and "peer_connected" in types and types[-1] == "done"
    assert events[0]["code"] == rx.code and "addresses" in events[0]
    copy = [i for i in infos if i.phase == "copy"]
    assert copy and copy[-1].done_bytes == copy[-1].total_bytes == 200_001 and copy[-1].done_files == 2
    rprog = [e["info"] for e in events if e["type"] == "progress" and e.get("info")]
    assert rprog and rprog[-1].done_bytes == 200_001


# --------------------------------------------------------------------------- 변조/무결성
def test_tampered_stream_detected_and_no_complete_file(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {"big.bin": os.urandom(400_000)})
    rx = rx_factory()
    px = Proxy(rx.port, flip_at=150_000)
    try:
        with pytest.raises(LanError):
            lan_api.run_send(f"127.0.0.1:{px.listen_port}", rx.code, sources=[str(src)], options={"chunk": 4096})
    finally:
        px.close()
    wait_state(rx, "waiting")
    assert not (tmp_path / "dst" / "big.bin").exists()
    assert any(e["type"] in ("error", "disconnected") for e in rx.events)


def test_wrong_file_hash_discards_part_and_reports_mismatch(rx_factory, tmp_path):
    rx = rx_factory()
    ch = raw_client(rx)
    raw_plan(ch, 1, 10)
    raw_list(ch, [{"p": "f.bin", "s": 10, "d": False}])
    ch.send_json(P.T_FILE_BEGIN, {"i": 0, "p": "f.bin", "s": 10, "h": sha(b"0123456789"), "m": 0, "start": 0, "wait": False})
    ch.send(P.T_FILE_DATA, b"0123456780")  # 마지막 글자가 다름
    ch.send_json(P.T_FILE_END, {"i": 0})
    r = ch.recv_json(P.T_FILE_RESULT)[1]
    assert r["ok"] is False and r["reason"] == "mismatch"
    dst = tmp_path / "dst"
    assert not (dst / "f.bin").exists() and not (dst / "f.bin.pcmigrate-part").exists()
    ch.close()


def test_sender_retries_mismatch_from_start(rx_factory, tmp_path, monkeypatch):
    """수신 측 해시 대조가 실패하면(전송 손상) 임시 파일을 버리고 송신 측이 처음부터 다시 보낸다."""
    src = make_src(tmp_path / "src", {"f.bin": os.urandom(20000)})
    rx = rx_factory()
    orig = LT._Session._on_data
    state = {"n": 0}

    def corrupt(self, payload):
        if state["n"] == 0 and self.cur is not None and not self.cur.err:
            state["n"] = 1
            payload = bytearray(payload)
            payload[0] ^= 0xFF
        return orig(self, payload)
    monkeypatch.setattr(LT._Session, "_on_data", corrupt)
    rep = send(rx, [str(src)])
    assert rep["success"] and rep["retried"] == 1
    assert (tmp_path / "dst" / "f.bin").read_bytes() == (src / "f.bin").read_bytes()


# --------------------------------------------------------------------------- 경로 공격
BAD_PATHS = ["../evil.txt", "a/../../evil.txt", "/abs/evil.txt", "C:/evil.txt", "C:evil.txt", "a\\b.txt", "file.txt:stream", "con.txt", "aux", "LPT1.log",
             "dir./x.txt", "name ", "a//b.txt", "./x", "", ".pcmigrate-manifest.jsonl", "x.pcmigrate-part", "a" * 300, "/".join(["d" * 200] * 6), "nul\x00.txt"]


@pytest.mark.parametrize("bad", BAD_PATHS)
def test_validate_rel_rejects(bad):
    with pytest.raises(P.UnsafePath):
        validate_rel(bad)


def test_validate_rel_accepts_normal_names():
    for ok in ("a.txt", "한글/폴더 이름/파일 (1).dwg", "D/프로젝트/A/x.rvt", "a b/c.d.e", "x" * 200):
        assert validate_rel(ok) == ok


@pytest.mark.parametrize("bad", ["../evil.txt", "/abs/evil.txt", "C:/evil.txt", "x.txt:ads", "con.txt", "sub/../../evil.txt", "trail.", "a/b /c.txt"])
def test_receiver_rejects_unsafe_list_and_writes_nothing_outside(rx_factory, tmp_path, bad):
    dst = tmp_path / "box" / "dst"
    rx = rx_factory(dst)
    ch = raw_client(rx)
    raw_plan(ch, 1, 5)
    with pytest.raises(RemoteError) as ei:
        raw_list(ch, [{"p": bad, "s": 5, "d": False}])
    assert ei.value.code == "unsafe_path"
    ch.close()
    wait_state(rx, "waiting")
    assert user_tree(tmp_path / "box") == {} and not (tmp_path / "evil.txt").exists() and not (tmp_path / "box" / "evil.txt").exists()


def test_receiver_rechecks_path_in_file_begin(rx_factory, tmp_path):
    dst = tmp_path / "box" / "dst"
    rx = rx_factory(dst)
    ch = raw_client(rx)
    raw_plan(ch, 1, 5)
    raw_list(ch, [{"p": "ok.txt", "s": 5, "d": False}])
    ch.send_json(P.T_FILE_BEGIN, {"i": 0, "p": "../../evil.txt", "s": 5, "h": sha(b"hello"), "m": 0, "start": 0, "wait": False})
    ch.send(P.T_FILE_DATA, b"hello")
    ch.send_json(P.T_FILE_END, {"i": 0})
    with pytest.raises(RemoteError) as ei:
        ch.recv_json()
    assert ei.value.code == "unsafe_path"
    assert not any((tmp_path / n).exists() for n in ("evil.txt", "box/evil.txt"))


def _make_link(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        return r.returncode == 0
    return False


def test_receiver_refuses_to_write_through_link(rx_factory, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    if not _make_link(dst / "lnk", outside):
        pytest.skip("링크를 만들 수 없는 환경")
    src = make_src(tmp_path / "src", {"lnk/evil.txt": b"evil", "ok.txt": b"ok"})
    rx = rx_factory(dst)
    rep = send(rx, [str(src)])
    assert not (outside / "evil.txt").exists() and list(outside.iterdir()) == []
    assert (dst / "ok.txt").read_bytes() == b"ok"
    assert not rep["success"] and rep["verification"]["missing"] == 1
    assert rep["failed"][0]["path"] == "lnk/evil.txt"


def test_source_links_are_not_followed(rx_factory, tmp_path):
    outside = make_src(tmp_path / "outside", {"secret.txt": b"s"})
    src = make_src(tmp_path / "src", {"a.txt": b"a"})
    if not _make_link(src / "lnk", outside):
        pytest.skip("링크를 만들 수 없는 환경")
    rx = rx_factory()
    rep = send(rx, [str(src)])
    assert rep["success"] and rep["skipped_links"] == 1
    assert list(user_tree(tmp_path / "dst")) == ["a.txt"]


def test_receiver_rejects_oversize_declaration_and_extra_files(rx_factory, tmp_path):
    rx = rx_factory(max_files=10)
    ch = raw_client(rx)
    ch.send_json(P.T_PLAN, {"files": 11, "dirs": 0, "bytes": 1, "max_size": 1, "chunk": 4096, "name": "t"})
    with pytest.raises(RemoteError) as ei:
        ch.recv_json()
    assert ei.value.code == "limit"
    rx2 = rx_factory(tmp_path / "d2")
    ch2 = raw_client(rx2)
    raw_plan(ch2, 1, 5)
    raw_list(ch2, [{"p": "a", "s": 5, "d": False}])
    ch2.send_json(P.T_FILE_BEGIN, {"i": 0, "p": "a", "s": 10 ** 9, "h": sha(b""), "m": 0, "start": 0, "wait": False})
    with pytest.raises(RemoteError):
        ch2.recv_json()


# --------------------------------------------------------------------------- 이어받기 / 취소 / 동시 접속
def test_resume_after_forced_disconnect(rx_factory, tmp_path):
    data = os.urandom(600_000)
    src = make_src(tmp_path / "src", {"big.bin": data, "small.txt": b"s"})
    rx = rx_factory()
    px = Proxy(rx.port, cut_after=300_000)
    try:
        with pytest.raises(LanError) as ei:
            lan_api.run_send(f"127.0.0.1:{px.listen_port}", rx.code, sources=[str(src)], options={"chunk": 4096})
        assert ei.value.code == "disconnected" and "다시 실행" in str(ei.value)
    finally:
        px.close()
    wait_state(rx, "waiting")
    part = tmp_path / "dst" / "big.bin.pcmigrate-part"
    assert part.exists() and 0 < part.stat().st_size < len(data)
    rep = send(rx, [str(src)])
    assert rep["success"] and rep["resumed_files"] == 1
    assert (tmp_path / "dst" / "big.bin").read_bytes() == data and not part.exists()
    assert rep["verification"]["exact"] == 2


def test_resume_with_corrupt_part_prefix_is_caught_by_full_hash(rx_factory, tmp_path):
    """이어받는 앞부분이 손상돼 있어도 해시는 처음부터 검증하므로 잡아내고 처음부터 다시 받는다."""
    data = os.urandom(100_000)
    src = make_src(tmp_path / "src", {"big.bin": data})
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "big.bin.pcmigrate-part").write_bytes(b"\x00" * 40_000)  # 엉뚱한 앞부분
    rx = rx_factory(dst)
    rep = send(rx, [str(src)])
    assert rep["success"] and rep["retried"] == 1 and rep["resumed_files"] >= 1
    assert (dst / "big.bin").read_bytes() == data


def test_cancel_stops_sender_and_receiver_keeps_waiting(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {f"f{i}.bin": os.urandom(50_000) for i in range(20)})
    rx = rx_factory()
    tok = CancelToken()
    seen = {"n": 0}

    def cancel():
        seen["n"] += 1
        return seen["n"] > 150  # 전송 도중에 취소
    with pytest.raises(Cancelled):
        lan_api.run_send(f"127.0.0.1:{rx.port}", rx.code, sources=[str(src)], options={"chunk": 4096}, cancel=cancel)
    wait_state(rx, "waiting")
    assert not rx.done
    assert any(e["type"] == "cancelled" for e in rx.events)
    assert len(user_tree(src)) == 20  # 원본은 그대로


def test_cancel_via_checkpoint_binding(rx_factory, tmp_path):
    from pcmigrate import cancel as cm
    src = make_src(tmp_path / "src", {"a.bin": os.urandom(10_000)})
    rx = rx_factory()
    tok = CancelToken()
    tok.cancel()
    with cm.bind(tok):
        with pytest.raises(Cancelled):
            send(rx, [str(src)])


def test_second_sender_refused_while_first_is_connected(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {"a.txt": b"a"})
    rx = rx_factory()
    first = raw_client(rx)  # 인증된 채로 대기
    with pytest.raises(LanError) as ei:
        send(rx, [str(src)])
    assert ei.value.code == "busy"
    first.close()
    wait_state(rx, "waiting")
    assert send(rx, [str(src)])["success"]


def test_stale_connection_is_taken_over(rx_factory, tmp_path):
    src = make_src(tmp_path / "src", {"a.txt": b"a"})
    rx = rx_factory(takeover_idle=0.0)
    first = raw_client(rx)
    time.sleep(0.05)
    assert send(rx, [str(src)])["success"]
    first.close()


def test_stop_releases_port(rx_factory, tmp_path):
    rx = rx_factory()
    port = rx.port
    rx.stop()
    assert rx.state == "stopped"
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    finally:
        s.close()


def test_port_in_use_is_friendly(rx_factory, tmp_path):
    rx = rx_factory()
    with pytest.raises(LanError) as ei:
        lan_api.start_receiver(tmp_path / "other", rx.port, discovery=False)
    assert ei.value.code == "port_in_use" and "--port" in str(ei.value)


# --------------------------------------------------------------------------- 디스크/FAT32
def test_space_check_rejects_before_writing(rx_factory, tmp_path, monkeypatch):
    src = make_src(tmp_path / "src", {"a.bin": os.urandom(10_000)})
    monkeypatch.setattr(fs, "free_space", lambda p: 100)
    rx = rx_factory()
    with pytest.raises(LanError) as ei:
        send(rx, [str(src)])
    assert ei.value.code == "space" and "여유 공간" in str(ei.value)
    assert user_tree(tmp_path / "dst") == {}


def test_fat32_with_huge_file_is_refused_with_guidance(rx_factory, tmp_path, monkeypatch):
    real = fs.describe_destination
    monkeypatch.setattr(fs, "describe_destination", lambda p: dataclasses.replace(real(p), fs="FAT32"))
    rx = rx_factory()
    ch = raw_client(rx)
    ch.send_json(P.T_PLAN, {"files": 1, "dirs": 0, "bytes": 5 * 1024 ** 3, "max_size": 5 * 1024 ** 3, "chunk": 4096, "name": "t"})
    with pytest.raises(RemoteError) as ei:
        ch.recv_json()
    assert ei.value.code == "fat32" and "NTFS" in str(ei.value)


# --------------------------------------------------------------------------- 주소/경고/검색/진단
def test_classify_ip_and_warnings(rx_factory, tmp_path):
    assert lan_link.classify_ip("192.168.0.5") == "private" and lan_link.classify_ip("10.1.2.3") == "private"
    assert lan_link.classify_ip("172.16.0.1") == "private"
    assert lan_link.classify_ip("169.254.10.20") == "linklocal"
    assert lan_link.classify_ip("127.0.0.1") == "loopback"
    assert lan_link.classify_ip("8.8.8.8") == "public" and lan_link.classify_ip("garbage") == "public"
    with pytest.raises(LanError) as ei:
        lan_api.start_receiver(tmp_path / "d", 0, bind="8.8.8.8", discovery=False)
    assert ei.value.code == "public_address" and "암호화" in str(ei.value)
    A = lan_link.LocalAddress
    rx = rx_factory(addresses=[A("192.168.0.5", "private"), A("169.254.9.9", "linklocal"), A("8.8.4.4", "public")])
    assert "169.254.9.9:%d" % rx.port in rx.addresses and "192.168.0.5:%d" % rx.port in rx.addresses
    assert "8.8.4.4:%d" % rx.port not in rx.addresses
    text = " ".join(rx.warnings)
    assert "암호화" in text and "방화벽" in text and "공인" in text


def test_receiver_binds_all_adapters_by_default_and_lists_linklocal(rx_factory):
    A = lan_link.LocalAddress
    rx = rx_factory(addresses=[A("169.254.1.2", "linklocal")])
    assert rx.bind == "0.0.0.0" and rx.addresses == [f"169.254.1.2:{rx.port}"]
    assert rx.address_kinds[rx.addresses[0]] == "linklocal"


def test_send_to_public_address_needs_explicit_consent(tmp_path):
    src = make_src(tmp_path / "src", {"a": b"a"})
    with pytest.raises(LanError) as ei:
        lan_api.run_send("8.8.8.8:47830", "123456", sources=[str(src)], options={"allow_public": False})
    assert ei.value.code == "public_address"


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_udp_discovery_loopback(tmp_path):
    port = _free_udp_port()
    rx = lan_api.start_receiver(tmp_path / "d", 0, discovery=True, discovery_port=port, name="새PC-시험", fail_delay=0)
    try:
        assert rx.discovery_active
        found = lan_api.discover_receivers(1.0, port=port, targets=["127.0.0.1"])
        assert [(f.name, f.ip, f.port) for f in found] == [("새PC-시험", "127.0.0.1", rx.port)]
        assert "123" not in json.dumps([f.__dict__ for f in found]) or rx.code not in json.dumps([f.__dict__ for f in found])  # 코드는 답에 없음
        host, p = lan_api.resolve_target("자동", timeout=1.0, discovery_port=port, targets=["127.0.0.1"])
        assert (host, p) == ("127.0.0.1", rx.port)
    finally:
        rx.stop()


def test_discovery_none_found_gives_manual_hint():
    port = _free_udp_port()
    assert lan_api.discover_receivers(0.3, port=port, targets=["127.0.0.1"]) == []
    with pytest.raises(LanError) as ei:
        lan_api.resolve_target("자동", timeout=0.3, discovery_port=port, targets=["127.0.0.1"])
    assert ei.value.code == "no_receiver" and "직접 입력" in str(ei.value)


def test_discovery_multiple_requires_choice(tmp_path):
    port = _free_udp_port()
    found = [lan_link.Receiver("a", "127.0.0.1", 1), lan_link.Receiver("b", "127.0.0.2", 2)]
    import pcmigrate.lan_api as la
    orig = la.discover
    la.discover = lambda *a, **k: found
    try:
        with pytest.raises(LanError) as ei:
            la.resolve_target("auto")
        assert ei.value.code == "multiple_receivers"
        assert la.resolve_target("auto", choose=lambda f: f[1]) == ("127.0.0.2", 2)
    finally:
        la.discover = orig


def test_parse_target():
    assert lan_link.parse_target("192.168.0.2") == ("192.168.0.2", P.DEFAULT_PORT)
    assert lan_link.parse_target("192.168.0.2:5000") == ("192.168.0.2", 5000)
    for bad in ("", "a b", "x:abc", "x:0", "x:99999"):
        with pytest.raises(LanError):
            lan_link.parse_target(bad)


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_diagnose_outcomes(rx_factory):
    rx = rx_factory()
    d = lan_api.run_diagnose(f"127.0.0.1:{_closed_port()}")
    assert not d.ok and d.code == "port_refused" and d.port_open is False and "receive" in d.summary
    d = lan_api.run_diagnose(f"127.0.0.1:{rx.port}")
    assert d.ok and d.port_open is True
    wrong = "000000" if rx.code != "000000" else "111111"
    d = lan_api.run_diagnose(f"127.0.0.1:{rx.port}", wrong)
    assert not d.ok and d.code == "bad_code" and "코드" in d.summary
    d = lan_api.run_diagnose(f"127.0.0.1:{rx.port}", rx.code)
    assert d.ok and "코드도 맞습니다" in d.summary
    assert lan_api.run_diagnose("a b").code == "bad_target"
    assert "\n" not in d.summary


def test_diagnose_subnet_logic():
    A = lan_link.LocalAddress
    locs = [A("192.168.1.5", "private")]
    assert lan_link.same_subnet("192.168.1.77", locs) is True
    assert lan_link.same_subnet("192.168.2.77", locs) is False
    assert lan_link.same_subnet("10.0.0.1", locs) is False
    assert lan_link.same_subnet("169.254.3.4", [A("169.254.9.9", "linklocal")]) is True
    assert lan_link.same_subnet("1.2.3.4", []) is None


def test_diagnose_expired_and_locked_codes(rx_factory):
    rx = rx_factory(ttl=0.05)
    time.sleep(0.15)
    assert lan_api.run_diagnose(f"127.0.0.1:{rx.port}", rx.code).code == "expired"
    rx2 = rx_factory(max_failures=1)
    lan_api.run_diagnose(f"127.0.0.1:{rx2.port}", "000000" if rx2.code != "000000" else "111111")
    assert lan_api.run_diagnose(f"127.0.0.1:{rx2.port}", rx2.code).code == "locked"


# --------------------------------------------------------------------------- CLI
def test_cli_argument_errors(tmp_path, capsys):
    src = make_src(tmp_path / "src", {"a": b"a"})
    assert cli.main(["send", "127.0.0.1:1", str(src)]) == 2  # 코드 없음(비대화형)
    assert cli.main(["send", "127.0.0.1:1", "--code", "123456"]) == 2  # 원본 없음
    assert cli.main(["send", "127.0.0.1:1", "--code", "123456", "--from-selection", "x.json", str(src)]) == 2
    assert cli.main(["send", "127.0.0.1:1", "--code", "12", str(src)]) == 2
    assert cli.main(["send", "127.0.0.1:1", "--code", "123456", "--exclude-backup-copies", str(src)]) == 2
    assert cli.main(["receive", "--code", "abc", "--dest", str(tmp_path / "d")]) == 2
    assert cli.main(["receive", "--bind", "8.8.8.8", "--dest", str(tmp_path / "d")]) == 2
    out = capsys.readouterr().err
    assert "암호화" in out
    rc = cli.main(["send", f"127.0.0.1:{_closed_port()}", "--code", "123456", str(src)])
    assert rc == 1 and "diagnose" in capsys.readouterr().err


def test_cli_diagnose(rx_factory, capsys):
    rx = rx_factory()
    assert cli.main(["send", f"127.0.0.1:{rx.port}", "--diagnose", "--code", rx.code]) == 0
    assert "[정상]" in capsys.readouterr().out
    assert cli.main(["send", f"127.0.0.1:{_closed_port()}", "--diagnose"]) == 1
    assert "[문제]" in capsys.readouterr().out


def test_cli_send_in_process_and_report(rx_factory, tmp_path, capsys):
    src = make_src(tmp_path / "src", {"a.txt": b"a" * 100})
    rx = rx_factory()
    rep = tmp_path / "rep.json"
    rc = cli.main(["send", f"127.0.0.1:{rx.port}", "--code", rx.code, "--chunk-kb", "4", "--report", str(rep), str(src)])
    out = capsys.readouterr()
    assert rc == 0 and "정확함 1개 / 내용 다름 0개 / 누락 0개" in out.out
    assert json.loads(rep.read_text(encoding="utf-8"))["verification"]["exact"] == 1


# --------------------------------------------------------------------------- 실제 프로세스 간 통신
def test_real_process_receive_and_send(tmp_path):
    src = make_src(tmp_path / "src", {"도면/a.dwg": os.urandom(70_000), "b b.txt": b"hello", "e/z": b""}, dirs=["빈 폴더"])
    dst = tmp_path / "받은 폴더"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    code = "482913"
    rcv = subprocess.Popen([sys.executable, "-m", "pcmigrate", "receive", "--dest", str(dst), "--port", "0", "--code", code, "--no-discovery"],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        port = None
        lines = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = rcv.stdout.readline()
            if not line:
                break
            lines.append(line)
            m = re.match(r"\s+(\d+\.\d+\.\d+\.\d+):(\d+)\s", line)
            if m and port is None:
                port = int(m.group(2))
            if "구 PC 에서:" in line:
                break
        assert port, lines
        snd = subprocess.run([sys.executable, "-m", "pcmigrate", "send", f"127.0.0.1:{port}", "--code", code, "--chunk-kb", "16", str(src)],
                             env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert snd.returncode == 0, snd.stdout + snd.stderr
        assert "정확함 3개 / 내용 다름 0개 / 누락 0개" in snd.stdout
        rest = rcv.communicate(timeout=60)[0]
        assert rcv.returncode == 0
        assert "[완료]" in rest and "pcmigrate verify" in rest
    finally:
        if rcv.poll() is None:
            rcv.kill()
    assert user_tree(dst) == user_tree(src)
    assert (dst / "빈 폴더").is_dir()
    ver = subprocess.run([sys.executable, "-m", "pcmigrate", "verify", "--manifest", str(dst / ".pcmigrate-manifest.jsonl"), "--target", str(dst)],
                         env=env, capture_output=True, text=True, encoding="utf-8")
    assert ver.returncode == 0, ver.stdout + ver.stderr


# --------------------------------------------------------------------------- 문서
def test_docs_mention_lan_direct_and_stay_synced():
    md = (ROOT / "docs" / "field-guide.md").read_text(encoding="utf-8")
    ht = (ROOT / "docs" / "field-guide.html").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("pcmigrate receive", "pcmigrate send", "암호화", "방화벽"):
        assert needle in md and needle in ht and needle in readme, needle
