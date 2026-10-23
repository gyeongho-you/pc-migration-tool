"""환경변수 확장 함수 하나. 모든 경로(규칙, discover, export --extra, import 허용 루트)는 이 함수로만 해석한다.

os.path.expandvars 를 직접 쓰지 않는 이유:
- Windows 환경 블록은 대소문자만 다른 변수(예: Git Bash 에서 PROGRAMDATA=... 로 지정하면 기존 ProgramData 와 공존)를
  가질 수 있어 어느 쪽이 보일지 불확실하다. 이 함수는 덮어쓰기(override)를 먼저 보고, 그다음 환경을 대소문자 무시로 찾는다.
- 덮어쓰기(CLI 전역 옵션 --env NAME=VALUE, 테스트, 다른 사용자 프로필 분석)를 한 곳에서 지원한다.
- 확장 못 한 %VAR% 는 그대로 남겨(기존 동작) 호출자가 '확장 실패'를 판정하게 한다.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

_VAR = re.compile(r"%([^%\\/:*?\"<>|]+)%")
_overrides: dict[str, str] = {}


def set_overrides(values: Mapping[str, str] | None) -> None:
    """환경변수 덮어쓰기를 설정한다(None/빈 값이면 해제). 이름은 대소문자를 구분하지 않는다."""
    _overrides.clear()
    for k, v in (values or {}).items():
        _overrides[k.casefold()] = v


def get_overrides() -> dict[str, str]:
    return dict(_overrides)


def lookup(name: str) -> str | None:
    """덮어쓰기 -> 환경 순으로 변수 값을 찾는다(대소문자 무시). 없으면 None."""
    key = name.casefold()
    if key in _overrides:
        return _overrides[key]
    exact = os.environ.get(name)
    if exact is not None:
        return exact
    for k, v in os.environ.items():  # 대소문자만 다른 이름
        if k.casefold() == key:
            return v
    return None


def expand_env(text: str) -> str:
    """%VAR% 를 확장한다. 정의되지 않은 변수는 그대로 둔다."""
    def repl(m: re.Match) -> str:
        val = lookup(m.group(1))
        return m.group(0) if val is None else val
    return _VAR.sub(repl, text)


def parse_assignments(items: list[str] | None) -> dict[str, str]:
    """['NAME=VALUE', ...] -> dict. 형식이 틀리면 ValueError."""
    out: dict[str, str] = {}
    for it in items or []:
        name, sep, value = it.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"--env 는 NAME=VALUE 형식이어야 합니다: {it!r}")
        out[name.strip()] = value
    return out
