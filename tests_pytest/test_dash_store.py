"""대시보드 저장소(dash_store): DPAPI 암호화 왕복, 다른 계정(풀 수 없음)이면 locked, 예전 평문 옮기기, 상한, 원자적 쓰기(.bak),
recent_rows 값 거부, 연결 열쇠에 계정·비밀번호가 없음, 정의 지문, HEX·기준일 검사. 값은 테스트 안에서 지어낸다."""
import json
import os

import pytest

from core import credentials
from core import dash_store as store

CARD = {"kind": "chart", "title": "매출", "source": {"schema": "", "table": "orders", "body": {"base": "orders", "x": "매장", "series": [{"y": "금액", "agg": "sum"}]}},
        "data": {"x": "매장", "values": {"labels": ["강남", "홍대"], "series": [{"name": "합계 (금액)", "values": [10, 20]}]}}}


def board(name="주간", cards=None, **more):
    return {"name": name, "cards": [CARD] if cards is None else cards, **more}


def test_dpapi_roundtrip_and_file_is_encrypted(folder):
    saved = store.save_dashboard(board("비밀 이름 대시보드", palette=["#fff", "#11706F"], as_of="2026-10-01"))
    raw = (folder / store.FILE).read_text(encoding="utf-8")
    assert json.loads(raw)["v"] == 1 and "비밀 이름" not in raw and "강남" not in raw and "orders" not in raw  # 이름도 값도 평문이 아니다
    found = store.load()
    assert found["locked"] is False and found["dashboards"][saved["id"]] == saved
    assert saved["palette"] == ["#fff", "#11706F"] and saved["as_of"] == "2026-10-01" and saved["created"] and saved["updated"]
    assert saved["cards"][0]["data"]["values"]["series"][0]["values"] == [10, 20]


def test_other_account_is_locked_and_file_kept(folder, monkeypatch):
    store.save_dashboard(board())
    before = (folder / store.FILE).read_bytes()
    monkeypatch.setattr(credentials, "reveal", lambda stored: None)  # 다른 PC·계정: 풀 수 없다
    assert store.load() == {"dashboards": {}, "locked": True}
    assert store.listing() == {"dashboards": [], "locked": True}
    with pytest.raises(ValueError, match="이 PC에서 만든"):
        store.save_dashboard(board("덮어쓰기"))
    with pytest.raises(ValueError, match="이 PC에서 만든"):
        store.delete_dashboard("x")
    assert (folder / store.FILE).read_bytes() == before  # 덮어쓰지도 지우지도 않는다


def test_broken_file_is_locked_not_overwritten(folder):
    (folder / store.FILE).write_text("깨진 파일", encoding="utf-8")
    assert store.load()["locked"] is True
    with pytest.raises(ValueError):
        store.save_dashboard(board())
    assert (folder / store.FILE).read_text(encoding="utf-8") == "깨진 파일"


def test_atomic_write_keeps_one_backup(folder, monkeypatch):
    first = store.save_dashboard(board("하나"))
    store.save_dashboard(board("둘"))
    older = json.loads((folder / (store.FILE + ".bak")).read_text(encoding="utf-8"))
    assert older["v"] == 1 and list(json.loads(credentials.reveal(older["blob"]))["dashboards"].values())[0]["name"] == "하나"  # 바꾸기 전 파일이 .bak
    before = (folder / store.FILE).read_bytes()
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("전원이 나감")))  # 바꿔치기 직전에 꺼짐
    with pytest.raises(OSError):
        store.save_dashboard(board("셋"))
    monkeypatch.undo()
    assert (folder / store.FILE).read_bytes() == before and {d["name"] for d in store.load()["dashboards"].values()} == {"하나", "둘"}
    assert first["id"] in store.load()["dashboards"]


def test_plain_file_migrated_not_deleted(folder):
    chart = {"title": "매장별", "ratio": 1.5, "ranges": {"y": {"min": 0}, "y1": {}}, "query": {"schema": "dbo", "base": "orders", "x": "매장", "series": [{"y": "금액", "agg": "sum"}]},
             "used": [{"y": "금액", "agg": "sum", "type": "line", "dash": "dashed", "axis": "right"}]}
    metric = {"title": "지표", "ratio": 1.0, "ranges": {}, "query": {"base": "orders", "names": ["전환율"]}, "used": ["전환율"], "kind": "metric"}
    original = json.dumps({"예전": {"charts": [chart, metric]}, "빈것": {"charts": []}}, ensure_ascii=False)
    (folder / store.PLAIN).write_text(original, encoding="utf-8")
    found = store.load()["dashboards"].values()
    old = next(d for d in found if d["name"] == "예전")
    assert [c["kind"] for c in old["cards"]] == ["chart", "metric"] and old["cards"][0]["data"] == {}
    assert old["cards"][0]["source"]["table"] == "orders" and old["cards"][0]["source"]["schema"] == "dbo" and old["cards"][0]["source"]["body"]["x"] == "매장"
    assert old["cards"][0]["view"]["styles"] == [{"type": "line", "dash": "dashed", "axis": "right"}] and old["cards"][0]["view"]["ratio"] == 1.5
    assert not (folder / store.PLAIN).exists() and (folder / (store.PLAIN + ".migrated")).read_text(encoding="utf-8") == original  # 이름만 바뀌고 지워지지 않는다
    assert (folder / store.FILE).exists() and len(store.load()["dashboards"]) == 2


