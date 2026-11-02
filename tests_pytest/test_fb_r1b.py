"""조건에서 컬럼끼리 비교(F12)와 날짜 컬럼끼리 비교(F15)."""
import pytest


@pytest.fixture
def t():
    import duckdb
    import ibis
    raw = duckdb.connect()
    raw.execute("""create table t as select * from (values
        (1, 2, 1.5, 'x', 'a', timestamp '2026-01-01 10:00', date '2026-01-02'),
        (3, 3, 3.0, 'b', 'b', timestamp '2026-01-03 00:00', date '2026-01-02'),
        (5, 4, 9.0, 'c', 'x', timestamp '2026-01-05 12:00', date '2026-01-04')
    ) v(a, b, f, s, x, ts_a, d_b)""")
    return ibis.duckdb.from_connection(raw).table("t")


def count(t, line, quotes=True):
    from core import parse
    return t.filter(parse.parse_condition(line, t, quotes)).count().execute()


@pytest.mark.parametrize("line, rows", [("a = b", 1), ("a != b", 2), ("a > b", 1), ("a >= b", 2), ("a < b", 1), ("a <= b", 2), ("a<b", 1), ("a < f", 2), ("s = x", 1)])
def test_column_compares_with_column(t, line, rows):
    """우변이 따옴표 없는 컬럼 이름이면 컬럼끼리 비교한다(정수와 실수끼리도)."""
    assert count(t, line) == rows


def test_quoted_name_is_value(t):
    """따옴표로 감싸거나 따옴표 해석을 끈 줄에서는 컬럼 이름과 같은 글자도 값이다."""
    assert count(t, "s = 'x'") == 1 and count(t, "x = 'x'") == 1
    assert count(t, "x = x", quotes=False) == 1 and count(t, "x = x") == 3  # 끄면 값 'x', 켜면 자기 자신과 비교라 모든 행


def test_column_type_mismatch_is_value_error(t):
    from core import parse
    with pytest.raises(ValueError, match="비교할 수 없"):
        parse.parse_condition("a = s", t)


@pytest.mark.parametrize("line, rows", [("ts_a < d_b", 1), ("ts_a > d_b", 2), ("d_b <= ts_a", 2)])
def test_date_columns_compare(t, line, rows):
    """날짜시간 컬럼과 날짜 컬럼도 조건으로 비교한다(F15: 이를 때·늦을 때). 기간 칸은 '컬럼 시작 ~ 끝' 값 범위만 받는다."""
    assert count(t, line) == rows
