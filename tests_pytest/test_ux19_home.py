"""(Phase 19) 묶음 H: 홈의 "무엇을 하고 싶어요?" 카드, 연결 칸의 안내 글, 설정 창 분류마다 한 줄 설명."""
import re

from test_fix_audit_js import between, read

CARDS = [("/search", "query", "쿼리", "데이터를 표로 훑어보고 조건으로 찾아요"),
         ("/prep", "prep", "분석", "데이터를 다듬고 그래프·통계로 살펴봐요"),
         ("/dashboard", "dashboard", "대시보드", "늘 보는 숫자와 차트를 한 화면에 모아요"),
         ("/collect", "collect", "수집", "인터넷(API·웹페이지)에서 데이터를 받아 와요"),
         ("/integrations", "integrations", "연동", "결과를 Slack·Notion으로 보내거나 GA4 데이터를 받아요")]
TABS = ["feat", "update", "calc", "sample", "date", "viz", "dest"]


def test_home_cards_name_and_one_line():
    now = between("home.html", '<div id="home-now"', "</nav>")
    assert "무엇을 하고 싶어요?" in now
    found = re.findall(r'<a class="btn" href="(/\w+)" data-feature="(\w+)"><b>([^<]+)</b><span>([^<]+)</span></a>', now)
    assert found == CARDS  # 메뉴 이름 그대로, 쓸 기능에서 끈 화면은 data-feature로 숨는다(common.js)
    assert '<p class="lead" data-feature="query">처음이면 [쿼리]에서 테이블을 골라 보세요.</p>' in now  # 쿼리를 끄면 이 안내도 숨는다


def test_connect_lead_and_kind_chip_tip():
    html = read("connect.html")
    head = html.index('<h3 id="conn-title">데이터 연결</h3>')
    assert html.index('<p class="lead">내 PC의 파일(Excel·CSV 등)이나 회사 DB를 골라 연결해요. 원본은 바뀌지 않아요.</p>') > head
    assert '찾을 파일 종류 <span class="hint" id="kinds-tip">누르면 빼거나 넣어요</span>' in html
    assert 'id="kinds"' in html and 'aria-describedby="kinds-tip"' in html


def test_every_settings_page_starts_with_lead():
    panel = between("status.html", '<div id="feat-panel"', '<div class="err" id="err-settings">')
    pages = re.findall(r'<section class="set-page[^"]*" data-set="(\w+)" role="tabpanel" aria-label="[^"]+">\s*<p class="lead">([^<]+)</p>', panel)
    assert [p for p, _ in pages] == TABS
    assert all(len(text) > 10 for _, text in pages)


def test_hard_setting_names_have_plain_titles():
    status = read("status.html")
    for plain in ("표본 결과를 얼마나 믿을지예요", "맛보기로 먼저 뽑는 줄 수(파일럿)", "비슷한 것끼리 몇 무리(층)로", "최근 값에 무게를 더 주는 평균(EWMA)", "줄마다 다른 값(ID·해시)"):
        assert plain in status, plain
