"""흐름(다이어그램) 보기를 없앤 뒤 예전에 저장한 것이 깨지지 않는지: 흐름 그래프가 든 검색 초안은 body로 단계 카드에 열고(짧은 안내),
흐름(flow_id)에 묶은 내 지표·대시보드 카드는 오류 대신 다시 만들어 달라는 한국어 안내를 준다. 저장한 정의는 지우지 않는다."""
import shutil

import pytest

from core import dash_store
from dashboard import dash_calc
from metrics import my_metrics
from query import sentence_exec
from test_fix_audit_js import index_slice, run_node

OLD = {"id": "m1", "name": "옛 지표", "scope": {"key": "k", "label": "L", "schema": "", "flow_id": "f1"}, "template": "m02", "slots": {"대상": "user"},
       "deps": [], "def_hash": "h", "unit": "", "better": "", "cols": {"user": "text"}}


def test_old_flow_metric_says_flow_is_gone_and_stays_saved(folder):
    my_metrics.save([OLD])
    assert my_metrics.check_use(OLD, {"key": "k", "table": "sales"}) == my_metrics.FLOW_GONE
    with pytest.raises(ValueError, match="흐름 보기가 없어져 이 지표는 다시 구할 수 없어요"):
        sentence_exec.check_scope(OLD, {"base": "sales"})
    assert my_metrics.scope_text(OLD) == "옛 지표 · f1 (L)"  # 목록에는 그대로 보인다
    assert [m["id"] for m in my_metrics.load()] == ["m1"]  # 저장한 정의는 지우지 않는다


def test_old_flow_card_says_flow_is_gone(folder):
    card = dash_store.check_card({"kind": "kpi", "title": "옛 카드", "source": {"key": "k", "label": "L", "flow_id": "f1"}})  # 예전 카드도 읽힌다
    with pytest.raises(ValueError) as e:
        dash_calc.compute(card, "2026-10-27 10:00:00")
    assert str(e.value) == "흐름 보기가 없어져 이 카드는 다시 구할 수 없어요. 탐색에서 다시 만들어 주세요."


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_old_flow_draft_opens_in_cards_from_its_body(tmp_path):
    code = index_slice("const FLOW_GONE", "\nlet view") + index_slice("async function restoreSearch", "// 초안을 복원했을 때만")
    prelude = """
let view = "cards"; const calls = []; const SENTENCE_JS = [];
const loadBody = async b => calls.push(["load", b]); const need = async () => {};
const paintView = n => { view = n; calls.push(["paint", n]); }; const flash = (t, ms) => calls.push(["flash", t, ms]);
const setView = async v => { calls.push(["set", v]); view = v; return true; };
const sentenceView = () => ({ setDraft: d => calls.push(["sentence", d]) });
"""
    body = """
(async () => {
  const graph = { nodes: [{ id: "n1", kind: "table" }, { id: "n2", kind: "aggregate" }, { id: "n3", kind: "chart" }], edges: [["n1", "n2"], ["n2", "n3"]] };
  await restoreSearch({ view: "flow", body: { base: "t", conditions: ["a = 1"] }, graph });
  const flow = calls.splice(0);
  await restoreSearch({ view: "sentence", body: { base: "t" }, sentence: { items: [] } });
  console.log(JSON.stringify({ flow, sentence: calls, view }));
})();
"""
    got = run_node(tmp_path, body, parts=[prelude, code])
    assert got["flow"] == [["load", {"base": "t", "conditions": ["a = 1"]}], ["flash", "흐름 보기가 없어져서 저장된 흐름 그림은 빼고 단계별 보기로 열었어요.", 4000]]  # 단계 카드에 body만, 안내 한 번
    assert got["sentence"] == [["load", {"base": "t"}], ["sentence", {"items": []}], ["set", "sentence"]] and got["view"] == "sentence"
