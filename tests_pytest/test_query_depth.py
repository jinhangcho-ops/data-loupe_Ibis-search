"""만든 SQL의 SELECT 중첩 깊이: 창 함수 계산 줄은 mutate 하나로 묶이고, 도우미 컬럼은 drop 대신 select로 빼 SELECT가 늘지 않는다.
DB에 연결하지 않고 스키마만 있는 표(ibis.table)를 방언별 SQL로 바꿔 센다. 중복 기준은 DISTINCT ON을 못 하는 방언에서 행 번호로 대신한다."""
import pytest
import sqlglot
from sqlglot import exp

from lazy import ibis

DIALECTS = ["duckdb", "postgres", "mssql", "oracle", "bigquery"]


def depth(expr, dialect):
    tree = sqlglot.parse_one(ibis.to_sql(expr, dialect=dialect), read=dialect)
    best = 0
    for node in tree.find_all(exp.Select):
        n, up = 1, node.parent
        while up is not None:
            n += isinstance(up, exp.Select)
            up = up.parent
        best = max(best, n)
    return best


@pytest.fixture
def orders():
    return ibis.table({"id": "int64", "store": "int64", "day": "date", "amt": "float64", "memo": "string"}, name="orders")


@pytest.mark.parametrize("dialect", DIALECTS)
def test_window_lines_share_one_select(orders, dialect):
    from core import calc
    many = calc.apply(["순위 기준 amt desc 그룹 store", "누적합 amt 기준 id", "이전값 amt 기준 id", "이동평균 amt 3행 기준 id"], orders)
    one = calc.apply(["누적합 amt 기준 id"], orders)
    assert depth(many, dialect) == depth(one, dialect) <= 2


@pytest.mark.parametrize("dialect", DIALECTS)
def test_top_rows_add_one_level(orders, dialect):
    from core import calc
    from core import parse
    top = calc.apply(["상위 2 기준 amt desc 그룹 store"], orders)
    assert depth(top, dialect) <= 2 and depth(parse.apply(top, [], [parse.parse_sort("amt desc", top)], []), dialect) <= 2
    assert "__ibis_top" not in top.columns


def test_batched_lines_still_see_earlier_columns(orders):
    from core import calc
    expr = calc.apply(["누적합 amt 기준 id", "차이 amt_누적합 기준 id", "순위 기준 amt_누적합 desc"], orders)
    assert expr.columns[-3:] == ("amt_누적합", "amt_누적합_차이", "amt_누적합_순위")
    with pytest.raises(ValueError, match="이미 있습니다"):
        calc.apply(["누적합 amt 기준 id", "누적합 amt 기준 id"], orders)  # 같은 묶음 안에서 겹치는 이름도 막는다


def test_first_rows_fall_back_to_row_numbers(orders, monkeypatch):
    from prep import prep_expr
    from core import source
    monkeypatch.setattr(source, "compiles", lambda expr: False)  # DISTINCT ON(keep first)을 못 하는 연결처럼
    kept = prep_expr.first_rows(orders, ["store"])
    assert kept.columns == orders.columns
    for dialect in ("mssql", "oracle"):
        sql = "".join(ibis.to_sql(kept, dialect=dialect).split()).upper()  # 줄바꿈·공백은 방언마다 달라 빼고 본다
        assert "ROW_NUMBER()OVER(PARTITIONBY" in sql and depth(kept, dialect) <= 2


def test_first_rows_on_duckdb_keep_one_per_key():
    import duckdb
    from prep import prep_expr
    raw = duckdb.connect()
    raw.execute("create table t as select i as id, i % 3 as store from range(10) r(i)")
    con = ibis.duckdb.from_connection(raw)
    found = prep_expr.first_rows(con.table("t"), ["store"]).to_pyarrow()
    assert sorted(found["store"].to_pylist()) == [0, 1, 2] and found.num_rows == 3
