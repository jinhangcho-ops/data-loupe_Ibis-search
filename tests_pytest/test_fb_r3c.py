"""F17 모든 테이블의 컬럼 요약을 한 파일로, F28 구조·맵·JSON 컬럼을 컬럼 여러 개로 펼치기. 데이터는 tmp_path에서 지어낸 진짜 DuckDB 파일이다."""
import csv
from pathlib import Path

import duckdb
import fastexcel
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def api(folder, tmp_path, monkeypatch):
    """표 넷(a: 10행, a?b: 1행, b: 3행, mp: 맵 컬럼 5행)이 든 DuckDB 파일에 연결한 클라이언트. 저장 대화상자는 tmp_path의 고른 이름으로 바꿔 끼운다."""
    from core import profiler, server
    from prep import prep_app  # noqa: F401  전처리 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute("""create table a as select i as id, case when i < 4 then null else 'v' || (i % 3) end as tag, i * 1.5 as amt,
        (case when i = 0 then '=SUM(1)' else 'ok' end) as note,
        {'x': i, 'y': 'q' || i} as s,
        case when i = 0 then '{"a": 1, "b": "x"}' when i = 1 then '{"b": "y", "c": true}' else null end as j,
        [i, i + 1] as l from range(10) r(i)""")
    raw.execute("create table mp as select i as id, map {'k1': i, 'k2': i * 2} as m from range(5) r(i)")
    raw.execute('create table "a?b" as select 1 as v')  # 시트 이름에 못 쓰는 글자
    raw.execute("create table b as select i as n, 'x' as name from range(3) r(i)")
    raw.close()
    monkeypatch.setattr(profiler, "FALLBACK", [("head", profiler.head)])
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")}).status_code == 200
    asked = []
    server.ask_save = lambda name, types: (asked.append((name, types)), str(tmp_path / "out" / name))[1]
    (tmp_path / "out").mkdir()
    c.asked = asked
    yield c
    server.ask_save = None
    c.post("/api/disconnect", json={})


def export(c, **more):
    return c.post("/api/profile/export", json={"sample": 1000, **more})


def sheets(path):
    book = fastexcel.read_excel(str(path))
    return {name: book.load_sheet_by_name(name).to_polars() for name in book.sheet_names}


def test_엑셀은_테이블마다_시트와_첫_시트_목록(api, tmp_path):
    got = export(api, format="xlsx").get_json()
    path = Path(got["path"])
    assert path.suffix == ".xlsx" and got["tables"] == 4 and api.asked[0][0].endswith(".xlsx")
    book = sheets(path)
    assert list(book) == ["테이블 목록", "a", "a_b", "b", "mp"]
    assert book["테이블 목록"].select("테이블", "행 수").rows() == [("a", 10), ("a?b", 1), ("b", 3), ("mp", 5)]
    assert [r["컬럼"] for r in book["mp"].iter_rows(named=True)] == ["id", "m"]
    cols = {r["컬럼"]: r for r in book["a"].iter_rows(named=True)}
    assert list(cols) == ["id", "tag", "amt", "note", "s", "j", "l"]
    assert cols["tag"]["빈 값 수"] == 4 and cols["tag"]["빈 값 비율"] == 0.4 and cols["tag"]["고유값 수"] == 3
    assert (cols["id"]["최솟값"], cols["id"]["최댓값"]) == (0, 9) and cols["id"]["타입"] == "int64"
    assert 1 <= len(cols["tag"]["예시 값"].split(" | ")) <= 3 and set(cols["id"]["예시 값"].split(" | ")) <= {str(i) for i in range(10)}  # 표본은 기본 층화(Phase 2)라 앞 행이 아닐 수 있다
    assert cols["s"]["최솟값"] is None and cols["s"]["예시 값"]  # 구조 컬럼은 최솟값 없이 예시 값만


def test_고른_테이블만과_CSV는_테이블_이름_컬럼(api):
    got = export(api, tables=["b"], format="csv").get_json()
    rows = list(csv.DictReader(open(got["path"], encoding="utf-8-sig", newline="")))
    assert [r["테이블"] for r in rows] == ["b", "b"] and [r["컬럼"] for r in rows] == ["n", "name"] and rows[0]["행 수"] == "3"
    assert rows[1]["최솟값"] == "" and rows[1]["예시 값"] == "x" and rows[0]["최댓값"] == "2"


def test_수식으로_읽힐_예시_값은_막는다(api):
    got = export(api, tables=["a"], format="csv").get_json()
    note = next(r for r in csv.DictReader(open(got["path"], encoding="utf-8-sig", newline="")) if r["컬럼"] == "note")
    assert not note["예시 값"].startswith("=") and "SUM" in note["예시 값"]


def test_full이면_전체_값으로(api, monkeypatch):
    from core import profiler
    monkeypatch.setattr(profiler, "FALLBACK", [("head", profiler.head)])
    got = export(api, tables=["a"], format="xlsx", sample=1000, full=True).get_json()
    assert "path" in got, got
    assert sheets(got["path"])["테이블 목록"].row(0, named=True)["기준"] == "전체"
    small = export(api, tables=["a"], format="xlsx", full=False).get_json()
    assert sheets(small["path"])["테이블 목록"].row(0, named=True)["기준"].startswith("표본")


def test_저장_취소와_잘못된_입력(api):
    from core import server
    server.ask_save = lambda name, types: None
    assert export(api, format="xlsx").get_json() == {"cancelled": True}
    assert "테이블" in export(api, tables=["없는표"], format="xlsx").get_json()["error"]
    assert "형식" in export(api, format="pdf").get_json()["error"]
    server.ask_save = None
    assert "저장 위치" in export(api, format="xlsx").get_json()["error"]


def test_화면_글자_확인():
    html = (ROOT / "static/prep/prep.html").read_text(encoding="utf-8")
    assert 'id="profile-export"' in html and "/api/profile/export" in html
    assert "expand:" in html and "펼치기" in html


# ---------- F28 ----------
def expanded(api, column, base="a", **spec):
    """미리보기는 맵 컬럼을 보여 주지 못해(Polars가 못 바꿈) 맵 컬럼은 keep false로 뺀다(펼친 컬럼만 남는다)."""
    rec = {"base": base, "columns": {column: {"ops": {"expand": True}, **spec}}}
    got = api.post("/api/preview", json=rec).get_json()
    if "rows" in got:  # {rows: {columns, rows}, derived, …} → 컬럼 이름과 행 목록을 위로
        got = {**got, "columns": got["rows"]["columns"], "rows": got["rows"]["rows"]}
    return got, rec


def test_구조는_필드마다_컬럼(api):
    got, _ = expanded(api, "s")
    assert got["columns"][got["columns"].index("s") + 1:][:2] == ["s.x", "s.y"]
    row = got["rows"][3]
    assert row["s.x"] == 3 and row["s.y"] == "q3" and got["derived"][got["columns"].index("s.x")] is True


def test_맵은_표본의_키마다_컬럼(api):
    got, _ = expanded(api, "m", base="mp", keep=False)
    assert got["columns"] == ["id", "m.k1", "m.k2"]
    assert got["rows"][4]["m.k2"] == 8


def test_JSON_글자는_맨_위_키마다_글자_컬럼(api):
    got, _ = expanded(api, "j")
    row0, row1, row2 = got["rows"][:3]
    assert [c for c in got["columns"] if c.startswith("j.")] == ["j.a", "j.b", "j.c"]
    assert (row0["j.a"], row0["j.b"], row0["j.c"]) == ("1", "x", None)
    assert (row1["j.b"], row1["j.c"]) == ("y", "true") and row2["j.a"] is None


def test_펼칠_수_없는_컬럼과_이름_겹침과_키_상한(api, tmp_path):
    assert "구조·맵·JSON" in expanded(api, "id")[0]["error"]
    assert "구조" in expanded(api, "l")[0]["error"]  # 목록은 unnest로
    assert "이미 있어" in api.post("/api/preview", json={"base": "a", "copies": [{"from": "id", "name": "s.x"}], "columns": {"s": {"ops": {"expand": True}}}}).get_json()["error"]
    api.post("/api/disconnect", json={})
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute("create table w as select map_from_entries([('k' || x, 1) for x in range(60)]) as m")
    raw.close()
    api.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")})
    assert "50" in api.post("/api/preview", json={"base": "w", "columns": {"m": {"keep": False, "ops": {"expand": True}}}}).get_json()["error"]


def test_recipe에_담겨_저장되고_다시_열린다(api):
    rec = {"base": "a", "columns": {"s": {"ops": {"expand": True}}}}
    assert api.post("/api/recipes", json={"name": "펼침", "recipe": rec}).status_code == 200
    assert api.get("/api/recipes").get_json()["recipes"]["펼침"] == rec
