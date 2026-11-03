"""묶음 D1(지표 정확성): 시간대·주 시작·회계연도 설정, 순매출 기준 결제자(ARPU·ARPPU·PUR), 전환 기한(window_days)·기간 밖 B, 달력 비교(previous mode),
기간별 ARPU·ARPPU, 주문 컬럼이 빈 경고, 이커머스 커널(RFM·환불률·구매 간격·신규/기존 매출), 코호트 매출 유지율·활성화율, LTV 관찰 완료·A/B 0 채움, last_rows 상한.
데이터는 시험 안에서 지어내고 값은 손으로 센 것과 맞춘다."""
import datetime as dt
import types
import zoneinfo

import polars as pl
import pyarrow as pa
import pytest
from lazy import ibis

from core import settings
from core import sql_funcs as S
from dashboard import period
from metrics import metric_calc as mc
from metrics import metric_catalog as cat
from metrics import metric_kernels as mk

D = dt.datetime
SEOUL = zoneinfo.ZoneInfo("Asia/Seoul")


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def run(entry_id, slots, table, body=None, whole=None):
    ctx = cat.Ctx(table=table, whole=whole)
    return cat.compute(entry_id, slots, body or {}, ctx)


def seoul_table():
    ts = [D(2026, 1, 1, 23, 30, tzinfo=SEOUL), D(2026, 1, 2, 0, 30, tzinfo=SEOUL)]
    return ibis.memtable(pa.table({"u": ["a", "b"], "t": pa.array(ts, pa.timestamp("us", tz="Asia/Seoul"))}))


# ---------- 설정 키 ----------

def test_settings_accept_timezone_week_start_and_fiscal_month(folder):
    assert settings.valid("timezone", "Asia/Seoul") and settings.valid("timezone", "+09:00") and not settings.valid("timezone", "Mars/Base")
    assert settings.valid("week_start", "sun") and settings.valid("week_start", "mon") and not settings.valid("week_start", "tue")
    assert settings.valid("fiscal_start_month", 4) and not settings.valid("fiscal_start_month", 13) and not settings.valid("fiscal_start_month", True)
    settings.save({"timezone": "Asia/Seoul", "week_start": "sun", "fiscal_start_month": 4})
    assert settings.load() == {"timezone": "Asia/Seoul", "week_start": "sun", "fiscal_start_month": 4}


# ---------- DA1 날짜 경계는 설정 시간대 ----------

def test_day_boundaries_follow_the_timezone_setting(folder):
    settings.save({"timezone": "Asia/Seoul"})
    t = seoul_table()
    days = t.select(d=mc.day_index(t.t)).to_pyarrow()["d"].to_pylist()
    assert days[1] - days[0] == 1 and mc.EPOCH + dt.timedelta(days=days[0]) == dt.date(2026, 1, 1)  # 서울 23:30과 00:30은 다른 날
    naive = mem(t=[D(2026, 1, 1, 23, 30), D(2026, 1, 2, 0, 30)])
    assert naive.select(d=mc.day_index(naive.t)).to_pyarrow()["d"].to_pylist() == [20454, 20455]  # 시간대 없는 시각은 그대로
    dau = run("dau", {"사용자": "u", "시각": "t"}, t)
    assert [r["period"] for r in dau["rows"]] == ["2026-01-01", "2026-01-02"]


def test_to_timezone_function_and_dst_zone(folder):
    t = seoul_table()
    got = t.select(v=S.make("to_timezone", t, {"x": "t", "zone": "Asia/Seoul"})).to_pyarrow()["v"].to_pylist()
    assert got == [D(2026, 1, 1, 23, 30), D(2026, 1, 2, 0, 30)]
    ny = ibis.memtable(pa.table({"t": pa.array([D(2026, 1, 15, 12, tzinfo=dt.timezone.utc), D(2026, 7, 15, 12, tzinfo=dt.timezone.utc)], pa.timestamp("us", tz="UTC"))}))
    assert ny.select(v=S.to_timezone(ny.t, "America/New_York")).to_pyarrow()["v"].to_pylist() == [D(2026, 1, 15, 7), D(2026, 7, 15, 8)]  # 겨울 −5, 여름 −4
    with pytest.raises(ValueError, match="시간대"):
        S.to_timezone(ny.t, "Mars/Base")


