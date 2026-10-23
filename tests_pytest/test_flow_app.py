"""흐름·문장·내 지표 라우트(flow_app.py): 연결을 쓰지 않는 라우트는 차례를 기다리지 않고(FREE), 연결에서 읽는 라우트는 원격 DB에서 확인 없이 값을 읽는 조회를 보내지 않는다.
데이터는 테스트 안에서 지어낸다(sales: id 0~11, shop_id = id % 3, amt = (id+1)*10 / shops: 가게 0·1은 KR, 2는 US)."""
import duckdb
import pytest
from conftest import spy_queries

from query import flow
from core import held
from core import server

FREE = ["/api/flow/check_edge", "/api/flow/check", "/api/flow/to_body", "/api/flow/from_body", "/api/flow/keys/remember", "/api/flow/keys/forget", "/api/my_metrics",
        "/api/my_metrics/validate", "/api/my_metrics/update", "/api/my_metrics/delete", "/api/my_metrics/copy", "/api/my_metrics/status", "/api/my_metrics/check_use",
        "/api/my_metrics/used_by", "/api/my_metrics/stale", "/api/my_metrics/legacy", "/api/my_metrics/bind_legacy", "/api/sentence/check", "/api/sentence/compile"]
READS = ["/api/flow/schemas", "/api/flow/rows", "/api/flow/optimize", "/api/flow/optimize/apply", "/api/flow/keys", "/api/flow/fanout"]  # 연결에서 읽는 라우트는 차례를 기다린다
SCHEMAS = {"sales": {"columns": {"id": "num", "shop_id": "num", "amt": "num"}, "source_key": "k"}, "shops": {"columns": {"shop_id": "num", "country": "text"}, "source_key": "k"}}
BODY = {"base": "sales", "joins": ["shops inner shop_id=shop_id"], "conditions": ["amt > 50"], "aggregate": {"by": ["country"], "series": [{"agg": "sum", "y": "amt", "name": "total"}]}}


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
    assert server.constants("query")["flow"]["ranks"]["join"] == 2 and server.constants("query")["flow"]["order"] == flow.ORDER_TEXT


def test_free_routes_answer_while_another_job_holds_the_turn(c):
    assert server.turn.acquire(timeout=1)  # 다른 데이터 작업이 연결을 쓰는 중
    try:
        graph = flow.from_body(BODY)
        code, body = post(c, "/api/flow/check_edge", graph=graph, a="n1", b="n2")
        assert (code, body["ok"], body["code"]) == (200, False, "shape")  # 이미 이어져 있다
        assert post(c, "/api/sentence/check", template="e06", slots={})[0] == 200
        assert c.get("/api/my_metrics").status_code == 200
    finally:
        server.turn.release()


def test_check_edge_route_uses_the_schemas_sent_by_the_screen(c):
    graph = {"nodes": [{"id": "o", "kind": "table", "params": {"table": "sales"}}, {"id": "s", "kind": "table", "params": {"table": "shops"}},
                       {"id": "j", "kind": "join", "params": {"how": "inner", "keys": [["amt", "country"]]}}], "edges": [["o", "j"]]}
    code, body = post(c, "/api/flow/check_edge", graph=graph, a="s", b="j", schemas=SCHEMAS)
    assert code == 200 and body["ok"] is False and body["code"] == "key_type" and body["suggest"] == {"side": "right", "column": "country", "to": "num"}
    assert body["reason"] == "조인 키 ‘amt’(숫자)과 ‘country’(글자)의 종류가 달라 이을 수 없어요. 오른쪽 ‘country’을(를) 숫자로 바꿔(cast) 이어 보세요."
    code, body = post(c, "/api/flow/check_edge", graph=graph, a="s", b="j")  # 스키마가 없으면 순위·순환·모양만 본다
    assert body["ok"] is True
    code, body = post(c, "/api/flow/check", graph=graph, schemas=SCHEMAS)
    assert body["problems"] == [] and body["body_problem"] == "흐름의 끝이 2곳이에요. 어느 단계까지 볼지 정하세요."  # 테이블 shops가 아직 이어지지 않았다
    assert post(c, "/api/flow/check_edge", graph={"nodes": [{"id": "a", "kind": "zzz"}]}, a="a", b="a")[0] == 400


