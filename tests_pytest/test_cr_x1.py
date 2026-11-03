"""X1: 서버·설치 쪽 리뷰·감사 수정. 고정 오프셋 시간대(+09:00) 묶기, @이름 보낸 검색의 처리 용량 확인, 설치 web/out, 주 시작·회계연도 by_unit,
wait_turn 제외 경로, data 없는 대시보드 저장, make_table 다시 열기 실패, fill_periods 한 벌, prep.html 옛 계산 스크립트, TSV BOM 등."""
import datetime as dt
import pathlib

import polars as pl
import pytest
from lazy import ibis

import test_cr_e2 as e2

remote, bigquery = e2.remote, e2.bigquery  # 가짜 BigQuery 연결(dry run만 흉내)
ROOT = pathlib.Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
AWARE = pl.DataFrame({"t": [dt.datetime(2026, 1, 1, 20, 0, tzinfo=UTC)], "v": [1]})  # 서울(+9)로는 1월 2일


@pytest.fixture(params=["+09:00", "Asia/Seoul"])
def zone(request, monkeypatch):
    from core import settings
    monkeypatch.setattr(settings, "load", lambda: {"timezone": request.param})
    return request.param


@pytest.fixture(params=["memory", "polars"])
def table(request):
    return ibis.memtable(AWARE.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", AWARE)


# ---------- 1. 고정 오프셋 시간대도 묶는다 ----------

def test_chart_day_unit_in_fixed_offset_and_iana(zone, table):
    from core import result
    labels, data, _ = result.get_chart(table, "t", [{"y": "v", "agg": "count"}], x_unit="day")
    assert [str(v)[:10] for v in labels] == ["2026-01-02"] and data[0]["values"] == [1]


def test_local_start_and_day_index(zone, table):
    from analysis import stats
    from metrics import metric_calc
    got = table.select(b=stats.local_start(table.t, "D"), d=metric_calc.day_index(table.t)).to_pyarrow().to_pylist()[0]
    assert str(got["b"])[:10] == "2026-01-02" and got["d"] == (dt.date(2026, 1, 2) - dt.date(1970, 1, 1)).days


# ---------- 2. @이름 보낸 검색도 BigQuery면 먼저 묻는다 ----------

def test_handoff_and_prep_reads_ask_on_bigquery(bigquery, remote):
    from prep import prep_app  # noqa: F401  전처리 라우트를 올린다
    search = {"base": "orders"}
    r = remote.post("/api/handoff", json={**search, "mode": "query", "name": "원격"})
    assert r.headers.get("X-Needs-Confirm") == "1", r.get_json()
    base = remote.post("/api/handoff", json={**search, "mode": "query", "name": "원격", "confirmed": True}).get_json()["base"]
    for api in ("/api/shape", "/api/profile"):
        r = remote.post(api, json={"schema": "", "base": base})
        assert r.headers.get("X-Needs-Confirm") == "1", (api, r.get_json())
    assert remote.post("/api/shape", json={"schema": "", "base": base, "confirmed": True}).status_code == 200
    assert bigquery.runs == []


# ---------- 4. 일요일 시작 주·4월 회계연도는 서버가 주·연을 구한다 ----------

@pytest.mark.parametrize("engine", ["memory", "polars"])
def test_time_data_week_and_fiscal_year_on_server(monkeypatch, engine):
    from core import settings
    from dashboard import dash_calc
    monkeypatch.setattr(settings, "load", lambda: {"week_start": "sun", "fiscal_start_month": 4})
    df = pl.DataFrame({"day": [dt.date(2026, 1, 3), dt.date(2026, 1, 4), dt.date(2026, 4, 1)], "amt": [1, 2, 4]})
    t = ibis.memtable(df.to_arrow()) if engine == "memory" else ibis.polars.connect().create_table("t", df)
    # 합계처럼 다시 묶을 수 있는 집계는 설정과 상관없이 일 값만 준다: 주 시작·회계연도는 화면이 상수(constants)로 받아 묶는다(서버가 또 구하면 조회가 늘고 설정을 바꿔도 낡은 값이 남는다)
    assert set(dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}], None)["by_unit"]) == {"day"}
    by_unit = dash_calc.time_data(t, "day", [{"y": "amt", "agg": "nunique"}], None)["by_unit"]  # 다시 묶을 수 없는 집계만 서버가 설정대로 구한다
    assert by_unit["week"]["labels"] == ["2025-12-28", "2026-01-04", "2026-03-29"] and by_unit["year"]["labels"] == ["2025-04-01", "2026-04-01"]


def test_constants_carry_calendar_settings(monkeypatch):
    from core import server, settings
    from dashboard import dash_app  # noqa: F401  late_constants를 단다
    monkeypatch.setattr(settings, "load", lambda: {"week_start": "sun", "fiscal_start_month": 4})
    found = server.constants("dashboard")
    assert found["week_start"] == "sun" and found["fiscal_start_month"] == 4
    monkeypatch.setattr(settings, "load", lambda: {})
    found = server.constants("dashboard")
    assert found["week_start"] == "mon" and found["fiscal_start_month"] == 1


# ---------- 5. 화면 파일(/static·/next)은 차례를 기다리지 않는다 ----------

