"""퍼널 계산: 순서 뒤바뀜, 되돌아감, 같은 시각, 기간 제한 경계, 단계 하나, 입력 검사. 데이터는 테스트 안에서 지어낸다(손으로 센 값과 맞춘다)."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from core import funnel

T0 = dt.datetime(2026, 1, 1, 12, 0, 0)


def table(rows):
    """rows = [(사용자, 단계, 시작 후 초)] → ibis 메모리 표."""
    df = pl.DataFrame({"uid": [r[0] for r in rows], "ev": [r[1] for r in rows],
                       "ts": [T0 + dt.timedelta(seconds=r[2]) for r in rows]}, schema={"uid": pl.Int64, "ev": pl.String, "ts": pl.Datetime("us")})
    return ibis.memtable(df.to_arrow())


def run(rows, steps, window=None):
    return funnel.compute(table(rows), "uid", "ev", "ts", steps, window)


def users(result):
    return [s["users"] for s in result["steps"]]


def test_basic_funnel_numbers():
    rows = [(1, "a", 0), (1, "b", 10), (1, "c", 20),   # 끝까지
            (2, "a", 0), (2, "b", 10),                 # b까지
            (3, "a", 0),                               # a만
            (4, "b", 0)]                               # a 없이 b: 안 센다
    r = run(rows, ["a", "b", "c"])
    assert users(r) == [3, 2, 1]
    s = r["steps"]
    assert (s[0]["conv"], s[0]["drop"]) == (1.0, 0)
    assert (s[1]["conv"], s[1]["from_first"], s[1]["drop"]) == (2 / 3, 2 / 3, 1)
    assert (s[2]["conv"], s[2]["from_first"], s[2]["drop"]) == (0.5, 1 / 3, 1)
    assert r["window"] is None


def test_order_swapped_does_not_count():
    # 사용자 1은 b가 a보다 먼저: 퍼널 a→b에는 못 든다
    r = run([(1, "b", 0), (1, "a", 10), (2, "a", 0), (2, "b", 10)], ["a", "b"])
    assert users(r) == [2, 1]


def test_back_and_forth_uses_earliest_forward_path():
    # a, b, a, b 처럼 되돌아가도 한 번 도달한 것으로 센다. 중간의 다른 행동(x)은 무시
    rows = [(1, "a", 0), (1, "x", 1), (1, "b", 2), (1, "a", 3), (1, "b", 4), (1, "c", 5),
            (2, "b", 0), (2, "a", 5), (2, "b", 6), (2, "c", 2)]   # 2: c가 b보다 먼저, 이후 b가 있어도 c는 b 뒤여야 함
    r = run(rows, ["a", "b", "c"])
    assert users(r) == [2, 2, 1]
    # 사용자 2: T1=5, T2=6, c는 2초라 6보다 앞서서 못 센다


def test_same_timestamp_counts():
    r = run([(1, "a", 5), (1, "b", 5), (1, "c", 5), (2, "a", 5), (2, "b", 4)], ["a", "b", "c"])
    assert users(r) == [2, 1, 1]


def test_window_boundary_inclusive():
    rows = [(1, "a", 0), (1, "b", 60),     # 정확히 60초: 포함
            (2, "a", 0), (2, "b", 61),     # 61초: 제외
            (3, "a", 0), (3, "b", 30), (3, "c", 90)]  # c는 첫 단계로부터 90초
    assert users(run(rows, ["a", "b"], 60)) == [3, 2]
    r = run(rows, ["a", "b", "c"], 90)
    assert users(r) == [3, 3, 1] and r["window"] == 90
    assert users(run(rows, ["a", "b", "c"], 89)) == [3, 3, 0]
    assert users(run(rows, ["a", "b"])) == [3, 3]


def test_window_uses_first_step_not_previous():
    # b가 a 뒤 50초, c가 b 뒤 50초: 단계 사이는 짧아도 첫 단계로부터는 100초
    rows = [(1, "a", 0), (1, "b", 50), (1, "c", 100)]
    assert users(run(rows, ["a", "b", "c"], 60)) == [1, 1, 0]
    assert users(run(rows, ["a", "b", "c"], 100)) == [1, 1, 1]


def test_single_step_and_empty_first_step():
    r = run([(1, "a", 0), (1, "a", 5), (2, "a", 1), (3, "b", 0)], ["a"])
    assert users(r) == [2] and r["steps"][0]["conv"] == 1.0 and r["steps"][0]["from_first"] == 1.0
    none = run([(1, "a", 0)], ["z", "a"])
    assert users(none) == [0, 0]
    assert none["steps"][0]["conv"] is None and none["steps"][1]["from_first"] is None


def test_numeric_step_column_and_date_time():
    df = pl.DataFrame({"u": ["x", "x", "y"], "lv": [1, 2, 2], "d": [dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 1)]})
    r = funnel.compute(ibis.memtable(df.to_arrow()), "u", "lv", "d", [1, "2"])
    assert users(r) == [1, 1]


def test_validation_messages():
    t = table([(1, "a", 0)])
    bad = [
        (dict(user="nope"), "'nope'"),
        (dict(time="ev"), "날짜"),
        (dict(steps=[]), "1~10"),
        (dict(steps=list("abcdefghijk")), "1~10"),
        (dict(steps=["a", "a"]), "두 번"),
        (dict(window_seconds=0), "0보다"),
        (dict(window_seconds="10"), "0보다"),
        (dict(step=""), "단계 컬럼"),
    ]
    for change, text in bad:
        args = dict(user="uid", step="ev", time="ts", steps=["a"], window_seconds=None) | change
        with pytest.raises(ValueError, match=text):
            funnel.compute(t, **args)


def test_ten_steps_allowed():
    rows = [(1, f"s{i}", i) for i in range(10)]
    assert users(run(rows, [f"s{i}" for i in range(10)])) == [1] * 10
