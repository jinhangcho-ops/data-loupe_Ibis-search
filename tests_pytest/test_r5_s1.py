"""R5 S1(서버): 계산 줄 그룹집계·전처리(C3·C4), 전처리 단계 목록(C5), 치환 방식(C6), 날짜 범위(C7), 넘기기 shown(C8), 테이블 놓기(C10),
컬럼끼리 비교(F12·F15), 표본 없이 전체 요약(F18), 퍼널 기간 제한을 앞 단계 기준으로(F29), 새 컬럼 상자의 서버 쪽(P9).
데이터는 시험 안에서 지어낸 임시 DuckDB·SQLite·memtable."""
import datetime as dt
import re
import sqlite3
import threading
from pathlib import Path

import duckdb
import pytest

from lazy import ibis
from core import calc
from core import held
from core import prep_ops
from core import server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def shop(folder, tmp_path):
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute('create table orders ("주문번호" varchar, "주문일" date, "시각" timestamp, "채널" varchar, "금액" double, "고객" varchar)')
    raw.executemany("insert into orders values (?, ?, ?, ?, ?, ?)",
                    [(f"o{i}", dt.date(2025, 3, 28) + dt.timedelta(days=i % 44), dt.datetime(2025, 3, 28, 9) + dt.timedelta(hours=i), "앱웹"[i % 2], 1000.0 + i, f"c{i % 5}")
                     for i in range(60)])
    raw.execute('create table other (k varchar, d date)')
    raw.execute("insert into other values ('a', date '2024-01-01')")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def body(**extra):
    return {"schema": "", "base": "orders", "joins": [], "periods": [], "conditions": [], "calcs": [], "sorts": [], "columns": [], **extra}


def rows_of(expr, *cols):
    return sorted(tuple(r[c] for c in cols) for r in expr.to_pyarrow().to_pylist())


# ---------- C3 그룹집계 ----------

EVENTS = {"u": ["a", "a", "a", "b", "b", "c"], "s": ["x", "y", "x", "x", "x", None], "n": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}


def sqlite_events(tmp_path):
    path = tmp_path / "e.db"
    raw = sqlite3.connect(path)
    raw.execute("create table e (u text, s text, n real)")
    raw.executemany("insert into e values (?, ?, ?)", list(zip(*EVENTS.values())))
    raw.commit()
    raw.close()
    return ibis.sqlite.connect(str(path)).table("e")


@pytest.mark.parametrize("engine", ["duckdb", "sqlite"])
def test_group_aggregate_nunique(engine, tmp_path):
    t = ibis.memtable(EVENTS) if engine == "duckdb" else sqlite_events(tmp_path)
    out = calc.apply(["그룹집계 nunique(s) 그룹 u 이름 종류수"], t)
    assert list(out.columns) == [*t.columns, "종류수"]
    assert rows_of(out, "u", "n", "종류수") == [("a", 1.0, 2), ("a", 2.0, 2), ("a", 3.0, 2), ("b", 4.0, 1), ("b", 5.0, 1), ("c", 6.0, 0)]
    whole = calc.apply(["그룹집계 count_distinct(u) 이름 사람수"], t)  # 그룹이 없으면 표 전체 값을 모든 행에
    assert {r["사람수"] for r in whole.to_pyarrow().to_pylist()} == {3} and whole.count().to_pyarrow().as_py() == 6


def test_group_aggregate_options():
    t = ibis.memtable(EVENTS)
    out = calc.apply(["그룹집계 sum(n) 그룹 u, s 조건 n > 1 이름 큰합", "그룹집계 quantile(n, 0.5) 그룹 u 이름 중앙"], t)
    got = {(r["u"], r["s"], r["n"]): (r["큰합"], r["중앙"]) for r in out.to_pyarrow().to_pylist()}
    assert got[("a", "x", 1.0)] == (3.0, 2.0) and got[("a", "y", 2.0)] == (2.0, 2.0) and got[("b", "x", 4.0)] == (9.0, 4.5)
    with pytest.raises(ValueError, match="3개"):
        calc.apply(["그룹집계 count() 그룹 u, s, n, u 이름 x"], t)
    with pytest.raises(ValueError, match="이미 있어요"):
        calc.apply(["그룹집계 count() 그룹 u 이름 n"], t)
    with pytest.raises(ValueError, match="집계"):
        calc.apply(["그룹집계 lag(n) 그룹 u 이름 x"], t)


def test_formula_whole_table_aggregate(shop):
    """쿼리 식 줄의 집계 함수는 표 전체 값(전처리 계산 컬럼과 같게)."""
    r = shop.post("/api/query", json=body(calcs=["식 (금액 - mean(금액)) / std(금액) 이름 z"], limit=100)).get_json()
    z = [row["z"] for row in r["rows"]["rows"]]
    assert len(z) == 60 and abs(sum(z)) < 1e-9


# ---------- C4 전처리 줄 ----------

def test_prep_line():
    t = ibis.memtable({"금액": [" 1,000 ", "20", None], "판매 채널": ["App", "web", "APP"]})
    out = calc.apply(['전처리 type 금액 {"to": "float", "clean": true}', '전처리 case 판매 채널 "lower"', '전처리 fill 금액 {"how": "value", "value": 0}'], t)
    assert out["금액"].type().is_floating()
    assert rows_of(out, "판매 채널", "금액") == [("app", 0.0), ("app", 1000.0), ("web", 20.0)]
    for line in ("전처리 label 판매 채널 true", '전처리 fill 금액 {"how": "mean"}', '전처리 scale 금액 "standard"', "전처리 expand 판매 채널 true"):
        with pytest.raises(ValueError, match="전처리 화면으로 넘겨서"):  # 표 전체 통계를 먼저 읽는 단계는 쿼리 계산 줄로는 거절(리뷰 ①)
            calc.apply([line], t)
    with pytest.raises(ValueError, match="JSON"):
        calc.apply(["전처리 type 금액 to=float"], t)
    with pytest.raises(ValueError, match="전처리 항목"):
        calc.apply(["전처리 nope 금액 true"], t)


