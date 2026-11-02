"""컨트롤러의 '직접 고른 파일·폴더'(끌어다 놓기, stage63) 부분. Controller 가 이 믹스인을 상속한다. 화면 없이 시험할 수 있다.

목록에는 경로·용량·수정 시각만 기억한다(복사하지 않음). 겹침 제거/임시 보관/전송/정리는 전송을 시작할 때 백엔드(drop_stage)가 한다.
원본은 읽기만 한다. 한 항목이 읽히지 않아도 그 항목만 빼고 계속한다.
"""
from __future__ import annotations

import os
from typing import Any

from .. import drop_stage as ds
from ..progress import fmt_bytes
from . import texts as T
from .files_flow import norm


def _guard(message: str) -> Exception:
    from .controller import GuardError
    return GuardError(message)


def _under(k: str, base: str) -> bool:
    return k == base or k.startswith(base.rstrip(os.sep) + os.sep)


def _clean(path: str) -> str:
    return (path or "").strip().strip("\"'“”‘’`").strip()


class DropMixin:
    def drop_entries(self) -> list:
        return list(self.files.drops)

    def drop_total(self) -> tuple[int, int]:
        """(담은 항목 수, 용량 합계 바이트)."""
        d = self.files.drops
        return len(d), sum(e.size for e in d)

    def drop_total_text(self) -> str:
        n, size = self.drop_total()
        return T.DP_TOTAL.format(n=n, size=fmt_bytes(size)) if n else ""

    def _checked_keys(self) -> list[str]:
        return [norm(p) for p in self.data_selection()]

    def drop_rows(self) -> list[tuple[str, str, str, str, bool]]:
        """표 줄: (경로, 용량, 수정한 날, 꼬리표, 선택한 폴더에 포함되는가)."""
        checked = self._checked_keys()
        rows = []
        for e in self.files.drops:
            covered = any(_under(norm(e.path), c) for c in checked)
            tag = T.DP_TAG_COVERED if covered else ""
            when = ""
            if e.mtime_ns:
                import time
                when = time.strftime("%Y-%m-%d", time.localtime(e.mtime_ns / 1e9))
            rows.append((e.path, fmt_bytes(e.size), when, tag, covered))
        return rows

    def drop_notes(self) -> list[str]:
        """상자 아래 안내 줄(겹침으로 빠질 개수). 실제 제거는 전송을 시작할 때 다시 계산한다."""
        n = sum(1 for r in self.drop_rows() if r[4])
        return [T.DP_OVERLAP.format(n=n)] if n else []

    def add_drop_paths(self, paths) -> str:
        """'파일 추가'/끌어다 놓기로 받은 경로들을 정리해, 파일은 직접 고른 목록에 담고 폴더는 위쪽 폴더 표에 추가한다(체크된 상태, 폴더째 전송).
        사용자에게 보일 안내 문구를 돌려준다.

        읽을 수 없는 항목은 빼고 나머지는 담는다. 파일 수가 상한(MAX_DROP_FILES)을 넘으면 파일은 하나도 담지 않고 막는다(폴더 추가는 이미 처리됨).
        """
        if self.state.busy:
            raise _guard(T.G_BUSY)
        cleaned = [c for c in (_clean(p) for p in paths or []) if c]
        if not cleaned:
            raise _guard(T.DP_VIRTUAL)
        f = self.files
        known = {norm(e.path) for e in f.drops}
        new, dup, bad, folders = [], 0, 0, 0
        for p in cleaned:
            if os.path.isdir(p):  # 폴더는 직접 고른 파일이 아니라 폴더 표로: '직접 폴더 추가'와 같은 방식
                try:
                    self.add_data_folder(p)
                except Exception as e:  # noqa: BLE001 - 이미 체크된 폴더 등 안내 가드는 무시
                    from .controller import GuardError
                    if not isinstance(e, GuardError):
                        raise
                folders += 1
                continue
            k = norm(p)
            if k in known:
                dup += 1
                continue
            known.add(k)
            e = ds.describe(p)
            if e.error:
                bad += 1
                continue
            new.append(e)
        if sum(e.files for e in f.drops) + sum(e.files for e in new) > ds.MAX_DROP_FILES:
            if folders:
                self._drops_changed()
            raise _guard(T.DP_TOO_MANY.format(max=f"{ds.MAX_DROP_FILES:,}"))
        f.drops.extend(new)
        self._drops_changed()
        parts = []
        if folders:
            parts.append(T.DP_FOLDERS_MOVED.format(n=folders))
        if new:
            parts.append(T.DP_ADDED.format(n=len(new)))
        if dup:
            parts.append(T.DP_ALREADY.format(n=dup))
        if bad:
            parts.append(T.DP_BAD.format(n=bad))
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
        lines = [T.DP_RESULT.format(n=d.get("entries", 0), ok=d.get("staged_files", 0) - 0, skip=skip, bad=failed)]
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
