"""Next 대시보드 뼈대(web/): API 클라이언트(web/lib/api.ts)가 부르는 경로가 Flask에 모두 있는지, 빌드한 실제 페이지의 스크립트에 nonce가 다 붙는지."""
import re
from pathlib import Path

import pytest

from core import server
from dashboard import dash_app
from query import search_app  # noqa: F401  /api/columns·query·images 라우트

WEB = Path(__file__).resolve().parent.parent / "web"
NEEDED = {"/api/dash", "/api/dash/get", "/api/dash/delete", "/api/dash/card/add", "/api/dash/card/compute", "/api/dash/cards/compute", "/api/dash/settings",
          "/api/dash/last", "/api/dash/live_min", "/api/dash/refresh/start", "/api/dash/refresh/status", "/api/dash/refresh/stop", "/api/dash/image",
          "/api/dash/templates", "/api/dash/templates/delete", "/api/dash/pdf", "/api/chart/points", "/api/period/parse", "/api/period/day", "/api/period/every",
          "/api/compare", "/api/targets", "/api/tables", "/api/columns", "/api/query", "/api/my_metrics", "/api/images", "/api/dash/constants"}  # 대시보드 화면이 쓰는 API(NEXT_PLAN 계약)


def api_paths(text):
    """TS 글에서 '/api/...' 경로(따옴표·백틱으로 시작하는 것, ?쿼리 앞까지)."""
    return set(re.findall(r"[\"'`](/api/[A-Za-z0-9_/]+)", text))


def test_api_paths_helper():
    assert api_paths('a("/api/dash/get?id=" + q(id)); b(`/api/targets?offset=${o}`); c("/api/x")') == {"/api/dash/get", "/api/targets", "/api/x"}


def test_api_ts_paths_are_flask_routes():
    """오타 방지: api.ts의 경로가 모두 Flask 라우트(app.url_map)에 있다. 없는 경로를 넣으면 이 시험이 그 이름을 보인다."""
    used = api_paths((WEB / "lib" / "api.ts").read_text(encoding="utf-8"))
    routes = {r.rule for r in server.app.url_map.iter_rules()}
    assert sorted(NEEDED - used) == []  # 대시보드 API 전부(빠뜨려도 알게)
    assert sorted(used - routes) == []


def test_api_paths_check_catches_typo():
    routes = {r.rule for r in server.app.url_map.iter_rules()}
    assert api_paths('api("/api/dash/card/compte", {})') - routes == {"/api/dash/card/compte"}


@pytest.mark.skipif(not (WEB / "out" / "dashboard.html").is_file(), reason="web/out이 없음(npm run build --prefix web 전)")
def test_built_dashboard_scripts_all_nonced(client, monkeypatch):
    """빌드한 실제 페이지(web/out/dashboard.html): /dashboard가 그것을 내주고, 모든 <script와 스크립트 미리 받기 링크에 이번 응답의 nonce가 붙는다."""
    monkeypatch.setattr(dash_app, "NEXT_PAGE", WEB / "out" / "dashboard.html")
    r = client.get("/dashboard")
    nonce = re.search(r"script-src 'nonce-([^']+)'", r.headers["Content-Security-Policy"]).group(1)
    assert r.status_code == 200 and "/next/_next/" in r.text
    assert r.text.count("<script") > 0 and r.text.count("<script") == r.text.count(f'<script nonce="{nonce}"')
    assert r.text.count('rel="preload" as="script"') == r.text.count(f'<link nonce="{nonce}" rel="preload" as="script"')
    for css in re.findall(r'<link rel="stylesheet" href="(/static/[^"]+)"', r.text):  # 공용 CSS는 Flask static에서
        assert client.get(css).status_code == 200
