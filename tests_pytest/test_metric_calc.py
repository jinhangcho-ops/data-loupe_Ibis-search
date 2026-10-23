"""지표 계산 바탕(metric_calc): 세션·동시 수·사건 사이 시간·사용자 상태·코호트·LTV·NRR(Ibis 식), 분포·생존·예측 평가·실험 통계(수식).
데이터는 테스트 안에서 지어내고, 값은 손으로 센 것과 맞춘다."""
import datetime as dt
import math

import numpy as np
import polars as pl
import pytest
from lazy import ibis

import metric_calc as mc

T0 = dt.datetime(2026, 3, 1, 9, 0, 0)


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def at(minutes):
    return T0 + dt.timedelta(minutes=minutes)


# ---------- 세션 ----------

def sessions_table():
    # 사용자 1: 0, 10 | 50, 55 (간격 40분), 사용자 2: 0, 사용자 3: 0, 30(정확히 30분은 같은 세션)
    ev = [(1, 0, "page"), (1, 10, "click"), (1, 50, "page"), (1, 55, "page"), (2, 0, "page"), (3, 0, "page"), (3, 30, "page")]
    return mem(uid=[e[0] for e in ev], ts=[at(e[1]) for e in ev], kind=[e[2] for e in ev])


def test_sessions_by_gap_and_stats():
    t = mc.add_sessions(sessions_table(), "uid", "ts", gap_minutes=30)
    assert t.count().to_pyarrow().as_py() == 7
    stats = mc.session_stats(t, "session", "ts")
    # 세션: u1 {0,10} {50,55}, u2 {0}, u3 {0,30} → 길이 600, 300, 0, 1800초
    assert stats["sessions"] == 4
    assert stats["mean_length"] == pytest.approx((600 + 300 + 0 + 1800) / 4)
    assert stats["median_length"] == pytest.approx((300 + 600) / 2)
    assert (stats["bounces"], stats["bounce_rate"]) == (1, 0.25)


def test_session_bounce_counts_only_selected_events():
    t = mc.add_sessions(sessions_table(), "uid", "ts", gap_minutes=30)
    stats = mc.session_stats(t, "session", "ts", only=lambda x: x.kind == "page")
    # 페이지 이벤트 수: u1 첫 세션 1, 둘째 2, u2 1, u3 2 → 이탈 2/4
    assert (stats["sessions"], stats["bounces"], stats["bounce_rate"]) == (4, 2, 0.5)


def test_sessions_by_key_and_exit_rates():
    ev = [("s1", 0, "home"), ("s1", 1, "list"), ("s1", 2, "detail"), ("s2", 0, "home"), ("s2", 1, "list"), ("s3", 0, "home")]
    t = mem(uid=[1, 1, 1, 2, 2, 3], sid=[e[0] for e in ev], ts=[at(e[1]) for e in ev], page=[e[2] for e in ev])
    keyed = mc.add_sessions(t, "uid", "ts", key=["uid", "sid"])
    assert keyed.session.nunique().to_pyarrow().as_py() == 3
    out = mc.exit_rates(keyed, "session", "ts", "page")
    assert out == [{"page": "home", "views": 3, "exits": 1, "exit_rate": 1 / 3}, {"page": "list", "views": 2, "exits": 1, "exit_rate": 0.5},
                   {"page": "detail", "views": 1, "exits": 1, "exit_rate": 1.0}]


def test_sessions_validation():
    t = sessions_table()
    for bad in (None, 0, -5, True):
        with pytest.raises(ValueError, match="간격"):
            mc.add_sessions(t, "uid", "ts", gap_minutes=bad)
    with pytest.raises(ValueError, match="'nope'"):
        mc.add_sessions(t, "nope", "ts", gap_minutes=30)


# ---------- 동시 수 ----------

