"""기간 직접 입력: 문법, 윤년·월말·분기, 열린 범위, 시각, 기준일, 직전 기간, 날짜 읽기(미래 거부), 주기, 잘못된 입력."""
import pytest

from dashboard import period

TODAY = "2026-10-22"  # 목요일


def span(text, today=TODAY):
    found = period.parse(text, today)
    assert found["error"] is None, found["error"]
    return found


def ok(text, start, end, days, today=TODAY):
    found = span(text, today)
    assert (found["start"], found["end"], found["days"]) == (start, end, days)


def bad(text, *words):
    found = period.parse(text, TODAY)
    assert found["start"] is None and found["end"] is None and found["days"] is None
    for word in words:
        assert word in found["error"]
    return found["error"]


def test_months_quarters_years_and_leap_year():
    ok("2026-10", "2026-10-01", "2026-10-31", 31)
    ok("2024-02", "2024-02-01", "2024-02-29", 29)  # 윤년
    ok("2025-02", "2025-02-01", "2025-02-28", 28)
    ok("1900-02", "1900-02-01", "1900-02-28", 28)  # 100의 배수는 윤년이 아니다
    ok("2000-02", "2000-02-01", "2000-02-29", 29)
    ok("2026-Q1", "2026-01-01", "2026-03-31", 90)
    ok("2024-Q1", "2024-01-01", "2024-03-31", 91)
    ok("2026-q4", "2026-10-01", "2026-12-31", 92)
    ok("2024", "2024-01-01", "2024-12-31", 366)
    ok("2026.10", "2026-10-01", "2026-10-31", 31)
    assert span("2026-Q3")["label"] == "2026년 3분기"


def test_dates_in_all_formats():
    for text in ["2026-10-01", "2026.10.01", "2026/10/01", "20261001", "  2026-10-01  "]:
        ok(text, "2026-10-01", "2026-10-01", 1)
    ok("2024-02-29", "2024-02-29", "2024-02-29", 1)


def test_recent_n_units_use_reference_day():
    # 최근 N단위는 기준일 전날(어제)까지(사용자 결정, 11차 리뷰 G2)
    ok("최근 7일", "2026-10-15", "2026-10-21", 7)
    ok("최근 1일", "2026-10-21", "2026-10-21", 1)
    ok("최근 2주", "2026-10-08", "2026-10-21", 14)
    ok("최근 3개월", "2026-07-22", "2026-10-21", 92)
    ok("최근 1달", "2026-09-22", "2026-10-21", 30)
    ok("최근 1년", "2025-10-22", "2026-10-21", 365)
    ok("최근7일", "2026-10-15", "2026-10-21", 7)


def test_recent_month_end_and_leap_clamp():
    # 3-31의 한 달 전은 2-28(윤년이면 2-29): 최근 1달은 그 다음 날부터. 끝은 기준일 전날
    ok("최근 1달", "2026-03-01", "2026-03-31", 31, today="2026-04-01")
    ok("최근 1달", "2024-03-01", "2024-03-31", 31, today="2024-04-01")
    ok("최근 1달", "2026-01-01", "2026-01-31", 31, today="2026-02-01")
    ok("최근 1년", "2024-02-29", "2025-02-28", 366, today="2025-03-01")  # 윤일이 든 1년
    assert period.add_months(period.as_date("2026-03-31"), -1).isoformat() == "2026-02-28"
    assert period.add_months(period.as_date("2024-03-31"), -1).isoformat() == "2024-02-29"
    assert period.add_months(period.as_date("2026-01-31"), 1).isoformat() == "2026-02-28"
    assert period.add_months(period.as_date("2026-01-15"), -14).isoformat() == "2024-11-15"
    assert period.add_months(period.as_date("2024-02-29"), 12).isoformat() == "2025-02-28"


