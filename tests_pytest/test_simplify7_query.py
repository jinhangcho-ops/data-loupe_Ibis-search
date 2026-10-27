"""7차 /simplify G3(쿼리·지표·전처리·지도) 시험: 효율 고침이 옛 방식과 같은 결과를 내는지. 데이터는 시험 안에서 지어냈고 엔진(DuckDB·Polars)은 진짜를 쓴다."""
import datetime as dt
import types

import duckdb
import numpy as np
import polars as pl
import pyarrow as pa
import pytest
from flask import jsonify
from lazy import ibis

from core import server, use_counts
from geo import prep_geo
from metrics import metric_calc as mc
from metrics import metric_kernels as mk


def table_of(**cols):
    return ibis.duckdb.connect().create_table("t", ibis.memtable(pa.table(cols)))


# ---------- S-E1 / S-E2: 변화점·시간차를 Arrow에서 바로 numpy로 ----------

def old_concurrency(table, start, end):
    s = table.filter(table[start].notnull(), table[end].isnull() | (table[end] >= table[start]))
    opens = s.select(t=s[start].cast("timestamp"), d=ibis.literal(1))
    closes = s.filter(s[end].notnull()).select(t=s[end].cast("timestamp"), d=ibis.literal(-1))
    events = opens.union(closes)
    steps = mc.fetch_rows(events.group_by("t").aggregate(d=events.d.sum()).order_by("t"), "동시 접속이 바뀌는 시각")
    return np.array([r["t"] for r in steps], dtype="datetime64[us]"), np.cumsum([r["d"] for r in steps]).astype(np.int64)


def same_series(table, start="a", end="b"):
    old, new = old_concurrency(table, start, end), mc.concurrency_series(table, start, end)
    assert old[0].dtype == new[0].dtype and old[1].dtype == new[1].dtype
    assert np.array_equal(old[0], new[0]) and np.array_equal(old[1], new[1])
    return new


def test_concurrency_series_same_as_row_dicts_naive_empty_and_nulls():
    day = dt.datetime(2024, 1, 1)
    a = [day + dt.timedelta(minutes=i * 7, microseconds=i) for i in range(500)] + [None]
    b = [x + dt.timedelta(minutes=30) if i % 3 else None for i, x in enumerate(a[:-1])] + [day]
    times, counts = same_series(table_of(a=pa.array(a, pa.timestamp("us")), b=pa.array(b, pa.timestamp("us"))))
    assert len(times) > 100 and counts[-1] == len([1 for i in range(500) if i % 3 == 0])  # 끝이 빈 구간만 남는다
    same_series(table_of(a=pa.array([], pa.timestamp("us")), b=pa.array([], pa.timestamp("us"))))  # 빈 결과
    same_series(table_of(a=pa.array([None, None], pa.timestamp("us")), b=pa.array([None, None], pa.timestamp("us"))))  # 시작이 전부 빈 값
    same_series(table_of(a=pa.array(a, pa.timestamp("ns")), b=pa.array(b, pa.timestamp("ns"))))  # 나노초 컬럼
    same_series(table_of(a=pa.array(a, pa.timestamp("s")), b=pa.array(b, pa.timestamp("s"))))  # 초 컬럼


def test_concurrency_series_same_with_time_zone_columns():
    utc = dt.timezone.utc
    a = [dt.datetime(2024, 3, 1, 9, 0, tzinfo=utc) + dt.timedelta(minutes=i) for i in range(50)]
    b = [x + dt.timedelta(minutes=20) for x in a]
    same_series(table_of(a=pa.array(a, pa.timestamp("us", tz="UTC")), b=pa.array(b, pa.timestamp("us", tz="Asia/Seoul"))))


def test_concurrency_series_same_for_date_and_string_columns():
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(30)]
    same_series(table_of(a=pa.array(days, pa.date32()), b=pa.array([d + dt.timedelta(days=3) for d in days], pa.date32())))
    same_series(table_of(a=[d.isoformat() for d in days], b=[(d + dt.timedelta(days=3)).isoformat() for d in days]))


