"""컨트롤러의 '내 파일 옮기기'(②) 부분(stage30a). Controller 가 이 믹스인을 상속한다(병합 충돌을 줄이려고 별도 파일).

비시각(헤드리스)로 시험할 수 있다. 화면은 이 메서드들만 부른다. 외부 호출은 data_api / lan_api 속성으로 바꿔 끼울 수 있다
(시험에서는 가짜를 넣는다. 실제 스캔·전송·네트워크 방송은 시험에서 하지 않는다).

안전 원칙(기존과 같다): 원본은 읽기만 하고, 쓰기(복사/보내기) 전에는 미리보기와 사람의 확인이 있어야 하며,
아무것도 자동으로 선택하지 않는다.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .. import api as real_api
from . import status as _status
from ..find_data import SelectedItem
from ..lan_protocol import LanError
from ..progress import fmt_bytes
from . import state as S
from . import texts as T
from .files_flow import (FIND_CATEGORIES, FIND_TIME_LIMIT, DataRow, FilesState, lan_error_text, norm, row_from_item, row_from_manual,
                         row_visible, signature_of)

# 현황 카드의 단계 표시(작업 이름 -> {진행 이벤트 phase -> 몇 번째 단계})
_status.PHASE_MAP[T.TASK_FIND_DATA] = {"find": 0, "scan": 0, "done": 1}
_status.PHASE_MAP[T.TASK_LAN_SEND] = {"scan": 0, "copy": 1, "verify": 2, "manifest": 2}
_status.PHASE_MAP[T.TASK_LAN_FIND] = {}
_status.PHASE_MAP[T.TASK_LAN_DIAG] = {}

_RX_ACTIVE = ("waiting", "connected", "receiving")
_SECS = {"design": "design", "photo": "photo", "video": "video", "doc": "doc"}


def _clean(path: str) -> str:
    return (path or "").strip().strip("\"'“”‘’`").strip()


class FilesMixin:
    # 시험에서 가짜를 넣는 자리(None 이면 실제 모듈)
    data_api: Any = None
    lan_api: Any = None
    bundle_temp_root: str | None = None  # 설정 묶음의 임시 복사본을 둘 위치(None 이면 시스템 임시 폴더). 시험에서 바꿔 끼운다

    # ------------------------------------------------------------------ 기본
    @property
    def files(self) -> FilesState:
        f = self.__dict__.get("_files")
        if f is None:
            f = self.__dict__["_files"] = FilesState()
        return f

    def _data(self):
        if self.data_api is not None:
            return self.data_api
        from .. import data_api
        return data_api

    def _lan(self):
        if self.lan_api is not None:
            return self.lan_api
        from .. import lan_api
        return lan_api

    def _files_on_go(self, screen: str) -> None:
        """다른 흐름의 화면으로 가면 '선택 목록으로 복사' 모드를 끝낸다(예전 단일 폴더 흐름이 선택 목록을 쓰지 않게)."""
        f = self.files
        if f.use_selection and screen not in (S.TRANSFER, S.METHOD, S.LAN_RECV, S.LAN_SEND, S.FIND, S.PLACE):  # stage50: 놓기 화면에 다녀와도 고른 목록은 유지
            self._end_selection_mode()

    def _end_selection_mode(self) -> None:
        f, s = self.files, self.state
        f.use_selection = False
        f.transfer_items = []
        if s.transfer_src and s.transfer_src == f.signature:
            s.transfer_src = ""
            s.transfer_report, s.transfer_preview_key, s.transfer_blocked = None, None, ""
            s.transfer_preview_opts = s.transfer_preview_split = None
            s.transfer_was_dry_run = True
        f.signature = ""

    # ------------------------------------------------------------------ 1/4 찾기
    def find_data_start(self) -> None:
        """'내 파일 찾기'. 읽기만 한다. 시간 상한에 닿으면 지금까지 찾은 결과를 보여 준다."""
        f = self.files
        d = self._data()
        holder: dict[str, Any] = {}

        def work(progress, token):
            holder["token"] = token
            kw = _supported_kw(d.run_find_data, cancel=token, progress=progress, progress_info=progress, time_limit=FIND_TIME_LIMIT)
            return d.run_find_data(None, **kw)

        def done(res):
            f.result = res
            f.find_error, f.find_cancelled = "", False
            known = {norm(p) for p in f.extra}
            f.selected = {k for k in f.selected if k in known}  # 자동 선택 없음: 직접 추가한 폴더의 선택만 남긴다
            self.emit("find_data_done", res)

        def failed():
            tok = holder.get("token")
            f.find_cancelled = bool(tok is not None and tok.is_set())
            f.find_error = "" if f.find_cancelled else T.FD_FAILED
            self.emit("files_changed")

        self._run(T.TASK_FIND_DATA, work, done, cancellable=True, on_fail=failed)

    def data_rows(self, visible_only: bool = True) -> list[DataRow]:
        f = self.files
        rows: list[DataRow] = []
        seen: set[str] = set()
        res = f.result
        if res is not None:
            items = sorted([*res.projects, *res.user_folders], key=lambda it: -int(it.total_bytes or 0))
            for it in items:
                k = norm(it.path)
                if k in seen:
                    continue
                seen.add(k)
                rows.append(row_from_item(it, k in f.selected))
        for p in f.extra:
            k = norm(p)
            if k not in seen:
                seen.add(k)
                rows.append(row_from_manual(p, k in f.selected))
        return [r for r in rows if row_visible(r, f.filters)] if visible_only else rows

    def set_data_filter(self, cat: str, on: bool) -> None:
        if cat not in FIND_CATEGORIES:
            raise _guard(T.G_NOT_IN_LIST)
        f = self.files
        (f.filters.add if on else f.filters.discard)(cat)
        self.emit("files_changed")

    def set_data_selected(self, path: str, on: bool) -> None:
        if self.state.busy:
            raise _guard(T.G_BUSY)
        k = norm(path)
        if not any(norm(r.path) == k for r in self.data_rows(visible_only=False)):
            raise _guard(T.G_NOT_IN_LIST)
        f = self.files
        (f.selected.add if on else f.selected.discard)(k)
        self.emit("files_changed")

    def select_visible_data(self, on: bool) -> int:
        """지금 보이는 줄 전체를 선택/해제한다(민감한 항목 개념이 없어 단순하다). 바뀐 개수를 돌려준다."""
        if self.state.busy:
            raise _guard(T.G_BUSY)
        f = self.files
        n = 0
        for r in self.data_rows():
            k = norm(r.path)
            if on and k not in f.selected:
                f.selected.add(k)
                n += 1
            elif not on and k in f.selected:
                f.selected.discard(k)
                n += 1
        self.emit("files_changed")
        return n

    def data_selection(self) -> list[str]:
        """선택한 폴더 경로 목록(표 순서)."""
        return [r.path for r in self.data_rows(visible_only=False) if r.selected]

    def data_total(self) -> tuple[int, int, int]:
        """(선택한 폴더 수, 알려진 용량 합계(바이트), 용량을 모르는 폴더 수)."""
        sel = [r for r in self.data_rows(visible_only=False) if r.selected]
        known = sum(r.total_bytes or 0 for r in sel if r.total_bytes is not None)
        return len(sel), known, sum(1 for r in sel if r.total_bytes is None)

    def data_total_text(self) -> str:
        n, size, unknown = self.data_total()
        b = self.bundle_item()
        bsize = fmt_bytes(b.total_bytes or 0) if b is not None else ""
        if not n:
            return T.FD_TOTAL_ONLY_BUNDLE.format(note=T.BUNDLE_INCLUDED, size=bsize) if b is not None else T.FD_TOTAL_NONE
        text = T.FD_TOTAL.format(n=n, size=fmt_bytes(size))
        text += (" " + T.FD_TOTAL_UNKNOWN.format(k=unknown) if unknown else "")
        return text + (T.FD_TOTAL_BUNDLE.format(note=T.BUNDLE_INCLUDED, size=bsize) if b is not None else "")

    def add_data_folder(self, path: str) -> str:
        """'직접 폴더 추가'. 폴더가 아니면 막는다. 이미 목록에 있으면 선택만 켠다(이미 선택됐으면 안내)."""
        if self.state.busy:
            raise _guard(T.G_BUSY)
        p = _clean(path)
        if not p or not os.path.isdir(p):
            raise _guard(T.G_FD_NOT_FOLDER)
        k = norm(p)
        f = self.files
        known = {norm(r.path): r for r in self.data_rows(visible_only=False)}
        if k in known:
            if known[k].selected:
                raise _guard(T.G_FD_ALREADY)
            f.selected.add(k)
            self.emit("files_changed")
            return known[k].path
        f.extra.append(p)
        f.selected.add(k)
        self.emit("files_changed")
        return p

    def set_data_junk(self, on: bool) -> None:
        self.files.junk = bool(on)
        self.emit("files_changed")

    def data_excluded(self) -> list[tuple[str, int, list[str]]]:
        """제외한 곳: [(이유, 폴더 수, 예시 경로 최대 3개)]."""
        res = self.files.result
        if res is None:
            return []
        out = []
        for reason, n in sorted(res.excluded.items(), key=lambda kv: -kv[1]):
            ex = [e["path"] for e in res.excluded_examples if e.get("reason") == reason][:3]
            out.append((reason, int(n), ex))
        return out

    def data_excluded_total(self) -> int:
        return sum(n for _r, n, _e in self.data_excluded())

    def data_cloud_rows(self) -> list[tuple[str, int, int]]:
        """클라우드 전용(내려받지 않은) 파일이 있는 폴더: [(경로, 파일 수, 바이트)]."""
        return [(r.path, r.cloud_files, r.cloud_bytes) for r in self.data_rows(visible_only=False) if r.cloud_files]

    def data_summary_text(self) -> str:
        res = self.files.result
        if res is None:
            return ""
        n = len(res.projects) + len(res.user_folders)
        return T.FD_SUMMARY.format(n=n, files=f"{res.files_seen:,}", sec=f"{res.seconds:.0f}")

    def data_selected_items(self) -> list[SelectedItem]:
        out = []
        for r in self.data_rows(visible_only=False):
            if r.selected:
                out.append(SelectedItem(r.path, True, r.total_bytes, r.total_files, r.kind))
        b = self.bundle_item()
        if b is not None:  # 체크한 경우에만(자동으로 넣지 않는다)
            out.append(b)
        return out

    # ------------------------------------------------------------------ ① 에서 만든 설정 묶음 함께 보내기(stage34)
    def bundle_source(self) -> tuple[str, int] | None:
        """함께 보낼 수 있는 설정 묶음(.zip): 마지막으로 내보낸 결과 파일, 없으면 기본 저장 위치(바탕화면)의 pcmigrate-settings.zip.
        실제로 파일이 있을 때만 (경로, 크기). 읽기만 한다."""
        cands = []
        last = getattr(self.state, "export_output", "") or ""
        if last:
            cands.append(last)
        try:
            cands.append(self.default_export_path())
        except Exception:  # noqa: BLE001 - 기본 위치를 알 수 없으면 마지막 내보내기 결과만 본다
            pass
        for c in cands:
            try:
                if c and os.path.isfile(c) and c.lower().endswith(".zip"):
                    return c, int(os.path.getsize(c))
            except OSError:
                continue
        return None

    def bundle_included(self) -> bool:
        return bool(self.files.bundle_include and self.bundle_source() is not None)

    def set_bundle_included(self, on: bool) -> None:
        if self.state.busy:
            raise _guard(T.G_BUSY)
        if on and self.bundle_source() is None:
            raise _guard(T.G_BUNDLE_GONE)
        f = self.files
        if bool(on) != f.bundle_include and f.use_selection:
            self._end_selection_mode()  # 보낼 목록이 바뀌므로 앞서 만든 복사 준비(미리보기)는 다시 만든다
        f.bundle_include = bool(on)
        self.emit("files_changed")

    def bundle_label(self) -> str:
        """체크 줄에 보일 문구('① 에서 만든 설정 묶음 이름(크기) — 함께 보내기'). 묶음이 없으면 빈 문자열."""
        b = self.bundle_source()
        return T.FD_BUNDLE_CHECK.format(name=os.path.basename(b[0]), size=fmt_bytes(b[1])) if b else ""

    def _bundle_stage_dir(self, src: str) -> str:
        root = self.bundle_temp_root or tempfile.gettempdir()
        return os.path.join(root, "pcmigrate-bundle-" + hashlib.sha1(norm(src).encode("utf-8", "replace")).hexdigest()[:8])

    def bundle_item(self) -> SelectedItem | None:
        """보낼 목록에 넣을 '설정 묶음 임시 폴더' 항목(체크했고 파일이 있을 때만). 폴더 단위 전송 계층에 맞춰 폴더로 넣는다."""
        if not self.files.bundle_include:
            return None
        b = self.bundle_source()
        if b is None:
            return None
        return SelectedItem(self._bundle_stage_dir(b[0]), True, b[1], 1, T.BUNDLE_LABEL, T.BUNDLE_DEST_DIR)

    @staticmethod
    def is_bundle_item(item: Any) -> bool:
        return getattr(item, "label", "") == T.BUNDLE_LABEL

    def bundle_note(self, items: list | None = None) -> str:
        """합계/확인창/결과에 보일 '설정 묶음 1개 포함'(없으면 빈 문자열)."""
        items = self.data_selected_items() if items is None else items
        return T.BUNDLE_INCLUDED if any(self.is_bundle_item(i) for i in items) else ""

    @contextlib.contextmanager
    def bundle_staging(self, items: list):
        """보내기/복사 직전에 설정 묶음만 담은 임시 폴더를 만들고(원본 zip 은 읽기만), 끝나면(취소·실패 포함) 지운다.
        임시 폴더 이름은 원본 경로로 정해져 있어 미리보기와 복사가 같은 경로를 본다. 우리가 만들지 않은 내용이 있으면 건드리지 않고 멈춘다."""
        bundle = [i for i in items if self.is_bundle_item(i)]
        if not bundle:
            yield
            return
        src = self.bundle_source()
        if src is None:
            raise _guard(T.G_BUNDLE_GONE)
        stage, name = bundle[0].path, os.path.basename(src[0])
        copy = os.path.join(stage, name)
        try:
            if os.path.lexists(stage):
                if not os.path.isdir(stage) or any(e != name for e in os.listdir(stage)):
                    raise OSError("unexpected temp content")
                if os.path.lexists(copy):
                    os.remove(copy)
                os.rmdir(stage)
            os.makedirs(stage)
            shutil.copy2(src[0], copy)
        except OSError as e:
            self._bundle_cleanup(stage, name)
            raise _guard(T.G_BUNDLE_STAGE) from e
        try:
            yield
        finally:
            self._bundle_cleanup(stage, name)

    @staticmethod
    def _bundle_cleanup(stage: str, name: str) -> None:
        try:
            os.remove(os.path.join(stage, name))
        except OSError:
            pass
        try:
            os.rmdir(stage)
        except OSError:
            pass

    def data_has_send(self) -> bool:
        """보낼 것이 있는가(체크한 폴더가 있거나, 설정 묶음만 함께 보내기로 체크했다)."""
        return bool(self.data_selection()) or self.bundle_item() is not None

    # ------------------------------------------------------------------ 2/4 방법
    def files_go_method(self) -> None:
        if self.state.busy:
            raise _guard(T.G_BUSY)
        if not self.data_has_send():
            raise _guard(T.G_FD_NEED_SELECTION)
        self.go(S.METHOD)

    def set_method(self, method: str) -> None:
        if method not in (T.MV_DIRECT, T.MV_EXTERNAL, T.MV_SHARE):
            raise _guard(T.G_NOT_IN_LIST)
        self.files.method = method
        self.emit("files_changed")

    def set_role(self, role: str) -> None:
        if role not in (T.MV_ROLE_SEND, T.MV_ROLE_RECV):
            raise _guard(T.G_NOT_IN_LIST)
        self.files.role = role
        self.emit("files_changed")

    def files_method_reason(self) -> str:
        """'다음' 을 누를 수 없는 이유(빈 문자열이면 눌러도 됨)."""
        f = self.files
        if not f.method:
            return T.WHY_MV_NEED_METHOD
        if f.method == T.MV_DIRECT and not f.role:
            return T.WHY_MV_NEED_ROLE
        if not (f.method == T.MV_DIRECT and f.role == T.MV_ROLE_RECV) and not self.data_has_send():
            return T.WHY_FD_NEED_SELECTION
        return ""

    def files_continue(self) -> None:
        """옮기는 방법 화면의 '다음'."""
        if self.state.busy:
            raise _guard(T.G_BUSY)
        why = self.files_method_reason()
        if why:
            raise _guard(why)
        f = self.files
        if f.method == T.MV_DIRECT:
            self.go(S.LAN_RECV if f.role == T.MV_ROLE_RECV else S.LAN_SEND)
        else:
            self.enter_selection_transfer(T.MODE_EXTERNAL if f.method == T.MV_EXTERNAL else T.MODE_NETWORK)

    def files_receive_only(self) -> None:
        """새 PC 에서는 고를 폴더가 없다: 바로 받기 화면으로."""
        f = self.files
        f.method, f.role = T.MV_DIRECT, T.MV_ROLE_RECV
        self.go(S.LAN_RECV)

    def files_legacy_transfer(self) -> None:
        """폴더 하나를 직접 입력하는 기존 복사 화면(새 PC 의 2단계 확인 포함)."""
        self._end_selection_mode()
        self.go(S.TRANSFER)

    def enter_selection_transfer(self, mode: str) -> None:
        """고른 폴더 목록을 가지고 기존 복사 화면(미리보기 → 복사 → 검사)으로 들어간다."""
        items = self.data_selected_items()
        if not items:
            raise _guard(T.G_FD_NEED_SELECTION)
        f, s = self.files, self.state
        sig = signature_of([i.path for i in items], f.junk)
        same = f.use_selection and f.signature == sig and s.transfer_src == sig
        f.use_selection, f.transfer_items, f.signature = True, items, sig
        self.set_transfer_flow(mode, 1)
        if not same:  # 선택이 바뀌었으면 이전 미리보기/결과는 무효
            s.transfer_src = sig
            s.transfer_report, s.transfer_preview_key, s.transfer_blocked = None, None, ""
            s.transfer_preview_opts = s.transfer_preview_split = None
            s.transfer_was_dry_run = True
        self.go(S.TRANSFER)

    def has_selection(self) -> bool:
        """복사 화면이 '선택 목록' 흐름인가(1단계 복사일 때만. 새 PC 의 2단계/검사는 예전처럼 폴더를 입력한다)."""
        f = self.files
        return bool(f.use_selection and f.transfer_items and self.state.transfer_stage == 1)

    def sync_selection_src(self) -> None:
        """선택 목록 흐름이면 복사 원본 표식을 맞추고, 2단계로 바꾸면 비운다(화면이 입력칸을 보일 수 있게)."""
        f, s = self.files, self.state
        if not (f.use_selection and f.transfer_items):
            return
        if s.transfer_stage == 1:
            if s.transfer_src != f.signature:
                s.transfer_src = f.signature
                s.transfer_report, s.transfer_preview_key, s.transfer_blocked = None, None, ""
                s.transfer_preview_opts = s.transfer_preview_split = None
                s.transfer_was_dry_run = True
        elif s.transfer_src == f.signature:
            s.transfer_src = ""

    def src_display(self) -> str:
        """확인 창에 보일 '원본' 표시(선택 목록이면 폴더 수와 앞쪽 경로, 아니면 입력한 원본 폴더). 설정 묶음이 있으면 한 줄 더."""
        if not self.has_selection():
            return self.state.transfer_src
        items = [i for i in self.files.transfer_items if not self.is_bundle_item(i)]
        lines = [T.TR_SELECTION_SRC.format(n=len(items))] if items else []
        lines += ["  · " + T.ellipsize_path(i.path, 70) for i in items[:6]]
        if len(items) > 6:
            lines.append("  · " + T.TR_SELECTION_MORE.format(n=len(items) - 6))
        lines += self._bundle_lines(self.files.transfer_items)
        return "\n".join(lines)

    def _bundle_lines(self, items: list) -> list[str]:
        out = []
        for i in items:
            if self.is_bundle_item(i):
                b = self.bundle_source()
                out.append(T.BUNDLE_LINE.format(name=os.path.basename(b[0]) if b else T.BUNDLE_LABEL, size=fmt_bytes(i.total_bytes or 0)))
        return out

    def transfer_back_screen(self) -> str:
        return S.METHOD if self.files.use_selection and self.files.transfer_items else S.START

    def selection_box_text(self) -> str:
        """복사 화면의 '옮길 폴더' 요약(처음 몇 곳의 경로 포함). 설정 묶음이 있으면 '설정 묶음 1개 포함' 줄."""
        all_items = self.files.transfer_items
        items = [i for i in all_items if not self.is_bundle_item(i)]
        known = sum(i.total_bytes or 0 for i in items if i.total_bytes is not None)
        unknown = sum(1 for i in items if i.total_bytes is None)
        size = ("약 " + fmt_bytes(known)) + (" 이상" if unknown else "")
        lines = [T.TR_SELECTION_BOX.format(n=len(items), size=size)]
        lines += ["  · " + T.ellipsize_path(i.path, 70) for i in items[:4]]
        if len(items) > 4:
            lines.append("  · " + T.TR_SELECTION_MORE.format(n=len(items) - 4))
        lines += ["  · " + t for t in self._bundle_lines(all_items)]
        return "\n".join(lines)

    # ---- 보내는 쪽 화면의 한 줄 요약/확인 문구
    def _folders_and_size(self, items: list) -> tuple[list, str]:
        folders = [i for i in items if not self.is_bundle_item(i)]
        known = sum(i.total_bytes or 0 for i in folders if i.total_bytes is not None)
        return folders, ("약 " + fmt_bytes(known)) + (" 이상" if any(i.total_bytes is None for i in folders) else "")

    def tx_selection_text(self, items: list | None = None) -> str:
        items = self.lan_send_items() if items is None else items
        folders, size = self._folders_and_size(items)
        text = T.TX_SELECTION.format(n=len(folders), size=size) if folders else T.TX_SELECTION_UNKNOWN.format(n=0)
        note = self.bundle_note(items)
        return text + (" · " + note if note else "")

    def tx_confirm_text(self, target: str, items: list | None = None) -> str:
        items = self.lan_send_items() if items is None else items
        folders, size = self._folders_and_size(items)
        text = T.TX_CONFIRM.format(n=len(folders), size=size, target=target)
        lines = self._bundle_lines(items)
        return ("\n".join(lines) + "\n\n" + text) if lines else text

    def files_stage(self, screen: str) -> int:
        """맨 위 단계 표시줄용: 이 화면이 '내 파일 옮기기' 4단계 흐름의 몇 번째인가(흐름 밖이면 0)."""
        f, s = self.files, self.state
        if screen == S.FIND:
            return 1
        if screen == S.METHOD:
            return 2
        if screen == S.LAN_RECV:
            return 4 if f.rx_state == "done" else 3
        if screen == S.LAN_SEND:
            return 4 if f.tx_report is not None else 3
        if screen == S.TRANSFER and self.has_selection():
            done = s.transfer_report is not None and not s.transfer_was_dry_run
            return 4 if done else 3
        return 0

    # ------------------------------------------------------------------ 복사 화면(선택 목록 흐름)에서 쓰는 점검/실행/요약
    def source_folders(self) -> list[str]:
        """결과 파일을 저장하면 안 되는 원본 폴더들(원본은 읽기만 하므로 그 안에 쓰지 않는다)."""
        s = self.state
        if self.is_verify_flow():
            return [s.verify_target]
        if self.has_selection():
            return [i.path for i in self.files.transfer_items if not self.is_bundle_item(i)]
        return [s.transfer_src]

    def _check_selection_paths(self, dst: str):
        from .controller import PathCheck
        s, f = self.state, self.files
        lines: list[tuple[bool, str]] = []
        items = f.transfer_items
        missing = [i.path for i in items if not self.is_bundle_item(i) and not os.path.isdir(i.path)]  # 설정 묶음 임시 폴더는 시작할 때 만든다
        if not items:
            lines.append((False, T.G_FD_NEED_SELECTION))
        elif missing:
            lines.append((False, T.G_FD_SELECTION_MISSING.format(path=missing[0])))
        else:
            lines.append((True, T.G_FD_SELECTION_OK.format(n=len(items))))
        if not dst:
            lines.append((False, T.G_NEED_DEST))
        elif items and not missing:
            rd = os.path.realpath(dst)
            inside = False
            for i in items:
                rs = os.path.realpath(i.path)
                try:
                    if os.path.commonpath([rs, rd]) == rs:
                        inside = True
                        break
                except ValueError:  # 다른 드라이브
                    continue
            lines.append((False, T.G_DEST_INSIDE) if inside else (True, T.G_DEST_OK))
        info, warnings = [], []
        d = self.describe_dest(dst) if dst else None
        if d is not None:
            fs_name = d.fs or (T.TR_FS_UNKNOWN_NOTE if d.kind == "network" else T.TR_FS_UNKNOWN)
            free = fmt_bytes(d.free) if d.free is not None else T.TR_FS_UNKNOWN
            info.append(T.TR_DEST_INFO.format(kind=T.TR_KIND_LABELS.get(d.kind, T.TR_KIND_LABELS["unknown"]), fs=fs_name, free=free))
            if d.fs == "FAT32":
                warnings.append(T.TR_FAT32_WARN)
            elif d.fs == "EXFAT":
                warnings.append(T.TR_EXFAT_WARN)
            if d.kind == "network":
                warnings.append(T.TR_NETWORK_NOTE)
            if d.kind == "cdrom":
                lines.append((False, T.TR_CDROM_BLOCK))
        return PathCheck(all(ok for ok, _ in lines) and bool(items) and bool(dst), lines, info, warnings)

    @staticmethod
    def _is_multi(rep: Any) -> bool:
        return rep is not None and hasattr(rep, "item_errors") and hasattr(rep, "items")

    def _multi_summary(self, rep) -> dict:
        """선택 목록 복사 결과(MultiReport)를 복사 화면이 쓰는 요약 모양으로 바꾼다."""
        d = rep.to_dict()
        counts = d["summary"]
        failures = [(f.src, T.TR_STATUS_LABELS.get(f.status, f.status), T.friendly_detail(f.error)) for _it, f in rep.failures]
        failures += [(it.src, T.TR_STATUS_LABELS.get("failed", "실패"), T.friendly_detail(it.error)) for it in rep.item_errors]
        cloud = [r.src for it in rep.items if it.report is not None for r in it.report.results if r.status == "cloud_only"]
        repaired = [x["file"] for it in rep.items if it.report is not None for x in it.report.meta.get("repaired", [])]
        return {"dry_run": self.state.transfer_was_dry_run, "counts": counts,
                "verified": counts.get("verified", 0), "mismatch": counts.get("mismatch", 0), "missing": counts.get("missing", 0),
                "cloud_only": counts.get("cloud_only", 0), "failed": counts.get("failed", 0) + len(rep.item_errors),
                "failures": failures, "cloud_files": cloud, "repaired": repaired, "pending": counts.get("pending_verify", 0),
                "added": 0, "mode": "multi", "io": {}, "split": 0, "manifest_complete": True, "manifest_errors": 0,
                "verification_complete": d.get("verification_complete"), "success": d.get("success"),
                "bundle": 1 if any(self.is_bundle_item(i) for i in self.files.transfer_items) else 0}

    def _start_selection_transfer(self, dry_run: bool, strict: bool, include_cloud: bool, report_path: str, write_confirmed: bool,
                                  verify_only: bool, retry_failed: bool, workers: int | None, split_large: bool) -> None:
        from .controller import _accepts
        s, f = self.state, self.files
        check = self.check_transfer_paths(s.transfer_src, s.transfer_dst)
        if not check.ok:
            raise _guard(" ".join(t for ok, t in check.lines if not ok))
        if report_path:
            for folder in self.source_folders():
                if real_api.report_inside(report_path, folder):
                    raise _guard(T.G_REPORT_INSIDE_SOURCE)
        key = (s.transfer_src, s.transfer_dst)
        if verify_only:
            if not os.path.isdir(s.transfer_dst):
                raise _guard(T.G_NEED_COPY_FIRST)
        elif not dry_run:
            if not write_confirmed:
                raise _guard(T.G_TRANSFER_CONFIRM)
            if s.transfer_preview_key != key:
                raise _guard(T.G_TRANSFER_NEED_PREVIEW)
            if s.transfer_preview_opts is not None and s.transfer_preview_opts != (bool(include_cloud), bool(strict)):
                raise _guard(T.G_TRANSFER_OPTIONS_CHANGED)
            if s.transfer_preview_split is not None and s.transfer_preview_split != bool(split_large):
                raise _guard(T.G_TRANSFER_OPTIONS_CHANGED)
            if s.transfer_blocked:
                raise _guard(s.transfer_blocked)
        d = self._data()
        extra: dict = {}
        if not dry_run and not verify_only:
            out = self.manifest_out_path()
            if out:
                extra["manifest_out"] = out
        if verify_only:
            extra["verify_only"] = True
        if retry_failed:
            extra["retry_failed"] = True
        if workers:
            extra["workers"] = workers
        if split_large:
            if not _accepts(d.run_transfer_selection, "split_large"):
                raise _guard(T.G_SPLIT_UNSUPPORTED)
            extra["split_large"] = True
        items = list(f.transfer_items)
        junk = f.junk

        def work(progress, token):
            with self.bundle_staging(items):  # 설정 묶음이 있으면 임시 폴더에 복사본을 만들고, 끝나면(실패·취소 포함) 지운다
                return d.run_transfer_selection(items, s.transfer_dst, dry_run=dry_run, include_cloud=include_cloud, strict=strict,
                                                progress=progress, cancel=token, deps=self.deps, warn=progress, progress_info=progress,
                                                exclude_junk=junk, **extra)

        def done(rep):
            s.transfer_report, s.transfer_was_dry_run = rep, dry_run
            if dry_run:
                s.transfer_preview_key = key
                s.transfer_preview_opts = (bool(include_cloud), bool(strict))
                s.transfer_preview_split = bool(split_large)
                meta = getattr(rep, "meta", {}) or {}
                s.transfer_blocked = T.WHY_SPACE if meta.get("space_blocked") else ""
            else:
                s.transfer_blocked = ""
            self.emit("transfer_done", rep)
            if report_path:
                try:
                    rep.write(Path(report_path))
                except OSError as e:
                    self.emit("error", self.report_error(e, label))
        label = (T.TASK_TRANSFER_PREVIEW if dry_run else T.TASK_VERIFY_AGAIN if verify_only
                 else T.TASK_RETRY if retry_failed else T.TASK_TRANSFER)
        self._run(label, work, done, cancellable=True)

    # ------------------------------------------------------------------ 3/4 받는 쪽(새 PC)
    def lan_dest(self) -> str:
        f = self.files
        if not f.dest:
            f.dest = self._lan().default_receive_dir()
        return f.dest

    def lan_set_dest(self, path: str) -> None:
        if self.files.rx_state in _RX_ACTIVE:
            raise _guard(T.G_RX_RUNNING)
        p = _clean(path)
        if not p:
            raise _guard(T.G_RX_NEED_DEST)
        self.files.dest = p
        self.emit("lan_rx", None)

    def lan_receive_active(self) -> bool:
        return self.files.rx_state in _RX_ACTIVE

    def lan_receive_start(self) -> bool:
        """받기를 시작한다(코드와 접속 주소가 생긴다). 시작하지 못하면 화면에 보일 쉬운 문구를 files.rx_error 에 두고 False."""
        import queue
        f = self.files
        if f.rx_state in _RX_ACTIVE:
            raise _guard(T.G_RX_RUNNING)
        dest = _clean(self.lan_dest())
        if not dest:
            raise _guard(T.G_RX_NEED_DEST)
        f.rx_queue = queue.Queue()
        f.rx_log, f.rx_info, f.rx_plan, f.rx_result, f.rx_error = [], None, None, None, ""
        try:
            rx = self._lan().start_receiver(dest, on_event=f.rx_queue.put)
        except LanError as e:
            f.rx_state, f.rx_error = "error", lan_error_text(e.code, str(e))
            self.emit("lan_rx", None)
            return False
        f.rx = rx
        f.rx_state = "waiting"
        f.rx_code = str(rx.code)
        f.rx_addresses = list(rx.addresses)
        f.rx_kinds = dict(getattr(rx, "address_kinds", {}) or {})
        f.rx_warnings = list(getattr(rx, "warnings", []) or [])
        self.emit("lan_rx", None)
        return True

    def lan_receive_stop(self) -> None:
        f = self.files
        rx = f.rx
        if rx is not None:
            try:
                rx.stop()
            except Exception:  # noqa: BLE001 - 멈추다 난 오류가 화면을 막지 않게
                self.log.warning("receiver stop failed")
        if f.rx_state in _RX_ACTIVE:
            f.rx_state = "stopped"
            self._rx_note(T.RX_STATE["stopped"])
        self.lan_poll()
        self.emit("lan_rx", None)

    def _rx_note(self, text: str) -> None:
        f = self.files
        if text and (not f.rx_log or f.rx_log[-1] != text):
            f.rx_log.append(text)
            del f.rx_log[:-8]

    def lan_poll(self) -> int:
        """받기 스레드가 남긴 소식을 화면 스레드에서 처리한다. 처리한 개수를 돌려준다."""
        import queue
        f = self.files
        n = 0
        while True:
            try:
                ev = f.rx_queue.get_nowait()
            except queue.Empty:
                break
            n += 1
            self._rx_event(ev)
        if n:
            self.emit("lan_rx", None)
        return n

    def _rx_event(self, ev: dict) -> None:
        f = self.files
        kind = ev.get("type", "")
        if kind == "peer_connected":
            f.rx_state = "connected"
            self._rx_note(T.RX_STATE["connected"])
        elif kind == "plan":
            f.rx_plan = {"files": int(ev.get("files", 0) or 0), "bytes": int(ev.get("bytes", 0) or 0)}
            f.rx_state = "receiving"
            self._rx_note(T.RX_PLAN.format(files=f.rx_plan["files"], size=fmt_bytes(f.rx_plan["bytes"])))
        elif kind == "progress":
            if ev.get("info") is not None:
                f.rx_info = ev["info"]
            if f.rx_state in ("waiting", "connected"):
                f.rx_state = "receiving"
        elif kind == "auth_failed":
            self._rx_note(T.RX_AUTH_FAILED)
        elif kind == "disconnected":
            f.rx_state = "waiting" if f.rx_state in _RX_ACTIVE else f.rx_state
            self._rx_note(T.RX_DISCONNECTED)
        elif kind == "cancelled":
            f.rx_state = "waiting" if f.rx_state in _RX_ACTIVE else f.rx_state
            self._rx_note(T.RX_CANCELLED)
        elif kind == "error":
            f.rx_error = lan_error_text(str(ev.get("code", "")), str(ev.get("message", "")))
            if getattr(f.rx, "state", "") == "error":
                f.rx_state = "error"
            self._rx_note(T.RX_ERROR_PREFIX.format(msg=f.rx_error))
        elif kind == "done":
            f.rx_result = {k: ev.get(k, 0) for k in ("files", "bytes", "ok", "mismatch", "failed", "sender_failed", "dest")}
            f.rx_state = "done"
            self._rx_note(T.RX_STATE["done"])
        elif kind == "stopped":
            if f.rx_state in _RX_ACTIVE:
                f.rx_state = "stopped"

    def rx_result_text(self) -> tuple[str, bool]:
        """받기 결과 요약 문구와 '모두 정확한가'."""
        r = self.files.rx_result
        if not r:
            return "", False
        files = int(r.get("files", 0) or 0)
        bad = int(r.get("mismatch", 0) or 0) + int(r.get("failed", 0) or 0) + int(r.get("sender_failed", 0) or 0)
        ok = int(r.get("ok", 0) or 0)
        text = T.RX_RESULT.format(files=files, ok=ok, mismatch=int(r.get("mismatch", 0) or 0),
                                  failed=int(r.get("sender_failed", 0) or 0))
        return text, bad == 0 and ok >= files

    def rx_public_warning(self) -> bool:
        return any(k == "public" for k in self.files.rx_kinds.values())

    # ------------------------------------------------------------------ 3/4 보내는 쪽(구 PC)
    def lan_find_start(self) -> None:
        """'새 PC 찾기': 같은 네트워크에서 받기를 기다리는 PC 를 찾는다(짧은 안내 방송 1회. 시험에서는 가짜로 대체)."""
        f = self.files
        lan = self._lan()

        def work(progress, token):
            return lan.discover_receivers(2.0)

        def done(found):
            f.found = list(found or [])
            f.found_message = T.TX_FOUND_N.format(n=len(f.found)) if f.found else T.TX_FOUND_NONE
            if len(f.found) == 1:
                self.lan_pick_found(0)
            self.emit("lan_found", f.found)
        self._run(T.TASK_LAN_FIND, work, done, cancellable=True)

    def lan_pick_found(self, index: int) -> str:
        f = self.files
        if not 0 <= index < len(f.found):
            raise _guard(T.G_NOT_IN_LIST)
        r = f.found[index]
        f.target = f"{r.ip}:{r.port}"
        self.emit("files_changed")
        return f.target

    def lan_set_target(self, target: str, code: str = "") -> None:
        f = self.files
        f.target, f.code = _clean(target), (code or "").strip()

    def lan_diagnose_start(self, target: str, code: str = "") -> None:
        """'연결 진단': 접속 시험만 한다(아무것도 보내지 않는다). 한 줄 결과는 files.diag."""
        t = _clean(target)
        if not t:
            raise _guard(T.G_TX_NEED_ADDR)
        digits = "".join(ch for ch in (code or "") if ch.isdigit())
        use_code = digits if len(digits) == 6 else None
        f, lan = self.files, self._lan()

        def work(progress, token):
            return lan.run_diagnose(t, use_code)

        def done(diag):
            f.diag = T.TX_DIAG_RESULT.format(summary=getattr(diag, "summary", str(diag)))
            f.diag_code = str(getattr(diag, "code", "") or "")
            self.emit("lan_diag", diag)
        self._run(T.TASK_LAN_DIAG, work, done, cancellable=True)

    def lan_send_items(self) -> list:
        """보낼 폴더 목록(복사 화면에 넘긴 선택이 있으면 그것, 없으면 지금 체크한 폴더)."""
        f = self.files
        return list(f.transfer_items) if f.use_selection and f.transfer_items else self.data_selected_items()

    def lan_send_problem(self, target: str, code: str) -> str:
        """보내기를 시작할 수 없는 이유(빈 문자열이면 시작해도 됨)."""
        if not self.lan_send_items():
            return T.G_TX_NEED_SELECTION
        if not _clean(target):
            return T.G_TX_NEED_ADDR
        if not (code or "").strip():
            return T.G_TX_NEED_CODE
        return ""

    def lan_send_start(self, target: str, code: str, confirmed: bool = False) -> None:
        """연결하고 선택한 폴더를 보낸다. 새 PC 에 쓰는 일이므로 사람의 확인(confirmed)이 있어야 한다. 원본은 읽기만 한다."""
        why = self.lan_send_problem(target, code)
        if why:
            raise _guard(why)
        if not confirmed:
            raise _guard(T.G_TRANSFER_CONFIRM)
        items = self.lan_send_items()
        t, c = _clean(target), (code or "").strip()
        f, lan = self.files, self._lan()
        junk = f.junk
        f.tx_report, f.tx_error, f.tx_error_code, f.tx_cancelled = None, "", "", False
        f.tx_bundle = any(self.is_bundle_item(i) for i in items)
        holder: dict[str, Any] = {}

        def work(progress, token):
            holder["token"] = token
            try:
                with self.bundle_staging(items):  # 설정 묶음이 있으면 임시 폴더에 복사본을 만들고, 끝나면(실패·취소 포함) 지운다
                    rep = lan.run_send(t, c, selection=items, options={"exclude_junk": junk}, progress_info=progress, cancel=token)
            except LanError as e:  # 연결/코드 문제는 화면에 쉬운 문구로 보인다(오류 대화상자 대신)
                return {"lan_error": e.code}
            return {"report": rep}

        def done(res):
            if res.get("lan_error"):
                f.tx_error_code = res["lan_error"]
                f.tx_error = lan_error_text(f.tx_error_code)
            else:
                f.tx_report = res["report"]
            self.emit("lan_send_done", res)

        def failed():
            tok = holder.get("token")
            f.tx_cancelled = bool(tok is not None and tok.is_set())
            self.emit("files_changed")
        self._run(T.TASK_LAN_SEND, work, done, cancellable=True, on_fail=failed)

    def tx_result_lines(self) -> tuple[str, bool, list[str]]:
        """보내기 결과: (한 줄 요약, 모두 정확한가, 덧붙일 안내 줄들)."""
        rep = self.files.tx_report
        if not rep:
            return "", False, []
        v = rep.get("verification") or {}
        exact, mismatch, missing = int(v.get("exact", 0)), int(v.get("mismatch", 0)), int(v.get("missing", 0))
        files = int(rep.get("files", exact + mismatch + missing))
        text = T.TX_RESULT.format(files=files, exact=exact, mismatch=mismatch, missing=missing)
        ok = bool(rep.get("success")) and not mismatch and not missing
        notes = []
        cloud, junk = rep.get("cloud_only_skipped") or 0, rep.get("junk_excluded") or 0
        cloud_n = cloud if isinstance(cloud, int) else len(cloud)
        junk_n = junk if isinstance(junk, int) else int((junk or {}).get("files", 0)) if isinstance(junk, dict) else len(junk)
        if cloud_n or junk_n:
            notes.append(T.TX_SKIPPED_NOTE.format(n=cloud_n, junk=junk_n))
        if rep.get("failed_count"):
            notes.append(T.TX_FAILED_HEAD.format(n=rep["failed_count"]))
        if self.files.tx_bundle:
            notes.insert(0, T.BUNDLE_RESULT_NOTE)
        notes.append(T.TX_VERIFY_NEW_PC)
        return text, ok, notes


def _supported_kw(fn, **kw) -> dict:
    from .controller import _supported
    return _supported(fn, **kw)


def _guard(message: str) -> Exception:
    """controller.GuardError (순환 import 를 피하려고 그때그때 가져온다)."""
    from .controller import GuardError
    return GuardError(message)

