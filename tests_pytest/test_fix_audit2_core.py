"""2차 감사 수정 2A(core): 고치기 전에는 실패하던 것들을 하나씩 재현한다. 데이터는 테스트 안에서 지어내고 값은 손으로 센다."""
import threading

import polars as pl
import pytest
from lazy import ibis

from core import sql_funcs


def both_engines(data, name="t"):
    """같은 표를 DuckDB와 Polars 연결에 올린 {엔진: 표}. Polars는 파일 연결이 쓰는 같은 보정(source.fix_polars_text)을 건다."""
    from core import source
    if hasattr(source, "fix_polars_text"):
        source.fix_polars_text()
    return {n: c.create_table(name, ibis.memtable(pl.DataFrame(data))) for n, c in (("duckdb", ibis.duckdb.connect()), ("polars", ibis.polars.connect()))}


# ---------- 2A1 고유 개수에서 빈 값 ----------

def test_distinct_count_ignores_nulls_on_both_engines():
    data = {"x": ["a", None, "a", "b", None], "g": [1, 1, 2, 2, 2]}
    for name, t in both_engines(data).items():
        assert t.aggregate(v=sql_funcs.aggregate("count_distinct", t, "x")).to_polars()["v"][0] == 2, name
        by = t.group_by("g").aggregate(v=sql_funcs.aggregate("count_distinct", t, "x")).to_polars().sort("g")["v"].to_list()
        assert by == [1, 2], name  # g=1: a·빈 값 → 1, g=2: a·b·빈 값 → 2
        assert t.aggregate(v=sql_funcs.aggregate("approx_count_distinct", t, "x")).to_polars()["v"][0] == 2, name


def test_unique_or_null_and_conflicts_ignore_nulls():
    data = {"k": [1, 1, 2, 2], "x": ["a", None, "b", "c"]}
    for name, t in both_engines(data).items():
        got = t.group_by("k").aggregate(v=sql_funcs.aggregate("unique_or_null", t, "x")).to_polars().sort("k")["v"].to_list()
        assert got == ["a", None], name  # k=1은 값이 a 하나뿐(빈 값은 세지 않는다)
        assert sql_funcs.conflicts(t, ["k"], ["x"]).to_polars() == 1, name


def test_summary_unique_ignores_nulls():
    from core import result
    for name, t in both_engines({"x": ["a", None, "a", "b"]}).items():
        assert result.get_summary(t)[0]["unique"] == 2, name


# ---------- 2A2 Polars length·find ----------

def test_polars_length_and_find_count_characters():
    data = {"s": ["홍길동", None, "abc"]}
    for name, t in both_engines(data).items():
        got = t.select(l=t.s.length(), f=t.s.find("길")).to_polars()
        assert got["l"].to_list() == [3, None, 3], name
        assert got["f"].to_list() == [1, None, -1], name  # 글자 위치(0부터), 빈 값은 빈 값
        assert t.select(l=ibis.literal("홍길동").length()).to_polars()["l"][0] == 3, name


# ---------- 2A3 PC 사본 새로 받기 ----------

def test_runnable_passes_fresh_to_held(monkeypatch):
    from core import held, querying, source
    seen = []
    monkeypatch.setattr(held, "ensure", lambda key, expr, stop, **more: (seen.append(more.get("fresh")), (None, 0))[1])
    monkeypatch.setattr(source, "compiles", lambda expr: False)
    monkeypatch.setattr(querying, "build", lambda body, select=True: ibis.memtable({"a": [1]}))
    monkeypatch.setattr(querying, "filtered", lambda body: ibis.memtable({"a": [1]}))
    monkeypatch.setattr(querying, "run", lambda f: f())
    monkeypatch.setattr(querying, "search_key", lambda body, keep=None: "k")
    for fresh in (False, True):
        with pytest.raises(ValueError):
            querying.runnable({"base": "t"}, fresh=fresh)
    assert seen == [False, True]


