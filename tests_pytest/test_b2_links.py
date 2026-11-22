"""B2+B4(plans/dash-blocks.md "B2+B4 세부 명세 (2차, 확정)"): 숫자 타일 카드 종류(tile, 값은 KPI 길), 타일의 조건을 그대로 거는 필터(ctx.filters의 {body, as?, periods?}),
조건 식이 읽는 컬럼(dash_calc.pred_fields·tile_reads), 링크 저장(check_link·link_defaults), 맞춘 컬럼으로 거르기(parse.filter_as), 칩 줄 값 좁히기, 링크 컬럼 받기(linked_columns).
값은 손으로 계산해 맞춘다. 데이터는 시험 안에서 지어낸다."""
import datetime as dt

import duckdb
import ibis
import pytest

from core import dash_store
from core import parse
from core import server
from dashboard import dash_calc

ROWS = [("A", "x", 10.0), ("A", "y", 20.0), ("B", "x", 5.0), ("B", "y", 50.0), ("C", "x", 1.0)]  # store, kind, amt
SHOPS = [("B", 50.0, 1.0), ("B", 5.0, 100.0), ("A", 70.0, 2.0)]  # area, price, amt(다른 테이블: 지역 이름·가격이 다르다)
SALES = [(dt.date(2026, 1, 1), "A", 1.0), (dt.date(2026, 1, 2), "A", 2.0), (dt.date(2026, 1, 1), "B", 4.0)]  # day, store, amt
SUM = [{"y": "amt", "agg": "sum"}]
NOT_APPLIED = "필터(store)는 이 카드의 테이블에 그 컬럼이 없어 걸리지 않았어요."


@pytest.fixture
def db(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401  대시보드 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "f.duckdb"))
    raw.execute("create table orders (store varchar, kind varchar, amt double)")
    raw.executemany("insert into orders values (?, ?, ?)", ROWS)
    raw.execute("create table visits (kind varchar, n integer)")
    raw.executemany("insert into visits values (?, ?)", [("x", 3), ("y", 4)])
    raw.execute("create table shops (area varchar, price double, amt double)")
    raw.executemany("insert into shops values (?, ?, ?)", SHOPS)
    raw.execute("create table sales (day date, store varchar, amt double)")
    raw.executemany("insert into sales values (?, ?, ?)", SALES)
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "f.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def card(table, x, series, kind="chart"):
    return {"kind": kind, "title": "t", "source": {"schema": "", "table": table, "body": {"base": table, "series": series, **({"x": x} if x else {})}}}


def tile_body(*conditions, table="orders"):
    """타일이 숫자를 셀 때 쓴 조회(source.body). 필터로 걸리는 것은 conditions."""
    return {"base": table, "series": [{"y": None, "agg": "count"}], "conditions": list(conditions)}


def by_label(data):
    return dict(zip(data["values"]["labels"], data["values"]["series"][0]["values"]))


def compute(c, **body):
    return c.post("/api/dash/card/compute", json=body).get_json()


def frame_card(card_id, kind, frame, **view):
    return {"id": card_id, "kind": kind, "title": kind, "layout": {"x": 0, "y": 0, "w": 4, "h": 1}, "view": {"frame": frame, **view}}


# ---------- pred_fields ----------

def test_pred_fields_lists_condition_columns():
    t = ibis.memtable({"a": [1], "b": ["x"], "c": [2.0], "d": [0]})
    preds = [parse.predicate("a >= 1", t), parse.predicate({"any": ["b = x", "c < 3"]}, t)]
    assert dash_calc.pred_fields(preds) == {"a", "b", "c"}  # d는 조건에 없다
    assert dash_calc.pred_fields([]) == set()


# ---------- tile 카드 종류 ----------

def test_tile_kind_saves_like_kpi():
    assert "tile" in dash_store.KINDS and dash_store.KINDS.index("tile") == dash_store.KINDS.index("kpi") + 1
    saved = dash_store.check_card({**card("orders", None, SUM, kind="tile"), "view": {"format": "number", "unit_text": "건"}})
    assert saved["kind"] == "tile" and saved["view"]["unit_text"] == "건"


