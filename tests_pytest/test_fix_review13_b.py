"""13B: 단위마다 구한 지표의 평균(틀 13)이 앞 50개 기간만으로 평균을 내지 않는다."""
import datetime as dt

import ibis
import pandas as pd
import pytest

from core import metric_file
from query import sentence_exec as se


def by_day(days):
    """i번째 날에 i행인 표를 날마다 행 수로 구해 mean_of에 넣을 결과 하나."""
    rows = [(dt.date(2026, 1, 1) + dt.timedelta(days=i - 1),) for i in range(1, days + 1) for _ in range(i)]
    t = ibis.memtable(pd.DataFrame(rows, columns=["d"]))
    t = t.mutate(기간=t.d.cast("timestamp").truncate("D"))
    m = metric_file.metric("행수", {"numerator": {"agg": "count"}})
    labels, values, more = metric_file.compute(t, ["행수"], {"행수": m}, "기간")
    return se.one(["기간"], "행수", [{"기간": x, "행수": v} for x, v in zip(labels, values["행수"])], more)


def test_mean_of_refuses_when_periods_cut():
    with pytest.raises(ValueError, match="기간이 50개를 넘어"):
        se.combine({"op": "mean_of", "a": 0}, [by_day(90)])


def test_mean_of_within_limit():
    done = se.combine({"op": "mean_of", "a": 0}, [by_day(10)])
    assert done["rows"][0]["평균"] == pytest.approx(5.5)
