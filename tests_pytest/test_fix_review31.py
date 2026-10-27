"""31차 리뷰: 기준일만 정한 대시보드에서 다시 정한 카드 값은 저장한다(기간을 건 값만 화면에만), 거꾸로 누적은 받은 값이 모자라면 막는다, 복사의 긴 이름."""
import pytest

from metrics import my_metrics
from query import sentence_exec
from test_fix_audit7_js import fn, needs_node, page, run


@needs_node
def test_as_of_only_recompute_stores_value(tmp_path):
    ctl, dash = page("dashboard/dash_ctl.js"), page("dashboard/dashboard.html")
    a = dash.index("  recompute: async")
    recompute = dash[a:dash.index("\n  star:", a)]
    got = run(tmp_path, r"""
globalThis.DashBoard = { plain: c => ({ id: c.id, kind: c.kind, source: c.source, layout: c.layout, data: c.data }) };
""" + fn(ctl, "function showLive") + "\n" + next(x for x in ctl.splitlines() if x.startswith("const storedCard")) + r"""
let dashId = "d1", valuesDirty = false, valuesSeq = 0, lastSaved;
""" + next(x for x in dash.splitlines() if x.startswith("const cardSettings")) + chr(10) + next(x for x in dash.splitlines() if x.startswith("const sigOf")) + r"""
const needsValues = c => c;
const card = { id: 1, kind: "chart", source: { table: "region" }, layout: { x: 0 }, data: { labels: ["서울"] } };
const board = { cards: [card], ctx: () => ({ range: null, prev: null, as_of: "2026-01-01" }), draw() {} };
const scheduleSave = () => {};
lastSaved = JSON.stringify([[], {}, [cardSettings(card)]]);
const api = async () => ({ data: { labels: ["사과"] } });
const panelHost = {
""" + recompute + r"""
};
(async () => {
  await panelHost.recompute(card);
  console.log(JSON.stringify({ stored: storedCard(card).data }));
})();
""", fake=False)
    assert got["stored"] == {"labels": ["사과"]}


def test_reverse_cumsum_refuses_partial_values():
    a = {"keys": ["day"], "col": "v", "more": True, "rows": [{"day": 1, "v": 1}]}
    with pytest.raises(ValueError, match="거꾸로"):
        sentence_exec.combine({"op": "cumsum", "a": "a", "reverse": True}, {"a": a})


def test_rename_keeps_longer_name_and_unquoted_apostrophe():
    whole = {"order date": "order date", "date": "dt"}
    assert my_metrics.rename_in("order date >= '2024-01-01' and date < '2024-02-01'", whole) == "order date >= '2024-01-01' and dt < '2024-02-01'"
    assert my_metrics.rename_in("nick = it's or status = 'paid'", {"status": "st"}) == "nick = it's or st = 'paid'"
