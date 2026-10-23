"""지표 사전(metric_catalog·metric_kernels·catalog_app): 항목 자료의 모양, 쓸 수 있는지(이유), 영역마다 손으로 계산한 값, 두 집계의 비율 지표, 다른 테이블이 필요한 지표의 맞춤 구절,
라우트(FREE·원격 DB에서 미리 세지 않음·파일 연결·내 지표로 저장).
데이터는 테스트 안에서 지어내고 값은 손으로 센 것과 맞춘다."""
import datetime as dt
import math
import re

import duckdb
import numpy as np
import polars as pl
import pytest
from conftest import spy_queries
from lazy import ibis

from core import held
from metrics import metric_calc as mc
from metrics import metric_catalog as cat
from core import server

D = dt.datetime


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def run(entry_id, slots, table, align=None, other=None, limit=None):
    """표 하나에서 항목을 구한다(other = 이름 → 표)."""
    ctx = cat.Ctx(table=table, other=(lambda name: other[name]) if other else None)
    return cat.compute(entry_id, slots, {}, ctx, align, limit)


def value(entry_id, slots, table, **kw):
    return run(entry_id, slots, table, **kw)["value"]


def parts(entry_id, slots, table, **kw):
    return {p["id"]: p["value"] for p in run(entry_id, slots, table, **kw)["pair"]}


# 행동 기록: 사용자 1은 3/1 접속·페이지·3/2 구매, 사용자 2는 3/1 접속·3/3 구매, 사용자 3은 3/2 접속(한 시간 뒤 페이지)
ROWS = [(1, D(2026, 3, 1, 9, 0), "login", 100), (1, D(2026, 3, 1, 9, 10), "page", 0), (1, D(2026, 3, 2, 9, 0), "buy", 500),
        (2, D(2026, 3, 1, 10, 0), "login", 0), (2, D(2026, 3, 3, 10, 0), "buy", 300), (3, D(2026, 3, 2, 11, 0), "login", 0), (3, D(2026, 3, 2, 12, 0), "page", 0)]


@pytest.fixture
def events():
    return mem(uid=[r[0] for r in ROWS], ts=[r[1] for r in ROWS], ev=[r[2] for r in ROWS], amt=[r[3] for r in ROWS])


U, UA = {"사용자": "uid", "시각": "ts"}, {"사용자": "uid", "금액": "amt"}


# ---------- 항목 자료의 모양 ----------

def test_every_entry_has_korean_texts_and_valid_links():
    assert len(cat.ENTRIES) >= 75 and len({e["id"] for e in cat.ENTRIES}) == len(cat.ENTRIES)
    areas = {a["id"] for a in cat.AREAS}
    assert {e["area"] for e in cat.ENTRIES} == areas and len(areas) == 8
    hangul = re.compile("[가-힣]")
    for e in cat.ENTRIES:
        for key in ("name", "help", "sentence", "formula"):
            assert hangul.search(e[key]) or key == "formula", (e["id"], key)
        assert e["abbr"] and isinstance(e["needs"], list) and e["slots"] is not None, e["id"]
        names = [s["name"] for s in e["slots"]]
        assert len(set(names)) == len(names), e["id"]
        assert set(re.findall(r"\{([^}]+)\}", e["sentence"])) <= set(names), e["id"]  # 문장의 빈칸은 모두 빈칸 목록에 있다
        assert e["calc"] in __import__("metrics.metric_kernels", fromlist=["KERNELS"]).KERNELS and e["format"] in ("number", "percent") and e["hint"] in ("", *cat.HINTS)
        assert all(p in cat.BY_ID or p in cat.PARTS for p in e["pair"]), e["id"]
        assert all(s["name"] in names for s in e["slots"] if s["constant"]) and e["constants"] == [s["name"] for s in e["slots"] if s["constant"]]
        for s in e["slots"]:
            assert s["role"] and s["type"] in __import__("query.sentences", fromlist=["SLOT_TYPES"]).SLOT_TYPES and s["label"], (e["id"], s["name"])
        for need in e["needs"]:
            assert need["name"] in names and hangul.search(need["name"]), e["id"]


def test_three_principles_are_data():
    ratio_ids = {"arppu", "arpu", "ctr", "cvr", "pur", "cac", "roas", "take_rate", "churn", "retention_classic", "precision", "stickiness"}
    assert all(cat.BY_ID[i]["pair"] for i in ratio_ids)
    assert cat.BY_ID["arppu"]["pair"] == ["gross_revenue", "pu"]  # ARPPU의 짝 절대량은 PU(와 매출)
    assert {i for i, e in cat.BY_ID.items() if e["spread"]} >= {"dau", "arpu", "arppu", "aov", "asd", "tsd", "ttv", "pv_per_uv", "purchase_frequency"}
    assert {e["hint"] for e in cat.ENTRIES} == {"", "north_star", "counter", "guardrail"} and cat.BY_ID["guardrail"]["hint"] == "guardrail"
    listing = cat.catalog()
    assert listing["spread"] == ["median", "p90", "p99"] and listing["parts"]["uv"] == "고유 사용자 수" and listing["entries"][0]["pair_names"] == {}
    assert next(e for e in listing["entries"] if e["id"] == "arppu")["pair_names"] == {"gross_revenue": "총매출", "pu": "결제 사용자 수"}


def test_units_match_the_calculation_module_and_startup_stays_light():
    assert cat.UNITS == mc.UNITS  # 시작할 때 numpy를 올리지 않으려고 따로 둔 상수


def test_template_makes_catalog_entries_usable_as_metric_templates():
    t = cat.template("catalog:arpu")
    assert t["kind"] == "metric" and t["id"] == "catalog:arpu" and [s["name"] for s in t["slots"]] == ["사용자", "금액"] and cat.is_catalog(t["id"]) and not cat.is_catalog("m03")
    with pytest.raises(ValueError, match="지표 사전 항목이 없습니다"):
        cat.get("catalog:nothing")


# ---------- 쓸 수 있는지 ----------

def test_availability_reasons():
    plain = {"id": "num", "name": "text", "created": "date"}
    got = cat.available(plain)
    assert got["uv"]["usable"] and got["pv"]["usable"] and got["dau"]["usable"] and got["dau"]["suggest"] == {"사용자": "id", "시각": "created"}
    assert got["cpc"] == {"usable": False, "reason": "광고비·클릭 수 컬럼이 있는 테이블이 필요해요.", "suggest": {}}
    assert got["ctr"]["reason"] == "클릭 수·노출 수 컬럼이 있는 테이블이 필요해요."
    assert cat.available({"id": "num"})["pcu"]["reason"] == "시작·끝 컬럼이 있는 테이블이 필요해요."
    assert got["gini"]["usable"] and got["precision"]["reason"] == "정답·예측 또는 점수 컬럼이 있는 테이블이 필요해요."
    ads = cat.available({"day": "date", "ad_cost": "num", "impressions_cnt": "num", "clicks": "num", "installs": "num"})
    assert ads["cpc"]["usable"] and ads["cpc"]["suggest"] == {"광고비": "ad_cost", "클릭 수": "clicks"} and ads["ipm"]["usable"] and not ads["cpa"]["usable"]
    cross = cat.available({"u": "text", "t": "date"})["cac"]
    assert cross["usable"] and "맞춤 구절" in cross["note"]
    none = cat.available({"u": "text", "t": "date"}, {"sales": {"id": "num"}})["cac"]
    assert not none["usable"] and none["reason"] == "광고비 컬럼이 있는 테이블이 필요해요."
    assert cat.available({"u": "text", "t": "date"}, {"ads": {"cost": "num"}})["cac"]["usable"]


