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
