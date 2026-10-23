"""SQL 함수 사전(sql_funcs): 함수마다 손으로 계산한 값과 맞추고, 대표 값 고르기·소계·SQL 식 직접 쓰기·지원 판정을 시험한다.
데이터는 테스트 안에서 DuckDB 메모리 표로 짓는다. 값은 파이썬으로 따로 손계산한 것."""
import datetime as dt

import duckdb
import polars as pl
import pytest
from lazy import ibis

from core import sql_exec
from core import sql_funcs as S

# ev: 묶음 a(3행) b(4행) c(1행). val·other는 숫자, txt는 글자(빈 값 하나), flag는 참거짓, d는 하루씩 늘어나는 날짜.
EV = [(1, "a", 1.0, 2.0, "x", True), (2, "a", 2.0, 4.0, " y ", True), (3, "a", 3.0, 6.0, "x", False), (4, "b", 10.0, 1.0, "p-q", True),
      (5, "b", 20.0, 3.0, "p-q", True), (6, "b", 30.0, 5.0, "r", True), (7, "b", 40.0, 7.0, "s", True), (8, "c", 5.0, 9.0, None, False)]
NUMS = [(-2.6, 2, "12", 1.0), (0.0, 0, "3x", 10.0), (2.4, 5, "7", 100.0), (9.0, 3, None, 1000.0)]  # x y s z


@pytest.fixture(scope="module")
def con():
    raw = duckdb.connect()
    raw.execute("create table ev (id bigint, grp varchar, val double, other double, txt varchar, flag boolean, d date)")
    raw.executemany("insert into ev select ?, ?, ?, ?, ?, ?, date '2026-01-01' + ? * interval 1 day", [(*r, r[0] - 1) for r in EV])
    raw.execute("create table nums (x double, y bigint, s varchar, z double)")
    raw.executemany("insert into nums values (?, ?, ?, ?)", NUMS)
    raw.execute("create table misc (j json, st struct(k varchar, n bigint), a varchar[], ts timestamp, s varchar)")
    raw.execute("""insert into misc values ('{"a":{"b":5},"l":[7,8]}', {'k': 'v', 'n': 5}, ['p', 'q'], timestamp '2026-03-05 10:30:00', '2026-03-05')""")
    yield ibis.duckdb.from_connection(raw)
    raw.close()


@pytest.fixture
def ev(con):
    return con.table("ev")


def per_group(ev, id, column="val", **raw):
    """묶음(grp)마다 집계 값 {묶음: 값}."""
    agg = S.aggregate(id, ev, column, None, raw)
    frame = ev.group_by("grp").aggregate(v=agg).order_by("grp").to_polars()
    return dict(zip(frame["grp"], frame["v"]))


def column_of(t, id, **raw):
    """행마다 계산하는 함수의 값 목록(id 순서)."""
    return t.mutate(v=S.make(id, t, raw)).order_by("id" if "id" in t.columns else "x").to_polars()["v"].to_list()


def near(got, want, tol=1e-9):
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert (g is None and w is None) or (g is not None and w is not None and abs(g - w) < tol), (got, want)


# ---------- 집계 ----------

def test_basic_aggregates(ev):
    assert per_group(ev, "sum") == {"a": 6, "b": 100, "c": 5}
    assert per_group(ev, "mean") == {"a": 2, "b": 25, "c": 5}
    assert per_group(ev, "min") == {"a": 1, "b": 10, "c": 5} and per_group(ev, "max") == {"a": 3, "b": 40, "c": 5}
    assert per_group(ev, "count") == {"a": 3, "b": 4, "c": 1} and per_group(ev, "count", "txt") == {"a": 3, "b": 4, "c": 0}
    assert per_group(ev, "count_distinct", "txt") == {"a": 2, "b": 3, "c": 0} and per_group(ev, "nunique", "txt") == {"a": 2, "b": 3, "c": 0}  # nunique = 예전 이름
    assert per_group(ev, "approx_count_distinct", "txt") == {"a": 2, "b": 3, "c": 0}
    assert per_group(ev, "range") == {"a": 2, "b": 30, "c": 0}


