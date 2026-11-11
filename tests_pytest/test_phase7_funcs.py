"""Phase 7(plans/phase-7.md): 시계열·창 전용 함수 — 함수 사전의 날짜 행 함수·창 함수·시퀀스 함수, 계산 줄, 전처리 parts, Polars 지원 판정.
데이터는 시험 안에서 지어낸 메모리 표이고 기대값은 손으로 셌다(EWMA 긴 계열만 Polars ewm_mean과 견준다). 새 함수가 스켈레톤이면 실패하는 것이 정상이다.
기존 함수 수정((수정) 절)이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-7 <항목>")로 표시했다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from core import calc
from core import prep_ops
from core import settings
from core import sql_funcs as S

A = S.Args
T = dt.datetime
EV = pl.DataFrame({  # 2026-01-05는 월요일, 2026-01-10은 토요일
    "id": [1, 2, 3, 4, 5, 6],
    "uid": ["u1", "u1", "u1", "u1", "u2", "u2"],
    "ts": [T(2026, 1, 5, 8, 3), T(2026, 1, 5, 8, 20), T(2026, 1, 5, 9, 30), T(2026, 1, 6, 23, 59), T(2026, 1, 10, 13, 0), T(2026, 1, 10, 13, 10)],
    "ev": ["로그인", "상품", "상품", "결제완료", "로그인", "장바구니"],
    "amt": [10.0, 20.0, None, 40.0, 5.0, 5.0],
})


def table(df=EV):
    return ibis.memtable(df)


def values(t, value, by="id"):
    """값 식 하나를 t에 붙여 by 순으로 꺼낸다."""
    return t.mutate(__v=value).order_by(by).to_polars()["__v"].to_list()


def column(out, name, by="id"):
    return out.order_by(by).to_polars()[name].to_list()


def test_rebind_moves_keys_to_the_new_table():
    t = table()
    t1 = t.mutate(z=1)
    g, k = S.rebind(A(t=t, group=[t.uid], order=[t.ts.desc()]), t1)
    assert [x.get_name() for x in g] == ["uid"] and k[0].op().arg.name == "ts" and not k[0].op().ascending
    assert list(t1.select(*g).columns) == ["uid"]  # t1 위의 식이다


# ---------- A. 날짜 행 함수 ----------

def test_time_bucket_floors_to_day_aligned_steps():
    t = table()
    assert values(t, S.time_bucket(A(t=t, x=t.ts, n=15))) == [T(2026, 1, 5, 8, 0), T(2026, 1, 5, 8, 15), T(2026, 1, 5, 9, 30), T(2026, 1, 6, 23, 45),
                                                              T(2026, 1, 10, 13, 0), T(2026, 1, 10, 13, 0)]
    assert values(t, S.time_bucket(A(t=t, x=t.ts, n=360))) == [T(2026, 1, 5, 6), T(2026, 1, 5, 6), T(2026, 1, 5, 6), T(2026, 1, 6, 18), T(2026, 1, 10, 12), T(2026, 1, 10, 12)]  # 6시간
    old = ibis.memtable({"id": [1], "ts": [T(1969, 12, 31, 23, 50)]})
    assert values(old, S.time_bucket(A(t=old, x=old.ts, n=15))) == [T(1969, 12, 31, 23, 45)]  # 1970년 전도 내림


def test_time_bucket_refuses_bad_steps_and_dates(folder):
    t = table()
    with pytest.raises(ValueError, match="하루"):
        S.time_bucket(A(t=t, x=t.ts, n=7))
    with pytest.raises(ValueError, match="1 이상"):
        S.time_bucket(A(t=t, x=t.ts, n=0))
    d = ibis.memtable({"d": [dt.date(2026, 1, 5)]})
    with pytest.raises(ValueError, match="시각이 있는"):
        S.time_bucket(A(t=d, x=d.d, n=15))
    assert values(t, S.time_bucket(A(t=t, x=t.ts, n=None)))[1] == T(2026, 1, 5, 8, 15)  # 비우면 설정이 없으니 기본 15분


def test_weekday_number_name_and_weekend(folder):
    t = table()
    assert values(t, S.weekday_num(A(t=t, x=t.ts))) == [1, 1, 1, 2, 6, 6]
    assert values(t, S.weekday_name(A(t=t, x=t.ts))) == ["월", "월", "월", "화", "토", "토"]
    assert values(t, S.is_weekend(A(t=t, x=t.ts))) == [False, False, False, False, True, True]
    sun = ibis.memtable({"id": [1], "d": [dt.date(2026, 1, 11)]})
    assert values(sun, S.weekday_num(A(t=sun, x=sun.d))) == [7] and values(sun, S.is_weekend(A(t=sun, x=sun.d))) == [True]
    assert values(t, S.is_weekend(A(t=t, x=t.ts, days="sun"))) == [False] * 6  # 인자가 우선: 일요일만 주말이면 토요일은 아니다
    assert S.weekend_days(None) == [5, 6] and S.weekend_days("fri_sat") == [4, 5]
    with pytest.raises(ValueError, match="sat_sun"):
        S.weekend_days("mon_tue_wed_thu_fri_sat_sun")  # 모든 요일이 주말이면 영업일이 없다


def test_weekday_number_follows_week_start(folder):
    settings.save({"week_start": "sun"})
    d = ibis.memtable({"id": [1, 2, 3], "d": [dt.date(2026, 1, 11), dt.date(2026, 1, 5), dt.date(2026, 1, 10)]})
    assert values(d, S.weekday_num(A(t=d, x=d.d))) == [1, 2, 7]


def test_time_of_day_and_month_phase_defaults_and_custom():
    h = ibis.memtable({"id": list(range(8)), "ts": [T(2026, 1, 5, x) for x in (0, 5, 6, 11, 12, 17, 18, 23)]})
    assert values(h, S.time_of_day(A(t=h, x=h.ts, bounds=list(S.DAY_BOUNDS), labels=None))) == ["새벽", "새벽", "오전", "오전", "오후", "오후", "야간", "야간"]
    assert values(h, S.time_of_day(A(t=h, x=h.ts, bounds=[9, 18], labels=["밤", "낮", "저녁"]))) == ["밤", "밤", "밤", "낮", "낮", "낮", "저녁", "저녁"]
    with pytest.raises(ValueError, match="1~23"):
        S.time_of_day(A(t=h, x=h.ts, bounds=[0, 25], labels=None))
    d = ibis.memtable({"id": list(range(6)), "d": [dt.date(2026, 1, x) for x in (1, 10, 11, 20, 21, 31)]})
    assert values(d, S.month_phase(A(t=d, x=d.d, bounds=list(S.MONTH_BOUNDS), labels=None))) == ["월초", "월초", "월중", "월중", "월말", "월말"]
    assert values(d, S.month_phase(A(t=d, x=d.d, bounds=[16], labels=["상반", "하반"]))) == ["상반", "상반", "상반", "하반", "하반", "하반"]
    with pytest.raises(ValueError, match="시각이 있는"):
        S.time_of_day(A(t=d, x=d.d, bounds=list(S.DAY_BOUNDS), labels=None))


def test_work_hours_weekdays_and_all(folder):
    t = table()
    assert values(t, S.work_hours(A(t=t, x=t.ts, start=9, end=18, days="weekdays"))) == [False, False, True, False, False, False]  # 토요일 13시는 평일이 아니다
    assert values(t, S.work_hours(A(t=t, x=t.ts, start=9, end=18, days="all"))) == [False, False, True, False, True, True]
    assert values(t, S.work_hours(A(t=t, x=t.ts, start=None, end=None, days="weekdays"))) == [False, False, True, False, False, False]  # 비우면 기본 9~18시
    assert values(t, S.work_hours(A(t=t, x=t.ts, start=8, end=None, days="weekdays")))[:2] == [True, True]  # 한쪽만 주면 다른 쪽은 기본
    with pytest.raises(ValueError, match="시작 시 < 끝 시"):
        S.work_hours(A(t=t, x=t.ts, start=18, end=9, days="all"))


def test_is_month_end_handles_leap_years():
    d = ibis.memtable({"id": list(range(6)), "d": [dt.date(2026, 1, 31), dt.date(2026, 1, 30), dt.date(2026, 2, 28), dt.date(2024, 2, 28), dt.date(2024, 2, 29), dt.date(2026, 12, 31)]})
    assert values(d, S.is_month_end(A(t=d, x=d.d))) == [True, False, True, False, True, True]


# ---------- B·C·E. 창 함수 ----------

def test_since_prev_and_until_next():
    t = table()
    c = A(t=t, x=t.ts, unit="minute", order=[], group=[t.uid])
    assert values(t, S.since_prev(c)) == [None, 17, 70, 2309, None, 10]
    assert values(t, S.until_next(c)) == [17, 70, 2309, None, 10, None]


EP = pl.DataFrame({"id": list(range(8)), "uid": ["a"] * 6 + ["b"] * 2, "ts": [T(2026, 1, 1, 0, m) for m in (0, 1, 2, 3, 4, 5, 0, 1)],
                   "ev": ["로그인", "상품", "결제완료", "상품", "로그인", "상품", "상품", "로그인"]})


def test_episode_id_start_and_after():
    t = table(EP)
    ep = lambda at, vals: values(t, S.episode_id(A(t=t, x=t.ev, at=at, values=vals, order=[t.ts.asc()], group=[t.uid])))
    assert ep("start", ["로그인"]) == [1, 1, 1, 1, 2, 2, 0, 1]  # 첫 나누는 값 전 행은 0
    assert ep("after", ["결제완료"]) == [1, 1, 1, 2, 2, 2, 1, 1]  # 그 값의 다음 행부터 새 번호
    assert ep("start", ["로그인", "결제완료"]) == [1, 1, 2, 2, 3, 3, 0, 1]
    with pytest.raises(ValueError, match="하나 이상"):
        S.episode_id(A(t=t, x=t.ev, at="start", values=[], order=[t.ts.asc()], group=[t.uid]))


# ---------- 시퀀스 함수 ----------

def test_session_id_splits_on_gap_and_keeps_ties():
    t = table()
    out = S.session_id(A(t=t, x=t.ts, gap=30, group=[t.uid], name="s"))
    assert list(out.columns) == [*t.columns, "s"] and column(out, "s") == [1, 1, 2, 3, 1, 1]
    ties = ibis.memtable({"id": [1, 2, 3], "ts": [T(2026, 1, 1, 0, 0), T(2026, 1, 1, 0, 40), T(2026, 1, 1, 0, 40)]})
    assert column(S.session_id(A(t=ties, x=ties.ts, gap=30, group=[], name="s")), "s") == [1, 2, 2]  # 같은 시각은 같은 세션
    for bad in (0, True):
        with pytest.raises(ValueError, match="0보다 큰"):
            S.session_id(A(t=t, x=t.ts, gap=bad, group=[t.uid], name="s"))
    with pytest.raises(ValueError, match="이미 있어요"):
        S.session_id(A(t=t, x=t.ts, gap=30, group=[t.uid], name="ev"))


def test_cum_distinct_counts_new_values_only():
    t = table()
    assert column(S.cum_distinct(A(t=t, x=t.ev, order=[t.ts.asc()], group=[t.uid], name="k")), "k") == [1, 2, 2, 3, 1, 2]
    n = ibis.memtable({"id": [1, 2, 3, 4], "v": ["a", None, "a", "b"]})
    assert column(S.cum_distinct(A(t=n, x=n.v, order=[n.id.asc()], group=[], name="k")), "k") == [1, 1, 1, 2]  # 빈 값은 세지 않는다


def test_ewma_matches_adjusted_definition():
    s = ibis.memtable({"id": [1, 2, 3, 4], "x": [1.0, 2.0, 3.0, 4.0]})
    got = column(S.ewma(A(t=s, x=s.x, span=3, order=[s.id.asc()], group=[], name="w")), "w")
    assert got == pytest.approx([1.0, 2.5 / 1.5, 4.25 / 1.75, 6.125 / 1.875])  # α = 0.5, d = 0.5
    gap = ibis.memtable({"id": [1, 2, 3], "x": [1.0, None, 3.0]})
    assert column(S.ewma(A(t=gap, x=gap.x, span=3, order=[gap.id.asc()], group=[], name="w")), "w") == pytest.approx([1.0, 1.0, 3.25 / 1.25])  # 빈 값은 빼되 거리는 센다
    assert column(S.ewma(A(t=s, x=s.x, span=1, order=[s.id.asc()], group=[], name="w")), "w") == [1.0, 2.0, 3.0, 4.0]
    assert column(S.ewma(A(t=s, x=s.x, span=0.5, order=[s.id.asc()], group=[], name="w")), "w") == pytest.approx(got)  # α 0.5 = N 3
    for bad in (0, 1.5, 1001):
        with pytest.raises(ValueError, match="span"):
            S.ewma(A(t=s, x=s.x, span=bad, order=[s.id.asc()], group=[], name="w"))
    with pytest.raises(ValueError, match="정렬 기준"):
        S.ewma(A(t=s, x=s.x, span=3, order=[], group=[], name="w"))


def test_ewma_long_series_crosses_blocks():
    """60행(묶음 둘 × 30행, span 3이면 K = 20행 블록을 넘는다)이 Polars ewm_mean(adjust=True)과 EWMA_TOL 안에서 같다."""
    xs = [float((i * 7) % 11) for i in range(60)]
    df = pl.DataFrame({"id": list(range(60)), "g": ["a", "b"] * 30, "x": xs})
    want = df.sort("id").with_columns(w=pl.col("x").ewm_mean(span=3, adjust=True).over("g"))["w"].to_list()
    t = ibis.memtable(df)
    got = column(S.ewma(A(t=t, x=t.x, span=3, order=[t.id.asc()], group=[t.g], name="w")), "w")
    assert got == pytest.approx(want, rel=1e-4, abs=1e-4)


def test_streak_days_and_flag():
    d = ibis.memtable({"id": list(range(8)), "uid": ["a"] * 6 + ["b"] * 2,
                       "d": [dt.date(2026, 1, x) for x in (1, 2, 2, 3, 5, 6, 1, 3)]})
    assert column(S.streak(A(t=d, x=d.d, how="days", order=[], group=[d.uid], name="s")), "s") == [1, 2, 2, 3, 1, 2, 1, 1]
    f = ibis.memtable({"id": list(range(8)), "ok": [True, True, False, True, True, True, None, True]})
    assert column(S.streak(A(t=f, x=f.ok, how="flag", order=[f.id.asc()], group=[], name="s")), "s") == [1, 2, 0, 1, 2, 3, 0, 1]
    with pytest.raises(ValueError, match="날·주·월 연속"):
        S.streak(A(t=f, x=f.ok, how="days", order=[], group=[], name="s"))
    with pytest.raises(ValueError, match="참거짓"):
        S.streak(A(t=d, x=d.d, how="flag", order=[d.id.asc()], group=[], name="s"))


def test_streak_weeks_and_months(folder):
    w = ibis.memtable({"id": list(range(4)), "d": [dt.date(2026, 1, 5), dt.date(2026, 1, 7), dt.date(2026, 1, 12), dt.date(2026, 1, 26)]})  # 월요일 시작 주: 1주·1주·2주·4주
    assert column(S.streak(A(t=w, x=w.d, how="weeks", order=[], group=[], name="s")), "s") == [1, 1, 2, 1]
    m = ibis.memtable({"id": list(range(6)), "d": [dt.date(2025, 12, 31), dt.date(2026, 1, 15), dt.date(2026, 2, 1), dt.date(2026, 2, 20), dt.date(2026, 4, 1), dt.date(2026, 5, 9)]})
    assert column(S.streak(A(t=m, x=m.d, how="months", order=[], group=[], name="s")), "s") == [1, 2, 3, 3, 1, 2]  # 해를 넘어도 이어진다, 3월이 비면 다시 1


def test_event_path_dedupe_limit_and_join():
    t = table()
    path = lambda **kw: column(S.event_path(A(**{"t": t, "x": t.ev, "dedupe": "yes", "limit": 0, "sep": S.PATH_SEP, "order": [t.ts.asc()], "group": [t.uid], "name": "p", **kw})), "p")
    assert path() == ["로그인 -> 상품 -> 결제완료"] * 4 + ["로그인 -> 장바구니"] * 2
    assert path(dedupe="no")[0] == "로그인 -> 상품 -> 상품 -> 결제완료"
    assert path(limit=2)[0] == "로그인 -> 상품" and path(sep="|")[0] == "로그인|상품|결제완료"
    assert path(group=[])[0] == "로그인 -> 상품 -> 결제완료 -> 로그인 -> 장바구니"  # 묶음이 없으면 표 전체가 경로 하나
    with pytest.raises(ValueError, match="0 이상"):
        path(limit=-1)


# ---------- 사전 등록·계산 줄·지원 판정 ((수정) 뒤) ----------

def test_calendar_settings_are_validated(folder):
    ok = [("weekend", "sat_sun"), ("weekend", "fri_sat"), ("weekend", "sun"), ("work_hours", "9_18"), ("work_hours", "0_24"), ("bucket_minutes", 15),
          ("bucket_minutes", 360), ("ewma_span", 7)]
    bad = [("weekend", "sat_sat"), ("weekend", "xyz"), ("weekend", "mon_tue_wed_thu_fri_sat_sun"), ("work_hours", "18_9"), ("work_hours", "9_25"),
           ("bucket_minutes", 7), ("ewma_span", 1001)]
    assert all(settings.valid(n, v) for n, v in ok) and not any(settings.valid(n, v) for n, v in bad)
    assert settings.WEEKDAY_CODES == ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def test_settings_change_defaults_and_arguments_win(folder):
    settings.save({"weekend": "fri_sat", "work_hours": "8_17", "bucket_minutes": 30, "ewma_span": 3})
    t = table()
    assert values(t, S.is_weekend(A(t=t, x=t.ts, days=None))) == [False, False, False, False, True, True]  # 금·토
    assert values(t, S.is_weekend(A(t=t, x=t.ts, days="sun"))) == [False] * 6  # 인자가 우선
    assert values(t, S.work_hours(A(t=t, x=t.ts, days="weekdays", start=None, end=None)))[:3] == [True, True, True]  # 8~17시
    assert values(t, S.work_hours(A(t=t, x=t.ts, days="all", start=9, end=None)))[:3] == [False, False, True]  # 시작은 인자, 끝은 설정
    assert values(t, S.time_bucket(A(t=t, x=t.ts, n=None)))[1] == T(2026, 1, 5, 8, 0) and values(t, S.time_bucket(A(t=t, x=t.ts, n=15)))[1] == T(2026, 1, 5, 8, 15)
    s = ibis.memtable({"id": [1, 2], "x": [1.0, 2.0]})
    assert column(S.ewma(A(t=s, x=s.x, span=None, order=[s.id.asc()], group=[], name="w")), "w") == pytest.approx([1.0, 2.5 / 1.5])  # 설정 N 3


NEW_ROWS = ["time_bucket", "weekday_num", "weekday_name", "is_weekend", "time_of_day", "work_hours", "month_phase", "is_month_end"]
NEW_WINDOWS = ["moving_std", "since_prev", "until_next", "episode_id"]


def test_catalog_lists_new_functions_and_calc_screen():
    for i in NEW_ROWS + NEW_WINDOWS + list(S.SEQUENCES):
        assert i in S.FUNCS, i
    assert all(S.FUNCS[i]["table"] and S.FUNCS[i]["category"] == "window" for i in S.SEQUENCES)
    assert set(NEW_WINDOWS) <= set(calc.NEW_WINDOWS) and set(S.SEQUENCES) <= set(S.ids_for("calc"))
    assert not set(S.SEQUENCES) & set(S.ids_for("prep")) and set(NEW_ROWS + NEW_WINDOWS) <= set(S.ids_for("prep"))
    assert next(a for a in S.FUNCS["time_of_day"]["args"] if a["name"] == "bounds")["default"] == [6, 12, 18]
    t = table()
    assert values(t, S.make("weekday_name", t, {"x": "ts"})) == ["월", "월", "월", "화", "토", "토"]


def test_support_on_polars_marks_windows_local():
    pt = ibis.polars.connect().create_table("ev7", EV)
    found = S.support(pt, ids=NEW_WINDOWS + list(S.SEQUENCES))
    assert {i: v["status"] for i, v in found.items()} == {i: "local" for i in NEW_WINDOWS + list(S.SEQUENCES)}  # 파일 연결은 창이 없어 이 PC 사본에서


def test_calc_lines_for_sequences_and_new_windows():
    t = table()
    lines = ["session_id ts 30 그룹 uid 이름 s", "since_prev ts minute 기준 ts 그룹 uid 이름 g", "episode_id ev start 로그인,결제완료 기준 ts 그룹 uid 이름 e",
             "moving_std amt 2 rows 기준 ts 그룹 uid 이름 m", "cum_distinct ev 기준 ts 그룹 uid 이름 k", "streak ts days 그룹 uid 이름 st",
             "event_path ev yes 0 기준 ts 그룹 uid 이름 p", "ewma amt 3 기준 ts 그룹 uid 이름 w"]
    out = calc.apply(lines, t).order_by("id").to_polars()
    assert out["s"].to_list() == [1, 1, 2, 3, 1, 1] and out["g"].to_list() == [None, 17, 70, 2309, None, 10]
    assert out["e"].to_list() == [1, 1, 1, 2, 1, 1] and out["k"].to_list() == [1, 2, 2, 3, 1, 2] and out["st"].to_list() == [1, 1, 1, 2, 1, 1]
    assert out["m"].to_list()[:2] == [None, pytest.approx(7.0710678)] and out["m"].to_list()[5] == 0.0
    assert out["p"].to_list() == ["로그인 -> 상품 -> 결제완료"] * 4 + ["로그인 -> 장바구니"] * 2
    assert out["w"].to_list() == pytest.approx([10.0, 25 / 1.5, 25 / 1.5, 46.25 / 1.375, 5.0, 5.0])
    assert calc.apply(["cum_distinct ev 기준 ts"], t).columns[-1] == "ev_cum_distinct"
    with pytest.raises(ValueError, match="이미 있어요"):
        calc.apply(["session_id ts 30 그룹 uid 이름 ev"], t)


def test_prep_parts_add_calendar_columns():
    t = table()
    parts = ["weekday_num", "weekday_name", "is_weekend", "time_of_day", "work_hours", "month_phase", "is_month_end"]
    row = prep_ops.apply_ops(t, "ts", {"parts": parts}).order_by("id").to_polars().row(0, named=True)  # 2026-01-05(월) 08:03
    assert [row[f"ts_{p}"] for p in parts] == [1, "월", False, "오전", False, "월초", False]
    d = ibis.memtable({"d": [dt.date(2026, 1, 5)]})
    with pytest.raises(ValueError, match="'time_of_day'는 날짜시간 컬럼에만"):
        prep_ops.apply_ops(d, "d", {"parts": ["year", "time_of_day"]})
    with pytest.raises(ValueError, match="'hour'는 날짜시간 컬럼에만"):
        prep_ops.apply_ops(d, "d", {"parts": ["hour"]})
    fields = {f["name"] for f in next(x for x in prep_ops.LABELS if x["id"] == "parts")["fields"]}
    assert set(parts) <= fields
