# PC 이전 현장 가이드
구 PC에서 새 Windows 11 PC로 옮길 때 현장에서 펼쳐 보는 한 장입니다. 체크 표시는 이 기기의 이 브라우저에만 저장됩니다. 이 파일은 인터넷 없이 열립니다(링크는 인터넷이 있을 때만 열림).
- [확인됨] 공식 문서를 열어 확인
- [요약·일반] 2차 요약이나 일반 지식
- [미확인] 제조사에 직접 확인
- [도구] 이 도구가 해 줌
- [직접] 사람이 직접
**이 도구가 하지 않는 것.** 프로그램 설치, 라이선스 복원, 브라우저 비밀번호와 쿠키, 카카오톡, 공인인증서 이전은 되지 않습니다. 구 PC의 파일은 지우지 않습니다. **새 PC에서 확인이 끝나기 전에는 구 PC를 지우거나 포맷하지 마세요.**

## 1. 가기 전 준비물
- [ ] 이전 도구 폴더 pcmigrate 전체 (exe만 따로 옮기면 안 됩니다. _internal 폴더까지 한 폴더째 USB에 복사하세요. 받은 빌드의 SHA-256 값도 따로 적어 두세요.)
- [ ] 외장 SSD나 HDD (NTFS 포맷)와 랜선 (랜선은 Cat5e 이상. 노트북이면 USB 랜 어댑터가 필요할 수 있습니다. 외장은 1TB 이상이고 원본 총량보다 5% 이상 여유가 있어야 합니다.)
- [ ] 설치 파일, 시리얼, 계정 정보 (프로그램은 새로 설치해야 하고 라이선스는 복원되지 않습니다. Autodesk 계정, Rhino 계정, MIDAS 인증 방식(Web 또는 USB 락) 등을 미리 확인하세요.)
- [ ] 동글과 동글 드라이버 설치 파일 (드라이버를 먼저 설치한 뒤 동글을 꽂습니다. 동글 제조사(Sentinel, HASP, CodeMeter)를 구 PC에서 확인하세요.)
- [ ] 새 PC의 접속 계정과 비밀번호 (랜선 방식) (비밀번호가 없는 로컬 계정은 네트워크 접속이 막힙니다. 공유 폴더 이름도 정해 두세요.)
- [ ] 전원 어댑터와 여분 케이블 (구 PC가 노트북이면 전원을 반드시 연결하세요.)
- [ ] 이 안내서를 휴대폰에서 열어 둡니다

## 2. 전체 순서

### 구 PC에서
1. [직접] 모든 프로그램 종료(Chrome 등 실행 중인 프로그램은 닫고 내보내기), 전원 연결 (열려 있는 파일은 복사와 검사에 실패합니다. 특히 Chrome/Edge가 실행 중이면 프로필 파일이 잠겨 설정 패키지에서 제외됩니다.)
2. [도구] 프로그램 조사와 해야 할 일 목록 저장 (구 PC 1~3단계. 목록 파일에 이 PC의 설치 목록이 들어 있으니 공유하지 마세요.)
3. [도구] [직접] 규칙 없는 프로그램 확인과 선택 (자동으로 포함되지 않습니다. 비밀 정보 경고 항목은 신중하게.)
4. [도구] 설정 패키지(.zip) 내보내기 (라이선스는 들어가지 않습니다.)
5. [도구] [직접] 데이터 복사 방식 선택(외장 또는 랜선)과 복사 (미리보기 후 복사. 끝나면 결과가 "실패 0"인지 확인합니다.)
6. [직접] 검증이 끝날 때까지 구 PC를 지우거나 포맷하지 않기

### 새 PC에서
1. [직접] Windows 업데이트와 장치 드라이버
2. [도구] [직접] 필요한 프로그램을 새로 설치하고 라이선스 인증 (동글 드라이버 포함. 가능하면 각 프로그램을 한 번 실행했다가 종료하세요.)
3. [도구] 데이터 복사와 검사 (랜선이면 새 PC에서 "검사 시작"으로 해시 목록과 비교합니다.)
4. [도구] 모든 프로그램을 종료하고 설정 패키지 미리보기를 확인한 뒤 복원 (덮어쓴 파일은 .bak으로 남습니다.)
5. [직접] 각 프로그램을 열어 설정, 템플릿, 글꼴, 플로터, 참조 경로 점검, 라이선스 상태 확인
6. [도구] [직접] 폴더 위치(드라이브 문자)가 구 PC와 같은지 확인 (도면의 외부 참조(XREF)와 데이터 단축은 절대 경로를 쓸 수 있습니다. 위치가 다르면 도구가 경고하지만 도면에서 참조가 이어지는지는 직접 열어 봐야 합니다.)

