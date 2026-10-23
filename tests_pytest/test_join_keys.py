"""공통 키 찾기(join_keys.py): 점수(이름 0.35·겹침 0.45·타입 0.10·고유 0.10)를 손으로 계산한 값과 맞추고, 자동 선택(1등 ≥ 0.7이고 2등과 0.15 이상 차이)·모호하면 상위 3개·후보 없으면 날짜 맞춤,
글자↔숫자(바꾸면), 두 컬럼 묶음, 기억(스키마 지문), 이은 뒤 부풂 경고, 원격 DB는 값을 읽는 조회를 새로 보내지 않음. 데이터는 테스트 안에서 지어낸다."""
import duckdb
import polars as pl
import pytest
from lazy import ibis

from query import join_keys as jk
from conftest import spy_queries


@pytest.fixture
def db(tmp_path, folder):
    path = tmp_path / "k.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table orders as select i::int as order_id, (i % 5)::int as user_id, (i % 3)::int as region, ((i + 1) * 1.5)::double as amt from range(100) t(i)")
    raw.execute("create table users as select i::int as id, 'n' || i as name, (i % 3)::int as region from range(5) t(i)")
    raw.execute("create table users_slim as select i::int as id, 'n' || i as name from range(5) t(i)")
    raw.execute("create table users_txt as select i::varchar as id, 'n' || i as name from range(5) t(i)")
    raw.execute("create table t1 as select 'x' || i as a, date '2026-01-01' + i::int as d from range(10) t(i)")
    raw.execute("create table t2 as select 'y' || i as b, date '2027-06-01' + i::int as e from range(10) t(i)")
    raw.execute("create table grid as select (i // 4)::int as x, (i % 4)::int as y from range(16) t(i)")
    raw.execute("create table one as select 0::int as x, 0::int as y")
    raw.execute("create table lk as select * from (values (1, 10), (1, 20), (2, 30)) t(k, v)")
    raw.execute("create table rk as select * from (values (1, 'a'), (1, 'b'), (2, 'c'), (2, 'd')) t(k, w)")
    raw.execute("create table rk1 as select * from (values (1, 'a'), (2, 'c'), (3, 'e')) t(k, w)")
    raw.close()
    con = ibis.duckdb.connect(str(path))
    yield con
    con.disconnect()


def pair(db, a, b, a_name=None, b_name=None, **kw):
    return jk.suggest(db.table(a), db.table(b), a_name or a, b_name or b, **kw)


def test_name_scores():
    assert jk.name_score("userId", "user_id") == 1.0 and jk.name_score("order date", "orderDate") == 1.0 and jk.name_score("Order-Date", "order_date") == 1.0
    assert jk.name_score("user_id", "id", "orders", "users") == 0.9  # 테이블이름(단수)+id ↔ id
    assert jk.name_score("id", "user_id", "users", "orders") == 0.9
    assert jk.name_score("user_id", "id", "orders", "accounts") == 0.5  # 낱말 Jaccard {user, id} ∩ {id} / {user, id}
    assert jk.name_score("customer_id", "user_id") == pytest.approx(1 / 3)
    assert jk.name_score("amt", "region") == 0.0 and jk.name_score("", "id") == 0.0
    assert jk.words("customerID_2") == ["customer", "id", "2"]


def test_score_is_the_weighted_sum_and_ambiguous_picks_top_three(db):
    found = pair(db, "orders", "users")
    assert found["decision"] == "ask" and found["basis"] == "표본 기준"
    keys = [c["keys"] for c in found["candidates"]]
    assert keys == [[["user_id", "id"]], [["region", "region"]], [["region", "id"]]]
    first, second, third = found["candidates"]
    # user_id/id: 이름 0.9, A 값 {0..4}가 모두 B.id에 있어 겹침 1, 타입 1, B.id가 고유해 1 → 0.35·0.9 + 0.45 + 0.10 + 0.10
    assert (first["name"], first["contain"], first["type"], first["unique"], first["score"]) == (0.9, 1.0, 1.0, True, 0.965)
    # region/region: 이름 1, 겹침 1, 타입 1, 양쪽 모두 고유하지 않아 0 → 0.35 + 0.45 + 0.10
    assert (second["name"], second["contain"], second["unique"], second["score"]) == (1.0, 1.0, False, 0.9)
    # region/id: 이름 0, 겹침 1, 타입 1, B.id가 고유 → 0.45 + 0.10 + 0.10
    assert (third["name"], third["contain"], third["unique"], third["score"]) == (0.0, 1.0, True, 0.65)
    assert first["score"] - second["score"] < jk.DECIDE_GAP  # 1등과 2등 차이가 0.15보다 작아 고르게 한다
    assert first["reasons"] == ["이름이 ‘테이블이름+id’ ↔ ‘id’ 모양이에요", "A 값의 100%가 B에 있어요(표본 기준)", "한쪽 컬럼 값이 모두 달라요 (표본 기준)"]


