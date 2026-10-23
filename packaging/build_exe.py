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
- 코드는 정적 분석으로 따라가므로 외부 패키지가 없는 이 프로젝트에서는 rules/ 폴더(--add-data)와 tkinter 만 신경 쓰면 된다.
"""
from __future__ import annotations

import argparse
import hashlib
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
a_gui = Analysis([r"{gui}"], pathex=[r"{src}"], datas=[(r"{rules}", "rules")], excludes=[], noarchive=False)
a_cli = Analysis([r"{cli}"], pathex=[r"{src}"], datas=[(r"{rules}", "rules")], excludes=[], noarchive=False)
MERGE((a_gui, "pcmigrate-gui", "pcmigrate-gui"), (a_cli, "pcmigrate", "pcmigrate"))
pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)
exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="pcmigrate-gui", console=False, upx=False)
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="pcmigrate", console=True, upx=False)
coll = COLLECT(exe_gui, a_gui.binaries, a_gui.datas, exe_cli, a_cli.binaries, a_cli.datas, upx=False, name="pcmigrate")
'''


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
    work = Path(args.work).resolve()
    out = Path(args.out).resolve()
    if ROOT in out.parents and out.name not in ("dist",) and not (out == ROOT / "dist" or ROOT / "dist" in out.parents):
        print(f"경고: 결과 위치 {out} 가 저장소 안입니다. 커밋되지 않게 dist/ 밖이면 .gitignore 를 확인하세요.", file=sys.stderr)
    venv = work / "venv"
    work.mkdir(parents=True, exist_ok=True)
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
        spec.write_text(SPEC_TEMPLATE.format(root=str(ROOT), src=str(ROOT / "src"), rules=str(ROOT / "rules"),
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
               "--distpath", str(out), "--workpath", str(work / "build"), "--specpath", str(work / "spec"),
               "--console" if console else "--noconsole", "--onefile"]
        # tkinter(Tcl/Tk 데이터 포함)는 PyInstaller 기본 hook 이 모은다. 외부 패키지는 쓰지 않는다.
        t0 = time.time()
        run(cmd + [str(ROOT / "packaging" / entry)])
        print(f"  빌드 {name}: {time.time() - t0:.0f}초", flush=True)
        outputs.append(out / (name + ".exe"))
    return outputs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PyInstaller 로 pcmigrate exe 만들기")
    ap.add_argument("--mode", choices=("onedir", "onefile"), default="onedir",
                    help="onedir(기본): 폴더째 배포, 시작이 빠르고 백신 오탐이 적음 / onefile: exe 하나(시작 때마다 임시 폴더에 풀림)")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="결과 폴더(기본 dist/, .gitignore 대상)")
    ap.add_argument("--work", default=str(Path(tempfile.gettempdir()) / "pcm-build"), help="가상환경/중간 산출물 위치(기본 %%TEMP%%\\pcm-build)")
    ap.add_argument("--pyinstaller-version", default=PYINSTALLER_VERSION)
    ap.add_argument("--keep-work", action="store_true", help="빌드 후 가상환경/중간 산출물을 지우지 않음")
    ap.add_argument("--zip", action="store_true", help="onedir 결과를 pcmigrate-win.zip 으로도 묶음")
    ap.add_argument("--no-smoke", action="store_true", help="빌드 후 pcmigrate.exe --version 확인을 건너뜀")
    args = ap.parse_args(argv)
    if os.name != "nt":
        print("이 스크립트는 Windows exe 용입니다(다른 OS 에서는 해당 OS 용 실행 파일이 만들어집니다).", file=sys.stderr)
    work = Path(args.work)
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
            r = subprocess.run([str(cli), "--version"], capture_output=True, text=True, timeout=120)
            print(f"smoke: {cli.name} --version -> rc={r.returncode} {r.stdout.strip()}")
            if r.returncode:
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
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
