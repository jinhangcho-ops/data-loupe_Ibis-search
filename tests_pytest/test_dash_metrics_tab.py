"""묶음 B: 대시보드의 [지표] 탭. 쿼리 화면 문장 보기의 지표 화면(sentences.js SentenceView + metric_catalog.js)을
연결·테이블을 넘겨 띄우고, 저장은 같은 /api/my_metrics를 쓴다. 화면 없이 가짜 DOM과 가짜 api로 node에서 돌린다. node가 없으면 건너뛴다."""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT
from core import server

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

HEAD = r"""
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ""; this.dataset = {}; this.style = {}; this._text = ""; }
  append(...k) { k.forEach(c => this.children.push(typeof c === "string" ? { text: c, children: [] } : c)); }
  replaceChildren(...k) { this.children = []; this.append(...k); }
  remove() {}
  querySelectorAll() { return []; }
  querySelector() { return null; }
  addEventListener() {}
  get classList() { const n = this; return { add: c => { n.className += " " + c; }, remove() {}, toggle() {}, contains: c => n.className.split(" ").includes(c) }; }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text + this.children.map(c => c.text ?? c.textContent).join(" "); }
}
globalThis.document = { createElement: t => new Node(t), body: new Node("body"), documentElement: {}, querySelectorAll: () => [] };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
globalThis.el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
globalThis.chip = (text) => el("span", {}, text);
globalThis.busy = async (b, fn) => fn();
globalThis.sameSource = (a, b) => a === b;
globalThis.S = { schema: "", base: "", cols: [] };
globalThis.calls = [];
const MY = { source: { key: "K1", label: "연결1" }, legacy: [], metrics: [
  { id: "a", name: "주문합", text: "금액 합계", scope: { key: "K1", table: "orders" }, template: "m01", slots: { "컬럼": "금액" }, deps: [] },
  { id: "b", name: "방문수", text: "방문 개수", scope: { key: "K1", table: "visits" }, template: "m01", slots: { "컬럼": "id" }, deps: [] }] };
globalThis.api = async (url, body) => {
  calls.push([url, body]);
  if (url === "/api/sentences") return { preps: {}, templates: [{ id: "m01", no: "M1", name: "합계", text: "{컬럼}의 합계", kind: "metric", slots: [{ name: "컬럼", type: "column", kinds: "num", label: "컬럼" }] }] };
  if (url === "/api/my_metrics") return MY;
  if (url === "/api/my_metrics/status") return { status: {} };
  if (url === "/api/catalog") return { areas: [], entries: [] };
  if (url === "/api/catalog/available") return { availability: {} };
  if (url === "/api/question_sets") return { sets: {} };
  return {};
};
"""


def run_node(tmp_path, body, page="dashboard"):
    """page 화면이 진짜로 받는 상수(server.constants)를 SERVER로 넣는다(가짜 SERVER는 빠진 값을 가린다)."""
    script = tmp_path / "case.js"
    server_js = f"globalThis.SERVER = {json.dumps(server.constants(page), ensure_ascii=False)};\n"
    parts = [HEAD, server_js] + [(ROOT / "static" / f).read_text(encoding="utf-8") for f in ("core/funcs.js", "query/sentences.js", "query/metric_catalog.js")]
    parts.append("Funcs.load = async () => {}; Funcs.loadSupport = async () => ({});\n" + body)
    script.write_text("\n".join(parts), encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


SHOW = """
(async () => {
  const host = new Node("div");
  const view = SentenceView(host, CTX);
  await view.show(); await new Promise(r => setTimeout(r, 100));
  const status = calls.find(c => c[0] === "/api/my_metrics/status");
  console.log(JSON.stringify({ text: host.textContent, tabs: host.children.length, status: status ? status[1].scope : null, avail: calls.some(c => c[0] === "/api/catalog/available"), urls: [...new Set(calls.map(c => c[0]))] }));
})();
"""


def test_dashboard_mounts_metric_view_for_the_chosen_table(tmp_path):
    """대시보드: S는 비어 있어도 scope()로 넘긴 테이블 기준으로 내 지표를 나누고(같은 /api/my_metrics), 탭 줄 없이 지표 화면만 그린다."""
    got = run_node(tmp_path, "const CTX = { only: 'metric', scope: () => ({ base: 'orders', schema: '' }), body: () => ({ base: 'orders', schema: '' }), columns: () => [['금액', '실수'], ['id', '정수']], tables: () => ['orders', 'visits'] };" + SHOW)
    assert got["tabs"] == 1  # 탭 줄 없이 본문 하나
    assert got["status"] == {"table": "orders", "schema": ""}
    assert got["avail"] and "/api/my_metrics" in got["urls"] and "/api/sentences" in got["urls"]
    text = got["text"]
    assert "금액 합계" in text and "다른 테이블의 지표" in text and "방문 개수" in text  # orders 것은 '내 지표', visits 것은 '다른 테이블' 쪽
    assert text.index("금액 합계") < text.index("다른 테이블의 지표") < text.index("방문 개수")


def test_dashboard_without_a_table_asks_to_pick_one(tmp_path):
    got = run_node(tmp_path, "const CTX = { only: 'metric', scope: () => ({ base: '', schema: '' }), body: () => ({}), columns: () => [], tables: () => ['orders'] };" + SHOW)
    assert "테이블을 고르세요" in got["text"] and got["status"] is None


def test_query_screen_still_follows_S_and_keeps_its_tabs(tmp_path):
    """쿼리 화면은 scope·only 없이 지금처럼 전역 S를 따르고 탭 줄(탐색·지표)이 있다."""
    got = run_node(tmp_path, "S.base = 'visits'; const CTX = { body: () => ({}), columns: () => [['id', '정수']], tables: () => ['orders', 'visits'] };" + SHOW.replace(
        "await view.show();", "await view.show(); host.children[0].children[1].onclick(); await new Promise(r => setTimeout(r, 100));").replace("tabs: host.children.length", "tabs: host.children[0].children.length"), page="query")
    assert got["tabs"] == 2 and got["status"] == {"table": "visits", "schema": ""}


def test_dashboard_page_loads_the_shared_scripts_and_button():
    html = (ROOT / "static/dashboard/dashboard.html").read_text(encoding="utf-8")
    for needle in ('"/static/query/sentences.js"', '"/static/query/metric_catalog.js"', 'id="d-metrics"', 'id="d-met-host"', "/static/query/sentences.css", "only: \"metric\""):
        assert needle in html
