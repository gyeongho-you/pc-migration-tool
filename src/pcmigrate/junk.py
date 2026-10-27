"""전송에서 뺄 수 있는 '정크'(다시 만들어지는 임시/잠금/캐시 파일) 목록과 적용 도구.

기본은 아무것도 제외하지 않는다. transfer 의 --exclude-junk(api exclude_junk)를 켠 때만 쓴다.
목록은 이 파일 한 곳에서만 정한다. robocopy 에는 /XF(파일 이름 패턴), /XD(폴더 이름 패턴)로 넘기고,
같은 규칙으로 원본 조사 결과에서도 빼서 '복사 안 한 파일을 대상에서 찾는' 검사 실패가 생기지 않게 한다.

근거 등급: A = 해당 프로그램/Windows 가 매번 다시 만들고 내용에 사용자 작업이 없다고 널리 알려진 것,
           B = 일반 관행(드물게 사용자가 직접 만든 파일일 수 있어 목록에서 근거를 밝힌다).
의도적으로 넣지 않은 것(사용자 작업이 들어 있을 수 있음):
  *.bak(AutoCAD 도면 백업 등 사용자 백업), *.sv$(AutoCAD 자동 저장 = 저장 못 한 작업의 유일한 사본일 수 있음),
  .git 폴더(버전 이력), *.log, *.dmp, 이름만 'backup' 인 폴더.
옵션 그룹 backup-copies(기본 꺼짐): Revit 저장 백업 사본(이름.0001.rvt 등), Revit *_backup 폴더, Rhino *.3dmbak, SketchUp *.skb.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

FILE, DIR = "file", "dir"
OPTIONAL_GROUP = "backup-copies"


@dataclass(frozen=True)
class JunkRule:
    group: str
    kind: str  # FILE | DIR
    pattern: str  # 대소문자 무시 glob(robocopy /XF /XD 와 같은 모양)
    grade: str  # A | B
    reason: str


JUNK_RULES: tuple[JunkRule, ...] = (
    # --- Windows 탐색기 메타 파일
    JunkRule("os-meta", FILE, "Thumbs.db", "A", "탐색기 썸네일 캐시(폴더를 열면 다시 만들어짐)"),
    JunkRule("os-meta", FILE, "ehthumbs.db", "A", "탐색기 썸네일 캐시(미디어 센터)"),
    JunkRule("os-meta", FILE, "desktop.ini", "B", "폴더 아이콘/표시 이름 설정(없어도 폴더 내용은 같음, 직접 지정한 폴더 아이콘은 사라질 수 있음)"),
    JunkRule("os-meta", DIR, "$RECYCLE.BIN", "A", "휴지통(드라이브마다 자동 생성)"),
    JunkRule("os-meta", DIR, "System Volume Information", "A", "시스템 복원 지점(접근 불가, 복사 의미 없음)"),
    # --- Office 잠금/임시
    JunkRule("office-lock", FILE, "~$*", "A", "Office 가 문서를 여는 동안 만드는 잠금 파일(작성자 이름만 들어 있음)"),
    JunkRule("office-lock", FILE, "~WRL*.tmp", "A", "Word 임시 복구 파일"),
    # --- 일반 임시
    JunkRule("temp", FILE, "*.tmp", "B", "프로그램이 만드는 임시 파일(드물게 사용자가 이름을 붙인 파일일 수 있음)"),
    # --- CAD 임시/잠금 (자동 저장 *.sv$ 와 백업 *.bak 은 사용자 작업일 수 있어 넣지 않음)
    JunkRule("cad-temp", FILE, "*.dwl", "A", "AutoCAD 도면 잠금 파일(도면을 연 사용자/PC 이름만 들어 있음)"),
    JunkRule("cad-temp", FILE, "*.dwl2", "A", "AutoCAD 도면 잠금 파일(확장)"),
    JunkRule("cad-temp", FILE, "*.ac$", "A", "AutoCAD 임시 파일"),
    JunkRule("cad-temp", FILE, "*.rhl", "B", "Rhino 잠금 파일"),
    # --- 다른 OS 가 만든 메타
    JunkRule("other-os", FILE, ".DS_Store", "A", "macOS 폴더 표시 정보"),
    JunkRule("other-os", DIR, "__MACOSX", "B", "macOS 가 압축 풀 때 만드는 리소스 폴더"),
    # --- 다시 설치/생성되는 의존성
    JunkRule("deps", DIR, "node_modules", "B", "npm 이 package.json 으로 다시 만드는 폴더"),
    # --- 옵션(기본 꺼짐): 저장 백업 사본. 사용자가 복구용으로 남긴 것일 수 있어 따로 고른다.
    JunkRule(OPTIONAL_GROUP, FILE, "*.0???.rvt", "B", "Revit 저장 때마다 남는 백업 사본(이름.0001.rvt)"),
    JunkRule(OPTIONAL_GROUP, FILE, "*.0???.rfa", "B", "Revit 패밀리 백업 사본(이름.0001.rfa)"),
    JunkRule(OPTIONAL_GROUP, FILE, "*.0???.rte", "B", "Revit 템플릿 백업 사본(이름.0001.rte)"),
    JunkRule(OPTIONAL_GROUP, DIR, "*_backup", "B", "Revit 작업 공유 파일 옆 *_backup 폴더(이름만으로 판단하므로 옵션)"),
    JunkRule(OPTIONAL_GROUP, FILE, "*.3dmbak", "B", "Rhino 백업 사본"),
    JunkRule(OPTIONAL_GROUP, FILE, "*.skb", "B", "SketchUp 백업 사본"),
)
DEFAULT_GROUPS: tuple[str, ...] = tuple(dict.fromkeys(r.group for r in JUNK_RULES if r.group != OPTIONAL_GROUP))
MAX_EXAMPLES = 20


@dataclass(frozen=True)
class JunkSpec:
    """어떤 규칙을 적용할지. rules 는 JUNK_RULES 의 부분집합."""
    rules: tuple[JunkRule, ...]

    @property
    def xf(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.pattern for r in self.rules if r.kind == FILE))

    @property
    def xd(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.pattern for r in self.rules if r.kind == DIR))

    def file_group(self, name: str) -> str | None:
        low = name.lower()
        for r in self.rules:
            if r.kind == FILE and fnmatch.fnmatchcase(low, r.pattern.lower()):
                return r.group
        return None

    def dir_group(self, name: str) -> str | None:
        low = name.lower()
        for r in self.rules:
            if r.kind == DIR and fnmatch.fnmatchcase(low, r.pattern.lower()):
                return r.group
        return None

    def skip_dir(self, name: str) -> bool:
        return self.dir_group(name) is not None

    def classify_rel(self, rel: str, is_dir: bool = False) -> str | None:
        """원본 루트 기준 상대 경로(posix)가 제외 대상이면 그룹 이름. 폴더 규칙이 먼저."""
        parts = rel.split("/")
        for p in parts if is_dir else parts[:-1]:
            g = self.dir_group(p)
            if g:
                return g
        return None if is_dir else self.file_group(parts[-1])


def make_spec(include_backup_copies: bool = False) -> JunkSpec:
    """기본 그룹(+ 옵션이면 backup-copies)의 규칙으로 JunkSpec 을 만든다."""
    return JunkSpec(tuple(r for r in JUNK_RULES if r.group != OPTIONAL_GROUP or include_backup_copies))


@dataclass
class JunkExcluded:
    files: int = 0
    bytes: int = 0
    by_group: dict = field(default_factory=dict)  # 그룹 -> {"files": n, "bytes": b}
    examples: list = field(default_factory=list)  # 앞 MAX_EXAMPLES 개의 상대 경로

    def add(self, group: str, rel: str, size: int) -> None:
        self.files += 1
        self.bytes += size
        g = self.by_group.setdefault(group, {"files": 0, "bytes": 0})
        g["files"] += 1
        g["bytes"] += size
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append(rel)

    def as_dict(self, spec: JunkSpec | None = None) -> dict:
        d = {"files": self.files, "bytes": self.bytes, "by_group": self.by_group, "examples": list(self.examples)}
        if spec is not None:
            d["xf"], d["xd"] = list(spec.xf), list(spec.xd)
            d["backup_copies"] = any(r.group == OPTIONAL_GROUP for r in spec.rules)
        return d


def apply_to_scan(scan, spec: JunkSpec) -> JunkExcluded:
    """원본 조사(ScanInfo) 결과에서 제외 대상을 빼고(제자리) 합계를 다시 계산한다. 제외한 내역을 돌려준다.

    robocopy /XF /XD 와 같은 규칙이므로 복사되지 않는 파일을 '대상에 없다'고 실패 처리하지 않는다.
    """
    out = JunkExcluded()
    kept = []
    for e in scan.entries:
        g = spec.classify_rel(e[0])
        if g:
            out.add(g, e[0], e[1])
        else:
            kept.append(e)
    kept_cloud = []
    for e in scan.cloud:
        g = spec.classify_rel(e[0])
        if g:
            out.add(g, e[0], 0)  # 내려받지 않은 파일이라 이 PC 의 용량으로는 세지 않는다
        else:
            kept_cloud.append(e)
    scan.entries, scan.cloud = kept, kept_cloud
    scan.errors = [x for x in scan.errors if x[0] == "." or not spec.classify_rel(x[0], is_dir=True)
                   and not spec.classify_rel(x[0])]
    scan.total_bytes = sum(e[1] for e in kept)
    scan.cloud_bytes = sum(e[1] for e in kept_cloud)
    scan.max_size = max((e[1] for e in kept), default=0)
    layout: dict = {}
    for rel, size, _m in kept:
        top = rel.split("/", 1)[0] if "/" in rel else "(최상위 파일)"
        agg = layout.setdefault(top, [0, 0])
        agg[0] += 1
        agg[1] += size
    scan._layout = layout
    return out


def summary_line(meta: dict) -> list[str]:
    """리포트 dict 의 junk_excluded 를 한 줄 요약으로(없으면 빈 목록)."""
    j = meta.get("junk_excluded")
    if not j:
        return []
    from .progress import fmt_bytes
    if not j.get("files"):
        return ["- 정크 제외 옵션을 켰지만 제외할 파일이 없었습니다"]
    groups = ", ".join(f"{g} {v['files']}개" for g, v in j["by_group"].items())
    return [f"- 정크 제외: {j['files']}개({fmt_bytes(j['bytes'])}) 복사하지 않음 ({groups})"]