def test_wrong_slots_are_refused_with_reasons(events):
    with pytest.raises(ValueError, match="‘사용자’ 칸을 채워 주세요"):
        run("uv", {}, events)
    with pytest.raises(ValueError, match="'ghost' 컬럼이 없습니다"):
        run("uv", {"사용자": "ghost"}, events)
    with pytest.raises(ValueError, match="글자 컬럼이라 쓸 수 없어요"):
        run("gross_revenue", {"금액": "ev"}, events)
    with pytest.raises(ValueError, match="1 이상의 정수"):
        run("retention_classic", {**U, "N": 0}, events)
    with pytest.raises(ValueError, match="day, week, month 중 하나"):
        run("uv", {**U, "단위": "year"}, events)
    with pytest.raises(ValueError, match="이 지표에 없는 칸"):
        run("uv", {**U, "금액": "amt"}, events)
    with pytest.raises(ValueError, match="0 이상 100 이하"):
        run("net_revenue", {"금액": "amt", "수수료(%)": 120}, events)
    with pytest.raises(ValueError, match="단계를 2개 이상"):
        run("ftue", {**U, "이벤트": "ev", "단계들": ["login"]}, events)
    with pytest.raises(ValueError, match="가공"):
        run("uv", {"사용자": {"column": "uid", "prep": "lower"}}, events)
    with pytest.raises(ValueError, match="단위별로 보려면 시각"):
        run("uv", {"사용자": "uid", "단위": "day"}, events)


# ---------- 1 트래픽·활성도 ----------

def test_traffic_values(events):
    assert value("uv", {"사용자": "uid"}, events) == 3
    by_day = run("uv", {**U, "단위": "day"}, events)["rows"]
    assert [(r["period"], r["users"]) for r in by_day] == [("2026-03-01", 2), ("2026-03-02", 2), ("2026-03-03", 1)]
    assert value("pv", {}, events) == 7 and value("pv", {"이벤트": "ev", "이벤트 값": "page"}, events) == 2
    assert value("pv_per_uv", {"사용자": "uid"}, events) == pytest.approx(7 / 3)
    assert parts("pv_per_uv", {"사용자": "uid"}, events) == {"pv": 7, "uv": 3}
    dau = run("dau", U, events)
    assert dau["value"] == pytest.approx(5 / 3) and dau["spread"]["median"] == 2.0 and dau["spread"]["n"] == 3  # 일별 2·2·1
    assert value("wau", U, events) == pytest.approx(2.5)  # 3/1(일요일)은 앞 주 → 주 1: {1,2}=2, 주 2: {1,2,3}=3
    assert value("mau", U, events) == 3
    st = run("stickiness", U, events)
    assert st["value"] == pytest.approx((5 / 3) / 3) and {p["id"]: p["value"] for p in st["pair"]} == {"dau": pytest.approx(5 / 3), "mau": 3.0}
    assert st["format"] == "percent"


def test_concurrency_values():
    t = mem(s=[D(2026, 3, 1, 9), D(2026, 3, 1, 9, 30), D(2026, 3, 1, 10)], e=[D(2026, 3, 1, 10), D(2026, 3, 1, 10, 30), None])
    # 동시 수: 9:00 1 · 9:30 2 · 10:00 끝(−1)과 시작(+1) 겹침 → 2 유지 · 10:30 1 (세 번째는 끝나지 않음)
    assert value("pcu", {"시작": "s", "끝": "e"}, t) == 2
    assert value("acu", {"시작": "s", "끝": "e"}, t) == pytest.approx((1 * 30 + 2 * 30 + 2 * 30 + 1 * 0) / 90)  # 9:00~10:30
    mcu = run("mcu", {"시작": "s", "끝": "e"}, t)
    assert mcu["value"] == 2 and mcu["rows"] == [{"period": "2026-03", "mcu": 2}]


def test_session_family(events):
    s = {**U}
    # 세션(30분 간격): 사용자1 {9:00, 9:10} {3/2}, 사용자2 {3/1} {3/3}, 사용자3 {11:00} {12:00} → 6개, 길이는 600초 하나뿐
    assert value("session_count", s, events) == pytest.approx(6 / 5)  # 접속한 사용자·날 5개
    assert parts("session_count", s, events) == {"sessions": 6, "user_days": 5}
    asd = run("asd", s, events)
    assert asd["value"] == pytest.approx(100.0) and asd["spread"]["median"] == 0.0 and asd["spread"]["p90"] == pytest.approx(300.0)
    assert {p["id"]: p["value"] for p in asd["pair"]} == {"stay_seconds": 600.0, "sessions": 6}
    tsd = run("tsd", s, events)
    assert tsd["value"] == pytest.approx(600 / 5) and tsd["spread"]["n"] == 5
    assert value("asd", {**s, "세션 간격(분)": 90}, events) == pytest.approx((600 + 3600) / 5)  # 간격 90분이면 사용자3이 한 세션(3600초) → 세션 5개
    assert value("bounce", s, events) == pytest.approx(5 / 6)
    page = run("bounce", {**s, "이벤트": "ev", "이벤트 값": "page"}, events)  # 페이지 이벤트가 든 세션은 {사용자1 첫, 사용자3 둘째} 둘이고 둘 다 페이지 이벤트가 하나뿐
    assert (page["value"], page["parts"]) == (1.0, {"sessions": 2, "bounces": 2})
    ex = run("exit_rate", {**s, "페이지": "ev"}, events)
    assert ex["value"] == pytest.approx(6 / 7) and {r["page"]: r["exit_rate"] for r in ex["rows"]} == {"login": pytest.approx(2 / 3), "page": 1.0, "buy": 1.0}
    with pytest.raises(ValueError, match="세션을 나눌 간격"):
        run("asd", {**s, "세션 간격(분)": 0}, events)
    keyed = mem(uid=[1, 1, 2], ts=[D(2026, 3, 1, 9), D(2026, 3, 1, 9, 5), D(2026, 3, 1, 9)], sid=["a", "b", "c"])
    assert value("session_count", {**s, "세션 컬럼들": ["sid"]}, keyed) == pytest.approx(3 / 2)  # 세션 컬럼 3개, 사용자·날 2개


def test_dau_split_and_quick_ratio_and_power_curve():
    rows = [(1, 0), (2, 0), (1, 1), (3, 1), (4, 1), (1, 2), (2, 2), (4, 2)]  # 사용자·날
    t = mem(uid=[r[0] for r in rows], ts=[D(2026, 3, 1 + r[1], 9) for r in rows])
    split = run("dau_split", U, t)
    assert [(r["active"], r["new"], r["retained"], r["resurrected"]) for r in split["rows"]] == [(2, 2, 0, 0), (3, 2, 1, 0), (3, 0, 2, 1)]
    assert split["value"] == pytest.approx(8 / 3)
    quick = run("quick_ratio", {**U, "단위": "day"}, t)
    # 3/2: (신규 2 + 복귀 0) ÷ 이탈 1(사용자2) = 2, 3/3: (0 + 복귀 1) ÷ 이탈 1(사용자3) = 1
    assert [r["quick_ratio"] for r in quick["rows"]] == [None, 2.0, 1.0] and quick["value"] == pytest.approx(1.5)
    assert {p["id"]: p["value"] for p in quick["pair"]} == {"new": 4, "resurrected": 1, "churned": 2}
    curve = run("power_curve", {**U, "최근 일수": 2}, t)
    assert [(r["days"], r["users"]) for r in curve["rows"]] == [(1, 2), (2, 2)] and curve["value"] == pytest.approx(1.5)  # 3/2·3/3 중 사용자1·4는 2일, 2·3은 1일


# ---------- 2 퍼널·온보딩 ----------

