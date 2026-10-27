"""10차 감사: SQLite·SQL Server처럼 isnan·isinf를 못 하는 연결의 분석·전처리, 무한도 빈 값 그룹으로."""
import math
import sqlite3

import ibis

from analysis import stats
from prep import prep_expr


def sqlite_table(tmp_path, ddl, rows):
    path = tmp_path / "s.db"
    con = sqlite3.connect(path)
    con.execute(ddl)
    con.executemany(f"insert into s values ({', '.join('?' * len(rows[0]))})", rows)
    con.commit()
    con.close()
    con = ibis.sqlite.connect(str(path))
    return con, con.table("s")


def test_group_compare_with_real_value_column_on_sqlite(tmp_path):
    _, t = sqlite_table(tmp_path, "create table s(g REAL, c TEXT, x REAL)", [(1.5, "a", 1.0), (None, "b", 2.5), (1.5, "a", None)])
    assert stats.groups(t, "c", "x", "sum")["values"] == [2.5, 1.0]
    assert stats.groups(t, "g", "g", "mean")["labels"] == ["1.5", "(빈 값)"]


def test_prep_fill_and_outlier_on_sqlite_real_column(tmp_path):
    con, t = sqlite_table(tmp_path, "create table s(x REAL)", [(1.0,), (None,), (3.0,)])
    recipe = {"base": "s", "columns": {"x": {"ops": {"fill": {"how": "mean"}, "outlier": {"how": "clip", "by": "range", "low": 0, "high": 2.5}}}}}
    out, _ = prep_expr.shape(con, recipe, table=t)
    assert sorted(out.to_polars()["x"].to_list()) == [1.0, 2.0, 2.5]


def test_infinity_joins_the_empty_group():
    con = ibis.duckdb.connect()
    t = con.create_table("t", ibis.memtable({"g": [1.5, None, float("nan"), 1.5, math.inf, float("nan")], "x": [1, 2, 3, 4, 5, 6]}))
    got = stats.groups(t, "g", "x", "sum")
    assert got["labels"] == ["(빈 값)", "1.5"] and got["counts"] == [4, 2]


# ---------- core·쿼리 ----------
import polars as pl

from core import parse
from core import result
from core import server
from core import source


def polars_table(tmp_path, name, frame):
    source.fix_polars_text()
    frame.write_parquet(tmp_path / f"{name}.parquet")
    con = ibis.polars.connect()
    con.read_parquet(tmp_path / f"{name}.parquet", table_name=name)
    return con, con.table(name)


def test_asof_on_file_connection_with_unsorted_times(tmp_path):
    con, left = polars_table(tmp_path, "l", pl.DataFrame({"k": [1, 1, 1], "t": [9, 5, 2]}))
    pl.DataFrame({"k": [1, 1, 1], "t": [8, 1, 4], "p": ["p8", "p1", "p4"]}).write_parquet(tmp_path / "r.parquet")
    con.read_parquet(tmp_path / "r.parquet", table_name="r")
    got = parse.apply_joins(left, [("r", "asof", [("k", "k")], ("t", "t"))], con.table).to_polars()
    assert sorted(zip(got["t"], got["p"])) == [(2, "p1"), (5, "p4"), (9, "p8")]


def test_pages_of_a_distinct_then_sorted_file_table_do_not_overlap(tmp_path):
    n = 600_000
    _, t = polars_table(tmp_path, "t", pl.DataFrame({"g": pl.int_range(n, eager=True) % 3, "v": pl.int_range(n, eager=True)}))
    e = t.distinct().order_by("g")
    seen = pl.concat([result.get_rows(e, 100_000, o) for o in range(0, n, 100_000)])
    assert seen["v"].n_unique() == n

def test_inner_join_merged_key_keeps_the_usual_right_names():
    con = ibis.duckdb.connect()
    o = con.create_table("o", ibis.memtable({"k": [1], "k_right": [2], "v": [3]}))
    con.create_table("r", ibis.memtable({"k": [1], "v": [4]}))
    assert parse.apply_joins(o, [("r", "inner", [("k", "k")], None)], con.table).columns == ("k", "k_right", "v", "v_right")


def test_nested_binary_becomes_hex_inside_and_stays_nested():
    df = pl.DataFrame({"l": [[b"a", b"b"]], "s": [{"x": b"\x01", "n": 3}]})
    out = server.text_wide(df, True)
    assert out.dtypes == [pl.List(pl.String), pl.Struct({"x": pl.String, "n": pl.Int64})]
    assert server.dump(out) == '[{"l":["61","62"],"s":{"x":"01","n":3}}]'


def test_paging_note_only_when_sort_values_tie(client, tmp_path):
    """둘째 쪽부터, 정렬 값이 같은 행이 있을 때만 안내. 정렬 끝이 고유하면(tests.py의 amount desc, order_id) 안내 없음."""
    from query.search_app import LOCAL_PAGES, TIES
    con = ibis.duckdb.connect()
    con.create_table("t", ibis.memtable({"i": list(range(30)), "k": [x % 3 for x in range(30)]}))
    server.state.update(con=con, opened={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "t.duckdb")})
    TIES.clear()
    try:
        ask = lambda sorts, offset: client.post("/api/query", json={"base": "t", "sorts": sorts, "limit": 10, "offset": offset}).get_json()["note"]
        got = [ask(["k"], 0), ask(["k"], 10), ask(["k desc", "i"], 10), ask(["i"], 10)]
    finally:
        server.state.update(con=None)
    assert got == ["", LOCAL_PAGES, "", ""]
