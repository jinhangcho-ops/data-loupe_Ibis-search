"""F08 전처리로 보내기: 파일을 만들지 않고 검색 자체(body)를 전처리의 기준 표로 넘긴다. 데이터는 tmp_path에서 지어낸 진짜 DuckDB 파일이다."""
import duckdb
import pytest

ODD = [i for i in range(20) if i % 3 == 1]  # g = 1인 행의 id


@pytest.fixture
def api(folder, tmp_path):
    """표 하나(orders: 20행, g = id % 3)가 든 DuckDB 파일에 연결한 클라이언트."""
    from core import server
    from prep import prep_app  # noqa: F401  전처리 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute("create table orders as select i as id, i % 3 as g, i * 1.5 as amt, 'n' || i as note from range(20) r(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")}).status_code == 200
    yield c
    server.ask_save = None
    c.post("/api/disconnect", json={})


SEARCH = {"base": "orders", "conditions": ["g = 1"], "columns": ["id, amt"], "sorts": ["id"]}


def ok(c, url, **body):
    r = c.post(url, json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_검색을_파일_없이_넘기고_전처리가_그_행으로_동작(api, tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    opened = api.get("/api/state").get_json()["opened"]
    got = ok(api, "/api/handoff", **SEARCH, mode="query", name="내 검색")
    assert got["base"] == got["table"] == "@내 검색" and got["url"] == "/prep?base=%40%EB%82%B4%20%EA%B2%80%EC%83%89"
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # 파일이 생기지 않는다
    assert api.get("/api/state").get_json()["opened"] == opened  # 연결도 그대로
    base = got["base"]
    shape = ok(api, "/api/shape", base=base)
    assert shape["rows"] == len(ODD) and [c["name"] for c in shape["columns"]] == ["id", "amt"]
    assert ok(api, "/api/profile", base=base, sample=1000)["rows"] == len(ODD)
    pre = ok(api, "/api/preview", base=base, columns={"amt": {"keep": False}}, filters=["id > 5"])
    assert pre["before"] == len(ODD) and pre["after"] == len([i for i in ODD if i > 5])
    assert pre["rows"]["columns"] == ["id"] and [r["id"] for r in pre["rows"]["rows"]] == [i for i in ODD if i > 5]
    assert ok(api, "/api/dupes", base=base, dedupe=["id"]) == {"groups": 0, "rows": 0}
    assert ok(api, "/api/run", recipe={"base": base, "columns": {}}, target={"kind": "table", "name": "t2"}) == {"ok": True, "rows": len(ODD)}
    assert ok(api, "/api/rowcount", base="t2") == {"rows": len(ODD)}
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_같은_이름으로_다시_보내면_새_검색(api):
    base = ok(api, "/api/handoff", **SEARCH, mode="query")["base"]
    assert base == "@검색 결과"
    assert ok(api, "/api/profile", base=base, sample=1000)["rows"] == len(ODD)
    ok(api, "/api/handoff", **{**SEARCH, "conditions": ["g = 0"]}, mode="query")
    assert ok(api, "/api/shape", base=base)["rows"] == 7
    assert ok(api, "/api/profile", base=base, sample=1000)["rows"] == 7  # 앞 검색의 캐시를 쓰지 않는다


def test_잘못된_검색과_없는_넘김은_거부(api):
    assert "없어요" in api.post("/api/handoff", json={"base": "nothing", "mode": "query"}).get_json()["error"]
    assert "없어요" in api.post("/api/shape", json={"base": "@모르는 검색"}).get_json()["error"]


def test_옛_방식은_그대로_파일로_저장하고_연결을_바꾼다(api, tmp_path):
    from core import server
    server.ask_save = lambda name, types: str(tmp_path / "picked.parquet")
    got = ok(api, "/api/handoff", **SEARCH)
    assert got["table"] == "picked" and (tmp_path / "picked.parquet").exists()
    assert ok(api, "/api/rowcount", base="picked") == {"rows": len(ODD)}


def test_원격이면_처리_용량_확인을_거쳐_검색_결과를_사본으로_받는다(api, monkeypatch):
    from core import held, server
    monkeypatch.setattr(held, "remote", lambda con: True)
    asked = []
    monkeypatch.setattr(server, "scan_gate", lambda body, make_sql, rows=None: asked.append(make_sql()[0]))
    base = ok(api, "/api/handoff", **SEARCH, mode="query", name="원격")["base"]
    pre = ok(api, "/api/preview", base=base, columns={})
    assert pre["local"] is True and pre["before"] == pre["after"] == len(ODD)
    assert [r["id"] for r in pre["rows"]["rows"]] == ODD
    assert asked and "orders" in asked[0]  # 사본으로 받을 검색의 SQL을 확인했다
    assert any(isinstance(k, tuple) and k[0] == "prep" and base in k[1] for k in held.store["items"])
