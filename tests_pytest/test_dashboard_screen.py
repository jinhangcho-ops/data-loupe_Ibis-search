"""대시보드 화면(/dashboard): 경로, 위쪽 줄의 대시보드 전환 단추, 첫 화면의 다섯째 카드(대시보드 구역은 없음), 쿼리 화면의 탭 이동과 예전 주소(#dashboard) 보내기,
[대시보드에 추가]의 고르는 창·"대시보드에서 보기" 링크, 최신화 팝업 조각 포함. 화면 파일의 글을 확인한다(브라우저 없이)."""
import re
from pathlib import Path

import pytest

import server

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGES = ["/", "/search", "/prep", "/dashboard", "/collect", "/integrations"]


def page(client, path):
    got = client.get(path)
    assert got.status_code == 200, path
    return got.get_data(as_text=True)


def test_dashboard_route_and_parts(client):
    html = page(client, "/dashboard")
    for part in ('id="d-pick"', 'id="d-settings"', 'id="d-add-pop"', 'href="/search"', "쿼리에서 카드 만들기", 'id="d-modal"', 'id="d-ctl"', 'id="d-palette"', 'id="d-targets"', 'id="refresh-pop"', 'id="rf-pill"'):
        assert part in html, part
    for script in ("dash.js", "dash_cards.js", "dash_rules.js", "dash_ctl.js", "dash_menu.js", "dash_refresh.js", "board.js", "chart.umd.min.js"):
        assert f"/static/{script}" in html and (STATIC / script).exists()
    assert "/dashboard" in server.FREE  # 연결 없이도(조회가 도는 중에도) 열린다
    assert client.get("/dashboard?open=abc").status_code == 200  # ?open=id로 바로 열기


def test_nav_has_dashboard_on_every_screen(client):
    for path in PAGES:
        html = page(client, path)
        assert '<a href="/dashboard"' in html and ">대시보드</a>" in html, path
    assert re.findall(r'<nav class="pages">(.*?)</nav>', page(client, "/"))[0].count("<a ") == 5  # 쿼리·분석·대시보드·수집·연동


def test_home_has_fifth_card_and_no_dashboard_section(client):
    html = page(client, "/")
    assert html.count('class="choice"') == 5 and 'data-go="/dashboard"' in html
    assert re.search(r'<b>대시보드</b><span>[^<]+</span><em>연결 없이 시작</em>', html)  # 한 줄 설명, 연결 없이 시작
    for gone in ("h-dash", "h-grid", "h-ctl", "쿼리에서 편집", "dash_menu.js", "dash_cards.js", "chart.umd"):
        assert gone not in html, gone
    assert '["/collect", "/integrations", "/dashboard"].includes(target)' in html  # 연결 없이 들어간다
    assert 'id="refresh-pop"' in html and "/static/dash_refresh.js" in html  # 연결하면 최신화 팝업은 첫 화면에서도


def test_query_screen_dashboard_tab_moved(client):
    html = page(client, "/search")
    assert 'data-view="dash"' not in html and 'id="dash"' not in html and "dash-grid" not in html and "dash_menu.js" not in html
    assert 'location.hash === "#dashboard") location.replace("/dashboard")' in html  # 예전 주소 /search#dashboard는 대시보드 화면으로
    assert "/static/dash_add.js" in html and 'id="dash-add"' in html and 'id="met-dash"' in html and 'showAdded("err-chart"' in html and 'showAdded("err-met"' in html
    for view in re.findall(r'data-view="(\w+)"', html):
        assert view in ("explore", "sql", "metric")


def test_add_to_dashboard_chooser_and_link():
    text = (STATIC / "dash_add.js").read_text(encoding="utf-8")
    assert "어느 대시보드에 넣을까요?" in text and "새 대시보드" in text and "/api/dash/card/add" in text
    assert '"/dashboard?open=" + encodeURIComponent(added.id)' in text and "대시보드에서 보기" in text
    prep = (STATIC / "prep.html").read_text(encoding="utf-8")
    assert "/dashboard?open=" in prep and "대시보드에서 보기" in prep and "[대시보드] 탭" not in prep  # 분석 화면도 같은 링크


def test_old_dashboard_tab_leftovers_are_gone():
    for name in ("index.html", "home.html"):
        text = (STATIC / name).read_text(encoding="utf-8")
        for gone in ("ibis_dashboard", "DASH_KEY", "dash-newcard", "dash-palette", "showDashboards", "[쿼리에서 편집]"):
            assert gone not in text, (name, gone)
    menu = (STATIC / "dash_menu.js").read_text(encoding="utf-8")
    assert "S.cols" not in menu and "state()" not in menu and "addCard(" not in menu  # 탐색의 검색에 기대지 않고 메뉴에서 테이블을 고른다
    assert "/api/columns" in menu and "/api/tables" in menu


@pytest.mark.parametrize("doc", ["README.md", "docs/query.md", "docs/dashboard.md", "docs/dashboard_cards.md"])
def test_docs_do_not_point_to_old_tab(doc):
    text = (STATIC.parent / doc).read_text(encoding="utf-8")
    assert "[대시보드] 탭" not in text and "대시보드 탭" not in text, doc


def test_empty_period_ui_and_own_range():
    ctl = (STATIC / "dash_ctl.js").read_text(encoding="utf-8")
    assert "비우면 카드의 원래 기간" in ctl and 'period: "최근 30일"' not in ctl and 'd.period || "최근 30일"' not in ctl
    assert "blank = !this.per.value.trim()" in ctl
    assert "function ownRange(card)" in (STATIC / "dash.js").read_text(encoding="utf-8")
