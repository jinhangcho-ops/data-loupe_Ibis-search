"""모니터링 규칙(watch.py): 상태 규칙 판정(가장 나쁜 단계), 끊김 규칙, since 유지·해제, N분째 글, 영향받는 대상(연결선 한 단계), 신선도 문장, 규칙 검사. 값은 테스트 안에서 지어낸다."""
import datetime as dt

import pytest

import watch

NOW = dt.datetime(2026, 10, 22, 12, 0, 0)
RULES = [{"column": "cpu", "op": ">=", "value": 80, "level": "warn"}, {"column": "cpu", "op": ">=", "value": 95, "level": "bad"}, {"column": "state", "op": "=", "value": "DOWN", "level": "bad"}]


def row(name, cpu, state="UP", minutes_ago=1, kind="서버"):
    return {"name": name, "cpu": cpu, "state": state, "at": NOW - dt.timedelta(minutes=minutes_ago), "kind": kind}


def run(rows, **more):
    return watch.evaluate(rows, RULES, "name", NOW, time_column="at", kind_column="kind", **more)


def levels(found):
    return {e["name"]: e["level"] for e in found["entities"]}


def test_levels_take_the_worst_matching_rule():
    found = run([row("a", 10), row("b", 85), row("c", 97), row("d", 10, "down"), row("e", None)])
    assert levels(found) == {"a": "ok", "b": "warn", "c": "bad", "d": "bad", "e": "ok"}  # 97은 주의·이상 둘 다 맞지만 이상, 글자는 대소문자 무시, 빈 값은 맞지 않음
    assert found["counts"] == {"ok": 2, "warn": 1, "bad": 2}
    assert [e["reasons"] for e in found["entities"] if e["name"] == "c"] == [["cpu >= 80", "cpu >= 95"]]


def test_no_data_rule_makes_entity_bad():
    rows = [row("fresh", 10, minutes_ago=3), row("stale", 10, minutes_ago=45), {"name": "never", "cpu": 1, "state": "UP", "at": None, "kind": "서버"}]
    found = run(rows, stale_minutes=30)
    assert levels(found) == {"fresh": "ok", "never": "bad", "stale": "bad"}
    assert "30분 넘게 새 데이터가 없어요" in found["entities"][2]["reasons"][0]
    assert run(rows)["counts"]["bad"] == 0  # 끊김 규칙이 없으면 판정하지 않는다


def test_latest_row_per_entity_is_used():
    rows = [row("a", 99, minutes_ago=10), row("a", 10, minutes_ago=1), row("b", 10, minutes_ago=5), row("b", 99, minutes_ago=2)]
    assert levels(run(rows)) == {"a": "ok", "b": "bad"}


def test_since_kept_while_bad_and_cleared_when_recovered():
    first = run([row("a", 99), row("b", 10)])
    assert first["since"] == {"a": "2026-10-22T12:00:00"} and first["entities"][0]["since_text"] == "방금"
    later = NOW + dt.timedelta(minutes=7)
    second = watch.evaluate([row("a", 99), row("b", 85)], RULES, "name", later, "at", None, first["since"])
    assert second["since"] == {"a": "2026-10-22T12:00:00", "b": "2026-10-22T12:07:00"}  # a는 처음 나빠진 시각 그대로, b는 새로
    assert {e["name"]: e["since_text"] for e in second["entities"]} == {"a": "7분째", "b": "방금"}
    third = watch.evaluate([row("a", 10), row("b", 85)], RULES, "name", later + dt.timedelta(minutes=3), "at", None, second["since"])
    assert third["since"] == {"b": "2026-10-22T12:07:00"}  # a는 좋아져서 지운다
    assert watch.since_text("2026-10-22T10:00:00", NOW) == "2시간째" and watch.since_text("2026-10-20T12:00:00", NOW) == "2일째" and watch.since_text("엉터리", NOW) == ""


def test_impact_follows_links_one_step_downstream():
    links = [("a", "b"), ("b", "c"), ("x", "y"), ("a", "c")]
    found = run([row("a", 99), row("b", 10), row("c", 10), row("x", 10), row("y", 10)], links=links)
    assert {e["name"]: e["impact"] for e in found["entities"]} == {"a": ["b", "c"], "b": [], "c": [], "x": [], "y": []}  # 한 단계만(b의 아래 c는 b가 나쁘지 않아 제외)
    both = run([row("a", 99), row("b", 99), row("c", 10)], links=links)
    assert {e["name"]: e["impact"] for e in both["entities"]}["a"] == ["c"]  # 이미 나쁜 대상은 영향 목록에서 뺀다
    assert watch.affected({"a"}, [("a", "b"), ("a", "b")]) == {"a": ["b"]}


def test_freshness_sentences():
    assert watch.freshness(NOW - dt.timedelta(minutes=2), 30, NOW) == "마지막 데이터 11:58 · 2분 전"
    assert watch.freshness(NOW - dt.timedelta(minutes=45), 30, NOW) == "45분째 새 데이터가 없어요 (마지막 11:15)"
    assert watch.freshness(NOW - dt.timedelta(minutes=90), None, NOW) == "마지막 데이터 10:30 · 1시간 전" and watch.freshness(None, 30, NOW) == ""
    assert run([row("a", 1, minutes_ago=2)], stale_minutes=30)["fresh"] == "마지막 데이터 11:58 · 2분 전"


def test_kind_and_text_values():
    found = run([row("a", 1, kind="라우터"), {"name": "b", "cpu": "높음", "state": "UP", "at": NOW, "kind": None}])
    assert [e["kind"] for e in found["entities"]] == ["라우터", ""] and levels(found) == {"a": "ok", "b": "ok"}  # 숫자가 아닌 값은 숫자 규칙에 맞지 않는다


@pytest.mark.parametrize("rules, message", [([{"column": "", "op": ">", "value": 1, "level": "bad"}], "컬럼 이름"), ([{"column": "a", "op": "~", "value": 1, "level": "bad"}], "비교"),
                                            ([{"column": "a", "op": ">", "value": 1, "level": "fatal"}], "단계"), ([{"column": "a", "op": ">", "value": None, "level": "bad"}], "값"), ([{"column": "a", "op": ">", "value": 1, "level": "bad"}] * 21, "20개"),
                                            ("규칙", "20개")])
def test_bad_rules(rules, message):
    with pytest.raises(ValueError, match=message):
        watch.check_rules(rules)
    assert watch.check_rules(None) == []
