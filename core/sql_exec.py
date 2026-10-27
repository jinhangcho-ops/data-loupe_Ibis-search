"""SQL 직접 쓰기(SafeIbisSQLExecutor)와 가상 테이블.
- 검사: sqlglot으로 읽어 문장이 하나이고 맨 위가 SELECT·UNION·INTERSECT·EXCEPT인지, 어디에도 바꾸는 문장(DROP·DELETE·INSERT 등)이 없는지 본다.
  파일·폴더를 읽는 함수(read_*·glob·*_scan 등)와 FROM '경로', 환경·설정 읽기(getenv 등), 잠금(FOR UPDATE·TABLOCK 등)도 막는다.
  이 목록은 실수를 막는 것이고 원격 DB에서 지키는 선은 읽기 전용 계정이다(모르는 함수 이름은 통과한다). 어기면 SecurityViolationError. WITH, 여러 테이블 JOIN, LIKE, * 는 된다.
- 실행: 검사한 SQL을 연결의 con.sql()로 Ibis 식으로 만들고, 화면에 보일 때는 max_rows행까지만(limit) 받는다.
- 가상 테이블: 이름 붙인 SQL(또는 Parquet 파일 경로). 쓰는 SQL 앞에 WITH 이름 AS (…)로 풀어 넣어 DB에는 아무것도 만들지 않는다.
  연결마다 따로 둔다(virtual_tables.json {연결 열쇠: {이름: 정의}}, 연결 정보·비밀번호 없음). Parquet은 DuckDB·파일(Polars) 연결에서만 쓴다."""
import datetime as dt
import functools
import re

from core import settings
from core import source

VT_FILE = "virtual_tables.json"
MAX_VIRTUAL = 100  # 연결 하나에 둘 수 있는 가상 테이블 수
MAX_SQL_SIZE = 20_000  # SQL 하나의 글자 수
DEFAULT_ROWS, MAX_ROWS = 1_000, 100_000  # 화면에 받을 행 수의 기본값과 상한
NAME = re.compile(r"[A-Za-z_가-힣][A-Za-z0-9_가-힣]{0,49}")
ROOTS = ("Select", "Union", "Intersect", "Except", "Subquery")  # 맨 위에 올 수 있는 것(괄호로 감싼 SELECT 포함)
FORBIDDEN = ("Drop", "Delete", "Insert", "Update", "Create", "Alter", "TruncateTable", "Grant", "Revoke", "Command", "Merge", "Into",  # 어디에 있어도 막는 것:
             "Pragma", "Set", "Copy", "Attach", "Detach", "Use", "Transaction", "Commit", "Rollback", "LoadData", "Kill", "Refresh",  # 데이터·권한·설정을 바꾸거나
             "Cache", "Uncache", "Analyze")  # 파일·DB를 붙이는 문장
PARQUET_BACKENDS = ("duckdb", "polars")
FILE_FUNCS = {"glob", "sniff_csv", "st_read", "query", "query_table", "postgres_query", "postgres_execute", "mysql_query", "sqlite_query",  # 파일·폴더를 읽거나 문자열을 SQL로 돌리는 함수 이름
              "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "load_file", "openrowset", "opendatasource", "openquery",  # Postgres·MySQL·MSSQL 서버의 파일·원격 읽기
              "json_execute_serialized_sql", "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema", "cursor_to_xml",  # 글자로 준 SQL을 돌리는 함수(DuckDB·Postgres)
              "url", "urlcluster", "file", "remote", "remotesecure", "cluster", "clusterallreplicas", "s3", "s3cluster", "gcs", "hdfs", "hdfscluster",  # ClickHouse의 파일·네트워크·다른 서버 읽기
              "azureblobstorage", "azureblobstoragecluster", "mysql", "postgresql", "mongodb", "redis", "sqlite", "jdbc", "odbc", "executable", "input"}
