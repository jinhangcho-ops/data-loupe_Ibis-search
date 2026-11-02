"""F01: 테이블을 바꿔 가며 조회해도 앱 메모리가 쌓이지 않게. 받아 둔 결과(held)의 DuckDB도 쿼리가 끝나면(source.release) 캐시를 비운다."""


def test_쿼리가_끝나면_받아둔_결과의_DuckDB_캐시도_비운다(folder):
    from core import held, source

    def work():
        held.clear()
        con = held.local().con
        limit = con.execute("select current_setting('memory_limit')").fetchone()[0]
        con.execute("create table big as select range x, md5(range::varchar) || md5((range * 7)::varchar) s from range(5000000)")  # 압축이 덜 되는 값(약 160MB, FLOOR 64MB보다 커야 비는 것이 보인다)
        used = lambda: con.execute("select sum(memory_usage_bytes) from duckdb_memory()").fetchone()[0]  # noqa: E731
        before = used()
        source.release(object())  # 지금 연결(원격 DB 등)은 비울 것이 없어도 held는 비운다
        after, back = used(), con.execute("select current_setting('memory_limit')").fetchone()[0]
        rows = con.execute("select count(*) from big").fetchone()[0]  # 받아 둔 결과는 그대로(파일에서 다시 읽는다)
        held.clear()
        return before, after, back == limit, rows

    before, after, restored, rows = source.within(None, work, "")
    assert after < before / 2, (before, after)
    assert restored and rows == 5_000_000
