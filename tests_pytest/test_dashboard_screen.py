"""대시보드 화면(/dashboard, Next): 경로, 왼쪽 메뉴의 대시보드 단추, 첫 화면의 바로가기(대시보드 구역은 없음), 쿼리 화면의 탭 이동과 예전 주소(#dashboard) 보내기,
[대시보드에 추가]의 고르는 창·"대시보드에서 보기" 링크, 최신화 팝업 조각 포함. 화면 파일의 글을 확인한다(브라우저 없이)."""
import re
from pathlib import Path

import pytest

from core import server
from dashboard import dash_app

STATIC = Path(__file__).resolve().parent.parent / "static"
WEB = STATIC.parent / "web"
PAGES = ["/", "/search", "/prep", "/collect", "/integrations"]  # Flask가 그리는 화면(대시보드는 Next가 위쪽 줄을 그린다)


def page(client, path):
    got = client.get(path)
    assert got.status_code == 200, path
    return got.get_data(as_text=True)


def test_dashboard_route_and_parts(client, monkeypatch, tmp_path):
    built = tmp_path / "dashboard.html"
    built.write_text('<html><head><script src="/next/_next/a.js"></script></head><body><script>self.__next_f=[]</script></body></html>', encoding="utf-8")
    monkeypatch.setattr(dash_app, "NEXT_PAGE", built)
    got = client.get("/dashboard")
    nonce = re.search(r"script-src 'nonce-([^']+)'", got.headers["Content-Security-Policy"]).group(1)
    assert got.status_code == 200 and got.get_data(as_text=True).count(f'<script nonce="{nonce}"') == 2  # Next 페이지의 스크립트마다 이번 응답의 nonce
    assert "/dashboard" in server.FREE  # 연결 없이도(조회가 도는 중에도) 열린다
    assert client.get("/dashboard?open=abc").status_code == 200  # ?open=id로 바로 열기(쿼리·분석 화면의 "대시보드에서 보기")
    assert not (STATIC / "dashboard").exists()  # 옛 화면은 없다


def test_nav_has_dashboard_on_every_screen(client):
    for path in PAGES:
        html = page(client, path)
        assert '<a href="/dashboard" data-feature="dashboard"' in html and "<span>대시보드</span></a>" in html, path
    assert re.findall(r'<nav class="rail"[^>]*>(.*?)</nav>', page(client, "/"), re.S)[0].count("<a ") == 6  # 홈·쿼리·분석·대시보드·수집·연동


def test_home_shortcuts_and_no_dashboard_section(client):
    html = page(client, "/")
    go = re.search(r'<nav class="home-go".*?</nav>', html, re.S).group(0)
    assert go.count('<a class="btn"') == 5 and 'href="/dashboard" data-feature="dashboard"' in go
    for gone in ("h-dash", "h-grid", "h-ctl", "쿼리에서 편집", "dash_menu.js", "dash_cards.js", "chart.umd"):
        assert gone not in html, gone
    assert 'id="refresh-pop"' in html and "/static/core/dash_refresh.js" in html  # 연결하면 최신화 팝업은 첫 화면에서도


def test_query_screen_dashboard_tab_moved(client):
    html = page(client, "/search")
    assert 'data-view="dash"' not in html and 'id="dash"' not in html and "dash-grid" not in html and "dash_menu.js" not in html
    assert 'location.hash === "#dashboard") location.replace("/dashboard")' in html  # 예전 주소 /search#dashboard는 대시보드 화면으로
    assert "/static/query/dash_add.js" in html and 'id="dash-add"' in html and 'id="met-dash"' in html and 'showAdded("err-chart"' in html and 'showAdded("err-met"' in html
    for view in re.findall(r'data-view="(\w+)"', html):
        assert view in ("explore", "sql", "metric")


def test_add_to_dashboard_chooser_and_link():
    text = (STATIC / "query" / "dash_add.js").read_text(encoding="utf-8")
    assert "어느 대시보드에 넣을까요?" in text and "새 대시보드" in text and "/api/dash/card/add" in text
    assert "showAdded" not in text  # 알림 줄 쓰기는 core/common.js 한 곳(쿼리·전처리 화면이 함께 씀, R4-D2b에서 dash.js를 지우며 옮김)
    shown = (STATIC / "core" / "common.js").read_text(encoding="utf-8")
    assert '"/dashboard?open=" + encodeURIComponent(added.id)' in shown and "대시보드에서 보기" in shown
    prep = (STATIC / "prep" / "prep.html").read_text(encoding="utf-8")
    assert 'showAdded("err-ana", added.dashboard)' in prep and "[대시보드] 탭" not in prep  # 분석 화면도 같은 링크


def test_old_dashboard_tab_leftovers_are_gone():
    for name in ("index.html", "home.html"):
        text = next(STATIC.rglob(name)).read_text(encoding="utf-8")
        for gone in ("ibis_dashboard", "DASH_KEY", "dash-newcard", "dash-palette", "showDashboards", "[쿼리에서 편집]"):
            assert gone not in text, (name, gone)
    assert not (STATIC / "core" / "dash.js").exists()  # 대시보드 화면 전용은 Next(web/)로, 남은 공용 함수는 common.js로 옮겼다(R4-D2b)
    assert "class Board" not in (STATIC / "core" / "board.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("doc", ["README.md", *sorted(p.relative_to(STATIC.parent).as_posix() for p in (STATIC.parent / "docs").rglob("*.md"))])
def test_docs_do_not_point_to_old_tab(doc):
    text = (STATIC.parent / doc).read_text(encoding="utf-8")
    assert "[대시보드] 탭" not in text and "대시보드 탭" not in text, doc


def test_empty_period_ui_and_own_range():
    bar = (WEB / "components" / "shell" / "PeriodBar.tsx").read_text(encoding="utf-8")
    assert "비우면 카드의 원래 기간" in bar and 'period: "최근 30일"' not in bar
    assert "export function ownRange(card" in (WEB / "lib" / "calc.ts").read_text(encoding="utf-8")