def test_query_prep_line(shop):
    r = shop.post("/api/query", json=body(calcs=['전처리 replace 채널 {"how": "exact", "pairs": [["앱", "app"]], "to_new": "채널2"}'], limit=5)).get_json()
    assert "error" not in r, r
    cols = r["rows"]["columns"]
    assert cols[-1] == "채널2" and {row["채널2"] for row in r["rows"]["rows"]} <= {"app", "웹"}


# ---------- C5 단계 목록 ----------

def test_prep_ops_labels_match_screen(shop):
    found = shop.get("/api/prep/ops").get_json()["ops"]
    assert [o["id"] for o in found] == prep_ops.OPS_NAMES
    assert "/api/prep/ops" in server.FREE
    html = (ROOT / "static/prep/prep.html").read_text(encoding="utf-8")
    screen = {m[1]: (m[2], m[3]) for m in re.finditer(r'^  (\w+): \{ label: "([^"]+)", group: "([^"]+)"', html, re.M)}
    assert {o["id"]: (o["label"], o["group"]) for o in found} == screen
    kinds = {None: "any", prep_ops.STRING: "str", prep_ops.NUMBER: "num", prep_ops.DATE: "date"}
    for o, op in zip(found, prep_ops.OPS):
        assert o["need"] in ("str", "num", "date", "any", "struct", "list")
        assert o["need"] == kinds[op[1]] or o["id"] == "expand"
        assert all(isinstance(f.get("name"), str) for f in o["fields"])
    assert next(o for o in found if o["id"] == "replace")["need"] == "any"


# ---------- C6 치환 ----------

def rep(values, spec, kind="string"):
    t = ibis.memtable({"c": values}, schema={"c": kind})
    out = prep_ops.apply_ops(t, "c", {"replace": spec})
    return out[spec.get("to_new") or "c" if isinstance(spec, dict) else "c"].to_pyarrow().to_pylist()


def test_replace_modes():
    v = ["Apple", "apple pie", " banana ", None, "cherry"]
    assert rep(v, [["Apple", "사과"]]) == ["사과", "apple pie", " banana ", None, "cherry"]  # 옛 꼴
    assert rep(v, {"how": "exact", "pairs": [["apple", "사과"]], "ignore_case": True}) == ["사과", "apple pie", " banana ", None, "cherry"]
    assert rep(v, {"how": "contains", "pairs": [["an", "AN"], ["pie", "P"]]}) == ["Apple", "apple P", " bANANa ", None, "cherry"]
    assert rep(v, {"how": "contains", "pairs": [["APP", "x"]], "ignore_case": True}) == ["xle", "xle pie", " banana ", None, "cherry"]
    assert rep(v, {"how": "prefix", "pairs": [["app", "A"]]}) == ["Apple", "Ale pie", " banana ", None, "cherry"]
    assert rep(v, {"how": "suffix", "pairs": [["rry", "!"]]}) == ["Apple", "apple pie", " banana ", None, "che!"]
    assert rep(v, {"how": "regex", "pairs": [["^(a)(p+)", r"\2\1"]]}) == ["Apple", "ppale pie", " banana ", None, "cherry"]
    assert rep(v, {"how": "list", "pairs": [[["Apple", "cherry"], "과일"]]}) == ["과일", "apple pie", " banana ", None, "과일"]
    assert rep(v, {"how": "cond", "pairs": [["c like an", "바나나"]]}) == ["Apple", "apple pie", "바나나", None, "cherry"]
    assert rep(v, {"how": "null_to", "pairs": [["", "없음"]]}) == ["Apple", "apple pie", " banana ", "없음", "cherry"]
    assert rep(v, {"how": "to_null", "pairs": [["cherry", ""], ["Apple", ""]]}) == [None, "apple pie", " banana ", None, None]
    assert rep(v, {"how": "exact", "pairs": [["banana", "B"]], "trim": True}) == ["Apple", "apple pie", "B", None, "cherry"]
    assert rep(v, {"how": "exact", "pairs": [["Apple", "A"]], "rest": "null"}) == ["A", None, None, None, None]
    assert rep(v, {"how": "exact", "pairs": [["Apple", "A"]], "rest": "기타"}) == ["A", "기타", "기타", "기타", "기타"]
    assert rep(v, {"how": "exact", "pairs": [["Apple", "A"]], "to_new": "c2"}) == ["A", "apple pie", " banana ", None, "cherry"]


def test_replace_typed_columns():
    assert rep([1, 2, None], {"how": "exact", "pairs": [["1", "10"]]}, "int64") == [10, 2, None]
    assert rep([1, 2, None], {"how": "null_to", "pairs": [["", "0"]]}, "int64") == [1, 2, 0]
    assert rep([dt.date(2025, 1, 1), dt.date(2025, 1, 2)], {"how": "exact", "pairs": [["2025-01-01", "2024-12-31"]]}, "date") == [dt.date(2024, 12, 31), dt.date(2025, 1, 2)]
    t = ibis.memtable({"c": [1, 2]})
    with pytest.raises(ValueError):
        prep_ops.apply_ops(t, "c", {"replace": {"how": "exact", "pairs": [["x", "1"]]}})
    with pytest.raises(ValueError, match="글자 컬럼"):
        prep_ops.apply_ops(t, "c", {"replace": {"how": "contains", "pairs": [["1", "2"]]}})
    t = ibis.memtable({"c": ["a"]})
    with pytest.raises(ValueError, match="이미 있어"):
        prep_ops.apply_ops(t, "c", {"replace": {"how": "exact", "pairs": [["a", "b"]], "to_new": "C"}})
    with pytest.raises(ValueError, match="형식"):
        prep_ops.apply_ops(t, "c", {"replace": {"how": "nope", "pairs": [["a", "b"]]}})


# ---------- C7 날짜 범위 ----------

def test_date_range(shop):
    from query import search_app
    r = shop.get("/api/date_range?schema=&base=orders&column=주문일").get_json()
    assert r == {"min": "2025-03-28", "max": "2025-05-10", "rows": 60}
    r = shop.get("/api/date_range?schema=&base=orders&column=시각").get_json()
    assert r == {"min": "2025-03-28T09:00:00", "max": "2025-03-30T20:00:00", "rows": 60}
    assert (None, "orders", "시각") in search_app.DATE_RANGES
    assert "error" in shop.get("/api/date_range?schema=&base=orders&column=금액").get_json()
    shop.post("/api/disconnect", json={})
    assert not search_app.DATE_RANGES  # 연결이 바뀌면 비운다


