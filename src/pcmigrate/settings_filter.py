"""'허용 방식(설정 파일만)': 규칙 없는 프로그램(extra)을 내보낼 때 설정 형식 파일만 담는 기준을 한 곳에 모은다.

배경: 예전 방식('all')은 고른 폴더 전체에서 캐시만 빼고 담아 용량이 크고 정크가 섞였다. 기본(config)은 아래 표의 확장자만 허용하고,
파일당/폴더당 크기 상한과 파일 수 상한을 넘는 것은 담지 않고 사유를 기록한다. 이 모듈은 읽기 전용 판정만 한다(파일을 만지지 않는다).

근거 등급(이 표는 개발 시점의 일반 지식 기준이며, 프로그램/버전별 편차가 있을 수 있어 현장 확인이 필요하다):
  A = 사람이 읽는 텍스트 설정 형식으로 널리 문서화됨(거의 모든 프로그램이 설정 저장에 씀).
  B = 건축 CAD/BIM 프로그램이 사용자 정의(단축키/명령 별칭/메뉴/템플릿/출력 스타일) 파일로 공식 문서에 적는 형식.
  C = 프로그램/버전에 따라 설정일 수도, 사용자 자료일 수도 있는 형식(범위가 좁아 허용하되 현장 확인 권장).
허용하지 않는 것(기본 제외 유지): 실행/스크립트/라이브러리(.exe .dll .lsp .py .js ...: 복원도 거부), 라이선스(.lic 와 이름에 license),
로그(.log), 캐시/임시/잠금, 데이터베이스(.db .sqlite .dat .bin), 이미지/영상, 모델/문서 자료(.dwg .rvt .skp .3dm 등은 설정이 아니라 작업물),
.txt/.csv(설정인지 메모인지 구별할 수 없음), 글꼴(.shx .ttf 등).
.reg 는 의도적으로 허용하지 않는다: 더블클릭 한 번으로 레지스트리를 바꾸는 실행성 파일이라 복원 거부 목록(package.DENY_EXTENSIONS)에도 있다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

EXTRA_MODE_CONFIG = "config"  # 허용 방식(기본): 설정 형식 파일만
EXTRA_MODE_ALL = "all"        # 예전 방식: 폴더 전체(캐시/라이선스/링크/보안 거부 형식만 제외)
EXTRA_MODES = (EXTRA_MODE_CONFIG, EXTRA_MODE_ALL)
DEFAULT_EXTRA_MODE = EXTRA_MODE_CONFIG

# 확장자 -> (근거 등급, 설명). 소문자, 점 포함.
ALLOWED_CONFIG_EXTS: dict[str, tuple[str, str]] = {
    # A: 텍스트 설정 표준 형식
    ".ini": ("A", "INI 설정"), ".cfg": ("A", "설정"), ".conf": ("A", "설정"), ".config": ("A", ".NET/일반 설정"),
    ".json": ("A", "JSON 설정(단축키/사용자 설정 등)"), ".xml": ("A", "XML 설정(단축키/도구 막대 등)"),
    ".yaml": ("A", "YAML 설정"), ".yml": ("A", "YAML 설정"), ".toml": ("A", "TOML 설정"),
    ".properties": ("A", "Java 속성 설정"), ".jsonc": ("A", "주석 허용 JSON 설정(VS Code 등)"),
    ".code-snippets": ("A", "VS Code 사용자 코드 조각"),
    # B: 건축 CAD/BIM 사용자 정의
    ".pgp": ("B", "AutoCAD 명령 별칭"), ".cui": ("B", "AutoCAD 사용자 인터페이스(구)"), ".cuix": ("B", "AutoCAD 사용자 인터페이스"),
    ".mns": ("B", "AutoCAD 메뉴 소스"), ".dwt": ("B", "AutoCAD 도면 템플릿"),
    ".ctb": ("B", "AutoCAD 색상 기준 출력 스타일"), ".stb": ("B", "AutoCAD 이름 기준 출력 스타일"),
    ".pc3": ("B", "AutoCAD 플로터 구성"), ".pmp": ("B", "AutoCAD 플로터 모델 매개변수"),
    ".lin": ("B", "AutoCAD 선종류 정의(텍스트)"), ".pat": ("B", "AutoCAD 해치 패턴 정의(텍스트)"),
    ".rte": ("B", "Revit 프로젝트 템플릿"), ".rft": ("B", "Revit 패밀리 템플릿"),
    ".rui": ("B", "Rhino 도구 모음/사용자 인터페이스"),
    ".plist": ("B", "속성 목록 설정(일부 프로그램이 Windows 에서도 사용)"),
    # C: 근거 약함(프로그램/버전별 편차)
    ".bpx": ("C", "Bluebeam 도구 세트"), ".bfx": ("C", "Bluebeam 프로필"), ".tpl": ("C", "ArchiCAD 등 템플릿"),
}
# 확장자 없는 점 파일 중 설정으로 허용하는 이름(소문자).
ALLOWED_CONFIG_NAMES: frozenset[str] = frozenset({".gitconfig", ".gitattributes", ".editorconfig", ".gitignore_global"})
# 허용 확장자여도 정크로 보는 이름(소문자 완전 일치) / 이름에 들어 있으면 라이선스로 보는 낱말.
JUNK_NAMES: frozenset[str] = frozenset({"desktop.ini", "thumbs.db"})
LICENSE_WORDS: tuple[str, ...] = ("license", "licence", "eula")

# 상한(근거: 설정 파일은 보통 수 KB~수 MB. 템플릿(.rte 등)이 수십 MB 일 수 있어 파일당 32MB 까지 허용).
MAX_FILE_BYTES = 32 * 1024 * 1024        # 파일 하나
MAX_FOLDER_BYTES = 256 * 1024 * 1024     # 직접 고른 폴더(항목) 하나에 담는 총량
MAX_FOLDER_FILES = 5000                  # 직접 고른 폴더(항목) 하나에 담는 파일 수

# 제외 사유 코드
NOT_CONFIG, LICENSE, JUNK = "not_config", "license", "junk"
TOO_BIG, FOLDER_BYTES, FOLDER_FILES = "file_size", "folder_bytes", "folder_files"
LIMIT_KINDS = (TOO_BIG, FOLDER_BYTES, FOLDER_FILES)
NOT_CONFIG_REASON = "설정 형식이 아니라 제외(허용 방식)"
LIMIT_REASON = "상한을 넘어 제외(허용 방식: 파일 크기/폴더 용량/파일 수)"
LIMIT_TEXT = {TOO_BIG: "파일 하나가 너무 큼", FOLDER_BYTES: "폴더 용량 상한 초과", FOLDER_FILES: "폴더 파일 수 상한 초과"}
BY_EXT_KEEP = 12  # 설정 형식이 아닌 제외 파일을 확장자별로 기록할 때 남길 가짓수(나머지는 '기타')
LIMIT_EXAMPLES = 5


@dataclass(frozen=True)
class ConfigLimits:
    file_bytes: int = MAX_FILE_BYTES
    folder_bytes: int = MAX_FOLDER_BYTES
    folder_files: int = MAX_FOLDER_FILES


def default_limits() -> ConfigLimits:
    """호출 시점의 모듈 상수로 만든다(시험에서 상수를 바꿔 쓸 수 있게)."""
    return ConfigLimits(MAX_FILE_BYTES, MAX_FOLDER_BYTES, MAX_FOLDER_FILES)


def ext_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def is_config_name(name: str) -> bool:
    """이름(확장자/점 파일 이름)만으로 설정 형식인지. 정크/라이선스 이름은 호출자가 따로 거른다."""
    low = name.lower().rstrip(" .")
    return low in ALLOWED_CONFIG_NAMES or ext_of(low) in ALLOWED_CONFIG_EXTS


def grade_of(name: str) -> str:
    low = name.lower().rstrip(" .")
    if low in ALLOWED_CONFIG_NAMES:
        return "A"
    return ALLOWED_CONFIG_EXTS.get(ext_of(low), ("", ""))[0]


class ConfigGate:
    """폴더(extra 항목) 하나에 대한 허용 방식 판정기. 파일을 정해진 순서로 한 개씩 judge() 에 넣는다(순서가 같으면 결과도 같다)."""

    def __init__(self, limits: ConfigLimits | None = None) -> None:
        self.limits = limits or default_limits()
        self.files = 0
        self.bytes = 0
        self.ext_counts: dict[str, int] = {}
        self.nc_files = 0
        self.nc_bytes = 0
        self.nc_by_ext: dict[str, list[int]] = {}
        self.lim_files = 0
        self.lim_bytes = 0
        self.lim_kinds: dict[str, int] = {}
        self.lim_examples: list[str] = []

    def judge(self, name: str, size: int, shown: str = "") -> str | None:
        """None 이면 담는다(통계에 더함). 아니면 제외 사유 코드를 돌려주고 통계에 기록한다."""
        low = name.lower().rstrip(" .")
        if not is_config_name(low) or low in JUNK_NAMES or any(w in low for w in LICENSE_WORDS):
            self.nc_files += 1
            self.nc_bytes += size
            e = self.nc_by_ext.setdefault(ext_of(low) or "(확장자 없음)", [0, 0])
            e[0] += 1
            e[1] += size
            return NOT_CONFIG
        lim = self.limits
        kind = None
        if size > lim.file_bytes:
            kind = TOO_BIG
        elif self.files + 1 > lim.folder_files:
            kind = FOLDER_FILES
        elif self.bytes + size > lim.folder_bytes:
            kind = FOLDER_BYTES
        if kind:
            self.lim_files += 1
            self.lim_bytes += size
            self.lim_kinds[kind] = self.lim_kinds.get(kind, 0) + 1
            if len(self.lim_examples) < LIMIT_EXAMPLES:
                self.lim_examples.append(shown or name)
            return kind
        self.files += 1
        self.bytes += size
        k = ext_of(low) or low
        self.ext_counts[k] = self.ext_counts.get(k, 0) + 1
        return None

    def by_ext_record(self) -> dict[str, list[int]]:
        ordered = sorted(self.nc_by_ext.items(), key=lambda kv: (-kv[1][0], kv[0]))
        out = {k: list(v) for k, v in ordered[:BY_EXT_KEEP]}
        rest = ordered[BY_EXT_KEEP:]
        if rest:
            out["(기타)"] = [sum(v[0] for _, v in rest), sum(v[1] for _, v in rest)]
        return out

    def skipped_records(self, rule_id: str, path: str, name: str) -> list[dict]:
        """manifest['skipped'] 에 넣을 요약 항목(파일마다 하나가 아니라 폴더마다 최대 2개)."""
        out: list[dict] = []
        if self.nc_files:
            out.append({"rule": rule_id, "path": path, "name": name, "reason": NOT_CONFIG_REASON, "not_config": True,
                        "files": self.nc_files, "bytes": self.nc_bytes, "by_ext": self.by_ext_record()})
        if self.lim_files:
            out.append({"rule": rule_id, "path": path, "name": name, "reason": LIMIT_REASON, "limit": True,
                        "files": self.lim_files, "bytes": self.lim_bytes, "kinds": dict(self.lim_kinds),
                        "examples": list(self.lim_examples)})
        return out

    def coverage_fields(self) -> dict:
        """설정 확인표(coverage)에 넣을 필드(manifest 호환: 기존 키는 건드리지 않고 덧붙인다)."""
        return {"config_mode": True, "allowed_files": self.files, "allowed_bytes": self.bytes,
                "ext_counts": dict(sorted(self.ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                "not_config_files": self.nc_files, "not_config_bytes": self.nc_bytes,
                "limit_files": self.lim_files, "limit_bytes": self.lim_bytes}


@dataclass
class ConfigEstimate:
    """'이 프로그램 설정 찾기' 후보 하나에 대해 허용 방식으로 담길 양(읽기 전용 추정). complete=False 면 시간 상한에 걸려 이상이다."""
    files: int = 0
    bytes: int = 0
    excluded_files: int = 0   # 설정 형식이 아니라 빠질 파일
    excluded_bytes: int = 0
    limit_files: int = 0      # 상한을 넘어 빠질 파일
    limit_bytes: int = 0
    ext_counts: dict[str, int] = field(default_factory=dict)
    complete: bool = True
