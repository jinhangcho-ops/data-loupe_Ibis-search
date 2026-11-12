"""(감사49) CSV 결과를 DB·Kafka에 적재할 때 수식 막기 '를 떼고 원래 값을 넣는다. 값은 시험 안에서 지어낸다."""
import pyarrow as pa

from lazy import ibis

from collect import collect_store, load_job

VALUES = ["=1+2", " @x", "-inf", "-1500", "'=a", "plain", None]


def written(tmp_path):
    con = ibis.duckdb.connect()
    table = ibis.memtable(pa.table({"=n": VALUES, "v": list(range(len(VALUES))), "empty": [None] * len(VALUES)}, schema=pa.schema([("=n", pa.string()), ("v", pa.int64()), ("empty", pa.string())])))
    out = tmp_path / "r.csv"
    collect_store.write_output(con, table, "csv", out, tmp_path)
    return out


def test_csv_load_strips_formula_guard(tmp_path):
    out = written(tmp_path)
    assert "'=1+2" in out.read_text(encoding="utf-8-sig")  # 파일에는 수식 막기가 그대로
    with load_job.Batches(out, 100) as batches:
        got = pa.concat_tables(list(batches))
    assert got.column_names == ["=n", "v", "empty"]
    assert got.column("=n").to_pylist() == ["=1+2", " @x", "-inf", "-1500", "=a", "plain", None]  # 원래 '=a였던 값은 구분할 수 없어 같이 떼진다
    assert got.column("v").type == pa.int64()


def test_empty_columns_reads_unguarded_names(tmp_path):
    out = written(tmp_path)
    assert load_job.empty_columns(out, load_job.file_schema(out)) == {"empty"}
