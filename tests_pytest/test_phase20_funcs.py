"""Phase 20 묶음 B(plans/phase-20.md): date_zone(시간대 있는 시각의 날짜 함수), week_help(week·weekday 도움말), moments(큰 수에서의 대체 식)."""
import datetime as dt

import polars as pl
import pytest
from conftest import spy_queries

from core import prep_ops, settings
from core import sql_funcs as S
from lazy import ibis

# 설정 시간대(Asia/Seoul)의 벽시계 시각: 차례로 UTC로는 전날 토요일, 전해 12월 31일, 일요일(앞 주), 업무시간 밖(01시)
WALLS = ("2026-01-04 00:30:15", "2026-01-01 01:00:00", "2026-01-05 00:30:00", "2026-01-05 10:00:00")
ZONED_CALLS = [("year", {}), ("quarter", {}), ("month", {}), ("week", {}), ("day", {}), ("hour", {}), ("minute", {}), ("second", {}), ("weekday", {}), ("weekday_num", {}),
               ("weekday_name", {}), ("is_weekend", {}), ("time_of_day", {}), ("work_hours", {}), ("month_phase", {}), ("is_month_end", {}), ("truncate", {"unit": "day"}),
               ("truncate", {"unit": "week"}), ("to_date", {}), ("to_time", {}), ("strftime", {"format": "%Y-%m-%d %H"}), ("time_bucket", {"n": 60})]
BIG = (1000000000.0, 1000000001.0, 1000000002.0)
MOMENT_IDS = ("var", "var_pop", "std", "std_pop", "cov", "cov_pop", "corr")


@pytest.fixture
def seoul(folder):
    settings.save({"timezone": "Asia/Seoul", "week_start": "sun"})


@pytest.fixture
def times(seoul):
    """z = 시간대 있는 시각, w = 같은 순간의 서울 벽시계 시각(시간대 없음)."""
    con = ibis.duckdb.connect()
    con.raw_sql("create table t (i integer, z timestamptz, w timestamp)")
    for i, wall in enumerate(WALLS):
        con.con.execute("insert into t values (?, cast(? as timestamptz), cast(? as timestamp))", [i, wall + "+09", wall])
    return con.table("t")


def values(t, id, raw):
    return t.order_by("i").select(v=S.make(id, t, raw)).to_polars()["v"].to_list()


# ---------- date_zone ----------

@pytest.mark.parametrize("id,opts", ZONED_CALLS, ids=[f"{i}{'_' + o['unit'] if 'unit' in o else ''}" for i, o in ZONED_CALLS])
def test_date_functions_use_the_setting_timezone(times, id, opts):
    assert values(times, id, {"x": "z", **opts}) == values(times, id, {"x": "w", **opts})


def test_date_zone_spec_values(times):
    first = lambda id, col, **opts: values(times, id, {"x": col, **opts})[0]
    for col in ("z", "w"):  # 시간대 없는 컬럼(w)은 고치기 전과 같은 값
        assert [first(i, col) for i in ("hour", "day", "weekday", "weekday_num", "week")] == [0, 4, 6, 1, 1]
        assert first("truncate", col, unit="day") == dt.datetime(2026, 1, 4) and first("to_date", col) == dt.date(2026, 1, 4)
        assert first("time_bucket", col, n=60) == dt.datetime(2026, 1, 4)
    assert times.schema()["w"] == S.resolve(S.get("hour"), times, {"x": "w"}).x.type()


def test_time_differences_keep_the_instant(times):
    """시각의 차이를 보는 함수는 벽시계로 바꾸지 않는다."""
    assert values(times, "epoch", {"x": "z"})[0] == int(dt.datetime(2026, 1, 3, 15, 30, 15, tzinfo=dt.timezone.utc).timestamp())
    for id in ("epoch", "date_diff", "date_add", "date_sub", "diff_now", "diff_today", "since_prev", "until_next"):
        assert S.resolve(S.get(id), times, {"x": "z", **({"y": "z"} if id == "date_diff" else {})}).x.type().timezone


# ---------- week_help ----------

def test_week_help_names_the_functions_that_follow_the_setting():
    for id in ("week", "weekday"):
        text = S.get(id)["help"]
        assert "설정의 주 시작 요일과 상관없이" in text and "truncate" in text and "weekday_num" in text


