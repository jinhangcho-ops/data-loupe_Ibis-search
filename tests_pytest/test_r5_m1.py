"""R5 M1 지표 묶음: 비중 % 열(탐색 틀 51·52·53), P3 기간별 표(metrics/period_table, POST /api/period_table), P13 지표 기간·비교(POST /api/metrics/run의 period·compare).
진짜 DuckDB에 시험 안에서 만든 표만 쓴다."""
import duckdb
import pytest
from lazy import ibis

from core import held
from core import server
from core import settings
from metrics import period_compare
from metrics import period_table
from query import period_app  # noqa: F401  라우트는 Flask 앱이 첫 요청을 받기 전에 올려 둔다
from query import sentence_exec as ex

COLS = {"rid": "num", "uid": "text", "target": "text", "ts": "date"}


@pytest.fixture
def c(folder, tmp_path):
    """rep(신고: u1 1번 · u2 2번 · u3 3번 · u4 5번 · u5 12번 · u6 25번, uid가 빈 행 1개), users(u0~u9), votes(u1 4행 · u2 1행 · u3 3행, uid가 빈 행 1개)."""
    from query.search_app import app
    path = tmp_path / "x.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table users as select 'u' || i as id from range(10) t(i)")
    raw.execute("""create table rep as select row_number() over () as rid, uid from (
                   select 'u' || k as uid from (values (1, 1), (2, 2), (3, 3), (4, 5), (5, 12), (6, 25)) v(k, n), range(n) r(j)
                   union all select null)""")
    raw.execute("""create table votes as select row_number() over () as rid, uid, target, ts::date as ts from (values
                   ('u1', 'a', '2026-01-01'), ('u1', 'a', '2026-01-01'), ('u1', 'b', '2026-01-01'), ('u1', 'a', '2026-01-02'),
                   ('u2', 'c', '2026-01-01'), ('u3', 'a', '2026-01-01'), ('u3', 'b', '2026-01-02'), ('u3', 'c', '2026-01-03'),
                   (null, 'a', '2026-01-01')) v(uid, target, ts)""")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def go(tid, slots, base):
    body = {"base": base, "joins": [], "periods": [], "conditions": [], "calcs": []}
    return ex.execute_compiled(ex.prepare(tid, slots, body, COLS))


# ---------- 비중 % ----------

def test_e51_share_column_with_entity_table(c):
    done = go("e51", {"대상": "uid", "경계": "1, 2, 5, 10, 20", "대상 테이블": "users", "대상 테이블 컬럼": "id"}, "rep")
    assert done["columns"] == ["횟수", "대상 수", "비중 %"]
    assert [r["대상 수"] for r in done["rows"]] == [4, 1, 2, 1, 1, 1]
    assert [r["비중 %"] for r in done["rows"]] == pytest.approx([40, 10, 20, 10, 10, 10])


def test_e51_share_zero_when_no_entity_and_sums_to_100(c):
    done = go("e51", {"대상": "uid", "경계": "1, 2, 5, 10, 20, 30", "단위 글자": "건"}, "rep")
    shares = [r["비중 %"] for r in done["rows"]]
    assert shares[-1] == 0 and sum(shares) == pytest.approx(100) and shares[0] == pytest.approx(100 / 6)


def test_e52_share_is_entity_rows_over_all_entities_not_only_shown(c):
    done = go("e52", {"대상": "uid", "날짜": "ts", "정렬": "활동일", "N": 2}, "votes")
    assert done["columns"] == ["uid", "행 수", "비중 %", "활동일", "하루 평균", "하루 최대"]
    assert [(r["uid"], r["행 수"], r["비중 %"]) for r in done["rows"]] == [("u3", 3, pytest.approx(37.5)), ("u1", 4, pytest.approx(50))]  # u2(12.5)는 N=2 밖이어도 분모에 든다


