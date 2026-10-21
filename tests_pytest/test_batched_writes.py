"""큰 결과를 묶음씩 받기·저장(result.batches, write_*): 파일 연결(Polars)은 LazyFrame에서 흘려 쓰고, Excel은 메모리 절약 모드로 묶음씩 쓴다.
지어낸 Parquet 파일에 Polars로 연결해 저장한 파일을 다시 읽어 맞춘다."""
import datetime as dt

import polars as pl
import pytest

from lazy import ibis

N = 2_500


@pytest.fixture
def table(tmp_path):
    frame = pl.DataFrame({"id": list(range(N)), "day": [dt.date(2026, 1, 1) + dt.timedelta(days=i % 30) for i in range(N)],
                          "at": [dt.datetime(2026, 1, 1, i % 24) for i in range(N)], "memo": [None if i % 7 == 0 else f"m{i}" for i in range(N)],
                          "tags": [[i, i + 1] for i in range(N)]})
    frame.write_parquet(tmp_path / "t.parquet")
    con = ibis.polars.connect()
    con.read_parquet(tmp_path / "t.parquet", table_name="t")
    return con.table("t")


def test_batches_cover_all_rows_in_pieces(table):
    import result
    sizes = [b.num_rows for b in result.batches(table, size=1_000)]
    assert sum(sizes) == N and len(sizes) >= 3 and max(sizes) <= 1_000
    assert result.lazy(table) is not None


def test_parquet_and_csv_from_file_connection(table, tmp_path):
    import result
    result.write_parquet(table.filter(table.id >= 10), tmp_path / "o.parquet")
    assert pl.read_parquet(tmp_path / "o.parquet").height == N - 10
    result.write_csv(table, tmp_path / "o.csv")
    back = pl.read_csv(tmp_path / "o.csv")
    assert back.height == N and back["tags"][0] == "[0,1]"  # 목록은 JSON 글자로


def test_excel_written_in_pieces(table, tmp_path, monkeypatch):
    import result
    monkeypatch.setattr(result, "CHUNK", 700)  # 여러 묶음에 걸쳐 쓰게
    result.export(table.mutate(memo=ibis.ifelse(table.id == 1, "=1+1", table.memo)), tmp_path / "o.xlsx")
    back = pl.read_excel(tmp_path / "o.xlsx")
    assert back.height == N and back.columns == ["id", "day", "at", "memo", "tags"]
    first = back.row(0, named=True)
    assert first["memo"] is None and back["memo"][1] == "'=1+1"  # 빈 값은 빈 칸, 수식으로 읽힐 글자는 '를 붙인다
    assert str(back["day"][0])[:10] == "2026-01-01" and str(back["at"][5]).startswith("2026-01-01 05:00")
