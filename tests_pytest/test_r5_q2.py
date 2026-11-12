"""R5 Q2 공용 화면: 새 컬럼 상자의 줄 만들기(NewCol.lines), 고른 행·값의 조건(RowPick), 치환 상자 spec(PrepMenu), 표의 고르기 칸(common.js mountTable selectable·addCol),
달력(Calendar)·전처리 메뉴(PrepMenu.items)를 본다. 순수 함수는 node vm으로, 만든 줄은 진짜 엔진(DuckDB memtable의 calc·parse)에 걸어 보고,
화면은 헤드리스 Edge(없거나 websocket-client가 없으면 건너뜀)에서 쿼리 화면을 열어 스크립트를 불러 돌린다. 서버의 /api/prep/ops는 fetch를 가로채 가짜로 준다."""
import datetime as dt
import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import ROOT, kill_edge, spawn_edge
from test_fix_audit_js import COMMON, FAKE_DOM, GRID_HOST, STUBS

CORE = ROOT / "static" / "core"
NODE = shutil.which("node")
EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).exists()), None)
needs_node = pytest.mark.skipif(NODE is None, reason="node가 없다")


def vm_run(tmp_path, files, body):
    """static/core 파일들을 한 vm 문맥에서 차례로 돌린 뒤 body(식)의 값을 JSON으로 돌려준다."""
    runner = tmp_path / "vm.js"
    runner.write_text("""
const fs = require("fs"), vm = require("vm"), ctx = vm.createContext({});
for (const f of JSON.parse(process.argv[2])) vm.runInContext(fs.readFileSync(f, "utf8"), ctx, { filename: f });
console.log(JSON.stringify(vm.runInContext(process.argv[3], ctx)));
""", encoding="utf-8")
    done = subprocess.run([NODE, str(runner), json.dumps([str(CORE / f) for f in files]), body], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


# 방식 13개: 폼 → 기대 줄
FORMS = {
    "calc": ({"how": "calc", "op": "+", "a": "금액", "b": "수량", "name": "합"}, ["식 금액 + 수량 이름 합"]),
    "calc_value": ({"how": "calc", "op": "*", "a": "판매 금액", "b": "", "value": "1.1", "name": "세후"}, ['식 col("판매 금액") * 1.1 이름 세후']),
    "calc_ratio": ({"how": "calc", "op": "ratio", "a": "금액", "b": "수량", "name": "단가"}, ["식 safe_divide(금액, 수량) 이름 단가"]),
    "calc_concat": ({"how": "calc", "op": "concat", "a": "u", "b": "s", "sep": "-", "name": "c"}, ["식 concat(u, '-', s) 이름 c"]),
    "cond": ({"how": "cond", "kind": "case", "rules": [{"col": "x", "op": ">=", "value": "3", "then": "큰"}, {"col": "u", "op": "=", "value": "a", "then": "1", "text": True},
                                                      {"col": "x", "op": "<", "value": "", "then": "버림"}], "otherwise": "작은", "name": "분류"},
             ["식 case(x >= 3, '큰', u == 'a', '1', '작은') 이름 분류"]),  # 결과에 글자가 있으면 숫자 모양 결과도 글자로
    "cond_bucket": ({"how": "cond", "kind": "bucket", "col": "x", "bounds": "0, 2, 10", "labels": "", "name": "구간"}, ["식 bucket(x, [0, 2, 10]) 이름 구간"]),
    "cond_isin": ({"how": "cond", "kind": "isin", "col": "u", "values": "a, c", "text": True, "name": "i"}, ["식 isin(u, 'a', 'c') 이름 i"]),
    "date_lines": ({"how": "date", "kind": "diff", "a": "t", "b": "b", "style": "lines", "units": ["hour", "day"], "name": "기간"},
                   ["식 date_diff(t, b, 'day') 이름 기간_일", "식 date_diff(t, b, 'hour') 이름 기간_시간"]),
    "date_text": ({"how": "date", "kind": "diff", "a": "t", "b": "b", "style": "text", "units": ["year", "month"], "name": "기간"},
                  ["식 concat(sign(date_diff(t, b, 'month')) * floor(abs(date_diff(t, b, 'month')) / 12), '년 ', sign(date_diff(t, b, 'month')) * (abs(date_diff(t, b, 'month')) % 12), '개월') 이름 기간"]),
    "date_one": ({"how": "date", "kind": "diff", "a": "t", "b": "b", "style": "one", "unit": "hour", "name": "h"}, ["식 date_diff(t, b, 'hour') 이름 h"]),
    "date_part": ({"how": "date", "kind": "part", "col": "t", "part": "year", "name": "연"}, ["식 year(t) 이름 연"]),
    "text": ({"how": "text", "fn": "lpad", "args": [{"col": "s"}, 5, "0"], "name": "p"}, ["식 lpad(s, 5, '0') 이름 p"]),
    "window": ({"how": "window", "kind": "순위", "order": "x", "dir": "desc", "group": "u", "name": "r"}, ["순위 기준 x desc 그룹 u 이름 r"]),
    "window_ma": ({"how": "window", "kind": "이동평균", "col": "x", "size": "3행", "order": "t", "dir": "asc", "group": "", "name": "m"}, ["이동평균 x 3행 기준 t 이름 m"]),
    "window_lag": ({"how": "window", "kind": "lag", "col": "x", "n": "2", "order": "t", "group": "u", "name": "l"}, ["lag x 2 기준 t 그룹 u 이름 l"]),
    "group": ({"how": "group", "agg": "count_distinct", "col": "s", "groups": ["u", "", ""], "where": "x > 1", "name": "n"}, ["그룹집계 count_distinct(s) 그룹 u 조건 x > 1 이름 n"]),
    "group_z": ({"how": "group", "combo": "z", "col": "x", "groups": ["u"], "name": "gz"},
                ["그룹집계 mean(x) 그룹 u 이름 gz_평균", "그룹집계 std(x) 그룹 u 이름 gz_표준편차", "식 (x - gz_평균) / gz_표준편차 이름 gz"]),
    "stat": ({"how": "stat", "kind": "z", "col": "x", "groups": ["", "", ""], "name": "z"}, ["식 (x - mean(x)) / std(x) 이름 z"]),
    "stat_iqr": ({"how": "stat", "kind": "outlier", "method": "iqr", "col": "x", "k": "", "name": "o"},
                 ["식 (x < quantile(x, 0.25) - 1.5 * (quantile(x, 0.75) - quantile(x, 0.25))) or (x > quantile(x, 0.75) + 1.5 * (quantile(x, 0.75) - quantile(x, 0.25))) 이름 o"]),
    "encode": ({"how": "encode", "kind": "rare", "col": "u", "min": "5", "label": "", "name": ""}, ['전처리 rare u {"min":5,"label":"기타"}']),
    "encode_replace": ({"how": "encode", "kind": "replace", "col": "u", "spec": {"how": "exact", "pairs": [["a", "A"]]}}, ['전처리 replace u {"how":"exact","pairs":[["a","A"]]}']),
    "struct": ({"how": "struct", "kind": "unnest", "col": "태그"}, ["펼치기 태그"]),
    "struct_fn": ({"how": "struct", "kind": "array_get", "args": [{"col": "태그"}, 1], "name": "첫"}, ["식 array_get(태그, 1) 이름 첫"]),
    "behavior": ({"how": "behavior", "kind": "session", "user": "u", "time": "t", "minutes": "30", "name": "세션"},
                 ["session_id t 30 그룹 u 이름 세션"]),
    "behavior_gap": ({"how": "behavior", "kind": "gap", "user": "u", "time": "t", "unit": "minute", "name": "g"}, ["since_prev t minute 기준 t 그룹 u 이름 g"]),
    "geo": ({"how": "geo", "fn": "haversine", "args": [{"col": "위도"}, {"col": "경도"}, 37.5, 127], "name": "거리"}, ["식 haversine(위도, 경도, 37.5, 127) 이름 거리"]),
    "join": ({"how": "join", "table": "고객", "kind": "left", "keys": "고객ID=id"}, ["조인 고객 left 고객ID=id"]),
    "expr": ({"how": "expr", "text": "x * 2", "name": "x2"}, ["식 x * 2 이름 x2"]),
}
# 지금 엔진(calc.apply)에 걸 수 있는 줄(그룹집계·전처리·조인은 S1 계약 줄이라 여기서 뺀다)
ENGINE = ["calc_concat", "cond", "cond_bucket", "cond_isin", "date_lines", "date_text", "date_one", "date_part", "text", "window", "window_ma", "window_lag", "stat", "stat_iqr", "behavior", "behavior_gap", "expr"]


@needs_node
def test_newcol_lines_for_every_method(tmp_path):
    got = vm_run(tmp_path, ["newcol.js"], f"(() => {{ const F = {json.dumps({k: v[0] for k, v in FORMS.items()}, ensure_ascii=False)}, out = {{}};"
                 " for (const k in F) out[k] = NewCol.lines(F[k]);"
                 " for (const [k, f] of Object.entries({ mixed: { how: 'date', kind: 'diff', a: 't', b: 'b', style: 'text', units: ['month', 'day'] }, empty: { how: 'calc', op: '+', a: '', b: 'x' } }))"
                 "   try { NewCol.lines(f); out[k] = 'no error'; } catch (e) { out[k] = e.message; }"
                 " out.methods = NewCol.METHODS.map(m => m[0]); return out; })()")
    for k, (_, want) in FORMS.items():
        assert got[k] == want, k
    assert got["methods"] == ["calc", "row", "cond", "flag", "bin", "date", "text", "window", "group", "stat", "encode", "struct", "behavior", "geo", "join", "expr"]  # Phase 6: row·flag·bin
    assert "연·분기·월끼리" in got["mixed"] and got["empty"] == "칸을 채워 주세요: A"


@needs_node
def test_newcol_lines_run_on_duckdb(tmp_path):
    """만든 줄을 calc.apply로 DuckDB 표에 건다: 세션 번호(30분 넘게 떨어지면 새 세션), 날짜 차이 ①②, case·Z점수 값."""
    from lazy import ibis
    from core import calc
    lines = vm_run(tmp_path, ["newcol.js"], f"(() => {{ const F = {json.dumps({k: FORMS[k][0] for k in ENGINE}, ensure_ascii=False)}, out = {{}}; for (const k in F) out[k] = NewCol.lines(F[k]); return out; }})()")
    t = ibis.memtable({"u": ["a", "a", "a", "b"], "s": ["ab", "cd", "ef", "gh"], "x": [1.0, 2.0, 3.0, 4.0],
                       "t": [dt.datetime(2024, 1, 1, 0, 0), dt.datetime(2024, 1, 1, 0, 10), dt.datetime(2024, 1, 1, 2, 0), dt.datetime(2024, 1, 1, 0, 0)],
                       "b": [dt.datetime(2025, 3, 15, 5, 0)] * 4})
    con = ibis.duckdb.connect()
    out = {k: con.execute(calc.apply(v, t)).sort_values("x").reset_index(drop=True) for k, v in lines.items()}  # 행 차례는 엔진이 정하므로 x(원래 차례)로 맞춘다
    assert out["behavior"]["세션"].tolist() == [1, 1, 2, 1]
    assert out["date_text"]["기간"].tolist()[0] == "1년 2개월"
    assert out["date_lines"]["기간_일"].tolist()[0] == 439
    assert out["cond"]["분류"].tolist() == ["1", "1", "큰", "큰"] and out["cond_isin"]["i"].tolist() == [True, True, True, False]
    assert [round(v, 3) for v in out["stat"]["z"]] == [-1.162, -0.387, 0.387, 1.162] and out["text"]["p"].tolist()[0] == "000ab"


@needs_node
def test_rowpick_conditions(tmp_path):
    """키 컬럼이 있으면 in/except, 없으면 보이는 컬럼(중첩 값 제외, 앞 8개)의 튜플 in/except(쉼표·or·괄호가 든 값은 따옴표, 빈 값이 든 행은 빼고 skipped로 셈).
    고른 값의 조건 줄(빈 값 포함) 두 가지."""
    got = vm_run(tmp_path, ["rowpick.js"], """(() => {
      const g = { columns: ["id", "이름", "태그"], rows: [{ id: 1, 이름: "a, b", 태그: [1] }, { id: 2, 이름: "x", 태그: [2] }, { id: 3, 이름: null, 태그: null }, { id: 4, 이름: "c or d", 태그: null }],
                  selected: new Set([3, 0, 2]) };
      return { keep: RowPick.condition(g, "id", true), drop: RowPick.condition(g, "id", false), combo: RowPick.condition(g, null, true), notCombo: RowPick.condition(g, null, false),
               none: RowPick.condition({ ...g, selected: new Set() }, "id", true), tuples: RowPick.tuples(g),
               paren: RowPick.condition({ columns: ["a"], rows: [{ a: "x (y)" }, { a: "x (y)" }], selected: new Set([0, 1]) }, null, true),
               wide: RowPick.condition({ columns: "abcdefghij".split(""), rows: [Object.fromEntries("abcdefghij".split("").map((c, i) => [c, i]))], selected: new Set([0]) }, null, false),
               allNull: RowPick.condition({ columns: ["a"], rows: [{ a: null }], selected: new Set([0]) }, null, true),
               vKeep: RowPick.valueLines("지역", true, ["서울", null, "부산, 해운대"]), vDrop: RowPick.valueLines("지역", false, ["서울", null, "부산, 해운대"]), onlyNull: RowPick.valueLines("지역", false, [null]) };
    })()""")
    assert got["keep"] == "id in 1, 3, 4" and got["drop"] == "id except 1, 3, 4" and got["none"] == ""
    assert got["combo"] == "(id, 이름) in (1, 'a, b'), (4, 'c or d')"  # 중첩 값 컬럼(태그)은 빼고, 빈 값이 든 행(id 3)은 넣지 않는다
    assert got["notCombo"] == "(id, 이름) except (1, 'a, b'), (4, 'c or d')" and got["tuples"]["skipped"] == 1
    assert got["paren"] == "(a) in ('x (y)')" and got["wide"] == "(a, b, c, d, e, f, g, h) except (0, 1, 2, 3, 4, 5, 6, 7)" and got["allNull"] == ""
    assert got["vKeep"] == ["지역 in 서울, '부산, 해운대' or 지역 is null"]
    assert got["vDrop"] == ["지역 except 서울, '부산, 해운대'", "지역 is not null"] and got["onlyNull"] == ["지역 is not null"]
    from lazy import ibis
    from core import parse
    t = ibis.memtable({"id": [1, 2, 3, 4], "지역": ["서울", None, "부산, 해운대", "대구"]})
    con = ibis.duckdb.connect()
    assert con.execute(t.filter(parse.predicate(got["drop"], t)))["id"].tolist() == [2]
    assert sorted(con.execute(t.filter(parse.predicate(got["vKeep"][0], t)))["id"].tolist()) == [1, 2, 3]
    assert con.execute(t.filter(parse.predicate(got["vDrop"], t)))["id"].tolist() == [4]  # 목록은 and



@needs_node
def test_rowpick_tuple_on_duckdb(tmp_path):
    """키 없는 튜플 조건을 parse.predicate로 DuckDB 표에 건다(S1의 튜플 조건이 parse.py에 들어온 뒤에만, 없으면 건너뜀)."""
    from lazy import ibis
    from core import parse
    t = ibis.memtable({"id": [1, 2, 3, 4], "이름": ["a, b", "x", None, "c or d"]})
    try:
        parse.predicate("(id, 이름) in (1, x)", t)
    except ValueError:
        pytest.skip("parse.py에 튜플 조건(S1)이 아직 없다")
    got = vm_run(tmp_path, ["rowpick.js"], """(() => { const g = { columns: ["id", "이름"], rows: [{ id: 1, 이름: "a, b" }, { id: 2, 이름: "x" }, { id: 3, 이름: null }, { id: 4, 이름: "c or d" }], selected: new Set([0, 2, 3]) };
      return [RowPick.condition(g, null, true), RowPick.condition(g, null, false)]; })()""")
    con = ibis.duckdb.connect()
    assert sorted(con.execute(t.filter(parse.predicate(got[0], t)))["id"].tolist()) == [1, 4]
    assert sorted(con.execute(t.filter(parse.predicate(got[1], t)))["id"].tolist()) == [2, 3]  # except는 빈 값 행(3)을 남긴다

@needs_node
def test_replace_spec_and_paste_parser(tmp_path):
    got = vm_run(tmp_path, ["prepmenu.js"], """(() => ({
      pairs: PrepMenu.parsePairs("a\\tA\\r\\nb,B\\n\\nc"),
      exact: PrepMenu.replaceSpec({ how: "exact", pairs: [["a", "A"], ["", "x"]], picked: [], to: "", ignore_case: true, trim: false, rest: "value", restValue: "기타", toNew: "" }),
      list: PrepMenu.replaceSpec({ how: "list", pairs: [], picked: ["a", "b"], to: "AB", rest: "keep", toNew: "새" }),
      nullTo: PrepMenu.replaceSpec({ how: "null_to", pairs: [], picked: [], to: "0", rest: "keep" }),
      toNull: PrepMenu.replaceSpec({ how: "to_null", pairs: [["-", "x"]], picked: [], to: "", rest: "null" }),
      empty: PrepMenu.replaceSpec({ how: "exact", pairs: [["", ""]], picked: [], to: "", rest: "keep" }),
      kinds: ["정수", "문자", "날짜시간", "목록<정수>", "struct<a: int>", "string", "timestamp(6)", "참/거짓"].map(PrepMenu.kindOf) }))()""")
    assert got["pairs"] == [["a", "A"], ["b", "B"], ["c", ""]]
    assert got["exact"] == {"how": "exact", "pairs": [["a", "A"]], "ignore_case": True, "trim": False, "rest": "기타", "to_new": ""}
    assert got["list"]["pairs"] == [[["a", "b"], "AB"]] and got["list"]["to_new"] == "새"
    assert got["nullTo"]["pairs"] == [["", "0"]] and got["toNull"]["pairs"] == [["-", ""]] and got["toNull"]["rest"] == "null" and got["empty"] is None
    assert got["kinds"] == ["num", "str", "date", "list", "struct", "str", "date", "other"]


@needs_node
def test_grid_selectable_and_addcol(tmp_path):
    """mountTable selectable: 첫 열 체크 칸(머리글 칸 포함), 고른 행은 체크·picked, Shift는 마지막 클릭부터 범위. addCol: 머리글 끝 + 칸. 옵션이 없으면 예전과 같은 HTML."""
    body = GRID_HOST + """
const rows = [{ a: 1 }, { a: 2 }, { a: 3 }, { a: 4 }], spec = { columns: ["a"], rows, head: "<th>a</th>", row: r => cell(r.a) };
const plain = makeHost(); mountTable(plain, spec);
const host = makeHost(); let added = 0; mountTable(host, { ...spec, selectable: true, addCol: () => added++ });
const g = host._grid, events = [];
g.host.dispatchEvent = e => events.push(e.type);
gridPick(g, 1, true, false); gridPick(g, 3, true, true);
const afterRange = [...g.selected].sort();
gridPick(g, 2, false, false);
const big = gridTrs({ ...g, virtual: true, rows: Array.from({ length: 300 }, (_, i) => ({ a: i })), selected: new Set([250]) }, 249, 251, 40);
console.log(JSON.stringify({ plain: plain.html, head: host.html, body: host.tbody.html, afterRange, now: [...g.selected].sort(), events, big }));
"""
    script = tmp_path / "grid.js"
    script.write_text(FAKE_DOM + "globalThis.CustomEvent = class { constructor(type, o) { this.type = type; Object.assign(this, o); } };\n" + STUBS + COMMON + "\n" + body, encoding="utf-8")
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert got["plain"] == '<table><thead><tr><th>a</th></tr></thead><tbody><tr><td class="num">1</td></tr><tr><td class="num">2</td></tr><tr><td class="num">3</td></tr><tr><td class="num">4</td></tr></tbody></table>'
    assert '<thead><tr><th class="pick"' in got["head"] and '<th class="addcol"><button type="button"' in got["head"]
    assert got["afterRange"] == [1, 2, 3] and got["now"] == [1, 3] and got["events"] == ["rowselect"] * 3
    assert '<tr class="picked"><td class="pick"><label class="check"><input type="checkbox" data-i="1" checked' in got["body"] and '<tr><td class="pick"><label class="check"><input type="checkbox" data-i="0" aria-label' in got["body"]
    assert 'data-p="250" aria-rowindex="252" class="picked"><td class="pick"><label class="check"><input type="checkbox" data-i="250" checked' in got["big"]


FAKE_OPS = [
    {"id": "trim", "label": "앞뒤 공백 지우기", "group": "글자", "need": "str", "fields": []},
    {"id": "round", "label": "반올림", "group": "숫자", "need": "num", "fields": [{"name": "digits", "placeholder": "소수점 아래 자릿수(0 이상)"}]},
    {"id": "nulls", "label": "이 값들을 빈 값으로", "group": "글자", "need": "str", "fields": [{"name": "text", "placeholder": "예: -, N/A"}, {"name": "blank", "check": "빈 문자열도"}]},
    {"id": "type", "label": "타입 바꾸기", "group": "타입", "need": "any", "fields": [{"name": "to", "options": [["int", "정수"], ["float", "실수"]]}]},
    {"id": "expand", "label": "컬럼 여러 개로 펼치기", "group": "새 컬럼", "need": "struct", "fields": []},
    {"id": "replace", "label": "값 바꿔 쓰기", "group": "글자", "need": "any", "fields": []},
    {"id": "fill", "label": "빈 값 처리", "group": "빈 값", "need": "any", "fields": [{"name": "how", "options": [["drop", "그 행 빼기"], ["value", "정한 값으로"], ["mean", "평균으로"], ["median", "중앙값으로"], ["mode", "최빈값으로"], ["prev", "앞의 값으로"], ["next", "뒤의 값으로"]]}, {"name": "value", "placeholder": "채울 값"}]},
    {"id": "outlier", "label": "범위 밖 값 처리", "group": "숫자", "need": "num", "fields": [{"name": "how", "options": [["drop", "그 행 빼기"], ["clip", "범위로 잘라 내기"]]}, {"name": "by", "options": [["iqr", "사분위수(IQR)"], ["range", "직접 범위"], ["std", "표준편차"]]}, {"name": "low", "placeholder": "최소"}, {"name": "high", "placeholder": "최대"}, {"name": "k", "placeholder": "k"}]},
]

# 빈 시험 화면: 화면들이 함께 쓰는 조각(head·메뉴·위쪽 줄(설정 창 포함)·연결, common.js가 처음에 찾는 칸)과 공용 스크립트 두 개만. Q2 스크립트는 PAGE가 fetch를 가로챈 뒤 need로 부른다
BLANK = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><!--include core/head.html--></head>
<body><!--include core/status.html--><!--include core/connect.html-->
<script src="/static/core/common.js"></script><script src="/static/core/funcs.js"></script></body></html>"""


def blank_page(server):
    """BLANK의 조각을 static 파일로 채우고 SERVER 상수를 넣는다(server.page와 같은 규칙, CSP·nonce는 없음)."""
    html = re.sub(r"<!--include ([A-Za-z0-9._/-]+)-->", lambda m: (ROOT / "static" / m[1]).read_text(encoding="utf-8"), BLANK)
    return html.replace("/*SERVER*/{}", json.dumps(server.constants("query"), ensure_ascii=False).replace("<", "\\u003c")).encode("utf-8")

PAGE = """(async () => {
  const FAKE = %s, real = window.fetch;
  window.fetch = (url, opt) => String(url).includes("/api/prep/ops") ? Promise.resolve(new Response(JSON.stringify({ ops: FAKE }), { headers: { "Content-Type": "application/json" } })) : real(url, opt);
  await need("/static/core/calendar.js", "/static/core/prepmenu.js", "/static/core/rowpick.js", "/static/core/newcol.js");
  const out = {}, day = d => document.querySelector(`.cal [data-d="${d}"]`);
  // 달력: 처음 보이는 달은 max의 달(오른쪽), 두 날 클릭(역순) → 정렬된 기간, min 밖은 눌러도 없음
  let got = null;
  const cb = document.body.appendChild(document.createElement("button"));
  cb.textContent = "달력"; cb.style.cssText = "position:fixed;left:700px;top:300px";
  const cal = Calendar.open({ anchor: cb, min: "2024-01-05", max: "2024-02-20T10:00:00", onPick: (a, b) => { got = [a, b]; } });
  out.calAt = [cb.getBoundingClientRect().left, cal.el.parentNode.getBoundingClientRect().left, cb.getBoundingClientRect().bottom, cal.el.parentNode.getBoundingClientRect().top];
  out.months = [...document.querySelectorAll(".cal-grid")].map(t => t.getAttribute("aria-label"));
  out.focus = document.activeElement.dataset.d;
  document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
  out.moved = document.activeElement.dataset.d;
  out.offDisabled = day("2024-01-03").disabled && day("2024-02-21").disabled;
  day("2024-01-03").click();
  out.afterOff = document.querySelector(".cal-note").textContent;
  day("2024-02-10").click(); day("2024-01-20").click();
  out.reversed = got; out.closed = !document.querySelector(".cal");
  Calendar.open({ anchor: document.body, min: "2024-01-05", max: "2024-02-20", onPick: (a, b) => { got = [a, b]; } });
  day("2024-01-10").click(); day("2024-01-12").click();
  out.forward = got;
  const c3 = Calendar.open({ anchor: cb, min: "2023-01-01", max: "2024-12-31", start: "2024-03-05" });
  out.tab0Start = c3.el.querySelectorAll('[tabindex="0"]').length;
  c3.el.querySelector(".cal-nav").click();
  out.tab0Prev = [...c3.el.querySelectorAll('[tabindex="0"]')].map(b => b.dataset.d);
  c3.close();
  // 전처리 메뉴: 항목 수 = 단계 수, need가 안 맞으면 실행 없음+이유, JSON 모양 글자는 struct도 켠다, 칸 있는 단계는 상자 → onPick(id, 값)
  await PrepMenu.load();
  const picks = [], items = PrepMenu.items({ column: "메모", type: "문자", sample: () => ['{"a":1}', '{"b":2}', '[1]', "x", null], onPick: (id, v) => picks.push([id, v]) });
  out.labels = items.map(i => i[0]); out.off = items.filter(i => !i[1]).map(i => [i[0], i[2]]);
  out.plain = PrepMenu.items({ column: "메모", type: "문자", sample: () => ["a", "b", "c"], onPick() {} }).filter(i => !i[1]).length;
  items.find(i => i[0] === "글자 › 앞뒤 공백 지우기")[1]();
  items.find(i => i[0] === "글자 › 이 값들을 빈 값으로")[1]();
  const box = document.querySelector(".pm-box"), inp = box.querySelector("input:not([type])");
  inp.value = "-, N/A"; inp.dispatchEvent(new Event("input")); box.querySelector("input[type=checkbox]").click();
  [...box.querySelectorAll("button")].find(b => b.textContent === "적용").click();
  out.picks = picks; out.boxClosed = !document.querySelector(".pm-box");
  const qPicks = [], qItems = PrepMenu.items({ column: "금액", type: "실수", sample: () => [], onPick: (id, v) => qPicks.push([id, v]) });
  qItems.find(i => i[0] === "빈 값 › 빈 값 처리")[1]();
  let fb = document.querySelector(".pm-box");
  out.fillOff = [...fb.querySelectorAll("option")].filter(o => o.disabled).map(o => o.value);
  out.fillNote = [...fb.querySelectorAll(".hint")].map(h => h.textContent).join(" ");
  [...fb.querySelectorAll("button")].find(b => b.textContent === "적용").click();
  qItems.find(i => i[0] === "숫자 › 범위 밖 값 처리")[1]();
  fb = document.querySelector(".pm-box");
  out.outlierOff = [...fb.querySelectorAll("option")].filter(o => o.disabled).map(o => o.value);
  out.outlierBy = fb.querySelectorAll("select")[1].value;
  fb.parentNode.remove();
  out.qPicks = qPicks;
  // 테이블 파일 연결(flat)이면 빈 값 처리의 앞·뒤 값 채우기는 상자에 없다(전처리 정리 칸 OPS.fill과 같은 규칙)
  PrepMenu.items({ column: "금액", type: "실수", screen: "prep", flat: true, sample: () => [], onPick() {} }).find(i => i[0] === "빈 값 › 빈 값 처리")[1]();
  fb = document.querySelector(".pm-box");
  out.flatHow = [...fb.querySelectorAll("select")[0].options].map(o => o.value);
  fb.parentNode.remove();
  PrepMenu.items({ column: "금액", type: "실수", screen: "prep", sample: () => [], onPick() {} }).find(i => i[0] === "빈 값 › 빈 값 처리")[1]();
  fb = document.querySelector(".pm-box");
  out.dbHow = [...fb.querySelectorAll("select")[0].options].map(o => o.value);
  fb.parentNode.remove();
  // 값 고르기 상자: 3개 체크 → 제외/만 보기
  let applied = [];
  for (const keep of [false, true]) {
    PrepMenu.valuesBox({ column: "지역", values: async () => ["서울", "부산", "대구", "광주"], onApply: (k, v) => applied.push(RowPick.valueLines("지역", k, v)) });
    await new Promise(r => setTimeout(r, 50));
    const vb = document.querySelector(".pm-box"), boxes = [...vb.querySelectorAll(".pm-values input")];
    out.valueCount = boxes.length;
    [0, 1, 3].forEach(i => boxes[i].click());
    [...vb.querySelectorAll("button")].find(b => b.textContent === (keep ? "고른 값만 보기" : "고른 값 제외")).click();
  }
  out.applied = applied;
  PrepMenu.valuesBox({ column: "빈", values: async () => ["", "a"], onApply() {} });  // RB1: 빈 글자도 고를 수 있게 이름을 붙인다
  await new Promise(r => setTimeout(r, 50));
  const eb = document.querySelector(".pm-box");
  out.emptyLabels = [...eb.querySelectorAll(".pm-values label")].map(l => l.textContent);
  eb.parentNode.remove();
  PrepMenu.valuesBox({ column: "동", values: async () => ({ values: Array.from({ length: 150 }, (_, i) => "동" + i), more: true, partial: true }), onApply() {} });
  await new Promise(r => setTimeout(r, 50));
  const mb = document.querySelector(".pm-box");
  out.manyCount = mb.querySelectorAll(".pm-values input").length; out.manyNote = mb.querySelector(".pm-list .hint").textContent;
  mb.parentNode.remove();
  // 치환 상자: 찾을→바꿀 한 줄, 새 컬럼으로
  let spec = null;
  PrepMenu.replaceBox({ column: "등급", values: async () => [], onApply: s => { spec = s; } });
  const rb = document.querySelector(".pm-box"), [find, put] = rb.querySelectorAll(".pm-pair input");
  find.value = "a"; put.value = "A"; rb.querySelector("input[value=new]").click();
  [...rb.querySelectorAll("button")].find(b => b.textContent === "적용").click();
  out.spec = spec;
  // 표 고르기 칸: 큰 표(가상 스크롤)에서 클릭·Shift 클릭 → 선택, 막대가 보이고 조건 줄, [+]는 addCol
  const host = document.body.appendChild(document.createElement("div")), bar = document.body.appendChild(document.createElement("div"));
  host.style.cssText = "height:300px;overflow:auto";
  let addCol = 0, line = null;
  const rows = Array.from({ length: 300 }, (_, i) => ({ id: i + 1, 이름: "n" + i }));
  mountTable(host, { columns: ["id", "이름"], rows, head: "<th>id</th><th>이름</th>", row: r => cell(r.id) + cell(r.이름), selectable: true, addCol: () => addCol++ });
  const rp = RowPick.bar(bar, () => host._grid, { keyColumns: ["id"], onApply: l => { line = l; } });
  out.barHidden = rp.el.classList.contains("hidden");
  out.pickH = host.querySelector("td.pick label").getBoundingClientRect().height;  // 누를 곳 24px 이상
  host.querySelector('td.pick input[data-i="0"]').click();
  host.querySelector('td.pick input[data-i="3"]').dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey: true }));
  out.selected = [...host._grid.selected].sort((a, b) => a - b);
  out.checked = [...host.querySelectorAll("td.pick input:checked")].map(i => +i.dataset.i);
  out.barShown = !rp.el.classList.contains("hidden");
  [...rp.el.querySelectorAll("button")].find(b => b.textContent === "선택한 행 빼기").click();
  out.line = line;
  let tupleLine = null;
  const rp2 = RowPick.bar(bar, () => host._grid, { keyColumns: [], onApply: l => { tupleLine = l; } });
  [...rp2.el.querySelectorAll("button")].find(b => b.textContent === "선택한 행만 남기기").click();
  out.tupleLine = tupleLine;
  const wide = document.body.appendChild(document.createElement("div")), wbar = document.body.appendChild(document.createElement("div")), wc = "abcdefghi".split("");
  mountTable(wide, { columns: wc, rows: [Object.fromEntries(wc.map(c => [c, 1]))], head: wc.map(c => `<th>${c}</th>`).join(""), row: r => wc.map(c => cell(r[c])).join(""), selectable: true });
  const rp3 = RowPick.bar(wbar, () => wide._grid, { keyColumns: [], onApply() {} });
  wide.querySelector('td.pick input[data-i="0"]').click();
  out.wideNote = rp3.el.querySelector(".hint").textContent;
  host.querySelector("th.addcol button").click(); out.addCol = addCol;
  // 새 컬럼 상자: 방식 13개를 모두 열어 보고, 계산 방식으로 추가
  let added = null;
  await Funcs.load();
  const nc = NewCol.open({ columns: () => [["금액", "실수"], ["수량", "정수"], ["주문일", "날짜"], ["이름", "문자"]], screen: "prep", funcs: Funcs, groupable: true, onPreview: async () => ({ columns: ["a"], rows: [{ a: 1 }] }), onAdd: v => { added = v; } });
  await new Promise(r => setTimeout(r, 100));
  out.forms = NewCol.METHODS.map(([id]) => { nc.choose(id); return [id, document.querySelector(".nc-form").children.length > 0, document.querySelector(".nc-form .hint")?.textContent ?? ""]; });
  out.dim = [...document.querySelectorAll(".nc-method.dim")].map(b => b.dataset.id);
  nc.choose("calc");
  const ncBox = document.querySelector(".nc-box"), name = ncBox.querySelector(".nc-foot input");
  name.value = "세션 번호";
  ncBox.querySelector('.nc-form select[aria-label="B"]').value = "수량";
  ncBox.querySelector(".nc-form").dispatchEvent(new Event("change"));
  [...ncBox.querySelectorAll("button")].find(b => b.textContent === "추가").click();
  await new Promise(r => setTimeout(r, 20));
  out.badName = [ncBox.querySelector(".err").textContent, ncBox.isConnected, added];
  name.value = "합계";
  ncBox.querySelector('.nc-form select[aria-label="B"]').value = "수량";
  ncBox.querySelector(".nc-form").dispatchEvent(new Event("change"));
  [...ncBox.querySelectorAll("button")].find(b => b.textContent === "추가").click();
  await new Promise(r => setTimeout(r, 20));
  out.added = added; out.ncClosed = !ncBox.isConnected;
  // R3 막대의 columns(쓸 컬럼): 계산 컬럼이 빈 값이어도 원래 컬럼으로 단추가 켜지고 조건을 만든다
  const r3 = document.body.appendChild(document.createElement("div")), r3bar = document.body.appendChild(document.createElement("div"));
  mountTable(r3, { columns: ["id", "고객", "할인율"], rows: [{ id: 1, 고객: "a", 할인율: null }, { id: 2, 고객: "b", 할인율: 0.1 }], head: "<th>id</th><th>고객</th><th>할인율</th>", row: r => cell(r.id) + cell(r.고객) + cell(r.할인율), selectable: true });
  let r3line = null;
  const r3all = RowPick.bar(r3bar, () => r3._grid, { keyColumns: [], onApply() {} }), r3only = RowPick.bar(r3bar, () => r3._grid, { keyColumns: [], columns: c => c !== "할인율", onApply: l => { r3line = l; } });
  r3.querySelector('td.pick input[data-i="0"]').click();
  out.r3allOff = [...r3all.el.querySelectorAll("button")].find(b => b.textContent === "선택한 행 빼기").disabled;
  const r3drop = [...r3only.el.querySelectorAll("button")].find(b => b.textContent === "선택한 행 빼기");
  out.r3onlyOff = r3drop.disabled; r3drop.click(); out.r3line = r3line;
  const r3none = RowPick.bar(r3bar, () => r3._grid, { keyColumns: [], columns: () => false, onApply() {} });
  out.r3none = [r3none.el.querySelector(".hint").textContent, [...r3none.el.querySelectorAll("button")].find(b => b.textContent === "선택한 행 빼기").disabled];
  // B12 공용 우클릭 메뉴(common.js ctxMenu): 누른 자리, 흐린 항목 건너뛰는 화살표, Esc면 연 곳으로 초점, 키보드로 열면(좌표 0) 사각형 아래, 실행, 밖으로 초점이 나가면 닫힘
  let ran = 0;
  const ctxItemsT = [["가", () => { ran += 1; }], ["나", null, "이유"], ["다", () => { ran += 10; }]];
  cb.focus();
  ctxMenu({ target: cb, clientX: 500, clientY: 200, preventDefault() {} }, ctxItemsT, cb.getBoundingClientRect());
  const cr = ctxBox.getBoundingClientRect();
  out.ctxAt = [Math.round(cr.left), Math.round(cr.top)];
  out.ctxBtns = [...ctxBox.querySelectorAll("button")].map(b => [b.textContent, b.disabled, b.title || b.dataset.tip]);  // 초점이 간 단추의 title은 말풍선(common.js showTip)이 data-tip으로 옮긴다
  out.ctxFirst = document.activeElement.textContent;
  document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  out.ctxDown = document.activeElement.textContent;
  document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  out.ctxEsc = [!ctxBox, document.activeElement === cb];
  ctxMenu({ target: cb, clientX: 0, clientY: 0, preventDefault() {} }, ctxItemsT, cb.getBoundingClientRect());
  const kr2 = ctxBox.getBoundingClientRect(), cbr = cb.getBoundingClientRect();
  out.ctxKey = [[Math.round(cbr.left), Math.round(cbr.bottom)], [Math.round(kr2.left), Math.round(kr2.top)]];
  [...ctxBox.querySelectorAll("button")].find(b => b.textContent === "다").click();
  out.ctxRan = [ran, !ctxBox];
  ctxMenu({ target: cb, clientX: 300, clientY: 300, preventDefault() {} }, ctxItemsT);
  document.querySelector(".nc-box, input, select")?.focus?.(); document.body.appendChild(document.createElement("input")).focus();
  out.ctxBlur = !ctxBox;
  let prevented = false;
  ctxMenu({ target: cb, clientX: 1, clientY: 1, preventDefault() { prevented = true; } }, null);
  out.ctxNone = [!ctxBox, prevented];
  const nv = NewCol.open({ columns: () => [["등급", "문자"]], screen: "prep", funcs: Funcs, groupable: true, values: async c => ({ values: ["A-" + c, "B-" + c], more: false }), onPreview: async () => null, onAdd() {} });
  await new Promise(r => setTimeout(r, 50));
  nv.choose("encode");
  const nvBox = document.querySelector(".nc-box"), nvKind = nvBox.querySelector('.nc-form select[aria-label="방식"]');
  nvKind.value = "replace"; nvBox.querySelector(".nc-form").dispatchEvent(new Event("change"));
  [...nvBox.querySelectorAll("button")].find(b => b.textContent === "바꿀 값 정하기…").click();
  await new Promise(r => setTimeout(r, 50));
  const nvRb = [...document.querySelectorAll(".pm-box")].at(-1);
  out.r4values = [...nvRb.querySelectorAll(".pm-values label")].map(l => l.textContent);
  nvRb.parentNode.remove(); nv.close();
  const nq = NewCol.open({ columns: () => [["금액", "실수"], ["이름", "문자"]], screen: "query", funcs: Funcs, groupable: true, onPreview: async () => null, onAdd() {} });
  await new Promise(r => setTimeout(r, 50));
  nq.choose("encode");
  const nqBox = document.querySelector(".nc-box"), enc = nqBox.querySelector('.nc-form select[aria-label="방식"]');
  out.encodeOff = [...enc.options].filter(o => o.disabled).map(o => o.value); out.encodeNow = enc.value;
  nq.choose("struct");
  nqBox.querySelector('.nc-form select[aria-label="방식"]').value = "expand";
  nqBox.querySelector('.nc-form select[aria-label="컬럼"]').value = "이름";
  nqBox.querySelector(".nc-form").dispatchEvent(new Event("change"));
  [...nqBox.querySelectorAll("button")].find(b => b.textContent === "미리보기").click();
  out.expandMsg = nqBox.querySelector(".err").textContent;
  nq.choose("struct");
  nqBox.querySelector('.nc-form select[aria-label="방식"]').value = "counts";
  nqBox.querySelector(".nc-form").dispatchEvent(new Event("change"));
  nqBox.querySelector(".err").textContent = "";
  [...nqBox.querySelectorAll("button")].find(b => b.textContent === "미리보기").click();
  out.countsMsg = nqBox.querySelector(".err").textContent;
  nq.close();
  return JSON.stringify({ ...out, errs: window.__errs || [] });
})()"""


@pytest.mark.skipif(EDGE is None, reason="Edge가 없다")
def test_screens_headless(tmp_path):
    """헤드리스 Edge로 빈 화면(시험 안의 WSGI가 내주는 /q2: common.js·funcs.js만)을 열고 Q2 스크립트를 불러 달력·메뉴·상자·고르기 칸을 돌린다.
    쿼리 화면은 Q2 스크립트를 이미 불러 두어(다시 부르면 같은 이름 선언 오류) 쓰지 않는다. 다운로드는 막는다."""
    websocket = pytest.importorskip("websocket")  # websocket-client는 로컬 전용(CI에는 없음)
    from werkzeug.serving import make_server
    from core import server
    from query.search_app import app
    with app.app_context():
        blank = blank_page(server)

    def wsgi(environ, start):  # /q2만 시험 화면, 나머지(정적 파일·/api/funcs)는 진짜 앱
        if environ["PATH_INFO"] != "/q2":
            return app(environ, start)
        start("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Set-Cookie", f"key={server.KEY}; SameSite=Strict; HttpOnly")])
        return [blank]
    http = make_server("127.0.0.1", 0, wsgi, threaded=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        cdp = s.getsockname()[1]
    profile = tempfile.mkdtemp(prefix="q2edge_", dir=tmp_path)
    spawn_edge([EDGE, "--headless=new", f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", f"--user-data-dir={profile}", "--no-first-run",
                       "--disable-extensions", "--window-size=1440,900", "about:blank"])
    ws, n = None, [0]

    def call(method, **params):
        n[0] += 1
        ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == n[0]:
                return msg.get("result", msg)
    try:
        t0 = time.time()
        while ws is None and time.time() - t0 < 30:
            try:
                page = next(p for p in json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json", timeout=1)) if p["type"] == "page")
                ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
            except Exception:
                time.sleep(0.3)
        assert ws, "Edge에 연결하지 못함"
        call("Browser.setDownloadBehavior", behavior="deny")  # 이 시험은 내려받지 않는다(혹시 일어나도 막는다)
        call("Page.enable")
        call("Emulation.setFocusEmulationEnabled", enabled=True)  # 창이 앞에 없어도 focus·focusout이 일어나게(메뉴 밖으로 초점이 나가면 닫힘)
        call("Page.addScriptToEvaluateOnNewDocument", source="window.__errs=[];addEventListener('error',e=>__errs.push(e.message));addEventListener('unhandledrejection',e=>__errs.push(String(e.reason)));")
        call("Page.navigate", url=f"http://127.0.0.1:{http.server_port}/q2")
        time.sleep(1.5)
        res = call("Runtime.evaluate", expression=PAGE % json.dumps(FAKE_OPS, ensure_ascii=False), awaitPromise=True, returnByValue=True)
        assert "exceptionDetails" not in res, res
        out = json.loads(res["result"]["value"])
    finally:
        if ws:
            ws.close()
        kill_edge(profile)
        http.shutdown()
    assert out["months"] == ["2024년 1월", "2024년 2월"] and out["focus"] == "2024-02-20" and out["moved"] == "2024-02-19"
    assert out["offDisabled"] and out["afterOff"] == "시작 날을 고르세요"
    assert out["reversed"] == ["2024-01-20", "2024-02-10"] and out["closed"] and out["forward"] == ["2024-01-10", "2024-01-12"]
    assert len(out["labels"]) == len(FAKE_OPS)
    assert out["labels"][:3] == ["글자 › 앞뒤 공백 지우기", "글자 › 이 값들을 빈 값으로", "글자 › 값 바꿔 쓰기"]  # 그룹별로 모은다
    assert out["off"] == [["숫자 › 반올림", "숫자 컬럼에만 쓸 수 있어요"], ["숫자 › 범위 밖 값 처리", "숫자 컬럼에만 쓸 수 있어요"]]  # 쿼리에서도 통계 단계가 켜진다(결정 1)
    assert out["plain"] == 3  # JSON 모양이 아니면 struct 단계도 꺼진다(반올림·범위 밖 값은 숫자 컬럼만)
    assert out["picks"] == [["trim", True], ["nulls", ["-", "N/A", ""]]] and out["boxClosed"]
    assert out["valueCount"] == 5 and out["applied"] == [["지역 except 서울, 대구", "지역 is not null"], ["지역 in 서울, 대구 or 지역 is null"]]
    assert out["spec"] == {"how": "exact", "pairs": [["a", "A"]], "ignore_case": False, "trim": False, "rest": "keep", "to_new": "등급_바꿈"}
    assert out["barHidden"] and out["barShown"] and out["selected"] == [0, 1, 2, 3] and out["checked"][:4] == [0, 1, 2, 3]
    assert out["line"] == "id except 1, 2, 3, 4" and out["addCol"] == 1
    assert out["pickH"] >= 23.5
    assert out["tupleLine"] == "(id, 이름) in (1, n0), (2, n1), (3, n2), (4, n3)"
    assert all(f[1] for f in out["forms"]) and out["dim"] == ["join"]  # 전처리 화면에서 조인은 흐리게
    assert dict((f[0], f[2]) for f in out["forms"])["join"].startswith("쿼리 화면에서만")
    assert out["added"] == {"name": "합계", "lines": ["식 금액 + 수량 이름 합계"], "joins": []}
    assert abs(out["calAt"][0] - out["calAt"][1]) < 1 and 0 <= out["calAt"][3] - out["calAt"][2] < 10  # 달력은 단추 바로 아래(화면 왼쪽 끝이 아니라)
    assert out["tab0Start"] == 1 and len(out["tab0Prev"]) == 1  # 보이는 달 밖 start·달 이동 뒤에도 키보드로 들어갈 날이 하나
    assert out["manyCount"] == 151 and "150개" in out["manyNote"] and "일부" in out["manyNote"]
    assert "앞 8개 컬럼" in out["wideNote"] and "키 컬럼" not in out["wideNote"]
    assert "새 컬럼 이름" in out["badName"][0] and out["badName"][1] and out["badName"][2] is None and out["ncClosed"]
    assert out["fillOff"] == [] and "전처리 화면으로" not in out["fillNote"]  # 결정 1: 쿼리에서도 통계 옵션을 고른다
    assert out["qPicks"] == [["fill", {"how": "drop"}]] and out["outlierOff"] == [] and out["outlierBy"] == "iqr"  # 막힌 옵션이 없으니 가짜 목록의 첫 옵션
    assert out["encodeOff"] == [] and out["encodeNow"] == "label"
    assert "전처리 화면으로" not in out["expandMsg"] and out["countsMsg"] == ""  # 글자 컬럼 펼치기도 된다
    assert out["ctxAt"] == [500, 200] and out["ctxBtns"] == [["가", False, "가"], ["나", True, "이유"], ["다", False, "다"]]  # B12 공용 우클릭 메뉴
    assert out["ctxFirst"] == "가" and out["ctxDown"] == "다" and out["ctxEsc"] == [True, True]
    assert out["ctxKey"][0] == out["ctxKey"][1] and out["ctxRan"] == [10, True] and out["ctxBlur"] and out["ctxNone"] == [True, False]
    assert out["r3allOff"] and not out["r3onlyOff"] and out["r3line"] == "(id, 고객) except (1, a)"  # R3: 막대가 쓸 컬럼으로 단추를 켜고 같은 컬럼으로 조건
    assert "쓸 수 있는 컬럼이 없어요" in out["r3none"][0] and out["r3none"][1]
    assert out["r4values"] == ["A-등급", "B-등급"]  # R4: 새 컬럼 상자의 치환 상자에도 그 컬럼의 값 목록
    assert out["flatHow"] == ["drop", "value", "mean", "median", "mode"] and out["dbHow"][-2:] == ["prev", "next"]  # 파일 연결이면 앞·뒤 값 채우기를 숨긴다
    assert out["emptyLabels"] == ["(빈 값)", "(빈 글자)", "a"]  # RB1
    assert out["errs"] == []


# ---------- 화면 리뷰(2026-11-03) 2·5·6·12·17·18 ----------

@needs_node
def test_review_quotes_in_rowpick(tmp_path):
    """2: 값 가운데 작은·큰따옴표가 든 값(O'Brien, say "hi")도 튜플·키 조건이 서버에서 읽힌다(반대 따옴표로 감싼다). 18: 컬럼 8개 넘음 표시."""
    got = vm_run(tmp_path, ["rowpick.js"], """(() => { const g = { columns: ["id", "s"], rows: [{ id: 1, s: "O'Brien" }, { id: 2, s: 'say "hi"' }, { id: 3, s: "x" }], selected: new Set([0, 1]) };
      const wide = { columns: "abcdefghi".split(""), rows: [{ a: 1 }], selected: new Set([0]) };
      return { keep: RowPick.condition(g, null, true), drop: RowPick.condition(g, null, false), kKeep: RowPick.condition(g, "s", true), kDrop: RowPick.condition(g, "s", false),
               cut: RowPick.tuples(wide).cut, uncut: RowPick.tuples(g).cut }; })()""")
    assert got["keep"] == """(id, s) in (1, "O'Brien"), (2, 'say "hi"')""" and got["cut"] is True and got["uncut"] is False
    from lazy import ibis
    from core import parse
    t = ibis.memtable({"id": [1, 2, 3], "s": ["O'Brien", 'say "hi"', "x"]})
    con = ibis.duckdb.connect()
    ids = {k: sorted(con.execute(t.filter(parse.predicate(got[k], t)))["id"].tolist()) for k in ("keep", "drop", "kKeep", "kDrop")}
    assert ids == {"keep": [1, 2], "drop": [3], "kKeep": [1, 2], "kDrop": [3]}


@needs_node
def test_review_newcol_names(tmp_path):
    """5: 새 컬럼 이름에 빈칸·기호가 있으면 줄을 만들지 않고 안내. 6: 식 안의 '이름'·'기준'·'그룹' 컬럼은 col("…")로 감싸 calc가 옵션 낱말로 읽지 않게."""
    got = vm_run(tmp_path, ["newcol.js"], """(() => {
      const out = { case: NewCol.lines({ how: "cond", kind: "case", rules: [{ col: "x", op: ">=", value: "3", then: "a" }, { col: "이름", op: "=", value: "홍", then: "b", text: true }], otherwise: "c", name: "분류" }),
                    calc: NewCol.lines({ how: "calc", op: "+", a: "x", b: "기준", name: "합" }), grp: NewCol.lines({ how: "expr", text: "1", name: "그룹_1" }) };
      for (const name of ["세션 번호", "a-b", "1등"]) try { NewCol.lines({ how: "expr", text: "x", name }); out[name] = "no error"; } catch (e) { out[name] = e.message; }
      return out; })()""")
    assert got["case"] == ["""식 case(x >= 3, 'a', col("이름") == '홍', 'b', 'c') 이름 분류"""] and got["calc"] == ["""식 x + col("기준") 이름 합"""]
    assert got["grp"] == ["식 1 이름 그룹_1"] and all("새 컬럼 이름" in got[n] for n in ("세션 번호", "a-b", "1등"))
    from lazy import ibis
    from core import calc
    t = ibis.memtable({"x": [1, 2, 3], "이름": ["홍", "김", "홍"], "기준": [10, 20, 30]})
    con = ibis.duckdb.connect()
    assert con.execute(calc.apply(got["case"], t))["분류"].tolist() == ["b", "c", "a"]
    assert con.execute(calc.apply(got["calc"], t))["합"].tolist() == [11, 22, 33]


@needs_node
def test_review_prepmenu_value(tmp_path):
    """12: PrepMenu.value(id, st, kind)를 공개(전처리 화면과 같은 규칙): 글자 컬럼의 평균·중앙값 채우기 막기, 1e999 같은 무한 수 막기, type의 clean은 글자 컬럼만."""
    got = vm_run(tmp_path, ["prepmenu.js"], """({
      meanStr: PrepMenu.value("fill", { how: "mean" }, "str"), meanNum: PrepMenu.value("fill", { how: "mean" }, "num"), fillInf: PrepMenu.value("fill", { how: "value", value: "1e999" }, "num"),
      inf: PrepMenu.value("outlier", { how: "drop", by: "range", low: "1e999", high: "" }, "num"), range: PrepMenu.value("outlier", { how: "clip", by: "range", low: "0", high: "1e3" }, "num"),
      cleanNum: PrepMenu.value("type", { to: "int", clean: true, format: "" }, "num"), cleanStr: PrepMenu.value("type", { to: "float", clean: true, format: "" }, "str"),
      round: PrepMenu.value("round", { digits: "2" }, "num"), scale: PrepMenu.value("scale", { how: "minmax" }, "num") })""")
    assert got["meanStr"] is None and got["meanNum"] == {"how": "mean"} and got["fillInf"] is None and got["inf"] is None
    assert got["range"] == {"how": "clip", "by": "range", "low": 0, "high": 1000}
    assert got["cleanNum"] == {"to": "int"} and got["cleanStr"] == {"to": "float", "clean": True} and got["round"] == 2 and got["scale"] == "minmax"


@needs_node
def test_review_find_resets_shift_anchor(tmp_path):
    """17: 결과 안 찾기로 보이는 줄이 바뀌면 Shift 범위의 기준점(pickAt)을 버린다."""
    body = GRID_HOST + """
const host = makeHost(); mountTable(host, { columns: ["a"], rows: [{ a: 1 }, { a: 2 }, { a: 3 }], head: "<th>a</th>", row: r => cell(r.a), selectable: true });
const g = host._grid; g.host.dispatchEvent = () => {};
gridPick(g, 2, true, false); const before = g.pickAt; gridFind(g, "3");
console.log(JSON.stringify({ before, after: g.pickAt }));
"""
    script = tmp_path / "find.js"
    script.write_text(FAKE_DOM + "globalThis.CustomEvent = class { constructor(type, o) { this.type = type; Object.assign(this, o); } };" + chr(10) + STUBS + COMMON + chr(10) + body, encoding="utf-8")
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == {"before": 2, "after": -1}



# ---------- 옛 화면 감사 B(2026-11-03) 3·6·8 ----------

@needs_node
def test_audit_b_newcol_lines(tmp_path):
    """B3: 그룹집계 값 컬럼 이름에 괄호·쉼표가 있으면 따옴표로 감싼다(서버 GROUP_LINE이 받으면 DuckDB에서). B6: 파이썬 예약어·None·True·False 이름의 컬럼은 col("…").
    B8: 날짜 차이 한 칸 글자는 시작이 끝보다 늦어도(음수) 맞다: -14개월 → "-1년 -2개월"."""
    got = vm_run(tmp_path, ["newcol.js"], """(() => ({
      group: NewCol.lines({ how: "group", agg: "sum", col: "금액(원)", groups: ["u"], name: "s" }),
      groupQ: NewCol.lines({ how: "group", agg: "sum", col: "a,b", groups: ["u"], name: "s2" }),
      statZ: NewCol.lines({ how: "stat", kind: "z", col: "금액(원)", groups: ["u"], name: "z" }),
      keyword: NewCol.lines({ how: "calc", op: "+", a: "from", b: "None", name: "kw" }),
      kw2: NewCol.lines({ how: "cond", kind: "isnull", col: "class", name: "kc" }),
      neg: NewCol.lines({ how: "date", kind: "diff", a: "b", b: "t", style: "text", units: ["year", "month"], name: "음" }),
      pos: NewCol.lines({ how: "date", kind: "diff", a: "t", b: "b", style: "text", units: ["year", "month"], name: "양" }),
      dhm: NewCol.lines({ how: "date", kind: "diff", a: "b", b: "t", style: "text", units: ["day", "hour", "minute"], name: "dhm" }) }))()""")
    assert got["group"] == ["그룹집계 sum('금액(원)') 그룹 u 이름 s"] and got["groupQ"] == ["그룹집계 sum('a,b') 그룹 u 이름 s2"]
    assert got["statZ"][:2] == ["그룹집계 mean('금액(원)') 그룹 u 이름 z_평균", "그룹집계 std('금액(원)') 그룹 u 이름 z_표준편차"]
    assert got["statZ"][2] == """식 (col("금액(원)") - z_평균) / z_표준편차 이름 z"""
    assert got["keyword"] == ["""식 col("from") + col("None") 이름 kw"""] and got["kw2"] == ["""식 isnull(col("class")) 이름 kc"""]
    from lazy import ibis
    from core import calc
    t = ibis.memtable({"u": ["a", "a"], "금액(원)": [1.0, 3.0], "from": [1, 2], "None": [10, 20], "class": [None, "x"],
                       "t": [dt.datetime(2024, 1, 1, 0, 10), dt.datetime(2024, 1, 1)], "b": [dt.datetime(2025, 3, 15, 5, 0), dt.datetime(2025, 3, 15)]})
    con = ibis.duckdb.connect()
    def run(lines):
        return con.execute(calc.apply(lines, t))
    assert run(got["keyword"])["kw"].tolist() == [11, 22] and run(got["kw2"])["kc"].tolist() == [True, False]
    assert run(got["neg"])["음"].tolist() == ["-1년 -2개월", "-1년 -2개월"] and run(got["pos"])["양"].tolist() == ["1년 2개월", "1년 2개월"]
    assert run(got["dhm"])["dhm"].tolist()[0] == "-439일 -4시간 -50분"
    try:
        out = run(got["group"])
    except ValueError as e:
        pytest.skip(f"서버 GROUP_LINE이 따옴표로 감싼 값 컬럼을 아직 못 읽는다(S1): {e}")
    assert out["s"].tolist() == [4.0, 4.0] and [round(v, 3) for v in run(got["statZ"])["z"]] == [-0.707, 0.707]



# ---------- B12 우클릭 메뉴 공용화, B5 구간 이름 ----------

def test_context_menu_is_shared():
    """B12: 쿼리·전처리 화면은 우클릭 메뉴를 직접 만들지 않고 common.js ctxMenu(e, items, 사각형)를 부른다(두 벌 금지)."""
    common = (CORE / "common.js").read_text(encoding="utf-8")
    assert "function ctxMenu(e, items, fallbackRect)" in common and "function closeCtx()" in common
    for page in (ROOT / "static" / "query" / "index.html", ROOT / "static" / "prep" / "prep.html"):
        text = page.read_text(encoding="utf-8")
        assert "ctxMenu(e, " in text and "const closeCtx" not in text and "let ctxMenu" not in text and 'setAttribute("role", "menu")' not in text, page.name


@needs_node
def test_bucket_labels_one_more_than_bounds(tmp_path):
    """B5: 구간 이름은 경계보다 하나 많게(경계 아래·사이·위). 개수가 다르면 줄을 만들지 않고 안내, 맞으면 DuckDB에서 그 이름으로 나눈다."""
    got = vm_run(tmp_path, ["newcol.js"], """(() => {
      const out = { ok: NewCol.lines({ how: "cond", kind: "bucket", col: "x", bounds: "10, 20", labels: "낮음, 중간, 높음", name: "b" }) };
      try { NewCol.lines({ how: "cond", kind: "bucket", col: "x", bounds: "10, 20", labels: "낮음, 높음", name: "b" }); out.bad = "no error"; } catch (e) { out.bad = e.message; }
      return out; })()""")
    assert got["ok"] == ["식 bucket(x, [10, 20], ['낮음', '중간', '높음']) 이름 b"] and "하나 많게" in got["bad"]
    from lazy import ibis
    from core import calc
    t = ibis.memtable({"x": [5, 15, 25]})
    assert ibis.duckdb.connect().execute(calc.apply(got["ok"], t))["b"].tolist() == ["낮음", "중간", "높음"]



# ---------- 옛 화면 리뷰 6(2026-11-03) R2·R3·R5·R9 ----------

@needs_node
def test_review6_rowpick_always_quotes_and_mixed_quotes(tmp_path):
    """R2: 쿼리 화면에서 따옴표 해석을 꺼 두어도(cellValue가 값을 그대로 줌) 프로그램이 만든 조건은 늘 감싼다(쉼표·앞뒤 공백 값).
    R9: 작은·큰따옴표가 둘 다 든 값의 행은 튜플에 넣지 않고 따로 센다. R3: condition·tuples는 거른 컬럼으로. 줄은 parse.predicate로 DuckDB에서."""
    got = vm_run(tmp_path, ["rowpick.js"], """(() => {
      globalThis.cellValue = v => v; globalThis.document = { getElementById: () => ({ checked: false }) };  // 따옴표 해석을 끈 쿼리 화면
      const g = { columns: ["id", "s"], rows: [{ id: 1, s: "Lee, J" }, { id: 2, s: " pad " }, { id: 3, s: "x" }, { id: 4, s: `it's "q"` }], selected: new Set([0, 1, 3]) };
      return { vals: RowPick.valueLines("s", true, ["Lee, J", " pad "]), keep: RowPick.condition(g, null, true), drop: RowPick.condition(g, null, false), t: RowPick.tuples(g) }; })()""")
    assert got["vals"] == ["s in 'Lee, J', ' pad '"]
    assert got["keep"] == "(id, s) in (1, 'Lee, J'), (2, ' pad ')" and got["t"]["mixed"] == 1 and got["t"]["skipped"] == 0
    from lazy import ibis
    from core import parse
    t = ibis.memtable({"id": [1, 2, 3, 4], "s": ["Lee, J", " pad ", "x", 'it\'s "q"']})
    con = ibis.duckdb.connect()
    ids = lambda line: sorted(con.execute(t.filter(parse.predicate(line, t)))["id"].tolist())
    assert ids(got["vals"][0]) == [1, 2] and ids(got["keep"]) == [1, 2] and ids(got["drop"]) == [3, 4]


@needs_node
def test_review6_uint_is_number(tmp_path):
    """R5: 부호 없는 정수(uint8~uint64)도 숫자 컬럼."""
    got = vm_run(tmp_path, ["prepmenu.js"], '["uint8", "uint64", "int32", "UInt16"].map(PrepMenu.kindOf)')
    assert got == ["num", "num", "num", "num"]



# ---------- 2차 감사(옛 화면) AB4·AB5·AB7 ----------

@needs_node
def test_audit6_rowpick_quote_export_and_blank_key(tmp_path):
    """AB4: RowPick.quote를 내보낸다(쿼리 화면 cellValue가 같은 규칙을 쓰게). AB7: 키 컬럼 조건에서 키가 빈 값인 고른 행은 빼고 한 줄로(서버는 ' and '를 나누지 않는다)."""
    got = vm_run(tmp_path, ["rowpick.js"], """(() => { const g = { columns: ["k"], rows: [{ k: "a" }, { k: "b" }, { k: null }], selected: new Set([0, 2]) };
      return { q: ["rock 'n roll", "a, b", "x"].map(RowPick.quote), drop: RowPick.condition(g, "k", false), keep: RowPick.condition(g, "k", true),
               allNull: RowPick.condition({ ...g, selected: new Set([2]) }, "k", false) }; })()""")
    assert got["q"] == ['"rock \'n roll"', "'a, b'", "x"] and got["drop"] == "k except a" and got["keep"] == "k in a" and got["allNull"] == ""
    from lazy import ibis
    from core import parse
    t = ibis.memtable({"k": ["a", "b", None, "rock 'n roll"]})
    con = ibis.duckdb.connect()
    assert [v if v == v else None for v in con.execute(t.filter(parse.predicate(got["drop"], t)))["k"].tolist()] == ["b", None, "rock 'n roll"]  # 빈 값 행은 except가 남긴다(막대가 안내)
    assert con.execute(t.filter(parse.predicate(f"k = {got['q'][0]}", t)))["k"].tolist() == ["rock 'n roll"]


@needs_node
def test_audit6_word_columns_blocked_in_word_lines(tmp_path):
    """AB5: 창 함수·행동 분석·백분위 줄은 낱말 줄이라 '고객 이름'처럼 이름·기준·그룹 낱말이 따로 든 컬럼을 서버가 잘못 읽는다(고치기 전 줄은 calc.apply에서 엉뚱한 오류).
    그런 컬럼이면 백틱으로 감싸서 된다(감싸지 않은 줄은 여전히 오류), 그룹별 집계(괄호 안)는 그대로 된다."""
    from lazy import ibis
    from core import calc
    t = ibis.memtable({"금액": [1.0, 2.0], "고객 이름": ["a", "b"], "t": [dt.datetime(2024, 1, 1), dt.datetime(2024, 1, 2)]})
    with pytest.raises(ValueError):  # 예전에 만들던 줄: "'고객' 컬럼이 없어요" 같은 엉뚱한 오류
        calc.apply(["순위 기준 금액 desc 그룹 고객 이름 이름 등수"], t)
    got = vm_run(tmp_path, ["newcol.js"], """(() => { const out = {};
      const forms = { rank: { how: "window", kind: "순위", order: "금액", dir: "desc", group: "고객 이름", name: "등수" },
        lag: { how: "window", kind: "lag", col: "금액", n: "1", order: "기준 일자", group: "", name: "l" },
        session: { how: "behavior", kind: "session", user: "고객 이름", time: "t", minutes: "30", name: "세션" },
        pct: { how: "stat", kind: "pct", col: "금액", groups: ["고객 그룹"], name: "p" } };
      for (const [k, f] of Object.entries(forms)) try { out[k] = NewCol.lines(f); } catch (e) { out[k] = e.message; }
      out.group = NewCol.lines({ how: "group", agg: "count_distinct", col: "금액", groups: ["고객 이름"], name: "n" });
      out.okRank = NewCol.lines({ how: "window", kind: "순위", order: "금액", dir: "desc", group: "", name: "r" });
      return out; })()""")
    assert got["rank"] == ["순위 기준 금액 desc 그룹 `고객 이름` 이름 등수"] and got["lag"] == ["lag 금액 1 기준 `기준 일자` 이름 l"]
    assert got["pct"] == ["percent_rank 기준 금액 그룹 `고객 그룹` 이름 p"] and got["session"] == ["session_id t 30 그룹 `고객 이름` 이름 세션"]
    assert got["group"] == ["그룹집계 count_distinct(금액) 그룹 `고객 이름` 이름 n"] and got["okRank"] == ["순위 기준 금액 desc 이름 r"]
    con = ibis.duckdb.connect()
    assert con.execute(calc.apply(got["rank"], t)).sort_values("금액")["등수"].tolist() == [1, 1]
    assert con.execute(calc.apply(got["group"], t)).sort_values("금액")["n"].tolist() == [1, 1]
    assert con.execute(calc.apply(got["okRank"], t)).sort_values("금액")["r"].tolist() == [2, 1]  # 행 차례는 엔진이 정한다



@needs_node
def test_regex_group_reference_survives_expression(tmp_path):
    r"""식 줄은 파이썬 글자 규칙으로 읽혀 '#\1'(역슬래시 하나)은 제어 문자 \x01이 된다: 글자 방식의 regex_replace는 사용자가 \1로 써도 줄에 역슬래시 두 개(\\1)가 들어가 묶음 번호가 산다."""
    got = vm_run(tmp_path, ["newcol.js"], r"""NewCol.lines({ how: "text", fn: "regex_replace", args: [{ col: "s" }, String.raw`(\d+)`, String.raw`#\1`], name: "r" })""")  # 사용자가 칸에 친 글자 그대로(역슬래시 하나)
    assert got == [r"식 regex_replace(s, '(\\d+)', '#\\1') 이름 r"]
    from lazy import ibis
    from core import calc
    t = ibis.memtable({"s": ["a12", "b3"]})
    out = ibis.duckdb.connect().execute(calc.apply(got, t))["r"].tolist()
    assert out == ["a#12", "b#3"] and not any(chr(1) in v for v in out)



# ---------- 옛 화면 2차 리뷰(review7) RB1·RB6·RB7·RB8 ----------

@needs_node
def test_review7_empty_text_value_is_quoted(tmp_path):
    """RB1: 빈 글자('')도 ''로 감싼다(감싸지 않으면 's in , a'가 되어 서버가 빈 조각을 버린다). 값 줄·튜플 줄을 parse.predicate로 DuckDB에서."""
    got = vm_run(tmp_path, ["rowpick.js"], """(() => { const g = { columns: ["id", "s"], rows: [{ id: 1, s: "" }, { id: 2, s: "a" }, { id: 3, s: "b" }, { id: 4, s: null }], selected: new Set([0, 1]) };
      return { keep: RowPick.valueLines("s", true, ["", "a"]), drop: RowPick.valueLines("s", false, ["", "a"]), only: RowPick.valueLines("s", true, [""]),
               tKeep: RowPick.condition(g, null, true), tDrop: RowPick.condition(g, null, false), q: RowPick.quote("") }; })()""")
    assert got["keep"] == ["s in '', a"] and got["drop"] == ["s except '', a"] and got["only"] == ["s in ''"] and got["q"] == "''"
    assert got["tKeep"] == "(id, s) in (1, ''), (2, a)"
    from lazy import ibis
    from core import parse
    t = ibis.memtable({"id": [1, 2, 3, 4], "s": ["", "a", "b", None]})
    con = ibis.duckdb.connect()
    ids = lambda line: sorted(con.execute(t.filter(parse.predicate(line, t)))["id"].tolist())
    assert ids(got["keep"][0]) == [1, 2] and ids(got["drop"][0]) == [3, 4] and ids(got["only"][0]) == [1]
    assert ids(got["tKeep"]) == [1, 2] and ids(got["tDrop"]) == [3, 4]


@needs_node
def test_review7_newcol_group_words_name_rule_gcol(tmp_path):
    """RB6: 그룹집계 줄의 그룹 컬럼 이름에 '조건'·'기준' 낱말이 따로 있으면 백틱으로 감싼다.
    RB7: 새 컬럼 이름 규칙은 서버 check_name과 같다(영문·한글·숫자·밑줄). RB8: gcol을 NewCol.gcol로 내보낸다."""
    from lazy import ibis
    from core import calc
    t = ibis.memtable({"조건 코드": ["a", "a", "b"], "x": [1.0, 2.0, 3.0]})
    assert sorted(ibis.duckdb.connect().to_polars(calc.apply(["그룹집계 count() 그룹 조건 코드 이름 n"], t))["n"].to_list()) == [1, 2, 2]  # 칸 낱말 바로 뒤라 이제 된다
    got = vm_run(tmp_path, ["newcol.js"], """(() => { const out = {};
      const forms = { group: { how: "group", agg: "count", col: "", groups: ["조건 코드"], name: "n" },
        z: { how: "stat", kind: "z", col: "x", groups: ["기준 월"], name: "z" },
        cohort: { how: "behavior", kind: "cohort", user: "조건 코드", time: "t", unit: "month", name: "co" },
        cafe: { how: "expr", text: "1", name: "café" }, circled: { how: "expr", text: "1", name: "합①" } };
      for (const [k, f] of Object.entries(forms)) try { out[k] = NewCol.lines(f); } catch (e) { out[k] = e.message; }
      out.ok = NewCol.lines({ how: "group", agg: "count", col: "", groups: ["코드 값"], name: "가_1" });
      out.gcol = ["금액(원)", "a,b", "x"].map(NewCol.gcol);
      return out; })()""")
    assert got["group"] == ["그룹집계 count() 그룹 `조건 코드` 이름 n"]
    assert len(got["z"]) == 3 and sum("그룹 `기준 월`" in line for line in got["z"]) == 2  # 평균·표준편차 두 그룹집계 줄(셋째는 식 줄)
    assert "그룹 `조건 코드`" in got["cohort"][0]  # 둘째 줄은 식 줄
    assert all(isinstance(got[k], str) and "새 컬럼 이름" in got[k] for k in ("cafe", "circled"))
    assert got["ok"] == ["그룹집계 count() 그룹 `코드 값` 이름 가_1"] and got["gcol"] == ["'금액(원)'", "'a,b'", "x"]