## 3. 외장 드라이브와 랜선, 어느 쪽으로
|  | 외장 드라이브 | 랜선(네트워크) |
|---|---|---|
| 흐름 | 구 PC → 외장 → 새 PC (복사 2번) | 구 PC → 새 PC 공유 폴더 (복사 1번) |
| 준비물 | NTFS 외장 SSD/HDD, USB 3.x 포트 | 랜선, 새 PC의 공유 폴더와 접속 계정 |
| 속도 | USB 3.x 외장 SSD는 수백 MB/s (장치마다 다름, [가정]) | 기가비트 이론 약 110MB/s, 100Mbps면 약 11MB/s ([이론]) |
| 검사 | 외장의 해시 목록으로 새 PC의 복사본을 비교 | 새 PC에서 목록과 로컬 복사본을 비교 (네트워크로 다시 읽지 않음) |
| 막히기 쉬운 곳 | FAT32(4GB 초과 파일 불가), USB 2.0 포트, 드라이브 문자 변경 | 공유 설정, 계정과 비밀번호, 네트워크 프로필, 링크 속도, 절전 |
| 장점 | 중간 사본이 하나 더 생김 | 복사가 한 번, 외장 구입 불필요 |

### 1TB 소요 시간 (속도는 [가정] 이며 이 PC에서 잰 값이 아닙니다)
| 지속 속도 | 상황 | 복사만 | 복사 + 검사 |
|---|---|---|---|
| 30MB/s | USB 2.0 외장 | 약 9.3시간 | 약 19~28시간 |
| 100MB/s | 기가비트 랜, HDD | 약 2.8시간 | 약 5.6~8.3시간 |
| 400MB/s | USB 3.x 외장 SSD | 약 0.7시간 | 약 1.4~2.1시간 |

작은 파일이 많으면 파일당 약 2ms(SSD 기준)가 더해지고, 실제는 2배 이상 달라질 수 있습니다. **먼저 실제 폴더 구조의 5~10GB를 시험 복사해서 속도를 재고 판단하세요.** 큰 파일 하나로만 시험하면 낙관적으로 나옵니다.

## 4. 랜선 준비 [일반 순서, 환경에 따라 다름]
같은 공유기나 스위치에 두 PC를 유선으로 연결하는 쪽이 가장 쉽습니다. 공유기가 없으면 랜선 하나로 두 PC를 직접 잇고 양쪽에 고정 IP를 정합니다(크로스 케이블은 필요 없습니다).

### ① 직결일 때 고정 IP (게이트웨이는 비움)
새 PC, 관리자 PowerShell (이름 "Ethernet"은 Get-NetAdapter로 확인)
```
New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress 192.168.50.2 -PrefixLength 24
```
구 PC
```
New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress 192.168.50.1 -PrefixLength 24
```

### ② 네트워크 프로필을 개인으로 [확인됨]
공용(Public)이면 PC가 숨겨지고 파일 공유에 쓸 수 없습니다.
```
Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private
```

### ③ 새 PC에 공유 폴더 만들기 [확인됨]
먼저 **비밀번호가 있는** 로컬 계정을 하나 만드세요. 비밀번호가 없는 로컬 계정은 네트워크 로그온이 안 됩니다. 공유 이름은 80자까지입니다.
관리자 PowerShell, 계정 이름은 실제 값으로
```
New-SmbShare -Name Migrate -Path D:\Migrate -ChangeAccess "PC이름\접속계정"
```
"파일 및 프린터 공유" 방화벽 규칙 이름은 한글 Windows에서 다를 수 있습니다
```
Get-NetFirewallRule | Select DisplayGroup -Unique
Enable-NetFirewallRule -DisplayGroup "File and Printer Sharing"
```

