"""흐름 모델(flow.py): 이음 검사 이유 문장(순위·순환·조인 키 타입·다른 연결·이어붙일 컬럼·집계 뒤 키·숫자·기간), graph↔body 왕복(core.build와 같은 SQL),
body로 못 바꾸는 흐름, 단계별 행 수(원격 DB는 확인 전에 세지 않음). 데이터는 테스트 안에서 지어낸다."""
import duckdb
import pytest
from lazy import ibis

from core import querying
from query import flow
from core import server

SCHEMAS = {
    "orders": {"columns": {"id": "num", "user_id": "num", "code": "text", "amt": "num", "day": "date"}, "source_key": "k1"},
    "users": {"columns": {"id": "num", "name": "text", "country": "text"}, "source_key": "k1"},
    "other": {"columns": {"id": "num", "x": "text"}, "source_key": "k2"},
    "plain": {"columns": {"a": "text", "b": "text"}, "source_key": "k1"},
}


def g(*nodes, edges=()):
    return {"nodes": [{"id": i, "kind": k, "params": p} for i, k, p in nodes], "edges": [list(e) for e in edges]}


def table(i, name):
    return (i, "table", {"table": name})


def reason(graph, a, b, schemas=SCHEMAS):
    return flow.check_edge(graph, a, b, schemas)


def test_rank_reversal_and_ends():
    graph = g(table("t", "orders"), ("c", "condition", {"cond": "amt > 1"}), ("p", "period", {"line": "day 2026-01-01 ~"}), ("a", "aggregate", {"by": ["code"], "series": [{"agg": "count"}]}),
              ("ch", "chart", {"x": "code"}))
    assert reason(graph, "c", "p") == "조건 뒤에 기간 단계를 놓을 수 없어요. 순서는 테이블 → 이어붙이기 → 조인 → 기간 → 조건 → 계산 → 중복 제거 → 집계·지표 → 차트·카드예요."
    assert reason(graph, "c", "t") == "조건 뒤에 테이블을 이을 수 없어요. 테이블은 흐름의 시작이에요."
    assert reason(graph, "ch", "c") == "차트 뒤에는 다른 단계를 이을 수 없어요. 차트·카드는 흐름의 끝이에요."
    assert reason(graph, "t", "p") is None and reason(graph, "p", "c") is None and reason(graph, "t", "a") is None
    assert reason(graph, "a", "ch") is None
    assert reason(graph, "c", "c") == "단계를 자기 자신에게 이을 수 없어요."


def test_one_input_per_step_and_duplicates():
    graph = g(table("t", "orders"), table("u", "users"), ("c", "condition", {"cond": "amt > 1"}), edges=[("t", "c")])
    assert reason(graph, "u", "c") == "조건에는 이미 앞 단계(테이블 ‘orders’)가 이어져 있어요. 한 단계에는 하나만 이을 수 있어요."
    assert reason(graph, "t", "c") == "이미 이어져 있어요."


def test_cycle_is_found_by_dfs():
    graph = g(table("t", "orders"), ("c1", "condition", {"cond": "amt > 1"}), ("c2", "condition", {"cond": "amt > 2"}), ("c3", "condition", {"cond": "amt > 3"}),
              edges=[("t", "c1"), ("c1", "c2"), ("c2", "c3")])
    assert reason(graph, "c3", "c1") == "순환이 생겨요: 조건(c1) → 조건(c2) → 조건(c3) → 조건(c1)."
    assert "이미 앞 단계" in reason(graph, "c1", "c3")  # 건너뛰는 이음은 순환이 아니라 입력이 이미 있어서 막힌다


def test_join_key_type_mismatch_suggests_cast():
    graph = g(table("o", "orders"), table("u", "users"), ("j", "join", {"how": "left", "keys": [["code", "id"]]}), edges=[("o", "j")])
    found = flow.edge_problem(graph, "u", "j", SCHEMAS)
    assert found["code"] == "key_type"
    assert found["reason"] == "조인 키 ‘code’(글자)과 ‘id’(숫자)의 종류가 달라 이을 수 없어요. 왼쪽 ‘code’을(를) 숫자로 바꿔(cast) 이어 보세요."
    assert found["suggest"] == {"side": "left", "column": "code", "to": "num"}
    ok = g(table("o", "orders"), table("u", "users"), ("j", "join", {"how": "left", "keys": [["user_id", "id"]]}), edges=[("o", "j")])
    assert reason(ok, "u", "j") is None
    missing = g(table("o", "orders"), table("u", "users"), ("j", "join", {"how": "inner", "keys": [["user_id", "nope"]]}), edges=[("o", "j")])
    assert reason(missing, "u", "j") == "오른쪽에 ‘nope’ 컬럼이 없어요."