# ---------- moments ----------

def sub_value(t, id, raw):
    f = S.get(id)
    return t.aggregate(v=S.SUBS[id][0][1](S.resolve(f, t, raw))).to_polars()["v"][0]


def original_value(t, id, raw):
    f = S.get(id)
    return t.aggregate(v=f["build"](S.resolve(f, t, raw))).to_polars()["v"][0]


def pair(id):
    return {"x": "a", "y": "b"} if id in ("cov", "cov_pop", "corr") else {"x": "a"}


def test_substitutes_are_right_on_big_numbers():
    t = ibis.duckdb.connect().create_table("t", ibis.memtable({"a": list(BIG), "b": [2 * v for v in BIG]}))
    want = {"var": 1.0, "var_pop": 2 / 3, "std": 1.0, "std_pop": (2 / 3) ** 0.5, "cov": 2.0, "cov_pop": 4 / 3, "corr": 1.0}
    for id in MOMENT_IDS:
        assert sub_value(t, id, pair(id)) == pytest.approx(want[id], rel=1e-9), id
        assert original_value(t, id, pair(id)) == pytest.approx(want[id], rel=1e-9), id


def test_substitutes_work_on_decimal_columns():
    con = ibis.duckdb.connect()
    con.raw_sql("create table t as select cast(v as decimal(18, 2)) as a, cast(v * 2 as decimal(18, 2)) as b from (values (1000000000), (1000000001), (1000000002)) x(v)")
    t = con.table("t")
    assert sub_value(t, "std", {"x": "a"}) == pytest.approx(1.0, rel=1e-9)
    assert sub_value(t, "cov", {"x": "a", "y": "b"}) == pytest.approx(2.0, rel=1e-9)


def test_substitutes_keep_small_values():
    t = ibis.duckdb.connect().create_table("t", ibis.memtable({"a": [1.0, 2.0, 4.0, 8.0, None], "b": [3.0, 1.0, None, 5.0, 2.0], "k": [1, 1, 1, 0, 1]}))
    for id in MOMENT_IDS:
        for raw in (pair(id), {**pair(id), "where": "k = 1"}):
            assert sub_value(t, id, raw) == pytest.approx(original_value(t, id, raw)), (id, raw)


def test_no_query_when_the_original_works_or_only_judging(monkeypatch):
    duck = ibis.duckdb.connect().create_table("t", ibis.memtable({"a": list(BIG), "b": list(BIG)}))
    found = spy_queries(monkeypatch)
    for id in MOMENT_IDS:  # 원래 식을 하는 연결: 조회가 나가지 않는다
        S.aggregate(id, duck, "a", opts={"y": "b"} if "y" in pair(id) else None)
    assert found == []
    assert S.judge(S.get("std"), duck, lambda expr: expr.op().find(ibis.expr.operations.StandardDev) == [])["status"] == "substitute"
    assert found == []  # 지원 판정은 식을 만들어만 본다


def test_one_extra_query_when_the_substitute_is_used(monkeypatch):
    t = ibis.polars.connect().create_table("t", pl.DataFrame({"a": list(BIG), "b": list(BIG)}))  # 파일 연결(Polars)은 공분산을 못 한다
    asked = []
    stats = prep_ops.stats
    monkeypatch.setattr(prep_ops, "stats", lambda table, **metrics: (asked.append(sorted(metrics)), stats(table, **metrics))[1])
    assert S.support(t, ["cov", "cov_pop"]) == {i: {"status": "substitute", "note": S.SUBS[i][0][0]} for i in ("cov", "cov_pop")} and asked == []
    for id, want in (("cov", 1.0), ("cov_pop", 2 / 3)):
        expr = S.aggregate(id, t, "a", opts={"y": "b"})
        assert asked == [["x", "y"]], id  # 평균을 구하는 조회 한 번
        assert t.aggregate(v=expr).to_polars()["v"][0] == pytest.approx(want, rel=1e-9)
        asked.clear()
    from core import querying
    assert querying.agg_unsupported({"series": [{"agg": "cov", "y": "a", "opts": {"y": "b"}}]}, t) is False and asked == []  # 되는지만 볼 때는 평균 조회를 보내지 않는다(요청 한 번에 한 번만)
