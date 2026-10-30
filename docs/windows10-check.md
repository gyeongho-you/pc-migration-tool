# Windows 10 에서 써 보기 전 확인표

**요약: 이 도구는 Windows 11 에서만 개발·시험했고, Windows 10 은 검증하지 않았습니다.** 아래 내용은 "코드와 문서에서 확인된 것"과 "확인하지 못한 것"을 나눠 적었습니다. 확인하지 못한 것은 '된다'고 가정하지 말고, 이 문서의 순서대로 **임시 폴더에서 읽기·미리보기만** 해 본 뒤 판단하세요.

## 확인된 것 / 확인하지 못한 것

| 구분 | 내용 |
|---|---|
| 확인됨 | 개발과 시험은 Windows 11 한 대에서 했습니다(자동 시험 약 2300개 포함). 요구 조건은 Python 3.10 이상(`pyproject.toml`), 외부 패키지 없음입니다. |
| 확인됨 | 프로그램 목록은 레지스트리(읽기)와 `winget export`/`winget list` 로 모읍니다. winget 이 없거나 실패하면 winget 쪽 목록은 비어 있게 처리합니다(코드의 `detect.py`). |
| 확인됨 | 대용량 복사는 Windows 내장 `robocopy` 를 씁니다. |
| **미확인** | Windows 10 에서 어느 기능이든 실제로 돌려 본 적이 없습니다(exe 포함). 빌드한 exe 를 깨끗한 Windows 설치에서 돌려 본 적도 없습니다. |
| **미확인** | 이더넷 주소 자동 맞추기(`lan-setup`)는 Windows 11 에서도 **실제 PC 시험을 하지 못했습니다**(가짜 명령 실행기로만 시험). Windows 10 에서는 더 알 수 없습니다. |
| **미확인** | winget 이 Windows 10 의 어느 버전부터 기본으로 있는지는 이 저장소에서 확인한 적이 없습니다. 아래 1번으로 PC 에서 직접 확인하세요. |

## 1. Windows 10 버전과 winget 확인

PowerShell 에서(읽기만 합니다):

```
winver                       # 창이 열립니다. 버전(예: 21H2, 22H2)을 확인
winget --version             # 버전이 나오면 있음. "인식되지 않습니다"면 없음
Get-AppxPackage Microsoft.DesktopAppInstaller | Select Name, Version   # '앱 설치 관리자'가 설치돼 있는지
```

- 권장 기준은 **Windows 10 21H2 이상**으로 잡았습니다(이 도구가 요구하는 것이 아니라, 확인 대상을 좁히기 위한 기준입니다).
- winget 이 없으면 Microsoft Store 의 "앱 설치 관리자(App Installer)"를 설치·업데이트하면 생기는 것으로 알려져 있지만, 이 도구에서는 시험하지 않았습니다. 없어도 도구는 멈추지 않고 레지스트리 목록만 쓰는 것으로 코드에 되어 있으나, 새 PC 에서 `winget import` 로 설치하는 흐름은 쓸 수 없고 수동 설치 목록 안내만 남습니다.
- 지원이 끝난 OS 에서는 Microsoft Store 나 winget 원본이 더는 갱신되지 않을 수 있습니다. 패키지가 안 보이면 도구 문제가 아닐 수 있습니다.

## 2. 임시 폴더에서 순서대로 돌려 보기

실제 업무 폴더를 쓰지 말고, 빈 임시 폴더(예: `%TEMP%\pcm-check`)와 작은 시험 폴더(파일 몇 개)로 합니다. 각 단계에서 오류가 나면 **다음으로 넘어가지 말고** 멈추고 `pcmigrate logs` 의 내용과 함께 기록하세요(오류 로그에는 파일 경로가 들어 있으니 공유 전에 확인).

```
pcmigrate --version                                   # 설치·실행이 되는지
pcmigrate check-rules                                  # 규칙 폴더 읽기만 확인
pcmigrate scan                                        # 설치 프로그램 감지(읽기만, 레지스트리와 winget 조회)
pcmigrate export --output %TEMP%\pcm-check\settings.zip   # 설정 묶음 만들기
pcmigrate import %TEMP%\pcm-check\settings.zip --dry-run  # 복원 미리보기(쓰기 없음)
```

