"""8차 감사 반영 8B: 쿼리. 진짜 DuckDB로 시험하고 데이터는 시험 안에서 만든다."""
import pytest

from test_dash_calc import db  # noqa: F401  진짜 DuckDB 파일에 연결한 시험 클라이언트


def con_with(**tables):
    from lazy import ibis
    con = ibis.duckdb.connect()
    for name, data in tables.items():
        con.create_table(name, ibis.memtable(data))
    return con


def table_node(i, name):
    return {"id": i, "kind": "table", "params": {"table": name}}


def test_union_of_same_table_is_refused_in_flow():
    """8B1: 같은 뿌리 테이블 둘을 이어붙이면 거부(안내 문구), 다른 테이블 둘은 된다."""
    from query import flow
    g = {"nodes": [table_node("a", "orders"), table_node("b", "orders"), table_node("c", "items"), {"id": "u", "kind": "union", "params": {"how": "left"}}], "edges": [["a", "u"]]}
    schemas = {"orders": {"columns": {"id": "num"}}, "items": {"columns": {"id": "num"}}}
    bad = flow.check_edge(g, "b", "u", schemas)
    assert bad and "다르고 서로 겹치지 않아야" in bad
    assert flow.check_edge(g, "c", "u", schemas) is None


def test_self_join_works_in_search_and_flow():
    """8B2: 자기 조인이 검색과 흐름 모두 3행, 오른쪽 컬럼은 _right."""
    from core import parse
    from query import flow
    con = con_with(emp={"id": [1, 2, 3], "boss": [None, 1, 1], "name": ["a", "b", "c"]})
    get = lambda name, schema=None: con.table(name)
    out = parse.apply_joins(con.table("emp"), [("emp", "left", [("boss", "id")], None)], get)
    assert out.count().execute() == 3 and "id_right" in out.columns and "name_right" in out.columns
    g = {"nodes": [table_node("a", "emp"), table_node("b", "emp"), {"id": "j", "kind": "join", "params": {"how": "left", "keys": [["boss", "id"]]}}], "edges": [["a", "j"], ["b", "j"]]}
    assert flow.to_expr(g, "j", get).count().execute() == 3
    # 다른 테이블과의 조인 컬럼 이름 규칙은 그대로
    con2 = con_with(emp={"id": [1, 2], "x": [1, 2]}, other={"id": [1, 2], "y": [5, 6]})
    j = parse.apply_joins(con2.table("emp"), [("other", "inner", [("id", "id")], None)], lambda n: con2.table(n))
    assert tuple(j.columns) == ("id", "x", "y")


def test_self_join_asof_and_search_route(db):
    """8B2: asof 자기 조인, 검색 길."""
    from core import parse
    con = con_with(t={"k": [1, 1], "ts": [1, 5]})
    out = parse.apply_joins(con.table("t"), [("t", "asof", [("k", "k")], ("ts", "ts"))], lambda n: con.table(n))
    assert out.count().execute() == 2
    r = db.post("/api/query", json={"base": "orders", "joins": ["orders left store=store"], "limit": 5, "offset": 0, "fresh": True})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]


def test_remote_computed_copy_asks_scan_gate_first(db, monkeypatch):
    """8B3: 원격 DB에서 연결이 못 하는 계산으로 사본을 받기 전에 처리 용량 확인(X-Needs-Confirm)."""
    from core import held, querying, scan_size
    from core import metric_file
    monkeypatch.setattr(metric_file, "registry", lambda: {"total": {"numerator": {"agg": "sum", "column": "amt", "where": []}, "denominator": None, "format": "number", "description": ""}})
    monkeypatch.setattr(held, "remote", lambda con: True)
    monkeypatch.setattr(querying, "agg_unsupported", lambda *a, **k: True)
    monkeypatch.setattr(scan_size, "limit_check", lambda *a, **k: {"over": True, "unknown": False, "blocked": False, "bytes": 9, "limit": 1, "text": "너무 커요"})
    body = {"base": "orders", "x": "store", "series": [{"y": "amt", "agg": "sum"}]}
    for path, extra in [("/api/chart", {}), ("/api/summary", {}), ("/api/pivot", {"pivot": {"rows": ["store"], "column": "store", "how": "sum", "value": "amt"}}),
                        ("/api/values", {"column": "store"}), ("/api/metrics/run", {"names": ["total"]})]:
        r = db.post(path, json={**body, **extra})
        assert r.headers.get("X-Needs-Confirm") == "1" and r.get_json()["needs_confirm"], (path, r.get_data(as_text=True)[:200])
    # 확인(confirmed)하면 문 앞에서 막지 않는다
    monkeypatch.setattr(held, "ensure", lambda *a, **k: (None,))
    r = db.post("/api/chart", json={**body, "confirmed": True})
    assert "X-Needs-Confirm" not in r.headers


def test_usage_write_failure_does_not_fail_responses(db, monkeypatch, tmp_path):
    """8B4: 사용 횟수 파일을 못 써도 지표·검색 응답은 성공."""
    from core import metric_file, use_counts
    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(use_counts, "use_many", boom)
    monkeypatch.setattr(metric_file, "registry", lambda: {"total": {"numerator": {"agg": "sum", "column": "amt", "where": []}, "denominator": None, "format": "number", "description": ""}})
    r = db.post("/api/metrics/run", json={"base": "orders", "names": ["total"]})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert r.get_json()["values"]["total"] is not None


def test_nl2sql_more_tables_note_points_below():
    """8B5: '테이블이 더 있으나' 문구는 목록 앞에 있으니 '아래 목록'."""
    from query import nl2sql
    text = nl2sql.system_prompt({"dialect": "duckdb", "tables": [], "more_tables": True, "metrics": [], "virtual": []}) if hasattr(nl2sql, "system_prompt") else ""
    assert "위 목록" not in text and "아래 목록" in text
