"""Phase 8 다차원 집계·교차 요약(plans/phase-8.md): 프리셋(볼륨·타겟 분포·괴리율·분위수·ARPU/ARPPU), 값별 조건·전환율, 전체·부모 비중과 침투율, 총계 줄,
전체 비율·리프트, 파레토 누적, 결과 요약 묶음, 기간별 표 총계·침투율. 데이터는 시험 안에서 만든다.
T(6행): g = a,a,a,b,b,b · h = x,y,x,x,y,y · k = p,q,p,q,p,q · uid = u1,u1,u2,u3,u1,u3 · amt = 0,10,20,30,0,40."""
import duckdb
import polars as pl
import pytest
from lazy import ibis

from core import calc
from core import crosstab
from core import querying
from core import result
from core import server
from core import source
from core import sql_funcs
from metrics import period_table
from query import period_app  # noqa: F401  라우트는 Flask 앱이 첫 요청을 받기 전에 올려 둔다
from conftest import ROOT

T = pl.DataFrame({"g": ["a", "a", "a", "b", "b", "b"], "h": ["x", "y", "x", "x", "y", "y"], "k": ["p", "q", "p", "q", "p", "q"],
                  "uid": ["u1", "u1", "u2", "u3", "u1", "u3"], "amt": [0.0, 10.0, 20.0, 30.0, 0.0, 40.0]})
TT = ibis.memtable(T.to_arrow())
vals = lambda e: e.to_pyarrow()["v"].to_pylist()
BODY = {"base": "t", "joins": [], "periods": [], "conditions": [], "calcs": []}


def run(spec, t=TT):
    return calc.pivot(t, spec, vals).to_polars()


def by(frame, key="g"):
    return {r[key]: r for r in frame.iter_rows(named=True)}


# ---------- crosstab 순수 함수(스켈레톤) ----------

def test_quantile_name():
    assert [crosstab.quantile_name(p) for p in (0.25, 0.5, 0.9, 0.999)] == ["P25", "P50", "P90", "P99.9"]


def test_check_ps():
    assert crosstab.check_ps(None) == crosstab.QUANTILES
    assert crosstab.check_ps([0.9, 0.1, 0.9]) == (0.1, 0.9)
    too_many = [i / 100 for i in range(1, crosstab.MAX_QUANTILES + 2)]
    for bad in ([0], [1], [True], ["0.5"], [], "0.5", too_many):
        with pytest.raises(ValueError, match="분위는 0과 1 사이"):
            crosstab.check_ps(bad)


def test_expand_volume_and_errors():
    measures, derived, shares = crosstab.expand({"kind": "volume", "key": "uid"}, TT)
    assert [m["name"] for m in measures] == ["행 수", "고유 uid"] and [m["how"] for m in measures] == ["count", "count_distinct"]
    assert derived == [{"name": crosstab.DUP, "kind": "ratio", "num": "행 수", "den": "고유 uid"}] and shares == []
    _, _, shares = crosstab.expand({"kind": "target", "key": "uid", "target": "amt"}, TT)
    assert shares == ["고유 uid"]
    with pytest.raises(ValueError, match="프리셋은"):
        crosstab.expand({"kind": "nope"}, TT)
    with pytest.raises(ValueError, match="기준\\(key\\)"):
        crosstab.expand({"kind": "volume"}, TT)
    with pytest.raises(ValueError, match="숫자여야"):
        crosstab.expand({"kind": "gap", "target": "uid"}, TT)
    with pytest.raises(ValueError, match="above는 수여야"):
        crosstab.expand({"kind": "paying", "target": "amt", "above": "0"}, TT)


def test_conversions():
    assert crosstab.conversions(["a", "b", "c"], "prev") == [{"name": "a → b 전환율", "kind": "ratio", "num": "b", "den": "a"},
                                                             {"name": "b → c 전환율", "kind": "ratio", "num": "c", "den": "b"}]
    assert [d["den"] for d in crosstab.conversions(["a", "b", "c"], "first")] == ["a", "a"]
    with pytest.raises(ValueError, match="전환율은 prev"):
        crosstab.conversions(["a", "b"], "next")
    with pytest.raises(ValueError, match="둘 이상"):
        crosstab.conversions(["a"], "prev")


