"""흐름(퍼널) 화면: 분석 종류 funnel(순서 뒤바뀜·되돌아감·기간 제한·대시보드 기간), /api/analyze, funnel 카드(검사·다시 구하기·최신화 대상), 화면 파일, 생키 좌표(node).
데이터는 테스트 안에서 지어낸다. 값은 손으로 센다."""
import datetime as dt
import shutil
import subprocess

import duckdb
import polars as pl
import pytest
from lazy import ibis

import analysis
import dash_calc
import dash_refresh
import dash_store
import server
from conftest import ROOT

T0 = dt.datetime(2026, 1, 1, 12, 0, 0)
LATER = dt.datetime(2026, 2, 10, 12, 0, 0)
# (사용자, 행동, 시각)
ROWS = [("u1", "view", T0), ("u1", "cart", T0 + dt.timedelta(seconds=60)), ("u1", "pay", T0 + dt.timedelta(seconds=120)),
        ("u2", "cart", T0), ("u2", "view", T0 + dt.timedelta(seconds=10)), ("u2", "cart", T0 + dt.timedelta(seconds=70)), ("u2", "pay", T0 + dt.timedelta(seconds=90)),  # 되돌아감: 처음 cart는 view 앞이라 무시
        ("u3", "view", T0), ("u3", "cart", T0 + dt.timedelta(hours=1)),  # 한 시간 뒤: 30분 제한이면 빠진다
        ("u4", "view", T0),
        ("u5", "pay", T0), ("u5", "view", T0 + dt.timedelta(seconds=5)),  # 순서 뒤바뀜: view 뒤에 cart가 없다
        ("u6", "view", LATER), ("u6", "cart", LATER + dt.timedelta(seconds=60)), ("u6", "pay", LATER + dt.timedelta(seconds=120))]  # 한 달 뒤
DF = pl.DataFrame({"uid": [r[0] for r in ROWS], "ev": [r[1] for r in ROWS], "ts": [r[2] for r in ROWS], "memo": ["x" * 30] * len(ROWS)}, schema_overrides={"ts": pl.Datetime("us")})
STEPS = ["view", "cart", "pay"]
HOW = {"kind": "funnel", "who": "uid", "step": "ev", "time": "ts", "steps": STEPS}


def users(found):
    return [s["users"] for s in found["steps"]]


