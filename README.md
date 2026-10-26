# pc-migration-tool (PC 이전 도우미)

구 Windows PC의 업무용(건축 CAD/BIM) 환경을 새 PC로 안전하게 옮기는 Python CLI입니다.
원본은 읽기만 하고, 복사 후 SHA-256으로 검증하며, 덮어쓸 파일은 `.bak`으로 백업합니다.
설계 방침과 요구사항은 [CLAUDE.md](CLAUDE.md)를 보세요.

## 내려받기 (Python 없이 쓰려면)

Python 이 없는 PC 에서는 [릴리스 페이지](https://github.com/gyeongho-you/pc-migration-tool/releases)에서 받으세요.

| 파일 | 설명 |
|---|---|
| `pcmigrate-win.zip` | exe 묶음. 압축을 풀어 **폴더째** 쓰세요(`pcmigrate-gui.exe` 더블클릭). `_internal` 폴더를 지우거나 exe 만 따로 옮기지 마세요 |
| `field-guide.html` | 현장 안내(준비물, 순서, 명령, 막힐 때, 공식 링크). 인터넷 없이 브라우저로 열립니다 |
| `field-guide.md` | 같은 내용의 텍스트판(메모장이나 문서 앱으로 열림) |
| `SHA256SUMS.txt` | 위 파일들의 SHA-256. 받은 파일이 맞는지 `certutil -hashfile 파일 SHA256` 또는 `Get-FileHash 파일` 결과와 비교하세요 |

- 현장 안내 문서는 저장소의 [`docs/`](docs/) 폴더에도 있습니다([field-guide.html](docs/field-guide.html), [field-guide.md](docs/field-guide.md)).
- exe 는 **코드 서명이 없습니다.** 처음 실행하면 "Windows의 PC 보호" 창이 뜰 수 있으니 SHA-256 을 확인한 뒤 "추가 정보 → 실행" 을 누르세요.
- 이 도구는 실제 건축/토목 PC 에서 아직 검증되지 않은 부분이 많습니다(아래 "검증하지 못한 것" 참고).

## 설치

Python 3.10 이상이 필요하고 외부 패키지는 없습니다(대용량 전송은 Windows 내장 `robocopy`).

```
pip install -e .            # 개발용 설치
```

설치하지 않고 쓰려면 저장소 루트에서 `PYTHONPATH=src`(PowerShell: `$env:PYTHONPATH="src"`)를 지정합니다.

## 실행

```
python -m pcmigrate <명령> ...
```

`pcmigrate` 명령은 pip의 스크립트 폴더가 PATH에 없으면 "찾을 수 없다"고 나옵니다.
그럴 때는 위처럼 `python -m pcmigrate`를 쓰세요. 도움말: `python -m pcmigrate --help`

## 그래픽 화면(GUI)

명령어 없이 마우스로 쓸 수 있는 마법사입니다(Python 기본 `tkinter` 만 사용, 추가 설치 없음).

```
python -m pcmigrate.gui          # 또는: python -m pcmigrate gui
```

설치하지 않았다면 저장소 폴더에서 `PYTHONPATH=src` 와 함께 실행합니다. 더블클릭으로 열려면 아래 내용을 `pcmigrate-gui.bat`
로 저장해 두세요(경로는 실제 저장소 위치로 바꿉니다. 검은 창 없이 열려면 `python` 대신 `pythonw`).

```
@echo off
cd /d C:\path\to\pc-migration-tool
set PYTHONPATH=src
python -m pcmigrate.gui
pause
```

시작 화면에서 세 가지 중 하나를 고릅니다.

| 선택 | 화면 순서 | 하는 일 |
|---|---|---|
| 구 PC(내보내기) | 스캔 → 규칙 없는 프로그램 확인 → 체크리스트 → 설정 패키지 내보내기 | 설치 프로그램과 설정을 확인하고 `.zip` 패키지를 만듭니다 |
| 신 PC(가져오기) | 패키지 선택 → 검증과 미리보기 → 복원 확정 → 결과 | 미리보기를 보고 확인한 뒤에만 복원합니다 |
| 대용량 데이터 복사 | 한 화면(맨 위에서 방식 선택) | **외장 드라이브 / 랜선(네트워크) / 같은 PC** 중 고르고, 1단계(구 PC)·2단계(새 PC)로 복사하고 SHA-256 으로 검증합니다(미리보기 → 복사, 진행률·남은 시간, 취소/이어받기) |

안전 장치는 명령줄과 같습니다.
- 원본은 읽기만 합니다. 쓰기 전에는 항상 "어디에 무엇이 써지는지" 확인 창이 뜹니다.
- 규칙 없는 프로그램은 자동으로 포함되지 않고, 직접 체크한 것만 들어갑니다. 비밀 정보 가능성이 있는 항목(붉게 표시)은
  "비밀 정보가 포함될 수 있음을 이해했습니다"에 동의해야 선택할 수 있습니다.
- 복원은 "미리보기를 확인했습니다" 체크 + 확인 창에서 "예"를 눌러야 실행되고, 규칙 없이 선택 포함된 항목이 있으면 별도 동의가 필요합니다.
- 대용량 복사는 미리보기(쓰기 없음)를 먼저 실행해야 복사 버튼이 열리고, OneDrive 클라우드 전용 파일 포함은 기본 꺼짐입니다.
- 라이선스는 복원되지 않습니다.

오래 걸리는 작업(스캔, 탐지, 내보내기, 복원, 복사)은 백그라운드에서 돌아 화면이 멈추지 않으며, 규칙 없는 프로그램 탐지와 대용량 복사는
아래쪽 "취소" 버튼으로 멈출 수 있습니다(복사는 나중에 이어받기). 오류는 한국어 대화상자로 나오고 자세한 내용은 "상세 보기"에만 있습니다.
체크리스트 저장 파일에는 개인 정보가 들어 있으니 커밋/공유하지 마세요.

### 작업 현황 카드와 전체 단계

- 창 맨 위에 **"전체 단계 2/4"** 처럼 지금이 흐름의 몇 번째 화면인지 크게 표시됩니다(구 PC/새 PC 4단계, 외장·랜선 복사는 2단계).
- 오래 걸리는 작업 화면(스캔, 규칙 없는 프로그램 찾기, 해야 할 일 목록, 패키지 만들기, 패키지 검사, 복원, 대용량 복사/검사)에는
  제목 바로 아래에 **작업 현황 카드**가 나옵니다.
  1. 큰 글씨 한 줄: 지금 하는 일(예: `② 복사 중 — 12,431 / 50,003개 파일 (24%)`)
  2. 단계 표시줄: `① 조사 → ② 복사 → ③ 검사 → ④ 완료`(끝난 단계는 ✔, 지금 단계는 강조, 작업마다 자기 단계가 나옵니다)
  3. 진행 막대와 지표: 파일 완료/전체, 용량, 속도, 경과 시간, 남은 시간(추정할 수 없으면 "계산 중")
  4. 지금 처리 중인 폴더/파일 한 줄(긴 경로는 줄여 보이고 마우스를 올리면 전체 경로)
  5. 카운터 칩: `✔ 성공` / `↷ 건너뜀` / `⚠ 읽지 못함(사용 중)` / `✖ 실패` — 0 이 아니면 색과 아이콘으로 강조되고, 누르면 해당 목록이 열립니다
  6. "문제가 있나요?" 한 줄 요약(없으면 "지금까지 문제 없음")과 마지막 메시지 한 줄
- 작업이 끝나도 결과 요약(`④ 완료 — …`)이 카드에 남아 있어 한눈에 확인할 수 있습니다.

### 오류 로그

예기치 않은 오류가 나면 전체 내용(traceback 포함)이 아래 파일에 한 건씩 덧붙여집니다.

```
%LOCALAPPDATA%\pcmigrate\logs\errors-YYYYMMDD.log      (쓸 수 없으면 %TEMP%\pcmigrate-logs\)
```

- 한 건에는 시각, 도구 버전, 실행한 명령/화면 이름, 예외 종류와 메시지, 전체 traceback 이 들어갑니다.
  파일은 하루 단위이고 5MB 를 넘으면 `errors-YYYYMMDD.1.log` 로 이어지며, 최근 10개 파일만 남깁니다.
- 설치 프로그램 목록과 파일 내용은 기록하지 않습니다. **오류 로그에는 파일 경로가 들어 있으니 다른 사람에게 보내기 전에 확인하세요.**
- GUI: 오류 대화상자의 **로그 폴더 열기 / 오류 내용 복사** 버튼, 메인 창 아래쪽의 **오류 로그 열기** 버튼으로 바로 열 수 있습니다.
  (`%TEMP%\pcmigrate-gui.log` 에는 계속 이벤트 이름만 남습니다.)
- 명령줄: `pcmigrate logs` 가 로그 폴더 경로와 최근 오류 한 건의 요약을 보여 주고, `pcmigrate logs --open` 은 탐색기로 엽니다.
  예상하지 못한 오류는 traceback 대신 "예상하지 못한 오류가 났습니다. 자세한 내용은 … 에 기록했습니다." 두 줄과 종료 코드 1 로 끝나며,
  Ctrl+C 는 "중단했습니다." 와 종료 코드 130, 잘못된 옵션(argparse 오류)은 종료 코드 2 입니다.

화면 사용 팁
- 각 화면 오른쪽 위의 **`?`** 버튼(또는 F1 키)을 누르면 "무엇을 하는 화면인지 / 안전한지 / 다음 단계"를 짧게 보여 줍니다.
- 버튼이 눌러지지 않을 때는 버튼 옆에 이유가 적혀 있습니다(예: "미리보기를 먼저 실행해 주세요").
- 키보드: Tab 으로 이동, Enter 로 기본 버튼 실행, Esc 로 이전 화면(작업 중에는 취소).
- 표의 긴 경로는 줄여서 보이며, 마우스를 올리면 전체 경로가 나옵니다. 열 제목을 누르면 정렬됩니다.
- 해야 할 일 목록은 항목에 체크하면서 진행할 수 있고(저장하면 체크한 항목이 `[x]` 로 남음), "관련 없는 경고도 모두 보기"로 숨긴 경고를 볼 수 있습니다.
- 화면의 모든 한국어 문구는 `src/pcmigrate/gui/texts.py` 한 곳에 있습니다(문구를 고칠 때 이 파일만 수정).

Python 없이 exe 로 실행하는 방법과 빌드 방법은 아래 "exe 로 실행하기"를, 1TB 이전은 "1TB 이전 가이드"를 보세요.

## 전체 이전 순서

구 PC 에서 새 PC 로 옮기는 전체 순서입니다. GUI 에서는 모든 화면 오른쪽 위의 **`전체 이전 순서 보기`** 버튼(또는 `?` 도움말 창의 같은 버튼)으로 체크하면서 볼 수 있습니다. 항목마다 **도구가 도와주는 일**과 **직접 하셔야 하는 일**을 구분했습니다. 이 절의 내용은 `src/pcmigrate/gui/texts.py` 의 `ROADMAP_*` 상수에서 만들어지며(테스트가 일치를 확인합니다), 고칠 때는 그 상수를 고친 뒤 README 의 표시 구간을 다시 만드세요.

<!-- ROADMAP:BEGIN -->
**구 PC 에서 (떠나기 전)**

1. ☐ **모든 프로그램을 종료하고 전원을 연결합니다** — _직접 하셔야 함_  
   CAD·BIM 같은 파일을 쓰는 프로그램을 모두 닫으세요. 노트북은 전원을 연결하세요. 사용 중인 파일은 복사와 검사에 실패합니다.
2. ☐ **프로그램 조사(스캔)와 해야 할 일 목록 저장** — _도구가 도와줌_  
   '구 PC' 1~3단계에서 설치된 프로그램을 조사하고, 새 PC 에서 할 일 목록을 파일로 저장해 둡니다.
3. ☐ **규칙 없는 프로그램 확인·선택** — _도구가 도와주고 직접 확인_  
   도구가 규칙에 없는 프로그램의 설정 폴더 후보를 찾아 줍니다. 패키지에 넣을 항목은 직접 고르셔야 합니다.
4. ☐ **설정 패키지 내보내기** — _도구가 도와줌_  
   '구 PC' 4단계에서 설정과 프로그램 목록을 .zip 패키지로 만듭니다. 라이선스는 들어가지 않습니다.
5. ☐ **데이터 복사 방식 선택(외장/랜선)과 복사** — _도구가 도와주고 직접 확인_  
   '대용량 복사'에서 방식을 고르고 미리보기 → 복사를 진행합니다. 외장 드라이브(NTFS)나 랜선·공유 폴더 준비는 직접 하셔야 합니다.
6. ☐ **모든 검증이 끝나기 전까지 구 PC 를 삭제하거나 포맷하지 않습니다** — _직접 하셔야 함_  
   새 PC 에서 검사 결과가 '실패 0'이고 프로그램과 데이터에 문제가 없음을 확인할 때까지 구 PC 의 파일을 지우거나 포맷하지 마세요. 이 도구는 원본을 지우지 않습니다.

**새 PC 에서 (도착해서)**

1. ☐ **윈도우 업데이트와 장치 드라이버를 먼저 맞춥니다** — _직접 하셔야 함_  
   Windows 업데이트를 끝내고 그래픽카드·플로터·동글 등 장치 드라이버를 설치하세요.
2. ☐ **필요한 프로그램을 새로 설치하고 라이선스를 인증합니다** — _도구가 도와주고 직접 확인_  
   도구의 해야 할 일 목록이 설치할 프로그램을 알려 줍니다. 설치, 로그인·인증(동글 드라이버 포함)은 직접 하셔야 하며, 가능하면 각 프로그램을 한 번 실행했다가 종료하세요.
3. ☐ **데이터를 복사하고 검사합니다** — _도구가 도와줌_  
   '대용량 복사'로 데이터를 옮기고, 랜선이면 2단계 '검사 시작'으로 목록 파일과 비교합니다. 결과가 '실패 0'인지 확인하세요.
4. ☐ **모든 프로그램을 종료하고 설정 패키지를 미리보기로 확인한 뒤 복원합니다** — _도구가 도와줌_  
   '신 PC' 화면에서 패키지를 고르고 미리보기(실제로 바꾸지 않음)를 확인한 뒤 복원합니다. 덮어쓰는 파일은 백업 파일(.bak)로 남습니다.
5. ☐ **각 프로그램을 열어 설정·템플릿·글꼴·플로터·참조 경로를 점검합니다** — _직접 하셔야 함_  
   자동으로 옮겨지지 않은 항목이 있을 수 있습니다. 라이선스 상태도 프로그램마다 확인하세요.
6. ☐ **폴더 위치(드라이브 문자)가 구 PC 와 같은지 확인합니다** — _도구가 도와주고 직접 확인_  
   도면의 외부 참조(xref)와 데이터 단축은 절대 경로를 씁니다. 도구가 위치가 다르면 경고해 주지만, 도면을 열어 참조가 이어지는지는 직접 확인하셔야 합니다.
<!-- ROADMAP:END -->

## exe 로 실행하기 (Python 없이)

구 PC 에 Python 이 없어도 됩니다. 빌드한 폴더(`pcmigrate\`)를 USB 나 공유 폴더로 옮겨서 실행하세요.

```
pcmigrate\
  pcmigrate-gui.exe     ← 더블클릭하면 마법사 화면이 열립니다(콘솔 창 없음)
  pcmigrate.exe         ← 명령 프롬프트/PowerShell 에서 쓰는 명령줄 버전
  _internal\            ← 실행에 필요한 파일(지우거나 exe 만 따로 옮기지 마세요)
```

- **관리자 권한이 필요 없습니다.** 일반 사용자로 실행하세요(`asInvoker`). 바탕화면, 한글/공백이 들어간 폴더, USB 드라이브 어디에서든 실행됩니다.
- exe 에는 `rules\` 와 Python 실행기, tkinter 가 모두 들어 있습니다(실제 Python 이 PATH 에 없는 환경에서 `--version`, `scan`, `checklist`, `discover`, `transfer`, `manifest`, `verify`, GUI 실행을 확인했습니다. 아래 "검증한 범위" 참고).
- 명령줄 예: `pcmigrate.exe --version`, `pcmigrate.exe transfer D:\프로젝트 E:\이전 --dry-run`

### 처음 실행할 때 (SmartScreen)

서명하지 않은 exe 라서 처음 실행하면 파란 "Windows의 PC 보호" 창이 뜰 수 있습니다. 받은 파일이 맞는지 확인한 뒤
**추가 정보 → 실행** 을 누르세요. 보낸 사람이 알려 준 SHA-256 값과 아래 명령의 결과가 같은지 먼저 비교하세요.

```
cmd:         certutil -hashfile pcmigrate-gui.exe SHA256
PowerShell:  Get-FileHash .\pcmigrate-gui.exe -Algorithm SHA256
```

회사 백신이 막으면(오탐) 폴더를 예외로 등록하거나 담당자에게 허용을 요청하세요. 빌드할 때마다 exe 의 바이트가 달라지므로
**받은 빌드와 함께 전달받은 SHA-256 값**을 기준으로 확인합니다.

### 폴더 방식(onedir)과 단일 파일(onefile) — 기본은 폴더 방식

| 기준 | 폴더 방식 `onedir` (기본) | 단일 파일 `onefile` |
|---|---|---|
| 크기(측정) | 폴더 29.4MB (zip 14.5MB), exe 는 각 2.6MB | exe 2개 각 12.1MB |
| 시작 시간(측정, 이 PC) | `--version` 0.26~0.31초, GUI 창이 6초 안에 열림 | **매 실행 3.4~4.4초**(실행할 때마다 임시 폴더에 풀고 검사), GUI 는 6초 뒤에도 창이 없었음 |
| 백신 오탐 | 상대적으로 적음 | 자기 압축 해제 방식이라 오탐이 더 잦다고 알려져 있음(이 PC 에서는 직접 확인 못 함) |
| 임시 폴더 제한 | 필요 없음 | `%TEMP%` 에 쓰고 실행해야 함(보안 정책이 막으면 실행 안 됨) |
| 배포 | 폴더째 복사(zip 으로 묶기 쉬움) | exe 파일만 복사 |

그래서 기본은 폴더 방식입니다. 파일 하나로만 전달해야 하면 `--mode onefile` 로 빌드할 수 있습니다.

### 직접 빌드하기

Windows 에서 저장소 루트에서 실행합니다. PyInstaller 는 **전역 Python 에 설치하지 않고**
`%TEMP%\pcm-build` 아래 새 가상환경에만 설치하며(버전 고정: `packaging/build_exe.py` 의 `PYINSTALLER_VERSION`),
끝나면 그 폴더와 중간 산출물을 지웁니다.

```
python packaging\build_exe.py                       # dist\pcmigrate\ (git 에 올라가지 않음)
python packaging\build_exe.py --zip                 # 같은 결과를 pcmigrate-win.zip 으로도 묶음
python packaging\build_exe.py --mode onefile        # exe 단일 파일(느림, 위 표 참고)
python packaging\build_exe.py --out %TEMP%\pcm-out  # 결과 위치 지정(PowerShell: $env:TEMP\pcm-out)
```

스크립트 없이 직접 만들려면(임시 가상환경에서) 같은 일을 하는 명령은 다음과 같습니다. `rules` 폴더를 `--add-data` 로 함께 넣어야 하며,
번들된 `rules\` 는 프로그램이 자동으로 찾습니다.

```
pyinstaller --noconfirm --noconsole --paths src --add-data "rules;rules" -n pcmigrate-gui packaging\gui_entry.py
pyinstaller --noconfirm --console   --paths src --add-data "rules;rules" -n pcmigrate     packaging\cli_entry.py
```

빌드가 끝나면 각 exe 의 크기와 **SHA-256** 을 출력하고 `pcmigrate.exe --version` 으로 간단히 확인합니다.
출력된 SHA-256 을 받는 사람에게 함께 알려 주세요. exe 와 `dist\`, `build\`, `*.spec` 은 `.gitignore` 대상이라 커밋되지 않습니다.

검증한 범위(Windows 11, 일반 사용자 권한, PATH 에서 Python 제외): `--version`, `scan`, `checklist --output`, `discover`,
`transfer`(미리보기/실복사/목록 저장), `verify`(정상/변조 감지), `manifest`, 한글·공백 경로, GUI 를 띄워 정상 종료.
**검증하지 못한 것**: 아주 깨끗한 새 Windows 설치(이 PC 는 개발 PC 라 Visual C++ 런타임 등이 이미 있음), USB 드라이브에서의 실행,
실제 SmartScreen/백신 반응, 창 모드 exe 에서 복사 도구 창이 번쩍이는지 눈으로 확인(코드에서는 숨김 옵션을 줌),
**실제 OneDrive PC 에서의 클라우드 전용 파일 동작**(감지/건너뜀이 현장 환경에서 기대대로인지 확인 필요). 참고로 robocopy 의 `/xa:O`(오프라인 속성 제외)가 클라우드 전용 파일을 정확히 건너뛰는지는
공식 문서로 확인되지 않았고, **이 도구는 `/xa:O` 에 의존하지 않습니다**(자체 속성 검사로 감지).

## 1TB 이전 가이드 (외장 드라이브 / 랜선)

데이터가 크고(약 1TB) 밤새 돌려도 끊겨도 이어받고 결과를 믿을 수 있어야 하는 상황을 기준으로 정리했습니다.
**두 방식을 모두 지원합니다.** GUI 의 "대용량 복사" 화면 맨 위 **"어떻게 옮기시나요?"** 에서 고릅니다.

### 두 방식 한눈에 비교

| | 외장 드라이브 | 랜선(네트워크) | 같은 PC 안 |
|---|---|---|---|
| 흐름 | 구 PC → 외장 → 새 PC (복사 2번) | 구 PC → 새 PC 공유 폴더 (복사 1번) | 폴더 → 폴더 |
| 준비물 | NTFS 로 포맷한 외장 SSD/HDD, USB 3.x 포트 | 랜선, (LAN 포트가 없으면) USB 기가비트 랜 어댑터, 새 PC 에 비밀번호 있는 계정과 공유 폴더 | 없음 |
| 속도 한계 | USB 3.x 외장 SSD 는 보통 수백 MB/s(장치마다 다름, 이 PC 에서 측정하지 못함) | 기가비트 이론 최대 약 110MB/s (100Mbps 면 약 11MB/s) | 디스크 속도 |
| 검사 방법 | 1단계: 외장에 복사한 파일을 다시 읽어 확인 + 목록 파일 저장 / 2단계: 외장의 목록 파일과 새 PC 의 복사본을 비교(외장 원본을 다시 읽지 않음) | 1단계: 복사하며 원본 해시를 계산해 목록 파일 저장(네트워크로 다시 읽지 않음) / 2단계: 새 PC 에서 목록 파일과 로컬 복사본 비교 | 복사본을 다시 읽어 확인 |
| 막히기 쉬운 곳 | FAT32 형식(4GB 초과 파일 불가), USB 2.0 포트, 드라이브 문자 변경 | 공유 설정, 계정/비밀번호, 네트워크 프로필, 링크 속도, 절전 | 여유 공간 |

**랜선으로 충분한 경우**: 기가비트(1000Mbps) 연결이 확인되고 새 PC 에 공유 폴더를 만들 수 있으며, 복사가 한 번뿐이라 전체 시간이 외장보다 짧을 때.
**외장 드라이브가 나은 경우**: 네트워크 설정이 어렵거나 연결이 자주 끊길 때, 두 PC 를 한 곳에 둘 수 없을 때, 단순한 구조로 이어받고 싶을 때,
또는 외장 드라이브의 실제 속도가 기가비트 랜보다 확실히 빠를 때.
(측정값과 이론값을 구분해서 보세요: 이 표의 기가비트 110MB/s 는 이론값이고, 외장 SSD 속도는 제품마다 다릅니다.)

### 흐름별 체크리스트

**외장 드라이브**
1. 준비물: 외장 SSD/HDD(**NTFS 로 포맷**), USB 3.x 포트, 구 PC 의 모든 프로그램 종료, 노트북은 전원 연결.
2. (구 PC, 1단계) 화면에서 "외장 드라이브" → 1단계. 원본 폴더와 외장의 빈 폴더를 고르고 **미리보기 → 복사 시작**.
   끝나면 결과가 "정확히 복사됨 N / 실패 0" 인지 확인합니다. 외장 폴더에 확인용 목록 파일(`.pcmigrate-manifest.jsonl`)이 함께 저장됩니다.
3. 외장을 안전하게 제거해 새 PC 에 꽂습니다(**드라이브 문자가 바뀌면** 화면에 경고가 뜹니다).
4. (새 PC, 2단계) "외장 드라이브" → 2단계. 외장의 복사 폴더와 새 PC 의 폴더를 고르고 **미리보기 → 복사 시작**. 외장의 목록 파일로 새 PC 의 복사본을 검사합니다.
5. 실패 0 을 확인한 **뒤에만** 구 PC 의 원본/외장을 정리하세요.

**랜선(네트워크)**
1. 준비물: 랜선(CAT5e 이상), 필요하면 USB 기가비트 랜 어댑터, 새 PC 의 접속 계정과 공유 폴더 — 아래 "네트워크 준비" 참고.
2. (구 PC, 1단계) 탐색기에서 `\\새PC의IP\공유이름` 이 열리는지, 파일을 만들 수 있는지 먼저 확인합니다.
   화면에서 "랜선(네트워크)" → 1단계. 대상에 `\\새PC의IP\공유이름\폴더` 를 입력하고 **미리보기 → 복사 시작**.
   끝나면 목록 파일이 새 PC 의 공유 폴더에 함께 저장됩니다. 이 단계의 결과는 "복사 완료(검사는 새 PC 에서)" 입니다.
3. (새 PC, 2단계) "랜선(네트워크)" → 2단계. 목록 파일과 복사된 폴더를 고르고 **검사 시작**. 구 PC 와 네트워크를 거치지 않고 로컬 파일만 읽습니다(폴더는 읽기만).
4. 실패 0 을 확인한 **뒤에만** 구 PC 의 원본을 정리하세요.

문제가 생겼을 때 확인할 곳: 외장은 **드라이브 문자와 형식(NTFS)**, USB 3.x 포트(느리면 화면에 USB 2.0 확인 안내), 연결 상태 /
랜선은 **ping, 공유 폴더 접속, 링크 속도(1000Mbps), 네트워크 프로필, 절전 설정**(느리면 화면에 100Mbps 확인 안내).

### 네트워크 준비 (랜선으로 옮길 때)

**환경(공유기, 보안 소프트웨어, Windows 버전)에 따라 다를 수 있습니다.** 아래는 일반적인 예시이고, 화면 오른쪽 위 "네트워크 준비 도움말" 버튼에도 같은 내용이 있습니다.

1. **연결**: 같은 공유기/스위치에 두 PC 를 모두 **유선**으로 연결하거나, 두 PC 를 랜선 하나로 직접 연결합니다. LAN 포트가 없으면 USB 기가비트 랜 어댑터를 씁니다.
2. **랜선 직결일 때 고정 IP (예시)**: 설정 > 네트워크 및 인터넷 > 이더넷 > IP 할당 > 편집 > 수동 > IPv4
   - 구 PC: IP `192.168.50.1`, 서브넷 마스크 `255.255.255.0`, **기본 게이트웨이는 비워 둡니다**.
   - 새 PC: IP `192.168.50.2`, 서브넷 마스크 `255.255.255.0`, 기본 게이트웨이는 비워 둡니다.
   - 두 PC 모두 같은 설정 화면에서 **네트워크 프로필을 "개인"** 으로 바꿉니다(공용이면 접속이 막힙니다).
   명령으로 확인/설정할 수도 있습니다(관리자 권한 필요, 이름 "이더넷" 은 `ipconfig` 로 확인).
   ```
   cmd:         netsh interface ip set address name="이더넷" static 192.168.50.2 255.255.255.0
   PowerShell:  New-NetIPAddress -InterfaceAlias "이더넷" -IPAddress 192.168.50.2 -PrefixLength 24
                Set-NetConnectionProfile -InterfaceAlias "이더넷" -NetworkCategory Private
   ```
3. **새 PC 에서 공유 폴더 만들기**: ① **비밀번호가 있는** 로컬 계정 하나를 만듭니다(비밀번호가 없으면 네트워크 접속이 막힙니다).
   ② 받을 폴더(예: `D:\이전`)를 만들고 속성 > 공유 > 고급 공유에서 공유한 뒤, 그 계정에 **변경(쓰기)** 권한을 줍니다.
   ```
   cmd:         net share 이전=D:\이전 /GRANT:접속계정,CHANGE
   PowerShell:  New-SmbShare -Name 이전 -Path D:\이전 -ChangeAccess 접속계정
   ```
   (`net share` 는 아래 [각주 1](#net-note) 참고. `New-SmbShare` 문서: [참고 링크](#참고-링크) 절)
4. **접속 테스트(구 PC 에서)**:
   ```
   cmd / PowerShell:  ping 192.168.50.2
   cmd:               net use \\192.168.50.2\이전 /user:접속계정
   PowerShell:        Test-Path \\192.168.50.2\이전
   ```
   탐색기 주소창에 `\\192.168.50.2\이전` 을 입력해 열리는지, 폴더 안에 새 텍스트 파일을 만들었다 지워서 **쓰기**가 되는지 확인합니다.
   접속이 꼬였으면 `net use * /delete` 로 기존 연결을 끊고 다시 시도합니다. (`net use` 는 [각주 1](#net-note) 참고)
5. **링크 속도 확인**: 설정 > 네트워크 및 인터넷 > 이더넷 > 속성의 "링크 속도"가 **1000/1000(Mbps)** 인지 봅니다(PowerShell: `Get-NetAdapter | Format-Table Name, LinkSpeed`).
   100Mbps 로 나오면 케이블(CAT5e 이상인지)이나 어댑터를 바꿔 보세요. 기가비트에서 이론 최대는 약 110MB/s 이고 실제는 환경에 따라 크게 다릅니다.
6. **전송 중 (받는 PC 는 직접 해야 합니다)**: 이 도구는 **보내는 PC(도구를 실행하는 PC)의 절전만** 막습니다. 공유 폴더를 가진 **받는 새 PC 는 도구가 건드리지 못하므로**
   사용자가 전송 전에 직접 ① 전원 어댑터를 연결하고 ② 잠자기를 꺼 두세요. 안 그러면 새 PC 가 잠들어 공유 폴더가 끊깁니다(끊겨도 같은 설정으로 다시 실행하면 이어받습니다).
   ```
   cmd:           powercfg /change standby-timeout-ac 0      ← 전원 연결 시 잠자기까지의 시간(분). 0 = 안 함 (powercfg /? 도움말 기준)
   되돌리기:      powercfg /change standby-timeout-ac 30     ← 전송 전에 적어 둔 원래 값(분)으로. 현재 값은 `powercfg /query` 로 확인
   ```
   (관리자 권한이 필요할 수 있습니다. 이 README 에서는 확인하지 못했습니다.)
   네트워크 어댑터의 전원 절약도 끕니다: **장치 관리자 > 네트워크 어댑터 > (사용 중인 어댑터) 속성 > 전원 관리 탭 > "전원을 절약하기 위해 이 장치를 끌 수 있음" 체크 해제**.
   PowerShell 로 하려면 `Set-NetAdapterPowerManagement` 문서([참고 링크](#참고-링크) 절)를 보세요(옵션 이름은 이 README 에서 확인하지 못했습니다).
   전송이 끝나면 위 설정을 원래대로 되돌리세요(체크 다시 켜기, 잠자기 시간 복구).

**SMB 서명과 로그온 주의**: Microsoft 문서([Control SMB signing behavior](https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-signing))에 따르면
Windows 11 24H2 의 **Pro/Enterprise/Education 은 SMB 서명을 기본으로 요구**하고 **Home 은 요구하지 않습니다**. 서명을 지원하지 못하는 NAS 나 게스트(익명) 접속은 실패할 수 있습니다.
Microsoft 는 서명을 끄는 우회를 권장하지 않으므로, 이 문서도 끄는 방법을 안내하지 않습니다. **이 도구는 SMB 서명 등 Windows 의 네트워크/보안 설정을 바꾸지 않습니다**(필요하면 사용자가 직접 확인).
또한 **비밀번호가 없는 로컬 계정은 네트워크 로그온이 되지 않습니다**: [Accounts: Limit local account use of blank passwords to console logon only](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/accounts-limit-local-account-use-of-blank-passwords-to-console-logon-only).

<a id="net-note"></a>
**각주 1 (net use / net share)**: Microsoft Learn 의 현재 Windows commands 목록에는 `net use`, `net share` 페이지가 없습니다. 두 명령은 Windows 11 에서도 동작하지만
공식 문서는 보관본([보관본 문서(Windows Server 2012 R2/2012)](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/gg651155(v=ws.11)))뿐이라, 옵션은 `net use /?`, `net share /?` 로 직접 확인하세요.

네트워크 오류는 쉬운 문구로 나옵니다: 공유 폴더를 찾을 수 없음(IP/공유 이름/프로필 확인), 로그인 실패(계정/비밀번호), 이미 다른 계정으로 연결됨(`net use * /delete`), 쓰기 권한 없음(공유 권한).

### 1TB 예상 시간 (측정 기반 추정 + 한계)

**이 PC(18코어, NVMe SSD, 백신 켜짐)에서 측정한 값** — 합성 데이터(`%TEMP%`), 원본과 대상이 같은 디스크라서 실제 외장/네트워크 환경과 다릅니다.

| 항목 | 측정값 | 비고 |
|---|---|---|
| 해시 속도(파일 캐시 상태) | 1개 스레드 약 800MB/s, 2개 약 1.7GB/s | CPU 한계. 실제 디스크/네트워크는 이보다 훨씬 느려 병목이 아님 |
| 읽기 청크 64KB~16MB | 600~860MB/s 로 큰 차이 없음 | 4MiB 로 정함(느린 장치에서 큰 순차 읽기가 유리, 오버헤드 무시 가능). 파일 캐시 영향이 커서 콜드 읽기 차이는 이 측정으로 알 수 없음 |
| 복사 도구 방식(1GB×2, 로컬) | 일반 약 1.9GB/s, `/J` 약 0.95GB/s, `/Z`(기본) 약 0.65GB/s | 캐시 영향이 큼. `/Z` 는 끊겨도 파일 중간부터 이어받는 대신 로컬에서는 느림 |
| 작은 파일 20,000개 복사 | `/Z` 약 300~650개/초, `/MT:4` 약 1,100~1,250개/초 | 그래서 복사 스레드 기본값을 4 로 함(느린 HDD/USB 는 `--copy-threads 1`) |
| 작은 파일 50,000개(합계 200MB) 전체 | 복사 51초 + 검사 57초 = 총 108초, 약 2.1ms/파일 | 수정 전에는 파일당 약 6.4ms(복사 `/MT` 없음, 해시 버퍼 낭비) |
| 이어받기(이미 끝난 50,000개) | 약 5초, 원본/대상 읽기 0 | 상태 파일의 해시 재사용 |
| 검사만 다시 / 목록 비교 | 약 24초 / 약 21초 (50,000개) | 대상만 읽음 |
| 상태 파일 / 목록 파일 크기 | 각 약 7.3MB (50,000개) → 파일당 약 150바이트 | 줄 단위 JSON, 100만 개면 약 150MB |
| 최대 메모리(작업 집합) | 약 65~73MB (50,000개) | 파일당 약 0.6KB 로 보면 100만 개는 약 0.7GB, 300만 개는 약 2GB (외삽, 불확실) |

**1TB 외삽** (대용량 파일 위주라고 가정, 단순 모델: 복사 = 1TB ÷ 속도, 검사 = 원본/대상을 다시 읽는 시간으로 복사의 1~2배). 속도는 **가정**이며 이 PC 에서 측정한 값이 아닙니다.
실제는 가장 느린 구간(구 PC 디스크, 케이블/포트, 대상 디스크)이 결정합니다.

| 지속 속도(가정) | 대표 상황 | 복사 | 복사 + 검사 합계 |
|---|---|---|---|
| 30MB/s | USB 2.0 포트의 외장 | 약 9.3시간 | 약 19~28시간 |
| 100MB/s | 기가비트 랜 실측 수준, HDD | 약 2.8시간 | 약 5.6~8.3시간 |
| 110MB/s | 기가비트 랜 이론 최대 | 약 2.5시간 | 약 5.0~7.6시간 |
| 400MB/s | USB 3.x 외장 SSD(가정) | 약 0.7시간 | 약 1.4~2.1시간 |
| 1000MB/s | NVMe 끼리(가정) | 약 0.3시간 | 약 0.6~0.8시간 |

- **네트워크(랜선)는 복사본을 네트워크로 다시 읽지 않고**(검사는 새 PC 로컬 디스크에서) 하므로 표의 "검사" 부분이 줄어듭니다. 새 PC 의 로컬 검사 시간은 별도로 더해집니다.
- 작은 파일이 많으면 위 시간에 파일당 약 2ms(SSD 기준, HDD 는 몇 배)가 더해집니다(100만 개면 약 35분 이상).
- **불확실성**: 외장/랜 실제 속도, 백신, 파일 크기 분포, 디스크 상태에 따라 2배 이상 달라질 수 있습니다. 먼저 폴더 하나로 시험 복사해 속도를 보고 판단하세요.
  화면의 남은 시간은 최근 30초 속도로 계속 다시 계산합니다.

### 안전하게 돌리기 위한 약속

- **절전 방지**: 복사 중에는 시스템이 잠자기로 들어가지 않게 합니다(화면 끄기는 건드리지 않음, 끝나거나 오류가 나면 반드시 원래대로). 실패하면 경고만 하고 계속하니 전원 설정도 확인하세요. 노트북은 전원을 연결하세요. 명령줄에서 끄려면 `--no-keep-awake`.
- **OneDrive "클라우드 전용" 파일**: 클라우드에만 있는 파일은 읽는 순간 자동으로 내려받아지는 것이 일반적인 동작입니다(이번에 공식 문서로 확인하지는 못했습니다). 그러면 **구 PC 의 디스크 공간을 쓰고 로컬 상태가 바뀔 수** 있고, 1TB 규모면 시간과 공간이 크게 늘어납니다.
  이 도구는 **파일 속성으로 클라우드 전용 파일을 감지해 기본으로 건너뛰고**, 리포트에 `cloud_only` 로 남깁니다(`--include-cloud` 를 줄 때만 포함). 필요한 파일은 복사 전에 직접 내려받아 두세요:
  탐색기에서 해당 폴더를 우클릭 > **"이 디바이스에 항상 유지"** ([Microsoft 안내](https://support.microsoft.com/en-us/office/0e6860d3-d9f3-4971-b321-7092438fb38e)). 내려받는 동안 디스크 여유 공간을 확인하세요.
- **프로그램 종료 후 복사**: CAD/BIM 등 파일을 쓰는 프로그램을 모두 닫으세요. 사용 중인 파일은 복사/검사에 실패하고 "다른 프로그램이 파일을 사용 중" 으로 안내됩니다. 닫은 뒤 "실패한 것만 다시 시도"를 누르세요.
- **여유 공간 사전 검사**: 대상의 여유 공간이 원본 총량 + 5% 보다 적으면 시작하지 않습니다(네트워크 경로는 조회되면 같은 검사, 안 되면 경고만).
- **FAT32 차단**: 대상이 FAT32 인데 4GB 보다 큰 파일이 있으면 복사 전에 막습니다(분할 저장은 지원하지 않습니다). NTFS 로 포맷하거나 다른 드라이브를 쓰세요. exFAT 는 경고만 합니다.
- **드라이브 분리 감지**: 복사 중 외장 드라이브가 빠지면 즉시 멈추고 "드라이브가 분리되었습니다. 다시 연결하면 이어받습니다" 라고 알립니다. 다시 연결해 같은 설정으로 실행하면 이어받고, 드라이브 문자가 바뀌었으면 경고합니다.
- **xref/데이터 단축은 절대 경로를 씁니다**: 도면의 외부 참조, 이미지, 데이터 연결은 `D:\프로젝트\...` 같은 절대 경로로 저장된 것이 많습니다. 결과 화면에 원본의 절대 경로와 최상위 폴더 구조를 보여 주고,
  복원한 위치가 원래 경로와 다르면 노란 경고가 뜹니다. 새 PC 의 드라이브 문자와 폴더 구조를 구 PC 와 같게 맞추는 것이 가장 안전합니다.
- **검사가 끝나 "실패 0" 을 확인하기 전에는 구 PC 의 원본을 삭제하거나 PC 를 포맷하지 마세요.** 이 도구는 원본을 지우지 않습니다.
- 긴 경로(260자 초과)는 읽기/검사에 `\\?\` 접두를 써서 처리합니다. 그래도 읽지 못하면 "경로가 너무 깁니다" 로 알려 줍니다.

### 읽기 횟수 (큰 파일 기준, 파일 하나당)

복사 도구(robocopy)의 읽기와 이 도구의 검사 읽기를 모두 센 값입니다. "측정" 은 위 합성 데이터에서 확인한 값입니다.

| 상황 | 원본 읽기 | 대상(복사본) 읽기 | 이전 단계(11단계) 대비 |
|---|---|---|---|
| 로컬/외장 첫 복사 | 2번(복사 1 + 해시 1) | 1번 | 같음 |
| 이어받기(이미 검사 끝난 파일) | 0 (측정) | 0 (측정) | 상태 파일로 건너뜀(같음) |
| 검사만 다시(`--verify-only`) | 0 (측정, 상태 파일의 원본 해시 재사용) | 1번(항상 다시 읽음) | 11단계의 `--strict`(원본 1 + 대상 1)보다 원본 읽기 1번 줄어듦 |
| 외장 2단계(`--use-manifest`) | 외장 복사 1번만(해시 읽기 0, 측정) | 1번 | 외장 해시 읽기 1번 줄어듦 |
| 랜선 1단계(`--manifest-out`, 대상이 네트워크) | 2번 | 0 (네트워크로 다시 읽지 않음) | 네트워크 재읽기 1번 줄어듦, 새 PC 에서 로컬 검사로 대체 |
| 새 PC 검사(`verify`) | 0 | 1번(로컬) | 새 기능 |

첫 복사의 원본 2번 읽기(복사 도구 1 + 해시 1)는 이번 단계에서 줄이지 못했습니다(복사 도구를 쓰는 한 유지됩니다).

### 새 명령과 옵션

```
python -m pcmigrate transfer <원본> <대상> [옵션]
  --verify-only            복사 없이 원본/대상을 비교만(대상은 항상 다시 읽음)
  --retry-failed           전체를 다시 복사하지 않고 검사가 끝나지 않았거나 실패한 파일만 다시 복사/검사
  --workers N              파일 단위 병렬 검사 개수(기본 2, 최대 8). 느린 USB/HDD 는 1 이 나을 수 있음
  --copy-threads N         복사 도구가 동시에 복사할 파일 수(기본 4, 1~16)
  --chunk-mb N             읽기 청크(MB, 기본 4)
  --copy-mode MODE         restartable(기본, /Z: 파일 중간부터 이어받음) / normal / unbuffered(/J)
  --manifest-out FILE      원본 기준 해시 목록(.jsonl) 저장. 대상이 네트워크면 대상 검사를 미루고 새 PC 에서 verify
  --use-manifest FILE      원본 해시를 이 목록에서 가져와 원본을 다시 읽지 않음(외장 2단계)
  --verify-dest / --no-verify-dest   --manifest-out 과 함께 대상 검사를 지금 할지(기본: 네트워크면 미룸)
  --no-keep-awake          절전 방지를 끔

python -m pcmigrate manifest <원본> --out <목록.jsonl>            # 원본(로컬)을 읽어 해시 목록만 만들기(읽기 전용)
python -m pcmigrate verify --manifest <목록.jsonl> --target <폴더> [--report 결과.json] [--no-extras]
                                                                   # 새 PC: 목록과 로컬 폴더 비교(누락/내용 다름/추가 파일), 폴더는 읽기만
```

- `verify` 는 대상 폴더를 읽기만 합니다. 결과 파일(`--report`)을 검사할 폴더 안에 저장하려 하면 거부합니다.
- 복사 도구 실행 중 진행 상황(바이트, 속도, 남은 시간)이 CLI 는 stderr 에 10초마다, GUI 는 아래쪽 진행 막대에 표시됩니다. 속도가 계속 느리면 외장은 "USB 2.0 확인"(40MB/s 미만),
  네트워크는 "링크 속도 100Mbps 확인"(11MB/s 미만) 안내가 서로 다른 문구로 나옵니다.
- 복사 도구의 종료 코드/오류 줄을 원인별로 나눕니다: 사용 중, 권한, 경로 길이, 공간 부족, 연결 끊김, 네트워크 접속 실패(찾을 수 없음/로그인/충돌/쓰기 권한).
- 상태 파일(`.pcmigrate-state.json`)은 파일마다 한 줄씩 덧붙이는 **줄 단위 JSON** 이라 파일이 수백만 개여도 이어받기 중 전체를 다시 쓰지 않으며, 전원이 꺼져 마지막 줄이 잘려도 나머지는 쓸 수 있습니다.
- `.pcmigrate-` 로 시작하는 기록 파일(상태, 로그, 목록)은 다른 폴더로 복사하지 않습니다(외장 2단계에서 외장의 상태 파일이 새 PC 의 상태 파일을 덮어쓰는 일을 막기 위함).

## 다른 PC(구 PC)에서 실행하기

구 PC에는 이 도구가 없을 수 있습니다. 설치 없이 읽기 전용으로만 돌려도 되고, 아무것도 수정하지 않습니다.

1. **Python 3.10 이상 확인** — `python --version` (없으면 python.org 에서 설치, 다른 패키지는 필요 없음)
2. **저장소 받기** — GitHub 에서 zip 을 받아 풀거나 `git clone <저장소 주소>`
3. **설치 없이 실행(권장)** — 저장소 폴더에서:

   cmd:
   ```
   cd C:\path\to\pc-migration-tool
   set PYTHONPATH=src
   python -m pcmigrate scan
   ```
   PowerShell:
   ```
   cd C:\path\to\pc-migration-tool
   $env:PYTHONPATH = "src"
   python -m pcmigrate scan
   ```
   설치해서 쓰려면 `python -m pip install -e .` (이후 어디서나 `python -m pcmigrate`).

4. **구 PC에서 읽기 전용으로 돌릴 3개 명령** (출력 폴더는 저장소 밖으로, 예: USB `E:\`):

   cmd:
   ```
   python -m pcmigrate scan
   python -m pcmigrate discover --format md --output E:\pcm\discover.md
   python -m pcmigrate checklist --format md --output E:\pcm\checklist.md
   ```
   PowerShell:
   ```
   python -m pcmigrate scan
   python -m pcmigrate discover --format md --output E:\pcm\discover.md
   python -m pcmigrate checklist --format md --output E:\pcm\checklist.md
   ```
   `discover` 는 폴더 크기를 계산하느라 1분 안팎 걸릴 수 있고, 진행 상황이 화면(stderr)에 표시됩니다.

5. **결과 파일에는 개인 정보가 들어 있습니다**(설치된 프로그램 목록, 사용자 폴더 경로, 비밀 정보 가능성이 있는 폴더 이름).
   **저장소에 두거나 커밋하지 말고**, 남에게 공유하기 전에 내용을 확인하세요.

6. **인터넷이 없는 PC** — 이 도구는 외부 의존성이 없습니다. 저장소 폴더를 통째로 USB 나 공유 폴더로 복사한 뒤
   구 PC에서 위 3번처럼 `PYTHONPATH=src` 로 실행하면 됩니다(Python 3.10+ 만 설치되어 있으면 됩니다).
   `winget` 이 없거나 느려도 동작하며, 이 경우 winget 매칭은 건너뛰고 레지스트리 목록만 씁니다.

환경변수가 대소문자만 다른 중복(예: Git Bash 에서 `PROGRAMDATA=...` 로 지정)으로 의도와 다른 경로가 잡히면
`python -m pcmigrate --env PROGRAMDATA=D:\pd discover` 처럼 `--env NAME=VALUE` 로 경로 해석용 값을 덮어쓸 수 있습니다.

## 권장 사용 순서

구 PC에서:
1. `python -m pcmigrate scan` — 설치된 프로그램을 감지하고 적용될 규칙을 확인
2. `python -m pcmigrate checklist --format md --output <폴더>\checklist.md` — 수동 작업 체크리스트 작성
3. `python -m pcmigrate export --output <폴더>\settings.zip` — 설정 패키지 내보내기

신 PC에서:
4. `python -m pcmigrate import <settings.zip> --dry-run` — 무엇이 만들어지고 덮어써지는지 미리보기(쓰기 없음)
5. `python -m pcmigrate import <settings.zip> --yes` — 실제 복원(덮어쓸 파일은 `.bak` 백업)

사진/도면/프로젝트 같은 대용량은 설정 패키지가 아니라 `transfer`로 옮깁니다.
`python -m pcmigrate transfer <원본폴더> <대상> --dry-run` 으로 먼저 확인한 뒤 `--dry-run` 없이 실행하세요.
중단되면 같은 명령을 다시 실행하면 이어받습니다(`--strict` 는 모든 파일을 다시 해시 검증).

`transfer` 는 **대상 폴더에 기록 파일을 남깁니다**(원본에는 아무것도 쓰지 않습니다. 이 파일들은 다른 곳으로 복사되지 않습니다):

| 파일 | 용도 | 위치 바꾸기 |
|---|---|---|
| `.pcmigrate-state.json` | 이어받기 진행 상태(줄 단위 JSON: 검증 끝난 파일의 크기/수정 시각/해시) | `--state-file <경로>` |
| `.pcmigrate-robocopy.log` | robocopy 로그 | `--log <경로>` |
| `.pcmigrate-manifest.jsonl` | `--manifest-out` 을 줄 때만: 원본 기준 상대경로/크기/수정 시각/SHA-256 목록 | `--manifest-out <경로>` |

상태 파일이 손상되었거나 원본 경로가 다르면 무시하고 전부 다시 검증합니다. 전송을 마친 뒤 지워도 됩니다(다음 실행 때 전체를 다시 검증).
`--strict` 는 상태 파일을 믿지 않고 모든 파일을 SHA-256 으로 다시 확인하며, 크기·수정 시각은 같은데 내용이 어긋난 파일은
원본에서 그 파일만 다시 복사합니다(어긋났던 사본은 `.bak` 으로 보존).

체크리스트는 `1. 새 PC 에서 먼저 설치 → 2. 로그인·인증 → 3. 데이터 복사/기타` 순서이고,
이 PC에 없는 프로그램 관련 경고는 생략됩니다(`--all` 로 모두 표시).

## 규칙 없는 프로그램 (discover)

`rules/*.json`은 알려진 프로그램을 정밀하게 처리하는 용도입니다. 규칙이 없는 설치 프로그램은 조용히 빠지지 않도록
`discover`가 별도로 찾아서 보여 줍니다(읽기 전용).

```
python -m pcmigrate discover                          # 규칙 없는 프로그램과 설정 폴더 후보
python -m pcmigrate discover --format md --output <폴더>\discover.md
python -m pcmigrate discover --all                    # 제조사 번들, 규칙 쪽 폴더, 연결 못 한 폴더까지
python -m pcmigrate discover --min-size-kb 10         # 작은 폴더 숨김
python -m pcmigrate discover --output-selection <폴더>\selection.json
```

- 후보 위치: `%APPDATA%`, `%LOCALAPPDATA%`(캐시/임시 제외), `Documents`(OneDrive 후보 포함), `%PROGRAMDATA%`,
  `%USERPROFILE%` 바로 아래 점 폴더/파일(`.gitconfig`, `.ssh`, `.m2` 등).
- 프로그램과의 연결은 폴더 이름, 발행자, 설치 위치로 추정하고 근거 등급(확실/추정)을 표시합니다. 크기는 시간 상한이 있어
  오래 걸리면 "크기 미확인"으로 나옵니다.
- `.ssh`, `.aws`, 쿠키, 토큰, credentials 등이 들어 있을 수 있는 항목에는 "비밀 정보 가능성" 경고가 붙고 기본 제외입니다.

**처리 원칙: 자동 포함 안 함, 선택 후 포함.** discover 는 아무것도 export 에 넣지 않습니다. 포함하려면
`--output-selection`으로 만든 선택 파일(모두 `include: false`)에서 필요한 항목만 `true`로 바꾸고
`export --extra <선택파일>`로 지정합니다. 비밀 정보 가능성이 있는 항목은 `--allow-secrets`를 한 번 더 붙여야 하고,
신 PC에서 복원할 때도 `import --yes --allow-extra`가 필요합니다(`--dry-run`으로 먼저 확인).
포함된 항목에도 export의 기존 안전장치(원본 읽기 전용, 해시 manifest, `*.lic` 제외, 허용 루트 검증, zip 슬립 방어)가 적용됩니다.

`scan`, `checklist`, `export` 마지막에 "규칙 없는 설치 프로그램 N개, 설정 폴더 후보 M개"가 출력됩니다.

## 경로에 환경변수를 쓸 때 (cmd vs PowerShell)

| 셸 | 임시 폴더 예 |
|---|---|
| cmd | `%TEMP%\checklist.md` |
| PowerShell | `$env:TEMP\checklist.md` |
| Git Bash | `$TEMP/checklist.md` |

셸에 맞지 않는 표기는 확장되지 않은 채 들어옵니다. 이 도구는 `%TEMP%`, `$env:TEMP` 가 남아 있으면
폴더를 만들지 않고 오류와 힌트를 출력한 뒤 종료 코드 1로 끝납니다.

## 읽지 못한 파일, 입력 검사, 원본 보호 (v0.1.1)

- **실행 중인 프로그램이 잠근 파일**(예: Chrome 이 실행 중일 때 `User Data` 의 `LOCK` 파일)은 읽을 수 없습니다. `export` 는 그런 파일을
  패키지에 **흔적 없이 제외**하고(임시 저장소에 먼저 끝까지 읽은 뒤 성공했을 때만 zip 에 기록) `manifest['failed']` 에 "사용 중이라 읽지 못함"으로
  남긴 채 끝까지 진행하며, 결과에 "읽지 못해 제외된 파일 N개(예: Chrome 실행 중이면 닫고 다시 하세요)"와 예시 경로 5개를 보여 줍니다.
  패키지를 만든 직후 다시 검사해 문제가 있으면 임시 파일을 남기지 않고 어떤 항목이 문제인지 알려 줍니다.
  `LOCK`, `lockfile`, `parent.lock`, `SingletonLock`, `SingletonCookie`, `SingletonSocket` 은 복사할 가치가 없어 항상 제외합니다.
- `--chunk-mb`(0.25~1024), `--workers`(1~8), `--copy-threads`(1~16)는 범위를 벗어나면 조용히 바꾸지 않고 종료 코드 2 와 한국어 오류를 냅니다.
- 깨졌거나 UTF-8 이 아닌 해시 목록(`.jsonl`)과 패키지 `manifest.json` 은 traceback 없이 "해시 목록 오류" / "패키지 검증 실패" 로 안내합니다.
- 원본 폴더 안에는 아무것도 쓰지 않습니다: `transfer` 의 `--report/--log/--state-file/--manifest-out`, `manifest --out`, `copy --report` 가 원본 폴더 안
  (정션·대소문자 차이 포함)이면 시작하지 않습니다. GUI 의 결과 파일 저장도 같습니다.
- `export/discover/checklist --output` 에 기존 **폴더**를 주면 거부합니다(폴더를 `.bak` 으로 바꾸고 같은 이름의 파일을 만드는 일이 없도록).
  `import --report` 가 패키지 파일 자신이면 거부합니다. 빈 문자열 경로("")는 현재 폴더로 보지 않고 오류로 처리합니다.

## 개인정보 주의

`checklist`, `export`, `transfer --report` 의 출력 파일에는 이 PC에 설치된 프로그램 목록과 파일 경로가 들어갑니다.
저장소 안에 두거나 **커밋하지 마세요**(공유 전에 내용을 확인하세요). `.gitignore` 가 `checklist*.md`, `discover*.md`, `*.bak`, `*.jsonl`,
`.pcmigrate-*` 등을 막아 주지만 안심하지 말고 확인하세요. **오류 로그(`errors-*.log`)에도 파일 경로가 들어 있으니 공유 전에 확인하세요.** 라이선스는 복원되지 않습니다.

## 테스트

```
python -m pip install pytest
python -m pytest -q
```

테스트는 `tmp_path`와 mock만 사용하며 실제 레지스트리, winget, 사용자 파일은 건드리지 않습니다.

## 참고 링크

공식 문서 링크 모음입니다(링크 내용은 바뀔 수 있습니다).

- [robocopy](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/robocopy) — 복사 도구 옵션 설명
- [Get-FileHash](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/get-filehash) — PowerShell 로 SHA-256 확인
- [최대 경로 길이](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation) — 260자 제한과 `\\?\` 접두
- [파일 시스템 기능 비교](https://learn.microsoft.com/en-us/windows/win32/fileio/filesystem-functionality-comparison) — NTFS/FAT32/exFAT 차이(4GB 제한 등)
- [BitLocker 복구 키 찾기](https://support.microsoft.com/en-us/windows/finding-your-bitlocker-recovery-key-in-windows-6b71ad27-0b89-ea08-f143-056f5ab347d6) — 드라이브가 암호화돼 있을 때
- [SMB 보안](https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-security) — 공유 폴더 보안 개요
- [Control SMB signing behavior](https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-signing) — SMB 서명 기본값
- [New-SmbShare](https://learn.microsoft.com/en-us/powershell/module/smbshare/new-smbshare) — PowerShell 로 공유 폴더 만들기
- [Set-NetConnectionProfile](https://learn.microsoft.com/en-us/powershell/module/netconnection/set-netconnectionprofile) — 네트워크 프로필(개인/공용) 변경
- [Set-NetAdapterPowerManagement](https://learn.microsoft.com/en-us/powershell/module/netadapter/set-netadapterpowermanagement) — 어댑터 전원 관리
- [winget export](https://learn.microsoft.com/en-us/windows/package-manager/winget/export) — 설치 프로그램 목록 내보내기
- [OneDrive: 이 디바이스에 항상 유지](https://support.microsoft.com/en-us/office/0e6860d3-d9f3-4971-b321-7092438fb38e) — 클라우드 전용 파일 내려받기
- [AutoCAD 참조 도면 경로](https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-LT/files/GUID-AB6D738D-DD2F-41ED-9B56-45B64A467CF8.htm) — **AutoCAD LT 문서**이므로 최신 AutoCAD/Civil 3D 와 같은지는 확인되지 않았습니다