def test_concurrency_pcu_acu():
    # A 0~10, B 5~15, C 5~8, D 10~12(A가 끝난 자리에서 시작: 겹치지 않음)
    spans = [(0, 10), (5, 15), (5, 8), (10, 12)]
    t = mem(s=[at(a) for a, _ in spans], e=[at(b) for _, b in spans])
    times, counts = mc.concurrency_series(t, "s", "e")
    assert [int(x) for x in counts] == [1, 3, 2, 2, 1, 0]
    got = mc.concurrency_stats(times, counts)
    assert got["pcu"] == 3
    assert got["acu"] == pytest.approx((1 * 5 + 3 * 3 + 2 * 2 + 2 * 2 + 1 * 3) / 15)
    # 기간을 좁히면(6~11분): 3(6~8) 2(8~10) 2(10~11)
    part = mc.concurrency_stats(times, counts, at(6), at(11))
    assert part["pcu"] == 3 and part["acu"] == pytest.approx((3 * 2 + 2 * 2 + 2 * 1) / 5)


def test_concurrency_open_and_invalid_rows_and_month_peak():
    jan31, feb1 = dt.datetime(2026, 1, 31, 23, 0), dt.datetime(2026, 2, 1, 0, 0)
    t = mem(s=[jan31, feb1, feb1, feb1 + dt.timedelta(hours=5)], e=[feb1 + dt.timedelta(hours=1), feb1 + dt.timedelta(hours=2), None, feb1])  # 마지막 행은 끝 < 시작이라 빠진다
    times, counts = mc.concurrency_series(t, "s", "e")
    assert [int(x) for x in counts] == [1, 3, 2, 1]  # 열린 구간 하나가 끝까지 남는다
    peaks = mc.monthly_peak(times, counts)
    assert peaks == {"2026-01": 1, "2026-02": 3}
    assert mc.concurrency_stats([], [], None, None) == {"pcu": 0, "acu": 0.0}


# ---------- 사건 사이 시간 ----------

def test_time_between_events():
    ev = [(1, "signup", 0), (1, "buy", -50), (1, "buy", 100), (2, "signup", 0), (2, "buy", 500), (2, "buy", 300), (3, "signup", 0), (4, "buy", 10)]
    t = mem(uid=[e[0] for e in ev], ev=[e[1] for e in ev], ts=[T0 + dt.timedelta(seconds=e[2]) for e in ev])
    got = mc.time_between(t, "uid", "ts", "ev", "signup", "buy")
    # 사용자 1: signup 이후 첫 buy는 100초(-50은 앞), 사용자 2: 300초, 사용자 3: buy 없음
    assert got["users_first"] == 3 and got["n"] == 2
    assert (got["mean"], got["median"]) == (200, 200)
    assert got["p90"] == pytest.approx(280)
    assert mc.summarize_seconds([]) == {"n": 0, "mean": None, "median": None, "p90": None}


def test_payback_day():
    assert mc.payback_day([0.2, 0.5, 1.0, 1.4], 1.0) == 2
    assert mc.payback_day([0.2, 0.5], 1.0) is None


# ---------- 사용자 상태 ----------

def months_table():
    ev = [("u1", 1), ("u1", 2), ("u1", 3), ("u2", 1), ("u3", 2), ("u3", 4), ("u4", 3)]
    return mem(uid=[e[0] for e in ev], ts=[dt.date(2026, e[1], 15) for e in ev])


def test_user_states_monthly():
    got = {r["period"]: r for r in mc.user_states(months_table(), "uid", "ts", "month")}
    jan, feb, mar, apr = (got[f"2026-0{m}-01"] for m in (1, 2, 3, 4))
    assert (jan["active"], jan["new"], jan["retained"], jan["resurrected"], jan["churned"], jan["quick_ratio"]) == (2, 2, 0, 0, None, None)
    assert (feb["active"], feb["new"], feb["retained"], feb["resurrected"], feb["churned"]) == (2, 1, 1, 0, 1)
    assert (feb["quick_ratio"], feb["churn_rate"], feb["resurrection_rate"]) == (1.0, 0.5, None)
    assert (mar["new"], mar["retained"], mar["resurrected"], mar["churned"], mar["resurrection_rate"]) == (1, 1, 0, 1, 0.0)
    assert (apr["active"], apr["new"], apr["resurrected"], apr["churned"]) == (1, 0, 1, 2)
    assert (apr["quick_ratio"], apr["churn_rate"], apr["resurrection_rate"]) == (0.5, 1.0, 0.5)


