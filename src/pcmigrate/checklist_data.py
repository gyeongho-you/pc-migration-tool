"""체크리스트에 쓰이는 데이터 표(코드와 분리). 테스트(tests/test_checklist_v2.py)로 보호한다.

원칙: 확신이 없는 항목은 넣지 않는다. 숨기는(=참고로 접는) 패턴은 보수적으로, 업무용 앱이 걸리지 않게
구체적인 이름만 쓴다. 패턴은 모두 소문자 부분 문자열이며 설치 목록의 표시 이름(DisplayName)과 비교한다.
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- 공통(자동 복원 불가) 경고
# phase: "login" = 2. 로그인·인증, "data" = 3. 데이터 복사/기타
# always=True 이면 감지 여부와 무관하게 표시(거의 모든 PC 에 해당).
# always=False 이면 patterns(설치 목록 이름) 또는 rule_ids(감지된 규칙)에 걸릴 때만 표시.


@dataclass(frozen=True)
class CommonItem:
    id: str
    title: str
    text: str
    phase: str
    always: bool = False
    patterns: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    install_hint: str = ""  # 이 항목과 연결된 설치 항목 옆에 붙는 '무엇을/어떻게'
    covers: tuple[str, ...] = ()  # 이 항목이 표시되면 중복되는 규칙 경고(소문자 부분 문자열)는 생략


COMMON_ITEMS: tuple[CommonItem, ...] = (
    CommonItem(
        "autodesk", "Autodesk 계정/버전",
        "Autodesk 계열 — Autodesk 계정으로 재로그인하고 라이선스를 다시 활성화하세요(라이선스는 복원되지 않음). "
        "구 PC와 같은 버전을 설치해야 설정이 호환됩니다.",
        "login", patterns=("autodesk", "autocad", "revit", "navisworks"),
        rule_ids=("revit", "autocad", "navisworks"),
        install_hint="Autodesk 계정 로그인과 구 PC와 같은 버전 필요",
        covers=("autodesk 계정",)),
    CommonItem(
        "revit_cache", "Revit 로컬 캐시",
        "Revit 로컬 캐시 — 복원하지 않습니다. 신 PC에서 중앙 모델을 열어 로컬 사본을 다시 동기화하세요.",
        "data", patterns=("revit",), rule_ids=("revit",), covers=("revit 로컬 캐시",)),
    CommonItem(
        "dongle", "동글(하드웨어 락)",
        "동글(하드웨어 락) 프로그램 — 동글을 신 PC의 USB에 연결하고 제조사 드라이버를 설치한 뒤 인식되는지 확인하세요.",
        "login", patterns=("sentinel", "hasp", "safenet", "codemeter", "wibu", "rockey", "aladdin"),
        rule_ids=("archicad",)),
    CommonItem(
        "reactivate", "Rhino/V-Ray/Enscape/Lumion/ArchiCAD 재활성화",
        "Rhino / V-Ray / Enscape / Lumion / ArchiCAD — 재설치 후 각 프로그램에서 라이선스를 다시 활성화하세요"
        "(제품 키 또는 계정 필요, 복원되지 않음).",
        "login", patterns=("rhinoceros", "v-ray", "vray", "enscape", "lumion", "archicad", "graphisoft"),
        rule_ids=("rhino", "vray", "enscape", "lumion", "archicad"),
        install_hint="설치 후 라이선스 재활성화 필요"),
    CommonItem(
        "gpu", "GPU/드라이버",
        "GPU/드라이버 의존 설정 — 신 PC에 그래픽 드라이버(NVIDIA/AMD/Intel)를 먼저 설치한 뒤, "
        "렌더러·뷰포트의 GPU 설정을 직접 다시 확인하세요.",
        "data", always=True),
    CommonItem(
        "browser", "브라우저 비밀번호/쿠키",
        "브라우저 저장 비밀번호/쿠키 — Windows DPAPI 로 암호화되어 복사할 수 없습니다. "
        "구 PC에서 브라우저 계정 동기화를 확인하거나 비밀번호를 내보낸 뒤, 신 PC에서 다시 로그인하세요.",
        "login", always=True),
    CommonItem(
        "kakao_cert_finance", "카카오톡/공인인증서/금융보안",
        "카카오톡 대화 · 공인인증서 · 금융보안 프로그램 — 자동 이전되지 않습니다. 카카오톡은 대화 백업/이전 기능을, "
        "공인인증서는 인증서 내보내기(USB 등)를 쓰고, 금융보안 프로그램은 해당 은행 사이트 안내대로 신 PC에서 재설치하세요.",
        "data", always=True),
    CommonItem(
        "onedrive", "OneDrive 온디맨드",
        "OneDrive 파일 온디맨드 — 클라우드 전용 파일은 복사에 실패하거나 대량 다운로드가 발생합니다. "
        "필요한 폴더를 '항상 이 장치에 유지'로 설정해 다운로드를 끝낸 뒤 복사하세요"
        "(transfer 는 기본으로 건너뛰고 cloud_only 로 기록합니다).",
        "data", always=True),
    CommonItem(
        "office", "Microsoft Office 인증",
        "Microsoft Office — 설치 미디어(또는 Microsoft 365 계정)와 제품 키가 필요합니다. "
        "설치 후 라이선스 인증을 다시 해야 하며 복원되지 않습니다.",
        "login", patterns=("microsoft office", "microsoft 365", "office 16", "office standard",
                           "office professional"),
        install_hint="설치 미디어와 제품 키(또는 계정) 필요, 라이선스는 복원 안 됨"),
    CommonItem(
        "adobe", "Adobe CC 로그인",
        "Adobe Creative Cloud — Creative Cloud 앱을 설치하고 Adobe 계정으로 로그인하세요. "
        "구독 라이선스는 복원되지 않으며 각 앱은 Creative Cloud 에서 다시 설치합니다.",
        "login", patterns=("adobe creative cloud", "photoshop", "illustrator", "indesign", "premiere",
                           "after effects", "lightroom"),
        install_hint="Adobe 계정 로그인 필요, 구독 라이선스는 복원 안 됨"),
)

# --------------------------------------------------------------------------- 참고(설치 불필요 추정)
# (소문자 부분 문자열, 사유). 제조사 번들/드라이버/시스템 구성요소/업데이트 도구/런타임만. 보수적으로.
# 일부러 넣지 않은 것: 'ahnlab safe transaction' 같은 금융보안(수동 처리 대상), 'windows sdk'(개발 도구),
# 'visual studio community/professional'(업무용 앱), JDK/Node 등 개발 런타임 본체.

BUNDLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ahnlab v3", "AhnLab 제조사 번들 보안 제품"),
    ("ahnlab office security", "AhnLab 제조사 번들 보안 제품"),
    ("lg on screen display", "LG 제조사 유틸리티"),
    ("lg quick guide", "LG 제조사 유틸리티"),
    ("lg security guard", "LG 제조사 유틸리티"),
    ("lg smart assistant", "LG 제조사 유틸리티"),
    ("lg update", "LG 제조사 유틸리티"),
    ("microsoft update health tools", "Windows 업데이트 도구"),
    ("visual studio installer", "Visual Studio 설치 관리자(본체 설치 시 자동)"),
    ("application compatibility fix database", "Windows 호환성 데이터"),
    ("redistributable", "런타임/재배포 패키지"),
    ("webview2", "WebView2 런타임"),
    (".net runtime", ".NET 런타임"),
    ("desktop runtime", ".NET 런타임"),
    ("asp.net core", ".NET 런타임"),
    ("shared framework", ".NET 런타임"),
    ("click-to-run", "Office 구성요소(Office 설치 시 자동)"),
    ("update for ", "Windows/제품 업데이트"),
    ("security update", "Windows/제품 업데이트"),
    ("hotfix", "Windows/제품 업데이트"),
    ("nvidia graphics driver", "그래픽 드라이버(신 PC 에서 별도 설치)"),
    ("nvidia physx", "그래픽 드라이버 구성요소"),
    ("nvidia hd audio", "그래픽 드라이버 구성요소"),
    ("intel(r) graphics", "그래픽 드라이버(신 PC 에서 별도 설치)"),
    ("intel(r) chipset", "칩셋 드라이버"),
    ("realtek audio", "오디오 드라이버"),
    ("realtek high definition", "오디오 드라이버"),
)
# KB 번호 업데이트(정규식). BUNDLE_PATTERNS 와 별도로 한 줄.
BUNDLE_REGEX: tuple[tuple[str, str], ...] = ((r"\bkb\d{6,}\b", "Windows 업데이트"),)

# --------------------------------------------------------------------------- 대표 앱 표(winget ID)
# 표시 이름(괄호/버전/아키텍처 제거 후 공백·기호 제거한 형태)과 PackageIdentifier 짝.
# 근거: winget-pkgs 저장소에 등록된 널리 알려진 안정 PackageIdentifier 만 수록. 확신 없는 앱은 넣지 않음.
# 신 PC 에서 `winget search --id <ID> --exact` 로 재확인할 수 있다.


@dataclass(frozen=True)
class KnownApp:
    names: tuple[str, ...]  # 정규화 전 표시 이름(대소문자/기호 무관하게 비교됨)
    winget_id: str
    preinstalled: bool = False  # 새 Windows 11 에 기본 포함(설치 불필요)


KNOWN_APPS: tuple[KnownApp, ...] = (
    KnownApp(("Google Chrome",), "Google.Chrome"),
    KnownApp(("Microsoft Edge",), "Microsoft.Edge", preinstalled=True),
    KnownApp(("Mozilla Firefox",), "Mozilla.Firefox"),
    KnownApp(("Visual Studio Code", "Microsoft Visual Studio Code"), "Microsoft.VisualStudioCode"),
    KnownApp(("7-Zip",), "7zip.7zip"),
    KnownApp(("Notepad++",), "Notepad++.Notepad++"),
    KnownApp(("Zoom", "Zoom Workplace"), "Zoom.Zoom"),
    KnownApp(("Slack",), "SlackTechnologies.Slack"),
    KnownApp(("Git",), "Git.Git"),
    KnownApp(("VLC media player",), "VideoLAN.VLC"),
    KnownApp(("Discord",), "Discord.Discord"),
    KnownApp(("Dropbox",), "Dropbox.Dropbox"),
)

# --------------------------------------------------------------------------- 규칙 경고의 단계 분류
# 규칙(rules/*.json)의 manual_warnings 는 문장 하나라 단계 정보가 없다. 아래 키워드가 있으면 '로그인·인증', 아니면 '데이터/기타'.
LOGIN_KEYWORDS: tuple[str, ...] = ("라이선스", "재로그인", "로그인", "활성화", "인증", "동글")
