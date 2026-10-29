"""컨트롤러의 '설정 묶음 열기와 프로그램 설치 확인' 부분(stage43 분리). Controller 가 이 믹스인을 상속한다."""
from __future__ import annotations

import os

from .. import fs
from ..install_check import INSTALLED, WINGET, import_command, newly_installed, safe_winget_ids, selected_commands
from ..detect import valid_winget_id
from . import state as S, texts as T
from .controller_common import GuardError, _supported


class InstallMixin:
    # ------------------------------------------------------------------ 신 PC
    def set_package(self, path: str) -> None:
        self.state.package = path
        self.state.install_consent = self.state.preview_install_consent = False  # 설치 폴더 설정은 묶음을 새로 고를 때마다 '복원 안 함'으로 시작
        self._reset_confirms()
        self.state.preview = None
        self._reset_install()
        self.emit("package_changed", path)

    # ------------------------------------------------------------------ 신 PC: 프로그램 설치 확인(읽기 전용 비교, 설치 명령은 실행하지 않음)
    def _reset_install(self) -> None:
        s = self.state
        s.install_check = s.install_prev = None
        s.install_package, s.install_command = "", ""
        s.install_skipped = False
        s.install_marks, s.install_new = set(), []
        s.install_picks = set()

    def start_install_check(self) -> None:
        """묶음의 구 PC 프로그램과 이 PC의 설치 상태를 비교한다('다시 확인'도 같은 동작: 직전 결과와 비교해 새로 설치된 것을 센다)."""
        s = self.state
        if not s.package:
            raise GuardError(T.G_INSTALL_NEED_PACKAGE)
        package = s.package

        def work(progress, token):
            return self.api.run_install_check(package, deps=self.deps,
                                              **_supported(self.api.run_install_check, progress_info=progress))

        def done(res):
            prev = s.install_check if s.install_package == package else None
            s.install_prev = prev
            s.install_new = [i.name for i in newly_installed(prev, res)]
            s.install_check, s.install_package = res, package
            self.emit("install_done", res)
        self._run(T.TASK_INSTALL_CHECK, work, done)

    def install_needs_check(self) -> bool:
        """화면에 들어왔을 때 자동으로 확인을 시작해야 하는가(이 묶음 파일로 확인한 적이 없다)."""
        s = self.state
        return bool(s.package) and not s.busy and (s.install_check is None or s.install_package != s.package)

    def install_rows(self) -> list[dict]:
        r = self.state.install_check
        if r is None:
            return []
        rows = []
        for it in r.items:
            key = "installed_diff" if it.status == INSTALLED and it.version_diff else it.status
            version = T.INST_VERSION_DIFF.format(old=it.old_version, new=it.new_version) if it.version_diff else (
                (it.new_version if it.status == INSTALLED else "") or it.old_version)  # 애매함/설치 안 됨은 구 PC 버전
            rows.append({"name": it.name, "status": it.status, "label": T.INST_LABELS[key], "version": version,
                         "reason": it.reason, "hint": it.hint, "markable": it.needs_action, "diff": it.version_diff,
                         "mark": it.name in self.state.install_marks,
                         "pickable": self._pickable(it), "pick": self._pickable(it) and it.name in self.state.install_picks})
        return rows

    # ------------------------------------------------------------------ 설치할 프로그램 고르기(선택한 것만 winget.json 으로)
    @staticmethod
    def _pickable(it) -> bool:
        """고를 수 있는 줄: 'winget 으로 설치 가능' 판정이고 형식이 맞는 winget id 가 있는 항목(설치됨/버전 다름/불필요/수동은 아님)."""
        return it.status == WINGET and valid_winget_id(it.winget_id)

    def picked_items(self) -> list:
        """지금 확인 결과에서 여전히 고를 수 있는 선택 항목(다시 확인 뒤 설치된 것은 자동으로 빠진다)."""
        r = self.state.install_check
        if r is None:
            return []
        return [it for it in r.items if self._pickable(it) and it.name in self.state.install_picks]

    def install_pick_count(self) -> int:
        return len(self.picked_items())

    def toggle_install_pick(self, name: str) -> bool:
        """설치할 프로그램 선택 켜기/끄기. 고를 수 없는 줄이면 아무것도 바꾸지 않고 False."""
        r = self.state.install_check
        it = next((i for i in r.items if i.name == name), None) if r is not None else None
        if it is None or not self._pickable(it):
            return False
        self.state.install_picks.symmetric_difference_update({name})
        self.emit("install_picks", name)
        return name in self.state.install_picks

    def prepare_selected_install(self) -> dict:
        """선택한 프로그램만 담은 winget.json 을 폴더로 꺼내고(같은 이름 파일은 .bak 보존) 직접 실행할 명령을 만든다. 실행은 하지 않는다.

        묶음의 목록 파일에 있는 항목만 파일에 담기고, 대표 앱 표로만 연결된 항목은 id 별 명령으로 안내한다."""
        s = self.state
        if not s.package:
            raise GuardError(T.G_INSTALL_NEED_PACKAGE)
        if s.install_check is None:
            raise GuardError(T.G_INSTALL_NOT_YET)
        if not s.install_picks:
            raise GuardError(T.INST_PICK_NONE)
        items = self.picked_items()
        if not items:
            raise GuardError(T.INST_PICK_NOTHING_LEFT)
        in_file = safe_winget_ids([i.winget_id for i in items if i.in_import_file])
        rest = safe_winget_ids([i.winget_id for i in items if not i.in_import_file])
        cmds = selected_commands(None, [*in_file, *rest])
        each = dict(zip(cmds["ids"], cmds["each"]))
        target, bak, command = None, None, ""
        if in_file:
            folder = s.install_folder or self.default_install_folder()
            target, bak = self.api.run_winget_extract(s.package, folder, **_supported(self.api.run_winget_extract, only_ids=in_file))
            command = selected_commands(fs.plain_path_text(str(target)), in_file)["import"]
        else:
            command = "\n".join(each[i] for i in rest)
        s.install_command = command
        return {"command": command, "file": str(target) if target else "", "backup": str(bak) if bak else "", "count": len(items),
                "ids": [*in_file, *rest], "each": [each[i] for i in in_file], "individual": [each[i] for i in rest],
                "file_ids": in_file}

    def install_summary_text(self) -> str:
        r = self.state.install_check
        if r is None:
            return T.INST_NONE
        c = r.counts()
        return T.INST_SUMMARY.format(total=len(r.items), installed=c["installed"], diff=r.version_diff_count, winget=c["winget"],
                                     manual=c["manual"], ambiguous=c["ambiguous"], not_needed=c["not_needed"])

    def install_notes(self) -> list[str]:
        """요약 아래에 보여 줄 안내 문장들(남은 개수, 다시 확인 결과, 설치 도구 상태, 애매함 설명)."""
        s = self.state
        r = s.install_check
        if r is None:
            return []
        out = [T.INST_LEFT.format(n=r.remaining) if r.remaining else T.INST_ALL_DONE]
        if s.install_prev is not None:
            out.append(T.INST_RECHECK_NEW.format(n=len(s.install_new), names=", ".join(s.install_new[:5])
                                                 + (" …" if len(s.install_new) > 5 else "")) if s.install_new
                       else T.INST_RECHECK_SAME)
        if not r.winget_ok:
            out.append(T.INST_WINGET_MISSING)
        if not r.has_winget_file:
            out.append(T.INST_NO_WINGET_FILE)
        if r.by_status("ambiguous"):
            out.append(T.INST_AMBIGUOUS_NOTE)
        return out

    def toggle_install_mark(self, name: str) -> bool:
        """'설치 끝냄' 표시(기억용). 설치 여부 판정에는 쓰지 않는다."""
        marks = self.state.install_marks
        marks.symmetric_difference_update({name})
        self.emit("install_marks", name)
        return name in marks

    def default_install_folder(self) -> str:
        return os.path.join(self.default_desktop(), T.INST_FOLDER_DEFAULT_NAME)

    def set_install_folder(self, path: str) -> None:
        self.state.install_folder = path
        self.emit("install_folder", path)

    def prepare_install_command(self) -> dict:
        """묶음의 winget.json 을 폴더로 꺼내고(같은 이름 파일은 .bak 으로 보존) 사용자가 직접 실행할 명령을 만든다. 명령은 실행하지 않는다."""
        s = self.state
        r = s.install_check
        if not s.package:
            raise GuardError(T.G_INSTALL_NEED_PACKAGE)
        if r is None:
            raise GuardError(T.G_INSTALL_NOT_YET)
        if not r.has_winget_file:
            raise GuardError(T.G_INSTALL_NO_WINGET)
        if not r.import_needed:
            raise GuardError(T.INST_COPY_NOTHING)
        folder = s.install_folder or self.default_install_folder()
        target, bak = self.api.run_winget_extract(s.package, folder)
        cmd = import_command(fs.plain_path_text(str(target)))
        s.install_command = cmd
        return {"command": cmd, "file": str(target), "backup": str(bak) if bak else "", "individual": list(r.individual_commands)}

    def install_next(self, skip: bool = False) -> None:
        """미리보기 화면으로. skip=True 는 '건너뛰기'(설치 확인 없이 진행했다는 표시만 남는다)."""
        self.state.install_skipped = bool(skip)
        self.go(S.NEW_PREVIEW)

    def _reset_confirms(self) -> None:
        s = self.state
        s.preview_checked = s.bak_understood = s.final_confirmed = s.extra_consent = False