# ---------- C8 넘기기 shown ----------

def test_handoff_shown(shop):
    shown = {"columns": ["주문번호", "금액"], "rows": [["o1", 1001.0]], "offset": 0, "total": 60}
    r = shop.post("/api/handoff", json={**body(limit=20), "mode": "query", "name": "넘김", "shown": shown}).get_json()
    assert r["base"] == "@넘김"
    got = shop.get("/api/prep/handed?base=" + r["base"]).get_json()
    assert got["name"] == "넘김" and got["shown"] == shown and "shown" not in got["body"] and got["body"]["base"] == "orders"
    assert "error" in shop.get("/api/prep/handed?base=@없음").get_json()


# ---------- C10 테이블 놓기 ----------

def test_release_forgets_only_that_table(shop):
    from core import querying
    from prep import prep_app
    orders, other = (querying.search_key({"schema": "", "base": b}) for b in ("orders", "other"))
    copy = ("prep", prep_app.table_key({"schema": "", "base": "orders"}), "[]", ())
    con = server.get_con()
    for key, base in ((orders, "orders"), (other, "other"), (copy, "orders")):
        server.run(lambda k=key, b=base: held.ensure(k, con.table(b), threading.Event(), cap=False))
    prep_app.SAMPLES.update({prep_app.table_key({"schema": "", "base": b}): (1, None, {}) for b in ("orders", "other")})
    assert shop.post("/api/release", json={"schema": "", "base": "orders"}).get_json() == {"ok": True}
    assert other in held.store["items"] and orders not in held.store["items"] and copy not in held.store["items"]
    assert list(prep_app.SAMPLES) == [prep_app.table_key({"schema": "", "base": "other"})]
    assert held.table(held.store["items"][other][0]).count().to_pyarrow().as_py() == 1


# ---------- F12·F15 컬럼끼리 비교 ----------

def test_column_to_column_compare():
    from core import parse
    T = dt.datetime
    t = ibis.memtable({"a": [T(2025, 1, 1, 9), T(2025, 1, 2), None, T(2025, 1, 3)], "b": [T(2025, 1, 1, 9), T(2025, 1, 1), T(2025, 1, 1), None],
                       "x": [1, 5, 3, None], "y": [1.0, 2.0, 4.0, 1.0], "p": ["k", "m", None, "z"], "q": ["k", "n", "q", None]})
    n = t.mutate(i=ibis.row_number())
    pick = lambda cond: sorted(r["i"] for r in n.filter(parse.predicate(cond, n)).to_pyarrow().to_pylist())  # noqa: E731
    assert pick("a = b") == [0] and pick("a > b") == [1] and pick("a >= b") == [0, 1] and pick("a < b") == [] and pick("a != b") == [1, 2, 3]
    assert pick("x > y") == [1] and pick("x <= y") == [0, 2] and pick("x = y") == [0] and pick("x != y") == [1, 2, 3]
    assert pick("p = q") == [0] and pick("p != q") == [1, 2, 3] and pick("p < q") == [1]


# ---------- F18 표본 없이 전체 요약 ----------

def test_profile_full_without_sample(shop):
    r = shop.post("/api/profile/full", json={"schema": "", "base": "orders"}).get_json()
    assert "error" not in r, r
    assert r["rows"] == 60 and all(c["diff"] == [] for c in r["columns"])


# ---------- F29 퍼널 기간 제한은 앞 단계 기준 ----------

def test_funnel_window_from_previous_step():
    from core import funnel
    m = lambda n: dt.datetime(2025, 1, 1) + dt.timedelta(minutes=n)
    t = ibis.memtable({"u": ["a"] * 3 + ["b"] * 3, "s": ["A", "B", "C"] * 2, "t": [m(0), m(50), m(100), m(0), m(70), m(80)]})
    found = funnel.compute(t, "u", "s", "t", ["A", "B", "C"], window_seconds=3600)
    assert [s["users"] for s in found["steps"]] == [2, 1, 1]  # a: 50분 뒤 B, 다시 50분 뒤 C(첫 단계에서 100분이어도 앞 단계에서 50분). b: B가 70분 뒤라 빠짐


# ---------- P9 새 컬럼 상자의 줄(서버) ----------

def test_session_number_lines():
    m = lambda n: dt.datetime(2025, 1, 1, 9) + dt.timedelta(minutes=n)
    t = ibis.memtable({"u": ["a"] * 6, "t": [m(0), m(10), m(60), m(80), m(120), m(125)]})
    lines = ["lag t 1 기준 t 그룹 u 이름 _prev",
             "식 iif(isnull(_prev) or date_diff(_prev, t, 'minute') > 30, 1, 0) 이름 _new",
             "누적합 _new 기준 t 그룹 u 이름 세션"]
    assert rows_of(calc.apply(lines, t), "t", "세션") == [(m(0), 1), (m(10), 1), (m(60), 2), (m(80), 2), (m(120), 3), (m(125), 3)]


def test_date_diff_days_hours():
    t = ibis.memtable({"a": [dt.datetime(2025, 3, 28)], "b": [dt.datetime(2025, 5, 10)], "da": [dt.date(2025, 3, 28)], "db": [dt.date(2025, 5, 10)]})
    out = calc.apply(["식 date_diff(a, b, 'day') 이름 일", "식 date_diff(a, b, 'hour') 이름 시간", "식 date_diff(da, db, 'day') 이름 일2"], t).to_pyarrow().to_pylist()[0]
    assert (out["일"], out["시간"], out["일2"]) == (43, 1032, 43)


def test_prep_formulas_take_calc_lines():
    from core import prep_derive
    t = ibis.memtable(EVENTS)
    out, made = prep_derive.apply_formulas(t, [{"name": "두배", "expr": "n * 2"}, {"line": "그룹집계 count_distinct(s) 그룹 u 이름 종류수"},
                                               {"line": "누적합 두배 기준 n 그룹 u 이름 누적"}])
    assert made == ["두배", "종류수", "누적"]
    assert rows_of(out, "n", "종류수", "누적") == [(1.0, 2, 2.0), (2.0, 2, 6.0), (3.0, 2, 12.0), (4.0, 1, 8.0), (5.0, 1, 18.0), (6.0, 0, 12.0)]
    with pytest.raises(ValueError, match="계산 컬럼"):
        prep_derive.apply_formulas(t, [{"line": 3}])


