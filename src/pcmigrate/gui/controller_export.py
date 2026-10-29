"""컨트롤러의 '체크리스트와 내보내기(설정 묶음 만들기)' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

import os
from pathlib import Path

from .. import api as real_api
from ..checklist import write_output
from ..package import inspect_savepoint
from ..progress import fmt_bytes
from . import texts as T
from .controller_common import GuardError, _accepts, _supported


class ExportMixin:
    # ------------------------------------------------------------------ 구 PC 3) 해야 할 일 목록
    def start_checklist(self) -> None:
        """항상 '모두 보기' 로 만들고, 화면에서 관련 없는 경고를 숨기거나 보인다(조사를 다시 하지 않는다)."""
        def work(progress, token):
            return self.api.run_checklist(self.state.rules_dir, show_all=True, fmt="md", deps=self.deps,
                                          **_supported(self.api.run_checklist, progress_info=progress))

        def done(res):
            self.state.checklist = res
            self.checklist_marks = set()
            self.emit("checklist_done", res)
        self._run(T.TASK_CHECKLIST, work, done)

    def checklist_hidden_count(self) -> int:
        r = self.state.checklist
        if r is None:
            return 0
        cl = r.checklist
        return sum(1 for line in [*cl.login, *cl.data] if line.endswith(T.CL_HIDDEN_MARK))

    def checklist_tabs(self, show_all: bool = False, query: str = "") -> dict[str, list[str]]:
        r = self.state.checklist
        if r is None:
            return {}
        cl = r.checklist

        def visible(lines: list[str]) -> list[str]:
            out = [l for l in lines if show_all or not l.endswith(T.CL_HIDDEN_MARK)]
            q = query.strip().lower()
            return [l for l in out if q in l.lower()] if q else out

        hidden = 0 if show_all else self.checklist_hidden_count()
        summary = [
            T.CL_SUMMARY_INSTALL.format(a=len(cl.install), b=len(cl.winget_cmd), c=len(cl.winget_auto)),
            T.CL_SUMMARY_LOGIN.format(n=len(visible(cl.login))), T.CL_SUMMARY_DATA.format(n=len(visible(cl.data))),
            T.CL_SUMMARY_REF.format(n=len(cl.reference))]
        if hidden:
            summary.append(T.CL_SUMMARY_OMITTED.format(n=hidden))
        if cl.discovery is not None:
            n, m = cl.discovery
            summary.append(T.CL_SUMMARY_DISC.format(n=n, m=T.CL_SUMMARY_DISC_M.format(m=m) if m is not None else ""))
        summary.append(T.CL_SUMMARY_LICENSE)
        tabs = {T.CL_TABS[0]: summary,
                T.CL_TABS[1]: [*T.CL_GUIDE, *visible(cl.winget_cmd), *visible(cl.install)],
                T.CL_TABS[2]: visible(cl.login), T.CL_TABS[3]: visible(cl.data), T.CL_TABS[4]: visible(cl.reference)}
        return tabs

    CHECKLIST_PLAIN = {T.CL_TABS[0]: "all", T.CL_TABS[4]: "all", T.CL_TABS[1]: len(T.CL_GUIDE)}  # 체크박스 없는 줄 수

    def checklist_items(self, tab: str, show_all: bool = False, query: str = "") -> list[tuple[str, bool, bool]]:
        """(문구, 체크박스가 있는 항목인지, 체크됨) 목록."""
        lines = self.checklist_tabs(show_all, query).get(tab, [])
        plain = self.CHECKLIST_PLAIN.get(tab, 0)
        out = []
        for i, line in enumerate(lines):
            has_box = not (plain == "all" or (isinstance(plain, int) and i < plain))
            out.append((line, has_box, has_box and (tab, line) in self.checklist_marks))
        return out

    def toggle_check(self, tab: str, text: str) -> bool:
        key = (tab, text)
        if key in self.checklist_marks:
            self.checklist_marks.discard(key)
        else:
            self.checklist_marks.add(key)
        self.emit("checklist_marks")
        return key in self.checklist_marks

    def checklist_progress(self, show_all: bool = False) -> tuple[int, int]:
        total = done = 0
        for tab in T.CL_TABS:
            for _text, has_box, checked in self.checklist_items(tab, show_all):
                total += has_box
                done += checked
        return done, total

    def checklist_text(self, fmt: str = "md", show_all: bool = False) -> str:
        """저장용 텍스트. 체크한 항목은 [x] 로 남는다."""
        if self.state.checklist is None:
            raise GuardError(T.G_NEED_CHECKLIST_FIRST)
        md = fmt == "md"
        lines = ["# PC 이전 체크리스트" if md else "PC 이전 체크리스트", ""]
        for tab in T.CL_TABS:
            lines.append(f"## {tab}" if md else f"=== {tab} ===")
            items = self.checklist_items(tab, show_all)
            for text, has_box, checked in items or [(T.CL_EMPTY, False, False)]:
                lines.append(f"- [{'x' if checked else ' '}] {text}" if has_box else text)
            lines.append("")
        lines.append(T.CL_SUMMARY_LICENSE)
        return "\n".join(lines) + "\n"

    def save_text(self, path: str, text: str) -> str:
        """텍스트 저장(기존 파일은 .bak 보존). 호출자는 저장 위치 확인 대화상자를 먼저 보여 줘야 한다."""
        if not path:
            raise GuardError(T.G_NEED_SAVE_PATH)
        try:
            write_output(Path(path), text)
        except OSError as e:
            raise real_api.ApiError("\n".join(real_api.format_write_error(path, e))) from e
        self.log.info("saved text file")
        return path

    # ------------------------------------------------------------------ 구 PC 4) 설정 패키지 만들기
    def export_reason(self, output: str) -> str:
        """패키지 만들기 버튼이 막혀 있는 이유(없으면 빈 문자열)."""
        if self.state.busy:
            return T.WHY_BUSY
        return "" if output.strip() else T.WHY_NEED_OUTPUT

    def export_resume_hint(self, output: str) -> str:
        """같은 저장 위치로 이어받을 수 있는 임시 파일이 있으면 안내 한 줄(없으면 빈 문자열). 읽기 전용."""
        if not output.strip():
            return ""
        try:
            info = inspect_savepoint(output.strip())
        except (OSError, ValueError):
            return ""
        if info is None:
            return ""
        if not info["usable"]:
            return T.EXP_RESUME_BROKEN.format(why=info["reason"])
        if not info["files"]:
            return ""
        return T.EXP_RESUME_HINT.format(n=info["files"], size=fmt_bytes(info["bytes"]))

    def start_export(self, output: str, write_confirmed: bool = False, resume: bool = True, stage_dir: str = "",
                     include_caches: bool | None = None) -> None:
        if not write_confirmed:
            raise GuardError(T.G_NEED_EXPORT_CONFIRM)
        stage_dir = (stage_dir or "").strip()
        if stage_dir:
            if not os.path.isdir(stage_dir):
                raise GuardError(T.G_STAGE_MISSING.format(path=stage_dir))
            if not _accepts(self.api.run_export, "stage_dir"):  # 지원하지 않는 버전에서 조용히 무시하면 사용자가 모르게 다른 위치를 쓰게 된다
                raise GuardError(T.G_STAGE_UNSUPPORTED)
        if not output:
            raise GuardError(T.G_NEED_SAVE_PATH)
        if real_api.check_unexpanded(output=output):
            raise GuardError(T.G_OUTPUT_UNEXPANDED)
        if os.path.isdir(output):
            raise GuardError(T.G_OUTPUT_IS_DIR)
        sel = [r for r in self.selected_rows() if not r.license_only]  # 방어: 라이선스 후보는 어떤 경로로도 담지 않는다
        has_secret = any(r.secret for r in sel)
        if has_secret and not self.state.secrets_ack:
            raise GuardError(T.G_SECRET_NO_ACK)
        all_cands = {c.key: c for c in (self.state.discovery.candidates() if self.state.discovery else [])}
        cache_keys = {k for k, c in all_cands.items() if c.include_caches}
        inst_rows = [r for r in sel if r.install]
        plain_rows = [r for r in sel if not r.install]
        items = self.make_items({"format": 1, "candidates": [
            {"include": True, "root": k[0], "rel": k[1], **({"include_caches": True} if k in cache_keys else {})}
            for k in sorted(r.key for r in plain_rows)]}) if plain_rows else []
        install_items = []
        if inst_rows:  # 설치 폴더 설정: 별도 영역에 담는다(복원은 새 PC 에서 별도 동의가 있을 때만)
            if not _accepts(self.api.run_export, "install_items"):
                raise GuardError(T.G_INSTALL_UNSUPPORTED)
            install_items = self.make_install_items({"format": 1, "candidates": [], "install_dir": [
                {"include": True, "program": all_cands[r.key].install_program, "root": r.key[0],
                 "folder": all_cands[r.key].install_folder, "rel": r.key[1], "path": r.path}
                for r in sorted(inst_rows, key=lambda x: x.key) if r.key in all_cands]})

        # 민감 항목을 하나도 고르지 않았다면 동의 표시와 상관없이 에러 없이 진행한다(검사를 끝내지 못했다는 이유만으로는 막지 않는다).
        # 동의 표시가 켜져 있으면 api 의 다시 검사에서 새로 발견된 비밀 이름도 사용자가 이해한 것으로 본다.
        allow_secrets = has_secret or self.state.secrets_ack
        stage_kw = {"stage_dir": stage_dir} if stage_dir else {}
        if include_caches is None:
            include_caches = self.state.include_caches
        if include_caches:  # 기본(끔)은 넘기지 않는다: 이 옵션을 모르는 api 에도 안전
            if not _accepts(self.api.run_export, "include_caches"):
                raise GuardError(T.G_INCLUDE_CACHES_UNSUPPORTED)
            stage_kw["include_caches"] = True
        if self.state.extra_mode != "config":  # stage30b: 폴더 전체 담기(기본 config 는 넘기지 않는다: 이 옵션을 모르는 api 에도 안전)
            if not _accepts(self.api.run_export, "extra_mode"):
                raise GuardError(T.G_EXTRA_MODE_UNSUPPORTED)
            stage_kw["extra_mode"] = self.state.extra_mode

        def work(progress, token):
            return self.api.run_export(self.state.rules_dir, output, extra_items=items or None,
                                       allow_secrets=allow_secrets, deps=self.deps,
                                       **_supported(self.api.run_export, progress_info=progress, resume=resume,
                                                    allow_unverified=True), **stage_kw,
                                       **({"install_items": install_items} if install_items else {}))

        def done(res):
            self.state.export_result, self.state.export_output = res, output
            self.emit("export_done", res)
        self._run(T.TASK_EXPORT, work, done)

    # ------------------------------------------------------------------ 구 PC 4) 읽지 못해 제외된 파일만 다시 담기
    def export_unreadable(self) -> list[dict]:
        """마지막 내보내기(또는 다시 담기) 결과에서 읽지 못해 제외된 파일(사유 코드가 있는 failed 항목)."""
        m = getattr(self.state.export_result, "manifest", None)
        if not isinstance(m, dict):
            return []
        return [f for f in m.get("failed", []) if f.get("reason")]

    def retry_export_reason(self) -> str:
        """'이 파일들만 다시 담기' 가 막혀 있는 이유(없으면 빈 문자열)."""
        if self.state.busy:
            return T.WHY_BUSY
        if not self.state.export_output:
            return T.G_RETRY_NO_PACKAGE
        return "" if self.export_unreadable() else T.G_RETRY_NOTHING

    def retry_export_failed(self, write_confirmed: bool = False) -> None:
        """이미 만든 설정 묶음에서 읽지 못해 제외된 파일만 다시 읽어 추가한다(새로 만들지 않는다). 취소할 수 있다."""
        if not write_confirmed:
            raise GuardError(T.G_NEED_RETRY_CONFIRM)
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        why = self.retry_export_reason()
        if why:
            raise GuardError(why)
        output = self.state.export_output
        if not os.path.isfile(output):
            raise GuardError(T.G_PACKAGE_MISSING.format(path=output))

        def work(progress, token):
            def info(i):
                token.raise_if_set()  # 진행 보고 때마다 취소 확인(기존 취소 흐름)
                progress(i)
            info(T.PROGRESS[T.TASK_EXPORT_RETRY])
            return self.api.run_export_retry(self.state.rules_dir, output, deps=self.deps,
                                             **_supported(self.api.run_export_retry, progress_info=info))

        def done(res):
            self.state.export_result = res  # manifest/resume/denied 를 가진 결과(화면의 개수와 요약이 새 상태가 된다)
            self.emit("export_retry_done", res)
        self._run(T.TASK_EXPORT_RETRY, work, done, cancellable=True)
