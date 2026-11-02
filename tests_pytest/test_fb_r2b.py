"""조건 '* is not null'(모든 컬럼에 값이 있는 행)과 '* is null'(어느 한 컬럼이라도 빈 행) — F11."""
import pytest


@pytest.fixture
def t():
    import duckdb
    import ibis
    raw = duckdb.connect()
    raw.execute("""create table t as select * from (values
        (1, 'a', {'k': 1}),
        (null, 'b', {'k': 2}),
        (3, null, {'k': 3}),
        (4, 'd', null),
        (5, 'e', {'k': null})
    ) v(n, s, st)""")
    return ibis.duckdb.from_connection(raw).table("t")


def count(t, cond):
    from core import parse
    return t.filter(parse.predicate(cond, t)).count().execute()


def test_모든_컬럼에_값이_있는_행만(t):
    """구조 컬럼은 컬럼 자체의 null만 본다(안 필드가 null인 마지막 행은 남는다)."""
    assert count(t, "* is not null") == 2
    assert count(t, "* IS NOT NULL") == 2


def test_어느_한_컬럼이라도_빈_행(t):
    assert count(t, "* is null") == 3


def test_다른_조건과_and_or(t):
    assert count(t, ["* is not null", "n > 1"]) == 1
    assert count(t, "* is null or n = 1") == 4


def test_컬럼끼리_비교는_그대로(t):
    """어제 넣은 '우변이 컬럼 이름이면 컬럼끼리 비교'와 부딪치지 않는다."""
    assert count(t, "n is not null") == 4
    assert count(t, "n = n") == 4
