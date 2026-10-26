"""17차 B: 예전 연결 열쇠의 내 지표 고치기·상태(K2), 지표 단계가 있는 흐름의 '쓰는 컬럼만'(M4), 문장 보기 정렬 칸의 공백 있는 컬럼(M5). 값은 시험 안에서 지어낸다."""
import json
import shutil
import subprocess

import pytest
from lazy import ibis

from conftest import ROOT
from core import dash_store, metric_file
from metrics import my_metrics as mm
from query import flow, flow_opt

OLD = "url:databricks://:/"  # v1.2.1의 source_key가 host 없는 주소에 남기던 열쇠(with_scope가 지표 scope.key로 저장)
NOW = dash_store.source_key({"kind": "url", "url": "databricks://?server_hostname=dev.cloud.example&http_path=/sql/1"})
C = {"amt": "num", "user": "text", "day": "date", "status": "text"}


def test_old_key_metrics_can_be_edited_and_get_status(client, monkeypatch):
    """K2: 예전 열쇠로 저장한 '비율'이 '사용자 수'를 쓸 때, 지금 연결(같은 연결의 새 열쇠)에서 둘 다 고쳐 저장할 수 있고, status도 둘을 본다."""
    old = {"key": OLD, "label": "DW", "table": "sales"}
    a = mm.create({"name": "사용자 수", "scope": old, "template": "m02", "slots": {"대상": "user"}}, C)
    b = mm.create({"name": "비율", "scope": old, "template": "m05", "slots": {"지표 A": a["id"], "지표 B": a["id"]}}, C)
    monkeypatch.setattr(dash_store, "current_source", lambda: {"key": NOW, "label": "DW"})
    scope = {"table": "sales", "schema": ""}  # 화면(sentences.js)이 보내는 scope: key는 서버 with_scope가 채운다
    status = client.post("/api/my_metrics/status", json={"scope": scope, "columns": C}).get_json()["status"]
    assert set(status) == {a["id"], b["id"]}
    for m, slots in ((b, b["slots"]), (a, {"대상": "status"})):
        got = client.post("/api/my_metrics/update", json={"id": m["id"], "patch": {"name": m["name"], "scope": scope, "template": m["template"], "slots": slots, "unit": "", "better": ""}, "columns": C})
        assert got.status_code == 200, got.get_json()
        assert got.get_json()["metric"]["scope"]["key"] == NOW


def test_prune_skips_metric_step_without_defs(folder, tmp_path):
    """M4: 지표 파일 이름만 담은 지표 단계(flow.js의 {names})가 쓰는 컬럼은 흐름에서 알 수 없으니 원격에서도 '쓰는 컬럼만'을 적용하지 않는다."""
    path = tmp_path / "m.yaml"
    path.write_text('metrics:\n  CTR:\n    format: percent\n    numerator: {agg: count, where: "event = click"}\n    denominator: {agg: count, where: "event = view"}\n', encoding="utf-8")
    metric_file.save_file_path(str(path))
    con = ibis.duckdb.connect()
    con.create_table("ev", ibis.memtable({"uid": [1, 2, 3, 4], "event": ["click", "view", "view", "view"], "amount": [1, 2, 3, 4]}))
    get = lambda name, schema=None: con.table(name)
    g = {"nodes": [{"id": "t1", "kind": "table", "params": {"table": "ev"}, "pos": {"x": 0, "y": 0}},
                   {"id": "m1", "kind": "metric", "params": {"names": ["CTR"]}, "pos": {"x": 0, "y": 0}}], "edges": [["t1", "m1"]]}
    applied = flow_opt.apply(g, [4], get, con, remote=True, scan_ok=False)
    assert 4 not in applied["applied"]
    assert flow.to_expr(applied["graph"], "m1", get).to_polars().to_dicts() == [{"CTR": pytest.approx(1 / 3)}]


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_order_slot_keeps_spaced_column(tmp_path):
    """M5: 정렬 칸 값 '주문 금액 desc'의 컬럼은 첫 낱말이 아니라 컬럼 목록에서 가장 길게 맞는 이름(서버 flow.head_name과 같은 기준)."""
    body = """
const tpl = { slots: [{ name: "정렬 기준", type: "order" }, { name: "컬럼", type: "column" }] };
const fit = sentFitColumns(tpl, { "정렬 기준": "주문 금액 desc", "컬럼": "주문 금액" }, { "주문": "text", "주문 금액": "num" });
console.log(JSON.stringify({ fit, col: sentOrderCol("주문 금액 desc", ["주문", "주문 금액"]), plain: sentOrderCol("amt asc", []) }));
"""
    script = tmp_path / "t.js"
    script.write_text((ROOT / "static/query/sentences.js").read_text(encoding="utf-8") + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert got["fit"] == {"slots": {"정렬 기준": "주문 금액 desc", "컬럼": "주문 금액"}, "gone": []}
    assert got["col"] == "주문 금액" and got["plain"] == "amt"
