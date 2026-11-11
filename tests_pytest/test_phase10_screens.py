"""Phase 10 화면(plans/phase-10.md): 새 컬럼 상자의 백틱 인용·행동 틀(newcol.js), 제자리 계산은 대상 컬럼만 막기(plainCol·rowCol),
행 고르기 키 컬럼(rowKeyPick), 계산 폼 인용(calcLine), 찾기 ③ 최근 값(openLookup), 기간별 표 비중 % 기본(pt-show).
순수 함수·잘라 온 index.html 구간은 node(가짜 화면)로, 만든 줄은 DuckDB memtable의 calc.apply로 돌린다."""
import datetime as dt
import json
import re
import shutil

import pytest

from conftest import ROOT
from test_fix_audit_js import index_slice, run_node

HTML = (ROOT / "static" / "query" / "index.html").read_text(encoding="utf-8")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
OPTION = "globalThis.Option = class { constructor(text, value) { this.textContent = text; this.value = value; } };\n"


@needs_node
def test_newcol_quotes_word_columns_and_new_behavior_lines(tmp_path):
    forms = {
        "rank": {"how": "window", "kind": "순위", "order": "금액", "dir": "desc", "group": "고객 이름", "name": "등수"},
        "lag": {"how": "window", "kind": "lag", "col": "금액", "n": "1", "order": "기준 일자", "group": "", "name": "l"},
        "pct": {"how": "stat", "kind": "pct", "col": "금액", "groups": ["고객 그룹"], "name": "p"},
        "group": {"how": "group", "agg": "count", "col": "", "groups": ["조건 코드"], "name": "n"},
        "same": {"how": "behavior", "kind": "same_day", "user": "u", "time": "t", "name": "k"},
        "cum": {"how": "behavior", "kind": "cum_count", "user": "u", "time": "t", "name": "c"},
        "comma": {"how": "window", "kind": "순위", "order": "a,b", "dir": "asc", "group": "", "name": "r"},
        "tick": {"how": "window", "kind": "lag", "col": "a`b", "n": "1", "order": "t", "group": "", "name": "r"},
    }
    body = f"""const F = {json.dumps(forms, ensure_ascii=False)}, out = {{}};
for (const [k, f] of Object.entries(F)) try {{ out[k] = NewCol.lines(f); }} catch (e) {{ out[k] = e.message; }}
out.bq = ["x", "고객 이름", "이름", "키"].map(NewCol.bq);
out.tokens = NewCol.tokens("반올림 0 `판매 금액`, x");
console.log(JSON.stringify(out));"""
    got = run_node(tmp_path, body, files=["newcol.js"])
    assert got["rank"] == ["순위 기준 금액 desc 그룹 `고객 이름` 이름 등수"] and got["lag"] == ["lag 금액 1 기준 `기준 일자` 이름 l"]
    assert got["pct"] == ["percent_rank 기준 금액 그룹 `고객 그룹` 이름 p"] and got["group"] == ["그룹집계 count() 그룹 `조건 코드` 이름 n"]
    assert got["same"] == ["식 truncate(t, 'day') 이름 k_날", "순위 기준 t 그룹 u, k_날 이름 k"] and got["cum"] == ["식 iif(isnull(t), 0, 1) 이름 c_1", "누적합 c_1 기준 t 그룹 u 이름 c"]
    assert got["comma"] == "'a,b' 컬럼 이름에는 쉼표가 있어 계산 줄로는 못 써요. 전처리 화면에서 이름을 바꾸거나 식 직접 쓰기의 col(\"…\")로 쓰세요."
    assert isinstance(got["tick"], str) and got["tick"].startswith("'a`b' 컬럼 이름에는 백틱(`)이 있어 계산 줄로는 못 써요.")
    assert got["bq"] == ["x", "`고객 이름`", "`이름`", "`키`"]
    assert got["tokens"] == [{"word": "반올림", "quoted": False}, {"word": "0", "quoted": False}, {"word": "판매 금액", "quoted": True}, {"word": ",", "quoted": False}, {"word": "x", "quoted": False}]
    from lazy import ibis
    from core import calc
    con = ibis.duckdb.connect()
    t = ibis.memtable({"금액": [1.0, 2.0, 3.0], "고객 이름": ["a", "a", "b"], "기준 일자": [dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 3)],
                       "고객 그룹": ["g", "g", "h"], "조건 코드": ["x", "x", "y"]})
    out = con.to_polars(calc.apply([*got["rank"], *got["lag"], *got["pct"], *got["group"]], t)).sort("금액")
    assert out["등수"].to_list() == [2, 1, 1] and out["l"].to_list() == [None, 1.0, 2.0] and out["n"].to_list() == [2, 2, 1]
    h = lambda d, hr: dt.datetime(2024, 1, d, hr)
    b = ibis.memtable({"u": ["a", "a", "a", "b", "a"], "t": [h(1, 9), h(1, 9), h(2, 8), h(1, 9), h(1, 10)], "i": [0, 1, 2, 3, 4]})
    out = con.to_polars(calc.apply([*got["same"], *got["cum"]], b)).sort("i")
    assert out["k"].to_list() == [1, 1, 1, 1, 3] and out["c"].to_list() == [2, 2, 4, 1, 3]  # 같은 시각은 같은 번호·같은 건수