def test_held_ensure_fresh_reads_changed_source(tmp_path):
    from core import held
    path = tmp_path / "a.csv"
    path.write_text("n\n1\n2\n3\n", encoding="utf-8")
    con = ibis.duckdb.connect()
    expr = con.read_csv(str(path), table_name="src")
    try:
        assert held.ensure("k", expr, threading.Event())[1] == 3
        path.write_text("n\n1\n2\n3\n4\n5\n", encoding="utf-8")
        assert held.ensure("k", expr, threading.Event())[1] == 3  # 사본을 그대로 쓴다
        assert held.ensure("k", expr, threading.Event(), fresh=True)[1] == 5
        path.write_text("n\n1\n", encoding="utf-8")
        with held.renewing(set()):  # 최신화·Slack 질의가 차례를 잡았다: 쓰는 사본은 처음 쓸 때 새로
            assert held.ensure("k", expr, threading.Event())[1] == 1
    finally:
        held.clear()


def test_using_fresh_starts_and_ends_renewal(client, monkeypatch):
    from core import held, querying, server
    server.state["con"] = object()
    monkeypatch.setattr(querying, "run", lambda f: f())
    with querying.using():
        assert held.RENEWED[0] is None
    with querying.using(fresh=True):
        assert held.RENEWED[0] == set()
    assert held.RENEWED[0] is None


# ---------- 2A4 연결 열쇠: 폴더만 있던 예전 열쇠 ----------

def test_same_source_accepts_old_folder_key():
    from core import use_counts
    new = "folder:c:/data#c:/data/a.duckdb"
    assert use_counts.same_source("folder:c:/data", new)  # 예전 열쇠(폴더만)
    assert use_counts.same_source(new, new)
    assert not use_counts.same_source("folder:c:/data#c:/data/b.duckdb", new)  # 다른 파일
    assert not use_counts.same_source("folder:c:/other", new)
    assert not use_counts.same_source("", new) and not use_counts.same_source(None, new)
    assert use_counts.renewed("folder:c:/data", new) == new and use_counts.renewed("", new) == new
    assert use_counts.renewed("folder:c:/other", new) == "folder:c:/other"


def open_file(name):
    from core import server
    server.state["con"] = object()
    server.state["opened"] = {"kind": "folder", "path": "C:/data", "file": f"C:/data/{name}"}


def test_card_check_overwrites_old_key(client):
    from core import dash_store
    open_file("a.duckdb")
    now = dash_store.current_source()["key"]
    assert "#" in now
    assert dash_store.check_source({"key": now.split("#")[0], "table": "t"})["key"] == now  # 저장할 때 새 열쇠로
    assert dash_store.check_source({"key": "folder:c:/other", "table": "t"})["key"] == "folder:c:/other"


def test_refresh_and_compute_accept_old_key(client):
    import datetime as dt
    from core import dash_store
    from dashboard import dash_refresh
    open_file("a.duckdb")
    now = dash_store.current_source()["key"]
    card = {"kind": "kpi", "source": {"key": now.split("#")[0]}, "data": {}}
    assert dash_refresh.skip_reason({"refresh": {}}, card, now, dt.datetime.now()) != "다른 연결에서 구한 카드예요"
    res = client.post("/api/dash/card/compute?key=", json={"card": {"kind": "kpi", "title": "k", "source": {"key": now.split("#")[0], "table": "t"}}})
    assert "다른 연결" not in res.get_data(as_text=True)


def test_sentence_scope_accepts_old_key(client):
    from core import dash_store
    from query import sentence_exec
    open_file("a.duckdb")
    old = dash_store.current_source()["key"].split("#")[0]
    sentence_exec.check_scope({"scope": {"key": old, "table": "t"}}, {"base": "t"})
    with pytest.raises(ValueError):
        sentence_exec.check_scope({"name": "m", "scope": {"key": "folder:c:/other", "table": "t"}}, {"base": "t"})


# ---------- 2A5 초안 열쇠 ----------

def test_draft_connection_key_tells_files_apart(client):
    from core import draft_app, server
    keys = []
    for name in ("a.duckdb", "b.duckdb"):
        server.state["opened"] = {"kind": "folder", "path": "C:/data", "file": f"C:/data/{name}"}
        keys.append(draft_app.connection()["key"])
    assert keys[0] != keys[1]


# ---------- 2A7 반올림·나머지 ----------

def test_half_up_rounds_the_same_everywhere_and_survives_big_values():
    data = {"x": [0.5, 1.5, 2.5, -2.5, 1e13, float("nan"), float("inf"), None]}
    for name, t in both_engines(data).items():
        for digits in (0, 6):
            got = t.select(r=sql_funcs.half_up(t.x, digits)).to_polars()["r"].to_list()
            assert got[:5] == ([1.0, 2.0, 3.0, -3.0, 1e13] if digits == 0 else [0.5, 1.5, 2.5, -2.5, 1e13]), (name, digits)
            assert got[5] != got[5] and got[6] == float("inf") and got[7] is None, (name, digits)  # nan·inf는 그대로, 빈 값은 빈 값


