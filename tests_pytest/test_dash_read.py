"""최신화는 필요한 것만 읽는다(8-4): 카드가 쓰는 컬럼만 SQL에 나가고, 기간 조건(직전 기간 시작 ~ 끝)이 집계 전에 걸리며, 집계는 DB에서 하고, PC 사본도 쓰는 컬럼만 받고,
같은 테이블·조건의 카드는 한 번에 읽고, 처리 용량 추정이 테이블 전체보다 작다. 실제 DuckDB 파일에 연결해 보낸 SQL 글을 본다. 데이터는 테스트 안에서 지어낸다."""
import datetime as dt

import duckdb
import pytest
from lazy import ibis

import core
import dash_calc
import dash_store
import held
import scan_size
import server
import source

RANGE = {"range": {"start": "2026-01-12", "end": "2026-02-02"}, "prev": {"start": "2025-12-21", "end": "2026-01-11", "text": "직전 22일"}}
WINDOW = {"start": "2025-12-21", "end": "2026-02-02"}  # 직전 기간 시작 ~ 보는 기간 끝
UNUSED = ("memo", "blob", "uid", "region")  # 카드가 쓰지 않는 컬럼
START, END = "MAKE_DATE(2025, 12, 21)", "MAKE_DATE(2026, 2, 2)"  # 읽을 범위(직전 기간 시작 ~ 보는 기간 끝)가 SQL에 나가는 모양


@pytest.fixture
def wide(folder, tmp_path):
    import dash_app  # noqa: F401
    import prep_app  # noqa: F401
    from app import app
    raw = duckdb.connect(str(tmp_path / "w.duckdb"))
    raw.execute("create table wide (day date, store varchar, amt double, uid varchar, memo varchar, blob varchar, region varchar)")
    days = [dt.date(2025, 6, 1), dt.date(2026, 1, 5), dt.date(2026, 1, 12), dt.date(2026, 1, 20), dt.date(2026, 2, 2), dt.date(2026, 3, 1)]
    raw.executemany("insert into wide values (?, ?, ?, ?, ?, ?, ?)", [(d, "AB"[i % 2], float(10 + i), f"u{i}", "메모" * 20, "x" * 50, "서울") for i, d in enumerate(days)])
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "w.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


@pytest.fixture
def sent(monkeypatch):
    """실행하려고 연결에 넘기는 식들의 SQL 글을 모은다."""
    from ibis.expr.types.relations import Table
    found = []

    def spy(name):
        original = getattr(Table, name)

        def wrapped(self, *args, **kwargs):
            found.append(str(ibis.to_sql(self)))
            return original(self, *args, **kwargs)
        monkeypatch.setattr(Table, name, wrapped)
    spy("to_polars")
    spy("to_pyarrow")
    return found


def card(kind, series, date="day", view=None, **body):
    source_ = {"table": "wide", "body": {"base": "wide", "series": series, **body}, **({"date": date} if date else {})}
    return dash_store.check_card({"kind": kind, "title": "t", "source": source_, "view": view or {}})


def test_time_card_reads_only_used_columns_and_the_period_window(wide, sent):
    data = dash_calc.compute(card("kpi", [{"y": "amt", "agg": "sum"}]), "2026-10-22T09:00:00", RANGE)
    labels = data["by_unit"]["day"]["labels"]
    assert data["window"] == WINDOW and labels == ["2026-01-05", "2026-01-12", "2026-01-20", "2026-02-02"]  # 2025-06-01과 2026-03-01은 읽지 않는다
    assert sent and all(not any(u in sql for u in UNUSED) for sql in sent), sent  # 쓰지 않는 컬럼은 SQL에 없다
    assert all(START in sql and END in sql for sql in sent) and all("GROUP BY" in sql for sql in sent)  # 기간 조건이 집계 전에 걸리고 집계는 DB에서
    assert not any("SELECT *" in sql for sql in sent)


def test_conditions_are_applied_before_aggregating(wide, sent):
    c = card("kpi", [{"y": "amt", "agg": "sum"}], conditions=["store = A"])
    data = dash_calc.compute(c, "2026-10-22T09:00:00", RANGE)
    assert data["by_unit"]["day"]["labels"] == ["2026-01-12", "2026-02-02"]  # A 매장(짝수 번째) 행만
    assert all("'A'" in sql and not any(u in sql for u in ("memo", "blob")) for sql in sent)


def test_category_chart_and_summary_read_only_their_columns(wide, sent):
    dash_calc.compute(card("chart", [{"y": "amt", "agg": "sum"}], date=None, x="store"), "2026-10-22T09:00:00", RANGE)
    dash_calc.compute(card("summary", [{"y": "amt", "agg": "sum"}], view={"group": "store"}), "2026-10-22T09:00:00", RANGE)
    assert sent and all(not any(u in sql for u in UNUSED) for sql in sent)
    assert any("MAKE_DATE(2026, 1, 12)" in sql for sql in sent)  # 요약은 보는 기간으로 걸린다


