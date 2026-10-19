"""분석: 분포·상관관계·산점도·그룹 비교·시계열 추이가 메모리 표와 Polars 연결에서 같은 값을 내는지, /api/analyze가 표본에 전처리를 적용하고 full이면 전체로 구하는지.
데이터는 테스트 안에서 지어낸다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

import analysis

DF = pl.DataFrame({"g": ["a", "b", "a", "c", None, "a"] * 50, "x": [1, 2, 3, 4, 5, 100] * 50, "y": [2.0, 4, 6, 8, 10, None] * 50,
                   "z": [5, 3, 4, 1, 2, 6] * 50, "d": [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(300)]})


@pytest.fixture(params=["memory", "polars"])
def t(request):
    return ibis.memtable(DF.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", DF)


def test_distribution(t):
    d = analysis.analyze(t, {"kind": "distribution", "column": "x"})
    assert d["kind"] == "distribution" and d["type"] == "num"
    assert (d["stats"]["n"], d["stats"]["min"], d["stats"]["max"], d["stats"]["median"]) == (300, 1, 100, 3.5)
    assert sum(d["histogram"]["counts"]) == 300 and d["histogram"]["counts"][-1] == 50
    assert (d["box"]["low"], d["box"]["high"], d["box"]["outliers"]) == (1, 5, 50)
    text = analysis.analyze(t, {"kind": "distribution", "column": "g"})
    assert "stats" not in text and text["values"] == {"labels": ["a", "b", "c"], "counts": [150, 50, 50], "nulls": 50, "other": 0, "unique": 3}


def test_correlation_and_scatter(t):
    d = analysis.analyze(t, {"kind": "correlation", "columns": ["x", "y", "z"]})
    assert d["n"] == 250 and d["matrix"][0][1] == pytest.approx(1.0) and d["matrix"][0][0] == pytest.approx(1.0)
    expected = DF.drop_nulls(["x", "y", "z"]).select(pl.corr("y", "z")).item()
    assert d["matrix"][1][2] == pytest.approx(expected) and d["matrix"][2][1] == pytest.approx(expected)
    s = analysis.analyze(t, {"kind": "scatter", "x": "x", "y": "z"})
    assert len(s["points"]) == 300 and s["r"] == pytest.approx(DF.select(pl.corr("x", "z")).item())


def test_groups_and_trend(t):
    g = analysis.analyze(t, {"kind": "groups", "by": "g", "value": "x", "agg": "sum"})
    assert dict(zip(g["labels"], g["values"])) == {"a": 5200, "(빈 값)": 250, "c": 200, "b": 100} and g["labels"][0] == "a"
    m = analysis.analyze(t, {"kind": "trend", "column": "d", "unit": "month", "value": "", "agg": "count"})
    assert m["labels"][:2] == ["2026-01-01", "2026-02-01"] and m["values"][:2] == [31, 28] and sum(m["values"]) == 300
    w = analysis.analyze(t, {"kind": "trend", "column": "d", "unit": "week", "value": "x", "agg": "sum"})
    assert w["labels"][0] == "2025-12-29"  # 주는 월요일부터


@pytest.mark.parametrize("how, error", [
    ({"kind": "nope"}, "분석 종류"), ({"kind": "distribution", "column": "없음"}, "없음"),
    ({"kind": "correlation", "columns": ["x"]}, "2~"), ({"kind": "correlation", "columns": ["x", "g"]}, "숫자"),
    ({"kind": "scatter", "x": "x", "y": "x"}, "다른 컬럼"), ({"kind": "trend", "column": "x", "unit": "day", "agg": "count"}, "날짜"),
    ({"kind": "trend", "column": "d", "unit": "year", "agg": "count"}, "단위"), ({"kind": "groups", "by": "g", "value": "g", "agg": "sum"}, "숫자 컬럼"),
])
def test_checks(t, how, error):
    with pytest.raises(ValueError, match=error):
        analysis.analyze(t, how)


def test_route_sample_and_full(client, tmp_path):
    """표본(전처리 적용)과 전체. 연결은 지어낸 DuckDB 파일."""
    import duckdb
    import server
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.register("df", DF.to_arrow())
        raw.execute("create table sales as select * from df")
    con = ibis.duckdb.connect(str(path))
    server.state.update(con=con, opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    recipe = {"base": "sales", "filters": ["x >= 3"], "sorts": ["x desc"]}
    how = {"kind": "distribution", "column": "x"}
    d = client.post("/api/analyze", json={"recipe": recipe, "how": how}).get_json()
    assert (d["stats"]["n"], d["stats"]["min"], d["data"]["full"], d["data"]["rows"]) == (200, 3, False, 300)
    full = client.post("/api/analyze", json={"recipe": recipe, "how": how, "full": True}).get_json()
    assert (full["stats"]["n"], full["data"]) == (200, {"full": True})
    cols = client.post("/api/analyze", json={"recipe": {"base": "sales"}, "how": {"kind": "columns"}}).get_json()["columns"]
    assert cols == [["g", "text"], ["x", "num"], ["y", "num"], ["z", "num"], ["d", "date"]]