def test_funnel_values(events):
    s = {**U, "이벤트": "ev", "단계 A": "login", "단계 B": "buy"}
    cvr = run("cvr", s, events)
    assert cvr["value"] == pytest.approx(2 / 3) and {p["id"]: p["value"] for p in cvr["pair"]} == {"users_first": 3, "users_reached": 2}
    assert value("drop_off", s, events) == pytest.approx(1 / 3)
    ttv = run("ttv", s, events)
    assert ttv["value"] == pytest.approx(129600) and ttv["spread"]["median"] == pytest.approx(129600) and ttv["spread"]["p90"] == pytest.approx(164160)  # 86400초·172800초
    assert value("tutorial", {**U, "이벤트": "ev", "튜토리얼 시작 값": "login", "튜토리얼 완료 값": "page"}, events) == pytest.approx(2 / 3)
    assert value("ttfp", {**U, "이벤트": "ev", "가입 값": "login", "첫 결제 값": "buy"}, events) == pytest.approx(129600)
    ftue = run("ftue", {**U, "이벤트": "ev", "단계들": ["login", "page", "buy"]}, events)
    assert [r["users"] for r in ftue["rows"]] == [3, 2, 1] and ftue["value"] == pytest.approx(1 / 3) and ftue["rows"][1]["conv"] == pytest.approx(2 / 3)
    assert value("cart_abandon", {**U, "이벤트": "ev", "장바구니 값": "page", "구매 값": "buy"}, events) == pytest.approx(1 / 2)  # page를 한 사용자1·3 중 buy는 사용자1만


# ---------- 3 리텐션·코호트 ----------

@pytest.fixture
def daily():
    rows = [(1, 0), (1, 1), (1, 2), (2, 0), (2, 2), (3, 1), (4, 1), (4, 2)]  # 사용자·날 번호(3/1 + 번호)
    return mem(uid=[r[0] for r in rows], ts=[D(2026, 3, 1 + r[1], 9) for r in rows])


def test_retention_values(daily):
    # 코호트 3/1 = {1, 2}, 3/2 = {3, 4}, 자료의 끝 3/3
    assert value("retention_classic", {**U, "N": 1}, daily) == pytest.approx((1 + 1) / 4)  # 3/2 활동: 사용자1 / 3/3 활동: 사용자4
    assert value("retention_classic", {**U, "N": 2}, daily) == pytest.approx(1.0)  # 3/1 코호트만 볼 수 있다(둘 다 3/3 활동)
    assert value("retention_rolling", {**U, "N": 1}, daily) == pytest.approx((2 + 1) / 4)
    assert value("retention_range", {**U, "범위 시작 N": 1, "범위 끝 N": 2}, daily) == pytest.approx(3 / 4)
    assert parts("retention_classic", {**U, "N": 1}, daily) == {"cohort_size": 4}
    rows = run("retention_classic", {**U, "N": 1}, daily)["rows"]
    assert [(r["cohort"], r["size"], r["d0"], r["d1"]) for r in rows] == [("2026-03-01", 2, 1.0, 0.5), ("2026-03-02", 2, 1.0, 0.5)]
    flat = mem(uid=[u for u in (1, 2, 3) for _ in range(6)], ts=[D(2026, 3, 1 + d, 9) for _ in (1, 2, 3) for d in range(6)])
    assert value("flattening", {**U, "N": 5}, flat) == 1  # 모두 매일 와서 유지율이 처음부터 수평(1.0)


def test_state_rates(daily):
    churn = run("churn", {**U, "단위": "day"}, daily)
    assert [r["churn_rate"] for r in churn["rows"]] == [None, 0.5, pytest.approx(1 / 3)] and churn["value"] == pytest.approx(5 / 12)
    assert parts("churn", {**U, "단위": "day"}, daily) == {"churned": 2}
    back = run("resurrection", {**U, "단위": "day"}, daily)
    assert [r["resurrection_rate"] for r in back["rows"]] == [None, None, 1.0] and back["value"] == 1.0  # 3/3에 사용자2가 돌아옴, 휴면자는 사용자2 하나
    long_gap = mem(uid=[1, 1, 2], ts=[D(2026, 3, 1), D(2026, 3, 20), D(2026, 3, 1)])
    states = run("churn", {**U, "단위": "week", "미접속 기준(일)": 7}, long_gap)  # 미접속 기준을 주면 창 함수를 쓴다
    assert states["rows"] and all("churn_rate" in r for r in states["rows"])


def test_survival_curve(daily):
    km = run("survival", {**U, "미접속 기준(일)": 1}, daily)
    # 사용자3은 3/2가 마지막이고 자료 끝(3/3)보다 1일 앞서 떠난 것(기간 0), 나머지는 아직 남은 사람(중도절단)
    assert km["rows"][0] == {"t": 0, "survival": 0.75, "at_risk": 4, "events": 1} and km["value"] is None and km["parts"] == {"users": 4}
    t2 = mem(uid=list(range(1, 7)) * 2, ts=[D(2026, 3, 1)] * 6 + [D(2026, 3, 1 + d) for d in (1, 2, 2, 3, 5, 30)])
    got = run("survival", {**U, "미접속 기준(일)": 1}, t2)  # 기간 1·2·2·3·5는 떠남, 30일 쪽(끝)은 남음
    assert [(r["t"], r["at_risk"], r["events"]) for r in got["rows"]] == [(1, 6, 1), (2, 5, 2), (3, 3, 1), (5, 2, 1)]
    assert got["value"] == 2  # S: 5/6 → 5/6 × 3/5 = 0.5 → 1/3 → 0: 처음 0.5 이하가 되는 t = 2


# ---------- 4 수익화·LTV ----------

def test_revenue_values(events):
    s = {**U, "금액": "amt"}
    assert value("gross_revenue", {"금액": "amt"}, events) == 900
    refunds = mem(amt=[1000.0, 500.0], rf=[100.0, 0.0])
    net = run("net_revenue", {"금액": "amt", "환불 금액": "rf", "수수료(%)": 10, "부가세(%)": 10}, refunds)
    assert net["value"] == pytest.approx((1500 - 100) / 1.1 * 0.9) and {p["id"]: p["value"] for p in net["pair"]} == {"gross_revenue": 1500.0, "refund": 100.0}
    assert value("pu", UA, events) == 2
    npa = run("npa", {**s, "단위": "month"}, events)
    assert npa["value"] == 2 and npa["rows"] == [{"period": "2026-03-01", "npa": 2}]
    pur = run("pur", {**s, "단위": "month"}, events)
    assert pur["value"] == pytest.approx(2 / 3) and {p["id"]: p["value"] for p in pur["pair"]} == {"pu": 2, "uv": 3}
    arpu = run("arpu", UA, events)
    assert arpu["value"] == pytest.approx(300) and arpu["spread"]["median"] == 300.0 and {p["id"]: p["value"] for p in arpu["pair"]} == {"gross_revenue": 900.0, "uv": 3}
    arppu = run("arppu", UA, events)
    assert arppu["value"] == pytest.approx(450) and arppu["spread"]["n"] == 2 and {p["id"]: p["value"] for p in arppu["pair"]} == {"gross_revenue": 900.0, "pu": 2}
    assert value("arpdau", s, events) == pytest.approx(900 / 5)
    # 금액 행에 의존하는 짝: ARPU = PUR × ARPPU (결제자 비율 × 결제자당 매출)
    assert value("arpu", UA, events) == pytest.approx(value("pur", {**s, "단위": "month"}, events) * value("arppu", UA, events))


