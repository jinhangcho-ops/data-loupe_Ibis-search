"""DB·DW·DL별로 필요한 드라이버를 확인한다. 없으면 어디서 받는지, 어떤 명령으로 설치하는지 알려 준다. 스스로 받거나 설치하지 않는다."""
import functools
import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

# 주소 앞부분: (DB 이름, 파이썬 모듈, Ibis 설치 이름, 따로 설치할 프로그램)
DRIVERS = {
    "athena": ("Amazon Athena", "pyathena", "athena", None),
    "bigquery": ("BigQuery", "google.cloud.bigquery", "bigquery", None),
    "clickhouse": ("ClickHouse", "clickhouse_connect", "clickhouse", None),
    "databricks": ("Databricks", "databricks.sql", "databricks", None),
    "druid": ("Druid", "pydruid.db", "druid", None),
    "duckdb": ("DuckDB", "duckdb", "duckdb", None),
    "exasol": ("Exasol", "pyexasol", "exasol", None),
    "flink": ("Flink", "pyflink", "flink", "java"),
    "impala": ("Impala", "impala.dbapi", "impala", None),
    "materialize": ("Materialize", "psycopg", "materialize", None),
    "mssql": ("MSSQL", "pyodbc", "mssql", "odbc"),
    "mysql": ("MySQL", "MySQLdb", "mysql", None),
    "oracle": ("Oracle", "oracledb", "oracle", None),
    "postgres": ("PostgreSQL", "psycopg", "postgres", None),
    "pyspark": ("Spark", "pyspark", "pyspark", "java"),
    "risingwave": ("RisingWave", "psycopg2", "risingwave", None),
    "singlestoredb": ("SingleStore", "singlestoredb", "singlestoredb", None),
    "snowflake": ("Snowflake", "snowflake.connector", "snowflake", None),
    "sqlite": ("SQLite", "sqlite3", "sqlite", None),
    "trino": ("Trino", "trino", "trino", None),
}

# 따로 설치할 프로그램: (이름, 다운로드 주소)
PROGRAMS = {
    "odbc": ("ODBC Driver 17/18 for SQL Server",
             "https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"),
    "java": ("Java (JDK 11 이상)", "https://adoptium.net/"),
}