# ---------- DA22 주 시작·회계연도 ----------

def test_period_start_reads_week_start_and_fiscal_month(folder):
    t = mem(d=[dt.date(2026, 1, 1), dt.date(2026, 5, 1)])
    get = lambda unit: t.select(v=S.period_start(t.d, unit)).to_pyarrow()["v"].to_pylist()
    assert get("W") == [dt.date(2025, 12, 29), dt.date(2026, 4, 27)] and get("Y") == [dt.date(2026, 1, 1)] * 2  # 기본은 지금과 같다
    settings.save({"week_start": "sun", "fiscal_start_month": 4})
    assert get("W") == [dt.date(2025, 12, 28), dt.date(2026, 4, 26)]
    assert get("Y") == [dt.date(2025, 4, 1), dt.date(2026, 4, 1)]


def test_metric_weeks_follow_week_start(folder):
    t = mem(d=[D(2026, 1, 3, 9), D(2026, 1, 4, 9)])  # 토·일
    weeks = lambda: t.select(w=mc.bucket_index(t.d, "week")).to_pyarrow()["w"].to_pylist()
    a, b = weeks()
    assert a == b
    settings.save({"week_start": "sun"})
    a, b = weeks()
    assert b == a + 1 and mc.bucket_start(b, "week") == dt.date(2026, 1, 4)


# ---------- DA2 순매출 기준 결제자 ----------

def test_payers_use_net_revenue_per_user(folder):
    t = mem(u=["a", "a", "b", "c"], amt=[100, -100, 50, 0], ts=[D(2026, 3, 1, 9)] * 4)
    ua = {"사용자": "u", "금액": "amt"}
    arpu, arppu = run("arpu", ua, t)["value"], run("arppu", ua, t)["value"]
    pur = run("pur", {**ua, "시각": "ts"}, t)["value"]
    assert arpu == pytest.approx(50 / 3) and arppu == 50 and pur == pytest.approx(1 / 3) and run("pu", ua, t)["value"] == 1
    assert arpu == pytest.approx(pur * arppu)


# ---------- DA3 전환 기한·기간 밖 B ----------

def test_conversion_window_days(folder):
    t = mem(u=[1, 1, 2, 2], ev=["a", "b", "a", "b"], ts=[D(2026, 10, 1), D(2026, 10, 20), D(2026, 10, 1), D(2026, 10, 3)])
    s = {"사용자": "u", "시각": "ts", "이벤트": "ev", "단계 A": "a", "단계 B": "b"}
    assert run("cvr", s, t)["value"] == 1.0
    assert run("cvr", {**s, "기한(일)": 7}, t)["value"] == 0.5
    retry = mem(u=[1, 1, 1], ev=["a", "a", "b"], ts=[D(2026, 10, 1), D(2026, 10, 20), D(2026, 10, 22)])
    assert run("cvr", {**s, "기한(일)": 7}, retry)["value"] == 1.0  # 나중 시도가 기한 안에 끝나면 전환(분석 퍼널과 같다)


def test_conversion_sees_b_after_the_period_when_given_the_whole_table(folder):
    rows = dict(u=[1, 1], ev=["a", "b"], ts=[D(2026, 10, 31, 23), D(2026, 11, 1, 1)])
    whole = mem(**rows)
    inside = whole.filter(whole.ts < D(2026, 11, 1))
    s = {"사용자": "u", "시각": "ts", "이벤트": "ev", "단계 A": "a", "단계 B": "b"}
    assert run("cvr", s, inside)["value"] == 0.0
    assert run("cvr", s, inside, body={"periods": ["ts 2026-10"]}, whole=lambda b, keep: whole)["value"] == 1.0


