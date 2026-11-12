"""Phase 20 묶음 A: 분석 함수의 틀린 숫자 고치기(A/B 사용자 단위·배정 비율·1행 그룹, RFM 동점, 이상 시점의 끝·오염, 구성 효과의 그룹 값)와 지표의 주의 글.
데이터는 시험 안에서 지어내고 씨앗을 고정한다. 명세는 plans/phase-20.md."""
import datetime as dt
import types

import numpy as np
import polars as pl
import pytest
from lazy import ibis

from analysis import insight
from metrics import metric_calc as mc
from metrics import metric_kernels as mk

RUNS = 20  # 씨앗 고정 반복 수
ALPHA = 0.05
DAY0 = dt.date(2026, 1, 5)  # 월요일


def tables(df):
    return [ibis.memtable(df.to_arrow()), ibis.polars.connect().create_table("t", df)]


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


# ---------- A/B ----------

def ab_users(seed, users=200, rows=20):
    """사용자마다 평균이 다르고 그룹 효과는 없는 표(사용자 users명 × rows행)."""
    rng = np.random.default_rng(seed)
    uid = np.repeat(np.arange(users), rows)
    level = np.repeat(rng.normal(10.0, 3.0, users), rows)
    group = np.repeat(rng.permutation(users) % 2, rows)
    return pl.DataFrame({"u": uid, "g": np.where(group == 0, "A", "B"), "x": level + rng.normal(0.0, 1.0, users * rows)})


def test_ab_who_stops_inflated_test():
    hits = 0
    for seed in range(RUNS):
        t = ibis.memtable(ab_users(seed).to_arrow())
        got = insight.ab_test(t, "g", "mean", "x", "A", 95, "u")
        assert got["who"] == "u" and sum(g["n"] for g in got["groups"]) == 200  # 사람 수로 센다
        hits += got["test"]["p"] < ALPHA
    assert hits <= 4
    plain = insight.ab_test(ibis.memtable(ab_users(0).to_arrow()), "g", "mean", "x", "A", 95)
    assert plain["who"] is None and any("사용자 칸을 비워" in n for n in plain["notes"])
    assert sum(g["n"] for g in plain["groups"]) == 4000


WHO = pl.DataFrame({"g": ["A", "A", "A", "A", "B", "B", "B", "B", "A", None],
                    "u": ["u1", "u1", "u2", "u3", "u4", "u4", "u5", "u1", None, "u9"],
                    "hit": [0, 1, 0, 1, 0, 0, 1, 1, 1, 1],
                    "x": [1.0, 2.0, 5.0, 4.0, 2.0, 2.5, 6.0, 3.0, 9.0, 9.0]})


@pytest.mark.parametrize("t", tables(WHO))
def test_ab_who_groups_users_first(t):
    rate = insight.ab_test(t, "g", "rate", "hit", "A", 95, "u")  # 비율은 사용자별 최댓값: A는 u1 1·u2 0·u3 1, B는 u4 0·u5 1·u1 1
    assert [(g["name"], g["n"], g["sum"]) for g in rate["groups"]] == [("A", 3, 2), ("B", 3, 2)]
    assert rate["excluded"] == 2  # 사용자가 빈 행 하나, 그룹이 빈 행 하나
    assert any("두 그룹" in n and "1명" in n for n in rate["notes"])  # u1이 두 그룹에 걸쳐 있다
    assert not any("사용자 칸을 비워" in n for n in rate["notes"])
    mean = insight.ab_test(t, "g", "mean", "x", "A", 95, "u")  # 평균은 사용자별 합계: A 3·5·4, B 4.5·6·3
    assert [(g["name"], g["n"], g["mean"]) for g in mean["groups"]] == [("A", 3, pytest.approx(4.0)), ("B", 3, pytest.approx(4.5))]
    for who in ("g", "x"):
        with pytest.raises(ValueError, match="다른 컬럼"):
            insight.ab_test(t, "g", "mean", "x", "A", 95, who)
    with pytest.raises(ValueError, match="없음"):
        insight.ab_test(t, "g", "mean", "x", "A", 95, "없음")