FILE_PREFIXES = ("read_", "parquet_", "iceberg_", "delta_", "dblink", "xp_", "lo_")  # 이름이 이렇게 시작하는 함수(lo_*: Postgres 큰 객체 읽기·쓰기·지우기)
FILE_SUFFIXES = ("_scan", "_attach")  # 이름이 이렇게 끝나는 함수(delta_scan, iceberg_scan, excel_scan …, sqlite_attach는 연결한 DB에 뷰를 만든다). DuckDB의 postgres_query 같은 원격 조회는 FILE_FUNCS에 이름으로(json_query처럼 정상인 *_query 함수는 막지 않는다)
ENV_FUNCS = {"getenv", "current_setting", "current_settings", "duckdb_settings", "duckdb_secrets", "which_secret",
             "setval", "nextval", "set_config", "pg_terminate_backend", "pg_cancel_backend", "sleep", "pg_sleep",  # 환경·설정·비밀을 읽거나 서버 상태를 바꾸는 함수
             "get_lock", "pg_reload_conf", "pg_switch_wal", "pg_rotate_logfile", "pg_promote", "benchmark",  # 잠금을 잡거나 서버 설정·로그·부하를 바꾸는 함수
             "enable_logging", "disable_logging", "enable_profiling", "disable_profiling", "write_log", "truncate_duckdb_logs",  # DuckDB 로그·프로파일: 아무 파일에나 쓰고 연결을 망가뜨린다
             "checkpoint", "force_checkpoint"}
ENV_PREFIXES = ("pg_advisory_", "pg_try_advisory_", "pg_stat_reset", "pg_drop_", "pg_create_", "system$")  # 이름이 이렇게 시작하는 함수(잠금·통계 지우기·복제 슬롯, Snowflake SYSTEM$…)
LOCK_HINT = re.compile(r"(TAB|UPD|X|HOLD|PAG|ROW)LOCK|SERIALIZABLE|REPEATABLEREAD", re.I)  # SQL Server 테이블 힌트 중 잠금을 잡는 것(NOLOCK·INDEX 같은 힌트는 된다)
FILE_TABLE = re.compile(r"\.(csv|tsv|parquet|jsonl?|ndjson|geojson|duckdb|gz|zst|xlsx|tbl|txt)$", re.I)  # DuckDB가 파일로 읽는 테이블 이름(스키마.테이블로 나뉜 t.csv 포함)
FILE_CLASSES = ("ReadCSV", "ReadParquet")  # sqlglot이 함수 이름 대신 따로 만드는 클래스(없는 버전이면 건너뜀)
READ_MESSAGE = "SQL 안에서 파일을 직접 읽을 수 없어요. 파일은 [연결]로 열거나 가상 테이블(Parquet)로 등록하세요."
ENV_MESSAGE = "SQL 안에서 환경·설정 값을 읽거나 서버 상태를 바꿀 수 없어요."
LOCK_MESSAGE = "SQL 안에서 테이블을 잠글 수 없어요(FOR UPDATE·TABLOCK 같은 잠금)."


@functools.cache
def glot():
    """(sqlglot, sqlglot.exp, ROOTS 클래스들, FORBIDDEN 클래스들). sqlglot은 무거워 처음 SQL을 다룰 때 올린다(지연 로딩)."""
    import sqlglot
    from sqlglot import exp
    return sqlglot, exp, tuple(getattr(exp, n) for n in ROOTS), tuple(getattr(exp, n) for n in FORBIDDEN if hasattr(exp, n))


class SecurityViolationError(ValueError):
    """읽기만 하는 SQL이 아니다(바꾸는 문장, 여러 문장 등)."""


class SqlSyntaxError(ValueError):
    """SQL 문법 오류. line·col은 오류가 난 자리(1부터, 모르면 None)."""

    def __init__(self, message, line=None, col=None):
        super().__init__(message)
        self.line, self.col = line, col


