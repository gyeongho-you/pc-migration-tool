"""컨트롤러의 '복원 결과와 되돌리기' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

from typing import Any

from . import texts as T
from .controller_common import GuardError


class UndoMixin:
    # ---- 화면 6: 결과의 프로그램별 표와 되돌리기
    def result_programs(self) -> list[dict]:
        """결과 화면의 프로그램별 표: id/name/selected/new/overwrite/same/failed + undo(막힌 이유, 비었으면 가능)."""
        r = self.state.import_result
        if r is None or r.report is None:
            return []
        progs = getattr(r.plan, "programs", None) or []
        by_entry = {it.get("entry"): it.get("program", "") for it in r.plan.items}
        rows = {p["id"]: {"id": p["id"], "name": p["name"], "selected": p.get("selected", True), "new": 0, "overwrite": 0,
                          "same": 0, "failed": 0} for p in progs}
        for res in r.report.results:
            row = rows.get(by_entry.get(res.src, ""))
            if row is None:
                continue
            if res.status == "copied":
                row["overwrite" if res.backup else "new"] += 1
            elif res.status == "skipped_identical":
                row["same"] += 1
            elif res.status in ("failed", "rejected"):
                row["failed"] += 1
        out = list(rows.values())
        for row in out:
            row["undo"] = self.undo_reason(row["id"], row)
        return out

    def undo_reason(self, pid: str, row: dict | None = None) -> str:
        """프로그램별 [되돌리기] 가 막혀 있는 이유(없으면 빈 문자열). 이미 되돌렸으면 그 결과 문구."""
        r = self.state.import_result
        if r is None or r.report is None:
            return T.UNDO_NO_RECORD
        if not r.record_path:
            return T.UNDO_RECORD_FAILED if r.record_error else T.UNDO_NO_RECORD
        if self.state.busy:
            return T.WHY_BUSY
        done = self.state.undo_results.get(pid)
        if done is not None and getattr(done, "mode", "") == "applied":
            return T.UNDO_DONE_LABEL.format(n=self.undo_counts(done)[0])
        if row is None:
            row = next((x for x in self.result_programs() if x["id"] == pid), None)
        if row is None or not row["selected"] or not row["overwrite"]:
            return T.UNDO_NOTHING
        return ""

    @staticmethod
    def undo_counts(res: Any) -> tuple[int, int]:
        """적용한 되돌리기 결과의 (되돌린 개수, 못 한 개수)."""
        items = getattr(getattr(res, "plan", None), "items", [])
        return sum(1 for i in items if i.result == "restored"), sum(1 for i in items if i.result == "failed")

    @staticmethod
    def undo_preview_counts(res: Any) -> tuple[int, int, int]:
        """되돌리기 미리보기의 (되돌릴 개수, 지우지 않고 둘 새 파일 개수, 되돌릴 수 없는 개수)."""
        s = res.plan.summary()
        return s["restore"], s["new_kept"], s["problems"]

    def _undo_api(self):
        fn = getattr(self.api, "run_undo_import", None)
        if fn is None:
            raise GuardError(T.G_UNDO_UNSUPPORTED)
        return fn

    def start_undo_preview(self, pid: str) -> None:
        """프로그램 하나의 되돌리기 미리보기(아무것도 바꾸지 않음). 끝나면 undo_preview_done 이벤트."""
        fn = self._undo_api()
        why = self.undo_reason(pid)
        if why:
            raise GuardError(why)
        record = self.state.import_result.record_path

        def work(progress, token):
            return fn([pid], record=record, dry_run=True)

        def done(res):
            self.state.undo_preview[pid] = res
            self.emit("undo_preview_done", {"id": pid, "result": res})
        self._run(T.TASK_UNDO_PREVIEW, work, done)

    def start_undo(self, pid: str, confirmed: bool = False) -> None:
        """미리보기를 보고 확인한 뒤에만 실제로 되돌린다(새로 생긴 파일은 지우지 않는다). 끝나면 undo_done 이벤트."""
        if not confirmed:
            raise GuardError(T.G_NEED_FINAL_YES)
        fn = self._undo_api()
        why = self.undo_reason(pid)
        if why:
            raise GuardError(why)
        if pid not in self.state.undo_preview:
            raise GuardError(T.G_UNDO_UNKNOWN)
        record = self.state.import_result.record_path

        def work(progress, token):
            return fn([pid], record=record, yes=True)

        def done(res):
            self.state.undo_results[pid] = res
            self.emit("undo_done", {"id": pid, "result": res})
        self._run(T.TASK_UNDO, work, done)
