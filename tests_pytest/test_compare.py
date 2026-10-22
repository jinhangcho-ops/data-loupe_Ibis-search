"""비교 문장: 늘고 줄고 같은 경우, 직전 값 0·없음, %p, ₩·쉼표, 일부만 있음, 좋고 나쁨, 조사, 값이 없는 경우."""
import math

import pytest

import compare
from compare import compare as cmp


def test_number_up_and_down_with_percent_and_delta():
    up = cmp(1234, 1000, "number", "span", "직전 7일")
    assert up["text"] == "직전 7일보다 23.4% 늘었어요 (+234)"
    assert (up["delta_text"], up["direction"], up["tone"]) == ("+234", "up", "good")
    assert up["pct"] == pytest.approx(23.4)
    down = cmp(750, 1000, "number", "span", "직전 7일")
    assert down["text"] == "직전 7일보다 25% 줄었어요 (-250)"
    assert (down["delta_text"], down["direction"], down["tone"], down["pct"]) == ("-250", "down", "bad", -25.0)


def test_thousands_separators_and_decimals():
    assert cmp(1_500_000, 1_000_000)["text"] == "직전 기간보다 50% 늘었어요 (+500,000)"
    assert cmp(10.456, 10.0)["delta_text"] == "+0.46"
    assert cmp(1234.5, 1000)["delta_text"] == "+234.5"
    assert cmp(100.0, 50.0)["delta_text"] == "+50"  # 끝의 .0은 떼고
    assert compare.format_value(1234567.891) == "1,234,567.89"
    assert compare.format_value(None) == "" and compare.format_value("가") == ""


def test_currency_won_and_thousands():
    got = cmp(1_234_000, 1_000_000, "currency", "span", "직전 30일")
    assert got["text"] == "직전 30일보다 23.4% 늘었어요 (+₩234,000)"
    assert got["delta_text"] == "+₩234,000"
    assert cmp(900, 1000, "currency")["delta_text"] == "-₩100"
    assert compare.format_value(1234567, "currency") == "₩1,234,567"
    assert compare.format_value(-5000, "currency") == "-₩5,000"
    assert compare.format_delta(-5000.4, "currency") == "-₩5,000"


def test_percent_format_uses_percent_points():
    got = cmp(0.138, 0.123, "percent", "span", "직전 7일")
    assert got["delta_text"] == "+1.5%p"
    assert got["text"] == "직전 7일보다 1.5%p 늘었어요 (12.3% → 13.8%)"
    assert got["direction"] == "up"
    down = cmp(0.10, 0.125, "percent", "point", "7일 전")
    assert down["delta_text"] == "-2.5%p" and down["text"].startswith("7일 전보다 2.5%p 줄었어요")
    assert compare.format_value(0.5, "percent") == "50%"
    # 비율 값은 0도 값이라 0에서도 %p로 쓴다
    assert cmp(0.2, 0, "percent", "span", "직전 7일")["text"] == "직전 7일보다 20%p 늘었어요 (0% → 20%)"


def test_previous_zero_or_missing_has_no_ratio():
    zero = cmp(1234, 0, "number", "span", "직전 7일")
    assert zero["text"] == "직전 7일에는 값이 없었어요 (+1,234)"
    assert zero["pct"] is None and zero["direction"] == "up" and zero["tone"] == "good"
    none = cmp(1234, None, "number", "span", "직전 7일")
    assert none["text"] == zero["text"] and none["pct"] is None
    assert cmp(-5, 0)["text"] == "직전 기간에는 값이 없었어요 (-5)" and cmp(-5, 0)["direction"] == "down"
    assert cmp(5, None, "currency", label="직전 7일")["text"] == "직전 7일에는 값이 없었어요 (+₩5)"
    # 직전이 없고 현재도 0이면 같다고 하지 않고 값이 없었다고 말한다
    assert cmp(0, None, label="직전 7일")["text"] == "직전 7일에는 값이 없었어요 (0)"