def test_median_quantile(ev):
    assert per_group(ev, "median") == {"a": 2, "b": 25, "c": 5}
    assert per_group(ev, "quantile", p=0.5) == per_group(ev, "p50") == {"a": 2, "b": 25, "c": 5}
    near([per_group(ev, "p90")["b"], per_group(ev, "quantile", p=0.9)["b"]], [37.0, 37.0])  # 10,20,30,40의 90%: 30 + 0.7 × 10
    near([per_group(ev, "p95")["b"], per_group(ev, "p99")["b"]], [38.5, 39.7])
    with pytest.raises(ValueError, match="0~1"):
        S.aggregate("quantile", ev, "val", None, {"p": 1.5})


def test_mode_spread_and_pairs(ev):
    assert per_group(ev, "mode", "txt")["b"] == "p-q" and per_group(ev, "mode")["c"] == 5
    near(list(per_group(ev, "std").values())[:1], [1.0])  # 1,2,3의 표본 표준편차
    near([per_group(ev, "std_pop")["a"], per_group(ev, "var")["a"], per_group(ev, "var_pop")["a"]], [(2 / 3) ** 0.5, 1.0, 2 / 3])
    near([per_group(ev, "corr", y="other")["a"], per_group(ev, "corr", y="other")["b"]], [1.0, 1.0])  # 두 묶음 모두 other가 val의 일차식
    near([per_group(ev, "cov", y="other")["a"], per_group(ev, "cov_pop", y="other")["a"]], [2.0, 4 / 3])  # (−1·−2 + 0 + 1·2) / 2, / 3


def test_string_agg_collect(ev):
    assert per_group(ev, "string_agg", "txt", sep="|", order=["id"])["a"] == "x| y |x"
    assert per_group(ev, "string_agg", "txt", sep="|", distinct="yes")["a"] in ("x| y ", " y |x")  # 중복을 빼면 값 순
    assert per_group(ev, "string_agg", "grp", sep="-", order=["id desc"])["b"] == "b-b-b-b"
    assert sorted(per_group(ev, "collect", "txt")["a"]) == [" y ", "x", "x"] and sorted(per_group(ev, "collect", "txt", distinct="yes")["a"]) == [" y ", "x"]


def test_boolean_and_conditional_aggregates(ev):
    assert per_group(ev, "all", None, cond="flag = true") == {"a": False, "b": True, "c": False}
    assert per_group(ev, "any", None, cond="flag = true") == {"a": True, "b": True, "c": False}
    assert per_group(ev, "count_if", None, cond="val > 2") == {"a": 1, "b": 4, "c": 1}
    assert per_group(ev, "sum_if", "val", cond="flag = true") == {"a": 3, "b": 100, "c": None}
    near([per_group(ev, "mean_if", "val", cond="flag = true")["a"]], [1.5])
    assert S.aggregate("sum", ev, "val", "grp = b").execute() == 100  # 모든 집계의 조건(where)


def test_arg_first_last_any(ev):
    assert per_group(ev, "arg_max", "txt", by="val")["b"] == "s" and per_group(ev, "arg_min", "txt", by="val")["b"] == "p-q"
    assert per_group(ev, "first", "other", order=["val desc"])["b"] == 7 and per_group(ev, "last", "other", order=["val desc"])["b"] == 1
    assert per_group(ev, "first", "txt", order=["id"]) ["a"] == "x" and per_group(ev, "last", "txt", order=["id"])["a"] == "x"
    assert per_group(ev, "any_value")["b"] in (10, 20, 30, 40) and per_group(ev, "any_value")["c"] == 5
    got = per_group(ev, "unique_or_null")
    assert got["a"] is None and got["b"] is None and got["c"] == 5


# ---------- 창 함수 ----------

