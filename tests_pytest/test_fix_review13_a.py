"""13차 리뷰 A: 자료가 마지막 기간 중간에 끝나면 이탈률·Quick Ratio·고착도·PUR·LTV(ARPU÷이탈률)는 그 기간을 평균에서 빼고, MRR·ARR·NRR·신규 결제자는 끝난 직전 기간 값을 쓴다.
실수 컬럼의 NaN이 JSON(NaN)이나 A/B 계산 오류가 되지 않는다. Payback에도 '덜 지난 코호트' 안내가 붙는다. 데이터는 시험 안에서 만든다."""
import datetime as dt
import json

import numpy as np
import pyarrow as pa
import pytest
from lazy import ibis

from metrics import metric_catalog as cat
from metrics import metric_kernels as mk

D = dt.datetime
S = {"user": "u", "time": "ts", "amount": "amt"}


class Ctx:
    def __init__(self):
        self.notes, self.other, self.align = [], None, None


def table(rows):
    return ibis.memtable(pa.table({"u": [r[0] for r in rows], "ts": [r[1] for r in rows], "amt": [r[2] for r in rows]}))


def monthly(end_day):
    """사용자 100명이 1~9월 매달 10일에 1000원씩 결제, 10월에는 10명만 end_day일에."""
    rows = [(u, D(2026, m, 10), 1000.0) for m in range(1, 10) for u in range(100)]
    return table(rows + [(u, D(2026, 10, end_day), 1000.0) for u in range(10)])


def calc(name, t, p, s=S):
    ctx = Ctx()
    got = mk.KERNELS[name](t, s, p, ctx)
    return got, got["notes"] + ctx.notes


def test_open_last_month_is_left_out_of_averages_and_latest():
    t = monthly(2)
    churn, notes = calc("states", t, {"pick": "churn"})
    assert churn["value"] == 0.0 and churn["rows"][-1]["churn_rate"] == 0.9 and any("진행 중" in n for n in notes)  # 표에는 그대로
    assert churn["parts"]["churned"] == 0
    assert calc("states", t, {"pick": "quick"})[0]["value"] is None  # 이탈이 없으면 Quick Ratio는 구할 수 없다(0이 아님)
    arr, notes = calc("recurring", t, {"pick": "arr"})
    assert arr["value"] == 1_200_000 and arr["rows"][-1]["mrr"] == 10_000 and any("직전" in n for n in notes)
    nrr, _ = calc("nrr", t, {})
    assert nrr["value"] == 1.0 and nrr["parts"]["kept_revenue"] == 100_000
    ltv, _ = calc("ltv", t, {}, {**S, "n_days": 30, "method": "ARPU÷이탈률"})
    assert ltv["parts"]["churn_rate"] == 0.0 and ltv["value"] is None
    assert calc("active", t, {"unit": "month", "unit_name": "MAU"})[0]["value"] == 100


def test_finished_last_month_is_kept():
    t = monthly(31)
    churn, notes = calc("states", t, {"pick": "churn"})
    assert churn["value"] == pytest.approx(0.9 / 9) and not any("진행 중" in n for n in notes)
    assert calc("recurring", t, {"pick": "arr"})[0]["value"] == 120_000


def test_stickiness_and_pur_skip_open_month():
    rows, d = [], dt.date(2026, 1, 1)
    while d <= dt.date(2026, 10, 1):  # 매일 1/3이 오고 10%가 결제, 자료는 10월 1일에 끝난다
        rows += [(u, D(d.year, d.month, d.day, 12), 1.0 if u % 10 == 0 else 0.0) for u in range(100) if (u + d.toordinal()) % 3 == 0]
        d += dt.timedelta(days=1)
    t = table(rows)
    stick, notes = calc("stickiness", t, {})
    assert stick["value"] == pytest.approx(1 / 3, abs=0.005) and stick["rows"][-1]["stickiness"] == 1.0 and any("진행 중" in n for n in notes)
    assert calc("pur", t, {"unit": "month"})[0]["value"] == pytest.approx(0.1)


def test_new_payers_uses_last_finished_period():
    rows = [(u, D(2026, 8, 5), 10.0) for u in range(3)] + [(u, D(2026, 9, 5), 10.0) for u in range(3, 8)] + [(9, D(2026, 10, 2), 10.0)]
    got, notes = calc("new_payers", table(rows), {}, {**S, "unit": "month"})
    assert got["value"] == 5 and got["rows"][-1]["npa"] == 1 and any("직전" in n for n in notes)
    gap = [(1, D(2026, 8, 5), 10.0), (2, D(2026, 9, 5), 0.0), (3, D(2026, 10, 2), 10.0)]  # 9월에는 처음 결제한 사람이 없다
    assert calc("new_payers", table(gap), {}, {**S, "unit": "month"})[0]["value"] == 0


def test_nan_amount_is_left_out():
    t = ibis.memtable(pa.table({"u": [1, 2, 3], "amt": [100.0, float("nan"), 50.0]}))
    for entry, slots, want in (("gross_revenue", {"금액": "amt"}, 150), ("arpu", {"사용자": "u", "금액": "amt"}, 50)):
        out = cat.compute(entry, slots, {}, cat.Ctx(table=t))
        assert out["value"] == want  # NaN은 빈 값(분석 화면과 같게)
        json.dumps(out, allow_nan=False)  # 브라우저가 읽을 수 있는 JSON


def test_ab_metrics_skip_nan():
    v = list(np.random.default_rng(1).normal(10, 2, 100))
    g = ["A"] * 50 + ["B"] * 50
    clean = ibis.memtable(pa.table({"g": g[:3] + g[4:], "v": v[:3] + v[4:]}))
    v[3] = float("nan")
    dirty = ibis.memtable(pa.table({"g": g, "v": v}))
    s = {"group": "g", "control": "A", "treat": "B", "metric": "v", "alpha": 0.05, "power": 0.8, "limit": -5, "op": ">="}
    for name in ("lift", "pvalue", "mde", "guardrail"):
        assert calc(name, dirty, {}, s)[0]["value"] == pytest.approx(calc(name, clean, {}, s)[0]["value"])


def test_payback_warns_about_young_cohorts():
    events = ibis.memtable(pa.table({"uid": [1, 2], "ts": [D(2026, 3, 1), D(2026, 3, 2)], "amt": [100.0, 100.0]}))
    ads = ibis.memtable(pa.table({"day": [D(2026, 3, 1)], "cost": [50.0]}))
    slots = {"사용자": "uid", "시각": "ts", "금액": "amt", "최대 일수": 3, "광고 테이블": "ads", "광고비": "cost", "광고 날짜": "day"}
    out = cat.compute("payback", slots, {}, cat.Ctx(table=events, other=lambda name: ads), {"kind": "period", "unit": "day"})
    assert any("덜 지난 코호트" in n for n in out["notes"])