def test_period_app_import_hook():
    """M1이 만드는 query/period_app.py의 라우트가 달리도록 search_app이 import한다(없으면 그냥 넘어간다)."""
    assert "from query import period_app" in (ROOT / "query/search_app.py").read_text(encoding="utf-8")


# ---------- P12 행 고르기: 튜플 조건 ----------

def test_tuple_condition():
    from core import parse
    t = ibis.memtable({"a": ["x", "x", "y", "y", None, "z,1"], "b": [1, 2, 1, 2, 1, 3]})
    n = t.mutate(i=ibis.row_number())
    pick = lambda cond: sorted(r["i"] for r in n.filter(parse.predicate(cond, n)).to_pyarrow().to_pylist())  # noqa: E731
    assert pick("(a, b) in (x, 1), (y, 2), ('z,1', 3)") == [0, 3, 5]
    assert pick("(a, b) except (x, 1), (y, 2), ('z,1', 3)") == [1, 2, 4]  # 빈 값이 있는 행(4)은 남긴다
    assert pick("(b) in (2), (3)") == [1, 3, 5]
    with pytest.raises(ValueError, match="행이 너무 많아요"):
        parse.predicate("(a, b) in " + ", ".join(f"(x, {i})" for i in range(201)), n)
    with pytest.raises(ValueError, match="2개"):
        parse.predicate("(a, b) in (x, 1, 2)", n)
    with pytest.raises(ValueError):
        parse.predicate("(a, b) in (x, 1), (y", n)


# ---------- P17 탭 = 가상 테이블(@탭), 다른 테이블에서 찾기 ----------

@pytest.fixture
def two(folder, tmp_path):
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "t.duckdb"))
    raw.execute('create table orders ("주문번호" varchar, "고객" varchar, "금액" double)')
    raw.executemany("insert into orders values (?, ?, ?)", [(f"o{i}", f"c{i % 5}", float(i + 1)) for i in range(10)])
    raw.execute('create table customers (customer_id varchar, "등급" varchar)')
    raw.executemany("insert into customers values (?, ?)", [("c0", "VIP"), ("c1", "VIP"), ("c2", "일반"), ("c9", "VIP")])
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "t.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def tab(c, name, **b):
    r = c.post("/api/tabs/register", json={"name": name, "body": {"schema": "", "joins": [], "conditions": [], **b}}).get_json()
    assert r == {"ok": True, "name": name}, r


def found_rows(c, **b):
    r = c.post("/api/query", json=body(limit=100, **b)).get_json()
    assert "error" not in r, r
    return r["count"], r["rows"]["rows"]


def test_tab_as_table(two):
    tab(two, "@vip", base="customers", conditions=["등급 = VIP"])
    assert found_rows(two, base="orders", joins=["@vip semi 고객=customer_id"])[0] == 4
    n, rows = found_rows(two, base="orders", joins=["@vip left 고객=customer_id"])
    assert n == 10 and sorted(r["등급"] or "" for r in rows) == [""] * 6 + ["VIP"] * 4
    assert found_rows(two, base="orders", joins=["@vip anti 고객=customer_id"])[0] == 6
    assert found_rows(two, base="@vip")[0] == 3
    sql = two.post("/api/sql", json=body(base="orders", joins=["@vip semi 고객=customer_id"], limit=20)).get_json()["sql"]
    assert "customers" in sql and "VIP" in sql  # 처리 용량 확인·SQL 보기가 탭의 가공까지 푼 SQL을 센다
    tab(two, "@vip", base="customers", conditions=["등급 = 일반"])  # 탭이 바뀌면 새 식
    assert found_rows(two, base="@vip")[0] == 1
    assert two.post("/api/tabs/forget", json={"name": "@vip"}).get_json() == {"ok": True}
    assert "error" in two.post("/api/query", json=body(base="@vip", limit=5)).get_json()
    assert "error" in two.post("/api/tabs/register", json={"name": "@탭 1", "body": {"base": "orders"}}).get_json()


def test_tabs_list(two):
    assert "/api/tabs" in server.FREE
    tab(two, "@목록", base="orders")
    assert "@목록" in two.get("/api/tabs").get_json()["names"]
    two.post("/api/tabs/forget", json={"name": "@목록"})
    assert "@목록" not in two.get("/api/tabs").get_json()["names"]


def test_tab_self_reference_and_depth(two):
    tab(two, "@a", base="orders", joins=["@a semi 고객=고객"])
    assert "자기 자신" in two.post("/api/query", json=body(base="@a", limit=5)).get_json()["error"]
    tab(two, "@t1", base="orders")
    for i in (2, 3, 4):
        tab(two, f"@t{i}", base=f"@t{i - 1}")
    assert found_rows(two, base="@t3")[0] == 10
    assert "3단계" in two.post("/api/query", json=body(base="@t4", limit=5)).get_json()["error"]


def test_lookup_preview(two):
    r = two.post("/api/lookup/preview", json={**body(base="orders"), "column": "고객", "target": {"schema": "", "base": "customers", "key": "customer_id"}}).get_json()
    assert r == {"values": 5, "found": 3, "missing": 2}
    tab(two, "@vip", base="customers", conditions=["등급 = VIP"])
    r = two.post("/api/lookup/preview", json={**body(base="orders"), "column": "고객", "target": {"schema": "", "base": "@vip", "key": "customer_id"}}).get_json()
    assert r == {"values": 5, "found": 2, "missing": 3}
    assert "error" in two.post("/api/lookup/preview", json={**body(base="orders"), "column": "금액", "target": {"schema": "", "base": "customers", "key": "customer_id"}}).get_json()


def test_group_aggregate_from_table(two):
    tab(two, "@big", base="orders", conditions=["금액 > 5"])
    n, rows = found_rows(two, base="customers", calcs=["그룹집계 count() 테이블 orders 키 customer_id=고객 이름 주문수",
                                                        "그룹집계 sum(금액) 테이블 @big 키 customer_id=고객 이름 큰합"])
    got = {r["customer_id"]: (r["주문수"], r["큰합"]) for r in rows}
    assert n == 4 and got == {"c0": (2, 6.0), "c1": (2, 7.0), "c2": (2, 8.0), "c9": (None, None)}
    assert "키" in two.post("/api/query", json=body(base="customers", calcs=["그룹집계 count() 테이블 orders 이름 x"], limit=5)).get_json()["error"]