def test_cards_sharing_table_and_conditions_are_read_once(wide, sent):
    a, b = card("kpi", [{"y": "amt", "agg": "sum"}]), card("kpi", [{"y": "amt", "agg": "mean"}])
    other = card("kpi", [{"y": "amt", "agg": "sum"}], conditions=["store = A"])
    plan = dash_calc.plan_shared([a, b, other], RANGE)
    assert len(plan["series"]) == 2 and sorted(len(v) for v in plan["series"].values()) == [1, 2]  # 같은 조건의 두 카드는 계열을 합쳐 한 번에
    ctx = {**RANGE, "plan": plan, "shared": {}}
    got = [dash_calc.compute(c, "2026-10-22T09:00:00", ctx)["by_unit"]["day"]["series"] for c in (a, b, other)]
    assert [[s["name"] for s in series] for series in got] == [["합계 (amt)"], ["평균 (amt)"], ["합계 (amt)"]]  # 카드마다 자기 계열만
    days = [sql for sql in sent if "GROUP BY" in sql]
    assert len(days) == 2  # 묶음 하나(두 카드)와 다른 조건 하나 = 집계 쿼리 2번(묶지 않으면 3번)
    assert "SUM" in days[0] and not any(u in sql for sql in days for u in UNUSED)  # 평균은 합계·개수로 화면이 구하므로 한 집계에 둘 다 들어 있다
    alone = dash_calc.compute(a, "2026-10-22T09:00:00", RANGE)
    assert alone["by_unit"]["day"]["series"][0]["sum"] == got[0][0]["sum"]  # 묶어 읽어도 값은 같다


def test_local_copy_takes_only_the_columns_and_rows_needed(wide, monkeypatch):
    kept = []

    def ensure(key, expr, cancel, **more):
        kept.append((key, list(expr.columns)))
        return None, 0
    monkeypatch.setattr(held, "ensure", ensure)
    monkeypatch.setattr(source, "compiles", lambda expr: False)  # 이 연결이 못 하는 계산: PC 사본이 필요
    body = {"base": "wide", "conditions": ["store = A"]}
    for keep in (["day", "amt"], None):
        with pytest.raises(ValueError, match="받지 않았습니다"):
            core.runnable(body, select=False, keep=keep)
    assert kept[0][1] == ["day", "amt"] and kept[1][1] == ["day", "store", "amt", "uid", "memo", "blob", "region"]  # 쓰는 컬럼만 / 줄이지 않으면 전체
    assert kept[0][0] != kept[1][0] and core.search_key(body, ["amt", "day"]) == core.search_key(body, ["day", "amt"])  # 컬럼이 다르면 다른 사본, 순서는 상관없음
    with pytest.raises(ValueError):
        core.runnable({**body, "calcs": ["순위 기준 amt desc"]}, select=False, keep=["day"])
    assert kept[2][1][:2] == ["day", "store"]  # 계산 줄이 있으면 줄이지 않는다


def test_scan_estimate_is_smaller_than_the_whole_table(wide):
    con, opened = server.state["con"], server.state["opened"]
    c = card("kpi", [{"y": "amt", "agg": "sum"}])
    sql = dash_calc.read_sql(c, RANGE)
    slim = scan_size.estimate(con, sql, opened)
    full = scan_size.estimate(con, "SELECT * FROM wide", opened)
    assert slim["bytes"] and full["bytes"] and slim["bytes"] < full["bytes"]
    assert START in sql and not any(u in sql for u in UNUSED) and dash_calc.read_sql(card("recent_rows", []), RANGE) is None


def test_columns_used_by_each_kind(wide):
    status = card("status", [], date=None, view={"entity": "store", "time_col": "day", "rules": [{"column": "amt", "op": ">", "value": 1, "level": "warn"}]})
    assert dash_calc.card_columns(status) == ["amt", "day", "store"]  # 상태 규칙이 보는 컬럼도 읽는다
    assert dash_calc.card_columns(card("table", [], date=None)) is None and dash_calc.card_columns(card("recent_rows", [], date=None)) is None
    assert dash_calc.card_columns(card("kpi", [{"y": "amt", "agg": "sum"}], conditions=["store = A"])) == ["amt", "day"]  # 조건 컬럼은 거르기에만 쓰고 읽지 않는다
    assert dash_calc.body_columns({"series": [{"y": "amt"}], "calcs": ["x"]}) is None and dash_calc.body_columns({"names": ["지표"]}) is None
    assert dash_calc.fetch_window(RANGE) == WINDOW and dash_calc.fetch_window(None) is None and dash_calc.fetch_window({"range": {"start": None, "end": "2026-03-01"}}) == {"start": None, "end": "2026-03-01"}
