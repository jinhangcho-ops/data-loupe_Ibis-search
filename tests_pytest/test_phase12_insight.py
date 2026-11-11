"""Phase 12: 분석 화면의 새 분석 8가지(analysis/insight.py)와 검정 분포(metrics/metric_calc.chi2_sf·f_sf), 등록(stats.analyze)·전체로 구하기(prep_app.whole)·읽는 횟수 안내(prep_app.reads_times).
메모리 표와 Polars 연결에서 같은 값이 나오는지 본다. 데이터는 테스트 안에서 지어낸다. 명세는 plans/phase-12.md."""
import datetime as dt
import math

import polars as pl
import pytest
from lazy import ibis

from analysis import insight
from analysis import stats
from metrics import metric_calc


def tables(df):
    return [ibis.memtable(df.to_arrow()), ibis.polars.connect().create_table("t", df)]


# ---------- 검정 분포 ----------

def test_chi2_sf_and_f_sf():
    assert metric_calc.chi2_sf(3.841458820694124, 1) == pytest.approx(0.05, abs=1e-9)
    assert metric_calc.chi2_sf(5.991464547107979, 2) == pytest.approx(0.05, abs=1e-9)  # 자유도 2는 exp(-x/2)
    assert metric_calc.chi2_sf(10.0, 2) == pytest.approx(math.exp(-5), rel=1e-9)
    for x in (0.1, 1.0, 7.5, 40.0):
        assert metric_calc.chi2_sf(x, 1) == pytest.approx(math.erfc(math.sqrt(x / 2)), rel=1e-8)
    assert metric_calc.chi2_sf(0.0, 3) == 1.0
    assert metric_calc.chi2_sf(18.307038053275146, 10) == pytest.approx(0.05, abs=1e-8)
    for t, df in ((2.0, 5), (1.3, 40), (3.1, 2.5)):
        assert metric_calc.f_sf(t * t, 1, df) == pytest.approx(metric_calc.t_two_sided_p(t, df), rel=1e-9)  # F(1, d) = t²
    assert metric_calc.f_sf(0.0, 2, 6) == 1.0


# ---------- 코호트 리텐션 ----------

RET = pl.DataFrame({"uid": ["u1", "u1", "u2", "u3", "u3", None],
                    "ts": [dt.datetime(2025, 1, 5), dt.datetime(2025, 2, 3), dt.datetime(2025, 1, 20), dt.datetime(2025, 2, 10), dt.datetime(2025, 3, 31, 12), dt.datetime(2025, 3, 1)]})


@pytest.mark.parametrize("t", tables(RET))
def test_retention_matrix(t):
    got = insight.retention(t, "uid", "ts", "month", 2)
    assert got["periods"] == [0, 1, 2]
    assert [(c["start"], c["size"], c["retained"]) for c in got["cohorts"]] == [("2025-01-01", 2, [1.0, 0.5, 0.0]), ("2025-02-01", 1, [1.0, 1.0, None])]
    assert got["average"] == pytest.approx([1.0, 2 / 3, 0.0])
    assert got["partial"] is False


@pytest.mark.parametrize("args, error", [
    (("uid", "ts", "year", None), "day, week, month"), (("uid", "ts", "month", 0), "1~"), (("uid", "ts", "month", insight.MAX_COHORT_SPAN + 1), "1~"),
    (("uid", "uid", "month", None), "날짜"), (("없음", "ts", "month", None), "없음"),
])
def test_retention_checks(args, error):
    with pytest.raises(ValueError, match=error):
        insight.retention(ibis.memtable(RET.to_arrow()), *args)


def test_retention_refuses_too_many_cohorts():
    df = pl.DataFrame({"uid": [f"u{i}" for i in range(insight.MAX_COHORTS + 1)], "ts": [dt.datetime(2025, 1, 1) + dt.timedelta(days=i) for i in range(insight.MAX_COHORTS + 1)]})
    with pytest.raises(ValueError, match="코호트"):
        insight.retention(ibis.memtable(df.to_arrow()), "uid", "ts", "day", 3)


# ---------- RFM ----------

