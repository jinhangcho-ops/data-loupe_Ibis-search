"""B10 기간 단위의 분기(plans/dash-blocks.md "B10 세부 명세"): 서버 단위 목록, 날짜 차트의 분기 값(DuckDB·Polars 두 백엔드), 기간 번호, 다음 기간.
분기는 달력 분기(1·4·7·10월 1일 시작). 값은 손으로 계산해 맞춘다. 데이터는 테스트 안에서 지어낸다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from core import dash_store, result
from dashboard import dash_calc

D = dt.date
# 1분기: 1/5, 2/2 (u1, u1) · 2분기: 4/6, 4/7 (u2, u1) · 3분기: 7/1 (u3)
DF = pl.DataFrame({"day": [D(2000, 1, 5), D(2000, 2, 2), D(2000, 4, 6), D(2000, 4, 7), D(2000, 7, 1)],
                   "amt": [10.0, 20.0, 30.0, 40.0, 50.0], "uid": ["u1", "u1", "u2", "u1", "u3"]})
QUARTERS = ["2000-01-01", "2000-04-01", "2000-07-01"]


@pytest.fixture(params=["memory", "polars"])
def t(request):
    return ibis.memtable(DF.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", DF)


def named(section):
    return {s["name"]: s for s in section["series"]}


def test_units_have_quarter() -> None:
    assert list(dash_store.UNITS) == ["day", "week", "month", "quarter", "year"] and dash_store.UNITS["quarter"] == "분기"
    assert dash_store.check_card({"kind": "kpi", "title": "값", "view": {"unit": "quarter"}})["view"]["unit"] == "quarter"
    with pytest.raises(ValueError, match="시간 단위는 day, week, month, quarter, year 중 하나예요."):
        dash_store.check_card({"kind": "kpi", "title": "값", "view": {"unit": "half"}})


def test_time_data_quarter_on_server(t) -> None:
    """다시 묶을 수 없는 집계(고유 개수)는 서버가 분기마다 따로 구한다. 합계뿐이면 일 값만 준다(화면이 묶는다)."""
    by_unit = dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}, {"y": "uid", "agg": "nunique"}], None)["by_unit"]
    assert list(by_unit) == ["day", "week", "month", "quarter", "year"]
    assert by_unit["quarter"]["labels"] == QUARTERS
    assert named(by_unit["quarter"])["고유 개수 (uid)"]["values"] == [1, 2, 1] and list(named(by_unit["quarter"])) == ["고유 개수 (uid)"]
    assert set(dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}], None)["by_unit"]) == {"day"}


def test_time_data_cut_serves_quarter(t, monkeypatch) -> None:
    """일 값을 잘랐으면 분기도 월·연처럼 잘린 앞부분까지 서버가 구한다(주는 남은 일 값으로)."""
    monkeypatch.setattr(dash_store, "MAX_DAYS", 2)
    data = dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}], None)
    by_unit = data["by_unit"]
    assert by_unit["day"]["labels"] == ["2000-04-07", "2000-07-01"] and data["note"] == "앞 3일은 월 단위만 있어요."
    assert by_unit["quarter"]["labels"] == QUARTERS and named(by_unit["quarter"])["합계 (amt)"]["values"] == [30, 70, 50]
    assert "week" not in by_unit and named(by_unit["year"])["합계 (amt)"]["values"] == [150]


def test_slot_counts_quarters() -> None:
    assert dash_calc.slot(D(2000, 4, 1), "quarter") - dash_calc.slot(D(1999, 10, 1), "quarter") == 2
    assert dash_calc.slot(D(2000, 3, 31), "quarter") == dash_calc.slot(D(2000, 1, 1), "quarter")
    assert dash_calc.slot(D(2000, 4, 1), "quarter") - dash_calc.slot(D(2000, 3, 31), "quarter") == 1
    assert dash_calc.slot(D(2000, 4, 1), "month") - dash_calc.slot(D(1999, 10, 1), "month") == 6  # 다른 단위는 그대로


def test_next_period_quarter() -> None:
    assert result.next_period(D(1999, 10, 1), "quarter") == D(2000, 1, 1)
    assert result.next_period(D(2000, 4, 1), "quarter") == D(2000, 7, 1)
    assert result.next_period(D(2000, 12, 1), "month") == D(2001, 1, 1) and result.next_period(D(2000, 4, 1), "year") == D(2001, 4, 1)  # 다른 단위는 그대로
    assert result.next_period(D(2000, 1, 3), "week") == D(2000, 1, 10)
