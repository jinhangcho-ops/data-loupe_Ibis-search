"""실행 계획 보기: 프로그램이 만든 SELECT(검사를 통과한 것)에 EXPLAIN을 붙여, DB 옵티마이저가 인덱스를 타는지 테이블 전체를 읽는지 본다.
EXPLAIN ANALYZE는 조회를 실제로 돌리므로 쓰지 않는다. 사용자가 SQL 탭에 쓴 EXPLAIN은 지금처럼 막는다(sql_exec).
실행 계획을 보는 문장과 그 모양은 DB마다 달라 방언(sqlglot 이름)마다 표로 둔다. 모르는 방언은 EXPLAIN을 해 보고 글만 보인다.
데이터를 읽는 함수는 server.run으로 연결 스레드에서 부른다."""
import re

from core import sql_exec

# 방언: 문장 머리. 조회 SQL 앞에 붙인다(조회는 Ibis가 만든 것이고 사용자 글자를 넣지 않는다).
HEADS = {"sqlite": "EXPLAIN QUERY PLAN ", "duckdb": "EXPLAIN ", "postgres": "EXPLAIN ", "mysql": "EXPLAIN ", "singlestore": "EXPLAIN ",
         "snowflake": "EXPLAIN ", "trino": "EXPLAIN ", "databricks": "EXPLAIN ", "spark": "EXPLAIN ", "clickhouse": "EXPLAIN ",
         "materialize": "EXPLAIN ", "risingwave": "EXPLAIN ", "athena": "EXPLAIN ", "pyspark": "EXPLAIN ", "impala": "EXPLAIN ", "hive": "EXPLAIN ",
         "exasol": None, "druid": None, "flink": None}
# 보지 않는 방언과 까닭(읽기만 한다는 원칙, 또는 실행 계획이 없는 DB)
REFUSED = {
    "oracle": "Oracle은 실행 계획을 보려면 PLAN_TABLE에 써야 해서 이 프로그램은 보지 않습니다(읽기만 한다는 원칙). DB 도구의 '실행 계획'으로 확인하세요.",
    "mssql": "MSSQL은 실행 계획을 보려면 연결 설정(SET SHOWPLAN)을 바꿔야 해서 이 프로그램은 보지 않습니다. SSMS의 '예상 실행 계획 표시'를 쓰세요.",
    "tsql": "MSSQL은 실행 계획을 보려면 연결 설정(SET SHOWPLAN)을 바꿔야 해서 이 프로그램은 보지 않습니다. SSMS의 '예상 실행 계획 표시'를 쓰세요.",
    "bigquery": "BigQuery는 인덱스가 없어 실행 계획 대신 [처리 용량](요금 없는 dry run)으로 읽을 양을 봅니다. 파티션·클러스터 컬럼으로 조건을 걸면 줄어듭니다.",
}
NOTES = {
    "duckdb": "DuckDB는 인덱스 대신 블록마다 적어 둔 최솟값·최댓값으로 읽지 않아도 되는 부분을 건너뜁니다. 계획의 SEQ_SCAN이 곧 전체 읽기라는 뜻은 아닙니다.",
    "sqlite": "SCAN 테이블은 전체 읽기, SEARCH … USING INDEX는 인덱스로 찾기, SCAN … USING INDEX는 인덱스 순서로 읽기입니다.",
    "postgres": "Seq Scan은 전체 읽기, Index Scan·Index Only Scan·Bitmap Index Scan은 인덱스로 찾기입니다. 작은 테이블은 Seq Scan이 더 빠를 수 있습니다.",
    "mysql": "type이 ALL이면 전체 읽기, ref·range·eq_ref·const는 인덱스로 찾기입니다.",
}


def name_of(dialect):
    """sqlglot 방언(클래스·인스턴스·이름)의 소문자 이름."""
    if isinstance(dialect, str):
        return dialect.lower()
    return (getattr(dialect, "__name__", None) or type(dialect).__name__).lower()


def tables_of(sql, dialect):
    """SQL의 별칭 → 테이블 이름(실행 계획에는 별칭이 나온다)."""
    sqlglot, exp = sql_exec.glot()[:2]  # sqlglot은 처음 쓸 때 올린다
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return {}
    return {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}


def full_scans(name, columns, rows, text, aliases):
    """전체 읽기한 테이블 이름들. 알아볼 수 없는 방언이면 None."""
    if name == "sqlite":
        found = re.findall(r"^SCAN ([^\s(]\S*)$", text, re.M)  # SCAN t0(인덱스 없이). (subquery-n)은 하위 결과라 뺀다
    elif name in ("postgres", "materialize", "risingwave"):
        found = re.findall(r"Seq Scan on (\S+)", text)
    elif name in ("mysql", "singlestore") and "type" in columns and "table" in columns:
        kind, table = columns.index("type"), columns.index("table")
        found = [str(r[table]) for r in rows if str(r[kind]).upper() == "ALL"]
    else:
        return None
    return sorted({aliases.get(t, t) for t in found})


def explain(con, sql, dialect):
    """sql(Ibis가 만든 SELECT)의 실행 계획: {text, full_scans(전체 읽기한 테이블, 알아볼 수 없으면 None), note}. 보지 않는 방언이면 ValueError."""
    name = name_of(dialect)
    if name in REFUSED:
        raise ValueError(REFUSED[name])
    head = HEADS.get(name, "EXPLAIN ")
    if head is None:
        raise ValueError("이 DB의 실행 계획은 이 프로그램에서 볼 수 없습니다. DB 도구에서 확인하세요.")
    cursor = con.raw_sql(head + sql)
    try:
        columns = [d[0] for d in getattr(cursor, "description", None) or []]
        rows = cursor.fetchall()
    finally:
        close = getattr(cursor, "close", None)
        if close and cursor is not getattr(con, "con", None):  # DuckDB의 raw_sql은 연결 그 자체를 돌려준다(닫으면 연결이 끊긴다)
            close()
    text = "\n".join(str(r[-1]) if name == "sqlite" else "\t".join("" if v is None else str(v) for v in r) for r in rows)
    if columns and name not in ("sqlite", "postgres", "duckdb"):
        text = "\t".join(columns) + "\n" + text
    return {"text": text, "full_scans": full_scans(name, columns, rows, text, tables_of(sql, dialect)), "note": NOTES.get(name, "")}
