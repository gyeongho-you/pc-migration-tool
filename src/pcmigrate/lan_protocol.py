r"""LAN 직접 전송(SMB 없이 도구끼리 TCP 로 전송)의 프로토콜: 프레임, 핸드셰이크(페어링 코드 상호 인증), 경로 검증.

보안 수준(반드시 읽을 것)
  - 제공: 페어링 코드 기반 상호 인증 + 모든 프레임의 HMAC-SHA256 무결성(변조/재전송/순서 바꿈 거부) + 파일 SHA-256 검증.
  - 없음: 암호화. 표준 라이브러리만으로는 AES 를 쓸 수 없으므로 전송 내용은 평문이다. 같은 네트워크의 제3자가 패킷을 엿보면
    파일 내용이 보이고, 6자리 코드는 엿본 핸드셰이크에 대한 오프라인 대입(PBKDF2 로 비용을 올렸지만 100만 가지뿐)에 약하다.
    따라서 '내가 믿는 사설 LAN(같은 공유기/스위치, 랜선 직결)'에서만 쓴다. 공인 IP 면 경고하고 명시 동의(allow_public)가 있어야 한다.
  - 코드 자체는 전송하지 않는다. 시도 5회 실패하면 코드를 잠그고(수신 측을 다시 켜 새 코드 발급), 실패마다 지연이 늘어난다.
    코드는 시작 후 10분 안에 한 번 짝지어야 하고, 짝지은 뒤 연결이 끊기면 마지막 활동 후 30분 동안만 같은 코드로 이어받는다.

프레임(양방향 공통, 바이트 순서 big-endian)
  헤더 16바이트: magic "PM"(2) | 프로토콜 버전(1) | 종류(1) | 페이로드 길이(4) | 순번(8)   -> 페이로드(길이) -> HMAC-SHA256(32)
  HMAC 은 (헤더 + 페이로드) 전체에 대해 계산한다. 순번은 방향마다 0 부터 1 씩 늘며 어긋나면 거부한다(재전송/삭제 방지).
  핸드셰이크 전 프레임은 공개 키로 체크섬만 하고 길이를 4KiB 로 제한한다. 인증 뒤에는 방향별 키
  (session 에서 c2s/s2c 를 따로 유도: 반사 공격 방지)로 서명한다. 인증 뒤 최대 페이로드는 협상한 청크 + 여유.

핸드셰이크
  C->S HELLO{ver, role, nonce_c, name}        버전이 다르면 S 가 ERROR{version} 으로 거절
  S->C CHALLENGE{ver, nonce_s, salt, iter}     (코드가 만료/잠김/종료면 여기서 ERROR)
  C->S AUTH{proof_c = HMAC(K, "client"|nonce_c|nonce_s)}      K = PBKDF2-HMAC-SHA256(코드, salt, iter)
  S->C AUTH_OK{proof_s = HMAC(K, "server"|nonce_c|nonce_s), name}  (틀리면 ERROR{auth, remaining} + 지연)
  세션 키 = HMAC(K, "session"|nonce_c|nonce_s); c2s = HMAC(세션키,"c2s"), s2c = HMAC(세션키,"s2c")
  양쪽이 모두 코드를 아는 쪽만 상대 증명을 만들 수 있으므로 수신 PC 가 가짜인 경우도 송신 측이 알아챈다.

전송 단계(인증 뒤)
  1) PLAN(송신)      {files, dirs, bytes, max_size, chunk, name}     -> PLAN_OK{chunk, free, warnings} | ERROR(limit/fat32)
                     파일 수/총량/가장 큰 파일을 미리 알리고, 수신 측이 상한/FAT32 4GB 를 점검한다. chunk 는 협상(작은 쪽).
  2) LIST(송신)      {entries:[{p,s,d}], last}  1000개씩. p=상대경로(/), s=크기, d=빈 폴더. 수신 측이 경로를 모두 검증하고
                     LIST_ACK{h:[...], off:{번호:오프셋}} 로 파일별 이어받기 힌트를 돌려준다(0 없음/교체, 1 같은 크기 존재, 2 .part 있음).
                     마지막 묶음의 응답에서 디스크 여유 공간을 점검한다(총량 헤더 기준, 부족하면 ERROR{space}).
  3) 파일 단계(송신) FILE_BEGIN{i,p,s,h=SHA-256(원본을 미리 읽어 계산),m,start,wait} -> FILE_DATA* -> FILE_END
                     힌트 0/2 는 응답을 기다리지 않고 바로 흘려보낸다(파이프라인). 힌트 1(같은 크기 존재)은 wait=true 로 보내고
                     수신 측이 기존 파일을 해시해 같으면 FILE_ACK{skip}, 다르면 FILE_ACK{send,start}. 수신 측은 .pcmigrate-part
                     임시 파일에 쓰면서 SHA-256 을 계산하고 FILE_END 에서 헤더 해시와 대조한다. 다르면 임시 파일을 지우고
                     FILE_RESULT{ok:false} 로 알리며 송신 측이 최대 2번 처음부터 다시 보낸다. 같으면 copier._install 규칙으로
                     (기존 다른 파일은 .bak 보존) 교체하고 수정 시각을 맞춘다. 이어받기: start>0 이면 .part 앞부분을 다시
                     읽어 해시에 넣으므로 해시는 항상 처음부터 검증한다.
  4) SUMMARY(송신)   {files, bytes, failed:[...]} -> 수신 측이 .pcmigrate-manifest.jsonl 을 완성하고 DONE{...} 로 집계를 돌려준다.
  기타: PING(긴 작업 중 연결 유지), CANCEL(송신이 취소), ERROR(어느 단계에서나, 쉬운 문구 포함).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import struct
import threading
import time
from pathlib import PurePosixPath

PROTO_VERSION = 1
DEFAULT_PORT = 47830
DISCOVERY_PORT = 47831
MAGIC = b"PM"
HDR = struct.Struct(">2sBBIQ")
MAC_LEN = 32
MAX_PRE_AUTH = 4096
FRAME_SLACK = 64 * 1024  # 청크 + 제어 정보 여유
KDF_ITERATIONS = 60_000
KDF_MAX_ACCEPT = 600_000  # 수신 측이 정한 반복 횟수를 이 이상이면 송신 측이 거부(느린 서버로 CPU 소모시키는 것 방지)
CODE_TTL = 600.0
RECONNECT_WINDOW = 1800.0
MAX_FAILURES = 5
FAIL_DELAY = 0.5
MIN_CHUNK, MAX_CHUNK, DEFAULT_CHUNK = 1024, 16 * 1024 * 1024, 1024 * 1024
MAX_REL = 1024
MAX_COMPONENT = 255
LIST_BATCH = 1000
OWN_PREFIX = ".pcmigrate-"
PART_SUFFIX = ".pcmigrate-part"

# 프레임 종류
T_HELLO, T_CHALLENGE, T_AUTH, T_AUTH_OK, T_ERROR = 1, 2, 3, 4, 5
T_PLAN, T_PLAN_OK, T_LIST, T_LIST_ACK = 10, 11, 12, 13
T_FILE_BEGIN, T_FILE_DATA, T_FILE_END, T_FILE_ACK, T_FILE_RESULT, T_FILE_ABORT = 20, 21, 22, 23, 24, 25
T_SUMMARY, T_DONE, T_PING, T_CANCEL = 30, 31, 32, 33

_PRE_KEY = b"pcmigrate-lan-handshake-v1"


class LanError(Exception):
    """LAN 전송 오류. code 는 기계가 보는 분류, 문구는 사용자에게 그대로 보여 줄 쉬운 말."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class ProtocolError(LanError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "protocol")


