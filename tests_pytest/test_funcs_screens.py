"""함수 사전을 화면에 잇는 부분(10-3): GET /api/funcs(ETag)·POST /api/funcs/support, 연결이 못 하는 집계는 대체 식이나 이 PC 사본으로,
화면(static)에 집계 목록을 직접 적어 두지 않았는지(grep), 모든 화면이 funcs.js의 고르는 칸을 쓰는지, funcs.js의 칸 그리기를 node로 돌려 본 결과.
값은 테스트 안에서 지어낸 작은 표와 손으로 계산한 값이다."""
import json
import re
import shutil
import sqlite3
import subprocess

import pytest

from core import sql_funcs
from conftest import ROOT

STATIC = ROOT / "static"
ORDERS = [(i, "AB"[i % 2], ["서울", "부산"][i % 2], float(i * 7 % 13 + 1), i % 4 + 1) for i in range(1, 25)]  # id, 매장, 지역, 금액, 수량


def connect(client, info):
    assert client.post("/api/connect", json=info).status_code == 200


@pytest.fixture
def real(folder, tmp_path):
    """진짜 연결(가짜 연결이 아닌)을 쓰는 시험 클라이언트."""
    from dashboard import dash_app  # noqa: F401  대시보드 라우트
    from prep import prep_app  # noqa: F401  전처리 라우트
    from core import server
    from query.search_app import app
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    yield c, tmp_path
    c.post("/api/disconnect", json={})


def lite_client(real):
    c, tmp = real
    raw = sqlite3.connect(tmp / "s.db")
    raw.execute("create table orders (id integer, store text, region text, amt real, qty integer)")
    raw.executemany("insert into orders values (?, ?, ?, ?, ?)", ORDERS)
    raw.commit()
    raw.close()
    connect(c, {"kind": "url", "url": f"sqlite://{tmp / 's.db'}"})
    return c


# ---------- 함수 사전 라우트 ----------

