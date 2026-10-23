"""함수 사전(sql_funcs) 도입 뒤에도 저장돼 있는 예전 값이 그대로 읽힌다: 지표 파일의 agg(nunique 포함), 피벗의 how, 차트·카드 계열의 agg, 전처리 recipe(dedupe, formulas)와
새로 더한 중복 제거 선택(dedupe_keep)·계산 컬럼의 정렬/묶음. 값은 손으로 계산한 것."""
import datetime as dt

import duckdb
import pytest
from lazy import ibis

import calc
import dash_calc
import metrics
import prep
import prep_derive
import result

ROWS = [(1, "a", 1.0, "x"), (2, "a", 2.0, "y"), (3, "a", 3.0, "x"), (4, "b", 10.0, "p"), (5, "b", 20.0, "p"), (6, "c", 5.0, None)]


@pytest.fixture(scope="module")
def t():
    raw = duckdb.connect()
    raw.execute("create table t (id bigint, grp varchar, val double, txt varchar)")
    raw.executemany("insert into t values (?, ?, ?, ?)", ROWS)
    yield ibis.duckdb.from_connection(raw).table("t")
    raw.close()


# ---------- 지표 ----------

def test_metric_parts_keep_their_old_shape():
    assert metrics.part("p", {"agg": "nunique", "column": "user"}) == {"agg": "nunique", "column": "user", "where": []}  # opts가 없으면 예전 모양 그대로
    assert metrics.part("p", {"agg": "count"}) == {"agg": "count", "column": None, "where": []}
    assert metrics.AGGS[:6] == ("count", "sum", "mean", "min", "max", "nunique") and metrics.AGG_NAMES["nunique"] == "고유 개수"
    assert metrics.AGG_NAMES["count"] == "개수" and metrics.AGG_NAMES["median"] == "중앙값"


def test_old_and_new_metrics_compute(t):
    found = {"고유": metrics.metric("고유", {"numerator": {"agg": "nunique", "column": "grp"}}),
             "중앙": metrics.metric("중앙", {"numerator": {"agg": "median", "column": "val", "where": "grp = a"}}),
             "분위": metrics.metric("분위", {"numerator": {"agg": "quantile", "column": "val", "opts": {"p": 0.5}}, "denominator": {"agg": "count"}}),
             "참": metrics.metric("참", {"numerator": {"agg": "count_if", "opts": {"cond": "val > 2"}}})}
    _, values, _ = metrics.compute(t, list(found), found)
    assert values["고유"] == [3] and values["중앙"] == [2.0] and values["참"] == [4]
    assert values["분위"] == [pytest.approx(4.0 / 6)]  # 중앙값(1,2,3,5,10,20 → 4) ÷ 행 수 6
    with pytest.raises(ValueError, match="숫자 컬럼이 아니라"):
        metrics.compute(t, ["합"], {"합": metrics.metric("합", {"numerator": {"agg": "sum", "column": "txt"}})})
    with pytest.raises(ValueError, match="column이 있어야"):
        metrics.metric("m", {"numerator": {"agg": "median"}})


# ---------- 피벗 ----------

def test_pivot_old_hows_and_new_ones(t):
    assert list(calc.HOWS)[:5] == ["count", "sum", "mean", "min", "max"] and calc.HOWS["sum"] == "합계"
    old = calc.pivot(t, {"rows": ["grp"], "how": "sum", "value": "val", "column": "txt"}, lambda e: ["p", "x", "y"]).order_by("grp").to_polars()
    assert old["전체 합계"].to_list() == [6.0, 30.0, 5.0] and old["x"].to_list() == [4.0, None, None] and old["p"].to_list() == [None, 30.0, None]
    old_unique = calc.pivot(t, {"rows": ["grp"], "how": "nunique", "value": "txt"}, lambda e: []).order_by("grp").to_polars()
    assert old_unique["전체 고유 개수"].to_list() == [2, 1, 0]  # 예전 이름 nunique
    new = calc.pivot(t, {"rows": ["grp"], "how": "quantile", "opts": {"p": 0.5}, "value": "val"}, lambda e: []).order_by("grp").to_polars()
    assert new["전체 분위수"].to_list() == [2.0, 15.0, 5.0]
    with pytest.raises(ValueError, match="집계는"):
        calc.pivot(t, {"rows": ["grp"], "how": "nope"}, lambda e: [])
    with pytest.raises(ValueError, match="숫자 컬럼에만"):
        calc.pivot(t, {"rows": ["grp"], "how": "sum", "value": "txt"}, lambda e: [])


# ---------- 차트·대시보드 카드 ----------

