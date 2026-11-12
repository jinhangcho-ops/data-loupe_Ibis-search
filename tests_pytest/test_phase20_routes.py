"""20단계 묶음 C: 차례(turn)를 기다리지 않는 경로(FREE)와 모든 /api 경로의 분류, 지울 쿠키(adv_open), 키 없는 덧붙이기의 이어 넣기 안내."""
import threading

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from collect import load_job
from core import server

WAIT = 1  # 초: 차례를 기다리지 않는 요청이 답해야 하는 시간
APPEND_NOTE = "키 컬럼이 없어 끊긴 묶음 하나가 겹쳐 들어갔을 수 있어요. 적재 ID로 행 수를 확인해 주세요."

# 연결(state["con"])·server.run을 쓰거나, 차례 안에서 도는 요청과 같은 메모리를 고쳐 차례를 기다려야 하는 경로.
# 새 /api 경로를 만들면 여기(차례를 기다림)나 server.FREE(연결을 쓰지 않음) 중 하나에 넣는다.
USES_CONNECTION = {
    "/api/analyze", "/api/catalog/compute", "/api/chart", "/api/chart/points", "/api/collect/open", "/api/columns", "/api/connect",
    "/api/dash/card/compute", "/api/dash/cards/compute", "/api/dash/open", "/api/dash/refresh/start", "/api/date_range",
    "/api/diag/dupes", "/api/diag/gaps", "/api/diag/hashed", "/api/diag/orphans", "/api/diag/quasi", "/api/diagnose", "/api/disconnect", "/api/dupes",
    "/api/export", "/api/export/tables", "/api/funcs/support", "/api/handoff", "/api/head", "/api/join_keys",
    "/api/labels", "/api/labels/apply", "/api/labels/apply_where", "/api/labels/drop", "/api/labels/rename", "/api/labels/setup", "/api/labels/vocab",
    "/api/llm", "/api/lookup/preview", "/api/metrics", "/api/metrics/run", "/api/nl2sql/ask", "/api/nl2sql/prompt",
    "/api/patterns", "/api/period_table", "/api/pivot", "/api/plan", "/api/prep/sorted", "/api/prep/table", "/api/preview",
    "/api/profile", "/api/profile/export", "/api/profile/full", "/api/prune", "/api/query", "/api/release", "/api/rowcount", "/api/run",
    "/api/scan", "/api/searches/upgrade", "/api/sentence/run", "/api/shape", "/api/spec", "/api/spec/table",
    "/api/sql", "/api/sql/check", "/api/sql/export", "/api/sql/plan", "/api/sql/run", "/api/sqls", "/api/state", "/api/summary",
    "/api/table_columns", "/api/tables", "/api/tabs/forget", "/api/tabs/register", "/api/values", "/api/virtual",
}


def test_every_api_route_is_free_or_uses_connection(client):
    rules = {rule.rule for rule in client.application.url_map.iter_rules() if rule.rule.startswith("/api/")}
    assert rules - server.FREE - USES_CONNECTION == set()  # 새 경로: FREE나 USES_CONNECTION에 넣는다
    assert USES_CONNECTION & server.FREE == set()
    assert USES_CONNECTION <= rules  # 없어진 경로는 목록에서도 뺀다
    assert not [path for path in server.FREE if "<" in path]  # FREE는 request.path 글자와 견줘서 <변수>가 든 경로는 맞지 않는다


def test_settings_do_not_wait_for_turn(client):
    done = []
    worker = threading.Thread(target=lambda: done.append(client.post("/api/settings", json={"theme": "dark"}).status_code))
    assert server.turn.acquire(timeout=WAIT)
    try:
        worker.start()
        worker.join(WAIT)
        assert done == [200]
    finally:
        server.turn.release()
        worker.join()
    assert {"/api/settings", "/api/recent", "/api/connections"} <= server.FREE


def test_page_clears_adv_open_cookie_when_not_saved(client):
    cookies = client.get("/").headers.getlist("Set-Cookie")
    assert any(cookie.startswith("adv_open=;") and "Max-Age=0" in cookie for cookie in cookies)


def load_config(db: str, key: list[str]) -> dict:
    return {"load": {"target": "table", "batch": 2, "unit": "batch", "retries": 0, "interval": 0,
                     "table": {"schema": "", "name": "items", "key": key, "how": "append", "load_id": False,
                               "connection": {"kind": "folder", "file": db, "url": "", "user": ""}},
                     "kafka": {"destination": "", "key": ""}}}


def resumed_messages(folder, key: list[str]) -> list[str]:
    """끊긴 적재의 기록이 남은 결과 파일을 이어 넣을 때 note가 받은 message들."""
    db = str(folder / "w.duckdb")
    with duckdb.connect(db) as con:
        con.execute("create table items (id bigint, v varchar)")
    out = folder / "r.parquet"
    pq.write_table(pa.table({"id": [1, 2, 3], "v": ["a", "b", "c"]}), out)
    config = load_config(db, key)
    load_job.write_record(out, {"sign": load_job.signature(config), "file": load_job.file_stamp(out), "rows": 0, "batches": 0, "inserted": 0, "skipped": 0, "failures": 0})
    messages = []
    load_job.run(config, out, "", threading.Event(), lambda **changed: messages.extend(m for m in [changed.get("message")] if m))
    with duckdb.connect(db) as con:
        assert con.execute("select count(*) from items").fetchone()[0] == 3  # 동작은 그대로
    return messages


def test_resumed_append_without_key_warns_about_repeated_batch(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "keyed").mkdir()
    assert resumed_messages(tmp_path / "plain", []) == ["이어 넣어요. " + APPEND_NOTE]  # 한 메시지로: 뒤의 안내가 앞의 것을 덮지 않는다
    assert resumed_messages(tmp_path / "keyed", ["id"]) == ["이어 넣어요."]