SYNTAX_WORDS = [  # sqlglot의 영어 오류 설명 → 한국어(앞에서부터 처음 맞는 것)
    (re.compile(r"Expected table name but got <Token[^>]*text: ([^,>]*)"), lambda m: f"테이블 이름이 와야 하는데 '{m[1]}'가 있습니다"),
    (re.compile(r"Expecting \)"), lambda m: "닫는 괄호 ')'가 필요합니다"),
    (re.compile(r"Expecting \("), lambda m: "여는 괄호 '('가 필요합니다"),
    (re.compile(r"Invalid expression / Unexpected token"), lambda m: "알 수 없는 표현이나 예상하지 못한 낱말이 있습니다"),
    (re.compile(r"Required keyword: '(\w+)' missing"), lambda m: "빠진 부분이 있습니다"),
    (re.compile(r"Expected (\w+)"), lambda m: f"{m[1]}가 와야 합니다"),
]


def syntax_message(description):
    """sqlglot 오류 설명을 한국어로. 모르는 것은 토큰 덤프만 낱말로 줄여 그대로."""
    for pattern, say in SYNTAX_WORDS:
        if m := pattern.search(description):
            return say(m)
    return re.sub(r"<Token[^>]*text: ([^,>]*)[^>]*>", lambda m: f"'{m[1]}'", description).split(". Line")[0]


def dialect_of(con):
    """연결의 SQL 방언(sqlglot이 읽고 쓸 이름 또는 클래스). 모르면 None(일반 SQL)."""
    return getattr(con, "dialect", None)


def check(sql, dialect=None):
    """sql을 검사해 sqlglot 식으로 돌려준다. 읽기만 하는 문장 하나가 아니면 SecurityViolationError, 문법 오류는 ValueError."""
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL을 입력하세요.")
    if len(sql) > MAX_SQL_SIZE:
        raise ValueError(f"SQL은 {MAX_SQL_SIZE:,}자까지입니다.")
    sqlglot, _, roots, forbidden = glot()
    try:
        parsed = [p for p in sqlglot.parse(sql, read=dialect) if p is not None]
    except sqlglot.errors.ParseError as e:
        first = (e.errors or [{}])[0]
        line, col = first.get("line"), first.get("col")
        where = f"{line}줄 {col}칸: " if line else ""
        raise SqlSyntaxError(f"SQL을 읽을 수 없습니다. {where}{syntax_message(first.get('description') or str(e))}", line, col) from None
    except sqlglot.errors.TokenError:
        raise SqlSyntaxError("SQL을 읽을 수 없습니다. 따옴표나 괄호가 닫히지 않았습니다.") from None
    if len(parsed) != 1:
        raise SecurityViolationError("SQL 문장은 하나만 실행할 수 있습니다(; 로 여러 문장을 이을 수 없음).")
    tree = parsed[0]
    if not isinstance(tree, roots):
        raise SecurityViolationError(f"읽기만 하는 SELECT 문만 실행할 수 있습니다({type(tree).__name__.upper()} 문은 실행하지 않습니다).")
    bad = next((node for node in tree.walk() if isinstance(node, forbidden)), None)
    if bad is not None:
        raise SecurityViolationError(f"데이터나 설정을 바꾸는 부분({type(bad).__name__.upper()})이 있어 실행하지 않습니다.")
    if problem := file_read_problem(tree, sql):
        raise SecurityViolationError(problem)
    return tree


