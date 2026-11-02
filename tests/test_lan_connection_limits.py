"""S4: LAN 수신 쪽의 동시 연결 상한과 연결별 사전 인증 총 제한시간. 루프백 포트 0 만 쓴다."""
from __future__ import annotations

import socket
import time

import pytest

from pcmigrate import lan_api, lan_protocol as P, lan_transfer as LT
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
    assert LT.MAX_CONNECTIONS == 8 and LT.PREAUTH_TIMEOUT == 30.0


def test_connections_over_limit_are_closed_immediately(rx_factory):
    _made, make = rx_factory
    rx = make(max_connections=2, preauth_timeout=0)
    held = [socket.create_connection(("127.0.0.1", rx.port), timeout=5) for _ in range(2)]  # 아무것도 보내지 않고 붙들기
    time.sleep(0.3)
    extra = socket.create_connection(("127.0.0.1", rx.port), timeout=5)
    try:
        assert _closed_by_peer(extra)
    finally:
        extra.close()
    # 붙들던 연결이 끝나면 다시 받는다
    for s in held:
        s.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ch = None
        try:
            ch = raw_client(rx)
            break
        except Exception:  # noqa: BLE001 - 정리가 끝날 때까지 잠깐 재시도
            time.sleep(0.1)
    assert ch is not None
    ch.close()


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
