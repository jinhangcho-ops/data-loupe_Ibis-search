"""감사에서 재현된 쿼리·지표(B 묶음) 버그의 되돌림 시험. 데이터는 시험 안에서 지어내고, 값은 손으로 센 것(또는 DuckDB 직접 집계)과 맞춘다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from metrics import metric_catalog as cat

D = dt.datetime


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def run(entry_id, slots, table, align=None, other=None):
    ctx = cat.Ctx(table=table, other=(lambda name: other[name]) if other else None)
    return cat.compute(entry_id, slots, {}, ctx, align, None)


# ---------- 1 세션: 사용자·시각이 NULL인 행은 세션에서 뺀다 ----------

def test_session_ignores_null_user_and_time_rows():
    # 사용자 1: 9:00·9:05 한 세션(300초). NULL 사용자 두 행(1시간·1일 간격)과 시각 NULL 행은 세션에 끼면 안 된다.
    t = mem(uid=[1, 1, None, None, 2], ts=[D(2026, 3, 1, 9, 0), D(2026, 3, 1, 9, 5), D(2026, 3, 1, 9, 10), D(2026, 3, 2, 9, 10), None])
    s = {"사용자": "uid", "시각": "ts"}
    asd = run("asd", s, t)
    assert asd["parts"] == {"stay_seconds": 300.0, "sessions": 1} and asd["value"] == 300.0
    assert run("bounce", s, t)["value"] == 0.0  # 세션 1개, 이벤트 2개


# ---------- 2 CAC·ROAS: 광고비 테이블에는 기간이 안 걸려 있으니 사용자 쪽 범위로 자른다 ----------

def test_cac_trims_spend_to_user_period():
    # 광고비 3/1 100, 3/2 100. 기간을 3/2로 건 사용자 표: 3/2에 신규 10명 → CAC는 100 ÷ 10 = 10 (3/1 광고비까지 세면 20)
    users = mem(uid=list(range(10)), ts=[D(2026, 3, 2, 9)] * 10, ch=["A"] * 10)
    ads = {"ads": mem(day=[D(2026, 3, 1), D(2026, 3, 2)], cost=[100.0, 100.0], ch=["A", "B"])}
    s = {"사용자": "uid", "시각": "ts", "광고 테이블": "ads", "광고비": "cost", "광고 날짜": "day"}
    got = run("cac", s, users, other=ads, align={"kind": "period", "unit": "day"})
    assert got["value"] == 10.0 and [r["key"] for r in got["rows"]] == ["2026-03-02"]
    assert any("앞의 활동" in n for n in got["notes"])
    # 공통 키 맞춤: 사용자가 있는 채널 A의 광고비 100만 센다(B 100은 이 사용자 표에 사용자가 없다)
    by_key = run("cac", {k: v for k, v in s.items() if k != "광고 날짜"}, users, other=ads, align={"kind": "key", "keys": [["ch", "ch"]]})
    assert by_key["value"] == 10.0 and [r["key"] for r in by_key["rows"]] == ["A"]


def test_roas_notes_unfinished_cohorts():
    users = mem(uid=[1], ts=[D(2026, 3, 2, 9)], amt=[50.0])
    ads = {"ads": mem(day=[D(2026, 3, 2)], cost=[100.0])}
    s = {"사용자": "uid", "시각": "ts", "금액": "amt", "N일": 7, "광고 테이블": "ads", "광고비": "cost", "광고 날짜": "day"}
    got = run("roas", s, users, other=ads, align={"kind": "period", "unit": "day"})
    assert got["value"] == 0.5 and any("N일이 덜 지난" in n or "7일이 덜 지난" in n for n in got["notes"])


# ---------- 3 or 조건: 모든 조각의 컬럼을 읽는다 ----------

def test_or_condition_reads_every_piece_column():
    from query import flow
    from metrics import my_metrics
    assert flow.cond_columns("channel = web or country = US") == {"channel", "country"}
    assert flow.cond_columns({"line": "a > 1 or b is null"}) == {"a", "b"}
    assert flow.cond_columns("memo = 'a or zzz = 1'") == {"memo"}  # 따옴표 안의 or는 값의 일부
    assert my_metrics.columns_in_text("channel = web or country = US") == {"channel", "country"}


def test_or_condition_across_join_is_not_pushed_down_and_propose_survives(tmp_path):
    import duckdb
    from query import flow, flow_opt
    path = tmp_path / "o.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table sales as select i as id, (i % 3)::int as shop_id, ((i + 1) * 10)::double as amt from range(12) t(i)")
    raw.execute("create table shops as select i::int as shop_id, case when i < 2 then 'KR' else 'US' end as country from range(3) t(i)")
    raw.close()
    con = ibis.duckdb.connect(str(path))
    graph = flow.from_body({"base": "sales", "joins": ["shops inner shop_id=shop_id"], "conditions": ["amt > 100 or country = KR"],
                            "aggregate": {"by": ["country"], "series": [{"agg": "sum", "y": "amt", "name": "total"}]}})
    found = flow_opt.propose(graph, lambda name, schema=None: con.table(name), con, only=[1])
    rule1 = found["rules"][0]
    assert rule1["items"] == [] and rule1["refused"], rule1  # 두 쪽 컬럼을 함께 읽는 or 조건은 한쪽으로 내릴 수 없다
    con.disconnect()


# ---------- 4 흐름 최적화: apply는 경고용 조회를 다시 하지 않고, 확인 문구에 조회 횟수를 적는다 ----------

def test_apply_skips_fanout_warnings_and_remote_note_tells_query_count(tmp_path, monkeypatch):
    import duckdb
    from core import held
    from query import flow, flow_opt
    path = tmp_path / "o.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table sales as select i as id, (i % 3)::int as shop_id, ((i + 1) * 10)::double as amt from range(12) t(i)")
    raw.execute("create table shops as select i::int as shop_id, 'x' as name from range(3) t(i)")
    raw.close()
    con = ibis.duckdb.connect(str(path))
    monkeypatch.setattr(held, "remote", lambda con: True)
    get = lambda name, schema=None: con.table(name)
    graph = flow.from_body({"base": "sales", "joins": ["shops left shop_id=shop_id"], "conditions": ["amt > 50"], "aggregate": {"by": ["shop_id"], "series": [{"agg": "sum", "y": "amt", "name": "total"}]}})
    calls = []
    real = flow_opt.fanout_warnings
    monkeypatch.setattr(flow_opt, "fanout_warnings", lambda g, c: calls.append(1) or real(g, c))
    found = flow_opt.propose(graph, get, con, remote=True, source_key="k")
    assert len(calls) == 1 and found["note"].startswith("원격 DB라") and "최대 7번 조회해요" in found["note"]  # 원래 결과 1 + 규칙 4개 + 조인 1개의 양쪽 키 확인 2
    flow_opt.apply(graph, [1], get, con, remote=True, scan_ok=True, source_key="k")
    assert len(calls) == 1  # apply는 경고를 다시 계산하지 않는다
    con.disconnect()


# ---------- 5 지표 세부 ----------

def test_dau_counts_empty_days_as_zero():
    # 3/1에 2명(1·2), 3/4에 1명(1): 3/2·3/3은 0명 → DAU = (2 + 0 + 0 + 1) / 4
    t = mem(uid=[1, 2, 1], ts=[D(2026, 3, 1, 9), D(2026, 3, 1, 10), D(2026, 3, 4, 9)])
    got = run("dau", {"사용자": "uid", "시각": "ts"}, t)
    assert got["value"] == 0.75 and [r["users"] for r in got["rows"]] == [2, 0, 0, 1] and got["spread"]["n"] == 4


def test_stickiness_note_says_inactive_days_are_left_out():
    t = mem(uid=[1, 2, 1], ts=[D(2026, 3, 1, 9), D(2026, 3, 1, 10), D(2026, 3, 4, 9)])
    got = run("stickiness", {"사용자": "uid", "시각": "ts"}, t)
    assert got["value"] == pytest.approx(1.5 / 2) and any("활동이 없는 날" in n for n in got["notes"])  # 활동 있는 날 3/1(2명)·3/4(1명)의 평균 1.5 ÷ MAU 2


def test_null_time_rows_do_not_break_payment_metrics():
    # 시각이 빈 행이 있어도 TypeError가 나지 않고, 가짜 사용자-날이 생기지 않는다.
    t = mem(uid=[1, 2, 2, 3], ts=[D(2026, 3, 1, 9), D(2026, 3, 1, 9), None, D(2026, 3, 2, 9)], amt=[100.0, 50.0, 70.0, 0.0])
    s = {"사용자": "uid", "시각": "ts", "금액": "amt"}
    pur = run("pur", {**s, "단위": "day"}, t)
    assert [(r["period"], r["payers"], r["users"]) for r in pur["rows"]] == [("2026-03-01", 2, 2), ("2026-03-02", 0, 1)]
    npa = run("npa", {**s, "단위": "day"}, t)
    assert [(r["period"], r["npa"]) for r in npa["rows"]] == [("2026-03-01", 2)]
    arpdau = run("arpdau", s, t)  # 사용자-날 3개(1/3-1, 2/3-1, 3/3-2): 매출 150 ÷ 3
    assert arpdau["value"] == 50.0 and arpdau["parts"] == {"gross_revenue": 150.0, "user_days": 3}


def test_pv_per_uv_distribution_counts_every_user():
    # 사용자 1: 조회 2회, 사용자 2: 클릭만(조회 0회) → 분포는 [2, 0]
    t = mem(uid=[1, 1, 2, 2], ev=["page", "page", "click", "click"])
    got = run("pv_per_uv", {"사용자": "uid", "이벤트": "ev", "이벤트 값": "page"}, t)
    assert got["value"] == 1.0 and got["parts"] == {"pv": 2, "uv": 2} and got["spread"]["n"] == 2 and got["spread"]["median"] == 1.0


def test_aov_ignores_null_amount_and_null_orders():
    # 주문 A 100(+NULL 행), 주문 B는 금액 NULL뿐, 주문 번호 NULL 행 → 주문 1건당 100
    t = mem(oid=["A", "A", "B", None], amt=[100.0, None, None, 70.0])
    got = run("aov", {"금액": "amt", "주문": "oid"}, t)
    assert got["value"] == 100.0 and got["parts"] == {"gmv": 100.0, "orders": 1}
    assert run("aov", {"금액": "amt"}, t)["parts"] == {"gmv": 170.0, "orders": 2}


def test_big_fetches_are_capped(monkeypatch):
    from metrics import metric_calc as mc
    monkeypatch.setattr(mc, "MAX_FETCH_ROWS", 2)
    spans = mem(s=[D(2026, 3, 1, 9), D(2026, 3, 1, 10), D(2026, 3, 1, 11)], e=[D(2026, 3, 1, 9, 30), D(2026, 3, 1, 10, 30), D(2026, 3, 1, 11, 30)])
    with pytest.raises(ValueError, match="범위를 좁혀"):
        run("pcu", {"시작": "s", "끝": "e"}, spans)
    ev = mem(uid=[1, 2, 3], ts=[D(2026, 3, 1)] * 3, ev=["a", "a", "a"])
    with pytest.raises(ValueError, match="범위를 좁혀"):
        mc.seconds_between(ev, "uid", "ts", "ev", "a", "a")
    pages = mem(sid=["s1", "s2", "s3"], ts=[D(2026, 3, 1)] * 3, pg=["a", "b", "c"])
    with pytest.raises(ValueError, match="범위를 좁혀"):
        mc.exit_rates(pages, "sid", "ts", "pg")


@pytest.mark.parametrize("slot", ["N", "최대 일수", "최근 일수", "N일", "미접속 기준(일)", "천장 횟수", "K"])
def test_integer_slots_have_an_upper_limit(slot):
    entries = {"N": "retention_classic", "최대 일수": "payback", "최근 일수": "power_curve", "N일": "ltv", "미접속 기준(일)": "survival", "천장 횟수": "pity_reach", "K": "ndcg"}
    e = cat.get(entries[slot])
    s = next(x for x in e["slots"] if x["name"] == slot)
    assert s["max"] == cat.MAX_N
    with pytest.raises(ValueError, match="이하여야 해요"):
        cat.check_value(s, cat.MAX_N + 1, mem(a=[1]))
    assert cat.check_value(s, cat.MAX_N, mem(a=[1])) == cat.MAX_N


def test_align_unit_year_is_refused_everywhere():
    from metrics import my_metrics
    assert my_metrics.ALIGN_UNITS == ("day", "week", "month")


def test_power_with_zero_error_is_none_with_a_note():
    # 두 군 모두 전환율이 0 또는 1이라 표준오차가 0
    t = mem(g=["c"] * 4 + ["t"] * 4, conv=[0, 0, 0, 0, 1, 1, 1, 1])
    got = run("power", {"실험군": "g", "지표 값": "conv", "대조군 값": "c", "실험군 값": "t"}, t)
    assert got["value"] is None and any("표준오차가 0" in n for n in got["notes"])


def test_sentence_formula_number_only_plain_decimals():
    from query import sentence_run
    assert [sentence_run.num(x) for x in ("12", "-1.5", "+3", "0", "0.5")] == ["12", "-1.5", "+3", "0", "0.5"]
    assert [sentence_run.num(x) for x in ("007", "nan", "1e3", "inf", "1_000", "5.")] == ["'007'", "'nan'", "'1e3'", "'inf'", "'1_000'", "'5.'"]


def test_prepped_new_column_name_has_no_spaces_or_symbols():
    from query import sentence_run as sr
    cols = {"url (원본)": "text", "name": "text"}
    done = sr.compile("e40", {"새 이름": "head", "컬럼": {"column": "url (원본)", "prep": "after_q"}, "가공": {"func": "upper"}}, {"base": "ev", "joins": [], "periods": [], "conditions": [], "calcs": []}, cols)
    assert done["ok"], done
    first = done["body"]["calcs"][0]
    assert "이름 url_원본_after_q" in first, first  # 공백·괄호는 _로
