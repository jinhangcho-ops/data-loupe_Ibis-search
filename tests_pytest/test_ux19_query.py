"""Phase 19 묶음 Q(쿼리 화면, plans/phase-19.md 19C1~19C5·19D1~19D3).
화면 글자·접기(data-adv 묶음 안의 .adv)·id 유지는 index.html을 읽어 정적으로, 화면 함수(showFlowGuide·showSqlTables·showMetrics)는 node로 돌려 본다.
각 시험은 고치기 전 코드에서 실패하도록 짰다."""
from html.parser import HTMLParser

from test_fix_audit_js import between, read, run_node
from test_ux16 import CLS, needs_node

PAGE = "query/index.html"


class Tree(HTMLParser):
    """id가 있는 요소마다 자기와 조상들의 속성(dict) 목록을 모은다(자기 것이 맨 앞)."""
    VOID = {"input", "br", "img", "meta", "link", "hr", "option"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict] = []
        self.ids: dict[str, list[dict]] = {}

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k: v or "" for k, v in attrs}
        a["<tag>"] = tag
        chain = [a, *reversed(self.stack)]
        if "id" in a:
            self.ids[a["id"]] = chain
        if tag not in self.VOID:
            self.stack.append(a)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        while self.stack and self.stack.pop()["<tag>"] != tag:
            pass


def tree() -> dict[str, list[dict]]:
    html = read("index.html")
    body = html[html.index("<body"):html.index("<script src=")]
    t = Tree()
    t.feed(body)
    return t.ids


def folded(ids: dict, name: str) -> bool:
    """name(또는 감싼 요소)이 .adv이고, 그 위에 data-adv 묶음이 있다."""
    chain = ids[name]
    at = next((i for i, a in enumerate(chain) if "adv" in a.get("class", "").split()), None)
    return at is not None and any("data-adv" in a for a in chain[at:])


def step(ids_html: str, key: str) -> str:
    """data-step=key 단계 카드의 여는 태그부터 머리글 줄까지."""
    a = ids_html.index(f'data-step="{key}"')
    return ids_html[ids_html.rindex("<div", 0, a):ids_html.index("</div>", a)]


# ---------- 19C2·19C3·19C4·19C5 접기 ----------

def test_19c2_rare_steps_folded_common_steps_shown():
    ids, html = tree(), read("index.html")
    assert "data-adv" in ids["flow"][0]
    for key in ("union", "joins", "calcs", "columns"):
        assert 'class="step adv"' in step(html, key), key
    for key in ("data", "periods", "conditions", "sorts"):
        assert "adv" not in step(html, key).split(">")[0], key
    for name in ("u-how", "join-add", "cf-kind", "col-pick"):  # 접힌 단계 안의 칸, id 그대로
        assert folded(ids, name), name
    for name in ("base", "periods", "conditions", "sorts", "search"):
        assert not folded(ids, name), name


def test_19c3_toolbar_keeps_find_and_export_only():
    ids = tree()
    assert "data-adv" in ids["sql-show"][1]
    for name in ("sql-show", "plan-show", "handoff", "label-open", "label-edit", "label-one-open"):
        assert folded(ids, name), name
    for name in ("row-find", "export-open"):
        assert not folded(ids, name), name


def test_19c4_pager_folds_maxlen_and_count():
    ids = tree()
    assert "data-adv" in ids["pager"][0]
    assert folded(ids, "maxlen") and folded(ids, "count")
    assert not folded(ids, "limit") and not folded(ids, "prev")


def test_19c5_chart_starts_with_axes_calc_type_draw():
    ids = tree()
    assert "data-adv" in ids["pane-chart"][0]
    for name in ("cx", "cy", "cagg", "ctype", "draw"):
        assert not folded(ids, name), name
    for name in ("cunit-box", "csplit", "corder", "cratio", "cdash", "caxis", "cscale", "series-add", "cswap",
                 "ctable-show", "ctable-csv", "cexport-all", "cimage", "csend", "csend-go", "ymin", "y1max", "cgroup-box", "cauto", "call"):
        assert folded(ids, name), name
    html = read("index.html")
    row1 = html[html.index('id="cx"'):html.index('id="draw"')]
    assert 'id="cy"' in row1 and 'id="cagg"' in row1 and 'id="ctype"' in row1  # 첫 줄에 X·Y·계산·종류


def test_19c_tabs_and_steps_explained():
    html = read("index.html")
    for pane, word in (("pane-rows", "행"), ("pane-summary", "개수·평균 같은 요약"), ("pane-chart", "그림으로")):
        a = html.index(f'id="{pane}"')
        lead = html[a:html.index("</p>", a)]
        assert '<p class="lead">' in lead and word in lead, pane
    tabs = html[html.index('<div class="tabs2">'):html.index("</div>", html.index('<div class="tabs2">'))]
    assert tabs.count("title=") == 3
    assert '<span class="t" title="다른 테이블의 컬럼을 옆에 붙여요">조인</span>' in html
    assert '<span class="t" title="같은 모양의 테이블을 아래로 이어 붙여요">행 합치기</span>' in html
    assert html.count('<span class="t" title=') == 8


# ---------- 19C1·19C2 단계 안내(node) ----------