def test_user_states_inactive_days_disagrees_with_bucket_rule():
    # x: 1/10, 3/5 (쉰 기간 49일 끝까지, 돌아올 때 54일 벌어짐) / y: 1/31, 3/2 (2월 말까지 28일, 돌아올 때 30일 벌어짐)
    t = mem(uid=["x", "x", "y", "y"], ts=[dt.date(2026, 1, 10), dt.date(2026, 3, 5), dt.date(2026, 1, 31), dt.date(2026, 3, 2)])
    bucket = {r["period"][:7]: r for r in mc.user_states(t, "uid", "ts", "month")}
    assert (bucket["2026-02"]["churned"], bucket["2026-03"]["resurrected"], bucket["2026-03"]["retained"]) == (2, 2, 0)
    days = {r["period"][:7]: r for r in mc.user_states(t, "uid", "ts", "month", inactive_days=45)}
    feb, mar = days["2026-02"], days["2026-03"]
    assert (feb["active"], feb["churned"], feb["churn_rate"], feb["quick_ratio"]) == (0, 1, 0.5, 0.0)  # y는 28일 < 45일이라 아직 이탈이 아니다
    assert (mar["active"], mar["resurrected"], mar["retained"], mar["churned"], mar["quick_ratio"]) == (2, 1, 1, 0, None)  # y는 30일 < 45일이라 유지
    assert mar["resurrection_rate"] == 1.0 and feb["resurrection_rate"] is None  # 3/1 기준 휴면: x 50일(≥45), y 29일(<45) → 1명, 복귀 1명
    low = {r["period"][:7]: r for r in mc.user_states(t, "uid", "ts", "month", inactive_days=10)}
    assert (low["2026-02"]["churned"], low["2026-03"]["resurrected"]) == (2, 2)  # 기준이 짧으면 버킷 규칙과 같다
    # z(1/1에만 활동)를 더하면 3/1 기준 휴면은 x·z 2명(50일, 59일), 복귀는 x 1명 → 0.5. 기준 10일이면 x·y·z 3명 중 복귀 2명
    t3 = mem(uid=["x", "x", "y", "y", "z"], ts=[dt.date(2026, 1, 10), dt.date(2026, 3, 5), dt.date(2026, 1, 31), dt.date(2026, 3, 2), dt.date(2026, 1, 1)])
    assert {r["period"][:7]: r for r in mc.user_states(t3, "uid", "ts", "month", inactive_days=45)}["2026-03"]["resurrection_rate"] == 0.5
    assert {r["period"][:7]: r for r in mc.user_states(t3, "uid", "ts", "month", inactive_days=10)}["2026-03"]["resurrection_rate"] == pytest.approx(2 / 3)
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="미접속"):
            mc.user_states(t, "uid", "ts", "month", inactive_days=bad)


def test_user_states_fills_empty_periods_and_week_units():
    t = mem(uid=["a", "a"], ts=[dt.date(2026, 3, 2), dt.date(2026, 3, 23)])  # 월요일, 3주 뒤
    weeks = mc.user_states(t, "uid", "ts", "week")
    assert [r["period"] for r in weeks] == ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
    assert [r["active"] for r in weeks] == [1, 0, 0, 1]
    assert weeks[1]["churned"] == 1 and weeks[3]["resurrected"] == 1
    assert [r["active"] for r in mc.user_states(t, "uid", "ts", 30)] == [1, 1]  # 30일 묶음: 2026-03-02와 03-23은 이웃한 묶음
    with pytest.raises(ValueError, match="묶음"):
        mc.user_states(t, "uid", "ts", "year")


# ---------- 코호트 ----------

def cohort_table():
    # u1: d1 d2 d4 / u2: d1 d3 / u3: d2 d3
    ev = [("u1", 1), ("u1", 2), ("u1", 4), ("u2", 1), ("u2", 3), ("u3", 2), ("u3", 3)]
    return mem(uid=[e[0] for e in ev], ts=[dt.date(2026, 5, e[1]) for e in ev])


def by_cohort(result):
    return {c["cohort"]: c for c in result["cohorts"]}


def test_cohort_classic_and_rolling():
    classic = by_cohort(mc.cohort_matrix(cohort_table(), "uid", "ts", "day", "classic", n_max=3))
    assert classic["2026-05-01"]["size"] == 2 and classic["2026-05-01"]["values"] == [1.0, 0.5, 0.5, 0.5]
    assert classic["2026-05-02"]["size"] == 1 and classic["2026-05-02"]["values"] == [1.0, 1.0, 0.0, None]
    rolling = by_cohort(mc.cohort_matrix(cohort_table(), "uid", "ts", "day", "rolling", n_max=3))
    assert rolling["2026-05-01"]["values"] == [1.0, 1.0, 1.0, 0.5]
    assert rolling["2026-05-02"]["values"] == [1.0, 1.0, 0.0, None]


