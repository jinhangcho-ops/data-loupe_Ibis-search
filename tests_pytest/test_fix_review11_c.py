"""11차 묶음 C: 2차원 배열 안 이진, 원격 큰 결과의 쪽 넘김 안내, 모든 방언의 수식 막기, Excel 시간대, NaN 한 칸의 요약·차트, 날짜 차트의 최근 쪽.
시험 데이터는 여기서 만든다(DuckDB·Polars·SQLite 파일). 다른 DB에는 붙지 않는다."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from core import held, result, server, source
from lazy import ibis


def test_two_dim_binary_array_keeps_its_shape():
    df = pl.DataFrame([pl.Series("a", [[[b"a", b"b"], [b"c", b"d"]]], dtype=pl.Array(pl.Binary, (2, 2)))])
    out = server.text_wide(df, True)
    assert out.dtypes == [pl.Array(pl.String, (2, 2))]
    assert out["a"].to_list() == [[["61", "62"], ["63", "64"]]]


def test_remote_too_big_note_stays_on_later_pages(client, tmp_path, monkeypatch):
    """원격 DB(SQLite) 결과가 받아 둘 상한을 넘으면 둘째 쪽도 TOO_BIG 그대로. 정렬 값이 겹치는지 세는 조회(tied)를 원격에 보내지 않는다."""
    from query import search_app
    raw = sqlite3.connect(tmp_path / "r.db")
    raw.execute("create table t (i integer, k integer)")
    raw.executemany("insert into t values (?, ?)", [(i, i % 3) for i in range(30)])
    raw.commit()
    raw.close()
    con = source.within(30, lambda: ibis.sqlite.connect(tmp_path / "r.db"), "")  # SQLite는 연결 스레드에서만 쓸 수 있다
    monkeypatch.setattr(held, "MAX_ROWS", 5)
    asked, real = [], search_app.tied
    monkeypatch.setattr(search_app, "tied", lambda body: asked.append(body) or real(body))
    server.state.update(con=con, opened={"kind": "url", "url": "sqlite://"})
    try:
        ask = lambda offset: client.post("/api/query", json={"base": "t", "sorts": ["k"], "limit": 10, "offset": offset}).get_json()
        first, second = ask(0), ask(10)
    finally:
        server.state.update(con=None)
        server.forget_tables()  # 기억한 표 t가 닫힌 SQLite를 가리키지 않게(다음 시험의 같은 이름 표)
        source.within(30, lambda: (held.clear(), con.disconnect()), "")
    assert first["note"] == second["note"] == search_app.TOO_BIG
    assert len(second["rows"]["rows"]) == 10 and not asked


def test_formula_guard_compiles_for_mssql(monkeypatch, tmp_path):
    """수식 막기 식이 정규식 없이: SQL Server(·Synapse·Exasol)도 SQL로 바뀐다. DuckDB에서 값은 전과 같다."""
    seen = []
    monkeypatch.setitem(result.EXPORTS, ".csv", lambda expr, path: seen.append(expr))
    result.export(ibis.table({"s": "string", "n": "int64"}, name="t"), tmp_path / "x.csv")
    assert "'" in ibis.to_sql(seen[0], dialect="mssql")
    monkeypatch.undo()
    t = ibis.memtable({"s": ["=1+1", "+a", "-b", "@c", "\tt", "\rr", "ok", "", None]})
    result.export(t, tmp_path / "y.csv")
    got = pl.read_csv(tmp_path / "y.csv")["s"].to_list()
    assert got == ["'=1+1", "'+a", "'-b", "'@c", "'\tt", "'\rr", "ok", "", None]


def test_excel_shows_pc_wall_clock(tmp_path):
    at = datetime(2024, 3, 2, 3, 4, tzinfo=timezone.utc)  # 자정이 아니게(UTC PC에서 자정이면 Excel을 읽을 때 날짜로만 돌아온다)
    t = ibis.memtable(pl.DataFrame({"at": [at]}).with_columns(pl.col("at").dt.replace_time_zone("UTC")))
    result.export(t, tmp_path / "x.xlsx")
    zone = source.pc_zone()
    want = pl.Series([at]).dt.convert_time_zone(zone).dt.replace_time_zone(None)[0]
    assert pl.read_excel(tmp_path / "x.xlsx")["at"][0] == want


def test_pc_zone_is_a_name_polars_and_duckdb_take():
    zone = source.pc_zone()
    assert pl.Series([datetime(2024, 1, 1)]).dt.replace_time_zone("UTC").dt.convert_time_zone(zone).len() == 1
    assert zone  # 'Asia/Seoul' 꼴 또는 '+09:00' 꼴


@pytest.fixture(params=["duckdb", "polars"])
def nan_table(request):
    frame = pl.DataFrame({"g": ["a", "a", "b"], "v": [1.0, float("nan"), 3.0]})
    con = ibis.duckdb.connect() if request.param == "duckdb" else ibis.polars.connect()
    con.create_table("t", ibis.memtable(frame))
    return con.table("t")


def test_summary_skips_nan(nan_table):
    row = result.get_summary(nan_table, ["v"])[0]
    assert (row["count"], row["nulls"], row["min"], row["max"], row["mean"]) == (3, 0, 1.0, 3.0, 2.0)  # 개수는 전처럼 NaN도 센다


def test_chart_metric_skips_nan(nan_table):
    labels, series, _ = result.get_chart(nan_table, "g", [{"y": "v", "agg": "sum"}, {"y": "v", "agg": "mean"}, {"y": "v", "agg": "max"}])
    got = {label: [s["values"][i] for s in series] for i, label in enumerate(labels)}
    assert got == {"a": [1.0, 1.0, 1.0], "b": [3.0, 3.0, 3.0]}


def test_date_chart_keeps_most_recent_groups():
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(result.MAX_GROUPS + 10)]
    t = ibis.memtable({"d": days})
    labels, series, _ = result.get_chart(t, "d", [{"agg": "count"}])
    assert labels == days[-result.MAX_GROUPS:]  # 최근 50일, 오래된 날부터
    assert series[0]["values"] == [1] * result.MAX_GROUPS
    names = ibis.memtable({"n": [f"n{i:03d}" for i in range(result.MAX_GROUPS + 10)]})
    assert result.get_chart(names, "n", [{"agg": "count"}])[0][0] == "n000"  # 날짜가 아니면 전처럼 앞에서부터