def test_derive():
    t = ibis.memtable(pl.DataFrame({"n": [4, 0], "d": [2, 0], "m": [3.0, 1.0], "e": [2.0, 0.0]}).to_arrow())
    out = crosstab.derive(t, [{"name": "r", "kind": "ratio", "num": "n", "den": "d"}, {"name": "gp", "kind": "gap", "num": "m", "den": "e"}]).to_polars()
    assert out["r"].to_list() == [2.0, None] and out["gp"].to_list() == [0.5, None]
    assert crosstab.derive(t, []) is t
    with pytest.raises(ValueError, match="칸이 없어요"):
        crosstab.derive(t, [{"name": "r", "kind": "ratio", "num": "zz", "den": "d"}])
    with pytest.raises(ValueError, match="이미 있어요"):
        crosstab.derive(t, [{"name": "n", "kind": "ratio", "num": "n", "den": "d"}])


def test_relative_share_reach_total_pct_lift():
    aggs = {"n": TT.count(), "u": TT.uid.nunique()}
    table = TT.group_by("g").aggregate(**aggs)
    out = by(crosstab.relative(table, TT, aggs, {"n": [], "u": []}, "value", ["n", "u"], {"u"}).to_polars())
    assert out["a"]["n 비중"] == pytest.approx(0.5) and out["a"]["u 침투율"] == pytest.approx(2 / 3) and out["b"]["u 침투율"] == pytest.approx(2 / 3)
    assert not any(c.startswith(crosstab.GRAND) for c in out["a"])
    sums = {"t": TT.amt.sum(), "x": TT.amt.sum(where=TT.h == "x")}
    pct = by(crosstab.relative(TT.group_by("g").aggregate(**sums), TT, sums, {"t": ["x"]}, "total_pct", [], set()).to_polars())
    assert (pct["a"]["t"], pct["a"]["x"], pct["b"]["t"], pct["b"]["x"]) == pytest.approx((0.3, 0.2, 0.7, 0.3))
    counts = {"t": TT.count(), "x": TT.count(where=TT.h == "x"), "y": TT.count(where=TT.h == "y")}
    lift = by(crosstab.relative(TT.group_by("g").aggregate(**counts), TT, counts, {"t": ["x", "y"]}, "lift", [], set()).to_polars())
    assert (lift["a"]["x"], lift["a"]["y"], lift["b"]["x"], lift["a"]["t"]) == pytest.approx((4 / 3, 2 / 3, 2 / 3, 3))
    uniq = {"u": TT.uid.nunique(), "ux": TT.uid.nunique(where=TT.h == "x"), "n": TT.count()}
    col = by(crosstab.relative(TT.group_by("g").aggregate(**uniq), TT, uniq, {"u": ["ux"], "n": []}, "col_pct", [], {"u"}).to_polars())
    assert (col["a"]["u"], col["a"]["ux"], col["b"]["ux"]) == pytest.approx((2 / 3, 2 / 3, 1 / 3)) and col["a"]["n"] == 3  # 고유 수는 다시 센 분모, 개수는 그대로(피벗이 sum으로)
    assert crosstab.relative(table, TT, aggs, {"n": []}, "value", [], set()) is table


def test_parent_table():
    aggs = {"s": TT.amt.sum()}
    out = crosstab.parent_table(TT, ["g", "h"], aggs, [["g", "h"], ["g"], []], ["s"]).to_polars()
    got = {(r["g"], r["h"]): r["s 부모 비중"] for r in out.iter_rows(named=True)}
    assert got[("a", "x")] == pytest.approx(2 / 3) and got[("b", "y")] == pytest.approx(4 / 7)
    assert got[("a", sql_funcs.SUBTOTAL)] == pytest.approx(0.3) and got[(sql_funcs.TOTAL, sql_funcs.TOTAL)] == pytest.approx(1.0)
    assert out.columns == ["g", "h", "s", "s 부모 비중"]


def test_pareto():
    frame = pl.DataFrame({"g": ["b", "a"], "전체 합계": [70.0, 30.0], "전체 합계 비중": [0.7, 0.3]})
    out = crosstab.pareto(frame, "전체 합계", "전체 합계 비중")
    assert out[f"{crosstab.CUM} 전체 합계"].to_list() == [70.0, 100.0] and out[crosstab.CUM_SHARE].to_list() == pytest.approx([0.7, 1.0])
    with pytest.raises(ValueError, match="칸이 없어요"):
        crosstab.pareto(frame, "전체 합계", "없는 칸")


def test_summary_columns():
    assert crosstab.summary_columns(["gap", "dup"], (0.5,)) == ["dup", "median", "gap"]
    assert crosstab.summary_columns(["zero", "quantiles"], (0.25, 0.9)) == ["P25", "P90", "above", "above_mean"]
    with pytest.raises(ValueError, match="요약 묶음은"):
        crosstab.summary_columns(["nope"], (0.5,))


# ---------- 피벗 (수정) ----------

