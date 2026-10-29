# 테스트 파일 배치

개발 단계 번호(`test_stageNN_*`)로 되어 있던 테스트 파일을 기능 기준 이름으로 바꾼 매핑표입니다. 파일 내용(테스트 함수 이름·개수)은 그대로이며 파일 간 import 만 새 이름으로 갱신했습니다(2077개 유지). 단계 번호는 찾기 어렵고 기능을 알려 주지 않아 바꿨습니다.

| 옛 파일 | 새 파일 | 설명 |
|---|---|---|
| `tests/test_stage4.py` | `tests/test_checklist_winget.py` | 체크리스트/winget 매칭/strict 이어받기 (단계 4) |
| `tests/test_stage8.py` | `tests/test_cli_api_env_encoding.py` | strict 복구, 환경변수 경로 덮어쓰기, UTF-8 출력, api 함수 (단계 8) |
| `tests/test_rules_stage5.py` | `tests/test_rules_extra_programs.py` | 추가 프로그램 규칙(Rhino 등) (단계 5) |
| `tests/test_stage12.py` | `tests/test_transfer_safety_manifest.py` | 모드 B 전송: 절전 방지/진행률/검증/사전 검사/해시 목록 |
| `tests/test_stage12_gui.py` | `tests/test_gui_transfer_modes.py` | 전송 방식(외장/랜선/같은 PC)별 GUI 흐름 |
| `tests/test_stage14_export.py` | `tests/test_export_locked_files.py` | 잠긴 파일이 있어도 내보내기 완주 |
| `tests/test_stage14_gui_errors.py` | `tests/test_gui_error_log.py` | GUI 오류 로그 기록/열기/복사 |
| `tests/test_stage14_repo.py` | `tests/test_repo_hygiene.py` | 저장소 위생(.gitignore/.gitattributes) |
| `tests/test_stage14_safety.py` | `tests/test_error_log_safety_guards.py` | 오류 로그, 입력 검증, 안전 가드 |
| `tests/test_stage14_status.py` | `tests/test_gui_status_card.py` | 현황 카드와 전체 단계 표시 |
| `tests/test_stage16a.py` | `tests/test_restore_transfer_safety.py` | 복원/전송 안전성 리뷰 항목 |
| `tests/test_stage16b_cli.py` | `tests/test_cli_rules_errors_import_progress.py` | 규칙 폴더 오류와 CLI import 진행 표시 |
| `tests/test_stage16b_gui.py` | `tests/test_gui_controller_polling_guards.py` | GUI 컨트롤러 폴링/상태 복구/가드 |
| `tests/test_stage16b_gui_tk.py` | `tests/test_gui_tk_tick_reschedule.py` | tk 화면 _tick 재예약 스모크 |
| `tests/test_stage16c.py` | `tests/test_display_packaging_overwrite.py` | 화면 표시(미룸/rejected 등), build_exe 보호, 덮어쓰기 보존 |
| `tests/test_stage18_savepoint.py` | `tests/test_export_savepoint_resume.py` | 내보내기 저장점 이어받기와 압축 생략 |
| `tests/test_stage19a.py` | `tests/test_review_regressions.py` | 정밀 리뷰/종단 시험 회귀 재현 |
| `tests/test_stage19b_ui.py` | `tests/test_gui_beginner_friendly.py` | 처음 쓰는 사람 친화 화면(U-1~U-18) |
| `tests/test_stage19c.py` | `tests/test_denied_checklist_messages.py` | denied 표시, 체크리스트 안내, 오류 문구 |
| `tests/test_stage20a_misc.py` | `tests/test_export_stage_dir_gitconfig.py` | 내보내기 임시 폴더 위치 옵션, .gitconfig 경고 |
| `tests/test_stage20a_split.py` | `tests/test_split_4gb.py` | 4GB 분할 파일과 join |
| `tests/test_stage20b_gui.py` | `tests/test_gui_auto_run_options.py` | GUI 그림 글자 감지, 분할/임시 위치 연결, 추천대로 한 번에 |
| `tests/test_stage21_select_all.py` | `tests/test_gui_select_all.py` | 구 PC 보이는 항목 전체 선택/해제 |
| `tests/test_stage22_install_check.py` | `tests/test_install_check.py` | 새 PC 설치 확인(install_check) |
| `tests/test_stage23_backend.py` | `tests/test_coverage_table_find_program.py` | 캐시 제외, 설정 확인표(coverage), 프로그램 설정 찾기 |
| `tests/test_stage23_gui.py` | `tests/test_gui_candidates_coverage.py` | GUI 후보 표 크기/합계, 설정 확인표, 화면 전환 |
| `tests/test_stage24_gui.py` | `tests/test_gui_export_retry.py` | GUI 읽지 못한 파일 다시 담기 |
| `tests/test_stage24_retry.py` | `tests/test_export_retry_failed.py` | 내보내기 실패 파일 재시도(package/api/CLI) |
| `tests/test_stage25_gui.py` | `tests/test_gui_install_dir.py` | GUI 설치 폴더 설정 후보, 신 PC 미리보기 동의 |
| `tests/test_stage25_install_dir.py` | `tests/test_install_dir.py` | 설치 폴더 설정 내보내기/복원 |
| `tests/test_stage28a_field_fixes.py` | `tests/test_field_fixes_cancel_smoothing.py` | 현장 피드백: 민감 항목 제외 묶음, 속도 평활화, 중지/강제 중단 |
| `tests/test_stage28b.py` | `tests/test_per_program_restore_undo.py` | 허용 방식 내보내기, 프로그램별 복원 선택, 되돌리기 |
| `tests/test_stage28c.py` | `tests/test_find_data_multi_transfer.py` | 내 파일 자동 찾기, 다중 원본 전송, 정크 제외 |
| `tests/test_stage29_lan.py` | `tests/test_lan_transfer.py` | LAN 직접 전송(receive/send) |
| `tests/test_stage30a_files.py` | `tests/test_gui_my_files_controller.py` | 내 파일 옮기기 컨트롤러/상태 |
| `tests/test_stage30a_gui_tk.py` | `tests/test_gui_my_files_tk.py` | 내 파일 옮기기 tk 화면 |
| `tests/test_stage30b.py` | `tests/test_gui_screens_4_7.py` | 화면 4~7(목록 밖 프로그램, 중지, 프로그램별 복원/되돌리기, 마지막 화면) |
| `tests/test_stage31_install.py` | `tests/test_install_dir_interactions.py` | 설치 폴더 설정과 선택/되돌리기/이어받기 상호작용 |
| `tests/test_stage31_no_config.py` | `tests/test_no_config_found.py` | '설정 파일 없음' 판정 |
| `tests/test_stage32.py` | `tests/test_settings_filter_excluded_report.py` | 허용 방식에서 빠진 파일 사유 기록/표시 |
| `tests/test_stage32_gui_tk.py` | `tests/test_gui_coverage_box_tk.py` | 설정 확인표 CoverageBox tk 시험 |
| `tests/test_stage33.py` | `tests/test_selftransfer.py` | 자체 이전 필요 프로그램 목록 |
| `tests/test_stage33_gui_tk.py` | `tests/test_gui_selftransfer_tk.py` | GUI 자체 이전 필요 구역 |
| `tests/test_stage34.py` | `tests/test_start_cards_settings_bundle.py` | 시작 화면 카드 순서, 설정 묶음 함께 보내기, 받은 폴더 찾기 |
| `tests/test_stage34_gui_tk.py` | `tests/test_gui_start_cards_tk.py` | 시작 화면/설정 묶음 줄 tk 시험 |
| `tests/test_stage35.py` | `tests/test_dest_rel.py` | 항목별 대상 상대 경로(dest_rel) |
| `tests/test_stage37_gui_tk.py` | `tests/test_gui_collapse_empty_tk.py` | 빈 결과/안내 영역 접힘 tk 시험 |
| `tests/test_stage38_winget_select.py` | `tests/test_winget_select_export.py` | 설치 확인에서 골라 winget.json 내보내기 |
| `tests/test_stage39_gui.py` | `tests/test_gui_ethernet_setup.py` | 이더넷 주소 맞추기 컨트롤러/화면 |
| `tests/test_stage39_net.py` | `tests/test_ethernet_setup.py` | 이더넷 주소 임시 맞추기(net_setup) |

## 테스트에서 값을 바꿀 때(패치) 주의

`package.py`, `transfer.py`, `gui/controller.py` 는 분해 후 재export 파사드입니다. 테스트에서 `package.X` 처럼 파사드의 이름을 바꾸면 실제로 그 값을 쓰는 하위 모듈(`package_*`, `transfer_*`, `controller_*`)에는 반영되지 않아 오류 없이 무력화될 수 있습니다. 반드시 `tests/conftest.py` 의 `patch_pkg` / `set_pkg` / `patch_xfer` / `patch_ctl` 헬퍼를 쓰세요(그 이름을 가진 모든 하위 모듈에 같이 적용됩니다).
