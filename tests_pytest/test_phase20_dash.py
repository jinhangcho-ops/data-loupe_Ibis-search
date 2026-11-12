"""Phase 20(plans/phase-20.md) 묶음 D: 코호트 카드의 진행 중인 마지막 기간 표시(cohort_partial)와 통화 기호 설정(currency). 데이터는 시험 안에서 지어낸다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from core import compare, dash_store, server, settings
from dashboard import dash_calc

D = dt.datetime
NOTE = "마지막 기간은 진행 중이라 낮게 보여요."


def visits(*rows):
    return ibis.memtable(pl.DataFrame(list(rows), schema=["ts", "uid"], orient="row").to_arrow())


# ---------- cohort_partial ----------

def test_cohort_is_partial_when_data_ends_mid_week(folder):
    """2026-01-05(월)에 온 두 사람 중 하나가 01-13(화)에 다시 왔다: 자료가 주 중간에 끝나 둘째 주 칸은 이틀 치다."""
    got = dash_calc.cohort_data(visits((D(2026, 1, 5, 9), "a"), (D(2026, 1, 6, 9), "b"), (D(2026, 1, 13, 9), "a")), "uid", "ts", "week")
    assert got["values"] == {"periods": [0, 1], "cohorts": [{"start": "2026-01-05", "size": 2, "retained": [1.0, 0.5]}], "partial": True}
    assert got["note"] == NOTE
    assert dash_store.check_data(got, "cohort") == got  # 카드 값 저장 검사가 새 칸을 거부하지 않는다


def test_cohort_is_not_partial_when_data_ends_on_the_last_day_of_the_week(folder):
    got = dash_calc.cohort_data(visits((D(2026, 1, 5, 9), "a"), (D(2026, 1, 6, 9), "b"), (D(2026, 1, 18, 23), "a")), "uid", "ts", "week")
    assert got == {"values": {"periods": [0, 1], "cohorts": [{"start": "2026-01-05", "size": 2, "retained": [1.0, 0.5]}]}, "note": ""}


def test_cohort_partial_for_year_unit_and_window_end(folder):
    """연 단위(metric_calc.open_bucket이 받지 않는 단위)도 같은 기준이고, 보는 기간이 주 중간에서 끝나면 그 뒤 자료가 있어도 진행 중이다."""
    rows = ((D(2025, 3, 1), "a"), (D(2026, 6, 30), "a"))
    assert dash_calc.cohort_data(visits(*rows), "uid", "ts", "year")["values"]["partial"] is True
    assert "partial" not in dash_calc.cohort_data(visits(*rows, (D(2026, 12, 31), "a")), "uid", "ts", "year")["values"]
    week = visits((D(2026, 1, 5, 9), "a"), (D(2026, 1, 13, 9), "a"), (D(2026, 1, 18, 9), "a"))
    assert "partial" not in dash_calc.cohort_data(week, "uid", "ts", "week")["values"]
    assert dash_calc.cohort_data(week, "uid", "ts", "week", {"end": "2026-01-14"})["values"]["partial"] is True


def test_cohort_day_of_today_is_still_open(folder, monkeypatch):
    """자료의 마지막 날이 PC의 오늘이면 그날은 아직 안 끝났다(metric_calc.open_bucket과 같은 기준)."""
    from metrics import metric_calc
    t = visits((D(2026, 1, 5, 9), "a"), (D(2026, 1, 6, 9), "a"))
    assert "partial" not in dash_calc.cohort_data(t, "uid", "ts", "day")["values"]
    monkeypatch.setattr(metric_calc, "today", lambda: dt.date(2026, 1, 6))
    assert dash_calc.cohort_data(t, "uid", "ts", "day")["values"]["partial"] is True


def test_cohort_window_end_uses_the_local_date(folder):
    """시간대 있는 시각: 01-11 16:00 UTC는 서울의 01-12(다음 주)라 끝이 01-11인 기간에 들지 않는다(UTC 날짜로 자르면 들어와 둘째 주가 생긴다)."""
    settings.save({"timezone": "Asia/Seoul"})
    utc = dt.timezone.utc
    t = visits((D(2026, 1, 5, 3, tzinfo=utc), "a"), (D(2026, 1, 11, 3, tzinfo=utc), "a"), (D(2026, 1, 11, 16, tzinfo=utc), "a"))
    got = dash_calc.cohort_data(t, "uid", "ts", "week", {"end": "2026-01-11"})
    assert got == {"values": {"periods": [0], "cohorts": [{"start": "2026-01-05", "size": 1, "retained": [1.0]}]}, "note": ""}


# ---------- currency ----------

def test_currency_symbol_setting_is_checked(folder):
    assert settings.get("currency_symbol") == "₩"  # 고른 적이 없으면 기본 기호
    assert all(settings.valid("currency_symbol", v) for v in ("₩", "$", "€", "US$", "kr", "<b>&", ".*("))
    for bad in ("", "abcde", " $", "$ ", "1", "$1", "-", "+", "\n", "\t$", 5, None, ["$"]):
        assert not settings.valid("currency_symbol", bad), bad  # 1~4자 글자만. 숫자·부호·공백은 값 글자를 다시 읽을 수 없게 해서 받지 않는다
    with pytest.raises(ValueError):
        settings.save({"currency_symbol": "12"})
    settings.save({"currency_symbol": "$"})
    assert settings.get("currency_symbol") == "$"


@pytest.mark.parametrize("symbol", ["$", "€", "US$", ".*(", "<b>&"])
def test_compare_texts_use_the_currency_symbol(folder, symbol):
    settings.save({"currency_symbol": symbol})
    assert compare.format_value(1234.5, "currency") == f"{symbol}1,235" and compare.format_value(-1234.5, "currency") == f"-{symbol}1,235"
    assert compare.format_value(-0.2, "currency") == f"{symbol}0"
    assert compare.format_delta(-500, "currency") == f"-{symbol}500" and compare.format_delta(0.2, "currency") == f"{symbol}0"
    got = compare.compare(1_234_000, 1_000_000, "currency", "span", "직전 30일")
    assert got["text"] == f"직전 30일보다 23.4% 늘었어요 (+{symbol}234,000)" and got["delta_text"] == f"+{symbol}234,000"
    assert compare.compare(1_000_000.2, 1_000_000, "currency")["direction"] == "flat"  # 보이는 변화가 0이면 같은 값
    assert compare.format_value(1234.5) == "1,234.5" and compare.format_value(0.5, "percent") == "50%"  # 다른 값 모양은 그대로


def test_dashboard_constants_carry_the_currency_symbol(folder):
    assert server.constants("dashboard")["currency_symbol"] == "₩" and server.constants("dashboard")["defaults"]["currency_symbol"] == "₩"
    settings.save({"currency_symbol": "€"})
    assert server.constants("dashboard")["currency_symbol"] == "€"


def test_page_response_sends_any_symbol_as_a_cookie(folder):
    """설정은 쿠키로도 간다: 쿠키에 그대로 못 쓰는 글자(₩·따옴표·;)여도 화면 응답이 깨지지 않는다."""
    for symbol in ("₩", '";\\', "<b>&"):
        settings.save({"currency_symbol": symbol})
        with server.app.test_request_context("/"):
            cookies = server.html_response("<html></html>").headers.getlist("Set-Cookie")
        assert any(c.startswith("currency_symbol=") for c in cookies), symbol