def test_static_and_next_skip_turn(monkeypatch):
    from core import server
    from query.search_app import app
    took = []
    monkeypatch.setattr(server, "turn", type("Spy", (), {"acquire": lambda self: took.append(1), "release": lambda self: None})())
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    took.clear()
    for path in ("/static/core/none.js", "/next/none.js"):
        c.get(path)
    assert took == []
    c.post("/api/disconnect", json={})
    assert took == [1]  # 연결을 쓰는 라우트는 그대로 기다린다


# ---------- 6. data 없이 보낸 카드는 저장된 값을 둔다 ----------

def test_save_dashboard_without_data_keeps_saved(folder):
    from core import dash_store
    card = {"kind": "chart", "title": "매출", "source": {"schema": "", "table": "orders", "body": {"base": "orders", "x": "매장", "series": [{"y": "금액", "agg": "sum"}]}},
            "data": {"x": "매장", "values": {"labels": ["강남"], "series": [{"name": "합계 (금액)", "values": [10]}]}}}
    first = dash_store.save_dashboard({"name": "주간", "cards": [card]})
    bare = {k: v for k, v in first["cards"][0].items() if k != "data"}
    again = dash_store.save_dashboard({**first, "cards": [{**bare, "title": "새 제목"}]})
    assert again["cards"][0]["title"] == "새 제목" and again["cards"][0]["data"] == first["cards"][0]["data"]
    assert dash_store.get_dashboard(first["id"])["cards"][0]["data"] == first["cards"][0]["data"]
    cleared = dash_store.save_dashboard({**first, "cards": [{**bare, "data": {}}]})
    assert cleared["cards"][0]["data"] == {}  # data를 보내면(빈 값이어도) 그 값으로


# ---------- 7. 테이블을 만든 뒤 읽기 전용으로 다시 열지 못하면 연결을 끊고 알린다 ----------

def test_make_table_reopen_failure_drops_connection(folder, tmp_path, monkeypatch):
    import duckdb
    from core import server
    from prep import prep_app
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table orders as select i as id from range(5) t(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    real = server.open_con

    def opener(opened, read_only=True):
        if read_only:
            raise RuntimeError("다른 프로그램이 잡고 있음")
        return real(opened, read_only=False)
    monkeypatch.setattr(server, "open_con", opener)
    try:
        with app.test_request_context(json={}), pytest.raises(ValueError, match="연결을 다시"):
            prep_app.make_table({"schema": "", "base": "orders"}, "made")
        assert server.state["con"] is None and server.state["opened"] is None
    finally:
        server.drop_connection()


# ---------- 8. 빈 기간 채우기의 다음 기간은 한 벌(회계연도도 맞게) ----------

def test_sentence_fill_periods_uses_result_next_period():
    from query import sentence_exec
    rows = [{"기간": "2025-04-01", "n": 1}, {"기간": "2027-04-01", "n": 2}]
    got = sentence_exec.fill_periods(rows, "기간", "year", ["n"])
    assert [r["기간"] for r in got] == ["2025-04-01", "2026-04-01", "2027-04-01"] and [r["n"] for r in got] == [1, 0, 2]
    assert not hasattr(sentence_exec, "next_start")


# ---------- 9. 분석 화면은 옛 대시보드 계산 스크립트를 부르지 않는다 ----------

def test_prep_page_without_old_dash_calc():
    assert "lib/dash_calc.js" not in (ROOT / "static/prep/prep.html").read_text(encoding="utf-8")


# ---------- 10. 작은 것들 ----------

def test_dash_pdf_cleans_part_on_failure(tmp_path, monkeypatch):
    from core import server
    from dashboard import dash_app, dash_pdf  # noqa: F401  /api/dash/pdf 라우트를 단다
    from query.search_app import app
    monkeypatch.setattr(dash_pdf, "decode_image", lambda image: b"")
    monkeypatch.setattr(dash_pdf, "make_pdf", lambda image: b"%PDF-1.4")
    (tmp_path / "r.pdf").mkdir()  # 같은 이름의 폴더가 있어 바꿔치기가 실패한다
    monkeypatch.setattr(server, "ask_save", lambda name, kinds: str(tmp_path / "r.pdf"))
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/dash/pdf", json={"image": "x", "name": "r"}).status_code == 400
    assert not (tmp_path / "r.pdf.part").exists()


def test_values_partial_skipped_on_bigquery(bigquery, remote):
    got = remote.post("/api/values", json={"base": "orders", "column": "grp", "confirmed": True}).get_json()
    assert got["values"] and got["partial"] is None  # 앞부분인지 세는 조회도 읽는 양만큼 요금이라 묻지 않는다(모름)


def test_settings_show_app_version():
    """설정 창에 지금 버전 줄이 있고, 화면 JS가 SERVER.features.version(설치 프로그램이 쓴 features.ini)으로 채운다. 소스 실행이면 개발 실행이라고 보인다."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "static" / "core"
    assert 'id="app-version"' in (root / "status.html").read_text(encoding="utf-8")
    js = (root / "common.js").read_text(encoding="utf-8")
    assert '$("app-version").textContent = SERVER.features.version ? `v${SERVER.features.version}` : "개발 실행' in js