def test_limits(folder, monkeypatch):
    with pytest.raises(ValueError, match="48개"):
        store.save_dashboard(board(cards=[CARD] * (store.MAX_CARDS + 1)))
    store.save_dashboard(board(cards=[CARD] * store.MAX_CARDS))  # 정확히 상한은 된다
    with pytest.raises(ValueError, match="상한 400,000자"):
        store.save_dashboard(board("큼", cards=[{**CARD, "data": {"values": {"x": "가" * store.MAX_CARD_BYTES}}}]))
    monkeypatch.setattr(store, "MAX_DASHBOARDS", 2)
    store.save_dashboard(board("둘째"))
    with pytest.raises(ValueError, match="2개까지"):
        store.save_dashboard(board("셋째"))
    monkeypatch.setattr(store, "MAX_STORE_BYTES", 100)
    with pytest.raises(ValueError, match="상한"):
        store.save_dashboard(next(iter(store.load()["dashboards"].values())))
    assert len(store.load()["dashboards"]) == 2  # 넘긴 저장은 반영되지 않았다


def test_recent_rows_values_rejected():
    ok = {"kind": "recent_rows", "title": "최근 주문", "data": {"computed_at": "2026-10-01T09:00:00", "columns": ["id", "금액"]}}
    assert store.check_card(ok)["data"]["columns"] == ["id", "금액"]
    for bad in ({"rows": [[1, 100]]}, {"values": {"rows": [[1, 100]]}}, {"by_unit": {}}):
        with pytest.raises(ValueError, match="행 값을 저장할 수 없"):
            store.check_card({**ok, "data": bad})


def test_secrets_never_stored():
    for key in ("password", "User", "username"):
        with pytest.raises(ValueError, match="저장하지 않아요"):
            store.check_card({**CARD, "source": {"body": {key: "x"}}})
        with pytest.raises(ValueError, match="쓸 수 없는 항목"):  # data의 최상위 키는 허용 목록이 막는다
            store.check_card({**CARD, "data": {key: "x"}})
        assert store.check_card({**CARD, "data": {"values": {"a": [{key: "x"}]}}})["data"]["values"]  # 그룹 값 안은 사용자 데이터라 허용


def test_source_key_has_no_account_or_password():
    a = {"kind": "url", "url": "postgres://kim:pw1@DB.example.com:5432/sales?sslmode=require", "user": "kim"}
    b = {"kind": "url", "url": "postgres://lee:other@db.example.com:5432/sales", "user": "lee"}
    assert store.source_key(a) == store.source_key(b) == "url:postgres://db.example.com:5432/sales"
    assert store.source_key({"kind": "url", "url": "postgres://db.example.com:5433/sales"}) != store.source_key(a)  # port가 다르면 다른 연결
    assert store.source_key({"kind": "url", "url": "postgres://db.example.com:5432/other"}) != store.source_key(a)  # database가 다르면 다른 연결
    for key in (store.source_key(a), store.source_key(b)):
        assert "kim" not in key and "lee" not in key and "pw1" not in key and "other" not in key
    assert store.source_label(a) == "db.example.com/sales"
    assert store.source_key({"kind": "folder", "path": "C:\\Data\\Sales"}) == store.source_key({"kind": "folder", "path": "c:/data/sales"})  # 소문자·정규화
    assert store.source_label({"kind": "folder", "path": "C:\\Data\\Sales"}) == "Sales"


def test_def_hash_ignores_key_order():
    assert store.def_hash({"a": 1, "b": [1, 2]}) == store.def_hash({"b": [1, 2], "a": 1})
    assert store.def_hash({"a": 1}) != store.def_hash({"a": 2}) and len(store.def_hash({"a": 1})) == 16 and store.def_hash({"a": "한글"})


@pytest.mark.parametrize("palette", [["red"], ["#12"], ["#1234567"], ["#ggg"], ["#fff"] * 13, "#fff"])
def test_bad_palette(palette):
    with pytest.raises(ValueError):
        store.check_dashboard(board(palette=palette))