@pytest.fixture(params=["memory", "polars"])
def t(request):
    return ibis.memtable(DF.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", DF)


def test_funnel_analysis_counts(t):
    found = analysis.analyze(t, HOW)
    assert found["kind"] == "funnel" and users(found) == [6, 4, 3]  # view 6명, cart까지 u1·u2·u3·u6, pay까지 u1·u2·u6
    assert found["window"] is None and found["window_seconds"] is None and found["range"] is None
    assert [round(s["from_first"], 4) for s in found["steps"]] == [1.0, round(4 / 6, 4), 0.5] and [s["drop"] for s in found["steps"]] == [0, 2, 1]


def test_funnel_analysis_window_and_range(t):
    half = analysis.analyze(t, {**HOW, "window": {"n": 30, "unit": "minute"}})
    assert users(half) == [6, 3, 3] and half["window_seconds"] == 1800  # u3은 1시간 뒤라 빠진다
    assert users(analysis.analyze(t, {**HOW, "window": {"n": 60, "unit": "minute"}})) == [6, 4, 3]  # 경계(정확히 1시간)는 포함
    assert users(analysis.analyze(t, {**HOW, "window": {"n": 1, "unit": "hour"}})) == [6, 4, 3]
    assert users(analysis.analyze(t, {**HOW, "window": {"n": 1, "unit": "day"}})) == [6, 4, 3]
    span = analysis.analyze(t, {**HOW, "range": {"start": "2026-01-01", "end": "2026-01-31"}})
    assert users(span) == [5, 3, 2]  # 한 달 뒤의 u6은 기간 밖
    assert users(analysis.analyze(t, {**HOW, "range": {"start": "2026-02-01", "end": None}})) == [1, 1, 1]
    assert users(analysis.analyze(t, {**HOW, "steps": ["view"]})) == [6]  # 단계 하나


def test_funnel_analysis_errors(t):
    for how, error in [({**HOW, "window": {"n": 0, "unit": "minute"}}, "0보다"), ({**HOW, "window": {"n": 5, "unit": "year"}}, "단위"), ({**HOW, "window": 30}, "단위"),
                       ({**HOW, "steps": []}, "단계는"), ({**HOW, "steps": ["view", "view"]}, "두 번"), ({**HOW, "time": "memo"}, "날짜"), ({**HOW, "who": "nope"}, "nope")]:
        with pytest.raises(ValueError, match=error):
            analysis.analyze(t, how)


@pytest.fixture
def shop(client, tmp_path):
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.register("df", DF.to_arrow())
        raw.execute("create table funnel_events as select * from df")
    before = {k: server.state.get(k) for k in ("con", "opened")}
    server.state.update(con=ibis.duckdb.connect(str(path)), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    yield client
    server.state.update(before)  # 다른 테스트가 이어받지 않게


def test_route_sample_full_and_card(shop):
    recipe = {"base": "funnel_events"}
    got = shop.post("/api/analyze", json={"recipe": recipe, "how": HOW}).get_json()
    assert users(got) == [6, 4, 3] and got["data"]["full"] is False
    full = shop.post("/api/analyze", json={"recipe": recipe, "how": {**HOW, "window": {"n": 30, "unit": "minute"}}, "full": True}).get_json()
    assert users(full) == [6, 3, 3] and full["data"] == {"full": True}
    values = shop.post("/api/analyze", json={"recipe": recipe, "how": {"kind": "distribution", "column": "ev"}}).get_json()["values"]
    assert values["labels"][0] == "view" and set(values["labels"]) == set(STEPS)  # 단계 값 고르기용 목록
    # 카드: 검사 → 다시 구하기(대시보드 기간 없이, 기간 있으면 날짜 안의 행만)
    card = {"kind": "funnel", "title": "구매 흐름", "view": {"type": ""}, "source": {"table": "funnel_events", "recipe": recipe, "analysis": HOW, "full": True, "date": "ts"}}
    checked = dash_store.check_card(card)
    plain = shop.post("/api/dash/card/compute", json={"card": checked}).get_json()["data"]
    assert users(plain["values"]) == [6, 4, 3] and plain["note"] == "테이블 전체" and plain["def_hash"]
    ranged = shop.post("/api/dash/card/compute", json={"card": checked, "ctx": {"range": {"start": "2026-01-01", "end": "2026-01-31"}}}).get_json()["data"]
    assert users(ranged["values"]) == [5, 3, 2] and ranged["values"]["range"] == {"start": "2026-01-01", "end": "2026-01-31"}
    other = dash_store.check_card({**card, "source": {**card["source"], "date": "uid"}})  # 시각 컬럼이 아닌 기간 기준은 걸지 않는다
    assert users(shop.post("/api/dash/card/compute", json={"card": other, "ctx": {"range": {"start": "2026-01-01", "end": "2026-01-31"}}}).get_json()["data"]["values"]) == [6, 4, 3]
    assert dash_calc.compute(checked, "2026-10-22T09:00:00")["def_hash"] == plain["def_hash"]


def test_funnel_card_store_checks():
    base = {"kind": "funnel", "title": "흐름", "source": {"recipe": {"base": "t"}, "analysis": HOW}}
    assert dash_store.check_card(base)["kind"] == "funnel"
    for source in ({"analysis": HOW}, {"recipe": {"base": "t"}}, {"recipe": {"base": "t"}, "analysis": {**HOW, "kind": "groups"}},
                   {"recipe": {"base": "t"}, "analysis": {**HOW, "steps": []}}, {"recipe": {"base": "t"}, "analysis": {**HOW, "steps": [str(i) for i in range(11)]}}):
        with pytest.raises(ValueError, match="흐름 카드"):
            dash_store.check_card({**base, "source": source})
    saved = dash_store.check_card({**base, "colors": {"series": {"이탈": "#e8590c", "다음 단계": "#2b8a3e"}}, "data": {"values": {"steps": []}}})
    assert saved["colors"]["series"]["이탈"] == "#e8590c"
    with pytest.raises(ValueError, match="HEX"):
        dash_store.check_card({**base, "colors": {"series": {"이탈": "주황"}}})


def test_funnel_card_is_refreshed():
    assert "funnel" in dash_refresh.REFRESHABLE
    card = dash_store.check_card({"kind": "funnel", "title": "흐름", "source": {"key": "k", "recipe": {"base": "t"}, "analysis": HOW}})
    assert dash_refresh.skip_reason({"refresh": {"every": "1시간"}}, card, "k", dt.datetime(2026, 1, 1)) == ""  # 다시 구할 수 있는 종류
    assert "다른 연결" in dash_refresh.skip_reason({"refresh": {"every": "1시간"}}, card, "other", dt.datetime(2026, 1, 1))


def test_screens_have_funnel(client):
    prep = client.get("/prep").get_data(as_text=True)
    for part in ('value="funnel"', 'id="a-fuser"', 'id="a-fstep"', 'id="a-ftime"', 'id="a-fsteps"', 'id="a-fwin"', 'id="a-funit"', 'id="a-funnel"', "/static/funnel.js"):
        assert part in prep, part
    assert 'kind: flow ? "funnel" : "analysis"' in prep
    dash = client.get("/dashboard").get_data(as_text=True)
    assert "/static/funnel.js" in dash
    for name in ("dash_cards.js", "dash_menu.js"):
        text = (ROOT / "static" / name).read_text(encoding="utf-8")
        assert "funnel" in text, name
    assert "paintFunnel" in (ROOT / "static" / "dash_cards.js").read_text(encoding="utf-8") and "흐름(퍼널" in (ROOT / "static" / "dash_menu.js").read_text(encoding="utf-8")
    assert 'if (k === "funnel")' in (ROOT / "static" / "dash_menu.js").read_text(encoding="utf-8")


SCRIPT = """
const assert = require("assert");
const close = (a, b) => Math.abs(a - b) < 1e-6;
const steps = [{ name: "a", users: 100, conv: 1, from_first: 1, drop: 0 }, { name: "b", users: 60, conv: 0.6, from_first: 0.6, drop: 40 },
               { name: "c", users: 30, conv: 0.5, from_first: 0.3, drop: 30 }, { name: "d", users: 30, conv: 1, from_first: 0.3, drop: 0 }];
const g = sankeyLayout(steps, { width: 600, height: 300, pad: 10, barW: 20, top: 30, bottom: 40, lane: 14 });
// 막대 높이는 사람 수에 비례한다(사람 한 명의 높이 = per)
assert.deepStrictEqual(g.bars.map(b => b.users), [100, 60, 30, 30]);
g.bars.forEach(b => assert(close(b.h, b.users * g.per), "막대 높이"));
assert(close(g.bars[0].h / g.bars[2].h, 100 / 30));
// 막대는 같은 위쪽에서 시작하고 가로로 고르게 놓인다
assert(g.bars.every(b => b.y === 30) && close(g.bars[0].x, 10) && close(g.bars[3].x, 570) && close(g.bars[1].x - g.bars[0].x, g.bars[2].x - g.bars[1].x));
// 다음 단계 띠 3개, 이탈 띠 2개(단계 b, c에서 빠짐)
assert.strictEqual(g.nexts.length, 3); assert.deepStrictEqual(g.drops.map(d => [d.step, d.users]), [[1, 40], [2, 30]]);
// 띠는 3차 베지어 둘 + 직선 둘로 닫힌 경로
for (const b of [...g.nexts, ...g.drops]) { assert.strictEqual((b.path.match(/C/g) || []).length, 2); assert.strictEqual((b.path.match(/L/g) || []).length, 1); assert(b.path.startsWith("M") && b.path.endsWith("Z")); }
// 다음 단계 띠는 앞 막대의 오른쪽 위쪽에서 뒤 막대의 왼쪽까지, 두께가 뒤 막대 높이와 같다
const first = g.nexts[0].path.match(/-?\\d+\\.?\\d*/g).map(Number);  // M x0 y0a C... x1 y1a L x1 y1b C ... x0 y0b
assert(close(first[0], g.bars[0].x + 20) && close(first[1], 30)); assert(close(first[first.length - 2], first[0]) && close(first[first.length - 1], 30 + g.bars[1].h));
// 이탈 띠: 앞 막대의 남는 아랫부분(다음 단계 높이 아래)에서 시작해 막대 아래 칸으로 내려간다
const lost = g.drops[0].path.match(/-?\\d+\\.?\\d*/g).map(Number);
assert(close(lost[1], 30 + g.bars[1].h)); assert(lost[7] > 30 + g.bars[0].h);  // 도착(위 가장자리 끝)은 첫 막대 아래
assert(close(lost[9] - lost[7], 40 * g.per));  // 이탈 띠 두께는 이탈한 사람 수에 비례
// 전체 높이 안에 들어간다
assert(Math.max(...g.drops.flatMap(d => { const n = d.path.match(/-?\\d+\\.?\\d*/g).map(Number); return [n[n.length - 1], n[n.length - 3] ?? 0]; })) <= g.height - 40 + 1e-6);
// 사람이 없으면 막대는 높이 0, 띠는 없다. 한 단계면 가운데에 막대 하나
const none = sankeyLayout([{ name: "a", users: 0 }, { name: "b", users: 0, drop: 0 }]);
assert(none.bars.every(b => b.h === 0) && none.nexts.length === 0 && none.drops.length === 0);
const one = sankeyLayout([{ name: "a", users: 5, from_first: 1, conv: 1, drop: 0 }], { width: 200 });
assert.strictEqual(one.bars.length, 1); assert(close(one.bars[0].x, 89)); assert.strictEqual(one.nexts.length, 0);
// 한 명이라도 있으면 막대는 1px 이상 보인다
assert(sankeyLayout([{ name: "a", users: 100000 }, { name: "b", users: 1, drop: 99999 }]).bars[1].h >= 1);
// 표와 한 줄 설명
assert.deepStrictEqual(funnelRows(steps)[0], { 단계: "a", "사용자 수": 100, "다음 단계 전환율": "60.0%", "처음 대비": "100.0%", "이탈 수": 40 });
assert.deepStrictEqual(funnelRows(steps)[3], { 단계: "d", "사용자 수": 30, "다음 단계 전환율": "-", "처음 대비": "30.0%", "이탈 수": "-" });
assert(funnelSentence(steps).includes("가장 많이 빠진 곳은 a → b") && funnelSentence(steps).includes("40명(40.0%)"));
assert.strictEqual(funnelSentence([{ name: "a", users: 0 }]), "첫 단계에 도달한 사용자가 없어요.");
assert.strictEqual(windowText({ n: 30, unit: "minute" }), "첫 단계부터 30분 안에 도달한 사람만"); assert.strictEqual(windowText(null), "기간 제한 없음");
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_sankey_geometry_in_node(tmp_path):
    script = tmp_path / "funnel_test.js"
    script.write_text((ROOT / "static" / "funnel.js").read_text(encoding="utf-8") + SCRIPT, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]