# ---------- 서버 리뷰 고침 ----------

@pytest.fixture
def lite(folder, tmp_path):
    from query.search_app import app
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table e (u text, s text, n real)")
    raw.executemany("insert into e values (?, ?, ?)", [("a", "x", 1.0), ("a", None, None), ("b", "y", 3.0)])
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


@pytest.mark.parametrize("line", ['전처리 fill n {"how": "mean"}', '전처리 scale n "standard"', "전처리 label s true"])
def test_prep_line_stats_refused_on_sqlite(lite, line):
    """① 통계를 먼저 읽는 단계는 식을 만들 때 연결 스레드 밖에서 조회해 SQLite 스레드 오류가 났다: 이제 안내로 거절."""
    err = lite.post("/api/query", json=body(base="e", limit=10, calcs=[line])).get_json()["error"]
    assert "전처리 화면으로 넘겨서" in err and "thread" not in err


@pytest.fixture
def csvs(folder, tmp_path):
    from query.search_app import app
    (tmp_path / "orders.csv").write_text("id,cust,amt\n" + "".join(f"o{i},c{i % 3},{i + 1}\n" for i in range(9)), encoding="utf-8")
    (tmp_path / "cust.csv").write_text("cid,grade\nc0,A\nc1,B\nc2,A\n", encoding="utf-8")
    (tmp_path / "prices.csv").write_text("amt,memo\n1.0,xx\n2.5,axa\n", encoding="utf-8")  # amt는 실수(orders.amt는 정수)
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    files = [str(tmp_path / f) for f in ("orders.csv", "cust.csv", "prices.csv")]
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "files": files}).status_code == 200
    assert server.state["con"].name == "polars"
    yield c
    c.post("/api/disconnect", json={})


def test_nested_tab_key_and_window_tab(csvs):
    """② 계산 줄(그룹집계 테이블)에서만 쓰는 @탭이 바뀌어도 받아 둔 결과를 다시 받는다. ⑥ 파일 연결이 못 하는 창 함수가 든 탭은 안내로 거절."""
    tab(csvs, "@b", base="orders", conditions=["amt > 6"])
    tab(csvs, "@a", base="cust", calcs=["그룹집계 sum(amt) 테이블 @b 키 cid=cust 이름 big"])
    big = lambda: {r["cid"]: r["big"] for r in found_rows(csvs, base="@a", calcs=["순위 기준 cid 이름 r"])[1]}  # noqa: E731  순위는 Polars가 못 해 받아 둔 결과 위에서
    assert big() == {"c0": 7, "c1": 8, "c2": 9}
    tab(csvs, "@b", base="orders", conditions=["amt > 0"])
    assert big() == {"c0": 12, "c1": 15, "c2": 18}
    tab(csvs, "@w", base="orders", calcs=["순위 기준 amt desc 이름 r"])
    err = csvs.post("/api/query", json=body(base="cust", joins=["@w left cid=cust"], limit=5)).get_json()["error"]
    assert "다른 탭에서 쓸 수 없어요" in err


def test_prep_count_follows_inner_tab(two):
    """② 전처리 기준 @탭의 행 수는 안에서 쓰는 @탭이 바뀌면 다시 센다."""
    tab(two, "@b", base="orders", conditions=["금액 > 8"])
    tab(two, "@a", base="orders", joins=["@b semi 주문번호=주문번호"])
    rows = lambda: two.post("/api/shape", json={"schema": "", "base": "@a"}).get_json()["rows"]  # noqa: E731
    assert rows() == 2
    tab(two, "@b", base="orders", conditions=["금액 > 5"])
    assert rows() == 5


def test_funnel_window_keeps_later_candidates():
    """③ 1시간 제한: A 0:00, B 0:10, B 0:50, C 1:40 → 가장 이른 B(0:10)에서는 C가 90분이지만 B 0:50에서는 50분이라 도달."""
    from core import funnel
    m = lambda n: dt.datetime(2025, 1, 1) + dt.timedelta(minutes=n)  # noqa: E731
    t = ibis.memtable({"u": ["a"] * 4, "s": ["A", "B", "B", "C"], "t": [m(0), m(10), m(50), m(100)]})
    found = funnel.compute(t, "u", "s", "t", ["A", "B", "C"], window_seconds=3600)
    assert [s["users"] for s in found["steps"]] == [1, 1, 1]
    assert [s["median_sec"] for s in found["steps"]] == [None, 600, 3000]


def test_suffix_replace_on_polars():
    """⑤ Polars는 길이 식을 substr 인자로 못 받았다: 끝 글자 바꾸기를 정규식으로."""
    import polars as pl
    t = ibis.polars.connect().create_table("t", pl.DataFrame({"s": ["Apple pie", "apple", "a$b", None]}))
    out = prep_ops.apply_ops(t, "s", {"replace": {"how": "suffix", "pairs": [["LE", "$1!"]], "ignore_case": True}})
    assert out["s"].to_pyarrow().to_pylist() == ["Apple pie", "app$1!", "a$b", None]


def test_date_range_of_tab(shop):
    """⑧ 날짜 범위도 @탭의 가공 결과로."""
    tab(shop, "@recent", base="orders", conditions=["주문일 >= 2025-05-01"])
    assert shop.get("/api/date_range?schema=&base=@recent&column=주문일").get_json() == {"min": "2025-05-01", "max": "2025-05-10", "rows": 10}


def test_group_line_long_spaces_fast():
    """⑩ 긴 빈칸 뒤의 틀린 글자에서 정규식이 오래 되짚었다."""
    import time
    t = ibis.memtable(EVENTS)
    start = time.perf_counter()
    with pytest.raises(ValueError):
        calc.apply(["그룹집계 count()" + " " * 80 + "x"], t)
    assert time.perf_counter() - start < 0.1