def test_ltv_repeat_tiers_recurring(events):
    s = {**U, "금액": "amt"}
    ltv = run("ltv", {**s, "N일": 1}, events)
    assert ltv["value"] == pytest.approx(600 / 3) and all(r["complete"] for r in ltv["rows"])  # 3/1 코호트(사용자1·2)는 하루 안 600, 3/2 코호트(사용자3)는 0
    assert [(r["cohort"], r["size"], r["revenue"]) for r in ltv["rows"]] == [("2026-03-01", 2, 600.0), ("2026-03-02", 1, 0.0)]
    late = run("ltv", {**s, "N일": 30}, events)
    assert late["value"] == pytest.approx(300) and any("지금까지의 값" in n for n in late["notes"])
    simple = mem(uid=[1, 1, 2, 2], ts=[D(2026, 1, 5), D(2026, 2, 5), D(2026, 1, 6), D(2026, 1, 7)], amt=[10.0, 10.0, 10.0, 10.0])
    ratio = run("ltv", {**s, "N일": 30, "계산 방식": "ARPU÷이탈률"}, simple)
    # 월 ARPU = 40 ÷ 사용자·월 3개, 월 이탈률(1월 활동 {1,2}, 2월 활동 {1}) = 1/2
    assert ratio["value"] == pytest.approx((40 / 3) / 0.5) and ratio["parts"]["churn_rate"] == 0.5
    rep = run("repeat_rate", UA, events)
    assert rep["value"] == 0.5 and {p["id"]: p["value"] for p in rep["pair"]} == {"pu": 2, "repeat_buyers": 1}  # 금액이 0보다 큰 행: 사용자1 둘, 사용자2 하나
    tiers = run("tiers", {**UA, "기준 방식": "금액", "Whale 기준": 500, "Dolphin 기준": 300}, events)
    assert [(r["tier"], r["users"]) for r in tiers["rows"]] == [("Whale", 1), ("Dolphin", 1), ("Minnow", 0)] and tiers["value"] == pytest.approx(600 / 900)
    by_rank = run("tiers", {**UA, "Whale 기준": 50, "Dolphin 기준": 100}, events)  # 분위수: 상위 50% = 결제자 둘 중 큰 쪽
    assert by_rank["rows"][0]["users"] == 1 and by_rank["rows"][0]["total_share"] == pytest.approx(600 / 900)
    with pytest.raises(ValueError, match="Whale 기준은 Dolphin 기준 이상"):
        run("tiers", {**UA, "기준 방식": "금액", "Whale 기준": 100, "Dolphin 기준": 300}, events)
    mrr = run("mrr", {"시각": "ts", "금액": "amt"}, events)
    assert mrr["value"] == 900 and value("arr", {"시각": "ts", "금액": "amt"}, events) == 10800 and parts("arr", {"시각": "ts", "금액": "amt"}, events) == {"mrr": 900.0}


def test_nrr():
    t = mem(uid=[1, 2, 1, 2, 3], ts=[D(2026, 1, 5), D(2026, 1, 6), D(2026, 2, 5), D(2026, 2, 6), D(2026, 2, 7)], amt=[100.0, 100.0, 150.0, 0.0, 50.0])
    got = run("nrr", {**U, "금액": "amt"}, t)
    assert got["value"] == pytest.approx(150 / 200) and {p["id"]: p["value"] for p in got["pair"]} == {"prev_revenue": 200.0, "kept_revenue": 150.0}  # 지난달 매출 있던 1·2의 이번 달 150 ÷ 200


# ---------- 5 마케팅·UA ----------

AD = dict(spend=[100.0, 200.0], impressions=[1000.0, 3000.0], clicks=[50.0, 70.0], installs=[10.0, 20.0], conversions=[4.0, 6.0])


def test_ad_ratios():
    t = mem(**AD)
    cases = {"ctr": (120 / 4000, {"클릭 수": "clicks", "노출 수": "impressions"}), "ipm": (30 / 4000 * 1000, {"설치 수": "installs", "노출 수": "impressions"}),
             "cpm": (300 / 4000 * 1000, {"광고비": "spend", "노출 수": "impressions"}), "cpc": (300 / 120, {"광고비": "spend", "클릭 수": "clicks"}),
             "cpi": (300 / 30, {"광고비": "spend", "설치 수": "installs"}), "cpa": (300 / 10, {"광고비": "spend", "전환 수": "conversions"})}
    for i, (want, slots) in cases.items():
        got = run(i, slots, t)
        assert got["value"] == pytest.approx(want), i
        assert all(p["value"] is not None for p in got["pair"]), i  # 짝 절대량(분자·분모)을 함께 준다
    assert parts("cpc", cases["cpc"][1], t) == {"spend": 300.0, "clicks": 120.0}
    assert value("cpc", {"광고비": "spend", "클릭 수": "clicks"}, mem(spend=[1.0], clicks=[0.0])) is None  # 0으로 나누기


def test_reach_frequency_kfactor_organic():
    logs = mem(uid=[1, 1, 1, 2])
    assert value("impressions", {}, logs) == 4 and value("reach", {"사용자": "uid"}, logs) == 2
    freq = run("frequency", {"사용자": "uid"}, logs)
    assert freq["value"] == 2.0 and freq["spread"]["median"] == 2.0 and {p["id"]: p["value"] for p in freq["pair"]} == {"impressions": 4.0, "reach": 2}
    assert value("impressions", {"노출 수": "n"}, mem(n=[5.0, 7.0])) == 12
    k = mem(uid=[1, 2, 3], inv=[10.0, 0.0, 5.0], got=[2.0, 0.0, 3.0])
    kf = run("k_factor", {"사용자": "uid", "보낸 초대 수": "inv", "초대로 가입한 수": "got"}, k)
    assert kf["value"] == pytest.approx((15 / 3) * (5 / 15)) and {p["id"]: p["value"] for p in kf["pair"]} == {"invites": 15.0, "joined": 5.0}
    src = mem(uid=[1, 2, 3, 4, 5, 1], ts=[D(2026, 3, i) for i in (1, 1, 1, 1, 1)] + [D(2026, 3, 9)], src=["ad", "org", "ad", "org", "org", "org"])
    up = run("organic_uplift", {"사용자": "uid", "시각": "ts", "유입 경로": "src", "유료 값": "ad"}, src)  # 사용자별 처음 행의 유입: 유료 2명, 오가닉 3명
    assert up["value"] == 1.5 and {p["id"]: p["value"] for p in up["pair"]} == {"paid_installs": 2, "organic_installs": 3}


@pytest.fixture
def ads():
    return {"ads": mem(day=[D(2026, 3, 1), D(2026, 3, 2)], cost=[120.0, 30.0], channel=["A", "B"])}


CROSS = {"광고 테이블": "ads", "광고비": "cost", "광고 날짜": "day"}
BY_DAY = {"kind": "period", "unit": "day"}


def test_cross_table_metrics_need_alignment(events, ads):
    s = {**U, **CROSS}
    with pytest.raises(ValueError, match="두 테이블을 맞추는 방법을 골라야 해요"):
        run("cac", s, events, other=ads)
    with pytest.raises(ValueError, match="기간 단위로 맞춰"):
        run("roas", {**s, "금액": "amt"}, events, other=ads, align={})
    with pytest.raises(ValueError, match="day·week·month"):
        run("cac", s, events, other=ads, align={"kind": "period", "unit": "year"})
    with pytest.raises(ValueError, match="광고 날짜"):
        run("cac", {k: v for k, v in s.items() if k != "광고 날짜"}, events, other=ads, align=BY_DAY)
    with pytest.raises(ValueError, match="다른 테이블을 읽을 수 없는"):
        run("cac", s, events, align=BY_DAY)


