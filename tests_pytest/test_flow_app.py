"""문장·내 지표·공통 키 라우트(flow_app.py): 연결을 쓰지 않는 라우트는 차례를 기다리지 않고(FREE), 연결에서 읽는 라우트는 원격 DB에서 확인 없이 값을 읽는 조회를 보내지 않는다.
데이터는 테스트 안에서 지어낸다(sales: id 0~11, shop_id = id % 3, amt = (id+1)*10 / shops: 가게 0·1은 KR, 2는 US)."""
import duckdb
import pytest
from conftest import spy_queries

from core import held
from core import server

FREE = ["/api/my_metrics",
        "/api/my_metrics/validate", "/api/my_metrics/update", "/api/my_metrics/delete", "/api/my_metrics/copy", "/api/my_metrics/status", "/api/my_metrics/check_use",
        "/api/my_metrics/used_by", "/api/my_metrics/stale", "/api/my_metrics/bind_legacy", "/api/sentence/check", "/api/sentence/compile"]
READS = ["/api/table_columns", "/api/join_keys"]  # 연결에서 읽는 라우트는 차례를 기다린다


@pytest.fixture
def c(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query import flow_app  # noqa: F401
    from prep import prep_app  # noqa: F401
    from query.search_app import app
    path = tmp_path / "r.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table sales as select i::int as id, (i % 3)::int as shop_id, ((i + 1) * 10)::double as amt from range(12) t(i)")
    raw.execute("create table shops as select i::int as shop_id, case when i < 2 then 'KR' else 'US' end as country from range(3) t(i)")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def post(c, path, **body):
    resp = c.post(path, json=body)
    return resp.status_code, resp.get_json()


def test_free_and_connection_routes_are_registered_correctly(c):
    assert all(path in server.FREE for path in FREE)
    assert not any(path in server.FREE for path in READS)
    rules = {r.rule for r in server.app.url_map.iter_rules()}
    assert all(path in rules for path in FREE + READS)
    assert not any(r.startswith("/api/flow") for r in rules) and "flow" not in server.constants("query")  # 흐름 보기 라우트·상수는 없앴다


def test_free_routes_answer_while_another_job_holds_the_turn(c):
    assert server.turn.acquire(timeout=1)  # 다른 데이터 작업이 연결을 쓰는 중
    try:
        assert post(c, "/api/sentence/check", template="e06", slots={})[0] == 200
        assert c.get("/api/my_metrics").status_code == 200
    finally:
        server.turn.release()


def test_table_columns_route(c):
    code, body = post(c, "/api/table_columns", table="sales")
    assert code == 200 and body["columns"] == {"id": "num", "shop_id": "num", "amt": "num"}
    assert post(c, "/api/table_columns")[1]["error"] == "테이블(table)을 고르세요."


def test_keys_route(c):
    code, body = post(c, "/api/join_keys", a={"table": "sales"}, b={"table": "shops"})
    assert code == 200 and body["decision"] == "auto" and body["candidates"][0]["keys"] == [["shop_id", "shop_id"]] and body["basis"] == "표본 기준" and not body["remembered"]
    assert body["source_key"] and body["fingerprint"]
    assert body["candidates"][0]["score"] == pytest.approx(0.35 * 1 + 0.45 * 1 + 0.10 * 1 + 0.10 * 1)  # 이름 같음·겹침 1·타입·shops.shop_id 고유
    assert post(c, "/api/join_keys", a={"table": "sales"})[1]["error"] == "이을 두 테이블(a, b)을 고르세요."


def test_keys_route_on_remote_sends_no_value_queries(c, monkeypatch):
    monkeypatch.setattr(held, "remote", lambda con: True)
    sent = spy_queries(monkeypatch)
    code, body = post(c, "/api/join_keys", a={"table": "sales"}, b={"table": "shops"})
    assert code == 200 and body["basis"] == "표본 없음" and "원격 DB라 값을 읽는 조회를 새로 보내지 않았어요" in body["note"]
    assert body["candidates"][0]["keys"] == [["shop_id", "shop_id"]]  # 이름·타입만으로도 같은 이름 키가 1등
    assert sent == [], sent
    code, body = post(c, "/api/join_keys", a={"table": "sales"}, b={"table": "shops"}, full=True)
    assert code == 400 and body["error"].startswith("원격 DB에서는 전체 값 확인을 하지 않아요")
    assert sent == []


