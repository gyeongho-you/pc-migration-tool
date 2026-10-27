"""LAN 직접 전송의 수신기(Receiver)와 송신기(send). 프로토콜은 lan_protocol.py 상단 주석 참고.

원칙
  - 송신 측은 원본을 읽기만 한다(쓰기 모드로 열지 않음).
  - 수신 측은 지정한 폴더 밖에는 절대 쓰지 않는다: 모든 상대 경로를 lan_protocol.validate_rel 로 검증하고 링크(정션/심볼릭)를 거치는
    위치는 거부한다. 받은 파일은 .pcmigrate-part 임시 파일에 쓰며 SHA-256 을 계산해 헤더의 해시와 같을 때만 교체(.bak 보존)한다.
  - 받은 파일은 어떤 경우에도 실행하지 않는다.
  - 이어받기: 중단 뒤 다시 접속하면 완성 파일은 건너뛰고(기존 파일을 해시로 확인), 쓰던 .part 는 오프셋부터 이어 받는다.
"""
from __future__ import annotations

import os
import queue
import socket
import stat
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import cancel as cancel_mod
from . import failure, fs
from .cancel import Cancelled
from .copier import COPIED, FileResult, _install, sha256_file
from .fs import long_path
from .junk import apply_to_scan, make_spec
from .lan_link import PUBLIC, DiscoveryResponder, LocalAddress, classify_ip, local_addresses, public_warning
from .lan_protocol import (DEFAULT_CHUNK, DEFAULT_PORT, DISCOVERY_PORT, FRAME_SLACK, LIST_BATCH, MAX_CHUNK, PART_SUFFIX, T_AUTH, T_CANCEL,
                           T_DONE, T_ERROR, T_FILE_ABORT, T_FILE_ACK, T_FILE_BEGIN, T_FILE_DATA, T_FILE_END, T_FILE_RESULT, T_LIST,
                           T_LIST_ACK, T_PING, T_PLAN, T_PLAN_OK, T_SUMMARY, AuthError, Channel, Disconnected, LanError, PairingCode,
                           ProtocolError, RemoteError, UnsafePath, client_handshake, clamp_chunk, new_code, parse_json, server_handshake,
                           validate_rel, DISCONNECT_TEXT)
from .manifest import DEFAULT_NAME as MANIFEST_NAME
from .manifest import ManifestWriter
from .progress import FAILED_KIND, OK, SKIPPED, UNREADABLE, ProgressInfo, ProgressTracker, fmt_bytes
from .scan import scan_source_info

MAX_FILES = 20_000_000
MAX_BYTES = 1 << 60
FAT32_LIMIT = 4 * 1024 ** 3 - 1
PING_EVERY = 5.0
HEX64 = frozenset("0123456789abcdef")
H_NONE, H_SAME_SIZE, H_PART, H_BLOCKED = 0, 1, 2, 4
RETRYABLE = ("mismatch", "offset", "size_over")

EVENT_TEXT = {
    "listening": "수신 대기 중입니다. 구 PC 에서 아래 주소와 코드로 접속하세요.",
    "peer_connected": "송신 PC 가 연결되었습니다.",
    "auth_failed": "코드가 맞지 않는 접속 시도가 있었습니다.",
    "disconnected": "연결이 끊어졌습니다. 같은 코드로 다시 접속하면 이어서 받습니다.",
    "done": "전송이 끝났습니다.",
}


