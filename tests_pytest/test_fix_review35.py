"""35차 리뷰: 적재 [확인]을 실제로 돌려 결과가 보이는지(요청 번호 변수 이름 겹침으로 늘 오류였다), 늦은 응답은 버린다."""
import shutil

import pytest

from test_fix_audit_js import between, run_node

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

STUB = r"""
const els = {}; const $ = id => (els[id] ??= { textContent: "", className: "", innerHTML: "", replaceChildren() { this.innerHTML = ""; }, addEventListener() {} });
let reply = null; const api = async () => { const r = reply; await null; return r; };
const busy = (b, f) => f(); const checked = () => ({}); const loadPassword = () => ""; const sampleTable = () => "<table>";
"""


@node
def test_load_check_shows_result_and_drops_late_reply(tmp_path):
    code = between("collect.html", "let checkCall = 0;", "// Kafka 적재할 곳 등록")
    body = r"""
(async () => {
  reply = { message: "테이블이 있어요", ok: true, columns: [["a", "정수"]], table_columns: [["a", "정수"]], exists: true };
  await $("ld-check").onclick();
  const first = [$("ld-check-msg").textContent, $("ld-check-msg").className];
  reply = { message: "옛 결과", ok: true };
  const late = $("ld-check").onclick();
  clearCheck();  // 기다리는 사이 칸을 고쳤다
  await late;
  console.log(JSON.stringify([first, $("ld-check-msg").textContent]));
})();
"""
    assert run_node(tmp_path, body, parts=[STUB, code]) == [["테이블이 있어요", "ok"], ""]
