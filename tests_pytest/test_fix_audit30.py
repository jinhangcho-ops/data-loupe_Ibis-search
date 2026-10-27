"""30차 감사: 16번 문장의 남의 조건, 가공한 내 지표의 조합, 흐름의 공백 컬럼(검사·규칙 4), robots를 창보다 먼저, 복사의 컬럼 바꾸기, 누적 desc."""
import threading

import duckdb
import pytest

from collect import collect_browser, collect_config, collect_store, collect_web
from collect.collect_http import Halt
from metrics import my_metrics
from query import sentence_exec, sentence_run


def test_sentence_16_uses_only_its_own_tables():
    body = {"base": "orders", "conditions": ["status = 'paid'"], "calcs": [], "periods": ["created_at 2024-01-01 ~ 2024-01-31"]}
    out = sentence_run.compile("e16", {"테이블 A": "users", "테이블 B": "events", "연결 컬럼": "uid"}, body, {"amount": "num", "status": "text"})
    assert out["ok"], out.get("reason")
    run = out["runs"][0]["body"]
    assert run["base"] == "users" and run["conditions"] == [] and run["periods"] == [] and run["joins"] == ["events inner uid=uid"]


def test_prepped_my_metric_works_inside_combined_sentence(client, tmp_path):
    from core import server
    from core.dash_store import current_source
    path = tmp_path / "x.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.execute("create table ev as select i::int as id, case when i % 2 = 0 then 'A@x' else 'a@x' end as email from range(4) t(i)")
    from lazy import ibis
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    cols, base = {"id": "num", "email": "text"}, {"base": "ev", "joins": [], "periods": [], "conditions": [], "calcs": []}
    m = my_metrics.create({"name": "uniq", "scope": {"key": current_source()["key"], "table": "ev"}, "template": "m02", "slots": {"대상": {"column": "email", "prep": "lower"}}}, cols)
    rows = sentence_exec.execute_compiled(sentence_exec.prepare("e21", {"지표 A": m["id"], "지표 B": m["id"]}, base, cols))["rows"]
    assert list(rows[0].values()) == [1.0]

def test_robots_checked_before_window_opens(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(collect_browser, "launch", lambda url, show=True: events.append("window"))
    monkeypatch.setattr(collect_web, "robots_rules", lambda base: events.append("robots") or collect_web.robots_parse("User-agent: *\nDisallow: /private\n"))
    monkeypatch.setattr(collect_store, "balancer_for", lambda *a: None)
    monkeypatch.setattr(collect_store, "began", lambda record: None)
    config = collect_config.normalize({"mode": "web", "web": {"url": "https://example.test/private/list", "items": "div.item",
                                                              "fields": [{"name": "a", "selector": "", "attr": "text"}], "show": False, "robots": True}})
    with pytest.raises(Halt):
        collect_web.run_web(config, {}, tmp_path / "out.csv", {"part": tmp_path / "p", "record": tmp_path / "r.json"}, {"done": []}, threading.Event())
    assert events == ["robots"]


def test_rename_in_handles_spaced_names_and_keeps_quoted_values():
    assert my_metrics.rename_in("order date >= '2024-01-01'", {"order date": "주문일"}) == "주문일 >= '2024-01-01'"
    assert my_metrics.rename_in("category = 'sales'", {"sales": "revenue", "category": "cat"}) == "cat = 'sales'"


def test_cumsum_desc_runs_backwards():
    a = {"keys": ["day"], "col": "v", "more": False, "rows": [{"day": 1, "v": 1}, {"day": 2, "v": 2}, {"day": 3, "v": 3}]}
    out = sentence_exec.combine({"op": "cumsum", "a": "a", "reverse": True}, {"a": a})
    assert [r["누적"] for r in out["rows"]] == [3, 5, 6]
