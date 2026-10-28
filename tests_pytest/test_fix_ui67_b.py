"""수집 화면 문서 스크린샷에서 찾은 것(막대·기록 새로 읽기·잘리는 칸·표 머리글·업로드 기록 글·마침표·적재 ID 안내).
JS는 collect.html의 해당 구간만 잘라 node로 돌리고, CSS·글은 원본을 읽어 본다. 모두 고치기 전 코드에서 실패한다."""
import re

from test_fix_audit_js import between, read, run_node

HTML = "collect.html"
DOLLAR = r"""
const els = {};
globalThis.$ = id => els[id] ??= Object.assign(new FakeEl('div'), { value: '', firstElementChild: { style: {} }, querySelectorAll: () => [] });
globalThis.fileName = p => String(p ?? '').split(/[\\/]/).pop();
globalThis.bytesText = n => String(n ?? '');
globalThis.secs = ms => ms + 'ms';
globalThis.usageText = () => '';
globalThis.BROKEN = '';
document.createTextNode = s => s;
"""


def test_finished_run_fills_bar(tmp_path):
    """요청을 10번으로 예상했는데 4쪽에서 끝나면 막대가 40%에 멈춰 있었다. 끝이면 가득."""
    body = """
let est = 10, running, output;
showStatus({ running: false, phase: "끝", wait: "", mode: "api", pages: 4, requests: 4, rows: 40, bytes: 100, failures: 0, concurrency: 1, elapsed: 1,
  load: null, message: "", usage: null, error: "", output: "C:/out.parquet" });
console.log(JSON.stringify($("bar").firstElementChild.style.width));
"""
    assert run_node(tmp_path, DOLLAR + body, parts=[between(HTML, "function showStatus(s)", "let watching")]) == "100.0%"


def test_watch_end_reloads_uploads(tmp_path):
    """수집·적재가 끝나도 업로드 기록 요약이 "아직 없어요"로 남았다. 끝나면 실행 기록과 함께 다시 읽는다."""
    body = """
const calls = [];
globalThis.api = async () => ({ running: false, phase: "끝", load: null, elapsed: 1, rows: 1, output: "a.parquet" });
globalThis.showStatus = () => {}; globalThis.say = () => {}; globalThis.notifyDone = () => {};
globalThis.loadLimits = () => calls.push("limits"); globalThis.loadRuns = () => calls.push("runs"); globalThis.loadUploads = () => calls.push("uploads");
(async () => { await watch(false, true); console.log(JSON.stringify(calls)); })();
"""
    calls = run_node(tmp_path, DOLLAR + body, parts=[between(HTML, "let watching = false;", '$("start").onclick')])
    assert "runs" in calls and "uploads" in calls


def test_runs_empty_text_says_what_is_recorded():
    """실행 기록은 예약 실행(프로그램·Windows·[지금 실행])만 남긴다(schedule.execute). [시작]으로 받은 것도 남는다고 읽히지 않게."""
    text = read(HTML)
    assert "수집하거나 예약이 실행되면 여기에 남아요" not in text
    assert "[지금 실행]" in between(HTML, "function renderRuns()", "async function markSeen")


def test_select_fields_wide_enough():
    """'새 테이블 만들기'·'구성을 고르세요'가 110px 칸에서 잘렸다."""
    m = re.search(r"\.fields \{[^}]*minmax\((\d+)px", read(HTML))
    assert int(m[1]) >= 150


def test_collect_tables_scroll_in_their_box():
    """표 머리글이 창 전체 스크롤에 붙어 다른 글 위에 떠 있었다. 카드 안 상자에서 스크롤하고 머리글은 불투명하게 위에."""
    text = read(HTML)
    assert re.search(r"\.collect-pane \.table-wrap \{[^}]*overflow:auto", text)
    assert re.search(r"\.collect-pane th \{[^}]*z-index:2[^}]*background:var\(--card\)", text)


def test_upload_time_and_target_readable(tmp_path):
    """요약에 ISO 시각(…T10:39:38), 자세히 보기의 대상에 JSON(백슬래시 두 개)이 그대로 나왔다."""
    body = r"""
globalThis.sampleTable = () => ""; globalThis.esc = s => s; globalThis.say = () => {}; globalThis.busy = () => {};
uploadsData = { statuses: {}, entries: [{ id: "a", kind: "table", started: "2026-10-28T10:39:38", finished: "2026-10-28T10:40:01", status: "done",
  source: "C:\\x\\out.parquet", target: { connection: "C:\\db\\w.duckdb", schema: "main", name: "items" }, rows: 3, bytes: null, range: {},
  removal: { how: "h", text: "DELETE", note: "n" } }] };
renderUploads();
const sum = $("up-sum").textContent;
upOpen = true; upPicked = "a"; renderUploads();
console.log(JSON.stringify([sum, $("up-cmd").textContent]));
"""
    sum_, cmd = run_node(tmp_path, DOLLAR + body, parts=[between(HTML, "let uploadsData", '$("up-toggle").onclick')])
    assert "(2026-10-28 10:39)" in sum_
    assert "대상: C:\\db\\w.duckdb / main.items" in cmd
    assert "시작 2026-10-28 10:39, 끝 2026-10-28 10:40" in cmd


def test_status_messages_end_with_period():
    """'저장했어요'·'수집을 마쳤어요'처럼 마침표 없는 상태 글(연동 화면은 '저장했어요.')."""
    lines = [ln for ln in read(HTML).splitlines() if re.search(r'say\("[\w-]+", .*, true\)|\$\("run-ok"\)\.textContent =', ln)]
    bare = [s for ln in lines for s in re.findall(r'"([^"]*요)"', ln)]
    assert lines and not bare, bare


def test_load_id_hint_matches_replace():
    """덮어쓰기는 테이블을 새로 만들어 바꾸므로(load_table.plan·Sink) 적재 ID 컬럼이 없어도 된다."""
    text = read(HTML)
    assert "추가하거나 덮어쓸 때는" not in text
    assert "기존 테이블에 추가할 때는 테이블에도 이 컬럼이 있어야 해요" in text
