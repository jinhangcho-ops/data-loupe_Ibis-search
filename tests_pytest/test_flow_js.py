"""static/query/flow.js(흐름 보기)와 static/query/sentences.js(문장 보기)의 화면 없는 계산을 node로 돌려 본다. node가 없으면 건너뛴다.
배치(깊이·겹침), 이음 규칙(서버 flow.edge_problem의 모양·순서·순환과 같은 판단), 끼우기·지우기·꼬리 붙이기, 조건 묶음 되돌리기(index.html의 grouped와 서로 반대),
문장 조각·빈칸 보임·기본값·컬럼 맞춤·기간 칸·결과 차트·카드 만들기. SERVER.flow는 서버 값을 그대로 쓴다. 값은 손으로 센 작은 예제다."""
import json
import re
import shutil
import subprocess

import pytest

from query import flow
from query import flow_app  # noqa: F401  server.constants("query")["flow"]
from query import sentences
from core import server
from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def run_node(tmp_path, body, name="case"):
    """flow.js·sentences.js를 이어 붙이고 body를 실행한 마지막 줄(JSON)을 돌려준다."""
    head = "const assert = require('assert'); globalThis.SERVER = {flow: %s};\n" % json.dumps(server.constants("query")["flow"], ensure_ascii=False)
    script = tmp_path / f"{name}.js"
    script.write_text(head + (ROOT / "static" / "query" / "flow.js").read_text(encoding="utf-8") + (ROOT / "static" / "query" / "sentences.js").read_text(encoding="utf-8") + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


# 흐름: 테이블 둘 → 조인 → 기간 → 조건 → 집계 → 차트
GRAPH = {"nodes": [{"id": "t1", "kind": "table", "params": {"table": "orders"}}, {"id": "t2", "kind": "table", "params": {"table": "shops"}},
                   {"id": "j", "kind": "join", "params": {"how": "inner", "keys": [["shop_id", "shop_id"]]}}, {"id": "p", "kind": "period", "params": {"line": "day 2026-01-01 ~"}},
                   {"id": "c", "kind": "condition", "params": {"cond": "amt >= 10"}}, {"id": "a", "kind": "aggregate", "params": {"by": ["country"], "series": [{"agg": "sum", "y": "amt", "name": "합"}]}},
                   {"id": "ch", "kind": "chart", "params": {"x": "country"}}],
         "edges": [["t1", "j"], ["t2", "j"], ["j", "p"], ["p", "c"], ["c", "a"], ["a", "ch"]]}


def test_layout_depth_and_no_overlap(tmp_path):
    """배치: 이음의 끝은 시작보다 오른쪽 칸에 놓이고, 어떤 두 단계도 겹치지 않는다. 순환이 있어도 끝난다."""
    got = run_node(tmp_path, "const g = %s; const at = flowLayout(g);\n"
                   "const cyc = { nodes: [{ id: 'a', kind: 'calc' }, { id: 'b', kind: 'calc' }], edges: [['a', 'b'], ['b', 'a']] };\n"
                   "console.log(JSON.stringify({ at, cyc: Object.keys(flowLayout(cyc)) }));" % json.dumps(GRAPH))
    at = got["at"]
    assert all(at[b]["x"] > at[a]["x"] for a, b in GRAPH["edges"])
    assert at["t1"]["x"] == at["t2"]["x"] and at["t1"]["y"] != at["t2"]["y"]  # 같은 칸의 두 테이블은 위아래로
    boxes = [(p["x"], p["y"]) for p in at.values()]
    assert len(set(boxes)) == len(boxes)
    assert sorted(got["cyc"]) == ["a", "b"]


EDGE_CASES = [("c", "p"), ("c", "t1"), ("ch", "c"), ("t1", "p"), ("p", "c"), ("t1", "a"), ("a", "ch"), ("c", "c"), ("j", "t2"), ("t1", "j"), ("a", "p"), ("zz", "p"), ("t1", "c"), ("p", "a")]


def test_local_edge_rules_match_server(tmp_path):
    """화면이 끌 때 미리 보는 이음 규칙(모양·순서·순환·한 곳에 하나)이 서버 flow.edge_problem과 같은 판단을 한다(막는지 아닌지, 이유 문장까지)."""
    got = run_node(tmp_path, "const g = %s; console.log(JSON.stringify(%s.map(([a, b]) => flowEdgeLocal(g, a, b))));" % (json.dumps(GRAPH), json.dumps(EDGE_CASES)))
    for (a, b), local in zip(EDGE_CASES, got):
        found = flow.edge_problem(GRAPH, a, b, None)
        assert bool(local) == bool(found), (a, b, local, found)
        if found:
            assert local == found["reason"]


def test_cycle_refused_locally(tmp_path):
    """같은 순위끼리 되돌아가는 이음은 순환으로, 이미 앞 단계가 있는 단계에 또 잇는 것은 한 곳에 하나로 막는다."""
    chain = {"nodes": [{"id": "x", "kind": "calc", "params": {}}, {"id": "y", "kind": "calc", "params": {}}, {"id": "z", "kind": "calc", "params": {}}], "edges": [["x", "y"], ["y", "z"]]}
    got = run_node(tmp_path, "const g = %s; console.log(JSON.stringify([flowEdgeLocal(g, 'z', 'x'), flowEdgeLocal(g, 'x', 'z')]));" % json.dumps(chain))
    assert got[0] == "순환이 생겨요: 계산(x) → 계산(y) → 계산(z) → 계산(x)." and "하나만 이을 수 있어요" in got[1]  # 되돌리면 순환, 앞 단계가 있는 단계에 또 이으면 한 곳에 하나
    assert flow.edge_problem(chain, "z", "x", None)["code"] == "cycle"


def test_insert_remove_keep_order(tmp_path):
    """이음 사이에 끼우면 그 자리(edges 차례)가 그대로라 조인의 왼쪽·오른쪽이 바뀌지 않는다. 가운데 단계를 지우면 앞뒤가 이어진다."""
    got = run_node(tmp_path, "const g = %s; const ins = flowInsert(g, 't2', 'j', 'condition', { cond: 'x = 1' }); const rm = flowRemove(g, 'p');\n"
                   "console.log(JSON.stringify({ edges: ins.graph.edges, id: ins.id, kinds: flowInsertKinds(g, 'j', 'p'), kinds2: flowInsertKinds(g, 'p', 'c'), kinds3: flowInsertKinds(g, 'c', 'a'), rm: rm.edges, n: rm.nodes.length, cut: flowCut(g, 'a', 'ch').edges.length }));" % json.dumps(GRAPH))
    assert got["edges"][:2] == [["t1", "j"], ["t2", got["id"]]] and got["edges"][2] == [got["id"], "j"]
    assert got["kinds"] == ["period"] and got["kinds2"] == ["period", "condition"] and got["kinds3"] == ["condition", "calc", "dedupe"]  # 순서(기간→조건→계산→중복 제거)가 맞는 종류만
    assert ["j", "c"] in got["rm"] and got["n"] == 6 and got["cut"] == 5


def test_tail_detach_and_attach(tmp_path):
    """집계·차트(단계 카드에 없는 끝)는 떼어 두었다가 새 body로 만든 흐름의 끝에 다시 붙는다. 꼬리 바로 앞 단계가 body의 끝(target)이다."""
    got = run_node(tmp_path, "const g = %s; const tail = flowTail(g); const base = { nodes: g.nodes.filter(n => !['a', 'ch'].includes(n.id)).map(n => ({ ...n, id: 'm' + n.id })), edges: [['mt1', 'mj'], ['mt2', 'mj'], ['mj', 'mp'], ['mp', 'mc']] };\n"
                   "const back = flowAttach(base, tail); console.log(JSON.stringify({ target: flowBodyTarget(g), tail: tail.nodes.map(n => n.id), tailEdges: tail.edges, feed: tail.feed, back: back.edges.slice(-2), ids: back.nodes.map(n => n.id), none: flowAttach(base, { nodes: [], edges: [], feed: [] }) === base }));" % json.dumps(GRAPH))
    assert got["target"] == "c" and got["tail"] == ["a", "ch"] and got["tailEdges"] == [["a", "ch"]] and got["feed"] == ["c"]
    assert got["back"] == [["a", "ch"], ["mc", "a"]] or got["back"] == [["mc", "a"], ["a", "ch"]]
    assert len(set(got["ids"])) == len(got["ids"]) and got["none"]


def test_node_text_and_defaults(tmp_path):
    got = run_node(tmp_path, "const g = %s; console.log(JSON.stringify({ t: g.nodes.map(flowNodeText), d: flowDefault('period', { columns: ['id', 'day'], dates: ['day'] }), d2: flowDefault('condition', { columns: ['id'] }), d3: flowDefault('table', { table: 'x' }), clip: flowClip('가'.repeat(40), 5) }));" % json.dumps(GRAPH))
    assert got["t"] == ["orders", "shops", "inner shop_id=shop_id", "day 2026-01-01 ~", "amt >= 10", "country · 합", "country"]
    assert got["d"] == {"line": "day ~ "} and got["d2"] == {"cond": "id is not null"} and got["d3"] == {"table": "x"} and got["clip"] == "가가가가…"


def test_diff_and_upstream(tmp_path):
    got = run_node(tmp_path, "const g = %s; const o = flowCopy(g); o.nodes.find(n => n.id === 'c').params = { cond: 'amt >= 99' }; o.nodes.push({ id: 'n9', kind: 'calc', params: {} }); o.nodes = o.nodes.filter(n => n.id !== 'p');\n"
                   "console.log(JSON.stringify({ marks: flowDiff(g, o), up: flowUpstream(g, 'c').sort(), cols: flowColumns(g, 'a', { orders: { columns: { shop_id: 'num', amt: 'num' } }, shops: { columns: { shop_id: 'num', country: 'text' } } }), jc: flowColumns(g, 'j', { orders: { columns: { shop_id: 'num', amt: 'num' } }, shops: { columns: { shop_id: 'num', country: 'text' } } }), root: flowRoot(g, 'c').id }));" % json.dumps(GRAPH))
    assert got["marks"] == {"c": "chg", "n9": "add", "p": "del"} and got["up"] == ["c", "j", "p", "t1", "t2"]
    assert got["cols"] == {"country": "other", "합": "num"} or got["cols"]["합"] == "num"
    assert got["jc"] == {"shop_id": "num", "amt": "num", "shop_id_right": "num", "country": "text"} and got["root"] == "t1"  # 같은 이름은 오른쪽에 _right(서버 right_names와 같다)


def test_join_columns_match_server():
    left, right = {"shop_id": "num", "amt": "num"}, {"shop_id": "num", "country": "text"}
    assert list(flow.right_names(left, right, "inner")) == ["shop_id", "amt", "shop_id_right", "country"]


def server_grouped_from_index():
    """index.html의 grouped()(단계 카드 → 서버로 보낼 조건 모양)를 그대로 뽑는다."""
    html = (ROOT / "static" / "query" / "index.html").read_text(encoding="utf-8")
    start = html.index("function grouped() {")
    return html[start:html.index("\n}\n", start) + 2]


def test_ungroup_is_inverse_of_grouped(tmp_path):
    """조건의 {any} 묶음을 단계 카드 항목으로 되돌린 것(flowUngroup)을 index.html의 grouped()로 다시 묶으면 처음 모양이 된다(세 보기가 같은 body)."""
    conds = ["a = 1", {"any": ["b = 2", "c = 3", {"line": "d = 4", "quotes": False}]}, {"line": "e = 5", "quotes": False}, "f = 6"]
    body = "const S = { conditions: [] }; %s\nconst conds = %s; S.conditions = flowUngroup(conds);\nconsole.log(JSON.stringify({ items: S.conditions, back: grouped() }));" % (server_grouped_from_index(), json.dumps(conds))
    got = run_node(tmp_path, body)
    assert got["back"] == conds
    assert got["items"][1] == {"line": "b = 2", "or": True} and got["items"][2] == {"line": "c = 3", "or": True} and got["items"][3] == {"line": "d = 4", "quotes": False}


# ---------- 문장 ----------

def test_sentence_tokens_visible_defaults(tmp_path):
    """문장 글을 조각으로, 곁가지 칸(when)은 조건이 맞을 때만, 기본값은 틀의 default·첫 선택지."""
    t26, t13 = sentences.get("e26"), sentences.get("e13")
    got = run_node(tmp_path, "const t = %s, t13 = %s; const P = t.slots.find(s => s.name === 'P');\n"
                   "console.log(JSON.stringify({ tokens: sentTokens(t13.text), v1: sentVisible(P, { 종류: '중앙값' }), v2: sentVisible(P, { 종류: '분위수 N' }), d: sentDefaults(t), d13: sentDefaults(t13), tm: sentDefaults(%s) }));"
                   % (json.dumps(t26, ensure_ascii=False), json.dumps(t13, ensure_ascii=False), json.dumps(sentences.get("e05"), ensure_ascii=False)))
    assert got["tokens"] == [{"slot": "무엇"}, {"text": "별 "}, {"slot": "값"}, {"text": "의 "}, {"slot": "집계"}, {"text": " 상위 "}, {"slot": "N"}, {"text": "개를 봐요"}]
    assert got["v1"] is False and got["v2"] is True
    assert got["d"] == {"종류": "중앙값", "P": 0.5} and got["d13"] == {"N": 10} and got["tm"] == {"글자": {"match": "포함", "text": ""}}


def test_every_template_text_tokens_match_slots(tmp_path):
    """모든 문장 틀(탐색·지표 62개): 글 속 {빈칸} 조각이 그 틀의 빈칸 이름에 모두 있다(그려지지 않는 칸이 없다)."""
    got = run_node(tmp_path, "const all = %s; console.log(JSON.stringify(all.map(t => [t.id, sentTokens(t.text).filter(x => x.slot).map(x => x.slot).filter(n => !t.slots.some(s => s.name === n))])));" % json.dumps(sentences.TEMPLATES, ensure_ascii=False))
    assert len(got) == 62 and [row for row in got if row[1]] == []


def test_fit_columns_other_table(tmp_path):
    """질문 묶음을 다른 테이블에서 열면 같은 이름 컬럼만 채우고 없는 컬럼의 칸은 비운다(어느 칸을 비웠는지 알린다)."""
    t30, t02 = sentences.get("e30"), sentences.get("e02")
    got = run_node(tmp_path, "const t30 = %s, t02 = %s;\n"
                   "console.log(JSON.stringify([sentFitColumns(t30, { 무엇: 'shop', 컬럼: { column: 'name', prep: 'lower' }, '정렬 기준': 'day desc', 구분자: ',' }, { shop: 'text', name: 'text' }),"
                   " sentFitColumns(t30, { 무엇: 'shop', 컬럼: 'name', '정렬 기준': 'day desc' }, { id: 'num' }), sentFitColumns(t02, { 컬럼들: ['a', 'b', 'c'] }, { a: 'num', c: 'num' })]));"
                   % (json.dumps(t30, ensure_ascii=False), json.dumps(t02, ensure_ascii=False)))
    same, other, multi = got
    assert same["gone"] == ["정렬 기준"] and same["slots"] == {"무엇": "shop", "컬럼": {"column": "name", "prep": "lower"}, "구분자": ","}
    assert other["gone"] == ["무엇", "컬럼", "정렬 기준"] and other["slots"] == {}
    assert multi["gone"] == ["컬럼들"] and multi["slots"] == {"컬럼들": ["a", "c"]}


def test_period_slot_filter_kinds(tmp_path):
    got = run_node(tmp_path, "console.log(JSON.stringify({ ok: sentPeriod('day', { start: '2026-03-01', end: '2026-03-31' }), open: sentPeriod('day', { start: '2026-03-01' }), err: sentPeriod('day', { error: 'x' }), none: sentPeriod('', { start: 'a' }),"
                   " f: sentFilter(%s, '중앙값').map(t => t.id), all: sentFilter(%s, '').length, kinds: sentKinds([['a', '정수'], ['b', '문자']], t => t === '정수' ? 'num' : 'text'), chip: ['num', 'date', 'text', 'json'].map(sentChip), fit: [sentFits('any', 'num'), sentFits(['num', 'date'], 'text'), sentFits('text', 'text')] }));"
                   % (json.dumps(sentences.EXPLORE, ensure_ascii=False), json.dumps(sentences.EXPLORE, ensure_ascii=False)))
    assert got["ok"] == {"column": "day", "start": "2026-03-01", "end": "2026-03-31"} and got["open"] == {"column": "day", "start": "2026-03-01", "end": ""}
    assert got["err"] is None and got["none"] is None and got["f"] == ["e26"] and got["all"] == 47
    assert got["kinds"] == {"a": "num", "b": "text"} and got["chip"] == ["num", "date", "text", "other"] and got["fit"] == [True, False, True]


def test_result_chart_and_card(tmp_path):
    """끝난 표 → 차트(글자 라벨 + 숫자 계열은 막대, 날짜 라벨은 선, 숫자만 있으면 첫 컬럼이 라벨), 50개 넘으면 잘림 표시. 묶어 집계한 문장만 대시보드 차트 카드가 된다."""
    rows = [{"국가": "KR", "합": 3, "건": 1}, {"국가": "JP", "합": 5, "건": None}]
    days = [{"일": f"2026-03-{d:02d}", "값": d} for d in range(1, 4)]
    big = [{"k": f"k{i}", "v": i} for i in range(60)]
    pivot = {"ok": True, "runs": [{"route": "/api/pivot", "body": {"base": "orders", "joins": [], "periods": ["d 2026-03-01 ~"], "pivot": {"rows": ["country", "status"], "how": "sum", "value": "amt"}}}]}
    got = run_node(tmp_path, "const A = sentChart({ columns: ['국가', '합', '건'], rows: %s }, ['#111', '#222']), B = sentChart({ columns: ['일', '값'], rows: %s }), C = sentChart({ columns: ['k', 'v'], rows: %s }), D = sentChart({ columns: ['a'], rows: [{ a: 'x' }] }), E = sentChart({ columns: ['a'], rows: [] });\n"
                   "const card = sentCard(%s, '제목'), no1 = sentCard({ ok: true, combine: { op: 'ratio' }, runs: [{}, {}] }, 't'), no2 = sentCard({ ok: true, runs: [{ route: '/api/query', body: {} }] }, 't'), no3 = sentCard({ ok: false, reason: '못 만들어요' }, 't');\n"
                   "console.log(JSON.stringify({ A: [A.type, A.data.labels, A.data.datasets.map(d => [d.label, d.data, d.backgroundColor]), A.options.plugins.legend.display], B: [B.type, B.data.labels.length], C: [C.data.labels.length, C.truncated], D, E, card, no1, no2, no3 }));"
                   % (json.dumps(rows, ensure_ascii=False), json.dumps(days), json.dumps(big), json.dumps(pivot)))
    assert got["A"] == ["bar", ["KR", "JP"], [["합", [3, 5], "#111"], ["건", [1, None], "#222"]], True]
    assert got["B"] == ["line", 3] and got["C"] == [50, True] and got["D"] is None and got["E"] is None
    assert got["card"] == {"kind": "chart", "title": "제목", "body": {"base": "orders", "joins": [], "periods": ["d 2026-03-01 ~"], "x": "country", "split": "status", "order": "", "series": [{"y": "amt", "agg": "sum"}]}}
    assert "여러 계산" in got["no1"]["reason"] and "묶어 집계" in got["no2"]["reason"] and got["no3"] == {"reason": "못 만들어요"}


def test_sentence_set_shape(tmp_path):
    got = run_node(tmp_path, "console.log(JSON.stringify(sentSetOf(true, { column: 'day', text: '이번 달' }, [{ id: 3, template: 'e06', slots: {}, period: { column: 'a', text: 'x', span: { start: 'q' } }, result: { big: 1 } }])));")
    assert got == {"shared": True, "period": {"column": "day", "text": "이번 달"}, "items": [{"template": "e06", "slots": {}, "period": {"column": "a", "text": "x", "span": {"start": "q"}}}]}


def test_page_has_views_and_ends_operator():
    """단계 카드 화면에 세 보기 전환과 새 파일들이 달려 있고, 조건 연산자 목록에 ends가 있다(서버 parse가 지원한다)."""
    html = (ROOT / "static" / "query" / "index.html").read_text(encoding="utf-8")
    for needle in ('id="vswitch"', 'data-v="flow"', 'data-v="sentence"', '"/static/query/flow.js"', '"/static/query/sentences.js"', 'href="/static/query/flow.css"'):
        assert needle in html
    ops = re.search(r"const OPS_TEXT = \[(.*?)\]", html).group(1)
    assert '"ends"' in ops and "컬럼 ends 값" in html
    from core import parse
    assert "ends" in parse.CONDITION_GUIDE and "|ends)" in parse.LIKE_RE.pattern
