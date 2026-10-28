"""랜선 직결용 이더넷 주소 임시 맞추기(stage39). 화면 없는 모듈이며 시험에서는 전부 가짜 runner 를 쓴다.

- 조회(읽기 전용): PowerShell 고정 스크립트 하나로 어댑터/주소/게이트웨이/DNS/경로를 JSON 으로 읽는다.
- 직결 후보: 케이블 연결됨(Up) + 유선 Ethernet(802.3) + 실제 장치 + (Wi-Fi/가상/VPN/Bluetooth/루프백이 아님) + 기본 게이트웨이 없음.
  게이트웨이가 있는 어댑터(인터넷 연결)는 allow_internet 을 명시해야만 계획이 만들어진다.
- 대역: CANDIDATE_BANDS 중 이 PC 어댑터/경로에서 쓰지 않는 첫 번째. 보내는 쪽 .1, 받는 쪽 .2, 접두 24. peer(받는 쪽 주소)를 주면 그 대역으로 맞춘다.
- 적용: netsh 를 인자 리스트(shell=False)로만 호출한다. 어댑터는 조회로 얻은 인덱스 번호로만 지정하고, 주소는 후보 대역의 .1/.2 만 허용한다.
  DNS/게이트웨이는 건드리지 않는다. 적용 전에 원래 설정을 복구 기록 파일에 먼저 저장하고, 적용 뒤 다시 읽어 검증하며 실패하면 되돌린다.
- 관리자 권한: 이미 관리자면 직접, 아니면 UAC(Start-Process -Verb RunAs)로 같은 프로그램의 `lan-setup --apply-elevated` 보조 프로세스를 띄운다.
  보조 프로세스는 apply/restore 두 동작만 하고 복구 기록 파일의 검증된 값만 쓰며, 결과를 같은 파일의 result 로 돌려준다.
"""
from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .envpaths import expand_env

# --------------------------------------------------------------------------- 상수
# 직결용 후보 대역. 앞쪽이 기본(항상 첫 번째로 고정). 공유기/회사망/VPN/Docker/WSL 이 흔히 쓰는 10.0.x, 10.10.x, 172.16~17.x,
# 192.168.0.x/1.x/50.x/100.x 와 겹치지 않도록 일부러 눈에 안 띄는 사설 대역을 골랐다. 두 PC 가 겹침 때문에 서로 다른 후보를 고를 수 있어
# 받는 쪽이 고른 대역은 화면/접속 주소에 보이고, 보내는 쪽은 peer 로 같은 대역에 맞출 수 있다.
CANDIDATE_BANDS: tuple[str, ...] = ("10.254.250.0/24", "172.30.250.0/24", "192.168.250.0/24")
PREFIX = 24
ROLE_HOST = {"sender": 1, "receiver": 2}
ROLES = tuple(ROLE_HOST)
STATE_FILE_NAME = "net-restore.json"
STATE_DIR = "%LOCALAPPDATA%\\pcmigrate"
RECORD_VERSION = 1
NETSH_TIMEOUT = 30
ELEVATE_TIMEOUT = 300

# 어댑터 이름/설명에 이런 말이 있으면 직결 후보에서 뺀다(소문자 비교)
EXCLUDE_WORDS = ("wi-fi", "wifi", "wireless", "wlan", "802.11", "bluetooth", "hyper-v", "vethernet", "virtual", "vmware", "virtualbox", "vpn",
                 "tap-", "tap-windows", "wireguard", "loopback", "tunnel", "teredo", "isatap", "wan miniport", "docker", "wsl", "tailscale",
                 "zerotier", "openvpn", "fortinet", "cisco anyconnect", "pseudo")