def test_cohort_range_and_curve():
    rng = by_cohort(mc.cohort_matrix(cohort_table(), "uid", "ts", "day", "range", span=(3, 3)))
    assert rng["2026-05-01"]["values"] == [0.5] and rng["2026-05-02"]["values"] == [None]
    both = mc.cohort_matrix(cohort_table(), "uid", "ts", "day", "classic", n_max=3)["cohorts"]
    curve = mc.average_curve(both)
    assert curve == [1.0, pytest.approx(2 / 3), pytest.approx(1 / 3), 0.5]  # N=1: (0.5×2 + 1×1) / 3
    with pytest.raises(ValueError, match="span"):
        mc.cohort_matrix(cohort_table(), "uid", "ts", "day", "range")
    with pytest.raises(ValueError, match="classic"):
        mc.cohort_matrix(cohort_table(), "uid", "ts", "day", "other")


def test_flattening_point():
    curve = [1.0, 0.5, 0.3, 0.2, 0.1995, 0.1992, 0.1990, 0.1990]
    assert mc.flattening_point(curve) == 4  # N=4,5,6에서 차이가 0.5%p 미만이 3번 이어진다
    assert mc.flattening_point([1.0, 0.5, 0.2, 0.1]) is None
    assert mc.flattening_point([1.0, 0.9995, None, 0.999, 0.9985, 0.998]) is None


def test_ltv_and_nrr_and_roas():
    ev = [("u1", 1, 10), ("u1", 3, 20), ("u1", 10, 100), ("u2", 1, 5), ("u3", 2, 7)]
    t = mem(uid=[e[0] for e in ev], ts=[dt.date(2026, 5, e[1]) for e in ev], amt=[e[2] for e in ev])
    two = {r["cohort"]: r for r in mc.ltv(t, "uid", "ts", "amt", 2)}
    assert (two["2026-05-01"]["size"], two["2026-05-01"]["revenue"], two["2026-05-01"]["ltv"]) == (2, 35.0, 17.5)
    assert two["2026-05-02"]["ltv"] == 7.0 and two["2026-05-02"]["complete"] is True
    nine = {r["cohort"]: r for r in mc.ltv(t, "uid", "ts", "amt", 9)}
    assert nine["2026-05-01"]["revenue"] == 135.0 and nine["2026-05-01"]["complete"] is True and nine["2026-05-02"]["complete"] is False
    assert mc.roas(150, 100) == 1.5 and mc.roas(1, 0) is None

    ev2 = [("A", 1, 100), ("B", 1, 100), ("A", 2, 150), ("C", 2, 50), ("A", 3, 90), ("C", 3, 110)]
    m = mem(uid=[e[0] for e in ev2], ts=[dt.date(2026, e[1], 10) for e in ev2], amt=[e[2] for e in ev2])
    got = {r["month"]: r for r in mc.nrr(m, "uid", "ts", "amt")}
    assert sorted(got) == ["2026-02-01", "2026-03-01"]
    assert got["2026-02-01"]["nrr"] == 0.75 and got["2026-03-01"]["nrr"] == 1.0  # (150+0)/200, (90+110)/200


# ---------- 분포 ----------

def test_gini_pareto_quantiles_tiers():
    assert mc.gini([1, 1, 1, 1]) == pytest.approx(0)
    assert mc.gini([0, 0, 0, 1]) == pytest.approx(0.75)
    assert mc.gini([0, 0]) is None and mc.gini([-1, 2]) is None and mc.gini([]) is None
    assert mc.quantiles([1, 2, 3, 4, 5]) == {0.5: 3.0, 0.9: pytest.approx(4.6), 0.99: pytest.approx(4.96)}
    assert mc.quantiles([]) == {}
    assert mc.pareto_share([10, 10, 1, 1, 1, 1, 1, 1, 1, 1]) == pytest.approx(20 / 28)  # 상위 20% = 2개
    assert mc.pareto_share([5, 5, 5], top=0.2) == pytest.approx(1 / 3)  # 올림 → 1개
    tiers = mc.tier_shares([1, 2, 3, 10, 100], whale_min=100, dolphin_min=10)
    assert tiers["whale"] == {"users": 1, "user_share": 0.2, "total": 100.0, "total_share": pytest.approx(100 / 116)}
    assert (tiers["dolphin"]["users"], tiers["minnow"]["users"], tiers["minnow"]["total"]) == (1, 3, 6.0)
    with pytest.raises(ValueError, match="Whale"):
        mc.tier_shares([1], whale_min=1, dolphin_min=5)


