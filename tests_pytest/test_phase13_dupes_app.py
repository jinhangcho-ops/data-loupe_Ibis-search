"""Phase 13 중복 제거 개선(prep_app): /api/dupes의 예시·moved, 못 하는 연결은 이 PC 작업 사본(plans/phase-13.md).
데이터는 tmp_path에 지어낸 DuckDB 파일·Parquet·SQLite다."""
import sqlite3

import duckdb
import polars as pl
import pytest

# 다듬으면 ab · abc · abcxyz · xyz · qq · qq — 글자 다듬기만: {qq, qq} 1묶음, 문장 포함: {ab, abc, abcxyz, xyz}·{qq, qq} 2묶음(빠질 행 4)
DATA = {"id": [1, 2, 3, 4, 5, 6], "t": ["ab", "ABC!", "abc xyz", "xyz", "qq", "Qq?"]}


def connect(body):
    from core import server
    from prep import prep_app  # noqa: F401  전처리 라우트
    from query.search_app import app
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json=body).status_code == 200
    return c


@pytest.fixture
def duck(folder, tmp_path):
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute("create table t as select * from (values (1, 'ab'), (2, 'ABC!'), (3, 'abc xyz'), (4, 'xyz'), (5, 'qq'), (6, 'Qq?')) v(id, t)")
    raw.close()
    c = connect({"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")})
    yield c
    c.post("/api/disconnect", json={})


@pytest.fixture
def parquet(folder, tmp_path):
    from core import held
    pl.DataFrame(DATA).write_parquet(tmp_path / "t.parquet")
    c = connect({"kind": "folder", "path": str(tmp_path), "files": [str(tmp_path / "t.parquet")]})
    yield c
    c.post("/api/disconnect", json={})
    held.store["items"].clear()


@pytest.fixture
def lite(folder, tmp_path):
    from core import held
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table t (id integer, t text)")
    raw.executemany("insert into t values (?, ?)", list(zip(DATA["id"], DATA["t"])))
    raw.commit()
    raw.close()
    c = connect({"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"})
    yield c
    c.post("/api/disconnect", json={})
    held.store["items"].clear()


def ok(c, url, **body):
    r = c.post(url, json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def in_request(recipe, fn):
    from core import server
    with server.app.test_request_context("/api/dupes", method="POST", json=recipe):
        return fn(recipe)


CONTAIN = {"base": "t", "dedupe": ["t"], "dedupe_loose": True, "dedupe_contain": ["t"]}


# ---- dupes 라우트 (수정) ----

def test_api_dupes_duckdb_loose(duck):
    d = ok(duck, "/api/dupes", base="t", dedupe=["t"], dedupe_loose=True)
    assert (d["groups"], d["rows"], d["moved"]) == (1, 1, False)  # DuckDB 파일은 연결이 직접 한다
    assert d["examples"]["columns"] == ["id", "t"]
    assert sorted(r["t"] for r in d["examples"]["rows"]) == ["Qq?", "qq"] and d["example_groups"] == [1, 1]


def test_api_dupes_parquet_contain_moves(parquet):
    d = ok(parquet, "/api/dupes", **CONTAIN)
    assert (d["groups"], d["rows"], d["moved"]) == (2, 4, True)  # Polars는 UDF를 못 해 이 PC 사본에서
    assert len(d["examples"]["rows"]) == len(d["example_groups"]) == 6


def test_api_dupes_sqlite_contain_moves(lite):
    d = ok(lite, "/api/dupes", **CONTAIN)
    assert (d["groups"], d["rows"], d["moved"]) == (2, 4, True)


def test_api_dupes_all_columns(duck):
    d = ok(duck, "/api/dupes", base="t")  # 기준을 안 고르면 모든 컬럼
    assert (d["groups"], d["rows"], d["moved"]) == (0, 0, False)
    assert d["examples"]["rows"] == [] and d["example_groups"] == []


def test_api_dupes_limit(duck):
    r = duck.post("/api/dupes", json={**CONTAIN, "dedupe_limit": 4})  # 다듬은 서로 다른 값 5개(ab, abc, abcxyz, xyz, qq)
    assert r.status_code == 400 and "상한 4개" in r.get_json()["error"]
    assert ok(duck, "/api/dupes", **CONTAIN, dedupe_limit=5)["groups"] == 2
    assert duck.post("/api/dupes", json={**CONTAIN, "dedupe_limit": 0}).status_code == 400  # 0·음수·정수 아님은 거부


# ---- analysis_constants (수정) ----

def test_screen_constant_dedupe_limit():
    from prep import prep_app, prep_expr
    found = {}
    prep_app.analysis_constants(found)
    assert found["dedupe_limit"] == prep_expr.CONTAIN_LIMIT


# ---- built (수정) ----

def test_preview_contain_on_parquet(parquet):
    d = ok(parquet, "/api/preview", **CONTAIN, dedupe_keep={"how": "first", "order": ["id"]})
    assert d["after"] == 2 and sorted(r["id"] for r in d["rows"]["rows"]) == [1, 5]


def test_old_dupes_still_counts(duck):
    """옵션 없는 중복 세기는 예전 값 그대로(요구 5)."""
    d = ok(duck, "/api/dupes", base="t", dedupe=["t"])
    assert (d["groups"], d["rows"]) == (0, 0)


# ---- dedupe_moves ----

def test_dedupe_moves_files(duck):
    from prep import prep_app
    assert in_request(CONTAIN, prep_app.dedupe_moves) is False  # DuckDB 파일은 UDF를 한다
    assert in_request({"base": "t", "dedupe": ["t"]}, prep_app.dedupe_moves) is False


def test_dedupe_moves_parquet(parquet):
    from prep import prep_app
    assert in_request(CONTAIN, prep_app.dedupe_moves) is True
    assert in_request({"base": "t", "dedupe": [], "dedupe_contain": ["t"]}, prep_app.dedupe_moves) is False  # 중복 제거가 없으면 옮기지 않는다
    assert in_request({"base": "t", "dedupe": ["t"], "dedupe_contain": []}, prep_app.dedupe_moves) is False  # 포함 컬럼이 비면 옵션 없음


def test_dedupe_moves_remote(lite):
    from prep import prep_app
    assert in_request({"base": "t", "dedupe": ["t"], "dedupe_loose": True}, prep_app.dedupe_moves) is True  # 원격은 옵션이 있으면 늘 사본
    assert in_request({"base": "t", "dedupe": ["t"]}, prep_app.dedupe_moves) is False


# ---- local_mode (수정) ----

def test_local_mode_new_with_contain(lite, monkeypatch):
    from core import settings
    from prep import prep_app
    monkeypatch.setattr(settings, "load", lambda: {"prep_where": "new"})  # DB가 계산하는 설정에서도
    assert prep_app.local_mode(CONTAIN) is True
    assert prep_app.local_mode({"base": "t", "dedupe": ["t"]}) is False