### ④ 구 PC에서 접속 시험
```
Test-NetConnection -ComputerName 192.168.50.2 -Port 445
net use Z: \\192.168.50.2\Migrate /user:PC이름\접속계정 *
```
탐색기 주소창에 \\192.168.50.2\Migrate를 열어 보고, 파일을 하나 만들었다 지워서 **쓰기**가 되는지 확인하세요. 접속이 꼬이면 net use * /delete로 기존 연결을 끊고 다시 시도합니다. [net use는 동작하지만 현재 Microsoft Learn에는 공식 문서가 없고 보관본뿐입니다.]

**계속 접속이 안 될 때 (시스템 오류 67 "네트워크 이름을 찾을 수 없습니다", 1222 "네트워크를 사용할 수 없습니다" 등).** 위 ①~③과 포트 445 확인을 한 번씩 했는데도 같은 오류가 나면 설정에 오래 매달리지 말고 **외장 SSD/HDD 방식으로 바꾸세요** (5장). 회사 보안 소프트웨어나 네트워크 정책이 공유 폴더 접속을 막는 경우는 현장에서 고치기 어렵습니다. 외장 방식은 네트워크 없이 같은 해시 검증과 이어받기를 그대로 씁니다.

### ⑤ 링크 속도
```
Get-NetAdapter | ft Name,LinkSpeed,Status
```
1000/1000이어야 합니다. 100Mbps로 나오면 케이블(Cat5e 이상)과 어댑터를 바꿔 보세요. 100Mbps면 1TB에 하루 이상 걸립니다.

### ⑥ 받는 새 PC의 절전을 끄세요
이 도구는 도구를 실행하는 PC(구 PC)의 절전만 막습니다. 받는 PC는 직접 전원을 연결하고 절전을 꺼야 공유가 끊기지 않습니다.
관리자, 분 단위(0은 안 함, powercfg /? 도움말 기준)
```
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```
어댑터 전원 절약은 장치 관리자의 네트워크 어댑터 속성에서 "전원 관리" 탭의 "전원을 절약하기 위해 이 장치를 끌 수 있음"을 해제하는 것이 확실합니다. 이전이 끝나면 절전 설정을 원래대로 돌리세요.
**Windows 11 24H2 Pro 이상**은 SMB 서명을 기본으로 요구하고 Home은 요구하지 않습니다. 서명을 못 하는 NAS나 게스트 접속은 실패할 수 있습니다. Microsoft는 서명을 끄는 우회를 권장하지 않습니다. [조사원 보고]

## 5. 외장 드라이브 준비
- **NTFS로 포맷하세요.** FAT32는 파일당 4GiB 제한이고 NTFS와 exFAT는 사실상 제한이 없습니다. ACL과 소유자는 NTFS만 지원합니다. [확인됨]
- 이 도구는 대상이 FAT32이고 4GB 초과 파일이 있으면 복사 전에 막습니다(분할 저장은 지원하지 않음).
- **포맷은 데이터를 모두 지웁니다.** 포맷 명령의 -WhatIf는 미리보기로 동작하지 않는다고 문서에 있습니다. 드라이브 문자만 보고 지우지 말고 크기와 모델을 먼저 확인하세요.
포맷 전에 먼저 (관리자 PowerShell)
```
Get-Disk
Get-Volume
```
E:는 예시입니다. 전체 삭제됩니다
```
Format-Volume -DriveLetter E -FileSystem NTFS -NewFileSystemLabel MIGRATE
```
- USB 3.x(파란색 SuperSpeed 포트)에 꽂으세요. USB 2.0은 실사용 약 35~40MB/s입니다. [일반]
- **BitLocker**: "장치 암호화"는 외장 USB를 암호화하지 않습니다. 암호화된 드라이브는 새 PC에서 암호나 복구 키가 필요하고, Microsoft는 잃어버린 키를 복구해 주지 못합니다. [확인됨]
- 분리할 때는 "하드웨어 안전하게 제거"를 쓰고, **새 PC에서 드라이브 문자가 바뀌면** 도구가 경고합니다.

## 6. 도구 명령 (명령줄을 쓸 때)
화면(pcmigrate-gui.exe)을 쓰면 아래 명령은 필요 없습니다. 명령줄은 pcmigrate 폴더에서 pcmigrate.exe로 실행합니다. 옵션은 pcmigrate.exe <명령> --help로 확인하세요.

