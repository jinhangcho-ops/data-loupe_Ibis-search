"""SQL 직접 쓰기: WITH·JOIN·LIKE·*는 되고, DROP·DELETE·여러 문장 등은 SecurityViolationError. 가상 테이블은 WITH로 풀어 넣어 DB를 바꾸지 않는다.
데이터는 테스트 안에서 지어낸다."""
import os

import duckdb
import polars as pl
import pytest
from lazy import ibis

import sql_exec
from sql_exec import SecurityViolationError

ORDERS = pl.DataFrame({"id": [1, 2, 3, 4], "cust": [10, 20, 10, 30], "amount": [100, 250, 50, 400]})
CUSTS = pl.DataFrame({"cust": [10, 20, 30], "name": ["가람", "나래", "다솜"]})


@pytest.fixture(params=["duckdb", "polars"])
def con(request, tmp_path):
    if request.param == "polars":
        c = ibis.polars.connect()
        c.create_table("orders", ORDERS)
        c.create_table("custs", CUSTS)
        return c
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        for name, df in (("orders", ORDERS), ("custs", CUSTS)):
            raw.register("df", df.to_arrow())
            raw.execute(f"create table {name} as select * from df")
            raw.unregister("df")
    return ibis.duckdb.connect(str(path), read_only=True)


def rows(con, sql, vts=None):
    return sql_exec.to_expr(con, sql, vts or {}).to_pyarrow().to_pylist()


def test_allowed(con):
    assert len(rows(con, "select * from orders")) == 4
    assert rows(con, "select name from custs where name like '가%'") == [{"name": "가람"}]
    got = rows(con, """with big as (select * from orders where amount >= 100)
                       select c.name, sum(b.amount) as total from big b join custs c on b.cust = c.cust group by c.name order by total desc""")
    assert got == [{"name": "다솜", "total": 400}, {"name": "나래", "total": 250}, {"name": "가람", "total": 100}]
    assert rows(con, "(select id from orders where id = 1) union all (select id from orders where id = 2)") in ([{"id": 1}, {"id": 2}], [{"id": 2}, {"id": 1}])


@pytest.mark.parametrize("sql", [
    "drop table orders", "delete from orders", "insert into orders values (5, 10, 1)", "update orders set amount = 0",
    "create table x as select * from orders", "alter table orders add column z int", "truncate table orders",
    "select 1; drop table orders", "select * from orders; select * from custs", "select * into x from orders",
    "with d as (delete from orders returning *) select * from d", "copy orders to 'x.csv'", "attach 'other.db'",
    "pragma version", "set threads = 1", "grant select on orders to someone",
])
def test_blocked(sql):
    with pytest.raises(SecurityViolationError):
        sql_exec.check(sql, "duckdb")


def test_check_input():
    for bad in ("", "   ", None):
        with pytest.raises(ValueError, match="입력"):
            sql_exec.check(bad)
    with pytest.raises(ValueError, match="읽을 수 없습니다"):
        sql_exec.check("select from where")
    with pytest.raises(ValueError, match="자까지"):
        sql_exec.check("select " + "1, " * sql_exec.MAX_SQL_SIZE + "1")


def test_virtual_tables(con, tmp_path):
    vts = {"vip": {"sql": "select * from custs where cust in (select cust from orders where amount >= 250)"},
           "vip_orders": {"sql": "select o.* from orders o join vip v on o.cust = v.cust"}}  # 가상 테이블이 가상 테이블을 부른다
    assert sorted(r["id"] for r in rows(con, "select id from vip_orders", vts)) == [2, 4]
    assert rows(con, "with x as (select count(*) as n from vip) select n from x", vts) == [{"n": 2}]
    assert sql_exec.virtual_table(con, "vip", vts).count().to_pyarrow().as_py() == 2
    path = tmp_path / "extra.parquet"
    ORDERS.write_parquet(path)
    assert rows(con, "select count(*) as n from extra", {"extra": {"parquet": str(path)}}) == [{"n": 4}]
    with pytest.raises(ValueError, match="고리"):
        rows(con, "select * from a", {"a": {"sql": "select * from b"}, "b": {"sql": "select * from a"}})
    with pytest.raises(SecurityViolationError):
        rows(con, "select * from bad", {"bad": {"sql": "drop table orders"}})
    assert "vip" not in con.list_tables()  # DB에는 아무것도 만들지 않는다


