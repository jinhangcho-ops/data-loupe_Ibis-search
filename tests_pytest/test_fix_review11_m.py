"""11차 리뷰 M 묶음(지표 계산)의 되돌림 시험. 데이터는 시험 안에서 지어낸다."""
import datetime as dt
import math

import polars as pl
import pytest
from lazy import ibis

from metrics import metric_calc as mc
from metrics import metric_catalog as cat

D = dt.datetime
U = {"사용자": "uid", "시각": "ts"}
AB = {"실험군": "g", "지표 값": "v", "대조군 값": "c", "실험군 값": "t"}


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def run(entry_id, slots, table):
    return cat.compute(entry_id, slots, {}, cat.Ctx(table=table), None)


def row(got, name):
    return next(r["value"] for r in got["rows"] if r["name"] == name)


# ---------- 11D3 작은 표본은 판단 보류 ----------

def test_tiny_proportion_sample_is_not_called_significant():
    # 대조 0/3 대 실험 3/3: z 검정 p=0.0143이지만 Fisher p≈0.10, 표본이 너무 적다
    t = mem(g=["c"] * 3 + ["t"] * 3, v=[0, 0, 0, 1, 1, 1])
    got = run("pvalue", AB, t)
    assert got["value"] == pytest.approx(0.0143, abs=1e-3)
    assert not any("우연이라고 보기 어려워요" in n for n in got["notes"]) and any("판단 보류" in n for n in got["notes"])
    for eid in ("power", "mde"):
        assert any("표본이 적어" in n for n in run(eid, AB, t)["notes"]), eid


def test_tiny_welch_sample_is_not_called_significant():
    t = mem(g=["c", "c", "t", "t"], v=[1.0, 1.1, 2.0, 2.1])
    got = run("pvalue", AB, t)
    assert got["value"] < 0.05 and any("판단 보류" in n for n in got["notes"])


def test_large_balanced_sample_keeps_the_verdict():
    t = mem(g=["c"] * 1000 + ["t"] * 1000, v=[0] * 900 + [1] * 100 + [0] * 850 + [1] * 150)
    got = run("pvalue", AB, t)
    assert any("우연이라고 보기 어려워요" in n for n in got["notes"]) and not any("표본이 적어" in n or "배정" in n for n in got["notes"])
    # 11G4 차이·Lift의 95% 신뢰구간(정규 근사)
    se = math.sqrt(0.1 * 0.9 / 1000 + 0.15 * 0.85 / 1000)
    assert row(got, "차이 95% 신뢰구간 아래") == pytest.approx(0.05 - 1.959964 * se, abs=1e-6)
    assert row(got, "차이 95% 신뢰구간 위") == pytest.approx(0.05 + 1.959964 * se, abs=1e-6)
    sl = math.sqrt(0.9 / 100 + 0.85 / 150)
    assert row(got, "Lift 95% 신뢰구간 아래") == pytest.approx(math.exp(math.log(1.5) - 1.959964 * sl) - 1, abs=1e-6)
    assert row(got, "Lift 95% 신뢰구간 위") == pytest.approx(math.exp(math.log(1.5) + 1.959964 * sl) - 1, abs=1e-6)


# ---------- 11G4 배정 비율 검사(SRM) ----------

def test_sample_ratio_mismatch_is_warned():
    t = mem(g=["c"] * 1000 + ["t"] * 800, v=[0, 1] * 500 + [0, 1] * 400)  # 1:1을 기대했는데 1000:800 → χ²≈22, p≈3e-6
    for eid in ("pvalue", "lift", "power", "mde"):
        assert any("배정 비율" in n for n in run(eid, AB, t)["notes"]), eid
    assert mc.srm_p(1000, 800) < 1e-5 and mc.srm_p(1000, 1000) == 1.0


# ---------- 11G3 끝나지 않은 마지막 기간 ----------

def test_mau_leaves_out_the_month_in_progress():
    # 8·9월 100명, 10/1까지 10명 → 10월은 진행 중이라 평균에서 뺀다
    ts = [D(2026, 8, 5)] * 100 + [D(2026, 9, 5)] * 100 + [D(2026, 10, 1)] * 10
    t = mem(uid=list(range(100)) * 2 + list(range(10)), ts=ts)
    got = run("mau", U, t)
    assert got["value"] == 100 and any("진행 중" in n for n in got["notes"])
    assert [r["users"] for r in got["rows"]] == [100, 100, 10]  # 표에는 그대로 보인다


def test_mau_with_only_one_month_in_progress_keeps_it():
    t = mem(uid=[1, 2], ts=[D(2026, 3, 1), D(2026, 3, 2)])
    assert run("mau", U, t)["value"] == 2


def test_weekly_retention_leaves_out_the_week_in_progress():
    # 3/2(월) 주에 10명, 그중 3명이 3/10(화)에 다시 옴. 자료는 3/10(화)에 끝나 둘째 주는 진행 중
    t = mem(uid=list(range(10)) + [0, 1, 2], ts=[D(2026, 3, 2)] * 10 + [D(2026, 3, 10)] * 3)
    got = run("retention_classic", {**U, "단위": "week", "N": 1}, t)
    assert got["value"] is None and got["rows"][0]["d0"] == 1.0 and got["rows"][0]["d1"] is None
    assert any("진행 중" in n for n in got["notes"])
    done = mem(uid=list(range(10)) + [0, 1, 2, 9], ts=[D(2026, 3, 2)] * 10 + [D(2026, 3, 10)] * 3 + [D(2026, 3, 15)])  # 일요일까지 있으면 확정
    assert run("retention_classic", {**U, "단위": "week", "N": 1}, done)["value"] == pytest.approx(0.4)


# ---------- 11D11 고착도 분모는 그 달 날수 ----------

def test_stickiness_divides_by_days_in_month():
    # 3월 자료: 3/1에 10명, 3/31에 1명(그 10명 중 하나) → DAU 평균 11/31, MAU 10
    t = mem(uid=list(range(10)) + [0], ts=[D(2026, 3, 1)] * 10 + [D(2026, 3, 31)])
    got = run("stickiness", U, t)
    assert got["value"] == pytest.approx(11 / 31 / 10)
    # 진행 중인 달은 자료 끝 날까지: 4/1~4/5 중 하루만 10명 → DAU 10/5
    t2 = mem(uid=list(range(10)) + [0], ts=[D(2026, 4, 1)] * 10 + [D(2026, 4, 5)])
    assert run("stickiness", U, t2)["value"] == pytest.approx(11 / 5 / 10)
