"""컨트롤러의 '내 파일 직접 전송·검증(모드 B)' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

import os
from pathlib import Path

from .. import api as real_api
from ..manifest import DEFAULT_NAME as MANIFEST_NAME
from ..progress import fmt_bytes
from ..scan import skipped_links_view
from . import state as S, texts as T
from .controller_common import GuardError, PathCheck, _accepts, _count_of


class TransferMixin:
    # ------------------------------------------------------------------ 대용량 복사: 방식/단계
    def set_transfer_flow(self, mode: str, stage: int = 1) -> None:
        """'어떻게 옮기시나요?' 선택(외장 / 랜선 / 같은 PC)과 단계. 바뀌면 이전 미리보기와 결과는 무효가 된다."""
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        if mode not in (T.MODE_EXTERNAL, T.MODE_NETWORK, T.MODE_LOCAL):
            raise GuardError(T.G_NOT_IN_LIST)
        stage = 1 if mode == T.MODE_LOCAL or stage not in (1, 2) else stage
        s = self.state
        if (s.transfer_mode, s.transfer_stage) != (mode, stage):
            s.transfer_mode, s.transfer_stage = mode, stage
            s.transfer_report, s.transfer_preview_key, s.transfer_blocked = None, None, ""
            s.transfer_preview_opts = s.transfer_preview_split = None
            s.transfer_was_dry_run = True
        self.emit("transfer_flow", (mode, stage))

    def set_transfer_options(self, include_cloud: bool, strict: bool, split_large: bool = False) -> None:
        """옵션 체크 상태를 알린다. 미리보기 때와 달라지면 미리보기를 무효로 돌려, 보지 않은 내용으로 복사하지 못하게 한다."""
        s = self.state
        s.transfer_options = (bool(include_cloud), bool(strict))
        s.transfer_split = bool(split_large)
        changed = s.transfer_preview_opts is not None and s.transfer_preview_opts != s.transfer_options
        changed = changed or (s.transfer_preview_split is not None and s.transfer_preview_split != s.transfer_split)
        if changed:
            s.transfer_preview_key, s.transfer_preview_opts, s.transfer_blocked = None, None, ""
            s.transfer_preview_split = None
            self.emit("selection_changed")  # 버튼 상태 다시 계산

    def flow(self) -> tuple[str, int]:
        return self.state.transfer_mode, self.state.transfer_stage

    def is_verify_flow(self) -> bool:
        """랜선 2단계: 복사 없이 새 PC의 복사본을 목록 파일과 비교만 한다."""
        return self.flow() == (T.MODE_NETWORK, 2)

    def flow_labels(self) -> tuple[str, str]:
        return T.TR_FLOW_LABELS.get(self.flow(), T.TR_FLOW_LABELS[(T.MODE_LOCAL, 1)])

    def flow_dst_hint(self) -> str:
        return T.TR_FLOW_DST_HINT.get(self.state.transfer_mode, "")

    def flow_method(self) -> str:
        return T.TR_VERIFY_METHOD.get(self.flow(), "")

    def flow_steps(self) -> tuple[str, ...]:
        return T.TR_FLOW_STEPS.get(self.flow(), ())

    def transfer_step(self) -> tuple[tuple[str, ...], int]:
        """단계 표시줄용 (단계 이름들, 지금 단계 1부터). 방식 → 폴더 → 미리보기 → 복사 → 결과(랜선 2단계는 방식 → 목록·폴더 → 검사 → 결과)."""
        s, rep = self.state, self.state.transfer_report
        done = rep is not None and not s.transfer_was_dry_run
        if self.is_verify_flow():
            if done:
                return T.TR_VERIFY_STEP_NAMES, 4
            ok = self.check_verify_inputs(s.verify_manifest, s.verify_target).ok
            return T.TR_VERIFY_STEP_NAMES, 3 if (s.busy or ok) else 2
        if done:
            return T.TR_STEP_NAMES, 5
        if s.busy:
            return T.TR_STEP_NAMES, 3 if s.busy_label == T.TASK_TRANSFER_PREVIEW else 4
        if s.transfer_preview_key == (s.transfer_src, s.transfer_dst) and not s.transfer_blocked:
            return T.TR_STEP_NAMES, 4
        return T.TR_STEP_NAMES, 3 if self.check_transfer_paths(s.transfer_src, s.transfer_dst).ok else 2

    def flow_title(self) -> str:
        return T.TR_FLOW_TITLES.get(self.flow(), T.TR_HEADING)

    def manifest_out_path(self) -> str:
        """1단계(외장/랜선)에서 확인용 목록 파일을 저장할 위치: 대상(외장 드라이브 또는 새 PC의 공유 폴더) 안."""
        s = self.state
        if s.transfer_mode in (T.MODE_EXTERNAL, T.MODE_NETWORK) and s.transfer_stage == 1 and s.transfer_dst:
            return os.path.join(s.transfer_dst, MANIFEST_NAME)
        return ""

    def stage2_manifest(self) -> str:
        """외장 2단계: 외장 드라이브 폴더 안의 목록 파일. 없으면 빈 문자열(원본을 다시 읽어 검사)."""
        s = self.state
        if (s.transfer_mode, s.transfer_stage) == (T.MODE_EXTERNAL, 2) and s.transfer_src:
            cand = os.path.join(s.transfer_src, MANIFEST_NAME)
            return cand if os.path.isfile(cand) else ""
        return ""

    # ------------------------------------------------------------------ 대용량 복사: 점검
    def describe_dest(self, dst: str):
        """대상 드라이브 종류/파일 시스템/여유 공간(읽기 전용 조회, 같은 경로는 캐시). 네트워크 경로는 느릴 수 있어 조회하지 않는다."""
        fn = getattr(self.api, "destination_info", None)
        if not dst or fn is None:
            return None
        if dst.startswith("\\\\"):
            from ..fs import DestInfo
            return DestInfo(dst, "network")
        if dst not in self._dest_cache:
            try:
                self._dest_cache[dst] = fn(dst)
            except Exception:  # noqa: BLE001 - 조회 실패는 안내만 생략
                self._dest_cache[dst] = None
        return self._dest_cache[dst]

    def forget_dest_cache(self) -> None:
        self._dest_cache.clear()

    def check_transfer_paths(self, src: str, dst: str) -> PathCheck:
        if self.has_selection():  # stage30a: 선택 목록으로 복사
            return self._check_selection_paths(dst)
        lines: list[tuple[bool, str]] = []
        if not src:
            lines.append((False, T.G_NEED_SOURCE))
        elif not os.path.isdir(src):
            lines.append((False, T.G_SOURCE_MISSING.format(path=src)))
        else:
            lines.append((True, T.G_SOURCE_OK))
        if not dst:
            lines.append((False, T.G_NEED_DEST))
        elif src and os.path.isdir(src):
            rs, rd = os.path.realpath(src), os.path.realpath(dst)
            try:
                inside = os.path.commonpath([rs, rd]) == rs
            except ValueError:  # 다른 드라이브
                inside = False
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
        return PathCheck(all(ok for ok, _ in lines) and bool(src) and bool(dst), lines, info, warnings)

    def check_verify_inputs(self, manifest: str, target: str) -> PathCheck:
        lines: list[tuple[bool, str]] = []
        if not manifest:
            lines.append((False, T.G_NEED_MANIFEST))
        elif not os.path.isfile(manifest):
            lines.append((False, T.G_MANIFEST_MISSING.format(path=manifest)))
        else:
            lines.append((True, T.G_VERIFY_OK))
        if not target:
            lines.append((False, T.G_NEED_TARGET))
        elif not os.path.isdir(target):
            lines.append((False, T.G_TARGET_MISSING.format(path=target)))
        return PathCheck(all(ok for ok, _ in lines) and bool(manifest) and bool(target), lines)

    def transfer_reasons(self) -> tuple[str, str]:
        """(미리보기 버튼이 막힌 이유, 복사 시작 버튼이 막힌 이유). 빈 문자열이면 눌러도 됨."""
        s = self.state
        if s.busy:
            return T.WHY_BUSY, T.WHY_BUSY
        if self.is_verify_flow():
            ok = self.check_verify_inputs(s.verify_manifest, s.verify_target).ok
            return ("", "") if ok else (T.WHY_NEED_MANIFEST, T.WHY_NEED_MANIFEST)
        check = self.check_transfer_paths(s.transfer_src, s.transfer_dst)
        if not check.ok:
            return T.WHY_PATH_PROBLEM, T.WHY_PATH_PROBLEM
        if s.transfer_preview_key != (s.transfer_src, s.transfer_dst):
            return "", T.WHY_PREVIEW_FIRST
        if s.transfer_blocked:
            return "", s.transfer_blocked
        return "", ""

    def can_verify_again(self) -> tuple[bool, str]:
        """'검사만 다시': 복사를 이미 한 번 한 폴더 쌍에서만. 복사는 하지 않고 읽기만 한다."""
        s = self.state
        if s.busy:
            return False, T.WHY_BUSY
        if self.is_verify_flow():
            return False, ""
        if not self.check_transfer_paths(s.transfer_src, s.transfer_dst).ok:
            return False, T.WHY_PATH_PROBLEM
        if not os.path.isdir(s.transfer_dst):
            return False, T.G_NEED_COPY_FIRST
        return True, ""

    def has_retryable(self) -> bool:
        rep = self.state.transfer_report
        return bool(rep is not None and not self.state.transfer_was_dry_run and rep.failures and not self.is_verify_flow())

    # ------------------------------------------------------------------ 대용량 복사: 실행
    def start_transfer(self, dry_run: bool = True, strict: bool = False, include_cloud: bool = False,
                       report_path: str = "", write_confirmed: bool = False, verify_only: bool = False,
                       retry_failed: bool = False, workers: int | None = None, split_large: bool = False) -> None:
        s = self.state
        if self.is_verify_flow():
            raise GuardError(T.G_NOT_IN_LIST)
        if self.has_selection():  # stage30a: 선택 목록으로 복사(data_api.run_transfer_selection)
            return self._start_selection_transfer(dry_run, strict, include_cloud, report_path, write_confirmed, verify_only,
                                                  retry_failed, workers, split_large)
        check = self.check_transfer_paths(s.transfer_src, s.transfer_dst)
        if not check.ok:
            raise GuardError(" ".join(t for ok, t in check.lines if not ok))
        if report_path and real_api.report_inside(report_path, s.transfer_src):
            raise GuardError(T.G_REPORT_INSIDE_SOURCE)
        key = (s.transfer_src, s.transfer_dst)
        if verify_only:
            if not os.path.isdir(s.transfer_dst):
                raise GuardError(T.G_NEED_COPY_FIRST)
        elif not dry_run:
            if not write_confirmed:
                raise GuardError(T.G_TRANSFER_CONFIRM)
            if s.transfer_preview_key != key:
                raise GuardError(T.G_TRANSFER_NEED_PREVIEW)
            if s.transfer_preview_opts is not None and s.transfer_preview_opts != (bool(include_cloud), bool(strict)):
                raise GuardError(T.G_TRANSFER_OPTIONS_CHANGED)  # 미리보기에 없던 내용(예: 클라우드 파일 내려받기)이 생길 수 있다
            if s.transfer_preview_split is not None and s.transfer_preview_split != bool(split_large):
                raise GuardError(T.G_TRANSFER_OPTIONS_CHANGED)
            if s.transfer_blocked:
                raise GuardError(s.transfer_blocked)
        extra: dict = {}
        if not dry_run and not verify_only:
            out = self.manifest_out_path()
            if out:
                extra["manifest_out"] = out
            use = self.stage2_manifest()
            if use:
                extra["use_manifest"] = use
        if verify_only:
            extra["verify_only"] = True
        if retry_failed:
            extra["retry_failed"] = True
        if workers:
            extra["workers"] = workers
        if split_large:
            if not _accepts(self.api.run_transfer, "split_large"):  # 지원하지 않는 버전이 옵션을 무시하고 큰 파일을 그대로 복사하면 안 된다
                raise GuardError(T.G_SPLIT_UNSUPPORTED)
            extra["split_large"] = True

        def work(progress, token):
            return self.api.run_transfer(s.transfer_src, s.transfer_dst, dry_run=dry_run, include_cloud=include_cloud,
                                         strict=strict, progress=progress, cancel=token, deps=self.deps,
                                         warn=progress, progress_info=progress, **extra)

        def done(rep):
            s.transfer_report, s.transfer_was_dry_run = rep, dry_run
            if dry_run:
                s.transfer_preview_key = key
                s.transfer_preview_opts = (bool(include_cloud), bool(strict))
                s.transfer_preview_split = bool(split_large)
                meta = getattr(rep, "meta", {}) or {}
                s.transfer_blocked = (T.WHY_SPACE if meta.get("space_blocked") else T.WHY_FAT32 if meta.get("fat32_blocked") else "")
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

    def start_verify(self, report_path: str = "") -> None:
        """랜선 2단계(새 PC): 목록 파일과 로컬 폴더를 비교한다. 폴더는 읽기만 한다(결과 파일도 그 폴더 밖에만 저장)."""
        s = self.state
        check = self.check_verify_inputs(s.verify_manifest, s.verify_target)
        if not check.ok:
            raise GuardError(" ".join(t for ok, t in check.lines if not ok))
        if report_path and self.api.report_inside(report_path, s.verify_target):
            raise GuardError(T.G_REPORT_INSIDE)

        def work(progress, token):
            return self.api.run_verify(s.verify_manifest, s.verify_target, progress=progress, progress_info=progress,
                                       cancel=token, deps=self.deps)

        def done(rep):
            s.transfer_report, s.transfer_was_dry_run = rep, False
            self.emit("transfer_done", rep)
            if report_path:
                try:
                    rep.write(Path(report_path))
                except OSError as e:
                    self.emit("error", self.report_error(e, T.TASK_VERIFY))
        self._run(T.TASK_VERIFY, work, done, cancellable=True)

    # ------------------------------------------------------------------ 대용량 복사: 결과
    def location_warning(self) -> str:
        """복사/검사한 위치가 원래 경로와 다르면 노란 경고 문구(같으면 빈 문자열)."""
        rep, s = self.state.transfer_report, self.state
        if rep is None or s.transfer_was_dry_run:
            return ""
        meta = getattr(rep, "meta", {}) or {}
        if self.is_verify_flow():
            orig, now = meta.get("source_root", ""), meta.get("target", "") or s.verify_target
        elif (s.transfer_mode, s.transfer_stage) == (T.MODE_EXTERNAL, 2):
            orig, now = meta.get("original_root", ""), s.transfer_dst
        else:
            return ""
        if orig and now and os.path.normcase(os.path.normpath(orig)) != os.path.normcase(os.path.normpath(now)):
            return T.LOC_WARNING.format(orig=orig, now=now)
        return ""

    def layout_lines(self) -> list[str]:
        """원본 최상위 구조(절대 경로, 드라이브 포함). 결과 화면에서 xref/데이터 단축 경로 안내와 함께 보여 준다."""
        rep = self.state.transfer_report
        meta = getattr(rep, "meta", {}) or {}
        layout = meta.get("source_layout") or {}
        if not layout:
            return []
        lines = [T.TR_LAYOUT_ROOT.format(root=layout.get("root", ""))]
        for t in layout.get("top_level", [])[:12]:
            name = t["name"] if t["name"] != "(root)" else T.TR_LAYOUT_FILES
            lines.append(T.TR_LAYOUT_LINE.format(name=name, files=t["files"], size=fmt_bytes(t["bytes"])))
        more = layout.get("top_level_count", 0) - min(12, len(layout.get("top_level", [])))
        if more > 0:
            lines.append(T.TR_LAYOUT_MORE.format(n=more))
        return lines

    def preview_link_lines(self) -> list[str]:
        """건너뛴 바로가기/정션 안내(미리보기와 결과 화면 공통). 없으면 빈 목록."""
        rep = self.state.transfer_report
        n, paths, more = skipped_links_view(getattr(rep, "meta", {}) or {})
        if not n:
            return []
        return [T.TR_SKIPPED_LINKS.format(n=n, more=T.TR_SKIPPED_LINKS_MORE.format(shown=len(paths)) if more else "",
                                          paths="\n".join("  " + p for p in paths))]

    def split_count(self) -> int:
        """조각으로 나눈(나눌) 파일 개수. 리포트 meta 의 split_files(개수 또는 목록) 또는 split_count."""
        meta = getattr(self.state.transfer_report, "meta", {}) or {}
        return _count_of(meta.get("split_files")) or _count_of(meta.get("split_count"))

    def verify_notes(self) -> list[str]:
        """검사(verify) 결과 안내: 목록에서 빠진 파일이 있으면 '모두 정확함'이 아님."""
        rep = self.state.transfer_report
        n = int((getattr(rep, "meta", {}) or {}).get("manifest_errors") or 0)
        return [T.TR_MANIFEST_ERRORS.format(n=n)] if n else []

    def flow_notes(self) -> list[str]:
        """결과 화면 아래에 붙는 안내(검사 미룸, 다음 단계, 절전 방지 실패, xref, 원본 보존)."""
        rep, s = self.state.transfer_report, self.state
        if rep is None or s.transfer_was_dry_run:
            return []
        meta = getattr(rep, "meta", {}) or {}
        notes: list[str] = []
        if meta.get("dest_verification") == "deferred":
            notes.append(T.TR_DEFERRED_NOTE)
        if meta.get("keep_awake") == "failed":
            notes.append(T.TR_KEEP_AWAKE_FAILED)
        if meta.get("preserved_existing_count"):
            notes.append(T.TR_PRESERVED_NOTE.format(n=meta["preserved_existing_count"]))
        n_split = self.split_count()
        if n_split:
            notes.append(T.TR_SPLIT_RESULT.format(n=n_split))
        notes.extend(self.preview_link_lines())
        notes.extend(self.verify_notes())
        if s.transfer_mode == T.MODE_EXTERNAL and s.transfer_stage == 1 and not rep.failures:
            notes.append(T.TR_NEXT_STEP_EXT1)
        if s.transfer_mode == T.MODE_NETWORK and s.transfer_stage == 1 and not rep.failures:
            notes.append(T.TR_NEXT_STEP_NET1)
        loc = self.location_warning()
        if loc:
            notes.append(loc)
        notes.append(T.XREF_NOTE)
        notes.append(T.TR_DONE_REMINDER)
        return notes

    def transfer_summary(self) -> dict:
        rep = self.state.transfer_report
        if rep is None:
            return {}
        if self._is_multi(rep):  # stage30a: 선택 목록 복사 결과
            return self._multi_summary(rep)
        counts = rep.to_dict()["summary"]
        return {"dry_run": self.state.transfer_was_dry_run, "counts": counts,
                "verified": counts.get("verified", 0), "mismatch": counts.get("mismatch", 0),
                "missing": counts.get("missing", 0), "cloud_only": counts.get("cloud_only", 0),
                "failed": counts.get("failed", 0),
                "failures": [(f.src, T.TR_STATUS_LABELS.get(f.status, f.status), T.friendly_detail(f.error))
                             for f in rep.failures],
                "cloud_files": [r.src for r in rep.results if r.status == "cloud_only"],
                "repaired": [x["file"] for x in rep.meta.get("repaired", [])],
                "pending": counts.get("pending_verify", 0), "added": counts.get("extra", 0),
                "mode": rep.meta.get("mode", ""), "io": rep.meta.get("io") or {},
                "split": self.split_count(),
                "manifest_complete": rep.meta.get("manifest_complete", True),
                "manifest_errors": int(rep.meta.get("manifest_errors") or 0),
                "verification_complete": rep.meta.get("verification_complete"),
                "success": rep.to_dict().get("success")}

    def transfer_counts(self) -> tuple[int, int, int]:
        """(성공, 건너뜀, 실패). 건너뜀은 클라우드 전용 파일."""
        sm = self.transfer_summary()
        if not sm:
            return 0, 0, 0
        return sm["verified"] + sm["pending"], sm["cloud_only"], sm["mismatch"] + sm["missing"] + sm["failed"]

    def save_transfer_report(self, path: str) -> None:
        rep = self.state.transfer_report
        if rep is None:
            raise GuardError(T.G_NOTHING_TO_SAVE)
        s = self.state
        for folder in self.source_folders():  # stage30a: 선택 목록이면 고른 폴더들
            if folder and real_api.report_inside(path, folder):
                raise GuardError(T.G_REPORT_INSIDE_SOURCE)
        try:
            rep.write(Path(path))
        except OSError as e:
            raise real_api.ApiError("\n".join(real_api.format_write_error(path, e))) from e

    # ------------------------------------------------------------------ 추천대로 한 번에(auto_run)
    # 기존 start_* 와 완료 이벤트(*_done)를 순서대로 이어 붙이기만 한다. 새 권한을 만들지 않는다:
    # 쓰기 전 확인(내보내기/복사/복원)은 반드시 사람의 응답(auto_confirm/auto_apply)을 기다리고,
    # 복원은 기존 can_apply() 가드(체크 2개 + 최종 확인)를 그대로 지난다.
    _AUTO_MANUAL = {"old": S.OLD_SCAN, "new": S.NEW_PACKAGE, "transfer": S.TRANSFER}