def test_tile_value_like_kpi(db):
    def value(kind):
        return {k: v for k, v in compute(db, card=card("orders", None, SUM, kind=kind))["data"].items() if k not in ("computed_at", "reads", "def_hash")}  # def_hash는 카드 종류까지 넣어 만든 값이라 종류가 다르면 다르다
    assert "tile" in dash_calc.VALUE_KINDS
    assert value("tile") == value("kpi")


def test_tile_note_and_press():
    saved = dash_store.check_card({**card("orders", None, SUM, kind="tile"), "view": {"note": " 지난주 대비 ", "press": "period"}})
    assert saved["view"]["note"] == "지난주 대비" and saved["view"]["press"] == "period"
    with pytest.raises(ValueError, match="press"):
        dash_store.check_card({**card("orders", None, SUM, kind="tile"), "view": {"press": "always"}})
    with pytest.raises(ValueError, match="숫자 타일에만"):
        dash_store.check_card({**card("orders", "kind", SUM), "view": {"press": "cond"}})
    with pytest.raises(ValueError):
        dash_store.check_card({**card("orders", None, SUM, kind="tile"), "view": {"note": "가" * (dash_store.MAX_TILE_NOTE + 1)}})


# ---------- 링크 저장(view.link_to·link_map) ----------

def test_check_link_shapes():
    assert dash_store.check_link({"type": ""}) == {}
    assert dash_store.check_link({"link_to": []}) == {"link_to": []}  # 빈 목록도 남긴다(옛 것 옮기기가 다시 채우지 않게)
    assert dash_store.check_link({"link_to": ["a", "b"], "link_map": {"b": {" store ": "area"}}}) == {"link_to": ["a", "b"], "link_map": {"b": {"store": "area"}}}
    for bad in ({"link_to": "a"}, {"link_to": ["a", "a"]}, {"link_to": ["bad id!"]}, {"link_to": [f"c{i}" for i in range(dash_store.MAX_CARDS + 1)]}):
        with pytest.raises(ValueError, match="link_to"):
            dash_store.check_link(bad)
    for bad in ({"link_to": ["a"], "link_map": {"b": {"x": "y"}}}, {"link_map": {"a": {"x": "y"}}}, {"link_to": ["a"], "link_map": {"a": "x"}},
                {"link_to": ["a"], "link_map": {"a": {f"c{i}": "y" for i in range(dash_store.MAX_LINK_COLS + 1)}}}):
        with pytest.raises(ValueError, match="link_map"):
            dash_store.check_link(bad)


def test_card_link_rules():
    chart = {**card("orders", "kind", SUM), "id": "c1", "view": {"cross": True, "link_to": ["c2"], "link_map": {"c2": {"kind": "type"}}}}
    saved = dash_store.check_card(chart)
    assert saved["view"]["link_to"] == ["c2"] and saved["view"]["link_map"] == {"c2": {"kind": "type"}}
    assert dash_store.check_card({**chart, "view": {"link_to": []}})["view"]["link_to"] == []
    assert dash_store.check_card({**chart, "view": {"cross": True, "link_to": ["c1", "c2"]}})["view"]["link_to"] == ["c1", "c2"]  # 차트는 자기 자신도 걸러진다
    with pytest.raises(ValueError, match="자기 자신"):
        dash_store.check_card({**card("orders", None, SUM, kind="tile"), "id": "t1", "view": {"press": "cond", "link_to": ["t1"]}})
    with pytest.raises(ValueError, match="링크를 보내지 않아요"):
        dash_store.check_card({**card("orders", None, SUM, kind="kpi"), "view": {"link_to": ["c2"]}})
    with pytest.raises(ValueError, match="링크를 보내지 않아요"):
        dash_store.check_card(frame_card("b1", "button", {"label": "가"}, link_to=["c2"]))
    view_seg = frame_card("s1", "segment", {"mode": "view", "cells": [{"label": "가"}]}, link_to=["c1", "c2"])
    with pytest.raises(ValueError, match="하나예요"):
        dash_store.check_card(view_seg)
    filter_seg = frame_card("s2", "segment", {"mode": "filter", "column": "kind", "cells": [{"label": "가", "value": "x"}]}, link_to=["c1", "c2"])
    assert dash_store.check_card(filter_seg)["view"]["link_to"] == ["c1", "c2"]


