"""Phase 10 결정 10(사용자 확정 2): 조인 겹친 이름의 예전 꼴(x_right·x_테이블)을 저장한 검색·대시보드 카드를 불러올 때 새 꼴(대상_컬럼)로 옮긴다.
명세: plans/phase-10.md의 old_rname·renames·upgrade·search_renames·upgrade_search·saved.onchange·upgraded·upgrade_dashboard·dash_open·refresh_start·getDashboard 절."""

import pytest

from conftest import ROOT
from core import join_names

COLS = {"customers": ["id", "이름"], "items": ["id", "이름", "가격"], "logs": ["id", "고객"]}


def test_old_rname_matches_previous_rule():
    assert join_names.old_rname(["id", "고객"], ["id", "이름"], "customers") == "{name}_right"
    assert join_names.old_rname(["id", "id_right"], ["id"], "items") == "{name}_items"  # 첫 겹침 이름이 이미 있으면 테이블 이름
    assert join_names.old_rname(["id", "id_right", "id_c"], ["id"], "c") == "{name}_c_"


def test_renames_replays_old_and_new_rules():
    joins = ["customers left 고객=id", "items left id_right=id", "logs semi id=고객"]  # 둘째 줄의 키는 예전 이름
    got = join_names.renames(joins, ["id", "고객", "금액"], list(COLS), COLS.__getitem__)
    assert got == {"id_right": "customers_id", "id_items": "items_id", "이름_items": "items_이름"}  # 겹치지 않은 이름(첫 조인의 이름, 가격)은 그대로
    assert join_names.renames([], ["id"], list(COLS), COLS.__getitem__) == {}


def test_upgrade_renames_references_only():
    body = {"schema": "s", "base": "id_right", "joins": ["customers left 고객=id", "items left id_right=id"], "columns": ["id_right", "금액"],
            "conditions": ["id_right = 'id_right_x'", {"line": "이름_right is null", "quotes": False}], "sorts": ["id_right desc"],
            "calcs": ["순위 기준 id_right 이름 r"], "x": "id_right", "series": [{"y": "이름_right", "agg": "count"}]}
    before = repr(body)
    out = join_names.upgrade(body, {"id_right": "customers_id", "이름_right": "customers_이름", "customers_id": "x"})
    assert repr(body) == before  # 입력은 그대로
    assert out == {"schema": "s", "base": "id_right", "joins": ["customers left 고객=id", "items left customers_id=id"], "columns": ["customers_id", "금액"],
                   "conditions": ["customers_id = 'id_right_x'", {"line": "customers_이름 is null", "quotes": False}], "sorts": ["customers_id desc"],
                   "calcs": ["순위 기준 customers_id 이름 r"], "x": "customers_id", "series": [{"y": "customers_이름", "agg": "count"}]}  # 한 번에 바꾼다(customers_id가 x로 또 바뀌지 않는다)


def test_dashboard_card_upgraded(monkeypatch):
    from dashboard import dash_calc
    monkeypatch.setattr(join_names, "search_renames", lambda body: {"id_right": "customers_id"})
    card = {"id": "a", "kind": "chart", "source": {"body": {"base": "o", "joins": ["customers left 고객=id"], "x": "id_right"}, "date": "id_right"},
            "view": {"group": "id_right", "group_cols": ["id_right", "금액"], "groups": {"keep": ["id_right"]}}, "data": {}}
    out = dash_calc.upgraded(card)
    assert out["source"] == {"body": {"base": "o", "joins": ["customers left 고객=id"], "x": "customers_id"}, "date": "customers_id"}
    assert out["view"] == {"group": "customers_id", "group_cols": ["customers_id", "금액"], "groups": {"keep": ["id_right"]}}  # 값 칸(groups)은 그대로
    monkeypatch.setattr(join_names, "search_renames", lambda body: {})
    assert dash_calc.upgraded(card) == card