def test_activation_rate_is_conversion_with_a_default_window(folder):
    t = mem(u=[1, 1, 2, 2], ev=["join", "core", "join", "core"], ts=[D(2026, 10, 1), D(2026, 10, 5), D(2026, 10, 1), D(2026, 10, 30)])
    assert run("activation", {"사용자": "u", "시각": "ts", "이벤트": "ev", "가입 값": "join", "핵심 행동 값": "core"}, t)["value"] == 0.5


# ---------- DA4 달력 비교 ----------

def test_previous_modes():
    span = period.parse("2026-10-05 ~ 2026-10-11")  # 월~일 7일
    assert period.previous(span) == period.previous(span, "prev")
    same = period.previous(period.parse("2026-10-05 ~ 2026-10-14"), "same_weekday")  # 10일 → 14일 앞
    assert (same["start"], same["end"], same["days"]) == ("2026-09-21", "2026-09-30", 10)
    month = period.previous(period.parse("2026-02"), "yoy")
    assert (month["start"], month["end"]) == ("2025-02-01", "2025-02-28")
    quarter = period.previous(period.parse("2026-Q3"), "yoy")
    assert (quarter["start"], quarter["end"]) == ("2025-07-01", "2025-09-30")
    week = period.previous(span, "yoy")
    assert (week["start"], week["end"]) == ("2025-10-06", "2025-10-12") and dt.date(2025, 10, 6).weekday() == 0  # 364일 앞 = 같은 요일
    mtd = period.previous(period.parse("이번 달", "2026-11-03"), "yoy")
    assert (mtd["start"], mtd["end"]) == ("2025-11-01", "2025-11-03")
    assert period.previous(span, "nope")["error"]


# ---------- DA5 기간별 ARPU·ARPPU ----------

def test_arpu_and_arppu_by_period_average_the_periods(folder):
    t = mem(u=["a", "b", "a", "b"], amt=[100, 0, 30, 10], ts=[D(2026, 1, 5), D(2026, 1, 6), D(2026, 2, 28), D(2026, 2, 28)])
    s = {"사용자": "u", "금액": "amt", "시각": "ts", "단위": "month"}
    got = run("arpu_period", s, t)
    assert got["value"] == pytest.approx((50 + 20) / 2) and [r["arpu"] for r in got["rows"]] == [50, 20]
    assert run("arppu_period", s, t)["value"] == pytest.approx((100 + 20) / 2)


# ---------- DA6 주문 컬럼이 빈 경고 ----------

def test_blank_order_column_warns_when_rows_share_user_and_time(folder):
    t = mem(u=[1, 1, 2], ts=[D(2026, 3, 1, 9)] * 2 + [D(2026, 3, 1, 10)], amt=[10, 20, 30])
    got = run("aov", {"금액": "amt", "사용자": "u", "시각": "ts"}, t)
    assert got["value"] == 20 and any("주문 컬럼" in n for n in got["notes"])
    clean = mem(u=[1, 2], ts=[D(2026, 3, 1, 9), D(2026, 3, 1, 10)], amt=[10, 20])
    assert not any("주문 컬럼" in n for n in run("purchase_frequency", {"사용자": "u", "시각": "ts"}, clean)["notes"])
    assert any("주문 컬럼" in n for n in run("repeat_rate", {"사용자": "u", "금액": "amt", "시각": "ts"}, t)["notes"])


# ---------- DA7 이커머스 커널 ----------

def test_refund_rate_by_count_and_amount(folder):
    t = mem(amt=[100, 50, -50, 200])
    got = run("refund_rate", {"금액": "amt"}, t)
    assert got["value"] == pytest.approx(50 / 350)
    assert {r["name"]: r["value"] for r in got["rows"]}["건수 기준"] == pytest.approx(1 / 3)
    flagged = mem(amt=[100, 50, 50, 200], st=["ok", "ok", "refund", "ok"])
    assert run("refund_rate", {"금액": "amt", "상태": "st", "환불 값": "refund"}, flagged)["value"] == pytest.approx(50 / 350)


