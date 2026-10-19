"""테이블마다 마지막에 쓴 전처리를 기억했다가, 같은 테이블(같은 연결·스키마·이름)을 열면 돌려주는지."""
import prep_app

CONFIG = {"schema": "", "base": "orders", "edits": {"name": {"keep": True, "rename": "", "ops": {"trim": {}}}}, "copies": [], "drops": [], "filters": [],
          "dedupe": [], "sorts": [], "downcast": True, "target": {"kind": "file", "format": "parquet", "name": ""}}


def connect(client, folder, name):
    assert client.post("/api/connect", json={"kind": "folder", "path": str(folder / name), "file": "x.duckdb"}).status_code == 200


def remembered(client, base, schema=""):
    return client.get("/api/prep/table", query_string={"schema": schema, "base": base}).get_json()["config"]


def test_same_table_gets_saved_prep(client, folder):
    connect(client, folder, "a")
    assert remembered(client, "orders") is None
    assert client.post("/api/prep/table", json={"schema": "", "base": "orders", "config": CONFIG}).get_json() == {"ok": True}
    assert remembered(client, "orders") == CONFIG
    assert remembered(client, "customers") is None
    assert remembered(client, "orders", schema="other") is None
    changed = {**CONFIG, "downcast": False}
    client.post("/api/prep/table", json={"schema": "", "base": "orders", "config": changed})
    assert remembered(client, "orders") == changed  # 마지막에 쓴 것으로 바뀐다
    connect(client, folder, "b")
    assert remembered(client, "orders") is None  # 다른 연결의 같은 이름 테이블은 다른 테이블
    connect(client, folder, "a")
    assert remembered(client, "orders") == changed  # 다시 열어도 남아 있다


def test_url_connection_key_has_no_password(client, folder):
    client.post("/api/connect", json={"kind": "url", "url": "postgres://u:Inline-P4ss@db.example.invalid/sales", "user": "", "password": "Field-S3cret!"})
    client.post("/api/prep/table", json={"schema": "", "base": "orders", "config": CONFIG})
    saved = (folder / prep_app.TABLE_PREPS).read_text(encoding="utf-8")
    assert "Inline-P4ss" not in saved and "Field-S3cret!" not in saved


def test_needs_connection_and_valid_config(client, folder):
    assert "error" in client.get("/api/prep/table", query_string={"base": "orders"}).get_json()  # 연결 전
    connect(client, folder, "a")
    assert "error" in client.post("/api/prep/table", json={"base": "orders", "config": "글자"}).get_json()
    assert "error" in client.post("/api/prep/table", json={"base": "", "config": CONFIG}).get_json()


def test_kept_tables_are_capped(client, folder, monkeypatch):
    monkeypatch.setattr(prep_app, "MAX_TABLE_PREPS", 3)
    connect(client, folder, "a")
    for n in range(5):
        client.post("/api/prep/table", json={"schema": "", "base": f"t{n}", "config": CONFIG})
    assert [remembered(client, f"t{n}") is not None for n in range(5)] == [False, False, True, True, True]  # 오래전에 쓴 것부터 뺀다
