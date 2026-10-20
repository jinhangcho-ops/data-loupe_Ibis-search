"""실행하기 전에 "이 조회가 처리할 용량"을 알린다. 데이터는 읽지 않는다.
- BigQuery: dry run(요금 없음)의 totalBytesProcessed. 정확한 값(과금 기준).
- 파일(Parquet): 파일 정보(RowGroup)에서 읽는 컬럼의 압축 전 크기 합. 정확에 가까움(필터로 건너뛰는 RowGroup은 빼지 않는다).
- 파일(CSV·JSON·Excel): 파일 크기(최대치).
- DuckDB 테이블: 테이블의 추정 행 수 × 읽는 컬럼의 타입 크기(글자는 STRING_BYTES로 가정). 추정.
- Postgres: 통계(pg_class.reltuples × pg_stats.avg_width)로 읽는 컬럼만. 통계가 없으면 모름.
- 그 밖의 DB: 모름(None).
읽는 테이블·컬럼은 SQL을 sqlglot으로 읽어 고른다(SELECT *·테이블.*는 그 테이블의 모든 컬럼). 같은 이름의 컬럼은 그 이름을 가진 모든 테이블에서 읽는다고 본다."""
import os
from pathlib import Path

import source
import sql_exec

STRING_BYTES = 16  # 글자 컬럼 한 칸을 이만큼으로 본다(DuckDB 추정)
TYPE_BYTES = {"bool": 1, "int8": 1, "int16": 2, "int32": 4, "int64": 8, "uint8": 1, "uint16": 2, "uint32": 4, "uint64": 8,
              "float32": 4, "float64": 8, "date": 4, "time": 8, "timestamp": 8, "decimal": 16, "uuid": 16, "interval": 16}


def referenced(sql, dialect):
    """SQL이 읽는 (테이블 이름들, 컬럼 이름들 또는 None(모든 컬럼)). 가상 테이블은 이미 WITH로 풀린 SQL을 받는다."""
    exp = sql_exec.glot()[1]
    tree = sql_exec.check(sql, dialect)
    tables = sql_exec.table_names(tree)
    star = any(isinstance(s, exp.Star) for s in tree.find_all(exp.Star))
    columns = None if star else {c.name.lower() for c in tree.find_all(exp.Column) if c.name}
    return list(dict.fromkeys(tables)), columns


def parquet_bytes(path, columns):
    """Parquet 파일에서 그 컬럼들(None이면 모두)의 압축 전 크기 합."""
    import pyarrow.parquet as pq
    meta = pq.read_metadata(path)
    total = 0
    for g in range(meta.num_row_groups):
        group = meta.row_group(g)
        for i in range(group.num_columns):
            col = group.column(i)
            top = col.path_in_schema.split(".")[0].lower()
            if columns is None or top in columns:
                total += col.total_uncompressed_size
    return total


def file_tables(opened):
    """파일 연결이면 {테이블 이름(소문자): 파일 경로}. 아니면 {}."""
    if not opened or "files" not in opened:
        return {}
    chosen = sorted(Path(f) for f in opened["files"] if Path(f).is_file() and source.kind_of(f) not in (None, "duckdb"))
    return {n.lower(): p for p, n in zip(chosen, source.table_names(chosen))}


def type_bytes(dtype):
    if dtype.is_string() or dtype.is_binary():
        return STRING_BYTES
    name = str(dtype).lstrip("!").split("(")[0].lower()
    return TYPE_BYTES.get(name, 8)


def duckdb_bytes(con, table, columns):
    """DuckDB 테이블: 추정 행 수 × 읽는 컬럼의 타입 크기."""
    rows = con.raw_sql(f"select estimated_size from duckdb_tables() where lower(table_name) = lower('{table.replace(chr(39), chr(39) * 2)}')").fetchone()
    if not rows:
        return None
    schema = con.table(table).schema()
    width = sum(type_bytes(t) for n, t in schema.items() if columns is None or n.lower() in columns)
    return int(rows[0] or 0) * width


def postgres_bytes(con, table, columns):
    """Postgres: 통계의 행 수 × 읽는 컬럼의 평균 크기. 통계가 없으면 None."""
    with con.con.cursor() as cur:  # Ibis Postgres 백엔드의 psycopg 연결(값은 매개변수로 넘긴다)
        cur.execute("select reltuples from pg_class where lower(relname) = lower(%s) order by relkind limit 1", (table,))
        found = cur.fetchone()
        if not found or found[0] is None or found[0] < 0:
            return None
        cur.execute("select attname, avg_width from pg_stats where lower(tablename) = lower(%s)", (table,))
        widths = {n.lower(): w for n, w in cur.fetchall()}
    if not widths:
        return None
    return int(found[0] * sum(w for n, w in widths.items() if columns is None or n in columns))


def bigquery_bytes(con, sql):
    from google.cloud import bigquery
    job = con.client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    return int(job.total_bytes_processed or 0)


def estimate(con, sql, opened=None, dialect=None):
    """{"bytes": 수 또는 None, "exact": 정확한지, "how": 어떻게 셌는지}. 셀 수 없으면 bytes는 None. 실행하지 않는다.
    dialect를 주면 sql을 그 방언으로 읽는다(Polars 연결의 탐색 식은 duckdb 방언 SQL로 만든다)."""
    name = getattr(con, "name", "")
    dialect = dialect or sql_exec.dialect_of(con)
    if name == "bigquery":
        return {"bytes": bigquery_bytes(con, sql), "exact": True, "how": "BigQuery dry run"}
    tables, columns = referenced(sql, dialect)
    files = file_tables(opened)
    total, exact, hows = 0, True, set()
    for table in tables:
        path = files.get(table.lower())
        if path is not None:
            if path.suffix.lower() == ".parquet":
                total += parquet_bytes(path, columns)
                hows.add("Parquet 파일 정보")
            else:
                total += os.path.getsize(path)
                exact = False
                hows.add("파일 크기(최대)")
            continue
        if name == "duckdb":
            size = duckdb_bytes(con, table, columns)
            hows.add("DuckDB 행 수 × 컬럼 크기")
        elif name == "postgres":
            size = postgres_bytes(con, table, columns)
            hows.add("Postgres 통계")
        else:
            size = None
        if size is None:
            return {"bytes": None, "exact": False, "how": "이 연결은 미리 알 수 없음"}
        total += size
        exact = False
    return {"bytes": total, "exact": exact and bool(tables), "how": ", ".join(sorted(hows)) or "읽는 테이블 없음"}


def safe(work):
    """estimate를 돌리되 실패하면 그 이유로 '알 수 없음'(처리 용량은 참고라 검사·실행을 막지 않는다)."""
    try:
        return work()
    except Exception as e:
        return {"bytes": None, "exact": False, "how": (str(e).strip().splitlines() or [type(e).__name__])[0][:120]}


def size_text(n):
    """바이트를 B·KB·MB·GB·TB 글자로."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024


def message(found):
    """화면에 보일 글: '실행하면 약 1.2 GB를 처리합니다(추정 · 방법)'."""
    if found["bytes"] is None:
        return f"처리 용량: 알 수 없음({found['how']})"
    return f"실행하면 {'' if found['exact'] else '약 '}{size_text(found['bytes'])}를 처리합니다({'정확' if found['exact'] else '추정'} · {found['how']})"
