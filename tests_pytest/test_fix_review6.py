"""6차 마지막 리뷰에서 찾은 것의 시험. 값은 시험 안에서 지어냈다."""
from core import server
from query import search_app


def test_explore_gate_passes_the_plain_row_count(monkeypatch):
    """세어 둔 행 수는 (수, CSV 변경 횟수) 묶음으로 둔다: 용량 확인에는 수만 넘겨야 한다(묶음이 가면 곱셈에서 오류)."""
    seen = {}
    monkeypatch.setattr(search_app, "get_con", lambda: object())
    monkeypatch.setattr(search_app.held, "remote", lambda con: True)
    monkeypatch.setattr(server, "scan_gate", lambda body, make, rows=None: seen.setdefault("rows", rows))
    monkeypatch.setitem(server.state["rows"], (None, "orders"), (600, {}))
    search_app.explore_gate({"base": "orders", "fresh": True})
    assert seen["rows"] == 600


def test_dash_store_rereads_after_its_own_write(folder):
    """같은 시각 단위 안에 같은 크기로 다시 쓰면 (경로, mtime, 크기)가 같아 옛 캐시를 쓰던 문제: 최신화가 쓴 값이 다음 저장에서 옛 값으로 덮였다(CI에서 가끔)."""
    import os
    from core import dash_store as store
    old = {"computed_at": "2026-01-01T09:00:00", "def_hash": "h1", "values": {"labels": ["old"], "series": []}}
    card = {"kind": "chart", "title": "가", "source": {"key": "k", "label": "x", "table": "t", "body": {"base": "t", "x": "a", "series": [{"y": None, "agg": "count"}]}}, "data": old}
    d = store.save_dashboard({"name": "캐시", "cards": [card]})
    path = store.path_of()
    before = path.stat()
    store.load()  # 지금 파일을 캐시에 올린다
    fresh = {**old, "computed_at": "2026-01-01T10:00:00", "values": {"labels": ["new"], "series": []}}
    assert store.set_cards_data(d["id"], [(d["cards"][0]["id"], fresh, None)]) == [True]
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))  # 시각이 같은 틱에 든 것처럼
    assert path.stat().st_size == before.st_size  # 'old'와 'new'는 길이가 같아 크기도 같다
    assert store.load()["dashboards"][d["id"]]["cards"][0]["data"]["values"]["labels"] == ["new"]
