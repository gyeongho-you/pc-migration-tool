"""컨트롤러의 '찾을 파일 형식 설정' 부분(stage61). Controller 가 이 믹스인을 상속한다.

규칙은 모두 백엔드(find_exts)에 있다. 여기서는 창이 열려 있는 동안의 편집본(저장 전)과 '바뀐 것' 계산, 쉬운 문구 변환만 한다.
- 편집본은 저장해야 반영된다. 초기화만 예외로 바로 실행한다(백엔드가 이전 설정을 번호 보관본으로 남긴다).
- 설정 파일이 손상돼도 기본 목록으로 열린다(백엔드 load 가 오류 로그와 보관을 맡는다).
- 예기치 못한 예외는 오류 로그에 남기고 쉬운 문구로 알린다. 결과(성공/실패/바뀐 것 없음)는 항상 ExtsResult 로 돌려준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import re

from .. import find_exts as fx
from .. import find_exts_io as fio
from . import texts as T
from .controller_common import GuardError


@dataclass
class ExtsResult:
    ok: bool
    message: str = ""
    changed: bool = False
    warnings: list = field(default_factory=list)
    closed: bool = False  # 저장이 끝나 창을 닫아도 되는가


@dataclass
class ExtsState:
    base: fx.ExtConfig = field(default_factory=fx.ExtConfig)  # 창을 열 때(또는 초기화 직후)의 설정
    cfg: fx.ExtConfig = field(default_factory=fx.ExtConfig)   # 편집본
    open_note: str = ""                                       # 열 때 알릴 경고(손상 등)
    saved_notice: str = ""                                    # 저장 뒤 찾기 화면에 보일 안내(새로 찾으면 지움)
    has_misc: bool = False                                    # 저장된 설정에 '기타' 형식이 있는가(찾기 화면의 기타 칩 표시용)


@dataclass
class ExtsImportCheck:
    ok: bool            # 검사를 통과했는가
    same: bool = False  # 통과했지만 현재 설정과 같아 바꿀 것이 없는가
    message: str = ""   # 안내 상자에 보일 한 줄
    text: str = ""      # 거부면 창에 보일 줄 목록 글, 통과면 확인창 글


# 백엔드 문구의 내부 단어(파일 안 이름, JSON 등)를 화면에 보이기 전에 쉬운 말로 바꾼다(화면 문구에는 이런 말을 쓰지 않는다)
_WHERE = {"added": "추가 목록", "removed": "삭제 목록", "format": "파일 종류 표식", "version": "버전", "tool_version": "도구 버전",
          "created": "만든 시각", "note": "메모", "키": "항목", "전체": "전체"}
_BODY = [
    (re.compile(r"\(허용: [^)]*\)"), "(이 도구가 내보낸 파일에는 없는 항목입니다)"),
    (re.compile(r"\(format 이 '[^']*' 이어야 합니다\)"), "(이 도구의 '내보내기'로 만든 파일이 아닙니다)"),
    (re.compile(r"\(design, photo, video, doc, archive, misc 중 하나[^)]*\)"), "(설계, 사진, 영상, 문서, 압축, 기타 중 하나여야 합니다)"),
    (re.compile(r"\((NaN|-?Infinity)\)"), "(숫자로 읽을 수 없는 값)"),
    (re.compile(r"\{ \} 형태\(확장자: 분류\)여야 합니다"), "추가할 형식 목록의 모양이 올바르지 않습니다"),
    (re.compile(r"\[ \] 형태\(확장자 목록\)여야 합니다"), "삭제할 형식 목록의 모양이 올바르지 않습니다"),
    (re.compile(r"맨 바깥이 \{ \} 형태가 아닙니다"), "파일의 맨 바깥 구조가 올바르지 않습니다"),
    (re.compile(r"JSON 형식"), "설정 파일 형식"),
    (re.compile(r"\bJSON\b"), "설정 파일"),
    (re.compile(r"\bmisc\b"), "기타"),
]


def friendly_line(line: str) -> str:
    """백엔드가 만든 거부 줄(예: "7번째 줄 added: '.x': 이유")의 내부 단어를 한글로 바꾼다."""
    m = re.match(r"^((?:\d+번째 줄 )?)([A-Za-z_가-힣]+)(: .*)$", line)
    if m and m.group(2) in _WHERE:
        line = m.group(1) + _WHERE[m.group(2)] + m.group(3)
    for rx, rep in _BODY:
        line = rx.sub(rep, line)
    return line


class ExtsMixin:
    exts_directory = None  # 시험에서 설정 폴더를 바꿔 끼우는 자리(None 이면 백엔드 기본 위치)
    _exts_state: ExtsState | None = None

    @property
    def exts(self) -> ExtsState:
        if self._exts_state is None:
            self._exts_state = ExtsState()
        return self._exts_state

    def _exts_fail(self, e: BaseException, what: str) -> str:
        self.record_error(e, f"찾을 파일 형식 설정: {what}", None)
        return T.FX_UNEXPECTED

    # ------------------------------------------------------------------ 열기
    def exts_begin(self) -> ExtsState:
        """창을 열기 전에 설정을 읽어 편집본을 만든다. 작업 중이면 GuardError(찾는 도중에는 열지 않는다)."""
        if self.state.busy:
            raise GuardError(T.G_BUSY)
        st = self.exts
        cfg = fx.load(self.exts_directory)
        st.base, st.cfg = cfg.copy(), cfg.copy()
        st.open_note = T.FX_DAMAGED if cfg.damaged else " ".join(cfg.notes)
        return st

    def exts_describe(self) -> dict:
        return fx.describe(self.exts.cfg, show_empty_misc=True)  # '기타' 묶음은 비어 있어도 항상 보인다

    # ------------------------------------------------------------------ 찾기 화면의 '기타' 칩
    def exts_refresh_misc(self) -> bool:
        """저장된 설정에 '기타' 형식이 있는지 다시 읽는다(찾기 화면이 열릴 때, 저장·초기화·가져오기 뒤)."""
        try:
            cfg = fx.load(self.exts_directory)
            self.exts.has_misc = any(v == fx.MISC for v in cfg.added.values())
        except Exception as e:  # noqa: BLE001
            self.record_error(e, "찾을 파일 형식 설정: 기타 확인", None)
            self.exts.has_misc = False
        return self.exts.has_misc

    def exts_has_misc(self) -> bool:
        return self.exts.has_misc

    # ------------------------------------------------------------------ 바뀐 것
    def exts_changes(self) -> tuple[int, int]:
        """(추가 수, 삭제 수). 삭제했던 기본 형식을 되돌리면 추가로, 추가했던 형식을 빼면 삭제로 센다."""
        b, c = self.exts.base, self.exts.cfg
        added = len(set(c.added.items()) - set(b.added.items())) + len(set(b.removed) - set(c.removed))
        removed = len(set(c.removed) - set(b.removed)) + len(set(b.added) - set(c.added))
        return added, removed

    def exts_dirty(self) -> bool:
        b, c = self.exts.base, self.exts.cfg
        return b.added != c.added or set(b.removed) != set(c.removed)

    def exts_dirty_text(self) -> str:
        a, r = self.exts_changes()
        return T.FX_DIRTY.format(a=a, r=r) if self.exts_dirty() else T.FX_CLEAN

    # ------------------------------------------------------------------ 편집(저장 전)
    def exts_add(self, ext: str, category: str) -> ExtsResult:
        try:
            ch = fx.add(self.exts.cfg, ext, category)
        except ValueError as e:  # 입력 오류와 상한: 백엔드 문구 그대로
            return ExtsResult(False, str(e))
        except Exception as e:  # noqa: BLE001
            return ExtsResult(False, self._exts_fail(e, "추가"))
        if ch.changed:
            self.exts.cfg = ch.cfg
        return ExtsResult(True, ch.message, ch.changed, list(ch.warnings))

    def exts_remove(self, ext: str) -> ExtsResult:
        try:
            ch = fx.remove(self.exts.cfg, ext)
        except ValueError as e:
            return ExtsResult(False, str(e))
        except Exception as e:  # noqa: BLE001
            return ExtsResult(False, self._exts_fail(e, "삭제"))
        if ch.changed:
            self.exts.cfg = ch.cfg
        return ExtsResult(True, ch.message, ch.changed)

    def exts_restore(self, ext: str) -> ExtsResult:
        """삭제한 기본 형식을 원래 분류로 되돌린다(다른 분류로 다시 넣었던 것이 있으면 먼저 뺀다)."""
        try:
            cfg = self.exts.cfg
            if ext in cfg.added:
                cfg = fx.remove(cfg, ext).cfg
            ch = fx.add(cfg, ext, fx.fd.EXT_TABLE[ext][0])
        except (KeyError, ValueError) as e:
            return ExtsResult(False, str(e))
        except Exception as e:  # noqa: BLE001
            return ExtsResult(False, self._exts_fail(e, "되돌리기"))
        self.exts.cfg = ch.cfg
        return ExtsResult(True, ch.message, True)

    # ------------------------------------------------------------------ 저장 / 초기화
    def exts_save(self) -> ExtsResult:
        if not self.exts_dirty():
            return ExtsResult(True, T.FX_NOTHING_TO_SAVE, False, closed=True)
        try:
            fx.save(self.exts.cfg, self.exts_directory)
        except fx.FindExtsError:  # 백엔드가 이미 오류 로그에 남겼다
            return ExtsResult(False, T.FX_SAVE_FAILED)
        except Exception as e:  # noqa: BLE001
            return ExtsResult(False, self._exts_fail(e, "저장"))
        self.exts.base = self.exts.cfg.copy()
        self.exts.saved_notice = T.FX_SAVED_NOTICE
        self.exts_refresh_misc()
        self.emit("files_changed")  # 찾기 화면이 안내 한 줄을 그린다
        return ExtsResult(True, T.FX_SAVED_NOTICE, True, closed=True)

    def exts_reset(self) -> ExtsResult:
        """기본값으로 즉시 되돌린다(이전 설정은 번호 보관본으로 남는다). 초기화 직후 상태가 새 편집 시작점."""
        try:
            r = fx.reset(self.exts_directory)
        except Exception as e:  # noqa: BLE001
            return ExtsResult(False, self._exts_fail(e, "초기화"))
        if not r.ok:
            return ExtsResult(False, T.FX_RESET_FAILED)
        cfg = fx.load(self.exts_directory)
        self.exts.base, self.exts.cfg = cfg.copy(), cfg.copy()
        self.exts_refresh_misc()
        return ExtsResult(True, r.message, r.changed)

    # ------------------------------------------------------------------ 찾기 화면의 안내
    def exts_notice_lines(self, result=None) -> list[str]:
        """찾기 화면 안내 상자에 보일 줄: 방금 저장했다는 안내, 찾기 결과의 형식 설정 안내/경고(ext_notes)."""
        lines = []
        if self.exts.saved_notice:
            lines.append(self.exts.saved_notice)
        lines += [str(n) for n in (getattr(result, "ext_notes", None) or [])]
        return lines

    def exts_clear_notice(self) -> None:
        self.exts.saved_notice = ""

    # ------------------------------------------------------------------ 내보내기 / 가져오기
    def exts_export(self, path: str) -> ExtsResult:
        """저장된 설정(편집 중인 내용이 아님)을 파일로 내보낸다. 결과는 항상 돌려준다."""
        try:
            r = fio.export_to(path, self.exts_directory)
        except Exception as e:  # noqa: BLE001
            return ExtsResult(False, self._exts_fail(e, "내보내기"))
        msg = r.message + (" " + T.FX_EXPORT_NOTE if r.ok else "")
        return ExtsResult(r.ok, msg, r.ok, list(r.notes))

    def exts_import_check(self, path: str) -> ExtsImportCheck:
        """파일을 먼저 읽어 검사만 한다(아무것도 바꾸지 않음). 거부면 줄 목록 글, 통과면 확인창 글을 만든다."""
        try:
            r = fio.import_from(path, dry_run=True, directory=self.exts_directory)
        except Exception as e:  # noqa: BLE001
            msg = self._exts_fail(e, "가져오기 검사")
            return ExtsImportCheck(False, False, msg, msg)
        if not r.ok:
            lines = [friendly_line(i) for i in r.issues] or [friendly_line(r.message)]
            body = "\n".join("· " + ln for ln in lines)
            return ExtsImportCheck(False, False, T.FX_IMPORT_REJECT_SHORT, f"{T.FX_IMPORT_REJECT_HEAD}\n\n{body}\n\n{T.FX_IMPORT_REJECT_TAIL}")
        if r.summary.get("same_as_current"):
            return ExtsImportCheck(True, True, r.message, r.message)
        sm = r.summary
        lines = [T.FX_IMPORT_CONFIRM_HEAD, T.FX_IMPORT_CONFIRM_SIZE.format(a=sm["result_added"], r=sm["result_removed"]),
                 T.FX_IMPORT_CONFIRM_DIFF.format(a=sm["new_added"], r=sm["new_removed"]), T.FX_IMPORT_CONFIRM_KEEP]
        lines += ["· " + friendly_line(w) for w in r.warnings]
        lines.append(T.FX_IMPORT_CONFIRM_TAIL)
        return ExtsImportCheck(True, False, "", "\n".join(lines))

    def exts_import_apply(self, path: str) -> ExtsResult:
        """검사를 통과한 파일을 교체로 적용한다(적용 전 현재 설정은 백엔드가 보관). 성공하면 편집본과 시작점을 새 설정으로 바꾼다."""
        try:
            r = fio.import_from(path, mode="replace", directory=self.exts_directory)
        except Exception as e:  # noqa: BLE001
            return ExtsResult(False, self._exts_fail(e, "가져오기"))
        if not r.ok or not r.applied:
            extra = " ".join(friendly_line(i) for i in r.issues[:3])
            msg = friendly_line(r.message) if r.ok else (T.FX_IMPORT_FAILED + (" " + extra if extra else ""))
            return ExtsResult(r.ok, msg, False)
        cfg = fx.load(self.exts_directory)
        self.exts.base, self.exts.cfg = cfg.copy(), cfg.copy()
        self.exts_refresh_misc()
        self.exts.saved_notice = T.FX_SAVED_NOTICE
        self.emit("files_changed")
        return ExtsResult(True, friendly_line(r.message), True, [friendly_line(w) for w in r.warnings])