def test_clear_winner_is_chosen_automatically(db):
    found = pair(db, "orders", "users_slim", "orders", "users")
    assert found["decision"] == "auto" and len(found["candidates"]) == 1
    best = found["candidates"][0]
    assert best["keys"] == [["user_id", "id"]] and best["score"] == 0.965
    assert best["score"] >= jk.DECIDE_MIN


def test_text_to_number_pairs_are_marked_as_needing_a_cast(db):
    found = pair(db, "orders", "users_txt", "orders", "users")
    best = found["candidates"][0]
    assert best["keys"] == [["user_id", "id"]] and best["cast"] is True
    assert best["contain"] == 1.0 and best["type"] == 0.3  # 글자로 바꿔 견주면 A 값이 모두 있다
    assert "타입이 달라 한쪽을 바꿔(cast) 이어야 해요" in best["reasons"]
    assert best["score"] == pytest.approx(0.35 * 0.9 + 0.45 + 0.10 * 0.3 + 0.10)


def test_no_candidate_offers_date_alignment(db):
    found = pair(db, "t1", "t2")
    assert found["decision"] == "none" and found["candidates"] == []
    assert found["date"] == {"a": "d", "b": "e", "units": ["day", "week", "month"], "default": "day", "text": "‘d’과 ‘e’을 일·주·월 단위로 맞춰 이을 수 있어요."}
    assert pair(db, "orders", "users_txt", "orders", "users")["date"] is None  # 후보가 있으면 날짜 맞춤은 내놓지 않는다


def test_composite_key_candidate_when_single_keys_overlap_little(db):
    found = pair(db, "grid", "one")
    assert found["decision"] == "ask"
    composite = [c for c in found["candidates"] if len(c["keys"]) == 2]
    assert composite and composite[0]["keys"] == [["x", "x"], ["y", "y"]]
    # 단일 키 x/x의 겹침: A의 x {0,1,2,3} 중 B에 있는 것은 {0} → 0.25
    single = next(c for c in found["candidates"] if c["keys"] == [["x", "x"]])
    assert single["contain"] == 0.25 and single["name"] == 1.0
    assert composite[0]["contain"] == 0.0625  # 값 쌍 16개 중 (0, 0) 하나


def test_full_check_uses_all_values(db):
    found = pair(db, "orders", "users_slim", "orders", "users", full=True)
    assert found["basis"] == "전체" and found["candidates"][0]["basis"] == "전체" and found["candidates"][0]["contain"] == 1.0
    assert found["note"] == ""
    sample = pair(db, "orders", "users_slim", "orders", "users")
    assert sample["note"] == "표본 기준이에요. [전체로 확인]을 누르면 전체 값으로 다시 재요."


def test_remembered_keys_follow_the_schema_fingerprint(db, folder):
    a, b = db.table("orders"), db.table("users")
    fp = jk.fingerprint(a, b)
    assert jk.recall("file:x", "orders", "users", fp) is None
    jk.remember("file:x", "orders", "users", fp, [("user_id", "id")])
    assert jk.recall("file:x", "orders", "users", fp) == [["user_id", "id"]]
    assert jk.recall("file:y", "orders", "users", fp) is None  # 다른 연결
    assert jk.fingerprint(a, db.table("users_slim")) != fp  # 스키마(컬럼)가 다르면 지문이 다르다
    assert jk.recall("file:x", "orders", "users", jk.fingerprint(a, db.table("users_slim"))) is None
    found = jk.suggest(a, b, "orders", "users", recalled=jk.recall("file:x", "orders", "users", fp))
    assert found["candidates"][0]["remembered"] and found["candidates"][0]["keys"] == [["user_id", "id"]] and found["candidates"][0]["basis"] == "기억"
    jk.forget("file:x", "orders", "users", fp)
    assert jk.recall("file:x", "orders", "users", fp) is None
    settings_text = (folder / jk.FILE).read_text(encoding="utf-8")
    assert "password" not in settings_text and "file:x" not in settings_text.replace('"file:x"', "")  # 열쇠는 연결 열쇠뿐(계정·비밀번호 없음)