def windowed(ev, id, **raw):
    """묶음(grp)·정렬(id) 안의 창 함수 값을 id 순서로(그 함수에 없는 칸은 넣지 않는다)."""
    names = {a["name"] for a in S.FUNCS[id]["args"]}
    base = {k: v for k, v in {"order": ["id"], "group": ["grp"]}.items() if k in names}
    return ev.mutate(v=S.make(id, ev, {**base, **raw})).order_by("id").to_polars()["v"].to_list()


def test_rank_family(ev):
    assert windowed(ev, "row_number") == [1, 2, 3, 1, 2, 3, 4, 1]
    assert windowed(ev, "rank", order=["val desc"]) == [3, 2, 1, 4, 3, 2, 1, 1] and windowed(ev, "dense_rank", order=["val desc"]) == [3, 2, 1, 4, 3, 2, 1, 1]
    near(windowed(ev, "percent_rank")[3:7], [0, 1 / 3, 2 / 3, 1])
    near(windowed(ev, "cume_dist")[3:7], [0.25, 0.5, 0.75, 1.0])
    assert windowed(ev, "ntile", n=2)[3:7] == [1, 1, 2, 2]


def test_shift_and_value_windows(ev):
    assert windowed(ev, "lag", x="val") == [None, 1, 2, None, 10, 20, 30, None] and windowed(ev, "lead", x="val")[:3] == [2, 3, None]
    assert windowed(ev, "lag", x="val", default=0)[:3] == [0, 1, 2] and windowed(ev, "lag", x="val", n=2)[:3] == [None, None, 1]
    assert windowed(ev, "first_value", x="val")[3:7] == [10] * 4 and windowed(ev, "last_value", x="val")[3:7] == [40] * 4
    assert windowed(ev, "nth_value", x="val", n=2)[3:7] == [20] * 4
    near(windowed(ev, "diff", x="val")[3:7], [None, 10, 10, 10])
    near(windowed(ev, "pct_change", x="val")[3:7], [None, 1.0, 0.5, 1 / 3])


def test_cumulative_and_moving(ev):
    assert windowed(ev, "cum_sum", x="val")[3:7] == [10, 30, 60, 100] and windowed(ev, "cum_mean", x="val")[3:7] == [10, 15, 20, 25]
    assert windowed(ev, "cum_min", x="val")[3:7] == [10, 10, 10, 10] and windowed(ev, "cum_max", x="val")[3:7] == [10, 20, 30, 40]
    assert windowed(ev, "moving_mean", x="val", n=2)[3:7] == [10, 15, 25, 35] and windowed(ev, "moving_sum", x="val", n=2)[3:7] == [10, 30, 50, 70]
    assert windowed(ev, "moving_max", x="val", n=2)[3:7] == [10, 20, 30, 40] and windowed(ev, "moving_min", x="val", n=3)[3:7] == [10, 10, 10, 20]
    assert windowed(ev, "moving_sum", x="val", n=2, unit="days", order=["d"])[3:7] == [10, 30, 50, 70]  # 날짜가 하루씩이라 2행 = 2일
    with pytest.raises(ValueError, match="날짜"):
        windowed(ev, "moving_sum", x="val", n=2, unit="days")


def test_share_of_group(ev):
    near(windowed(ev, "share_of_group", x="val")[:4], [1 / 6, 2 / 6, 3 / 6, 0.1])


# ---------- 글자 ----------

@pytest.fixture
def words(con):
    return con.table("ev").filter(con.table("ev").id.isin([2, 4, 8]))  # " y ", "p-q", 빈 값