def rfm_frame():
    uid, ts, amt, tx = [], [], [], []
    for i in range(10):  # 사용자 i: i+1행, 마지막 구매는 2026-01-01 + i일, 한 행에 10, 거래 키는 두 행씩 같다(고유 거래 ceil((i+1)/2)개)
        for j in range(i + 1):
            uid.append(f"u{i}"), ts.append(dt.datetime(2026, 1, 1 + i, 9)), amt.append(10.0), tx.append(f"x{i}-{j // 2}")
    return pl.DataFrame({"uid": uid, "ts": ts, "amt": amt, "tx": tx})


@pytest.mark.parametrize("t", tables(rfm_frame()))
def test_rfm_scores_and_segments(t):
    got = insight.rfm(t, "uid", "ts", "amt")
    assert got["asof"] == "2026-01-10" and got["users"] == 10
    assert got["cuts"]["f"] == pytest.approx([2.8, 4.6, 6.4, 8.2]) and got["cuts"]["r"] == pytest.approx([1.8, 3.6, 5.4, 7.2])
    seg = {s["segment"]: s["users"] for s in got["segments"]}
    assert seg == {"챔피언": 4, "관심 필요": 2, "휴면 직전": 2, "이탈": 2}
    assert [s["segment"] for s in got["segments"]] == ["챔피언", "관심 필요", "휴면 직전", "이탈"]  # RFM_SEGMENTS 차례
    champ = next(s for s in got["segments"] if s["segment"] == "챔피언")
    assert champ["share"] == pytest.approx(0.4) and champ["recency"] == pytest.approx(1.5) and champ["frequency"] == pytest.approx(8.5)
    assert got["grid"][4][4] == 2 and got["grid"][0][0] == 2 and sum(map(sum, got["grid"])) == 10  # grid[R-1][F-1]
    assert got["tx"] is None


@pytest.mark.parametrize("t", tables(rfm_frame()))
def test_rfm_frequency_by_transaction_key(t):
    got = insight.rfm(t, "uid", "ts", "amt", None, "tx")  # F = 고유 거래 수 1,1,2,2,3,3,4,4,5,5
    assert got["tx"] == "tx" and got["cuts"]["f"] == pytest.approx([1.8, 2.6, 3.4, 4.2])
    assert {s["segment"]: s["users"] for s in got["segments"]} == {"챔피언": 4, "관심 필요": 2, "휴면 직전": 2, "이탈": 2}
    assert next(s for s in got["segments"] if s["segment"] == "챔피언")["frequency"] == pytest.approx(4.5)
    with pytest.raises(ValueError, match="다른 컬럼"):
        insight.rfm(t, "uid", "ts", "amt", None, "uid")


def test_rfm_asof_and_checks():
    t = ibis.memtable(rfm_frame().to_arrow())
    early = insight.rfm(t, "uid", "ts", "amt", "2026-01-05")
    assert early["asof"] == "2026-01-05" and early["users"] == 5  # 그날 뒤의 행은 뺀다
    for args, error in ((("uid", "ts", "uid", None), "숫자"), (("uid", "amt", "amt", None), "날짜"), (("uid", "ts", "amt", "2026-13-01"), "YYYY-MM-DD")):
        with pytest.raises(ValueError, match=error):
            insight.rfm(t, *args)


def test_score_up_and_segment_of():
    t = ibis.memtable(pl.DataFrame({"x": [1.0, 2.8, 2.9, 9.0, None]}).to_arrow())
    assert t.select(s=insight.score_up(t.x, [2.8, 4.6, 6.4, 8.2])).to_pyarrow()["s"].to_pylist() == [1, 1, 2, 5, None]
    pairs = [(r, f) for r in range(1, 6) for f in range(1, 6)]
    g = ibis.memtable(pl.DataFrame({"r": [p[0] for p in pairs], "f": [p[1] for p in pairs]}).to_arrow())
    names = g.select(n=insight.segment_of(g.r, g.f)).to_pyarrow()["n"].to_pylist()
    assert None not in names and names[pairs.index((5, 5))] == "챔피언" and names[pairs.index((1, 1))] == "이탈" and names[pairs.index((3, 5))] == "충성 고객"


# ---------- 파레토·ABC ----------

PAR = pl.DataFrame({"item": ["p1", "p1", "p2", "p3", "p4", "p5", None], "v": [30.0, 20.0, 30.0, 15.0, 5.0, -3.0, 1.0]})