# ---------- 이은 뒤 부풂 ----------

def test_fanout_is_reported_before_the_result(db):
    inflated = jk.fanout(db.table("lk"), db.table("rk"), [("k", "k")], "inner", "rk")
    # lk 3행(k=1이 둘, k=2가 하나)과 rk(k=1이 둘, k=2가 둘): 2×2 + 1×2 = 6행
    assert inflated["checked"] and (inflated["left_rows"], inflated["right_rows"], inflated["joined_rows"]) == (3, 4, 6)
    assert inflated["inflated"] and inflated["level"] == "bad" and inflated["right_unique"] is False and inflated["left_unique"] is False
    assert inflated["text"] == "오른쪽 키가 겹쳐서 이으면 행이 3행에서 6행으로 늘어요. 이대로 합계를 내면 값이 부풀어요."
    clean = jk.fanout(db.table("lk"), db.table("rk1"), [("k", "k")], "left", "rk1")
    assert clean["right_unique"] and not clean["inflated"] and clean["level"] is None and clean["joined_rows"] == 3 and clean["text"] == ""


def test_fanout_on_remote_needs_confirmation_and_sends_nothing(db, monkeypatch):
    sent = spy(monkeypatch)
    skipped = jk.fanout(db.table("lk"), db.table("rk"), [("k", "k")], "inner", "rk", remote=True)
    assert skipped["checked"] is False and skipped["note"].startswith("원격 DB라 이은 뒤 행 수를 미리 세지 않았어요") and sent == []
    sure = jk.fanout(db.table("lk"), db.table("rk"), [("k", "k")], "inner", "rk", remote=True, scan_ok=True)
    assert sure["checked"] and sure["joined_rows"] == 6 and sent  # 확인했을 때만 센다


# ---------- 원격 DB ----------

def spy(monkeypatch):
    return spy_queries(monkeypatch)


def test_remote_without_samples_sends_nothing_and_relies_on_name_and_type(db, monkeypatch):
    sent = spy(monkeypatch)
    found = pair(db, "orders", "users", remote=True)
    assert sent == []  # COUNT도 isin 조회도 표본 조회도 보내지 않았다
    assert found["basis"] == "표본 없음" and "원격 DB라 값을 읽는 조회를 새로 보내지 않았어요" in found["note"]
    assert all(c["contain"] is None and c["basis"] == "표본 없음" for c in found["candidates"])
    assert "값 겹침은 표본이 없어 보지 못했어요(이름·타입으로만 봤어요)" in found["candidates"][0]["reasons"]
    # 이름·타입으로만: region/region은 이름 1·타입 1 → (0.35 + 0.10) / 0.45 = 1.0, user_id/id는 이름 0.9 → (0.315 + 0.10) / 0.45
    scores = {tuple(map(tuple, c["keys"])): c["score"] for c in found["candidates"]}
    assert scores[(("region", "region"),)] == 1.0 and scores[(("user_id", "id"),)] == pytest.approx((0.35 * 0.9 + 0.10) / 0.45, abs=1e-4)


def test_remote_with_existing_samples_uses_them_without_new_queries(db, monkeypatch):
    sent = spy(monkeypatch)
    samples = {"a": pl.DataFrame({"user_id": [0, 1, 2, 3, 4, 0], "amt": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}), "b": pl.DataFrame({"id": [0, 1, 2, 3], "name": ["a", "b", "c", "d"]})}
    found = jk.suggest(ibis.table({"user_id": "int32", "amt": "float64"}, name="orders"), ibis.table({"id": "int32", "name": "string"}, name="users"), "orders", "users",
                       remote=True, samples=samples)
    assert sent == []
    best = found["candidates"][0]
    assert best["keys"] == [["user_id", "id"]] and best["contain"] == 0.8 and best["basis"] == "표본 기준"  # A의 서로 다른 값 5개 중 4개가 B 표본에 있다
    assert best["unique"] is True  # B.id는 표본에서 모두 다르다
