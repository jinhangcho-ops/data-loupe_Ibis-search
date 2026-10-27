"""36차 감사: 수집 파일 모드의 BOM JSON·UTF-16·뒤늦은 글자 CSV, Excel 긴 글자·이진 값, GA4 데이터 손실 안내."""
import json
import os

import polars as pl
import pyarrow.parquet as pq
import pytest
from lazy import ibis

from collect import collect_job
from collect.collect_http import Halt
from core import result
from integrations.adapters import ga4


@pytest.fixture
def finish(tmp_path, monkeypatch):
    monkeypatch.setattr(collect_job, "update", lambda **k: None)
    monkeypatch.setattr(collect_job, "replace", lambda found, path: os.replace(found["out"], path))

    def run(name, data, kind="auto"):
        raw, out, path = tmp_path / f"{name}.raw", tmp_path / f"{name}.part.parquet", tmp_path / f"{name}.parquet"
        raw.write_bytes(data)
        collect_job.finish_file({"file": {"kind": kind, "encoding": "auto"}, "output": {"format": "parquet"}}, path, {"part": raw, "out": out}, {"url": f"https://x.invalid/{name}"})
        return pq.read_table(path)
    return run


def test_file_mode_bom_json_and_late_text_csv(finish):
    assert finish("bom", b"\xef\xbb\xbf" + b'[{"a": 1}, {"a": 2}]', kind="json").column("a").to_pylist() == [1, 2]
    late = finish("late.csv", ("code,n\n" + "".join(f"{i},1\n" for i in range(30000)) + "A1,2\n").encode())
    assert late.column("code").to_pylist()[-1] == "A1"


def test_file_mode_refuses_utf16(finish):
    with pytest.raises(Halt, match="UTF-16"):
        finish("u16.csv", "이름\t값\n김\t1\n".encode("utf-16"))


def test_excel_refuses_long_text_and_writes_binary_as_hex(tmp_path):
    con = ibis.duckdb.connect()
    con.raw_sql("create table t as select 1 as id, from_hex('01ff') as b")
    result.export(con.table("t"), tmp_path / "b.xlsx")
    assert pl.read_excel(tmp_path / "b.xlsx").rows() == [(1, "01ff")]
    con.raw_sql("create table s as select repeat('가', 40000) as s")
    with pytest.raises(ValueError, match="32,767"):
        result.export(con.table("s"), tmp_path / "s.xlsx")


def test_ga4_notes_data_loss_and_sampling(monkeypatch):
    monkeypatch.setattr(ga4, "read_key", lambda p: {})
    monkeypatch.setattr(ga4, "access_token", lambda key: "tok")
    reply = {"metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"}], "rowCount": 1,
             "rows": [{"dimensionValues": [{"value": "(other)"}], "metricValues": [{"value": "9"}]}],
             "metadata": {"dataLossFromOtherRow": True, "samplingMetadatas": [{}]}}
    monkeypatch.setattr(ga4.transport, "post", lambda *a, **k: json.dumps(reply))
    notes = []
    ga4.fetch("k.json", {"property": "123", "start": "28daysAgo", "end": "yesterday", "dimensions": "pagePath", "metrics": "sessions"}, notes)
    assert len(notes) == 2 and "(other)" in notes[0]


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node가 없다")
def test_next_page_reuses_last_search_conditions(tmp_path):
    from test_fix_audit_js import between, run_node
    code = between("index.html", "let searched = null;", "// 결과 표의 머리글을")
    stub = r"""
const els = {}; const $ = id => (els[id] ??= { value: id === "limit" ? "2" : "", checked: false, disabled: false, textContent: "", onclick: null });
const S = { base: "t" }; let panel = ["amt > 0"], skip = 0, last = null; const sent = [];
const state = () => ({ base: "t", conditions: [...panel] }); const query = offset => ({ ...state(), limit: $("limit").value, offset });
const busy = (b, f) => f(); const timed = (k, f) => f();
const api = async (url, body) => { sent.push(body); return { rows: { rows: [1, 2] }, count: 10 }; };
const showBig = () => {}; const renderTables = () => {};
"""
    body = r"""
(async () => {
  await $("search").onclick();
  panel = ["amt > 100"];        // 패널을 고친 뒤
  await $("next").onclick();    // 쪽만 넘긴다
  console.log(JSON.stringify(sent.map(b => [b.conditions, b.offset])));
})();
"""
    assert run_node(tmp_path, body, parts=[stub, code]) == [[["amt > 0"], 0], [["amt > 0"], 2]]