def test_colors_view_and_layout_checks():
    card = store.check_card({**CARD, "colors": {"series": {"합계": "#abc"}, "order": ["합계"]}, "view": {"type": "area", "unit": "month"}, "layout": {"x": 6, "y": 2, "w": 6, "h": 4}})
    assert card["colors"] == {"series": {"합계": "#abc"}, "parts": {}, "order": ["합계"], "parts_order": []} and card["view"]["type"] == "area"
    for bad in ({"colors": {"series": {"합계": "빨강"}}}, {"view": {"type": "pie3d"}}, {"view": {"unit": "hour"}}, {"layout": {"x": 8, "y": 0, "w": 6, "h": 4}},
                {"layout": {"x": 0, "y": 0, "w": 1, "h": 4}}, {"kind": "마법"}, {"colors": {"모름": {}}}):
        with pytest.raises(ValueError):
            store.check_card({**CARD, **bad})


@pytest.mark.parametrize("as_of, ok", [("", True), ("2026-02-28", True), ("2024-02-29", True), ("2026-02-30", False), ("2026-13-01", False), ("20261001", False), ("어제", False)])
def test_as_of(as_of, ok):
    if ok:
        assert store.check_dashboard(board(as_of=as_of))["as_of"] == as_of
    else:
        with pytest.raises(ValueError, match="기준일"):
            store.check_dashboard(board(as_of=as_of))


def test_north_star_must_point_to_cards():
    d = store.check_dashboard(board(cards=[{**CARD, "id": "a1"}, {**CARD, "id": "b2"}], north_star={"card": "a1", "counters": ["b2"]}))
    assert d["north_star"] == {"card": "a1", "counters": ["b2"]}
    with pytest.raises(ValueError, match="북극성"):
        store.check_dashboard(board(cards=[{**CARD, "id": "a1"}], north_star={"card": "zz", "counters": []}))
    with pytest.raises(ValueError, match="겹쳐요"):
        store.check_dashboard(board(cards=[{**CARD, "id": "a1"}, {**CARD, "id": "a1"}]))


def test_scatter_bubble_view_and_points_data():
    assert {"scatter", "bubble"} <= set(store.CHART_TYPES) and len(store.CHART_TYPES) == 11 and not {"scatter", "bubble"} & set(store.TIME_TYPES)
    card = store.check_card({**CARD, "view": {"type": "bubble", "points": {"size": "금액"}, "trend": True, "show_r": False}, "data": {"x_kind": "num", "num_cols": ["금액"], "points": {"points": [[1, 2, 3]]}}})
    assert card["view"]["points"] == {"size": "금액"} and card["view"]["trend"] is True and card["data"]["points"]["points"] == [[1, 2, 3]]
    for bad in ({"points": {"size": "a", "x": 1}}, {"trend": "yes"}, {"points": "금액"}):
        with pytest.raises(ValueError):
            store.check_card({**CARD, "view": {"type": "scatter", **bad}})


def test_card_kind_view_settings_checks():
    v = store.check_card({**CARD, "kind": "donut_kpi", "view": {"ring": "half", "format": "percent", "icon": "cart", "unit_text": "원", "cells": 20, "size": 100, "rows": 10, "group": "매장",
                                                                  "target": {"name": "매출", "period": "2026-10"}, "window": {"from": "2026-01-01", "to": "2026-02-01"},
                                                                  "slots": [{"label": "실제", "format": "currency", "color": "#abc"}, {"label": "목표"}]}})["view"]
    assert v["ring"] == "half" and v["target"] == {"name": "매출", "period": "2026-10"} and v["slots"][1] == {"label": "목표", "format": "number", "unit_text": "", "color": ""} and v["window"]["to"] == "2026-02-01"
    assert store.check_card({**CARD, "view": {"target": {"value": 12.5}}})["view"]["target"] == {"value": 12.5}
    for bad in ({"ring": "square"}, {"format": "euro"}, {"icon": "x"}, {"target": {"value": 1, "name": "a"}}, {"target": {}}, {"target": {"value": "많이"}}, {"cells": 0}, {"slots": [{}] * 6},
                {"slots": [{"color": "red"}]}, {"window": {"from": "어제", "to": "2026-01-01"}}, {"rows": 101}):
        with pytest.raises(ValueError):
            store.check_card({**CARD, "view": bad})
    assert store.check_card({**CARD, "source": {**CARD["source"], "date": "day"}})["source"]["date"] == "day"


