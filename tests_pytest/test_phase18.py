"""Phase 18(plans/phase-18.md): 범위로 묶은 쿼리 차트의 "그룹이 많아" 안내, 차트에 그릴 그룹을 서버가 먼저 거르기(keep·숫자 범위).
데이터는 시험 안에서 지어낸다. 새 함수(pick_groups·server_picks)가 스켈레톤이면 실패하는 것이 정상이다.
기존 함수 수정((수정) 절)이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-18 <항목>")로 표시했다. 그 항목을 끝낸 쪽이 표시를 지운다."""
import datetime as dt
import json
import shutil
import subprocess

import duckdb
import polars as pl
import pytest
from lazy import ibis

from conftest import ROOT
from core import result
from dashboard import dash_calc

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def nums():
    """x 1~60과 빈 값, s(글자, 진짜 "None" 글자와 빈 값), f(실수)."""
    return ibis.memtable(pl.DataFrame({
        "x": list(range(1, 61)) + [None],
        "s": [f"k{i:02}" for i in range(1, 60)] + ["None", None],
        "f": [i / 2 for i in range(1, 61)] + [None],
    }).to_arrow())


def xs(t, col):
    return sorted(t.select(col).to_pyarrow().column(col).to_pylist(), key=lambda v: (v is None, v))


# ---------- 18C1 pick_groups ----------

def test_pick_groups_keep():
    t = nums()
    assert xs(result.pick_groups(t, "x", ["55", "3"], None), "x") == [3, 55]  # 받은 50개 밖(55)도
    assert xs(result.pick_groups(t, "x", ["55", result.NULL_LABEL], None), "x") == [55, None]  # "None"은 빈 X 그룹
    assert xs(result.pick_groups(t, "s", ["k03", result.NULL_LABEL], None), "s") == ["None", "k03", None]  # 글자 컬럼은 "None" 글자와 빈 값 둘 다
    assert xs(result.pick_groups(t, "f", ["1.5", "30.0"], None), "f") == [1.5, 30.0]  # 라벨 글자(str)를 컬럼 타입으로
    assert result.pick_groups(t, "x", None, None) is t and result.pick_groups(t, "x", [], []) is t  # 고른 것이 없으면 그대로
    labels, sets, _ = result.get_chart(result.pick_groups(t, "x", ["55", "3"], None), "x", [{"agg": "count"}])
    assert labels == [3, 55] and sets[0]["values"] == [1, 1] and sets[0]["truncated"] is False


def test_pick_groups_ranges():
    t = nums()
    assert xs(result.pick_groups(t, "x", None, [[10, 12], [58, None]]), "x") == [10, 11, 12, 58, 59, 60]  # 양끝 포함, 빈 끝은 열림, 범위끼리 또는
    assert xs(result.pick_groups(t, "x", ["11", "40", result.NULL_LABEL], [[10, 12]]), "x") == [11]  # keep과 범위는 그리고(빈 X는 범위 밖)
    assert xs(result.pick_groups(t, "s", ["k03"], [[1, 5]]), "s") == ["k03"]  # 숫자가 아닌 X의 범위는 화면 visible처럼 쓰지 않는다


def test_pick_groups_errors():
    days = ibis.memtable(pl.DataFrame({"d": [dt.date(2024, 1, 1)]}).to_arrow())
    with pytest.raises(ValueError, match="날짜 가로축"):
        result.pick_groups(days, "d", ["2024-01-01"], None)
    with pytest.raises(ValueError, match="정수"):
        result.pick_groups(nums(), "x", ["abc"], None)
    with pytest.raises(ValueError, match="숫자"):
        result.pick_groups(nums(), "x", None, [["2024-01-01", None]])
    with pytest.raises(ValueError):
        result.pick_groups(nums(), "nope", ["1"], None)


# ---------- 18C2 /api/chart groups ----------