def test_fetch_column_nulls_and_sampling():
    t = mem(v=[1.0, None, 3.0] * 100, s=["a"] * 300)
    values, sampled, total = mc.fetch_column(t, "v")
    assert (len(values), sampled, total) == (200, False, 300)
    values, sampled, total = mc.fetch_column(t, "v", limit=50)
    assert sampled and total == 300 and 0 < len(values) <= 50
    with pytest.raises(ValueError, match="숫자"):
        mc.fetch_column(t, "s")


# ---------- 생존 ----------

def test_kaplan_meier_6mp_textbook():
    # 6-MP 백혈병 시험 6-MP 군(Freireich 등 1963; Kleinbaum & Klein 교재, Wikipedia 'Kaplan–Meier estimator'에도 쓰이는 표준 예제).
    # 사건: 6,6,6,7,10,13,16,22,23 / 중도절단: 6,9,10,11,17,19,20,25,32,32,34,35. 교재 값: S(6)=.857 S(7)=.807 S(10)=.753 S(13)=.690 S(16)=.627 S(22)=.538 S(23)=.448
    events = [6, 6, 6, 7, 10, 13, 16, 22, 23]
    censored = [6, 9, 10, 11, 17, 19, 20, 25, 32, 32, 34, 35]
    km = mc.kaplan_meier(events + censored, [True] * len(events) + [False] * len(censored))
    assert km["times"] == [6, 7, 10, 13, 16, 22, 23]
    assert km["at_risk"] == [21, 17, 15, 12, 11, 7, 6] and km["events"] == [3, 1, 1, 1, 1, 1, 1]
    assert [round(s, 3) for s in km["survival"]] == [0.857, 0.807, 0.753, 0.690, 0.627, 0.538, 0.448]
    assert km["median"] == 23


def test_kaplan_meier_edge_cases():
    none = mc.kaplan_meier([5, 6], [False, False])
    assert none["times"] == [] and none["median"] is None
    assert mc.kaplan_meier([1, 2], [True, True])["median"] == 1
    with pytest.raises(ValueError):
        mc.kaplan_meier([1, 2], [True])


def test_kaplan_meier_is_fast_on_many_rows():
    # 정렬 한 번 + 이진 탐색(O(N log N)): 10만 행이 2초 안에 끝나고, 값은 느린 정의 그대로(작은 예제로 대조)
    import time
    rng = np.random.default_rng(7)
    d, e = rng.integers(0, 5000, 100_000), rng.random(100_000) < 0.6
    start = time.perf_counter()
    km = mc.kaplan_meier(d, e)
    assert time.perf_counter() - start < 2
    small_d, small_e = d[:300], e[:300]
    slow = np.cumprod([1 - (small_e & (small_d == t)).sum() / (small_d >= t).sum() for t in np.unique(small_d[small_e])])
    assert mc.kaplan_meier(small_d, small_e)["survival"] == pytest.approx(slow.tolist())
    assert km["survival"][-1] <= km["survival"][0] <= 1


# ---------- 예측 평가 ----------