def test_pivot_col_pct_distinct():
    uniq = by(run({"rows": ["g"], "column": "h", "how": "count_distinct", "value": "uid", "show": "col_pct"}))
    assert (uniq["a"]["전체 고유 개수"], uniq["a"]["x"], uniq["b"]["x"], uniq["b"]["y"]) == pytest.approx((2 / 3, 2 / 3, 1 / 3, 1.0))  # 분모는 다시 센 3·3·2(2+2·2+1이 아님)
    plain = by(run({"rows": ["g"], "column": "h", "how": "sum", "value": "amt", "show": "col_pct"}))
    assert (plain["a"]["전체 합계"], plain["a"]["x"]) == pytest.approx((0.3, 0.4))  # 더해지는 집계는 예전과 같다

def test_pivot_volume_preset():
    out = by(run({"rows": ["g"], "preset": {"kind": "volume", "key": "uid"}}))
    assert (out["a"]["행 수"], out["a"]["고유 uid"], out["a"][crosstab.DUP]) == (3, 2, pytest.approx(1.5))


def test_pivot_target_gap_quantiles_paying():
    target = by(run({"rows": ["g"], "preset": {"kind": "target", "key": "uid", "target": "amt"}}))
    assert target["a"]["고유 uid 침투율"] == pytest.approx(2 / 3) and target["b"]["amt 중앙값"] == pytest.approx(30)
    gap = by(run({"rows": ["g"], "preset": {"kind": "gap", "target": "amt"}}))
    assert gap["a"]["amt 괴리율"] == pytest.approx(0) and gap["b"]["amt 괴리율"] == pytest.approx((70 / 3 - 30) / 30)
    q = run({"rows": [], "preset": {"kind": "quantiles", "target": "amt", "ps": [0.5]}})
    assert q.columns == ["amt 최소", "amt P50", "amt 최대"] and (q["amt 최소"][0], q["amt 최대"][0]) == (0, 40)
    pay = by(run({"rows": ["g"], "preset": {"kind": "paying", "target": "amt", "key": "uid"}}))
    assert (pay["a"]["amt 평균(0 초과)"], pay["b"]["ARPU"], pay["b"]["ARPPU"], pay["b"]["0 초과 비율"]) == pytest.approx((15, 35, 70, 0.5))
    with pytest.raises(ValueError, match="함께 쓸 수 없어요"):
        run({"rows": ["g"], "preset": {"kind": "gap", "target": "amt"}, "values": [{"how": "count"}]})


def test_pivot_where_and_convert():
    out = by(run({"rows": ["g"], "values": [{"how": "count", "name": "방문"}, {"how": "count", "where": "amt > 0", "name": "결제"}], "convert": "prev"}))
    assert out["a"]["결제"] == 2 and out["a"]["방문 → 결제 전환율"] == pytest.approx(2 / 3)
    spread = by(run({"rows": ["g"], "column": "k", "how": "count", "convert": "first"}))
    assert spread["a"]["p → q 전환율"] == pytest.approx(0.5) and spread["b"]["p → q 전환율"] == pytest.approx(2.0)
    with pytest.raises(ValueError, match="겹쳐요"):
        run({"rows": ["g"], "values": [{"how": "count", "name": "n"}, {"how": "count", "name": "N"}]})


def test_pivot_share_reach_total_row():
    share = by(run({"rows": ["g"], "how": "sum", "value": "amt", "share": True}))
    assert share["a"]["전체 합계 비중"] == pytest.approx(0.3)
    reach = by(run({"rows": ["g"], "how": "count_distinct", "value": "uid", "share": True}))
    assert reach["a"]["전체 고유 개수 침투율"] == pytest.approx(2 / 3) and reach["b"]["전체 고유 개수 침투율"] == pytest.approx(2 / 3)
    total = run({"rows": ["g"], "how": "count_distinct", "value": "uid", "total_row": True})
    assert total["g"].to_list() == ["a", "b", sql_funcs.TOTAL] and total["전체 고유 개수"].to_list() == [2, 2, 3]  # 총계는 다시 센 3(2 + 2가 아님)


def test_pivot_total_pct_and_lift():
    pct = by(run({"rows": ["g"], "column": "h", "how": "sum", "value": "amt", "show": "total_pct"}))
    assert (pct["a"]["전체 합계"], pct["a"]["x"], pct["b"]["y"]) == pytest.approx((0.3, 0.2, 0.4))
    lift = by(run({"rows": ["g"], "column": "h", "how": "count", "show": "lift"}))
    assert (lift["a"]["x"], lift["a"]["y"], lift["a"]["전체 개수"]) == pytest.approx((4 / 3, 2 / 3, 3))
    with pytest.raises(ValueError, match="열로 펼칠 컬럼"):
        run({"rows": ["g"], "how": "count", "show": "lift"})
    with pytest.raises(ValueError, match="개수·합계에만"):
        run({"rows": ["g"], "column": "h", "how": "mean", "value": "amt", "show": "lift"})


