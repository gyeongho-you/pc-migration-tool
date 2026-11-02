"""S4: LAN 수신 쪽의 동시 연결 상한과 연결별 사전 인증 총 제한시간. 루프백 포트 0 만 쓴다."""
from __future__ import annotations

import socket
import threading
import time

import pytest

from pcmigrate import lan_api, lan_protocol as P, lan_transfer as LT
from pcmigrate.cancel import Cancelled
from pcmigrate.lan_protocol import LanError
from test_lan_transfer import make_src, raw_client, send  # noqa: F401  (같은 보조 함수 재사용)


@pytest.fixture(autouse=True)
def _fast_kdf(monkeypatch):
    monkeypatch.setattr(P, "KDF_ITERATIONS", 1000)


@pytest.fixture
def rx_factory(tmp_path):
    made = []

    def make(**kw):
        kw.setdefault("fail_delay", 0)
        kw.setdefault("discovery", False)
        kw.setdefault("addresses", [])
        rx = lan_api.start_receiver(tmp_path / "dst", 0, kw.pop("on_event", None), **kw)
        made.append(rx)
        return rx

    yield made, make
    for r in made:
        r.stop()


def _closed_by_peer(sock: socket.socket, timeout: float = 5.0) -> bool:
    sock.settimeout(timeout)
    try:
        return sock.recv(1) == b""
    except ConnectionResetError:
        return True
    except socket.timeout:
        return False


def test_defaults():
    assert LT.MAX_CONNECTIONS == 8 and LT.PREAUTH_TIMEOUT == 30.0 and LT.MAX_UNAUTH_PER_IP == 3 and LT.RESERVED_AUTHED == 2