def test_cross_table_values_by_period(events, ads):
    s = {**U, **CROSS}
    cac = run("cac", s, events, other=ads, align=BY_DAY)  # 신규: 3/1 {1, 2}, 3/2 {3} → 광고비 120, 30
    assert [(r["key"], r["new_users"], r["cac"]) for r in cac["rows"]] == [("2026-03-01", 2, 60.0), ("2026-03-02", 1, 30.0)] and cac["value"] == pytest.approx(150 / 3)
    assert {p["id"]: p["value"] for p in cac["pair"]} == {"spend": 150.0, "new_users": 3}
    roas = run("roas", {**s, "금액": "amt", "N일": 1}, events, other=ads, align=BY_DAY)  # 첫 활동 후 1일 안 매출: 3/1 코호트 600, 3/2 코호트 0
    assert roas["value"] == pytest.approx(600 / 150) and [r["roas"] for r in roas["rows"]] == [pytest.approx(600 / 120), 0.0]
    lc = run("ltv_cac", {**s, "금액": "amt", "N일": 1}, events, other=ads, align=BY_DAY)
    assert lc["value"] == pytest.approx((600 / 3) / (150 / 3)) and {p["id"]: p["value"] for p in lc["pair"]} == {"ltv": 200.0, "cac": 50.0}
    pay = run("payback", {**s, "금액": "amt", "최대 일수": 3}, events, other=ads, align=BY_DAY)
    # 누적 ARPU(신규 3명): 0일 100/3, 1일 600/3 = 200 ≥ CAC 50 → 첫 회수 1일
    assert pay["value"] == 1 and pay["rows"][0]["cum_arpu"] == pytest.approx(100 / 3) and pay["rows"][1]["cum_arpu"] == pytest.approx(200)
    never = run("payback", {**s, "금액": "amt", "최대 일수": 1}, events, other={"ads": mem(day=[D(2026, 3, 1)], cost=[1e9])}, align=BY_DAY)
    assert never["value"] is None and any("회수하지 못했어요" in n for n in never["notes"])


def test_cross_table_values_by_common_key(ads):
    users = mem(uid=[1, 2, 3, 1], ts=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 1), D(2026, 3, 1)], ch=["A", "B", "A", "A"], amt=[10.0, 5.0, 20.0, 5.0])  # 광고 날짜가 있으니 광고비는 사용자 쪽 날짜 범위(3/1~3/2)로 거른다
    key = {"kind": "key", "keys": [["ch", "channel"]]}
    s = {**U, **CROSS}
    cac = run("cac", s, users, other=ads, align=key)  # 채널 A: 광고비 120 ÷ 사용자 {1, 3} = 60, 채널 B: 30 ÷ {2} = 30
    assert {r["key"]: r["cac"] for r in cac["rows"]} == {"A": 60.0, "B": 30.0} and cac["value"] == pytest.approx(150 / 3)
    paid = run("cac_paid", {**s, "유입 경로": "ch", "유료 값": "A"}, users, other=ads, align=key)
    assert paid["value"] == pytest.approx(120 / 2)  # 유료(채널 A) 신규는 사용자 1·3, 사용자가 없는 채널 B의 광고비는 세지 않는다
    roas = run("roas", {**s, "금액": "amt", "N일": 7}, users, other=ads, align=key)
    assert {r["key"]: r["roas"] for r in roas["rows"]} == {"A": pytest.approx(35 / 120), "B": pytest.approx(5 / 30)}
    with pytest.raises(ValueError, match="맞춤 구절은"):
        run("cac", s, users, other=ads, align={"kind": "key", "keys": []})


# ---------- 6 게임 경제 ----------

@pytest.fixture
def economy():
    return mem(uid=[1, 1, 2, 2], ts=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 1), D(2026, 3, 2)], delta=[100.0, -30.0, 50.0, -20.0], bal=[100.0, 70.0, 50.0, 30.0])


def test_economy_values(economy):
    d = {"재화 변화량": "delta"}
    assert value("faucet", d, economy) == 150 and value("sink", d, economy) == 50 and value("net_flow", d, economy) == 100
    assert value("sink_faucet", d, economy) == pytest.approx(1 / 3) and parts("sink_faucet", d, economy) == {"sink": 50.0, "faucet": 150.0}
    supply = run("money_supply", {**U, "잔액": "bal"}, economy)
    assert supply["value"] == 100 and supply["parts"]["active_users"] == 2  # 마지막 잔액 70 + 30
    assert value("velocity", {**U, **d, "잔액": "bal"}, economy) == pytest.approx(0.5)
    old = mem(uid=[1, 2], ts=[D(2026, 3, 30), D(2026, 1, 1)], bal=[10.0, 99.0])
    assert value("money_supply", {**U, "잔액": "bal", "활성 기준(일)": 30}, old) == 10  # 사용자2는 자료의 끝보다 30일 넘게 앞서 활성이 아니다
    assert value("gini", {"값": "delta", "사용자": "uid"}, economy) == pytest.approx(0.2)  # 사용자별 합계 70·30
    assert value("gini", {"값": "bal", "사용자": "uid", "시각": "ts", "기준": "사용자별 마지막 값"}, economy) == pytest.approx(0.2)  # 마지막 잔액 70·30: (2×(1×30 + 2×70)) ÷ (2×100) − 3/2
    assert value("gini", {"값": "bal"}, mem(bal=[1.0, 1.0, 1.0, 1.0])) == 0.0
    assert value("gini", {"값": "bal"}, mem(bal=[0.0, 0.0, 0.0, 1.0])) == pytest.approx(0.75)
    neg = run("gini", {"값": "bal"}, mem(bal=[-1.0, 2.0]))
    assert neg["value"] is None and any("음수" in n for n in neg["notes"])


def test_stage_meta_and_pity():
    stage = mem(uid=[1, 1, 2, 1, 2], st=["s1", "s1", "s1", "s2", "s2"], res=["fail", "win", "win", "fail", "fail"])
    s = {"스테이지": "st", "결과": "res", "성공 값": "win", "사용자": "uid"}
    clear = run("stage_clear", s, stage)
    assert clear["value"] == pytest.approx(2 / 5) and [(r["stage"], r["clear_rate"]) for r in clear["rows"]] == [("s1", pytest.approx(2 / 3)), ("s2", 0.0)]
    anon = run("stage_clear", {k: v for k, v in s.items() if k != "사용자"}, stage)  # 사용자 컬럼이 없어도 클리어율은 구한다
    assert anon["value"] == pytest.approx(2 / 5)
    tries = run("stage_tries", s, stage)
    assert tries["value"] == pytest.approx(5 / 4) and tries["spread"]["n"] == 4  # (사용자, 스테이지) 4쌍에 도전 5번
    meta = mem(item=["A", "A", "A", "B"], res=["win", "lose", "win", "lose"])
    m = {"항목": "item", "결과": "res", "성공 값": "win"}
    pick, win = run("pick_rate", m, meta), run("win_rate", m, meta)
    assert pick["value"] == 0.75 and win["value"] == 0.5
    assert {r["item"]: (r["pick_rate"], r["win_rate"]) for r in pick["rows"]} == {"A": (0.75, pytest.approx(2 / 3)), "B": (0.25, 0.0)}
    pulls = mem(uid=[1] * 5 + [2] * 2 + [3] * 6)
    pity = run("pity_reach", {"사용자": "uid", "천장 횟수": 5}, pulls)
    assert pity["value"] == pytest.approx(2 / 3) and pity["spread"]["median"] == 5.0 and {p["id"]: p["value"] for p in pity["pair"]} == {"pulled_users": 3, "reached_users": 2}


# ---------- 7 이커머스 ----------

@pytest.fixture
def orders():
    return mem(uid=[1, 1, 2, 3], oid=["o1", "o4", "o2", "o3"], amt=[100.0, 50.0, 200.0, 300.0], status=["ok", "ok", "cancel", "ok"], fee=[10.0, 5.0, 20.0, 30.0])


