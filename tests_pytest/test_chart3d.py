"""3차원 그래프(막대·곡면·산점도)와 쿼리 차트 탭의 바로 그리기.
서버: 종류 상수·view 검사(점 잇기)·화면에 주는 상수·3차원 산점도 카드의 점([크기] 열이 Z축). 화면: dash_shelf.js의 3차원 산점도 이유 문장(node), 쿼리 화면 차트 탭의 글자·칸.
데이터는 시험 안에서 지어낸다."""
import shutil
import subprocess

import duckdb
import pytest

from conftest import ROOT
from core import dash_store, server
from dashboard import dash_calc

STATIC = ROOT / "static"
NODE = shutil.which("node")
NODE_TIMEOUT = 60  # node 한 번을 기다리는 초
ERR_TAIL = 3000  # 실패했을 때 보일 오류 글 길이
ROWS = [(1.0, 2.0, 3.0, "갑"), (2.0, 4.0, 1.0, "을"), (3.0, 1.0, 5.0, "갑"), (4.0, 8.0, 2.0, "을")]  # (가로, 세로, 높이, 묶음)


@pytest.fixture
def db(folder, tmp_path):
    from query.search_app import app  # 대시보드 라우트는 conftest가 올린다
    path = tmp_path / "s.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table spots (w double, d double, h double, team varchar)")
    raw.executemany("insert into spots values (?, ?, ?, ?)", ROWS)
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_three_d_types_registered():
    assert dash_store.THREE_D_TYPES == ("bar3d", "surface3d", "scatter3d")
    assert [dash_store.CHART_TYPES[k] for k in dash_store.THREE_D_TYPES] == ["3차원 막대", "3차원 곡면", "3차원 산점도"]
    assert {"bar3d", "surface3d"} <= set(dash_store.TIME_TYPES) and "scatter3d" not in dash_store.TIME_TYPES  # 날짜 X축은 격자로 그리는 둘만
    assert "scatter3d" in dash_calc.POINT_TYPES and not {"bar3d", "surface3d"} & set(dash_calc.POINT_TYPES)


def test_view_takes_three_d_types_and_connect():
    for kind in dash_store.THREE_D_TYPES:
        assert dash_store.check_view({"type": kind})["type"] == kind
    view = dash_store.check_view({"type": "scatter3d", "points": {"size": "h"}, "connect": True})
    assert view["points"] == {"size": "h"} and view["connect"] is True
    assert "connect" not in dash_store.check_view({"type": "scatter3d"})
    with pytest.raises(ValueError, match="connect"):
        dash_store.check_view({"type": "scatter3d", "connect": "yes"})
    with pytest.raises(ValueError, match="차트 종류"):
        dash_store.check_view({"type": "pie3d"})


def test_constants_carry_three_types(client):
    dash = client.get("/api/dash/constants").get_json()["dash"]
    assert dash["three_types"] == list(dash_store.THREE_D_TYPES) and all(k in dash["types"] for k in dash["three_types"])


def test_scatter3d_card_points_have_z(db):
    """3차원 산점도 카드: 점은 [x, y, z, (그룹)]. z는 view.points.size 컬럼. 점 잇기를 바꿔도 다시 구하는 정의(def_hash)는 같다."""
    card = {"kind": "chart", "title": "t", "source": {"schema": "", "table": "spots", "body": {"base": "spots", "x": "w", "split": "team", "series": [{"y": "d", "agg": "sum"}]}},
            "view": {"type": "scatter3d", "points": {"size": "h"}}}
    data = db.post("/api/dash/card/compute", json={"card": card}).get_json()["data"]
    assert sorted(data["points"]["points"]) == sorted([list(r) for r in ROWS]) and data["points"]["groups"] == ["갑", "을"] and data["points"]["size"] == "h"
    linked = db.post("/api/dash/card/compute", json={"card": {**card, "view": {**card["view"], "connect": True}}}).get_json()["data"]
    assert linked["def_hash"] == data["def_hash"]
    other = db.post("/api/dash/card/compute", json={"card": {**card, "view": {**card["view"], "points": {"size": "d"}}}}).get_json()["data"]
    assert other["def_hash"] != data["def_hash"]


def test_points_route_for_query_chart(db):
    """쿼리 차트 탭이 보내는 모양(series[0].y, size = Z축, 육각 칸 끔)으로 점을 받는다. Z가 숫자가 아니면 이유."""
    body = {"base": "spots", "x": "w", "x_unit": "", "split": "", "order": "auto", "series": [{"y": "d", "agg": "count"}], "size": "h", "auto": {"density": False}}
    got = db.post("/api/chart/points", json=body).get_json()["points"]
    assert sorted(got["points"]) == sorted([list(r[:-1]) for r in ROWS]) and got["groups"] == [] and "hex" not in got
    assert "숫자 컬럼" in db.post("/api/chart/points", json={**body, "size": "team"}).get_json()["error"]