def test_round_users_share_half_up():
    from core import calc, prep_derive, prep_ops
    for name, t in both_engines({"x": [0.5, 1.5, 2.5, -2.5]}).items():
        want = [1.0, 2.0, 3.0, -3.0]
        assert t.select(r=sql_funcs.make("round", t, {"x": "x", "digits": 0})).to_polars()["r"].to_list() == want, name
        assert calc.apply(["반올림 0 x"], t).to_polars()["x"].to_list() == want, name
        assert t.select(r=prep_derive.Formula(t, "round(x)").build()).to_polars()["r"].to_list() == want, name
        assert prep_ops.apply_ops(t, "x", {"round": 0}).to_polars()["x"].to_list() == want, name


def test_mod_sign_follows_dividend_on_both_engines():
    from core import prep_derive
    for name, t in both_engines({"x": [7, -7, 7, -7], "f": [7.5, -7.5, 0.5, 0.0]}).items():
        assert t.select(r=sql_funcs.make("mod", t, {"x": "x", "by": 3})).to_polars()["r"].to_list() == [1, -1, 1, -1], name
        assert t.select(r=sql_funcs.make("mod", t, {"x": "f", "by": 2})).to_polars()["r"].to_list() == [1.5, -1.5, 0.5, 0.0], name
        assert t.select(r=prep_derive.Formula(t, "x % 3").build()).to_polars()["r"].to_list() == [1, -1, 1, -1], name


def test_formula_mod_by_literal_zero_is_korean_error():
    from core import prep_derive
    t = ibis.memtable({"x": [1, 2]})
    with pytest.raises(ValueError, match="0으로 나눈 나머지"):
        prep_derive.Formula(t, "x % 0").build()


# ---------- 2A22 산술에 쓰는 큰 정수 ----------

def test_to_json_keeps_big_ints_numeric_when_asked():
    from core import server
    df = pl.DataFrame({"n": [2**60, 1]})
    assert server.to_json(df)["rows"][0]["n"] == str(2**60)  # 화면에는 글자로
    assert server.to_json(df, text_big=False)["rows"][0]["n"] == 2**60  # 서버가 계산에 쓰는 결과는 숫자로


# ---------- 2A6 Polars 조각·간격·날짜 컬럼의 시각 단위 ----------

def test_split_part_out_of_range_is_null_on_both_engines():
    for name, t in both_engines({"s": ["a,b", "x", None]}).items():
        assert t.select(r=sql_funcs.make("split_part", t, {"x": "s", "sep": ",", "n": 2})).to_polars()["r"].to_list() == ["b", None, None], name


def test_polars_month_interval_goes_to_pc_copy_instead_of_failing():
    import datetime as dt
    from core import source
    source.fix_polars_text()
    t = ibis.polars.connect().create_table("t", ibis.memtable({"d": [dt.date(2026, 1, 31)]}))
    month = t.mutate(r=sql_funcs.make("date_add", t, {"x": "d", "n": 1, "unit": "month"}))
    assert not source.compiles(month)  # Polars가 못 하는 계산: PC 사본(DuckDB)에서
    assert source.compiles(t.mutate(r=sql_funcs.make("date_add", t, {"x": "d", "n": 1, "unit": "day"})))


def test_time_unit_on_date_column_is_korean_error():
    import datetime as dt
    t = ibis.memtable({"d": [dt.date(2026, 1, 31)]})
    for id, raw in (("truncate", {"x": "d", "unit": "hour"}), ("date_add", {"x": "d", "n": 1, "unit": "minute"})):
        with pytest.raises(ValueError, match="날짜시간"):
            sql_funcs.make(id, t, raw)


# ---------- 2A8 날짜시간 컬럼에 날짜만 쓴 조건 ----------

