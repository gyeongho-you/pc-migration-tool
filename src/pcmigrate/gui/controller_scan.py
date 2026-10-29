"""컨트롤러의 '프로그램 찾기·후보 목록·선택' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

import os

from .. import coverage
from ..discover import fmt_size
from ..progress import fmt_bytes
from . import texts as T
from .controller_common import CandRow, GuardError, _supported


class ScanMixin:
    # ------------------------------------------------------------------ 구 PC 1) 조사
    def start_scan(self) -> None:
        def work(progress, token):
            return self.api.run_scan(self.state.rules_dir, self.deps,
                                     **_supported(self.api.run_scan, progress_info=progress))

        def done(res):
            self.state.scan = res
            self.emit("scan_done", res)
        self._run(T.TASK_SCAN, work, done)

    # ------------------------------------------------------------------ 구 PC 2) 규칙에 없는 프로그램
    def start_discover(self, min_size_kb: int = 0) -> None:
        def work(progress, token):
            return self.api.run_discover(self.state.rules_dir, min_size_kb=min_size_kb, progress=progress,
                                         deps=self.deps, cancel=token,
                                         **_supported(self.api.run_discover, progress_info=progress))

        def done(res):
            self.state.discovery = res
            self.state.selected = set()  # 자동 선택 없음
            self.state.secrets_ack = False
            self.emit("discover_done", res)
        self._run(T.TASK_DISCOVER, work, done, cancellable=True)

    def candidate_rows(self) -> list[CandRow]:
        d = self.state.discovery
        if d is None:
            return []
        rows = []
        for c in d.candidates():
            link = c.link
            eff_size, eff_files, note = self._effective(c)
            rows.append(CandRow(
                key=c.key, programs=", ".join(link.programs) if link else T.NO_PROGRAM, path=c.path,
                kind=T.KIND_FOLDER if c.is_dir else T.KIND_FILE, size=fmt_size(c), grade=link.grade if link else "없음",
                reason=link.reason if link else "", weak=bool(link and link.weak), secret=list(c.secret),
                selectable=((not c.secret) or self.state.secrets_ack) and not c.license_only,
                badge=c.badge, license_only=c.license_only, size_eff=eff_size, files_eff=eff_files, detail_cache=note,
                install=bool(getattr(c, "install_program", "")), self_transfer=getattr(c, "self_transfer", None),
                badge_note=T.DISC_INSTALL_BADGE_FULL if getattr(c, "install_program", "") else ""))
        for r in rows:
            if r.install:  # 설치 폴더 설정 후보: 상세줄에 복원 조건을 더한다(캐시 안내 대신)
                r.detail_cache = T.DISC_DETAIL_INSTALL
        return rows

    def _cache_excluded(self, c) -> bool:
        return not self.state.include_caches and bool(getattr(c, "cache_files", 0) or getattr(c, "cache_size", 0))

    def _effective(self, c) -> tuple[str, str, str]:
        """후보의 (크기 문구, 파일 수 문구, 캐시 안내). 크기를 다 못 쟀으면 '이상', 아예 모르면 '크기 미확인'.
        임시 저장물(캐시)은 내보낼 때 기본으로 빠지므로 그만큼 뺀 값을 보여 준다(include_caches 를 켜면 전체)."""
        if c.size is None:
            return fmt_size(c), (f"{c.files}개 이상" if c.files else "-"), ""
        cut = self._cache_excluded(c)
        size = c.size - (c.cache_size if cut else 0)
        files = (c.files or 0) - (c.cache_files if cut else 0)
        note = ""
        if cut:
            note = T.DISC_DETAIL_CACHE.format(total=fmt_bytes(c.size), files=c.files or 0, cache=fmt_bytes(c.cache_size),
                                              cfiles=c.cache_files)
        return fmt_bytes(size), f"{files}개", note

    def selection_totals(self) -> dict:
        """선택한 후보의 합계: n, bytes, files, cache_bytes/cache_files(빠지는 양), approx(시간 초과로 일부만 센 수), unknown(크기 모름)."""
        d = self.state.discovery
        out = {"n": 0, "bytes": 0, "files": 0, "cache_bytes": 0, "cache_files": 0, "approx": 0, "unknown": 0, "big": 0}
        if d is None:
            return out
        for c in d.candidates():
            if c.key not in self.state.selected:
                continue
            out["n"] += 1
            before = out["bytes"]
            if c.size is None:
                if getattr(c, "partial_size", None):
                    out["bytes"] += c.partial_size
                    out["files"] += c.files or 0
                    out["approx"] += 1
                else:
                    out["unknown"] += 1
                if out["bytes"] - before >= 1024 ** 3:
                    out["big"] += 1
                continue
            if self._cache_excluded(c):
                out["bytes"] += c.size - c.cache_size
                out["files"] += (c.files or 0) - c.cache_files
                out["cache_bytes"] += c.cache_size
                out["cache_files"] += c.cache_files
            else:
                out["bytes"] += c.size
                out["files"] += c.files or 0
            if out["bytes"] - before >= 1024 ** 3:
                out["big"] += 1
        return out

    def selection_total_text(self) -> str:
        """'선택한 N개: 약 X MB, 파일 Y개 (캐시 제외 후 …)' 한 줄(선택이 없으면 안내)."""
        t = self.selection_totals()
        if not t["n"]:
            return T.DISC_TOTAL_NONE
        if t["cache_bytes"] or t["cache_files"]:
            note = T.DISC_TOTAL_CACHE.format(size=fmt_bytes(t["cache_bytes"]), files=t["cache_files"])
        elif self.state.include_caches:
            note = T.DISC_TOTAL_CACHE_IN
        else:
            note = ""
        if t["approx"]:
            note += T.DISC_TOTAL_APPROX.format(k=t["approx"])
        if t["unknown"]:
            note += T.DISC_TOTAL_UNKNOWN.format(k=t["unknown"])
        return T.DISC_TOTAL.format(n=t["n"], size=fmt_bytes(t["bytes"]), files=t["files"], note=note)

    def set_include_caches(self, on: bool) -> None:
        self.state.include_caches = bool(on)
        self.emit("selection_changed")

    # ---- 이 프로그램 설정 찾기(규칙 없는 프로그램 하나)
    def ruleless_program_names(self) -> list[str]:
        d = self.state.discovery
        return [e.program.name for e in d.programs] if d is not None else []

    def start_find_program(self, name: str) -> None:
        d = self.state.discovery
        if d is None:
            raise GuardError(T.G_FIND_NEED_DISCOVERY)
        name = (name or "").strip()
        if name not in self.ruleless_program_names():
            raise GuardError(T.G_FIND_NEED_PROGRAM)

        def work(progress, token):
            return self.api.run_find_program(self.state.rules_dir, name, deps=self.deps, cancel=token,
                                             **_supported(self.api.run_find_program, progress_info=progress))

        def done(res):
            new, dup = self._merge_find(name, res)
            self.state.find_results[name] = res
            self.emit("find_done", {"name": name, "result": res, "new": new, "dup": dup})
        self._run(T.TASK_FIND_PROGRAM, work, done, cancellable=True)

    def _merge_find(self, name: str, res) -> tuple[int, int]:
        """찾은 후보를 해당 프로그램 항목에 더한다(이미 목록에 있는 폴더는 건너뜀). 자동 선택은 하지 않는다."""
        d = self.state.discovery
        entry = next((e for e in d.programs if e.program.name == name), None)
        have = {c.key for c in d.candidates()}
        new = dup = 0
        for c in [*getattr(res, "candidates", []), *getattr(res, "install_candidates", [])]:  # 설치 폴더 설정 후보도 체크 가능한 후보로(선택은 안 함)
            if c.key in have:
                dup += 1
                continue
            if entry is not None:
                entry.candidates.append(c)
                have.add(c.key)
                new += 1
        return new, dup

    def find_summary(self, info: dict) -> str:
        res = info["result"]
        name = info["name"]
        if getattr(res, "has_rule", False):
            return T.FIND_RESULT_RULE.format(name=name)
        if not res.candidates and not res.install_files:
            return T.FIND_RESULT_NONE.format(name=name)
        n_inst = len(getattr(res, "install_candidates", []) or [])
        toks = ", ".join(t for t, _s in res.tokens[:6]) or "-"
        text = T.FIND_RESULT.format(name=name, new=info["new"], dup=info["dup"], tokens=toks)
        text += "\n" + T.FIND_SCOPE_NOTE
        st = getattr(res, "self_transfer", None)
        if st:
            text += "\n" + T.DISC_SELF_DETAIL.format(reason=st["reason"], todo=st["todo"])
        if getattr(res, "no_config_found", False):
            text += "\n" + T.FIND_NO_CONFIG.format(size=fmt_bytes(res.folder_bytes))
            if getattr(res, "install_dir_found", False):
                text += T.FIND_NO_CONFIG_INSTALL
        if n_inst:
            names = ", ".join(os.path.basename(x["path"]) for x in res.install_files[:6])
            text += "\n" + T.FIND_INSTALL_CANDIDATES.format(n=n_inst, names=names)
            if res.install_more:
                text += T.FIND_INSTALL_MORE.format(k=res.install_more)
            if getattr(res, "install_excluded", None):
                text += T.FIND_INSTALL_EXCLUDED.format(k=len(res.install_excluded))
        elif res.install_files:
            names = ", ".join(os.path.basename(x["path"]) for x in res.install_files[:6])
            text += "\n" + T.FIND_INSTALL_FILES.format(n=len(res.install_files) + res.install_more, names=names)
        if res.budget_hit:
            text += T.FIND_BUDGET
        return text

    # ---- 설정 확인표
    def coverage_table(self, res=None) -> list[dict]:
        """내보내기 결과(manifest) 또는 복원 미리보기/결과(plan.manifest)에서 설정 확인표의 행. 없으면 빈 목록."""
        res = res if res is not None else self.state.export_result
        m = getattr(res, "manifest", None)
        if not isinstance(m, dict):
            m = getattr(getattr(res, "plan", None), "manifest", None)
        if not isinstance(m, dict):
            return []
        try:
            return coverage.rows(m)
        except (TypeError, KeyError, AttributeError):
            return []

    def coverage_headline(self, table: list[dict]) -> str:
        """설정 확인표 머리글: '담김 5 · 직접 해야 함 3 · 해당 없음 12'."""
        counts = coverage.summary_counts(table)
        parts = [f"{st} {counts[st]}" for st in coverage.STATUS_ORDER if counts.get(st) and st != coverage.ST_NONE]
        if counts.get(coverage.ST_NONE):
            parts.append(f"해당 없음 {counts[coverage.ST_NONE]}")
        return " · ".join(parts)

    def cache_skipped_text(self, res=None) -> str:
        res = res if res is not None else self.state.export_result
        m = getattr(res, "manifest", None)
        if not isinstance(m, dict):
            return ""
        n, f, b = coverage.cache_totals(m)
        return T.EXP_CACHE_SKIPPED.format(n=n, files=f, size=fmt_bytes(b)) if n else ""

    def _row(self, key: tuple) -> CandRow:
        for r in self.candidate_rows():
            if r.key == key:
                return r
        raise GuardError(T.G_NOT_IN_LIST)

    def set_secrets_ack(self, on: bool) -> None:
        self.state.secrets_ack = bool(on)
        if not on:  # 동의를 거두면 민감한 정보 가능성 항목은 모두 선택 해제
            for r in self.candidate_rows():
                if r.secret:
                    self.state.selected.discard(r.key)
        self.emit("selection_changed")

    def set_selected(self, key: tuple, on: bool) -> None:
        row = self._row(key)
        if on and row.license_only:
            raise GuardError(T.G_LICENSE_SELECT)
        if on and row.secret and not self.state.secrets_ack:
            raise GuardError(T.G_SECRET_SELECT)
        (self.state.selected.add if on else self.state.selected.discard)(key)
        self.emit("selection_changed")

    def selected_rows(self) -> list[CandRow]:
        return [r for r in self.candidate_rows() if r.key in self.state.selected]

    def _target_rows(self, keys) -> list[CandRow]:
        """일괄 선택/해제 대상: 화면에 보이는 키(keys). None 이면 후보 전체. 목록에 없는 키는 무시한다."""
        rows = self.candidate_rows()
        if keys is None:
            return rows
        wanted = set(keys)
        return [r for r in rows if r.key in wanted]

    def select_all_candidates(self, keys=None) -> tuple[int, int]:
        """보이는 후보를 모두 선택한다. 민감한 정보 가능성 항목은 동의 여부와 상관없이 건너뛴다(직접 골라야 함).
        반환: (새로 선택된 개수, 건너뛴 민감 항목 개수)."""
        added = skipped = 0
        for r in self._target_rows(keys):
            if r.license_only:
                continue  # 라이선스는 옮기지 않는다
            if r.secret:
                skipped += 1
                continue
            if r.key not in self.state.selected:
                self.state.selected.add(r.key)
                added += 1
        self.emit("selection_changed")
        return added, skipped

    def clear_all_candidates(self, keys=None) -> int:
        """보이는 후보의 선택을 모두 해제한다(민감 항목 포함). 반환: 해제된 개수."""
        n = 0
        for r in self._target_rows(keys):
            if r.key in self.state.selected:
                self.state.selected.discard(r.key)
                n += 1
        self.emit("selection_changed")
        return n
