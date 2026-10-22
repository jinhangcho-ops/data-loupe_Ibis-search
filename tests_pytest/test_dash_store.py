"""대시보드 저장소(dash_store): DPAPI 암호화 왕복, 다른 계정(풀 수 없음)이면 locked, 예전 평문 옮기기, 상한, 원자적 쓰기(.bak),
recent_rows 값 거부, 연결 열쇠에 계정·비밀번호가 없음, 정의 지문, HEX·기준일 검사. 값은 테스트 안에서 지어낸다."""
import json
import os

import pytest

import credentials
import dash_store as store

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
    for where in ("source", "data"):
        for key in ("password", "User", "username"):
            body = {"source": {"body": {key: "x"}}} if where == "source" else {"data": {"values": {"a": [{key: "x"}]}}}
            with pytest.raises(ValueError, match="저장하지 않습니다"):
                store.check_card({**CARD, **body})


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
    for bad in ({"colors": {"series": {"합계": "빨강"}}}, {"view": {"type": "scatter"}}, {"view": {"unit": "hour"}}, {"layout": {"x": 8, "y": 0, "w": 6, "h": 4}},
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
    with pytest.raises(ValueError, match="겹칩니다"):
        store.check_dashboard(board(cards=[{**CARD, "id": "a1"}, {**CARD, "id": "a1"}]))