def test_date_only_condition_on_timestamp_column_means_that_day():
    import datetime as dt
    from core import parse
    t = ibis.memtable({"t": [dt.datetime(2026, 10, 4, 23, 59), dt.datetime(2026, 10, 5, 0, 0), dt.datetime(2026, 10, 5, 13, 30), dt.datetime(2026, 10, 6, 0, 0)]})
    count = lambda line: t.filter(parse.parse_condition(line, t)).count().to_pyarrow().as_py()
    assert count("t = 2026-10-05") == 2 and count("t != 2026-10-05") == 2
    assert count("t <= 2026-10-05") == 3 and count("t > 2026-10-05") == 1
    assert count("t >= 2026-10-05") == 3 and count("t < 2026-10-05") == 1
    assert count("t = 2026-10-05T13:30:00") == 1  # 시각까지 쓰면 그대로


# ---------- 2A9 한글 엑셀(CP949) CSV ----------

def test_cp949_csv_opens_with_korean_intact(tmp_path):
    from core import source
    path = tmp_path / "k.csv"
    path.write_bytes("이름,값\n홍길동,1\n가나다,2\n".encode("cp949"))
    good = tmp_path / "u.csv"
    good.write_text("이름,값\n홍길동,1\n", encoding="utf-8")
    con = source.open_flat_files([path, good])
    try:
        t = con.table("k")
        assert list(t.columns) == ["이름", "값"] and t.to_polars()["이름"].to_list() == ["홍길동", "가나다"]
        assert con.table("u").to_polars()["이름"].to_list() == ["홍길동"]
    finally:
        source.close(con)


# ---------- 2A10 차트 나누기 ----------

def test_chart_split_keeps_null_x_group_and_empty_table_has_series():
    from core import result
    for name, t in both_engines({"x": [None, "k", "k"], "s": ["a", "a", "b"], "v": [1, 2, 3]}).items():
        labels, series, _ = result.get_chart(t, "x", [{"y": "v", "agg": "sum"}], "s")
        assert labels == ["k", None]
        assert {s["split"]: s["values"] for s in series} == {"a": [2, 1], "b": [3, None]}, name  # X가 빈 값인 묶음의 a 값(1)을 버리지 않는다
    empty = ibis.memtable(pl.DataFrame({"x": pl.Series([], dtype=pl.Utf8), "s": pl.Series([], dtype=pl.Utf8), "v": pl.Series([], dtype=pl.Int64)}))
    labels, series, more = result.get_chart(empty, "x", [{"y": "v", "agg": "sum"}], "s")
    assert labels == [] and len(series) == 1 and series[0]["values"] == [] and not more  # 행이 없어도 계열은 있다


# ---------- 2A11 누적 창: 같은 기준 값은 같은 결과 ----------

def test_cumulative_window_gives_same_value_to_tied_rows():
    t = ibis.duckdb.connect().create_table("t", ibis.memtable({"d": [1, 1, 2, 2], "x": [10, 20, 30, 40]}))
    assert t.mutate(r=sql_funcs.make("cum_sum", t, {"x": "x", "order": ["d"]})).to_polars()["r"].to_list() == [30, 30, 100, 100]
    got = t.mutate(r=sql_funcs.make("moving_mean", t, {"x": "x", "order": ["d"], "n": 2})).to_polars()["r"].to_list()
    assert got == [10.0, 15.0, 25.0, 35.0]  # 이동 창은 그대로 행 단위


# ---------- 2A13 큰 값 글자 ----------

def test_compare_formats_huge_numbers():
    from core import compare
    assert compare.format_value(1e26, "currency") == "₩" + f"{10**26:,}"
    assert compare.format_value(1e30).startswith("1,000,000,000,000,000,000,000,000,000,000")
    assert "늘었어요" in compare.compare(1e30, 5e29, "currency")["text"]


# ---------- 2A14 드라이브가 다른 파일 ----------

def test_table_names_with_different_drives():
    from pathlib import PureWindowsPath as P
    from core import source
    assert source.table_names([P("C:/a/x.csv"), P("D:/b/y.csv")]) == ["x", "y"]
    assert source.table_names([P("C:/a/x.csv"), P("D:/b/x.csv")]) == ["C_a_x_csv", "D_b_x_csv"]


# ---------- 2A15 지표 파일 캐시 ----------

