"""컨트롤러의 '설정 확인표와 복원할 프로그램 선택' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

from ..progress import fmt_bytes
from . import texts as T
from .controller_common import GuardError, _accepts


class ConfigMixin:
    # ---- 화면 4: 설정 파일 열, 선택 합계, 폴더 전체 담기
    def set_extra_mode(self, mode: str) -> None:
        """직접 고른 폴더를 담는 방식. 'config'(기본, 설정 파일만) 또는 'all'(폴더 전체)."""
        if mode not in ("config", "all"):
            raise GuardError(T.G_NOT_IN_LIST)
        self.state.extra_mode = mode
        self.emit("selection_changed")

    def candidate_cfg_cells(self) -> dict:
        """후보 key -> {cfg, size, excl, tip}: 표의 '설정 파일 / 용량 / 제외' 칸(값이 없으면 '계산 중' 또는 '확인 안 됨')."""
        from ..find_program import config_line
        d = self.state.discovery
        out: dict = {}
        if d is None:
            return out
        calc = bool(self.state.busy and self.state.busy_label in (T.TASK_DISCOVER, T.TASK_FIND_PROGRAM))
        all_mode = self.state.extra_mode == "all"
        for c in d.candidates():
            size_eff, files_eff, _note = self._effective(c)
            cut = self._cache_excluded(c)
            cache_txt = fmt_bytes(c.cache_size) if cut and c.cache_size else "-"
            if all_mode:
                out[c.key] = {"cfg": files_eff, "size": size_eff, "excl": cache_txt, "tip": ""}
            elif c.config_files is not None:
                more = "" if c.config_complete else "+"
                excl = (c.config_excluded_bytes or 0) + (c.cache_size if cut else 0)
                out[c.key] = {"cfg": (T.DISC_CFG_COUNT_MORE if more else T.DISC_CFG_COUNT).format(n=c.config_files),
                              "size": fmt_bytes(c.config_bytes or 0), "excl": fmt_bytes(excl) if excl else "-", "tip": config_line(c)}
                if getattr(c, "no_config_found", False):  # 설정 형식 파일 0개이고 계산이 끝남: '없음'(회색) + 상세 줄 문구
                    out[c.key].update({"cfg": T.DISC_CFG_NONE, "none": True,
                                       "tip": T.DISC_NO_CONFIG_DETAIL.format(size=fmt_bytes(c.size if c.size is not None else (c.partial_size or 0)))})
            else:
                size = T.DISC_SIZE_FOLDER.format(size=size_eff) if c.size is not None else "-"
                out[c.key] = {"cfg": T.DISC_CFG_CALC if calc else T.DISC_CFG_UNKNOWN, "size": size, "excl": cache_txt,
                              "tip": T.DISC_CFG_TIP_UNKNOWN}
        return out

    def is_no_config(self, key) -> bool:
        """이 후보가 '설정 파일로 보이는 파일이 없음'(허용 방식 기준)인지. 폴더 전체 담기를 켜면 False(담을 수 있으므로)."""
        d = self.state.discovery
        if d is None or self.state.extra_mode == "all":
            return False
        return any(c.key == key and getattr(c, "no_config_found", False) for c in d.candidates())

    def selection_config_text(self) -> str:
        """선택 합계 한 줄: '선택 N개 · 설정 파일 M개 · 약 X'. 폴더 전체 담기를 켜면 폴더 전체 기준."""
        d = self.state.discovery
        if d is None or not self.state.selected:
            return T.DISC_TOTAL_V2.format(n=0, files=0, size=fmt_bytes(0))
        if self.state.extra_mode == "all":
            t = self.selection_totals()
            return T.DISC_TOTAL_V2_ALL.format(n=t["n"], files=t["files"], size=fmt_bytes(t["bytes"]))
        n = files = size = unknown = 0
        for c in d.candidates():
            if c.key not in self.state.selected:
                continue
            n += 1
            if c.config_files is None:
                unknown += 1
            else:
                files += c.config_files
                size += c.config_bytes or 0
        text = T.DISC_TOTAL_V2.format(n=n, files=files, size=fmt_bytes(size))
        return text + (T.DISC_TOTAL_V2_UNKNOWN.format(k=unknown) if unknown else "")

    # ---- 화면 6: 프로그램별 복원 선택(미리보기/복원에 skip 을 넘긴다)
    def _selection_kwargs(self, skip: tuple) -> dict:
        """체크를 끈 프로그램(skip)을 run_import 에 넘길 인자. 이 선택을 받지 못하는 api 면 조용히 무시하지 않고 GuardError."""
        if not skip:
            return {}
        if not _accepts(self.api.run_import, "skip_programs"):
            raise GuardError(T.G_RESTORE_SELECT_UNSUPPORTED)
        return {"skip_programs": list(skip)}

    def restore_programs(self) -> list[dict]:
        """미리보기의 프로그램별 표(id/name/new/overwrite/same/files + checked)."""
        p = self.state.preview
        rows = getattr(getattr(p, "plan", None), "programs", None) or []
        return [{**r, "checked": r["id"] not in self.state.restore_skip} for r in rows]

    def set_restore_program(self, pid: str, on: bool) -> None:
        """프로그램의 복원 체크를 바꾼다. 미리보기와 달라지므로 확인 표시를 지우고, 다시 계산하기 전에는 복원할 수 없다."""
        if pid not in {r["id"] for r in self.restore_programs()}:
            raise GuardError(T.G_RESTORE_UNKNOWN_PROGRAM)
        if on:
            self.state.restore_skip.discard(pid)
        else:
            self.state.restore_skip.add(pid)
        self._reset_confirms()
        self.emit("restore_selection_changed", pid)

    def restore_selection_reason(self) -> str:
        """프로그램 체크 때문에 복원할 수 없는 이유(없으면 빈 문자열)."""
        if self.state.preview is None:
            return ""
        if tuple(sorted(self.state.restore_skip)) != tuple(self.state.preview_skip):
            return T.G_RESTORE_DIRTY
        rows = self.restore_programs()
        if rows and not any(r["checked"] for r in rows):
            return T.G_RESTORE_NONE
        return ""

    def restore_selected_count(self) -> tuple[int, int]:
        """(체크한 프로그램 수, 전체 프로그램 수)."""
        rows = self.restore_programs()
        return sum(1 for r in rows if r["checked"]), len(rows)

    def restore_item_count(self) -> int:
        """복원 확인창에 보여 줄 파일 수(체크를 끈 프로그램의 파일은 뺀다)."""
        p = self.state.preview
        return sum(1 for it in p.plan.items if it.get("status") != "not_selected") if p else 0

    def restart_preview_for_selection(self) -> None:
        """체크 상태대로 미리보기를 다시 계산한다."""
        self.start_preview(keep_selection=True)
