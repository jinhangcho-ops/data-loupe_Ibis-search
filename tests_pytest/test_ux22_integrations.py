"""연동 화면 재구성: 첫 화면은 시작 카드 2장, [설정하기]는 하위 페이지(#send, #llm)로, GA4는 수집 화면으로."""
import re

from test_fix_audit_js import between, read

INTEG = "integrations.html"
COLLECT = "collect.html"
GA4_IDS = ("ga4-box", "ga4-key", "ga4-prop", "ga4-start", "ga4-end", "ga4-dims", "ga4-mets", "ga4-out", "ga4-fetch", "ga4-open", "ga4-save", "ga4-check", "ga4-msg")
PAGES = {"": ("want",), "send": ("svc-send", "send-state", "send-kinds", "send-list", "send-manage"),
         "llm": ("svc-llm", "llm-state", "hub-llm-env", "hub-llm-pick", "hub-llm-read", "llm-msg", "svc-bot", "bot-hub-state", "bot-manage")}


def page(name):
    """하위 페이지 한 구역(다음 구역이나 화면 끝 직전까지)."""
    html = read(INTEG)
    start = html.index(f'data-page="{name}"')
    end = re.search(r'data-page="|<!--include core/connect\.html-->', html[start + 1:])
    return html[start:start + 1 + end.start()]


def test_first_screen_has_two_start_cards():
    home = page("")
    cards = re.findall(r'<li class="card" data-go="(\w+)">(.*?)</li>', home, re.S)
    assert [go for go, _ in cards] == ["send", "llm"] and 'class="card svc"' not in home  # 서비스 카드는 첫 화면에 없다
    for (_, card), title in zip(cards, ("결과 보내기 (Slack, Notion 등)", "질문으로 SQL 만들기")):
        assert title in card
        for part in ("<h3>", '<div class="hint">', '<button type="button" class="btn">설정하기</button>', '<span class="state"></span>'):
            assert card.count(part) == 1, part
    assert "어떤 걸 하실 건가요?" in home and "GA4" not in read(INTEG) and "업로드 기록" not in read(INTEG)


def test_sub_pages_hold_the_cards_and_a_way_back():
    html = read(INTEG)
    assert re.findall(r'data-page="(\w*)"', html) == list(PAGES)
    for name, ids in PAGES.items():
        part = page(name)
        for i in ids:
            assert part.count(f'id="{i}"') == 1 and html.count(f'id="{i}"') == 1, i
        if name:
            assert re.search(r'<div class="hidden" data-page="\w+">\s*<a class="btn back" href="#">← 연동</a>', html[html.index(f'data-page="{name}"') - 20:])
    for fake in ("Mattermost", "Jira", "준비 중"):
        assert fake not in html, fake


def test_hash_picks_the_page():
    html = read(INTEG)
    show = between(INTEG, "function showPage()", "\n}")
    assert "location.hash.slice(1)" in show and 'classList.toggle("hidden"' in show
    assert 'addEventListener("hashchange", showPage);' in html and "\nshowPage();" in html  # 뒤로 가기와 해시가 붙은 주소로 바로 열기
    assert "location.hash = b.dataset.go" in html and "scrollIntoView({ behavior" not in html
    hub = between(INTEG, "async function loadHub()", "\n}")
    assert 'showWant("send", senders.some(s => s.ready))' in hub and 'showWant("llm", by.llm.ready)' in hub and "ga4" not in hub
    assert 'senders.map(s => el("li"' in hub  # 보낼 곳 목록은 서버가 주는 종류를 그대로 그린다


def test_ga4_moved_to_collect():
    integ, collect = read(INTEG), read(COLLECT)
    card = between(COLLECT, 'id="ga4-box"', "</details>")
    for i in GA4_IDS:
        assert f'id="{i}"' not in integ and collect.count(f'id="{i}"') == 1, i
        assert i == "ga4-box" or f'id="{i}"' in card, i
    assert "/api/ga4" not in integ
    for call in ('api("/api/ga4").then(showGa4)', 'api("/api/ga4", ga4Body())', 'api("/api/ga4/check", {})', 'api("/api/ga4/fetch", {})', 'api("/api/collect/open", { path: ga4Output })'):
        assert call in collect, call
    assert collect.index('id="ga4-box"') < collect.index('id="up-card"')  # 업로드 기록 바로 위
    assert "loadUploads();" in between(COLLECT, '$("ga4-fetch").onclick', '$("ga4-open").onclick')  # 받은 보고서가 업로드 기록에 바로 보인다