def test_metric_registry_reads_file_once_until_it_changes(folder, tmp_path, monkeypatch):
    from core import metric_file
    path = tmp_path / "m.yaml"
    path.write_text("metrics:\n  a:\n    numerator: {agg: count}\n", encoding="utf-8")
    metric_file.save_file_path(str(path))
    metric_file.CACHE.clear()
    reads = []
    real = metric_file.read_file
    monkeypatch.setattr(metric_file, "read_file", lambda p: (reads.append(p), real(p))[1])
    first = metric_file.registry()
    first["a"]["description"] = "바꿔도"  # 돌려준 값을 고쳐도 캐시는 그대로
    assert metric_file.registry()["a"]["description"] != "바꿔도" and len(reads) == 1
    path.write_text("metrics:\n  a:\n    numerator: {agg: count}\n  b:\n    numerator: {agg: count}\n", encoding="utf-8")
    assert sorted(metric_file.registry()) == ["a", "b"] and len(reads) == 2


# ---------- 2A16 따옴표 나누기 ----------

def test_split_with_many_commas_is_fast_and_keeps_quotes():
    import time
    from core import parse
    assert parse.split(parse.COMMA_RE, "a, 'b, c', d") == ["a", " 'b, c'", " d"]
    start = time.perf_counter()
    assert len(parse.split(parse.COMMA_RE, "x in " + "'a' , " * 8000)) == 8001
    with pytest.raises(ValueError, match="닫히지"):
        parse.split(parse.COMMA_RE, "x in " + "'a, " * 8000)
    assert time.perf_counter() - start < 2


# ---------- 2A17 서버 결과 테이블 ----------

def test_server_page_reads_destination_after_job_finishes():
    from core import held

    class Job:
        destination = None

        def result(self):
            self.destination = "dest"  # 끝나야 결과 테이블이 정해진다
            return type("R", (), {"total_rows": 7})

    class Client:
        def query(self, sql, project=None):
            return Job()

        def list_rows(self, destination, start_index=0, max_results=10):
            assert destination == "dest"
            return type("L", (), {"to_arrow": lambda self: pl.DataFrame({"a": [1]}).to_arrow()})()
    con = type("C", (), {"client": Client(), "billing_project": None})()
    held.store["jobs"].clear()
    total, df = held.server_page("k", con, ibis.memtable({"a": [1]}), 10, 0)
    assert total == 7 and df["a"].to_list() == [1]
    held.store["jobs"].clear()


# ---------- 2A18 정규식 꺼내기·이어 쓰기 ----------

def test_regex_extract_no_match_is_null_and_concat_skips_nulls():
    data = {"a": ["x1", "none", None], "b": ["p", None, None]}
    for name, t in both_engines(data).items():
        got = t.select(r=sql_funcs.make("regex_extract", t, {"x": "a", "pattern": "([0-9]+)", "group_no": 1})).to_polars()["r"].to_list()
        assert got == ["1", None, None], name
        got = t.select(r=sql_funcs.make("concat", t, {"columns": ["a", "b"], "sep": "-"})).to_polars()["r"].to_list()
        assert got == ["x1-p", "none", None], name  # 빈 값은 건너뛰고 구분자도 붙이지 않는다


# ---------- 2A19 SQL 직접 쓰기의 함수 이름 ----------

def test_sql_check_allows_json_query_but_blocks_remote_query_and_sleep():
    from core import sql_exec
    sql_exec.check("SELECT json_query(a, '$.x') FROM t")
    for bad in ("SELECT * FROM postgres_query('db', 'select 1')", "SELECT pg_sleep(100)", "SELECT * FROM delta_scan('x')"):
        with pytest.raises(sql_exec.SecurityViolationError):
            sql_exec.check(bad)


# ---------- 2A20 두 날짜의 달 수 차이 ----------

def test_date_diff_months_and_years_are_full_units_without_adding_intervals():
    import datetime as dt
    d = lambda y, m, dd: dt.datetime(y, m, dd)
    t = ibis.duckdb.connect().create_table("t", ibis.memtable({"a": [d(1990, 12, 31), d(2026, 1, 15), d(2026, 3, 15), d(2026, 5, 20)], "b": [d(2026, 10, 23), d(2026, 3, 14), d(2026, 1, 15), d(2026, 5, 20)]}))
    month = t.select(r=sql_funcs.make("date_diff", t, {"x": "a", "y": "b", "unit": "month"}))
    assert month.to_polars()["r"].to_list() == [429, 1, -2, 0]
    assert t.select(r=sql_funcs.make("date_diff", t, {"x": "a", "y": "b", "unit": "year"})).to_polars()["r"].to_list() == [35, 0, 0, 0]
    assert "INTERVAL" not in str(ibis.to_sql(month)).upper()  # 월 간격을 더하지 않는다(BigQuery TIMESTAMP_ADD는 MONTH를 못 한다)


