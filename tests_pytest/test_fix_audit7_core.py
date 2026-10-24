"""7차 감사 묶음 7A(core)·서버 성능 시험. 데이터는 시험 안에서 지어냈고 DuckDB·Polars 엔진은 진짜를 쓴다."""
import ctypes
import gzip
import os
import threading
import time

import polars as pl
import pytest
from flask import Response
from lazy import ibis

from core import dash_store, monitor, profiler, result, server, settings, source, sql_funcs


def lock_file(path, seconds=None):
    """다른 프로세스(백신 등)가 path를 단독으로 열어 둔 것처럼 잠근다. seconds가 있으면 그 뒤에 풀고, 없으면 돌려준 close()로 푼다."""
    k = ctypes.windll.kernel32
    k.CreateFileW.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    close = lambda: k.CloseHandle(handle)
    if seconds is not None:
        threading.Timer(seconds, close).start()
    return close


@pytest.fixture
def two_dashboards(folder):
    dash_store.CACHE["stamp"] = None
    dash_store.save_dashboard({"name": "첫번째", "cards": []})
    dash_store.save_dashboard({"name": "두번째", "cards": []})  # .dat = {첫번째, 두번째}, .bak = {첫번째}
    yield
    dash_store.CACHE["stamp"] = None


def names(**kw):
    return sorted(d["name"] for d in dash_store.load(**kw)["dashboards"].values())


# ---------- 7A1 잠깐 못 읽을 때 .bak으로 떨어지지 않는다 ----------

def test_read_waits_for_a_locked_file_instead_of_returning_the_backup(folder):
    settings.write("x.json", {"a": 1})
    settings.write("x.json", {"a": 2})  # .bak = {"a": 1}
    lock_file(folder / "x.json", seconds=0.1)
    assert settings.read("x.json") == {"a": 2}


def test_read_raises_when_the_file_stays_locked(folder):
    settings.write("x.json", {"a": 1})
    settings.write("x.json", {"a": 2})
    close = lock_file(folder / "x.json")
    try:
        with pytest.raises(PermissionError):
            settings.read("x.json")
    finally:
        close()


def test_read_still_uses_the_backup_for_broken_json(folder):
    settings.write("x.json", {"a": 1})
    settings.write("x.json", {"a": 2})
    (folder / "x.json").write_text("{깨짐", encoding="utf-8")
    assert settings.read("x.json") == {"a": 1}


def test_dash_load_does_not_cache_the_old_generation_after_a_short_lock(two_dashboards):
    dash_store.CACHE["stamp"] = None
    lock_file(dash_store.path_of(), seconds=0.1)
    assert names() == ["두번째", "첫번째"]
    assert names() == ["두번째", "첫번째"]  # 풀린 뒤에도 옛 값이 캐시에 남지 않는다


def test_dash_load_does_not_cache_a_result_that_came_from_the_backup(two_dashboards):
    path = dash_store.path_of()
    path.write_text("{깨짐", encoding="utf-8")
    dash_store.CACHE["stamp"] = None
    assert names() == ["첫번째"]  # 깨졌으니 한 세대 전 .bak
    assert dash_store.CACHE["stamp"] is None


# ---------- 7A2 바꿔치기 재시도 ----------

def test_dash_write_waits_for_a_busy_file_and_leaves_no_temp(two_dashboards):
    lock_file(dash_store.path_of(), seconds=0.1)
    dash_store.save_dashboard({"name": "세번째", "cards": []})
    assert names(fresh=True) == ["두번째", "세번째", "첫번째"]
    assert not list(dash_store.path_of().parent.glob("*.tmp"))


def test_dash_write_removes_the_temp_file_when_it_cannot_replace(two_dashboards, monkeypatch):
    def busy(a, b):
        raise PermissionError("busy")
    monkeypatch.setattr(os, "replace", busy)
    with pytest.raises(PermissionError):
        dash_store.save_dashboard({"name": "세번째", "cards": []})
    assert not list(dash_store.path_of().parent.glob("*.tmp"))


# ---------- 7A3 컬럼 이름이 user인 표 ----------