# 주소 앞부분별 (묶음, 주소 예, 한국어 설명). 묶음: DB(운영 DB)·DW(데이터 웨어하우스)·DL(데이터 레이크·엔진)
DETAILS = {
    "clickhouse": ("DB", "clickhouse://host:8123/db", "분석용 열 기반 DB예요. HTTP로 접속하는 파이썬 패키지가 필요해요."),
    "druid": ("DB", "druid://host:8082/druid/v2/sql", "실시간 집계용 DB예요. SQL로 접속하는 pydruid 패키지가 필요해요."),
    "duckdb": ("DB", "duckdb://data.duckdb", "이 프로그램이 파일을 읽는 기본 엔진이에요. 보통 이미 들어 있어요."),
    "exasol": ("DB", "exasol://host:8563", "인메모리 분석 DB예요. pyexasol 패키지가 필요해요."),
    "materialize": ("DB", "materialize://host:6875/db", "스트리밍 SQL DB예요. PostgreSQL 방식으로 접속하므로 psycopg 패키지가 필요해요."),
    "mssql": ("DB", "mssql://host:1433/db", "SQL Server예요. pyodbc 패키지와 ODBC Driver 17/18 프로그램이 둘 다 필요해요."),
    "mysql": ("DB", "mysql://host:3306/db", "MySQL·MariaDB예요. mysqlclient 패키지가 필요해요."),
    "oracle": ("DB", "oracle://host:1521/db", "Oracle DB예요. oracledb 패키지가 필요해요."),
    "postgres": ("DB", "postgres://host:5432/db", "PostgreSQL이에요. psycopg 패키지가 필요해요."),
    "risingwave": ("DB", "risingwave://host:4566/db", "스트리밍 SQL DB예요. psycopg2 패키지가 필요해요."),
    "singlestoredb": ("DB", "singlestoredb://host:3306/db", "SingleStore예요. singlestoredb 패키지가 필요해요."),
    "sqlite": ("DB", "sqlite://data.db", "파일 하나짜리 작은 DB예요. 파이썬에 기본으로 들어 있어요."),
    "bigquery": ("DW", "bigquery://프로젝트/데이터셋", "Google의 웨어하우스예요. google-cloud-bigquery 패키지가 필요하고, 처음 접속할 때 Google 로그인을 해요."),
    "databricks": ("DW", "databricks://?server_hostname=…&http_path=…", "Databricks SQL 웨어하우스예요. databricks-sql-connector 패키지가 필요해요."),
    "impala": ("DW", "impala://host:21050/db", "하둡 계열 SQL 엔진이에요. impyla 패키지가 필요해요."),
    "snowflake": ("DW", "snowflake://계정/데이터베이스/스키마?warehouse=WH", "Snowflake예요. snowflake-connector-python 패키지가 필요해요."),
    "athena": ("DL", "athena://?s3_staging_dir=s3://…&region_name=…", "S3의 파일을 SQL로 조회하는 AWS 서비스예요. PyAthena 패키지가 필요해요."),
    "flink": ("DL", "flink://", "스트림 처리 엔진이에요. PyFlink 패키지와 Java가 필요하고, 소스로 실행할 때만 써요."),
    "pyspark": ("DL", "pyspark://", "Spark예요. PySpark 패키지와 Java가 필요하고, 소스로 실행할 때만 써요."),
    "trino": ("DL", "trino://host:8080/catalog/schema", "여러 저장소를 한 번에 조회하는 SQL 엔진이에요. trino 패키지가 필요해요."),
}
# DRIVERS에 없는 대상은 다른 드라이버를 빌려 쓴다: 열쇠 → (이름, 묶음, 빌려 쓰는 주소 앞부분, 주소 예, 설명)
BORROWED = {
    "redshift": ("Amazon Redshift", "DW", "postgres", "postgres://host:5439/db",
                 "AWS의 웨어하우스예요. PostgreSQL 방식으로 접속하므로 postgres 드라이버를 써요(주소도 postgres://)."),
    "supabase": ("Supabase", "DB", "postgres", "postgres://postgres.프로젝트ref@aws-0-리전.pooler.supabase.com:5432/postgres",
                 "Supabase(PostgreSQL)예요. postgres 드라이버(psycopg)를 써요. 대시보드 Connect에서 Session pooler 주소(5432)를 복사해 붙여넣고 비밀번호는 칸에 따로 쓰세요. "
                 "Direct(db.<ref>.supabase.co:5432)는 무료 플랜에서 IPv6 전용이라 네트워크에 따라 안 붙을 수 있고, Transaction pooler(포트 6543)는 prepared statement를 끄고 접속해요. 읽기 전용 계정을 권장해요."),
    "synapse": ("Azure Synapse", "DW", "mssql", "mssql://host:1433/db",
                "Azure의 웨어하우스예요. SQL Server 방식으로 접속하므로 mssql 드라이버와 ODBC 프로그램을 써요."),
}
# DuckDB 확장: 열쇠(= 확장 이름) → (이름, 주소 예, 설명). 파이썬 패키지가 아니라 DuckDB 안에 설치하는 확장이다.
EXTENSIONS = {
    "httpfs": ("DuckDB 확장 httpfs (S3)", "s3://버킷/경로/*.parquet",
               "DuckDB가 S3·HTTP의 파일을 바로 읽게 해 줘요. 파이썬 패키지가 아니라 DuckDB 확장이에요."),
    "delta": ("DuckDB 확장 delta", "delta_scan('경로')",
              "Delta Lake 테이블을 읽게 해 줘요. 파이썬 패키지가 아니라 DuckDB 확장이에요."),
    "iceberg": ("DuckDB 확장 iceberg", "iceberg_scan('경로')",
                "Iceberg 테이블을 읽게 해 줘요. 파이썬 패키지가 아니라 DuckDB 확장이에요."),
}
# 받는 곳: 드라이버(주소 앞부분) → PyPI 패키지 이름. 화면은 실행할 명령 대신 이 쪽으로 넘어가는 단추와 주소 복사를 보인다
PACKAGES = {"athena": "PyAthena", "bigquery": "google-cloud-bigquery", "clickhouse": "clickhouse-connect", "databricks": "databricks-sql-connector",
            "druid": "pydruid", "duckdb": "duckdb", "exasol": "pyexasol", "flink": "apache-flink", "impala": "impyla", "materialize": "psycopg",
            "mssql": "pyodbc", "mysql": "mysqlclient", "oracle": "oracledb", "postgres": "psycopg", "pyspark": "pyspark", "risingwave": "psycopg2",
            "singlestoredb": "singlestoredb", "snowflake": "snowflake-connector-python", "trino": "trino"}