def test_relative_words():
    ok("오늘", "2026-10-22", "2026-10-22", 1)
    ok("어제", "2026-10-21", "2026-10-21", 1)
    ok("지난주", "2026-10-12", "2026-10-18", 7)  # 월~일
    ok("이번 주", "2026-10-19", "2026-10-22", 4)  # 월~기준일
    ok("이번주", "2026-10-19", "2026-10-22", 4)
    ok("지난달", "2026-09-01", "2026-09-30", 30)
    ok("이번 달", "2026-10-01", "2026-10-22", 22)
    ok("올해", "2026-01-01", "2026-10-22", 295)
    ok("작년", "2025-01-01", "2025-12-31", 365)
    ok("작년", "2024-01-01", "2024-12-31", 366, today="2025-06-01")
    ok("지난달", "2025-12-01", "2025-12-31", 31, today="2026-01-15")  # 연초에서 본 지난달
    ok("지난주", "2025-12-22", "2025-12-28", 7, today="2026-01-01")
    ok("어제", "2024-02-29", "2024-02-29", 1, today="2024-03-01")


def test_as_of_changes_every_relative_expression():
    ok("최근 7일", "2026-09-23", "2026-09-29", 7, today="2026-09-30")
    ok("지난달", "2026-08-01", "2026-08-31", 31, today="2026-09-30")
    ok("최근 7일", "2026-10-01", "2026-10-07", 7, today=period.as_date("2026-10-08"))
    assert period.parse("오늘")["start"] == period.date.today().isoformat()  # 기준일이 없으면 이 PC의 오늘


def test_ranges_and_open_ranges():
    found = span("2026-01-01 ~ 2026-03-31")
    assert (found["start"], found["end"], found["days"], found["label"]) == ("2026-01-01", "2026-03-31", 90, "2026-01-01 ~ 2026-03-31")
    ok("2026-01-01~2026-01-31", "2026-01-01", "2026-01-31", 31)
    ok("2026-01 ~ 2026-03", "2026-01-01", "2026-03-31", 90)  # 끝은 그 달의 마지막 날
    ok("2024-Q1 ~ 2024-Q2", "2024-01-01", "2024-06-30", 182)
    ok("2026-10-01 ~ 2026-10-01", "2026-10-01", "2026-10-01", 1)
    ok("2026-10-01 ~ 오늘", "2026-10-01", "2026-10-22", 22)
    open_end = span("2026-01-01 ~")
    assert (open_end["start"], open_end["end"], open_end["days"]) == ("2026-01-01", None, None)
    open_start = span("~ 2026-03")
    assert (open_start["start"], open_start["end"], open_start["days"]) == (None, "2026-03-31", None)
    assert "2026-03-31" in open_start["label"]


def test_times():
    found = span("2026-10-09 09:00 ~ 2026-10-09 18:30")
    assert (found["start"], found["end"], found["days"]) == ("2026-10-09 09:00", "2026-10-09 18:30", 1)
    ok("2026-10-08 22:00 ~ 2026-10-09 03:00", "2026-10-08 22:00", "2026-10-09 03:00", 2)
    ok("09:00 ~ 18:00", "2026-10-22 09:00", "2026-10-22 18:00", 1)  # 시각만 쓰면 기준일의 시각
    ok("09:00 ~ 18:00", "2026-09-30 09:00", "2026-09-30 18:00", 1, today="2026-09-30")
    ok("2026-10-09 09:00 ~ 2026-10-10", "2026-10-09 09:00", "2026-10-10", 2)  # 날짜만 쓴 끝은 그날 끝까지
    ok("2026-10-09 7:05", "2026-10-09 07:05", "2026-10-09 07:05", 1)
    bad("2026-10-09 18:00 ~ 2026-10-09 09:00", "시작", "끝", "뒤")
    ok("2026-10-09 23:59 ~ 2026-10-09", "2026-10-09 23:59", "2026-10-09", 1)  # 날짜만 쓴 끝은 그날 23:59까지