def test_my_metric_routes(c):
    code, listed = (200, c.get("/api/my_metrics").get_json())
    assert listed["metrics"] == [] and any(t["id"] == "m09" for t in listed["templates"]) and listed["legacy"] == [] and listed["source"]["key"]
    cols = {"amt": "num", "user": "text"}
    made = post(c, "/api/my_metrics", definition={"name": "ARPU", "scope": {"table": "sales"}, "template": "m09", "slots": {"값": "amt", "대상": "user"}, "unit": "원"}, columns=cols)
    assert made[0] == 200
    metric = made[1]["metric"]
    assert metric["scope"]["key"] == listed["source"]["key"] and metric["scope"]["label"] == listed["source"]["label"] and metric["text"] == f"ARPU · sales ({listed['source']['label']})"
    assert post(c, "/api/my_metrics/validate", definition={"name": "나쁨", "scope": {"table": "sales"}, "template": "m09", "slots": {"값": "user", "대상": "user"}}, columns=cols)[1] == {
        "ok": False, "problem": "‘값’ 칸: ‘user’은 글자 컬럼이라 쓸 수 없어요(숫자 컬럼이 필요해요)."}
    assert post(c, "/api/my_metrics/validate", definition={"name": "OK", "scope": {"table": "sales"}, "template": "m02", "slots": {"대상": "user"}}, columns=cols)[1]["ok"] is True
    other = {"table": "orders"}
    code, body = post(c, "/api/my_metrics/check_use", id=metric["id"], scope=other)
    assert body["ok"] is False and "다른 테이블(흐름)의 지표라 그대로 쓸 수 없어요" in body["reason"]
    assert post(c, "/api/my_metrics/check_use", id=metric["id"], scope={"table": "sales"})[1] == {"ok": True}
    copied = post(c, "/api/my_metrics/copy", id=metric["id"], scope=other, mapping={"amt": "price"}, columns={"price": "num", "user": "text"})[1]
    assert copied["metrics"][0]["slots"] == {"값": "price", "대상": "user"} and copied["metrics"][0]["scope"]["table"] == "orders" and metric["id"] in copied["id_map"]
    status = post(c, "/api/my_metrics/status", scope={"table": "sales"}, columns={"user": "text"})[1]["status"]
    assert status == {metric["id"]: {"usable": False, "reason": "‘amt’ 컬럼이 테이블에서 없어졌어요."}}  # 같은 scope의 지표만
    updated = post(c, "/api/my_metrics/update", id=metric["id"], patch={"slots": {"값": "amt", "대상": "amt"}}, columns={"amt": "num", "user": "text"})[1]
    assert updated["changed"] is True and updated["metric"]["slots"]["대상"] == "amt"
    stale = post(c, "/api/my_metrics/stale", id=metric["id"], def_hash=metric["def_hash"], period="2026-01")[1]
    assert stale["stale"] is True and stale["def_hash"] == updated["metric"]["def_hash"] and stale["cache_key"].endswith('"2026-01"]')
    assert post(c, "/api/my_metrics/stale", id=metric["id"], def_hash=stale["def_hash"])[1]["stale"] is False
    users = post(c, "/api/my_metrics/used_by", id=metric["id"])[1]
    assert users["cards"] == [] and users["dependents"] == []
    assert post(c, "/api/my_metrics/delete", id=metric["id"])[1]["removed"] == metric["id"]
    assert post(c, "/api/my_metrics/delete", id=metric["id"])[1]["error"].startswith("그 지표를 찾지 못했어요")
    assert post(c, "/api/my_metrics/status", scope={"table": "sales"})[1]["error"] == "지금 테이블의 컬럼(columns)을 함께 보내세요."


def test_sentence_routes(c):
    code, body = post(c, "/api/sentence/check", template="e09", slots={"무엇": "store", "N": 0}, columns={"store": "text"})
    assert code == 200 and body["ok"] is False and body["issues"] == [{"slot": "N", "level": "bad", "text": "‘N 이상’에는 1 이상의 정수를 넣어 주세요."}]
    assert body["text"] == "store별 행 수가 0 이상인 것만 많은 순으로 보기"
    code, body = post(c, "/api/sentence/compile", template="e09", slots={"무엇": "store", "N": 4}, body={"base": "sales"}, columns={"store": "text"})
    assert code == 200 and body["ok"] and body["runs"][0]["route"] == "/api/pivot"
    assert body["runs"][0]["body"]["pivot"] == {"rows": ["store"], "how": "count", "having": "전체 개수 >= 4", "order": "전체 개수 desc"}
    code, body = post(c, "/api/sentence/compile", template="e05", slots={"컬럼": "store", "글자": {"match": "으로 끝", "text": "x"}}, body={"base": "sales"})
    assert body["ok"] is True and body["runs"][0]["body"]["conditions"] == ["store ends x"]
    assert post(c, "/api/sentence/compile", template="e05", slots={"컬럼": "store", "글자": {"match": "엉뚱", "text": "x"}}, body={"base": "sales"})[1]["ok"] is False
    assert post(c, "/api/sentence/compile", template="zz")[1]["error"] == "'zz' 문장 틀이 없습니다."
