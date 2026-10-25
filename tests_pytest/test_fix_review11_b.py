"""11차 리뷰 묶음 B: 대시보드 [지표] 창. 진짜 server.constants("dashboard")를 SERVER로 넣고 node에서 sentences.js를 돌린다. node가 없으면 건너뛴다."""
import json
import shutil

import pytest

from conftest import ROOT
from core import server
from test_dash_metrics_tab import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

# 화면을 띄운 뒤 글자로 버튼을 찾아 누르고, 본문 글과 '내 지표' 줄(고치기 단추가 있는 줄)의 지표 글을 돌려준다.
CASE = """
const walk = (n, out = []) => { out.push(n); (n.children ?? []).forEach(c => walk(c, out)); return out; };
(async () => {
  const host = new Node("div");
  const view = SentenceView(host, CTX);
  await view.show(); await new Promise(r => setTimeout(r, 100));
  const rows = walk(host).filter(n => (n.className ?? "").split(" ").includes("mrow"));
  const mine = rows.filter(r => !r.className.includes("other")).map(r => r.children[0].children[0].textContent);
  let error = "";
  try { const fix = walk(host).find(n => n.tag === "button" && n._text === CLICK); fix.onclick(); } catch (e) { error = String(e); }
  console.log(JSON.stringify({ text: host.textContent, mine, error }));
})();
"""


def ctx(schema):
    return f"const CTX = {{ only: 'metric', scope: () => ({{ base: 'orders', schema: '{schema}' }}), body: () => ({{}}), columns: () => [['금액', '실수'], ['id', '정수']], tables: () => ['orders'] }};"


@pytest.mark.parametrize("click", ["고치기", "합계"])  # [고치기]와 왼쪽 지표 틀(새 지표)
def test_dashboard_metric_editor_opens(tmp_path, client, click):
    """11B1: 대시보드 화면 상수(진짜 server.constants("dashboard"))에는 flow가 없다. 편집 칸의 좋고 나쁨 값은 진짜 /api/my_metrics 답에서 읽는다."""
    better = client.get("/api/my_metrics").get_json()["better"]
    assert better == ["", "up", "down"] and "flow" not in server.constants("dashboard")
    got = run_node(tmp_path, f"MY.better = {json.dumps(better)};" + ctx("") + f"const CLICK = '{click}';" + CASE.replace('n._text === CLICK', 'n._text === CLICK || n.children.some(c => c.text === " " + CLICK)'))
    assert got["error"] == "" and "클수록 좋음" in got["text"]


def test_metric_in_other_schema_is_not_mine(tmp_path):
    """11B2: 같은 이름의 테이블이라도 스키마가 다르면 '내 지표'가 아니다(서버 scope_id처럼 스키마도 본다)."""
    add = "MY.metrics.push({ id: 'c', name: '판매합', text: '판매 합계', scope: { key: 'K1', schema: 'sales', table: 'orders' }, template: 'm01', slots: { '컬럼': '금액' }, deps: [] });"
    got = run_node(tmp_path, add + ctx("") + "const CLICK = '';" + CASE)
    assert got["mine"] == ["금액 합계"]
    got = run_node(tmp_path, add + ctx("sales") + "const CLICK = '';" + CASE)
    assert got["mine"] == ["판매 합계"]


def test_dashboard_page_fixes():
    """11B3: 연결이 없을 때 안내로 host를 비우면 metView도 버려 다시 만든다. 11B4: 쿼리로 가는 링크는 쿼리 기능이 없으면 숨긴다."""
    html = (ROOT / "static/dashboard/dashboard.html").read_text(encoding="utf-8")
    opener = html.split("async function openMetrics()")[1]
    assert "metView = null" in opener.split("if (!connected)")[1].split("\n")[0]
    link = html.split("쿼리에서 카드 만들기")[0].rsplit("<a ", 1)[1]
    assert 'data-feature="query"' in link