def test_ab_drops_group_with_one_value():
    t = mem(g=["A"] * 5 + ["B"] * 5 + ["C"], x=[1.0, 2, 3, 4, 5, 2, 3, 4, 5, 6, 7])
    got = insight.ab_test(t, "g", "mean", "x", "A", 95)
    assert [g["name"] for g in got["groups"]] == ["A", "B"] and got["test"]["kind"] == "welch"
    assert got["excluded"] == 1 and "그룹 'C'은 값이 1개뿐이라 뺐어요." in got["notes"]
    with pytest.raises(ValueError, match="두 개 이상"):
        insight.ab_test(mem(g=["A"] * 5 + ["C"], x=[1.0, 2, 3, 4, 5, 7]), "g", "mean", "x", None, 95)


def test_ab_notes_sample_ratio_mismatch():
    rng = np.random.default_rng(0)
    skew = mem(g=["A"] * 100 + ["B"] * 900, x=rng.normal(0.0, 1.0, 1000))
    assert any("배정 비율 검사(SRM)" in n for n in insight.ab_test(skew, "g", "mean", "x", "A", 95)["notes"])
    even = mem(g=["A"] * 500 + ["B"] * 500, x=rng.normal(0.0, 1.0, 1000))
    assert not any("SRM" in n for n in insight.ab_test(even, "g", "mean", "x", "A", 95)["notes"])


# ---------- RFM ----------

def tied_frame():
    """사용자 100명: 1회 80명, 2회 15명, 5회 5명. 마지막 구매일은 사용자마다 다르고 자주 산 사람일수록 최근이다."""
    counts = [1] * 80 + [2] * 15 + [5] * 5
    uid = [f"u{i:03d}" for i, n in enumerate(counts) for _ in range(n)]
    ts = [dt.datetime(2026, 1, 1) + dt.timedelta(days=i) for i, n in enumerate(counts) for _ in range(n)]
    return pl.DataFrame({"uid": uid, "ts": ts, "amt": [10.0] * len(uid)})


@pytest.mark.parametrize("t", tables(tied_frame()))
def test_rfm_ties_use_distinct_levels(t):
    got = insight.rfm(t, "uid", "ts", "amt")
    assert got["levels"] == {"r": 5, "f": 3, "m": 3}
    champ = next(s for s in got["segments"] if s["segment"] == "챔피언")
    assert champ["users"] == 5 and champ["frequency"] == pytest.approx(5.0)  # 2회 구매자(F 3점)는 챔피언이 아니다
    by_f = [sum(row[f] for row in got["grid"]) for f in range(insight.RFM_BINS)]
    assert by_f == [80, 0, 15, 0, 5]  # 1회 1점, 2회 3점, 5회 5점
    assert len(got["notes"]) == 2 and any("빈도" in n and "3단계" in n and "1·3·5점" in n for n in got["notes"])


def test_rfm_ties_ask_distinct_values_once(monkeypatch):
    from analysis import stats
    rows, asked = stats.rows, []
    monkeypatch.setattr(stats, "rows", lambda expr, *a, **k: (asked.append(expr.columns), rows(expr, *a, **k))[1])
    got = insight.rfm(tables(tied_frame())[0], "uid", "ts", "amt")
    assert got["levels"] == {"r": 5, "f": 3, "m": 3}  # 두 축이 겹쳤어도
    assert [c for c in asked if tuple(c) == ("k", "v")] == [("k", "v")]  # 고유값 조회는 하나
    assert insight.READS["rfm"] == 6  # 읽는 횟수 안내는 그 조회까지 센다


