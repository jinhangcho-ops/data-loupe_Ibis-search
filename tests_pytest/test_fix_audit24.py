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
