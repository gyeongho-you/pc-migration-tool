# 예외 처리 감사 (`except Exception` / `except BaseException`)

대상: `src/` 전체의 광범위 예외 처리 59곳(bare `except:` 는 없음). 줄 번호는 수정 전(v0.3.1, `package.py`·`transfer.py`·`gui/controller.py` 분해 전) 기준이라 지금 파일 위치와 다릅니다. 내용으로 찾으세요. 수정한 곳은 조치 열에 표시했습니다(분류 표는 같은 파일의 연속 위치를 묶어 적은 곳이 있어 행 수와 59곳이 일치하지는 않습니다).

분류
- **A. 정리 후 재전파**: `BaseException` 으로 받아 임시 파일/프로세스를 정리하고 `raise`. 오류를 삼키지 않으므로 의도된 것.
- **B. 파일 단위 격리·보고**: 오류를 결과/이벤트/오류 화면으로 전달. 삼키지 않음.
- **C. 로깅 없이 삼킴**: 오류를 버리고 계속 진행. 버그가 숨을 수 있음.
- **D. 의도된 삼킴(로깅 불필요)**: 부가 기능 실패가 본 작업을 막지 않게 하며, 원인이 예측 가능(비 Windows, 권한 등)하거나 이미 다른 경로로 알림.

| 위치 | 분류 | 조치 |
|---|---|---|
| checklist.py:472 | A | 없음 |
| cli.py:1119 | B (오류 로그 기록 후 쉬운 문구) | 없음 |
| copier.py:190 | A (.bak 롤백) | 없음 |
| copier.py:229 | B (파일 단위 격리) | 없음 |
| copier.py:395 | A | 없음 |
| copier.py:440 | B (파일 단위 격리) | 없음 |
| detect.py:114 | A (프로세스 종료) | 없음 |
| engine.py:45 | A (대기 작업 취소) | 없음 |
| errorlog.py:185 | D (로그 실패가 원래 오류를 가리면 안 됨. 로그 모듈 자신이라 더 기록할 곳 없음) | 없음 |
| failure.py:107 | D (확인용, '모름'으로 처리). 다소 넓음 | 없음(OSError 로 좁히면 동작이 바뀔 수 있어 보류) |
| find_data.py:265 | C | **수정**: errorlog.record 추가, 반환값 그대로 |
| gui/app.py:51 | D (DPI 설정 실패는 외형만 영향) | 없음 |
| gui/app.py:270 | D (되돌리기 안내 실패, 화면 단추로 가능) | 없음 |
| gui/app.py:468 | B (표시 중 오류는 errorlog.record) | 없음 |
| gui/app.py:569 | B (report_callback_exception 으로 전달) | 없음 |
| gui/app.py:650 | D (알림 실패가 종료 코드를 바꾸면 안 됨) | 없음 |
| gui/app.py:670 | B (errorlog.record) | 없음 |
| gui/controller.py:268 | C | **수정**: errorlog.record("GUI 요약") 추가 |
| gui/controller.py:446 | D (탐색기 열기 실패는 False 반환으로 호출자에 알림) | 없음 |
| gui/controller.py:464 | D (기본 폴더 조회 실패, 마지막 폴더로 대체). 다소 넓음 | 없음 |
| gui/controller.py:470 | D (같은 목적) | 없음 |
| gui/controller.py:503 | D (드라이브 조회 실패는 찾기 범위만 줄임) | 없음 |
| gui/controller.py:543 | C | **수정**: errorlog.record("설정 묶음 자동 찾기") 추가 |
| gui/controller.py:572 | B (_callback_failed 로 기록) | 없음 |
| gui/controller.py:588 | D (대화상자 실패, 오류는 이미 앞에서 기록) | 없음 |
| gui/controller.py:603 | D (로그 실패가 오류 표시를 막으면 안 됨) | 없음 |
| gui/controller.py:697 | B (self.log.exception) | 없음 |
| gui/controller.py:1724 | D (조회 실패는 안내 생략, 결과를 캐시). 다소 넓음 | 없음 |
| gui/controller.py:2112 | B (report_error 로 기록·표시) | 없음 |
| gui/files_controller.py:257 | D (기본 위치 조회 실패, 대체 경로 사용) | 없음 |
| gui/files_controller.py:711 | B (self.log.warning) | 없음 |
| gui/net_controller.py:42 | C | **수정**: errorlog.record 추가 |
| gui/net_controller.py:64 | C (예상 밖 오류를 '어댑터 없음' 문구로만 표시) | **수정**: errorlog.record 추가, 표시 문구 그대로 |
| gui/net_controller.py:154 | D (바쁠 때 다음 기회, 단추로 되돌릴 수 있음) | 없음 |
| gui/runner.py:52 | B (오류 큐로 전달 후 UI 에서 표시) | 없음 |
| gui/runner.py:97 | B (errors 에 모아 훅으로 보고) | 없음 |
| gui/runner.py:105 | D (오류 보고 훅 자체의 실패) | 없음 |
| gui/views/install.py:135, 162 | B (report_error → show_error) | 없음 |
| gui/views/net_section.py:106, 129 | B (GuardError 등 메시지를 경고창에 표시) | 없음 |
| gui/views/old_pc.py:589 | B | 없음 |
| gui/views/transfer.py:480 | B | 없음 |
| lan_transfer.py:243 | C (화면 콜백 오류가 사라짐) | **수정**: errorlog.record 추가, 전송은 계속 |
| lan_transfer.py:295 | B (error 이벤트로 알림) | 없음 |
| lan_transfer.py:869 | B (LanError 로 변환, `from e`) | 없음 |
| lan_transfer.py:937 | B (큐로 예외 전달) | 없음 |
| lan_transfer.py:1055 | A | 없음 |
| manifest.py:261 | A | 없음 |
| net_setup.py:138 | D (비 Windows/권한 확인 불가는 '관리자 아님') | 없음 |
| net_setup_cli.py:116 | C | **수정**: errorlog.record 추가, 명령은 계속 |
| package.py:1346 | A | 없음 |
| package.py:1839 | C (프로그램 목록을 못 읽어 검사가 약해짐) | **수정**: errorlog.record 추가, 동작 그대로 |
| power.py:43 | B (경고 메시지 전달) | 없음 |
| power.py:62 | D (절전 방지 해제 실패, 상태만 failed) | 없음 |
| split.py:201, 239, 677 | A | 없음 |
| transfer.py:644 | A/B (롤백 후 원인 분류해 보고) | 없음 |

## 수정 요약
수정 8곳(find_data, gui/controller 2, gui/net_controller 2, lan_transfer, net_setup_cli, package). 모두 `errorlog.record(e, 문맥)` 한 줄을 더했고(이 함수는 어떤 경우에도 예외를 던지지 않음), 반환값·표시 문구·진행 흐름은 바꾸지 않았습니다.

## 남긴 항목
- D 중 "다소 넓음"으로 표시한 곳은 예외 종류를 좁히면 동작이 바뀔 수 있어 남겼습니다. 좁히려면 각 호출이 던지는 예외를 조사하고 시험을 추가해야 합니다.
- `gui/controller.py`(매우 큼)의 분해는 이번 범위가 아닙니다.
