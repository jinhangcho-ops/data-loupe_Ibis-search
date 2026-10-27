"""23차 흐름 화면: 중간 단계를 지워도 뒤 단계(조인)의 입력 차례가 그대로인지, 화면의 컬럼 어림(flowColumns)이 서버 out_columns와 같은지(이어붙이기 how마다·집계 계열 종류)."""
import json

from core import sql_funcs
from query import flow
from tests_pytest.test_flow_js import run_node

T = lambda i, t: {"id": i, "kind": "table", "params": {"table": t}}


def test_remove_keeps_join_sides(tmp_path):
    """오른쪽 가지의 조건을 먼저 만든 흐름에서 그 조건을 지워도 조인의 왼쪽·오른쪽이 바뀌지 않는다. 입력·출력이 여럿인 단계를 지우면 다시 잇지 않고 남은 이음 차례도 그대로다."""
    nodes = [T("t1", "a"), T("t2", "b"), {"id": "c", "kind": "condition", "params": {}}, {"id": "j", "kind": "join", "params": {}}]
    right = {"nodes": nodes, "edges": [["t2", "c"], ["t1", "j"], ["c", "j"]]}  # c가 오른쪽
    left = {"nodes": nodes, "edges": [["t2", "j"], ["t1", "c"], ["c", "j"]]}  # c가 왼쪽(뒤에 만듦)
    many = {"nodes": [T("t1", "a"), T("t2", "b"), {"id": "u", "kind": "union", "params": {}}, {"id": "c", "kind": "condition", "params": {}}, {"id": "k", "kind": "calc", "params": {}}],
            "edges": [["t1", "u"], ["u", "c"], ["t2", "u"], ["c", "k"]]}
    got = run_node(tmp_path, "console.log(JSON.stringify([flowRemove(%s, 'c').edges, flowRemove(%s, 'c').edges, flowRemove(%s, 'u').edges]));" % (json.dumps(right), json.dumps(left), json.dumps(many)))
    assert flow.join_sides({"nodes": nodes, "edges": got[0]}, "j") == ("t1", "t2")
    assert flow.join_sides({"nodes": nodes, "edges": got[1]}, "j") == ("t2", "t1")
    assert got[2] == [["c", "k"]]


SCHEMAS = {"a": {"columns": {"id": "num", "x": "text", "y": "num", "d": "date"}}, "b": {"columns": {"id": "num", "x": "text"}}}


def test_union_columns_match_the_server(tmp_path):
    """left는 첫 입력 컬럼 모두, inner·intersect·except는 모든 입력에 있는 컬럼만, left·inner는 '출처'를 붙인다(서버와 같은 차례·종류)."""
    graphs = {how: {"nodes": [T("t1", "a"), T("t2", "b"), {"id": "u", "kind": "union", "params": {"how": how}}], "edges": [["t1", "u"], ["t2", "u"]]} for how in flow.parse.UNIONS}
    got = run_node(tmp_path, "const gs = %s, s = %s; console.log(JSON.stringify(Object.fromEntries(Object.entries(gs).map(([h, g]) => [h, Object.entries(flowColumns(g, 'u', s))]))));" % (json.dumps(graphs), json.dumps(SCHEMAS)))
    for how, g in graphs.items():
        assert got[how] == [list(kv) for kv in flow.out_columns(g, "u", SCHEMAS).items()], how
    assert dict(got["inner"]) == {"id": "num", "x": "text", "출처": "text"}


def test_aggregate_kinds_match_the_server(tmp_path):
    """집계 계열의 종류는 함수 사전의 kind_out: max(날짜)는 date, string_agg는 text, 비율은 num, 예전 이름(nunique)도 읽는다."""
    g = {"nodes": [T("t1", "a"), {"id": "ag", "kind": "aggregate", "params": {"by": ["x", ""], "series": [
        {"agg": "max", "y": "d", "name": "last"}, {"agg": "string_agg", "y": "x", "name": "names"}, {"agg": "count", "y": ""}, {"agg": "sum", "y": "y", "ratio": True, "name": "r"}, {"agg": "nunique", "y": "x", "name": "n"}]}}],
         "edges": [["t1", "ag"]]}
    kinds = {i: {"kind_out": f["kind_out"]} for i, f in sql_funcs.FUNCS.items()}
    stub = "globalThis.Funcs = { get: id => (%s)[(%s)[id] ?? id] };" % (json.dumps(kinds), json.dumps(sql_funcs.ALIASES))  # 사전(Funcs.get)을 서버 값으로 흉내
    got = run_node(tmp_path, stub + "console.log(JSON.stringify(Object.entries(flowColumns(%s, 'ag', %s))));" % (json.dumps(g), json.dumps(SCHEMAS)))
    want = flow.out_columns(g, "ag", SCHEMAS)
    assert got == [list(kv) for kv in want.items()]
    assert want["last"] == "date" and want["names"] == "text"