def _keepalive(sock: socket.socket) -> None:
    """죽은 연결을 빨리 알아채도록 TCP keep-alive 를 켠다(15초 후부터 5초 간격). 실패해도 전송에는 영향 없음."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if sys.platform == "win32":
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 15000, 5000))  # type: ignore[attr-defined]
    except (OSError, AttributeError, ValueError):
        pass


def _is_hex64(s: object) -> bool:
    return isinstance(s, str) and len(s) == 64 and set(s) <= HEX64


def _int(v: object, lo: int = 0, hi: int = 1 << 62) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ProtocolError("메시지 값이 올바르지 않습니다")
    return v


# =========================================================================== 수신기
class _Active:
    def __init__(self, ch: Channel) -> None:
        self.ch = ch


@dataclass
class _Cur:
    i: int
    rel: str
    dst: Path
    part: Path
    size: int
    sha: str
    mtime: int
    start: int
    f: object = None
    hasher: object = None
    written: int = 0
    err: str = ""
    reason: str = ""


class Receiver:
    """수신 대기 서버. start() 후 코드/주소를 사용자에게 보여 주고, 송신이 끝나면(done) 스스로 멈춘다. stop() 으로 언제든 멈춘다."""

    def __init__(self, dest: str | os.PathLike, *, port: int = DEFAULT_PORT, bind: str = "0.0.0.0", code: str | None = None,
                 allow_public: bool = False, discovery: bool = True, discovery_port: int = DISCOVERY_PORT, name: str | None = None,
                 on_event: Callable[[dict], None] | None = None, max_files: int = MAX_FILES, max_bytes: int = MAX_BYTES,
                 chunk_max: int = MAX_CHUNK, io_timeout: float = 90.0, takeover_idle: float = 20.0, ttl: float | None = None,
                 window: float | None = None, fail_delay: float | None = None, max_failures: int | None = None,
                 addresses: list | None = None) -> None:
        self.dest = Path(dest)
        self.port = port
        self.bind = bind or "0.0.0.0"
        self.allow_public = allow_public
        self.discovery_enabled = discovery
        self.discovery_port = discovery_port
        self.name = name or socket.gethostname()
        self.on_event = on_event
        self.max_files, self.max_bytes, self.chunk_max = max_files, max_bytes, clamp_chunk(chunk_max)
        self.io_timeout, self.takeover_idle = io_timeout, takeover_idle
        kw = {k: v for k, v in (("ttl", ttl), ("window", window), ("fail_delay", fail_delay), ("max_failures", max_failures)) if v is not None}
        self.pairing = PairingCode(code or new_code(), **kw)
        self.code = self.pairing.code
        self.warnings: list[str] = []
        self.addresses: list[str] = []
        self.events: deque = deque(maxlen=500)
        self.state = "new"
        self.result: dict | None = None
        self._addr_override = addresses
        self._lsock: socket.socket | None = None
        self._stop = threading.Event()
        self._done = threading.Event()
        self._threads: list[threading.Thread] = []
        self._cur: _Active | None = None
        self._cur_lock = threading.Lock()
        self._resp: DiscoveryResponder | None = None
        self.discovery_active = False

    # ------------------------------------------------------------------ 수명
    def start(self) -> "Receiver":
        if self.bind not in ("0.0.0.0", "") and classify_ip(self.bind) == PUBLIC and not self.allow_public:
            raise LanError(public_warning(self.bind), "public_address")
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
            probe = self.dest / ".pcmigrate-write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as e:
            f = failure.classify(e, str(self.dest))
            raise LanError(f"받을 폴더를 쓸 수 없습니다({self.dest}): {f.text}", "dest_not_writable") from e
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform != "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.bind, self.port))
            s.listen(8)
        except OSError as e:
            s.close()
            raise LanError(f"포트 {self.port} 를 열 수 없습니다(이미 다른 프로그램이나 이전 수신이 쓰고 있을 수 있습니다). "
                           "다른 번호를 --port 로 지정하세요.", "port_in_use") from e
        s.settimeout(0.3)
        self._lsock = s
        self.port = s.getsockname()[1]
        self._collect_addresses()
        if self.discovery_enabled:
            self._resp = DiscoveryResponder(self.port, self.name, port=self.discovery_port)
            self.discovery_active = self._resp.start()
            if not self.discovery_active:
                self.warnings.append("자동 검색 포트를 열 수 없어 자동 검색은 되지 않습니다. 송신 쪽에서 위 주소를 직접 입력하세요.")
        self.state = "waiting"
        t = threading.Thread(target=self._accept_loop, name="pcmigrate-lan-accept", daemon=True)
        t.start()
        self._threads.append(t)
        self._emit("listening", message=EVENT_TEXT["listening"], addresses=list(self.addresses), code=self.code, port=self.port,
                   warnings=list(self.warnings), dest=str(self.dest))
        return self

    def _collect_addresses(self) -> None:
        if self.bind not in ("0.0.0.0", ""):
            locs = [LocalAddress(self.bind, classify_ip(self.bind))]
        elif self._addr_override is not None:
            locs = self._addr_override
        else:
            locs = local_addresses() or local_addresses(include_loopback=True)
        self.addresses = [f"{a.ip}:{self.port}" for a in locs]
        self.address_kinds = {f"{a.ip}:{self.port}": a.kind for a in locs}
        self.warnings.append("이 연결은 암호화되지 않습니다(코드로 상대 확인과 변조 검사만 합니다). 믿을 수 있는 사설 LAN(같은 공유기/스위치, 랜선 직결)에서만 쓰세요.")
        self.warnings.append("처음 실행하면 Windows 방화벽 창이 뜰 수 있습니다. '개인 네트워크'를 허용하세요(공용 네트워크는 허용하지 마세요). "
                             "도구는 방화벽 설정을 바꾸지 않습니다.")
        pub = [a.ip for a in locs if a.kind == PUBLIC]
        if pub:
            self.warnings.append(f"공인(인터넷) 주소({', '.join(pub)})가 있는 어댑터가 있습니다. 공유기 안에서만 쓰고, 불안하면 --bind 로 사설 주소를 지정하세요.")
            if self.bind in ("0.0.0.0", "") and not self.allow_public:
                self.warnings.append("공인 주소로는 접속하지 마세요. 접속 허용 목록에는 사설/링크로컬 주소만 표시합니다.")
                keep = [a for a in self.addresses if self.address_kinds[a] != PUBLIC]
                self.addresses = keep or self.addresses

    def stop(self) -> None:
        if self._stop.is_set() and self.state in ("stopped", "done"):
            return
        self._stop.set()
        if self.state not in ("done", "error"):
            self.state = "stopped"
        if self._resp is not None:
            self._resp.stop()
        if self._lsock is not None:
            try:
                self._lsock.close()
            except OSError:
                pass
        with self._cur_lock:
            cur = self._cur
        if cur is not None:
            cur.ch.close()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=3)
        self._emit("stopped", message="수신을 멈췄습니다.")

    def wait(self, timeout: float | None = None) -> bool:
        """전송이 끝나거나 stop() 될 때까지 기다린다. 끝났으면 True."""
        end = None if timeout is None else time.monotonic() + timeout
        while not (self._done.is_set() or self._stop.is_set()):
            if end is not None and time.monotonic() >= end:
                return False
            time.sleep(0.05)
        return True

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def status(self) -> dict:
        return {"state": self.state, "addresses": list(self.addresses), "code": self.code, "port": self.port, "dest": str(self.dest),
                "result": self.result}

    def _emit(self, type_: str, **kw) -> None:
        ev = {"type": type_, "time": time.time(), **kw}
        ev.setdefault("message", EVENT_TEXT.get(type_, ""))
        self.events.append(ev)
        if self.on_event is not None:
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001 - 화면 쪽 오류가 전송을 멈추면 안 된다
                pass

    # ------------------------------------------------------------------ 접속 처리
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._lsock.accept()  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(15)
            _keepalive(conn)
            t = threading.Thread(target=self._handle, args=(conn, addr), name="pcmigrate-lan-conn", daemon=True)
            t.start()
            self._threads = [x for x in self._threads if x.is_alive()] + [t]

    def _gate(self, ch: Channel):
        with self._cur_lock:
            if self._cur is not None:
                if time.monotonic() - self._cur.ch.last_rx > self.takeover_idle:
                    self._cur.ch.close()  # 응답이 없는 이전 연결(케이블 뽑힘 등)은 새 인증된 접속이 이어받는다
                else:
                    return ("busy", "수신 PC 가 이미 다른 송신 PC 와 전송 중입니다. 한 번에 한 PC 만 받을 수 있습니다.")
            self._cur = _Active(ch)
        return None

    def _handle(self, conn: socket.socket, addr) -> None:
        ch = Channel(conn)
        mine = None
        try:
            try:
                hello = server_handshake(ch, self.pairing, name=self.name, chunk_max=self.chunk_max, gate=lambda: self._gate(ch),
                                         sleep=self._sleep)
            except AuthError as e:
                self._emit("auth_failed", message=str(e), peer=addr[0], code=e.code, remaining=e.remaining)
                return
            except LanError as e:
                self._emit("protocol_error", message=str(e), peer=addr[0])
                return
            with self._cur_lock:
                mine = self._cur
            conn.settimeout(self.io_timeout)
            self.state = "connected"
            self._resp and setattr(self._resp, "active", False)
            self._emit("peer_connected", message=EVENT_TEXT["peer_connected"], peer=addr[0], name=str(hello.get("name", ""))[:64])
            _Session(self, ch, addr[0]).run()
        except Disconnected:
            self._emit("disconnected", message=EVENT_TEXT["disconnected"])
        except LanError as e:
            self._emit("error", message=str(e), code=e.code)
        except Exception as e:  # noqa: BLE001
            self._emit("error", message=f"수신 중 예상하지 못한 오류: {type(e).__name__}: {e}", code="internal")
        finally:
            with self._cur_lock:
                if mine is not None and self._cur is mine:
                    self._cur = None
                    if not self._done.is_set() and not self._stop.is_set():
                        self.state = "waiting"
                        self._resp and setattr(self._resp, "active", True)
            ch.close()

    def _sleep(self, secs: float) -> None:
        self._stop.wait(secs)


class _Session:
    """연결 하나(인증된 송신 한 대)의 수신 처리."""

    def __init__(self, rx: Receiver, ch: Channel, peer: str) -> None:
        self.rx, self.ch, self.peer = rx, ch, peer
        self.dest = rx.dest
        self.cur: _Cur | None = None
        self.writer: ManifestWriter | None = None
        self.chunk = DEFAULT_CHUNK
        self.plan: dict = {}
        self.checked_dirs: dict[str, str | None] = {}
        self.done_bytes = self.done_files = 0
        self.ok = self.skipped = self.preserved = 0
        self.seen_i: dict[int, int] = {}
        self.seen_total = 0
        self.bad: dict[int, str] = {}
        self.mismatch_events = 0
        self._last_ping = time.monotonic()
        self._last_emit = 0.0
        self.tracker: ProgressTracker | None = None

    # -- 공통
    def _err(self, code: str, msg: str) -> None:
        try:
            self.ch.send_json(T_ERROR, {"code": code, "msg": msg})
        except LanError:
            pass

    def _ping(self) -> None:
        now = time.monotonic()
        if now - self._last_ping >= PING_EVERY:
            self._last_ping = now
            self.ch.send(T_PING)

    def _progress(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < 0.3:
            return
        self._last_emit = now
        if self.tracker is not None:
            self.rx._emit("progress", info=self.tracker.snapshot(), message="")

    def _is_safe_parent(self, rel: str) -> str | None:
        """상대 경로의 부모 폴더 위치가 안전한지(링크를 거치지 않고 대상 폴더 안). 문제가 있으면 문구."""
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent in self.checked_dirs:
            return self.checked_dirs[parent]
        target = self.dest.joinpath(*parent.split("/")) if parent else self.dest
        msg = None
        link = fs.reparse_in_chain(self.dest, target)
        if link:
            msg = f"바로가기(링크)를 거치는 위치라 쓰지 않았습니다: {link}"
        else:
            try:
                Path(os.path.realpath(target)).relative_to(os.path.realpath(self.dest))
            except ValueError:
                msg = "받을 폴더 밖이라 쓰지 않았습니다"
        self.checked_dirs[parent] = msg
        return msg

    def _dst(self, rel: str) -> Path:
        return self.dest.joinpath(*rel.split("/"))

    # -- 본 흐름
    def run(self) -> None:
        rx, ch = self.rx, self.ch
        try:
            self._negotiate()
            self._list_phase()
            self._data_phase()
        except UnsafePath as e:
            self._err("unsafe_path", f"안전하지 않은 경로가 있어 전송을 거부했습니다: {e}")
            rx._emit("error", message=f"송신 쪽이 안전하지 않은 경로를 보내 거부했습니다: {e}", code="unsafe_path")
            self._abort_cur(drop=True)
            if self.writer:
                self.writer.abort()
        except ProtocolError as e:
            self._err("protocol", str(e))
            rx._emit("error", message=str(e), code="protocol")
            self._abort_cur(drop=False)
            if self.writer:
                self.writer.abort()
        except Disconnected:
            self._abort_cur(drop=False)
            if self.writer:
                self.writer.abort()
            raise

    def _negotiate(self) -> None:
        rx = self.rx
        _t, p = self.ch.recv_json(T_PLAN)
        files, dirs, nbytes, max_size = (_int(p.get("files")), _int(p.get("dirs", 0)), _int(p.get("bytes")), _int(p.get("max_size", 0)))
        if files > rx.max_files or nbytes > rx.max_bytes:
            self._err("limit", f"전송 규모(파일 {files:,}개, {fmt_bytes(nbytes)})가 이 수신 PC 의 허용 범위를 넘습니다.")
            raise LanError("규모 초과", "limit")
        info = fs.describe_destination(self.dest)
        if info.fs == "FAT32" and max_size > FAT32_LIMIT:
            self._err("fat32", f"받는 드라이브가 FAT32 이고 4GB 를 넘는 파일({fmt_bytes(max_size)})이 있어 받을 수 없습니다. "
                      "NTFS 또는 exFAT 드라이브의 폴더를 받는 위치로 지정하세요(LAN 전송은 파일 분할을 지원하지 않습니다).")
            raise LanError("FAT32 한도", "fat32")
        self.chunk = clamp_chunk(p.get("chunk", DEFAULT_CHUNK), rx.chunk_max)
        self.plan = {"files": files, "dirs": dirs, "bytes": nbytes, "max_size": max_size, "name": str(p.get("name", ""))[:64]}
        warnings = []
        if info.fs == "EXFAT":
            warnings.append("받는 드라이브가 exFAT 입니다. 전송 중 전원이 꺼지면 손상되기 쉬우니 절전을 끄세요.")
        self.ch.send_json(T_PLAN_OK, {"chunk": self.chunk, "free": info.free, "fs": info.fs, "warnings": warnings})
        self.tracker = ProgressTracker("copy", nbytes, files)
        rx._emit("plan", message=f"송신 PC 가 파일 {files:,}개({fmt_bytes(nbytes)})를 보내려고 합니다.", files=files, bytes=nbytes,
                 peer_name=self.plan["name"])

    def _list_phase(self) -> None:
        total = self.plan["files"] + self.plan["dirs"]
        seen = needed = 0
        while True:
            _t, b = self.ch.recv_json(T_LIST)
            ents = b.get("entries")
            if not isinstance(ents, list) or len(ents) > LIST_BATCH * 2:
                raise ProtocolError("목록 형식이 올바르지 않습니다")
            hints: list[int] = []
            off: dict[str, int] = {}
            for k, e in enumerate(ents):
                if not isinstance(e, dict):
                    raise ProtocolError("목록 형식이 올바르지 않습니다")
                rel = validate_rel(e.get("p"))
                size = _int(e.get("s", 0))
                msg = self._is_safe_parent(rel)
                is_dir = bool(e.get("d"))
                dst = self._dst(rel)
                lp = long_path
                h = H_NONE
                if msg:
                    h = H_BLOCKED
                elif is_dir:
                    try:
                        os.makedirs(lp(dst), exist_ok=True)
                        if fs.reparse_in_chain(self.dest, dst):
                            h = H_BLOCKED
                    except OSError:
                        h = H_BLOCKED
                else:
                    try:
                        st = os.lstat(lp(dst))
                    except OSError:
                        st = None
                    part = Path(str(dst) + PART_SUFFIX)
                    try:
                        psize = os.lstat(lp(part)).st_size
                    except OSError:
                        psize = -1
                    if st is not None and (stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                        h = H_BLOCKED
                    elif 0 < psize <= size:
                        h = H_PART
                        off[str(k)] = psize
                        needed += size - psize
                    elif st is not None and st.st_size == size:
                        h = H_SAME_SIZE
                        needed += size
                    else:
                        needed += size
                    seen += 1
                if is_dir:
                    seen += 1
                hints.append(h)
            if seen > total + LIST_BATCH:
                raise ProtocolError("목록의 항목 수가 예고한 수를 넘습니다")
            if b.get("last"):
                free = fs.free_space(self.dest)
                need = int(needed * 1.05)
                if free is not None and free < need:
                    self._err("space", f"받는 드라이브의 여유 공간이 부족합니다(필요 약 {fmt_bytes(need)}, 여유 {fmt_bytes(free)}). 공간을 확보하거나 다른 폴더를 지정하세요.")
                    raise LanError("공간 부족", "space")
                self.ch.send_json(T_LIST_ACK, {"h": hints, "off": off, "last": True, "needed": needed, "free": free})
                break
            self.ch.send_json(T_LIST_ACK, {"h": hints, "off": off})
        self.writer = ManifestWriter(self.dest / MANIFEST_NAME, f"lan:{self.plan['name'] or self.peer}", self.plan["files"],
                                     self.plan["bytes"], {"lan": True})

    def _data_phase(self) -> None:
        rx, ch = self.rx, self.ch
        while True:
            ftype, payload = ch.recv()
            rx.pairing.touch()
            if ftype == T_PING:
                continue
            if ftype == T_FILE_DATA:
                self._on_data(payload)
                continue
            obj = parse_json(payload)
            if ftype == T_FILE_BEGIN:
                self._on_begin(obj)
            elif ftype == T_FILE_END:
                self._on_end()
            elif ftype == T_FILE_ABORT:
                self._abort_cur(drop=True)
            elif ftype == T_SUMMARY:
                self._finish(obj)
                return
            elif ftype == T_CANCEL:
                self._abort_cur(drop=False)
                if self.writer:
                    self.writer.abort()
                rx._emit("cancelled", message="송신 쪽이 전송을 취소했습니다. 받은 부분은 남겨 두며 다시 접속하면 이어받습니다.")
                return
            elif ftype == T_ERROR:
                raise RemoteError(obj)
            else:
                raise ProtocolError(f"예상하지 못한 메시지({ftype})를 받았습니다")

    # -- 파일 하나
    def _result(self, cur_i: int, ok: bool, **kw) -> None:
        self.ch.send_json(T_FILE_RESULT, {"i": cur_i, "ok": ok, **kw})

    def _fail_note(self, i: int, rel: str, reason: str) -> None:
        self.bad[i] = reason  # 같은 파일을 나중에 다시 받아 성공하면 지운다(최종 상태만 집계)
        if reason == "mismatch":
            self.mismatch_events += 1
        if self.tracker:
            self.tracker.note(FAILED_KIND, rel)

    def _on_begin(self, o: dict) -> None:
        self._abort_cur(drop=False)
        i = _int(o.get("i"))
        rel = validate_rel(o.get("p"))
        size = _int(o.get("s"))
        sha = o.get("h")
        if not _is_hex64(sha):
            raise ProtocolError("파일 해시 형식이 올바르지 않습니다")
        mtime = _int(o.get("m", 0), 0, 1 << 63)
        start = _int(o.get("start", 0), 0, size)
        wait = bool(o.get("wait"))
        if self.seen_i.get(i) != size:
            self.seen_total += size - self.seen_i.get(i, 0)
            self.seen_i[i] = size
            if self.seen_total > self.plan["bytes"] + max(self.plan["bytes"] // 50, 1 << 20) or len(self.seen_i) > self.plan["files"]:
                raise ProtocolError("예고한 파일 수/총량보다 많이 보냈습니다")
        dst = self._dst(rel)
        part = Path(str(dst) + PART_SUFFIX)
        lp = long_path
        cur = _Cur(i, rel, dst, part, size, sha, mtime, start)
        self.cur = cur
        if self.tracker:
            self.tracker.set_current(rel)
        msg = self._is_safe_parent(rel)
        try:
            if msg is None:
                if os.path.islink(lp(dst)) or fs.is_reparse(dst) and not os.path.isfile(lp(dst)):
                    msg = "바로가기(링크)라 쓰지 않았습니다"
                elif os.path.isdir(lp(dst)):
                    f = failure.REASONS[failure.DEST_IS_DIR]
                    cur.err, cur.reason = f.text, f.code
        except OSError:
            pass
        if msg:
            cur.err, cur.reason = msg, failure.LINK_IN_PATH
        if cur.err:
            if wait:  # 송신이 응답을 기다리고 있으니 보내라고 답하고 데이터는 버린 뒤 FILE_END 에서 실패로 알린다
                self.ch.send_json(T_FILE_ACK, {"i": i, "action": "send", "start": 0})
            return
        acked = False
        try:
            os.makedirs(lp(dst.parent), exist_ok=True)
            if wait:
                if os.path.isfile(lp(dst)) and os.path.getsize(lp(dst)) == size:
                    if sha256_file(dst, self.chunk, None, lambda n: self._ping()) == sha:  # 이미 같은 파일: 건너뜀
                        self.cur = None
                        self.bad.pop(i, None)
                        self.skipped += 1
                        self.done_files += 1
                        if self.tracker:
                            self.tracker.add(size, 1)
                            self.tracker.note(SKIPPED, rel)
                        self.writer.write_row(rel, size, mtime, sha)  # type: ignore[union-attr]
                        self.ch.send_json(T_FILE_ACK, {"i": i, "action": "skip"})
                        self._progress()
                        return
                try:
                    psize = os.path.getsize(lp(part))
                except OSError:
                    psize = 0
                start = psize if 0 < psize <= size else 0
                cur.start = start
                self.ch.send_json(T_FILE_ACK, {"i": i, "action": "send", "start": start})
                acked = True
            self._open_part(cur)
        except OSError as e:
            f = failure.classify(e, rel)
            cur.err, cur.reason = f.text, f.code
            self._close_part(cur, delete=False)
            if wait and not acked:
                self.ch.send_json(T_FILE_ACK, {"i": i, "action": "send", "start": 0})

    def _open_part(self, cur: _Cur) -> None:
        import hashlib
        lp = long_path
        cur.hasher = hashlib.sha256()
        if cur.start == 0:
            cur.f = open(lp(cur.part), "wb")
            return
        try:
            have = os.path.getsize(lp(cur.part))
        except OSError:
            have = -1
        if have < cur.start:
            cur.err, cur.reason = "이어받을 임시 파일이 맞지 않아 처음부터 다시 받아야 합니다", "offset"
            return
        f = open(lp(cur.part), "r+b")
        left = cur.start
        buf = bytearray(min(self.chunk, 1 << 20))
        while left:  # 앞부분을 다시 읽어 해시를 처음부터 계산한다(이어받아도 전체 해시를 검증)
            n = f.readinto(memoryview(buf)[:min(left, len(buf))])
            if not n:
                break
            cur.hasher.update(memoryview(buf)[:n])  # type: ignore[union-attr]
            left -= n
            self._ping()
        if left:
            f.close()
            cur.err, cur.reason = "이어받을 임시 파일이 맞지 않아 처음부터 다시 받아야 합니다", "offset"
            return
        f.truncate(cur.start)
        f.seek(cur.start)
        cur.f = f
        cur.written = cur.start

    def _close_part(self, cur: _Cur, delete: bool) -> None:
        if cur.f is not None:
            try:
                cur.f.close()  # type: ignore[union-attr]
            except OSError:
                pass
            cur.f = None
        if delete:
            try:
                os.remove(long_path(cur.part))
            except OSError:
                pass

    def _on_data(self, payload: bytearray) -> None:
        cur = self.cur
        if cur is None or cur.err or cur.f is None:
            return
        if len(payload) > self.chunk or cur.written + len(payload) > cur.size:
            cur.err, cur.reason = "받은 데이터가 예고한 크기를 넘었습니다", "size_over"
            self._close_part(cur, delete=True)
            return
        try:
            cur.f.write(payload)  # type: ignore[union-attr]
        except OSError as e:
            f = failure.classify(e, cur.rel)
            cur.err, cur.reason = f.text, f.code
            self._close_part(cur, delete=False)
            return
        cur.hasher.update(payload)  # type: ignore[union-attr]
        cur.written += len(payload)
        if self.tracker:
            self.tracker.add(len(payload))
        self._progress()

    def _abort_cur(self, drop: bool) -> None:
        cur, self.cur = self.cur, None
        if cur is not None:
            self._close_part(cur, delete=drop)

    def _on_end(self) -> None:
        cur, self.cur = self.cur, None
        if cur is None:
            raise ProtocolError("시작하지 않은 파일의 끝을 받았습니다")
        rel = cur.rel
        if cur.err:
            self._close_part(cur, delete=cur.reason in RETRYABLE)
            self._fail_note(cur.i, rel, cur.reason)
            self._result(cur.i, False, reason=cur.reason or "other", err=cur.err)
            self._progress(True)
            return
        if cur.f is None:
            self._fail_note(cur.i, rel, "other")
            self._result(cur.i, False, reason="other", err="임시 파일을 열지 못했습니다")
            return
        try:
            cur.f.flush()  # type: ignore[union-attr]
            if cur.size >= (1 << 20):
                os.fsync(cur.f.fileno())  # type: ignore[union-attr]
        except OSError:
            pass
        self._close_part(cur, delete=False)
        digest = cur.hasher.hexdigest()  # type: ignore[union-attr]
        if cur.written != cur.size or digest != cur.sha:
            self._close_part(cur, delete=True)  # 어긋난 임시 파일은 폐기하고 송신 측에 다시 보내 달라고 알린다
            self._fail_note(cur.i, rel, "mismatch")
            self._result(cur.i, False, reason="mismatch", sha=digest,
                         err="받은 내용의 해시가 원본과 다릅니다(전송 중 손상 또는 변조). 임시 파일을 버렸습니다.")
            self._progress(True)
            return
        res = FileResult(src="(lan)", dst=str(cur.dst), status=COPIED, size=cur.size, sha256=digest)
        try:
            _install(cur.part, cur.dst, res, long_path)
        except OSError as e:
            f = failure.classify(e, rel)
            self._fail_note(cur.i, rel, f.code)
            self._result(cur.i, False, reason=f.code, err=f.text)
            return
        try:
            os.utime(long_path(cur.dst), ns=(cur.mtime, cur.mtime)) if cur.mtime else None
        except OSError:
            pass
        if res.backup:
            self.preserved += 1
        self.bad.pop(cur.i, None)
        self.ok += 1
        self.done_files += 1
        if self.tracker:
            self.tracker.add(0, 1)
            self.tracker.note(OK, rel)
        self.writer.write_row(rel, cur.size, cur.mtime, digest)  # type: ignore[union-attr]
        self._result(cur.i, True, sha=digest, bak=res.backup)
        self._progress()

    def _finish(self, o: dict) -> None:
        rx = self.rx
        self._abort_cur(drop=False)
        failed_sender = o.get("failed") if isinstance(o.get("failed"), list) else []
        assert self.writer is not None
        for _ in range(_int(o.get("failed_count", len(failed_sender)), 0, 1 << 40)):
            self.writer.note_error()
        rows, nbytes = self.writer.files, self.writer.bytes
        path = self.writer.close()
        summary = {"manifest": str(path), "files": rows, "bytes": nbytes, "ok": self.ok, "skipped_existing": self.skipped,
                   "mismatch": sum(1 for r in self.bad.values() if r == "mismatch"), "failed": len(self.bad),
                   "mismatch_retried": self.mismatch_events, "preserved_existing": self.preserved,
                   "sender_failed": len(failed_sender), "dest": str(self.dest)}
        self.ch.send_json(T_DONE, summary)
        rx.pairing.close()
        success = not self.bad and not failed_sender
        rx.result = {**summary, "success": success}
        rx.state = "done"
        rx._emit("progress", info=self.tracker.snapshot() if self.tracker else None, message="")
        rx._emit("done", message=EVENT_TEXT["done"], **rx.result)
        rx._done.set()
        rx._stop.set()
        if rx._lsock is not None:
            try:
                rx._lsock.close()
            except OSError:
                pass
        if rx._resp is not None:
            threading.Thread(target=rx._resp.stop, daemon=True).start()


# =========================================================================== 송신기
@dataclass
class SendOptions:
    exclude_junk: bool = False
    junk_backup_copies: bool = False
    include_cloud: bool = False
    chunk: int = DEFAULT_CHUNK
    allow_public: bool = False
    layout: str = "auto"  # auto(폴더 하나면 내용 그대로, 여러 개/선택 파일이면 <드라이브>\<경로>) | flat | drive
    retries: int = 2
    connect_timeout: float = 10.0
    ack_timeout: float = 900.0
    empty_dirs: bool = True
    name: str = ""


@dataclass
class Entry:
    kind: str  # f | d
    root: int
    rel: str  # 원본 루트 기준 상대 경로(/)
    wire: str  # 전송에 쓰는 대상 기준 상대 경로(/)
    size: int = 0
    mtime: int = 0


@dataclass
class SendPlan:
    roots: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)  # 보내기 전에 실패로 확인된 것(안전하지 않은 이름 등)
    dropped: list[dict] = field(default_factory=list)
    junk: dict | None = None
    cloud_only: int = 0
    skipped_links: int = 0
    scan_errors: list[dict] = field(default_factory=list)

    @property
    def files(self) -> list[Entry]:
        return [e for e in self.entries if e.kind == "f"]

    @property
    def total_files(self) -> int:
        return sum(1 for e in self.entries if e.kind == "f")

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries if e.kind == "f")

    @property
    def max_size(self) -> int:
        return max((e.size for e in self.entries if e.kind == "f"), default=0)


def _walk_empty_dirs(root: str, cancel: Callable[[], bool] | None, junk=None) -> list[str]:
    out: list[str] = []
    stack = [("", root)]
    while stack:
        rel, path = stack.pop()
        if cancel and cancel():
            raise Cancelled()
        try:
            with os.scandir(long_path(path)) as it:
                items = list(it)
        except OSError:
            continue
        subdirs = []
        n_real = 0
        for e in items:
            try:
                if e.name.startswith(".pcmigrate-"):
                    continue
                if e.is_symlink():
                    n_real += 1
                    continue
                n_real += 1
                if e.is_dir(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    if getattr(st, "st_file_attributes", 0) & 0x400:
                        continue
                    if junk is not None and junk.skip_dir(e.name):
                        n_real -= 1
                        continue
                    subdirs.append((f"{rel}/{e.name}" if rel else e.name, os.path.join(path, e.name)))
            except OSError:
                continue
        if not items:
            out.append(rel)
        stack.extend(reversed(subdirs))
    return out


def build_send_plan(sources: list[str] | None = None, selection: str | os.PathLike | list | None = None, options: SendOptions | None = None,
                    cancel: Callable[[], bool] | None = None, progress_info: Callable[[ProgressInfo], None] | None = None) -> SendPlan:
    """원본 폴더(들)를 읽기 전용으로 조사해 전송 목록을 만든다. 선택 파일/폴더 둘 이상은 <드라이브>/<경로> 구조(multi_transfer 와 같음)."""
    from . import multi_transfer as mt
    from .find_data import SelectedItem, load_selection
    opts = options or SendOptions()
    if selection is not None and sources:
        raise LanError("선택 파일과 원본 폴더를 함께 줄 수 없습니다", "bad_args")
    items: list[SelectedItem]
    from_selection = selection is not None
    if from_selection:
        items = list(selection) if isinstance(selection, list) else load_selection(selection)  # type: ignore[arg-type]
    else:
        items = [SelectedItem(str(s), True) for s in (sources or [])]
    if not items:
        raise LanError("보낼 원본 폴더가 없습니다", "no_sources")
    try:
        mplan = mt.plan_items(items, r"\\pcmigrate-lan-peer\share")
    except Exception as e:  # TransferError(원본 없음/선택 없음)
        raise LanError(str(e), "bad_sources") from e
    plan = SendPlan(dropped=list(mplan.dropped))
    flat = opts.layout == "flat" or (opts.layout == "auto" and not from_selection and len(mplan.items) == 1)
    spec = make_spec(opts.junk_backup_copies) if opts.exclude_junk else None
    excl_total = None
    for idx, it in enumerate(mplan.items):
        if cancel and cancel():
            raise Cancelled()
        cancel_mod.checkpoint()
        prefix = "" if flat and not it.custom else it.rel.replace("\\", "/")
        plan.roots.append(it.src)
        sc = scan_source_info(it.src, cancel=cancel, progress_info=progress_info)
        if spec is not None:
            ex = apply_to_scan(sc, spec)
            if excl_total is None:
                excl_total = ex
            else:
                excl_total.files += ex.files
                excl_total.bytes += ex.bytes
                for g, v in ex.by_group.items():
                    a = excl_total.by_group.setdefault(g, {"files": 0, "bytes": 0})
                    a["files"] += v["files"]
                    a["bytes"] += v["bytes"]
        rows = list(sc.entries) + (list(sc.cloud) if opts.include_cloud else [])
        plan.cloud_only += 0 if opts.include_cloud else len(sc.cloud)
        plan.skipped_links += sc.skipped_links
        for rel, code, text in sc.errors:
            plan.scan_errors.append({"path": f"{it.src}/{rel}", "reason": code, "error": text})
        for rel, size, mtime in rows:
            wire = f"{prefix}/{rel}" if prefix else rel
            try:
                validate_rel(wire)
            except UnsafePath as e:
                plan.failed.append({"path": wire, "reason": "unsafe_name", "error": f"Windows 에서 쓸 수 없는 이름이라 보내지 않았습니다({e})"})
                continue
            plan.entries.append(Entry("f", idx, rel, wire, size, mtime))
        if opts.empty_dirs:
            for d in _walk_empty_dirs(it.src, cancel, spec):
                wire = "/".join(x for x in (prefix, d) if x)
                if not wire:
                    continue
                try:
                    validate_rel(wire)
                except UnsafePath:
                    continue
                plan.entries.append(Entry("d", idx, d, wire))
    if excl_total is not None:
        plan.junk = excl_total.as_dict(spec)
    return plan


class _Inbox:
    """수신 측 프레임을 별도 스레드에서 읽어 큐에 넣는다(송신 중에도 결과/핑을 놓치지 않기 위함)."""

    def __init__(self, ch: Channel) -> None:
        self.ch = ch
        self.q: queue.Queue = queue.Queue()
        self.t = threading.Thread(target=self._run, name="pcmigrate-lan-inbox", daemon=True)
        self.t.start()

    def _run(self) -> None:
        try:
            while True:
                ftype, payload = self.ch.recv()
                if ftype == T_PING:
                    continue
                self.q.put((ftype, parse_json(payload)))
        except BaseException as e:  # noqa: BLE001
            self.q.put((None, e))


class _Rec:
    __slots__ = ("e", "sha", "tries", "state", "reason", "err", "bak")

    def __init__(self, e: Entry, sha: str) -> None:
        self.e, self.sha, self.tries, self.state, self.reason, self.err, self.bak = e, sha, 0, "sent", "", "", ""


def run_send(host: str, port: int, code: str, plan: SendPlan, options: SendOptions | None = None,
             progress_info: Callable[[ProgressInfo], None] | None = None, cancel: Callable[[], bool] | None = None,
             on_event: Callable[[dict], None] | None = None) -> dict:
    """계획(plan)대로 host:port 의 수신기로 보낸다. 리포트 dict 를 돌려준다. 취소하면 Cancelled, 연결 문제는 LanError."""
    s = _Sender(host, port, code, plan, options or SendOptions(), progress_info, cancel, on_event)
    return s.run()


class _Sender:
    def __init__(self, host, port, code, plan: SendPlan, opts: SendOptions, progress_info, cancel, on_event) -> None:
        self.host, self.port, self.code, self.plan, self.opts = host, port, code, plan, opts
        self.progress_info, self.cancel, self.on_event = progress_info, cancel, on_event
        self.ch: Channel | None = None
        self.inbox: _Inbox | None = None
        self.recs: dict[int, _Rec] = {}
        self.pending = 0
        self.tracker = ProgressTracker("copy", plan.total_bytes, plan.total_files)
        self.counts = {"sent": 0, "skipped_existing": 0, "resumed": 0, "retried": 0, "exact": 0, "mismatch": 0, "preserved_existing": 0}
        self.failed: list[dict] = list(plan.failed)
        self.chunk = clamp_chunk(opts.chunk)
        self._last_ping = time.monotonic()
        self._last_prog = 0.0
        self.chunk_buf = bytearray()
        self.receiver_info: dict = {}

    # -- 유틸
    def _check_cancel(self) -> None:
        cancel_mod.checkpoint()
        if self.cancel is not None and self.cancel():
            raise Cancelled()

    def _ping(self) -> None:
        now = time.monotonic()
        if now - self._last_ping >= PING_EVERY and self.ch is not None:
            self._last_ping = now
            self.ch.send(T_PING)

    def _prog(self, force: bool = False, message: str = "") -> None:
        if self.progress_info is None:
            return
        now = time.monotonic()
        if not force and now - self._last_prog < 0.25:
            return
        self._last_prog = now
        self.progress_info(self.tracker.snapshot(message))

    def _fail(self, e: Entry, reason: str, text: str) -> None:
        self.failed.append({"path": e.wire, "reason": reason, "error": text})
        self.tracker.note(UNREADABLE if reason == "in_use" else FAILED_KIND, e.wire)

    # -- 연결
    def _connect(self) -> None:
        from .lan_link import PUBLIC as _PUB
        try:
            ip = socket.gethostbyname(self.host)
        except OSError as e:
            raise LanError(f"{self.host} 주소를 찾을 수 없습니다. 수신 PC 화면의 숫자 주소를 입력하세요.", "bad_target") from e
        if classify_ip(ip) == _PUB and not self.opts.allow_public:
            raise LanError(public_warning(ip), "public_address")
        try:
            sock = socket.create_connection((ip, self.port), timeout=self.opts.connect_timeout)
        except ConnectionRefusedError as e:
            raise LanError(f"{ip}:{self.port} 에서 받는 프로그램이 응답하지 않습니다. 새 PC 에서 `pcmigrate receive` 가 켜져 있는지, "
                           "주소/포트가 같은지 확인하세요. 원인을 보려면 `pcmigrate send --diagnose` 를 실행하세요.", "refused") from e
        except (socket.timeout, TimeoutError) as e:
            raise LanError(f"{ip}:{self.port} 에 연결하지 못했습니다(응답 없음). 두 PC 가 같은 공유기/스위치(또는 랜선 직결)인지, 새 PC 의 방화벽에서 "
                           "개인 네트워크를 허용했는지 확인하세요. `pcmigrate send --diagnose` 로 원인을 알아볼 수 있습니다.", "timeout") from e
        except OSError as e:
            raise LanError(f"{ip}:{self.port} 에 연결하지 못했습니다({e.strerror or type(e).__name__}). 케이블/주소를 확인하세요.", "unreachable") from e
        sock.settimeout(30)
        _keepalive(sock)
        self.ch = Channel(sock)
        info = client_handshake(self.ch, self.code, name=self.opts.name or socket.gethostname())
        self.receiver_info = info
        sock.settimeout(None)

    def _event(self, type_: str, **kw) -> None:
        if self.on_event:
            self.on_event({"type": type_, "time": time.time(), **kw})

    # -- 전체
    def run(self) -> dict:
        t0 = time.monotonic()
        plan = self.plan
        self._connect()
        ch = self.ch
        assert ch is not None
        try:
            self._event("connected", message="수신 PC 와 연결되었습니다.", receiver=self.receiver_info.get("name", ""))
            ch.send_json(T_PLAN, {"files": plan.total_files, "dirs": len(plan.entries) - plan.total_files, "bytes": plan.total_bytes,
                                  "max_size": plan.max_size, "chunk": self.chunk, "name": self.opts.name or socket.gethostname()})
            _t, ok = ch.recv_json(T_PLAN_OK)
            self.chunk = clamp_chunk(ok.get("chunk"), self.chunk)
            self.receiver_warnings = list(ok.get("warnings") or [])
            hints, offs = self._list_phase()
            self.inbox = _Inbox(ch)
            self._data_phase(hints, offs)
            self._finish()
        except Cancelled:
            try:
                ch.send(T_CANCEL)
                ch.sock.shutdown(socket.SHUT_WR)  # 취소 메시지가 먼저 도착하도록 쓰기만 닫고 잠시 기다린 뒤 닫는다
                time.sleep(0.2)
            except (LanError, OSError):
                pass
            ch.close()
            raise
        except BaseException:
            ch.close()
            raise
        ch.close()
        return self._report(time.monotonic() - t0)

    def _list_phase(self) -> tuple[list[int], dict[int, int]]:
        ch, ents = self.ch, self.plan.entries
        hints: list[int] = []
        offs: dict[int, int] = {}
        n = len(ents)
        i = 0
        while True:
            self._check_cancel()
            batch = ents[i:i + LIST_BATCH]
            last = i + LIST_BATCH >= n
            ch.send_json(T_LIST, {"entries": [{"p": e.wire, "s": e.size, "d": e.kind == "d"} for e in batch], "last": last})
            _t, ack = ch.recv_json(T_LIST_ACK)
            h = ack.get("h")
            if not isinstance(h, list) or len(h) != len(batch):
                raise ProtocolError("수신 측의 이어받기 정보가 올바르지 않습니다")
            for k, v in (ack.get("off") or {}).items():
                offs[i + int(k)] = int(v)
            hints.extend(int(x) for x in h)
            i += LIST_BATCH
            self._prog(message="수신 PC 와 목록을 맞추는 중…")
            if last:
                break
        return hints, offs

    # -- 수신 메시지 처리
    def _next(self, timeout: float):
        assert self.inbox is not None
        try:
            ftype, obj = self.inbox.q.get(timeout=timeout)
        except queue.Empty:
            return "empty", None
        if ftype is None:
            if isinstance(obj, LanError):
                raise obj
            raise Disconnected()
        if ftype == T_ERROR:
            raise RemoteError(obj)
        return ftype, obj

    def _on_result(self, o: dict) -> None:
        rec = self.recs.get(o.get("i"))
        if rec is None:
            return
        self.pending -= 1
        if o.get("ok"):
            if o.get("sha") == rec.sha:
                rec.state = "ok"
                self.counts["exact"] += 1
                if o.get("bak"):
                    self.counts["preserved_existing"] += 1
                self.tracker.note(OK, rec.e.wire)
            else:
                rec.state = "bad"
                rec.reason, rec.err = "mismatch", "수신 측이 알린 해시가 다릅니다"
        else:
            rec.state, rec.reason, rec.err = "bad", str(o.get("reason") or "other"), str(o.get("err") or "")

    def _drain(self) -> None:
        while True:
            ftype, obj = self._next(0)
            if ftype == "empty":
                return
            if ftype == T_FILE_RESULT:
                self._on_result(obj)

    def _wait_for(self, want: int, idx: int | None = None):
        deadline = time.monotonic() + self.opts.ack_timeout
        assert self.ch is not None
        while True:
            self._check_cancel()
            self._ping()
            ftype, obj = self._next(1.0)
            if ftype == "empty":
                if time.monotonic() > deadline or time.monotonic() - self.ch.last_rx > 120:
                    raise Disconnected("수신 PC 가 오랫동안 응답하지 않습니다. " + DISCONNECT_TEXT)
                continue
            if ftype == T_FILE_RESULT:
                self._on_result(obj)
                if want == T_FILE_RESULT and self.pending == 0:
                    return obj
                continue
            if ftype == want and (idx is None or obj.get("i") == idx):
                return obj
            if ftype == T_DONE and want == T_DONE:
                return obj

    # -- 파일 단계
    def _data_phase(self, hints: list[int], offs: dict[int, int]) -> None:
        ents = self.plan.entries
        retry: list[int] = []
        for k, e in enumerate(ents):
            if e.kind != "f":
                continue
            self._check_cancel()
            if hints[k] == H_BLOCKED:
                self._fail(e, "link_in_path", "받을 위치가 링크이거나 같은 이름의 폴더가 있어 쓸 수 없습니다")
                continue
            idx = k
            self._send_one(idx, e, hints[k], offs.get(k, 0), force_zero=False)
            if self.pending > 512:
                while self.pending > 256:
                    self._wait_one_result()
            self._drain()
        for rnd in range(self.opts.retries):
            self._wait_all()
            bad = [i for i, r in self.recs.items() if r.state == "bad" and r.reason in RETRYABLE and r.tries <= rnd]
            if not bad:
                break
            for i in bad:
                r = self.recs[i]
                r.tries += 1
                self.counts["retried"] += 1
                self.tracker.done_bytes = max(0, self.tracker.done_bytes - r.e.size)  # 다시 보내므로 진행량에서 뺀다
                self._send_one(i, r.e, H_SAME_SIZE, 0, force_zero=True)
        self._wait_all()

    def _wait_one_result(self) -> None:
        self._wait_for(T_FILE_RESULT)

    def _wait_all(self) -> None:
        while self.pending > 0:
            self._wait_for(T_FILE_RESULT)
        self._drain()

    def _hash_source(self, path: str) -> tuple[str, int, int]:
        st = os.stat(long_path(path))
        sha = sha256_file(Path(path), self.chunk if self.chunk >= 65536 else 65536, self.cancel, lambda n: (self._ping(), self._check_cancel()))
        return sha, st.st_size, st.st_mtime_ns

    def _send_one(self, idx: int, e: Entry, hint: int, off: int, force_zero: bool) -> None:
        ch = self.ch
        assert ch is not None
        path = os.path.join(self.plan.roots[e.root], *e.rel.split("/"))
        self.tracker.set_current(e.wire)
        self._prog(message="")
        try:
            sha, size, mtime = self._hash_source(path)
        except OSError as ex:
            f = failure.classify(ex, path)
            self._fail(e, f.code, f.text)
            return
        wait = hint == H_SAME_SIZE or force_zero
        start = 0 if (force_zero or hint != H_PART) else off
        if start > size:
            start = 0
        rec = self.recs.get(idx) or _Rec(e, sha)
        rec.sha, rec.state = sha, "sent"
        self.recs[idx] = rec
        ch.send_json(T_FILE_BEGIN, {"i": idx, "p": e.wire, "s": size, "h": sha, "m": mtime, "start": start, "wait": wait})
        if wait:
            ack = self._wait_for(T_FILE_ACK, idx)
            if ack.get("action") == "skip":
                rec.state = "ok"
                self.counts["skipped_existing"] += 1
                self.counts["exact"] += 1
                self.tracker.add(size, 1)
                self.tracker.note(SKIPPED, e.wire)
                self._prog()
                return
            start = int(ack.get("start") or 0)
            if start > size:
                start = 0
        if start:
            self.counts["resumed"] += 1
            self.tracker.resumed += 1
            self.tracker.add(start)
        try:
            f = open(long_path(path), "rb", buffering=0)  # 원본은 읽기 전용으로만 연다
        except OSError as ex:
            f2 = failure.classify(ex, path)
            ch.send_json(T_FILE_ABORT, {"i": idx})
            self.recs.pop(idx, None)
            self._fail(e, f2.code, f2.text)
            return
        import hashlib
        h = hashlib.sha256() if start == 0 else None
        total = start
        try:
            if start:
                f.seek(start)
            if len(self.chunk_buf) != self.chunk:
                self.chunk_buf = bytearray(self.chunk)
            view = memoryview(self.chunk_buf)
            while True:
                self._check_cancel()
                n = failure.read_chunk(f.readinto, view)
                if not n:
                    break
                if h is not None:
                    h.update(view[:n])
                ch.send(T_FILE_DATA, view[:n])
                total += n
                self.tracker.add(n)
                self._prog()
        except OSError as ex:
            f2 = failure.classify(ex, path)
            ch.send_json(T_FILE_ABORT, {"i": idx})
            self.recs.pop(idx, None)
            self._fail(e, f2.code, f2.text)
            return
        finally:
            f.close()
        if total != size or (h is not None and h.hexdigest() != sha):
            ch.send_json(T_FILE_ABORT, {"i": idx})
            self.recs.pop(idx, None)
            self._fail(e, "changed", "보내는 중에 원본 파일이 바뀌어 보내지 않았습니다(다시 실행하세요)")
            return
        ch.send_json(T_FILE_END, {"i": idx})
        self.counts["sent"] += 1
        self.pending += 1
        self.tracker.add(0, 1)

    def _finish(self) -> None:
        assert self.ch is not None
        bad_recs = [r for r in self.recs.values() if r.state == "bad"]
        for r in bad_recs:
            self.counts["mismatch"] += 1 if r.reason == "mismatch" else 0
            self.failed.append({"path": r.e.wire, "reason": r.reason, "error": r.err})
        self.ch.send_json(T_SUMMARY, {"files": self.plan.total_files, "bytes": self.plan.total_bytes,
                                      "failed": self.failed[:200], "failed_count": len(self.failed)})
        self.receiver_done = self._wait_for(T_DONE)
        self._prog(True, "전송이 끝났습니다")

    def _report(self, seconds: float) -> dict:
        plan = self.plan
        total = plan.total_files
        exact = self.counts["exact"]
        mismatch = self.counts["mismatch"]
        missing = max(0, total - exact - mismatch)
        rd = self.receiver_done
        manifest_ok = rd.get("files") == exact and rd.get("mismatch", 0) == mismatch
        success = missing == 0 and mismatch == 0 and manifest_ok and not self.failed
        return {
            "mode": "lan-send", "success": success, "target": f"{self.host}:{self.port}", "seconds": round(seconds, 2),
            "files": total, "bytes": plan.total_bytes, "dirs": len(plan.entries) - total,
            "sent_files": self.counts["sent"], "skipped_existing": self.counts["skipped_existing"], "resumed_files": self.counts["resumed"],
            "retried": self.counts["retried"], "preserved_existing": self.counts["preserved_existing"],
            "verification": {"exact": exact, "mismatch": mismatch, "missing": missing},
            "receiver": rd, "manifest_matches": manifest_ok,
            "failed_count": len(self.failed), "failed": self.failed[:1000],
            "junk_excluded": plan.junk, "cloud_only_skipped": plan.cloud_only, "skipped_links": plan.skipped_links,
            "dropped_duplicates": plan.dropped, "scan_errors": plan.scan_errors[:200],
        }
