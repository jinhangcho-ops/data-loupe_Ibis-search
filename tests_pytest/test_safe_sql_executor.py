"""SQL 직접 쓰기: WITH·JOIN·LIKE·*는 되고, DROP·DELETE·여러 문장 등은 SecurityViolationError. 가상 테이블은 WITH로 풀어 넣어 DB를 바꾸지 않는다.
데이터는 테스트 안에서 지어낸다."""
import os

import duckdb
import polars as pl
import pytest
from lazy import ibis

from core import sql_exec
from core.sql_exec import SecurityViolationError

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
    with pytest.raises(ValueError, match="읽을 수 없어요"):
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

    from core import server
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
    assert len(stored) == 1 and next(iter(stored)).startswith("folder:" + os.path.normcase(os.path.abspath(str(tmp_path))) + "#")  # 연결 열쇠는 폴더 경로와 고른 파일뿐(비밀번호 없음)


def test_syntax_errors():
    with pytest.raises(sql_exec.SqlSyntaxError) as e:
        sql_exec.check("select a\nfrom t\nwhere (a = 1", "duckdb")
    assert (e.value.line, e.value.col) == (3, 12) and "3줄 12칸" in str(e.value) and "괄호" in str(e.value)
    with pytest.raises(sql_exec.SqlSyntaxError, match="따옴표"):
        sql_exec.check("select 'abc from t", "duckdb")
    with pytest.raises(sql_exec.SqlSyntaxError, match="테이블 이름이 와야"):
        sql_exec.check("select a from where", "duckdb")


def test_check_route(client, tmp_path):
    from core import server
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.register("df", ORDERS.to_arrow())
        raw.execute("create table orders as select * from df")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    check = lambda sql, deep=False: client.post("/api/sql/check", json={"sql": sql, "deep": deep}).get_json()
    got = check("with x as (select * from orders) select id from x")
    assert (got["ok"], got["message"]) == (True, "문법 이상 없음") and got["scan"].startswith("실행하면 약 ")
    bad = check("select id\nfrom orders where (id = 1")
    assert (bad["ok"], bad["line"], bad["col"]) == (False, 2, 25)
    assert check("select * from orderz")["message"] == "이 연결에 없는 테이블이에요: orderz"
    assert "DELETE" in check("delete from orders")["message"]
    assert check("select nope from orders")["ok"] is True  # 컬럼은 [검사](deep)에서만 본다
    deep = check("select nope from orders", deep=True)
    assert deep["ok"] is False and "nope" in deep["message"]
    assert check("select id, amount from orders", deep=True)["message"].startswith("이상 없음 · 결과 컬럼 2개: id, amount")


# ---------- 사용자 SQL의 파일·환경 읽기 막기 ----------

FILE_READS = [
    "select * from read_csv('x.csv')", "select * from READ_CSV('x.csv')", "select * from read_csv_auto('x.csv')", "select * from main.read_csv('x.csv')",
    "select * from \"read_csv\"('x.csv')", "select * from read_parquet('x.parquet')", "select * from read_json('x.json')",
    "select * from read_json_auto('x.json')", "select * from read_ndjson_objects('x.json')", "select * from read_xlsx('x.xlsx')",
    "select content from read_blob('x.bin')", "select * from read_text('x.txt')", "select * from glob('*')", "select * from main.glob('*')",
    "select * from sniff_csv('x.csv')", "select * from parquet_metadata('x.parquet')", "select * from parquet_schema('x.parquet')",
    "select * from delta_scan('d')", "select * from iceberg_scan('d')", "select * from excel_scan('x.xlsx')",
    "select * from 'x.csv'", "select * from 'x.parquet' t", "select * from orders o join 'x.csv' c on o.id = c.id", "select * from orders, 'x.csv'",
    "select * from (select * from read_csv('x.csv')) t", "with t as (select * from read_csv('x.csv')) select * from t",
    "select (select content from read_text('x.txt')) as x", "select * from orders where id in (select a from read_csv('x.csv'))",
    "select * from orders o join read_csv('x.csv') c on o.id = c.id", "select read_text('x.txt')",
    "select * from orders union all select * from read_csv('x.csv')", "(select * from read_csv('x.csv'))",
    "select * from orders o, lateral read_csv(o.id)", "from 'x.csv'",
]
ENV_READS = ["select getenv('PATH')", "select GETENV('PATH') from orders", "select current_setting('home_directory')",
             "select * from duckdb_settings()", "select main.getenv('X')", "select * from load_aws_credentials()"]


