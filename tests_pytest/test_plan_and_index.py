"""인덱스: 실행 계획 보기(EXPLAIN만, 조회는 돌리지 않음)와 인덱스를 막지 않는 SQL 모양(정렬의 빈 값 위치, like·starts).
지어낸 SQLite 파일(인덱스 있음)과 DuckDB 파일에 실제로 연결하고, 다른 방언은 SQL로 바꿔 모양만 본다."""
import sqlite3

import duckdb
import pytest

from lazy import ibis


def client(connect):
    from core import server
    from query.search_app import app
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json=connect).status_code == 200
    return c


@pytest.fixture
def indexed(folder, tmp_path):
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table orders (id integer primary key, store integer, memo text, day date)")
    raw.executemany("insert into orders values (?, ?, ?, ?)", [(i, i % 9, f"m{i}", f"2026-01-{1 + i % 28:02d}") for i in range(300)])
    for col in ("store", "memo", "day"):
        raw.execute(f"create index ix_{col} on orders({col})")
    raw.commit()
    raw.close()
    c = client({"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"})
    yield lambda api="/api/plan", **body: c.post(api, json={"base": "orders", "limit": 20, **body}).get_json()
    c.post("/api/disconnect", json={})


def test_plan_tells_index_from_full_scan(indexed):
    assert indexed(conditions=["store = 3"])["full_scans"] == []
    assert indexed(periods=["day 2026-01-03 ~ 2026-01-05"])["full_scans"] == []
    assert indexed(conditions=["memo like 5"])["full_scans"] == ["orders"]  # 포함 검색은 인덱스를 쓸 수 없다
    assert indexed()["full_scans"] == ["orders"]
    found = indexed(conditions=["store = 3"])
    assert "USING INDEX ix_store" in found["text"] and found["note"] and "LIMIT" not in found["sql"].upper()  # 받아 두는 조회(기간·조건까지)
    tab = indexed("/api/sql/plan", sql="SELECT id FROM orders WHERE store = 2")
    assert tab["full_scans"] == [] and "ix_store" in tab["text"]


def test_user_explain_is_still_blocked(indexed):
    assert "error" in indexed("/api/sql/run", sql="EXPLAIN SELECT * FROM orders")


def test_plan_keeps_duckdb_connection_open(folder, tmp_path):
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute("create table orders as select range id, range % 9 store from range(100)")
    raw.close()
    c = client({"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")})
    try:
        found = c.post("/api/plan", json={"base": "orders", "limit": 5, "conditions": ["store = 3"]}).get_json()
        assert found["full_scans"] is None and "DuckDB" in found["note"] and found["text"]
        assert c.post("/api/query", json={"base": "orders", "limit": 5}).get_json()["count"] == 100  # 계획을 본 뒤에도 연결이 살아 있다
    finally:
        c.post("/api/disconnect", json={})


@pytest.mark.parametrize("dialect, words", [("oracle", "PLAN_TABLE"), ("MSSQL", "SHOWPLAN"), ("bigquery", "처리 용량")])
def test_refused_dialects_explain_why(dialect, words):
    from core import plan
    with pytest.raises(ValueError, match=words):
        plan.explain(None, "SELECT 1", dialect)


def test_file_connection_has_no_plan(folder, tmp_path):
    import polars as pl
    pl.DataFrame({"a": [1, 2]}).write_parquet(tmp_path / "t.parquet")
    c = client({"kind": "folder", "path": str(tmp_path), "files": [str(tmp_path / "t.parquet")]})
    try:
        assert "실행 계획이 없습니다" in c.post("/api/plan", json={"base": "t", "limit": 5}).get_json()["error"]
    finally:
        c.post("/api/disconnect", json={})


@pytest.mark.parametrize("dialect", ["mssql", "mysql", "postgres", "oracle", "snowflake", "sqlite", "bigquery", "duckdb"])
def test_sort_keeps_native_null_order(dialect):
    from core import parse
    t = ibis.table({"id": "int64"}, name="t")
    for desc in (False, True):  # Ibis가 빈 값을 뒤로 맞추려 붙이는 NULLS LAST·CASE WHEN이 없어야 인덱스로 정렬할 수 있다
        tail = ibis.to_sql(t.order_by(parse.native_key(t, t.id, desc, dialect)), dialect=dialect).upper().rpartition("ORDER BY")[2]
        assert "NULLS" not in tail and "CASE" not in tail


@pytest.mark.parametrize("dialect", ["mssql", "postgres", "mysql", "oracle", "sqlite", "bigquery"])
def test_like_and_starts_shapes(dialect):
    from core import parse
    t = ibis.table({"s": "string"}, name="t")
    where = lambda line: " ".join(ibis.to_sql(t.filter(parse.parse_one(line, t)), dialect=dialect).split()).upper().partition("WHERE")[2]
    assert "LIKE '%AB%'" in where("s like Ab") and "CONTAINS(" not in where("s like Ab")  # MSSQL의 전문 검색 CONTAINS가 아니다
    assert where("s starts 김").endswith("LIKE '김%'")  # 컬럼에 함수를 씌우지 않는다(인덱스를 탈 수 있다)
    assert "LIKE" not in where("s like 5%") and "LIKE" not in where("s starts a_b")  # 값의 %·_는 와일드카드로 쓰지 않는다