QUERY_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$adapters = @()
foreach ($a in @(Get-NetAdapter -IncludeHidden:$false)) {
  $idx = $a.ifIndex
  $ifc = Get-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4
  $ips = @(Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 | ForEach-Object { [pscustomobject]@{ ip = "$($_.IPAddress)"; prefix = [int]$_.PrefixLength } })
  $gw = @(Get-NetRoute -InterfaceIndex $idx -DestinationPrefix '0.0.0.0/0' | ForEach-Object { "$($_.NextHop)" })
  $dns = @(Get-DnsClientServerAddress -InterfaceIndex $idx -AddressFamily IPv4 | ForEach-Object { $_.ServerAddresses } | ForEach-Object { "$_" })
  $adapters += [pscustomobject]@{
    name = "$($a.Name)"; index = [int]$idx; status = "$($a.Status)"; media = "$($a.PhysicalMediaType)"; desc = "$($a.InterfaceDescription)";
    hardware = [bool]$a.HardwareInterface; dhcp = "$($ifc.Dhcp)"; addresses = $ips; gateways = $gw; dns = $dns
  }
}
$routes = @(Get-NetRoute -AddressFamily IPv4 | ForEach-Object { [pscustomobject]@{ prefix = "$($_.DestinationPrefix)"; index = [int]$_.ifIndex } })
ConvertTo-Json -InputObject ([pscustomobject]@{ adapters = $adapters; routes = $routes }) -Depth 5 -Compress
"""

ELEVATE_SCRIPT = ("try { $p = Start-Process -FilePath $env:PCM_ELEV_FILE -ArgumentList $env:PCM_ELEV_ARGS -Verb RunAs -Wait -PassThru "
                  "-WindowStyle Hidden -ErrorAction Stop; exit $p.ExitCode } catch { exit 90 }")
ELEVATE_DENIED = 90

# --------------------------------------------------------------------------- 쉬운 오류 문구
MESSAGES = {
    "bad_role": "역할은 보내는 쪽(sender) 또는 받는 쪽(receiver) 이어야 합니다.",
    "query_failed": "이 PC 의 네트워크 어댑터 정보를 읽지 못했습니다. Windows 에서만 쓸 수 있습니다.",
    "no_candidate": "랜선으로 연결된(케이블이 꽂힌) 이더넷 어댑터를 찾지 못했습니다. 두 PC 를 랜선으로 연결했는지 확인해 주세요. "
                    "인터넷에 연결된 어댑터와 무선·가상 어댑터는 제외합니다.",
    "ambiguous": "후보 어댑터가 여러 개입니다. 어댑터를 하나 골라 주세요: {names}",
    "unknown_adapter": "이 PC 에서 그 이름의 이더넷 어댑터를 찾지 못했습니다.",
    "not_wired": "그 어댑터는 랜선 이더넷 어댑터가 아니라 바꿀 수 없습니다(무선·가상·연결 안 됨 포함).",
    "internet_adapter": "그 어댑터는 인터넷(게이트웨이)에 연결돼 있어 기본적으로 바꾸지 않습니다. 바꾸면 인터넷이 끊길 수 있습니다.",
    "bad_peer": "상대 PC 주소를 이해하지 못했습니다. 숫자 주소(예: 10.254.250.2)를 입력해 주세요.",
    "peer_not_in_range": "상대 PC 주소가 자동 맞추기 대역({bands})이 아닙니다. 상대 PC 가 이 도구로 주소를 맞춘 뒤의 주소를 입력해 주세요.",
    "band_in_use": "상대 PC 의 대역 {band} 를 이 PC 가 이미 다른 연결에서 쓰고 있어 맞출 수 없습니다. 받는 쪽 주소를 다른 대역으로 다시 맞춰 주세요.",
    "no_band": "겹치지 않는 대역이 없어 자동으로 맞출 수 없습니다. 주소를 직접 설정해 주세요.",
    "declined": "취소했습니다. 이더넷 설정은 바꾸지 않았습니다.",
    "pending_restore": "이전에 바꾼 이더넷 설정이 아직 원래대로 돌아가지 않았습니다. 먼저 '원래대로 되돌리기'를 해 주세요.",
    "state_write": "원래 설정을 저장하지 못해 이더넷 설정은 바꾸지 않았습니다(저장 위치에 쓸 수 없음).",
    "uac_denied": "관리자 권한 확인을 받지 못했습니다. 확인창에서 '예'를 눌러야 바꿀 수 있습니다. 이더넷 설정은 바꾸지 않았습니다.",
    "elevated_failed": "관리자 권한으로 바꾸는 중 문제가 생겼습니다. {detail}",
    "apply_failed": "이더넷 주소를 바꾸지 못했습니다. 설정을 원래대로 되돌렸습니다. {detail}",
    "verify_failed": "바꾼 주소가 확인되지 않아 원래대로 되돌렸습니다.",
    "rollback_failed": "주소를 바꾸다 문제가 생겼고 원래대로 되돌리는 데도 실패했습니다. 나중에 '원래대로 되돌리기'를 다시 눌러 주세요.",
    "restore_failed": "원래 설정으로 되돌리지 못했습니다. 어댑터 연결을 확인하고 다시 시도해 주세요. {detail}",
    "adapter_missing": "되돌릴 어댑터를 찾지 못했습니다(어댑터를 뺐거나 이름이 바뀜). 어댑터를 연결한 뒤 다시 시도해 주세요.",
    "bad_record": "복구 기록 파일이 올바르지 않아 사용할 수 없습니다. Windows 네트워크 설정에서 이더넷을 '자동(DHCP)'으로 바꿔 주세요.",
    "bad_args": "보조 프로세스에 전달된 값이 올바르지 않습니다.",
}


class NetSetupError(Exception):
    def __init__(self, code: str, **fmt: str) -> None:
        self.code = code
        text = MESSAGES.get(code, code)
        try:
            text = text.format(**{"detail": "", "names": "", "bands": ", ".join(CANDIDATE_BANDS), "band": "", **fmt})
        except (KeyError, IndexError):
            pass
        super().__init__(text.strip())


# --------------------------------------------------------------------------- 실행/권한 주입점
@dataclass
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[..., RunResult]  # runner(args: list[str], timeout: float = ..., env: dict | None = None) -> RunResult


def default_runner(args: list[str], timeout: float = NETSH_TIMEOUT, env: dict | None = None) -> RunResult:
    """실제 실행기: shell=False, 인자 리스트만. 시험에서는 쓰지 않는다."""
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(args, shell=False, capture_output=True, timeout=timeout, env=full_env, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as e:
        return RunResult(127, "", f"{type(e).__name__}: {e}")
    return RunResult(p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"))


def default_is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - Windows 가 아니면 관리자 아님으로 본다
        return False


def default_self_command() -> tuple[str, list[str]]:
    """보조 프로세스로 띄울 (실행 파일, 앞쪽 인자). 묶음 exe 면 그 exe, 아니면 파이썬 -m pcmigrate."""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    return sys.executable, ["-m", "pcmigrate"]


@dataclass
class NetEnv:
    runner: Runner = default_runner
    is_admin: Callable[[], bool] = default_is_admin
    state_path: Callable[[], Path] | None = None
    self_command: Callable[[], tuple[str, list[str]]] = default_self_command
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def state_file(self) -> Path:
        return self.state_path() if self.state_path else default_state_path()


def default_state_path() -> Path:
    base = expand_env(STATE_DIR)
    if "%" in base or not os.path.isabs(base):
        base = str(Path(tempfile.gettempdir()) / "pcmigrate")
    return Path(base) / STATE_FILE_NAME


# --------------------------------------------------------------------------- 어댑터 조회
@dataclass(frozen=True)
class Adapter:
    name: str
    index: int
    status: str = ""
    media: str = ""
    desc: str = ""
    hardware: bool = True
    dhcp: bool = True
    addresses: tuple[tuple[str, int], ...] = ()
    gateways: tuple[str, ...] = ()
    dns: tuple[str, ...] = ()

    @property
    def is_up(self) -> bool:
        return self.status.lower() == "up"

    @property
    def is_wired(self) -> bool:
        """유선 Ethernet(802.3) 실제 장치이고 무선/가상/VPN 이름이 아니다."""
        if not self.hardware or "802.3" not in self.media:
            return False
        text = f"{self.name} {self.desc}".lower()
        return not any(w in text for w in EXCLUDE_WORDS)

    @property
    def has_gateway(self) -> bool:
        return any(g not in ("", "0.0.0.0", "::") for g in self.gateways)

    @property
    def is_direct_candidate(self) -> bool:
        return self.is_up and self.is_wired and not self.has_gateway

    def summary(self) -> dict:
        return {"name": self.name, "index": self.index, "dhcp": self.dhcp, "addresses": [{"ip": a, "prefix": p} for a, p in self.addresses],
                "gateways": list(self.gateways), "dns": list(self.dns)}


def _valid_ipv4(text: object) -> str | None:
    try:
        return str(ipaddress.IPv4Address(str(text)))
    except ValueError:
        return None


def parse_query(text: str) -> tuple[list[Adapter], list[tuple[int, str]]]:
    """조회 스크립트의 JSON 출력 -> (어댑터 목록, [(인터페이스 번호, 경로 접두])."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise NetSetupError("query_failed") from None
    if not isinstance(data, dict):
        raise NetSetupError("query_failed")
    adapters: list[Adapter] = []
    for a in _as_list(data.get("adapters")):
        if not isinstance(a, dict):
            continue
        try:
            idx = int(a["index"])
        except (KeyError, ValueError, TypeError):
            continue
        addrs = []
        for x in _as_list(a.get("addresses")):
            ip = _valid_ipv4(x.get("ip")) if isinstance(x, dict) else None
            try:
                pre = int(x.get("prefix", PREFIX)) if isinstance(x, dict) else PREFIX
            except (ValueError, TypeError):
                pre = PREFIX
            if ip and 0 <= pre <= 32:
                addrs.append((ip, pre))
        adapters.append(Adapter(
            name=str(a.get("name", "")), index=idx, status=str(a.get("status", "")), media=str(a.get("media", "")), desc=str(a.get("desc", "")),
            hardware=bool(a.get("hardware", True)), dhcp=str(a.get("dhcp", "")).lower() == "enabled", addresses=tuple(addrs),
            gateways=tuple(g for g in (_valid_ipv4(x) for x in _as_list(a.get("gateways"))) if g),
            dns=tuple(d for d in (_valid_ipv4(x) for x in _as_list(a.get("dns"))) if d)))
    routes: list[tuple[int, str]] = []
    for r in _as_list(data.get("routes")):
        if isinstance(r, dict):
            try:
                routes.append((int(r.get("index", -1)), str(r.get("prefix", ""))))
            except (ValueError, TypeError):
                continue
    return adapters, routes


def _as_list(v: object) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def powershell_args(script: str) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]