def test_same_values_say_same_with_the_right_particle():
    assert cmp(100, 100, label="직전 7일")["text"] == "직전 7일과 같아요"  # 일(받침) → 과
    assert cmp(100, 100, label="7일 전")["text"] == "7일 전과 같아요"
    assert cmp(100, 100, label="어제")["text"] == "어제와 같아요"
    assert cmp(0, 0, label="직전 7일")["text"] == "직전 7일과 같아요"
    same = cmp(100.0, 100, label="직전 7일")
    assert (same["direction"], same["tone"], same["pct"], same["delta_text"]) == ("flat", "neutral", 0.0, "0")
    assert cmp(0.1 + 0.2, 0.3)["direction"] == "flat"  # 부동소수 오차는 같은 값


def test_small_changes_keep_two_decimals():
    assert cmp(100.05, 100)["text"].startswith("직전 기간보다 0.05% 늘었어요")
    assert cmp(110, 100)["text"].startswith("직전 기간보다 10% 늘었어요")
    assert cmp(100.4, 100)["text"].startswith("직전 기간보다 0.4% 늘었어요")
    assert cmp(30000, 100)["text"].startswith("직전 기간보다 29,900% 늘었어요")


def test_negative_previous_uses_absolute_value():
    got = cmp(-50, -100)
    assert got["pct"] == pytest.approx(50.0) and got["direction"] == "up"  # (−50 − −100) / |−100|
    assert cmp(-150, -100)["pct"] == pytest.approx(-50.0)


def test_better_up_or_down_sets_tone():
    assert cmp(120, 100, better="up")["tone"] == "good"
    assert cmp(80, 100, better="up")["tone"] == "bad"
    assert cmp(120, 100, better="down")["tone"] == "bad"
    assert cmp(80, 100, better="down")["tone"] == "good"
    assert cmp(100, 100, better="down")["tone"] == "neutral"
    assert cmp(120, 100, better=None)["tone"] == "neutral"
    assert cmp(5, 0, better="down")["tone"] == "bad"  # 0에서 늘어난 것도 방향대로
    assert cmp(120, 100)["tone"] == "good"  # 기본은 오르는 쪽이 좋다


def test_partial_note_for_span_and_point():
    assert cmp(120, 100, "number", "span", "직전 7일", partial=True)["text"] == "직전 7일보다 20% 늘었어요 (+20) (직전 기간 일부만 있음)"
    assert cmp(120, 100, "number", "point", "7일 전", partial=True)["text"].endswith("(비교 시점 값이 일부만 있음)")
    assert "일부만" not in cmp(120, 100)["text"]


def test_missing_or_bad_numbers_never_invent_values():
    got = cmp(None, 100, label="직전 7일")
    assert got["text"] == "이번 값이 없어 직전 7일과 비교할 수 없어요"
    assert (got["pct"], got["direction"], got["tone"], got["delta_text"]) == (None, "flat", "neutral", "")
    assert cmp(None, None)["direction"] == "flat"
    assert cmp(math.nan, 100)["pct"] is None and cmp(math.inf, 100)["direction"] == "flat"
    assert cmp(100, math.nan, label="직전 7일")["text"] == "직전 7일에는 값이 없었어요 (+100)"
    assert cmp(True, 1)["direction"] == "flat"  # bool은 숫자로 보지 않는다


def test_invalid_mode_or_format_raises():
    with pytest.raises(ValueError):
        cmp(1, 2, fmt="hex")
    with pytest.raises(ValueError):
        cmp(1, 2, mode="week")


def test_particle_reads_digits_and_percent():
    assert compare.particle("지난달", "과", "와") == "지난달과"
    assert compare.particle("어제", "과", "와") == "어제와"
    assert compare.particle("2026-10-01", "은", "는") == "2026-10-01은"  # 일
    assert compare.particle("2026-10-02", "은", "는") == "2026-10-02는"  # 이
    assert compare.particle("10", "이", "가") == "10이"  # 십
    assert compare.particle("3", "이", "가") == "3이"  # 삼
    assert compare.particle("5", "이", "가") == "5가"  # 오
    assert compare.particle("50%", "은", "는") == "50%는"  # 퍼센트
    assert compare.ending("강남", "이에요", "예요") == "이에요"
    assert compare.ending("대구", "이에요", "예요") == "예요"
    assert compare.particle("CTR", "은", "는") == "CTR은" and compare.particle("KPI", "은", "는") == "KPI는"
