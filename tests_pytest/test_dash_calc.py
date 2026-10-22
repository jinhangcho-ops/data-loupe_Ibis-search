"""대시보드 카드 값 구하기(dash_calc): 날짜 X축은 일 값 {sum, count, min, max}과 단위별 고유 개수, 일 값이 많으면 앞을 자르고 월·연은 서버가 따로,
나누기, 항목별 차트, /api/dash/card/compute(다른 연결 거부, 분석 카드). 값은 손으로 계산해 맞춘다. 데이터는 테스트 안에서 지어낸다."""
import datetime as dt

import duckdb
import polars as pl
import pytest
from lazy import ibis

import dash_calc
import dash_store
import server

D = dt.date
# 2026-01-05는 월요일. 1월 첫째 주(5~11일), 둘째 주(12~18일), 2월 2일(월)
DF = pl.DataFrame({"day": [D(2026, 1, 5), D(2026, 1, 5), D(2026, 1, 6), D(2026, 1, 6), D(2026, 1, 12), D(2026, 2, 2), D(2026, 2, 2)],
                   "store": ["A", "A", "A", "B", "A", "B", "A"], "amt": [10.0, 20.0, 30.0, 5.0, 40.0, 50.0, None], "uid": ["u1", "u1", "u2", "u3", "u1", "u4", "u5"]})
DAYS = ["2026-01-05", "2026-01-06", "2026-01-12", "2026-02-02"]


@pytest.fixture(params=["memory", "polars"])
def t(request):
    return ibis.memtable(DF.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", DF)


def named(section):
    return {s["name"]: s for s in section["series"]}


def test_daily_components(t):
    series = [{"y": "amt", "agg": "sum"}, {"y": None, "agg": "count"}, {"y": "amt", "agg": "mean"}, {"y": "uid", "agg": "nunique"}]
    day = dash_calc.time_data(t, "day", series, None)["by_unit"]["day"]
    assert day["labels"] == DAYS
    s = named(day)
    assert s["합계 (amt)"]["sum"] == [30, 35, 40, 50] and s["합계 (amt)"]["count"] == [2, 2, 1, 1]  # 빈 값은 count에서 빠진다
    assert s["합계 (amt)"]["min"] == [10, 5, 40, 50] and s["합계 (amt)"]["max"] == [20, 30, 40, 50]
    assert s["개수"]["count"] == [2, 2, 1, 2] and "sum" not in s["개수"]  # 개수는 행 수
    assert s["평균 (amt)"]["sum"] == [30, 35, 40, 50] and s["평균 (amt)"]["agg"] == "mean"  # 평균은 화면이 Σsum/Σcount로
    assert s["고유 개수 (uid)"]["values"] == [1, 2, 1, 2]


def test_nunique_is_computed_per_unit_on_server(t):
    unique = {"y": "uid", "agg": "nunique"}
    by_unit = dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}, unique], None)["by_unit"]
    # 일 값을 다시 묶으면 u1이 두 번 세어지므로(3+... ) 서버에서 단위마다 따로 구한다
    assert by_unit["week"]["labels"] == ["2026-01-05", "2026-01-12", "2026-02-02"]  # 월요일 시작
    assert named(by_unit["week"])["고유 개수 (uid)"]["values"] == [3, 1, 2] and list(named(by_unit["week"])) == ["고유 개수 (uid)"]
    assert named(by_unit["month"])["고유 개수 (uid)"]["values"] == [3, 2] and by_unit["month"]["labels"] == ["2026-01-01", "2026-02-01"]
    assert named(by_unit["year"])["고유 개수 (uid)"]["values"] == [5] and by_unit["year"]["labels"] == ["2026-01-01"]
    assert "week" not in dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}], None)["by_unit"]  # 고유 개수가 없으면 일 값만


def test_min_max_and_split(t):
    series = [{"y": "amt", "agg": "min"}, {"y": "amt", "agg": "max"}]
    s = named(dash_calc.time_data(t, "day", series, None)["by_unit"]["day"])
    assert s["최소 (amt)"]["min"] == [10, 5, 40, 50] and s["최대 (amt)"]["max"] == [20, 30, 40, 50]
    data = dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}], "store")
    s = named(data["by_unit"]["day"])
    assert list(s) == ["A", "B"]  # 행이 많은 값 먼저(A 5행, B 2행), 계열이 하나면 이름이 나눈 값
    assert s["A"]["sum"] == [30, 30, 40, None] and s["B"]["sum"] == [None, 5, None, 50]


def test_too_many_days_cut_and_month_year_from_server(t, monkeypatch):
    monkeypatch.setattr(dash_store, "MAX_DAYS", 2)
    data = dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}, {"y": "amt", "agg": "mean"}], None)
    by_unit, name = data["by_unit"], "합계 (amt)"
    assert by_unit["day"]["labels"] == DAYS[-2:] and named(by_unit["day"])[name]["sum"] == [40, 50] and named(by_unit["day"])[name]["count"] == [1, 1]
    assert data["note"] == "앞 2일은 월 단위만 있어요."
    assert named(by_unit["month"])[name]["values"] == [105, 50] and by_unit["month"]["labels"] == ["2026-01-01", "2026-02-01"]  # 잘린 앞부분까지
    assert named(by_unit["year"])[name]["values"] == [155]
    assert named(by_unit["month"])["평균 (amt)"]["values"] == [105 / 5, 50] and "week" not in by_unit  # 주는 남은 일 값으로 묶는다


