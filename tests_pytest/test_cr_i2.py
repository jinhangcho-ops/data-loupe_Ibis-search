"""I2: 시간대(trend·busiest·dash_calc 묶기), 퍼널 by 전달, index.html 힌트·피벗 화면 표식."""
import datetime as dt
import pathlib

import polars as pl
import pytest
from lazy import ibis

from analysis import stats

UTC = dt.timezone.utc
AWARE = pl.DataFrame({"t": [dt.datetime(2026, 1, 1, 20, 0, tzinfo=UTC)], "v": [1]}).with_columns(pl.col("t").dt.convert_time_zone("UTC"))
NAIVE = pl.DataFrame({"t": [dt.datetime(2026, 1, 1, 20, 0)], "v": [1]})


@pytest.fixture(autouse=True)
def seoul(monkeypatch):
    from core import settings
    monkeypatch.setattr(settings, "load", lambda: {"timezone": "Asia/Seoul"})


@pytest.fixture(params=["memory", "polars"])
def make(request):
    return lambda df: ibis.memtable(df.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", df)


def test_trend_busiest_local_day(make):
    t = make(AWARE)
    assert stats.trend(t, "t", "day", "v", "count")["labels"] == ["2026-01-02"]
    assert stats.busiest(t, "t", "day")["periods"][0]["start"] == "2026-01-02"


def test_naive_unchanged(make):
    t = make(NAIVE)
    assert stats.trend(t, "t", "day", "v", "count")["labels"] == ["2026-01-01"]


def test_funnel_by_passed(make, monkeypatch):
    from core import funnel
    seen = {}
    monkeypatch.setattr(funnel, "compute", lambda *a, **k: seen.update(k) or {"steps": [], "segments": [{"name": "a", "steps": []}]})
    monkeypatch.setattr(funnel, "check", lambda *a, **k: None)
    d = stats.funnel_flow(make(NAIVE), "v", "v", "t", ["1"], by="v")
    assert seen["by"] == "v" and d["segments"] and d["by"] == "v"


def test_index_html_markers():
    s = pathlib.Path("static/query/index.html").read_text(encoding="utf-8")
    for key in ("pv-show", "pv-add", "pv-extra", "최근 7일", "빈 값 행도 남겨요", "대소문자 무시"):
        assert key in s