def test_e53_share_only_for_counts(c):
    done = go("e53", {"대상": "uid", "조건": "target = c"}, "votes")
    assert done["columns"] == ["지표", "전체 uid", "조건에 맞는 대상", "비중 %", "p-value(집단 vs 나머지)"]
    got = {r["지표"]: r["비중 %"] for r in done["rows"]}
    assert got["uid 수"] == pytest.approx(200 / 3) and got["전체 행 수"] == pytest.approx(50)  # 집단 2/3명, 8행 중 4행
    assert got["대상당 평균"] is None and got["대상당 최대"] is None


# ---------- P3 기간별 표 ----------

@pytest.fixture
def shop(client, tmp_path):
    """orders(d 날짜, kind, amt, uid), visits(ts 날짜시각), span(2020·2026 두 날), wide(종류 25개)를 가진 DuckDB 파일에 연결한 클라이언트."""
    path = tmp_path / "shop.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("""create table orders as select d::date as d, kind, amt, uid from (values
                   ('2026-01-05', 'a', 100, 'u1'), ('2026-01-20', 'a', 200, 'u1'), ('2026-01-25', 'b', 50, 'u2'),
                   ('2026-03-03', 'a', 300, 'u3'), ('2026-03-30', 'b', 10, 'u3'), (null, 'a', 1, 'u9')) v(d, kind, amt, uid)""")
    raw.execute("""create table visits as select ts::timestamp as ts from (values ('2026-01-02 10:00'), ('2026-01-03 10:00'), ('2026-01-04 10:00'),
                   ('2026-01-05 10:00'), ('2026-01-06 10:00'), ('2026-02-10 10:00'), ('2026-04-01 10:00')) v(ts)""")
    raw.execute("create table span as select d::date as d from (values ('2020-01-01'), ('2026-01-01')) v(d)")
    raw.execute("create table wide as select date '2026-01-01' as d, 'k' || i as kind from range(25) t(i)")
    raw.close()
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    return client


def spec(*tables, **rest):
    return {"unit": "month", **rest, "tables": list(tables)}


ORDERS = {"base": "orders", "date": "d", "label": "주문", "how": "count"}
VISITS = {"base": "visits", "date": "ts", "label": "방문", "how": "count"}
SALES = {"base": "orders", "date": "d", "label": "매출", "how": "sum", "column": "amt"}
USERS = {"base": "orders", "date": "d", "label": "고객", "how": "count_distinct", "column": "uid"}


def test_period_table_fills_empty_months_with_zero_in_input_order(shop):
    got = period_table.build(spec(ORDERS, VISITS, SALES, USERS))
    assert got["columns"] == ["기간", "주문", "방문", "매출", "고객"]
    assert got["rows"] == [["2026-01", 3, 5, 350, 2], ["2026-02", 0, 1, 0, 0], ["2026-03", 2, 0, 310, 1], ["2026-04", 0, 1, 0, 0]]  # 날짜가 빈 행은 세지 않는다
    assert period_table.build(spec(VISITS, ORDERS))["columns"] == ["기간", "방문", "주문"]


def test_period_table_ratios(shop):
    ratios = period_table.build(spec(ORDERS, VISITS))["ratios"]
    assert ratios["share"]["주문"] == pytest.approx([60, 0, 40, 0])
    assert ratios["share"]["방문"] == [71.43, 14.29, 0, 14.29]  # 소수 둘째 자리까지
    assert ratios["change"]["주문"] == [None, -100, None, -100]  # 첫 기간과 이전이 0인 기간은 빈 값
    assert ratios["change"]["방문"] == [None, -80, -100, None]


def test_period_table_split_makes_a_column_per_value(shop):
    got = period_table.build(spec({**ORDERS, "split": "kind"}))
    assert got["columns"] == ["기간", "주문 · a", "주문 · b"]
    assert got["rows"] == [["2026-01", 2, 1], ["2026-02", 0, 0], ["2026-03", 1, 1]]
    assert set(got["ratios"]["share"]) == {"주문 · a", "주문 · b"}
    with pytest.raises(ValueError, match="20개"):
        period_table.build(spec({"base": "wide", "date": "d", "label": "w", "how": "count", "split": "kind"}))


def test_period_table_start_end_filter_dates_and_timestamps(shop):
    got = period_table.build(spec(ORDERS, VISITS, start="2026-02-01", end="2026-04-01"))
    assert got["rows"] == [["2026-02", 0, 1], ["2026-03", 2, 0], ["2026-04", 0, 1]]  # 날짜시각 컬럼의 끝 날짜(04-01 10:00)는 그날 끝까지 든다
    assert period_table.build(spec(VISITS, end="2026-03-31"))["rows"] == [["2026-01", 5], ["2026-02", 1]]


def test_period_table_week_quarter_year_units(shop, monkeypatch):
    real = settings.load
    monday = period_table.build(spec(ORDERS, unit="week"))["rows"]
    assert monday[:3] == [["2026-01-05", 1], ["2026-01-12", 0], ["2026-01-19", 2]] and monday[-1] == ["2026-03-30", 1] and len(monday) == 13
    monkeypatch.setattr(settings, "load", lambda: {**real(), "week_start": "sun"})
    sunday = period_table.build(spec(ORDERS, unit="week"))["rows"]
    assert sunday[:4] == [["2026-01-04", 1], ["2026-01-11", 0], ["2026-01-18", 1], ["2026-01-25", 1]]
    assert period_table.build(spec(ORDERS, unit="quarter"))["rows"] == [["2026-Q1", 5]]
    assert period_table.build(spec(ORDERS, VISITS, unit="year"))["rows"] == [["2026", 5, 7]]
    monkeypatch.setattr(settings, "load", lambda: {**real(), "fiscal_start_month": 4})
    assert period_table.build(spec(ORDERS, VISITS, unit="year"))["rows"] == [["2025-04-01", 5, 6], ["2026-04-01", 0, 1]]  # 회계연도는 시작일로 부른다


def test_period_table_guards(shop):
    big = {"base": "span", "date": "d", "label": "s", "how": "count"}
    with pytest.raises(ValueError, match="기간을 줄이거나 단위를 키우세요"):
        period_table.build(spec(big, unit="day"))
    assert len(period_table.build(spec(big))["rows"]) == 73  # 월 단위는 괜찮다
    with pytest.raises(ValueError, match="8개"):
        period_table.build(spec(*[{**ORDERS, "label": f"t{i}"} for i in range(9)]))
    for table, message in [({**ORDERS, "how": "avg"}, "집계"), ({**ORDERS, "how": "sum"}, "컬럼"), ({**SALES, "column": "kind"}, "숫자"), ({**SALES, "column": "nope"}, "nope"),
                           ({**ORDERS, "date": "nope"}, "nope"), ({**ORDERS, "date": "kind"}, "날짜"), ({**ORDERS, "base": "nope"}, "nope"), ({**ORDERS, "split": "nope"}, "nope")]:
        with pytest.raises(ValueError, match=message):
            period_table.build(spec(table))
    assert period_table.build(spec({**ORDERS, "label": ""}))["columns"] == ["기간", "orders"]  # 라벨을 비우면 테이블 이름
    with pytest.raises(ValueError, match="겹"):
        period_table.build(spec(ORDERS, {**VISITS, "label": "주문"}))
    with pytest.raises(ValueError, match="단위"):
        period_table.build(spec(ORDERS, unit="hour"))
    with pytest.raises(ValueError, match="테이블"):
        period_table.build({"unit": "month", "tables": []})
    with pytest.raises(ValueError):
        period_table.build(spec(ORDERS, start="2026-13-45"))


def test_period_table_empty_result_is_empty_rows(shop):
    got = period_table.build(spec(ORDERS, start="2030-01-01"))
    assert got["columns"] == ["기간", "주문"] and got["rows"] == [] and got["ratios"] == {"share": {"주문": []}, "change": {"주문": []}}


def test_period_table_route(shop):
    got = shop.post("/api/period_table", json=spec(ORDERS, VISITS)).get_json()
    assert got["columns"] == ["기간", "주문", "방문"] and got["rows"][0] == ["2026-01", 3, 5] and set(got["ratios"]) == {"share", "change"}
    bad = shop.post("/api/period_table", json=spec(ORDERS, unit="hour"))
    assert bad.status_code == 400 and "단위" in bad.get_json()["error"]


def test_period_table_remote_connection_asks_scan_gate_per_table(shop, monkeypatch):
    seen = []
    monkeypatch.setattr(held, "remote", lambda con: True)

    def gate(body, make_sql, rows=None):
        seen.append(make_sql()[0])
        return period_app.app.response_class("확인", 200) if len(seen) == 2 else None
    monkeypatch.setattr(server, "scan_gate", gate)
    r = shop.post("/api/period_table", json=spec(ORDERS, VISITS))
    assert r.get_data(as_text=True) == "확인" and len(seen) == 2 and "orders" in seen[0] and "visits" in seen[1]  # 둘째 테이블에서 멈추면 값은 구하지 않는다
    assert all("GROUP BY" in sql.upper() for sql in seen)
    seen.clear()
    monkeypatch.setattr(server, "scan_gate", lambda body, make_sql, rows=None: seen.append(make_sql()[0]))
    assert shop.post("/api/period_table", json=spec(ORDERS)).get_json()["rows"][0] == ["2026-01", 3] and len(seen) == 1


# ---------- P13 지표 기간·비교 ----------

METRICS = """
metrics:
  매출:
    format: number
    numerator: {agg: sum, column: amount}
  건수:
    numerator: {agg: count}
  객단가:
    numerator: {agg: sum, column: amount}
    denominator: {agg: count}