def test_rfm_spread_data_is_unchanged():
    counts = list(range(1, 11))
    df = pl.DataFrame({"uid": [f"u{i}" for i, n in enumerate(counts) for _ in range(n)],
                       "ts": [dt.datetime(2026, 1, 1 + i, 9) for i, n in enumerate(counts) for _ in range(n)], "amt": [10.0] * sum(counts)})
    got = insight.rfm(ibis.memtable(df.to_arrow()), "uid", "ts", "amt")
    assert got["levels"] == {"r": 5, "f": 5, "m": 5} and got["notes"] == []
    assert got["cuts"]["f"] == pytest.approx([2.8, 4.6, 6.4, 8.2])
    assert {s["segment"]: s["users"] for s in got["segments"]} == {"챔피언": 4, "관심 필요": 2, "휴면 직전": 2, "이탈": 2}


def test_tied_levels_rule():
    def never():
        raise AssertionError("겹침이 없으면 고유값을 묻지 않는다")
    assert mc.tied_levels([2.8, 4.6, 6.4, 8.2], never, 5) == ([2.8, 4.6, 6.4, 8.2], [1, 2, 3, 4, 5])
    assert mc.tied_levels([1.0, 1.0, 1.0, 1.2], lambda: [1.0, 2.0, 5.0], 5) == ([1.5, 3.5], [1, 3, 5])
    assert mc.tied_levels([1.0, 1.0, 1.0, 1.0], lambda: [1.0, 2.0], 5) == ([1.5], [1, 5])
    assert mc.tied_levels([7.0, 7.0, 7.0, 7.0], lambda: [7.0], 5) == ([], [3])
    assert mc.tied_levels([1.0, 1.0, 2.0, 4.0], lambda: [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 5) == ([1.0, 2.0, 4.0], [1, 2, 4, 5])  # 고유값이 많으면 겹친 경계만 합친다


def test_quintile_ties():
    f = np.array([1.0] * 80 + [2.0] * 15 + [5.0] * 5)
    got = mk.quintile(f)
    assert (got[0], got[80], got[99]) == (1, 3, 5)
    low = mk.quintile(f, True)
    assert (low[0], low[80], low[99]) == (5, 3, 1)
    spread = np.arange(1.0, 11.0)
    assert mk.quintile(spread).tolist() == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5] and mk.quintile(spread, True).tolist() == [5, 5, 4, 4, 3, 3, 2, 2, 1, 1]


# ---------- 이상 시점 ----------

def noisy_days(seed, spike=None, days=90):
    rng = np.random.default_rng(seed)
    dates = [DAY0 + dt.timedelta(days=i) for i in range(days)]
    values = np.where(np.arange(days) % 7 >= 5, 110.0, 100.0) + rng.normal(0.0, 5.0, days)
    if spike is not None:
        values[spike] *= 3
    return pl.DataFrame({"period": [d.isoformat() for d in dates], "value": values})


def test_seasonal_parts_catches_spike_on_last_day():
    for seed in range(RUNS):
        assert insight.seasonal_parts(noisy_days(seed, 89), "day", 7, insight.MAD_K)["flag"][89]
    ends = insight.seasonal_parts(noisy_days(0), "day", 7, insight.MAD_K)
    assert ends["trend"].null_count() == 0 and ends["remainder"].null_count() == 0  # 양 끝도 있는 칸만으로 추세를 구한다


def test_seasonal_parts_catches_middle_spike_without_false_flags():
    extra = 0
    for seed in range(RUNS):
        flags = insight.seasonal_parts(noisy_days(seed, 45), "day", 7, insight.MAD_K)["flag"].to_list()
        assert flags[45]
        extra += sum(flags) - 1
    assert extra / RUNS <= 1


def test_seasonal_parts_quiet_series_stays_quiet():
    quiet = lambda q: sum(insight.seasonal_parts(noisy_days(seed), "day", 7, insight.MAD_K, q)["flag"].sum() for seed in range(RUNS)) / RUNS
    assert quiet(insight.SCALE_Q) <= 0.6  # 기본 흩어짐 기준(상위 90%)
    assert quiet(insight.MIN_SCALE_Q) > 1  # 사용자가 0.5(보정 MAD)로 적으면 기준선이 낮아져 표시가 늘어난다


