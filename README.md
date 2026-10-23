# pc-migration-tool (PC 이전 도우미)

구 Windows PC의 업무용(건축 CAD/BIM) 환경을 새 PC로 안전하게 옮기는 Python CLI입니다.
원본은 읽기만 하고, 복사 후 SHA-256으로 검증하며, 덮어쓸 파일은 `.bak`으로 백업합니다.
설계 방침과 요구사항은 [CLAUDE.md](CLAUDE.md)를 보세요.

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

`transfer` 는 **대상 폴더에 두 개의 파일을 남깁니다**(원본에는 아무것도 쓰지 않습니다):

| 파일 | 용도 | 위치 바꾸기 |
|---|---|---|
| `.pcmigrate-state.json` | 이어받기 진행 상태(검증 끝난 파일의 크기/수정 시각/해시) | `--state-file <경로>` |
| `.pcmigrate-robocopy.log` | robocopy 로그 | `--log <경로>` |

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

## 개인정보 주의

`checklist`, `export`, `transfer --report` 의 출력 파일에는 이 PC에 설치된 프로그램 목록과 파일 경로가 들어갑니다.
저장소 안에 두거나 **커밋하지 마세요**(공유 전에 내용을 확인하세요). 라이선스는 복원되지 않습니다.

## 테스트

```
python -m pip install pytest
python -m pytest -q
```

테스트는 `tmp_path`와 mock만 사용하며 실제 레지스트리, winget, 사용자 파일은 건드리지 않습니다.
