"""LAN 직접 전송의 연결 보조: 이 PC 의 주소 목록과 사설/링크로컬/공인 구분, UDP 브로드캐스트 검색, 연결 진단.

- 도구는 방화벽/네트워크 설정을 바꾸지 않는다(읽기와 접속 시험만).
- 검색은 고정 UDP 포트(DISCOVERY_PORT)로 같은 서브넷에 브로드캐스트하고, 켜져 있는 receive 가 이름/포트를 답한다.
  답에는 코드를 넣지 않는다. 검색이 안 되면(공유기가 막거나 서브넷이 다름) 수신 PC 에 표시된 주소를 직접 입력한다.
- 랜선 직결(링크로컬 169.254.x.x)도 주소 목록과 검색 대상에 포함한다.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import envpaths
from .lan_protocol import (DEFAULT_PORT, DISCOVERY_PORT, PROTO_VERSION, AuthError, Channel, LanError, RemoteError, VersionMismatch,
                           client_handshake, normalize_code)

PRIVATE, LINKLOCAL, LOOPBACK, PUBLIC = "private", "linklocal", "loopback", "public"
DISCOVER_MAGIC = b"PCMIGRATE-DISCOVER\x01"
REPLY_MAGIC = "pcmigrate-lan"


def classify_ip(ip: str) -> str:
    """사설(RFC1918) / 링크로컬(169.254) / 루프백 / 공인. 파싱 못 하면 공인(보수적)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return PUBLIC
    if a.is_loopback:
        return LOOPBACK
    if a.is_link_local:
        return LINKLOCAL
    if a.is_private:
        return PRIVATE
    return PUBLIC


def is_safe_lan_address(ip: str) -> bool:
    if ip in ("0.0.0.0", ""):
        return True  # 모든 어댑터: 별도로 어댑터 주소를 점검한다
    return classify_ip(ip) != PUBLIC


@dataclass(frozen=True)
class LocalAddress:
    ip: str
    kind: str


def local_addresses(*, include_loopback: bool = False) -> list[LocalAddress]:
    """이 PC 의 IPv4 주소 목록(사설/링크로컬/공인 구분). 표준 라이브러리만 쓰며 외부로 패킷을 보내지 않는다."""
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    try:  # 라우팅 표만 확인(connect 는 UDP 라 패킷이 나가지 않는다)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    out: list[LocalAddress] = []
    seen: set[str] = set()
    for ip in found:
        if ip in seen or ip.startswith("0."):
            continue
        seen.add(ip)
        kind = classify_ip(ip)
        if kind == LOOPBACK and not include_loopback:
            continue
        out.append(LocalAddress(ip, kind))
    if include_loopback and "127.0.0.1" not in seen:
        out.append(LocalAddress("127.0.0.1", LOOPBACK))
    order = {PRIVATE: 0, LINKLOCAL: 1, LOOPBACK: 2, PUBLIC: 3}
    return sorted(out, key=lambda a: order[a.kind])


KIND_TEXT = {PRIVATE: "사설 네트워크", LINKLOCAL: "랜선 직결(링크로컬)", LOOPBACK: "이 PC 안(시험용)", PUBLIC: "공인 주소(주의)"}


def public_warning(ip: str) -> str:
    return (f"{ip} 는 공인(인터넷) 주소입니다. 이 도구는 전송 내용을 암호화하지 않으므로 사설 LAN(같은 공유기/스위치, 랜선 직결)에서만 쓰세요. "
            "그래도 쓰려면 명시적으로 허용(--allow-public)해야 합니다.")


def format_target(ip: str, port: int) -> str:
    return f"{ip}:{port}"