def test_release_keeps_tables_used_by_tabs(two):
    """⑪ 등록한 탭이 기준·조인에 쓰는 테이블의 받아 둔 결과는 놓지 않는다(그 탭을 쓰는 다른 탭이 곧 다시 읽는다)."""
    from core import querying
    key = querying.search_key({"schema": "", "base": "orders"})
    con = server.get_con()
    server.run(lambda: held.ensure(key, con.table("orders"), threading.Event(), cap=False))
    tab(two, "@t", base="customers", joins=["orders semi customer_id=고객"])
    two.post("/api/release", json={"schema": "", "base": "orders"})
    assert key in held.store["items"]
    two.post("/api/tabs/forget", json={"name": "@t"})
    two.post("/api/release", json={"schema": "", "base": "orders"})
    assert key not in held.store["items"]


def test_lookup_preview_one_query(two, monkeypatch):
    """⑬ 값 수와 찾은 수를 조회 한 번으로(10만 상한 없음)."""
    from query import search_app
    calls = []
    real = search_app.run
    monkeypatch.setattr(search_app, "run", lambda work, *a: (calls.append(1), real(work, *a))[1])
    r = two.post("/api/lookup/preview", json={**body(base="orders"), "column": "고객", "target": {"schema": "", "base": "customers", "key": "customer_id"}}).get_json()
    assert r == {"values": 5, "found": 3, "missing": 2} and len(calls) == 1


# ---------- 서버 감사 A1~A9 ----------

def test_funnel_window_many_rows_fast():
    """A1 기간 제한이 있으면 단계 표를 (사용자, 시각)으로만: 한 사람이 A·B·C를 1,200번씩 해도 표가 제곱으로 커지지 않는다(예전 12초)."""
    import time
    from core import funnel
    t0 = dt.datetime(2026, 1, 1)
    rows = [("bot", s, t0 + dt.timedelta(seconds=3 * i + k)) for i in range(1200) for k, s in enumerate("ABC")]
    t = ibis.memtable({"u": [r[0] for r in rows], "s": [r[1] for r in rows], "t": [r[2] for r in rows]})
    start = time.perf_counter()
    found = funnel.compute(t, "u", "s", "t", ["A", "B", "C"], window_seconds=3600)
    assert [s["users"] for s in found["steps"]] == [1, 1, 1] and [s["median_sec"] for s in found["steps"]] == [None, 1, 1]
    assert time.perf_counter() - start < 3
    levels = funnel.reached(t.select("u", "s", "t"), ["A", "B", "C"], 3600)
    assert [lv.count().to_pyarrow().as_py() for lv in levels] == [1200, 1200, 1200]


def test_contains_replace_all_on_polars(csvs):
    """A2 Ibis replace는 Polars에서 첫 것만 바꿨다: 전처리 contains·함수 사전 replace 둘 다 모두 바꾼다."""
    import polars as pl
    t = ibis.polars.connect().create_table("t", pl.DataFrame({"s": ["xx", "axa", None]}))
    assert prep_ops.apply_ops(t, "s", {"replace": {"how": "contains", "pairs": [["x", "$-"]]}})["s"].to_pyarrow().to_pylist() == ["$-$-", "a$-a", None]
    n, rows = found_rows(csvs, base="prices", calcs=["식 replace(memo, 'x', '-') 이름 r"])
    assert [r["r"] for r in rows] == ["--", "a-a"]


def test_mom_whole_month_compares_whole_month():
    """A3 달 단위 기간의 mom 비교는 지난달 전체(달 끝까지)."""
    from metrics import period_compare
    prev = period_compare.previous_span
    assert {k: prev({"start": "2026-02-01", "end": "2026-02-28"}, "mom")[k] for k in ("start", "end")} == {"start": "2026-01-01", "end": "2026-01-31"}
    assert {k: prev({"start": "2026-04-01", "end": "2026-04-30"}, "mom")[k] for k in ("start", "end")} == {"start": "2026-03-01", "end": "2026-03-31"}
    assert {k: prev({"start": "2026-03-10", "end": "2026-03-20"}, "mom")[k] for k in ("start", "end")} == {"start": "2026-02-10", "end": "2026-02-20"}


def test_tab_recipe_not_saved(folder):
    """A4 @탭을 기준·계산 줄에 쓰는 전처리 구성·분석 카드는 저장하지 않는다. A8 식 줄의 글자 ' @mail'은 탭이 아니다."""
    from core import dash_store
    from core import settings
    for recipe in ({"base": "@탭1"}, {"base": "orders", "formulas": [{"line": "그룹집계 count() 테이블 @탭1 키 a=b 이름 n"}]}):
        with pytest.raises(ValueError, match="탭"):
            dash_store.check_source({"recipe": recipe, "analysis": {"kind": "busiest"}})
        with pytest.raises(ValueError, match="탭"):
            settings.save_recipe("r", recipe)
    assert not dash_store.uses_tab({"base": "orders", "calcs": ["식 concat(id, ' @mail') 이름 m"]})
    assert dash_store.uses_tab({"base": "orders", "joins": ["@a semi id=id"]}) and dash_store.uses_tab({"base": "o", "calcs": ["그룹집계 count() 테이블 @b 키 x=y 이름 n"]})


def test_release_skips_calling_tab(two):
    """A5 화면이 기준을 바꾸며 release를 부를 때 등록부에는 그 탭의 옛 검색이 남아 있다: 요청의 tab(부르는 탭)은 사용 검사에서 뺀다."""
    from core import querying
    key = querying.search_key({"schema": "", "base": "orders"})
    con = server.get_con()
    server.run(lambda: held.ensure(key, con.table("orders"), threading.Event(), cap=False))
    tab(two, "@me", base="orders")
    two.post("/api/release", json={"schema": "", "base": "orders", "tab": "@me"})
    assert key not in held.store["items"]


def test_lookup_preview_casts_key(csvs):
    """A6 정수 ↔ 실수 키도 Polars 조인에서 맞춰 본다(키를 기준 컬럼 타입으로)."""
    r = csvs.post("/api/lookup/preview", json={**body(base="orders"), "column": "amt", "target": {"schema": "", "base": "prices", "key": "amt"}}).get_json()
    assert r == {"values": 9, "found": 1, "missing": 8}