def test_text_functions(words):
    for id, raw, want in [("length", {}, [3, 3, None]), ("upper", {}, [" Y ", "P-Q", None]), ("lower", {}, [" y ", "p-q", None]), ("capitalize", {}, [" y ", "P-q", None]),
                          ("trim", {}, ["y", "p-q", None]), ("ltrim", {}, ["y ", "p-q", None]), ("rtrim", {}, [" y", "p-q", None]),
                          ("substr", {"start": 1, "length": 2}, [" y", "p-", None]), ("left", {"n": 1}, [" ", "p", None]), ("right", {"n": 1}, [" ", "q", None]),
                          ("replace", {"find": "-", "to": "+"}, [" y ", "p+q", None]), ("regex_contains", {"pattern": "^p"}, [False, True, None]),
                          ("regex_extract", {"pattern": "(\\w)-(\\w)", "group_no": 2}, [None, "q", None]), ("regex_replace", {"pattern": "[-]", "to": "_"}, [" y ", "p_q", None]),
                          ("split_part", {"sep": "-", "n": 2}, [None, "q", None]), ("lpad", {"width": 5, "fill": "0"}, ["00 y ", "00p-q", None]), ("rpad", {"width": 4, "fill": "*"}, [" y *", "p-q*", None]),
                          ("contains", {"find": "-"}, [False, True, None]), ("startswith", {"find": "p"}, [False, True, None]), ("endswith", {"find": "q"}, [False, True, None]),
                          ("find", {"find": "q"}, [0, 3, None]), ("reverse", {}, [" y ", "q-p", None]), ("repeat", {"n": 2}, [" y  y ", "p-qp-q", None])]:
        assert column_of(words, id, x="txt", **raw) == want, id  # 셋째 행은 빈 값이라 결과도 빈 값
    assert column_of(words, "split", x="txt", sep="-")[1] == ["p", "q"]
    assert column_of(words, "concat", columns=["grp", "txt"], sep="/")[1] == "b/p-q"
    assert column_of(words, "concat", columns=["grp", "id"])[1] == "b4"  # 숫자는 글자로 바뀐다


# ---------- 숫자 ----------

def test_number_functions(con):
    t = con.table("nums").mutate(id=ibis.row_number().over(ibis.window(order_by="x")))  # x 순: (−2.6, 2) (0, 0) (2.4, 5) (9, 3)
    got = lambda id, **raw: [None if v is None else round(v, 6) for v in column_of(t, id, **raw)]
    assert got("round", x="x", digits=0) == [-3, 0, 2, 9] and got("ceil", x="x") == [-2, 0, 3, 9] and got("floor", x="x") == [-3, 0, 2, 9]
    assert got("abs", x="x") == [2.6, 0, 2.4, 9] and got("sign", x="x") == [-1, 0, 1, 1] and got("clip", x="x", lower=0, upper=5) == [0, 0, 2.4, 5]
    assert got("mod", x="y", by=2) == [0, 0, 1, 1] and got("power", x="y", n=2) == [4, 0, 25, 9] and got("sqrt", x="y")[:2] == [round(2 ** 0.5, 6), 0]
    assert got("safe_divide", x="x", y="y") == [-1.3, None, 0.48, 3.0]  # 나누는 값이 0이면 빈 값
    assert got("log10", x="z") == [0, 1, 2, 3] and got("log", x="z", base=10) == [0, 1, 2, 3] and got("exp", x="z")[0] == round(2.718281828459045, 6)
    assert got("ln", x="z")[1] == round(2.302585092994046, 6) and got("sqrt", x="z")[2] == 10
    assert column_of(t, "bucket", x="y", bounds=[1, 4]) == ["1~4", "~1", "4~", "1~4"]  # 4 미만이 가운데, 1 미만이 앞
    assert column_of(t, "bucket", x="y", bounds=[1, 4], labels=["낮음", "중간", "높음"]) == ["중간", "낮음", "높음", "중간"]
    with pytest.raises(ValueError, match="라벨은 경계보다 하나 많아야"):
        column_of(t, "bucket", x="y", bounds=[1, 4], labels=["a"])
    with pytest.raises(ValueError, match="작은 수부터"):
        column_of(t, "bucket", x="y", bounds=[4, 1])


# ---------- 날짜 ----------