def test_fetch_table_refuses_too_many_rows_with_the_same_words(monkeypatch):
    t = table_of(x=list(range(10)))
    monkeypatch.setattr(mc, "MAX_FETCH_ROWS", 9)
    with pytest.raises(ValueError) as new:
        mc.fetch_table(t, "동시 접속이 바뀌는 시각")
    with pytest.raises(ValueError) as old:
        mc.fetch_rows(t, "동시 접속이 바뀌는 시각")
    assert str(new.value) == str(old.value)
    monkeypatch.setattr(mc, "MAX_FETCH_ROWS", 10)
    assert mc.fetch_table(t, "x").num_rows == 10


def old_seconds(table, user, time, event, first, second):
    kind = table[event].type()
    a, b = mc.step_value(kind, first), mc.step_value(kind, second)
    base = table.select(u=table[user], e=table[event], t=table[time].cast("timestamp"))
    base = base.filter(base.u.notnull(), base.t.notnull())
    starts = base.filter(base.e == a).group_by("u").aggregate(ta=base.t.min())
    joined = base.filter(base.e == b).select("u", "t").join(starts, "u").filter(lambda j: j.t >= j.ta)
    done = joined.group_by("u").aggregate(ta=joined.ta.min(), tb=joined.t.min())
    gaps = done.select(sec=mc.epoch(done.tb) - mc.epoch(done.ta))
    return np.array([r["sec"] for r in mc.fetch_rows(gaps, "사용자")], dtype=float), int(mc.rows(starts.aggregate(n=starts.u.count()))[0]["n"])


def test_seconds_between_same_as_row_dicts():
    base = dt.datetime(2024, 5, 1)
    users, events, times = [], [], []
    for u in range(200):
        users += [u, u, u]
        events += ["a", "b", "b"]
        times += [base + dt.timedelta(seconds=u * 10), base + dt.timedelta(seconds=u * 10 + 5 + u % 7), base + dt.timedelta(seconds=u * 10 + 1000)]
    users += [None, 999]
    events += ["a", "a"]
    times += [base, base]  # 아직 둘째를 안 한 사용자와 사용자가 빈 행
    t = table_of(u=pa.array(users, pa.int64()), e=events, t=pa.array(times, pa.timestamp("us")))
    old, new = old_seconds(t, "u", "t", "e", "a", "b"), mc.seconds_between(t, "u", "t", "e", "a", "b")
    assert old[0].dtype == new[0].dtype and np.array_equal(old[0], new[0]) and old[1] == new[1] and len(new[0]) == 200
    empty = t.filter(t.u > 10_000)
    old, new = old_seconds(empty, "u", "t", "e", "a", "b"), mc.seconds_between(empty, "u", "t", "e", "a", "b")
    assert old[0].dtype == new[0].dtype and len(new[0]) == 0 and old[1] == new[1] == 0


# ---------- S-A6: latest ----------

def test_latest_is_int_max_or_none():
    t = table_of(p=pa.array([3, 9, None], pa.int64()))
    assert mc.latest(t, "p") == 9 and type(mc.latest(t, "p")) is int
    assert mc.latest(t.filter(t.p > 100), "p") is None
    assert mc.latest(t.filter(t.p.isnull()), "p") is None


# ---------- S-S10: 표 자르기 알림·고유 사용자 ----------

def test_cut_rows_notes_keep_their_words():
    ctx = types.SimpleNamespace(notes=[])
    rows = list(range(mk.MAX_ROWS + 5))
    assert mk.last_rows(rows, ctx, "코호트") == rows[-mk.MAX_ROWS:] and mk.top_rows(rows, ctx, "페이지") == rows[:mk.MAX_ROWS]
    mk.last_rows(rows, ctx)
    assert ctx.notes == [f"코호트이 많아 최근 {mk.MAX_ROWS}개만 표에 실었어요(대표 값은 전체 기준).",
                         f"페이지이 많아 앞의 {mk.MAX_ROWS}개만 표에 실었어요(대표 값은 전체 기준).",
                         f"기간이 많아 최근 {mk.MAX_ROWS}개만 표에 실었어요(대표 값은 전체 기준)."]