def test_different_connection_and_union_without_common_columns():
    graph = g(table("o", "orders"), table("x", "other"), ("j", "join", {"how": "inner", "keys": [["id", "id"]]}), edges=[("o", "j")])
    assert reason(graph, "x", "j") == "서로 다른 연결의 테이블이라 이을 수 없어요(k1, k2). 같은 연결의 테이블끼리만 이을 수 있어요."
    union = g(table("o", "orders"), table("p", "plain"), ("u", "union", {"how": "inner"}), edges=[("o", "u")])
    assert reason(union, "p", "u") == "이어붙일 테이블들에 같은 이름의 컬럼이 없어요. 컬럼 이름을 맞춘 뒤 이어 주세요."
    both = g(table("o", "orders"), table("u2", "users"), ("u", "union", {"how": "left"}), edges=[("o", "u")])
    assert reason(both, "u2", "u") is None  # id가 같다


def test_key_lost_after_aggregate_and_number_needed_and_period_needs_date():
    agg = ("a", "aggregate", {"by": ["code"], "series": [{"agg": "sum", "y": "amt", "name": "total"}]})
    graph = g(table("t", "orders"), agg, ("ch", "chart", {"x": "day"}), ("ch2", "chart", {"x": "code", "series": [{"agg": "sum", "y": "total"}]}),
              table("u", "users"), ("j", "join", {"how": "inner", "keys": [["name", "user_id"]]}), edges=[("t", "a"), ("u", "j")])
    assert "집계한 뒤에는 묶은 컬럼(code, total)만 남아서 ‘day’을(를) 쓸 수 없어요" in reason(graph, "a", "ch")
    assert reason(graph, "a", "ch2") is None
    assert reason(graph, "a", "j") == "오른쪽는 집계한 뒤라 묶은 컬럼(code, total)만 남아서 조인 키 ‘user_id’을(를) 쓸 수 없어요. 집계에 그 컬럼을 묶음 기준으로 넣어 주세요."
    text = g(table("t", "orders"), ("a", "aggregate", {"by": ["day"], "series": [{"agg": "sum", "y": "code"}]}))
    assert reason(text, "t", "a") == "‘code’은 글자 컬럼이라 합계를 구할 수 없어요. 숫자로 바꾸거나(cast) 개수·고유 개수를 쓰세요."
    assert reason(g(table("t", "orders"), ("a", "aggregate", {"by": ["day"], "series": [{"agg": "count_distinct", "y": "code"}]})), "t", "a") is None  # 고유 개수는 글자도 된다
    nodate = g(table("t", "plain"), ("p", "period", {"line": "a 2026-01-01 ~"}))
    assert reason(nodate, "t", "p") == "앞 단계에 날짜 컬럼이 없어서 기간을 걸 수 없어요. 날짜 컬럼이 있는 단계 뒤에 놓으세요."
    wrong = g(table("t", "orders"), ("p", "period", {"line": "code 2026-01-01 ~"}))
    assert reason(wrong, "t", "p") == "‘code’은 글자 컬럼이라 기간을 걸 수 없어요. 날짜 컬럼(day)을 고르세요."
    assert reason(g(table("t", "orders"), ("p", "period", {"line": "day 2026-01-01 ~"})), "t", "p") is None


def test_check_graph_rechecks_every_edge_in_place():
    graph = g(table("o", "orders"), table("u", "users"), ("j", "join", {"how": "left", "keys": [["user_id", "id"]]}), ("c", "condition", {"cond": "country = KR"}),
              edges=[("o", "j"), ("u", "j"), ("j", "c")])
    assert flow.check_graph(graph, SCHEMAS) == []  # 왼쪽·오른쪽 자리가 그대로 지켜진다
    graph["nodes"][2]["params"]["keys"] = [["code", "id"]]
    found = flow.check_graph(graph, SCHEMAS)
    assert [f["code"] for f in found] == ["key_type"] and found[0]["edge"] == ["u", "j"]
    with pytest.raises(ValueError, match="'a'가 겹쳐요"):
        flow.check_shape(g(table("a", "orders"), table("a", "users")))
    with pytest.raises(ValueError, match="두 단계가 흐름에 있어야"):
        flow.check_shape(g(table("a", "orders"), edges=[("a", "zzz")]))