def test_new_frames_check():
    assert dash_store.FRAME_KINDS[-3:] == ("segment", "chip_row", "filter_bar")
    seg = dash_store.check_frame({"cells": [{"label": "가", "value": "x"}]}, "segment")
    assert seg == {"mode": "view", "column": "", "cells": [{"label": "가", "series": "", "share": False, "value": "x"}], "ambient": False}
    assert dash_store.check_frame(None, "chip_row") == {"column": "", "ambient": False}
    assert dash_store.check_frame({"clear_label": " 모두 풀기 "}, "filter_bar") == {"clear_label": "모두 풀기", "ambient": False}
    for frame, word in (({"mode": "tabs"}, "mode"), ({"cells": [{"label": "가"}] * (dash_store.MAX_SEG_CELLS + 1)}, "cells"),
                        ({"cells": [{"label": "가", "color": "#000"}]}, "쓸 수 없는"), ({"cells": [{"label": "가", "share": "yes"}]}, "share")):
        with pytest.raises(ValueError, match=word):
            dash_store.check_frame(frame, "segment")
    with pytest.raises(ValueError):
        dash_store.check_frame({"cells": [{"label": " "}]}, "segment")  # 칸 글자는 꼭 적는다


def test_period_frames_keep_values():
    assert dash_store.check_frame({}, "period_pick") == {}  # 키가 없으면 "안 바꾼 요소"
    assert dash_store.check_frame({"period": " 지난달 "}, "period_pick") == {"period": "지난달"}
    assert dash_store.check_frame({"period": ""}, "period_pick") == {"period": ""}  # 비운 기간도 바꾼 값
    assert dash_store.check_frame({"as_of": "2026-01-31"}, "asof_field") == {"as_of": "2026-01-31"}
    assert dash_store.check_frame({"as_of": ""}, "asof_field") == {"as_of": ""}
    assert dash_store.check_frame({"unit": "month"}, "unit_seg") == {"unit": "month"}
    top = dash_store.check_frame({"parts": ["period", "asof"], "items": [], "period": "최근 7일", "as_of": ""}, "top_strip")
    assert top == {"parts": ["period", "asof"], "items": [], "period": "최근 7일", "as_of": ""}
    for frame, kind in (({"as_of": "어제"}, "asof_field"), ({"unit": "hour"}, "unit_seg"), ({"period": "가" * (dash_store.MAX_ELEM_PERIOD + 1)}, "period_pick"), ({"as_of": "2026-01-01"}, "period_pick")):
        with pytest.raises(ValueError):
            dash_store.check_frame(frame, kind)


def test_link_defaults_fills_old_dashboards():
    cards = [{"id": "c1", "kind": "chart", "view": {"cross": True}},
             {"id": "c2", "kind": "kpi", "view": {}},
             {"id": "c3", "kind": "chart", "view": {"cross": True, "link_to": []}},
             {"id": "p1", "kind": "period_pick", "view": {"frame": {}}},
             {"id": "t1", "kind": "top_strip", "view": {"frame": {"parts": ["name", "theme"], "items": []}}},
             {"id": "t2", "kind": "top_strip", "view": {"frame": {"parts": ["asof"], "items": []}}},
             {"id": "b1", "kind": "button", "view": {"frame": {}}}]
    out = dash_store.link_defaults(cards)
    assert [c["id"] for c in out] == [c["id"] for c in cards]
    assert {c["id"]: c["view"].get("link_to") for c in out} == {"c1": ["c1", "c2", "c3"], "c2": None, "c3": [], "p1": ["c1", "c2", "c3"], "t1": None, "t2": ["c1", "c2", "c3"], "b1": None}  # 차트는 자신 포함
    assert "link_to" not in cards[0]["view"]  # 받은 목록은 건드리지 않는다


def test_check_dashboard_moves_old_links():
    old = [{**card("orders", "kind", SUM), "id": "c1", "view": {"cross": True}}, {**card("orders", None, SUM, kind="kpi"), "id": "c2"}]
    saved = dash_store.check_dashboard({"id": "d1", "name": "d", "cards": old})
    assert next(c for c in saved["cards"] if c["id"] == "c1")["view"]["link_to"] == ["c1", "c2"]
    assert saved["press_chips"] is True  # 옛 대시보드는 누른 칩을 지금처럼 보인다
    assert dash_store.check_dashboard({"id": "d1", "name": "d", "cards": old, "press_chips": False})["press_chips"] is False
    with pytest.raises(ValueError, match="press_chips"):
        dash_store.check_dashboard({"id": "d1", "name": "d", "cards": old, "press_chips": "no"})