def query_all(env: NetEnv | None = None) -> tuple[list[Adapter], list[tuple[int, str]]]:
    env = env or NetEnv()
    r = env.runner(powershell_args(QUERY_SCRIPT), timeout=NETSH_TIMEOUT)
    if r.returncode != 0 or not r.stdout.strip():
        raise NetSetupError("query_failed")
    return parse_query(r.stdout)


def list_adapters(env: NetEnv | None = None, *, direct_only: bool = True) -> list[Adapter]:
    """직결 후보(기본) 또는 유선 연결된 모든 어댑터를 돌려준다. 읽기 전용."""
    adapters, _ = query_all(env)
    if direct_only:
        return [a for a in adapters if a.is_direct_candidate]
    return [a for a in adapters if a.is_up and a.is_wired]


# --------------------------------------------------------------------------- 대역 선택
def band_network(band: str) -> ipaddress.IPv4Network:
    return ipaddress.IPv4Network(band)


def band_host(band: str, role: str) -> str:
    if role not in ROLE_HOST:
        raise NetSetupError("bad_role")
    return str(band_network(band).network_address + ROLE_HOST[role])


def used_networks(adapters: list[Adapter], routes: list[tuple[int, str]], exclude_index: int | None = None) -> list[ipaddress.IPv4Network]:
    """이 PC 가 이미 쓰는 서브넷(대상 어댑터 자신의 것은 제외: 어차피 그 설정을 바꾸는 것이므로)."""
    nets: list[ipaddress.IPv4Network] = []
    for a in adapters:
        if a.index == exclude_index:
            continue
        for ip, pre in a.addresses:
            try:
                nets.append(ipaddress.IPv4Interface(f"{ip}/{pre}").network)
            except ValueError:
                continue
    for idx, prefix in routes:
        if idx == exclude_index or prefix in ("", "0.0.0.0/0"):
            continue
        try:
            n = ipaddress.IPv4Network(prefix, strict=False)
        except ValueError:
            continue
        if n.prefixlen >= 4 and not n.is_multicast and not n.is_loopback and n.prefixlen < 32:
            nets.append(n)
    return nets