### 조사와 체크리스트 (구 PC, 읽기만)
```
pcmigrate.exe scan
pcmigrate.exe checklist --format md --output C:\이전기록\체크리스트.md
pcmigrate.exe discover --output C:\이전기록\후보.md --output-selection C:\이전기록\선택.json
```
결과 파일에는 이 PC의 설치 목록이 들어 있으니 공유하거나 커밋하지 마세요. 저장소 폴더가 아닌 곳에 두세요.

### 설정 패키지
구 PC: 내보내기 (선택 파일의 include 가 true 인 항목만 추가됨)
```
pcmigrate.exe export --output E:\이전\설정.zip --extra C:\이전기록\선택.json
```
새 PC: 미리보기 먼저, 확인 후에만 --yes
```
pcmigrate.exe import E:\이전\설정.zip --dry-run
pcmigrate.exe import E:\이전\설정.zip --yes
```
--yes 없이 실행하면 아무것도 쓰지 않고 미리보기만 보여 준 뒤 종료 코드 2로 끝납니다. 규칙 없이 선택한 항목을 복원하려면 --allow-extra가 필요합니다.

- **내보내기 결과의 "읽지 못해 제외된 파일 N개"**: 실행 중인 프로그램(예: Chrome의 LOCK 파일)이 잠근 파일은 읽을 수 없어 패키지에서 빠지고, 결과에 개수와 예시 경로 5개가 나옵니다. 오류가 아니라 안내이며 패키지는 검증을 통과한 상태입니다. 해당 프로그램을 닫고 다시 내보내면 줄어듭니다. 실제로 쓸모없는 잠금 파일(LOCK, SingletonLock 등)은 늘 제외됩니다.
- **진행 표시**: 파일이 많으면 export와 import --yes는 몇 분 걸립니다. 10초마다 [진행] 줄이 나옵니다. import는 시작할 때 패키지 전체를 먼저 검사하므로 그동안은 줄이 없을 수 있습니다. 화면(GUI)에서는 현황 카드로 보입니다.
- **규칙 폴더 오류**: "규칙 폴더를 찾을 수 없습니다" 또는 "규칙 파일이 하나도 없습니다"가 나오면 exe 옆 _internal\rules 폴더가 지워진 것입니다. 이 상태로는 아무 프로그램도 감지하지 못하므로 도구가 종료 코드 1로 멈춥니다. 다시 내려받아 압축을 풀어 주세요.

### 대용량 복사
목록만 확인 (아무것도 쓰지 않음)
```
pcmigrate.exe transfer D:\프로젝트 E:\이전\프로젝트 --dry-run
```
외장으로 복사 (같은 PC 안의 두 디스크는 복사 후 바로 검사)
```
pcmigrate.exe transfer D:\프로젝트 E:\이전\프로젝트 --report C:\이전기록\복사결과.json
```
랜선: 원본 해시 목록을 만들며 복사, 대상 검사는 새 PC로 미룸
```
pcmigrate.exe transfer D:\프로젝트 \\192.168.50.2\Migrate\프로젝트 --manifest-out C:\이전기록\목록.jsonl
```
새 PC: 해시 목록과 로컬 폴더 비교 (읽기만, 결과 파일은 검사할 폴더 밖에)
```
pcmigrate.exe verify --manifest C:\이전기록\목록.jsonl --target D:\Migrate\프로젝트 --report C:\이전기록\검사결과.json
```
중단됐거나 일부 실패했을 때: 같은 명령을 다시 실행하면 이어받고, 실패한 것만 다시
```
pcmigrate.exe transfer D:\프로젝트 E:\이전\프로젝트 --retry-failed
```
원본을 건드리는 robocopy 옵션(/MIR, /PURGE, /MOV)은 이 도구가 쓰지 않습니다. 복사가 끝나면 대상 폴더에 .pcmigrate-state.json과 로그 파일이 남습니다. 외장 2단계(--use-manifest)와 랜선 1단계(--manifest-out)는 화면의 단계 안내를 따르는 편이 안전합니다.
**이 도구의 exe는 서명이 없습니다.** 처음 실행하면 "Windows의 PC 보호" 창이 뜰 수 있습니다. 받은 빌드와 함께 전달받은 SHA-256 값이 아래 명령 결과와 같은지 확인한 뒤 "추가 정보, 실행"을 누르세요.
```
certutil -hashfile pcmigrate-gui.exe SHA256
Get-FileHash .\pcmigrate-gui.exe -Algorithm SHA256
```

