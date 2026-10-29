"""컨트롤러의 '드라이브·경로 편의 조회와 설정 묶음 찾기' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

import os
import re
from typing import Any

from .. import errorlog, fs
from ..manifest import DEFAULT_NAME as MANIFEST_NAME
from ..progress import fmt_bytes
from . import texts as T
from .controller_common import DriveEntry, RECEIVED_DEPTH, RECEIVED_MAX_DIRS, RECEIVED_PREFIX, norm_path


class PackageFindMixin:
    # ------------------------------------------------------------------ 경로 편의(읽기 전용 조회)
    def list_drives(self) -> list[DriveEntry]:
        """연결된 드라이브 목록(외장/이동식 먼저). Windows 가 아니거나 조회에 실패하면 빈 목록."""
        if self.drive_lister is not None:
            return list(self.drive_lister())
        if os.name != "nt":
            return []
        out: list[DriveEntry] = []
        try:
            import ctypes
            from .. import fs
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if mask & (1 << i):
                    root = f"{chr(65 + i)}:\\"
                    kind = fs.drive_kind(root)
                    if kind == "cdrom":
                        continue
                    free = fs.free_space(root) if kind != "network" else None
                    out.append(DriveEntry(root, kind, fmt_bytes(free) if free is not None else ""))
        except (AttributeError, OSError):
            return out
        out.sort(key=lambda d: (d.kind != "removable", d.root))
        return out

    def default_desktop(self) -> str:
        """바탕화면 폴더(OneDrive 로 옮겨진 경우 포함). 찾지 못하면 사용자 폴더 아래 Desktop."""
        home = os.path.expanduser("~")
        cands = []
        for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            base = os.environ.get(var)
            if base:
                cands.append(os.path.join(base, "Desktop"))
        cands.append(os.path.join(home, "Desktop"))
        for c in cands:
            if os.path.isdir(c):
                return c
        return cands[-1]

    def default_downloads(self) -> str:
        """다운로드 폴더(OneDrive 로 옮겨진 경우 포함). 찾지 못하면 사용자 폴더 아래 Downloads."""
        home = os.path.expanduser("~")
        cands = []
        for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            base = os.environ.get(var)
            if base:
                cands.append(os.path.join(base, "Downloads"))
        cands.append(os.path.join(home, "Downloads"))
        for c in cands:
            if os.path.isdir(c):
                return c
        return cands[-1]

    # ------------------------------------------------------------------ 탐색기에서 보기(stage34): 열지 않고 보여 주기만 한다
    explorer_fn: Any = None  # 시험에서 가짜를 넣는 자리(None 이면 gui/explorer.explorer_open)

    def can_show_in_explorer(self, path: str, select: bool | None = None) -> bool:
        """이 PC 의 존재하는 로컬 디스크 경로인가(공유 폴더·장치 경로·상대 경로·명령 구분자가 든 경로는 False)."""
        from . import explorer
        if select is None:
            select = not os.path.isdir(path) if isinstance(path, str) else False
        return explorer.openable(path, bool(select))

    def show_in_explorer(self, path: str, select: bool | None = None) -> bool:
        """폴더는 그 폴더를, 파일은 탐색기에서 선택된 상태로 부모 폴더를 탐색기로 보여 준다(파일을 열거나 실행하지 않는다).
        select=None 이면 경로가 폴더인지로 정한다. 보여 줬으면 True."""
        from . import explorer
        if select is None:
            select = not os.path.isdir(path) if isinstance(path, str) else False
        fn = self.explorer_fn or explorer.explorer_open
        try:
            return bool(fn(path, bool(select)))
        except Exception:  # noqa: BLE001 - 탐색기를 못 열어도 화면은 계속 쓸 수 있게
            return False

    # ------------------------------------------------------------------ 설정 묶음 자동 찾기
    def received_dirs(self) -> list[str]:
        """새 PC 에 받은 파일이 저장된 위치 후보: 기본 받기 폴더(와 그 한 단계 아래), 바탕화면 아래 받은 파일 폴더, 마지막으로 받은 폴더.
        읽기만 하며 존재하는 폴더만 돌려준다(같은 폴더는 한 번만)."""
        out: list[str] = []

        def add(p: str) -> None:
            if p and os.path.isdir(p) and norm_path(p) not in {norm_path(x) for x in out}:
                out.append(p)

        last = (self.files.rx_result or {}).get("dest") if getattr(self.files, "rx_result", None) else ""
        if isinstance(last, str):
            add(last)
        try:
            default = self._lan().default_receive_dir()
        except Exception:  # noqa: BLE001 - 기본 받기 폴더를 알 수 없으면 마지막으로 받은 폴더만 본다
            default = ""
        prefixes = {RECEIVED_PREFIX}
        parents = []
        try:
            parents.append(self.default_desktop())  # 바탕화면 아래 받은 파일 폴더(이전에 받은 것)
        except Exception:  # noqa: BLE001
            pass
        if default:
            add(default)
            parent, base = os.path.split(default)
            parents.append(parent)
            prefixes.add(base.rsplit("_", 1)[0] if "_" in base else base)  # PC이전_받은파일_날짜시각 -> PC이전_받은파일
        for parent in dict.fromkeys(parents):
            try:
                with os.scandir(parent) as it:
                    for e in it:
                        if e.name.startswith(tuple(prefixes)) and e.is_dir(follow_symlinks=False):
                            add(e.path)
            except OSError:
                pass
        return out

    def find_package_entries(self, limit: int = 30) -> list[tuple[str, float, int, str]]:
        """받은 폴더·바탕화면·다운로드·연결된 외장 드라이브에서 pcmigrate-settings*.zip 을 찾는다(읽기만).
        (경로, 수정 시각, 크기, 위치 종류) 를 가장 최근 것부터. 같은 파일은 한 번만."""
        pat = re.compile(r"^" + re.escape(T.EXP_DEFAULT_NAME.rsplit(".", 1)[0]) + r".*\.zip$", re.IGNORECASE)
        # (폴더, 아래로 내려갈 깊이, 위치 종류). 받은 폴더는 보낼 때의 폴더 구조(<드라이브>\<경로>)가 그대로 들어 있어 더 깊이 본다
        received = self.received_dirs()
        # 새 방식(받는 폴더 바로 아래 '설정 묶음' 폴더)을 가장 먼저 얕게 보고, 이전 버전이 보낸 깊은 경로는 기존 깊이로 계속 찾는다
        dirs: list[tuple[str, int, str]] = [(os.path.join(d, T.BUNDLE_DEST_DIR), 0, T.PKG_WHERE_RECEIVED) for d in received]
        dirs += [(d, RECEIVED_DEPTH, T.PKG_WHERE_RECEIVED) for d in received]
        dirs += [(self.default_desktop(), 0, T.PKG_WHERE_DESKTOP), (self.default_downloads(), 0, T.PKG_WHERE_DOWNLOADS)]
        system = os.path.normcase(os.environ.get("SystemDrive") or "C:").rstrip("\\/")
        try:
            for d in self.list_drives():
                is_system = d.kind == "fixed" and os.path.normcase(d.root).rstrip("\\/") == system
                if d.kind in ("removable", "fixed", "unknown") and not is_system:  # 시스템 드라이브는 바탕화면/다운로드로 이미 찾는다
                    dirs.append((d.root, 1, T.PKG_WHERE_DRIVE))
        except Exception:  # noqa: BLE001 - 드라이브 조회 실패는 찾기만 줄인다
            pass
        found: dict[str, tuple[float, str, int, str]] = {}

        def hit(e, where: str) -> None:
            if e.is_file() and pat.match(e.name):
                st = e.stat()
                found.setdefault(os.path.normcase(os.path.realpath(e.path)), (st.st_mtime, e.path, int(st.st_size), where))

        for folder, depth, where in dirs:
            if not (folder and os.path.isdir(folder)):
                continue
            queue = [(folder, 0)]
            seen_dirs = 0
            while queue and seen_dirs < RECEIVED_MAX_DIRS:  # 너비 우선 + 폴더 수 상한: 큰 사진 폴더를 통째로 훑지 않는다
                cur, level = queue.pop(0)
                seen_dirs += 1
                try:
                    with os.scandir(cur) as it:
                        entries = list(it)
                except OSError:
                    continue
                for e in entries:
                    try:
                        hit(e, where)
                        if level < depth and e.is_dir(follow_symlinks=False) and not e.name.startswith(("$", ".")):
                            queue.append((e.path, level + 1))
                    except OSError:
                        continue
        ordered = sorted(found.values(), key=lambda t: -t[0])
        return [(path, m, size, where) for m, path, size, where in ordered[:limit]]

    scan_packages_on_show: bool = True  # ③ 첫 화면이 열릴 때 설정 묶음을 찾아 보여 줄지(시험에서는 끈다: 실제 폴더를 훑지 않게)

    def package_choices(self) -> list[tuple[str, float, int, str]]:
        """③ 첫 화면의 '이 PC에서 찾은 설정 묶음'(최신 순). 찾지 못하거나 찾기가 꺼져 있으면 빈 목록."""
        if not self.scan_packages_on_show:
            return []
        try:
            return self.find_package_entries()
        except Exception as e:  # noqa: BLE001 - 찾기 실패가 화면을 막지 않게(직접 고르면 된다)
            errorlog.record(e, "설정 묶음 자동 찾기")
            return []

    def find_packages(self, limit: int = 30) -> list[str]:
        """find_package_entries() 의 경로만(가장 최근 파일이 먼저). 신 PC '추천대로 한 번에'가 쓴다."""
        return [path for path, _m, _s, _w in self.find_package_entries(limit)]

    def default_export_path(self) -> str:
        """구 PC 의 설정 묶음 파일 저장 기본 위치: 바탕화면의 pcmigrate-settings.zip."""
        return os.path.join(self.default_desktop(), T.EXP_DEFAULT_NAME)

    def find_manifest(self, *folders: str) -> str:
        """확인용 목록 파일을 주어진 폴더(와 그 바로 위 폴더)에서 찾는다. 없으면 빈 문자열(읽기만 한다)."""
        seen: list[str] = []
        for f in folders:
            if f and os.path.isdir(f):
                seen += [f, os.path.dirname(os.path.normpath(f))]
        for folder in dict.fromkeys(seen):
            cand = os.path.join(folder, MANIFEST_NAME)
            if os.path.isfile(cand):
                return cand
        return ""
