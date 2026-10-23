"""업로드 기록: 적재마다 행·바이트·범위를 남기고, 지울 때 쓸 명령을 만든다(실행하지 않음).
DB는 지어낸 DuckDB 파일, Kafka는 가짜 Producer로 한다. 데이터는 테스트 안에서 지어낸다."""
import copy
import threading

import duckdb
import polars as pl
import pytest

from collect import collect_config
from collect import load_job
from collect import load_kafka
from collect import uploads

ROWS = pl.DataFrame({"id": [1, 2, 3, 4, 5], "name": ["a", "b", "c", "d", "e"]})


def config(target, **table):
    c = copy.deepcopy(collect_config.DEFAULT)
    c["load"]["target"] = target
    c["load"]["batch"] = 100
    c["load"]["table"].update(table)
    return c


def run(c, path):
    return load_job.run(c, path, "", threading.Event(), lambda **_: None)


@pytest.fixture
def result(tmp_path):
    path = tmp_path / "out.parquet"
    ROWS.write_parquet(path)
    return path


def test_table_with_load_id(folder, tmp_path, result):
    db = tmp_path / "wh.duckdb"
    c = config("table", connection={"kind": "folder", "file": str(db), "url": "", "user": ""}, name="items", how="new", load_id=True)
    run(c, result)
    e = uploads.entries()[0]
    assert (e["kind"], e["status"], e["rows"], e["bytes"], e["target"]["name"], e["resumed"]) == ("table", "done", 5, None, "items", False)
    load_id = e["range"]["load_id"]
    with duckdb.connect(str(db)) as con:
        assert con.execute(f"select count(*) from items where {uploads.LOAD_ID} = ?", [load_id]).fetchone()[0] == 5
    cmd = uploads.removal(e)
    assert cmd["text"] == f'DELETE FROM "items" WHERE "{uploads.LOAD_ID}" = \'{load_id}\';'

    # 덧붙이기(키로 건너뛰기, 적재 ID): 새 적재는 새 ID, 키 범위도 남는다
    more = tmp_path / "more.parquet"
    pl.DataFrame({"id": [5, 6, 7], "name": ["e", "f", "g"]}).write_parquet(more)
    run(config("table", connection={"kind": "folder", "file": str(db), "url": "", "user": ""}, name="items", how="append", key=["id"], load_id=True), more)
    e2 = uploads.entries()[0]
    assert e2["rows"] == 2 and e2["range"]["keys"] == {"id": [6, 7]} and e2["range"]["load_id"] != load_id

    # 적재 ID 없이 덧붙였으면 키 범위로 고르는 명령(주의 문구)
    more2 = tmp_path / "more2.parquet"
    pl.DataFrame({"id": [8, 9], "name": ["h", "i"], uploads.LOAD_ID: [None, None]}).cast({uploads.LOAD_ID: pl.String}).write_parquet(more2)
    run(config("table", connection={"kind": "folder", "file": str(db), "url": "", "user": ""}, name="items", how="append", key=["id"]), more2)
    e3 = uploads.entries()[0]
    assert uploads.removal(e3)["text"] == 'DELETE FROM "items" WHERE "id" BETWEEN 8 AND 9;' and "다른 행도" in uploads.removal(e3)["note"]


def test_append_needs_load_id_column(folder, tmp_path, result):
    db = tmp_path / "wh.duckdb"
    link = {"kind": "folder", "file": str(db), "url": "", "user": ""}
    run(config("table", connection=link, name="items", how="new"), result)
    with pytest.raises(ValueError):
        run(config("table", connection=link, name="items", how="append", load_id=True), result)
    assert uploads.entries()[0]["kind"] == "table" and uploads.entries()[0]["status"] == "done"  # 실패한 시작은 기록하지 않는다(Sink를 만들지 못함)


class FakeMsg:
    def __init__(self, p, o, value, key):
        self.p, self.o, self.v, self.k = p, o, value, key

    def partition(self):
        return self.p

    def offset(self):
        return self.o

    def value(self):
        return self.v

    def key(self):
        return self.k


class FakeProducer:
    def __init__(self):
        self.waiting, self.next = [], {0: 100, 1: 40}

    def produce(self, topic, body, key, on_delivery):
        self.waiting.append((body, key, on_delivery))

    def poll(self, _):
        pass

    def flush(self, _=None):
        for i, (body, key, report) in enumerate(self.waiting):
            p = i % 2
            report(None, FakeMsg(p, self.next[p], body, key))
            self.next[p] += 1
        self.waiting = []
        return 0


def test_kafka_offsets(folder, result, monkeypatch):
    monkeypatch.setattr(load_kafka, "producer", lambda item, password, on_error=None: FakeProducer())
    load_kafka.save("로컬", {"servers": "localhost:9092", "topic": "events"})
    c = config("kafka")
    c["load"]["kafka"].update(destination="로컬", key="id")
    run(c, result)
    e = uploads.entries()[0]
    assert (e["kind"], e["rows"], e["target"]["topic"], e["target"]["destination"]) == ("kafka", 5, "events", "로컬")
    assert e["range"]["partitions"] == {"0": [100, 102], "1": [40, 41]} and e["bytes"] > 0
    cmd = uploads.removal(e)
    assert '"offset": 103' in cmd["text"] and '"offset": 42' in cmd["text"] and "파티션 0: 100~102" in cmd["note"]


def test_file_log_and_routes(client, folder, result):
    uploads.log_file(result, 5)
    d = client.get("/api/uploads").get_json()
    e = d["entries"][0]
    assert e["kind"] == "file" and e["rows"] == 5 and e["bytes"] == result.stat().st_size and e["removal"]["text"] == str(result)
    assert client.post("/api/uploads/forget", json={"id": e["id"]}).get_json()["entries"] == []
    assert result.exists()  # 기록만 빼고 파일은 그대로


def test_legacy_config_gets_load_id_default():
    c = copy.deepcopy(collect_config.DEFAULT)
    del c["load"]["table"]["load_id"]
    assert collect_config.normalize(c)["load"]["table"]["load_id"] is False