def test_convert_routes_round_trip(c):
    code, body = post(c, "/api/flow/from_body", body=BODY)
    assert code == 200 and [n["kind"] for n in body["graph"]["nodes"]] == ["table", "join", "table", "condition", "aggregate"]
    code, back = post(c, "/api/flow/to_body", graph=body["graph"])
    assert code == 200 and back["body"] == {"schema": "", "joins": ["shops inner shop_id=shop_id"], "periods": [], "conditions": ["amt > 50"], "calcs": [], "base": "sales",
                                            "aggregate": BODY["aggregate"]}
    code, body = post(c, "/api/flow/to_body", graph={"nodes": [], "edges": []})
    assert code == 400 and body["error"] == "흐름의 끝이 없어요."
    assert post(c, "/api/flow/from_body", body={})[1]["error"] == "기준 테이블이 있는 검색 body가 필요해요."


def test_schemas_and_row_counts_per_step(c):
    graph = flow.from_body(BODY)
    code, body = post(c, "/api/flow/schemas", graph=graph)
    assert code == 200 and body["schemas"]["sales"]["columns"] == {"id": "num", "shop_id": "num", "amt": "num"} and body["schemas"]["shops"]["columns"]["country"] == "text"
    assert body["schemas"]["sales"]["source_key"] == body["source"]["key"] and "password" not in str(body)
    code, body = post(c, "/api/flow/rows", graph=graph)
    assert code == 200 and not body["needs_confirm"]
    assert {s["id"]: s["rows"] for s in body["steps"]} == {"n1": 12, "n3": 3, "n2": 12, "n4": 7}  # sales 12 · shops 3 · inner join 12 · amt > 50 인 7행(id 5~11)


def test_row_counts_ask_before_counting_on_remote(c, monkeypatch):
    monkeypatch.setattr(held, "remote", lambda con: True)
    sent = spy_queries(monkeypatch)
    graph = flow.from_body(BODY)
    code, body = post(c, "/api/flow/rows", graph=graph)
    assert code == 200 and body["needs_confirm"] and "원격 DB" in body["note"] and all(s["rows"] is None for s in body["steps"])
    assert sent == []
    code, body = post(c, "/api/flow/rows", graph=graph, confirmed=True)
    assert [s["rows"] for s in body["steps"]][-1] == 7 and sent


def test_optimize_and_apply_routes(c):
    graph = flow.from_body(BODY)
    code, found = post(c, "/api/flow/optimize", graph=graph)
    assert code == 200 and found["ok"] and found["verified"]
    assert [(r["no"], r["status"]) for r in found["rules"]] == [(1, "kept"), (2, "skipped"), (3, "kept"), (4, "kept")]
    assert found["bytes"]["text"].startswith("읽는 양 약 ")
    assert [n["kind"] for n in graph["nodes"]] == [n["kind"] for n in flow.from_body(BODY)["nodes"]]  # 제안은 흐름을 바꾸지 않는다
    code, done = post(c, "/api/flow/optimize/apply", graph=graph, rules=[1])
    assert code == 200 and done["applied"] == [1] and done["dropped"] == [] and done["body_problem"] == "조인 앞에 기간·조건·계산 단계가 있어 단계 카드(검색 body)로 바꿀 수 없어요. 흐름 보기에서만 쓰는 모양이에요."
    assert post(c, "/api/flow/optimize/apply", graph=graph, rules=[])[1]["error"] == "적용할 규칙을 고르세요."
    plain = flow.from_body({"base": "sales"})
    assert post(c, "/api/flow/optimize", graph=plain)[1]["reason"] == "집계(지표·집계 단계)가 결과인 흐름에서만 최적화를 제안해요."


def test_optimize_on_remote_sends_no_data_scans(c, monkeypatch):
    monkeypatch.setattr(held, "remote", lambda con: True)
    sent = spy_queries(monkeypatch)
    graph = flow.from_body(BODY)
    code, found = post(c, "/api/flow/optimize", graph=graph)
    assert code == 200 and not found["verified"] and [r["status"] for r in found["rules"]] == ["unverified", "skipped", "skipped", "unverified"]
    scans = [q for q in sent if not q.lstrip().upper().startswith(("EXPLAIN", "DESCRIBE")) and "duckdb_tables" not in q and "SELECT CURRENT_" not in q.upper()]
    assert scans == [], scans
    code, found = post(c, "/api/flow/optimize", graph=graph, scan_ok=True)
    assert found["verified"] and found["rules"][2]["status"] == "kept"


