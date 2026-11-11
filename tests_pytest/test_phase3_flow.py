"""Phase 3(plans/phase-3.md): 조건 단계 퍼널(AARRR·RARRA, 순서대로·단계마다 따로, 재방문 N일)과 생키 자동 흐름(건너뛰기·합치기·시작~끝·깊이).
데이터는 시험 안에서 지어낸 메모리 표이고 기대값은 손으로 셌다. 새 함수가 스켈레톤이면 실패하는 것이 정상이다.
기존 함수 수정((수정) 절)이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-3 <항목>")로 표시했다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from analysis import stats
from core import dash_store
from core import funnel
from dashboard import dash_calc
from dashboard import flow_pairs

T0 = dt.datetime(2026, 1, 1, 0, 0, 0)


def at(day, hour):
    return T0 + dt.timedelta(days=day, hours=hour)


# ---------- 조건 단계 퍼널 ----------
STAGE_ROWS = [  # 사용자, 행동, 금액, 시각
    ("u1", "signup", 0.0, at(0, 1)), ("u1", "login", 0.0, at(0, 2)), ("u1", "login", 0.0, at(2, 1)), ("u1", "invite", 0.0, at(3, 1)), ("u1", "purchase", 10.0, at(3, 2)),
    ("u2", "signup", 0.0, at(0, 1)), ("u2", "login", 0.0, at(1, 1)), ("u2", "purchase", 5.0, at(1, 2)),
    ("u3", "login", 0.0, at(0, 1)), ("u3", "purchase", 3.0, at(0, 2)),
    ("u4", "signup", 0.0, at(0, 1)), ("u4", "login", 0.0, at(0, 2)), ("u4", "refund", -2.0, at(0, 3)),
]
AARRR = [{"name": "획득", "conds": ["ev = signup"]}, {"name": "활성", "conds": ["ev = login"]}, {"name": "유지", "conds": [], "revisit": 7},
         {"name": "추천", "conds": ["ev = invite"]}, {"name": "수익", "conds": ["amt > 0"]}]


@pytest.fixture(params=["memory", "polars"])
def events(request):
    df = pl.DataFrame(STAGE_ROWS, schema={"uid": pl.String, "ev": pl.String, "amt": pl.Float64, "ts": pl.Datetime("us")}, orient="row")
    return ibis.memtable(df.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("ev_t", df)


def users(found):
    return [s["users"] for s in found["steps"]]


def test_check_stages_rejects_bad_input(events):
    ok = funnel.check_stages(events, "uid", "ts", AARRR, "order", None)
    assert [s["name"] for s in ok[0]] == ["획득", "활성", "유지", "추천", "수익"] and ok[1:] == ("order", None)
    bad = [([], "order", "단계는"), ([{"name": "a", "conds": ["ev = x"]}] * 11, "order", "단계는"),
           ([{"name": "a", "conds": ["ev = x"]}, {"name": "a", "conds": ["ev = y"]}], "order", "같은 이름"),
           ([{"name": "a", "conds": []}], "order", "조건"), ([{"name": "a", "conds": [], "revisit": 0}], "order", "재방문"),
           ([{"name": "a", "conds": [], "revisit": 366}], "order", "재방문"), ([{"name": "a", "conds": [], "revisit": True}], "order", "재방문"),
           ([{"name": "a", "conds": ["ev = x"], "color": "#fff"}], "order", "쓸 수 없는"), ([{"name": "", "conds": ["ev = x"]}], "order", "이름"),
           (AARRR, "both", "세기")]
    for stages, count, words in bad:
        with pytest.raises(ValueError, match=words):
            funnel.check_stages(events, "uid", "ts", stages, count, None)
    with pytest.raises(ValueError, match="날짜"):
        funnel.check_stages(events, "uid", "ev", AARRR, "order", None)


def test_stage_rows_shape(events):
    base = funnel.stage_rows(events, "uid", "ts", AARRR)
    assert set(base.columns) == {"u", "s", "t"}
    got = base.to_pyarrow().to_pylist()
    retained = sorted((r["u"], r["t"]) for r in got if r["s"] == 2)  # 재방문 7일: 첫 활동 날보다 뒤 ~ 7일 안. 같은 날(u4)은 아니다
    assert retained == [("u1", at(2, 1)), ("u1", at(3, 1)), ("u1", at(3, 2)), ("u2", at(1, 1)), ("u2", at(1, 2))]


H, D = 3600, 86400
NAMES = ["획득", "활성", "유지", "추천", "수익"]
# 이웃한 단계 쌍마다 (사람 수, 평균, 중앙값): 앞 단계 처음 시각 → 이 단계 처음 시각(초). 순서대로·따로 모두 이 데이터에서 같다
GAP_WANT = [(3, (H + D + H) / 3, H), (2, (2 * D - H) / 2, (2 * D - H) / 2), (1, D, D), (1, H, H)]


def gap_view(found):
    return [(g["from"], g["to"], g["n"], g["mean"], g["median"]) for g in found["gaps"]]


def test_stage_order_and_each(events):
    order = funnel.stage_compute(events, "uid", "ts", AARRR, "order")
    assert users(order) == [3, 3, 2, 1, 1] and order["count"] == "order" and order["window"] is None and order["trim"] == funnel.DEFAULT_TRIM
    assert order["steps"][1]["drop"] == 0 and order["steps"][2]["drop"] == 1
    each = funnel.stage_compute(events, "uid", "ts", AARRR, "each")
    assert users(each) == [3, 4, 2, 1, 3]  # 활성은 가입 안 한 u3도, 수익은 금액 > 0인 u1·u2·u3
    assert [s["drop"] for s in each["steps"]] == [0, 0, 2, 1, 0]  # 앞 단계 조건을 만족했는데 이 단계는 아닌 사람(유지: u3·u4, 추천: u2)
    assert each["steps"][1]["conv"] == 4 / 3


def test_stage_gaps_table(events):
    want = [(a, b, n, m, md) for (a, b), (n, m, md) in zip(zip(NAMES, NAMES[1:]), GAP_WANT)]
    for count in ("order", "each"):
        found = funnel.stage_compute(events, "uid", "ts", AARRR, count, trim=0)
        assert gap_view(found) == want, count
        assert all(g["trimmed"] == g["mean"] for g in found["gaps"]), count  # 자르는 몫 0이면 절사평균 = 평균
    each = funnel.stage_compute(events, "uid", "ts", AARRR, "each")
    assert [s["median_sec"] for s in each["steps"]] == [None, *[g["median"] for g in each["gaps"]]]  # 기존 응답 키는 그대로 채운다
    assert funnel.check_trim(None) == funnel.DEFAULT_TRIM and funnel.check_trim(0.2) == 0.2
    for bad in (-0.1, 0.6, True, "0.1"):
        with pytest.raises(ValueError, match="자르는"):
            funnel.check_trim(bad)


def test_stage_window_and_entry(events):
    assert users(funnel.stage_compute(events, "uid", "ts", AARRR, "order", 86400)) == [3, 3, 1, 0, 0]  # 앞 단계부터 1일 안(경계 포함): u2만 유지까지
    each = funnel.stage_compute(events, "uid", "ts", AARRR, "each", 86400)  # 따로 셀 때는 첫 단계 처음 시각부터 1일 안(가입 안 한 u3은 빠진다)
    assert users(each) == [3, 3, 1, 0, 0] and [s["drop"] for s in each["steps"]] == [0, 0, 2, 1, 0]
    assert users(funnel.stage_compute(events, "uid", "ts", AARRR, "order", None, (dt.date(2026, 1, 2), None))) == [0, 0, 0, 0, 0]  # 첫 단계(가입)만 기간 안
    assert users(funnel.stage_compute(events, "uid", "ts", AARRR, "each", None, (dt.date(2026, 1, 2), dt.date(2026, 1, 3)))) == [0, 2, 2, 0, 1]  # 따로 세면 모든 단계에 기간


def test_value_funnel_gets_gap_table(events):
    found = funnel.compute(events, "uid", "ev", "ts", ["signup", "login"])
    assert gap_view(found) == [("signup", "login", 3, (H + D + H) / 3, H)] and found["steps"][1]["median_sec"] == H  # 기존 median_sec 그대로


def test_span_entry():
    assert stats.span_entry(None) is None and stats.span_entry({"start": "", "end": None}) is None
    assert stats.span_entry({"start": "2026-01-02"}) == (dt.date(2026, 1, 2), None)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        stats.span_entry({"start": "2026/01/02"})


def test_stage_flow_answer(events):
    got = stats.stage_flow(events, "uid", "ts", AARRR, "order", {"n": 1, "unit": "day"}, {"start": "2026-01-01", "end": None}, 0.2)
    assert got["who"] == "uid" and got["time"] == "ts" and got["count"] == "order" and got["stages"] == NAMES and got["trim"] == 0.2
    assert got["window_seconds"] == 86400 and got["range"] == {"start": "2026-01-01", "end": None} and users(got) == [3, 3, 1, 0, 0]
    assert [g["n"] for g in got["gaps"]] == [3, 1, 0, 0]


def test_analyze_knows_stages(events):
    got = stats.analyze(events, {"kind": "stages", "who": "uid", "time": "ts", "stages": AARRR, "count": "each"})
    assert got["kind"] == "stages" and users(got) == [3, 4, 2, 1, 3]


def test_check_card_accepts_stage_funnel():
    src = {"table": "t", "recipe": {"schema": "", "base": "t"}, "full": True, "date": "ts",
           "analysis": {"kind": "stages", "who": "uid", "time": "ts", "stages": AARRR, "count": "order", "window": None}}
    assert dash_store.check_card({"kind": "funnel", "title": "t", "source": src})["source"]["analysis"]["kind"] == "stages"
    with pytest.raises(ValueError, match="흐름 카드"):
        dash_store.check_card({"kind": "funnel", "title": "t", "source": {**src, "analysis": {**src["analysis"], "stages": AARRR * 3}}})


def test_view_funnel_shape():
    got = dash_store.check_view({"funnel_shape": "ladder", "ladder_scale": True, "gap_stats": ["median", "trimmed"], "flow_unlimited": True, "flow_dropout": False})
    assert (got["funnel_shape"], got["ladder_scale"], got["gap_stats"], got["flow_unlimited"], got["flow_dropout"]) == ("ladder", True, ["median", "trimmed"], True, False)
    assert "flow_dropout" not in dash_store.check_view({})  # 정하지 않으면 키가 없다(끝 값을 골랐는지로 정하는 기본)
    for bad, words in (({"funnel_shape": "zigzag"}, "funnel_shape"), ({"ladder_scale": 1}, "ladder_scale"), ({"gap_stats": ["max"]}, "gap_stats"),
                       ({"gap_stats": ["mean", "mean"]}, "gap_stats"), ({"flow_unlimited": "yes"}, "flow_unlimited"), ({"flow_dropout": 0}, "flow_dropout")):
        with pytest.raises(ValueError, match=words):
            dash_store.check_view(bad)


def test_analysis_data_puts_period_on_stage_funnel(monkeypatch):
    seen = {}
    monkeypatch.setattr(dash_calc.prep_app, "analyzed", lambda body: seen.setdefault("how", body["how"]) and {"steps": [], "data": {"full": True}})
    src = {"recipe": {"schema": "", "base": "t"}, "full": True, "date": "ts", "analysis": {"kind": "stages", "who": "uid", "time": "ts", "stages": AARRR, "count": "order"}}
    dash_calc.analysis_data(src, {"range": {"start": "2026-01-02", "end": "2026-01-31"}})
    assert seen["how"]["range"] == {"start": "2026-01-02", "end": "2026-01-31"}


# ---------- 생키 자동 흐름 ----------
S = lambda n: T0 + dt.timedelta(seconds=n)  # noqa: E731
FLOW_ROWS = [  # 사용자, 이벤트, 시각(초). ad·cart는 고르지 않은 값(건너뛴다)
    ("u1", "ad", S(0)), ("u1", "home", S(1)), ("u1", "list", S(2)), ("u1", "list", S(3)), ("u1", "ad", S(4)), ("u1", "item", S(5)), ("u1", "buy", S(6)), ("u1", "home", S(7)),
    ("u2", "list", S(0)), ("u2", "home", S(1)), ("u2", "item", S(2)), ("u2", "ad", S(3)), ("u2", "item", S(4)), ("u2", "cart", S(5)),
    ("u3", "list", S(0)), ("u3", "item", S(1)),
    ("u4", "home", S(0)), ("u4", "home", S(1)), ("u4", "buy", S(2)),
]
PICK = {"starts": ["home"], "middles": ["list", "item"], "ends": ["buy"]}
WANT = {"nodes": [{"name": "home", "step": 1, "users": 3}, {"name": "buy", "step": 2, "users": 1}, {"name": "item", "step": 2, "users": 1}, {"name": "list", "step": 2, "users": 1},
                  {"name": "item", "step": 3, "users": 1}, {"name": "buy", "step": 4, "users": 1}, {"name": flow_pairs.OUT, "step": None, "users": 1}],
        "links": [{"s": 0, "t": 1, "v": 1}, {"s": 0, "t": 2, "v": 1}, {"s": 0, "t": 3, "v": 1}, {"s": 3, "t": 4, "v": 1}, {"s": 4, "t": 5, "v": 1},
                  {"s": 2, "t": 6, "v": 1}]}  # u2: item(2단계) 뒤에 고른 값이 더 없다(고르지 않은 cart만) → 이탈. 끝 값(buy)에 닿은 u1·u4는 이탈 아님
WANT_NO_OUT = {"nodes": WANT["nodes"][:-1], "links": WANT["links"][:-1]}  # 이탈 칸을 끈 카드(view.flow_dropout = false)


def flow_df(rows=FLOW_ROWS):
    return pl.DataFrame(rows, schema={"uid": pl.String, "page": pl.String, "ts": pl.Datetime("us")}, orient="row")


def flow_table(kind, rows=FLOW_ROWS):
    """흐름 시험 표: DuckDB memtable 또는 Polars 연결(창 함수 없는 연결에서도 같은 결과여야 한다)."""
    return ibis.memtable(flow_df(rows).to_arrow()) if kind == "memory" else ibis.polars.connect().create_table("flow_t", flow_df(rows))


KINDS = ["memory", "polars"]


def how(**more):
    return {"user": "uid", "step": "page", "time": "ts", "mode": "auto", **PICK, **more}


def test_check_auto():
    t = ibis.memtable(flow_df().to_arrow())
    assert flow_pairs.check_auto(t, how()) == {"user": "uid", "step": "page", "time": "ts", **PICK, "depth": flow_pairs.AUTO_DEPTH}
    assert flow_pairs.check_auto(t, {"user": "uid", "step": "page", "time": "ts", "mode": "auto"})["starts"] == []  # 비우면 제한 없음
    assert flow_pairs.check_auto(t, how(depth=50))["depth"] == 50
    for bad, words in ((how(depth=1), "깊이"), (how(depth=51), "깊이"), (how(depth=True), "깊이"), (how(starts="home"), "시작"),
                       (how(ends=["home"]), "두 역할"), (how(middles=["x"] * (flow_pairs.MAX_PICKS + 1)), "중간"), (how(time="page"), "날짜")):
        with pytest.raises(ValueError, match=words):
            flow_pairs.check_auto(t, bad)


@pytest.mark.parametrize("kind", KINDS)
def test_auto_walk_skips_merges_and_cuts(kind):
    t = flow_table(kind)
    base = t.select(u=t.uid, e=t.page, t=t.ts)
    got = sorted((r["u"], r["rn"], r["e"], r["nxt"]) for r in flow_pairs.auto_walk(base, ["home"], ["list", "item"], ["buy"], 6).to_pyarrow().to_pylist())
    assert got == [("u1", 0, "home", "list"), ("u1", 1, "list", "item"), ("u1", 2, "item", "buy"), ("u1", 3, "buy", None),  # ad 건너뜀, list 둘은 하나, buy 뒤 home은 자름
                   ("u2", 0, "home", "item"), ("u2", 1, "item", None),  # 시작 값(home) 앞의 list는 뺀다, 끝 값이 없으면 끝까지
                   ("u4", 0, "home", "buy"), ("u4", 1, "buy", None)]  # u3은 시작 값이 없어 빠진다
    short = flow_pairs.auto_walk(base, ["home"], ["list", "item"], ["buy"], 2).to_pyarrow().to_pylist()
    assert max(r["rn"] for r in short) == 1
    assert {(r["u"], r["e"], r["nxt"]) for r in short if r["rn"] == 1} == {("u1", "list", "item"), ("u2", "item", None), ("u4", "buy", None)}  # 깊이 끝 칸도 뒤 값을 미리 본다(잘린 것과 끝난 것을 가른다)


@pytest.mark.parametrize("kind", KINDS)
def test_auto_build_counts_and_repeats(kind):
    t = flow_table(kind)
    assert flow_pairs.auto_build(t, how()) == {"values": WANT}  # 끝 값을 골랐으면 이탈 칸 기본 켬
    assert flow_pairs.auto_build(t, how(), dropout=False) == {"values": WANT_NO_OUT}  # 카드에서 끄면 이탈 칸 없음
    mid = {"user": "uid", "step": "page", "time": "ts", "mode": "auto", "middles": ["home", "list"]}
    again = flow_pairs.auto_build(t, mid)["values"]  # 같은 값이 다른 단계에 다시 나와도 다른 칸. 끝 값을 안 골랐으면 이탈 칸 기본 끔
    plain = {"nodes": [{"name": "home", "step": 1, "users": 2}, {"name": "list", "step": 1, "users": 2}, {"name": "home", "step": 2, "users": 1},
                       {"name": "list", "step": 2, "users": 1}, {"name": "home", "step": 3, "users": 1}],
             "links": [{"s": 0, "t": 3, "v": 1}, {"s": 1, "t": 2, "v": 1}, {"s": 3, "t": 4, "v": 1}]}
    assert again == plain
    assert flow_pairs.auto_build(t, mid, dropout=True)["values"] == {  # 켜면 뒤에 고른 값이 더 없는 사람은 모두 이탈(3차 결정 A)
        "nodes": [*plain["nodes"], {"name": flow_pairs.OUT, "step": None, "users": 4}],
        "links": [*plain["links"], {"s": 0, "t": 5, "v": 1}, {"s": 1, "t": 5, "v": 1}, {"s": 2, "t": 5, "v": 1}, {"s": 4, "t": 5, "v": 1}]}
    cut = flow_pairs.auto_build(t, how(depth=2))["values"]  # 깊이 2: u1(home → list → …)은 뒤에 고른 값이 남아 잘렸을 뿐이라 이탈이 아니다. u2(home → item, 뒤에 cart만)는 이탈
    assert cut == {"nodes": [{"name": "home", "step": 1, "users": 3}, {"name": "buy", "step": 2, "users": 1}, {"name": "item", "step": 2, "users": 1}, {"name": "list", "step": 2, "users": 1},
                             {"name": flow_pairs.OUT, "step": None, "users": 1}],
                   "links": [{"s": 0, "t": 1, "v": 1}, {"s": 0, "t": 2, "v": 1}, {"s": 0, "t": 3, "v": 1}, {"s": 2, "t": 4, "v": 1}]}


@pytest.mark.parametrize("kind", KINDS)
def test_auto_build_period_and_limits(kind, monkeypatch):
    t = flow_table(kind, [*FLOW_ROWS, ("u5", "home", dt.datetime(2026, 2, 1)), ("u5", "buy", dt.datetime(2026, 2, 1, 1))])
    assert flow_pairs.auto_build(t, how(), (dt.date(2026, 1, 1), dt.date(2026, 1, 31))) == {"values": WANT}  # 기간 밖의 u5는 빠진다
    assert flow_pairs.auto_build(t, how())["values"]["nodes"][0] == {"name": "home", "step": 1, "users": 4}
    monkeypatch.setattr(flow_pairs, "MAX_FLOW_LINKS", 2)
    with pytest.raises(ValueError, match="너무 많아"):  # 기본: 넘으면 안내하고 그리지 않는다
        flow_pairs.auto_build(t, how(), (dt.date(2026, 1, 1), dt.date(2026, 1, 31)))
    assert flow_pairs.auto_build(t, how(), (dt.date(2026, 1, 1), dt.date(2026, 1, 31)), unlimited=True) == {"values": WANT}  # 카드에서 '상한 없음'


def test_check_card_accepts_sankey_auto():
    src = {"table": "t", "date": "ts", "body": {"base": "t", "conditions": ["page != ad"]}, "analysis": how()}
    assert dash_store.check_card({"kind": "sankey", "title": "t", "source": src})["source"]["analysis"]["mode"] == "auto"
    with pytest.raises(ValueError, match="source.analysis"):
        dash_store.check_card({"kind": "sankey", "title": "t", "source": {**src, "analysis": how(starts="home")}})


def test_shape_data_sankey_auto(monkeypatch):
    t = ibis.memtable(flow_df().to_arrow())
    monkeypatch.setattr(dash_calc.querying, "runnable", lambda body, select=True, keep=None, **kw: t)
    monkeypatch.setattr(dash_calc.querying, "build", lambda body, select=True, **kw: t)
    card = dash_store.check_card({"kind": "sankey", "title": "t", "source": {"table": "t", "date": "ts", "body": {"base": "t"}, "analysis": how()}})
    got = dash_calc.compute(card, "2026-11-04T09:00:00", {"range": {"start": "2026-01-01", "end": "2026-01-31"}})
    assert got["values"] == WANT and got["date_cols"] == ["ts"]
    monkeypatch.setattr(flow_pairs, "MAX_FLOW_LINKS", 2)
    free = dash_store.check_card({**card, "view": {**card["view"], "flow_unlimited": True}})  # view.flow_unlimited가 auto_build에 간다
    assert dash_calc.compute(free, "2026-11-04T09:00:00", {"range": {"start": "2026-01-01", "end": "2026-01-31"}})["values"] == WANT
    assert dash_calc.definition(free) != dash_calc.definition(card)  # 상한 없음은 다시 구하는 정의(def_hash)에 든다
    monkeypatch.undo()
    monkeypatch.setattr(dash_calc.querying, "runnable", lambda body, select=True, keep=None, **kw: t)
    monkeypatch.setattr(dash_calc.querying, "build", lambda body, select=True, **kw: t)  # undo가 build 바꿔 끼우기도 되돌린다(date_cols가 build를 부른다)
    off = dash_store.check_card({**card, "view": {**card["view"], "flow_dropout": False}})  # view.flow_dropout가 auto_build에 간다
    assert dash_calc.compute(off, "2026-11-04T09:00:00", {"range": {"start": "2026-01-01", "end": "2026-01-31"}})["values"] == WANT_NO_OUT
    assert dash_calc.definition(off) != dash_calc.definition(card)  # 이탈 칸 켜고 끄기도 정의에 든다
