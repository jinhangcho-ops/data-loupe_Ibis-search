"""SQL 보기·실행 계획의 SQL 글(result.get_sql): 고른 것이 하나뿐인 SELECT는 한 줄, 여럿이면 한 줄에 하나. 실행되는 SQL은 같다."""
import ibis
import sqlglot

from core import result


def table() -> ibis.Table:
    con = ibis.duckdb.connect()
    con.raw_sql("CREATE TABLE t AS SELECT 1 AS a, 'x' AS b, 3 AS c UNION ALL SELECT 2, 'y', 4")
    return con.table("t")


def same_query(text: str, expr: ibis.Table) -> bool:
    """보기용으로 고친 글이 Ibis가 만든 글과 같은 조회인지(문법 나무가 같다)."""
    return sqlglot.parse_one(text, read="duckdb") == sqlglot.parse_one(str(ibis.to_sql(expr)), read="duckdb")


def test_star_is_on_the_select_line() -> None:
    t = table()
    text = result.get_sql(t)
    assert text.splitlines()[0] == "SELECT *"
    assert same_query(text, t)


def test_one_column_is_on_the_select_line() -> None:
    t = table()
    text = result.get_sql(t.select("a"), 5)
    assert text.splitlines()[0] == 'SELECT "t0"."a"'
    assert same_query(text, t.select("a").limit(5))
    assert result.get_sql(t.select("a").distinct()).splitlines()[0] == 'SELECT DISTINCT "t0"."a"'


def test_many_columns_stay_one_per_line() -> None:
    t = table()
    text = result.get_sql(t.select("a", "b"))
    assert text == str(ibis.to_sql(t.select("a", "b")))
    assert text.splitlines()[:3] == ["SELECT", '  "t0"."a",', '  "t0"."b"']


def test_subquery_selects_follow_the_same_rule() -> None:
    t = table()
    expr = t.filter(t.a.isin(t.filter(t.c > 3).a)).select("b")
    text = result.get_sql(expr)
    assert str(ibis.to_sql(expr)).count("SELECT\n") == 3  # 고치기 전 글: 바깥·하위 질의·IN 안의 질의 모두 고른 것이 따로 떨어져 있다
    assert [line.strip() for line in text.splitlines() if "SELECT" in line] == ['SELECT "t2"."b"', "SELECT *", 'SELECT "t0"."a"']
    assert "      SELECT \"t0\".\"a\"\n      FROM" in text  # 들여쓰기는 그대로
    assert same_query(text, expr)
    assert t.sql(text).to_polars().rows() == expr.to_polars().rows() == [("y",)]  # 고친 글을 실행해도 같은 결과


def test_one_pick_that_spans_lines_is_left_alone() -> None:
    t = table()
    expr = t.select(k=t.view().a.max().as_scalar())  # 고른 것 하나가 여러 줄(괄호 안 질의)
    text = result.get_sql(expr)
    assert text.splitlines()[:3] == ["SELECT", "  (", '    SELECT MAX("t1"."a") AS "Max(a)"']  # 바깥은 그대로, 안쪽 하나짜리만 한 줄
    assert same_query(text, expr)


def test_text_with_newline_and_select_in_a_value_is_untouched() -> None:
    t = table()
    value = "x\nSELECT\n  *\nFROM y"
    expr = t.filter(t.b == value)
    text = result.get_sql(expr)
    assert text == str(ibis.to_sql(expr))  # 값 안에 줄바꿈이 있으면 글을 건드리지 않는다
    assert value.replace("'", "''") in text
    kept = t.filter(t.b == "SELECT *, FROM")  # 줄바꿈 없는 값 속 SELECT는 값 그대로, 바깥 SELECT만 한 줄
    text = result.get_sql(kept)
    assert text.splitlines()[0] == "SELECT *" and "'SELECT *, FROM'" in text
    assert same_query(text, kept)