def test_table_status_map_sentence_view_settings():
    v = store.check_card({**CARD, "kind": "map", "view": {"cond_rules": [{"column": "상태", "match": "*지연*", "color": "#c00", "arrow": "auto", "scope": "row"}], "rules": [{"column": "cpu", "op": ">=", "value": 90, "level": "bad"}],
                                                          "stale": 30, "entity": "name", "links": [["a", "b"]], "positions": {"a": [0.1, 0.9]}, "bg": "network", "group_cols": ["store"], "formats": {"금액": "currency", "x": "엉터리"},
                                                          "total": 1, "blanks": [{"name": "매출", "y": "amt", "agg": "sum", "kind": "delta"}], "template": "{매출}", "bucket": "hour", "baseline": 3, "threshold": 1.5}})["view"]
    assert v["cond_rules"] == [{"column": "상태", "match": "*지연*", "color": "#c00", "arrow": "auto", "scope": "row"}] and v["positions"] == {"a": [0.1, 0.9]} and v["formats"] == {"금액": "currency"} and v["total"] is True
    assert v["blanks"][0]["kind"] == "delta" and v["rules"][0]["level"] == "bad" and store.check_card({**CARD, "kind": "status"})["kind"] == "status"
    for bad in ({"cond_rules": [{"column": "a", "match": "1", "color": "red"}]}, {"cond_rules": [{"column": "", "match": "1", "color": "#fff"}]}, {"rules": [{"column": "a", "op": "~", "value": 1, "level": "bad"}]},
                {"positions": {"a": [2, 0]}}, {"positions": {"a": [0.1]}}, {"links": [["a"]]}, {"bg": "sky"}, {"bucket": "year"}, {"group_cols": ["a", "b", "c", "d"]}, {"blanks": [{}] * 11}, {"stale": 0}):
        with pytest.raises(ValueError):
            store.check_card({**CARD, "view": bad})
    assert store.check_card({**CARD, "kind": "alerts", "data": {"since": {"a": "2026-10-22T12:00:00"}}})["data"]["since"] == {"a": "2026-10-22T12:00:00"}



def test_source_key_for_file_urls_all_spellings():
    """sqlite·duckdb 주소는 어떻게 써도(슬래시 3개·역슬래시·대소문자·드라이브 글자) 같은 파일이면 같은 열쇠고, 예외를 내지 않는다. 계정·비밀번호는 없다."""
    key = lambda url: store.source_key({"kind": "url", "url": url})
    forms = ["sqlite:///C:/data/shop.db", "sqlite:///C:\\data\\shop.db", "sqlite://C:\\data\\shop.db", "sqlite://C:/data/shop.db", "SQLITE:///c:/Data/Shop.db"]
    assert {key(u) for u in forms} == {"url:sqlite://c:/data/shop.db"}
    assert key("duckdb:///C:/data/x.duckdb") == key("duckdb://C:\\data\\x.duckdb") == "url:duckdb://c:/data/x.duckdb"
    assert key("sqlite:///C:/data/shop.db") != key("duckdb:///C:/data/shop.db") and key("sqlite:///C:/data/a.db") != key("sqlite:///C:/data/b.db")
    here = os.path.abspath("/data/shop.db").replace("\\", "/").lower()
    assert key("sqlite:///data/shop.db") == "url:sqlite://" + here  # 드라이브 글자가 없으면 지금 드라이브의 절대 경로(ibis와 같다)
    assert key("sqlite://") == key("sqlite:///:memory:") == "url:sqlite://:memory:" and key("duckdb://") == "url:duckdb://:memory:"
    for odd in ("", "nonsense", "postgres://kim:pw@h:abc/db", "mssql+pyodbc://kim:pw@h/db?x=1", "sqlite://C:\\한글\\쇼핑.db", "://", "sqlite://@", "sqlite://\\\\srv\\share\\a.db"):
        got = key(odd)  # 예외 없음
        assert "pw" not in got and "kim" not in got
    assert key("postgres://kim:pw@h:abc/db") == "url:postgres://h:abc/db"  # 포트를 읽을 수 없어도 예외 없이
    assert key("postgres://kim:pw@db.example.com:5432/sales") == "url:postgres://db.example.com:5432/sales"  # 네트워크 주소는 그대로


def test_old_file_keys_are_still_matched(folder):
    """예전 모양의 열쇠(url:sqlite://:/C:/data/shop.db)로 저장한 카드·실시간 최소는 지금 열쇠로 이어진다."""
    old = "url:sqlite://:/C:/data/shop.db"
    new = store.source_key({"kind": "url", "url": "sqlite:///C:/data/shop.db"})
    assert store.canon_key(old) == new and store.canon_key(new) == new and store.canon_key("folder:c:/x") == "folder:c:/x" and store.canon_key(None) is None
    assert store.check_card({**CARD, "source": {**CARD["source"], "key": old}})["source"]["key"] == new
    d = store.check_dashboard(board(cards=[CARD]))
    d["cards"][0]["source"]["key"] = old  # 예전에 저장된 파일 그대로
    store.write({"d2": {**d, "id": "d2"}})
    assert store.load()["dashboards"]["d2"]["cards"][0]["source"]["key"] == new
    from dashboard import dash_refresh
    dash_refresh.set_live_min(old, 60)
    assert dash_refresh.live_min(new) == 60 and dash_refresh.live_min(old) == 60 and list(dash_refresh.live_mins()) == [new]