@pytest.mark.parametrize("column", ["user", "USERNAME", "password"])
def test_card_on_a_table_with_a_user_named_column_can_be_saved(column):
    source_ = {"schema": "", "table": "logins", "recipe": {"columns": {column: {"rename": "x"}}}, "analysis": {"kind": "x"}}
    assert dash_store.check_source(source_)["recipe"] == source_["recipe"]


def test_source_with_a_secret_top_level_key_is_still_rejected():
    with pytest.raises(ValueError):
        dash_store.check_source({"table": "t", "password": "x"})


# ---------- 7A4 Polars 반올림 ----------

def test_half_up_on_polars_with_two_or_more_rows_has_no_float_dust():
    vals = [1234.5678, 1.5, 0.285, 2.675, -1234.5678, 1e300, 4503599627370496.0, 7.0]
    con = ibis.polars.connect()
    con.create_table("t", pl.DataFrame({"x": vals}))
    got = {d: con.execute(sql_funcs.half_up(con.table("t").x, d)).tolist() for d in (0, 1, 2, -1)}
    duck = {d: ibis.duckdb.connect().execute(sql_funcs.half_up(ibis.memtable({"x": vals}).x, d)).tolist() for d in got}
    assert got[1][:5] == [1234.6, 1.5, 0.3, 2.7, -1234.6]
    assert got[2][:4] == [1234.57, 1.5, 0.28, 2.68] or got[2][:4] == duck[2][:4]
    assert got == duck  # 같은 값이 두 엔진에서 같다


def test_half_up_on_duckdb_is_unchanged():
    t = ibis.memtable({"x": [1234.5678, 0.5, 2.5, -2.5, 1.005]})
    assert ibis.duckdb.connect().execute(sql_funcs.half_up(t.x, 0)).tolist() == [1235.0, 1.0, 3.0, -3.0, 1.0]


# ---------- 7A5·7A6 CSV 바뀜: 안내 문구와 csv_gen ----------

@pytest.fixture
def opened():
    cons = []

    def make(*paths):
        con = source.open_flat_files.__wrapped__(list(paths))
        cons.append(con)
        server.state["tables"].clear()
        return con
    yield make
    for c in cons:
        source.close(c)
    server.state["tables"].clear()


def test_unreadable_changed_csv_gives_the_intended_message(tmp_path, opened):
    p = tmp_path / "k.csv"
    p.write_bytes("이름,값\n가,1\n나,2\n".encode("cp949"))
    con = opened(p)
    p.write_bytes("﻿이름,값\n가,1\n".encode("utf-16"))  # UTF-16은 읽지 못한다
    assert source.refresh_csv(con) == ["k"]
    with pytest.raises(ValueError, match="UTF-16"):
        con.table("k").execute()


def test_fail_table_does_not_read_the_changed_file(tmp_path, opened):
    p = tmp_path / "k.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    con = opened(p)
    columns = con.table("k").columns
    p.write_text("a,b,c\n1,2,3\n", encoding="utf-8")  # 바뀐 원본: 스키마가 다르다
    source.fail_table(con, "k", "읽을 수 없어요")
    assert con.table("k").columns == columns
    with pytest.raises(ValueError, match="읽을 수 없어요"):
        con.table("k").execute()


def test_csv_gen_makes_any_table_and_row_count_follow_added_columns(tmp_path, opened, monkeypatch):
    p = tmp_path / "k.csv"
    p.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    con = opened(p)
    monkeypatch.setattr(server, "get_con", lambda: con)
    monkeypatch.setattr(server, "run", lambda f: f())
    server.state["rows"].clear()
    assert server.any_table(con, "k", None, vts={}).columns == ("a", "b")
    assert server.row_count(None, "k") == 2
    p.write_text("a,b,c\n1,2,9\n3,4,9\n5,6,9\n", encoding="utf-8")
    assert server.any_table(con, "k", None, vts={}).columns == ("a", "b", "c")
    assert server.row_count(None, "k") == 3
    assert con.csv_gen["k"] == 1
    server.state["rows"].clear()


