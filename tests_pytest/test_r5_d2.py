"""4단계 D2(대시보드 서버): 차트 종류 17가지·카드 종류 12가지의 등록·검증(dash_store)과 새 카드 종류의 값 구하기(dash_calc, flow_pairs).
값은 손으로 계산해 맞춘다. 데이터는 시험 안에서 지어낸 DuckDB memtable이고, 카드의 조회(querying.runnable·build)만 그 표로 바꿔 끼운다."""
import datetime as dt
import re

import polars as pl
import pytest
from lazy import ibis

from core import dash_store
from dashboard import dash_calc

D = dt.datetime
ROWS = [  # 시각, 매장, 금액, 사용자, 단계, 가격
    (D(2026, 1, 5, 1), "A", 10.0, "u1", "view", 100.0),
    (D(2026, 1, 5, 2), "A", 20.0, "u1", "cart", 110.0),
    (D(2026, 1, 5, 3), "B", 30.0, "u2", "view", 90.0),
    (D(2026, 1, 6, 1), "B", 40.0, "u2", "cart", 95.0),
    (D(2026, 1, 6, 2), "A", 50.0, "u3", "view", 120.0),
    (D(2026, 1, 12, 1), "A", 60.0, "u1", "buy", 105.0),
    (D(2026, 1, 12, 2), "B", 70.0, "u2", "buy", 99.0),
    (D(2026, 2, 2, 1), "A", 80.0, "u4", "view", 101.0),
    (D(2026, 2, 9, 1), "B", 15.0, "u1", "view", 100.0),
]
DF = pl.DataFrame(ROWS, schema=["ts", "store", "amt", "uid", "step", "px"], orient="row")
SUM = [{"y": "amt", "agg": "sum"}]
NEW_CHARTS = ("group", "stack100", "hstack", "hstack100", "sarea", "sarea100", "step", "combo", "waterfall", "pareto", "radial", "polar", "range", "slope", "trendline", "butterfly", "lollipop")
NEW_KINDS = ("histogram", "boxplot", "heatmap", "calheat", "treemap", "sunburst", "bullet", "bump", "sankey", "cohort", "candle", "wordcloud")
FLOW = {"user": "uid", "step": "step", "time": "ts", "steps": ["view", "cart", "buy"]}


