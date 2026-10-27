"""37차 감사: LTV(ARPU÷이탈률)의 진행 중인 달, 수집 날짜 형식 검사, 기간 제한 퍼널의 조인(파일 연결과 같은 값)."""
import datetime as dt
import types

import pandas as pd
import polars as pl
import pytest
from lazy import ibis

from collect import collect_config
from core import funnel
from metrics import metric_kernels as mk


def test_ltv_arpu_leaves_out_open_month():
    rows = [(f"u{u}", dt.datetime(2026, m, d, 12), 1000.0) for m, last in ((8, 31), (9, 30), (10, 3)) for u in range({8: 100, 9: 90, 10: 81}[m]) for d in range(1, last + 1)]
    ctx = types.SimpleNamespace(notes=[], other=None, align=None)
    got = mk.KERNELS["ltv"](ibis.memtable(pd.DataFrame(rows, columns=["user", "ts", "amt"])), {"user": "user", "time": "ts", "amount": "amt", "n_days": 30, "method": "ARPU÷이탈률"}, {}, ctx)
    done = [r for r in rows if r[1].month < 10]
    assert round(got["parts"]["arpu_month"]) == round(sum(r[2] for r in done) / len({(r[0], r[1].month) for r in done}))  # 끝난 달만(전에는 27% 낮았다)


def test_collect_date_format_checked_on_save():
    with pytest.raises(ValueError, match="날짜 형식"):
        collect_config.normalize({"request": {"url": "https://x.invalid/a?d={today:%-d}"}})
    collect_config.normalize({"request": {"url": "https://x.invalid/a?d={today:%Y%m%d}"}})


def test_windowed_funnel_same_on_duckdb_and_polars():
    D = dt.datetime
    df = pl.DataFrame({"u": [1, 1, 2, 2], "e": ["a", "b", "a", "b"], "t": [D(2026, 1, 1, 0, 0), D(2026, 1, 1, 0, 30), D(2026, 1, 1), D(2026, 1, 2)]})
    duck = funnel.compute(ibis.memtable(df.to_arrow()), "u", "e", "t", ["a", "b"], window_seconds=3600)["steps"]
    pol = funnel.compute(ibis.polars.connect().create_table("ev", df), "u", "e", "t", ["a", "b"], window_seconds=3600)["steps"]
    assert [s["users"] for s in duck] == [s["users"] for s in pol] == [2, 1]