def test_date_functions(ev):
    first = ev.filter(ev.id == 1)
    get = lambda id, **raw: first.mutate(v=S.make(id, first, {"x": "d", **raw})).to_polars()["v"][0]
    assert get("truncate", unit="month") == dt.date(2026, 1, 1) and get("year") == 2026 and get("quarter") == 1 and get("month") == 1 and get("day") == 1
    assert get("weekday") == 3 and get("week") == 1 and get("epoch") == 1767225600  # 2026-01-01은 목요일(월요일 0), 1주차
    assert get("date_add", n=7, unit="day") == dt.date(2026, 1, 8) and get("date_sub", n=1, unit="month") == dt.date(2025, 12, 1)
    assert get("strftime", format="%Y/%m") == "2026/01" and get("to_date") == dt.date(2026, 1, 1)
    assert get("diff_today", unit="day") > 0 and get("diff_now", unit="day") > 0
    pair = ev.filter(ev.id == 1).mutate(e=ibis.date(2026, 1, 8).cast("date"))
    assert pair.mutate(v=S.make("date_diff", pair, {"x": "d", "y": "e", "unit": "day"})).to_polars()["v"][0] == 7
    assert pair.mutate(v=S.make("date_diff", pair, {"x": "d", "y": "e", "unit": "hour"})).to_polars()["v"][0] == 168


def test_timestamp_functions(con):
    m = con.table("misc")
    get = lambda id, **raw: m.select(v=S.make(id, m, raw)).to_polars()["v"][0]
    assert get("hour", x="ts") == 10 and get("minute", x="ts") == 30 and get("second", x="ts") == 0
    assert get("to_time", x="ts") == dt.time(10, 30) and get("to_date", x="ts") == dt.date(2026, 3, 5)
    assert get("parse_date", x="s", format="%Y-%m-%d") == dt.datetime(2026, 3, 5) and get("truncate", x="ts", unit="hour") == dt.datetime(2026, 3, 5, 10)


# ---------- 조건·형 변환·구조 ----------

def test_condition_functions(ev):
    ids = lambda id, **raw: ev.mutate(v=S.make(id, ev, raw)).order_by("id").to_polars()["v"].to_list()
    assert ids("case", cases=[{"when": "val >= 20", "then": "고액"}, {"when": "val >= 5", "then": "중간"}], otherwise="소액") == ["소액"] * 3 + ["중간", "고액", "고액", "고액", "중간"]
    assert ids("coalesce", columns=["txt", "grp"])[7] == "c" and ids("nullif", x="grp", value="a")[:3] == [None] * 3 and ids("nullif", x="grp", value="a")[3] == "b"
    assert ids("ifelse", cond="flag = true", then=1, otherwise=0) == [1, 1, 0, 1, 1, 1, 1, 0]
    assert ids("greatest", columns=["val", "other"])[:4] == [2, 4, 6, 10] and ids("least", columns=["val", "other"])[:4] == [1, 2, 3, 1]
    assert ids("isin", x="grp", members=["a", "c"]) == [True] * 3 + [False] * 4 + [True] and ids("between", x="val", low=2, high=10)[:4] == [False, True, True, True]
    assert ids("isnull", x="txt")[7] is True and ids("notnull", x="txt")[0] is True
    with pytest.raises(ValueError, match="아니면|값"):
        S.make("case", ev, {"cases": []})


def test_cast_and_try_cast(con):
    t = con.table("nums").mutate(id=ibis.row_number().over(ibis.window(order_by="x")))
    ids = lambda id, **raw: sorted(column_of(t, id, **raw), key=lambda v: (v is None, v))
    assert ids("try_cast", x="s", to="int") == [7, 12, None, None] and ids("cast", x="y", to="string") == ["0", "2", "3", "5"]
    assert ids("cast", x="y", to="float") == [0.0, 2.0, 3.0, 5.0]
    with pytest.raises(Exception, match="3x|Conversion|convert|cast"):
        column_of(t, "cast", x="s", to="int")