def test_interpurchase_median_days(folder):
    t = mem(u=[1, 1, 1, 2, 2], amt=[10] * 5, ts=[D(2026, 3, 1), D(2026, 3, 3), D(2026, 3, 8), D(2026, 3, 1), D(2026, 3, 2)])
    got = run("interpurchase", {"사용자": "u", "금액": "amt", "시각": "ts"}, t)
    assert got["value"] == 2 and got["spread"]["median"] == 2


def test_new_vs_returning_revenue_share(folder):
    t = mem(u=["a", "a", "b"], amt=[100, 50, 30], ts=[D(2026, 1, 5), D(2026, 2, 28), D(2026, 2, 28)])
    got = run("new_vs_returning_revenue", {"사용자": "u", "금액": "amt", "시각": "ts", "단위": "month"}, t)
    assert got["value"] == pytest.approx(30 / 80)
    assert [(r["new_revenue"], r["returning_revenue"]) for r in got["rows"]] == [(100, 0), (30, 50)]


def test_rfm_grades_users_in_quintiles(folder):
    users = ["a", "b", "c", "d", "e"]
    rows = []
    for i, u in enumerate(users):  # e가 가장 최근·자주·많이
        for k in range(i + 1):
            rows.append((u, D(2026, 3, 1 + i * 2) - dt.timedelta(days=k), 10 * (i + 1)))
    t = mem(u=[r[0] for r in rows], ts=[r[1] for r in rows], amt=[r[2] for r in rows])
    got = run("rfm", {"사용자": "u", "금액": "amt", "시각": "ts"}, t)
    found = {r["rfm"]: r["users"] for r in got["rows"]}
    assert found["555"] == 1 and found["111"] == 1 and got["value"] == pytest.approx(2 / 5)


# ---------- DA8 코호트 매출 유지율 ----------

def test_revenue_retention_cohort(folder):
    t = mem(u=[1, 1, 2], amt=[100, 50, 40], ts=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 1)])
    got = run("revenue_retention", {"사용자": "u", "시각": "ts", "금액": "amt", "N": 1}, t)
    assert got["value"] == pytest.approx(50 / 140)
    m = mc.cohort_matrix(t, "u", "ts", "day", n_max=1, amount="amt")
    assert m["cohorts"][0]["values"] == [1.0, pytest.approx(50 / 140)]


# ---------- 작은 것: LTV 관찰 완료·A/B 0 채움·표 상한 ----------

def test_ltv_complete_ignores_today(folder, monkeypatch):
    t = mem(u=[1, 1], ts=[D(2026, 3, 1, 9), D(2026, 3, 2, 9)], amt=[10, 5])
    monkeypatch.setattr(mc, "today", lambda: dt.date(2026, 3, 2))
    assert mc.ltv(t, "u", "ts", "amt", 1)[0]["complete"] is False  # 3-02(오늘)은 아직 안 끝났다
    monkeypatch.setattr(mc, "today", lambda: dt.date(2026, 3, 5))
    assert mc.ltv(t, "u", "ts", "amt", 1)[0]["complete"] is True


def test_ab_users_without_metric_rows_count_as_zero(folder):
    t = mem(g=["c", "c", "t", "t"], u=[1, 2, 3, 4], v=[10.0, 10.0, 20.0, None])
    got = run("lift", {"실험군": "g", "지표 값": "v", "대조군 값": "c", "실험군 값": "t", "사용자": "u"}, t)
    assert got["value"] == 0.0 and {p["id"]: p["value"] for p in got["pair"]}["treat_mean"] == 10.0


def test_last_rows_keeps_400_oldest_first():
    ctx = types.SimpleNamespace(notes=[])
    rows = list(range(450))
    assert mk.last_rows(rows, ctx) == rows[-400:] and mk.last_rows(rows[:400], ctx) == rows[:400]
