"""F16 조인 키 자동·기억, F02/F06 TSV·고정폭·여러 테이블 Excel 단추, F30 쿼리 화면의 가장 많은 기간 찾기. node로 index.html의 해당 구간만 돌린다."""
import json
import shutil
from urllib.parse import unquote

import pytest

from test_fix_audit_js import STATIC, index_slice, run_node

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
HTML = (STATIC / "query" / "index.html").read_text(encoding="utf-8")

JOIN_PRELUDE = r"""
const S = { schema: "", base: "orders", cols: [["cid"], ["day"]], joins: [], origins: {} };
const els = {}; globalThis.$ = id => els[id] ??= { textContent: "" };
const calls = [], saves = [];
const api = async (url, body) => {
  calls.push(url);
  if (url === "/api/settings") saves.push(body);
  if (url === "/api/join_keys") return { decision: "ask", candidates: [{ keys: [["cid", "id"]] }, { keys: [["day", "day"]] }] };
  return { columns: [["id"], ["day"]] };
};
const saved = n => n === "join_keys" ? JSON.stringify({ "orders\u0000old": [["cid", "oid"]] }) : undefined;  // head.html의 saved는 퍼센트 인코딩을 푼 글자를 돌려준다
const refreshColumns = async () => {}, showSummaries = () => {}, renderJoins = () => {}, state = () => ({});
"""


def join_run(tmp_path, body):
    return run_node(tmp_path, body, parts=[JOIN_PRELUDE, index_slice("const JOIN_HOWS", "async function renderJoins")])


@node
def test_join_keys_auto_then_remembered(tmp_path):
    body = r"""
(async () => {
  const out = { first: JSON.parse(JSON.stringify(joinMemory)) };
  const j = { table: "customers", how: "left", pairs: [["", ""]] };
  joinsUI.push(j);
  await autoKeys(j);
  out.auto = JSON.parse(JSON.stringify(j.pairs)); out.joins = [...S.joins]; out.asked = calls.filter(c => c === "/api/join_keys").length; out.save1 = saves.at(-1);
  j.pairs[0] = ["day", "day"];  // 사용자가 키를 바꾼다
  await applyJoins();
  out.save2 = saves.at(-1);
  const n = saves.length;
  await applyJoins();  // 바뀐 것이 없으면 다시 저장하지 않는다
  out.same = saves.length === n;
  calls.length = 0;
  const k = { table: "customers", how: "inner", pairs: [["", ""]] };
  joinsUI.push(k);
  await autoKeys(k);
  out.again = k.pairs; out.asked2 = calls.filter(c => c === "/api/join_keys").length;
  S.cols = [["cid"]];  // 기억한 키(day)가 이제 없으면 후보를 다시 묻는다
  const m = { table: "customers", how: "left", pairs: [["", ""]] };
  joinsUI.push(m);
  await autoKeys(m);
  out.gone = m.pairs; out.asked3 = calls.filter(c => c === "/api/join_keys").length;
  const busy = { table: "customers", how: "left", pairs: [["cid", "id"]] };  // 이미 고른 키는 덮지 않는다
  await autoKeys(busy);
  out.kept = busy.pairs;
  console.log(JSON.stringify(out));
})();
"""
    got = join_run(tmp_path, body)
    assert got["first"] == {"orders\u0000old": [["cid", "oid"]]}  # 저장된 설정(join_keys)을 읽는다
    assert got["auto"] == [["cid", "id", False]] and got["joins"] == ["customers left cid=id"] and got["asked"] == 1
    assert json.loads(unquote(got["save1"]["join_keys"]))["orders\u0000customers"] == [["cid", "id"]]
    assert json.loads(unquote(got["save2"]["join_keys"]))["orders\u0000customers"] == [["day", "day"]]
    assert got["same"] is True
    assert got["again"] == [["day", "day", False]] and got["asked2"] == 0  # 기억한 키를 먼저, 서버에 묻지 않는다
    assert got["gone"] == [["cid", "id", False]] and got["asked3"] == 1
    assert got["kept"] == [["cid", "id"]]


