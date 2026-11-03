"""stage85: 화면 용어 통일(v0.3.7 안 2)과 시작 화면 1단계(안 3) 시험.

- 같은 기능을 부르는 옛 이름('대용량 복사', '큰 파일 옮기기', '랜선' 등)과 화면에 남은 전문어가 사용자 문구에 다시 나타나지 않는다.
- 기준 용어: '내 파일 옮기기', 연결 방법은 '두 PC를 바로 연결 / 외장 드라이브 / 공유 폴더', 케이블은 '네트워크 케이블'.
- 시작 화면: 어느 PC 앞인지 라벨이 카드 맨 위, 두 PC 에서 각각 실행한다는 안내가 소개 글에 있다(기능·단추·순서는 그대로).
"""
import re
from pathlib import Path

import pytest

from pcmigrate.gui import state as S
from pcmigrate.gui import texts as T

from test_gui_texts import USER_CONSTANTS, flatten

OLD_NAMES = ("랜선", "대용량", "큰 파일 옮기기", "예전 이름", "예전의 '대용량", "기존 방식")
JARGON = ("정션", "저장점", "PowerShell", "(winget)", "LAN 포트", "USB 랜", "랜 어댑터")
PORT = re.compile(r"(USB|LAN)\s*[0-9.x]*\s*포트")
# Windows 설정 화면의 실제 메뉴 이름이라 그대로 두는 '이더넷'(사용자가 그 이름을 찾아가야 한다)
ETHERNET_OK = re.compile(r"(설정 > 네트워크( 및 인터넷)? > 이더넷|이더넷 > IP 할당|이더넷을 '자동'으로)")


def values_of(v):
    """사용자에게 보이는 글(딕셔너리는 값만: 키는 내부 이름이다)."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from values_of(x)
    elif isinstance(v, (tuple, list)):
        for x in v:
            yield from values_of(x)


def test_no_old_feature_names_in_user_visible_texts():
    found = [(n, w, t[:40]) for n, v in USER_CONSTANTS.items() for t in flatten(v) for w in OLD_NAMES if w in t]
    assert found == []


def test_no_leftover_jargon_in_user_visible_texts():
    found = [(n, w, t[:40]) for n, v in USER_CONSTANTS.items() for t in flatten(v) for w in JARGON if w in t]
    found += [(n, "포트", t[:40]) for n, v in USER_CONSTANTS.items() for t in flatten(v) if PORT.search(t)]
    assert found == []


def test_ethernet_word_only_inside_windows_menu_names():
    bad = []
    for n, v in USER_CONSTANTS.items():
        for t in flatten(v):
            rest = ETHERNET_OK.sub("", t)
            if "이더넷" in rest:
                bad.append((n, t[:50]))
    assert bad == []


def test_winget_appears_only_in_commands_or_file_names():
    """winget 은 사용자가 직접 입력하는 명령(winget import …, pcmigrate winget-extract …)과 파일 이름(winget.json) 안에서만 보인다.
    설명 글에는 '설치 도구'라고 쓴다."""
    bad = []
    for n, v in USER_CONSTANTS.items():
        for t in values_of(v):
            for line in t.splitlines():
                line = line.replace("{winget}", "")  # 서식 자리 이름은 화면에 보이지 않는다
                if "winget" in line and not (line.lstrip().startswith(("winget ", "pcmigrate winget")) or "winget.json" in line):
                    bad.append((n, line[:60]))
    assert bad == []


def test_reference_vocabulary_is_present():
    assert T.OVERALL_FLOWS["files"] == "② 내 파일 옮기기" and "내 파일 옮기기" in T.START_CHOICES[1][0]
    assert [o[1] for o in T.MV_OPTIONS] == ["두 PC를 바로 연결 (추천)", "외장 드라이브", "공유 폴더 (고급)"]
    assert [m[1] for m in T.TR_MODES][:2] == ["외장 드라이브로 옮기기", "공유 폴더로 옮기기"]
    assert T.TR_FLOW_TITLES[(T.MODE_NETWORK, 1)] == "공유 폴더 1단계 (구 PC)" and T.TR_FLOW_TITLES[(T.MODE_NETWORK, 2)] == "공유 폴더 2단계 (새 PC)"
    assert "네트워크 케이블" in T.HELP_NETWORK and "공유 폴더가 알맞은 경우" in T.HELP_COMPARE
    assert "내 파일 복사는" in T.AUTO_CANCELLED_HOW
    assert T.RX_ADDR_KINDS["linklocal"] == "네트워크 케이블 직접 연결"
    assert "UNC" not in " ".join(flatten(T.TR_MODES))  # UNC 는 화면에 직접 나오지 않는다(기존 규칙 유지)
    assert "6자리 코드" in T.MV_OPTIONS[0][2]  # '6자리 코드'는 유지


def test_unit_words_used_for_ports_and_formats():
    assert "단자" in T.TR_PRECHECK_LINES[2] and "Windows 형식(NTFS)" in T.TR_PRECHECK_LINES[2]
    assert "오래된 USB 형식(FAT32)" in T.TR_FAT32_WARN and "오래된 USB 형식(FAT32)" in T.WHY_FAT32


# ------------------------------------------------------------------ 시작 화면 1단계(문구·라벨 위치만)
def test_start_texts_tell_that_both_pcs_run_the_tool():
    assert "각각 실행" in T.START_INTRO and "쓰던 PC(구 PC)" in T.START_INTRO and len(T.START_INTRO.splitlines()) <= 2
    assert T.START_ORDER_NOTE == "처음이시면 ① 구 PC → ② 내 파일 옮기기(두 PC) → ③ 새 PC 순서로 진행해 주세요."
    assert T.START_WHERE == ("지금 구 PC 앞이라면", "두 PC에서 각각 실행합니다: 구 PC는 보내는 쪽, 새 PC는 받는 쪽", "지금 새 PC 앞이라면")
    assert len(T.START_CHOICES) == 3 and [c[3] for c in T.START_CHOICES] == [S.OLD_SCAN, S.FIND, S.NEW_PACKAGE]  # 진입 화면은 그대로
    assert "양쪽 PC에서 이 프로그램을 실행" in T.START_CHOICES[1][1]


def test_start_view_source_places_where_label_before_card_title():
    """tk 없이 배치 순서를 확인한다: 카드 안에서 where 라벨이 제목(head)보다 먼저 pack 되고 아래쪽(bottom) 배치가 없다."""
    src = (Path(__file__).resolve().parents[1] / "src" / "pcmigrate" / "gui" / "views" / "start.py").read_text(encoding="utf-8")
    assert src.index("where = tk.Label") < src.index("head = tk.Label")
    assert 'where.pack(side="bottom"' not in src
