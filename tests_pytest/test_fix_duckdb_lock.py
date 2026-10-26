"""보안 12E: 뷰·매크로가 든 DuckDB 파일은 파일·네트워크 읽기를 막고 연다(뷰 본문은 sql_exec.check를 거치지 않는다). 결과 저장은 그대로 된다.
파일은 모두 tmp_path 안에서 만든다."""
import duckdb
import pytest

from core import result, source, sql_exec


def make(tmp_path, name, *sql):
    path = tmp_path / name
    with duckdb.connect(str(path)) as con:
        con.execute("create table a as select 1 as x")
        for s in sql:
            con.execute(s)
    return str(path)


def test_view_over_outside_file_is_blocked_but_export_works(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("dummy-outside")
    con = source.open_duckdb(make(tmp_path, "v.duckdb", f"create view v as select * from read_text('{outside.as_posix()}')",
                                  f"create macro m() as table select * from read_text('{outside.as_posix()}')"))
    try:
        assert con.locked
        for sql in ("SELECT * FROM v", "SELECT * FROM m()"):
            with pytest.raises(Exception, match="Permission"):
                sql_exec.to_expr(con, sql, {}).execute()
        for suffix in (".csv", ".parquet"):
            result.export(con.table("a"), str(tmp_path / f"out{suffix}"))
        assert (tmp_path / "out.csv").read_text(encoding="utf-8-sig").replace("\"", "").split() == ["x", "1"]  # pyarrow CSV는 머리글을 따옴표로 감싼다
        with pytest.raises(ValueError, match="뷰·매크로"):
            sql_exec.definition_tree(con, "p", {"parquet": str(tmp_path / "x.parquet")})
    finally:
        source.close(con)


def test_plain_file_is_not_locked(tmp_path):
    con = source.open_duckdb(make(tmp_path, "plain.duckdb"))
    try:
        assert not con.locked
        sql_exec.definition_tree(con, "p", {"parquet": str(tmp_path / "x.parquet")})
    finally:
        source.close(con)