# ---------- graph ↔ body ----------

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "f.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table orders as select i as id, i % 3 as user_id, ((i + 1) * 10)::double as amt, date '2026-01-01' + i::int as day from range(12) t(i)")
    raw.execute("create table users as select i as id, chr(65 + i::int) as name, case when i < 2 then 'KR' else 'US' end as country from range(3) t(i)")
    raw.execute("create table extra as select i as id, i * 2 as score from range(12) t(i)")
    raw.close()
    con = ibis.duckdb.connect(str(path))
    yield con
    con.disconnect()


@pytest.fixture
def served(db, tmp_path, monkeypatch):
    """core.joined가 쓰는 server 연결을 이 DuckDB로(core.build와 비교하려고)."""
    monkeypatch.setitem(server.state, "con", db)
    monkeypatch.setitem(server.state, "opened", {"kind": "folder", "path": str(tmp_path)})
    monkeypatch.setitem(server.state, "tables", {})
    monkeypatch.setitem(server.state, "names", {})
    monkeypatch.setattr(server, "virtual_tables", lambda: {})
    return db


BODY = {"schema": "", "base": "orders", "joins": ["users left user_id=id"], "periods": ["day 2026-01-02 ~ 2026-01-10"], "conditions": ["country = KR", {"any": ["amt > 20", "amt < 15"]}],
        "calcs": ["누적합 amt 기준 day 이름 run"], "sorts": ["day asc"], "columns": ["id, amt, run"]}


def test_body_round_trip_matches_core_build(served):
    graph = flow.from_body(BODY)
    assert [n["kind"] for n in graph["nodes"]] == ["table", "join", "table", "period", "condition", "condition", "calc"]
    assert flow.check_graph(graph, flow.schemas_of(graph, lambda n, s=None: served.table(n), "k")) == []
    again = flow.to_body(graph)
    assert again == BODY  # 줄과 순서, 정렬·표시 컬럼(extra)까지 그대로
    first, second = querying.build(BODY), querying.build(again)
    assert str(ibis.to_sql(first)) == str(ibis.to_sql(second))
    assert first.to_polars().to_dicts() == second.to_polars().to_dicts()
    # 손으로 센 값: KR(user 0·1)이고 2026-01-02~10인 id 1·3·4·6·7·9 중 amt가 20 초과이거나 15 미만인 것(id 1은 amt 20이라 빠진다)
    rows = second.to_polars().to_dicts()
    assert [r["id"] for r in rows] == [3, 4, 6, 7, 9] and [r["amt"] for r in rows] == [40, 50, 70, 80, 100]


def test_union_and_asof_lines_round_trip():
    body = {"schema": "", "base": "orders", "union": {"tables": ["extra"], "how": "inner"}, "joins": ["users inner user_id=id"], "periods": [], "conditions": [], "calcs": []}
    graph = flow.from_body(body)
    assert [n["kind"] for n in graph["nodes"]] == ["table", "union", "table", "join", "table"]
    assert flow.to_body(graph) == body
    asof = {"schema": "", "base": "orders", "joins": ["users asof day>=day, id=id"], "periods": [], "conditions": [], "calcs": []}
    assert flow.to_body(flow.from_body(asof)) == asof


def test_aggregate_and_view_nodes_round_trip():
    body = {**BODY, "aggregate": {"by": ["name"], "series": [{"agg": "sum", "y": "amt"}]}, "view": {"kind": "chart", "x": "name"}}
    graph = flow.from_body(body)
    assert [n["kind"] for n in graph["nodes"]][-2:] == ["aggregate", "chart"]
    assert flow.to_body(graph) == body
    assert flow.to_body(graph, target=graph["nodes"][-2]["id"])["aggregate"]["by"] == ["name"] and "view" not in flow.to_body(graph, target=graph["nodes"][-2]["id"])


def test_branches_before_a_join_cannot_become_a_body_but_make_an_expr(db):
    graph = g(table("o", "orders"), table("u", "users"), ("c", "condition", {"cond": "country = KR"}), ("j", "join", {"how": "inner", "keys": [["user_id", "id"]]}),
              edges=[("o", "j"), ("u", "c"), ("c", "j")])
    assert "조인 앞 가지에 조건·집계가 있어" in flow.body_problem(graph, "j")
    with pytest.raises(ValueError, match="단계 카드"):
        flow.to_body(graph, "j")
    expr = flow.to_expr(graph, "j", lambda n, s=None: db.table(n))
    assert expr.count().execute() == 8  # KR 사용자 0·1의 주문 8건(id % 3 이 0 또는 1)


