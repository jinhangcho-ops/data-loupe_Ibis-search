"""명세 5번 NL2SQL: 문맥(명세서·캐시 추론·지표, 데이터 값 없음) → 시스템 프롬프트 → LLM(가짜) → SafeIbisSQLExecutor 검사.
LLM은 부르지 않는다(스텁과 가짜만). 데이터·명세서·지표 파일은 테스트 안에서 지어낸다."""

import duckdb
import polars as pl
import pytest
from lazy import ibis

from query import nl2sql
from integrations.adapters import llm
from core.sql_exec import SecurityViolationError, SqlSyntaxError

ORDERS = pl.DataFrame({"day": ["2026-01-01", "2026-01-02", "2026-01-02"], "status": ["secret-flag-a", "secret-flag-b", "secret-flag-a"], "amount": [100, 98765, 300]})


class Fake:
    name = "가짜"

    def __init__(self, reply):
        self.reply, self.seen = reply, None

    def complete(self, system, question):
        self.seen = (system, question)
        return self.reply


@pytest.fixture
def shop(client, tmp_path, folder):
    from core import profiler
    from core import server
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.register("df", ORDERS.to_arrow())
        raw.execute("create table orders as select * from df")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    server.forget_tables()  # 앱의 switch처럼, 앞 시험의 연결에서 기억한 테이블 목록을 비운다
    (tmp_path / "spec.yaml").write_text("tables:\n  orders:\n    description: 주문 내역\n    partition: {column: day, recent_days: 7}\n"
                                        "    columns:\n      amount: {description: 결제 금액(원)}\n", encoding="utf-8")
    profiler.save_spec_path(str(tmp_path / "spec.yaml"))
    (tmp_path / "m.yaml").write_text("metrics:\n  객단가:\n    numerator: {agg: sum, column: amount}\n    denominator: {agg: count}\n", encoding="utf-8")
    client.post("/api/metrics", json={"path": str(tmp_path / "m.yaml")})
    # 캐시된 추론: 플래그 값과 최소·최대(데이터 값)가 들어 있어도 프롬프트에는 나가지 않아야 한다
    key = "|".join([server.connection_key(), "", "orders"])
    profiler.remember(key, 5000, profile={"columns": [
        {"name": "status", "infer": {"null_ratio": 0.0, "unique": 2, "constant": False, "flags": ["secret-flag-a", "secret-flag-b"]}},
        {"name": "day", "infer": {"null_ratio": 0.25, "date_format": "YYYY-MM-DD", "exact": {"min": "2026-01-01", "max": "2026-01-02"}}},
        {"name": "amount", "infer": {"null_ratio": 0.0, "exact": {"min": 100, "max": 98765}}}]}, sampled={})
    return server


def test_prompt_has_context_without_values(client, shop):
    d = client.post("/api/nl2sql/prompt", json={"question": "매출 합계"}).get_json()
    system = d["system"]
    assert d["llm"] == "없음" and d["tables"] == 1 and d["metrics"] == 1 and d["question"] == "매출 합계"
    for want in ("### orders — 주문 내역 (파티션 컬럼: day)", "- amount int64 — 결제 금액(원)", "날짜 형식 YYYY-MM-DD", "빈 값 25%",
                 "값 종류가 적은 구분 컬럼", "- 객단가: 합계(amount) ÷ 개수", "SELECT 문 하나", "```sql", "너는 DuckDB SQL을"):
        assert want in system, want
    for leaked in ("secret-flag", "98765", "2026-01-01"):  # 플래그 값·최소·최대·표본 값
        assert leaked not in system, leaked


def test_stub_and_ask(client, shop):
    r = client.post("/api/nl2sql/ask", json={"question": "매출 합계"})
    assert r.status_code == 400 and "LLM이 설정되어 있지 않습니다" in r.get_json()["error"]
    ctx, known = nl2sql.context(shop.state["con"], shop.connection_key()), ["orders"]
    fake = Fake("설명입니다.\n```sql\nselect sum(amount) as 매출 from orders\n```")
    assert nl2sql.ask(" 매출 합계 ", ctx, "duckdb", known, fake) == "select sum(amount) as 매출 from orders"
    assert fake.seen[1] == "매출 합계" and "### orders" in fake.seen[0]
    for reply, error in [("```sql\ndelete from orders\n```", SecurityViolationError), ("select 1; drop table orders", SecurityViolationError),
                         ("select * from orders where (", SqlSyntaxError), ("select * from customers", ValueError), ("", ValueError)]:
        with pytest.raises(error):
            nl2sql.ask("q", ctx, "duckdb", known, Fake(reply))
    with pytest.raises(ValueError, match="만들지 못했습니다: 기간 컬럼이 없습니다"):
        nl2sql.ask("q", ctx, "duckdb", known, Fake("```sql\n-- 모름: 기간 컬럼이 없습니다\n```"))
    with pytest.raises(ValueError, match="질문을 입력"):
        nl2sql.ask("  ", ctx, "duckdb", known, fake)


def test_route_uses_active_client(client, shop, monkeypatch):
    fake = Fake("```sql\nselect status, count(*) from orders group by status\n```")
    monkeypatch.setattr(llm, "client", lambda: fake)
    assert client.post("/api/nl2sql/ask", json={"question": "상태별 건수"}).get_json() == {"sql": "select status, count(*) from orders group by status"}
    assert "secret-flag" not in fake.seen[0]