def test_previous_for_months_quarters_years_and_days():
    def prev(text, today=TODAY):
        found = period.previous(period.parse(text, today))
        assert found["error"] is None
        return found["start"], found["end"], found["days"]

    assert prev("2026-10") == ("2026-09-01", "2026-09-30", 30)  # 길이가 달라도 직전 달 전체
    assert prev("2026-03") == ("2026-02-01", "2026-02-28", 28)
    assert prev("2024-03") == ("2024-02-01", "2024-02-29", 29)
    assert prev("2026-01") == ("2025-12-01", "2025-12-31", 31)
    assert prev("2026-Q1") == ("2025-10-01", "2025-12-31", 92)
    assert prev("2026-Q3") == ("2026-04-01", "2026-06-30", 91)
    assert prev("2026") == ("2025-01-01", "2025-12-31", 365)
    assert prev("2025") == ("2024-01-01", "2024-12-31", 366)
    assert prev("2026-01 ~ 2026-02") == ("2025-11-01", "2025-12-31", 61)  # 두 달이면 직전 두 달
    assert prev("지난달") == ("2026-08-01", "2026-08-31", 31)
    assert prev("최근 7일") == ("2026-10-08", "2026-10-14", 7)  # 바로 앞 N일(최근 7일은 10-15~10-21)
    assert prev("2026-10-05 ~ 2026-10-09") == ("2026-09-30", "2026-10-04", 5)
    assert prev("어제") == ("2026-10-20", "2026-10-20", 1)
    assert prev("지난주") == ("2026-10-05", "2026-10-11", 7)


def test_previous_of_to_date_units_is_same_position_in_prior_unit():
    def prev(text, today):
        found = period.previous(period.parse(text, today))
        assert found["error"] is None
        return found["start"], found["end"], found["label"]

    assert period.parse("이번 달", TODAY)["to_date"] == "month" and "to_date" not in period.parse("지난달", TODAY)
    assert prev("이번 달", TODAY) == ("2026-09-01", "2026-09-22", "지난달 같은 기간")
    assert prev("이번 달", "2026-03-31") == ("2026-02-01", "2026-02-28", "지난달 같은 기간")  # 달 끝으로 맞춤
    assert prev("이번 달", "2024-03-31") == ("2024-02-01", "2024-02-29", "지난달 같은 기간")
    assert prev("이번 달", "2026-01-10") == ("2025-12-01", "2025-12-10", "지난달 같은 기간")
    assert prev("이번 주", TODAY) == ("2026-10-12", "2026-10-15", "지난주 같은 기간")  # 월~목 → 월~목
    assert prev("이번 주", "2026-10-19") == ("2026-10-12", "2026-10-12", "지난주 같은 기간")
    assert prev("올해", TODAY) == ("2025-01-01", "2025-10-22", "작년 같은 기간")
    assert prev("올해", "2024-02-29") == ("2023-01-01", "2023-02-28", "작년 같은 기간")  # 02-29 맞춤
    # 달력 입력은 단위 전체 그대로
    assert prev("지난달", TODAY)[:2] == ("2026-08-01", "2026-08-31")
    assert prev("2026", TODAY)[:2] == ("2025-01-01", "2025-12-31")


def test_previous_of_times_and_open_ranges():
    found = period.previous(period.parse("2026-10-09 09:00 ~ 2026-10-09 18:00"))
    assert (found["start"], found["end"]) == ("2026-10-08 23:59", "2026-10-09 08:59")  # 바로 앞 같은 9시간
    assert period.previous(period.parse("2026-01-01 ~"))["error"]
    assert period.previous(period.parse("~ 2026-03"))["error"]
    assert period.previous(period.parse("엉터리"))["error"]
    assert period.previous(None)["error"]
    # 직전의 직전도 이어서 구할 수 있다
    twice = period.previous(period.previous(period.parse("2026-03")))
    assert (twice["start"], twice["end"]) == ("2026-01-01", "2026-01-31")


def test_invalid_input_has_friendly_korean_errors():
    assert bad("2026-13-01", "2026-13-01", "없는 날짜") == "2026-13-01은 없는 날짜예요. 달력에 있는 날로 써 주세요."
    bad("2023-02-29", "없는 날짜")
    bad("2026-04-31", "없는 날짜")
    bad("2026-00-10", "없는 날짜")
    bad("2026-13", "없는 달")
    bad("2026-Q5", "없는 분기")
    bad("2026-Q0", "없는 분기")
    bad("최근 3개", "‘최근 3개’", "단위")
    bad("최근 3", "단위")
    bad("최근 0일", "1 이상")
    bad("2026-10 ~ 2026-09", "시작(2026-10-01)", "끝(2026-09-30)", "뒤예요")
    bad("2026-03-05 ~ 2026-03-01", "뒤예요")
    bad("2026-01-01 ~ 2026-02-01 ~ 2026-03-01", "한 번만")
    bad("~", "시작이나 끝")
    bad("   ", "입력")
    bad("", "입력")
    bad("blah", "읽을 수 없는 기간")
    bad("12:60", "없는 시각")
    bad("24:00 ~ 25:00", "없는 시각")
    bad("2026-1-2-3", "읽을 수 없는")
    assert period.parse(None, TODAY)["error"]
    assert "기준일" in period.parse("오늘", "어제")["error"]


