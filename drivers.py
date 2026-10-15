"""DB별로 필요한 드라이버를 확인한다. 없으면 어디서 받는지 알려 준다."""
import importlib
import os
import shutil
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


def mssql_driver():
    """설치된 SQL Server ODBC 드라이버 중 가장 최신 이름. 없으면 None."""
    import pyodbc
    found = sorted(d for d in pyodbc.drivers() if d.startswith("ODBC Driver") and d.endswith("for SQL Server"))
    return found[-1] if found else None


def has_java():
    return bool(os.environ.get("JAVA_HOME") or shutil.which("java"))


def missing(db, program):
    name, url = PROGRAMS[program]
    return ValueError(f"{db} 접속에 필요한 {name}가 이 PC에 없습니다. 다운로드: {url}")


def prepare(url, password=""):
    """접속 전에 드라이버를 확인하고, ibis.connect에 더할 인자를 돌려준다.
    없으면 설치 방법이나 다운로드 주소를 담은 ValueError를 낸다."""
    scheme = urlsplit(url).scheme.replace("postgresql", "postgres")
    if scheme not in DRIVERS:
        return {}
    db, module, extra, program = DRIVERS[scheme]
    try:
        importlib.import_module(module)
    except ImportError as e:  # 그 모듈이 없으면 Ibis 설치 안내, 그 밖(모듈이 쓰는 것이 없음)은 따로 설치할 프로그램이나 불러오기 오류
        if isinstance(e, ModuleNotFoundError) and e.name and module.startswith(e.name):
            raise ValueError(f"{db} 지원이 설치되어 있지 않습니다. 설치: pip install 'ibis-framework[{extra}]'")
        raise missing(db, program) if program else ValueError(f"{db} 드라이버를 불러오지 못했습니다: {e}")

    if program == "java" and not has_java():
        raise missing(db, program)
    if program == "odbc" and "driver=" not in urlsplit(url).query:
        driver = mssql_driver()
        if driver is None:
            raise missing(db, program)
        return {"driver": driver}
    if scheme == "trino" and password:
        # Ibis는 host가 https:// 로 시작할 때만 비밀번호 인증을 쓴다.
        return {"host": f"https://{urlsplit(url).hostname}"}
    return {}
