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
        dash_calc.clean_series([{"y": "amt", "agg": "bogus"}])
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
    stamp = lambda minutes: dt.datetime.now() - dt.timedelta(minutes=minutes)  # 지금 기준(끊김 규칙이 실제 지금 시각을 쓴다)
    raw.execute("create table nodes (name varchar, kind varchar, cpu double, seen timestamp, peer varchar)")
    raw.executemany("insert into nodes values (?, ?, ?, ?, ?)", [("a", "서버", 99.0, stamp(1), "b"), ("b", "서버", 10.0, stamp(2), None), ("c", "라우터", 10.0, stamp(120), "a")])
    raw.execute("create table temps (host varchar, ts timestamp, val double)")
    raw.executemany("insert into temps values (?, ?, ?)", [("x", dt.datetime(2026, 1, 1, 9, 5), 10.0), ("x", dt.datetime(2026, 1, 1, 9, 50), 20.0), ("x", dt.datetime(2026, 1, 1, 10, 5), 30.0),
                                                          ("y", dt.datetime(2026, 1, 1, 10, 30), 7.0)])
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
    assert "흐름 카드" in compute(db, card=card("funnel"))["error"]  # 다시 구할 분석이 없는 흐름 카드


def test_compute_analysis_card(db):
    found = compute(db, card=card("analysis", recipe={"base": "orders"}, analysis={"kind": "groups", "by": "store", "agg": "sum", "value": "amt"}, full=True))
    assert found["data"]["values"]["labels"] == ["A", "B"] and found["data"]["values"]["values"] == [100, 55] and found["data"]["note"] == "테이블 전체"


def test_add_card_fills_connection_without_account(db):
    added = db.post("/api/dash/card/add", json={"dashboard": "", "name": "분석 모음", "card": card("analysis", recipe={"base": "orders"}, analysis={"kind": "groups", "by": "store", "agg": "count", "value": ""})}).get_json()
    saved = db.get("/api/dash/get", query_string={"id": added["dashboard"]["id"]}).get_json()["dashboard"]
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


# ---------- 산점도·버블 ----------

PTS = pl.DataFrame({"x": [1, 2, 3, 4, None], "y": [2.0, 4.0, 5.0, 9.0, 7.0], "s": [10, 20, 30, 40, 50], "g": ["a", "b", "a", "a", "b"], "t": ["p", "q", "r", "s", "t"]})


