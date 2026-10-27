"""시작할 때와 연결 전에는 무거운 데이터 라이브러리·DB 드라이버·기능 모듈을 올리지 않는지. 프로그램이 창을 만들기 전에 올리는 것(desktop.py의 앞부분)을 새 파이썬에서 그대로 불러 본다.
(a) 시작할 때는 core/와 라우트 파일만, (b) 페이지 하나를 열면 그 화면의 묶음만, (c) 다른 화면의 기능 API를 불러도 지연 참조로 바로 동작한다."""
import json
import os
import subprocess
import sys
import tempfile

import pytest

from conftest import ROOT

# desktop.py의 창 만들기 앞부분(주소를 등록하는 import)을 그대로 실행한 뒤 일을 한다. %s 자리에 시험별 코드가 들어간다.
PROBE = """
import json, sys, time, psutil
src = open("desktop.py", encoding="utf-8").read()
sys.argv = ["desktop.py"]
exec(compile(src[:src.index("http = make_server")], "desktop.py", "exec"), {"__name__": "probe"})
HERE = %r
def kind(name):
    m = sys.modules.get(name)
    return "none" if m is None else type(m).__name__
def mine():
    return sorted(n for n, m in list(sys.modules.items()) if type(m).__name__ != "_LazyModule" and (getattr(m, "__file__", None) or "").startswith(HERE) and ".venv" not in m.__file__ and n != "probe")  # 지연 모듈은 속성을 읽으면 올라오므로 건드리지 않는다
out = {}
%s
names = ["ibis", "polars", "duckdb", "pyarrow", "pandas", "numpy", "pyproj", "sqlglot", "confluent_kafka", "pyodbc", "psycopg", "psycopg2", "MySQLdb",
         "oracledb", "snowflake.connector", "google.cloud.bigquery", "trino", "clickhouse_connect", "databricks.sql", "pyspark", "sqlalchemy"]
out.update(modules={n: kind(n) for n in names}, mb=psutil.Process().memory_info().rss / 2**20, mine=mine())
print(json.dumps(out))
"""
LAZY = {"ibis", "polars", "duckdb", "pyarrow"}  # lazy.py가 이름만 걸어 두는 것(처음 쓸 때 올라온다)
LIMIT_MB = 80  # 명세의 시작 메모리 목표(50~80MB 이하). 화면을 그리는 WebView2는 다른 프로세스라 들어가지 않는다.
# 시작할 때 올라와도 되는 기능 패키지의 모듈: 주소를 등록하는 라우트 파일, 그리고 프로그램 안 예약 스레드를 띄우는 schedule(수집·알림은 예약을 돌릴 때 올라온다)
ROUTES = {"collect.collect_app", "collect.load_app", "dashboard.dash_app", "integrations.bot_app", "integrations.integrations_app", "integrations.notify",
          "integrations.schedule", "integrations.schedule_app", "metrics.catalog_app", "prep.prep_app", "query.flow_app", "query.search_app"}
FEATURE = ("collect", "dashboard", "integrations", "metrics", "prep", "query", "analysis", "geo")  # geo(좌표)는 어느 화면의 묶음에도 넣지 않는다: 좌표 기능을 쓸 때만 올라온다
BUNDLES = {"/search": {"query.join_keys", "query.sentences", "query.sentence_run", "query.sentence_exec", "query.nl2sql",
                       "metrics.my_metrics", "metrics.metric_catalog", "metrics.metric_kernels"},
           "/prep": {"prep.prep_profile", "prep.prep_expr", "analysis.stats"},
           "/dashboard": {"dashboard.dash_calc", "dashboard.dash_refresh", "dashboard.period", "dashboard.dash_pdf", "dashboard.summary_text"},
           "/collect": {"collect.collect_config", "collect.collect_job", "collect.collect_store", "collect.collect_limits", "collect.collect_request", "collect.collect_http",
                        "collect.collect_web", "collect.load_job", "collect.load_table", "collect.load_kafka", "collect.uploads"},
           "/integrations": {"integrations.adapters.ga4", "integrations.adapters.llm", "integrations.commands"}}