def parse_target(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """'주소', '주소:포트' (IPv4 또는 호스트 이름). 잘못되면 LanError."""
    t = text.strip()
    host, port = t, default_port
    if t.count(":") == 1:
        host, _, p = t.partition(":")
        try:
            port = int(p)
        except ValueError:
            raise LanError(f"포트 번호가 올바르지 않습니다: {text!r}", "bad_target") from None
    if not host or any(c.isspace() for c in host):
        raise LanError(f"접속 주소가 올바르지 않습니다: {text!r} (예: 192.168.0.12:{DEFAULT_PORT})", "bad_target")
    if not 1 <= port <= 65535:
        raise LanError(f"포트 번호는 1~65535 입니다: {text!r}", "bad_target")
    return host, port


def default_receive_dir(now: datetime | None = None) -> str:
    """수신 폴더 기본값: 바탕화면 아래 새 폴더(만들지는 않는다)."""
    home = envpaths.lookup("USERPROFILE") or os.path.expanduser("~")
    desktop = Path(home) / "Desktop"
    base = desktop if desktop.is_dir() else Path(home)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return str(base / f"PC이전_받은파일_{stamp}")


# --------------------------------------------------------------------------- 검색
@dataclass(frozen=True)
class Receiver:
    name: str
    ip: str
    port: int
    kind: str = PRIVATE

    @property
    def address(self) -> str:
        return format_target(self.ip, self.port)


class DiscoveryResponder:
    """수신 측: UDP 검색 요청에 이름/TCP 포트를 답한다. 포트를 못 열면 start() 가 False(검색만 안 될 뿐 전송은 된다)."""

    def __init__(self, tcp_port: int, name: str, *, bind: str = "0.0.0.0", port: int = DISCOVERY_PORT) -> None:
        self.tcp_port, self.name, self.bind, self.port = tcp_port, name, bind, port
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.active = True  # 전송 중에는 False 로 바꿔 다른 송신이 접속하려 하지 않게 한다

    def start(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.bind, self.port))
            s.settimeout(0.3)
        except OSError:
            return False
        self._sock = s
        self._thread = threading.Thread(target=self._run, name="pcmigrate-discovery", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        reply = json.dumps({"magic": REPLY_MAGIC, "ver": PROTO_VERSION, "name": self.name, "port": self.tcp_port}, ensure_ascii=False).encode("utf-8")
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(512)  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                return
            if data.startswith(DISCOVER_MAGIC) and self.active:
                try:
                    self._sock.sendto(reply, addr)  # type: ignore[union-attr]
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.5)


def broadcast_targets(addresses: list[LocalAddress] | None = None) -> list[str]:
    """검색 패킷을 보낼 브로드캐스트 주소: 전체(255.255.255.255) + 어댑터별 /24 (링크로컬은 169.254.255.255)."""
    out = ["255.255.255.255"]
    for a in addresses if addresses is not None else local_addresses():
        if a.kind == LINKLOCAL:
            b = "169.254.255.255"
        elif a.kind == PRIVATE:
            b = str(ipaddress.ip_network(f"{a.ip}/24", strict=False).broadcast_address)
        else:
            continue
        if b not in out:
            out.append(b)
    return out


def discover(timeout: float = 2.0, *, port: int = DISCOVERY_PORT, targets: list[str] | None = None) -> list[Receiver]:
    """같은 네트워크에서 켜져 있는 receive 를 찾는다. 못 찾으면 빈 목록(호출자가 수동 주소 안내)."""
    found: dict[tuple[str, int], Receiver] = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return []
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("0.0.0.0", 0))
        for t in targets if targets is not None else broadcast_targets():
            try:
                s.sendto(DISCOVER_MAGIC, (t, port))
            except OSError:
                continue
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            s.settimeout(left)
            try:
                data, addr = s.recvfrom(1024)
            except socket.timeout:
                break
            except OSError:
                continue  # Windows: 이전 송신의 ICMP 거부가 recv 오류로 올 수 있음
            try:
                obj = json.loads(data.decode("utf-8"))
                if obj.get("magic") != REPLY_MAGIC or obj.get("ver") != PROTO_VERSION:
                    continue
                tcp = int(obj["port"])
                name = str(obj.get("name", ""))[:64]
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                continue
            if 1 <= tcp <= 65535:
                found[(addr[0], tcp)] = Receiver(name, addr[0], tcp, classify_ip(addr[0]))
    finally:
        s.close()
    return sorted(found.values(), key=lambda r: (r.name.lower(), r.ip))


NO_DISCOVERY_HELP = ("같은 네트워크에서 켜진 수신 PC 를 찾지 못했습니다. 수신 PC 의 `pcmigrate receive` 화면에 표시된 접속 주소를 "
                     "직접 입력하세요(예: pcmigrate send 192.168.0.12:47830 --code 123456 ...). 공유기가 기기 간 통신을 막았거나, "
                     "서브넷이 다르거나, 방화벽(네트워크 프로필이 '공용')이 막았을 수 있습니다.")


# --------------------------------------------------------------------------- 진단
@dataclass
class Diagnosis:
    ok: bool
    code: str  # ok | bad_target | subnet | port_refused | port_timeout | unreachable | bad_code | version | locked | expired | busy | closed | error
    summary: str  # 한 줄 요약(사용자에게 그대로)
    details: list[str] = field(default_factory=list)
    port_open: bool | None = None
    same_subnet: bool | None = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "code": self.code, "summary": self.summary, "details": list(self.details),
                "port_open": self.port_open, "same_subnet": self.same_subnet}