@pytest.mark.parametrize("sql", FILE_READS)
def test_file_reads_blocked(sql):
    with pytest.raises(SecurityViolationError, match="파일을 직접 읽을 수 없어요"):
        sql_exec.check(sql, "duckdb")


@pytest.mark.parametrize("sql", ENV_READS)
def test_env_reads_blocked(sql):
    with pytest.raises(SecurityViolationError, match="환경·설정"):
        sql_exec.check(sql, "duckdb")


def test_blocked_never_runs(tmp_path):
    """막힌 SQL은 DuckDB에서 실제로 실행되지 않는다(파일이 있어도 읽히지 않고, 막은 오류가 먼저 난다)."""
    data = tmp_path / "secret.csv"
    data.write_text("a\n1\n", encoding="utf-8")
    con = ibis.duckdb.connect()
    p = str(data).replace("\\", "/")
    for sql in (f"select * from read_csv('{p}')", f"select content from read_blob('{p}')", f"select * from glob('{tmp_path.as_posix()}/*')",
                f"select (select content from read_text('{p}')) as x", f"select * from '{p}'", "select getenv('PATH') as x"):
        with pytest.raises(SecurityViolationError):
            sql_exec.to_expr(con, sql, {})
    assert con.sql(f"select * from read_csv('{p}')").to_pyarrow().num_rows == 1  # 막지 않은 길로는 파일이 실제로 읽힌다(막는 쪽이 검사임을 확인)


@pytest.mark.parametrize("sql", [
    "select * from orders where id = 1", "select 'read_csv' as f, id from orders", "select * from custs where name = 'read_csv'",
    "select * from custs where name in ('glob', 'getenv', 'read_text', 'x.csv')", "select 'x.csv' as path from orders",
    "select \"id\" from \"orders\"", "select id as read_value from orders", "select * from custs where name like '%read_%'",
])
def test_plain_select_passes(con, sql):
    sql_exec.check(sql, "duckdb")


def test_string_values_run(con):
    assert rows(con, "select count(*) as n from custs where name = 'read_csv'") == [{"n": 0}]
    assert rows(con, "select 'glob' as f from custs where name = '가람'") == [{"f": "glob"}]


def test_parquet_virtual_expands_after_check(tmp_path):
    """가상 테이블(Parquet)의 read_parquet은 검사 뒤에 풀어 넣으므로 막히지 않고, 그 가상 테이블을 쓰는 SQL도 된다."""
    path = tmp_path / "extra.parquet"
    ORDERS.write_parquet(path)
    con = ibis.duckdb.connect()
    vts = {"extra": {"parquet": str(path)}}
    assert rows(con, "select count(*) as n from extra", vts) == [{"n": 4}]
    assert rows(con, "select sum(amount) as s from extra where id > 1", vts) == [{"s": 700}]
    assert sql_exec.virtual_table(con, "extra", vts).count().to_pyarrow().as_py() == 4
    with pytest.raises(SecurityViolationError):  # 사용자가 직접 쓴 같은 경로 읽기는 막힌다
        rows(con, f"select * from read_parquet('{path.as_posix()}')", vts)
    with pytest.raises(SecurityViolationError):  # SQL 가상 테이블 정의도 사용자 SQL이라 검사한다
        rows(con, "select * from bad", {"bad": {"sql": f"select * from read_parquet('{path.as_posix()}')"}})
