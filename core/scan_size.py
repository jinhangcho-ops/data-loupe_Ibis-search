"""실행하기 전에 "이 조회가 처리할 용량"을 알린다. 데이터는 읽지 않는다.
- BigQuery: dry run(요금 없음)의 totalBytesProcessed. 정확한 값(과금 기준).
- 파일(Parquet): 파일 정보(RowGroup)에서 읽는 컬럼의 압축 전 크기 합. 정확에 가까움(필터로 건너뛰는 RowGroup은 빼지 않는다).
- 파일(CSV·JSON·Excel): 파일 크기(최대치).
- DuckDB 테이블: 테이블의 추정 행 수 × 읽는 컬럼의 타입 크기(글자는 STRING_BYTES로 가정). 추정.
- Postgres: 통계(pg_class.reltuples × pg_stats.avg_width)로 읽는 컬럼만. 통계가 없으면 모름.
- 그 밖의 DB: 모름(None).
실행 전 확인(limit_check): 센 용량이 메모리 한도(설정 memory, 제한 없으면 지금 PC의 사용 가능 메모리)를 넘으면 알린다.
읽는 테이블·컬럼은 SQL을 sqlglot으로 읽어 고른다(SELECT *·테이블.*는 그 테이블의 모든 컬럼). 같은 이름의 컬럼은 그 이름을 가진 모든 테이블에서 읽는다고 본다."""
import datetime as dt
import math
import os
import re
from pathlib import Path

from lazy import ibis

from core import held
from core import monitor
from core import settings
from core import source
from core import sql_exec

STRING_BYTES = 16  # 글자 컬럼 한 칸을 이만큼으로 본다(DuckDB 추정)
TYPE_BYTES = {"bool": 1, "int8": 1, "int16": 2, "int32": 4, "int64": 8, "uint8": 1, "uint16": 2, "uint32": 4, "uint64": 8,
              "float32": 4, "float64": 8, "date": 4, "time": 8, "timestamp": 8, "decimal": 16, "uuid": 16, "interval": 16}


def referenced(sql, dialect):
    """SQL이 읽는 (테이블 이름들, 컬럼 이름들 또는 None(모든 컬럼)). 가상 테이블은 이미 WITH로 풀린 SQL을 받는다."""
    exp = sql_exec.glot()[1]
    tree = sql_exec.glot()[0].parse_one(sql, read=dialect)  # 읽기만 하고 실행하지 않는다(앱이 만든 read_parquet도 막지 않게 check 대신)
    tables = sql_exec.table_names(tree)
    star = any(not isinstance(s.parent, exp.Count) for s in tree.find_all(exp.Star))  # COUNT(*)의 *는 컬럼을 읽지 않는다
    columns = None if star else {c.name.lower() for c in tree.find_all(exp.Column) if c.name}
    return list(dict.fromkeys(tables)), columns


def qualified(tree):
    """스키마가 붙은 테이블들 [(catalog 또는 None, 스키마, 이름)](중복 없이). referenced의 table_names는 가상 테이블 풀기용이라 이것을 뺀다."""
    exp = sql_exec.glot()[1]
    return list(dict.fromkeys((t.args["catalog"].name if t.args.get("catalog") else None, t.args["db"].name, t.name) for t in tree.find_all(exp.Table) if t.args.get("db") and t.name))


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


def duckdb_bytes(con, table, columns, schema=None, catalog=None):
    """DuckDB 테이블(schema·catalog를 주면 그 스키마·DB의 것): 추정 행 수 × 읽는 컬럼의 타입 크기."""
    q = lambda s: s.replace("'", "''")
    where = (f" and lower(database_name) = lower('{q(catalog)}') and lower(schema_name) = lower('{q(schema)}')" if catalog
             else f" and (lower(schema_name) = lower('{q(schema)}') or (lower(database_name) = lower('{q(schema)}') and schema_name = 'main'))" if schema else "")  # o.big: 붙인 DB o의 main
    rows = con.raw_sql(f"select estimated_size, database_name, schema_name from duckdb_tables() where lower(table_name) = lower('{q(table)}'){where}"
                       + (f" order by lower(schema_name) = lower('{q(schema)}') desc" if schema else "")).fetchone()
    if not rows:
        return None
    schema = con.table(table, database=(rows[1], rows[2])).schema() if schema else con.table(table).schema()
    width = sum(type_bytes(t) for n, t in schema.items() if columns is None or n.lower() in columns)
    return int(rows[0] or 0) * width