EXTENSIONS_URL = "https://duckdb.org/docs/stable/extensions/overview"
GROUPS = ("DB", "DW", "DL")
PYTHON_URL = "https://www.python.org/downloads/"
FROZEN_PYTHON = "3.14"  # exe를 만든 파이썬. 따로 설치하는 드라이버도 이 버전용이어야 한다.


class DriverError(ValueError):
    """드라이버 때문에 접속하지 못했다. driver에 화면이 그대로 그릴 안내(entry 모양)를 담는다."""

    def __init__(self, message, driver):
        super().__init__(message)
        self.driver = driver


def frozen():
    return bool(getattr(sys, "frozen", False))


def driver_folder():
    """exe에서 따로 설치한 드라이버를 두는 폴더: %APPDATA%\\DataLoupe\\drivers."""
    return Path(os.environ.get("APPDATA") or Path.home()) / "DataLoupe" / "drivers"


def use_installed():
    """exe(sys.frozen)이면 따로 설치한 드라이버 폴더를 import 경로에 더한다(폴더가 있을 때만). 백엔드를 불러오기 전에 부른다."""
    folder = driver_folder()
    if frozen() and folder.is_dir() and str(folder) not in sys.path:
        sys.path.append(str(folder))


def ibis_version():
    """설치할 ibis-framework 버전: requirements.txt에 고정된 것. exe에는 그 파일이 없으므로 설치 정보에서 읽는다."""
    try:
        found = re.search(r"^ibis-framework(?:\[[^\]]*\])?==([\w.]+)", (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8"), re.M)
        if found:
            return found.group(1)
    except OSError:
        pass
    from importlib.metadata import version
    return version("ibis-framework")


@functools.cache  # py 런처를 부르는 데 3초쯤 걸려 실행마다 한 번만 묻는다(설치하면 프로그램을 다시 켜라고 안내한다)
def has_python():
    """exe의 드라이버 설치에 쓸 Python 3.14가 있는지: py 런처의 목록, 없으면 PATH의 python 버전. 버전만 묻는다."""
    def ask(*cmd):
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return done.stdout + done.stderr
        except (OSError, subprocess.SubprocessError):
            return ""
    if shutil.which("py") and f"-V:{FROZEN_PYTHON}" in ask("py", "-0"):
        return True
    return bool(shutil.which("python")) and f"Python {FROZEN_PYTHON}." in ask("python", "--version")


def pip_command(extra):
    """Ibis 드라이버(extra)를 설치하는 명령. 소스 실행은 평범한 pip, exe는 따로 설치 폴더로 받는다."""
    # 간접 의존까지 빌드 때 버전으로 고정한다. constraints.txt는 소스 실행이면 저장소의 것, exe면 spec이 넣은 것(같은 자리)
    spec = f'-c "{Path(__file__).resolve().parent.parent / "constraints.txt"}" "ibis-framework[{extra}]=={ibis_version()}"'
    if frozen():
        return f'py -{FROZEN_PYTHON} -m pip install --target "%APPDATA%\\DataLoupe\\drivers" {spec}'
    return f"pip install {spec}"


def extension_command(name):
    """DuckDB 확장(name)을 설치하는 명령. 받는 것은 DuckDB이고, 이 프로그램은 받지 않는다."""
    py = f"py -{FROZEN_PYTHON}" if frozen() else "python"
    return f"{py} -c \"import duckdb; duckdb.connect().execute('INSTALL {name}')\""


def exe_notice(what):
    """exe에서 Python 3.14를 찾지 못할 때의 안내. 소스 실행이거나 Python이 있으면 빈 글."""
    if not frozen() or has_python():
        return ""
    return f"이 exe에는 {what} 드라이버가 없어요. Python {FROZEN_PYTHON}를 설치하고 받는 곳의 패키지를 %APPDATA%\\DataLoupe\\drivers 폴더에 설치하거나(pip install --target) 소스로 실행하세요. Python 받는 곳: {PYTHON_URL}"


def after_note():
    """exe에서 할 일: 받은 패키지를 넣을 폴더와 다시 켜기. 소스 실행이면 빈 글."""
    return "exe는 %APPDATA%\\DataLoupe\\drivers 폴더의 패키지만 찾아요(pip install --target). 설치 뒤 프로그램을 다시 켜세요." if frozen() else ""


def module_found(module):
    """파이썬 모듈이 설치되어 있는지. 불러오지 않고 찾기만 한다."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def mssql_driver():
    """설치된 SQL Server ODBC 드라이버 중 가장 최신 이름. 없으면 None."""
    import pyodbc
    found = sorted(d for d in pyodbc.drivers() if d.startswith("ODBC Driver") and d.endswith("for SQL Server"))
    return found[-1] if found else None


def has_java():
    return bool(os.environ.get("JAVA_HOME") or shutil.which("java"))


def program_ready(program):
    """따로 설치할 프로그램(java·odbc)이 이 PC에 있는지."""
    if program == "java":
        return has_java()
    try:
        return mssql_driver() is not None
    except Exception:  # pyodbc를 불러오지 못하면 프로그램도 쓸 수 없다.
        return False


def extension_states():
    """DuckDB 확장 이름 → 설치 여부. duckdb_extensions()로 묻는다(아무것도 받지 않는다). duckdb가 없으면 빈 표."""
    try:
        import duckdb
        con = duckdb.connect(":memory:")
        try:
            return {name: bool(installed) for name, installed in con.execute("select extension_name, installed from duckdb_extensions()").fetchall()}
        finally:
            con.close()
    except Exception:
        return {}


def group_of(key):
    """대상 열쇠의 묶음(DB·DW·DL)."""
    return "DL" if key in EXTENSIONS else BORROWED[key][1] if key in BORROWED else DETAILS[key][0]


def keys():
    """안내할 대상 열쇠: DB·DW·DL 묶음 차례로, 묶음 안에서는 표에 적힌 차례로."""
    return sorted([*DETAILS, *BORROWED, *EXTENSIONS], key=lambda k: GROUPS.index(group_of(k)))


def entry(key, extensions=None):
    """대상 하나의 안내: {key, group, name, status, explanation, command, program, address, notice, after}.
    status: installed(쓸 수 있음) · missing(패키지·확장이 없음) · program_needed(패키지는 있는데 ODBC·Java가 없음).
    notice: exe에서 Python 3.14가 없을 때 하는 안내, after: exe에서 설치한 뒤 할 일. extensions는 extension_states() 결과(없으면 새로 묻는다)."""
    if key in EXTENSIONS:
        name, address, explanation = EXTENSIONS[key]
        installed = (extension_states() if extensions is None else extensions).get(key)
        status = "installed" if installed else "missing"
        return {"key": key, "group": "DL", "name": name, "status": status, "explanation": explanation, "command": extension_command(key), "url": EXTENSIONS_URL,
                "program": None, "address": address, "notice": exe_notice(name) if status == "missing" else "", "after": after_note()}
    if key in BORROWED:
        name, group, scheme, address, explanation = BORROWED[key]
    else:
        group, address, explanation = DETAILS[key]
        name, scheme = DRIVERS[key][0], key
    _, module, extra, program = DRIVERS[scheme]
    if not module_found(module):
        status = "missing"
    else:
        status = "installed" if not program or program_ready(program) else "program_needed"
    return {"key": key, "group": group, "name": name, "status": status, "explanation": explanation, "command": pip_command(extra),
            "url": f"https://pypi.org/project/{PACKAGES[scheme]}/" if scheme in PACKAGES else None,
            "program": dict(zip(("name", "url"), PROGRAMS[program])) if program else None, "address": address,
            "notice": exe_notice(name) if status == "missing" else "", "after": after_note()}


def overview():
    """모든 대상의 안내 목록(화면의 드라이버 표). 이 PC를 조사할 뿐 아무것도 받거나 설치하지 않는다."""
    states = extension_states()
    return [entry(k, states) for k in keys()]


def missing(db, program):
    name, url = PROGRAMS[program]
    return ValueError(f"{db} 접속에 필요한 {name}가 이 PC에 없어요. 다운로드: {url}")


QUERY_TIMEOUT = 1800  # 데이터 작업 하나를 기다리는 시간(초). server가 기다리는 시간이자 settle이 DB에 거는 쿼리 시간 제한.
APP_NAME = "DataLoupe"  # DB에 알리는 접속 이름(PG application_name, SQL Server APP)
# 접속 직후 보낼 시간 제한 문장. MySQL은 max_execution_time(ms), MariaDB는 max_statement_time(초): 모르는 쪽은 오류가 나고 넘어간다.
SESSION_LIMIT = {
    "mysql": ("SET SESSION max_execution_time = {ms}", "SET SESSION max_statement_time = {s}"),
    "singlestoredb": ("SET SESSION max_execution_time = {ms}",),
    "snowflake": ("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {s}",),
}
POOLER_PORT = 6543  # Supabase Transaction pooler(PgBouncer) 포트
PG_WAIT = 10  # PostgreSQL 접속을 기다리는 초(주소의 connect_timeout이 있으면 그것)


def pooler_args(url):
    """Transaction pooler(포트 6543이거나 주소에 pgbouncer 표시)는 접속마다 서버가 바뀌어 prepared statement를 못 쓴다 → psycopg의 prepare_threshold를 끈다.
    pgbouncer=true는 psycopg(libpq)가 모르는 매개변수라 None으로 덮어 접속 인자에서 뺀다. sslmode 같은 나머지 매개변수는 Ibis가 psycopg로 그대로 넘긴다."""
    parts = urlsplit(url)
    flagged = any(piece.partition("=")[0] == "pgbouncer" for piece in parts.query.split("&"))
    try:
        pooled = parts.port == POOLER_PORT
    except ValueError:
        pooled = False
    return ({"prepare_threshold": None} | ({"pgbouncer": None} if flagged else {})) if pooled or flagged else {}


def query_keys(url):
    """주소 query의 매개변수 이름들(소문자). 사용자가 주소에 넣은 값을 덮지 않으려고 본다."""
    return {piece.partition("=")[0].lower() for piece in urlsplit(url).query.split("&")}


def settle(con, url):
    """접속 직후 연결 설정. DB 쪽 쿼리 시간 제한을 QUERY_TIMEOUT과 같게 걸어, 앱이 기다림을 그만둔 쿼리(취소를 알리지 못한 드라이버)도 DB가 멈추게 한다.
    PostgreSQL은 접속 이름(application_name)도 단다. 주소에 넣은 값은 그대로 두고, 풀러(Transaction pooler)는 서버 연결을 남과 나눠 써서 건드리지 않는다.
    PostgreSQL은 접속 옵션(options=-c) 대신 SET을 쓴다: PgBouncer·Redshift가 모르는 시작 옵션이면 접속부터 거절할 수 있다. 안 되는 DB·권한이면 그냥 넘어간다."""
    scheme = urlsplit(url).scheme.replace("postgresql", "postgres")
    ms, asked = QUERY_TIMEOUT * 1000, query_keys(url)
    if scheme == "bigquery":  # Ibis가 쿼리마다 이 기본 설정을 합친다
        try:
            con.client.default_query_job_config.job_timeout_ms = ms
        except Exception:
            pass
        return
    if scheme == "postgres":
        statements = [] if pooler_args(url) else [f"SET statement_timeout = {ms}"] * ("statement_timeout" not in url.lower()) + [f"SET application_name = '{APP_NAME}'"] * ("application_name" not in asked)
    else:
        statements = [sql.format(ms=ms, s=QUERY_TIMEOUT) for sql in SESSION_LIMIT.get(scheme, ())]
    for sql in statements:
        try:
            con.raw_sql(sql).close()
        except Exception:
            pass


def prepare(url, password=""):
    """접속 전에 드라이버를 확인하고, ibis.connect에 더할 인자를 돌려준다.
    없으면 설치 방법이나 다운로드 주소를 담은 DriverError(ValueError)를 낸다. 그 driver에 화면용 안내가 들어 있다."""
    scheme = urlsplit(url).scheme.replace("postgresql", "postgres")
    if scheme not in DRIVERS:
        return {}
    db, module, extra, program = DRIVERS[scheme]

    def program_needed():
        return DriverError(str(missing(db, program)), {**entry(scheme), "status": "program_needed"})

    try:
        importlib.import_module(module)
    except ImportError as e:  # 그 모듈이 없으면 Ibis 설치 안내, 그 밖(모듈이 쓰는 것이 없음)은 따로 설치할 프로그램이나 불러오기 오류
        if isinstance(e, ModuleNotFoundError) and e.name and module.startswith(e.name):
            info = {**entry(scheme), "status": "missing"}
            raise DriverError(" ".join(filter(None, [f"{db} 지원이 설치되어 있지 않아요. 설치: {info['command']}", info["after"], info["notice"]])), info)
        if program:
            raise program_needed()
        raise ValueError(f"{db} 드라이버를 불러오지 못했어요: {e}")

    if program == "java" and not has_java():
        raise program_needed()
    if program == "odbc":  # autocommit: 조회 뒤 열린 트랜잭션(잠금)이 남지 않게. APP: DB 관리자가 이 앱의 접속을 알아보게(주소에 넣었으면 그것)
        extra = {"autocommit": True} | ({} if "app" in query_keys(url) else {"APP": APP_NAME})
        if "driver" not in query_keys(url):
            driver = mssql_driver()
            if driver is None:
                raise program_needed()
            extra["driver"] = driver
        return extra
    if scheme == "postgres":  # Windows에서 libpq는 거부된 접속을 알아채지 못하고 시간이 다 될 때까지 기다린다 → 주소에 없으면 PG_WAIT초로
        return ({} if "connect_timeout=" in urlsplit(url).query else {"connect_timeout": PG_WAIT}) | pooler_args(url)
    if scheme == "trino" and password:
        # Ibis는 host가 https:// 로 시작할 때만 비밀번호 인증을 쓴다.
        return {"host": f"https://{urlsplit(url).hostname}"}
    if scheme in ("mysql", "singlestoredb"):  # 서버가 PC의 파일을 읽어 가는 LOAD DATA LOCAL INFILE을 끈다(SingleStore는 Ibis 기본값이 켜짐)
        return {"local_infile": False}
    return {}