def test_day_values_fit_card_size(t, monkeypatch):
    monkeypatch.setattr(dash_store, "MAX_CARD_BYTES", 250)  # 하루 몫이 커서 일 수가 줄어든다
    data = dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}], None)
    assert 1 <= len(data["by_unit"]["day"]["labels"]) < 4 and data["note"].startswith("앞 ") and "month" in data["by_unit"]


def test_bad_series(t):
    with pytest.raises(ValueError, match="계열을 하나 이상"):
        dash_calc.clean_series([])
    with pytest.raises(ValueError, match="집계 방식"):
        dash_calc.clean_series([{"y": "amt", "agg": "median"}])
    with pytest.raises(ValueError, match="숫자 컬럼"):
        dash_calc.time_data(t, "day", [{"y": "store", "agg": "sum"}], None)
    with pytest.raises(ValueError, match="없습니다|찾을 수"):
        dash_calc.time_data(t, "nope", [{"y": "amt", "agg": "sum"}], None)


# ---------- 라우트: 실제 DuckDB 파일에 연결 ----------

@pytest.fixture
def db(folder, tmp_path):
    import dash_app  # noqa: F401  대시보드 라우트
    import prep_app  # noqa: F401  전처리 라우트
    from app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table orders (day date, store varchar, amt double, uid varchar)")
    raw.executemany("insert into orders values (?, ?, ?, ?)", [tuple(r) for r in DF.iter_rows()])
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def card(kind, **source):
    return {"kind": kind, "title": "t", "source": {"schema": "", "table": "orders", **source}}


def compute(c, **body):
    return c.post("/api/dash/card/compute", json=body).get_json()


def test_compute_time_chart(db):
    found = compute(db, card=card("chart", body={"base": "orders", "x": "day", "series": [{"y": "amt", "agg": "sum"}]}))
    assert named(found["data"]["by_unit"]["day"])["합계 (amt)"]["sum"] == [30, 35, 40, 50] and found["data"]["computed_at"] and len(found["data"]["def_hash"]) == 16
    assert found["source"]["key"].startswith("folder:") and found["source"]["label"]


def test_compute_category_chart(db):
    found = compute(db, card=card("chart", body={"base": "orders", "x": "store", "series": [{"y": "amt", "agg": "sum"}, {"y": None, "agg": "count"}]}))
    v = found["data"]["values"]
    assert v["labels"] == ["A", "B"] and [s["name"] for s in v["series"]] == ["합계 (amt)", "개수"] and v["series"][0]["values"] == [100, 55] and v["series"][1]["values"] == [5, 2]
    split = compute(db, card=card("chart", body={"base": "orders", "x": "store", "split": "day", "series": [{"y": "amt", "agg": "sum"}]}))
    assert split["data"]["values"]["series"][0]["name"] == "2026-01-05"  # 계열이 하나면 이름이 나눈 값


def test_same_definition_same_hash(db):
    body = {"base": "orders", "x": "store", "series": [{"y": "amt", "agg": "sum"}]}
    first = compute(db, card=card("chart", body=body))["data"]["def_hash"]
    assert compute(db, card={**card("chart", body={"series": body["series"], "x": "store", "base": "orders"}), "title": "다른 이름"})["data"]["def_hash"] == first
    assert compute(db, card=card("chart", body={**body, "x": "uid"}))["data"]["def_hash"] != first


def test_other_connection_rejected(db):
    other = card("chart", body={"base": "orders", "x": "store", "series": [{"y": None, "agg": "count"}]})
    other["source"].update(key="url:postgres://elsewhere:5432/x", label="elsewhere/x")
    assert "다른 연결(elsewhere/x)" in compute(db, card=other)["error"]
    assert "다시 구할 조회" in compute(db, card=card("chart"))["error"]
    assert "아직 다시 구할 수 없" in compute(db, card=card("kpi"))["error"]


def test_compute_analysis_card(db):
    found = compute(db, card=card("analysis", recipe={"base": "orders"}, analysis={"kind": "groups", "by": "store", "agg": "sum", "value": "amt"}, full=True))
    assert found["data"]["values"]["labels"] == ["A", "B"] and found["data"]["values"]["values"] == [100, 55] and found["data"]["note"] == "테이블 전체"


def test_add_card_fills_connection_without_account(db):
    added = db.post("/api/dash/card/add", json={"dashboard": "", "name": "분석 모음", "card": card("analysis", recipe={"base": "orders"}, analysis={"kind": "groups", "by": "store", "agg": "count", "value": ""})}).get_json()
    saved = db.get(f"/api/dash/{added['dashboard']['id']}").get_json()["dashboard"]
    key = saved["cards"][0]["source"]["key"]
    assert key.startswith("folder:") and saved["cards"][0]["source"]["label"]


def test_compute_metric_card(db, tmp_path):
    import metrics
    path = tmp_path / "m.yaml"
    path.write_text("metrics:\n  매출:\n    numerator: {agg: sum, column: amt}\n  A비율:\n    format: percent\n    numerator: {agg: count, where: store = A}\n    denominator: {agg: count}\n", encoding="utf-8")
    metrics.save_file_path(str(path))
    one = compute(db, card=card("metric", body={"base": "orders", "names": ["매출", "A비율"]}))["data"]["values"]
    assert one["labels"] == ["전체"] and [s["name"] for s in one["series"]] == ["매출", "A비율 (%)"]
    assert one["series"][0]["values"] == [155] and one["series"][1]["values"] == [round(5 / 7 * 100, 6)]  # 비율은 %로(×100)
    by = compute(db, card=card("metric", body={"base": "orders", "names": ["매출"], "by": "store"}))["data"]
    assert by["x"] == "store" and by["values"]["labels"] == ["A", "B"] and by["values"]["series"][0]["values"] == [100, 55]
