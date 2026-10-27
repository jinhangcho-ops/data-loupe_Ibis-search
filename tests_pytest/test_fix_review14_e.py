"""14차 리뷰 E 묶음(지표 사전의 A/B·기간·ClickHouse)의 되돌림 시험. 데이터는 시험 안에서 지어내고, 값은 손으로 푼 식과 견준다."""
import datetime as dt
import math
from statistics import NormalDist

import polars as pl
import pytest
from lazy import ibis

from core import sql_funcs
from metrics import metric_calc as mc
from metrics import metric_catalog as cat

D = dt.datetime
AB = {"실험군": "g", "지표 값": "v", "대조군 값": "c", "실험군 값": "t"}
Z975 = 1.959963984540054
PHI = NormalDist().cdf


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def run(entry_id, slots, table):
    return cat.compute(entry_id, slots, {}, cat.Ctx(table=table), None)


def row(got, name):
    return next(r["value"] for r in got["rows"] if r["name"] == name)


def has(got, text):
    return any(text in n for n in got["notes"])


# ---------- A1 사용자 단위 A/B ----------

def per_user_table():
    """대조군 400명은 한 행씩(40명 구매), 실험군 400명 중 200명은 두 행(사용자 기준 60명 구매, 행 기준 80행 구매)."""
    g, u, v = [], [], []
    for i in range(400):
        g.append("c"), u.append(f"c{i}"), v.append(1 if i < 40 else 0)
    for i in range(400):
        g.append("t"), u.append(f"t{i}"), v.append(1 if i < 60 else 0)
        if i < 200:
            g.append("t"), u.append(f"t{i}"), v.append(1 if i < 20 else 0)
    return mem(g=g, u=u, v=v)


def test_ab_counts_users_when_user_column_given():
    t = per_user_table()
    rows_only = run("pvalue", AB, t)  # 사용자 칸이 없으면 지금처럼 행 단위: 400 대 600 → SRM 경고, 한 행 = 한 사용자 안내
    assert rows_only["parts"] == {"control_n": 400, "treat_n": 600} and has(rows_only, "배정 비율") and has(rows_only, "한 행을 한 사용자로")
    got = run("pvalue", {**AB, "사용자": "u"}, t)
    assert got["parts"] == {"control_n": 400, "treat_n": 400} and not has(got, "배정 비율") and not has(got, "한 행을 한 사용자로")
    # 손 계산: 0.10 대 0.15, 각 400명, 합친 비율 0.125
    z = 0.05 / math.sqrt(0.125 * 0.875 * (2 / 400))
    assert got["value"] == pytest.approx(math.erfc(z / math.sqrt(2)), rel=1e-9) and row(got, "실험군 평균") == pytest.approx(0.15)


def test_ab_sums_amounts_per_user():
    t = mem(g=["c", "c", "t", "t", "t", "t"], u=["a", "b", "x", "x", "x", "y"], v=[10.0, 20.0, 5.0, 5.0, 10.0, 30.0])
    assert run("lift", AB, t)["value"] == pytest.approx((12.5 - 15) / 15)  # 행 단위: 대조 15, 실험 (5+5+10+30)/4
    got = run("lift", {**AB, "사용자": "u"}, t)
    assert got["value"] == pytest.approx((25 - 15) / 15) and got["parts"]["treat_n"] == 2  # 사용자 합계: x=20, y=30 → 25


# ---------- A3 검정력은 기대 효과로 ----------

def test_power_uses_expected_effect():
    conv = [0] * 900 + [1] * 100 + [0] * 880 + [1] * 120
    t = mem(g=["c"] * 1000 + ["t"] * 1000, v=conv)
    observed = run("power", AB, t)
    assert has(observed, "관측 효과 기준")
    mde = run("mde", AB, t)["value"]
    at_mde = run("power", {**AB, "기대 효과": mde}, t)
    assert at_mde["value"] == pytest.approx(0.8, abs=1e-6) and not has(at_mde, "관측 효과 기준")  # MDE와 같은 식: 기대 효과 = MDE면 검정력 0.8
    se = math.sqrt(0.1 * 0.9 * 2 / 1000)  # 손 계산: 대조군 분산으로 두 군
    want = PHI(0.02 / se - Z975) + PHI(-0.02 / se - Z975)
    assert run("power", {**AB, "기대 효과": 0.02}, t)["value"] == pytest.approx(want, abs=1e-6)
    assert run("power", {**AB, "기대 효과": 20, "기대 효과 단위": "%"}, t)["value"] == pytest.approx(want, abs=1e-6)  # 대조군 0.1의 20% = 0.02