def run(code=""):
    env = {**os.environ, "APPDATA": tempfile.mkdtemp(prefix="ibis_pytest_lazy_")}
    done = subprocess.run([sys.executable, "-c", PROBE % (str(ROOT), code)], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def feature_modules(found):
    """올라온 프로젝트 모듈 중 기능 패키지의 것(패키지 자신의 __init__은 뺀다)."""
    return {n for n in found if n.split(".")[0] in FEATURE and "." in n}


def test_heavy_libraries_not_loaded_at_startup():
    modules = run()["modules"]
    for name, kind in modules.items():
        if name in LAZY:
            assert kind == "_LazyModule", f"{name}이 시작할 때 올라옴"
        else:
            assert kind == "none", f"{name}이 연결 전에 올라옴"


def test_startup_memory_under_target():
    assert run()["mb"] < LIMIT_MB


def test_startup_loads_only_core_and_route_files():
    extra = feature_modules(run()["mine"]) - ROUTES
    assert not extra, f"시작할 때 기능 모듈이 올라옴: {sorted(extra)}"


def geo_loaded(found):
    return sorted(n for n in found["mine"] if n == "geo" or n.startswith("geo."))


# 페이지를 열면 뒤 스레드가 그 묶음을 올린다. 올라올 때까지 기다린 뒤(최대 60초) 올라온 기능 모듈을 본다.
OPEN = """
from query.search_app import app
c = app.test_client(); c.get("/?key=" + __import__("core.server", fromlist=["x"]).KEY)
want = set(%r)
status = c.get(%r).status_code
t = time.time()
while time.time() - t < 60 and not want <= set(mine()):
    time.sleep(0.2)
out.update(status=status, want_loaded=sorted(want - set(mine())))
"""


@pytest.mark.parametrize("path", sorted(BUNDLES))
def test_page_loads_only_its_bundle(path):
    found = run(OPEN % (sorted(BUNDLES[path]), path))
    assert found["status"] == 200 and found["want_loaded"] == [], found
    assert geo_loaded(found) == [] and found["modules"]["pyproj"] == "none", f"{path}를 열었는데 좌표 모듈이 올라옴: {geo_loaded(found)}"
    loaded = feature_modules(found["mine"]) - ROUTES
    others = set().union(*(b for p, b in BUNDLES.items() if p != path))
    # 다른 화면 묶음에만 있는 모듈은 올라오지 않는다. 같은 화면의 모듈이 끌고 오는 것(예: 쿼리 화면의 지표 사전이 쓰는 모듈)은 허용한다.
    stray = (loaded & others) - BUNDLES[path] - ALSO.get(path, set())
    assert not stray, f"{path}를 열었는데 다른 화면의 모듈이 올라옴: {sorted(stray)}"


# 한 화면의 묶음이 import로 끌고 오는 다른 화면의 모듈(쓰는 쪽이 있는 한 어쩔 수 없다)
ALSO = {"/dashboard": {"prep.prep_profile", "prep.prep_expr", "analysis.stats"},  # 대시보드 카드의 값 구하기가 분석(prep_app.analyzed)을 쓴다
        "/search": {"integrations.adapters.llm"}}  # 자연어 질문(nl2sql)이 LLM 어댑터를 쓴다

# 다른 화면의 기능 API를 아무 페이지도 열지 않고 불러도 지연 참조로 바로 동작한다.
CALLS = """
from query.search_app import app
from core import server
c = app.test_client(); c.get("/?key=" + server.KEY)
out["calls"] = {p: c.get(p).status_code for p in %r}
"""


def test_feature_api_works_without_opening_its_page():
    paths = ["/api/sentences", "/api/dash", "/api/catalog", "/api/collections", "/api/schedules", "/api/integrations", "/api/bot", "/api/destinations", "/api/my_metrics"]
    found = run(CALLS % (paths,))
    assert all(code == 200 for code in found["calls"].values()), found["calls"]


# 좌표 기능은 처음 쓸 때 올라온다(그 전에는 geo도 pyproj도 없다).
FIRST_USE = """
import ibis
from core import sql_funcs, source
start = sorted(n for n in sys.modules if n == "geo" or n.startswith("geo."))
t = ibis.memtable({"lat": [37.5], "lon": [127.0]})
sql_funcs.make("haversine", t, {"lat": "lat", "lon": "lon"})
after_func = sorted(n for n in sys.modules if n.startswith("geo."))
pyproj_after_func = kind("pyproj")
from geo import tm
tm.to_wgs84([953898.0], [1952036.0], 5179)
out.update(start=start, after_func=after_func, pyproj_after_func=pyproj_after_func, pyproj_after_tm=kind("pyproj"))
"""


def test_geo_and_pyproj_load_on_first_use():
    found = run(FIRST_USE)
    assert found["start"] == []  # 시작할 때는 geo가 없다
    assert found["after_func"] == ["geo.funcs"] and found["pyproj_after_func"] == "none"  # 거리 함수는 geo.funcs만(numpy·pyproj 없이)
    assert found["pyproj_after_tm"] == "module"  # TM 변환을 부를 때 pyproj가 올라온다