def band_free(band: str, used: list[ipaddress.IPv4Network]) -> bool:
    n = band_network(band)
    return not any(n.overlaps(u) for u in used)


def pick_band(used: list[ipaddress.IPv4Network]) -> str:
    """기본 후보(첫 번째)를 먼저 보고, 겹칠 때만 다음 후보로 넘어간다."""
    for b in CANDIDATE_BANDS:
        if band_free(b, used):
            return b
    raise NetSetupError("no_band")


def _peer_host(addr: str) -> str:
    """'주소' 또는 '주소:포트' -> 올바른 IPv4 주소 문자열(아니면 빈 문자열)."""
    host = (addr or "").strip()
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return _valid_ipv4(host) or ""


def band_of_address(addr: str) -> str | None:
    """주소(포트 포함 가능)가 후보 대역 중 어디에 속하는지. 없으면 None."""
    ip = _peer_host(addr)
    if not ip:
        return None
    a = ipaddress.IPv4Address(ip)
    for b in CANDIDATE_BANDS:
        if a in band_network(b):
            return b
    return None


# --------------------------------------------------------------------------- 계획
@dataclass
class IpPlan:
    role: str
    adapter: Adapter
    band: str
    ip: str
    prefix: int = PREFIX
    internet_adapter: bool = False
    original: dict = field(default_factory=dict)
    peer_ip: str = ""

    @property
    def restore_howto(self) -> str:
        return "전송이 끝나면 자동으로 원래대로 되돌립니다. 직접 되돌리려면 `pcmigrate lan-setup --restore --yes` 를 실행하거나 화면의 '원래대로 되돌리기'를 누르세요."

    def original_text(self) -> str:
        o = self.original
        if o.get("dhcp"):
            base = "자동(DHCP)"
            if o.get("addresses"):
                base += " · 현재 " + ", ".join(f"{a['ip']}/{a['prefix']}" for a in o["addresses"])
            return base
        return "고정 " + (", ".join(f"{a['ip']}/{a['prefix']}" for a in o.get("addresses", [])) or "주소 없음")

    def describe(self) -> str:
        lines = [f"이 PC 의 이더넷 설정을 임시로 바꿉니다: 어댑터 {self.adapter.name} 를 {self.ip} (/{self.prefix}) 로.",
                 f"지금 설정: {self.original_text()}",
                 "전송이 끝나면 원래대로 되돌립니다.",
                 "관리자 권한 확인창이 뜰 수 있습니다. DNS 와 게이트웨이는 바꾸지 않습니다."]
        if self.internet_adapter:
            lines.append("[!] 이 어댑터는 인터넷(게이트웨이)에 연결돼 있어 인터넷이 끊길 수 있습니다.")
        else:
            lines.append("인터넷 연결이 이 어댑터를 쓰면 끊길 수 있습니다.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"role": self.role, "adapter": self.adapter.name, "index": self.adapter.index, "ip": self.ip, "prefix": self.prefix,
                "band": self.band, "internet_adapter": self.internet_adapter, "original": self.original, "peer": self.peer_ip}