def test_counts_line_and_no_handoffs_alias():
    """A7 counts 단계는 표를 읽지 않아 쿼리 계산 줄로 된다. A9 쓰지 않는 별칭 prep_app.HANDOFFS는 없다."""
    from prep import prep_app
    out = calc.apply(['전처리 counts s {}'], ibis.memtable({"s": ["a b a", "c"]}))
    assert rows_of(out, "s_counts") == [('{"a": 2, "b": 1}',), ('{"c": 1}',)]
    assert not hasattr(prep_app, "HANDOFFS")


# ---------- 옛 화면 감사 B(서버 몫) ----------

def test_paren_column_names():
    """B3 컬럼 이름에 괄호가 있어도 값 조합 조건과 그룹집계(따옴표로 감싸도)가 된다. 따옴표가 많은 줄도 바로 거절."""
    import time
    from core import parse
    t = ibis.memtable({"id": [1, 2, 3], "u": ["a", "a", "b"], "금액(원)": [10, 20, 30]})
    n = t.mutate(i=ibis.row_number())
    assert sorted(r["id"] for r in n.filter(parse.predicate("(id, 금액(원)) except (1, 10)", n)).to_pyarrow().to_pylist()) == [2, 3]
    assert sorted(r["id"] for r in n.filter(parse.predicate("(id, 금액(원)) in (1, 10), (3, 30)", n)).to_pyarrow().to_pylist()) == [1, 3]
    for line in ("그룹집계 sum(금액(원)) 그룹 u 이름 합", '그룹집계 sum("금액(원)") 그룹 u 이름 합'):
        assert rows_of(calc.apply([line], t), "u", "합") == [("a", 30), ("a", 30), ("b", 30)]
    start = time.perf_counter()
    with pytest.raises(ValueError):
        calc.apply(["그룹집계 sum(" + "'" * 40 + "x 이름 n"], t)
    assert time.perf_counter() - start < 0.1


def test_stats_refusal_example_only_for_fill_scale():
    """B4 거절 안내의 식 줄 예시는 같은 일을 식으로 할 수 있는 fill·scale에만(이름에 괄호가 있으면 col("…"))."""
    t = ibis.memtable({"s": ["x"], "금액(원)": [1.0]})
    with pytest.raises(ValueError) as e:
        calc.apply(["전처리 label s true"], t)
    assert "식 줄" not in str(e.value)
    with pytest.raises(ValueError, match=r'std\(col\("금액\(원\)"\)\)'):
        calc.apply(['전처리 scale 금액(원) "standard"'], t)
    with pytest.raises(ValueError, match=r"coalesce\(s, mean\(s\)\)"):
        calc.apply(['전처리 fill s {"how": "mean"}'], t)


def test_bucket_with_labels():
    """B5 목록 칸이 [..] 한 덩어리면 그것만 읽어 다음 목록(구간 이름)을 줄 수 있다. 이름 수가 틀리면 안내."""
    from core import prep_derive
    t = ibis.memtable({"x": [5, 15, 25, None]})
    out, _ = prep_derive.apply_formulas(t, [{"name": "구간", "expr": "bucket(x, [10, 20], ['낮음', '중간', '높음'])"}])
    assert out["구간"].to_pyarrow().to_pylist() == ["낮음", "중간", "높음", None]
    out = calc.apply(["식 bucket(x, [10, 20]) 이름 b"], t)  # 이름 없이(예전 꼴)
    assert out["b"].to_pyarrow().to_pylist() == ["~10", "10~20", "20~", None]
    with pytest.raises(ValueError, match="하나 많아야"):
        prep_derive.apply_formulas(t, [{"name": "구간", "expr": "bucket(x, [10, 20], ['낮음', '높음'])"}])


# ---------- 서버 리뷰 6 ----------

@pytest.fixture
def keyed(folder, tmp_path):
    """기본 키(NOT NULL) 컬럼과 2^53 넘는 bigint, 스키마 s·main에 같은 이름의 orders."""
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "k.duckdb"))
    raw.execute('create table customers (customer_id varchar primary key, n bigint not null)')
    raw.execute("insert into customers values ('c0', 9007199254740992), ('c9', 1)")
    raw.execute('create table orders ("고객" varchar, big bigint)')
    raw.execute("insert into orders values ('c0', 9007199254740993), ('c1', 5)")
    raw.execute("create schema s")
    raw.execute('create table s.customers (customer_id varchar)')
    raw.execute("insert into s.customers values ('c0'), ('c1')")
    raw.execute('create table s.orders ("고객" varchar)')
    raw.execute("insert into s.orders values ('c0'), ('c0'), ('c0')")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "k.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_lookup_preview_not_null_and_big_ids(keyed):
    """리뷰 S1 NOT NULL 컬럼은 타입이 nullable만 달라 글자끼리를 timestamp로 바꾸다 실패했고, bigint를 실수로 바꿔 2^53+1이 2^53과 맞았다."""
    look = lambda column, key: keyed.post("/api/lookup/preview", json={**body(base="orders"), "column": column, "target": {"schema": "", "base": "customers", "key": key}}).get_json()  # noqa: E731
    assert look("고객", "customer_id") == {"values": 2, "found": 1, "missing": 1}
    assert look("big", "n") == {"values": 2, "found": 0, "missing": 2}


def test_group_aggregate_table_uses_schema(keyed):
    """리뷰 S2 그룹집계의 '테이블 대상'은 검색의 스키마에서(조인 줄과 같게). 예전엔 늘 기본 스키마의 orders를 읽었다."""
    n, rows = found_rows(keyed, schema="s", base="customers", calcs=["그룹집계 count() 테이블 orders 키 customer_id=고객 이름 주문수"])
    assert {r["customer_id"]: r["주문수"] for r in rows} == {"c0": 3, "c1": None}


# ---------- 2차 서버 감사 AA ----------

