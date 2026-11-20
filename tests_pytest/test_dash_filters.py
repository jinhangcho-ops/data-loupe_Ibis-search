"""대시보드 필터(3단계): 보는 사람이 고른 필터(ctx.filters)를 카드 조회의 조건으로 더해 다시 구한다. 컬럼이 없는 카드는 그대로 두고 안내에 적는다.
필터 칸 목록 저장(dash_store.check_filters), 필터 값 목록, 원래 행 카드가 쓸 조회. 값은 손으로 계산해 맞춘다. 데이터는 시험 안에서 지어낸다."""
import duckdb
import pytest

from core import dash_store
from core import server
from dashboard import dash_calc

ROWS = [("A", "x", 10.0), ("A", "y", 20.0), ("B", "x", 5.0), ("B", "y", 50.0), ("C", "x", 1.0)]  # store, kind, amt
SUM = [{"y": "amt", "agg": "sum"}]
ONLY_A = {"filters": [{"column": "store", "in": ["A"]}]}


@pytest.fixture
def db(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401  대시보드 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "f.duckdb"))
    raw.execute("create table orders (store varchar, kind varchar, amt double)")
    raw.executemany("insert into orders values (?, ?, ?)", ROWS)
    raw.execute("create table visits (kind varchar, n integer)")
    raw.executemany("insert into visits values (?, ?)", [("x", 3), ("y", 4)])
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "f.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def card(table, x, series, kind="chart"):
    return {"kind": kind, "title": "t", "source": {"schema": "", "table": table, "body": {"base": table, "x": x, "series": series}}}


def by_label(data):
    return dict(zip(data["values"]["labels"], data["values"]["series"][0]["values"]))


def compute(c, **body):
    return c.post("/api/dash/card/compute", json=body).get_json()


def test_filter_narrows_card(db):
    plain = compute(db, card=card("orders", "kind", SUM))["data"]
    assert by_label(plain) == {"x": 16, "y": 70}  # 10+5+1, 20+50
    cut = compute(db, card=card("orders", "kind", SUM), ctx=ONLY_A)["data"]
    assert by_label(cut) == {"x": 10, "y": 20} and "필터" not in cut["note"]
    two = compute(db, card=card("orders", "kind", SUM), ctx={"filters": [{"column": "store", "in": ["A", "C"]}, {"column": "kind", "in": ["x"]}]})["data"]
    assert by_label(two) == {"x": 11}  # 필터끼리는 and, 한 필터의 값들은 or


def test_filter_skips_card_without_column(db):
    found = compute(db, card=card("visits", "kind", [{"y": "n", "agg": "sum"}]), ctx=ONLY_A)["data"]
    assert by_label(found) == {"x": 3, "y": 4}  # store 컬럼이 없는 테이블: 그대로
    assert found["note"] == "필터(store)는 이 카드의 테이블에 그 컬럼이 없어 걸리지 않았어요."


def test_filter_in_many_cards_and_number_column(db):
    cards = [{**card("orders", "kind", SUM), "id": "a1"}, {**card("visits", "kind", [{"y": "n", "agg": "sum"}]), "id": "b2"}]
    got = {r["id"]: r["data"] for r in db.post("/api/dash/cards/compute", json={"cards": cards, "ctx": ONLY_A}).get_json()["results"]}
    assert by_label(got["a1"]) == {"x": 10, "y": 20} and by_label(got["b2"]) == {"x": 3, "y": 4} and "필터(store)" in got["b2"]["note"]
    num = compute(db, card=card("orders", "store", SUM), ctx={"filters": [{"column": "amt", "in": ["5", "50"]}]})["data"]
    assert by_label(num) == {"B": 55}  # 값은 글자로 와서 컬럼 타입(숫자)으로 바뀐다


def test_filter_bad_shape(db):
    assert "모양" in compute(db, card=card("orders", "kind", SUM), ctx={"filters": [{"in": ["A"]}]})["error"]
    assert "목록" in compute(db, card=card("orders", "kind", SUM), ctx={"filters": [{"column": "store", "in": []}]})["error"]
    assert "목록" in compute(db, card=card("orders", "kind", SUM), ctx={"filters": [{"column": "store", "in": [1]}]})["error"]


def test_filtered_body_for_row_cards(db):
    found = db.post("/api/dash/card/filtered", json={"card": card("orders", "", [], "recent_rows"), "ctx": {"filters": [*ONLY_A["filters"], {"column": "none", "in": ["z"]}]}}).get_json()
    assert found["missed"] == ["none"] and found["body"]["conditions"] == [{"column": "store", "in": ["A"]}]
    rows = db.post("/api/query", json={**found["body"], "limit": 10, "count": False}).get_json()["rows"]["rows"]
    assert sorted(r["amt"] for r in rows) == [10, 20]


def test_filter_values(db):
    saved = db.post("/api/dash", json={"name": "필터", "cards": [card("visits", "kind", [{"y": "n", "agg": "sum"}]), card("orders", "kind", SUM)],
                                       "filters": [{"column": "store", "label": "매장"}]}).get_json()["dashboard"]
    assert saved["filters"] == [{"column": "store", "label": "매장"}]
    assert db.post("/api/dash/open", json={"id": saved["id"]}).get_json()["dashboard"]["filters"] == saved["filters"]
    assert db.post("/api/dash/filter/values", json={"id": saved["id"], "column": "store"}).get_json() == {"values": ["A", "B", "C"], "more": False}  # store가 있는 첫 카드(orders)에서
    assert "없어요" in db.post("/api/dash/filter/values", json={"id": saved["id"], "column": "nope"}).get_json()["error"]


def test_check_filters():
    assert dash_store.check_filters(None) == []
    assert dash_store.check_filters([{"column": " 지역 ", "label": ""}]) == [{"column": "지역", "label": ""}]
    for bad in ([{"column": ""}], [{"column": "a"}, {"column": "a"}], "x", [{"column": "a"}] * (dash_store.MAX_FILTERS + 1), [1]):
        with pytest.raises(ValueError):
            dash_store.check_filters(bad)


def test_with_filters_keeps_saved_card():
    c = {"source": {"recipe": {}}}
    assert dash_calc.with_filters(c, ONLY_A) == (c, ["store"])  # 조회가 없는 카드: 하나도 못 건다
    assert dash_calc.with_filters(c, None) == (c, []) and dash_calc.filter_note([]) == ""