def test_commerce_values(orders):
    assert value("gmv", {"금액": "amt"}, orders) == 650 and value("gmv", {"금액": "amt", "상태": "status", "제외할 값들": ["cancel"]}, orders) == 450
    assert run("take_rate", {"금액": "amt", "수수료 수익": "fee"}, orders)["value"] == pytest.approx(0.1)
    aov = run("aov", {"금액": "amt", "주문": "oid"}, orders)
    assert aov["value"] == pytest.approx(650 / 4) and aov["spread"]["median"] == 150.0 and {p["id"]: p["value"] for p in aov["pair"]} == {"gmv": 650.0, "orders": 4}
    freq = run("purchase_frequency", {"사용자": "uid", "주문": "oid"}, orders)
    assert freq["value"] == pytest.approx(4 / 3) and freq["spread"]["n"] == 3
    items = mem(sku=list("abcde"), amt=[500.0, 100.0, 50.0, 30.0, 20.0])
    assert value("sku_pareto", {"항목": "sku", "금액": "amt"}, items) == pytest.approx(500 / 700)
    assert value("sku_pareto", {"항목": "sku", "금액": "amt", "상위 %": 40}, items) == pytest.approx(600 / 700)
    zero = mem(n=[5, 0, 0, 3])
    assert run("zero_result", {"검색 결과 수": "n"}, zero)["value"] == 0.5
    t = mem(uid=[1, 1, 2, 2, 3], ts=[D(2026, 3, 1, 9), D(2026, 3, 1, 9, 1), D(2026, 3, 1, 9), D(2026, 3, 1, 9, 1), D(2026, 3, 1, 9)], ev=["home", "search", "search", "click", "search"])
    ex = run("search_exit", {**U, "이벤트": "ev", "검색 이벤트 값": "search"}, t)
    assert ex["value"] == pytest.approx(2 / 3) and {p["id"]: p["value"] for p in ex["pair"]} == {"search_sessions": 3, "exit_sessions": 2}


# ---------- 8 실험·모델 ----------

def test_classification_and_f1_zero_case():
    t = mem(y=[1, 1, 1, 0, 0, 0], p=[1, 1, 0, 1, 0, 0])
    s = {"정답": "y", "예측": "p"}
    for i in ("precision", "recall", "f1"):
        assert value(i, s, t) == pytest.approx(2 / 3)
    assert parts("precision", s, t) == {"tp": 2, "fp": 1}
    zero = mem(y=[1, 1, 0, 0], p=[0, 0, 1, 1])
    assert value("f1", s, zero) == 0.0 and value("precision", s, zero) == 0.0  # 정밀도·재현율이 모두 0이면 F1은 0(없음이 아니다)
    scored = mem(y=[0, 0, 1, 1], sc=[0.1, 0.4, 0.35, 0.8])
    assert value("pr_auc", {"정답": "y", "점수": "sc"}, scored) == pytest.approx(5 / 6)
    assert run("precision", {"정답": "y", "점수": "sc", "임계값": 0.35}, scored)["rows"][0] == {"name": "TP", "value": 2}
    with pytest.raises(ValueError, match="‘예측’ 또는 ‘점수’ 칸 중 하나"):
        run("recall", {"정답": "y"}, scored)
    with pytest.raises(ValueError, match="0과 1"):
        run("recall", {"정답": "sc", "예측": "y"}, scored)
    bools = mem(y=[True, False, True], p=[True, True, False])
    assert value("recall", {"정답": "y", "예측": "p"}, bools) == 0.5


def test_regression_and_ranking():
    t = mem(y=[100.0, 200.0, 0.0], p=[110.0, 190.0, 5.0])
    s = {"정답": "y", "예측": "p"}
    assert value("rmse", s, t) == pytest.approx(math.sqrt(75)) and value("mae", s, t) == pytest.approx(25 / 3)
    mape = run("mape", s, t)
    assert mape["value"] == pytest.approx(0.075) and any("정답이 0인 1행" in n for n in mape["notes"])
    ranked = mem(q=["a", "a", "a", "b", "b"], r=[1, 2, 3, 1, 2], rel=[3, 2, 0, 0, 1])
    s = {"질의": "q", "순위": "r", "관련도": "rel", "K": 2}
    assert value("ndcg", s, ranked) == pytest.approx((1.0 + (1 / math.log2(3)) / 1.0) / 2)
    assert value("map", s, ranked) == pytest.approx((1.0 + 0.5) / 2)
    assert parts("ndcg", s, ranked) == {"queries": 2}
    none = mem(q=["a"], r=[1], rel=[0])
    got = run("ndcg", s, none)
    assert got["value"] is None and any("관련 항목이 하나도 없는" in n for n in got["notes"])