@pytest.fixture(params=["memory", "polars"])
def mem(request, monkeypatch):
    """카드의 조회를 시험 표로 바꿔 끼운다(memtable과 Polars 연결 두 가지)."""
    t = ibis.memtable(DF.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", DF)
    monkeypatch.setattr(dash_calc.querying, "runnable", lambda body, select=True, keep=None, **kw: t)
    monkeypatch.setattr(dash_calc.querying, "build", lambda body, select=True, **kw: t)
    return t


def card(kind, view=None, analysis=None, **body):
    source = {"table": "t", "body": {"base": "t", **body}, **({"analysis": analysis} if analysis else {})}
    return dash_store.check_card({"kind": kind, "title": "t", "source": source, "view": view or {}})


def data(kind, view=None, analysis=None, ctx=None, **body):
    return dash_calc.compute(card(kind, view, analysis, **body), "2026-11-03T09:00:00", ctx)


# ---------- dash_store: 이름 등록과 view 검증 ----------

def test_chart_types_and_time_types_registered():
    assert set(NEW_CHARTS) <= set(dash_store.CHART_TYPES) and len(dash_store.CHART_TYPES) == 28
    assert all(isinstance(v, str) and v for v in dash_store.CHART_TYPES.values())
    assert {"sarea", "sarea100", "step", "combo", "waterfall", "pareto", "slope"} <= set(dash_store.TIME_TYPES)
    assert not {"group", "radial", "scatter"} & set(dash_store.TIME_TYPES)
    for name in NEW_CHARTS:
        assert dash_store.check_view({"type": name})["type"] == name
    with pytest.raises(ValueError, match="차트 종류"):
        dash_store.check_view({"type": "sankey"})


def test_kinds_registered_and_pass_check_card():
    assert set(NEW_KINDS) <= set(dash_store.KINDS)
    for kind in NEW_KINDS:
        found = card(kind, analysis=FLOW if kind in ("sankey", "cohort") else None, x="store", series=SUM)
        assert found["kind"] == kind and found["data"] == {}
    for kind in ("sankey", "cohort"):  # 다시 구할 분석(사용자·시각 …)이 없으면 거부
        with pytest.raises(ValueError, match="source.analysis"):
            card(kind, x="store")
    with pytest.raises(ValueError, match="2~10개"):
        card("sankey", analysis={**FLOW, "steps": ["view"]})


def test_view_rings_fit_layout_bins_bands():
    ok = dash_store.check_view({"type": "doughnut", "rings": True, "fit": "fill", "layout": "right", "bins": 100, "bands": [50, 80.5, 100]})
    assert ok["rings"] is True and ok["fit"] == "fill" and ok["layout"] == "right" and ok["bins"] == 100 and ok["bands"] == [50, 80.5, 100]
    assert "rings" not in dash_store.check_view({}) and dash_store.check_view({"fit": "center", "layout": "below"})["layout"] == "below"
    bad = [({"rings": "yes"}, "rings"), ({"fit": "stretch"}, "fit"), ({"layout": "left"}, "layout"), ({"bins": 101}, "구간 수"), ({"bins": 0}, "구간 수"),
           ({"bins": True}, "구간 수"), ({"bins": 2.5}, "구간 수"), ({"bands": [3, 2]}, "bands"), ({"bands": "x"}, "bands"), ({"bands": list(range(7))}, "bands"), ({"bands": [True]}, "bands")]
    for view, why in bad:
        with pytest.raises(ValueError, match=why):
            dash_store.check_view(view)


def test_constants_route_sends_new_types(client):
    got = client.get("/api/dash/constants").get_json()["dash"]
    assert set(NEW_CHARTS) <= set(got["types"]) and got["types"]["stack100"] == "100% 누적 막대" and "waterfall" in got["time_types"]
    assert got["max_bins"] == 100 and got["max_bands"] == 6


def test_card_add_and_cards_compute_accept_new_kinds(client, mem, monkeypatch):
    from dashboard import dash_refresh
    monkeypatch.setattr(dash_refresh, "scan_decision", lambda c, ctx: "")  # 처리 용량 판정은 연결이 있어야 해서 이 시험과 상관없다
    body = {"base": "t", "x": "store", "split": "step", "series": SUM}
    dash = ""
    for kind in ("heatmap", "treemap", "histogram"):
        added = client.post("/api/dash/card/add", json={"dashboard": dash, "name": "새 판", "card": {"kind": kind, "title": kind, "source": {"table": "t", "body": body}, "view": {"bins": 5}}})
        assert added.status_code == 200, added.get_json()
        dash = added.get_json()["dashboard"]["id"]
    assert added.get_json()["dashboard"]["cards"] == 3
    cards = [{"id": f"c{i}", "kind": kind, "title": kind, "source": {"table": "t", "body": {**body, "x": "amt" if kind == "histogram" else "store"}}, "view": {"bins": 5}} for i, kind in enumerate(("heatmap", "treemap", "histogram"))]
    results = client.post("/api/dash/cards/compute", json={"cards": cards}).get_json()["results"]
    assert [list(r) for r in results] == [["id", "data"]] * 3, results
    assert results[0]["data"]["values"]["rows"] == ["A", "B"] and results[2]["data"]["bins"]["counts"] == [3, 1, 2, 1, 2]  # 너비 14: 10·15·20 / 30 / 40·50 / 60 / 70·80
    one = client.post("/api/dash/card/compute", json={"card": cards[1]}).get_json()
    assert one["data"]["values"]["items"][0]["name"] == "A"


def test_check_data_accepts_new_shapes():
    assert dash_store.check_data({"bins": {"edges": [0, 1], "counts": [3]}, "values": {"items": []}}, "histogram")["bins"]["counts"] == [3]
    with pytest.raises(ValueError, match="쓸 수 없는 항목"):
        dash_store.check_data({"cells": []}, "heatmap")
    for bad in ({"edges": [0, 1]}, [1, 2], {"edges": 1, "counts": 2}):
        with pytest.raises(ValueError, match="data.bins"):
            dash_store.check_data({"bins": bad}, "histogram")


# ---------- dash_calc: 새 카드 종류의 값 ----------

def test_histogram(mem):
    got = data("histogram", {"bins": 4}, x="amt")
    assert got["bins"] == {"edges": [10.0, 27.5, 45.0, 62.5, 80.0], "counts": [3, 2, 2, 2]}  # 15·10·20 / 30·40 / 50·60 / 70·80(최댓값은 마지막 구간)
    assert len(data("histogram", x="amt")["bins"]["counts"]) == 20 and sum(data("histogram", x="amt")["bins"]["counts"]) == 9  # 기본 20구간
    same = dash_calc.histogram_data(mem.filter(mem.step == "buy").mutate(amt=ibis.literal(5.0)), "amt", 4)["bins"]
    assert same == {"edges": [5.0, 5.0], "counts": [2]}  # 값이 모두 같으면 한 구간
    with pytest.raises(ValueError, match="숫자 컬럼"):
        data("histogram", x="store")


def test_boxplot_with_and_without_outliers(mem):
    got = data("boxplot", x="store", series=[{"y": "amt", "agg": "sum"}])["values"]["groups"]
    assert [g["name"] for g in got] == ["A", "B"]  # 행이 많은 매장 먼저
    assert got[0] == {"name": "A", "n": 5, "min": 10, "q1": 20, "med": 50, "q3": 60, "max": 80, "outliers": [], "outlier_count": 0}
    assert got[1] == {"name": "B", "n": 4, "min": 15, "q1": 26.25, "med": 35, "q3": 47.5, "max": 70, "outliers": [], "outlier_count": 0}
    one = data("boxplot", series=[{"y": "amt", "agg": "sum"}])["values"]["groups"]
    assert [g["name"] for g in one] == ["전체"] and one[0]["med"] == 40 and one[0]["n"] == 9
    spread = ibis.memtable(pl.DataFrame({"g": ["a"] * 10, "v": [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 100]}).to_arrow())
    box = dash_calc.boxplot_data(spread, "g", "v")["values"]["groups"][0]
    assert (box["q1"], box["q3"], box["max"], box["outliers"]) == (3.25, 7.75, 9, [100]) and box["min"] == 1  # 수염은 1.5 IQR 안의 가장 먼 값, 100은 이상치


def test_boxplot_outliers_are_capped(mem, monkeypatch):
    monkeypatch.setattr(dash_calc, "MAX_OUTLIERS", 2)
    values = [50.0] * 20 + [1000.0, 2000.0, 3000.0, -500.0]
    wide = ibis.memtable(pl.DataFrame({"g": ["a"] * len(values), "v": values}).to_arrow())
    box = dash_calc.boxplot_data(wide, "g", "v")["values"]["groups"][0]
    assert box["outliers"] == [3000, 2000] and box["outlier_count"] == 4  # 중앙값에서 먼 것부터 2개만, 전체 4개


def test_heatmap(mem, monkeypatch):
    got = data("heatmap", x="store", split="step", series=SUM)["values"]
    assert got == {"rows": ["A", "B"], "cols": ["buy", "cart", "view"], "cells": [[60, 20, 140], [70, 40, 45]]}
    monkeypatch.setattr(dash_calc, "MAX_CELLS", 5)
    with pytest.raises(ValueError, match="칸"):
        data("heatmap", x="store", split="step", series=SUM)  # 2 x 3 = 6칸
    with pytest.raises(ValueError, match="열 컬럼"):
        data("heatmap", x="store", series=SUM)


def test_calheat(mem):
    got = data("calheat", x="ts", series=SUM)["values"]["days"]
    assert got == [{"d": "2026-01-05", "v": 60}, {"d": "2026-01-06", "v": 90}, {"d": "2026-01-12", "v": 130}, {"d": "2026-02-02", "v": 80}, {"d": "2026-02-09", "v": 15}]
    assert dash_calc.with_period({"base": "t"}, "ts", {"start": "2026-01-01", "end": "2026-01-31"})["periods"] == ["ts 2026-01-01 ~ 2026-01-31"]  # 대시보드 기간은 날짜 컬럼(x)의 기간 줄로 걸린다


def test_treemap_and_sunburst(mem, monkeypatch):
    flat = data("treemap", x="store", series=SUM)["values"]["items"]
    assert flat == [{"name": "A", "value": 220}, {"name": "B", "value": 155}]
    nested = data("sunburst", x="store", split="step", series=SUM)["values"]["items"]
    assert nested == [{"name": "A", "value": 220, "children": [{"name": "view", "value": 140}, {"name": "buy", "value": 60}, {"name": "cart", "value": 20}]},
                      {"name": "B", "value": 155, "children": [{"name": "buy", "value": 70}, {"name": "view", "value": 45}, {"name": "cart", "value": 40}]}]
    monkeypatch.setattr(dash_calc, "MAX_TREE", 2)
    over = data("treemap", x="step", series=SUM)
    assert over["values"]["items"] == [{"name": "view", "value": 185}, {"name": "기타", "value": 190}] and "기타" in over["note"]  # 나머지(cart 60 + buy 130)를 묶는다
    monkeypatch.setattr(dash_calc, "MAX_TREE", 10)
    two = data("treemap", x="step", split="store", series=SUM)["values"]["items"]  # 두 단계는 윗단계를 10 // 10 = 1개만, 나머지는 기타(윗단계가 큰 view만 아래단계가 있다)
    assert two == [{"name": "view", "value": 185, "children": [{"name": "A", "value": 140}, {"name": "B", "value": 45}]}, {"name": "기타", "value": 190}]
    monkeypatch.setattr(dash_calc, "MAX_TREE", 9)  # 아래단계 항목 합이 9 - 1 = 8을 넘으면 값이 작은 것을 줄인다
    one = dash_calc.tree_data(mem.mutate(all=ibis.literal("all")), "all", "amt", dash_calc.clean_series(SUM)[0])
    assert len(one["values"]["items"][0]["children"]) == 8 and one["values"]["items"][0]["children"][-1]["value"] == 15 and "줄였어요" in one["note"]


def test_bullet_reads_value_and_target(mem):
    got = data("bullet", {"bands": [100, 300, 500], "target": {"value": 400}}, series=SUM)
    assert got["values"]["scalar"] == [375] and got["target"]["value"] == 400


def test_candle(mem, monkeypatch):
    month = data("candle", {"unit": "month"}, x="ts", series=[{"y": "px", "agg": "max"}])["values"]
    assert month == {"labels": ["2026-01-01", "2026-02-01"], "candles": [{"open": 100, "high": 120, "low": 90, "close": 99}, {"open": 101, "high": 101, "low": 100, "close": 100}]}
    day = data("candle", x="ts", series=[{"y": "px", "agg": "max"}])["values"]
    assert day["labels"][:2] == ["2026-01-05", "2026-01-06"] and day["candles"][0] == {"open": 100, "high": 110, "low": 90, "close": 90}  # 그날 안에서 시각 순
    monkeypatch.setattr(dash_calc, "MAX_PERIODS", 2)
    with pytest.raises(ValueError, match="단위를 키우"):
        data("candle", x="ts", series=[{"y": "px", "agg": "max"}])  # 일 단위 5개


def test_bump_ranks_per_period(mem):
    got = data("bump", {"unit": "week"}, x="ts", split="store", series=SUM)["values"]
    assert got["labels"] == ["2026-01-05", "2026-01-12", "2026-02-02", "2026-02-09"]
    series = {s["name"]: s for s in got["series"]}
    assert list(series) == ["A", "B"]  # 합이 큰 대상 먼저
    assert series["A"]["ranks"] == [1, 2, 1, None] and series["A"]["values"] == [80, 60, 80, None]
    assert series["B"]["ranks"] == [2, 1, None, 1] and series["B"]["values"] == [70, 70, None, 15]


def test_cohort(mem, monkeypatch):
    got = data("cohort", {"unit": "month"}, analysis={"user": "uid", "time": "ts"})["values"]
    assert got["periods"] == [0, 1]
    assert got["cohorts"] == [{"start": "2026-01-01", "size": 3, "retained": [1.0, pytest.approx(1 / 3)]}, {"start": "2026-02-01", "size": 1, "retained": [1.0]}]
    monkeypatch.setattr(dash_calc, "MAX_COHORT", 1)
    with pytest.raises(ValueError, match="단위를 키우"):
        data("cohort", {"unit": "month"}, analysis={"user": "uid", "time": "ts"})  # 2개월 > 1칸


def test_sankey_and_flow_pairs(mem):
    from dashboard import flow_pairs
    got = data("sankey", analysis=FLOW)["values"]
    assert got["nodes"] == [{"name": "view", "users": 4}, {"name": "cart", "users": 2}, {"name": "buy", "users": 2}, {"name": "이탈", "users": 2}]
    assert got["links"] == [{"s": 0, "t": 1, "v": 2}, {"s": 1, "t": 2, "v": 2}, {"s": 0, "t": 3, "v": 2}]  # 이탈이 0명인 쌍(cart → 이탈)은 뺀다
    base = mem.select(u=mem.uid, s=mem.step, t=mem.ts)
    assert flow_pairs.pairs(base, ["view", "cart"]) == {"nodes": [{"name": "view", "users": 4}, {"name": "cart", "users": 2}, {"name": "이탈", "users": 2}],
                                                         "links": [{"s": 0, "t": 1, "v": 2}, {"s": 0, "t": 2, "v": 2}]}
    only = flow_pairs.pairs(base.filter(base.u.isin(["u1", "u2"])), ["view", "cart", "buy"])
    assert [n["name"] for n in only["nodes"]] == ["view", "cart", "buy"] and [l["v"] for l in only["links"]] == [2, 2]  # 이탈이 없으면 이탈 칸을 만들지 않는다


def test_wordcloud(mem, monkeypatch):
    assert data("wordcloud", x="step")["values"]["words"] == [{"w": "view", "n": 5}, {"w": "buy", "n": 2}, {"w": "cart", "n": 2}]
    monkeypatch.setattr(dash_calc, "MAX_WORDS", 2)
    cut = data("wordcloud", x="step")
    assert [w["w"] for w in cut["values"]["words"]] == ["view", "buy"] and "2개" in cut["note"]


def test_every_new_kind_saves_its_data(mem):
    """구한 값이 카드에 그대로 저장되는지(check_card의 data 검사와 JSON 직렬화)."""
    import json
    cases = [("histogram", {}, None, {"x": "amt"}), ("boxplot", {}, None, {"x": "store", "series": SUM}), ("heatmap", {}, None, {"x": "store", "split": "step", "series": SUM}),
             ("calheat", {}, None, {"x": "ts", "series": SUM}), ("treemap", {}, None, {"x": "store", "split": "step", "series": SUM}), ("sunburst", {}, None, {"x": "store", "series": SUM}),
             ("bullet", {"bands": [100]}, None, {"series": SUM}), ("bump", {"unit": "week"}, None, {"x": "ts", "split": "store", "series": SUM}),
             ("sankey", {}, FLOW, {}), ("cohort", {"unit": "month"}, {"user": "uid", "time": "ts"}, {}), ("candle", {}, None, {"x": "ts", "series": [{"y": "px", "agg": "max"}]}), ("wordcloud", {}, None, {"x": "step"})]
    assert {c[0] for c in cases} == set(NEW_KINDS)  # 열두 종류를 빠짐없이
    for kind, view, analysis, body in cases:
        got = data(kind, view, analysis, **body)
        saved = dash_store.check_card({**card(kind, view, analysis, **body), "data": got})
        assert saved["data"]["def_hash"] == got["def_hash"] and json.dumps(saved["data"], allow_nan=False)


def test_new_kind_without_query_is_refused():
    c = dash_store.check_card({"kind": "histogram", "title": "t", "source": {"table": "t"}, "view": {}})
    with pytest.raises(ValueError, match="조회"):
        dash_calc.compute(c, "2026-11-03T09:00:00")


# ---------- 진짜 연결(DuckDB 파일)로 라우트를 지나 구하기 ----------

@pytest.fixture
def shop(folder, tmp_path):
    import duckdb
    from query.search_app import app
    from core import server
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table events (ts timestamp, store varchar, amt double, uid varchar, step varchar, px double)")
    raw.executemany("insert into events values (?, ?, ?, ?, ?, ?)", ROWS)
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_new_kinds_through_the_route_on_a_real_connection(shop):
    def run(kind, view=None, analysis=None, **body):
        source = {"table": "events", "body": {"base": "events", **body}, **({"analysis": analysis} if analysis else {})}
        got = shop.post("/api/dash/card/compute", json={"card": {"kind": kind, "title": "t", "source": source, "view": view or {}}}).get_json()
        assert "data" in got, got
        return got["data"]
    assert run("heatmap", x="store", split="step", series=SUM)["values"]["cells"] == [[60, 20, 140], [70, 40, 45]]
    assert run("histogram", {"bins": 4}, x="amt")["bins"]["counts"] == [3, 2, 2, 2]
    assert run("boxplot", x="store", series=SUM)["values"]["groups"][0]["med"] == 50
    assert run("calheat", x="ts", series=SUM)["values"]["days"][0] == {"d": "2026-01-05", "v": 60}
    assert run("treemap", x="store", series=SUM)["values"]["items"] == [{"name": "A", "value": 220}, {"name": "B", "value": 155}]
    assert run("bullet", {"bands": [100, 300], "target": {"value": 400}}, series=SUM)["values"]["scalar"] == [375]
    assert run("candle", {"unit": "month"}, x="ts", series=[{"y": "px", "agg": "max"}])["values"]["candles"][0] == {"open": 100, "high": 120, "low": 90, "close": 99}
    assert [s["ranks"] for s in run("bump", {"unit": "week"}, x="ts", split="store", series=SUM)["values"]["series"]] == [[1, 2, 1, None], [2, 1, None, 1]]
    assert run("wordcloud", x="step")["values"]["words"][0] == {"w": "view", "n": 5}
    assert [c["size"] for c in run("cohort", {"unit": "month"}, analysis={"user": "uid", "time": "ts"})["values"]["cohorts"]] == [3, 1]
    flow = run("sankey", analysis=FLOW)["values"]
    assert [n["users"] for n in flow["nodes"]] == [4, 2, 2, 2] and flow["links"][0] == {"s": 0, "t": 1, "v": 2}
    window = {"range": {"start": "2026-01-01", "end": "2026-01-31"}}  # 기간을 걸면 날짜 X 종류는 그 안의 행만, 생키는 첫 단계가 기간 안인 사람만
    got = shop.post("/api/dash/card/compute", json={"card": {"kind": "calheat", "title": "t", "source": {"table": "events", "body": {"base": "events", "x": "ts", "series": SUM}}}, "ctx": window}).get_json()
    assert [d["d"] for d in got["data"]["values"]["days"]] == ["2026-01-05", "2026-01-06", "2026-01-12"]
    src = {"table": "events", "date": "ts", "body": {"base": "events"}, "analysis": FLOW}
    got = shop.post("/api/dash/card/compute", json={"card": {"kind": "sankey", "title": "t", "source": src}, "ctx": {"range": {"start": "2026-01-06", "end": "2026-02-28"}}}).get_json()
    assert [n["users"] for n in got["data"]["values"]["nodes"]] == [3, 0, 0, 3]  # 1월 6일 이후 view를 처음 한 사람: u3, u4, u1(2월 9일). 셋 다 그 뒤에 cart가 없다


# ---------- 서버 리뷰 지적 ⑦ ⑨ ⑫ 와 빈 틀 카드 ----------

TAB = re.escape("탭(@이름)을 쓰는 검색은 저장할 수 없어요")


def test_tab_searches_cannot_be_saved(folder):
    from core import settings
    used = [{"base": "@탭1"}, {"base": "t", "joins": ["@탭1 left id=id"]}, {"base": "t", "union": {"tables": ["@탭1"], "how": "left"}},
            {"base": "t", "calcs": ["그룹집계 count() 테이블 @탭1 키 id=id 이름 n"]}]
    for body in used:
        with pytest.raises(ValueError, match="가상 테이블로 저장"):
            dash_store.check_card({"kind": "chart", "title": "t", "source": {"table": "t", "body": body}})
        with pytest.raises(ValueError, match=TAB):
            settings.save_search("내 검색", body)
    fine = {"base": "t", "joins": ["u left id=id"], "calcs": ["식 concat(a, '@x') 이름 m"], "conditions": ["mail = a@b.com"]}  # 글 안의 @는 탭이 아니다
    assert dash_store.check_card({"kind": "chart", "title": "t", "source": {"table": "t", "body": fine}})["source"]["body"] == fine
    assert settings.save_search("내 검색", fine) == {"내 검색": fine}


def test_cohort_period_applies_to_cohort_start_only(shop):
    def cohorts(start, end):
        source = {"table": "events", "body": {"base": "events"}, "analysis": {"user": "uid", "time": "ts"}}
        got = shop.post("/api/dash/card/compute", json={"card": {"kind": "cohort", "title": "t", "source": source, "view": {"unit": "month"}}, "ctx": {"range": {"start": start, "end": end}}}).get_json()
        return got["data"]["values"]
    feb = cohorts("2026-02-01", "2026-02-28")  # u1은 1월부터 활동했으니 2월 신규가 아니다: 2월 코호트는 u4 하나
    assert feb["cohorts"] == [{"start": "2026-02-01", "size": 1, "retained": [1.0]}] and feb["periods"] == [0]
    both = cohorts("2026-01-01", "2026-02-28")
    assert [c["size"] for c in both["cohorts"]] == [3, 1] and both["cohorts"][0]["retained"][1] == pytest.approx(1 / 3)
    jan = cohorts("2026-01-01", "2026-01-31")  # 기간 끝 뒤의 활동은 세지 않는다
    assert jan["cohorts"] == [{"start": "2026-01-01", "size": 3, "retained": [1.0]}]


def outlier_table():
    rows = [(g, v) for g in "abc" for v in [50.0] * 20 + [1000.0, 2000.0, 3000.0]]
    return ibis.memtable(pl.DataFrame(rows, schema=["g", "v"], orient="row").to_arrow())


def test_boxplot_outliers_in_one_query_when_windows_work(monkeypatch):
    from ibis.expr.types.relations import Table
    from core import source
    monkeypatch.setattr(dash_calc, "MAX_OUTLIERS", 2)
    sent = []
    for name in ("to_pyarrow", "to_polars"):
        original = getattr(Table, name)
        monkeypatch.setattr(Table, name, lambda self, *a, _o=original, **k: (sent.append(1), _o(self, *a, **k))[1])
    one = dash_calc.boxplot_data(outlier_table(), "g", "v")["values"]["groups"]
    assert [g["outliers"] for g in one] == [[3000, 2000]] * 3 and [g["outlier_count"] for g in one] == [3, 3, 3]
    assert len(sent) == 3  # 그룹 크기 1번 + 사분위·수염 1번 + 이상치 1번(그룹마다 따로 묻지 않는다)
    sent.clear()
    monkeypatch.setattr(source, "compiles", lambda expr: False)  # 연결이 창 함수를 못 하면 그룹마다
    each = dash_calc.boxplot_data(outlier_table(), "g", "v")["values"]["groups"]
    assert each == one and len(sent) == 5


def test_empty_flow_frame_cards_save_but_wrong_analysis_is_refused():
    for kind in ("sankey", "cohort", "funnel"):
        saved = dash_store.check_dashboard({"name": "x", "cards": [{"kind": kind, "title": "빈 틀"}]})  # 카드 모음에서 막 놓은 빈 틀
        assert saved["cards"][0]["kind"] == kind
    with pytest.raises(ValueError, match="source.analysis"):
        dash_store.check_dashboard({"name": "x", "cards": [{"kind": "sankey", "title": "t", "source": {"analysis": {"step": "s", "time": "t", "steps": ["a", "b"]}}}]})  # user 빠짐
    with pytest.raises(ValueError, match="source.analysis"):
        dash_store.check_dashboard({"name": "x", "cards": [{"kind": "cohort", "title": "t", "source": {"body": {"base": "t"}}}]})  # 조회만 정하고 분석이 없다
    with pytest.raises(ValueError, match="흐름 카드"):
        dash_store.check_dashboard({"name": "x", "cards": [{"kind": "funnel", "title": "t", "source": {"analysis": {"kind": "funnel", "steps": []}}}]})


# ---------- web 감사 C1(기간 기준 날짜) · C2(아랫단계는 합계·개수만) ----------

@pytest.fixture
def ranged(monkeypatch):
    """조회의 기간 줄('컬럼 시작 ~ 끝')을 실제로 거는 시험 표."""
    t = ibis.memtable(DF.to_arrow())

    def runnable(body, select=True, keep=None, **kw):
        out = t
        for line in body.get("periods") or []:
            col, rest = line.split(" ", 1)
            start, end = (dt.date.fromisoformat(s.strip()) for s in rest.split("~"))
            out = out.filter(out[col].cast("date") >= start, out[col].cast("date") <= end)
        return out
    monkeypatch.setattr(dash_calc.querying, "runnable", runnable)
    monkeypatch.setattr(dash_calc.querying, "build", lambda body, select=True, **kw: t)


def dated(kind, view=None, ctx=None, **body):
    source = {"table": "t", "date": "ts", "body": {"base": "t", **body}}
    return dash_calc.compute(dash_store.check_card({"kind": kind, "title": "t", "source": source, "view": view or {}}), "2026-11-03T09:00:00", ctx)


def test_period_reaches_kinds_without_a_date_x(ranged):
    feb, jan = {"range": {"start": "2026-02-01", "end": "2026-02-28"}}, {"range": {"start": "2026-01-01", "end": "2026-01-31"}}
    cases = [("histogram", {"bins": 2}, {"x": "amt"}, lambda d: d["bins"], {"edges": [15.0, 47.5, 80.0], "counts": [1, 1]}),
             ("boxplot", {}, {"x": "store", "series": SUM}, lambda d: [(g["name"], g["n"]) for g in d["values"]["groups"]], [("A", 1), ("B", 1)]),
             ("heatmap", {}, {"x": "store", "split": "step", "series": SUM}, lambda d: d["values"], {"rows": ["A", "B"], "cols": ["view"], "cells": [[80.0], [15.0]]}),
             ("treemap", {}, {"x": "store", "series": SUM}, lambda d: d["values"]["items"], [{"name": "A", "value": 80.0}, {"name": "B", "value": 15.0}]),
             ("sunburst", {}, {"x": "store", "series": SUM}, lambda d: d["values"]["items"], [{"name": "A", "value": 80.0}, {"name": "B", "value": 15.0}]),
             ("wordcloud", {}, {"x": "step"}, lambda d: d["values"]["words"], [{"w": "view", "n": 2}])]
    for kind, view, body, pick, want in cases:
        assert pick(dated(kind, view, feb, **body)) == want, kind
        assert pick(dated(kind, view, jan, **body)) != want and pick(dated(kind, view, jan, **body)) != pick(dated(kind, view, None, **body)), kind  # 1월과 전체는 값이 다르다
        assert dated(kind, view, feb, **body)["date_cols"] == ["ts"], kind  # 설정 패널이 기간 기준 날짜를 고를 수 있게


def test_sankey_and_cohort_keep_their_own_period_rules(ranged):
    """생키는 첫 단계만, 코호트는 코호트 시작에만 기간을 거니 조회 전체에 기간 줄을 걸지 않는다."""
    window = {"range": {"start": "2026-01-06", "end": "2026-02-28"}}
    source = {"table": "t", "date": "ts", "body": {"base": "t"}, "analysis": FLOW}
    got = dash_calc.compute(dash_store.check_card({"kind": "sankey", "title": "t", "source": source}), "2026-11-03T09:00:00", window)
    assert [n["users"] for n in got["values"]["nodes"]] == [3, 0, 0, 3]


def test_tree_split_needs_additive_aggregations(mem):
    for agg in ("mean", "min", "max", "median", "nunique"):
        series = [{"y": "amt" if agg != "nunique" else "uid", "agg": agg}]
        for kind in ("treemap", "sunburst"):
            with pytest.raises(ValueError, match="합계·개수만"):
                data(kind, x="store", split="step", series=series)
            with pytest.raises(ValueError, match="합계·개수만"):
                card(kind, x="store", split="step", series=series)  # 저장할 때 막는다
        assert data("treemap", x="store", series=series)["values"]["items"]  # 아랫단계가 없으면 어느 집계든
    assert data("treemap", x="store", split="step", series=[{"y": None, "agg": "count"}])["values"]["items"][0]["value"] == 5
    with pytest.raises(ValueError, match="합계·개수만"):
        dash_calc.tree_data(mem, "store", "step", {"y": "amt", "agg": "mean"})


# ---------- 서버 리뷰 S3: 처리 용량 추정(fetched)과 실제 조회(shape_data)의 기간 컬럼 ----------

def test_estimate_and_real_read_use_the_same_period_column(monkeypatch):
    t = ibis.memtable(pl.DataFrame({"created": [D(2026, 1, 1), D(2026, 3, 5)], "paid": [D(2026, 3, 1), D(2026, 3, 6)], "amt": [1.0, 2.0], "uid": ["a", "b"], "step": ["v", "w"],
                                    "store": ["A", "B"]}).to_arrow())
    seen = []
    monkeypatch.setattr(dash_calc.querying, "runnable", lambda body, select=True, keep=None, **kw: (seen.append(body.get("periods") or []), t)[1])
    monkeypatch.setattr(dash_calc.querying, "build", lambda body, select=True, **kw: t)
    ctx = {"range": {"start": "2026-03-01", "end": "2026-03-31"}, "prev": {"start": "2026-01-29", "end": "2026-02-28"}}
    series = [{"y": "amt", "agg": "sum"}]
    flow = {"user": "uid", "step": "step", "time": "paid", "steps": ["v", "w"]}
    cases = [("calheat", {"x": "paid", "series": series}, None, "created"), ("calheat", {"x": "paid", "series": series}, "x", "paid"),  # 고른 기간 기준 날짜가 먼저, 없으면 X
             ("candle", {"x": "paid", "series": series}, None, "created"), ("bump", {"x": "paid", "split": "store", "series": series}, None, "created"),
             ("histogram", {"x": "amt"}, None, "created"), ("heatmap", {"x": "store", "split": "step", "series": series}, None, "created"),
             ("cohort", {}, None, None), ("sankey", {}, None, None)]  # 코호트·생키는 조회에 기간을 걸지 않는다(추정에도)
    for kind, body, own, column in cases:
        src = {"table": "t", "body": {"base": "t", **body}, **({"date": "created"} if own is None else {})}
        if kind in ("cohort", "sankey"):
            src["date"], src["analysis"] = "created", flow
        card_ = dash_store.check_card({"kind": kind, "title": "t", "source": src, "view": {"unit": "month"}})
        seen.clear()
        got = dash_calc.shape_data(card_, ctx)
        lines = {"real": seen[0], "estimate": dash_calc.fetched(card_, ctx).get("periods") or []}
        assert {k: [l.split()[0] for l in v] for k, v in lines.items()} == {k: [column] if column else [] for k in lines}, (kind, own, lines)
        assert ("date_cols" in got) == (kind != "cohort"), kind  # 코호트에는 기간 기준 날짜를 고를 칸이 없다
        if column:  # 추정이 직전 기간까지 읽으면 확인 창의 바이트가 부풀고 받아 둔 사본의 열쇠가 어긋난다: 새 종류는 보는 기간 그대로
            assert lines["real"] == lines["estimate"] == [f"{column} 2026-03-01 ~ 2026-03-31"], kind


# ---------- 2차 서버 감사 AA4(코호트 기간 중간 시작) · AA5(SQLite의 상자 그림·캔들) ----------

def test_cohort_period_starting_mid_week_keeps_people_first_seen_inside(mem):
    """기간 01-06 ~ 01-31을 주 단위로: u3은 01-06에 처음 왔으니 신규(그 주 시작 01-05 < 기간 시작이어도), u1·u2는 01-05에 처음 왔으니 아니다."""
    got = dash_calc.cohort_data(mem, "uid", "ts", "week", {"start": "2026-01-06", "end": "2026-01-31"})["values"]
    assert got == {"periods": [0], "cohorts": [{"start": "2026-01-05", "size": 1, "retained": [1.0]}]}


@pytest.fixture
def lite(folder, tmp_path):
    import sqlite3
    from query.search_app import app
    from core import server
    q = sqlite3.connect(tmp_path / "s.db")
    q.execute("create table events (ts timestamp, store text, amt real, uid text, step text, px real)")
    q.executemany("insert into events values (?, ?, ?, ?, ?, ?)", [(r[0].isoformat(sep=" "), *r[1:]) for r in ROWS])
    q.commit()
    q.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_boxplot_and_candle_work_on_sqlite(lite):
    """SQLite는 분위수·정렬 집계(first·last)를 못 해서 카드가 읽는 행을 이 PC로 받아 구한다."""
    def run(kind, view=None, **body):
        source = {"table": "events", "body": {"base": "events", **body}}
        got = lite.post("/api/dash/card/compute", json={"card": {"kind": kind, "title": "t", "source": source, "view": view or {}}, "confirmed": True}).get_json()
        assert "data" in got, got
        return got["data"]["values"]
    box = run("boxplot", x="store", series=SUM)["groups"]
    assert [(g["name"], g["min"], g["q1"], g["med"], g["q3"], g["max"]) for g in box] == [("A", 10, 20, 50, 60, 80), ("B", 15, 26.25, 35, 47.5, 70)]
    candles = run("candle", {"unit": "month"}, x="ts", series=[{"y": "px", "agg": "max"}])
    assert candles["candles"][0] == {"open": 100, "high": 120, "low": 90, "close": 99}


# ---------- web 2차 감사 AC9: 빈 틀 카드의 최신화 건너뜀 이유 ----------

def test_refresh_skip_reason_for_a_card_without_data_choice():
    from dashboard import dash_refresh
    now = dt.datetime.now()
    empty = dash_store.check_card({"kind": "sankey", "title": "t", "source": {}})
    assert dash_refresh.skip_reason({"refresh": {"every": "1일"}}, empty, "file:///x#a.csv", now) == "데이터를 아직 정하지 않은 카드예요"
    other = {**empty, "source": {"key": "file:///y#b.csv", "body": {"base": "t"}, "analysis": FLOW}}
    assert dash_refresh.skip_reason({"refresh": {"every": "1일"}}, other, "file:///x#a.csv", now) == "다른 연결에서 구한 카드예요"


# ---------- 서버 2차 리뷰 RA1: 처리 용량 판정(copies_to_pc)이 상자 그림·양초의 집계도 본다 ----------

def made(kind, view=None, **body):
    return dash_store.check_card({"kind": kind, "title": "t", "source": {"table": "events", "body": {"base": "events", **body}}, "view": view or {}})


def test_copies_to_pc_counts_boxplot_and_candle_aggregations_on_sqlite(lite):
    """분위수·first/last를 못 하는 연결(SQLite)은 읽는 행을 이 PC로 받으니 True. 판정이 False면 원격 DB에서 크기를 묻지 않는다."""
    box, candle = made("boxplot", x="store", series=SUM), made("candle", {"unit": "day"}, x="ts", series=[{"y": "px", "agg": "max"}])
    assert dash_calc.shape_extra(box) == [("quantile", "amt", {"p": 0.5})] and dash_calc.shape_extra(made("chart", x="store", series=SUM)) == []
    assert [dash_calc.copies_to_pc(c, None) for c in (box, candle)] == [True, True]


def test_copies_to_pc_is_false_when_the_connection_can_do_it(shop):
    box, candle = made("boxplot", x="store", series=SUM), made("candle", {"unit": "day"}, x="ts", series=[{"y": "px", "agg": "max"}])
    assert [dash_calc.copies_to_pc(c, None) for c in (box, candle, made("histogram", x="amt"))] == [False, False, False]