def test_chart_metric_old_values_and_messages(t):
    assert result.chart_metric(t, None, "count").execute() == 6 and result.chart_metric(t, "val", "count").execute() == 6  # count는 y를 보지 않는다
    assert result.chart_metric(t, "val", "sum").execute() == 41 and result.chart_metric(t, "val", "mean").execute() == pytest.approx(41 / 6)
    with pytest.raises(ValueError, match="합계·평균은 숫자 컬럼을 Y로"):
        result.chart_metric(t, "txt", "sum")
    with pytest.raises(ValueError, match="집계 방식"):
        result.chart_metric(t, "val", "nope")
    assert result.chart_metric(t, "val", "median").execute() == 4.0 and result.chart_metric(t, "val", "quantile", {"p": 0.9}).execute() == pytest.approx(15.0)
    assert result.chart_metric(t, "txt", "nunique").execute() == 3
    labels, series, _ = result.get_chart(t, "grp", [{"y": "val", "agg": "sum"}, {"y": "val", "agg": "median"}])
    assert labels == ["a", "b", "c"] and series[0]["values"] == [6, 30, 5] and series[1]["values"] == [2, 15, 5]


def test_dash_series_old_and_new():
    assert dash_calc.AGGS[:6] == ("count", "sum", "mean", "min", "max", "nunique")
    assert dash_calc.clean_series([{"y": "amt", "agg": "sum"}]) == [{"y": "amt", "agg": "sum"}]  # 옵션이 없으면 예전 모양 그대로
    assert dash_calc.clean_series([{"y": None, "agg": "count"}]) == [{"y": None, "agg": "count"}]
    assert dash_calc.clean_series([{"y": "amt", "agg": "quantile", "opts": {"p": 0.9}}])[0]["opts"] == {"p": 0.9}
    assert [dash_calc.merges(a) for a in ("count", "sum", "mean", "min", "max", "nunique", "median")] == [True] * 5 + [False, False]
    with pytest.raises(ValueError, match="옵션"):
        dash_calc.clean_series([{"y": "amt", "agg": "sum", "opts": 3}])


def test_dash_time_data_old_and_new_series():
    raw = duckdb.connect()
    raw.execute("create table s (day date, amt double, uid varchar)")
    raw.executemany("insert into s values (?, ?, ?)", [(dt.date(2026, 1, 1) + dt.timedelta(days=i), float(10 * (i + 1)), "u%d" % (i % 2)) for i in range(4)])
    s = ibis.duckdb.from_connection(raw).table("s")
    data = dash_calc.time_data(s, "day", [{"y": "amt", "agg": "sum"}, {"y": "uid", "agg": "nunique"}, {"y": "amt", "agg": "median"}], None)
    day = {x["name"]: x for x in data["by_unit"]["day"]["series"]}
    assert day["합계 (amt)"]["sum"] == [10, 20, 30, 40] and "merge" not in day["합계 (amt)"]
    assert day["고유 개수 (uid)"]["values"] == [1, 1, 1, 1] and day["고유 개수 (uid)"]["agg"] == "nunique"  # 예전 모양: 값 하나 + 합칠 수 없음
    assert day["중앙값 (amt)"]["values"] == [10, 20, 30, 40] and day["중앙값 (amt)"]["merge"] is False
    month = {x["name"]: x["values"] for x in data["by_unit"]["month"]["series"]}
    assert month["고유 개수 (uid)"] == [2] and month["중앙값 (amt)"] == [25.0] and "합계 (amt)" not in month  # 다시 묶을 수 없는 것만 서버가 구한다
    raw.close()


# ---------- 전처리 ----------

def build(t, **recipe):
    expr, _ = prep.build(None, {"base": "t", **recipe}, table=t)
    return expr


def by_grp(expr, column):
    frame = expr.to_polars().sort("grp")
    return dict(zip(frame["grp"], frame[column]))


def test_old_dedupe_recipe_unchanged(t):
    assert sorted(build(t, dedupe=["grp"]).to_polars()["grp"].to_list()) == ["a", "b", "c"]  # 예전처럼 기준마다 한 행
    assert build(t, dedupe=["grp"], dedupe_keep="first").count().execute() == 3  # "first"만 있는 recipe도 같다
    assert build(t, dedupe=["grp", "txt", "id", "val"]).count().execute() == 6
    assert prep.dupes(None, {"base": "t", "dedupe": ["grp"]}, t) == {"groups": 2, "rows": 3}  # 예전 모양 그대로(conflicts 없음)