def test_dedupe_becomes_a_window_filter_line():
    assert flow.dedupe_line({"keys": ["user_id"], "order": "day desc"}) == "창거르기 row_number <= 1 기준 day desc 그룹 user_id"
    with pytest.raises(ValueError, match="기준 컬럼"):
        flow.dedupe_line({"keys": []})


def test_dedupe_node_keeps_the_latest_row_per_key(db):
    graph = g(table("o", "orders"), ("d", "dedupe", {"keys": ["user_id"], "order": "day desc"}), edges=[("o", "d")])
    rows = flow.to_expr(graph, "d", lambda n, s=None: db.table(n)).order_by("id").to_polars().to_dicts()
    assert [r["id"] for r in rows] == [9, 10, 11]  # 사용자마다 가장 늦은 주문


def test_row_counts_per_step(served):
    graph = flow.from_body(BODY)
    counted = []
    out = flow.row_counts(graph, flow.one_sink(graph), lambda e: (counted.append(1), e.count().execute())[1], None, lambda n, s=None: served.table(n))
    assert not out["needs_confirm"]
    by = {s["id"]: s["rows"] for s in out["steps"]}
    # n1 orders 12행 · n3 users 3행 · n2 left join 12행 · n4 기간(01-02~01-10) 9행 · n5 KR 6행 · n6 amt 20 초과 또는 15 미만 5행 · n7 계산 5행
    assert by["n1"] == 12 and by["n3"] == 3 and by["n2"] == 12 and by["n4"] == 9 and by["n5"] == 6
    assert by["n6"] == 5 and by["n7"] == 5
    assert len(counted) == len(out["steps"])


def test_row_counts_do_not_run_on_remote_without_confirmation(served):
    graph = flow.from_body(BODY)
    asked = []
    out = flow.row_counts(graph, flow.one_sink(graph), lambda e: asked.append(1), None, lambda n, s=None: served.table(n), remote=True)
    assert out["needs_confirm"] and "원격 DB" in out["note"] and asked == [] and all(s["rows"] is None for s in out["steps"])
    sure = flow.row_counts(graph, flow.one_sink(graph), lambda e: e.count().execute(), None, lambda n, s=None: served.table(n), remote=True, confirmed=True)
    assert not sure["needs_confirm"] and sure["steps"][0]["rows"] == 12


def test_body_keeps_one_fixed_stage_order():
    """body는 조인 → 기간·조건 → 계산 순으로만 적용하므로, 그 순서를 벗어난 흐름은 단계 카드로 바꾸지 않는다."""
    early = g(table("o", "orders"), ("c", "condition", {"cond": "amt > 1"}), table("u", "users"), ("j", "join", {"how": "inner", "keys": [["user_id", "id"]]}),
              edges=[("o", "c"), ("c", "j"), ("u", "j")])
    assert flow.body_problem(early, "j") == "조인 앞에 기간·조건·계산 단계가 있어 단계 카드(검색 body)로 바꿀 수 없어요. 흐름 보기에서만 쓰는 모양이에요."
    late = g(table("o", "orders"), ("k", "calc", {"line": "누적합 amt 기준 day 이름 run"}), ("c", "condition", {"cond": "amt > 1"}), edges=[("o", "k"), ("k", "c")])
    assert "계산 단계 뒤에 기간·조건 단계가 있어" in flow.body_problem(late, "c")
    fine = g(table("o", "orders"), ("c", "condition", {"cond": "amt > 1"}), ("k", "calc", {"line": "누적합 amt 기준 day 이름 run"}), edges=[("o", "c"), ("c", "k")])
    assert flow.body_problem(fine, "k") is None


def test_row_counts_use_the_expression_when_the_flow_is_not_a_body(db):
    graph = g(table("o", "orders"), table("u", "users"), ("c", "condition", {"cond": "country = KR"}), ("j", "join", {"how": "inner", "keys": [["user_id", "id"]]}),
              edges=[("o", "j"), ("u", "c"), ("c", "j")])
    out = flow.row_counts(graph, "j", lambda e: e.count().execute(), None, lambda n, s=None: db.table(n))
    assert {s["id"]: s["rows"] for s in out["steps"]} == {"o": 12, "u": 3, "c": 2, "j": 8}
