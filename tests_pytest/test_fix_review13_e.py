"""13차 묶음 E: 목록·구조 안 큰 정수, 스키마.테이블로 쓴 파일 이름 막기, 임시 폴더 만들기·지우기 경합, 빈 경로 파일 찾기."""
import json
import threading

import duckdb
import polars as pl
import pytest

from core import scratch, server, sql_exec

BIG = 9007199254740993  # 2^53 + 1: JSON.parse가 9007199254740992로 바꾼다


def test_big_int_inside_struct_and_list_is_text():
    df = pl.DataFrame({"id": [BIG], "s": [{"id": BIG, "n": 1}], "l": [[BIG, 2]], "small": [[1, 2]]})
    rows = server.to_json(df)["rows"]
    assert rows == [{"id": str(BIG), "s": {"id": str(BIG), "n": "1"}, "l": [str(BIG), "2"], "small": [1, 2]}]
    assert json.loads(server.json_reply(df).get_data())["rows"]["rows"] == rows
    assert server.to_json(df, text_big=False)["rows"][0]["s"] == {"id": BIG, "n": 1}  # 산술용은 숫자 그대로


def test_big_int_with_binary_inside_struct():
    df = pl.DataFrame({"s": [{"b": b"\x01", "id": -BIG}, None]})
    assert server.to_json(df)["rows"] == [{"s": {"b": "01", "id": str(-BIG)}}, {"s": None}]


@pytest.mark.parametrize("q", ["select * from t.csv", 'select * from "t.tsv"', "select * from t.tsv", 'select * from "t.csv.gz"',
                               "select * from t.csv.gz", "select * from a.b.parquet", "select * from x.TXT", "select * from t.zst"])
def test_file_table_names_blocked(q, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "t.csv").write_text("id\n1\n", encoding="utf-8")
    if q == "select * from t.csv":
        assert duckdb.sql(q).fetchall() == [(1,)]  # 막지 않으면 DuckDB가 작업 폴더의 파일을 읽는다
    with pytest.raises(Exception, match="파일"):
        sql_exec.check(q, "duckdb")


@pytest.mark.parametrize("q", ["select * from main.orders", "select * from sales.public.t", "select * from orders", 'select * from "csv"'])
def test_normal_table_names_pass(q):
    sql_exec.check(q, "duckdb")


def test_make_and_remove_race(tmp_path, monkeypatch):
    monkeypatch.setattr(scratch, "ROOT", tmp_path / "root")
    errors = []

    def loop():
        for _ in range(2000):
            try:
                scratch.remove(scratch.make())
            except OSError as e:
                errors.append(e)
    threads = [threading.Thread(target=loop) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and list(scratch.ROOT.iterdir()) == []


def test_files_empty_path(client):
    for body in ({"path": ""}, {}):
        assert client.post("/api/files", json=body).get_json()["error"] == "폴더나 파일 경로를 입력하세요."