def test_users_by_unit_note_and_unique_users():
    day = dt.date(2020, 1, 1)
    days = [day + dt.timedelta(days=i) for i in range(mk.MAX_ROWS + 3)]
    t = table_of(u=pa.array(list(range(len(days))), pa.int64()), d=pa.array(days, pa.date32()))
    ctx = types.SimpleNamespace(notes=[])
    rows, cells, found = mk.users_by_unit(t, {"user": "u", "time": "d"}, "day", ctx)
    assert len(rows) == mk.MAX_ROWS and cells == len(days) == len(found)
    assert ctx.notes == [f"기간이 많아 최근 {mk.MAX_ROWS}개만 표에 실었어요(대표 값은 전체 기준)."]
    assert mk.unique_users(mk.with_user(t, {"user": "u"}), {"user": "u"}) == len(days)


def test_event_mask_takes_only_table_and_roles():
    t = table_of(e=["a", "b", "a"], v=[1, 2, 3])
    s = {"event": "e", "event_value": "a"}
    assert mk.only_event(t, s).count().execute() == 2
    assert mk.event_mask(t, {}) is None and mk.only_event(t, {"event": "e", "event_value": ""}).count().execute() == 3


# ---------- S-E10: 좌표 쌍 → 값 고르기 ----------

def old_chosen(values, index):
    got = values.to_list()
    return pl.Series(values.name, [None if i < 0 else got[i] for i in index.tolist()], dtype=values.dtype)


@pytest.mark.parametrize("values", [pl.Series("강", ["가", "나", None, "라"]), pl.Series("반", [1.5, None, float("nan")]), pl.Series("번", [7, 8, 9], dtype=pl.Int64),
                                    pl.Series("빈", [], dtype=pl.String), pl.Series("날", [dt.date(2024, 1, 2), dt.date(2024, 5, 6)])])
def test_chosen_same_as_python_list(values):
    n = len(values)
    for index in (np.array([-1, -1, -1], dtype="int64"), np.array([], dtype="int64"), np.array([-1, 0, n - 1, -1, 0] if n else [-1, -1], dtype="int64")):
        old, new = old_chosen(values, index), prep_geo.chosen(values, index)
        assert new.name == old.name and new.dtype == old.dtype and new.equals(old, null_equal=True)


def test_chosen_big_and_region_number_column():
    values = pl.Series("v", [f"r{i}" for i in range(50)])
    index = np.random.default_rng(1).integers(-1, 50, 100_000)
    assert prep_geo.chosen(values, index).equals(old_chosen(values, index), null_equal=True)
    number = pl.select(pl.when(pl.lit(pl.Series(index)) >= 0).then(pl.lit(pl.Series(index)))).to_series().rename("").cast(pl.Int64)
    old = pl.Series([None if i < 0 else int(i) for i in index.tolist()], dtype=pl.Int64)
    assert number.name == old.name and number.dtype == old.dtype and number.equals(old, null_equal=True)
    nothing = np.array([], dtype="int64")
    assert pl.select(pl.when(pl.lit(pl.Series(nothing)) >= 0).then(pl.lit(pl.Series(nothing)))).to_series().rename("").cast(pl.Int64).dtype == pl.Int64


# ---------- S-E6: 연결을 바꾸면 표본을 버린다 ----------

def test_samples_cleared_on_switch_and_registered_once():
    from prep import prep_app
    assert server.ON_SWITCH.count(prep_app.SAMPLES.clear) == 1
    prep_app.SAMPLES["k"] = (1, object(), {})
    for hook in server.ON_SWITCH:
        if hook == prep_app.SAMPLES.clear:
            hook()
    assert prep_app.SAMPLES == {}


# ---------- S-E3b: json_reply로 바꾼 응답이 옛 응답과 같은지 ----------

def old_reply(df, text_big=True, **fields):
    return jsonify(rows=server.to_json(df, text_big), **fields)