def plan_ip_setup(role: str, adapter: str | None = None, peer: str | None = None, *, allow_internet: bool = False,
                  env: NetEnv | None = None) -> IpPlan:
    """읽기만 한다. 어떤 어댑터를 어떤 주소로 바꿀지 계획을 돌려준다(오류면 NetSetupError)."""
    env = env or NetEnv()
    if role not in ROLE_HOST:
        raise NetSetupError("bad_role")
    adapters, routes = query_all(env)
    wired_up = [a for a in adapters if a.is_up and a.is_wired]
    if adapter:
        chosen = next((a for a in adapters if a.name == adapter), None)
        if chosen is None:
            raise NetSetupError("unknown_adapter")
        if not (chosen.is_up and chosen.is_wired):
            raise NetSetupError("not_wired")
        if chosen.has_gateway and not allow_internet:
            raise NetSetupError("internet_adapter")
    else:
        cands = [a for a in wired_up if a.is_direct_candidate]
        if not cands:
            raise NetSetupError("no_candidate")
        if len(cands) > 1:
            raise NetSetupError("ambiguous", names=", ".join(a.name for a in cands))
        chosen = cands[0]
    used = used_networks(adapters, routes, exclude_index=chosen.index)
    peer_ip = ""
    if peer:
        peer_ip = _peer_host(peer)
        if not peer_ip:
            raise NetSetupError("bad_peer")
        band = band_of_address(peer_ip)
        if band is None:
            raise NetSetupError("peer_not_in_range")
        if not band_free(band, used):
            raise NetSetupError("band_in_use", band=band)
    else:
        band = pick_band(used)
    ip = band_host(band, role)
    if peer_ip and peer_ip == ip:
        raise NetSetupError("band_in_use", band=band)
    return IpPlan(role, chosen, band, ip, PREFIX, chosen.has_gateway, chosen.summary(), peer_ip)


# --------------------------------------------------------------------------- 명령(검증된 값만)
def _check_index(index: object) -> int:
    if not isinstance(index, int) or isinstance(index, bool) or not 0 < index < 1_000_000:
        raise NetSetupError("bad_args")
    return index


def _check_ip(ip: object) -> str:
    v = _valid_ipv4(ip)
    if v is None or v != ip:
        raise NetSetupError("bad_args")
    return v


def _check_prefix(p: object) -> int:
    if not isinstance(p, int) or isinstance(p, bool) or not 1 <= p <= 32:
        raise NetSetupError("bad_args")
    return p


def check_candidate_ip(ip: str, role: str | None = None) -> str:
    """적용할 주소는 후보 대역의 .1/.2 만 허용(역할을 주면 그 역할의 호스트 번호여야 한다)."""
    _check_ip(ip)
    a = ipaddress.IPv4Address(ip)
    for b in CANDIDATE_BANDS:
        if a in band_network(b):
            host = int(a) - int(band_network(b).network_address)
            if host in ROLE_HOST.values() and (role is None or ROLE_HOST.get(role) == host):
                return ip
    raise NetSetupError("bad_args")


def mask_of(prefix: int) -> str:
    return str(ipaddress.IPv4Network(f"0.0.0.0/{_check_prefix(prefix)}").netmask)


def cmd_set_static(index: int, ip: str, prefix: int, gateway: str | None = None) -> list[str]:
    args = ["netsh", "interface", "ip", "set", "address", f"name={_check_index(index)}", "source=static",
            f"address={_check_ip(ip)}", f"mask={mask_of(prefix)}"]
    if gateway:
        args.append(f"gateway={_check_ip(gateway)}")
    return args


def cmd_add_address(index: int, ip: str, prefix: int) -> list[str]:
    return ["netsh", "interface", "ip", "add", "address", f"name={_check_index(index)}", f"address={_check_ip(ip)}", f"mask={mask_of(prefix)}"]