def test_experiment_statistics():
    conv = [0] * 90 + [1] * 10 + [0] * 85 + [1] * 15  # 대조군 100명 중 10, 실험군 100명 중 15
    t = mem(g=["c"] * 100 + ["t"] * 100, conv=conv)
    s = {"실험군": "g", "지표 값": "conv", "대조군 값": "c", "실험군 값": "t"}
    lift = run("lift", s, t)
    assert lift["value"] == pytest.approx(0.5) and {p["id"]: p["value"] for p in lift["pair"]} == {"control_mean": pytest.approx(0.1), "treat_mean": pytest.approx(0.15)}
    z = 0.05 / math.sqrt(0.125 * 0.875 * 0.02)
    p = run("pvalue", s, t)
    assert p["value"] == pytest.approx(math.erfc(z / math.sqrt(2))) and p["value"] == pytest.approx(0.285, abs=1e-3) and "두 비율 z 검정" in p["label"]
    power = value("power", s, t)
    assert power == pytest.approx(mc.power_two_proportions(0.1, 0.15, 100, 100, 0.05)) and 0 < power < 1
    mde = run("mde", s, t)
    assert mde["value"] == pytest.approx(mc.mde_two_proportions(0.1, 100, 100, 0.05, 0.8)) and mde["rows"][2]["value"] == pytest.approx(mde["value"] / 0.1)
    cont = mem(g=["c"] * 5 + ["t"] * 5, v=[1.0, 2.0, 3.0, 4.0, 5.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    s2 = {"실험군": "g", "지표 값": "v", "대조군 값": "c", "실험군 값": "t"}
    welch = run("pvalue", s2, cont)
    assert welch["value"] == pytest.approx(0.3466, abs=1e-3) and "Welch" in welch["label"]  # t = 1.0, 자유도 8의 양측 p
    assert value("lift", s2, cont) == pytest.approx(1 / 3)
    assert value("mde", s2, cont) == pytest.approx(mc.mde_two_means(math.sqrt(2.5), 5, 5, 0.05, 0.8))
    with pytest.raises(ValueError, match="두 군 이름을 확인"):
        run("lift", {**s, "대조군 값": "x"}, t)


def test_guardrail_and_limits():
    t = mem(g=["c"] * 100 + ["t"] * 100, conv=[0] * 90 + [1] * 10 + [0] * 95 + [1] * 5)  # 대조군 10% → 실험군 5%: Lift −50%
    s = {"실험군": "g", "지표 값": "conv", "대조군 값": "c", "실험군 값": "t", "한계(%)": -20}
    bad = run("guardrail", s, t)
    assert bad["value"] == pytest.approx(-0.5) and bad["notes"] and "벗어났어요" in bad["label"] and bad["rows"][2]["value"] == "경고"
    ok = run("guardrail", {**s, "한계(%)": -60}, t)
    assert not ok["notes"] and "지키고 있어요" in ok["label"] and ok["rows"][2]["value"] == "정상"
    below = run("guardrail", {**s, "한계(%)": -60, "방향": "<="}, t)
    assert "벗어났어요" in below["label"]
    # 대시보드 카운터 한계와 같은 뜻: >=이면 값이 한계보다 작아질 때, <=이면 커질 때 경고
    assert cat.check_limit(0.5, {"op": ">=", "value": 0.8}).startswith("지금 값 0.5") and cat.check_limit(0.9, {"op": ">=", "value": 0.8}) is None
    assert cat.check_limit(0.9, {"op": "<=", "value": 0.8}) and cat.check_limit(None, {"op": "<=", "value": 0.8}) is None
    daily = mem(uid=[1, 2, 3], ts=[D(2026, 3, 1)] * 3)
    got = run("uv", {"사용자": "uid"}, daily, limit={"op": ">=", "value": 5})
    assert got["alert"] and run("uv", {"사용자": "uid"}, daily, limit={"op": ">=", "value": 2})["alert"] is None
    from core import dash_store
    assert set(dash_store.LIMIT_OPS) == {">=", "<="}


def test_every_ratio_entry_returns_its_pair_values(events, daily):
    """짝 절대량(pair)을 가진 항목은 계산 결과의 parts에 그 값이 들어 있다(자료를 지어낸 항목만)."""
    cases = {"pv_per_uv": ({"사용자": "uid"}, events), "stickiness": (U, events), "session_count": (U, events), "asd": (U, events), "tsd": (U, events), "cvr": ({**U, "이벤트": "ev", "단계 A": "login", "단계 B": "buy"}, events),
             "bounce": (U, events), "exit_rate": ({**U, "페이지": "ev"}, events), "ftue": ({**U, "이벤트": "ev", "단계들": ["login", "buy"]}, events), "retention_classic": ({**U, "N": 1}, daily),
             "churn": ({**U, "단위": "day"}, daily), "arpu": (UA, events), "arppu": (UA, events), "arpdau": ({**U, "금액": "amt"}, events), "pur": ({**U, "금액": "amt"}, events),
             "ltv": ({**U, "금액": "amt", "N일": 1}, events), "repeat_rate": (UA, events), "net_revenue": ({"금액": "amt"}, events)}
    for i, (slots, table) in cases.items():
        got = run(i, slots, table)
        assert all(p["value"] is not None for p in got["pair"]), (i, got["pair"])
        if got["value"] is not None and len(got["pair"]) == 2 and i in {"pv_per_uv", "arpu", "arppu", "session_count", "asd", "tsd", "arpdau", "repeat_rate"}:
            a, b = got["pair"][0]["value"], got["pair"][1]["value"]
            assert got["value"] == pytest.approx(a / b if i != "repeat_rate" else b / a), i  # 비율 = 분자 ÷ 분모


def test_survival_is_fast_on_many_users():
    import time
    rng = np.random.default_rng(3)
    n = 30_000
    first = [D(2026, 1, 1)] * n
    last = [D(2026, 1, 1) + dt.timedelta(days=int(d)) for d in rng.integers(0, 300, n)]
    t = mem(uid=list(range(n)) * 2, ts=first + last)
    start = time.perf_counter()
    got = run("survival", {**U, "미접속 기준(일)": 7}, t)
    assert time.perf_counter() - start < 10 and got["rows"] and got["value"] is not None


# ---------- 라우트 ----------

@pytest.fixture
def c(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query import flow_app  # noqa: F401
    from metrics import catalog_app  # noqa: F401
    from query.search_app import app
    path = tmp_path / "r.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table events (uid int, ts timestamp, ev varchar, amt double)")
    for r in ROWS:
        raw.execute("insert into events values (?, ?, ?, ?)", list(r))
    raw.execute("create table ads (day timestamp, cost double)")
    raw.execute("insert into ads values ('2026-03-01', 120), ('2026-03-02', 30)")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def post(c, path, **body):
    resp = c.post(path, json=body)
    return resp.status_code, resp.get_json()


def reads(sent, table="events"):
    """원격(여기서는 events 테이블)을 읽은 조회들(같은 글은 한 번으로: 표 실행과 raw_sql이 같은 글을 두 번 남긴다)."""
    return sorted({re.sub(r"\s+", " ", q) for q in scans(sent) if f'FROM "{table}"' in q})


def scans(sent):
    """값을 읽는 조회만(메타데이터·계획 조회는 뺀다)."""
    return [q for q in sent if not q.lstrip().upper().startswith(("EXPLAIN", "DESCRIBE", "PRAGMA", "SHOW")) and "duckdb_tables" not in q and "information_schema" not in q.lower()
            and "SELECT CURRENT_" not in q.upper()]


BODY = {"base": "events", "conditions": ["amt >= 0"]}


def test_catalog_routes_are_free_and_list_availability(c):
    assert {"/api/catalog", "/api/catalog/available", "/api/catalog/save"} <= server.FREE and "/api/catalog/compute" not in server.FREE
    listing = c.get("/api/catalog").get_json()
    assert len(listing["entries"]) == len(cat.ENTRIES) and "availability" not in listing and listing["areas"][0]["name"] == "트래픽·활성도"
    import json
    cols = json.dumps({"uid": "num", "ts": "date", "ev": "text", "amt": "num"})
    got = c.get("/api/catalog?columns=" + cols).get_json()["availability"]
    assert got["arpu"]["usable"] and not got["cpc"]["usable"] and got["cpc"]["reason"] == "광고비·클릭 수 컬럼이 있는 테이블이 필요해요."
    code, body = post(c, "/api/catalog/available", columns={"uid": "num"}, tables={"ads": {"cost": "num"}})
    assert code == 200 and body["availability"]["cac"]["reason"].startswith("시각") and post(c, "/api/catalog/available")[0] == 400


def test_compute_route_on_duckdb_file(c):
    code, got = post(c, "/api/catalog/compute", id="arpu", slots={"사용자": "uid", "금액": "amt"}, body=BODY)
    assert code == 200 and got["value"] == pytest.approx(300) and got["spread"]["median"] == 300.0 and got["name"] == "사용자당 매출"
    code, got = post(c, "/api/catalog/compute", id="cvr", slots={**U, "이벤트": "ev", "단계 A": "login", "단계 B": "buy"}, body={"base": "events", "conditions": ["amt >= 0"]})
    assert code == 200 and got["value"] == pytest.approx(2 / 3)
    code, got = post(c, "/api/catalog/compute", id="cac", slots={**U, "광고 테이블": "ads", "광고비": "cost", "광고 날짜": "day"}, body=BODY, align=BY_DAY)
    assert code == 200 and got["value"] == pytest.approx(50)
    code, got = post(c, "/api/catalog/compute", id="cac", slots={**U, "광고 테이블": "ads", "광고비": "cost", "광고 날짜": "day"}, body=BODY)
    assert code == 400 and "두 테이블을 맞추는 방법" in got["error"]
    assert post(c, "/api/catalog/compute", id="uv", slots={"사용자": "uid"}, body={})[0] == 400
    assert post(c, "/api/catalog/compute", id="nothing", slots={}, body=BODY)[0] == 400
    code, got = post(c, "/api/catalog/compute", id="uv", slots={"사용자": "nope"}, body=BODY)
    assert code == 400 and "nope" in got["error"]
    code, got = post(c, "/api/catalog/compute", id="uv", slots={"사용자": "uid"}, body=BODY, limit={"op": ">=", "value": 9})
    assert code == 200 and got["alert"]


def test_compute_on_remote_copies_needed_columns_without_counting(c, monkeypatch):
    monkeypatch.setattr(held, "remote", lambda con: True)
    sent = spy_queries(monkeypatch)
    code, got = post(c, "/api/catalog/compute", id="uv", slots={"사용자": "uid"}, body=BODY)  # prep_where 기본(all): 필요한 컬럼만 이 PC 사본으로 받아 계산
    assert code == 200 and got["value"] == 3
    assert reads(sent) == ['SELECT "t0"."uid" FROM "events" AS "t0" WHERE "t0"."amt" >= 0.0']  # 필요한 컬럼(uid)과 조건뿐, 행 수 세기(COUNT)는 없다
    again = len(sent)
    assert post(c, "/api/catalog/compute", id="uv", slots={"사용자": "uid"}, body=BODY)[0] == 200  # 같은 사본을 다시 쓴다: DB(events)를 다시 읽지 않는다
    assert reads(sent[again:]) == []


def test_compute_on_remote_with_prep_where_new_sends_only_the_metric_query(c, monkeypatch):
    from core import settings
    settings.write(settings.PATH.name, {**(settings.read(settings.PATH.name) or {}), "prep_where": "new"})
    monkeypatch.setattr(held, "remote", lambda con: True)
    sent = spy_queries(monkeypatch)
    code, got = post(c, "/api/catalog/compute", id="uv", slots={"사용자": "uid"}, body=BODY)
    assert code == 200 and got["value"] == 3
    found = reads(sent)
    assert len(found) == 1 and found[0].count("COUNT(") == 1 and "COUNT(*)" not in found[0] and "DISTINCT" in found[0]  # 집계 조회 한 번 — 행 수를 미리 세지 않는다
    sent.clear()
    code, got = post(c, "/api/catalog/compute", id="asd", slots=U, body=BODY)  # 창 함수를 쓰는 항목은 그래도 이 PC 사본에서
    assert code == 200 and got["value"] == pytest.approx(100.0)
    assert reads(sent) == ['SELECT "t0"."uid", "t0"."ts" FROM "events" AS "t0" WHERE "t0"."amt" >= 0.0']


def test_remote_asks_before_reading_when_over_the_limit(c, monkeypatch):
    from core import scan_size
    monkeypatch.setattr(held, "remote", lambda con: True)
    monkeypatch.setattr(scan_size, "limit_check", lambda *a, **k: {"over": True, "unknown": False, "blocked": False, "bytes": 10**12, "limit": 1, "text": "읽는 양 약 1 TB"})
    sent = spy_queries(monkeypatch)
    resp = c.post("/api/catalog/compute", json={"id": "uv", "slots": {"사용자": "uid"}, "body": BODY})
    assert resp.headers.get("X-Needs-Confirm") == "1" and resp.get_json()["needs_confirm"]
    assert reads(sent) == []  # 확인 전에는 값을 읽지 않는다
    assert c.post("/api/catalog/compute", json={"id": "uv", "slots": {"사용자": "uid"}, "body": BODY, "confirmed": True}).get_json()["value"] == 3


def test_remote_direct_compute_confirm_tells_how_many_times_the_table_is_read(c, monkeypatch):
    from core import scan_size, settings
    settings.write(settings.PATH.name, {**(settings.read(settings.PATH.name) or {}), "prep_where": "new"})  # 사본 없이 원격에서 바로 구한다
    monkeypatch.setattr(held, "remote", lambda con: True)
    monkeypatch.setattr(scan_size, "limit_check", lambda *a, **k: {"over": True, "unknown": False, "blocked": False, "bytes": 10**12, "limit": 1, "text": "읽는 양 약 1 TB"})
    resp = c.post("/api/catalog/compute", json={"id": "pv_per_uv", "slots": {"사용자": "uid"}, "body": BODY})  # 조회 합·사용자별 분포를 따로 읽는다
    got = resp.get_json()
    assert resp.headers.get("X-Needs-Confirm") == "1" and got["needs_confirm"] and got["text"].startswith("읽는 양 약 1 TB") and "표를 약 2번 읽어요" in got["text"]


def test_file_connection_computes_on_a_local_duckdb_copy(folder, tmp_path):
    """Polars(파일) 연결: 창 함수가 필요한 세션 지표도 이 PC의 DuckDB 사본에서 구한다."""
    from metrics import catalog_app  # noqa: F401
    from query.search_app import app
    pl.DataFrame({"uid": [r[0] for r in ROWS], "ts": [r[1] for r in ROWS], "ev": [r[2] for r in ROWS], "amt": [float(r[3]) for r in ROWS]}).write_parquet(tmp_path / "events.parquet")
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    try:
        assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "files": [str(tmp_path / "events.parquet")]}).status_code == 200
        assert server.state["con"].name == "polars"
        code, got = post(client, "/api/catalog/compute", id="asd", slots=U, body={"base": "events"})
        assert code == 200 and got["value"] == pytest.approx(100.0)
        code, got = post(client, "/api/catalog/compute", id="arpu", slots={"사용자": "uid", "금액": "amt"}, body={"base": "events"})
        assert code == 200 and got["value"] == pytest.approx(300)
        pl.DataFrame({"day": [D(2026, 3, 1), D(2026, 3, 2)], "cost": [120.0, 30.0]}).write_parquet(tmp_path / "ads.parquet")
        assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "files": [str(tmp_path / "events.parquet"), str(tmp_path / "ads.parquet")]}).status_code == 200
        code, got = post(client, "/api/catalog/compute", id="cac", slots={**U, **CROSS}, body={"base": "events"}, align=BY_DAY)  # 두 테이블 지표도 두 사본으로
        assert code == 200 and got["value"] == pytest.approx(50)
    finally:
        client.post("/api/disconnect", json={})


