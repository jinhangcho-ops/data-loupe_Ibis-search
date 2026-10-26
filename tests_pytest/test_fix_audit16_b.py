"""16차 감사 B: 범위 리텐션은 창 [a, b]가 다 지난 코호트만 값을 낸다(G3). NPA의 '가장 최근 기간'은 신규 0명인 달도 대표가 된다(G4). 데이터는 시험 안에서 만든다."""
import datetime as dt

import pyarrow as pa
from lazy import ibis

from metrics import metric_calc as mc
from metrics import metric_kernels as mk

D = dt.datetime


class Ctx:
    def __init__(self):
        self.notes, self.other, self.align = [], None, None


def table(rows):
    return ibis.memtable(pa.table({"u": [r[0] for r in rows], "ts": [r[1] for r in rows], "amt": [float(r[2]) for r in rows]}))


def values(m):
    return {c["cohort"]: c["values"] for c in m["cohorts"]}


def test_range_retention_waits_for_whole_window():
    # 자료 끝 1/10. a: 1/1 첫 활동, 1/5(D4) 재방문, 1/10 활동. b: 1/6 첫 활동(D3~D7 = 1/9~1/13, 이틀만 관찰).
    t = table([("a", D(2025, 1, 1), 0), ("a", D(2025, 1, 5), 0), ("a", D(2025, 1, 10), 0), ("b", D(2025, 1, 6), 0)])
    m = mc.cohort_matrix(t, "u", "ts", "day", "range", span=(3, 7))
    assert values(m) == {"2025-01-01": [1.0], "2025-01-06": [None]}  # 손으로: a는 D4에 왔으니 1/1, b는 아직 못 본다
    assert m["partial"] is False and mc.average_curve(m["cohorts"]) == [1.0]


def test_range_retention_cuts_open_period_inside_window():
    # month 단위, 자료가 3/15에 끝나 3월은 진행 중. 1월 코호트의 M1~M2 창(2~3월)에 진행 중인 3월이 들어 있다.
    t = table([("a", D(2025, 1, 5), 0), ("a", D(2025, 3, 15), 0)])
    m = mc.cohort_matrix(t, "u", "ts", "month", "range", span=(1, 2))
    assert values(m)["2025-01-01"] == [None] and m["partial"] is True
    assert mc.cohort_matrix(t, "u", "ts", "month", "classic", n_max=2)["cohorts"][0]["values"] == [1.0, 0.0, None]  # classic은 그대로


def npa(rows):
    ctx = Ctx()
    got = mk.KERNELS["new_payers"](table(rows), {"user": "u", "time": "ts", "amount": "amt", "unit": "month"}, {}, ctx)
    return got["value"], [r["npa"] for r in got["rows"]], got["notes"] + ctx.notes


def test_npa_latest_period_with_no_new_payer():
    # 1월 신규 3명, 2월(끝난 달)은 1이 다시 결제하고 4는 0원 활동 → 2월 신규 0명
    rows = [(1, D(2025, 1, 5), 100), (2, D(2025, 1, 6), 100), (3, D(2025, 1, 7), 100), (1, D(2025, 2, 10), 50), (4, D(2025, 2, 28, 12), 0)]
    value, table_rows, _ = npa(rows)
    assert value == 0 and table_rows == [3]


def test_npa_open_month_falls_back_to_empty_previous_month():
    # 1월 신규 3명, 2월 신규 0명(재결제만), 3월은 3/10에 자료가 끝나 진행 중 → 대표는 2월 = 0
    rows = [(1, D(2025, 1, 5), 100), (2, D(2025, 1, 6), 100), (3, D(2025, 1, 7), 100), (1, D(2025, 2, 10), 50), (2, D(2025, 3, 10), 0)]
    value, _, notes = npa(rows)
    assert value == 0 and any("직전" in n for n in notes)
    rows2 = rows + [(5, D(2025, 2, 11), 10)]  # 2월에 신규 1명이 있으면 1
    assert npa(rows2)[0] == 1