def postgres_bytes(con, table, columns, schema=None):
    """Postgres: 통계의 행 수 × 읽는 컬럼의 평균 크기(schema를 주면 그 스키마의 것). 통계가 없으면 None."""
    within = (" and relnamespace in (select oid from pg_namespace where lower(nspname) = lower(%s))", " and lower(schemaname) = lower(%s)") if schema else ("", "")  # regnamespace::text는 "Sales"처럼 따옴표가 붙는다
    extra = (schema,) if schema else ()
    with con.con.cursor() as cur:  # Ibis Postgres 백엔드의 psycopg 연결(값은 매개변수로 넘긴다)
        cur.execute(f"select reltuples from pg_class where lower(relname) = lower(%s){within[0]} order by relkind limit 1", (table, *extra))
        found = cur.fetchone()
        if not found or found[0] is None or found[0] < 0:
            return None
        cur.execute(f"select attname, avg_width from pg_stats where lower(tablename) = lower(%s){within[1]}", (table, *extra))
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
    exp = sql_exec.glot()[1]
    tree = sql_exec.glot()[0].parse_one(sql, read=dialect)
    paths = [n.expressions[0].name for n in tree.find_all(exp.ReadParquet) if n.expressions and n.expressions[0].is_string]  # Parquet 가상 테이블이 풀린 read_parquet('경로')
    schemed = qualified(tree)
    for path in paths:
        total += parquet_bytes(path, columns)
        hows.add("Parquet 파일 정보")
    for catalog, schema, table in [(None, None, t) for t in tables] + schemed:
        path = None if schema else files.get(table.lower())
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
            size = duckdb_bytes(con, table, columns, schema, catalog)
            hows.add("DuckDB 행 수 × 컬럼 크기")
        elif name == "postgres":
            size = postgres_bytes(con, table, columns, schema)
            hows.add("Postgres 통계")
        else:
            size = None
        if size is None:
            return {"bytes": None, "exact": False, "how": "이 연결은 미리 알 수 없음"}
        total += size
        exact = False
    return {"bytes": total, "exact": exact and bool(tables or paths or schemed), "how": ", ".join(sorted(hows)) or "읽는 테이블 없음"}


def reason(e):
    """오류의 첫 줄(120자까지). 비어 있으면 오류 종류 이름."""
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:120]


def blocks_unknown(config):
    """처리 용량을 알 수 없으면 실행하지 않도록 설정했는지(scan_unknown이 block)."""
    return config.get("scan_unknown") == "block"


def files_dialect(con):
    """파일 연결(Polars)은 SQL을 쓰지 않으므로 처리 용량 계산에는 duckdb 방언으로 읽는다. 그 밖에는 None(연결의 방언)."""
    return "duckdb" if getattr(con, "name", "") == "polars" else None


def sql_of(con, expr):
    """(SQL 글, 방언 또는 None): 처리 용량을 셀 때 읽을 Ibis 식의 SQL."""
    dialect = files_dialect(con)
    return str(ibis.to_sql(expr, dialect=dialect) if dialect else ibis.to_sql(expr)), dialect


def safe(work):
    """estimate를 돌리되 실패하면 그 이유로 '알 수 없음'(처리 용량은 참고라 검사·실행을 막지 않는다)."""
    try:
        return work()
    except Exception as e:
        return {"bytes": None, "exact": False, "how": reason(e)}


