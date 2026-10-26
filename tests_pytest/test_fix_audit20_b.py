"""20차 B: 집계가 본 흐름 가운데 있는 흐름의 단계 카드 바꾸기(B1), inner 조인 부풂(B2), 오른쪽이 빈 조인의 행 수(B3). 값은 시험 안에서 지어낸다."""
import json
import shutil
import subprocess

import pytest
from lazy import ibis

from conftest import ROOT
from core import server
from query import flow, join_keys
from query import flow_app  # noqa: F401  server.constants("query")["flow"]

# 최적화 규칙 3(미리 집계)을 적용한 뒤의 모양: 테이블 → 집계 → 조인 → 집계
PRE = {"nodes": [{"id": "n1", "kind": "table", "params": {"table": "sales"}}, {"id": "n2", "kind": "table", "params": {"table": "products"}},
                 {"id": "pre1", "kind": "aggregate", "params": {"by": ["pid"], "series": [{"agg": "sum", "y": "amt", "name": "amt"}]}},
                 {"id": "n3", "kind": "join", "params": {"how": "inner", "keys": [["pid", "pid"]]}},
                 {"id": "n4", "kind": "aggregate", "params": {"by": ["cat"], "series": [{"agg": "sum", "y": "amt", "name": "s"}]}}],
       "edges": [["n1", "pre1"], ["pre1", "n3"], ["n2", "n3"], ["n3", "n4"]]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_body_target_is_last_step_before_tail(tmp_path):
    """B1: 단계 카드로 바꿀 끝 단계는 끝 집계 바로 앞(조인)이다. 앞쪽 집계로 들어가는 테이블이 아니다."""
    head = "globalThis.SERVER = {flow: %s};\n" % json.dumps(server.constants("query")["flow"], ensure_ascii=False)
    script = tmp_path / "t.js"
    script.write_text(head + (ROOT / "static/query/flow.js").read_text(encoding="utf-8") + "\nconsole.log(JSON.stringify(flowBodyTarget(%s)));" % json.dumps(PRE), encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == "n3"


def test_aggregate_inside_chain_refuses_body():
    """B1: 본 흐름 가운데 집계가 있으면 조인을 빠뜨린 body 대신 바꿀 수 없다는 이유를 준다."""
    assert "흐름 보기에서만" in flow.body_problem(PRE, "n3")
    with pytest.raises(ValueError, match="흐름 보기에서만"):
        flow.to_body(PRE, "n3")
    assert flow.body_problem(PRE, "n1") is None  # 집계 앞 테이블만이면 그대로 바꿀 수 있다


def test_inner_fanout_counts_matched_rows_only():
    """B2: inner 조인에서 짝 없는 왼쪽 행이 빠져 행 수가 같아 보여도, 짝 있는 행이 겹쳐 늘었으면 부풂이다."""
    con = ibis.duckdb.connect()
    left = con.create_table("l", ibis.memtable({"k": [1, 2], "amt": [100, 100]}))
    right = con.create_table("r", ibis.memtable({"k": [1, 1], "tag": ["a", "b"]}))
    got = join_keys.fanout(left, right, [("k", "k")], "inner", "r")
    assert got["inflated"] and got["level"] == "bad"
    assert "1행에서 2행으로" in got["text"]


def test_join_without_right_input_says_why():
    """B3: 오른쪽 입력이 없는 조인의 식을 만들면 'None' 대신 이유를 담은 ValueError."""
    con = ibis.duckdb.connect()
    con.create_table("sales", ibis.memtable({"id": [1, 2]}))
    g = {"nodes": [{"id": "n1", "kind": "table", "params": {"table": "sales"}}, {"id": "n3", "kind": "join", "params": {"how": "inner", "keys": []}}], "edges": [["n1", "n3"]]}
    with pytest.raises(ValueError, match="오른쪽에 테이블이 이어지지 않았어요"):
        flow.to_expr(g, "n3", lambda name, schema=None: con.table(name))
