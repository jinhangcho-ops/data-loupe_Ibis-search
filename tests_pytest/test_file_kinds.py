"""파일 찾기: 고른 종류(확장자)만, 하위 폴더는 고를 때만, 경로가 파일이면 그 파일 하나. JSON(줄마다·배열)과 Excel도 테이블로 연다.
데이터는 테스트 안에서 지어낸다."""
import json

import polars as pl
import pytest

import source

ROWS = [{"지역": "가", "값": 1}, {"지역": "나", "값": 2}]


@pytest.fixture
def files(tmp_path):
    df = pl.DataFrame(ROWS)
    df.write_parquet(tmp_path / "a.parquet")
    df.write_csv(tmp_path / "b.CSV")
    df.write_ndjson(tmp_path / "c.jsonl")
    (tmp_path / "d.json").write_text(json.dumps(ROWS, ensure_ascii=False), encoding="utf-8")
    df.write_excel(tmp_path / "e.xlsx")
    (tmp_path / "f.duckdb").write_bytes(b"")
    (tmp_path / "note.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    df.write_parquet(tmp_path / "sub" / "g.parquet")
    (tmp_path / ".hidden").mkdir()
    df.write_parquet(tmp_path / ".hidden" / "h.parquet")
    return tmp_path


def names(found):
    return [[p.name for p in group] for group in found]


def test_all_kinds_deep(files):
    assert names(source.find_files(files)) == [["f.duckdb"], ["a.parquet", "b.CSV", "c.jsonl", "d.json", "e.xlsx", "g.parquet"]]


def test_kinds_and_shallow(files):
    assert names(source.find_files(files, ["json", "excel"])) == [[], ["c.jsonl", "d.json", "e.xlsx"]]
    assert names(source.find_files(files, ["parquet"], deep=False)) == [[], ["a.parquet"]]


def test_path_is_one_file(files):
    assert names(source.find_files(files / "e.xlsx")) == [[], ["e.xlsx"]]
    assert names(source.find_files(files / "e.xlsx", ["csv"])) == [[], []]
    assert names(source.find_files(files / "note.txt")) == [[], []]


def test_open_json_and_excel(files):
    paths = [files / "c.jsonl", files / "d.json", files / "e.xlsx"]
    con = source.open_flat_files(paths)
    for name in source.table_names(paths):
        assert con.table(name).order_by("값").to_pyarrow().to_pylist() == ROWS


def test_api_files_checks_input(client, files):
    def post(**body):
        return client.post("/api/files", json=body).get_json()
    assert [len(x) for x in (post(path=str(files), kinds=["excel"], deep=False)[k] for k in ("files", "flat"))] == [0, 1]
    assert "종류" in post(path=str(files), kinds=[])["error"]
    assert "종류" in post(path=str(files), kinds=["exe"])["error"]
    assert post(path=str(files / "없음"))["error"] == "그런 폴더나 파일이 없습니다."
    assert post(path=str(files / "note.txt"))["error"] == "열 수 있는 파일이 아니거나 고른 종류가 아닙니다."