"""


@pytest.fixture
def events(client, tmp_path):
    """events(d 날짜, kind, amount): 2025-03 1건(50), 2026-02 2건(300), 2026-03 3건(a 400 · b 100 · a 100, 03-05 · 03-10 · 03-20). 지표 파일을 골라 둔 클라이언트."""
    path = tmp_path / "ev.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("""create table events as select d::date as d, kind, amount from (values
                   ('2025-03-15', 'a', 50), ('2026-02-10', 'a', 100), ('2026-02-20', 'c', 200),
                   ('2026-03-05', 'a', 400), ('2026-03-10', 'b', 100), ('2026-03-20', 'a', 100)) v(d, kind, amount)""")
    raw.execute("create table twin as select d, d as e, amount from events")
    raw.close()
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    (tmp_path / "metrics.yaml").write_text(METRICS, encoding="utf-8")
    assert client.post("/api/metrics", json={"path": str(tmp_path / "metrics.yaml")}).status_code == 200
    return client


def run(client, **body):
    return client.post("/api/metrics/run", json={"base": "events", **body})


MARCH = {"start": "2026-03-01", "end": "2026-03-31"}


def test_metrics_run_unchanged_without_period_or_compare(events):
    got = run(events, names=["매출"]).get_json()
    assert got == {"labels": None, "values": {"매출": [950]}, "formats": {"매출": "number"}, "more": False, "max_groups": 50}


def test_metrics_run_period_only(events):
    got = run(events, names=["매출", "건수"], period=MARCH).get_json()
    assert got["values"] == {"매출": [600], "건수": [3]} and "changes" not in got and "compare" not in got
    assert got["period"] == {"column": "d", "start": "2026-03-01", "end": "2026-03-31"}
    assert run(events, names=["건수"], period={"start": "2026-03-10"}).get_json()["values"] == {"건수": [2]}  # 열린 범위


def test_metrics_run_period_replaces_search_period_on_same_column(events):
    got = run(events, names=["건수"], periods=["d 2026-02-01 ~ 2026-02-28"], period=MARCH).get_json()
    assert got["values"] == {"건수": [3]}


@pytest.mark.parametrize("compare, period, label, previous", [
    ("prev", MARCH, ("2026-02-01", "2026-02-28"), 300),
    ("yoy", MARCH, ("2025-03-01", "2025-03-31"), 50),
    ("wow", {"start": "2026-03-09", "end": "2026-03-15"}, ("2026-03-02", "2026-03-08"), 400),
    ("mom", {"start": "2026-03-10", "end": "2026-03-20"}, ("2026-02-10", "2026-02-20"), 300),
    ("same_weekday", {"start": "2026-03-09", "end": "2026-03-22"}, ("2026-02-23", "2026-03-08"), 400),
])
def test_metrics_run_compare_modes(events, compare, period, label, previous):
    got = run(events, names=["매출"], period=period, compare=compare).get_json()
    value = got["values"]["매출"][0]
    assert got["changes"]["매출"] == [{"value": value, "previous": previous, "delta": value - previous, "delta_pct": pytest.approx((value - previous) / previous * 100)}]
    assert (got["compare"]["start"], got["compare"]["end"], got["compare"]["mode"]) == (*label, compare)


def test_metrics_run_compare_zero_previous_and_ratio_metric(events):
    feb = {"start": "2026-02-01", "end": "2026-02-28"}
    got = run(events, names=["건수", "객단가"], period=feb, compare="prev").get_json()
    assert got["changes"]["건수"] == [{"value": 2, "previous": 0, "delta": 2, "delta_pct": None}]  # 이전이 0이면 퍼센트는 빈 값
    assert got["changes"]["객단가"] == [{"value": 150, "previous": None, "delta": None, "delta_pct": None}]  # 분모 0
    got = run(events, names=["건수"], period={"start": "2026-04-01", "end": "2026-04-30"}, compare="prev").get_json()
    assert got["changes"]["건수"] == [{"value": 0, "previous": 3, "delta": -3, "delta_pct": -100.0}]


def test_metrics_run_compare_by_group_matches_previous_by_label(events):
    got = run(events, names=["매출"], by="kind", period=MARCH, compare="prev").get_json()
    assert got["labels"] == ["a", "b"] and got["values"]["매출"] == [500, 100]
    assert got["changes"]["매출"] == [{"value": 500, "previous": 100, "delta": 400, "delta_pct": 400.0}, {"value": 100, "previous": None, "delta": None, "delta_pct": None}]


def test_metrics_run_period_errors(events):
    cases = [({"compare": "prev"}, "기간"), ({"period": MARCH, "compare": "zzz"}, "비교"), ({"period": {"start": "2026-03-31", "end": "2026-03-01"}}, "시작"),
             ({"period": {"start": "2026-03-01"}, "compare": "prev"}, "시작과 끝"), ({"period": {"start": "x", "end": "2026-03-31"}}, "YYYY-MM-DD"),
             ({"period": {**MARCH, "column": "nope"}}, "nope")]
    for extra, message in cases:
        r = run(events, names=["건수"], **extra)
        assert r.status_code == 400 and message in r.get_json()["error"], extra
    r = events.post("/api/metrics/run", json={"base": "twin", "names": ["건수"], "period": MARCH})  # 날짜 컬럼이 둘이면 고르게 한다
    assert r.status_code == 400 and "column" in r.get_json()["error"]
    assert events.post("/api/metrics/run", json={"base": "twin", "names": ["건수"], "period": {**MARCH, "column": "e"}}).status_code == 200


def test_previous_span_mom_and_wow_keep_length_and_clamp_month_end():
    mom = period_compare.previous_span({"start": "2026-03-29", "end": "2026-03-31"}, "mom")
    assert (mom["start"], mom["end"]) == ("2026-02-28", "2026-02-28")  # 시작·끝을 각각 한 달 앞으로, 없는 날은 달 끝으로
    whole = period_compare.previous_span({"start": "2026-03-01", "end": "2026-03-31"}, "mom")
    assert (whole["start"], whole["end"]) == ("2026-02-01", "2026-02-28")  # 한 달 전 달 전체
    wow = period_compare.previous_span({"start": "2026-03-09 09:00", "end": "2026-03-15 18:00"}, "wow")
    assert (wow["start"], wow["end"]) == ("2026-03-02 09:00", "2026-03-08 18:00")
    assert period_compare.previous_span({"start": "2026-03-01", "end": "2026-03-31"}, "prev")["start"] == "2026-02-01"


def test_period_table_change_uses_absolute_previous():
    got = period_table.ratios_of(["x"], [["a", -100], ["b", -50], ["c", 50], ["d", 100]])
    assert got["change"]["x"] == [None, 50, 200, 100]  # -100 → -50은 +50%(이전의 크기로 나눈다)