def cmd_set_dhcp(index: int) -> list[str]:
    return ["netsh", "interface", "ip", "set", "address", f"name={_check_index(index)}", "source=dhcp"]


def apply_commands(record: dict) -> list[list[str]]:
    ap = record["applied"]
    check_candidate_ip(ap["ip"], record.get("role"))
    return [cmd_set_static(record["adapter"]["index"], ap["ip"], _check_prefix(ap["prefix"]))]


def restore_commands(record: dict) -> list[list[str]]:
    idx = record["adapter"]["index"]
    o = record["original"]
    if o.get("dhcp"):
        return [cmd_set_dhcp(idx)]
    addrs = o.get("addresses") or []
    gws = o.get("gateways") or []
    if not addrs:  # 원래 고정이지만 주소가 없었다 -> 자동으로 두는 것이 가장 안전
        return [cmd_set_dhcp(idx)]
    cmds = [cmd_set_static(idx, addrs[0]["ip"], addrs[0]["prefix"], gws[0] if gws else None)]
    cmds += [cmd_add_address(idx, a["ip"], a["prefix"]) for a in addrs[1:]]
    return cmds


# --------------------------------------------------------------------------- 복구 기록
def _atomic_write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def build_record(plan: IpPlan, env: NetEnv) -> dict:
    return {"version": RECORD_VERSION, "created": env.now().isoformat(timespec="seconds"), "status": "pending", "role": plan.role,
            "adapter": {"name": plan.adapter.name, "index": plan.adapter.index},
            "applied": {"ip": plan.ip, "prefix": plan.prefix}, "original": plan.original, "result": None}


def validate_record(rec: object) -> dict:
    """복구 기록의 모든 값을 다시 검증한다(파일은 사용자 영역에 있으므로 믿지 않는다)."""
    try:
        if not isinstance(rec, dict) or rec.get("version") != RECORD_VERSION:
            raise NetSetupError("bad_record")
        if rec.get("role") not in ROLE_HOST:
            raise NetSetupError("bad_record")
        ad = rec["adapter"]
        _check_index(ad["index"])
        ap = rec["applied"]
        check_candidate_ip(ap["ip"], rec["role"])
        if _check_prefix(ap["prefix"]) != PREFIX:
            raise NetSetupError("bad_record")
        o = rec["original"]
        if not isinstance(o.get("dhcp"), bool):
            raise NetSetupError("bad_record")
        for a in o.get("addresses") or []:
            _check_ip(a["ip"])
            _check_prefix(a["prefix"])
        for g in o.get("gateways") or []:
            _check_ip(g)
        return rec
    except (KeyError, TypeError, AttributeError):
        raise NetSetupError("bad_record") from None
    except NetSetupError as e:
        raise NetSetupError("bad_record") from e


def load_record(env: NetEnv | None = None, path: Path | None = None) -> dict | None:
    """복구 기록을 읽는다. 없으면 None, 깨졌으면 NetSetupError(bad_record)."""
    p = path or (env or NetEnv()).state_file()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        raise NetSetupError("bad_record") from None
    try:
        return validate_record(json.loads(text))
    except ValueError:
        raise NetSetupError("bad_record") from None


def pending_restore(env: NetEnv | None = None) -> dict | None:
    """프로그램이 도중에 꺼져 복구 기록이 남아 있으면 요약 dict(어댑터/주소/원래 설정/안내), 없으면 None. 깨진 기록은 bad_record 로 알린다."""
    env = env or NetEnv()
    try:
        rec = load_record(env)
    except NetSetupError as e:
        return {"bad": True, "message": str(e)}
    if rec is None:
        return None
    return {"bad": False, "adapter": rec["adapter"]["name"], "index": rec["adapter"]["index"], "ip": rec["applied"]["ip"], "role": rec["role"],
            "created": rec.get("created", ""), "original": rec["original"],
            "message": f"이전에 이더넷 어댑터 {rec['adapter']['name']} 의 주소를 임시로 {rec['applied']['ip']} 로 바꿨는데 아직 원래대로 되돌리지 않았습니다. "
                       "`pcmigrate lan-setup --restore --yes` 또는 화면의 '지금 되돌리기'로 되돌려 주세요."}


# --------------------------------------------------------------------------- 실제 수행(관리자 권한 필요)
def _find(adapters: list[Adapter], index: int) -> Adapter | None:
    return next((a for a in adapters if a.index == index), None)


def _run_cmds(cmds: list[list[str]], env: NetEnv) -> str:
    for c in cmds:
        r = env.runner(c, timeout=NETSH_TIMEOUT)
        if r.returncode != 0:
            lines = (r.stdout or r.stderr or "").strip().splitlines()
            return lines[0][:200] if lines else f"exit {r.returncode}"
    return ""