## 7. 막혔을 때
| 증상 | 확인하고 고칠 것 |
|---|---|
| 복사가 멈춘 것처럼 보임 | 잠긴 파일(열려 있는 도면, 프로그램)일 수 있습니다. robocopy의 재시도 기본값은 100만 회, 대기 30초입니다. 이 도구는 3회와 5초로 줄여 쓰지만, 먼저 모든 프로그램을 종료하세요. 닫은 뒤 "실패한 것만 다시 시도". |
| 종료 코드가 1 또는 3 | 실패가 아닙니다. robocopy는 **8 이상일 때만** 실패입니다. [확인됨] |
| 공유 폴더에 접속이 안 됨 | 비밀번호 없는 계정인지, 네트워크 프로필이 공용인지, 방화벽의 파일 및 프린터 공유가 켜졌는지, 두 PC의 IP 대역이 같은지, ping과 포트 445를 확인하세요. 이미 다른 계정으로 연결돼 있으면 net use * /delete. |
| 속도가 너무 느림 | USB 2.0 포트(실사용 약 35~40MB/s 이하)이거나 링크 속도가 100Mbps일 수 있습니다. 케이블과 포트를 바꿔 보세요. 느린 HDD/USB는 --workers 1 --copy-threads 1이 나을 수 있습니다. |
| 4GB 넘는 파일이 복사 안 됨 | 대상이 FAT32입니다. NTFS로 포맷하거나 다른 드라이브를 쓰세요. |
| "경로가 너무 깁니다" | 260자 제한입니다. 이 도구는 읽기와 검사에 긴 경로 접두를 쓰지만, 그래도 읽지 못하면 폴더를 짧은 경로로 옮기세요. [확인됨] |
| OneDrive 파일이 건너뛰어짐 | 클라우드 전용 파일은 기본으로 건너뛰고 cloud_only로 기록됩니다. 복사 전에 폴더를 우클릭해 "이 디바이스에 항상 유지"로 내려받은 뒤 복사하거나 --include-cloud를 쓰세요(대량 다운로드가 생깁니다). 실제 OneDrive PC에서의 동작은 아직 시험하지 못했습니다. |
| 드라이브 문자가 바뀜 | 도면의 외부 참조가 끊길 수 있습니다. 디스크 관리에서 "드라이브 문자 및 경로 변경"으로 구 PC와 맞추세요. C:는 바꾸지 마세요. |
| 대상에 `.bak` 파일이 생김 | 대상 폴더에 같은 이름의 다른 파일이 이미 있어서, 덮어쓰기 전에 기존 파일을 `이름.bak`으로 옮겨 둔 것입니다(내용은 그대로). 필요 없으면 확인 후 지우세요. 복사 전 미리보기가 몇 개인지 알려 줍니다. |
| 검사에서 "내용 다름" | 복사 도중 파일이 바뀌었거나 복사본이 손상됐을 수 있습니다. 프로그램을 닫고 --strict로 다시 실행하면 어긋난 파일만 원본에서 다시 복사하고 기존 사본은 .bak로 남깁니다. |
| 예상하지 못한 오류가 났다고 나옴 | 오류 내용이 %LOCALAPPDATA%\pcmigrate\logs\errors-날짜.log 에 기록됩니다(쓸 수 없으면 %TEMP%\pcmigrate-logs). 명령줄에서 pcmigrate.exe logs 는 로그 폴더와 최근 오류 요약을, pcmigrate.exe logs --open 은 탐색기로 폴더를 엽니다. 화면에서는 오류 대화상자의 "로그 폴더 열기"와 "오류 내용 복사" 버튼을 쓰세요. 로그에는 파일 경로가 들어 있으니 보내기 전에 확인하세요. |
| 공유 폴더 접속 오류 67 또는 1222 | 4장 ④의 안내를 확인해도 계속되면 외장 SSD/HDD 방식으로 바꾸세요(5장). |
| exe가 안 열림 | SmartScreen 창에서 "추가 정보, 실행". 회사 백신이 막으면 폴더를 예외로 등록하거나 담당자에게 허용을 요청하세요. _internal 폴더가 같이 있는지 확인하세요. |

