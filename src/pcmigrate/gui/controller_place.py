"""컨트롤러의 '받은 파일 원래 위치에 놓기'(place) 부분(stage50). Controller 가 이 믹스인을 상속한다.

흐름: 입력(받은 폴더/옵션) -> 미리보기(아무것도 바꾸지 않음) -> 확인 -> 놓기 -> 결과.
- 놓기는 '같은 입력·옵션으로 미리보기를 마친 뒤'에만 시작한다(옵션이 바뀌면 미리보기는 무효). 받은 폴더에서 지울 파일이 있으면 확인 체크와 확인창을 거친다.
- 어떤 경우(성공/부분 실패/취소/강제 중단/예외)에도 결과 리포트는 작업 스레드의 finally 에서 저장한다(미리보기는 파일을 쓰지 않는다).
- 백엔드 호출은 self.api.run_place 한 곳(가짜 api 로 바꿔 끼울 수 있다). 오류는 기존 _run 의 report_error 가 오류 로그에 남긴다.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from .. import api as real_api
from .. import errorlog, fs
from .. import place_original as po
from ..cancel import Cancelled
from ..transfer import TransferError
from . import state as S
from . import texts as T
from .controller_common import GuardError, RECEIVED_MAX_DIRS, norm_path
from .place_flow import (PlaceCheck, PlaceState, place_confirm_text, place_head, place_level, place_notes, place_rows, place_summary,
                         place_summary_line, place_todo_lines, plain_cli)

_LETTER = re.compile(r"^[A-Za-z]$")
MAX_CHOICES = 30


def _clean(p: str) -> str:
    return (p or "").strip()


class PlaceMixin:
    place_desktop_fn: Callable[[], str] | None = None  # 시험에서 바탕화면 위치를 바꿔 끼우는 자리(None 이면 백엔드 기본: Windows 알려진 폴더)
    place_env: dict | None = None  # 시험에서 환경변수(OneDrive 위치)를 바꿔 끼우는 자리
    place_protected_fn: Callable | None = None  # 시험에서 '보호 위치' 판정을 바꿔 끼우는 자리(임시 폴더가 AppData 아래라서)

    # ------------------------------------------------------------------ 화면 드나들기
    def place_open(self, from_screen: str, recv: str = "") -> None:
        """기존 화면의 '받은 파일을 원래 위치에 놓기' 버튼이 부른다. recv 를 알면 받은 폴더 칸에 채운다."""
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        s = self.place_state
        s.came_from = from_screen
        if recv and _clean(recv) != s.recv:
            self.place_set(recv=_clean(recv))
        self.go(S.PLACE)

    def place_back_screen(self) -> str:
        return self.place_state.came_from or S.START

    # ------------------------------------------------------------------ 입력/옵션
    def _place_key(self) -> tuple:
        s = self.place_state
        maps = tuple(sorted((a.strip().upper(), b.strip().upper()) for a, b in s.maps if (a or b)))
        gather = (_clean(s.gather_dir) or "<기본>") if s.gather_on else None
        return (norm_path(s.recv) if s.recv else "", maps, bool(s.keep), bool(s.no_overwrite), bool(s.include_unverified), gather)

    def place_set(self, **opts: Any) -> None:
        """화면의 입력/옵션을 알린다(recv, maps, keep, no_overwrite, include_unverified, gather_on, gather_dir, report_path).
        미리보기 때와 달라지면 미리보기는 무효가 되어, 보지 않은 내용으로 놓지 못하게 한다."""
        s = self.place_state
        changed = False
        for name in ("recv", "keep", "no_overwrite", "include_unverified", "gather_on", "gather_dir", "report_path"):
            if name not in opts:
                continue
            value = opts[name]
            value = _clean(value) if isinstance(value, str) else bool(value) if isinstance(getattr(s, name), bool) else value
            if getattr(s, name) != value:
                setattr(s, name, value)
                changed = True
        if "maps" in opts:
            maps = [(str(a), str(b)) for a, b in (opts["maps"] or [])]
            if maps != s.maps:
                s.maps, changed = maps, True
        if not changed:
            return
        if s.preview_key is not None and s.preview_key != self._place_key():
            s.preview_key, s.preview_stale, s.ack = None, True, False
        self.emit("place_changed")

    def place_set_ack(self, on: bool) -> None:
        self.place_state.ack = bool(on)
        self.emit("place_changed")

    def place_default_report(self) -> str:
        """결과 리포트 기본 저장 위치: 바탕화면\\이전 결과\\place-리포트-날짜시각.json (이름만 만들며 폴더는 만들지 않는다)."""
        try:
            desktop = self.default_desktop()
        except Exception:  # noqa: BLE001 - 바탕화면을 못 찾으면 사용자 폴더 아래로
            desktop = os.path.expanduser("~")
        return os.path.join(desktop, "이전 결과", time.strftime("place-리포트-%Y%m%d-%H%M%S.json"))

    def place_gather_default(self) -> str:
        """모을 폴더를 비워 두면 쓰는 기본 폴더(문구용)."""
        try:
            return str(po.default_gather_folder(self.place_desktop_fn))
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------ 점검(읽기 전용, 글자를 칠 때마다 불러도 가벼운 것만)
    @staticmethod
    def _scan_recv_top(recv: str) -> tuple[list[str], bool]:
        """받은 폴더 맨 위의 드라이브 폴더 문자들과 받기 기록 파일 유무(읽기만). 읽지 못하면 OSError."""
        drives: list[str] = []
        journal = False
        with os.scandir(recv) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False) and _LETTER.match(e.name):
                        drives.append(e.name.upper())
                    elif e.name == po.JOURNAL_NAME and e.is_file(follow_symlinks=False):
                        journal = True
                except OSError:
                    continue
        return sorted(set(drives)), journal

    def check_place_inputs(self, recv: str, maps: list | None = None, gather_on: bool = False, gather_dir: str = "",
                           report: str = "") -> PlaceCheck:
        lines: list[tuple[bool, str]] = []
        chk = PlaceCheck(False, lines, [], [])
        recv = _clean(recv)
        recv_ok = False
        if not recv:
            lines.append((False, T.PL_CHECK_NEED))
        elif not os.path.isdir(recv):
            lines.append((False, T.PL_CHECK_NO_FOLDER.format(path=recv)))
        else:
            try:
                drives, journal = self._scan_recv_top(recv)
            except OSError as e:
                errorlog.record(e, "받은 파일 놓기: 받은 폴더 읽기")
                lines.append((False, T.PL_CHECK_UNREADABLE.format(path=recv)))
            else:
                chk.drives = drives
                if drives:
                    lines.append((True, T.PL_CHECK_OK.format(n=len(drives), drives=", ".join(drives))))
                    recv_ok = True
                elif journal:
                    lines.append((True, T.PL_CHECK_OK_JOURNAL))
                    recv_ok = True
                else:
                    lines.append((False, T.PL_CHECK_NOT_RECV))
        # ---- 드라이브 바꾸기
        pairs = [(_clean(a).upper(), _clean(b).upper()) for a, b in (maps or []) if _clean(a) or _clean(b)]
        if pairs:
            bad = [p for p in pairs if not (_LETTER.match(p[0]) and _LETTER.match(p[1]))]
            seen: set[str] = set()
            dup = next((a for a, _b in pairs if a in seen or seen.add(a)), "")
            if bad:
                lines.append((False, T.PL_MAP_BAD))
            elif dup:
                lines.append((False, T.PL_MAP_DUP.format(letter=dup)))
            else:
                try:
                    chk.maps = po.parse_maps([f"{a}={b}" for a, b in pairs])
                except ValueError:
                    lines.append((False, T.PL_MAP_DST_DUP))
        else:
            chk.maps = {}
        # ---- 한 곳에 모으기
        if gather_on and recv_ok:
            kw: dict = {}
            if self.place_desktop_fn is not None:
                kw["desktop_fn"] = self.place_desktop_fn
            if self.place_env is not None:
                kw["env"] = self.place_env
            if self.place_protected_fn is not None:
                kw["protected_fn"] = self.place_protected_fn
            default = not _clean(gather_dir)
            try:
                g, ginfo = po.resolve_gather("" if default else _clean(gather_dir), Path(recv), **kw)
            except TransferError as e:
                lines.append((False, plain_cli(str(e))))
            else:
                chk.gather_folder = str(g)
                chk.gather_onedrive = bool(ginfo.get("onedrive_redirected"))
                if chk.gather_onedrive:
                    chk.warnings.append(T.PL_GATHER_ONEDRIVE)
                    if default:  # 기본 폴더가 OneDrive 이면 백엔드가 놓기를 거부한다: 직접 고르게 안내
                        chk.gather_default_blocked = True
                        chk.apply_block = T.WHY_PL_GATHER_ONEDRIVE
        # ---- 결과 리포트 위치
        rep = _clean(report)
        if rep:
            if os.path.isdir(rep):
                lines.append((False, T.PL_CHECK_REPORT_DIR.format(path=rep)))
            elif recv and real_api.report_inside(rep, recv):
                lines.append((False, T.G_PL_REPORT_INSIDE))
        chk.ok = bool(recv_ok) and all(ok for ok, _ in lines)
        return chk

    def place_check_state(self) -> PlaceCheck:
        s = self.place_state
        return self.check_place_inputs(s.recv, s.maps, s.gather_on, s.gather_dir, s.report_path or self.place_default_report())

    def place_reasons(self) -> tuple[str, str]:
        """(미리보기 버튼이 막힌 이유, 놓기 버튼이 막힌 이유). 빈 문자열이면 눌러도 됨."""
        s = self.place_state
        if self.state.busy:
            return T.WHY_BUSY, T.WHY_BUSY
        if not s.recv:
            return T.WHY_PL_RECV, T.WHY_PL_RECV
        chk = self.place_check_state()
        if not chk.ok:
            return T.WHY_PL_PROBLEM, T.WHY_PL_PROBLEM
        if s.preview_key != self._place_key():
            return "", (T.WHY_PL_OPTIONS if s.preview_stale else T.WHY_PL_PREVIEW_FIRST)
        if chk.apply_block:
            return "", chk.apply_block
        rep = s.preview_report
        if rep is not None:
            sm = place_summary(rep)
            if not sm["total_files"] and not sm["gather_n"]:  # 놓을 것도 모을 것도 없으면 놓기를 해도 바뀌는 것이 없다
                return "", T.WHY_PL_NOTHING
            if not s.keep and sm["delete_n"] and not s.ack:
                return "", T.WHY_PL_ACK
        return "", ""

    def place_step(self) -> tuple[tuple[str, ...], int]:
        """단계 표시줄용 (이름들, 지금 단계 1부터): 받은 폴더 -> 미리보기 -> 놓기 -> 결과."""
        s = self.place_state
        names = T.PL_STEP_NAMES
        if s.report is not None and not s.was_preview and not self.state.busy:
            return names, 4
        if self.state.busy:
            return names, 2 if self.state.busy_label == T.TASK_PLACE_PREVIEW else 3
        if s.preview_key is not None and s.preview_key == self._place_key():
            return names, 3
        if not s.recv or not self.check_place_inputs(s.recv).ok:
            return names, 1
        return names, 2

    # ------------------------------------------------------------------ 받은 폴더 자동 찾기(읽기만)
    def place_choices(self) -> list[tuple[str, float, int, str]]:
        """이 PC 에서 받은 폴더로 보이는 곳(받기 기록 파일이 있거나 드라이브 폴더가 든 폴더), 최신 순. (경로, 시각, 0, 위치 종류).
        ③ 첫 화면의 설정 묶음 찾기와 같은 스위치(scan_packages_on_show)로 끈다(시험에서 실제 드라이브를 훑지 않게)."""
        if not self.scan_packages_on_show:
            return []
        try:
            return self._find_place_dirs()
        except Exception as e:  # noqa: BLE001 - 찾기 실패가 화면을 막지 않게(직접 고르면 된다)
            errorlog.record(e, "받은 폴더 자동 찾기")
            return []

    def _looks_received(self, folder: str) -> tuple[bool, float]:
        try:
            drives, journal = self._scan_recv_top(folder)
            if not (drives or journal):
                return False, 0.0
            jp = os.path.join(folder, po.JOURNAL_NAME)
            return True, os.path.getmtime(jp) if journal else os.path.getmtime(folder)
        except OSError:
            return False, 0.0

    def _find_place_dirs(self) -> list[tuple[str, float, int, str]]:
        found: dict[str, tuple[float, str, str]] = {}

        def consider(folder: str, where: str) -> None:
            ok, mtime = self._looks_received(folder)
            if ok:
                found.setdefault(norm_path(folder), (mtime, folder, where))

        cands = [(d, T.PKG_WHERE_RECEIVED) for d in self.received_dirs()]
        system = os.path.normcase(os.environ.get("SystemDrive") or "C:").rstrip("\\/")
        try:
            for d in self.list_drives():
                if d.kind in ("removable", "fixed", "unknown") and os.path.normcase(d.root).rstrip("\\/") != system:
                    cands.append((d.root, T.PKG_WHERE_DRIVE))
                    try:  # 드라이브 바로 아래 폴더 한 단계(너무 많으면 앞의 일부만)
                        with os.scandir(d.root) as it:
                            for n, e in enumerate(it):
                                if n >= RECEIVED_MAX_DIRS // 10:
                                    break
                                if e.is_dir(follow_symlinks=False) and not e.name.startswith(("$", ".")) and not _LETTER.match(e.name):
                                    cands.append((e.path, T.PKG_WHERE_DRIVE))
                    except OSError:
                        continue
        except Exception as e:  # noqa: BLE001 - 드라이브 조회 실패는 찾기만 줄인다
            errorlog.record(e, "받은 폴더 자동 찾기: 드라이브 조회")
        for folder, where in cands:
            consider(folder, where)
        ordered = sorted(found.values(), key=lambda t: -t[0])
        return [(path, m, 0, where) for m, path, where in ordered[:MAX_CHOICES]]

    # ------------------------------------------------------------------ 실행
    def _place_probe_report(self, path: str) -> str:
        """결과 리포트를 쓸 수 있는지 시작 전에 점검한다(폴더 만들기 + 시험 파일). 문제가 있으면 문구, 없으면 빈 문자열."""
        p = Path(path)
        try:
            if os.path.isdir(fs.long_path(p)):
                return T.PL_CHECK_REPORT_DIR.format(path=path)
            os.makedirs(fs.long_path(p.parent), exist_ok=True)
            probe = p.parent / f".pcmigrate-report-test-{os.getpid()}"
            with open(fs.long_path(probe), "wb") as h:
                h.write(b"x")
            os.unlink(fs.long_path(probe))
        except OSError as e:
            errorlog.record(e, "받은 파일 놓기: 결과 리포트 위치 점검")
            return T.PL_REPORT_PROBE.format(path=path)
        return ""

    def _write_place_report(self, rep: Any, path: str) -> tuple[str, str]:
        """결과 리포트 저장. (저장한 경로, 안내 문구). 저장하지 못하면 이름.new.json 으로 대신 저장하고, 그것도 안 되면 ('', 실패 문구)."""
        p = Path(path)
        try:
            os.makedirs(fs.long_path(p.parent), exist_ok=True)
            rep.write(p)
            return str(p), ""
        except OSError as e:
            errorlog.record(e, "받은 파일 놓기: 결과 리포트 저장")
            alt = p.with_name(p.stem + ".new" + (p.suffix or ".json"))
            try:
                rep.write(alt)
                return str(alt), T.PL_REPORT_FALLBACK.format(path=path, alt=alt)
            except OSError as e2:
                errorlog.record(e2, "받은 파일 놓기: 결과 리포트 대체 저장")
                return "", T.PL_REPORT_FAILED

    def _place_save(self, holder: dict, path: str, run_id: int) -> None:
        """작업 스레드의 finally 에서 부른다: 결과가 있으면 항상 저장하고, 저장 때문에 원래 예외를 덮지 않는다."""
        rep = holder.get("rep")
        if rep is None:
            return
        try:
            saved, note = self._write_place_report(rep, path)
        except Exception as e:  # noqa: BLE001 - 저장 오류가 작업 결과/원래 예외를 가리지 않게
            errorlog.record(e, "받은 파일 놓기: 결과 리포트 저장")
            saved, note = "", T.PL_REPORT_FAILED
        holder["saved"], holder["note"] = saved, note
        s = self.place_state
        if s.run_id == run_id:  # 강제로 버린 옛 작업이 늦게 끝나도 새 작업의 상태를 덮지 않는다
            s.saved_path, s.save_note = saved, note

    def start_place(self, apply: bool = False, write_confirmed: bool = False, ack: bool | None = None, **opts: Any) -> None:
        """미리보기(apply=False) 또는 놓기(apply=True). 놓기는 같은 입력·옵션의 미리보기, 확인창의 '예'(write_confirmed),
        (지울 파일이 있으면) 확인 체크(ack)가 모두 있어야 한다. opts 는 place_set 과 같은 이름의 입력/옵션."""
        if opts:
            self.place_set(**opts)
        s = self.place_state
        if ack is not None:
            s.ack = bool(ack)
        if self.runner.abandoned_alive():  # 강제로 버린 옛 작업이 아직 파일을 만지고 있을 수 있다
            raise GuardError(T.G_PL_BUSY_ABANDONED)
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        report_path = s.report_path or self.place_default_report()
        chk = self.check_place_inputs(s.recv, s.maps, s.gather_on, s.gather_dir, report_path)
        if not chk.ok:
            raise GuardError(" ".join(t for ok, t in chk.lines if not ok) or T.G_PL_ENTER_FIRST)
        key = self._place_key()
        if apply:
            if not write_confirmed:
                raise GuardError(T.G_PL_NEED_CONFIRM)
            if s.preview_key != key:
                raise GuardError(T.G_PL_NEED_PREVIEW)
            if chk.apply_block:
                raise GuardError(chk.apply_block)
            prev = s.preview_report
            if prev is not None and not s.keep and place_summary(prev)["delete_n"] and not s.ack:
                raise GuardError(T.G_PL_NEED_ACK)
            bad = self._place_probe_report(report_path)
            if bad:
                raise GuardError(T.PL_ERR_PREFLIGHT.format(reason=bad))
        s.run_id += 1
        run_id = s.run_id
        s.preview_stale = False
        recv = s.recv
        kw: dict = dict(apply=apply, maps=dict(chk.maps or {}), keep=s.keep, no_overwrite=s.no_overwrite,
                        include_unverified=s.include_unverified, gather=(None if not s.gather_on else (_clean(s.gather_dir) if _clean(s.gather_dir) else "")))
        if self.place_desktop_fn is not None:
            kw["desktop_fn"] = self.place_desktop_fn
        if self.place_env is not None:
            kw["env"] = self.place_env
        if self.place_protected_fn is not None:
            kw["protected_fn"] = self.place_protected_fn
        holder: dict = {"rep": None, "exc": None, "warns": [], "saved": "", "note": ""}

        def work(progress, token):
            def warn(msg: str) -> None:
                text = plain_cli(msg)
                if text and text not in holder["warns"] and len(holder["warns"]) < 20:
                    holder["warns"].append(text)
                progress(text)

            try:
                rep = self.api.run_place(recv, progress_info=progress, cancel=token, warn=warn, **kw)
                holder["rep"] = rep
                return rep
            except BaseException as e:  # noqa: BLE001 - Cancelled/TransferError/DriveDisconnected/DiskFull/예외: 부분 결과를 붙여 다시 던진다
                holder["rep"], holder["exc"] = getattr(e, "report", None), e
                if isinstance(e, TransferError):  # 오류 대화상자에 명령줄 옵션 이름이 보이지 않게
                    e.args = (plain_cli(str(e)),)
                raise
            finally:
                if apply:
                    self._place_save(holder, report_path, run_id)

        def adopt(rep: Any) -> None:
            s.report, s.was_preview, s.warnings = rep, not apply, list(holder["warns"])
            s.stopped, s.stopped_note = "", ""
            if apply:
                s.saved_path, s.save_note = holder.get("saved", ""), holder.get("note", "")
                s.preview_key, s.ack = None, False  # 놓은 뒤에는 새 미리보기가 있어야 다시 놓을 수 있다
            else:
                s.saved_path, s.save_note = "", ""

        def done(rep):
            adopt(rep)
            if not apply:
                s.preview_key, s.preview_report, s.ack = key, rep, False
            self.emit("place_done", rep)

        def fail():
            """UI 스레드에서 취소/오류 때 부른다(강제 중단은 작업이 아직 도는 중이라 결과가 없을 수 있다): 부분 결과를 화면에 올린다."""
            rep, exc = holder.get("rep"), holder.get("exc")
            adopt(rep)
            s.stopped = "cancelled" if isinstance(exc, Cancelled) else ("error" if exc is not None else "forced")
            if rep is None:
                s.stopped_note = T.PL_STOPPED_NO_REPORT.format(path=report_path) if apply else T.STOP_DONE_PLACE_PREVIEW
            self.emit("place_done", rep)

        label = T.TASK_PLACE if apply else T.TASK_PLACE_PREVIEW
        self._run(label, work, done, cancellable=True, on_fail=fail)

    # ------------------------------------------------------------------ 결과(화면이 그릴 것)
    def place_confirm_message(self) -> str:
        s = self.place_state
        rep = s.preview_report
        if rep is None:
            return ""
        return place_confirm_text(rep, s.keep, s.report_path or self.place_default_report())

    def place_ack_needed(self) -> bool:
        """'받은 폴더의 파일이 지워진다는 것을 확인했습니다' 체크를 보일 때: 같은 입력의 미리보기가 있고, 지울 파일이 있고, 복사만 하기가 아닐 때."""
        s = self.place_state
        return bool(s.preview_report is not None and s.preview_key is not None and s.preview_key == self._place_key()
                    and not s.keep and place_summary(s.preview_report)["delete_n"])

    def place_result(self) -> dict:
        """결과 영역에 그릴 내용(없으면 빈 딕셔너리). 순수 계산은 place_flow 에 있다."""
        s = self.place_state
        rep = s.report
        if rep is None:
            return {"stopped_note": s.stopped_note} if s.stopped_note else {}
        sm = place_summary(rep)
        return {"sm": sm, "head": place_head(sm), "line": place_summary_line(sm), "level": place_level(sm),
                "notes": place_notes(rep, sm), "rows": place_rows(rep), "todo": place_todo_lines(rep, s.warnings),
                "warnings": list(s.warnings), "preview": sm["preview"], "stopped_note": s.stopped_note,
                "saved_path": s.saved_path, "save_note": s.save_note, "stopped": s.stopped}