def test_seasonal_scale_quantile_is_checked_and_returned():
    frame = noisy_days(0, 45)
    t = ibis.memtable(frame.with_columns(pl.col("period").str.to_date()).to_arrow())
    got = insight.seasonal(t, "period", "day", "value", "sum")
    assert got["q"] == insight.SCALE_Q and "2026-02-19" in got["flags"] or got["flags"]
    low = insight.seasonal(t, "period", "day", "value", "sum", None, None, None, insight.MIN_SCALE_Q)
    assert low["q"] == insight.MIN_SCALE_Q and low["limit"] == pytest.approx(insight.MAD_K * low["mad"] / 0.6745, rel=1e-3)  # 0.5면 보정 MAD
    for bad in (0.4, 1.0, True, "0.9"):
        with pytest.raises(ValueError, match="흩어짐 기준 분위"):
            insight.seasonal(t, "period", "day", "value", "sum", None, None, None, bad)


# ---------- 요인 분해 ----------

def test_mix_parts_group_effect_is_centered():
    got = insight.mix_parts({"A": (50, 10.0), "B": (50, 100.0)}, {"A": (60, 10.0), "B": (40, 100.0)})
    assert got["mix"] == pytest.approx(-9.0) and got["rate"] == 0.0 and got["interaction"] == 0.0
    assert got["groups"]["A"]["mix"] == pytest.approx(-4.5) and got["groups"]["B"]["mix"] == pytest.approx(-4.5)
    assert got["after"] - got["before"] == pytest.approx(-9.0)


# ---------- 지표의 주의 글 ----------

def ctx():
    return types.SimpleNamespace(notes=[])


def test_recurring_warns_about_one_off_payments(folder):
    c = ctx()
    t = mem(ts=[dt.datetime(2026, 1, 5), dt.datetime(2026, 2, 5)], amt=[100.0, 200.0])
    mk.recurring(t, {"time": "ts", "amount": "amt"}, {"pick": "mrr"}, c)
    assert "금액 컬럼의 월 합계예요. 일회성 결제가 섞여 있으면 반복 매출보다 크게 나와요. 구독 결제만 조건으로 남겨 주세요." in c.notes


def test_capped_fetches_say_not_random(folder, monkeypatch):
    monkeypatch.setattr(mc, "MAX_FETCH_ROWS", 2)
    c = ctx()
    users = mem(u=[1, 2, 3], ts=[dt.datetime(2026, 1, 1), dt.datetime(2026, 1, 2), dt.datetime(2026, 1, 9)])
    mk.survival(users, {"user": "u", "time": "ts", "churn_days": 3}, {}, c)
    assert "DB가 돌려준 앞의 2명만 썼어요(무작위가 아니에요). 기간·조건으로 좁혀 주세요." in c.notes
    c = ctx()
    t = mem(q=[1, 1, 2], r=[1, 2, 1], x=[1, 0, 1])
    mk.pairs(t, {"y_true": "x", "y_pred": "r"}, ["y_true", "y_pred"], c)
    assert c.notes == ["DB가 돌려준 앞의 2행만 썼어요(무작위가 아니에요). 기간·조건으로 좁혀 주세요."]
    c = ctx()
    mk.ranking(t, {"qid": "q", "rank": "r", "rel": "x", "k": 2}, {"pick": "ndcg"}, c)
    assert c.notes[:2] == ["DB가 돌려준 앞의 2행만 썼어요(무작위가 아니에요). 기간·조건으로 좁혀 주세요.", "마지막 질의는 일부만 들어 있을 수 있어요."]
    monkeypatch.setattr(mc, "MAX_FETCH_ROWS", 10)
    c = ctx()
    mk.ranking(t, {"qid": "q", "rank": "r", "rel": "x", "k": 2}, {"pick": "ndcg"}, c)
    assert c.notes == []