@node
def test_join_memory_fits_cookie(tmp_path):
    body = r"""
(async () => {
  for (let i = 0; i < 200; i++) joinMemory["orders\u0000t" + i] = [["긴컬럼이름" + i, "다른긴컬럼" + i]];
  joinsUI.push({ table: "customers", how: "left", pairs: [["cid", "id"]] });
  await applyJoins();
  const text = saves.at(-1).join_keys;
  console.log(JSON.stringify({ len: text.length, keys: Object.keys(JSON.parse(decodeURIComponent(text))) }));
})();
"""
    got = join_run(tmp_path, body)
    assert got["len"] <= 3000
    assert "orders\u0000customers" in got["keys"] and "orders\u0000old" not in got["keys"]  # 먼저 기억한 쌍부터 버린다


def test_join_table_pick_fills_keys():
    assert "applyJoins().then(() => autoKeys(j))" in HTML


def test_settings_accepts_join_keys():
    from core import settings
    assert settings.valid("join_keys", "%7B%7D") and settings.valid("join_keys", "")
    assert not settings.valid("join_keys", "x" * 3001) and not settings.valid("join_keys", {"a": 1})


# ---------- 내보내기 ----------

def test_export_buttons_tsv_fixed():
    for fmt, label in (("tsv", "TSV"), ("fixed", "고정폭")):
        assert f'data-export="{fmt}"' in HTML and f">{label}</button>" in HTML
    assert '"result." + (format === "fixed" ? "txt" : format)' in HTML
    assert 'id="export-tables"' in HTML and 'aria-label="테이블마다 시트 하나로 Excel에 내보내기"' in HTML


@node
def test_export_tables_list(tmp_path):
    prelude = r"""
const S = { schema: "main", base: "orders", union: { tables: ["orders2"], how: "left" } };
let joinsUI = [{ table: "customers" }, { table: "" }, { table: "orders2" }];
"""
    code = index_slice("const exportTables", '$("export-tables").onclick')
    got = run_node(tmp_path, "console.log(JSON.stringify(exportTables()));", parts=[prelude, code])
    assert got == [{"schema": "main", "name": n} for n in ("orders", "orders2", "customers")]
    assert 'api("/api/export/tables", { tables: exportTables(), format: "xlsx" })' in HTML


# ---------- 가장 많은 기간 찾기 ----------

@node
def test_busiest_on_query_screen(tmp_path):
    prelude = r"""
const S = { schema: "", base: "orders", cols: [["day", "날짜"], ["at", "날짜시간"], ["n", "정수"], ["cday", "날짜"]], origins: { day: "orders", at: "orders", cday: "customers" } };
const els = { "pk-col": { value: "at" }, "pk-unit": { value: "week" } }; globalThis.$ = id => els[id] ??= mk("x", {});
const sent = [], added = [];
const api = async (url, body) => { sent.push([url, body]); return { periods: [{ start: "2026-01-05", end: "2026-01-11", count: 1200 }], more: true }; };
const timed = (kind, fn) => fn();
const addItem = async (key, line, err) => { added.push([key, line]); return true; };
"""
    code = index_slice("const dateCols", '$("peak-find").onclick')
    body = r"""
(async () => {
  const cols = dateCols();
  await findPeaks();
  const row = $("pk-list").children[0];
  await row.children[1].onclick();
  const btn = row.children[1];
  els["pk-col"].value = "";
  await findPeaks();
  console.log(JSON.stringify({ cols, sent: sent[0], added, text: row.children[0], label: btn.ariaLabel, more: $("pk-list").children.length, none: $("err-peak").textContent }));
})();
"""
    got = run_node(tmp_path, body, parts=[prelude, code])
    assert got["cols"] == ["day", "at"]  # 날짜 타입이고 기준 테이블의 컬럼만
    assert got["sent"] == ["/api/analyze", {"recipe": {"schema": "", "base": "orders", "filters": []}, "how": {"kind": "busiest", "column": "at", "unit": "week", "top": 5}}]
    assert got["added"] == [["periods", "at 2026-01-05 ~ 2026-01-11"]]
    assert got["text"] == "2026-01-05 ~ 2026-01-11 · 1,200건 " and got["label"] and got["more"] == 1  # (감사48) 서버가 늘 false로 주던 more의 "400개" 안내는 지웠다: 기간 줄 하나만
    assert got["none"] == "기준 테이블에 날짜 타입 컬럼이 없어요."
    assert 'id="pk-col"' in HTML and 'id="peak-find"' in HTML and 'fill($("pk-col"), dateCols())' in HTML
