"""보안 감사 12B: 머리글 수식 막기(result.export), MySQL LOAD DATA LOCAL 끄기(drivers.prepare), 명세서 YAML 별칭 폭증 막기(profiler.load_spec)."""
import sys
import time
import types

import polars as pl
import pytest

from lazy import ibis


@pytest.mark.parametrize("engine", ["polars", "duckdb"])
def test_csv_header_formula_guarded(engine, tmp_path):
    from core import result
    frame = pl.DataFrame({"=1+1": [1, 2], "memo": ["=2+2", "ok"], "plain": [3, 4]})
    frame.write_parquet(tmp_path / "t.parquet")
    con = getattr(ibis, engine).connect()
    t = con.read_parquet(tmp_path / "t.parquet", table_name="t")
    result.export(t, tmp_path / "o.csv")
    lines = (tmp_path / "o.csv").read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split(",") == ["'=1+1", "memo", "plain"]
    assert "'=2+2" in lines[1]  # 값 막기는 그대로
    result.export(t, tmp_path / "raw.csv", guard=False)  # 수식 막기를 끄면 이름도 그대로
    assert (tmp_path / "raw.csv").read_text(encoding="utf-8-sig").splitlines()[0].startswith("=1+1")
    result.export(t, tmp_path / "o.parquet")  # Parquet은 이름을 바꾸지 않는다
    assert pl.read_parquet(tmp_path / "o.parquet").columns == ["=1+1", "memo", "plain"]


@pytest.mark.parametrize("url", ["mysql://u@h:3306/x", "singlestoredb://u@h:3306/x"])
def test_mysql_local_infile_off(url, monkeypatch):
    from core import drivers
    module = drivers.DRIVERS[url.split(":")[0]][1]
    monkeypatch.setitem(sys.modules, module, types.ModuleType(module))  # 드라이버가 깔리지 않은 PC에서도 불러오기만 통과
    assert drivers.prepare(url, "pw") == {"local_infile": False}


def test_spec_alias_bomb_rejected_fast(tmp_path):
    from core import profiler
    columns = "\n".join(f"  c{i}: {{}}" for i in range(2000))
    tables = "\n".join(f"  t{i}: {{columns: *c}}" for i in range(2000))
    path = tmp_path / "bomb.yaml"
    path.write_text(f"cols: &c\n{columns}\ntables:\n{tables}\n", encoding="utf-8")
    start = time.perf_counter()
    with pytest.raises(ValueError, match="개까지"):
        profiler.load_spec(path)
    assert time.perf_counter() - start < 1


def test_spec_normal_still_reads(tmp_path):
    from core import profiler
    path = tmp_path / "spec.yaml"
    path.write_text("tables:\n  orders:\n    description: 주문\n    columns:\n      id: {description: 번호, bytes: 8}\n", encoding="utf-8")
    assert profiler.load_spec(path)["tables"]["orders"]["columns"]["id"]["bytes"] == 8