FLOW = CLS + r"""
const ELS = {}, steps = ["data", "union", "joins", "periods"].map(k => ({ id: k === "data" ? "step-data" : "", classList: cls() }));
const $ = id => ELS[id] ??= { id, classList: cls() };
globalThis.document.querySelectorAll = sel => sel === ".step" ? steps : [];
let shownAdv = 0; const showAdv = box => { if (box === $("flow")) shownAdv++; };
const S = { base: "", union: { tables: [] }, joins: [], calcs: [], columns: [] };
"""


@needs_node
def test_19c1_no_table_opens_and_marks_first_step(tmp_path):
    part = between("index.html", "function showFlowGuide()", "\n}\n") + "\n}\n"
    body = r"""
steps[2].classList.add("open"); showFlowGuide();
const before = { first: $("flow").classList.contains("first"), open: steps.map(s => s.classList.contains("open")) };
S.base = "orders"; steps[2].classList.add("open"); showFlowGuide();
const after = { first: $("flow").classList.contains("first"), open: steps.map(s => s.classList.contains("open")) };
console.log(JSON.stringify({ before, after, shownAdv }));"""
    got = run_node(tmp_path, body, parts=[FLOW, part])
    assert got["before"] == {"first": True, "open": [True, False, False, False]}
    assert got["after"]["first"] is False and got["after"]["open"][2] is True  # 고른 뒤에는 단계를 건드리지 않는다
    assert got["shownAdv"] == 0
    css = read("index.html")
    assert ".flow.first .step:not(#step-data)" in css and "opacity" in css[css.index(".flow.first .step:not(#step-data)"):][:80]


@needs_node
def test_19c2_folded_step_with_value_opens(tmp_path):
    part = between("index.html", "function showFlowGuide()", "\n}\n") + "\n}\n"
    body = r"""
S.base = "orders"; showFlowGuide(); const empty = shownAdv;
S.joins = ["customers left id=id"]; showFlowGuide(); const joined = shownAdv;
S.joins = []; S.columns = ["a"]; showFlowGuide();
console.log(JSON.stringify([empty, joined, shownAdv]));"""
    assert run_node(tmp_path, body, parts=[FLOW, part]) == [0, 1, 2]
    html = read("index.html")
    assert "showSummaries = () => { showFlowGuide(); summed(); }" in html  # 모든 화면 갱신(불러오기·초안·되돌리기 포함) 때
    rest = html[html.index("async function restore(st)"):]
    assert "advMore();" in rest[:rest.index("\n}\n")]  # 탭을 되살린 뒤 차트 칸의 값으로 펼침


# ---------- 19D1 SQL 자리 글 ----------

@needs_node
def test_19d1_sql_placeholder_uses_first_table(tmp_path):
    html = read("index.html")
    assert "상태 LIKE" not in html[html.index('id="sql-text"'):][:200]
    part = between("index.html", "function showSqlTables()", "\n}\n") + "\n}\n"
    stubs = r"""
const ELS = {}, $ = id => ELS[id] ??= { id, placeholder: "", replaceChildren() {}, querySelectorAll: () => opts };
let opts = [{ value: "" }, { value: "orders" }, { value: "items" }]; const insertSql = () => {};
"""
    body = r"""
showSqlTables(); const one = $("sql-text").placeholder;
opts = []; showSqlTables();
console.log(JSON.stringify([one, $("sql-text").placeholder]));"""
    one, none = run_node(tmp_path, body, parts=[stubs, part])
    assert one.startswith("SELECT * FROM orders LIMIT 10")
    assert "상태" not in none and "orders" not in none


# ---------- 19D2 쉬운 title ----------

def test_19d2_plain_titles():
    ids, html = tree(), read("index.html")
    assert "틀린 곳" in ids["sql-deep"][0]["title"]
    assert ids["sql-plan-show"][0]["title"].startswith("실행하지 않고")
    assert "자주 쓰는 SQL에 이름을 붙여 테이블처럼 골라 써요. DB는 바뀌지 않아요" in html


# ---------- 19D3 지표 ----------

@needs_node
def test_19d3_no_metrics_hides_period_row(tmp_path):
    part = between("index.html", "function showMetrics(d)", "\n}\n") + "\n}\n"
    stubs = CLS + r"""
const ELS = {}, $ = id => ELS[id] ??= { id, value: "", open: false, classList: cls(), replaceChildren() {} };
let metDrawn = null; const busy = () => {}, runMetric = () => {}, noMetrics = () => ({});
"""
    body = r"""
const ROW = ["met-pcol", "met-period", "met-cal", "met-cmp", "met-dash"];
showMetrics({ path: "", metrics: [], order: [] }); const none = ROW.map(id => $(id).classList.contains("hidden"));
showMetrics({ path: "", metrics: [{ name: "m", text: "t" }], order: ["m"] }); const some = ROW.map(id => $(id).classList.contains("hidden"));
console.log(JSON.stringify({ none, some }));"""
    got = run_node(tmp_path, body, parts=[stubs, part])
    assert got == {"none": [True] * 5, "some": [False] * 5}
    ids = tree()
    assert "flex:1 1 100%" in ids["met-on"][0]["style"]  # 안내 글은 좁은 칸에 꺾이지 않고 한 줄 전체