@pytest.mark.parametrize("t", tables(PAR))
def test_pareto_abc_grades(t):
    got = insight.pareto_abc(t, "item", "v", "sum")
    rows = [(r["item"], r["grade"]) for r in got["rows"]]
    assert rows[:4] == [("p1", "A"), ("p2", "A"), ("p3", "B"), ("p4", "C")]
    assert (got["dropped"], got["excluded"]) == (1, 1)  # 합계가 0 이하인 항목(p5), 항목이 빈 행
    assert {c["grade"]: c["items"] for c in got["classes"]} == {"A": 2, "B": 1, "C": 1}
    assert got["total"] == pytest.approx(100.0)
    narrow = insight.pareto_abc(t, "item", "v", "sum", 0.5, 0.9)
    assert [r["grade"] for r in narrow["rows"][:4]] == ["A", "B", "B", "C"]
    counted = insight.pareto_abc(t, "item", None, "count")
    assert counted["rows"][0] == {**counted["rows"][0], "item": "p1", "v": 2}


def test_pareto_checks():
    t = ibis.memtable(PAR.to_arrow())
    for args, error in ((("item", "v", "mean"), "sum, count"), (("item", "item", "sum"), "숫자"), (("item", "v", "sum", 0.9, 0.8), "A 경계"), (("item", "v", "sum", 0.0, 0.8), "A 경계")):
        with pytest.raises(ValueError, match=error):
            insight.pareto_abc(t, *args)


def test_abc_grades_frame():
    frame = pl.DataFrame({"item": ["a", "b", "c"], "v": [6.0, 3.0, 1.0]})
    got = insight.abc_grades(frame, 0.6, 0.9)
    assert got["share"].to_list() == pytest.approx([0.6, 0.3, 0.1])
    assert got["grade"].to_list() == ["A", "B", "C"]


# ---------- 두 범주 관계 검정 ----------

CHI = pl.DataFrame({"a": ["a"] * 30 + ["b"] * 70 + [None], "b": ["x"] * 10 + ["y"] * 20 + ["x"] * 30 + ["y"] * 40 + ["x"]})


@pytest.mark.parametrize("t", tables(CHI))
def test_chi_square_values(t):
    got = insight.chi_square(t, "a", "b")
    chi2 = 4 / 12 + 4 / 18 + 4 / 28 + 4 / 42
    assert (got["rows"], got["cols"], got["n"], got["excluded"]) == (["a", "b"], ["x", "y"], 100, 1)
    assert got["observed"] == [[10, 20], [30, 40]] and [v for row in got["expected"] for v in row] == pytest.approx([12, 18, 28, 42])  # approx는 겹친 목록을 받지 않는다
    assert (got["chi2"], got["df"]) == (pytest.approx(chi2), 1)
    assert got["p"] == pytest.approx(math.erfc(math.sqrt(chi2 / 2)), rel=1e-8)
    assert got["v"] == pytest.approx(math.sqrt(chi2 / 100))
    assert (got["low_share"], got["warn"]) == (0.0, False)


def test_chi_stats_low_expected_and_checks():
    got = insight.chi_stats([[1, 2], [3, 30]])
    assert got["low_share"] == pytest.approx(0.75) and got["warn"] is True  # 기대 빈도 0.33·2.67·3.67 < 5
    with pytest.raises(ValueError, match="2개 이상"):
        insight.chi_stats([[1, 2]])
    t = ibis.memtable(pl.DataFrame({"a": [str(i) for i in range(insight.MAX_LEVELS + 1)] * 2, "b": ["x", "y"] * (insight.MAX_LEVELS + 1)}).to_arrow())
    with pytest.raises(ValueError, match=str(insight.MAX_LEVELS)):
        insight.chi_square(t, "a", "b")
    with pytest.raises(ValueError, match="다른 컬럼"):
        insight.chi_square(t, "a", "a")


# ---------- A/B 결과 검정 ----------

AB = pl.DataFrame({"g": ["A"] * 100 + ["B"] * 100 + [None], "hit": [1] * 10 + [0] * 90 + [1] * 20 + [0] * 80 + [1],
                   "x": [float(i % 7) for i in range(100)] + [float(i % 7) + 0.5 for i in range(100)] + [9.0]})


