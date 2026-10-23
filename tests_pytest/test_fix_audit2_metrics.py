"""2차 감사(2B 묶음, 쿼리·지표) 버그의 되돌림 시험. 데이터는 시험 안에서 지어내고, 값은 손으로 센 것(또는 DuckDB 직접 집계)과 맞춘다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from conftest import spy_queries
from metrics import metric_calc as mc
from metrics import metric_catalog as cat

D = dt.datetime
KEY = {"kind": "key", "keys": [["ch", "ch"]]}
COLS = {"unit price": "num", "unit": "text", "store": "text", "day": "date"}
SPACED = {"unit price": "num", "store": "text", "day": "date"}  # "unit" 컬럼은 없다(첫 낱말만 읽으면 없는 컬럼이 된다)
BODY = {"base": "ev", "joins": [], "periods": [], "conditions": [], "calcs": []}


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def run(entry_id, slots, table, align=None, other=None):
    ctx = cat.Ctx(table=table, other=(lambda name: other[name]) if other else None)
    return cat.compute(entry_id, slots, {}, ctx, align, None)


# ---------- 2B1 세션 번호: 같은 시각의 행은 같은 세션 ----------

def test_sessions_rows_with_the_same_time_stay_in_one_session():
    # 사용자 5만 명이 각자 같은 시각 3행 + 5시간 뒤 같은 시각 3행 → 간격 30분이면 세션은 정확히 10만 개(같은 시각 행이 쪼개지면 더 많다)
    base = D(2026, 3, 1)
    users = 50_000
    uid = [u for u in range(users) for _ in range(6)]
    ts = [base + dt.timedelta(hours=5 * k) for _ in range(users) for k in (0, 1) for _ in range(3)]
    s = mc.add_sessions(mem(uid=uid, ts=ts), "uid", "ts", 30)
    assert s.session.nunique().to_pyarrow().as_py() == 2 * users


# ---------- 2B2 날짜 이상값 한 줄이 DAU·user_states를 폭주시키지 않는다 ----------

def test_dau_with_a_far_date_outlier_builds_only_the_last_rows_and_notes_it():
    t = mem(uid=[1, 2], ts=[D(2026, 3, 1, 9), D(9999, 12, 31, 9)])
    got = run("dau", {"사용자": "uid", "시각": "ts"}, t)
    cells = (dt.date(9999, 12, 31) - dt.date(2026, 3, 1)).days + 1
    assert got["value"] == pytest.approx(2 / cells) and len(got["rows"]) == 60  # 평균은 칸 수로 산술 계산
    assert got["rows"][-1] == {"period": "9999-12-31", "users": 1} and got["rows"][0]["users"] == 0
    assert any("이상값" in n and f"{cells:,}칸" in n for n in got["notes"])
    uv = run("uv", {"사용자": "uid", "시각": "ts", "단위": "month"}, t)
    assert uv["value"] == 2 and len(uv["rows"]) == 60


def test_user_states_notes_when_cells_dwarf_the_active_ones():
    t = mem(uid=[1, 2], ts=[D(2026, 3, 1, 9), D(2040, 3, 1, 9)])
    got = run("churn", {"사용자": "uid", "시각": "ts", "단위": "month"}, t)
    assert any("이상값" in n for n in got["notes"])
    quiet = mem(uid=[1, 2], ts=[D(2026, 3, 1, 9), D(2026, 4, 1, 9)])
    assert not any("이상값" in n for n in run("churn", {"사용자": "uid", "시각": "ts", "단위": "month"}, quiet)["notes"])


# ---------- 2B3 공통 키 맞춤 CAC·ROAS ----------

def test_cac_by_key_trims_spend_by_ad_date_when_given():
    # 사용자 활동 3/2~3/3. 광고비 A: 3/1 100(범위 밖)·3/2 40, B: 3/3 60. 신규 A 2명(1·2), B 1명(3)
    users = mem(uid=[1, 2, 3], ts=[D(2026, 3, 2), D(2026, 3, 2), D(2026, 3, 3)], ch=["A", "A", "B"])
    ads = {"ads": mem(day=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 3)], cost=[100.0, 40.0, 60.0], ch=["A", "A", "B"])}
    s = {"사용자": "uid", "시각": "ts", "광고 테이블": "ads", "광고비": "cost", "광고 날짜": "day"}
    got = run("cac", s, users, other=ads, align=KEY)
    assert {r["key"]: r["spend"] for r in got["rows"]} == {"A": 40.0, "B": 60.0} and got["value"] == pytest.approx(100 / 3)
    assert not any("기간이 걸리지 않아요" in n for n in got["notes"])


def test_cac_by_key_without_ad_date_says_spend_has_no_period():
    users = mem(uid=[1], ts=[D(2026, 3, 2)], ch=["A"])
    ads = {"ads": mem(cost=[100.0, 7.0], ch=["A", "Z"])}
    got = run("cac", {"사용자": "uid", "시각": "ts", "광고 테이블": "ads", "광고비": "cost"}, users, other=ads, align=KEY)
    assert any("광고비에는 기간이 걸리지 않아요" in n for n in got["notes"])
    # 신규가 0명인 키(Z)의 광고비도 기간 맞춤처럼 남긴다
    assert {r["key"]: r["new_users"] for r in got["rows"]} == {"A": 1, "Z": 0} and got["value"] == 107.0


def test_new_users_by_key_counts_each_user_once_under_the_first_key():
    # 사용자 1은 3/1에 A, 3/2에 B로 들어왔다 → 신규는 A에만 1명(B에도 세면 합이 늘어난다)
    users = mem(uid=[1, 1, 2], ts=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 2)], ch=["A", "B", "B"])
    ads = {"ads": mem(cost=[10.0, 10.0], ch=["A", "B"])}
    got = run("cac", {"사용자": "uid", "시각": "ts", "광고 테이블": "ads", "광고비": "cost"}, users, other=ads, align=KEY)
    assert {r["key"]: r["new_users"] for r in got["rows"]} == {"A": 1, "B": 1} and got["parts"]["new_users"] == 2


# ---------- 2B6 키 타입이 달라 맞는 키가 없으면 알린다 ----------

def test_key_type_mismatch_is_noted():
    users = mem(uid=[1, 2], ts=[D(2026, 3, 1)] * 2, cid=[1, 2])
    align = {"kind": "key", "keys": [["cid", "cid"]]}
    s = {"사용자": "uid", "시각": "ts", "광고 테이블": "ads", "광고비": "cost"}
    got = run("cac", s, users, other={"ads": mem(cost=[10.0, 20.0], cid=[1.0, 2.0])}, align=align)
    assert any("키 타입을 확인하세요" in n for n in got["notes"])
    same = run("cac", s, users, other={"ads": mem(cost=[10.0], cid=[1])}, align=align)
    assert not any("키 타입" in n for n in same["notes"])


# ---------- 2B4 공백 있는 컬럼 이름의 조건·정렬 ----------

def test_cond_columns_reads_the_longest_spaced_column_name():
    from query import flow
    assert flow.cond_columns("unit price > 3", COLS) == {"unit price"}
    assert flow.cond_columns("unit price > 3 or store = a", COLS) == {"unit price", "store"}
    assert flow.cond_columns({"any": [{"line": "unit price is null"}, "unit = x"]}, COLS) == {"unit price", "unit"}
    assert flow.cond_columns("unit price > 3") == {"unit"}  # 컬럼 목록을 모르면 첫 낱말


def test_sentence_check_accepts_spaced_column_in_condition_and_order():
    from query import sentence_run as sr
    assert [i for i in sr.check("e07", {"조건": "unit price > 3", "대상": "store"}, SPACED) if i["level"] == "bad"] == []
    bad = [i for i in sr.check("e07", {"조건": "zz price > 3", "대상": "store"}, SPACED) if i["level"] == "bad"]
    assert len(bad) == 1 and "‘zz’" in bad[0]["text"]  # 모르는 이름은 첫 낱말로 읽는다
    ok = sr.check("e36", {"정렬 기준": "unit price desc", "값": "unit price", "종류": "이동 평균"}, SPACED)
    assert [i for i in ok if i["level"] == "bad"] == []


# ---------- 2B5 문장 → 계산식 ----------

def test_in_values_keep_commas_inside_quotes_and_num_accepts_leading_dot():
    from query import sentence_run as sr
    assert sr.formula_one("store in 'a,b', c, 3") == "isin(col('store'), ['a,b', 'c', 3])"
    assert sr.formula_one("store except 'x', y") == "not isin(col('store'), ['x', 'y'])"
    assert [sr.num(x) for x in (".5", "-.5", "+.25")] == [".5", "-.5", "+.25"]
    assert [sr.num(x) for x in ("5.", ".", "1.2.3", ". 5")] == ["'5.'", "'.'", "'1.2.3'", "'. 5'"]


def test_period_dict_without_column_is_a_korean_error():
    from query import sentence_run as sr
    done = sr.compile("e07", {"조건": "store = a", "대상": "store", "기간": {"start": "2026-03-01", "end": "2026-03-31"}}, BODY, COLS)
    assert done["ok"] is False and "날짜 컬럼" in done["reason"]


# ---------- 2B7·2B8 stage_clear·meta ----------

def test_stage_clear_skips_null_rows_and_sorts_numbers_as_numbers():
    # 스테이지 2: 도전 2·클리어 1, 스테이지 10: 도전 1·클리어 1. 결과/스테이지/사용자가 빈 행은 도전이 아니다
    t = mem(uid=[1, 1, 2, 3, None, 4], stage=[2, 2, 10, None, 2, 10], res=["win", "lose", "win", "win", "win", None])
    s = {"스테이지": "stage", "결과": "res", "성공 값": "win", "사용자": "uid"}
    got = run("stage_clear", s, t)
    assert [(r["stage"], r["tries"], r["clears"]) for r in got["rows"]] == [("2", 2, 1), ("10", 1, 1)]  # 2가 10보다 앞(글자 순이면 10이 앞)
    assert got["value"] == pytest.approx(2 / 3)


def test_meta_skips_null_item_and_result_rows():
    t = mem(item=["a", "a", "b", None, "b", "b"], res=["win", "lose", "win", "win", None, "lose"])
    got = run("pick_rate", {"항목": "item", "결과": "res", "성공 값": "win"}, t)
    assert [(r["item"], r["picks"], r["wins"]) for r in got["rows"]] == [("a", 2, 1), ("b", 2, 1)] and got["value"] == 0.5


def test_stage_clear_meta_and_cross_table_fetches_are_capped(monkeypatch):
    monkeypatch.setattr(mc, "MAX_FETCH_ROWS", 2)
    t = mem(uid=[1, 2, 3], stage=[1, 2, 3], res=["win"] * 3, item=["a", "b", "c"])
    with pytest.raises(ValueError, match="범위를 좁혀"):
        run("stage_clear", {"스테이지": "stage", "결과": "res", "성공 값": "win"}, t)
    with pytest.raises(ValueError, match="범위를 좁혀"):
        run("pick_rate", {"항목": "item", "결과": "res", "성공 값": "win"}, t)
    users = mem(uid=[1, 2, 3], ts=[D(2026, 3, 1)] * 3, ch=["A", "B", "C"])
    ads = {"ads": mem(cost=[1.0, 2.0, 3.0], ch=["A", "B", "C"])}
    with pytest.raises(ValueError, match="범위를 좁혀"):
        run("cac", {"사용자": "uid", "시각": "ts", "광고 테이블": "ads", "광고비": "cost"}, users, other=ads, align=KEY)


# ---------- 2B9 ----------

def test_mde_with_zero_variance_is_none_with_a_note():
    t = mem(g=["c"] * 4 + ["t"] * 4, conv=[0, 0, 0, 0, 1, 0, 1, 1])
    got = run("mde", {"실험군": "g", "지표 값": "conv", "대조군 값": "c", "실험군 값": "t"}, t)
    assert got["value"] is None and any("최소 검출 효과를 구할 수 없어요" in n for n in got["notes"])
    assert mc.mde_two_proportions(0.0, 100) is None and mc.mde_two_proportions(1.0, 100) is None
    assert mc.mde_two_proportions(0.5, 1000) == pytest.approx(0.062647, abs=2e-6)


def test_money_supply_uses_the_last_known_balance_when_the_last_one_is_null():
    # 사용자 1: 잔액 100 → 80 → NULL(마지막 행) ⇒ 80. 사용자 2: 50
    t = mem(uid=[1, 1, 1, 2], ts=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 3), D(2026, 3, 3)], bal=[100.0, 80.0, None, 50.0])
    got = run("money_supply", {"사용자": "uid", "시각": "ts", "잔액": "bal", "활성 기준(일)": 30}, t)
    assert got["value"] == 130.0


def test_survival_wording_says_n_days_or_more():
    assert "일 이상 안 오면" in cat.get("survival")["sentence"]
    t = mem(uid=[1, 2], ts=[D(2026, 3, 1), D(2026, 3, 5)])
    got = run("survival", {"사용자": "uid", "시각": "ts", "미접속 기준(일)": 4}, t)
    assert any("4일 이상 앞서면" in n for n in got["notes"])


# ---------- 2B10 흐름 최적화 원격 조회 횟수 ----------

def test_remote_propose_query_count_matches_the_note(tmp_path, monkeypatch):
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
    note = flow_opt.propose(graph, get, con, remote=True, source_key="k")["note"]
    limit = int(note.split("최대 ")[1].split("번")[0])
    sent = spy_queries(monkeypatch)
    flow_opt.propose(graph, get, con, remote=True, scan_ok=True, source_key="k")
    scans = [q for q in sent if not q.lstrip().upper().startswith(("EXPLAIN", "DESCRIBE")) and "duckdb_tables" not in q and "SELECT CURRENT_" not in q.upper()]
    assert len(scans) == 8 and len(scans) <= limit == 9, (len(scans), limit)  # 원래 1 + 규칙 비교 3(3번은 할 것 없음) + 키 고유 확인 2곳 x 2번
    con.disconnect()
