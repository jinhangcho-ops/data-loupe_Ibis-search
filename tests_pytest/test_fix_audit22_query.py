"""감사 22 쿼리 화면: (5) 조인 덩어리의 [테이블]을 바꿔도 S.joins에 예전 조인이 남던 것, (6) 공백 있는 컬럼의 정렬 줄을 첫 두 낱말로 읽던 것,
(7) X 값이 딱 MAX_GROUPS개여도 "그룹이 많아"가 뜨던 것, (8) 행 수가 쪽당 행의 배수면 마지막 쪽에서도 [다음]이 켜지던 것.
화면 부분은 index.html에서 함수를 잘라 node로 돌린다(node가 없으면 그 시험만 건너뛴다)."""
import json
import shutil
import subprocess

import pyarrow as pa
import pytest
from lazy import ibis

from conftest import ROOT
from core import result

SCRIPT = r"""
const assert = require("assert");
const html = require("fs").readFileSync(%(path)s, "utf8");
const cut = (a, b) => { const i = html.indexOf(a); return html.slice(i, html.indexOf(b, i)); };
class E { constructor(t) { this.tag = t; this.children = []; } append(...k) { this.children.push(...k); } }
globalThis.el = (t, p = {}, ...k) => { const e = new E(t); Object.assign(e, p); e.append(...k); return e; };
globalThis.Option = function (text, value) { return { text, value }; };
const S = { schema: "", base: "orders", cols: [["cid", "정수"]], joins: [], sorts: [], union: { tables: [] } };
const box = { replaceChildren(...k) { this.kids = k; } };
const nodes = { limit: { value: "10" } };
const $ = id => id === "join-blocks" ? box : (nodes[id] ??= { textContent: "" });
let reply = null;
const api = async (u, b) => u === "/api/query" ? reply : { columns: [["cid", "정수"], ["pid", "정수"]] };
const state = () => ({ ...S });
const tableNames = () => ["orders", "customers", "products"];
const refreshColumns = async () => {}, showSummaries = () => {}, showBig = () => {}, renderTables = () => {};
const busy = (b, f) => f(), timed = (k, f) => f();
const esc = s => s, numCol = () => false, cell = () => "", lit = s => s;
let skip, last;
eval(cut("const sortText", "const columnCount").replace(/^const /gm, "var "));
eval(cut("const JOIN_HOWS", '$("join-add").onclick').replace(/^(const|let) /gm, "var "));
eval(cut("function gridSpec", "// 검색과"));
eval(cut("const query = offset", "// 결과 위의").replace(/^const /, "var "));
const conditionTexts = () => [];
eval(cut("let searched = null", '$("search").onclick').replace(/^let /, "var "));
(async () => {
  // (6) 끝 낱말만 방향으로, 대소문자 무관, 방향이 없으면 작은 것부터
  assert.deepStrictEqual(["my col desc", "order date asc", "amt DESC", "my col"].map(sortText),
    ["my col 큰 것부터", "order date 작은 것부터", "amt 큰 것부터", "my col 작은 것부터"]);
  S.sorts = ["my col DESC"];
  assert.match(gridSpec({ columns: ["my", "my col"], rows: [] }).head, /<th class="">my<\/th><th class="sorted">my col ↓<\/th>/);
  S.sorts = [];
  // (5) 조인을 적용한 뒤 덩어리의 테이블을 바꾸면(키는 아직) S.joins에서 빠진다
  joinsUI = [joinParts("customers left cid=cid")];
  await applyJoins();
  assert.deepStrictEqual(S.joins, ["customers left cid=cid"]);
  await renderJoins();
  const tableSel = box.kids[0].children[0].children[1];
  tableSel.value = "products"; tableSel.onchange();
  await new Promise(r => setTimeout(r, 0));
  assert.deepStrictEqual(joinsUI[0], { table: "products", how: "left", pairs: [["", ""]] });
  assert.deepStrictEqual(S.joins, []);
  // (8) 행 수를 알면 offset + n >= count로, 모르면 n < 쪽당 행으로 [다음]을 끈다
  const rows = n => ({ rows: { columns: [], rows: Array(n).fill({}) } });
  const next = async (offset, n, count) => { reply = { ...rows(n), count }; await runSearch(offset); return $("next").disabled; };
  assert.strictEqual(await next(10, 10, 20), true);   // 20행의 2쪽(마지막)
  assert.strictEqual(await next(0, 10, 20), false);
  assert.strictEqual(await next(10, 5, 15), true);
  assert.strictEqual(await next(10, 10, null), false);  // 모르면 꽉 찬 쪽은 다음이 있을 수 있다
  assert.strictEqual(await next(10, 5, null), true);
  console.log("ok");
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_query_screen_bits():
    path = json.dumps(str(ROOT / "static" / "query" / "index.html"))
    out = subprocess.run(["node", "-e", SCRIPT % {"path": path}], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_chart_truncated_only_when_more_than_max_groups():
    n = result.MAX_GROUPS
    exact = ibis.memtable(pa.table({"x": list(range(n)), "s": ["a"] * n}))
    over = ibis.memtable(pa.table({"x": list(range(n + 1)), "s": ["a"] * (n + 1)}))
    for split in (None, "s"):
        labels, sets, _ = result.get_chart(exact, "x", [{"agg": "count"}], split)
        assert len(labels) == n and not any(d["truncated"] for d in sets)
        labels, sets, _ = result.get_chart(over, "x", [{"agg": "count"}], split)
        assert labels == list(range(n)) and all(d["truncated"] for d in sets)
    html = (ROOT / "static" / "query" / "index.html").read_text(encoding="utf-8")
    assert "d.truncated ? `그룹이 많아" in html and "d.labels.length === d.max_groups" not in html


def test_dashboard_chart_note_only_when_cut():
    """대시보드 차트 카드도 X 값이 딱 MAX_GROUPS개면 '그룹이 많아' 안내를 띄우지 않는다."""
    t = ibis.memtable({"x": [f"k{i:03}" for i in range(result.MAX_GROUPS)]})
    over = ibis.memtable({"x": [f"k{i:03}" for i in range(result.MAX_GROUPS + 1)]})
    seen = {}
    for name, table in (("exact", t), ("over", over)):
        labels, sets, _ = result.get_chart(table, "x", [{"agg": "count"}])
        seen[name] = sets[0]["truncated"]
    assert seen == {"exact": False, "over": True}
    src = (ROOT / "dashboard/dash_calc.py").read_text(encoding="utf-8")
    assert 'datasets[0]["truncated"]' in src and "len(labels) == result.MAX_GROUPS" not in src