@pytest.mark.parametrize("t", tables(AB))
def test_ab_rate_two_groups(t):
    got = insight.ab_test(t, "g", "rate", "hit", "A", 95)
    z = metric_calc.two_proportion_z(10, 100, 20, 100)
    ci = metric_calc.diff_lift_ci(0.1, 0.1 * 0.9 / 100, 0.2, 0.2 * 0.8 / 100, 0.95)
    assert got["control"] == "A" and got["test"]["kind"] == "z" and got["test"]["p"] == pytest.approx(z["p"])
    pair = got["pairs"][0]
    assert pair["name"] == "B" and pair["diff"] == pytest.approx(0.1) and pair["ci"] == pytest.approx(list(ci["diff"])) and pair["lift_ci"] == pytest.approx(list(ci["lift"]))
    assert got["excluded"] == 1 and got["small"] is False


@pytest.mark.parametrize("t", tables(AB))
def test_ab_mean_welch(t):
    got = insight.ab_test(t, "g", "mean", "x", None, 90)
    a, b = AB.filter(pl.col("g") == "A")["x"], AB.filter(pl.col("g") == "B")["x"]
    w = metric_calc.welch_from_stats(a.mean(), a.std(), 100, b.mean(), b.std(), 100)
    assert got["control"] == "A"  # 비우면 글자 차례 첫 그룹
    assert got["test"]["kind"] == "welch" and got["test"]["df"] == pytest.approx(w["df"]) and got["test"]["p"] == pytest.approx(w["p"])
    ci = metric_calc.diff_lift_ci(a.mean(), a.std() ** 2 / 100, b.mean(), b.std() ** 2 / 100, 0.9, w["df"])
    assert got["pairs"][0]["ci"] == pytest.approx(list(ci["diff"])) and got["conf"] == 90


def test_ab_three_groups_and_checks():
    df = pl.DataFrame({"g": ["g1"] * 3 + ["g2"] * 3 + ["g3"] * 3, "x": [1.0, 2, 3, 4, 5, 6, 7, 8, 9], "hit": [1, 0, 0, 1, 1, 0, 1, 1, 1]})
    t = ibis.memtable(df.to_arrow())
    got = insight.ab_test(t, "g", "mean", "x", "g1", 95)
    assert got["test"]["kind"] == "anova" and (got["test"]["f"], got["test"]["df1"], got["test"]["df2"]) == (pytest.approx(27.0), 2, 6)
    assert got["test"]["p"] == pytest.approx(metric_calc.f_sf(27.0, 2, 6))
    assert [p["name"] for p in got["pairs"]] == ["g2", "g3"] and all(p["p_adj"] == pytest.approx(min(1.0, p["p"] * 2)) for p in got["pairs"])
    rate = insight.ab_test(t, "g", "rate", "hit", "g1", 95)
    assert rate["test"]["kind"] == "chi2" and rate["test"]["df"] == 2 and rate["small"] is True
    for args, error in ((("g", "rate", "x", "g1", 95), "0 또는 1"), (("g", "mean", "x", "없는 그룹", 95), "대조군"), (("g", "mean", "x", "g1", 100), "신뢰 수준"),
                        (("g", "ratio", "x", "g1", 95), "rate, mean"), (("x", "mean", "x", None, 95), "다른 컬럼")):
        with pytest.raises(ValueError, match=error):
            insight.ab_test(t, *args)


def test_anova_pure():
    got = insight.anova([(3, 2.0, 1.0), (3, 5.0, 1.0), (3, 8.0, 1.0)])
    assert (got["f"], got["df1"], got["df2"]) == (pytest.approx(27.0), 2, 6)
    with pytest.raises(ValueError, match="두 그룹"):
        insight.anova([(3, 2.0, 1.0)])


# ---------- 장바구니 연관 ----------

BAS = pl.DataFrame({"tx": ["t1", "t1", "t1", "t2", "t2", "t2", "t3", "t3", "t4", None], "it": ["a", "b", "a", "a", "b", "c", "a", "c", "b", "a"]})


@pytest.mark.parametrize("t", tables(BAS))
def test_basket_pairs(t):
    got = insight.basket(t, "tx", "it", 0.3, 10)
    assert (got["transactions"], got["min_count"], got["items"]) == (4, 2, 3)
    pairs = [(p["a"], p["b"]) for p in got["pairs"]]
    assert pairs == [("a", "c"), ("a", "b")]  # 향상도 큰 순, bc(1번)는 최소 지지도 아래
    ac = got["pairs"][0]
    assert (ac["n"], ac["support"], ac["conf_ab"], ac["conf_ba"], ac["lift"]) == (2, 0.5, pytest.approx(2 / 3), 1.0, pytest.approx(4 / 3))
    assert got["pairs"][1]["lift"] == pytest.approx(8 / 9) and got["truncated"] is False