def test_power_expected_effect_for_means():
    t = mem(g=["c"] * 5 + ["t"] * 5, v=[1.0, 2.0, 3.0, 4.0, 5.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    se = math.sqrt(2.5) * math.sqrt(2 / 5)
    want = PHI(2 / se - Z975) + PHI(-2 / se - Z975)
    assert run("power", {**AB, "기대 효과": 2}, t)["value"] == pytest.approx(want, abs=1e-6)


# ---------- A4 Welch면 신뢰구간도 t 분포 ----------

def test_welch_interval_uses_t_quantile():
    t = mem(g=["c"] * 5 + ["t"] * 5, v=[1.0, 2.0, 3.0, 4.0, 5.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    got = run("pvalue", AB, t)  # 분산 2.5씩, 5개씩 → 표준오차 1, 자유도 8, t(0.975, 8) = 2.306004 (t 분포표)
    assert row(got, "차이 95% 신뢰구간 아래") == pytest.approx(1 - 2.306004, abs=1e-5)
    assert row(got, "차이 95% 신뢰구간 위") == pytest.approx(1 + 2.306004, abs=1e-5)


def test_welch_interval_agrees_with_p_value():
    import numpy as np
    rng = np.random.default_rng(3)
    a, b = rng.normal(0, 1, 30), rng.normal(0, 1, 30)
    se = math.sqrt(a.var(ddof=1) / 30 + b.var(ddof=1) / 30)
    b = b - b.mean() + a.mean() + 1.99 * se  # z로는 유의, t(자유도 약 58)로는 유의하지 않은 경계
    got = run("pvalue", AB, mem(g=["c"] * 30 + ["t"] * 30, v=list(a) + list(b)))
    assert got["value"] > 0.05 and row(got, "차이 95% 신뢰구간 아래") < 0


def test_t_quantile_hand_values():
    assert mc.t_crit(0.05, 8) == pytest.approx(2.306004, abs=1e-5) and mc.t_crit(0.05, 1) == pytest.approx(12.7062, abs=1e-3)


# ---------- A5 가드레일은 신뢰구간 끝으로, 작은 표본 점검 ----------

def test_guardrail_uses_interval_end():
    t = mem(g=["c"] * 100 + ["t"] * 100, v=[0] * 90 + [1] * 10 + [0] * 95 + [1] * 5)  # Lift −50%
    low = math.exp(math.log(0.5) - Z975 * math.sqrt(0.9 / 10 + 0.95 / 5)) - 1  # 손 계산: Lift 95% 신뢰구간 아래 ≈ −82%
    unsure = run("guardrail", {**AB, "한계(%)": -60}, t)  # 점추정 −50%는 한계 안이지만 신뢰구간 아래 끝이 넘는다
    assert row(unsure, "상태") == "경고" and row(unsure, "Lift 95% 신뢰구간 아래") == pytest.approx(low, abs=1e-6)
    ok = run("guardrail", {**AB, "한계(%)": -90}, t)
    assert row(ok, "상태") == "정상" and "지키고 있어요" in ok["label"]


def test_guardrail_small_sample_is_on_hold():
    t = mem(g=["c"] * 5 + ["t"] * 5, v=[10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 9.0])
    got = run("guardrail", AB, t)
    assert row(got, "상태") == "판단 보류" and has(got, "표본이 적어")


# ---------- A6 기간을 건 코호트 지표의 신규 안내 ----------

@pytest.mark.parametrize("eid,extra", [("retention_classic", {"N": 1}), ("flattening", {}), ("churn", {}), ("ltv", {"금액": "amt", "N일": 7})])
def test_cohort_metrics_warn_about_old_users(eid, extra):
    t = mem(u=["a", "a", "b"], ts=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 2)], amt=[1.0, 2.0, 3.0])
    assert has(run(eid, {"사용자": "u", "시각": "ts", **extra}, t), "기존 사용자가 섞일 수")


# ---------- A7 일 단위의 오늘은 진행 중 ----------

def test_today_is_open_day(monkeypatch):
    t = mem(u=["a", "b", "c", "a", "b", "c", "a"], ts=[D(2026, 3, 1)] * 3 + [D(2026, 3, 2)] * 3 + [D(2026, 3, 3, 9)])
    s = {"사용자": "u", "시각": "ts"}
    monkeypatch.setattr(mc, "today", lambda: dt.date(2026, 3, 4))
    assert run("dau", s, t)["value"] == pytest.approx(7 / 3)  # 마지막 날이 어제: 다 끝났다
    monkeypatch.setattr(mc, "today", lambda: dt.date(2026, 3, 3))
    got = run("dau", s, t)
    assert got["value"] == pytest.approx(3.0) and has(got, "진행 중")  # 오늘(3/3)은 평균에서 뺀다: (3 + 3) / 2


# ---------- A9 ClickHouse는 정확한 함수로 ----------

def test_clickhouse_exact_distinct_and_quantile():
    sql_funcs.exact_clickhouse()  # 앱에서는 ClickHouse에 접속할 때(source.open_url) 부른다
    t = ibis.table({"x": "int64", "y": "float64"}, name="t")
    expr = t.aggregate(a=sql_funcs.aggregate("count_distinct", t, "x"), b=sql_funcs.aggregate("p90", t, "y"), c=sql_funcs.aggregate("quantile", t, "y", None, {"p": 0.3}))
    sql = ibis.to_sql(expr, dialect="clickhouse")
    assert "uniqExactIf(" in sql and "quantileExactInclusive(0.9)" in sql and "quantileExactInclusive(0.3)" in sql
    assert "uniqIf(" not in sql and "quantile(0" not in sql
    assert "uniqExact(" in ibis.to_sql(t.aggregate(a=t.x.nunique()), dialect="clickhouse")  # 지표 계산이 바로 쓰는 nunique도
    assert "COUNT(DISTINCT" in ibis.to_sql(t.aggregate(a=t.x.nunique()), dialect="duckdb")  # 다른 연결은 그대로