- **`export` 에는 `--dry-run` 이 없습니다.** 대신 원본은 읽기만 하고 출력 `.zip` 한 개(와 이어받기용 임시 파일)만 쓰므로, 출력 위치를 임시 폴더로 지정해 시험하세요. 시험이 끝나면 임시 폴더를 직접 지웁니다.
- 복원은 **`--dry-run` 미리보기까지만** 합니다. `--yes` 로 실제 복원을 하는 것은 시험 PC 에서 미리보기 결과를 읽고 이해한 뒤에 하세요.
- 받기 폴더를 원래 위치로 놓는 `place` 는 기본이 미리보기입니다. 시험용 받기 폴더(`<드라이브>\<경로>` 구조의 작은 폴더)로 **`--yes` 없이** 미리보기만 보세요.
  ```
  pcmigrate place %TEMP%\pcm-check\받기
  ```
- 작은 시험 폴더 복사는 `pcmigrate transfer <시험원본> %TEMP%\pcm-check\복사본 --dry-run` 으로 목록만 확인한 뒤, 되면 `--dry-run` 없이 한 번 실행하고 `verify` 로 확인합니다. `robocopy` 가 있는지 `robocopy /?` 로 먼저 확인하세요.
- Python 이 없는 PC 라면 릴리스의 exe 묶음(`pcmigrate-win.zip`)으로 같은 순서(`pcmigrate.exe --version` 부터)를 따릅니다. exe 는 서명이 없어 처음에 SmartScreen 이 뜰 수 있습니다.

## 3. 이더넷 주소 맞추기(`lan-setup`)

랜선 직결에서 두 PC 의 주소 대역이 달라 연결이 안 될 때 쓰는 기능입니다. 어댑터 설정을 **임시로 바꾸는 기능이라 실제 PC 시험이 확인되지 않았습니다.** Windows 10 에서는 반드시 미리보기부터 하세요.

```
pcmigrate lan-setup --status                       # 직결 후보 어댑터 보기(읽기 전용)
pcmigrate lan-setup --role receiver --dry-run      # 새 PC 쪽: 무엇을 바꿀지 보기만
pcmigrate lan-setup --role sender --dry-run        # 구 PC 쪽
```

- 기본이 미리보기이고 `--yes` 를 붙여야 적용되는 것으로 README 에 적혀 있습니다. 옵션 이름과 동작은 `pcmigrate lan-setup --help` 로 먼저 확인하세요.
- 적용하면 관리자 권한(UAC)이 필요할 수 있고, 인터넷에 연결된 어댑터는 건드리지 않게 되어 있습니다(README 의 설명). 이 동작이 Windows 10 에서도 같은지는 확인되지 않았습니다.
- 가능하면 이 기능 대신 공유기를 거친 같은 사설 LAN, 또는 외장 드라이브(NTFS)로 옮기는 쪽이 시험이 더 된 길입니다.

## 4. 지원이 끝난 OS 에 업무 데이터를 옮길 때의 일반 주의

Windows 10 은 Microsoft 의 일반 지원이 종료되는 OS 입니다(종료일과 연장 보안 업데이트 조건은 Microsoft 공식 안내를 확인하세요). 이 도구의 기능과 별개로 다음을 지키세요.

- 업무 데이터(도면, 프로젝트, 사진)의 **원본은 구 PC 에 그대로 두고**, 새 PC 의 검사 결과가 '실패 0'일 때까지 지우거나 포맷하지 않습니다.
- 인터넷에 연결된 상태로 오래 쓸 PC 라면 보안 업데이트를 받을 수 있는지 먼저 확인하세요. 받을 수 없다면 이전 대상 PC 로 쓰는 것을 권하지 않습니다.
- 도구가 모은 목록 파일과 오류 로그에는 프로그램 목록과 파일 경로가 들어 있습니다. 공유하거나 저장소에 올리지 마세요.
- 라이선스는 복원되지 않고, Autodesk 계열·GPU 드라이버 의존 설정 등은 자동 복원이 되지 않습니다. 새 PC 가 Windows 10 이든 11 이든 같습니다.
- 소프트웨어 자체(Revit, AutoCAD 등)의 Windows 10 지원 여부는 각 제조사 공식 문서로 확인하세요. 이 도구의 규칙에는 그 정보가 들어 있지 않습니다.

## 5. 결과를 알려 주실 때

어느 단계까지 됐는지(`--version` → `check-rules` → `scan` → `export` → `import --dry-run` → `place` 미리보기 → `lan-setup --dry-run`)와 Windows 10 의 버전(`winver`), winget 유무, 실패한 단계의 오류 메시지를 적어 주세요. 개인 경로나 계정 이름은 지우고 보내세요.
