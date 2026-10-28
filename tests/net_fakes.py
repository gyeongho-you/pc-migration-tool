"""stage39 시험용 가짜 PC: 실제 netsh/PowerShell 을 부르지 않고 어댑터 상태를 메모리에서 흉내 낸다. 이 PC 의 네트워크 설정은 절대 건드리지 않는다."""
from __future__ import annotations

import ipaddress
import json
import shlex
from pathlib import Path

from pcmigrate import net_setup as ns


def adapter(name="Ethernet A", index=7, status="Up", media="802.3", desc="Realtek PCIe GbE Family Controller", hardware=True, dhcp=True,
            addresses=(), gateways=(), dns=()):
    return {"name": name, "index": index, "status": status, "media": media, "desc": desc, "hardware": hardware,
            "dhcp": "Enabled" if dhcp else "Disabled", "addresses": [{"ip": a, "prefix": p} for a, p in addresses],
            "gateways": list(gateways), "dns": list(dns)}


def _prefix(mask: str) -> int:
    return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen


class FakePC:
    """runner 로 쓴다: fake(args, timeout=..., env=...) -> RunResult. 호출 기록은 calls."""

    def __init__(self, tmp: Path, adapters=None, routes=None, admin=True):
        self.adapters = adapters if adapters is not None else [adapter(addresses=[("169.254.10.20", 16)])]
        self.routes = routes or []
        self.admin = admin
        self.calls: list[list[str]] = []
        self.envs: list[dict | None] = []
        self.netsh_fail = False  # netsh 가 실패 코드를 돌려줌
        self.netsh_noop = False  # netsh 는 성공이라지만 아무것도 안 바뀜(검증 실패용)
        self.netsh_fail_after = None  # N 번째 netsh 호출부터 실패(롤백 실패 시험)
        self.uac_denied = False
        self.dhcp_address = ("169.254.10.20", 16)
        self.state = tmp / "pcmigrate" / "net-restore.json"
        self.elevated_runs = 0
        self.netsh_count = 0
        self.state_seen_before_change: list[bool] = []

    def env(self) -> ns.NetEnv:
        return ns.NetEnv(runner=self, is_admin=lambda: self.admin, state_path=lambda: self.state,
                         self_command=lambda: ("python.exe", ["-m", "pcmigrate"]))

    def find(self, idx):
        return next(a for a in self.adapters if a["index"] == idx)

    def __call__(self, args, timeout=30, env=None):
        assert isinstance(args, list) and all(isinstance(a, str) for a in args), args
        self.calls.append(list(args))
        self.envs.append(env)
        if args[0] == "powershell.exe":
            if args[-1] == ns.QUERY_SCRIPT:
                return ns.RunResult(0, json.dumps({"adapters": self.adapters, "routes": self.routes}))
            if args[-1] == ns.ELEVATE_SCRIPT:
                return self._elevate(env or {})
            raise AssertionError("알 수 없는 PowerShell 호출")
        if args[:4] in (["netsh", "interface", "ip", "set"], ["netsh", "interface", "ip", "add"]):
            return self._netsh(args)
        raise AssertionError(f"허용되지 않은 명령: {args}")

    def _elevate(self, env):
        if self.uac_denied:
            return ns.RunResult(ns.ELEVATE_DENIED)
        self.elevated_runs += 1
        parts = [p.strip('"') for p in shlex.split(env["PCM_ELEV_ARGS"], posix=False)]
        assert env["PCM_ELEV_FILE"] == "python.exe"
        i = parts.index("lan-setup")
        opts = dict(zip(parts[i + 2::2], parts[i + 3::2]))
        rc = ns.elevated_main(opts["--action"], opts["--role"], int(opts["--adapter-index"]), opts["--state-file"],
                              ns.NetEnv(runner=self, is_admin=lambda: True, state_path=lambda: Path(opts["--state-file"])))
        return ns.RunResult(rc)

    def _netsh(self, args):
        self.netsh_count += 1
        self.state_seen_before_change.append(self.state.exists())
        if self.netsh_fail or (self.netsh_fail_after is not None and self.netsh_count > self.netsh_fail_after):
            return ns.RunResult(1, "오류")
        kv = dict(a.split("=", 1) for a in args[5:] if "=" in a)
        a = self.find(int(kv["name"]))
        if self.netsh_noop:
            return ns.RunResult(0, "확인")
        if args[3] == "add":
            a["addresses"].append({"ip": kv["address"], "prefix": _prefix(kv["mask"])})
        elif kv.get("source") == "dhcp":
            a["dhcp"] = "Enabled"
            a["addresses"] = [{"ip": self.dhcp_address[0], "prefix": self.dhcp_address[1]}]
        else:
            a["dhcp"] = "Disabled"
            a["addresses"] = [{"ip": kv["address"], "prefix": _prefix(kv["mask"])}]
            if "gateway" in kv:
                a["gateways"] = [kv["gateway"]]
        return ns.RunResult(0, "확인")