def test_parquet_only_on_file_backends(tmp_path):
    class Other:  # DuckDB·Polars가 아닌 연결
        name = "postgres"
        dialect = "postgres"
    with pytest.raises(ValueError, match="DuckDB"):
        sql_exec.expand(Other(), sql_exec.check("select * from p"), {"p": {"parquet": "x.parquet"}})


def test_store(folder):
    key = "folder:c:\\data"
    ok = lambda trial: None
    assert "vip" in sql_exec.save_virtual(key, " vip ", {"sql": "select 1 as a"}, ["orders"], ok)
    assert sql_exec.virtual("folder:other") == {}  # 연결마다 따로
    with pytest.raises(ValueError, match="테이블 이름"):
        sql_exec.save_virtual(key, "ORDERS", {"sql": "select 1"}, ["orders"], ok)
    with pytest.raises(ValueError, match="이름은"):
        sql_exec.save_virtual(key, "1bad name", {"sql": "select 1"}, [], ok)
    def fail(trial):
        raise ValueError("읽을 수 없음")
    with pytest.raises(ValueError, match="읽을 수 없음"):
        sql_exec.save_virtual(key, "broken", {"sql": "select * from nowhere"}, [], fail)
    assert "broken" not in sql_exec.virtual(key)  # 실패하면 저장하지 않는다
    assert sql_exec.save_virtual(key, "vip", None, [], ok) == {}
    with pytest.raises(ValueError, match="행 수"):
        sql_exec.rows_limit(0)
    assert sql_exec.rows_limit(None) == sql_exec.DEFAULT_ROWS


def test_routes(client, tmp_path, folder):
    """SQL 실행·저장, 가상 테이블 저장과 탐색(기준 테이블·조인·행 수) 연동. 연결은 지어낸 DuckDB 파일."""
    import json

    import server
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        for name, df in (("orders", ORDERS), ("custs", CUSTS)):
            raw.register("df", df.to_arrow())
            raw.execute(f"create table {name} as select * from df")
            raw.unregister("df")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    post = lambda url, **body: client.post(url, json=body)

    d = post("/api/sql/run", sql="select cust, sum(amount) as total from orders group by cust order by cust", rows=2).get_json()
    assert d["rows"]["rows"] == [{"cust": 10, "total": 150.0}, {"cust": 20, "total": 250.0}] and d["more"] is True
    r = post("/api/sql/run", sql="drop table orders")
    assert r.status_code == 400 and "SELECT" in r.get_json()["error"]

    vt = post("/api/virtual", name="vip", sql="select * from custs where cust >= 20").get_json()
    assert vt["order"] == ["vip"]
    assert post("/api/virtual", name="orders", sql="select 1").get_json()["error"].startswith("'orders'")
    assert "vip" in client.get("/api/tables?virtual=1").get_json()["virtual"]
    assert "virtual" not in client.get("/api/tables").get_json()  # 분석 화면은 가상 테이블을 받지 않는다
    q = post("/api/query", base="vip", joins=["orders left cust=cust"], conditions=[], periods=[], sorts=[], columns=[], limit=10).get_json()
    assert q["count"] == 2 and len(q["rows"]["rows"]) == 2  # vip(고객 20·30)마다 주문 하나
    assert post("/api/rowcount", base="vip").get_json()["rows"] == 2
    assert post("/api/sql/run", sql="select count(*) as n from vip join orders using (cust)").get_json()["rows"]["rows"] == [{"n": 2}]

    saved = post("/api/sqls", name="매출", sql="select * from vip").get_json()
    assert saved["order"] == ["매출"] and post("/api/sqls/use", name="매출").status_code == 200
    assert "SELECT" in post("/api/sqls", name="나쁨", sql="delete from orders").get_json()["error"]
    assert post("/api/sqls", name="매출", sql=None).get_json()["sqls"] == {}
    assert post("/api/virtual", name="vip", sql=None, parquet=None).get_json()["virtual"] == {}

    stored = json.loads((folder / "virtual_tables.json").read_text(encoding="utf-8"))
    assert list(stored) == ["folder:" + os.path.normcase(os.path.abspath(str(tmp_path)))]  # 연결 열쇠는 폴더 경로뿐(비밀번호 없음)
