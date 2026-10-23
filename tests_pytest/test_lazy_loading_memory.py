"""시작할 때와 연결 전에는 무거운 데이터 라이브러리·DB 드라이버를 올리지 않는지. 프로그램이 시작할 때 올리는 모듈을 새 파이썬에서 그대로 불러 본다."""
import json
import os
import subprocess
import sys
import tempfile

from conftest import ROOT

PROBE = """
import json, sys, psutil
import lazy  # desktop.py와 같은 차례로 먼저
import webview
import app, collect_app, dash_app, flow_app, notify, prep_app, schedule_app, server, source  # desktop.py가 창을 띄우기 전에 올리는 것
def kind(name):
    m = sys.modules.get(name)
    return "none" if m is None else type(m).__name__
names = ["ibis", "polars", "duckdb", "pyarrow", "pandas", "numpy", "sqlglot", "confluent_kafka", "pyodbc", "psycopg", "psycopg2", "MySQLdb",
         "oracledb", "snowflake.connector", "google.cloud.bigquery", "trino", "clickhouse_connect", "databricks.sql", "pyspark", "sqlalchemy"]
print(json.dumps({"modules": {n: kind(n) for n in names}, "mb": psutil.Process().memory_info().rss / 2**20}))
"""
LAZY = {"ibis", "polars", "duckdb", "pyarrow"}  # lazy.py가 이름만 걸어 두는 것(처음 쓸 때 올라온다)
LIMIT_MB = 80  # 명세의 시작 메모리 목표(50~80MB 이하). 화면을 그리는 WebView2는 다른 프로세스라 들어가지 않는다.


def startup():
    env = {**os.environ, "APPDATA": tempfile.mkdtemp(prefix="ibis_pytest_lazy_")}
    done = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_heavy_libraries_not_loaded_at_startup():
    modules = startup()["modules"]
    for name, kind in modules.items():
        if name in LAZY:
            assert kind == "_LazyModule", f"{name}이 시작할 때 올라옴"
        else:
            assert kind == "none", f"{name}이 연결 전에 올라옴"


def test_startup_memory_under_target():
    assert startup()["mb"] < LIMIT_MB