## 8. 구 PC를 지우기 전 [일반 권장]
- [ ] 해시 검사 결과가 "실패 0" (누락, 내용 다름이 없어야 합니다.)
- [ ] 새 PC에서 샘플 파일과 프로그램을 열어 봄 (외부 참조(XREF)가 이어지는지, 템플릿과 글꼴이 맞는지.)
- [ ] 라이선스를 해제(로그아웃, 비활성화)함 (해제하지 않고 포맷하면 재활성화가 막힐 수 있습니다. 해제 절차는 제조사마다 다르니 제조사 안내를 확인하세요.)
- [ ] 브라우저 북마크와 비밀번호 관리자, 2단계 인증(OTP) 이전
- [ ] 프린터와 플로터 드라이버 확보
- [ ] 메일(PST) 백업과 공인인증서 이전 (인증서는 은행과 기관 안내를 따르세요.)
- [ ] BitLocker 복구 키 확인
- [ ] 새 PC를 며칠 정상 사용해 본 뒤에 초기화 ("데이터 정리" 초기화도 정부나 업계 삭제 표준을 충족하지는 않는다고 문서에 명시되어 있습니다.)

## 9. 공식 링크
모두 열어서 확인했습니다(2026-10-23). 아래 페이지의 영문 주소에서 en-us를 ko-kr로 바꾸면 한국어 페이지가 열리는 경우가 많습니다(문서에 따라 영문이 나옵니다). 링크는 새 탭에서 열립니다.

### 복사와 검증
- Robocopy <https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/robocopy> (옵션, 종료 코드)
- Get-FileHash <https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/get-filehash>
- certutil <https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/certutil> (-hashfile)
- 최대 경로 길이 제한 <https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation> (260자, LongPathsEnabled)
- Volume Shadow Copy Service <https://learn.microsoft.com/en-us/windows-server/storage/file-server/volume-shadow-copy-service>

### 저장장치
- 파일 시스템 비교 <https://learn.microsoft.com/en-us/windows/win32/fileio/filesystem-functionality-comparison> (NTFS, exFAT, FAT32)
- NTFS 개요 <https://learn.microsoft.com/en-us/windows-server/storage/file-server/ntfs-overview>
- exFAT 사양 <https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification>
- format <https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/format>, Format-Volume <https://learn.microsoft.com/en-us/powershell/module/storage/format-volume>, chkdsk <https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/chkdsk>
- 하드웨어 안전하게 제거 <https://support.microsoft.com/windows/hardware/safely-remove-hardware-in-windows>
- USB 3.2 (USB-IF) <https://www.usb.org/superspeed-usb> (속도만, 구 명칭 대응표는 아님)
- BitLocker 개요 <https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/>
- BitLocker 복구 키 찾기 <https://support.microsoft.com/en-us/windows/finding-your-bitlocker-recovery-key-in-windows-6b71ad27-0b89-ea08-f143-056f5ab347d6>

### 네트워크
- SMB 보안 개선 <https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-security>
- SMB 서명 동작 제어 <https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-signing>
- SMBv1, v2, v3 확인과 켜고 끄기 <https://learn.microsoft.com/en-us/windows-server/storage/file-server/troubleshoot/detect-enable-and-disable-smbv1-v2-v3>
- SMB2/3 게스트 로그온 <https://learn.microsoft.com/en-us/windows-server/storage/file-server/enable-insecure-guest-logons-smb2-and-smb3>
- 빈 암호 로컬 계정 정책 <https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/accounts-limit-local-account-use-of-blank-passwords-to-console-logon-only> (보관 문서)
- New-SmbShare <https://learn.microsoft.com/en-us/powershell/module/smbshare/new-smbshare>
- Set-NetConnectionProfile <https://learn.microsoft.com/en-us/powershell/module/netconnection/set-netconnectionprofile>
- New-NetIPAddress <https://learn.microsoft.com/en-us/powershell/module/nettcpip/new-netipaddress>
- Get-NetAdapter <https://learn.microsoft.com/en-us/powershell/module/netadapter/get-netadapter>
- Test-NetConnection <https://learn.microsoft.com/en-us/powershell/module/nettcpip/test-netconnection>
- Enable-NetFirewallRule <https://learn.microsoft.com/en-us/powershell/module/netsecurity/enable-netfirewallrule>
- Set-NetAdapterPowerManagement <https://learn.microsoft.com/en-us/powershell/module/netadapter/set-netadapterpowermanagement>
- powercfg 옵션 <https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options>
- Windows에서 네트워크 파일 공유 <https://support.microsoft.com/en-us/windows/experience/connectivity-networking/file-sharing-over-a-network-in-windows>
- net use <https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/gg651155(v=ws.11)> (보관 문서만 있음)

