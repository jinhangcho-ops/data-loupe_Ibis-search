"""전처리의 계산 컬럼·PCA·컬럼 고르기 제안과, 원격 DB의 전처리를 이 PC의 작업 사본에서 계산하기(설정 prep_where).
지어낸 SQLite 파일에 주소로 연결해(원격 DB로 다룬다) 확인한다. 다른 DB에는 붙지 않는다."""
import sqlite3

import numpy as np
import pytest

from lazy import ibis


def frame(n=400, seed=0):
    rng = np.random.default_rng(seed)
    a = rng.normal(size=n)
    return {"id": np.arange(n), "amt": (a * 1000).round(), "qty": rng.integers(1, 5, n), "x": a, "y": 2 * a + rng.normal(scale=0.05, size=n),
            "z": rng.normal(size=n), "name": [f"n{i % 7}" for i in range(n)], "same": ["A"] * n}


@pytest.fixture
def remote(folder, tmp_path, monkeypatch):
    """SQLite 파일에 주소로 연결한 클라이언트와 DB에 간 조회 수(작업 사본을 받은 뒤에는 늘지 않아야 한다)."""
    import server
    from app import app
    import prep_app  # noqa: F401  전처리 라우트
    data = frame()
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table t (id integer, amt real, qty integer, x real, y real, z real, name text, same text)")
    raw.executemany("insert into t values (?, ?, ?, ?, ?, ?, ?, ?)", list(zip(*(data[k].tolist() if hasattr(data[k], "tolist") else data[k] for k in data))))
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"}).status_code == 200
    calls = []
    real = type(server.state["con"]).raw_sql
    monkeypatch.setattr(type(server.state["con"]), "raw_sql", lambda self, *a, **k: (calls.append(a[0] if a else ""), real(self, *a, **k))[1])
    yield c, data, calls, tmp_path
    c.post("/api/disconnect", json={})


RECIPE = {"base": "t", "filters": ["qty >= 2"], "formulas": [{"name": "total", "expr": "amt * qty"}, {"name": "grade", "expr": "iif(amt >= 0, 'plus', 'minus')"},
          {"name": "ratio", "expr": "amt / (qty - qty)"}, {"name": "label", "expr": "name + '님'"}], "pca": {"columns": ["x", "y", "z"], "n": 2}}


def test_preview_with_formulas_and_pca_on_local_copy(remote):
    c, data, calls, _ = remote
    d = c.post("/api/preview", json=RECIPE).get_json()
    assert d["local"] and d["rows"]["columns"][-6:] == ["total", "grade", "ratio", "label", "pca_1", "pca_2"]
    assert d["sources"][-6:] == [""] * 6 and all(d["derived"][-6:])
    row = d["rows"]["rows"][0]
    assert row["total"] == row["amt"] * row["qty"] and row["ratio"] is None and row["label"] == row["name"] + "님"
    keep = data["qty"] >= 2
    X = np.column_stack([data["x"][keep], data["y"][keep], data["z"][keep]])
    w = np.sort(np.linalg.eigvalsh(np.corrcoef(X.T)))[::-1]
    assert np.allclose(d["info"]["pca"]["ratio"], (w / w.sum())[:2], atol=1e-5) and d["after"] == int(keep.sum())
    before = len(calls)
    again = c.post("/api/preview", json={**RECIPE, "formulas": RECIPE["formulas"][:1]}).get_json()  # 같은 테이블·거르기: 사본을 다시 쓴다
    assert "error" not in again and len(calls) == before  # DB에 다시 묻지 않는다(행 수는 처음 센 것을 쓴다)


def test_make_table_inserts_locally_computed_result(remote):
    c, data, calls, tmp_path = remote
    made = c.post("/api/run", json={"recipe": RECIPE, "target": {"kind": "table", "name": "t_prep"}}).get_json()
    assert made == {"ok": True, "rows": int((data["qty"] >= 2).sum())}
    raw = sqlite3.connect(tmp_path / "s.db")
    cols = [r[1] for r in raw.execute("pragma table_info(t_prep)")]
    assert cols[-2:] == ["pca_1", "pca_2"] and raw.execute("select count(*) from t_prep where total = amt * qty").fetchone()[0] == made["rows"]
    raw.close()