def test_quoted_spaces_kept_in_lines():
    """AA1 낱말로 나눈 뒤 이어 붙여 따옴표 안 연속 빈칸이 하나로 줄었다(그룹집계 조건, 식 줄 본문). 이름을 안 쓴 줄의 조건 안 '이름' 컬럼도 조건."""
    t = ibis.memtable({"매장": ["A", "A", "B"], "id": [1, 2, 3], "메모": ["a  b", "a b", "a  b"], "이름": ["김", "이", "김"]})
    out = calc.apply(["그룹집계 count() 그룹 매장 조건 메모 = 'a  b' 이름 n"], t)
    assert rows_of(out, "id", "n") == [(1, 1), (2, 1), (3, 1)]
    out = calc.apply(["그룹집계 count() 그룹 매장 조건 이름 = '김'"], t)
    assert rows_of(out, "id", "행_count") == [(1, 1), (2, 1), (3, 1)]
    out = calc.apply(["식 replace(메모, '  ', '_') 이름 r"], t)
    assert rows_of(out, "id", "r") == [(1, "a_b"), (2, "a b"), (3, "a_b")]


def test_regex_replace_same_on_duckdb_and_polars():
    """AA2 정규식 바꾸기의 \\1(묶음)·$(글자 그대로)가 Polars에서도 DuckDB와 같다: 함수 사전 regex_replace, 전처리 regex 단계, 치환 regex."""
    import polars as pl
    data = {"s": ["10 USD", "x"]}
    for t in (ibis.memtable(data), ibis.polars.connect().create_table("t", pl.DataFrame(data))):
        assert calc.apply([r"식 regex_replace(s, '(\\d+)', '#\\1') 이름 r"], t)["r"].to_pyarrow().to_pylist() == ["#10 USD", "x"]  # 식은 파이썬 글자 규칙이라 \\1로 쓴다
        assert calc.apply([r"식 regex_replace(s, 'USD', '$1') 이름 r"], t)["r"].to_pyarrow().to_pylist() == ["10 $1", "x"]
        assert prep_ops.apply_ops(t, "s", {"regex": [r"(\d+)", "$1원"]})["s"].to_pyarrow().to_pylist() == ["$1원 USD", "x"]
        assert prep_ops.apply_ops(t, "s", {"replace": {"how": "regex", "pairs": [[r"(\d+) (\w+)", r"\2 \1"]]}})["s"].to_pyarrow().to_pylist() == ["USD 10", "x"]


def test_prep_sample_follows_inner_tab(two):
    """AA3 전처리 기준 @탭의 열쇠(표본·통계·사본)는 안쪽 @탭의 검색까지 본다: 안쪽을 고치면 표본을 새로 읽는다."""
    tab(two, "@in", base="orders", conditions=["금액 > 0"])
    tab(two, "@out", base="@in")
    rows = lambda: two.post("/api/profile", json={"schema": "", "base": "@out"}).get_json()["rows"]  # noqa: E731
    assert rows() == 10
    tab(two, "@in", base="orders", conditions=["금액 > 8"])
    assert rows() == 2


def test_used_tables_one_rule():
    """AA7 검색이 테이블 자리에 쓰는 이름(기준·이어붙이기·조인 첫 낱말)은 querying.used_tables 한 곳."""
    from core import dash_store
    from core import querying
    b = {"base": "a", "union": {"tables": ["b"]}, "joins": ["c left x=y", "", "  "]}
    assert querying.used_tables(b) == ["a", "b", "c"]
    assert dash_store.uses_tab({"base": "a", "joins": ["@t semi x=x"]}) and not dash_store.uses_tab(b)


# ---------- 서버 2차 리뷰 RA ----------

def test_regex_backslash_same_on_duckdb_and_polars():
    """RA2 바꿀 글자의 \\\\(역슬래시 한 글자)도 Polars에서 DuckDB와 같다(\\\\1은 글자 \\1)."""
    import polars as pl
    data = {"s": ["a/b", "x"]}
    for t in (ibis.memtable(data), ibis.polars.connect().create_table("t", pl.DataFrame(data))):
        assert prep_ops.apply_ops(t, "s", {"regex": ["(/)", "\\\\"]})["s"].to_pyarrow().to_pylist() == ["a\\b", "x"]
        assert prep_ops.apply_ops(t, "s", {"regex": ["(/)", "\\\\1"]})["s"].to_pyarrow().to_pylist() == ["a\\1b", "x"]
        assert prep_ops.apply_ops(t, "s", {"regex": ["(/)", "C:\\\\x\\1"]})["s"].to_pyarrow().to_pylist() == ["aC:\\x/b", "x"]


def test_group_order_desc_not_taken_as_name():
    """RA3 '기준 이름 desc'에서 새 이름을 생략하면 desc를 새 이름으로 읽어 그룹 컬럼 오류가 났다."""
    t = ibis.memtable({"매장": ["A", "A", "B"], "이름": ["가", "나", "다"], "금액": [1, 2, 3]})
    out = calc.apply(["그룹집계 first(금액) 그룹 매장 기준 이름 desc"], t)
    assert rows_of(out, "매장", "금액_first") == [("A", 2), ("A", 2), ("B", 3)]


def test_my_metric_on_tab_not_saved(two):
    """확인: 내 지표가 @탭(메모리에만 있는 등록)에 속하면 앱을 다시 켜면 풀 수 없어 저장하지 않는다(검색 저장과 같은 규칙)."""
    from core.dash_store import current_source
    from metrics import my_metrics
    with pytest.raises(ValueError, match="탭"):
        my_metrics.create({"name": "n", "scope": {"key": current_source()["key"], "table": "@t"}, "template": "m02", "slots": {"대상": {"column": "고객"}}}, {"고객": "text"})


def test_ubigint_max_in_conditions():
    """UBIGINT 최댓값처럼 int64 밖의 정수는 타입 없는 리터럴이면 Ibis가 비교를 FALSE로 접어 0행이었다: 컬럼 타입의 리터럴로. 타입 범위도 넘으면 안내."""
    from core import parse
    con = ibis.duckdb.connect()
    con.raw_sql("create table t as select 1 as id, 18446744073709551615::ubigint as u union all select 2, 5::ubigint")
    t = con.table("t")
    pick = lambda cond: sorted(r["id"] for r in t.filter(parse.predicate(cond, t)).to_pyarrow().to_pylist())  # noqa: E731
    assert pick("u = 18446744073709551615") == [1] and pick("(id, u) in (1, 18446744073709551615)") == [1]
    assert pick("u in 5, 18446744073709551615") == [1, 2] and pick("u != 18446744073709551615") == [2]
    with pytest.raises(ValueError, match="정수 값이 아니에요"):
        parse.predicate("u = 18446744073709551616", t)