def test_pivot_parent_share():
    plain = run({"rows": ["g", "h"], "how": "sum", "value": "amt", "parent": True})
    got = {(r["g"], r["h"]): r["전체 합계 부모 비중"] for r in plain.iter_rows(named=True)}
    assert got[("a", "x")] == pytest.approx(2 / 3) and got[("b", "x")] == pytest.approx(3 / 7)
    rolled = run({"rows": ["g", "h"], "how": "sum", "value": "amt", "parent": True, "subtotals": "rollup"})
    got = {(r["g"], r["h"]): r["전체 합계 부모 비중"] for r in rolled.iter_rows(named=True)}
    assert got[("b", sql_funcs.SUBTOTAL)] == pytest.approx(0.7) and got[(sql_funcs.TOTAL, sql_funcs.TOTAL)] == pytest.approx(1.0)


def test_pivot_option_errors():
    cases = [({"rows": ["g"], "how": "count", "parent": True}, "둘 이상"),
             ({"rows": [], "how": "count", "total_row": True}, "행 기준 컬럼이 있어야"),
             ({"rows": ["g"], "how": "count", "total_row": True, "subtotals": "rollup"}, "소계 없이"),
             ({"rows": [], "how": "count", "pareto": True}, "파레토 누적은 행 기준"),
             ({"rows": ["g"], "how": "count", "pareto": True, "subtotals": "rollup"}, "소계·총계 줄과 함께"),
             ({"rows": ["g"], "how": "mean", "value": "amt", "pareto": True}, "파레토 누적은 개수·합계"),
             ({"rows": ["g"], "how": "count", "share": "yes"}, "참·거짓")]
    for spec, text in cases:
        with pytest.raises(ValueError, match=text):
            run(spec)


def test_polars_pivot_options():
    pt = ibis.polars.connect().create_table("t", T)
    assert by(run({"rows": ["g"], "how": "count_distinct", "value": "uid", "share": True}, pt))["a"]["전체 고유 개수 침투율"] == pytest.approx(2 / 3)
    assert by(run({"rows": ["g"], "column": "h", "how": "count", "show": "lift"}, pt))["a"]["x"] == pytest.approx(4 / 3)
    assert run({"rows": ["g"], "how": "count_distinct", "value": "uid", "total_row": True}, pt)["전체 고유 개수"].to_list() == [2, 2, 3]
    parent = run({"rows": ["g", "h"], "how": "sum", "value": "amt", "parent": True}, pt)
    assert {(r["g"], r["h"]): r["전체 합계 부모 비중"] for r in parent.iter_rows(named=True)}[("a", "x")] == pytest.approx(2 / 3)
    assert by(run({"rows": ["g"], "preset": {"kind": "volume", "key": "uid"}}, pt))["a"][crosstab.DUP] == pytest.approx(1.5)


# ---------- 결과 요약 (수정) ----------

def test_summary_extras():
    amt, uid = result.get_summary(TT, ["amt", "uid"], extra=["dup", "gap", "quantiles", "zero"], ps=(0.5,))
    assert amt["dup"] == pytest.approx(6 / 5) and uid["dup"] == pytest.approx(2)
    assert amt["gap"] == pytest.approx((100 / 6 - amt["median"]) / amt["median"]) and amt["P50"] is not None
    assert (amt["above"], amt["above_mean"]) == (4, pytest.approx(25))
    assert uid["median"] is None and uid["gap"] is None and uid["above"] is None
    with pytest.raises(ValueError, match="요약 묶음은"):
        result.get_summary(TT, ["amt"], extra=["nope"])


# ---------- 지원 판정 (수정) ----------

def test_agg_unsupported_sees_new_measures(monkeypatch):
    monkeypatch.setattr(source, "compiles", lambda e: False)
    assert querying.agg_unsupported({"pivot": {"values": [{"how": "median", "value": "amt"}]}}, TT)
    assert querying.agg_unsupported({"pivot": {"preset": {"kind": "gap", "target": "amt"}}}, TT)
    assert querying.agg_unsupported({"summary_extra": ["quantiles"], "summary_columns": ["amt"]}, TT)
    monkeypatch.setattr(source, "compiles", lambda e: True)
    assert not querying.agg_unsupported({"pivot": {"preset": {"kind": "gap", "target": "amt"}}, "summary_extra": ["gap"]}, TT)


# ---------- 기간별 표 ----------