REASON = """
const assert = require("assert");
const cols = [["w", "실수"], ["d", "정수"], ["h", "실수"], ["team", "문자"], ["at", "날짜"]];
assert.strictEqual(scatter3dReason("w", "d", "h", cols), "");
assert.strictEqual(scatter3dReason("w", "d", "", cols), "3차원 산점도는 숫자 컬럼이 셋 필요해요. [Z축]에 숫자 컬럼을 골라 주세요.");
assert.strictEqual(scatter3dReason("", "", "h", cols), "3차원 산점도는 숫자 컬럼이 셋 필요해요. [X축]·[Y축]에 숫자 컬럼을 골라 주세요.");
assert.strictEqual(scatter3dReason("at", "d", "h", cols), "3차원 산점도의 X축·Y축·Z축은 모두 숫자 컬럼이어야 해요. X축 ‘at’은(는) 숫자 컬럼이 아니에요.");
assert.strictEqual(scatter3dReason("w", "team", "없는컬럼", cols), "3차원 산점도의 X축·Y축·Z축은 모두 숫자 컬럼이어야 해요. Y축 ‘team’, Z축 ‘없는컬럼’은(는) 숫자 컬럼이 아니에요.");
console.log("ok");
"""


@pytest.mark.skipif(NODE is None, reason="node가 없다")
def test_scatter3d_reason_in_node(tmp_path):
    script = tmp_path / "reason_test.js"
    script.write_text((STATIC / "core" / "dash_shelf.js").read_text(encoding="utf-8") + REASON, encoding="utf-8")
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=NODE_TIMEOUT)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-ERR_TAIL:]


def test_query_chart_tab_draws_on_change():
    """쿼리 차트 탭: 칸·선반이 바뀌면 바로 그리고(그리는 중이면 끝난 뒤 한 번 더), 3차원 종류·Z축·점 잇기 칸이 있다(글자로 확인, 화면 동작은 헤드리스 확인 도구로 봤다)."""
    html = (STATIC / "query" / "index.html").read_text(encoding="utf-8")
    pane = html[html.index('id="pane-chart"'):html.index('id="pane-sql"')]
    assert "X축과 Y축을 고르면 바로 그려요" in pane and "[차트 그리기]를 누르세요" not in pane
    assert 'id="draw"' in pane and 'id="cz"' in pane and 'id="cconnect"' in pane and 'id="cz-box"' in pane
    assert "function redraw() { if (drawn && " not in html  # 예전: 이미 그린 차트가 있을 때만 다시 그렸다
    assert 'if ($("draw").disabled) { drawAgain = true; return; }' in html and "if (drawAgain) { drawAgain = false; redraw(); }" in html
    assert 'if ($("cx").value && (series.length || now.series.length)) $("draw").click();' not in html  # 예전: 선반은 값이 있어야만 그렸고, 그리는 중이면 버렸다
    assert "SERVER.dash.three_types" in html and 'api("/api/chart/points", { ...query, size: z, auto: { density: false } })' in html
    assert "onclick=" not in pane  # 인라인 핸들러 없음(CSP nonce)


def test_grid_3d_takes_more_split_values():
    """3차원 막대·곡면만 나눌 값 상한이 오른다(나눈 값이 한 축이라 가짓수가 많아야 곡면이 촘촘하다). 다른 종류는 8개 그대로."""
    import ibis

    from core import result
    assert [result.split_limit(kind) for kind in ("surface3d", "bar3d", "scatter3d", "bar", "", None)] == [result.MAX_SPLITS_3D] * 2 + [result.MAX_SPLITS] * 4
    kinds = [f"k{i:02d}" for i in range(result.MAX_SPLITS + 4)]
    t = ibis.memtable({"x": ["가"] * len(kinds), "kind": kinds, "v": list(range(len(kinds)))})
    series = [{"y": "v", "agg": "sum"}]
    _, usual, more = result.get_chart(t, "x", series, "kind")
    assert len(usual) == result.MAX_SPLITS and more is True
    _, wide, more = result.get_chart(t, "x", series, "kind", "x", "", result.split_limit("surface3d"))
    assert [d["split"] for d in wide] == kinds and more is False


def test_card_definition_changes_with_grid_3d_type():
    """카드 종류를 3차원 막대·곡면으로 바꾸면 정의(지문)가 달라져 더 많은 나눈 값으로 다시 구한다."""
    card = {"kind": "chart", "source": {"body": {"x": "x"}}, "view": {"type": "bar"}}
    flat, grid = dash_calc.definition(card), dash_calc.definition({**card, "view": {"type": "surface3d"}})
    assert "max_splits" not in flat.get("view", {}) and grid["view"]["max_splits"] == dash_calc.result.MAX_SPLITS_3D