@pytest.fixture(params=["memory", "polars"])
def pt(request):
    return ibis.memtable(PTS.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("pts", PTS)


def test_points_r_and_trend_line_by_hand(pt):
    # 빈 값 행(x가 None)은 뺀다: x=1..4, y=2,4,5,9. x̄=2.5 ȳ=5 Σdxdy=11 Σdx²=5 Σdy²=26 → b=2.2, a=-0.5, r=11/√130
    d = dash_calc.points_data(pt, "x", "y")
    assert d["points"] == [[1, 2], [2, 4], [3, 5], [4, 9]] and d["total"] == 4 and d["sampled"] == "" and d["note"] == ""
    assert d["n"] == 4 and d["b"] == pytest.approx(2.2) and d["a"] == pytest.approx(-0.5) and d["r"] == pytest.approx(11 / 130 ** 0.5)
    assert d["size_range"] is None and d["groups"] == []


def test_points_size_split_and_range(pt):
    d = dash_calc.points_data(pt, "x", "y", "s", "g")
    assert d["points"] == [[1, 2, 10, "a"], [2, 4, 20, "b"], [3, 5, 30, "a"], [4, 9, 40, "a"]] and d["groups"] == ["a", "b"] and d["size_range"] == [10, 40]


def test_points_limit_even_and_head(pt, monkeypatch):
    monkeypatch.setattr(dash_calc, "MAX_POINTS", 3)
    d = dash_calc.points_data(pt, "x", "y")
    assert d["total"] == 4 and d["sampled"] in ("even", "head") and 0 < len(d["points"]) <= 3 and "전체 4개" in d["note"]
    assert d["r"] == pytest.approx(11 / 130 ** 0.5) and d["b"] == pytest.approx(2.2)  # r·추세선은 뽑은 점이 아니라 전체 행으로
    monkeypatch.setattr(dash_calc.connection, "compiles", lambda expr: False)  # 뽑기가 안 되는 연결
    head = dash_calc.points_data(pt, "x", "y")
    assert head["sampled"] == "head" and len(head["points"]) == 3 and head["note"].startswith("점이 많아 앞에서 3개만")
    again = dash_calc.points_data(pt, "x", "y")
    assert again["points"] == head["points"]


def test_points_reject_non_numeric_and_degenerate(pt):
    for args in (("t", "y"), ("x", "t"), ("x", "y", "t"), ("nope", "y")):
        with pytest.raises(ValueError):
            dash_calc.points_data(pt, *args)
    flat = ibis.memtable(pl.DataFrame({"x": [1, 1, 1], "y": [1, 2, 3]}).to_arrow())
    d = dash_calc.points_data(flat, "x", "y")
    assert d["b"] is None and d["a"] is None and d["r"] is None


def test_points_route_and_card(db):
    body = {"base": "orders", "x": "amt", "y": "amt", "split": "store"}
    got = db.post("/api/chart/points", json={**body, "y": "amt"}).get_json()["points"]
    assert got["total"] == 6 and got["r"] == pytest.approx(1.0) and got["groups"] == ["A", "B"]
    assert "숫자 컬럼" in db.post("/api/chart/points", json={**body, "x": "store"}).get_json()["error"]
    assert "다른 연결" in db.post("/api/chart/points", json={**body, "source_key": "folder:other"}).get_json()["error"]
    c = card("chart", body={"base": "orders", "x": "amt", "series": [{"y": "amt", "agg": "sum"}]})
    c["view"] = {"type": "bubble", "points": {"size": "amt"}, "trend": True, "show_r": True}
    data = compute(db, card=c)["data"]
    assert data["x_kind"] == "num" and data["num_cols"] == ["amt"] and data["points"]["size"] == "amt" and len(data["points"]["points"]) == 6 and "values" in data
    plain = compute(db, card=card("chart", body={"base": "orders", "x": "store", "series": [{"y": "amt", "agg": "sum"}]}))["data"]
    assert plain["x_kind"] == "text" and "points" not in plain


# ---------- 3단계 카드 종류: kpi·gauge·summary·recent_rows, 기간 기준 날짜 컬럼, 목표 파일 ----------

def value_card(kind, series, date=None, view=None, **body):
    c = card(kind, body={"base": "orders", "series": series, **body})
    if date:
        c["source"]["date"] = date
    c["view"] = view or {}
    return c


SUM = [{"y": "amt", "agg": "sum"}]


def test_kpi_with_date_column_keeps_daily_values_and_lists_date_columns(db):
    data = compute(db, card=value_card("kpi", SUM, "day"))["data"]
    assert named(data["by_unit"]["day"])["합계 (amt)"]["sum"] == [30, 35, 40, 50] and data["date_cols"] == ["day"] and "target" in data and data["target"] is None
    bad = compute(db, card=value_card("kpi", SUM, "store"))
    assert "날짜 컬럼" in bad["error"]


def test_kpi_without_date_is_scalar_and_period_line_is_ignored(db):
    data = compute(db, card=value_card("kpi", [*SUM, {"y": None, "agg": "count"}]))["data"]
    assert data["values"] == {"scalar": [155, 7], "names": ["합계 (amt)", "개수"]}


def test_gauge_uses_item_values(db):
    found = compute(db, card=value_card("gauge", SUM, x="store"))["data"]["values"]
    assert found["labels"] == ["A", "B"] and found["series"][0]["values"] == [100, 55]


def test_period_applies_to_non_date_cards_with_date_column(db):
    ctx = {"range": {"start": "2026-01-12", "end": "2026-02-02"}, "prev": {"start": "2025-12-21", "end": "2026-01-11", "text": "직전 22일"}}
    c = card("chart", body={"base": "orders", "x": "store", "series": SUM})
    c["source"]["date"] = "day"
    got = compute(db, card=c, ctx=ctx)["data"]
    assert got["values"]["labels"] == ["A", "B"] and got["values"]["series"][0]["values"] == [40, 50] and got["date_cols"] == ["day"]  # 그 기간(1-12~2-2)의 행만
    no_date = compute(db, card=card("chart", body={"base": "orders", "x": "store", "series": SUM}), ctx=ctx)["data"]
    assert no_date["values"]["series"][0]["values"] == [100, 55]  # 날짜 컬럼이 없으면 기간을 걸지 않는다


def test_summary_sentences_with_groups_and_target(db):
    ctx = {"range": {"start": "2026-01-12", "end": "2026-02-02"}, "prev": {"start": "2025-12-21", "end": "2026-01-11", "text": "직전 22일"}}
    c = value_card("summary", SUM, "day", {"group": "store", "target": {"value": 180}})
    data = compute(db, card=c, ctx=ctx)["data"]
    assert data["values"]["scalar"] == [90] and data["values"]["prev"] == [65]
    assert "직전 22일보다 38.5% 늘었어요" in data["sentences"][0] and "목표" in data["sentences"][-1] and data["target"]["value"] == 180
    plain = compute(db, card=value_card("summary", SUM), ctx=None)["data"]
    assert plain["values"]["scalar"] == [155] and plain["values"]["prev"] == [None] and "값이 없었어요" in plain["text"]


def test_targets_file_routes_and_named_target(db, tmp_path):
    path = tmp_path / "목표.csv"
    path.write_text("이름,값,기간\n매출,1억,\n건수,5,2026-10\n건수,7,2026-11\n", encoding="utf-8")
    assert db.get("/api/targets").get_json() == {"path": "", "rows": [], "errors": [], "error": ""}
    got = db.post("/api/targets", json={"path": str(path)}).get_json()
    assert got["path"] == str(path) and [r["value"] for r in got["rows"]] == [1e8, 5, 7] and got["error"] == ""
    assert "찾을 수 없어요" in db.post("/api/targets", json={"path": str(tmp_path / "없음.csv")}).get_json()["error"]
    assert db.get("/api/targets").get_json()["path"] == str(path)  # 읽을 수 없는 경로는 저장하지 않는다
    named_target = lambda **spec: compute(db, card=value_card("kpi", SUM, view={"target": spec}))["data"]["target"]
    assert named_target(name="매출") == {"value": 1e8, "label": "매출", "error": ""}
    assert "어느 기간인지 알 수 없어요" in named_target(name="건수")["error"] and named_target(name="건수", period="2026-11")["value"] == 7
    assert "찾지 못했어요" in named_target(name="없는 목표")["error"]
    assert db.post("/api/targets", json={"path": ""}).get_json()["path"] == "" and "목표 파일을 먼저" in named_target(name="매출")["error"]


def test_metric_kpi_has_pair_amounts(db, tmp_path):
    import metrics
    path = tmp_path / "m.yaml"
    path.write_text("metrics:\n  A비율:\n    format: percent\n    numerator: {agg: count, where: store = A}\n    denominator: {agg: count}\n", encoding="utf-8")
    metrics.save_file_path(str(path))
    data = compute(db, card=value_card("kpi", SUM, names=["A비율"]))["data"]
    assert data["values"]["scalar"] == [pytest.approx(5 / 7)] and data["values"]["format"] == "percent"
    assert data["pair"] == {"num_label": "개수 [store = A]", "num": 5, "den_label": "개수", "den": 7}


def test_recent_rows_stores_columns_only(db):
    data = compute(db, card=card("recent_rows", body={"base": "orders"}))["data"]
    assert data["columns"] == ["day", "store", "amt", "uid"] and set(data) <= {"columns", "computed_at", "def_hash"}


# ---------- 3단계 B: 표·문장·상태·경고·편차, 고유 개수 기간 값 ----------
RANGE = {"range": {"start": "2026-01-12", "end": "2026-02-02"}, "prev": {"start": "2025-12-21", "end": "2026-01-11", "text": "직전 22일"}}


def test_table_card_groups_rows_and_previous_period(db):
    c = value_card("table", SUM, "day", {"group_cols": ["store"]})
    data = compute(db, card=c, ctx=RANGE)["data"]["values"]
    assert data["columns"] == [{"name": "store", "kind": "group"}, {"name": "합계 (amt)", "kind": "value", "agg": "sum"}] and data["rows"] == [["A", 40], ["B", 50]]
    assert data["prev"] == {"A": [60], "B": [5]} and data["more"] is False  # 직전 기간 값(▲▼ 칸용)
    whole = compute(db, card=value_card("table", SUM, view={"group_cols": ["store"]}))["data"]["values"]
    assert whole["rows"] == [["A", 100], ["B", 55]] and whole["prev"] is None
    total = compute(db, card=value_card("table", [*SUM, {"y": None, "agg": "count"}]))["data"]["values"]
    assert total["rows"] == [[155, 7]]  # 묶음 컬럼이 없으면 한 줄
    raw = compute(db, card=card("table", body={"base": "orders"}))["data"]
    assert raw["columns"] == ["day", "store", "amt", "uid"] and "values" not in raw  # 원래 행 표는 값을 저장하지 않는다
    assert "묶음 컬럼" in compute(db, card=value_card("table", SUM, view={"group_cols": ["nope"]}))["error"]


def test_sentence_blanks(db):
    view = {"template": "{매출}은 {비교}, {전}에서, {기준일} 기준", "compare": {"mode": "span"}, "better": "up", "blanks": [
        {"name": "매출", "y": "amt", "agg": "sum", "kind": "value", "format": "currency"}, {"name": "전", "y": "amt", "agg": "sum", "kind": "prev"},
        {"name": "비교", "y": "amt", "agg": "sum", "kind": "delta"}, {"name": "기준일", "kind": "asof"}, {"name": "나쁨", "y": "store", "agg": "sum", "kind": "value"}]}
    c = value_card("sentence", SUM, "day", view)
    blanks = compute(db, card=c, ctx=RANGE)["data"]["values"]["blanks"]
    assert blanks["매출"]["cur"] == 90 and blanks["전"]["cur"] == 90 and blanks["전"]["prev"] == 65 and "직전 22일보다 38.5% 늘었어요" in blanks["비교"]["text"]
    assert blanks["기준일"]["text"] == "2026-02-02" and "숫자 컬럼" in blanks["나쁨"]["error"]  # 계산 못 하는 빈칸은 이유만


def test_nunique_over_selected_period_is_recomputed(db):
    uniq = [{"y": "uid", "agg": "nunique"}, *SUM]
    c = value_card("kpi", uniq, "day")
    got = compute(db, card=c, ctx={"range": {"start": "2026-01-05", "end": "2026-01-06"}})["data"]
    assert got["values"]["window_nunique"] == {"start": "2026-01-05", "end": "2026-01-06", "values": {"0": 3}}  # u1·u2·u3
    assert "values" not in compute(db, card=c)["data"]  # 기간이 없으면 따로 구하지 않는다
    assert "values" not in compute(db, card=value_card("kpi", SUM, "day"), ctx=RANGE)["data"]


def test_watch_cards_status_since_and_impact(db):
    view = {"entity": "name", "time_col": "seen", "kind_col": "kind", "link_col": "peer", "stale": 30, "rules": [{"column": "cpu", "op": ">=", "value": 95, "level": "bad"}], "links": [["b", "z"]]}
    c = card("status", body={"base": "nodes"})
    c["view"] = view
    first = compute(db, card=c)["data"]
    got = {e["name"]: e for e in first["values"]["entities"]}
    assert {n: e["level"] for n, e in got.items()} == {"a": "bad", "b": "ok", "c": "bad"} and first["values"]["counts"] == {"ok": 1, "warn": 0, "bad": 2}  # a는 cpu, c는 끊김
    assert got["a"]["impact"] == ["b"] and got["c"]["impact"] == [] and got["a"]["kind"] == "서버" and set(first["since"]) == {"a", "c"}
    assert "마지막 데이터" in first["values"]["fresh"]
    again = compute(db, card={**c, "data": {"since": first["since"]}})["data"]
    assert again["since"] == first["since"]  # 계속 나쁘면 처음 나빠진 시각 그대로
    assert "엔티티" in compute(db, card=card("alerts", body={"base": "nodes"}))["error"]


def test_deviation_table_buckets_and_entities(db):
    c = card("deviation", body={"base": "temps", "series": [{"y": "val", "agg": "mean"}]})
    c["source"]["date"] = "ts"
    c["view"] = {"entity": "host", "bucket": "hour"}
    data = compute(db, card=c)["data"]["values"]
    assert data["buckets"] == ["2026-01-01 09:00:00", "2026-01-01 10:00:00"] and data["entities"] == [{"name": "x", "values": [15, 30]}, {"name": "y", "values": [None, 7]}]
    daily = card("deviation", body={"base": "orders", "series": SUM})
    daily["source"]["date"] = "day"
    daily["view"] = {"entity": "store", "bucket": "hour"}
    assert "시간대(시)" in compute(db, card=daily)["error"]
    daily["view"] = {"entity": "store", "bucket": "month"}
    month = compute(db, card=daily)["data"]["values"]
    assert month["buckets"] == ["2026-01-01", "2026-02-01"] and month["entities"] == [{"name": "A", "values": [100, None]}, {"name": "B", "values": [5, 50]}]


# ---------- 대시보드 기간이 비면 카드 자신의 기간 줄이 보는 기간, 바로 앞 같은 일수가 직전 기간 ----------
OWN = "day 2026-01-12 ~ 2026-02-02"  # RANGE와 같은 22일 → 값도 위 RANGE 테스트와 같다(이번 90, 직전 2025-12-21~2026-01-11은 65)


def test_own_range_rules():
    own = lambda *periods: dash_calc.own_range({"source": {"body": {"periods": list(periods)}}})
    got = own(OWN)
    assert got["range"] == {"start": "2026-01-12", "end": "2026-02-02"} and got["prev"] == {"start": "2025-12-21", "end": "2026-01-11", "text": "직전 22일"} and got["column"] == "day"
    assert own("day 2026-01-01 ~ ", "day ~ 2026-03-31") is None  # 열린 줄은 쓰지 않는다
    assert own("day 2026-01-01 ~ ", "when 2026-03-01 ~ 2026-03-31")["column"] == "when"  # 줄이 여럿이면 쓸 수 있는 첫 줄
    assert own("day 2026-03-31 ~ 2026-03-01", "day 2026-02-30 ~ 2026-03-01") is None  # 거꾸로 된 줄·없는 날
    assert own("day 2026-03-01 ~ 2026-03-01")["prev"] == {"start": "2026-02-28", "end": "2026-02-28", "text": "직전 1일"}
    assert own() is None and dash_calc.own_range({"source": {"body": {}}}) is None


@pytest.mark.parametrize("date", [None, "day"])
def test_own_period_gives_previous_range_to_summary_sentence_table(db, date):
    body = {"periods": [OWN]}
    summary = compute(db, card=value_card("summary", SUM, date, {}, **body), ctx=None)["data"]
    assert summary["values"]["scalar"] == [90] and summary["values"]["prev"] == [65]  # 날짜 컬럼을 안 골라도 카드의 기간 줄로
    assert "직전 22일" in summary["text"]
    view = {"template": "{전} {비교}", "compare": {"mode": "span"}, "better": "up", "blanks": [
        {"name": "전", "y": "amt", "agg": "sum", "kind": "prev"}, {"name": "비교", "y": "amt", "agg": "sum", "kind": "delta"}]}
    blanks = compute(db, card=value_card("sentence", SUM, date, view, **body))["data"]["values"]["blanks"]
    assert blanks["전"]["cur"] == 90 and blanks["전"]["prev"] == 65 and "직전 22일보다 38.5% 늘었어요" in blanks["비교"]["text"]
    table = compute(db, card=value_card("table", SUM, date, {"group_cols": ["store"]}, **body))["data"]["values"]
    assert table["rows"] == [["A", 40], ["B", 50]] and table["prev"] == {"A": [60], "B": [5]}


def test_no_period_line_means_no_previous(db):
    s = compute(db, card=value_card("summary", SUM, "day"), ctx=None)["data"]
    assert s["values"]["prev"] == [None]  # 대시보드 기간도 카드 기간 줄도 없으면 비교 문장이 없다
    t = compute(db, card=value_card("table", SUM, "day", {"group_cols": ["store"]}))["data"]["values"]
    assert t["prev"] is None
    open_ended = compute(db, card=value_card("table", SUM, "day", {"group_cols": ["store"]}, periods=["day 2026-01-12 ~ "]))["data"]["values"]
    assert open_ended["prev"] is None
    withdash = compute(db, card=value_card("table", SUM, "day", {"group_cols": ["store"]}), ctx=RANGE)["data"]["values"]
    assert withdash["prev"] == {"A": [60], "B": [5]}  # 대시보드 기간이 있으면 그것을 쓴다(지금까지와 같다)