### 새 PC와 프로그램 목록
- 권장 하드웨어 드라이버 자동 받기 <https://support.microsoft.com/en-us/windows/hardware/drivers/automatically-get-recommended-and-updated-hardware-drivers>
- Windows 정품 인증 <https://support.microsoft.com/en-us/windows/activation/activate-windows>, Slmgr.vbs 옵션 <https://learn.microsoft.com/en-us/windows-server/get-started/activation-slmgr-vbs-options>
- 사용자 계정 관리 <https://support.microsoft.com/en-us/windows/security/identity-signin/manage-user-accounts-in-windows>
- 드라이브 문자 변경 <https://learn.microsoft.com/en-us/windows-server/storage/disk-management/change-a-drive-letter>
- winget export <https://learn.microsoft.com/en-us/windows/package-manager/winget/export>, import <https://learn.microsoft.com/en-us/windows/package-manager/winget/import>, configure <https://learn.microsoft.com/en-us/windows/package-manager/winget/configure>
- Windows 백업 <https://support.microsoft.com/en-us/help/17143> (설정과 앱 목록만, CAD 설정은 범위 밖)
- OneDrive 파일 주문형 <https://support.microsoft.com/en-us/office/0e6860d3-d9f3-4971-b321-7092438fb38e>
- OneDrive 폴더 백업 <https://support.microsoft.com/en-us/office/back-up-your-folders-with-onedrive-d61a7930-a6fb-4b95-b28a-6552e77c3057>

### 토목과 건축 프로그램 (제조사 문서)
- AutoCAD: 같은 릴리스 간 설정 내보내기와 가져오기 <https://help.autodesk.com/cloudhelp/2025/ENU/AutoCAD-Customization/files/GUID-5EDF557F-098A-4F81-8104-3F0890B5CCC2.htm>
- 참조 도면(XREF) 경로 <https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-LT/files/GUID-AB6D738D-DD2F-41ED-9B56-45B64A467CF8.htm> (AutoCAD LT 문서)
- 전송 패키지(Transmittal) <https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-LT/files/GUID-21C65B7B-6E50-46D6-B218-54FF88C17944.htm> (AutoCAD LT 문서)
- 옵션, 파일 탭 <https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-LT/files/GUID-F95EE827-7567-44EA-9D69-E9D0D37EE13F.htm> (AutoCAD LT 문서)
- Revit 애드인 등록 <https://help.autodesk.com/cloudhelp/2016/ENU/Revit-API/files/GUID-4FFDB03E-6936-417C-9772-8FC258A261F7.htm> (2016 API 문서)
- Revit 중앙 모델의 로컬 사본 <https://help.autodesk.com/cloudhelp/2014/ENU/Revit/files/GUID-A3B005C4-5988-4CE6-9772-92F4F3FC68BC.htm> (2014 문서)
- Rhino 8: OptionsExport <https://docs.mcneel.com/rhino/8/help/en-us/commands/optionsexport.htm>, Yak 패키지 <https://developer.rhino3d.com/guides/yak/installing-and-managing-packages/>, 라이선스 <https://www.rhino3d.com/features/administration/licensing>
- Bentley: WorkSpace 설정 파일 <https://docs.bentley.com/LiveContent/web/MicroStation%20Help-v28/en/GUID-73CEB803-2983-441F-8E14-D8867C3098B9.html>, 라이선스 구성 <https://www.bentley.com/en/licensing-configuration>
- QGIS 사용자 프로필 <https://docs.qgis.org/latest/en/docs/user_manual/introduction/qgis_configuration.html>
AutoCAD LT와 오래된 Revit 문서는 최신 AutoCAD, Civil 3D, Revit과 완전히 같은 내용인지 확인되지 않았습니다. 개념 설명용으로만 보고, 실제 동작은 현장 버전의 도움말로 확인하세요.