def test_basket_checks_and_rules():
    t = ibis.memtable(BAS.to_arrow())
    for args, error in ((("tx", "it", 0, 10), "지지도"), (("tx", "it", 1.5, 10), "지지도"), (("tx", "it", 0.3, 0), f"1~{insight.MAX_BASKET_TOP}"), (("tx", "tx", 0.3, 10), "다른 컬럼")):
        with pytest.raises(ValueError, match=error):
            insight.basket(t, *args)
    rules = insight.pair_rules(pl.DataFrame({"a": ["a", "a"], "b": ["b", "c"], "n": [2, 2]}), pl.DataFrame({"item": ["a", "b", "c"], "n": [3, 3, 2]}), 4, 1)
    assert rules.height == 1 and rules["b"].to_list() == ["c"] and rules["lift"].to_list() == pytest.approx([4 / 3])


# ---------- 지표 변화 요인 분해 ----------

def decomp_frame():
    def part(day, g, n, ones):
        return {"d": [day] * n, "g": [g] * n, "v": [1.0] * ones + [0.0] * (n - ones)}
    parts = [part(dt.date(2026, 1, 5), "x", 50, 10), part(dt.date(2026, 1, 6), "y", 50, 20), part(dt.date(2026, 2, 5), "x", 25, 10), part(dt.date(2026, 2, 6), "y", 75, 45),
             part(dt.date(2026, 3, 1), "x", 9, 9)]  # 3월은 두 기간 밖
    return pl.concat([pl.DataFrame(p) for p in parts])


@pytest.mark.parametrize("t", tables(decomp_frame()))
def test_decompose_sums_to_change(t):
    got = insight.decompose(t, "d", {"start": "2026-01-01", "end": "2026-01-31"}, {"start": "2026-02-01", "end": "2026-02-28"}, "g", "v")
    assert (got["before"], got["after"], got["change"]) == (pytest.approx(0.3), pytest.approx(0.55), pytest.approx(0.25))
    assert (got["mix"], got["rate"], got["interaction"]) == (pytest.approx(0.05), pytest.approx(0.2), pytest.approx(0.0))
    x = next(g for g in got["groups"] if g["group"] == "x")
    assert (x["mix"], x["rate"], x["interaction"], x["total"]) == (pytest.approx(-0.05), pytest.approx(0.1), pytest.approx(-0.05), pytest.approx(0.0))
    assert [s["name"] for s in got["steps"]] == ["기준 기간", "구성 변화", "그룹 안 변화", "상호작용", "비교 기간"]


def test_mix_parts_new_and_gone_groups():
    got = insight.mix_parts({"x": (10, 0.5)}, {"x": (10, 0.5), "z": (10, 1.0)})
    assert (got["mix"], got["rate"], got["interaction"]) == (pytest.approx(0.25), 0.0, 0.0)
    gone = insight.mix_parts({"x": (10, 0.5), "z": (10, 1.0)}, {"x": (10, 0.5)})
    assert gone["mix"] == pytest.approx(-0.25) and gone["groups"]["z"]["mix"] == pytest.approx(-0.5)


def test_decompose_checks():
    t = ibis.memtable(decomp_frame().to_arrow())
    jan, feb = {"start": "2026-01-01", "end": "2026-01-31"}, {"start": "2026-02-01", "end": "2026-02-28"}
    for args, error in ((("d", jan, {"start": "2026-01-20", "end": "2026-02-10"}, "g", "v"), "겹"), (("d", jan, {"start": "2026-02-28", "end": "2026-02-01"}, "g", "v"), "시작"),
                        (("d", jan, feb, "g", "g"), "숫자"), (("g", jan, feb, "g", "v"), "날짜"), (("d", jan, {"start": "2026-04-01", "end": "2026-04-30"}, "g", "v"), "행이 없")):
        with pytest.raises(ValueError, match=error):
            insight.decompose(t, *args)


# ---------- 시계열 분해·이상 시점 ----------

def season_frame():
    days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(70)]
    counts = [10 + (i % 7) + (i * 3 % 5) + (200 if i == 40 else 0) for i in range(70)]  # 요일 효과 + 주기 5의 흔들림(MAD > 0) + 40일째 튀는 값
    return pl.DataFrame({"d": [d for d, n in zip(days, counts) for _ in range(n)]})