def file_read_problem(tree, sql):
    """식 어디에든(하위 쿼리·CTE·조인·값 자리) 파일을 읽거나 환경·설정을 읽는 부분이 있으면 그 오류 글, 없으면 None.
    함수는 sqlglot 전용 클래스(ReadCSV 등)와 Anonymous 이름 둘 다 보고(스키마·대소문자·따옴표 무관), 글자로 쓴 테이블(FROM '경로')도 본다."""
    exp = glot()[1]
    classes = tuple(getattr(exp, n) for n in FILE_CLASSES if hasattr(exp, n))
    for node in tree.walk():
        if isinstance(node, classes):
            return READ_MESSAGE
        if isinstance(node, exp.Lock) or isinstance(node, exp.WithTableHint) and LOCK_HINT.search(node.sql()):
            return LOCK_MESSAGE
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in ENV_FUNCS or name.startswith(ENV_PREFIXES):
                return ENV_MESSAGE
            if name in FILE_FUNCS or name.startswith(FILE_PREFIXES) or name.endswith(FILE_SUFFIXES):
                return READ_MESSAGE
        elif isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
            ident = node.this
            start = ident.meta.get("start")
            if start is not None and sql[start:start + 1] == "'":  # 작은따옴표 글자가 테이블 자리에 온 것
                return READ_MESSAGE
            full = ".".join(p.name for p in node.parts)  # 카탈로그.스키마.테이블 전체(t.csv는 스키마 t, 테이블 csv로 나뉜다)
            if re.search(r"[/\\]", full) or FILE_TABLE.search(full):  # 따옴표 종류와 관계없이 경로 모양이거나 파일 확장자로 끝나는 이름
                return READ_MESSAGE
            if ident.quoted and re.search(r"\.d?db$", ident.name, re.I):  # 따옴표로 감싼 "x.db"·"x.ddb"는 DuckDB가 DuckDB 파일로 읽는다(따옴표 없는 스키마.db 테이블은 정상)
                return READ_MESSAGE
    return None


def table_names(tree):
    """식에서 읽는 테이블 이름들(스키마로 가리지 않은 것). 식 안의 WITH 이름은 뺀다."""
    exp = glot()[1]
    own = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    return [t.name for t in tree.find_all(exp.Table) if not t.args.get("db") and t.name and t.name.lower() not in own]


# ---------- 가상 테이블 저장소 ----------

def all_virtual():
    """{연결 열쇠: {이름: {"sql" 또는 "parquet", "saved"}}}. 망가진 항목은 뺀다."""
    found = settings.read(VT_FILE) or {}
    return {key: {n: d for n, d in vts.items() if isinstance(d, dict) and ("sql" in d or "parquet" in d)}
            for key, vts in found.items() if isinstance(vts, dict)}


def virtual(key):
    """그 연결의 가상 테이블 {이름: 정의}. 연결 열쇠가 파일까지 가르기 전(폴더만, '#' 없음)에 저장한 것도 같은 연결의 것으로 읽는다(use_counts.same_source)."""
    found = all_virtual()
    return found.get(key) or found.get(key.partition("#")[0], {})


def save_virtual(key, name, definition, real_tables, verify):
    """가상 테이블을 저장한다(같은 이름은 덮어쓴다). definition이 None이면 지운다. 바뀐 그 연결의 가상 테이블을 돌려준다.
    definition은 {"sql"} 또는 {"parquet": 경로}. verify(저장한 뒤의 가상 테이블들)가 지금 연결에서 읽어 보고 안 되면 예외를 낸다.
    진짜 테이블과 같은 이름은 쓸 수 없다."""
    name = name.strip() if isinstance(name, str) else ""
    if not NAME.fullmatch(name):
        raise ValueError("가상 테이블 이름은 영문·한글·밑줄로 시작하고 영문·숫자·밑줄·한글만 50자까지 쓸 수 있습니다.")
    with settings.updating(VT_FILE):  # 읽고-고치고-쓰는 동안 다른 저장이 끼어들지 못하게
        data = all_virtual()
        old = key.partition("#")[0]
        if key not in data and old != key and old in data:  # 예전 열쇠로 저장한 것은 저장할 때 지금 열쇠로 옮긴다
            data[key] = data.pop(old)
        vts = data.setdefault(key, {})
        if definition is None:
            vts.pop(name, None)
        else:
            if name.lower() in {t.lower() for t in real_tables}:
                raise ValueError(f"'{name}'은 이 연결에 있는 테이블 이름입니다. 다른 이름을 쓰세요.")
            if not isinstance(definition, dict) or not (isinstance(definition.get("sql"), str) or isinstance(definition.get("parquet"), str)):
                raise ValueError("가상 테이블은 SQL이나 Parquet 파일 경로로 정의합니다.")
            item = {"sql": definition["sql"]} if isinstance(definition.get("sql"), str) else {"parquet": definition["parquet"].strip()}
            if name not in vts and len(vts) >= MAX_VIRTUAL:
                raise ValueError(f"가상 테이블은 연결마다 {MAX_VIRTUAL}개까지입니다. 쓰지 않는 것을 지우세요.")
            verify({**vts, name: item})  # 검사, 이름 고리, 지금 연결에서 읽히는지
            vts[name] = {**item, "saved": dt.datetime.now().isoformat(timespec="seconds")}
        settings.write(VT_FILE, data)
        return vts


