"""PyInstaller 로 pcmigrate-gui.exe(창 모드)와 pcmigrate.exe(콘솔)를 만든다.

사용(저장소 루트에서, Windows):
    python packaging/build_exe.py                      # 기본: onedir, 결과는 dist/pcmigrate/ (git 에 올라가지 않음)
    python packaging/build_exe.py --mode onefile       # exe 하나짜리(느리고 백신 오탐이 더 잦음, README 비교표 참고)
    python packaging/build_exe.py --out %TEMP%\\pcm-out  # 결과 위치 지정
    python packaging/build_exe.py --zip                # onedir 결과를 zip 으로도 묶음

안전 원칙
- PyInstaller 는 전역 Python 환경에 설치하지 않는다. 임시 폴더(기본 %TEMP%\\pcm-build)에 새 가상환경을 만들어 거기에만 설치한다.
- 빌드가 끝나면 가상환경과 중간 산출물(build/, *.spec)을 지운다(--keep-work 로 남길 수 있음).
- 만든 exe 의 SHA-256 을 출력한다. 받는 사람이 같은 값인지 확인할 수 있게 함께 전달한다.
- 코드는 정적 분석으로 따라가므로 외부 패키지가 없는 이 프로젝트에서는 rules/·selftransfer/ 폴더(--add-data)와 tkinter 만 신경 쓰면 된다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

# 빌드 재현을 위해 버전을 고정한다(올릴 때는 이 값만 바꾸고 아래 '검증' 절차를 다시 실행).
PYINSTALLER_VERSION = "6.16.0"

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = {  # exe 이름 -> (진입 스크립트, 창 모드 여부)
    "pcmigrate-gui": ("gui_entry.py", False),
    "pcmigrate": ("cli_entry.py", True),
}


SPEC_TEMPLATE = '''# 자동 생성(packaging/build_exe.py). 커밋하지 않는다.
a_gui = Analysis([r"{gui}"], pathex=[r"{src}"], datas=[(r"{rules}", "rules"), (r"{selftransfer}", "selftransfer")], excludes=[], noarchive=False)
a_cli = Analysis([r"{cli}"], pathex=[r"{src}"], datas=[(r"{rules}", "rules"), (r"{selftransfer}", "selftransfer")], excludes=[], noarchive=False)
MERGE((a_gui, "pcmigrate-gui", "pcmigrate-gui"), (a_cli, "pcmigrate", "pcmigrate"))
pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)
exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="pcmigrate-gui", console=False, upx=False)
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="pcmigrate", console=True, upx=False)
coll = COLLECT(exe_gui, a_gui.binaries, a_gui.datas, exe_cli, a_cli.binaries, a_cli.datas, upx=False, name="pcmigrate")
'''


WORK_MARKER = ".pcmigrate-build-work"  # 이 도구가 만든 --work 폴더라는 표식. 표식이 있는 폴더만 다시 쓰고 정리한다.


class WorkDirError(Exception):
    """--work 로 지정한 폴더를 안전하게 쓸 수 없음(지우면 안 되는 폴더일 수 있음)."""


def _is_same_or_inside(child: Path, parent: Path) -> bool:
    c, p = os.path.normcase(str(child)), os.path.normcase(str(parent))
    return c == p or c.startswith(p.rstrip(os.sep + "/") + os.sep)


def prepare_work(work: Path, out: Path | None = None) -> Path:
    """--work 폴더를 검사하고 표식을 만든다. 비어 있지 않은데 표식이 없거나, 지우면 큰일 나는 위치면 WorkDirError.

    이전에는 빌드 뒤에 --work 를 무조건 통째로 지웠기 때문에 `--work` 에 문서 폴더 같은 기존 폴더를 잘못 지정하면 치명적이었다.
    """
    work = Path(work).resolve()
    if work == Path(work.anchor) or work == Path.home().resolve() or work == Path(tempfile.gettempdir()).resolve():
        raise WorkDirError(f"--work 로 쓸 수 없는 위치입니다(드라이브 루트/사용자 폴더/임시 폴더 자체): {work}")
    for guarded, what in ((ROOT, "저장소"), (Path.home().resolve(), "사용자 폴더"), (Path(out).resolve() if out else None, "결과 폴더")):
        if guarded is not None and _is_same_or_inside(guarded, work):
            raise WorkDirError(f"--work 가 {what}({guarded})를 포함하거나 같습니다. 빌드 뒤 정리에서 지워질 수 있어 거부합니다: {work}")
    marker = work / WORK_MARKER
    if work.exists():
        if not work.is_dir():
            raise WorkDirError(f"--work 가 폴더가 아닙니다: {work}")
        if not marker.exists() and any(work.iterdir()):
            raise WorkDirError(f"--work 폴더가 비어 있지 않고 이 도구가 만든 표식({WORK_MARKER})이 없어 쓰지 않습니다: {work}\n"
                               "비어 있는 새 폴더 이름(또는 기본값)을 지정하세요. 아무것도 지우지 않았습니다.")
    work.mkdir(parents=True, exist_ok=True)
    marker.write_text("pcmigrate build work folder. 빌드가 끝나면 이 폴더를 지워도 됩니다.\n", encoding="utf-8")
    return work


def cleanup_work(work: Path) -> None:
    """표식이 있는 폴더만 지운다(표식이 없으면 아무것도 하지 않는다)."""
    work = Path(work).resolve()
    if (work / WORK_MARKER).is_file():
        shutil.rmtree(work, ignore_errors=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def build(args: argparse.Namespace) -> list[Path]:
    work = Path(args.work).resolve()  # main() 이 prepare_work 로 이미 검사했다
    out = Path(args.out).resolve()
    if ROOT in out.parents and out.name not in ("dist",) and not (out == ROOT / "dist" or ROOT / "dist" in out.parents):
        print(f"경고: 결과 위치 {out} 가 저장소 안입니다. 커밋되지 않게 dist/ 밖이면 .gitignore 를 확인하세요.", file=sys.stderr)
    venv = work / "venv"
    if not venv_python(venv).exists():
        run([sys.executable, "-m", "venv", str(venv)])
    py = str(venv_python(venv))
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", f"pyinstaller=={args.pyinstaller_version}"])
    ver = subprocess.run([py, "-m", "PyInstaller", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    print(f"PyInstaller {ver} (고정 버전 {args.pyinstaller_version}), Python {sys.version.split()[0]}")

    outputs: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)
    if args.mode == "onedir":
        # 두 exe 가 한 폴더(dist/pcmigrate/)의 런타임(_internal)을 함께 쓰도록 spec 을 만들어 한 번에 빌드한다(.spec 은 커밋하지 않음).
        spec = work / "spec" / "pcmigrate.spec"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(SPEC_TEMPLATE.format(root=str(ROOT), src=str(ROOT / "src"), rules=str(ROOT / "rules"), selftransfer=str(ROOT / "selftransfer"),
                                             gui=str(ROOT / "packaging" / "gui_entry.py"), cli=str(ROOT / "packaging" / "cli_entry.py")),
                        encoding="utf-8")
        t0 = time.time()
        run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(out), "--workpath", str(work / "build"), str(spec)])
        print(f"  빌드 pcmigrate(onedir): {time.time() - t0:.0f}초", flush=True)
        return [out / "pcmigrate" / "pcmigrate-gui.exe", out / "pcmigrate" / "pcmigrate.exe"]
    for name, (entry, console) in ENTRIES.items():
        cmd = [py, "-m", "PyInstaller", "--noconfirm", "--clean", "--noupx",
               "--name", name,
               "--paths", str(ROOT / "src"),
               "--add-data", f"{ROOT / 'rules'}{os.pathsep}rules",  # rules.default_rules_dir() 이 _MEIPASS/rules 를 찾는다
               "--add-data", f"{ROOT / 'selftransfer'}{os.pathsep}selftransfer",  # selftransfer.default_dir() 이 _MEIPASS/selftransfer 를 찾는다
               "--distpath", str(out), "--workpath", str(work / "build"), "--specpath", str(work / "spec"),
               "--console" if console else "--noconsole", "--onefile"]
        # tkinter(Tcl/Tk 데이터 포함)는 PyInstaller 기본 hook 이 모은다. 외부 패키지는 쓰지 않는다.
        t0 = time.time()
        run(cmd + [str(ROOT / "packaging" / entry)])
        print(f"  빌드 {name}: {time.time() - t0:.0f}초", flush=True)
        outputs.append(out / (name + ".exe"))
    return outputs


def smoke_test(cli_exe: Path, run=subprocess.run) -> tuple[bool, str]:
    """만든 콘솔 exe 를 실제로 실행해 본다: --version 과 check-rules(번들된 rules/*.json 이 읽히는지). (성공 여부, 메시지)."""
    r = run([str(cli_exe), "--version"], capture_output=True, text=True, timeout=120)
    if r.returncode:
        return False, f"{cli_exe.name} --version 실패(rc={r.returncode})"
    msg = f"{cli_exe.name} --version -> {r.stdout.strip()}"
    r = run([str(cli_exe), "check-rules"], capture_output=True, text=True, timeout=120)
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
        n = int(out["rules"])
        n_st = int(out.get("self_transfer", 1))  # 구 출력(키 없음)은 통과로 본다
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        n, n_st = 0, 0
    if r.returncode or n < 1:
        return False, msg + f"\n규칙 파일이 exe 에 들어 있지 않거나 읽을 수 없습니다(check-rules rc={r.returncode}, 규칙 {n}개)"
    if n_st < 1:
        return False, msg + "\n자체 이전 필요 목록(selftransfer/*.json)이 exe 에 들어 있지 않거나 비어 있습니다"
    return True, msg + f", 번들 규칙 {n}개, 자체 이전 목록 {n_st}개 확인"


def smoke_test_gui(gui_exe: Path, run=subprocess.run) -> tuple[bool, str]:
    """창 모드 exe 는 --version 을 메시지 상자로 알리므로, 상자를 띄우지 않는 PCMIGRATE_NO_DIALOG=1 로 실행해 곧바로 끝나는지만 본다."""
    env = dict(os.environ, PCMIGRATE_NO_DIALOG="1")
    r = run([str(gui_exe), "--version"], capture_output=True, text=True, timeout=120, env=env)
    if r.returncode:
        return False, f"{gui_exe.name} --version 실패(rc={r.returncode})"
    return True, f"{gui_exe.name} --version 정상 종료(창을 열지 않음)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PyInstaller 로 pcmigrate exe 만들기")
    ap.add_argument("--mode", choices=("onedir", "onefile"), default="onedir",
                    help="onedir(기본): 폴더째 배포, 시작이 빠르고 백신 오탐이 적음 / onefile: exe 하나(시작 때마다 임시 폴더에 풀림)")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="결과 폴더(기본 dist/, .gitignore 대상)")
    ap.add_argument("--work", default=str(Path(tempfile.gettempdir()) / "pcm-build"), help="가상환경/중간 산출물 위치(기본 %%TEMP%%\\pcm-build)")
    ap.add_argument("--pyinstaller-version", default=PYINSTALLER_VERSION)
    ap.add_argument("--keep-work", action="store_true", help="빌드 후 가상환경/중간 산출물을 지우지 않음")
    ap.add_argument("--zip", action="store_true", help="onedir 결과를 pcmigrate-win.zip 으로도 묶음")
    ap.add_argument("--no-smoke", action="store_true", help="빌드 후 pcmigrate.exe 실행 확인(--version, check-rules)을 건너뜀")
    args = ap.parse_args(argv)
    if os.name != "nt":
        print("이 스크립트는 Windows exe 용입니다(다른 OS 에서는 해당 OS 용 실행 파일이 만들어집니다).", file=sys.stderr)
    try:
        work = prepare_work(Path(args.work), Path(args.out))
    except WorkDirError as e:
        print(f"[거부] {e}", file=sys.stderr)
        return 2
    args.work = str(work)
    try:
        exes = build(args)
        print("\n=== 결과 ===")
        for exe in exes:
            if not exe.exists():
                print(f"[실패] 만들어지지 않음: {exe}")
                return 1
            size = exe.stat().st_size
            print(f"{exe.name}: {size / 1024 / 1024:.1f}MB  SHA-256 {sha256(exe)}")
        if args.mode == "onedir":
            folder = exes[0].parent
            print(f"  폴더 {folder.name}: {folder_size(folder) / 1024 / 1024:.1f}MB (exe 두 개와 _internal 을 포함한 이 폴더를 통째로 배포)")
        if not args.no_smoke:
            cli = [e for e in exes if e.name.startswith("pcmigrate.")][0]
            ok, msg = smoke_test(cli)
            print(f"smoke: {msg}")
            if not ok:
                return 1
            gui = [e for e in exes if e.name.startswith("pcmigrate-gui.")]
            if gui:
                ok, msg = smoke_test_gui(gui[0])
                print(f"smoke: {msg}")
                if not ok:
                    return 1
        if args.zip and args.mode == "onedir":
            zpath = Path(args.out) / "pcmigrate-win.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in exes[0].parent.rglob("*"):
                    if p.is_file():
                        z.write(p, p.relative_to(Path(args.out)))
            print(f"zip: {zpath} {zpath.stat().st_size / 1024 / 1024:.1f}MB  SHA-256 {sha256(zpath)}")
        print("\n받는 사람에게 위 SHA-256 값을 함께 알려 주세요(README 'exe 사용법' 참고). 서명되지 않은 exe 는 SmartScreen 경고가 뜹니다.")
        return 0
    finally:
        if not args.keep_work:
            cleanup_work(work)


if __name__ == "__main__":
    raise SystemExit(main())
