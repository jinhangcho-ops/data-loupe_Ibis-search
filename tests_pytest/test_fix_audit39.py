"""감사 29차: 문장 14번의 이번 달·올해 직전 기간, 문장·지표 사전 기간 칸의 시각, 목록 N번째 0, 기준일로 자른 기간의 목표 달."""
import pytest

from lazy import ibis
from core import sql_funcs
from dashboard import dash_calc, dash_refresh
from query import sentence_run
from test_fix_audit_js import run_node


def test_sentence_14_this_month_compares_same_days_of_last_month(monkeypatch):
    monkeypatch.setattr(sentence_run, "known_metric", lambda ref: True)
    body = {"base": "ev", "joins": [], "conditions": [], "calcs": [], "periods": []}
    month = {"column": "day", "start": "2026-10-01", "end": "2026-10-28", "to_date": "month"}
    out = sentence_run.compile("m14", {"지표": "클릭", "기간": month}, body, {"day": "date"})
    assert out["ok"], out.get("reason")
    assert out["runs"][1]["body"]["periods"] == ["day 2026-09-01 ~ 2026-09-28"]  # 예전엔 09-03~09-30(직전 28일)
    year = sentence_run.compile("m14", {"지표": "클릭", "기간": {**month, "start": "2026-01-01", "to_date": "year"}}, body, {"day": "date"})
    assert year["runs"][1]["body"]["periods"] == ["day 2025-01-01 ~ 2025-10-28"]


def test_sentence_period_box_keeps_clock_but_dashboard_refuses(client):
    post = lambda **b: client.post("/api/period/parse", json=b).get_json()["span"]
    text = "2026-10-05 09:00 ~ 2026-10-05 17:00"
    assert post(text=text, as_of="2026-10-22")["error"] is None
    assert "날짜로" in post(text=text, as_of="2026-10-22", dashboard=True)["error"]


def test_array_get_refuses_zero():
    t = ibis.memtable({"a": [[1, 2, 3]]})
    with pytest.raises(ValueError, match="1부터"):
        sql_funcs.make("array_get", t, {"x": "a", "n": 0})


def test_target_month_only_when_cut_period_is_whole_months():
    assert dash_calc.target_period(dash_refresh.context({"period": "2026-03-01 ~ 2026-03-20", "as_of": "2026-03-10"})) is None  # 예전엔 3월 목표
    assert dash_calc.target_period(dash_refresh.context({"period": "2026-03", "as_of": "2026-03-10"})) == "2026-03"
    assert dash_calc.target_period(dash_refresh.context({"period": "2026-Q1", "as_of": "2026-02-10"})) == "2026-Q1"
    assert dash_calc.target_period(dash_refresh.context({"period": "이번 달", "as_of": "2026-03-10"})) == "2026-03"


def test_board_ctx_carries_months_to_compute(tmp_path):
    body = """
(async () => {
  const b = Object.create(DashBoard.prototype);
  Object.assign(b, { cards: [], hadRange: false, async recompare() {} });
  await b.setRange({ start: "2026-01-01", end: "2026-02-10" }, null, 3);
  const cut = b.ctx();
  await b.setRange({ start: "2026-01-01", end: "2026-01-31" }, null, undefined);
  console.log(JSON.stringify({ cut, whole: JSON.parse(JSON.stringify(b.ctx())) }));
})();
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js"], parts=["const SVG_KINDS = ['kpi'];\n"])
    assert got["cut"]["months"] == 3 and "months" not in got["whole"]
    ctx = {k: v for k, v in got["cut"].items() if v is not None}
    ctx["prev"] = None
    assert dash_calc.target_period({**ctx, "range": {"start": "2026-01-01", "end": "2026-02-10"}}) == "2026-Q1"  # 화면이 보낸 모양으로도 분기 목표