def test_structure_functions(con):
    m = con.table("misc")
    get = lambda id, **raw: m.select(v=S.make(id, m, raw)).to_polars()["v"][0]
    assert get("json_get", x="j", path="a.b", to="int") == 5 and get("json_get", x="j", path="l[1]", to="int") == 8
    assert get("struct_field", x="st", field="k") == "v" and get("array_length", x="a") == 2 and get("array_get", x="a", n=2) == "q"
    with pytest.raises(ValueError, match="필드가 없습니다"):
        S.make("struct_field", m, {"x": "st", "field": "zz"})
    assert S.apply("unnest", m, {"x": "a"}).count().execute() == 2


# ---------- 묶음 ----------

def totals(table, key="grp"):
    frame = table.to_polars()
    return {row[key]: row for row in frame.iter_rows(named=True)}


def test_having_qualify_distinct(ev):
    agg = ev.group_by("grp").aggregate(total=ev.val.sum())
    assert sorted(S.apply("having", agg, {"cond": "total > 5"}).to_polars()["grp"]) == ["a", "b"]
    top = S.apply("qualify", ev, {"func": "rank", "func_args": {"order": ["val desc"], "group": ["grp"]}, "op": "<=", "value": 2})
    assert sorted(top.to_polars()["id"]) == [2, 3, 6, 7, 8]  # 묶음마다 값이 큰 2행
    assert top.columns == ev.columns
    assert S.apply("distinct", ev, {"columns": ["grp"]}).count().execute() == 3 and S.apply("distinct", ev, {}).count().execute() == 8
    with pytest.raises(ValueError, match="창 함수"):
        S.apply("qualify", ev, {"func": "sum", "op": "<=", "value": 2})


def test_rollup_cube_grouping_sets(ev):
    aggs = [{"agg": "sum", "y": "val", "name": "total"}]
    rolled = totals(S.apply("rollup", ev, {"columns": ["grp"], "aggs": aggs}))
    assert {k: v["total"] for k, v in rolled.items()} == {"a": 6, "b": 100, "c": 5, S.TOTAL: 111}
    two = S.apply("rollup", ev, {"columns": ["grp", "flag"], "aggs": aggs}).to_polars()
    assert two.height == 8 and two.filter(two["grp"] == S.TOTAL)["total"].to_list() == [111]  # 4 + 소계 3 + 총계 1
    sub = two.filter(two["flag"] == S.SUBTOTAL).sort("grp")
    assert sub["grp"].to_list() == ["a", "b", "c"] and sub["total"].to_list() == [6, 100, 5]
    cube = S.apply("cube", ev, {"columns": ["grp", "flag"], "aggs": aggs}).to_polars()
    assert cube.height == 10 and cube.filter((cube["grp"] == S.SUBTOTAL) & (cube["flag"] == "false"))["total"].to_list() == [8]  # flag가 거짓인 행: 3 + 5
    sets = S.apply("grouping_sets", ev, {"columns": ["grp", "flag"], "aggs": aggs, "sets": [["grp"], []]}).to_polars()
    assert sets.height == 4 and sets.filter(sets["grp"] == S.TOTAL)["total"].to_list() == [111]
    with pytest.raises(ValueError, match="묶음 기준"):
        S.apply("grouping_sets", ev, {"columns": ["grp"], "aggs": aggs, "sets": [["nope"]]})


# ---------- 대표 값 고르기 ----------

def test_representative_choices(ev):
    def pick(how, column="val", **kw):
        frame = ev.group_by("grp").aggregate(v=S.pick(how, ev, column, **kw)).order_by("grp").to_polars()
        return dict(zip(frame["grp"], frame["v"]))
    assert pick("min") == {"a": 1, "b": 10, "c": 5} and pick("max") == {"a": 3, "b": 40, "c": 5}
    assert pick("any_value")["c"] == 5 and pick("mode", "txt")["b"] == "p-q"
    assert pick("first", "val", order=["id desc"]) == {"a": 3, "b": 40, "c": 5} and pick("last", "val", order=["id desc"]) == {"a": 1, "b": 10, "c": 5}
    assert pick("string_agg", "val", order=["id"], sep="|") == {"a": "1.0|2.0|3.0", "b": "10.0|20.0|30.0|40.0", "c": "5.0"}
    assert pick("warn") == {"a": None, "b": None, "c": 5}  # 값이 둘 이상이면 빈 값
    with pytest.raises(ValueError, match="정렬 기준"):
        S.pick("first", ev, "val")
    with pytest.raises(ValueError, match="중 하나"):
        S.pick("median", ev, "val")