def quote(name):
    return '"' + str(name).replace('"', '""') + '"'


# ---------- 풀어 넣기와 실행 ----------

def definition_tree(con, name, item):
    """가상 테이블 하나의 정의를 식으로. Parquet은 DuckDB·Polars 연결에서만(read_parquet)."""
    if "sql" in item:
        return check(item["sql"], dialect_of(con))
    if con.name not in PARQUET_BACKENDS:
        raise ValueError(f"Parquet 가상 테이블 '{name}'은 DuckDB 파일이나 파일 연결에서만 쓸 수 있습니다.")
    if getattr(con, "locked", False):
        raise ValueError(f"Parquet 가상 테이블 '{name}'은 이 DuckDB 파일에서 쓸 수 없습니다. 파일에 뷰·매크로가 있어 다른 파일 읽기를 막았습니다(설정의 안전하게 쓰기).")
    path = item["parquet"].replace("\\", "/").replace("'", "''")
    return glot()[0].parse_one(f"SELECT * FROM read_parquet('{path}')", read="duckdb")


def expand(con, tree, vts):
    """tree가 읽는 가상 테이블(그 정의가 읽는 가상 테이블까지)을 WITH로 앞에 넣은 식. 정의가 서로를 고리로 부르면 ValueError."""
    order, visiting = [], []
    lower = {n.lower(): n for n in vts}

    def visit(name):
        if name in order:
            return
        if name in visiting:
            raise ValueError("가상 테이블이 서로를 고리처럼 부릅니다: " + " → ".join(visiting + [name]))
        visiting.append(name)
        sub = definition_tree(con, name, vts[name])
        for used in table_names(sub):
            if used.lower() in lower:
                visit(lower[used.lower()])
        visiting.pop()
        order.append(name)

    for used in table_names(tree):
        if used.lower() in lower:
            visit(lower[used.lower()])
    if not order:
        return tree
    exp = glot()[1]
    ctes = [exp.CTE(this=definition_tree(con, n, vts[n]), alias=exp.TableAlias(this=exp.to_identifier(n, quoted=True))) for n in order]
    tree = tree.copy()
    if isinstance(tree, exp.Subquery):
        tree = tree.unnest()
    own = tree.args.get("with_")  # sqlglot은 WITH를 with_ 인자에 둔다
    tree.set("with_", exp.With(expressions=ctes + (own.expressions if own else []), recursive=bool(own and own.args.get("recursive"))))
    return tree


def to_expr(con, sql, vts):
    """검사하고 가상 테이블을 풀어 넣은 SQL을 그 연결의 Ibis 식으로(아직 실행하지 않는다)."""
    source.refresh_csv(con)  # 원본 CSV가 바뀌었으면 다시 변환(SQL 탭·검사·내보내기도 옛 값에 머물지 않게)
    tree = expand(con, check(sql, dialect_of(con)), vts)
    return con.sql(tree.sql(dialect=dialect_of(con)))


def virtual_table(con, name, vts):
    """가상 테이블 하나를 Ibis 식으로(탐색의 기준·조인·이어붙이기 테이블로 쓴다)."""
    return to_expr(con, f"SELECT * FROM {quote(name)}", vts)


def rows_limit(value):
    """화면에 받을 행 수. 없으면 DEFAULT_ROWS."""
    n = DEFAULT_ROWS if value in (None, "") else value
    if type(n) is not int or not 1 <= n <= MAX_ROWS:
        raise ValueError(f"받을 행 수는 1 이상 {MAX_ROWS:,} 이하의 정수로 입력하세요.")
    return n
