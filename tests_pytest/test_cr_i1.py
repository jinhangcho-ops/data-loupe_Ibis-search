"""I1(통합 후속): 보낼 곳 설정 글자·인증 확인 이름, 전처리 품질 점검 줄(checks), 펼치기 키 저장, 미리보기 BigQuery 처리 용량 확인, 문장 틀 분류.
화면은 해당 구간만 잘라 node로 돌린다(node가 없으면 건너뛴다). 원격 DB·BigQuery 가짜는 test_cr_e2의 fixture를 그대로 쓴다."""
import shutil

import pytest

from test_cr_e2 import bigquery, remote  # noqa: F401  fixture
from test_fix_audit_js import between, read, run_node

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_dest_button_text_is_in_html_not_js():
    assert ">보낼 곳 설정</button>" in read("status.html") and 'dest-open").textContent' not in read("common.js")
    notify = read("notify.html")
    assert "연결 확인" not in notify and notify.count("인증 확인") == 2


@needs_node
def test_prep_checks_lines_shown(tmp_path):
    code = between("prep.html", "const checkLines", "function showSampled")
    got = run_node(tmp_path, r"""
const prof = { columns: [{ name: "id", checks: ["서로 다른 값 수가 행 수(5)와 같아 키 후보예요(표본 기준)."] }, { name: "s", checks: [] }, { name: "d" },
  { name: "x", checks: ["IQR 밖 값이 1개예요(표본 기준).", "값이 없는 날이 3일이에요(표본 기준)."] }] };
console.log(JSON.stringify([checkLines(prof), checkLines(null)]));""", parts=[code])
    assert got[0] == ["id · 서로 다른 값 수가 행 수(5)와 같아 키 후보예요(표본 기준).", "x · IQR 밖 값이 1개예요(표본 기준).", "x · 값이 없는 날이 3일이에요(표본 기준)."] and got[1] == []
    assert "extraLines" not in read("prep.html") and "key_candidate" not in read("prep.html")


@needs_node
def test_expand_keys_saved_from_first_preview(tmp_path):
    op = between("prep.html", "  expand: {", "};\n")  # expand는 화면 OPS의 마지막 단계(서버 OPS 순서와 같다)
    save = between("prep.html", "function saveExpandKeys", '$("preview").onclick')
    got = run_node(tmp_path, r"""
const OPS = {
""" + op + r"""};
const edits = { j: { ops: { expand: { ...OPS.expand.init } } }, t: { ops: {} } };
const cols = () => [{ name: "j" }, { name: "t" }];
const first = OPS.expand.value(edits.j.ops.expand);
saveExpandKeys({ rows: { columns: ["j", "t", "j.a", "j.b", "t.x"] }, derived: [false, false, true, true, true] });
const saved = OPS.expand.value(edits.j.ops.expand);
saveExpandKeys({ rows: { columns: ["j", "j.zzz"] }, derived: [false, true] });  // 이미 저장했으면 그대로
console.log(JSON.stringify({ first, saved, again: OPS.expand.value(edits.j.ops.expand), loaded: OPS.expand.load({ keys: ["q"] }), plain: OPS.expand.load(true) }));""", parts=[save])
    assert got == {"first": True, "saved": {"keys": ["a", "b"]}, "again": {"keys": ["a", "b"]}, "loaded": {"keys": ["q"]}, "plain": {"keys": None}}


def test_recipe_with_expand_keys_saves_and_applies(remote):
    rec = {"base": "a", "columns": {"j": {"ops": {"expand": {"keys": ["a", "b"]}}}}}
    assert remote.post("/api/recipes", json={"name": "펼침", "recipe": rec}).status_code == 200
    assert remote.get("/api/recipes").get_json()["recipes"]["펼침"] == rec


def test_preview_bigquery_row_count_asks_first(bigquery, remote):
    body = {"schema": "", "base": "orders", "columns": {}, "filters": ["id > 3"], "count": True}
    from core import settings
    settings.save({**settings.load(), "prep_where": "new"})  # 사본을 받지 않고 DB가 바로 센다
    r = remote.post("/api/preview", json=body)
    assert r.headers.get("X-Needs-Confirm") == "1", r.get_json()
    assert bigquery.dry and "읽어요" in r.get_json()["text"]
    assert remote.post("/api/preview", json={**body, "confirmed": True}).status_code == 200
    assert bigquery.runs == []
    bigquery.dry.clear()
    assert remote.post("/api/preview", json={"schema": "", "base": "orders", "columns": {}}).status_code == 200 and not bigquery.dry  # 행 수를 바꾸는 일이 없으면 묻지 않는다


@needs_node
def test_sentence_groups_cover_every_template(tmp_path):
    from query import sentences
    ids = [t["id"] for t in sentences.TEMPLATES if t["kind"] == "explore"]
    code = between("sentences.js", "// 문장 틀 분류", "// 질문 묶음 저장 모양")
    got = run_node(tmp_path, "const tpls = " + __import__("json").dumps([{"id": i} for i in ids + ["e999"]]) + r""";
const out = sentGroups(tpls), table = SENT_GROUPS.flatMap(([, g]) => g);
console.log(JSON.stringify({ names: out.map(g => g[0]), placed: out.flatMap(g => g[1].map(t => t.id)), last: out.at(-1)[1].map(t => t.id), table, groups: SENT_GROUPS.length }));""", parts=[code])
    assert sorted(got["placed"]) == sorted(ids + ["e999"]) and "e999" in got["last"]  # 모든 틀이 한 분류에 한 번씩, 표에 없는 id는 마지막 분류
    assert 4 <= got["groups"] <= 6 and set(got["table"]) <= set(ids) and len(got["table"]) == len(set(got["table"]))  # 표에 낡은 id·겹침이 없다
    assert not (set(ids) - set(got["table"]))  # 지금 있는 틀은 모두 표에 있다(새 틀을 더하면 이 시험이 분류를 정하라고 알려 준다)
    js = read("sentences.js")
    assert '"문장 만들기"' in js and '"내 지표"' in js and 'dataset.tab = k' in js and '[["explore", "' in js
