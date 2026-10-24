"""9차 리뷰(f914e7c): SQLite 그룹 비교, 정렬 키 이름, 묶은 뒤 정렬한 쪽 넘김, 두 번째 조인의 asof, 오른쪽 표 안의 이름 겹침, 파일 연결 SQL 쪽 안내."""
import sqlite3

import ibis
import polars as pl

from analysis import stats
from core import parse
from core import source
from query import flow


def test_group_compare_on_sqlite_float_column(tmp_path):
    path = tmp_path / "s.db"
    con = sqlite3.connect(path)
    con.execute("create table s(g REAL, x INTEGER)")
    con.executemany("insert into s values (?, ?)", [(1.5, 1), (None, 2)])
    con.commit()
    con.close()
    assert stats.groups(ibis.sqlite.connect(str(path)).table("s"), "g", "x", "count")["labels"] == ["1.5", "(빈 값)"]


def polars_table(tmp_path, frame):
    source.fix_polars_text()
    frame.write_parquet(tmp_path / "t.parquet")
    con = ibis.polars.connect()
    con.read_parquet(tmp_path / "t.parquet", table_name="t")
    return con.table("t")


def test_sort_keeps_a_user_column_named_like_the_sort_key(tmp_path):
    t = polars_table(tmp_path, pl.DataFrame({"i": [1, 2, 3], "_sort_key_0": ["a", "b", "c"], "s": ["x", "y", "z"]}))
    got = t.order_by(ibis.desc("i")).to_polars()
    assert got.columns == ["i", "_sort_key_0", "s"] and got["_sort_key_0"].to_list() == ["c", "b", "a"]


def test_pages_of_a_grouped_then_sorted_file_table_do_not_overlap(tmp_path):
    n = 600_000
    t = polars_table(tmp_path, pl.DataFrame({"g": pl.int_range(n, eager=True) % 50_000, "v": pl.int_range(n, eager=True)}))
    e = t.group_by("g").aggregate(n=t.count()).order_by("n")  # 그룹마다 12행이라 모두 동률
    seen = pl.concat([e.limit(10_000, offset=o).to_polars() for o in range(0, 50_000, 10_000)])
    assert seen["g"].n_unique() == 50_000


def test_asof_after_a_join_with_shared_names(tmp_path):
    con = ibis.duckdb.connect()
    o = con.create_table("o", ibis.memtable({"id": [1], "cid": [1], "t": [5], "name": ["o"]}))
    con.create_table("c", ibis.memtable({"cid": [1], "name": ["c"], "t": [1]}))
    p = con.create_table("p", ibis.memtable({"id": [1], "t": [3], "name": ["p"]}))
    first = parse.apply_joins(o, [("c", "left", [("cid", "cid")], None)], con.table)
    e = parse.apply_joins(o, [("c", "left", [("cid", "cid")], None), ("p", "asof", [("id", "id")], ("t", "t"))], con.table)
    assert e.execute().to_dict("records")[0]["name_p"] == "p"
    assert list(flow.right_names(dict.fromkeys(first.columns), dict.fromkeys(p.columns), "asof", "p")) == list(e.columns)


def test_right_table_that_already_has_the_renamed_name():
    con = ibis.duckdb.connect()
    o = con.create_table("o", ibis.memtable({"cid": [1], "name": ["o"]}))
    con.create_table("r2", ibis.memtable({"cid": [1], "name": ["a"], "name_right": ["b"]}))
    e = parse.apply_joins(o, [("r2", "left", [("cid", "cid")], None)], con.table)
    assert e.execute().to_dict("records") == [{"cid": 1, "name": "o", "cid_r2": 1, "name_r2": "a", "name_right": "b"}]


def test_sql_tab_on_file_connection_warns_about_ties_when_paging(client, tmp_path):
    from core import server
    from query.search_app import FILE_SQL_PAGES
    pl.DataFrame({"i": list(range(30)), "k": [x % 3 for x in range(30)]}).write_csv(tmp_path / "pts.csv")
    con = source.open_flat_files([tmp_path / "pts.csv"])
    server.state.update(con=con)
    try:
        many = client.post("/api/sql/run", json={"sql": "SELECT * FROM pts ORDER BY k", "rows": 10}).get_json()
        few = client.post("/api/sql/run", json={"sql": "SELECT * FROM pts ORDER BY k", "rows": 100}).get_json()
    finally:
        server.state.update(con=None)
        source.close(con)
    assert many["more"] and many["note"] == FILE_SQL_PAGES
    assert not few["more"] and few["note"] == ""