def _wait_closed_count(socks, n, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        closed = 0
        for s in socks:
            s.settimeout(0.05)
            try:
                if s.recv(1) == b"":
                    closed += 1
            except ConnectionResetError:
                closed += 1
            except (socket.timeout, OSError):
                pass
        if closed >= n:
            return closed
    return closed


def test_per_ip_unauth_cap_evicts_oldest_connection(rx_factory):
    _made, make = rx_factory
    rx = make(preauth_timeout=0)
    held = [socket.create_connection(("127.0.0.1", rx.port), timeout=5) for _ in range(LT.MAX_UNAUTH_PER_IP)]
    time.sleep(0.3)
    newest = socket.create_connection(("127.0.0.1", rx.port), timeout=5)  # 같은 IP 의 4번째: 가장 오래된 것을 끊고 이것은 받는다
    try:
        assert _wait_closed_count(held[:1], 1) == 1  # 가장 오래된 연결이 끊김
        time.sleep(0.2)
        newest.settimeout(0.3)
        with pytest.raises(socket.timeout):  # 새 연결은 열려 있다
            newest.recv(1)
        ch = raw_client(rx)  # 그리고 정상 송신도 연결된다
        ch.close()
    finally:
        for s in held + [newest]:
            s.close()


def test_reserved_slots_keep_room_for_authenticated(rx_factory):
    _made, make = rx_factory
    rx = make(max_connections=4, preauth_timeout=0)  # 예약 2 -> 인증 전 연결은 2개까지
    assert LT.RESERVED_AUTHED == 2
    idle = [socket.create_connection(("127.0.0.1", rx.port), timeout=5) for _ in range(3)]
    time.sleep(0.3)
    try:
        ch = raw_client(rx)  # 인증 전 연결이 가득 차도 새 정상 송신이 자리를 얻는다
        ch.close()
        assert len(rx._conns) <= 4
    finally:
        for s in idle:
            s.close()


def test_attacker_loop_does_not_block_real_send(rx_factory, tmp_path):
    """공격자가 계속 8개씩 열고 닫아도(루프백 포트 0) 정상 전송이 성공한다."""
    _made, make = rx_factory
    rx = make(preauth_timeout=0)
    stop = threading.Event()

    def attack():
        while not stop.is_set():
            socks = []
            for _ in range(8):
                try:
                    socks.append(socket.create_connection(("127.0.0.1", rx.port), timeout=2))
                except OSError:
                    pass
            time.sleep(0.4)
            for s in socks:
                s.close()

    th = threading.Thread(target=attack, daemon=True)
    th.start()
    try:
        time.sleep(0.2)
        src = make_src(tmp_path / "src", {"a.txt": b"hello" * 1000})
        assert send(rx, sources=[str(src)], busy_wait=0.05) is not None  # 수신 쪽은 한 번 받으면 끝나므로 1회
        assert (tmp_path / "dst" / "a.txt").read_bytes() == b"hello" * 1000
    finally:
        stop.set()
        th.join(5)


def _closing_server():
    """연결을 받자마자 닫는 가짜 수신 쪽(바쁜 수신 쪽 흉내). (소켓, 연결 수 리스트, 정지 이벤트)"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    count, stop = [], threading.Event()

    def loop():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            count.append(1)
            c.close()

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return srv, count, stop, th


def test_sender_retries_then_reports_busy_or_cable(tmp_path):
    srv, count, stop, th = _closing_server()
    try:
        src = make_src(tmp_path / "src", {"a.txt": b"x"})
        with pytest.raises(LanError) as ei:
            lan_api.run_send(f"127.0.0.1:{srv.getsockname()[1]}", "123456", sources=[str(src)],
                             options={"chunk": 4096, "busy_wait": 0.01, "busy_retries": 2})
        assert ei.value.code == "receiver_busy"
        assert "바쁘거나 케이블" in str(ei.value) and "연결을 확인하세요" not in str(ei.value).split("케이블")[0]
        assert len(count) == 3  # 처음 1번 + 다시 시도 2번
    finally:
        stop.set()
        srv.close()
        th.join(2)


def test_sender_wait_is_cancellable(tmp_path):
    srv, count, stop, th = _closing_server()
    try:
        src = make_src(tmp_path / "src", {"a.txt": b"x"})
        calls = []

        def cancel():
            calls.append(1)
            return len(count) >= 1 and len(calls) > 3

        t0 = time.monotonic()
        with pytest.raises(Cancelled):
            lan_api.run_send(f"127.0.0.1:{srv.getsockname()[1]}", "123456", sources=[str(src)], cancel=cancel,
                             options={"chunk": 4096, "busy_wait": 5.0, "busy_retries": 3})
        assert time.monotonic() - t0 < 4  # 5초 대기를 기다리지 않고 취소된다
    finally:
        stop.set()
        srv.close()
        th.join(2)


def test_idle_connection_is_closed_after_preauth_timeout(rx_factory):
    events = []
    _made, make = rx_factory
    rx = make(preauth_timeout=0.5, on_event=events.append)
    s = socket.create_connection(("127.0.0.1", rx.port), timeout=5)
    try:
        t0 = time.monotonic()
        assert _closed_by_peer(s, timeout=5)
        assert time.monotonic() - t0 < 4
    finally:
        s.close()
    time.sleep(0.2)
    assert any(e.get("type") == "protocol_error" and "제한시간" in e.get("message", "") for e in events), events


def test_slow_drip_connection_is_cut_by_total_timeout(rx_factory):
    """글자를 조금씩 보내 소켓 읽기 제한(15초)을 계속 늘려도 '총' 제한시간에서 끊긴다."""
    _made, make = rx_factory
    rx = make(preauth_timeout=0.8)
    s = socket.create_connection(("127.0.0.1", rx.port), timeout=5)
    try:
        t0 = time.monotonic()
        closed = False
        while time.monotonic() - t0 < 5 and not closed:
            try:
                s.sendall(b"\x00")
            except OSError:
                closed = True
                break
            time.sleep(0.2)
        assert closed or _closed_by_peer(s, timeout=2)
        assert time.monotonic() - t0 < 4
    finally:
        s.close()


def test_authenticated_connection_survives_past_preauth_timeout(rx_factory):
    from test_lan_transfer import raw_plan
    _made, make = rx_factory
    rx = make(preauth_timeout=0.5)
    ch = raw_client(rx)  # 인증 완료
    try:
        time.sleep(1.2)  # 사전 인증 제한시간(0.5초)이 지난 뒤에도 연결이 살아 있어야 한다
        info = raw_plan(ch, 0, 0)
        assert "chunk" in info
    finally:
        ch.close()


def test_normal_send_still_works_with_defaults(rx_factory, tmp_path):
    _made, make = rx_factory
    rx = make()
    src = make_src(tmp_path / "src", {"a.txt": b"hello" * 1000})
    assert send(rx, sources=[str(src)]) is not None
    assert (tmp_path / "dst").exists()