@pytest.fixture
def local(folder, tmp_path):
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "shop.duckdb"))
    raw.execute("create table orders as select i as id, i % 5 as grp, 'n' || i as name, i * 1.5 as amt, "
                "date '2024-01-01' + i::int as day, 9007199254740993 + i as big from range(23) t(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "shop.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def both(client, monkeypatch, api, **body):
    new, reply = client.post(api, json=body), server.json_reply
    monkeypatch.setattr(server, "json_reply", old_reply)
    old = client.post(api, json=body)
    monkeypatch.setattr(server, "json_reply", reply)
    assert new.status_code == old.status_code == 200
    return new.get_json(), old.get_json()


def test_query_pivot_and_sql_run_replies_same_as_before(local, monkeypatch):
    new, old = both(local, monkeypatch, "/api/query", base="orders", limit=10, offset=0, fresh=True)
    assert new == old and set(new) >= {"count", "rows", "held", "server", "note"} and len(new["rows"]["rows"]) == 10
    new, old = both(local, monkeypatch, "/api/sql/run", sql="select * from orders order by id", limit=10, offset=0)
    assert new == old and set(new) >= {"rows", "more", "limit", "offset", "count", "held", "server", "note"} and len(new["rows"]["rows"]) == 23
    new, old = both(local, monkeypatch, "/api/pivot", base="orders", pivot={"rows": ["grp"], "values": [{"col": "amt", "agg": "sum"}]})
    assert new == old and set(new) == {"rows", "more"}


def test_prep_head_and_preview_replies_same_as_before(local, monkeypatch):
    new, old = both(local, monkeypatch, "/api/head", base="orders")
    assert new == old and set(new) == {"rows"} and new["rows"]["columns"]
    new, old = both(local, monkeypatch, "/api/preview", base="orders")
    assert new == old and {"rows", "types", "before", "after", "sources", "derived", "info", "local"} <= set(new)


# ---------- S-E9b: 가상 테이블 사용 횟수 ----------

def test_count_virtual_writes_same_counts_as_use_one_by_one(folder, monkeypatch):
    from query import search_app
    monkeypatch.setattr(server, "virtual_tables", lambda: {"v1": "select 1", "v2": "select 2", "v3": "select 3"})
    monkeypatch.setattr(server, "connection_key", lambda: "conn")
    search_app.count_virtual(["v1", "v2", "x", "v1"])
    after = {k: v["count"] for k, v in use_counts.load()["virtual_tables"].items()}
    assert after == {"conn|v1": 1, "conn|v2": 1}
    search_app.count_virtual([])
    assert {k: v["count"] for k, v in use_counts.load()["virtual_tables"].items()} == after


# ---------- 삭제·합치기 확인 ----------

def test_removed_things_are_gone_and_shared_ones_used():
    from query import flow, flow_app, search_app, sentence_run
    assert not hasattr(flow, "JOIN_HOWS") and not hasattr(flow, "STAGES") and not hasattr(search_app, "RESERVED")
    assert "/api/my_metrics/legacy" not in server.FREE and not [r for r in flow_app.app.url_map.iter_rules() if r.rule == "/api/my_metrics/legacy"]
    assert search_app.image_stem("con") == "_con" and search_app.image_stem('a/b:c*"d') == "a_b_c__d" and search_app.image_stem(None) == "chart"
    assert sentence_run.unit_line.__code__.co_argcount == 1 and search_app.llm_view.__code__.co_argcount == 0


def test_scope_of_fills_key_and_label_like_with_scope(folder):
    from query import flow_app
    for data in ({}, {"scope": None}, {"scope": {"table": "t"}}, {"scope": {"key": "k", "label": "x"}}):
        assert flow_app.scope_of(data) == flow_app.with_scope({"scope": data.get("scope")})["scope"]

def test_my_metrics_now_text_is_dash_stores():
    from core import dash_store
    from metrics import my_metrics
    assert not hasattr(my_metrics, "now_text") and not hasattr(my_metrics, "dt")
    assert dt.datetime.fromisoformat(dash_store.now_text()).microsecond == 0
