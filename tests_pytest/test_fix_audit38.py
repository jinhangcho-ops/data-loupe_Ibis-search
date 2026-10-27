"""감사 38차: Parquet 가상 테이블에 SQL 식·처리 용량 확인, 가상 테이블 이름의 대소문자(따옴표)."""
import duckdb
import pytest

from lazy import ibis
from core import calc, scan_size, sql_exec, sql_funcs


@pytest.fixture
def pv(tmp_path):
    """Parquet 가상 테이블 pv와 SQL 가상 테이블 sv가 있는 DuckDB 파일 연결."""
    pq = (tmp_path / "p.parquet").as_posix()
    duckdb.sql(f"COPY (SELECT range AS id, range * 10 AS amount FROM range(5)) TO '{pq}' (FORMAT parquet)")
    db = str(tmp_path / "vt.duckdb")
    w = duckdb.connect(db)
    w.sql("CREATE TABLE orders AS SELECT 1 AS id")
    w.close()
    con = ibis.duckdb.connect(db, read_only=True)
    yield con, {"pv": {"parquet": pq}, "sv": {"sql": "SELECT id, id*2 AS d FROM orders"}}
    con.disconnect()


def test_sql_expr_on_parquet_virtual_table(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    out = calc.apply(["SQL식 amount + 1 이름 x"], expr).execute()
    assert out["x"].tolist() == [1, 11, 21, 31, 41]


def test_raw_sql_still_checks_user_expression(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    with pytest.raises(sql_exec.SecurityViolationError):  # 사용자 식의 파일 읽기는 여전히 막는다
        sql_funcs.raw_sql(expr, "x", "(SELECT count(*) FROM read_csv('a.csv'))")
    with pytest.raises(sql_exec.SecurityViolationError):  # 주석으로 FROM을 바꾸는 것도 막는다(A7)
        sql_funcs.raw_sql(expr, "x", "1) AS x FROM orders --")


def test_limit_check_counts_parquet_virtual_table(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    sql, dialect = scan_size.sql_of(con, expr)
    r = scan_size.limit_check(con, sql, None, dialect, {"scan_unknown": "block"})
    assert not r["unknown"] and not r["blocked"] and r["bytes"] > 0 and "Parquet" in r["how"]


class Con:
    def __init__(self, name, dialect):
        self.name, self.dialect = name, dialect


@pytest.mark.parametrize("dialect", ["postgres", "snowflake", "duckdb"])
def test_virtual_table_reference_keeps_exact_case(dialect):
    vts = {"Sales": {"sql": "SELECT 1 AS a"}, "orders_vt": {"sql": "SELECT a FROM sales"}}
    con = Con(dialect, dialect)
    tree = sql_exec.expand(con, sql_exec.check("SELECT Sales.a FROM Sales JOIN orders_vt o ON TRUE", dialect), vts)
    exp = sql_exec.glot()[1]
    refs = {t.this.this: t.this.quoted for t in tree.find_all(exp.Table)}
    assert refs == {"Sales": True, "orders_vt": True}  # 정의 안의 sales도 저장한 이름 "Sales"로
    assert {c.alias_or_name for c in tree.find_all(exp.CTE)} == {"Sales", "orders_vt"}


def test_virtual_table_case_runs_on_duckdb(pv):
    con, _ = pv
    vts = {"Sales": {"sql": "SELECT 1 AS a"}}
    assert con.raw_sql(sql_exec.expand(con, sql_exec.check("SELECT Sales.a FROM sales", "duckdb"), vts).sql(dialect="duckdb")).fetchall() == [(1,)]
