"""모두 비우기(flush): 위쪽 줄 [비우기]와 설정 [메모리]의 [모두 비우고 처음으로].
한 번 더 눌러야 비우고, 붙잡고 있는 것 전부를 놓은 뒤 첫 화면으로 간다. 화면 함수(flushButton)는 node로 돌려 본다."""
from test_fix_audit_js import between, read, run_node
from test_ux16 import needs_node, web


@needs_node
def test_flush_asks_once_then_drops_everything_and_goes_home(tmp_path):
    code = between("common.js", 'const FLUSH_SURE = ', 'flushButton($("flush")')
    body = r"""
const out = { calls: [], went: [], failed: [] };
let items = [{ id: "connection" }, { id: "sample" }], broken = false;
globalThis.api = async (url, body) => { out.calls.push([url, body ?? null]); if (broken) throw new Error("비우지 못했어요"); return { items }; };
globalThis.location = { assign: to => out.went.push(to) };
const b = { textContent: "비우기", disabled: false };
flushButton(b, text => out.failed.push(text));
(async () => {
  await b.onclick(); out.first = [b.textContent, out.calls.length];  // 처음 누르면 묻기만 한다
  b.onblur(); out.blurred = b.textContent;  // 다른 곳을 누르면 원래 글자로
  await b.onclick(); await b.onclick(); out.second = [b.disabled, out.calls.slice(), out.went.slice()];  // 두 번 누르면 전부 놓고 첫 화면으로
  items = []; out.calls = []; out.went = []; b.disabled = false; b.textContent = "비우기";
  await b.onclick(); await b.onclick(); out.empty = [out.calls.slice(), out.went.slice()];  // 붙잡은 것이 없으면 놓기 요청 없이 첫 화면으로
  broken = true; out.went = []; b.disabled = false; b.textContent = "비우기";
  await b.onclick(); await b.onclick(); out.broken = [b.disabled, b.textContent, out.went.slice(), out.failed.slice()];  // 실패하면 단추를 되돌리고 문구를 알린다
  console.log(JSON.stringify(out));
})();
"""
    got = run_node(tmp_path, body, parts=[code])
    assert got["first"] == ["한 번 더 누르면 모두 비워요", 0] and got["blurred"] == "비우기"
    assert got["second"] == [True, [["/api/memory", None], ["/api/memory/drop", {"ids": ["connection", "sample"]}]], ["/"]]
    assert got["empty"] == [[["/api/memory", None]], ["/"]]
    assert got["broken"] == [False, "비우기", [], ["비우지 못했어요"]]


def test_flush_buttons_are_on_topbar_and_memory_page():
    top = between("status.html", '<header class="topbar">', "</header>")
    assert top.index('<div class="top-tools">') < top.index('<button id="flush" class="btn" type="button"') and ">비우기</button>" in top
    page = between("status.html", '<section class="set-page hidden" data-set="memory"', "</section>")
    assert page.index('id="mem-flush"') < page.index('id="mem-all"') and ">모두 비우고 처음으로</button>" in page
    js = read("common.js")
    assert 'flushButton($("flush"), text => { $("flush").title = text; });' in js and 'flushButton($("mem-flush"), text => say("mem-msg", text));' in js
    bar, api = web("components/shell/TopBar.tsx"), web("lib/api.ts")  # 대시보드 위쪽 줄도 같은 길
    assert 'id="flush"' in bar and 'await flushMemory(); location.replace("/");' in bar and "if (!sure) return setSure(true);" in bar
    assert 'if (ids.length) await api("/api/memory/drop", { ids });' in api