def same_subnet(ip: str, locals_: list[LocalAddress]) -> bool | None:
    """대상 IP 가 이 PC 어댑터 중 하나와 같은 서브넷(/24, 링크로컬은 둘 다 링크로컬)이면 True, 아니면 False, 판단 불가는 None."""
    try:
        target = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if target.is_loopback:
        return True
    if not locals_:
        return None
    for a in locals_:
        if classify_ip(ip) == LINKLOCAL and a.kind == LINKLOCAL:
            return True
        try:
            if target in ipaddress.ip_network(f"{a.ip}/24", strict=False):
                return True
        except ValueError:
            continue
    return False


def diagnose(target: str, code: str | None = None, *, timeout: float = 3.0, locals_: list[LocalAddress] | None = None) -> Diagnosis:
    """연결이 안 될 때 원인을 한 줄로 요약한다. 접속 시험만 하며 어떤 설정도 바꾸지 않는다.

    코드를 주면 핸드셰이크(인증)까지 시험한다. 틀린 코드는 수신 측 실패 횟수에 들어가므로 한두 번만 시험한다.
    """
    try:
        host, port = parse_target(target)
    except LanError as e:
        return Diagnosis(False, "bad_target", str(e))
    details: list[str] = []
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return Diagnosis(False, "bad_target", f"{host} 를 찾을 수 없습니다. 수신 PC 화면의 숫자 주소(예: 192.168.0.12)를 입력하세요.")
    locs = local_addresses() if locals_ is None else locals_
    sub = same_subnet(ip, locs)
    kind = classify_ip(ip)
    if kind == PUBLIC:
        details.append(public_warning(ip))
    if sub is False:
        details.append("이 PC 와 서브넷이 다른 것으로 보입니다(같은 공유기/스위치에 연결했는지, 한쪽이 Wi-Fi 이고 다른 쪽이 유선인지 확인).")
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
    except ConnectionRefusedError:
        return Diagnosis(False, "port_refused", f"{ip}:{port} 에 응답은 있으나 받는 프로그램이 없습니다. 수신 PC 에서 pcmigrate receive 가 켜져 있는지, 포트 번호가 같은지 확인하세요.",
                         details, port_open=False, same_subnet=sub)
    except (socket.timeout, TimeoutError):
        if sub is False:
            return Diagnosis(False, "subnet", f"{ip} 에 닿지 않습니다: 서브넷이 다른 것 같습니다. 두 PC 를 같은 공유기/스위치에 연결하거나 랜선으로 직결하세요.",
                             details, port_open=False, same_subnet=False)
        return Diagnosis(False, "port_timeout", f"{ip}:{port} 가 응답하지 않습니다: 수신 PC 의 Windows 방화벽이 막았거나 네트워크 프로필이 '공용'일 수 있습니다"
                         "(수신 PC 에서 처음 실행할 때 뜨는 방화벽 창에서 '개인 네트워크 허용'을 눌렀는지 확인).", details, port_open=False, same_subnet=sub)
    except OSError as e:
        return Diagnosis(False, "unreachable", f"{ip}:{port} 에 연결하지 못했습니다({e.strerror or type(e).__name__}). 케이블과 주소를 확인하세요.",
                         details, port_open=False, same_subnet=sub)
    if not code:
        s.close()
        return Diagnosis(True, "ok", f"{ip}:{port} 의 포트가 열려 있습니다. 코드로 접속을 시험하려면 --code 를 함께 주세요.", details, True, sub)
    ch = Channel(s)
    s.settimeout(timeout * 3)
    try:
        client_handshake(ch, normalize_code(code))
        return Diagnosis(True, "ok", f"{ip}:{port} 에 연결되고 코드도 맞습니다. 바로 전송할 수 있습니다.", details, True, sub)
    except VersionMismatch as e:
        return Diagnosis(False, "version", str(e), details, True, sub)
    except (AuthError, RemoteError) as e:
        c = getattr(e, "code", "auth")
        text = {"auth": "연결은 되지만 코드가 맞지 않습니다. 수신 PC 화면의 6자리 코드를 다시 확인하세요.",
                "locked": "코드가 잠겼습니다(여러 번 틀림). 수신 PC 에서 receive 를 다시 시작해 새 코드를 받으세요.",
                "expired": "코드가 만료되었습니다. 수신 PC 에서 receive 를 다시 시작해 새 코드를 받으세요.",
                "closed": "이 코드의 전송은 이미 끝났습니다. 수신 PC 에서 receive 를 다시 시작하세요.",
                "busy": "연결은 되지만 수신 PC 가 다른 송신과 전송 중입니다."}.get(c, str(e))
        return Diagnosis(False, {"auth": "bad_code"}.get(c, c) if c in ("auth", "locked", "expired", "closed", "busy", "version") else "error",
                         text, details, True, sub)
    except LanError as e:
        return Diagnosis(False, "error", f"연결은 되지만 인증 중 문제가 생겼습니다: {e}", details, True, sub)
    finally:
        ch.close()