def test_csv_gen_message_for_an_unreadable_file_reaches_row_count(tmp_path, opened, monkeypatch):
    p = tmp_path / "k.csv"
    p.write_bytes("이름,값\n가,1\n".encode("cp949"))
    con = opened(p)
    monkeypatch.setattr(server, "get_con", lambda: con)
    monkeypatch.setattr(server, "run", lambda f: f())
    server.state["rows"].clear()
    assert server.row_count(None, "k") == 1
    p.write_bytes("﻿이름,값\n가,1\n".encode("utf-16"))
    with pytest.raises(ValueError, match="UTF-16"):
        server.row_count(None, "k")
    server.state["rows"].clear()


# ---------- 7A7 옮기기 실패 안내를 다시 ----------

def test_left_behind_repeats_the_notice_while_the_old_folder_is_left(tmp_path):
    new, old = tmp_path / "DataLoupe", tmp_path / settings.OLD_NAME
    old.mkdir()
    (old / "settings.json").write_text('{"connections": "important"}')
    new.mkdir()
    (new / settings.FAILED).write_text("예전 폴더를 옮기지 못했습니다", encoding="utf-8")
    (new / "settings.json").write_text("{}")  # 실패한 뒤 새 폴더에 설정이 생겼다
    assert settings.migrate(new / "settings.json") is None  # 병합은 하지 않는다
    assert settings.left_behind(new / "settings.json") == "예전 폴더를 옮기지 못했습니다"
    assert (old / "settings.json").read_text() == '{"connections": "important"}' and (new / "settings.json").read_text() == "{}"


def test_left_behind_is_quiet_without_the_old_folder_or_the_mark_or_new_files(tmp_path):
    new, old = tmp_path / "DataLoupe", tmp_path / settings.OLD_NAME
    new.mkdir()
    (new / settings.FAILED).write_text("x", encoding="utf-8")
    (new / "settings.json").write_text("{}")
    assert settings.left_behind(new / "settings.json") is None  # 옛 폴더가 없다
    old.mkdir()
    (new / "settings.json").unlink()
    assert settings.left_behind(new / "settings.json") is None  # 새 폴더에 파일이 아직 없다(migrate가 다시 옮긴다)
    (new / "settings.json").write_text("{}")
    (new / settings.FAILED).unlink()
    assert settings.left_behind(new / "settings.json") is None  # 실패 표식이 없다


# ---------- 7A8 gzip;q=0 ----------

@pytest.mark.parametrize("header, gzipped", [("gzip", True), ("gzip;q=0.5", True), ("gzip;q=0, identity", False), ("identity", False), ("", False), ("*", True)])
def test_squeeze_honours_the_quality_value(header, gzipped):
    big = Response(b"[" + b"1," * server.GZIP_FROM + b"1]", mimetype="application/json")
    with server.app.test_request_context(headers={"Accept-Encoding": header}):
        got = server.squeeze(big)
    assert (got.headers.get("Content-Encoding") == "gzip") is gzipped
    if gzipped:
        assert gzip.decompress(got.get_data()).startswith(b"[1,")


# ---------- 7P9(b)·7P10·7P11 Polars 백엔드 ----------

@pytest.fixture
def big_csv(tmp_path):
    rows = 20_000
    df = pl.DataFrame({"id": range(rows), "city": [f"c{i % 7}" for i in range(rows)], "grp": [i % 3 for i in range(rows)], "amount": [(i * 37 % 1000) / 8 for i in range(rows)]})
    p = tmp_path / "sales.csv"
    df.write_csv(p)
    return p, df


@pytest.fixture
def polars_con(big_csv, opened):
    return opened(big_csv[0])


@pytest.fixture
def engines(monkeypatch):
    """LazyFrame.collect가 받은 engine 값들을 모은다."""
    seen = []
    original = pl.LazyFrame.collect

    def spy(self, *a, **k):
        seen.append(k.get("engine"))
        return original(self, *a, **k)
    monkeypatch.setattr(pl.LazyFrame, "collect", spy)
    return seen


def test_get_count_on_polars_uses_pl_len_and_matches(polars_con, big_csv, monkeypatch):
    t = polars_con.table("sales")
    filtered = t.filter(t.city == "c3")
    monkeypatch.setattr(ibis.expr.types.Table, "count", lambda self, *a, **k: pytest.fail("ibis count() 대신 pl.len()을 써야 한다"))
    assert result.get_count(t) == 20_000
    assert result.get_count(filtered) == big_csv[1].filter(pl.col("city") == "c3").height
    assert result.get_count(t, polars_con) == 20_000
    assert result.get_count(t.limit(0)) == 0


