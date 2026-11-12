"""감사 44: SQL 검사(sql_exec.check)가 놓치던 모양 — SUMMARIZE '파일', 시퀀스 다음 값, 잠금 풀기, 서버 파일 정보, 글자 SQL 원격 실행."""
import pytest

from core import sql_exec

BLOCKED = [
    ("select * from (summarize 'a.csv')", "duckdb"),  # 파일 내용(컬럼별 최솟값·최댓값)을 읽는다
    ("summarize 'a.csv'", "duckdb"),
    ("select next value for s", "tsql"),  # nextval과 같다(시퀀스를 올린다)
    ("select s.nextval", "snowflake"),
    ("select release_lock('a')", "mysql"),  # get_lock의 짝
    ("select pg_stat_file('x')", "postgres"),  # pg_read_file의 짝(서버 파일 정보)
    ("select * from external_query('a', 'select 1')", "bigquery"),  # 글자로 준 SQL을 다른 DB에서 돌린다
]


@pytest.mark.parametrize("sql,dialect", BLOCKED)
def test_blocked(sql, dialect):
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_exec.check(sql, dialect)


@pytest.mark.parametrize("sql,dialect", [
    ("select * from (summarize t)", "duckdb"),  # 연결한 테이블의 요약은 된다
    ("select nextval from t", "snowflake"),  # 테이블 이름 없는 nextval 컬럼은 된다
])
def test_still_allowed(sql, dialect):
    sql_exec.check(sql, dialect)