@pytest.fixture
def shop(folder, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from core import server
    from query.search_app import app
    path = tmp_path / "shop.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table orders as select i::int as id, 'c' || (i % 5) as 고객, i * 10.0 as 금액 from range(10) t(i)")
    raw.execute("create table customers as select 'c' || i as id, '이름' || i as 이름 from range(5) t(i)")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def test_upgrade_search_route_saves_new_names(shop):
    from core import settings
    old = {"base": "orders", "joins": ["customers left 고객=id"], "columns": ["id_right", "금액"], "sorts": ["id_right desc"], "limit": 10}
    settings.save_search("옛 검색", old)
    got = shop.post("/api/searches/upgrade", json={"name": "옛 검색"}).get_json()
    assert got["renamed"] == {"id_right": "customers_id"} and got["search"]["columns"] == ["customers_id", "금액"]
    assert settings.searches()["옛 검색"]["sorts"] == ["customers_id desc"] and got["searches"]["옛 검색"] == got["search"]
    assert shop.post("/api/query", json={**got["search"], "offset": 0, "count": False}).status_code == 200  # 새 이름으로 검색된다
    plain = {"base": "orders", "columns": ["금액"], "limit": 10}
    settings.save_search("새 검색", plain)
    assert shop.post("/api/searches/upgrade", json={"name": "새 검색"}).get_json()["renamed"] == {}
    assert shop.post("/api/searches/upgrade", json={"name": "없음"}).status_code != 200


def test_saved_search_loader_calls_upgrade():
    html = (ROOT / "static" / "query" / "index.html").read_text(encoding="utf-8")
    a = html.index('$("saved").onchange')
    body = html[a:html.index("\n};\n", a)]
    assert body.index('api("/api/searches/upgrade"') < body.index("S.base = ")


def test_upgrade_dashboard_saves_once(monkeypatch):
    from core import dash_store
    from dashboard import dash_calc
    saved = []
    monkeypatch.setattr(dash_store, "current_source", lambda: {"key": "duckdb:a", "label": "a"})
    monkeypatch.setattr(dash_store, "save_dashboard", lambda d: saved.append(d) or d)
    monkeypatch.setattr(dash_store, "get_dashboard", lambda i: {"id": i, "stored": True})
    monkeypatch.setattr(join_names, "search_renames", lambda body: {"id_right": "customers_id"} if "id_right" in str(body) else {})
    mine = {"id": "a", "kind": "chart", "source": {"body": {"base": "o", "joins": ["customers left 고객=id"], "x": "id_right"}}, "view": {}, "data": {"x": "1"}}
    other = {**mine, "id": "b", "source": {**mine["source"], "key": "duckdb:zzz"}}  # 다른 연결의 카드는 그대로
    d = {"id": "D", "name": "판", "cards": [mine, other]}
    assert dash_calc.upgrade_dashboard(d) == {"id": "D", "stored": True}
    assert [c["source"]["body"]["x"] for c in saved[0]["cards"]] == ["customers_id", "id_right"] and saved[0]["cards"][0]["data"] == {"x": "1"}
    fresh = {"id": "E", "name": "새", "cards": [{**mine, "source": {"body": {"base": "o", "x": "금액"}}}]}
    assert dash_calc.upgrade_dashboard(fresh) is fresh and len(saved) == 1  # 바꿀 것이 없으면 저장하지 않는다
    monkeypatch.setattr(dash_store, "current_source", lambda: None)
    assert dash_calc.upgrade_dashboard(d) is d and len(saved) == 1  # 연결이 없으면 다음에 열 때


def old_dashboard():
    from core import dash_store
    card = {"kind": "chart", "title": "고객별", "source": {"body": {"base": "orders", "joins": ["customers left 고객=id"], "x": "id_right", "series": [{"y": "금액", "agg": "sum"}]}}}
    return dash_store.save_dashboard({"name": "옛 판", "cards": [card]})["id"]


def test_dash_open_route_renames_and_saves(shop):
    from core import dash_store
    dash_id = old_dashboard()
    got = shop.post("/api/dash/open", json={"id": dash_id}).get_json()["dashboard"]
    assert got["cards"][0]["source"]["body"]["x"] == "customers_id"
    assert dash_store.get_dashboard(dash_id)["cards"][0]["source"]["body"]["x"] == "customers_id"  # 한 번 바꿔 저장(카드 편집 칸에도 새 이름)


def test_refresh_start_upgrades_stored_dashboards(shop):
    from core import dash_store, server
    dash_id = old_dashboard()
    assert "/api/dash/refresh/start" not in server.FREE  # 연결을 쓰므로 차례를 기다린다
    assert shop.post("/api/dash/refresh/start", json={}).status_code == 200
    assert dash_store.get_dashboard(dash_id)["cards"][0]["source"]["body"]["x"] == "customers_id"  # 열지 않은 대시보드도 최신화 전에


def test_web_opens_dashboard_through_upgrade():
    api = (ROOT / "web" / "lib" / "api.ts").read_text(encoding="utf-8")
    line = next(s for s in api.splitlines() if s.startswith("export const getDashboard"))
    assert '"/api/dash/open", { id }' in line and "/api/dash/get" not in line