def test_get_count_on_duckdb_is_unchanged():
    t = ibis.duckdb.connect().create_table("t", ibis.memtable({"a": [1, 2, 3]}))
    assert result.get_count(t.filter(t.a > 1)) == 2


def test_get_rows_values_and_count_charts_stream_on_polars_with_same_results(polars_con, big_csv, engines):
    t = polars_con.table("sales")
    df = big_csv[1]
    rows = result.get_rows(t.order_by([t.amount.desc(), t.id]), 25, 5)
    assert rows.rows() == df.sort(["amount", "id"], descending=[True, False]).slice(5, 25).rows()
    assert "streaming" in engines
    engines.clear()
    values, more = result.get_values(t, "city")
    assert values == sorted(df["city"].unique().to_list()) and more is False
    assert "streaming" in engines
    engines.clear()
    labels, series, _ = result.get_chart(t, "city", [{"agg": "count"}, {"y": "id", "agg": "nunique"}], order="value")
    want = df.group_by("city").agg(pl.len().alias("n")).sort(["n", "city"], descending=[True, False])
    assert labels == want["city"].to_list() and series[0]["values"] == want["n"].to_list() and series[1]["values"] == want["n"].to_list()
    assert "streaming" in engines
    engines.clear()
    _, split_series, _ = result.get_chart(t, "city", [{"agg": "count"}], split="grp")
    assert len(split_series) == 3 and "streaming" in engines


def test_sums_and_means_are_not_streamed(polars_con, engines):
    t = polars_con.table("sales")
    result.get_chart(t, "city", [{"y": "amount", "agg": "sum"}])
    result.get_chart(t, "city", [{"agg": "count"}, {"y": "amount", "agg": "mean"}])  # 하나라도 합계·평균이면 streaming이 아니다
    result.get_summary(t)
    assert "streaming" not in engines


def test_duckdb_calls_get_no_engine_argument():
    t = ibis.duckdb.connect().create_table("t", ibis.memtable({"a": [3, 1, 2], "b": ["x", "y", "x"]}))
    assert result.get_rows(t.order_by("a"), 2).rows() == [(1, "y"), (2, "x")]
    assert result.get_values(t, "b") == (["x", "y"], False)
    assert result.get_chart(t, "b", [{"agg": "count"}])[0] == ["x", "y"]


def test_block_sample_on_polars_streams_and_keeps_the_size(polars_con, engines):
    t = polars_con.table("sales")
    found = profiler.block(polars_con, t, "", "sales", 500, 20_000, None)
    assert found is not None and 0 < found.num_rows <= 500 and found.schema.names == ["id", "city", "grp", "amount"]
    assert "streaming" in engines


# ---------- 7P13 자식 프로세스 목록 기억 ----------

def test_usage_scans_children_once_in_ten_seconds(monkeypatch):
    calls = []

    class Me:
        def __init__(self, real):
            self.real = real

        def children(self, recursive=False):
            calls.append(recursive)
            return self.real.children(recursive=recursive)

        def __getattr__(self, name):
            return getattr(self.real, name)
    monkeypatch.setattr(monitor, "ME", Me(monitor.ME))
    monkeypatch.setitem(monitor.kids, "at", -monitor.KIDS_FOR)
    first = monitor.usage()
    monitor.usage()
    monitor.usage()
    assert len(calls) == 1 and set(first) >= {"pc_memory", "memory", "memory_mb", "cpu", "pc_cpu"}
    monitor.kids["at"] = time.monotonic() - monitor.KIDS_FOR - 1  # 10초가 지나면 다시 훑는다
    monitor.usage()
    assert len(calls) == 2


def test_half_up_on_duckdb_big_values_are_not_rounded_twice():
    t = ibis.memtable({"x": [5e13 + 0.123456, 4e13 + 0.123456]})
    assert ibis.duckdb.connect().execute(sql_funcs.half_up(t.x, 2)).tolist() == pytest.approx([5e13 + 0.12, 4e13 + 0.12], abs=0.02)
