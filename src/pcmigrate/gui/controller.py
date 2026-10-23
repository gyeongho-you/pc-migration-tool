"""GUI 컨트롤러: api 함수 호출, 스레드 관리, 안전 가드. tkinter 에 의존하지 않는다(가짜 api 로 테스트 가능).

안전 원칙(CLI 와 같은 보수적 기본값):
- 원본은 읽기 전용, 쓰기 전에는 반드시 미리보기(dry-run)와 확인.
- 규칙 없는 프로그램은 사용자가 고른 것만 포함(자동 포함 없음). 비밀 정보 가능성 항목은 명시 동의 후에만 선택.
- 가드 위반은 GuardError 로 막는다(뷰가 버튼을 막아 두더라도 컨트롤러가 한 번 더 막는다).
- 로그에는 설치 프로그램 목록/경로를 남기지 않고 이벤트 종류와 개수만 남긴다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import api as real_api
from ..cancel import Cancelled, CancelToken
from ..checklist import write_output
from ..discover import fmt_size, items_from_selection
from ..rules import default_rules_dir
from ..transfer import TransferError
from . import state as S
from .runner import Runner


class GuardError(Exception):
    """안전 가드 위반(예: 미리보기 없이 복원, 동의 없이 비밀 항목 선택). 메시지는 사용자에게 그대로 보여 준다."""


@dataclass
class FriendlyError:
    title: str
    message: str
    detail: str = ""  # traceback(접힌 상세 보기에만)


def friendly_error(exc: BaseException, tb: str = "") -> FriendlyError:
    if isinstance(exc, real_api.ApiError):
        return FriendlyError("작업을 할 수 없습니다", "\n".join([*exc.lines, exc.message]))
    if isinstance(exc, TransferError):
        return FriendlyError("복사를 시작할 수 없습니다", str(exc))
    if isinstance(exc, OSError):
        path = exc.filename if isinstance(getattr(exc, "filename", None), str) else ""
        return FriendlyError("파일을 읽거나 쓸 수 없습니다", "\n".join(real_api.format_write_error(path, exc)), tb)
    if isinstance(exc, GuardError):
        return FriendlyError("안전을 위해 막았습니다", str(exc))
    return FriendlyError("예상하지 못한 오류가 발생했습니다",
                         f"{type(exc).__name__}: {exc}\n자세한 내용은 '상세 보기'를 눌러 확인하고 담당자에게 전달하세요.", tb)


@dataclass
class CandRow:
    key: tuple
    programs: str
    path: str
    kind: str
    size: str
    grade: str
    reason: str
    weak: bool
    secret: list = field(default_factory=list)
    selectable: bool = True


@dataclass
class PathCheck:
    ok: bool
    lines: list  # [(ok, 문장)]


class Controller:
    """화면과 api 사이의 중간층. 이벤트는 listener(kind, payload) 로 전달된다."""

    def __init__(self, api_mod: Any = None, deps: Any = None, runner: Runner | None = None,
                 rules_dir: str | Path | None = None, logger: logging.Logger | None = None,
                 make_items: Callable | None = None) -> None:
        self.api = api_mod or real_api
        self.deps = deps
        self.runner = runner or Runner()
        self.state = S.WizardState(rules_dir=str(rules_dir or default_rules_dir()))
        self.log = logger or logging.getLogger("pcmigrate.gui")
        self.make_items = make_items or items_from_selection
        self.listener: Callable[[str, Any], None] | None = None
        self.cancel_token: CancelToken | None = None
        self.can_cancel = False

    # ------------------------------------------------------------------ 이벤트/실행
    def emit(self, kind: str, payload: Any = None) -> None:
        self.log.info("event %s", kind)  # 내용(목록/경로)은 로그에 남기지 않는다
        if self.listener:
            self.listener(kind, payload)

    def pump(self) -> int:
        return self.runner.pump()

    def go(self, screen: str) -> None:
        self.state.screen = screen
        self.emit("screen", screen)

    def system_info(self):
        return self.api.system_info()

    def _run(self, label: str, fn: Callable[[Callable[[str], None], Any], Any], done: Callable[[Any], None],
             cancellable: bool = False) -> None:
        if self.state.busy:
            raise GuardError("다른 작업이 진행 중입니다. 끝난 뒤에 다시 시도하세요.")
        token = CancelToken() if cancellable else None
        self.cancel_token, self.can_cancel = token, cancellable
        self.state.busy, self.state.busy_label = True, label
        self.log.info("task start: %s", label)
        self.emit("busy", label)

        def finish() -> None:
            self.state.busy, self.state.busy_label = False, ""
            self.cancel_token, self.can_cancel = None, False

        def on_done(res: Any) -> None:
            finish()
            self.emit("busy", None)
            done(res)

        def on_error(exc: BaseException, tb: str) -> None:
            finish()
            self.log.error("task failed: %s (%s)", label, type(exc).__name__)
            self.emit("busy", None)
            self.emit("error", friendly_error(exc, tb))

        def on_cancel() -> None:
            finish()
            self.log.info("task cancelled: %s", label)
            self.emit("busy", None)
            self.emit("cancelled", label)

        self.runner.submit(lambda progress: fn(progress, token), on_done=on_done, on_error=on_error,
                           on_cancel=on_cancel, on_progress=lambda m: self.emit("progress", m))

    def cancel(self) -> bool:
        """취소 가능한 작업(규칙 없는 프로그램 탐지, 대용량 복사)에만 효과가 있다."""
        if self.cancel_token is not None:
            self.cancel_token.cancel()
            self.emit("progress", "취소하는 중입니다...")
            return True
        return False

    # ------------------------------------------------------------------ 구 PC 1) 스캔
    def start_scan(self) -> None:
        def work(progress, token):
            progress("설치된 프로그램과 규칙을 대조하는 중입니다(winget 확인으로 1분 정도 걸릴 수 있습니다)...")
            return self.api.run_scan(self.state.rules_dir, self.deps)

        def done(res):
            self.state.scan = res
            self.emit("scan_done", res)
        self._run("스캔", work, done)

    # ------------------------------------------------------------------ 구 PC 2) 규칙 없는 프로그램
    def start_discover(self, min_size_kb: int = 0) -> None:
        def work(progress, token):
            return self.api.run_discover(self.state.rules_dir, min_size_kb=min_size_kb, progress=progress,
                                         deps=self.deps, cancel=token)

        def done(res):
            self.state.discovery = res
            self.state.selected = set()  # 자동 선택 없음
            self.state.secrets_ack = False
            self.emit("discover_done", res)
        self._run("규칙 없는 프로그램 탐지", work, done, cancellable=True)

    def candidate_rows(self) -> list[CandRow]:
        d = self.state.discovery
        if d is None:
            return []
        rows = []
        for c in d.candidates():
            link = c.link
            rows.append(CandRow(
                key=c.key, programs=", ".join(link.programs) if link else "(연결된 프로그램 없음)", path=c.path,
                kind="폴더" if c.is_dir else "파일", size=fmt_size(c), grade=link.grade if link else "없음",
                reason=link.reason if link else "", weak=bool(link and link.weak), secret=list(c.secret),
                selectable=(not c.secret) or self.state.secrets_ack))
        return rows

    def _row(self, key: tuple) -> CandRow:
        for r in self.candidate_rows():
            if r.key == key:
                return r
        raise GuardError("목록에 없는 항목입니다.")

    def set_secrets_ack(self, on: bool) -> None:
        self.state.secrets_ack = bool(on)
        if not on:  # 동의를 거두면 비밀 정보 가능성 항목은 모두 선택 해제
            for r in self.candidate_rows():
                if r.secret:
                    self.state.selected.discard(r.key)
        self.emit("selection_changed")

    def set_selected(self, key: tuple, on: bool) -> None:
        row = self._row(key)
        if on and row.secret and not self.state.secrets_ack:
            raise GuardError("비밀 정보(키, 비밀번호, 쿠키 등)가 들어 있을 수 있는 항목입니다. "
                             "'비밀 정보가 포함될 수 있음을 이해했습니다'에 먼저 체크해야 선택할 수 있습니다.")
        (self.state.selected.add if on else self.state.selected.discard)(key)
        self.emit("selection_changed")

    def selected_rows(self) -> list[CandRow]:
        return [r for r in self.candidate_rows() if r.key in self.state.selected]

    # ------------------------------------------------------------------ 구 PC 3) 체크리스트
    def start_checklist(self, show_all: bool = False) -> None:
        def work(progress, token):
            progress("체크리스트를 만드는 중입니다(winget 확인으로 1분 정도 걸릴 수 있습니다)...")
            return self.api.run_checklist(self.state.rules_dir, show_all=show_all, fmt="md", deps=self.deps)

        def done(res):
            self.state.checklist = res
            self.emit("checklist_done", res)
        self._run("체크리스트 작성", work, done)

    def checklist_tabs(self) -> dict[str, list[str]]:
        r = self.state.checklist
        if r is None:
            return {}
        cl = r.checklist
        guide = ["신 PC 에서 패키지 안의 programs/winget.json 을 꺼내 다음 명령을 직접 실행하세요(자동 실행하지 않습니다):",
                 "winget import -i winget.json --ignore-unavailable --accept-package-agreements --accept-source-agreements"]
        summary = [
            f"1. 새 PC 에서 먼저 설치: 수동 설치 {len(cl.install)}건, winget 개별 설치 {len(cl.winget_cmd)}건 "
            f"(winget import 로 자동 설치 {len(cl.winget_auto)}건 별도)",
            f"2. 로그인·인증: {len(cl.login)}건", f"3. 데이터 복사/기타: {len(cl.data)}건",
            f"참고(설치 불필요 추정): {len(cl.reference)}건"]
        if cl.omitted:
            summary.append(f"이 PC 에 없는 프로그램 관련 경고 {len(cl.omitted)}건은 생략되었습니다.")
        if cl.discovery is not None:
            n, m = cl.discovery
            summary.append(f"규칙 없는 설치 프로그램 {n}개" + (f", 설정 폴더 후보 {m}개" if m is not None else "")
                           + " — '규칙 없는 프로그램 확인' 화면에서 직접 고른 것만 패키지에 포함됩니다.")
        summary.append("라이선스는 복원되지 않습니다. 모든 항목은 직접 확인하세요.")
        return {"요약": summary, "1. 먼저 설치": guide + cl.winget_cmd + cl.install,
                "2. 로그인·인증": list(cl.login), "3. 데이터·기타": list(cl.data), "참고": list(cl.reference)}

    def checklist_text(self, fmt: str = "md") -> str:
        r = self.state.checklist
        if r is None:
            raise GuardError("먼저 체크리스트를 만드세요.")
        from ..checklist import render
        return render(r.checklist, fmt)

    def save_text(self, path: str, text: str) -> str:
        """텍스트 저장(기존 파일은 .bak 보존). 호출자는 저장 위치 확인 대화상자를 먼저 보여 줘야 한다."""
        if not path:
            raise GuardError("저장할 위치를 선택하세요.")
        try:
            write_output(Path(path), text)
        except OSError as e:
            raise real_api.ApiError("\n".join(real_api.format_write_error(path, e))) from e
        self.log.info("saved text file")
        return path

    # ------------------------------------------------------------------ 구 PC 4) 내보내기
    def start_export(self, output: str, write_confirmed: bool = False) -> None:
        if not write_confirmed:
            raise GuardError("패키지를 저장할 위치를 확인하는 단계가 필요합니다.")
        if not output:
            raise GuardError("패키지를 저장할 위치를 선택하세요.")
        sel = self.selected_rows()
        has_secret = any(r.secret for r in sel)
        if has_secret and not self.state.secrets_ack:
            raise GuardError("비밀 정보 가능성이 있는 항목이 선택되어 있지만 동의가 없습니다.")
        items = self.make_items({"format": 1, "candidates": [
            {"include": True, "root": k[0], "rel": k[1]} for k in sorted(self.state.selected)]}) if sel else []

        def work(progress, token):
            progress("설정 패키지를 만드는 중입니다(원본은 읽기만 합니다)...")
            return self.api.run_export(self.state.rules_dir, output, extra_items=items or None,
                                       allow_secrets=has_secret, deps=self.deps)

        def done(res):
            self.state.export_result, self.state.export_output = res, output
            self.emit("export_done", res)
        self._run("설정 패키지 내보내기", work, done)

    # ------------------------------------------------------------------ 신 PC
    def set_package(self, path: str) -> None:
        self.state.package = path
        self._reset_confirms()
        self.state.preview = None
        self.emit("package_changed", path)

    def _reset_confirms(self) -> None:
        s = self.state
        s.preview_checked = s.final_confirmed = s.extra_consent = False

    def start_preview(self) -> None:
        if not self.state.package:
            raise GuardError("복원할 패키지(.zip)를 먼저 선택하세요.")
        package = self.state.package

        def work(progress, token):
            progress("패키지를 검증하고 복원 미리보기를 만드는 중입니다(아무것도 쓰지 않습니다)...")
            return self.api.run_import(package, self.state.rules_dir, dry_run=True, deps=self.deps)

        def done(res):
            self.state.preview, self.state.preview_package = res, package
            self._reset_confirms()
            self.emit("preview_done", res)
        self._run("검증과 미리보기", work, done)

    def preview_rows(self) -> list[dict]:
        p = self.state.preview
        if p is None:
            return []
        label = {"would_copy": "새로 만듦", "would_overwrite": "덮어씀(.bak 백업)", "skipped_identical": "동일(건너뜀)"}
        return [{"status": label.get(it["status"], it["status"]), "target": it["target"],
                 "backup": it.get("backup", ""), "extra": bool(it.get("extra")), "size": it.get("size", 0)}
                for it in p.plan.items]

    def set_preview_checked(self, on: bool) -> None:
        self.state.preview_checked = bool(on)
        if not on:
            self.state.final_confirmed = False
        self.emit("confirm_changed")

    def set_extra_consent(self, on: bool) -> None:
        self.state.extra_consent = bool(on)
        self.emit("confirm_changed")

    def confirm_final(self, yes: bool) -> None:
        """확인 대화상자에서 '예'를 눌렀을 때만 True 로 호출한다."""
        self.state.final_confirmed = bool(yes) and self.state.preview_checked
        self.emit("confirm_changed")

    def can_apply(self) -> tuple[bool, str]:
        s, p = self.state, self.state.preview
        if p is None or s.preview_package != s.package:
            return False, "먼저 미리보기를 만드세요(패키지를 바꾸면 미리보기를 다시 만들어야 합니다)."
        if p.mode == "blocked" or p.plan.problems:
            return False, "패키지 검증에 실패했습니다. 문제를 해결하기 전에는 복원할 수 없습니다."
        if not s.preview_checked:
            return False, "'미리보기를 확인했습니다'에 체크하세요."
        if p.n_extra and not s.extra_consent:
            return False, "규칙 없이 선택 포함된 항목이 있습니다. 별도 동의가 필요합니다."
        if not s.final_confirmed:
            return False, "확인 대화상자에서 '예'를 눌러야 합니다."
        return True, ""

    def start_apply(self) -> None:
        ok, why = self.can_apply()
        if not ok:
            raise GuardError(why)
        package, allow_extra = self.state.package, self.state.extra_consent

        def work(progress, token):
            progress("복원하는 중입니다(덮어쓸 파일은 .bak 으로 백업됩니다)...")
            return self.api.run_import(package, self.state.rules_dir, yes=True, allow_extra=allow_extra,
                                       deps=self.deps)

        def done(res):
            self.state.import_result = res
            self._reset_confirms()  # 확정은 한 번만 유효
            self.emit("import_done", res)
        self._run("복원", work, done)

    def import_summary(self) -> dict:
        r = self.state.import_result
        if r is None or r.report is None:
            return {}
        rep = r.report
        counts = rep.to_dict()["summary"]
        return {"counts": counts, "failures": [(f.dst, f.error) for f in rep.failures],
                "backups": [x.backup for x in rep.results if x.backup],
                "warnings": [(w.get("name", ""), i) for w in r.plan.warnings for i in w.get("items", [])]}

    # ------------------------------------------------------------------ 대용량 복사
    def check_transfer_paths(self, src: str, dst: str) -> PathCheck:
        lines: list[tuple[bool, str]] = []
        if not src:
            lines.append((False, "원본 폴더를 선택하세요."))
        elif not os.path.isdir(src):
            lines.append((False, f"원본 폴더를 찾을 수 없습니다: {src}"))
        else:
            lines.append((True, "원본 폴더가 있습니다. 원본은 읽기만 하며 수정하거나 삭제하지 않습니다."))
        if not dst:
            lines.append((False, "대상 폴더를 선택하세요."))
        elif src and os.path.isdir(src):
            rs, rd = os.path.realpath(src), os.path.realpath(dst)
            try:
                inside = os.path.commonpath([rs, rd]) == rs
            except ValueError:  # 다른 드라이브
                inside = False
            if inside:
                lines.append((False, "대상 폴더가 원본 폴더와 같거나 원본 폴더 안에 있습니다. 다른 위치를 고르세요."))
            else:
                lines.append((True, "대상 폴더가 원본 폴더 밖에 있습니다."))
        return PathCheck(all(ok for ok, _ in lines) and bool(src) and bool(dst), lines)

    def start_transfer(self, dry_run: bool = True, strict: bool = False, include_cloud: bool = False,
                       report_path: str = "", write_confirmed: bool = False) -> None:
        s = self.state
        check = self.check_transfer_paths(s.transfer_src, s.transfer_dst)
        if not check.ok:
            raise GuardError(" ".join(t for ok, t in check.lines if not ok))
        key = (s.transfer_src, s.transfer_dst)
        if not dry_run:
            if not write_confirmed:
                raise GuardError("대상 폴더에 파일을 쓰기 전에 확인이 필요합니다.")
            if s.transfer_preview_key != key:
                raise GuardError("먼저 '미리보기(쓰기 없음)'를 실행해 무엇이 복사되는지 확인하세요.")

        def work(progress, token):
            return self.api.run_transfer(s.transfer_src, s.transfer_dst, dry_run=dry_run, include_cloud=include_cloud,
                                         strict=strict, progress=progress, cancel=token, deps=self.deps,
                                         warn=progress)

        def done(rep):
            s.transfer_report, s.transfer_was_dry_run = rep, dry_run
            if dry_run:
                s.transfer_preview_key = key
            self.emit("transfer_done", rep)
            if report_path:
                try:
                    rep.write(Path(report_path))
                except OSError as e:
                    self.emit("error", friendly_error(e))
        self._run("복사 미리보기(쓰기 없음)" if dry_run else "대용량 복사", work, done, cancellable=True)

    def transfer_summary(self) -> dict:
        rep = self.state.transfer_report
        if rep is None:
            return {}
        counts = rep.to_dict()["summary"]
        return {"dry_run": self.state.transfer_was_dry_run, "counts": counts,
                "verified": counts.get("verified", 0), "mismatch": counts.get("mismatch", 0),
                "missing": counts.get("missing", 0), "cloud_only": counts.get("cloud_only", 0),
                "failed": counts.get("failed", 0),
                "failures": [(f.src, f.status, f.error) for f in rep.failures],
                "cloud_files": [r.src for r in rep.results if r.status == "cloud_only"],
                "repaired": [x["file"] for x in rep.meta.get("repaired", [])]}

    def save_transfer_report(self, path: str) -> None:
        rep = self.state.transfer_report
        if rep is None:
            raise GuardError("저장할 결과가 없습니다.")
        try:
            rep.write(Path(path))
        except OSError as e:
            raise real_api.ApiError("\n".join(real_api.format_write_error(path, e))) from e
