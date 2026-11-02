"""F13: 시작 화면의 카드를 누르면, 지난번 연결이 폴더·파일이면 연결 카드를 거치지 않고 그 연결로 바로 이어 간다.
home.html의 인라인 스크립트를 가짜 화면·가짜 서버 응답 위에서 node로 돌려 본다(고치기 전에는 대부분 연결 카드를 띄워 실패한다).
서버는 폴더 연결의 파일 이름을 남기지 않으므로, 찾은 파일이 하나뿐일 때만 자동으로 잇는다."""
import json
import re
import shutil

import pytest

from conftest import ROOT
from test_fix_audit_js import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

HOME = (ROOT / "static" / "core" / "home.html").read_text(encoding="utf-8")
SCRIPT = HOME[HOME.index("let target = null;"):HOME.index("</script>", HOME.index("let target = null;"))]

# 가짜 환경: 서버 응답은 CASE(환경 변수)에서 읽고, 연결 카드·화면 이동·알림은 기록만 한다.
PRELUDE = r"""
const CASE = JSON.parse(process.env.CASE);
const out = { calls: [], href: "", open: [], msgs: [], shown: [], refresh: false };
globalThis.location = { set href(v) { out.href = v; }, get href() { return out.href; } };
globalThis.SERVER = { file_kinds: ["duckdb", "parquet", "csv", "json", "excel"] };
globalThis.api = async (url, body) => {
  out.calls.push({ url, body });
  const r = CASE[url] ?? {};
  if (r.error) throw new Error(r.error);
  return r;
};
globalThis.openConnect = t => out.open.push(t);
globalThis.connMsg = t => out.msgs.push(t);
globalThis.closeConnect = () => {};
globalThis.showConnected = async (flat, schemas, info) => { out.shown.push({ flat, info }); globalThis.connected = flat ? "flat" : "other"; };
globalThis.startRefresh = () => { out.refresh = true; };
globalThis.resume = async () => {};
globalThis.connected = CASE.connected;
const buttons = [{ dataset: { go: CASE.go }, querySelector: () => ({ textContent: "쿼리" }), onclick: null }];
document.querySelectorAll = () => buttons;
"""
BODY = "(async () => { await buttons[0].onclick(); console.log(JSON.stringify(out)); })();"


def click(tmp_path, target, *, recent=None, files=None, flat=None, files_error=None, connect=None, connected=None):
    """시작 화면에서 target 카드를 누른 결과(out)를 돌려준다."""
    case = {
        "go": target,
        "connected": connected,
        "/api/recent": {"recent": recent},
        "/api/files": {"error": files_error} if files_error else {"files": files or [], "flat": flat or []},
        "/api/connect": connect or {"schemas": ["main"]},
    }
    return run_node(tmp_path, BODY, parts=[PRELUDE, SCRIPT], env={"CASE": json.dumps(case, ensure_ascii=False)})


def connects(out):
    return [c["body"] for c in out["calls"] if c["url"] == "/api/connect"]


FOLDER = {"kind": "folder", "path": "C:/data"}


def test_duckdb_file_resumes_without_card(tmp_path):
    out = click(tmp_path, "/search", recent=FOLDER, files=["C:/data/sales.duckdb"])
    assert connects(out) == [{"kind": "folder", "path": "C:/data", "file": "C:/data/sales.duckdb"}]
    assert out["href"] == "/search" and out["open"] == []
    assert out["shown"][0]["flat"] is False and out["refresh"] is True


def test_single_table_file_resumes_with_files(tmp_path):
    out = click(tmp_path, "/prep", recent=FOLDER, flat=["C:/data/a.csv"])
    assert connects(out) == [{"kind": "folder", "path": "C:/data", "files": ["C:/data/a.csv"]}]
    assert out["href"] == "/prep" and out["open"] == []
    assert out["shown"][0]["flat"] is True


def test_several_files_keep_the_card(tmp_path):
    out = click(tmp_path, "/search", recent=FOLDER, files=["C:/data/a.duckdb"], flat=["C:/data/a.csv", "C:/data/b.csv"])
    assert connects(out) == [] and out["href"] == ""
    assert out["open"] == ["연결하면 쿼리 화면으로 넘어가요"]


def test_vanished_folder_shows_card_with_error(tmp_path):
    out = click(tmp_path, "/search", recent=FOLDER, files_error="그런 폴더나 파일이 없어요.")
    assert connects(out) == [] and out["href"] == ""
    assert out["msgs"] == ["그런 폴더나 파일이 없어요."] and len(out["open"]) == 1


@pytest.mark.parametrize("recent", [None, {"kind": "url", "url": "postgres://db.example.invalid/sales", "user": "analyst"}])
def test_db_or_no_recent_keeps_card(tmp_path, recent):
    out = click(tmp_path, "/search", recent=recent)
    assert not any(c["url"] == "/api/files" for c in out["calls"])
    assert connects(out) == [] and out["href"] == "" and len(out["open"]) == 1


def test_collect_opens_without_connection(tmp_path):
    out = click(tmp_path, "/collect", recent=FOLDER, files=["C:/data/sales.duckdb"])
    assert out["calls"] == [] and out["href"] == "/collect"


def test_already_connected_goes_straight(tmp_path):
    out = click(tmp_path, "/search", recent=FOLDER, connected="other")
    assert out["calls"] == [] and out["href"] == "/search"


def test_changed_files_have_no_control_chars():
    for path in ("static/core/common.js", "static/core/home.html", "tests_pytest/test_fb_r1d.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text), path