def test_setting_new_runs_plain_recipes_on_db(remote):
    import held
    import settings
    c = remote[0]
    settings.write(settings.PATH.name, {"prep_where": "new"})
    plain = c.post("/api/preview", json={"base": "t", "filters": ["qty >= 2"]}).get_json()
    assert plain["local"] is False and not any(k[0] == "prep" for k in held.store["items"])  # 사본을 만들지 않았다
    assert c.post("/api/preview", json=RECIPE).get_json()["local"] is True  # 계산 컬럼·PCA가 있으면 이 PC에서


def test_prune_suggests_from_sample_only(remote):
    c, _, calls, _ = remote
    c.post("/api/profile", json={"base": "t", "sample": 1000})  # 표본을 읽어 둔다
    before = len(calls)
    found = {x["column"]: x for x in c.post("/api/prune", json={"base": "t", "sample": 1000}).get_json()["columns"]}
    assert len(calls) == before  # 이 PC의 표본으로만
    # amt는 x×1000을 반올림한 것, y는 2x에 작은 잡음: 앞에 있는 amt를 남기고 x·y를 빼자고 한다(빈 값 수가 같으면 뒤쪽을 뺀다)
    assert set(found) == {"same", "x", "y"} and found["x"]["with"] == found["y"]["with"] == "amt" and found["y"]["r"] > 0.99
    assert "하나뿐" in found["same"]["reason"] and "z" not in found


@pytest.mark.parametrize("text, message", [("__import__('os')", "함수"), ("amt.real", "Attribute"), ("amt ** 2", "**"), ("amt + name", "숫자끼리"),
                                           ("nothing + 1", "'nothing' 컬럼이 없습니다"), ("iif(amt, 1, 2)", "참거짓"), ("", "식을 입력"), ("(" * 300, "")])
def test_formula_rejects(text, message):
    import prep_derive
    t = ibis.table({"amt": "float64", "name": "string"}, name="t")
    with pytest.raises(ValueError, match=None) as found:
        prep_derive.Formula(t, text).build()
    assert message in str(found.value)


def test_formula_column_names_and_errors():
    import prep_derive
    t = ibis.table({"금액": "float64", "my col": "int64"}, name="t")
    assert prep_derive.Formula(t, '금액 + col("my col")').build().type().is_floating()
    with pytest.raises(ValueError, match="이미 있어"):
        prep_derive.apply_formulas(t, [{"name": "금액", "expr": "1"}])
    with pytest.raises(ValueError, match="PCA에는"):
        prep_derive.pca(t, {"columns": ["금액"], "n": 1}, {})


def test_insert_all_keeps_big_ints_and_leaves_no_temp_tables(tmp_path):
    import pyarrow as pa
    import prep_app
    big = 2**62 + 1  # 실수로 바뀌면 끝자리가 틀어지는 값
    local = ibis.duckdb.connect()
    expr = local.create_table("src", ibis.memtable(pa.table({"i": pa.array([big, None, -big], pa.int64()), "s": ["a", None, "c"]})))
    db = ibis.sqlite.connect(tmp_path / "s.db")
    assert prep_app.insert_all(db, "out", None, expr) == 3
    assert db.list_tables() == ["out"]  # Ibis가 올린 임시 테이블(memtable)을 남기지 않는다
    raw = sqlite3.connect(tmp_path / "s.db")
    assert raw.execute("select i, typeof(i) from out order by rowid").fetchall() == [(big, "integer"), (None, "null"), (-big, "integer")]
    raw.close()


def test_head_and_spec_table_for_tabs(remote, tmp_path):
    c = remote[0]
    rows = c.post("/api/head", json={"base": "t", "sample": 1000}).get_json()["rows"]
    assert len(rows["rows"]) == 20 and rows["columns"][:2] == ["id", "amt"]  # 정보 탭: 표본의 앞 20행
    assert c.post("/api/spec/table", json={"base": "t"}).get_json() == {"path": "", "description": "", "partition": None, "columns": {}}
    spec = tmp_path / "spec.yaml"
    spec.write_text("tables:\n  t:\n    description: 지어낸 표\n    columns:\n      amt: {description: 금액}\n", encoding="utf-8")
    assert c.post("/api/spec", json={"path": str(spec)}).status_code == 200
    found = c.post("/api/spec/table", json={"base": "t"}).get_json()
    assert found["description"] == "지어낸 표" and found["columns"] == {"amt": "금액"}
