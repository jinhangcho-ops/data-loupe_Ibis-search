"""static/query/metric_catalog.js(지표 사전 화면)의 화면 없는 계산을 node로 돌려 본다. node가 없으면 건너뛴다.
영역 묶기, 값 보이기(비율은 %), 검색 body에 기간 더하기, 서버로 보낼 빈칸, 맞춤 구절. 항목 자료는 서버 metric_catalog.catalog()를 그대로 쓴다."""
import json
import shutil
import subprocess

import pytest

from query import flow_app  # noqa: F401  server.constants("query")["flow"]
from metrics import metric_catalog
from core import server
from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def run_node(tmp_path, body):
    head = "globalThis.SERVER = {flow: %s};\n" % json.dumps(server.constants("query")["flow"], ensure_ascii=False)
    script = tmp_path / "case.js"
    script.write_text(head + "".join(next((ROOT / "static").rglob(f)).read_text(encoding="utf-8") for f in ("flow.js", "sentences.js", "metric_catalog.js")) + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_groups_values_body_and_align(tmp_path):
    cat = metric_catalog.catalog()
    got = run_node(tmp_path, "const cat = %s; const uv = cat.entries.find(e => e.id === 'uv');\n"
                   "console.log(JSON.stringify({ groups: mcGroups(cat).map(g => [g.id, g.entries.length]),\n"
                   "  values: [mcValue(0.1234, 'percent', ''), mcValue(1234567.891, 'number', '원'), mcValue(null, 'number', '명'), mcValue(0, 'percent', '')],\n"
                   "  body: [mcBody({ base: 't' }, { column: 'ts', start: '2026-01-01', end: '2026-01-31' }), mcBody({ base: 't', periods: ['a 1 ~ 2'] }, null)],\n"
                   "  slots: mcSlots(uv, { '사용자': 'uid', '시각': 'ts', '없는칸': 'x', '단위': undefined }),\n"
                   "  align: [mcAlign({ kind: 'period', unit: 'week' }), mcAlign({ kind: 'key', keys: [['a', 'b']] }), mcAlign({ kind: 'key', keys: null }), mcAlign({ kind: '' })] }));" % json.dumps(cat))
    assert [g[0] for g in got["groups"]] == [a["id"] for a in metric_catalog.AREAS] and sum(g[1] for g in got["groups"]) == len(metric_catalog.ENTRIES)
    assert got["values"] == ["12.34%", "1,234,567.89 원", "—", "0%"]
    assert got["body"] == [{"base": "t", "periods": ["ts 2026-01-01 ~ 2026-01-31"]}, {"base": "t", "periods": ["a 1 ~ 2"]}]
    assert got["slots"] == {"사용자": "uid", "시각": "ts"}
    assert got["align"] == [{"kind": "period", "unit": "week"}, {"kind": "key", "keys": [["a", "b"]]}, None, None]