# ---------- 2A21 대체 식이 원래 식과 같은 값 ----------

def test_first_last_arg_substitutes_match_original():
    t = ibis.duckdb.connect().create_table("t", ibis.memtable({"val": [10, 20, 30], "ord": [3, 1, 2]}))
    for id, raw in (("first", {"x": "val", "order": ["ord"]}), ("first", {"x": "val", "order": ["ord desc"]}), ("arg_min", {"x": "val", "by": "ord"}), ("arg_max", {"x": "val", "by": "ord"})):
        f = sql_funcs.FUNCS[id]
        original = t.aggregate(v=f["build"](sql_funcs.resolve(f, t, raw))).to_polars()["v"][0]
        for _, build in sql_funcs.SUBS[id]:
            assert t.aggregate(v=build(sql_funcs.resolve(f, t, raw))).to_polars()["v"][0] == original, (id, raw)


# ---------- 2A23 공백 있는 컬럼 이름과 연산자 낱말 ----------

def test_spaced_column_does_not_eat_operator_word():
    from core import parse
    t = ibis.memtable({"total in": [1, 2, 3], "total": [1, 2, 3], "x is": [1, 2, 3], "x": [1, None, 3]})
    count = lambda line: t.filter(parse.parse_condition(line, t)).count().to_pyarrow().as_py()
    assert count("total in 1, 2") == 2 and count("total = 2") == 1
    assert count("x is null") == 1  # 'x is'가 아니라 x
    assert count("x is >= 2") == 2 and count("total in = 2") == 1  # 이름 뒤에 연산자가 오면 공백 있는 이름


# ---------- 2A24 읽고-고치고-쓰기 잠금 ----------

def test_virtual_table_and_profiler_cache_saves_hold_the_settings_lock(folder, monkeypatch):
    import contextlib
    from core import profiler, settings, sql_exec
    held = []

    @contextlib.contextmanager
    def spy(name):
        held.append(name)
        yield
    monkeypatch.setattr(settings, "updating", spy)
    sql_exec.save_virtual("folder:c:/x", "v", {"sql": "SELECT 1"}, [], lambda vts: None)
    profiler.remember("k", 100, profile={})
    assert held == [sql_exec.VT_FILE, profiler.CACHE]


def test_virtual_tables_of_old_folder_key_follow_the_new_key(folder):
    from core import sql_exec
    sql_exec.save_virtual("folder:c:/x", "v", {"sql": "SELECT 1"}, [], lambda vts: None)  # 예전 열쇠(폴더만)
    new = "folder:c:/x#c:/x/a.duckdb"
    assert list(sql_exec.virtual(new)) == ["v"]  # 새 열쇠로도 보인다
    sql_exec.save_virtual(new, "w", {"sql": "SELECT 2"}, [], lambda vts: None)
    data = sql_exec.all_virtual()
    assert sorted(data[new]) == ["v", "w"] and "folder:c:/x" not in data  # 저장하면 새 열쇠로 옮긴다


# ---------- 2A25 주소의 비밀 매개변수 ----------

@pytest.mark.parametrize("piece", ["auth=abc", "credentials=x", "sig=1", "X-Amz-Signature=2", "sslpassword=x"])
def test_secret_param_catches_auth_credentials_signature(piece):
    from core import source
    assert source.secret_param(piece)


def test_secret_param_leaves_plain_options():
    from core import source
    assert not source.secret_param("sslmode=require") and not source.secret_param("connect_timeout=30")


# ---------- 2A12 목표 값 글자: 숫자 사이에는 단위가 있어야 ----------

@pytest.mark.parametrize("text", ["1.500.000", "2026.10.05", "1,2,3", "12,34", "1 00", "1,000 000"])
def test_target_value_rejects_dotted_or_loose_numbers(text):
    from core import targets
    with pytest.raises(ValueError):
        targets.parse_value(text)


@pytest.mark.parametrize("text, want", [("₩5,000,000", 5000000), ("1억5000만", 150000000), ("5000 만", 50000000), ("5 000", 5000), ("1.2억", 120000000), ("85%", 0.85)])
def test_target_value_still_reads_usual_forms(text, want):
    from core import targets
    assert targets.parse_value(text) == want
