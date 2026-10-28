"""패키지 안의 winget 목록 읽기/추출.

패키지 모듈 분해(stage41)의 일부. 공개 이름은 pcmigrate.package 에서 다시 내보낸다.
"""
from __future__ import annotations
import json
import os
import zipfile
from pathlib import Path

from .copier import _backup_path
from .detect import valid_winget_id
from .package_base import PackageError
from .package_savepoint import is_unfinished_part
from .package_verify import verify_package


WINGET_ENTRY = "programs/winget.json"
MAX_WINGET_BYTES = 64 * 1024 * 1024


def _read_winget_raw(path: Path) -> bytes:
    """패키지를 검증하고 programs/winget.json 원문을 읽는다(JSON 으로 읽히는지도 확인)."""
    path = Path(path)
    if is_unfinished_part(path):
        raise PackageError("이 파일(.part)은 아직 완성되지 않은 임시 파일입니다. 완성된 .zip 파일을 선택하세요.")
    manifest, problems = verify_package(path)
    if manifest is None or problems:
        raise PackageError("패키지 검증 실패: " + "; ".join(problems[:3]))
    with zipfile.ZipFile(path) as zf:
        try:
            info = zf.getinfo(WINGET_ENTRY)
        except KeyError:
            raise PackageError("이 패키지에는 winget 목록이 없습니다(구 PC 에서 winget export 가 실패했거나 winget 이 없었습니다). "
                               "pcmigrate checklist --package 로 수동 설치 목록을 확인하세요.") from None
        if info.file_size > MAX_WINGET_BYTES:
            raise PackageError("패키지의 winget 목록이 비정상적으로 큽니다. 안전하지 않은 패키지일 수 있습니다.")
        raw = zf.read(WINGET_ENTRY)
    try:
        json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        raise PackageError("패키지의 winget 목록이 올바른 JSON 이 아닙니다. 쓰지 않았습니다.") from None
    return raw


def list_winget_packages(path: Path) -> list[dict]:
    """묶음 안 winget 목록의 패키지 [{id, version, source}] (winget export 에는 표시 이름이 없어 id 로 구분한다)."""
    data = json.loads(_read_winget_raw(Path(path)).decode("utf-8-sig"))
    out = []
    for src in (data.get("Sources") or []) if isinstance(data, dict) else []:
        name = ((src.get("SourceDetails") or {}).get("Name") or "") if isinstance(src, dict) else ""
        for pkg in (src.get("Packages") or []) if isinstance(src, dict) else []:
            pid = pkg.get("PackageIdentifier", "") if isinstance(pkg, dict) else ""
            if pid:
                out.append({"id": pid, "version": str(pkg.get("Version", "") or ""), "source": name})
    return out


def filter_winget_json(raw: bytes, only_ids: list[str]) -> bytes:
    """winget export JSON 에서 only_ids(대소문자 무시)의 패키지만 남긴 새 JSON 을 만든다.

    최상위 필드와 남은 Source 의 다른 필드는 그대로 두고, 패키지가 하나도 안 남은 Source 는 뺀다.
    id 형식이 틀리거나 묶음에 없는 id 가 있거나 남는 패키지가 없으면 PackageError(아무것도 쓰지 않는다)."""
    ids = []
    for x in only_ids or []:
        if not valid_winget_id(x):
            raise PackageError(f"올바르지 않은 winget id 입니다(영문자·숫자와 . - _ 만 쓸 수 있습니다): {x!r}")
        if x.lower() not in [i.lower() for i in ids]:
            ids.append(x)
    if not ids:
        raise PackageError("꺼낼 프로그램이 하나도 선택되지 않았습니다.")
    want = {i.lower() for i in ids}
    data = json.loads(raw.decode("utf-8-sig"))
    found: set[str] = set()
    sources = []
    for src in (data.get("Sources") or []) if isinstance(data, dict) else []:
        if not isinstance(src, dict):
            continue
        keep = [p for p in (src.get("Packages") or []) if isinstance(p, dict) and str(p.get("PackageIdentifier", "")).lower() in want]
        found.update(str(p["PackageIdentifier"]).lower() for p in keep)
        if keep:
            sources.append({**src, "Packages": keep})
    missing = [i for i in ids if i.lower() not in found]
    if missing:
        raise PackageError("이 묶음의 winget 목록에 없는 id 입니다: " + ", ".join(missing)
                           + ". 'winget-extract 묶음.zip --output 폴더 --list' 로 목록을 확인하세요.")
    return (json.dumps({**data, "Sources": sources}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def extract_winget(path: Path, out_dir: Path, only_ids: list[str] | None = None) -> tuple[Path, Path | None]:
    """패키지(.zip)의 programs/winget.json 을 <out_dir>/winget.json 으로 꺼낸다(신 PC 에서 winget import 에 쓰기 위해).

    패키지를 먼저 검증한다. 같은 이름의 파일이 이미 있으면 덮어쓰지 않고 .bak 으로 옮긴 뒤 새로 쓴다.
    폴더가 같은 이름 자리에 있거나 출력 위치가 파일이면 아무것도 바꾸지 않고 PackageError.
    only_ids 를 주면 그 winget id(대소문자 무시)의 패키지만 담은 winget.json 을 쓴다(None 이면 전체).
    반환: (새 파일 경로, .bak 으로 옮긴 이전 파일 경로 또는 None)."""
    path, out_dir = Path(path), Path(out_dir)
    raw = _read_winget_raw(path)
    if only_ids is not None:  # 선택한 id 만 담은 새 목록(없는 id/형식 오류/전부 제거는 여기서 거부, 아무것도 쓰기 전)
        raw = filter_winget_json(raw, list(only_ids))
    if out_dir.exists() and not out_dir.is_dir():
        raise PackageError(f"출력 위치가 폴더가 아닙니다: {out_dir}. 폴더 경로를 지정하세요(없으면 만들어 줍니다).")
    target = out_dir / "winget.json"
    if target.is_dir():
        raise PackageError(f"같은 이름의 폴더가 있어 쓰지 않았습니다: {target}")
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".pcmigrate-part")
    tmp.write_bytes(raw)
    bak: Path | None = None
    try:
        if target.exists():
            bak = _backup_path(target)
            os.replace(target, bak)
        try:
            os.replace(tmp, target)
        except OSError:
            if bak is not None:
                os.replace(bak, target)  # 되돌린다
            raise
    finally:
        tmp.unlink(missing_ok=True)
    return target, bak


# --------------------------------------------------------------------------- 읽지 못해 제외된 파일만 다시 담기