def verify_applied(record: dict, env: NetEnv) -> bool:
    adapters, _ = query_all(env)
    a = _find(adapters, record["adapter"]["index"])
    ap = record["applied"]
    return bool(a and not a.dhcp and (ap["ip"], ap["prefix"]) in a.addresses)


def verify_restored(record: dict, env: NetEnv) -> bool:
    adapters, _ = query_all(env)
    a = _find(adapters, record["adapter"]["index"])
    if a is None:
        raise NetSetupError("adapter_missing")
    o = record["original"]
    ours = (record["applied"]["ip"], record["applied"]["prefix"])
    if o.get("dhcp") or not o.get("addresses"):
        return a.dhcp and ours not in a.addresses
    want = {(x["ip"], x["prefix"]) for x in o["addresses"]}
    return (not a.dhcp) and want <= set(a.addresses) and (ours in want or ours not in a.addresses)


def perform(action: str, record: dict, env: NetEnv) -> dict:
    """관리자 권한으로 직접 수행(적용/복구). 결과 dict {ok, message}. 적용이 실패하면 스스로 되돌린다."""
    try:
        validate_record(record)
        idx = record["adapter"]["index"]
        adapters, _ = query_all(env)
        a = _find(adapters, idx)
        if a is None:
            return {"ok": False, "message": MESSAGES["adapter_missing"]}
        if action == "apply":
            if not a.is_wired:
                return {"ok": False, "message": MESSAGES["not_wired"]}
            err = _run_cmds(apply_commands(record), env)
            if not err and verify_applied(record, env):
                return {"ok": True, "message": ""}
            rb_err = _run_cmds(restore_commands(record), env)
            ok_back = not rb_err and verify_restored(record, env)
            if not ok_back:
                return {"ok": False, "message": MESSAGES["rollback_failed"], "rolled_back": False}
            return {"ok": False, "rolled_back": True, "code": "apply_failed" if err else "verify_failed",
                    "message": MESSAGES["apply_failed" if err else "verify_failed"].format(detail=err)}
        if action == "restore":
            err = _run_cmds(restore_commands(record), env)
            if not err and verify_restored(record, env):
                return {"ok": True, "message": ""}
            return {"ok": False, "message": MESSAGES["restore_failed"].format(detail=err)}
        return {"ok": False, "message": MESSAGES["bad_args"]}
    except NetSetupError as e:
        return {"ok": False, "message": str(e)}


def _quote_arg(text: str) -> str:
    if not text or any(c in text for c in '"\r\n\0') or "%" in text:
        raise NetSetupError("bad_args")
    return f'"{text}"' if (" " in text or "\t" in text) else text


def elevated_args_text(action: str, record: dict, state_file: Path, prefix_args: list[str]) -> str:
    """ArgumentList 로 넘길 문자열. 역할/동작 단어, 숫자, 우리가 만든 파일 경로만 들어간다."""
    if action not in ("apply", "restore"):
        raise NetSetupError("bad_args")
    parts = list(prefix_args) + ["lan-setup", "--apply-elevated", "--action", action, "--role", record["role"], "--adapter-index",
                                 str(_check_index(record["adapter"]["index"])), "--state-file", str(state_file)]
    return " ".join(_quote_arg(p) for p in parts)


def run_elevated(action: str, record: dict, env: NetEnv) -> dict:
    """UAC 로 승격한 보조 프로세스에 맡긴다. 결과는 복구 기록 파일의 result 로 돌아온다."""
    path = env.state_file()
    exe, prefix = env.self_command()
    r = env.runner(powershell_args(ELEVATE_SCRIPT), timeout=ELEVATE_TIMEOUT,
                   env={"PCM_ELEV_FILE": exe, "PCM_ELEV_ARGS": elevated_args_text(action, record, path, prefix)})
    if r.returncode == ELEVATE_DENIED:
        raise NetSetupError("uac_denied")
    try:
        rec = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        rec = None
    res = rec.get("result") if isinstance(rec, dict) else None
    if action == "restore" and rec is None:  # 복구가 끝나면 보조 프로세스가 기록을 지운다
        return {"ok": r.returncode == 0, "message": ""}
    if not isinstance(res, dict) or res.get("action") != action:
        raise NetSetupError("elevated_failed", detail="결과를 받지 못했습니다.")
    return res


def _do(action: str, record: dict, env: NetEnv) -> dict:
    return perform(action, record, env) if env.is_admin() else run_elevated(action, record, env)


