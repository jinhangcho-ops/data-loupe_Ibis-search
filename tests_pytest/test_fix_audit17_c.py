"""17차 C: Excel 저장에서 소수 0자리 Decimal 정수(원격 DB의 NUMBER·DECIMAL(19,0) ID)도 15자리가 넘으면 글자로. 값은 시험 안에서 지어낸다."""
import re
import zipfile

import duckdb
from lazy import ibis

from core import result


def test_excel_keeps_decimal_id_digits(tmp_path):
    raw = duckdb.connect()
    raw.execute("create table t as select 1234567890123456789::DECIMAL(20,0) d, 12.5::DECIMAL(10,2) m, 7::DECIMAL(10,0) small")
    out = tmp_path / "a.xlsx"
    result.write_excel(ibis.duckdb.from_connection(raw).table("t"), out)
    sheet = zipfile.ZipFile(out).read("xl/worksheets/sheet1.xml").decode()
    cells = dict(re.findall(r'<c r="([A-C]2)"[^>]*>(.*?)</c>', sheet))
    assert cells["A2"] == "<is><t>1234567890123456789</t></is>"  # 끝자리가 바뀌지 않게 글자로
    assert cells["B2"] == "<v>12.5</v>" and cells["C2"] == "<v>7</v>"  # 소수가 있는 값·작은 정수는 숫자 그대로


@__import__("pytest").mark.skipif(__import__("shutil").which("node") is None, reason="node가 없다")
def test_deleting_last_of_or_group_keeps_and(tmp_path):
    """(paid or refund) and amount>100에서 refund를 지우면 paid and amount>100(or로 붙지 않는다). 화면의 dropItem·grouped를 그대로 돌린다."""
    from test_fix_audit_js import index_slice, run_node
    code = index_slice("function dropItem", "// 검색 조건 패널")
    body = """
const S = { conditions: [{ line: "status = 'paid'", or: true }, { line: "status = 'refund'", or: false }, { line: "amount > 100", or: false }] };
dropItem(S.conditions, 1);
const a = grouped();
S.conditions = [{ line: "a = 1", or: true }, { line: "b = 2", or: true }, { line: "c = 3", or: false }];
dropItem(S.conditions, 1);  // 묶음 가운데를 지우면 남은 둘은 그대로 or
const b = grouped();
console.log(JSON.stringify([a, b]));
"""
    assert run_node(tmp_path, body, parts=[code]) == [["status = 'paid'", "amount > 100"], [{"any": ["a = 1", "c = 3"]}]]