@needs_node
def test_inplace_blocks_only_target_columns(tmp_path):
    code = index_slice("const INPLACE", "const valuesBody")
    body = r"""
const S = { cols: [["금액", "실수"], ["수량", "정수"], ["이름", "문자"], ["주문일", "날짜"], ["판매 금액", "실수"], ["태그", "목록<문자>"]],
  origins: { 금액: "t", 수량: "t", 이름: "t", 주문일: "t", "판매 금액": "t", 태그: "t" }, calcs: [] };
const names = S.cols.map(c => c[0]), out = {};
for (const [k, lines] of Object.entries({ round: ["반올림 0 숫자"], trim: ["공백지우기 글자"], times: ["곱하기 1.1 `판매 금액`, 수량"], fill: ["빈값채우기 2024-01-01 날짜"],
  glob: ["반올림 0 금*"], textTimes: ["곱하기 2 이름"], unnest: ["펼치기 태그"], longer: ["세로로 금액, 수량"], prep: [{ line: "전처리 trim 이름 true" }] })) {
  S.calcs = lines;
  out[k] = { plain: names.filter(plainCol), row: names.filter(rowCol) };
}
console.log(JSON.stringify(out));"""
    got = run_node(tmp_path, body, files=["newcol.js"], parts=[code])
    every = ["금액", "수량", "이름", "주문일", "판매 금액", "태그"]
    but = lambda *gone: [n for n in every if n not in gone]
    assert got["round"]["plain"] == but("금액", "수량", "판매 금액") and got["round"]["row"] == but("금액", "수량", "판매 금액")
    assert got["trim"]["plain"] == but("이름") and got["times"]["plain"] == but("판매 금액", "수량") and got["fill"]["plain"] == but("주문일")
    assert got["glob"]["plain"] == but("금액") and got["textTimes"]["plain"] == every and got["prep"]["plain"] == but("이름")
    assert got["unnest"]["plain"] == but("태그") and got["unnest"]["row"] == [] and got["longer"]["plain"] == but("금액", "수량") and got["longer"]["row"] == []


@needs_node
def test_row_key_pick_builds_key_condition(tmp_path):
    prelude = OPTION + r"""
const S = { cols: [["id", "정수"], ["name", "문자"], ["두배", "실수"], ["tags", "목록<문자>"]], origins: { id: "t", name: "t", tags: "t" }, calcs: [] };
const els = { rows: { _grid: null }, "err-search": {} };
globalThis.$ = id => els[id] ??= mk("x", {});
const added = [], runSearch = async () => true, addItem = async (k, l) => { added.push(l); return true; }, gridDraw = () => {};
"""
    code = index_slice("const INPLACE", "const valuesBody") + index_slice("const rowKeyPick", '$("rows-tools").append')
    body = r"""
$("rows")._grid = { columns: ["id", "name", "두배", "tags"], rows: [{ id: 1, name: "a", 두배: 2, tags: ["x"] }, { id: 2, name: "b", 두배: 4, tags: ["y"] }], selected: new Set([0, 1]) };
fillRowKey();
const options = rowKeyPick.children.map(o => o.value), inBar = rowPick.el.children.includes(rowKeyPick);
rowKeyPick.value = "id"; rowPick.el.children[1].onclick(); const keyed = added.at(-1);
rowKeyPick.value = ""; rowPick.el.children[1].onclick(); const tuple = added.at(-1);
console.log(JSON.stringify({ options, inBar, keyed, tuple }));"""
    got = run_node(tmp_path, body, files=["rowpick.js", "newcol.js"], parts=[prelude, code])
    assert got == {"options": ["", "id", "name"], "inBar": True, "keyed": "id except 1, 2", "tuple": "(id, name) except (1, a), (2, b)"}


@needs_node
def test_calc_form_quotes_columns(tmp_path):
    prelude = r"""
const vals = { "cf-kind": "window", "cf-name": "r", "cf-x": "판매 금액" };
globalThis.$ = id => ({ value: vals[id] ?? "" });
const pickers = { calc: { get: () => ({ agg: "lag", opts: { order: ["기준 일자 desc"], group: ["고객 그룹", "g"], n: 1 } }), yMode: () => "required" } };
const Funcs = { get: () => ({}), words: (f, x, opts) => [x, String(opts.n)] };
"""
    code = index_slice("function calcLine()", '$("cf-kind").onchange')
    body = 'const line = calcLine(); vals["cf-x"] = "a,b"; let why = ""; try { calcLine(); } catch (e) { why = e.message; } console.log(JSON.stringify({ line, why }));'
    got = run_node(tmp_path, body, files=["newcol.js"], parts=[prelude, code])
    assert got["line"] == "lag `판매 금액` 1 기준 `기준 일자` desc 그룹 `고객 그룹`, g 이름 r"
    assert got["why"].startswith("'a,b' 컬럼 이름에는 쉼표가 있어 계산 줄로는 못 써요.")  # 계산 폼은 이 글을 err-cf에 보인다(지금 try)


def test_lookup_recent_value_and_prefixed_names():
    a = HTML.index("function openLookup(")
    body = HTML[a:HTML.index("\n}\n", a)]
    assert '["max", "최댓값"], ["last", "최근 값"]' in body  # 최댓값도 남긴다(사용자 확정 4)
    assert "테이블 ${NewCol.bq(t)} 키 ${NewCol.bq(c)}=${NewCol.bq(k)}" in body and "기준 ${NewCol.bq(ord.value)}" in body
    assert "_right" not in body and "최근 값은 기준 시각 컬럼을 고르세요." in body
    assert "catch (e) { msg.textContent = e.message; return; }" in body  # 쉼표·백틱 이름 안내가 상자에 보인다


def test_period_table_share_is_default():
    select = re.search(r'<select id="pt-show"[^>]*>(.*?)</select>', HTML, re.S)[1]
    options = re.findall(r'<option value="(\w+)"( selected)?>', select)
    chosen = next((v for v, s in options if s), options[0][0])
    assert chosen == "share"
