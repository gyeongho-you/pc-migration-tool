"""컨트롤러의 '가져오기 미리보기와 적용' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

import os

from . import texts as T
from .controller_common import GuardError, _accepts, _meta_value, _supported


class ImportMixin:
    def start_preview(self, keep_selection: bool = False) -> None:
        if not self.state.package:
            raise GuardError(T.G_NEED_PACKAGE)
        package = self.state.package
        if not keep_selection:  # 새로 고른 묶음은 모든 프로그램 체크로 시작한다(stage30b)
            self.state.restore_skip = set()
        skip = tuple(sorted(self.state.restore_skip))
        sel_kw = self._selection_kwargs(skip)

        consent = self.state.install_consent
        if consent and not _accepts(self.api.run_import, "allow_install_dir"):
            raise GuardError(T.G_INSTALL_UNSUPPORTED)
        inst_kw = {"allow_install_dir": True} if consent else {}

        def work(progress, token):
            return self.api.run_import(package, self.state.rules_dir, dry_run=True, deps=self.deps, **sel_kw, **inst_kw)

        def done(res):
            self.state.preview, self.state.preview_package = res, package
            self.state.preview_skip = skip
            self.state.preview_install_consent = consent
            self._reset_confirms()
            self.emit("preview_done", res)
        self._run(T.TASK_PREVIEW, work, done)

    def set_install_consent(self, on: bool) -> None:
        """'설치 폴더 설정도 복원' 체크. 바뀌면 미리보기를 다시 만든다(권한 점검과 덮어쓸 목록이 달라지므로)."""
        on = bool(on)
        if on == self.state.install_consent:
            return
        self.state.install_consent = on
        self.state.final_confirmed = False
        self.emit("confirm_changed")
        if self.state.package:
            self.start_preview()

    def install_view(self) -> list[dict]:
        p = self.state.preview
        return list(getattr(p, "install_view", None) or []) if p is not None else []

    def install_note_lines(self) -> list[str]:
        """미리보기 요약 아래의 설치 폴더 설정 안내(없으면 빈 목록): 개수/복원 조건, 폴더 없음, 버전 경고."""
        view = self.install_view()
        if not view:
            return []
        p = self.state.preview
        allowed = bool(getattr(p, "install_allowed", False))
        n = len(view)
        out = [(T.PREV_INSTALL_ON if allowed else T.PREV_INSTALL_OFF).format(icon=T.ICON_WARN, n=n)]
        miss = [v for v in view if v.get("status") in ("missing_dir", "missing_program")]
        if miss:
            out.append(T.PREV_INSTALL_MISSING.format(icon=T.ICON_WARN, k=len(miss)))
        out.append(T.PREV_INSTALL_VERSION.format(icon=T.ICON_WARN))
        for pr in getattr(p.plan, "install_programs", []) or []:
            old, new = pr.get("old_version") or "", pr.get("new_version") or ""
            if allowed and old and new and old != new:
                out.append(T.PREV_INSTALL_VERSION_DIFF.format(icon=T.ICON_WARN, name=pr.get("program") or "?", old=old, new=new))
        return out

    def install_count(self) -> int:
        """복원 대상(허용했을 때 실제로 쓰는) 설치 폴더 설정 개수."""
        p = self.state.preview
        return sum(1 for it in p.plan.items if it.get("install") and it.get("status") in ("would_copy", "would_overwrite")) if p else 0

    def preview_rows(self) -> list[dict]:
        p = self.state.preview
        if p is None:
            return []
        rows = [{"status": T.PREV_STATUS.get(it["status"], it["status"]), "target": it["target"],
                 "backup": it.get("backup", ""), "extra": bool(it.get("extra")), "size": it.get("size", 0), "install": False}
                for it in p.plan.items if not it.get("install")]
        allowed = bool(getattr(p, "install_allowed", False))
        for v in self.install_view():  # 설치 폴더 설정: 허용 전에는 '복원 안 함'으로만 보여 준다(덮어쓸 목록은 허용하면 그대로 나온다)
            label = T.PREV_STATUS.get(v["status"], v["status"]) if allowed else T.PREV_INSTALL_NOT_RESTORED
            rows.append({"status": label, "target": v["target"], "backup": v.get("backup", "") if allowed else "", "extra": False,
                         "size": v.get("size", 0), "install": True})
        return rows

    def preview_rejected_count(self) -> int:
        p = self.state.preview
        return sum(1 for it in p.plan.items if it.get("status") == "rejected") if p else 0

    def preview_denied_count(self) -> int:
        p = self.state.preview
        return len(p.denied) if p else 0

    def preview_lines(self) -> list[str]:
        """미리보기 화면 위쪽 안내(복원되지 않을 항목)."""
        n = self.preview_rejected_count()
        return [T.PREV_REJECTED_NOTE.format(n=n)] if n else []

    def denied_note(self) -> str:
        """보안상 복원하지 않는 형식이 있으면 한 줄 안내(미리보기 요약/결과 화면 공용). 없으면 빈 문자열."""
        n = self.preview_denied_count() if self.state.preview else self.import_summary().get("denied", 0)
        return T.PREV_DENIED_NOTE.format(n=n) if n else ""

    def git_config_note(self, applied: bool = False) -> str:
        """리포트 meta 의 git_config_warning 이 켜져 있으면 한 줄 안내(미리보기/결과 화면 공용). 없으면 빈 문자열."""
        res = self.state.import_result if applied else self.state.preview
        if res is None or not _meta_value(res, "git_config_warning"):
            return ""
        return T.RES_GIT_NOTE if applied else T.PREV_GIT_NOTE

    def preview_problems(self) -> list[str]:
        p = self.state.preview
        return [T.friendly_detail(x) for x in p.plan.problems] if p else []

    def set_preview_checked(self, on: bool) -> None:
        self.state.preview_checked = bool(on)
        if not on:
            self.state.final_confirmed = False
        self.emit("confirm_changed")

    def set_bak_understood(self, on: bool) -> None:
        """'덮어쓰는 파일은 백업 파일(.bak)로 남는다는 것을 이해했습니다'(미리보기 확인과 따로 받는 두 번째 확인)."""
        self.state.bak_understood = bool(on)
        if not on:
            self.state.final_confirmed = False
        self.emit("confirm_changed")

    def set_extra_consent(self, on: bool) -> None:
        self.state.extra_consent = bool(on)
        self.emit("confirm_changed")

    def confirm_final(self, yes: bool) -> None:
        """확인 대화상자에서 '예'를 눌렀을 때만 True 로 호출한다."""
        self.state.final_confirmed = bool(yes) and self.state.preview_checked and self.state.bak_understood
        self.emit("confirm_changed")

    def can_apply(self) -> tuple[bool, str]:
        s, p = self.state, self.state.preview
        if p is None or s.preview_package != s.package:
            return False, T.G_NEED_PREVIEW
        if p.mode == "blocked" or p.plan.problems:
            return False, T.G_PACKAGE_BLOCKED
        why = self.restore_selection_reason()  # stage30b: 프로그램 체크가 미리보기와 다르면 복원 불가
        if why:
            return False, why
        if s.preview_install_consent != s.install_consent:  # 체크를 바꾼 뒤 아직 새 미리보기가 없다
            return False, T.G_NEED_PREVIEW
        if not s.preview_checked:
            return False, T.G_NEED_CHECK_PREVIEW
        if not s.bak_understood:
            return False, T.G_NEED_BAK_UNDERSTAND
        if p.n_extra and not s.extra_consent:
            return False, T.G_NEED_EXTRA_CONSENT
        if not s.final_confirmed:
            return False, T.G_NEED_FINAL_YES
        return True, ""

    def apply_reason(self) -> str:
        """복원 실행 버튼(확인 창 전)이 막혀 있는 이유. 마지막 '확인 창' 단계는 이유로 치지 않는다."""
        ok, why = self.can_apply()
        return "" if ok or why == T.G_NEED_FINAL_YES else why

    def start_apply(self) -> None:
        ok, why = self.can_apply()
        if not ok:
            raise GuardError(why)
        package, allow_extra = self.state.package, self.state.extra_consent
        sel_kw = self._selection_kwargs(tuple(sorted(self.state.restore_skip)))
        consent = self.state.install_consent
        if consent and not _accepts(self.api.run_import, "allow_install_dir"):
            raise GuardError(T.G_INSTALL_UNSUPPORTED)
        inst_kw = {"allow_install_dir": True} if consent else {}

        def work(progress, token):
            return self.api.run_import(package, self.state.rules_dir, yes=True, allow_extra=allow_extra,
                                       deps=self.deps, **_supported(self.api.run_import, progress_info=progress), **sel_kw, **inst_kw)

        def done(res):
            self.state.import_result = res
            self.state.undo_results, self.state.undo_preview = {}, {}  # stage30b: 새 복원이므로 되돌리기 상태를 비운다
            self.state.install_consent = self.state.preview_install_consent = False  # 다음 복원은 다시 '복원 안 함'으로 시작
            self.state.preview = None  # 확정은 한 번만 유효: 다시 복원하려면 미리보기부터
            self._reset_confirms()
            self.emit("import_done", res)
        self._run(T.TASK_APPLY, work, done, on_fail=self._reset_confirms)  # 실패/취소 뒤에도 확인 표시를 지운다

    def import_summary(self) -> dict:
        r = self.state.import_result
        if r is None or r.report is None:
            return {}
        rep = r.report
        counts = rep.to_dict()["summary"]
        return {"counts": counts, "rejected": counts.get("rejected", 0), "denied": counts.get("denied", 0),
                "failures": [(f.dst, T.friendly_detail(f.error)) for f in rep.failures],
                "backups": [x.backup for x in rep.results if x.backup],
                "warnings": [(w.get("name", ""), i) for w in r.plan.warnings for i in w.get("items", [])]}

    def backup_folder(self) -> str:
        """복원 때 만든 백업 파일(.bak)이 있는 폴더(여러 곳이면 첫 번째). 없으면 빈 문자열."""
        backups = self.import_summary().get("backups", [])
        return os.path.dirname(backups[0]) if backups else ""

    def import_counts(self) -> tuple[int, int, int]:
        """(성공, 건너뜀, 실패)"""
        c = self.import_summary().get("counts", {})
        return (c.get("copied", 0), c.get("skipped_identical", 0) + c.get("denied", 0),
                c.get("failed", 0) + c.get("rejected", 0))
