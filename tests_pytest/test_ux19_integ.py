"""Phase 19 묶음 I: 연동·수집 화면을 처음 쓰는 사람 말로. 기능·id는 그대로 두고 글자·배치·접기만 바꿨는지 본다."""
import re

from test_fix_audit_js import between, read

INTEG = "integrations.html"
COLLECT = "collect.html"
INTEG_IDS = ("send-state", "send-kinds", "send-list", "send-manage", "bot-hub-state", "bot-manage", "ga4-state", "ga4-key", "ga4-prop",
             "ga4-start", "ga4-end", "ga4-dims", "ga4-mets", "ga4-out", "ga4-fetch", "ga4-open", "ga4-save", "ga4-check", "ga4-msg",
             "llm-state", "hub-llm-env", "hub-llm-pick", "hub-llm-read", "llm-msg", "err-hub")
GA4_INPUTS = ("ga4-key", "ga4-prop", "ga4-start", "ga4-end", "ga4-dims", "ga4-mets", "ga4-out", "ga4-fetch", "ga4-save", "ga4-check")


def test_integ_ids_kept():
    html = read(INTEG)
    for i in INTEG_IDS:
        assert html.count(f'id="{i}"') == 1, i


def test_integ_cards_fold_inputs_behind_setup():
    """19G1: GA4·LLM 카드의 입력 칸은 [설정하기]를 눌러야 보이고, 저장된 값이 있으면 펼쳐 둔다."""
    ga4 = between(INTEG, 'id="ga4-box"', "</details>")
    assert "<summary" in ga4 and "설정하기" in ga4
    for i in GA4_INPUTS:
        assert f'id="{i}"' in ga4, i
    llm = between(INTEG, 'id="hub-llm-box"', "</details>")
    assert "<summary" in llm and "설정하기" in llm
    for i in ("hub-llm-env", "hub-llm-pick", "hub-llm-read"):
        assert f'id="{i}"' in llm, i
    html = read(INTEG)
    assert '$("ga4-box").open = true' in html and '$("hub-llm-box").open = true' in html


def test_integ_want_list_jumps_to_cards():
    """19G2: 맨 위 "무엇을 하고 싶어요?" 목록이 카드로 데려간다(스크롤·강조)."""
    html = read(INTEG)
    want = between(INTEG, 'id="want"', "</ul>")
    assert html.index('id="want"') < html.index('class="grid"')
    assert "무엇을 하고 싶어요?" in html
    assert re.findall(r'data-go="([\w-]+)"', want) == ["svc-send", "svc-bot", "svc-ga4", "svc-llm"]
    for word in ("Slack·Notion으로 보내기", "Slack에서 물어보기", "웹사이트 방문 데이터 받기", "질문으로 SQL 만들기"):
        assert word in want, word
    assert "scrollIntoView" in between(INTEG, "[data-go]", "}));") and "flash" in html
    assert "onclick=\"" not in html


def test_collect_steps_easy_names_and_folded():
    """19H1: 오른쪽 단계 이름은 쉬운 말, 받는 속도·사용 한도는 [더 많은 설정] 뒤에."""
    html = read(COLLECT)
    flow = between(COLLECT, 'class="flow"', "</aside>")
    assert '<span class="t">비밀 키 파일(env)</span>' in flow and '<span class="t">여러 쪽 받기</span>' in flow
    assert '<span class="t">env 파일</span>' not in flow and '<span class="t">쪽 넘기기</span>' not in flow
    assert re.search(r'<div class="flow"[^>]*data-adv', html) and "data-adv-slot" in flow
    for step in ("speed", "limits"):
        assert re.search(rf'<div class="cstep adv"><div class="cn"></div>\s*<div class="step" data-step="{step}"', flow), step
    assert len(re.findall(r'<div class="sp" title="[^"]+">', flow)) == 7  # 단계마다 쉬운 설명
    restore = between(COLLECT, "function restore(c)", "\nfunction showCollections")
    assert "showAdv(" in restore and "DEFAULT.balance" in restore  # 저장한 구성이 기본과 다르면 펼친다
    assert 'getComputedStyle(c).display !== "none"' in html  # 접힌 단계는 번호를 건너뛴다


def test_collect_request_and_response_words():
    html = read(COLLECT)
    assert "주소 뒤에 붙는 ?이름=값" in html and "API가 요구하는 열쇠" in html
    assert "행이 될 배열" not in html and "줄이 될 목록 위치" in html
    for i in ("query", "headers", "f-headers", "body-kind", "body", "records", "p-kind", "b-conc", "lgroup", "env-path", "first-guide"):
        assert html.count(f'id="{i}"') == 1, i
    guide = between(COLLECT, 'id="first-guide"', "</section>")
    assert "curl" not in guide.replace("(curl)", "")  # curl은 풀이와 함께만
