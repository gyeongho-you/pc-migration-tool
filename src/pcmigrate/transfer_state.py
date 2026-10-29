"""transfer 분해 모듈: 이어받기 상태 파일(.pcmigrate-state.json) 기록/읽기.

공개 이름은 transfer.py 파사드가 그대로 다시 내보낸다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .fs import long_path
from .transfer_base import MTIME_TOLERANCE_NS, STATE_COMPACT_FACTOR, STATE_FLUSH_EVERY, STATE_VERSION, _same_path

# --------------------------------------------------------------------------- 상태 파일(줄 단위 JSON)

class State:
    """이어받기용 진행 상태. 파일마다 한 줄씩 덧붙이고(크래시에 강함), 가끔 정리해서 다시 쓴다.

    항목: rel -> (size, mtime_ns, sha256(원본 해시), verified, reason). reason 이 'pending' 이면 복사는 끝났고 대상 검사는 미룸.
    첫 줄은 헤더({"state_version":2,"src":...,"dest_path":...,"dest_serial":...}). 마지막 줄이 잘려도 나머지는 쓴다.
    손상/원본 경로 불일치/옛 형식이면 비우고 전체를 다시 검사한다(reset_reason).
    """

    def __init__(self, path: Path, src: Path, dest_path: str = "", dest_serial: int | None = None) -> None:
        self.path = Path(path)
        self.src = str(src)
        self.files: dict[str, tuple[int, int, str, bool, str]] = {}
        self.reset_reason = ""
        self.prev_dest_path = ""
        self.prev_dest_serial: int | None = None
        self.dest_path, self.dest_serial = dest_path, dest_serial
        self._fh = None
        self._appended = 0
        self._since_flush = 0
        self._last_sync = time.monotonic()
        self._rewritten = False
        self._lock = threading.Lock()
        self._load()

    # -- 읽기
    def _reset(self, why: str) -> None:
        self.files = {}
        self.reset_reason = f"상태 파일 무시(전체 재검사): {why}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(long_path(self.path), "r", encoding="utf-8") as f:
                first = f.readline()
                head = json.loads(first)
                if not isinstance(head, dict):
                    raise ValueError("형식 오류")
                if head.get("state_version") != STATE_VERSION or not _same_path(head.get("src"), self.src):
                    raise ValueError(f"버전/원본 경로 불일치(이전 원본: {head.get('src')}). 같은 원본이어도 드라이브 문자가 바뀌었으면 안전하게 전체를 다시 검사합니다")
                self.prev_dest_path = str(head.get("dest_path") or "")
                self.prev_dest_serial = head.get("dest_serial") if isinstance(head.get("dest_serial"), int) else None
                files: dict[str, tuple] = {}
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        files[e["rel"]] = (int(e["s"]), int(e["m"]), str(e["h"]), bool(e["v"]), str(e.get("r", "")))
                    except (ValueError, KeyError, TypeError):
                        continue  # 마지막 줄이 중간에 끊긴 경우 등: 그 항목만 무시
                self.files = files
        except (OSError, ValueError, KeyError, TypeError) as e:
            self._reset(f"{type(e).__name__}: {e}")

    # -- 조회
    def is_verified(self, rel: str, size: int, mtime_ns: int, dst: str) -> str | None:
        """검사 완료로 건너뛸 수 있으면 원본 해시, 아니면 None. 대상은 크기/수정시각만 가볍게 확인한다."""
        e = self.files.get(rel)
        if not e or not e[3] or e[0] != size or e[1] != mtime_ns:
            return None
        try:
            ds = os.stat(long_path(dst))
        except OSError:
            return None
        if ds.st_size == size and abs(ds.st_mtime_ns - mtime_ns) <= MTIME_TOLERANCE_NS:
            return e[2]
        return None

    def src_hash(self, rel: str, size: int, mtime_ns: int) -> str | None:
        """원본이 그대로(크기/수정시각 동일)이면 이전에 계산해 둔 원본 해시."""
        e = self.files.get(rel)
        return e[2] if e and e[0] == size and e[1] == mtime_ns and e[2] else None

    # -- 쓰기
    def _open(self) -> None:
        if self._fh is not None:
            return
        if not self.path.exists() or (self.reset_reason and not self._rewritten):
            self._write_all()
        self._fh = open(long_path(self.path), "a", encoding="utf-8", newline="\n")

    def _header(self) -> str:
        return json.dumps({"state_version": STATE_VERSION, "src": self.src, "dest_path": self.dest_path,
                           "dest_serial": self.dest_serial, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                          ensure_ascii=False, separators=(",", ":")) + "\n"

    def _write_all(self) -> None:
        tmp = self.path.with_name(self.path.name + ".part")
        with open(long_path(tmp), "w", encoding="utf-8", newline="\n") as f:
            f.write(self._header())
            for rel, (s, m, h, v, r) in self.files.items():
                f.write(self._line(rel, s, m, h, v, r))
            f.flush()
            os.fsync(f.fileno())
        os.replace(long_path(tmp), long_path(self.path))
        self._appended = 0
        self._rewritten = True

    @staticmethod
    def _line(rel: str, s: int, m: int, h: str, v: bool, r: str) -> str:
        return json.dumps({"rel": rel, "s": s, "m": m, "h": h, "v": int(v), "r": r}, ensure_ascii=False,
                          separators=(",", ":")) + "\n"

    def record(self, rel: str, size: int, mtime_ns: int, sha256: str, verified: bool, reason: str = "") -> None:
        with self._lock:
            self.files[rel] = (size, mtime_ns, sha256, verified, reason)
            self._open()
            self._fh.write(self._line(rel, size, mtime_ns, sha256, verified, reason))
            self._appended += 1
            self._since_flush += 1
            now = time.monotonic()
            if self._since_flush >= STATE_FLUSH_EVERY or now - self._last_sync >= 10:
                self._flush(now)

    def _flush(self, now: float | None = None) -> None:
        if self._fh is None:
            return
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass
        self._since_flush = 0
        self._last_sync = now or time.monotonic()

    def save(self) -> None:
        """기록을 확정한다. 덧붙인 줄이 너무 많으면(같은 파일이 여러 번 기록됨) 한 번 정리해서 다시 쓴다."""
        with self._lock:
            if self._fh is not None:
                self._flush()
                self._fh.close()
                self._fh = None
            if not self.path.exists() or (self.reset_reason and not self._rewritten):
                self._write_all()  # 기록한 항목이 없어도 상태 파일(헤더)은 남긴다
            if self._appended > STATE_COMPACT_FACTOR * max(1, len(self.files)) + 1000:
                self._write_all()

    def close(self) -> None:
        self.save()


def read_state_entries(path: Path) -> dict[str, dict]:
    """상태 파일을 읽어 {상대 경로: {size, mtime_ns, sha256, verified, reason}} 로 돌려준다(점검/테스트용)."""
    out: dict[str, dict] = {}
    with open(long_path(path), "r", encoding="utf-8") as f:
        f.readline()
        for line in f:
            try:
                e = json.loads(line)
                out[e["rel"]] = {"size": e["s"], "mtime_ns": e["m"], "sha256": e["h"], "verified": bool(e["v"]),
                                 "reason": e.get("r", "")}
            except (ValueError, KeyError):
                continue
    return out
