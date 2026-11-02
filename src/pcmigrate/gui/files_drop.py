"""컨트롤러의 '직접 추가한 파일·폴더'(끌어다 놓기, stage63/70) 부분. Controller 가 이 믹스인을 상속한다. 화면 없이 시험할 수 있다.

한 목록(f.drops)에 파일과 폴더를 함께 담는다(경로·수정 시각, 파일은 용량도 기억 / 복사하지 않음).
- 폴더: 폴더째 전송한다. 보낼 목록에 '선택 폴더'로 들어가며(data_selected_items) 임시 보관·2만 개 제한이 없고, 정크 제외 옵션은 기존 규칙 그대로다.
  크기는 목록에 담을 때 읽지 않는다(화면이 멈추지 않게): 합계에는 '크기를 아직 모릅니다'로 센다.
- 파일: 임시 보관 경로(drop_stage)로 보낸다. 겹침 제거/임시 보관/전송/정리는 전송을 시작할 때 백엔드가 한다. 최대 2만 개.
- 겹침(경로 정규화·대소문자 무시·접두 비교만, 파일 하나하나를 열지 않음): 직접 추가한 폴더가 체크한 자동 폴더 안이면 그 폴더는 빼고,
  체크한 자동 폴더를 품으면 바깥 폴더로 한 번만 보낸다(실제 제거는 plan_items 가 전송 시작 때 하고 리포트 dropped 에 남는다).
원본은 읽기만 한다. 한 항목이 읽히지 않아도 그 항목만 빼고 계속한다.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .. import drop_stage as ds
from ..progress import fmt_bytes
from . import texts as T
from .files_flow import norm, unsafe_data_folder


def _guard(message: str) -> Exception:
    from .controller import GuardError
    return GuardError(message)


def _under(k: str, base: str) -> bool:
    """k 가 base 와 같거나 그 안인가(정규화된 경로 접두 비교)."""
    return k == base or k.startswith(base.rstrip(os.sep) + os.sep)


def _clean(path: str) -> str:
    return (path or "").strip().strip("\"'“”‘’`").strip()


class DropMixin:
    def drop_entries(self) -> list:
        return list(self.files.drops)

    def drop_files(self) -> list:
        """임시 보관 경로로 보낼 파일 항목."""
        return [e for e in self.files.drops if not e.is_dir]

    def drop_dirs(self) -> list:
        """폴더째 보낼 폴더 항목."""
        return [e for e in self.files.drops if e.is_dir]

    def drop_total(self) -> tuple[int, int]:
        """(담은 항목 수(파일+폴더), 알려진 용량 합계 바이트)."""
        d = self.files.drops
        return len(d), sum(e.size for e in d if not e.unknown)

    def drop_unknown(self) -> int:
        """크기를 아직 모르는 폴더 수."""
        return sum(1 for e in self.files.drops if e.unknown)

    def drop_total_text(self, files_only: bool = False) -> str:
        """합계 문구. files_only: 폴더는 '선택 폴더'로 따로 세는 화면(복사/보내기)에서 직접 고른 파일만 센다."""
        if files_only:
            fl = self.drop_files()
            return T.DP_TOTAL.format(n=len(fl), size=fmt_bytes(sum(e.size for e in fl))) if fl else ""
        n, size = self.drop_total()
        if not n:
            return ""
        k = self.drop_unknown()
        return T.DP_TOTAL.format(n=n, size=fmt_bytes(size)) + (" " + T.FD_TOTAL_UNKNOWN.format(k=k) if k else "")

    def folder_cover(self) -> dict[str, str]:
        """겹쳐서 빠질 폴더: {빠질 폴더 경로: 포함하는 폴더 경로}. 자동 체크 폴더 + 직접 추가한 폴더 전체를 접두 비교만으로 따진다.
        같은 폴더가 둘이면 자동 체크 쪽을 남긴다(앞선 것이 우선)."""
        allf = [(norm(p), p) for p in self.data_selection()] + [(norm(e.path), e.path) for e in self.drop_dirs()]
        out: dict[str, str] = {}
        for i, (k, p) in enumerate(allf):
            for j, (k2, p2) in enumerate(allf):
                if i != j and _under(k, k2) and (k != k2 or j < i):
                    out.setdefault(p, p2)
                    break
        return out

    def _checked_keys(self) -> list[str]:
        """파일이 '이미 포함됨'인지 볼 때 쓰는 폴더들: 자동 체크 폴더 + 직접 추가한 폴더(모두 폴더째 가는 것)."""
        return [norm(p) for p in self.data_selection()] + [norm(e.path) for e in self.drop_dirs()]

    def drop_rows(self) -> list[tuple[str, str, str, str, bool]]:
        """표 줄: (경로, 용량, 수정한 날, 꼬리표, 이미 포함되는가)."""
        checked = self._checked_keys()
        cover = self.folder_cover()
        rows = []
        for e in self.files.drops:
            if e.is_dir:
                covered = e.path in cover
            else:
                covered = any(_under(norm(e.path), c) for c in checked)
            tag = T.DP_TAG_COVERED if covered else (T.DP_TAG_FOLDER if e.is_dir else "")
            when = time.strftime("%Y-%m-%d", time.localtime(e.mtime_ns / 1e9)) if e.mtime_ns else ""
            rows.append((e.path, "-" if e.unknown else fmt_bytes(e.size), when, tag, covered))
        return rows

    def drop_notes(self) -> list[str]:
        """상자 아래 안내 줄(겹쳐서 빠질 개수: 직접 추가한 파일·폴더와 안쪽 자동 폴더 모두). 실제 제거는 전송을 시작할 때 다시 계산한다."""
        n = sum(1 for r in self.drop_rows() if r[4])
        n += sum(1 for p in self.data_selection() if p in self.folder_cover())  # 직접 추가한 바깥 폴더에 포함돼 빠지는 자동 폴더
        return [T.DP_OVERLAP.format(n=n)] if n else []

    def add_drop_paths(self, paths) -> str:
        """'파일 추가'/'폴더 추가'/끌어다 놓기로 받은 경로들을 목록에 담고, 사용자에게 보일 안내 문구를 돌려준다.

        읽을 수 없거나 담을 수 없는 항목(시스템 폴더 등)은 빼고 나머지는 담는다. 파일 수가 상한(MAX_DROP_FILES)을 넘으면 파일은 하나도 담지 않고 막는다.
        """
        if self.state.busy:
            raise _guard(T.G_BUSY)
        cleaned = [c for c in (_clean(p) for p in paths or []) if c]
        if not cleaned:
            raise _guard(T.DP_VIRTUAL)
        f = self.files
        known = {norm(e.path) for e in f.drops}
        new, dup, bad, system = [], 0, 0, 0
        for p in cleaned:
            k = norm(p)
            if k in known:
                dup += 1
                continue
            isdir = os.path.isdir(p)
            if isdir and unsafe_data_folder(p):  # 드라이브 루트·Windows·Program Files·ProgramData·사용자 홈 루트: 자동 찾기와 같은 기준으로 거부
                system += 1
                continue
            known.add(k)
            e = ds.describe(p, shallow=True)  # 폴더는 안을 읽지 않는다(크기 모름)
            if e.error:
                bad += 1
                continue
            new.append(e)
        if sum(e.files for e in f.drops if not e.is_dir) + sum(e.files for e in new if not e.is_dir) > ds.MAX_DROP_FILES:
            raise _guard(T.DP_TOO_MANY.format(max=f"{ds.MAX_DROP_FILES:,}"))
        if not new and system and not dup and not bad:
            raise _guard(T.G_FD_SYSTEM_FOLDER)
        if new:
            self._selection_edited()
        f.drops.extend(new)
        self._drops_changed()
        parts = []
        if new:
            parts.append(T.DP_ADDED.format(n=len(new)))
        if dup:
            parts.append(T.DP_ALREADY.format(n=dup))
        if bad:
            parts.append(T.DP_BAD.format(n=bad))
        if system:
            parts.append(T.G_FD_SYSTEM_FOLDER)
        return " ".join(parts)

    def remove_drop_paths(self, paths) -> int:
        if self.state.busy:
            raise _guard(T.G_BUSY)
        gone = {norm(p) for p in paths or []}
        before = len(self.files.drops)
        self.files.drops[:] = [e for e in self.files.drops if norm(e.path) not in gone]
        self._drops_changed()
        return before - len(self.files.drops)

    def clear_drops(self) -> None:
        if self.state.busy:
            raise _guard(T.G_BUSY)
        self.files.drops.clear()
        self._drops_changed()

    def _drops_changed(self) -> None:
        f = self.files
        if f.use_selection:  # 이미 복사 화면에 넘긴 선택이 있으면 미리보기/결과는 무효(목록이 달라졌다)
            f.use_selection, f.transfer_items, f.signature = False, [], ""
        self.emit("files_changed")

    # ---- 전송 결과 보조
    @staticmethod
    def drop_result_lines(ds_info: dict[str, Any] | None) -> list[str]:
        """결과/미리보기 화면에 덧붙일 줄(DP_RESULT, 제외 안내, 정리 결과). 직접 고른 항목이 없었으면 빈 목록."""
        d = ds_info or {}
        if not d:
            return []
        failed = len(d.get("failed", []))
        skip = d.get("overlap_count", 0) + d.get("duplicate_count", 0) + d.get("cloud_only_total", 0) + d.get("skipped_links_total", 0)
        lines = [T.DP_RESULT.format(n=d.get("entries", 0), ok=d.get("staged_files", 0), skip=skip, bad=failed)]
        if d.get("overlap_count"):
            lines.append(T.DP_OVERLAP.format(n=d["overlap_count"]))
        if d.get("duplicate_count"):
            lines.append(T.DP_DUP.format(n=d["duplicate_count"]))
        if d.get("cloud_only_total"):
            lines.append(T.DP_CLOUD.format(n=d["cloud_only_total"]))
        if d.get("skipped_links_total"):
            lines.append(T.DP_LINK.format(n=d["skipped_links_total"]))
        if d.get("changed"):
            lines.append(T.DP_CHANGED.format(n=len(d["changed"])))
        if failed:
            lines.append(T.DP_GONE.format(n=failed))
        c = d.get("cleanup")
        if c:
            lines.append(T.DP_CLEANED if c.get("removed") else T.DP_CLEAN_FAIL)
        return lines
