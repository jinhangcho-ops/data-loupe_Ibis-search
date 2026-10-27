"""25차 감사(지표 계산) 버그의 되돌림 시험. 데이터는 시험 안에서 지어내고, 값은 손으로 센 것과 맞춘다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from metrics import metric_catalog as cat

D = dt.datetime


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols, strict=False).to_arrow())


def run(entry_id, roles, table, align=None, other=None):
    """역할 이름({"user": 컬럼})으로 슬롯을 채워 계산한다."""
    names = {s["role"]: s["name"] for s in cat.get(entry_id)["slots"]}
    ctx = cat.Ctx(table=table, other=(lambda name: other[name]) if other else None)
    return cat.compute(entry_id, {names[r]: v for r, v in roles.items()}, {}, ctx, align)


def test_cac_paid_counts_users_by_their_first_row_source():
    # a는 1월 organic으로 들어와 2월에 paid 행이 생겼다 → 유료 신규가 아니다. 2월 유료 신규는 b 하나 → CAC 100
    ev = mem(u=["a", "a", "b"], t=[D(2025, 1, 10), D(2025, 2, 10), D(2025, 2, 11)], src=["organic", "paid", "paid"])
    ad = mem(day=[D(2025, 1, 1), D(2025, 2, 1)], cost=[0.0, 100.0])
    roles = {"user": "u", "time": "t", "source": "src", "paid_value": "paid", "ad_table": "ads", "spend": "cost", "ad_time": "day"}
    r = run("cac_paid", roles, ev, align={"kind": "period", "unit": "month"}, other={"ads": ad})
    assert r["value"] == pytest.approx(100.0)
    assert r["parts"]["new_users"] == 1
    up = run("organic_uplift", {"user": "u", "time": "t", "source": "src", "paid_value": "paid"}, ev)
    assert up["parts"]["paid_installs"] == r["parts"]["new_users"]  # 두 지표의 유료 신규 기준이 같다


def test_ltv_arpu_churn_ignores_rows_without_time():
    base = dict(u=["a", "a", "a", "b", "b", "b", "c"], t=[D(2025, 1, 5), D(2025, 2, 5), D(2025, 3, 31), D(2025, 1, 6), D(2025, 2, 6), D(2025, 3, 30), D(2025, 1, 7)],
                amt=[90.0] * 3 + [0.0] * 4)
    roles = {"user": "u", "time": "t", "amount": "amt", "n_days": 30, "unit": "day", "method": "ARPU÷이탈률"}
    clean = run("ltv", roles, mem(**base))
    noisy = run("ltv", roles, mem(**{k: v + [x] for (k, v), x in zip(base.items(), ["a", None, 0.0])}))  # 시각이 빈 행 하나
    assert noisy["value"] == pytest.approx(clean["value"])
    assert noisy["parts"]["arpu_month"] == pytest.approx(270 / 7)  # 매출 270 ÷ 사용자-월 7


def test_gini_last_value_uses_last_known_value():
    # a의 마지막 행은 값이 비었다 → 마지막으로 알려진 100. 값 [100, 10]의 지니 = 90 / (2 × 2 × 55) ≈ 0.409
    t = mem(u=["a", "a", "b"], t=[D(2025, 1, 1), D(2025, 1, 2), D(2025, 1, 1)], bal=[100.0, None, 10.0])
    r = run("gini", {"value": "bal", "user": "u", "time": "t", "basis": "사용자별 마지막 값"}, t)
    assert r["parts"]["holders"] == 2
    assert r["value"] == pytest.approx(90 / 220)


def test_purchase_frequency_skips_users_without_orders():
    # b는 주문이 빈 행뿐 → 구매자가 아니다. a의 주문 2건 ÷ 구매자 1명
    t = mem(u=["a", "a", "b"], oid=["o1", "o2", None])
    r = run("purchase_frequency", {"user": "u", "orders": "oid"}, t)
    assert r["parts"] == {"orders": 2, "buyers": 1}
    assert r["value"] == pytest.approx(2.0)
