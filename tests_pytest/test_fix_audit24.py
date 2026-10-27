"""24차 감사: SQL 검사가 sqlite_attach(연결한 DuckDB에 뷰를 만듦)와 "x.db"·"x.ddb" 파일 테이블을 통과시키던 것,
수집 화면 [×]로 뺀 비밀 아님이 같은 이름의 다른 자리까지 검사에서 빼던 것."""
import pytest

from collect import collect_request
from core import sql_exec


@pytest.mark.parametrize("sql", ["SELECT * FROM sqlite_attach('s.db')", 'SELECT * FROM "secret.db"', 'SELECT * FROM "x.DDB"'])
def test_sql_check_blocks_attach_and_db_files(sql):
    with pytest.raises(Exception) as e:
        sql_exec.check(sql, "duckdb")
    assert not isinstance(e.value, ValueError) or "SQL을 읽을 수 없" not in str(e.value)  # 문법 오류가 아니라 막힌 것


def test_sql_check_still_allows_plain_tables():
    sql_exec.check('SELECT * FROM orders', "duckdb")
    sql_exec.check('SELECT dbname FROM "sales"', "duckdb")


def test_plain_skips_only_that_place():
    request = {"method": "GET", "url": "https://api.test/x", "headers": [], "query": [["token", "public-demo"]],
               "body_kind": "json", "body": '{"token": "sk-live-1"}'}
    places = lambda plain: collect_request.literal_places({"request": request, "plain": plain})
    assert len(places([])) == 2
    assert places(["query:token"]) == ["본문 token"]  # 쿼리 자리만 뺐다: 본문의 진짜 토큰은 여전히 비밀
    assert places(["token"]) == []  # 예전 구성(이름만)은 그 이름 전부(원래 설계 tests.py)


def test_quoted_db_only_and_schema_db_table_allowed():
    """다른 DB에서 스키마 아래 이름이 db인 테이블은 정상(DuckDB는 x.db를 파일로 열어 막는다, test_fix_review26)."""
    sql_exec.check("SELECT * FROM sales.db", "postgres")
    sql_exec.check("SELECT * FROM analytics.ddb", "postgres")


def test_file_mode_query_secret_marked_plain_passes():
    """파일 모드: [읽기]가 쿼리로 본 api_key를 ×(query:api_key)로 빼면, 주소에 합쳐진 뒤에도 비밀 검사에서 빠진다."""
    config = {"mode": "file", "file": {"url": "https://ex.com/f.csv?api_key=abc123def", "headers": []}}
    assert collect_request.literal_places({**config, "plain": []}) == ["파일 주소의 api_key"]
    assert collect_request.literal_places({**config, "plain": ["query:api_key"]}) == []


@pytest.mark.parametrize("sql", ["SELECT * FROM enable_logging(storage='file', storage_path='x.csv')", "SELECT * FROM checkpoint()",
                                 "SELECT write_log('x')", "SELECT * FROM truncate_duckdb_logs()"])
def test_sql_check_blocks_duckdb_logging(sql):
    """DuckDB 로그 함수는 읽기 전용 연결에서도 아무 CSV에 글을 덧붙이고, 연결을 망가뜨린다(25차 감사)."""
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_exec.check(sql, "duckdb")