def size_text(n):
    """바이트를 B·KB·MB·GB·TB 글자로."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024


def message(found):
    """화면에 보일 글: '실행하면 약 1.2 GB를 처리해요(추정 · 방법)'."""
    if found["bytes"] is None:
        return f"처리 용량: 알 수 없음({found['how']})"
    return f"실행하면 {'' if found['exact'] else '약 '}{size_text(found['bytes'])}를 처리해요({'정확' if found['exact'] else '추정'} · {found['how']})"


WIDE_STRING = 32  # 글자 컬럼 한 칸을 이만큼으로 본다(메타데이터도 명세서도 없을 때. 원격 DB는 표본을 읽지 않는다)
NESTED_BYTES = 64  # 구조·목록·맵·JSON 컬럼 한 칸
ROW_PATTERNS = (r"rows=(\d+)", r"Estimates: \{rows: ([\d,]+)", r"~\s*([\d,]+)\s+rows", r"EC:\s*(\d+)")  # 실행 계획 글에서 행 수 추정을 찾는 모양(앞에서부터)
DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(?:MAKE_DATE|DATE)\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)", re.I)
FLIP = {"GT": "LT", "GTE": "LTE", "LT": "GT", "LTE": "GTE", "EQ": "EQ"}  # 값이 왼쪽에 있는 비교를 컬럼이 왼쪽인 모양으로


def default_width(dtype):
    """타입만으로 어림한 한 칸의 크기: 숫자·날짜·불리언은 타입 크기, 글자는 WIDE_STRING, 구조·목록·맵·JSON은 NESTED_BYTES."""
    if dtype.is_string() or dtype.is_binary():
        return WIDE_STRING
    if dtype.is_struct() or dtype.is_array() or dtype.is_map() or dtype.is_json():
        return NESTED_BYTES
    return TYPE_BYTES.get(str(dtype).lstrip("!").split("(")[0].lower(), 8)


def is_aggregate(sql, dialect):
    """맨 바깥 SELECT가 집계(GROUP BY가 있거나 고르는 값이 모두 집계 함수)인지. 집계 결과는 작아서 원격 DB에서는 세지 않는다."""
    try:
        sqlglot, exp = sql_exec.glot()[:2]
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return False
    if not isinstance(tree, exp.Select):
        return False
    if tree.args.get("group"):
        return True
    return bool(tree.expressions) and all(p.find(exp.AggFunc) is not None and p.find(exp.Window) is None for p in tree.expressions)


def spec_widths(tables):
    """데이터 명세서에 적힌 컬럼별 크기 {컬럼(소문자): 바이트}. 명세서 컬럼에 선택 항목 bytes(양의 정수)를 적으면 쓴다. 없으면 {}."""
    from core import profiler
    found = {}
    for table in tables:
        for column, info in (profiler.spec_table(None, table).get("columns") or {}).items():
            if isinstance(info, dict) and type(info.get("bytes")) is int and info["bytes"] > 0:
                found[column.lower()] = info["bytes"]
    return found


def stat_widths(con, tables):
    """DB의 통계로 컬럼마다 평균 크기 {컬럼(소문자): 바이트}(데이터를 읽지 않는다). Postgres는 pg_stats의 avg_width. 없거나 못 읽으면 {}."""
    if getattr(con, "name", "") != "postgres" or len(tables) != 1:
        return {}
    try:
        with con.con.cursor() as cur:
            cur.execute("select attname, avg_width from pg_stats where lower(tablename) = lower(%s)", (tables[0],))
            return {n.lower(): w for n, w in cur.fetchall() if w}
    except Exception:
        return {}


def table_row_bytes(con, tables):
    """테이블 한 행의 평균 크기: BigQuery 테이블 정보의 num_bytes / num_rows(데이터를 읽지 않는다). 모르면 None."""
    client = getattr(con, "client", None)
    if getattr(con, "name", "") != "bigquery" or client is None or len(tables) != 1:
        return None
    try:
        info = client.get_table(bigquery_name(con, tables[0]))
        return info.num_bytes / info.num_rows if info.num_rows else None
    except Exception:
        return None


def bigquery_name(con, table):
    """프로젝트.데이터셋.테이블(알 수 있는 앞부분만)."""
    return ".".join(p for p in (getattr(con, "current_catalog", None), getattr(con, "current_database", None), table) if p)


def row_width(con, expr, tables):
    """(원격 DB 식 한 행이 PC에서 차지할 크기, 근거 글). 컬럼마다 명세서의 bytes > DB 통계(Postgres avg_width) > 타입으로 어림.
    BigQuery는 테이블의 평균 행 크기를 타입 어림 비율로 컬럼에 나눈다. 행을 읽어 재지 않는다(원격은 읽는 만큼 요금·시간이 든다)."""
    widths = {n: default_width(t) for n, t in expr.schema().items()}
    basis = set()
    average = table_row_bytes(con, tables)
    if average:
        whole = sum(widths.values()) or 1
        widths = {n: w * average / whole for n, w in widths.items()}
        basis.add("테이블 메타")
    stats, spec = stat_widths(con, tables), spec_widths(tables)
    for name in widths:
        if name.lower() in spec:
            widths[name] = spec[name.lower()]
            basis.add("데이터 명세서")
        elif name.lower() in stats:
            widths[name] = stats[name.lower()]
            basis.add("DB 통계")
    return max(1, round(sum(widths.values()))), "·".join(sorted(basis)) or "스키마"


def plan_rows(con, sql, dialect):
    """DB의 실행 계획(EXPLAIN, 조회를 돌리지 않는 것)에서 읽는 행 수 추정. 글 모양은 DB마다 달라 ROW_PATTERNS를 차례로 찾고, 표 모양(MySQL: rows 칸)도 본다. 못 찾으면 None."""
    from core import plan
    try:
        text = plan.explain(con, sql, dialect or sql_exec.dialect_of(con))["text"]
    except Exception:
        return None
    lines = text.splitlines()
    if len(lines) > 1 and "rows" in lines[0].split("\t"):  # 표 모양: 머리글의 rows 칸
        head = lines[0].split("\t")
        at, key = head.index("rows"), head.index("id") if "id" in head else None
        kind = head.index("select_type") if "select_type" in head else None
        found, again = {}, False
        for c in (line.split("\t") for line in lines[1:]):
            if at < len(c) and c[at].strip().isdigit():
                found.setdefault(c[key] if key is not None and key < len(c) else "", []).append(int(c[at]))
            again = again or kind is not None and kind < len(c) and c[kind].upper().startswith(("DEPENDENT", "UNCACHEABLE"))
        if found and again:  # 바깥 행마다 다시 도는 서브쿼리(select_type 칸): 모두 곱한다
            return math.prod(n for rows in found.values() for n in rows)
        if found:
            return sum(map(math.prod, found.values()))  # 같은 id(한 SELECT의 조인)는 곱하고, UNION·서브쿼리(다른 id)는 더해 크게 센다
    for pattern in ROW_PATTERNS:
        if found := re.search(pattern, text):
            return int(found[1].replace(",", ""))
    return None


def date_bounds(sql, dialect, column):
    """SQL의 조건에서 그 날짜 컬럼에 건 범위 (처음, 끝)(datetime.date, 없는 쪽은 None). ISO 글자나 DATE(년,월,일)·MAKE_DATE 모양의 값만 읽는다."""
    sqlglot, exp = sql_exec.glot()[:2]
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return None, None
    if tree.find(exp.Or, exp.Not) or len(list(tree.find_all(exp.Select))) > 1:  # OR·NOT·하위쿼리·CTE가 있으면 조건이 범위를 좁히는지 알 수 없다
        return None, None
    low = high = None
    for node in tree.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ):
        left, right, kind = node.this, node.expression, type(node).__name__
        if isinstance(right, exp.Column) and not isinstance(left, exp.Column):
            left, right, kind = right, left, FLIP[kind]
        if not isinstance(left, exp.Column) or left.name.lower() != column.lower():
            continue
        literal = all(isinstance(n, (exp.Literal, exp.Cast, exp.DataType, exp.Date, exp.DateFromParts, exp.Timestamp, exp.TsOrDsToDate, exp.TsOrDsToDatetime))
                      or isinstance(n, exp.Anonymous) and n.name.lower() == "make_date" for n in right.walk())  # DATE_SUB(DATE '…', …) 같은 계산식 안의 날짜는 범위가 아니다
        m = literal and DATE.search(right.sql(dialect))  # 방언 그대로 써야 BigQuery DATE(2025, 1, 1)이 DATE_FROM_PARTS로 바뀌지 않는다
        if not m:
            continue
        try:
            day = dt.date(*[int(x) for x in (m.groups()[:3] if m[1] else m.groups()[3:])])
        except ValueError:
            continue
        zoned = re.search(r"\d:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:[+-]\d{2}(?::?\d{2})?|Z|[A-Za-z][\w/+-]*)'|,\s*'", right.sql(dialect))  # 시간대가 붙은 시각(+09:00, 'Asia/Seoul'): UTC 파티션 날짜가 하루 어긋날 수 있어 넓힌다
        shift = dt.timedelta(days=1 if zoned else 0)
        if kind in ("GT", "GTE", "EQ"):
            low = day - shift if low is None else max(low, day - shift)
        if kind in ("LT", "LTE", "EQ"):
            high = day + shift if high is None else min(high, day + shift)
    return low, high


def partition_rows(con, sql, dialect, tables):
    """BigQuery에서 그 테이블이 데이터 명세서의 파티션 컬럼(날짜)으로 나뉘어 있으면, 조건의 날짜 범위에 드는 파티션들의 행 수 합(INFORMATION_SCHEMA.PARTITIONS 메타데이터, 테이블을 읽지 않는다).
    일 단위 파티션(partition_id가 YYYYMMDD)만 다루고, 아니면 None."""
    client = getattr(con, "client", None)
    if getattr(con, "name", "") != "bigquery" or client is None or len(tables) != 1:
        return None
    from core import profiler
    part = profiler.spec_table(None, tables[0]).get("partition") or {}
    if not part.get("column"):
        return None
    low, high = date_bounds(sql, dialect, part["column"])
    try:
        schema = bigquery_name(con, "x").rsplit(".", 1)[0]  # 프로젝트.데이터셋(앞부분)
        name = tables[0].replace("'", "")
        rows = client.query(f"select partition_id, total_rows from `{schema}.INFORMATION_SCHEMA.PARTITIONS` where table_name = '{name}'").result()
        total = 0
        for row in rows:
            pid = str(row["partition_id"])
            if pid == "__NULL__":  # 파티션 날짜가 빈 행(원래 설계대로 뺀다)
                continue
            if pid == "__UNPARTITIONED__":  # 막 넣은(스트리밍) 행: 날짜를 모르니 센다
                total += int(row["total_rows"] or 0)
                continue
            if not re.fullmatch(r"\d{8}", pid):
                return None  # 달·시간 단위 파티션은 다루지 않는다(0으로 세면 확인 없이 받는다)
            day = dt.date(int(pid[:4]), int(pid[4:6]), int(pid[6:]))
            if (low is None or day >= low) and (high is None or day <= high):
                total += int(row["total_rows"] or 0)
        return total
    except Exception:
        return None


def man(n):
    """행 수를 읽기 쉽게: 1만 이상은 '4,100만'."""
    return f"{n / 10000:,.0f}만" if n >= 10000 else f"{n:,}"


def fetched(con, sql, rows=None, dialect=None):
    """원격 DB에서 sql의 결과를 이 PC로 받을 때의 {"bytes", "exact": False, "how", "rows"}. 조회를 더 보내 세지 않고(COUNT·표본 없음) 메타데이터만 쓴다.
    행 수: 아는 값(rows) > BigQuery 파티션 메타 > 실행 계획(EXPLAIN) 추정 > BigQuery dry run 바이트(받는 양의 최대). 행 크기는 row_width. 알 수 없으면 ValueError."""
    expr = con.sql(sql)
    tables, _ = referenced(sql, dialect or sql_exec.dialect_of(con))
    if rows is not None:
        count, basis_rows = rows, "아는 행 수"
    elif (count := partition_rows(con, sql, dialect, tables)) is not None:
        basis_rows = "파티션 메타"
    elif (count := plan_rows(con, sql, dialect)) is not None:
        basis_rows = "실행 계획 추정"
    elif getattr(con, "name", "") == "bigquery":
        return {"bytes": bigquery_bytes(con, sql), "exact": False, "how": "BigQuery dry run(처리 바이트, 받는 양의 최대)", "rows": None}
    else:
        raise ValueError("이 연결은 받을 행 수를 메타데이터로 알 수 없음")
    width, basis = row_width(con, expr, tables)
    return {"bytes": int(count) * width, "exact": False, "how": f"약 {man(int(count))}행 × 행당 약 {width}바이트({basis_rows}·{basis} 기준 추정)", "rows": int(count)}


def limit(config=None):
    """(기준 바이트, 기준 글자). 설정 memory(MB, 1GB=1000MB)가 있으면 그 값, 없으면(제한 없음) 지금 PC의 사용 가능 메모리."""
    mb = (config if config is not None else settings.load()).get("memory")
    if mb:
        return monitor.bound_bytes(config), f"메모리 한도 {mb / 1000:g} GB"
    free = monitor.bound_bytes(config)
    return free, f"지금 PC의 사용 가능 메모리 {size_text(free)}(메모리 한도 없음)"


def limit_check(con, sql, opened=None, dialect=None, config=None, rows=None):
    """실행 전 처리 용량 확인(데이터는 읽지 않는다). 대시보드 최신화와 서버 라우트가 같이 쓴다. 안쪽에서 DB를 묻는 것은 호출하는 쪽이 연결 스레드에서 부른다.
    돌려주는 값: over(센 용량이 기준을 넘음), unknown(용량을 알 수 없음), blocked(알 수 없는데 설정 scan_unknown이 block),
    bytes(셀 수 없으면 None), exact, how, limit(기준 바이트), tables, columns(None이면 모든 컬럼), text(팝업·건너뛴 까닭에 보일 글).
    원격 DB 연결이면 sql의 결과를 이 PC로 받는 양(fetched, 메타데이터로만)을 견준다. 맨 바깥이 집계면 세지 않는다(how="집계 결과라 작음", BigQuery는 dry run으로 읽는 양).
    받는 식이 아닌 일(파일로 흘려 쓰기, DB 안의 계산)은 부르는 쪽이 부르지 않는다. rows는 원격에서 이미 아는 행 수. 돌려주는 값에 remote(원격 기준인지)를 더했다.
    over이거나 blocked일 때만 실행을 멈추고 묻는다(blocked는 확인해도 실행하지 않는다)."""
    config = settings.load() if config is None else config
    remote = held.remote(con)
    small = remote and is_aggregate(sql, dialect or sql_exec.dialect_of(con))  # 집계 결과는 작아서 원격 DB에서는 세지 않는다
    billed = small and getattr(con, "name", "") == "bigquery"  # BigQuery는 결과가 작아도 읽는 양에 요금이 나므로 집계도 dry run 바이트(정확)로 견준다
    found = ({"bytes": 0, "exact": False, "how": "집계 결과라 작음"} if small and not billed
             else safe(lambda: fetched(con, sql, rows, dialect) if remote and not billed else estimate(con, sql, opened, dialect)))
    bound, bound_text = limit(config)
    try:
        tables, columns = referenced(sql, dialect or sql_exec.dialect_of(con))
        named = tables + [".".join(p for p in q if p) for q in qualified(sql_exec.glot()[0].parse_one(sql, read=dialect or sql_exec.dialect_of(con)))]  # 팝업 글에는 스키마 붙은 테이블도
    except Exception:
        tables, columns, named = [], None, []
    unknown = found["bytes"] is None
    over = not unknown and found["bytes"] > bound
    blocked = unknown and blocks_unknown(config)
    what = f"테이블 {', '.join(named) or '없음'} · 컬럼 {'모두' if columns is None else ', '.join(sorted(columns)[:12]) + (' …' if len(columns) > 12 else '')}"
    if unknown:
        text = f"처리 용량을 미리 알 수 없어요({found['how']}). " + ("설정에서 '알 수 없으면 실행하지 않음'으로 정해 두어 실행하지 않아요." if blocked else "")
    else:
        text = f"PC로 약 {size_text(found['bytes'])}를 받아요(추정 · {found['how']}) · {bound_text} · {what}" if remote and not billed else f"{'' if found['exact'] else '약 '}{size_text(found['bytes'])}를 읽어요({'정확' if found['exact'] else '추정'} · {found['how']}) · {bound_text} · {what}"
    return {"over": over, "unknown": unknown, "blocked": blocked, "bytes": found["bytes"], "exact": found["exact"], "how": found["how"],
            "limit": bound, "remote": remote, "tables": tables, "columns": None if columns is None else sorted(columns), "text": text.strip()}
