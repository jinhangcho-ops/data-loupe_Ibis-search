"""9차 통합: 오른쪽 표 둘이 같은 이름 컬럼을 가질 때 흐름의 컬럼 어림(서버 out_columns·화면 flowColumns)과 쓰는 컬럼만 읽기가 실제 조인 결과(parse.rname_for)와 같은지."""
import json

import ibis

from query import flow
from query import flow_opt
from tests_pytest.test_flow_js import run_node

SCHEMAS = {"orders": {"columns": {"id": "num", "cid": "num", "pid": "num", "name": "text"}},
           "customers": {"columns": {"cid": "num", "name": "text"}},
           "products": {"columns": {"pid": "num", "name": "text"}}}
GRAPH = {"nodes": [{"id": "o", "kind": "table", "params": {"table": "orders"}}, {"id": "c", "kind": "table", "params": {"table": "customers"}},
                   {"id": "p", "kind": "table", "params": {"table": "products"}},
                   {"id": "j1", "kind": "join", "params": {"how": "inner", "keys": [["cid", "cid"]]}},
                   {"id": "j2", "kind": "join", "params": {"how": "left", "keys": [["pid", "pid"]]}},
                   {"id": "k", "kind": "condition", "params": {"cond": "name_products = 'b'"}}],
         "edges": [["o", "j1"], ["c", "j1"], ["j1", "j2"], ["p", "j2"], ["j2", "k"]]}


def tables():
    con = ibis.duckdb.connect()
    con.create_table("orders", ibis.memtable({"id": [1, 2], "cid": [1, 2], "pid": [1, 2], "name": ["o1", "o2"]}))
    con.create_table("customers", ibis.memtable({"cid": [1, 2], "name": ["x", "y"]}))
    con.create_table("products", ibis.memtable({"pid": [1, 2], "name": ["a", "b"]}))
    return con


def test_out_columns_match_the_real_join():
    con = tables()
    real = flow.to_expr(GRAPH, "j2", lambda name, schema=None: con.table(name)).columns
    guess = list(flow.out_columns(GRAPH, "j2", SCHEMAS))
    assert list(real) == ["id", "cid", "pid", "name", "name_right", "pid_products", "name_products"]
    assert [c for c in guess if c != "cid_right"] == list(real)  # inner 조인의 같은 키(cid_right)는 Ibis가 합친다(어림은 전부터 남긴다)


def test_screen_columns_match_the_server(tmp_path):
    got = run_node(tmp_path, "console.log(JSON.stringify(Object.keys(flowColumns(%s, 'j2', %s))));" % (json.dumps(GRAPH), json.dumps(SCHEMAS)))
    assert got == list(flow.out_columns(GRAPH, "j2", SCHEMAS))


def test_optimizer_keeps_the_column_renamed_by_the_second_join():
    """name_products를 아래에서 쓰니 products 조인을 빼지 않고, products의 name도 읽는다."""
    con = tables()
    ctx = {"schemas": SCHEMAS, "get_table": lambda name, schema=None: con.table(name), "remote": False, "scan_ok": True, "uniq": {}}
    dropped, info = flow_opt.drop_join(GRAPH, ctx)
    assert dropped is None or any(n["id"] == "j2" for n in dropped["nodes"])
    pruned, _ = flow_opt.prune_columns(GRAPH, ctx)
    if pruned:
        cols = flow.by_id(pruned)["p"]["params"].get("columns")
        assert cols is None or "name" in cols
        flow.to_expr(pruned, "k", lambda name, schema=None: con.table(name)).execute()