def test_pr_auc_four_points():
    # 정답 [0,0,1,1], 점수 [.1,.4,.35,.8] → 점수 순 1,0,1,0: P·ΔR = 1×.5 + .5×0 + (2/3)×.5 = 0.8333 (scikit-learn 문서의 average_precision_score 예제와 같은 값)
    assert mc.average_precision([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == pytest.approx(5 / 6)
    assert mc.average_precision([1, 0, 1, 0], [0.5] * 4) == pytest.approx(0.5)  # 같은 점수는 한 번에 넘는다
    assert mc.average_precision([0, 0], [0.1, 0.2]) is None


def test_classification_metrics():
    got = mc.classification_metrics([1, 1, 1, 0, 0, 0], y_pred=[1, 1, 0, 1, 0, 0])
    assert (got["tp"], got["fp"], got["fn"], got["tn"]) == (2, 1, 1, 2)
    assert got["precision"] == pytest.approx(2 / 3) and got["recall"] == pytest.approx(2 / 3) and got["f1"] == pytest.approx(2 / 3)
    assert got["accuracy"] == pytest.approx(4 / 6) and got["pr_auc"] is None
    by_score = mc.classification_metrics([0, 0, 1, 1], score=[0.1, 0.4, 0.35, 0.8], threshold=0.35)
    assert (by_score["tp"], by_score["fp"]) == (2, 1) and by_score["pr_auc"] == pytest.approx(5 / 6)
    assert mc.classification_metrics([0, 0], y_pred=[0, 0])["precision"] is None
    zero = mc.classification_metrics([1, 1, 0, 0], y_pred=[0, 0, 1, 1])  # 맞게 찾은 것이 없다: 정밀도·재현율이 모두 0이면 F1은 0(없음이 아님)
    assert (zero["precision"], zero["recall"], zero["f1"]) == (0.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        mc.classification_metrics([0, 2], y_pred=[0, 1])


def test_ndcg_and_map():
    # 관련도 3,2,3,0,1,2 (Wikipedia 'Discounted cumulative gain' 예제): DCG6 = 6.861, IDCG6 = 7.141, NDCG6 = 0.961
    ranked = [3, 2, 3, 0, 1, 2]
    dcg = 3 + 2 / math.log2(3) + 3 / 2 + 0 + 1 / math.log2(6) + 2 / math.log2(7)
    assert dcg == pytest.approx(6.861, abs=1e-3)
    assert mc.ndcg_at_k(ranked, 6) == pytest.approx(0.961, abs=1e-3)
    assert mc.ndcg_at_k([3, 3, 2, 2, 1, 0], 6) == pytest.approx(1.0)
    assert mc.ndcg_at_k([0, 0], 2) is None
    assert mc.ndcg_at_k([1], 1, all_relevances=[1, 1]) == pytest.approx(1.0)
    assert mc.ap_at_k([1, 0, 1, 0, 1], 5) == pytest.approx((1 + 2 / 3 + 3 / 5) / 3)
    assert mc.ap_at_k([1, 0, 1, 0, 1], 3) == pytest.approx((1 + 2 / 3) / 3)  # 맞는 것 전체 3개, min(3, 3)
    assert mc.ap_at_k([0, 0], 2) is None
    assert mc.mean_defined([1.0, None, 0.5]) == 0.75 and mc.mean_defined([None]) is None
    with pytest.raises(ValueError, match="K"):
        mc.ndcg_at_k([1], 0)


def test_regression_metrics_excludes_zero_actuals():
    got = mc.regression_metrics([100, 0, 200], [110, 5, 180])
    assert got["rmse"] == pytest.approx(math.sqrt(175)) and got["mae"] == pytest.approx(35 / 3)
    assert got["mape"] == pytest.approx(0.1) and (got["n"], got["excluded"]) == (3, 1)
    assert mc.regression_metrics([0, 0], [1, 1])["mape"] is None
    with pytest.raises(ValueError):
        mc.regression_metrics([1], [1, 2])


# ---------- 실험 통계 ----------

def test_normal_functions():
    assert mc.norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert mc.norm_ppf(0.5) == pytest.approx(0, abs=1e-9)
    assert mc.norm_ppf(0.001) == pytest.approx(-3.090232, abs=1e-6)
    assert mc.norm_ppf(0.8) == pytest.approx(0.841621, abs=1e-6)
    for p in (1e-6, 0.01, 0.1, 0.3, 0.5, 0.9, 0.999):
        assert mc.norm_cdf(mc.norm_ppf(p)) == pytest.approx(p, abs=1e-8)
    assert mc.norm_cdf(1.959964) == pytest.approx(0.975, abs=1e-6)
    with pytest.raises(ValueError):
        mc.norm_ppf(1)


def test_lift_and_two_proportion_z():
    assert mc.lift(12, 10) == pytest.approx(0.2) and mc.lift(1, 0) is None
    got = mc.two_proportion_z(100, 1000, 120, 1000)
    # 합친 비율 .11, se = √(.11×.89×.002) = .013993, z = .02/.013993 = 1.4293 → 표준정규표 Φ(1.43)=.9236 → 양측 p ≈ .1529
    assert got["z"] == pytest.approx(1.4293, abs=1e-3) and got["p"] == pytest.approx(0.1529, abs=5e-4)
    assert got["diff"] == pytest.approx(0.02) and got["lift"] == pytest.approx(0.2)
    assert math.erfc(1.959964 / math.sqrt(2)) == pytest.approx(0.05, abs=1e-6)
    same = mc.two_proportion_z(10, 100, 10, 100)
    assert same["z"] == 0 and same["p"] == pytest.approx(1.0)
    assert mc.two_proportion_z(0, 100, 0, 100)["p"] is None
    with pytest.raises(ValueError):
        mc.two_proportion_z(5, 0, 1, 10)


def test_t_distribution_hand_values():
    assert mc.t_two_sided_p(1, 1) == pytest.approx(0.5, abs=1e-12)  # df=1은 코시 분포: p = 1 − (2/π)·atan(1)
    assert mc.t_two_sided_p(2, 2) == pytest.approx(1 - 2 / math.sqrt(6), abs=1e-12)  # df=2: p = 1 − t/√(2+t²)
    assert mc.t_two_sided_p(0, 5) == pytest.approx(1.0)
    assert mc.t_two_sided_p(2.2281, 10) == pytest.approx(0.05, abs=1e-4)  # t표: df=10, 양측 5% 임계값 2.228


def test_welch_t_test_sleep_data_matches_r():
    # Student(1908)의 수면 자료 extra(R 기본 데이터 sleep). R: t.test(extra ~ group, data = sleep)
    #   t = -1.8608, df = 17.776, p-value = 0.07939 (group 1 − group 2 이므로 부호만 반대)
    g1 = [0.7, -1.6, -0.2, -1.2, -0.1, 3.4, 3.7, 0.8, 0.0, 2.0]
    g2 = [1.9, 0.8, 1.1, 0.1, -0.1, 4.4, 5.5, 1.6, 4.6, 3.4]
    got = mc.welch_t_test(g1, g2)
    assert round(got["t"], 4) == 1.8608 and round(got["df"], 3) == 17.776
    assert round(got["p"], 4) == 0.0794 and got["p"] == pytest.approx(0.07939, abs=5e-6)
    assert got["diff"] == pytest.approx(1.58)
    from_stats = mc.welch_from_stats(np.mean(g1), np.std(g1, ddof=1), 10, np.mean(g2), np.std(g2, ddof=1), 10)
    assert from_stats["p"] == pytest.approx(got["p"])
    with pytest.raises(ValueError):
        mc.welch_t_test([1], [1, 2])
    with pytest.raises(ValueError, match="같아"):
        mc.welch_t_test([1, 1], [1, 1])


def test_power_and_mde():
    # 비율 0.5, 집단당 1000명: se = √(.25×2/1000) = .0223607, MDE = (1.959964 + .841621) × se = .062647
    assert mc.mde_two_proportions(0.5, 1000) == pytest.approx(0.062647, abs=2e-6)
    assert mc.power_normal(0.062647, mc.prop_se(0.5, 0.5, 1000, 1000)) == pytest.approx(0.8, abs=1e-4)
    assert mc.power_two_proportions(0.5, 0.5 + 0.062647, 1000) == pytest.approx(0.803, abs=1e-3)  # 실험군 분산이 조금 작아 약간 높다
    assert mc.power_two_proportions(0.5, 0.5, 1000) == pytest.approx(0.05, abs=1e-9)  # 효과가 없으면 검정력 = 유의수준
    assert mc.mde_two_means(sd=1, n1=100) == pytest.approx(2.801585 * math.sqrt(2 / 100), abs=1e-6)
    assert mc.power_two_means(2.801585 * math.sqrt(2 / 100), 1, 100) == pytest.approx(0.8, abs=1e-4)
    assert mc.power_two_proportions(0.1, 0.2, 100, 400) > mc.power_two_proportions(0.1, 0.2, 100, 100)
    with pytest.raises(ValueError):
        mc.mde_normal(1.0, alpha=0)