@pytest.fixture
def shop(client, tmp_path):
    """t(위 T)와 orders(d 날짜, uid): 1월 u1·u2, 3월 u1·u3(u1이 두 달에 걸친다), 날짜 빈 행 u9."""
    path = tmp_path / "p8.duckdb"
    raw = duckdb.connect(str(path))
    raw.register("t_rows", T.to_arrow())
    raw.execute("create table t as select * from t_rows")
    raw.execute("""create table orders as select d::date as d, uid from (values ('2026-01-05', 'u1'), ('2026-01-20', 'u2'),
                   ('2026-03-03', 'u1'), ('2026-03-30', 'u3'), (null, 'u9')) v(d, uid)""")
    raw.close()
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    return client


USERS = {"base": "orders", "date": "d", "label": "고객", "how": "count_distinct", "column": "uid"}
ORDERS = {"base": "orders", "date": "d", "label": "주문", "how": "count"}


def test_period_column_name():
    assert period_table.column_name({"label": "고객"}, None) == "고객"
    assert period_table.column_name({"label": "고객", "split": "kind"}, "a") == "고객 · a"
    assert period_table.column_name({"label": "고객", "split": "kind"}, None) == f"고객 · {period_table.EMPTY}"


def test_period_whole(shop):
    spec = period_table.check({"unit": "month", "tables": [USERS, ORDERS]})
    assert period_table.whole(spec, spec["tables"][0]) == {"고객": 3}
    with pytest.raises(ValueError, match="고유 개수에만"):
        period_table.whole(spec, spec["tables"][1])


def test_period_build_total(shop):
    got = period_table.build({"unit": "month", "tables": [USERS, ORDERS], "total": True})
    assert got["rows"] == [["2026-01", 2, 2], ["2026-02", 0, 0], ["2026-03", 2, 2]]
    assert got["total"] == [period_table.TOTAL, 3, 4]  # 고객은 다시 센 3(2 + 2가 아님), 주문은 합 4
    assert got["ratios"]["share"] == {"고객": [66.67, 0.0, 66.67], "주문": [50.0, 0.0, 50.0]} and "reach" not in got["ratios"]  # 고유 개수 열은 다시 센 3이 분모
    plain = period_table.build({"unit": "month", "tables": [USERS]})
    assert "total" not in plain and plain["ratios"]["share"]["고객"] == [66.67, 0.0, 66.67]  # total이 없어도 고유 개수 분모는 다시 센 값
    with pytest.raises(ValueError, match="total은 참·거짓"):
        period_table.check({"unit": "month", "tables": [ORDERS], "total": "yes"})


# ---------- 라우트·화면 (수정) ----------

def test_pivot_route_pareto(shop):
    got = shop.post("/api/pivot", json={**BODY, "pivot": {"rows": ["g"], "how": "sum", "value": "amt", "pareto": True}}).get_json()
    rows = got["rows"]["rows"]
    assert [r["g"] for r in rows] == ["b", "a"] and [r[crosstab.CUM_SHARE] for r in rows] == pytest.approx([0.7, 1.0])


def test_summary_route_extras(shop):
    got = shop.post("/api/summary", json={**BODY, "summary_columns": ["amt"], "summary_extra": ["dup", "zero"]}).get_json()["summary"]
    assert got["columns"] == [*result.SUMMARY_COLS, "dup", "above", "above_mean"] and got["rows"][0]["above"] == 4


def test_period_route_total(shop):
    got = shop.post("/api/period_table", json={"unit": "month", "tables": [USERS], "total": True}).get_json()
    assert got["total"] == [period_table.TOTAL, 3] and got["ratios"]["share"]["고객"][0] == 66.67


def test_search_constants_crosstab():
    from query import search_app
    found = {}
    search_app.search_constants(found)
    assert found["calc"]["crosstab"]["quantiles"] == list(crosstab.QUANTILES) and found["calc"]["shows"] == list(calc.SHOWS)
    assert "total_pct" in calc.SHOWS and "lift" in calc.SHOWS


def test_index_html_has_phase8_controls():
    html = (ROOT / "static/query/index.html").read_text(encoding="utf-8")
    for id_ in ("pv-rows2", "pv-preset", "pv-key", "pv-target", "pv-ps", "pv-above", "pv-share", "pv-parent", "pv-total", "pv-pareto", "pv-convert",
                "s-dup", "s-gap", "s-quant", "s-zero", "s-ps", "s-above", "pt-total"):
        assert f'id="{id_}"' in html, id_
    assert "SERVER.calc.crosstab" in html and '"total_pct"' in html and '"lift"' in html and "summary_extra" in html