def test_conflicts_counts_groups_with_many_values(ev):
    assert S.conflicts(ev, ["grp"], ["txt"]).execute() == 2  # a: x·y, b: p-q·r·s(둘 이상), c: 빈 값뿐
    assert S.conflicts(ev, ["grp"], ["val"]).execute() == 2  # c만 값이 하나
    assert S.conflicts(ev, ["id"], ["val", "txt"]).execute() == 0  # 기준이 모두 다르면 겹침 없음
    assert S.conflicts(ev, ["flag"], ["grp"]).execute() == 2


# ---------- SQL 식 직접 쓰기 ----------

def test_raw_sql_expression(ev):
    got = S.raw_expr(ev, "twice", "val * 2").order_by("id").to_polars()
    assert got["twice"].to_list()[:3] == [2, 4, 6] and got.columns == [*ev.columns, "twice"]
    assert S.raw_expr(ev, "up", "upper(grp) || '!'").to_polars()["up"][0] == "A!"
    for text, exc in [("1) AS u, (2", ValueError), ("(SELECT * FROM read_csv('x.csv'))", sql_exec.SecurityViolationError),
                      ("getenv('PATH')", sql_exec.SecurityViolationError), ("val); DROP TABLE ev; --", ValueError)]:
        with pytest.raises(exc):
            S.raw_expr(ev, "bad", text)
    with pytest.raises(ValueError, match="이미 있습니다"):
        S.raw_expr(ev, "val", "1")
    with pytest.raises(ValueError, match="새 컬럼 이름"):
        S.raw_expr(ev, "a b", "1")
    with pytest.raises(ValueError, match="입력하세요"):
        S.raw_expr(ev, "z", " ")
    files = ibis.polars.connect().create_table("p", pl.DataFrame({"a": [1]}))
    with pytest.raises(S.NeedsLocal):
        S.raw_expr(files, "z", "a + 1")
    with pytest.raises(sql_exec.SecurityViolationError):  # 파일 연결도 PC 사본에 보내기 전에 검사한다
        S.raw_expr(files, "z", "getenv('PATH')")
    from core.source import compiles
    placeholder = S.local_only(files, "z")
    assert list(placeholder.columns) == ["a", "z"] and not compiles(placeholder)  # 계획으로 못 바뀌어야 PC 사본으로 간다


# ---------- 인자 검사와 사전 ----------

def test_argument_errors(ev):
    with pytest.raises(ValueError, match="숫자 컬럼"):
        S.make("sum", ev, {"x": "txt"})
    with pytest.raises(ValueError, match="모르는 항목"):
        S.make("sum", ev, {"x": "val", "zzz": 1})
    with pytest.raises(ValueError, match="정렬 기준"):
        S.make("rank", ev, {})
    with pytest.raises(ValueError, match="컬럼들"):
        S.make("concat", ev, {"columns": []})
    with pytest.raises(ValueError, match="비슷|함수가 없"):
        S.get("medain")
    with pytest.raises(ValueError, match="표를 바꾸는"):
        S.make("having", ev, {"cond": "val > 1"})
    with pytest.raises(ValueError, match="집계 함수가 아닙니다"):
        S.aggregate("rank", ev, "val")