# ---------- 맞춘 컬럼으로 거르기(parse.filter_as) ----------

def test_filter_as_keeps_clashing_columns():
    t = ibis.memtable({"area": ["B", "B", "A"], "price": [50.0, 5.0, 70.0], "amt": [1.0, 100.0, 2.0]})
    out = parse.filter_as(t, {"as": {"store": "area", "amt": "price"}, "where": ["store = B", "amt >= 10"], "periods": []})
    assert list(out.columns) == ["area", "price", "amt"]
    assert out.to_pyarrow().to_pylist() == [{"area": "B", "price": 50.0, "amt": 1.0}]  # amt는 원래 값(가격으로 바꿔 건 것은 조건에서만)
    swapped = parse.filter_as(t, {"as": {"price": "amt", "amt": "price"}, "where": ["price >= 10"], "periods": []})
    assert swapped.to_pyarrow().to_pylist() == [{"area": "B", "price": 5.0, "amt": 100.0}]  # 서로 바꾼 맞춤도 원래 값으로
    with pytest.raises(ValueError):
        parse.filter_as(t, {"as": {"store": "nope"}, "where": ["store = B"], "periods": []})


def test_predicate_rejects_as_item():
    t = ibis.memtable({"a": [1]})
    with pytest.raises(ValueError, match="filter_as"):
        parse.predicate({"as": {"x": "a"}, "where": ["x = 1"], "periods": []}, t)


# ---------- 타일의 조건 그대로 거는 필터({body, as?, periods?}) ----------

def test_tile_filter_narrows_other_card(db):
    got = compute(db, card=card("orders", "kind", SUM), ctx={"filters": [{"body": tile_body("store = B", "amt >= 10")}]})["data"]
    assert by_label(got) == {"y": 50} and "필터" not in got["note"]  # 타일 조건끼리는 and: B이면서 10 이상
    both = compute(db, card=card("orders", "kind", SUM), ctx={"filters": [{"body": tile_body("amt >= 10")}, {"column": "kind", "in": ["x"]}]})["data"]
    assert by_label(both) == {"x": 10}  # 타일 필터와 값 필터도 and(겹치면 모두 만족)


def test_tile_filter_needs_every_column_in_the_card(db):
    visits = card("visits", "kind", [{"y": "n", "agg": "sum"}])
    same = compute(db, card=visits, ctx={"filters": [{"body": tile_body("kind = x")}]})["data"]
    assert by_label(same) == {"x": 3}  # 다른 테이블이어도 그 컬럼이 있으면 걸린다(지금 값 필터와 같은 규칙)
    part = compute(db, card=visits, ctx={"filters": [{"body": tile_body("kind = x", "store = A")}]})["data"]
    assert by_label(part) == {"x": 3, "y": 4} and part["note"] == NOT_APPLIED  # 컬럼 하나라도 없으면 그 타일 필터는 통째로 안 걸고, 없는 컬럼만 적는다


def test_tile_filter_without_conditions_changes_nothing(db):
    plain = compute(db, card=card("orders", "kind", SUM))["data"]
    got = compute(db, card=card("orders", "kind", SUM), ctx={"filters": [{"body": tile_body()}]})["data"]
    assert by_label(got) == by_label(plain) == {"x": 16, "y": 70} and got["note"] == plain["note"]


def test_tile_filter_bad_shape(db):
    for bad in ({"body": "orders"}, {"column": "store", "in": ["A"], "body": tile_body("store = A")}, {},
                {"body": tile_body("store = A"), "as": ["store"]}, {"body": tile_body("store = A"), "periods": "yes"}):
        assert "모양" in compute(db, card=card("orders", "kind", SUM), ctx={"filters": [bad]})["error"]


def test_with_filters_tile_filter_on_card_without_query(db):
    c = {"source": {"recipe": {}}}
    assert dash_calc.with_filters(c, {"filters": [{"body": tile_body("store = A", "amt >= 1")}, {"column": "kind", "in": ["x"]}]}) == (c, ["amt", "store", "kind"])
    mapped = {"body": tile_body("store = A"), "as": {"store": "area"}}
    assert dash_calc.with_filters(c, {"filters": [mapped]}) == (c, ["area"])  # 맞춘 컬럼이면 상대 쪽 이름을 적는다


