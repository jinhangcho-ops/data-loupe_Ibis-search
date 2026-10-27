"""26차 감사: Link 헤더 주소의 쉼표, 같은 시각 매일 예약, PV/UV 빈 이벤트 사용자, 지표 표 행 상한, MCU 달별 구간."""
import datetime as dt

import numpy as np
import polars as pl
import pytest
from lazy import ibis

from collect import collect_job
from integrations import schedule
from metrics import metric_calc as mc
from metrics import metric_catalog as cat

D = dt.datetime


class Reply:
    def __init__(self, link):
        self.link = link

    def header(self, name):
        return self.link if name == "Link" else None


def test_link_next_keeps_commas_inside_url():
    link = '<https://api.example.com/items?fields=id,name&page=2>; rel="next", <https://api.example.com/items?page=9>; rel="last"'
    assert collect_job.link_next(Reply(link)) == "https://api.example.com/items?fields=id,name&page=2"
    assert collect_job.link_next(Reply('<https://a.example/x?page=9>; rel="last"')) is None
    assert collect_job.link_next(Reply('<https://a.example/1>; title="go <here>"; rel="next"')) == "https://a.example/1"


def test_daily_schedule_waits_for_running_collect_instead_of_skipping(monkeypatch):
    item = {"every": "daily", "time": "09:00", "inapp": True, "windows": False}
    now, busy, ran = [D(2026, 10, 27, 8, 59)], [True], []
    monkeypatch.setattr(schedule, "clock", lambda: now[0])
    monkeypatch.setattr(schedule, "schedules", lambda: {"B": dict(item)})
    monkeypatch.setattr(schedule, "public", lambda x: x)
    monkeypatch.setattr(collect_job, "running", lambda: busy[0])
    monkeypatch.setattr(schedule, "execute", lambda name, how: ran.append((name, now[0])))
    due = {}
    schedule.tick(due)
    now[0] = D(2026, 10, 27, 9, 0)
    schedule.tick(due)
    assert ran == []  # 다른 수집이 도는 동안은 기다린다
    busy[0], now[0] = False, D(2026, 10, 27, 9, 10)
    schedule.tick(due)
    assert ran == [("B", D(2026, 10, 27, 9, 10))] and due["B"][1] == D(2026, 10, 28, 9, 0)


def run(entry_id, roles, table):
    names = {s["role"]: s["name"] for s in cat.get(entry_id)["slots"]}
    return cat.compute(entry_id, {names[r]: v for r, v in roles.items()}, {}, cat.Ctx(table=table), None)


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols, strict=False).to_arrow())


def test_pv_per_uv_counts_user_with_only_null_events_as_zero():
    t = mem(u=["a", "a", "b", "c"], ev=["view", "view", "click", None])
    r = run("pv_per_uv", {"user": "u", "event": "ev", "event_value": "view"}, t)
    assert r["parts"] == {"pv": 2, "uv": 3}
    assert (r["spread"]["n"], r["spread"]["median"]) == (3, 0.0)  # 분포에도 c(0번)가 든다


def test_flattening_and_power_curve_tables_keep_row_cap():
    t = mem(u=[f"u{i % 5}" for i in range(40)], t=[D(2025, 1, 1) + dt.timedelta(days=i) for i in range(40)])
    flat = run("flattening", {"user": "u", "time": "t", "unit": "day", "n": 300, "eps": 1, "run": 3}, t)
    power = run("power_curve", {"user": "u", "time": "t", "span": 300}, t)
    assert len(flat["rows"]) == len(power["rows"]) == 60


def test_monthly_peak_matches_full_scan():
    rng = np.random.default_rng(0)
    times = np.sort(np.datetime64("2024-01-01T00:00", "us") + rng.integers(0, 400 * 86400, 3000).astype("timedelta64[s]"))
    counts = rng.integers(0, 50, len(times))
    times[100:103] = times[100]  # 같은 시각의 여러 변화
    months = np.arange(times[0].astype("datetime64[M]"), times[-1].astype("datetime64[M]") + mc.MONTH)
    full = {str(m): mc.concurrency_stats(times, counts, m.astype("datetime64[us]"), (m + mc.MONTH).astype("datetime64[us]"))["pcu"] for m in months}
    assert mc.monthly_peak(times, counts) == full