def test_dedupe_keep_choices(t):
    assert by_grp(build(t, dedupe=["grp"], dedupe_keep={"how": "first", "order": ["val desc"]}), "val") == {"a": 3, "b": 20, "c": 5}
    assert by_grp(build(t, dedupe=["grp"], dedupe_keep={"how": "last", "order": ["val desc"]}), "val") == {"a": 1, "b": 10, "c": 5}
    assert by_grp(build(t, dedupe=["grp"], dedupe_keep={"how": "first", "order": ["id"]}), "id") == {"a": 1, "b": 4, "c": 6}
    assert by_grp(build(t, dedupe=["grp"], dedupe_keep="max"), "val") == {"a": 3, "b": 20, "c": 5} and by_grp(build(t, dedupe=["grp"], dedupe_keep="min"), "val") == {"a": 1, "b": 10, "c": 5}
    assert by_grp(build(t, dedupe=["grp"], dedupe_keep={"how": "mode"}), "txt") == {"a": "x", "b": "p", "c": None}
    merged = build(t, dedupe=["grp"], dedupe_keep={"how": "string_agg", "order": ["id"], "sep": "|"})
    assert by_grp(merged, "val") == {"a": "1.0|2.0|3.0", "b": "10.0|20.0", "c": "5.0"} and merged.columns == t.columns
    warned = build(t, dedupe=["grp"], dedupe_keep="warn")
    assert by_grp(warned, "val") == {"a": None, "b": None, "c": 5} and by_grp(warned, "txt") == {"a": None, "b": "p", "c": None}  # 값이 둘 이상이면 빈 값
    assert prep.dupes(None, {"base": "t", "dedupe": ["grp"], "dedupe_keep": "warn"}, t) == {"groups": 2, "rows": 3, "conflicts": 2}  # a와 b에서 값이 여럿


@pytest.mark.parametrize("keep, message", [({"how": "median"}, "how는"), ({"how": "last"}, "order"), ({"zzz": 1}, "모양"), ({"how": "first", "order": "val"}, "order는"),
                                           ({"how": "string_agg", "order": ["nope"]}, "컬럼|없습니다")])
def test_dedupe_keep_errors(t, keep, message):
    with pytest.raises(ValueError, match=message):
        build(t, dedupe=["grp"], dedupe_keep=keep)


def test_old_formulas_unchanged(t):
    expr, made = prep_derive.apply_formulas(t, [{"name": "twice", "expr": "val * 2"}, {"name": "label", "expr": "iif(val >= 5, 'big', 'small')"},
                                               {"name": "up", "expr": "upper(txt)"}, {"name": "r", "expr": "round(val / 3, 1)"}])
    frame = expr.select(*made).order_by("twice").to_polars()
    assert made == ["twice", "label", "up", "r"] and frame["twice"].to_list() == [2, 4, 6, 10, 20, 40] and frame["label"].to_list() == ["small"] * 3 + ["big"] * 3
    assert frame["up"].to_list()[:2] == ["X", "Y"] and frame["r"].to_list()[:3] == [0.3, 0.7, 1.0]
    with pytest.raises(ValueError, match="모양"):
        prep_derive.apply_formulas(t, [{"name": "x", "expr": "val", "zzz": 1}])
    with pytest.raises(ValueError, match="묶음 기준"):
        prep_derive.apply_formulas(t, [{"name": "x", "expr": "val", "group": ["nope"]}])


def test_new_formula_functions_use_order_and_group(t):
    expr, _ = prep_derive.apply_formulas(t, [{"name": "prev", "expr": "lag(val, 1, 0)", "order": ["id"], "group": ["grp"]}, {"name": "mid", "expr": "median(val)", "group": ["grp"]},
                                            {"name": "part", "expr": "substr(txt, 1, 1)"}, {"name": "tier", "expr": "case(val >= 10, 'hi', val >= 3, 'mid', 'lo')"}])
    frame = expr.select("id", "prev", "mid", "tier").order_by("id").to_polars()
    assert frame["prev"].to_list() == [0, 1, 2, 0, 10, 0] and frame["mid"].to_list() == [2, 2, 2, 15, 15, 5]
    assert frame["tier"].to_list() == ["lo", "lo", "mid", "hi", "hi", "mid"]


# ---------- 계산 줄 ----------

def test_old_calc_lines_and_new_window_lines(t):
    out = calc.apply(["순위 기준 val desc 그룹 grp", "누적합 val 기준 id 그룹 grp 이름 run", "dense_rank 기준 val desc 이름 dr", "lead val 1 기준 id 그룹 grp 이름 nxt"], t).order_by("id").to_polars()
    assert out["val_순위"].to_list() == [3, 2, 1, 2, 1, 1] and out["run"].to_list() == [1, 3, 6, 10, 30, 5]
    assert out["dr"].to_list() == [6, 5, 4, 2, 1, 3]  # 전체에서 val이 큰 순서(20 → 1, 10 → 2, 5 → 3 …)
    assert out["nxt"].to_list() == [2, 3, None, 20, None, None]
    with pytest.raises(ValueError, match="중 하나로 시작"):
        calc.apply(["nope val"], t)