def test_tile_filter_mapped_to_other_table(db):
    shops = card("shops", "area", SUM)
    got = compute(db, card=shops, ctx={"filters": [{"body": tile_body("store = B", "amt >= 10"), "as": {"store": "area", "amt": "price"}}]})["data"]
    assert by_label(got) == {"B": 1} and "필터" not in got["note"]  # 지역 B이고 가격 10 이상인 줄의 amt(원래 값)
    plain = compute(db, card=shops, ctx={"filters": [{"body": tile_body("store = B")}]})["data"]
    assert by_label(plain) == {"A": 2, "B": 101} and plain["note"] == NOT_APPLIED  # 맞추지 않으면 같은 이름 규칙(없어서 안 걸림)
    missing = compute(db, card=shops, ctx={"filters": [{"body": tile_body("store = B"), "as": {"store": "nope"}}]})["data"]
    assert by_label(missing) == {"A": 2, "B": 101} and missing["note"] == "필터(nope)는 이 카드의 테이블에 그 컬럼이 없어 걸리지 않았어요."


def test_tile_filter_with_period_lines(db):
    sales = card("sales", "store", SUM)
    tile = {"base": "sales", "series": SUM, "conditions": [], "periods": ["day 2026-01-01 ~ 2026-01-01"]}
    with_lines = compute(db, card=sales, ctx={"filters": [{"body": tile, "periods": True}]})["data"]
    assert by_label(with_lines) == {"A": 1, "B": 4}  # 기간 줄도 걸기: 1일 하루
    cond_only = compute(db, card=sales, ctx={"filters": [{"body": tile}]})["data"]
    assert by_label(cond_only) == {"A": 3, "B": 4}  # 조건만: 조건이 없으니 아무 일 없음


# ---------- 타일이 읽는 컬럼, 링크 컬럼 ----------

def test_tile_reads(db):
    body = {"base": "sales", "series": SUM, "conditions": ["store = A", {"any": ["amt > 1", "amt < 0"]}], "periods": ["day 2026-01-01 ~ 2026-01-31"]}
    assert dash_calc.tile_reads(body) == {"conditions": ["amt", "store"], "periods": ["day"]}
    assert dash_calc.tile_reads({"base": "orders", "series": SUM}) == {"conditions": [], "periods": []}


def test_tile_data_has_reads(db):
    tile = {"kind": "tile", "title": "t", "source": {"schema": "", "table": "orders", "body": {"base": "orders", "series": SUM, "conditions": ["store = A"]}}}
    assert compute(db, card=tile)["data"]["reads"] == {"conditions": ["store"], "periods": []}


def test_linked_columns(db):
    target = {**card("shops", "area", SUM), "id": "t1", "view": {}}
    tile = {"id": "s1", "kind": "tile", "title": "s", "view": {"press": "cond", "link_to": ["t1"], "link_map": {"t1": {"store": "area"}}}, "source": {"body": tile_body("store = B", "amt >= 10")}}
    chart = {"id": "s2", "kind": "chart", "title": "c", "view": {"cross": True, "link_to": ["t1"]}, "source": {"body": {"base": "orders", "x": "kind", "series": SUM}}}
    off = {"id": "s3", "kind": "tile", "title": "o", "view": {"link_to": ["t1"]}, "source": {"body": tile_body("store = A")}}  # 누르기를 안 고른 타일은 보내지 않는다
    dash = {"id": "d", "cards": [target, tile, chart, off]}
    assert dash_calc.linked_columns(dash, target) == {"area", "amt", "kind"}  # 맞춘 컬럼은 상대 쪽 이름, 안 맞춘 것은 같은 이름
    assert dash_calc.linked_columns(dash, tile) == set()


def test_filter_values_narrowed_by_links(db):
    cards = [card("orders", "kind", SUM)]
    assert dash_calc.filter_values(cards, "kind") == (["x", "y"], False, [])
    assert dash_calc.filter_values(cards, "kind", [{"body": tile_body("store = C")}]) == (["x"], False, [])  # 칩 줄도 링크 필터를 받아 맞는 값만
    assert dash_calc.filter_values(cards, "kind", [{"column": "nope", "in": ["1"]}]) == (["x", "y"], False, ["nope"])