@pytest.fixture
def searching(folder, tmp_path):
    from core import server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "shop.duckdb"))
    raw.execute("create table t as select i as id, 'v' || i as name, date '2024-01-01' + i::int as day from range(1, 301) r(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "shop.duckdb")}).status_code == 200
    yield lambda api, **body: c.post(api, json=body).get_json()
    c.post("/api/disconnect", json={})


def test_chart_route_picks_groups(searching):
    count = [{"agg": "count"}]
    got = searching("/api/chart", base="t", x="id", series=count, groups={"keep": ["299", "7"]})
    assert got["labels"] == ["7", "299"] and got["truncated"] is False and got["datasets"][0]["values"] == [1, 1]
    ranged = searching("/api/chart", base="t", x="id", series=count, groups={"ranges": [[290, None]]})
    assert ranged["labels"] == [str(i) for i in range(290, 301)]
    assert searching("/api/chart", base="t", x="name", series=count, groups={"keep": ["v299"]})["labels"] == ["v299"]
    plain = searching("/api/chart", base="t", x="id", series=count)
    assert len(plain["labels"]) == result.MAX_GROUPS and plain["truncated"] is True  # groups가 없으면 지금처럼
    assert "날짜 가로축" in searching("/api/chart", base="t", x="day", series=count, groups={"keep": ["2024-01-02"]})["error"]
    assert "error" in searching("/api/chart", base="t", x="id", series=count, groups={"keep": "7"})  # check_groups 모양 검사


# ---------- 18C3 카드(server_picks·category_data·definition·compute) ----------

def chart_card(groups, body=None):
    return {"kind": "chart", "title": "c", "view": {"type": "", "unit": "day", "groups": groups},
            "source": {"body": body or {"base": "t", "x": "x", "series": [{"y": "", "agg": "count"}]}}}


def test_server_picks():
    assert dash_calc.server_picks(chart_card({"keep": ["a"], "series": ["s"], "rename": {"a": "에이"}})) == {"keep": ["a"]}
    assert dash_calc.server_picks(chart_card({"keep": ["a"], "ranges": [[1, 5], [8, None]]})) == {"keep": ["a"], "ranges": [[1, 5], [8, None]]}
    assert dash_calc.server_picks(chart_card({"ranges": [["2024-01-01", None]]})) == {}  # 날짜 범위는 화면에서
    assert dash_calc.server_picks(chart_card({"ranges": [[1, 5], ["2024-01-01", None]]})) == {}  # 섞이면 화면에서
    assert dash_calc.server_picks(chart_card({"keep": ["a"], "ranges": [[1, 5]], "merge": True})) == {}  # 묶기는 range_data
    assert dash_calc.server_picks({**chart_card({"keep": ["a"]}), "kind": "boxplot"}) == {}
    assert dash_calc.server_picks({"kind": "chart", "view": {"type": ""}, "source": {}}) == {}


def test_category_data_uses_body_groups():
    t = nums()
    body = {"x": "x", "series": [{"y": "", "agg": "count"}], "order": "x"}
    series = dash_calc.clean_series(body["series"])
    got = dash_calc.category_data(t, {**body, "groups": {"keep": ["55", result.NULL_LABEL], "ranges": [[50, None]]}}, series)
    assert got["values"]["labels"] == ["55"] and got["note"] == ""
    plain = dash_calc.category_data(t, {**body, "groups": {}}, series)
    assert len(plain["values"]["labels"]) == result.MAX_GROUPS and "그룹이 많아" in plain["note"]  # 고른 것이 없으면 지금처럼


def test_definition_includes_server_picks():
    assert dash_calc.definition(chart_card({"keep": ["a"], "rename": {"a": "에이"}}))["view"]["groups"] == {"keep": ["a"]}  # 이름 바꾸기는 보기라 빠진다
    assert "view" not in dash_calc.definition(chart_card({"series": ["s"]}))  # 보일 계열만이면 정의는 그대로
    assert dash_calc.definition(chart_card({"ranges": [[1, 5]], "merge": True}))["view"]["groups"] == {"ranges": [[1, 5]], "merge": True}  # 묶기는 지금처럼


def test_compute_filters_chart_card(monkeypatch):
    t = nums()
    from core import querying
    monkeypatch.setattr(querying, "runnable", lambda body, **kw: t)
    monkeypatch.setattr(querying, "build", lambda body, **kw: t)
    got = dash_calc.compute(chart_card({"keep": ["55", "3"]}), "2026-11-12T00:00:00")
    assert got["values"]["labels"] == ["3", "55"] and got["note"] == ""


# ---------- 18A·18C4 쿼리 화면(node·정적 검사) ----------

def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def between(page, start, end):
    s = page.index(start)
    return page[s:page.index(end, s)] + end


def run_node(tmp_path, body):
    page = text("static/query/index.html")
    parts = [between(page, "async function groupsChanged() {", "\n}\n"), between(page, "function toDrawn(", "\n}\n")]
    head = r"""
const ELS = {}, $ = id => (ELS[id] ??= { textContent: "" }), calls = [], KIND = { v: "num" };
let drawn = null, chartGroups = {}, chartMerged = null, mergeSeq = 0;
const rangeError = () => "", drawChart = () => {}, seriesTitle = s => s.agg, xKind = () => KIND.v;
const api = async (url, body) => {
  calls.push(JSON.parse(JSON.stringify(body)));
  return body.merge_ranges ? { labels: ["1~2"], series: [{ name: "count", values: [3] }], merged: true }
    : { labels: ["7"], datasets: [{ series: 0, split: null, values: [1] }], truncated: false, more_splits: false, max_groups: 50, max_splits: 8 };
};
const query = { x: "id", series: [{ y: "", agg: "count" }] }, used = [{ y: "", agg: "count" }];
const cutData = { labels: ["1", "2"], datasets: [{ series: 0, split: null, values: [5, 6] }], truncated: true, more_splits: false, max_groups: 50, max_splits: 8 };
"""
    script = tmp_path / "t.js"
    script.write_text("\n".join(parts) + "\n" + head + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@node
def test_chart_note_hidden_when_merged(tmp_path):
    got = run_node(tmp_path, r"""
(async () => {
  drawn = toDrawn(query, used, cutData);
  await groupsChanged(); const plain = $("err-chart").textContent;
  chartGroups = { ranges: [[1, 2]], merge: true }; await groupsChanged(); const merged = $("err-chart").textContent;
  chartGroups = {}; await groupsChanged(); const off = $("err-chart").textContent;
  console.log(JSON.stringify({ plain, merged, off, n: calls.length }));
})();""")
    assert got == {"plain": "그룹이 많아 50개만 그려요.", "merged": "", "off": "그룹이 많아 50개만 그려요.", "n": 1}


@node
def test_query_chart_sends_picks(tmp_path):
    got = run_node(tmp_path, r"""
(async () => {
  drawn = toDrawn(query, used, cutData);
  chartGroups = { keep: ["7", "None"] }; await groupsChanged();
  const kept = { body: calls.at(-1), labels: chartMerged?.labels, err: $("err-chart").textContent };
  KIND.v = "date"; chartGroups = { keep: ["1"] }; await groupsChanged();
  const date = { n: calls.length, merged: chartMerged, err: $("err-chart").textContent };
  KIND.v = "num"; chartGroups = { ranges: [[1, 5]], keep: ["7"] }; await groupsChanged();
  const ranged = calls.at(-1);
  KIND.v = "text"; chartGroups = { keep: ["a"], ranges: [[1, 5]] }; await groupsChanged();
  console.log(JSON.stringify({ kept, date, ranged, text: calls.at(-1), n: calls.length }));
})();""")
    kept = got["kept"]
    assert kept["body"]["groups"] == {"keep": ["7", "None"]} and "merge_ranges" not in kept["body"] and kept["body"]["x"] == "id"
    assert kept["labels"] == ["7"] and kept["err"] == ""  # 거른 응답이 안 잘렸으면 안내도 없다
    assert got["date"] == {"n": 1, "merged": None, "err": "그룹이 많아 50개만 그려요."}  # 날짜 X는 화면에서만 거른다
    assert got["ranged"]["groups"] == {"keep": ["7"], "ranges": [[1, 5]]}
    assert got["text"]["groups"] == {"keep": ["a"]} and got["n"] == 3  # 글자 X에는 범위를 보내지 않는다


def test_query_group_panel_find_box():
    page = text("static/query/index.html")
    panel = between(page, "function groupPanel() {", "\n}\n")
    assert 'checkList("keep", [...new Set([...drawn.labels, ...(chartGroups.keep ?? [])])])' in panel
    assert 'placeholder: "그룹 찾아 더하기"' in panel and 'findAsTyped(find, q => api("/api/values", { ...drawn.query, column: drawn.x, q })' in panel
    assert "keep: [...new Set([...(chartGroups.keep ?? []), v])]" in panel
    draw = between(page, "function drawChart() {", "\n}\n")
    assert "chartMerged?.merged ? { merged: true }" in draw


# ---------- 18C5 카드 GroupPicker(정적 검사) ----------

def test_group_picker_find_box():
    picker = text("web/components/edit/panel/GroupPicker.tsx")
    assert 'aria-label="그룹 찾아 더하기"' in picker and "fetchValues({ ...(card.source.body ?? {}), q }" in picker
    assert "update({ ...g, keep: [...new Set([...(g.keep ?? []), v])] }, true)" in picker
    assert 'list === "names" && served' in picker and "const FIND_WAIT = 300;" in picker
