"""감사 33차: 파일 주소 [바꾸기]는 지금 칸으로, 내려받기 형식 검사, 연결을 바꾸면 SQL 쪽 넘기기를 처음으로."""
import pytest

from core import server
from test_fix_audit_js import between, run_node


def test_file_secretize_uses_edited_url(tmp_path):
    js = between("collect.html", "const enc = s =>", "// 받는 방식에 따라")  # enc·fileRequest·fillFile
    body = """
const cells = { "f-url": { value: "" } }, $ = id => cells[id], val = id => cells[id].value, getPairs = () => [], setPairs = () => {};
%s
fillFile({ url: "https://x/b.csv", query: [["token", "{env:T}"], ["q", "a b"]] });
console.log(JSON.stringify([val("f-url"), fileRequest()]));
""" % js
    url, req = run_node(tmp_path, body)
    assert url == "https://x/b.csv?token={env:T}&q=a%20b"
    assert req["url"] == "https://x/b.csv" and req["query"] == [["token", "{env:T}"], ["q", "a b"]]  # 고친 주소(b.csv)로, 쿼리는 따로


def test_download_refuses_unknown_format():
    with pytest.raises(ValueError, match="파일 이름은"):
        server.download(None, "csv/../../x.csv")


def test_switch_resets_sql_paging():
    assert 'sqlSkip = 0; sqlMore = false; sqlRan = ""; sqlPager();' in between("index.html", "function clearScreen() {", "\n}\n")
