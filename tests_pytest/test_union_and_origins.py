"""검색의 이어붙이기(left·inner, 출처 컬럼)와 컬럼마다 원래 테이블(머리글 아래 작은 글씨). 지어낸 작은 DuckDB 파일로 실제 연결한다."""
import duckdb
import pytest


@pytest.fixture
def searching(folder, tmp_path):
    import server
    from app import app
    raw = duckdb.connect(str(tmp_path / "shop.duckdb"))
    raw.execute("create table orders as select i as id, i % 3 + 1 as cust, 'o' as kind, i * 10 as amount from range(1, 6) t(i)")
    raw.execute("create table old_orders as select i + 100 as id, 1 as cust, 99 as amount, 'x' as extra from range(2) t(i)")
    raw.execute("create table customers as select i as cust, 'c' || i as name, 'k' as kind from range(1, 4) t(i)")
    raw.execute("create table odd as select 1 as 출처")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "shop.duckdb")}).status_code == 200
    yield lambda api, **body: c.post(api, json=body).get_json()
    c.post("/api/disconnect", json={})


def rows(got, **body):
    return got("/api/query", base="orders", limit=100, **body)


def test_union_left_keeps_base_columns(searching):
    found = rows(searching, union={"tables": ["old_orders"], "how": "left"}, sorts=["id asc"])
    assert found["rows"]["columns"] == ["id", "cust", "kind", "amount", "출처"] and found["count"] == 7
    tail = found["rows"]["rows"][-1]
    assert (tail["id"], tail["kind"], tail["출처"]) == (101, None, "old_orders")  # 붙인 테이블에 없는 컬럼은 빈 값, 붙인 테이블에만 있는 extra는 뺀다


def test_union_inner_keeps_common_columns(searching):
    found = rows(searching, union={"tables": ["old_orders"], "how": "inner"})
    assert found["rows"]["columns"] == ["id", "cust", "amount", "출처"] and found["count"] == 7


def test_union_rejects_bad_requests(searching):
    error = lambda **union: searching("/api/columns", base="orders", union=union).get("error", "")
    assert "중 하나" in error(tables=["old_orders"], how="outer")
    assert "기준 테이블과 다르고" in error(tables=["orders"], how="left")
    assert "없습니다" in error(tables=["nothing"], how="left")
    assert "'출처' 컬럼이 이미 있어" in searching("/api/columns", base="odd", union={"tables": ["orders"], "how": "left"})["error"]
    assert searching("/api/columns", base="orders", union={"tables": [], "how": "left"})["columns"][0] == ["id", "정수"]  # 고른 테이블이 없으면 이어붙이지 않는다


def test_origins_follow_joins(searching):
    found = searching("/api/columns", base="orders", union={"tables": ["old_orders"], "how": "left"}, joins=["customers left cust=cust"])
    origins = found["origins"]
    assert (origins["id"], origins["출처"], origins["name"]) == ("orders", "이어붙인 테이블", "customers")
    assert origins[next(c for c, _ in found["columns"] if c.startswith("kind_"))] == "customers"  # 이름이 겹쳐 바뀐 컬럼도 그 테이블
    assert set(origins) == {c for c, _ in found["columns"]}


def test_union_works_with_conditions_and_values(searching):
    body = {"union": {"tables": ["old_orders"], "how": "left"}}
    assert rows(searching, conditions=["출처 = old_orders"], **body)["count"] == 2
    assert searching("/api/values", base="orders", column="출처", **body)["values"] == ["old_orders", "orders"]