def test_funcs_route_serves_the_catalog_with_etag(client):
    from core import server
    reply = client.get("/api/funcs")
    assert reply.status_code == 200 and reply.get_json() == json.loads(json.dumps(sql_funcs.catalog()))
    tag = reply.headers["ETag"]
    assert tag == f'"{sql_funcs.version()}"' and "max-age" in reply.headers["Cache-Control"]
    again = client.get("/api/funcs", headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.data == b""
    assert client.get("/api/funcs", headers={"If-None-Match": '"old"'}).status_code == 200
    assert "/api/funcs" in server.FREE and "/api/funcs/support" not in server.FREE  # 사전은 연결 없이, 지원 판정은 연결을 쓴다


def test_constants_keep_only_small_lists(client):
    from core import server
    assert "funcs" not in server.CONSTANTS and server.CONSTANTS["funcs_ver"] == sql_funcs.version()
    assert server.CONSTANTS["agg_names"]["nunique"] == "고유 개수" and server.CONSTANTS["agg_names"]["median"] == "중앙값" and server.CONSTANTS["merge"] == list(sql_funcs.MERGE)
    assert len(json.dumps(server.CONSTANTS, ensure_ascii=False)) < 12_000  # 모든 화면에 들어가는 값이라 작게(사전 전체는 66KB쯤)
    html = client.get("/search").get_data(as_text=True)
    assert sql_funcs.version() in html and '"kind_in"' not in html  # 화면에는 버전만 들어가고 사전은 따로 받는다


def test_version_changes_with_the_catalog(monkeypatch):
    before = sql_funcs.version()
    monkeypatch.setitem(sql_funcs.FUNCS["sum"], "help", "다른 설명")
    assert sql_funcs.version() != before


def test_support_route_on_a_real_connection(real):
    c = lite_client(real)
    found = c.post("/api/funcs/support", json={"base": "orders"}).get_json()["support"]
    assert set(found) == set(sql_funcs.FUNCS)
    assert found["sum"]["status"] == "ok" and found["median"]["status"] == "local" and "PC" in found["median"]["note"]
    assert found["corr"]["status"] == "substitute" and found["corr"]["note"] and found["string_agg"]["status"] in ("local", "ok", "substitute")
    assert found["upper"]["status"] == "ok" and found["sql_expr"]["status"] == "ok"
    assert c.post("/api/funcs/support", json={"base": "nope"}).status_code == 400


def test_support_route_needs_a_connection(client):
    assert client.post("/api/funcs/support", json={"base": "orders"}).status_code == 400


# ---------- 연결이 못 하는 집계는 대체 식이나 이 PC 사본으로 ----------

def test_unsupported_aggregates_run_on_the_pc_copy(real):
    from core import held
    held.store["items"].clear()
    c = lite_client(real)
    pivot = c.post("/api/pivot", json={"base": "orders", "pivot": {"rows": ["region"], "how": "median", "value": "amt"}}).get_json()
    assert not pivot.get("error"), pivot
    for region in ("서울", "부산"):
        values = sorted(o[3] for o in ORDERS if o[2] == region)
        mid = len(values) // 2
        want = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
        assert {r["region"]: r["전체 중앙값"] for r in pivot["rows"]["rows"]}[region] == pytest.approx(want)
    chart = c.post("/api/chart", json={"base": "orders", "x": "region", "series": [{"y": "amt", "agg": "quantile", "opts": {"p": 0.5}}, {"y": "qty", "agg": "mode"}]}).get_json()
    assert not chart.get("error"), chart
    assert chart["labels"] == ["부산", "서울"]
    for label, got in zip(chart["labels"], chart["datasets"][0]["values"]):
        values = sorted(o[3] for o in ORDERS if o[2] == label)
        assert got == pytest.approx((values[len(values) // 2 - 1] + values[len(values) // 2]) / 2)
    from core import held
    assert held.store["items"]  # 이 PC 사본에서 구했다


def test_substitute_is_used_when_the_original_does_not_compile(real):
    """상관계수는 SQLite가 못 해 평균·개수로 구하는 대체 식을 쓴다(PC 사본으로 가지 않는다). 값은 numpy로 따로 구한 것과 같다."""
    import numpy as np
    from core import held
    held.store["items"].clear()
    c = lite_client(real)
    got = c.post("/api/pivot", json={"base": "orders", "pivot": {"rows": ["region"], "how": "corr", "value": "amt", "opts": {"y": "qty"}}}).get_json()
    assert not got.get("error"), got
    for region in ("서울", "부산"):
        mine = [o for o in ORDERS if o[2] == region]
        want = np.corrcoef([o[3] for o in mine], [o[4] for o in mine])[0, 1]
        assert {r["region"]: r["전체 상관계수"] for r in got["rows"]["rows"]}[region] == pytest.approx(want)
    from core import held
    assert not held.store["items"]  # 대체 식을 연결에서 바로 구했다(PC 사본으로 가지 않았다)


def test_calc_pivot_route_still_rejects_bad_options(real):
    c = lite_client(real)
    assert "집계는" in c.post("/api/pivot", json={"base": "orders", "pivot": {"how": "nope"}}).get_json()["error"]
    assert "0~1" in c.post("/api/pivot", json={"base": "orders", "pivot": {"how": "quantile", "value": "amt", "opts": {"p": 3}}}).get_json()["error"]


# ---------- 화면에 집계 목록을 직접 적어 두지 않았다 ----------

AGG_WORDS = "count|sum|mean|min|max|nunique|median"
SCREEN_FILES = [p for p in sorted(STATIC.rglob('*')) if p.suffix in (".js", ".html") and "chart.umd" not in p.name]
ALLOWED = {  # (파일, 그 줄에 든 글) → 집계 목록이 아니라 다른 뜻인 곳
    ("dash.js", "{ sum: null, count: 0, min: null, max: null }"): "일 값을 다시 묶는 계산(MERGE의 구현)",
    ("dash.js", "mean: g => g.count"): "일 값을 다시 묶는 계산(MERGE의 구현)",
    ("dash.js", "sum: pick(s.sum)"): "일 값을 다시 묶는 계산(MERGE의 구현)",
    ("dash_cards.js", '["sum", "count"].include'): "합계 줄은 더해도 되는 열(합계·개수)에만",
    ("prep.html", '["mean", "median"]'): "빈 값 채우기 방법(집계 목록이 아님)",
    ("dash_rules.js", '"sum" : "count"'): "빈칸 줄의 기본 집계",
}
FORBIDDEN = [
    (re.compile(rf'<option value="(?:{AGG_WORDS})"'), "집계를 고르는 <option>을 직접 적음"),
    (re.compile(rf'["\'](?:{AGG_WORDS})["\']\s*,\s*["\'](?:{AGG_WORDS})["\']'), "집계 이름을 나란히 적은 목록"),
    (re.compile(rf'\b(?:{AGG_WORDS})\s*:\s*["\'](?:개수|합계|평균|최소|최대|고유 개수|중앙값)["\']'), "집계 이름 → 한글 이름 표"),
    (re.compile(r'\b(?:count|sum|mean)\s*:\s*[^,}]*,\s*(?:sum|mean|min|max)\s*:'), "집계마다 하는 일을 적은 표"),
]


def allowed(name, line):
    return any(name == f and text in line for f, text in ALLOWED)


def test_no_hardcoded_aggregate_lists_in_screens():
    found = []
    for path in SCREEN_FILES:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, what in FORBIDDEN:
                if pattern.search(line) and not allowed(path.name, line):
                    found.append(f"{path.name}:{n} {what}: {line.strip()[:100]}")
    assert not found, "\n".join(found)


def test_allowed_exceptions_still_exist():
    """허용 목록이 낡아 아무것도 못 잡는 일이 없도록: 적어 둔 줄이 지금 파일에 있다."""
    for (name, text), why in ALLOWED.items():
        assert text in next(STATIC.rglob(name)).read_text(encoding="utf-8"), (name, text, why)


def test_screens_pick_from_the_catalog():
    pages = {name: next(STATIC.rglob(name)).read_text(encoding="utf-8") for name in ("index.html", "prep.html", "dashboard.html")}
    for name, text in pages.items():
        assert '<script src="/static/core/funcs.js"></script>' in text and text.index("funcs.js") > text.index("common.js"), name
    assert "Funcs.picker" in pages["index.html"] and "pickers.chart" in pages["index.html"] and "pickers.pivot" in pages["index.html"] and "pickers.calc" in pages["index.html"]
    assert "Funcs.picker" in pages["prep.html"] and "a-gagg" in pages["prep.html"] and "pickers[key]" in pages["prep.html"] and "pickers.keep" in pages["prep.html"] and "Funcs.panel" in pages["prep.html"]
    assert "Funcs.picker" in (STATIC / "dashboard" / "dash_menu.js").read_text(encoding="utf-8")
    assert "Object.entries(SERVER.calc.hows)" not in pages["index.html"] and "SERVER.funcs" not in pages["index.html"]
    assert "SERVER.dash.aggs" in (STATIC / "dashboard" / "dash_rules.js").read_text(encoding="utf-8")
    dash = (STATIC / "core" / "dash.js").read_text(encoding="utf-8")
    assert "SERVER.merge" in dash and '"nunique"' not in dash and "SERVER.agg_names" in dash
    for name in ("dash_cards.js", "dash_menu.js"):
        assert '"nunique"' not in next(STATIC.rglob(name)).read_text(encoding="utf-8"), name


def test_every_screen_list_comes_from_the_catalog():
    """서버가 화면에 주는 목록(상수·라우트)이 모두 함수 사전에서 나온다."""
    from core import calc
    from dashboard import dash_calc
    from core import metric_file
    from core import result
    aggs = set(sql_funcs.aggregate_ids())
    assert set(calc.HOWS) == aggs
    assert aggs <= set(result.chart_aggs()) and aggs <= set(metric_file.AGGS) and aggs <= set(dash_calc.AGGS)
    assert set(calc.NEW_WINDOWS) == {i for i, f in sql_funcs.FUNCS.items() if f["window"]}
    assert set(dash_calc.MERGE) == set(sql_funcs.MERGE) and set(sql_funcs.agg_names()) >= aggs | set(sql_funcs.ALIASES)


# ---------- funcs.js를 node로 ----------

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

SCRIPT = r"""
const assert = require("assert");
// 시험용 가짜 DOM: el()이 만드는 요소가 하는 일(자식, 값, 클래스, 이벤트)만 흉내 낸다.
class Fake {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this._value = ""; this.classes = new Set(); this.classList = { toggle: (c, on) => on ? this.classes.add(c) : this.classes.delete(c) }; }
  append(...kids) { this.children.push(...kids.map(k => typeof k === "string" ? { text: k } : k)); }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  options() { return this.children.flatMap(c => c.tag === "optgroup" ? c.children : [c]).filter(c => c.tag === "option"); }
  get value() { if (this.tag !== "select") return this._value; const o = this.options(); return o.some(x => x.value === this._value) ? this._value : o[0]?.value ?? ""; }
  set value(v) { this._value = this.tag === "select" ? v : String(v); }
  set selectedIndex(i) { this._value = this.options()[i]?.value ?? ""; }
  get text() { return this.textContent ?? this.children.map(c => c.text ?? "").join(""); }
}
globalThis.document = { createElement: tag => new Fake(tag) };
globalThis.el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
const catalog = JSON.parse(%(catalog)s);
globalThis.SERVER = { funcs_ver: "v", agg_names: JSON.parse(%(names)s) };
let supportAnswer = {};
globalThis.api = async (url, body) => url.startsWith("/api/funcs/support") ? { support: supportAnswer } : catalog;
%(code)s
const cols = [["amt", "실수"], ["qty", "정수"], ["name", "문자"], ["day", "날짜"]];
(async () => {
  await Funcs.load();
  const aggs = catalog.screens.chart.filter(i => Funcs.get(i).aggregate);
  assert.strictEqual(Funcs.cat.funcs.length, catalog.funcs.length);
  // 집계 고르는 칸: 사전의 집계 전부, 대표 값 고르기는 따로 묶임
  const p = Funcs.picker({ ids: aggs, columns: () => cols });
  const values = p.select.options().map(o => o.value);
  assert.deepStrictEqual([...values].sort(), aggs.filter(i => !(i in catalog.aliases)).sort());
  for (const id of ["median", "p90", "mode", "string_agg", "quantile", "unique_or_null", "count_if"]) assert.ok(values.includes(id), id);
  const groups = p.select.children.map(g => g.label);
  assert.ok(groups.includes("겹치는 값 중 하나 고르기") && groups.includes("집계"));
  assert.strictEqual(p.select.children.find(g => g.label === "겹치는 값 중 하나 고르기").children.find(o => o.value === "unique_or_null").textContent, "여러 개면 알리기(그 칸은 빈 값)");
  // 인자 칸: 분위수 P는 숫자 칸, 이어 붙이기는 구분자·정렬 기준(칸+방향)·중복
  p.set({ agg: "quantile", opts: { p: 0.9 } });
  assert.deepStrictEqual(p.get(), { agg: "quantile", opts: { p: 0.9 } });
  assert.strictEqual(p.yMode(), "required");
  p.set({ agg: "string_agg", opts: { sep: "|", order: ["amt desc"], distinct: "yes" } });
  assert.deepStrictEqual(p.get(), { agg: "string_agg", opts: { sep: "|", order: ["amt desc"], distinct: "yes" } });
  p.set({ agg: "string_agg" });
  assert.deepStrictEqual(p.get(), { agg: "string_agg" });  // 비우거나 기본값이면 보내지 않는다
  p.set({ agg: "nunique" });  // 예전 이름은 지금 id로 읽힌다
  assert.strictEqual(p.get().agg, "count_distinct");
  p.set({ agg: "count" }); assert.strictEqual(p.yMode(), "optional");
  p.set({ agg: "count_if", opts: { cond: "amt > 3" } }); assert.strictEqual(p.yMode(), "none"); assert.deepStrictEqual(p.get(), { agg: "count_if", opts: { cond: "amt > 3" } });
  p.set({ agg: "corr", opts: { y: "qty" } }); assert.deepStrictEqual(p.get(), { agg: "corr", opts: { y: "qty" } });
  // 값 컬럼은 그 집계의 첫 컬럼 칸이 받는 종류만
  p.set({ agg: "sum" }); assert.deepStrictEqual(p.xColumns(cols).map(c => c[0]), ["amt", "qty"]);
  p.set({ agg: "mode" }); assert.deepStrictEqual(p.xColumns(cols).map(c => c[0]), ["amt", "qty", "name", "day"]);
  p.set({ agg: "count_if" }); assert.deepStrictEqual(p.xColumns(cols), []);
  // 이 연결이 못 하는 집계: 안내를 붙이고, 못 쓰는 것은 막는다
  supportAnswer = { median: { status: "local", note: "이 연결이 못 하는 계산이라 이 PC 사본에서 계산해요." }, corr: { status: "substitute", note: "공분산 ÷ (표준편차 × 표준편차)로 구해요." },
    mode: { status: "unavailable", note: "맞는 종류의 컬럼이 없어요" } };
  await Funcs.loadSupport("orders");
  p.refresh();
  const byValue = Object.fromEntries(p.select.options().map(o => [o.value, o]));
  assert.strictEqual(byValue.median.textContent, "중앙값 · 이 PC에서 계산"); assert.strictEqual(byValue.corr.textContent, "상관계수 · 대체 식");
  assert.strictEqual(byValue.mode.disabled, true); assert.strictEqual(byValue.sum.disabled, false);
  p.set({ agg: "corr" }); assert.strictEqual(p.el.children[2].textContent, "공분산 ÷ (표준편차 × 표준편차)로 구해요.");
  // 계산 줄의 낱말: 컬럼, 칸 차례의 인자(값이 없는 뒤쪽 칸은 뺌)
  assert.deepStrictEqual(Funcs.words(Funcs.get("lag"), "amt", { n: 2, order: ["day asc"] }), ["amt", "2"]);
  assert.deepStrictEqual(Funcs.words(Funcs.get("ntile"), "", { n: 4 }), ["4"]);
  assert.deepStrictEqual(Funcs.words(Funcs.get("moving_mean"), "amt", { n: 3, unit: "days" }), ["amt", "3", "days"]);
  assert.deepStrictEqual(Funcs.words(Funcs.get("lag"), "amt", {}), ["amt"]);
  // 찾기 패널: 설명·예시로 찾고, 맞는 컬럼 종류가 없으면 흐리게 + 이유
  const picked = [];
  const panel = Funcs.panel({ columns: () => [["amt", "실수"]], onpick: f => picked.push(f.id) });
  const list = panel.el.children[1], items = () => list.children.filter(c => c.tag === "button");
  const item = id => items().find(b => b.children[1].textContent.startsWith(id + "("));
  assert.strictEqual(items().length, catalog.funcs.length);
  assert.ok(item("upper").className.includes("dim") && item("upper").title.includes("글자 컬럼이 없어요"), item("upper").className);
  assert.ok(!item("sum").className.includes("dim") && !item("median").className.includes("dim"));
  const search = panel.el.children[0].children[0];
  search.value = "중앙값"; search.oninput();
  assert.ok(items().some(b => b.children[1].textContent.startsWith("median(")) && items().length < 10);
  item("median").onclick(); assert.deepStrictEqual(picked, ["median"]);
  search.value = "zzzz없는함수"; search.oninput(); assert.strictEqual(items().length, 0);
  console.log("ok");
})().catch(e => { console.error(e); process.exit(1); });
"""


@node
def test_funcs_js_renders_pickers_and_panel(tmp_path):
    code = (STATIC / "core" / "funcs.js").read_text(encoding="utf-8")
    catalog = json.loads(json.dumps(sql_funcs.catalog()))
    script = SCRIPT.replace("%(catalog)s", json.dumps(json.dumps(catalog, ensure_ascii=False))).replace("%(names)s", json.dumps(json.dumps(sql_funcs.agg_names(), ensure_ascii=False))).replace("%(code)s", code)
    path = tmp_path / "funcs_test.js"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]
