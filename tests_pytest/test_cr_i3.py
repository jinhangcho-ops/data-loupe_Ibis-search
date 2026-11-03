"""묶음 I3(지표 사전): 전환 계열 지표가 기간 밖 B도 찾도록 catalog_app이 기간을 뺀 표(whole)를 넘기고, 그 조회도 처리 용량 확인을 받는다.
데이터는 시험 안에서 지어낸다(DuckDB 파일 연결, 진짜 커널)."""
import duckdb
import pytest

S = {"사용자": "u", "시각": "ts", "이벤트": "ev", "단계 A": "cart", "단계 B": "buy"}
BODY = {"base": "ev", "joins": [], "conditions": [], "periods": ["ts 2026-10-01 ~ 2026-10-31"]}


@pytest.fixture
def c(folder, tmp_path):
    from core import held
    from core import server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table ev (u integer, ev varchar, ts timestamp)")
    raw.execute("insert into ev values (1, 'cart', '2026-10-31 23:00'), (1, 'buy', '2026-11-01 01:00'), (2, 'cart', '2026-10-10 00:00'), (2, 'buy', '2026-10-25 00:00')")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield client
    held.store["items"].clear()
    client.post("/api/disconnect", json={})


def compute(c, slots, entry="cvr"):
    r = c.post("/api/catalog/compute", json={"id": entry, "slots": slots, "body": BODY, "confirmed": True})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["value"]


def test_b_after_the_period_counts_as_converted(c):
    assert compute(c, S) == 1.0  # 10-31 23시에 담고 11-01에 산 1번도 전환(고치기 전엔 0.5)
    assert compute(c, S, "drop_off") == 0.0


def test_b_after_the_window_is_still_drop_off(c):
    assert compute(c, {**S, "기한(일)": 7}) == 0.5  # 2번은 15일 뒤에 사서 기한 밖


def test_scan_ask_checks_the_body_without_periods_too(c, monkeypatch):
    from core import server
    from metrics import catalog_app
    from metrics import metric_catalog
    seen = []
    monkeypatch.setattr(catalog_app, "stage", lambda body, keep, real=catalog_app.stage: (seen.append(body["periods"]), real(body, keep))[1])
    monkeypatch.setattr(server, "scan_gate", lambda body, make_sql, rows=None: make_sql() and None)
    assert catalog_app.scan_ask(metric_catalog.get("cvr"), S, BODY, {}) is None
    assert BODY["periods"] in seen and [] in seen
    seen.clear()
    assert catalog_app.scan_ask(metric_catalog.get("dau"), {"사용자": "u", "시각": "ts"}, BODY, {}) is None
    assert [] not in seen  # 전환 계열이 아니면 기간을 뺀 표를 읽지 않는다


def test_sentence_scan_ask_checks_period_free_body_for_conversion(monkeypatch):
    """문장·카드로 구하는 전환 계열 내 지표도 기간을 뺀 표를 읽으므로 그 body까지 처리 용량 확인을 받는다."""
    from query import sentence_exec
    from metrics import metric_catalog, my_metrics
    from core import server
    asked = []
    monkeypatch.setattr(server, "get_con", lambda: None)
    monkeypatch.setattr(server, "expr_sql", lambda con, e: "sql")
    monkeypatch.setattr(sentence_exec.querying, "filtered", lambda b: b)
    monkeypatch.setattr(server, "scan_gate", lambda req, make: asked.append(json.loads(req_key)) if False else asked.append(make()) and None)
    monkeypatch.setattr(my_metrics, "get", lambda ref: {"template": "catalog:cvr", "slots": {}})
    monkeypatch.setattr(metric_catalog, "is_catalog", lambda t: True)
    monkeypatch.setattr(metric_catalog, "get", lambda t: {"calc": "conversion" if "cvr" in t else "count"})
    body = {"base": "t", "periods": ["d 2026-10-01 ~ 2026-10-31"]}
    done = {"ok": True, "runs": [{"route": "metric", "ref": "m1", "body": body}]}
    monkeypatch.setattr(server, "scan_gate", lambda req, make: (asked.append(make()), None)[1])
    monkeypatch.setattr(server, "expr_sql", lambda con, b: b)
    assert sentence_exec.scan_ask(done, {}) is None
    assert asked == [body, {"base": "t", "periods": []}]
    asked.clear()
    monkeypatch.setattr(my_metrics, "get", lambda ref: {"template": "catalog:dau", "slots": {}})
    assert sentence_exec.scan_ask(done, {}) is None and asked == [body]
