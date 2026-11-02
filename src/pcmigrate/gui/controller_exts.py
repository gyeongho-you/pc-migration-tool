"""컨트롤러의 '찾을 파일 형식 설정' 부분(stage61). Controller 가 이 믹스인을 상속한다.

규칙은 모두 백엔드(find_exts)에 있다. 여기서는 창이 열려 있는 동안의 편집본(저장 전)과 '바뀐 것' 계산, 쉬운 문구 변환만 한다.
- 편집본은 저장해야 반영된다. 초기화만 예외로 바로 실행한다(백엔드가 이전 설정을 번호 보관본으로 남긴다).
- 설정 파일이 손상돼도 기본 목록으로 열린다(백엔드 load 가 오류 로그와 보관을 맡는다).
- 예기치 못한 예외는 오류 로그에 남기고 쉬운 문구로 알린다. 결과(성공/실패/바뀐 것 없음)는 항상 ExtsResult 로 돌려준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import find_exts as fx
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
        return fx.describe(self.exts.cfg)

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