def test_catalog_shape():
    cat = S.catalog()
    assert [c["id"] for c in cat["categories"]] == [c for c, _ in S.CATEGORIES]
    ids = [f["id"] for f in cat["funcs"]]
    assert len(ids) == len(set(ids)) and "build" not in cat["funcs"][0]
    for f in cat["funcs"]:
        assert f["label"] and f["help"] and f["example"] and f["category"] in dict(S.CATEGORIES), f["id"]
        for a in f["args"]:
            assert {"name", "kind", "label"} <= set(a), (f["id"], a)
            assert a["kind"] in ("column", "columns", "number", "int", "text", "choice", "cond", "order", "list", "cases", "value", "dict"), (f["id"], a)
    import json
    json.dumps(cat)  # 화면으로 보낼 수 있는 모양
    assert cat["aliases"] == {"nunique": "count_distinct"} and set(cat["representative"]) == set(S.REPRESENTATIVE)


# ---------- 지원 판정 ----------

def test_support_on_duckdb_is_ok(con):
    t = con.table("misc")
    assert S.support(t)["json_get"]["status"] == "ok"
    found = S.support(con.table("ev"))
    assert found["median"]["status"] == "ok" and found["row_number"]["status"] == "ok" and found["lpad"]["status"] == "ok"
    assert S.support(con.table("ev"), ["sum", "string_agg"]) == {"sum": {"status": "ok", "note": ""}, "string_agg": {"status": "ok", "note": ""}}


def test_support_unavailable_without_fitting_column(con):
    bare = con.table("nums").select("x")
    got = S.support(bare, ["upper", "sum", "json_get"])
    assert got["sum"]["status"] == "ok" and got["upper"]["status"] == "unavailable" and "글자" in got["upper"]["note"] and got["json_get"]["status"] == "unavailable"


def test_window_functions_fall_back_to_local_on_files():
    t = ibis.polars.connect().create_table("w", pl.DataFrame({"g": ["a", "b"], "v": [1.0, 2.0], "d": [dt.date(2026, 1, 1), dt.date(2026, 1, 2)]}))
    got = S.support(t, ["rank", "sum", "lag"])
    assert got["rank"]["status"] == "local" and "PC" in got["rank"]["note"] and got["sum"]["status"] == "ok"


@pytest.mark.parametrize("dialect", ["mysql", "mssql", "sqlite", "oracle"])
def test_dialect_substitutes_keep_the_meaning(dialect):
    """원래 식을 못 만드는 방언에서도 같은 결과의 대체 식이 있는 것들은 대체로, 없는 것은 PC 사본으로."""
    statuses = {i: S.dialect_status(i, dialect) for i in ("any_value", "var", "corr", "median", "sum")}
    assert statuses["sum"]["status"] == "ok"
    for i, s in statuses.items():
        assert s["status"] in ("ok", "substitute", "local")
        assert s["status"] != "substitute" or (S.SUBS[i] and s["note"])
    # 대체 식이 모델이 되는 방언 하나는 있어야 한다(MySQL: any_value → 최솟값)
    if dialect == "mysql":
        assert statuses["any_value"]["status"] == "substitute" and "최솟값" in statuses["any_value"]["note"]


def test_substitute_values_match_the_original(ev):
    """대체 식(평균 제곱 − 제곱 평균 …)이 원래 식과 같은 값을 준다."""
    for id, raw in [("var_pop", {"x": "val"}), ("var", {"x": "val"}), ("std", {"x": "val"}), ("std_pop", {"x": "val"}), ("cov_pop", {"x": "val", "y": "other"}),
                    ("cov", {"x": "val", "y": "other"}), ("corr", {"x": "val", "y": "other"}), ("median", {"x": "val"}), ("any_value", {"x": "val"}), ("approx_count_distinct", {"x": "txt"})]:
        f = S.FUNCS[id]
        original = ev.aggregate(v=f["build"](S.resolve(f, ev, raw))).to_polars()["v"][0]
        for _, build in S.SUBS[id]:
            other = ev.aggregate(v=build(S.resolve(f, ev, raw))).to_polars()["v"][0]
            if id == "any_value":
                assert other == 1  # 최솟값
            else:
                assert other == pytest.approx(original), id