class VersionMismatch(LanError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "version")


class AuthError(LanError):
    def __init__(self, message: str, code: str = "auth", remaining: int | None = None) -> None:
        super().__init__(message, code)
        self.remaining = remaining


class Disconnected(LanError):
    def __init__(self, message: str = "") -> None:
        super().__init__(message or DISCONNECT_TEXT, "disconnected")


class UnsafePath(LanError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "unsafe_path")


class RemoteError(LanError):
    """상대가 ERROR 프레임으로 알려 온 오류."""

    def __init__(self, payload: dict) -> None:
        super().__init__(str(payload.get("msg") or payload.get("code") or "상대가 오류를 알렸습니다"), str(payload.get("code") or "remote"))
        self.payload = payload


DISCONNECT_TEXT = ("연결이 끊어졌습니다. 이미 받은 파일은 새 PC 에 그대로 있습니다. 케이블/공유기 연결을 확인하고 "
                   "(수신 쪽이 켜져 있으면) 같은 명령을 다시 실행하면 이어서 받습니다. 코드는 같은 것을 쓸 수 있습니다.")


# --------------------------------------------------------------------------- 프레임
class Channel:
    """소켓 위의 프레임 채널. send 는 스레드 안전(락), recv 는 한 스레드에서만 부른다."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._send_key = self._recv_key = _PRE_KEY
        self._send_seq = self._recv_seq = 0
        self.max_payload = MAX_PRE_AUTH
        self.strict_version = False
        self._lock = threading.Lock()
        self.last_rx = time.monotonic()
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def set_keys(self, send_key: bytes, recv_key: bytes, max_payload: int) -> None:
        with self._lock:
            self._send_key, self._recv_key = send_key, recv_key
            self._send_seq = self._recv_seq = 0
            self.max_payload = max_payload
            self.strict_version = True

    def send(self, ftype: int, payload: bytes | bytearray | memoryview = b"") -> None:
        n = len(payload)
        with self._lock:
            hdr = HDR.pack(MAGIC, PROTO_VERSION, ftype, n, self._send_seq)
            mac = hmac.new(self._send_key, hdr, hashlib.sha256)
            mac.update(payload)
            self._send_seq += 1
            try:
                if n <= 32 * 1024:
                    self.sock.sendall(b"".join((hdr, bytes(payload), mac.digest())))
                else:
                    self.sock.sendall(hdr)
                    self.sock.sendall(payload)
                    self.sock.sendall(mac.digest())
            except OSError as e:
                raise Disconnected() from e

    def send_json(self, ftype: int, obj: dict) -> None:
        self.send(ftype, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def _read_exact(self, n: int) -> bytearray:
        buf = bytearray(n)
        view, got = memoryview(buf), 0
        while got < n:
            try:
                k = self.sock.recv_into(view[got:], n - got)
            except socket.timeout as e:
                raise Disconnected("상대가 한동안 응답하지 않아 연결을 닫았습니다. " + DISCONNECT_TEXT) from e
            except OSError as e:
                raise Disconnected() from e
            if not k:
                raise Disconnected()
            got += k
        return buf

    def recv(self) -> tuple[int, bytearray]:
        hdr = bytes(self._read_exact(HDR.size))
        magic, ver, ftype, n, seq = HDR.unpack(hdr)
        if magic != MAGIC:
            raise ProtocolError("PC 이전 도우미가 아닌 상대가 접속했거나 데이터가 깨졌습니다")
        if self.strict_version and ver != PROTO_VERSION:
            raise VersionMismatch(f"프로토콜 버전이 다릅니다(상대 {ver}, 이 PC {PROTO_VERSION})")
        if n > self.max_payload:
            raise ProtocolError("프레임이 허용 크기를 넘어 연결을 닫습니다")
        payload = self._read_exact(n) if n else bytearray()
        mac = bytes(self._read_exact(MAC_LEN))
        want = hmac.new(self._recv_key, hdr, hashlib.sha256)
        want.update(payload)
        if not hmac.compare_digest(want.digest(), mac):
            raise ProtocolError("받은 데이터의 무결성 검사에 실패했습니다(전송 중 변조되었거나 코드가 다름). 연결을 닫습니다")
        if seq != self._recv_seq:
            raise ProtocolError("프레임 순서가 어긋났습니다(재전송/누락). 연결을 닫습니다")
        self._recv_seq += 1
        self.last_rx = time.monotonic()
        return ftype, payload

    def recv_json(self, expect: int | tuple = ()) -> tuple[int, dict]:
        """(종류, JSON). ERROR 는 RemoteError 로 올린다. expect 가 있고 다른 종류면 ProtocolError. PING 은 건너뛴다."""
        while True:
            ftype, payload = self.recv()
            if ftype == T_PING:
                continue
            break
        obj = parse_json(payload)
        if ftype == T_ERROR:
            raise RemoteError(obj)
        exp = (expect,) if isinstance(expect, int) else tuple(expect)
        if exp and ftype not in exp:
            raise ProtocolError(f"예상하지 못한 메시지({ftype})를 받았습니다")
        return ftype, obj

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def parse_json(payload: bytes | bytearray) -> dict:
    try:
        obj = json.loads(bytes(payload).decode("utf-8")) if payload else {}
    except (ValueError, RecursionError) as e:
        raise ProtocolError("메시지를 해석할 수 없습니다") from e
    if not isinstance(obj, dict):
        raise ProtocolError("메시지 형식이 올바르지 않습니다")
    return obj


# --------------------------------------------------------------------------- 코드
def new_code() -> str:
    import secrets
    return f"{secrets.randbelow(10 ** 6):06d}"


def normalize_code(code: str) -> str:
    """사용자가 입력한 코드: 공백/하이픈 제거 후 숫자 6자리여야 한다."""
    c = "".join(ch for ch in str(code) if ch not in " -")
    if len(c) != 6 or not c.isdigit():
        raise LanError("페어링 코드는 숫자 6자리입니다(수신 PC 화면에 표시된 코드).", "bad_code")
    return c


class PairingCode:
    """수신 측 코드 상태: 10분 만료, 실패 횟수 제한(+지연), 한 세션 뒤 종료. 스레드 안전."""

    def __init__(self, code: str, *, ttl: float = CODE_TTL, window: float = RECONNECT_WINDOW,
                 max_failures: int = MAX_FAILURES, fail_delay: float = FAIL_DELAY, clock=time.monotonic) -> None:
        self.code = normalize_code(code)
        self.ttl, self.window, self.max_failures, self.fail_delay, self.clock = ttl, window, max_failures, fail_delay, clock
        self.created = self.last_active = clock()
        self.paired = False
        self.closed = False
        self.failures = 0
        self._lock = threading.Lock()

    def check_open(self) -> None:
        with self._lock:
            now = self.clock()
            if self.closed:
                raise AuthError("이 코드의 전송은 이미 끝났습니다. 새로 하려면 수신 쪽을 다시 시작해 새 코드를 받으세요.", "closed")
            if self.failures >= self.max_failures:
                raise AuthError("코드를 여러 번 틀려 잠겼습니다. 수신 쪽을 다시 시작해 새 코드를 받으세요.", "locked")
            if (not self.paired and now - self.created > self.ttl) or (self.paired and now - self.last_active > self.window):
                raise AuthError("코드가 만료되었습니다(10분). 수신 쪽을 다시 시작해 새 코드를 받으세요.", "expired")

    def record_failure(self) -> tuple[int, float]:
        """(남은 시도 횟수, 이번에 적용할 지연 초)."""
        with self._lock:
            self.failures += 1
            delay = min(self.fail_delay * (2 ** (self.failures - 1)), 8.0) if self.fail_delay else 0.0
            return max(0, self.max_failures - self.failures), delay

    def record_success(self) -> None:
        with self._lock:
            self.paired = True
            self.failures = 0
            self.last_active = self.clock()

    def touch(self) -> None:
        with self._lock:
            self.last_active = self.clock()

    def close(self) -> None:
        with self._lock:
            self.closed = True


def _kdf(code: str, salt: bytes, iters: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", code.encode("ascii"), salt, iters)


def _proof(k: bytes, who: bytes, cn: bytes, sn: bytes) -> str:
    return hmac.new(k, who + cn + sn, hashlib.sha256).hexdigest()


def _session_keys(k: bytes, cn: bytes, sn: bytes) -> tuple[bytes, bytes]:
    sess = hmac.new(k, b"session" + cn + sn, hashlib.sha256).digest()
    return (hmac.new(sess, b"c2s", hashlib.sha256).digest(), hmac.new(sess, b"s2c", hashlib.sha256).digest())


def _hex(obj: dict, key: str, size: int) -> bytes:
    try:
        raw = bytes.fromhex(str(obj.get(key, "")))
    except ValueError as e:
        raise ProtocolError("핸드셰이크 메시지가 올바르지 않습니다") from e
    if len(raw) != size:
        raise ProtocolError("핸드셰이크 메시지가 올바르지 않습니다")
    return raw


# --------------------------------------------------------------------------- 핸드셰이크
def client_handshake(ch: Channel, code: str, *, name: str = "", chunk_max: int = MAX_CHUNK) -> dict:
    """송신 측. 성공하면 채널 키가 설정되고 수신 측 정보({name,ver})를 돌려준다."""
    code = normalize_code(code)
    cn = os.urandom(16)
    ch.send_json(T_HELLO, {"ver": PROTO_VERSION, "role": "send", "nonce": cn.hex(), "name": name})
    _t, ch_obj = ch.recv_json(T_CHALLENGE)
    if ch_obj.get("ver") != PROTO_VERSION:
        raise VersionMismatch(f"프로토콜 버전이 다릅니다(수신 {ch_obj.get('ver')}, 이 PC {PROTO_VERSION}). 양쪽 PC 의 도구 버전을 같게 맞추세요.")
    sn, salt = _hex(ch_obj, "nonce", 16), _hex(ch_obj, "salt", 16)
    iters = ch_obj.get("iter")
    if not isinstance(iters, int) or isinstance(iters, bool) or not 1 <= iters <= KDF_MAX_ACCEPT:
        raise ProtocolError("핸드셰이크 메시지가 올바르지 않습니다")
    k = _kdf(code, salt, iters)
    ch.send_json(T_AUTH, {"proof": _proof(k, b"client", cn, sn)})
    _t, ok = ch.recv_json(T_AUTH_OK)
    if not hmac.compare_digest(str(ok.get("proof", "")), _proof(k, b"server", cn, sn)):
        raise AuthError("수신 쪽이 올바른 코드를 알고 있지 않습니다(다른 PC 이거나 코드가 다름). 전송하지 않고 연결을 닫습니다.")
    c2s, s2c = _session_keys(k, cn, sn)
    ch.set_keys(c2s, s2c, chunk_max + FRAME_SLACK)
    return {"name": str(ok.get("name", "")), "ver": ok.get("ver")}


def _send_error(ch: Channel, code: str, msg: str, **extra) -> None:
    try:
        ch.send_json(T_ERROR, {"code": code, "msg": msg, **extra})
    except LanError:
        pass


def server_handshake(ch: Channel, pairing: PairingCode, *, name: str = "", chunk_max: int = MAX_CHUNK, gate=None,
                     sleep=time.sleep) -> dict:
    """수신 측. gate() 는 인증 성공 직후 호출되며 None 이거나 (code, 문구) 를 돌려 거절한다(이미 다른 송신이 접속 중 등)."""
    _t, hello = ch.recv_json(T_HELLO)
    if hello.get("ver") != PROTO_VERSION:
        _send_error(ch, "version", f"프로토콜 버전이 다릅니다(수신 {PROTO_VERSION}, 송신 {hello.get('ver')}). 양쪽 PC 의 도구 버전을 같게 맞추세요.")
        raise VersionMismatch("송신 쪽 프로토콜 버전이 다릅니다")
    cn = _hex(hello, "nonce", 16)
    try:
        pairing.check_open()
    except AuthError as e:
        _send_error(ch, e.code, str(e))
        raise
    sn, salt = os.urandom(16), os.urandom(16)
    ch.send_json(T_CHALLENGE, {"ver": PROTO_VERSION, "nonce": sn.hex(), "salt": salt.hex(), "iter": KDF_ITERATIONS})
    _t, auth = ch.recv_json(T_AUTH)
    k = _kdf(pairing.code, salt, KDF_ITERATIONS)
    if not hmac.compare_digest(str(auth.get("proof", "")), _proof(k, b"client", cn, sn)):
        remaining, delay = pairing.record_failure()
        if delay:
            sleep(delay)
        msg = ("코드가 맞지 않습니다. " + (f"{remaining}번 더 틀리면 코드가 잠깁니다." if remaining else "코드가 잠겼습니다. 수신 쪽을 다시 시작해 새 코드를 받으세요."))
        _send_error(ch, "locked" if not remaining else "auth", msg, remaining=remaining)
        raise AuthError(msg, "auth", remaining)
    pairing.check_open()  # 맞는 코드라도 잠김/만료 상태면 거절
    if gate is not None:
        refusal = gate()
        if refusal:
            _send_error(ch, refusal[0], refusal[1])
            raise AuthError(refusal[1], refusal[0])
    pairing.record_success()
    ch.send_json(T_AUTH_OK, {"proof": _proof(k, b"server", cn, sn), "name": name, "ver": PROTO_VERSION})
    c2s, s2c = _session_keys(k, cn, sn)
    ch.set_keys(s2c, c2s, chunk_max + FRAME_SLACK)
    return hello


# --------------------------------------------------------------------------- 경로 검증(수신 측)
def validate_rel(rel: object) -> str:
    """송신 측이 보낸 상대 경로(/ 구분) 검증. 통과한 경로만 돌려주고, 위반이면 UnsafePath.

    package._safe_rel(.. . 절대경로 드라이브문자/ADS ':' 역슬래시 예약 이름 끝 점/공백) 에 더해 길이, NUL, 도구 기록 파일 이름을 막는다.
    링크(심볼릭/정션)를 거치는지는 파일 시스템을 봐야 하므로 쓰는 쪽(lan_transfer)이 fs.reparse_in_chain 으로 따로 검사한다.
    """
    from .package import PackageError, _safe_rel
    if not isinstance(rel, str) or not rel:
        raise UnsafePath("빈 경로가 들어 있습니다")
    if len(rel) > MAX_REL:
        raise UnsafePath(f"경로가 너무 깁니다: {rel[:60]!r}…")
    if "\x00" in rel:
        raise UnsafePath("경로에 널 문자가 들어 있습니다")
    try:
        p = _safe_rel(rel)
    except PackageError as e:
        raise UnsafePath(f"안전하지 않은 경로를 거부했습니다: {rel[:80]!r}") from e
    if "/".join(p.parts) != rel:  # 'a//b', './x', 'a/' 처럼 정규화하면 달라지는 모양은 받지 않는다
        raise UnsafePath(f"경로 모양이 올바르지 않습니다: {rel[:80]!r}")
    for part in p.parts:
        if len(part) > MAX_COMPONENT:
            raise UnsafePath(f"이름이 너무 깁니다: {part[:40]!r}…")
        if part.lower().endswith(PART_SUFFIX):
            raise UnsafePath(f"도구의 임시 파일 이름은 쓸 수 없습니다: {part[:60]!r}")
    if p.parts[0].lower().startswith(OWN_PREFIX):
        raise UnsafePath(f"도구의 기록 파일 이름은 쓸 수 없습니다: {p.parts[0][:60]!r}")
    return str(PurePosixPath(*p.parts))


def clamp_chunk(n: object, hi: int = MAX_CHUNK) -> int:
    try:
        v = int(n)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        v = DEFAULT_CHUNK
    return max(MIN_CHUNK, min(v, hi, MAX_CHUNK))
