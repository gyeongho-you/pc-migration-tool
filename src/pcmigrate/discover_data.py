"""discover(규칙 없는 프로그램 탐지)에 쓰이는 데이터 표. 테스트로 보호한다.

discover 는 읽기 전용이다. 여기 있는 표는 '무엇을 후보로 볼지/무엇을 건너뛸지/무엇을 비밀 위험으로 볼지'만 정한다.
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- 탐색 루트
# (키, 환경변수 템플릿, 설명). export --extra / import 가 허용하는 루트도 이 표(EXTRA_ROOTS)로만 제한한다.


@dataclass(frozen=True)
class Root:
    key: str
    template: str
    label: str
    dotfiles_only: bool = False  # %USERPROFILE% 은 바로 아래 '.'으로 시작하는 폴더/파일만


ROOTS: tuple[Root, ...] = (
    Root("appdata", "%APPDATA%", "AppData\\Roaming"),
    Root("localappdata", "%LOCALAPPDATA%", "AppData\\Local"),
    Root("documents", "%USERPROFILE%\\Documents", "Documents"),
    Root("onedrive", "%OneDrive%\\Documents", "OneDrive Documents(리디렉션 후보)"),
    Root("onedrive-business", "%OneDriveCommercial%\\Documents", "OneDrive 회사/학교 Documents(리디렉션 후보)"),
    Root("programdata", "%PROGRAMDATA%", "ProgramData"),
    Root("home", "%USERPROFILE%", "사용자 홈(점 폴더/파일)", dotfiles_only=True),
)
EXTRA_ROOTS: tuple[str, ...] = tuple(r.template for r in ROOTS)

# --------------------------------------------------------------------------- 제외(노이즈/시스템)
# 루트 바로 아래 항목 이름(소문자 완전 일치). 캐시/임시/시스템 구성요소만. 업무용 앱 이름은 넣지 않는다.
SKIP_NAMES: frozenset[str] = frozenset({
    "temp", "tmp", "cache", "caches", "crashdumps", "d3dscache", "packages", "publishers", "history",
    "inetcache", "comms", "connecteddevices", "programs", "squirreltemp", "virtualstore",
    "microsoft", "windows", "package cache", "softwaredistribution", "usoshared", "usoprivate",
    "desktop.ini", "thumbs.db", "elevateddiagnostics", "packages", "placeholdertilelogoplate",
    "application data", "start menu", "templates", "my music", "my pictures", "my videos",
    "default", "default user", "all users", "public",
})
# 루트 바로 아래 항목 이름이 이 패턴(소문자 부분 문자열)을 포함하면 캐시/로그류로 보고 제외.
SKIP_SUBSTRINGS: tuple[str, ...] = ("cache", "crashpad", "crash reports", "telemetry", ".tmp", "$recycle")
SKIP_PREFIXES: tuple[str, ...] = ("regid.", "~", "{")

# 점 폴더/파일 중 후보에서 빼는 것(셸/도구가 매번 만드는 캐시성). 소문자 완전 일치.
DOT_SKIP: frozenset[str] = frozenset({".", "..", ".cache", ".local", ".dotnet", ".nuget", ".templateengine",
                                      ".gradle-cache", ".lesshst", ".viminfo", ".bash_history",
                                      ".python_history", ".node_repl_history", ".sqlite_history"})

# --------------------------------------------------------------------------- 점 폴더/파일 -> 프로그램 힌트
# 소문자 이름 -> 설치 프로그램 표시 이름에 들어 있을 부분 문자열들(하나라도 있으면 '추정' 연결).
DOT_HINTS: dict[str, tuple[str, ...]] = {
    ".gitconfig": ("git",), ".git-credentials": ("git",), ".gitignore_global": ("git",),
    ".ssh": ("openssh", "putty", "git"), ".aws": ("aws command line", "aws cli"),
    ".m2": ("maven", "intellij", "java"), ".gradle": ("gradle", "intellij", "android studio"),
    ".vscode": ("visual studio code",), ".kube": ("kubernetes", "docker desktop", "kubectl"),
    ".docker": ("docker",), ".npmrc": ("node.js", "nodejs"), ".pypirc": ("python",),
    ".config": (), ".android": ("android studio",), ".idlerc": ("python",),
    ".jdks": ("intellij", "java"), ".sdkman": (), ".azure": ("azure",),
}

# --------------------------------------------------------------------------- 비밀 정보 가능성(이름 패턴)
# 폴더/파일 이름(소문자 부분 문자열)에 있으면 '비밀 정보 가능성' 경고 + 기본 제외 사유로 표시한다.
# 일부는 요청 목록 외 보강: id_rsa/id_ed25519(개인키), logins.json·key4.db(Firefox), .netrc/.git-credentials/.npmrc/.pypirc
# (토큰이 평문으로 들어가는 설정), kdbx/.pwd(비밀번호 저장소), .token.
SECRET_PATTERNS: tuple[str, ...] = (
    "credentials", "credential", "token", "secret", "keychain", ".ssh", ".aws", ".kube",
    "login data", "cookies", "id_rsa", "id_ed25519", "logins.json", "key4.db", ".netrc",
    ".git-credentials", ".npmrc", ".pypirc", ".kdbx", "c.pwd", "password", "authorized_keys",
    "user data",  # Chromium 계열 브라우저의 프로필 루트
)

# 브라우저 프로필/쿠키 DB 가 들어 있을 수 있는 제조사/브라우저 폴더(소문자 완전 일치). 이름만으로 '비밀 정보 가능성'
# 으로 본다(하위에 Chrome/Edge/Firefox 프로필이 있는 상위 제조사 폴더 포함, 예: %LOCALAPPDATA%\Google).
BROWSER_VENDOR_DIRS: frozenset[str] = frozenset({
    "google", "chrome", "chromium", "microsoft edge", "edge", "mozilla", "firefox", "bravesoftware", "brave",
    "opera software", "opera", "vivaldi", "yandex", "naver", "whale", "naver whale",
})

# --------------------------------------------------------------------------- 가져오기 방어
# export --extra 로 들어온 항목을 복원할 때 거부할 경로 조각(소문자). 시작 프로그램 등록 경로를 통한 실행을 막는다.
RESTORE_DENY_SUBSTRINGS: tuple[str, ...] = ("\\startup\\", "\\start menu\\", "authorized_keys")