def test_save_as_my_metric_and_run_it_in_a_sentence(c):
    from metrics import my_metrics
    from query import sentence_exec
    cols = {"uid": "num", "ts": "date", "ev": "text", "amt": "num"}
    code, made = post(c, "/api/catalog/save", id="arpu", slots={"사용자": "uid", "금액": "amt"}, scope={"table": "events"}, columns=cols)
    assert code == 200 and made["metric"]["template"] == "catalog:arpu" and made["metric"]["unit"] == "원" and made["metric"]["better"] == "up" and made["metric"]["scope"]["table"] == "events"
    assert made["metric"]["text"].startswith("사용자당 매출 · events") and made["metric"]["cols"] == {"amt": "num", "uid": "num"}
    again = post(c, "/api/catalog/save", id="arpu", slots={"사용자": "uid", "금액": "amt"}, scope={"table": "events"}, columns=cols)
    assert again[0] == 400 and "이미 있어요" in again[1]["error"]
    other = post(c, "/api/catalog/save", id="arpu", name="ARPU 2", slots={"사용자": "uid", "금액": "amt"}, scope={"table": "events"}, columns=cols)[1]["metric"]
    assert other["id"] != made["metric"]["id"]
    assert post(c, "/api/catalog/save", id="arpu", slots={"사용자": "uid", "금액": "nope"}, scope={"table": "events"}, columns=cols, name="x")[0] == 400
    assert post(c, "/api/catalog/save", id="cac", slots={**U, "광고 테이블": "ads", "광고비": "cost"}, scope={"table": "events"}, columns=cols)[1]["error"].startswith("다른 테이블의 값을 쓰는 지표는")
    saved = post(c, "/api/catalog/save", id="cac", slots={**U, "광고 테이블": "ads", "광고비": "cost", "광고 날짜": "day"}, scope={"table": "events"}, columns=cols, align=BY_DAY)[1]["metric"]
    assert saved["align"] == BY_DAY
    listed = c.get("/api/my_metrics").get_json()["metrics"]
    assert {m["id"] for m in listed} >= {made["metric"]["id"], saved["id"]}
    # 문장 안에서 쓴다: 내 지표 id의 값을 구한다(scope가 같은 테이블이어야 한다)
    mine = my_metrics.get(made["metric"]["id"])
    assert sentence_exec.metric_run({"ref": mine["id"], "body": BODY}, 0)["rows"] == [{"사용자당 매출": pytest.approx(300)}]
    assert sentence_exec.metric_run({"ref": saved["id"], "body": BODY}, 0)["rows"] == [{"고객 획득 비용(전체)": pytest.approx(50)}]
    with pytest.raises(ValueError, match="다른 테이블"):
        sentence_exec.metric_run({"ref": mine["id"], "body": {"base": "ads"}}, 0)
    with pytest.raises(ValueError, match="값마다 나눠"):
        sentence_exec.metric_run({"ref": mine["id"], "body": BODY, "by": "ev"}, 0)
    # 지표 틀 ‘A÷B’의 칸에도 들어간다
    code, ok = post(c, "/api/my_metrics/validate", definition={"name": "비율", "scope": {"table": "events"}, "template": "m05", "slots": {"지표 A": mine["id"], "지표 B": other["id"]}}, columns=cols)
    assert code == 200 and ok["ok"] and ok["deps"] == [mine["id"], other["id"]]
    # 저장한 내 지표를 지금 테이블의 구조가 바뀌면 쓸 수 없음으로 알려 준다
    status = post(c, "/api/my_metrics/status", scope={"table": "events"}, columns={"uid": "num"})[1]["status"]
    assert status[mine["id"]]["usable"] is False and "amt" in status[mine["id"]]["reason"]