def test_parse_day_formats():
    for text in ["2026-10-01", "2026.10.01", "2026/10/01", "20261001", "2026-10-1", " 2026-10-01 "]:
        found = period.parse_day(text, TODAY)
        assert found["date"] == "2026-10-01" and found["error"] is None, text
    assert period.parse_day("2026-10-01", TODAY)["label"] == "2026-10-01(목) 기준"
    assert period.parse_day("2024-02-29", TODAY)["date"] == "2024-02-29"
    assert period.parse_day("2026-10-22", TODAY)["date"] == TODAY  # 오늘은 된다


def test_parse_day_words_follow_today():
    cases = {"오늘": "2026-10-22", "어제": "2026-10-21", "그제": "2026-10-20", "그저께": "2026-10-20", "지난달 말": "2026-09-30", "지난달말": "2026-09-30", "작년 말": "2025-12-31", "": "2026-10-22"}
    for text, want in cases.items():
        assert period.parse_day(text, TODAY)["date"] == want, text
    assert period.parse_day("지난달 말", "2026-03-15")["date"] == "2026-02-28"
    assert period.parse_day("지난달 말", "2024-03-15")["date"] == "2024-02-29"
    assert period.parse_day("지난달 말", "2026-01-05")["date"] == "2025-12-31"
    assert period.parse_day("어제", "2024-03-01")["date"] == "2024-02-29"
    assert period.parse_day(None, TODAY)["date"] == TODAY
    assert period.parse_day("오늘")["date"] == period.date.today().isoformat()


def test_parse_day_rejects_future_and_garbage():
    future = period.parse_day("2026-10-23", TODAY)
    assert future["date"] is None and "아직 오지 않은 날이에요" in future["error"]
    assert "아직 오지 않은 날이에요" in period.parse_day("20271231", TODAY)["error"]
    assert "없는 날짜" in period.parse_day("2026-13-01", TODAY)["error"]
    assert "없는 날짜" in period.parse_day("2023-02-29", TODAY)["error"]
    for text in ["내일", "abc", "2026-10", "최근 7일", "2026-10-01 09:00"]:
        found = period.parse_day(text, TODAY)
        assert found["date"] is None and "날짜로 읽을 수 없어요" in found["error"], text
    assert period.parse_day("오늘", "엉터리")["error"]


def test_parse_every():
    assert period.parse_every("30초") == 30
    assert period.parse_every("30분") == 1800
    assert period.parse_every("2시간") == 7200
    assert period.parse_every("1일") == 86400
    assert period.parse_every("2주") == 14 * 86400
    assert period.parse_every("1달") == 30 * 86400
    assert period.parse_every("3개월") == 90 * 86400
    assert period.parse_every("1년") == 365 * 86400
    assert period.parse_every(" 15 분 ") == 900
    assert period.parse_every("1.5시간") == 5400
    for text in ["", "분", "30", "30 분마다", "-5분", "0분", "열 분", "5ms"]:
        with pytest.raises(ValueError) as e:
            period.parse_every(text)
        assert e.value.args[0]
    assert "단위" in str(pytest.raises(ValueError, period.parse_every, "30").value)


def test_particles_follow_the_last_sound():
    # 숫자 끝 글자도 읽는 소리로 받침을 따진다: 일(받침) → 은, 이(없음) → 는
    assert "2026-13-01은" in period.parse("2026-13-01", TODAY)["error"]
    assert "2026-13-02는" in period.parse("2026-13-02", TODAY)["error"]
