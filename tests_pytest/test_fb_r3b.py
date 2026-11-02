"""사용자 피드백 F06(고정장·비고정장 형식)과 F02(여러 테이블을 Excel 한 파일의 시트 여러 개로). 진짜 DuckDB·Polars로, 데이터는 tmp_path에 지어낸다."""
import duckdb
import polars as pl
import pytest

from lazy import ibis
from core import result
from core import server

BOM = b"\xef\xbb\xbf"


def small(tmp_path):
    """id(정수)·name(글자, 한글)·score(실수, 빈 값 하나)의 DuckDB 표."""
    con = ibis.duckdb.connect()
    con.raw_sql("create table t as select * from (values (1, '가나', 1.5), (10, 'abc', null)) v(id, name, score)")
    return con.table("t")


def test_TSV는_탭으로_나누고_BOM을_붙인다(tmp_path):
    result.export(small(tmp_path), tmp_path / "o.tsv")
    raw = (tmp_path / "o.tsv").read_bytes()
    assert raw.startswith(BOM)
    assert raw[3:].decode("utf-8").splitlines() == ["id\tname\tscore", "1\t가나\t1.5", "10\tabc\t"]


def test_TSV는_파일_연결_Polars에서도_된다(tmp_path):
    pl.DataFrame({"a": [1, 2], "b": ["x\ty", "z"]}).write_parquet(tmp_path / "p.parquet")
    t = ibis.polars.connect().read_parquet(tmp_path / "p.parquet", table_name="p")
    result.export(t, tmp_path / "p.tsv")
    back = pl.read_csv(tmp_path / "p.tsv", separator="\t")  # 탭이 든 값은 따옴표로 감싸 되읽힌다
    assert back.rows() == [(1, "x\ty"), (2, "z")]


def test_고정폭은_한글을_2칸으로_세고_숫자는_오른쪽_정렬(tmp_path):
    result.export(small(tmp_path), tmp_path / "o.txt")
    raw = (tmp_path / "o.txt").read_bytes()
    assert raw.startswith(BOM)
    assert raw[3:].decode("utf-8").split("\n") == ["id name score", " 1 가나   1.5", "10 abc       ", ""]


def test_고정폭은_줄바꿈을_빈칸으로_바꾸고_행_상한을_지킨다(tmp_path, monkeypatch):
    t = ibis.memtable({"s": ["a\nb", "c"]})
    result.export(t, tmp_path / "n.txt")
    assert (tmp_path / "n.txt").read_bytes()[3:].decode("utf-8").split("\n") == ["s  ", "a b", "c  ", ""]
    monkeypatch.setattr(result, "EXCEL_MAX_ROWS", 1)
    with pytest.raises(ValueError, match="1행까지"):
        result.export(t, tmp_path / "m.txt")
    assert not (tmp_path / "m.txt").exists()  # 쓰다 만 파일은 남기지 않는다


def test_시트_이름은_금지_문자_31자_중복을_다룬다():
    long = "가" * 40
    assert result.sheet_names(["a/b:c", long, "T", "t", "History", "", "'q'"]) == ["a_b_c", "가" * 31, "T", "t_2", "History_2", "Sheet", "q"]
    assert result.sheet_names([long, long]) == ["가" * 31, "가" * 29 + "_2"]


@pytest.fixture
def tables(client, tmp_path, monkeypatch):
    path = tmp_path / "s.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("create table \"주문/목록\" as select i as id, 'x' || i as name from range(5) t(i)")
        c.execute("create table t as select 1 as k, '=1+1' as f")
        c.execute("create schema s2")
        c.execute("create table s2.t as select 2 as k")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    return client


def test_여러_테이블을_시트_여러_개로_저장(tables, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(server, "ask_save", lambda name, types: seen.append((name, types)) or str(tmp_path / "out"))
    monkeypatch.setattr(result, "EXCEL_MAX_ROWS", 3)  # 넘친 테이블은 상한까지만 쓰고 알린다
    body = {"tables": [{"name": "주문/목록"}, {"name": "t"}, {"schema": "s2", "name": "t"}], "format": "xlsx"}
    got = tables.post("/api/export/tables", json=body).get_json()
    assert got == {"path": str(tmp_path / "out.xlsx"), "cut": ["주문_목록"], "limit": 3}
    assert seen == [("tables.xlsx", ("Excel (*.xlsx)",))]
    sheets = pl.read_excel(tmp_path / "out.xlsx", sheet_id=0)
    assert list(sheets) == ["주문_목록", "t", "t_2"]
    assert sheets["주문_목록"]["id"].to_list() == [0, 1, 2]
    assert sheets["t"].rows() == [(1, "'=1+1")]  # 수식 막기
    assert sheets["t_2"].rows() == [(2,)]
    assert not list(tmp_path.glob("*.part.xlsx"))
    assert "/api/export/tables" not in server.FREE  # 연결을 쓴다


def test_여러_테이블_저장의_취소와_잘못된_요청(tables, monkeypatch):
    monkeypatch.setattr(server, "ask_save", lambda name, types: None)
    assert tables.post("/api/export/tables", json={"tables": [{"name": "t"}]}).get_json() == {"cancelled": True}
    for bad, why in [({"tables": []}, "하나 이상"), ({"tables": [{"name": "t"}], "format": "csv"}, "Excel"), ({"tables": ["t"]}, "하나 이상")]:
        reply = tables.post("/api/export/tables", json=bad)
        assert reply.status_code == 400 and why in reply.get_json()["error"]
    monkeypatch.setattr(server, "ask_save", None)
    assert "프로그램 창" in tables.post("/api/export/tables", json={"tables": [{"name": "t"}]}).get_json()["error"]


def test_내보내기_라우트의_tsv와_fixed(tables):
    query = dict(base="t", joins=[], conditions=[], periods=[], sorts=[], columns=[])
    tsv = tables.post("/api/export", json={**query, "format": "tsv"})
    assert "result.tsv" in tsv.headers["Content-Disposition"] and tsv.data == BOM + "k\tf\n1\t'=1+1\n".encode()
    fixed = tables.post("/api/export", json={**query, "format": "fixed"})
    assert "result.txt" in fixed.headers["Content-Disposition"] and fixed.data == BOM + "k f    \n1 '=1+1\n".encode()
    sql = tables.post("/api/sql/export", json={"sql": "select k from t", "format": "fixed"})
    assert sql.data == BOM + b"k\n1\n"


def test_여러_시트_문서():
    from conftest import ROOT
    text = (ROOT / "docs" / "functions" / "explore.md").read_text(encoding="utf-8")
    assert "TSV" in text and "고정폭" in text and "시트" in text