# --------------------------------------------------------------------------- 공개 동작
def apply_ip_setup(plan: IpPlan, confirm_fn: Callable[[str], bool] | None, env: NetEnv | None = None) -> dict:
    """확인(confirm_fn) -> 원래 설정 선저장 -> 적용 -> 재확인(실패 시 롤백). 성공하면 {ip, adapter, ...}."""
    env = env or NetEnv()
    if confirm_fn is None or not confirm_fn(plan.describe()):
        raise NetSetupError("declined")
    if env.state_file().exists():
        raise NetSetupError("pending_restore")
    record = build_record(plan, env)
    validate_record(record)  # 어댑터 번호/후보 주소 검증(실패 시 아무것도 쓰지 않고 중단)
    try:
        _atomic_write(env.state_file(), record)
    except OSError:
        raise NetSetupError("state_write") from None
    try:
        res = _do("apply", record, env)
    except NetSetupError:
        _drop_if_untouched(record, env)
        raise
    ok = bool(res.get("ok"))
    if ok:
        try:
            ok = verify_applied(record, env)  # 승격 경로여도 이쪽에서 한 번 더 읽어 확인한다(읽기 전용)
        except NetSetupError:
            ok = False
        if not ok:
            back = _do("restore", record, env)
            if back.get("ok"):
                _remove_state(env)
            raise NetSetupError("verify_failed" if back.get("ok") else "rollback_failed")
        record["status"], record["result"] = "applied", {"ok": True, "action": "apply"}
        _atomic_write(env.state_file(), record)
        return {"ok": True, "ip": plan.ip, "prefix": plan.prefix, "adapter": plan.adapter.name, "role": plan.role}
    if res.get("rolled_back") is False:  # 되돌리기도 실패: 기록을 남겨 나중에 되돌리게 한다
        raise NetSetupError("rollback_failed")
    _remove_state(env)
    if res.get("code") == "verify_failed":
        raise NetSetupError("verify_failed")
    raise NetSetupError("apply_failed", detail=str(res.get("message", "")))


def _drop_if_untouched(record: dict, env: NetEnv) -> None:
    """승격 실패(거부 등)로 아무것도 바뀌지 않았으면 방금 쓴 기록을 지운다. 바뀌었는지 모르겠으면 남긴다."""
    try:
        if not verify_applied(record, env):
            _remove_state(env)
    except NetSetupError:
        pass


def _remove_state(env: NetEnv) -> None:
    try:
        env.state_file().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def restore_ip_setup(env: NetEnv | None = None) -> dict:
    """복구 기록이 있으면 원래 설정으로 되돌리고 기록을 지운다. 기록이 없으면 {'restored': False}."""
    env = env or NetEnv()
    record = load_record(env)
    if record is None:
        return {"ok": True, "restored": False}
    res = _do("restore", record, env)
    if not res.get("ok"):
        raise NetSetupError("restore_failed", detail=str(res.get("message", "")))
    _remove_state(env)
    return {"ok": True, "restored": True, "adapter": record["adapter"]["name"]}


def current_status(env: NetEnv | None = None) -> dict:
    """복구 기록과 후보 어댑터 현황(읽기 전용)."""
    env = env or NetEnv()
    out: dict = {"pending": pending_restore(env), "adapters": []}
    try:
        out["adapters"] = [{**a.summary(), "direct_candidate": a.is_direct_candidate} for a in list_adapters(env, direct_only=False)]
    except NetSetupError as e:
        out["error"] = str(e)
    return out


# --------------------------------------------------------------------------- 승격된 보조 프로세스 본체
def elevated_main(action: str, role: str, adapter_index: int, state_file: str, env: NetEnv | None = None) -> int:
    """`pcmigrate lan-setup --apply-elevated ...` 가 부른다. 임의 명령은 받지 않는다(apply/restore 만, 기록 파일의 검증된 값만 사용)."""
    env = env or NetEnv(is_admin=lambda: True)
    path = Path(state_file)
    try:
        if action not in ("apply", "restore"):
            raise NetSetupError("bad_args")
        record = load_record(env, path)
        if record is None:
            raise NetSetupError("bad_record")
        if record["adapter"]["index"] != adapter_index or record["role"] != role:
            raise NetSetupError("bad_args")
    except NetSetupError as e:
        _write_result(path, {"ok": False, "action": action, "message": str(e)})
        return 1
    res = perform(action, record, NetEnv(runner=env.runner, is_admin=lambda: True, state_path=lambda: path, now=env.now))
    res["action"] = action
    if action == "restore" and res.get("ok"):
        try:
            path.unlink()
        except OSError:
            pass
    else:
        _write_result(path, res, record)
    return 0 if res.get("ok") else 1


def _write_result(path: Path, res: dict, record: dict | None = None) -> None:
    try:
        rec = record
        if rec is None:
            rec = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": RECORD_VERSION}
        rec["result"] = res
        _atomic_write(path, rec)
    except (OSError, ValueError):
        pass
