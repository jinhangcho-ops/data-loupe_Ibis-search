"""26차 리뷰: 따옴표 없는 x.db(DuckDB 파일), sleep_ms, query:이름이 다른 주소까지 빼던 것, 같은 첫 시각의 유료·오가닉이 행 순서로 갈리던 것."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from collect import collect_request
from core import sql_exec
from metrics import metric_catalog as cat

D = dt.datetime


@pytest.mark.parametrize("sql", ["SELECT * FROM x.db", "SELECT * FROM y.DDB", "SELECT sleep_ms(600000)"])
def test_duckdb_blocks_unquoted_db_file_and_sleep_ms(sql):
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_exec.check(sql, "duckdb")


def test_other_dialect_schema_db_table_allowed():
    sql_exec.check("SELECT * FROM sales.db", "postgres")


def test_query_plain_does_not_hide_request_or_web_url_secret():
    request = {"method": "GET", "url": "https://a.test/x?token=abc", "query": [["token", "zzz"]], "headers": []}
    assert collect_request.literal_places({"request": request, "plain": ["query:token"]}) == ["주소의 token"]
    assert collect_request.literal_places({"web": {"url": "https://w.test/p?token=REAL"}, "plain": ["query:token"]}) == ["웹페이지 주소의 token"]


@pytest.mark.parametrize("order", [["organic", "paid"], ["paid", "organic"]])
def test_paid_first_same_time_does_not_depend_on_row_order(order):
    t = ibis.memtable(pl.DataFrame({"u": ["b", "b"], "t": [D(2025, 1, 1)] * 2, "src": order}).to_arrow())
    names = {s["role"]: s["name"] for s in cat.get("organic_uplift")["slots"]}
    roles = {"user": "u", "time": "t", "source": "src", "paid_value": "paid"}
    r = cat.compute("organic_uplift", {names[k]: v for k, v in roles.items()}, {}, cat.Ctx(table=t))
    assert r["parts"]["paid_installs"] == 1


def test_save_sql_checks_with_connection_dialect(client):
    """[SQL 저장]도 연결의 방언으로 검사한다: Postgres의 sales.db 테이블은 저장되고, 연결이 없으면 DuckDB처럼 막는다(27차 리뷰1)."""
    from core import server
    post = lambda: client.post("/api/sqls", json={"name": "s", "sql": "SELECT * FROM sales.db"}).get_json()
    assert "파일" in post()["error"]
    server.state["con"] = type("Pg", (), {"dialect": "postgres"})()
    assert post()["order"] == ["s"]