@pytest.mark.parametrize("t", tables(season_frame()))
def test_seasonal_flags_spike(t):
    got = insight.seasonal(t, "d", "day", "", "count")
    spike = (dt.date(2026, 1, 1) + dt.timedelta(days=40)).isoformat()
    assert len(got["labels"]) == 70 and got["season"] == "weekday" and got["window"] == insight.SEASON_WINDOWS["day"]
    assert spike in got["flags"]
    i = got["labels"].index(spike)
    rem = [abs(r) for r in got["remainder"] if r is not None]
    assert abs(got["remainder"][i]) == max(rem)
    for v, tr, s, r in zip(got["values"], got["trend"], got["seasonal"], got["remainder"]):
        if tr is not None:
            assert v == pytest.approx(tr + s + r)
    assert got["trend"][:3] == [None] * 3 and got["trend"][-3:] == [None] * 3
    from prep.prep_diag import MAD_C
    assert got["mad"] > 0 and got["limit"] == pytest.approx(insight.MAD_K * got["mad"] / MAD_C)  # 보정 MAD(Phase 5와 같은 MAD_C)
    for label, r in zip(got["labels"], got["remainder"]):
        if r is not None:
            assert (label in got["flags"]) == (abs(r - got["median"]) > got["limit"])


def test_seasonal_parts_and_checks():
    frame = pl.DataFrame({"period": [(dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(21)], "value": [5.0] * 21})
    flat = insight.seasonal_parts(frame, "day", 7, 3.0)
    assert flat["flag"].to_list() == [False] * 21  # 나머지가 모두 같으면(MAD 0) 표시하지 않는다
    t = ibis.memtable(season_frame().to_arrow())
    for kw, error in (({"window": 4}, "홀수"), ({"window": 1}, "홀수"), ({"k": 0}, "배수"), ({"k": insight.MAX_MAD_K + 1}, "배수"), ({"window": 61}, "기간이 적")):
        with pytest.raises(ValueError, match=error):
            insight.seasonal(t, "d", "day", "", "count", None, **kw)


# ---------- 등록·라우트 ----------

def test_analyze_knows_new_kinds():
    t = ibis.memtable(PAR.to_arrow())
    got = stats.analyze(t, {"kind": "pareto", "item": "item", "value": "v", "agg": "sum"})
    assert got["kind"] == "pareto" and got["rows"][0]["item"] == "p1"
    assert set(insight.READS) <= set(stats.KINDS) and set(insight.WHOLE_KINDS) <= set(insight.READS)
    assert stats.LIMITS["whole"] == list(insight.WHOLE_KINDS) and stats.LIMITS["pareto_a"] == insight.PARETO_A


def test_whole_kinds_use_full_table():
    from prep import prep_app
    assert all(prep_app.whole({"how": {"kind": k}}) for k in insight.WHOLE_KINDS)
    assert not prep_app.whole({"how": {"kind": "chisq"}}) and not prep_app.whole({"how": {"kind": "abtest"}})


def test_reads_times():
    import json
    from core import server
    from prep import prep_app
    with server.app.test_request_context():
        ask = server.jsonify(needs_confirm=True, text="DB가 이 테이블을 읽어요(약 1.0 MB) · 기준 0.5 MB", bytes=1048576)
        got = json.loads(prep_app.reads_times(ask, 4).get_data())
    assert got["text"].startswith("DB가 이 테이블을 4번 읽어요") and got["times"] == 4 and got["total_bytes"] == 4 * 1048576
    with server.app.test_request_context():
        once = json.loads(prep_app.reads_times(server.jsonify(needs_confirm=True, text="DB가 이 테이블을 읽어요", bytes=None), 1).get_data())
    assert once["text"] == "DB가 이 테이블을 읽어요" and once["total_bytes"] is None


def test_route_new_kind_runs_full(client, tmp_path):
    import duckdb
    from core import server
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.register("df", BAS.to_arrow())
        raw.execute("create table bas as select * from df")
    server.state.update(con=ibis.duckdb.connect(str(path)), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    got = client.post("/api/analyze", json={"recipe": {"base": "bas"}, "how": {"kind": "basket", "key": "tx", "item": "it", "support": 0.3, "top": 10}}).get_json()
    assert got["kind"] == "basket" and got["data"] == {"full": True} and got["transactions"] == 4