def test_keys_route_and_remembering(c):
    code, body = post(c, "/api/flow/keys", a={"table": "sales"}, b={"table": "shops"})
    assert code == 200 and body["decision"] == "auto" and body["candidates"][0]["keys"] == [["shop_id", "shop_id"]] and body["basis"] == "표본 기준" and not body["remembered"]
    assert body["source_key"] and body["fingerprint"]
    assert body["candidates"][0]["score"] == pytest.approx(0.35 * 1 + 0.45 * 1 + 0.10 * 1 + 0.10 * 1)  # 이름 같음·겹침 1·타입·shops.shop_id 고유
    keys = [["shop_id", "shop_id"]]
    assert post(c, "/api/flow/keys/remember", source_key=body["source_key"], a="sales", b="shops", fingerprint=body["fingerprint"], keys=keys)[0] == 200
    again = post(c, "/api/flow/keys", a={"table": "sales"}, b={"table": "shops"})[1]
    assert again["remembered"] and again["candidates"][0]["basis"] == "기억"
    assert post(c, "/api/flow/keys/forget", source_key=body["source_key"], a="sales", b="shops", fingerprint=body["fingerprint"])[0] == 200
    assert not post(c, "/api/flow/keys", a={"table": "sales"}, b={"table": "shops"})[1]["remembered"]
    assert post(c, "/api/flow/keys", a={"table": "sales"})[1]["error"] == "이을 두 테이블(a, b)을 고르세요."
    assert post(c, "/api/flow/keys/remember", a="sales")[0] == 400


def test_keys_route_on_remote_sends_no_value_queries(c, monkeypatch):
    monkeypatch.setattr(held, "remote", lambda con: True)
    sent = spy_queries(monkeypatch)
    code, body = post(c, "/api/flow/keys", a={"table": "sales"}, b={"table": "shops"})
    assert code == 200 and body["basis"] == "표본 없음" and "원격 DB라 값을 읽는 조회를 새로 보내지 않았어요" in body["note"]
    assert body["candidates"][0]["keys"] == [["shop_id", "shop_id"]]  # 이름·타입만으로도 같은 이름 키가 1등
    assert sent == [], sent
    code, body = post(c, "/api/flow/keys", a={"table": "sales"}, b={"table": "shops"}, full=True)
    assert code == 400 and body["error"].startswith("원격 DB에서는 전체 값 확인을 하지 않아요")
    assert sent == []


def test_fanout_route(c, monkeypatch):
    graph = flow.from_body({"base": "sales", "joins": ["shops inner shop_id=shop_id"]})
    code, body = post(c, "/api/flow/fanout", graph=graph, join="n2")
    assert code == 200 and body["checked"] and body["joined_rows"] == 12 and body["right_unique"] and body["level"] is None
    assert post(c, "/api/flow/fanout", graph=graph, join="n1")[1]["error"] == "조인 단계를 고르세요."
    monkeypatch.setattr(held, "remote", lambda con: True)
    sent = spy_queries(monkeypatch)
    code, body = post(c, "/api/flow/fanout", graph=graph, join="n2")
    assert body["checked"] is False and sent == []


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
    assert body["text"] == "store별 건수가 0 이상인 것을 많은 순으로 봐요"
    code, body = post(c, "/api/sentence/compile", template="e09", slots={"무엇": "store", "N": 4}, body={"base": "sales"}, columns={"store": "text"})
    assert code == 200 and body["ok"] and body["runs"][0]["route"] == "/api/pivot"
    assert body["runs"][0]["body"]["pivot"] == {"rows": ["store"], "how": "count", "having": "전체 개수 >= 4", "order": "전체 개수 desc"}
    code, body = post(c, "/api/sentence/compile", template="e05", slots={"컬럼": "store", "글자": {"match": "으로 끝", "text": "x"}}, body={"base": "sales"})
    assert body["ok"] is True and body["runs"][0]["body"]["conditions"] == ["store ends x"]
    assert post(c, "/api/sentence/compile", template="e05", slots={"컬럼": "store", "글자": {"match": "엉뚱", "text": "x"}}, body={"base": "sales"})[1]["ok"] is False
    assert post(c, "/api/sentence/compile", template="zz")[1]["error"] == "'zz' 문장 틀이 없습니다."
