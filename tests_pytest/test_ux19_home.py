"""(Phase 19) 묶음 H: 홈의 "어떤 걸 하실 건가요?" 카드, 연결 칸의 안내 글, 설정 창 분류마다 한 줄 설명."""
import re

from test_fix_audit_js import between, read

CARDS = [("/prep", "prep", "분석", "데이터를 다듬고 그래프·통계로 살펴봐요"),
         ("/dashboard", "dashboard", "대시보드", "늘 보는 숫자와 차트를 한 화면에 모아요"),
         ("/collect", "collect", "수집", "인터넷(API·웹페이지)에서 데이터를 받아 와요"),
         ("/integrations", "integrations", "연동", "결과를 Slack·Notion으로 보내거나 GA4 데이터를 받아요")]
TABS = ["feat", "view", "update", "calc", "memory", "sample", "date", "viz", "dest"]


def test_home_cards_name_and_one_line():
    now = between("home.html", '<section class="home-main"', "</nav>")
    assert now.index('<h2 class="home-ask">어떤 걸 하실 건가요?</h2>') < now.index('<div id="home-now"')  # 묻는 말이 맨 위, 그 아래 지금 연결 줄
    found = re.findall(r'<a class="btn" href="(/\w+)" data-feature="(\w+)"><span class="icon-tile"><svg [^>]+>.*?</svg></span><b>([^<]+)</b><span>([^<]+)</span><span class="card-art" data-art="\w+" aria-hidden="true"></span></a>', now)
    assert found == CARDS  # 작은 카드: 그림 타일 + 메뉴 이름 그대로 + 한 줄 + 카드 그림. 쓸 기능에서 끈 화면은 data-feature로 숨는다(common.js)
    # [쿼리]는 큰 시작 카드: 제목 + 한 줄 설명(처음 쓰는 사람 안내 포함) + 강조 단추 모양. 쿼리를 끄면 카드째 숨는다
    start = re.search(r'<a class="btn" href="/search" data-feature="query" id="home-start"><b>([^<]+)</b><span>([^<]+)</span><span class="btn p"><svg [^>]+>.*?</svg>([^<]+)</span><span class="card-art" data-art="table" aria-hidden="true"></span></a>', now)
    assert start.groups() == ("쿼리로 시작하기", "데이터를 표로 훑어보고 조건으로 찾아요. 처음이면 여기에서 테이블을 골라 보세요.", "쿼리 열기")
    assert now.index('id="home-start"') < now.index('data-feature="prep"')  # 시작 카드가 맨 위
    css = read("common.css")
    assert "#home-start { grid-column:1 / -1;" in css and ".icon-tile {" in css
    assert ".home-go { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr));" in read("home.html")  # 작은 카드는 2열


def test_card_art_is_decoration_only():
    """카드 그림(첫 화면 5장, 연동 4장)은 장식이다: 읽어 주지 않고, 글자·숫자가 없고, 색은 CSS 변수만 쓰고, 좁은 창에서는 숨는다."""
    css = between("common.css", ".card-art { display:none; }", "\n}\n")
    assert css.startswith(".card-art { display:none; }\n@media (min-width:900px) {")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|content:\"[^\"]", css)
    arts = re.findall(r'<span class="card-art" data-art="(\w+)" aria-hidden="true"></span>', read("home.html") + read("integrations.html"))
    assert arts == ["table", "bars", "stat", "picks", "msg", "msg", "chat", "stat", "code"]
    assert all(f".card-art[data-art={kind}]::before" in css for kind in arts)
    assert (read("home.html") + read("integrations.html")).count('class="card-art"') == len(arts)  # 그림마다 빈 태그 하나


def test_connect_lead_and_kind_chip_tip():
    html = read("connect.html")
    head = html.index('<h3 id="conn-title">데이터 연결</h3>')
    assert html.index('<p class="lead">내 PC의 파일이나 회사 DB를 열어요. 원본은 바뀌지 않아요.</p>') > head
    assert '<div class="field-label">데이터가 있는 위치</div>' in html
    foot = between("connect.html", '<div class="conn-foot">', "</div>")
    assert foot.index('id="conn-cancel"') < foot.index('id="connect" class="btn p conn-go"')  # 아래 오른쪽: [취소] [연결]
    js = read("common.js")
    assert '[$("conn-close"), $("conn-cancel")].forEach(b => b.classList.toggle("hidden", !connClosable()));' in js  # 닫을 수 없으면 [취소]도 숨는다
    assert '$("conn-close").onclick = $("conn-cancel").onclick = closeConnect;' in js
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