### 동글과 보안
- Sentinel LDK Run-time Environment <https://docs.sentinel.thalesgroup.com/ldk/rte.htm>, Admin Control Center 실행 <https://docs.sentinel.thalesgroup.com/ldk/LDKdocs/SPNL/LDK_SLnP_Guide/Distributing/Admin_Control_Center/110-LaunchingAdmin.htm>
- SmartScreen FAQ <https://feedback.smartscreen.microsoft.com/smartscreenfaq.aspx>
- Windows 보안의 앱 및 브라우저 컨트롤 <https://support.microsoft.com/en-us/windows/security/windows-security/app-browser-control-in-the-windows-security-app>
- CNG DPAPI <https://learn.microsoft.com/en-us/windows/win32/seccng/cng-dpapi> (브라우저 비밀번호와 쿠키가 PC 간 복원되지 않는 이유와 맞는 설명)
- PC 초기화 <https://support.microsoft.com/en-us/windows/experience/backup-recovery/reset-your-pc>, PC 재활용이나 판매 전 안내 <https://support.microsoft.com/en-us/accounts-billing/security/before-you-recycle-sell-or-gift-your-xbox-or-windows-pc>
- CISA: 백업 옵션 (3-2-1 원칙) <https://www.cisa.gov/sites/default/files/publications/data_backup_options.pdf> (이 링크만 제 확인에서 403이 나왔고 조사원이 본문을 확인했습니다)

## 10. 확인하지 못한 것, 제조사에 물어볼 것
**이 도구의 규칙 대부분은 실제 건축, 토목 PC에서 한 번도 시험하지 못했습니다.** 규칙의 설정 경로는 공식 문서와 2차 요약을 근거로 했고 각 규칙의 notes에 근거 등급이 적혀 있습니다. 구 PC에서 scan과 discover 결과를 보고 빠졌거나 틀린 항목을 확인하세요.

### 제조사나 담당자에게 직접 확인
- **Autodesk**: 현행 로그아웃과 해제 정책, 동시 사용 대수, 네트워크 라이선스 서버 이전과 재발급, Civil 3D 설정 이전 범위, 데이터 단축과 Vault 설정 이전, Revit.ini 위치와 로컬 캐시 복사 여부. (Autodesk 지원 기사 페이지는 열리지 않아 내용을 읽지 못했습니다.)
- **McNeel(Rhino)**: Rhino 8 설정 폴더 위치(공식 문서), 툴바(RUI) 이전. 라이선스는 기존 위치에서 제거한 뒤 새 위치에 추가합니다.
- **Bentley**: SES 로그인과 오프라인 check-out 최신 절차, OpenRoads 설정 위치.
- **MIDAS**: Web 인증이나 USB 락의 해제와 이전 절차(공식 페이지를 하나도 열람하지 못함).
- **Esri(ArcGIS Pro)**: user.config의 정확한 경로와 대상 버전, Single Use 승인 절차.
- **Thales(Sentinel), Wibu(CodeMeter)**: Windows 11 지원 버전과 보안 패치 버전. CodeMeter 구버전에는 권한 상승 취약점이 보고되어 최신 빌드를 권장합니다. [검색 요약]
- **사내 IT**: 서명 없는 exe 실행 허용 정책. **은행**: 공인인증서와 금융인증서 이전.

### 현장에서 시험해야 확정되는 것
- OneDrive 클라우드 전용 파일이 이 도구에서 실제로 건너뛰어지는지.
- 깨끗한 새 Windows와 사무실 보안 환경에서 exe가 정상 동작하는지.
- 실제 외장 드라이브 분리, 네트워크 끊김 후 이어받기.
- QGIS 프로필 폴더 전체 복사가 같은 버전에서 안전한지.
조사 기준일 2026-10-23. 위 속도와 시간은 이론값이나 가정이고, 링크는 이날 열어 확인했습니다. 이 페이지에는 이 PC의 설치 목록이나 개인 정보가 들어 있지 않습니다.
